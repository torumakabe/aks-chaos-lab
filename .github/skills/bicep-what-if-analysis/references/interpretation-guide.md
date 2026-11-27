# What-If 結果の詳細解釈ガイド

`ShortCircuitedResourceId`、`potential Delete` と `Unsupported` の照合、同一 ID への複数操作、`readOnly` 根拠の確認、Bicep 文字列検索の解釈が必要な場合に参照する。

## `ShortCircuitedResourceId` と `potential Delete` / `Unsupported` の照合

Stack what-if に `ShortCircuitedResourceId` がある場合、ARM は実行時参照を含むリソース ID を解決できず、確定差分のない管理対象を `potential Delete` として報告することがある。この結果だけでは、テンプレートからリソースが外れたことも、適用時に削除されることも示さない。診断、変更確度、管理状態を確認し、実効入力と Bicep のリソース宣言をたどって削除候補との対応を調べる。ID の一致は現在の宣言との対応を示すだけで、適用時に削除されない保証にはならない。照合できない候補と未解決の値は明示し、Azure の予測と診断をそのまま報告する。診断や ID の一致だけで Bicep の ID 生成方法を変更したり、削除の有無や適用可否を断定したりしない。

`potential Delete` と `Unsupported` を照合する場合は、同じ what-if 結果の `properties.changes.resourceChanges` と分析器 JSON の `changes` から、それぞれの ID、リソース型、確度と診断を確認する。`Unsupported` の ID が式なら、宣言の条件、scope、parameters、変数、モジュールと参照先をたどり、解決できた値で ID を再計算する。Azure 照会が許可されている場合は参照先の現在値を読み取れるが、現在値と配備時の値が同じとは限らない。role assignment は ID に加えて scope、principal、role definition も照合する。式や条件を解決できない場合は未解決として残す。

文字列引数の `guid()` は、[ARM の仕様](https://learn.microsoft.com/azure/azure-resource-manager/templates/template-functions-string#guid)に従い、引数の順序と文字列を変えずに `-` で連結して UUID v5 を計算する。Python では、解決済み文字列のリストを `args` として次を使える。

```python
uuid.uuid5(uuid.UUID("11fb06fb-712d-4ddd-98c7-e71bbd588830"), "-".join(args))
```

ID の残りの構成も式と照合し、GUID だけの一致をリソース ID 全体の一致と扱わない。

## 同一リソース ID への Modify と Delete

同じリソース ID に Modify と Delete が返った場合は、両方を別の予測として残し、それぞれの確度、管理状態、プロパティ差分と診断を確認する。一方が他方を打ち消すと解釈せず、条件と現在の状態を調べても結果が決まらない場合は未解決として報告する。

## `NoEffect`、ローカル分類、`readOnly` の根拠確認

Azure が返した `NoEffect` は `evaluation.reason=noEffect` として記録する。readOnly パターンの一致は `localClassification` に候補として記録し、Azure の評価や `noise_confirmed` と混同しない。どちらも変更前後の値を表示し、分類だけを理由に差分を省略しない。JSON には全差分を残す。

readOnly の確認では、対象リソースの型、API バージョンとプロパティパスに対応する Bicep 型または公式 REST 仕様を参照する。Bicep の `BCP073` は対象パスが readOnly である根拠になるが、`BCP081` などで対象 API の型がない場合や仕様にパスがない場合は判断できない。親オブジェクトに設定可能な属性がある場合は、子属性の根拠を親全体へ広げない。readOnly と確認できても、それだけでは Azure が差分を返した原因や適用時の影響は確定しない。確認した根拠と未確認の理由を、対象のプロパティと変更前後の値を添えて Azure の予測と分けて報告する。

## 他リソースのプロパティに現れる差分と生 JSON スキーマ

NSG、Route Table、Private DNS Zone など他リソースへ紐付く対象の影響を確認するときは、そのリソースタイプ単体（例: `Microsoft.Network/networkSecurityGroups`）だけでなく、全リソースの `propertyChanges`（パスおよび before/after 値）と `modifySummary` を横断確認する。たとえば Azure Policy でサブネットへ挿入された NSG は、NSG リソース自体の操作ではなく親 VNet（`Microsoft.Network/virtualNetworks`）の `properties.subnets.<index>.properties.networkSecurityGroup` の `Delete` 差分として現れる。

`az stack-whatif sub show --with-property-changes true --output json` で取得する Azure 生 JSON では、各リソースのプロパティ差分は `propertyChanges` ではなく `resourceConfigurationChanges.delta`（および `before` / `after`）に格納される。分析器 JSON（`propertyChanges`）とキー名が異なるため、生 JSON を直接照会するときは `resourceConfigurationChanges.delta` を確認する。

## Bicep 文字列検索ステータス（`bicepSearch.status`）の解釈

Bicep 照合はソースの文字列検索による参考情報である。

- `singleMatch` / `multipleMatches`: 検索候補であり、参照、変数、モジュールを介した値の流れや実効値を確定しない。
- `noMatch`: 検索範囲に候補がないことのみを示す。「Bicep で管理されていない」と断定しない。
- `notSearched`: 該当ブロックなどを検索できなかったことを示す。

実際のリソース宣言や値の出所を確認できない場合は未確認と伝える。noise pattern、`pending`、`noise_confirmed`、Bicep 検索結果は安全性や適用可否の結論に使わない。
