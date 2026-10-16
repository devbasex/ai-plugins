"""`relay.py account` の値の決定: 引数 → 対話 → 足りない引数の報告 の順で 1 つの値を決める（#1468）。

端末かどうか（標準入力が端末か）を判定するのはここだけである。端末でなければ入力を待たず、決められない値の
引数の名前と候補を `MissingArgs` で返す（I7）。問いは標準エラーへ出す（標準出力は結果だけにする）。
"""

from __future__ import annotations

import sys


class MissingArgs(Exception):
    """端末でなく、決められない値がある。`missing` は引数の名前の並び、`candidates` は引数ごとの候補。"""

    def __init__(self, missing: list[str], candidates: dict | None = None):
        super().__init__("足りない引数: " + ", ".join(missing))
        self.missing = missing
        self.candidates = candidates or {}


class Fail(Exception):
    """副命令の失敗。`reason` は `--json` の `reason`、`code` は終了コード、`extra` は JSON へ足すキー。"""

    def __init__(self, reason: str, message: str, code: int = 1, **extra):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.code = code
        self.extra = extra


def interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


class Asker:
    """値を決める。`interactive` は標準入力が端末か、`yes` は `--yes`（確認を飛ばす）。"""

    def __init__(self, yes: bool = False, is_tty: bool | None = None):
        self.yes = yes
        self.interactive = interactive() if is_tty is None else is_tty

    def _input(self, question: str) -> str:
        print(question, end="", file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line:
            raise MissingArgs([])
        return line.strip()

    def confirm(self, arg: str, question: str) -> bool:
        """確認（y/n）。`--yes` なら真。端末でなければ `arg`（`--yes`）が足りない。"""
        if self.yes:
            return True
        if not self.interactive:
            raise MissingArgs([arg])
        return self._input(f"{question} [y/N] ").lower() in ("y", "yes")

    def value(self, arg: str, question: str, given: str | None = None, candidates: list[str] | None = None) -> str:
        """引数 `given` → 候補が 1 つなら確認 → 複数なら番号で選ぶ → 候補が無ければ入力。端末でなければ `arg` が足りない。

        候補が 1 つのときの確認は `--yes` で飛ばす。"""
        if given:
            return given
        cands = candidates or []
        if len(cands) == 1 and self.yes:
            return cands[0]
        if not self.interactive:
            raise MissingArgs([arg], {arg: cands} if cands else None)
        if len(cands) == 1:
            if self.confirm(arg, f"{question}: {cands[0]} を使う？"):
                return cands[0]
            raise MissingArgs([arg], {arg: cands})
        if cands:
            for i, c in enumerate(cands, 1):
                print(f"  {i}. {c}", file=sys.stderr)
            got = self._input(f"{question}（番号）: ")
            if got.isdigit() and 1 <= int(got) <= len(cands):
                return cands[int(got) - 1]
            if got in cands:
                return got
            raise MissingArgs([arg], {arg: cands})
        got = self._input(f"{question}: ")
        if not got:
            raise MissingArgs([arg])
        return got
