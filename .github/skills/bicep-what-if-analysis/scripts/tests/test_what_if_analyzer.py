#!/usr/bin/env python3
"""
what_if_analyzer のユニットテスト

標準ライブラリの unittest のみを使用（ゼロ依存）
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# テスト対象モジュールのパスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from what_if_analyzer import (
    DisplayConfigLoader,
    NoisePatternLoader,
    _extract_actual_provider_type,
    contains_arm_reference,
    evaluate_property_change,
    extract_resource_changes,
    flatten_property_changes,
    format_azd_style_output,
    get_bicep_param_names,
    get_reference_info,
    is_create_false_positive,
    is_main_resource,
    is_readonly_property,
    parse_azure_yaml_layers,
    resolve_parameters_file_placeholders,
    run_what_if,
)


class TestEvaluatePropertyChange(unittest.TestCase):
    """evaluate_property_change のテスト"""

    def test_noeffect_returns_noise_confirmed(self) -> None:
        """NoEffect は noise_confirmed を返す"""
        result = evaluate_property_change(
            path="properties.sku.tier",
            change_type="NoEffect",
            before="Standard",
            after="Standard",
        )
        self.assertEqual(result["status"], "noise_confirmed")
        self.assertEqual(result["reason"], "noEffect")
        self.assertEqual(result["confidence"], "high")

    def test_readonly_property_returns_noise_confirmed(self) -> None:
        """readOnly プロパティは noise_confirmed を返す"""
        result = evaluate_property_change(
            path="properties.provisioningState",
            change_type="Modify",
            before="Succeeded",
            after="Updating",
        )
        self.assertEqual(result["status"], "noise_confirmed")
        self.assertEqual(result["reason"], "readOnly")

    def test_arm_reference_returns_noise_confirmed(self) -> None:
        """ARM 参照式を含む場合は noise_confirmed を返す"""
        result = evaluate_property_change(
            path="properties.subnetId",
            change_type="Modify",
            before="[reference(resourceId('Microsoft.Network/virtualNetworks', 'vnet'))]",
            after="/subscriptions/.../subnets/default",
        )
        self.assertEqual(result["status"], "noise_confirmed")
        self.assertEqual(result["reason"], "armReference")

    def test_unknown_property_returns_pending(self) -> None:
        """不明なプロパティは pending を返す"""
        result = evaluate_property_change(
            path="properties.customProperty",
            change_type="Modify",
            before="old",
            after="new",
        )
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["reason"])


class TestIsReadonlyProperty(unittest.TestCase):
    """is_readonly_property のテスト"""

    def test_provisioning_state_is_readonly(self) -> None:
        """provisioningState は readOnly"""
        self.assertTrue(is_readonly_property("properties.provisioningState"))

    def test_etag_is_readonly(self) -> None:
        """etag は readOnly"""
        self.assertTrue(is_readonly_property("etag"))

    def test_custom_property_is_not_readonly(self) -> None:
        """カスタムプロパティは readOnly ではない"""
        self.assertFalse(is_readonly_property("properties.customSetting"))


class TestContainsArmReference(unittest.TestCase):
    """contains_arm_reference のテスト"""

    def test_reference_function(self) -> None:
        """reference() 関数を検出"""
        self.assertTrue(contains_arm_reference("[reference(resourceId('...'))]"))

    def test_resourceid_function(self) -> None:
        """resourceId() 関数を検出"""
        self.assertTrue(
            contains_arm_reference(
                "[resourceId('Microsoft.Network/virtualNetworks', 'vnet')]"
            )
        )

    def test_plain_string(self) -> None:
        """通常の文字列は検出しない"""
        self.assertFalse(contains_arm_reference("/subscriptions/xxx/resourceGroups/rg"))

    def test_non_string(self) -> None:
        """文字列以外は False"""
        self.assertFalse(contains_arm_reference(123))
        self.assertFalse(contains_arm_reference(None))
        self.assertFalse(contains_arm_reference({"key": "value"}))


class TestFlattenPropertyChanges(unittest.TestCase):
    """flatten_property_changes のテスト"""

    def test_empty_delta(self) -> None:
        """空の delta は空リストを返す"""
        result = flatten_property_changes(None)
        self.assertEqual(result, [])

        result = flatten_property_changes([])
        self.assertEqual(result, [])

    def test_simple_change(self) -> None:
        """単純な変更を正しくフラット化"""
        delta = [
            {
                "path": "properties.enableRBAC",
                "propertyChangeType": "Modify",
                "before": True,
                "after": False,
            }
        ]
        result = flatten_property_changes(delta)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "properties.enableRBAC")
        self.assertEqual(result[0]["changeType"], "Modify")

    def test_nested_change(self) -> None:
        """ネストされた変更を正しくフラット化"""
        delta = [
            {
                "path": "properties",
                "propertyChangeType": "Modify",
                "children": [
                    {
                        "path": "addressSpace",
                        "propertyChangeType": "Modify",
                        "children": [
                            {
                                "path": "addressPrefixes",
                                "propertyChangeType": "Modify",
                                "before": ["10.0.0.0/16"],
                                "after": ["10.0.0.0/15"],
                            }
                        ],
                    }
                ],
            }
        ]
        result = flatten_property_changes(delta)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "properties.addressSpace.addressPrefixes")


class TestExtractResourceChanges(unittest.TestCase):
    """extract_resource_changes のテスト"""

    def test_normalizes_unsupported_extension_resource(self) -> None:
        """Unsupported な extensionResourceId を正規化する"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Unsupported",
                    "resourceId": (
                        "[extensionResourceId("
                        "'/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerRegistry/registries/acrtest', "
                        "'Microsoft.Authorization/roleAssignments', "
                        "guid('/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerRegistry/registries/acrtest', "
                        "reference('/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerService/managedClusters/akstest', "
                        "'2025-06-02-preview').identityProfile.kubeletidentity.objectId, "
                        "'AcrPull'))]"
                    ),
                }
            ]
        }

        result = extract_resource_changes(what_if_result)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["resourceType"],
            "Microsoft.ContainerRegistry/registries/providers/roleAssignments",
        )
        self.assertEqual(result[0]["resourceName"], "<dynamic>")
        self.assertEqual(
            result[0]["resourceId"],
            "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
            "Microsoft.ContainerRegistry/registries/acrtest/providers/"
            "Microsoft.Authorization/roleAssignments/<dynamic>",
        )
        self.assertEqual(
            result[0]["originalResourceId"],
            what_if_result["changes"][0]["resourceId"],
        )

    def test_uses_last_providers_segment_for_nested_extensions(self) -> None:
        """ネストした拡張リソースでも最後の providers を使う"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Unsupported",
                    "resourceId": (
                        "[extensionResourceId("
                        "'/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerService/managedClusters/"
                        "akstest/providers/Microsoft.Chaos/targets/chaosmesh', "
                        "'Microsoft.Authorization/roleAssignments', guid('scope', 'id'))]"
                    ),
                }
            ]
        }

        result = extract_resource_changes(what_if_result)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["resourceType"],
            "Microsoft.Chaos/targets/providers/roleAssignments",
        )
        self.assertEqual(
            result[0]["resourceId"],
            "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
            "Microsoft.ContainerService/managedClusters/akstest/providers/"
            "Microsoft.Chaos/targets/chaosmesh/providers/"
            "Microsoft.Authorization/roleAssignments/<dynamic>",
        )

    def test_keeps_existing_type_for_regular_extension_resource_ids(self) -> None:
        """通常の拡張リソース ID は既存の型抽出を維持する"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Modify",
                    "resourceId": (
                        "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
                        "Microsoft.Network/virtualNetworks/vnettest/subnets/snet-app/"
                        "providers/Microsoft.Authorization/roleAssignments/assignment1"
                    ),
                    "delta": [],
                }
            ]
        }

        result = extract_resource_changes(what_if_result)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0]["resourceType"],
            "Microsoft.Network/virtualNetworks/subnets/providers/roleAssignments",
        )


