### バリデーション - Bicep ビルドによる Fleet 連携検証 - 2025-09-19T00:00:00Z
**目的**: 新規 Fleet モジュールおよび AKS テンプレート変更の構文妥当性を Bicep ビルドで確認する。
**コンテキスト**: `infra/modules/fleet.bicep` と `infra/modules/aks.bicep` を更新し、Base モードの更新管理を Fleet に移行した。プレビュー API のため型チェックが部分的にスキップされる可能性がある。
**決定**: `~/.azure/bin/bicep build infra/main.bicep` を実行し、ビルド成功と警告内容を確認する。
**実行**:
```bash
~/.azure/bin/bicep build infra/main.bicep
```
**出力**:
```
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(36,16) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(45,22) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/members@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(54,30) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/updateStrategies@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(82,29) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed. [https://aka.ms/bicep/core-diagnostics#BCP081]
```
**検証**: ビルドは正常終了（終了コード0）。警告はプレビュー API の型情報未提供に起因する想定内の挙動であり、追加の修正は不要。
**次**: 将来のプレビュー脱却後、型定義更新に伴い警告が解消されるか確認する。
