#!/usr/bin/env python3
"""check-trigger.py: 検査（構造改善と実装レビュー）のトリガーを判定し、検査の範囲の用意と後始末と記録を持つ（#1078）。

    check-trigger.py eval [--final | --review] [--id <計画名>] [--since <ref>] [--root DIR]
    check-trigger.py prepare --id <名> --state <計画の状態ディレクトリ> [--review] [--root DIR]
    check-trigger.py scope --id <名> --state <DIR>
    check-trigger.py finish --id <名> --pr N [--root DIR]
    check-trigger.py record --id <名> --state <DIR> (--pr N | --failed [--pr N]) [--review] [--root DIR]
    check-trigger.py escape --pr N [--of M] [--root DIR]
    check-trigger.py changed --id <名> [--root DIR]
    check-trigger.py stats [--root DIR]

`pace: fast` の検査は Pull Request ごとに通さず、次のトリガーのどれかが立ったときに、次の開発版の前に
1 回通す。閾値と領域はリポジトリの `.ndf/pace.json` の宣言から読む（無い値は初期値）。

| トリガー | 立つ条件 |
| --- | --- |
| score | 前回の検査からの Pull Request の点数 ≥ triggers.score（共通層を触った PR は common_weight 点、他は 1 点） |
| lines | 前回の検査からの変更（追加 + 削除）> triggers.lines |
| escapes | 前回の検査の後に、同じ領域で逃げた不具合の記録 ≥ triggers.escapes |
| hours | 前回の検査から triggers.hours 時間以上たち、その間に PR が 1 本以上ある |
| final | --final を渡し、範囲に PR が 1 本以上ある（ミッションの終わり） |
| review | --review を渡し、前回のレビューからの範囲に PR が 1 本以上ある（開発版ごとの実装レビュー。ほかのトリガーは見ない） |

**`--review` は実装レビューだけの検査である。** 範囲の起点は、レビューだけの回を含む前回の検査である。記録には
`only: review` が付き、構造改善を含む検査（`--review` 無し）の範囲の起点にはならない。レビューを開発版ごとに
通しても、構造改善のトリガーは前回の構造改善からの差分で数える。

終了コード（eval）: 立った = 0（ok）/ 立たない = 3（stopped。正常な否定の結果）/ 宣言が読めない・git が無い・
範囲を決められない = 2。`finish` と `changed` の 3 は「変更なし」。eval は立ったかどうかにかかわらず
検査の記録へ 1 行を足す（閾値を見直す材料）。

検査の記録は通過記録と同じ置き場（`${CLAUDE_PLUGIN_DATA}` → `${XDG_STATE_HOME:-~/.local/state}/ndf`
→ `${TMPDIR:-/tmp}/ndf-checks`）の `checks/<所有者>__<リポジトリ>.jsonl` に、事象を追記するだけで持つ。
前回の検査は result が merged か no_change の check の最新の行で、無ければ --since、リポジトリの配布の宣言
（`.ndf/supervise.json` の release）が決める正式版のタグの最新、起点のブランチとの分岐点の順に使う。
トリガーの評価は通信しない（git の履歴だけを読む）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (EXIT_OK, EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION,  # noqa: E402
                         emit, result)
from pace import PaceError, matches, read_pace  # noqa: E402

TOOL = "check-trigger"
MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from [^/\s]+/(\S+)")
SKIP_BRANCHES = ("release/", "check/")
BASE_PREFIX = "check-base/"
ENDED = ("merged", "no_change")  # 前回の検査になる check の終わり方


class Stop(Exception):
    def __init__(self, summary: str, code: int = EXIT_UNREADABLE):
        super().__init__(summary)
        self.code = code


# ---------------------------------------------------------------- 宣言・git・置き場


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def git(root: Path, *args: str, check: bool = True) -> str:
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise Stop("git が無い")
    if check and p.returncode != 0:
        raise Stop(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout.strip() if p.returncode == 0 else ""


def gh(root: Path, *args: str) -> str:
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=root)
    except FileNotFoundError:
        raise Stop("gh が無い", EXIT_VIOLATION)
    if p.returncode != 0:
        raise Stop(f"gh {' '.join(args)}: {p.stderr.strip()[:300]}", EXIT_VIOLATION)
    return p.stdout


def repo_root(arg: str | None) -> Path:
    root = git(Path(arg or "."), "rev-parse", "--show-toplevel")
    if not root:
        raise Stop("git の作業ツリーではない（--root を渡す）")
    return Path(root)


def read_json(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Stop(f"{what} が無い: {path}")
    except (OSError, ValueError) as e:
        raise Stop(f"{what} を読めない: {path}: {e}")
    if not isinstance(data, dict):
        raise Stop(f"{what} はオブジェクトで書く: {path}")
    return data


def load_decl(root: Path) -> dict:
    try:
        return read_pace(root)
    except PaceError as e:
        raise Stop(str(e))


def optional_decl(root: Path, name: str) -> dict:
    try:
        return read_json(root / ".ndf" / name, name)
    except Stop:
        return {}


def area_of(path: str, decl: dict) -> tuple[str, bool]:
    """(領域の名前, 共通層か)。どの領域にも当たらなければ、ディレクトリの先頭 3 階層を名前にする。"""
    for a in decl["areas"]:
        if matches(path, a["paths"]):
            return a["name"], bool(a.get("common"))
    parts = Path(path).parts[:-1][:3]
    return ("/".join(parts) or "."), False


def slug_of(root: Path) -> str:
    url = git(root, "config", "--get", "remote.origin.url", check=False)
    m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise Stop("origin の URL から所有者とリポジトリを決められない")
    return f"{m.group(1)}__{m.group(2)}"


def state_base() -> Path:
    """通過記録と同じ順で置き場を決める。"""
    fallback = Path(os.environ.get("TMPDIR", "/tmp")) / "ndf-checks"
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        base = Path(os.environ["CLAUDE_PLUGIN_DATA"])
    elif os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"]) / "ndf"
    elif os.environ.get("HOME"):
        base = Path(os.environ["HOME"]) / ".local" / "state" / "ndf"
    else:
        return fallback
    try:
        (base / "checks").mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        return fallback


def log_path(root: Path) -> Path:
    base = state_base()
    if base.name == "ndf-checks":
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{slug_of(root)}.jsonl"
    return base / "checks" / f"{slug_of(root)}.jsonl"


def read_events(root: Path) -> list[dict]:
    p = log_path(root)
    if not p.is_file():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("kind"):
            rows.append(d)
    return rows


def append_event(root: Path, row: dict) -> None:
    p = log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": row.pop("kind"), "at": now_iso(), **row}, ensure_ascii=False) + "\n")


def base_branch(root: Path) -> str:
    wt = optional_decl(root, "worktree.json")
    if wt.get("base_branch"):
        return wt["base_branch"]
    head = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if head.startswith("origin/"):
        return head[len("origin/"):]
    raise Stop("起点のブランチが分からない（.ndf/worktree.json の base_branch）")


def release_tag_glob(root: Path) -> str | None:
    """配布の宣言から正式版のタグの形を決める。package-plugin は `<plugin>--v*`。"""
    rel = optional_decl(root, "supervise.json").get("release") or {}
    if isinstance(rel, dict) and rel.get("form") == "package-plugin" and rel.get("plugin"):
        return f"{rel['plugin']}--v"
    return None


def commit_time(root: Path, ref: str) -> datetime:
    ts = git(root, "log", "-1", "--format=%cI", ref)
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def parse_at(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------------------------------------------------------------- 範囲とトリガー


def last_check(events: list[dict], review: bool = False) -> dict | None:
    """前回の検査。構造改善を含む検査（review=False）は、レビューだけの回（only: review）を数えない。"""
    ended = [e for e in events if e["kind"] == "check" and e.get("result") in ENDED and e.get("to")
             and (review or e.get("only") != "review")]
    return ended[-1] if ended else None


def range_start(root: Path, events: list[dict], since: str | None,
                review: bool = False) -> tuple[str, datetime, str]:
    """(from のコミット, 期限の起点, 決め方)。"""
    prev = last_check(events, review)
    if prev:
        return prev["to"], parse_at(prev["at"]), f"前回の検査 {prev.get('id', '')}"
    if since:
        sha = git(root, "rev-parse", f"{since}^{{commit}}")
        return sha, commit_time(root, sha), f"--since {since}"
    prefix = release_tag_glob(root)
    if prefix:
        for tag in git(root, "tag", "--list", f"{prefix}*", "--sort=-v:refname").split():
            if "-" not in tag[len(prefix):]:
                sha = git(root, "rev-parse", f"{tag}^{{commit}}")
                return sha, commit_time(root, sha), f"正式版のタグ {tag}"
    base = base_branch(root)
    sha = git(root, "merge-base", f"origin/{base}", "HEAD")
    return sha, commit_time(root, sha), f"origin/{base} との分岐点"


def merged_prs(root: Path, frm: str, to: str, decl: dict) -> list[dict]:
    out = []
    log = git(root, "log", "--first-parent", "--merges", "--format=%H%x09%s", f"{frm}..{to}")
    for line in log.splitlines():
        sha, _, subject = line.partition("\t")
        m = MERGE_SUBJECT.match(subject)
        if not m or m.group(2).startswith(SKIP_BRANCHES):
            continue
        files = git(root, "diff", "--name-only", f"{sha}^1", sha).splitlines()
        common = any(area_of(f, decl)[1] for f in files)
        out.append({"pr": int(m.group(1)), "branch": m.group(2), "common": common,
                    "points": decl["triggers"]["common_weight"] if common else 1})
    return out


def changed_lines(root: Path, frm: str, to: str) -> int:
    stat = git(root, "diff", "--shortstat", frm, to)
    return sum(int(n) for n in re.findall(r"(\d+) (?:insertion|deletion)", stat))


def escapes_since(events: list[dict], since: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        if e["kind"] == "escape" and parse_at(e["at"]) >= since:
            for a in e.get("areas") or []:
                counts[a] = counts.get(a, 0) + 1
    return counts


def evaluate(root: Path, final: bool, since: str | None, to_ref: str | None = None,
             review: bool = False) -> dict:
    decl = load_decl(root)
    events = read_events(root)
    frm, since_at, how = range_start(root, events, since, review)
    to = git(root, "rev-parse", to_ref or f"origin/{base_branch(root)}")
    prs = merged_prs(root, frm, to, decl)
    t = decl["triggers"]
    esc = escapes_since(events, since_at)
    hours = round((now() - since_at).total_seconds() / 3600, 2)
    metrics = {"prs": len(prs), "score": sum(p["points"] for p in prs), "lines": changed_lines(root, frm, to),
               "hours": hours, "escapes": max(esc.values(), default=0), "from": frm, "to": to}
    fired = []
    if review:
        if prs:
            fired.append({"trigger": "review", "value": len(prs), "threshold": 1})
        return {"decl": decl, "fired": fired, "metrics": metrics, "escape_areas": esc, "how": how}
    if metrics["score"] >= t["score"]:
        fired.append({"trigger": "score", "value": metrics["score"], "threshold": t["score"]})
    if metrics["lines"] > t["lines"]:
        fired.append({"trigger": "lines", "value": metrics["lines"], "threshold": t["lines"]})
    if metrics["escapes"] >= t["escapes"]:
        fired.append({"trigger": "escapes", "value": metrics["escapes"], "threshold": t["escapes"]})
    if hours >= t["hours"] and prs:
        fired.append({"trigger": "hours", "value": hours, "threshold": t["hours"]})
    if final and prs:
        fired.append({"trigger": "final", "value": len(prs), "threshold": 1})
    return {"decl": decl, "fired": fired, "metrics": metrics, "escape_areas": esc, "how": how}


# ---------------------------------------------------------------- 副命令


def cmd_eval(a, root: Path) -> tuple[dict, int]:
    ev = evaluate(root, a.final, a.since, review=a.review)
    m = ev["metrics"]
    names = [f["trigger"] for f in ev["fired"]]
    append_event(root, {"kind": "eval", "id": a.id or "", "from": m["from"], "to": m["to"], "fired": names,
                        "metrics": {k: m[k] for k in ("prs", "score", "lines", "hours", "escapes")}})
    rng = f"{m['from'][:10]}..{m['to'][:10]}（{ev['how']}から）"
    if names:
        return result(TOOL, "ok", f"検査のトリガーが立った（{' / '.join(names)}）: 範囲 {rng}・PR {m['prs']} 本",
                      ev["fired"], m), EXIT_OK
    return result(TOOL, "stopped", f"検査のトリガーは立たない: 範囲 {rng}・PR {m['prs']} 本・点数 {m['score']}・"
                  f"{m['lines']} 行・{m['hours']} 時間", [], m), EXIT_PRECONDITION


def check_json(state: str) -> Path:
    return Path(state) / "check.json"


def delete_base(root: Path, name: str) -> None:
    branch = BASE_PREFIX + name
    git(root, "push", "-q", "origin", "--delete", branch, check=False)
    git(root, "branch", "-D", branch, check=False)


def cmd_prepare(a, root: Path) -> tuple[dict, int]:
    ev = evaluate(root, False, a.since, to_ref="HEAD", review=a.review)
    m = ev["metrics"]
    prev = [e for e in read_events(root) if e["kind"] == "eval" and e.get("id") == a.id]
    fired = prev[-1].get("fired", []) if prev else [f["trigger"] for f in ev["fired"]]
    branch = BASE_PREFIX + a.id
    git(root, "branch", "-f", branch, m["from"])
    try:
        git(root, "push", "-q", "-f", "origin", f"{branch}:refs/heads/{branch}")
    except Stop as e:
        raise Stop(f"{branch} を送れない: {e}", EXIT_VIOLATION)
    git(root, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", check=False)
    files = git(root, "diff", "--name-only", m["from"], m["to"]).splitlines()
    data = {"id": a.id, "from": m["from"], "to": m["to"], "fired": fired, "metrics": m, "files": files,
            "escape_areas": ev["escape_areas"], "base": branch}
    check_json(a.state).parent.mkdir(parents=True, exist_ok=True)
    check_json(a.state).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return result(TOOL, "ok", f"{branch} を {m['from'][:10]} に作って送った（範囲の PR {m['prs']} 本・"
                  f"ファイル {len(files)} 件）", [{"branch": branch, "from": m["from"], "to": m["to"]}],
                  {k: m[k] for k in ("prs", "score", "lines")}), EXIT_OK


def scope_dirs(root: Path, data: dict) -> list[str]:
    decl = load_decl(root)
    escaped = set(data.get("escape_areas") or {})
    groups: list[list[str]] = [[], [], []]
    for f in data.get("files") or []:
        d = str(Path(f).parent)
        if d == ".":
            continue
        name, common = area_of(f, decl)
        g = 0 if common else 1 if name in escaped else 2
        if not any(d in grp for grp in groups):
            groups[g].append(d)
    return [d for grp in groups for d in grp] or ["."]


def cmd_scope(a, root: Path) -> int:
    p = check_json(a.state)
    if not p.is_file():
        print(f"check.json が無い: {p}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(" ".join(scope_dirs(root, json.loads(p.read_text()))))
    return EXIT_OK


def cmd_finish(a, root: Path) -> tuple[dict, int]:
    base = base_branch(root)
    stat = git(root, "diff", "--numstat", f"origin/{base}...HEAD")
    lines = sum(int(x) for ln in stat.splitlines() for x in ln.split("\t")[:2] if x.isdigit())
    files = len(stat.splitlines())
    if not files:
        gh(root, "pr", "close", str(a.pr), "--comment", "検査で変更が無かったため閉じる（check-trigger.py finish）")
        return result(TOOL, "stopped", f"検査で変更が無い。#{a.pr} を閉じた（変更なし）", [],
                      {"files": 0, "lines": 0}), EXIT_PRECONDITION
    try:
        gh(root, "pr", "edit", str(a.pr), "--base", base)
    except Stop as e:
        raise Stop(f"#{a.pr} の宛先を {base} へ付け替えられない: {e}", EXIT_VIOLATION)
    return result(TOOL, "ok", f"#{a.pr} の宛先を {base} へ付け替えた（検査の修正 {files} ファイル・{lines} 行）", [],
                  {"files": files, "lines": lines}), EXIT_OK


def findings_of(state: Path) -> tuple[dict, str]:
    """計画の state.json から (件数, 最後に落ちたステップ) を読む。"""
    try:
        log = json.loads((state / "state.json").read_text()).get("log") or []
    except (OSError, ValueError):
        log = []
    counts = {e.get("id"): e.get("counts") or {} for e in log if e.get("counts")}
    ref, rev = counts.get("refactor", {}), counts.get("review", {})
    findings = {"applied": ref.get("adopted", ref.get("applied")), "reverted": ref.get("reverted"),
                "findings": rev.get("findings"), "unresolved": rev.get("unresolved")}
    failed = [e.get("id") for e in log if e.get("exit") not in (0, None) and not e.get("gate")
              and not str(e.get("id", "")).startswith("abort")]
    return findings, (failed[-1] if failed else "")


def cmd_record(a, root: Path) -> tuple[dict, int]:
    p = check_json(a.state)
    data = json.loads(p.read_text()) if p.is_file() else {}
    findings, failed_at = findings_of(Path(a.state))
    row = {"kind": "check", "id": a.id, "from": data.get("from", ""), "to": data.get("to", ""),
           "metrics": data.get("metrics", {}), "findings": findings}
    if a.review:
        row["only"] = "review"
    if a.pr:
        row["pr"] = a.pr
    if a.failed:
        row.update(result="failed", failed_at=failed_at or "不明")
        try:
            append_event(root, row)
            written = "記録した"
        except OSError as e:
            written = f"記録できない（{e}）"
        delete_base(root, a.id)
        if a.pr:
            subprocess.run(["gh", "pr", "close", str(a.pr), "--comment", "検査が途中で落ちたため閉じる"],
                           cwd=root, capture_output=True, text=True)
        return result(TOOL, "stopped", f"検査 {a.id} が {row['failed_at']} で落ちた。{written}・"
                      f"{BASE_PREFIX}{a.id} を消した", [row], {}), EXIT_VIOLATION
    if not a.pr:
        raise Stop("record には --pr か --failed が要る")
    state = json.loads(gh(root, "pr", "view", str(a.pr), "--json", "state")).get("state")
    res = {"MERGED": "merged", "CLOSED": "no_change"}.get(state)
    if not res:
        raise Stop(f"#{a.pr} がマージも閉じられもしていない（{state}）", EXIT_VIOLATION)
    row["result"] = res
    try:
        append_event(root, row)
    except OSError as e:
        raise Stop(f"検査の記録へ書けない: {e}", EXIT_VIOLATION)
    delete_base(root, a.id)
    return result(TOOL, "ok", f"検査 {a.id} を記録した（{res}・#{a.pr}）", [row], {}), EXIT_OK


def cmd_escape(a, root: Path) -> tuple[dict, int]:
    decl = load_decl(root)
    info = json.loads(gh(root, "pr", "view", str(a.pr), "--json", "files"))
    areas: list[str] = []
    for f in info.get("files") or []:
        name = area_of(f.get("path", ""), decl)[0]
        if name not in areas:
            areas.append(name)
    row = {"kind": "escape", "pr": a.pr, "of": a.of, "areas": areas}
    try:
        append_event(root, row)
    except OSError as e:
        raise Stop(f"検査の記録へ書けない: {e}", EXIT_VIOLATION)
    of = f"#{a.of}" if a.of else "不明"
    return result(TOOL, "ok", f"逃げた不具合を記録した（直した #{a.pr}・持ち込んだ {of}・領域 {' / '.join(areas)}）",
                  [row], {"areas": len(areas)}), EXIT_OK


def cmd_changed(a, root: Path) -> tuple[dict, int]:
    rows = [e for e in read_events(root) if e.get("id") == a.id and e["kind"] in ("eval", "check")]
    checks = [e for e in rows if e["kind"] == "check"]
    if checks:
        res = checks[-1].get("result")
        if res == "merged":
            return result(TOOL, "ok", f"検査 {a.id} は変更をマージした", [checks[-1]], {}), EXIT_OK
        if res == "no_change":
            return result(TOOL, "stopped", f"検査 {a.id} は変更なし", [checks[-1]], {}), EXIT_PRECONDITION
        return result(TOOL, "stopped", f"検査 {a.id} は落ちた（{checks[-1].get('failed_at', '')}）",
                      [checks[-1]], {}), EXIT_VIOLATION
    if rows and not rows[-1].get("fired"):
        return result(TOOL, "stopped", f"検査 {a.id} はトリガーが立たず流れていない（変更なし）", [rows[-1]], {}), \
            EXIT_PRECONDITION
    raise Stop(f"検査 {a.id} の記録が無い")


def cmd_stats(a, root: Path) -> tuple[dict, int]:
    events = read_events(root)
    evals = [e for e in events if e["kind"] == "eval"]
    checks = [e for e in events if e["kind"] == "check"]
    ended = [e for e in checks if e.get("result") in ENDED]
    rows = []
    for i, c in enumerate(ended):
        # 区間は同じ種類（構造改善を含む検査どうし・実装レビューだけの回どうし）の次の記録までで切る
        nxt = next((d for d in ended[i + 1:] if d.get("only") == c.get("only")), None)
        until = parse_at(nxt["at"]) if nxt else now()
        escaped = sum(1 for e in events if e["kind"] == "escape" and parse_at(c["at"]) < parse_at(e["at"]) <= until)
        rows.append({"id": c.get("id"), "at": c["at"], "result": c["result"], "pr": c.get("pr"), "only": c.get("only"),
                     "findings": c.get("findings") or {}, "escapes_after": escaped})
    fired: dict[str, int] = {}
    for e in evals:
        for t in e.get("fired") or []:
            fired[t] = fired.get(t, 0) + 1
    metrics = {"evals": len(evals), "fired": sum(1 for e in evals if e.get("fired")), "by_trigger": fired,
               "checks": len(checks), "failed": sum(1 for c in checks if c.get("result") == "failed"),
               "escapes": sum(1 for e in events if e["kind"] == "escape"), "log": str(log_path(root))}
    return result(TOOL, "ok", f"評価 {metrics['evals']} 回（立った {metrics['fired']}）・検査 {len(checks)} 回・"
                  f"逃げた不具合 {metrics['escapes']} 件", rows, metrics), EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="check-trigger.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, need_id: bool = False, state: bool = False) -> argparse.ArgumentParser:
        s = sub.add_parser(name)
        s.add_argument("--root")
        if need_id:
            s.add_argument("--id", required=True)
        if state:
            s.add_argument("--state", required=True, help="計画の状態ディレクトリ（check.json と state.json の置き場）")
        return s

    s = add("eval")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--final", action="store_true")
    g.add_argument("--review", action="store_true", help="実装レビューだけの検査（前回のレビューから PR が 1 本以上で立つ）")
    s.add_argument("--id", default="")
    s.add_argument("--since")
    s = add("prepare", True, True)
    s.add_argument("--since")
    s.add_argument("--review", action="store_true", help="範囲の起点をレビューだけの回を含む前回の検査にする")
    add("scope", True, True)
    s = add("finish", True)
    s.add_argument("--pr", type=int, required=True)
    s = add("record", True, True)
    s.add_argument("--pr", type=int)
    s.add_argument("--failed", action="store_true")
    s.add_argument("--review", action="store_true", help="レビューだけの回として記録する（only: review）")
    s = add("escape")
    s.add_argument("--pr", type=int, required=True)
    s.add_argument("--of", type=int, default=0, help="不具合を持ち込んだ PR（分からなければ 0）")
    add("changed", True)
    add("stats")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        root = repo_root(a.root)
        if a.cmd == "scope":
            return cmd_scope(a, root)
        fn = {"eval": cmd_eval, "prepare": cmd_prepare, "finish": cmd_finish, "record": cmd_record,
              "escape": cmd_escape, "changed": cmd_changed, "stats": cmd_stats}[a.cmd]
        out, code = fn(a, root)
    except Stop as e:
        out, code = result(TOOL, "stopped", str(e), [], {}), e.code
    emit(out, code)


if __name__ == "__main__":
    sys.exit(main())
