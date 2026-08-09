"""In-memory reference factory and its protocol adapter.

Deterministic: logical ticks only, no wall-clock, no randomness. The
factory model lives in model.py, the protocol adapter in adapter.py
(importable API plus an optional stdio loop), and the drill driver in
run_drill.py. Imports stay out of this module so that python3 -m
adapters.in_memory.adapter runs without a double-import warning.
"""
