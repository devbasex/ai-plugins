"""区間の報告の読み手（#3242）。人のいない実行の conductor の最後の応答を、契約のファイルの形の宣言で判定する。

形（結果の値・項目・値ごとに要る項目・材料の書き方）の正本は `contracts/forms/unattended-report.json` の `shape` で、
この部品はそこから読む（コードに書き写さない）。結果の行の読み方は `ndf-next-block.json` の規則の後半と同じで、
`RESULT_RE` がプラグインの中の唯一の置き場である。`load_form` だけがファイルを読み、ほかは純粋な関数。
`describe_form` は同じ宣言から形の説明（置き方・値ごとの意味と要る項目・材料の書き方・値ごとの例）の文を作る（#3329）。
契約のファイルを Read できない conductor へ、hook が差し戻しの文と案内に載せて渡す。
標準ライブラリだけを import する（hook 用の仮想環境が無くても `python3` で動く）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import jsonio

FORM_PATH = Path(__file__).resolve().parents[2] / "contracts" / "forms" / "unattended-report.json"
# 行内の空白は `[ \t]` だけにする（`\s` は改行を含み、空の値を次の行の文字列で補ってしまう）
RESULT_ITEM = "結果"
RESULT_RE = re.compile(rf"^[ \t]*-[ \t]*{re.escape(RESULT_ITEM)}:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
ITEM_RE = re.compile(r"^[ \t]*-[ \t]*([^:\n]+?):[ \t]*(.*?)[ \t]*$", re.MULTILINE)
NONE_VALUE = "無し"

ERR_NO_RESULT = "結果の行が無い"
ERR_BAD_VALUE = "結果の値が {values} のどれでもない: {value}"
ERR_MISSING = "「{item}」が無いか無しになっている（結果が {value} のとき要る）"
ERR_NO_MATERIAL_HEADING = "「{item}」が{body}なのに、区間の報告より前に「{heading}」の節が無い"
ERR_BAD_MATERIAL = "「{item}」が{body}でも {url_prefix} の URL でもない: {value}"

MATERIAL_KEYS = ("item", "body", "heading", "url_prefix")

# 形の説明の言い回し。値・項目の名前・条件の値・材料の 4 欄は持たず、契約の `shape` から埋める
TEXT_HEAD = "区間の報告の形（契約のファイルから作った説明）:"
TEXT_PLACEMENT = (
    "- 置き方: 最後の応答の末尾に置く。「- {result}: <値>」の行の後ろに「- <項目>: <値>」の行を並べる（どの行も行頭は「- 」）。"
    "項目は {items} の順。要らない項目は「{none}」と書く"
)
TEXT_VALUES = "結果の値（値: 意味。要る項目は無しにしない）:"
TEXT_ALWAYS = "要る項目: {items}"
TEXT_PREFIX = "「{item}」が {cond} で始まるとき「{need}」も要る"
TEXT_EQUALS = "「{item}」が {cond} のとき「{need}」も要る"
TEXT_MATERIAL = (
    "- 材料の書き方: 「{item}」は {body} か、{url_prefix} で始まる URL（空白で区切って並べてよい）。"
    "{body} と書くときは、結果の行より前に「{heading}」の行を置き、その節に材料を書く"
)
TEXT_EXAMPLE = "例（{value}）:"
EXAMPLE_MATERIAL = "<人が判断するための材料>"


class FormError(ValueError):
    """契約のファイルが読めない・形の宣言が違う。"""


@dataclass(frozen=True)
class Condition:
    """値ごとに要る項目の条件（`when` の 1 つ）。項目 `item` の値が `prefix` で始まるか `equals` と同じなら `need` が要る。"""

    item: str
    need: tuple[str, ...]
    prefix: str | None = None
    equals: str | None = None

    def matches(self, items: dict[str, str]) -> bool:
        got = items.get(self.item, "")
        return got.startswith(self.prefix) if self.prefix is not None else got == self.equals


@dataclass(frozen=True)
class Requirement:
    """結果の値 1 つに要る項目（`shape.required` の 1 行）。"""

    always: tuple[str, ...]
    when: tuple[Condition, ...]


@dataclass(frozen=True)
class Material:
    """材料の書き方（`shape.material`）。項目 `item` は本文 `body`（見出し `heading` の節）か `url_prefix` の URL。"""

    item: str
    body: str
    heading: str
    url_prefix: str


@dataclass(frozen=True)
class ReportForm:
    """形の宣言（`shape`）を読み手が使う形にしたもの。"""

    values: tuple[str, ...]
    items: tuple[str, ...]
    material: Material
    required: dict[str, Requirement]
    meanings: dict[str, str]


def load_form(path: Path = FORM_PATH) -> ReportForm:
    """契約のファイルを読み、形の宣言を `ReportForm` にする。読めなければ `FormError`。"""
    try:
        data = jsonio.read(path, want=dict)
    except jsonio.JsonReadError as e:
        raise FormError(str(e)) from None
    return form_from_dict(data)


def _strs(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise FormError(f"shape.{where} は空でない文字列の並び")
    return tuple(value)


def _when(rows: object, where: str, items: tuple[str, ...]) -> tuple[Condition, ...]:
    if not isinstance(rows, list):
        raise FormError(f"shape.{where}.when は並び")
    return tuple(_condition_from_row(row, where, items) for row in rows)


def _condition_from_row(row: object, where: str, items: tuple[str, ...]) -> Condition:
    """`when` の 1 行を確かめて `Condition` にする（形 → need → 項目名と条件の順）。"""
    if (
        not isinstance(row, dict)
        or not isinstance(row.get("item"), str)
        or len(set(row) & {"prefix", "equals"}) != 1
        or not set(row) <= {"item", "prefix", "equals", "need"}
    ):
        raise FormError(f"shape.{where}.when の要素は item・need と prefix か equals の 1 つを持つ")
    cond = row.get("prefix", row.get("equals"))
    need = _strs(row.get("need"), f"{where}.when.need")
    if not isinstance(cond, str) or row["item"] not in items or not set(need) <= set(items):
        raise FormError(f"shape.{where}.when の項目名が items に無いか、条件が文字列でない")
    return Condition(item=row["item"], need=need, prefix=row.get("prefix"), equals=row.get("equals"))


def form_from_dict(data: dict) -> ReportForm:
    """契約のファイルの中身から `shape` を取り出し、読み手が使う形へ直す。形が違えば `FormError`。"""
    shape = data.get("shape") if isinstance(data, dict) else None
    if not isinstance(shape, dict):
        raise FormError("shape が無い")
    values_decl, material, required = shape.get("values"), shape.get("material"), shape.get("required")
    if (
        not isinstance(values_decl, dict)
        or not values_decl
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in values_decl.items())
    ):
        raise FormError("shape.values は値 → 意味の空でない辞書")
    values = tuple(values_decl)
    items = _strs(shape.get("items"), "items")
    _check_material(material, items)
    req = _required(required, values, items)
    return ReportForm(values=values, items=items, material=Material(**material), required=req, meanings=dict(values_decl))


def _check_material(material: object, items: tuple[str, ...]) -> None:
    """材料の書き方（`shape.material`）は MATERIAL_KEYS の空でない文字列を持ち、item は items のどれか。"""
    if (
        not isinstance(material, dict)
        or set(material) != set(MATERIAL_KEYS)
        or not all(isinstance(material[k], str) and material[k] for k in MATERIAL_KEYS)
    ):
        raise FormError(f"shape.material は {'・'.join(MATERIAL_KEYS)} の文字列を持つ")
    if material["item"] not in items:
        raise FormError("shape.material.item が items に無い")


def _required(required: object, values: tuple[str, ...], items: tuple[str, ...]) -> dict[str, Requirement]:
    """値ごとに要る項目（`shape.required`）を確かめ、値 → `Requirement` の辞書を返す。"""
    if not isinstance(required, dict) or set(required) != set(values):
        raise FormError("shape.required のキーは shape.values と同じ集合")
    req: dict[str, Requirement] = {}
    for value, row in required.items():
        if not isinstance(row, dict) or set(row) != {"always", "when"}:
            raise FormError(f"shape.required.{value} は always と when を持つ")
        always = _strs(row["always"], f"required.{value}.always")
        if not set(always) <= set(items):
            raise FormError(f"shape.required.{value}.always の項目名が items に無い")
        req[value] = Requirement(always=always, when=_when(row["when"], f"required.{value}", items))
    return req


def _last_result(text: object) -> re.Match | None:
    if not isinstance(text, str):
        return None
    last = None
    for last in RESULT_RE.finditer(text):
        pass
    return last


def result_value(text: object) -> str:
    """応答の文の最後の結果の行の値（前後の空白を除く）。行が無い・文字列でなければ空の文字列。"""
    m = _last_result(text)
    return m.group(1) if m else ""


def report_items(text: object) -> dict[str, str]:
    """最後の結果の行より後ろの `- <項目>: <値>` の行を項目の辞書にする（同じ項目は後ろの値）。結果の行も含める。"""
    m = _last_result(text)
    if m is None:
        return {}
    return _items_after(m)


def _items_after(m: re.Match) -> dict[str, str]:
    """結果の行 `m` から後ろの `- <項目>: <値>` の行を項目の辞書にする。"""
    return {k.strip(): v for k, v in ITEM_RE.findall(m.string, m.start())}


def _absent(value: str) -> bool:
    """項目の値が無い（空か `無し`）か。"""
    return value in ("", NONE_VALUE)


def _missing_items(value: str, items: dict[str, str], form: ReportForm) -> list[str]:
    """結果の値ごとに要る項目のうち、無いか `無し` のものを宣言の順に返す。"""
    rule = form.required[value]
    need = list(rule.always)
    for cond in rule.when:
        if cond.matches(items):
            need.extend(cond.need)
    missing: list[str] = []
    for name in need:
        if _absent(items.get(name, NONE_VALUE)) and name not in missing:
            missing.append(name)
    return missing


def _material_errors(text: str, m: re.Match, items: dict[str, str], form: ReportForm) -> list[str]:
    """材料の値が本文か URL か、本文なら見出しが区間の報告（最後の結果の行 `m`）より前にあるかを確かめる。"""
    mat = form.material
    got = items.get(mat.item, "")
    if _absent(got):
        return []
    if got == mat.body:
        before = text[: m.start()]
        if any(line.strip() == mat.heading for line in before.splitlines()):
            return []
        return [ERR_NO_MATERIAL_HEADING.format(**asdict(mat))]
    if all(w.startswith(mat.url_prefix) and len(w) > len(mat.url_prefix) for w in got.split()):
        return []
    return [ERR_BAD_MATERIAL.format(**asdict(mat), value=got)]


def report_errors(text: object, form: ReportForm) -> list[str]:
    """応答の文が区間の報告の形に合わない理由を並べる（結果の値・要る項目・材料の順）。合えば空。"""
    m = _last_result(text)
    if m is None:
        return [ERR_NO_RESULT]
    assert isinstance(text, str)
    value = m.group(1)
    if value not in form.values:
        return [ERR_BAD_VALUE.format(values=" / ".join(form.values), value=value)]
    items = _items_after(m)
    errors = [ERR_MISSING.format(item=name, value=value) for name in _missing_items(value, items, form)]
    return errors + _material_errors(text, m, items, form)


def _condition_text(cond: Condition) -> str:
    """条件つきの項目 1 つを文にする。"""
    need = "・".join(cond.need)
    if cond.prefix is not None:
        return TEXT_PREFIX.format(item=cond.item, cond=cond.prefix, need=need)
    return TEXT_EQUALS.format(item=cond.item, cond=cond.equals, need=need)


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
    head: list[str] = []
    lines = [f"- {RESULT_ITEM}: {value}"]
    for name in form.items:
        if name == RESULT_ITEM:
            continue
        if name not in always:
            got = NONE_VALUE
        elif name == mat.item:
            got = mat.body
            head = [mat.heading, "", EXAMPLE_MATERIAL, ""]
        else:
            got = f"<{name}>"
        lines.append(f"- {name}: {got}")
    return "\n".join(head + lines) + "\n"


def form_examples(form: ReportForm) -> dict[str, str]:
    """結果の値ごとに、判定（`report_errors`）に通る区間の報告の例を 1 つ作る。"""
    return {value: _example(value, form) for value in form.values}


def describe_form(form: ReportForm) -> str:
    """形の宣言から、置き方・値ごとの意味と要る項目・材料の書き方・値ごとの例の順に並べた形の説明の文を作る。"""
    items = "・".join(name for name in form.items if name != RESULT_ITEM)
    lines = [TEXT_HEAD, TEXT_PLACEMENT.format(result=RESULT_ITEM, items=items, none=NONE_VALUE), TEXT_VALUES]
    lines.extend(_requirement_lines(form))
    lines.append(TEXT_MATERIAL.format(**asdict(form.material)))
    for value, example in form_examples(form).items():
        lines.extend([TEXT_EXAMPLE.format(value=value), "```markdown", example.rstrip("\n"), "```"])
    return "\n".join(lines)
