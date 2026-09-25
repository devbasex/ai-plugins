"""起票先の判断を読み取る補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。**束ごとに
違う名前を付ければ、どの束から実行しても同じものが読まれる。**
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REFERENCE = SKILL_DIR / "references" / "issue-target.md"

REMOTE = "https://github.com/devbasex/ai-plugins.git"
SLUG = "devbasex/ai-plugins"

RESOLUTION_TABLE_HEADING = "## 起票先のリポジトリを決める"


def read(path: Path) -> str:
    assert path.is_file(), f"ファイルが無い: {path}"
    return path.read_text(encoding="utf-8")


def fenced_blocks(body: str) -> list[str]:
    """囲みの中身を、本文に現れる順で返す。"""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def resolution_snippet(body: str) -> str:
    """手順の解決を書いた囲みを返す。

    読み取れないこと自体を失敗として扱う。囲みを消すだけで、解決の形を見る検査を無効に
    できる形にしない。
    """
    found = [
        block
        for block in fenced_blocks("\n".join(section(body, RESOLUTION_TABLE_HEADING)))
        if "SKILL_REPO=" in block
    ]
    assert found, "手順の解決を書いた囲みが見つからない"
    return found[0]


def section(body: str, heading: str) -> list[str]:
    """見出しから、次の同じ深さ以上の見出しまでの行を返す。

    読み取れないこと自体を失敗として扱う。見出しを消すか節を空にするだけで、この節を
    見る検査を無効にできる形にしない。囲みの中の `#` は見出しとして数えない。
    """
    depth = len(heading) - len(heading.lstrip("#"))
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    found: list[str] = []
    fenced = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            found.append(line)
            continue
        if not fenced and stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= depth:
                break
        found.append(line)
    assert any(line.strip() for line in found), f"節の本文が空である: {heading}"
    return found


# 手順 2 が見る位置を、ランタイムごとに作る。**手順書の表が挙げる位置と同じものを作る。**
# 手順は「1 つに絞れたときだけ採る」ため、どのランタイムでも配置は 1 つにする。
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


def run_resolution(body: str, *, home: Path, cwd: Path) -> str:
    """手順の解決の囲みをそのまま実行し、決まった名前を返す。

    **手順書に書いてある本文を動かす。** 写し取った別の実装を試すと、手順書が誤ったまま
    でも検査は通る。

    厳しい設定（`set -euo pipefail`）の下で動かす。未定義の変数とパイプの途中の失敗を
    拾うためで、手順書の囲みは呼び出す側の設定を選べない。
    """
    script = f'set -euo pipefail\n{resolution_snippet(body)}\nprintf "%s" "$SKILL_REPO"\n'
    env = os.environ.copy()
    env.pop("NDF_SKILL_REPO", None)
    env.update({"HOME": str(home), "LC_ALL": "C.UTF-8"})
    done = subprocess.run(
        ["bash", "-c", script], cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    assert done.returncode == 0, f"解決が落ちた: {done.stderr}"
    return done.stdout.strip()
