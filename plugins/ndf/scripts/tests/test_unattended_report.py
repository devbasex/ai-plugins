"""lib/unattended_report.py（区間の報告の読み手。#3242）の試験。

2 つの契約（`unattended-report.json`・`ndf-next-block.json`）の例の組を同じ関数で読み、結果の行の読み方が食い違わないことと、
値ごとに要る項目を契約のファイルの形の宣言から読むことを縛る。
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import unattended_report as ur  # noqa: E402

FORMS = SCRIPTS.parent / "contracts" / "forms"
REPORT = json.loads((FORMS / "unattended-report.json").read_text(encoding="utf-8"))
NEXT = json.loads((FORMS / "ndf-next-block.json").read_text(encoding="utf-8"))
FORM = ur.load_form()


@pytest.mark.parametrize("case", REPORT["cases"], ids=range(len(REPORT["cases"])))
def test_the_report_cases_give_the_declared_result_and_errors(case):
    assert ur.result_value(case["text"]) == case["result"]
    assert ur.report_errors(case["text"], FORM) == case["errors"]


@pytest.mark.parametrize("case", NEXT["cases"], ids=range(len(NEXT["cases"])))
def test_the_ndf_next_cases_read_the_same_result_line(case):
    """ndf-next の規則の後半（最後の結果の行が「判断が要る」なら起動しない）と同じ読み方をする。"""
    if len(case["blocks"]) == 1:
        assert (ur.result_value(case["text"]) != "判断が要る") == case["launch"]


def test_load_form_reads_the_three_values_of_the_plugin_contract():
    assert FORM.values == tuple(REPORT["shape"]["values"])
    assert len(FORM.values) == 3


@pytest.mark.parametrize("body", ["", "{", "[]"])
def test_load_form_refuses_an_unreadable_file(tmp_path: Path, body):
    path = tmp_path / "f.json"
    if body:
        path.write_text(body, encoding="utf-8")
    with pytest.raises(ur.FormError):
        ur.load_form(path)


def _shape(**over) -> dict:
    data = copy.deepcopy(REPORT)
    data["shape"].update(over)
    return data


@pytest.mark.parametrize(
    "data",
    [
        {k: v for k, v in REPORT.items() if k != "shape"},
        _shape(required={"完了": {"always": [], "when": []}}),
        _shape(required={**REPORT["shape"]["required"], "完了": {"always": ["無い項目"], "when": []}}),
        _shape(material={**REPORT["shape"]["material"], "item": "無い項目"}),
        _shape(values={}),
    ],
)
def test_form_from_dict_refuses_a_wrong_shape(data):
    with pytest.raises(ur.FormError):
        ur.form_from_dict(data)


def test_the_required_items_come_from_the_contract_not_the_code():
    """形の宣言の required を書き換えると判定が変わる（要る項目を関数に書き写していない）。"""
    text = "## 区間の報告\n\n- 結果: 完了\n- 課題: #1\n"
    assert ur.report_errors(text, FORM) == []
    data = _shape(required={**REPORT["shape"]["required"], "完了": {"always": ["理由"], "when": []}})
    assert ur.report_errors(text, ur.form_from_dict(data)) == [ur.ERR_MISSING.format(item="理由", value="完了")]


def test_result_value_reads_the_last_line_and_trims_spaces():
    assert ur.result_value("- 結果: 完了\n\n  -   結果:   判断が要る  \n") == "判断が要る"


@pytest.mark.parametrize("text", ["結果: 完了", "", None, 3])
def test_result_value_is_empty_without_a_result_line(text):
    assert ur.result_value(text) == ""


def test_report_items_reads_after_the_last_result_line():
    text = "- 課題: #9\n- 結果: 完了\n- 課題: #1\n- Pull Request: https://example.com/p/1\n- 課題: #2\n"
    assert ur.report_items(text) == {"結果": "完了", "課題": "#2", "Pull Request": "https://example.com/p/1"}
    assert ur.report_items("本文だけ") == {}


@pytest.mark.parametrize(
    ("material", "before", "errors"),
    [
        ("本文", "## 判断の材料\n\n中身\n\n", []),
        ("https://example.com/a https://example.com/b", "", []),
        ("本文", "", ["NO_HEADING"]),
        ("/tmp/approval-request.md", "", ["BAD"]),
        ("http://example.com/a", "", ["BAD"]),
    ],
)
def test_the_material_is_the_body_or_https_urls(material, before, errors):
    text = f"{before}## 区間の報告\n\n- 結果: 判断が要る\n- 区分: ndf-ask:cost\n- 問い: 払ってよいか\n- 材料: {material}\n"
    mat = asdict(FORM.material)
    want = {"NO_HEADING": ur.ERR_NO_MATERIAL_HEADING.format(**mat), "BAD": ur.ERR_BAD_MATERIAL.format(**mat, value=material)}
    assert ur.report_errors(text, FORM) == [want[e] for e in errors]


def test_a_material_heading_after_the_report_does_not_count():
    text = "## 区間の報告\n\n- 結果: 判断が要る\n- 区分: ndf-ask:cost\n- 問い: 払ってよいか\n- 材料: 本文\n\n## 判断の材料\n"
    assert ur.report_errors(text, FORM) == [ur.ERR_NO_MATERIAL_HEADING.format(**asdict(FORM.material))]


def test_a_gate_needs_the_gate_name_and_the_pull_request():
    base = "## 判断の材料\n\nx\n\n- 結果: 判断が要る\n- 問い: マージしてよいか\n- 材料: 本文\n"
    assert ur.report_errors(base + "- 区分: ndf-ask:cost\n", FORM) == []
    got = ur.report_errors(base + "- 区分: ndf-gate:1\n", FORM)
    assert got == [ur.ERR_MISSING.format(item="承認ゲート", value="判断が要る")]
    got = ur.report_errors(base + "- 区分: ndf-gate:1\n- 承認ゲート: 設計 Pull Request のマージ\n", FORM)
    assert got == [ur.ERR_MISSING.format(item="Pull Request", value="判断が要る")]
    # 設計承認ゲートの委任の承認（スプリントの MVV）はまだ Pull Request が無い
    assert ur.report_errors(base + "- 区分: ndf-gate:1\n- 承認ゲート: 設計承認ゲートの委任の承認\n", FORM) == []


# --- form_from_dict: 形の宣言の検査を分岐ごとに固定する（現状固定） ---

SHAPE = REPORT["shape"]
REQ = SHAPE["required"]
MAT = SHAPE["material"]
WHEN = {"item": "区分", "prefix": "ndf-gate:", "need": ["承認ゲート"]}


def _req(value: str, row) -> dict:
    return _shape(required={**REQ, value: row})


def _cond(w: dict) -> ur.Condition:
    """形の宣言の when の 1 つを、読み手の `Condition` にしたもの。"""
    (op,) = set(w) & set(ur.MATCHERS)
    return ur.Condition(item=w["item"], need=tuple(w["need"]), op=op, value=w[op])


def test_form_from_dict_turns_the_plugin_shape_into_the_reader_form():
    form = ur.form_from_dict(REPORT)
    assert form.values == ("判断が要る", "完了", "できなかった")
    assert form.items == tuple(SHAPE["items"])
    assert asdict(form.material) == MAT
    assert list(form.required) == list(REQ)
    assert form.meanings == SHAPE["values"] and list(form.meanings) == list(form.values)
    for value, row in REQ.items():
        assert form.required[value].always == tuple(row["always"])
        assert form.required[value].when == tuple(_cond(w) for w in row["when"])
    assert form == FORM


def test_form_from_dict_keeps_the_declared_order_and_accepts_equals_conditions():
    data = _shape(
        values={"b": "B", "a": "A"},
        required={
            "a": {"always": ["理由", "課題"], "when": [{"item": "区分", "equals": "x", "need": ["問い"]}]},
            "b": {"always": [], "when": [WHEN]},
        },
    )
    form = ur.form_from_dict(data)
    assert form.values == ("b", "a")
    assert form.required["a"] == ur.Requirement(always=("理由", "課題"), when=(_cond({"item": "区分", "equals": "x", "need": ("問い",)}),))
    assert form.required["b"].when == (_cond({**WHEN, "need": ("承認ゲート",)}),)


def test_form_from_dict_reads_a_shape_with_a_single_value():
    """値が 1 つだけの形でも、値と意味を持った形として読める（#3329 の詳細設計の境界値）。"""
    form = ur.form_from_dict(_shape(values={"済んだ": "通し切った"}, required={"済んだ": {"always": [], "when": []}}))
    assert form.values == ("済んだ",)
    assert form.meanings == {"済んだ": "通し切った"}
    assert list(ur.form_examples(form)) == ["済んだ"]


