"""glossary.py: プロジェクトの用語集の設定・入口の検査・文書の生成・用語チェック・差分。

用語集の語は、どのプロジェクトにも無い通販の領域の語だけで書く（I6: スクリプトは特定の
プロジェクトの語を持たず、すべて用語集から読む）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "glossary.py"
PY = sys.executable

DEFAULT_SOURCE = "docs/glossary/glossary.json"
DEFAULT_DOCUMENT = "docs/glossary.md"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def write_json(root, rel, obj):
    return write(root, rel, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _parsed(p):
    lines = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(lines[-1]) if lines else None), p.stderr


def run(root, *args):
    """分岐はプロセスの中で main を呼んで確かめる（起動の配線は `run_cli` の代表の数件）。"""
    return _parsed(inproc.run(SCRIPT, *args, "--root", str(root), cwd=root))


def run_cli(root, *args):
    return _parsed(subprocess.run([PY, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True, cwd=root))


def commit(root, msg="c"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


def snapshot(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if ".git" in p.relative_to(root).parts:
            continue
        out[str(p.relative_to(root))] = p.read_bytes() if p.is_file() and not p.is_symlink() else None
    return out


def shop_glossary(terms=None):
    """ai-plugins の語を 1 つも含まない用語集（I6）。"""
    return {
        "version": 1,
        "contexts": [
            {"id": "ordering", "name": "受注", "meaning": "注文を受けて確定するまで"},
            {"id": "shipping", "name": "配送", "meaning": "確定した注文を届けるまで"},
        ],
        "terms": terms
        if terms is not None
        else [
            {
                "term": "注文",
                "context": "ordering",
                "meaning": "顧客が買うと決めた品の組",
                "deprecated": ["オーダー"],
                "source": "docs/ordering.md",
            },
            {"term": "カートリッジ", "context": "ordering", "meaning": "交換できる部品"},
            {"term": "荷物", "context": "shipping", "meaning": "1 回で運ぶ箱", "deprecated": ["cart"], "source": ""},
        ],
    }


def declaration(**check):
    return {
        "version": 1,
        "format": "json",
        "source": DEFAULT_SOURCE,
        "document": DEFAULT_DOCUMENT,
        "check": {
            "paths": check.get("paths", ["issues/*.md"]),
            **({"term_sections": check["term_sections"]} if "term_sections" in check else {}),
        },
    }


@pytest.fixture
def bare(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    write(root, "README.md", "# 店\n")
    commit(root, "init")
    return root


@pytest.fixture
def repo(bare):
    """宣言と用語集と文書が揃ったリポジトリ。feature/x のブランチにいる。"""
    write_json(bare, ".ndf/glossary.json", declaration())
    write_json(bare, DEFAULT_SOURCE, shop_glossary())
    code, _, err = run(bare, "render")
    assert code == 0, err
    commit(bare, "glossary")
    git(bare, "checkout", "-q", "-b", "feature/x")
    return bare


def rules_of(out):
    return sorted({it["rule"] for it in out["items"]})


# --- 不変条件（I1〜I6） --------------------------------------------------------


def test_i1_same_term_twice_in_one_context_is_duplicate(repo):
    g = shop_glossary()
    g["terms"].append({"term": "注文", "context": "ordering", "meaning": "別の意味"})
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, err = run(repo, "check", "--rules", "structure")
    assert code == 1
    assert rules_of(out) == ["duplicate"]
    assert out["items"][0]["term"] == "注文"
    assert "duplicate" in err


def test_i1_same_term_in_other_contexts_is_allowed(repo):
    g = shop_glossary()
    g["terms"].append({"term": "注文", "context": "shipping", "meaning": "配送の依頼"})
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, err = run(repo, "check", "--rules", "structure")
    assert code == 0, (out, err)


def test_i2_deprecated_word_overlapping_live_term_in_same_context_is_schema(repo):
    g = shop_glossary()
    g["terms"][0]["deprecated"] = ["カートリッジ"]
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"]


def test_i3_term_pointing_to_unknown_context_is_schema(repo):
    g = shop_glossary()
    g["terms"][0]["context"] = "billing"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"]


def test_source_outside_declared_spec_paths_is_unconfirmed(repo):
    """check.source_paths を宣言すると、語の正本は確定仕様だけを指す。空と URL は見ない。"""
    d = declaration()
    d["check"]["source_paths"] = ["docs/specifications/*.md"]
    write_json(repo, ".ndf/glossary.json", d)
    g = shop_glossary()
    g["terms"][0]["source"] = "issues/issue-1-design.md"
    g["terms"][1]["source"] = "https://example.com/spec"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["unconfirmed_source"] and out["items"][0]["term"] == "注文"
    g["terms"][0]["source"] = "docs/specifications/ordering.md"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, err = run(repo, "check", "--rules", "structure")
    assert code == 0, (out, err)


def test_source_paths_absent_does_not_check_sources(repo):
    g = shop_glossary()
    g["terms"][0]["source"] = "issues/issue-1-design.md"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, err = run(repo, "check", "--rules", "structure")
    assert code == 0, (out, err)


def test_declared_paths_star_does_not_cross_slash(repo):
    """check.paths の `*` は `/` をまたがない（lib/pathmatch.py。fnmatch の頃は issues/old/a.md にも当たった）。"""
    write(repo, "issues/old/a.md", "オーダーを受ける。\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 0, (out, err)
    config = json.loads((repo / ".ndf/glossary.json").read_text(encoding="utf-8"))
    config["check"]["paths"] = ["issues/**/*.md"]
    write_json(repo, ".ndf/glossary.json", config)
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and [(it["path"], it["term"]) for it in out["items"]] == [("issues/old/a.md", "オーダー")]


def test_fence_closes_only_with_the_same_mark(repo):
    """囲みは CommonMark の規則で閉じる（lib/md.py）。``` の中の `~~~` の行では閉じない。"""
    write(repo, "issues/a.md", "```\n~~~\nオーダー\n```\nオーダー\n")
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and [(it["line"], it["term"]) for it in out["items"]] == [(5, "オーダー")]


def test_i4_glossary_changed_without_render_is_stale_document(repo):
    g = shop_glossary()
    g["terms"].append({"term": "返品", "context": "ordering", "meaning": "受け取った品を戻すこと"})
    write_json(repo, DEFAULT_SOURCE, g)
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["stale_document"]
    code, _, _ = run(repo, "render", "--check")
    assert code == 1
    run(repo, "render")
    assert run(repo, "check", "--rules", "structure")[0] == 0


@pytest.mark.parametrize("mode", ["standard", "legacy-refactor"])
def test_i5_modes_with_design_stop_without_declaration(bare, mode):
    code, out, _ = run(bare, "gate", "--mode", mode)
    assert code == 1
    assert out["tool"] == "glossary" and out["status"] == "stopped"
    assert len(out["items"]) == 3
    assert "init" in out["items"][0]["text"]
    assert "requirements-design" in out["items"][2]["text"]
    assert "init" in out["summary"]


def test_i6_all_rules_run_from_a_glossary_without_project_words(repo):
    """ai-plugins の語を含まない用語集で、5 つの規則がすべて当たる。"""
    write(repo, "issues/a.md", "# 要求\n\n## 用語\n\n| 用語 | 意味 |\n| --- | --- |\n| 在庫 | 置いてある品 |\n\nオーダーを受ける。\n")
    g = shop_glossary()
    g["terms"].append({"term": "荷物", "context": "shipping", "meaning": "重複"})
    g["terms"].append({"term": "送り状", "context": "nowhere", "meaning": "伝票"})
    write_json(repo, DEFAULT_SOURCE, g)
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1
    assert rules_of(out) == ["deprecated", "duplicate", "schema", "stale_document", "unregistered"]


# --- 入口の検査（gate） ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["light", "operation", "documentation"])
def test_gate_passes_modes_without_design(bare, mode):
    assert run(bare, "gate", "--mode", mode)[0] == 0


@pytest.mark.parametrize(
    "terms", [[], [{"term": "注文", "context": "ordering", "meaning": "m"}, {"term": "荷物", "context": "shipping", "meaning": "m"}]]
)
def test_gate_passes_empty_or_small_glossary(bare, terms):
    write_json(bare, ".ndf/glossary.json", declaration())
    write_json(bare, DEFAULT_SOURCE, shop_glossary(terms))
    run(bare, "render")
    assert run(bare, "gate", "--mode", "standard")[0] == 0


def test_gate_unreadable_declaration_is_2(bare):
    write(bare, ".ndf/glossary.json", "{broken")
    assert run(bare, "gate", "--mode", "standard")[0] == 2


def test_gate_unreadable_glossary_is_2(bare):
    write_json(bare, ".ndf/glossary.json", declaration())
    write(bare, DEFAULT_SOURCE, "{broken")
    assert run(bare, "gate", "--mode", "standard")[0] == 2


@pytest.mark.parametrize("field,value", [("version", 2), ("format", "yaml")])
def test_gate_unknown_version_or_format_is_2(bare, field, value):
    d = declaration()
    d[field] = value
    write_json(bare, ".ndf/glossary.json", d)
    write_json(bare, DEFAULT_SOURCE, shop_glossary([]))
    assert run(bare, "gate", "--mode", "standard")[0] == 2


# --- 起こす（init） ----------------------------------------------------------------


def test_init_creates_three_files_and_gate_passes(bare):
    code, out, err = run(bare, "init")
    assert code == 0, err
    decl = json.loads((bare / ".ndf/glossary.json").read_text())
    assert decl["source"] == DEFAULT_SOURCE and decl["document"] == DEFAULT_DOCUMENT
    g = json.loads((bare / DEFAULT_SOURCE).read_text())
    assert g == {"version": 1, "contexts": [], "terms": []}
    assert (bare / DEFAULT_DOCUMENT).is_file()
    assert len(out["items"]) == 3
    assert run(bare, "gate", "--mode", "standard")[0] == 0
    assert run(bare, "check", "--rules", "structure")[0] == 0


def test_init_does_not_overwrite(repo):
    before = snapshot(repo)
    code, out, _ = run(repo, "init")
    assert code == 0 and out["items"] == []
    assert snapshot(repo) == before


@pytest.mark.parametrize("present", ["decl", "glossary", "decl+glossary", "decl+document"])
def test_init_fills_only_missing_files(bare, present):
    d = declaration()
    d["source"], d["document"] = "terms/words.json", "terms/words.md"
    if "decl" in present:
        write_json(bare, ".ndf/glossary.json", d)
    src = "terms/words.json" if "decl" in present else DEFAULT_SOURCE
    if "glossary" in present:
        write_json(bare, src, shop_glossary())
    if "document" in present:
        write(bare, "terms/words.md", "# 手で書いた\n")
    kept = snapshot(bare)
    code, _, err = run(bare, "init")
    assert code == 0, err
    after = snapshot(bare)
    for k, v in kept.items():
        assert after[k] == v
    assert run(bare, "gate", "--mode", "standard")[0] == 0


def test_init_leaves_unreadable_glossary_alone(bare):
    write_json(bare, ".ndf/glossary.json", declaration())
    write(bare, DEFAULT_SOURCE, "{broken")
    before = snapshot(bare)
    assert run(bare, "init")[0] == 2
    assert snapshot(bare) == before


def test_init_uses_given_places_without_declaration(bare):
    code, _, _ = run(bare, "init", "--source", "g/terms.json", "--document", "g/terms.md")
    assert code == 0
    assert json.loads((bare / ".ndf/glossary.json").read_text())["source"] == "g/terms.json"
    assert (bare / "g/terms.md").is_file()


# --- 文書（render） ----------------------------------------------------------------


def test_render_has_heading_and_six_columns_per_context(repo):
    doc = (repo / DEFAULT_DOCUMENT).read_text()
    assert doc.startswith("# 用語集\n")
    assert DEFAULT_SOURCE in doc
    assert doc.index("## 受注（`ordering`）") < doc.index("## 配送（`shipping`）")
    assert "| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |" in doc
    assert "| 注文 | — | 顧客が買うと決めた品の組 | オーダー | — | `docs/ordering.md` |" in doc
    assert "| カートリッジ | — | 交換できる部品 | — | — | — |" in doc
    assert doc.index("| 注文 |") < doc.index("| カートリッジ |")


def test_render_escapes_pipes_and_newlines(repo):
    g = shop_glossary()
    g["terms"][0]["meaning"] = "a|b\nc"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    assert "| 注文 | — | a\\|b c |" in (repo / DEFAULT_DOCUMENT).read_text()


# --- 用語チェック（check） --------------------------------------------------------

TERMS_DOC = "# 要求\n\n## 用語\n\n| 用語 | 意味 |\n| --- | --- |\n| 注文 | 買う品 |\n"


def test_unregistered_term_fails_and_passes_after_adding(repo):
    write(repo, "issues/a.md", TERMS_DOC)
    commit(repo)
    write(repo, "issues/a.md", TERMS_DOC + "| 在庫 | 置いてある品 |\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1
    assert out["items"] == [{"rule": "unregistered", "path": "issues/a.md", "line": 8, "term": "在庫", "detail": out["items"][0]["detail"]}]
    assert "ERROR: issues/a.md:8: unregistered: 在庫" in err
    g = shop_glossary()
    g["terms"].append({"term": "在庫", "context": "ordering", "meaning": "置いてある品"})
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    assert run(repo, "check", "--diff", "develop")[0] == 0


def test_check_file_reads_whole_file_for_both_rules(repo):
    draft = repo / "draft.md"
    draft.write_text(TERMS_DOC + "| 在庫 | 置いてある品 |\n\nオーダーを受ける。\n", encoding="utf-8")
    code, out, _ = run(repo, "check", "--file", str(draft))
    assert code == 1
    assert {(it["rule"], it["term"]) for it in out["items"]} == {("unregistered", "在庫"), ("deprecated", "オーダー")}


def test_diff_sees_only_added_lines(repo):
    write(repo, "issues/a.md", "オーダーを受ける。\n消す行のオーダー。\n")
    commit(repo, "base")
    git(repo, "branch", "-f", "develop")
    write(repo, "issues/a.md", "オーダーを受ける。\n足した行のオーダー。\n")
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1
    assert [(it["line"], it["term"]) for it in out["items"]] == [(2, "オーダー")]


def test_diff_ignores_lines_added_on_base_after_branching(repo):
    git(repo, "checkout", "-q", "develop")
    write(repo, "issues/other.md", "オーダーを受ける。\n")
    commit(repo, "other")
    git(repo, "checkout", "-q", "feature/x")
    write(repo, "issues/mine.md", "注文を受ける。\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0


def test_code_blocks_and_inline_code_are_not_matched(repo):
    write(
        repo,
        "issues/a.md",
        "`オーダー` は廃止した。\n\n```text\nオーダー\n```\n\n## 用語\n\n| 用語 | 意味 |\n| --- | --- |\n| 注文 | m |\n",
    )
    assert run(repo, "check", "--diff", "develop")[0] == 0


def test_blockquote_lines_are_not_matched(repo):
    """引用（`>` で始まる行）は他者の言葉の写しなので、廃止した語があっても当てない。引用の外は当てる。"""
    write(repo, "issues/a.md", "## 依頼（原文）\n\n> オーダーを速くする\n>\n> * オーダーの一覧\n\n本文。\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0
    write(repo, "issues/a.md", "## 依頼（原文）\n\n> オーダーを速くする\n\nオーダーは廃止した。\n")
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and [i["line"] for i in out["items"]] == [5]


def test_deprecated_inside_live_term_is_not_matched(repo):
    g = shop_glossary()
    g["terms"][1]["deprecated"] = ["カート"]
    g["terms"][2]["deprecated"] = []
    g["terms"].insert(0, {"term": "カートン", "context": "shipping", "meaning": "箱"})
    write_json(repo, DEFAULT_SOURCE, g)
    code, _, err = run(repo, "render")
    assert code == 0, err
    write(repo, "issues/a.md", "カートンに入れる。\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0
    write(repo, "issues/a.md", "カートに入れる。\n")
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and out["items"][0]["term"] == "カート"


def test_deprecated_live_in_other_context_is_not_matched(repo):
    g = shop_glossary()
    g["terms"].append({"term": "オーダー", "context": "shipping", "meaning": "配送の依頼"})
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    write(repo, "issues/a.md", "オーダーを受ける。\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0


def test_ascii_words_match_on_word_boundary(repo):
    write(repo, "issues/a.md", "a cartridge here\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0
    write(repo, "issues/a.md", "a cart here\n")
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and out["items"][0]["term"] == "cart"


def test_files_outside_check_paths_and_other_tables_are_not_matched(repo):
    write(repo, "docs/free.md", "オーダーを受ける。\n")
    write(repo, "issues/a.md", "## 登場する品\n\n| 用語 | 意味 |\n| --- | --- |\n| 在庫 | m |\n")
    assert run(repo, "check", "--diff", "develop")[0] == 0


def test_term_sections_come_from_declaration(repo):
    write_json(repo, ".ndf/glossary.json", declaration(term_sections=["Glossary"]))
    write(
        repo,
        "issues/a.md",
        "## Glossary\n\n| Term | Meaning |\n| --- | --- |\n| stock | m |\n\n## 用語\n\n| 用語 | 意味 |\n| --- | --- |\n| 在庫 | m |\n",
    )
    code, out, _ = run(repo, "check", "--diff", "develop")
    assert code == 1 and [it["term"] for it in out["items"]] == ["stock"]


def test_structure_rules_skip_words(repo):
    write(repo, "issues/a.md", "オーダーを受ける。\n")
    assert run(repo, "check", "--diff", "develop", "--rules", "structure")[0] == 0


def test_one_letter_deprecated_word_is_schema(repo):
    g = shop_glossary()
    g["terms"][0]["deprecated"] = ["品"]
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"]


def test_pending_source_pointing_to_a_missing_design_is_schema(repo):
    g = shop_glossary()
    g["terms"][0]["pending_source"] = "issues/gone-design.md"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"] and "gone-design.md" in out["items"][0]["detail"]
    write(repo, "issues/gone-design.md", "# 設計\n")
    assert run(repo, "check", "--rules", "structure")[0] == 0


@pytest.mark.parametrize("pending", ["/etc/hosts", "../outside-design.md", "./issues/x-design.md"])
def test_pending_source_outside_or_unnormalized_is_schema(repo, pending):
    """spec-finalize が照合できない書き方（絶対パス・..・./ 付き）は、ファイルがあっても落とす。"""
    write(repo, "issues/x-design.md", "# 設計\n")
    g = shop_glossary()
    g["terms"][0]["pending_source"] = pending
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"]


def test_missing_required_field_is_schema(repo):
    g = shop_glossary()
    del g["terms"][0]["meaning"]
    write_json(repo, DEFAULT_SOURCE, g)
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and "schema" in rules_of(out)


# --- 識別子（code / deprecated_code） ----------------------------------------------


def import_glossary():
    sys.path.insert(0, str(SCRIPTS))
    import glossary

    return glossary


def test_code_forms_derive_every_spelling_from_snake_case():
    assert import_glossary().code_forms("shopping_cart") == ["shopping_cart", "ShoppingCart", "SHOPPING_CART", "shopping-cart"]
    assert import_glossary().code_forms("plan") == ["plan", "Plan", "PLAN"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("code", "ShoppingCart"),
        ("code", "shopping-cart"),
        ("code", "shopping__cart"),
        ("code", "_cart"),
        ("code", "2cart"),
        ("code", ""),
        ("code", 1),
        ("deprecated_code", "cart"),
        ("deprecated_code", ["Cart"]),
        ("deprecated_code", [1]),
    ],
)
def test_malformed_code_is_schema(repo, field, value):
    g = shop_glossary()
    g["terms"][0][field] = value
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"], out


def test_same_code_in_one_context_is_schema_and_other_context_is_allowed(repo):
    g = shop_glossary()
    g["terms"][0]["code"] = "order"
    g["terms"][1]["code"] = "order"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"] and out["items"][0]["term"] == "order"
    g["terms"][1]["code"] = "cartridge"
    g["terms"][2]["code"] = "order"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    assert run(repo, "check", "--rules", "structure")[0] == 0


def test_deprecated_code_overlapping_live_code_in_same_context_is_schema(repo):
    g = shop_glossary()
    g["terms"][0]["code"] = "order"
    g["terms"][1]["code"] = "cartridge"
    g["terms"][1]["deprecated_code"] = ["order"]
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert code == 1 and rules_of(out) == ["schema"]


def test_render_has_code_columns(repo):
    g = shop_glossary()
    g["terms"][0].update({"code": "order", "deprecated_code": ["purchase_order", "po_item"]})
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    doc = (repo / DEFAULT_DOCUMENT).read_text()
    assert "| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |" in doc
    assert "| 注文 | `order` | 顧客が買うと決めた品の組 | オーダー | `purchase_order`、`po_item` | `docs/ordering.md` |" in doc
    assert "| カートリッジ | — | 交換できる部品 | — | — | — |" in doc


def coded_repo(repo):
    g = shop_glossary()
    g["terms"][0].update({"code": "order", "deprecated_code": ["purchase_order", "cart"]})
    g["terms"][1]["code"] = "shopping_cart"
    write_json(repo, DEFAULT_SOURCE, g)
    run(repo, "render")
    commit(repo, "code")
    git(repo, "branch", "-f", "develop")


def test_diff_finds_deprecated_code_in_every_spelling_on_added_code_lines(repo):
    coded_repo(repo)
    write(repo, "src/a.py", "purchase_order = 1\n")
    commit(repo, "old")
    git(repo, "branch", "-f", "develop")
    write(repo, "src/a.py", "purchase_order = 1\nclass PurchaseOrder: pass\n")
    write(repo, "src/b.sh", "PURCHASE_ORDER=1\ncmd --purchase-order\n")
    write(repo, "src/c.js", "const x = cart;\n")
    write(repo, "src/d.ts", "let y: Cart;\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1
    assert sorted((it["rule"], it["path"], it["line"], it["term"]) for it in out["items"]) == [
        ("deprecated_code", "src/a.py", 2, "PurchaseOrder"),
        ("deprecated_code", "src/b.sh", 1, "PURCHASE_ORDER"),
        ("deprecated_code", "src/b.sh", 2, "purchase-order"),
        ("deprecated_code", "src/c.js", 1, "cart"),
        ("deprecated_code", "src/d.ts", 1, "Cart"),
    ]
    assert "PurchaseOrder" not in out["items"][0]["detail"] and "Order（注文）" in out["items"][0]["detail"]
    assert "ERROR: src/a.py:2: deprecated_code: PurchaseOrder" in err


def test_deprecated_code_inside_live_code_or_other_files_is_not_matched(repo):
    coded_repo(repo)
    write(repo, "src/a.py", "shopping_cart = ShoppingCart(SHOPPING_CART)\nrun('--shopping-cart')\nsub_cart = 1\n")
    write(repo, "src/notes.txt", "cart\n")
    write(repo, "issues/a.md", "`cart` と書く。\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 0, (out, err)


def test_deprecated_code_in_comments_is_not_matched_but_in_strings_is(repo):
    coded_repo(repo)
    write(repo, "src/a.py", "# cart is the old name\nx = 1  # Cart\ny = '#' + cart\n")
    write(repo, "src/b.sh", "#!/bin/sh\n# CART\necho ${#cart} # cart\n")
    write(repo, "src/c.js", "// cart\nconst u = 'http://x'; /* Cart\n cart */ cart;\n")
    write(repo, "src/d.ts", 'let s = "// \\" " + Cart; // cart\n')
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1, (out, err)
    assert sorted((it["path"], it["line"], it["term"]) for it in out["items"]) == [
        ("src/a.py", 3, "cart"),
        ("src/b.sh", 3, "cart"),
        ("src/c.js", 3, "cart"),
        ("src/d.ts", 1, "Cart"),
    ]


def test_comment_marks_inside_multiline_strings_are_not_comments(repo):
    coded_repo(repo)
    write(repo, "src/a.py", 's = """\n# cart\n"""  # cart\n')
    write(repo, "src/c.js", "const t = `\n// Cart ${x} src/*\n`; cart; // cart\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1, (out, err)
    assert sorted((it["path"], it["line"], it["term"]) for it in out["items"]) == [
        ("src/a.py", 2, "cart"),
        ("src/c.js", 2, "Cart"),
        ("src/c.js", 3, "cart"),
    ]


