"""Tests for deriving a contract from an installation.

Every test here names the mutation that flips it. A derivation that can only
report one verdict is not a measurement, so the confirmed rail is tested as
explicitly as the drift rail: the first two tests differ by a single call site.
"""

import ast
import re
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


def _reconcile_with(tmp_path, contract_text, files=None):
    root, probes = _install(tmp_path, files or {"bin/poster": KEYED})
    contract = tmp_path / "factory.yaml"
    contract.write_text(contract_text)
    return subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "reconcile",
         str(contract), str(root), "--probes", str(probes)],
        capture_output=True, text=True, cwd=tmp_path)


PROSE_CONTRACT = CONTRACT.replace(
    "effect_identity: idempotency_key",
    'effect_identity: "the (channel, thread, message body) triple; the key is what the destination deduplicates on"')


def test_an_identity_described_in_prose_is_not_drift(tmp_path):
    """The contract's effect_identity is written by a human describing what
    identifies the effect; the probe's identity name is the token the scanner
    binds. Comparing them with == is a vocabulary error that happens to work
    whenever the author guessed the token, and can NEVER match on the effects
    whose identity is a composite worth a sentence.

    That is not a cosmetic wrong answer. It means fixing the last unmarked call
    site does not move the verdict -- doing the right thing and the reading not
    changing, which is the exact failure this tool exists to catch.

    Every call site here carries the key. Before the fix this printed DRIFT and
    exited 1.
    """
    result = _reconcile_with(tmp_path, PROSE_CONTRACT)
    assert result.returncode == 0, result.stdout
    assert "DRIFT" not in result.stdout
    assert "UNVERIFIED" in result.stdout
    # The message has to carry the fix, not just the complaint: a reader who
    # cannot tell what to type is told the identity is unverifiable forever.
    assert "effect_identity_key: idempotency_key" in result.stdout


def test_naming_the_key_beside_the_prose_confirms(tmp_path):
    """The prose stays where it belongs and the contract names the token. This
    is the only way a composite identity can ever read CONFIRMED."""
    result = _reconcile_with(
        tmp_path,
        PROSE_CONTRACT.replace(
            "retry_contract: deduplicate",
            "effect_identity_key: idempotency_key\n    retry_contract: deduplicate"))
    assert result.returncode == 0, result.stdout
    assert "CONFIRMED" in result.stdout
    assert "UNVERIFIED" not in result.stdout


def test_a_named_key_that_is_wrong_still_drifts(tmp_path):
    """The escape hatch is not a way to declare yourself correct. Naming a key
    the call sites do not carry is the sharpest drift there is, and a fix that
    only ever moved verdicts toward CONFIRMED would be worse than the bug."""
    result = _reconcile_with(
        tmp_path,
        PROSE_CONTRACT.replace(
            "retry_contract: deduplicate",
            "effect_identity_key: request_uuid\n    retry_contract: deduplicate"))
    assert result.returncode == 1
    assert "carry idempotency_key, not request_uuid" in result.stdout


def test_the_prose_lane_does_not_swallow_a_wrong_token(tmp_path):
    """A one-word wrong identity is a key, not prose, and stays DRIFT. Without
    the shape test every wrong identity in the corpus would quietly become
    UNVERIFIED, which is a guard that can no longer go red."""
    result = _reconcile_with(
        tmp_path,
        CONTRACT.replace("effect_identity: idempotency_key",
                         "effect_identity: request_uuid"))
    assert result.returncode == 1
    assert "carry idempotency_key, not request_uuid" in result.stdout