class TestNoisePatternLoader(unittest.TestCase):
    """NoisePatternLoader のテスト"""

    def test_load_default_patterns(self) -> None:
        """デフォルトパターンを読み込める"""
        loader = NoisePatternLoader()
        readonly = loader.get_readonly_patterns()
        self.assertIn("^provisioningState$", readonly)

    def test_load_custom_patterns(self) -> None:
        """カスタムパターンを読み込める"""
        loader = NoisePatternLoader()
        custom = loader.get_custom_patterns()
        # 共通の tags パターンが存在するはず
        patterns = [p[0] for p in custom]
        self.assertTrue(any("tags" in p for p in patterns))

    def test_missing_file_returns_empty(self) -> None:
        """存在しないファイルは空の辞書を返す"""
        loader = NoisePatternLoader("/nonexistent/path.json")
        readonly = loader.get_readonly_patterns()
        self.assertEqual(readonly, [])


class TestDisplayConfigLoader(unittest.TestCase):
    """DisplayConfigLoader のテスト"""

    def test_load_display_names(self) -> None:
        """表示名を読み込める"""
        loader = DisplayConfigLoader()
        names = loader.get_display_names()
        self.assertEqual(
            names.get("Microsoft.ContainerService/managedClusters"),
            "AKS Managed Cluster",
        )

    def test_load_filtered_types(self) -> None:
        """フィルタリング対象を読み込める"""
        loader = DisplayConfigLoader()
        filtered = loader.get_filtered_types()
        self.assertIn("microsoft.authorization/roleassignments", filtered)

    def test_missing_file_returns_empty(self) -> None:
        """存在しないファイルは空を返す"""
        loader = DisplayConfigLoader("/nonexistent/path.json")
        names = loader.get_display_names()
        self.assertEqual(names, {})


