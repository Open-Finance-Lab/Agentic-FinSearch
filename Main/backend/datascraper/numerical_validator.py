"""
Post-generation numerical validation for financial agent responses.

Compares numbers in the agent's final output against numbers from tool outputs
to detect LLM hallucination of financial figures (e.g., $190.66 vs $190.68)
and orphan numbers not traceable to any source.
"""

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Set

logger = logging.getLogger(__name__)

# Pattern to match financial numbers (integers, decimals, percentages)
_NUMBER_PATTERN = re.compile(
    r'(?<!\w)'           # not preceded by word char
    r'-?'                # optional negative sign
    r'\$?'               # optional dollar sign
    r'(\d{1,3}(?:,?\d{3})*'  # integer with optional comma separators (comma optional for raw JSON integers)
    r'(?:\.\d+)?)'       # optional decimal part
    r'%?'                # optional percent sign
    r'(?!\w)',           # not followed by word char
)


@dataclass
class ValidationResult:
    """Structured result from numerical validation."""
    exact_matches: int = 0                        # Numbers matching tool output exactly
    close_matches: list = field(default_factory=list)   # (resp_num, tool_num, diff) within 1%
    orphan_numbers: list = field(default_factory=list)   # Numbers not traceable to any source
    suspicious: list = field(default_factory=list)       # Close-but-wrong values


def _extract_numbers(text: str) -> Set[str]:
    """
    Extract all numerical values from text, normalized (commas removed).
    Returns a set of string representations for exact matching.
    """
    numbers = set()
    for match in _NUMBER_PATTERN.finditer(text):
        num_str = match.group(1).replace(',', '')
        # Skip very short numbers (likely not financial data)
        try:
            val = float(num_str)
            # Only track numbers that look like financial data (> 0.001 and not just 0, 1, 2...)
            if val > 0.01 and len(num_str) > 1:
                numbers.add(num_str)
        except ValueError:
            continue
    return numbers


def _extract_tool_output_numbers(run_result) -> Set[str]:
    """
    Extract numbers from tool call outputs in the agent run result.
    """
    tool_numbers = set()
    try:
        items = getattr(run_result, 'new_items', None) or []
        for item in items:
            item_type = getattr(item, 'type', '')
            if item_type == 'tool_call_output_item':
                output = getattr(item, 'output', '')
                if output:
                    # Try parsing as JSON first (structured tool output)
                    try:
                        parsed = json.loads(output) if isinstance(output, str) else output
                        tool_numbers.update(_extract_numbers(json.dumps(parsed)))
                    except (json.JSONDecodeError, TypeError):
                        tool_numbers.update(_extract_numbers(str(output)))
    except Exception as e:
        logger.debug(f"[NUM VALIDATOR] Error extracting tool numbers: {e}")

    return tool_numbers


def validate_numerical_accuracy(run_result, final_output: str) -> ValidationResult:
    """
    Compare numbers in the final output against tool outputs.

    Returns a ValidationResult with:
    - exact_matches: count of numbers matching tool output
    - close_matches: numbers within 1% but not exact
    - orphan_numbers: numbers not traceable to any tool output
    - suspicious: close-but-wrong values (same as close_matches, kept for logging)

    Backward-compatible: callers that ignored the previous None return are unaffected.
    """
    result = ValidationResult()

    if not final_output:
        return result

    try:
        response_numbers = _extract_numbers(final_output)
        if not response_numbers:
            return result

        tool_numbers = _extract_tool_output_numbers(run_result)
        if not tool_numbers:
            logger.debug("[NUM VALIDATOR] No tool output numbers found, skipping validation")
            return result

        for resp_num in response_numbers:
            resp_val = float(resp_num)

            found_exact = False
            found_close = False
            for tool_num in tool_numbers:
                tool_val = float(tool_num)
                if tool_val == 0:
                    continue
                relative_diff = abs(resp_val - tool_val) / abs(tool_val)

                # Exact match or very close (within 0.01%)
                if relative_diff < 0.0001:
                    result.exact_matches += 1
                    found_exact = True
                    break
                # Close-but-wrong (within 1% but not exact)
                if relative_diff < 0.01:
                    diff = abs(resp_val - tool_val)
                    result.close_matches.append((resp_num, tool_num, diff))
                    result.suspicious.append((resp_num, tool_num, diff))
                    found_close = True
                    break

            if not found_exact and not found_close:
                result.orphan_numbers.append(resp_num)

        # Log warnings for suspicious values
        if result.suspicious:
            for resp_num, tool_num, diff in result.suspicious:
                logger.warning(
                    f"[NUM VALIDATOR] Possible numerical hallucination: "
                    f"response has {resp_num}, tool output has {tool_num} "
                    f"(difference: {diff:.6f})"
                )

        # Log orphan numbers
        if result.orphan_numbers:
            logger.info(
                f"[NUM VALIDATOR] Orphan numbers (not in tool output): "
                f"{result.orphan_numbers}"
            )

    except Exception as e:
        logger.debug(f"[NUM VALIDATOR] Validation error (non-critical): {e}")

    return result