def test_a_key_and_a_different_prose_token_is_a_contradiction(tmp_path):
    """The escape hatch is not an override. effect_identity here is itself a
    bare token, so it already names a key; a different effect_identity_key
    beside it means the contract says two things. Without the conflict check
    this reads CONFIRMED on the key, which turns a new field into a way to
    declare yourself correct -- the same contract read DRIFT before the field
    existed."""
    result = _reconcile_with(
        tmp_path,
        CONTRACT.replace(
            "effect_identity: idempotency_key\n    retry_contract: deduplicate",
            "effect_identity: request_uuid\n"
            "    effect_identity_key: idempotency_key\n"
            "    retry_contract: deduplicate"))
    assert result.returncode == 1, result.stdout
    assert "names two identities" in result.stdout
    assert "CONFIRMED" not in result.stdout


def test_prose_with_no_spaces_is_still_prose(tmp_path):
    """A key shape of "anything without whitespace" passes every other test in
    this file and lets `channel/thread/message` confirm against a probe token it
    does not name."""
    result = _reconcile_with(
        tmp_path,
        CONTRACT.replace("effect_identity: idempotency_key",
                         "effect_identity: channel/thread/message"))
    assert result.returncode == 0, result.stdout
    assert "UNVERIFIED" in result.stdout
    assert "DRIFT" not in result.stdout


def test_a_token_with_a_dash_or_a_digit_is_a_key(tmp_path):
    """A key shape narrowed to letters and underscores passes every other test
    here while quietly moving real token names into the prose lane, where they
    can never drift again."""
    for token in ("request-id", "identity.v2", "key2"):
        result = _reconcile_with(
            tmp_path,
            CONTRACT.replace("effect_identity: idempotency_key",
                             "effect_identity: %s" % token))
        assert result.returncode == 1, (token, result.stdout)
        assert "carry idempotency_key, not %s" % token in result.stdout


def test_the_key_comparison_is_case_sensitive(tmp_path):
    """The probe's token is a literal the scanner matches in source. Folding
    case here would confirm a contract naming a token no call site carries."""
    result = _reconcile_with(
        tmp_path,
        CONTRACT.replace(
            "retry_contract: deduplicate",
            "effect_identity_key: Idempotency_Key\n    retry_contract: deduplicate")
        .replace("effect_identity: idempotency_key",
                 'effect_identity: "the channel and the message body"'))
    assert result.returncode == 1, result.stdout
    assert "carry idempotency_key, not Idempotency_Key" in result.stdout


def test_the_schema_and_the_key_shape_agree(tmp_path):
    """Two copies of one grammar, in a JSON file and in a regex, drift the
    moment either is edited alone -- and the drift is silent: the schema would
    accept a value reconcile then treats as prose, or reject one it would have
    compared."""
    import json
    from factory_check import _KEY_PATTERN
    for name in ("factory.schema.json", "effect.schema.json"):
        doc = json.loads((ROOT / "schemas" / name).read_text())
        found = []

        def walk(node):
            if isinstance(node, dict):
                if "effect_identity_key" in node:
                    found.append(node["effect_identity_key"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(doc)
        assert found, name
        for decl in found:
            assert decl.get("pattern") == _KEY_PATTERN, (name, decl)


def test_a_padded_key_is_a_schema_error_not_a_silent_strip(tmp_path):
    """minLength alone accepts "   " and " idempotency_key ". The first reads as
    absent and the second is normalized into a confirmation, so two values the
    schema called valid get two different silent treatments."""
    contract = tmp_path / "factory.yaml"
    contract.write_text(CONTRACT.replace(
        "retry_contract: deduplicate",
        'effect_identity_key: " idempotency_key "\n    retry_contract: deduplicate'))
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "factory_check.py"), "validate",
         str(contract)],
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode != 0, result.stdout
    assert "effect_identity_key" in result.stdout + result.stderr


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