class TestGetReferenceInfo(unittest.TestCase):
    """get_reference_info のテスト"""

    def test_custom_tags_returns_warning(self) -> None:
        """tags.* は警告を返す"""
        result = get_reference_info(
            path="tags.Environment",
            before="dev",
            after="prod",
            bicep_definition={"status": "notDefined"},
        )
        self.assertIn("⚠️", result)

    def test_readonly_returns_lock_icon(self) -> None:
        """readOnly は🔒を返す"""
        result = get_reference_info(
            path="properties.provisioningState",
            before="Succeeded",
            after="Updating",
            bicep_definition={"status": "notDefined"},
        )
        self.assertIn("🔒", result)

    def test_defined_in_bicep_returns_pin(self) -> None:
        """Bicep 定義ありは📍を返す"""
        result = get_reference_info(
            path="properties.customSetting",
            before="old",
            after="new",
            bicep_definition={
                "status": "defined",
                "file": "main.bicep",
                "line": 42,
            },
        )
        self.assertIn("📍", result)

    def test_unknown_returns_question(self) -> None:
        """未分類は❓を返す"""
        result = get_reference_info(
            path="properties.unknownProperty",
            before="old",
            after="new",
            bicep_definition={"status": "notDefined"},
        )
        self.assertIn("❓", result)


