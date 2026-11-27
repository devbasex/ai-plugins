"""判定役を 1 回起動して回答を返す（#4363）。

判定役は、修正担当・相手役とは別の、読むだけの claude の起動である（`participants.traced_launch`）。読める場所は
レビューする worktree と、この起動の資料のディレクトリ（差分と指摘の一覧のファイル、承認済みの MVV の写し）だけである。
指摘の文はプロセスの引数に載せず、一覧のファイルで渡す。判定役へ渡す一覧・差分・MVV の写し・指示に埋める基準の行には、
伏せ字（`run_log.redact`）を掛ける。この伏せ字が消すのは一部の形式だけで、秘密を守る境界ではない（相手役には差分を伏せずに
渡し、相手役・修正担当・判定役は作業場所を直接読める）。資料と出力は、起動の間だけの一時ディレクトリ（`tempfile.TemporaryDirectory`。起動の関数の中で
閉じる）の別々のディレクトリに置き、相手役が読んだ元の差分は変えず、判定役に読ませない。起動の後で、状態の保存先に
起動ごとに違う名前の実行者だけが読めるファイルとして新規作成する（排他作成。前からあるファイルは置き換えない）。書けなければ過程に
1 行残す。保存先の記録の片付けと、強制終了などで残った一時ディレクトリの回収は持たない。どの指摘を渡すかと回答の読み方は
`verify_decide`、回答を指摘の行へ当てるのは呼び手の `drive` である。

失敗しても例外で駆動を止めず、起動し直さない。回答なしと注意にする（どの指摘も下がらない）。資料のほかのファイルの読み取りを
拒否された起動と、外側の JSON の結果か拒否の一覧を確かめられない起動と、差分と指摘の一覧を最後の行まで `Read` した記録の無い起動は、
成り立たないとした回答だけを採らない（`verify_decide.unlowering`）。記録を確かめるため出力は 1 歩ごとの行にし、保存する
記録には結果の行と Tool の呼び出しだけを残す（Tool が返したファイルの内容は残さない。判定役の回答の引用は残りうる）。注意は PR の記録に載るので、
手元の絶対パス・指摘の本文・回答の中身を書かない。過程も結果 JSON に出るので、載せるのはエラーの種別と資料の種類・ファイル名までで、
起動の状態の詳細（標準エラーの原文を含みうる）と絶対パスを含む詳細は、標準エラーのログとは別の、実行者だけが読める記録に残す。
監視の早期の警告も種類だけを知らせ、標準エラーの行は載せない（`agents.Launch.private`）。
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

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


def _masked_copy(source: Path, into: Path) -> Path:
    """`source` に伏せ字（`run_log.redact`。一部の形式だけ）を掛けた写しを、資料のディレクトリ `into` に同じ名前で作る。写せなければ `OSError`。"""
    copied = into / source.name
    copied.write_text(run_log.redact(source.read_bytes()), encoding="utf-8")
    return copied


def _readable_mvv(snap: Optional[mvv.Snapshot], into: Path) -> Optional[mvv.Snapshot]:
    """承認済みの MVV なら、本文と共通原則を資料のディレクトリ `into` へ写し（`_masked_copy`）、写しを指すスナップショットを返す。
    元のファイルは利用者の状態の保存先にあり判定役が読めないためで、読める場所は増やさない。"""
    held = criteria_decide.approved(snap)
    if held is None or held.path is None or held.principles_path is None:
        return snap
    return held._replace(path=_masked_copy(held.path, into), principles_path=_masked_copy(held.principles_path, into))


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
            if record.suffix == ".out":  # Tool が返したファイルの内容は、状態の保存先に残さない
                data = participants.recordable_output(data)
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


class _Materials(NamedTuple):
    """判定役が読む資料のファイル。`checked`（差分と指摘の一覧）は、読んだ記録が無ければ否定の回答を採らない。"""

    checked: tuple[Path, ...]
    others: tuple[Path, ...]


def _prepare(
    run: VerifyRun,
    round_no: int,
    rows: Sequence[Mapping[str, Any]],
    diffs: Mapping[str, Sequence[Path]],
    into: Path,
) -> tuple[str, _Materials]:
    """資料のディレクトリ `into` を作って判定役の資料を置き、指示と、判定役が読む資料のファイルを返す。判定役へ渡すものは
    どれにも伏せ字（一部の形式だけ。秘密を守る境界ではない）を掛け、元の差分は変えない。用意できなければ `OSError`。"""
    into.mkdir(mode=0o700)
    used = tuple(dict.fromkeys(diff for row in rows for diff in diffs[str(row["id"])]))
    copies = {diff: _masked_copy(diff, into) for diff in used}
    snap = _readable_mvv(run.snapshot, into)
    # 相手役が引用した認証情報にも伏せ字を掛ける（差分のパスは変えないので、足す前に伏せる）
    masked = run_log.redact([dict(row) for row in rows])
    # 差分が 1 つなら、行ごとに繰り返し書かない
    shown = masked if len(used) == 1 else [{**row, "diffs": [str(copies[d]) for d in diffs[str(row["id"])]]} for row in masked]
    listed = into / f"round{round_no}-verify-findings.jsonl"
    listed.write_text(verify_decide.listing(shown), encoding="utf-8")
    criteria = [
        *criteria_decide.criteria_lines(run.cfg.settings.focus, snap),
        *criteria_decide.document_line(run.documents, rows),
    ]
    shared = tuple(copies.values())
    # 指示に埋める基準の行（重点・文書の基準）にも同じ伏せ字を掛ける
    prompt = verify_decide.instruction(listed, shared, run.cfg.worktree, run_log.redact(criteria))
    held = criteria_decide.approved(snap)
    # MVV の本文と共通原則の写しも、読めなければ確かめにならない資料である
    extra = () if held is None else tuple(p for p in (held.path, held.principles_path) if p is not None)
    return prompt, _Materials((*shared, listed), extra)


def ask(
    run: VerifyRun, round_no: int, rows: Sequence[Mapping[str, Any]], diffs: Mapping[str, Sequence[Path]], launch: Launcher
) -> verify_decide.Answers:
    """指摘 `rows`（空でない）を判定役の 1 回の起動で確かめる。`diffs` は指摘の番号ごとの、その指摘を出した相手役が読んだ
    差分のファイルで、どれも資料のディレクトリにある。"""
    count = len(rows)
    stem = run.round_stem(round_no, "-verify")
    # 状態の保存先に残す記録の名前。起動ごとに違うので、同じラウンドを起動し直しても前の記録が残る
    name = f"{stem.name}-{uuid.uuid4().hex[:8]}"

    def unprepared(material: str, exc: OSError) -> verify_decide.Answers:
        warning = (
            "判定役の資料（指摘の一覧・差分と MVV の本文と共通原則の写し）か一時ディレクトリを用意できないので、判定役を起動せず下げない"
        )
        # 絶対パスを含む詳細は、状態の保存先の実行者だけが読める記録に残す
        _record(run, round_no, stem.with_name(f"{name}.detail.log"), f"{material}: {exc}\n".encode())
        return _unanswered(run, round_no, count, f"起動していない: {material}で {type(exc).__name__}", warning)

    try:
        scratch = tempfile.TemporaryDirectory(prefix=f"{stem.name}-")
    except OSError as exc:
        return unprepared("一時ディレクトリ", exc)
    with scratch as made:
        # 回答は非公開の内容を含みうる。出力の先は新しく作る実行者だけが開けるディレクトリで、前からあるファイルやリンクを開かない
        into, written = Path(made, "in"), Path(made, "out")
        try:
            written.mkdir(mode=0o700)
            prompt, materials = _prepare(run, round_no, rows, diffs, into)
        except OSError as exc:
            return unprepared("判定役の資料", exc)
        system = str(criteria_decide.verify_text()["system"])
        spec = participants.traced_launch(system, run.cfg.worktree, prompt, written / stem.name, (into,))
        # 判定役の標準エラーは接続先やアカウントを含みうるので、監視の警告に行を載せない
        spec = replace(spec, private=True)
        try:
            answers = _answers(run, round_no, rows, spec, materials, launch)
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
    materials: _Materials,
    launch: Launcher,
) -> verify_decide.Answers:
    """起動 `spec` を打ち、指摘 `rows` への回答を読む。"""
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
    denied = participants.denied_reads(answer.denials, (*materials.checked, *materials.others), spec.cwd)
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
    elif not answer.verified:
        # 外側の JSON を確かめられなければ拒否の有無が分からないので、本文の途中から読めた否定の回答を採らない
        answers = verify_decide.unlowering(answers, "判定役の出力の結果か拒否の一覧を確かめられない")
    else:
        read = participants.read_files(spec.paths.out, spec.cwd)
        unread = [p.name for p in materials.checked if Path(os.path.normpath(p)) not in read]
        if unread:
            answers = verify_decide.unlowering(
                answers, f"判定役が資料のファイル {'・'.join(unread)} を最後の行まで読んだことを確かめられない"
            )
    valid = sum(1 for verdict in answers.verdicts.values() if verdict.holds is not None)
    run.note(round_no, "verify", f"判定役へ {count} 件、有効な回答 {valid} 件")
    return answers
