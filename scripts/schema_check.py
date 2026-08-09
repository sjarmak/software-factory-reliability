#!/usr/bin/env python3
"""Schema conformance checker.

Validates:
  - every *.yaml under examples/ against schemas/factory.schema.json, except
    files named campaign.yaml (schemas/campaign.schema.json) and files under
    a guarantees/ directory (schemas/guarantee.schema.json)
  - fixtures/*/campaign.yaml against schemas/campaign.schema.json
  - fixtures/**/work-manifests/*.json against schemas/work-manifest.schema.json
  - observability/sample-events.jsonl parses as JSONL and every event carries
    the identity fields (work_id, a timestamp key, an event-kind key)

Prints one PASS/FAIL line per file. Exit status 1 on any failure.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schemas"

try:
    import yaml
except ImportError:
    print("FAIL setup: PyYAML is required (pip install pyyaml)")
    sys.exit(1)

try:
    import jsonschema
except ImportError:
    print("FAIL setup: jsonschema is required (pip install jsonschema)")
    sys.exit(1)

TIME_KEYS = ("ts", "timestamp", "time")
EVENT_KEYS = ("event", "event_type", "kind", "type")
MAX_ERRORS_SHOWN = 3

_schema_cache = {}


def rel(path):
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_schema(name, results):
    path = SCHEMA_DIR / name
    if name in _schema_cache:
        return _schema_cache[name]
    if not path.exists():
        results.append((False, rel(path), "schema file missing"))
        _schema_cache[name] = None
        return None
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        results.append((False, rel(path), f"schema unreadable: {err}"))
        _schema_cache[name] = None
        return None
    _schema_cache[name] = schema
    return schema


def validate_docs(path, schema_name, results):
    schema = load_schema(schema_name, results)
    if schema is None:
        results.append((False, rel(path), f"skipped: {schema_name} unavailable"))
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as err:
        results.append((False, rel(path), f"unreadable: {err}"))
        return
    try:
        docs = [d for d in yaml.safe_load_all(raw) if d is not None]
    except yaml.YAMLError as err:
        results.append((False, rel(path), f"YAML parse error: {err}"))
        return
    if not docs:
        results.append((False, rel(path), "empty document"))
        return

    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors = []
    for doc in docs:
        for err in validator.iter_errors(doc):
            where = "/".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"{where}: {err.message}")
    if errors:
        shown = "; ".join(errors[:MAX_ERRORS_SHOWN])
        if len(errors) > MAX_ERRORS_SHOWN:
            shown += f"; and {len(errors) - MAX_ERRORS_SHOWN} more"
        results.append((False, rel(path), f"against {schema_name}: {shown}"))
    else:
        results.append((True, rel(path), f"valid against {schema_name}"))


def validate_json_file(path, schema_name, results):
    schema = load_schema(schema_name, results)
    if schema is None:
        results.append((False, rel(path), f"skipped: {schema_name} unavailable"))
        return
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        results.append((False, rel(path), f"JSON parse error: {err}"))
        return
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors = []
    for err in validator.iter_errors(doc):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{where}: {err.message}")
    if errors:
        shown = "; ".join(errors[:MAX_ERRORS_SHOWN])
        if len(errors) > MAX_ERRORS_SHOWN:
            shown += f"; and {len(errors) - MAX_ERRORS_SHOWN} more"
        results.append((False, rel(path), f"against {schema_name}: {shown}"))
    else:
        results.append((True, rel(path), f"valid against {schema_name}"))


def schema_name_for_example(path):
    relative = path.relative_to(REPO_ROOT / "examples")
    if path.name == "campaign.yaml":
        return "campaign.schema.json"
    if "guarantees" in relative.parts[:-1]:
        return "guarantee.schema.json"
    return "factory.schema.json"


def check_examples(results):
    examples_dir = REPO_ROOT / "examples"
    if not examples_dir.exists():
        results.append((False, "examples/", "directory missing"))
        return
    files = sorted(examples_dir.rglob("*.yaml")) + sorted(examples_dir.rglob("*.yml"))
    if not files:
        results.append((False, "examples/", "no YAML files found"))
        return
    for path in files:
        validate_docs(path, schema_name_for_example(path), results)


def check_fixture_campaigns(results):
    fixtures_dir = REPO_ROOT / "fixtures"
    if not fixtures_dir.exists():
        results.append((False, "fixtures/", "directory missing"))
        return
    files = sorted(fixtures_dir.glob("*/campaign.yaml"))
    if not files:
        results.append((False, "fixtures/*/campaign.yaml", "no files found"))
        return
    for path in files:
        validate_docs(path, "campaign.schema.json", results)


def check_work_manifests(results):
    fixtures_dir = REPO_ROOT / "fixtures"
    if not fixtures_dir.exists():
        return
    files = sorted(fixtures_dir.rglob("work-manifests/*.json"))
    if not files:
        results.append((False, "fixtures/**/work-manifests/*.json", "no files found"))
        return
    for path in files:
        validate_json_file(path, "work-manifest.schema.json", results)


def check_sample_events(results):
    path = REPO_ROOT / "observability" / "sample-events.jsonl"
    if not path.exists():
        results.append((False, "observability/sample-events.jsonl", "file missing"))
        return
    problems = []
    count = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError as err:
            problems.append(f"line {lineno}: not valid JSON ({err})")
            continue
        if not isinstance(event, dict):
            problems.append(f"line {lineno}: event is not an object")
            continue
        if "work_id" not in event:
            problems.append(f"line {lineno}: missing work_id")
        if not any(k in event for k in TIME_KEYS):
            problems.append(f"line {lineno}: missing timestamp key ({'/'.join(TIME_KEYS)})")
        if not any(k in event for k in EVENT_KEYS):
            problems.append(f"line {lineno}: missing event-kind key ({'/'.join(EVENT_KEYS)})")
    if count == 0:
        problems.append("no events found")
    if problems:
        shown = "; ".join(problems[:MAX_ERRORS_SHOWN])
        if len(problems) > MAX_ERRORS_SHOWN:
            shown += f"; and {len(problems) - MAX_ERRORS_SHOWN} more"
        results.append((False, rel(path), shown))
    else:
        results.append((True, rel(path), f"{count} events, identity fields present"))


def main():
    results = []
    check_examples(results)
    check_fixture_campaigns(results)
    check_work_manifests(results)
    check_sample_events(results)

    failed = 0
    for ok, name, detail in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"{status} {name}: {detail}")
    print(f"TOTAL: {len(results) - failed} pass, {failed} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
