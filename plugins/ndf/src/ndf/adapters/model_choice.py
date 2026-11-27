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

実験の割り付け（実験の記録・既にある割り付け・抽選）はまだ行わず、単位があっても基準の組を返す（#2944 のレビューの単位 B1 の後半で足す）。
"""

from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf import contracts, identity
from ndf.adapters import launch_settings
from ndf.contracts import facts, internal

# 同梱の表
TABLE = "launch-experiments"
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
    `--effort` を足さない）。
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
        if "effort" in arm and arm["effort"] not in efforts:  # 値は載せない（誤って入れた秘密を標準エラーへ漏らさない）
            errs.append(f"{at}/{i}/effort: 順位に無い値（使える値は {'・'.join(efforts)}）")
        if "model" in arm and (not arm["model"] or _NEWLINE.search(arm["model"])):
            errs.append(f"{at}/{i}/model: 空か改行を含む")
    return errs


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
    if not 1 <= days <= 365:
        errs.append(f"{at}/window_days: 1 以上 365 以下")
    return errs


def bundled_errors() -> list[str]:
    """同梱の値が起動の設定の形（スキーマ）に合わない箇所。同梱の値はリリースの後に変わらないので、起動のたびには照らさず CI のテストが呼ぶ。"""
    return contracts.broken_places({"version": 1, "experiments": _bundled()}, launch_settings.SCHEMA)


def table(root: Path, settings: Optional[launch_settings.Settings] = None) -> Table:
    """同梱の表とリポジトリ `root` の `experiments` を重ね、値の制約を確かめる。外れれば直す場所を載せて `precondition`。

    `settings`（`root` の起動の設定を読んだもの）を渡されれば読み直さずに使う。
    """
    if settings is None:
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
    if value not in efforts:  # 値は載せない（誤って入れた秘密を標準エラーへ漏らさない）
        raise _fail(f"{where} の effort は {runtime} の順位に無い値（使える値は {'・'.join(efforts)}）")


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


def launch_choice(
    layer: str,
    name: str,
    runtime: str = "claude",
    *,
    cwd: Path,
    environ: Optional[Mapping[str, str]] = None,
    settings: Optional[launch_settings.Settings] = None,
    **request: Any,
) -> Choice:
    """優先の順で値を決め、`retry` があれば引き上げる。値の誤り・壊れた設定は `precondition`。

    `request` は `Request` の欄（`unit`・`charge_to`・`retry`・`mode`・`argument`）。今は割り付けず、単位があっても基準の組を返す。
    `settings` は `table` へ渡す（渡されれば起動の設定を読み直さない）。
    """
    req = Request(**request)
    env = os.environ if environ is None else environ
    stage = stage_of(layer, name)
    if req.retry is not None and req.retry not in _retries():
        raise _fail(f"打ち直しの理由 {req.retry} は無い（{'・'.join(sorted(_retries()))}）")
    got = table(launch_settings.launch_root(cwd), settings)
    manual = _manual(runtime, req.argument, env)
    arms = got.arms[stage][runtime] if stage is not None and runtime in got.arms[stage] else ()
    base = arms[0] if arms else None
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
    )
    if req.retry is None or manual.given or stage is None:
        return choice
    up = raised(runtime, model, effort)
    return replace(choice, model=up[0], effort=up[1], raised=True) if up != (model, effort) else choice
