# API仕様 - AKS Chaos Lab

## エンドポイント
- GET `/` : メイン（Redis操作含む）
- GET `/health` : 健康確認（依存が健全なら200）

## ヘッダー
- `X-Request-ID`: リクエストID。未指定時はサーバーが UUID を生成し、レスポンスヘッダーにエコーする（トレース属性 `http.request_id` にも付与）。

## ステータスコード
- 200: 正常応答
- 503: 依存（主に Redis）障害による一時的なサービス不可
- 500: 想定外エラー（未捕捉例外）

## エラーレスポンス（標準形）
```
{
  "error": "Service Unavailable",
  "detail": "Redis operation failed: <reason>",
  "timestamp": "2025-08-11T00:00:00Z",
  "request_id": "abc123"
}
```

備考:
- 500（Internal Server Error）の場合、`detail` はデフォルトでは省略（`LOG_LEVEL=DEBUG` 時のみ詳細を含む実装）
- `/health` は Redis 無効/未設定時は 200、Redis 有効時は PING 計測に基づき 200/503 を返す。本文に `status` と `redis.connected/latency_ms` を含む（ヘルス結果は5秒間キャッシュ）。

## レスポンススキーマ
- `GET /`（200）:
  - `message`: 文字列（固定メッセージ）
  - `redis_data`: 文字列または null（Redis 無効時/未接続時は "Redis unavailable" 相当）
  - `timestamp`: ISO8601 文字列（UTC）
- `GET /`（503）: 上記「エラーレスポンス」参照（`error`=`Service Unavailable`、`detail` に Redis 失敗理由）
- `GET /health`（200/503）:
  - `status`: `healthy` | `unhealthy`
  - `redis`: `{ connected: boolean, latency_ms: number }`
  - `timestamp`: ISO8601 文字列（UTC）

## 認証/認可
- 管理系操作は将来的に保護予定（現状は開発用ラボのため未認証でも可）。

## 可観測性
- OpenTelemetry によるトレース/メトリクス/ログ送信（Application Insights）
- Redis 接続のカスタムメトリクスを送信（`redis_connection_status`, `redis_connection_latency_ms`）
- Chaos Experiment ID 等のカスタム属性付与（メタデータ付与は今後の拡張）
