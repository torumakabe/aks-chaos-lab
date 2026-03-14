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
from typing import Any

# テスト対象モジュールのパスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from what_if_analyzer import (
    DisplayConfigLoader,
    NoisePatternLoader,
    _extract_actual_provider_type,
    evaluate_property_change,
    extract_resource_changes,
    flatten_property_changes,
    format_azd_style_output,
    get_reference_info,
    is_create_false_positive,
    is_main_resource,
    is_readonly_property,
    contains_arm_reference,
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
        self.assertTrue(contains_arm_reference("[resourceId('Microsoft.Network/virtualNetworks', 'vnet')]"))

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
        self.assertEqual(names.get("Microsoft.ContainerService/managedClusters"), "AKS Managed Cluster")

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
            "afterState": {"type": "Microsoft.Network/virtualNetworks", "name": "vnet-new"},
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
