---
name: repository-freshness-checker
description: 対象ファイルを直接読み、公開Markdownリンクの到達性とAzure Functions extension bundleのsupport範囲をcheck-onlyで確認する。鮮度確認、公開リンク確認、extension bundleのsupport確認で使用する。
---

# Repository Freshness Checker

公開Markdownリンクの到達性とAzure Functions extension bundleのsupport範囲を確認する。本文と実装の一致や通常のversion更新候補は確認範囲に含めない。

## 入力と対象の確認

入力は対象リポジトリ、ディレクトリ、またはMarkdownとFunctionsの`host.json`のファイル指定である。事前の機械検査や対象抽出JSONは不要。

ファイル指定では指定された全ファイルを対象にする。ディレクトリ指定では配下のMarkdownとFunctionsの`host.json`を列挙する。Gitリポジトリ全体では、追跡ファイルとignoreされていない未追跡ファイルを対象にする。対象commitと既存の作業変更を記録する。列挙失敗、指定ファイルの欠落、読取失敗を対象0件で補わない。

1. Markdownを直接読み、公開HTTP/HTTPSリンクをファイルと行番号とともに記録する。通常のリンク、参照形式、autolinkと本文中のURLを確認し、インラインコードやコードブロック内のURLは除く。公開か判断できないリンクは接続せず、理由付きで対象外とする。
2. Functionsの`host.json`を直接読み、`extensionBundle.version`のrangeを記録する。rangeを解釈して指定major範囲を特定する。JSON不正、version欠落、不正なrangeは確認できた問題として指摘（`issue`）に残す。設定の不備を確認できてもsupport範囲の判断ができなければ、確認状況は`unverified`とし、不備と未確認の事項を併記する。読めないファイルや解釈できるか判断できないrangeも`unverified`とする。リポジトリ固有のpin形式や出現数の機械検査は、この公開support確認に含めない。

## check-onlyの操作範囲

- ファイルの編集、自動更新、commit、push、PR作成を行わない。
- Azure subscription、AKS cluster、Fleet、resource provider、feature登録状態など、認証が必要なAzure実環境を照会しない。
- 公開情報を取得できない場合は`unverified`とし、到達可能またはsupport対象と推定しない。
- support対象外を検出しても自動更新しない。影響、移行先、確認が必要な互換性を報告する。

Docker base imageのEOLは確認しない。

## 情報源と判定

公式情報を優先し、利用可能な公開文書の検索、取得手段を使う。特定のツールが利用できないことだけで終了せず、許可された代替手段を使う。取得できなければその対象を`unverified`（`evidence-unavailable`）とする。

公開Markdownリンクは重複URLごとに一度取得し、到達性とredirect先を記録する。`404`と`410`は到達性の確認状況を`verified`とし、リンク切れを指摘（`issue`）に残す。timeout、TLS障害、rate limit、server error、認証要求は`unverified`。正常な応答でもログイン画面や応答内容の制限により到達性を判断できなければ`unverified`とする。到達性を判断でき、指摘がない場合も`verified`とする。本文が現在の実装と一致するかは、このスキルの判定に含めない。

Azure Functions extension bundleは、次の固定情報源でsupport範囲を確認する。

https://learn.microsoft.com/azure/azure-functions/extension-bundles#bundle-versions

宣言したmajor範囲のsupportを公式情報で判断できれば`verified`とし、support対象外なら指摘（`issue`）を残す。複数majorを含む場合もrange全体について判断し、一部majorの掲載だけで確認済みにしない。公開情報から判断できなければ`unverified`（`assessment-incomplete`）とする。latestとの比較は行わない。

## 結果と完了条件

対象範囲、対象commitと作業変更、確認日時、対象ファイル数、リンクとbundleそれぞれの対象数と確認状況別件数を返す。確認状況は`verified`（確認済み）、`unverified`（未確認）、`excluded`（理由付き対象外）とし、指摘（`issue`）と別に記録する。リンクはファイルと行番号、bundleはファイルと設定名で識別し、現在値、確認状況、指摘、理由、根拠URL、redirect先または公開support範囲を示す。同じURLを一度だけ取得しても、ファイル内の各参照を結果に含める。

`verified`は問題がないという意味ではなく、必要な確認を終えたことを表す。指摘は確認状況と別に列挙し、件数も別に数える。指摘があって未確認の事項も残る場合は両方を保持する。「問題なし」は必要な確認を終え、指摘がない範囲についてだけ述べる。

未着手のファイルと対象は`unverified`（`not-processed`）、読取失敗は`unverified`（`target-unresolved`）として報告する。未読ファイルは対象数が不明なため別に記録する。未処理と公開情報の取得不能を同じ理由で報告しない。

全対象ファイルを読み、対象ごとの必要な確認と根拠が揃った場合だけ「確認完了」とする。指摘の存在は確認完了と両立する。`unverified`、未読ファイル、未確定の対象範囲があれば「確認未完了」。対象0件は対象範囲の確認成否とともに示し、公開情報を照合済みとは報告しない。

全体statusや合否は付けず、確認の完了状況、指摘、未確認対象を報告する。HTTP応答、処理の終了コード、診断、例外、timeoutは実行結果として別に記録し、対象の問題へ読み替えない。実行エラーがある場合も、それまでに確認できた内容を保持し、未確認となった範囲を示す。対象の読み取りや結果の形式だけを公開情報確認の成功と扱わない。