def test_escaped_newline_inside_plain_strings_carries_the_quote(repo):
    coded_repo(repo)
    write(repo, "src/a.py", "s = 'a\\\n# cart' # cart\nt = '\\\\' # cart\n")
    write(repo, "src/c.js", 'const u = "a\\\n// Cart"; // cart\n')
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1, (out, err)
    assert sorted((it["path"], it["line"], it["term"]) for it in out["items"]) == [
        ("src/a.py", 2, "cart"),
        ("src/c.js", 2, "Cart"),
    ]


def test_shell_backslash_outside_quotes_and_inside_single_quotes(repo):
    coded_repo(repo)
    write(repo, "src/b.sh", "echo 'it'\\''s' # cart\necho \\' # cart\ntr -d '\\' # cart\necho \"a\n# cart\" # cart\n")
    code, out, err = run(repo, "check", "--diff", "develop")
    assert code == 1, (out, err)
    assert [(it["path"], it["line"], it["term"]) for it in out["items"]] == [("src/b.sh", 5, "cart")]


def test_check_file_finds_deprecated_code_in_a_code_file(repo):
    coded_repo(repo)
    p = write(repo, "tool.py", "Cart()\n")
    code, out, _ = run(repo, "check", "--file", str(p))
    assert code == 1 and [(it["rule"], it["term"]) for it in out["items"]] == [("deprecated_code", "Cart")]


