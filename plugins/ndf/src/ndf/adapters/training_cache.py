"""学習に使わない設定のスナップショットと、バックアップを読んで起動を断る規則（#3391 の 3391-5）。

バックアップは状態の保存先の `training.json` に、`ndf training check`（`training.py`）が時刻つきで書く。LLM と外部 CLI の
起動の口（`launch_slot.gate`）は `refusal` でバックアップを読むだけで、学習に使う・バックアップが無い・期限切れ
（`contracts/limits.json` の `training.check-valid`）・読めないなら起動を断り、理由に確認のサブコマンドを載せる。

バックアップは確かめたときの認証の文脈（設定の保存先・アカウントの識別子・確かめに使う認証情報・認証の環境変数）の
ハッシュ（`context`）を持ち、起動先の環境から求めた文脈と違えば（アカウントや保存先を切り替えた後・トークンを更新した後）
期限内でも記録を使わず、起動の口が確かめ直す（`stale`）。ハッシュの材料はバックアップ・出力・記録へ写さない。

確かめ直した結果を記録できないときは、担当ごとの取り消しのファイル（`training.<担当>.revoked`）で古い記録を使えなくする
（`forget`）。取り消しのファイルは `training.json` の外に置くので、ほかの担当の保存が古い内容を書き戻しても取り消しは消えない。

外部の API から設定を読む接続（httpx）は `training.py` が持ち、ここは読まない。claude の認証情報（認証ファイル・
macOS のキーチェーン）は、確かめに使うトークンと文脈を同じ読み方で取るよう、ここが読む。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
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
CLAUDE_TOKEN_ENV = str(facts.row("env", "CLAUDE_CODE_OAUTH_TOKEN")["id"])
# OAuth より優先される認証の変数（値は出さず、名前だけを理由に載せる）。名前は環境変数の表の行から引き、
# 提供元の選択の変数は表の provider の行すべて（子の環境・実効の設定の確かめ・従量の接続の検証と同じ 1 つの分類）
CLAUDE_FOREIGN_AUTH = (
    *(str(facts.row("env", name)["id"]) for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")),
    *facts.ids("env", provider=True),
)
# claude のログインのアカウントの記録（設定の保存先の外のホーム、または CLAUDE_CONFIG_DIR の下）
CLAUDE_STATE = ".claude.json"
# claude の OAuth の認証情報（設定の保存先の下。CLAUDE_CONFIG_DIR が無い macOS ではキーチェーン）
CLAUDE_CREDENTIALS = ".credentials.json"
KEYCHAIN_SERVICE = "Claude Code-credentials"
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
        return self._asdict()


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


def claude_oauth_entry(env: Mapping[str, str]) -> Optional[dict]:
    """認証ファイル（→ `CLAUDE_CONFIG_DIR` が無い macOS ではキーチェーン）の `claudeAiOauth`。無ければ None。

    確かめに使うトークン（`training._claude_token`）と認証の文脈（`context`）の両方がここから読む。"""
    conf = env.get("CLAUDE_CONFIG_DIR")
    credentials = _read_json((Path(conf) if conf else _home(env) / ".claude") / CLAUDE_CREDENTIALS)
    entry = credentials.get("claudeAiOauth") if credentials is not None else None
    if not isinstance(entry, dict) and not conf and sys.platform == "darwin":
        entry = _keychain_oauth()
    return entry if isinstance(entry, dict) else None


def _claude_context(env: Mapping[str, str]) -> dict[str, object]:
    """claude の文脈の材料。`.claude.json` のアカウントだけでは、確かめに使った認証情報（認証ファイル・キーチェーン）が
    同じアカウントのものか分からない（ログインの切り替えの途中で片方だけ書き換わる）。そこで、確かめに使うトークンも
    材料に入れ、記録をそのトークンにひも付ける（#3907）。`CLAUDE_CODE_OAUTH_TOKEN` があればそちらを使うので、
    認証ファイルのトークンは入れない（値は `env` の材料に入る）。"""
    conf = env.get("CLAUDE_CONFIG_DIR")
    state = _read_json(Path(conf) / CLAUDE_STATE if conf else _home(env) / CLAUDE_STATE) or {}  # noqa: NDF501 ファイル: 無いか壊れていればアカウントは無い
    account = state.get("oauthAccount")
    entry = None if env.get(CLAUDE_TOKEN_ENV) else claude_oauth_entry(env)
    return {
        "config_dir": conf or str(_home(env) / ".claude"),
        "account": account.get("accountUuid") if isinstance(account, dict) else None,
        "credential": entry.get("accessToken") if entry is not None else None,
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


def revoked_path(runtime: str) -> Path:
    """`runtime` の取り消しのファイル（状態の保存先の `training.<担当>.revoked`。中身は取り消した時刻）。"""
    return cache_path().with_name(_revoked_name(runtime))


def _revoked_name(runtime: str) -> str:
    return f"training.{runtime}.revoked"


# 時刻によらず今ある記録をすべて取り消す取り消しのファイルの中身（`forget` がバックアップを読めなかったとき）
REVOKE_ALL = "all"


def save(readings: list[Reading], now: float, env: Optional[Mapping[str, str]] = None) -> Path:
    """確かめた担当の結果を、認証の文脈とともにバックアップへ書く。

    文脈は確かめ始めたときのもの（`Reading.context`）を使い、無ければ確かめた環境 `env`（既定は今の環境）から求める。
    保存の時点で求め直すと、確かめる間に切り替わった別のアカウントへ結果がひも付く（#3907）。
    ほかの担当のバックアップは残す。読み・更新・一時ファイルからの置き換えを `training.json.lock` の排他の中で行い、
    同時に保存した担当の項目を消し合わない（#3907 の決定 11）。排他を待ちきれなければ `filelock.Timeout`。
    書けたら、保存した担当の取り消しのファイル（`forget`）を消す（新しい記録が取り消しのファイルより前の時刻を持つことがあるため）。
    """
    env = os.environ if env is None else env

    def record_readings(kept: dict) -> None:
        """排他の中で、確かめた担当の記録を文脈とともに `kept` へ上書きする（ほかの担当の項目は残す）。"""
        kept.update({r.runtime: {**r.item(_stamp(now)), "context": r.context or context(r.runtime, env)} for r in readings})

    path = _rewrite_cache(record_readings)
    for r in readings:
        _unlink(revoked_path(r.runtime))
    return path


def _unlink(path: Path) -> bool:
    """`path` を消す。消せたか（無かったときを含む）を返す。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # noqa: NDF401 ファイル: 消せなければ取り消しのファイルが残り、起動の口が確かめ直す（断る側へ倒れる）
        return False
    return True


