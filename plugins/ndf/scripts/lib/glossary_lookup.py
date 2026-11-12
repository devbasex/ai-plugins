"""glossary_lookup.py: `glossary.py lookup` と、`check`・`diff`・`lookup` の `--field`（#2909 の F3）。

用語集を読むだけで書き換えない（I2）。`lookup` は語を部分一致で引き、当たった語ごとに
`{kind: term, name, result: <コンテキストの id>, meaning, source}` を `items` に、件数を `metrics.matched` に返す。
`--contexts` はコンテキストの id の並びを `metrics.contexts` に返す。`--field` の評価は `json_pick.py` が持つ。

終了コード: 0 = 引けた（当たりが無くても 0）、2 = 読めない・指し先の書き方の誤り、3 = 宣言か用語集が無い。
"""

from __future__ import annotations

from pathlib import Path

import json_pick
import step_result


def glossary_lookup(g: dict, terms=None, context: str | None = None, contexts_only: bool = False) -> dict:
    """用語集から語を引く。`terms` は部分一致（どれか 1 つに当たれば採る）。省けばコンテキストの中のすべての語。"""
    tool = "glossary"
    if contexts_only:
        ids = [c.get("id") for c in g.get("contexts", []) if isinstance(c, dict) and c.get("id")]
        return step_result.result(tool, "ok", f"コンテキスト {len(ids)} 件", [], {"contexts": ids})
    rows = []
    for t in g.get("terms", []):
        if not isinstance(t, dict) or not isinstance(t.get("term"), str):
            continue
        if context and t.get("context") != context:
            continue
        if terms and not any(w in t["term"] for w in terms):
            continue
        rows.append(
            {"kind": "term", "name": t["term"], "result": t.get("context") or "", "meaning": t.get("meaning"), "source": t.get("source")}
        )
    return step_result.result(tool, "ok", f"{len(rows)} 語が当たった", rows, {"matched": len(rows)})


FIELD_COMMANDS = ("check", "diff", "lookup")  # `--field` を受ける副命令（引数は glossary.py が足す）


def lookup_command(load_declaration, load_glossary):
    """`glossary.py lookup` の本体を返す。宣言と用語集の読み込みは glossary.py の関数を受ける。"""

    def cmd_lookup(a):
        decl = load_declaration(Path(a.root))
        if decl is None or not decl.source_path.exists():
            step_result.emit(step_result.result("glossary", "stopped", "用語集の宣言か用語集が無い"), step_result.EXIT_PRECONDITION)
        step_result.emit(glossary_lookup(load_glossary(decl), a.term, a.context, a.contexts))

    return cmd_lookup


def wrap_fields(sub) -> None:
    """`FIELD_COMMANDS` の本体を、`--field` を渡したときだけ値を取り出して出し直す形に包む（`json_pick.picked`）。"""
    for name in FIELD_COMMANDS:
        q = sub.choices[name]
        q.set_defaults(func=json_pick.picked(q.get_default("func"), "glossary"))
