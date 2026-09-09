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
from unittest.mock import mock_open, patch

# テスト対象モジュールのパスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from what_if_analyzer import (
    DisplayConfigLoader,
    NoisePatternLoader,
    _extract_actual_provider_type,
    build_output,
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
    match_known_default,
    parse_azure_yaml_layers,
    resolve_parameters_file_placeholders,
    run_what_if,
)


class TestEvaluatePropertyChange(unittest.TestCase):
    """evaluate_property_change のテスト"""

    def test_storage_account_kind_change_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as bicep_dir:
            output = build_output(
                {
                    "changes": [
                        {
                            "changeType": "Modify",
                            "resourceId": (
                                "/subscriptions/sub/resourceGroups/rg/providers/"
                                "Microsoft.Storage/storageAccounts/st-test"
                            ),
                            "delta": [
                                {
                                    "path": "kind",
                                    "propertyChangeType": "Modify",
                                    "before": "Storage",
                                    "after": "StorageV2",
                                }
                            ],
                        }
                    ]
                },
                template="infra/main.bicep",
                location="japaneast",
                bicep_dir=bicep_dir,
            )
        change = output["changes"][0]["propertyChanges"][0]
        self.assertEqual(change["evaluation"]["status"], "pending")
        self.assertIsNone(change["evaluation"]["reason"])
        self.assertEqual(output["evaluationSummary"]["noise_confirmed"], 0)
        self.assertEqual(output["pendingEvaluations"]["count"], 1)
        text = format_azd_style_output(output)
        self.assertIn("kind", text)
        self.assertNotIn("readOnly", text)

    def test_dce_properties_change_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as bicep_dir:
            output = build_output(
                {
                    "changes": [
                        {
                            "changeType": "Modify",
                            "resourceId": (
                                "/subscriptions/sub/resourceGroups/rg/providers/"
                                "Microsoft.Insights/dataCollectionEndpoints/dce-test"
                            ),
                            "delta": [
                                {
                                    "path": "properties",
                                    "propertyChangeType": "Modify",
                                    "before": {
                                        "networkAcls": {
                                            "publicNetworkAccess": "Disabled"
                                        }
                                    },
                                    "after": {
                                        "networkAcls": {
                                            "publicNetworkAccess": "Enabled"
                                        }
                                    },
                                }
                            ],
                        }
                    ]
                },
                template="infra/main.bicep",
                location="japaneast",
                bicep_dir=bicep_dir,
            )
        change = output["changes"][0]["propertyChanges"][0]
        self.assertEqual(change["path"], "properties")
        self.assertEqual(change["evaluation"]["status"], "pending")
        self.assertIsNone(change["evaluation"]["reason"])
        self.assertEqual(output["evaluationSummary"]["noise_confirmed"], 0)
        self.assertEqual(output["pendingEvaluations"]["count"], 1)
        text = format_azd_style_output(output)
        self.assertIn("dce-test", text)
        self.assertIn("properties", text)
        self.assertNotIn("readOnly", text)

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

    def test_arm_reference_remains_pending(self) -> None:
        """ARM 参照式だけでは変更が消えることを証明できない"""
        result = evaluate_property_change(
            path="properties.subnetId",
            change_type="Modify",
            before="[reference(resourceId('Microsoft.Network/virtualNetworks', 'vnet'))]",
            after="/subscriptions/.../subnets/default",
        )
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["reason"], "armReference")
        self.assertIsNone(result["confidence"])

    def test_configurable_changes_remain_pending_in_output(self) -> None:
        cases = (
            (
                "Microsoft.ContainerService/fleets",
                "properties",
                {},
                {"hubProfile": {"dnsPrefix": "new-hub"}},
                None,
            ),
            (
                "Microsoft.ManagedIdentity/userAssignedIdentities",
                "properties",
                {"isolationScope": "None"},
                {"isolationScope": "Regional"},
                None,
            ),
            (
                "Microsoft.Monitor/accounts",
                "properties",
                {"publicNetworkAccess": "Disabled"},
                {"publicNetworkAccess": "Enabled"},
                None,
            ),
            (
                "Microsoft.OperationalInsights/workspaces/tables",
                "properties.schema",
                {"columns": []},
                {"columns": [{"name": "Message", "type": "string"}]},
                None,
            ),
            (
                "Microsoft.Monitor/accounts",
                "properties.publicNetworkAccess",
                "Disabled",
                "Enabled",
                None,
            ),
            (
                "Microsoft.OperationalInsights/workspaces/tables",
                "properties.schema.columns",
                [],
                [{"name": "Message", "type": "string"}],
                None,
            ),
            (
                "Microsoft.Web/sites/config",
                "properties.name",
                "old-setting",
                "new-setting",
                None,
            ),
            (
                "Microsoft.Network/privateEndpoints",
                "properties.subnet.id",
                "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'vnet', 'old')]",
                "[resourceId('Microsoft.Network/virtualNetworks/subnets', 'vnet', 'new')]",
                "armReference",
            ),
            (
                "Microsoft.Authorization/roleAssignments",
                "properties.principalId",
                "[reference('old-identity').principalId]",
                "[reference('new-identity').principalId]",
                "armReference",
            ),
            (
                "Microsoft.Network/virtualNetworks",
                "properties.customValue",
                "old",
                "[subscription().subscriptionId]",
                "armReference",
            ),
        )
        with tempfile.TemporaryDirectory() as bicep_dir:
            for resource_type, path, before, after, reason in cases:
                with self.subTest(resource_type=resource_type, path=path):
                    provider, *type_segments = resource_type.split("/")
                    resource_path = "/".join(
                        f"{segment}/test-resource" for segment in type_segments
                    )
                    output = build_output(
                        {
                            "changes": [
                                {
                                    "changeType": "Modify",
                                    "resourceId": (
                                        "/subscriptions/sub/resourceGroups/rg/providers/"
                                        f"{provider}/{resource_path}"
                                    ),
                                    "delta": [
                                        {
                                            "path": path,
                                            "propertyChangeType": "Modify",
                                            "before": before,
                                            "after": after,
                                        }
                                    ],
                                }
                            ]
                        },
                        template="infra/main.bicep",
                        location="japaneast",
                        bicep_dir=bicep_dir,
                    )
                    self.assertEqual(
                        output["changes"][0]["resourceType"], resource_type
                    )
                    change = output["changes"][0]["propertyChanges"][0]
                    self.assertEqual(change["evaluation"]["status"], "pending")
                    self.assertEqual(change["evaluation"]["reason"], reason)
                    self.assertEqual(output["evaluationSummary"]["noise_confirmed"], 0)
                    self.assertEqual(output["pendingEvaluations"]["count"], 1)
                    text = format_azd_style_output(output)
                    self.assertIn(path, text)
                    self.assertNotIn("readOnly", text)
                    self.assertNotIn("非表示", text)
                    if reason == "armReference":
                        self.assertIn("ARM 参照式", text)
                        self.assertIn("要確認", text)

    def test_noeffect_and_readonly_keep_precedence_over_arm_reference(self) -> None:
        for path, change_type, reason in (
            ("properties.subnetId", "NoEffect", "noEffect"),
            ("properties.provisioningState", "Modify", "readOnly"),
        ):
            with self.subTest(path=path):
                result = evaluate_property_change(
                    path, change_type, "[reference('old')]", "[reference('new')]"
                )
                self.assertEqual(result["status"], "noise_confirmed")
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["confidence"], "high")

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

    def test_kind_is_not_common_readonly(self) -> None:
        for resource_type in (
            "",
            "Microsoft.Storage/storageAccounts",
            "Microsoft.Web/sites",
            "Microsoft.Example/resources",
        ):
            with self.subTest(resource_type=resource_type):
                self.assertFalse(is_readonly_property("kind", resource_type))

    def test_provisioning_state_is_readonly(self) -> None:
        """provisioningState は readOnly"""
        self.assertTrue(is_readonly_property("properties.provisioningState"))

    def test_etag_is_readonly(self) -> None:
        """etag は readOnly"""
        self.assertTrue(is_readonly_property("etag"))

    def test_resource_metadata_does_not_match_settings_dictionary(self) -> None:
        for path in ("name", "type", "id", "etag", "systemData.createdAt"):
            with self.subTest(path=path):
                self.assertTrue(is_readonly_property(path))
                self.assertFalse(
                    is_readonly_property(
                        f"properties.{path}", "Microsoft.Web/sites/config"
                    )
                )
                self.assertFalse(is_readonly_property(f"properties.nested.{path}"))

    def test_resource_specific_readonly_is_preserved(self) -> None:
        for resource_type, path in (
            ("Microsoft.ContainerService/managedClusters", "properties.powerState"),
            ("Microsoft.Monitor/accounts", "properties.endpoints"),
        ):
            with self.subTest(resource_type=resource_type):
                self.assertTrue(is_readonly_property(path, resource_type))
                self.assertFalse(
                    is_readonly_property(path, "Microsoft.Example/resources")
                )

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

    def _loader_with_entries(
        self, scope: str, category: str, entries: list[object]
    ) -> NoisePatternLoader:
        loader = NoisePatternLoader()
        if scope == "common":
            loader._data = {"common": {category: entries}}
        else:
            loader._data = {
                "resource_types": {"Microsoft.Test/resources": {category: entries}}
            }
        return loader

    def test_rejects_non_string_pattern_fields(self) -> None:
        """不正な文字列型にはカテゴリ、リソース型、添字、フィールドを示す"""
        for scope, category, field in (
            ("common", "readonly_patterns", None),
            ("common", "auto_managed_patterns", "pattern"),
            ("common", "custom_patterns", "pattern"),
            ("resource_types.Microsoft.Test/resources", "readonly_patterns", None),
            (
                "resource_types.Microsoft.Test/resources",
                "auto_managed_patterns",
                "pattern",
            ),
            ("resource_types.Microsoft.Test/resources", "custom_patterns", "pattern"),
            ("resource_types.Microsoft.Test/resources", "known_defaults", "path"),
        ):
            for value in (None, 42, True, [], {}):
                with self.subTest(scope=scope, category=category, value=value):
                    valid = {field: "valid"} if field else "valid"
                    invalid = {field: value} if field else value
                    loader = self._loader_with_entries(
                        scope, category, [valid, invalid]
                    )
                    with self.assertRaises(ValueError) as error:
                        loader._validate_patterns()
                    location = f"{scope}.{category}[1]"
                    if field:
                        location += f".{field}"
                    self.assertIn(location, str(error.exception))
                    self.assertIn("文字列が必要", str(error.exception))

    def test_rejects_missing_fields_and_non_object_entries(self) -> None:
        """辞書でない項目や必須文字列の欠落を読み飛ばさない"""
        for scope, category, field in (
            ("common", "auto_managed_patterns", "pattern"),
            ("common", "custom_patterns", "pattern"),
            (
                "resource_types.Microsoft.Test/resources",
                "auto_managed_patterns",
                "pattern",
            ),
            ("resource_types.Microsoft.Test/resources", "custom_patterns", "pattern"),
            ("resource_types.Microsoft.Test/resources", "known_defaults", "path"),
        ):
            for item in (None, "pattern", [], {}):
                with self.subTest(scope=scope, category=category, item=item):
                    loader = self._loader_with_entries(scope, category, [item])
                    with self.assertRaises(ValueError) as error:
                        loader._validate_patterns()
                    self.assertIn(
                        f"{scope}.{category}[0].{field}", str(error.exception)
                    )

    def test_valid_patterns_keep_values_and_prefix_warning_scope(self) -> None:
        """型別 prefix と共通の辞書形式パターンだけを警告する"""
        for scope in ("common", "resource_types.Microsoft.Test/resources"):
            for category, field, normal, prefixed in (
                ("readonly_patterns", None, "^state$", "^properties\\.state$"),
                ("auto_managed_patterns", "pattern", "^state$", "^properties\\.state$"),
                ("custom_patterns", "pattern", "^state$", "^properties\\.state$"),
                ("known_defaults", "path", "state", "properties.state"),
            ):
                for value in (normal, prefixed, ""):
                    with self.subTest(scope=scope, category=category, value=value):
                        entry = (
                            {field: value, "value": True, "description": "説明"}
                            if field
                            else value
                        )
                        loader = self._loader_with_entries(scope, category, [entry])
                        should_warn = value == prefixed and not (
                            scope == "common"
                            and category in ("readonly_patterns", "known_defaults")
                        )
                        if should_warn:
                            with self.assertLogs(
                                "what_if_analyzer", level="WARNING"
                            ) as logs:
                                loader._validate_patterns()
                            self.assertEqual(len(logs.records), 3)
                            self.assertIn(
                                f"{scope}.{category}[0]", logs.records[1].getMessage()
                            )
                        else:
                            with self.assertNoLogs("what_if_analyzer", level="WARNING"):
                                loader._validate_patterns()
                        resource_type = (
                            "" if scope == "common" else "Microsoft.Test/resources"
                        )
                        if category == "readonly_patterns":
                            self.assertEqual(
                                loader.get_readonly_patterns(resource_type), [value]
                            )
                        elif category == "known_defaults":
                            self.assertEqual(
                                loader.get_known_defaults(resource_type),
                                [(value, True, "説明")],
                            )
                        else:
                            getter = (
                                loader.get_auto_managed_patterns
                                if category == "auto_managed_patterns"
                                else loader.get_custom_patterns
                            )
                            self.assertEqual(getter(resource_type), [(value, "説明")])

    def test_common_readonly_full_path_is_valid(self) -> None:
        """共通 readonly の properties. フルパスは警告せず照合できる"""
        loader = self._loader_with_entries(
            "common", "readonly_patterns", ["^properties\\.provisioningState$"]
        )
        with self.assertNoLogs("what_if_analyzer", level="WARNING"):
            patterns = loader.get_readonly_patterns()
            loader._validate_patterns()
        self.assertEqual(patterns, ["^properties\\.provisioningState$"])
        with patch("what_if_analyzer.get_pattern_loader", return_value=loader):
            self.assertTrue(is_readonly_property("properties.provisioningState"))

    def test_invalid_loaded_patterns_are_not_cached_or_replaced_with_fallback(
        self,
    ) -> None:
        """検証エラーは伝播し、再読み込みでも不正データを返さない"""
        data = {"common": {"custom_patterns": [{"pattern": 42}]}}
        loader = NoisePatternLoader()
        with patch("builtins.open", mock_open(read_data=json.dumps(data))) as opened:
            for _ in range(2):
                with self.assertRaisesRegex(
                    ValueError, r"common\.custom_patterns\[0\]\.pattern"
                ):
                    loader.get_custom_patterns()
                self.assertIsNone(loader._data)
            self.assertEqual(opened.call_count, 2)

    def test_invalid_json_returns_empty(self) -> None:
        """JSON 構文エラー時の既存 fallback を維持する"""
        loader = NoisePatternLoader()
        with (
            patch("builtins.open", mock_open(read_data="{")),
            self.assertLogs("what_if_analyzer", level="WARNING"),
        ):
            self.assertEqual(loader.get_readonly_patterns(), [])

    def test_load_default_patterns(self) -> None:
        """デフォルトパターンを読み込める"""
        loader = NoisePatternLoader()
        readonly = loader.get_readonly_patterns()
        self.assertIn("^properties\\.provisioningState$", readonly)

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