def test_an_undeclared_identity_lists_no_site_as_missing_it():
    """Nothing can lack a marker that was never named.

    The reason string above was fixed for this case and the per-site list was
    not, so a scaffolded pack printed one clean summary sentence and then a
    fix list of `no unknown: <path>` for every call site found -- 155 of them
    on the first installation this was tried against. The summary said there
    was nothing to look for; the list underneath it disagreed, at length.

    This is invisible on the installation the kit was written against, whose
    probe pack declares real identities, and it is the FIRST output every new
    user sees. Flips if missing_identity stops guarding on identity_name.
    """
    sites = [infer.CallSite(path="bin/x", line=n, text="git push",
                            kind="scripted", has_identity=False)
             for n in (1, 2, 3)]
    evidence = infer.EffectEvidence(
        name="git_push", destination="code_host",
        identity_name="unknown", sites=sites)
    assert evidence.missing_identity == []
    assert len(evidence.sites) == 3, "the sites are still found and reported"


def test_a_declared_identity_still_lists_the_sites_that_lack_it():
    """The guard above must not empty the fix list for a real identity."""
    carries = infer.CallSite(path="bin/a", line=1, text="git push --ref x",
                             kind="scripted", has_identity=True)
    lacks = infer.CallSite(path="bin/b", line=2, text="git push",
                           kind="scripted", has_identity=False)
    evidence = infer.EffectEvidence(
        name="git_push", destination="code_host",
        identity_name="expected_remote_ref", sites=[carries, lacks])
    assert [s.path for s in evidence.missing_identity] == ["bin/b"]


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


# --------------------------------------------------------------------------
# Fenced call sites.
#
# The defect these cover, measured on the live installation on 2026-08-18:
# adopting a fence made the score WORSE. `git-push-fenced` matches the bare
# verb pattern (the name contains both "git" and "push"), the identity marker
# lives inside the wrapper rather than on the call line, and so all nineteen
# scripted sites -- including the fence itself -- were reported as carrying no
# expected_remote_ref. An instrument that cannot see a fix tells the person who
# made it that nothing happened.
# --------------------------------------------------------------------------

FENCE_PROBES = """\
version: factory.probes/v1
name: control
factory_name: control
scan:
  include_globs: ["bin/*", "formulas/*.toml"]
effects:
  - name: git_push
    destination: code_host
    call_site:
      harness_globs: ["bin/*.test"]
      scripted:
        path_globs: ["bin/*"]
        any_of:
          - regex: '\\bgit\\b[^|;&]*\\bpush\\b'
            languages: [shell, unknown]
      instructed:
        path_globs: ["formulas/*.toml"]
        any_of:
          - regex: '\\bgit push\\b'
    identity:
      name: expected_remote_ref
      markers: ["--force-with-lease="]
      fenced_by:
        - name: git-push-fenced
          command: '\\bgit-push-fenced\\b'
          implementation: ["bin/git-push-fenced"]
"""

# The wrapper as it is really written: the flag is computed into a variable and
# the push references it. A marker search over the push line alone finds
# nothing.
FENCE_OK = """\
#!/usr/bin/env bash
OBSERVED=$(git ls-remote "$REMOTE" "$REF" | awk '{print $1}')
LEASE="--force-with-lease=$REF:$OBSERVED"
git push "$LEASE" "$REMOTE" "$INTENDED:$REF"
"""

# The mutation: a wrapper that reads the ref back and then pushes blind. Every
# other line is identical.
FENCE_BROKEN = """\
#!/usr/bin/env bash
OBSERVED=$(git ls-remote "$REMOTE" "$REF" | awk '{print $1}')
LEASE="$REF:$OBSERVED"
git push "$LEASE" "$REMOTE" "$INTENDED:$REF"
"""

CALLS_FENCE = "#!/usr/bin/env bash\ngit-push-fenced --remote origin --branch main\n"


def _fence_identity(tmp_path, files):
    root = tmp_path / "install"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    probes = tmp_path / "probes.yaml"
    probes.write_text(FENCE_PROBES)
    _, evidence = infer.derive(root, infer.load_probes(probes))
    return evidence[0]


def test_a_site_that_calls_the_fence_carries_the_identity(tmp_path):
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "bin/ship": CALLS_FENCE,
    })
    value, reason = item.derived_identity()
    assert value == "expected_remote_ref", reason
    assert "through a fenced command" in reason
    assert [s.path for s in item.fenced] == ["bin/ship"]


