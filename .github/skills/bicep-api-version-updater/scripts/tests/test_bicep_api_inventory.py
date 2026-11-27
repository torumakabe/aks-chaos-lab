#!/usr/bin/env python3
"""Unit tests for bicep_api_inventory.py (stdlib unittest, zero external deps)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import bicep_api_inventory as inv


class TestParseBicepText(unittest.TestCase):
    def test_extracts_standard_existing_conditional_loop_and_nested_declarations(
        self,
    ) -> None:
        text = """
// resource commentedOut 'Microsoft.Storage/storageAccounts@2023-01-01' = {}
/*
resource blockCommented 'Microsoft.Network/virtualNetworks@2024-01-01' = {}
*/
var multiline = '''
resource insideString 'Microsoft.KeyVault/vaults@2023-07-01' = {}
'''
param cfg resourceInput<'Microsoft.Network/virtualNetworks@2024-05-01'>
output out resourceOutput<'Microsoft.Network/virtualNetworks@2024-05-01'> = cfg

resource aks 'Microsoft.ContainerService/managedClusters@2026-07-01' existing = {
  name: 'aks-1'
}

resource vnet 'Microsoft.Network/virtualNetworks@2025-05-01' = if (true) {
  name: 'vnet-1'

  resource subnet 'subnets' = {
    name: 'snet-1'
  }
}

resource roles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in range(0, 2): {
  name: 'role-${i}'
}]
"""
        parsed = inv.parse_bicep_text(text, "infra/test.bicep")
        self.assertEqual(parsed.unresolved, [])
        self.assertEqual(len(parsed.excluded_type_refs), 2)
        self.assertEqual(
            [(d.name, d.resource_type, d.api_version) for d in parsed.declarations],
            [
                ("aks", "Microsoft.ContainerService/managedClusters", "2026-07-01"),
                ("vnet", "Microsoft.Network/virtualNetworks", "2025-05-01"),
                ("subnet", "Microsoft.Network/virtualNetworks/subnets", "2025-05-01"),
                ("roles", "Microsoft.Authorization/roleAssignments", "2022-04-01"),
            ],
        )
        by_name = {d.name: d for d in parsed.declarations}
        self.assertTrue(by_name["aks"].is_existing)
        self.assertTrue(by_name["vnet"].is_conditional)
        self.assertTrue(by_name["subnet"].is_nested)
        self.assertEqual(by_name["subnet"].parent_name, "vnet")
        self.assertTrue(by_name["roles"].is_loop)

    def test_records_unresolved_when_child_has_no_parent_or_missing_version(
        self,
    ) -> None:
        text = """
