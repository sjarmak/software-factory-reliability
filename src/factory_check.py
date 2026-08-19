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
  cites      resolve the path:line references a contract makes about code

Schemas are located relative to this script, not the working directory.
"""

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import cites as cites_mod  # noqa: E402
import infer as infer_mod
import probe_scaffold as scaffold_mod  # noqa: E402
import render as render_mod  # noqa: E402
import rules as rules_mod  # noqa: E402

SCHEMA_DIR = SCRIPT_DIR.parent / "schemas"

STARTER_CONTRACT = """\
# Starter factory reliability contract, written by factory-check init.
# Replace each "unknown" with a decided value. review reads a declared
# "unknown" as an explicit finding, never as a silent pass, so this file is a
# worklist as much as a contract.
#
# An ABSENT section is not the same thing, and the difference does not run the
# way you would want. Several rule groups return early when their section is
# missing, so deleting a section can produce FEWER findings than declaring it
# undecided: measured on this starter, omitting campaigns gives 5 FAIL / 8
# WARN and declaring it gives 5 FAIL / 9 WARN. Do not read a short findings
# list as a clean bill of health.

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
    retry_contract: unknown              # deduplicate, converge, reconcile, at_least_once
    unknown_state_policy: unknown        # decide block_and_escalate, reconcile_then_block, or manual_review

# Sections still to declare: authorities, reconciliation, scheduling,
# code_estate, campaigns, observability. Most omissions produce a not-declared
# finding in review; campaigns and code_estate do not, per the note at the top
# of this file. Omission means "not yet decided", and it is not scored as one.
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


# The body fields of a derived effect, in emission order, each with the comment
# that explains why it reads as it does. Kept as data rather than a run of
# append() calls so the emitter can prove it covered every field derive()
# produced instead of quietly dropping the ones nobody remembered.
_DERIVED_EFFECT_FIELDS = [
    ("effect_identity", None),
    ("retry_contract",
     "not derivable from call sites: how the destination behaves on a repeat"),
    ("unknown_state_policy",
     "not derivable from call sites: a decision, not code"),
    ("code_lane_identity",
     "OBSERVATION, not a guarantee: what the code that performs this effect"
     " carries. Differs from effect_identity exactly when the line below is"
     " nonzero"),
    ("instructed_call_sites",
     "call sites that are prose telling an agent to run the command; nothing"
     " static can read an argument list that does not exist until run time"),
]


# The sections infer PRODUCES, and every other section in the schema with the
# reason it is left for hand-writing. Both halves are here because the claim the
# kit makes about itself -- "derived where it can be, hand-written only where it
# cannot" -- is otherwise a sentence in a README that nothing checks.
#
# What separates the two lists is what THIS TOOL DOES, not a claim about what
# is knowable. The scanner reads call sites, so the effects inventory is within
# reach; nothing here reads a scheduler config, a store schema, or a CI
# definition, so those sections are not produced.
#
# Two different reasons live in the second list and the entries say which.
# Some sections record a DECISION -- which system is the authority for durable
# facts -- and no amount of scanning recovers one; deriving it would mean
# guessing, which is the failure this kit exists to catch one layer up. Others
# hold fields a scanner genuinely could observe with probes this kit does not
# have yet. Writing "cannot be derived" over that second group would be the same
# overclaim the effect_identity field was carrying a week ago: a limit on the
# instrument dressed as a property of the thing measured.
#
# `effects` is in the first list and is still not derived whole. retry_contract
# and unknown_state_policy are fixed at unknown by construction, and the effect
# names, destinations, and identity markers come from the probe pack a human
# wrote. What is read off the installation is the CALL SITES and whether each
# scripted one carries the declared marker.
_DERIVED_SECTIONS = ("version", "factory", "effects")

