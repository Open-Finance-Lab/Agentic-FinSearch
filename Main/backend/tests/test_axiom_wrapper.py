"""Unit tests for ``axioms.wrapper.wrap_claim_values``.

Covers candidate generation per ratio, delimited-region preservation,
first-match semantics, idempotency, and structured logging on no-match.
"""

import logging

import pytest

from axioms.wrapper import wrap_claim_values


# ── fixtures ────────────────────────────────────────────────────────


def _claim(ratio, ticker, period, value, claim_id=None):
    return {
        "claim_id": claim_id or f"{ratio}-{ticker}-{period}-0",
        "ratio": ratio,
        "ticker": ticker,
        "period": period,
        "claimed_value": value,
    }


# ── happy paths (three demo ratios) ──────────────────────────────────


def test_gross_margin_wraps_percentage_form():
    prose = "Apple's gross margin was 44.13% in FY2023."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>' in out
    assert out.replace(
        '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>',
        "44.13%",
    ) == prose


def test_current_ratio_wraps_decimal_form():
    prose = "Microsoft's current ratio was 0.99 at quarter-end."
    claims = [_claim("current_ratio", "MSFT", "2023-06-30", 0.988)]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="current_ratio-MSFT-2023-06-30-0">0.99</span>' in out


def test_accounting_equation_wraps_integer_form():
    prose = "Tesla reported total assets of 352,755,000,000 for FY2023."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 352755000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">352,755,000,000</span>'
        in out
    )


# ── rounding drift ───────────────────────────────────────────────────


def test_gross_margin_rounding_drift_to_one_decimal():
    # Tool call stored 44.13, prose rounds to 44.1%.
    prose = "Gross margin hit 44.1% last quarter."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.1%</span>' in out


def test_accounting_equation_billions_suffix():
    # Tool call stored 352755000000, prose says "$352.8 billion".
    prose = "Total assets were $352.8 billion at year-end."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 352755000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$352.8 billion</span>'
        in out
    )


def test_accounting_equation_millions_suffix():
    prose = "Total assets equaled $352,755 million."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 352755000000)]
    out = wrap_claim_values(prose, claims)
    assert 'data-claim-id="accounting_equation-TSLA-2023-12-31-0"' in out
    assert "$352,755 million" in out


def test_accounting_equation_two_decimal_billions():
    # LLMs commonly round billions to two decimals.
    prose = "Total assets were $106.62 billion."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106.62 billion</span>'
        in out
    )


def test_accounting_equation_three_decimal_billions():
    prose = "Total assets stood at $106.618 billion at year-end."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106.618 billion</span>'
        in out
    )


def test_accounting_equation_bare_millions_in_table_cell():
    # Markdown table with "(in millions)" unit in the header, not the cell.
    prose = (
        "| Line Item | Q4 2023 |\n"
        "|---|---|\n"
        "| Total Assets | $106,618 |\n"
        "| Total Liabilities | $43,009 |\n"
    )
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106,618</span>'
        in out
    )


def test_accounting_equation_prefers_million_suffix_over_bare_at_same_position():
    # When "$106,618 million" appears, the suffix form should win — not
    # the bare "$106,618" substring.
    prose = "Total assets of $106,618 million."
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106,618 million</span>'
        in out
    )


# ── delimited-region skips (one per kind) ────────────────────────────


def test_skip_inside_fenced_code_block():
    prose = "```\nmargin = 44.13\n```"
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose  # unchanged — claim falls through to no-match


def test_skip_inside_inline_code():
    prose = "Consider `44.13` as a constant."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_skip_inside_inline_math_dollars():
    prose = "The identity $44.13$ appears."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_skip_inside_display_math_dollars():
    prose = "$$\ngross\\_margin = 44.13\n$$"
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_skip_inside_display_math_brackets():
    prose = r"\[ gm = 44.13 \]"
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_skip_inside_inline_math_parens():
    prose = r"See \( 44.13 \) above."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_skip_inside_html_attribute():
    prose = '<a href="https://ex.com/44.13/row">row</a>'
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out == prose


def test_value_in_both_code_and_free_text_only_wraps_free_text():
    prose = "In the snippet ```\n44.13\n``` and also 44.13 in prose."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    # Fenced region preserved verbatim
    assert "```\n44.13\n```" in out
    # Free-text occurrence wrapped
    assert '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13</span> in prose' in out


# ── multiple-occurrence: wrap all ────────────────────────────────────


def test_multiple_occurrence_wraps_every_instance():
    prose = "Gross margin of 44.13% this year, up from 44.13% last year."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    # Both occurrences wrapped with the same id.
    assert out.count('<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>') == 2
    # Structure preserved — stripping the wraps returns the original prose.
    assert out.replace(
        '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>',
        "44.13%",
    ) == prose


def test_wraps_across_prose_and_table():
    # Same value appears both in narrative prose and in a markdown table row.
    prose = (
        "Total assets were $106,618 million at year-end.\n\n"
        "| Line Item | Q4 2023 |\n"
        "|---|---|\n"
        "| Total Assets | $106,618 |\n"
    )
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    # The prose sentence locks the candidate to the "$106,618 million" form,
    # so the table's bare "$106,618" remains unwrapped (different string).
    assert out.count(
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106,618 million</span>'
    ) == 1
    assert "| Total Assets | $106,618 |" in out