def test_structure_rules_skip_deprecated_code(repo):
    coded_repo(repo)
    write(repo, "src/a.py", "cart = 1\n")
    assert run(repo, "check", "--diff", "develop", "--rules", "structure")[0] == 0


def test_no_declaration_check_and_diff_are_ok(bare):
    code, out, _ = run(bare, "check", "--diff", "develop")
    assert code == 0 and "宣言が無い" in out["summary"]
    code, out, _ = run(bare, "diff", "--base", "develop")
    assert code == 0 and out["items"] == []


# --- 差分（diff） -------------------------------------------------------------------


def test_diff_reports_four_changes(repo):
    g = shop_glossary()
    g["terms"][0]["meaning"] = "新しい意味"
    g["terms"][2]["deprecated"] = ["cart", "パッケージ"]
    del g["terms"][1]
    g["terms"].append({"term": "返品", "context": "ordering", "meaning": "戻すこと"})
    write_json(repo, DEFAULT_SOURCE, g)
    code, out, _ = run(repo, "diff", "--base", "develop")
    assert code == 0
    got = {(it["change"], it["context"], it["term"]) for it in out["items"]}
    assert got == {
        ("meaning_changed", "ordering", "注文"),
        ("removed", "ordering", "カートリッジ"),
        ("added", "ordering", "返品"),
        ("deprecated", "shipping", "パッケージ"),
    }
    changed = next(it for it in out["items"] if it["change"] == "meaning_changed")
    assert changed["before"] == "顧客が買うと決めた品の組" and changed["after"] == "新しい意味"


