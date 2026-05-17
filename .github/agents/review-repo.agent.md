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

### 7. プレビュー機能 / リソースプロバイダー登録の鮮度

`docs/deployment.md` の「プレビュー機能とリソースプロバイダー登録」セクションに記載されている `az feature register` 対象が、まだプレビュー扱いで明示登録が必要か確認する。GA すると `az feature register` は不要（または no-op）になり、手順が陳腐化する。

対象 feature の現状を確認する:

```bash
# docs/deployment.md に記載された feature をすべて確認する
for ns_name in \
  "Microsoft.ContainerService/AKS-AddonAutoscalingPreview" \
  "Microsoft.ContainerService/AzureMonitorAppMonitoringPreview" \
  "Microsoft.ContainerService/AKS-OMSAppMonitoring" \
  "Microsoft.Insights/OtlpApplicationInsights"; do
  ns="${ns_name%/*}"
  name="${ns_name#*/}"
  echo "=== ${ns_name} ==="
  az feature show --namespace "$ns" --name "$name" --query "{state:properties.state}" -o tsv 2>/dev/null \
    || echo "  (not found — may have been GA'd and retired)"
done
```

判定基準:

- `Registered` のまま → プレビューが続いている、記載を維持
- `NotRegistered` でも feature 自体は存在 → プレビュー継続中、記載を維持
- feature が見つからない（`az feature show` がエラー） → GA して feature flag が削除された可能性が高い。Microsoft Learn / Azure Updates で GA 状況を確認し、確認できたら `docs/deployment.md` から該当行を削除する提案を出す
- ADR に対応する記述（例: ADR-006 の OTLP プレビュー機能）がある場合は、ADR 側にも GA 反映の更新を提案する

迷ったら Microsoft Learn の該当ページ（例: AKS の "What's new" / OTLP for Application Insights ドキュメント）で GA 表記を一次確認する。

### 8. ワークアラウンドの棚卸し

`docs/workarounds.md` は本リポジトリで継続中のワークアラウンドと、それぞれの **解消条件** をまとめた棚卸しドキュメント。GA や仕様改善で不要になったものを剥がすため、`review-repo` 実行時にレビューする。

走査手順:

1. `docs/workarounds.md` を読み、各エントリの **解消条件** と **確認方法** を確認する。
2. プレビュー機能 GA / API バージョン GA 系（C-1, C-2, B-1, B-4）は §7 のチェックと連動して状況を確認する。
3. upstream issue 系（D-1: prometheus-collector#483 等）は GitHub issue のステータスを確認する。
4. 自前で運用ワークアラウンドにしているもの（A-1〜A-5, B-2, B-3）は、Microsoft Learn / Azure Updates で当該機能の改善 announcement が出ていないか確認する。
5. リポジトリ側の状況とドキュメントが食い違っていないか確認する:
   - `docs/workarounds.md` に書いてある **場所** のファイル / 行が実在するか
   - すでに剥がされたワークアラウンドが残っていないか
   - 新たに追加されたワークアラウンドが棚卸しに記載されているか

剥がせる候補が見つかったら、ユーザーに以下を提案する:

- `docs/workarounds.md` から該当エントリを削除
- 関連コード / ADR / README / `docs/deployment.md` / `docs/observability.md` の該当箇所を更新
- 必要なら ADR を Superseded / 新規 ADR で置き換え（`manage-adr` 経由）

新しいワークアラウンドを追加する場合は、棚卸しの構造（概要 / 理由 / 場所 / 解消条件 / 確認方法）に揃えて記載する。

## 出力

1. 各チェック項目の結果を一覧で報告する（✅ / ⚠️ / ❌）
2. 問題がある項目について具体的な修正案を提示する
3. ユーザーの承認を得てから編集を適用する
