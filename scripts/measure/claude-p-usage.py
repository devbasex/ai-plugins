#!/usr/bin/env python3
"""claude -p（計画と作業ツリーで動いた CLI）の消費を、載った版ごとに集計する（#1142 の進め方 0・#1316）。

読むだけ。本文・プロンプト・会話の ID は出力に写さない（数値・ブランチ名・PR 番号・計画の名前だけ）。

    python3 scripts/measure/claude-p-usage.py [--since <ISO 8601>] [--until <ISO 8601>]
        [--repo <リポジトリ>] [--plugin ndf] [--usage-root <帳簿のディレクトリ>]
        [--sv-root ~/.local/state/ndf/sv] [--projects ~/.claude/projects]
        [--out ~/.local/state/ndf/measure-1142/claude-p-usage]

副命令（物差しの測り直し。旧い帳簿・会話の置き場・`gh`・`ndf` を読まない。JSON 1 つを標準出力へ書く）:

    python3 scripts/measure/claude-p-usage.py launches --since <時差つき> --until <時差つき>
        --repo <所有者>/<リポジトリ> [--state <.ndf>]... [--usage-log <usage.jsonl>]
    python3 scripts/measure/claude-p-usage.py plan-cost --since <時差つき> --until <時差つき>
        --counted <token-usage.py --sessions-out の一覧> --repo <所有者>/<リポジトリ> [--state <.ndf>]... [--usage-log <usage.jsonl>]
    python3 scripts/measure/claude-p-usage.py measure ...（plan-cost と同じ引数。U2 と U1 の計画起動分を一度に出す）

期間は半開区間 `[--since, --until)`。母数はどちらも、対象リポジトリに属する状態ディレクトリ（`--state` で
期間に動いたプランの保存先をすべて渡す。`ndf plan next` は `--state-dir` を省くとプランの隣の `<プラン>.state` に書く。
省いた既定はカレントのリポジトリの .ndf だけ。別のリポジトリのものを渡すと計測不能）の、起動の時刻で期間に絞った
`kind: launch` の行で、使用量ログの行はその鍵（起動ごとに一意な `launch_id`）で照合する。`launches` は U2（使用量の行が
残った起動 ÷ 計画が起動した claude -p。起動の記録に対応していない実行（頭の `kind: run` に `launches` を持たない）・
識別できない起動の行・鍵の無い使用量の行がある期間は計測不能）、`plan-cost` は U1 の計画起動分（照合できた行のうち
対象リポジトリの `repo` とモデル別の使用量 `model_usage` を持ち `--counted` に無い会話の換算の費用と、期間の終わりをまたいだ一覧の会話の
会話側で計上した分を引いた残り。母数のすべての起動に行があるときだけ `complete: true`）を返す。両方を測るときは `measure`（引数は `plan-cost` と同じ）を打つ: 母数の収集と使用量ログの
照合を 1 回だけ行い、`u2` と `plan_cost` を 1 つの JSON で返す（値は 2 つの副命令と同じ）。

`--repo` はタグ・CHANGELOG・`gh` を読むリポジトリと、帳簿のファイル（`<repo_key>.jsonl`）と、
会話の置き場の名前（`<repo のパスの / と . を - にしたもの>--worktrees-`）を決める。`--plugin` はタグの接頭辞
`<plugin>--v` と CHANGELOG の見出し `## [<plugin> <版>]` を決める。`--since` の既定は帳簿の最も古い行の時刻。

集める 3 つの出所:
  A. 記録の残る claude -p: <projects>/<repo>--worktrees-*/<会話>.jsonl（supervise.py の full のステップ）。
     呼び出しごとの usage が取れるので token-usage.py と同じ Usage / scan_file で数える
  B. 記録の残らない claude -p（supervise.py の work / judge / slow / pr と MVV の判定）。
     帳簿（lib/usage_ledger.py）の行を計画（`plan` のパス）ごとに足す。モデル・書き込みの 5 分 / 1 時間の別を持つ。
     帳簿の `full` の行は、session_id の会話が A にあれば A で数え、B には件数（full_calls）だけを出す（I1）。
     帳簿の最も古い行より前の期間だけ、フェーズの報告の「LLM の使用量」の行から読む（source = report。
     モデルと 5 分 / 1 時間の別が無い）
  C. レビュー・改修の席: <projects>/-tmp-ndf-worktrees-*（cross-review / cross-refactoring の claude の席）。
     token-usage.py が外部 CLI として扱うもの。参考として別に数える

版は lib/release_map.py の載った版（token-usage.py の軸 release と同じ寄せ方）。版の PR 数（release_prs）は
PR の一覧だけで決まり、観測した行から数えた PR は prs_seen に残す。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("token_usage", Path(__file__).resolve().parents[1] / "token-usage.py")
tu = importlib.util.module_from_spec(_spec)
sys.modules["token_usage"] = tu
_spec.loader.exec_module(tu)  # 係数（WEIGHTS / READ_RATES）と数え方は token-usage.py から取る（scripts/lib を import の道に足す）
import release_map  # noqa: E402  載った版への寄せ方（token-usage.py と同じ関数）
import token_rates  # noqa: E402  token-usage.py が読む係数と同じもの（plan-cost の換算）
import usage_ledger  # noqa: E402

# 置き場所は main が引数から決める（configure）
REPO = Path("/work/ai-plugins")
SV_ROOT = Path.home() / ".local/state/ndf/sv"
PROJ = Path.home() / ".claude/projects"
WT_PREFIX = "-work-ai-plugins--worktrees-"
SEAT_PREFIX = "-tmp-ndf-worktrees-devbasex--ai-plugins-"

USE_RE = re.compile(r"LLM の使用量: 入力 (\d+) / cache read (\d+) / cache write (\d+) / 出力 (\d+) / \$([\d.]+)")
REC_RE = re.compile(r"- 記録: (\S+)")
PR_RE = re.compile(r"- Pull Request: ([^\n]*)")
PR_NUM_RE = re.compile(r"(?:pull/|#|^)(\d{3,5})\b")
ISSUE_RE = re.compile(r"- 課題: ([^\n]*)")
PHASE_RE = re.compile(r"- フェーズ: ([^\n]*)")
WORKER_RE = re.compile(r"使った worker: 修正 (\d+)（claude -p）/ 判断 (\d+)")
ROW_RE = re.compile(r"^\| (\S+) \| (\d+) \| ([\d.]*) \| (\d+) \| (\d+) \| (\d+) \| \$([\d.]+) \|$", re.M)


def plan_name_re(sv_root: Path) -> re.Pattern:
    """報告の「記録」のパスから計画名（<置き場の名前>/<計画>）を取る。古い `r<数字>/plans/…-state` も読む。"""
    return re.compile(re.escape(str(sv_root)) + r"/([\w.-]+)/(?:plans/)?([\w.-]+?)-state")


PLANNAME_RE = plan_name_re(SV_ROOT)
# 計画の JSON の「フェーズ」→ 種類
PHASE_KIND = (
    ("設計", "design"),
    ("仕様", "design"),
    ("実装", "impl"),
    ("検査", "check"),
    ("レビュー", "review"),
    ("リリース", "release"),
    ("デプロイ", "release"),
    ("配布", "release"),  # 語を分ける前の計画（#2620）
)
OUTSIDE = "（計画の外）"


def project_dir_name(path: Path) -> str:
    """Claude Code が会話の置き場の名前にする形（/ と . を - にする）。"""
    return re.sub(r"[/.]", "-", str(path))


def configure(repo: Path, sv_root: Path, projects: Path, seat_prefix: str) -> None:
    global REPO, SV_ROOT, PROJ, WT_PREFIX, SEAT_PREFIX, PLANNAME_RE
    REPO, SV_ROOT, PROJ, SEAT_PREFIX = repo, sv_root, projects, seat_prefix
    WT_PREFIX = project_dir_name(repo / ".worktrees") + "-"
    PLANNAME_RE = plan_name_re(sv_root)


def iso(t: float | None) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else "-"


ts = release_map.ts


def kind_of_name(name: str) -> str:
    """計画名から種類を決める（計画の JSON に「フェーズ」が無いとき）。初期の名前（plan-<課題>）は実装の計画。"""
    k = next(
        (k for k in ("impl", "design", "spec", "check", "review", "release") if k in name),
        "impl" if re.fullmatch(r"plan-[\d-]+", name) else "不明",
    )
    return "design" if k == "spec" else k


# ---------- A / C: 記録の残る会話 ----------


def first_user_head(path: Path) -> str:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "user" and not d.get("isMeta"):
            return tu._text((d.get("message") or {}).get("content"))[:200]
    return ""


def kind_of(branch: str, head: str) -> str:
    h = head.lower()
    if "cross-review" in h:
        return "review"
    if "cross-refactoring" in h:
        return "refactoring"
    for pre, k in (
        ("design/", "design"),
        ("release/", "release"),
        ("check", "check"),
        ("feat", "impl"),
        ("fix/", "impl"),
        ("docs/", "impl"),
    ):
        if branch.startswith(pre):
            return k
    return "不明"


def conv_row(path: Path, until: float | None) -> dict | None:
    s = tu.scan_file(path, until=until)
    subs = [tu.scan_file(p, until=until) for p in sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))]
    u = tu.Usage()
    u.add(s.usage)
    for x in subs:
        u.add(x.usage)
    if not s.times or not u.calls:
        return None
    return {
        "start": min(s.times),
        "end": max(s.times),
        "active_sec": tu.active_seconds(s.times, tu.IDLE_CAP),
        "model": tu.most_common(s.models),
        "cwd": s.cwd,
        "calls": u.calls,
        "calls_main": s.usage.calls,
        "subagents": len(subs),
        "input": u.inp,
        "cache_read": u.read,
        "cache_write_5m": u.w5,
        "cache_write_1h": u.w1h,
        "output": u.out,
        "P": s.usage.p,
        "cost": round(u.cost, 1),
    }


def read_persisted(since: float, until: float | None, rm: release_map.ReleaseMap) -> tuple[list[dict], list[dict]]:
    wt, seats = [], []
    for d in sorted(PROJ.iterdir()):
        if "worktrees" not in d.name:
            continue
        for f in sorted(d.glob("*.jsonl")):
            r = conv_row(f, until)
            if not r or r["start"] < since:
                continue
            r["session"] = f.stem  # 帳簿の full の行と突き合わせる（出力には写さない）
            if d.name.startswith(WT_PREFIX):
                branch = r["cwd"].replace(str(REPO / ".worktrees") + "/", "") if r["cwd"] else d.name[len(WT_PREFIX) :]
                r.update(source="A", branch=branch, kind=kind_of(branch, first_user_head(f)))
                cands = rm.by_branch.get(branch, [])
                r["prs"] = [p["number"] for p in cands]
                wt.append(r)
            elif d.name.startswith(SEAT_PREFIX):
                key = d.name[len(SEAT_PREFIX) :]
                r.update(
                    source="C",
                    branch=key,
                    kind="review" if key.startswith("pr") else "refactoring",
                    prs=[int(key[2:])] if key.startswith("pr") and key[2:].isdigit() else [],
                )
                seats.append(r)
            else:  # 他のリポジトリの席は数えない（名前も残さない）
                continue
    return wt, seats


# ---------- B: 記録の残らない claude -p（フェーズの報告） ----------


def parse_report(text: str) -> list[dict]:
    out = []
    for block in text.split("## フェーズの報告")[1:]:
        m = USE_RE.search(block)
        rec = REC_RE.search(block)
        if not m or not rec:
            continue
        w = WORKER_RE.search(block)
        rows = ROW_RE.findall(block)
        pr = PR_RE.search(block)
        iss = ISSUE_RE.search(block)
        ph = PHASE_RE.search(block)
        out.append(
            {
                "record": rec.group(1).rstrip("`）)"),
                "input": int(m.group(1)),
                "cache_read": int(m.group(2)),
                "cache_write": int(m.group(3)),
                "output": int(m.group(4)),
                "usd": float(m.group(5)),
                "work": int(w.group(1)) if w else None,
                "judge": int(w.group(2)) if w else None,
                "turns": sum(int(r[1]) for r in rows),
                "llm_sec": sum(float(r[2] or 0) for r in rows),
                "pr_field": PR_NUM_RE.findall(pr.group(1).strip()) if pr else [],
                "issues": [int(x) for x in re.findall(r"#(\d+)", iss.group(1))] if iss else [],
                "phase_len": len(ph.group(1)) if ph else 0,
            }
        )
    return out


# ---------- B: 帳簿（記録の残らない claude -p） ----------


def read_ledger(root: Path, key: str) -> list[tuple[float, dict]]:
    """Claude の帳簿の `claude -p` の行（時刻・行）を時刻の順に。版 2 の `claude/` と版 1 の直下の両方を読む。
    サブエージェントの行（`kind=subagent`。#1953）と時刻を読めない行は捨てる。"""
    out = []
    for r in usage_ledger.read_usage_ledger("claude", root, repo=key):
        t = tu.parse_ts(r.get("at"))
        if t is not None and r.get("kind") != "subagent":
            out.append((t, r))
    return sorted(out, key=lambda x: x[0])


