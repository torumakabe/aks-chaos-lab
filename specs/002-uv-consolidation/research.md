# Research: uv への依存パッケージ管理一本化

**Feature**: 002-uv-consolidation  
**Date**: 2025-12-08  
**Status**: Complete

---

## 1. uv Docker 統合のベストプラクティス

### 決定: uv バイナリをコピーする方式を採用

**理由**: 
- 公式 distroless イメージからバイナリをコピーする方法が推奨されている
- インストーラースクリプト方式より軽量で高速
- バージョン固定が容易

**検討した代替案**:
1. ~~インストーラースクリプト (`install.sh`)~~: curl 依存、追加パッケージが必要
2. ~~uv 公式イメージをベースにする~~: Python バージョン制御が複雑になる

**推奨パターン**:
```dockerfile
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
```

### 決定: バージョン固定を行う

**理由**: 再現可能なビルドのため

**推奨**: ARG でバージョンを指定し、ビルド時に上書き可能にする
```dockerfile
ARG UV_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /bin/
```

**ローカルバージョンとの同期**: `uv --version` の出力と Dockerfile の `UV_VERSION` を合わせる

---

## 2. レイヤーキャッシュ最適化

### 決定: 中間レイヤー戦略を採用

**理由**:
- 依存関係は頻繁に変わらないため、キャッシュが効く
- アプリケーションコードのみの変更時にビルドが高速化

**推奨パターン**:
```dockerfile
# 依存関係ファイルを先にコピー
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# アプリケーションコードをコピー
COPY . /app

# プロジェクトを同期
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
```

### 決定: `--locked` フラグを使用

**理由**: lockfile が最新であることを検証し、再現可能なビルドを保証

---

## 3. 仮想環境の扱い

### 決定: .venv をイメージ内に作成し、PATH に追加

**理由**:
- uv はデフォルトで `.venv` に仮想環境を作成
- `uv run` を使うより直接実行が高速

**推奨パターン**:
```dockerfile
ENV PATH="/app/.venv/bin:$PATH"
```

### 決定: .dockerignore で .venv を除外

**理由**: ローカルの仮想環境はプラットフォーム依存のため、イメージには含めない

---

## 4. 本番ビルド最適化

### 決定: バイトコードコンパイルを有効化

**理由**: 起動時間の改善

**推奨**:
```dockerfile
RUN uv sync --compile-bytecode
```

### 決定: キャッシュマウントを使用

**理由**: ビルド間でのキャッシュ共有によるパフォーマンス向上

**推奨**:
```dockerfile
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync
```

---

## 5. セキュリティ考慮事項

### 決定: non-root ユーザーでの実行を継続

**理由**: 既存の Dockerfile と同様のセキュリティプラクティスを維持

### 決定: マルチステージビルドは現時点では不採用

**理由**: 
- uv バイナリのサイズは比較的小さい（~30MB）
- 運用の複雑さを避ける
- 将来的に必要になれば検討

---

## 6. Makefile の変更

### 決定: `requirements` ターゲットを削除

**理由**: requirements.txt が不要になるため

### 決定: `.PHONY` 宣言から `requirements` を削除

**理由**: ターゲット自体がなくなるため

---

## 7. 最終的な Dockerfile 設計

```dockerfile
FROM python:3.13-slim

# Install uv (version can be overridden with --build-arg UV_VERSION=x.x.x)
ARG UV_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /bin/

WORKDIR /app

# Copy dependency files first (cache optimization)
COPY pyproject.toml uv.lock ./

# Install dependencies only (not the project)
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --compile-bytecode

# Copy application code
COPY app ./app

# Create non-root user and set permissions
RUN groupadd -g 10001 app && \
    useradd -r -u 10001 -g app app && \
    chown -R app:app /app

ENV PORT=8000
EXPOSE 8000

USER app:app

# Add virtual environment to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Run with uvicorn directly (not uv run)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
```

---

## 8. uv バージョン更新の運用

### 更新が必要な箇所

| 項目 | 場所 | 更新方法 |
|------|------|----------|
| ローカル開発環境 | システム | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker ビルド | `src/Dockerfile` の `ARG UV_VERSION` | 手動編集 |
| ロックファイル | `src/uv.lock` | `uv lock` で自動更新 |

### 自動化: Makefile に検証ターゲットを追加

`make check-uv-version` でローカルと Dockerfile のバージョン不一致を検出：

```makefile
check-uv-version: ## Check if local uv version matches Dockerfile
	@LOCAL_UV=$$(uv --version | awk '{print $$2}'); \
	DOCKER_UV=$$(grep -oP 'ARG UV_VERSION=\K[0-9.]+' Dockerfile); \
	if [ "$$LOCAL_UV" != "$$DOCKER_UV" ]; then \
		echo "⚠️  uv version mismatch: local=$$LOCAL_UV, Dockerfile=$$DOCKER_UV"; \
		echo "   Update Dockerfile: ARG UV_VERSION=$$LOCAL_UV"; \
		exit 1; \
	else \
		echo "✓ uv version match: $$LOCAL_UV"; \
	fi
```

### 手動更新手順

1. **ローカル uv を更新**:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv --version  # 新バージョンを確認
   ```

2. **Dockerfile を更新**:
   ```bash
   # src/Dockerfile の ARG UV_VERSION を編集
   sed -i "s/ARG UV_VERSION=.*/ARG UV_VERSION=$(uv --version | awk '{print $2}')/" src/Dockerfile
   ```

3. **ロックファイルを更新**（必要に応じて）:
   ```bash
   cd src && uv lock
   ```

4. **検証**:
   ```bash
   cd src && make check-uv-version
   docker build -t chaos-app:test .
   ```

### Constitution への適用

この変更は「依存パッケージのバージョン更新」に該当し、Constitution IV の適用除外により Spec Kit ワークフローを経由せず直接実施可能：

> **適用除外**: 依存パッケージのバージョン更新（セキュリティパッチ、バグ修正）

---

## 参考資料

- [uv Docker Integration Guide](https://docs.astral.sh/uv/guides/integration/docker/)
- [uv-docker-example](https://github.com/astral-sh/uv-docker-example)