resource orphan 'subnets@2025-05-01' = {
  name: 'orphan'
}
resource noVer 'Microsoft.Network/virtualNetworks' = {
  name: 'no-ver'
}
"""
        parsed = inv.parse_bicep_text(text, "infra/bad.bicep")
        self.assertEqual(parsed.declarations, [])
        self.assertEqual(len(parsed.unresolved), 2)
        self.assertTrue(
            all(u.reason_code == "target-unresolved" for u in parsed.unresolved)
        )


class TestVersionExtractionAndClassification(unittest.TestCase):
    def test_extract_versions_from_learn_html_and_latest_ga(self) -> None:
        html = """
        <a href="/en-us/azure/templates/microsoft.network/2024-05-01/virtualnetworks">2024-05-01</a>
        <a href="/en-us/azure/templates/microsoft.network/2025-05-01/virtualnetworks">2025-05-01</a>
        <a href="/en-us/azure/templates/microsoft.network/2026-01-01-preview/virtualnetworks">preview</a>
        """
        versions = inv.extract_versions_from_learn_html(
            html, "Microsoft.Network/virtualNetworks"
        )
        self.assertEqual(versions, ["2024-05-01", "2025-05-01", "2026-01-01-preview"])
        self.assertEqual(inv.latest_ga_version(versions), "2025-05-01")

    def test_extract_versions_from_learn_html_handles_relative_links(self) -> None:
        top_level_html = """
        <a href="2024-05-01/virtualnetworks">2024-05-01</a>
        <a href="2025-05-01/virtualnetworks?pivots=deployment-language-bicep">2025-05-01</a>
        """
        self.assertEqual(
            inv.extract_versions_from_learn_html(
                top_level_html, "Microsoft.Network/virtualNetworks"
            ),
            ["2024-05-01", "2025-05-01"],
        )

        child_html = """
        <a href="../2025-07-01/redisenterprise/databases">2025-07-01</a>
        <a href="../2026-09-01/redisenterprise/databases">2026-09-01</a>
        <a href="../../2026-09-01/redisenterprise/databases/accesspolicyassignments">child</a>
        """
        self.assertEqual(
            inv.extract_versions_from_learn_html(
                child_html, "Microsoft.Cache/redisEnterprise/databases"
            ),
            ["2025-07-01", "2026-09-01"],
        )
        self.assertEqual(
            inv.extract_versions_from_learn_html(
                child_html,
                "Microsoft.Cache/redisEnterprise/databases/accessPolicyAssignments",
            ),
            ["2026-09-01"],
        )

    def test_resolve_spec_root_separates_services_within_same_provider(self) -> None:
        self.assertEqual(
            inv.resolve_spec_root("Microsoft.ContainerService/managedClusters"),
            "specification/containerservice/resource-manager/Microsoft.ContainerService/aks",
        )
        self.assertEqual(
            inv.resolve_spec_root("Microsoft.ContainerService/fleets/members"),
            "specification/containerservice/resource-manager/Microsoft.ContainerService/fleet",
        )
        self.assertEqual(
            inv.resolve_spec_root("Microsoft.Network/privateDnsZones"),
            "specification/privatedns/resource-manager/Microsoft.Network",
        )
        self.assertEqual(
            inv.resolve_spec_root("Microsoft.Network/virtualNetworks"),
            "specification/network/resource-manager/Microsoft.Network",
        )
        self.assertEqual(
            inv.resolve_spec_root("Microsoft.Insights/components"),
            "specification/applicationinsights/resource-manager/Microsoft.Insights/components",
        )

    def test_fetch_rest_api_specs_does_not_merge_ambiguous_subdirectories(self) -> None:
        entries = [
            {"type": "dir", "name": "serviceA", "path": "spec/serviceA"},
            {"type": "dir", "name": "serviceB", "path": "spec/serviceB"},
        ]
        with patch.object(inv, "_list_github_dir", return_value=(200, entries, None)):
            result = inv.fetch_rest_api_specs_versions(
                "specification/example/resource-manager/Microsoft.Example",
                timeout=5.0,
                token=None,
            )
        self.assertEqual(result["status"], "ambiguous-service-layout")
        self.assertEqual(result["versions"], [])
        self.assertIsNone(result["latest_ga"])

    def test_classify_comparison(self) -> None:
        self.assertEqual(
            inv.classify_comparison("2025-05-01", "2025-05-01", "2025-05-01"),
            "up-to-date",
        )
        self.assertEqual(
            inv.classify_comparison("2025-01-01", "2025-05-01", "2025-05-01"),
            "update-candidate",
        )
        self.assertEqual(
            inv.classify_comparison("2025-05-01-preview", "2025-05-01", "2025-05-01"),
            "preview-current",
        )
        self.assertEqual(
            inv.classify_comparison("2026-07-01", "2026-08-01", "2026-09-01"),
            "update-candidate-source-mismatch",
        )
        self.assertEqual(
            inv.classify_comparison("2025-05-01", None, None),
            "evidence-unavailable",
        )


class TestVerifyCoverage(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = {
            "declarations": [
                {"declaration_id": "infra/a.bicep:1:vnet"},
                {"declaration_id": "infra/a.bicep:5:aks"},
            ],
            "unresolved": [],
        }

    def test_complete_when_all_declarations_verified(self) -> None:
        assessments = {
            "declarations": [
                {
                    "declaration_id": "infra/a.bicep:1:vnet",
                    "status": "verified",
                    "has_issue": False,
                    "has_update": False,
                },
                {
                    "declaration_id": "infra/a.bicep:5:aks",
                    "status": "verified",
                    "has_issue": False,
                    "has_update": True,
                },
            ]
        }
        result = inv.verify_coverage(self.inventory, assessments)
        self.assertTrue(result["complete"])
        self.assertTrue(result["coverage_valid"])
        self.assertEqual(result["counts"]["updates"], 1)

    def test_rejects_missing_declaration_and_unjustified_not_processed(self) -> None:
        assessments = {
            "declarations": [
                {
                    "declaration_id": "infra/a.bicep:1:vnet",
                    "status": "unverified",
                    "reason_code": "not-processed",
                }
            ]
        }
        result = inv.verify_coverage(self.inventory, assessments)
        self.assertFalse(result["complete"])
        self.assertFalse(result["coverage_valid"])
        self.assertEqual(result["missing_declaration_ids"], ["infra/a.bicep:5:aks"])
        self.assertEqual(
            result["unjustified_not_processed_ids"], ["infra/a.bicep:1:vnet"]
        )

    def test_allows_not_processed_only_with_external_constraint_and_marks_incomplete(
        self,
    ) -> None:
        assessments = {
            "declarations": [
                {
                    "declaration_id": "infra/a.bicep:1:vnet",
                    "status": "verified",
                },
                {
                    "declaration_id": "infra/a.bicep:5:aks",
                    "status": "unverified",
                    "reason_code": "not-processed",
                    "external_constraint": "GitHub API rate limit exceeded",
                },
            ]
        }
        result = inv.verify_coverage(self.inventory, assessments)
        self.assertFalse(result["complete"])
        self.assertTrue(result["coverage_valid"])


class TestExtractAndCli(unittest.TestCase):
    def test_extract_missing_target_records_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = inv.extract_inventory(root, ["missing.bicep"])
            self.assertEqual(result["summary"]["files_scanned"], 0)
            self.assertEqual(result["summary"]["unresolved_count"], 1)

    def test_cli_extract_and_fetch_versions_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bicep = root / "main.bicep"
            bicep.write_text(
                "resource vnet 'Microsoft.Network/virtualNetworks@2025-05-01' = {}\n",
                encoding="utf-8",
            )
            inv_path = root / "inv.json"
            out_path = root / "versions.json"

            rc = inv.main(
                [
                    "extract",
                    "--repo-root",
                    str(root),
                    "--target",
                    "main.bicep",
                    "--output",
                    str(inv_path),
                ]
            )
            self.assertEqual(rc, 0)
            inv_data = json.loads(inv_path.read_text(encoding="utf-8"))
            self.assertEqual(inv_data["summary"]["declarations_count"], 1)

            fake_learn = {
                "source": "microsoft-learn",
                "url": "https://learn.microsoft.com/en-us/azure/templates/microsoft.network/virtualnetworks",
                "status": "ok",
                "http_status": 200,
                "error": None,
                "versions": ["2025-05-01"],
                "latest_ga": "2025-05-01",
            }
            with patch.object(inv, "fetch_learn_versions", return_value=fake_learn):
                rc2 = inv.main(
                    [
                        "fetch-versions",
                        "--inventory",
                        str(inv_path),
                        "--no-github",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc2, 0)
            ver_data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(ver_data["summary"]["up_to_date"], 1)


if __name__ == "__main__":
    unittest.main()
