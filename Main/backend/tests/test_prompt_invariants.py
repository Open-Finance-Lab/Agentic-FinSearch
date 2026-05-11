"""Locks the RATIO CLAIMS relevance gate and SHARE-COUNT UNITS rule in
`prompts/core.md`. Without them the agent pads responses with tangential
ratios so Validate runs on data the user never asked about, and displays
10-K "(in thousands)" share values as if they were full integer counts.
"""


def test_relevance_gate_present(core_prompt: str) -> None:
    assert "RELEVANCE GATE" in core_prompt, (
        "RATIO CLAIMS lost the RELEVANCE GATE header. Without it the agent "
        "reverts to padding responses with supported ratios and emitting "
        "report_claim for each, producing fake-validation on unrelated "
        "questions."
    )
    lowered = core_prompt.lower()
    assert "financial context" in lowered, (
        "Prompt no longer names the 'Financial Context' padding pattern by "
        "example; the specific phrase is what the agent was producing."
    )
    assert "emit zero claims" in lowered, (
        "Prompt no longer states the zero-emission outcome for unsupported "
        "metrics (EPS, P/E, etc.). The agent needs an explicit instruction."
    )


def test_unsupported_metrics_enumerated(core_prompt: str) -> None:
    for metric in ("EPS", "P/E", "market cap"):
        assert metric in core_prompt, (
            f"Prompt no longer names {metric!r} as an unsupported metric "
            f"in the relevance gate; agent may re-learn to pad claims."
        )


def test_share_count_unit_rule_present(core_prompt: str) -> None:
    assert "SHARE-COUNT UNITS" in core_prompt, (
        "DATA ACCURACY lost the SHARE-COUNT UNITS header. Without it the "
        "agent again displays 10-K 'in thousands' share values raw, which "
        "makes EPS arithmetic internally inconsistent on the user's screen."
    )
    assert "in thousands" in core_prompt.lower(), (
        "Prompt no longer cites the '(in thousands)' 10-K header that is "
        "the actual trigger for the unit mistake."
    )


def test_math_delimiter_rule_canonical(core_prompt: str) -> None:
    """The prompt must teach \\(...\\) inline / $$...$$ display and forbid
    single $...$ for math. The renderer (frontend/src/modules/markdownRenderer.js)
    does not parse single $ as math because currency mentions collide; if the
    prompt drifts back to "Use $ for inline math" the agent's math output
    stops rendering."""
    assert "\\(...\\)" in core_prompt and "$$...$$" in core_prompt, (
        "core.md no longer shows the canonical math delimiter pair "
        "\\(...\\) for inline / $$...$$ for display."
    )
    assert "single $" in core_prompt.lower() or "single $...$" in core_prompt.lower(), (
        "core.md no longer explicitly forbids single $...$ for math, "
        "which is what triggers currency-vs-math collision in the renderer."
    )


def test_formula_inputs_audit_only(core_prompt: str) -> None:
    """formula_inputs is decorative in every flow: the engine compares
    claimed_value to XBRL ground truth directly. A previous version of the
    prompt said the field was "informational only in the validate-user-claim
    case", which misled readers into believing Q&A flow used it for
    verification. axioms/engine.py never reads it."""
    lowered = core_prompt.lower()
    assert "formula_inputs" in core_prompt
    forbidden = "informational only and does not drive the verdict"
    assert forbidden not in lowered, (
        "core.md still scopes the formula_inputs audit-only rule to the "
        "validate-user-claim case. The engine never reads formula_inputs in "
        "ANY flow; the unscoped wording is the load-bearing one."
    )
    assert "audit" in lowered and "never drives the verdict in any flow" in lowered, (
        "core.md no longer says formula_inputs is audit-only across all "
        "flows. Without that, readers may assume Q&A verification depends "
        "on it."
    )


