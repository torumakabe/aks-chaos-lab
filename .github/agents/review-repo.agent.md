---
name: review-repo
description: リポジトリ全体の衛生状態を非編集で点検する。標準はfast（taskのみ）で、「full」「全検査」「鮮度確認」を指定された場合だけfullを実行する。「リポジトリを点検」「衛生チェック」「hygiene」「review-repo」と言われたら使う。特定の指示文やファイルの改善だけを依頼された場合は対象外。
---

# Review Repo

既存のtaskと専門skillを組み合わせ、今回の走査範囲におけるリポジトリの状態を報告する。レビュー中は追跡ファイルを編集しない。

## 実行モード

- 指定がない場合はfastを実行する。
- fastは`review-repo-fast`だけを実行する。外部APIとDockerを使わず、文書やAI運用資産の意味評価も行わない。
- `full`、全検査、鮮度確認のいずれかを明示された場合だけfullを実行する。
- fullは`review-repo-full`を実行し、その結果を使って文書とAI運用資産を評価する。公開Markdownリンク、Docker base imageのEOL、Functions extension bundleのsupport範囲は`repository-freshness-checker`、Bicep resource API versionは`bicep-api-version-updater`のcheck-onlyモードで確認する。
- fullはCopilotの専用worktreeまたは、利用者が明示的に用意した作業用worktreeで実行する。task内部で別のcopyや一時Git repositoryを作成しない。

## task

```bash
uv run --no-project "${PWD}/scripts/tasks.py" review-repo-fast \
  --inventory-json "<temp>/inventory.json" \
  --results-json "<temp>/review-fast.json"

uv run --no-project "${PWD}/scripts/tasks.py" review-repo-full \
  --inventory-json "<temp>/inventory.json" \
  --results-json "<temp>/review-full.json"
```

`<temp>`はリポジトリ外のOS一時ディレクトリとする。`review-repo-fast`と`review-repo-full`は、利用できないツールを`unverified`として記録し、実行可能な検査を継続する。

fastは次を実行する。

- `check-repo-health`
- `check-uv-version`
- `check-public-lock`
- `check-publisher-requirements`
- `check-version-pins`

fullはfastに加えて次を実行する。

- `qa-app --skip-publisher-requirements`
- `test-hooks`
- `build-bicep`
- `lint-k8s`
- `validate-helm-values`
- `lint-workflows`
- `validate-aw`

同じ検査をagent側で組み立て直さない。taskが出力した`status`と`reason_code`をそのまま使用する。

## fullの意味評価

| 種類 | 確認内容 | 補助するagentまたはskill |
|---|---|---|
| `.github/copilot-instructions.md` | 記載した構造、参照先、agent、skillが実在し、現在の運用と一致する | なし |
| agent | trigger、入力、出力、副作用、参照するtaskとskillが実態と一致する | agent自身の契約テスト |
| skill | trigger、責務、入力、出力、完了条件、外部情報源が実態と一致する | 対象skillの契約 |
| ADR | INDEXとの対応、必須見出し、Status、Acceptedな判断と実装の一致 | `manage-adr` |
| Feature Document | 決定事項、未完了作業、現在状態と実装の一致 | `resume` |
| `docs/workarounds.md` | 概要、理由、場所、解消条件、確認方法と実装の一致 | 該当する専門skill |
| READMEと運用文書 | path、task、command、説明と現在の実装の一致 | 該当する専門skill |
| workflow sourceと生成物 | source、生成lock、actions lock、参照task、参照skillの一致 | `lint-workflows`、`compile-aw` |

inventoryへの出現だけで`pass`にしない。評価できない対象は理由を付けて`unverified`とする。明示した形式、参照先、現在の実装との不一致は`fail`とする。

## 責務の境界

- 通常のversion更新候補はRenovateが検出する。gh-awとLefthookは明示的な保守作業で更新する。
- fastはリポジトリ内のversion契約だけを検査する。
- AKSの公開更新情報は`aks-updates-analyzer`が担当する。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態を照会しない。
- ファイルの自動更新、commit、push、Pull Request作成を行わない。

## 出力

実行モード、対象commit、状態別の検査結果、inventory categoryごとの対象数、`unverified`と`excluded`の理由、修正が必要なファイルと計画を報告する。結論は「今回の走査範囲では」と表現し、「すべて確認済み」と断定しない。
