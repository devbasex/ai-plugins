"""出し直しの指摘の部品（#2510）: 読み取りと項目の数え方の唯一の実装。

ループの記録の人の判断待ちの行が正本で、周回のスプリントの組み立てがすべてのループの記録から課題の回を集めて入力ファイル
（`<周回のディレクトリ>/revise-<課題>.json`）へ書き出す。設計のプラン・MVV 判定・表の突き合わせはこの入力を読み、
課題の本文の履歴の節は人が読むためのコピーとして追記だけを行う。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import gh_sections
import jsonio
from sprint_loop_names import LOOP_DIR

HISTORY = "## 出し直しの指摘の履歴"
TABLE = "## 出し直しの指摘への対応"
AUTO, USER = "auto", "user"
BY_LABEL = {AUTO: "自動", USER: "人"}
ASK = "この指摘を満たすように直す。満たせない項目は理由を書く"
CHECK_OK, CHECK_MISSING, CHECK_UNREADABLE = 0, 10, 2  # 表の突き合わせの終了コード（10 は承認ゲートの帯）

_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.*\S)\s*$")
_BLANK = ("", "-", "—")  # 欄が空とみなす字面


def split_items(note: str) -> list[str]:
    """指摘の項目: 箇条書きと番号付きの行を 1 項目とし（コードブロックの中は数えない）、どちらも無ければ全文を 1 項目にする。"""
    text = (note or "").strip()
    if not text:
        return []
    hits = [_ITEM.match(line) for _, line in gh_sections.unfenced_lines(text.split("\n"))]
    return [m.group(1) for m in hits if m] or [text]


def issue_rows(rec: dict, number: int) -> list[dict]:
    """記録の、その課題の答えが「直して出し直す」の行（記録の順）。"""
    return [w for w in rec.get("waiting", []) if w.get("number") == number and w.get("answer") == "revise"]


def _round(loop: str, w: dict, n: int) -> dict:
    by = AUTO if w.get("by") == AUTO else USER  # `by` の無い行は人の回
    items = [] if by == AUTO else [{"id": f"{loop}:{n}-{i}", "text": t} for i, t in enumerate(split_items(w.get("note") or ""), 1)]
    missing = list(w.get("missing") or []) if by == AUTO else []
    return {
        "loop": loop, "round": n, "at": w.get("answered_at") or "", "pr": w.get("pr"), "by": by,
        "note": "" if by == AUTO else w.get("note") or "", "items": items, "missing": missing,
    }  # fmt: skip


def rounds_of(rec: dict, number: int) -> list[dict]:
    """1 つのループの記録の、その課題の回。回数はループごとに 1 から数える。"""
    return [_round(rec.get("name") or "", w, w.get("revise_count") or i) for i, w in enumerate(issue_rows(rec, number), 1)]


def other_loops(main: Path, name: str) -> tuple[list[dict], list[str]]:
    """いま回っているループ以外の記録と、読めなかったループの名前。"""
    recs, skipped = [], []
    for p in sorted((Path(main) / LOOP_DIR).glob("*/loop.json")):
        if p.parent.name == name:
            continue
        try:
            recs.append({"name": p.parent.name, **jsonio.read(p, want=dict)})
        except (OSError, ValueError, jsonio.JsonReadError):
            skipped.append(p.parent.name)
    return recs, skipped


def collect(main: Path, rec: dict, number: int) -> tuple[dict | None, list[str]]:
    """課題の出し直しの指摘の入力と、読めずに飛ばしたループの名前。回が無ければ入力は None。"""
    others, skipped = other_loops(main, rec.get("name") or "")
    rounds = [r for x in [rec, *others] for r in rounds_of(x, number)]
    rounds.sort(key=lambda r: r["at"])
    return ({"issue": number, "rounds": rounds} if rounds else None), skipped


def input_path(lap_dir: str | Path, number: int) -> Path:
    return Path(lap_dir) / f"revise-{number}.json"


def write_input(path: Path, data: dict) -> None:
    jsonio.write_atomic(path, data, indent=1)


def read_input(path: str | Path) -> dict:
    """入力ファイルを読む。読めない・形が違えば OSError か ValueError。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rounds"), list):
        raise ValueError(f"{path} は出し直しの指摘の入力でない")
    return data


def item_ids(data: dict) -> list[str]:
    return [i["id"] for r in data["rounds"] for i in r["items"]]


def round_title(r: dict) -> str:
    return f"第 {r['round']} 回（開発ループ {r['loop']}・{r['at'][:10]}・{BY_LABEL[r['by']]}）"


