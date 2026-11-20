"""LLM のアカウントが入力を学習に使わない設定かの確認と、そのバックアップ（#3391 の 3391-5）。

確認はサブコマンド `ndf training check` と、バックアップが無いか期限切れのときの起動の口（`ensure`。#3907）が行う。
どちらも `settle` を通り、設定を読んで学習に使う設定なら、true のキーだけを false へ書き込んで 1 回読み直す（#3907 の 5）。
読み直して false なら書き換えた時刻と前後の値（`rewrites`）つきで記録し、そうでなければ学習に使う設定として記録して断る。

- claude: OAuth の `GET https://api.anthropic.com/api/oauth/account/settings` の `grove_enabled`
- codex: ChatGPT の `GET https://chatgpt.com/backend-api/settings/user` の `settings.training_allowed` ほか 2 つのキー
- kiro・agy（`UNSUPPORTED`）: 確かめる口が無いので確かめず、起動は通して記録に `unsupported` を残す

結果は状態の保存先の `training.json` へ時刻つきで記録する。バックアップの読み書きと、バックアップを読んで起動を断る規則
（`refusal`）は `training_cache.py` が持つ（起動の口は httpx を読まずに済む）。

トークンとアカウントの識別子は `Authorization` などの見出しにだけ置き、バックアップ・出力・記録に含めない。
公開の API ではないため、応答の形が違えば「確かめられない」（`training` が None）とする。
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple, Optional, Union

import filelock
import httpx

from ndf import cli, identity
from ndf.adapters.run_log import record

# バックアップと起動を断る規則は training_cache が持つ。既存の呼び出し元のために名前を再輸出する
from ndf.adapters.training_cache import (
    CACHE_FILE as CACHE_FILE,
    CHECK_COMMAND,
    CHECKED_RUNTIMES,
    CLAUDE_CREDENTIALS,
    CLAUDE_FOREIGN_AUTH as CLAUDE_FOREIGN_AUTH,
    CLAUDE_TOKEN_ENV as CLAUDE_TOKEN_ENV,
    REFUSED as REFUSED,
    UNSUPPORTED,
    VALID_POLICY as VALID_POLICY,
    Reading,
    Rewrite,
    _read_json,
    _stamp,
    cache_path as cache_path,
    claude_oauth_entry,
    context,
    forget,
    off_by_hand,
    refusal as refusal,
    save,
    verdict,
)
from ndf.contracts import facts, limits, result

TOOL = cli.tool_of(__name__)
CLAUDE_URL = "https://api.anthropic.com/api/oauth/account/settings"
CODEX_URL = "https://chatgpt.com/backend-api/settings/user"
# codex の学習の設定を 1 キーずつ書き込む口（10 系の #1597 の決定 14 と 2026-11-20 の確かめの形）
CODEX_PATCH_URL = "https://chatgpt.com/backend-api/settings/account_user_setting"
CODEX_KEYS = ("training_allowed", "codex_training_allowed", "codex_training_allowed_v2")
# Python の既定の User-Agent では 403 が返る（2026-10-02 の実測。#1597）
CODEX_USER_AGENT = "ndf-training-check"

NO_TOKEN = "トークンが無い"
NOT_CHATGPT = "ChatGPT のログインでない（API キーのログイン）"
BAD_SHAPE = "応答に学習の設定の真偽値が無い"
BUSY = "ほかの確かめが終わらない"
# httpx の上限が掛かる段階（接続・送信・受信の待ち）の数。同じ担当の確かめを待つ側の打ち切りに使う（#3907 の決定 11）
HTTP_STAGES = 3


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


def _claude_token(env: Mapping[str, str], now: float) -> tuple[Optional[str], str]:
    """(トークン, 無いときの理由)。`CLAUDE_CODE_OAUTH_TOKEN` → 認証ファイル（→ macOS のキーチェーン）。"""
    if env.get(CLAUDE_TOKEN_ENV):
        return env[CLAUDE_TOKEN_ENV], ""
    entry = claude_oauth_entry(env)
    token = entry.get("accessToken") if entry is not None else None
    if not (isinstance(token, str) and token):
        return None, f"claude の OAuth の{NO_TOKEN}（{CLAUDE_TOKEN_ENV}・{CLAUDE_CREDENTIALS}）"
    expires = entry.get("expiresAt")
    if isinstance(expires, (int, float)) and expires / 1000 <= now:
        return None, "claude のトークンの期限切れ（claude を 1 度起動して更新する）"
    return token, ""


class Patch(NamedTuple):
    """1 つのキーを false へ書き込む要求の形（`params` は問い合わせの文字列、`body` は JSON の本文）。"""

    url: str
    params: Optional[dict[str, str]]
    body: Optional[dict[str, bool]]


class Endpoint(NamedTuple):
    """学習の設定を読み書きする口。`url` を `policy`（`contracts/limits.json`）の時間で読み、`values` でキーごとの真偽値を取り出す。

    `source` は書き換えないときの理由に載せる設定の在りか、`place` は書き換えた後の理由に載せる設定の在りか（キーを含まない）、
    `patch` は 1 つのキーを false へ書き込む要求の形。
    """

    url: str
    policy: str
    values: Callable[[dict], Optional[dict[str, bool]]]
    source: str
    place: str
    patch: Callable[[str], Patch]


def _grove_values(body: dict) -> Optional[dict[str, bool]]:
    grove = body.get("grove_enabled")
    return {"grove_enabled": grove} if isinstance(grove, bool) else None


def codex_values(settings: object) -> Optional[dict[str, bool]]:
    """3 つのキーのうち応答にあるものの真偽値。形が違えば None（`training_allowed` は必ず真偽値、ほかはあれば真偽値）。"""
    if not isinstance(settings, dict) or not isinstance(settings.get("training_allowed"), bool):
        return None
    if any(key in settings and not isinstance(settings[key], bool) for key in CODEX_KEYS[1:]):
        return None
    return {key: settings[key] for key in CODEX_KEYS if key in settings}


def codex_training(settings: object) -> Optional[bool]:
    """3 つのキーから学習に使うか。形が違えば None（真の有無より先に形を見る）。"""
    values = codex_values(settings)
    return None if values is None else any(values.values())


CLAUDE_ENDPOINT = Endpoint(
    CLAUDE_URL,
    "claude-training.call",
    _grove_values,
    "oauth/account/settings.grove_enabled",
    "oauth/account/settings",
    lambda key: Patch(CLAUDE_URL, None, {key: False}),
)
CODEX_ENDPOINT = Endpoint(
    CODEX_URL,
    "codex-training.call",
    lambda body: codex_values(body.get("settings")),
    "chatgpt/settings/user.training_allowed",
    "chatgpt/settings/user",
    lambda key: Patch(CODEX_PATCH_URL, {"feature": key, "value": "false"}, None),
)


def _endpoints() -> dict[str, Endpoint]:
    """確かめる口のある担当の読み書きの口（担当の集まりは `_readers` と同じ）。"""
    return {"claude": CLAUDE_ENDPOINT, "codex": CODEX_ENDPOINT}


def _claude_headers(env: Mapping[str, str], now: float) -> tuple[Optional[dict[str, str]], str]:
    """(見出し, 無いときの理由)。OAuth より優先される接続が有効なら見出しを作らない。"""
    foreign = [name for name in CLAUDE_FOREIGN_AUTH if env.get(name)]
    if foreign:
        return None, f"OAuth 以外の接続が有効（{', '.join(foreign)}）"
    token, why = _claude_token(env, now)
    if token is None:
        return None, why
    return {"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20", "Accept": "application/json"}, ""


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


def _auth(runtime: str, env: Mapping[str, str], now: float) -> tuple[Optional[dict[str, str]], str]:
    """担当の (見出し, 無いときの理由)。"""
    return _claude_headers(env, now) if runtime == "claude" else _codex_auth(env)


def _values(client: httpx.Client, endpoint: Endpoint, headers: Mapping[str, str]) -> tuple[Optional[dict[str, bool]], str]:
    """`GET` を 1 回送り、(キーごとの真偽値, 読めないときの理由)。"""
    body, why = _get(client, endpoint.url, headers, limits.seconds(endpoint.policy))
    if body is None:
        return None, why
    values = endpoint.values(body)
    return (values, "") if values is not None else (None, BAD_SHAPE)


def _first_read(
    runtime: str, auth: tuple[Optional[Mapping[str, str]], str], client: httpx.Client, endpoint: Endpoint
) -> Reading | tuple[Mapping[str, str], dict[str, bool]]:
    """(見出し, 無いときの理由) で `endpoint` を 1 回読む。読めれば (見出し, キーごとの真偽値)、読めなければその `Reading`。"""
    headers, why = auth
    if headers is None:
        return _unread(runtime, why)
    values, why = _values(client, endpoint, headers)
    if values is None:
        return _unread(runtime, why)
    return headers, values


def _read_over_http(runtime: str, auth: tuple[Optional[Mapping[str, str]], str], client: httpx.Client, endpoint: Endpoint) -> Reading:
    """(見出し, 無いときの理由) で `endpoint` を読み、学習の設定の真偽値を取り出す。例外を出さない。"""
    got = _first_read(runtime, auth, client, endpoint)
    if isinstance(got, Reading):
        return got
    _, values = got
    return Reading(runtime, any(values.values()), endpoint.source)


def read_claude(env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    """claude の学習の設定を読む。書き込まない。例外を出さない。"""
    return _read_over_http("claude", _claude_headers(env, now), client, CLAUDE_ENDPOINT)


def read_codex(env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    """codex の学習の設定を読む。書き込まない。例外を出さない。"""
    return _read_over_http("codex", _codex_auth(env), client, CODEX_ENDPOINT)


Reader = Callable[[Mapping[str, str], httpx.Client, float], Reading]


def _readers() -> dict[str, Reader]:
    """確かめる口のある担当（表 runtimes の training_check）と、その読み手。"""
    return {"claude": read_claude, "codex": read_codex}


if set(_readers()) != CHECKED_RUNTIMES or set(_endpoints()) != CHECKED_RUNTIMES:
    raise ImportError(
        f"読み手の担当 {sorted(_readers())}・読み書きの口の担当 {sorted(_endpoints())} がバックアップの規則の担当 {sorted(CHECKED_RUNTIMES)} と食い違う"
    )


def reader(runtime: str) -> Optional[Reader]:
    """担当の学習の設定の読み手。確かめる口の無い担当（kiro・agy など）は None。"""
    return _readers().get(runtime)


def checked() -> tuple[str, ...]:
    """確かめる口のある担当（`contracts` の表 runtimes の並び）。"""
    return tuple(rt for rt in facts.ids("runtimes") if reader(rt) is not None)


def read_setting(runtime: str, env: Mapping[str, str], client: httpx.Client, now: float) -> Reading:
    read = reader(runtime)
    return read(env, client, now) if read is not None else _unread(runtime, UNSUPPORTED)


def _send_off(client: httpx.Client, endpoint: Endpoint, headers: Mapping[str, str], key: str) -> str:
    """1 つのキーを false へ書き込む。受け付けられた（2xx）なら空文字、そうでなければ失敗の理由。リダイレクトを辿らない。

    応答の本文は読まない（202 は処理の受け付けで、保存を約束しない。成否は読み直しで決める。#3907 の決定 2）。
    """
    patch = endpoint.patch(key)
    try:
        response = client.request(
            "PATCH",
            patch.url,
            params=patch.params,
            json=patch.body,
            headers=dict(headers),
            timeout=limits.seconds(endpoint.policy),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return f"{key} が通信の失敗（{type(exc).__name__}）"
    return "" if 200 <= response.status_code < 300 else f"{key} が HTTP {response.status_code}"


def settle(runtime: str, env: Mapping[str, str], client: httpx.Client, now: float, clock: Callable[[], float] = time.time) -> Reading:
    """学習の設定を読み、学習に使う設定なら true のキーだけを false へ書き込んで 1 回読み直す（#3907 の 5）。例外を出さない。

    学習に使わない設定と確かめられないときは書き込まず、`read_setting` と同じ結果を返す。書き込んだときは、最初に true だった
    キーがすべて読み直しで明示的に false で、ほかのキーにも true が無いときだけ `training` を false にする。理由の文には
    キーの名前・HTTP の状態・例外の型名だけを入れ、見出しと応答の本文を入れない。
    """
    endpoint = _endpoints().get(runtime)
    if endpoint is None:
        return _unread(runtime, UNSUPPORTED)
    got = _first_read(runtime, _auth(runtime, env, now), client, endpoint)
    if isinstance(got, Reading):
        return got
    headers, values = got
    on = [key for key, value in values.items() if value]
    if not on:
        return Reading(runtime, False, endpoint.source)
    sent: list[tuple[str, str]] = []
    failed: list[str] = []
    for key in on:
        at = _stamp(clock())
        why = _send_off(client, endpoint, headers, key)
        if why:
            failed.append(why)
        else:
            sent.append((key, at))
    again, why_again = _values(client, endpoint, headers)
    rewrites = tuple(Rewrite(key, True, None if again is None else again.get(key), at) for key, at in sent)
    reading = _after_rewrite(runtime, endpoint, on, failed, again, why_again)._replace(rewrites=rewrites)
    record("training", runtime=runtime, state="rewritten" if reading.training is False else "rewrite-failed", keys=on)
    return reading


def _after_rewrite(
    runtime: str, endpoint: Endpoint, on: list[str], failed: list[str], again: Optional[dict[str, bool]], why_again: str
) -> Reading:
    """書き込みと読み直しの結果から、担当の確認の結果を決める（`rewrites` は呼び出し元が足す）。"""
    if failed:
        return Reading(runtime, True, "・".join(failed))
    if again is None:
        return Reading(runtime, True, f"書き換えた後に読み直せない（{why_again}）")
    still = [key for key, value in again.items() if value]
    if still:
        return Reading(runtime, True, f"書き換えた後も学習に使う設定のまま: {'・'.join(still)}")
    missing = [key for key in on if key not in again]
    if missing:
        return Reading(runtime, True, f"書き換えた後の読み直しにキーが無い: {'・'.join(missing)}")
    off = "・".join(key for key, value in again.items() if value is False)
    return Reading(runtime, False, f"{endpoint.place}（Off へ書き換えた: {'・'.join(on)}。読み直して false: {off}）")


Read = Callable[[str, Mapping[str, str], float], Reading]


def _read_now(runtime: str, env: Mapping[str, str], now: float) -> Reading:
    """起動の口の既定の確かめ。読むだけでなく、学習に使う設定なら書き換えて読み直す（`settle`）。"""
    with httpx.Client() as client:
        return settle(runtime, env, client, now)


def _settle_wait(runtime: str) -> float:
    """同じ担当のほかの確かめを待つ側が打ち切る秒（claude 27 秒・codex 45 秒）。持ち手の処理の上限ではない。

    要求の数は、読む 1 回・書き込めるキーの数（claude 1・codex 3）・読み直す 1 回の和（claude 3・codex 5）。
    """
    writes = len(CODEX_KEYS) if runtime == "codex" else 1
    return (writes + 2) * HTTP_STAGES * limits.seconds(_endpoints()[runtime].policy)


def _settle_lock(runtime: str) -> filelock.FileLock:
    """同じ担当の確かめの排他（状態の保存先の `training.<担当>.lock`）。待ちきれなければ `filelock.Timeout`。"""
    return filelock.FileLock(str(cache_path().with_name(f"training.{runtime}.lock")), timeout=_settle_wait(runtime))


def ensure(
    runtime: str, env: Optional[Mapping[str, str]] = None, now: Optional[float] = None, read: Optional[Read] = None
) -> Optional[str]:
    """起動してよければ None、断るなら理由。バックアップが確かめ直せば直りうる（`verdict` の `stale`。無い・期限切れ・
    取り消し済み・認証の文脈が違う）なら、その場で確かめて記録してから判定する（#3907）。

    10 系から上げた利用者はバックアップが無い状態から始まるため、起動の口で `ndf training check` の分を済ませる。
    確かめて学習に使わない設定なら起動を続け、学習に使う設定か確かめられなければ今までどおり断る。`read` はテストで差し替える。
    """
    env = os.environ if env is None else env
    if not verdict(runtime, now, env).stale or reader(runtime) is None:
        return refusal(runtime, now, env)
    at = time.time() if now is None else now
    # 待つ間にほかの確かめが期限内の記録を書いていれば、確かめ直さずにその記録で決める
    done = _locked_check(runtime, env, at, lambda: (read or _read_now)(runtime, env, at), needed=lambda: verdict(runtime, now, env).stale)
    if isinstance(done, filelock.Timeout):
        return f"{refusal(runtime, now, env)}（{BUSY}）"
    if isinstance(done, Exception):
        return f"{refusal(runtime, now, env)}（その場で確かめられない: {done}）"
    if done is None:
        return refusal(runtime, now, env)
    got, unsaved = done
    if unsaved is not None:
        outcome = "" if got.training is False else f"。確かめた結果: {got.reason}"
        return f"{refusal(runtime, now, env)}（その場で確かめた結果を記録できなかった: {unsaved}{outcome}{_rewrite_note(got)}）"
    why = refusal(runtime, now, env)
    return None if why is None else f"バックアップが無いか期限切れのため、その場で確かめた: {why}"


def _locked_check(
    runtime: str,
    env: Mapping[str, str],
    now: float,
    run: Callable[[], Reading],
    needed: Callable[[], bool] = lambda: True,
) -> Union[filelock.Timeout, identity.IdentityError, OSError, None, tuple[Reading, Optional[str]]]:
    """同じ担当の排他の中で `run` で確かめ、`now` の時刻で記録する（書けなければ取り消す）。`ensure` と `_check_one` が共有する。

    排他を取れなければその例外（待ちきれなければ `filelock.Timeout`）を返し、排他の中で `needed` が偽なら確かめずに None、
    確かめたら (結果, `_save_or_forget` の理由) を返す。排他を取った後の例外は呼び出し元へそのまま出す。
    """
    try:
        held = _settle_lock(runtime)
        held.acquire()
    except (filelock.Timeout, identity.IdentityError, OSError) as exc:
        return exc
    try:
        if not needed():
            return None
        got = _in_context(runtime, env, run)
        return got, _save_or_forget(got, now, env)
    finally:
        held.release()


def _in_context(runtime: str, env: Mapping[str, str], run: Callable[[], Reading]) -> Reading:
    """確かめる直前の認証の文脈を結果に添えて `run` で確かめる。確かめる間に文脈が変われば確かめられないとする。

    確かめに使った認証と記録にひも付く文脈を同じ時点で取り、途中でアカウントが切り替わった結果を新しいアカウントの
    Off として残さない（#3907）。
    """
    before = context(runtime, env)
    got = run()
    if got.training is not None and context(runtime, env) != before:
        got = got._replace(
            training=None, reason=f"確かめる間に認証の文脈（アカウント・設定の保存先・認証の環境変数）が変わった（{got.reason}）"
        )
    return got._replace(context=before)


def _save_or_forget(got: Reading, now: float, env: Mapping[str, str]) -> Optional[str]:
    """`got` をバックアップへ書く。書けたら None、書けなければ理由（例外の型名。パスを出さない）。

    書けず `got` が学習に使わない設定でないときは、この確かめで否定された古い記録を次の起動が使わないよう取り消す（`forget`）。
    """
    try:
        save([got], now, env)
        return None
    except (identity.IdentityError, OSError) as exc:
        why = type(exc).__name__
    if got.training is False:
        return why
    return why if forget(got.runtime, now) else f"{why}。古い記録も取り消せなかった"


def _rewrite_note(got: Reading) -> str:
    """書き換えたキーを、読み直して Off を確かめたキーとそうでないキーに分けた文（書き換えなければ空）。"""
    if not got.rewrites:
        return ""
    off = [r.key for r in got.rewrites if r.after is False]
    rest = [f"{r.key}（{'読み直して true' if r.after else '読み直して値が読めない'}）" for r in got.rewrites if r.after is not False]
    parts = [f"Off へ書き換えて読み直して false: {'・'.join(off)}"] if off else []
    if rest:
        parts.append(f"書き換えを送ったが Off を確かめられない: {'・'.join(rest)}")
    return "。" + "。".join(parts)


# ---------------------------------------------------------------- サブコマンド


def _check_one(runtime: str, env: Mapping[str, str], client: httpx.Client, now: float) -> tuple[Reading, bool]:
    """1 つの担当を同じ担当の排他の中で確かめて記録する。(結果, 記録できたか)。例外を出さない（#3907 の決定 11）。"""
    if runtime not in CHECKED_RUNTIMES:
        return settle(runtime, env, client, now), True
    done = _locked_check(runtime, env, now, lambda: settle(runtime, env, client, now))
    if isinstance(done, filelock.Timeout):
        return _unread(runtime, BUSY), True
    if isinstance(done, Exception) or done is None:
        return _unread(runtime, f"記録できなかった（{type(done).__name__}）"), True
    got, unsaved = done
    if unsaved is not None:
        return got._replace(reason=f"{got.reason}。記録できなかった（{unsaved}）"), False
    return got, True