def plan_meta(plan_path: str) -> dict:
    """計画の JSON（無ければ空）。"""
    try:
        d = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _pr_numbers(value) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in _pr_numbers(v)]
    if isinstance(value, str):
        return [int(x) for x in PR_NUM_RE.findall(value.strip())]
    return []


def plan_prs(plan_path: str, meta: dict, rm: release_map.ReleaseMap) -> list[int]:
    """計画の PR: 計画の JSON の Pull Request → 状態ディレクトリの report.md → 計画の課題を題に持つマージ済みの PR。"""
    prs = _pr_numbers(meta.get("Pull Request"))
    if not prs:
        report = Path(plan_path).with_suffix("").parent / (Path(plan_path).stem + "-state") / "report.md"
        try:
            text = report.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for m in PR_RE.finditer(text):
            prs += _pr_numbers(m.group(1))
    prs = [n for n in dict.fromkeys(prs) if n in rm.prs]
    if prs:
        return prs
    return rm.merged_pr_of_issues(_issues(meta.get("課題")))


def _issues(value) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in _issues(v)]
    if isinstance(value, str):
        return [int(x) for x in re.findall(r"#?(\d+)", value)]
    return []


def kind_of_plan(meta: dict, name: str) -> str:
    phase = str(meta.get("フェーズ") or "")
    return next((k for head, k in PHASE_KIND if phase.startswith(head)), None) or kind_of_name(name)


def _empty_b(plan: str, source: str) -> dict:
    return {
        "source": source,
        "plan": plan,
        "calls": 0,
        "turns": 0,
        "llm_sec": 0.0,
        "full_calls": 0,
        "usd": 0.0,
        "models": Counter(),
        "run_versions": Counter(),
        "start": None,
        "at": None,
        "usage": tu.Usage(),
    }


def _add_row(b: dict, t: float, r: dict) -> None:
    b["start"] = t if b["start"] is None else min(b["start"], t)
    b["at"] = t if b["at"] is None else max(b["at"], t)
    b["run_versions"][r.get("ndf_version") or "-"] += 1


def _finish(b: dict) -> dict:
    u = b.pop("usage")
    b["model"] = tu.most_common(b.pop("models")) if b["calls"] else "-"
    b["run_version"] = tu.most_common(b.pop("run_versions"), "-")
    b.update(
        input=u.inp,
        cache_read=u.read,
        cache_write_5m=u.w5,
        cache_write_1h=u.w1h,
        cache_write=u.w5 + u.w1h,
        output=u.out,
        usd=round(b["usd"], 4),
        llm_sec=round(b["llm_sec"], 1),
        cost_low=round(u.cost, 1),
        cost_high=round(u.cost, 1),  # 帳簿はモデルと 5 分 / 1 時間の別を持つため下限と上限が同じ
    )
    return b


def _out_of_range(t: float, since: float, until: float | None) -> bool:
    return t < since or (until is not None and t > until)


def read_ledger_plans(
    rows: list[tuple[float, dict]], since: float, until: float | None, a_sessions: set[str], rm: release_map.ReleaseMap
) -> tuple[list[dict], dict, list[dict]]:
    """帳簿の行を計画ごとに足す。(計画の行, 計画の外の 1 まとまり, 計画の外の行ごとの時刻と換算) を返す。

    `full` の行は session_id の会話が A にあれば費用に足さず full_calls に数える（I1）。
    `plan` が空でない計画は、`full` だけの計画も 1 行になる（I3）。
    """
    plans: dict[str, dict] = {}
    outside = _empty_b(OUTSIDE, "ledger")
    outside_rows = []
    for t, r in rows:
        if _out_of_range(t, since, until):
            continue
        plan = r.get("plan") or ""
        b = plans.setdefault(plan, _empty_b(plan, "ledger")) if plan else outside
        _add_row(b, t, r)
        if r.get("kind") == "full" and r.get("session_id") in a_sessions:
            b["full_calls"] += 1
            continue
        u = tu.ledger_usage(r)
        b["usage"].add(u)
        b["calls"] += 1
        b["turns"] += int(r.get("turns") or 0)
        b["llm_sec"] += float(r.get("seconds") or 0)
        b["usd"] += float(r.get("cost_usd") or 0)
        if r.get("model"):
            b["models"][r["model"]] += 1
        if not plan:
            outside_rows.append({"at": t, "cost": u.cost})
    out = []
    for path, b in plans.items():
        meta = plan_meta(path)
        p = Path(path)
        name = f"{p.parent.name}/{p.stem}"  # 置き場の名前とファイル名（決定 6）
        b = _finish(b)
        b.update(
            plan=name,
            kind=kind_of_plan(meta, p.stem),
            issues=_issues(meta.get("課題")),
            pr_candidates=plan_prs(path, meta, rm),
        )
        out.append(b)
    return sorted(out, key=lambda b: b["at"]), _finish(outside), outside_rows


