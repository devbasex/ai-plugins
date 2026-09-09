"""見出し直下の Markdown 表を解析する補助（#517 / R3-002）。

`SKILL.md` などの「## 見出し」の直後に来る表を、ヘッダーの行と本文の行に分けて返す。
`test_workflow_units.py`（`workflow_table`）と `test_workflow_stage_matrix.py`（`_table`）が
同一の解析を持っていたため、その部分だけをここへ寄せる。

ファイルの読み込みと、表を読めなかったときの固有の失敗メッセージは、呼び出し側の薄い
ラッパーに残す。ここが行うのは「本文と見出しから header と rows を取り出す」ところまでで、
見出しが見つからないことだけを共通の失敗として扱う。

**終了条件や失敗時の契約が異なるパーサーは寄せない。** `test_operation_mode.py`
（`## ` ではなく `#` で打ち切る）や `test_approval_gates.py`（rows だけを返す）は、
ここには含めない。
"""
from __future__ import annotations


def parse_table(body: str, heading: str) -> tuple[list[str], list[list[str]]]:
    """`heading` の直後に来る表の header と rows を返す。

    見出しが見つからないときは AssertionError を投げる（`見出しが見つからない: <見出し>`）。
    表が空かどうかの判定は呼び出し側に委ねる（診断のメッセージが呼び出し側で異なるため）。
    """
    lines = body.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    assert start is not None, f"見出しが見つからない: {heading}"
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return header, rows
