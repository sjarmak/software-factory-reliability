"""Rule catalog for reviewing factory reliability contracts.

Every rule emits findings rather than raising, so a partial contract still
gets a complete review. An absent section produces an explicit not-declared
finding under the rule that governs it; absence never passes silently.
"""

import re
from dataclasses import dataclass


@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    hint: str
    path: str

    def as_dict(self):
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "hint": self.hint,
            "path": self.path,
        }


SAFE_ENFORCERS = {"publisher", "destination", "store"}
SAFE_OPERATIONS = {"compare-and-set", "transactional"}
ALLOWED_RETRY_CONTRACTS = {"deduplicate", "converge", "reconcile", "at_least_once"}
SOUND_POLICIES = {"block_and_escalate", "reconcile_then_block", "manual_review"}
UNSOUND_POLICIES = {"assume_success", "assume_failure"}
# The exact vocabulary, not a floor. EFFECT-003 used to fail two named bad
# values and pass everything else, so a value added to the schemas and to
# nothing else -- by a fork, by a merge, by a good idea nobody finished --
# reviewed GREEN, because "not one of the two bad ones" is not the same as
# "one of the ones we reasoned about". Failing the unrecognised value is the
# fail-closed direction: the cost of being wrong is a finding somebody reads.
ALLOWED_UNKNOWN_STATE_POLICIES = SOUND_POLICIES | UNSOUND_POLICIES | {"unknown"}
CANONICAL_PROMISES = [
    "ready_to_claim",
    "claimed_to_started",
    "started_to_progress",
    "completed_to_verified",
    "verified_to_published",
    "published_to_acknowledged",
]

# Tokens too generic to establish that a reconciliation entry covers an
# effect destination; coverage needs an overlap on a meaningful token.
GENERIC_TOKENS = {
    "read", "current", "state", "resolve", "find", "by", "for", "the", "a",
    "of", "query", "rerun", "latest", "snapshot", "against", "id", "get",
}


def tokens(text):
    parts = re.split(r"[^0-9A-Za-z]+", str(text or "").lower())
    return {p for p in parts if p and p not in GENERIC_TOKENS}


def _declared(value):
    # Stripped before both tests, and the reason is not tidiness. A field whose
    # whole content is whitespace carries the same information as an absent one,
    # and " unknown " carries the same information as "unknown" -- so if either
    # counted as declared, a contract could clear every not-declared rule in
    # this file with a space bar. That is the exact collapse these rules exist
    # to prevent: the builder who looked and the builder who did not must not
    # produce the same record.
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(value) and value != "unknown"


def _get_dict(container, key):
    value = container.get(key) if isinstance(container, dict) else None
    return value if isinstance(value, dict) else None


def _recon_entries(doc):
    section = doc.get("reconciliation")
    if not isinstance(section, list):
        return []
    return [e for e in section if isinstance(e, dict)]


def entry_covers_effect(entry, effect):
    """An entry with an explicit destination covers exactly the effects of
    that destination. Entries without one fall back to token overlap, which
    is weaker evidence in both directions."""
    declared = entry.get("destination")
    if _declared(declared):
        return declared == effect.get("destination")
    entry_tokens = tokens(entry.get("fact")) | tokens(entry.get("query"))
    effect_tokens = tokens(effect.get("name")) | tokens(effect.get("destination"))
    return bool(entry_tokens & effect_tokens)


def _decided_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def review(doc):
    findings = []
    work = _get_dict(doc, "work")
    _check_authorities(doc, findings)
    _check_identity(doc, work, findings)
    _check_ownership(work, findings)
    _check_effects(doc, work, findings)
    _check_artifacts(doc, findings)
    _check_reconciliation(doc, findings)
    _check_scheduling(doc, findings)
    _check_campaigns(doc, findings)
    _check_observability(doc, findings)
    return findings


