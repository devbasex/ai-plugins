"""規約の文書の guard: supervisor が読み込まれたプラグインの外の規約の文書を Read と Bash で読むのを止める PreToolUse の判定（#2813）。Claude Code だけ。

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `NDF_SUPERVISOR_DOCS_GUARD=0` | 通す |
| 2 | 呼び手の `agent_type` が `ndf:supervisor`・`ndf:supervisor-waits` のどちらでもない | 通す（会話の記録を読まない） |
| 3 | 読む先の候補のどれも外の規約の文書でない | 通す |
| 4 | Read で、中に同じ相対パスの文書がある | 止める（理由に中の同じ文書の絶対パス） |
| 5 | Read で、中に同じ相対パスの文書が無い | 止める（理由に中の `skills/` の絶対パス） |
| 6 | Bash で、コマンドに外の規約の文書のパスがある | 止める（理由に中のプラグインルートと、中にあれば同じ文書の絶対パス） |
| 7 | 判定の途中で失敗した | 通す（呼び手の hook が拾う） |

読む先の候補は、Read は `file_path`、Bash はコマンドを語の並びへ分けて `/` を含む語（変数を含む語は除く。リダイレクトの語は記述子と演算子を外す）。`~` は展開し、
相対パスは入力の `cwd` から解く。**外の規約の文書**は、パス自身とその祖先のうち `.claude-plugin/plugin.json` を持つ最も近い
ディレクトリをプラグインルートとみなし、その `name` が読み込まれたプラグインと同じで、ルートが hook 自身のプラグインルートと違い
（シンボリックリンクは解決して比べる）、相対パスがルート自身・`skills`・`skills/<Skill>`・`skills/<Skill>/SKILL.md`・
`skills/<Skill>/references/` の下のどれかであるもの。この判定は読む先を書き換えない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import payload as pl
from .token_guard import _field, _off, deny
from .worker_skills import SUPERVISORS

OWN_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(".claude-plugin") / "plugin.json"
ENV = "NDF_SUPERVISOR_DOCS_GUARD"
REDIRECT = re.compile(r"^\d*[<>&|]+")


def _name(root: Path) -> str | None:
    """プラグインルートの `plugin.json` の `name`。読めない・JSON でないときは None。"""
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("name") if isinstance(data, dict) else None


def _plugin_root(p: Path) -> Path | None:
    for d in (p, *p.parents):
        if (d / MANIFEST).is_file():
            return d
    return None


def _is_doc(rel: tuple[str, ...]) -> bool:
    if not rel:
        return True
    if rel[0] != "skills" or len(rel) <= 2:
        return rel[0] == "skills"
    return (len(rel) == 3 and rel[2] == "SKILL.md") or rel[2] == "references"


def outside_doc(path: Path, own: Path = OWN_ROOT) -> tuple[Path, Path] | None:
    """外の規約の文書なら（そのプラグインルート, ルートからの相対パス）。そうでなければ None。`path` は絶対パス。"""
    p = path.resolve()
    root = _plugin_root(p)
    own = own.resolve()
    if root is None or root == own or _name(root) != _name(own):
        return None
    rel = p.relative_to(root)
    return (root, rel) if _is_doc(rel.parts) else None


def _abs_doc_path(word: str, cwd: str) -> Path | None:
    if not word or "$" in word:
        return None
    p = Path(word).expanduser()
    if not p.is_absolute():
        if not cwd:
            return None
        p = Path(cwd) / p
    return p


def doc_candidates(ev: pl.Event) -> list[Path]:
    """読む先の候補（絶対パス）。"""
    if ev.tool == "Read":
        p = _abs_doc_path(_field(ev.raw, "tool_input", "file_path"), ev.cwd)
        return [p] if p else []
    from .words import command_stream

    out: list[Path] = []
    for w in command_stream(_field(ev.raw, "tool_input", "command")):
        w = REDIRECT.sub("", w)  # リダイレクトの語（`</x`・`2>/x`）は記述子と演算子を外す
        for part in {w, w.split("=", 1)[-1]}:
            if "/" in part and (p := _abs_doc_path(part, ev.cwd)):
                out.append(p)
    return out


TAIL = "版の違う規約で worker の起動指示を組まないためである。止めるなら " + ENV + "=0。"


def _read_reason(hit: Path, rel: Path, own: Path) -> str:
    inner = own / rel
    head = f"読み込まれたプラグインの外の規約の文書 {hit} を読もうとした。"
    if inner.exists():
        return f"{head}読み込まれたプラグインの同じ文書 {inner} を読み直す。{TAIL}"
    return f"{head}読み込まれたプラグイン（{own}）のこの版には同じ文書が無い。{own / 'skills'} の下から規約の文書を探して読む。{TAIL}"


def _bash_reason(hits: list[tuple[Path, Path]], own: Path) -> str:
    shown = "、".join(dict.fromkeys(str(h) for h, _ in hits))
    inner = [str(own / rel) for _, rel in hits if (own / rel).is_file()]
    same = f"同じ文書: {'、'.join(dict.fromkeys(inner))}。" if inner else ""
    return (
        f"コマンドが読み込まれたプラグインの外の規約の文書 {shown} を指している。"
        f"パスを読み込まれたプラグインのプラグインルート {own} の下へ置き換えて打ち直す。{same}{TAIL}"
    )


def docs_decision(ev: pl.Event) -> dict | None:
    """判定のエントリポイント（hook.py の振り分けからだけ呼ぶ）。どの失敗も通す側へ倒す（呼び手の hook が拾う）。"""
    if _off(ENV) or ev.tool not in ("Read", "Bash"):
        return None
    if _field(ev.raw, "agent_type") not in SUPERVISORS:
        return None
    own = OWN_ROOT.resolve()
    hits = []
    for c in doc_candidates(ev):
        found = outside_doc(c, own)
        if found:
            hits.append((c, found[1]))
    if not hits:
        return None
    if ev.tool == "Read":
        return deny(_read_reason(hits[0][0], hits[0][1], own))
    return deny(_bash_reason(hits, own))
