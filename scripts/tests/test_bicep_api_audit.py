import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bicep_api_audit import (  # noqa: E402
    AuditError,
    Schemas,
    assessment_template,
    block_pair,
    declaration_usage,
    evidence_bytes,
    executable,
    inventory,
    new_state,
    next_pair,
    prepare_issue,
    published_file,
    record_pair,
    render,
    usage_units,
    verify_issue,
)

SHA = "a" * 40
BASE = f"https://raw.githubusercontent.com/Azure/azure-rest-api-specs/{SHA}/test/"
CURRENT = BASE + "stable/2025-01-01/api.json"
GA = BASE + "stable/2026-01-01/api.json"
CHANGE = (
    "https://learn.microsoft.com/en-us/azure/templates/microsoft.test/change-log/items"
)
EVIDENCE_URL = f"https://github.com/example/lab/blob/{'b' * 40}/audit/evidence.json"
REPORT_URL = f"https://github.com/example/lab/blob/{'b' * 40}/audit/report.md"


def published_payload(state, directory, title="fixture", allow_blocked=False):
    prepare_issue(state, title, directory, allow_blocked=allow_blocked)
    with patch(
        "bicep_api_audit.subprocess.check_output",
        side_effect=[
            (Path(directory) / "evidence.json").read_bytes(),
            (Path(directory) / "report.md").read_bytes(),
        ],
    ):
        return prepare_issue(
            state,
            title,
            directory,
            evidence_url=EVIDENCE_URL,
            report_url=REPORT_URL,
            allow_blocked=allow_blocked,
        )


class FixtureFetcher:
    def __init__(self, documents=None):
        self.documents = documents or {}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        document = self.documents.get(url, {})
        return {"status": 200, "body": json.dumps(document), "final_url": url}


def fixture():
    rows = [
        {
            "file": "fixture.bicep",
            "line": line,
            "name": name,
            "type": "Microsoft.Test/items",
            "version": "2025-01-01",
            "existing": False,
            "body": "{\nmode: 'Enabled'\n}",
        }
        for line, name in ((1, "one"), (2, "two"))
    ]
    state = new_state(
        {
            "files": ["fixture.bicep"],
            "declarations": rows,
            "sources": {"fixture.bicep": ""},
        },
        SHA,
        SHA,
    )
    evidence = {
        "current": {
            "version": "2025-01-01",
            "analysis": "Exact current schema checked",
            "urls": [CURRENT],
        },
        "ga": {
            "version": "2026-01-01",
            "analysis": "Latest GA sources compared",
            "urls": [GA],
        },
        "breaking": {"analysis": "No used property removed", "urls": [CHANGE]},
    }
    results = [
        {
            **r,
            "status": "verified",
            "reason": "Properties and values checked",
            "migration": "GA compatible",
            "findings": [],
            "usage": [
                {
                    "source_id": "body:6",
                    "path": "/mode",
                    "expression": "mode: 'Enabled'",
                    "analysis": "Supported enum in current and candidate",
                }
            ],
        }
        for r in rows
    ]
    return state, evidence, results


