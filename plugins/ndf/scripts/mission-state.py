#!/usr/bin/env python3
"""ミッションの状態を mission.json に持ち、引継ぎ文書の節・status・ndf-next を生成する（#1063）。

LLM を呼ばない。入力は supervise.py queue の done の JSON と各計画の report.md だけである。

| 副命令 | 何をする |
| --- | --- |
| `init <mission.json> --name <名> [--milestone M] [--issue N]... [--plan <種類>=<plan.json>]... [--done <done.json>]... [--dev <版>] [--prod <版>] [--goal <雛形の文字列か @ファイル>]` | 状態のファイルを作る |
| `update <mission.json> [--done <done.json>]... [--next <plan.json>=<文>]...` | done の JSON と報告を読み、行の状態・PR・秒・費用を埋める。何度走らせても同じ結果 |
| `gate <mission.json> <関門の名> --what <何を> [--at <ISO 8601>]` | 関門の承認の時刻を書く |
| `render <mission.json> <引継ぎ文書> --section <見出しの語> [--demote <前の節の語> --heading <新しい見出し>]` | 見出しに語を含む節の本文を置き換える。節の外は変えない |
| `status <mission.json>` | 端末向けに 1 行ずつ（ミッション・状態・次） |
| `next <mission.json> [--doc <引継ぎ文書> --section <見出しの語>] [--replace <見出しの語>]` | ndf-next の囲みを出す。`--replace` なら引継ぎ文書のその節も置き換える |

雛形（`--goal`）は `{name}`・`{milestone}`・`{heading}`（現在地の見出し）・`{dev}`・`{prod}`・
`{issues}` を差し込む。

計画の種類は 実装・開発版・本番（ほかの語もそのまま使える）。done を登録しなければ、計画の
状態ディレクトリ（`<計画>-state/queue-done.json`）を探す。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOL = "mission-state"
SECTION_DEFAULT = "今の会話の進み"
NEXT_SECTION_DEFAULT = "次に実行するコマンド"
NOT_DONE = "まだ"


def result(status: str, summary: str, items=None, metrics=None, **extra) -> dict:
    out = {"tool": TOOL, "status": status, "summary": summary, "items": items or [], "metrics": metrics or {}}
    out.update(extra)
    return out


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def save(path: str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    os.replace(tmp, p)


def state_dir_of(plan: str) -> Path:
    return Path(plan).parent / (Path(plan).stem + "-state")


def field(report: str, name: str) -> str:
    m = re.search(rf"^- {re.escape(name)}: (.*)$", report, re.M)
    return m.group(1).strip() if m else ""


def pr_label(value: str) -> str:
    """報告の Pull Request（URL か番号）を `#番号` にする。無ければ空。"""
    if not value or value == "無し":
        return ""
    m = re.search(r"(\d+)\s*$", value)
    return f"#{m.group(1)}" if m else value


def report_cost(report: str) -> float | None:
    m = re.search(r"/ \$([0-9.]+)\s*$", field(report, "LLM の使用量"))
    return float(m.group(1)) if m else None


def plan_issues(plan: str) -> list[int]:
    try:
        return [int(i) for i in json.loads(Path(plan).read_text()).get("課題", [])]
    except (OSError, ValueError, TypeError):
        return []


def parse_pair(text: str, flag: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key or not value:
        raise SystemExit(f"{flag} は <左>=<右> の形で渡す: {text}")
    return key, value


def default_label(kind: str, issues: list[int], m: dict) -> str:
    if kind == "開発版" and m.get("versions", {}).get("dev"):
        return f"開発版 {m['versions']['dev']}"
    if kind == "本番" and m.get("versions", {}).get("prod"):
        return f"本番 {m['versions']['prod']}"
    tail = " ".join(f"#{i}" for i in issues)
    return f"{kind} {tail}".strip()


# ---------------------------------------------------------------- init / update / gate


def cmd_init(a) -> dict:
    goal = a.goal or ""
    if goal.startswith("@"):
        goal = Path(goal[1:]).read_text().rstrip("\n")
    m = {
        "name": a.name, "milestone": a.milestone or "", "issues": a.issue or [],
        "versions": {"dev": a.dev or "", "prod": a.prod or ""},
        "plans": [], "done": list(a.done or []), "gates": [], "goal_template": goal,
    }
    for text in a.plan or []:
        kind, plan = parse_pair(text, "--plan")
        issues = plan_issues(plan)
        m["plans"].append({"kind": kind, "plan": plan, "issues": issues,
                           "label": default_label(kind, issues, m), "next": ""})
    save(a.mission, m)
    return result("ok", f"ミッション {a.name} を書いた（計画 {len(m['plans'])} 本）: {a.mission}",
                  [{"plan": p["plan"], "kind": p["kind"]} for p in m["plans"]],
                  {"plans": len(m["plans"]), "done": len(m["done"])})


def done_items(m: dict) -> dict[str, dict]:
    """登録した done（無ければ各計画の状態ディレクトリの queue-done.json）の items を計画のパスで引く。"""
    paths = list(m.get("done", []))
    for p in m.get("plans", []):
        d = str(state_dir_of(p["plan"]) / "queue-done.json")
        if d not in paths:
            paths.append(d)
    items: dict[str, dict] = {}
    for d in paths:
        try:
            data = json.loads(Path(d).read_text())
        except (OSError, ValueError):
            continue
        for it in data.get("items", []):
            if it.get("plan"):
                items[it["plan"]] = it  # 後に書いた done（登録の順）を採る
    return items


def fill_row(p: dict, item: dict | None) -> dict:
    row = {"result": NOT_DONE, "exit": None, "pr": "", "seconds": None, "cost": None, "reason": "", "report": ""}
    if item is None:
        return row
    row.update(result=item.get("result") or "不明", exit=item.get("exit"), seconds=item.get("seconds"))
    rep = Path(item.get("report") or state_dir_of(p["plan"]) / "report.md")
    if rep.is_file():
        text = rep.read_text()
        row["report"] = str(rep)
        row["pr"] = pr_label(field(text, "Pull Request"))
        row["cost"] = report_cost(text)
        reason = field(text, "理由")
        if row["result"] != "完了" and reason not in ("", "無し"):
            row["reason"] = reason
    return row


def cmd_update(a) -> dict:
    m = load(a.mission)
    for d in a.done or []:
        if d not in m["done"]:
            m["done"].append(d)
    nexts = dict(parse_pair(t, "--next") for t in (a.next or []))
    items = done_items(m)
    for p in m["plans"]:
        if p["plan"] in nexts:
            p["next"] = nexts[p["plan"]]
        if not p.get("issues"):
            p["issues"] = plan_issues(p["plan"])
        p["row"] = fill_row(p, items.get(p["plan"]))
    save(a.mission, m)
    rows = [{"plan": p["plan"], **p["row"]} for p in m["plans"]]
    finished = sum(1 for r in rows if r["result"] != NOT_DONE)
    stopped = sum(1 for r in rows if r["result"] not in (NOT_DONE, "完了"))
    return result("ok", f"計画 {len(rows)} 本: 終わった {finished} / 完了でない {stopped}", rows,
                  {"plans": len(rows), "finished": finished, "stopped": stopped})


def cmd_gate(a) -> dict:
    m = load(a.mission)
    at = a.at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    gates = [g for g in m.get("gates", []) if g.get("name") != a.name]
    gates.append({"name": a.name, "what": a.what, "at": at})
    m["gates"] = gates
    save(a.mission, m)
    return result("ok", f"{a.name} の承認を書いた（{at}）", gates, {"gates": len(gates)})


# ---------------------------------------------------------------- 生成


def row_of(p: dict) -> dict:
    return p.get("row") or fill_row(p, None)


def fmt_seconds(v) -> str:
    return "—" if v is None else f"{v:g}"


def fmt_cost(v) -> str:
    return "—" if v is None else f"${v:.3f}"


def state_text(r: dict) -> str:
    s = r["result"]
    if r["result"] not in (NOT_DONE, "完了") and r.get("exit") not in (None, 0):
        s += f"（exit={r['exit']}）"
    if r.get("reason"):
        s += f"。理由: {r['reason']}"
    return s


def cell(text: str) -> str:
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def section_body(m: dict) -> str:
    """節の本文（見出しの次の行から）。空行で始まり、空行で終わる。"""
    lines = ["", "| ミッション | 状態 | PR | 秒 | 費用 | 次 |", "| --- | --- | --- | ---: | ---: | --- |"]
    for p in m.get("plans", []):
        r = row_of(p)
        lines.append("| " + " | ".join(cell(x) for x in (
            p.get("label") or p["plan"], state_text(r), r.get("pr"), fmt_seconds(r.get("seconds")),
            fmt_cost(r.get("cost")), p.get("next"))) + " |")
    lines.append("")
    head = f"- ミッション: {m.get('name', '')}"
    if m.get("milestone"):
        head += f"（マイルストーン {m['milestone']}）"
    if m.get("issues"):
        head += "。課題: " + " ".join(f"#{i}" for i in m["issues"])
    lines.append(head)
    v = m.get("versions", {})
    if v.get("dev") or v.get("prod"):
        lines.append(f"- 版: 開発版 {v.get('dev') or '—'} / 本番 {v.get('prod') or '—'}")
    for g in m.get("gates", []):
        lines.append(f"- {g['name']} 承認（{g['at']}）: {g.get('what', '')}")
    if m.get("done"):
        lines.append("- queue の done: " + " ".join(f"`{d}`" for d in m["done"]))
    total = [row_of(p).get("cost") for p in m.get("plans", [])]
    if any(c is not None for c in total):
        lines.append(f"- LLM の費用の計: ${sum(c for c in total if c is not None):.3f}")
    lines.append("")
    return "\n".join(lines) + "\n"


def find_section(text: str, word: str) -> tuple[int, int, int, str] | None:
    """見出しに word を含む最初の節の (見出しの行の始まり, 本文の始まり, 本文の終わり, 見出しの行) を返す。

    本文は、見出しと同じか浅い見出しの手前まで。囲みのコードブロックの中の # は見出しとみなさない。"""
    lines = text.splitlines(keepends=True)
    pos, fence, start = 0, "", None
    level = 0
    head_start = body_start = 0
    head_line = ""
    for line in lines:
        s = line.rstrip("\r\n")
        f = re.match(r"^(`{3,}|~{3,})", s)
        if f and (not fence or s.startswith(fence[0] * len(fence)) and not s[len(fence):].strip()):
            fence = "" if fence else f.group(1)
        elif not fence:
            h = re.match(r"^(#{1,6})\s", s)
            if h:
                if start is not None and len(h.group(1)) <= level:
                    return head_start, body_start, pos, head_line
                if start is None and word in s:
                    start, level = pos, len(h.group(1))
                    head_start, body_start, head_line = pos, pos + len(line), line
        pos += len(line)
    if start is None:
        return None
    return head_start, body_start, len(text), head_line


