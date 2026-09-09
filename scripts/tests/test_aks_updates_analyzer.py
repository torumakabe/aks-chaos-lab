from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import nullcontext, redirect_stdout
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from freezegun import freeze_time

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github/workflows/aks-updates-analyzer.md"
)
RunSource = Callable[..., dict[str, Any]]
RSS_ITEM = """<item><title>AKS update</title><description>Kubernetes fixes</description>
<link>https://example.com/update</link><pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate></item>"""
RELEASE = {
    "tag_name": "2026-09-08",
    "name": "AKS release",
    "html_url": "https://github.com/Azure/AKS/releases/tag/2026-09-08",
    "published_at": "2026-09-08T00:00:00Z",
    "body": "Kubernetes fixes",
}


def rss(*items: str) -> bytes:
    return f"<rss><channel>{''.join(items)}</channel></rss>".encode()


@pytest.fixture
def run_source(monkeypatch: pytest.MonkeyPatch) -> RunSource:
    scripts = re.findall(
        r"python3 << 'PYEOF'\n(.*?)\nPYEOF",
        WORKFLOW.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert len(scripts) == 2

    def run(
        source: int,
        payload: bytes = b"",
        error: BaseException | None = None,
        read_error: BaseException | None = None,
    ) -> dict[str, Any]:
        response = Mock()
        response.read = Mock(return_value=payload, side_effect=read_error)
        request = Mock(return_value=nullcontext(response), side_effect=error)
        monkeypatch.setattr(urllib.request, "urlopen", request)
        output = io.StringIO()
        with freeze_time("2026-09-09T00:00:00Z"), redirect_stdout(output):
            try:
                exec(compile(scripts[source], str(WORKFLOW), "exec"), {})  # noqa: S102
            except SystemExit as exc:
                assert exc.code == 0
        request.assert_called_once()
        assert request.call_args.kwargs["timeout"] == 30
        result = json.loads(output.getvalue())
        assert isinstance(result, dict), "fetch failures must not look like empty items"
        assert result["status"] in {"pass", "unverified"}
        assert result["reason_code"]
        assert result["reason"]
        return result

    return run


@pytest.mark.parametrize("source,payload", [(0, rss()), (1, b"[]")])
def test_successful_empty_source(
    run_source: RunSource, source: int, payload: bytes
) -> None:
    result = run_source(source, payload)
    assert result["status"] == "pass"
    assert result["reason_code"] == "evidence-available"
    assert result["items"] == []
    assert result["received_count"] == result["invalid_count"] == 0


@pytest.mark.parametrize("source", [0, 1])
@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("unreachable"),
        urllib.error.HTTPError(
            "https://example.com", 429, "rate limited", Message(), None
        ),
        TimeoutError("timed out"),
        OSError("connection reset"),
    ],
)
def test_transport_failure_is_unverified(
    run_source: RunSource, source: int, error: BaseException
) -> None:
    result = run_source(source, error=error)
    assert result["status"] == "unverified"
    assert result["reason_code"] == "evidence-unavailable"
    assert result["items"] == []
    assert result["received_count"] is None


@pytest.mark.parametrize("source", [0, 1])
def test_incomplete_download_is_unverified(run_source: RunSource, source: int) -> None:
    result = run_source(source, read_error=IncompleteRead(b"partial", 100))
    assert result["status"] == "unverified"
    assert result["reason_code"] == "evidence-unavailable"
    assert result["items"] == []


@pytest.mark.parametrize("source", [0, 1])
def test_unexpected_error_is_not_swallowed(run_source: RunSource, source: int) -> None:
    with pytest.raises(RuntimeError, match="unexpected"):
        run_source(source, error=RuntimeError("unexpected"))


@pytest.mark.parametrize(
    "source,payload",
    [
        (0, b"<rss>"),
        (0, b"<html><body>Maintenance</body></html>"),
        (0, b"<rss/>"),
        (0, b"<rss><channel/><channel/></rss>"),
        (1, b"{"),
        (1, b"\xff"),
        (1, b'{"message": "rate limited"}'),
        (1, b"null"),
        (1, b'"not releases"'),
    ],
)
def test_invalid_response_is_unverified(
    run_source: RunSource, source: int, payload: bytes
) -> None:
    result = run_source(source, payload)
    assert result["status"] == "unverified"
    assert result["reason_code"] == "invalid-response"
    assert result["items"] == []