class StateTests(unittest.TestCase):
    def test_pending_pair_cannot_publish(self):
        state, _, _ = fixture()
        with self.assertRaisesRegex(AuditError, "Pending assessments"):
            render(state)

    def test_generic_unverified_fallback_cannot_advance_pair(self):
        state, evidence, results = fixture()
        results[1]["status"] = "unverified"
        with self.assertRaisesRegex(AuditError, "Unfinished"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
        self.assertEqual(next_pair(state)[0], "Microsoft.Test/items@2025-01-01")

    def test_missing_or_duplicate_declaration_cannot_hide_behind_total(self):
        state, evidence, results = fixture()
        results[1] = results[0]
        with self.assertRaisesRegex(AuditError, "declaration results"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_record_requires_all_evidence_classes(self):
        state, evidence, results = fixture()
        del evidence["breaking"]
        with self.assertRaisesRegex(AuditError, "Incomplete evidence"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_wrong_current_version_cannot_substitute_for_preview(self):
        state, evidence, results = fixture()
        evidence["current"]["version"] = "2026-01-01"
        with self.assertRaisesRegex(AuditError, "Wrong current version"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_mutable_rest_evidence_is_rejected(self):
        state, evidence, results = fixture()
        evidence["ga"]["urls"] = [GA.replace(SHA, "main")]
        with self.assertRaisesRegex(AuditError, "not pinned"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_blocked_pair_cannot_be_turned_into_completion(self):
        state, _, _ = fixture()
        block_pair(
            state,
            next_pair(state)[0],
            [
                {
                    "url": CURRENT,
                    "finding": "Exact schema absent from public REST directory",
                },
                {"url": CHANGE, "finding": "Exact version absent from public selector"},
            ],
            "Cannot establish exact current schema",
        )
        self.assertIsNone(next_pair(state))
        with self.assertRaisesRegex(AuditError, "Missing public evidence"):
            render(state)
        incomplete = render(state, allow_blocked=True)
        self.assertIn("確認未完了", incomplete)
        self.assertIn("verified=0, excluded=0, unverified=2", incomplete)
        self.assertNotIn("確認完了。", incomplete)

    def test_every_usage_requires_expression_and_check(self):
        state, evidence, results = fixture()
        del results[1]["usage"][0]["expression"]
        with self.assertRaisesRegex(AuditError, "Incomplete property"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_complete_results_serialize_without_manual_aggregation(self):
        state, evidence, results = fixture()
        record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            payload = published_payload(state, directory, "fixture only")
            saved = json.loads((Path(directory) / "issue-payload.json").read_text())
            self.assertEqual(payload, saved)
            self.assertIn("verified=2, excluded=0, unverified=0", payload["body"])
            self.assertEqual(len(verify_issue(payload, saved)), 64)
            encoded = (Path(directory) / "evidence.json").read_bytes()
            restored = json.loads(encoded)
            self.assertEqual(restored["state"], state)
            self.assertEqual(restored["state"]["pairs"], state["pairs"])
            self.assertEqual(encoded, evidence_bytes(state))
            self.assertIn(hashlib.sha256(encoded).hexdigest(), payload["body"])
            self.assertIn(EVIDENCE_URL, payload["body"])
            self.assertNotIn("```text", payload["body"])
            report = (Path(directory) / "report.md").read_text()
            self.assertEqual(report.count("usage=1"), 2)

    def test_used_property_cannot_be_omitted_while_status_claims_verified(self):
        state, evidence, results = fixture()
        row = state["declarations"][1]
        row["body"] = "{\nmode: 'Enabled'\nother: 'value'\n}"
        state["usage_targets"][json.dumps((row["file"], row["line"], row["name"]))] = (
            declaration_usage(row, "")
        )
        with self.assertRaisesRegex(AuditError, "source property/value"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_expression_cannot_change_while_source_id_stays_the_same(self):
        state, evidence, results = fixture()
        results[1]["usage"][0]["expression"] = "mode: 'Disabled'"
        with self.assertRaisesRegex(AuditError, "Changed source expression"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_wrong_property_cannot_claim_another_source_unit(self):
        state, evidence, results = fixture()
        results[1]["usage"][0]["path"] = "properties.unrelated"
        with self.assertRaisesRegex(AuditError, "does not match"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_suffix_and_same_leaf_at_different_depth_are_rejected(self):
        for wrong in ("/loggingmode", "/properties/mode"):
            state, evidence, results = fixture()
            results[1]["usage"][0]["path"] = wrong
            with self.assertRaisesRegex(AuditError, "does not match"):
                record_pair(
                    state, next_pair(state)[0], results, evidence, FixtureFetcher()
                )

    def test_actual_posted_payload_must_be_identical(self):
        state, evidence, results = fixture()
        record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            payload = published_payload(state, directory)
            actual = {**payload, "body": payload["body"] + "\nchanged"}
            with self.assertRaisesRegex(AuditError, "differs"):
                verify_issue(payload, actual)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            "definitions": {
                "Resource": {
                    "title": "Concrete",
                    "allOf": [{"$ref": "../../common/base.json#/definitions/Resource"}],
                    "properties": {"properties": {"$ref": "#/definitions/Properties"}},
                },
                "Properties": {
                    "type": "object",
                    "properties": {
                        "mode": {"$ref": "#/definitions/Mode"},
                        "members": {
                            "type": "array",
                            "items": {
                                "$ref": "../../common/base.json#/definitions/Resource"
                            },
                        },
                        "metadata": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "count": {"type": "integer", "minimum": 1},
                    },
                },
                "Mode": {"type": "string", "enum": ["Enabled", "Disabled"]},
            }
        }
        self.common_url = BASE + "common/base.json"
        self.fetcher = FixtureFetcher(
            {
                CURRENT: self.document,
                self.common_url: {
                    "definitions": {
                        "Resource": {
                            "title": "Base",
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string", "minLength": 2}},
                        }
                    }
                },
            }
        )
        self.schemas = Schemas(self.fetcher)
        self.root = {"$ref": "#/definitions/Resource"}

    def test_external_allof_origin_and_required_are_preserved(self):
        expanded = self.schemas.expand(self.root, CURRENT)
        self.assertIn("name", expanded["required"])
        self.schemas.literal(self.root, CURRENT, "name", "valid")
        self.assertEqual(self.fetcher.calls.count(self.common_url), 1)

    def test_nested_enum_is_checked(self):
        self.schemas.literal(self.root, CURRENT, "properties.mode", "Enabled")
        with self.assertRaisesRegex(AuditError, "Unsupported enum"):
            self.schemas.literal(self.root, CURRENT, "properties.mode", "Typo")

    def test_items_relative_reference_is_resolved(self):
        self.schemas.literal(self.root, CURRENT, "properties.members.[].name", "member")

    def test_additional_properties_are_resolved(self):
        self.schemas.literal(self.root, CURRENT, "properties.metadata.key", "value")
        with self.assertRaisesRegex(AuditError, "Wrong literal type"):
            self.schemas.literal(self.root, CURRENT, "properties.metadata.key", 3)

    def test_missing_property_is_not_silently_supported(self):
        with self.assertRaisesRegex(AuditError, "explicit additionalProperties review"):
            self.schemas.property(self.root, CURRENT, "properties.unspecified")

    def test_boolean_is_not_accepted_as_integer(self):
        with self.assertRaisesRegex(AuditError, "Wrong literal type"):
            self.schemas.literal(self.root, CURRENT, "properties.count", True)

    def test_numeric_and_string_constraints_are_checked(self):
        with self.assertRaisesRegex(AuditError, "Invalid minimum"):
            self.schemas.literal(self.root, CURRENT, "properties.count", 0)
        with self.assertRaisesRegex(AuditError, "Invalid string length"):
            self.schemas.literal(self.root, CURRENT, "name", "x")

    def test_cycles_require_explicit_review_instead_of_truncated_success(self):
        self.document["definitions"]["Mode"] = {"$ref": "#/definitions/Mode"}
        with self.assertRaisesRegex(AuditError, "Recursive schema"):
            self.schemas.property(self.root, CURRENT, "properties.mode")

    def test_network_errors_do_not_create_empty_schemas(self):
        class Denied(FixtureFetcher):
            def get(self, url):
                return {"status": 404, "body": "", "final_url": url}

        with self.assertRaisesRegex(AuditError, "Schema HTTP 404"):
            Schemas(Denied()).document(CURRENT)


class FileEvidenceTests(unittest.TestCase):
    def assessed(self):
        state, evidence, results = fixture()
        record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
        return state

    def test_assessment_template_copies_source_without_claiming_checks(self):
        state, _, _ = fixture()
        template = assessment_template(state)
        self.assertEqual(template["pair"], next_pair(state)[0])
        for result, row in zip(template["results"], state["declarations"], strict=True):
            self.assertEqual(result["body"], row["body"])
            self.assertEqual(result["status"], "pending")
            for unit in result["usage"]:
                self.assertEqual(unit["analysis"], "")
                self.assertEqual(unit["path"], "/mode")
        self.assertEqual(next_pair(state)[0], template["pair"])
        self.assertIsNone(assessment_template(self.assessed()))

    def test_prepare_without_publication_only_writes_local_files(self):
        state = self.assessed()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            hashes = prepare_issue(state, "fixture", directory)
            self.assertEqual(len(hashes["evidence_sha256"]), 64)
            self.assertTrue((Path(directory) / "report.md").is_file())
            self.assertFalse((Path(directory) / "issue-payload.json").exists())

    def test_every_hash_and_source_unit_round_trips_exactly(self):
        state = self.assessed()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            payload = published_payload(state, directory)
            data = (Path(directory) / "evidence.json").read_bytes()
            restored = json.loads(data)
            self.assertEqual(restored["state"], state)
            self.assertEqual(
                restored["results"], next(iter(state["pairs"].values()))["results"]
            )
            self.assertEqual(
                restored["state"]["source_sha256"]["fixture.bicep"],
                hashlib.sha256(b"").hexdigest(),
            )
            for source in next(iter(restored["state"]["pairs"].values()))[
                "evidence"
            ].values():
                for url in source["urls"]:
                    self.assertEqual(
                        source["sha256"][url], hashlib.sha256(b"{}").hexdigest()
                    )
            self.assertIn(hashlib.sha256(data).hexdigest(), payload["body"])

    def test_real_population_size_is_not_limited_by_issue_body(self):
        import subprocess

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            subprocess.run([executable("git"), "init", "-q", directory], check=True)
            source = "@allowed(['Enabled', 'Disabled'])\nparam mode string\n"
            source += "var settings = { mode: mode }\n"
            source += "\n".join(
                f"resource item{number} 'Microsoft.Test/items@2025-01-01' = {{\n"
                f"  name: 'item{number}'\n"
                "  properties: {\n"
                "    settings: settings\n"
                "    members: [{ name: 'one' }]\n"
                + "".join(f"    option{field}: 'value{field}'\n" for field in range(12))
                + "  }\n}"
                for number in range(179)
            )
            (root / "fixture.bicep").write_text(source, encoding="utf-8")
            state = new_state(inventory(directory), SHA, SHA)
            self.assertEqual(len(state["declarations"]), 179)
            _, evidence, _ = fixture()
            results = []
            for row in state["declarations"]:
                units = state["usage_targets"][
                    json.dumps((row["file"], row["line"], row["name"]))
                ]
                self.assertTrue(
                    any(u["source_id"].startswith("symbol:mode:") for u in units)
                )
                self.assertTrue(
                    any(u["path"] == "/properties/members/[]/name" for u in units)
                )
                results.append(
                    {
                        **row,
                        "status": "verified",
                        "reason": "Synthetic fixture only",
                        "migration": "Synthetic candidate",
                        "findings": [],
                        "usage": [
                            {**unit, "analysis": "Synthetic schema comparison. " * 12}
                            for unit in units
                        ],
                    }
                )
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
            output = root / "audit"
            payload = published_payload(state, output)
            data = (output / "evidence.json").read_bytes()
            self.assertGreater(len(data), 1_077_590)
            self.assertLess(len(payload["body"].encode("utf-8")), 6000)
            restored = json.loads(data)
            self.assertEqual(restored["summary"]["declarations"], 179)
            self.assertEqual(restored["state"], state)
            self.assertEqual(restored["results"], results)
            self.assertEqual(data, evidence_bytes(state))

    def test_semantically_equal_but_different_published_bytes_are_rejected(self):
        state = self.assessed()
        changed = json.dumps(
            json.loads(evidence_bytes(state)), ensure_ascii=False
        ).encode()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with (
                patch(
                    "bicep_api_audit.subprocess.check_output",
                    side_effect=[
                        changed,
                        render(state).encode(),
                    ],
                ),
                self.assertRaisesRegex(AuditError, "evidence differs"),
            ):
                prepare_issue(
                    state,
                    "fixture",
                    directory,
                    evidence_url=EVIDENCE_URL,
                    report_url=REPORT_URL,
                )
            self.assertFalse((Path(directory) / "issue-payload.json").exists())

    def test_published_report_mismatch_is_rejected(self):
        state = self.assessed()
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as directory,
            patch(
                "bicep_api_audit.subprocess.check_output",
                side_effect=[
                    evidence_bytes(state),
                    b"changed",
                ],
            ),
            self.assertRaisesRegex(AuditError, "report differs"),
        ):
            prepare_issue(
                state,
                "fixture",
                directory,
                evidence_url=EVIDENCE_URL,
                report_url=REPORT_URL,
            )

    def test_publication_requires_immutable_urls_and_same_commit(self):
        for url in (
            EVIDENCE_URL.replace("b" * 40, "main"),
            EVIDENCE_URL.replace("https:", "http:"),
            EVIDENCE_URL + "?ref=main",
            EVIDENCE_URL.replace("github.com", "github.com.evil.example"),
        ):
            with self.assertRaisesRegex(AuditError, "immutable"):
                published_file(url)
        state = self.assessed()
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as directory,
            patch(
                "bicep_api_audit.subprocess.check_output",
                side_effect=[
                    evidence_bytes(state),
                    render(state).encode(),
                ],
            ),
            self.assertRaisesRegex(AuditError, "share repository and commit"),
        ):
            prepare_issue(
                state,
                "fixture",
                directory,
                evidence_url=EVIDENCE_URL,
                report_url=REPORT_URL.replace("b" * 40, "c" * 40),
            )

    def test_failed_publication_removes_stale_payload(self):
        state = self.assessed()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            published_payload(state, directory)
            with (
                patch(
                    "bicep_api_audit.subprocess.check_output",
                    side_effect=OSError("offline"),
                ),
                self.assertRaises(OSError),
            ):
                prepare_issue(
                    state,
                    "fixture",
                    directory,
                    evidence_url=EVIDENCE_URL,
                    report_url=REPORT_URL,
                )
            self.assertFalse((Path(directory) / "issue-payload.json").exists())
            self.assertFalse((Path(directory) / "issue-body-sha256.json").exists())

    def test_blocked_evidence_remains_incomplete_in_json_and_issue(self):
        state, _, _ = fixture()
        block_pair(
            state,
            next_pair(state)[0],
            [
                {"url": CURRENT, "finding": "Exact schema absent"},
                {"url": CHANGE, "finding": "Version absent"},
            ],
            "No exact public source",
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            payload = published_payload(state, directory, allow_blocked=True)
            document = json.loads((Path(directory) / "evidence.json").read_bytes())
            self.assertFalse(document["summary"]["complete"])
            self.assertEqual(document["summary"]["unverified"], 2)
            self.assertTrue(
                all(r["status"] == "unverified" for r in document["results"])
            )
            self.assertIn("確認未完了", payload["body"])
            self.assertNotIn("確認完了。", payload["body"])

    def test_wrong_declaration_type_cannot_claim_correct_usage(self):
        state, evidence, results = fixture()
        results[1]["type"] = "Microsoft.Other/items"
        with self.assertRaisesRegex(AuditError, "differs from source declaration"):
            record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())

    def test_missing_source_unit_or_changed_source_registry_cannot_finalize(self):
        state = self.assessed()
        key = next(iter(state["pairs"]))
        state["pairs"][key]["results"][1]["usage"] = []
        with self.assertRaisesRegex(AuditError, "Missing declaration-specific usage"):
            evidence_bytes(state)
        state = self.assessed()
        state["usage_targets"].pop(next(iter(state["usage_targets"])))
        with self.assertRaisesRegex(AuditError, "Source usage inventory"):
            evidence_bytes(state)

    def test_reloaded_status_and_source_hashes_are_rechecked(self):
        state = self.assessed()
        key = next(iter(state["pairs"]))
        corrupted = copy.deepcopy(state)
        corrupted["pairs"][key]["results"][0]["status"] = "unverified"
        with self.assertRaisesRegex(AuditError, "Unfinished"):
            evidence_bytes(corrupted)
        corrupted = copy.deepcopy(state)
        corrupted["source_sha256"]["fixture.bicep"] = "c" * 64
        with self.assertRaisesRegex(AuditError, "Source usage inventory"):
            evidence_bytes(corrupted)
        corrupted = copy.deepcopy(state)
        corrupted["pairs"][key]["stage"] = "complete"
        with self.assertRaisesRegex(AuditError, "Unknown pair stage"):
            evidence_bytes(corrupted)


class CoverageTests(unittest.TestCase):
    def test_inline_properties_each_require_their_own_check(self):
        units = usage_units("{ properties: { name: 'x', kind: 't' } }", "body")
        self.assertEqual([u["label"] for u in units], ["properties", "name", "kind"])
        self.assertEqual(len({u["source_id"] for u in units}), 3)
        self.assertEqual(
            [u["path"] for u in units],
            ["/properties", "/properties/name", "/properties/kind"],
        )

    def test_root_name_and_nested_name_are_distinct(self):
        units = usage_units("{ name: 'root', properties: { name: 'nested' } }", "body")
        self.assertEqual(
            [u["path"] for u in units],
            ["/name", "/properties", "/properties/name"],
        )

    def test_dictionary_keys_do_not_alias_nested_properties(self):
        units = usage_units("{ 'a.b': 1, a: { b: 2 }, 'a/b': 3 }", "body")
        self.assertEqual([u["path"] for u in units], ["/a.b", "/a", "/a/b", "/a~1b"])

    def test_resource_loops_and_property_arrays_have_distinct_paths(self):
        loop = usage_units("[for value in inputs: { name: value }]", "body")
        self.assertIn("/name", [u["path"] for u in loop])
        nested = usage_units("{ members: [{ name: 'one' }] }", "body")
        self.assertIn("/members/[]/name", [u["path"] for u in nested])
        symbol = usage_units("[{ name: 'one' }]", "symbol:items")
        self.assertIn("/$symbol/items/[]/name", [u["path"] for u in symbol])

    def test_multiline_literal_contents_are_not_lost(self):
        units = usage_units("{\nquery: '''\nfirst\nsecond\n'''\n}", "body")
        expressions = [u["expression"] for u in units]
        self.assertIn("first", expressions)
        self.assertIn("second", expressions)

    def test_transitive_variables_and_allowed_values_are_covered(self):
        source = (
            "@allowed(['One', 'Two'])\nparam mode string\n"
            "var options = { mode: mode }\n"
            "resource item 'Microsoft.Test/items@2025-01-01' = {\n"
            "properties: options\n}\n"
        )
        units = declaration_usage({"body": "{ properties: options }"}, source)
        ids = [u["source_id"] for u in units]
        expressions = [u["expression"] for u in units]
        self.assertTrue(any(i.startswith("symbol:options:") for i in ids))
        self.assertTrue(any(i.startswith("symbol:mode:") for i in ids))
        self.assertIn("@allowed(['One', 'Two'])", expressions)

    def test_comments_and_string_examples_do_not_create_properties(self):
        units = usage_units("{\n// fake: false\nname: 'text: example'\n}", "body")
        self.assertEqual([u["label"] for u in units], ["name"])


if __name__ == "__main__":
    unittest.main()
