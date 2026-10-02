#!/usr/bin/env python3
"""training-optout.py: LLM のアカウントが入力を学習に使わない設定かを確かめ、NDF のセッションの開始で Off にする（#1597）。

    python3 training-optout.py check [--runtime claude|codex|kiro|agy ...] [--config-dir <設定ディレクトリ> ...]
    python3 training-optout.py session-start [--runtime claude|codex]

`check` は学習の設定を読むだけで書き換えない。結果は lib/step_result.py の形の 1 行の JSON で、すべてが学習に使わない
設定なら ok（0）、学習に使う設定があれば stopped（1）、確かめられないもの（認証が無い・期限切れ・OAuth 以外の接続・
従量の接続の宣言・HTTP の失敗・応答の形の違い・unsupported）があれば stopped（3）。読み手は ok のときだけ
「学習に使わない」と扱う。claude は OAuth の `account/settings` の `grove_enabled`、codex は ChatGPT の `settings/user` の
3 つの鍵を読み、kiro / agy は `unsupported`。`--config-dir` を渡すと、既定のアカウントに加えて渡した設定ディレクトリごとに
その認証ファイルのトークンで確かめる（supervise が切り替え得るアカウントをすべて確かめる）。従量の接続の宣言
（`NDF_SUPERVISE_CLAUDE_FALLBACK`、無ければ置き場の `metered.json`）は確かめられないとし、外すには
`NDF_SUPERVISE_CLAUDE_FALLBACK=` を空で定義する。

`session-start` は SessionStart の hook から呼ぶ。学習の設定が true なら false へ書き換え、利用者へ `systemMessage` で
知らせる。既に false なら何も送らず何も出さない。失敗しても終了コードは常に 0 で、失敗を知らせる。
`NDF_TRAINING_OPTOUT=0` なら読みも書きも送らない（`check` はこの変数を読まない）。
トークンとアカウントの ID は出力に出さない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import claude_accounts as ca  # noqa: E402
import claude_training as ct  # noqa: E402
import codex_training as xt  # noqa: E402
from step_result import EXIT_OK, EXIT_PRECONDITION, EXIT_VIOLATION, emit, main_with, result  # noqa: E402

TOOL = "training-optout"
RUNTIMES = ("claude", "codex", "kiro", "agy")
OPTOUT_ENV = "NDF_TRAINING_OPTOUT"
STOP = f"止めるには {OPTOUT_ENV}=0"
CODEX_LABELS = {
    "training_allowed": "「Improve the model for everyone」",
    "codex_training_allowed": "Codex の環境の学習（codex_training_allowed）",
    "codex_training_allowed_v2": "Codex の環境の学習（codex_training_allowed_v2）",
}


# --- check -------------------------------------------------------------------


def declared_metered() -> list[ct.Reading]:
    """従量の接続の宣言があれば、確かめられない 1 項目（変数の名前だけを出す）。"""
    decl = ca.fallback_env()
    if not decl:
        return []
    reason = f"従量の接続の宣言がある（{', '.join(decl)}。外すには {ca.FALLBACK_ENV}= を空で定義する）"
    return [ct.unread(reason, config_dir=ca.METERED)]


def checked_name(item: dict) -> str:
    return f"{item['runtime']}（{item['config_dir']}）" if item["config_dir"] else item["runtime"]


def readings(runtime: str, dirs: list[str]) -> list[ct.Reading]:
    if runtime == "claude":
        return [ct.read_setting(), *(ct.read_setting(config_dir=d) for d in dirs), *declared_metered()]
    if runtime == "codex":
        return [xt.read_setting()]
    return [ct.unread("unsupported", runtime)]


def cmd_check(a):
    dirs = list(dict.fromkeys(a.config_dir or []))
    items = [r.to_item() for rt in dict.fromkeys(a.runtime or ["claude"]) for r in readings(rt, dirs)]
    used = [checked_name(i) for i in items if i["training"] is True]
    unread = [checked_name(i) for i in items if i["training"] is None]
    if unread:
        code, summary = EXIT_PRECONDITION, f"確かめられない: {', '.join(unread)}"
    elif used:
        code, summary = EXIT_VIOLATION, f"学習に使う設定: {', '.join(used)}"
    else:
        code, summary = EXIT_OK, f"学習に使わない設定: {', '.join(map(checked_name, items))}"
    emit(result(TOOL, "ok" if code == EXIT_OK else "stopped", summary, items), code)


# --- session-start -----------------------------------------------------------


def read_failed(reason: str) -> str:
    return f"[ndf] 学習の設定を確かめられなかった（{reason}）。「学習に使わない」とは扱わない。{STOP}"


def claude_session(env) -> str | None:
    """claude の書き換え。知らせの文面か None（知らせない）を返す。"""
    if ct.foreign_auth(env):
        return None  # OAuth でない接続には学習の設定が無い（I4）
    url = ct.target(env, ct.URL_ENV, ct.URL)
    if url is None:
        return read_failed(ct.NOT_LOCAL)
    token = ct.oauth_token(env)
    if not token:
        return read_failed(ct.NO_TOKEN)
    reading = ct.read_with(token, url)
    if reading.training is None:
        return read_failed(reading.reason)
    if reading.training is False:
        return None
    why = ct.turn_off(token, url)
    if why:
        return (
            f"[ndf] 学習の設定を Off にできなかった（{why}）。設定画面の「Help improve our AI models」を確かめる。{STOP}"
        )
    return (
        "[ndf] このアカウントの「Help improve our AI models」を Off にした（入力を学習に使わない設定）。"
        f"外すには {OPTOUT_ENV}=0 を設定する"
    )


def codex_session(env) -> str | None:
    """codex の書き換え。true の鍵ごとに 1 回送る。"""
    url = ct.target(env, xt.URL_ENV, xt.URL)
    if url is None:
        return read_failed(ct.NOT_LOCAL)
    cred, why = xt.auth(env)
    if cred is None:
        return None if why == xt.NOT_CHATGPT else read_failed(why)  # API キーのログインには学習の設定が無い
    reading, keys = xt.read_with(cred, url)
    if reading.training is None:
        return read_failed(reading.reason)
    if not keys:
        return None
    failed = [(k, r) for k in keys if (r := xt.turn_off(cred, url, k))]
    done = [CODEX_LABELS[k] for k in keys if k not in dict(failed)]
    if failed:
        reasons = "・".join(sorted({r for _, r in failed}))
        names = "・".join(CODEX_LABELS[k] for k, _ in failed)
        return f"[ndf] ChatGPT のアカウントの {names} を Off にできなかった（{reasons}）。ChatGPT の設定画面を確かめる。{STOP}"
    return (
        f"[ndf] この ChatGPT のアカウントの {'・'.join(done)} を Off にした（入力を学習に使わない設定）。"
        f"外すには {OPTOUT_ENV}=0 を設定する"
    )


def cmd_session_start(a) -> int:
    """どの失敗でも終了コード 0（I6）。知らせがあるときだけ `{"systemMessage": …}` を 1 行出す。"""
    env = os.environ
    if env.get(OPTOUT_ENV) == "0":
        return 0  # 書き換えの無効化（I3）
    try:
        msg = (codex_session if a.runtime == "codex" else claude_session)(env)
    except Exception as e:  # noqa: BLE001 予期しない例外も失敗の知らせに変える（理由は型名だけ）
        msg = read_failed(type(e).__name__)
    if msg:
        try:
            print(json.dumps({"systemMessage": msg}, ensure_ascii=False), flush=True)
        except Exception:  # noqa: BLE001 知らせの出力自体が失敗しても止めない
            pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(required=True)
    c = sub.add_parser("check")
    c.add_argument("--runtime", action="append", choices=RUNTIMES)
    c.add_argument("--config-dir", action="extend", nargs="+", help="claude の設定ディレクトリ（既定のアカウントに加えて確かめる）")
    c.set_defaults(func=cmd_check)
    s = sub.add_parser("session-start")
    s.add_argument("--runtime", choices=("claude", "codex"), default="claude")
    s.set_defaults(func=cmd_session_start)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