def test_diff_between_two_refs_with_missing_side(bare):
    write_json(bare, ".ndf/glossary.json", declaration())
    write_json(bare, DEFAULT_SOURCE, shop_glossary([{"term": "注文", "context": "ordering", "meaning": "m"}]))
    run(bare, "render")
    commit(bare)
    code, out, _ = run(bare, "diff", "--base", "HEAD~1", "--head", "HEAD")
    assert code == 0 and [(it["change"], it["term"]) for it in out["items"]] == [("added", "注文")]


# --- 候補（candidates） ------------------------------------------------------------


def test_candidates_collect_three_kinds(repo):
    write(
        repo,
        "docs/spec.md",
        "## 用語\n\n| 用語 | 意味 |\n| --- | --- |\n| 在庫 | 置いてある品 |\n| 注文 | 既にある |\n\n"
        "**引当**を行う。**引当**の後で**引当**を戻す。\n",
    )
    write(repo, "src/model.ts", "export class Invoice {}\ninterface Payment {}\ntype Refund = {};\n")
    commit(repo)
    code, out, _ = run(repo, "candidates")
    assert code == 0
    got = {(it["term"], it["kind"]) for it in out["items"]}
    assert {("在庫", "table"), ("引当", "bold"), ("Invoice", "type"), ("Payment", "type"), ("Refund", "type")} <= got
    assert not any(it["term"] == "注文" for it in out["items"])
    bold = next(it for it in out["items"] if it["term"] == "引当")
    assert bold["count"] == 3 and bold["first"] == "docs/spec.md:8"


