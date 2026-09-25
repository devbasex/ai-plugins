#!/usr/bin/env python3
"""NDF skill usage statistics from Claude Code transcripts.

Scans ~/.claude/projects/**/*.jsonl and counts, for each NDF skill:
  - auto:        tool_use where name="Skill" and input.skill="ndf:<name>"
  - explicit:    user slash commands, recorded as <command-name>/ndf:<name>
  - invocations: auto + explicit
  - triggers:    user messages whose text contains keywords from the skill's
                 description / when_to_use trigger declaration
  - hits:        user messages that (a) matched a trigger AND (b) were followed
                 by an auto invocation of the same skill before the next user
                 turn. A slash command ends the window: typing the command
                 means the trigger did not fire on its own.
  - hit_rate:    hits / triggers (percent)

Supports project-level breakdown and date-range filtering.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Iterable

# 会話の記録を層の単位で読む部品は共通層にある（#550 の決定 13）。Kiro CLI が Skill を
# symlink にするため、`.resolve()` を通してからプラグインルートへ登る（`scripts/lib/README.md`）。
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"),
)
import transcript_agents  # noqa: E402

# 割る候補の印を付ける目安。`development-workflow/references/context-window.md` の
# 「遅くとも切る」値と揃える。**モデルに依る値であり、あの文書が書き換わったら揃え直す。**
DEFAULT_WINDOW_LIMIT = 200000


def plugin_root_default() -> pathlib.Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return pathlib.Path(env)
    # scripts/skill-stats.py -> plugins/ndf/skills/skill-stats/scripts/
    return pathlib.Path(__file__).resolve().parents[3]


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise SystemExit(f"[skill-stats] invalid date: {s} (expected YYYY-MM-DD)")


def iter_transcripts(
    days: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Iterable[pathlib.Path]:
    """Yield transcript .jsonl paths whose mtime falls in the requested window.

    Priority: explicit --from/--to > --days (if neither given and days>0, use days).
    """
    root = transcript_agents.config_root() / "projects"
    if not root.exists():
        return

    # Compute effective cutoffs
    lower: datetime | None = date_from
    upper: datetime | None = date_to
    if lower is None and days is not None and days > 0:
        lower = datetime.now() - timedelta(days=days)
    # make upper inclusive to end-of-day
    if upper is not None:
        upper = upper + timedelta(days=1) - timedelta(microseconds=1)

    for p in root.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if lower and mtime < lower:
            continue
        if upper and mtime > upper:
            continue
        yield p


def iter_events(path: pathlib.Path) -> Iterable[dict]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# トリガ語の宣言は description 末尾の全角丸括弧に「・」区切りで並べる
# （規約: plugins/ndf/skills/AUTHORING.md「トリガ語の書式」）。
# 誤検出を避けるため、末尾にあり日本語を 1 文字以上含むものだけを宣言と見なす。
_TRIGGER_PAREN_RE = re.compile(r"（([^（）]{2,160})）\s*[.。]?\s*$")
_HAS_JA_RE = re.compile(r"[ぁ-んァ-ヶ一-龠ー]")
_TRIGGER_SPLIT_RE = re.compile(r"[・/／,、]")
_JA_WORD_RE = re.compile(r"[一-龥ぁ-んァ-ヶー]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}")
_STOPWORDS = {
    "true", "false", "null", "none", "when", "triggers", "trigger",
    "description", "use", "used", "using",
}


def parse_front_matter(text: str) -> dict[str, str]:
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}
    fm = m.group(1)
    out: dict[str, str] = {}
    key = None
    buf: list[str] = []
    for line in fm.splitlines():
        if re.match(r"^[A-Za-z_-]+:\s*", line):
            if key is not None:
                out[key] = "\n".join(buf).strip()
            k, _, v = line.partition(":")
            key = k.strip()
            buf = [v.strip()]
        else:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out


def extract_triggers(
    description: str,
    when_to_use: str = "",
    include_fallback: bool = False,
) -> tuple[list[str], str]:
    """Collect trigger keywords declared in description / when_to_use.

    Triggers are declared as a full-width parenthesised list at the end of the
    field: `... Use when a PR was merged（マージ後の後片付け・ブランチを整理）.`

    Each field is searched independently so that a declaration in `description`
    cannot swallow `when_to_use`.
    """
    triggers: list[str] = []
    for field in (description, when_to_use):
        if not field:
            continue
        m = _TRIGGER_PAREN_RE.search(field.strip())
        if not m or not _HAS_JA_RE.search(m.group(1)):
            continue
        for w in _TRIGGER_SPLIT_RE.split(m.group(1)):
            w = w.strip()
            if w:
                triggers.append(w)
    if triggers:
        return _dedupe_ci(triggers), "explicit"
    if not include_fallback:
        return [], "none"
    text = "\n".join(t for t in (description, when_to_use) if t)
    flat = text.replace('"', " ").replace("'", " ")
    seen: set[str] = set()
    for w in _JA_WORD_RE.findall(flat):
        w = w.strip()
        if not w or w.lower() in _STOPWORDS:
            continue
        if len(w) < 3:
            continue
        if w not in seen:
            seen.add(w)
            triggers.append(w)
        if len(triggers) >= 10:
            break
    return _dedupe_ci(triggers), "fallback"


def _dedupe_ci(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in items:
        tl = t.lower()
        if tl in seen:
            continue
        seen.add(tl)
        out.append(t)
    return out


def load_skills(plugin_root: pathlib.Path, include_fallback: bool = False) -> list[dict]:
    out: list[dict] = []
    skills_dir = plugin_root / "skills"
    if not skills_dir.is_dir():
        return out
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        f = d / "SKILL.md"
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = parse_front_matter(text)
        name = fm.get("name", d.name).strip().strip('"')
        desc = fm.get("description", "").strip().strip('"')
        when = fm.get("when_to_use", "").strip().strip('"')
        triggers, source = extract_triggers(
            desc, when, include_fallback=include_fallback
        )
        out.append({
            "name": name,
            "qualified": f"ndf:{name}",
            "triggers": triggers,
            "triggers_source": source,
            "dir": d.name,
        })
    return out


_SYSTEM_TAG_RE = re.compile(r"^\s*<(local-command|command-name|command-message|command-args|system-reminder)")
_COMMAND_NAME_RE = re.compile(r"<command-name>\s*/([^<\s]+)\s*</command-name>")


def raw_user_text(ev: dict) -> str:
    """Return the user message text without dropping system-tagged content."""
    msg = ev.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def extract_slash_invocation(ev: dict, skill_names: set[str]) -> str | None:
    """Return the skill a user invoked by slash command, if this event is one.

    Explicit invocations never appear as a Skill tool_use; they are recorded as
    a user message carrying <command-name>/ndf:pr-review</command-name>. Installs
    that predate the plugin prefix record the bare name (/review).
    """
    if ev.get("type") != "user":
        return None
    m = _COMMAND_NAME_RE.search(raw_user_text(ev))
    if not m:
        return None
    name = m.group(1).split(":")[-1]
    return f"ndf:{name}" if name in skill_names else None


def extract_user_text(ev: dict) -> str:
    if ev.get("type") != "user":
        return ""
    msg = ev.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        if _SYSTEM_TAG_RE.match(c):
            return ""
        return c
    if isinstance(c, list):
        parts: list[str] = []
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                t = b.get("text", "")
                if t and not _SYSTEM_TAG_RE.match(t):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def extract_skill_invocations(ev: dict) -> list[str]:
    if ev.get("type") != "assistant":
        return []
    msg = ev.get("message") or {}
    content = msg.get("content") or []
    invoked: list[str] = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") != "tool_use":
            continue
        if c.get("name") != "Skill":
            continue
        inp = c.get("input") or {}
        skill = inp.get("skill") or inp.get("name") or ""
        if skill:
            invoked.append(skill)
    return invoked


def detect_project(path: pathlib.Path, first_cwd: str | None) -> str:
    """Return a short project label for a transcript path.

    Preference: `cwd` field from transcript events > decoded parent dir name.
    """
    if first_cwd:
        # /work/ai-plugins -> ai-plugins
        p = pathlib.Path(first_cwd)
        return p.name or str(p)
    # fallback: encoded dir name (e.g. "-work-ai-plugins")
    parent = path.parent.name
    if parent.startswith("-"):
        # decode `-` back to `/`
        return parent[1:].replace("-", "/")
    return parent


def build_timeline(
    path: pathlib.Path,
    skill_names: set[str],
) -> tuple[list[tuple[str, object]], str]:
    """Return (timeline, project_label)."""
    timeline: list[tuple[str, object]] = []
    first_cwd: str | None = None
    for ev in iter_events(path):
        if first_cwd is None:
            cwd = ev.get("cwd")
            if isinstance(cwd, str) and cwd:
                first_cwd = cwd
        t = ev.get("type")
        if t == "user":
            slash = extract_slash_invocation(ev, skill_names)
            if slash:
                timeline.append(("slash", slash))
                continue
            text = extract_user_text(ev)
            if text:
                timeline.append(("user", text))
        elif t == "assistant":
            for skill in extract_skill_invocations(ev):
                timeline.append(("skill", skill))
    project = detect_project(path, first_cwd)
    return timeline, project


def aggregate_by_project(
    transcripts: list[pathlib.Path],
    skills: list[dict],
    all_skill_names: set[str] | None = None,
    lookahead_cap: int = 100,
) -> dict[str, tuple[Counter, Counter, Counter, Counter]]:
    """Return { project: (auto, explicit, triggers_hits, hits) }.

    `skills` may already be narrowed by `--skill`; it only decides which rows
    are counted. Slash-command detection must stay based on the *unfiltered*
    skill set (`all_skill_names`), because every slash command closes the
    hit-lookahead window. Deriving the boundary set from a filtered `skills`
    would hide other skills' slash commands and over-count hits.
    """
    result: dict[str, tuple[Counter, Counter, Counter, Counter]] = defaultdict(
        lambda: (Counter(), Counter(), Counter(), Counter())
    )
    skill_names = (
        all_skill_names if all_skill_names is not None
        else {s["name"] for s in skills}
    )
    skill_triggers = [
        (s["qualified"], [t.lower() for t in s["triggers"] if t])
        for s in skills
    ]
    for path in transcripts:
        tl, project = build_timeline(path, skill_names)
        auto, explicit, trig_h, hits = result[project]
        for i, (kind, data) in enumerate(tl):
            if kind == "skill":
                auto[data] += 1
                continue
            if kind == "slash":
                explicit[data] += 1
                continue
            if kind != "user":
                continue
            text_l = str(data).lower()
            for qualified, trs in skill_triggers:
                if not trs:
                    continue
                if any(t in text_l for t in trs):
                    trig_h[qualified] += 1
                    end = min(i + 1 + lookahead_cap, len(tl))
                    for j in range(i + 1, end):
                        k2, d2 = tl[j]
                        if k2 in ("user", "slash"):
                            break
                        if k2 == "skill" and d2 == qualified:
                            hits[qualified] += 1
                            break
    return result


def merge_counters(
    per_project: dict[str, tuple[Counter, Counter, Counter, Counter]],
) -> tuple[Counter, Counter, Counter, Counter]:
    auto_total: Counter = Counter()
    explicit_total: Counter = Counter()
    trig_total: Counter = Counter()
    hits_total: Counter = Counter()
    for auto, explicit, trig, hits in per_project.values():
        auto_total.update(auto)
        explicit_total.update(explicit)
        trig_total.update(trig)
        hits_total.update(hits)
    return auto_total, explicit_total, trig_total, hits_total


def build_rows(
    skills: list[dict],
    auto: Counter,
    explicit: Counter,
    triggers_hits: Counter,
    hits: Counter,
) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    total_auto = total_explicit = total_trig = total_hit = 0
    for s in sorted(skills, key=lambda x: x["name"]):
        q = s["qualified"]
        a = auto.get(q, 0)
        e = explicit.get(q, 0)
        trig = triggers_hits.get(q, 0)
        hit = hits.get(q, 0)
        rate = round(hit / trig * 100, 1) if trig else 0.0
        rows.append({
            "skill": q,
            "triggers_source": s["triggers_source"],
            "invocations": a + e,
            "auto": a,
            "explicit": e,
            "triggers": trig,
            "hits": hit,
            "hit_rate_pct": rate,
            "trigger_keywords": s["triggers"],
        })
        total_auto += a
        total_explicit += e
        if s["triggers_source"] == "explicit":
            total_trig += trig
            total_hit += hit
    total_rate = round(total_hit / total_trig * 100, 1) if total_trig else 0.0
    total = {
        "invocations": total_auto + total_explicit,
        "auto": total_auto,
        "explicit": total_explicit,
        "triggers": total_trig,
        "hits": total_hit,
        "hit_rate_pct": total_rate,
    }
    return rows, total


def format_markdown(rows: list[dict], total: dict, heading: str | None = None) -> str:
    lines: list[str] = []
    if heading:
        lines.append(heading)
    lines.extend([
        "| skill | triggers源 | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for r in rows:
        src = r["triggers_source"]
        if src == "none":
            rate = "-"
            trig = "-"
            hit = "-"
        else:
            rate = f"{r['hit_rate_pct']}%" if r["triggers"] else "-"
            trig = str(r["triggers"])
            hit = str(r["hits"])
        lines.append(
            f"| {r['skill']} | {src} | {r['invocations']} | {r['auto']} | "
            f"{r['explicit']} | {trig} | {hit} | {rate} |"
        )
    lines.append(
        f"| **合計** | | **{total['invocations']}** | **{total['auto']}** | "
        f"**{total['explicit']}** | **{total['triggers']}** | "
        f"**{total['hits']}** | **{total['hit_rate_pct']}%** |"
    )
    return "\n".join(lines)


# ---------- 3 層の context window の測定（#550 の AC20〜AC37） ----------

_LAYER_ORDER = {"conductor": 0, "supervisor": 1, "worker": 2}
_ROLE_ORDER = {
    name: i for i, name in enumerate(
        ("-",) + transcript_agents.POSTS + transcript_agents.TASKS
        + (transcript_agents.OTHER,)
    )
}


def _order(layer: str, role: str) -> tuple[int, int, str]:
    return (_LAYER_ORDER.get(layer, 9), _ROLE_ORDER.get(role, 9), role)


def _median(values: list[int]) -> int:
    """中央値。**分布の目安として読む列であり、判定には使わない**（決定 16）。"""
    return int(round(statistics.median(values))) if values else 0


def measured_records(records: list) -> list:
    """束ねの表に載せる記録。応答が 3 に満たないものはここからだけ外す（AC28）。"""
    return [r for r in records if r.responses >= 3 and r.fixed is not None]


def summarize_agents(records: list, window_limit: int) -> tuple[list[dict], int]:
    """層とフェーズ（worker は作業の種類）とモデルの組ごとに束ねる（AC29）。"""
    kept = measured_records(records)
    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in kept:
        groups[(r.layer, r.role, r.model or "-")].append(r)

    rows: list[dict] = []
    for (layer, role, model), items in groups.items():
        below = sum(1 for r in items if r.work < r.fixed)
        peak_max = max(r.peak for r in items)
        marks: list[str] = []
        # **束ねる候補は supervisor の行にだけ付く。** 設計のフェーズは、作るときはどの
        # モードでも必ず関門を返すため対象にしない（契約の印の表）。
        if layer == "supervisor" and role != "設計" and below > len(items) / 2:
            marks.append("束ねる候補")
        if peak_max > window_limit:
            marks.append("割る候補")
        rows.append({
            "layer": layer,
            "role": role,
            "model": model,
            "records": len(items),
            "fixed_median": _median([r.fixed for r in items]),
            "work_median": _median([r.work for r in items]),
            "work_below_fixed": below,
            "peak_max": peak_max,
            "mark": "・".join(marks),
        })
    rows.sort(key=lambda r: (_order(r["layer"], r["role"]), r["model"]))
    return rows, len(records) - len(kept)


def layer_totals(records: list) -> tuple[list[dict], dict]:
    """層ごとの合計と、3 層を合算した総消費（AC36）。**外した記録も含める。**"""
    rows: list[dict] = []
    total = {"records": 0, "fixed_sum": 0, "work_sum": 0, "total_spend": 0}
    for layer in transcript_agents.LAYERS:
        items = [r for r in records if r.layer == layer]
        if not items:
            continue
        fixed_sum = sum(r.fixed or 0 for r in items)
        work_sum = sum(r.work or 0 for r in items)
        rows.append({
            "layer": layer,
            "records": len(items),
            "fixed_sum": fixed_sum,
            "work_sum": work_sum,
            "total_spend": fixed_sum + work_sum,
        })
        total["records"] += len(items)
        total["fixed_sum"] += fixed_sum
        total["work_sum"] += work_sum
        total["total_spend"] += fixed_sum + work_sum
    return rows, total


def role_usage(records: list) -> list[dict]:
    """フェーズごとの worker の使い方（AC37）。**行は起動元ごとに 1 つである。**"""
    supervisors = [r for r in records if r.layer == "supervisor"]
    supervisors.sort(key=lambda r: (r.started_at or "", r.agent_id or ""))
    workers: dict[str, list] = defaultdict(list)
    for r in records:
        if r.layer == "worker" and r.parent_agent_id:
            workers[r.parent_agent_id].append(r)

    seq: Counter = Counter()
    rows: list[dict] = []
    for s in supervisors:
        seq[s.role] += 1
        mine = workers.get(s.agent_id or "", [])
        if not mine:
            continue
        fixed_sum = (s.fixed or 0) + sum(w.fixed or 0 for w in mine)
        work = s.work or 0
        rows.append({
            "role": s.role,
            "supervisor": seq[s.role],   # フェーズの中の連番。識別子は出さない
            "supervisor_work": work,
            "workers": len(mine),
            "fixed_sum": fixed_sum,
            "mark": "worker を使いすぎ" if fixed_sum > work else "",
        })
    rows.sort(key=lambda r: (_ROLE_ORDER.get(r["role"], 9), r["supervisor"]))
    return rows


def format_agent_summary_section(summary: list[dict], excluded: int) -> list[str]:
    lines = [
        "## 層・フェーズ・モデルごとの束ね",
        "",
        "| 層 | フェーズ | モデル | 件数 | 固定費の中央値 | 実作業の中央値 "
        "| 実作業 < 固定費 | 最大充填の最大 | 印 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in summary:
        lines.append(
            f"| {r['layer']} | {r['role']} | {r['model']} | {r['records']} | "
            f"{r['fixed_median']} | {r['work_median']} | {r['work_below_fixed']} | "
            f"{r['peak_max']} | {r['mark']} |"
        )
    lines.append("")
    lines.append(f"束ねの表から外した記録: {excluded} 件（応答が 3 に満たない）")
    return lines


def format_layer_totals_section(totals_rows: list[dict], totals: dict) -> list[str]:
    lines = [
        "## 層ごとの合計",
        "",
        "| 層 | 件数 | 固定費の合計 | 実作業の合計 | 総消費 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in totals_rows:
        lines.append(
            f"| {r['layer']} | {r['records']} | {r['fixed_sum']} | "
            f"{r['work_sum']} | {r['total_spend']} |"
        )
    lines.append(
        f"| 合計 | {totals['records']} | {totals['fixed_sum']} | "
        f"{totals['work_sum']} | {totals['total_spend']} |"
    )
    return lines


def format_role_usage_section(usage: list[dict]) -> list[str]:
    lines = [
        "## フェーズごとの worker の使い方",
        "",
        "| フェーズ | supervisor | supervisor の実作業 | worker の件数 "
        "| supervisor と worker の固定費の合計 | 印 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in usage:
        lines.append(
            f"| {r['role']} | {r['supervisor']} | {r['supervisor_work']} | "
            f"{r['workers']} | {r['fixed_sum']} | {r['mark']} |"
        )
    return lines


def format_agents_markdown(
    records: list, summary: list[dict], excluded: int,
    totals_rows: list[dict], totals: dict, usage: list[dict],
    with_session: bool,
) -> str:
    lines: list[str] = []
    if with_session:
        lines.append("## 記録ごとの context window")
        lines.append("")
        lines.append(transcript_agents.format_list(records, with_agent_id=False))
        lines.append("")
    lines.extend(format_agent_summary_section(summary, excluded))
    lines.append(
        "フェーズが読めなかった supervisor: "
        f"{transcript_agents.unphased_supervisors(records)} 件"
        "（`description` の先頭語がフェーズにも工程名にも当たらない）"
    )
    lines.append("")
    lines.extend(format_layer_totals_section(totals_rows, totals))
    if with_session:
        lines.append("")
        lines.extend(format_role_usage_section(usage))
    return "\n".join(lines)


def collect_agents(sessions: list[str], layer: str | None) -> tuple[list, int]:
    """指定のセッションの記録を読む。セッションを渡さなければ全セッションを読む。"""
    counter: dict = {}
    if sessions:
        records = transcript_agents.read_sessions(sessions, counter=counter)
    else:
        records = []
        for session in all_session_ids():
            records.extend(transcript_agents.read_session(session, counter=counter))
    if layer:
        records = [r for r in records if r.layer == layer]
    return records, counter.get("skipped", 0)


def all_session_ids() -> list[str]:
    root = transcript_agents.config_root() / "projects"
    if not root.is_dir():
        return []
    return sorted(
        p.stem for project in root.iterdir() if project.is_dir()
        for p in project.glob("*.jsonl")
    )


def select_transcripts(
    args: argparse.Namespace,
    date_from: datetime | None,
    date_to: datetime | None,
    effective_days: int | None,
) -> list[pathlib.Path]:
    """集計対象の transcript を決める。

    `--session` を渡したときはそのセッションの conductor の記録だけを数える（AC6）。
    渡さないときは日数・日付範囲で mtime を絞る。
    """
    if args.session:
        # **`--session` は Skill の統計をそのセッションの conductor の記録だけで数える**
        # （AC6 の確かめ方）。配下の記録は `--agents` の表が扱う。
        return [
            path for path in (
                transcript_agents.session_paths(s)[0] for s in args.session
            ) if path is not None
        ]
    return list(iter_transcripts(effective_days, date_from, date_to))


def filter_projects(
    per_project: dict[str, tuple[Counter, Counter, Counter, Counter]],
    needle: str,
) -> dict[str, tuple[Counter, Counter, Counter, Counter]]:
    """プロジェクト名の部分一致でテーブルを絞る。"""
    needle = needle.lower()
    return {
        k: v for k, v in per_project.items() if needle in k.lower()
    }


def emit_json(
    skills: list[dict],
    per_project: dict[str, tuple[Counter, Counter, Counter, Counter]],
    args: argparse.Namespace,
    effective_days: int | None,
    plugin_root: pathlib.Path,
    transcripts: list[pathlib.Path],
    agents: list,
    agent_summary: list[dict],
    totals_rows: list[dict],
    totals: dict,
    usage: list[dict],
    excluded: int,
) -> None:
    projects_json = []
    for project, (auto, explicit, trig, hits) in sorted(per_project.items()):
        rows, total = build_rows(skills, auto, explicit, trig, hits)
        projects_json.append({
            "project": project,
            "total": total,
            "skills": rows,
        })
    grand_rows, grand_total = build_rows(skills, *merge_counters(per_project))
    out = {
        "meta": {
            "days": effective_days,
            "date_from": args.date_from,
            "date_to": args.date_to,
            "transcripts": len(transcripts),
            "plugin_root": str(plugin_root),
            "by_project": args.by_project,
            "project_filter": args.project,
        },
        "total": grand_total,
        "grand_skills": grand_rows,
        "projects": projects_json,
    }
    if args.agents:
        out["meta"]["window_limit"] = args.window_limit
        out.update({
            "agents": [r.as_row() for r in agents],
            "agent_summary": agent_summary,
            "layer_totals": totals_rows,
            "totals": totals,
            "role_usage": usage,
            "excluded": excluded,
            "unphased_supervisors": transcript_agents.unphased_supervisors(agents),
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))


def emit_markdown(
    skills: list[dict],
    per_project: dict[str, tuple[Counter, Counter, Counter, Counter]],
    args: argparse.Namespace,
    agents: list,
    agent_summary: list[dict],
    totals_rows: list[dict],
    totals: dict,
    usage: list[dict],
    excluded: int,
) -> None:
    if args.by_project:
        for project, (auto, explicit, trig, hits) in sorted(per_project.items()):
            rows, total = build_rows(skills, auto, explicit, trig, hits)
            if total["invocations"] == 0 and total["triggers"] == 0:
                continue  # skip silent projects
            print()
            print(format_markdown(rows, total, heading=f"## {project}"))
        # grand total
        grand_rows, grand_total = build_rows(skills, *merge_counters(per_project))
        print()
        print(format_markdown(grand_rows, grand_total, heading="## 全プロジェクト合計"))
    else:
        rows, total = build_rows(skills, *merge_counters(per_project))
        print(format_markdown(rows, total))

    if args.show_keywords:
        print("\n## 抽出トリガーキーワード")
        for s in sorted(skills, key=lambda x: x["name"]):
            kw = ", ".join(s["triggers"]) or "-"
            print(f"- `ndf:{s['name']}`: {kw}")

    if args.agents:
        print()
        print(format_agents_markdown(
            agents, agent_summary, excluded, totals_rows, totals, usage,
            with_session=bool(args.session),
        ))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="NDF skill usage statistics from Claude Code transcripts",
    )
    ap.add_argument("--days", type=int, default=90,
                    help="集計対象の遡及日数 (default: 90、--from/--to 指定時は無視)")
    ap.add_argument("--from", dest="date_from", default=None,
                    help="開始日 YYYY-MM-DD (inclusive)")
    ap.add_argument("--to", dest="date_to", default=None,
                    help="終了日 YYYY-MM-DD (inclusive)")
    ap.add_argument("--plugin-root", default=None,
                    help="NDFプラグインのルート (default: 自動検出)")
    ap.add_argument("--format", choices=["md", "json"], default="md",
                    help="出力形式 (default: md)")
    ap.add_argument("--skill", default=None,
                    help="skill名(部分一致)でフィルタ")
    ap.add_argument("--project", default=None,
                    help="プロジェクト名(部分一致)でフィルタ")
    ap.add_argument("--by-project", action="store_true",
                    help="プロジェクト別に個別のテーブルを出力")
    ap.add_argument("--show-keywords", action="store_true",
                    help="各skillに抽出されたトリガーキーワードを出力")
    ap.add_argument("--include-fallback", action="store_true",
                    help="Triggers欄が無いskillでも description から語彙抽出してマッチ (ノイズ多)")
    ap.add_argument("--agents", action="store_true",
                    help="3 層（conductor / supervisor / worker）の context window を出す")
    ap.add_argument("--session", action="append", default=[],
                    help="セッション ID で絞る (繰り返し可)")
    ap.add_argument("--layer", choices=transcript_agents.LAYERS, default=None,
                    help="--agents の出力を 1 つの層に絞る")
    ap.add_argument("--window-limit", type=int, default=DEFAULT_WINDOW_LIMIT,
                    help=f"割る候補の印を付ける最大充填の目安 (default: {DEFAULT_WINDOW_LIMIT})")
    args = ap.parse_args()

    plugin_root = pathlib.Path(args.plugin_root) if args.plugin_root else plugin_root_default()
    if not (plugin_root / "skills").is_dir():
        print(f"[skill-stats] plugin root not found: {plugin_root}", file=sys.stderr)
        return 2

    date_from = _parse_date(args.date_from)
    date_to = _parse_date(args.date_to)
    # When explicit date range is given, days becomes informational only
    effective_days = args.days if (date_from is None and date_to is None) else None

    skills = load_skills(plugin_root, include_fallback=args.include_fallback)
    # Slash boundaries must be detected across every skill, not just the ones
    # left after --skill narrowing.
    all_skill_names = {s["name"] for s in skills}
    if args.skill:
        skills = [s for s in skills if args.skill in s["name"]]

    transcripts = select_transcripts(args, date_from, date_to, effective_days)

    agents: list = []
    agent_summary: list[dict] = []
    totals_rows: list[dict] = []
    totals: dict = {}
    usage: list[dict] = []
    excluded = 0
    if args.agents:
        agents, skipped = collect_agents(args.session, args.layer)
        if skipped:
            print(f"[skill-stats] 読めない行を飛ばした: {skipped} 件", file=sys.stderr)
        agent_summary, excluded = summarize_agents(agents, args.window_limit)
        totals_rows, totals = layer_totals(agents)
        usage = role_usage(agents) if args.session else []

    # Header summary
    window = []
    if date_from:
        window.append(f"from={date_from:%Y-%m-%d}")
    if date_to:
        window.append(f"to={date_to:%Y-%m-%d}")
    if not window and effective_days:
        window.append(f"last {effective_days} days")
    print(
        f"# NDF Skill 使用統計 ({' / '.join(window) or 'all time'} / "
        f"transcript {len(transcripts)}件 / plugin {plugin_root})",
        file=sys.stderr,
    )

    per_project = aggregate_by_project(transcripts, skills, all_skill_names)
    if args.project:
        per_project = filter_projects(per_project, args.project)
        if not per_project:
            print(f"[skill-stats] no projects matched: {args.project}", file=sys.stderr)
            return 0

    if args.format == "json":
        emit_json(
            skills, per_project, args, effective_days, plugin_root, transcripts,
            agents, agent_summary, totals_rows, totals, usage, excluded,
        )
    else:
        emit_markdown(
            skills, per_project, args,
            agents, agent_summary, totals_rows, totals, usage, excluded,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
