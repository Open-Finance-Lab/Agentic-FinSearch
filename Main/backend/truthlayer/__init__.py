"""Canonical XBRL truth layer: companyfacts -> DuckDB -> as_of-parameterized reads.

Pure-Python, no Django/MCP deps, so the axiom resolver, the benchmark grader,
and the Agent Trading Lab can all import it and call it in-process.
"""

from truthlayer.contracts import Evidence, Period, Query
from truthlayer.retrieve import retrieve_evidence, retrieve_evidence_batch

# Frozen public surface: a caller constructs a Query (with a Period), calls
# retrieve_evidence, and reads back an Evidence. Re-exported here so downstream
# callers (resolver, benchmark grader, Agent Trading Lab) import one stable name.
__all__ = [
    "retrieve_evidence",
    "retrieve_evidence_batch",
    "Query",
    "Period",
    "Evidence",
]
