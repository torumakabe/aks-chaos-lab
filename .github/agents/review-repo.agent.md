---
name: review-repo
description: リポジトリ全体の衛生チェック。instructions の鮮度、ADR/Feature Doc の健全性、追跡衛生、規約整合性を確認する。「リポジトリを点検」「衛生チェック」「hygiene」「priming をレビュー」「instructions を見直して」「review-repo」と言われたら使う。
---

リポジトリの衛生状態を包括的にチェックし、問題を報告・修正する。

## 原則

- `copilot-instructions.md` はコンパクトに保つ（肥大化を防ぐ）
- 詳細知識はスキル（`.github/skills/`）に分離し、instructions には参照だけ書く
- 不要になった記述の削除も提案する（追加だけでなく）
- 問題の修正は**ユーザーの承認後**に行う

## チェック項目

### 1. copilot-instructions.md の整合性

#### 1a. ディレクトリ構造

リポジトリの top-level ディレクトリを走査し、「プロジェクト構造」セクションと照合する。

- 記載されているが存在しないディレクトリはないか
- 存在するが記載されていないディレクトリはないか
- 説明文が実態と一致しているか

#### 1b. 知識ソースの参照先

「知識ソース」セクションに列挙されたパスを検証する。

- 参照先が実在するか（`docs/adr/`, `.github/skills/` 等）
- 新しいスキルやナレッジソースが追加されていないか

#### 1c. エージェント一覧

`.github/agents/` 内のエージェント一覧を確認する。

- 各エージェントの `description` がプロジェクトの実態に即しているか
- `copilot-instructions.md` 内でエージェント名を参照している箇所があれば、実在するか

### 2. git 追跡の衛生

git で追跡されているファイルに、追跡すべきでないものが含まれていないか確認する。

```bash
git ls-files --cached | grep -E '\.(whl|pyc|pyo)$|__pycache__|\.ruff_cache|\.DS_Store|\.env$'
```

検出されたファイルがあれば `git rm --cached` を提案する。

### 3. 品質ゲートの確認

品質ゲートが通るか確認する:

- Python: `cd src && make qa`（ruff + ty + pytest）
- Bicep: `az bicep build --file infra/main.bicep`

### 4. ADR 健全性

`docs/adr/INDEX.md` を読み、Accepted な ADR の一覧を確認する。

- 各 ADR ファイルが実在するか
- ADR が参照しているコードパス（`infra/`, `src/`, `k8s/` 等）が存在するか
- 詳細なコード照合が必要な場合は `manage-adr`（パス E）の実行を提案する

### 5. Feature Document 健全性

`docs/features/` を走査する。

- 各 Feature Document の最終更新日を `git log -1 --format=%ci -- <file>` で確認する
- 長期未更新（目安: 30 日以上）のものがあれば、卒業（`manage-adr`）か破棄かを提案する

### 6. スキル整合性

`.github/skills/` 内の各ディレクトリに `SKILL.md` が存在するか確認する。

- `SKILL.md` がないスキルディレクトリ → 作成を提案

## 出力

1. 各チェック項目の結果を一覧で報告する（✅ / ⚠️ / ❌）
2. 問題がある項目について具体的な修正案を提示する
3. ユーザーの承認を得てから編集を適用する
