"""サムネイルの保存（`ndf gws thumbnails`）: Google Slides の全ページのサムネイルを PNG で保存する。

1. `gws slides presentations get` でページの ID（`objectId`）をページ順に取る
2. ページごとに `gws slides presentations pages getThumbnail` を打ち、返った `contentUrl` から curl で画像を取る
3. `<出力のディレクトリ>/p<番号>.png` へ保存する（番号は 1 から、ページ順）

- 未認証かは、gws が失敗したときだけ認証の状態を 1 回読み、records の gws の部品の判定で決める（gws のエラーの文言に
  頼らない）。読めなければ未認証でないとして元の gws の失敗で止まる。全ページを保存できたときは認証の状態を読まない
- gws の資格情報は読まない。`contentUrl` は要求した利用者の権限で開ける一時的な URL で、結果へ載せない
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf.contracts import exit_codes, limits, result
from ndf.domains.gws import GwsStop, Outcome, call, unauthenticated_gate
from ndf.domains.gws import report_decide as rd
from ndf.domains.records import gws as records_gws

# 上限の設定（contracts/limits/）の名前。gws のスライドの操作・認証の状態の読み取り・curl に使う
LIMIT = "google-workspace.thumbnails"
# サムネイルの大きさ（引数の選択肢。引数のスキーマ gws.args はここから生成し、結果のスキーマ gws.result の列挙と同じであることをテストが確かめる）
SIZES = ("SMALL", "MEDIUM", "LARGE")
DEFAULT_SIZE = "LARGE"
CURL = "curl"
# curl が --max-time に当たったときの終了コード
CURL_TIMEOUT_EXIT = 28


class Item:
    """項目の result の値（結果のスキーマ gws.result の items[].result の説明に並ぶ値）。"""

    SAVED = "saved"
    UNAUTHENTICATED = "unauthenticated"
    GWS_FAILED = "gws-failed"
    UNREADABLE = "unreadable"
    DOWNLOAD_FAILED = "download-failed"


@dataclass(frozen=True)
class _Deck:
    """操作するプレゼンテーション（gws の実行ファイル・プレゼンテーションの ID・サムネイルの大きさ）。"""

    gws: str
    presentation_id: str
    size: str


class _NeedsLogin(Exception):
    """gws の操作が未認証で失敗した（承認ゲートの結果にする）。"""

    def __init__(self, what: str, detail: str) -> None:
        super().__init__(what)
        self.what = what
        self.detail = detail


def run(tool: str, presentation_id: str, out_dir: str, size: str) -> Outcome:
    """全ページのサムネイルを `p<番号>.png` で保存し、サムネイルの保存の結果と終了コードを返す。"""
    gws, out = _prepare(out_dir)
    try:
        items = _save_all(_Deck(gws, presentation_id, size), out)
    except _NeedsLogin as login:
        return unauthenticated_gate(
            tool,
            "gws が未認証。利用者に `gws auth login` を頼み、終えたら routes の retry を打つ",
            ("gws", "thumbnails", presentation_id, out_dir, "--size", size),
            items=[{"name": login.what, "result": Item.UNAUTHENTICATED, "detail": login.detail}],
        )
    metrics = {"pages": len(items), "size": size, "out_dir": str(out)}
    done = result.result(tool, result.OK, f"{len(items)} ページのサムネイルを保存した", items=items, metrics=metrics)
    return done, exit_codes.first_code(result.OK)


def _prepare(out_dir: str) -> tuple[str, Path]:
    """gws と curl が PATH にあるかを確かめ、出力のディレクトリを作る。gws の実行ファイルと出力のディレクトリを返す。"""
    gws = shutil.which("gws")
    if not gws:
        raise GwsStop("precondition", "gws が PATH に無い", summary="gws が PATH に無い。`ndf gws check` で確かめる")
    if not shutil.which(CURL):
        raise GwsStop("precondition", "curl が PATH に無い（サムネイルの画像を取るのに使う）")
    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GwsStop("unknown", f"出力のディレクトリを作れない: {exc}") from exc
    return gws, out


def _save_all(deck: _Deck, out: Path) -> list[dict[str, str]]:
    """全ページのサムネイルをページ順に保存し、ページごとの saved の項目を返す（最初の失敗で止まる）。"""
    items: list[dict[str, str]] = []
    for no, oid in enumerate(_page_ids(deck), start=1):
        dest = _thumbnail(deck, no, oid, out)
        items.append({"name": f"p{no}", "result": Item.SAVED, "objectId": oid, "detail": str(dest)})
    return items


def _gws(gws: str, args: Sequence[str], what: str) -> Any:
    """gws のスライドの操作を打ち、応答の JSON を返す。未認証の失敗は `_NeedsLogin`、ほかは `GwsStop`。"""
    done = call([gws, *args], LIMIT, f"{what}: gws")
    out = rd.output_tail(done.stdout, done.stderr)
    if done.returncode != 0:
        if _unauthenticated():
            raise _NeedsLogin(what, out)
        raise GwsStop(
            "unknown",
            f"{what}: gws が終了コード {done.returncode} で終わった",
            items=[{"name": what, "result": Item.GWS_FAILED, "detail": out}],
        )
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise GwsStop(
            "invalid", f"{what}: gws の応答が JSON でない", items=[{"name": what, "result": Item.UNREADABLE, "detail": out}]
        ) from exc


def _unauthenticated() -> bool:
    """gws が失敗した後に認証の状態を 1 回読み、未認証かを決める。読めなければ偽（一般の失敗として扱う）。"""
    try:
        status = records_gws.auth_status(LIMIT)
    except records_gws.GwsError:  # noqa: NDF401 外部コマンド: 読めない認証の状態は未認証でないとして元の gws の失敗で止める
        return False
    return not records_gws.authenticated(status)


def _page_ids(deck: _Deck) -> list[str]:
    """ページの ID をページ順に取る。読めなければ `GwsStop`（invalid）。"""
    params = json.dumps({"presentationId": deck.presentation_id, "fields": "slides(objectId)"})
    data = _gws(deck.gws, ["slides", "presentations", "get", "--params", params], "ページの ID の取得")
    slides = data.get("slides") if isinstance(data, dict) else None
    ids = [s.get("objectId") for s in slides or [] if isinstance(s, dict)]  # noqa: NDF501 外部コマンド: slides の無い応答は下で止める
    if not ids or not all(isinstance(i, str) and i for i in ids):
        raise GwsStop("invalid", "ページの ID を読めない（slides[].objectId が無い）")
    return ids


def _thumbnail(deck: _Deck, no: int, object_id: str, out: Path) -> Path:
    """`no` ページ目のサムネイルの URL を取り、curl で `out` の下の `p<番号>.png` へ保存し、そのパスを返す。"""
    what = f"p{no}（{object_id}）のサムネイル"
    dest = out / f"p{no}.png"
    params = {"presentationId": deck.presentation_id, "pageObjectId": object_id, "thumbnailProperties.thumbnailSize": deck.size}
    data = _gws(deck.gws, ["slides", "presentations", "pages", "getThumbnail", "--params", json.dumps(params)], what)
    url = data.get("contentUrl") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url:
        raise GwsStop("invalid", f"{what}: 応答に contentUrl が無い")
    _download_thumbnail(url, dest, what)
    return dest


def _download_thumbnail(url: str, dest: Path, what: str) -> None:
    """サムネイルの画像を curl で `url` から `dest` へ保存する。取れなければ `GwsStop`。"""
    # 打ち切りは curl の --max-time に任せ、subprocess の上限を重ねない
    cmd = [CURL, "-fsSL", "--max-time", str(limits.seconds(LIMIT)), "-o", str(dest), url]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GwsStop("unknown", f"{what}: curl を起動できない: {exc}") from exc
    if done.returncode == CURL_TIMEOUT_EXIT:
        raise GwsStop("interrupted", f"{what}: 画像の取得が上限の {limits.seconds(LIMIT)} 秒を超えた")
    if done.returncode != 0:
        raise GwsStop(
            "unknown",
            f"{what}: 画像を取れない（curl の終了コード {done.returncode}）",
            items=[{"name": what, "result": Item.DOWNLOAD_FAILED, "detail": rd.tail(done.stderr)}],
        )
