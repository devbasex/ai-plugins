"""学習に使わない設定のスナップショットと、バックアップを読んで起動を断る規則（#3391 の 3391-5）。

バックアップは状態の保存先の `training.json` に、`ndf training check`（`training.py`）が時刻つきで書く。LLM と外部 CLI の
起動の口（`launch_slot.gate`）は `refusal` でバックアップを読むだけで、学習に使う・バックアップが無い・期限切れ
（`contracts/limits.json` の `training.check-valid`）・読めないなら起動を断り、理由に確認のサブコマンドを載せる。

バックアップは確かめたときの認証の文脈（設定の保存先・アカウントの識別子・認証の環境変数）のハッシュ（`context`）を持ち、
起動先の環境から求めた文脈と違えば（アカウントや保存先を切り替えた後）期限内でも起動を断る。ハッシュの材料は
バックアップ・出力・記録へ写さない。

外部の API から設定を読む接続（httpx・トークン・キーチェーン）は `training.py` が持ち、ここは読まない。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Mapping
from typing import NamedTuple, Optional

from filelock import FileLock

from ndf import identity
from ndf.adapters.run_log import record
from ndf.contracts import facts, limits

CHECK_COMMAND = "ndf training check"
# 確かめる口の無い担当（kiro・agy など）の記録の値
UNSUPPORTED = "unsupported"
# バックアップを読んで起動を断ったときの記録の値
REFUSED = "refused"
CACHE_FILE = "training.json"
# バックアップの期限の方針（contracts/limits.json）
VALID_POLICY = "training.check-valid"
# 確かめる口のある担当（表 runtimes の training_check。読み手は `training.reader`。ほかの担当は確かめず起動を通す）
CHECKED_RUNTIMES = frozenset(facts.ids("runtimes", training_check=True))
CLAUDE_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
# OAuth より優先される認証の変数（値は出さず、名前だけを理由に載せる）
CLAUDE_FOREIGN_AUTH = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
# claude のログインのアカウントの記録（設定の保存先の外のホーム、または CLAUDE_CONFIG_DIR の下）
CLAUDE_STATE = ".claude.json"
# codex のログインの置き換えになる変数
CODEX_FOREIGN_AUTH = ("OPENAI_API_KEY", "CODEX_API_KEY")
# バックアップを書く排他の待ちの上限の方針（contracts/limits.json。#3907 の決定 11）
SAVE_LOCK_POLICY = "training.save-lock"


def _manual_off() -> dict[str, str]:
    """書き換えられなかったとき、利用者が手で Off にする場所（担当 → 画面の項目。#3907 の決定 5）。

    担当の集まりは表 runtimes の training_check が持つので、最上位の定数にせず、import の時に表と照らす（`training._readers` と同じ形）。
    """
    return {
        "claude": "claude.ai の設定の「プライバシー」の「Help improve Claude」",
        "codex": "ChatGPT の設定の「データコントロール」の「すべての人のためにモデルを改善する」と、Codex の設定のデータの学習（環境を含める設定）",
    }


if set(_manual_off()) != CHECKED_RUNTIMES:
    raise ImportError(f"手で Off にする場所の担当 {sorted(_manual_off())} が確かめる担当 {sorted(CHECKED_RUNTIMES)} と食い違う")


def manual_off(runtime: str) -> str:
    """`runtime` の学習の設定を手で Off にする場所。"""
    return _manual_off()[runtime]


class Rewrite(NamedTuple):
    """学習の設定の 1 つのキーを false へ書き換えた記録。`before` は書き込む前に読んだ値、`after` は読み直した値
    （読めなければ None）、`at` は書き込みを送った時刻。"""

    key: str
    before: bool
    after: Optional[bool]
    at: str

    def item(self) -> dict[str, object]:
        return {"key": self.key, "before": self.before, "after": self.after, "at": self.at}


class Reading(NamedTuple):
    """1 つの担当の確認の結果。`training` は真（学習に使う）・偽（使わない）・None（確かめられない）。

    `rewrites` は Off へ書き換えて受け付けられたキーの記録（#3907）。書き換えなければ空。
    `context` は確かめ始めたときの認証の文脈のハッシュ（`context`）。`save` はこれを記録に使い、None なら保存の時点で求める。
    """

    runtime: str
    training: Optional[bool]
    reason: str
    rewrites: tuple[Rewrite, ...] = ()
    context: Optional[str] = None

    def item(self, checked_at: str) -> dict[str, object]:
        item: dict[str, object] = {"runtime": self.runtime, "training": self.training, "reason": self.reason, "checked_at": checked_at}
        if self.rewrites:
            item["rewrites"] = [r.item() for r in self.rewrites]
        return item


def off_by_hand(runtime: str) -> str:
    """書き換えられないとき、手で Off にして確かめ直す案内の文。"""
    return f"手で Off にする場所: {manual_off(runtime)}。Off にした後に `{CHECK_COMMAND} --runtime {runtime}` で確かめ直す"


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 無いか壊れていれば無いとして扱う
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 認証の文脈


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path.home()


def _claude_context(env: Mapping[str, str]) -> dict[str, object]:
    conf = env.get("CLAUDE_CONFIG_DIR")
    state = _read_json(Path(conf) / CLAUDE_STATE if conf else _home(env) / CLAUDE_STATE) or {}  # noqa: NDF501 ファイル: 無いか壊れていればアカウントは無い
    account = state.get("oauthAccount")
    return {
        "config_dir": conf or str(_home(env) / ".claude"),
        "account": account.get("accountUuid") if isinstance(account, dict) else None,
        "env": {name: env.get(name) for name in (CLAUDE_TOKEN_ENV, *CLAUDE_FOREIGN_AUTH)},
    }


def _codex_context(env: Mapping[str, str]) -> dict[str, object]:
    home = env.get("CODEX_HOME") or str(_home(env) / ".codex")
    auth = _read_json(Path(home) / "auth.json") or {}  # noqa: NDF501 ファイル: 無いか壊れていればログインは無い
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    return {
        "codex_home": home,
        "auth_mode": auth.get("auth_mode"),
        "account": tokens.get("account_id"),
        "env": {name: env.get(name) for name in CODEX_FOREIGN_AUTH},
    }


def context(runtime: str, env: Mapping[str, str]) -> str:
    """`env` で起動する `runtime` の認証の文脈のハッシュ。アカウント・保存先・認証の変数が変われば変わる。"""
    material = _claude_context(env) if runtime == "claude" else _codex_context(env) if runtime == "codex" else {}
    text = json.dumps({"runtime": runtime, **material}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- バックアップ


def cache_path() -> Path:
    """バックアップの保存先（状態の保存先の `training.json`）。"""
    return identity.prepare_state_dir(identity.load()).path / CACHE_FILE


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def save(readings: list[Reading], now: float, env: Optional[Mapping[str, str]] = None) -> Path:
    """確かめた担当の結果を、認証の文脈とともにバックアップへ書く。

    文脈は確かめ始めたときのもの（`Reading.context`）を使い、無ければ確かめた環境 `env`（既定は今の環境）から求める。
    保存の時点で求め直すと、確かめる間に切り替わった別のアカウントへ結果がひも付く（#3907）。
    ほかの担当のバックアップは残す。読み・更新・一時ファイルからの置き換えを `training.json.lock` の排他の中で行い、
    同時に保存した担当の項目を消し合わない（#3907 の決定 11）。排他を待ちきれなければ `filelock.Timeout`。
    """
    env = os.environ if env is None else env
    return _rewrite_cache(
        lambda kept: kept.update({r.runtime: {**r.item(_stamp(now)), "context": r.context or context(r.runtime, env)} for r in readings})
    )


def _rewrite_cache(change) -> Path:
    """`training.json.lock` の排他の中でバックアップを読み、`change` で変えて置き換える。"""
    path = cache_path()
    with FileLock(str(path.with_name(f"{path.name}.lock")), timeout=limits.seconds(SAVE_LOCK_POLICY)):
        kept = _read_json(path)
        if kept is None:
            kept = {}
        change(kept)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps(kept, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return path


def forget(runtime: str) -> bool:
    """`runtime` のバックアップを次の起動が使えないようにする。できたら True。例外を出さない。

    確かめ直した結果を保存できなかったとき、その確かめで否定された古い記録（期限内の Off など）で起動が通らないよう呼ぶ。
    排他の中で担当の項目だけを消し、それもできなければ（排他の待ち切れ・書けない）ファイルごと消す。ほかの担当の記録も
    失うが、起動は断る側（バックアップが無い）へ倒れ、確かめ直せば戻る。
    """
    try:
        _rewrite_cache(lambda kept: kept.pop(runtime, None))
        return True
    except (identity.IdentityError, OSError):  # noqa: NDF401 ファイル: 書けなければファイルごと消す
        pass
    try:
        cache_path().unlink(missing_ok=True)
        return True
    except (identity.IdentityError, OSError):  # noqa: NDF401 ファイル: 消せなければ呼び出し元が理由に載せる
        return False


def _ask_check(runtime: str, why: str) -> str:
    return f"学習に使わない設定と確かめられていないので {runtime} を起動しない（{why}）。`{CHECK_COMMAND} --runtime {runtime}` で確かめる"


class Verdict(NamedTuple):
    """バックアップを読んだ判定。`why` は断る理由（起動してよければ None）、`stale` はバックアップが無いか期限切れで、
    確かめ直せば直りうるか（#3907）。"""

    why: Optional[str]
    stale: bool = False


