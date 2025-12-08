# Quickstart: uv への依存パッケージ管理一本化

**Feature**: 002-uv-consolidation  
**Date**: 2025-12-08

---

## 概要

この機能は、Python 依存パッケージ管理を `uv` に一本化し、`requirements.txt` を廃止します。

## 変更対象ファイル

| ファイル | 変更内容 |
|----------|---------|
| `src/Dockerfile` | uv sync ベースに移行 |
| `src/Makefile` | requirements ターゲット削除 |
| `src/requirements.txt` | ファイル削除 |

## 実装手順

### Step 1: Dockerfile の更新

現在の Dockerfile を uv ベースに変更します。

**変更前**:
```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

**変更後**:
```dockerfile
# Install uv (version can be overridden with --build-arg UV_VERSION=x.x.x)
ARG UV_VERSION=0.9.16
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /bin/

# Copy dependency files first (cache optimization)
COPY pyproject.toml uv.lock ./

# Install dependencies
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --compile-bytecode
```

### Step 2: requirements.txt の削除

```bash
rm src/requirements.txt
```

### Step 3: Makefile の更新

`requirements` ターゲットと `.PHONY` 宣言から削除します。

### Step 4: .dockerignore の確認

`.venv` が除外されていることを確認します（既存で対応済みの場合はスキップ）。

## 検証手順

```bash
# Docker ビルド
cd src
docker build -t chaos-app:test .

# コンテナ起動テスト
docker run --rm -p 8000:8000 chaos-app:test &
sleep 3
curl http://localhost:8000/health

# 既存テストの実行
make qa
```

## 成功基準

- [ ] Docker ビルドが成功する
- [ ] コンテナが正常に起動する
- [ ] ヘルスチェックが 200 を返す
- [ ] `make qa` がすべて成功する
- [ ] `requirements.txt` が削除されている
- [ ] Makefile に `requirements` ターゲットがない
