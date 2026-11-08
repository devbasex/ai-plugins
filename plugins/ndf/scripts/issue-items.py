#!/usr/bin/env python3
"""Issue の記載項目の定義に照らした検査（#2737）。定義は `lib/issue_items.py` が読む。

    python3 issue-items.py audit     [--repo <所有者>/<リポジトリ>]   # 起票の後の検査（open の Issue の欠けの一覧）
    python3 issue-items.py templates [--root <リポジトリ>]            # 一致の検査（定義とテンプレートの食い違い）

結果は `lib/step_result.py` の形の 1 行の JSON（`tool` は `issue-items`）。終了コード:

    0   欠け・食い違いが 0 件。課題の置き場が GitHub でないときの audit（検査しなかった）
    1   欠けのある open の Issue・食い違いが 1 件以上
    2   GitHub・テンプレート・定義を読めない、引数の誤り（「0 件」と読ませない）

`audit` は open の Issue だけを読み、本文とラベルを `judge_body(本文, ラベル, {})` で判定する（起票した者が AI かは GitHub から
決められないので、必須の項目・種類・順序だけを見る）。課題の置き場は `.ndf/project.json` の `issues.primary`（無ければ `github`）。

`templates` は `<root>/.github/ISSUE_TEMPLATE/*.yml`（`config.yml` を除く）のフォームと、NDF が持つ `out-of-scope` の本文の
骨格・`requirements-design` の仕様のテンプレート（このスクリプトと同じプラグインの `skills/` の下。` ```markdown ` のブロックの
見出し）を読む。フォームは `labels` の種類のラベルで
受け持つ種類を決め、その種類の必須の項目を必須欄に持つかを見る。骨格と仕様のテンプレートは 4 種類を受け持ち、どれかの種類で
必須の項目をすべて見出しに持つかを見る。どのテンプレートも、項目名に当たらない欄・見出しを持たない。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md", "yamlio")
import issue_items  # noqa: E402
import md  # noqa: E402
import proc  # noqa: E402
import project_decl  # noqa: E402
import run_core  # noqa: E402
from step_result import EXIT_OK, EXIT_UNREADABLE, EXIT_VIOLATION, emit, result  # noqa: E402

TOOL = "issue-items"
AUDIT_LIMIT = 2000  # open の Issue を読む上限
ERROR_EXCERPT = 300
FORMS_DIR = Path(".github") / "ISSUE_TEMPLATE"
FORM_SKIP = ("config.yml",)
SKILLS = Path(__file__).resolve().parents[1] / "skills"
MARKDOWN_TEMPLATES = (Path("out-of-scope/SKILL.md"), Path("requirements-design/references/spec-template.md"))


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> "NoReturn":  # noqa: F821
        emit(result(TOOL, "stopped", f"引数の誤り: {message}"), EXIT_UNREADABLE)


def _stop_items(summary: str, code: int = EXIT_UNREADABLE, items=None, metrics=None) -> "NoReturn":  # noqa: F821
    emit(result(TOOL, "stopped", summary, items, metrics), code)


def _items_root(given: str | None) -> Path:
    if given:
        return Path(given).resolve()
    return Path(proc.git_out(Path.cwd(), "rev-parse", "--show-toplevel") or ".").resolve()


def _definition(root: Path) -> issue_items.Definition:
    try:
        return issue_items.load_definition(root)
    except issue_items.DefinitionError as exc:
        _stop_items(f"記載項目の定義を読めない: {exc}")


# --- audit -------------------------------------------------------------------------------


def store_of(root: Path) -> str:
    issues = project_decl.read_project_decl(root).get("issues")
    primary = issues.get("primary") if isinstance(issues, dict) else None
    return primary if isinstance(primary, str) and primary else "github"


def audit_rows(rows: list[dict], definition: issue_items.Definition) -> list[dict]:
    """open の Issue の並びから、欠けのある Issue ごとの項目。"""
    out = []
    for row in rows:
        labels = [lab.get("name") for lab in row.get("labels") or [] if isinstance(lab, dict)]
        _kind, gaps = issue_items.judge_body(row.get("body") or "", labels, {}, definition)
        if gaps:
            out.append({"kind": "issue", "name": f"#{row.get('number')}", "result": "missing", "missing": [g.heading for g in gaps]})
    return out


def cmd_audit(a) -> None:
    root = _items_root(None)
    store = store_of(root)
    if store != "github":
        metrics = {"checked": None, "missing": None, "store": store}
        emit(result(TOOL, "ok", f"検査しなかった: 課題の置き場は {store}", [], metrics), EXIT_OK)
    definition = _definition(root)
    import gh_rest  # GitHub を読むときだけ読み込む

    try:
        got = gh_rest.issue_list(a.repo, "number,title,body,labels", "open", None, AUDIT_LIMIT)
    except ValueError as exc:  # リポジトリを決められない
        _stop_items(f"GitHub を読めなかった: {exc}")
    if not got.ok or not isinstance(got.value, list):
        _stop_items(f"GitHub を読めなかった: {str(got.error or '一覧が並びでない')[:ERROR_EXCERPT]}")
    items = audit_rows(got.value, definition)
    metrics = {"checked": len(got.value), "missing": len(items), "store": store}
    if items:
        _stop_items(f"open の Issue {len(got.value)} 件のうち {len(items)} 件に欠けがある", EXIT_VIOLATION, items, metrics)
    emit(result(TOOL, "ok", f"open の Issue {len(got.value)} 件に欠けは無い", [], metrics), EXIT_OK)


# --- templates ---------------------------------------------------------------------------


def _mismatch(path: str, item: str, what: str) -> dict:
    return {"kind": "template", "name": path, "item": item, "result": what}


def _form_labels(form: dict) -> list[str]:
    labels = form.get("labels") or []
    if isinstance(labels, str):
        labels = labels.split(",")
    return [str(x).strip() for x in labels if str(x).strip()]


def form_mismatches(rel: str, form: dict, definition: issue_items.Definition) -> list[dict]:
    """フォーム 1 つの食い違い。受け持つ種類が 1 つに決まらなければ種類の食い違いだけを返す。"""
    kind = issue_items.kind_of(_form_labels(form), definition)
    if kind is None:
        return [_mismatch(rel, "種類", "kind")]
    out, fields = [], {}
    for el in form.get("body") or []:
        label = ((el or {}).get("attributes") or {}).get("label") if isinstance(el, dict) else None
        if not label:
            continue
        it = definition.item_for_heading(str(label))
        if it is None:
            out.append(_mismatch(rel, str(label), "unknown"))
            continue
        required = bool(((el.get("validations") or {}).get("required")))
        fields[it.id] = fields.get(it.id, False) or required
    for it in definition.required_sections(kind):
        if it.id not in fields:
            out.append(_mismatch(rel, it.name, "missing"))
        elif not fields[it.id]:
            out.append(_mismatch(rel, it.name, "not-required"))
    return out


def markdown_mismatches(rel: str, text: str, definition: issue_items.Definition) -> list[dict]:
    """骨格・仕様のテンプレートの食い違い。見出しは ` ```markdown ` のブロックの中の H2 と H3。"""
    blocks = [t.content for t in md.md_tokens(text) if t.type == "fence" and t.info.strip() == "markdown"]
    heads = [h.title for block in blocks for h in md.headings(block) if h.level in (2, 3)]
    out, seen = [], set()
    for h in heads:
        it = definition.item_for_heading(h)
        if it is None:
            out.append(_mismatch(rel, h, "unknown"))
        else:
            seen.add(it.id)
    needed = {it.id: it for kind in definition.kinds for it in definition.required_sections(kind)}
    out += [_mismatch(rel, it.name, "missing") for iid, it in needed.items() if iid not in seen]
    return out


def _load_form(path: Path, rel: str) -> dict:
    import yamlio

    try:
        form = yamlio.load_yaml(path.read_text(encoding="utf-8"), rel)
    except (OSError, UnicodeDecodeError, yamlio.YamlError) as exc:
        _stop_items(f"テンプレートを読めない: {rel}（{exc}）")
    if not isinstance(form, dict):
        _stop_items(f"テンプレートの最上位がオブジェクトでない: {rel}")
    return form


def cmd_templates(a) -> None:
    root = _items_root(a.root)
    definition = _definition(root)
    forms = sorted(p for p in (root / FORMS_DIR).glob("*.yml") if p.name not in FORM_SKIP)
    if not forms:
        _stop_items(f"フォームが無い: {root / FORMS_DIR}")
    items: list[dict] = []
    for p in forms:
        rel = str(p.relative_to(root))
        items += form_mismatches(rel, _load_form(p, rel), definition)
    for rel in MARKDOWN_TEMPLATES:
        path = SKILLS / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _stop_items(f"テンプレートを読めない: {path}（{exc}）")
        items += markdown_mismatches(f"skills/{rel}", text, definition)
    metrics = {"templates": len(forms) + len(MARKDOWN_TEMPLATES), "mismatches": len(items), "definition": str(definition.path)}
    if items:
        names = "・".join(f"{it['name']} の {it['item']}（{it['result']}）" for it in items[:10])
        _stop_items(f"記載項目の定義とテンプレートが {len(items)} か所で食い違う: {names}", EXIT_VIOLATION, items, metrics)
    emit(result(TOOL, "ok", f"テンプレート {metrics['templates']} 個が記載項目の定義と一致する", [], metrics), EXIT_OK)


# --- 入口 ---------------------------------------------------------------------------


def items_cli_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="issue-items.py", description="Issue の記載項目の定義に照らした検査")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("audit", help="open の Issue のうち必須の項目が欠けたものを並べる")
    p.add_argument("--repo")
    p.set_defaults(func=cmd_audit)
    p = sub.add_parser("templates", help="記載項目の定義とテンプレートの食い違いを並べる")
    p.add_argument("--root")
    p.set_defaults(func=cmd_templates)
    return ap


def main(argv=None) -> None:
    a = items_cli_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    run_core.main("scripts/issue-items.py", main)
