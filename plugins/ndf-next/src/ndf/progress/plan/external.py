"""外のオーガナイザーとのファイルのやり取り（エントリポイント file-organizer。#3097・#2439）。

外のオーガナイザー（サービス Factory のランナーなど）の下で動くレーンは、計画の進行表の代わりに、環境変数
`NDF_ORGANIZER_DIR` の置き場のファイルでオーガナイザーとやり取りする。置き場・パス・項目は表が持ち、ここは写さない:

- 置き場は `places` の `organizer`（環境変数の名前）、やり取りするファイルはエントリポイントの表 `entrypoints` の
  `file-organizer` の `files`、各ファイルのパスと項目は `ndf-files` の行が持つ
- レーンが書くのはリリースの依頼（`organizer-request`）だけで、外のオーガナイザーの答え（`organizer-answer`）を読む。
  ほかのファイル（ranges・batch・presented・progress・results）はリリースの工程（M2）が読み書きする
- どちらの書き手も一時ファイルへ書いて名前を付け替えるので、読み手は書きかけのファイルを読まない
- 表に無いパスはやり取りしない（`exchange_file` が None を返す）

DB は読まない。置き場は呼び手が渡す（環境変数を読むのは `organizer_dir`）。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import facts
from ndf.progress.plan import RELEASE_RESULTS

# 依頼の ID（ファイル名になる。区切りや `..` を含めない）
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ExchangeError(contracts.ContractError):
    """やり取りのファイルの形が表と違う。"""


def organizer_dir(env: Mapping[str, str]) -> Optional[Path]:
    """外のオーガナイザーの置き場。環境変数（`places` の `organizer` の `env`）が無ければ None（計画の進行表で動く）。"""
    found = env.get(facts.value("places", "organizer", "env"))
    return Path(found) if found else None


def _pattern(path: str) -> re.Pattern[str]:
    """`ndf-files` の path（`<名前>` は実行ごとに決まる 1 階層）を正規表現にする。"""
    parts = re.split(r"(<[^>]+>)", path)
    return re.compile("".join("[^/]+" if p.startswith("<") else re.escape(p) for p in parts))


def exchange_file(rel: str) -> Optional[str]:
    """置き場からの相対パス `rel` が、エントリポイントのやり取りするファイルのどれか（`ndf-files` の id）。無ければ None。"""
    for file_id in facts.value("entrypoints", "file-organizer", "files"):
        if _pattern(facts.value("ndf-files", file_id, "path")).fullmatch(rel):
            return file_id
    return None


def _path(root: Path, file_id: str, ident: str) -> Path:
    if not _ID.fullmatch(ident):
        raise ExchangeError(f"依頼の ID {ident!r} はファイル名にできない")
    return root / facts.value("ndf-files", file_id, "path").replace("<id>", ident)


def request_body(fields: Mapping[str, Any], ndf_version: str) -> dict[str, Any]:
    """リリースの依頼の中身。項目は `ndf-files` の `organizer-request` の `fields` だけで、どれも欠かせない。"""
    body = {**fields, "kind": "request", "ndf_version": ndf_version}
    want = facts.value("ndf-files", "organizer-request", "fields")
    missing = [k for k in want if body.get(k) in (None, "")]
    extra = sorted(set(body) - set(want))
    if missing or extra:
        raise ExchangeError(f"リリースの依頼の項目が表と違う（無い: {missing}・表に無い: {extra}）")
    return {k: body[k] for k in want}


def write_request(root: Path, body: Mapping[str, Any]) -> Path:
    """リリースの依頼を `requests/<id>.json` へ書く（一時ファイルへ書いて名前を付け替える）。書いたパスを返す。"""
    path = _path(root, "organizer-request", str(body["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dict(body), ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_answer(root: Path, ident: str) -> Optional[dict[str, Any]]:
    """外のオーガナイザーの答え（`answers/<id>.json`）。まだ無ければ None。形が違えば `ExchangeError`。"""
    path = _path(root, "organizer-answer", ident)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ExchangeError(f"答え {path.name} を JSON として読めない: {e}") from e
    if not isinstance(body, dict) or body.get("result") not in RELEASE_RESULTS:
        raise ExchangeError(f"答え {path.name} の result が {' / '.join(RELEASE_RESULTS)} のどれでもない")
    return body