def _rewrite_cache(change) -> Path:
    """`training.json.lock` の排他の中でバックアップを読み、`change` で変えて置き換える。"""
    path = cache_path()
    with FileLock(str(path.with_name(f"{path.name}.lock")), timeout=limits.seconds(SAVE_LOCK_POLICY)):
        kept = _read_json(path)
        if kept is None:
            kept = {}
        change(kept)
        _replace_text(path, json.dumps(kept, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _replace_text(path: Path, text: str) -> None:
    """`text` を同じディレクトリの一時ファイルへ書き、`path` へ置き換える（読み手に書きかけを見せない）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _checked_at(entry: object) -> Optional[float]:
    """記録の確かめた時刻。記録が無いか時刻が読めなければ None。"""
    if not isinstance(entry, dict):
        return None
    try:
        return datetime.fromisoformat(str(entry.get("checked_at"))).timestamp()
    except ValueError:  # noqa: NDF401 ファイル: 時刻が読めない記録は無いとして扱い、確かめ直す側へ倒れる
        return None


def _read_cache(path: Path) -> Optional[dict]:
    """バックアップの中身。無ければ空、在るのに読めないか壊れていれば None（`_read_json` と違い、無いと読めないを分ける）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めなければ呼び出し元がすべて取り消す
        return None
    return data if isinstance(data, dict) else None


def forget(runtime: str, now: float) -> bool:
    """`runtime` の今ある記録と `now` までに確かめた記録を次の起動が使えないようにする。できたら True。例外を出さない。

    確かめ直した結果を保存できなかったとき、その確かめで否定された古い記録（期限内の Off など）で起動が通らないよう呼ぶ。
    `training.json` は書き換えず、担当ごとの取り消しのファイル（`revoked_path`）に時刻を書く。`training.json` から項目を
    消すと、ほかの担当の保存が消す前に読んだ内容を置き換えで書き戻し、古い記録が戻る（#3907）。取り消しのファイルはほかの担当の保存が
    触らず、同じ担当の書き手は確かめの排他（`training.<担当>.lock`）で 1 つに絞られるので、`training.json.lock` は要らない。

    呼び出し元は確かめの排他の中で呼ぶ。`now` は排他を取る前に決まるので、排他を待つ間にほかの確かめが保存した記録は
    `now` より新しい時刻を持つ。それもこの確かめで否定されたので、今ある記録の時刻と `now` の遅い方まで取り消す（#3907）。

    バックアップが無ければ `now` まで取り消す。在るのに読めなければ（読み取りの一時的な失敗・壊れた中身）、今ある記録の
    時刻を確かめられないので、時刻によらずすべての記録を取り消す（`REVOKE_ALL`。次の `save` が取り消しのファイルを消すまで続く）。
    読めないのを無いとして `now` まで取り消すと、排他を待つ間に保存された `now` より新しい Off が、読み取りが直った後に
    起動を通す（#3907）。
    """
    try:
        path = revoked_path(runtime)
        cache = _read_cache(path.with_name(CACHE_FILE))
        if cache is None:
            text = REVOKE_ALL
        else:
            current = _checked_at(cache.get(runtime))
            text = _stamp(now if current is None else max(now, current))
        _replace_text(path, text + "\n")
        return True
    except (identity.IdentityError, OSError):  # noqa: NDF401 ファイル: 書けなければ呼び出し元が理由に載せる
        return False


def _marker_text(runtime: str, path: Path) -> tuple[Optional[str], bool]:
    """(取り消しのファイルの中身, 取り消しのファイルが読めたか)。取り消しのファイルが無ければ (None, True)。"""
    try:
        return path.with_name(_revoked_name(runtime)).read_text(encoding="utf-8").strip(), True
    except FileNotFoundError:
        return None, True
    except (OSError, UnicodeDecodeError):  # 読めなければすべて取り消したとして扱う
        return None, False


def _revoked_until(marker: tuple[Optional[str], bool]) -> tuple[Optional[float], bool]:
    """`_marker_text` の結果から (取り消した時刻, 取り消しのファイルが読めたか)。取り消しのファイルが無ければ (None, True)。"""
    text, readable = marker
    if text is None:
        return None, readable
    if text == REVOKE_ALL:
        return math.inf, True
    try:
        return datetime.fromisoformat(text).timestamp(), True
    except ValueError:
        return None, False


# バックアップと取り消しのファイルをそろえて読み直す回数の上限（`_snapshot`）
SNAPSHOT_TRIES = 3


def _snapshot(runtime: str, path: Path) -> Optional[tuple[Optional[dict], tuple[Optional[str], bool]]]:
    """取り消しのファイル・バックアップ・取り消しのファイルの順に読み、取り消しのファイルが読む間に変わらなかった (バックアップ, 取り消しのファイル) を返す。

    `save` はバックアップを置き換えてから取り消しのファイルを消し、`forget` は取り消しのファイルだけを書く。バックアップを読んだ後で取り消しのファイルだけを読むと、
    その間にほかの確かめが On を保存して取り消しのファイルを消したとき、取り消し済みの古い Off を有効と読む（#3907）。前後の取り消しのファイルが同じなら、
    読んだバックアップはその取り消しのファイルと同じ時点のものとして扱える。変わり続けて `SNAPSHOT_TRIES` 回そろわなければ None。
    """
    for _ in range(SNAPSHOT_TRIES):
        before = _marker_text(runtime, path)
        cache = _read_json(path) if path.is_file() else {}
        if _marker_text(runtime, path) == before:
            return cache, before
    return None


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
    checked_at = _checked_at(entry)
    if checked_at is None:
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
        # トークンの更新でも変わるので、確かめ直せば直りうる（起動の口がその場で確かめ直す）
        return Verdict(
            _ask_check(runtime, "確かめた後に認証の文脈（アカウント・設定の保存先・認証情報・認証の環境変数）が変わった"), stale=True
        )
    return Verdict(None)


def verdict(runtime: str, now: Optional[float] = None, env: Optional[Mapping[str, str]] = None) -> Verdict:
    """`refusal` の判定を、確かめ直せば直りうるか（`stale`）とともに返す。記録は残さない。"""
    if runtime not in CHECKED_RUNTIMES:
        return Verdict(None)
    try:
        path = cache_path()
    except (identity.IdentityError, OSError) as exc:
        return Verdict(_ask_check(runtime, f"バックアップの保存先を用意できない: {exc}"))
    snapshot = _snapshot(runtime, path)
    if snapshot is None:
        return Verdict(_ask_check(runtime, "読む間に記録の取り消しのファイルが変わり続けた"), stale=True)
    cache, marker = snapshot
    # 取り消しのファイルはバックアップの読み取りより先に見る。`forget` はバックアップが壊れているときに全件取り消しのファイルを書くので、
    # 読めないバックアップで止めると、起動の口が確かめ直して直す経路へ進めない（#3907）
    until, readable = _revoked_until(marker)
    if not readable:
        return Verdict(_ask_check(runtime, "記録の取り消しのファイルが読めない"), stale=True)
    if until == math.inf:
        return Verdict(_ask_check(runtime, "確かめ直した結果を記録できず、前の記録を取り消した"), stale=True)
    if cache is None:
        return Verdict(_ask_check(runtime, "バックアップが読めない"))
    entry = cache.get(runtime)
    checked_at = _checked_at(entry)
    # 時刻の読めない記録は _cached_verdict が断る
    if until is not None and checked_at is not None and checked_at <= until:
        return Verdict(_ask_check(runtime, "確かめ直した結果を記録できず、前の記録を取り消した"), stale=True)
    expected = context(runtime, os.environ if env is None else env)
    return _cached_verdict(runtime, entry, time.time() if now is None else now, expected)


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
