PY ?= python3

# The nine executable drills, run in protected mode. Names must match the
# DRILLS registry in adapters/in_memory/run_drill.py.
DRILLS ?= worker-dies-agent-survives stale-writer-completes effect-commits-ack-is-lost event-is-lost child-completes-after-join request-accepted-effect-never-applied source-advances-view-answers-anyway state-changes-check-does-not guard-refuses-repair-never-runs

.PHONY: test schema-check prose-check drills check

test:
	$(PY) -m pytest tests/ -q

schema-check:
	$(PY) scripts/schema_check.py

prose-check:
	$(PY) scripts/prose-check.py

drills:
	@set -e; for d in $(DRILLS); do \
		echo "== drill $$d (protected) =="; \
		$(PY) -m adapters.in_memory.run_drill $$d --mode protected; \
	done

check: schema-check prose-check test drills