def test_a_fence_that_does_not_pin_withdraws_it_from_every_caller(tmp_path):
    """The mutation from the test above: one line of the wrapper.

    This is the property that makes the credit a measurement rather than a
    declaration. The caller is byte-identical in both tests.
    """
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_BROKEN,
        "bin/ship": CALLS_FENCE,
    })
    value, reason = item.derived_identity()
    assert value == "unknown", reason
    assert [s.identity_evidence for s in item.fenced] == [
        "git-push-fenced does not carry it"
    ]


def test_the_fence_implementation_is_itself_a_measured_call_site(tmp_path):
    """It is not exempted by being named in the probe pack.

    Exempting it is the tempting shortcut and it is the hand-written-contract
    shape again: the one place the identity has to exist would be the one place
    nothing checks.
    """
    item = _fence_identity(tmp_path, {"bin/git-push-fenced": FENCE_OK})
    assert [s.kind for s in item.scripted] == ["scripted"]
    assert [s.path for s in item.scripted] == ["bin/git-push-fenced"]
    assert item.derived_identity()[0] == "expected_remote_ref"


def test_a_fence_with_no_implementation_in_the_tree_credits_nothing(tmp_path):
    """A fence nobody can point at is a claim, and claims are what this
    replaces."""
    item = _fence_identity(tmp_path, {"bin/ship": CALLS_FENCE})
    value, reason = item.derived_identity()
    assert value == "unknown", reason
    assert all(not s.has_identity for s in item.fenced)


def test_an_instructed_site_that_names_the_fence_no_longer_withdraws_it(tmp_path):
    """A sentence telling an agent to run the fenced command is bindable.

    The usual rule -- an instructed site withdraws the identity because no
    static marker can bind it -- holds because the agent writes the argument
    list at run time. It does not hold when the identity is inside the command
    being named: the agent cannot type a version of it that skips the fence.
    """
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "formulas/ship.toml": 'prompt = "then run git-push-fenced --remote origin"\n',
    })
    assert item.derived_identity()[0] == "expected_remote_ref"
    assert item.instructed == []


def test_the_same_instruction_with_the_bare_verb_still_withdraws_it(tmp_path):
    """The mutation from the test above: `git push` in place of the fence."""
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "formulas/ship.toml": 'prompt = "then run git push origin main"\n',
    })
    value, reason = item.derived_identity()
    assert value == "unknown", reason
    assert "agent instructions" in reason


def test_a_marker_in_a_variable_the_command_does_not_use_does_not_count(tmp_path):
    """Bound by variable name, never by proximity.

    Without this the file-scoped assembly would credit any push in a file that
    computes a lease anywhere in it, which is the direction that mints a false
    confirmed.
    """
    source = """\
#!/usr/bin/env bash
UNUSED_LEASE="--force-with-lease=refs/heads/main:abc"
git push "$REMOTE" "$BRANCH"
"""
    item = _fence_identity(tmp_path, {"bin/pusher": source})
    value, reason = item.derived_identity()
    assert value == "unknown", reason


def test_a_harness_site_never_becomes_a_fenced_site(tmp_path):
    """A test that exercises the fence must not be credited as production use,
    and must not vanish either."""
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "bin/x.test": CALLS_FENCE,
    })
    assert [s.path for s in item.harness] == ["bin/x.test"]
    assert item.fenced == []