def test_form_from_dict_accepts_an_empty_need_as_it_is_now():
    """現状: when.need の空の並びは通る（_strs が空の並びを拒まない。「空でない」の文言と食い違う。不具合の疑い）。"""
    form = ur.form_from_dict(_req("完了", {"always": [], "when": [{**WHEN, "need": []}]}))
    assert form.required["完了"].when == (_cond({**WHEN, "need": ()}),)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (None, "shape が無い"),
        ([], "shape が無い"),
        ({"shape": []}, "shape が無い"),
        (_shape(values=["完了"]), "shape.values は値 → 意味の空でない辞書"),
        (_shape(values={}), "shape.values は値 → 意味の空でない辞書"),
        (_shape(values={"": "空"}), "shape.values は値 → 意味の空でない辞書"),
        (_shape(values={**SHAPE["values"], "完了": ""}), "shape.values は値 → 意味の空でない辞書"),
        (_shape(values={**SHAPE["values"], "完了": 1}), "shape.values は値 → 意味の空でない辞書"),
        # 現状: 空の並びは _strs を通り、material.item の検査で落ちる（「空でない」の文言と食い違う。不具合の疑い）
        (_shape(items=[]), "shape.material.item が items に無い"),
        (_shape(items="結果"), "shape.items は空でない文字列の並び"),
        (_shape(items=["結果", ""]), "shape.items は空でない文字列の並び"),
        (_shape(items=["結果", 1]), "shape.items は空でない文字列の並び"),
        (_shape(material=[]), "shape.material は item・body・heading・url_prefix の文字列を持つ"),
        (
            _shape(material={k: v for k, v in MAT.items() if k != "heading"}),
            "shape.material は item・body・heading・url_prefix の文字列を持つ",
        ),
        (_shape(material={**MAT, "x": "y"}), "shape.material は item・body・heading・url_prefix の文字列を持つ"),
        (_shape(material={**MAT, "body": ""}), "shape.material は item・body・heading・url_prefix の文字列を持つ"),
        (_shape(material={**MAT, "url_prefix": 1}), "shape.material は item・body・heading・url_prefix の文字列を持つ"),
        (_shape(material={**MAT, "item": "無い項目"}), "shape.material.item が items に無い"),
        (_shape(required=[]), "shape.required のキーは shape.values と同じ集合"),
        (_shape(required={k: v for k, v in REQ.items() if k != "完了"}), "shape.required のキーは shape.values と同じ集合"),
        (_shape(required={**REQ, "余分": {"always": [], "when": []}}), "shape.required のキーは shape.values と同じ集合"),
        (_req("完了", []), "shape.required.完了 は always と when を持つ"),
        (_req("完了", {"always": []}), "shape.required.完了 は always と when を持つ"),
        (_req("完了", {"always": [], "when": [], "x": 1}), "shape.required.完了 は always と when を持つ"),
        (_req("完了", {"always": "理由", "when": []}), "shape.required.完了.always は空でない文字列の並び"),
        (_req("完了", {"always": [""], "when": []}), "shape.required.完了.always は空でない文字列の並び"),
        (_req("完了", {"always": ["無い項目"], "when": []}), "shape.required.完了.always の項目名が items に無い"),
        (_req("完了", {"always": [], "when": {}}), "shape.required.完了.when は並び"),
        (_req("完了", {"always": [], "when": ["x"]}), "shape.required.完了.when の要素は item・need と prefix か equals の 1 つを持つ"),
        (
            _req("完了", {"always": [], "when": [{"item": "区分", "need": ["理由"]}]}),
            "shape.required.完了.when の要素は item・need と prefix か equals の 1 つを持つ",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "equals": "x"}]}),
            "shape.required.完了.when の要素は item・need と prefix か equals の 1 つを持つ",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "item": 1}]}),
            "shape.required.完了.when の要素は item・need と prefix か equals の 1 つを持つ",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "x": 1}]}),
            "shape.required.完了.when の要素は item・need と prefix か equals の 1 つを持つ",
        ),
        (
            _req("完了", {"always": [], "when": [{k: v for k, v in WHEN.items() if k != "need"}]}),
            "shape.required.完了.when.need は空でない文字列の並び",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "prefix": 1}]}),
            "shape.required.完了.when の項目名が items に無いか、条件が文字列でない",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "item": "無い項目"}]}),
            "shape.required.完了.when の項目名が items に無いか、条件が文字列でない",
        ),
        (
            _req("完了", {"always": [], "when": [{**WHEN, "need": ["無い項目"]}]}),
            "shape.required.完了.when の項目名が items に無いか、条件が文字列でない",
        ),
    ],
)
def test_form_from_dict_names_each_wrong_shape(data, message):
    with pytest.raises(ur.FormError) as e:
        ur.form_from_dict(data)
    assert str(e.value) == message