def _check_authorities(doc, findings):
    authorities = _get_dict(doc, "authorities")
    planes = ("facts", "procedure", "policy", "effects")
    if authorities is None:
        missing = list(planes)
    else:
        missing = [p for p in planes
                   if not _declared((_get_dict(authorities, p) or {}).get("system"))]
    if missing:
        findings.append(Finding(
            "AUTH-000", "WARN",
            "Authority planes undeclared or undecided: " + ", ".join(missing) +
            ". Without named authorities, one layer can silently impersonate another.",
            "Name the system that owns each plane: facts, procedure, policy, and effects.",
            "authorities"))


def _check_identity(doc, work, findings):
    if work is None:
        findings.append(Finding(
            "IDENT-001", "FAIL",
            "No work section declared: logical, attempt, and session identities are undefined.",
            "Declare work.logical_identity, work.attempt_identity, and work.session_identity as three distinct fields.",
            "work"))
        return
    names = ("logical_identity", "attempt_identity", "session_identity")
    values = {n: work.get(n) for n in names}
    undecided = [n for n, v in values.items() if not _declared(v)]
    if undecided:
        findings.append(Finding(
            "IDENT-001", "FAIL",
            "Identity classes undecided or missing: " + ", ".join(undecided) + ".",
            "Give each identity class its own decided field name; \"unknown\" leaves retries unable to tell item, attempt, and session apart.",
            "work"))
        return
    if len(set(values.values())) != len(names):
        findings.append(Finding(
            "IDENT-001", "FAIL",
            "Identity classes are not mutually distinct: " + ", ".join(str(v) for v in values.values()) + ".",
            "Use three different fields; conflating item, attempt, and session identity is the root of duplicate-effect and stale-writer failures.",
            "work"))


def _check_ownership(work, findings):
    ownership = _get_dict(work, "ownership") if work else None
    if ownership is None:
        findings.append(Finding(
            "AUTH-001", "FAIL",
            "No work.ownership declared: neither a claim generation nor a lease expiry exists.",
            "Declare both: the lease bounds how long a dead owner blocks progress, and the generation fences a stale owner that wakes up after replacement.",
            "work.ownership"))
    else:
        undecided = [n for n in ("generation", "lease_expiry")
                     if not _declared(ownership.get(n))]
        if undecided:
            findings.append(Finding(
                "AUTH-001", "FAIL",
                "Ownership fields undecided or missing: " + ", ".join(undecided) + ".",
                "Declare both generation and lease_expiry; either one alone leaves a failure mode open.",
                "work.ownership"))
    fence = _get_dict(ownership, "fence") if ownership else None
    if fence is None:
        findings.append(Finding(
            "AUTH-002", "FAIL",
            "No ownership fence declared, so nothing rejects a stale writer at the destination.",
            "Declare a fence enforced at the destination (publisher, destination, or store) with a compare-and-set or transactional operation.",
            "work.ownership.fence"))
        return
    enforced = str(fence.get("enforced_by", ""))
    operation = str(fence.get("operation", ""))
    if enforced.lower() == "caller":
        findings.append(Finding(
            "AUTH-002", "FAIL",
            "Fence enforced by the caller: between the caller's ownership check and its write the claim can change hands, a time-of-check to time-of-use race.",
            "Move enforcement to the destination side (publisher, destination, or store), which evaluates the generation atomically with the write.",
            "work.ownership.fence.enforced_by"))
    elif not _declared(enforced) or enforced.lower() not in SAFE_ENFORCERS:
        findings.append(Finding(
            "AUTH-002", "FAIL",
            f"Fence enforcer {enforced or 'missing'!r} is not a recognized destination-side component.",
            "Name the destination side: publisher, destination, or store.",
            "work.ownership.fence.enforced_by"))
    if operation.lower() == "read-then-write":
        findings.append(Finding(
            "AUTH-002", "FAIL",
            "Fence operation is read-then-write: the gap between the read and the write is a time-of-check to time-of-use window a reclaim can slip through.",
            "Use compare-and-set or a transaction so the generation check and the write are one atomic step.",
            "work.ownership.fence.operation"))
    elif not _declared(operation) or operation.lower() not in SAFE_OPERATIONS:
        findings.append(Finding(
            "AUTH-002", "FAIL",
            f"Fence operation {operation or 'missing'!r} is not an atomic check-and-act family.",
            "Use compare-and-set or transactional, evaluated atomically at the destination.",
            "work.ownership.fence.operation"))


