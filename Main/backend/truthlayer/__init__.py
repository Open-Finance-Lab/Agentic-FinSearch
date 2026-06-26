"""Canonical XBRL truth layer: companyfacts -> DuckDB -> as_of-parameterized reads.

Pure-Python, no Django/MCP deps, so the axiom resolver, the benchmark grader,
and the Agent Trading Lab can all import it and call it in-process.
"""
