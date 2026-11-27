#!/usr/bin/env python3
"""Unit tests for freshness_inventory.py (stdlib unittest, zero external deps)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import freshness_inventory as fi


class TestMarkdownLinkExtraction(unittest.TestCase):
    def test_extracts_public_links_and_skips_code_and_non_public(self) -> None:
        md = """
# Doc

See [Learn](https://learn.microsoft.com/en-us/azure/aks/) and <https://github.com/Azure/AKS>.
Bare link: https://kubernetes.io/docs/home/.
Ref link: [ref]

`https://inline.example.org/ignored`

```bash
curl https://codeblock.example.org/ignored
```

Local: [local](http://localhost:8000/health)
Template: [tpl](https://learn.microsoft.com/en-us/azure/{service})

[ref]: https://learn.microsoft.com/azure/azure-functions/extension-bundles#bundle-versions
"""
        extracted = fi.extract_markdown_links(md, "docs/sample.md")
        urls = [item.url for item in extracted.links]
        self.assertEqual(
            urls,
            [
                "https://learn.microsoft.com/en-us/azure/aks/",
                "https://github.com/Azure/AKS",
                "https://kubernetes.io/docs/home/",
                "https://learn.microsoft.com/azure/azure-functions/extension-bundles#bundle-versions",
            ],
        )
        excluded_urls = [item.url for item in extracted.excluded]
        self.assertEqual(
            excluded_urls,
            [
                "http://localhost:8000/health",
                "https://learn.microsoft.com/en-us/azure/{service}",
            ],
        )


class TestHostJsonBundleExtraction(unittest.TestCase):
    def test_parses_valid_range(self) -> None:
        raw = json.dumps(
            {
                "version": "2.0",
                "extensionBundle": {
                    "id": "Microsoft.Azure.Functions.ExtensionBundle",
                    "version": "[4.*, 5.0.0)",
                },
            }
        )
        target = fi.extract_host_json_bundle(raw, "src/func/host.json")
        self.assertIsNone(target.parse_issue)
        self.assertEqual(target.major_versions, [4])

    def test_parses_multi_major_range(self) -> None:
        majors, err = fi.parse_bundle_version_range("[3.*, 5.0.0)")
        self.assertIsNone(err)
        self.assertEqual(majors, [3, 4])

    def test_records_issue_on_invalid_json_or_missing_version(self) -> None:
        bad_json = fi.extract_host_json_bundle("{not json", "src/func/host.json")
        self.assertIsNotNone(bad_json.parse_issue)

        missing_ver = fi.extract_host_json_bundle(
            json.dumps({"extensionBundle": {"id": "x"}}), "src/func/host.json"
        )
        self.assertIsNotNone(missing_ver.parse_issue)


class TestVerifyCoverage(unittest.TestCase):
    def setUp(self) -> None:
        self.inventory = {
            "links": [{"target_id": "link:README.md:10:1"}],
            "bundles": [
                {"target_id": "bundle:src/fn/host.json:extensionBundle.version"}
            ],
            "unresolved": [],
        }

    def test_complete_when_all_targets_verified(self) -> None:
        assessments = {
            "targets": [
                {
                    "target_id": "link:README.md:10:1",
                    "status": "verified",
                    "has_issue": True,
                },
                {
                    "target_id": "bundle:src/fn/host.json:extensionBundle.version",
                    "status": "verified",
                    "has_issue": False,
                },
            ]
        }
        result = fi.verify_coverage(self.inventory, assessments)
        self.assertTrue(result["complete"])
        self.assertTrue(result["coverage_valid"])
        self.assertEqual(result["counts"]["issues"], 1)

    def test_rejects_unjustified_not_processed_and_missing(self) -> None:
        assessments = {
            "targets": [
                {
                    "target_id": "link:README.md:10:1",
                    "status": "unverified",
                    "reason_code": "not-processed",
                }
            ]
        }
        result = fi.verify_coverage(self.inventory, assessments)
        self.assertFalse(result["complete"])
        self.assertFalse(result["coverage_valid"])
        self.assertEqual(
            result["missing_target_ids"],
            ["bundle:src/fn/host.json:extensionBundle.version"],
        )
        self.assertEqual(
            result["unjustified_not_processed_ids"], ["link:README.md:10:1"]
        )


class TestCliFlow(unittest.TestCase):
    def test_extract_and_check_links_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "See [AKS](https://learn.microsoft.com/en-us/azure/aks/)\n",
                encoding="utf-8",
            )
            inv_path = root / "inv.json"
            out_path = root / "links.json"

            rc = fi.main(
                [
                    "extract",
                    "--repo-root",
                    str(root),
                    "--target",
                    "README.md",
                    "--output",
                    str(inv_path),
                ]
            )
            self.assertEqual(rc, 0)
            inv_data = json.loads(inv_path.read_text(encoding="utf-8"))
            self.assertEqual(inv_data["summary"]["unique_urls_count"], 1)

            fake_probe = {
                "url": "https://learn.microsoft.com/en-us/azure/aks/",
                "classification": "ok",
                "http_status": 200,
                "final_url": "https://learn.microsoft.com/en-us/azure/aks/",
                "error": None,
            }
            with patch.object(fi, "_probe_url", return_value=fake_probe):
                rc2 = fi.main(
                    [
                        "check-links",
                        "--inventory",
                        str(inv_path),
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc2, 0)
            link_data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(link_data["summary"]["ok"], 1)


if __name__ == "__main__":
    unittest.main()
