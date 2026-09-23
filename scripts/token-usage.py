#!/usr/bin/env python3
"""ndf の版ごとのトークン消費と所要時間を集計する（#893）。

#827 の使い捨ての集計（`measure.py` / `poll.py` / `report.py`）を、日付ではなく ndf の版で
集計できるようにしたもの。**読むだけで、送信先を持たない。**

    python3 scripts/token-usage.py [--min-version 10.14.0] [--by version,mode,model]
                                   [--format md|json]

読む記録:

| ランタイム | 場所 | 取る値 |
| --- | --- | --- |
| claude | `~/.claude/projects/*/<会話>.jsonl` と配下の `subagents/` | 応答ごとの usage・時刻・モデル・Claude Code の版 |
| codex | `~/.codex/sessions/**/rollout-*.jsonl` | 最後の `token_count` の累計・モデル・時刻 |
| kiro | `~/.kiro/sessions/cli/<id>.json` | credit（`metering_usage`）と `turn_duration`。トークン数は記録が 0 のため読まない |

外部 CLI（cross-review / cross-refactoring の席）は作業ディレクトリ
`/tmp/ndf-worktrees/<所有者>--<リポジトリ>/(pr|rf)<番号>` で見分け、同じ文字列を記録に含み、
時刻が範囲に入る会話へ寄せる。

**出力に会話の本文・ファイルのパス・リポジトリ名・会話の ID を載せない。** 集計値は
`docs/metrics/` へコミットするため、他社のリポジトリ名も残さない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/ndf/scripts/lib"))
from transcript_agents import layer_of, role_of  # 持ち場の語彙は 1 か所に置く

# 換算費用の重み（input=1）。#827 と同じ
WEIGHTS = {"inp": 1.0, "read": 0.1, "w5": 1.25, "w1h": 2.0, "out": 5.0}
IDLE_CAP = 1800  # 会話の行の間隔をこの秒数で打ち切る（利用者の返答待ちを所要に入れない）
LINK_MARGIN = 600  # 外部 CLI を会話へ寄せるときの時刻の余裕（秒）

VERSION_RE = re.compile(r"Base directory for this skill: \S*?/ai-plugins/ndf/(\d+\.\d+\.\d+(?:-[\w.]+)?)/skills/")
MODE_RE = re.compile(r"\bmode\s+[\"']?(light|operation|legacy-refactor|standard|documentation|architecture|full)\b")
PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
WT_RE = re.compile(r"/tmp/ndf-worktrees/([\w.-]+--[\w.-]+)/((?:pr|rf)\d+)")

AXES = ("version", "mode", "model", "cc", "reviewers")
AXIS_LABEL = {"version": "ndf の版", "mode": "モード", "model": "モデル", "cc": "Claude Code", "reviewers": "cross-review の担当"}


def version_key(v: str) -> tuple:
    head, _, suffix = v.partition("-")
    nums = tuple(int(x) for x in head.split("."))
    # 開発版（接尾辞つき）は同じ番号の正式版より前に並べる
    return nums + ((0, suffix) if suffix else (1, ""))


def parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def active_seconds(times: list[float], cap: int) -> float:
    times = sorted(times)
    return sum(min(b - a, cap) for a, b in pairwise(times))


def most_common(counter: Counter, empty: str = "不明") -> str:
    return counter.most_common(1)[0][0] if counter else empty


def _text(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict):
            if c.get("type") == "text":
                out.append(c.get("text", ""))
            elif c.get("type") == "tool_result":
                out.append(_text(c.get("content")))
    return "\n".join(out)


# ---------- claude の記録 ----------

@dataclass
class Usage:
    calls: int = 0
    inp: int = 0
    read: int = 0
    w5: int = 0
    w1h: int = 0
    out: int = 0

    @property
    def context(self) -> int:
        return self.inp + self.read + self.w5 + self.w1h

    @property
    def cost(self) -> float:
        return sum(WEIGHTS[k] * getattr(self, k) for k in WEIGHTS)

    def add(self, other: Usage) -> None:
        for k in ("calls", "inp", "read", "w5", "w1h", "out"):
            setattr(self, k, getattr(self, k) + getattr(other, k))


@dataclass
class FileScan:
    usage: Usage = field(default_factory=Usage)
    times: list = field(default_factory=list)
    models: Counter = field(default_factory=Counter)
    cc: Counter = field(default_factory=Counter)
    modes: Counter = field(default_factory=Counter)
    versions: list = field(default_factory=list)
    prs: set = field(default_factory=set)
    keys: set = field(default_factory=set)
    cwd: str = ""


def scan_file(path: Path, text: str | None = None) -> FileScan:
    """記録 1 件を読む。応答は message.id で重複を除き、最後の usage を取る。

    `text` を渡すと、読み済みの本文を使ってファイルを読み直さない。
    """
    s = FileScan()
    per_msg: dict[str, dict] = {}
    bash_cmds: dict[str, str] = {}
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        t = parse_ts(d.get("timestamp"))
        if t is not None:
            s.times.append(t)
        if not s.cwd and d.get("cwd"):
            s.cwd = d["cwd"]
        for key in WT_RE.findall(line):
            s.keys.add("/".join(key))
        msg = d.get("message") or {}
        if d.get("type") == "assistant":
            model = msg.get("model")
            if model and model != "<synthetic>":
                s.models[model] += 1
                if d.get("version"):
                    s.cc[d["version"]] += 1
                if msg.get("id") and msg.get("usage"):
                    per_msg[msg["id"]] = msg["usage"]
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command", "")
                    bash_cmds[c.get("id")] = cmd
                    s.modes.update(MODE_RE.findall(cmd))
        elif d.get("type") == "user":
            content = msg.get("content")
            if d.get("isMeta"):
                m = VERSION_RE.search(_text(content))
                if m:
                    s.versions.append(m.group(1))
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and "gh pr create" in bash_cmds.get(c.get("tool_use_id"), ""):
                        s.prs.update(PR_URL_RE.findall(_text(c.get("content"))))
    for u in per_msg.values():
        cc = u.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
        w5 = cc.get("ephemeral_5m_input_tokens", (u.get("cache_creation_input_tokens") or 0) - w1h) or 0
        row = Usage(1, u.get("input_tokens") or 0, u.get("cache_read_input_tokens") or 0, w5, w1h, u.get("output_tokens") or 0)
        if row.context == 0:
            continue
        s.usage.add(row)
    return s


@dataclass
class Role:
    usage: Usage = field(default_factory=Usage)
    n: int = 0
    sec: float = 0.0


@dataclass
class Session:
    version: str
    mode: str
    model: str
    cc: str
    prs: set
    keys: set
    start: float
    end: float
    active: float
    roles: dict  # (層, 持ち場) -> Role
    external: list = field(default_factory=list)

    @property
    def reviewers(self) -> str:
        seats = sorted({e.runtime for e in self.external if e.kind == "pr"})
        return "+".join(seats) or "-"

    def axis(self, name: str) -> str:
        return self.reviewers if name == "reviewers" else getattr(self, name)


def read_meta(path: Path) -> dict:
    try:
        return json.loads(path.with_name(path.name[:-6] + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mode_of(modes: Counter) -> str:
    if not modes:
        return "不明"
    return next(iter(modes)) if len(modes) == 1 else "混在"


def read_claude(root: Path, idle_cap: int) -> tuple[list[Session], list, dict]:
    sessions: list[Session] = []
    seats: list = []
    skipped = Counter()
    for main in sorted(root.glob("*/*.jsonl")):
        head = main.read_text(encoding="utf-8", errors="replace")
        is_seat = main.parent.name.startswith("-tmp-ndf-worktrees-")
        if not is_seat and "/ai-plugins/ndf/" not in head:
            skipped["ndf の Skill が無い"] += 1
            continue
        s = scan_file(main, head)  # 判定で読んだ本文を使い、同じファイルを 2 度読まない
        if is_seat or s.cwd.startswith("/tmp/ndf-worktrees/"):
            m = WT_RE.search(s.cwd + "/")
            if m and s.times and s.usage.calls:  # 応答の無い記録（起動に失敗した席）は数えない
                seats.append(External("claude", m.group(2)[:2], "/".join(m.groups()), min(s.times),
                                      active_seconds(s.times, idle_cap), s.models and most_common(s.models) or "不明",
                                      tokens={"context": s.usage.context, "out": s.usage.out, "cost": s.usage.cost}))
            continue
        subs = [(p, scan_file(p), read_meta(p)) for p in sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))]
        versions = s.versions or [v for _, sub, _ in subs for v in sub.versions]
        if not versions or not s.times:
            skipped["版を判定できない"] += 1
            continue
        roles: dict = defaultdict(Role)
        c = roles[("conductor", "-")]
        c.usage.add(s.usage)
        c.n += 1
        c.sec += active_seconds(s.times, idle_cap)
        modes, prs, keys = Counter(s.modes), set(s.prs), set(s.keys)
        for _, sub, meta in subs:
            depth = int(meta.get("spawnDepth") or 1)
            layer = layer_of(depth, meta.get("description"))
            r = roles[(layer, role_of(meta.get("description"), layer))]
            r.usage.add(sub.usage)
            r.n += 1
            r.sec += (max(sub.times) - min(sub.times)) if sub.times else 0
            modes.update(sub.modes)
            prs |= sub.prs
            keys |= sub.keys
        sessions.append(Session(versions[0], mode_of(modes), most_common(s.models), most_common(s.cc), prs, keys,
                                min(s.times), max(s.times), c.sec, dict(roles)))
    return sessions, seats, dict(skipped)


# ---------- 外部 CLI ----------

@dataclass
class External:
    runtime: str
    kind: str  # pr = cross-review / rf = cross-refactoring
    key: str
    start: float
    sec: float
    model: str
    tokens: dict = field(default_factory=dict)


def read_codex(root: Path, idle_cap: int) -> list[External]:
    out = []
    for path in sorted(root.glob("**/rollout-*.jsonl")):
        cwd, model, total, times = "", None, None, []
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            t = parse_ts(d.get("timestamp"))
            if t is not None:
                times.append(t)
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                cwd = p.get("cwd") or ""
            elif d.get("type") == "turn_context" and not model:
                model = p.get("model")
            elif p.get("type") == "token_count" and p.get("info"):
                total = p["info"].get("total_token_usage") or total
        m = WT_RE.search(cwd + "/")
        if not m or not times or total is None:  # token_count の無い記録（起動に失敗した席）は数えない
            continue
        out.append(External("codex", m.group(2)[:2], "/".join(m.groups()), min(times), active_seconds(times, idle_cap),
                            model or "不明", tokens={"input": total.get("input_tokens", 0),
                                                    "cached": total.get("cached_input_tokens", 0),
                                                    "out": total.get("output_tokens", 0)}))
    return out


def read_kiro(root: Path) -> list[External]:
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = WT_RE.search((d.get("cwd") or "") + "/")
        start = parse_ts(d.get("created_at"))
        if not m or start is None:
            continue
        turns = ((d.get("session_state") or {}).get("conversation_metadata") or {}).get("user_turn_metadatas") or []
        credit = sum(x.get("value", 0) for t in turns for x in (t.get("metering_usage") or []))
        sec = sum((t.get("turn_duration") or {}).get("secs", 0) for t in turns)
        models = Counter(t.get("model") for t in turns if t.get("model"))
        out.append(External("kiro", m.group(2)[:2], "/".join(m.groups()), start, sec, most_common(models),
                            tokens={"credit": credit}))
    return out


def link_external(sessions: list[Session], externals: list[External]) -> int:
    """外部 CLI を会話へ寄せる。寄せ先が無い件数を返す。"""
    unlinked = 0
    for e in externals:
        cand = [s for s in sessions if e.key in s.keys and s.start - LINK_MARGIN <= e.start <= s.end + LINK_MARGIN]
        if cand:
            min(cand, key=lambda s: s.end - s.start).external.append(e)
        else:
            unlinked += 1
    return unlinked


# ---------- 集計 ----------

def _m(x: float) -> str:
    return f"{x / 1e6:.2f}M"


def _k(x: float) -> str:
    return f"{x / 1e3:.1f}k"


def aggregate(sessions: list[Session], by: list[str]) -> dict:
    groups: dict[tuple, list[Session]] = defaultdict(list)
    for s in sessions:
        groups[tuple(s.axis(a) for a in by)].append(s)

    def order(k):
        return tuple(version_key(v) if a == "version" else (v,) for a, v in zip(by, k))

    per_pr, per_role, external = [], [], []
    for k in sorted(groups, key=order):
        ss = groups[k]
        axis = dict(zip(by, k))
        with_pr = [s for s in ss if s.prs]
        n_pr = sum(len(s.prs) for s in with_pr)
        if n_pr:
            layer_cost = Counter()
            total = Usage()
            ext = Counter()
            for s in with_pr:
                for (layer, _), r in s.roles.items():
                    layer_cost[layer] += r.usage.cost
                    total.add(r.usage)
                for e in s.external:
                    for tk, tv in e.tokens.items():
                        ext[f"{e.runtime}.{tk}"] += tv
            per_pr.append(axis | {
                "sessions": len(ss), "sessions_with_pr": len(with_pr), "prs": n_pr,
                "conductor_cost": layer_cost["conductor"] / n_pr, "supervisor_cost": layer_cost["supervisor"] / n_pr,
                "worker_cost": layer_cost["worker"] / n_pr, "context": total.context / n_pr, "out": total.out / n_pr,
                "minutes": sum(s.active for s in with_pr) / n_pr / 60,
                "codex_input": ext["codex.input"] / n_pr, "codex_out": ext["codex.out"] / n_pr,
                "kiro_credit": ext["kiro.credit"] / n_pr, "claude_seat_cost": ext["claude.cost"] / n_pr,
            })
        else:
            per_pr.append(axis | {"sessions": len(ss), "sessions_with_pr": 0, "prs": 0})
        roles: dict = defaultdict(Role)
        for s in ss:
            for rk, r in s.roles.items():
                roles[rk].usage.add(r.usage)
                roles[rk].n += r.n
                roles[rk].sec += r.sec
        layer_order = {"conductor": 0, "supervisor": 1, "worker": 2}
        for (layer, role), r in sorted(roles.items(), key=lambda x: (layer_order[x[0][0]], x[0][1])):
            per_role.append(axis | {"layer": layer, "role": role, "count": r.n, "cost": r.usage.cost / r.n,
                                    "context": r.usage.context / r.n, "out": r.usage.out / r.n,
                                    "minutes": r.sec / r.n / 60})
        ext_groups: dict = defaultdict(list)
        for s in ss:
            for e in s.external:
                ext_groups[(e.runtime, e.kind, e.model)].append(e)
        for (rt, kind, model), es in sorted(ext_groups.items()):
            tok = Counter()
            for e in es:
                tok.update(e.tokens)
            external.append(axis | {"runtime": rt, "skill": "cross-review" if kind == "pr" else "cross-refactoring",
                                    "cli_model": model, "count": len(es), **{k: v / len(es) for k, v in tok.items()},
                                    "minutes": sum(e.sec for e in es) / len(es) / 60})
    return {"per_pr": per_pr, "per_role": per_role, "external": external}


def render_md(result: dict, by: list[str]) -> str:
    heads = [AXIS_LABEL[a] for a in by]

    def table(cols: list[str], rows: list[list[str]]) -> list[str]:
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        return lines + ["| " + " | ".join(r) + " |" for r in rows]

    meta = result["meta"]
    summary = (f"対象の会話: {meta['sessions']} 件 / PR を作った会話: {meta['sessions_with_pr']} 件 / "
               f"寄せ先の無い外部 CLI: {meta['unlinked_external']} 件")
    legend = ("換算は input を 1 とした費用（cache read 0.1 / cache write 5 分 1.25・1 時間 2 / output 5）。"
              "入力は input + cache read + cache write。所要は分。")
    out = [summary, "", legend, "", "## PR 1 本あたり", ""]
    rows = []
    for r in result["per_pr"]:
        if not r["prs"]:
            continue
        rows.append([r[a] for a in by] + [str(r["sessions_with_pr"]), str(r["prs"]), _m(r["conductor_cost"]),
                    _m(r["supervisor_cost"]), _m(r["worker_cost"]), _m(r["context"]), _k(r["out"]),
                    f"{r['minutes']:.1f}", _m(r["codex_input"]), _k(r["codex_out"]),
                    f"{r['kiro_credit']:.2f}", _m(r["claude_seat_cost"])])
    out += table(heads + ["会話", "PR", "conductor 換算", "supervisor 換算", "worker 換算", "入力", "出力", "所要",
                          "codex 入力", "codex 出力", "kiro credit", "claude 席 換算"], rows)
    out += ["", "## 持ち場ごと（1 起動あたり）", ""]
    out += table(heads + ["層", "持ち場", "起動", "換算", "入力", "出力", "所要"],
                 [[r[a] for a in by] + [r["layer"], r["role"], str(r["count"]), _m(r["cost"]), _m(r["context"]),
                                        _k(r["out"]), f"{r['minutes']:.1f}"] for r in result["per_role"]])
    out += ["", "## 外部 CLI（1 起動あたり）", "",
            "kiro はトークン数を記録しない（値が 0）ため credit だけを載せる。agy は読まない。", ""]
    out += table(heads + ["ランタイム", "Skill", "モデル", "起動", "入力", "cache", "出力", "credit", "所要"],
                 [[r[a] for a in by] + [r["runtime"], r["skill"], r["cli_model"], str(r["count"]),
                                        _m(r.get("input", r.get("context", 0))), _m(r.get("cached", 0)),
                                        _k(r.get("out", 0)), f"{r.get('credit', 0):.2f}", f"{r['minutes']:.1f}"]
                  for r in result["external"]])
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ndf の版ごとのトークン消費と所要時間を集計する")
    home = Path(os.path.expanduser("~"))
    ap.add_argument("--claude-root", type=Path, default=home / ".claude/projects")
    ap.add_argument("--codex-root", type=Path, default=home / ".codex/sessions")
    ap.add_argument("--kiro-root", type=Path, default=home / ".kiro/sessions/cli")
    ap.add_argument("--min-version", help="この版以上だけを集計する（例: 10.14.0）")
    ap.add_argument("--by", default="version,mode,model", help=f"表の軸（カンマ区切り）: {', '.join(AXES)}")
    ap.add_argument("--idle-cap", type=int, default=IDLE_CAP, help="所要に入れる行の間隔の上限（秒）")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    args = ap.parse_args(argv)
    by = [a.strip() for a in args.by.split(",") if a.strip()]
    bad = [a for a in by if a not in AXES]
    if bad or not by:
        ap.error(f"--by に使えない軸: {', '.join(bad) or '(空)'}（使える軸: {', '.join(AXES)}）")

    sessions, seats, skipped = read_claude(args.claude_root, args.idle_cap)
    externals = seats + read_codex(args.codex_root, args.idle_cap) + read_kiro(args.kiro_root)
    unlinked = link_external(sessions, externals)  # 版で絞る前に寄せる（古い版の会話の分を未対応に数えない）
    if args.min_version:
        floor = version_key(args.min_version)
        sessions = [s for s in sessions if version_key(s.version) >= floor]
    result = aggregate(sessions, by)
    result["meta"] = {"by": by, "min_version": args.min_version, "sessions": len(sessions),
                      "sessions_with_pr": sum(1 for s in sessions if s.prs), "unlinked_external": unlinked,
                      "skipped": skipped}
    if args.format == "json":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_md(result, by))
    return 0


if __name__ == "__main__":
    sys.exit(main())
