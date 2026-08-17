# Coverage Report: cmp-apimig-01

The coverage reduction for the campaign in [campaign.yaml](campaign.yaml),
in the format defined by
[docs/observability/campaign-coverage.md](../../../docs/observability/campaign-coverage.md).
Generated from a fresh discovery pass plus the durable event record; the
client-go and cli child event streams are in
[docs/observability/sample-events.jsonl](../../../docs/observability/sample-events.jsonl),
and the per-child manifests are in [work-manifests/](work-manifests/).

```
Campaign:   cmp-apimig-01
Intent:     no hand-written old.API call sites outside service-api
Discovery:  2026-08-08T06:00:00Z  query "symbol:old.API -repo:service-api"
            against code-index@2026-08-08
Origin:     service-api @ 7e6f5a4b3c2d (v2.Client available since 2026-08-01)

Current targets (discovery pass of 2026-08-08):
  client-go   @ 8f7e6d5c4b3a
  client-js   @ 2a3b4c5d6e7f
  cli         @ 9a8b7c6d5e4f
  web         @ 6c5d4e3f2a1b

Published (2 of 4):
  client-go   wi-2101  pub-3320  sha256:4b8e...d8e6b  acknowledged 2026-08-06
              evidence: commit 0c1d2e3f4a5b is ancestor of client-go/main
  client-js   wi-2103  pub-3327  sha256:c1a9...44f2e  acknowledged 2026-08-07
              evidence: commit 7b8c9d0e1f2a is ancestor of client-js/main

Blocked (1 of 4):
  cli         wi-2102  since 2026-08-06  owner: platform-team
              reason: downstream batch API is incompatible with the v2
              client; migration cannot proceed until platform-team ships
              the compatible endpoint

Exempted (1 of 4):
  web         wi-2104  exempted 2026-08-07  review by 2026-09-30
              reason: web's hand-written calls are scheduled for
              replacement by a generated v2 client in September 2026;
              migrating them now would produce work discarded within weeks

Stale work:            none
Undispositioned:       none

Campaign state: BLOCKED
  2 published, 1 blocked, 1 exempted, 0 stale, 0 undispositioned.
  Counts sum: 2 + 1 + 1 + 0 + 0 = 4 current targets.
```

## Completion under the declared rule

The campaign declares completion as `all_current_targets_have_disposition:
[published, exempted, blocked_with_owner]`, and this pass satisfies it:
every target the close-time discovery found carries an accountable
disposition, no target is undispositioned, no in-flight child is stale,
the one block names an owner, and the one exemption carries a review date.
There is no coverage hole.

The report state is BLOCKED rather than COMPLETE because the state
vocabulary reserves COMPLETE for every target published or exempted; a
blocked target is accounted for, not finished. The block surfaces in
platform-team's escalation lane. The web exemption expires 2026-09-30 and
reverts to undispositioned if not reconfirmed. Any later discovery pass
that finds a new old.API call site adds an undispositioned target and
moves the campaign back to OPEN; completion is a statement about the
revisions this pass observed, at the time it ran.
