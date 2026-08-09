"""Factory adapters.

An adapter speaks the protocol defined in adapters/protocol.schema.json
(JSON over stdio, one message per line) against a concrete factory. The
in-memory reference implementation lives in adapters.in_memory; adapters
for real factories implement the same ten ops against real systems. See
adapters/README.md for the contract.
"""