def test_form_from_dict_checks_values_before_items_and_material_before_required():
    """複数の誤りがあるとき、values → items → material → material.item → required のキー → 行の順で最初の 1 つを返す。"""
    data = _shape(values={}, items="結果", material=[], required=[])
    order = [
        ("values", "shape.values", SHAPE["values"]),
        ("items", "shape.items", SHAPE["items"]),
        ("material", "shape.material は", MAT),
        ("required", "shape.required のキー", REQ),
    ]
    for key, message, fixed in order:
        with pytest.raises(ur.FormError) as e:
            ur.form_from_dict(data)
        assert str(e.value).startswith(message)
        data["shape"][key] = fixed
    assert ur.form_from_dict(data) == FORM


# --- 形の説明（#3329） ---


def _parts(form: ur.ReportForm) -> list[str]:
    """形の説明に現れるべき、契約の宣言の語（値・意味・要る項目・条件・材料の 4 欄）。"""
    out = [*form.values, *form.meanings.values(), *asdict(form.material).values()]
    for rule in form.required.values():
        out.extend(rule.always)
        for cond in rule.when:
            out.extend([cond.item, cond.value, *cond.need])
    return out


def test_describe_form_carries_every_declared_word_and_the_examples():
    text = ur.describe_form(FORM)
    for word in _parts(FORM):
        assert word in text
    for example in ur.form_examples(FORM).values():
        assert example in text
    assert ur.describe_form(FORM) == text


