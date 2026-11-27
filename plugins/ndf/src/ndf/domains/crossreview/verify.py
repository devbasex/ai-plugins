"""判定役を 1 回起動して回答を返す（#4363）。

判定役は、修正担当・相手役とは別の、読むだけの claude の起動である（`participants.read_only_launch`）。読める場所は
レビューする worktree と、この実行の資料のディレクトリ（差分と指摘の一覧のファイル、承認済みの MVV の写し）だけである。
指摘の文はプロセスの引数に載せず一覧のファイルで渡す。一覧は、実行者だけが開ける資料のディレクトリに置く。判定役の出力は
起動ごとに新しく作る実行者だけが開けるディレクトリに書かせ、起動の後で記録を起動ごとの名前で状態の保存先へ移してディレクトリを
消す。起動の監視中に割り込まれたら記録を移さず、移している途中で割り込まれたら移していない記録をディレクトリに残す。再開は、NDF が作った目印（`<ディレクトリ名>.held`）の
あるディレクトリのうち、目印の排他（`flock`）を取れたものだけから移して、空になれば消す（`recover`）。排他は起動した実行と判定役の
プロセスが持つ。ハードリンクを作れない保存先では、排他的に新規作成して写す。移した記録は残すだけで、片付けない。どの指摘を渡すかと回答の読み方は `verify_decide`、回答を指摘の行へ当てるのは呼び手の
`drive` である。

失敗しても例外で駆動を止めず、起動し直さない。回答なしと注意にする（どの指摘も下がらない）。注意は PR の記録に載るので、
手元の絶対パス・指摘の本文・回答の中身を書かない。過程も結果 JSON に出るので、載せるのはエラーの種別と資料の種類・ファイル名までで、
起動の状態の詳細（標準エラーの原文を含みうる）と絶対パスを含む詳細は、標準エラーのログとは別の、実行者だけが読める記録に残す。
"""

from __future__ import annotations

import fcntl
import filecmp
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters import agents
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, verify_decide
from ndf.domains.crossreview.fixer import Launcher
from ndf.domains.crossreview.settings import RunConfig
from ndf.domains.records import mvv

# 起動ごとの出力のディレクトリの名前（記録の名前の部分とラウンドの番号）
_HELD = re.compile(r"(round(\d+)-verify)-\w+")
# 目印のファイル名の接尾辞（目印の名前は、ディレクトリの名前にこれを足したもの）
_MARKER = ".held"


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


