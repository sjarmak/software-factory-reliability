"""Rendering of a factory reliability contract into diagrams and tables."""

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules


def _node_id(prefix, name):
    return prefix + "_" + re.sub(r"[^0-9A-Za-z]+", "_", str(name)).strip("_")


def _effects(doc):
    section = doc.get("effects")
    if not isinstance(section, list):
        return []
    return [e for e in section if isinstance(e, dict)]


def _recon(doc):
    section = doc.get("reconciliation")
    if not isinstance(section, list):
        return []
    return [e for e in section if isinstance(e, dict)]


def _factory_name(doc):
    factory = doc.get("factory")
    if isinstance(factory, dict) and factory.get("name"):
        return str(factory["name"])
    return "unnamed factory"


def authority_map(doc):
    authorities = doc.get("authorities")
    authorities = authorities if isinstance(authorities, dict) else {}
    lines = ["flowchart LR", '  subgraph factory_authorities["Factory authorities"]']
    for role in ("facts", "procedure", "policy", "effects"):
        entry = authorities.get(role)
        entry = entry if isinstance(entry, dict) else {}
        system = entry.get("system", "not declared")
        record = entry.get("record")
        label = f"{role}: {system}" + (f" ({record})" if record else "")
        lines.append(f'    auth_{role}["{label}"]')
    lines.append("  end")
    destinations = []
    for effect in _effects(doc):
        destination = effect.get("destination", "unknown")
        if destination not in destinations:
            destinations.append(destination)
    if destinations:
        lines.append('  subgraph external_systems["External systems"]')
        for destination in destinations:
            lines.append(f'    {_node_id("ext", destination)}["{destination}"]')
        lines.append("  end")
    lines.append("  auth_procedure -->|revalidates ownership against| auth_facts")
    lines.append("  auth_policy -->|admits and places work using| auth_facts")
    lines.append("  auth_effects -->|claims and fences through| auth_facts")
    entries = _recon(doc)
    for effect in _effects(doc):
        destination = effect.get("destination", "unknown")
        if any(rules.entry_covers_effect(entry, effect) for entry in entries):
            edge = f'  {_node_id("ext", destination)} -.->|reconciled into| auth_facts'
            if edge not in lines:
                lines.append(edge)
    for destination in destinations:
        lines.append(f'  auth_effects -->|mutates| {_node_id("ext", destination)}')
    return "\n".join(lines) + "\n"


def work_lifecycle(doc):
    lines = [
        "stateDiagram-v2",
        "  [*] --> ready",
        "  ready --> claimed: claim under lease and generation",
        "  claimed --> executing: start or attach session",
        "  executing --> artifact_prepared: prepare artifact",
        "  artifact_prepared --> verified: verification binds artifact identity",
        "  verified --> published: publish under rechecked conditions",
        "  published --> acknowledged: destination acknowledges",
        "  acknowledged --> [*]",
        "  executing --> blocked: unknown outcome or lost owner",
        "  blocked --> ready: reclaim increments generation",
    ]
    for entry in _recon(doc):
        fact = entry.get("fact", "unnamed fact")
        interval = entry.get("interval") or "interval unknown"
        query = str(entry.get("query", ""))
        if "session" in str(fact).lower() or "session" in query.lower():
            lines.append(
                f"  claimed --> executing: reconcile {fact} every {interval} (start or attach)")
        else:
            lines.append(f"  executing --> executing: reconcile {fact} every {interval}")
    return "\n".join(lines) + "\n"


