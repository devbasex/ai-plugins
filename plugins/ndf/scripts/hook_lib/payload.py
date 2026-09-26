"""hook の入力を 4 ランタイム（Claude Code・Codex CLI・Kiro CLI・agy）で同じ形へ直す。

agy は事象名を持たず、Tool の名前と引数を `toolCall` へ入れる。作業ディレクトリは `workspacePaths` の先頭、
セッションの識別子は `conversationId` にある。項目の名前がほかの 3 つと重ならないため、Tool の名前がどちらに
入っているかで見分ける。Tool の名前はランタイムごとに違うので、ここで種別（編集・パッチ・シェル）へまとめる。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# hook の matcher も同じ一覧から作る（`hooks/*.json` の PreToolUse。テストが突き合わせる）
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit", "fs_write", "edit_file", "write_file",
              "str_replace_editor", "replace", "write_to_file", "replace_file_content")
PATCH_TOOLS = ("apply_patch",)
SHELL_TOOLS = ("Bash", "shell", "execute_bash", "local_shell", "run_command", "run_shell_command")


def tool_matcher() -> str:
    return "|".join((*EDIT_TOOLS, *PATCH_TOOLS, *SHELL_TOOLS))


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _as_dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else []


@dataclass
class Event:
    raw: dict
    event: str
    tool: str
    cwd: str
    session: str
    agy: bool

    @property
    def tool_input(self) -> dict:
        return _as_dict(self.raw.get("tool_input"))

    @property
    def agy_args(self) -> dict:
        return _as_dict(_as_dict(self.raw.get("toolCall")).get("args"))

    @property
    def tool_kind(self) -> str:
        if self.tool in PATCH_TOOLS:
            return "patch"
        if self.tool in EDIT_TOOLS:
            return "edit"
        if self.tool in SHELL_TOOLS:
            return "shell"
        return ""

    def field(self, name: str) -> str:
        """最上位の項目を文字列で（無ければ空）。"""
        v = self.raw.get(name)
        return "" if v is None else (v if isinstance(v, str) else str(v))

    def patch_text(self) -> str:
        ti = self.tool_input
        return next((ti[k] for k in ("command", "patch", "input") if isinstance(ti.get(k), str)), "")

    def edit_paths(self) -> list[str]:
        ti = self.tool_input
        found = [ti.get("file_path"), ti.get("path"), ti.get("notebook_path")]
        found += [_as_dict(e).get("file_path") for e in _as_list(ti.get("edits"))]
        found += [_as_dict(o).get("path") for o in _as_list(ti.get("operations"))]
        found.append(self.agy_args.get("TargetFile"))
        return sorted({p for p in found if isinstance(p, str) and p})

    def command_text(self) -> str:
        cmd = self.tool_input.get("command")
        if isinstance(cmd, list):
            return " ".join(str(w) for w in cmd)
        if isinstance(cmd, str):
            return cmd
        return _as_str(self.agy_args.get("CommandLine"))

    def command_cwd(self) -> str:
        """コマンドの実行ディレクトリの指定（`run_shell_command` は `dir_path`、agy は `Cwd`）。無ければ空。"""
        ti = self.tool_input
        for k in ("dir_path", "cwd", "workdir"):
            if isinstance(ti.get(k), str):
                return ti[k]
        return _as_str(self.agy_args.get("Cwd"))


def event_of(raw: dict) -> Event:
    tool, cwd = _as_str(raw.get("tool_name")), _as_str(raw.get("cwd"))
    agy = False
    if not tool:
        name = _as_str(_as_dict(raw.get("toolCall")).get("name"))
        if name:
            agy, tool = True, name
            paths = raw.get("workspacePaths")
            if not cwd and isinstance(paths, list) and paths and isinstance(paths[0], str):
                cwd = paths[0]
    session = _as_str(raw.get("session_id")) or _as_str(raw.get("conversationId")) or os.environ.get("KIRO_SESSION_ID", "")
    here = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
    return Event(raw, _as_str(raw.get("hook_event_name")), tool, os.path.realpath(here), session, agy)
