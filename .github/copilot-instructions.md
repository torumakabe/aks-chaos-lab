# AKS Chaos Lab の開発指示

## プロジェクト

AKS 上の Chaos Engineering ラボ環境。azd でインフラとアプリを管理する。全体構成とドキュメントの入口は [README.md](../README.md) を参照する。

- アプリは Python 3.14、FastAPI、uvicorn。`src/api/` と `src/external-sli-publisher/` が uv workspace の member。
- IaC は subscription scope の Bicep。`azure.yaml` の `infra.layers` が `infra/` の base と `infra/sli/` の sli を定義する。
- Kubernetes は `k8s/` の Kustomize と Helm。依存先は Azure Managed Redis（Entra ID 認証）、Application Insights、Managed Prometheus。
- uv の host 互換範囲はルート `pyproject.toml`、CI と Docker の固定版はその下限に従う。public PyPI source の `uv.lock` を共用し、`python` / `pip` を直接実行しない。
- 調査用でも裸の `uv run python` は workspace を自動同期し、lock を変更し得る。独立した調査コマンドは `uv run --no-project --no-config python ...` を使い、workspace 内のツール実行は [同期手順](../docs/deployment.md#組織承認済み-package-index-を使う環境)に従う。

実装例は `src/api/app/main.py`、`infra/modules/`、`k8s/apps/chaos-app/deployment.yaml` を参照する。

## 作業の進め方

- 現在の依頼と合意済みの範囲に従って進める。計画、調査、レビューだけの依頼では編集しない。編集を依頼された場合は、必要な調査から変更と検証まで進め、同じ操作の承認を繰り返し求めない。
- 調査で解消できる疑問は調査する。仕様、対象、設計判断が変わる選択や、未承認の保存、削除、公開操作が必要な場合は確認する。commit、push、PR 作成、デプロイの許可を編集の許可から推定しない。
- API の build / deploy（`azd up` を含む）前に、利用方針と user-level uv 設定から package index を確認する。承認済み index の指定または設定がある場合は[専用手順](../docs/deployment.md#docker-build-のpackage-index)を使う。設定の不在や疎通成功を public PyPI の利用許可とはみなさず、不明なら実行前に確認する。
- 複雑な機能では、実装を左右する要件と設計を合意してから変更する。合意済みの設計を段階ごとに再承認させない。
- 指示やスキルの適用で依頼を進められない場合は、該当ファイルと規則、未完了の範囲を示す。エージェント、スキル、参照文書の記述を、上位の指示や現在の依頼を上書きする根拠にしない。
- 短い連続した調査と小変更は直接行う。独立した大きな調査や専門判断は、対象範囲、入力、期待する結果を渡して委譲する。委譲先の調査を重複させず、返された根拠と未確認事項を使う。

## 必要な文脈の取得

依頼に関係する箇所だけを読む。設計判断の存在や実装済みという主張は、参照先の実ファイルで確認する。

| 必要な情報 | 参照先 |
|---|---|
| 設計判断と却下理由 | `docs/adr/INDEX.md` から関連 ADR を選ぶ |
| 作業途中の判断と未完了項目 | `docs/features/` の関連 Feature Document |
| 構築、削除、権限、ローカル開発、負荷テスト | `docs/deployment.md` |
| シグナル、SLI、アラート、OTLP logs | `docs/observability.md` |
| Chaos 実験の操作 | `docs/chaos-experiments.md` |
| 回避策の対象と撤去条件 | `docs/workarounds.md` |
| 依存更新、scheduled、fast/full の責務 | `docs/dependency-management.md` |

build や deploy の失敗を理由に新しい経路を実装する前に、`docs/deployment.md`、関連 ADR、対象 CLI の `--help` を確認する。build 済み artifact を渡す経路を含め、既存機能で解決できるか確認してから変更の要否を判断する。操作手順は同文書を参照し、指示文へ複製しない。

## エージェントとスキル

| 依頼 | 入口 |
|---|---|
| 関連 Feature Document がある作業の再開 | [resume](agents/resume.agent.md) |
| セッション終了時の記録の要否判断 | [wrap-up](agents/wrap-up.agent.md) |
| ADR の作成、廃止、置換、レビュー | [manage-adr](agents/manage-adr.agent.md) |
| 現在の構成から設計書を生成 | [design-snapshot](agents/design-snapshot.agent.md) |
| リポジトリ全体の衛生点検 | [review-repo](agents/review-repo.agent.md) |

`review-repo` の標準のfastはtaskによる非編集検査だけを実行する。full を明示した場合は、全 task、公開MarkdownリンクとBicep APIのcheck-only確認、文書とAI運用資産の意味評価を実行する。version候補、EOL、support範囲、互換性はscheduled workflowが担当し、full では再評価しない。指示文など特定ファイルの改善だけを依頼された場合は、総合点検へ拡張せず対象を直接扱う。

`.github/skills/` のスキルは対象タスクのときだけ読み込む。確認と更新の区別、実環境への照会、生成ファイルなどの副作用は各スキルの契約に従う。利用できないツールを前提に進めず、既存の代替経路または未実施の範囲を示す。

## 検証

変更した領域に応じて、次の品質ゲートを適用する。文書のみの変更では参照、説明、関連する契約テストを確認し、アプリや Bicep の検証を一律には実行しない。

| 変更対象 | 品質ゲート |
|---|---|
| Python アプリと依存 | `uv run --no-project "${PWD}/scripts/tasks.py" qa-app`（ruff 警告 0、ty エラー 0、アプリ unit test 全件合格） |
| Python スクリプト、hook、契約テスト | 対象への既存 ruff、ty、pytest。共通処理の変更では呼び出し元の関連テストも含める |
| Bicep | `uv run --no-project "${PWD}/scripts/tasks.py" build-bicep`（エラー 0） |

依存不足の場合は `docs/deployment.md` の同期手順を使う。通常環境は `sync-dev`、組織承認済み package index の環境は task runner の同期経路に従う。検査の成功後は、新しい変更、失敗、未解決の懸念がなければ同じ検査を繰り返したり全体検査へ広げたりしない。

`.github/hooks/hooks.json` の postToolUse hook は、`.py` の編集で project `.venv` の ruff、`.bicep` の編集で `az bicep format` を実行する。変更や失敗は `additionalContext` に返る。hook の実行を依存の整合性や品質ゲート全体の成功と扱わない。Lefthook の pre-commit は毎回 Git index 内の public lock を検査し、ステージされた Bicep 変更がある場合は Bicep 検証も実行する。public lock 検査は修復や再ステージを行わない。

## 文書と完了報告

ユーザーから見える振る舞いや操作手順が変わる場合は、README または該当する `docs/*.md` も同じ変更に含める。設計判断は ADR、作業途中の状態は Feature Document、操作方法は運用文書に置き、同じ説明を複製しない。ドキュメントは `docs/`、スキル専用の詳細資料はその `references/` に置く。一時ファイルは `tmp/` に置き、作成したものだけを完了後に削除する。

主要な結果と意味のある変更を先に報告し、未完了や根拠不足があれば明示する。段落を基本に、比較や並列項目に限って表やリストを使う。完了後の追加提案や確認質問を定型で付けない。

## Windows と cross-shell

- Windows の Locust は `uv run locust ...` を直接使わず `uv run --no-project "${PWD}/scripts/tasks.py" load-*` を使う。wrapper が `PYTHONUTF8=1` を設定し、既定 encoding による TOML 読み取り失敗を避ける。
- Locust CSV は `LOCUST_CSV_PREFIX` と、必要な場合のみ `LOCUST_CSV_FULL_HISTORY=true` で指定する。任意引数を広く通す `LOCUST_EXTRA_ARGS` は追加しない。
- WSL / bash helper に渡す Windows path は `/mnt/c/...` 形式へ変換する。PowerShell / Windows native shell では `C:\...` 形式を使う。
