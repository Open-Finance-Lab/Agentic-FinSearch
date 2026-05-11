"""
Safe calculator tool for the financial agent.

Provides a calculate() function tool that uses Python's ast module
for safe expression parsing — only arithmetic operators, numeric literals,
and whitelisted math functions are allowed. No arbitrary code execution.
"""

import ast
import math
import operator
from agents import function_tool

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_WHITELISTED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "pow": pow,
}


def _compute_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node, allowing only safe operations."""
    if isinstance(node, ast.Expression):
        return _compute_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Constant type {type(node.value).__name__} not allowed")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ValueError(f"Binary operator {op_type.__name__} not allowed")
        left = _compute_node(node.left)
        right = _compute_node(node.right)
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ValueError("division by zero")
        return float(_BINARY_OPS[op_type](left, right))

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"Unary operator {op_type.__name__} not allowed")
        return float(_UNARY_OPS[op_type](_compute_node(node.operand)))

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_compute_node(elt) for elt in node.elts]

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only named function calls allowed (no methods)")
        func_name = node.func.id
        if func_name not in _WHITELISTED_FUNCTIONS:
            raise ValueError(f"Function '{func_name}' not allowed")
        args = [_compute_node(arg) for arg in node.args]
        # round() requires an int for ndigits
        if func_name == "round" and len(args) > 1:
            args[1] = int(args[1])
        result = _WHITELISTED_FUNCTIONS[func_name](*args)
        return float(result) if isinstance(result, (int, float)) else result

    raise ValueError(f"AST node type {type(node).__name__} not allowed")


def safe_compute(expression: str) -> float:
    """
    Safely evaluate a mathematical expression.

    Uses ast.parse to build an AST, then walks it allowing only
    numeric constants, arithmetic operators, and whitelisted functions.

    Args:
        expression: A Python math expression string.

    Returns:
        The computed result as a float.

    Raises:
        ValueError: If the expression is empty, contains disallowed
                    constructs, or results in a math error.
    """
    if not expression or not expression.strip():
        raise ValueError("Empty expression")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression: {exc}") from exc

    return _compute_node(tree)


@function_tool
def calculate(expression: str) -> str:
    """Calculate a mathematical expression safely. Use this for ANY arithmetic:
    percentages, ratios, differences, sums, averages. Supports list literals for batch operations. Examples:
    - "(0.50 - 0.45) / 0.45 * 100" for percentage change
    - "1234.56 * 0.15" for a 15% calculation
    - "sqrt(144)" for square root
    - "sum([1.5, 2.3, 3.7])" for sum of a list
    - "sum([1.5, 2.3, 3.7]) / len([1.5, 2.3, 3.7])" for mean
    """
    result = safe_compute(expression)
    return str(result)


def get_calculator_tools() -> list:
    """Return the list of calculator tools for agent registration."""
    return [calculate]
