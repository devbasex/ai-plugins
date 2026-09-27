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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/ndf/scripts/lib"))
from transcript_agents import layer_of, role_of  # 持ち場の語彙は 1 か所に置く

# 換算費用の重み（input=1）。cache read だけはモデルで倍率が違うため READ_RATES で決める
WEIGHTS = {"inp": 1.0, "w5": 1.25, "w1h": 2.0, "out": 5.0}
# cache read の倍率（そのモデルの input 単価との比）。**この表を正本にする**（#893。#955 の文書はここを指す）。
# 出典: 公式の料金表 https://platform.claude.com/docs/en/about-claude/pricing と claude-api の Skill
# （2026-09-24 に確認。Opus 5.5 は input $4 / cache read $0.20、Fable 5.1 は $10 / $0.25）。
# `message.model` の先頭で引き、無いモデルは READ_RATE_DEFAULT
READ_RATES = (("claude-opus-5-5", 0.05), ("claude-fable-5-1", 0.025))
READ_RATE_DEFAULT = 0.1
# 全体の書き直し: 1 回の書き込みが文脈のこの割合を超え、かつ REWRITE_MIN を超える呼び出し（#954 の判定）
REWRITE_SHARE = 0.5
REWRITE_MIN = 20_000
CACHE_5M = 300  # 書き直しの直前の間隔がこの秒数を超えたら、5 分のキャッシュが切れた後とみなす
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
    return sum(min(b - a, cap) for a, b in zip(times, times[1:]))  # pairwise は 3.10 から


def read_rate(model: str | None) -> float:
    for prefix, rate in READ_RATES:
        if (model or "").startswith(prefix):
            return rate
    return READ_RATE_DEFAULT


def is_rewrite(write: int, context: int) -> bool:
    return write > REWRITE_SHARE * context and write > REWRITE_MIN