def test_validate_intent_cues_cover_frontend_regex(core_prompt: str) -> None:
    """Every cue in intent.js::VALIDATION_INTENT_RE must also appear in
    core.md Rule 1, so the frontend's auto-fire trigger and the agent's
    claimed_value-pinning rule cannot disagree about what counts as a
    validate-user-claim intent. If a cue is added to the regex it must be
    added to the prompt in the same commit."""
    lowered = core_prompt.lower()
    required_cues = [
        "validate",
        "verify",
        "fact-check",
        "double-check",
        "sanity-check",
        "cross-check",
    ]
    for cue in required_cues:
        assert cue in lowered, (
            f"core.md Rule 1 no longer lists {cue!r} as a validate-user-claim "
            f"phrasing. The frontend regex in intent.js still fires on it, "
            f"so the agent will be auto-validated without knowing to pin "
            f"claimed_value to the user's number."
        )


def test_no_playwright_leak_in_core(core_prompt: str) -> None:
    """core.md must not name 'Playwright' anywhere, neither in tool
    descriptions nor in the security rule. The actual tool names
    (navigate_to_url, click_element, extract_page_content) stay; only the
    library brand is scrubbed. Otherwise the prompt simultaneously names
    Playwright and tells the agent never to disclose it: a confused signal
    that increases leak risk rather than reduces it."""
    assert "playwright" not in core_prompt.lower(), (
        "core.md still mentions 'Playwright'. The catalog/section header "
        "should use neutral 'Browser tools' wording so the prompt does not "
        "simultaneously name and forbid the brand."
    )
    # The functional tool names must still be present.
    for tool_name in ("navigate_to_url", "click_element", "extract_page_content"):
        assert tool_name in core_prompt, (
            f"Tool name {tool_name!r} disappeared from core.md when "
            f"Playwright references were scrubbed; the agent now has no "
            f"way to learn this tool exists."
        )


def test_user_provided_context_security_rule(core_prompt: str) -> None:
    """The SECURITY rules now live in `prompts/_security.md` and are spliced
    into core.md at the `<!-- SECURITY_RULES_INSERT -->` marker by
    PromptBuilder. The rule that tells the model the USER-PROVIDED CONTEXT
    block is data must survive in the assembled output; we check the
    fragment + the marker so either drift breaks the test."""
    from pathlib import Path

    assert "<!-- SECURITY_RULES_INSERT -->" in core_prompt, (
        "core.md no longer carries the SECURITY_RULES_INSERT marker. "
        "PromptBuilder will silently emit no security section, and prompt "
        "injection via the system_prompt parameter regains its effect."
    )

    fragment = (
        Path(__file__).resolve().parent.parent / "prompts" / "_security.md"
    ).read_text(encoding="utf-8").lower()
    assert "user-provided context" in fragment, (
        "_security.md no longer references the USER-PROVIDED CONTEXT block."
    )
    assert "data, not instructions" in fragment, (
        "_security.md no longer says USER-PROVIDED CONTEXT is data, not "
        "instructions. The exact phrasing mirrors the wrap in "
        "PromptBuilder.build (USER_CONTEXT_OPEN); if either drifts the "
        "boundary collapses."
    )