def _check_effects(doc, work, findings):
    effects = doc.get("effects")
    if not isinstance(effects, list) or not effects:
        findings.append(Finding(
            "EFFECT-000", "WARN",
            "No effects section declared; retry behavior at each external destination is unspecified.",
            "Declare every class of external mutation with its effect identity, retry contract, and unknown-state policy.",
            "effects"))
        return
    attempt = (work or {}).get("attempt_identity")
    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            continue
        path = f"effects[{i}]"
        name = effect.get("name") or path
        identity = effect.get("effect_identity")
        if not _declared(identity):
            findings.append(Finding(
                "EFFECT-001", "FAIL",
                f"Effect {name} has no decided effect_identity; the destination cannot recognize a repeat.",
                "Give each intended effect a stable logical identity carried unchanged across retries.",
                f"{path}.effect_identity"))
        # Both places an identity can be written, not just the prose one.
        # effect_identity_key was added so a composite identity can stay in
        # prose and still be machine-checkable; a rule that reads only the prose
        # lets an attempt-scoped key in through the new field, and the
        # reconciler then CONFIRMS it against call sites that faithfully carry
        # an identity no destination can deduplicate on.
        #
        # Deliberately NOT nested under "the prose is decided". An undecided
        # prose identity beside a decided attempt-scoped KEY is the worst of the
        # two, and hanging this off the prose reported only that the prose was
        # missing.
        for field in ("effect_identity", "effect_identity_key"):
            value = effect.get(field)
            if not _declared(value):
                continue
            if (_declared(attempt) and attempt in value) or "attempt" in value.lower():
                findings.append(Finding(
                    "IDENT-002", "WARN",
                    f"Effect {name} keys its {field} ({value}) on the attempt identity; every retry mints a new effect id, so no destination can deduplicate.",
                    "Derive the effect identity from the logical work item, not the attempt.",
                    f"{path}.{field}"))
        contract = effect.get("retry_contract")
        if contract not in ALLOWED_RETRY_CONTRACTS:
            findings.append(Finding(
                "EFFECT-002", "FAIL",
                f"Effect {name} has retry_contract {contract!r}; retries redeliver work, and the destination's behavior on a repeat is undefined.",
                "Decide deduplicate, converge, reconcile, or at_least_once for this destination.",
                f"{path}.retry_contract"))
        policy = effect.get("unknown_state_policy")
        if policy in UNSOUND_POLICIES:
            # Both schemas ACCEPT these two, which looks like a gap and is the
            # point. A factory that assumes failure on an ambiguous outcome does
            # not stop doing it because the schema refused the word; the only
            # thing refusing it changes is the record, which then has to read
            # "unknown" -- indistinguishable from a builder who never looked.
            # So the value is writable and this rule fails it, which is strictly
            # more information than a validation error nobody keeps.
            # The two costs are opposite and each has its own precondition.
            # An earlier draft of this said assume_success loses the effect
            # when "the attempt may have landed", which is backwards: if it
            # landed, assuming success happens to be RIGHT. The loss is on the
            # branch where it did NOT land and nothing goes back to check.
            cost = ("assuming success loses the effect: if the attempt did not "
                    "land, nothing will ever go back and check, and the work "
                    "is silently dropped"
                    if policy == "assume_success" else
                    "assuming failure duplicates the effect: if the attempt "
                    "did land, the retry sends a second one")
            findings.append(Finding(
                "EFFECT-003", "FAIL",
                f"Effect {name} has unknown_state_policy {policy}; {cost}.",
                "Declare block_and_escalate, reconcile_then_block, or manual_review so an ambiguous outcome stops and surfaces.",
                f"{path}.unknown_state_policy"))
        elif not _declared(policy):
            findings.append(Finding(
                "EFFECT-003", "FAIL",
                f"Effect {name} has an undecided unknown_state_policy; an ambiguous outcome will be resolved ad hoc under incident pressure.",
                "Decide block_and_escalate, reconcile_then_block, or manual_review before the ambiguity happens.",
                f"{path}.unknown_state_policy"))
        elif policy not in ALLOWED_UNKNOWN_STATE_POLICIES:
            findings.append(Finding(
                "EFFECT-003", "FAIL",
                f"Effect {name} has unknown_state_policy {policy!r}, which these rules do not recognise; whether it stops on an ambiguous outcome or resolves it by guessing is not something this contract establishes.",
                "Use one of: " + ", ".join(sorted(SOUND_POLICIES)) + ". If the factory genuinely does something else, add it to the rules with its cost stated, not only to the schema.",
                f"{path}.unknown_state_policy"))
        # at_least_once is a DECIDED value whose answer is bad, and the enum
        # had no way to say that. A destination with no dedup property leaves
        # a builder three values that are all false and "unknown", which reads
        # as "you have not decided" when the truth is "we decided and repeats
        # duplicate". Recording the bad answer is strictly more information
        # than recording no answer, and it must not be silently green either.
        if contract == "at_least_once" and not _declared(effect.get("duplicate_disposition")):
            findings.append(Finding(
                "EFFECT-005", "FAIL",
                f"Effect {name} declares retry_contract at_least_once and does not say what a duplicate costs; a repeat reaches the destination and nothing here states whether that is acceptable.",
                "Declare duplicate_disposition: what a second copy does at the destination, and what bounds it.",
                f"{path}.duplicate_disposition"))
        # An identity that only the CODE carries. This fires on the honest
        # record the derivation now produces, and it exists so that record
        # cannot be read as a clean bill of health.
        #
        # A marker cannot bind a sentence. When some call sites are prose
        # telling an agent to run the command, this checker can establish the
        # identity wherever the code performs the effect and NOWHERE about the
        # routes the agent takes -- not because the agent omits the identity
        # (an instruction can name the flag, and often does) but because there
        # is no argument list to read until run time.
        #
        # FAIL, not WARN, and this was WARN for one release. The argument for
        # WARN was that the declaration is true of the code. It is, and that is
        # not the question the catalog asks: a route exists that performs an
        # external mutation with no statically established identity discipline,
        # so a retry through it can duplicate. That is an open failure
        # boundary. "The remedy is a design change" is not a severity argument;
        # several FAIL findings here need design changes.
        #
        # No _declared(effect_identity) guard. It used to have one, so an
        # effect with an undecided identity AND instructed routes reported only
        # the generic EFFECT-001 and the instructed routes vanished -- two
        # defects with different remedies, one of them silent. Overlapping
        # effect findings are normal in this catalog.
        instructed = effect.get("instructed_call_sites")
        if isinstance(instructed, int) and instructed > 0:
            findings.append(Finding(
                "EFFECT-006", "FAIL",
                f"Effect {name} is performed at {instructed} call site(s) that are agent instructions rather than code, so no static check can establish that those routes carry the identity; the declaration holds only where the code performs this effect.",
                "Route the effect through a script the agent calls, so the identity is applied by code that can be checked rather than by an instruction whose arguments do not exist until run time.",
                f"{path}.instructed_call_sites"))
        if contract == "reconcile" and not _declared(effect.get("readback")):
            findings.append(Finding(
                "EFFECT-004", "WARN",
                f"Effect {name} declares retry_contract reconcile with no readback; a retry cannot ask the destination whether the prior attempt landed.",
                "Declare the readback query a retry runs before repeating the effect.",
                f"{path}.readback"))


