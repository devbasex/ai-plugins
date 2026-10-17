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
- `contentUrl` は要求した利用者の権限で 30 分ほど開ける一時的な URL で、認証なしで取れる
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request

LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import step_result as sr  # noqa: E402

TOOL = "slides-thumbnails"
EXIT_UNAUTHENTICATED = sr.EXIT_GATE + 1
SIZES = ("SMALL", "MEDIUM", "LARGE")
TAIL_LINES = 20
TIMEOUT = 120


def _tail(text: str) -> str:
    return "\n".join((text or "").strip().splitlines()[-TAIL_LINES:])


def _stop(summary: str, code: int, items=None, metrics=None) -> None:
    sr.emit(sr.result(TOOL, "stopped", summary, items=items, metrics=metrics), code)


def _gws(gws: str, args: list[str], what: str) -> dict:
    """gws を打って応答の JSON を返す。未認証は 11、失敗は 1、読めない応答は 2 で終える。"""
    try:
        cp = subprocess.run([gws, *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        _stop(f"{what}: gws を起動できない: {e}", sr.EXIT_VIOLATION)
    out = _tail(cp.stdout + "\n" + cp.stderr)
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
        _stop(f"{what}: gws が終了コード {cp.returncode} で終わった", sr.EXIT_VIOLATION, items=[{"name": what, "result": "gws-failed", "detail": out}])
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError:
        _stop(f"{what}: gws の応答が JSON でない", sr.EXIT_UNREADABLE, items=[{"name": what, "result": "unreadable", "detail": out}])
    return {}


def _page_ids(gws: str, pid: str) -> list[str]:
    data = _gws(gws, ["slides", "presentations", "get", "--params", json.dumps({"presentationId": pid, "fields": "slides(objectId)"})], "ページの ID の取得")
    slides = data.get("slides") if isinstance(data, dict) else None
    ids = [s.get("objectId") for s in slides or [] if isinstance(s, dict)]
    if not ids or not all(isinstance(i, str) and i for i in ids):
        _stop("ページの ID を読めない（slides[].objectId が無い）", sr.EXIT_UNREADABLE)
    return ids


def _thumbnail(gws: str, pid: str, oid: str, size: str, dest: pathlib.Path, no: int) -> None:
    what = f"p{no}（{oid}）のサムネイル"
    params = {"presentationId": pid, "pageObjectId": oid, "thumbnailProperties.thumbnailSize": size}
    data = _gws(gws, ["slides", "presentations", "pages", "getThumbnail", "--params", json.dumps(params)], what)
    url = data.get("contentUrl") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url:
        _stop(f"{what}: 応答に contentUrl が無い", sr.EXIT_UNREADABLE)
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:  # noqa: S310 - gws が返した URL だけを開く
            dest.write_bytes(r.read())
    except OSError as e:
        _stop(f"{what}: 画像を取れない: {e}", sr.EXIT_VIOLATION, items=[{"name": what, "result": "download-failed", "detail": str(e)}])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Google Slides の全ページのサムネイルを PNG で保存する")
    ap.add_argument("presentation_id")
    ap.add_argument("out_dir")
    ap.add_argument("--size", default="LARGE", choices=SIZES)
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        _stop("引数の誤り", sr.EXIT_UNREADABLE)
    gws = shutil.which("gws")
    if not gws:
        _stop("gws が無い。google-workspace の手順 1. で確かめる", sr.EXIT_PRECONDITION)
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