class TestFormatAzdStyleOutput(unittest.TestCase):
    """format_azd_style_output のテスト"""

    def test_filtered_prometheus_rule_group_modify_is_visible(self) -> None:
        """filtered_resource_types 対象でも Modify は text 出力に表示する"""
        output_data = {
            "changes": [
                {
                    "operation": "Modify",
                    "resourceId": (
                        "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
                        "Microsoft.AlertsManagement/prometheusRuleGroups/"
                        "app-operational-alerts"
                    ),
                    "resourceType": "Microsoft.AlertsManagement/prometheusRuleGroups",
                    "resourceName": "app-operational-alerts",
                    "propertyChanges": [
                        {
                            "changeType": "Modify",
                            "path": "properties.rules.0.expression",
                            "referenceInfo": "❓ 未分類。確認推奨",
                        }
                    ],
                    "likelyFalsePositive": False,
                }
            ]
        }

        result = format_azd_style_output(output_data)

        self.assertIn("Modify", result)
        self.assertIn("Prometheus Rule Group", result)
        self.assertIn("app-operational-alerts", result)
        self.assertIn("properties.rules.0.expression", result)

    def test_filtered_prometheus_rule_group_nochange_is_hidden(self) -> None:
        """filtered_resource_types 対象の NoChange は引き続き非表示にする"""
        output_data = {
            "changes": [
                {
                    "operation": "NoChange",
                    "resourceId": (
                        "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
                        "Microsoft.AlertsManagement/prometheusRuleGroups/"
                        "app-operational-alerts"
                    ),
                    "resourceType": "Microsoft.AlertsManagement/prometheusRuleGroups",
                    "resourceName": "app-operational-alerts",
                    "propertyChanges": [],
                }
            ]
        }

        result = format_azd_style_output(output_data)

        self.assertEqual(result.strip(), "Resources:")

    def test_filters_known_acr_acrpull_unsupported(self) -> None:
        """既知の ACR AcrPull Unsupported だけを非表示にする"""
        output_data = {
            "changes": [
                {
                    "operation": "Unsupported",
                    "resourceId": (
                        "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
                        "Microsoft.ContainerRegistry/registries/acrtest/providers/"
                        "Microsoft.Authorization/roleAssignments/<dynamic>"
                    ),
                    "resourceType": (
                        "Microsoft.ContainerRegistry/registries/providers/"
                        "roleAssignments"
                    ),
                    "originalResourceId": (
                        "[extensionResourceId("
                        "'/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerRegistry/registries/acrtest', "
                        "'Microsoft.Authorization/roleAssignments', "
                        "guid('scope', reference('aks', '2025-06-02-preview')."
                        "identityProfile.kubeletidentity.objectId, 'AcrPull'))]"
                    ),
                    "resourceName": "<dynamic>",
                    "propertyChanges": [],
                }
            ]
        }

        result = format_azd_style_output(output_data)

        self.assertEqual(result.strip(), "Resources:")

    def test_filters_acr_role_assignment_by_provider_type(self) -> None:
        """ACR の拡張リソース role assignment は実際のプロバイダー型でフィルタされる"""
        output_data = {
            "changes": [
                {
                    "operation": "Unsupported",
                    "resourceId": (
                        "/subscriptions/test-sub/resourceGroups/test-rg/providers/"
                        "Microsoft.ContainerRegistry/registries/acrtest/providers/"
                        "Microsoft.Authorization/roleAssignments/<dynamic>"
                    ),
                    "resourceType": (
                        "Microsoft.ContainerRegistry/registries/providers/"
                        "roleAssignments"
                    ),
                    "originalResourceId": (
                        "[extensionResourceId("
                        "'/subscriptions/test-sub/resourceGroups/test-rg/"
                        "providers/Microsoft.ContainerRegistry/registries/acrtest', "
                        "'Microsoft.Authorization/roleAssignments', "
                        "guid('scope', 'pipeline-sp-id', 'AcrPush'))]"
                    ),
                    "resourceName": "<dynamic>",
                    "propertyChanges": [],
                }
            ]
        }

        result = format_azd_style_output(output_data)

        # 拡張リソース型マッチングにより Microsoft.Authorization/roleAssignments として
        # filtered_resource_types にマッチしフィルタされる
        self.assertEqual(result.strip(), "Resources:")


class TestPatternStats(unittest.TestCase):
    """パターン統計機能のテスト"""

    def test_record_pattern_match(self) -> None:
        """パターンマッチを記録できる"""
        loader = NoisePatternLoader()
        loader.record_pattern_match("^tags\\.", "custom_patterns")
        self.assertIn("custom_patterns:^tags\\.", loader._matched_patterns)

    def test_get_unused_patterns_empty(self) -> None:
        """統計ファイルがない場合は空リスト"""
        loader = NoisePatternLoader("/nonexistent/path.json")
        unused = loader.get_unused_patterns(days=30)
        self.assertEqual(unused, [])