def median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


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
    """応答の usage の合計と、呼び出しの並びから数えた値。

    `read_cost` は cache read に呼び出しごとのモデルの倍率を掛けた値（モデルが混ざっても換算がずれない）。
    `p` は起動ごとの固定費（記録ごとの最初の呼び出しの文脈）の合計、`rewrites` は 2 回目以降の呼び出しの
    うち全体の書き直しに当たる回数、`gaps` はその直前の呼び出しとの間隔（秒）。
    """
    calls: int = 0
    inp: int = 0
    read: int = 0
    w5: int = 0
    w1h: int = 0
    out: int = 0
    read_cost: float = 0.0
    p: int = 0
    rewrites: int = 0
    rewrite_tokens: int = 0
    gaps: list = field(default_factory=list)

    @property
    def context(self) -> int:
        return self.inp + self.read + self.w5 + self.w1h

    @property
    def cost(self) -> float:
        return sum(WEIGHTS[k] * getattr(self, k) for k in WEIGHTS) + self.read_cost

    def add(self, other: Usage) -> None:
        for k in ("calls", "inp", "read", "w5", "w1h", "out", "read_cost", "p", "rewrites", "rewrite_tokens"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.gaps.extend(other.gaps)

    def record_calls(self, calls: list[tuple[float | None, int, int]]) -> None:
        """呼び出しの並び（時刻・文脈・書き込み）から P と書き直しを数える。時刻の順に並べて渡す。"""
        prev = None
        for i, (t, context, write) in enumerate(calls):
            if i == 0:
                self.p += context
            elif is_rewrite(write, context):
                self.rewrites += 1
                self.rewrite_tokens += write
                # 時刻の欠けた書き直しも None として積み、回数と間隔の分母を揃える
                self.gaps.append(t - prev if t is not None and prev is not None else None)
            prev = t


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


def scan_file(path: Path, text: str | None = None, until: float | None = None) -> FileScan:
    """記録 1 件を読む。応答は message.id で重複を除き、最後の usage を取る。

    `text` を渡すと、読み済みの本文を使ってファイルを読み直さない。`until` より後の時刻の行は読まない。
    """
    s = FileScan()
    per_msg: dict[str, dict] = {}
    msg_meta: dict[str, tuple] = {}  # message.id -> (最初に現れた時刻, モデル)
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
        if until is not None and t is not None and t > until:
            continue
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
                    msg_meta.setdefault(msg["id"], (t, model))
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
    calls = []
    for mid, u in per_msg.items():
        cc = u.get("cache_creation") or {}
        w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
        w5 = cc.get("ephemeral_5m_input_tokens", (u.get("cache_creation_input_tokens") or 0) - w1h) or 0
        t, model = msg_meta[mid]
        read = u.get("cache_read_input_tokens") or 0
        row = Usage(1, u.get("input_tokens") or 0, read, w5, w1h, u.get("output_tokens") or 0, read * read_rate(model))
        if row.context == 0:
            continue
        s.usage.add(row)
        calls.append((t, row.context, w5 + w1h))
    # 時刻の無い応答は元の順のまま末尾へ置く（sorted は安定）
    s.usage.record_calls(sorted(calls, key=lambda c: (c[0] is None, c[0] or 0)))
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


def read_claude(root: Path, idle_cap: int, until: float | None = None) -> tuple[list[Session], list, dict]:
    sessions: list[Session] = []
    seats: list = []
    skipped = Counter()
    for main in sorted(root.glob("*/*.jsonl")):
        head = main.read_text(encoding="utf-8", errors="replace")
        is_seat = main.parent.name.startswith("-tmp-ndf-worktrees-")
        if not is_seat and "/ai-plugins/ndf/" not in head:
            skipped["ndf の Skill が無い"] += 1
            continue
        s = scan_file(main, head, until)  # 判定で読んだ本文を使い、同じファイルを 2 度読まない
        if is_seat or s.cwd.startswith("/tmp/ndf-worktrees/"):
            m = WT_RE.search(s.cwd + "/")
            if m and s.times and s.usage.calls:  # 応答の無い記録（起動に失敗した席）は数えない
                seats.append(External("claude", m.group(2)[:2], "/".join(m.groups()), min(s.times),
                                      active_seconds(s.times, idle_cap), s.models and most_common(s.models) or "不明",
                                      tokens={"context": s.usage.context, "out": s.usage.out, "cost": s.usage.cost},
                                      usage=s.usage))
            continue
        subs = [(p, scan_file(p, until=until), read_meta(p)) for p in sorted((main.parent / main.stem / "subagents").glob("*.jsonl"))]
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
    usage: Usage | None = None  # 呼び出しの並びが分かる席だけ（claude・codex）。P・k・書き直しに使う


def read_codex(root: Path, idle_cap: int, until: float | None = None) -> list[External]:
    out = []
    for path in sorted(root.glob("**/rollout-*.jsonl")):
        cwd, model, total, times, calls = "", None, None, [], []
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
            if until is not None and t is not None and t > until:
                continue
            if t is not None:
                times.append(t)
            p = d.get("payload") or {}
            if d.get("type") == "session_meta":
                cwd = p.get("cwd") or ""
            elif d.get("type") == "turn_context" and not model:
                model = p.get("model")
            elif p.get("type") == "token_count" and p.get("info"):
                new_total = p["info"].get("total_token_usage") or total
                last = p["info"].get("last_token_usage") or {}
                if last and new_total != total:  # 同じ累計の token_count は同じ呼び出しの再掲
                    inp, cached = last.get("input_tokens", 0), last.get("cached_input_tokens", 0)
                    calls.append((t, inp, cached, last.get("output_tokens", 0)))
                total = new_total
        m = WT_RE.search(cwd + "/")
        if not m or not times or total is None:  # token_count の無い記録（起動に失敗した席）は数えない
            continue
        # codex の input_tokens は cached を含む。キャッシュに当たらなかった分を書き込みとみなす。
        # 呼び出しごとの値（last_token_usage）が無い記録は、P・k を取れないものとして usage を持たない
        usage = None
        if calls:
            usage = Usage()
            for _, inp, cached, out_t in calls:
                usage.add(Usage(1, inp - cached, cached, 0, 0, out_t))
            usage.record_calls([(t, inp, inp - cached) for t, inp, cached, _ in calls])
        out.append(External("codex", m.group(2)[:2], "/".join(m.groups()), min(times), active_seconds(times, idle_cap),
                            model or "不明", tokens={"input": total.get("input_tokens", 0),
                                                    "cached": total.get("cached_input_tokens", 0),
                                                    "out": total.get("output_tokens", 0)}, usage=usage))
    return out


def read_kiro(root: Path, until: float | None = None) -> list[External]:
    """kiro の記録はターンに時刻を持たないため、`until` では作成が後の記録だけを除く。"""
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = WT_RE.search((d.get("cwd") or "") + "/")
        start = parse_ts(d.get("created_at"))
        if not m or start is None or (until is not None and start > until):
            continue
        turns = ((d.get("session_state") or {}).get("conversation_metadata") or {}).get("user_turn_metadatas") or []
        credit = sum(x.get("value", 0) for t in turns for x in (t.get("metering_usage") or []))
        sec = sum((t.get("turn_duration") or {}).get("secs", 0) for t in turns)
        models = Counter(t.get("model") for t in turns if t.get("model"))
        out.append(External("kiro", m.group(2)[:2], "/".join(m.groups()), start, sec, most_common(models),
                            tokens={"credit": credit, "calls": len(turns)}))
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


def call_stats(u: Usage, n: int) -> dict:
    """1 起動あたりの P・k・書き込みの内訳と、書き直しの回数・量・直前の間隔（分）。

    回数と量は合計で、1 起動あたりではない（`rewrite_tokens / rewrites` が 1 回あたりの量になる）。
    """
    known = [g for g in u.gaps if g is not None]
    gap = median(known)
    return {"p": u.p / n, "k": u.calls / n, "w5": u.w5 / n, "w1h": u.w1h / n,
            "rewrites": u.rewrites, "rewrites_after_5m": sum(1 for g in known if g > CACHE_5M),
            "rewrites_untimed": len(u.gaps) - len(known),
            "rewrite_tokens": u.rewrite_tokens, "rewrite_gap_median": None if gap is None else gap / 60}


def _min(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}"


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
                                    "minutes": r.sec / r.n / 60, **call_stats(r.usage, r.n)})
        ext_groups: dict = defaultdict(list)
        for s in ss:
            for e in s.external:
                ext_groups[(e.runtime, e.kind, e.model)].append(e)
        for (rt, kind, model), es in sorted(ext_groups.items()):
            tok = Counter()
            calls = Usage()
            with_calls = [e for e in es if e.usage]
            for e in es:
                tok.update(e.tokens)
            for e in with_calls:
                calls.add(e.usage)
            # 呼び出しの並びが分かる席だけを分母にする。分からない席しか無ければ P・k を出さない（kiro はターン数を k に）
            if with_calls:
                stats = call_stats(calls, len(with_calls)) | {"call_seats": len(with_calls)}
                if rt == "codex":  # codex は書き込みを記録しない（キャッシュに当たらなかった分は書き直しの判定にだけ使う）
                    stats.pop("w5"), stats.pop("w1h")
            elif "calls" in tok:  # kiro のターン数は利用者のターンで、呼び出し回数 k ではない
                stats = {"turns": tok.pop("calls") / len(es)}
            else:
                stats = {}
            external.append(axis | {"runtime": rt, "skill": "cross-review" if kind == "pr" else "cross-refactoring",
                                    "cli_model": model, "count": len(es), **{k: v / len(es) for k, v in tok.items()},
                                    "minutes": sum(e.sec for e in es) / len(es) / 60, **stats})
    return {"per_pr": per_pr, "per_role": per_role, "external": external}


