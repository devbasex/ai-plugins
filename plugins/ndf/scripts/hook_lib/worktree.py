"""メインディレクトリを編集しようとしたときに、worktree で作業する旨を伝える guard（#1142 の決定 20）。

結線先は `hooks/*.json`（Claude Code・Codex）。Kiro CLI と agy の分岐は、配布を終える前（#2231）に導入した手元の hook が
この入口を呼び続けるために残す。
- PreToolUse（Claude Code / Codex CLI）: 編集先のパスを見た案内を `additionalContext` でモデルへ渡す
- プロンプト送信時（Kiro CLI）: パスを見ない案内を標準出力へ書く
- PreToolUse（agy）: 案内をセッションの状態ファイルの `pending` へ積み、次のモデル呼び出しの前に
  `worktree-session.sh` が渡す（agy がこの事象でモデルへ文言を返す口は拒否のときにしか働かない）

**拒否しない。** 宣言（`.ndf/worktree.json`）が無いリポジトリと worktree の中では何も出さない。同じパスへの案内は
セッションの内で繰り返さない。構文を読み切れない Bash のコマンドは、判定をせずに通して案内だけを出す。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

import jsonio

from . import payload as pl
from . import write_target as wt

DEFAULT_ALLOW_PATHS = ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".agents/", ".serena/", ".ndf/", ".gitignore"]
DECLARATION_VERSION = 1
DECLARATION_FILE = ".ndf/worktree.json"
WORKTREE_DIR = ".worktrees"

KIRO_NOTICE = f"""[ndf:worktree] 現在の作業ディレクトリは、リポジトリを clone した主ディレクトリです。
開発の変更は {WORKTREE_DIR}/<ブランチ名> の作業ツリーの中で行ってください。
作業ツリーの用意は /ndf:worktree の手順に従います。
issues/ や docs/ など、知識と設定の更新はこのままで構いません。
"""
SUMMARY = f"主ディレクトリを編集しています。開発は {WORKTREE_DIR}/<ブランチ名> で行ってください（/ndf:worktree）"
CONTEXT = """この編集先は、リポジトリを clone した主ディレクトリです。

{paths}

開発の変更は作業ツリーの中で行います。まだ用意していなければ /ndf:worktree の手順で
{wdir}/<ブランチ名> に作り、そこへ移ってから編集してください。既に主ディレクトリで
変更を加えている場合は、同じ手順の移送で作業ツリー側へ移せます。

