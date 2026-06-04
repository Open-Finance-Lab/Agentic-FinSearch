# Hallucination Mitigation Design

**Date:** 2026-02-23
**Status:** Approved
**Approach:** B — Calculator Tool + Prompt Hardening + Validator Upgrade

## Problem Statement

Two distinct hallucination types reported in the FinGPT search agent:

### Methodological Hallucination (EPS Example)
The agent retrieved correct data (EPS $0.50 reported, $0.45 estimated) but computed the earnings surprise percentage incorrectly: reported 10.96% instead of the correct 11.11%. The LLM predicted a plausible-looking number rather than actually computing `(0.50-0.45)/0.45*100`.

### Factual Hallucination (Options Volume Example)
The agent was asked for total daily options volume for AVGO. Instead of reporting the actual Yahoo Finance total (20,875), it summed partial data from two specific expiration dates (20,893 + 76,378 = 97,271) and presented this as the answer.

## Root Cause Analysis

**Methodological:** LLMs perform "pattern-matching math" — they predict likely token sequences rather than compute. Research shows >69% of arithmetic errors occur in the first computation step (DELI framework, ACL 2024). Our `core.md` prompt says "show calculation steps" but has no enforcement mechanism.

**Factual:** The research engine's `Synthesizer` combines sub-question answers with the instruction "use exact values from research results." This doesn't prevent the LLM from selecting the *wrong* values to combine or from aggregating partial data into a fabricated total.

## Industry Context

| Company/Paper | Approach | Result |
|---|---|---|
| OpenAI Code Interpreter | LLM writes Python, sandbox executes | Eliminates arithmetic hallucination |
| Program of Thoughts (Chen et al.) | Separate reasoning from computation | +20% on FinQA benchmark |
| Google Check Grounding API | Per-claim support score vs source spans | Configurable threshold filtering |
| Perplexity Sonar | Retrieval-first, 2-3x more citations | 93.9% SimpleQA accuracy |
| FinSage (2025) | Evidence string annotation | Prevents speculation on partial data |

Key principle: **never let the LLM perform arithmetic in natural language.**

## Design

### Component 1: Calculator Tool

**New file:** `Main/backend/datascraper/calculator_tool.py`

A `calculate()` function tool registered in the agent's toolset:
- Signature: `calculate(expression: str) -> str`
- Uses Python `ast` module for safe evaluation — only arithmetic operators, `math` module functions, and numeric literals
- No `exec`/`eval` of arbitrary code
- Returns full-precision result as string
- Registered in `create_fin_agent()` alongside MCP and Playwright tools

**Safe eval approach:**
- Parse expression with `ast.parse(expression, mode='eval')`
- Walk the AST, allowing only: `Num`, `BinOp`, `UnaryOp`, `Call` (for whitelisted math functions like `abs`, `round`)
- Reject anything else (variable names, imports, attribute access)
- Evaluate the validated AST

### Component 2: Prompt Hardening

#### 2a. `core.md` — New CALCULATION RULES section

```markdown
CALCULATION RULES:
- For ANY derived metric (percentage change, ratio, difference, sum, average),
  call the calculate() tool with a Python expression. Never compute in text.
- Present the tool's result exactly. Do not round or modify calculator output
  unless the user asks for a specific precision.
- When showing a derived value, include the formula:
  e.g., "Earnings surprise: (0.50 - 0.45) / 0.45 * 100 = 11.11%"
```

#### 2b. `research_engine.py` `_SYNTHESIS_SYSTEM` — Anti-aggregation rules

Add to existing synthesis prompt:

```markdown
SOURCE INTEGRITY:
- Every numerical value must come directly from a single research result.
- NEVER sum, average, or combine numbers from different sub-question results
  unless the original query explicitly asks for an aggregation.
- If data covers only a subset (e.g., 2 of 10 expiration dates), state
  "Based on [N] of [M] available items" — never present partial data as a total.
- If the exact data point requested is not in the research results,
  say "This specific data point was not found in the research results"
  rather than constructing an approximation from related data.
- For any calculations in the synthesis, show the formula and the exact
  source values used. Never present a computed value without attribution.
```

#### 2c. `research_engine.py` `_ANALYZER_SYSTEM` — Improved sub-question classification

Update the query analyzer to avoid over-decomposing simple single-lookup queries. Add a rule:

```
- If the query asks for a SINGLE aggregate metric (e.g., "total options volume today"),
  treat it as a single numerical query, NOT as a decomposition target.
  Only decompose when the user explicitly asks to compare or break down by category.
```

### Component 3: Numerical Validator Upgrade

**File:** `Main/backend/datascraper/numerical_validator.py`

Changes:
1. Return a `ValidationResult` dataclass instead of `None`
2. Track calculator tool outputs (tool type `function_call` with name `calculate`)
3. Flag "orphan numbers" — values in the response that don't trace to any tool output
4. Categorize findings: `exact_match`, `close_match`, `orphan`, `suspicious`
5. Keep non-blocking — callers choose whether to act on findings

```python
@dataclass
class ValidationResult:
    exact_matches: int        # Numbers matching tool output exactly
    close_matches: list[tuple] # Numbers close to tool output (within 1%)
    orphan_numbers: list[str]  # Numbers not traceable to any source
    suspicious: list[tuple]    # Close-but-wrong values (the current detection)
```

## Affected Files

| File | Change |
|---|---|
| `Main/backend/datascraper/calculator_tool.py` | **New** — safe eval calculator |
| `Main/backend/mcp_client/agent.py` | Register calculator tool |
| `Main/backend/prompts/core.md` | Add CALCULATION RULES section |
| `Main/backend/datascraper/research_engine.py` | Update synthesis + analyzer prompts |
| `Main/backend/datascraper/numerical_validator.py` | Upgrade to structured ValidationResult |

## Testing Strategy

1. **Calculator tool:** Unit tests for safe_eval — valid expressions, rejection of dangerous inputs, edge cases (division by zero, very large numbers)
2. **Prompt changes:** Manual testing with the two reported queries (EPS surprise, options volume)
3. **Validator:** Unit tests for orphan number detection, ValidationResult structure

## Future Considerations (Not in Scope)

- **Approach C verifier agent:** Can be added later using `ValidationResult` as input signal
- **Structured JSON output:** For programmatic consumption of financial data
- **Calculator in synthesis pipeline:** The synthesizer doesn't have tool access; if needed, we could add a code-execution step between research and synthesis
