# Hallucination Mitigation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate methodological hallucination (bad math) and factual hallucination (creative aggregation) in the FinGPT search agent.

**Architecture:** Register a safe `calculate()` tool in the agent so the LLM offloads arithmetic to Python. Harden prompts in both the agent and research synthesis pipelines to prohibit in-context math and cross-source aggregation. Upgrade the numerical validator to return structured results and detect orphan numbers.

**Tech Stack:** Python `ast` module for safe expression parsing (AST walking only, no use of built-in eval/exec), `agents` library `@function_tool` decorator, pytest for tests.

---

## Task 1: Calculator Tool - Tests

**Files:**
- Create: `Main/backend/tests/test_calculator_tool.py`

**Step 1: Write the failing tests**

Tests cover: basic arithmetic, the exact EPS surprise percentage case, negative numbers, exponentiation, whitelisted math functions, division by zero, rejection of variable names, rejection of imports, rejection of attribute access, rejection of strings, empty input, large numbers, and the tool registration function.

The test function `test_percentage_calculation` specifically validates: `(0.50 - 0.45) / 0.45 * 100 = 11.111...` (the reported bug).

**Step 2: Run tests to verify they fail**

Run: `cd Main/backend && python -m pytest tests/test_calculator_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

---

## Task 2: Calculator Tool - Implementation

**Files:**
- Create: `Main/backend/datascraper/calculator_tool.py`

**Step 3: Implement safe_compute and calculate tool**

The module provides:
- `safe_compute(expression: str) -> float` - Parses expression with `ast.parse(expression, mode="expression")`, then walks the AST tree via `_compute_node()`. Only allows: numeric constants (`ast.Constant` with int/float), binary ops (`+,-,*,/,//,%,**`), unary ops (`+,-`), and whitelisted function calls (`abs, round, min, max, sum, sqrt, log, log10`). Rejects all other AST node types.
- `@function_tool calculate(expression: str) -> str` - Wraps `safe_compute`, returns string result. Registered with the agents framework.
- `get_calculator_tools() -> list` - Returns `[calculate]` for agent registration.

**Step 4: Run tests**

Run: `cd Main/backend && python -m pytest tests/test_calculator_tool.py -v`
Expected: All 14 tests PASS

**Step 5: Commit**

Commit message: `feat: add safe calculator tool for agent arithmetic`

---

## Task 3: Register Calculator Tool in Agent

**Files:**
- Modify: `Main/backend/mcp_client/agent.py` (after line ~125, after `tools.extend(playwright_tools)`)

**Step 6: Add calculator tool registration**

Add after the playwright tools block:

```python
    # Calculator tool for safe arithmetic
    from datascraper.calculator_tool import get_calculator_tools
    calculator_tools = get_calculator_tools()
    tools.extend(calculator_tools)
```

**Step 7: Verify import works**

Run: `cd Main/backend && python -c "from datascraper.calculator_tool import get_calculator_tools; print(len(get_calculator_tools()))"`
Expected: `1`

**Step 8: Commit**

Commit message: `feat: register calculator tool in financial agent`

---

## Task 4: Prompt Hardening - core.md

**Files:**
- Modify: `Main/backend/prompts/core.md`

**Step 9: Add CALCULATION RULES section**

Insert after the DATA ACCURACY section (after line 17), before SECURITY:

```markdown
CALCULATION RULES:
- For ANY derived metric (percentage change, ratio, difference, sum, average), call the calculate() tool with a Python math expression. Never perform arithmetic in your response text.
- Present the calculate() tool's result exactly. Do not round or modify the tool output unless the user asks for specific precision.
- When reporting a derived value, include the formula used: e.g., "Earnings surprise: (0.50 - 0.45) / 0.45 * 100 = 11.11%"
- If you need to add, subtract, multiply, or divide any numbers, no matter how simple, use calculate().
```

**Step 10: Commit**

Commit message: `feat: add calculation rules to agent prompt`

---

## Task 5: Prompt Hardening - Research Synthesis

**Files:**
- Modify: `Main/backend/datascraper/research_engine.py` (the `_SYNTHESIS_SYSTEM` string, around line 339)

