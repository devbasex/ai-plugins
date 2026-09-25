#!/usr/bin/env python3
"""resume.py: 再開した会話が状態を戻す調べを 1 回で出す（試行）。

    python3 resume.py [--relay-dir DIR]

出すもの: ラッパーの判定と理由・この区間の始まり（自動か手か）・前の区間の終わり（ended_by）・
プラグインの版（区間の起動時・導入済み・写し）・
前の区間と今の区間で印を書かなかった Stop（mark_skipped: 背景の作業が残った・ブロックが 2 つ以上）。人が読む数行の後に step_result の 1 行の JSON。
ラッパーの外でも exit 0 で終わる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "lib"))
import relay  # noqa: E402
from step_result import emit, result  # noqa: E402


def events(d: Path) -> list[dict]:
    rows = []
    try:
        for line in (d / relay.LOG_FILE).read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return rows


def position(d: str | None) -> str:
    """relay / no-dir / not-running / not-child"""
    if not d:
        return "no-dir"
    if not relay.relay_running(d):
        return "not-running"
    return "relay" if relay.is_direct_child(d) else "not-child"


def previous_end(d: Path | None, rows: list[dict]) -> tuple[dict | None, str | None]:
    """今の区間の前の end。今の状態ディレクトリに無ければ、1 つ前の状態ディレクトリの最後の end。"""
    starts = [i for i, r in enumerate(rows) if r.get("event") == "start"]
    if starts:
        before = [r for r in rows[:starts[-1]] if r.get("event") == "end"]
        if before:
            return before[-1], str(d)
    root = Path(relay.state_root())
    dirs = sorted(p for p in root.glob("*") if p.is_dir() and (d is None or p.name < d.name))
    for p in reversed(dirs):
        ends = [r for r in events(p) if r.get("event") == "end"]
        if ends:
            return ends[-1], str(p)
    return None, None


def skipped_marks(d: Path | None, rows: list[dict], start: dict | None, end: dict | None,
                  end_dir: str | None) -> list[dict]:
    """前の区間（end の区間）と今の区間の mark_skipped の行。"""
    out = []
    if end and end_dir:
        src = rows if d is not None and end_dir == str(d) else events(Path(end_dir))
        out += [r for r in src if r.get("event") == "mark_skipped" and r.get("section") == end.get("section")]
    if start:
        out += [r for r in rows if r.get("event") == "mark_skipped" and r.get("section") == start.get("section")]
    return out


def skipped_line(r: dict) -> str:
    what = {"background": "背景の作業が残った", "blocks": "ndf-next のブロックが 2 つ以上"}.get(
        r.get("reason"), str(r.get("reason")))
    tasks = "、".join(f"{t.get('id') or '?'}（{t.get('command') or '?'}）" for t in r.get("tasks") or [])
    return (f"印を書かなかった Stop: 区間 {r.get('section')}・{r.get('at')}・{what}"
            + (f": {tasks}" if tasks else "") + ("・Stop を止めて知らせた" if r.get("held") else ""))


def installed_version() -> str | None:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((base / "plugins" / "installed_plugins.json").read_text())
        ent = data.get("plugins", {}).get("ndf@ai-plugins") or []
        return ent[0].get("version") if ent else None
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def copy_version() -> str | None:
    try:
        return Path(relay.copy_version_path()).read_text().strip() or None
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--relay-dir", default=os.environ.get("NDF_RELAY_DIR"))
    a = ap.parse_args()
    d = Path(a.relay_dir) if a.relay_dir else None
    pos = position(a.relay_dir)
    rows = events(d) if d else []
    start = next((r for r in reversed(rows) if r.get("event") == "start"), None)
    end, end_dir = previous_end(d, rows)
    started_by = None
    if start:
        started_by = "自動（印から）" if start.get("from_session") else "手（利用者の起動）"
    vers = {"区間の起動時": (start or {}).get("plugin_version"), "導入済み": installed_version(),
            "写し": copy_version()}
    known = {v for v in vers.values() if v}
    lines = [f"ラッパー: {pos}" + (f"（{a.relay_dir}）" if a.relay_dir else "")]
    if start:
        lines.append(f"この区間: {start.get('section')}・{started_by}・{start.get('at')}")
    if end:
        lines.append(f"前の区間の終わり: {end.get('ended_by')}（区間 {end.get('section')}・"
                     f"{end.get('seconds')} 秒・{end.get('at')}・{end_dir}）")
    else:
        lines.append("前の区間の終わり: 記録なし")
    skipped = skipped_marks(d, rows, start, end, end_dir)
    lines += [skipped_line(r) for r in skipped]
    lines.append("版: " + " / ".join(f"{k} {v or '-'}" for k, v in vers.items())
                 + ("（食い違いあり）" if len(known) > 1 else ""))
    for line in lines:
        print(line)
    item = {"position": pos, "relay_dir": a.relay_dir, "section": (start or {}).get("section"),
            "started_by": started_by, "previous_end": end, "previous_dir": end_dir, "versions": vers,
            "mark_skipped": skipped}
    emit(result("resume", "ok", " / ".join(lines), [item],
                {"version_mismatch": len(known) > 1, "under_relay": pos == "relay", "mark_skipped": len(skipped)}))


if __name__ == "__main__":
    sys.exit(main())
