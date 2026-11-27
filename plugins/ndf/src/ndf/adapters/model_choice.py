"""起動のモデルと effort を選ぶ（#2944 の「値を選ぶ処理」）。

起動する所は、層（`worker`・`skill`・`judge`・`seat`）・作業の名前・ランタイムを渡し、値と出所（`Choice`）を受け取る。
選択肢は同梱の表（`contracts/domains/launch-experiments.json`）に、リポジトリの起動の設定（`.ndf/launch.json`）の
`experiments` を重ねたもの（`table`）。重ねた後の値が形や制約に合わなければ、使わない値でも `precondition` で止める
（黙って別の値で起動しない。受け入れ条件 14）。

モデルと effort は別々に、上から最初に値があるものを使う（設計書の「値の優先」）。

1. 起動の引数（`argument`）
2. 一時的な上書きの環境変数（`NDF_LAUNCH_*`。名前は表 launch-experiments の order の行が表 env のキーで持つ）
3. ランタイム自身の effort の環境変数（claude の `CLAUDE_CODE_EFFORT_LEVEL`。値は検証せず、子へ引き継ぐ）
4. 工程の選択肢（基準の組は先頭）
5. 指定なし（CLI の既定）

1〜3 で片側でも指定があれば、その起動は割り付けず、指定の無い側は基準の組の値（出所 `base`。組が値を持たなければ `default`）。
`retry`（同じ失敗への 2 回目以降の起動の理由）があれば、決まった組に `raised` を 1 回だけ当てる。引数と環境変数の値は上げない。

この版の `pick` は、実験の割り付け（実験の記録・既にある割り付け・抽選）をまだ行わず、単位があっても基準の組を返す。
"""

from __future__ import annotations

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
# 行の種類（表の列 kind）
STAGE_ROW = "stage"
ORDER_ROW = "order"
DEFAULTS_ROW = "defaults"
# 値の引数の形の列（表 runtimes）
ARGS_COLUMN = "choice_args"
# 値の出所（使用量ログの model_source・effort_source）
ARGUMENT = "argument"
ENVIRONMENT = "environment"
EXPERIMENT = "experiment"
BASE = "base"
DEFAULT = "default"
# 選択肢の 1 組のキー
MODEL = "model"
EFFORT = "effort"
# 引数の形の値に入れる位置（`{model}`・`{effort}`）
_PLACE = re.compile(r"\{(model|effort)\}")
# 引き上げの表の境の effort（これより低い effort は effort を上げ、これ自身は 1 つ上の effort へ上げる）
HIGH = "high"
# 改行（モデル名に含むと引数の 1 行 1 語の形が壊れる）
_NEWLINE = re.compile(r"[\r\n]")


def _fail(detail: str) -> internal.InternalError:
    return internal.InternalError("precondition", detail)


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
    model_source: str
    effort_source: str
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
        values = {MODEL: self.model, EFFORT: self.effort}
        out: list[str] = []
        for flag, form in zip(words[::2], words[1::2]):
            place = _PLACE.search(form)
            value = values[place.group(1)] if place is not None else None
            if value is not None:
                out += [flag, _PLACE.sub(value, form)]
        return out

    def env_changes(self) -> dict[str, Optional[str]]:
        """子の環境の変更。ランタイム自身の effort の変数を、引き継ぐなら値、外すなら None にする。変えなければ空。"""
        key = _order(self.runtime).get("inherited_effort_env")
        if key is None:
            return {}
        name = identity.env_name(key)
        if self.inherited_effort is not None:
            return {name: self.inherited_effort}
        if self.effort_source in (ARGUMENT, ENVIRONMENT):
            return {name: None}
        return {}

    def row_fields(self) -> dict[str, Any]:
        """使用量ログの行に足す欄（値の無い欄は置かない）。"""
        fields: dict[str, Any] = {
            "layer": self.layer,
            "name": self.name,
            "runtime": self.runtime,
            "mode": self.mode,
            "unit": self.unit,
            "assign_id": self.assign_id,
            "requested_model": self.model,
            "effort": self.effort,
            "model_source": self.model_source,
            "effort_source": self.effort_source,
            "raised": self.raised,
        }
        return {k: v for k, v in fields.items() if v is not None}


def _bundled_path() -> Path:
    return contracts.contracts_dir() / facts.location(TABLE)


def _rows(kind: str) -> tuple[dict[str, Any], ...]:
    return facts.rows(TABLE, kind=kind)


def _order(runtime: str) -> dict[str, Any]:
    """ランタイムの順位の行。無いランタイムは `precondition`。"""
    found = {r["id"]: r for r in _rows(ORDER_ROW)}
    if runtime not in found:
        raise _fail(f"ランタイム {runtime} は値を選べない（選べるのは {'・'.join(found)}。{_bundled_path()}）")
    return found[runtime]