def _summary(readings: list[Reading], unrecorded: list[str]) -> str:
    """断る理由のある担当をすべて並べ、Off へ書き換えた担当を添えた文（`。` で区切る）。"""
    parts = []
    unread = [f"{r.runtime}（{r.reason}）" for r in readings if r.training is None]
    if unread:
        parts.append(f"確かめられない: {', '.join(unread)}")
    for r in readings:
        if r.training is True:
            parts.append(f"学習に使う設定: {r.runtime}（{r.reason}）")
            if r.runtime in CHECKED_RUNTIMES:
                parts.append(off_by_hand(r.runtime))
    rewritten = [r.runtime for r in readings if r.rewrites and r.training is False]
    if rewritten:
        parts.append(f"Off へ書き換えた: {', '.join(rewritten)}")
    for runtime in unrecorded:
        parts.append(f"記録できなかった: {runtime}。`{CHECK_COMMAND} --runtime {runtime}` で確かめ直す")
    if not unrecorded and all(r.training is False for r in readings):
        parts.insert(0, f"学習に使わない設定: {', '.join(r.runtime for r in readings)}")
    return "。".join(parts)


def check(runtimes: list[str], env: Mapping[str, str], client: httpx.Client, now: float) -> dict[str, object]:
    """担当ごとに確かめ（学習に使う設定なら書き換え）てバックアップ、結果 JSON を組む。すべて学習に使わない設定で記録できたときだけ ok。

    確かめられない担当か記録できなかった担当があれば `precondition`、そうでなく学習に使う設定の担当があれば `refused` で、
    `summary` には断る理由のある担当をすべて並べる。
    """
    checked_ = [_check_one(rt, env, client, now) for rt in dict.fromkeys(runtimes)]
    readings = [r for r, _ in checked_]
    unrecorded = [r.runtime for r, kept in checked_ if not kept]
    items = [r.item(_stamp(now)) for r in readings]
    summary = _summary(readings, unrecorded)
    if unrecorded or any(r.training is None for r in readings):
        return result.stopped(TOOL, "precondition", summary, items=items)
    if any(r.training is True for r in readings):
        return result.stopped(TOOL, "refused", summary, items=items)
    return result.result(TOOL, result.OK, summary, items=items)


def main(argv: list[str]) -> int:
    """`ndf training check [--runtime claude|codex ...]`: 学習の設定を読み、学習に使う設定なら Off へ書き換えて記録する。"""
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