class TestExtractActualProviderType(unittest.TestCase):
    """_extract_actual_provider_type のテスト"""

    def test_extension_resource_role_assignment(self) -> None:
        """AKS スコープのロールアサインメントから実際のプロバイダー型を抽出"""
        resource_id = (
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.ContainerService/managedClusters/aks-test/providers/"
            "Microsoft.Authorization/roleAssignments/guid-123"
        )
        result = _extract_actual_provider_type(resource_id)
        self.assertEqual(result, "Microsoft.Authorization/roleAssignments")

    def test_regular_resource(self) -> None:
        """通常リソースはプロバイダー型をそのまま返す"""
        resource_id = (
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.Chaos/experiments/exp-aks-test"
        )
        result = _extract_actual_provider_type(resource_id)
        self.assertEqual(result, "Microsoft.Chaos/experiments")

    def test_empty_resource_id(self) -> None:
        """空の ID は None を返す"""
        self.assertIsNone(_extract_actual_provider_type(""))


class TestIsMainResourceExtensionType(unittest.TestCase):
    """is_main_resource の拡張リソース型マッチングテスト"""

    def test_aks_scoped_role_assignment_is_filtered(self) -> None:
        """AKS スコープのロールアサインメントはフィルタされる"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.ContainerService/managedClusters/providers/roleAssignments",
            "resourceId": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.ContainerService/managedClusters/aks-test/providers/"
                "Microsoft.Authorization/roleAssignments/guid-123"
            ),
            "resourceName": "guid-123",
        }
        self.assertFalse(is_main_resource(change))

    def test_chaos_experiment_is_not_filtered(self) -> None:
        """Chaos experiments はフィルタされない（主要ワークロードリソース）"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Chaos/experiments",
            "resourceId": (
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Chaos/experiments/exp-aks-pod-failure"
            ),
            "resourceName": "exp-aks-pod-failure",
        }
        self.assertTrue(is_main_resource(change))


class TestIsCreateFalsePositiveWithPattern(unittest.TestCase):
    """is_create_false_positive のパターンマッチテスト（resourceType 対応）"""

    def test_chaos_experiment_create_is_false_positive_by_pattern(self) -> None:
        """Chaos experiments の Create はパターン B で false positive 判定"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Chaos/experiments",
            "resourceName": "exp-aks-pod-failure",
            "resourceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure",
            "beforeState": None,
            "afterState": {"type": "Microsoft.Chaos/experiments"},
            "propertyChanges": [],
        }
        self.assertTrue(is_create_false_positive(change))

    def test_non_matching_create_is_not_false_positive(self) -> None:
        """パターンにマッチしない Create は false positive ではない"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Network/virtualNetworks",
            "resourceName": "vnet-test",
            "resourceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-test",
            "beforeState": None,
            "afterState": {"type": "Microsoft.Network/virtualNetworks"},
            "propertyChanges": [],
        }
        self.assertFalse(is_create_false_positive(change))


class TestIsCreateFalsePositive(unittest.TestCase):
    """is_create_false_positive のテスト"""

    def test_create_with_null_before_and_after_is_false_positive(self) -> None:
        """before/after 両方 null の Create は false positive"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Chaos/experiments",
            "resourceName": "exp-aks-pod-failure",
            "resourceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Chaos/experiments/exp-aks-pod-failure",
            "beforeState": None,
            "afterState": None,
            "propertyChanges": [],
        }
        self.assertTrue(is_create_false_positive(change))

    def test_create_with_after_state_is_not_false_positive(self) -> None:
        """after がある Create でパターン外のリソースは正当な新規作成"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Network/virtualNetworks",
            "resourceName": "vnet-new",
            "resourceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-new",
            "beforeState": None,
            "afterState": {
                "type": "Microsoft.Network/virtualNetworks",
                "name": "vnet-new",
            },
            "propertyChanges": [],
        }
        self.assertFalse(is_create_false_positive(change))

    def test_modify_operation_is_not_false_positive(self) -> None:
        """Modify 操作は false positive 判定の対象外"""
        change = {
            "operation": "Modify",
            "resourceType": "Microsoft.ContainerService/managedClusters",
            "resourceName": "aks-test",
            "resourceId": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/aks-test",
            "beforeState": None,
            "afterState": None,
            "propertyChanges": [],
        }
        self.assertFalse(is_create_false_positive(change))

    def test_create_with_before_state_is_not_false_positive(self) -> None:
        """before がある Create は false positive ではない"""
        change = {
            "operation": "Create",
            "resourceType": "Microsoft.Authorization/roleAssignments",
            "resourceName": "role-1",
            "resourceId": "/subscriptions/sub/providers/Microsoft.Authorization/roleAssignments/role-1",
            "beforeState": {"type": "Microsoft.Authorization/roleAssignments"},
            "afterState": None,
            "propertyChanges": [],
        }
        self.assertFalse(is_create_false_positive(change))