def test_the_examples_fit_the_contract_they_come_from():
    """値ごとの例は同じ契約の判定で誤り 0 件で、結果の行の値がその値になる（I3）。"""
    examples = ur.form_examples(FORM)
    assert list(examples) == list(FORM.values)
    for value, example in examples.items():
        assert ur.report_errors(example, FORM) == []
        assert ur.result_value(example) == value


def test_only_an_example_with_the_body_material_has_the_heading():
    examples = ur.form_examples(FORM)
    heading = FORM.material.heading
    assert examples["判断が要る"].index(heading) < examples["判断が要る"].index("- 結果:")
    assert ur.report_items(examples["判断が要る"])[FORM.material.item] == FORM.material.body
    assert heading not in examples["完了"]
    assert set(ur.report_items(examples["完了"]).values()) == {"完了", ur.NONE_VALUE}


def test_the_requirement_lines_follow_the_values_and_name_no_items_as_none():
    lines = ur._requirement_lines(FORM)
    assert [line.split(":")[0] for line in lines] == [f"- {v}" for v in FORM.values]
    assert ur.TEXT_ALWAYS.format(items=ur.NONE_VALUE) in lines[FORM.values.index("完了")]


def test_the_condition_text_follows_prefix_or_equals_and_lists_every_need():
    prefix = ur.Condition(item="区分", need=("承認ゲート", "Pull Request"), op="prefix", value="ndf-gate:")
    equals = ur.Condition(item="区分", need=("人に頼むこと",), op="equals", value="ndf-ask:human-action")
    assert ur._condition_text(prefix) == ur.TEXT_PREFIX.format(item="区分", cond="ndf-gate:", need="承認ゲート・Pull Request")
    assert ur._condition_text(equals) == ur.TEXT_EQUALS.format(item="区分", cond="ndf-ask:human-action", need="人に頼むこと")


def test_the_description_follows_another_shape():
    """値・要る項目・条件・材料を変えた別の契約では、説明と例がその形に変わり、元の値が現れない（I2・I3）。"""
    data = _shape(
        values={"止めた": "人を待つ", "済んだ": "通し切った"},
        material={**MAT, "body": "下に書いた", "heading": "## 材料の節"},
        required={
            "止めた": {"always": ["問い", "材料"], "when": [{"item": "問い", "prefix": "x", "need": ["人に頼むこと"]}]},
            "済んだ": {"always": ["課題", "理由"], "when": []},
        },
    )
    form = ur.form_from_dict(data)
    text = ur.describe_form(form)
    for word in _parts(form):
        assert word in text
    for old in FORM.values:
        assert old not in text
    assert FORM.material.heading not in text
    for value, example in ur.form_examples(form).items():
        assert ur.report_errors(example, form) == []
        assert ur.result_value(example) == value
