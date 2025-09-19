### バリデーション - Fleet Manual Gate アラート確認 - 2025-09-19T00:00:00Z
**目的**: 追加した Scheduled Query Rule と KQL テンプレートを含む Bicep テンプレートの整合性を確認する。
**コンテキスト**: Fleet モジュールへ pending gate アラートを追加した直後。
**実行**:
```bash
~/.azure/bin/bicep build infra/main.bicep
```
**出力**:
```
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(40,16) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(52,22) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/members@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(61,30) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/updateStrategies@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(89,29) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
```
**検証**: ビルドは終了コード0で成功。警告はプレビュー API の型未提供による既知のもの。
**次**: 実環境で Pending Gate 発生時にアラートが発火するか確認するテストを計画する。
