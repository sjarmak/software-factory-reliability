#!/usr/bin/env python3
"""factory-check: validate, review, and render factory reliability contracts.

Subcommands:
  init       write a commented starter factory.yaml
  validate   schema-validate contract, guarantee, campaign, and manifest files
  review     run the semantic rule catalog over one contract
  render     produce diagrams and tables from one contract
  infer      derive a contract from a real installation, with call-site evidence
  probes-init  scaffold a probe pack by reading an installation
  reconcile  compare a hand-written contract against what the installation shows

Schemas are located relative to this script, not the working directory.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import infer as infer_mod
import probe_scaffold as scaffold_mod  # noqa: E402
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


def _derive(args, notes=None):
    probes = infer_mod.load_probes(args.probes)
    return infer_mod.derive(args.installation, probes, notes)


def _print_scan_notes(notes):
    """A filter the scan was asked for and could not apply is printed, never
    swallowed. The alternative is a report whose scope claim is wrong in the
    reassuring direction: it looks like a clean scan of source, and it is a
    scan of source plus every log the installation writes."""
    for note in notes:
        print("SCAN  %s" % note)


def _emit_derived_yaml(contract, evidence):
    """Render the derived contract with its provenance as YAML comments.

    Provenance cannot live in the document body: the effect schema sets
    additionalProperties false, and a generated file that fails validation is
    worse than no generated file. Comments carry it, and out/evidence.json
    carries it in a form a script can read.
    """
    lines = [
        "# Derived by factory-check infer. Do not hand-edit: rerun the command.",
        "#",
        "# Every value below was read off the installation. A value that reads",
        "# unknown was looked for and not established, and the reason is on the",
        "# line above it. Editing an unknown to a decided value here changes the",
        "# score and changes nothing about the installation, which is the exact",
        "# move factory-check reconcile exists to catch.",
        "",
        "version: factory.reliability/v1",
        "",
        "factory:",
        "  name: %s" % contract["factory"]["name"],
        "",
        "effects:",
    ]
    by_name = {e.name: e for e in evidence}
    for effect in contract["effects"]:
        item = by_name.get(effect["name"])
        lines.append("  # %s" % effect.pop("_reason"))
        if item is not None and item.sites:
            shown = item.sites[:3]
            for site in shown:
                lines.append("  #   %s:%d" % (site.path, site.line))
            if len(item.sites) > len(shown):
                lines.append("  #   ... %d more call sites"
                             % (len(item.sites) - len(shown)))
        lines.append("  - name: %s" % effect["name"])
        lines.append("    destination: %s" % effect["destination"])
        lines.append("    effect_identity: %s" % effect["effect_identity"])
        lines.append("    # not derivable from call sites: how the destination"
                     " behaves on a repeat")
        lines.append("    retry_contract: %s" % effect["retry_contract"])
        lines.append("    # not derivable from call sites: a decision, not code")
        lines.append("    unknown_state_policy: %s" % effect["unknown_state_policy"])
    return "\n".join(lines) + "\n"


def cmd_infer(args):
    notes = []
    contract, evidence = _derive(args, notes)
    _print_scan_notes(notes)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = sum(len(e.sites) for e in evidence)
    print("scanned %d files, found %d call site(s) across %d effect(s)"
          % (contract.pop("_scanned_files"), total, len(evidence)))
    for item in evidence:
        value, reason = item.derived_identity()
        mark = "ok  " if value != "unknown" else "MISS"
        print("  %s %-22s %2d site(s), identity %s (%s)"
              % (mark, item.name, len(item.sites), value, reason))
        for site in item.missing_identity:
            print("         no %s: %s:%d" % (item.identity_name, site.path, site.line))
        # Printed under their own heading, with the matched text, because the
        # reader's job on these is to decide whether each one is an invocation
        # at all -- which the location alone cannot tell them.
        for site in item.unclassified:
            print("         review (%s): %s:%d  %s"
                  % (site.set_aside, site.path, site.line, site.text[:70]))

    evidence_path = out_dir / "evidence.json"
    evidence_path.write_text(json.dumps(
        [
            {
                "effect": e.name,
                "destination": e.destination,
                "identity_name": e.identity_name,
                "derived_identity": e.derived_identity()[0],
                "reason": e.derived_identity()[1],
                "sites": [vars(s) for s in e.sites],
            }
            for e in evidence
        ], indent=2) + "\n")
    print("wrote %s" % evidence_path)

    target = Path(args.write) if args.write else out_dir / "factory.derived.yaml"
    target.write_text(_emit_derived_yaml(contract, evidence))
    print("wrote %s" % target)
    return 0


def cmd_probes_init(args):
    """Scaffold a probe pack by reading an installation.

    The gap this closes is the one that makes the kit unusable by anyone who
    did not write it: every command downstream needs a probe pack, and writing
    one from a blank page requires knowing both this file format and every
    place your own factory reaches outside itself.
    """
    excludes = list(scaffold_mod._DEFAULT_EXCLUDES)
    excludes.extend("**/%s/**" % e.strip("/") for e in (args.exclude or []))
    scan = {
        "include_globs": ["**"],
        "exclude_globs": excludes,
        "prune_nested_repos": not args.include_nested_repos,
        # A path the installation's own VCS ignores is one it declares to be
        # generated output, so the command in it was RECORDED, not run.
        "respect_vcs_ignore": not args.scan_ignored_paths,
    }
    notes = []
    found, scanned = scaffold_mod.survey(args.installation, scan, notes)
    _print_scan_notes(notes)
    if not found:
        print("read %d file(s) and found no call sites from the built-in "
              "catalog." % scanned)
        print("That is a finding, not a success: either this installation "
              "performs no external effects,")
        print("or it performs them in a way the catalog does not know. Check "
              "the second before believing the first.")
        return 1

    print("read %d file(s); found %d effect class(es)" % (scanned, len(found)))
    for name, item in found.items():
        print("  %-22s %4d call site(s) in %3d file(s)"
              % (name, item["total"], len(item["paths"])))

    print("")
    print("call sites by top-level directory:")
    for top, hits in scaffold_mod.directory_distribution(found)[:12]:
        print("  %-24s %4d" % (top, hits))
    print("")
    print("Directories your factory WRITES (state, logs, generated reports) "
          "will show up here")
    print("alongside the ones it RUNS, and the same command is a call site in "
          "one and a")
    print("description of one in the other. Re-run with --exclude <dir> for "
          "each that is output.")

    target = Path(args.write)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(scaffold_mod.render(args.installation, found, scanned))
    print("")
    print("wrote %s" % target)
    print("Every identity in it reads unknown, so `infer` will withdraw all of "
          "them until you")
    print("decide each one. That is the intended starting state, not an error.")
    return 0


# An effect_identity that is a bare identifier is being USED as a key: it is
# the token a call site would carry. Anything with a space, a comma or a
# sentence in it is a human describing a composite identity, and comparing that
# to a probe's token with == is a vocabulary error, not a measurement.
#
# This is a syntactic test, not a judgement about meaning, and both ways of
# being wrong are safe. Prose mistaken for a key reads DRIFT, which is loud and
# whose fix is to name the key. A key mistaken for prose reads UNVERIFIED and
# prints the exact line to add. Neither can produce a false CONFIRMED, which is
# the only direction this tool is never allowed to be wrong in.
# The schema and the runtime share one grammar. `$` is written for the
# schema, and Python's `$` also matches just before a trailing newline --
# so a key of "idempotency_key\n" would pass the shape test, get stripped,
# and confirm. \Z is the end of the string and nothing else.
_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]*$"
_KEY_SHAPED = re.compile(_KEY_PATTERN[:-1] + r"\Z")


def _comparable_key(effect, claimed):
    """The token to compare against the probe's identity, or None.

    None means the contract has not named one: it described the identity in
    prose and did not set effect_identity_key. There is nothing to compare, and
    saying so is the honest answer -- reporting DRIFT there accuses the
    installation of something the contract never claimed.
    """
    explicit = effect.get("effect_identity_key")
    if isinstance(explicit, str) and _KEY_SHAPED.match(explicit):
        return explicit
    if isinstance(claimed, str) and _KEY_SHAPED.match(claimed):
        return claimed
    return None


def _identity_conflict(effect, claimed):
    """The contract naming two different identities, or None.

    effect_identity_key exists so a composite identity can stay in prose and
    still be checkable. It is NOT an override. When effect_identity is itself a
    bare token, it already names a key, and a different effect_identity_key
    beside it means the contract says two things -- which used to read DRIFT on
    the prose value and would otherwise now read CONFIRMED on the key, turning
    a new field into a way to declare yourself correct.
    """
    explicit = effect.get("effect_identity_key")
    if not (isinstance(explicit, str) and _KEY_SHAPED.match(explicit)):
        return None
    if not (isinstance(claimed, str) and _KEY_SHAPED.match(claimed)):
        return None
    if claimed == explicit:
        return None
    return ("the contract names two identities: effect_identity %s and "
            "effect_identity_key %s" % (claimed, explicit))


def _with_residual(reason, residual):
    """Never report a code-lane confirmation without its unbindable residual.

    A confirmation that omits the instructed sites is a claim of a static
    guarantee that does not exist: no edit to the code binds a sentence in a
    prompt. The count travels with the confirmation so the reader cannot get
    one without the other.
    """
    if not residual:
        return reason
    return ("%s; %d further call site(s) are agent instructions and no static "
            "marker can bind them" % (reason, residual))


def cmd_reconcile(args):
    """Report every place the declaration claims more than the installation shows.

    This is the rule that makes the derivation load-bearing. A hand-written
    contract may legitimately declare things no scan can see, and those are
    reported as unverifiable rather than as drift. What it may not do is
    declare an effect identity the call sites do not carry.
    """
    declared_doc = _load_valid_contract(args.file)
    if declared_doc is None:
        return 2
    notes = []
    _, evidence = _derive(args, notes)
    _print_scan_notes(notes)
    by_name = {e.name: e for e in evidence}

    declared_effects = declared_doc.get("effects") or []
    drift = []
    unverifiable = []
    confirmed = []
    confirmed_on_key = []
    open_items = []
    for effect in declared_effects:
        if not isinstance(effect, dict):
            continue
        name = effect.get("name")
        item = by_name.get(name)
        claimed = effect.get("effect_identity")
        if item is None:
            unverifiable.append(
                (name, "no probe covers this effect; the derivation is blind to it"))
            continue
        derived, reason = item.derived_identity()
        # The reconciler asks a different question from the validator. The
        # validator asks whether the effect can be statically guaranteed;
        # derived_identity answers that, and answers "unknown" whenever one
        # agent-instruction site exists. The reconciler asks whether the
        # DECLARATION matches the installation, and folding those together made
        # every declared identity read as drift on an agent-driven factory --
        # five effects in five different situations, one verdict. The code lane
        # is compared here; the unbindable residual is carried alongside it and
        # printed, never dropped.
        code_derived, code_reason, residual = item.code_lane_identity()
        claimed_key = _comparable_key(effect, claimed)
        conflict = _identity_conflict(effect, claimed)
        if claimed in (None, "unknown"):
            if derived != "unknown":
                unverifiable.append(
                    (name, "installation supports effect_identity %s but the "
                           "contract leaves it unknown" % derived))
            else:
                # Undecided on both sides. Reporting nothing here made the
                # counts silently smaller than the number of declared effects,
                # so a reader could not tell an effect that was checked and
                # left open from one the report simply never reached.
                open_items.append((name, reason))
            continue
        if conflict:
            drift.append((name, claimed, conflict))
            continue
        if code_derived == "unknown":
            if not (item.scripted or item.fenced) and residual:
                # No code performs the effect at all: every site is an agent
                # instruction. That is not the contract claiming more than the
                # installation shows, it is the installation putting the effect
                # somewhere no scan can reach -- a different problem, needing a
                # different fix, and it read as drift.
                unverifiable.append((name, code_reason))
            else:
                drift.append((name, claimed, code_reason))
        elif claimed_key is None:
            # Reachable only once the code lane derives a DECIDED identity. A
            # prose contract over call sites that carry no marker at all stays
            # DRIFT above, and that is right: the installation is missing the
            # identity whatever vocabulary the contract used, and the vocabulary
            # question does not arise until there is something to compare.
            #
            # The code lane carries a decided identity and the contract
            # describes its own in prose. Those are two vocabularies, and the
            # equality that used to run here could never match -- so on exactly
            # the effects whose identity is interesting enough to need a
            # sentence, fixing the last unmarked call site did not move the
            # verdict. That is the failure this tool exists to catch, in the
            # tool.
            unverifiable.append(
                (name,
                 "the contract describes its identity in prose and does not "
                 "name a key; the call sites carry %s, and the two cannot be "
                 "compared. Add effect_identity_key: %s if that is what the "
                 "prose means." % (code_derived, code_derived)))
        elif claimed_key != code_derived:
            # Two decided values that disagree is the sharpest drift there is:
            # the contract names an identity the call sites do not carry, and
            # without this branch it read as confirmed.
            drift.append(
                (name, claimed_key,
                 "call sites carry %s, not %s" % (code_derived, claimed_key)))
        else:
            confirmed.append(
                (name, claimed_key, _with_residual(code_reason, residual)))
            if claimed_key != claimed:
                confirmed_on_key.append(name)

    # Effects the installation performs and the contract never mentions are
    # kept OUT of the drift bucket. Folding them in inflated drift past the
    # number of declared effects, which broke the accounting the summary
    # promises: the four buckets are a partition of what was DECLARED, and an
    # undeclared effect is by definition not in that set.
    probed_names = {e.name for e in evidence}
    undeclared = []
    for name in sorted(
            probed_names
            - {e.get("name") for e in declared_effects if isinstance(e, dict)}):
        item = by_name[name]
        if item.sites:
            undeclared.append(
                (name, "%d call site(s) found in the installation, and the "
                       "contract does not mention it" % len(item.sites)))

    for name, claimed, reason in drift:
        print("DRIFT  %s: contract says effect_identity %s; installation says %s"
              % (name, claimed, reason))
    for name, note in unverifiable:
        print("UNVERIFIED  %s: %s" % (name, note))
    for name, claimed, reason in confirmed:
        print("CONFIRMED  %s: effect_identity %s, %s" % (name, claimed, reason))
    for name in confirmed_on_key:
        # A static scan can check that every call site carries the token the
        # contract named. It cannot check that the token's runtime VALUE is the
        # identity the prose describes -- "a fresh execution nonce" keyed as
        # idempotency_key confirms here and is unstable across retries. Say what
        # was and was not checked rather than let the word CONFIRMED carry a
        # claim nobody measured.
        print("           %s: confirmed on the named key; whether that key's "
              "value is the identity the prose describes is not statically "
              "checkable" % name)
    for name, reason in open_items:
        print("OPEN  %s: undecided in the contract and %s" % (name, reason))
    for name, reason in undeclared:
        print("UNDECLARED  %s: %s" % (name, reason))
    print("%d drift, %d unverified, %d confirmed, %d open (of %d declared)"
          % (len(drift), len(unverifiable), len(confirmed), len(open_items),
             len([e for e in declared_effects if isinstance(e, dict)])))
    if undeclared:
        print("%d effect(s) performed but never declared" % len(undeclared))
    return 1 if drift or undeclared else 0


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

    p_infer = sub.add_parser(
        "infer", help="derive a contract from a real installation")
    p_infer.add_argument("installation", help="root directory of the installation")
    p_infer.add_argument("--probes", required=True, help="probe pack YAML")
    p_infer.add_argument("--out", default="out", help="directory for evidence.json")
    p_infer.add_argument("--write", default=None,
                         help="path for the derived contract "
                              "(default: <out>/factory.derived.yaml)")
    p_infer.set_defaults(func=cmd_infer)

    p_pinit = sub.add_parser(
        "probes-init",
        help="scaffold a probe pack by reading an installation")
    p_pinit.add_argument("installation", help="root directory of the installation")
    p_pinit.add_argument("--write", default="probes.yaml",
                         help="path for the generated probe pack")
    p_pinit.add_argument("--exclude", action="append", default=[],
                         help="directory name to skip; repeatable")
    p_pinit.add_argument("--include-nested-repos", action="store_true",
                         help="do not skip subdirectories that are themselves "
                              "git repositories")
    p_pinit.add_argument("--scan-ignored-paths", action="store_true",
                         help="read paths the installation's own VCS ignores; "
                              "these are usually logs and generated reports in "
                              "which the command was recorded, not run")
    p_pinit.set_defaults(func=cmd_probes_init)

    p_recon = sub.add_parser(
        "reconcile",
        help="compare a hand-written contract against the installation")
    p_recon.add_argument("file", help="hand-written factory contract")
    p_recon.add_argument("installation", help="root directory of the installation")
    p_recon.add_argument("--probes", required=True, help="probe pack YAML")
    p_recon.set_defaults(func=cmd_reconcile)

    p_render = sub.add_parser("render", help="render diagrams and tables")
    p_render.add_argument("file", help="factory contract to render")
    p_render.add_argument("--out", default="out", help="directory for rendered files")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