MUTABLE_IDENTITY_TOKENS = {"branch", "ref", "head", "tag", "latest"}
SELF_REPORT_TOKENS = {"self", "testimony"}


def _check_artifacts(doc, findings):
    artifacts = _get_dict(doc, "artifacts")
    identity_value = artifacts.get("identity") if artifacts else None
    if artifacts and tokens(identity_value) & MUTABLE_IDENTITY_TOKENS:
        findings.append(Finding(
            "VERIFY-004", "FAIL",
            f"Artifact identity {identity_value!r} names a mutable reference; whatever it points at can change between verification and publication.",
            "Identify artifacts by an immutable value such as a commit digest; a branch or tag is a pointer, not an identity.",
            "artifacts.identity"))
    verification = _get_dict(artifacts, "verification") if artifacts else None
    if verification and tokens(verification.get("identity")) & SELF_REPORT_TOKENS:
        findings.append(Finding(
            "VERIFY-005", "FAIL",
            f"Verification identity {verification.get('identity')!r} names worker testimony; the worker's claim of completion is the hypothesis, not the observation that establishes it.",
            "Verify through an independent mechanism (CI run, repository state) whose record binds to the artifact identity.",
            "artifacts.verification.identity"))
    if verification is None:
        findings.append(Finding(
            "VERIFY-003", "WARN",
            "No artifacts.verification block; completion rests on worker self-report.",
            "Declare a verification identity bound to the immutable artifact identity.",
            "artifacts.verification"))
    else:
        identity = artifacts.get("identity")
        binds_to = verification.get("binds_to")
        if not _declared(identity) or binds_to != identity:
            mutable = " Binding to a branch or other mutable reference lets verified content drift from published content." \
                if "branch" in str(binds_to).lower() else ""
            findings.append(Finding(
                "VERIFY-001", "FAIL",
                f"Verification binds to {binds_to!r}, not the artifact identity {identity!r}.{mutable}",
                "Set verification.binds_to equal to artifacts.identity, an immutable identity such as a commit digest.",
                "artifacts.verification.binds_to"))
    publication = _get_dict(artifacts, "publication") if artifacts else None
    if publication is None:
        findings.append(Finding(
            "VERIFY-002", "FAIL",
            "No artifacts.publication conditions declared; nothing rechecks ownership or evidence at publish time.",
            "Declare publication.conditions including current_generation and verification_matches_artifact.",
            "artifacts.publication"))
        return
    conditions = publication.get("conditions") or []
    missing = [c for c in ("current_generation", "verification_matches_artifact")
               if c not in conditions]
    if missing:
        findings.append(Finding(
            "VERIFY-002", "FAIL",
            "Publication conditions omit " + ", ".join(missing) + "; a publish can ship under a lost claim or with evidence for a different artifact.",
            "Recheck both current_generation and verification_matches_artifact atomically at the destination.",
            "artifacts.publication.conditions"))


