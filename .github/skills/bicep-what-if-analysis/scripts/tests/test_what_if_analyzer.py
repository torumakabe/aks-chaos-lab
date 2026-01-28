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
    evaluate_property_change,
    flatten_property_changes,
    get_reference_info,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
