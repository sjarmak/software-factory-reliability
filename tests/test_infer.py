"""Tests for deriving a contract from an installation.

Every test here names the mutation that flips it. A derivation that can only
report one verdict is not a measurement, so the confirmed rail is tested as
explicitly as the drift rail: the first two tests differ by a single call site.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import infer  # noqa: E402

PROBES = """\
version: factory.probes/v1
name: control
factory_name: control
scan:
  include_globs: ["bin/*", "formulas/*.toml"]
effects:
  - name: slack_publish
    destination: messaging
    call_site:
      harness_globs: ["bin/*.test"]
      scripted:
        path_globs: ["bin/*"]
        any_of:
          - regex: 'gc slack publish-to-channel'
            languages: [shell, unknown]
            not_regex: ['--help']
          # Mirrors the python matcher in probes/gc-shell-city.yaml. A fixture
          # simpler than the real probe pack proves a property of the fixture.
          - regex: '["'']publish-to-channel["'']'
            languages: [python]
            not_regex: ['==', 'assert', '--help', 'startswith', ' in ']
            require_regex: '["'']publish-to-channel["'']\\s*[,\\])]'
      instructed:
        path_globs: ["formulas/*.toml"]
        any_of:
          - regex: 'gc slack publish-to-channel'
    identity:
      name: idempotency_key
      markers: ["--idempotency-key"]
"""

CONTRACT = """\
version: factory.reliability/v1
factory: {name: control}
effects:
  - name: slack_publish
    destination: messaging
    effect_identity: idempotency_key
    retry_contract: deduplicate
    unknown_state_policy: block_and_escalate
"""


def _install(tmp_path, files):
    root = tmp_path / "install"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    probes = tmp_path / "probes.yaml"
    probes.write_text(PROBES)
    return root, probes


KEYED = "#!/usr/bin/env bash\ngc slack publish-to-channel --session x --idempotency-key k --file /tmp/m\n"
BARE = "#!/usr/bin/env bash\ngc slack publish-to-channel --session x --file /tmp/m\n"


def _identity(tmp_path, files):
    root, probes = _install(tmp_path, files)
    _, evidence = infer.derive(root, infer.load_probes(probes))
    return evidence[0].derived_identity()


def test_every_scripted_site_keyed_yields_a_decided_identity(tmp_path):
    value, reason = _identity(tmp_path, {"bin/poster": KEYED})
    assert value == "idempotency_key"
    assert "1 scripted" in reason


def test_one_unkeyed_scripted_site_withdraws_the_identity(tmp_path):
    """The mutation from the test above: one added call site with no key."""
    value, reason = _identity(tmp_path, {"bin/poster": KEYED, "bin/bare": BARE})
    assert value == "unknown"
    assert "1 of 2 scripted" in reason


def test_an_instructed_site_withdraws_the_identity_even_when_code_is_clean(tmp_path):
    """No static marker can bind a sentence telling an agent to do it."""
    value, reason = _identity(tmp_path, {
        "bin/poster": KEYED,
        "formulas/f.toml": 'prompt = "run gc slack publish-to-channel at the end"\n',
    })
    assert value == "unknown"
    assert "agent instructions" in reason


def test_harness_sites_are_subtracted_but_still_collected(tmp_path):
    """A harness call site must not withdraw the identity, and must not vanish.

    Silently dropping it would hide a harness that performs a real effect
    against the wrong destination.
    """
    root, probes = _install(tmp_path, {"bin/poster": KEYED, "bin/x.test": BARE})
    _, evidence = infer.derive(root, infer.load_probes(probes))
    item = evidence[0]
    assert item.derived_identity()[0] == "idempotency_key"
    assert [s.path for s in item.harness] == ["bin/x.test"]


def test_a_comment_only_line_is_not_a_call_site(tmp_path):
    value, _ = _identity(tmp_path, {
        "bin/poster": KEYED,
        "bin/doc": "#!/usr/bin/env bash\n# gc slack publish-to-channel is how we post\n",
    })
    assert value == "idempotency_key"


def test_not_regex_removes_a_help_probe(tmp_path):
    """A --help invocation names the verb and performs nothing."""
    value, _ = _identity(tmp_path, {
        "bin/poster": KEYED,
        "bin/probe": "#!/usr/bin/env bash\ngc slack publish-to-channel --help\n",
    })
    assert value == "idempotency_key"


def test_a_read_verb_sharing_a_write_prefix_is_excluded(tmp_path):
    """The case the exclusion mechanism exists for: a read subcommand whose
    name extends the write verb. Without the exclusion these join the
    population and the count describes nothing.

    Mutation: drop 'nudge poll' from not_regex and this goes to unknown.
    """
    probes = """\
