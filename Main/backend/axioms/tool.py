"""`report_claim` agent tool for Layer 1 Validate.

Registers a numerical ratio claim the agent has emitted into a response,
so that the user can later trigger deterministic validation against XBRL
ground truth via the /api/axioms/validate/ endpoint.

Implemented as a native `agents.function_tool` (not an MCP server) because
it needs direct access to Django cache and session context — spinning up a
new stdio MCP server just to write to cache would be pure overhead.
"""

from __future__ import annotations

import json
import logging
from typing import List

from agents import function_tool

from axioms import SUPPORTED_RATIOS
from axioms.registry import add_claim

logger = logging.getLogger(__name__)


def get_axiom_tools(session_id: str) -> List:
    """Return a list of agent tools bound to the given session_id.

    The session_id is captured in the closure so each MCP call routes
    the claim to the correct user's registry entry without threading
    context through every tool invocation.
    """

    @function_tool
    def report_claim(
        ratio: str,
        ticker: str,
        period: str,
        claimed_value: float,
        formula_inputs: str,
    ) -> str:
        """Record a numerical ratio claim so the user can verify it against
        SEC XBRL ground truth. See system-prompt sections RELEVANCE GATE and
        RATIO CLAIMS for the full contract; the rules below are the binding
        summary.

        Supported ratios (the ONLY ones the Validate pipeline can verify):
          - accounting_equation: A = L + Temporary Equity + E. Pass Total Assets as claimed_value.
          - gross_margin: percentage (e.g., 44.13 for 44.13%).
          - current_ratio: dimensionless (e.g., 0.9880).

        WHEN TO CALL (RELEVANCE GATE; emit ZERO claims unless this matches):
          Emit a claim ONLY when one of the supported ratios above is the
          DIRECT subject of the user's question, either as Q&A ("What is
          Apple's gross margin?") or as a user-supplied number to be
          fact-checked. Do NOT call this for tangential mentions,
          "supporting metrics", or ratio-padded "Financial Snapshot" sections
          appended to an unrelated answer. If the user asks about EPS, P/E,
          net income, revenue totals, market cap, ROE, ROA, dividend yield,
          or any other unsupported metric, do NOT call this tool at all.

        WHAT TO PASS AS claimed_value (Rule 1):
          - Validate-user-claim flow: phrasings like "validate", "verify",
            "fact-check", "double-check", "sanity-check", "cross-check",
            "is this right/true/correct/accurate", or any analyst/document
            figure quoted as something to be checked. claimed_value MUST be
            the user's stated number, NOT a value you computed yourself,
            even if your prose shows a discrepancy. Substituting your
            correction defeats the verification.
          - Normal Q&A flow: claimed_value is the value you computed and
            reported as the answer.

        Emit one claim per ratio per (ticker, period). Not in a loop, not per
        paragraph. Do NOT mention this tool, the Validate button, or the claim
        registry to the user; the Validate UX is rendered by the frontend.

        Args:
            ratio: One of the SUPPORTED_RATIOS names.
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT", "TSLA").
            period: Fiscal period end date as ISO YYYY-MM-DD.
            claimed_value: Numeric value being claimed (see "WHAT TO PASS"
                           above for which value to use).
            formula_inputs: JSON object string with ratio-specific keys.
                            accounting_equation: {"assets","liabilities","equity"}
                            gross_margin:        {"revenue","cogs"}
                            current_ratio:       {"current_assets","current_liabilities"}
                            Recorded for audit only; the engine compares
                            claimed_value to XBRL ground truth directly and
                            never uses formula_inputs in the verdict.
        """
        if ratio not in SUPPORTED_RATIOS:
            return (
                f"ERROR: '{ratio}' is not a supported ratio. "
                f"Supported: {', '.join(SUPPORTED_RATIOS)}."
            )
        try:
            inputs = json.loads(formula_inputs) if formula_inputs else {}
            if not isinstance(inputs, dict):
                raise ValueError("formula_inputs must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return f"ERROR: could not parse formula_inputs as JSON object: {exc}"

        claim = {
            "ratio": ratio,
            "ticker": ticker.upper(),
            "period": period,
            "claimed_value": float(claimed_value),
            "formula_inputs": inputs,
        }
        try:
            add_claim(session_id, claim)
        except Exception as exc:
            logger.exception("Failed to record axiom claim")
            return f"ERROR: failed to record claim: {exc}"

        return (
            f"Claim recorded: {ratio} for {ticker.upper()} @ {period} = "
            f"{claimed_value}. User may click Validate to verify against SEC filing."
        )

    return [report_claim]
