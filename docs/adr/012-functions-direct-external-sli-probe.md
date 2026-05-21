# ADR-012: Azure Functions direct probe を Azure Monitor SLI の正本にする

## Status

Accepted — Supersedes [ADR-011](011-external-availability-sli-publisher.md). Latency SLI 部分は [ADR-013](013-histogram-bucket-latency-sli.md) で histogram bucket + `EQ` filter 方式に amend。

## Context

ADR-011 では、AKS クラスタ停止時も SLI を悪化させるため、Application Insights Standard availability test を外形 source とし、Azure Functions publisher が `AppAvailabilityResults` を Prometheus good / total metrics に変換する構成を採用した。

この構成は Azure Monitor SLI が `AppAvailabilityResults` を直接 input signal にできない制約を回避する。一方で、本ラボでは availability test の multi-location 冗長性を主要価値とはしない。外形 source の独立性は「AKS 外で動く Azure Functions Timer Trigger」でも満たせるため、availability test と Log Analytics query を中間層として維持する必要性は低い。

## Decision

- Azure Monitor SLI の Availability / Latency の正本を、Azure Functions external SLI publisher の direct HTTP probe にする。
- Publisher は Gateway public FQDN の通常 API `GET /` を実行し、結果を Managed Prometheus に remote-write する。Application Insights availability test と `AppAvailabilityResults` query は default path から外す。
- HTTP 2xx を Availability good とする。HTTP error、timeout、DNS error、TLS error、その他の probe 失敗は Availability bad とする。
- Latency good は HTTP 2xx かつ duration <= `externalSliLatencyThresholdMs` とする。
- Function host / Timer 停止で欠落した window は、過去状態を再プローブできないため bad (`0/1`) として発行する。`externalSliMaxCatchupWindows` を上限にし、Azure Monitor Workspace の `OldData` 制約を避けるため、catch-up 分は publisher 実行時刻の sample に合算する。
- Metric names は ADR-011 と同じ `chaos_app_external_availability_good/total`, `chaos_app_external_latency_good/total`, `chaos_app_external_sli_publisher_heartbeat` を維持する。Prometheus label key も `environment`, `service`, `test` を維持し、`test` には probe name を入れる。
- Probe name の既定値は旧 availability test name と同じ `avail-${appName}-${environment}-${resourceToken}` 形にし、既存 SLI dimension の連続性を保つ。
- Function App では Azure Monitor OpenTelemetry を構成し、probe HTTP 呼び出しを client span として記録する。Probe は trace context を伝搬し、chaos-app 側の `GET /` request telemetry と相関させる。Application Map の Function App -> chaos app の線は診断用であり、SLI の正本は Managed Prometheus に発行する good / total metrics とする。
- Function App の Application Insights role name は生成ホスト名ではなく `external-sli-publisher` とし、Functions host は `telemetryMode: OpenTelemetry` と `OTEL_SERVICE_NAME`、Python worker は `service.name` resource attribute で揃える。環境差分は role instance 側に寄せる。
- Publisher の Function host storage と deployment storage は managed identity 接続を維持し、public access は無効化する。Prometheus remote-write に必要な `Monitoring Metrics Publisher` RBAC は維持し、Log Analytics Reader RBAC は不要化する。

## Consequences

- **利点**: Application Insights availability test と Log Analytics query への依存がなくなり、外形 SLI source の構成要素が減る。
- **利点**: Function App から chaos app への HTTP dependency telemetry が Application Insights / Application Map に表示され、外形 probe 経路を視覚的に確認できる。
- **利点**: AKS 内 synthetic traffic に依存しないため、AKS クラスタ停止時も SLI が bad sample を受け取れる。
- **制約**: Microsoft managed availability test の multi-location 実行は使わない。外形 source の冗長性は Function App の実行基盤に依存する。
- **制約**: Function host / Timer 停止中のサービス状態は再構成できないため、欠損 window は保守的に bad として扱う。これは service 停止と probe source 停止を SLI 上で同じ低下として表現する。
- **制約**: 新しい denominator は window あたり 1 になるため、短時間の失敗は availability test 複数 location 方式より粒度が粗い。

## 代替案

### 案 A: ADR-011 の availability test 方式を継続する

- 不採用理由: `AppAvailabilityResults` を Prometheus metrics に変換する publisher は結局必要であり、ロケーション冗長性を重視しないなら中間層が多い。

### 案 B: Application Insights dependency telemetry を直接 SLI の正本にする

- 不採用理由: Azure Monitor SLI の input signal は Managed Prometheus metrics であり、Application Insights dependencies を直接 input signal にできない。dependency telemetry は Application Map と診断に使い、SLI input は Prometheus good / total metrics にする。

### 案 C: AKS 内 CronJob synthetic traffic に戻す

- 不採用理由: AKS クラスタ停止時に synthetic traffic generator も止まり、今回の silent failure が再発する。