version: factory.probes/v1
name: control
scan:
  include_globs: ["bin/*"]
effects:
  - name: agent_mail_nudge
    destination: messaging
    call_site:
      scripted:
        path_globs: ["bin/*"]
        any_of:
          - regex: 'gc nudge\\b'
            languages: [shell, unknown]
            not_regex: ['nudge poll', 'nudge drain']
    identity:
      name: nudge_id
      markers: ["--nudge-id"]
"""
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "send").write_text(
        "#!/usr/bin/env bash\ngc nudge send --nudge-id abc\n")
    (root / "bin" / "poll").write_text("#!/usr/bin/env bash\ngc nudge poll\n")
    probe_path = tmp_path / "p.yaml"
    probe_path.write_text(probes)
    _, evidence = infer.derive(root, infer.load_probes(probe_path))
    assert [s.path for s in evidence[0].sites] == ["bin/send"]
    assert evidence[0].derived_identity()[0] == "nudge_id"


def test_a_flag_on_the_next_command_does_not_satisfy_this_one(tmp_path):
    two_calls = (
        "#!/usr/bin/env bash\n"
        "gc slack publish-to-channel --session x --file /tmp/a\n"
        "gc slack publish-to-channel --session x --idempotency-key k --file /tmp/b\n"
    )
    value, reason = _identity(tmp_path, {"bin/poster": two_calls})
    assert value == "unknown"
    assert "1 of 2 scripted" in reason


def test_a_flag_later_in_the_same_pipeline_does_not_satisfy_this_one(tmp_path):
    """A window cannot tell one command's flags from the next command's."""
    value, reason = _identity(tmp_path, {
        "bin/poster": "#!/usr/bin/env bash\n"
                      "gc slack publish-to-channel --file /tmp/a "
                      "| tee --idempotency-key\n",
    })
    assert value == "unknown"
    assert "1 of 1 scripted" in reason


def test_a_marker_in_a_trailing_comment_does_not_count(tmp_path):
    value, reason = _identity(tmp_path, {
        "bin/poster": "#!/usr/bin/env bash\n"
                      "gc slack publish-to-channel --file /tmp/a "
                      "# TODO add --idempotency-key\n",
    })
    assert value == "unknown"
    assert "1 of 1 scripted" in reason


def test_a_backslash_continued_invocation_is_found(tmp_path):
    """Line-by-line matching misses this entirely, and a missed unkeyed call
    site is a confirmed identity that should have been withdrawn."""
    value, reason = _identity(tmp_path, {
        "bin/poster": KEYED,
        "bin/wrapped": "#!/usr/bin/env bash\ngc slack \\\n"
                       "    publish-to-channel --file /tmp/m\n",
    })
    assert value == "unknown"
    assert "1 of 2 scripted" in reason


def test_a_continued_invocation_keeps_its_own_flag(tmp_path):
    """The mutation of the test above: the wrapped call carries the key."""
    value, _ = _identity(tmp_path, {
        "bin/wrapped": "#!/usr/bin/env bash\ngc slack \\\n"
                       "    publish-to-channel --idempotency-key k --file /tmp/m\n",
    })
    assert value == "idempotency_key"


def test_harness_only_effect_does_not_confirm_by_vacuous_truth(tmp_path):
    """Zero scripted sites means zero evidence, not universal agreement."""
    value, reason = _identity(tmp_path, {"bin/x.test": KEYED})
    assert value == "unknown"
    assert "harness" in reason


def test_a_markdown_heading_is_an_instruction_not_a_comment(tmp_path):
    """'#' opens a heading in Markdown. Dropping it as a comment discards an
    instructed site, and the identity is then confirmed when it should have
    been withdrawn."""
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "bin" / "poster").write_text(KEYED)
    (root / "docs" / "runbook.md").write_text(
        "# Run gc slack publish-to-channel when the report is ready\n")
    probes = tmp_path / "p.yaml"
    probes.write_text(PROBES.replace(
        'include_globs: ["bin/*", "formulas/*.toml"]',
        'include_globs: ["bin/*", "formulas/*.toml", "docs/*.md"]').replace(
        'path_globs: ["formulas/*.toml"]',
        'path_globs: ["formulas/*.toml", "docs/*.md"]'))
    _, evidence = infer.derive(root, infer.load_probes(probes))
    value, reason = evidence[0].derived_identity()
    assert value == "unknown"
    assert "agent instructions" in reason


def test_a_file_directly_inside_a_double_star_directory_is_scanned(tmp_path):
    """fnmatch has no '**': 'hooks/**/*' alone skips 'hooks/post', and a probe
    that misses a directory's own files confirms off a population it never
    looked at."""
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "hooks").mkdir(parents=True)
    (root / "bin" / "poster").write_text(KEYED)
    (root / "hooks" / "post").write_text(BARE)
    probes = tmp_path / "p.yaml"
    probes.write_text(PROBES.replace(
        'include_globs: ["bin/*", "formulas/*.toml"]',
        'include_globs: ["bin/*", "hooks/**/*", "formulas/*.toml"]').replace(
        'path_globs: ["bin/*"]', 'path_globs: ["bin/*", "hooks/**/*"]'))
    _, evidence = infer.derive(root, infer.load_probes(probes))
    value, reason = evidence[0].derived_identity()
    assert value == "unknown"
    assert "1 of 2 scripted" in reason


def test_probe_pack_version_is_enforced(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: nope\neffects: [{name: x}]\n")
    with pytest.raises(ValueError, match="declares version"):
        infer.load_probes(bad)


def test_probe_pack_with_no_effects_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: factory.probes/v1\neffects: []\n")
    with pytest.raises(ValueError, match="no effects"):
        infer.load_probes(bad)


def _reconcile(tmp_path, files):
    root, probes = _install(tmp_path, files)
    contract = tmp_path / "factory.yaml"
    contract.write_text(CONTRACT)
    return subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "reconcile",
         str(contract), str(root), "--probes", str(probes)],
        capture_output=True, text=True, cwd=tmp_path)


def test_reconcile_confirms_a_declaration_the_installation_supports(tmp_path):
    result = _reconcile(tmp_path, {"bin/poster": KEYED})
    assert result.returncode == 0
    assert "CONFIRMED" in result.stdout


def test_reconcile_catches_a_declaration_the_installation_does_not_support(tmp_path):
    """Editing the contract to a decided value must not turn this green.

    The contract is byte-identical to the test above. Only the installation
    differs, which is the property the whole command exists to enforce.
    """
    result = _reconcile(tmp_path, {"bin/poster": KEYED, "bin/bare": BARE})
    assert result.returncode == 1
    assert "DRIFT" in result.stdout


def test_reconcile_drifts_when_the_declared_identity_is_not_the_derived_one(tmp_path):
    """Same installation as the confirming test, one word changed in the
    contract. Without an equality check this reads CONFIRMED, because both
    values are merely decided."""
    root, probes = _install(tmp_path, {"bin/poster": KEYED})
    contract = tmp_path / "factory.yaml"
    contract.write_text(CONTRACT.replace(
        "effect_identity: idempotency_key", "effect_identity: request_uuid"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "reconcile",
         str(contract), str(root), "--probes", str(probes)],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1
    assert "DRIFT" in result.stdout
    assert "carry idempotency_key, not request_uuid" in result.stdout


def test_reconcile_reports_an_effect_the_contract_never_declared(tmp_path):
    root, probes = _install(tmp_path, {"bin/bare": BARE})
    contract = tmp_path / "factory.yaml"
    contract.write_text(
        "version: factory.reliability/v1\nfactory: {name: control}\neffects: []\n")
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "reconcile",
         str(contract), str(root), "--probes", str(probes)],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 1
    assert "does not mention it" in result.stdout
    # Reported in its own bucket, not folded into drift: the four declared
    # buckets partition the declared set, and this effect is not in it.
    assert "UNDECLARED" in result.stdout
    assert result.stdout.strip().splitlines()[-2].startswith("0 drift")


def test_a_python_tuple_argv_is_a_call_site_like_a_list_one():
    r"""A missed call site is the failure direction that lies.

    The suffix class first read [,\]] and silently dropped the tuple form,
    so an installation whose only unkeyed call built a tuple would report
    CONFIRMED. Flips if ")" is removed from the class in the probe pack.
    """
    matcher = {
        "language": "python",
        "regex": r"publish-to-channel",
        "require_regex": r'["\']publish-to-channel["\']\s*[,\])]',
    }
    tuple_argv = 'cmd = ("gc", "slack", "publish-to-channel")\ndispatch(cmd)\n'
    assert len(list(infer.find_sites(tuple_argv, "python", [matcher]))) == 1
    # The other rail is unchanged: a dict key is still a declaration, because
    # what follows the verb is a colon rather than an argv separator.
    dict_key = 'V = {"publish-to-channel": handler}\n'
    assert list(infer.find_sites(dict_key, "python", [matcher])) == []


def test_a_constant_command_table_is_counted_and_that_is_deliberate():
    """A spurious site withdraws an identity; a missed one confirms a false one.

    SUPPORTED = ["publish-to-channel"] is not a call, and this matcher counts
    it. The result is an unkeyed site, so the identity is withdrawn and a
    human is asked. That is the safe direction, and a cleverer regex that
    tried to tell the two apart would start erring the other way. Exclude a
    real table with not_regex rather than by loosening this.
    """
    matcher = {
        "language": "python",
        "regex": r"publish-to-channel",
        "require_regex": r'["\']publish-to-channel["\']\s*[,\])]',
    }
    table = 'SUPPORTED = ["publish-to-channel"]\n'
    assert len(list(infer.find_sites(table, "python", [matcher]))) == 1


def test_an_argv_list_built_into_a_variable_is_a_call_site():
    """The runner may be on a different statement than the argv vector.

    Requiring a visible runner token (subprocess/Popen/run() ) in the same
    statement was tried against the live city and dropped four real call
    sites that build ``cmd = [...]`` and hand it to a helper elsewhere. A
    dropped call site does not show up as a missing site; it shows up as an
    identity CONFIRMED on an incomplete population, which is the direction
    that silently lies.
    """
    matcher = {
        "language": "python",
        "regex": r"publish-to-channel",
        "require_regex": r'["\']publish-to-channel["\']\s*[,\]]',
    }
    argv_in_a_variable = (
        'cmd = [\n'
        '    "gc", "slack", "publish-to-channel",\n'
        '    "--conversation-id", CHANNEL,\n'
        ']\n'
        'dispatch(cmd)\n'
    )
    found = list(infer.find_sites(argv_in_a_variable, "python", [matcher]))
    assert len(found) == 1, "argv built into a variable must still be a call site"

    # The other rail: the same verb as a dict key is a declaration. It is told
    # apart by the colon that follows it, not by what runs it.
    declared_in_a_table = 'VERBS = {\n    "publish-to-channel": handler,\n}\n'
    assert list(infer.find_sites(declared_in_a_table, "python", [matcher])) == []


def test_every_declared_effect_is_accounted_for_in_the_reconcile_counts(tmp_path):
    """The counts must add up to the number of declared effects.

    An effect the contract leaves unknown AND the code cannot decide fell
    through every branch and printed nothing, so a five-effect contract
    reported on three. Silence there is indistinguishable from the report
    never having reached the effect.

    Flips if the OPEN branch is removed: the summary reads "0 drift, 0
    unverified, 0 confirmed, 0 open (of 1 declared)" and the sum is 0.
    """
    root, probes = _install(tmp_path, {})          # no call sites at all
    contract = tmp_path / "factory.yaml"
    contract.write_text(CONTRACT.replace(
        "effect_identity: idempotency_key", "effect_identity: unknown"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "reconcile",
         str(contract), str(root), "--probes", str(probes)],
        capture_output=True, text=True, cwd=tmp_path)
    summary = result.stdout.strip().splitlines()[-1]
    assert "of 1 declared" in summary, result.stdout
    counted = sum(int(part.split()[0])
                  for part in summary.split(" (of ")[0].split(", "))
    assert counted == 1, "declared effect went unreported: " + result.stdout
    assert "OPEN" in result.stdout


def test_an_undeclared_identity_is_reported_as_a_question_not_a_failed_search():
    """A scaffolded pack leaves every identity undecided on purpose.

    With identity.name literally "unknown", the missing-marker branch read
    "1 of 1 scripted call sites carry no unknown" -- a sentence that describes
    a broken tool rather than the decision it is waiting on. Flips if the
    early return for an undeclared identity is removed.
    """
    site = infer.CallSite(path="bin/x", line=1, text="git push",
                          kind="scripted", has_identity=False)
    evidence = infer.EffectEvidence(
        name="git_push", destination="code_host",
        identity_name="unknown", sites=[site])
    value, reason = evidence.derived_identity()
    assert value == "unknown"
    assert "no identity is declared" in reason
    assert "carry no unknown" not in reason


def test_a_file_in_two_groups_is_counted_once(tmp_path):
    """Overlapping path_globs must not double-count an invocation.

    A tree holding code and documents side by side is the normal case, and
    listing it in both groups produced two sites for one call: the population
    every ratio is computed over was inflated by exactly the overlap.

    Flips if _resolve_overlaps is removed: len(sites) becomes 2.
    """
    root = tmp_path / "install"
    (root / "svc").mkdir(parents=True)
    (root / "svc" / "notes.md").write_text("Run `gc slack publish-to-channel` when done.\\n")
    probe = {
        "name": "slack_publish",
        "destination": "messaging",
        "call_site": {
            "scripted": {"path_globs": ["svc/**/*"],
                         "any_of": [{"regex": "publish-to-channel"}]},
            "instructed": {"path_globs": ["svc/**/*.md"],
                           "any_of": [{"regex": "publish-to-channel"}]},
        },
        "identity": {"name": "idempotency_key", "markers": ["--idempotency-key"]},
    }
    files = list(infer.scan_files(root, {"include_globs": ["**"]}))
    evidence = infer.probe_effect(probe, files)
    assert len(evidence.sites) == 1, [s.kind for s in evidence.sites]
    # And it is the instructed reading that survives: a command in a document
    # is read, not run, so no static marker could bind it.
    assert evidence.sites[0].kind == "instructed"


def test_one_invocation_matching_two_patterns_is_one_site(tmp_path):
    """"any_of" means any matched, not each that matched.

    Measured on a real installation: three nudge call sites were counted
    twice because a single `gc session nudge mayor "$msg"` line matched two
    of the group's patterns. Flips if _resolve_overlaps is removed.
    """
    root = tmp_path / "install"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "surfacer").write_text('#!/bin/sh\ngc session nudge mayor "$msg"\n')
    probe = {
        "name": "nudge",
        "destination": "agent",
        "call_site": {
            "scripted": {
                "path_globs": ["bin/*"],
                "any_of": [{"regex": r"gc session nudge"},
                           {"regex": r"\bnudge\b"}],
            },
        },
        "identity": {"name": "nudge_id", "markers": ["--nudge-id"]},
    }
    files = list(infer.scan_files(root, {"include_globs": ["**"]}))
    evidence = infer.probe_effect(probe, files)
    assert len(evidence.sites) == 1, [(s.path, s.line) for s in evidence.sites]


def test_an_instructed_site_does_not_hide_how_the_scripted_sites_scored(tmp_path):
    """Both facts reach the reader, because they call for different work.

    Found by running this on a real city: agent_mail_nudge reported "2 call
    site(s) are agent instructions" and nothing else, while 30 of its 30
    scripted sites carried no nudge_id. The verdict was right and the reason
    named the smaller number. An instructed site is a design problem someone
    has to think about; thirty scripted sites missing a flag is a patch, and a
    reader who only sees the first never learns the second is available.

    Mutation that flips this: drop the missing-identity clause from the
    instructed branch of derived_identity and report only the instructed count.
    """
    value, reason = _identity(tmp_path, {
        "bin/a": BARE,
        "bin/b": BARE,
        "formulas/f.toml": 'prompt = "run gc slack publish-to-channel at the end"\n',
    })
    assert value == "unknown"
    assert "agent instructions" in reason
    assert "2 of 2 scripted" in reason


def test_clean_scripted_sites_are_reported_alongside_an_instructed_one(tmp_path):
    """The other rail: the added clause must not always read as a failure.

    Without this, a reason that hardcoded "N of N scripted call sites carry no
    X" would pass the test above while lying about a codebase whose scripted
    sites are fine and whose only problem is the instruction.
    """
    value, reason = _identity(tmp_path, {
        "bin/a": KEYED,
        "formulas/f.toml": 'prompt = "run gc slack publish-to-channel at the end"\n',
    })
    assert value == "unknown"
    assert "agent instructions" in reason
    assert "carry no" not in reason
    assert "1 scripted site(s) carry it" in reason


# Both taken from a real installation. The flag is on the same command list as
# the invocation; it is added by a later statement because the list is built up
# rather than written as one literal, which is ordinary Python and not a
# workaround for anything.
ASSEMBLED = '''#!/usr/bin/env python3
import subprocess


def post(body, session_id):
    command = [
        "gc", "slack", "publish-to-channel",
        "--session", session_id,
    ]
    command.extend(["--body-file", "/tmp/m"])
    command.extend(["--idempotency-key", "poster:" + body])
    return subprocess.run(command)
'''

ASSEMBLED_AUGMENTED = '''#!/usr/bin/env python3
import subprocess


def post(body, sid):
    cmd = ["gc", "slack", "publish-to-channel", "--session", sid]
    cmd += ["--body-file", "/tmp/m"]
    cmd += ["--idempotency-key", "mirror:" + body]
    return subprocess.run(cmd)
'''

ASSEMBLED_BARE = '''#!/usr/bin/env python3
import subprocess


def post(body, session_id):
    command = [
        "gc", "slack", "publish-to-channel",
        "--session", session_id,
    ]
    command.extend(["--body-file", "/tmp/m"])
    return subprocess.run(command)
'''


def test_a_flag_appended_to_the_same_command_list_counts(tmp_path):
    """A command assembled over several statements is one invocation.

    Measured on a real city: two Slack call sites were reported as carrying no
    idempotency key while both pass one, because the flag is appended to the
    same list two statements after the literal. The unit rule that produces
    this is right in general -- a bracket-balanced statement is what a flag can
    belong to, and a line window cannot tell one invocation's flags from the
    next one's -- and it is wrong for a list that is built up.

    So the search widens by NAME AND SCOPE rather than by distance: the later
    statement has to mutate the same variable inside the same function. That is
    the same command object, not a nearby one.

    This is the direction the checker is normally not allowed to err in, since
    it turns a withdrawn identity into a confirmed one. It is admissible here
    only because the binding is exact; a window of N lines would not be.
    """
    value, reason = _identity(tmp_path, {"bin/poster": ASSEMBLED})
    assert value == "idempotency_key", reason
    assert "1 scripted" in reason


def test_augmented_assignment_assembles_the_same_way(tmp_path):
    value, reason = _identity(tmp_path, {"bin/mirror": ASSEMBLED_AUGMENTED})
    assert value == "idempotency_key", reason


def test_assembly_does_not_invent_an_identity_that_is_not_there(tmp_path):
    """The rail that matters, because this widening can only mint confirmeds.

    Same file, same shape, same number of appends, and no key anywhere in it.
    If this passes, the assembly step is reading something other than the
    marker and every green above is worthless.
    """
    value, reason = _identity(tmp_path, {"bin/poster": ASSEMBLED_BARE})
    assert value == "unknown", reason
    assert "1 of 1 scripted" in reason


def test_a_flag_on_a_different_command_does_not_satisfy_this_one(tmp_path):
    """Binding by name is the whole claim, so it is tested by name collision.

    Two lists in one function: the one that is invoked carries no key, and an
    unrelated one does. Widening by proximity confirms the identity here, which
    is precisely the false confirmed the unit rule exists to prevent.
    """
    source = '''#!/usr/bin/env python3
import subprocess


def post(body, sid):
    other = ["gc", "mail", "send"]
    other.extend(["--idempotency-key", "unrelated"])
    command = ["gc", "slack", "publish-to-channel", "--session", sid]
    command.extend(["--body-file", "/tmp/m"])
    subprocess.run(other)
    return subprocess.run(command)
'''
    value, reason = _identity(tmp_path, {"bin/poster": source})
    assert value == "unknown", reason


def test_a_flag_added_in_another_function_does_not_count(tmp_path):
    """Scope is half the binding. A module-level name reused in two functions
    is common, and the second function's flags say nothing about the first's
    call.
    """
    source = '''#!/usr/bin/env python3
import subprocess


def post(sid):
    command = ["gc", "slack", "publish-to-channel", "--session", sid]
    return subprocess.run(command)


def elsewhere(command):
    command.extend(["--idempotency-key", "not-this-call"])
    return command
'''
    value, reason = _identity(tmp_path, {"bin/poster": source})
    assert value == "unknown", reason
