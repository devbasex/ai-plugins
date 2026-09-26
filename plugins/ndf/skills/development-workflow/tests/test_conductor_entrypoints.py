"""conductor の一覧（`references/conductor-entrypoints.md`）に載るエントリポイントが実在することを確かめる。

一覧の表の「エントリポイント」の列は `<plugins/ndf からのパス> [副命令 ...] [引数 ...]` の形で書く。
テストは列の値からパスと副命令を取り出し、ファイルがあること・副命令をそのスクリプトが受けることを見る。
文言は照合しない。スクリプトを動かしたり消したりしたのに一覧を直し忘れた状態を落とす。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
LISTING = PLUGIN_ROOT / "skills" / "development-workflow" / "references" / "conductor-entrypoints.md"
COLUMN = "エントリポイント"


def listed_entrypoints() -> list[str]:
    """一覧の表から「エントリポイント」の列のコードの値を集める。"""
    items: list[str] = []
    col = None
    for line in LISTING.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            col = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if COLUMN in cells:
            col = cells.index(COLUMN)
            continue
        if col is None or set(line) <= set("|- :"):
            continue
        items += re.findall(r"`([^`]+)`", cells[col])
    return items


def split(entry: str) -> tuple[str, list[str]]:
    """パスと副命令に分ける。`-` と `<` で始まる語から後は引数として読まない。"""
    words = entry.split()
    subs: list[str] = []
    for w in words[1:]:
        if w.startswith(("-", "<", "{")):
            break
        subs.append(w)
    return words[0], subs


ENTRIES = listed_entrypoints()


def test_listing_has_entrypoints():
    assert len(ENTRIES) >= 10, ENTRIES


@pytest.mark.parametrize("entry", ENTRIES)
def test_listed_script_exists(entry: str):
    path, _ = split(entry)
    assert (PLUGIN_ROOT / path).is_file(), f"{entry}: {path} が無い"


def accepted_subcommands(path: Path, prefix: list[str]) -> str:
    """スクリプトが副命令として受ける語が現れる文字列を返す。

    argparse のスクリプトは `<prefix> --help` の出力、そうでないものは本体の文字列リテラルを見る。
    """
    if path.suffix == ".py":
        got = subprocess.run([sys.executable, str(path), *prefix, "--help"],
                             capture_output=True, text=True, timeout=60)
        if got.returncode == 0 and "usage:" in got.stdout:
            return got.stdout
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("entry", [e for e in ENTRIES if split(e)[1]])
def test_listed_subcommand_is_accepted(entry: str):
    path, subs = split(entry)
    script = PLUGIN_ROOT / path
    for i, sub in enumerate(subs):
        text = accepted_subcommands(script, subs[:i])
        assert re.search(rf"(?<![\w-]){re.escape(sub)}(?![\w-])", text), \
            f"{entry}: {path} が副命令 {sub} を受けない"
