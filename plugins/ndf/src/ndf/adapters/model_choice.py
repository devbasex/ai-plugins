"""起動のモデルと effort を選ぶ（#2944 の「値を選ぶ処理」）。

起動する所は、層（`worker`・`skill`・`judge`・`seat`）・作業の名前・ランタイムを渡し、値と出所（`Choice`）を受け取る。
選択肢は同梱の表（`contracts/domains/launch-experiments.json`）に、リポジトリの起動の設定（`.ndf/launch.json`）の
`experiments` を重ねたもの（`table`）。重ねた後の値が形や制約に合わなければ、使わない値でも `precondition` で止める
（黙って別の値で起動しない。受け入れ条件 14）。

モデルと effort は別々に、上から最初に値があるものを使う（設計書の「値の優先」）。

1. 起動の引数（`argument`）
2. 一時的な上書きの環境変数（`NDF_LAUNCH_*`。どの変数を読むかは表 launch-experiments の order の行が表 env のキーで持つ）
3. ランタイム自身の effort の環境変数（claude の `CLAUDE_CODE_EFFORT_LEVEL`。値は検証せず、子へ引き継ぐ）
4. 工程の選択肢（基準の組は先頭）
5. 指定なし（CLI の既定）

1〜3 で片側でも指定があれば、その起動は割り付けず、指定の無い側は基準の組の値（出所 `base`。組が値を持たなければ `default`）。
`retry`（同じ失敗への 2 回目以降の起動の理由）があれば、決まった組に `raised` を 1 回だけ当てる。引数と環境変数の値は上げない。

単位（`unit`）を持つ起動は、指定が無ければ割り付ける（設計書の「割り付けの流れ」）。停止（`enabled` が偽）・固定（選択肢が 1 つ）・
単位の停止か基準への切り替えの記録・既にある割り付け（互換性のキーが同じなら同じ組）・候補の抽選の順に見る。記録は使用量ログと
同じ保存先の月ごとの JSONL（表 ndf-files の launch-experiments）に追記し、全部の月で 1 つのロックの中で判定と追記をする。
記録を読み書きできなければ警告を出し、割り付けずに起動する。起動のたびに最初に月の掃除（`prune`）をする。
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import random
import re
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from filelock import FileLock, Timeout

from ndf import contracts, identity
from ndf.adapters import launch_records, launch_settings, launch_store
from ndf.adapters.launch_store import LEDGER, LEDGER_LOCK, MONTH, PRUNED, PRUNED_LOCK, REPO, WEIGHTS, WEIGHTS_LOCK
from ndf.adapters.launch_store import done_in as _pruned
from ndf.adapters.launch_store import drop_months as _drop_months
from ndf.adapters.launch_store import month as _month
from ndf.adapters.launch_store import month_files as _month_files
from ndf.adapters.launch_store import month_start as _month_start
from ndf.adapters.launch_store import past as _past
from ndf.adapters.launch_store import prune_weights as _prune_weights
from ndf.adapters.launch_store import store_file as _store_file
from ndf.adapters.launch_store import write_replacing as _write_replacing
from ndf.contracts import facts, internal, limits

# 同梱の表（表 ndf-files の実験の記録の行と同じ名前）
TABLE = LEDGER
# 値の引数の形の列（表 runtimes）
ARGS_COLUMN = "choice_args"
# 引数の形の値に入れる位置（`{model}`・`{effort}`）
_PLACE = re.compile(r"\{(model|effort)\}")
# 改行（モデル名に含むと引数の 1 行 1 語の形が壊れる）
_NEWLINE = re.compile(r"[\r\n]")


class Source(enum.StrEnum):
    """値の出所（使用量ログの model_source・effort_source）。"""

    ARGUMENT = "argument"
    ENVIRONMENT = "environment"
    EXPERIMENT = "experiment"
    BASE = "base"
    DEFAULT = "default"


def _fail(detail: str) -> internal.InternalError:
    return internal.InternalError("precondition", detail)


def _env_names() -> dict[str, str]:
    """表 launch-experiments の order の行が持つ環境変数のキー → 名前（`identity.env_name` はキーを文字列リテラルで受ける）。"""
    return {
        "launch-model": identity.env_name("launch-model"),
        "launch-effort": identity.env_name("launch-effort"),
        "launch-codex-model": identity.env_name("launch-codex-model"),
        "launch-codex-effort": identity.env_name("launch-codex-effort"),
        "claude-effort-level": identity.env_name("claude-effort-level"),
    }


def _order_env(order: Mapping[str, Any], column: str) -> Optional[str]:
    """順位の行の列 `column` が指す環境変数の名前。列が無ければ None。"""
    return _env_names()[order[column]] if column in order else None


@dataclass(frozen=True)
class Table:
    """同梱の表にリポジトリの `experiments` を重ね、検証した後の値。`arms` は工程 → ランタイム → 選択肢（先頭が基準の組）。"""

    enabled: bool
    allocation: Mapping[str, Any]
    arms: Mapping[str, Mapping[str, tuple[Mapping[str, str], ...]]]


@dataclass(frozen=True)
class Choice:
    """1 回の起動の値と出所。`arm` は割り付けた組（実験でなければ None）、`base_arm` は工程の基準の組（工程が無ければ None）。

    `inherited_effort` は、ランタイム自身の effort の環境変数を子へ引き継ぐときの値（そのとき `effort` は None で、
    `--effort` を足さない）。`retry` は引き上げを求めた理由（引数か環境変数の指定の起動は None）で、`raised` は値が実際に
    変わったか。`store` は選んだときの実験の記録の保存先で、`base_fallback` が同じ保存先へ書くために持つ。
    """

    layer: str
    name: str
    runtime: str
    stage: Optional[str]
    mode: Optional[str]
    model: Optional[str]
    effort: Optional[str]
    model_source: Source
    effort_source: Source
    unit: Optional[str] = None
    assign_id: Optional[str] = None
    raised: bool = False
    arm: Optional[Mapping[str, str]] = None
    base_arm: Optional[Mapping[str, str]] = None
    inherited_effort: Optional[str] = None
    retry: Optional[str] = None
    store: Optional[Path] = None

    def cli_args(self) -> list[str]:
        """CLI に渡す引数（表 runtimes の `choice_args` の形。値の無い側は足さない）。"""
        row = facts.row(launch_settings.RUNTIMES, self.runtime)
        words = row[ARGS_COLUMN] if ARGS_COLUMN in row else []
        values = {"model": self.model, "effort": self.effort}
        out: list[str] = []
        for flag, form in zip(words[::2], words[1::2]):
            place = _PLACE.search(form)
            value = values[place.group(1)] if place is not None else None
            if value is not None:
                out += [flag, _PLACE.sub(lambda _: value, form)]  # 置換の文字列として解釈させず、値を文字どおり入れる
        return out

    def env_changes(self) -> dict[str, Optional[str]]:
        """子の環境の変更。ランタイム自身の effort の変数を、引き継ぐなら値、外すなら None にする。変えなければ空。"""
        name = _order_env(_order_row(self.runtime), "inherited_effort_env")
        if name is None:
            return {}
        if self.inherited_effort is not None:
            return {name: self.inherited_effort}
        if self.effort_source in (Source.ARGUMENT, Source.ENVIRONMENT):
            return {name: None}
        return {}

    def row_fields(self) -> dict[str, Any]:
        """使用量ログの行に足す欄（値の無い欄は置かない）。`unit` は割り付けを受けた起動（`assign_id` がある）だけに置く。"""
        fields: dict[str, Any] = {
            "layer": self.layer,
            "name": self.name,
            "runtime": self.runtime,
            "mode": self.mode,
            "unit": self.unit if self.assign_id is not None else None,
            "assign_id": self.assign_id,
            "requested_model": self.model,
            "effort": self.effort,
            "model_source": self.model_source.value,
            "effort_source": self.effort_source.value,
            "raised": self.raised,
        }
        return {k: v for k, v in fields.items() if v is not None}


def _bundled_path() -> Path:
    return contracts.contracts_dir() / facts.location(TABLE)


def _stage_rows() -> tuple[dict[str, Any], ...]:
    return facts.rows(TABLE, kind="stage")


def _order_row(runtime: str) -> dict[str, Any]:
    """ランタイムの順位の行。無いランタイムは `precondition`。"""
    found = {r["id"]: r for r in facts.rows(TABLE, kind="order")}
    if runtime not in found:
        raise _fail(f"ランタイム {runtime} は値を選べない（選べるのは {'・'.join(found)}。{_bundled_path()}）")
    return found[runtime]


def _launch_stages() -> dict[tuple[str, str], str]:
    """(層, 作業の名前) → 工程。"""
    return {(layer, name): r["id"] for r in _stage_rows() for layer, names in r["launches"].items() for name in names}


def stage_of(layer: str, name: str) -> Optional[str]:
    """層と作業の名前から工程を引く。表に無い組は None（実験の引数を足さない）。どの工程にも無い層は `precondition`。"""
    launches = _launch_stages()
    layers = sorted({lay for lay, _ in launches})
    if layer not in layers:
        raise _fail(f"層 {layer} は無い（層は {'・'.join(layers)}）")
    return launches[(layer, name)] if (layer, name) in launches else None


def _retries() -> set[str]:
    return {reason for r in _stage_rows() if "retries" in r for reason in r["retries"]}


def _bundled() -> dict[str, Any]:
    """同梱の値を起動の設定の `experiments` と同じ形で返す。"""
    defaults = facts.rows(TABLE, kind="defaults")
    if len(defaults) != 1:
        raise _fail(f"{_bundled_path()} の defaults の行が {len(defaults)} 行ある（1 行だけ要る）")
    stages = {r["id"]: {"arms": r["arms"]} for r in _stage_rows()}
    return {"enabled": defaults[0]["enabled"], "stages": stages, "allocation": defaults[0]["allocation"]}


def _given(given: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """リポジトリが書いたオブジェクトのキー。無ければ空（同梱の値を使う）。"""
    return given[key] if key in given else {}


def _given_keys(given: Mapping[str, Any], bundled: Mapping[str, Any], where: Path) -> None:
    """リポジトリが同梱に無い工程・ランタイムを書いていないか。"""
    for stage, spec in _given(given, "stages").items():
        if stage not in bundled["stages"]:
            raise _fail(f"{where} の experiments/stages/{stage} は無い工程（工程は {'・'.join(bundled['stages'])}）")
        known = bundled["stages"][stage]["arms"]
        for runtime in spec["arms"]:
            if runtime not in known:
                raise _fail(f"{where} の experiments/stages/{stage}/arms/{runtime} は、この工程に無いランタイム（{'・'.join(known)}）")


def _overlaid(bundled: Mapping[str, Any], given: Mapping[str, Any]) -> dict[str, Any]:
    arms = {stage: dict(spec["arms"]) for stage, spec in bundled["stages"].items()}
    for stage, spec in _given(given, "stages").items():
        arms[stage].update(spec["arms"])
    return {
        "enabled": given["enabled"] if "enabled" in given else bundled["enabled"],
        "allocation": {**bundled["allocation"], **_given(given, "allocation")},
        "arms": arms,
    }


def _arms_errors(stage: str, runtime: str, arms: list[Any]) -> list[str]:
    """選択肢の並びの値の制約の誤り（`<キーの位置>: <理由>`）。"""
    at = f"experiments/stages/{stage}/arms/{runtime}"
    if not arms:
        return [f"{at}: 選択肢が空（1 組以上要る）"]
    efforts = _order_row(runtime)["efforts"]
    errs: list[str] = []
    for i, arm in enumerate(arms):
        if arm in arms[:i]:
            errs.append(f"{at}/{i}: 同じ組を 2 回書いている")
        if "effort" in arm and arm["effort"] not in efforts:
            errs.append(f"{at}/{i}/effort: {arm['effort']} は順位に無い（{'・'.join(efforts)}）")
        if "model" in arm and (not arm["model"] or _NEWLINE.search(arm["model"])):
            errs.append(f"{at}/{i}/model: 空か改行を含む")
    return errs


# window_days の上限。ほかのリポジトリの記録は、どの設定でも使わないこの日数（と records の行の kept_extra_days）を過ぎた月だけを消す
WINDOW_LIMIT = 365


def _allocation_errors(allocation: Mapping[str, Any], widest: int) -> list[str]:
    """割り付けの値の制約の誤り。`widest` は最も多い選択肢の数。"""
    at = "experiments/allocation"
    floor, units, margin, days = (allocation[k] for k in ("floor", "min_units", "margin", "window_days"))
    errs = []
    if not floor > 0 or floor * widest > 1:
        errs.append(f"{at}/floor: 0 より大きく、floor × 組の数（最大 {widest}）が 1 以下")
    if units < 2:
        errs.append(f"{at}/min_units: 2 以上")
    if not 0 <= margin <= 1:
        errs.append(f"{at}/margin: 0 以上 1 以下")
    if not 1 <= days <= WINDOW_LIMIT:
        errs.append(f"{at}/window_days: 1 以上 {WINDOW_LIMIT} 以下")
    return errs


def bundled_errors() -> list[str]:
    """同梱の値が起動の設定の形（スキーマ）に合わない箇所。同梱の値はリリースの後に変わらないので、起動のたびには照らさず CI のテストが呼ぶ。"""
    return contracts.broken_places({"version": 1, "experiments": _bundled()}, launch_settings.SCHEMA)


def table(root: Path) -> Table:
    """同梱の表とリポジトリ `root` の `experiments` を重ね、値の制約を確かめる。外れれば直す場所を載せて `precondition`。"""
    settings = launch_settings.load(root)
    given = settings.experiments
    bundled = _bundled()
    _given_keys(given, bundled, settings.path)
    merged = _overlaid(bundled, given)
    errs = [e for stage, by in merged["arms"].items() for runtime, arms in by.items() for e in _arms_errors(stage, runtime, arms)]
    widest = max(len(arms) for by in merged["arms"].values() for arms in by.values())
    errs += _allocation_errors(merged["allocation"], widest)
    if errs:
        raise _fail(f"モデルと effort の選択肢の値が制約に合わない（{settings.path} か同梱の {_bundled_path()}）: {'; '.join(errs)}")
    arms = {stage: {runtime: tuple(a) for runtime, a in by.items()} for stage, by in merged["arms"].items()}
    return Table(merged["enabled"], merged["allocation"], arms)


def raised(runtime: str, model: Optional[str], effort: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """設計書の「引き上げる起動」の上げ方を 1 行だけ当てた (モデル, effort)。当たらなければ元の値。

    境の effort（順位の行の `raise_at`。`high`）より低い effort は 1 つ上げる。モデルの順位を持つランタイムで、最上位でない
    順位のモデルは、effort が境以上か指定なしならモデルを 1 つ上げる。ほかは effort が境なら 1 つ上げ、それ以外は変えない。
    """
    order = _order_row(runtime)
    efforts: list[str] = order["efforts"]
    models: list[str] = order["models"] if "models" in order else []
    edge = efforts.index(order["raise_at"])
    at = efforts.index(effort) if effort in efforts else None
    if at is not None and at < edge:
        return model, efforts[at + 1]
    if model in models[:-1]:
        return models[models.index(model) + 1], effort
    if at == edge:
        return model, efforts[at + 1]
    return model, effort


def _check_effort(value: str, runtime: str, where: str) -> None:
    efforts = _order_row(runtime)["efforts"]
    if value not in efforts:
        raise _fail(f"{where} の effort {value} は {runtime} の順位に無い（{'・'.join(efforts)}）")


def _check_model(value: str, where: str) -> None:
    if _NEWLINE.search(value):
        raise _fail(f"{where} のモデル名が改行を含む")


def _first_given(*found: tuple[Optional[str], Source, str]) -> Optional[tuple[str, Source, str]]:
    """値のある最初の (値, 出所, 読んだ場所)。どれも無ければ None。下位の値は使わないので確かめない。"""
    for value, source, where in found:
        if value:
            return value, source, where
    return None


@dataclass(frozen=True)
class _Manual:
    """引数と環境変数の指定。`inherited` はランタイム自身の effort の変数を引き継ぐ値。"""

    model: Optional[tuple[str, Source]]
    effort: Optional[tuple[str, Source]]
    inherited: Optional[str]

    @property
    def given(self) -> bool:
        return self.model is not None or self.effort is not None or self.inherited is not None


def _manual(runtime: str, argument: tuple[Optional[str], Optional[str]], environ: Mapping[str, str]) -> _Manual:
    """引数と環境変数の指定を読む。モデルと effort それぞれで優先の順に値を決めてから、使う値だけを確かめる。誤りは `precondition`。"""
    order = _order_row(runtime)
    model_env, effort_env = _order_env(order, "model_env"), _order_env(order, "effort_env")
    model = _first_given(
        (argument[0], Source.ARGUMENT, "引数 --model"),
        (environ[model_env] if model_env in environ else None, Source.ENVIRONMENT, f"環境変数 {model_env}"),
    )
    effort = _first_given(
        (argument[1], Source.ARGUMENT, "引数 --effort"),
        (environ[effort_env] if effort_env in environ else None, Source.ENVIRONMENT, f"環境変数 {effort_env}"),
    )
    if model is not None:
        _check_model(model[0], model[2])
    if effort is not None:
        _check_effort(effort[0], runtime, effort[2])
    inherited_env = _order_env(order, "inherited_effort_env")
    inherited = None
    if effort is None and inherited_env is not None and inherited_env in environ and environ[inherited_env]:
        inherited = environ[inherited_env]
    return _Manual(
        model[:2] if model is not None else None,
        effort[:2] if effort is not None else None,
        inherited,
    )


def _from_base(base: Optional[Mapping[str, str]], key: str) -> tuple[Optional[str], Source]:
    value = base[key] if base is not None and key in base else None
    return value, (Source.BASE if value is not None else Source.DEFAULT)


@dataclass(frozen=True)
class Request:
    """`launch_choice` に渡す起動の手がかり。`charge_to` は手戻りの費用を負う割り付け ID（実験の記録を書くときに使う）。"""

    unit: Optional[str] = None
    charge_to: Optional[str] = None
    retry: Optional[str] = None
    mode: Optional[str] = None
    argument: tuple[Optional[str], Optional[str]] = (None, None)


# --- 実験の記録 --------------------------------------------------------------------------------------

# 錠の待ちの上限（contracts/limits）
LOCK_LIMIT = "launch.experiments-lock"
# 記録を読み書きできない原因（起動は続ける）
_STORE_ERRORS = (OSError, Timeout, LookupError, identity.IdentityError)

log = logging.getLogger(__name__)


class Halt(enum.StrEnum):
    """停止の行の理由。"""

    DISABLED = "disabled"
    FIXED = "fixed"
    CHANGED = "changed"
    MANUAL = "manual"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_value() -> float:
    """抽選の乱数（0 以上 1 未満）。テストが差し替える。"""
    return random.random()  # noqa: S311 抽選の偏りを避けるだけで、秘密には使わない


def _time_text(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def _row_time(row: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(row["at"])


def _months(first: str, last: str) -> list[str]:
    """月 `first` から `last` まで（両端を含む）。"""
    out = [first]
    while out[-1] < last:
        out.append(_month(_month_start(out[-1], 1)))
    return out


def _rule() -> dict[str, Any]:
    """表 launch-experiments の records の行（起動できない組の条件と記録を残す期間）。"""
    return facts.rows(TABLE, kind="records")[0]


def records_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """実験の記録を置く場所（使用量ログと同じ）。記録はこの下のリポジトリごとのディレクトリ（`repo_store`）に置く。

    決められなければ `LookupError` か `identity.IdentityError`。
    """
    env = os.environ if env is None else env
    location = facts.value("ndf-files", LEDGER, "location")
    var = facts.value("locations", location, "env")
    if var in env and env[var]:
        return Path(env[var])
    return identity.load().state_dir / facts.value("locations", location, "path")


def repo_store(base: Path, repo: str) -> Path:
    """リポジトリ `repo`（`repo_key` の値）の実験の記録のディレクトリ。名前はキーの SHA-256 の先頭 16 文字。

    割り付け・出現率・集計はどれも同じリポジトリの記録しか使わず、残す期間もリポジトリの設定で決まるので、リポジトリごとに分ける。
    """
    name = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
    return base / Path(facts.value("ndf-files", LEDGER, "path").replace(REPO, name)).parent


def _month_file(root: Path, month: str) -> Path:
    return root / Path(facts.value("ndf-files", LEDGER, "path")).name.replace(MONTH, month)


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    """リポジトリのディレクトリのロック `path` を、保存先の共有ロック（`launch_store.using`）の中で取る。回収と重ならない。"""
    limit = limits.seconds(LOCK_LIMIT)
    with launch_store.using(path.parent.parent, limit):
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path), timeout=limit):
            yield


def read_experiments(since: datetime, root: Path) -> list[dict[str, Any]]:
    """`since` の月から今月までの実験の記録（ロックを取らない）。壊れた行と書きかけの行は飛ばす。"""
    return [r for m in _months(_month(since), _month(_utc_now())) for r in launch_records.whole_rows(_month_file(root, m))]


@dataclass
class Ledger:
    """ロックの中の実験の記録。`rows` は読んだ行と、この中で足した行。"""

    root: Path
    rows: list[dict[str, Any]]

    def append(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """行の形を確かめて、行の日時の月のファイルへ足す（書きかけの末尾は閉じてから足す）。形が違えば `InternalError`。"""
        out = internal.write_record("experiment", row)
        launch_records.write_row(_month_file(self.root, out["at"][:7]), out)
        self.rows.append(out)
        return out


@contextmanager
def ledger(since: datetime, root: Path) -> Iterator[Ledger]:
    """ロックの外で `since` の月から読み、ロックを取ってからは追記された分（月が替われば新しい月のファイル）だけを読み足して渡す。

    無い月は空として読む。ある月を読めなければ `OSError` を送る（読めない月を空とみなすと、その月の割り付けを見失って同じ単位に
    別の組を割り付けるため）。
    """
    months = _months(_month(since), _month(_utc_now()))
    rows = [r for m in months[:-1] for r in launch_records.read_rows(_month_file(root, m), 0)[0]]
    last, offset = launch_records.read_rows(_month_file(root, months[-1]), 0)
    rows += last
    with _lock(_store_file(root, LEDGER_LOCK)):
        rows += launch_records.read_rows(_month_file(root, months[-1]), offset)[0]
        for m in _months(months[-1], _month(_utc_now()))[1:]:
            rows += launch_records.read_rows(_month_file(root, m), 0)[0]
        yield Ledger(root, rows)


def append_row(row: Mapping[str, Any], root: Path) -> bool:
    """`ledger` を開いて 1 行足す。書けなければ警告を出して偽。"""
    try:
        with ledger(_utc_now(), root) as led:
            led.append(row)
    except _STORE_ERRORS as exc:
        log.warning("実験の記録へ %s の行を書けない: %s", row["kind"], exc)
        return False
    return True


def prune(now: datetime, window_days: int, root: Path) -> None:
    """リポジトリのディレクトリ `root`（`repo_store`）の月の掃除の後に、保存先全体の片付け（`launch_store.sweep`）を呼ぶ。失敗は呼び手へ送る。

    残す日数は、`window_days`（今の設定）と、`root` に残る割り付けの行の `window_days` の最大値に `kept_extra_days` を足したもの。
    読み取りと削除は記録のロックの中で行い、読めない月があれば何も消さない。掃除の錠を待たずに取れた起動だけが掃除し、全部成功したら
    `launch-pruned` に今月を書く。片付けの済みは `launch-swept` が別に持つので、片付けが済まなくても自分の掃除はやり直さない。
    """
    if not _pruned(_store_file(root, PRUNED), _month(now)):
        with launch_store.using(root.parent, limits.seconds(LOCK_LIMIT)):  # 回収と重ならないよう、掃除の錠もこの中で取る
            if not _prune_own(root, now, window_days):
                return  # ほかの起動が掃除と片付けをしている。失敗すれば次の起動がやり直す
    launch_store.sweep(root.parent, now, root, now - timedelta(days=WINDOW_LIMIT + _rule()["kept_extra_days"]))


def _prune_own(root: Path, now: datetime, window_days: int) -> bool:
    """掃除の錠を待たずに取り、まだ今月でなければ掃除して `launch-pruned` に今月を書く。錠を取れなければ偽。

    掃除の中のロックの待ちの超過は、錠の取り合いでないので呼び手へ送る。
    """
    done, month = _store_file(root, PRUNED), _month(now)
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_store_file(root, PRUNED_LOCK)), timeout=0)
    if not launch_store.locked_now(lock):
        return False
    try:
        if not _pruned(done, month):
            _prune_now(root, now, window_days)
            _write_replacing(done, month)
    finally:
        lock.release()
    return True


def _prune_now(root: Path, now: datetime, window_days: int) -> None:
    with _lock(_store_file(root, LEDGER_LOCK)):
        rows = [r for p in _month_files(root).values() for r in launch_records.read_rows(p, 0)[0]]  # 読めない月は OSError
        days = max([window_days] + [r["window_days"] for r in rows if r["kind"] == "assign" and "window_days" in r])
        _drop_months(root, now - timedelta(days=days + _rule()["kept_extra_days"]))
    with _lock(_store_file(root, WEIGHTS_LOCK)):
        _prune_weights(root, now)


# --- 割り付け ----------------------------------------------------------------------------------------


def compatibility_key(arms: Sequence[Mapping[str, str]], metric: Optional[str]) -> str:
    """互換性のキー: 選択肢の並び（先頭が基準）と指標の名前とバージョンの、キーの順にそろえた JSON の SHA-256。"""
    return launch_records.canonical_sha({"arms": [dict(a) for a in arms], "metric": metric})


def metric_of(stage: str) -> Optional[str]:
    """工程の効果指標の名前とバージョン（`<名前>/<バージョン>`）。指標の無い工程は None（実験しない）。"""
    row = facts.row(TABLE, stage)
    return f"{row['metric']}/{row['metric_version']}" if "metric" in row else None


def _arm_text(arm: Mapping[str, str]) -> str:
    return json.dumps(dict(arm), sort_keys=True)


def _assigns(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {r["assign_id"]: r for r in rows if r["kind"] == "assign"}


@dataclass(frozen=True)
class Where:
    """割り付けの行と出現率の項目を照らすキー（リポジトリ・工程・ランタイム・互換性のキー）。B2 の出現率の計算も使う。"""

    repo: str
    stage: str
    runtime: str
    key: str

    def of(self, row: Optional[Mapping[str, Any]]) -> bool:
        """`row`（割り付けの行か出現率の項目）が同じキーを持つか。"""
        return row is not None and all(row[k] == v for k, v in vars(self).items())

    def at(self, row: Mapping[str, Any]) -> bool:
        """`row`（割り付けの行か停止の行）が同じリポジトリ・工程・ランタイムか（互換性のキーは見ない）。

        単位の ID は呼び出し元が付けるので、別のリポジトリ・工程・ランタイムが同じ名前を使っても混ぜない。
        """
        return (row["repo"], row["stage"], row["runtime"]) == (self.repo, self.stage, self.runtime)

    def fields(self) -> dict[str, str]:
        """停止の行に書く範囲の欄。"""
        return {"repo": self.repo, "stage": self.stage, "runtime": self.runtime}


def candidates(
    arms: Sequence[Mapping[str, str]], rows: Sequence[Mapping[str, Any]], entry: Optional[Mapping[str, Any]], now: datetime, where: Where
) -> list[int]:
    """打ち切った組（出現率の項目の期限の前）と起動できない組を除いた添字。基準の組（添字 0）は除かない。

    起動できない組は、直近 `refusal_days` 日の `launch_error` の行のうち、割り付け ID で引いた割り付けの行がリポジトリ・工程・
    ランタイム・互換性のキーで今と同じで、組が同じものが `refusals` 件以上ある組。割り付けの行を引けない拒否は数えない。
    """
    rule, assigns = _rule(), _assigns(rows)
    since = now - timedelta(days=rule["refusal_days"])
    refused = Counter(
        _arm_text(r["arm"]) for r in rows if r["kind"] == "launch_error" and _row_time(r) >= since and where.of(assigns.get(r["assign_id"]))
    )
    cutoffs = entry["cutoffs"] if entry is not None and "cutoffs" in entry else []
    cut = {c["arm"] for c in cutoffs if now < datetime.fromisoformat(c["until"])}
    return [i for i, arm in enumerate(arms) if i == 0 or (i not in cut and refused[_arm_text(arm)] < rule["refusals"])]


@dataclass(frozen=True)
class _Unit:
    """割り付ける単位と、そのときの設定。"""

    unit: Optional[str]
    mode: Optional[str]
    arms: tuple[Mapping[str, str], ...]
    allocation: Mapping[str, Any]
    experiment: bool
    enabled: bool
    where: Where
    now: datetime
    store: Path

    @property
    def start(self) -> datetime:
        return self.now - timedelta(days=self.allocation["window_days"])

    @property
    def since(self) -> datetime:
        """読む月の始まり（期間と、起動できない組を数える日数の長いほう）。"""
        return self.now - timedelta(days=max(self.allocation["window_days"], _rule()["refusal_days"]))


def _weights_entry(u: _Unit) -> Optional[dict[str, Any]]:
    """出現率のファイルの、同じリポジトリ・工程・ランタイム・互換性のキーの項目。無いか読めなければ None。"""
    path = _store_file(u.store, WEIGHTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        found = [e for e in data["entries"] if u.where.of(e) and not _past(e, u.now)]  # 掃除の後に期限を過ぎた項目も使わない
    except (OSError, ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 無いか壊れた出現率のファイルは項目なし（均等に抽選する）
        return None
    return found[-1] if found else None


def _model_group(arm: Mapping[str, str], arms: Sequence[Mapping[str, str]]) -> str:
    """実際のモデルのまとまり: モデルを指定しない組すべては `default`、指定する組は添字。"""
    return "default" if "model" not in arm else str(list(arms).index(arm)) if arm in arms else "?"


def _models_agree(entry: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], u: _Unit) -> bool:
    """項目の実際のモデルが、期間の中の同じキーの最も新しい `observed` の行と、まとまりごとに食い違わないか。

    最も新しい行は記録の順でなく行の日時で選ぶ。並行する起動の観測は、ロックの待ちで日時と逆の順に記録されることがある。
    """
    assigns = _assigns(rows)
    latest: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        if r["kind"] == "observed" and _row_time(r) >= u.start and u.where.of(assigns.get(r["assign_id"])):
            group = _model_group(r["arm"], u.arms)
            if group not in latest or _row_time(r) >= _row_time(latest[group]):
                latest[group] = r
    models = entry["models"] if "models" in entry else {}
    return all(latest[g]["model"] == m for g, m in models.items() if g in latest)


def _has_unpriced(rows: Sequence[Mapping[str, Any]], u: _Unit) -> bool:
    """直近 `window_days` 日に割り付けた、同じキーの単位に `unpriced` の行があるか。"""
    assigns = _assigns(rows)
    return any(
        r["kind"] == "unpriced" and u.where.of(a := assigns.get(r["assign_id"])) and a is not None and _row_time(a) >= u.start for r in rows
    )


def _drawn(probs: Sequence[float]) -> int:
    x, total = _random_value(), 0.0
    for i, p in enumerate(probs):
        total += p
        if p > 0 and x < total:
            return i
    return max(i for i, p in enumerate(probs) if p > 0)  # 合計の丸めで 1 に届かないとき


def _assignment(rows: Sequence[Mapping[str, Any]], u: _Unit) -> dict[str, Any]:
    """候補を決めて抽選した割り付けの行。調整した出現率を使えればその確率、使えなければ候補で均等。"""
    entry = _weights_entry(u)
    if entry is not None and not (entry.get("allocation") == dict(u.allocation) and _models_agree(entry, rows, u)):
        entry = None  # 割り付けの値か実際のモデルが違う項目は、打ち切りも確率も読まない
    w = u.where
    cands = candidates(u.arms, rows, entry, u.now, w)
    unpriced = _has_unpriced(rows, u)
    if entry is not None and entry.get("phase") == "adaptive" and entry.get("candidates") == cands and not unpriced:
        probs, phase = list(entry["probs"]), "adaptive"
    else:
        probs, phase = [1 / len(cands) if i in cands else 0 for i in range(len(u.arms))], "uniform" if unpriced else "burn_in"
    row = {
        "kind": "assign",
        "at": _time_text(u.now),
        "assign_id": uuid.uuid4().hex,
        "unit": u.unit,
        "stage": w.stage,
        "runtime": w.runtime,
        "repo": w.repo,
        "mode": u.mode,
        "arms": [dict(a) for a in u.arms],
        "probs": probs,
        "chosen": _drawn(probs),
        "key": w.key,
        "phase": phase,
        "window_days": u.allocation["window_days"],
    }
    return {k: v for k, v in row.items() if v is not None}


def _halted(r: Mapping[str, Any], unit: str, where: Where) -> bool:
    return r["kind"] == "halted" and r["unit"] == unit and where.at(r)


def _halt(led: Ledger, u_now: datetime, start: datetime, unit: str, where: Where, assign_id: Optional[str], reason: Halt) -> None:
    """停止の行を足す。期間の中に、同じリポジトリ・工程・ランタイムの同じ単位の同じ理由の行があれば足さない。"""
    if any(_halted(r, unit, where) and r["reason"] == reason and _row_time(r) >= start for r in led.rows):
        return
    row = {"kind": "halted", "at": _time_text(u_now), "unit": unit, **where.fields(), "assign_id": assign_id, "reason": str(reason)}
    led.append(row)


def _own(rows: Sequence[Mapping[str, Any]], u: _Unit) -> list[Mapping[str, Any]]:
    """期間の中の、同じリポジトリ・工程・ランタイムのその単位の割り付けの行（古い順）。"""
    return [r for r in rows if r["kind"] == "assign" and r["unit"] == u.unit and u.where.at(r) and _row_time(r) >= u.start]


def _left_experiment(rows: Sequence[Mapping[str, Any]], u: _Unit, own: Sequence[Mapping[str, Any]]) -> bool:
    """単位に、期間の中の停止の行（同じリポジトリ・工程・ランタイム）か、基準の組への切り替えの行があるか。"""
    ids = {a["assign_id"] for a in own}
    return any(
        _row_time(r) >= u.start
        and (_halted(r, u.unit, u.where) or (r["kind"] == "launch_error" and r["switched"] and r["assign_id"] in ids))
        for r in rows
    )


def _from_arm(arm: Mapping[str, str], key: str) -> tuple[Optional[str], Source]:
    value = arm[key] if key in arm else None
    return value, (Source.EXPERIMENT if value is not None else Source.DEFAULT)


def _with_assignment(choice: Choice, row: Mapping[str, Any]) -> Choice:
    arm = row["arms"][row["chosen"]]
    (model, ms), (effort, es) = _from_arm(arm, "model"), _from_arm(arm, "effort")
    return replace(choice, model=model, effort=effort, model_source=ms, effort_source=es, assign_id=row["assign_id"], arm=arm)


def _decide_assignment(led: Ledger, u: _Unit, choice: Choice) -> Choice:
    """設計書の「割り付けの流れ」の 2〜5（停止と固定・停止か切り替えの記録・既にある割り付け・抽選）。"""
    own = _own(led.rows, u)
    last = own[-1] if own else None
    if not u.experiment:
        if last is not None:
            _halt(led, u.now, u.start, u.unit, u.where, last["assign_id"], Halt.FIXED if u.enabled else Halt.DISABLED)
        return choice
    if _left_experiment(led.rows, u, own):
        return choice
    if last is not None:
        if last["key"] == u.where.key:
            return _with_assignment(choice, last)
        _halt(led, u.now, u.start, u.unit, u.where, last["assign_id"], Halt.CHANGED)
        return choice
    return _with_assignment(choice, led.append(_assignment(led.rows, u)))


def _allocated(choice: Choice, u: _Unit) -> Choice:
    """記録のロックの中で割り付ける。記録を読み書きできなければ警告を出し、実験の対象なら指定なし（割り付け ID なし）で続ける。"""
    try:
        with ledger(u.since, u.store) as led:
            return _decide_assignment(led, u, choice)
    except _STORE_ERRORS as exc:
        log.warning("実験の記録を読み書きできないので、割り付けずに起動する（単位 %s）: %s", u.unit, exc)
    if not u.experiment:
        return choice
    return replace(choice, model=None, effort=None, model_source=Source.DEFAULT, effort_source=Source.DEFAULT)


def _halt_manual(u: _Unit, charge_to: Optional[str]) -> None:
    """引数か環境変数の指定の起動: その単位と、費用を負う割り付けの単位に `manual` の停止の行を足す。"""
    try:
        with ledger(u.since, u.store) as led:
            if u.unit is not None:
                own = _own(led.rows, u)
                _halt(led, u.now, u.start, u.unit, u.where, own[-1]["assign_id"] if own else None, Halt.MANUAL)
            charged = _assigns(led.rows).get(charge_to) if charge_to is not None else None
            if charged is not None:
                at = Where(charged["repo"], charged["stage"], charged["runtime"], charged["key"])
                _halt(led, u.now, u.start, charged["unit"], at, charged["assign_id"], Halt.MANUAL)
    except _STORE_ERRORS as exc:
        log.warning("実験の記録へ手動の指定の停止を書けない（単位 %s）: %s", u.unit, exc)


def _prepared_store(env: Mapping[str, str], now: datetime, window_days: int, repo: str) -> Optional[Path]:
    """リポジトリ `repo` の記録のディレクトリを決め、月の掃除をする。決められなければ警告を出して None。掃除の失敗は警告だけ。"""
    try:
        store = repo_store(records_dir(env), repo)
    except (LookupError, identity.IdentityError) as exc:
        log.warning("実験の記録の保存先を決められないので、割り付けずに起動する: %s", exc)
        return None
    try:
        prune(now, window_days, store)
    except _STORE_ERRORS as exc:
        log.warning("実験の記録の月の掃除に失敗した（次の起動でやり直す）: %s", exc)
    return store


def base_fallback(choice: Choice, reason: str) -> Optional[Choice]:
    """割り付けた基準でない組の起動がモデルを理由に拒否されたとき、基準へ切り替えたことを持つ `launch_error` の行を書き、基準の組の値を返す。

    基準の組と割り付けの無い起動は None。行は割り付けと同じ保存先（`choice.store`）へ書き、書けなくても基準の値を返す。
    引き上げを求めた起動（`retry` がある）は、元の組が上限で値が変わらなかった場合も、基準の組から引き上げ直す。
    """
    if choice.assign_id is None or choice.store is None or choice.arm is None or choice.arm == choice.base_arm:
        return None
    row = {"kind": "launch_error", "at": _time_text(_utc_now()), "assign_id": choice.assign_id, "stage": choice.stage}
    append_row({**row, "runtime": choice.runtime, "arm": dict(choice.arm), "reason": reason, "switched": True}, choice.store)
    log.warning("組 %s は起動できなかったので、基準の組で続ける（工程 %s・単位 %s）", _arm_text(choice.arm), choice.stage, choice.unit)
    (model, ms), (effort, es) = _from_base(choice.base_arm, "model"), _from_base(choice.base_arm, "effort")
    back = replace(choice, model=model, effort=effort, model_source=ms, effort_source=es, assign_id=None, arm=None, raised=False)
    return _raised_once(back) if choice.retry is not None else back


def _raised_once(choice: Choice) -> Choice:
    up = raised(choice.runtime, choice.model, choice.effort)
    return replace(choice, model=up[0], effort=up[1], raised=True) if up != (choice.model, choice.effort) else choice


def launch_choice(
    layer: str, name: str, runtime: str = "claude", *, cwd: Path, environ: Optional[Mapping[str, str]] = None, **request: Any
) -> Choice:
    """優先の順で値を決め、要れば割り付け、`retry` があれば引き上げる。値の誤り・壊れた設定は `precondition`。

    `request` は `Request` の欄（`unit`・`charge_to`・`retry`・`mode`・`argument`）。起動のたびに最初に月の掃除をする。
    `unit` の無い起動と工程の無い起動は、実験の記録を読み書きしない。
    """
    req = Request(**request)
    env = os.environ if environ is None else environ
    stage = stage_of(layer, name)
    if req.retry is not None and req.retry not in _retries():
        raise _fail(f"打ち直しの理由 {req.retry} は無い（{'・'.join(sorted(_retries()))}）")
    root = launch_settings.launch_root(cwd)
    got = table(root)
    manual = _manual(runtime, req.argument, env)
    arms = got.arms[stage][runtime] if stage is not None and runtime in got.arms[stage] else ()
    base = arms[0] if arms else None
    now = _utc_now()
    repo = launch_settings.repo_key(root)
    store = _prepared_store(env, now, got.allocation["window_days"], repo)
    model, model_source = manual.model if manual.model is not None else _from_base(base, "model")
    effort, effort_source = manual.effort if manual.effort is not None else _from_base(base, "effort")
    if manual.inherited is not None:
        effort, effort_source = None, Source.ENVIRONMENT
    choice = Choice(
        layer,
        name,
        runtime,
        stage,
        req.mode,
        model,
        effort,
        model_source,
        effort_source,
        unit=req.unit,
        base_arm=base,
        inherited_effort=manual.inherited,
        retry=None if manual.given else req.retry,
        store=store,
    )
    if stage is None or store is None or (req.unit is None and req.charge_to is None):
        return choice if manual.given or req.retry is None or stage is None else _raised_once(choice)
    metric = metric_of(stage)
    where = Where(repo, stage, runtime, compatibility_key(arms, metric))
    experiment = got.enabled and len(arms) > 1 and metric is not None
    u = _Unit(req.unit, req.mode, tuple(arms), got.allocation, experiment, got.enabled, where, now, store)
    if manual.given:
        _halt_manual(u, req.charge_to)
        return choice
    if req.unit is not None:
        choice = _allocated(choice, u)
    return choice if req.retry is None else _raised_once(choice)
