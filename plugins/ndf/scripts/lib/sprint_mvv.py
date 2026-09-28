"""sprint_mvv.py: `sprint-state.py init` のスプリント MVV（#1078・#1400）とプロジェクト MVV（#1366）の扱い。

`pace: normal` は `normal_mvv` が写す（特定できなくても止めず、照合もしない）。以下は `fast` / `auto` の扱い。

- マイルストーンの説明（`gh api repos/<所有者>/<リポジトリ>/milestones/<M>`）から `## Mission` / `## Vision` / `## Value` の節を
  状態のファイルの隣の `mvv.md` へ写し、`mvv.path`・`mvv.sha256` を返す。見出しが 1 つでも無い・取得できないときは止まる（3）。
  `--mvv` を渡せば写さずにそのファイルを使う。どちらも無ければ 2（承認済みのプロジェクト MVV があれば、スプリント MVV なしで進める）
- 承認済みのプロジェクト MVV（`--root` の `.ndf/`）があれば、スプリント MVV を照合（`project-mvv.py vet --kind sprint` と同じ）に通し、
  「従う」でなければ箇所を示して止まる（1）。状態には参照（`project_mvv`: 版・sha256）を書く

止まるときは `(理由, 終了コード, items)` を返し、`sprint-state.py` が結果の形にする。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import md

MVV_SECTIONS = ("Mission", "Vision", "Value")
MVV_PACES = ("fast", "auto")  # 承認ゲートを MVV 判定で通す進め方（スプリント MVV を写せなければ止まり、写しを照合する）
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


def normal_mvv(a) -> tuple[dict | None, tuple | None, dict | None]:
    """pace: normal の写し（#1400 の決定 13）。(状態へ書く mvv, 止まるときの (理由, 終了コード, items), 特定できなかった理由の 1 件)。

    `--mvv` のファイルが無いときだけ止まる（明示の指定の誤り）。`--milestone` の説明を読めない・見出しがそろわないときは
    止めずに理由を返す。照合（vet）は呼び手もしない。"""
    if a.mvv:
        path = Path(a.mvv).resolve()
        if not path.is_file():
            return None, (f"MVV のファイルが無い: {a.mvv}", EXIT_PRECONDITION, []), None
        return _ref(path), None, None
    if not a.milestone:
        return None, None, None
    try:
        text = mvv_sections(milestone_description(a.milestone, a.repo))
        why = None if text else f"マイルストーン {a.milestone} の説明に ## Mission / ## Vision / ## Value の見出しがそろっていない"
    except OSError as e:
        text, why = None, f"マイルストーン {a.milestone} の説明を読めない: {e}"
    if text is None:
        return None, None, {"kind": "sprint_mvv", "result": "none", "reason": f"{why}。スプリント MVV なしで進める"}
    path = Path(a.sprint).resolve().parent / "mvv.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _ref(path), None, None


def init_mvv(a, project) -> tuple[dict | None, tuple | None]:
    """(状態へ書く mvv, 止まるときの (理由, 終了コード, items))。pace が normal なら `normal_mvv` が受け持つ。"""
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
    path = Path(a.sprint).resolve().parent / "mvv.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _ref(path), None


def withdraw(m: dict, name: str, at: str) -> int:
    """状態 m から同じ名前の承認ゲートの MVV 判定の記録（by: mvv）を外し、外した数を返す。MVV 判定で通した後に
    承認ゲートへ落ちたときに使う（#1370 の I8）。外した時刻は外す記録が無くても withdrawals に残す。並列の設計プランが
    後から書く by: mvv を `withdrawn` で断り、1 件でも関門へ落ちたら自動の通過を残さないためである。それより前の
    MVV 判定は覆しの照合で直前の判定として読ませない（`project_mvv_signals.last_mvv_verdict`）。利用者の承認の記録は外さない。"""
    gates = m.get("gates", [])
    kept = [g for g in gates if not (g.get("name") == name and g.get("by") == "mvv")]
    m["gates"] = kept
    m.setdefault("withdrawals", []).append({"name": name, "at": at})
    return len(gates) - len(kept)


def withdrawn(m: dict, name: str) -> bool:
    """同じ名前の承認ゲートで MVV 判定の通過を取り消したことがあるか。あれば by: mvv の記録を書かない。"""
    return any(w.get("name") == name for w in m.get("withdrawals", []))


def vet_stop(root: Path, path: str) -> tuple | None:
    """スプリント MVV をプロジェクト MVV に照らす。従えば None、従わなければ止まる理由と箇所。"""
    from project_lib import mvv_llm

    rec, _ = mvv_llm.vet_body(root, Path(path).read_text(encoding="utf-8"), "sprint")
    if rec["verdict"] == "follow":
        return None
    where = "／".join(f"{x.get('item')}: {x.get('reason')}" for x in rec["locations"])
    return f"スプリント MVV がプロジェクト MVV と食い違う（{rec['verdict']}）: {where}。状態を書かない", 1, rec["locations"]
