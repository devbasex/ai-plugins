"""`cross-review` と `cross-refactoring` が同じ配布先へ揃うことを固定する（#870）。

**`cross-refactoring` の drive は、最終ゲートで `cross-review` の drive を起動させる。**
片方だけを配る配布先があると、ゲートのコマンドが解決できずに止まる。**配る基準は
manifest が持つ**ため、そちらを固定する。

`refactoring` の表を読む形と同じ条件である（#444）。
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFESTS = sorted((ROOT / "plugins/ndf/manifests").glob("*-skills.txt"))
PAIR = ("cross-review", "cross-refactoring")


def test_the_manifests_are_found() -> None:
    """配る基準そのものが見つかること。**空の一覧で通さない。**"""
    assert len(MANIFESTS) >= 4, [p.name for p in MANIFESTS]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.stem)
def test_both_skills_ride_together(manifest: pathlib.Path) -> None:
    """ゲートを起動させる側と起動される側が、同じ配布先へ揃うこと。"""
    listed = {ln.strip() for ln in manifest.read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")}
    present = [name for name in PAIR if name in listed]
    assert len(present) in (0, 2), (
        f"{manifest.name} は {present} だけを載せている。"
        "ゲートを起動させる側と起動される側は同じ配布先へ揃える"
    )
