"""判定役を 1 回起動して回答を返す（#4363）。

判定役は、修正担当・相手役とは別の、読むだけの claude の起動である（`participants.read_only_launch`）。読める場所は
レビューする worktree と、この実行の資料のディレクトリ（差分と指摘の一覧のファイル、承認済みの MVV の写し）だけである。
指摘の文はプロセスの引数に載せず一覧のファイルで渡す。一覧は、実行者だけが開ける資料のディレクトリに置く。判定役の出力は
起動ごとに新しく作る実行者だけが開けるディレクトリに書かせ、起動の後で記録を起動ごとの名前で状態の保存先の下の
記録専用のディレクトリ（`RECORD_DIR`）へ移して、起動ごとのディレクトリを消す。そこで残す期間（`RECORD_KEEP`）を過ぎた記録は、次に判定役を
起動するときに消す。どの指摘を渡すかと回答の読み方は `verify_decide`、回答を指摘の行へ当てるのは呼び手の `drive` である。

失敗しても例外で駆動を止めず、起動し直さない。回答なしと注意にする（どの指摘も下がらない）。注意は PR の記録に載るので、
手元の絶対パス・指摘の本文・回答の中身を書かない。詳しい原因は過程にだけ残す。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import agents
from ndf.contracts import limits
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, verify_decide
from ndf.domains.crossreview.fixer import Launcher
from ndf.domains.crossreview.settings import RunConfig
from ndf.domains.records import mvv

# 判定役の記録だけを置く、状態の保存先の下のディレクトリ。古い記録の片付けはこの中だけを見る
RECORD_DIR = "ndf-judge-records"
# 判定役の記録を残す秒
RECORD_KEEP = "cross-review.judge-record.keep"
# `_keep_records` が付ける記録の名前（接尾辞は `tempfile.mkdtemp` の 8 字）。`RECORD_DIR` の中の、この形のファイルだけを古さで消す
_RECORD = re.compile(r"round\d+-verify-[a-z0-9_]{8}(\.out|\.err\.log|-launch\.json|-progress\.log)")


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


def _keep_records(run: VerifyRun, round_no: int, held: Path, stem: Path) -> None:
    """起動ごとのディレクトリ `held` の記録を、実行者だけが読めるファイルにして `stem` の隣の `RECORD_DIR` へ移し、`held` を消す。
    移し先の名前は、`stem` の名前の部分を `held` の名前（起動ごとに違う）に替えたものである。前からある同じ名前のファイルやリンクは
    置き換えない（`os.link` は移し先があれば失敗する）。移せない記録はディレクトリと一緒に消える。"""
    kept = stem.parent / RECORD_DIR
    try:
        if kept.is_symlink():  # リンクの先は NDF の記録の場所でないので、書かない
            raise OSError("シンボリックリンクである")
        kept.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録のディレクトリ {RECORD_DIR} を使えず、記録を残さない（{exc}）")
        shutil.rmtree(held, ignore_errors=True)
        return
    for record in held.iterdir():
        if record.is_symlink() or not record.is_file():
            continue
        name = held.name + record.name.removeprefix(stem.name)
        try:
            record.chmod(0o600)
            os.link(record, kept / name)
        except OSError as exc:  # 診断の記録を移せなくても、判定の結果と駆動は変えない
            run.note(round_no, "verify", f"判定役の記録 {name} を移せない（{exc}）")
    shutil.rmtree(held, ignore_errors=True)


def _drop_old_records(run: VerifyRun, round_no: int, state_dir: Path) -> None:
    """状態の保存先 `state_dir` の `RECORD_DIR` にある判定役の記録のうち、残す期間を過ぎた通常のファイル（リンクは除く）を消す。
    `RECORD_DIR` の外と、`RECORD_DIR` がリンクのときは何も消さない。"""
    kept = state_dir / RECORD_DIR
    if kept.is_symlink() or not kept.is_dir():
        return
    cutoff = time.time() - limits.seconds(RECORD_KEEP)
    for record in kept.iterdir():
        if not _RECORD.fullmatch(record.name) or record.is_symlink() or not record.is_file():
            continue
        try:
            if record.stat().st_mtime < cutoff:
                record.unlink()
        except OSError as exc:  # 古い記録を消せなくても、判定の結果と駆動は変えない
            run.note(round_no, "verify", f"古い判定役の記録 {record.name} を消せない（{exc}）")


def _unanswered(run: VerifyRun, round_no: int, count: int, why: str, warning: str) -> verify_decide.Answers:
    """回答の得られなかった起動。原因 `why` は過程にだけ残す。"""
    run.note(round_no, "verify", f"判定役へ {count} 件、回答無し（{why}）")
    return verify_decide.Answers({}, (warning,))


def ask(
    run: VerifyRun, round_no: int, rows: Sequence[Mapping[str, Any]], diffs: Mapping[str, Sequence[Path]], launch: Launcher
) -> verify_decide.Answers:
    """指摘 `rows`（空でない）を判定役の 1 回の起動で確かめる。`diffs` は指摘の番号ごとの、その指摘を出した相手役が読んだ
    差分のファイルで、どれも資料のディレクトリにある。"""
    count = len(rows)
    used = tuple(dict.fromkeys(diff for row in rows for diff in diffs[str(row["id"])]))
    # 差分が 1 つなら、行ごとに繰り返し書かない
    shown = rows if len(used) == 1 else [{**row, "diffs": [str(d) for d in diffs[str(row["id"])]]} for row in rows]
    into = used[0].parent
    listed = into / f"round{round_no}-verify-findings.jsonl"
    stem = run.round_stem(round_no, "-verify")
    _drop_old_records(run, round_no, stem.parent)
    try:
        snap = _readable_mvv(run.snapshot, into)
        listed.write_text(verify_decide.listing(shown), encoding="utf-8")
        criteria = [
            *criteria_decide.criteria_lines(run.cfg.settings.focus, snap),
            *criteria_decide.document_line(run.documents, rows),
        ]
        prompt = verify_decide.instruction(listed, used, run.cfg.worktree, criteria)
        # 回答は非公開の内容を含みうる。出力の先は新しく作り（0700）、前からあるファイルやリンクを開かない
        held = Path(tempfile.mkdtemp(prefix=f"{stem.name}-", dir=stem.parent))
    except OSError as exc:
        warning = "判定役の資料（指摘の一覧・MVV の本文と共通原則の写し）か出力の保存先を用意できないので、判定役を起動せず下げない"
        return _unanswered(run, round_no, count, f"起動していない: {exc}", warning)
    system = str(criteria_decide.verify_text()["system"])
    spec = participants.read_only_launch(system, run.cfg.worktree, prompt, held / stem.name, readable=(into,))
    held_mvv = criteria_decide.approved(snap)
    # MVV の本文と共通原則の写しも、読めなければ確かめにならない資料である
    copied = () if held_mvv is None else tuple(p for p in (held_mvv.path, held_mvv.principles_path) if p is not None)
    try:
        return _answers(run, round_no, rows, spec, (*used, listed, *copied), launch)
    finally:
        _keep_records(run, round_no, held, stem)


def _answers(
    run: VerifyRun,
    round_no: int,
    rows: Sequence[Mapping[str, Any]],
    spec: agents.Launch,
    required: Sequence[Path],
    launch: Launcher,
) -> verify_decide.Answers:
    """起動 `spec` を打ち、指摘 `rows` への回答を読む。`required` は判定役が読む資料のファイルである。"""
    count = len(rows)
    status = launch(spec)
    if status.state != agents.AgentState.DONE:
        warning = f"判定役が終わらなかった（{status.state}）ので下げない"
        return _unanswered(run, round_no, count, f"{status.state}: {status.detail}", warning)
    try:
        answer = participants.claude_answer(spec.paths.out)
    except OSError:  # 出力の無い判定役は、回答を読めなかった扱いにする
        answer = participants.ClaudeAnswer("", ())
    denied = participants.denied_reads(answer.denials, required, spec.cwd)
    if denied:
        warning = f"判定役が資料のファイル {'・'.join(p.name for p in denied)} の読み取りを拒否されたので下げない"
        return _unanswered(run, round_no, count, f"permission_denials: {' / '.join(map(str, denied))}", warning)
    ids = [str(row["id"]) for row in rows]
    answers = verify_decide.read(participants.listed_of(answer.text, "verdicts"), ids)
    valid = sum(1 for verdict in answers.verdicts.values() if verdict.holds is not None)
    run.note(round_no, "verify", f"判定役へ {count} 件、有効な回答 {valid} 件")
    return answers
