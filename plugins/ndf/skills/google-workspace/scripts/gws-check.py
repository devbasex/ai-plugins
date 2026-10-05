#!/usr/bin/env python3
"""gws-check.py: gws（Google Workspace CLI）の有無と認証の状態を確かめ、次の手を返す（#744）。

    python3 gws-check.py [--install]

結果は `step_result` の形の 1 行の JSON で標準出力へ出し、終了コードで終える。

| 状態 | status | 終了コード | 次の手 |
| --- | --- | --- | --- |
| authenticated | ok | 0 | 操作へ進む |
| missing | gate | 10 | 承認資料を示して同意を求め、同意を得たら --install で打ち直す |
| unauthenticated | gate | 11 | 利用者に gws auth login を案内する |
| uninstallable | stopped | 3 | npm（Node.js）を入れてから打ち直す |

- `--install` のときだけ `npm install -g @googleworkspace/cli` を打つ（同意の印。C6）
- gws の副命令は `auth status` だけを打つ。`auth login` などは打たない（ブラウザの承認は利用者が行う）
- `auth status` の出力から読むのは `credential_source`・`auth_method`・`client_config_exists` だけで、
  資格情報のファイルは開かない（C1）
- 状態を決められない（`auth status` が 0 以外・JSON でない・キーが欠ける）ときは stopped と 2 で返す
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

TOOL = "google-workspace"
PACKAGE = "@googleworkspace/cli"
PACKAGE_URL = "https://www.npmjs.com/package/@googleworkspace/cli"
INSTALL_CMD = ["npm", "install", "-g", PACKAGE]
PROBE_KEYS = ("credential_source", "auth_method", "client_config_exists")
EXIT_UNAUTHENTICATED = sr.EXIT_GATE + 1
TAIL_LINES = 20
TIMEOUT = 120
INSTALL_TIMEOUT = 600


def _tail(text: str) -> str:
    return "\n".join((text or "").strip().splitlines()[-TAIL_LINES:])


def _run(cmd: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _npm_prefix(npm: str) -> str | None:
    """npm の全体インストールの置き場所（読むだけ）。読めなければ None。"""
    try:
        cp = _run([npm, "prefix", "-g"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = cp.stdout.strip()
    return out if cp.returncode == 0 and out else None


def _halt(summary: str, code: int, items=None, next_: str | None = None, metrics=None):
    sr.emit(sr.result(TOOL, "stopped", summary, items=items, metrics=metrics, next=next_), code)


def _probe(gws: str) -> dict:
    """`gws auth status` を 1 回打ち、3 つのキーだけを返す。読めなければ stopped と 2 で終える。"""
    try:
        cp = _run([gws, "auth", "status"])
    except (OSError, subprocess.TimeoutExpired) as e:
        _halt(f"gws auth status を起動できない: {e}", sr.EXIT_UNREADABLE)
    stderr_item = [{"name": "gws auth status の標準エラーの末尾", "result": "stderr", "detail": _tail(cp.stderr)}]
    if cp.returncode != 0:
        _halt(f"gws auth status が終了コード {cp.returncode} で終わった", sr.EXIT_UNREADABLE, items=stderr_item)
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError:
        _halt("gws auth status の出力が JSON でない", sr.EXIT_UNREADABLE, items=stderr_item)
    if not isinstance(data, dict) or any(k not in data for k in PROBE_KEYS):
        _halt(f"gws auth status の出力に {' / '.join(PROBE_KEYS)} のどれかが無い", sr.EXIT_UNREADABLE, items=stderr_item)
    return {k: data[k] for k in PROBE_KEYS}


def _report_gws(gws: str) -> None:
    probe = _probe(gws)
    metrics = {"state": "", **probe}
    if probe["credential_source"] != "none":
        metrics["state"] = "authenticated"
        sr.emit(sr.result(TOOL, "ok", "gws は認証済み", metrics=metrics, next="Skill の操作の対応表のコマンドへ進む"), sr.EXIT_OK)
    metrics["state"] = "unauthenticated"
    login = "Claude Code では `! gws auth login` を、ほかのランタイムでは別の端末で `gws auth login` を打つよう利用者に案内し、終えたら引数なしで打ち直す"
    if probe["client_config_exists"]:
        next_ = login
    else:
        next_ = (
            "OAuth クライアントが無い。先に `gws auth setup`（gcloud が要る）か、OAuth クライアントの "
            "client_secret.json の用意を利用者に案内する。そのうえで " + login
        )
    sr.emit(sr.result(TOOL, "gate", "gws はあるが未認証", metrics=metrics, next=next_), EXIT_UNAUTHENTICATED)


def _present_install(npm: str) -> str:
    prefix = _npm_prefix(npm)
    where = f"`{prefix}`（`npm prefix -g` の値）" if prefix else "`npm prefix -g` が示す場所（値を読めなかった）"
    return sr.approval_present(
        TOOL,
        "install",
        title="gws（Google Workspace CLI）の導入の同意",
        targets=[{"url": PACKAGE_URL, "title": f"npm の {PACKAGE}"}],
        change="npm の全体インストール 1 件",
        judge=[
            ("取得元", f"npm の `{PACKAGE}`"),
            ("打つコマンド", f"`{' '.join(INSTALL_CMD)}`"),
            ("入る先", where),
            ("注意", "Google の公式サポート外の CLI で、版は 0.x"),
        ],
        consent=["上の取得元から利用者の環境へ全体インストールを行う"],
        rollback=f"`npm uninstall -g {PACKAGE}`",
    )


def _install(npm: str) -> None:
    try:
        cp = _run([npm, *INSTALL_CMD[1:]], timeout=INSTALL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        _halt(f"npm install を起動できない: {e}", sr.EXIT_VIOLATION)
    if cp.returncode != 0:
        _halt(
            f"npm install -g が終了コード {cp.returncode} で終わった",
            sr.EXIT_VIOLATION,
            items=[{"name": "npm の出力の末尾", "result": "failed", "detail": _tail(cp.stdout + "\n" + cp.stderr)}],
            next_="出力を利用者に示して止まる。sudo で打ち直さない",
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="gws の有無と認証の状態を確かめる")
    ap.add_argument("--install", action="store_true", help="利用者が導入に同意した印。gws が無ければ npm で入れる")
    args = ap.parse_args(argv)

    gws = shutil.which("gws")
    if gws:
        _report_gws(gws)
    npm = shutil.which("npm")
    if not npm:
        _halt(
            "gws も npm も PATH に無く、gws を導入できない",
            sr.EXIT_PRECONDITION,
            metrics={"state": "uninstallable"},
            next_="npm（Node.js）を入れてから打ち直すよう利用者に案内して止まる",
        )
    if not args.install:
        path = _present_install(npm)
        sr.emit(
            sr.result(
                TOOL,
                "gate",
                "gws が PATH に無い",
                metrics={"state": "missing"},
                presentation_path=path,
                next="承認資料を示して導入の同意を求め、同意を得たら --install を付けて打ち直す。断られたら止まる",
            ),
            sr.EXIT_GATE,
        )
    _install(npm)
    gws = shutil.which("gws")
    if not gws:
        prefix = _npm_prefix(npm)
        bin_dir = f"{prefix}/bin" if prefix else "`npm prefix -g` の bin"
        _halt(
            "gws を入れたが PATH に見つからない",
            sr.EXIT_VIOLATION,
            next_=f"{bin_dir} を PATH へ足すよう利用者に案内して止まる（スクリプトは PATH も設定のファイルも書き換えない）",
        )
    _report_gws(gws)


if __name__ == "__main__":
    main()
