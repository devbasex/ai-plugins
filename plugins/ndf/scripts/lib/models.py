"""モデル指定の解析・フラグ生成・実測値の突き合わせ（収束ループ共通層）。

「どのランタイムのどのモデルが優れているか」を測るために、**指定値を固定し、
実際に動いたモデルを可能な限り記録する**。指定値は初期化時に確定して以後変えない
（途中で変えると比較が成立しない）。

モデル名の妥当性は各 CLI の検証に委ねる。ここで綴りを検査すると、CLI 側が新しい
モデルを増やすたびにこの表を追いかけることになり、必ず古くなる。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

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

# 実際に動いた言語モデルの名前を、**公開された出力から**読み取れるランタイム。
# claude だけが `--output-format json` の `modelUsage` を持つ。
#
# agy は会話の記録（利用者のホーム配下に置かれる SQLite ファイル）にモデル名を
# 残すが、表の構造も値の形式も公開されていない。版が上がって形が変われば誤った値を
# 実測値として記録することになり、**指定どおりに動いた実行を食い違いとして警告する**。
# 取得できないことより害が大きいため採らない。公開された手段が出た時点でここへ足せば、
# 判定も報告も追随する。
OBSERVABLE_RUNTIMES: tuple[str, ...] = ("claude",)


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
            raise ModelSpecError(
                f"--model は <ランタイム>=<モデル> の形式で指定してください: {raw}"
            )
        runtime, _, model = raw.partition("=")
        runtime = runtime.strip()
        model = model.strip()
        if runtime not in parsed:
            raise ModelSpecError(
                f"未知のランタイムです: {runtime}"
                f"（指定できるのは {'/'.join(ALL_RUNTIMES)}）"
            )
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


def separation_reason(runtime: str, model: Optional[str]) -> Optional[str]:
    """集計から分離する理由。分離しないなら `None`。

    分離するのは**何が動いたか分からない**ラウンドである。2 通りある。

    | 状態 | 条件 |
    | --- | --- |
    | 既定が `auto` | kiro。「タスクに応じて最適なモデルを選ぶ」ため、標準出力にも
      セッション一覧にも出ず、消費単位の倍率も 1.0 固定で逆算もできない |
    | 指定が無く実測もできない | `OBSERVABLE_RUNTIMES` の外で `--model` が無い |

    **理由をランタイムごとに書き分ける。** 文言を 1 つにすると、報告を読む側は
    codex と agy の行を「指定したモデルの成績」として読める。
    """
    if runtime == "kiro" and model in (None, KIRO_AUTO_MODEL):
        return f"kiro の {KIRO_AUTO_MODEL} はラウンドごとに違うモデルが動きうる"
    if model is None and runtime not in OBSERVABLE_RUNTIMES:
        return f"{runtime} はモデルを指定しておらず、実際に動いたモデルも取得できない"
    return None


def assumption_note(runtime: str, model: Optional[str]) -> Optional[str]:
    """指定値で代用したことを報告へ残す注記。代用でないなら `None`。

    分離ではない。指定があれば何を渡したかは分かるため集計へ入れるが、
    実測で裏付けたわけではないことを読み手が知る必要がある。
    """
    if not model or runtime in OBSERVABLE_RUNTIMES:
        return None
    if separation_reason(runtime, model):
        return None
    return f"{runtime} は指定した {model} で動いた前提で数える（実測不可）"


def is_measurable(runtime: str, model: Optional[str]) -> bool:
    """そのラウンドを計測に使えるか。分離の理由が無いことと同じである。"""
    return separation_reason(runtime, model) is None


_CLAUDE_MODEL_USAGE = re.compile(r'"modelUsage"\s*:\s*\{')


def observed_model(runtime: str, stdout_text: str) -> Optional[str]:
    """CLI の出力から**実際に使われたモデル名**を取り出す。取れなければ `None`。

    取れるのは `OBSERVABLE_RUNTIMES` に挙げた claude だけである
    （`--output-format json` の `modelUsage`）。codex / agy / kiro は実行した
    モデルを公開された機械可読な形で出さない。
    """
    if runtime not in OBSERVABLE_RUNTIMES or not stdout_text:
        return None
    if not _CLAUDE_MODEL_USAGE.search(stdout_text):
        return None
    try:
        payload: Any = json.loads(stdout_text)
    except (json.JSONDecodeError, TypeError):
        return None
    usage = payload.get("modelUsage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict) or not usage:
        return None
    # 複数モデルが動いた場合は入力トークンが最も多いものを主たるモデルとみなす。
    def _input_tokens(item: tuple[str, Any]) -> int:
        _, v = item
        return v.get("inputTokens", 0) if isinstance(v, dict) else 0

    return max(usage.items(), key=_input_tokens)[0]


def mismatch_warning(
    runtime: str, requested: Optional[str], observed: Optional[str]
) -> Optional[str]:
    """指定値と実測値の食い違いを警告文にする。食い違いが無ければ `None`。

    実測値を取れないランタイムでは常に `None` になる。「取れない」ことと
    「一致した」ことを混同しないよう、呼び出し側は既定モデルのラウンドを
    `is_measurable()` で別に区別する。
    """
    if not observed or not requested:
        return None
    if observed == requested:
        return None
    return (
        f"⚠ {runtime}: 指定したモデル {requested} と実際に動いたモデル {observed} が"
        "食い違っています。比較には使えません"
    )
