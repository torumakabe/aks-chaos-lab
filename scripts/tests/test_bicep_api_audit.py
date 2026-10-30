import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
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


class PublishedResponse(io.BytesIO):
    def __init__(self, data, url, status=200):
        super().__init__(data)
        self.url = url
        self.status = status

    def geturl(self):
        return self.url


def mock_published_files(*bodies):
    responses = iter(bodies)

    def open_request(request, timeout):
        return PublishedResponse(next(responses), request.full_url)

    return patch("bicep_api_audit.urllib.request.urlopen", side_effect=open_request)


def published_payload(state, directory, title="fixture", allow_blocked=False):
    prepare_issue(state, title, directory, allow_blocked=allow_blocked)
    with mock_published_files(
        (Path(directory) / "evidence.json").read_bytes(),
        (Path(directory) / "report.md").read_bytes(),
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


class PublishedFileTests(unittest.TestCase):
    def test_raw_bytes_need_neither_tokens_nor_github_cli(self):
        body = bytes(range(256)) + b"\x00\r\n"
        for environment in ({}, {"GH_TOKEN": "unused", "GITHUB_TOKEN": "unused"}):
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "bicep_api_audit.subprocess.check_output",
                    side_effect=AssertionError("No subprocess may be invoked"),
                ),
                patch(
                    "bicep_api_audit.executable",
                    side_effect=AssertionError("No executable may be looked up"),
                ),
                mock_published_files(body) as opener,
            ):
                identity, actual = published_file(EVIDENCE_URL)
                self.assertEqual(identity, ("example", "lab", "b" * 40))
                self.assertEqual(actual, body)
                request = opener.call_args.args[0]
                self.assertEqual(
                    request.full_url,
                    f"https://raw.githubusercontent.com/example/lab/{'b' * 40}/audit/evidence.json",
                )
                self.assertEqual(request.get_method(), "GET")
                self.assertIsNone(request.get_header("Authorization"))
                self.assertEqual(opener.call_args.kwargs, {"timeout": 30})
                self.assertEqual(opener.call_count, 1)

    def test_http_and_connection_failures_are_explicit_without_fallback(self):
        raw_url = f"https://raw.githubusercontent.com/example/lab/{'b' * 40}/audit/evidence.json"
        for error, message in (
            (
                urllib.error.HTTPError(raw_url, 404, "Not Found", Message(), None),
                "HTTP 404",
            ),
            (
                urllib.error.HTTPError(raw_url, 503, "Unavailable", Message(), None),
                "HTTP 503",
            ),
            (urllib.error.URLError("offline"), "retrieval failed"),
            (TimeoutError("timeout"), "retrieval failed"),
        ):
            with (
                self.subTest(error=error),
                patch(
                    "bicep_api_audit.urllib.request.urlopen", side_effect=error
                ) as opener,
                patch(
                    "bicep_api_audit.subprocess.check_output",
                    side_effect=AssertionError("No CLI fallback"),
                ),
                self.assertRaisesRegex(AuditError, message),
            ):
                try:
                    published_file(EVIDENCE_URL)
                finally:
                    self.assertEqual(opener.call_count, 1)

    def test_non_success_status_and_redirected_response_are_rejected(self):
        raw_url = f"https://raw.githubusercontent.com/example/lab/{'b' * 40}/audit/evidence.json"
        for response, message in (
            (PublishedResponse(b"rate limited", raw_url, status=429), "HTTP 429"),
            (
                PublishedResponse(b"other file", raw_url.replace("b" * 40, "main")),
                "redirected",
            ),
        ):
            with (
                self.subTest(message=message),
                patch(
                    "bicep_api_audit.urllib.request.urlopen", return_value=response
                ) as opener,
                self.assertRaisesRegex(AuditError, message),
            ):
                try:
                    published_file(EVIDENCE_URL)
                finally:
                    self.assertEqual(opener.call_count, 1)

    def test_over_four_megabytes_remain_byte_exact_through_issue_preparation(self):
        state, evidence, results = fixture()
        for result in results:
            result["usage"][0]["analysis"] = "雪" * 400_000
        record_pair(state, next_pair(state)[0], results, evidence, FixtureFetcher())
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as directory,
            patch.dict(os.environ, {}, clear=True),
            patch(
                "bicep_api_audit.subprocess.check_output",
                side_effect=AssertionError("No CLI dependency"),
            ),
        ):
            payload = published_payload(state, directory)
            data = (Path(directory) / "evidence.json").read_bytes()
            self.assertGreater(len(data), 4 * 1024 * 1024)
            self.assertEqual(data, evidence_bytes(state))
            self.assertEqual(json.loads(data)["state"], state)
            self.assertIn(hashlib.sha256(data).hexdigest(), payload["body"])
            self.assertLess(len(payload["body"].encode("utf-8")), 6000)
            self.assertEqual(len(verify_issue(payload, dict(payload))), 64)