def prompt_text(data: dict, doc: str | None = None) -> str:
    """起動指示へ入れる全文。`doc` を渡すと、その設計文書へ指摘への対応の表を書く指示を添える。"""
    lines = ["## 出し直しの指摘（承認ゲート 1 で送られた全回）", "", f"{ASK}。前の回で満たした項目を崩さない。"]
    if doc:
        lines += [
            "",
            f"設計文書 `{doc}`（頭のファイル）に節 `{TABLE}` を置き、次の項目ごとに 1 行の表を書く。"
            "列は「項目」（識別子をそのまま書く）・「指摘」（要約）・「対応」（`直した` か `直せない`）・「直した箇所・直せない理由」である。",
        ]
    for r in data["rounds"]:
        lines += ["", f"### {round_title(r)}"]
        if r["by"] == AUTO:
            lines += ["", "前の出し直しで表に示されなかった項目: " + "、".join(r["missing"])]
            continue
        lines += ["", r["note"], "", "項目:", *(f"- {i['id']}: {i['text']}" for i in r["items"])]
    return "\n".join(lines)


def table_rows(section: str | None) -> dict[str, list[str]]:
    """節の中の表の行を、項目の識別子ごとのセルの並びで返す。"""
    import md

    return {row[0].strip().strip("`"): row for t in md.tables(section or "") for row in t.rows if row}


def missing_items(data: dict, section: str | None) -> list[str]:
    """表に行が無い項目と、対応か箇所・理由の欄が空の項目の識別子。"""
    rows = table_rows(section)

    def filled(row: list[str] | None) -> bool:
        return bool(row) and len(row) >= 4 and row[2].strip() not in _BLANK and row[3].strip() not in _BLANK

    return [i for i in item_ids(data) if not filled(rows.get(i))]


def synced_body(body: str, section: str) -> str:
    """設計文書の節を写した設計 PR の本文（表の正は設計文書。本文の側の表は置き換える）。"""
    return gh_sections.replace_section(body, TABLE, section)


def check_table(run, cwd: str, pr: int, doc_text: str, data: dict) -> dict:
    """設計文書の節を設計 PR の本文へ写し、入力の項目と突き合わせる。{code, missing, reason} を返す。`run` は (cmd, cwd, stdin)。"""
    section = gh_sections.get_section(doc_text, TABLE)
    if section is not None:
        code, body, err = run(["gh", "pr", "view", str(pr), "--json", "body", "-q", ".body"], cwd=cwd)
        if code != 0:
            return {"code": CHECK_UNREADABLE, "missing": [], "reason": f"設計 PR #{pr} の本文を読めない: {(err or '').strip()[:200]}"}
        if (gh_sections.get_section(body, TABLE) or "").strip() != section.strip():
            code, _, err = run(["gh", "pr", "edit", str(pr), "--body-file", "-"], cwd=cwd, stdin=synced_body(body, section))
            if code != 0:
                return {"code": CHECK_UNREADABLE, "missing": [], "reason": f"設計 PR #{pr} の本文へ表を写せない: {(err or '').strip()[:200]}"}
    missing = missing_items(data, section)
    return {"code": CHECK_MISSING if missing else CHECK_OK, "missing": missing, "reason": ""}


def doc_at_head(run, cwd: str, pr: int, issue: int, head: str) -> str | None:
    """設計 PR の先頭のコミットの設計文書の頭のファイル。読めなければ None。"""
    if not head:
        return None
    path = f"issues/issue-{issue}-design.md"
    code, text, _ = run(["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{{owner}}/{{repo}}/contents/{path}?ref={head}"], cwd=cwd)
    return text if code == 0 else None


def may_return(rec: dict, number: int) -> bool:
    """自動の出し直しへ戻してよいか（人の出し直し 1 回につき 1 回まで）: 記録の直前の出し直しの行が自動でない。"""
    rows = issue_rows(rec, number)
    return not rows or rows[-1].get("by") != AUTO


def history_title(loop: str, w: dict) -> str:
    by = AUTO if w.get("by") == AUTO else USER
    pr = f"#{w['pr']}" if w.get("pr") else "—"
    return f"### 第 {w['revise_count']} 回（{(w.get('answered_at') or '')[:10]}・設計 PR {pr}・開発ループ {loop}・{BY_LABEL[by]}）"


def history_entry(loop: str, w: dict) -> str:
    """履歴の 1 回分。指摘の全文は引用にして置く（指摘の見出しが履歴の節を区切らないため）。"""
    if w.get("by") == AUTO:
        body = "届いていない項目: " + "、".join(w.get("missing") or [])
    else:
        body = "\n".join(f"> {line}".rstrip() for line in (w.get("note") or "").split("\n"))
    return f"{history_title(loop, w)}\n\n{body}"


def with_history(body: str, loop: str, w: dict) -> str:
    """課題の本文の履歴の節へ 1 回分を足す。同じループの同じ回が既にあれば本文を変えない。"""
    now = gh_sections.get_section(body, HISTORY) or ""
    head, tail = f"### 第 {w['revise_count']} 回（", f"・開発ループ {loop}・"
    if any(line.startswith(head) and tail in line for line in now.split("\n")):
        return body
    return gh_sections.replace_section(body, HISTORY, f"{now.rstrip()}\n\n{history_entry(loop, w)}".strip("\n"))
