"""mission_mvv.py: `mission-state.py init --pace fast` / `auto` のミッション MVV（#1078）とプロジェクト MVV（#1366）の扱い。

- マイルストーンの説明（`gh api repos/<所有者>/<リポジトリ>/milestones/<M>`）から `## Mission` / `## Vision` / `## Value` の節を
  状態のファイルの隣の `mvv.md` へ写し、`mvv.path`・`mvv.sha256` を返す。見出しが 1 つでも無い・取得できないときは止まる（3）。
  `--mvv` を渡せば写さずにそのファイルを使う。どちらも無ければ 2（承認済みのプロジェクト MVV があれば、ミッション MVV なしで進める）
- 承認済みのプロジェクト MVV（`--root` の `.ndf/`）があれば、ミッション MVV を照合（`project-mvv.py vet --kind mission` と同じ）に通し、
  「従う」でなければ箇所を示して止まる（1）。状態には参照（`project_mvv`: 版・sha256）を書く

止まるときは `(理由, 終了コード, items)` を返し、`mission-state.py` が結果の形にする。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import md

MVV_SECTIONS = ("Mission", "Vision", "Value")
MVV_PACES = ("fast", "auto")  # 承認ゲートを MVV 判定で通す進め方（MVV を写し、プロジェクト MVV の参照を残す）
EXIT_UNREADABLE, EXIT_PRECONDITION = 2, 3


def mvv_sections(text: str) -> str | None:
    """説明から Mission / Vision / Value の節を順に取り出す。1 つでも無ければ None。"""
    lines = text.splitlines()
    found = {}
    for s in md.md_sections(text):
        m = re.match(r"(Mission|Vision|Value)\b", s.heading.title)
        if s.heading.level == 2 and m and lines[s.heading.line].startswith("## "):
            found.setdefault(m.group(1), "\n".join(lines[s.heading.line : s.end]).strip())
    if any(k not in found for k in MVV_SECTIONS):
        return None
    return "\n\n".join(found[k] for k in MVV_SECTIONS) + "\n"


def milestone_description(milestone: str, repo: str | None) -> str:
    """マイルストーンの説明を gh api で読む。読めなければ OSError。"""
    path = f"repos/{repo or '{owner}/{repo}'}/milestones/{milestone}"
    p = subprocess.run(["gh", "api", path, "--jq", ".description"], capture_output=True, text=True)
    if p.returncode != 0:
        raise OSError(f"gh api {path}: {p.stderr.strip()[:300]}")
    return p.stdout


def _ref(path: Path) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def init_mvv(a, project) -> tuple[dict | None, tuple | None]:
    """(状態へ書く mvv, 止まるときの (理由, 終了コード, items))。pace が normal なら (None, None)。"""
    if a.pace not in MVV_PACES or (not a.mvv and not a.milestone and project.approved):
        return None, None
    if a.mvv:
        path = Path(a.mvv).resolve()
        if not path.is_file():
            return None, (f"MVV のファイルが無い: {a.mvv}", EXIT_PRECONDITION, [])
        return _ref(path), None
    if not a.milestone:
        return None, (f"--pace {a.pace} には --milestone（MVV の複製元）か --mvv が要る", EXIT_UNREADABLE, [])
    try:
        text = mvv_sections(milestone_description(a.milestone, a.repo))
    except (OSError, FileNotFoundError) as e:
        return None, (f"マイルストーン {a.milestone} の説明を読めない: {e}", EXIT_PRECONDITION, [])
    if text is None:
        why = f"マイルストーン {a.milestone} の説明に ## Mission / ## Vision / ## Value の見出しがそろっていない。説明を直してから打ち直す"
        return None, (why, EXIT_PRECONDITION, [])
    path = Path(a.mission).resolve().parent / "mvv.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _ref(path), None


def vet_stop(root: Path, path: str) -> tuple | None:
    """ミッション MVV をプロジェクト MVV に照らす。従えば None、従わなければ止まる理由と箇所。"""
    from project_lib import mvv_llm

    rec, _ = mvv_llm.vet_body(root, Path(path).read_text(encoding="utf-8"), "mission")
    if rec["verdict"] == "follow":
        return None
    where = "／".join(f"{x.get('item')}: {x.get('reason')}" for x in rec["locations"])
    return f"ミッション MVV がプロジェクト MVV と食い違う（{rec['verdict']}）: {where}。状態を書かない", 1, rec["locations"]
