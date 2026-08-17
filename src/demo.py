#!/usr/bin/env python3
"""The five-minute demo: one factory, one fault, two enforcement points.

Runs the stale-writer drill twice against the in-memory reference factory,
once with the ownership fence checked by the caller and once with it checked
at the destination, and narrates both runs from the event log the runs
actually produced.

Every line below the mode header is rendered from an event in
out/evidence/stale-writer-completes-<mode>.json. Nothing here is a canned
transcript: an event the narrator does not recognize prints as its raw kind,
and a run whose oracle verdict is not the expected one fails the demo.

Usage:
    python3 src/demo.py [--out-dir out/evidence]

Exit status 0 when the unsafe run violated its oracle and the protected run
passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - script-invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapters.in_memory.run_drill import run_drill  # noqa: E402

DRILL = "stale-writer-completes"

MODE_HEADERS = {
    "unsafe": ("UNSAFE", "fence checked by the writer, then an "
                         "unconditional write"),
    "protected": ("PROTECTED", "fence checked at the destination, atomically "
                               "with the write"),
}

EXPECTED_VERDICT = {"unsafe": "violation", "protected": "pass"}

# The window worth narrating starts when the fault lands. Everything before
# it is identical in both runs and is summarized in one setup line.
FIRST_NARRATED_KIND = "lease-expired"

RESULT_COLUMN = 62


def _generation_of_lease(lease: str | None) -> str:
    """'lease-g7' names generation 7. Anything else narrates as itself."""
    if isinstance(lease, str) and lease.startswith("lease-g"):
        return lease[len("lease-g"):]
    return str(lease)


def narrate(event: dict, current_gen: int | None) -> list[str]:
    """The lines for one event; empty for events outside the story.

    current_gen is the generation the log says is current at this point, so
    a write can be called stale on the evidence rather than on the mode name.
    An event kind with no entry here is narrated as its raw kind rather than
    dropped, so a changed simulator shows up in the demo instead of being
    smoothed over by a fixed script.
    """
    kind = event.get("kind")
    gen = event.get("generation")
    current = event.get("current_generation")
    artifact = event.get("artifact_id")
    if kind in ("barrier-reached", "fault-injected", "worker-started",
                "session-launched", "artifact-prepared"):
        return []
    if kind == "lease-expired":
        lease_gen = _generation_of_lease(event.get("lease"))
        return [f"generation {lease_gen} loses ownership"]
    if kind == "generation-advanced":
        return [f"generation {gen} becomes current"]
    if kind == "publish-accepted":
        return [f"generation {gen} publishes {artifact}"]
    if kind == "publish-unfenced-applied":
        stale = (isinstance(gen, int) and isinstance(current_gen, int)
                 and gen < current_gen)
        if stale:
            return [f"generation {gen} writes {artifact} anyway",
                    "destination applies it, nothing checks the generation"]
        return [f"generation {gen} writes {artifact}"]
    if kind == "publish-unfenced-skipped":
        return [f"generation {gen} skips its write on a caller-side check"]
    if kind == "publish-rejected-stale":
        return [f"generation {gen} attempts to publish {artifact}",
                f"destination rejects the stale writer, current is {current}"]
    if kind == "outcome-accepted":
        return [f"generation {gen} records the work complete"]
    if kind == "completion-rejected-stale":
        return [f"generation {gen} attempts to record completion",
                "destination rejects the stale completion"]
    detail = {k: v for k, v in sorted(event.items()) if k != "kind"}
    return [f"[{kind}] {json.dumps(detail)}"]


def setup_line(events: list[dict]) -> str:
    """One sentence for the state at the moment the fault is injected."""
    claim = next((e for e in events if e.get("kind") == "work-claimed"), None)
    prepared = next((e for e in events
                     if e.get("kind") == "artifact-prepared"), None)
    if claim is None or prepared is None:
        return "setup events missing from the log"
    return (f"generation {claim.get('generation')} holds the claim and has "
            f"prepared {prepared.get('artifact_id')}")


def render(evidence: dict) -> tuple[str, bool]:
    """The narrated block for one run, and whether its oracle behaved."""
    mode = evidence["mode"]
    label, enforcement = MODE_HEADERS[mode]
    events = evidence["events"]
    oracle = evidence["oracle"]
    verdict = oracle["verdict"]
    as_expected = verdict == EXPECTED_VERDICT[mode]

    start = next((i for i, e in enumerate(events)
                  if e.get("kind") == FIRST_NARRATED_KIND), 0)
    lines = [f"{label}  ({enforcement})", f"  {setup_line(events)}"]
    current_gen = None
    for index, event in enumerate(events):
        if event.get("kind") in ("work-added", "work-claimed",
                                 "generation-advanced"):
            current_gen = event.get("generation", current_gen)
        if index >= start:
            lines.extend(f"  {text}" for text in narrate(event, current_gen))

    artifact = (evidence["external_effects"].get("artifact") or {})
    held = artifact.get("artifact_id", "nothing")
    result = "PASS" if verdict == "pass" else "FAIL"
    if not as_expected:
        result = f"UNEXPECTED ({verdict})"
    closing = f"  destination holds {held}"
    lines.append(f"{closing.ljust(RESULT_COLUMN)}{result}")
    return "\n".join(lines), as_expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 src/demo.py",
        description="Run the stale-writer drill in both modes and narrate "
                    "the two event logs.")
    parser.add_argument("--out-dir", default="out/evidence")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    blocks = []
    ok = True
    for mode in ("unsafe", "protected"):
        # The runner's own summary line duplicates what the narration below
        # says. Diagnostics go to stderr and are left alone.
        with contextlib.redirect_stdout(io.StringIO()):
            code = run_drill(DRILL, mode, out_dir)
        expected_code = 2 if mode == "unsafe" else 0
        evidence_file = out_dir / f"{DRILL}-{mode}.json"
        if code not in (0, 2) or not evidence_file.exists():
            print(f"the {mode} run did not complete (exit {code}); "
                  f"no narration is possible", file=sys.stderr)
            return 1
        if code != expected_code:
            ok = False
        evidence = json.loads(evidence_file.read_text())
        block, as_expected = render(evidence)
        blocks.append(block)
        ok = ok and as_expected

    print()
    print("One work item, one lease expiry, two factories. The factories "
          "differ in")
    print("exactly one place: where the ownership fence is checked.")
    print()
    print("\n\n".join(blocks))
    print()
    print("The unsafe factory lost generation 8's work and published a "
          "superseded")
    print("artifact under a completion record that says everything went "
          "fine. This")
    print('is the failure the essay describes in "Authority has to expire '
          'cleanly".')
    print()
    print("  pattern   patterns/fenced-authority.md")
    print(f"  drill     drills/{DRILL}/DRILL.md")
    print(f"  evidence  {out_dir}/{DRILL}-" + "{unsafe,protected}.json")
    print("  next      make drills   (runs all nine executable drills)")
    print()

    if not ok:
        print("At least one run did not produce its expected oracle verdict. "
              "The narration above is still what happened; the demo's own "
              "claim is what failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
