"""起票先の解決（`issue-file.py resolve-target`）を配置ごとに呼ぶ補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。**束ごとに
違う名前を付ければ、どの束から実行しても同じものが読まれる。**

手順書の Markdown は読まない。解決の本体は部品にあり、テストは部品を直接呼ぶ（#851）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR.parents[1] / "scripts"
SCRIPT = SCRIPTS / "issue-file.py"

sys.path.insert(0, str(SCRIPTS / "lib"))
import gh_call  # noqa: E402

REMOTE = "https://github.com/devbasex/ai-plugins.git"
SLUG = "devbasex/ai-plugins"


def _load():
    spec = importlib.util.spec_from_file_location("ndf_issue_file_for_target", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ISSUE_FILE = _load()


# 手順 2 が見る位置を、ランタイムごとに作る。**部品が見る位置と同じものを作る。**
# 部品は「1 つに絞れたときだけ採る」ため、どのランタイムでも配置は 1 つにする。
RUNTIME_LAYOUTS = {
    "claude": ".claude/plugins/marketplaces/ai-plugins",
    # 取得元を持たない。clone した作業ディレクトリそのものを見る。agy も同じ位置になるため、
    # 配置としては 1 つで足りる。
    "kiro": None,
    "codex": ".codex/.tmp/marketplaces/ai-plugins",
}


def make_clone(path: Path, url: str | None = REMOTE, *, carries_ndf: bool = True) -> Path:
    """origin を持つ clone を作る。通信はしない。

    `carries_ndf` を偽にすると `plugins/ndf/` を持たない clone になる。配布元へ絞る
    条件が働いているかを確かめるために使う。
    """
    path.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    run("init", "-q")
    if url:
        run("remote", "add", "origin", url)
    if carries_ndf:
        (path / "plugins" / "ndf").mkdir(parents=True, exist_ok=True)
    return path


def runtime_layout(root: Path, runtime: str, url: str | None = REMOTE) -> tuple[Path, Path]:
    """そのランタイムの配置を作り、`(HOME, 実行する現在地)` を返す。"""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    relative = RUNTIME_LAYOUTS[runtime]
    if relative is None:
        return home, make_clone(root / "clone", url)
    make_clone(home / relative, url)
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    return home, work


def _answer_target(target: str | None):
    """`gh repo view` だけに答える。ほかの呼び出しは GitHub へ届く経路なので失敗にする。"""

    def runner(args, stdin=None, cwd=None):
        if args[:2] == ["repo", "view"]:
            return gh_call.GhResult(0, target + "\n", "") if target else gh_call.GhResult(1, "", "no repo")
        raise AssertionError(f"想定外の gh の呼び出し: {args}")

    return runner


def run_resolution(*, home: Path, cwd: Path, target: str | None = None, env_repo: str | None = None) -> tuple[int, dict]:
    """`resolve-target` を一時の `HOME` と現在地で呼び、`(終了コード, 結果)` を返す。"""
    saved_env = {k: os.environ.get(k) for k in ("HOME", "NDF_SKILL_REPO")}
    saved_cwd, saved_runner = Path.cwd(), gh_call.RUNNER
    out = io.StringIO()
    try:
        os.environ["HOME"] = str(home)
        os.environ.pop("NDF_SKILL_REPO", None)
        if env_repo is not None:
            os.environ["NDF_SKILL_REPO"] = env_repo
        os.chdir(cwd)
        gh_call.RUNNER = _answer_target(target)
        with contextlib.redirect_stdout(out):
            try:
                ISSUE_FILE.main(["resolve-target"])
                code = 0
            except SystemExit as done:
                code = done.code
    finally:
        gh_call.RUNNER = saved_runner
        os.chdir(saved_cwd)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def resolved_upstream(*, home: Path, cwd: Path) -> str:
    """上流リポジトリの名前。決まらなければ空文字（終了コード 20 であることも確かめる）。"""
    code, obj = run_resolution(home=home, cwd=cwd)
    upstream = obj["metrics"]["upstream"]
    assert code == (0 if upstream else 20), obj
    return upstream or ""
