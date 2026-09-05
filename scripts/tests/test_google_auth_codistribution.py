"""`google-drive` を配る配布先は `google-auth` も配ることを固定する（#116）。

`gdrive_fetch.py` は `google-auth` の `get_credentials()` を使う。参照は 3 つの候補を
順に見る形で、環境変数（`GOOGLE_AUTH_SCRIPTS`）と `~/.claude/skills/` を先に試すため、
隣に無い配置でも動きうる。**ただし、どれも当たらなければ import で落ちる。**

v10.5.0 で 2 つとも配布へ回した（`optional-skills/` を無くした）。**配る先で片方だけが
欠ける状態を作らない**ことが、Skill の境界をまたぐ参照を例外として許した条件である。
条件そのものをここで固定する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANIFESTS = sorted((REPO / "plugins/ndf/manifests").glob("*-skills.txt"))

CONSUMER = "google-drive"
PROVIDER = "google-auth"


def _listed(manifest: Path) -> list[str]:
    return [
        line.split("#", 1)[0].strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]


def test_the_manifests_are_found() -> None:
    """走査先が空のまま通らないようにする。"""
    assert MANIFESTS, "manifests/*-skills.txt が 1 つも無い"


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.name)
def test_the_provider_ships_wherever_the_consumer_does(manifest: Path) -> None:
    listed = _listed(manifest)
    if CONSUMER not in listed:
        pytest.skip(f"{manifest.name} は {CONSUMER} を配らない")
    assert PROVIDER in listed, (
        f"{manifest.name} は {CONSUMER} を配るが {PROVIDER} を配らない。"
        f"{CONSUMER} の資格情報の取得が解決できない"
    )


def test_the_reference_still_points_at_the_provider() -> None:
    """参照が消えたら、この束と検査の例外はもう要らない。"""
    body = (
        REPO / "plugins/ndf/skills/google-drive/scripts/gdrive_fetch.py"
    ).read_text(encoding="utf-8")
    assert "google-auth" in body
    assert "from google_auth import get_credentials" in body