def _check_reconciliation(doc, findings):
    entries = _recon_entries(doc)
    effects = doc.get("effects") if isinstance(doc.get("effects"), list) else []
    # RECON-001 is a statement about a DESTINATION, not about an effect: the
    # remediation is one reconciliation entry that covers the destination, no
    # matter how many effects target it. Reporting it per-effect emitted the
    # identical message and hint once per effect, so a factory with five effects
    # across two uncovered destinations raised five warnings for two problems and
    # its warning count tracked effect multiplicity rather than missing coverage.
    # Report once per uncovered destination, naming every effect that relies on it.
    uncovered = {}
    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            continue
        if any(entry_covers_effect(entry, effect) for entry in entries):
            continue
        destination = effect.get("destination", "unknown destination")
        uncovered.setdefault(destination, []).append((i, effect.get("name")))
    for destination, affected in uncovered.items():
        names = ", ".join(str(name) for _, name in affected if name)
        via = f" Effects relying on it: {names}." if names else ""
        findings.append(Finding(
            "RECON-001", "WARN",
            f"No reconciliation entry covers effect destination {destination}; the factory trusts its cached belief about that system's state.{via}",
            "Add a reconciliation entry with destination set to this effect's destination, whose query rereads its actual state on a cadence.",
            f"effects[{affected[0][0]}].destination"))
    if not any("session" in str(e.get("fact", "")).lower()
               or "session" in str(e.get("query", "")).lower()
               for e in entries):
        findings.append(Finding(
            "RECON-002", "WARN",
            "No reconciliation entry resolves the running session for the current claim; a retry cannot start-or-attach, only launch blind.",
            "Add an entry such as fact running_session with query resolve_session_for_current_claim.",
            "reconciliation"))


