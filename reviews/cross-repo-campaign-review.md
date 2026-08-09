# Cross-repo campaign review checklist

Scope: campaigns that fan work out across many repositories or targets:
discovery, child identity, dispositions, and coverage recheck. Each question
carries the review rule id it maps to or the drill it corresponds to.
Automated findings carry rule id, severity, a message stating the defect, and
a one-line remediation hint; a run exits 1 on any FAIL, or on any WARN under
--strict, else 0.

1. Is campaign completion disposition-based over the current target set? Any
   completion rule of the form "all scheduled children finished" fails,
   because it measures the plan, not the world. [CAMP-001]
2. Is discovery a query over current state that can be rerun at any time, or
   a list remembered from campaign start? A remembered list cannot see a
   target that appeared after discovery ran.
   [drill campaign-coverage-drifts]
3. Does close evaluation rerun discovery and require a disposition for every
   target the close-time query returns? Walk the scenario: children finish,
   a new target appeared meanwhile; show the check that keeps the campaign
   open. [drill campaign-coverage-drifts]
4. Is the allowed disposition set enumerated (completed with evidence,
   skipped with reason, quarantined, superseded, scheduled), and does every
   disposition record carry its reason and its evidence? [CAMP-001]
5. Does each child carry a stable logical identity derived from
   (campaign id, target id), distinct from its attempt and session
   identities? [IDENT-001]
6. Can a discovery rerun mint a duplicate child for an already-covered
   target? The dedup check must query the same store the children live in;
   a dedup query against a different store misses the duplicate it exists to
   prevent. [IDENT-001]
7. Are child retries new attempts of the same logical child rather than new
   children, so a late result from an expired attempt cannot overwrite a
   successful retry? [IDENT-002]
8. Is per-target completion derived from destination facts (the artifact
   landed, the change is an ancestor of the target's main line) rather than
   from child status records? A closed child is a status signal, not an
   outcome signal. [VERIFY-003]
9. For each target repository: is the base state identity recorded at
   planning time as an immutable input of the child, and does publication
   compare the declared base to the destination's current state at an atomic
   reference update? [drill repository-base-moves]
10. When the base has moved, does the child receive an explicit disposition
    (rebase and re-verify, replan, quarantine, reject), and is a silent
    publish structurally impossible rather than merely discouraged?
    [drill repository-base-moves]
11. If discovery reads an index, is the index generation bound to the
    repository state identity it was built from, and does a stale index
    surface as an explicit freshness failure rather than a fluent answer
    over old state? [drill repository-base-moves]
12. Does a rebased or amended child artifact require a verdict naming its
    new identity before publication, so no verdict crosses bases or
    identities? [VERIFY-001]
13. Are campaign lifecycle transitions (opened, discovery completed, child
    scheduled, child terminal, coverage recheck, closed) all promised
    observations, so a stranded campaign is visible to a scan rather than
    discovered by a human months later? [OBS-001]
14. Is there a level-triggered sweep, outside the campaign's own failure
    domain, that detects targets which drifted in after final close and
    routes them to a new campaign or an explicit disposition?
    [drill campaign-coverage-drifts]
15. At close, does the record enumerate the verified target set and each
    target's disposition, so a later audit can recompute coverage from
    retained evidence instead of trusting the close event? [CAMP-001]
