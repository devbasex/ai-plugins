"""設計文書の種類の契約（`contracts/design-docs.json`）と読み手 `doc_kinds`（#3144 の決定 12・I11）。

契約の読み取り・種類の判定と、読む側（用語チェックの既定の対象・行数の検査・設計 PR の分類）が名前の末尾を
定数に持たず契約から引くことを見る。契約を差し替えると 3 つの読む側がそろって変わる。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
LIB = SCRIPTS / "lib"
CROSS_REVIEW = SCRIPTS.parent / "skills" / "cross-review" / "scripts"
for _p in (LIB, CROSS_REVIEW):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import doc_kinds  # noqa: E402
import classifications  # noqa: E402
from review_lib import categories  # noqa: E402

PLUGIN_CONTRACT = SCRIPTS.parent / "contracts" / "design-docs.json"


@pytest.fixture(autouse=True)
def _restore():
    doc_kinds.use_contract(None)
    yield
    doc_kinds.use_contract(None)


def _contract(tmp_path: pathlib.Path, kinds: list[dict], **extra) -> pathlib.Path:
    path = tmp_path / "contracts" / "design-docs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "dir": "issues", "kinds": kinds, **extra}, ensure_ascii=False), encoding="utf-8")
    return path


def _plugin_kinds() -> list[dict]:
    return json.loads(PLUGIN_CONTRACT.read_text(encoding="utf-8"))["kinds"]


def test_the_plugin_contract_is_read_in_order() -> None:
    place, kinds = doc_kinds._contract()
    assert place == "issues"
    assert [k["kind"] for k in kinds] == ["requirements", "design", "decisions", "detail"]


def test_the_contract_is_read_once(tmp_path, monkeypatch) -> None:
    doc_kinds.use_contract(_contract(tmp_path, _plugin_kinds()))
    doc_kinds._contract()
    calls = []
    real = pathlib.Path.read_text
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: calls.append(self) or real(self, *a, **k))
    doc_kinds._contract()
    assert calls == []


@pytest.mark.parametrize(
    "body",
    [
        None,  # ファイルが無い
        "{ not json",
        json.dumps(
            {
                "version": 2,
                "dir": "issues",
                "kinds": [{"kind": "a", "suffix": "-a.md", "line_limit": True, "glossary": True, "review": True}],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "dir": "issues",
                "kinds": [
                    {"kind": "a", "suffix": "-a.md", "line_limit": True, "glossary": True, "review": True},
                    {"kind": "a", "suffix": "-b.md", "line_limit": True, "glossary": True, "review": True},
                ],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "dir": "issues",
                "kinds": [{"kind": "a", "suffix": "-a.md", "line_limit": "yes", "glossary": True, "review": True}],
            }
        ),
    ],
    ids=["missing", "not-json", "version", "duplicate", "flag-not-bool"],
)
def test_an_unreadable_contract_stops(tmp_path, body) -> None:
    path = tmp_path / "design-docs.json"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    doc_kinds.use_contract(path)
    with pytest.raises(doc_kinds.DocKindsError):
        doc_kinds.suffixes("review")


def test_suffixes_and_patterns_follow_the_flags() -> None:
    assert doc_kinds.suffixes("glossary") == ("-requirements.md", "-design.md", "-design-decisions.md", "-design-detail.md")
    assert doc_kinds.suffixes("line_limit") == ("-design.md", "-design-decisions.md", "-design-detail.md")
    assert doc_kinds.patterns("glossary")[-1] == "issues/*-design-detail.md"
    with pytest.raises(doc_kinds.DocKindsError):
        doc_kinds.suffixes("unknown")


@pytest.mark.parametrize(
    "path,placed,kind",
    [
        ("issues/issue-7-design-detail.md", True, "detail"),
        ("issues/issue-7-design-decisions.md", True, "decisions"),
        ("issues/issue-7-design.md", True, "design"),
        ("issues/issue-7-design-api.md", True, None),
        ("docs/issue-7-design.md", True, None),
        ("docs/issue-7-design.md", False, "design"),
    ],
)
def test_doc_kind(path, placed, kind) -> None:
    assert doc_kinds.doc_kind(path, placed=placed) == kind


def test_template_path() -> None:
    assert doc_kinds.template_path("detail") == SCRIPTS.parent / "skills" / "design" / "references" / "detail-template.md"
    for kind in ("design", "nothing"):
        with pytest.raises(doc_kinds.DocKindsError):
            doc_kinds.template_path(kind)


def test_the_glossary_default_targets_come_from_the_contract() -> None:
    spec = importlib.util.spec_from_file_location("glossary_under_doc_kinds", SCRIPTS / "glossary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.DEFAULT_PATHS == doc_kinds.patterns("glossary")


def test_the_line_limit_covers_the_detail_document(tmp_path) -> None:
    (tmp_path / "issues").mkdir()
    for name in ("issue-7-design-detail.md", "issue-7-requirements.md"):
        (tmp_path / "issues" / name).write_text("x\n" * 1001, encoding="utf-8")
    got = classifications.oversized_design_docs(tmp_path, ["issues/issue-7-design-detail.md", "issues/issue-7-requirements.md"])
    assert [g["path"] for g in got] == ["issues/issue-7-design-detail.md"]


def test_the_domain_model_is_looked_for_in_line_limited_documents(tmp_path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "issue-7-design.md").write_text("# t\n\n## ドメインモデル\n", encoding="utf-8")
    (tmp_path / "docs" / "issue-7-requirements.md").write_text("# t\n\n## ドメインモデル\n", encoding="utf-8")
    assert classifications.has_domain_model(tmp_path, ["docs/issue-7-design.md"])
    assert not classifications.has_domain_model(tmp_path, ["docs/issue-7-requirements.md"])


@pytest.mark.parametrize(
    "path,want",
    [
        ("issues/issue-7-design-detail.md", True),
        ("issues/issue-7-requirements.md", True),
        ("issues/issue-7-design-api.md", False),
        ("docs/issue-7-design.md", False),
    ],
)
def test_the_design_pr_documents(path, want) -> None:
    assert categories._is_design_doc_path(path) is want


def test_a_new_kind_in_the_contract_reaches_every_reader(tmp_path) -> None:
    """契約へ種類を足すと、行数の検査・設計 PR の分類・用語チェックの既定の対象がそろって変わる。"""
    kinds = _plugin_kinds() + [{"kind": "notes", "suffix": "-design-notes.md", "line_limit": True, "glossary": True, "review": True}]
    doc_kinds.use_contract(_contract(tmp_path, kinds))
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "issue-7-design-notes.md").write_text("x\n" * 1001, encoding="utf-8")
    assert classifications.oversized_design_docs(tmp_path, ["issues/issue-7-design-notes.md"])
    assert categories._is_design_doc_path("issues/issue-7-design-notes.md")
    spec = importlib.util.spec_from_file_location("glossary_under_new_kind", SCRIPTS / "glossary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "issues/*-design-notes.md" in mod.DEFAULT_PATHS


def test_the_readers_stop_on_an_unreadable_contract(tmp_path) -> None:
    doc_kinds.use_contract(tmp_path / "missing.json")
    with pytest.raises(doc_kinds.DocKindsError):
        classifications.oversized_design_docs(tmp_path, ["issues/issue-7-design.md"])
    with pytest.raises(doc_kinds.DocKindsError):
        categories._is_design_doc_path("issues/issue-7-design.md")


def test_has_flag_keeps_the_readers_matching(tmp_path) -> None:
    """現状固定: 置き場の見方は placed で変わり、末尾が重なるときは最長一致の種類の真偽でなく、真の末尾のどれかで終わるかで決まる。"""
    row = {"line_limit": False, "glossary": False, "review": False}
    kinds = [{**row, "kind": "a", "suffix": "-x.md", "line_limit": True}, {**row, "kind": "b", "suffix": "-y-x.md", "review": True}]
    doc_kinds.use_contract(_contract(tmp_path, kinds))
    assert doc_kinds.doc_kind("issues/issue-1-y-x.md") == "b"
    assert doc_kinds.has_flag("issues/issue-1-y-x.md", "line_limit") and doc_kinds.has_flag("issues/issue-1-y-x.md", "review")
    assert not doc_kinds.has_flag("issues/issue-1-x.md", "review")
    assert not doc_kinds.has_flag("docs/issue-1-x.md", "line_limit")
    assert doc_kinds.has_flag("docs/issue-1-x.md", "line_limit", placed=False)
    assert not doc_kinds.has_flag("issues/issue-1-z.md", "line_limit", placed=False)
    doc_kinds.use_contract(tmp_path / "missing.json")
    with pytest.raises(doc_kinds.DocKindsError):
        doc_kinds.has_flag("issues/issue-1-x.md", "line_limit")