def test_candidates_empty_repository_is_scratch(bare):
    code, out, _ = run(bare, "candidates")
    assert code == 0 and out["items"] == [] and "スクラッチ" in out["summary"]


# --- パスの境界 ---------------------------------------------------------------------


def _outside_cases(tmp_path, root):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (root / "link").symlink_to(outside, target_is_directory=True)
    return [str(outside / "g.json"), "../g.json", "link/g.json"]


@pytest.mark.parametrize("field", ["source", "document", "paths"])
@pytest.mark.parametrize("idx", [0, 1, 2])
def test_declaration_paths_outside_root_stop_every_command(bare, tmp_path, field, idx):
    bad = _outside_cases(tmp_path, bare)[idx]
    d = declaration()
    if field == "paths":
        d["check"]["paths"] = [bad]
    else:
        d[field] = bad
    write_json(bare, ".ndf/glossary.json", d)
    before, outside_before = snapshot(bare), snapshot(tmp_path / "outside")
    for args in (
        ["gate", "--mode", "standard"],
        ["init"],
        ["candidates"],
        ["render"],
        ["check", "--diff", "develop"],
        ["diff", "--base", "develop"],
        ["merge"],
    ):
        code, _, err = run(bare, *args)
        assert code == 2, (args, err)
    assert snapshot(bare) == before
    assert snapshot(tmp_path / "outside") == outside_before


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_init_places_outside_root_are_2(bare, tmp_path, idx):
    bad = _outside_cases(tmp_path, bare)[idx]
    before = snapshot(bare)
    assert run(bare, "init", "--source", bad)[0] == 2
    assert run(bare, "init", "--document", bad)[0] == 2
    assert snapshot(bare) == before
    assert list((tmp_path / "outside").iterdir()) == []


