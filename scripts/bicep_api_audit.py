"""無効の手動移行 PoC 用 Bicep API 監査状態とファイル出力。"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit

TOKEN = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/|'''[\s\S]*?'''|'(?:[^'\\]|\\.)*'"
    r"|[A-Za-z_][A-Za-z_0-9]*|[^\s]"
)


def inventory(root):
    root = Path(root)
    paths = (
        subprocess.check_output(
            [
                executable("git"),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    files = sorted({p for p in paths if p.endswith(".bicep")})
    rows = []
    sources = {}
    for path in files:
        text = (root / path).read_text(encoding="utf-8")
        sources[path] = text
        tokens = [
            m for m in TOKEN.finditer(text) if not m.group().startswith(("//", "/*"))
        ]
        for i, token in enumerate(tokens):
            if token.group() != "resource":
                continue
            name, literal = tokens[i + 1 : i + 3]
            if not re.fullmatch(r"[A-Za-z_]\w*", name.group()):
                continue
            if not literal.group().startswith("'"):
                continue
            j = i + 3
            existing = tokens[j].group() == "existing"
            if existing:
                j += 1
            if tokens[j].group() != "=":
                raise ValueError(f"{path}:{token.start()}: unexpected declaration")
            opening = j + 1
            # Conditional declarations put if (...) before the resource body.
            if tokens[opening].group() == "if":
                opening += 1
                depth = 0
                while opening < len(tokens):
                    value = tokens[opening].group()
                    depth += (value == "(") - (value == ")")
                    opening += 1
                    if depth == 0:
                        break
            if tokens[opening].group() not in ("{", "["):
                raise ValueError(f"{path}:{token.start()}: unsupported body")
            stack = []
            closing = None
            for k in range(opening, len(tokens)):
                value = tokens[k].group()
                if value in ("{", "[", "("):
                    stack.append(value)
                elif value in ("}", "]", ")"):
                    if (
                        not stack
                        or stack.pop() != {"}": "{", "]": "[", ")": "("}[value]
                    ):
                        raise ValueError(f"{path}:{token.start()}: unbalanced body")
                    if not stack:
                        closing = tokens[k].end()
                        break
            if closing is None:
                raise ValueError(f"{path}:{token.start()}: missing body end")
            raw = literal.group()[1:-1]
            kind, at, version = raw.partition("@")
            parent = next(
                (
                    r
                    for r in reversed(rows)
                    if r["file"] == path and r["start"] < token.start() < r["end"]
                ),
                None,
            )
            if "." not in kind.split("/")[0]:
                if parent is None:
                    raise ValueError(f"{path}:{token.start()}: unresolved parent")
                kind = parent["type"] + "/" + kind
                if not at:
                    version = parent["version"]
            if not version:
                raise ValueError(f"{path}:{token.start()}: unresolved version")
            rows.append(
                {
                    "file": path,
                    "name": name.group(),
                    "line": text.count("\n", 0, token.start()) + 1,
                    "type": kind,
                    "version": version,
                    "existing": existing,
                    "start": token.start(),
                    "end": closing,
                    "header": text[token.start() : tokens[opening].start()],
                    "body": text[tokens[opening].start() : closing],
                }
            )
    return {"files": files, "declarations": rows, "sources": sources}


def usage_units(text, prefix):
    tokens = [m for m in TOKEN.finditer(text) if not m.group().startswith(("//", "/*"))]
    lines = text.splitlines()
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    units = []
    covered = set()
    meaningful = set()
    line_paths = {}
    root = ["$symbol", prefix.split(":", 1)[1]] if prefix.startswith("symbol:") else []
    stack: list[dict[str, Any]] = []
    current: dict[str, Any] = {"path": root, "value": root, "ternary": 0}

    def pointer(parts):
        return "/" + "/".join(
            str(p).replace("~", "~0").replace("/", "~1") for p in parts
        )

    for index, token in enumerate(tokens):
        value = token.group()
        first = text.count("\n", 0, token.start())
        last = text.count("\n", 0, token.end() - 1)
        if value not in ("{", "}", "[", "]", "(", ")", ","):
            meaningful.update(range(first, last + 1))
            for line in range(first, last + 1):
                line_paths.setdefault(line, list(current["value"]))
        if value in ("{", "[", "("):
            parent = current
            path = list(parent["value"])
            if value == "[" and (stack or root):
                path.append("[]")
            current = {"path": path, "value": path, "ternary": 0, "kind": value}
            stack.append(parent)
            continue
        if value in ("}", "]", ")"):
            if not stack:
                raise ValueError("Unbalanced property source")
            current = stack.pop()
            continue
        previous = tokens[index - 1].group() if index else ""
        following = tokens[index + 1].group() if index + 1 < len(tokens) else ""
        if value == "?" and previous != "?" and following not in ("?", "."):
            current["ternary"] += 1
        if value != ":":
            continue
        covered.add(first)
        is_key = bool(
            re.fullmatch(r"[A-Za-z_][\w-]*", previous) or previous.startswith("'")
        )
        if current["ternary"]:
            current["ternary"] -= 1
            is_key = False
        if is_key and current.get("kind") == "{":
            label = (
                previous[1:-1].replace("\\'", "'")
                if previous.startswith("'")
                else previous
            )
            path = [*current["path"], label]
            current["value"] = path
        else:
            label = "$expression"
            path = [*current["path"], "$syntax", token.start()]
        units.append(
            {
                "source_id": f"{prefix}:{token.start()}",
                "label": label,
                "path": pointer(path),
                "expression": lines[first],
            }
        )
    for line in sorted(meaningful - covered):
        units.append(
            {
                "source_id": f"{prefix}:line:{starts[line]}",
                "label": "$expression",
                "path": pointer([*line_paths[line], "$expression-line", line]),
                "expression": lines[line],
            }
        )
    return units


def dependency_sources(text):
    tokens = [m for m in TOKEN.finditer(text) if not m.group().startswith(("//", "/*"))]
    headers = []
    depth = 0
    decorator = None
    keywords = {
        "param",
        "var",
        "resource",
        "module",
        "output",
        "type",
        "func",
        "import",
        "extension",
        "metadata",
        "targetScope",
        "assert",
    }
    for index, token in enumerate(tokens):
        value = token.group()
        if depth == 0 and value == "@":
            decorator = token.start() if decorator is None else decorator
        leading = text[text.rfind("\n", 0, token.start()) + 1 : token.start()]
        if depth == 0 and value in keywords and not leading.strip():
            headers.append(
                (
                    decorator if decorator is not None else token.start(),
                    value,
                    tokens[index + 1].group(),
                )
            )
            decorator = None
        if value in ("{", "[", "("):
            depth += 1
        elif value in ("}", "]", ")"):
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced dependency source")
    if depth:
        raise ValueError("Unbalanced dependency source")
    definitions = {}
    for index, (start, kind, name) in enumerate(headers):
        if kind in ("param", "var", "type"):
            end = headers[index + 1][0] if index + 1 < len(headers) else len(text)
            definitions[name] = text[start:end]
    return definitions


def declaration_usage(row, source):
    body = row["body"]
    header = row.get("header", "")
    units = usage_units(body, "body") + usage_units(header, "header")
    definitions = dependency_sources(source)
    remaining = set(re.findall(r"\b[A-Za-z_]\w*\b", body + header))
    seen = set()
    while remaining:
        name = min(remaining)
        remaining.remove(name)
        if name in seen or name not in definitions:
            continue
        seen.add(name)
        definition = definitions[name]
        units.extend(usage_units(definition, "symbol:" + name))
        remaining.update(re.findall(r"\b[A-Za-z_]\w*\b", definition))
    return units


def save_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    path = Path(path)
    path.write_text(encoded, encoding="utf-8")
    if json.loads(path.read_text(encoding="utf-8")) != value:
        raise ValueError(f"JSON readback mismatch: {path}")


class Fetcher:
    def __init__(
        self,
        directory,
        opener=urllib.request.urlopen,
        clock=time.monotonic,
        sleep=time.sleep,
        wall=time.time,
    ):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener, self.clock, self.sleep, self.wall = opener, clock, sleep, wall
        log = self.directory / "http-attempts.json"
        self.attempts = (
            json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
        )
        learn_attempts = [
            a
            for a in self.attempts
            if urlsplit(a["url"]).hostname == "learn.microsoft.com"
        ]
        self.last_learn = (
            learn_attempts[-1]["started_monotonic"] if learn_attempts else None
        )
        self.consecutive_429 = 0
        for attempt in reversed(learn_attempts):
            if attempt["status"] != 429:
                break
            self.consecutive_429 += 1
        self.learn_stopped = self.consecutive_429 >= 3

    def get(self, url):
        host = urlsplit(url).hostname
        if host not in ("learn.microsoft.com", "api.github.com") and not url.startswith(
            "https://raw.githubusercontent.com/Azure/azure-rest-api-specs/"
        ):
            raise ValueError(f"Unapproved public source: {url}")
        cache = self.directory / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        learn = urlsplit(url).hostname == "learn.microsoft.com"
        if learn and self.learn_stopped:
            raise RuntimeError("Learn requests stopped after three consecutive 429s")
        retry_wait = 0
        for attempt in range(1, 5):
            spacing = (
                0
                if self.last_learn is None
                else max(0, 3 - (self.clock() - self.last_learn))
            )
            waited = max(retry_wait, spacing if learn else 0)
            if waited:
                self.sleep(waited)
            started = self.clock()
            if learn:
                self.last_learn = started
            retry_after = None
            result = None
            error = None
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "bicep-api-audit/1.0"}
                )
                try:
                    response = self.opener(request, timeout=30)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    retry_after = response.headers.get("Retry-After")
                    result = {
                        "url": url,
                        "final_url": response.geturl(),
                        "status": response.code,
                        "headers": dict(response.headers),
                        "body": response.read().decode("utf-8"),
                    }
            except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            self.attempts.append(
                {
                    "url": url,
                    "attempt": attempt,
                    "started_monotonic": started,
                    "status": result["status"] if result else None,
                    "error": error,
                    "retry_after": retry_after,
                    "wait_seconds": waited,
                }
            )
            save_json(self.directory / "http-attempts.json", self.attempts)
            if error:
                raise RuntimeError(error)
            if result is None:
                raise RuntimeError("No HTTP response")
            if result["status"] != 429:
                if learn:
                    self.consecutive_429 = 0
                save_json(cache, result)
                return result
            if learn:
                self.consecutive_429 += 1
                if self.consecutive_429 >= 3:
                    self.learn_stopped = True
                    save_json(cache, result)
                    return result
            if retry_after:
                try:
                    retry_wait = max(0, float(retry_after))
                except ValueError:
                    parsed = parsedate_to_datetime(retry_after)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    retry_wait = max(0, parsed.timestamp() - self.wall())
            else:
                retry_wait = (30, 60, 120)[min(attempt - 1, 2)]
        save_json(cache, result)
        return result


def resolve_href(response, href):
    return urljoin(response["final_url"], href)


class AuditError(ValueError):
    pass


def identity(row):
    return row["file"], row["line"], row["name"]


def require(condition, message):
    if not condition:
        raise AuditError(message)


def executable(name):
    path = shutil.which(name)
    require(path is not None, f"Required executable unavailable: {name}")
    return path


def public_url(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "https", "Evidence must use HTTPS")
    require(not parsed.username and not parsed.password, "Credentials in evidence URL")
    require(
        parsed.hostname in ("learn.microsoft.com", "api.github.com")
        or (
            parsed.hostname == "raw.githubusercontent.com"
            and parsed.path.startswith("/Azure/azure-rest-api-specs/")
        ),
        "Unapproved evidence host or repository",
    )


class Schemas:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.documents = {}

    def document(self, url):
        public_url(url)
        if url not in self.documents:
            response = self.fetcher.get(url)
            require(
                response["status"] == 200, f"Schema HTTP {response['status']}: {url}"
            )
            self.documents[url] = json.loads(response["body"])
        return self.documents[url]

    def locate(self, reference, base):
        url, pointer = urldefrag(urljoin(base, reference))
        value = self.document(url)
        if pointer:
            require(pointer.startswith("/"), f"Unsupported JSON pointer: {pointer}")
            for part in pointer[1:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                value = value[int(part)] if isinstance(value, list) else value[part]
        require(
            isinstance(value, dict), f"Schema reference is not an object: {reference}"
        )
        return value, url

    def expand(self, schema, base, seen=()):
        if "$ref" in schema:
            absolute = urljoin(base, schema["$ref"])
            require(
                absolute not in seen,
                f"Recursive schema requires focused review: {absolute}",
            )
            target, target_base = self.locate(schema["$ref"], base)
            parts = [self.expand(target, target_base, (*seen, absolute))]
            parts.append({k: v for k, v in schema.items() if k != "$ref"})
        else:
            parts = [self.expand(part, base, seen) for part in schema.get("allOf", [])]
            parts.append({k: v for k, v in schema.items() if k != "allOf"})
        merged = {"properties": {}, "required": []}
        for part in parts:
            origin = part.get("_base", base)
            for name, value in part.items():
                if name == "_base":
                    continue
                if name == "properties":
                    for key, child in value.items():
                        require(
                            key not in merged["properties"]
                            or merged["properties"][key] == child,
                            f"Conflicting inherited property: {key}",
                        )
                        # Keep the originating document for relative child references.
                        child = dict(child)
                        child.setdefault("_base", origin)
                        merged["properties"][key] = child
                elif name == "required":
                    merged["required"] = list(
                        dict.fromkeys([*merged["required"], *value])
                    )
                elif name in ("title", "description"):
                    merged[name] = value
                elif name in ("items", "additionalProperties") and isinstance(
                    value, dict
                ):
                    child = dict(value)
                    child.setdefault("_base", origin)
                    require(
                        name not in merged or merged[name] == child,
                        f"Conflicting {name}",
                    )
                    merged[name] = child
                elif name in merged and merged[name] != value:
                    raise AuditError(f"Conflicting schema constraint: {name}")
                else:
                    merged[name] = value
        merged["_base"] = base
        return merged

    def property(self, schema, base, path):
        current = schema
        for field in path.split("."):
            base = current.get("_base", base)
            current = self.expand(current, base)
            if field == "[]":
                require("items" in current, "Expected array items")
                current = current["items"]
            elif field in current.get("properties", {}):
                current = current["properties"][field]
            else:
                additional = current.get("additionalProperties", None)
                require(
                    isinstance(additional, dict),
                    f"Property requires explicit additionalProperties review: {path}",
                )
                current = additional
        return self.expand(current, current.get("_base", base))

    def literal(self, schema, base, path, value):
        resolved = self.property(schema, base, path)
        require(not resolved.get("readOnly"), f"Read-only property: {path}")
        if "enum" in resolved:
            require(value in resolved["enum"], f"Unsupported enum at {path}: {value!r}")
        kind = resolved.get("type")
        expected = {
            "string": str,
            "boolean": bool,
            "integer": int,
            "number": (int, float),
            "object": dict,
            "array": list,
        }.get(kind)
        if expected is None:
            raise AuditError(f"Unresolved type at {path}: {kind}")
        require(
            isinstance(value, expected)
            and not (kind in ("integer", "number") and isinstance(value, bool)),
            f"Wrong literal type at {path}: {value!r}",
        )
        if kind == "string":
            require(
                len(value) >= resolved.get("minLength", 0)
                and len(value) <= resolved.get("maxLength", float("inf")),
                f"Invalid string length at {path}",
            )
            require(
                "pattern" not in resolved or re.search(resolved["pattern"], value),
                f"Invalid string pattern at {path}",
            )
        if kind in ("integer", "number"):
            for bound, passes in (
                (
                    "minimum",
                    lambda limit: (
                        value > limit
                        if resolved.get("exclusiveMinimum")
                        else value >= limit
                    ),
                ),
                (
                    "maximum",
                    lambda limit: (
                        value < limit
                        if resolved.get("exclusiveMaximum")
                        else value <= limit
                    ),
                ),
            ):
                require(
                    bound not in resolved or passes(resolved[bound]),
                    f"Invalid {bound} at {path}",
                )
        if kind == "object":
            require(
                all(field in value for field in resolved["required"]),
                f"Missing required properties at {path}",
            )
        if kind == "array":
            require(
                len(value) >= resolved.get("minItems", 0)
                and len(value) <= resolved.get("maxItems", float("inf")),
                f"Invalid array length at {path}",
            )
        return resolved


def new_state(inventory, commit, specs_commit):
    rows = inventory["declarations"]
    require(rows and inventory["files"], "Unresolved or empty target inventory")
    require(
        len(set(inventory["files"])) == len(inventory["files"])
        and set(inventory["files"]) == set(inventory["sources"])
        and all(row["file"] in inventory["sources"] for row in rows),
        "Source files differ from inventory",
    )
    require(len({identity(row) for row in rows}) == len(rows), "Duplicate inventory")
    require(
        all(
            len(value) == 40 and all(c in "0123456789abcdef" for c in value)
            for value in (commit, specs_commit)
        ),
        "Sources must be pinned by full commit SHA",
    )
    return {
        "commit": commit,
        "specs_commit": specs_commit,
        "files": inventory["files"],
        "declarations": rows,
        "sources": inventory["sources"],
        "source_sha256": {
            path: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for path, text in inventory["sources"].items()
        },
        "usage_targets": {
            json.dumps(identity(row)): declaration_usage(
                row, inventory["sources"][row["file"]]
            )
            for row in rows
        },
        "pairs": {
            key: {"stage": "pending", "results": [], "evidence": {}}
            for key in sorted({f"{r['type']}@{r['version']}" for r in rows})
        },
    }


def next_pair(state):
    for key, pair in state["pairs"].items():
        if pair["stage"] != "pending":
            continue
        rows = [
            r for r in state["declarations"] if f"{r['type']}@{r['version']}" == key
        ]
        return key, rows
    return None


def assessment_template(state):
    pair = next_pair(state)
    if pair is None:
        return None
    key, rows = pair
    return {
        "pair": key,
        "results": [
            {
                **row,
                "status": "pending",
                "reason": "",
                "migration": "",
                "findings": [],
                "usage": [
                    {**unit, "analysis": ""}
                    for unit in state["usage_targets"][json.dumps(identity(row))]
                ],
            }
            for row in rows
        ],
        "evidence": {
            "current": {"version": key.rsplit("@", 1)[1], "analysis": "", "urls": []},
            "ga": {"version": "", "analysis": "", "urls": []},
            "breaking": {"analysis": "", "urls": []},
        },
    }


def validate_pair(state, key, results, evidence, fetcher=None):
    require(key in state["pairs"], f"Unknown pair: {key}")
    expected = [
        r for r in state["declarations"] if f"{r['type']}@{r['version']}" == key
    ]
    require(
        Counter(map(identity, expected)) == Counter(map(identity, results)),
        f"Missing, extra, or duplicate declaration results: {key}",
    )
    required = ("current", "ga", "breaking")
    require(all(evidence.get(name) for name in required), f"Incomplete evidence: {key}")
    for name in required:
        source = evidence[name]
        require(
            source.get("analysis") and source.get("urls"),
            f"Missing source analysis: {key}/{name}",
        )
        for url in source["urls"]:
            public_url(url)
            if fetcher is not None:
                response = fetcher.get(url)
                require(
                    response["status"] == 200, f"Unread evidence: {key}/{name}/{url}"
                )
                require(response["body"].strip(), f"Empty evidence: {url}")
                source.setdefault("sha256", {})[url] = hashlib.sha256(
                    response["body"].encode("utf-8")
                ).hexdigest()
            require(
                re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", {}).get(url, "")),
                f"Missing full evidence hash: {url}",
            )
        if name != "breaking":
            require(source.get("version") is not None, f"Missing exact version: {name}")
    current_version = key.rsplit("@", 1)[1]
    require(evidence["current"]["version"] == current_version, "Wrong current version")
    pinned = f"/Azure/azure-rest-api-specs/{state['specs_commit']}/"
    require(
        all(
            urlsplit(url).hostname != "raw.githubusercontent.com" or pinned in url
            for name in required
            for url in evidence[name]["urls"]
        ),
        "REST evidence is not pinned to the reviewed commit",
    )
    for result in results:
        require(
            result.get("status") in ("verified", "excluded"), "Unfinished assessment"
        )
        require(result.get("reason") and result.get("migration"), "Missing judgment")
        require(result.get("usage"), "Missing declaration-specific usage checks")
        targets = {
            unit["source_id"]: unit
            for unit in state["usage_targets"][json.dumps(identity(result))]
        }
        require(
            Counter(check.get("source_id") for check in result["usage"])
            == Counter(targets.keys()),
            "Missing, extra, or duplicate source property/value checks",
        )
        for check in result["usage"]:
            require(
                all(check.get(field) for field in ("path", "expression", "analysis")),
                "Incomplete property/value check",
            )
            unit = targets[check["source_id"]]
            require(
                check["expression"] == unit["expression"], "Changed source expression"
            )
            require(
                check["path"] == unit["path"],
                "Source property does not match the assessed property",
            )
        original = next(row for row in expected if identity(row) == identity(result))
        require(
            all(result.get(field) == value for field, value in original.items()),
            "Result differs from source declaration",
        )
        require(
            isinstance(result.get("findings"), list), "Missing issue/update findings"
        )
        for finding in result["findings"]:
            require(
                finding.get("kind") in ("issue", "update") and finding.get("detail"),
                "Invalid issue/update finding",
            )
        if result["status"] == "excluded":
            require(
                current_version.endswith("-preview"),
                "Only reviewed previews can be excluded",
            )
            require(
                result.get("exclusion_reason"),
                "Exclusion cannot replace unfinished work",
            )


def record_pair(state, key, results, evidence, fetcher):
    require(key in state["pairs"], f"Unknown pair: {key}")
    pair = state["pairs"][key]
    require(pair["stage"] == "pending", f"Pair already assessed: {key}")
    evidence = json.loads(json.dumps(evidence))
    validate_pair(state, key, results, evidence, fetcher)
    pair.update(
        stage="assessed",
        results=json.loads(json.dumps(results)),
        evidence=evidence,
    )


def validate_partial(state, key, partial_results, partial_evidence):
    require(key in state["pairs"], f"Unknown pair: {key}")
    expected = [
        row for row in state["declarations"] if f"{row['type']}@{row['version']}" == key
    ]
    partial = {}
    if partial_results is not None:
        require(isinstance(partial_results, list), "Partial results must be a list")
        require(
            all(
                isinstance(row, dict)
                and all(field in row for field in ("file", "line", "name"))
                for row in partial_results
            ),
            "Missing partial declaration identity",
        )
        require(
            Counter(map(identity, expected)) == Counter(map(identity, partial_results)),
            "Missing, extra, or duplicate partial declaration results",
        )
        for row in partial_results:
            require(
                row.get("status", "unverified") == "unverified",
                "Partial results must remain unverified",
            )
            require(isinstance(row.get("findings"), list), "Missing partial findings")
            for finding in row["findings"]:
                require(
                    isinstance(finding, dict)
                    and finding.get("kind") in ("issue", "update")
                    and isinstance(finding.get("detail"), str)
                    and finding["detail"].strip(),
                    "Invalid partial issue/update finding",
                )
            partial[identity(row)] = row
    if partial_evidence is not None:
        require(
            isinstance(partial_evidence, dict)
            and partial_evidence
            and set(partial_evidence) <= {"current", "ga", "breaking"},
            "Invalid partial evidence classes",
        )
        for label, source in partial_evidence.items():
            require(
                isinstance(source, dict)
                and isinstance(source.get("analysis"), str)
                and source["analysis"].strip()
                and isinstance(source.get("urls"), list)
                and source["urls"],
                "Missing partial source analysis or URLs",
            )
            if label != "breaking":
                require(
                    isinstance(source.get("version"), str)
                    and source["version"].strip(),
                    "Missing exact partial version",
                )
            if label == "current":
                require(
                    source["version"] == key.rsplit("@", 1)[1],
                    "Wrong partial current version",
                )
            for url in source["urls"]:
                require(
                    isinstance(url, str) and url.strip(), "Invalid partial evidence URL"
                )
                public_url(url)
                require(
                    urlsplit(url).hostname != "raw.githubusercontent.com"
                    or urlsplit(url).path.startswith(
                        f"/Azure/azure-rest-api-specs/{state['specs_commit']}/"
                    ),
                    "Partial REST evidence is not pinned to the reviewed commit",
                )
                if "sha256" in source:
                    require(
                        isinstance(source["sha256"], dict)
                        and isinstance(source["sha256"].get(url), str)
                        and re.fullmatch(r"[0-9a-f]{64}", source["sha256"][url]),
                        "Invalid partial evidence hash",
                    )
    require(
        not any(row["findings"] for row in partial.values()) or partial_evidence,
        "Partial findings require source evidence",
    )
    return partial


def block_pair(
    state, key, attempts, reason, *, partial_results=None, partial_evidence=None
):
    require(key in state["pairs"], f"Unknown pair: {key}")
    require(state["pairs"][key]["stage"] == "pending", "Pair already processed")
    require(
        reason and len(attempts) >= 2, "Unavailability needs concrete source attempts"
    )
    for attempt in attempts:
        public_url(attempt["url"])
        require(attempt.get("finding"), "Missing failed lookup finding")
    validate_partial(state, key, partial_results, partial_evidence)
    state["pairs"][key].update(stage="blocked", attempts=attempts, reason=reason)
    if partial_results is not None:
        state["pairs"][key]["partial_results"] = json.loads(json.dumps(partial_results))
    if partial_evidence is not None:
        state["pairs"][key]["partial_evidence"] = json.loads(
            json.dumps(partial_evidence)
        )


def finalized_records(state, allow_blocked=False):
    require(
        set(state["pairs"])
        == {f"{row['type']}@{row['version']}" for row in state["declarations"]},
        "Pair inventory differs from declarations",
    )
    require(
        all(
            pair["stage"] in ("pending", "assessed", "blocked")
            for pair in state["pairs"].values()
        ),
        "Unknown pair stage",
    )
    rebuilt = new_state(
        {
            "files": state["files"],
            "declarations": state["declarations"],
            "sources": state["sources"],
        },
        state["commit"],
        state["specs_commit"],
    )
    require(
        rebuilt["usage_targets"] == state["usage_targets"]
        and rebuilt["source_sha256"] == state["source_sha256"],
        "Source usage inventory or hash changed",
    )
    pending = [
        key for key, pair in state["pairs"].items() if pair["stage"] == "pending"
    ]
    blocked = [
        key for key, pair in state["pairs"].items() if pair["stage"] == "blocked"
    ]
    require(not pending, f"Pending assessments cannot be finalized: {pending}")
    require(
        allow_blocked or not blocked,
        f"Missing public evidence prevents completion: {blocked}",
    )
    records = [row for pair in state["pairs"].values() for row in pair["results"]]
    blocked_results = {}
    for key, pair in state["pairs"].items():
        if pair["stage"] == "assessed":
            validate_pair(state, key, pair["results"], pair["evidence"])
    for key in blocked:
        pair = state["pairs"][key]
        require(not pair["results"], "Blocked pairs cannot contain assessed results")
        require(
            pair.get("reason") and len(pair.get("attempts", [])) >= 2,
            "Unavailability needs concrete source attempts",
        )
        for attempt in pair["attempts"]:
            public_url(attempt["url"])
            require(attempt.get("finding"), "Missing failed lookup finding")
        partial = validate_partial(
            state, key, pair.get("partial_results"), pair.get("partial_evidence")
        )
        rows = [
            {
                **row,
                "status": "unverified",
                "reason": pair["reason"],
                "migration": "未確定",
                "usage": [],
                "findings": partial.get(identity(row), {}).get("findings", []),
            }
            for row in state["declarations"]
            if f"{row['type']}@{row['version']}" == key
        ]
        blocked_results[key] = rows
        records.extend(rows)
    require(
        all(row["status"] in ("verified", "excluded", "unverified") for row in records),
        "Invalid final assessment status",
    )
    require(
        all(
            row["status"] in ("verified", "excluded")
            for pair in state["pairs"].values()
            for row in pair["results"]
        ),
        "Unfinished assessments cannot be finalized",
    )
    require(
        Counter(map(identity, state["declarations"]))
        == Counter(map(identity, records)),
        "Final declaration coverage differs from inventory",
    )
    return records, blocked_results


def summary(state, records):
    counts = Counter(row["status"] for row in records)
    return {
        "files": len(state["files"]),
        "declarations": len(records),
        "pairs": len(state["pairs"]),
        "verified": counts["verified"],
        "excluded": counts["excluded"],
        "unverified": counts["unverified"],
        "issue": sum(f["kind"] == "issue" for r in records for f in r["findings"]),
        "update": sum(f["kind"] == "update" for r in records for f in r["findings"]),
        "complete": not counts["unverified"],
    }


def render(state, allow_blocked=False):
    records, blocked_results = finalized_records(state, allow_blocked)
    counts = Counter(row["status"] for row in records)
    issue_count = sum(f["kind"] == "issue" for r in records for f in r["findings"])
    update_count = sum(f["kind"] == "update" for r in records for f in r["findings"])
    lines = [
        "# Bicep API version check-only",
        "",
        "確認未完了。以下の公開根拠不足を除き照合済み。"
        if blocked_results
        else "確認完了。",
        f"対象commit: `{state['commit']}`",
        f"REST commit: `{state['specs_commit']}`",
        f"{len(state['files'])} files / {len(records)} declarations / {len(state['pairs'])} pairs",
        f"verified={counts['verified']}, excluded={counts['excluded']}, unverified={counts['unverified']}",
        f"issue={issue_count}, update={update_count}",
        "",
    ]
    for number, (key, pair) in enumerate(state["pairs"].items(), 1):
        lines.extend([f"## P{number:02} {key}", ""])
        for label in () if key in blocked_results else ("current", "ga", "breaking"):
            source = pair["evidence"][label]
            lines.append(f"{label}: {source.get('version', '')} {source['analysis']}")
            lines.extend(source["urls"])
        if key in blocked_results:
            lines.extend(
                f"{attempt['url']}: {attempt['finding']}"
                for attempt in pair["attempts"]
            )
            for label, source in pair.get("partial_evidence", {}).items():
                lines.append(
                    f"partial {label}: {source.get('version', '')} {source['analysis']}"
                )
                lines.extend(source["urls"])
        for row in sorted(blocked_results.get(key, pair["results"]), key=identity):
            lines.append(
                f"- `{row['file']}:{row['line']}` `{row['name']}` "
                f"{row['status']}; {row['migration']}; {row['reason']}; "
                f"usage={len(row['usage'])}"
            )
            for finding in row["findings"]:
                require(
                    finding["kind"] in ("issue", "update"), "Invalid finding category"
                )
                lines.append(f"  {finding['kind']}: {finding['detail']}")
        lines.append("")
    lines.extend(["## Source files", "", *state["files"]])
    return "\n".join(lines)


def evidence_bytes(state, allow_blocked=False):
    records, _ = finalized_records(state, allow_blocked)
    document = {
        "schema_version": 1,
        "summary": summary(state, records),
        "state": state,
        "results": records,
    }
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def finalize_evidence(state, directory, allow_blocked=False):
    """別途許可されたpushの前に詳細根拠をすべて保存する。"""
    data = evidence_bytes(state, allow_blocked)
    report = render(state, allow_blocked).encode("utf-8")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evidence.json").write_bytes(data)
    (directory / "report.md").write_bytes(report)
    require(
        (directory / "evidence.json").read_bytes() == data, "Evidence readback mismatch"
    )
    require(
        (directory / "report.md").read_bytes() == report, "Report readback mismatch"
    )
    return {
        "evidence_sha256": hashlib.sha256(data).hexdigest(),
        "report_sha256": hashlib.sha256(report).hexdigest(),
    }


def published_file(url):
    """公開repositoryのcommit固定ファイルを認証なしで読む。"""
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    require(
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and not parsed.query
        and not parsed.fragment
        and len(parts) >= 5
        and parts[2] == "blob"
        and re.fullmatch(r"[0-9a-f]{40}", parts[3]),
        "Publication needs an immutable GitHub commit/file URL",
    )
    owner, repository, _, commit, *path_parts = parts
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner)
        and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", repository),
        "Invalid GitHub repository",
    )
    path = unquote("/".join(path_parts))
    require(
        path and all(part not in ("", ".", "..") for part in path.split("/")),
        "Invalid published file path",
    )
    require(
        url
        == (
            f"https://github.com/{owner}/{repository}/blob/{commit}/"
            f"{quote(path, safe='/')}"
        ),
        "Publication needs a canonical immutable GitHub file URL",
    )
    raw_url = (
        f"https://raw.githubusercontent.com/{owner}/{repository}/{commit}/"
        f"{quote(path, safe='/')}"
    )
    request = urllib.request.Request(
        raw_url, headers={"User-Agent": "bicep-api-audit/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            require(
                response.status == 200,
                f"Published file HTTP {response.status}: {raw_url}",
            )
            require(
                response.geturl() == raw_url,
                "Published file redirected from pinned URL",
            )
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise AuditError(f"Published file HTTP {exc.code}: {raw_url}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuditError(
            f"Published file retrieval failed ({type(exc).__name__}): {raw_url}"
        ) from None
    return (owner, repository, commit), data


def prepare_issue(
    state, title, directory, *, evidence_url=None, report_url=None, allow_blocked=False
):
    """URL指定前はファイルだけを保存し、公開内容の一致確認後にpayloadを作る。"""
    directory = Path(directory)
    for name in ("issue-payload.json", "issue-body-sha256.json"):
        (directory / name).unlink(missing_ok=True)
    require(
        bool(evidence_url) == bool(report_url), "Both published file URLs are required"
    )
    hashes = finalize_evidence(state, directory, allow_blocked)
    if evidence_url is None:
        return hashes
    evidence_commit, published = published_file(evidence_url)
    report_commit, published_report = published_file(report_url)
    require(
        evidence_commit == report_commit,
        "Published files must share repository and commit",
    )
    require(
        published == (directory / "evidence.json").read_bytes(),
        "Published evidence differs from local evidence bytes",
    )
    require(
        published_report == (directory / "report.md").read_bytes(),
        "Published report differs from local report bytes",
    )
    counts = json.loads(published)["summary"]
    body = "\n".join(
        [
            "# Bicep API version check-only",
            "",
            "確認完了。"
            if counts["complete"]
            else "確認未完了。公開根拠不足の対象を含む。",
            f"対象commit: `{state['commit']}`",
            f"REST commit: `{state['specs_commit']}`",
            f"{counts['files']} files / {counts['declarations']} declarations / {counts['pairs']} pairs",
            f"verified={counts['verified']}, excluded={counts['excluded']}, unverified={counts['unverified']}",
            f"issue={counts['issue']}, update={counts['update']}",
            f"[詳細JSON]({evidence_url})",
            f"evidence SHA-256: `{hashes['evidence_sha256']}`",
            f"[人間向けレポート]({report_url})",
            f"report SHA-256: `{hashes['report_sha256']}`",
            "",
            "無効の手動移行PoCの記録。ジョブ終了やHTTP成功だけでは仕様照合完了を示さない。",
        ]
    )
    require(len(body.encode("utf-8")) <= 6000, "Summary exceeds bounded issue size")
    payload = {"title": title, "body": body}
    save_json(directory / "issue-payload.json", payload)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    save_json(directory / "issue-body-sha256.json", {"sha256": digest})
    return payload


def verify_issue(payload, actual):
    require(
        actual["body"] == payload["body"], "Published issue differs from frozen payload"
    )
    require(
        actual["title"] == payload["title"],
        "Published title differs from frozen payload",
    )
    return hashlib.sha256(actual["body"].encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--root", required=True)
    initialize.add_argument("--commit", required=True)
    initialize.add_argument("--specs-commit", required=True)
    initialize.add_argument("--state", required=True)
    pending = commands.add_parser("next")
    pending.add_argument("--state", required=True)
    pending.add_argument("--output", required=True)
    advance = commands.add_parser("record")
    advance.add_argument("--state", required=True)
    advance.add_argument("--pair", required=True)
    advance.add_argument("--assessment", required=True)
    advance.add_argument("--cache", required=True)
    blocked = commands.add_parser("block")
    blocked.add_argument("--state", required=True)
    blocked.add_argument("--pair", required=True)
    blocked.add_argument("--assessment", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--state", required=True)
    prepare.add_argument("--title", required=True)
    prepare.add_argument("--directory", required=True)
    prepare.add_argument("--allow-blocked", action="store_true")
    prepare.add_argument("--evidence-url")
    prepare.add_argument("--report-url")
    verify = commands.add_parser("verify-issue")
    verify.add_argument("--payload", required=True)
    verify.add_argument("--actual", required=True)
    args = parser.parse_args()

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    if args.command == "init":
        head = subprocess.check_output(
            [executable("git"), "rev-parse", "HEAD"], cwd=args.root, text=True
        ).strip()
        require(head == args.commit, "Target commit differs from checkout HEAD")
        changes = subprocess.check_output(
            [
                executable("git"),
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "*.bicep",
            ],
            cwd=args.root,
            text=True,
        )
        require(not changes.strip(), "Bicep sources differ from pinned checkout")
        state = new_state(inventory(args.root), args.commit, args.specs_commit)
        save_json(args.state, state)
    elif args.command == "next":
        save_json(args.output, assessment_template(read(args.state)))
    elif args.command in ("record", "block"):
        state, assessment = read(args.state), read(args.assessment)
        if args.command == "record":
            record_pair(
                state,
                args.pair,
                assessment["results"],
                assessment["evidence"],
                Fetcher(args.cache),
            )
        else:
            block_pair(
                state,
                args.pair,
                assessment["attempts"],
                assessment["reason"],
                partial_results=assessment.get("partial_results"),
                partial_evidence=assessment.get("partial_evidence"),
            )
        save_json(args.state, state)
    elif args.command == "prepare":
        result = prepare_issue(
            read(args.state),
            args.title,
            args.directory,
            evidence_url=args.evidence_url,
            report_url=args.report_url,
            allow_blocked=args.allow_blocked,
        )
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(verify_issue(read(args.payload), read(args.actual)))


if __name__ == "__main__":
    main()
