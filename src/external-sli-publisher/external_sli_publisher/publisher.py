"""Probe the chaos app and publish Azure Monitor SLI signals."""

from __future__ import annotations

import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import cramjam
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobClient
from opentelemetry import trace
from opentelemetry.instrumentation.utils import suppress_http_instrumentation
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind, Status, StatusCode

LOGGER = logging.getLogger(__name__)
AZURE_MONITOR_SCOPE = "https://monitor.azure.com/.default"
TRACER = trace.get_tracer(__name__)

UrlOpen = Callable[..., Any]
Clock = Callable[[], float]

# Latency threshold buckets used to emit "good" samples per `le` boundary.
# Each entry is `(label_string, seconds_float)`. The publisher emits one
# sample per bucket per window with label `le=<label_string>`; the SLI
# definition picks which bucket to evaluate via a `dimensionName=le,
# operator=EQ, values=[<threshold>]` filter. Changing the SLO threshold
# therefore does not require redeploying the publisher. See ADR-013.
LATENCY_BUCKETS: tuple[tuple[str, float], ...] = (
    ("0.1", 0.1),
    ("0.25", 0.25),
    ("0.5", 0.5),
    ("1", 1.0),
    ("2", 2.0),
    ("5", 5.0),
)
MAX_BUCKET_SECONDS: float = max(seconds for _, seconds in LATENCY_BUCKETS)
LATENCY_GOOD_METRIC = "chaos_app_external_latency_good"
LATENCY_TOTAL_METRIC = "chaos_app_external_latency_total"