def test_command_lines_are_step_results(repo):
    for args in (["gate", "--mode", "standard"], ["render", "--check"], ["check"], ["diff", "--base", "develop"]):
        code, out, _ = run_cli(repo, *args)
        assert code == 0 and out["tool"] == "glossary" and out["status"] == "ok"
    assert os.access(SCRIPT, os.X_OK)


# --- merge: 取り込みの途中の用語集の衝突（#1913） ---------------------------------------

ADDED_OURS = {"term": "返品", "context": "ordering", "meaning": "届いた品を戻すこと"}
ADDED_THEIRS = {"term": "置き配", "context": "ordering", "meaning": "玄関先へ置く届け方"}


def merge_repo(bare, source=DEFAULT_SOURCE, document=DEFAULT_DOCUMENT, base_terms=None):
    """祖先の用語集を develop に置き、feature/x へ移ったリポジトリ。"""
    config = {"version": 1, "format": "json", "source": source, "document": document, "check": {"paths": ["issues/*.md"]}}
    write_json(bare, ".ndf/glossary.json", config)
    write_json(bare, source, shop_glossary(base_terms))
    assert run(bare, "render")[0] == 0
    commit(bare, "glossary")
    git(bare, "checkout", "-q", "-b", "feature/x")
    return bare


def side(root, branch, edit, source=DEFAULT_SOURCE, extra=None):
    """branch へ移り、用語集の terms を edit で変えて文書を作り直し、コミットする。"""
    git(root, "checkout", "-q", branch)
    g = json.loads((root / source).read_text(encoding="utf-8"))
    g["terms"] = edit(g["terms"])
    write_json(root, source, g)
    assert run(root, "render")[0] == 0
    if extra:
        write(root, "notes.md", extra)
    commit(root, branch)


