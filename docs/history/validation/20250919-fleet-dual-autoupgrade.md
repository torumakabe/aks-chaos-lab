### バリデーション - Dual autoUpgradeProfiles 追加後のBicepビルド確認 - 2025-09-19T00:00:00Z
**目的**: Dual autoUpgradeProfiles 追加後の Bicep テンプレート整合性を確認する。
**実行**:
```bash
~/.azure/bin/bicep build infra/main.bicep
```
**結果**:
```
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(40,16) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed.
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(52,22) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/members@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed.
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(61,30) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/updateStrategies@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed.
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(89,41) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed.
/home/tomakabe/workspace/github.com/torumakabe/aks-chaos-lab/infra/modules/fleet.bicep(102,38) : Warning BCP081: Resource type "Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview" does not have types available. Bicep is unable to validate resource properties prior to deployment, but this will not block the resource from being deployed.
```
**評価**: プレビュー API の型未提供による警告のみ。ビルド成功（終了コード0）。
**次**: 実運用で Update Run の挙動を観測し、必要に応じてドキュメントにフィードバックする。