def test_a_fence_declaration_widens_detection_it_does_not_only_reclassify(tmp_path):
    """Adopting a fence must not make a call site DISAPPEAR from the effect.

    The regression this pins, measured on the gas-city installation: the
    scripted pattern matched `git-push-fenced` by accident of the hyphen, and
    the instructed pattern `\\bgit push\\b` (literal space) did not. So five
    formula sites that adopted the fence left the effect's site list entirely.
    A count of call sites that shrinks because somebody fixed them is a worse
    reading than one that calls them unkeyed -- the reviewer now has the wrong
    number for how much of the factory performs the effect at all.
    """
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        # `git-push-fenced` does not contain the substring `git push`, so
        # nothing in the instructed group's own any_of can match this file.
        "formulas/mol-ship.toml": (
            'prompt = """\nRun:\ngit-push-fenced --remote origin --branch main\n"""\n'
        ),
    })
    fenced = {site.path for site in item.fenced}
    assert "formulas/mol-ship.toml" in fenced, [
        (s.path, s.kind) for s in item.sites
    ]


def test_one_unpinned_push_in_the_wrapper_withdraws_credit_from_every_caller(tmp_path):
    """A fence's argument is that N call sites collapse to ONE place worth
    checking. That is only true if the one place has no unfenced path through
    it, so a fallback push with no lease costs the whole fence its credit.

    This read the other way for one commit -- credit survived a non-pinning
    line -- and the reason is worth keeping. Before mentions were detected, a
    wrapper's own `ME=git-push-fenced` counted as an unpinned call, so the
    strict rule made both real fences read "does not carry it" and every
    adopting site lost the identity. The instrument inverted on the fix it was
    written to see, and loosening the rule was the wrong place to fix it.
    """
    wrapper = FENCE_OK + 'git push "$REMOTE" "$FALLBACK"\n'
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": wrapper,
        "bin/ship": CALLS_FENCE,
    })
    caller = [s for s in item.fenced if s.path == "bin/ship"]
    assert caller and not caller[0].has_identity
    assert "does not carry it" in caller[0].identity_evidence


def test_a_wrapper_that_only_mentions_itself_still_credits_its_callers(tmp_path):
    """The other half, and the one that made the strict rule usable.

    Every real fence assigns its own name. If that line counts as an unpinned
    call the strict rule can never be satisfied by any wrapper anyone would
    actually write -- a guard that can never go green.
    """
    wrapper = "#!/usr/bin/env bash\nME=git-push-fenced\n" + FENCE_OK
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": wrapper,
        "bin/ship": CALLS_FENCE,
    })
    caller = [s for s in item.fenced if s.path == "bin/ship"]
    assert caller and caller[0].has_identity
    assert "1 of 1 readable site(s)" in caller[0].identity_evidence


def _git_install(tmp_path, files, gitignore):
    import subprocess as sp
    root = tmp_path / "install"
    root.mkdir()
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / ".gitignore").write_text(gitignore)
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"]):
        sp.run(cmd, cwd=root, check=True, capture_output=True)
    return root


def test_a_vcs_ignored_path_is_not_a_call_site(tmp_path):
    """A command RECORDED in a generated log is not a command run.

    The installation's own .gitignore is a declaration BY the installation
    about which of its paths are output, which is why this can be derived
    instead of hand-written. Measured on the city this kit was written
    against, the scaffold reported 401 git_push call sites in 158 files and
    the three noisiest directories were all ignored.
    """
    root = _git_install(
        tmp_path,
        {"bin/ship": "#!/usr/bin/env bash\ngit push origin main\n",
         "logs/run.log": "2026-08-18 ran: git push origin main\n"},
        "logs/\n")
    scanned = {rel for rel, _, _ in infer.scan_files(
        root, {"include_globs": ["**"], "respect_vcs_ignore": True})}
    assert "bin/ship" in scanned
    assert "logs/run.log" not in scanned
    # Without the flag the log is read, which is the behaviour a pack that does
    # not ask for this still gets.
    plain = {rel for rel, _, _ in infer.scan_files(root, {"include_globs": ["**"]})}
    assert "logs/run.log" in plain