class TestExtractResourceChangesWithFalsePositive(unittest.TestCase):
    """extract_resource_changes の false positive フラグテスト"""

    def test_create_with_null_state_gets_flagged(self) -> None:
        """before/after null の Create に likelyFalsePositive フラグが付く"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Create",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.Chaos/experiments/exp-aks-pod-failure"
                    ),
                }
            ]
        }
        result = extract_resource_changes(what_if_result)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["likelyFalsePositive"])

    def test_create_with_after_state_not_flagged(self) -> None:
        """after がありパターン外の Create には likelyFalsePositive フラグが付かない"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Create",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.Network/virtualNetworks/vnet-new"
                    ),
                    "after": {
                        "type": "Microsoft.Network/virtualNetworks",
                        "name": "vnet-new",
                    },
                }
            ]
        }
        result = extract_resource_changes(what_if_result)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["likelyFalsePositive"])

    def test_modify_not_flagged(self) -> None:
        """Modify 操作には likelyFalsePositive フラグが付かない"""
        what_if_result = {
            "changes": [
                {
                    "changeType": "Modify",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.ContainerService/managedClusters/aks-test"
                    ),
                    "delta": [],
                }
            ]
        }
        result = extract_resource_changes(what_if_result)
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["likelyFalsePositive"])


class TestFormatAzdStyleOutputFalsePositive(unittest.TestCase):
    """format_azd_style_output の false positive フィルタリングテスト"""

    def test_false_positive_create_hidden_with_summary(self) -> None:
        """false positive の Create は非表示でサマリー行が出る"""
        output_data = {
            "changes": [
                {
                    "operation": "Create",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.Chaos/experiments/exp-aks-pod-failure"
                    ),
                    "resourceType": "Microsoft.Chaos/experiments",
                    "resourceName": "exp-aks-pod-failure",
                    "propertyChanges": [],
                    "likelyFalsePositive": True,
                    "beforeState": None,
                    "afterState": None,
                },
                {
                    "operation": "Modify",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.ContainerService/managedClusters/aks-test"
                    ),
                    "resourceType": "Microsoft.ContainerService/managedClusters",
                    "resourceName": "aks-test",
                    "propertyChanges": [],
                    "likelyFalsePositive": False,
                    "beforeState": {},
                    "afterState": {},
                },
            ]
        }
        result = format_azd_style_output(output_data)
        self.assertNotIn("exp-aks-pod-failure", result)
        self.assertIn("aks-test", result)
        self.assertIn("1 件の Create を非表示", result)

    def test_no_summary_when_no_false_positives(self) -> None:
        """false positive がない場合はサマリー行なし"""
        output_data = {
            "changes": [
                {
                    "operation": "Create",
                    "resourceId": (
                        "/subscriptions/sub/resourceGroups/rg/providers/"
                        "Microsoft.Chaos/experiments/exp-aks-new"
                    ),
                    "resourceType": "Microsoft.Chaos/experiments",
                    "resourceName": "exp-aks-new",
                    "propertyChanges": [],
                    "likelyFalsePositive": False,
                    "beforeState": None,
                    "afterState": {"type": "Microsoft.Chaos/experiments"},
                },
            ]
        }
        result = format_azd_style_output(output_data)
        self.assertIn("exp-aks-new", result)
        self.assertNotIn("非表示", result)


