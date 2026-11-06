"""到達の確認（#1337）。コンテナで走るテストが worktree を見ているかを、コンテナの中から探りの印を読んで確かめる。

対象は `.ndf/project.json` の `test.suites[]` に `container`（`service`・`compose_files`）を書いた suite だけである。
コマンドの語は見ない。

1. 環境を受ける: `worktree-testenv.sh compose-env <worktree>` が出す `KEY=VALUE`（`NDF_*`・`COMPOSE_PROJECT_NAME`・
   `COMPOSE_FILE`）をそのまま足す環境にする。自分では組まない（テスト環境の割り当ての持ち主は `worktree-testenv.sh`）
2. 印を置く: 共通の git ディレクトリの `info/exclude` へ `.ndf-evidence/` を登録してから、worktree の
   `.ndf-evidence/reach-<乱数>` に乱数を書く
3. 読む: 1 の環境で、worktree を作業ディレクトリにして `docker compose exec -T <サービス> cat <印>` を打つ
4. 消す: 印を必ず消す
5. 判定: 出力が乱数と一致すれば届いた。コンテナが無い・exec が 0 以外・中身が違う、はいずれも届かない

届かなければテストを走らせない（メインディレクトリのコードで走った偽の green を出さない）。

テストのステップの経路（`env_for`）は、起こせば直りうる理由で届かないと、テスト環境を 1 度だけ起こして確かめ直す
（テスト環境の用意。#2567）。起こす対象は宣言のサービスだけで、起動は `worktree-testenv.sh` を通す。起こした・
起こせなかった記録は `wake_events()` が返す。`discard_testenv` は worktree を消す前に公開を閉じてテスト環境を破棄する。

CLI（シェルから呼ぶ）は起こさない:

    python3 container_reach.py probe <worktree> --service <サービス> [--compose-file <パス>]...

終了コードは 0 届いた（標準出力に足す環境を `KEY=VALUE` で 1 行ずつ）/ 1 届かない / 2 コンテナ実行系が無い
（どちらも標準エラーに理由の 1 行）。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

import secret_redact
import worktree_base

TESTENV = Path(__file__).resolve().parent.parent / "worktree-testenv.sh"
EVIDENCE = ".ndf-evidence"
EXEC_TIMEOUT = 60  # compose exec の上限（秒）。コンテナの中の cat 1 回で足りる
UP_TIMEOUT = 300  # テスト環境の起動の上限（秒）。実測の最長 49 秒の 6 倍で、遅れの見張りの既定の下限と同じ（#2567 の決定 3）
STOP_GRACE = 5  # 打ち切った起動の、SIGTERM から SIGKILL までの猶予（秒）
TAIL_CHARS = 300  # 理由へ載せる標準エラーの末尾の文字数（伏せ字の後に数える）

# 作業ディレクトリごとの、到達を確かめた後に足す環境（1 プロセスで 1 回だけ確かめる）
_CACHE: dict[str, dict[str, str]] = {}
# 作業ディレクトリごとの、起こそうとして届かなかった理由（1 プロセスで起動は 1 度まで）
_FAILED: dict[str, "Unreachable"] = {}
# 起こした・起こせなかった記録（結果の `testenv` の行）
_EVENTS: list[dict] = []


class Unreachable(Exception):
    """worktree をコンテナへ届けられない。`code` は CLI の終了コード（1 届かない / 2 実行系が無い）。
    `wakeable` はテスト環境を起こせば直りうるか。"""

    def __init__(self, reason: str, code: int = 1, wakeable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.wakeable = wakeable


def docker_command() -> str:
    """コンテナ実行系のコマンド。`worktree-testenv.sh` と同じく `WT_DOCKER_COMMAND` で差し替えられる。"""
    return os.environ.get("WT_DOCKER_COMMAND") or "docker"


def compose_env(worktree: str | Path) -> dict[str, str]:
    """`worktree-testenv.sh compose-env` が出す環境。割り当てが無ければ空。"""
    p = subprocess.run(["bash", str(TESTENV), "compose-env", str(worktree)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Unreachable(f"テスト環境の値を得られない: {_tail(p.stderr)}")
    env: dict[str, str] = {}
    for line in p.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            env[key] = value
    return env


def probe_worktree(worktree: str | Path, service: str, compose_files: Iterable[str] = ()) -> dict[str, str]:
    """サービスのコンテナが worktree を見ていれば、足す環境を返す。見ていなければ `Unreachable`。

    `compose_files`（宣言の suite の `container.compose_files`）は、テスト環境の割り当てが `COMPOSE_FILE` を
    出さないときだけ使う。
    """
    docker = docker_command()
    if shutil.which(docker) is None:
        raise Unreachable(f"コンテナ実行系（{docker}）が見つからない", 2)
    wt = Path(worktree).resolve()
    env = compose_env(wt)
    files = [str(wt / f) for f in compose_files]
    if files and "COMPOSE_FILE" not in env:
        env["COMPOSE_FILE"] = ":".join(files)
    if not worktree_base.add_exclude(wt, f"{EVIDENCE}/"):
        raise Unreachable(f"探りの印を git の無視へ登録できない（{wt}）")
    token = secrets.token_hex(16)
    rel = f"{EVIDENCE}/reach-{token}"
    mark = wt / rel
    try:
        mark.parent.mkdir(exist_ok=True)
        mark.write_text(token, encoding="utf-8")
        p = subprocess.run(
            [docker, "compose", "exec", "-T", service, "cat", rel],
            cwd=wt,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=EXEC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise Unreachable(f"サービス {service} のコンテナが {EXEC_TIMEOUT} 秒で答えない", wakeable=True) from None
    except OSError as e:
        raise Unreachable(f"サービス {service} のコンテナを探れない: {e}") from None
    finally:
        try:
            mark.unlink()
        except FileNotFoundError:
            pass
        try:
            mark.parent.rmdir()
        except OSError:
            pass
    if p.returncode == 0 and p.stdout.strip() == token:
        return env
    if p.returncode == 0 or "No such file" in p.stdout + p.stderr:
        raise Unreachable(
            f"サービス {service} の作業ディレクトリはこの worktree ではない（メインディレクトリを見ている可能性がある）", wakeable=True
        )
    project = env.get("COMPOSE_PROJECT_NAME") or "既定"
    tail = secret_redact.redact_output(p.stderr, max_lines=1, max_chars=200)
    raise Unreachable(f"サービス {service} のコンテナが動いていない（プロジェクト {project}。{tail}）", wakeable=True)


def container_suites(root: str | Path) -> list[tuple[str, tuple[str, ...]]]:
    """宣言の suite のうち `container` を書いたものの `(service, compose_files)`。重ねない。"""
    import project_decl

    test = project_decl.read_project_decl(root).get("test")
    suites = test.get("suites") if isinstance(test, dict) else None
    out: list[tuple[str, tuple[str, ...]]] = []
    for suite in suites if isinstance(suites, list) else []:
        c = suite.get("container") if isinstance(suite, dict) else None
        if not isinstance(c, dict) or not isinstance(c.get("service"), str) or not c["service"]:
            continue
        files = tuple(f for f in c.get("compose_files") or [] if isinstance(f, str))
        if (c["service"], files) not in out:
            out.append((c["service"], files))
    return out


def _tail(text: str) -> str:
    """標準エラーを伏せ字にしてから末尾だけにする（結果と summary へ載るため、切る前に伏せる）。"""
    return secret_redact.redact_output(text, max_chars=TAIL_CHARS)


def _stop_group(p: "subprocess.Popen[str]") -> None:
    """`start_new_session` で起こしたプロセスのグループへ SIGTERM、猶予の後も残れば SIGKILL を送る。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            return
        try:
            p.wait(timeout=STOP_GRACE)
            return
        except subprocess.TimeoutExpired:
            pass


