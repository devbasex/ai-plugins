#!/usr/bin/env python3
"""deps-trial.py: #1142 の決定 17 を L0 に入れる前に確かめる試行（実験版）。

    python3 deps-trial.py where                 # require("github") の後、どの環境で動いたかを返す
    python3 deps-trial.py etag --repo o/n --pr N [--wait 秒]
                                                # githubkit の ETag 付きの読み直しが上限に数えられるかを測る

require() は決定 17 の表の順 1〜5 の試作である。宣言と lock は隣の deps-trial/ にあり、
環境は ~/.cache/ndf/venv/trial に置く（NDF_DEPS_VENV で変えられる）。
結果は lib/step_result.py の形の 1 行の JSON。終了コード 0 = ok / 3 = 前提が無い。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from step_result import emit, result  # noqa: E402

TOOL = "deps-trial"
PROJECT = Path(__file__).resolve().parent / "deps-trial"
UV_VERSION = "0.12.19"
GROUPS = {"github": ["githubkit"]}


def finish(status: str, summary: str, items: list | None = None, metrics: dict | None = None,
           code: int | None = None) -> None:
    emit(result(TOOL, status, summary, items, metrics), code)


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def install_uv() -> str | None:
    """版を固定した公式のインストーラで ~/.local/bin へ入れる。curl が無ければ pip を使う。"""
    env = dict(os.environ, UV_NO_MODIFY_PATH="1")
    if shutil.which("curl"):
        cmd = f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh"
        subprocess.run(["sh", "-c", cmd], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    else:
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet",
                        f"uv=={UV_VERSION}"], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
    return find_uv()


def require(group: str) -> None:
    """group のパッケージが import できる環境で動いていることを保証する。無ければ起動し直す。"""
    if all(importlib.util.find_spec(m) for m in GROUPS[group]):
        return
    if os.environ.get("NDF_DEPS_REEXEC"):
        finish("stopped", f"uv の環境へ起動し直したが {group} を import できない", code=3)
    uv = find_uv()
    if not uv:
        print(f"[{TOOL}] uv が無いため {UV_VERSION} を ~/.local/bin へ入れる", file=sys.stderr)
        uv = install_uv()
    if not uv:
        finish("stopped", "uv を入れられない。手で入れる: "
             f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh", code=3)
    venv = os.environ.get("NDF_DEPS_VENV") or str(Path.home() / ".cache/ndf/venv/trial")
    env = dict(os.environ, NDF_DEPS_REEXEC="1", UV_PROJECT_ENVIRONMENT=venv)
    argv = [uv, "run", "--quiet", "--frozen", "--project", str(PROJECT), "--extra", group,
            "python", str(Path(__file__).resolve()), *sys.argv[1:]]
    os.execve(uv, argv, env)


def gh_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                          check=True).stdout.strip()


def cmd_where() -> None:
    require("github")
    from importlib.metadata import version
    ver = version("githubkit")
    finish("ok", f"githubkit {ver} を {sys.executable} で import した",
         metrics={"executable": sys.executable, "reexec": bool(os.environ.get("NDF_DEPS_REEXEC")),
                  "python": sys.version.split()[0], "githubkit": ver})


def cmd_etag(repo: str, pr: int, wait: float) -> None:
    require("github")
    from githubkit import GitHub
    owner, name = repo.split("/", 1)
    token = gh_token()
    gh = GitHub(token, http_cache=True)
    plain = GitHub(token, http_cache=False)

    def core_used() -> int:
        # /rate_limit の used は応答のヘッダーと食い違う（2026-09-26 に 0 と 132）ため、キャッシュの無い
        # プローブの X-Ratelimit-Used を読む。プローブ自身も 1 回に数えられる
        resp = plain.rest.repos.get(owner, name)
        return int(resp.headers["x-ratelimit-used"])
    reads = []
    for i in range(3):
        if i:
            time.sleep(wait)
        u0 = core_used()
        resp = gh.rest.pulls.get(owner, name, pr)
        ext = resp.raw_response.extensions if hasattr(resp.raw_response, "extensions") else {}
        u1 = core_used()
        reads.append({"read": i + 1, "status": resp.status_code, "counted": u1 - u0 - 1,
                      "from_cache": bool(ext.get("hishel_from_cache")),
                      "revalidated": bool(ext.get("hishel_revalidated")),
                      "etag": resp.headers.get("etag"),
                      "cache_control": resp.headers.get("cache-control"),
                      "updated_at": str(resp.parsed_data.updated_at)})
    total = sum(r["counted"] for r in reads)
    finish("ok", f"読み 3 回で上限に数えられたのは {total} 回（別の利用者の呼び出しが混ざると増える）",
         items=reads, metrics={"counted_total": total, "wait": wait})


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where")
    e = sub.add_parser("etag")
    e.add_argument("--repo", required=True)
    e.add_argument("--pr", type=int, required=True)
    e.add_argument("--wait", type=float, default=5.0)
    a = p.parse_args()
    if a.cmd == "where":
        cmd_where()
    else:
        cmd_etag(a.repo, a.pr, a.wait)


if __name__ == "__main__":
    main()