def conflict(root, ours, theirs, source=DEFAULT_SOURCE, extra=False):
    """feature/x（自分）を ours、develop（相手）を theirs で変え、feature/x へ develop を取り込んで止める。"""
    side(root, "feature/x", ours, source, "ours\n" if extra else None)
    side(root, "develop", theirs, source, "theirs\n" if extra else None)
    git(root, "checkout", "-q", "feature/x")
    p = subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "develop"], capture_output=True, text=True)
    assert p.returncode != 0, p.stdout
    return unmerged(root)


def unmerged(root):
    return git(root, "diff", "--name-only", "--diff-filter=U").split()


def merging(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True).returncode == 0


def term_keys(root, source=DEFAULT_SOURCE):
    return [(t["context"], t["term"]) for t in json.loads((root / source).read_text(encoding="utf-8"))["terms"]]


def rendered(root, source=DEFAULT_SOURCE):
    glossary = import_glossary()
    return glossary.render_text(json.loads((root / source).read_text(encoding="utf-8")), source)


@pytest.mark.parametrize("source,document", [(DEFAULT_SOURCE, DEFAULT_DOCUMENT), ("words/terms.json", "words/terms.md")])
def test_merge_keeps_terms_added_on_both_sides_and_commits(bare, source, document):
    root = merge_repo(bare, source, document)
    assert sorted(conflict(root, lambda ts: ts + [ADDED_OURS], lambda ts: ts + [ADDED_THEIRS], source)) == sorted([source, document])
    code, out, err = run(root, "merge")
    assert code == 0, err
    keys = term_keys(root, source)
    assert keys.count(("ordering", "返品")) == 1 and keys.count(("ordering", "置き配")) == 1
    assert (root / document).read_text(encoding="utf-8") == rendered(root, source)
    assert unmerged(root) == [] and not merging(root)
    assert git(root, "status", "--porcelain") == ""
    assert out["items"] == [{"name": source, "result": "merged"}, {"name": document, "result": "rendered"}]
    assert out["metrics"]["added"] == 1


def test_merge_source_has_no_hardcoded_glossary_place():
    for p in (SCRIPTS / "lib" / "glossary_merge.py", SCRIPTS / "glossary.py"):
        text = p.read_text(encoding="utf-8")
        body = text[text.find("def cmd_merge") :] if p.name == "glossary.py" else text
        assert "docs/glossary" not in body.split("def build_parser")[0]


def _changed(ts):
    return [dict(t, meaning="自分が変えた意味") if t["term"] == "注文" else t for t in ts]


@pytest.mark.parametrize(
    "ours,theirs,kind",
    [
        (_changed, lambda ts: [dict(t, meaning="相手が変えた意味") if t["term"] == "注文" else t for t in ts], "both_changed"),
        (_changed, lambda ts: [t for t in ts if t["term"] != "注文"] + [ADDED_THEIRS], "deleted_and_changed"),
        (lambda ts: ts + [ADDED_OURS], lambda ts: ts + [dict(ADDED_OURS, meaning="別の意味")], "both_added"),
    ],
)
def test_merge_stops_on_term_conflicts_without_writing(repo, ours, theirs, kind):
    conflict(repo, ours, theirs)
    before, index_before = snapshot(repo), git(repo, "ls-files", "-u")
    code, out, err = run(repo, "merge")
    assert code == 1 and out["status"] == "stopped"
    assert snapshot(repo) == before and git(repo, "ls-files", "-u") == index_before and merging(repo)
    assert {"kind": kind, "context": "ordering", "term": "返品" if kind == "both_added" else "注文"} in out["items"]
    assert f"ERROR: {kind}: ordering:" in err


def test_merge_takes_one_side_rewrite_and_other_side_addition(bare):
    pending = {"term": "返品", "context": "ordering", "meaning": "届いた品を戻すこと", "pending_source": "issues/x-design.md"}
    write(bare, "issues/x-design.md", "# 設計\n")
    root = merge_repo(bare, base_terms=shop_glossary()["terms"] + [pending])

    def confirm(ts):
        return [
            {k: v for k, v in t.items() if k != "pending_source"} | {"source": "docs/ordering.md"} if t["term"] == "返品" else t for t in ts
        ]

    conflict(root, lambda ts: ts + [ADDED_THEIRS], confirm)
    code, _, err = run(root, "merge")
    assert code == 0, err
    terms = {(t["context"], t["term"]): t for t in json.loads((root / DEFAULT_SOURCE).read_text(encoding="utf-8"))["terms"]}
    assert ("ordering", "置き配") in terms
    assert terms[("ordering", "返品")].get("source") == "docs/ordering.md" and "pending_source" not in terms[("ordering", "返品")]


def test_merge_keeps_a_term_added_identically_on_both_sides_once(repo):
    conflict(repo, lambda ts: ts + [ADDED_OURS, ADDED_THEIRS], lambda ts: ts + [ADDED_THEIRS])
    code, _, err = run(repo, "merge")
    assert code == 0, err
    assert term_keys(repo).count(("ordering", "置き配")) == 1
    code, out, _ = run(repo, "check", "--rules", "structure")
    assert "duplicate" not in [it["rule"] for it in out["items"]]