def _testenv(*args: str, timeout: int | None = None) -> tuple[int | None, str, str]:
    """`worktree-testenv.sh` を打ち、(終了コード, 標準出力, 標準エラー) を返す。上限を超えたらグループごと止めて終了コードは None。"""
    with subprocess.Popen(
        ["bash", str(TESTENV), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as p:
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop_group(p)
            return None, "", ""
        return p.returncode, out, err


def leftover_volumes(environment: str) -> list[str]:
    """同じプロジェクト名で残っている名前付きボリューム。読めなければ空を返し、止めない。"""
    try:
        p = subprocess.run(
            [docker_command(), "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={environment}"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=EXEC_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return p.stdout.split() if p.returncode == 0 else []


def _wake(cwd: Path, services: list[str], missed: list[str]) -> tuple[str, list[str]]:
    """テスト環境を起こし、(環境名, 起こす前から残っていたボリューム) を返す。起こせなければ `Unreachable`。"""
    names = "、".join(missed)
    code, out, err = _testenv("env", str(cwd))
    if code != 0:
        raise Unreachable(f"サービス {names} のテスト環境を起こせない: {_tail(err)}")
    if not out.strip():
        raise Unreachable(f".ndf/worktree.json に testenv の宣言が無いため、サービス {names} のテスト環境を起こせない")
    environment = json.loads(out)["environment"]
    if "COMPOSE_FILE" not in compose_env(cwd):
        raise Unreachable(".ndf/worktree.json の localenv.compose_files が無いため、テスト環境を起こせない")
    reused = leftover_volumes(environment)
    print(f"テスト環境を起こす: {environment}（サービス {names}。上限 {UP_TIMEOUT} 秒）", file=sys.stderr, flush=True)
    waits = [arg for service in services for arg in ("--wait-service", service)]
    code, _, err = _testenv("up", str(cwd), *waits, timeout=UP_TIMEOUT)
    if code is None:
        raise Unreachable(f"サービス {names} のテスト環境の起動が {UP_TIMEOUT} 秒で終わらなかった")
    if code != 0:
        raise Unreachable(f"サービス {names} のテスト環境を起こせない（終了コード {code}）: {_tail(err)}")
    return environment, reused


def _probe_all(cwd: str | Path, suites: list[tuple[str, tuple[str, ...]]]) -> tuple[dict[str, str], dict[str, Unreachable]]:
    """すべてのサービスを確かめ、(足す環境, 届かなかったサービスと理由) を返す。"""
    env: dict[str, str] = {}
    missed: dict[str, Unreachable] = {}
    for service, files in suites:
        try:
            env.update(probe_worktree(cwd, service, files))
        except Unreachable as e:
            missed.setdefault(service, e)
    return env, missed


def _wake_and_recheck(key: str, suites: list[tuple[str, tuple[str, ...]]], missed: dict[str, Unreachable]) -> dict[str, str]:
    """テスト環境を起こして確かめ直す。起こした・起こせなかったことを記録する。"""
    started = time.monotonic()
    event: dict = {"environment": None, "result": "failed"}
    try:
        event["environment"], reused = _wake(Path(key), list(dict.fromkeys(s for s, _ in suites)), list(missed))
        if reused:
            event["reused_volumes"] = reused
        env, again = _probe_all(key, suites)
        if again:
            raise Unreachable(next(iter(again.values())).reason)
    except Unreachable as e:
        event["reason"] = e.reason
        _FAILED[key] = e
        raise
    else:
        event["result"] = "woken"
        return env
    finally:
        event["seconds"] = round(time.monotonic() - started, 1)
        _EVENTS.append(event)


def env_for(cwd: str | Path) -> dict[str, str]:
    """`cwd` で走らせるテストに足す環境。コンテナの suite が無ければ空。届かなければ `Unreachable`。

    すべてのサービスを確かめ、起こせば直りうる理由で 1 つでも届かなければ、テスト環境を起こして全部を確かめ直す。
    作業ディレクトリごとに 1 プロセスで 1 回だけ確かめ、結果を覚えて使い回す（起こせなかった理由も送り直す）。
    """
    key = str(Path(cwd).resolve())
    if key in _CACHE:
        return _CACHE[key]
    if key in _FAILED:
        raise _FAILED[key]
    suites = container_suites(cwd)
    env, missed = _probe_all(cwd, suites)
    if missed:
        for e in missed.values():
            if not e.wakeable:
                raise e
        env = _wake_and_recheck(key, suites, missed)
    _CACHE[key] = env
    return env


def wake_events() -> list[dict]:
    """起こした・起こせなかった記録。`result` は `woken` / `failed`。"""
    return list(_EVENTS)


def discard_testenv(worktree: str | Path) -> tuple[bool, str | None]:
    """worktree を消す前に公開を閉じ、テスト環境を破棄する。(worktree を外してよいか, 理由) を返す。

    公開を閉じられなければ破棄せず (False, 理由)。閉じる手段は worktree で動くため、外すと閉じられない。
    破棄できなければ (True, 理由)。割り当ても公開も無ければコンテナ実行系を呼ばず (True, None)。
    ボリュームを消すかは `worktree-testenv.sh down` が宣言で決める。
    """
    wt = str(Path(worktree).resolve())
    code, _, err = _testenv("unexpose", wt, timeout=UP_TIMEOUT)
    if code != 0:
        line = secret_redact.redact_output(err, max_lines=1) or "公開を閉じる手段が終わらなかった: "
        why, _, where = line.rpartition(": ")
        return False, f"テスト環境の公開を閉じられない: {where}（{why}）。close_command を直して worktree-testenv.sh unexpose {wt} を打つ"
    try:
        environment = compose_env(wt).get("COMPOSE_PROJECT_NAME")
    except Unreachable as e:
        return True, f"テスト環境を破棄できない: {wt}（{e.reason}）"
    if not environment:
        return True, None
    code, _, err = _testenv("down", wt, timeout=UP_TIMEOUT)
    if code != 0:
        return True, f"テスト環境を破棄できない: {environment}（{_tail(err) or f'{UP_TIMEOUT} 秒で終わらなかった'}）"
    return True, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="コンテナが worktree を見ているかを確かめる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe")
    pr.add_argument("worktree")
    pr.add_argument("--service", required=True)
    pr.add_argument("--compose-file", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        env = probe_worktree(a.worktree, a.service, a.compose_file)
    except Unreachable as e:
        hint = f"。worktree-testenv.sh up {Path(a.worktree).resolve()} で起こす" if e.wakeable else ""
        print(e.reason + hint, file=sys.stderr)
        return e.code
    for key, value in env.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