_HAND_WRITTEN_SECTIONS = [
    ("work",
     "the field names your work store uses for the logical item, one attempt,"
     " and the executor session, plus how ownership is leased",
     "A scan can list the columns a store has. It cannot tell you which of them"
     " is the attempt identity -- that is what the column MEANS, and the store"
     " does not record meanings."),
    ("authorities",
     "which system is the authority for facts, for procedure, for policy, and"
     " for effects",
     "A decision about your architecture. Nothing in the tree records it, and"
     " two installations with identical files can have made it differently."),
    ("artifacts",
     "what identifies one artifact immutably, how verification runs are"
     " identified, and what is rechecked at publication",
     "The identity is a choice between a commit digest, a content hash, and a"
     " mutable reference; the publication conditions are a policy."),
    ("observability",
     "which lifecycle transitions you watch end to end",
     "An instrument's existence is visible. Whether it watches a transition"
     " END TO END is a claim about what it would catch, which only a test that"
     " breaks the transition can settle."),
    ("scheduling",
     "your execution pool's capacity, its scheduling classes, and how fairness"
     " is applied",
     "Capacity and fairness are policy. On some installations a scheduler"
     " config states them; on others they live in an operator's head, and a"
     " derivation that read the first case would report silence for the second"
     " as though the policy did not exist."),
    ("reconciliation",
     "how divergence between intended and actual state is detected and closed",
     "A procedure, not a call site."),
    ("campaigns",
     "when a campaign is complete",
     "A rule you choose. The schema accepts several styles precisely because"
     " installations disagree about it."),
    ("code_estate",
     "the repositories and trees this factory treats as its own",
     "Optional, and a matter of intent rather than of what happens to be"
     " checked out next to the contract."),
]