@dataclass(frozen=True)
class Settings:
    probe_url: str
    probe_name: str
    remote_write_url: str
    state_blob_url: str
    service_name: str
    environment: str
    window_seconds: int
    probe_timeout_seconds: int
    max_catchup_windows: int
    not_before: datetime | None

    def __post_init__(self) -> None:
        if self.probe_timeout_seconds <= MAX_BUCKET_SECONDS:
            raise RuntimeError(
                "EXTERNAL_SLI_PROBE_TIMEOUT_SECONDS must be greater than the "
                f"largest latency bucket ({MAX_BUCKET_SECONDS:g}s) so successful "
                "but slow probes can be observed in the top bucket"
            )

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            probe_url=required_env("EXTERNAL_SLI_PROBE_URL"),
            probe_name=required_env("EXTERNAL_SLI_PROBE_NAME"),
            remote_write_url=required_env("PROMETHEUS_REMOTE_WRITE_URL"),
            state_blob_url=required_env("EXTERNAL_SLI_STATE_BLOB_URL"),
            service_name=os.environ.get("EXTERNAL_SLI_SERVICE_NAME", "chaos-app"),
            environment=os.environ.get("AZURE_ENV_NAME", "unknown"),
            window_seconds=env_int("EXTERNAL_SLI_WINDOW_SECONDS", 300),
            probe_timeout_seconds=env_int("EXTERNAL_SLI_PROBE_TIMEOUT_SECONDS", 10),
            max_catchup_windows=env_int("EXTERNAL_SLI_MAX_CATCHUP_WINDOWS", 12),
            not_before=optional_datetime("EXTERNAL_SLI_NOT_BEFORE_UTC"),
        )


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    status_code: int | None
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class SliSamples:
    """Aggregated SLI signal contribution for one publish operation.

    `latency_buckets` is keyed by the `le` label string (e.g. "1") and stores
    the count of probes whose duration <= that bucket boundary. Each bucket
    is published as one sample of the single metric `LATENCY_GOOD_METRIC`
    distinguished by the `le` label; see `LATENCY_BUCKETS` and
    `metric_samples`. `latency_total` is the Latency SLI denominator
    (== availability_total). We intentionally do not emit an average latency
    because missed/failed windows would skew the mean.
    """

    availability_good: int
    availability_total: int
    latency_buckets: dict[str, int]
    latency_total: int


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def optional_datetime(name: str) -> datetime | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return parse_state_datetime(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an ISO 8601 datetime") from exc


def floor_datetime(value: datetime, step_seconds: int) -> datetime:
    seconds = int(value.timestamp())
    return datetime.fromtimestamp(seconds - (seconds % step_seconds), tz=UTC)


def timestamp_ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def target_window(now: datetime, settings: Settings) -> Window:
    end = floor_datetime(now, settings.window_seconds)
    return Window(start=end - timedelta(seconds=settings.window_seconds), end=end)


def windows_to_publish(
    *,
    last_published_end: datetime | None,
    target: Window,
    settings: Settings,
) -> list[Window]:
    if last_published_end is not None and target.end <= last_published_end:
        return []

    step = timedelta(seconds=settings.window_seconds)
    if last_published_end is None:
        next_start = target.start
        if settings.not_before is not None:
            next_start = floor_datetime(settings.not_before, settings.window_seconds)
            if next_start < settings.not_before:
                next_start += step
    else:
        next_start = last_published_end

    windows: list[Window] = []
    while len(windows) < settings.max_catchup_windows:
        next_window = Window(start=next_start, end=next_start + step)
        if next_window.end > target.end:
            break
        windows.append(next_window)
        next_start = next_window.end
    return windows


def probe_endpoint(
    settings: Settings,
    *,
    urlopen: UrlOpen = urllib.request.urlopen,
    clock: Clock = time.perf_counter,
) -> ProbeResult:
    parsed = urllib.parse.urlsplit(settings.probe_url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    start = clock()
    with TRACER.start_as_current_span(
        f"GET {path}",
        kind=SpanKind.CLIENT,
    ) as span:
        headers = {"User-Agent": "aks-chaos-lab-external-sli-publisher"}
        inject(headers)
        request = urllib.request.Request(  # noqa: S310
            settings.probe_url,
            headers=headers,
            method="GET",
        )
        span.set_attribute("http.request.method", "GET")
        span.set_attribute("url.full", settings.probe_url)
        span.set_attribute("server.address", parsed.hostname or "")
        span.set_attribute("server.port", port)
        span.set_attribute("peer.service", settings.service_name)
        try:
            with (
                suppress_http_instrumentation(),
                urlopen(
                    request,
                    timeout=settings.probe_timeout_seconds,
                ) as response,
            ):
                response.read()
                status_code = int(getattr(response, "status", response.getcode()))
        except urllib.error.HTTPError as exc:
            duration_ms = elapsed_ms(start, clock)
            span.set_attribute("http.response.status_code", exc.code)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            LOGGER.warning(
                "external SLI probe returned HTTP error status=%s url=%s",
                exc.code,
                settings.probe_url,
            )
            return ProbeResult(
                success=False,
                status_code=exc.code,
                duration_ms=duration_ms,
                error=str(exc),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            duration_ms = elapsed_ms(start, clock)
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            LOGGER.warning(
                "external SLI probe failed error=%s url=%s",
                exc,
                settings.probe_url,
            )
            return ProbeResult(
                success=False,
                status_code=None,
                duration_ms=duration_ms,
                error=str(exc),
            )

        duration_ms = elapsed_ms(start, clock)
        success = 200 <= status_code < 300
        span.set_attribute("http.response.status_code", status_code)
        if not success:
            span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
        return ProbeResult(
            success=success,
            status_code=status_code,
            duration_ms=duration_ms,
        )


def elapsed_ms(start: float, clock: Clock) -> int:
    return max(0, int((clock() - start) * 1000))


def empty_latency_buckets() -> dict[str, int]:
    return {le_label: 0 for le_label, _ in LATENCY_BUCKETS}


def probe_result_to_sli_samples(
    result: ProbeResult,
    settings: Settings,  # noqa: ARG001 — kept for API symmetry; threshold lives in SLI
) -> SliSamples:
    availability_good = 1 if result.success else 0
    buckets = empty_latency_buckets()
    if result.success:
        duration_seconds = result.duration_ms / 1000.0
        for le_label, le_seconds in LATENCY_BUCKETS:
            if duration_seconds <= le_seconds:
                buckets[le_label] = 1
    return SliSamples(
        availability_good=availability_good,
        availability_total=1,
        latency_buckets=buckets,
        latency_total=1,
    )


def missed_window_samples(count: int) -> SliSamples:
    return SliSamples(
        availability_good=0,
        availability_total=count,
        latency_buckets=empty_latency_buckets(),
        latency_total=count,
    )


def combine_sli_samples(samples: Iterable[SliSamples]) -> SliSamples:
    availability_good = 0
    availability_total = 0
    latency_total = 0
    latency_buckets = empty_latency_buckets()
    for sample in samples:
        availability_good += sample.availability_good
        availability_total += sample.availability_total
        latency_total += sample.latency_total
        for le_label, value in sample.latency_buckets.items():
            latency_buckets[le_label] = latency_buckets.get(le_label, 0) + value
    return SliSamples(
        availability_good=availability_good,
        availability_total=availability_total,
        latency_buckets=latency_buckets,
        latency_total=latency_total,
    )


def encode_varint(value: int) -> bytes:
    chunks = bytearray()
    while value > 0x7F:
        chunks.append((value & 0x7F) | 0x80)
        value >>= 7
    chunks.append(value)
    return bytes(chunks)


def encode_key(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def encode_length_delimited(field_number: int, payload: bytes) -> bytes:
    return encode_key(field_number, 2) + encode_varint(len(payload)) + payload


def encode_string(field_number: int, value: str) -> bytes:
    return encode_length_delimited(field_number, value.encode("utf-8"))


def encode_double(field_number: int, value: float) -> bytes:
    return encode_key(field_number, 1) + struct.pack("<d", value)


def encode_int64(field_number: int, value: int) -> bytes:
    return encode_key(field_number, 0) + encode_varint(value)


def encode_label(name: str, value: str) -> bytes:
    return encode_string(1, name) + encode_string(2, value)


def encode_sample(value: float, timestamp_ms: int) -> bytes:
    return encode_double(1, value) + encode_int64(2, timestamp_ms)


def encode_time_series(
    metric_name: str,
    labels: dict[str, str],
    value: float,
    timestamp_ms: int,
) -> bytes:
    payload = bytearray()
    label_items = {"__name__": metric_name, **labels}
    for name, label_value in sorted(label_items.items()):
        payload += encode_length_delimited(1, encode_label(name, label_value))
    payload += encode_length_delimited(2, encode_sample(value, timestamp_ms))
    return bytes(payload)


def encode_write_request(
    samples: Iterable[tuple[str, dict[str, str], float, int]],
) -> bytes:
    payload = bytearray()
    for metric_name, labels, value, sample_timestamp_ms in samples:
        payload += encode_length_delimited(
            1,
            encode_time_series(metric_name, labels, value, sample_timestamp_ms),
        )
    return bytes(payload)


def sli_labels(settings: Settings) -> dict[str, str]:
    return {
        "environment": settings.environment,
        "service": settings.service_name,
        "source": "external_probe",
        "test": settings.probe_name,
    }


def metric_samples(
    sli_samples: SliSamples,
    settings: Settings,
    sample_time: datetime,
) -> list[tuple[str, dict[str, str], float, int]]:
    labels = sli_labels(settings)
    sample_timestamp_ms = timestamp_ms(sample_time)
    samples: list[tuple[str, dict[str, str], float, int]] = [
        (
            "chaos_app_external_availability_good",
            labels,
            float(sli_samples.availability_good),
            sample_timestamp_ms,
        ),
        (
            "chaos_app_external_availability_total",
            labels,
            float(sli_samples.availability_total),
            sample_timestamp_ms,
        ),
        (
            LATENCY_TOTAL_METRIC,
            labels,
            float(sli_samples.latency_total),
            sample_timestamp_ms,
        ),
    ]
    for le_label, _ in LATENCY_BUCKETS:
        samples.append(
            (
                LATENCY_GOOD_METRIC,
                {**labels, "le": le_label},
                float(sli_samples.latency_buckets.get(le_label, 0)),
                sample_timestamp_ms,
            )
        )
    return samples


def heartbeat_sample(
    settings: Settings,
    sample_time: datetime,
) -> tuple[str, dict[str, str], float, int]:
    return (
        "chaos_app_external_sli_publisher_heartbeat",
        sli_labels(settings),
        1.0,
        timestamp_ms(sample_time),
    )


def publish_remote_write_samples(
    token: str,
    samples: Iterable[tuple[str, dict[str, str], float, int]],
    settings: Settings,
) -> None:
    payload = encode_write_request(samples)
    compressed = compress_snappy_raw(payload)
    request = urllib.request.Request(  # noqa: S310
        settings.remote_write_url,
        data=compressed,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Encoding": "snappy",
            "Content-Type": "application/x-protobuf",
            "X-Prometheus-Remote-Write-Version": "0.1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        response.read()


def publish_heartbeat(token: str, settings: Settings, sample_time: datetime) -> None:
    publish_remote_write_samples(
        token,
        [heartbeat_sample(settings, sample_time)],
        settings,
    )


def publish_sli_samples(
    token: str,
    samples: SliSamples,
    settings: Settings,
    sample_time: datetime,
) -> None:
    publish_remote_write_samples(
        token,
        metric_samples(samples, settings, sample_time),
        settings,
    )


def publish_remote_write(
    token: str,
    samples: SliSamples,
    settings: Settings,
    sample_time: datetime,
) -> None:
    publish_sli_samples(token, samples, settings, sample_time)


def parse_state_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def format_state_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load_last_published(blob: BlobClient) -> datetime | None:
    try:
        state = blob.download_blob().readall()
    except ResourceNotFoundError:
        return None
    payload = json.loads(state.decode("utf-8") if isinstance(state, bytes) else state)
    value = payload.get("last_published_window_end")
    if not isinstance(value, str) or not value:
        return None
    return parse_state_datetime(value)


def compress_snappy_raw(payload: bytes) -> bytes:
    snappy_codec = getattr(cramjam, "snappy")  # noqa: B009
    return bytes(snappy_codec.compress_raw(payload))


def save_last_published(blob: BlobClient, window_end: datetime) -> None:
    blob.upload_blob(
        json.dumps({"last_published_window_end": format_state_datetime(window_end)}),
        overwrite=True,
    )


def run_once(settings: Settings, now: datetime | None = None) -> int:
    credential = DefaultAzureCredential()
    blob = BlobClient.from_blob_url(settings.state_blob_url, credential=credential)
    monitor_token = credential.get_token(AZURE_MONITOR_SCOPE).token
    sample_time = now or datetime.now(UTC)
    target = target_window(sample_time, settings)
    windows = windows_to_publish(
        last_published_end=load_last_published(blob),
        target=target,
        settings=settings,
    )
    if not windows:
        LOGGER.info("no external SLI windows to publish")
        publish_heartbeat(monitor_token, settings, sample_time)
        return 0

    publish_heartbeat(monitor_token, settings, sample_time)
    missed_window_count = max(0, len(windows) - 1)
    current_result = probe_endpoint(settings)
    samples = combine_sli_samples(
        [
            missed_window_samples(missed_window_count),
            probe_result_to_sli_samples(current_result, settings),
        ]
    )
    publish_remote_write(monitor_token, samples, settings, sample_time)
    last_window = windows[-1]
    save_last_published(blob, last_window.end)
    LOGGER.info(
        "published external SLI probe windows start=%s end=%s count=%s sample_time=%s missed=%s status=%s duration_ms=%s availability_good=%s/%s latency_total=%s buckets=%s",
        format_state_datetime(windows[0].start),
        format_state_datetime(last_window.end),
        len(windows),
        format_state_datetime(sample_time),
        missed_window_count,
        current_result.status_code,
        current_result.duration_ms,
        samples.availability_good,
        samples.availability_total,
        samples.latency_total,
        samples.latency_buckets,
    )
    if missed_window_count:
        LOGGER.info(
            "external SLI publisher marked %s missed windows as bad; samples are aggregated at publish time because Azure Monitor Workspace rejects OldData timestamps",
            missed_window_count,
        )
    return 0


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    return run_once(Settings.from_env())


if __name__ == "__main__":
    raise SystemExit(main())
