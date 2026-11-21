"""従量の接続（Bedrock・API キー）の設定・確かめ・月の額・使い方（旧い `relay.py account` の add-bedrock・add-api-key・
api-key-monthly・api-key-use・check を移した。#3569）。

Bedrock は `aws` を呼んで確かめる。aws へ渡すのはプロファイル名・地域・モデルの ID だけで、キーは aws 自身が `~/.aws/` から
読む。aws の出力のうち使うのはエラーの種類の名前と終了コードだけで、応答とエラーの本文は結果へ出さない。
API キーは Messages API へ `max_tokens` 1 の要求を 1 回打って確かめる。キーの値は登録簿の外へ出さない（結果・ログにも）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional, Sequence

import httpx

from ndf.contracts import limits
from ndf.domains.relay import account_registry as reg

PROVIDER = "bedrock"
ERROR_RE = re.compile(r"An error occurred \(([A-Za-z0-9_.]+)\)")
REASONS = {
    "auth_expired": "認証切れ",
    "no_permission": "権限が無い",
    "region_unavailable": "地域で使えない",
    "model_unavailable": "モデルが有効でない",
    "unclassified": "区分できない失敗",
}
CHECK_URL = "https://api.anthropic.com/v1/messages"
CHECK_MODEL = "claude-haiku-4-5"  # 確かめに使う安いモデル。NDF_API_KEY_CHECK_MODEL で差し替えられる


class MeteredError(Exception):
    """従量の接続を扱えない。`kind` は結果の原因の種類（`refused`・`invalid`・`precondition`・`input_missing`）。"""

    def __init__(self, kind: str, message: str, items: Sequence[dict[str, Any]] = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.items = list(items)


class KeyRequest(NamedTuple):
    """保存する API キー（値はこの外へ出さない）。"""

    name: str
    account: str
    key: str
    monthly: Optional[float]


class Target(NamedTuple):
    """Bedrock の呼ぶ先。"""

    profile: str
    region: str
    model: str

    def details(self) -> dict[str, str]:
        return {"profile": self.profile, "region": self.region, "model": self.model}

    def env(self) -> dict[str, str]:
        return {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": self.profile, "AWS_REGION": self.region, "ANTHROPIC_MODEL": self.model}

    def label(self) -> str:
        return "Bedrock（" + "・".join(self.details().values()) + "）"


def from_env(env: Mapping[str, str]) -> Optional[Target]:
    region, model = env.get("AWS_REGION", ""), env.get("ANTHROPIC_MODEL", "")  # noqa: NDF502 利用者の入力: 無い変数は Bedrock の設定でないとみなす
    if not reg.provider_on(env.get("CLAUDE_CODE_USE_BEDROCK")) or not region or not model:
        return None
    return Target(env.get("AWS_PROFILE", "default"), region, model)  # noqa: NDF502 利用者の入力: プロファイルの無い設定は aws の既定のプロファイル


def _aws(
    environ: Mapping[str, str], args: list[str], profile: Optional[str] = None, region: Optional[str] = None
) -> Optional[subprocess.CompletedProcess]:
    aws = shutil.which("aws")
    if aws is None:
        return None
    env = {k: v for k, v in environ.items() if k not in reg.AWS_KEY_ENV}
    if profile:
        env["AWS_PROFILE"] = profile
        if region:
            env["AWS_REGION"] = region
    try:
        return subprocess.run(
            [aws, *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=limits.seconds("relay.bedrock")
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "Timeout")
    except OSError:  # noqa: NDF401 外部コマンド: aws を起動できなければ None（呼び手が区分できない失敗として止める）
        return None


def profiles(environ: Mapping[str, str]) -> list[str]:
    p = _aws(environ, ["configure", "list-profiles"])
    return [x.strip() for x in p.stdout.splitlines() if x.strip()] if p is not None and p.returncode == 0 else []


def region_of(environ: Mapping[str, str], profile: str) -> str:
    p = _aws(environ, ["configure", "get", "region", "--profile", profile])
    return p.stdout.strip() if p is not None and p.returncode == 0 else ""


def models(environ: Mapping[str, str], profile: str, reg_name: str) -> list[str]:
    """推論プロファイル（SYSTEM_DEFINED）のうち ID に `anthropic.claude` を含むもの。読めなければ空。"""
    args = [
        "bedrock",
        "list-inference-profiles",
        "--type-equals",
        "SYSTEM_DEFINED",
        "--output",
        "json",
        "--profile",
        profile,
        "--region",
        reg_name,
    ]
    p = _aws(environ, args, profile, reg_name)
    if p is None or p.returncode != 0:
        return []
    try:
        rows = json.loads(p.stdout).get("inferenceProfileSummaries") or []  # noqa: NDF501 外部コマンド: 一覧のキーが無ければ候補は空
    except (ValueError, AttributeError):
        return []
    ids = [r.get("inferenceProfileId") for r in rows if isinstance(r, dict)]
    return sorted(i for i in ids if isinstance(i, str) and "anthropic.claude" in i)


def error_name(err: str) -> str:
    m = ERROR_RE.search(err)
    if m:
        return m.group(1)
    if "Could not connect to the endpoint URL" in err:
        return "EndpointConnectionError"
    if "Timeout" in err or "timed out" in err:
        return "Timeout"
    return ""


def classify_failure(err: str) -> tuple[str, str]:
    """`bedrock-runtime converse` の失敗の (区分, AWS のエラーの種類の名前)。"""
    name, low = error_name(err), err.lower()
    if name == "EndpointConnectionError" or "not supported in this region" in low or "not available in this region" in low:
        return "region_unavailable", name
    if name in ("ExpiredTokenException", "UnrecognizedClientException", "InvalidSignatureException"):
        return "auth_expired", name
    if name == "AccessDeniedException":
        if ("access to the model" in low or "model access" in low) and not ("not authorized to perform" in low or "explicit deny" in low):
            return "model_unavailable", name
        return "no_permission", name
    if name in ("ValidationException", "ResourceNotFoundException") and ("model" in low or "identifier" in low):
        return "model_unavailable", name
    return "unclassified", name


def verify(environ: Mapping[str, str], t: Target) -> Optional[tuple[str, str]]:
    """認証（`sts get-caller-identity`）と 1 回の短い応答（最大 1 トークン）を確かめる。通れば None、落ちれば (区分, 種類)。"""
    p = _aws(environ, ["sts", "get-caller-identity", "--output", "json", "--profile", t.profile, "--region", t.region], t.profile, t.region)
    if p is None:
        return "unclassified", ""
    if p.returncode != 0:
        return "auth_expired", error_name(p.stderr)
    messages = json.dumps([{"role": "user", "content": [{"text": "ping"}]}])
    args = ["bedrock-runtime", "converse", "--model-id", t.model, "--messages", messages]
    args += ["--inference-config", json.dumps({"maxTokens": 1}), "--output", "json", "--profile", t.profile, "--region", t.region]
    p = _aws(environ, args, t.profile, t.region)
    if p is None:
        return "unclassified", ""
    return None if p.returncode == 0 else classify_failure(p.stderr)


def saved_view(environ: Mapping[str, str]) -> dict[str, str]:
    """保存した従量の接続の設定の子が見る親の環境（照らす変数 `METERED_CHECKED` を外した写し）。

    保存した設定の子は親の接続先・資格情報の選択（`AWS_CONFIG_FILE`・`AWS_SHARED_CREDENTIALS_FILE`・`AWS_PROFILE` ほか）を
    受け継がない（`session_env.metered_env` の `saved`）。登録の候補・確かめもこの環境で aws を呼び、確かめた接続と
    起動した子の接続を同じ資格情報の選択にする。
    """
    return {k: v for k, v in environ.items() if k not in reg.METERED_CHECKED}


def _verify_or_fail(environ: Mapping[str, str], t: Target) -> None:
    fail = verify(environ, t)
    if fail is not None:
        reason, aws_error = fail
        item = {"name": reg.METERED, "kind": "metered", "reason": reason, "state": aws_error or None}  # noqa: NDF501 外部コマンド: 種類の名前を読めなければ null
        raise MeteredError("refused", f"{t.label()} を呼べない（{REASONS[reason]}・{aws_error or '不明'}）。保存しない", [item])


def add_bedrock(store: Path, environ: Mapping[str, str], asked: Target, yes: bool) -> dict[str, Any]:
    """Bedrock の接続を確かめ、通ったときだけ従量の接続の設定を保存する。足りない引数（空の欄）は候補を添えて止める。

    候補の一覧と確かめは、保存した設定の子と同じ環境（`saved_view`）で aws を呼ぶ。
    """
    environ = saved_view(environ)
    profile, region, model = asked
    if shutil.which("aws") is None:
        raise MeteredError("precondition", "aws CLI が見つからない（Bedrock の登録には aws が要る）")
    if not profile:
        cands = profiles(environ)
        if not cands:
            raise MeteredError(
                "precondition", "AWS のプロファイルが無い（aws configure list-profiles が 0 件）。先に aws configure sso などで作る"
            )
        raise MeteredError("input_missing", f"--profile を渡す。候補: {', '.join(cands)}")
    region = region or region_of(environ, profile)
    if not region:
        raise MeteredError("input_missing", f"--region を渡す（{profile} に region が無い）")
    if not model:
        raise MeteredError("input_missing", "--model を渡す。候補: " + (", ".join(models(environ, profile, region)) or "（読めない）"))
    t = Target(profile, region, model)
    _verify_or_fail(environ, t)
    old = reg.load_metered(store)[0]
    if not yes:
        what = f"前の設定（{old.details.get('profile') or old.provider}）を置き換える" if old else "保存する"
        raise MeteredError("input_missing", f"{t.label()} は呼べる。{what}なら --yes を付けて打ち直す")
    reg.save_metered(store, PROVIDER, t.env(), t.details(), time.time())
    return {"name": reg.METERED, "kind": "metered", "provider": PROVIDER, "details": t.details(), "replaced": old is not None}


def check_api_key(key: str, environ: Mapping[str, str], transport: Optional[httpx.BaseTransport] = None) -> tuple[str, int, Optional[str]]:
    """API キーで 1 回要求を打ち、(結果, HTTP の状態, エラーの型) を返す。結果は ok・auth_failed・credit_low・network・unclassified。"""
    url = environ.get("NDF_API_KEY_CHECK_URL") or CHECK_URL
    body = {
        "model": environ.get("NDF_API_KEY_CHECK_MODEL") or CHECK_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    try:
        with httpx.Client(transport=transport, timeout=limits.seconds("claude-api-keys.check"), follow_redirects=False) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError:
        return "network", 0, None
    if resp.status_code == 200:
        return "ok", 200, None
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        err = None
    err = err if isinstance(err, dict) else {}
    etype = err.get("type") if isinstance(err.get("type"), str) else None
    if resp.status_code == 401 or etype == "authentication_error":
        return "auth_failed", resp.status_code, etype
    msg = str(err.get("message") or "").lower()  # noqa: NDF501 ネットワーク: 応答に message が無ければ残高切れでない
    if "credit balance" in msg and "too low" in msg:
        return "credit_low", resp.status_code, etype
    return "unclassified", resp.status_code, etype


CHECK_TEXT = {
    "auth_failed": "認証が通らない（キーが違うか失効している）",
    "credit_low": "残高切れ（credit balance is too low）",
    "network": "通信に失敗した",
}


def _verify_key(key: str, environ: Mapping[str, str], name: str, transport: Optional[httpx.BaseTransport]) -> None:
    got, status, etype = check_api_key(key, environ, transport)
    if got != "ok":
        text = CHECK_TEXT.get(got, f"確かめに失敗した（HTTP {status}・{etype or '-'}）")  # noqa: NDF502 ネットワーク: 区分の無い失敗は状態と型だけを出す
        raise MeteredError("refused", f"{name}: {text}", [{"name": name, "kind": "api_key", "reason": got, "state": etype}])


def amount(v: Optional[str], allow_zero: bool = False) -> Optional[float]:
    """月の額の引数（正の数。`allow_zero` なら 0 は設定を外す None）。読めなければ `MeteredError`（値は表示しない）。"""
    if v is None:
        return None
    x = reg.parse_number(v)
    if allow_zero and x == 0:
        return None
    if x is None or not (x > 0 and x != float("inf")):
        raise MeteredError("invalid", "月の額は正の数（ドル）で渡す（入力値は表示しない）")
    return x


def add_api_key(
    store: Path, environ: Mapping[str, str], req: KeyRequest, transport: Optional[httpx.BaseTransport] = None
) -> dict[str, Any]:
    """API キーを確かめ、通ったときだけ保存する（同じ名前なら置き換える）。結果にキーの値を含めない。"""
    name, account, key, monthly = req
    accounts = reg.names(store)
    if name == reg.METERED or name in accounts:
        raise MeteredError("invalid", f"名前が登録済みアカウントか予約の名前と重なる: {name}")
    if not reg.valid_name(name):
        raise MeteredError("invalid", "名前は英小文字・数字・- と _ の 32 字まで（入力値は表示しない）")
    if account not in accounts:
        raise MeteredError("refused", f"登録済みアカウントでない: {account}")
    if not key:
        raise MeteredError("input_missing", "キーの値を標準入力か --key-file で渡す")
    with reg.locked(store, reg.BOOK_LOCK):
        _refuse_taken(reg.read_book(store), account, name)  # 有料の確かめより前に、登録簿だけで断れる要求を断る
    _verify_key(key, environ, name, transport)
    at = time.time()
    with reg.locked(store, reg.BOOK_LOCK):
        book = reg.read_book(store)
        _refuse_taken(book, account, name)  # 確かめている間に別のキーが結び付いたら書かない
        replaced = name in book["keys"]
        book.setdefault("version", 1)
        book.setdefault("use", "off")
        row: dict[str, Any] = {"key": key, "account": account, "added_at": reg.iso_utc(at), "generation": str(uuid.uuid4())}
        if monthly is not None:
            row["monthly_usd"] = monthly
        book["keys"][name] = row
        reg.write(reg.book_path(store), book)
    return {"name": name, "kind": "api_key", "account": account, "monthly_usd": monthly, "replaced": replaced}


def _refuse_taken(book: Mapping[str, Any], account: str, name: str) -> None:
    """アカウントに別の名前のキーが結び付いていれば `MeteredError`（1 アカウントに 1 本）。"""
    taken = [n for n, r in book["keys"].items() if isinstance(r, dict) and r.get("account") == account and n != name]
    if taken:
        raise MeteredError("refused", f"アカウント {account} には API キー {taken[0]} が結び付いている（1 アカウントに 1 本）")


def read_key(key_file: Optional[str], stdin_line: str) -> str:
    """キーの値（`--key-file` の 1 行目か、標準入力の 1 行目）。読めなければ `MeteredError`。"""
    if not key_file:
        return stdin_line.strip()
    try:
        with open(key_file, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError as exc:
        raise MeteredError("input_missing", f"--key-file を読めない（{exc.strerror}）") from exc


def set_monthly(store: Path, name: str, usd: Optional[float]) -> dict[str, Any]:
    """登録済みのキーの月の額を書く（None で外す）。値と世代は変えない。"""
    with reg.locked(store, reg.BOOK_LOCK):
        book = reg.read_book(store)
        row = book["keys"].get(name)
        if not isinstance(row, dict):
            raise MeteredError("refused", f"登録されていない: {name}")
        if usd is None:
            row.pop("monthly_usd", None)
        else:
            row["monthly_usd"] = usd
        reg.write(reg.book_path(store), book)
    return {"name": name, "kind": "api_key", "monthly_usd": usd}


def weekly_at(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    x = reg.parse_number(v)
    if x is None or not 1 <= x <= 100:
        raise MeteredError("invalid", "--weekly-at は 1 以上 100 以下の百分率")
    return x


def _use_now(book: Mapping[str, Any]) -> tuple[str, float]:
    use = book.get("use") if book.get("use") in reg.USES else "off"
    at = reg.positive(book.get("weekly_at"))
    return str(use), at if at is not None and at <= 100 else reg.WEEKLY_AT_DEFAULT


def set_use(store: Path, mode: Optional[str], at: Optional[float]) -> dict[str, Any]:
    """API キーへ切り替える使い方と閾値を書く。どちらも無ければ今の値を返す。"""
    if mode is None and at is None:
        try:
            use, cur = _use_now(reg.read_book(store))
        except reg.AccountError:
            use, cur = "off", reg.WEEKLY_AT_DEFAULT
        return {"kind": "api_key", "use": use, "weekly_at": cur}
    if mode is not None and mode not in reg.USES:
        raise MeteredError("invalid", f"使い方は {' / '.join(reg.USES)} のどれか: {mode}")
    with reg.locked(store, reg.BOOK_LOCK):
        book = reg.read_book(store)
        book["use"] = mode if mode is not None else _use_now(book)[0]
        if at is not None:
            book["weekly_at"] = at
        reg.write(reg.book_path(store), book)
    use, cur = _use_now(book)
    return {"kind": "api_key", "use": use, "weekly_at": cur}


def check(store: Path, environ: Mapping[str, str], target: str, transport: Optional[httpx.BaseTransport] = None) -> dict[str, Any]:
    """従量の接続（`metered`）か登録済み API キーが呼べるかを確かめる。状態は記録しない。"""
    if target in reg.key_names(store):
        row = reg.read_book(store)["keys"][target]
        _verify_key(str(row.get("key") or ""), environ, target, transport)  # noqa: NDF501 ファイル: 値の無い登録は確かめに落ちる
        return {"name": target, "kind": "api_key", "state": "使える"}
    if target != reg.METERED:
        raise MeteredError("invalid", f"確かめられるのは metered か登録済み API キーだけ: {target}")
    env = reg.fallback_env(store, environ)
    if not env:
        raise MeteredError("refused", "従量の接続の設定が無い")
    t = from_env(env)
    if t is None:
        raise MeteredError("refused", "今の設定は Bedrock（CLAUDE_CODE_USE_BEDROCK・AWS_REGION・ANTHROPIC_MODEL）でないため確かめられない")
    if shutil.which("aws") is None:
        raise MeteredError("precondition", "aws CLI が見つからない")
    # 保存した設定なら、その子と同じ環境で確かめる（環境変数の設定の子は親の環境を受け継ぐ）
    _verify_or_fail(environ if reg.declared_env(environ) is not None else saved_view(environ), t)
    return {"name": reg.METERED, "kind": "metered", "provider": PROVIDER, "details": t.details(), "state": "使える"}


def remove(store: Path, target: str) -> dict[str, Any]:
    """従量の接続の設定か API キーの登録を外す。"""
    if target == reg.METERED:
        if not reg.remove_metered(store):
            raise MeteredError("refused", "保存した従量の接続の設定が無い")
        return {"name": target, "kind": "metered"}
    if not reg.remove_key(store, target):
        raise MeteredError("refused", f"登録されていない: {target}")
    return {"name": target, "kind": "api_key"}