def test_an_unavailable_ignore_check_is_reported_not_swallowed(tmp_path):
    """Asked-for and not-applied must never read as applied.

    An empty ignore set means "nothing is ignored"; a missing git means "this
    was never checked". A scan that reports the first when the second is true
    claims a scope it never had.
    """
    root = tmp_path / "plain"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "ship").write_text("#!/usr/bin/env bash\ngit push origin main\n")
    notes = []
    scanned = {rel for rel, _, _ in infer.scan_files(
        root, {"include_globs": ["**"], "respect_vcs_ignore": True}, notes)}
    assert "bin/ship" in scanned
    assert notes and "could not be checked" in notes[0]
    assert "scanned as if it were source" in notes[0]


def test_a_quoted_mention_is_set_aside_from_the_fix_list(tmp_path):
    """Prose that names the command is not something anyone can add a flag to.

    Measured on the gas-city installation before this existed: git_push
    reported "6 of 16 scripted site(s) carry no expected_remote_ref", and three
    of the six were a jq glob pattern, an error message, and a sed replacement
    string. A reader sent to fix six things found three of them unfixable.
    """
    value, reason = _identity(tmp_path, {
        "bin/real": BARE,
        "bin/talks-about-it": (
            "#!/usr/bin/env bash\n"
            'echo "run gc slack publish-to-channel to post it"\n'
        ),
    })
    assert value == "unknown"
    root, probes = _install(tmp_path, {
        "bin/real": BARE,
        "bin/talks-about-it": (
            "#!/usr/bin/env bash\n"
            'echo "run gc slack publish-to-channel to post it"\n'
        ),
    })
    _, evidence = infer.derive(root, infer.load_probes(probes))
    item = evidence[0]
    assert [s.path for s in item.missing_identity] == ["bin/real"]
    assert [s.path for s in item.unclassified] == ["bin/talks-about-it"]
    assert "1 match(es) set aside" in reason


def test_setting_a_quoted_match_aside_never_decides_the_identity(tmp_path):
    """The dangerous case and the harmless one look identical, so neither is
    allowed to make the score better.

    A deferred command assembled with no marker -- PUSH_CMD="git push origin
    main" -- is exactly the unfenced write this kit exists to find, and it is
    quoted. Dropping quoted matches silently would be a guard that can only
    ever raise a score, which is the shape of an instrument that inverts on the
    case it was built for.
    """
    value, reason = _identity(tmp_path, {
        "bin/keyed": KEYED,
        "bin/deferred": (
            "#!/usr/bin/env bash\n"
            'CMD="gc slack publish-to-channel --session x --file /tmp/m"\n'
            'eval "$CMD"\n'
        ),
    })
    # Every unquoted site carries the marker, and the answer is still not yes.
    assert value == "unknown"
    assert "every readable scripted call site carries it" in reason
    assert "not_regex" in reason


def test_excluding_a_quoted_mention_by_not_regex_decides_it(tmp_path):
    """The escape has to move the score, or the note above is a dead end.

    An effect whose only unkeyed matches are quoted would otherwise be stuck at
    unknown with nothing the reader could do about it -- and a fix that cannot
    change the reading is a fix nobody will make.
    """
    root, probes = _install(tmp_path, {
        "bin/keyed": KEYED,
        "bin/talks-about-it": (
            "#!/usr/bin/env bash\n"
            'echo "run gc slack publish-to-channel to post it"\n'
        ),
    })
    text = probes.read_text().replace(
        "            not_regex: ['--help']",
        "            not_regex: ['--help', 'echo \"run ']",
    )
    probes.write_text(text)
    _, evidence = infer.derive(root, infer.load_probes(probes))
    value, reason = evidence[0].derived_identity()
    assert value == "idempotency_key"
    assert "quoted" not in reason


def test_an_unquoted_command_is_not_set_aside(tmp_path):
    """The rail that keeps the quote test from swallowing real call sites."""
    root, probes = _install(tmp_path, {"bin/real": BARE})
    _, evidence = infer.derive(root, infer.load_probes(probes))
    item = evidence[0]
    assert [s.path for s in item.missing_identity] == ["bin/real"]
    assert item.unclassified == []


