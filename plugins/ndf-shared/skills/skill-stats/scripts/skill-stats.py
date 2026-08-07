#!/usr/bin/env python3
"""NDF skill usage statistics from Claude Code transcripts.

Scans ~/.claude/projects/**/*.jsonl and counts, for each NDF skill:
  - auto:        tool_use where name="Skill" and input.skill="ndf:<name>"
  - explicit:    user slash commands, recorded as <command-name>/ndf:<name>
  - invocations: auto + explicit
  - triggers:    user messages whose text contains keywords from the skill's
                 description / when_to_use "Triggers:" line
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
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Iterable


def plugin_root_default() -> pathlib.Path:
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return pathlib.Path(env)
    # scripts/skill-stats.py -> plugins/ndf-shared/skills/skill-stats/scripts/
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
    root = pathlib.Path.home() / ".claude" / "projects"
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
_QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")
_TRIGGER_LABEL_RE = re.compile(
    r"(?:Triggers?|明示トリガ|トリガー?)\s*[:：]\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
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

    Almost every skill lists its triggers in `when_to_use`, not `description`,
    and the label is written either as "Triggers:" or in Japanese
    ("明示トリガ:"). Both fields and both labels are accepted.
    """
    text = "\n".join(t for t in (description, when_to_use) if t)
    triggers: list[str] = []
    m = _TRIGGER_LABEL_RE.search(text)
    if m:
        for q in _QUOTED_RE.findall(m.group(1)):
            triggers.append(q.strip())
    if triggers:
        return _dedupe_ci(triggers), "explicit"
    if not include_fallback:
        return [], "none"
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
    a user message carrying <command-name>/ndf:review</command-name>. Installs
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
    lookahead_cap: int = 100,
) -> dict[str, tuple[Counter, Counter, Counter, Counter]]:
    """Return { project: (auto, explicit, triggers_hits, hits) }."""
    result: dict[str, tuple[Counter, Counter, Counter, Counter]] = defaultdict(
        lambda: (Counter(), Counter(), Counter(), Counter())
    )
    skill_names = {s["name"] for s in skills}
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
    if args.skill:
        skills = [s for s in skills if args.skill in s["name"]]

    transcripts = list(iter_transcripts(effective_days, date_from, date_to))

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

    per_project = aggregate_by_project(transcripts, skills)
    if args.project:
        needle = args.project.lower()
        per_project = {
            k: v for k, v in per_project.items() if needle in k.lower()
        }
        if not per_project:
            print(f"[skill-stats] no projects matched: {args.project}", file=sys.stderr)
            return 0

    if args.format == "json":
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
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
