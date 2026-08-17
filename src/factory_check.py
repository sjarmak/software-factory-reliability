#!/usr/bin/env python3
"""factory-check: validate, review, and render factory reliability contracts.

Subcommands:
  init      write a commented starter factory.yaml
  validate  schema-validate contract, guarantee, campaign, and manifest files
  review    run the semantic rule catalog over one contract
  render    produce diagrams and tables from one contract

Schemas are located relative to this script, not the working directory.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import render as render_mod  # noqa: E402
import rules as rules_mod  # noqa: E402

SCHEMA_DIR = SCRIPT_DIR.parent / "schemas"

STARTER_CONTRACT = """\
# Starter factory reliability contract, written by factory-check init.
# Replace each "unknown" with a decided value. The review command reads
# "unknown" and absent sections as explicit findings, never as silent
# passes, so this file is a worklist as much as a contract.

version: factory.reliability/v1

factory:
  name: starter-factory

# Three identity classes stay distinct: the logical work item is stable for
# the item's whole life, a retry mints a new attempt identity, and the
# executor session can outlive the attempt that launched it.
work:
  logical_identity: work_id
  attempt_identity: attempt_id      # never key durable external state on this
  session_identity: session_id
  ownership:
    generation: claim_generation    # monotonic; fences stale writers after a reclaim
    lease_expiry: claim_expires_at  # bounds how long a dead owner blocks progress
    fence:
      enforced_by: unknown          # decide a destination-side enforcer: publisher, destination, or store
      operation: unknown            # decide compare-and-set or transactional

# The artifact chain: an immutable identity, verification bound to that same
# identity, and publication conditions rechecked atomically at the destination.
artifacts:
  identity: commit_sha

# One entry per class of external mutation. State how the destination behaves
# when the same logical effect arrives twice, and what the factory does when
# an attempt's outcome cannot be determined.
effects:
  - name: open_change_request
    destination: code_host
    effect_identity: change_request_key  # stable across retries; never attempt-scoped
    retry_contract: unknown              # decide deduplicate, converge, or reconcile
    unknown_state_policy: unknown        # decide block_and_escalate, reconcile_then_block, or manual_review