def _private_log(run: VerifyRun, round_no: int, stem: Path, text: str, once: str = "") -> None:
    """絶対パスを含みうる詳細を、状態の保存先の実行者だけが読める新しいファイル（`stem` の名前で始まる `.detail.log`）に残す。
    `once` を渡すと名前を `<once>.detail.log` に決め、既にあれば足さない（打ち直しのたびに同じ診断のファイルが増えない）。"""
    try:
        if once:
            try:
                fd = os.open(stem.parent / f"{once}.detail.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return
        else:
            fd, _ = tempfile.mkstemp(prefix=f"{stem.name}-", suffix=".detail.log", dir=stem.parent)
        with open(fd, "w", encoding="utf-8") as out:
            out.write(f"{text}\n")
    except OSError as exc:  # 保存先に書けなくても判定の結果と駆動は変えない
        run.note(round_no, "verify", f"判定役の失敗の詳細を書けない（{type(exc).__name__}）")


def _save_record(record: Path, to: Path) -> None:
    """記録 `record` を、実行者だけが読める新しいファイル `to` にする。前からある同じ名前のファイルやリンクは置き換えない
    （`FileExistsError`）。ハードリンクを作れない保存先では、排他的に新規作成して中身を写し、写し切れなければ（割り込みを含む）消す。"""
    record.chmod(0o600)
    try:
        os.link(record, to)
        return
    except FileExistsError:
        raise
    except OSError:
        fd = os.open(to, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with open(fd, "wb") as out, record.open("rb") as source:
            shutil.copyfileobj(source, out)
    except BaseException:  # 割り込み（KeyboardInterrupt）でも、途中までの中身を正式な名前で残さない
        to.unlink(missing_ok=True)
        raise


def _marker(held: Path) -> Path:
    """起動ごとのディレクトリ `held` の目印。目印は起動ごとに 1 つで、ほかの実行の保存で消えない。"""
    return held.with_name(held.name + _MARKER)


def _claim(run: VerifyRun, round_no: int, held: Path) -> Optional[int]:
    """`held` の目印を作り、排他を持つファイル記述子を返す。排他してから目印の名前にするので、回収は持ち主のいる目印を排他できない。
    排他できない保存先では、目印を作らず None を返す（判定役は起動する。中断で残ったディレクトリは回収しない）。"""
    fd, made = None, None
    try:
        fd, made = tempfile.mkstemp(prefix=f".{held.name}.", suffix=".tmp", dir=held.parent)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.rename(made, _marker(held))
        return fd
    except OSError as exc:
        why = f"判定役の出力のディレクトリを排他できないので、中断で残っても自動で片付けない（{type(exc).__name__}）"
        run.note(round_no, "verify", why)
        if fd is not None and made is not None:
            os.close(fd)
            Path(made).unlink(missing_ok=True)
        return None


def _release(held: Path, fd: Optional[int]) -> None:
    """`_claim` の排他を手放す。`held` が消えていれば目印も消し、残っていれば回収のために目印を残す。"""
    if fd is None:
        return
    try:
        if not held.exists():
            _marker(held).unlink(missing_ok=True)
    finally:
        os.close(fd)


def _move_records(run: VerifyRun, round_no: int, held: Path, stem: Path) -> bool:
    """起動ごとのディレクトリ `held` の記録を `stem` の隣へ保存し（`_save_record`）、保存できた記録を `held` から取り除く。
    保存先の名前は、`stem` の名前の部分を `held` の名前（起動ごとに違う）に替えたものである。保存中に割り込まれたら、移し直さずに
    割り込みを直ちに伝える。1 つでも保存できたら真。"""
    saved = False
    for record in list(held.iterdir()):
        if record.is_symlink() or not record.is_file():
            continue
        name = held.name + record.name.removeprefix(stem.name)
        to = stem.parent / name
        try:
            try:
                _save_record(record, to)
            except FileExistsError:
                # 保存した直後、取り除く前に割り込まれた記録は、同じ中身が保存先にある
                if to.is_symlink() or not filecmp.cmp(record, to, shallow=False):
                    raise
            saved = True
            record.unlink()  # 後の記録の保存中に割り込まれても、`held` には移していない記録だけが残る
        except OSError as exc:  # 診断の記録を移せなくても、判定の結果と駆動は変えない
            run.note(round_no, "verify", f"判定役の記録 {name} を移せない（{type(exc).__name__}）")
            _private_log(run, round_no, stem, f"{name}: {exc}", once=f"{name}.move")
    return saved


def _keep_records(run: VerifyRun, round_no: int, held: Path, stem: Path) -> bool:
    """起動の後で記録を移し（`_move_records`）、`held` を消す。保存できない記録はディレクトリと一緒に消える。
    割り込まれたら、`held` と、まだ移していない記録は回収できるように残る（`recover`）。1 つでも保存できたら真。"""
    saved = _move_records(run, round_no, held, stem)
    shutil.rmtree(held, ignore_errors=True)
    return saved


def recover(run: VerifyRun) -> None:
    """前の起動が残した出力のディレクトリのうち、目印のあるものから記録を移し、空になれば目印と一緒に消す。起動した実行か
    判定役のプロセスが排他を持つ間は触らない。移せない記録は消さずに残し、目印も残す（次の起動でもう一度移す）。"""
    for marker in sorted(run.cfg.state_dir.glob(f"round*-verify-*{_MARKER}")):
        name = marker.name.removesuffix(_MARKER)
        found = _HELD.fullmatch(name)
        if found is None:
            continue
        round_no = int(found[2])
        held = run.cfg.state_dir / name
        try:
            fd = os.open(marker, os.O_RDWR | os.O_NOFOLLOW)
        except OSError:  # 別の実行が片付けた直後の目印と、開けない目印は触らない
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if held.is_dir() and not held.is_symlink():
                _move_records(run, round_no, held, run.cfg.state_dir / found[1])
                held.rmdir()  # 空のときだけ消える
            marker.unlink(missing_ok=True)
        except BlockingIOError:  # 使っている最中
            pass
        except OSError as exc:
            run.note(round_no, "verify", f"判定役の出力のディレクトリ {name} を片付けられない（{type(exc).__name__}）")
        finally:
            os.close(fd)


def _detail(run: VerifyRun, round_no: int, spec: agents.Launch, text: str) -> None:
    """起動の詳細を出力と同じディレクトリの別の記録に書く（出力と一緒に 0600 で移る。子プロセスが書いた標準エラーのログは変えない）。"""
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
    # 差分が 1 つなら、行ごとに繰り返し書かない
    shown = rows if len(used) == 1 else [{**row, "diffs": [str(d) for d in diffs[str(row["id"])]]} for row in rows]
    into = used[0].parent
    listed = into / f"round{round_no}-verify-findings.jsonl"
    stem = run.round_stem(round_no, "-verify")
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
        # 回答は非公開の内容を含みうる。出力の先は新しく作り（0700）、前からあるファイルやリンクを開かない
        material = "出力の保存先"
        held = Path(tempfile.mkdtemp(prefix=f"{stem.name}-", dir=stem.parent))
    except OSError as exc:
        warning = "判定役の資料（指摘の一覧・MVV の本文と共通原則の写し）か出力の保存先を用意できないので、判定役を起動せず下げない"
        _private_log(run, round_no, stem, f"{material}: {exc}")
        return _unanswered(run, round_no, count, f"起動していない: {material}で {type(exc).__name__}", warning)
    system = str(criteria_decide.verify_text()["system"])
    spec = participants.read_only_launch(system, run.cfg.worktree, prompt, held / stem.name, readable=(into,))
    held_mvv = criteria_decide.approved(snap)
    # MVV の本文と共通原則の写しも、読めなければ確かめにならない資料である
    copied = () if held_mvv is None else tuple(p for p in (held_mvv.path, held_mvv.principles_path) if p is not None)
    claimed = _claim(run, round_no, held)
    if claimed is not None:
        # 判定役にも排他を引き継ぐ（この実行が強制終了されても、判定役が書き終えるまで回収させない）
        spec = replace(spec, keep_fds=(claimed,))
    try:
        # 監視が割り込まれると判定役は生きたままなので、記録は移さない。目印とディレクトリを残し、判定役が排他を解いた後の
        # 回収（`recover`）に任せる
        answers = _answers(run, round_no, rows, spec, (*used, listed, *copied), launch)
        saved = _keep_records(run, round_no, held, stem)
    finally:
        _release(held, claimed)
    # 保存できた出力が無ければ、ラウンドの記録から参照させない
    return answers._replace(record=held.name if saved else "")


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
    valid = sum(1 for verdict in answers.verdicts.values() if verdict.holds is not None)
    run.note(round_no, "verify", f"判定役へ {count} 件、有効な回答 {valid} 件")
    return answers
