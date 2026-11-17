"""区間の報告の読み手（#3242・#3746）。人のいない実行の conductor の最後の応答を、契約の形の宣言で判定する。

形（結果の値・項目・値ごとに要る項目・材料の書き方）の正本は `contracts/hooks/unattended-report.json` の `shape` で、
この部品はそこから読む（コードに書き写さない）。結果の行は応答の文の最後の `- 結果:` の行で、`RESULT_RE` が
プラグインの中の唯一の置き場である。`load_form` だけが表を読み、ほかは純粋な関数。
形の説明の文（`describe_form`）は `report_text` が作る。
標準ライブラリだけを import する（hook の経路では jsonschema を読む `ndf.contracts` を使わない）。
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import asdict, dataclass, fields

from ndf.domains.hooks import tables

FORM_TABLE = "hooks/unattended-report.json"
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

# 条件の種類（`when` の要素の鍵）ごとの照合。種類はこの表と `report_text.CONDITION_TEXTS`（文の型）の鍵だけ
PREFIX, EQUALS = "prefix", "equals"
MATCHERS = {PREFIX: str.startswith, EQUALS: str.__eq__}


class FormError(ValueError):
    """契約のファイルが読めない・形の宣言が違う。"""


@dataclass(frozen=True)
class Condition:
    """値ごとに要る項目の条件（`when` の 1 つ）。項目 `item` の値が種類 `op`（`MATCHERS` の鍵）で `value` に合えば `need` が要る。"""

    item: str
    need: tuple[str, ...]
    op: str
    value: str

    def matches(self, items: dict[str, str]) -> bool:
        return MATCHERS[self.op](items.get(self.item, ""), self.value)  # noqa: NDF502 利用者の入力: 応答に無い項目は書いていない値として読む


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


MATERIAL_KEYS = tuple(f.name for f in fields(Material))


@dataclass(frozen=True)
class ReportForm:
    """形の宣言（`shape`）を読み手が使う形にしたもの。"""

    items: tuple[str, ...]
    material: Material
    required: dict[str, Requirement]
    meanings: dict[str, str]

    @property
    def values(self) -> tuple[str, ...]:
        """結果の値（宣言の順。`meanings` の鍵）。"""
        return tuple(self.meanings)


def load_form(rel: str = FORM_TABLE) -> ReportForm:
    """契約の表を読み、形の宣言を `ReportForm` にする。読めなければ `FormError`。"""
    try:
        data = tables.load(rel)
    except tables.TableError as e:
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
    ops = set(row) & set(MATCHERS) if isinstance(row, dict) else set()
    if not isinstance(row, dict) or not isinstance(row.get("item"), str) or len(ops) != 1 or not set(row) <= {"item", "need", *MATCHERS}:
        raise FormError(f"shape.{where}.when の要素は item・need と prefix か equals の 1 つを持つ")
    (op,) = ops
    cond = row[op]
    need = _strs(row.get("need"), f"{where}.when.need")
    if not isinstance(cond, str) or row["item"] not in items or not set(need) <= set(items):
        raise FormError(f"shape.{where}.when の項目名が items に無いか、条件が文字列でない")
    return Condition(item=row["item"], need=need, op=op, value=cond)


def form_from_dict(data: dict) -> ReportForm:
    """契約のファイルの中身から `shape` を取り出し、読み手が使う形へ直す。形が違えば `FormError`。"""
    shape = data.get("shape") if isinstance(data, dict) else None
    if not isinstance(shape, dict):
        raise FormError("shape が無い")
    meanings = _meanings(shape.get("values"))
    values = tuple(meanings)
    items = _strs(shape.get("items"), "items")
    material = shape.get("material")
    _check_material(material, items)
    req = _required(shape.get("required"), values, items)
    return ReportForm(items=items, material=Material(**material), required=req, meanings=meanings)


def _meanings(values_decl: object) -> dict[str, str]:
    """結果の値と意味（`shape.values`）は空でない文字列どうしの空でない辞書。値 → 意味の辞書を返す。"""
    if (
        not isinstance(values_decl, dict)
        or not values_decl
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in values_decl.items())
    ):
        raise FormError("shape.values は値 → 意味の空でない辞書")
    return dict(values_decl)


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
    """応答の文の最後の結果の行の一致。行が無い・文字列でなければ None。"""
    if not isinstance(text, str):
        return None
    last = deque(RESULT_RE.finditer(text), maxlen=1)
    return last[0] if last else None


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
        if _absent(items.get(name, NONE_VALUE)) and name not in missing:  # noqa: NDF502 利用者の入力: 応答に無い項目は書いていない値として読む
            missing.append(name)
    return missing


def _material_errors(text: str, m: re.Match, items: dict[str, str], form: ReportForm) -> list[str]:
    """材料の値が本文か URL か、本文なら見出しが区間の報告（最後の結果の行 `m`）より前にあるかを確かめる。"""
    mat = form.material
    got = items.get(mat.item, "")  # noqa: NDF502 利用者の入力: 応答に無い項目は書いていない値として読む
    if _absent(got):
        return []
    if got == mat.body:
        return [] if _has_heading(text, m, mat.heading) else [ERR_NO_MATERIAL_HEADING.format(**asdict(mat))]
    if _is_url_list(got, mat.url_prefix):
        return []
    return [ERR_BAD_MATERIAL.format(**asdict(mat), value=got)]


def _has_heading(text: str, m: re.Match, heading: str) -> bool:
    """結果の行 `m` より前に、前後の空白を除くと `heading` になる行があるか。"""
    return any(line.strip() == heading for line in text[: m.start()].splitlines())


def _is_url_list(got: str, prefix: str) -> bool:
    """空白で区切った語がどれも `prefix` で始まり、`prefix` より長いか。"""
    return all(w.startswith(prefix) and len(w) > len(prefix) for w in got.split())


def report_errors(text: object, form: ReportForm) -> list[str]:
    """応答の文が区間の報告の形に合わない理由を並べる（結果の値・要る項目・材料の順）。合えば空。"""
    m = _last_result(text)
    if m is None:
        return [ERR_NO_RESULT]
    value = m.group(1)
    if value not in form.values:
        return [ERR_BAD_VALUE.format(values=" / ".join(form.values), value=value)]
    items = _items_after(m)
    errors = [ERR_MISSING.format(item=name, value=value) for name in _missing_items(value, items, form)]
    return errors + _material_errors(m.string, m, items, form)
