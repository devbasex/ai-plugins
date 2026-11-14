"""通知のアダプター。HTTP は httpx だけが行い、urllib を使わない。

- `post_json` は JSON を 1 回 POST し、応答の JSON のオブジェクトを返す。失敗は `PostError` で、打ち直して通りうるか
  （`retryable`）を区別する
- `fetch_body` は URL の本文を、1 件あたりの総経過時間の上限の内で取得する。失敗は `FetchError`
- `slack_call` は Slack の Web API を 1 回呼ぶ。設定は `slack_settings` が環境変数と `.env`（python-dotenv）から読む
- `drain` は送信待ち（`outbox_dir`）の通知を送る送り手で、排他で 1 つだけ動き、通りうる失敗を打ち直す。
  副命令 `notify send` がこれを呼ぶ

上限の秒数は呼ぶ側が上限の宣言（`contracts/limits.json`）から引いて渡す。テストは `transport` に
`httpx.MockTransport` を渡し、ネットワークへ出ない。

送信待ちの 1 件は、通知 1 件を 1 ファイル（`<時刻>-<任意>.json`）として一時の名前で書いてから名前を変えたもので、
形は `contracts/schemas/notice.schema.json` が決める。書く側（hook）は書いた後に送り手を切り離して起動してよく、
送り手が既に動いていれば、後から起動した送り手は何もせずに終わる。
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import tenacity
from dotenv import dotenv_values
from filelock import FileLock, Timeout

from ndf import cli, contracts, identity
from ndf.contracts import limits, result

TOOL = cli.tool_of(__name__)

# 単調時計。テストが差し替える
_now = time.monotonic
# 壁時計（送信待ちの 1 件の書いた時刻と比べる）。テストが差し替える
_wall = time.time


class FetchError(Exception):
    """本文を取得できなかった。`str(e)` が理由の日本語 1 行。"""


class PostError(Exception):
    """JSON を送れなかった・応答が使えなかった。`str(e)` が理由の日本語 1 行。

    `retryable` が真なら、同じ要求を打ち直して通りうる（接続の失敗・上限の時間・429・5xx・Slack の `ratelimited`）。
    偽なら打ち直しても同じ失敗になる（URL の誤り・トークンの誤り・応答の形の誤り）。
    """

    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.retryable = retryable


def _status_retryable(status: int) -> bool:
    return status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """JSON を 1 回 POST し、応答が 2xx で JSON のオブジェクトならその辞書を返す。ほかは `PostError`。"""
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as e:
        raise PostError("URL が不正", retryable=False) from e
    except httpx.TimeoutException as e:
        raise PostError(f"{_seconds(timeout)} 秒を越えた", retryable=True) from e
    except httpx.TransportError as e:
        raise PostError(f"接続できない（{type(e).__name__}）", retryable=True) from e
    if not resp.is_success:
        raise PostError(f"HTTP {resp.status_code}", retryable=_status_retryable(resp.status_code))
    try:
        data = resp.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise PostError("応答が JSON でない", retryable=False) from e
    if not isinstance(data, dict):
        raise PostError("応答が JSON のオブジェクトでない", retryable=False)
    return data


def _seconds(timeout: float) -> str:
    return f"{timeout:g}"


# 要求ごとに新しく接続を張り、期限の監視が今の接続を必ず受け取れるようにする
_NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)


def _expired(timeout: float) -> FetchError:
    return FetchError(f"{_seconds(timeout)} 秒を越えた")


# 監視の対象にする接続を返す httpcore の trace の事象（前置きは "connection." や "proxy." など）
_WATCHED_EVENTS = (".connect_tcp.complete", ".start_tls.complete")


class _Watchdog:
    """期限に、待っている受信（応答の頭と本文）を止める。

    httpcore は受信の待ちの秒数を要求ごと・本文の読み始めに引き、以後の受信はその秒数で待つため、少しずつ
    届き続ける応答では総経過時間の上限が効かない。そこで取得の全体に 1 つの期限を掛け、期限に別のスレッドから
    今の接続を止め（`shutdown`）、待っている受信を失敗か終わりで戻す。接続は張るたびと TLS 化のたびに
    httpcore の `trace` で受け取る。止めた後の受信の終わりは本文の正常な終わりと読まれることがある（長さの無い応答）ため、
    読み終えた側は `fired` を確かめる。
    """

    def __init__(self, seconds: float) -> None:
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._done = False
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def trace(self, event: str, info: dict[str, Any]) -> None:
        """httpcore の `trace` の拡張。TCP の接続を張り終えたら、その接続を監視の対象にする。

        HTTPS では続く TLS 化が元の接続を手放し（記述子が -1 になる）、TLS の接続を新しく返すため、
        TLS 化を終えたらその接続へ監視の対象を移す。
        """
        if not event.endswith(_WATCHED_EVENTS):
            return
        stream = info.get("return_value")
        sock = stream.get_extra_info("socket") if stream is not None else None
        with self._lock:
            self._sock = sock if isinstance(sock, socket.socket) else None
            if self.fired:
                _shutdown(self._sock)

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.fired = True
            _shutdown(self._sock)

    def stop(self) -> None:
        with self._lock:
            self._done = True
        self._timer.cancel()


def _shutdown(sock: socket.socket | None) -> None:
    """接続の受信と送信を止める。接続の無いとき（テストの transport）と閉じた接続では何もしない。

    TLS の接続でも TLS の状態を別のスレッドから変えないよう、記述子を複製して `shutdown` だけを掛ける。
    """
    if sock is None:
        return
    try:
        with socket.fromfd(sock.fileno(), sock.family, sock.type) as dup:
            dup.shutdown(socket.SHUT_RDWR)
    except OSError:  # noqa: NDF401 ネットワーク: 既に閉じた接続は止める必要が無い
        return


def _limit_hook(deadline: float, timeout: float, watch: _Watchdog):
    """要求（転送の先を含む）ごとに、期限の残りを httpx のタイムアウトへ掛け直し、接続を監視へ渡す。"""

    def hook(request: httpx.Request) -> None:
        left = deadline - _now()
        if left <= 0:
            raise _expired(timeout)
        request.extensions["timeout"] = httpx.Timeout(left).as_dict()
        request.extensions["trace"] = watch.trace

    return hook


def _read(resp: httpx.Response, deadline: float, timeout: float, watch: _Watchdog) -> bytes:
    """最終の応答の本文を、期限の内で読み切る。2xx でなければ読まずに `FetchError`。

    受信のたびに本文を受け取り（量で溜めない）、受け取るたびに期限を確かめる。受信を待っている間に
    期限が来たら `_Watchdog` が待ちを止める。止めた受信が本文の終わりと読まれても、途中までの本文を返さない。
    """
    if not resp.is_success:
        raise FetchError(f"HTTP {resp.status_code}")
    parts: list[bytes] = []
    for chunk in resp.iter_bytes():
        parts.append(chunk)
        if _now() > deadline:
            raise _expired(timeout)
    if watch.fired:
        raise _expired(timeout)
    return b"".join(parts)


def _follow(client: httpx.Client, request: httpx.Request, deadline: float, timeout: float, watch: _Watchdog) -> bytes:
    """転送を追い、最終の応答の本文を返す。転送の応答は本文を読まずに閉じる。"""
    for _ in range(client.max_redirects + 1):
        with contextlib.closing(client.send(request, stream=True)) as resp:
            if resp.next_request is None:
                return _read(resp, deadline, timeout, watch)
            request = resp.next_request
    raise FetchError("転送が多すぎる")


def fetch_body(url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> bytes:
    """URL の本文を取得する。転送は追い、上限の秒数は転送の前後で通しで数える。

    上限は接続・応答の頭の受信・本文の受信のすべてに効く。最終の応答が 2xx なら本文の全部を返す。
    上限を越えた・2xx でない・転送が多すぎる・URL が不正・接続できないときは
    `FetchError`（理由の日本語 1 行）を送出する。
    """
    deadline = _now() + timeout
    watch = _Watchdog(timeout)
    hooks = {"request": [_limit_hook(deadline, timeout, watch)]}
    try:
        with httpx.Client(transport=transport, timeout=timeout, event_hooks=hooks, limits=_NO_KEEPALIVE) as client:
            return _follow(client, client.build_request("GET", url), deadline, timeout, watch)
    except httpx.TimeoutException as e:
        raise _expired(timeout) from e
    except httpx.InvalidURL as e:
        raise FetchError("URL が不正") from e
    except httpx.RequestError as e:
        if watch.fired:
            raise _expired(timeout) from e
        raise FetchError(f"接続できない（{type(e).__name__}）") from e
    finally:
        watch.stop()


# ---- Slack ----

# Slack の Web API の宛先の既定（`NDF_SLACK_API_BASE` で替える。テストの相手を指すため）
SLACK_API_BASE = "https://slack.com"
# 打ち直して通りうる Slack の `error`
_SLACK_RETRYABLE = frozenset({"ratelimited"})


@dataclass(frozen=True)
class SlackSettings:
    """Slack へ送るための設定。`mention` が空でなければ本文の先頭に付ける。"""

    token: str
    channel: str
    mention: str = ""
    api_base: str = SLACK_API_BASE


def find_env_file(start: Path) -> Path | None:
    """`start` から上へ `.env` を探す。`.git` のあるディレクトリ（git のトップ）か根で止まる。"""
    cur = start
    while True:
        if (cur / ".env").is_file():
            return cur / ".env"
        if (cur / ".git").exists() or cur.parent == cur:
            return None
        cur = cur.parent


def env_file_values(cwd: Path, fallback: Path | None) -> dict[str, str]:
    """`.env` を `cwd` から上へ探し、無ければ `fallback` から探して、見つけた 1 本の値を返す。無ければ空。"""
    for start in (cwd, fallback):
        found = find_env_file(start) if start is not None else None
        if found is not None:
            return {k: v for k, v in dotenv_values(found).items() if k and v is not None}
    return {}


def slack_settings(cwd: Path, fallback: Path | None, environ: dict[str, str]) -> SlackSettings | None:
    """環境変数と `.env` から Slack の設定を読む。環境変数が `.env` に勝つ。トークンか宛先が無ければ `None`（送らない）。"""
    env = {**env_file_values(cwd, fallback), **environ}
    token, channel = env.get("SLACK_BOT_TOKEN"), env.get("SLACK_CHANNEL_ID")
    if not (token and channel):
        return None
    api_base = (env.get("NDF_SLACK_API_BASE") or SLACK_API_BASE).rstrip("/")
    mention = env.get("SLACK_USER_MENTION", "")  # noqa: NDF502 利用者の入力: 未設定ならメンションを付けない
    return SlackSettings(token=token, channel=channel, mention=mention, api_base=api_base)


def slack_call(
    method: str,
    payload: dict[str, Any],
    settings: SlackSettings,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Slack の Web API（`chat.postMessage` など）を 1 回呼び、応答の辞書を返す。`ok` が偽なら `PostError`。"""
    headers = {"Authorization": f"Bearer {settings.token}"}
    data = post_json(f"{settings.api_base}/api/{method}", payload, headers, timeout, transport)
    if data.get("ok") is not True:
        error = str(data.get("error") or "ok が偽")
        raise PostError(f"Slack {method}: {error}", retryable=error in _SLACK_RETRYABLE)
    return data


