"""殻: 工程の担当の 1 回の起動と、結果の回収（#750）。

- 読み取りの工程（表 `loop-phases` の coding が偽）の担当は、読み取り用ディレクトリで読むだけの起動をする。ファイルを
  書けないため、結果は標準出力の最後の JSON（`{<キー>: [...]}`）で返し、起動の側がそれを回収先へ書く。claude は
  読む Tool だけを許した起動で、`--output-format json` の外側の JSON の `result` の本文から結果を探す。ほかは
  ランタイムの表の `review_args` の起動である
- コードを書く工程の担当（claude）は書き込み用の作業ディレクトリで動き、結果ファイルを作業ディレクトリの
  `.cross_refactoring/` へ書く。起動の側が終わった後に回収先へ移す。担当は作業ディレクトリの外を書かない。
  `--output-format json` は完了まで標準出力を出さないため、担当は同じ `.cross_refactoring/` の進捗ファイルへ
  作業の途中で追記し、無進捗の監視はそのファイルの増え方も測る
- 担当が結果を出さずに終わったら None を返す（続け方は `phase_decide.on_no_result` が決める）
- 起動の費用は担当の標準出力の報告から読む（`cost_decide`）。claude 以外のランタイムは費用を報告しないので不明（None）
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import agents, claude
from ndf.contracts import limits
from ndf.core.converge import participants
from ndf.domains.crossrefactor import cost_decide, launch_decide

Launcher = Callable[[agents.Launch], agents.AgentStatus]
# コードを書く工程の claude に許す Tool（リファクタリングは 1 つのファイルの複数箇所を書き換える）
CODE_TOOLS = "Read,Edit,MultiEdit,Write,Bash,Grep,Glob"


@dataclass(frozen=True)
class Request:
    """1 回の起動の頼み。`key` は読み取りの工程の結果 JSON の並びのキー（コードを書く工程では使わない）。"""

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


def location_of(req: Request, dirs: Dirs) -> launch_decide.ResultPlace:
    return launch_decide.result_location(req.phase, req.runtime, req.stem, (dirs.read_root, dirs.work, dirs.logs))


def phase_limit(phase: str) -> float:
    """工程の監視の上限の秒（設定 `monitor.phase.<工程>`）。"""
    return limits.seconds(f"monitor.phase.{phase}")


def _limit(req: Request) -> float:
    return req.timeout if req.timeout is not None else phase_limit(req.phase)


def _read_only(req: Request, workdir: Path, paths_stem: Path) -> agents.Launch:
    reader = participants.ClaudeReader(f"{launch_decide.SKILL} の {req.phase} の担当")
    return participants.reading_launch(req.runtime, reader, workdir, req.prompt, paths_stem, _limit(req))


def _coding(req: Request, workdir: Path, paths_stem: Path, location: launch_decide.ResultPlace) -> agents.Launch:
    call = claude.ClaudeCall(system=f"{launch_decide.SKILL} の {req.phase} の担当", permission=claude.Permission.TOOLS, tools=CODE_TOOLS)
    submit, progress = Path(location.submit), Path(location.progress)
    prompt = (
        f"{req.prompt}\n\n作業の途中は、ファイルを編集するたび・テストを打つたびに、何をしたかを 1 行で "
        f"{progress.relative_to(workdir)} へ追記する（追記が止まると無進捗として止められる）。\n"
        f"終えたら結果の JSON を {submit.relative_to(workdir)} へ書く（作業ディレクトリの外は書かない）。"
    )
    paths = replace(agents.AgentPaths.at(paths_stem), progress=progress)
    return agents.Launch(req.runtime, (*claude.claude_args(call), prompt), workdir, paths, _limit(req))


def _collect_stdout(req: Request, out: Path, collect: Path) -> Optional[dict[str, Any]]:
    key = req.key
    found = participants.listed_of(participants.answer_text(req.runtime, out), key) if out.is_file() else None
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


def _stem(req: Request, dirs: Dirs) -> Path:
    return dirs.logs / f"{req.runtime}-{req.phase}-{req.stem}"


def cost(req: Request, dirs: Dirs) -> Optional[float]:
    """終わった起動の費用（USD）。claude 以外のランタイム・出力が無い・報告が無いときは None（不明）。"""
    out = agents.AgentPaths.at(_stem(req, dirs)).out
    if req.runtime != participants.FIXER or not out.is_file():
        return None
    return cost_decide.reported(out.read_text(encoding="utf-8", errors="replace"))


def run(req: Request, dirs: Dirs, launcher: Launcher = agents.run_agent) -> tuple[Optional[dict[str, Any]], agents.AgentStatus]:
    """担当を起動して結果を回収する。返すのは (結果。出さなければ None, 監視の結果)。"""
    where = location_of(req, dirs)
    stem = _stem(req, dirs)
    read_only = req.phase in launch_decide.read_only_phases()
    if read_only:
        spec = _read_only(req, Path(where.workdir), stem)
    else:
        Path(where.submit).parent.mkdir(parents=True, exist_ok=True)
        spec = _coding(req, Path(where.workdir), stem, where)
    status = launcher(spec)
    if read_only:
        return _collect_stdout(req, spec.paths.out, Path(where.collect)), status
    return _collect_file(Path(where.submit), Path(where.collect)), status