def _check_scheduling(doc, findings):
    scheduling = _get_dict(doc, "scheduling")
    classes = _get_dict(scheduling, "classes") if scheduling else None
    recovery = _get_dict(classes, "recovery") if classes else None
    if recovery is None or not _decided_number(recovery.get("maximum")):
        findings.append(Finding(
            "FLEET-001", "WARN",
            "No recovery class with a decided numeric maximum; an undecided limit bounds nothing, and a retry storm can occupy every slot in the pool.",
            "Declare scheduling.classes.recovery with a hard numeric maximum.",
            "scheduling.classes.recovery"))
    has_interactive_reserve = any(
        "interactive" in name.lower() and _decided_number(spec.get("reserved"))
        for name, spec in (classes or {}).items()
        if isinstance(spec, dict))
    if not has_interactive_reserve:
        findings.append(Finding(
            "FLEET-002", "WARN",
            "No class reserves capacity for interactive work; human-facing work queues behind bulk and recovery load.",
            "Declare an interactive class with reserved slots that other classes cannot take.",
            "scheduling.classes"))
    fairness = _get_dict(scheduling, "fairness") if scheduling else None
    levels = fairness.get("levels") if fairness else None
    if not levels:
        findings.append(Finding(
            "FLEET-003", "WARN",
            "No fairness levels declared; a single tenant or repository can hold the whole pool.",
            "Declare scheduling.fairness.levels, outermost dimension first, for example [tenant, repository, priority].",
            "scheduling.fairness.levels"))


def _check_campaigns(doc, findings):
    campaigns = _get_dict(doc, "campaigns")
    if campaigns is None:
        return
    code_estate = _get_dict(doc, "code_estate")
    if code_estate is None or not _declared(code_estate.get("canonical_identity")):
        findings.append(Finding(
            "CODE-000", "WARN",
            "Campaigns declared with no decided code_estate; target discovery and completion cannot be checked against current repository state.",
            "Declare code_estate.canonical_identity (repository@revision) and a topology provider the campaign reruns discovery through.",
            "code_estate"))
    completion = _get_dict(campaigns, "completion") or {}
    keys = set(completion)
    other = sorted(keys - {"all_current_targets_have_disposition"})
    if "all_current_targets_have_disposition" not in keys or other:
        style = "; declared style: " + ", ".join(other) if other else ""
        findings.append(Finding(
            "CAMP-001", "FAIL",
            "Campaign completion is not disposition-based over current targets" + style +
            ". Child-task completion says nothing about targets discovered after kickoff or tasks that silently dropped a target.",
            "Complete a campaign only when every current target carries published, exempted, or blocked_with_owner.",
            "campaigns.completion"))


def _check_observability(doc, findings):
    observability = _get_dict(doc, "observability")
    promises = observability.get("promises") if observability else None
    declared = promises if isinstance(promises, list) else []
    missing = [p for p in CANONICAL_PROMISES if p not in declared]
    if missing:
        prefix = ("No observability section declared; unwatched lifecycle transitions: "
                  if observability is None
                  else "Observability promises omit lifecycle transitions: ")
        findings.append(Finding(
            "OBS-001", "WARN",
            prefix + ", ".join(missing) + ".",
            "Watch all six canonical promises so a stall between states is actionable without any component reporting an error.",
            "observability.promises"))
