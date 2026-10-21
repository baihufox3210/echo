import re
import secrets

from agents import FunctionTool, function_tool

DICE_PATTERN = re.compile(r"^\s*(\d{0,2})\s*[dD]\s*(\d{1,4})\s*([+-]\s*\d{1,4})?\s*$")
MAX_DICE = 20


def roll(notation: str) -> str:
    match = DICE_PATTERN.match(notation)
    if not match:
        raise ValueError("骰子格式錯誤，請使用例如 1d6、2d20+3")

    count = int(match.group(1) or 1)
    sides = int(match.group(2))
    modifier = int(match.group(3).replace(" ", "")) if match.group(3) else 0
    if not 1 <= count <= MAX_DICE or sides < 2:
        raise ValueError(f"骰子數量需介於 1~{MAX_DICE}，面數至少 2")

    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    total = sum(rolls) + modifier
    detail = " + ".join(map(str, rolls))
    if modifier:
        detail += f" {'+' if modifier > 0 else '-'} {abs(modifier)}"
    return f"{notation.strip()}：{detail} = {total}"


def build_fun_tools() -> list[FunctionTool]:
    @function_tool
    def roll_dice(notation: str = "1d6") -> str:
        """擲骰子，用於遊戲、桌遊或需要隨機結果的情境。結果必須以實際擲出的數字為準。

        Args:
            notation: 骰子表示法，例如 1d6、2d20+3、d100。
        """
        try:
            return roll(notation)
        except ValueError as error:
            return str(error)

    @function_tool
    def random_pick(options: list[str], count: int = 1) -> str:
        """從選項中公平地隨機抽選，例如幫忙決定吃什麼、抽籤。

        Args:
            options: 候選項目列表（至少 2 個）。
            count: 要抽出的數量。
        """
        options = [o.strip() for o in options if o.strip()]
        if len(options) < 2:
            return "至少需要 2 個選項"
        count = min(max(count, 1), len(options))
        pool = list(options)
        picked = [pool.pop(secrets.randbelow(len(pool))) for _ in range(count)]
        return "抽選結果：" + "、".join(picked)

    return [roll_dice, random_pick]