def effect_matrix(doc):
    lines = [f"# Effect matrix: {_factory_name(doc)}", ""]
    effects = _effects(doc)
    if not effects:
        lines.append("No effects declared.")
        return "\n".join(lines) + "\n"
    recon = _recon(doc)
    # Identity key gets its own column. Two contracts with identical prose and
    # different keys are treated differently by reconcile and by IDENT-002, and
    # a matrix that shows only the prose renders them as the same row.
    lines.append("| Effect | Destination | Effect identity | Identity key | Retry contract | Readback | Unknown-state policy | Reconciled by |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for effect in effects:
        covering = [
            str(entry.get("fact", ""))
            for entry in recon
            if rules.entry_covers_effect(entry, effect)
        ]
        row = [
            str(effect.get("name", "not declared")),
            str(effect.get("destination", "not declared")),
            str(effect.get("effect_identity", "not declared")),
            str(effect.get("effect_identity_key") or "the identity above"),
            str(effect.get("retry_contract", "not declared")),
            str(effect.get("readback") or "none"),
            str(effect.get("unknown_state_policy") or "not declared"),
            ", ".join(c for c in covering if c) or "none",
        ]
        lines.append("| " + " | ".join(row) + " |")
    # Effects that duplicate get their disposition spelled out under the table
    # rather than squeezed into a cell. It is a sentence, and it is the whole
    # answer to "what does a retry cost here" -- the row above says only that a
    # repeat lands, which reads as a shrug without the bound beside it.
    duplicating = [e for e in effects
                   if isinstance(e, dict) and e.get("retry_contract") == "at_least_once"]
    if duplicating:
        lines.extend(["", "## Effects that duplicate on a repeat", ""])
        for effect in duplicating:
            lines.append("- **%s** -> %s: %s" % (
                effect.get("name", "not declared"),
                effect.get("destination", "not declared"),
                effect.get("duplicate_disposition") or "no disposition declared"))
    return "\n".join(lines) + "\n"


def _evidence_path_exists(reference, contract_path):
    """Resolve a claimed evidence path against the working directory and the
    contract's directory tree; a fault-tested claim whose evidence resolves
    nowhere is reported, not displayed as tested."""
    candidate = Path(str(reference))
    if candidate.is_absolute():
        return candidate.exists()
    roots = [Path.cwd()]
    roots.extend(Path(contract_path).resolve().parents)
    return any((root / candidate).exists() for root in roots)


def guarantee_ledger(doc, contract_path):
    lines = [f"# Guarantee ledger: {_factory_name(doc)}", ""]
    guarantee_dir = Path(contract_path).resolve().parent / "guarantees"
    files = sorted(guarantee_dir.glob("*.yaml")) if guarantee_dir.is_dir() else []
    entries = []
    for path in files:
        try:
            loaded = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(loaded, dict):
            entries.append((path.name, loaded))
    if not entries:
        lines.append("no guarantees recorded")
        return "\n".join(lines) + "\n"
    lines.append("| Guarantee | Evidence status | Owner | Mechanism | Last verified |")
    lines.append("|---|---|---|---|---|")
    for _, g in entries:
        evidence = g.get("evidence") if isinstance(g.get("evidence"), dict) else {}
        mechanism = g.get("mechanism") if isinstance(g.get("mechanism"), dict) else {}
        status = str(evidence.get("status", "not recorded"))
        if status == "fault-tested":
            missing = [k for k in ("drill", "artifact")
                       if evidence.get(k)
                       and not _evidence_path_exists(evidence[k], contract_path)]
            if missing:
                status = "fault-tested (EVIDENCE MISSING: " + ", ".join(missing) + ")"
        lines.append("| " + " | ".join([
            str(g.get("id", "unnamed")),
            status,
            str(g.get("owner", "not recorded")),
            str(mechanism.get("type", "not recorded")),
            str(evidence.get("last_verified", "never")),
        ]) + " |")
    for filename, g in entries:
        lines.append("")
        lines.append(f"## {g.get('id', filename)}")
        lines.append("")
        lines.append(str(g.get("claim", "no claim recorded")).strip())
        evidence = g.get("evidence") if isinstance(g.get("evidence"), dict) else {}
        for key in ("drill", "artifact"):
            if evidence.get(key):
                found = _evidence_path_exists(evidence[key], contract_path)
                suffix = "" if found else "  (path does not resolve)"
                lines.append(f"- {key}: {evidence[key]}{suffix}")
    return "\n".join(lines) + "\n"


def promise_map(doc):
    lines = [f"# Promise map: {_factory_name(doc)}", ""]
    observability = doc.get("observability")
    declared = []
    if isinstance(observability, dict) and isinstance(observability.get("promises"), list):
        declared = [str(p) for p in observability["promises"]]
    else:
        lines.append("No observability section declared.")
        lines.append("")
    lines.append("Declared promise transitions:")
    if declared:
        lines.extend(f"- {p}" for p in declared)
    else:
        lines.append("- (none)")
    missing = [p for p in rules.CANONICAL_PROMISES if p not in declared]
    lines.append("")
    lines.append("Missing from the canonical six:")
    if missing:
        lines.extend(f"- {p}" for p in missing)
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def render_all(doc, contract_path, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        ("authority-map.mmd", authority_map(doc)),
        ("work-lifecycle.mmd", work_lifecycle(doc)),
        ("effect-matrix.md", effect_matrix(doc)),
        ("guarantee-ledger.md", guarantee_ledger(doc, contract_path)),
        ("promise-map.md", promise_map(doc)),
    ]
    written = []
    for name, text in outputs:
        path = out_dir / name
        path.write_text(text)
        written.append(path)
    return written
