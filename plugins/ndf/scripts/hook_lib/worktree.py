"""メインディレクトリを編集しようとしたときに、worktree で作業する旨を伝える guard（#1142 の決定 20）。

結線先は 3 つある（`hooks/*.json`・`dev.kiro/install.sh`・`dev.agy/hooks.json`）。
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
import tempfile
from dataclasses import dataclass, field
from typing import Any

from . import payload as pl
from . import write_target as wt

DEFAULT_ALLOW_PATHS = ["issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".agents/", ".serena/", ".ndf/",
                       ".gitignore"]
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
UNREAD = ("このコマンドは構文を読み切れないため、主ディレクトリへの書き込みかを判定していません（止めてはいません）。"
          f"書き込むなら、開発の変更は {WORKTREE_DIR}/<ブランチ名> の作業ツリーの中で行ってください（/ndf:worktree）。")


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


def resolve(cwd: str) -> tuple[str, bool] | None:
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
    try:
        with open(os.path.join(main_dir, DECLARATION_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
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
    return path[len(main_dir) + 1:] if path.startswith(main_dir + "/") else None


# --- セッションの状態ファイル（worktree-session.sh が `pending` を読む） ---------------------------------------

def state_file(session: str) -> str | None:
    if not session:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session)
    return os.path.join(os.environ.get("TMPDIR") or "/tmp", f"ndf-worktree-{safe}.json")


def _read_state(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(path: str, data: dict[str, Any]) -> None:
    try:
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=os.path.dirname(path) or ".")
    except OSError:
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def place(cwd: str, path: str | None) -> tuple[Place, dict] | None:
    """位置と宣言の控え（Tool の呼び出しのたびに git を起動しない）。解決したときの cwd と宣言の目印が合えば使う。"""
    state = _read_state(path)
    if state and state.get("resolved_from") == cwd and state.get("main_dir"):
        if state.get("declaration_stamp", "") == declaration_stamp(state["main_dir"]):
            allow = [x for x in state.get("allow_paths") or [] if isinstance(x, str)]
            return Place(state["main_dir"], bool(state.get("in_worktree")), bool(state.get("has_declaration")),
                         allow), state
    found = resolve(cwd)
    if found is None:
        return None
    decl = declaration(found[0])
    p = Place(found[0], found[1], decl is not None, allow_paths(decl))
    state = {"main_dir": p.main_dir, "resolved_from": cwd, "in_worktree": p.in_worktree,
             "has_declaration": p.has_declaration, "declaration_stamp": declaration_stamp(p.main_dir),
             "allow_paths": [x for x in p.allow_paths if x], "notified": [], "pending": []}
    if path:
        _write_state(path, state)
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
        if shparse.unreadable(shparse.parse_bash(cmd)):
            return [], base, True
        return wt.shell_targets(cmd, base), base, False
    return None


def guard(ev: pl.Event) -> dict | str | None:
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
    flagged = []
    for r in raw:
        if not r:
            continue
        rel = relative_to_main(wt.normalize_path(r, base), where.main_dir)
        if rel is None or rel == "." or rel.startswith(WORKTREE_DIR + "/"):
            continue
        if not is_allowed(rel, where.allow_paths):
            flagged.append(rel)
    if not flagged:
        return None
    if path and os.path.isfile(path):
        seen = set(state.get("notified") or [])
        flagged = [r for r in flagged if r not in seen]
        if not flagged:
            return None
        state = _read_state(path) or state
        state["notified"] = sorted(set(state.get("notified") or []) | set(flagged))
        _write_state(path, state)
    paths = "\n".join(f"  - {r}" for r in flagged)
    return _output(ev, path, SUMMARY, CONTEXT.format(paths=paths, wdir=WORKTREE_DIR))


def _output(ev: pl.Event, path: str | None, summary: str | None, context: str) -> dict:
    if ev.agy:  # 案内は状態ファイルへ積み、次のモデル呼び出しの前に worktree-session.sh が渡す
        state = _read_state(path) if path and os.path.isfile(path) else None
        if state is not None:
            state["pending"] = list(state.get("pending") or []) + [context]
            _write_state(path, state)
        return {"decision": "allow"}
    out: dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
    if summary:
        out = {"systemMessage": summary, **out}
    return out