この編集を止めてはいません。意図した操作であればそのまま続けてください。"""
UNREAD = (
    "このコマンドは構文を読み切れないため、主ディレクトリへの書き込みかを判定していません（止めてはいません）。"
    f"書き込むなら、開発の変更は {WORKTREE_DIR}/<ブランチ名> の作業ツリーの中で行ってください（/ndf:worktree）。"
)


@dataclass
class Place:
    main_dir: str
    in_worktree: bool
    has_declaration: bool
    allow_paths: list[str] = field(default_factory=list)


def _git_lines(cwd: str, *args: str) -> list[str] | None:
    try:
        p = subprocess.run(["git", "-C", cwd, "rev-parse", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.split("\n") if p.returncode == 0 else None


def locate(cwd: str) -> tuple[str, bool] | None:
    """(メインディレクトリ, worktree の中か)。サブモジュールの中は通常のリポジトリとして扱う。外なら None。"""
    lines = _git_lines(cwd, "--git-dir", "--git-common-dir", "--show-superproject-working-tree")
    if lines is None or len(lines) < 2:
        return None
    git_dir = os.path.realpath(os.path.join(cwd, lines[0]))
    common = os.path.realpath(os.path.join(cwd, lines[1]))
    if len(lines) > 2 and lines[2]:
        top = _git_lines(cwd, "--show-toplevel")
        return (os.path.realpath(top[0]), False) if top and top[0] else None
    return os.path.dirname(common), git_dir != common


def declaration(main_dir: str) -> dict | None:
    """共有の宣言。無い・JSON として読めない・版が未対応なら None（個人の宣言は guard の項目を変えない）。"""
    data = jsonio.read(os.path.join(main_dir, DECLARATION_FILE), missing=None, broken=None, want=dict)
    if data is None:
        return None
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, (int, float)) or version != DECLARATION_VERSION:
        return None
    return data


def declaration_stamp(main_dir: str) -> str:
    """宣言ファイルの内容の目印（無ければ空）。控えを作り直すかを git を呼ばずに決める。"""
    try:
        with open(os.path.join(main_dir, DECLARATION_FILE), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except FileNotFoundError:
        return ""
    except OSError:
        return "unreadable"


def allow_paths(decl: dict | None) -> list[str]:
    """案内を出さないパス。`guard.allow_paths` が配列ならそれ（空の配列は何も許可しない）、それ以外は既定。"""
    guard = decl.get("guard") if isinstance(decl, dict) else None
    items = guard.get("allow_paths") if isinstance(guard, dict) else None
    if not isinstance(items, list):
        return list(DEFAULT_ALLOW_PATHS)
    return [x if isinstance(x, str) else json.dumps(x) for x in items]


def is_allowed(rel: str, entries: list[str]) -> bool:
    """末尾が `/` の項目は前方一致（ディレクトリそのものも含む）、それ以外は完全一致とその配下。"""
    for e in entries:
        if not e:
            continue
        if e.endswith("/"):
            if rel == e[:-1] or rel.startswith(e):
                return True
        elif rel == e or rel.startswith(e + "/"):
            return True
    return False


def relative_to_main(path: str, main_dir: str) -> str | None:
    if path == main_dir:
        return "."
    return path[len(main_dir) + 1 :] if path.startswith(main_dir + "/") else None


# --- セッションの状態ファイル（worktree-session.sh が `pending` を読む） ---------------------------------------


def state_file(session: str) -> str | None:
    if not session:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session)
    return os.path.join(os.environ.get("TMPDIR") or "/tmp", f"ndf-worktree-{safe}.json")


def _load_state(path: str | None) -> dict | None:
    return jsonio.read(path, missing=None, broken=None, want=dict) if path else None


def _save_state(path: str, data: dict[str, Any]) -> None:
    try:
        jsonio.write_atomic(path, data, indent=None)
    except OSError:
        pass


# セッションの持ち主として探すランタイムのプロセスの名前（先頭の一致）。
_RUNTIME_NAMES = ("claude", "codex", "kiro-cli", "kiro", "agy")


def session_owner() -> dict[str, Any] | None:
    """hook の親をたどって最初に当たるランタイムのプロセス（`{"pid", "started"}`）。見つからなければ None（#2229）。

    片付け（`lib/run_state.py`）は、この pid が開始時刻ごと生きている間は状態ファイルを消さない。記録が無ければ
    最終更新から寿命の窓で判定する。"""
    try:
        import procs  # psutil を要する。無い環境では記録を書かない
    except Exception:  # noqa: BLE001
        return None
    for pid in procs.ancestor_pids(os.getpid()):
        got = procs.parent_and_name(pid)
        if got and got[1].lower().startswith(_RUNTIME_NAMES):
            started = procs.start_time(pid)
            return {"pid": pid, "started": started} if started is not None else None
    return None


def place(cwd: str, path: str | None) -> tuple[Place, dict] | None:
    """位置と宣言の控え（Tool の呼び出しのたびに git を起動しない）。解決したときの cwd と宣言の目印が合えば使う。"""
    state = _load_state(path)
    if state is not None and path and "owner" not in state:
        # 持ち主の記録の無い既存のファイルには、次に読んだときに 1 度だけ足す（見つからなければ null を書く）
        state["owner"] = session_owner()
        _save_state(path, state)
    if state and state.get("resolved_from") == cwd and state.get("main_dir"):
        if state.get("declaration_stamp", "") == declaration_stamp(state["main_dir"]):
            allow = [x for x in state.get("allow_paths") or [] if isinstance(x, str)]
            return Place(state["main_dir"], bool(state.get("in_worktree")), bool(state.get("has_declaration")), allow), state
    found = locate(cwd)
    if found is None:
        return None
    decl = declaration(found[0])
    p = Place(found[0], found[1], decl is not None, allow_paths(decl))
    state = {
        "main_dir": p.main_dir,
        "resolved_from": cwd,
        "in_worktree": p.in_worktree,
        "has_declaration": p.has_declaration,
        "declaration_stamp": declaration_stamp(p.main_dir),
        "allow_paths": [x for x in p.allow_paths if x],
        "notified": [],
        "pending": [],
        "owner": state["owner"] if state and "owner" in state else session_owner(),
    }
    if path:
        _save_state(path, state)
    return p, state


# --- 判定 ---------------------------------------------------------------------------------------------


def targets(ev: pl.Event) -> tuple[list[str], str, bool] | None:
    """(書き込み先の候補, 相対パスの起点, 構文を読み切れなかったか)。対象の Tool でなければ None。"""
    base = ev.cwd
    if ev.tool_kind == "patch":
        text = ev.patch_text()
        return (wt.patch_targets(text), base, False) if text else None
    if ev.tool_kind == "edit":
        return ev.edit_paths(), base, False
    if ev.tool_kind == "shell":
        cmd = ev.command_text()
        if not cmd:
            return None
        cmd_cwd = ev.command_cwd()
        if cmd_cwd:
            base = wt.normalize_path(cmd_cwd, ev.cwd)
        import shparse  # 構文木が要るときだけ読む（Edit の呼び出しでは tree-sitter を読まない）

        if shparse.has_unreadable_error(shparse.parse_bash(cmd)):
            return [], base, True
        return wt.shell_targets(cmd, base), base, False
    return None


def _flagged_targets(raw: list[str], base: str, where: Place) -> list[str]:
    """書き込み先をメインディレクトリからの相対パスにし、worktree の外で許可されていないものだけを残す。"""
    flagged = []
    for r in raw:
        if not r:
            continue
        rel = relative_to_main(wt.normalize_path(r, base), where.main_dir)
        if rel is None or rel == "." or rel.startswith(WORKTREE_DIR + "/"):
            continue
        if not is_allowed(rel, where.allow_paths):
            flagged.append(rel)
    return flagged


def _dedupe_notified(path: str | None, state: dict, flagged: list[str]) -> list[str]:
    """状態ファイルがあれば、既に案内した先を除き、残りを案内済みとして保存する。"""
    if not (path and os.path.isfile(path)):
        return flagged
    seen = set(state.get("notified") or [])
    flagged = [r for r in flagged if r not in seen]
    if not flagged:
        return flagged
    state = _load_state(path) or state
    state["notified"] = sorted(set(state.get("notified") or []) | set(flagged))
    _save_state(path, state)
    return flagged


def notice(ev: pl.Event) -> dict | str | None:
    """PreToolUse とプロンプト送信時の判定。返り値は hook の出力（辞書）か、Kiro へ書く文字列か、None（何も出さない）。"""
    path = state_file(ev.session)
    found = place(ev.cwd, path)
    if found is None:
        return None
    where, state = found
    if not where.has_declaration or where.in_worktree:  # 宣言の無いリポジトリと worktree の中では何もしない
        return None
    if ev.event in ("userPromptSubmit", "UserPromptSubmit"):
        return KIRO_NOTICE
    got = targets(ev)
    if got is None:
        return None
    raw, base, unread = got
    if unread:
        return _output(ev, path, None, UNREAD)
    flagged = _flagged_targets(raw, base, where)
    if not flagged:
        return None
    flagged = _dedupe_notified(path, state, flagged)
    if not flagged:
        return None
    paths = "\n".join(f"  - {r}" for r in flagged)
    return _output(ev, path, SUMMARY, CONTEXT.format(paths=paths, wdir=WORKTREE_DIR))


def _output(ev: pl.Event, path: str | None, summary: str | None, context: str) -> dict:
    if ev.agy:  # 案内は状態ファイルへ積み、次のモデル呼び出しの前に worktree-session.sh が渡す
        state = _load_state(path) if path and os.path.isfile(path) else None
        if state is not None:
            state["pending"] = list(state.get("pending") or []) + [context]
            _save_state(path, state)
        return {"decision": "allow"}
    out: dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
    if summary:
        out = {"systemMessage": summary, **out}
    return out
