#!/usr/bin/env python3
"""slides-thumbnails.py: Google Slides の全ページのサムネイルを PNG で保存する（#2065）。

    python3 slides-thumbnails.py <presentationId> <出力のディレクトリ> [--size LARGE]

1. `gws slides presentations get` でページの ID（`objectId`）をページ順に取る
2. ページごとに `gws slides presentations pages getThumbnail` を打ち、返った `contentUrl` から画像を取る
3. `<出力のディレクトリ>/p<番号>.png` へ保存する（番号は 1 から、ページ順）

結果は `step_result` の形の 1 行の JSON で標準出力へ出す。`items` にページごとの `objectId` と保存先、
`metrics.pages` にページ数を載せる。

| 終了コード | status | 意味 |
| --- | --- | --- |
| 0 | ok | 全ページを保存した |
| 1 | stopped | gws か画像の取得が失敗した（gws の出力の末尾を `items` に載せる） |
| 2 | stopped | 引数の誤り・gws の応答を読めない |
| 11 | gate | gws が未認証（`gws-check.py` と同じ値）。利用者に `gws auth login` を頼む |

- gws の認証情報は読まない。認証は gws が自分の設定ディレクトリに持つ（C1）
- `contentUrl` は要求した利用者の権限で開ける一時的な URL で、認証なしで curl で取る
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import step_result as sr  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gws_common import emit_stopped, output_tail  # noqa: E402

TOOL = "slides-thumbnails"
EXIT_UNAUTHENTICATED = sr.EXIT_GATE + 1
SIZES = ("SMALL", "MEDIUM", "LARGE")
TIMEOUT = 120
CURL = "curl"


def _gws(gws: str, args: list[str], what: str) -> dict:
    """gws を打って応答の JSON を返す。未認証は 11、失敗は 1、読めない応答は 2 で終える。"""
    try:
        cp = subprocess.run([gws, *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        emit_stopped(TOOL, f"{what}: gws を起動できない: {e}", sr.EXIT_VIOLATION)
    out = output_tail(cp.stdout + "\n" + cp.stderr)
    if cp.returncode != 0:
        if "error[auth]" in cp.stderr or "No credentials" in cp.stderr:
            sr.emit(
                sr.result(
                    TOOL,
                    "gate",
                    "gws が未認証",
                    items=[{"name": what, "result": "unauthenticated", "detail": out}],
                    metrics={"state": "unauthenticated"},
                    next="google-workspace の手順 3. で利用者に gws auth login を頼み、終えたら打ち直す",
                ),
                EXIT_UNAUTHENTICATED,
            )
        emit_stopped(TOOL, f"{what}: gws が終了コード {cp.returncode} で終わった", sr.EXIT_VIOLATION, items=[{"name": what, "result": "gws-failed", "detail": out}])
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        emit_stopped(TOOL, f"{what}: gws の応答が JSON でない", sr.EXIT_UNREADABLE, items=[{"name": what, "result": "unreadable", "detail": out}])
    return {}


def _page_ids(gws: str, pid: str) -> list[str]:
    data = _gws(gws, ["slides", "presentations", "get", "--params", json.dumps({"presentationId": pid, "fields": "slides(objectId)"})], "ページの ID の取得")
    slides = data.get("slides") if isinstance(data, dict) else None
    ids = [s.get("objectId") for s in slides or [] if isinstance(s, dict)]
    if not ids or not all(isinstance(i, str) and i for i in ids):
        emit_stopped(TOOL, "ページの ID を読めない（slides[].objectId が無い）", sr.EXIT_UNREADABLE)
    return ids


def _thumbnail(gws: str, pid: str, oid: str, size: str, dest: pathlib.Path, no: int) -> None:
    what = f"p{no}（{oid}）のサムネイル"
    params = {"presentationId": pid, "pageObjectId": oid, "thumbnailProperties.thumbnailSize": size}
    data = _gws(gws, ["slides", "presentations", "pages", "getThumbnail", "--params", json.dumps(params)], what)
    url = data.get("contentUrl") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url:
        emit_stopped(TOOL, f"{what}: 応答に contentUrl が無い", sr.EXIT_UNREADABLE)
    try:
        cp = subprocess.run([CURL, "-fsSL", "--max-time", str(TIMEOUT), "-o", str(dest), url], capture_output=True, text=True, check=False)
    except OSError as e:
        emit_stopped(TOOL, f"{what}: curl を起動できない: {e}", sr.EXIT_VIOLATION)
    if cp.returncode != 0:
        emit_stopped(TOOL, f"{what}: 画像を取れない（curl の終了コード {cp.returncode}）", sr.EXIT_VIOLATION, items=[{"name": what, "result": "download-failed", "detail": output_tail(cp.stderr)}])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Google Slides の全ページのサムネイルを PNG で保存する")
    ap.add_argument("presentation_id")
    ap.add_argument("out_dir")
    ap.add_argument("--size", default="LARGE", choices=SIZES)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        emit_stopped(TOOL, "引数の誤り", sr.EXIT_UNREADABLE)
    gws = shutil.which("gws")
    if not gws:
        emit_stopped(TOOL, "gws が無い。google-workspace の手順 1. で確かめる", sr.EXIT_PRECONDITION)
    if not shutil.which(CURL):
        emit_stopped(TOOL, "curl が無い（サムネイルの画像を取るのに使う）", sr.EXIT_PRECONDITION)
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = _page_ids(gws, a.presentation_id)
    items = []
    for no, oid in enumerate(ids, start=1):
        dest = out / f"p{no}.png"
        _thumbnail(gws, a.presentation_id, oid, a.size, dest, no)
        items.append({"name": f"p{no}", "result": "saved", "objectId": oid, "detail": str(dest)})
    sr.emit(sr.result(TOOL, "ok", f"{len(ids)} ページのサムネイルを保存した", items=items, metrics={"pages": len(ids), "size": a.size, "out_dir": str(out)}), sr.EXIT_OK)


if __name__ == "__main__":
    main()
