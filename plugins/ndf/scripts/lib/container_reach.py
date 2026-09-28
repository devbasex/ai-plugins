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

CLI（シェルから呼ぶ）:

    python3 container_reach.py probe <worktree> --service <サービス> [--compose-file <パス>]...

終了コードは 0 届いた（標準出力に足す環境を `KEY=VALUE` で 1 行ずつ）/ 1 届かない / 2 コンテナ実行系が無い
（どちらも標準エラーに理由の 1 行）。
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

TESTENV = Path(__file__).resolve().parent.parent / "worktree-testenv.sh"
EVIDENCE = ".ndf-evidence"
EXEC_TIMEOUT = 60  # compose exec の上限（秒）。コンテナの中の cat 1 回で足りる

# 作業ディレクトリごとの、到達を確かめた後に足す環境（1 プロセスで 1 回だけ確かめる）
_CACHE: dict[str, dict[str, str]] = {}


class Unreachable(Exception):
    """worktree をコンテナへ届けられない。`code` は CLI の終了コード（1 届かない / 2 実行系が無い）。"""

    def __init__(self, reason: str, code: int = 1) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def docker_command() -> str:
    """コンテナ実行系のコマンド。`worktree-testenv.sh` と同じく `WT_DOCKER_COMMAND` で差し替えられる。"""
    return os.environ.get("WT_DOCKER_COMMAND") or "docker"


def compose_env(worktree: str | Path) -> dict[str, str]:
    """`worktree-testenv.sh compose-env` が出す環境。割り当てが無ければ空。"""
    p = subprocess.run(["bash", str(TESTENV), "compose-env", str(worktree)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Unreachable(f"テスト環境の値を得られない: {p.stderr.strip()[:300]}")
    env: dict[str, str] = {}
    for line in p.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            env[key] = value
    return env


def _exclude_evidence(worktree: Path) -> bool:
    """共通の git ディレクトリの `info/exclude` へ `.ndf-evidence/` を登録する（`worktree-testenv.sh` と同じ）。"""
    p = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return False
    exclude = Path(p.stdout.strip()) / "info" / "exclude"
    line = f"{EVIDENCE}/"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if line not in text.splitlines():
            with exclude.open("a", encoding="utf-8") as f:
                f.write(("" if not text or text.endswith("\n") else "\n") + line + "\n")
    except OSError:
        return False
    return True


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
    if not _exclude_evidence(wt):
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
        raise Unreachable(f"サービス {service} のコンテナが {EXEC_TIMEOUT} 秒で答えない") from None
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
        raise Unreachable(f"サービス {service} の作業ディレクトリはこの worktree ではない（メインディレクトリを見ている可能性がある）")
    project = env.get("COMPOSE_PROJECT_NAME") or "既定"
    tail = (p.stderr.strip().splitlines() or [""])[-1][:200]
    raise Unreachable(
        f"サービス {service} のコンテナが動いていない（プロジェクト {project}）。worktree-testenv.sh up {wt} で起こす（{tail}）"
    )


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


def env_for(cwd: str | Path) -> dict[str, str]:
    """`cwd` で走らせるテストに足す環境。コンテナの suite が無ければ空。届かなければ `Unreachable`。

    作業ディレクトリごとに 1 プロセスで 1 回だけ確かめ、届いた結果を覚えて使い回す。
    """
    key = str(Path(cwd).resolve())
    if key in _CACHE:
        return _CACHE[key]
    env: dict[str, str] = {}
    for service, files in container_suites(cwd):
        env.update(probe_worktree(cwd, service, files))
    _CACHE[key] = env
    return env


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
        print(e.reason, file=sys.stderr)
        return e.code
    for key, value in env.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