def _emit_derived_yaml(contract, evidence):
    """Render the derived contract with its provenance as YAML comments.

    Provenance cannot live in the document body: the effect schema sets
    additionalProperties false, and a generated file that fails validation is
    worse than no generated file. Comments carry it, and out/evidence.json
    carries it in a form a script can read.

    One exception, and it is deliberate: instructed_call_sites IS a schema field,
    because a reviewer has to be able to see that an identity holds only where
    code performs the effect, and a comment is not something a rule can read.

    This emitter names every field by hand, so a field added to derive() and not
    added here is dropped in silence -- which is how instructed_call_sites came
    out None in the first generated contract that had it. test_the_emitter_writes
    _every_field_derive_produces exists to make that a red test rather than a
    field nobody notices is missing.
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
        code_reason = effect.pop("_code_lane_reason", None)
        if code_reason:
            # The second lane's reason, always printed, even when it agrees.
            # Printing it only on disagreement would make its ABSENCE the signal
            # that the lanes agree, and an absent line is also what a tool that
            # never looked produces.
            lines.append("  # code lane: %s" % code_reason)
        if item is not None and item.sites:
            shown = item.sites[:3]
            for site in shown:
                lines.append("  #   %s:%d" % (site.path, site.line))
            if len(item.sites) > len(shown):
                lines.append("  #   ... %d more call sites"
                             % (len(item.sites) - len(shown)))
        lines.append("  - name: %s" % effect["name"])
        lines.append("    destination: %s" % effect["destination"])
        emitted = {"name", "destination"}
        for field, note in _DERIVED_EFFECT_FIELDS:
            if note:
                lines.append("    # %s" % note)
            lines.append("    %s: %s" % (field, effect[field]))
            emitted.add(field)
        # A field added to derive() and not added to the list above used to be
        # dropped in silence -- which is how instructed_call_sites came out None
        # in the first generated contract that had it. A test can only catch
        # that after someone writes the test; this catches it at the moment the
        # field is added, in the run that would have emitted the broken file.
        # Underscore keys are provenance for the comments and never body fields.
        expected = {k for k in effect if not k.startswith("_")}
        if expected != emitted:
            raise AssertionError(
                "the derived-contract emitter does not cover every field "
                "derive() produces: missing %s, unexpected %s. Add it to "
                "_DERIVED_EFFECT_FIELDS and to both schemas."
                % (sorted(expected - emitted) or "none",
                   sorted(emitted - expected) or "none"))

    # The same coverage discipline one level up, on SECTIONS. A schema that
    # gains a section this file has not classified stops the run here rather
    # than emitting a contract whose closing block quietly under-reports what
    # was left to the reader. Checked against the schema on disk, not against a
    # list kept beside it, because two hand-maintained lists agreeing with each
    # other is not evidence either agrees with the schema.
    schema = json.loads((SCHEMA_DIR / "factory.schema.json").read_text())
    # A root that describes its shape through $ref or allOf has no `properties`
    # here, and .get(..., {}) would turn that into "the schema has no sections"
    # -- a classification error reported against every section at once, when the
    # real fault is that this guard cannot read this schema. Say which it is.
    if not isinstance(schema.get("properties"), dict) or not schema["properties"]:
        raise AssertionError(
            "factory.schema.json has no top-level `properties` map, so the "
            "derived/hand-written classification cannot be checked against it. "
            "A root composed with $ref or allOf needs this guard taught to "
            "resolve it, not a default of {}.")
    schema_sections = set(schema["properties"])

    # A PARTITION, not a union. Union equality alone accepts a section listed in
    # BOTH lists: the totals then say 4 derived and 8 hand-written for an
    # 11-section schema, and the section is printed as homework the file claims
    # to have derived. Duplicates inside one list print the same entry twice.
    derived = list(_DERIVED_SECTIONS)
    handwritten = [n for n, _, _ in _HAND_WRITTEN_SECTIONS]
    for label, names in (("_DERIVED_SECTIONS", derived),
                         ("_HAND_WRITTEN_SECTIONS", handwritten)):
        if len(names) != len(set(names)):
            raise AssertionError(
                "%s lists a section twice: %s"
                % (label, sorted(n for n in set(names) if names.count(n) > 1)))
    both = set(derived) & set(handwritten)
    if both:
        raise AssertionError(
            "a section cannot be both derived and hand-written: %s"
            % sorted(both))
    classified = set(derived) | set(handwritten)
    if schema_sections != classified:
        raise AssertionError(
            "every schema section must be classified as derived or "
            "hand-written: unclassified %s, classified but not in the schema "
            "%s. Add it to _DERIVED_SECTIONS or _HAND_WRITTEN_SECTIONS."
            % (sorted(schema_sections - classified) or "none",
               sorted(classified - schema_sections) or "none"))

    lines.extend([
        "",
        "# ---------------------------------------------------------------",
        "# WHAT IS NOT ABOVE, AND WHY.",
        "#",
        "# This file carries %d of the schema's %d sections: %s."
        % (len(_DERIVED_SECTIONS), len(schema_sections),
           ", ".join(_DERIVED_SECTIONS)),
        "# Carried is not the same as fully observed, even for those three:",
        "# version is a constant, factory.name can fall back to the directory",
        "# basename, the effect names and destinations come from the probe pack",
        "# a human wrote, and retry_contract and unknown_state_policy are fixed",
        "# at unknown by construction. What was read off the installation is",
        "# the CALL SITES, and whether each scripted one carries its marker.",
        "#",
        "# The %d below THIS TOOL DOES NOT DERIVE. Write them in a separate"
        % len(_HAND_WRITTEN_SECTIONS),
        "# contract file of your own, NOT in this one: this file is regenerated",
        "# from a scan, so anything you add here is lost on the next run, and",
        "# reconcile only ever checks the effects section against the",
        "# installation. A section you hand-write is a CLAIM. Nothing in this",
        "# kit contradicts it.",
        "#",
        "# Read the reason under each name before you decide it is missing",
        "# work. Some are decisions no scan can recover -- two installations",
        "# with byte-identical files decide them differently. Others hold",
        "# fields a scanner COULD observe, and the reason says which; that is",
        "# a limit on this tool, not a property of your factory.",
        "#",
        "# Omitting a section is NOT equivalent to declaring it unknown, and",
        "# the difference does not always run the way you would want. Several",
        "# rule groups return early when their section is absent -- campaigns",
        "# is the clearest: leave it out and you get no campaign finding at",
        "# all, declare it undecided and you get several. So a missing section",
        "# can SCORE BETTER than an honest one. Do not read a short findings",
        "# list off this file as a clean bill of health.",
        "#",
    ])
    for name, answers, why in _HAND_WRITTEN_SECTIONS:
        lines.extend("# " + ln for ln in textwrap.wrap(
            "%s: %s." % (name, answers), 74, subsequent_indent="  "))
        lines.extend("#     " + ln for ln in textwrap.wrap(why, 70))
    lines.append("# ---------------------------------------------------------------")
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
        # BOTH lanes, in the order the contract records them, so the run's
        # stdout and its output file cannot disagree about the installation.
        # Printing only the code lane is what an earlier version did, and it let
        # a reader watch `ok slack_publish ... identity idempotency_key` scroll
        # past while the file being written said `effect_identity: unknown`.
        strict, reason = item.derived_identity()
        code_value, code_reason, residual = item.code_lane_identity()
        mark = "ok  " if strict != "unknown" else "MISS"
        print("  %s %-22s %2d site(s), effect_identity %s (%s)"
              % (mark, item.name, len(item.sites), strict, reason))
        # The narrower observation, on its own line and labelled, printed only
        # when it says something the line above did not. It is the fact that
        # survives fixing every scripted site, and it is not a guarantee.
        if code_value != strict:
            print("         code lane: %s (%s)" % (code_value, code_reason))
        if residual:
            print("         %d call site(s) are agent instructions; nothing "
                  "static can read their arguments" % residual)
        for site in item.missing_identity:
            print("         no %s: %s:%d" % (item.identity_name, site.path, site.line))
        # Printed under their own heading, with the matched text, because the
        # reader's job on these is to decide whether each one is an invocation
        # at all -- which the location alone cannot tell them.
        _print_review_lines(item)

    # Render the contract BEFORE writing anything. The emitter can refuse --
    # an unclassified schema section, an unreadable schema, a field derive()
    # produces that it does not cover -- and it used to refuse AFTER
    # evidence.json had already been replaced, leaving a fresh evidence file
    # beside a derived contract from an earlier run. Two files from two runs in
    # one directory is worse than a run that wrote nothing: the timestamps agree
    # and nothing in either file says they disagree.
    contract_text = _emit_derived_yaml(contract, evidence)

    evidence_path = out_dir / "evidence.json"
    evidence_path.write_text(json.dumps(
        [
            {
                "effect": e.name,
                "destination": e.destination,
                "identity_name": e.identity_name,
                # BOTH answers, deliberately. The strict one is "can this be
                # statically guaranteed" and the code-lane one is "does the code
                # that performs it carry the identity"; they differ exactly when
                # an agent is instructed in prose, and a consumer that can only
                # see one of them cannot tell that case from a real gap.
                "derived_identity": e.derived_identity()[0],
                "reason": e.derived_identity()[1],
                "code_lane_identity": e.code_lane_identity()[0],
                "code_lane_reason": e.code_lane_identity()[1],
                "instructed_call_sites": e.code_lane_identity()[2],
                "sites": [vars(s) for s in e.sites],
            }
            for e in evidence
        ], indent=2) + "\n")
    print("wrote %s" % evidence_path)

    target = Path(args.write) if args.write else out_dir / "factory.derived.yaml"
    target.write_text(contract_text)
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
    print("call sites by top-level directory (prose = matches in .md/.rst/.txt):")
    rows = scaffold_mod.directory_distribution(found)[:12]
    all_prose = []
    for top, hits, prose in rows:
        note = ""
        if prose == hits:
            note = "  all prose"
            all_prose.append(top)
        elif prose:
            note = "  %d prose" % prose
        print("  %-24s %4d%s" % (top, hits, note))
    print("")
    print("Directories your factory WRITES (state, logs, generated reports) "
          "will show up here")
    print("alongside the ones it RUNS, and the same command is a call site in "
          "one and a")
    print("description of one in the other. Re-run with --exclude <dir> for "
          "each that is output.")
    # Named rather than excluded. An all-prose directory is usually docs, an
    # archive, or the factory's own written record of past runs -- on the
    # repository this was measured against, the top contributor was 275
    # completed gate checklists whose matched lines were table rows saying a
    # check had PASSED. It is also where a genuine agent instruction lives, so
    # the tool must not decide. Printing the list is the difference between
    # advice the reader can act on and advice they cannot.
    if all_prose:
        print("")
        print("Every match in these is prose, so none of them is a route your "
              "code takes:")
        print("  %s" % ", ".join(all_prose))
        print("Some will be agent instructions, which belong in the scan. "
              "Documentation,")
        print("archives, and your own gate or run records do not; exclude "
              "those.")

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


def _check_declared_observations(effect, item, name, out):
    """Contradict a declared observation with the fresh scan, or say nothing.

    code_lane_identity and instructed_call_sites are the two fields a derived
    contract writes that a hand author can also write. Every other field in the
    contract is a claim about the world that only a human can settle; these two
    are readings of the code on disk, so a declared value that disagrees with
    the scan is simply wrong, and the scan is the authority.

    This exists because the review rules cannot tell the difference. EFFECT-006
    fails on a nonzero instructed_call_sites, so an author who does not want
    that finding can write 0, or delete the line, and review goes green with the
    installation untouched -- the exact hand-edit-the-declaration move this whole
    tool was built to catch, reintroduced by the field added to catch it.

    Absence is not contradiction, and it is not a silent pass either. An omitted
    field means the author never measured, which is honest, so it is reported
    as UNRECORDED rather than as a lie -- and it does not set the exit code,
    because failing it would punish the contract that declines to guess over the
    one that writes a comfortable number. What it must not do is disappear:
    review reads the contract, so an omitted count means EFFECT-006 never fires,
    and this is the only place that knows the real one.
    """
    declared_count = effect.get("instructed_call_sites")
    scanned_count = len(item.instructed)
    if isinstance(declared_count, int) and declared_count != scanned_count:
        out.append((
            name, "STALE",
            "contract declares instructed_call_sites %d; the scan finds %d"
            % (declared_count, scanned_count)))
    elif declared_count is None and scanned_count:
        out.append((
            name, "UNRECORDED",
            "the scan finds %d agent-instruction call site(s) and the contract "
            "records no instructed_call_sites, so EFFECT-006 cannot fire on it"
            % scanned_count))
    declared_lane = effect.get("code_lane_identity")
    scanned_lane = item.code_lane_identity()[0]
    if isinstance(declared_lane, str) and declared_lane.strip() != scanned_lane:
        out.append((
            name, "STALE",
            "contract declares code_lane_identity %s; the scan finds %s"
            % (declared_lane.strip(), scanned_lane)))


def _print_review_lines(item):
    """Name every match the reader has to judge, in both lanes.

    Derive already printed the scripted lane's set-aside matches; reconcile did
    not, and reconcile is the command that runs on a schedule and sets the exit
    code, so the reader who only ever sees reconcile got "exclude the ones that
    are not invocations with not_regex in the probe pack" and no way to find out
    which ones. Both commands call this now rather than keeping a loop each,
    because the two drifting apart is what produced that gap in the first place.

    The instruction lane had the same gap one level worse: not one line of it was
    ever printed anywhere, only counted. EFFECT-006 fails on that count, so a
    reader was handed a failure over 34 sites with no way to see one of them, and
    `not_regex` -- the lever the scripted lane's own message names -- cannot be
    written against a list nobody can read. Every instructed site prints, sorted
    into what it is, because that partition is the whole judgement being asked
    for:

      instruction              readable as a direction to perform the effect
      review, instruction      not readable as an invocation at all
      instruction spells out   a literal command carrying the identity marker

    The matched text goes on every line: the reader's job here is to decide
    whether the line is an invocation at all, and a path and a line number cannot
    tell them that.

    Reporting only. Nothing here changes a verdict or a count -- see
    EffectEvidence.unclassified and .instructed_unclassified for why setting a
    match aside must not, and why the instruction lane needs it most.
    """
    if item is None:
        return
    for site in item.unclassified:
        print("         review (%s): %s:%d  %s"
              % (site.set_aside, site.path, site.line, site.text[:70]))
    for site in item.instructed_plain:
        print("         instruction: %s:%d  %s"
              % (site.path, site.line, site.text[:70]))
    for site in item.instructed_unclassified:
        print("         review, instruction (%s): %s:%d  %s"
              % (site.set_aside, site.path, site.line, site.text[:70]))
    for site in item.instructed_with_marker:
        print("         instruction spells out %s: %s:%d  %s"
              % (site.identity_evidence, site.path, site.line, site.text[:70]))


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
    # Declared OBSERVATIONS that the fresh scan contradicts. Kept out of the
    # four buckets below on purpose: those are a partition of the declared
    # effects, and an effect can be CONFIRMED on its identity while its
    # instructed-site count is a fiction. This is the check that stops a hand
    # author from clearing EFFECT-006 by writing instructed_call_sites: 0 --
    # the review rules read the contract, and only the scan can contradict it.
    stale_observations = []
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
        _check_declared_observations(effect, item, name, stale_observations)
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
        _print_review_lines(by_name.get(name))
    # Every verdict prints its review lines, and the reasoning that used to
    # exempt two of them is recorded here because it was correct and stopped
    # being so. It held for the SCRIPTED lane only: unclassified reads scripted
    # + fenced, an effect with no code has none, and a decided identity requires
    # it to be empty, so those calls really were branches that could never fire.
    # None of that constrains the instruction lane. An effect performed only by
    # prose is precisely the UNVERIFIED case, and an effect whose code is clean
    # is precisely the case where EFFECT-006 is the only finding left -- the two
    # verdicts where a reader most needs to see which lines produced the count,
    # and the two that were silent.
    for name, note in unverifiable:
        print("UNVERIFIED  %s: %s" % (name, note))
        _print_review_lines(by_name.get(name))
    for name, claimed, reason in confirmed:
        print("CONFIRMED  %s: effect_identity %s, %s" % (name, claimed, reason))
        _print_review_lines(by_name.get(name))
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
        _print_review_lines(by_name.get(name))
    for name, reason in undeclared:
        print("UNDECLARED  %s: %s" % (name, reason))
    for name, label, reason in stale_observations:
        print("%s  %s: %s" % (label, name, reason))
    print("%d drift, %d unverified, %d confirmed, %d open (of %d declared)"
          % (len(drift), len(unverifiable), len(confirmed), len(open_items),
             len([e for e in declared_effects if isinstance(e, dict)])))
    if undeclared:
        print("%d effect(s) performed but never declared" % len(undeclared))
    contradicted = [o for o in stale_observations if o[1] == "STALE"]
    unrecorded = [o for o in stale_observations if o[1] == "UNRECORDED"]
    if contradicted:
        print("%d declared observation(s) the scan contradicts" % len(contradicted))
    if unrecorded:
        print("%d effect(s) the scan measured and the contract does not record"
              % len(unrecorded))
    return 1 if drift or undeclared or contradicted else 0


def cmd_cites(args):
    """Report the path:line references that no longer resolve.

    Deliberately reads the contract as TEXT rather than as a parsed document:
    almost every cite in a real contract lives in a comment, which the YAML
    loader discards. A version that walked the parsed tree would report a
    handful of cites and a clean run, which is the shape of a check that finds
    nothing because it is not looking.
    """
    path = Path(args.file)
    try:
        text = path.read_text()
    except OSError as exc:
        print(f"{path}: unreadable: {exc}")
        return 2
    missing_roots = [r for r in args.roots if not Path(r).is_dir()]
    if missing_roots:
        for root in missing_roots:
            print(f"{root}: not a directory")
        # Exit 2, not 1. Every cite would report missing against a root that
        # is not there, and a wall of findings caused by a typo'd argument
        # reads exactly like a contract that rotted through.
        return 2

    found = cites_mod.resolve(cites_mod.extract(text), args.roots)
    if not found:
        print(f"{path}: no path:line cites found")
        print("This is a claim about the contract's TEXT, not about the code.")
        return 0

    buckets = {"missing": [], "out_of_range": [], "ambiguous": [],
               "resolved": [], "resolved_via_contract": []}
    for cite in found:
        buckets[cite.status].append(cite)

    for status, label in (("missing", "MISSING"), ("out_of_range", "OUT OF RANGE")):
        for cite in buckets[status]:
            print(f"{label} {path}:{cite.line_number}: {cite.raw} — {cite.detail}")
    for cite in buckets["ambiguous"]:
        print(f"AMBIGUOUS {path}:{cite.line_number}: {cite.raw} — {cite.detail}")

    # Printed, not merely counted: each of these rests on a full path the
    # CONTRACT wrote somewhere else, not on anything at the cite itself, and a
    # basename the contract pinned once can still have been meant for a
    # different file of the same name. Naming the file it chose is what lets a
    # reader disagree with the inference.
    for cite in buckets["resolved_via_contract"]:
        print(f"INFERRED {path}:{cite.line_number}: {cite.raw} -> "
              f"{cite.resolved}, from a full path this contract gave elsewhere")

    resolved = len(buckets["resolved"])
    inferred = len(buckets["resolved_via_contract"])
    broken = len(buckets["missing"]) + len(buckets["out_of_range"])
    print(f"{len(found)} cite(s): {resolved} resolved, {inferred} resolved via a "
          f"full path the contract gave elsewhere, {broken} broken, "
          f"{len(buckets['ambiguous'])} ambiguous")
    print("Resolved means the file exists and the line is inside it. It does "
          "NOT mean the line still says what the contract claims: a line-pinned "
          "claim invalidates its own refutation as soon as anything above it "
          "moves, so that is not checkable here.")
    return 1 if broken else 0


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

    p_cites = sub.add_parser(
        "cites",
        help="resolve the path:line references a contract makes about code")
    p_cites.add_argument("file", help="hand-written factory contract")
    p_cites.add_argument("roots", nargs="+",
                         help="one or more repository roots the cites point into")
    p_cites.set_defaults(func=cmd_cites)

    p_render = sub.add_parser("render", help="render diagrams and tables")
    p_render.add_argument("file", help="factory contract to render")
    p_render.add_argument("--out", default="out", help="directory for rendered files")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