def test_multiple_occurrence_skips_delimited_regions():
    # Three occurrences: free text, fenced code, free text. Only the two in
    # free text should be wrapped; the fenced one stays verbatim.
    prose = (
        "Gross margin was 44.13% last quarter.\n\n"
        "```\nmargin = 44.13%\n```\n\n"
        "Up from 44.13% a year prior."
    )
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert out.count('<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>') == 2
    assert "```\nmargin = 44.13%\n```" in out


def test_wraps_bare_billion_form_in_table():
    # Regression: LLM renders a balance sheet in a "USD billions" table with
    # no "billion" unit in any cell, so the only visible form is bare
    # ``$106.62``. The wrapper must match the bare billions form.
    prose = (
        "All figures are in USD billions unless otherwise noted.\n\n"
        "| Category | Value |\n"
        "|---|---|\n"
        "| Total Assets | $106.62 |\n"
        "\nAccounting Equation: 106.62 = 43.01 + 63.61 = 106.62\n"
    )
    claims = [_claim("accounting_equation", "TSLA", "2023-12-31", 106618000000)]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">$106.62</span>' in out
    # Single-candidate-per-claim contract: the bare ``106.62`` in the equation
    # is a different string than the chosen ``$106.62``, so it stays unwrapped.
    assert (
        '<span data-claim-id="accounting_equation-TSLA-2023-12-31-0">106.62</span>'
        not in out
    )
    assert "106.62 = 43.01 + 63.61 = 106.62" in out


# ── no-match case ────────────────────────────────────────────────────


def test_no_match_returns_prose_unchanged_and_logs(caplog):
    prose = "The report contained no relevant figures."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    with caplog.at_level(logging.WARNING, logger="axioms.wrapper"):
        out = wrap_claim_values(prose, claims, session_id="sess-xyz")
    assert out == prose
    assert any(
        "no candidate matched" in record.message for record in caplog.records
    )


# ── already-wrapped: idempotent ──────────────────────────────────────


def test_does_not_double_wrap_if_span_already_present():
    claim = _claim("gross_margin", "AAPL", "2023-09-30", 44.13)
    prose = (
        'Gross margin was <span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span> '
        "this year."
    )
    out = wrap_claim_values(prose, [claim])
    assert out == prose
    assert out.count('data-claim-id="gross_margin-AAPL-2023-09-30-0"') == 1


# ── multiple claims in one prose ─────────────────────────────────────


def test_multiple_claims_each_wrapped_independently():
    prose = (
        "Apple gross margin 44.13%, Microsoft current ratio 0.99, "
        "Tesla total assets 352,755,000,000."
    )
    claims = [
        _claim("gross_margin", "AAPL", "2023-09-30", 44.13, "gm-0"),
        _claim("current_ratio", "MSFT", "2023-06-30", 0.988, "cr-0"),
        _claim("accounting_equation", "TSLA", "2023-12-31", 352755000000, "ae-0"),
    ]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="gm-0">44.13%</span>' in out
    assert '<span data-claim-id="cr-0">0.99</span>' in out
    assert '<span data-claim-id="ae-0">352,755,000,000</span>' in out


# ── empty / defensive paths ──────────────────────────────────────────


def test_empty_prose_passes_through():
    assert wrap_claim_values("", [_claim("gross_margin", "AAPL", "2023", 44.13)]) == ""


def test_empty_claims_passes_through():
    prose = "The quick brown fox."
    assert wrap_claim_values(prose, []) == prose


def test_claim_without_claim_id_is_skipped():
    prose = "Gross margin was 44.13%."
    claim = {
        "ratio": "gross_margin",
        "ticker": "AAPL",
        "period": "2023",
        "claimed_value": 44.13,
    }  # no claim_id
    out = wrap_claim_values(prose, [claim])
    assert out == prose


# ── regex: bare '<' comparisons must not swallow the whole tail ──────


def test_literal_less_than_does_not_eat_later_content():
    # "<$100M" is not an HTML tag; the wrapper must still find 44.13%
    # further along in free text rather than treat everything from '<'
    # onwards as one big delimited region.
    prose = "Revenue was <$100M; gross margin held at 44.13% for FY2023."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert '<span data-claim-id="gross_margin-AAPL-2023-09-30-0">44.13%</span>' in out
    # And the original '<$100M' is preserved verbatim.
    assert "<$100M;" in out


def test_angle_bracket_without_tag_name_is_not_delimited():
    # '<5%' and 'x<y' shouldn't register as delimiters either.
    prose = "Threshold x<y triggers review; margin was 44.13% regardless."
    claims = [_claim("gross_margin", "AAPL", "2023-09-30", 44.13)]
    out = wrap_claim_values(prose, claims)
    assert "44.13%</span>" in out
    assert "x<y triggers" in out