def test_a_separator_inside_quotes_does_not_end_the_command():
    assert infer.split_shell_segments(
        "run --command 'cd /tmp && git push origin main' --json"
    ) == ["run --command 'cd /tmp && git push origin main' --json"]


def test_a_separator_outside_quotes_still_ends_the_command():
    """The rail that keeps the quote tracking from swallowing every split.

    Without this the fix for the line above is indistinguishable from returning
    the whole line always, which would let a neighbouring command's flag
    satisfy this one -- the error logical_units' own docstring names first.
    """
    assert infer.split_shell_segments("a --x && b --y ; c | d") == [
        "a --x ", " b --y ", " c ", " d"]


def test_an_unbalanced_quote_widens_rather_than_truncates():
    assert infer.split_shell_segments("echo 'oops && b") == ["echo 'oops && b"]


def test_a_quoted_command_split_at_its_own_separator_is_still_seen_as_quoted(tmp_path):
    """Segmentation and the quoted-mention test are one mechanism, not two.

    Measured on the gas-city installation: a compatibility check passes a
    fixture command to a permission tester,

        amp permissions test shell_command --command 'cd /tmp && git push ...'

    and a quote-blind split cut it at the `&&` INSIDE the quotes. The tail
    began mid-string, so the quote tracker never saw the opening quote and a
    string argument was reported as an unfenced push somebody should go fix.
    """
    root, probes = _install(tmp_path, {
        "bin/checks": (
            "#!/usr/bin/env bash\n"
            "probe --command 'cd /tmp && gc slack publish-to-channel --session x' --json\n"
        ),
        "bin/keyed": KEYED,
    })
    _, evidence = infer.derive(root, infer.load_probes(probes))
    item = evidence[0]
    assert [s.path for s in item.missing_identity] == []
    assert [s.path for s in item.unclassified] == ["bin/checks"]


def test_a_bare_assignment_is_set_aside_not_reported_as_a_call(tmp_path):
    """`ME=git-push-fenced` is a string constant, and every wrapper has one.

    This was created by the fence-detection widening: the fence's own command
    pattern matches the wrapper's name literal, so adopting a fence added a
    permanent unfixable entry to the fix list of every installation that
    adopted one.
    """
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "bin/names-it": "#!/usr/bin/env bash\nPUSHER=git-push-fenced\n",
    })
    assert [s.path for s in item.missing_identity] == []
    assert [(s.path, s.set_aside) for s in item.unclassified] == [
        ("bin/names-it",
         "assigned as a bare literal; nothing is invoked on this line")]


def test_an_env_prefixed_command_is_not_a_bare_assignment(tmp_path):
    """The rail. `FOO=bar cmd ...` is an invocation with an assignment in front
    of it, and reading it as a constant would hide a real call site."""
    item = _fence_identity(tmp_path, {
        "bin/git-push-fenced": FENCE_OK,
        "bin/prefixed": "#!/usr/bin/env bash\nGIT_QUIET=1 git-push-fenced --remote origin\n",
    })
    assert [s.path for s in item.unclassified] == []
    caller = [s for s in item.fenced if s.path == "bin/prefixed"]
    assert caller and caller[0].has_identity


