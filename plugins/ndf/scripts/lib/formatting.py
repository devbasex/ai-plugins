"""整形の宣言を読み、当てて、整形で変わったファイルだけをコミットする（#1651 #1507 の決定 4・7・8）。

整形のコマンドはプロジェクトの宣言 `.ndf/supervise.json` の `format`（`[{"name", "command"}, ...]`）だけが持つ。
実装のプランの `format` のステップ（`supervise.py format --commit`）と、cross-review の修正の送信（`result_posts.push_fix`）が
同じ関数（`format_commands`）で同じ宣言を読む。宣言が無ければ、どちらも整形しない。

コマンドの終了コードの約束: 0 = 整形の後に違反が残らない / 1 = 整形で直らない違反が残る / それ以外 = 仕組みの失敗。
コミットに入れるのは、整形の前に変更の無かった追跡ファイルのうち整形で変わったものだけである（I6）。
コミットは上に足し、前のコミットを書き換えない（I5）。外部パッケージを要しない。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import NamedTuple

DECL = Path(".ndf") / "supervise.json"
KEY = "format"
OK, VIOLATIONS, BROKEN = "ok", "violations", "broken"
TAIL = 1500  # 出力の末尾として残す文字数


class FormatError(ValueError):
    """整形の宣言が読めない・形が違う。"""


class FormatCommand(NamedTuple):
    name: str
    command: str


class FormatOutcome(NamedTuple):
    """整形の結果。`runs` はコマンドごとの `{name, result, exit, tail}`、`skipped` は整形の前から変更があり、
    整形でも変わったためコミットに入れなかったファイル。"""

    status: str
    commit: str | None = None
    files: list[str] = []
    failed: str | None = None
    tail: str = ""
    runs: list[dict] = []
    skipped: list[str] = []


def format_commands(decl: dict) -> list[FormatCommand]:
    """宣言（supervise.json の中身）の `format` を並びで返す。無い・空・null は []。形が違えば FormatError。"""
    raw = (decl or {}).get(KEY)
    if raw in (None, []):
        return []
    hint = f'（{KEY} は {{"name", "command"}} の並びで書く）'
    if not isinstance(raw, list):
        raise FormatError(f"supervise.json: {KEY} が並びでない{hint}")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise FormatError(f"supervise.json: {KEY}[{i}] がオブジェクトでない{hint}")
        name, command = item.get("name"), item.get("command")
        if not (isinstance(name, str) and name and isinstance(command, str) and command):
            raise FormatError(f"supervise.json: {KEY}[{i}] に name か command が無い{hint}")
        out.append(FormatCommand(name, command))
    return out


def read_format_commands(root: Path | str) -> list[FormatCommand]:
    """根の `.ndf/supervise.json` の `format`。宣言のファイルが無ければ []。読めない・形が違えば FormatError。"""
    f = Path(root) / DECL
    if not f.is_file():
        return []
    try:
        decl = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FormatError(f"{f}: JSON として読めない: {e}") from e
    if not isinstance(decl, dict):
        raise FormatError(f"{f}: 最上位がオブジェクトでない")
    return format_commands(decl)


def _format_git(root: Path | str, *args: str) -> list[str] | None:
    """git の標準出力の行。0 以外で終われば None。"""
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.stdout.splitlines() if p.returncode == 0 else None


def _dirty(root: Path | str) -> set[str] | None:
    """HEAD から変わっている追跡ファイル（索引と作業ツリーの両方）。git が落ちたら None。"""
    # -z: core.quotePath の引用・エスケープを受けず、実際のファイル名を NUL 区切りで読む
    p = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", "HEAD"], capture_output=True, text=True)
    return {f for f in p.stdout.split("\0") if f} if p.returncode == 0 else None


def _contents(root: Path | str, paths: set[str]) -> dict[str, bytes | None]:
    out: dict[str, bytes | None] = {}
    for rel in paths:
        try:
            out[rel] = (Path(root) / rel).read_bytes()
        except OSError:
            out[rel] = None
    return out


def apply_format(root: Path | str, commands: list[FormatCommand], commit: bool = True) -> FormatOutcome:
    """宣言の整形を根で順に打つ。すべて 0 なら、commit のとき整形で変わったファイルを 1 つのコミットにする。

    どれかが 1 なら `violations`、0 と 1 以外・git が落ちた・コミットが落ちたなら `broken` で、そのコマンドで止まる。"""
    before = _dirty(root)
    if before is None:
        return FormatOutcome(BROKEN, failed="git", tail="git diff を読めない（作業ツリーでないか、HEAD が無い）")
    held = _contents(root, before)
    runs = []
    for c in commands:
        p = subprocess.run(c.command, shell=True, cwd=str(root), capture_output=True, text=True)
        tail = (p.stdout + p.stderr).strip()[-TAIL:]
        runs.append(
            {"name": c.name, "result": "ok" if p.returncode == 0 else "failed", "exit": p.returncode, "tail": tail if p.returncode else ""}
        )
        if p.returncode != 0:
            return FormatOutcome(VIOLATIONS if p.returncode == 1 else BROKEN, failed=c.name, tail=tail, runs=runs)
    after = _dirty(root)
    if after is None:
        return FormatOutcome(BROKEN, failed="git", tail="整形の後に git diff を読めない", runs=runs)
    changed = sorted(after - before)
    skipped = sorted(f for f, body in _contents(root, after & before).items() if body != held.get(f))
    if not commit or not changed:
        return FormatOutcome(OK, files=changed, runs=runs, skipped=skipped)
    names = "・".join(c.name for c in commands)
    # パスを渡したコミットはそのパスだけを入れる（前から索引にあった変更を巻き込まない）。
    # --literal-pathspecs: `[id]` などを glob と読まず、列挙したファイル名だけに当てる
    msg = f"Update: 宣言の整形を当てる（{names}）"
    done = subprocess.run(
        ["git", "-C", str(root), "--literal-pathspecs", "commit", "-q", "-m", msg, "--", *changed], capture_output=True, text=True
    )
    if done.returncode != 0:
        return FormatOutcome(BROKEN, files=changed, failed="commit", tail=(done.stdout + done.stderr).strip()[-TAIL:], runs=runs)
    head = _format_git(root, "rev-parse", "HEAD") or [""]
    return FormatOutcome(OK, commit=head[0] or None, files=changed, runs=runs, skipped=skipped)


def format_before_push(root: Path | str) -> str:
    """送る前に宣言の整形を当ててコミットする。送れないときはその理由（送ってよければ空）。宣言が無ければ何もしない。"""
    try:
        commands = read_format_commands(root)
    except FormatError as e:
        return f"整形の宣言を読めない: {e}"
    out = apply_format(root, commands) if commands else FormatOutcome(OK)
    if out.status == OK:
        return ""
    why = "整形で直らない違反が残る" if out.status == VIOLATIONS else "整形を当てられない"
    return f"{why}（{out.failed}）: {out.tail[-300:]}"
