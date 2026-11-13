"""区間の報告の読み手（#3242）。人のいない実行の conductor の最後の応答を、契約のファイルの形の宣言で判定する。

形（結果の値・項目・値ごとに要る項目・材料の書き方）の正本は `contracts/forms/unattended-report.json` の `shape` で、
この部品はそこから読む（コードに書き写さない）。結果の行の読み方は `ndf-next-block.json` の規則の後半と同じで、
`RESULT_RE` がプラグインの中の唯一の置き場である。`load_form` だけがファイルを読み、ほかは純粋な関数。
標準ライブラリだけを import する（hook 用の仮想環境が無くても `python3` で動く）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import jsonio

FORM_PATH = Path(__file__).resolve().parents[2] / "contracts" / "forms" / "unattended-report.json"
RESULT_RE = re.compile(r"^\s*-\s*結果:\s*(.*?)\s*$", re.MULTILINE)
ITEM_RE = re.compile(r"^\s*-\s*([^:\n]+?):\s*(.*?)\s*$", re.MULTILINE)
NONE_VALUE = "無し"

ERR_NO_RESULT = "結果の行が無い"
ERR_BAD_VALUE = "結果の値が {values} のどれでもない: {value}"
ERR_MISSING = "「{item}」が無いか無しになっている（結果が {value} のとき要る）"
ERR_NO_MATERIAL_HEADING = "「{item}」が{body}なのに、区間の報告より前に「{heading}」の節が無い"
ERR_BAD_MATERIAL = "「{item}」が{body}でも {url_prefix} の URL でもない: {value}"

MATERIAL_KEYS = ("item", "body", "heading", "url_prefix")


class FormError(ValueError):
    """契約のファイルが読めない・形の宣言が違う。"""


@dataclass(frozen=True)
class ReportForm:
    """形の宣言（`shape`）を読み手が使う形にしたもの。"""

    values: tuple[str, ...]
    items: tuple[str, ...]
    material: dict[str, str]
    required: dict[str, dict]


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


def _when(rows: object, where: str, items: tuple[str, ...]) -> tuple[dict, ...]:
    if not isinstance(rows, list):
        raise FormError(f"shape.{where}.when は並び")
    out = []
    for row in rows:
        ok = isinstance(row, dict) and isinstance(row.get("item"), str) and len(set(row) & {"prefix", "equals"}) == 1
        if not ok or not set(row) <= {"item", "prefix", "equals", "need"}:
            raise FormError(f"shape.{where}.when の要素は item・need と prefix か equals の 1 つを持つ")
        cond = row.get("prefix", row.get("equals"))
        need = _strs(row.get("need"), f"{where}.when.need")
        if not isinstance(cond, str) or row["item"] not in items or not set(need) <= set(items):
            raise FormError(f"shape.{where}.when の項目名が items に無いか、条件が文字列でない")
        out.append({**row, "need": need})
    return tuple(out)


def form_from_dict(data: dict) -> ReportForm:
    """契約のファイルの中身から `shape` を取り出し、読み手が使う形へ直す。形が違えば `FormError`。"""
    shape = data.get("shape") if isinstance(data, dict) else None
    if not isinstance(shape, dict):
        raise FormError("shape が無い")
    values_decl, material, required = shape.get("values"), shape.get("material"), shape.get("required")
    if not isinstance(values_decl, dict) or not values_decl or not all(isinstance(k, str) and k for k in values_decl):
        raise FormError("shape.values は値 → 意味の空でない辞書")
    values = tuple(values_decl)
    items = _strs(shape.get("items"), "items")
    _check_material(material, items)
    req = _required(required, values, items)
    return ReportForm(values=values, items=items, material=dict(material), required=req)


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


def _required(required: object, values: tuple[str, ...], items: tuple[str, ...]) -> dict[str, dict]:
    """値ごとに要る項目（`shape.required`）を確かめ、always と when をタプルにした辞書を返す。"""
    if not isinstance(required, dict) or set(required) != set(values):
        raise FormError("shape.required のキーは shape.values と同じ集合")
    req: dict[str, dict] = {}
    for value, row in required.items():
        if not isinstance(row, dict) or set(row) != {"always", "when"}:
            raise FormError(f"shape.required.{value} は always と when を持つ")
        always = _strs(row["always"], f"required.{value}.always")
        if not set(always) <= set(items):
            raise FormError(f"shape.required.{value}.always の項目名が items に無い")
        req[value] = {"always": always, "when": _when(row["when"], f"required.{value}", items)}
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
    assert isinstance(text, str)
    return {k.strip(): v for k, v in ITEM_RE.findall(text, m.start())}


def _missing_items(value: str, items: dict[str, str], form: ReportForm) -> list[str]:
    """結果の値ごとに要る項目のうち、無いか `無し` のものを宣言の順に返す。"""
    rule = form.required[value]
    need = list(rule["always"])
    for cond in rule["when"]:
        got = items.get(cond["item"], "")
        hit = got.startswith(cond["prefix"]) if "prefix" in cond else got == cond["equals"]
        if hit:
            need.extend(cond["need"])
    missing: list[str] = []
    for name in need:
        if items.get(name, NONE_VALUE) in ("", NONE_VALUE) and name not in missing:
            missing.append(name)
    return missing


def _material_errors(text: str, items: dict[str, str], form: ReportForm) -> list[str]:
    """材料の値が本文か URL か、本文なら見出しが区間の報告より前にあるかを確かめる。"""
    mat = form.material
    got = items.get(mat["item"], "")
    if got in ("", NONE_VALUE):
        return []
    if got == mat["body"]:
        m = _last_result(text)
        before = text[: m.start()] if m else ""
        if any(line.strip() == mat["heading"] for line in before.splitlines()):
            return []
        return [ERR_NO_MATERIAL_HEADING.format(**mat)]
    if all(w.startswith(mat["url_prefix"]) and len(w) > len(mat["url_prefix"]) for w in got.split()):
        return []
    return [ERR_BAD_MATERIAL.format(**mat, value=got)]


def report_errors(text: object, form: ReportForm) -> list[str]:
    """応答の文が区間の報告の形に合わない理由を並べる（結果の値・要る項目・材料の順）。合えば空。"""
    if _last_result(text) is None:
        return [ERR_NO_RESULT]
    assert isinstance(text, str)
    value = result_value(text)
    if value not in form.values:
        return [ERR_BAD_VALUE.format(values=" / ".join(form.values), value=value)]
    items = report_items(text)
    errors = [ERR_MISSING.format(item=name, value=value) for name in _missing_items(value, items, form)]
    return errors + _material_errors(text, items, form)