def _launches() -> dict[tuple[str, str], str]:
    """(層, 作業の名前) → 工程。"""
    return {(layer, name): r["id"] for r in _rows(STAGE_ROW) for layer, names in r["launches"].items() for name in names}


def stage_of(layer: str, name: str) -> Optional[str]:
    """層と作業の名前から工程を引く。表に無い組は None（実験の引数を足さない）。どの工程にも無い層は `precondition`。"""
    launches = _launches()
    layers = sorted({lay for lay, _ in launches})
    if layer not in layers:
        raise _fail(f"層 {layer} は無い（層は {'・'.join(layers)}）")
    return launches.get((layer, name))


def _retries() -> set[str]:
    return {reason for r in _rows(STAGE_ROW) if "retries" in r for reason in r["retries"]}


def _bundled() -> dict[str, Any]:
    """同梱の値を起動の設定の `experiments` と同じ形で返す。"""
    defaults = _rows(DEFAULTS_ROW)
    if len(defaults) != 1:
        raise _fail(f"{_bundled_path()} の defaults の行が {len(defaults)} 行ある（1 行だけ要る）")
    stages = {r["id"]: {"arms": r["arms"]} for r in _rows(STAGE_ROW)}
    return {"enabled": defaults[0]["enabled"], "stages": stages, "allocation": defaults[0]["allocation"]}


def _given_keys(given: Mapping[str, Any], bundled: Mapping[str, Any], where: Path) -> None:
    """リポジトリが同梱に無い工程・ランタイムを書いていないか。"""
    for stage, spec in given.get("stages", {}).items():
        if stage not in bundled["stages"]:
            raise _fail(f"{where} の experiments/stages/{stage} は無い工程（工程は {'・'.join(bundled['stages'])}）")
        known = bundled["stages"][stage]["arms"]
        for runtime in spec["arms"]:
            if runtime not in known:
                raise _fail(f"{where} の experiments/stages/{stage}/arms/{runtime} は、この工程に無いランタイム（{'・'.join(known)}）")


def _overlay(bundled: Mapping[str, Any], given: Mapping[str, Any]) -> dict[str, Any]:
    arms = {stage: dict(spec["arms"]) for stage, spec in bundled["stages"].items()}
    for stage, spec in given.get("stages", {}).items():
        arms[stage].update(spec["arms"])
    return {
        "enabled": given.get("enabled", bundled["enabled"]),
        "allocation": {**bundled["allocation"], **given.get("allocation", {})},
        "arms": arms,
    }


def _arms_errors(stage: str, runtime: str, arms: list[Any]) -> list[str]:
    """選択肢の並びの値の制約の誤り（`<キーの位置>: <理由>`）。"""
    at = f"experiments/stages/{stage}/arms/{runtime}"
    if not arms:
        return [f"{at}: 選択肢が空（1 組以上要る）"]
    efforts = _order(runtime)["efforts"]
    errs: list[str] = []
    for i, arm in enumerate(arms):
        if arm in arms[:i]:
            errs.append(f"{at}/{i}: 同じ組を 2 回書いている")
        if EFFORT in arm and arm[EFFORT] not in efforts:
            errs.append(f"{at}/{i}/effort: {arm[EFFORT]} は順位に無い（{'・'.join(efforts)}）")
        if MODEL in arm and (not arm[MODEL] or _NEWLINE.search(arm[MODEL])):
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


def table(root: Path) -> Table:
    """同梱の表とリポジトリ `root` の `experiments` を重ね、値の制約を確かめる。外れれば直す場所を載せて `precondition`。"""
    settings = launch_settings.load(root)
    given = settings.experiments
    bundled = _bundled()
    broken = contracts.broken_places({"version": 1, "experiments": bundled}, launch_settings.SCHEMA)
    if broken:
        raise _fail(f"{_bundled_path()} の選択肢が起動の設定の形に合わない: {'; '.join(broken)}")
    _given_keys(given, bundled, settings.path)
    merged = _overlay(bundled, given)
    errs = [e for stage, by in merged["arms"].items() for runtime, arms in by.items() for e in _arms_errors(stage, runtime, arms)]
    widest = max(len(arms) for by in merged["arms"].values() for arms in by.values())
    errs += _allocation_errors(merged["allocation"], widest)
    if errs:
        raise _fail(f"モデルと effort の選択肢の値が制約に合わない（{settings.path} か同梱の {_bundled_path()}）: {'; '.join(errs)}")
    arms = {stage: {runtime: tuple(a) for runtime, a in by.items()} for stage, by in merged["arms"].items()}
    return Table(merged["enabled"], merged["allocation"], arms)


