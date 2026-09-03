"""起票先の判断を読み取る補助。

`conftest.py` ではなく固有名のモジュールへ置く。pytest は収集したテストのあるディレクトリを
`sys.path` の先頭へ足すため、束を同時に実行すると同名のファイルが互いを覆う。**束ごとに
違う名前を付ければ、どの束から実行しても同じものが読まれる。**

表の読み取りは `retrospective` の束にも同じものがある。**束は単独でも実行できる必要があり、
別の Skill のテストのディレクトリは、その束を収集しない限り `sys.path` に載らない。**
共有するには置き場所をリポジトリの根へ上げることになるため、束ごとに持つ。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
REFERENCE = SKILL_DIR / "references" / "issue-target.md"

REMOTE = "https://github.com/devbasex/ai-plugins.git"
SLUG = "devbasex/ai-plugins"

DECISION_TABLE_HEADING = "## 判断表"
RESOLUTION_TABLE_HEADING = "## 起票先のリポジトリを決める"
CROSS_REPOSITORY_HEADING = "## 両方にまたがる課題"


def read(path: Path) -> str:
    assert path.is_file(), f"ファイルが無い: {path}"
    return path.read_text(encoding="utf-8")


def table(body: str, heading: str) -> tuple[list[str], list[list[str]]]:
    """見出しの直後に来る表の見出し行と本文の行を返す。

    読み取れないこと自体を失敗として扱う。素通りさせると、表の書き方を変えるだけで
    この検査を無効にできる。
    """
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    header: list[str] = []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert header and rows, f"表を読み取れない: {heading}"
    return header, rows


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
    """段の解決を書いた囲みを返す。

    読み取れないこと自体を失敗として扱う。囲みを消すだけで、解決の形を見る検査を無効に
    できる形にしない。
    """
    found = [
        block
        for block in fenced_blocks("\n".join(section(body, RESOLUTION_TABLE_HEADING)))
        if "SKILL_REPO=" in block
    ]
    assert found, "段の解決を書いた囲みが見つからない"
    return found[0]


def headings(body: str, prefix: str) -> list[str]:
    """その深さの見出しを、本文に現れる順で返す。囲みの中は数えない。"""
    found: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith(prefix):
            found.append(line[len(prefix) :].strip())
    return found


def command_lines(body: str) -> list[str]:
    """囲みの中の行だけを返す。

    実行できる呼び出しかどうかを見るため、本文の説明に出てくる同じ語は数えない。
    """
    found: list[str] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            found.append(line)
    return found


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


def ordered_steps(body: str, heading: str) -> list[str]:
    """節の中の番号付きの手順を、本文に現れる順で返す。囲みの中は数えない。"""
    steps: list[str] = []
    fenced = False
    for line in section(body, heading):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        matched = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if matched:
            steps.append(matched.group(2).strip())
    assert steps, f"番号付きの手順を読み取れない: {heading}"
    return steps


def gh_issue_commands(body: str) -> list[str]:
    """囲みの中の `gh issue` で始まる呼び出しを、行の継続をつないで返す。

    `\\` で折り返した呼び出しは 1 つにまとめる。行ごとに見ると、次の行へ回した
    `--repo` を渡していないものとして読んでしまう。
    """
    joined: list[str] = []
    buffer = ""
    for line in command_lines(body):
        stripped = line.strip()
        if not buffer and not stripped.startswith("gh issue"):
            continue
        continues = stripped.endswith("\\")
        if continues:
            stripped = stripped[:-1].strip()
        buffer = f"{buffer} {stripped}".strip()
        if not continues:
            joined.append(buffer)
            buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


# 段 2 が見る位置を、ランタイムごとに作る。**手順書の表が挙げる位置と同じものを作る。**
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
    """段の解決の囲みをそのまま実行し、決まった名前を返す。

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
