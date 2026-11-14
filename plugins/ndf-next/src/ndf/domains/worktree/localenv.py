"""worktree でのローカルの動作検証（宣言の `localenv` 節。実行系は `kind: compose` だけを扱う）。

- `setup`: 宣言の `localenv.copy_from_main`・`copy_as_real` に従って設定と依存物を worktree へ複製する（複製の手順は `deps` と同じ）
- `verify`: ローカル環境に載っているコードと対象の worktree のブランチを照合する（判断は `localenv_decide.verify`）
- `healthcheck`: 照合が一致したときだけ、宣言の `localenv.healthcheck` をメインディレクトリで打つ
- `aim`: ローカル環境が指すコードを対象の worktree へ向ける（`layout: indirect` だけ。`direct`・`host` は手順書で行う）
- `mode`: 対象の変更の一覧から、相乗りか分離かを提示する（判断は `localenv_decide.mode`）

宣言・`localenv` 節が無い、または `kind` が `compose` でないリポジトリでは、どれも `None`（適用外）を返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git
from ndf.domains.worktree import container, declaration, deps, localenv_decide, repo

SECTION = declaration.LOCALENV
KIND = "compose"


class LocalenvError(Exception):
    """処理できない（宣言の値が足りない・実行系が無い・複製やコマンドが失敗した）。"""


@dataclass(frozen=True)
class Target:
    """対象の worktree と、メインディレクトリ・`localenv` 節。"""

    path: Path
    main_dir: Path
    section: dict[str, Any]

    def text(self, key: str) -> Optional[str]:
        value = self.section.get(key)
        return value if isinstance(value, str) and value else None

    def strings(self, key: str) -> list[str]:
        value = self.section.get(key)
        return [x for x in value if isinstance(x, str) and x] if isinstance(value, list) else []


def target(path: Path) -> Optional[Target]:
    """対象。適用外なら `None`。"""
    main_dir = repo.locate(Git(path)).main_dir
    decl = declaration.load(main_dir)
    section = decl.raw.get(SECTION) if decl is not None else None
    if not isinstance(section, dict) or section.get("kind") != KIND:
        return None
    return Target(path, main_dir, section)


def setup(t: Target) -> int:
    """複製し、複製した件数を返す。失敗は `LocalenvError`。"""
    count = 0
    for kind in deps.STEPS[:2]:
        for rel in t.strings(kind):
            why = deps.copy_step(t.main_dir, t.path, rel, kind, redo=False)
            if why is not None:
                raise LocalenvError(f"中断: {kind} の {why}")
            count += 1
    return count


def verify(t: Target, shell: container.Shell) -> localenv_decide.Verdict:
    probe = t.text("branch_probe")
    branch = repo.current_branch(Git(t.path))
    if probe is None:
        return localenv_decide.verify(None, branch)
    done = shell.sh(probe, t.main_dir)
    return localenv_decide.verify(done.stdout.replace("\r", "") if done.returncode == 0 else None, branch)


def healthcheck(t: Target, shell: container.Shell) -> tuple[localenv_decide.Verdict, Optional[int]]:
    """照合の結果と、動作確認のコマンドの終了コード（打たなければ `None`）。"""
    got = verify(t, shell)
    command = t.text("healthcheck")
    if got.result != localenv_decide.MATCH or command is None:
        return got, None
    return got, shell.sh(command, t.main_dir).returncode


def _build(t: Target, shell: container.Shell) -> None:
    for build in t.strings("build_before_aim"):
        if shell.sh(build, t.path).returncode != 0:
            raise LocalenvError(f"資産のビルドに失敗した: {build}")


def _relink(t: Target, shell: container.Shell, project: str, src_target: str) -> int:
    """コードの位置がメインディレクトリかその下を指すコンテナだけを、対象へ張り替える。張り替えた数を返す。"""
    main, moved = str(t.main_dir), 0
    for name in shell.containers(project):
        link = shell.docker_run("exec", name, "readlink", "--", src_target).stdout.strip()
        if link != main and not link.startswith(main + "/"):
            continue
        if shell.docker_run("exec", "-u", "root", name, "ln", "-sfn", "--", str(t.path), src_target).returncode != 0:
            raise LocalenvError(f"コンテナ {name} のコードの位置を張り替えられない")
        moved += 1
    return moved


def _reload(t: Target, shell: container.Shell, project: str) -> None:
    signal = t.section.get("reload_signal")
    service = t.text("app_service")
    if not isinstance(signal, dict) or service is None or not signal.get("process") or not signal.get("signal"):
        return
    for name in shell.containers(project, service):
        # 再読み込みを促すだけで、プロセスが無いことは失敗にしない
        shell.docker_run("exec", "-u", "root", name, "pkill", f"-{signal['signal']}", "-x", "--", str(signal["process"]))


def aim(t: Target, shell: container.Shell) -> int:
    """ローカル環境が指すコードを対象へ向け、張り替えたコンテナの数を返す。失敗は `LocalenvError`。"""
    if not t.path.is_dir():
        raise LocalenvError(f"worktree が無い: {t.path}")
    layout, src_target = t.text("layout"), t.text("src_target")
    if layout in ("direct", "host"):
        raise LocalenvError(f"{layout} 型の切り替えは worktree の手順書（ローカル環境）の手順で行う")
    if layout != "indirect":
        raise LocalenvError("localenv.layout が indirect・direct・host のどれでもない")
    if src_target is None:
        raise LocalenvError("localenv.src_target が要る")
    project = container.compose_project(t.main_dir.name)
    if project is None:
        raise LocalenvError("コンテナのプロジェクト名を決められない")
    if not shell.has_docker():
        raise LocalenvError("コンテナの実行系が見つからない")
    _build(t, shell)
    moved = _relink(t, shell, project, src_target)
    _reload(t, shell, project)
    return moved


def changed_paths(path: Path) -> list[str]:
    """変更のパス（追跡していないものを含む）。改名と複製は変更前のパスも含める（外へ移す変更を見落とさない）。"""
    fields = Git(path).run("status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    out, i = [], 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        out.append(entry[3:])
        if entry[0] in "RC" and i < len(fields):
            out.append(fields[i])
            i += 1
    return out


def mode(t: Target) -> tuple[localenv_decide.Verdict, list[str]]:
    return localenv_decide.mode(changed_paths(t.path), t.strings("isolate_when"))
