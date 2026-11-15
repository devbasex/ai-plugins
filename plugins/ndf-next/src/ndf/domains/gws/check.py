"""gws の確認（`ndf gws check`）: gws の有無と認証の状態を確かめ、gws の状態と次の手を返す。

| 状態 | status | 終了コード | 次の手 |
| --- | --- | --- | --- |
| authenticated | ok | 0 | 操作へ進む |
| missing | gate | 10 | 承認資料を示して同意を求め、同意を得たら `--install` で打ち直す（routes の approved） |
| unauthenticated | gate | 11 | 利用者に `gws auth login` を頼み、終えたら打ち直す（routes の retry） |
| uninstallable | stopped | 3 | npm（Node.js）を入れてから打ち直す |

- 導入の同意の印（`--install`）があるときだけ `npm install -g @googleworkspace/cli` を打つ（C6）。`sudo` を打たず、PATH と
  設定のファイルを書き換えない
- gws の副命令は `auth status` だけを打つ。読み取りと認証済みかの判定は records の gws の部品が持ち、結果へ写すのは
  3 つのキーだけである。資格情報のファイルは開かない（C1）
- 状態を決められない（`auth status` を読めない）ときは `stopped`（invalid）で返し、未認証に倒さない
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from ndf.contracts import exit_codes, limits, result
from ndf.domains.gws import GwsStop, Outcome, material, unauthenticated_code
from ndf.domains.gws import report_decide as rd
from ndf.domains.records import gws as records_gws

PACKAGE = "@googleworkspace/cli"
PACKAGE_URL = "https://www.npmjs.com/package/@googleworkspace/cli"
# npm の後ろに付ける引数
INSTALL_ARGS = ("install", "-g", PACKAGE)
# 上限の宣言（contracts/limits.json）の名前
CALL_LIMIT = "google-workspace.call"
INSTALL_LIMIT = "google-workspace.install"
# Claude Code とほかのランタイムでのログインの打ち方
_LOGIN = "利用者に `gws auth login` を頼み（Claude Code では `! gws auth login`、ほかのランタイムでは別の端末で打つ）、終えたら `ndf gws check` を打ち直す"


def run(tool: str, install: bool) -> Outcome:
    """gws の有無・npm の有無・導入の同意の印から次の処理を選び、gws の確認の結果と終了コードを返す。"""
    if shutil.which("gws"):
        return _report(tool)
    npm = shutil.which("npm")
    if not npm:
        raise GwsStop(
            "precondition",
            "gws も npm も PATH に無く、gws を導入できない",
            summary="gws も npm も PATH に無い。npm（Node.js）を入れてから `ndf gws check` を打ち直す",
            metrics={"state": rd.UNINSTALLABLE},
        )
    if not install:
        return _present(tool, npm)
    _install(npm)
    if not shutil.which("gws"):
        prefix = _npm_prefix(npm)
        bin_dir = f"`{prefix}/bin`" if prefix else "`npm prefix -g` の bin"
        raise GwsStop(
            "unknown",
            "gws を入れたが PATH に見つからない",
            summary=f"gws を入れたが PATH に見つからない。{bin_dir} を PATH へ足すよう利用者に案内する（NDF は PATH も設定のファイルも書き換えない）",
        )
    return _report(tool)


def _report(tool: str) -> Outcome:
    """認証の状態を読み、records の gws の部品の判定で authenticated か unauthenticated の結果を組む。"""
    try:
        status = records_gws.auth_status(CALL_LIMIT)
    except records_gws.GwsError as exc:
        stderr = rd.tail(exc.stderr)
        items = [{"name": "gws auth status の標準エラーの末尾", "result": "stderr", "detail": stderr}] if stderr else None
        raise GwsStop("invalid", f"gws の認証の状態を読めない: {exc}", items=items) from exc
    keys = rd.pick(status, records_gws.STATUS_KEYS)
    if records_gws.authenticated(status):
        return result.result(tool, result.OK, "gws は認証済み", metrics={"state": rd.AUTHENTICATED, **keys}), exit_codes.first_code(
            result.OK
        )
    summary = "gws はあるが未認証。" + _LOGIN
    if not keys["client_config_exists"]:
        summary = (
            "gws はあるが未認証で、OAuth クライアントが無い。先に `gws auth setup`（gcloud が要る）か、OAuth クライアントの "
            "client_secret.json の用意を利用者に案内する。そのうえで " + _LOGIN
        )
    routes = {"retry": result.command("gws", "check"), "cancel": None}
    out = result.result(tool, result.GATE, summary, metrics={"state": rd.UNAUTHENTICATED, **keys}, routes=routes)
    return out, unauthenticated_code()


def _present(tool: str, npm: str) -> Outcome:
    """導入の承認資料を書き、missing の承認ゲートの結果を組む。"""
    prefix = _npm_prefix(npm)
    where = f"`{prefix}`（`npm prefix -g` の値）" if prefix else "`npm prefix -g` が示す場所（値を読めなかった）"
    text = material.render(package=PACKAGE, package_url=PACKAGE_URL, command=("npm", *INSTALL_ARGS), where=where)
    try:
        path = material.write(text)
    except OSError as exc:
        raise GwsStop("unknown", f"導入の承認資料を書けない: {exc}") from exc
    routes = {"approved": result.command("gws", "check", "--install"), "rejected": None}
    summary = "gws が PATH に無い。承認資料を示して導入の同意を求める（同意を得たら routes の approved、断られたら止まる）"
    out = result.result(tool, result.GATE, summary, metrics={"state": rd.MISSING}, presentation_path=str(path), routes=routes)
    return out, exit_codes.first_code(result.GATE)


def _install(npm: str) -> None:
    """`npm install -g @googleworkspace/cli` を 1 回打つ。失敗したら止まる（`sudo` で打ち直さない）。"""
    no_sudo = "出力を利用者に示して止まる。sudo で打ち直さない"
    try:
        done = subprocess.run([npm, *INSTALL_ARGS], capture_output=True, text=True, timeout=limits.seconds(INSTALL_LIMIT), check=False)
    except subprocess.TimeoutExpired as exc:
        raise GwsStop("interrupted", f"npm install -g が上限の {exc.timeout} 秒を超えた", summary=no_sudo) from exc
    except OSError as exc:
        raise GwsStop("unknown", f"npm install を起動できない: {exc}", summary=no_sudo) from exc
    if done.returncode != 0:
        raise GwsStop(
            "unknown",
            f"npm install -g が終了コード {done.returncode} で終わった",
            summary=f"npm install -g が終了コード {done.returncode} で終わった。{no_sudo}",
            items=[{"name": "npm の出力の末尾", "result": "failed", "detail": rd.tail(done.stdout + "\n" + done.stderr)}],
        )


def _npm_prefix(npm: str) -> Optional[str]:
    """npm の全体インストールの置き場（読むだけ）。読めなければ None。"""
    try:
        done = subprocess.run([npm, "prefix", "-g"], capture_output=True, text=True, timeout=limits.seconds(CALL_LIMIT), check=False)
    except (OSError, subprocess.SubprocessError):  # noqa: NDF401 外部コマンド: 読めない置き場は承認資料と案内の文を変えるだけ
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and out else None