def test_rss_filters_valid_items_by_keyword_and_date(run_source: RunSource) -> None:
    result = run_source(
        0,
        rss(
            RSS_ITEM,
            RSS_ITEM.replace("08 Sep", "01 Sep"),
            RSS_ITEM.replace("AKS update", "Storage").replace("Kubernetes", "Storage"),
        ),
    )
    assert result["status"] == "pass"
    assert result["received_count"] == 3
    assert result["invalid_count"] == 0
    assert result["items"] == [
        {
            "title": "AKS update",
            "date": "Tue, 08 Sep 2026 00:00:00 GMT",
            "link": "https://example.com/update",
            "desc": "Kubernetes fixes",
        }
    ]


@pytest.mark.parametrize(
    "bad_item",
    [
        RSS_ITEM.replace("Tue, 08 Sep 2026 00:00:00 GMT", "invalid"),
        RSS_ITEM.replace(" GMT", ""),
        RSS_ITEM.replace(" GMT", " -0000"),
        RSS_ITEM.replace("08 Sep", "99 Sep"),
        RSS_ITEM.replace("<title>AKS update</title>", ""),
        RSS_ITEM.replace("<description>Kubernetes fixes</description>", ""),
        RSS_ITEM.replace("https://example.com/update", " "),
        RSS_ITEM.replace("<pubDate>Tue, 08 Sep 2026 00:00:00 GMT</pubDate>", ""),
        RSS_ITEM.replace("AKS update", "Storage")
        .replace("Kubernetes", "Storage")
        .replace(" GMT", ""),
    ],
)
@pytest.mark.parametrize("include_valid", [False, True])
def test_rss_partial_parse_keeps_valid_items(
    run_source: RunSource, bad_item: str, include_valid: bool
) -> None:
    result = run_source(0, rss(bad_item, RSS_ITEM if include_valid else ""))
    assert result["status"] == "unverified"
    assert result["reason_code"] == "partial-parse"
    assert result["received_count"] == 1 + include_valid
    assert result["invalid_count"] == 1
    assert len(result["items"]) == int(include_valid)
    assert result["errors"][0]["index"] == 1
    assert result["errors"][0]["reason"]


@pytest.mark.parametrize(
    "patch",
    [
        {"published_at": None},
        {"published_at": 123},
        {"published_at": "invalid"},
        {"published_at": "2026-09-08T00:00:00"},
        {"published_at": "2026-09-08"},
        {"published_at": "2026-99-08T00:00:00Z"},
        {"tag_name": ""},
        {"html_url": None},
        {"name": []},
        {"body": {}},
    ],
)
def test_release_partial_parse_keeps_valid_items(
    run_source: RunSource, patch: dict[str, Any]
) -> None:
    result = run_source(1, json.dumps([RELEASE | patch, RELEASE]).encode())
    assert result["status"] == "unverified"
    assert result["reason_code"] == "partial-parse"
    assert result["received_count"] == 2
    assert result["invalid_count"] == 1
    assert len(result["items"]) == 1
    assert result["errors"][0]["index"] == 1
    assert result["errors"][0]["reason"]


@pytest.mark.parametrize("bad_release", [None, 42, [], {}, "release"])
def test_invalid_release_item_is_not_successful_empty(
    run_source: RunSource, bad_release: Any
) -> None:
    result = run_source(1, json.dumps([bad_release]).encode())
    assert result["status"] == "unverified"
    assert result["reason_code"] == "partial-parse"
    assert result["items"] == []
    assert result["invalid_count"] == 1


@pytest.mark.parametrize("field", ["published_at", "tag_name", "html_url"])
def test_missing_release_field_is_unverified(run_source: RunSource, field: str) -> None:
    release = RELEASE.copy()
    del release[field]
    result = run_source(1, json.dumps([release]).encode())
    assert result["status"] == "unverified"
    assert result["reason_code"] == "partial-parse"
    assert field in result["errors"][0]["reason"]


def test_release_filters_valid_dates_and_allows_optional_text(
    run_source: RunSource,
) -> None:
    result = run_source(
        1,
        json.dumps(
            [
                RELEASE,
                RELEASE | {"published_at": "2026-08-25T23:59:59Z"},
                RELEASE | {"name": None, "body": None},
            ]
        ).encode(),
    )
    assert result["status"] == "pass"
    assert result["received_count"] == 3
    assert result["invalid_count"] == 0
    assert result["items"][0] == {
        "tag": RELEASE["tag_name"],
        "name": RELEASE["name"],
        "url": RELEASE["html_url"],
        "published_at": RELEASE["published_at"],
        "body": RELEASE["body"],
    }
    assert len(result["items"]) == 2
    assert result["items"][1]["name"] == RELEASE["tag_name"]
    assert result["items"][1]["body"] == ""