def raised(runtime: str, model: Optional[str], effort: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """設計書の「引き上げる起動」の上げ方を 1 行だけ当てた (モデル, effort)。当たらなければ元の値。

    effort が `high` より低ければ effort を 1 つ上げる。モデルの順位を持つランタイムで、最上位でない順位のモデルは、effort が
    `high` 以上か指定なしならモデルを 1 つ上げる。ほかは effort が `high` なら 1 つ上げ（`xhigh`）、それ以外は変えない。
    """
    order = _order(runtime)
    efforts: list[str] = order["efforts"]
    models: list[str] = order["models"] if "models" in order else []
    high = efforts.index(HIGH)
    at = efforts.index(effort) if effort in efforts else None
    if at is not None and at < high:
        return model, efforts[at + 1]
    if model in models[:-1]:
        return models[models.index(model) + 1], effort
    if at == high:
        return model, efforts[at + 1]
    return model, effort


def _given_effort(value: Optional[str], runtime: str, where: str) -> Optional[str]:
    if not value:
        return None
    efforts = _order(runtime)["efforts"]
    if value not in efforts:
        raise _fail(f"{where} の effort {value} は {runtime} の順位に無い（{'・'.join(efforts)}）")
    return value


def _given_model(value: Optional[str], where: str) -> Optional[str]:
    if not value:
        return None
    if _NEWLINE.search(value):
        raise _fail(f"{where} のモデル名が改行を含む")
    return value


def _manual(
    runtime: str, argument: tuple[Optional[str], Optional[str]], environ: Mapping[str, str]
) -> tuple[Optional[tuple[str, str]], Optional[tuple[str, str]], Optional[str]]:
    """引数と環境変数の指定。(モデルと出所, effort と出所, 引き継ぐ effort の変数の値)。値の誤りは `precondition`。"""
    order = _order(runtime)
    model_env, effort_env = identity.env_name(order["model_env"]), identity.env_name(order["effort_env"])
    env_model = _given_model(environ.get(model_env), f"環境変数 {model_env}")
    env_effort = _given_effort(environ.get(effort_env), runtime, f"環境変数 {effort_env}")
    arg_model = _given_model(argument[0], "引数 --model")
    arg_effort = _given_effort(argument[1], runtime, "引数 --effort")
    model = (arg_model, ARGUMENT) if arg_model else ((env_model, ENVIRONMENT) if env_model else None)
    effort = (arg_effort, ARGUMENT) if arg_effort else ((env_effort, ENVIRONMENT) if env_effort else None)
    inherited = None
    if effort is None and "inherited_effort_env" in order:
        inherited = environ.get(identity.env_name(order["inherited_effort_env"])) or None
    return model, effort, inherited


def _from_base(base: Optional[Mapping[str, str]], key: str) -> tuple[Optional[str], str]:
    value = base.get(key) if base is not None else None
    return value, (BASE if value is not None else DEFAULT)


def pick(
    layer: str,
    name: str,
    runtime: str = "claude",
    *,
    cwd: Path,
    unit: Optional[str] = None,
    charge_to: Optional[str] = None,
    retry: Optional[str] = None,
    mode: Optional[str] = None,
    argument: tuple[Optional[str], Optional[str]] = (None, None),
    environ: Optional[Mapping[str, str]] = None,
) -> Choice:
    """優先の順で値を決め、`retry` があれば引き上げる。値の誤り・壊れた設定は `precondition`。

    `charge_to` は手戻りの費用を負う割り付け ID で、実験の記録を書く版が使う。この版は割り付けず、単位があっても基準の組を返す。
    """
    del charge_to  # 実験の記録（停止の行）を書く版で使う
    env = os.environ if environ is None else environ
    stage = stage_of(layer, name)
    if retry is not None and retry not in _retries():
        raise _fail(f"打ち直しの理由 {retry} は無い（{'・'.join(sorted(_retries()))}）")
    got = table(launch_settings.launch_root(cwd))
    model_given, effort_given, inherited = _manual(runtime, argument, env)
    arms = got.arms[stage].get(runtime, ()) if stage is not None else ()
    base = arms[0] if arms else None
    model, model_source = model_given if model_given else _from_base(base, MODEL)
    effort, effort_source = effort_given if effort_given else _from_base(base, EFFORT)
    if inherited is not None:
        effort, effort_source = None, ENVIRONMENT
    choice = Choice(layer, name, runtime, stage, mode, model, effort, model_source, effort_source, unit=unit, base_arm=base,
                    inherited_effort=inherited)
    manual = model_given is not None or effort_given is not None or inherited is not None
    if retry is None or manual or stage is None:
        return choice
    up = raised(runtime, model, effort)
    return replace(choice, model=up[0], effort=up[1], raised=True) if up != (model, effort) else choice
