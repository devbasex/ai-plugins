"""`refactoring` と `cross-refactoring` が同じ配布先へ揃うことを固定する（#444）。

**`cross-refactoring` は兆候と手法の呼び名を `refactoring` から読む。** 片方だけを配る
配布先があると、読む側は表を解決できずに止まる。**配る基準は manifest が持つ**ため、
そちらを固定する。

`google-drive` が `google-auth` を読む形と同じ条件である（#116）。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFESTS = sorted((ROOT / "plugins/ndf/manifests").glob("*-skills.txt"))
PAIR = ("refactoring", "cross-refactoring")


def test_the_manifests_are_found() -> None:
    """配る基準そのものが見つかること。**空の一覧で通さない。**"""
    assert len(MANIFESTS) >= 4, [p.name for p in MANIFESTS]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_both_skills_ride_together(manifest: pathlib.Path) -> None:
    """呼び名を読む側と持つ側が、同じ配布先へ揃うこと。"""
    listed = {ln.strip() for ln in manifest.read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")}
    present = [name for name in PAIR if name in listed]
    assert len(present) in (0, 2), (
        f"{manifest.name} は {present} だけを載せている。"
        "呼び名を読む側と持つ側は同じ配布先へ揃える"
    )
