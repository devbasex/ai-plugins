"""現状固定: drive の提案の工程へ渡す指示文（`_propose_prompt`）の組み立て。

触る関数はファイルごとに 1 行で、名前は昇順。ファイル単位で扱うファイル（whole_files）は指示文に入らない。
"""

from __future__ import annotations

from types import SimpleNamespace

from ndf.domains.crossrefactor import drive
from ndf.domains.crossrefactor.symbols_decide import Touched

HEAD = "PR #{pr} が触る次の関数・クラスについて、振る舞いを変えないリファクタリングを提案する。ほかの関数は提案しない。\n"
TAIL = '最後の行に JSON {"proposals": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'


def _prompt(pr: int, touched: Touched) -> str:
    run = SimpleNamespace(cfg=SimpleNamespace(pr=pr), state={"touched": touched.as_json()})
    return drive._propose_prompt(run)  # type: ignore[arg-type]


def test_propose_prompt_lists_each_file_with_sorted_names():
    touched = Touched({"src/geo.py": frozenset({"area", "Shape.size"}), "lib/a.py": frozenset({"f"})})
    got = _prompt(7, touched)
    assert got == HEAD.format(pr=7) + "- lib/a.py: f\n- src/geo.py: Shape.size, area\n" + TAIL


def test_propose_prompt_with_no_symbols_leaves_an_empty_line():
    assert _prompt(12, Touched({})) == HEAD.format(pr=12) + "\n" + TAIL


def test_propose_prompt_keeps_a_file_without_names_and_ignores_whole_files():
    touched = Touched({"lib/cli.py": frozenset()}, ("web/app.ts",))
    got = _prompt(3, touched)
    assert got == HEAD.format(pr=3) + "- lib/cli.py: \n" + TAIL
    assert "web/app.ts" not in got