# ---------- B: 帳簿の無い期間（フェーズの報告） ----------


def _reports_from_files(since: float, until: float | None, found: dict[tuple, dict]) -> None:
    """残っている report.md（時刻はファイルの更新時刻）"""
    for p in [*SV_ROOT.glob("*/plans/*-state/report.md"), *SV_ROOT.glob("*/*-state/report.md")]:
        t = p.stat().st_mtime
        if _out_of_range(t, since, until):
            continue
        for r in parse_report(p.read_text(errors="replace")):
            r["at"] = t
            found.setdefault((r["record"], r["input"], r["cache_read"], r["output"]), r)


def _tool_result_texts(d: dict) -> list[str]:
    content = (d.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [tu._text(c.get("content")) for c in content if isinstance(c, dict) and c.get("type") == "tool_result"]


def _reports_from_transcripts(since: float, until: float | None, found: dict[tuple, dict]) -> None:
    """会話の tool_result に出た報告（conductor / サブエージェントが読んだもの）"""
    for f in PROJ.glob("**/*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        if "LLM の使用量" not in raw:
            continue
        for line in raw.split("\n"):
            if "LLM の使用量" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "user":
                continue
            t = tu.parse_ts(d.get("timestamp"))
            if t is None or _out_of_range(t, since, until):
                continue
            for tx in _tool_result_texts(d):
                for r in parse_report(tx):
                    k = (r["record"], r["input"], r["cache_read"], r["output"])
                    if k not in found or t < found[k]["at"]:
                        r["at"] = t
                        found[k] = r


def _report_cost(r: dict) -> tuple[float, float]:
    # 換算: モデルと 5 分/1 時間の別が無い。既定のモデル（claude-opus-5-5）の read 倍率と、書き込み 5 分（下限）
    low = round(
        r["input"]
        + r["cache_read"] * tu.read_rate("claude-opus-5-5")
        + r["cache_write"] * tu.WEIGHTS["w5"]
        + r["output"] * tu.WEIGHTS["out"],
        1,
    )
    return low, round(low + r["cache_write"] * (tu.WEIGHTS["w1h"] - tu.WEIGHTS["w5"]), 1)


def _annotate_report(r: dict) -> dict | None:
    m = PLANNAME_RE.search(r["record"])
    r["plan"] = f"{m.group(1)}/{m.group(2)}" if m else "不明"
    r["kind"] = kind_of_name(m.group(2) if m else "")
    if r["input"] + r["cache_read"] + r["cache_write"] + r["output"] == 0:
        return None  # LLM を使わなかった計画（run だけ）は数えない
    r["cost_low"], r["cost_high"] = _report_cost(r)
    r.update(source="report", model="不明", calls=None, cache_write_5m=None, cache_write_1h=None, full_calls=None, run_version="-")
    return r


def _last_per_record(rows: list[dict]) -> list[dict]:
    # 同じ記録で数値が伸びた報告（途中と最後）は最後の 1 件だけ残す
    best: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["cache_read"] + r["output"]):
        best[r["record"]] = r
    return sorted(best.values(), key=lambda r: r["at"])


def read_reports(since: float, until: float | None) -> list[dict]:
    found: dict[tuple, dict] = {}
    _reports_from_files(since, until, found)
    _reports_from_transcripts(since, until, found)
    annotated = [_annotate_report(r) for r in found.values()]
    return _last_per_record([r for r in annotated if r is not None])


# ---------- 寄せと集計 ----------


def assign(rows: list[dict], rm: release_map.ReleaseMap, pr_key) -> Counter:
    unassigned = Counter()
    for r in rows:
        pl = rm.place(pr_key(r), r.get("end") or r.get("at"))
        r["pr"], r["version"], r["by"] = pl.pr, pl.version, pl.by
        if pl.by == "time_only":
            unassigned[f"PR に寄せられない（{pl.reason}）→ 時刻で版だけ決めた"] += 1
            if not pl.version:
                unassigned["版も決まらない（最後の正式版より後）"] += 1
    return unassigned


def pr_of_report(rm: release_map.ReleaseMap):
    def f(r):
        if r["pr_field"]:
            return [int(x) for x in r["pr_field"] if int(x) in rm.prs]
        return rm.merged_pr_of_issues(r["issues"])

    return f


def versions_in_range(rm: release_map.ReleaseMap, since: float, until: float | None) -> list[tuple[str, bool]]:
    """範囲と重なる正式版と、範囲が版の途中から始まる（または途中で終わる）か。"""
    out = []
    for r in rm.versions:
        prev, end = rm.window(r.version)
        if end < since or (until is not None and prev is not None and prev > until):
            continue
        out.append((r.version, (prev is not None and since > prev) or (until is not None and until < end)))
    return out


def _a_in_b(r: dict, b_issues: set[int]) -> bool:
    """A の会話のブランチの課題が、報告から読んだ B の課題に入っているか。"""
    m = re.search(r"issue-(\d+)", r["branch"])
    return bool(m and int(m.group(1)) in b_issues)


def _total_cost(av: list[dict], bv: list[dict], b_out: float) -> float:
    """換算 A+B（B に含まれる A を除き、B 外を足す）。"""
    return sum(r["cost"] for r in av if not r["in_B"]) + sum(r["cost_low"] for r in bv) + b_out


def _per(rows: list[dict], key: str, scale: float = 1, ndigits: int | None = None) -> float | None:
    """rows の key の 1 行あたりの平均（scale で割って丸める）。rows が空なら None。"""
    return round(sum(r[key] for r in rows) / len(rows) / scale, ndigits) if rows else None


def _version_row(
    rm: release_map.ReleaseMap, v: str | None, partial: bool, a: list[dict], b: list[dict], c: list[dict], outside: list[dict]
) -> dict:
    av, bv, cv, ov = ([r for r in rs if r["version"] == v] for rs in (a, b, c, outside))
    seen = {r["pr"] for r in av + bv if r.get("pr")}
    release_prs = len(rm.prs_of(v)) if v else 0
    # 報告から読んだ B（帳簿の無い期間）には full のステップの会話（A）の使用量も足されている。
    # 同じ版で報告の B の課題に A のブランチの課題が入っていれば、A を B に含まれるものとして合計から外す。
    # 帳簿から読んだ B は full の行を session_id で除いてあるため、推定を使わない（I1）
    b_issues = {i for r in bv if r["source"] == "report" for i in r["issues"]}
    for r in av:
        r["in_B"] = _a_in_b(r, b_issues)
    b_out = sum(r["cost"] for r in ov)
    cost = _total_cost(av, bv, b_out)
    return {
        "version": v or "未リリース",
        "partial": partial,
        "A_convs": len(av),
        "B_plans": len(bv),
        "B_from_ledger": sum(r["source"] == "ledger" for r in bv),
        "release_prs": release_prs,
        "prs_seen": len(seen),
        "cost_A": round(sum(r["cost"] for r in av)),
        "cost_B_low": round(sum(r["cost_low"] for r in bv)),
        "cost_B_high": round(sum(r["cost_high"] for r in bv)),
        "B_outside": round(b_out),
        "cost_total_low": round(cost),
        "A_in_B": sum(r["in_B"] for r in av),
        "cost_per_pr": round(cost / release_prs) if release_prs else None,
        "A_P_per_conv": _per(av, "P"),
        "A_calls_per_conv": _per(av, "calls", ndigits=1),
        "A_min_per_conv": _per(av, "active_sec", scale=60, ndigits=1),
        "B_turns_per_plan": _per(bv, "turns", ndigits=1),
        "B_llm_min_per_plan": _per(bv, "llm_sec", scale=60, ndigits=1),
        "C_seats": len(cv),
        "C_cost": round(sum(r["cost"] for r in cv)),
        "C_P_per_seat": _per(cv, "P"),
        "C_calls_per_seat": _per(cv, "calls", ndigits=1),
        "kinds_A": dict(Counter(r["kind"] for r in av)),
        "kinds_B": dict(Counter(r["kind"] for r in bv)),
        "models_B": dict(Counter(r["model"] for r in bv)),
    }


def table(
    rm: release_map.ReleaseMap, a: list[dict], b: list[dict], c: list[dict], outside: list[dict], since: float, until: float | None
) -> list[dict]:
    vers = versions_in_range(rm, since, until)
    if any(r["version"] is None for r in a + b + c + outside):
        vers.append((None, True))
    return [_version_row(rm, v, partial, a, b, c, outside) for v, partial in vers]


def md(rows: list[dict], meta: dict) -> str:
    def f(x):
        return "-" if x is None else (f"{x:,}" if isinstance(x, int) else str(x))

    if meta["ledger_since"] is None:
        boundary = "帳簿が無い。B はすべてフェーズの報告から読んだ。"
    elif meta["report_until"]:
        boundary = f"帳簿の最も古い行は {meta['ledger_since']}。それより前の B はフェーズの報告から、後は帳簿から読んだ。"
    else:
        boundary = f"帳簿の最も古い行は {meta['ledger_since']}。範囲の B はすべて帳簿から読んだ。"
    out = [
        "# claude -p の消費（載った版ごと）",
        "",
        f"生成: {meta['generated']} / 範囲: {meta['since']} 〜 {meta['until'] or '最後まで'}",
        "",
        boundary,
        "",
        "換算は token-usage.py の係数（input 1 / cache read はモデル別 / cache write 5 分 1.25・1 時間 2 / output 5）。"
        "A = 記録の残る claude -p（.worktrees の会話）、B = 記録の残らない supervise.py の claude -p（帳簿の計画ごとの合計。"
        "報告から読んだ計画は換算の下限: opus-5-5 の read 倍率・書き込みを 5 分とみなす）、B 外 = 計画の外の claude -p（MVV の判定など）、"
        "C = レビュー・改修の claude の席（/tmp/ndf-worktrees）。"
        "換算 A+B は報告の B に含まれる A（full のステップの会話）を除き、B 外を足す。"
        "PR は版に載った PR の数（PR の一覧だけで決まる）、観測は A と B の行から寄った PR の数。版の途中から始まる・途中で終わる範囲は「途中」。"
        "所要は A の行の間隔（30 分で打ち切り）、B は秒の合計。",
        "",
        "| 版 | 途中 | A 会話（うち B に含む） | B 計画（帳簿） | PR | 観測 | 換算 A | 換算 B（下限〜上限） | 換算 B 外 | 換算 A+B | PR あたり | A の P/会話 | A の呼び出し/会話 | A の所要（分） | B の往復/計画 | B の LLM 所要（分） | C 席 | 換算 C | C の P/席 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        out.append(
            f"| {r['version']} | {'途中' if r['partial'] else '-'} | {r['A_convs']}（{r['A_in_B']}） | {r['B_plans']}（{r['B_from_ledger']}） | "
            f"{r['release_prs']} | {r['prs_seen']} | {f(r['cost_A'])} | "
            f"{f(r['cost_B_low'])}〜{f(r['cost_B_high'])} | {f(r['B_outside'])} | {f(r['cost_total_low'])} | {f(r['cost_per_pr'])} | "
            f"{f(r['A_P_per_conv'])} | {f(r['A_calls_per_conv'])} | {f(r['A_min_per_conv'])} | "
            f"{f(r['B_turns_per_plan'])} | {f(r['B_llm_min_per_plan'])} | {r['C_seats']} | {f(r['C_cost'])} | "
            f"{f(r['C_P_per_seat'])} |"
        )
    out += ["", "## 寄せられなかったもの", ""]
    for src, c in meta["unassigned"].items():
        for k, n in c.items():
            out.append(f"- {src}: {k} {n} 件")
    out += [
        "",
        "## 会話ごと（A）",
        "",
        "| ブランチ | 種類 | 開始 | 終了 | モデル | 呼び出し | input | cache read | write 5 分 | write 1 時間 | output | P | 換算 | PR | 載った版 | 寄せ方 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for r in meta["A"]:
        out.append(
            f"| {r['branch']} | {r['kind']} | {iso(r['start'])} | {iso(r['end'])} | {r['model']} | {r['calls']} | "
            f"{r['input']:,} | {r['cache_read']:,} | {r['cache_write_5m']:,} | {r['cache_write_1h']:,} | {r['output']:,} | "
            f"{r['P']:,} | {r['cost']:,} | {r.get('pr') or '-'} | {r['version'] or '-'} | {r['by']} |"
        )
    out += [
        "",
        "## 計画ごと（B）",
        "",
        "出所 ledger は帳簿、report はフェーズの報告。full は A で数えた Skill を回すステップの呼び出しの数。",
        "",
        "| 出所 | 計画 | 種類 | 開始 | 終了 | モデル | 呼び出し | 往復 | full | input | cache read | write 5 分 | write 1 時間 | output | 換算 | 動いた版 | PR | 載った版 | 寄せ方 |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    for r in meta["B"] + ([meta["B_outside"]] if meta["B_outside"]["calls"] else []):
        ledger = r["source"] == "ledger"
        out.append(
            f"| {r['source']} | {r['plan']} | {r.get('kind', '-')} | {iso(r.get('start'))} | {iso(r['at'])} | {r['model']} | "
            f"{f(r['calls'])} | {r['turns']} | {f(r['full_calls'])} | {r['input']:,} | {r['cache_read']:,} | "
            f"{f(r['cache_write_5m']) if ledger else '合計 ' + f(r['cache_write'])} | {f(r['cache_write_1h'])} | {r['output']:,} | "
            f"{r['cost_low']:,} | {r['run_version']} | {r.get('pr') or '-'} | {r.get('version') or '-'} | {r.get('by', '-')} |"
        )
    return "\n".join(out) + "\n"


# ---------- 物差しの測り直し: launches（U2）・plan-cost（U1 の計画起動分） ----------
# 旧い帳簿・会話の置き場・PR の一覧（gh）・ndf を読まない。読むのはプランの進捗の記録・使用量ログ・会話の ID の一覧だけ

LAUNCH_TYPES = ("work", "judge")  # progress.jsonl の kind: step の type のうち、claude -p を起動するもの
CLI_CALLER = "claude-p"  # 使用量ログの caller（ndf.domains.records.usage.CLAUDE_CLI と同じ値。import しない）
SUBCOMMANDS = ("launches", "plan-cost", "measure")  # measure は両方を 1 回の収集で出す


def parse_aware(text) -> datetime | None:
    """時差つきの ISO 8601 を読む（末尾の Z も受ける）。読めないか時差が無ければ None。"""
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _in_range(row: dict, field: str, since: datetime, until: datetime) -> bool:
    t = parse_aware(row.get(field))
    return t is not None and since <= t < until


def _jsonl_rows(path: Path) -> Iterator[dict]:
    """辞書の行だけを 1 行ずつ読む（壊れた行は読み飛ばす。ファイル全体を持たない）。"""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                yield d


def default_usage_log(env=None) -> Path:
    """使用量ログの既定のパス（ndf.domains.records.usage.usage_log_path と同じ規則を写す。import しない）。

    `NDF_USAGE_DIR` があれば `<それ>/usage.jsonl`、無ければ `<状態の根>/ndf/usage/usage.jsonl`。
    状態の根は `NDF_STATE_HOME`・`XDG_STATE_HOME`・`~/.local/state` の順（ndf.identity._state_home）。
    """
    env = os.environ if env is None else env
    if env.get("NDF_USAGE_DIR"):
        return Path(env["NDF_USAGE_DIR"]) / "usage.jsonl"
    home = next((Path(env[k]).expanduser() for k in ("NDF_STATE_HOME", "XDG_STATE_HOME") if env.get(k)), Path.home() / ".local" / "state")
    return home / "ndf" / "usage" / "usage.jsonl"


def default_state_root(cwd: Path | None = None) -> Path:
    """カレントのリポジトリ（.git を持つ最も近い祖先）の .ndf。見つからなければカレントの .ndf。"""
    here = (cwd or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d / ".ndf"
    return here / ".ndf"


KEY_FIELDS = ("launch_id",)  # 起動の鍵（progress.jsonl の kind: launch と使用量ログの行が同じ値を持つ。起動ごとに一意）
LAUNCH_LOG_VERSION = 1  # 起動の記録に対応した実行が、実行の頭の kind: run の行に書く `launches` の値
CHOSEN_EVENT = "route_chosen"  # 選択の記録（ndf.contracts.chosen.EVENT と同じ値。import しない）


def _key(row: dict) -> tuple[str, ...] | None:
    ids = [row.get(k) for k in KEY_FIELDS]
    return tuple(ids) if all(isinstance(v, str) and v for v in ids) else None


def _activity(row: dict) -> bool:
    """起動の記録に対応していない実行で、claude -p を起動したかもしれない行か（ステップの work・judge・選択の記録・起動）。"""
    kind = row.get("kind")
    if kind == "step":
        return row.get("type") in LAUNCH_TYPES
    return kind == "launch" or row.get("event") == CHOSEN_EVENT


def _launch_keys(path: Path, since: datetime, until: datetime) -> tuple[set[tuple[str, ...]], int, int, set[str]]:
    """1 本の progress.jsonl から、期間の内の起動の鍵（launch_id）と、起動の記録に対応していない実行の
    期間の内の行の数と、識別できない起動の行の数と、初回から起動の記録に対応した回の `run_id`（期間を問わない）を返す。

    起動の母数は、`claude -p` を 1 回起動するごとに書く `kind: launch` の行（`runtime: claude` で、`launch_id` を
    持つ）だけから作る。ステップの行（`kind: step`）・選択の記録（`event: route_chosen`）は、他のランタイムを指定した
    `work` が `claude -p` を起動しないこと・full の再開で同じステップの中で複数回起動することを区別できず、PR の判断や
    遅れの見張りからの起動も持たないため、母数にしない。

    起動の記録に対応した実行は、実行の頭に `launches`（`LAUNCH_LOG_VERSION`）を持つ `kind: run` の行を書く（書き手の
    契約）。実行は `kind: run` の行で区切り、ファイルの頭から最初の `kind: run` の行までも 1 つの実行とみなす。
    `launches` を持たない実行（11.0.13 の runner は `kind: run` も `kind: launch` も書かず `event: route_chosen` だけを
    書く）の、期間の内の `work`・`judge` のステップの行・選択の記録・起動の行は「対応していない実行の行」に数える
    （呼び手がその期間を計測不能にする。書き手の導入をまたぐ期間で、導入前の起動を母数から黙って落とさない）。
    返す `run_id` は、回の耐久ワークフローを作った実行（`resumed: false`）が宣言した回だけである。

    対応した実行の `kind: launch` の行のうち、`runtime` が無い・鍵が欠けている・時刻を読めない行は、claude の起動か・
    期間の内か・どの起動かを決められないので「識別できない起動の行」に数える（母数から黙って落とさない。期間の外と
    分かる行と、claude 以外のランタイムの行は数えない）。
    """
    keys: set[tuple[str, ...]] = set()
    unknown = bad = 0
    declared = False  # 今の実行が起動の記録に対応しているか
    runs: set[str] = set()

    for row in _jsonl_rows(path):
        kind = row.get("kind")
        if kind == "run":
            declared = row.get("launches") == LAUNCH_LOG_VERSION
            # 回の最初の実行（`resumed: false`）から対応した回だけを、旧い回の検出（_legacy_runs）から外す。旧版で
            # 起動して対応した版で再開した回の宣言は `resumed: true` で、旧版での起動を記録済みとして扱わない
            if declared and row.get("resumed") is False and isinstance(row.get("run_id"), str) and row["run_id"]:
                runs.add(row["run_id"])
        elif not declared:
            if _activity(row) and (_in_range(row, "at", since, until) or parse_aware(row.get("at")) is None):
                unknown += 1
        elif kind == "launch":
            runtime = row.get("runtime")
            if isinstance(runtime, str) and runtime and runtime != "claude":
                continue
            t = parse_aware(row.get("at"))
            if t is not None and not since <= t < until:
                continue
            key = _key(row)
            if runtime != "claude" or key is None or t is None:
                bad += 1
            else:
                keys.add(key)
    return keys, unknown, bad, runs


RUN_FILE = "run.json"  # 実行の回の記録（ndf.core.plan.runner.RUN_FILE と同じ値。import しない）
RESULTS_DIR = "results"  # 終わった回の結果の置き場（runner.RESULTS_DIR。`<run_id>.json`）
RUN_PREFIX = "run-"  # 回の名前の接頭辞（ndf.core.durable.RUN_PREFIX。名前は接頭辞と回のキーの sha256 の先頭 32 桁）
PROGRESS_FILE = "progress.jsonl"  # 進捗の記録（runner.PROGRESS_FILE）
RUN_RESULT = re.compile(re.escape(RUN_PREFIX) + r"[0-9a-f]{32}\.json")  # runner が書く結果のファイル名（`<run_id>.json`）


def run_name(key: str) -> str:
    """回のキーから回の名前（`run_id`。ndf.core.durable.run_name と同じ規則を写す）。"""
    return RUN_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:32]


def _modified_since(path: Path, since: datetime) -> bool:
    try:
        return path.stat().st_mtime >= since.timestamp()
    except OSError:
        return False


def _run_results(state_dir: Path) -> list[Path]:
    """`results/` の下の、runner が書く形の名前（`run-` と 32 桁の 16 進）の結果ファイル。別用途の JSON は採らない。"""
    try:
        return sorted(f for f in (state_dir / RESULTS_DIR).iterdir() if RUN_RESULT.fullmatch(f.name) and f.is_file())
    except OSError:
        return []


def _run_record(state_dir: Path) -> dict | None:
    """`run.json` が回の記録の形（文字列の `key` を持つ JSON のオブジェクト）なら、その中身。ほかは None。"""
    try:
        record = json.loads((state_dir / RUN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("key"), str) and record["key"] else None


def _legacy_runs(state_dir: Path, declared: set[str], since: datetime) -> list[str]:
    """状態の保存先に残った、初回から起動の記録に対応していない回（期間の始まり以後に動いたかもしれないもの）。

    runner はどの版も、回を進めるたびに `run.json`（回のキー・HEAD の並び）を書き、回が終わると
    `results/<run_id>.json` を書く。旧い runner は全ステップが正常に終わり停止も承認の回答も無いと `progress.jsonl` に
    何も書かない（空か無い）ので、進捗の行だけでは旧い実行を見落とす。そこで、`resumed: false` の `kind: run` の
    `run_id`（`declared`）に無い回の `results/<run_id>.json` と `run.json` を挙げる（旧版で始めて対応した版で再開した
    回は `resumed: true` の宣言しか持たないので挙げる）。ファイルの更新時刻が期間の始まりより前なら、その回は期間の前に
    動き終えているので挙げない。期間の終わりより後の更新も挙げる（旧い回は開始の時刻を残さないので、期間の内に始まり
    後で終わった回と期間の後に始まった回を分けられない。`run.json` の `heads` は開始時に HEAD が無ければ空で、終了時の
    HEAD が先頭になり得るうえ、コミットの時刻は設定できるので、開始の時刻の下限にしない）。

    `run.json` は回の記録の形（`_run_record`）のものだけを読む。形の違う `run.json` は、同じディレクトリに
    `progress.jsonl` か結果のファイルがある（状態の保存先と分かる）ときだけ、壊れた回の記録として挙げる。"""
    runs: set[str] = set()
    results = _run_results(state_dir)
    runs |= {f.stem for f in results if f.stem not in declared and _modified_since(f, since)}
    run_file = state_dir / RUN_FILE
    if run_file.is_file() and _modified_since(run_file, since):
        record = _run_record(state_dir)
        if record is not None:
            if (name := run_name(record["key"])) not in declared:
                runs.add(name)
        elif results or (state_dir / PROGRESS_FILE).is_file():
            runs.add(str(run_file))
    return sorted(runs)


def _state_dirs(root: Path) -> Iterator[Path]:
    """`root` の下で、状態の保存先の形を持つディレクトリ: `progress.jsonl` を持つか、回の記録の形の `run.json`
    （`_run_record`）を持つか、`results/` に runner の形の名前の結果ファイル（`_run_results`）を持つもの。
    名前だけが同じ無関係な `run.json`・`results/` は採らない。

    `root` の下の別のリポジトリ（`.git` を持つディレクトリ。別の clone・サブモジュール・作業ツリー）へは入らない
    （`root` の帰属は `root` の origin でしか確かめていないので、境界の先の記録を対象リポジトリのものとして読まない。
    境界の先にあった対象リポジトリの起動は、使用量ログの「母数に無い起動の行」で計測不能になる）。"""
    for d, subdirs, files in os.walk(root):
        here = Path(d)
        subdirs[:] = sorted(s for s in subdirs if not (here / s / ".git").exists())
        if (
            PROGRESS_FILE in files
            or (RUN_FILE in files and _run_record(here) is not None)
            or (RESULTS_DIR in subdirs and _run_results(here))
        ):
            yield here


GITHUB_ORIGIN = re.compile(
    r"(?:https?://(?:[^@/]+@)?github\.com/|ssh://(?:[^@/]+@)?github\.com(?::\d+)?/|[^@/:]+@github\.com:)"
    r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?",
    re.IGNORECASE,
)


def github_slug(url: str) -> str | None:
    """origin の URL が GitHub のリポジトリを指せば `<所有者>/<リポジトリ>`、ほかは None。
    HTTPS（`https://github.com/o/r.git`）と SSH（`git@github.com:o/r.git`・`ssh://git@github.com/o/r`）を扱い、
    別のホスト（`https://gitlab.com/o/r.git` など）は末尾が同じでも None にする。"""
    m = GITHUB_ORIGIN.fullmatch(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def state_root_repo(root: Path) -> str | None:
    """状態ディレクトリが属する GitHub のリポジトリの `<所有者>/<リポジトリ>`（そのディレクトリを含むリポジトリの
    origin から。origin が無い・GitHub を指さないなど決められなければ None）。

    URL は `git remote get-url` で読み、`url.<base>.insteadOf` を展開した後の接続先で判定する（設定値そのままでは
    `gh:o/r.git` のような短縮形を GitHub と認めず、GitHub の URL を別ホストへ書き換える設定も見落とす）。"""
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=usage_ledger.GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return github_slug(res.stdout)


def collect_launches(state_roots: list[Path], since: datetime, until: datetime, repo: str) -> dict:
    """U2 と U1 の計画起動分の母数: 状態ディレクトリの下の progress.jsonl から、期間の内の起動の鍵を集める。

    `kind: launch` の行は `repo` を持たないので、母数の帰属は状態ディレクトリで確かめる。既定のもの（カレントの
    リポジトリの .ndf）も `--state` で渡したものも、そのディレクトリを含むリポジトリの origin が対象リポジトリ
    （`repo`。大文字と小文字は問わない）でなければ読まずに `foreign` に挙げる（呼び手がその期間を計測不能にする。
    別のリポジトリの起動を母数にしない）。根の下の別のリポジトリへは入らない（`_state_dirs`）。無いディレクトリは
    起動を持たないものとして飛ばす。進捗の行を残さない旧い実行の回は `legacy` に数える（`_legacy_runs`。旧い回は
    開始の時刻を残さないので、期間の後に始まった回かを確かめられず、外さない）。"""
    launched: set[tuple[str, ...]] = set()
    unidentified = bad = legacy = 0
    foreign: list[str] = []
    read: set[Path] = set()  # 重なる根（リポジトリの根とその下の <プラン>.state など）で同じディレクトリを 2 度数えない
    for root in state_roots:
        if not root.is_dir():
            continue
        owner = state_root_repo(root)
        if owner is None or owner.lower() != repo.lower():
            foreign.append(f"{root}（{owner or '属するリポジトリを決められない'}）")
            continue
        for d in _state_dirs(root):
            if d.resolve() in read:
                continue
            read.add(d.resolve())
            declared: set[str] = set()
            f = d / PROGRESS_FILE
            if f.is_file():
                keys, unknown, b, declared = _launch_keys(f, since, until)
                launched |= keys
                unidentified += unknown
                bad += b
            legacy += len(_legacy_runs(d, declared, since))
    return {"launched": launched, "unidentified": unidentified, "bad": bad, "legacy": legacy, "foreign": foreign}


def _launch_reason(ls: dict, repo: str) -> str | None:
    """母数を確定できない理由（無ければ None）。launches と plan-cost が同じ理由を返す。"""
    if ls["foreign"]:
        return f"状態ディレクトリが対象リポジトリ（{repo}）に属すると確かめられない（{'・'.join(ls['foreign'])}）"
    if ls["unidentified"]:
        return (
            f"起動の記録に対応していない実行がある（頭の kind: run に launches: {LAUNCH_LOG_VERSION} を持たない実行の、"
            f"work・judge のステップ・選択の記録（event: {CHOSEN_EVENT}）・起動の行が {ls['unidentified']} 行。"
            "書き手が入る前の実行で、実際に claude -p を起動したかと試行の数が分からない）"
        )
    if ls["legacy"]:
        return (
            f"起動の記録に対応していない実行の回がある（状態の保存先の {RUN_FILE}・{RESULTS_DIR}/ に、初回から launches: "
            f"{LAUNCH_LOG_VERSION} の kind: run（resumed: false）を持たない回が {ls['legacy']} 件、期間の始まり以後に"
            "更新され、開始の時刻が残っていない。書き手が入る前の実行で、進捗の行を残さずに claude -p を"
            "起動したかもしれない）"
        )
    if ls["bad"]:
        return f"識別できない起動の行がある（kind: launch の行のうち runtime・鍵（launch_id）・時刻の欠けた行が {ls['bad']} 行）"
    if not ls["launched"]:
        return "起動の母数が無い（期間の内に claude -p の起動の行（kind: launch）が無い）"
    return None


def _maybe_in_period(row: dict, since: datetime, until: datetime) -> bool:
    """鍵で照合できない使用量の行が、期間の起動の分であり得るか。書き手の契約の `launched_at`（起動の時刻）を
    読めればそれが `[since, until)` の内か、読めなければ記録の時刻 `at` が `since` 以降か読めないか
    （起動の後に記録されるので、`since` より前の記録は期間の起動の分でない）。"""
    t = parse_aware(row.get("launched_at"))
    if t is not None:
        return since <= t < until
    t = parse_aware(row.get("at"))
    return t is None or t >= since


def _launch_time_in_range(row: dict, since: datetime, until: datetime) -> bool:
    """照合できなかった行が期間の内の起動の分か。`launched_at` を読めればその時刻だけで決め（期間の前に起動して
    期間の内に記録された行を入れない）、読めなければ記録の時刻 `at` で決める。"""
    t = parse_aware(row.get("launched_at"))
    if t is None:
        t = parse_aware(row.get("at"))
    return t is not None and since <= t < until


def match_usage(usage_log: Path, repo: str, launched: set, since: datetime, until: datetime) -> dict | None:
    """使用量ログを 1 行ずつ読み、caller: claude-p の行を母数の鍵で照合する。使用量ログが無ければ None。

    持つのは照合できた行（期間の起動の分。鍵ごとに 1 行で、使用量を読めた行を優先）と、診断の件数だけ
    （ログの全期間・全リポジトリの行を持たない）。使用量ログは利用者全体で 1 本なので、`repo` が別のリポジトリの
    行は照合しない。件数は次のとおり（どれも期間の起動の分であり得る行だけを数える）:

    - `other_repo`: 別のリポジトリの行
    - `keyless`: 鍵を持たない行（どの起動の分か決められない）
    - `orphan`: 対象リポジトリの鍵付きの行で、`launched_at` が期間の内なのに母数に無いもの（読んだ状態ディレクトリが足りない）
    - `seen`: 対象リポジトリか `repo` を持たない行（照合できた行・`keyless`・`orphan` と、期間の内と分かる残りの行）
    """
    if not usage_log.is_file():
        return None
    target = repo.lower()
    matched: dict[tuple[str, ...], dict] = {}
    other = keyless = orphan = seen = 0
    for r in _jsonl_rows(usage_log):
        if r.get("caller") != CLI_CALLER:
            continue
        owner = r.get("repo")
        has_owner = isinstance(owner, str) and bool(owner)
        if has_owner and owner.lower() != target:
            other += _maybe_in_period(r, since, until)
            continue
        key = _key(r)
        if key is not None and key in launched:
            seen += 1
            if key not in matched or (not isinstance(matched[key].get("usage"), dict) and isinstance(r.get("usage"), dict)):
                matched[key] = r
        elif key is None:
            if _maybe_in_period(r, since, until):
                seen += 1
                keyless += 1
        elif has_owner and _in_range(r, "launched_at", since, until):
            seen += 1
            orphan += 1
        elif _launch_time_in_range(r, since, until):
            seen += 1
    return {"matched": matched, "other_repo": other, "keyless": keyless, "orphan": orphan, "seen": seen}


def _collect_and_match(state_roots: list[Path], usage_log: Path, since: datetime, until: datetime, repo: str) -> tuple[dict, dict | None]:
    """母数の収集（collect_launches）と使用量ログの照合（match_usage）を 1 回ずつ行い、(ls, m) を返す。"""
    ls = collect_launches(state_roots, since, until, repo)
    return ls, match_usage(usage_log, repo, ls["launched"], since, until)


def count_launches(state_roots: list[Path], usage_log: Path, since: datetime, until: datetime, repo: str) -> dict:
    """U2: 使用量の行が残った起動の数 ÷ 計画が起動した claude -p の数。同じ鍵で突き合わせる。

    母数は対象リポジトリに属する状態ディレクトリの、起動の時刻（`kind: launch` の `at`）で期間に絞った起動で、
    使用量ログの行はその鍵で照合する（行の記録の時刻では絞らない。終わりの直前に起動して終わりの後に記録された
    起動を、未記録に数えない）。鍵の無い行のうち期間の起動の分であり得るもの（`launched_at` が期間の内か、それが
    読めず記録の時刻が始まり以降か読めない行で、`repo` が対象か無い行）が 1 行でもあれば、どの起動の分か決められない
    ので計測不能にする。母数に無い対象リポジトリの起動（`launched_at` が期間の内）があるときも、母数が欠けているので
    計測不能にする。
    """
    ls, m = _collect_and_match(state_roots, usage_log, since, until, repo)
    return _u2(ls, m, usage_log, repo)


def _u2(ls: dict, m: dict | None, usage_log: Path, repo: str) -> dict:
    """集めた母数（collect_launches）と照合の結果（match_usage）から U2 を出す。"""
    launched = ls["launched"]
    recorded = {k for k, r in (m["matched"] if m else {}).items() if isinstance(r.get("usage"), dict)}
    reason = _launch_reason(ls, repo)
    if reason is None:
        if m is None:
            reason = f"使用量ログが無い（{usage_log}）"
        elif not m["seen"]:
            reason = "使用量ログに対象リポジトリの caller: claude-p の行が無い（書き手が無い）"
        elif m["keyless"]:
            reason = f"使用量ログに突き合わせの鍵（launch_id）を持たない行が {m['keyless']} 行あり、どの起動の分か決められない"
        elif m["orphan"]:
            reason = f"母数に無い期間の内の起動の使用量の行が {m['orphan']} 行ある（読んだ状態ディレクトリに無い起動。--state が足りない）"
    value = None if reason else len(recorded) / len(launched)
    return {
        "u2": {
            "value": value,
            "launched": len(launched),
            "recorded": len(recorded),
            "keyless": m["keyless"] if m else 0,
            "other_repo": m["other_repo"] if m else 0,
            "orphan": m["orphan"] if m else 0,
            "unidentified_steps": ls["unidentified"],
            "legacy_runs": ls["legacy"],
            "unidentified_launches": ls["bad"],
            "foreign_state": ls["foreign"],
            "reason": reason,
        }
    }


TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def model_usage_of(row: dict) -> dict[str, dict] | None:
    """行の `model_usage`（書き手の契約: `claude -p` の結果の `modelUsage` を、モデルの名前 → そのモデルの
    `TOKEN_FIELDS` の数に写したもの）。補助のモデルの分も入る。1 つも読めなければ None。"""
    mu = row.get("model_usage")
    if not isinstance(mu, dict):
        return None
    got = {m: u for m, u in mu.items() if isinstance(m, str) and m and isinstance(u, dict)}
    return got or None


def row_cost(row: dict) -> float:
    """使用量ログの 1 行の換算の費用（token-usage.py と同じ係数）。

    行の使用量は 5 分と 1 時間の書き込みを分けないので、書き込みは 5 分の重みで数える。
    cache read の倍率はモデルで違う（4 倍まで）ので、`model_usage` のモデルごとに、そのモデルの倍率で引いて足す
    （1 回の起動が主のモデルと補助のモデルを併用しても、合計の読み取りに主のモデルの倍率を掛けない。会話側の
    token-usage.py も応答ごとのモデルで引くので、期間の終わりをまたいだ起動の残りにも倍率の差が混ざらない）。
    `model_usage` の無い行は `_plan_cost` が足さずに `complete: false` にするので、ここで 0 にした値は U1 に入らない。
    """
    w = token_rates.WEIGHTS
    total = 0.0
    for model, u in (model_usage_of(row) or {}).items():
        n = {k: u.get(k) if isinstance(u.get(k), int) else 0 for k in TOKEN_FIELDS}
        total += (
            w["inp"] * n["input_tokens"]
            + w["w5"] * n["cache_creation_input_tokens"]
            + w["out"] * n["output_tokens"]
            + token_rates.read_rate(model) * n["cache_read_input_tokens"]
        )
    return total


def plan_cost(
    state_roots: list[Path], usage_log: Path, counted_sessions: dict[str, float], since: datetime, until: datetime, repo: str
) -> dict:
    """U1 の計画起動分: 対象リポジトリの計画が期間の内に起動した claude -p（`launches` と同じ母数）のうち、
    token-usage.py が U1 の分子に入れていない分の換算の費用の和。

    `counted_sessions` は token-usage.py `--sessions-out` の一覧（会話の ID → その会話から U1 の分子に入れた期間の内の
    費用。書き込みを 5 分の重みで数えた `row_cost` と同じ換算）。足すのは、母数の鍵で照合できた使用量ログの行のうち、
    `repo` が対象リポジトリで、`model_usage`（モデル別の使用量）・usage を読めた行で、`session_id` が一覧に無い行の費用と、一覧にある会話のうち
    起動が期間の終わりをまたいだ（記録の時刻 `at` が `until` 以後か読めない）会話の、起動全体の費用から会話側で
    計上した費用を引いた残り（負なら 0）。期間の内に記録を終えた一覧の会話は、会話側が全体を数えたので足さない。

    `complete: true` は、母数を確定でき（`launches` と同じ条件）、母数のすべての起動に使用量の行があり、期間の起動の
    分であり得る鍵の無い行・母数に無い起動の行が無く、照合できた行がすべて `repo`・`session_id`・`model_usage`・usage を
    持つときだけ（使用量の書き手が入る前後をまたぐ期間では、書き手の入る前の実行が母数を確定できないので
    `complete: false` になる）。それ以外は足せた分を `value` に、欠けた入力を `reason` に返す。"""
    ls, m = _collect_and_match(state_roots, usage_log, since, until, repo)
    return _plan_cost(ls, m, usage_log, counted_sessions, repo, until)


def _crosses_until(row: dict, until: datetime) -> bool:
    """起動の記録の時刻が期間の終わり以後（読めなければ以後とみなす）。会話の一部が期間の外にあり得る。"""
    t = parse_aware(row.get("at"))
    return t is None or t >= until


def _plan_cost(ls: dict, m: dict | None, usage_log: Path, counted_sessions: dict[str, float], repo: str, until: datetime) -> dict:
    """集めた母数（collect_launches）と照合の結果（match_usage）から U1 の計画起動分を出す。"""
    reasons = [r] if (r := _launch_reason(ls, repo)) else []
    matched = m["matched"] if m else {}
    missing = len(ls["launched"]) - len(matched)
    if m is None:
        reasons.append(f"使用量ログが無い（{usage_log}）")
    else:
        if missing:
            reasons.append(f"使用量の行が無い起動が {missing} 件（書き手が入る前の起動か記録の漏れ。その分を足していない）")
        if m["keyless"]:
            reasons.append(f"突き合わせの鍵（launch_id）を持たない行が {m['keyless']} 行（足していない）")
        if m["orphan"]:
            reasons.append(f"母数に無い期間の内の起動の行が {m['orphan']} 行（読んだ状態ディレクトリに無い起動。足していない）")
    total, added, skipped = 0.0, 0, 0
    no_sid = unknown = no_repo = no_model = 0
    counted_rows: dict[str, list[dict]] = {}  # 一覧にある会話の起動の行
    for r in matched.values():
        sid = r.get("session_id")
        if not isinstance(r.get("repo"), str) or not r.get("repo"):
            no_repo += 1
        elif not isinstance(sid, str) or not sid:
            no_sid += 1
        elif not isinstance(r.get("usage"), dict):
            unknown += 1  # 会話の ID の一覧にあっても、使用量を読めない起動は下限として扱う（加算の除外より先に見る）
        elif model_usage_of(r) is None:
            no_model += 1  # cache read の倍率がモデルで 4 倍まで違い、補助のモデルも併用するので、1 つの倍率で足さない
        elif sid in counted_sessions:
            counted_rows.setdefault(sid, []).append(r)
        else:
            total += row_cost(r)
            added += 1
    boundary = 0
    for sid, rows in counted_rows.items():
        if any(_crosses_until(r, until) for r in rows):
            total += max(0.0, sum(row_cost(r) for r in rows) - counted_sessions[sid])
            boundary += len(rows)
        else:
            skipped += len(rows)
    for n, text in (
        (no_repo, f"repo が無くリポジトリへの帰属を確かめられない行が {no_repo} 行（足していない）"),
        (no_sid, f"session_id の無い行が {no_sid} 行（足していない）"),
        (unknown, f"usage が unknown の行が {unknown} 行（足していない）"),
        (no_model, f"model_usage（モデル別の使用量）の無い行が {no_model} 行（cache read の倍率を決められない。足していない）"),
    ):
        if n:
            reasons.append(text)
    return {
        "plan_cost": {
            "value": round(total, 2),
            "rows": added,
            "skipped_counted": skipped,
            "boundary_counted": boundary,
            "launched": len(ls["launched"]),
            "missing": missing if m else len(ls["launched"]),
            "other_repo": m["other_repo"] if m else 0,
            "complete": not reasons,
            "reason": "・".join(reasons) or None,
        }
    }


def measure_both(
    state_roots: list[Path], usage_log: Path, counted_sessions: dict[str, float], since: datetime, until: datetime, repo: str
) -> dict:
    """副命令 measure: U2 と U1 の計画起動分を、母数の収集（状態ディレクトリの帰属を確かめる git の起動と
    progress.jsonl の探索・読み取り）と使用量ログの照合を 1 回だけ行って出す。値は launches・plan-cost と同じ。"""
    ls, m = _collect_and_match(state_roots, usage_log, since, until, repo)
    return {**_u2(ls, m, usage_log, repo), **_plan_cost(ls, m, usage_log, counted_sessions, repo, until)}


def parse_counted(text: str) -> dict[str, float] | None:
    """`token-usage.py --sessions-out` の一覧（1 行に `<会話の ID>\t<U1 の分子に入れた費用>`）を読む。形が違えば None。"""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        sid, sep, cost = line.strip().partition("\t")
        try:
            value = float(cost)
        except ValueError:
            return None
        if not sep or not sid:
            return None
        out[sid] = out.get(sid, 0.0) + value
    return out


def _aware_arg(text: str) -> datetime:
    t = parse_aware(text)
    if t is None:
        raise argparse.ArgumentTypeError(f"時差つきの ISO 8601 で渡す（例 2026-11-18T00:00:00+09:00）: {text}")
    return t


def _slug_arg(text: str) -> str:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        raise argparse.ArgumentTypeError(f"`<所有者>/<リポジトリ>` の形で渡す（例 devbasex/ai-plugins）: {text}")
    return text


def measure_main(argv: list[str]) -> int:
    """副命令 launches・plan-cost・measure（両方）。JSON 1 つを標準出力へ書く。"""
    ap = argparse.ArgumentParser(prog="claude-p-usage.py " + argv[0], description="物差しの測り直し（U2・U1 の計画起動分）")
    ap.add_argument("--since", type=_aware_arg, required=True, help="期間の始まり（含む。時差つきの ISO 8601）")
    ap.add_argument("--until", type=_aware_arg, required=True, help="期間の終わり（含まない。時差つきの ISO 8601）")
    ap.add_argument("--usage-log", type=Path, default=None, help="使用量ログ（既定は ndf records usage の置き場の usage.jsonl）")
    ap.add_argument(
        "--state",
        type=Path,
        action="append",
        help="progress.jsonl を探す根（繰り返せる。期間に動いたプランの状態の保存先（--state-dir の値か、渡さない実行は"
        "プランのファイルの隣の <プラン>.state）をすべて渡す。省くとカレントのリポジトリの .ndf だけ。どれも対象リポジトリに属すること）",
    )
    if argv[0] != "launches":
        ap.add_argument("--counted", type=Path, required=True, help="token-usage.py --sessions-out が書いた会話の ID と計上した費用の一覧")
    ap.add_argument(
        "--repo", type=_slug_arg, required=True, help="対象リポジトリの `<所有者>/<リポジトリ>`（使用量ログの行の repo で絞る）"
    )
    a = ap.parse_args(argv[1:])
    usage_log = a.usage_log or default_usage_log()
    state_roots = a.state or [default_state_root()]
    if argv[0] == "launches":
        out = count_launches(state_roots, usage_log, a.since, a.until, a.repo)
    else:
        try:
            text = a.counted.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"会話の ID の一覧を読めない: {a.counted}: {exc}", file=sys.stderr)
            return 2
        counted = parse_counted(text)
        if counted is None:
            print(
                f"会話の ID の一覧の形が違う（1 行に `<会話の ID><タブ><費用>`。token-usage.py --sessions-out の出力）: {a.counted}",
                file=sys.stderr,
            )
            return 2
        measure = measure_both if argv[0] == "measure" else plan_cost
        out = measure(state_roots, usage_log, counted, a.since, a.until, a.repo)
    print(json.dumps({"since": a.since.isoformat(), "until": a.until.isoformat(), **out}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in SUBCOMMANDS:
        return measure_main(argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", help="範囲の始まり（ISO 8601。既定は帳簿の最も古い行の時刻）")
    ap.add_argument("--until")
    ap.add_argument("--repo", type=Path, default=REPO, help="タグ・CHANGELOG・gh・帳簿のファイル名を決めるリポジトリ（既定 %(default)s）")
    ap.add_argument(
        "--plugin",
        default=release_map.DEFAULT_PLUGIN,
        help="タグの接頭辞 <plugin>--v と CHANGELOG の見出しのプラグイン（既定 %(default)s）",
    )
    ap.add_argument("--usage-root", type=Path, default=usage_ledger.ledger_dir(), help="使用量の帳簿のディレクトリ（既定 %(default)s）")
    ap.add_argument("--sv-root", type=Path, default=SV_ROOT, help="計画の置き場。帳簿の無い期間の報告だけを読む（既定 %(default)s）")
    ap.add_argument("--projects", type=Path, default=PROJ, help="Claude Code の会話の置き場（既定 %(default)s）")
    ap.add_argument("--seat-prefix", default=SEAT_PREFIX, help="レビュー・改修の席の会話の置き場の接頭辞（既定 %(default)s）")
    ap.add_argument(
        "--out",
        default=str(Path.home() / ".local/state/ndf/measure-1142/claude-p-usage"),
        help="出力の接頭辞（.md と .json を書く。PR の一覧のキャッシュは同じディレクトリの prs.json。既定 %(default)s）",
    )
    a = ap.parse_args(argv)
    configure(a.repo, a.sv_root, a.projects, a.seat_prefix)
    key = usage_ledger.repo_key(str(a.repo))
    ledger_path = a.usage_root / "claude" / f"{key}.jsonl"
    ledger = read_ledger(a.usage_root, key)
    t0 = ledger[0][0] if ledger else None  # 帳簿のある期間の始まり（決定 4）
    if a.since is None and t0 is None:
        ap.error(f"帳簿が無い（{ledger_path}）。--since を渡す")
    since = ts(a.since) if a.since else t0
    until = ts(a.until) if a.until else None
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    rm = release_map.ReleaseMap.from_repo(a.repo, release_map.load_prs(a.repo, Path(a.out).parent / "prs.json"), a.plugin)
    A, C = read_persisted(since, until, rm)
    a_sessions = {r["session"] for r in A}
    B, outside, outside_rows = read_ledger_plans(ledger, since, until, a_sessions, rm)
    report_until = None
    if t0 is None or since < t0:  # 帳簿の無い期間だけ報告から読む（I2）
        report_until = until if t0 is None else min(t0, until) if until is not None else t0
        B = [r for r in read_reports(since, report_until) if t0 is None or r["at"] < t0] + B
    un = {
        "A": assign(A, rm, lambda r: r["prs"]),
        "B": assign(B, rm, lambda r: r["pr_candidates"] if r["source"] == "ledger" else pr_of_report(rm)(r)),
        "C": assign(C, rm, lambda r: r["prs"]),
    }
    for r in outside_rows:
        r["version"] = rm.version_at(r["at"])
    rows = table(rm, A, B, C, outside_rows, since, until)
    meta = {
        "generated": iso(datetime.now(timezone.utc).timestamp()),
        "since": iso(since),
        "until": a.until,
        "ledger_since": iso(t0) if t0 is not None else None,
        "ledger_path": str(ledger_path),
        "report_until": iso(report_until) if report_until is not None else None,
        "unassigned": {k: dict(v) for k, v in un.items()},
        "A": A,
        "B": B,
        "B_outside": outside,
    }
    Path(a.out + ".md").write_text(md(rows, meta))
    for r in A + C:
        r.pop("cwd", None)
        r.pop("session", None)
    for r in B:
        r.pop("record", None)
    keys = ("generated", "since", "until", "ledger_since", "ledger_path", "report_until", "unassigned")
    Path(a.out + ".json").write_text(
        json.dumps(
            {"meta": {k: meta[k] for k in keys}, "by_version": rows, "A": A, "B": B, "B_outside": outside, "C": C},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        json.dumps(
            {"A": len(A), "B": len(B), "C": len(C), "ledger_since": meta["ledger_since"], "unassigned": meta["unassigned"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