class PartialBlockedTests(unittest.TestCase):
    def setUp(self):
        state, evidence, _ = fixture()
        rows = [
            {**row, "version": "2026-01-01-preview"} for row in state["declarations"]
        ]
        self.state = new_state(
            {
                "files": state["files"],
                "declarations": rows,
                "sources": state["sources"],
            },
            SHA,
            SHA,
        )
        self.key = next_pair(self.state)[0]
        self.attempts = [
            {
                "url": BASE + "preview/2026-01-01-preview/api.json",
                "finding": "Exact current schema unavailable",
            },
            {"url": CHANGE, "finding": "Current property coverage unresolved"},
        ]
        self.partial_results = [
            {
                "file": row["file"],
                "line": row["line"],
                "name": row["name"],
                "findings": [{"kind": kind, "detail": detail}],
            }
            for row, kind, detail in zip(
                rows,
                ("update", "issue"),
                (
                    "GA 2026-01-01 exists; compatibility remains unresolved",
                    "Separately confirmed configuration issue",
                ),
                strict=True,
            )
        ]
        self.partial_evidence = {"ga": evidence["ga"]}

    def block(self, state=None, **changes):
        values = {
            "partial_results": self.partial_results,
            "partial_evidence": self.partial_evidence,
            **changes,
        }
        block_pair(
            self.state if state is None else state,
            self.key,
            self.attempts,
            "Exact current schema missing",
            **values,
        )

    def test_ga_and_findings_survive_without_current_schema_completion(self):
        self.block()
        with self.assertRaisesRegex(AuditError, "Missing public evidence"):
            render(self.state)
        report = render(self.state, allow_blocked=True)
        self.assertIn("partial ga: 2026-01-01", report)
        self.assertIn(GA, report)
        self.assertIn("issue=1, update=1", report)
        self.assertIn("verified=0, excluded=0, unverified=2", report)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            payload = published_payload(self.state, directory, allow_blocked=True)
            data = (Path(directory) / "evidence.json").read_bytes()
            document = json.loads(data)
            pair = document["state"]["pairs"][self.key]
            self.assertEqual(pair["partial_evidence"], self.partial_evidence)
            self.assertEqual(pair["partial_results"], self.partial_results)
            self.assertNotIn("current", pair["partial_evidence"])
            self.assertEqual(pair["stage"], "blocked")
            self.assertFalse(document["summary"]["complete"])
            self.assertEqual(document["summary"]["issue"], 1)
            self.assertEqual(document["summary"]["update"], 1)
            self.assertEqual(document["summary"]["unverified"], 2)
            self.assertTrue(
                all(row["status"] == "unverified" for row in document["results"])
            )
            self.assertEqual(
                [row["findings"] for row in document["results"]],
                [row["findings"] for row in self.partial_results],
            )
            self.assertEqual(data, evidence_bytes(self.state, allow_blocked=True))
            self.assertIn("issue=1, update=1", payload["body"])
            self.assertNotIn("確認完了。", payload["body"])

    def test_partial_identity_coverage_must_be_exact(self):
        for rows in (
            [],
            self.partial_results[:1],
            [self.partial_results[0], self.partial_results[0]],
            self.partial_results + [{**self.partial_results[0], "name": "extra"}],
        ):
            with self.subTest(rows=rows):
                state = copy.deepcopy(self.state)
                with self.assertRaisesRegex(AuditError, "partial declaration results"):
                    self.block(state, partial_results=rows)
                self.assertEqual(state["pairs"][self.key]["stage"], "pending")
        rows = copy.deepcopy(self.partial_results)
        del rows[0]["name"]
        with self.assertRaisesRegex(AuditError, "Missing partial declaration identity"):
            self.block(partial_results=rows)

    def test_partial_findings_require_valid_kind_and_nonblank_detail(self):
        for finding in (
            {"kind": "other", "detail": "detail"},
            {"kind": "update"},
            {"kind": "issue", "detail": " \n"},
            {"kind": "update", "detail": 3},
        ):
            with self.subTest(finding=finding):
                rows = copy.deepcopy(self.partial_results)
                rows[0]["findings"] = [finding]
                with self.assertRaisesRegex(AuditError, "Invalid partial issue/update"):
                    self.block(partial_results=rows)

    def test_partial_evidence_requires_analysis_version_and_public_urls(self):
        for source in (
            {**self.partial_evidence["ga"], "analysis": " \n"},
            {**self.partial_evidence["ga"], "urls": []},
            {**self.partial_evidence["ga"], "version": ""},
        ):
            with self.subTest(source=source), self.assertRaises(AuditError):
                self.block(partial_evidence={"ga": source})
        for url in (
            "http://learn.microsoft.com/version",
            "https://example.com/schema.json",
            "../schema.json",
        ):
            with self.subTest(url=url), self.assertRaises(AuditError):
                self.block(
                    partial_evidence={
                        "ga": {**self.partial_evidence["ga"], "urls": [url]},
                    }
                )

    def test_partial_ga_cannot_substitute_for_current_version(self):
        with self.assertRaisesRegex(AuditError, "Wrong partial current version"):
            self.block(partial_evidence={"current": self.partial_evidence["ga"]})
        self.assertEqual(self.state["pairs"][self.key]["stage"], "pending")

    def test_partial_rest_commit_and_optional_full_hash_are_checked(self):
        for source, message in (
            (
                {**self.partial_evidence["ga"], "urls": [GA.replace(SHA, "main")]},
                "not pinned",
            ),
            (
                {**self.partial_evidence["ga"], "sha256": {GA: "short"}},
                "Invalid partial evidence hash",
            ),
        ):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(AuditError, message),
            ):
                self.block(partial_evidence={"ga": source})

    def test_partial_cannot_claim_verified_or_findings_without_evidence(self):
        rows = copy.deepcopy(self.partial_results)
        rows[0]["status"] = "verified"
        with self.assertRaisesRegex(AuditError, "must remain unverified"):
            self.block(partial_results=rows)
        with self.assertRaisesRegex(AuditError, "require source evidence"):
            self.block(partial_evidence=None)
        self.assertEqual(self.state["pairs"][self.key]["stage"], "pending")

    def test_reloaded_partial_findings_and_evidence_are_revalidated(self):
        self.block()
        state = copy.deepcopy(self.state)
        state["pairs"][self.key]["partial_results"][0]["findings"][0]["kind"] = "other"
        with self.assertRaisesRegex(AuditError, "Invalid partial issue/update"):
            evidence_bytes(state, allow_blocked=True)
        state = copy.deepcopy(self.state)
        state["pairs"][self.key]["partial_evidence"]["ga"]["analysis"] = ""
        with self.assertRaisesRegex(AuditError, "Missing partial source analysis"):
            render(state, allow_blocked=True)

    def test_partial_evidence_only_is_preserved_and_input_aliases_are_frozen(self):
        self.block(partial_results=None)
        self.assertIn("partial ga: 2026-01-01", render(self.state, allow_blocked=True))
        document = json.loads(evidence_bytes(self.state, allow_blocked=True))
        self.assertEqual(document["summary"]["update"], 0)
        self.assertEqual(document["summary"]["unverified"], 2)
        self.partial_evidence["ga"]["analysis"] = "changed after block"
        self.assertNotIn("changed after block", render(self.state, allow_blocked=True))


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
                mock_published_files(
                    changed,
                    render(state).encode(),
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
            mock_published_files(
                evidence_bytes(state),
                b"changed",
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
            mock_published_files(
                evidence_bytes(state),
                render(state).encode(),
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
                    "bicep_api_audit.urllib.request.urlopen",
                    side_effect=OSError("offline"),
                ),
                self.assertRaisesRegex(AuditError, "retrieval failed"),
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
