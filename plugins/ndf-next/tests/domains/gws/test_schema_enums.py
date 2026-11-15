"""サムネイルの大きさの定数と、引数・結果のスキーマの列挙が一致することを確かめる。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ndf.domains.gws import thumbnails


def _io_schema(name: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "io" / f"gws.{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_sizes_match_the_schema_enums() -> None:
    # サムネイルの大きさは結果のスキーマ gws.result の metrics.size と、引数のスキーマ gws.args の --size の列挙と同じ
    result_enum = _io_schema("result")["properties"]["metrics"]["properties"]["size"]["enum"]
    args = next(v for v in _io_schema("args")["oneOf"] if v["properties"]["subcommand"]["const"] == "thumbnails")
    assert list(thumbnails.SIZES) == result_enum == args["properties"]["--size"]["enum"]
    assert thumbnails.DEFAULT_SIZE == args["properties"]["--size"]["default"]