def test_merge_with_other_conflicts_resolves_glossary_but_does_not_commit(repo):
    conflict(repo, lambda ts: ts + [ADDED_OURS], lambda ts: ts + [ADDED_THEIRS], extra=True)
    code, out, err = run(repo, "merge")
    assert code == 1 and out["status"] == "stopped"
    assert out["items"] == [{"path": "notes.md", "result": "unresolved"}]
    assert unmerged(repo) == ["notes.md"] and merging(repo)
    assert "ERROR: unresolved: notes.md" in err


def test_merge_outside_a_merge_or_without_glossary_conflict_writes_nothing(repo):
    before = snapshot(repo)
    code, out, _ = run(repo, "merge")
    assert code == 1 and "MERGE_HEAD" in out["summary"] and snapshot(repo) == before
    side(repo, "feature/x", lambda ts: ts, extra="ours\n")
    side(repo, "develop", lambda ts: ts, extra="theirs\n")
    git(repo, "checkout", "-q", "feature/x")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "develop"], capture_output=True)
    before = snapshot(repo)
    code, out, _ = run(repo, "merge")
    assert code == 1 and "衝突していない" in out["summary"] and snapshot(repo) == before
    assert out["items"] == [{"path": "notes.md", "result": "unresolved"}]


def test_merge_resolves_a_document_only_conflict_by_rendering(repo):
    write(repo, DEFAULT_DOCUMENT, "自分が手で書いた\n")
    commit(repo, "ours doc")
    git(repo, "checkout", "-q", "develop")
    write(repo, DEFAULT_DOCUMENT, "相手が手で書いた\n")
    commit(repo, "theirs doc")
    git(repo, "checkout", "-q", "feature/x")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "develop"], capture_output=True)
    assert unmerged(repo) == [DEFAULT_DOCUMENT]
    code, out, err = run(repo, "merge")
    assert code == 0, err
    assert out["items"] == [{"name": DEFAULT_DOCUMENT, "result": "rendered"}]
    assert (repo / DEFAULT_DOCUMENT).read_text(encoding="utf-8") == rendered(repo) and not merging(repo)


def test_merge_stops_when_one_side_deleted_the_glossary(repo):
    side(repo, "feature/x", lambda ts: ts + [ADDED_OURS])
    git(repo, "checkout", "-q", "develop")
    git(repo, "rm", "-q", DEFAULT_SOURCE)
    commit(repo, "rm")
    git(repo, "checkout", "-q", "feature/x")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "develop"], capture_output=True)
    before = snapshot(repo)
    code, out, _ = run(repo, "merge")
    assert code == 1 and out["items"] == [{"kind": "file_deleted", "side": "theirs", "path": DEFAULT_SOURCE}]
    assert snapshot(repo) == before


# --- glossary_merge（純粋な処理） --------------------------------------------------------


def import_merge():
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import glossary_merge

    return glossary_merge


def _g(*terms, contexts=None):
    return {"version": 1, "contexts": contexts if contexts is not None else [{"id": "ordering"}], "terms": list(terms)}


def _t(term, meaning="m"):
    return {"term": term, "context": "ordering", "meaning": meaning}


def test_merge_is_deterministic_and_inserts_theirs_after_its_predecessor():
    gm = import_merge()
    base = _g(_t("a"), _t("z"))
    ours = _g(_t("a"), _t("o1"), _t("z"))
    theirs = _g(_t("t0"), _t("a"), _t("t1"), _t("t2"), _t("z"), _t("t3"))
    first = gm.merge_glossaries(base, ours, theirs)
    again = gm.merge_glossaries(base, ours, theirs)
    assert json.dumps(first.glossary, ensure_ascii=False) == json.dumps(again.glossary, ensure_ascii=False)
    assert [t["term"] for t in first.glossary["terms"]] == ["t0", "a", "t1", "t2", "o1", "z", "t3"]
    assert first.counts == {"added": 4, "changed": 0, "removed": 0}


def test_merge_takes_one_side_changes_and_removals():
    gm = import_merge()
    base = _g(_t("a"), _t("b"), _t("c"))
    m = gm.merge_glossaries(base, _g(_t("a", "new"), _t("b"), _t("c")), _g(_t("a"), _t("c")))
    assert m.conflicts == [] and [(t["term"], t["meaning"]) for t in m.glossary["terms"]] == [("a", "new"), ("c", "m")]
    assert m.counts["removed"] == 1


def test_merge_reports_duplicates_and_context_and_top_level_conflicts():
    gm = import_merge()
    dup = gm.merge_glossaries(_g(), _g(_t("a"), _t("a")), _g())
    assert dup.glossary is None and dup.duplicates == [{"kind": "duplicate", "side": "ours", "context": "ordering", "term": "a"}]
    ctx = gm.merge_glossaries(_g(), _g(contexts=[{"id": "ordering", "name": "x"}]), _g(contexts=[{"id": "ordering", "name": "y"}]))
    assert ctx.conflicts == [{"kind": "both_changed", "context": "ordering", "term": None}]
    top = gm.merge_glossaries(_g(), dict(_g(), version=2), dict(_g(), version=3))
    assert top.conflicts == [{"kind": "key_changed", "context": None, "term": None, "key": "version"}]


def test_merge_treats_a_missing_base_as_empty():
    gm = import_merge()
    m = gm.merge_glossaries({"version": 1}, _g(_t("a")), _g(_t("b")))
    assert [t["term"] for t in m.glossary["terms"]] == ["b", "a"] and m.glossary["contexts"] == [{"id": "ordering"}]
