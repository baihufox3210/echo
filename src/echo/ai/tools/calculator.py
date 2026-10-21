import ast
import math
import operator

from agents import FunctionTool, function_tool

MAX_EXPONENT = 1000
MAX_EXPRESSION_LENGTH = 300

BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
FUNCTIONS = {
    name: getattr(math, name)
    for name in (
        "sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "asin", "acos", "atan",
        "floor", "ceil", "factorial", "gcd", "lcm", "degrees", "radians", "hypot",
    )
} | {"abs": abs, "round": round, "min": min, "max": max}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


class CalculationError(Exception):
    pass


def evaluate(expression: str) -> float | int:
    """安全地計算數學運算式（不使用 eval）。"""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculationError("運算式過長")
    expression = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculationError("運算式格式錯誤") from error
    return _eval(tree.body)


def _eval(node: ast.AST) -> float | int:
    match node:
        case ast.Constant(value=value) if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        case ast.Name(id=name) if name in CONSTANTS:
            return CONSTANTS[name]
        case ast.BinOp(left=left, op=op, right=right) if type(op) in BINARY_OPS:
            lhs, rhs = _eval(left), _eval(right)
            if isinstance(op, ast.Pow) and abs(rhs) > MAX_EXPONENT:
                raise CalculationError("指數過大")
            if isinstance(op, ast.Mult) and isinstance(lhs, int) and isinstance(rhs, int) and abs(lhs) > 10**100 and abs(rhs) > 10**100:
                raise CalculationError("數值過大")
            return BINARY_OPS[type(op)](lhs, rhs)
        case ast.UnaryOp(op=op, operand=operand) if type(op) in UNARY_OPS:
            return UNARY_OPS[type(op)](_eval(operand))
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if name in FUNCTIONS:
            values = [_eval(arg) for arg in args]
            if name == "factorial" and values and values[0] > 500:
                raise CalculationError("數值過大")
            return FUNCTIONS[name](*values)
    raise CalculationError("包含不支援的運算")


def build_calculator_tool() -> FunctionTool:
    @function_tool
    def calculate(expression: str) -> str:
        """精確計算數學運算式。涉及數字運算、換算或百分比時使用，不要心算。

        Args:
            expression: 數學運算式，例如 (1200 * 0.85) / 3、sqrt(2)、2 ** 10。支援 + - * / // % **、sqrt、log、sin 等函式與 pi、e 常數。
        """
        try:
            result = evaluate(expression)
        except (CalculationError, ArithmeticError, ValueError, TypeError) as error:
            return f"計算失敗：{error}"
        if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
            result = int(result)
        return f"{expression} = {result}"

    return calculate
