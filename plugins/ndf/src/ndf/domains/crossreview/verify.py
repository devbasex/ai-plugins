"""判定役を 1 回起動して回答を返す（#4363）。

判定役は、修正担当・相手役とは別の、読むだけの claude の起動である（`participants.read_only_launch`）。読める場所は
レビューする worktree と、この実行の資料のディレクトリ（差分と指摘の一覧のファイル、承認済みの MVV の写し）だけである。
指摘の文はプロセスの引数に載せず一覧のファイルで渡し、一覧と判定役の出力は実行者だけが読める権限にする。どの指摘を
渡すかと回答の読み方は `verify_decide`、回答を指摘の行へ当てるのは呼び手の `drive` である。

失敗しても例外で駆動を止めず、起動し直さない。回答なしと注意にする（どの指摘も下がらない）。注意は PR の記録に載るので、
手元の絶対パス・指摘の本文・回答の中身を書かない。詳しい原因は過程にだけ残す。
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import agents
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, verify_decide
from ndf.domains.crossreview.fixer import Launcher
from ndf.domains.crossreview.settings import RunConfig
from ndf.domains.records import mvv


class VerifyRun(Protocol):
    """判定役の起動が読み書きする、1 回の起動の値（`drive._Run`）。"""

    cfg: RunConfig
    snapshot: Optional[mvv.Snapshot]
    documents: bool

    def round_stem(self, round_no: int, suffix: str) -> Path: ...

    def note(self, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None: ...


def _readable_mvv(snap: Optional[mvv.Snapshot], into: Path) -> Optional[mvv.Snapshot]:
    """承認済みの MVV なら、本文と共通原則を資料のディレクトリ `into` へ写し、写しを指すスナップショットを返す。元のファイルは
    利用者の状態の保存先にあり判定役が読めないためで、読める場所は増やさない。写せなければ `OSError`。"""
    held = criteria_decide.approved(snap)
    if held is None or held.path is None or held.principles_path is None:
        return snap
    copies = []
    for source in (held.path, held.principles_path):
        copies.append(into / source.name)
        shutil.copyfile(source, copies[-1])
    return held._replace(path=copies[0], principles_path=copies[1])


def _private_file(path: Path) -> Path:
    """`path` を実行者だけが読み書きできるファイルにして返す（無ければ空で作る）。後から開いて書いても権限は変わらない。"""
    path.touch(mode=0o600)
    path.chmod(0o600)
    return path


def _unanswered(run: VerifyRun, round_no: int, count: int, why: str, warning: str) -> verify_decide.Answers:
    """回答の得られなかった起動。原因 `why` は過程にだけ残す。"""
    run.note(round_no, "verify", f"判定役へ {count} 件、回答無し（{why}）")
    return verify_decide.Answers({}, (warning,))


def ask(run: VerifyRun, round_no: int, rows: Sequence[Mapping[str, Any]], diff: Path, launch: Launcher) -> verify_decide.Answers:
    """指摘 `rows`（空でない）を判定役の 1 回の起動で確かめる。`diff` は指摘を書いた相手役が読んだ差分で、資料のディレクトリ
    にある。"""
    count = len(rows)
    listed = diff.parent / f"round{round_no}-verify-findings.jsonl"
    try:
        snap = _readable_mvv(run.snapshot, diff.parent)
        _private_file(listed).write_text(verify_decide.listing(rows), encoding="utf-8")
        criteria = [
            *criteria_decide.criteria_lines(run.cfg.settings.focus, snap),
            *criteria_decide.document_line(run.documents, rows),
        ]
        prompt = verify_decide.instruction(listed, diff, run.cfg.worktree, criteria)
        system = str(criteria_decide.verify_text()["system"])
        stem = run.round_stem(round_no, "-verify")
        spec = participants.read_only_launch(system, run.cfg.worktree, prompt, stem, readable=(diff.parent,))
        # 回答は非公開の内容を含みうる。起動の前に作り、umask によらず実行者だけが読めるようにする
        for output in (spec.paths.out, spec.paths.err):
            _private_file(output)
    except OSError as exc:
        warning = "判定役の資料（指摘の一覧・MVV の本文と共通原則の写し）か出力のファイルを用意できないので、判定役を起動せず下げない"
        return _unanswered(run, round_no, count, f"起動していない: {exc}", warning)
    status = launch(spec)
    if status.state != agents.AgentState.DONE:
        warning = f"判定役が終わらなかった（{status.state}）ので下げない"
        return _unanswered(run, round_no, count, f"{status.state}: {status.detail}", warning)
    try:
        answer = participants.claude_answer(spec.paths.out)
    except OSError:  # 出力の無い判定役は、回答を読めなかった扱いにする
        answer = participants.ClaudeAnswer("", ())
    denied = participants.denied_reads(answer.denials, (diff, listed), spec.cwd)
    if denied:
        warning = f"判定役が資料のファイル {'・'.join(p.name for p in denied)} の読み取りを拒否されたので下げない"
        return _unanswered(run, round_no, count, f"permission_denials: {' / '.join(map(str, denied))}", warning)
    ids = [str(row["id"]) for row in rows]
    answers = verify_decide.read(participants.listed_of(answer.text, "verdicts"), ids)
    valid = sum(1 for verdict in answers.verdicts.values() if verdict.holds is not None)
    run.note(round_no, "verify", f"判定役へ {count} 件、有効な回答 {valid} 件")
    return answers
