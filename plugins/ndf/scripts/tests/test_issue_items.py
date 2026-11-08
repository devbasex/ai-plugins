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


def test_headings_in_a_fence_are_not_sections():
    body = BUG.replace("## 影響\n\n止まる。\n", "```\n## 影響\n```\n")
    assert ii.judge_body(body, ["bug"], {}, ii.load_definition())[1] == [ii.Gap("影響", "missing")]


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
