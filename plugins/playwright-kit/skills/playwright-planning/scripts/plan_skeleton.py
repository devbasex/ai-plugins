"""page role とデータ型から、テスト計画書の雛形を組み立てる。

雛形には次を並べる。判定（適用 / 不適用）と理由の欄は空のまま残し、LLM か人が埋める。

- `docs/checklists/checklist-<role>.md` の観点 ID（`### FM1: ...` の形の見出し）
- `docs/checklists/checklist-common.md` の観点 ID（表の `| C1.1 | ... |` の行）
- `docs/03-test-techniques.md` §11 の必須技法のうち、role とデータ型に当てはまる行

role は `--role` で渡すか、`classify_page_role.py` の出力を `--classification` で渡す。
後者では分類の上位 2 件の差が `--margin` 未満、または role が決まらないとき、
雛形を作らずに exit 1 で人へ渡す。

標準出力は常に 1 つの JSON。`--output` を付けると Markdown の雛形をそのパスへ書く。

終了コード:
    0  雛形を作った
    1  分類が決め切れない（人へ渡す）
    2  引数・入力ファイルの誤り

Usage:
    python scripts/plan_skeleton.py --role form --data-types 数値 ファイル --output plan.md
    python scripts/plan_skeleton.py --classification classifications.json --url https://example.com/contact
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
DEFAULT_MARGIN = 0.5

# classify_page_role.py の role 名 → チェックリストの名前。
# §11 の表は role を cart / checkout のように個別の名前で書くため、別名も持つ。
CHECKLIST_OF_ROLE: dict[str, str] = {
    "lp": "lp",
    "list": "list",
    "item": "item",
    "edit": "edit",
    "form": "form",
    "search": "search",
    "dashboard": "dashboard",
    "auth": "auth",
    "cart": "cart-checkout",
    "checkout": "cart-checkout",
    "cart-checkout": "cart-checkout",
    "modal": "modal-wizard",
    "wizard": "modal-wizard",
    "modal-wizard": "modal-wizard",
}

_HEADING_ID = re.compile(r"^###\s+(?P<id>[A-Z]+\d+):\s*(?P<title>.+?)\s*(?:`\[(?P<tags>[^\]]*)\]`)?\s*$")
_COMMON_ROW = re.compile(r"^\|\s*(?P<id>C\d+\.\d+)\s*\|\s*(?P<title>[^|]+?)\s*\|\s*(?P<tech>[^|]*?)\s*\|\s*(?P<oracle>[^|]*?)\s*\|")
_TECHNIQUE_ROW = re.compile(r"^\((?P<role>[^,]+),\s*(?P<data>[^)]+?)\)\s*→\s*(?P<tech>.+?)\s*$")


class UsageError(Exception):
    pass


def role_aliases(role: str) -> set[str]:
    """§11 の role 欄と照らす名前の集合（同じチェックリストを指す名前すべて）。"""
    checklist = CHECKLIST_OF_ROLE[role]
    return {r for r, c in CHECKLIST_OF_ROLE.items() if c == checklist}


def parse_checklist(path: Path) -> list[dict[str, str]]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _HEADING_ID.match(line)
        if m:
            items.append({
                "id": m["id"], "title": m["title"], "tags": m["tags"] or "",
                "source": path.name, "judgement": "", "reason": "",
            })
    return items


def parse_common(path: Path) -> list[dict[str, str]]:
    """共通の観点のうち、技法と oracle の列を持つ表の行を読む。

    §8（runner が自動で FAIL にする）と §9（ログだけ取る）の表はこの 2 列を持たず、
    適用を判定する観点ではないため、正規表現に合わず雛形に入らない。
    """
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _COMMON_ROW.match(line)
        if m:
            items.append({
                "id": m["id"], "title": m["title"],
                "tags": " / ".join(t for t in (m["tech"], m["oracle"]) if t),
                "source": path.name, "judgement": "", "reason": "",
            })
    return items


def parse_technique_rules(path: Path) -> list[dict[str, str]]:
    """03-test-techniques.md §11 のコードブロックから (role, data) → 技法 の行を読む。"""
    text = path.read_text(encoding="utf-8")
    start = text.find("## 11.")
    if start < 0:
        raise UsageError(f"{path.name} に §11 が見つからない")
    section = text[start:]
    end = section.find("\n## ", 1)
    if end > 0:
        section = section[:end]
    rules = []
    for line in section.splitlines():
        m = _TECHNIQUE_ROW.match(line.strip())
        if m and m["role"].strip() != "role":
            rules.append({
                "role": m["role"].strip(), "data_type": m["data"].strip(),
                "techniques": m["tech"],
            })
    return rules


def select_rules(rules: list[dict[str, str]], role: str, data_types: list[str]) -> list[dict[str, str]]:
    aliases = role_aliases(role)
    chosen = []
    for r in rules:
        role_ok = r["role"] == "*" or r["role"] in aliases
        data_ok = r["data_type"] == "*" or r["data_type"] in data_types
        # (*, *) の行は無い。役割だけ・データ型だけの行はどちらかが * で表す
        if role_ok and data_ok and not (r["role"] == "*" and r["data_type"] == "*"):
            chosen.append(r)
    return chosen


def known_data_types(rules: list[dict[str, str]]) -> list[str]:
    seen: list[str] = []
    for r in rules:
        if r["data_type"] != "*" and r["data_type"] not in seen:
            seen.append(r["data_type"])
    return seen


def load_classification(path: Path, url: str | None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsageError(f"分類の結果を読めない: {path} ({exc})") from exc
    entries = data if isinstance(data, list) else [data]
    if url:
        entries = [e for e in entries if e.get("url") == url]
        if not entries:
            raise UsageError(f"分類の結果に {url} が無い")
    if len(entries) != 1:
        raise UsageError(f"分類の結果が {len(entries)} 件ある。--url で 1 件に絞る")
    entry = entries[0]
    if "error" in entry:
        raise UsageError(f"分類に失敗した URL である: {entry['error']}")
    return entry


def judge_classification(entry: dict[str, Any], margin: float) -> dict[str, Any]:
    """上位 2 件の差を見て、role を決めてよいかを返す。"""
    primary = {"role": entry.get("primary_role", "unknown"),
               "score": float(entry.get("primary_score") or 0.0)}
    alternates = sorted(
        ({"role": a.get("role"), "score": float(a.get("score") or 0.0)}
         for a in entry.get("alternates", [])),
        key=lambda a: -a["score"],
    )
    candidates = [primary, *alternates]
    second = alternates[0]["score"] if alternates else 0.0
    gap = round(primary["score"] - second, 6)
    reason = None
    if primary["role"] == "unknown":
        reason = "primary_role が unknown（スコア 1.0 以上の候補が無い）"
    elif gap < margin:
        reason = f"上位 2 件の差 {gap} が margin {margin} 未満"
    elif primary["role"] not in CHECKLIST_OF_ROLE:
        reason = f"role {primary['role']} に対応するチェックリストが無い"
    return {"url": entry.get("url"), "role": primary["role"], "gap": gap,
            "margin": margin, "candidates": candidates, "reason": reason}


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# テスト計画書: {result['role']}",
        "",
        f"- page role: `{result['role']}`（`{Path(result['checklist']).name}`）",
        f"- データ型: {', '.join(result['data_types']) or '（指定なし）'}",
    ]
    if result.get("classification"):
        c = result["classification"]
        lines.append(f"- 分類: {c['url']}（上位 2 件の差 {c['gap']}）")
    lines += [
        "",
        "判定の欄には「適用」か「不適用」を書き、不適用には理由を添える。",
        "",
        "## 観点",
        "",
        "| 観点 ID | 観点 | 分類 | 判定 | 理由 |",
        "|---|---|---|---|---|",
    ]
    for v in result["viewpoints"]:
        lines.append(f"| {v['id']} | {v['title']} | {v['tags']} |  |  |")
    lines += [
        "",
        "## 必須技法（03-test-techniques.md §11）",
        "",
        "| role | データ型 | 必須技法 | 適用する観点 ID |",
        "|---|---|---|---|",
    ]
    for t in result["techniques"]:
        lines.append(f"| {t['role']} | {t['data_type']} | {t['techniques']} |  |")
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    docs = Path(args.docs_dir)
    techniques_path = docs / "03-test-techniques.md"
    if not techniques_path.is_file():
        raise UsageError(f"技法の文書が無い: {techniques_path}")
    rules = parse_technique_rules(techniques_path)

    known = known_data_types(rules)
    unknown = [d for d in args.data_types if d not in known]
    if unknown:
        raise UsageError(f"未知のデータ型: {unknown}（使える値: {known}）")

    classification = None
    if args.classification:
        entry = load_classification(Path(args.classification), args.url)
        classification = judge_classification(entry, args.margin)
        if classification["reason"]:
            return 1, {"status": "needs_human", **classification}
        role = classification["role"]
    else:
        role = args.role
        if role not in CHECKLIST_OF_ROLE:
            raise UsageError(
                f"role {role} に対応するチェックリストが無い（使える値: {sorted(CHECKLIST_OF_ROLE)}）"
            )

    checklist = docs / "checklists" / f"checklist-{CHECKLIST_OF_ROLE[role]}.md"
    common = docs / "checklists" / "checklist-common.md"
    for p in (checklist, common):
        if not p.is_file():
            raise UsageError(f"チェックリストが無い: {p}")

    result: dict[str, Any] = {
        "status": "ok",
        "role": role,
        "data_types": list(args.data_types),
        "checklist": str(checklist),
        "viewpoints": parse_checklist(checklist) + parse_common(common),
        "techniques": select_rules(rules, role, args.data_types),
    }
    if classification:
        result["classification"] = classification
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(result), encoding="utf-8")
        result["output"] = str(out)
    return 0, result


class _Parser(argparse.ArgumentParser):
    """引数の誤りも標準出力の 1 つの JSON で返す（argparse は既定で標準エラーへ出して終える）。"""

    def error(self, message: str):  # type: ignore[override]
        raise UsageError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="テスト計画書の雛形を作る")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--role", help="page role（form / list / checkout など）")
    source.add_argument("--classification",
                        help="classify_page_role.py の出力 JSON。上位 2 件の差で role を決める")
    parser.add_argument("--url", default=None,
                        help="--classification が複数 URL を含むときに選ぶ URL")
    parser.add_argument("--data-types", nargs="*", default=[],
                        help="§11 のデータ型（数値 / 文字列 / 日付 / ファイル / 金額 など）")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN,
                        help=f"上位 2 件のスコアの差がこれ未満なら人へ渡す（既定 {DEFAULT_MARGIN}）")
    parser.add_argument("--output", default=None, help="Markdown の雛形の書き出し先")
    parser.add_argument("--docs-dir", default=str(DOCS_DIR), help=argparse.SUPPRESS)
    try:
        code, result = build(parser.parse_args(argv))
    except UsageError as exc:
        code, result = 2, {"status": "error", "message": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