def heading_text(line: str) -> str:
    return line.rstrip("\r\n").lstrip("#").strip()


def cmd_render(a) -> dict:
    m = load(a.mission)
    text = Path(a.doc).read_text()
    found = find_section(text, a.section)
    if found is None:
        return result("stopped", f"見出しに「{a.section}」を含む節が無い: {a.doc}")
    head_start, body_start, body_end, head_line = found
    body = section_body(m)
    if a.demote:
        if not a.heading:
            return result("stopped", "--demote には新しい見出し（--heading）が要る")
        hashes = re.match(r"^#+", head_line).group(0)
        eol = head_line[len(head_line.rstrip("\r\n")):] or "\n"
        old_head = head_line.replace(a.section, a.demote, 1)
        new = f"{hashes} {a.heading}{eol}{body}"
        out = text[:head_start] + new + old_head + text[body_start:]
        summary = f"節「{heading_text(head_line)}」を「{heading_text(old_head)}」へ下げ、節「{a.heading}」を足した"
    else:
        out = text[:body_start] + body + text[body_end:]
        summary = f"節「{heading_text(head_line)}」の本文を置き換えた"
    if out != text:
        Path(a.doc).write_text(out)
    return result("ok", summary, [{"doc": a.doc, "changed": out != text}],
                  {"plans": len(m.get("plans", [])), "bytes": len(body.encode())})