def _cached_verdict(runtime: str, entry: object, now: float, expected: str) -> Verdict:
    if not isinstance(entry, dict):
        return Verdict(_ask_check(runtime, "バックアップが無い"), stale=True)
    try:
        checked_at = datetime.fromisoformat(str(entry.get("checked_at"))).timestamp()
    except ValueError:
        return Verdict(_ask_check(runtime, "バックアップの時刻が読めない"))
    if now - checked_at > limits.seconds(VALID_POLICY):
        return Verdict(_ask_check(runtime, f"バックアップの期限切れ（{entry.get('checked_at')} に確かめた）"), stale=True)
    training = entry.get("training")
    if training is True:
        reason = entry.get("reason")
        said = f"（{reason}）" if isinstance(reason, str) and reason else ""
        return Verdict(f"{runtime} のアカウントが入力を学習に使う設定なので起動しない{said}。{off_by_hand(runtime)}")
    if training is not False:
        return Verdict(_ask_check(runtime, f"確かめられなかった: {entry.get('reason')}"))
    if entry.get("context") != expected:
        return Verdict(_ask_check(runtime, "確かめた後に認証の文脈（アカウント・設定の保存先・認証の環境変数）が変わった"))
    return Verdict(None)


def verdict(runtime: str, now: Optional[float] = None, env: Optional[Mapping[str, str]] = None) -> Verdict:
    """`refusal` の判定を、確かめ直せば直りうるか（`stale`）とともに返す。記録は残さない。"""
    if runtime not in CHECKED_RUNTIMES:
        return Verdict(None)
    try:
        path = cache_path()
    except (identity.IdentityError, OSError) as exc:
        return Verdict(_ask_check(runtime, f"バックアップの保存先を用意できない: {exc}"))
    cache = _read_json(path) if path.is_file() else {}
    if cache is None:
        return Verdict(_ask_check(runtime, "バックアップが読めない"))
    expected = context(runtime, os.environ if env is None else env)
    return _cached_verdict(runtime, cache.get(runtime), time.time() if now is None else now, expected)


def refusal(runtime: str, now: Optional[float] = None, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`env`（起動先の環境。既定は今の環境）で `runtime` を起動してよければ None、断るなら理由。

    バックアップを読むだけで、確かめ直さない（確かめ直すのは `training.ensure`）。バックアップを確かめた認証の文脈が
    `env` の文脈と違えば断る。確かめる口の無い担当（kiro・agy など）は通し、記録に `unsupported` を残す。
    """
    if runtime not in CHECKED_RUNTIMES:
        record("training", runtime=runtime, state=UNSUPPORTED)
        return None
    why = verdict(runtime, now, env).why
    if why is not None:
        record("training", runtime=runtime, state=REFUSED)
    return why
