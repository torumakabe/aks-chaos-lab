# Feature Specification: uv への依存パッケージ管理一本化

**Feature Branch**: `002-uv-consolidation`  
**Created**: 2025-12-08  
**Status**: In Progress  
**Input**: User description: "Pythonアプリケーションの依存パッケージ管理をuvに一本化。requirements.txtの利用を廃止する"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - コンテナビルドでuvを使用 (Priority: P1)

開発者として、Dockerコンテナのビルド時にuvを使用して依存パッケージをインストールしたい。これにより、ローカル開発とコンテナビルドで同じパッケージマネージャーを使用でき、環境差異によるトラブルを防止できる。

**Why this priority**: コンテナビルドは本番環境へのデプロイに直接影響するため、最も重要な変更

**Independent Test**: Dockerイメージをビルドし、アプリケーションが正常に起動することを確認できる

**Acceptance Scenarios**:

1. **Given** pyproject.toml に依存関係が定義されている、**When** `docker build` を実行する、**Then** uv を使用して依存関係がインストールされ、イメージが正常にビルドされる
2. **Given** ビルドされたDockerイメージ、**When** コンテナを起動する、**Then** FastAPIアプリケーションが正常に起動し、ヘルスチェックエンドポイントが応答する
3. **Given** pyproject.toml の依存関係が更新されている、**When** `docker build` を再実行する、**Then** 新しい依存関係が反映されたイメージがビルドされる

---

### User Story 2 - requirements.txt ファイルの廃止 (Priority: P2)

開発者として、requirements.txt ファイルを削除し、pyproject.toml のみで依存関係を管理したい。これにより、依存関係の定義が単一の場所に集約され、メンテナンスが容易になる。

**Why this priority**: ファイル削除は P1 の変更後に安全に実施でき、影響範囲が限定的

**Independent Test**: requirements.txt が存在しない状態でローカル開発とコンテナビルドが正常に動作することを確認できる

**Acceptance Scenarios**:

1. **Given** requirements.txt が削除されている、**When** `docker build` を実行する、**Then** ビルドが成功する
2. **Given** requirements.txt が削除されている、**When** ローカルで `uv sync` を実行する、**Then** 開発環境が正常にセットアップされる

---

### User Story 3 - Makefile の更新 (Priority: P3)

開発者として、Makefile から requirements.txt 生成ターゲットを削除したい。これにより、不要なコマンドがなくなり、ワークフローがシンプルになる。

**Why this priority**: Makefile の変更は開発者体験の改善であり、本番環境に直接影響しない

**Independent Test**: `make help` で requirements ターゲットが表示されないことを確認できる

**Acceptance Scenarios**:

1. **Given** 更新された Makefile、**When** `make help` を実行する、**Then** requirements ターゲットが表示されない
2. **Given** 更新された Makefile、**When** 他の make ターゲット（test, lint, qa など）を実行する、**Then** 正常に動作する

---

### User Story 4 - CI ワークフローの更新 (Priority: P4)

開発者として、GitHub Actions CI ワークフローで使用する uv バージョンをローカル・ Docker と一貫させたい。これにより、すべての環境で同じ uv バージョンを使用し、lockfile 互換性の問題を防止できる。

**Why this priority**: CI は直接本番環境に影響しないが、バージョン不整合によるビルド失敗を防止する

**Independent Test**: CI ワークフローが成功し、uv バージョンが `.uv-version` と一致することを確認

**Acceptance Scenarios**:

1. **Given** 更新された CI ワークフロー、**When** GitHub Actions が実行される、**Then** `.uv-version` で指定されたバージョンの uv が使用される
2. **Given** 更新された CI ワークフロー、**When** `uv sync` が実行される、**Then** `--locked` フラグにより lockfile の整合性が検証される
3. **Given** 更新された CI ワークフロー、**When** 公式 Action `astral-sh/setup-uv` が使用される、**Then** キャッシュが自動管理され、ワークフローが簡素化される

---

### Edge Cases

- uv がインストールされていない環境でのビルドはどうなるか？ → Dockerfile 内で uv をインストールするため問題なし
- Docker ビルドキャッシュの効率は維持されるか？ → pyproject.toml と uv.lock を先にコピーし、依存関係インストール後にアプリケーションコードをコピーする層分離により維持
- マルチステージビルドは必要か？ → 現時点では不要。将来的に uv をランタイムイメージに含めたくない場合は検討
- CI で `.uv-version` が見つからない場合は？ → `astral-sh/setup-uv` の `version-file` オプションで明示的にパスを指定

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Dockerfile は uv を使用して依存パッケージをインストールしなければならない（MUST）
- **FR-002**: Dockerfile は pyproject.toml と uv.lock を使用して再現可能なビルドを提供しなければならない（MUST）
- **FR-003**: src/requirements.txt ファイルは削除されなければならない（MUST）
- **FR-004**: Makefile から requirements ターゲットは削除されなければならない（MUST）
- **FR-005**: Docker ビルドキャッシュの効率を維持するため、依存関係ファイルとアプリケーションコードは別の層でコピーされなければならない（MUST）
- **FR-006**: ビルドされたコンテナはセキュリティのため non-root ユーザーで実行されなければならない（MUST）
- **FR-007**: CI ワークフローは公式 Action `astral-sh/setup-uv` を使用しなければならない（MUST）
- **FR-008**: CI ワークフローは `.uv-version` ファイルから uv バージョンを読み込まなければならない（MUST）
- **FR-009**: CI ワークフローは `uv sync --locked` を使用して lockfile の整合性を検証しなければならない（MUST）

### Key Entities

- **pyproject.toml**: プロジェクトの依存関係定義ファイル。uv および PEP 621 に準拠
- **uv.lock**: uv が生成するロックファイル。依存関係のバージョンを固定
- **Dockerfile**: コンテナイメージのビルド定義
- **.uv-version**: uv バージョンを一元管理するファイル。Dockerfile と CI が参照
- **ci.yml**: GitHub Actions CI ワークフロー定義

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Docker ビルドが成功し、アプリケーションが正常に起動する
- **SC-002**: ビルドされたコンテナで `curl http://localhost:8000/health` が 200 を返す
- **SC-003**: `make qa` がすべて成功する（既存のテスト・リント・型チェックが通る）
- **SC-004**: requirements.txt がリポジトリから削除されている
- **SC-005**: Makefile に requirements ターゲットが存在しない
- **SC-006**: CI ワークフローが `astral-sh/setup-uv` を使用し、`.uv-version` からバージョンを読み込んでいる
- **SC-007**: CI ワークフローの全ジョブが成功する

## Assumptions

- uv.lock ファイルはすでにリポジトリに存在する、または作成される
- Docker ビルド環境はインターネットに接続されており、uv パッケージをダウンロードできる
- Python 3.13 ベースイメージを継続使用する