class TestMatchKnownDefault(unittest.TestCase):
    def test_only_exact_path_and_value_match(self) -> None:
        defaults = [
            ("enableRBAC", True, "RBAC default"),
            ("networkProfile.ipFamilies", ["IPv4"], "IPv4 default"),
        ]
        for path, value, expected in (
            ("enableRBAC", True, "RBAC default"),
            ("enableRBAC", False, None),
            ("enableRBAC", None, None),
            ("enableRBAC", 1, None),
            ("otherenableRBAC", True, None),
            ("nested.enableRBAC", True, None),
            ("networkProfile.ipFamilies", ["IPv4"], "IPv4 default"),
            ("ipFamilies", ["IPv4"], None),
            ("other.networkProfile.ipFamilies", ["IPv4"], None),
        ):
            with self.subTest(path=path, value=value):
                self.assertEqual(match_known_default(path, value, defaults), expected)


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

    def test_aks_nginx_mode_returns_warning(self) -> None:
        """自動管理候補は適用条件の確認を促す"""
        result = get_reference_info(
            path="properties.ingressProfile.webAppRouting.nginx.mode",
            before="Enabled",
            after=None,
            bicep_definition={"status": "notDefined"},
            resource_type="Microsoft.ContainerService/managedClusters",
        )
        self.assertIn("⚠️", result)
        self.assertIn("要確認", result)

    def test_extension_generated_properties_return_warning(self) -> None:
        """生成プロパティの候補もパス一致だけでは断定しない"""
        resource_type = (
            "Microsoft.ContainerService/managedClusters/providers/extensions"
        )
        for path in ("aksAssignedIdentity", "extensionState", "managementDetails"):
            with self.subTest(path=path):
                result = get_reference_info(
                    path=f"properties.{path}",
                    before={"value": "generated"},
                    after=None,
                    bicep_definition={"status": "notDefined"},
                    resource_type=resource_type,
                )
                self.assertIn("⚠️", result)
                self.assertIn("要確認", result)

    def test_auto_managed_candidates_do_not_claim_equivalence(self) -> None:
        for resource_type, path, before, after in (
            ("Microsoft.Insights/scheduledQueryRules", "windowSize", "PT60M", "PT30M"),
            (
                "Microsoft.Insights/diagnosticSettings",
                "logs.0",
                {"enabled": False},
                {"enabled": True},
            ),
            (
                "Microsoft.Chaos/experiments",
                "steps.0.branches.0.actions.0.parameters.0.value",
                '{"mode":"one"}',
                '{"mode":"all"}',
            ),
            (
                "Microsoft.ContainerService/managedClusters",
                "agentPoolProfiles.0.count",
                2,
                4,
            ),
            (
                "Microsoft.Storage/storageAccounts/blobServices",
                "properties",
                {},
                {"deleteRetentionPolicy": {"enabled": True, "days": 7}},
            ),
        ):
            for bicep_status in ("defined", "notDefined", "unknown"):
                with self.subTest(
                    resource_type=resource_type, bicep_status=bicep_status
                ):
                    full_path = path if path == "properties" else f"properties.{path}"
                    result = get_reference_info(
                        full_path,
                        before,
                        after,
                        {"status": bicep_status},
                        resource_type,
                    )
                    self.assertIn("⚠️", result)
                    self.assertIn("適用条件と変更内容は要確認", result)
                    self.assertNotIn("📘", result)
                    self.assertNotIn("値は等価", result)
                    self.assertNotIn("ノイズ", result)
                    evaluation = evaluate_property_change(
                        full_path, "Modify", before, after, resource_type
                    )
                    self.assertEqual(evaluation["status"], "pending")

    def test_known_default_identifies_matching_side(self) -> None:
        for before, after, before_matches, after_matches in (
            (True, False, True, False),
            (False, True, False, True),
            (True, True, True, True),
            (True, None, True, False),
            (None, True, False, True),
            (None, None, False, False),
            (False, False, False, False),
        ):
            with self.subTest(before=before, after=after):
                result = get_reference_info(
                    "properties.enableRBAC",
                    before,
                    after,
                    {"status": "notDefined"},
                    "Microsoft.ContainerService/managedClusters",
                )
                self.assertEqual("変更前が既定値" in result, before_matches)
                self.assertEqual("変更後が既定値" in result, after_matches)
                self.assertEqual("📘" in result, before_matches or after_matches)
                evaluation = evaluate_property_change(
                    "properties.enableRBAC",
                    "Modify",
                    before,
                    after,
                    "Microsoft.ContainerService/managedClusters",
                )
                self.assertEqual(evaluation["status"], "pending")

    def test_extension_auto_upgrade_mode_returns_warning(self) -> None:
        """AKS 拡張機能の autoUpgradeMode は要確認として扱う"""
        resource_type = (
            "Microsoft.ContainerService/managedClusters/providers/extensions"
        )
        for before, after in (("compatible", None), ("compatible", "patch")):
            with self.subTest(before=before, after=after):
                result = get_reference_info(
                    path="properties.autoUpgradeMode",
                    before=before,
                    after=after,
                    bicep_definition={"status": "notDefined"},
                    resource_type=resource_type,
                )
                self.assertIn("⚠️", result)

    def test_extension_scope_returns_warning(self) -> None:
        """AKS 拡張機能の scope は要確認として扱う"""
        result = get_reference_info(
            path="properties.scope",
            before={"cluster": {"releaseNamespace": "gadget"}},
            after=None,
            bicep_definition={"status": "notDefined"},
            resource_type=(
                "Microsoft.ContainerService/managedClusters/providers/extensions"
            ),
        )
        self.assertIn("⚠️", result)

    def test_sli_window_size_returns_warning(self) -> None:
        """SLI の windowSizeMinutes は要確認として扱う"""
        result = get_reference_info(
            path=(
                "properties.sliProperties.goodSignals.signalSources.0."
                "temporalAggregation.windowSizeMinutes"
            ),
            before=None,
            after=5,
            bicep_definition={"status": "defined"},
            resource_type="Microsoft.Management/serviceGroups/providers/slis",
        )
        self.assertIn("⚠️", result)


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
    """誤検知候補の Create も表示して注意を促すテスト"""

    def test_possible_false_positive_create_shown_with_warning(self) -> None:
        """誤検知候補もリソース名と要確認注記を表示する"""
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
        self.assertIn("Create", result)
        self.assertIn("exp-aks-pod-failure", result)
        self.assertIn("aks-test", result)
        self.assertIn("誤検知の可能性", result)
        self.assertIn("要確認", result)
        self.assertNotIn("非表示", result)

    def test_create_visible_after_classification(self) -> None:
        for resource_type, name, after, expected_flag in (
            (
                "Microsoft.Chaos/experiments",
                "exp-aks-new",
                {"type": "Microsoft.Chaos/experiments", "name": "exp-aks-new"},
                True,
            ),
            ("Microsoft.Chaos/experiments", "exp-aks-unknown", None, True),
            ("Microsoft.Network/virtualNetworks", "vnet-unknown", None, True),
            (
                "Microsoft.Network/virtualNetworks",
                "vnet-new",
                {"type": "Microsoft.Network/virtualNetworks", "name": "vnet-new"},
                False,
            ),
            ("Microsoft.Authorization/roleAssignments", "role-new", None, True),
        ):
            with self.subTest(resource_type=resource_type, name=name):
                output_data = build_output(
                    {
                        "changes": [
                            {
                                "changeType": "Create",
                                "resourceId": (
                                    "/subscriptions/sub/resourceGroups/rg/providers/"
                                    f"{resource_type}/{name}"
                                ),
                                "before": None,
                                "after": after,
                            }
                        ]
                    },
                    template="infra/main.bicep",
                    location="japaneast",
                )
                self.assertEqual(output_data["summary"]["create"], 1)
                self.assertEqual(
                    output_data["createFalsePositives"], int(expected_flag)
                )
                self.assertEqual(
                    output_data["changes"][0]["likelyFalsePositive"], expected_flag
                )
                result = format_azd_style_output(output_data)
                self.assertIn("Create", result)
                self.assertIn(name, result)
                self.assertEqual("要確認" in result, expected_flag)
                self.assertNotIn("非表示", result)

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
        self.assertNotIn("要確認", result)
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

    def test_azd_default_preserves_explicit_localdns_mode(self) -> None:
        path = self._write_params(
            {
                "parameters": {
                    "localDnsMode": {"value": "${AZURE_AKS_LOCAL_DNS_MODE=Required}"}
                }
            }
        )
        self.addCleanup(Path(path).unlink)
        for env_values, expected in (
            ({}, "Required"),
            ({"AZURE_AKS_LOCAL_DNS_MODE": ""}, "Required"),
            ({"AZURE_AKS_LOCAL_DNS_MODE": "Required"}, "Required"),
            ({"AZURE_AKS_LOCAL_DNS_MODE": "Disabled"}, "Disabled"),
        ):
            with self.subTest(env_values=env_values):
                self.assertEqual(
                    resolve_parameters_file_placeholders(path, env_values),
                    {"localDnsMode": expected},
                )

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