def status_lines(m: dict) -> list[str]:
    head = f"ミッション: {m.get('name', '')}"
    if m.get("milestone"):
        head += f"（マイルストーン {m['milestone']}）"
    out = [head]
    for p in m.get("plans", []):
        r = row_of(p)
        extra = "・".join(x for x in (r.get("pr"), (f"{fmt_seconds(r['seconds'])} 秒" if r.get("seconds") is not None
                                                  else ""), (fmt_cost(r["cost"]) if r.get("cost") is not None else "")) if x)
        state = state_text(r) + (f"（{extra}）" if extra else "")
        out.append(f"{p.get('label') or p['plan']}: {state}。次: {p.get('next') or '—'}")
    for g in m.get("gates", []):
        out.append(f"{g['name']}: 承認 {g['at']}")
    return out


def cmd_status(a) -> dict | None:
    m = load(a.mission)
    lines = status_lines(m)
    if a.json:
        return result("ok", lines[0], lines[1:], {"plans": len(m.get("plans", []))})
    print("\n".join(lines))
    return None


class Blank(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def next_block(m: dict, heading: str) -> str:
    v = m.get("versions", {})
    values = Blank(name=m.get("name", ""), milestone=m.get("milestone", ""), heading=heading,
                   dev=v.get("dev", ""), prod=v.get("prod", ""),
                   issues=" ".join(f"#{i}" for i in m.get("issues", [])))
    body = (m.get("goal_template") or "").format_map(values).strip("\n")
    return f"```ndf-next\n{body}\n```\n"


def cmd_next(a) -> dict | None:
    m = load(a.mission)
    heading = m.get("heading", "")
    text = None
    if a.doc:
        text = Path(a.doc).read_text()
        found = find_section(text, a.section)
        if found is None:
            return result("stopped", f"見出しに「{a.section}」を含む節が無い: {a.doc}")
        heading = heading_text(found[3])
    if not (m.get("goal_template") or "").strip():
        return result("stopped", "mission.json に /goal の雛形（goal_template）が無い")
    block = next_block(m, heading)
    if not a.replace:
        if a.json:
            return result("ok", "ndf-next の囲みを作った", [block], {"heading": heading})
        print(block, end="")
        return None
    if text is None:
        return result("stopped", "--replace には引継ぎ文書（--doc）が要る")
    found = find_section(text, a.replace)
    if found is None:
        return result("stopped", f"見出しに「{a.replace}」を含む節が無い: {a.doc}")
    _, body_start, body_end, head_line = found
    tail = "\n" if body_end < len(text) else ""
    out = text[:body_start] + "\n" + block + tail + text[body_end:]
    if out != text:
        Path(a.doc).write_text(out)
    return result("ok", f"節「{heading_text(head_line)}」を ndf-next の囲みで置き換えた", [block],
                  {"heading": heading, "changed": out != text})


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mission-state.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("mission")
    s.add_argument("--name", required=True)
    s.add_argument("--milestone")
    s.add_argument("--issue", type=int, action="append")
    s.add_argument("--plan", action="append", help="<種類>=<plan.json>（種類は 実装・開発版・本番 など）")
    s.add_argument("--done", action="append")
    s.add_argument("--dev")
    s.add_argument("--prod")
    s.add_argument("--goal", help="/goal の文面の雛形。@<ファイル> ならファイルから読む")

    s = sub.add_parser("update")
    s.add_argument("mission")
    s.add_argument("--done", action="append")
    s.add_argument("--next", action="append", help="<plan.json>=<行の「次」>")

    s = sub.add_parser("gate")
    s.add_argument("mission")
    s.add_argument("name")
    s.add_argument("--what", required=True)
    s.add_argument("--at")

    s = sub.add_parser("render")
    s.add_argument("mission")
    s.add_argument("doc")
    s.add_argument("--section", default=SECTION_DEFAULT)
    s.add_argument("--demote", help="今の節の見出しの語をこの語へ替えて下げ、新しい節を前に足す（例: 前の会話の進み）")
    s.add_argument("--heading", help="--demote で足す節の見出し")

    s = sub.add_parser("status")
    s.add_argument("mission")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("next")
    s.add_argument("mission")
    s.add_argument("--doc")
    s.add_argument("--section", default=SECTION_DEFAULT, help="現在地の見出しを読む節")
    s.add_argument("--replace", nargs="?", const=NEXT_SECTION_DEFAULT,
                   help=f"引継ぎ文書のこの語の節を置き換える（既定 {NEXT_SECTION_DEFAULT}）")
    s.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    fn = {"init": cmd_init, "update": cmd_update, "gate": cmd_gate, "render": cmd_render,
          "status": cmd_status, "next": cmd_next}[a.cmd]
    try:
        out = fn(a)
    except (OSError, ValueError) as e:
        out = result("stopped", f"読めない・書けない: {e}")
    if out is None:
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
