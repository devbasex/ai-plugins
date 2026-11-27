"""判定役を 1 回起動して回答を返す（#4363）。

判定役は、修正担当・相手役とは別の、読むだけの claude の起動である（`participants.read_only_launch`）。読める場所は
レビューする worktree と、この実行の資料のディレクトリ（差分と指摘の一覧のファイル、承認済みの MVV の写し）だけである。
指摘の文はプロセスの引数に載せず、秘密（`run_log.redact`）を伏せた一覧のファイルで渡す。一覧は、実行者だけが開ける資料のディレクトリに置く。判定役の出力は
起動の間だけの一時ディレクトリ（`tempfile.TemporaryDirectory`。起動の関数の中で閉じる）に書かせ、起動の後で、状態の保存先に
起動ごとに違う名前の実行者だけが読めるファイルとして新規作成する（排他作成。前からあるファイルは置き換えない）。書けなければ過程に
1 行残す。保存先の記録の片付けと、強制終了などで残った一時ディレクトリの回収は持たない。どの指摘を渡すかと回答の読み方は
`verify_decide`、回答を指摘の行へ当てるのは呼び手の `drive` である。

失敗しても例外で駆動を止めず、起動し直さない。回答なしと注意にする（どの指摘も下がらない）。資料のほかのファイルの読み取りを
拒否された起動は、成り立たないとした回答だけを採らない（`verify_decide.unlowering`）。注意は PR の記録に載るので、
手元の絶対パス・指摘の本文・回答の中身を書かない。過程も結果 JSON に出るので、載せるのはエラーの種別と資料の種類・ファイル名までで、
起動の状態の詳細（標準エラーの原文を含みうる）と絶対パスを含む詳細は、標準エラーのログとは別の、実行者だけが読める記録に残す。
監視の早期の警告も種類だけを知らせ、標準エラーの行は載せない（`agents.Launch.private`）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import agents, run_log
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


def _record(run: VerifyRun, round_no: int, to: Path, data: bytes) -> bool:
    """記録を、実行者だけが読める新しいファイル `to` に書く。前からあるファイルやリンクは置き換えない（排他作成）。書けなくても
    判定の結果と駆動は変えず、過程に 1 行残して偽を返す。"""
    try:
        with open(to, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
            out.write(data)
    except OSError as exc:
        run.note(round_no, "verify", f"判定役の記録 {to.name} を書けない（{type(exc).__name__}）")
        return False
    return True


def _save_records(run: VerifyRun, round_no: int, written: Path, stem: Path, name: str) -> bool:
    """一時ディレクトリ `written` の記録を、`stem` の隣へ、名前の部分を起動ごとの名前 `name` に替えて書く。1 つでも書けたら真。"""
    saved = False
    for record in sorted(written.iterdir()):
        to = stem.with_name(name + record.name.removeprefix(stem.name))
        try:
            data = record.read_bytes()
        except OSError as exc:
            run.note(round_no, "verify", f"判定役の記録 {to.name} を読めない（{type(exc).__name__}）")
        else:
            saved = _record(run, round_no, to, data) or saved
    return saved


def _detail(run: VerifyRun, round_no: int, spec: agents.Launch, text: str) -> None:
    """起動の詳細を出力と同じディレクトリの別の記録に書く（出力と一緒に保存する。子プロセスが書いた標準エラーのログは変えない）。"""
    try:
        spec.paths.out.with_name(f"{spec.paths.out.stem}.detail.log").write_text(f"{text}\n", encoding="utf-8")
    except OSError as exc:  # 書けなくても判定の結果と駆動は変えない
        run.note(round_no, "verify", f"判定役の起動の詳細を書けない（{exc.strerror}）")


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
    # 相手役が引用した認証情報は、外部のランタイム（判定役）へ渡す前に伏せる（差分のパスは変えないので、足す前に伏せる）
    masked = run_log.redact([dict(row) for row in rows])
    # 差分が 1 つなら、行ごとに繰り返し書かない
    shown = masked if len(used) == 1 else [{**row, "diffs": [str(d) for d in diffs[str(row["id"])]]} for row in masked]
    into = used[0].parent
    listed = into / f"round{round_no}-verify-findings.jsonl"
    stem = run.round_stem(round_no, "-verify")
    # 状態の保存先に残す記録の名前。起動ごとに違うので、同じラウンドを起動し直しても前の記録が残る
    name = f"{stem.name}-{uuid.uuid4().hex[:8]}"
    material = "MVV の写し"
    try:
        snap = _readable_mvv(run.snapshot, into)
        material = "指摘の一覧"
        listed.write_text(verify_decide.listing(shown), encoding="utf-8")
        criteria = [
            *criteria_decide.criteria_lines(run.cfg.settings.focus, snap),
            *criteria_decide.document_line(run.documents, rows),
        ]
        prompt = verify_decide.instruction(listed, used, run.cfg.worktree, criteria)
        # 回答は非公開の内容を含みうる。出力の先は新しく作る実行者だけが開けるディレクトリで、前からあるファイルやリンクを開かない
        material = "出力の一時ディレクトリ"
        scratch = tempfile.TemporaryDirectory(prefix=f"{stem.name}-")
    except OSError as exc:
        warning = (
            "判定役の資料（指摘の一覧・MVV の本文と共通原則の写し）か出力の一時ディレクトリを用意できないので、判定役を起動せず下げない"
        )
        # 絶対パスを含む詳細は、状態の保存先の実行者だけが読める記録に残す
        _record(run, round_no, stem.with_name(f"{name}.detail.log"), f"{material}: {exc}\n".encode())
        return _unanswered(run, round_no, count, f"起動していない: {material}で {type(exc).__name__}", warning)
    with scratch as made:
        written = Path(made)
        system = str(criteria_decide.verify_text()["system"])
        spec = participants.read_only_launch(system, run.cfg.worktree, prompt, written / stem.name, readable=(into,))
        # 判定役の標準エラーは接続先やアカウントを含みうるので、監視の警告に行を載せない
        spec = replace(spec, private=True)
        held_mvv = criteria_decide.approved(snap)
        # MVV の本文と共通原則の写しも、読めなければ確かめにならない資料である
        copied = () if held_mvv is None else tuple(p for p in (held_mvv.path, held_mvv.principles_path) if p is not None)
        try:
            answers = _answers(run, round_no, rows, spec, (*used, listed, *copied), launch)
        finally:
            # 監視中の割り込みでも、起動の層（`agents`）が判定役を止めてから伝えるので、記録は書き終わっている
            saved = _save_records(run, round_no, written, stem, name)
    # 保存できた出力が無ければ、ラウンドの記録から参照させない
    return answers._replace(record=name if saved else "")


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
        # 詳細は接続先やアカウントを含みうるので過程に載せない。起動できなかった詳細は標準エラーに無いので別の記録に書く
        _detail(run, round_no, spec, str(status.detail))
        return _unanswered(run, round_no, count, str(status.state), warning)
    try:
        answer = participants.claude_answer(spec.paths.out)
    except OSError:  # 出力の無い判定役は、回答を読めなかった扱いにする
        answer = participants.ClaudeAnswer("", ())
    denied = participants.denied_reads(answer.denials, required, spec.cwd)
    if denied:
        warning = f"判定役が資料のファイル {'・'.join(p.name for p in denied)} の読み取りを拒否されたので下げない"
        _detail(run, round_no, spec, f"permission_denials: {' / '.join(map(str, denied))}")
        return _unanswered(run, round_no, count, f"permission_denials: {'・'.join(p.name for p in denied)}", warning)
    ids = [str(row["id"]) for row in rows]
    answers = verify_decide.read(participants.listed_of(answer.text, "verdicts"), ids)
    # 実装や設定の読み取りの拒否は、どの指摘の確かめに要ったかを決められないので、否定の回答を採らない
    blocked = [d for d in answer.denials if str(d.get("tool_name")) in participants.READ_PATH_ARGS]
    if blocked:
        _detail(run, round_no, spec, f"permission_denials: {json.dumps(blocked, ensure_ascii=False)}")
        answers = verify_decide.unlowering(answers)
    valid = sum(1 for verdict in answers.verdicts.values() if verdict.holds is not None)
    run.note(round_no, "verify", f"判定役へ {count} 件、有効な回答 {valid} 件")
    return answers