# ---- 送信待ちの 1 件 ----

# 知らせる事象（送信待ちの `kind`）と、本文の先頭に付ける見出し
KIND_LABELS = {"question": "回答待ち", "approval": "承認待ち", "done": "完了"}
NOTICE_SCHEMA = contracts.schema_path("notice")


class NoticeError(Exception):
    """送信待ちの 1 件を読めない・形が違う。"""


@dataclass(frozen=True)
class Notice:
    """送信待ちの 1 件。`key` が同じ通知は重ねて送らない（`window` が真なら上限の宣言の `notify.window` の秒の内だけ）。"""

    kind: str
    key: str
    text: str
    cwd: Path
    created_at: float
    window: bool = False

    def message(self, mention: str) -> str:
        head = f"{mention} " if mention else ""
        return f"{head}{KIND_LABELS[self.kind]}: {self.text}"


def read_notice(path: Path) -> Notice:
    """送信待ちの 1 ファイルを読む。読めない・形が違えば `NoticeError`。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NoticeError(f"読めない（{type(e).__name__}）") from e
    errs = contracts.schema_errors(raw, NOTICE_SCHEMA)
    if errs:
        raise NoticeError(f"形が違う: {'; '.join(errs)}")
    return Notice(
        kind=raw["kind"],
        key=raw["key"],
        text=raw["text"],
        cwd=Path(raw["cwd"]),
        created_at=float(raw["created_at"]),
        window=raw["window"],
    )


# ---- 送り手 ----

# 送信待ちの置き場（状態の置き場からの相対）
OUTBOX_PARTS = ("hooks", "outbox")
# 送り手が 1 つだけ動くための排他のファイル・送ったキーの記録・送れなかった 1 件の置き場
_LOCK = ".sender.lock"
_SENT = ".sent.json"
_UNSENT = "unsent"


def outbox_dir(state_dir: Path) -> Path:
    """状態の置き場の下の送信待ちの置き場。"""
    return state_dir.joinpath(*OUTBOX_PARTS)


@dataclass
class Drained:
    """1 回の送り手の結果。`busy` は、ほかの送り手が動いていて何もしなかったこと。"""

    sent: int = 0
    duplicate: int = 0
    stale: int = 0
    unconfigured: int = 0
    left: int = 0
    busy: bool = False
    failed: list[str] = field(default_factory=list)


# 1 件を送る関数。送ったら真、設定が無く送らなかったら偽。送れなければ `PostError`
Deliver = Callable[[Notice], bool]


def pending(outbox: Path) -> list[Path]:
    """送信待ちの 1 件のファイルを、書いた順（名前の順）に返す。書きかけ（`.` で始まる一時の名前）を含めない。"""
    return sorted(p for p in outbox.glob("*.json") if not p.name.startswith("."))


def _keep_since(now: float) -> float:
    """送ったキーと送れなかった 1 件を残す期限の境。これより前のものは捨てる。"""
    return now - limits.seconds("notify.keep")


def _load_sent(outbox: Path, now: float) -> dict[str, float]:
    try:
        raw = json.loads((outbox / _SENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # 無い・壊れた記録は空から数え直す（重ねる恐れは最後の 1 件だけ）
        return {}
    if not isinstance(raw, dict):
        return {}
    keep = _keep_since(now)
    return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float)) and v >= keep}


def _save_sent(outbox: Path, sent: dict[str, float]) -> None:
    tmp = outbox / f"{_SENT}.tmp"
    tmp.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    tmp.replace(outbox / _SENT)


def _prune_unsent(outbox: Path, now: float) -> None:
    keep = _keep_since(now)
    for path in (outbox / _UNSENT).glob("*.json"):
        if path.stat().st_mtime < keep:
            path.unlink(missing_ok=True)


def _duplicate(notice: Notice, sent: dict[str, float], now: float) -> bool:
    at = sent.get(notice.key)
    if at is None:
        return False
    return not notice.window or now - at < limits.seconds("notify.window")


def _retrying(sleep: Callable[[float], None]) -> tenacity.Retrying:
    """通りうる失敗（`PostError.retryable`）だけを、上限の宣言の `notify.send` の回数と間隔で打ち直す。"""
    name = "notify.send"
    return tenacity.Retrying(
        retry=tenacity.retry_if_exception(lambda e: isinstance(e, PostError) and e.retryable),
        stop=tenacity.stop_after_attempt(int(limits.value(name, "attempts"))),
        wait=tenacity.wait_exponential(
            multiplier=limits.value(name, "interval"),
            exp_base=limits.value(name, "factor"),
            max=limits.value(name, "max_interval"),
        ),
        sleep=sleep,
        reraise=True,
    )


@dataclass
class _Run:
    """1 回の送り手の文脈。`sent` は送ったキーと送った時刻の記録で、送るたびに書き足す。"""

    outbox: Path
    deliver: Deliver
    sleep: Callable[[float], None]
    sent: dict[str, float]
    out: Drained


def _deliver(notice: Notice, run: _Run) -> PostError | None:
    """1 件を打ち直し付きで送り、送った・設定が無いを数える。打ち直しても送れなければ、その失敗を返す。"""
    try:
        delivered = _retrying(run.sleep)(run.deliver, notice)
    except PostError as e:
        return e
    if not delivered:
        run.out.unconfigured += 1
        return None
    run.out.sent += 1
    run.sent[notice.key] = _wall()
    _save_sent(run.outbox, run.sent)
    return None


def _send_one(path: Path, run: _Run) -> bool:
    """1 件を扱う。打ち直しても送れず、送信待ちに残したら偽（この回の送り手を終える）。"""
    now = _wall()
    try:
        notice = read_notice(path)
    except NoticeError as e:
        _move_unsent(path, run, f"{path.name}: {e}")
        return True
    if now - notice.created_at > limits.seconds("notify.stale"):
        run.out.stale += 1
    elif _duplicate(notice, run.sent, now):
        run.out.duplicate += 1
    elif failure := _deliver(notice, run):
        if failure.retryable:
            run.out.left = len(pending(run.outbox))
            return False
        _move_unsent(path, run, f"{path.name}: {failure}")
        return True
    path.unlink(missing_ok=True)
    return True


def _move_unsent(path: Path, run: _Run, reason: str) -> None:
    (run.outbox / _UNSENT).mkdir(exist_ok=True)
    path.replace(run.outbox / _UNSENT / path.name)
    run.out.failed.append(reason)


def _drain_locked(outbox: Path, deliver: Deliver, sleep: Callable[[float], None], out: Drained) -> bool:
    """排他を持ったまま、送信待ちが空になるまで送る。打ち直しても送れない 1 件で止めたら偽。"""
    _prune_unsent(outbox, _wall())
    run = _Run(outbox=outbox, deliver=deliver, sleep=sleep, sent=_load_sent(outbox, _wall()), out=out)
    while paths := pending(outbox):
        for path in paths:
            if not _send_one(path, run):
                return False
    return True


def drain(outbox: Path, deliver: Deliver, sleep: Callable[[float], None] = time.sleep) -> Drained:
    """送信待ちを送る送り手。排他で 1 つだけ動き、ほかの送り手が動いていれば何もせずに `busy` で返す。

    排他を外した後にも送信待ちを見直し、外す間際に書かれた 1 件（書いた側の起動した送り手は、排他を取れずに
    終わっている）を取りこぼさない。打ち直しても送れない 1 件があれば、それと後の 1 件を送信待ちに残して終える
    （次に起動した送り手が送り直す）。打ち直しても通らない失敗の 1 件は `unsent/` へ移す。
    """
    outbox.mkdir(parents=True, exist_ok=True)
    out = Drained()
    lock = FileLock(outbox / _LOCK)
    first = True
    while first or pending(outbox):
        try:
            with lock.acquire(timeout=0):
                finished = _drain_locked(outbox, deliver, sleep, out)
        except Timeout:
            out.busy = first
            return out
        first = False
        if not finished:
            return out
    return out


def slack_deliver(
    fallback: Path | None,
    transport: httpx.BaseTransport | None = None,
    environ: Mapping[str, str] | None = None,
) -> Deliver:
    """送信待ちの 1 件を Slack へ 1 回送る `Deliver`。設定は 1 件ごとに、その `cwd` の `.env` と環境変数から読む。

    環境変数は `environ`（`None` なら `os.environ`）から読む。
    """

    def deliver(notice: Notice) -> bool:
        settings = slack_settings(notice.cwd, fallback, dict(os.environ if environ is None else environ))
        if settings is None:
            return False
        payload = {"channel": settings.channel, "text": notice.message(settings.mention)}
        slack_call("chat.postMessage", payload, settings, limits.seconds("notify.slack"), transport)
        return True

    return deliver


def _report(out: Drained) -> dict[str, Any]:
    metrics = {
        "sent": out.sent,
        "duplicate": out.duplicate,
        "stale": out.stale,
        "unconfigured": out.unconfigured,
        "failed": len(out.failed),
        "left": out.left,
    }
    warnings = out.failed[:10]
    if out.busy:
        return result.result(TOOL, result.OK, "ほかの送り手が送っているので何もしない", metrics=metrics)
    if out.left:
        detail = f"打ち直しても送れず、{out.left} 件を送信待ちに残した"
        return result.stopped(TOOL, "transient", detail, metrics=metrics, warnings=warnings)
    return result.result(TOOL, result.OK, f"{out.sent} 件を送った", metrics=metrics, warnings=warnings)


def main(argv: list[str]) -> int:
    """`ndf notify send`: 状態の置き場の送信待ちを Slack へ送る（hook が切り離して起動する送り手）。"""
    if argv != ["send"]:
        return result.emit(result.stopped(TOOL, "invalid", f"使い方: notify send（受けた引数: {' '.join(argv)}）"))
    ident = identity.load()
    out = drain(outbox_dir(ident.state_dir), slack_deliver(ident.root))
    return result.emit(_report(out))
