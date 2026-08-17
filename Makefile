PY ?= python3

# The nine executable drills, run in protected mode. Names must match the
# DRILLS registry in src/adapters/in_memory/run_drill.py.
DRILLS ?= worker-dies-agent-survives stale-writer-completes effect-commits-ack-is-lost event-is-lost child-completes-after-join request-accepted-effect-never-applied source-advances-view-answers-anyway state-changes-check-does-not guard-refuses-repair-never-runs

# make drill DRILL=<name> MODE=<protected|unsafe>
DRILL ?= stale-writer-completes
MODE ?= protected

.PHONY: demo drill test schema-check prose-check drills check

demo:
	@$(PY) src/demo.py

drill:
	$(PY) src/adapters/in_memory/run_drill.py $(DRILL) --mode $(MODE)

test:
	$(PY) -m pytest tests/ -q

schema-check:
	$(PY) src/checks/schema_check.py

prose-check:
	$(PY) src/checks/prose_check.py

drills:
	@set -e; for d in $(DRILLS); do \
		echo "== drill $$d (protected) =="; \
		$(PY) src/adapters/in_memory/run_drill.py $$d --mode protected; \
	done

check: schema-check prose-check test drills