def _languages_the_classifier_can_emit():
    """Every value _language_of can return, derived from its own source.

    Two halves, because the function has two kinds of return: literals like
    "shell", and `suffix.lstrip(".")` for a set of suffixes. The second is
    computed, so the literals alone under-report it -- an enumerator that
    misses a branch is the same defect as the hand-written list it replaces,
    which is why the suffixes are collected and then actually run through the
    function rather than transformed by hand here.
    """
    tree = ast.parse((ROOT / "src" / "infer.py").read_text())
    body = next((n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_language_of"), None)
    assert body is not None, "no module-level function named _language_of"
    strings = {n.value for n in ast.walk(body)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    emitted = {n.value.value for n in ast.walk(body)
               if isinstance(n, ast.Return)
               and isinstance(n.value, ast.Constant)
               and isinstance(n.value.value, str)}
    suffixes = {t for t in strings if t.startswith(".") and len(t) > 1}
    assert suffixes, "no file suffixes found in _language_of; the walk is broken"
    for suffix in suffixes:
        emitted.add(infer._language_of("probe" + suffix, ""))
    return emitted


def _set_aside_gate_languages():
    """The language names set_aside_reason tests against, from the source."""
    source = (ROOT / "src" / "infer.py").read_text()
    match = re.search(r"if language not in \(([^)]*)\)", source)
    assert match, "the set-aside language gate is no longer a tuple literal"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_every_language_the_set_aside_gate_names_can_actually_occur():
    """A gate naming a language the classifier never emits is dead text.

    It shipped naming "markdown" while _language_of returns "md", so prompt
    markdown -- named in that function's own docstring as in scope -- was
    excluded from quote handling by a string that could never match. The
    branch read as covered and could not be taken.

    Enumerated from the AST on both sides: a hand-written list of either half
    is the same defect one level up.
    """
    emitted = _languages_the_classifier_can_emit()
    named = _set_aside_gate_languages()
    assert named <= emitted, (
        "the set-aside gate names languages _language_of cannot return: "
        f"{sorted(named - emitted)}; it emits {sorted(emitted)}")


def test_markdown_is_the_language_name_the_classifier_uses():
    """The specific case above, pinned so the rename cannot silently revert."""
    assert infer._language_of("prompts/mayor.md", "") == "md"
    assert "md" in _set_aside_gate_languages()


def test_a_confirmation_on_a_named_key_says_what_it_did_not_check(tmp_path):
    """CONFIRMED on a named key is a narrower claim than CONFIRMED on a key the
    contract wrote as its whole identity, and the word is the same either way.

    A static scan checks that every call site carries the token. It cannot check
    that the token's runtime VALUE is the identity the prose describes -- "a
    fresh execution nonce" declared as idempotency_key confirms here and is
    unstable across every retry. Without this line the reader takes the stronger
    reading, which is the one nobody measured.
    """
    result = _reconcile_with(
        tmp_path,
        PROSE_CONTRACT.replace(
            "retry_contract: deduplicate",
            "effect_identity_key: idempotency_key\n    retry_contract: deduplicate"))
    assert result.returncode == 0, result.stdout
    assert "not statically checkable" in result.stdout


def test_a_plain_confirmation_carries_no_such_caveat(tmp_path):
    """The other rail. If the note printed on every confirmation it would be
    noise rather than a scope statement, and a reader would learn to skip it on
    exactly the rows where it is load-bearing."""
    result = _reconcile(tmp_path, {"bin/poster": KEYED})
    assert result.returncode == 0
    assert "CONFIRMED" in result.stdout
    assert "not statically checkable" not in result.stdout


def test_a_key_with_a_trailing_newline_does_not_confirm(tmp_path):
    """The schema pattern is an ECMA-262 regex, where `$` is the end of the
    string. Python's `re` is what actually enforces it, and there `$` also
    matches just before a trailing newline -- so "idempotency_key\\n" passes the
    schema, and passed the runtime shape test, and used to be stripped into a
    CONFIRMED against a probe token it is not equal to.

    The reading has to fall the safe way. A value the runtime cannot compare
    reads UNVERIFIED and prints the line to add; it never reads CONFIRMED on a
    comparison that only succeeded because the tool edited the contract first.
    """
    result = _reconcile_with(
        tmp_path,
        PROSE_CONTRACT.replace(
            "retry_contract: deduplicate",
            'effect_identity_key: "idempotency_key\\n"\n    retry_contract: deduplicate'))
    assert "CONFIRMED" not in result.stdout, result.stdout
    assert "UNVERIFIED" in result.stdout, result.stdout
