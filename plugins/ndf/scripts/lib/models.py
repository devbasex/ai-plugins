"""モデル指定の解析・フラグ生成・実測値の突き合わせ（収束ループ共通層）。

「どのランタイムのどのモデルが優れているか」を測るために、**指定値を固定し、
実際に動いたモデルを可能な限り記録する**。指定値は初期化時に確定して以後変えない
（途中で変えると比較が成立しない）。

モデル名の妥当性は各 CLI の検証に委ねる。ここで綴りをチェックすると、CLI 側が新しい
モデルを増やすたびにこの表を追いかけることになり、必ず古くなる。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import clock
from assignment import ALL_RUNTIMES

# モデルを渡すフラグ。4 CLI すべてで実在を確認済み
# （codex は `-m` の別名も持つが、長い方で統一する）。
MODEL_FLAGS: dict[str, str] = {
    "claude": "--model",
    "codex": "--model",
    "agy": "--model",
    "kiro": "--model",
}

# 既定モデルで走ったラウンドを報告で区別するための表示名。
DEFAULT_MODEL_LABEL = "default"

# kiro の既定モデル。**実際に選ばれたモデルを取得できない**ため、
# 計測目的の実行では必ず `--model kiro=<name>` を指定する。
KIRO_AUTO_MODEL = "auto"

# 実際に動いた言語モデルの名前を、**公開された記録から**読み取れるランタイム（#759）。
#
# | ランタイム | 取る場所 |
# | --- | --- |
# | claude | `--output-format json` の `modelUsage` |
# | codex | `$CODEX_HOME/sessions/<日付>/rollout-*.jsonl` の `turn_context.model` |
#
# agy は今の版（1.2.11）で要求ログにモデル名が載るかを確かめられていない（#1603 が確かめる）。
# 会話の記録（SQLite）は表の構造も値の形式も公開されていない。kiro の `auto` はサーバーの側で
# モデルを選び、クライアントの出力・ログ（kiro-cli 2.24.1 で確かめた）に選んだ名前が返らない。
# 確かめていない形から取ると、版の違いで誤った値を実測値として記録し、**指定どおりに動いた
# 実行を食い違いとして警告する**。取れないことより害が大きいため、この 2 つは取らない。
OBSERVABLE_RUNTIMES: tuple[str, ...] = ("claude", "codex")

# 実測値が取れなかった理由の符号（状態ファイルの `unobserved`）。文にしない（集計できなくなる）。
NO_RECORD = "no_record"
AMBIGUOUS = "ambiguous"
NO_MODEL_FIELD = "no_model_field"
UNSUPPORTED = "unsupported"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Observation:
    """実測の結果。`model` と `reason` のどちらか一方だけが入る。

    会話の本文を持ち出さないよう、モデル名と理由の符号の 2 つしか持たない。
    """

    model: Optional[str] = None
    reason: Optional[str] = None


class ModelSpecError(ValueError):
    """`--model` の指定が不正。呼び出し側は初期化ごと失敗させる。"""


def parse_model_args(pairs: Optional[Iterable[str]]) -> dict[str, Optional[str]]:
    """`--model <ランタイム>=<モデル>` の繰り返し指定を辞書へ変換する。

    未指定のランタイムは `None`（CLI の既定モデル）になる。同じランタイムを
    2 回指定したら後勝ちではなくエラーにする。取り違えたまま計測すると、
    どちらの値で走ったのか成果物から判別できない。
    """
    parsed: dict[str, Optional[str]] = {r: None for r in ALL_RUNTIMES}
    seen: set[str] = set()
    for raw in pairs or []:
        if "=" not in raw:
            raise ModelSpecError(f"--model は <ランタイム>=<モデル> の形式で指定してください: {raw}")
        runtime, _, model = raw.partition("=")
        runtime = runtime.strip()
        model = model.strip()
        if runtime not in parsed:
            raise ModelSpecError(f"未知のランタイムです: {runtime}（指定できるのは {'/'.join(ALL_RUNTIMES)}）")
        if not model:
            raise ModelSpecError(f"モデル名が空です: {raw}")
        if runtime in seen:
            raise ModelSpecError(f"--model {runtime}= が重複しています")
        seen.add(runtime)
        parsed[runtime] = model
    return parsed


def model_flag(runtime: str, model: Optional[str]) -> list[str]:
    """CLI へ渡すモデル指定フラグ。未指定なら空リスト（CLI の既定へ委ねる）。"""
    if not model:
        return []
    flag = MODEL_FLAGS.get(runtime)
    if flag is None:
        raise ModelSpecError(f"モデル指定に対応していないランタイムです: {runtime}")
    return [flag, model]


def label(model: Optional[str]) -> str:
    """報告で使う表示名。既定モデルで走ったラウンドを区別できるようにする。"""
    return model or DEFAULT_MODEL_LABEL


def separation_reason(runtime: str, model: Optional[str], observed: Optional[str] = None) -> Optional[str]:
    """集計から分離する理由。分離しないなら `None`。

    分離するのは**何が動いたか分からない**実行である。判定は実行の後に、実測値を見て決める。

    | 順 | 条件 | 判定 |
    | --- | --- | --- |
    | 1 | 実測値がある | 分離しない |
    | 2 | kiro で、指定が無いか `auto` | kiro の文言で分離する。`auto` はサーバーの側で選ばれ、
      選んだモデルがクライアントへ返らない |
    | 3 | 指定も実測値も無い | ランタイム名を入れた文言で分離する（claude も同じ） |

    **理由をランタイムごとに書き分ける。** 文言を 1 つにすると、報告を読む側は
    codex と agy の行を「指定したモデルの成績」として読める。
    """
    if observed:
        return None
    if runtime == "kiro" and model in (None, KIRO_AUTO_MODEL):
        return f"kiro の {KIRO_AUTO_MODEL} はラウンドごとに違うモデルが動きうる"
    if model is None:
        return f"{runtime} はモデルを指定しておらず、実際に動いたモデルも取得できない"
    return None


def foreseen_separation(runtime: str, model: Optional[str]) -> Optional[str]:
    """起動する前に、分離されそうな理由を返す。着手前の警告に使う。

    まだ実測値が無いため、実測できる見込みのあるランタイム（`OBSERVABLE_RUNTIMES`）は警告しない。
    """
    if runtime in OBSERVABLE_RUNTIMES:
        return None
    return separation_reason(runtime, model)


def assumption_note(runtime: str, model: Optional[str], observed: Optional[str] = None) -> Optional[str]:
    """指定値で代用したことを報告へ残す注記。代用でないなら `None`。

    分離ではない。指定があれば何を渡したかは分かるため集計へ入れるが、
    実測で裏付けたわけではないことを読み手が知る必要がある。
    """
    if observed or not model:
        return None
    if separation_reason(runtime, model, observed):
        return None
    return f"{runtime} は指定した {model} で動いた前提で数える（実測不可）"


def is_measurable(runtime: str, model: Optional[str], observed: Optional[str] = None) -> bool:
    """その実行を計測に使えるか。分離の理由が無いことと同じである。"""
    return separation_reason(runtime, model, observed) is None


_CLAUDE_MODEL_USAGE = re.compile(r'"modelUsage"\s*:\s*\{')
_CLAUDE_TOKEN_KEYS = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens")
_CODEX_SESSION_ID = re.compile(r"^session id:\s*([0-9A-Za-z-]+)\s*$", re.MULTILINE)


def observed_model(
    runtime: str,
    stem: os.PathLike[str] | str,
    ended_at: Any = None,
    not_before: Any = None,
) -> Observation:
    """起動 1 回で**実際に動いたモデル名**を取る。**失敗しない。**

    `stem` は `launch-cli.sh` へ渡した出力の接頭辞（置き場のディレクトリを含む）。
    起動の記録（`<stem>-launch.json`）の開始の時刻と作業ディレクトリで、その起動に
    結びつく記録だけを読む。起動の記録が無いか `not_before` より古ければ、前の起動の
    残骸として取らない。`ended_at` は監視の終了の時刻で、無ければ今を使う。

    取れなければ `Observation(None, <理由の符号>)` を返す。ランタイムの記録は読むだけで書き換えない。
    """
    try:
        return _observe(runtime, str(stem), ended_at, not_before)
    except Exception:  # noqa: BLE001 — 取得の失敗で起動の処理を止めない
        return Observation(reason=UNREADABLE)


def _observe(runtime: str, stem: str, ended_at: Any, not_before: Any) -> Observation:
    if runtime not in OBSERVABLE_RUNTIMES:
        return Observation(reason=UNSUPPORTED)
    launch = _read_launch_record(stem)
    started = clock.parse(launch.get("started_at")) if launch else None
    if launch is None or started is None:
        return Observation(reason=NO_RECORD)
    floor = clock.parse(not_before)
    if floor is not None and started < floor.replace(microsecond=0):
        return Observation(reason=NO_RECORD)
    if runtime == "claude":
        return _observe_claude(pathlib.Path(f"{stem}-stdout.log"))
    ended = clock.parse(ended_at) or clock.now(utc=True)
    return _observe_codex(stem, launch, started, ended)


def _read_launch_record(stem: str) -> Optional[dict[str, Any]]:
    path = pathlib.Path(f"{stem}-launch.json")
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _observe_claude(stdout_log: pathlib.Path) -> Observation:
    if not stdout_log.is_file():
        return Observation(reason=NO_RECORD)
    text = stdout_log.read_text(encoding="utf-8", errors="replace")
    if not _CLAUDE_MODEL_USAGE.search(text):
        return Observation(reason=NO_MODEL_FIELD)
    try:
        payload: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return Observation(reason=UNREADABLE)
    usage = payload.get("modelUsage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict) or not usage:
        return Observation(reason=NO_MODEL_FIELD)
    return Observation(model=_dominant_model(usage))


def _dominant_model(usage: dict[str, Any]) -> str:
    """主たるモデル: 4 種のトークンの和が最大のもの（#759 の決定 5）。

    入力のトークンだけで選ぶと、キャッシュの読み取りを数えず、補助で動いたモデルを選ぶ。
    """

    def _tokens(item: tuple[str, Any]) -> int:
        _, v = item
        if not isinstance(v, dict):
            return 0
        return sum(int(v.get(k) or 0) for k in _CLAUDE_TOKEN_KEYS)

    return max(usage.items(), key=_tokens)[0]


def _codex_sessions_dir() -> pathlib.Path:
    home = os.environ.get("CODEX_HOME")
    return pathlib.Path(home) if home else pathlib.Path.home() / ".codex"


def _session_day_dirs(started: _dt.datetime, ended: _dt.datetime) -> list[pathlib.Path]:
    """開始日の前日〜終了日の翌日の日付ディレクトリ。日付が現地時刻で切られても覆う。"""
    root = _codex_sessions_dir() / "sessions"
    day = (started.astimezone(_dt.timezone.utc) - _dt.timedelta(days=1)).date()
    last = (ended.astimezone(_dt.timezone.utc) + _dt.timedelta(days=1)).date()
    dirs: list[pathlib.Path] = []
    while day <= last:
        path = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if path.is_dir():
            dirs.append(path)
        day += _dt.timedelta(days=1)
    return dirs


def _observe_codex(stem: str, launch: dict[str, Any], started: _dt.datetime, ended: _dt.datetime) -> Observation:
    days = _session_day_dirs(started, ended)
    session_id = _codex_session_id(pathlib.Path(f"{stem}-err.log"))
    if session_id:
        matches = [f for d in days for f in d.glob(f"rollout-*-{session_id}.jsonl")]
    else:
        matches = [f for d in days for f in d.glob("rollout-*.jsonl") if _codex_meta_matches(f, launch, started, ended)]
    if not matches:
        return Observation(reason=NO_RECORD)
    if len(matches) > 1:
        return Observation(reason=AMBIGUOUS)
    return _codex_turn_model(matches[0])


def _codex_session_id(err_log: pathlib.Path) -> Optional[str]:
    """`codex exec` が標準エラーの見出しに出す `session id: <ID>`。見出しは先頭に出るため先頭だけを読む。"""
    if not err_log.is_file():
        return None
    with err_log.open(encoding="utf-8", errors="replace") as fh:
        head = fh.read(8192)
    found = _CODEX_SESSION_ID.search(head)
    return found.group(1) if found else None


def _codex_meta_matches(rollout: pathlib.Path, launch: dict[str, Any], started: _dt.datetime, ended: _dt.datetime) -> bool:
    """rollout の 1 行目（`session_meta`）の作業ディレクトリと時刻が、その起動に合うか。"""
    try:
        with rollout.open(encoding="utf-8", errors="replace") as fh:
            first = json.loads(fh.readline())
    except (OSError, json.JSONDecodeError):
        return False
    payload = first.get("payload") if isinstance(first, dict) and first.get("type") == "session_meta" else None
    if not isinstance(payload, dict):
        return False
    cwd, workdir = payload.get("cwd"), launch.get("workdir")
    if not isinstance(cwd, str) or not isinstance(workdir, str):
        return False
    if os.path.realpath(cwd) != os.path.realpath(workdir):
        return False
    at = clock.parse(payload.get("timestamp"), naive="utc")
    return at is not None and started.replace(microsecond=0) <= at <= ended


def _codex_turn_model(rollout: pathlib.Path) -> Observation:
    """`turn_context` の行の `payload.model`。値が 1 種類ならそれ、2 種類以上なら `ambiguous`。"""
    found: set[str] = set()
    try:
        with rollout.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"turn_context"' not in line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("type") != "turn_context":
                    continue
                payload = row.get("payload")
                model = payload.get("model") if isinstance(payload, dict) else None
                if isinstance(model, str) and model:
                    found.add(model)
    except (OSError, json.JSONDecodeError):
        return Observation(reason=UNREADABLE)
    if not found:
        return Observation(reason=NO_MODEL_FIELD)
    if len(found) > 1:
        return Observation(reason=AMBIGUOUS)
    return Observation(model=found.pop())


def model_record(requested: Optional[str] = None) -> dict[str, Optional[str]]:
    """状態ファイルへ置くモデルの記録の初期形（指定値・実測値・取れなかった理由）。"""
    return {"requested": requested, "observed": None, "unobserved": None}


def apply_observation(record: dict[str, Any], observation: Observation) -> None:
    """実測の結果を記録へ反映する。

    取れたら `observed` を入れ `unobserved` を空にする。取れなければ `unobserved` だけを書き、
    先に取れた `observed` があれば何も変えない（消さない）。
    """
    if observation.model:
        record["observed"], record["unobserved"] = observation.model, None
    elif not record.get("observed"):
        record["unobserved"] = observation.reason


def mismatch_warning(runtime: str, requested: Optional[str], observed: Optional[str]) -> Optional[str]:
    """指定値と実測値の食い違いを警告文にする。食い違いが無ければ `None`。

    実測値が無ければ常に `None` になる。「取れない」ことと「一致した」ことを
    混同しないよう、呼び出し側は分離を `is_measurable()` で別に区別する。
    """
    if not observed or not requested:
        return None
    if observed == requested:
        return None
    return f"⚠ {runtime}: 指定したモデル {requested} と実際に動いたモデル {observed} が食い違っています。比較には使えません"