def render_md(result: dict, by: list[str]) -> str:
    heads = [AXIS_LABEL[a] for a in by]

    def table(cols: list[str], rows: list[list[str]]) -> list[str]:
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        return lines + ["| " + " | ".join(r) + " |" for r in rows]

    meta = result["meta"]
    summary = (f"対象の会話: {meta['sessions']} 件 / PR を作った会話: {meta['sessions_with_pr']} 件 / "
               f"寄せ先の無い外部 CLI: {meta['unlinked_external']} 件")
    rates = " / ".join(f"{m} {r}" for m, r in READ_RATES)
    legend = (f"換算は input を 1 とした費用（cache read はモデル別: {rates} / 他 {READ_RATE_DEFAULT}。"
              "cache write 5 分 1.25・1 時間 2 / output 5）。入力は input + cache read + cache write。所要は分。")
    calls_legend = (f"P は起動ごとの固定費（最初の呼び出しの文脈）、k は呼び出し回数（どちらも 1 起動あたり）。"
                    f"書き直しは 2 回目以降の呼び出しのうち、書き込みが文脈の {REWRITE_SHARE:.0%} を超え、かつ "
                    f"{REWRITE_MIN // 1000}k を超えたものの回数（合計）。5 分超はそのうち直前の呼び出しとの間隔が "
                    f"{CACHE_5M // 60} 分を超えた回数、間隔は直前の間隔の中央値（分）。")
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
    out += ["", "## 持ち場ごとの呼び出しとキャッシュ", "", calls_legend, ""]
    out += table(heads + ["層", "持ち場", "起動", "P", "k", "書き込み 5 分", "書き込み 1 時間", "書き直し", "5 分超", "間隔"],
                 [[r[a] for a in by] + [r["layer"], r["role"], str(r["count"]), _k(r["p"]), f"{r['k']:.1f}",
                                        _m(r["w5"]), _m(r["w1h"]), str(r["rewrites"]), str(r["rewrites_after_5m"]),
                                        _min(r["rewrite_gap_median"])] for r in result["per_role"]])
    out += ["", "## 外部 CLI（1 起動あたり）", "",
            "kiro はトークン数を記録しない（値が 0）ため credit だけを載せる。agy は読まない。", ""]
    out += table(heads + ["ランタイム", "Skill", "モデル", "起動", "入力", "cache", "出力", "credit", "所要"],
                 [[r[a] for a in by] + [r["runtime"], r["skill"], r["cli_model"], str(r["count"]),
                                        _m(r.get("input", r.get("context", 0))), _m(r.get("cached", 0)),
                                        _k(r.get("out", 0)), f"{r.get('credit', 0):.2f}", f"{r['minutes']:.1f}"]
                  for r in result["external"]])
    out += ["", "## 外部 CLI の呼び出しとキャッシュ（1 起動あたり）", "",
            "codex は input のうち cached に当たらなかった分を書き込みとみなし、呼び出しごとの値（last_token_usage）を持つ席だけで数える。"
            "kiro は呼び出し回数を記録しない（利用者のターン数は JSON の turns）。取れない値は -。", ""]
    out += table(heads + ["ランタイム", "Skill", "モデル", "起動", "P", "k", "書き直し", "5 分超", "間隔"],
                 [[r[a] for a in by] + [r["runtime"], r["skill"], r["cli_model"], str(r["count"]),
                                        _k(r["p"]) if "p" in r else "-", f"{r['k']:.1f}" if "k" in r else "-",
                                        str(r.get("rewrites", "-")), str(r.get("rewrites_after_5m", "-")),
                                        _min(r.get("rewrite_gap_median"))]
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
    ap.add_argument("--until", help="この時刻より後の記録の行を読まない（ISO 8601。例: 2026-09-24T09:00:00Z）。"
                                     "進行中の会話を含むときに、同じ集計を後から作り直せるようにする")
    args = ap.parse_args(argv)
    by = [a.strip() for a in args.by.split(",") if a.strip()]
    bad = [a for a in by if a not in AXES]
    if bad or not by:
        ap.error(f"--by に使えない軸: {', '.join(bad) or '(空)'}（使える軸: {', '.join(AXES)}）")

    until = None
    if args.until:
        until = parse_ts(args.until)
        if until is None:
            ap.error(f"--until を時刻として読めない: {args.until}")
        if datetime.fromisoformat(args.until.replace("Z", "+00:00")).tzinfo is None:  # 機械の時間帯で打ち切りが変わる
            ap.error(f"--until に時間帯を付ける（例: 2026-09-24T09:00:00Z）: {args.until}")
    sessions, seats, skipped = read_claude(args.claude_root, args.idle_cap, until)
    externals = seats + read_codex(args.codex_root, args.idle_cap, until) + read_kiro(args.kiro_root, until)
    unlinked = link_external(sessions, externals)  # 版で絞る前に寄せる（古い版の会話の分を未対応に数えない）
    if args.min_version:
        floor = version_key(args.min_version)
        sessions = [s for s in sessions if version_key(s.version) >= floor]
    result = aggregate(sessions, by)
    result["meta"] = {"by": by, "min_version": args.min_version, "until": args.until, "sessions": len(sessions),
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
