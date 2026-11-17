"""区間の報告の形の説明の文（#3242・#3746）。`report` が読んだ形の定義（`ReportForm`）から作る。

`describe_form` は形の説明（置き方・値ごとの意味と要る項目・材料の書き方・値ごとの例）の文を作る。
コントラクトのファイルを Read できない conductor へ、hook が止めて返す文と案内に載せて渡す。純粋な関数だけを置く。
標準ライブラリだけを import する（hook の経路では jsonschema を読む `ndf.contracts` を使わない）。
"""

from __future__ import annotations

from dataclasses import asdict

from ndf.domains.hooks.report import EQUALS, NONE_VALUE, PREFIX, RESULT_ITEM, Condition, Material, ReportForm

# 形の説明の言い回し。値・項目の名前・条件の値・材料の 4 欄は持たず、コントラクトの `shape` から埋める
TEXT_HEAD = "区間の報告の形（コントラクトのファイルから作った説明）:"
TEXT_PLACEMENT = (
    "- 置き方: 最後の応答の末尾に置く。「- {result}: <値>」の行の後ろに「- <項目>: <値>」の行を並べる（どの行も行頭は「- 」）。"
    "項目は {items} の順。要らない項目は「{none}」と書く"
)
TEXT_VALUES = "結果の値（値: 意味。要る項目は無しにしない）:"
TEXT_ALWAYS = "要る項目: {items}"
TEXT_PREFIX = "「{item}」が {cond} で始まるとき「{need}」も要る"
TEXT_EQUALS = "「{item}」が {cond} のとき「{need}」も要る"
# 条件の種類（`when` の要素のキー）ごとの文の型。種類は `report.MATCHERS` と同じキー
CONDITION_TEXTS = {PREFIX: TEXT_PREFIX, EQUALS: TEXT_EQUALS}
TEXT_MATERIAL = (
    "- 材料の書き方: 「{item}」は {body} か、{url_prefix} で始まる URL（空白で区切って並べてよい）。"
    "{body} と書くときは、結果の行より前に「{heading}」の行を置き、そのセクションに材料を書く"
)
TEXT_EXAMPLE = "例（{value}）:"
EXAMPLE_MATERIAL = "<人が判断するための材料>"


def _condition_text(cond: Condition) -> str:
    """条件つきの項目 1 つを文にする。"""
    return CONDITION_TEXTS[cond.op].format(item=cond.item, cond=cond.value, need="・".join(cond.need))


def _requirement_lines(form: ReportForm) -> list[str]:
    """結果の値ごとに、値・意味・要る項目・条件つきの項目を 1 行にする（`form.values` の順）。"""
    lines = []
    for value in form.values:
        rule = form.required[value]
        parts = [form.meanings[value], TEXT_ALWAYS.format(items="・".join(rule.always) or NONE_VALUE)]
        parts.extend(_condition_text(c) for c in rule.when)
        lines.append(f"- {value}: " + "。".join(parts))
    return lines


def _example(value: str, form: ReportForm) -> str:
    """結果の値 1 つの、判定に通る区間の報告の例。要る項目は仮の値 `<項目>`、ほかは `無し`。"""
    mat = form.material
    always = form.required[value].always
    lines = [f"- {RESULT_ITEM}: {value}"]
    lines.extend(f"- {name}: {_example_value(name, always, mat)}" for name in form.items if name != RESULT_ITEM)
    # 材料を本文で書く例だけ、結果の行より前に材料のセクションを置く
    head = [mat.heading, "", EXAMPLE_MATERIAL, ""] if mat.item in always and mat.item != RESULT_ITEM else []
    return "\n".join(head + lines) + "\n"


def _example_value(name: str, always: tuple[str, ...], mat: Material) -> str:
    """例の項目 1 つの値。要らない項目は `無し`、材料は本文、ほかの要る項目は仮の値 `<項目>`。"""
    if name not in always:
        return NONE_VALUE
    if name == mat.item:
        return mat.body
    return f"<{name}>"


def form_examples(form: ReportForm) -> dict[str, str]:
    """結果の値ごとに、判定（`report_errors`）に通る区間の報告の例を 1 つ作る。"""
    return {value: _example(value, form) for value in form.values}


def describe_form(form: ReportForm) -> str:
    """形の定義から、置き方・値ごとの意味と要る項目・材料の書き方・値ごとの例の順に並べた形の説明の文を作る。"""
    items = "・".join(name for name in form.items if name != RESULT_ITEM)
    lines = [TEXT_HEAD, TEXT_PLACEMENT.format(result=RESULT_ITEM, items=items, none=NONE_VALUE), TEXT_VALUES]
    lines.extend(_requirement_lines(form))
    lines.append(TEXT_MATERIAL.format(**asdict(form.material)))
    for value, example in form_examples(form).items():
        lines.extend([TEXT_EXAMPLE.format(value=value), "```markdown", example.rstrip("\n"), "```"])
    return "\n".join(lines)