def test_security_rules_single_source() -> None:
    """`prompts/_security.md` is the canonical security source. Both
    `mcp_client.prompt_builder.PromptBuilder` (agent path) and
    `datascraper.datascraper._SECURITY_GUARDRAILS` (legacy /chat path)
    must read from it. Inline duplicates in datascraper.py are the
    drift channel that was caught in the audit."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "datascraper" / "datascraper.py"
    ).read_text(encoding="utf-8")
    assert "_load_security_fragment" in src, (
        "datascraper.py no longer defines _load_security_fragment; the "
        "SECURITY rules drifted back to an inline copy."
    )
    assert "prompts/_security.md" in src or "_security.md" in src, (
        "datascraper.py does not reference the shared _security.md "
        "fragment; security rules will diverge on the next edit."
    )
    # The old hardcoded "FinGPT assistant" identity must NOT come back —
    # agent path standardised on "FinSearch", and any inline copy here
    # creates the cross-file drift the audit was meant to fix.
    assert "FinGPT assistant" not in src, (
        "datascraper.py still inlines the old 'FinGPT assistant' identity. "
        "The shared _security.md fragment is the single source; inline "
        "copies recreate the cross-file drift."
    )


def test_unsupported_metric_fact_check_rule(core_prompt: str) -> None:
    """When a user asks to fact-check an unsupported metric (EPS, P/E,
    market cap, etc.) the agent must answer in prose, name the verifier's
    scope to the user, and emit ZERO claims. Without the rule the agent
    silently returns no Validate badge and the user has no way to know
    why — looks like a flaky button rather than an out-of-scope metric."""
    assert "UNSUPPORTED-METRIC FACT-CHECK" in core_prompt, (
        "core.md no longer carries the UNSUPPORTED-METRIC FACT-CHECK "
        "section header; the agent loses the cue for unsupported-metric "
        "validate-user-claim flows."
    )
    lowered = core_prompt.lower()
    assert "in-line ratio verifier covers margin / current ratio / balance-sheet identity only" in lowered, (
        "core.md no longer carries the user-facing scope sentence for the "
        "unsupported-metric fact-check rule. Without it the agent will "
        "either invent a Validate emission or stay silent about why no "
        "badge appears."
    )


def test_tool_catalog_uses_whitelist_wording(core_prompt: str) -> None:
    """The IMPORTANT line under AVAILABLE TOOLS must be a positive
    whitelist ("Only call tools whose names appear..."), not a blacklist
    of specific wrong names. Audit P2.1: blacklist is whack-a-mole and
    drifts every time we add a synonym."""
    assert "Only call tools whose names appear literally in the AVAILABLE TOOLS list above" in core_prompt, (
        "core.md no longer carries the positive-whitelist tool-naming "
        "rule. The blacklist that came before was incomplete by design."
    )
    # The old blacklist enumeration must not come back.
    assert "There are no tools named get_key_statistics" not in core_prompt, (
        "core.md reverted to the blacklist enumeration of forbidden tool "
        "names; it never covered every wrong name."
    )


def test_accounting_equation_precision_rule(core_prompt: str) -> None:
    """Rule 3 in RATIO CLAIMS must spell out claimed_value precision for
    accounting_equation as a raw integer. Without it the agent passes
    Total Assets in millions/billions and the engine compares mismatched
    units against L+TE+E."""
    assert "accounting_equation: claimed_value is the Total Assets figure as a raw integer" in core_prompt, (
        "core.md no longer carries the accounting_equation raw-integer "
        "precision rule. The MSFT $364,840M demo will scale the value "
        "wrong and report MISMATCH for arithmetic-correct claims."
    )


def test_examples_c_and_d_present(core_prompt: str) -> None:
    """Examples C (current_ratio validate) and D (accounting_equation
    validate) must accompany A and B. Models follow concrete examples
    much more reliably than prose; the AAPL/MSFT demos rely on them."""
    assert "Example C — user-supplied claim" in core_prompt, (
        "core.md is missing Example C (current_ratio validate-user-claim). "
        "The Apple FY2023 demo loses its concrete pattern."
    )
    assert "Example D — user-supplied claim" in core_prompt, (
        "core.md is missing Example D (accounting_equation validate). "
        "The MSFT $364,840M demo loses its concrete pattern."
    )


def test_xbrl_and_ratio_claims_have_precedence_rules(core_prompt: str) -> None:
    """Both XBRL VERIFICATION and RATIO CLAIMS sections must carry an
    explicit precedence rule at the top so the agent knows when to use
    which. Without it the MSFT validate-user-claim demo flips between
    flows non-deterministically."""
    # Anchor on the full header strings so we don't match cross-references
    # in surrounding prose (e.g. "use the RATIO CLAIMS flow below instead").
    idx_xbrl = core_prompt.find("XBRL VERIFICATION:")
    idx_ratio = core_prompt.find("RATIO CLAIMS (output protocol")
    assert idx_xbrl != -1, "XBRL VERIFICATION section header disappeared"
    assert idx_ratio != -1, "RATIO CLAIMS section header disappeared"

    xbrl_head = core_prompt[idx_xbrl:idx_xbrl + 400]
    ratio_head = core_prompt[idx_ratio:idx_ratio + 400]
    assert "PRECEDENCE" in xbrl_head, (
        "XBRL VERIFICATION section is missing the PRECEDENCE rule at the "
        "top; the agent will continue to flip between this manual-table "
        "flow and the RATIO CLAIMS flow on validate-user-claim prompts."
    )
    assert "PRECEDENCE" in ratio_head, (
        "RATIO CLAIMS section is missing the PRECEDENCE rule at the top; "
        "same flip risk in the reverse direction."
    )


def test_available_tools_catalog_has_runtime_markers(core_prompt: str) -> None:
    """The AVAILABLE TOOLS catalog must be wrapped in the boundary markers
    PromptBuilder._render_tool_catalog looks for. If these markers vanish
    the catalog stops being filtered against the actual tool registry and
    silently re-diverges from MCP changes."""
    assert "<!-- AVAILABLE_TOOLS_CATALOG_START -->" in core_prompt
    assert "<!-- AVAILABLE_TOOLS_CATALOG_END -->" in core_prompt
    # And the catalog itself must still live between them.
    start = core_prompt.find("<!-- AVAILABLE_TOOLS_CATALOG_START -->")
    end = core_prompt.find("<!-- AVAILABLE_TOOLS_CATALOG_END -->")
    assert start < end
    assert "AVAILABLE TOOLS" in core_prompt[start:end]


def test_agent_tool_scope_appendix_is_declarative() -> None:
    """agent.py's tool-scope appendix must NOT threaten a 'fatal error'.
    The tool-allow-list filter strips disallowed tools before the model
    sees them, so the threat is false; declarative whitelist wording
    avoids surprising the model when it tries a tool name that is
    technically valid but not attached this run."""
    from pathlib import Path

    agent_path = Path(__file__).resolve().parent.parent / "mcp_client" / "agent.py"
    src = agent_path.read_text(encoding="utf-8")
    assert "fatal error" not in src.lower(), (
        "agent.py still threatens 'fatal error' for disallowed tools. "
        "The threat is false (the filter strips them) and degrades the "
        "model's trust in the prompt's other claims."
    )
    assert "TOOL SCOPE:" in src, (
        "agent.py no longer carries the TOOL SCOPE appendix. It is what "
        "tells the model which subset of the catalog is in scope this "
        "request when a planner-level tool restriction applies."
    )


def test_report_claim_docstring_mirrors_relevance_gate() -> None:
    """The report_claim function-tool docstring is what the model reads
    when deciding to call the tool. It must echo the RELEVANCE GATE and
    Rule 1 from core.md, NOT the old "Call this EXACTLY ONCE for every
    supported ratio" wording that drove the agent to pad responses."""
    from pathlib import Path

    tool_path = Path(__file__).resolve().parent.parent / "axioms" / "tool.py"
    src = tool_path.read_text(encoding="utf-8")
    assert "EXACTLY ONCE for every supported ratio" not in src, (
        "axioms/tool.py::report_claim docstring still tells the model to "
        "emit a claim for every supported ratio it presents. This contradicts "
        "core.md's RELEVANCE GATE and is the channel the model trusts most."
    )
    assert "RELEVANCE GATE" in src, (
        "axioms/tool.py::report_claim docstring no longer references the "
        "RELEVANCE GATE. The docstring and prompt must agree on when to "
        "call this tool."
    )
    assert "user's stated number" in src, (
        "axioms/tool.py::report_claim docstring no longer states that "
        "claimed_value must be the user's number in validate-user-claim "
        "flows. Without it, the agent substitutes its own corrected value "
        "and silently breaks Validate."
    )