class TestParseAzureYamlLayers(unittest.TestCase):
    """parse_azure_yaml_layers のテスト"""

    def _write_yaml(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            return f.name

    def test_repo_format(self) -> None:
        """本リポと同じ multi-layer 形式を解析できる"""
        path = self._write_yaml(
            "name: aks-chaos-lab\n"
            "infra:\n"
            "  layers:\n"
            "    - name: base\n"
            "      path: ./infra\n"
            "    - name: sli\n"
            "      path: ./infra/sli\n"
            "\n"
            "services:\n"
            "  api:\n"
            "    project: src\n"
        )
        layers = parse_azure_yaml_layers(path)
        self.assertEqual(
            layers,
            [
                {"name": "base", "path": "./infra"},
                {"name": "sli", "path": "./infra/sli"},
            ],
        )

    def test_no_layers_section(self) -> None:
        """layers セクションがなければ空リスト"""
        path = self._write_yaml("name: app\nservices:\n  api:\n    host: aks\n")
        self.assertEqual(parse_azure_yaml_layers(path), [])

    def test_missing_yaml(self) -> None:
        """yaml が無ければ空リスト"""
        self.assertEqual(parse_azure_yaml_layers("/no/such/file.yaml"), [])

    def test_quoted_values_stripped(self) -> None:
        """quote を剥がす"""
        path = self._write_yaml(
            "infra:\n  layers:\n    - name: \"base\"\n      path: './infra'\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )

    def test_inline_comments_stripped(self) -> None:
        """値の後ろの inline comment を除去する"""
        path = self._write_yaml(
            "infra:\n"
            "  layers:\n"
            "    - name: base  # primary layer\n"
            "      path: ./infra\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )

    def test_path_first_then_name(self) -> None:
        """- path: で項目が始まり次に name: の形式も解析できる"""
        path = self._write_yaml(
            "infra:\n  layers:\n    - path: ./infra/sli\n      name: sli\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "sli", "path": "./infra/sli"}]
        )

    def test_duplicate_name_keeps_first(self) -> None:
        """重複した name は最初のものを採用する"""
        path = self._write_yaml(
            "infra:\n"
            "  layers:\n"
            "    - name: base\n"
            "      path: ./infra\n"
            "    - name: base\n"
            "      path: ./infra/dup\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )

    def test_layer_without_path_excluded(self) -> None:
        """name のみで path がない項目は除外される"""
        path = self._write_yaml(
            "infra:\n"
            "  layers:\n"
            "    - name: base\n"
            "      path: ./infra\n"
            "    - name: incomplete\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )

    def test_top_level_keys_after_layers(self) -> None:
        """layers の後に来る他のトップレベルキーを誤って取り込まない"""
        path = self._write_yaml(
            "infra:\n"
            "  layers:\n"
            "    - name: base\n"
            "      path: ./infra\n"
            "workflows:\n"
            "  up:\n"
            "    steps:\n"
            "      - azd: provision base\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )

    def test_other_infra_keys_after_layers(self) -> None:
        """infra 配下の layers と同じ indent の別キーを誤って取り込まない"""
        path = self._write_yaml(
            "infra:\n"
            "  provider: bicep\n"
            "  layers:\n"
            "    - name: base\n"
            "      path: ./infra\n"
            "  someOther: value\n"
        )
        self.assertEqual(
            parse_azure_yaml_layers(path), [{"name": "base", "path": "./infra"}]
        )


class TestGetBicepParamNames(unittest.TestCase):
    """get_bicep_param_names のテスト"""

    def _write_bicep(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".bicep", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            return f.name

    def test_extracts_params(self) -> None:
        """param 宣言を抽出する"""
        path = self._write_bicep(
            "targetScope = 'subscription'\n"
            "\n"
            "param environment string\n"
            "param location string = 'japaneast'\n"
            "param appName string = 'aks-chaos-lab'\n"
            "var foo = 'bar'\n"
        )
        self.assertEqual(
            get_bicep_param_names(path), {"environment", "location", "appName"}
        )

    def test_skips_commented_param(self) -> None:
        """コメント行内の 'param' は拾わない"""
        path = self._write_bicep("// param disabled string\nparam environment string\n")
        self.assertEqual(get_bicep_param_names(path), {"environment"})

    def test_missing_template(self) -> None:
        """template が無い場合は空集合"""
        self.assertEqual(get_bicep_param_names("/no/such/file.bicep"), set())


class TestResolveParametersFilePlaceholders(unittest.TestCase):
    """resolve_parameters_file_placeholders のテスト"""

    def _write_params(self, data: dict) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(data, f)
            return f.name

    def test_resolves_env_var(self) -> None:
        """${VAR} を env から解決する"""
        path = self._write_params(
            {
                "parameters": {
                    "environment": {"value": "${AZURE_ENV_NAME}"},
                    "appName": {"value": "aks-chaos-lab"},
                }
            }
        )
        result = resolve_parameters_file_placeholders(path, {"AZURE_ENV_NAME": "eval"})
        self.assertEqual(result, {"environment": "eval"})

    def test_uses_default_when_env_missing(self) -> None:
        """env が無ければ ${VAR:default} の default を使う"""
        path = self._write_params(
            {"parameters": {"resourceGroupName": {"value": "${AZURE_RG:none}"}}}
        )
        result = resolve_parameters_file_placeholders(path, {})
        self.assertEqual(result, {"resourceGroupName": "none"})

    def test_skips_when_no_env_no_default(self) -> None:
        """env も default もなければ含めない (file の literal がそのまま渡る)"""
        path = self._write_params({"parameters": {"x": {"value": "${MISSING_VAR}"}}})
        self.assertEqual(resolve_parameters_file_placeholders(path, {}), {})

    def test_ignores_non_string_values(self) -> None:
        """bool / int / array / object はそのまま file 側に任せる"""
        path = self._write_params(
            {
                "parameters": {
                    "enabled": {"value": True},
                    "count": {"value": 42},
                    "items": {"value": ["a", "b"]},
                    "nested": {"value": {"k": "v"}},
                    "name": {"value": "${AZURE_ENV_NAME}"},
                }
            }
        )
        result = resolve_parameters_file_placeholders(path, {"AZURE_ENV_NAME": "eval"})
        self.assertEqual(result, {"name": "eval"})

    def test_ignores_partial_placeholder(self) -> None:
        """部分一致 (prefix-${VAR}) は対象外で literal のまま"""
        path = self._write_params(
            {"parameters": {"x": {"value": "prefix-${AZURE_ENV_NAME}-suffix"}}}
        )
        self.assertEqual(
            resolve_parameters_file_placeholders(path, {"AZURE_ENV_NAME": "eval"}),
            {},
        )

    def test_missing_file(self) -> None:
        """存在しないファイルは空 dict"""
        self.assertEqual(
            resolve_parameters_file_placeholders("/no/such/file.json", {}), {}
        )

    def test_default_with_empty_string(self) -> None:
        """${VAR:} (空 default) は空文字列を返す"""
        path = self._write_params({"parameters": {"x": {"value": "${VAR:}"}}})
        self.assertEqual(resolve_parameters_file_placeholders(path, {}), {"x": ""})


class TestRunWhatIfCommandConstruction(unittest.TestCase):
    """run_what_if のコマンド構築テスト (subprocess.run をモック)"""

    def test_parameters_file_before_inline(self) -> None:
        """--parameters @file が --parameters key=value より先に来る (= 後勝ち)"""
        import subprocess
        from unittest.mock import patch

        captured: dict[str, list[str]] = {}

        class _Result:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        with patch.object(subprocess, "run", _fake_run):
            run_what_if(
                template="t.bicep",
                location="japaneast",
                parameters={"environment": "eval"},
                parameters_file="/tmp/params.json",
            )

        cmd = captured["cmd"]
        # @file の位置 < key=value の位置 を確認
        file_idx = cmd.index("@/tmp/params.json")
        kv_idx = cmd.index("environment=eval")
        self.assertLess(file_idx, kv_idx)

    def test_no_parameters_file_when_unset(self) -> None:
        """parameters_file 未指定時は --parameters @ は付かない"""
        import subprocess
        from unittest.mock import patch

        captured: dict[str, list[str]] = {}

        class _Result:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        with patch.object(subprocess, "run", _fake_run):
            run_what_if(template="t.bicep", location="japaneast")

        cmd = captured["cmd"]
        for arg in cmd:
            self.assertFalse(
                isinstance(arg, str) and arg.startswith("@"),
                f"unexpected file arg: {arg}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
