"""殻: 工程の担当の 1 回の起動と、結果の回収（#750）。

- 読み取りの工程（表 `loop-phases` の coding が偽）の担当は、読み取り用ディレクトリで読むだけの起動をする。ファイルを
  書けないため、結果は標準出力の最後の JSON（`{<鍵>: [...]}`）で返し、起動の側がそれを回収先へ書く。claude は
  読む Tool だけを許した起動、ほかはランタイムの表の `review_args` の起動である
- コードを書く工程の担当（claude）は書き込み用の作業ディレクトリで動き、結果ファイルを作業ディレクトリの
  `.cross_refactoring/` へ書く。起動の側が終わった後に回収先へ移す。担当は作業ディレクトリの外を書かない
- 担当が結果を出さずに終わったら None を返す（続け方は `phase_decide.on_no_result` が決める）
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import agents, claude
from ndf.contracts import limits
from ndf.core.converge import participants
from ndf.domains.crossrefactor import launch_decide

Launcher = Callable[[agents.Launch], agents.AgentStatus]
# 読み取りの工程の claude に許す Tool（読むだけ）
READ_TOOLS = "Read,Grep,Glob"
# コードを書く工程の claude に許す Tool（リファクタリングは 1 つのファイルの複数箇所を書き換える）
CODE_TOOLS = "Read,Edit,MultiEdit,Write,Bash,Grep,Glob"


@dataclass(frozen=True)
class Request:
    """1 回の起動の頼み。`key` は読み取りの工程の結果 JSON の並びの鍵（コードを書く工程では使わない）。"""

    phase: str
    runtime: str
    prompt: str
    stem: str
    key: str = ""
    timeout: Optional[float] = None


@dataclass(frozen=True)
class Dirs:
    read_root: Path
    work: Path
    logs: Path


def place_of(req: Request, dirs: Dirs) -> launch_decide.ResultPlace:
    return launch_decide.result_place(req.phase, req.runtime, req.stem, (dirs.read_root, dirs.work, dirs.logs))


def _limit(req: Request) -> float:
    return req.timeout if req.timeout is not None else limits.seconds(f"monitor.phase.{req.phase}")


def _read_only(req: Request, workdir: Path, paths_stem: Path) -> agents.Launch:
    if req.runtime == participants.FIXER:
        call = claude.ClaudeCall(system=f"{launch_decide.SKILL} の {req.phase} の担当", permission=claude.Permission.TOOLS, tools=READ_TOOLS)
        return agents.Launch(req.runtime, (*claude.claude_args(call), req.prompt), workdir, agents.AgentPaths.at(paths_stem), _limit(req))
    return participants.review_launch(req.runtime, workdir, req.prompt, paths_stem, _limit(req))


def _coding(req: Request, workdir: Path, paths_stem: Path, submit: Path) -> agents.Launch:
    call = claude.ClaudeCall(system=f"{launch_decide.SKILL} の {req.phase} の担当", permission=claude.Permission.TOOLS, tools=CODE_TOOLS)
    prompt = f"{req.prompt}\n\n終えたら結果の JSON を {submit.relative_to(workdir)} へ書く（作業ディレクトリの外は書かない）。"
    return agents.Launch(req.runtime, (*claude.claude_args(call), prompt), workdir, agents.AgentPaths.at(paths_stem), _limit(req))


def _collect_stdout(out: Path, key: str, collect: Path) -> Optional[dict[str, Any]]:
    found = participants.listed_of(agents.tail(out), key) if out.is_file() else None
    if found is None:
        return None
    got = {key: found}
    collect.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    return got


def _collect_file(submit: Path, collect: Path) -> Optional[dict[str, Any]]:
    if not submit.is_file():
        return None
    shutil.move(str(submit), str(collect))
    try:
        data = json.loads(collect.read_text(encoding="utf-8"))
    except ValueError:  # noqa: NDF401 ファイル: 担当が書いた結果が JSON でなければ結果が無いとして扱う
        return None
    return data if isinstance(data, dict) else None


def run(req: Request, dirs: Dirs, launcher: Launcher = agents.run_agent) -> tuple[Optional[dict[str, Any]], agents.AgentStatus]:
    """担当を起動して結果を回収する。返すのは (結果。出さなければ None, 監視の結果)。"""
    where = place_of(req, dirs)
    stem = dirs.logs / f"{req.runtime}-{req.phase}-{req.stem}"
    read_only = req.phase in launch_decide.read_only_phases()
    if read_only:
        spec = _read_only(req, Path(where.workdir), stem)
    else:
        Path(where.submit).parent.mkdir(parents=True, exist_ok=True)
        spec = _coding(req, Path(where.workdir), stem, Path(where.submit))
    status = launcher(spec)
    if read_only:
        return _collect_stdout(spec.paths.out, req.key, Path(where.collect)), status
    return _collect_file(Path(where.submit), Path(where.collect)), status