**Step 11: Add SOURCE INTEGRITY rules**

Append to the existing synthesis system prompt, after the current rules:

```
SOURCE INTEGRITY:
- Every numerical value you present must come directly from a single research result.
- NEVER sum, average, or combine numbers across different sub-question results unless the original query explicitly asks for an aggregation across categories.
- If data covers only a subset (e.g., 2 of 10 expiration dates), state "Based on [N] of [M] available items" and never present partial data as a complete total.
- If the exact data point requested is not in the research results, say "This specific data point was not found in the research results" rather than constructing an approximation from related data.
- For any calculations, show the formula and the exact source values used.
```

**Step 12: Commit**

Commit message: `feat: add anti-aggregation rules to research synthesis prompt`

---

## Task 6: Prompt Hardening - Query Analyzer

**Files:**
- Modify: `Main/backend/datascraper/research_engine.py` (the `_ANALYZER_SYSTEM` string, around line 62)

**Step 13: Add single-lookup rule**

Add this rule after the existing rules in `_ANALYZER_SYSTEM`:

```
- If the query asks for a SINGLE aggregate metric from a SINGLE source (e.g., "total options volume today", "total revenue last quarter"), treat it as ONE numerical sub-question, NOT as a decomposition target. Only decompose when the user explicitly asks to compare multiple items or break down by category.
```

**Step 14: Commit**

Commit message: `feat: prevent over-decomposition of single-lookup queries`

---

## Task 7: Numerical Validator Upgrade - Tests

**Files:**
- Create: `Main/backend/tests/test_numerical_validator.py`

**Step 15: Write failing tests**

Tests cover: ValidationResult structure, detection of close-but-wrong matches, orphan number detection (the 97271 options case), no false positives on exact matches, and empty response handling.

**Step 16: Run tests to verify they fail**

Run: `cd Main/backend && python -m pytest tests/test_numerical_validator.py -v`
Expected: FAIL with `ImportError` for `ValidationResult`

---

## Task 8: Numerical Validator Upgrade - Implementation

**Files:**
- Modify: `Main/backend/datascraper/numerical_validator.py`

**Step 17: Upgrade to structured ValidationResult**

Key changes:
1. Add `@dataclass ValidationResult` with fields: `exact_matches` (int), `close_matches` (list), `orphan_numbers` (list), `suspicious` (list)
2. `validate_numerical_accuracy` returns `ValidationResult` instead of `None`
3. Numbers not matching any tool output are added to `orphan_numbers`
4. Backward-compatible: callers ignoring return value are unaffected

**Step 18: Run all tests**

Run: `cd Main/backend && python -m pytest tests/ -v`
Expected: All tests PASS (new + existing)

**Step 19: Commit**

Commit message: `feat: upgrade numerical validator with structured results and orphan detection`

---

## Task 9: Integration Tests

**Files:**
- Create: `Main/backend/tests/test_hallucination_mitigation.py`

**Step 20: Write integration tests for reported scenarios**

Four tests:
1. `test_calculator_eps_surprise` - Verifies `(0.50-0.45)/0.45*100 = 11.111...`
2. `test_validator_catches_options_volume_orphan` - Verifies 97271 flagged as orphan when tool output has call_volume=10899, put_volume=9976
3. `test_synthesis_prompt_contains_anti_aggregation` - Checks `_SYNTHESIS_SYSTEM` has "SOURCE INTEGRITY" and "partial data"
4. `test_core_prompt_contains_calculation_rules` - Checks `core.md` has "CALCULATION RULES" and "calculate()"

**Step 21: Run all tests**

Run: `cd Main/backend && python -m pytest tests/ -v`
Expected: All tests PASS

**Step 22: Commit**

Commit message: `test: add integration tests for hallucination mitigation`

---

## Task 10: Final Verification

**Step 23: Run full test suite**

Run: `cd Main/backend && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 24: Commit design docs**

```
git add Docs/plans/2026-02-23-hallucination-mitigation-design.md Docs/plans/2026-02-23-hallucination-mitigation-impl.md
```

Commit message: `docs: add hallucination mitigation design and implementation plan`
