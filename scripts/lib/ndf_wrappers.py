"""根の `scripts/` が NDF の包み（`plugins/ndf/scripts/lib/`。#1142 の決定 19）を使うための 1 か所。

依存はリポジトリの根の `pyproject.toml` と `uv.lock` で解決する（決定 19・22）。根の環境は `plugins/ndf` の
全グループを同じ版で持つ。エントリポイントは外部パッケージの import より前に呼ぶ。

    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    from ndf_wrappers import require
    require("md", "versions")   # 2 つ以上のグループは 1 回で渡す（決定 23）
    import md, versions

`python3 scripts/<名前>.py` で起動したときは、根の uv の環境（`<根>/.venv`）へ 1 回だけ起動し直す。
全体テスト（`uv run --frozen --project . --all-extras pytest`）の中では、そのまま戻る。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NDF_LIB = REPO / "plugins" / "ndf" / "scripts" / "lib"

if str(NDF_LIB) not in sys.path:
    sys.path.insert(0, str(NDF_LIB))

import deps  # noqa: E402


def require(group: str, *more: str) -> None:
    """根の宣言と lock で、渡したグループのパッケージが import できる環境を保証する。"""
    deps.require(group, *more, project=REPO)
