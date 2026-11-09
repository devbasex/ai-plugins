"""Issue の記載項目の定義（lib/issue_items.py）と、その検査（issue-items.py の audit・templates）。#2737。

GitHub は `gh_rest.issue_list` を差し替えて答える（GitHub へは届かない）。テンプレートは一時ディレクトリへ写して壊す。
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[2]
sys.path.insert(0, str(SCRIPTS / "lib"))
import gh_rest  # noqa: E402
import issue_items as ii  # noqa: E402
import step_result  # noqa: E402

_spec = importlib.util.spec_from_file_location("ndf_issue_items_cli", SCRIPTS / "issue-items.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

DEFAULT = json.loads(ii.DEFAULT_ITEMS.read_text(encoding="utf-8"))
STORES = json.loads(ii.STORES.read_text(encoding="utf-8"))
BUG = """## 事象

A になる。

## 期待する結果

B になる。

### 再現手順

1. 打つ

## 環境: 版

10.27.45

## 影響

止まる。
"""


def run(*argv: str) -> tuple[int, dict]:
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as done:
        cli.main(list(argv))
    code = done.value.code
    obj = json.loads(out.getvalue().strip())
    assert step_result.validate_result(obj, code) == []
    assert obj["tool"] == "issue-items"
    return code, obj


def project(tmp_path: Path, items: dict | None = None, primary: str | None = None) -> Path:
    (tmp_path / ".ndf").mkdir(exist_ok=True)
    if items is not None:
        (tmp_path / ".ndf" / "issue-items.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    if primary is not None:
        (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"issues": {"primary": primary}}), encoding="utf-8")
    return tmp_path


def defined(**changes) -> dict:
    d = copy.deepcopy(DEFAULT)
    d.update(changes)
    return d


# --- 定義（受け入れ条件 3・4・17、I1〜I3） ----------------------------------------------


def test_the_default_definition_fills_every_kind_and_level():
    d = ii.load_definition()
    assert d.kinds == ["不具合", "機能", "調査", "運用"] and len(d.items) == 30
    for it in d.items:
        assert set(it.need) == set(d.kinds) and set(it.need.values()) <= set(ii.NEEDS)
        assert it.level.strip()
        assert "該当時" not in it.need.values() or it.when
    for store in ii.STORE_NAMES:
        assert all(d.rep(it, store).get("type") for it in d.items)


@pytest.mark.parametrize("breaks", ["need", "level", "when"])
def test_a_broken_item_stops_reading(tmp_path, breaks):
    d = copy.deepcopy(DEFAULT)
    target = next(it for it in d["items"] if it["id"] == "related")
    if breaks == "need":
        target["need"]["調査"] = ""
    elif breaks == "level":
        target["level"] = " "
    else:
        del target["when"]
    with pytest.raises(ii.DefinitionError, match="関連"):
        ii.load_definition(project(tmp_path, d))


def test_a_project_definition_replaces_the_default_and_takes_its_own_item(tmp_path):
    d = copy.deepcopy(DEFAULT)
    d["items"].append({"id": "customer", "name": "顧客", "need": dict.fromkeys(d["kinds"], "必須"), "level": "顧客名を書く"})
    got = ii.load_definition(project(tmp_path, d))
    assert got.path == tmp_path / ".ndf" / "issue-items.json"
    own = got.by_id["customer"]
    assert [got.rep(own, s)["type"] for s in ii.STORE_NAMES] == ["section", "section", "section"]
    _kind, gaps = ii.judge_body(BUG, ["bug"], {}, got)
    assert gaps == [ii.Gap("顧客", "missing")]


def test_a_store_without_a_default_representation_stops_reading(monkeypatch, tmp_path):
    stores = copy.deepcopy(STORES)
    del stores["stores"]["redmine"]["default"]
    path = tmp_path / "stores.json"
    path.write_text(json.dumps(stores, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ii, "STORES", path)
    with pytest.raises(ii.DefinitionError, match="redmine"):
        ii.load_definition()


# --- 本文の判定（受け入れ条件 6・12、I5・I6） ----------------------------------------------


def test_a_bug_body_with_every_required_item_has_no_gap():
    assert ii.judge_body(BUG, ["bug"], {}, ii.load_definition()) == ("不具合", [])


def test_a_form_body_reads_h3_and_treats_no_response_as_empty():
    body = "### 事象\n\nA\n\n### 期待する結果\n\n_No response_\n\n### 再現手順\n\n1\n\n### 環境: OS\n\nU\n\n### 影響\n\nx\n"
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == [ii.Gap("期待する結果", "empty")]


def test_the_context_adds_the_presumption_and_the_origin():
    gaps = ii.judge_body(BUG, ["bug"], {"ai": True, "origin": True}, ii.load_definition())[1]
    assert gaps == [ii.Gap("起票者の推定", "missing"), ii.Gap("由来", "missing")]


@pytest.mark.parametrize("labels", [[], ["bug", "enhancement"], ["question"]])
def test_a_body_without_exactly_one_kind_is_kind_unknown(labels):
    assert ii.judge_body(BUG, labels, {}, ii.load_definition()) == (None, [ii.Gap("種類", "kind")])


def test_only_process_sections_may_follow_the_request():
    d = ii.load_definition()
    ok = BUG + "\n## 依頼（原文）\n\n> 直して\n\n### 補足の引用\n\nx\n\n## 進行\n\n- [x] 要求\n\n## 人の判断待ち（開発ループ lproc）\n\nx\n"
    assert ii.judge_body(ok, ["bug"], {}, d)[1] == []
    bad = BUG + "\n## 依頼（原文）\n\n> 直して\n\n## 対象範囲\n\nx\n"
    assert ii.judge_body(bad, ["bug"], {}, d)[1] == [ii.Gap("依頼（原文）", "order")]


def test_a_project_definition_adds_its_own_process_section_headings(tmp_path):
    got = ii.load_definition(project(tmp_path, defined(record_headings=["作業記録"])))
    body = BUG + "\n## 依頼（原文）\n\n> 直して\n\n## 作業記録\n\nx\n\n## 進行\n\n- [x] 要求\n"
    assert ii.judge_body(body, ["bug"], {}, got)[1] == []
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == [ii.Gap("依頼（原文）", "order")]


def test_process_section_headings_that_are_not_strings_stop_reading(tmp_path):
    with pytest.raises(ii.DefinitionError, match="record_headings"):
        ii.load_definition(project(tmp_path, defined(record_headings="作業記録")))


def test_a_project_definition_renames_the_kind_labels(tmp_path):
    got = ii.load_definition(project(tmp_path, defined(kind_labels={"調査": "question", "不具合": "type:bug"})))
    assert got.kind_labels() == {"type:bug": "不具合", "enhancement": "機能", "question": "調査", "operation": "運用"}
    assert ii.kind_of(["type:bug"], got) == "不具合" and ii.kind_of(["bug"], got) is None


@pytest.mark.parametrize(
    "labels",
    [["question"], {"雑談": "chat"}, {"調査": ""}, {"調査": "x", "運用": "x"}, {"調査": "bug"}],
)
def test_kind_labels_that_cannot_name_one_kind_each_stop_reading(tmp_path, labels):
    with pytest.raises(ii.DefinitionError, match="kind_labels"):
        ii.load_definition(project(tmp_path, defined(kind_labels=labels)))


def test_headings_in_a_fence_are_not_sections():
    body = BUG.replace("## 影響\n\n止まる。\n", "```\n## 影響\n```\n")
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == [ii.Gap("影響", "missing")]


# --- judge_body の現状固定（リファクタリング前の振る舞い） ----------------------------------


def test_an_empty_body_lists_every_required_section_as_missing_in_definition_order():
    d = ii.load_definition()
    want = [ii.Gap(n, "missing") for n in ("事象", "期待する結果", "再現手順", "環境", "影響")]
    assert ii.judge_body(None, ["bug"], None, d) == ("不具合", want)
    assert ii.judge_body("", ["bug"], {}, d) == ("不具合", want)


def test_a_crlf_body_is_read_like_lf():
    assert ii.judge_body(BUG.replace("\n", "\r\n"), ["bug"], {}, ii.load_definition()) == ("不具合", [])


def test_a_level_one_heading_does_not_fill_an_item():
    body = BUG.replace("## 影響", "# 影響")
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == [ii.Gap("影響", "missing")]


def test_a_repeated_heading_is_empty_only_when_every_copy_is_empty():
    d = ii.load_definition()
    one_filled = BUG + "\n## 影響\n\n<!-- 空 -->\n"
    assert ii.judge_body(one_filled, ["bug"], {}, d)[1] == []
    both_empty = BUG.replace("止まる。", "_No response_") + "\n## 影響\n\n<!-- ndf:section-end -->\n"
    assert ii.judge_body(both_empty, ["bug"], {}, d)[1] == [ii.Gap("影響", "empty")]


def test_text_under_a_deeper_heading_fills_the_section():
    body = BUG.replace("## 影響\n\n止まる。\n", "## 影響\n\n#### 詳しく\n\n止まる。\n")
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == []


def test_a_context_key_that_is_false_or_unknown_adds_nothing():
    d = ii.load_definition()
    assert ii.judge_body(BUG, ["bug"], {"ai": False, "origin": False, "scope": True}, d)[1] == []


def test_gaps_keep_the_definition_order_and_the_order_gap_comes_last():
    body = BUG.replace("## 事象\n\nA になる。\n\n", "").replace("止まる。", "")
    body += "\n## 依頼（原文）\n\n> 直して\n\n## 対象範囲\n\nx\n"
    got = ii.judge_body(body, ["bug"], {"origin": True}, ii.load_definition())
    assert got == (
        "不具合",
        [
            ii.Gap("事象", "missing"),
            ii.Gap("影響", "empty"),
            ii.Gap("由来", "missing"),
            ii.Gap("依頼（原文）", "order"),
        ],
    )


def test_an_unknown_kind_still_reports_the_order_gap_after_the_kind_gap():
    body = BUG + "\n## 依頼（原文）\n\n> 直して\n\n## 対象範囲\n\nx\n"
    got = ii.judge_body(body, [], {}, ii.load_definition())
    assert got == (None, [ii.Gap("種類", "kind"), ii.Gap("依頼（原文）", "order")])


def test_a_definition_without_the_kind_item_names_the_gap_kind():
    d = ii.load_definition()
    d.by_id.pop("kind")
    assert ii.judge_body(BUG, ["bug"], {}, d) == (None, [ii.Gap("種類", "kind")])


# --- audit（受け入れ条件 13〜15・18、I9・I10） ----------------------------------------------


@pytest.fixture()
def listed(monkeypatch):
    calls = []

    def answer(value=None, error=None):
        def fake(repo, fields, state="open", labels=None, limit=30):
            calls.append({"repo": repo, "fields": fields, "state": state})
            return gh_rest.Attempt(value, error, "rest")

        monkeypatch.setattr(gh_rest, "issue_list", fake)

    answer.calls = calls
    return answer


def issue(number: int, body: str, *labels: str) -> dict:
    return {"number": number, "title": "t", "body": body, "labels": [{"name": x} for x in labels]}


def test_audit_lists_open_issues_with_gaps(listed, tmp_path, monkeypatch):
    monkeypatch.chdir(project(tmp_path))
    listed([issue(1, BUG, "bug"), issue(2, "## 事象\n\nA\n", "bug"), issue(3, BUG)])
    code, obj = run("audit", "--repo", "o/r")
    assert code == 1 and obj["status"] == "stopped"
    assert [(it["name"], it["missing"]) for it in obj["items"]] == [
        ("#2", ["期待する結果", "再現手順", "環境", "影響"]),
        ("#3", ["種類"]),
    ]
    assert obj["metrics"] == {"checked": 3, "missing": 2, "store": "github"}
    assert [c["state"] for c in listed.calls] == ["open"]


def test_audit_without_gaps_is_zero(listed, tmp_path, monkeypatch):
    monkeypatch.chdir(project(tmp_path))
    listed([issue(1, BUG, "bug")])
    code, obj = run("audit", "--repo", "o/r")
    assert (code, obj["status"], obj["items"]) == (0, "ok", [])


def test_audit_that_cannot_read_github_is_not_zero_gaps(listed, tmp_path, monkeypatch):
    monkeypatch.chdir(project(tmp_path))
    listed(error="HTTP 502")
    code, obj = run("audit", "--repo", "o/r")
    assert code == 2 and "読めなかった" in obj["summary"]


def test_audit_skips_a_store_that_is_not_github(listed, tmp_path, monkeypatch):
    monkeypatch.chdir(project(tmp_path, primary="redmine"))
    code, obj = run("audit")
    assert code == 0 and obj["summary"] == "検査しなかった: 課題の置き場は redmine"
    assert obj["metrics"] == {"checked": None, "missing": None, "store": "redmine"}
    assert listed.calls == []


# --- templates（受け入れ条件 7〜9、I4） -----------------------------------------------------


@pytest.fixture()
def copy_templates(monkeypatch):
    """フォームを `<tmp>/.github/ISSUE_TEMPLATE/` へ、骨格と仕様のテンプレートを `<tmp>/skills/` へ写す。"""

    def make(tmp_path: Path) -> Path:
        shutil.copytree(REPO_ROOT / ".github" / "ISSUE_TEMPLATE", tmp_path / ".github" / "ISSUE_TEMPLATE")
        for rel in cli.MARKDOWN_TEMPLATES:
            (tmp_path / "skills" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cli.SKILLS / rel, tmp_path / "skills" / rel)
        monkeypatch.setattr(cli, "SKILLS", tmp_path / "skills")
        return tmp_path

    return make


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_templates_agree_with_the_definition(tmp_path, copy_templates):
    code, obj = run("templates", "--root", str(copy_templates(tmp_path)))
    assert (code, obj["items"]) == (0, [])


FORM = Path(".github/ISSUE_TEMPLATE/bug_report.yml")
SKELETON = Path("skills/out-of-scope/SKILL.md")


@pytest.mark.parametrize(
    "rel, old, new, expected",
    [
        (FORM, "      label: 影響\n", "      label: 困りごと\n", [("困りごと", "unknown"), ("影響", "missing")]),
        (FORM, "      label: 期待する結果\n", "      label: 期待する結果\n      placeholder: x\n", []),
        (Path(".github/ISSUE_TEMPLATE/feature_request.yml"), 'labels: ["enhancement"]', 'labels: ["question"]', [("種類", "kind")]),
        (SKELETON, "## 影響\n", "", [("影響", "missing")]),
        (SKELETON, "## 由来\n（", "## 補足\n\n## 由来\n（", [("補足", "unknown")]),
    ],
)
def test_a_broken_template_is_reported_with_its_name_and_item(tmp_path, copy_templates, rel, old, new, expected):
    root = copy_templates(tmp_path)
    edit(root / rel, old, new)
    code, obj = run("templates", "--root", str(root))
    got = [(it["item"], it["result"]) for it in obj["items"]]
    assert got == expected and code == (1 if expected else 0)
    assert all(it["name"] == str(rel) for it in obj["items"])


def test_a_required_field_that_is_not_required_is_reported(tmp_path, copy_templates):
    root = copy_templates(tmp_path)
    text = (root / FORM).read_text(encoding="utf-8")
    head, tail = text.split("      label: 影響\n", 1)
    tail = tail.replace("    validations:\n      required: true\n", "", 1)
    (root / FORM).write_text(head + "      label: 影響\n" + tail, encoding="utf-8")
    code, obj = run("templates", "--root", str(root))
    assert code == 1 and [(it["item"], it["result"]) for it in obj["items"]] == [("影響", "not-required")]


def test_missing_templates_cannot_be_read(tmp_path):
    code, obj = run("templates", "--root", str(tmp_path))
    assert code == 2 and obj["status"] == "stopped"


# --- form_mismatches の現状固定（フォーム 1 つの食い違い） -------------------------------------


def field(label, required=None) -> dict:
    el = {"type": "textarea", "attributes": {"label": label}}
    if required is not None:
        el["validations"] = {"required": required}
    return el


FEATURE_FIELDS = [field("目的", True), field("対象範囲", True), field("受け入れ条件", True)]


def mismatches(form: dict) -> list[tuple[str, str]]:
    got = cli.form_mismatches("f.yml", form, ii.load_definition(REPO_ROOT))
    assert all(it["kind"] == "template" and it["name"] == "f.yml" for it in got)
    return [(it["item"], it["result"]) for it in got]


@pytest.mark.parametrize("labels", [None, [], ["question"], ["bug", "enhancement"], "bug, enhancement"])
def test_form_mismatches_without_exactly_one_kind_returns_only_the_kind_gap(labels):
    form = {"body": [field("困りごと")]}
    if labels is not None:
        form["labels"] = labels
    assert mismatches(form) == [("種類", "kind")]


def test_form_mismatches_reads_comma_separated_labels():
    assert mismatches({"labels": " enhancement , ", "body": FEATURE_FIELDS}) == []


def test_form_mismatches_skips_elements_without_a_label():
    body = [None, "x", {}, {"attributes": None}, {"attributes": {"label": ""}}, {"type": "markdown"}] + FEATURE_FIELDS
    assert mismatches({"labels": ["enhancement"], "body": body}) == []


def test_form_mismatches_without_a_body_lists_every_required_section_as_missing():
    assert mismatches({"labels": ["enhancement"], "body": None}) == [
        ("目的", "missing"),
        ("対象範囲", "missing"),
        ("受け入れ条件", "missing"),
    ]


def test_form_mismatches_lists_unknown_labels_first_then_required_sections_in_definition_order():
    body = [field("困りごと", True), field("受け入れ条件"), field("目的", False), field("補足")]
    assert mismatches({"labels": ["enhancement"], "body": body}) == [
        ("困りごと", "unknown"),
        ("補足", "unknown"),
        ("目的", "not-required"),
        ("対象範囲", "missing"),
        ("受け入れ条件", "not-required"),
    ]


def test_form_mismatches_counts_a_repeated_field_as_required_if_any_copy_is():
    body = [field("目的: 補足"), field("目的（別）", True), field("対象範囲", True), field("対象範囲"), field("受け入れ条件", "yes")]
    assert mismatches({"labels": ["enhancement"], "body": body}) == []


def test_form_mismatches_ignores_optional_fields_that_are_not_required():
    body = FEATURE_FIELDS + [field("影響"), field("前提", False)]
    assert mismatches({"labels": ["enhancement"], "body": body}) == []