# Sections still to declare: authorities, reconciliation, scheduling,
# code_estate, campaigns, observability. Each omission produces a
# not-declared finding in review; omission means "not yet decided".
"""


def _load_yaml(path):
    return yaml.safe_load(Path(path).read_text())


def _pick_schema_name(doc):
    if isinstance(doc, dict):
        if doc.get("version") == "factory.reliability/v1" or "factory" in doc:
            return "factory.schema.json"
        if "campaign_id" in doc and "targets" in doc:
            return "campaign.schema.json"
        if "claim" in doc and "oracle" in doc:
            return "guarantee.schema.json"
        if "work_id" in doc and "attempt_id" in doc:
            return "work-manifest.schema.json"
        if "effect_identity" in doc and "destination" in doc:
            return "effect.schema.json"
    return "factory.schema.json"


def _unschemad_type(doc):
    """Name the document type when this tool recognizes it but has no schema
    for it, else None.

    A code estate describes repositories and symbols; it is an input to
    planning, not a promise the factory makes, and schemas/ has no estate
    schema. Naming it beats checking it against a contract schema it was
    never written to satisfy and reporting the mismatch as a defect.
    """
    if isinstance(doc, dict) and "estate" in doc and "repositories" in doc:
        return "code estate"
    return None


def _validator_for(schema_name, cache):
    if schema_name not in cache:
        schema = json.loads((SCHEMA_DIR / schema_name).read_text())
        cache[schema_name] = Draft202012Validator(
            schema, format_checker=FormatChecker())
    return cache[schema_name]


def _validate_one(path, cache):
    """Validate one file; print the outcome and return its status.

    Status is one of "ok", "invalid", or "skipped". Skipped is a document
    type this tool recognizes and has no schema for; it is reported as
    itself rather than folded into either of the other two.
    """
    try:
        doc = _load_yaml(path)
    except (OSError, yaml.YAMLError) as exc:
        print(f"{path}: unreadable or not valid YAML: {exc}")
        return "invalid"
    unschemad = _unschemad_type(doc)
    if unschemad:
        print(f"{path}: SKIP ({unschemad}; schemas/ holds no schema for this "
              f"document type, so nothing was checked)")
        return "skipped"
    schema_name = _pick_schema_name(doc)
    validator = _validator_for(schema_name, cache)
    errors = sorted(validator.iter_errors(doc),
                    key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        print(f"{path}: INVALID against {schema_name}")
        for err in errors:
            location = ".".join(str(part) for part in err.absolute_path) or "(document root)"
            print(f"  at {location}: {err.message}")
        return "invalid"
    print(f"{path}: OK ({schema_name})")
    return "ok"


def cmd_init(args):
    if args.target:
        target = Path(args.target)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "factory.yaml"
    if target.exists():
        print(f"{target}: already exists; refusing to overwrite")
        return 1
    target.write_text(STARTER_CONTRACT)
    print(f"wrote {target}")
    return 0


def cmd_validate(args):
    cache = {}
    statuses = [_validate_one(Path(f), cache) for f in args.files]
    skipped = statuses.count("skipped")
    if skipped:
        print(f"{skipped} file(s) skipped: no schema exists for their "
              f"document type, so this run makes no claim about them")
    return 1 if "invalid" in statuses else 0


def _load_valid_contract(file_arg):
    """Load a contract and require factory-schema validity; return doc or None."""
    path = Path(file_arg)
    try:
        doc = _load_yaml(path)
    except (OSError, yaml.YAMLError) as exc:
        print(f"{path}: unreadable or not valid YAML: {exc}")
        return None
    validator = _validator_for("factory.schema.json", {})
    errors = list(validator.iter_errors(doc))
    if errors:
        print(f"{path}: not a valid factory contract; run validate for details")
        for err in errors[:5]:
            location = ".".join(str(part) for part in err.absolute_path) or "(document root)"
            print(f"  at {location}: {err.message}")
        return None
    return doc


def cmd_review(args):
    doc = _load_valid_contract(args.file)
    if doc is None:
        return 2
    findings = rules_mod.review(doc)
    ordered = sorted(enumerate(findings),
                     key=lambda pair: (0 if pair[1].severity == "FAIL" else 1, pair[0]))
    ordered = [f for _, f in ordered]
    for finding in ordered:
        print(f"{finding.severity} {finding.rule}")
        print(f"  {finding.message}")
        print(f"  {finding.hint}")
    fail_count = sum(1 for f in ordered if f.severity == "FAIL")
    warn_count = len(ordered) - fail_count
    print(f"{fail_count} FAIL, {warn_count} WARN")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    findings_path = out_dir / "findings.json"
    findings_path.write_text(
        json.dumps([f.as_dict() for f in ordered], indent=2) + "\n")
    print(f"wrote {findings_path}")
    if fail_count or (args.strict and warn_count):
        return 1
    return 0


def cmd_render(args):
    doc = _load_valid_contract(args.file)
    if doc is None:
        return 2
    written = render_mod.render_all(doc, Path(args.file), Path(args.out))
    for path in written:
        print(f"wrote {path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="factory-check",
        description="Validate, review, and render factory reliability contracts.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a commented starter factory.yaml")
    p_init.add_argument("target", nargs="?", default=None,
                        help="file to write (default: factory.yaml in --out)")
    p_init.add_argument("--out", default=".", help="directory to write factory.yaml into")
    p_init.set_defaults(func=cmd_init)

    p_validate = sub.add_parser("validate", help="schema-validate one or more files")
    p_validate.add_argument("files", nargs="+", help="YAML files to validate")
    p_validate.set_defaults(func=cmd_validate)

    p_review = sub.add_parser("review", help="run the semantic rule catalog")
    p_review.add_argument("file", help="factory contract to review")
    p_review.add_argument("--strict", action="store_true",
                          help="exit nonzero on WARN findings as well as FAIL")
    p_review.add_argument("--out", default="out", help="directory for findings.json")
    p_review.set_defaults(func=cmd_review)

    p_render = sub.add_parser("render", help="render diagrams and tables")
    p_render.add_argument("file", help="factory contract to render")
    p_render.add_argument("--out", default="out", help="directory for rendered files")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
