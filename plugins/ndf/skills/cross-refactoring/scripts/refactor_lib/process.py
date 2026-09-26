"""テストのプロセスの実行と打ち切り。プロセスグループごと止める。"""
from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import time
from typing import Optional

from . import info
from .paths import git_out


def run_with_timeout(
    command: "str | list[str]", cwd: str, timeout: int, kill_grace: float = 5.0,
    output: Optional[pathlib.Path] = None,
) -> tuple[Optional[int], bool]:
    """テストコマンドを実行し `(終了コード, 打ち切ったか)` を返す。

    **新しいプロセスグループで起動し、打ち切るときはグループごと止める。**
    `shell=True` のまま `subprocess.run(timeout=...)` を使うと、終了するのは
    シェルだけで、pytest などの子プロセスは走り続ける。残ったプロセスは同じ
    作業ディレクトリを書き換え続けるため、直後の `git checkout` と競合する。

    **語の並び（`list`）はシェルを通さずに走らせる**（#933 の AC10b）。範囲テストは
    進行側が `test_targets` から組み立てた語の並びで、シェルの構文を解釈させない。
    文字列は利用者が渡したコマンド（`--baseline-test` / `--round-test`）で、今と同じく
    シェルで走らせる。

    `output` を渡すと標準出力と標準エラーをそのファイルへ書く（修正担当へ渡す材料）。
    """
    sink = open(output, "wb") if output is not None else None
    try:
        proc = subprocess.Popen(
            command, shell=isinstance(command, str), cwd=cwd, start_new_session=True,
            stdout=sink if sink is not None else subprocess.PIPE,
            stderr=subprocess.STDOUT if sink is not None else subprocess.PIPE,
        )
    except OSError as exc:
        if sink is not None:
            sink.write(f"起動できませんでした: {exc}\n".encode("utf-8"))
            sink.close()
        return 127, False
    try:
        proc.communicate(timeout=timeout)
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc, kill_grace)
        # 出力はもう使わない。**パイプを閉じてから**待つ。開いたままだと、
        # パイプを継承した子が残っている限り EOF が来ず、ここで止まる。
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        try:
            proc.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return None, True
    finally:
        if sink is not None:
            sink.close()


def _process_group_alive(pgid: int) -> bool:
    """プロセスグループに生きたプロセスが残っているか。"""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # 判断できないときは「残っている」側に倒す（SIGKILL まで進める）。
        return True


def _kill_process_group(
    proc: "subprocess.Popen[bytes]", grace: float = 5.0
) -> None:
    """プロセスグループごと止める。SIGTERM のあと、残っていれば SIGKILL。

    **親シェルの終了で打ち切らない。** 親が終わっても、SIGTERM を無視する子は
    グループに残って作業ディレクトリを書き換え続ける。判定は必ず
    **グループの存否**で行う。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except (PermissionError, OSError):
        proc.kill()
        return

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        # 親シェルを先に回収する。回収しないとゾンビがグループに残り、
        # 子がすべて終わっていても猶予を最後まで待つ（#883）
        proc.poll()
        if not _process_group_alive(pgid):
            return
        time.sleep(0.2)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_test_at(
    work: str, sha: str, command: str, head_branch: str,
    timeout: int, kill_grace: float = 5.0,
) -> str:
    """指定コミットを取り出してテストを実行し `pass` / `fail` を返す。

    **各コミットでテストが通ったかは、実際に走らせないと分からない。**
    結果ファイルの `test_status` は実装担当の申告にすぎず、チェックの根拠にできない。
    実行後は必ず元の位置へ戻す。ブランチの上にいたらそのブランチへ、detach していたら
    元のコミットへ戻る（書き込み用の作業ディレクトリは detach で作る。#638）。

    上限時間を超えたら `fail` とする。生成されたコードやテストが無限ループに入ると、
    待ち続けて進行全体が止まるためで、通す側には倒さない。
    """
    branch = git_out(work, ["symbolic-ref", "-q", "--short", "HEAD"])
    back = [branch] if branch else ["--detach", git_out(work, ["rev-parse", "HEAD"]) or "HEAD"]
    if git_out(work, ["checkout", "--detach", sha]) is None:
        return "missing"
    try:
        code, timed_out = run_with_timeout(command, work, timeout, kill_grace)
        if timed_out:
            info(f"⚠ コミット {sha[:7]} のテストが {timeout} 秒で終わりませんでした")
            return "fail"
        return "pass" if code == 0 else "fail"
    finally:
        subprocess.run(
            ["git", "checkout", *back], cwd=work, capture_output=True, text=True
        )
