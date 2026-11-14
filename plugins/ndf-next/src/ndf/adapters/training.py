"""LLM のアカウントが入力を学習に使わない設定かの確認と、その控え（#3391 の 3391-5）。

確認は副命令 `ndf training check` だけが行い、設定を読むだけで書き換えない（Off への書き換えはしない）。

- claude: OAuth の `GET https://api.anthropic.com/api/oauth/account/settings` の `grove_enabled`
- codex: ChatGPT の `GET https://chatgpt.com/backend-api/settings/user` の `settings.training_allowed` ほか 2 つの鍵
- kiro・agy（`UNSUPPORTED`）: 確かめる口が無いので確かめず、起動は通して記録に `unsupported` を残す

結果は状態の置き場の `training.json` へ時刻つきで控える。LLM と外部 CLI の起動の口（`claude.call_claude`・
`agents.run_agent`）は `refusal` で控えを読むだけで、学習に使う・控えが無い・期限切れ（`contracts/limits.json` の
`training.check-valid`）・読めないなら起動を断り、理由に確認の副命令を載せる。

トークンとアカウントの識別子は `Authorization` などの見出しにだけ置き、控え・出力・記録に含めない。
公開の API ではないため、応答の形が違えば「確かめられない」（`training` が None）とする。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import httpx

from ndf import cli, identity
from ndf.adapters.run_log import record
from ndf.contracts import facts, limits, result

TOOL = cli.tool_of(__name__)
CHECK_COMMAND = "ndf training check"
# 確かめる口の無い担当（kiro・agy など）の記録の値
UNSUPPORTED = "unsupported"
CACHE_FILE = "training.json"
# 控えの期限の方針（contracts/limits.json）
VALID_POLICY = "training.check-valid"

CLAUDE_URL = "https://api.anthropic.com/api/oauth/account/settings"
CLAUDE_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
CLAUDE_CREDENTIALS = ".credentials.json"
KEYCHAIN_SERVICE = "Claude Code-credentials"
# OAuth より優先される認証の変数（値は出さず、名前だけを理由に載せる）
CLAUDE_FOREIGN_AUTH = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
CODEX_URL = "https://chatgpt.com/backend-api/settings/user"
CODEX_KEYS = ("training_allowed", "codex_training_allowed", "codex_training_allowed_v2")
# Python の既定の User-Agent では 403 が返る（2026-10-02 の実測。#1597）
CODEX_USER_AGENT = "ndf-training-check"

NO_TOKEN = "トークンが無い"
NOT_CHATGPT = "ChatGPT のログインでない（API キーのログイン）"
BAD_SHAPE = "応答に学習の設定の真偽値が無い"


class Reading(NamedTuple):
    """1 つの担当の確認の結果。`training` は真（学習に使う）・偽（使わない）・None（確かめられない）。"""

    runtime: str
    training: Optional[bool]
    reason: str

    def item(self, checked_at: str) -> dict[str, object]:
        return {"runtime": self.runtime, "training": self.training, "reason": self.reason, "checked_at": checked_at}


def _unread(runtime: str, reason: str) -> Reading:
    return Reading(runtime, None, reason)


def _get(client: httpx.Client, url: str, headers: Mapping[str, str], timeout: float) -> tuple[Optional[dict], str]:
    """`GET` を 1 回送り、(応答の JSON のオブジェクト, 読めないときの理由)。リダイレクトを辿らない。"""
    try:
        response = client.get(url, headers=dict(headers), timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        return None, f"通信の失敗（{type(exc).__name__}）"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return None, "応答が JSON でない"
    return (body, "") if isinstance(body, dict) else (None, "応答が JSON のオブジェクトでない")


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 無いか壊れていれば無いとして扱う
        return None
    return data if isinstance(data, dict) else None


def _keychain_oauth() -> Optional[dict]:
    """macOS のキーチェーンの `claudeAiOauth`。読めなければ None。"""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=limits.seconds("claude-training.keychain"),
            check=False,
        ).stdout
        entry = json.loads(out).get("claudeAiOauth")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):  # noqa: NDF401 外部コマンド: 読めなければ認証が無いとして扱う
        return None
    return entry if isinstance(entry, dict) else None


def _claude_token(env: Mapping[str, str], now: float) -> tuple[Optional[str], str]:
    """(トークン, 無いときの理由)。`CLAUDE_CODE_OAUTH_TOKEN` → 認証ファイル（→ macOS のキーチェーン）。"""
    if env.get(CLAUDE_TOKEN_ENV):
        return env[CLAUDE_TOKEN_ENV], ""
    conf = env.get("CLAUDE_CONFIG_DIR")
    credentials = _read_json(Path(conf or Path.home() / ".claude") / CLAUDE_CREDENTIALS)
    entry = credentials.get("claudeAiOauth") if credentials is not None else None
    if not isinstance(entry, dict) and not conf and sys.platform == "darwin":
        entry = _keychain_oauth()
    token = entry.get("accessToken") if isinstance(entry, dict) else None
    if not (isinstance(token, str) and token):
        return None, f"claude の OAuth の{NO_TOKEN}（{CLAUDE_TOKEN_ENV}・{CLAUDE_CREDENTIALS}）"
    expires = entry.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 <= now:
        return None, "claude のトークンの期限切れ（claude を 1 度起動して更新する）"
    return token, ""


def read_claude(env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    """claude の学習の設定を読む。例外を出さない。"""
    foreign = [name for name in CLAUDE_FOREIGN_AUTH if env.get(name)]
    if foreign:
        return _unread("claude", f"OAuth 以外の接続が有効（{', '.join(foreign)}）")
    token, why = _claude_token(env, now)
    if token is None:
        return _unread("claude", why)
    headers = {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}
    body, why = _get(client, CLAUDE_URL, headers, limits.seconds("claude-training.call"))
    if body is None:
        return _unread("claude", why)
    grove = body.get("grove_enabled")
    if not isinstance(grove, bool):
        return _unread("claude", BAD_SHAPE)
    return Reading("claude", grove, "oauth/account/settings.grove_enabled")


def _codex_auth(env: Mapping[str, str]) -> tuple[Optional[dict[str, str]], str]:
    """(見出し, 無いときの理由)。ChatGPT のログインのトークンだけを使う。"""
    data = _read_json(Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json")
    if data is None:
        return None, f"codex の{NO_TOKEN}"
    if data.get("auth_mode") != "chatgpt":
        return None, NOT_CHATGPT
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    token, account = tokens.get("access_token"), tokens.get("account_id")
    if not (isinstance(token, str) and token):
        return None, f"codex の{NO_TOKEN}"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": CODEX_USER_AGENT, "Accept": "application/json"}
    if isinstance(account, str) and account:
        headers["ChatGPT-Account-Id"] = account
    return headers, ""


def codex_training(settings: object) -> Optional[bool]:
    """3 つの鍵から学習に使うか。形が違えば None（真の有無より先に形を見る）。"""
    if not isinstance(settings, dict) or not isinstance(settings.get("training_allowed"), bool):
        return None
    if any(key in settings and not isinstance(settings[key], bool) for key in CODEX_KEYS[1:]):
        return None
    return any(settings.get(key) is True for key in CODEX_KEYS)


def read_codex(env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    """codex の学習の設定を読む。例外を出さない。"""
    headers, why = _codex_auth(env)
    if headers is None:
        return _unread("codex", why)
    body, why = _get(client, CODEX_URL, headers, limits.seconds("codex-training.call"))
    if body is None:
        return _unread("codex", why)
    training = codex_training(body.get("settings"))
    if training is None:
        return _unread("codex", BAD_SHAPE)
    return Reading("codex", training, "chatgpt/settings/user.training_allowed")


Reader = Callable[[Mapping[str, str], httpx.Client, float], Reading]


def reader(runtime: str) -> Optional[Reader]:
    """担当の学習の設定の読み手。確かめる口の無い担当（kiro・agy など）は None。"""
    if runtime == "claude":
        return read_claude
    if runtime == "codex":
        return read_codex
    return None


def checked() -> tuple[str, ...]:
    """確かめる口のある担当（`contracts` の表 runtimes の並び）。"""
    return tuple(rt for rt in facts.ids("runtimes") if reader(rt) is not None)


def read_setting(runtime: str, env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    read = reader(runtime)
    return read(env, client, now) if read is not None else _unread(runtime, UNSUPPORTED)


# ---------------------------------------------------------------- 控え


def cache_path() -> Path:
    """控えの置き場（状態の置き場の `training.json`）。"""
    return identity.prepare_state_dir(identity.load()) / CACHE_FILE


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def save(readings: list[Reading], now: float) -> Path:
    """確かめた担当の結果を控えへ書く（ほかの担当の控えは残す）。一時ファイルから置き換える。"""
    path = cache_path()
    kept = _read_json(path)
    if kept is None:
        kept = {}
    kept.update({r.runtime: r.item(_stamp(now)) for r in readings})
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(kept, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _ask_check(runtime: str, why: str) -> str:
    return f"学習に使わない設定と確かめられていないので {runtime} を起動しない（{why}）。`{CHECK_COMMAND} --runtime {runtime}` で確かめる"


def _cached_refusal(runtime: str, entry: object, now: float) -> Optional[str]:
    if not isinstance(entry, dict):
        return _ask_check(runtime, "控えが無い")
    try:
        checked = datetime.fromisoformat(str(entry.get("checked_at"))).timestamp()
    except ValueError:
        return _ask_check(runtime, "控えの時刻が読めない")
    if now - checked > limits.seconds(VALID_POLICY):
        return _ask_check(runtime, f"控えの期限切れ（{entry.get('checked_at')} に確かめた）")
    training = entry.get("training")
    if training is True:
        return f"{runtime} のアカウントが入力を学習に使う設定なので起動しない。設定を Off にしてから `{CHECK_COMMAND} --runtime {runtime}` で確かめ直す"
    if training is not False:
        return _ask_check(runtime, f"確かめられなかった: {entry.get('reason')}")
    return None


def refusal(runtime: str, now: Optional[float] = None) -> Optional[str]:
    """`runtime` を起動してよければ None、断るなら理由。控えを読むだけで、確かめ直さない。

    確かめる口の無い担当（kiro・agy など）は通し、記録に `unsupported` を残す。
    """
    if reader(runtime) is None:
        record("training", runtime=runtime, state=UNSUPPORTED)
        return None
    try:
        path = cache_path()
    except (identity.IdentityError, OSError) as exc:
        return _ask_check(runtime, f"控えの置き場を用意できない: {exc}")
    cache = _read_json(path) if path.is_file() else {}
    if cache is None:
        return _ask_check(runtime, "控えが読めない")
    why = _cached_refusal(runtime, cache.get(runtime), time.time() if now is None else now)
    if why is not None:
        record("training", runtime=runtime, state="refused")
    return why


# ---------------------------------------------------------------- 副命令


def check(runtimes: list[str], env: Mapping[str, str], client: httpx.Client, now: float) -> dict[str, object]:
    """担当ごとに確かめて控え、結果 JSON を組む。すべて学習に使わない設定のときだけ ok。"""
    readings = [read_setting(rt, env, client, now) for rt in dict.fromkeys(runtimes)]
    save([r for r in readings if reader(r.runtime) is not None], now)
    items = [r.item(_stamp(now)) for r in readings]
    unread = [f"{r.runtime}（{r.reason}）" for r in readings if r.training is None]
    used = [r.runtime for r in readings if r.training is True]
    if unread:
        return result.stopped(TOOL, "precondition", f"確かめられない: {', '.join(unread)}", items=items)
    if used:
        return result.stopped(TOOL, "refused", f"学習に使う設定: {', '.join(used)}（設定を Off にしてから確かめ直す）", items=items)
    return result.result(TOOL, result.OK, f"学習に使わない設定: {', '.join(r.runtime for r in readings)}", items=items)


def main(argv: list[str]) -> int:
    """`ndf training check [--runtime claude|codex ...]`: 学習の設定を読んで控える。書き換えない。"""
    parser = argparse.ArgumentParser(prog=CHECK_COMMAND.rsplit(" ", 1)[0], add_help=True)
    sub = parser.add_subparsers(dest="action", required=True)
    checker = sub.add_parser("check")
    checker.add_argument("--runtime", action="append", choices=checked(), help="確かめる担当（既定は確かめる口のある担当すべて）")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        return result.emit(result.stopped(TOOL, "invalid", f"引数が誤っている: {' '.join(argv)}"))
    with httpx.Client() as client:
        return result.emit(check(args.runtime or list(checked()), os.environ, client, time.time()))
