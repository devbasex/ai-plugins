"""worker の作業の定義（`contracts/worker/kinds.json`）を読み、作業ごとの入力と作業場所の欠けを返す（#3908 の決定 3）。

作業の名前の唯一の基準は `kinds.json` のキーである。`progress.invoke`（工程がプランを組む層）・`progress.gate.entry`
（`ndf answer work`）・`core.route`（単発のプランの描画）・`core.plan.runner`（プランの実行）から読むので、どれも読まずに
`ndf.contracts` だけを読む。差し込みの出所の文（`invoke.FILL_SOURCES`）は呼び手が `sources` で渡す。

作業が読む値の受け渡し（組んだ差し込みの値を残す `values.json`。#3908 の決定 2）の読み書きもここに置く。書くのは工程
（`invoke.prepare_plan`）と、単発のプランを流す `ndf plan next`（プランが持つ `PLAN_KEY` の値を、実行が選んだ保存先へ回ごとに
書く。PR 4319 の指摘 r2-2・r2-3）。読むのは工程の描画（`invoke._prepared`）と `ndf answer work` で、どちらも保存先を記述子で
開き、共有の保存先で別の利用者が置いた・書き換えられる値を使わない。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import internal, worker_paths

KINDS_FILE = "worker/kinds.json"
# 出したものを指す値の文脈のキー（作業の outputs・まとめたリリースの回答が書く。#4513）
PRODUCT_VERSION = "product_version"
# 届け先の文脈のキー（作業 release の outputs・オーガナイザーの承認の記録が書く。#4513）
DELIVERY = "delivery"
NO_SOURCES: Mapping[str, str] = MappingProxyType({})


@contracts.cached_reader
def work_kinds() -> dict[str, dict[str, Any]]:
    """作業の名前 → 定義（`work`・`summary`・`skills`・`excerpt`・`inputs`・`cwd`・`outputs`）。"""
    return contracts.load_json(KINDS_FILE)["kinds"]


def _filled(value: Any) -> bool:
    return value is not None and value not in ("", [])


def kind_values(kind: str, values: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """作業の定義の `inputs` の名前ごとの値。差し込みの値（`values`）にあればそれを、無ければ工程の入力（`inputs`）を取る。"""
    out: dict[str, Any] = {}
    for name in work_kinds()[kind]["inputs"]:
        if name in values:
            out[name] = values[name]
        elif name in inputs:
            out[name] = inputs[name]
    return out


def work_dir_of(kind: str, values: Mapping[str, Any]) -> Optional[str]:
    """作業の定義の `cwd`（`worktree` か `repo`）が指す差し込みの値。空か無ければ None。"""
    found = values.get(work_kinds()[kind]["cwd"])
    return found if isinstance(found, str) and found else None


NO_COVERS: Mapping[str, tuple[str, ...]] = MappingProxyType({})


def kind_gaps(
    kind: str,
    values: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    sources: Mapping[str, str] = NO_SOURCES,
    covers: Mapping[str, tuple[str, ...]] = NO_COVERS,
) -> dict[str, str]:
    """作業の定義の `inputs` と `cwd` が指す値のうち、どちらにも無い（または空の）名前 → 出所の人が読む文。そろえば空。

    出所は `sources`（差し込みの名前 → 出所の文。呼び手が `invoke.FILL_SOURCES` を渡す）にあればその文、無ければ工程の入力。
    `covers`（名前 → その値があれば要らない名前。呼び手が `route.LAUNCH_SOURCES` を渡す）の値がそろっていれば、
    その元の名前は欠けとしない（`acceptance` を明示した複数課題の単位は、単独の `issue` を持たなくてよい）。
    """
    spec = work_kinds()[kind]
    got = kind_values(kind, values, inputs)
    spared = {n for name, names in covers.items() if _filled(got.get(name)) for n in names}
    gaps = {
        n: sources[n] if n in sources else f"工程の入力 {n}（工程の定義の metadata.ndf.inputs）"
        for n in spec["inputs"]
        if not _filled(got.get(n)) and n not in spared
    }
    if work_dir_of(kind, values) is None:
        cwd = spec["cwd"]
        gaps[cwd] = f"作業 {kind} の作業場所（差し込み {cwd}）"
    return gaps


# プランを組んだ差し込みの値を、プランの実行の保存先に残すファイル（#3908 の決定 2）
VALUES_FILE = "values.json"
# values.json のキー → 値の型（形が違えば読まなかったものとして扱う）
VALUES_SHAPE = {"plan": str, "values": dict, "inputs": dict, "local_tests": bool, "at": str}


class Recorded(NamedTuple):
    """記録済みの値（#4513）。まとめたリリースでオーガナイザーが記録したリリースの識別子・届け先・承認の時点の保存先の種類。

    `delivery` は承認と一緒に記録した届け先（記録できなければ None）。`store` は承認を記録した時点のバージョンの保存先の種類
    （`none`・`versioned`・`unknown`。この変更の前に承認された記録は None）。
    """

    version: str
    delivery: Optional[dict[str, Any]]
    store: Optional[str]

    @property
    def without_number(self) -> bool:
        """承認の時点でバージョン番号を持たないリポジトリだったか（保存先の種類が `none`）。"""
        return self.store == "none"

    @property
    def store_unknown(self) -> bool:
        """承認の時点で設定を読めず、保存先の種類が決まらなかったか。"""
        return self.store == "unknown"

    @property
    def with_number(self) -> bool:
        """承認の時点でバージョン番号を持つリポジトリだったか。"""
        return self.store == "versioned"


# values.json に記録済みの値を残すキー（`VALUES_SHAPE` に足さない。直す前の NDF が書いた values.json も読む）
RELEASED_KEYS = ("released_version", "released_delivery", "released_store")


def store_kind_of(has_version_number: Optional[bool]) -> str:
    """承認を記録する時点のバージョンの保存先の種類。設定を読めなければ（None）`unknown`。"""
    if has_version_number is None:
        return "unknown"
    return "versioned" if has_version_number else "none"


def _released_fields(value: Optional[Recorded]) -> dict[str, Any]:
    if value is None:
        return {}
    fields: dict[str, Any] = {"released_version": value.version}
    if value.delivery is not None:
        fields["released_delivery"] = dict(value.delivery)
    if value.store is not None:
        fields["released_store"] = value.store
    return fields


def released_of(held: Mapping[str, Any]) -> Optional[Recorded]:
    """読んだ `values.json` の記録済みの値。`released_version` が空白だけでない文字列でなければ None。

    `released_delivery` がオブジェクトでなければ届け先は記録されていないものとし、`released_store` が 3 つの種類のどれでも
    なければ種類は記録されていないものとする（手で書き換えた値で worker の起動の判断を誤らない）。
    """
    version = held.get("released_version")
    if not (isinstance(version, str) and version.strip()):
        return None
    delivery = held.get("released_delivery")
    store = held.get("released_store")
    known = store if store in (store_kind_of(None), store_kind_of(True), store_kind_of(False)) else None
    return Recorded(version, dict(delivery) if isinstance(delivery, dict) else None, known)


def write_released(state_dir: Path, value: Optional[Recorded]) -> None:
    """既にある `values.json` の記録済みの値のキー（`RELEASED_KEYS`）だけを書き換える。ほかのキーは元のまま。

    `value` が None なら 3 つのキーを消す。書き方は `write_values` と同じ（開いた保存先の記述子を起点に、ほかの利用者が
    書き込めないファイルとして公開する）。`values.json` が無い・形が違えば `OSError`（呼び手の `plan_failure` が precondition にする）。
    """
    with worker_paths.open_resolved(state_dir) as wd:
        held = values_in(wd)
        if held is None:
            raise OSError(f"保存先 {state_dir} に {VALUES_FILE} が無いので記録済みの値を書けない")
        kept = {k: v for k, v in held.items() if k not in RELEASED_KEYS}
        body = {**kept, **_released_fields(value)}
        worker_paths.write_file(wd, VALUES_FILE, json.dumps(body, ensure_ascii=False), private=True)


def write_values(
    state_dir: Path,
    plan: str,
    values: Mapping[str, Any],
    inputs: Mapping[str, Any],
    local_tests: bool,
    released: Optional[Recorded] = None,
) -> None:
    """プランの名前・差し込みの値・工程の入力・手元のテストを含めるか・書いた時刻を `<保存先>/values.json` へ書く。

    保存先はあった要素までを祖先のリンクを解きながら確かめ（`worker_paths.resolve_existing`。macOS の `/tmp`・`/var` も通る）、
    無い残りの要素はリンクをたどらずに作って 1 回だけ開く（`worker_paths.open_workdir(create=True)`。解いた時点の観測と開いた
    記述子を突き合わせ、解いた後に現れた・差し替えられたディレクトリは `EntryUnsafe`（`replaced`）で止める）。その記述子の
    直下の私有の一時ディレクトリへ排他的に作った新しいファイルに、グループとほかの利用者の書き込み権限を外してから中身を書き、
    `values.json` へ公開する（`worker_paths.write_file(private=True)`）。固定名の一時ファイルを開かず、公開した後にパスを引き
    直さないので、共有の保存先で名前をリンクへ差し替えられても、呼んだ利用者の別のファイルを切り詰めることもアクセス権を
    変えることも無い。公開したファイルは `read_values` と `ndf answer work` の `read_file(owned=True)` を通る。
    書けなければ `OSError`・`worker_paths.EntryError` をそのまま上げる（呼び手の `plan_failure` が precondition にする）。
    `released` が None でなければ記録済みの値（`RELEASED_KEYS`）も同じ書き込みで書く（#4513）。
    """
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    held = {"plan": plan, "values": dict(values), "inputs": dict(inputs), "local_tests": local_tests, "at": at}
    held.update(_released_fields(released))
    with worker_paths.open_or_create(state_dir) as wd:
        worker_paths.write_file(wd, VALUES_FILE, json.dumps(held, ensure_ascii=False), private=True)


def read_values(state_dir: Path) -> Optional[dict[str, Any]]:
    """`<保存先>/values.json`。保存先かファイルが無い・壊れている・キーか型が `VALUES_SHAPE` と違えば None。

    `write_values` と同じく保存先を `open_resolved` で開き、`values_in` で読む（呼んだ利用者が持ち、ほかの利用者が書き込めない
    ファイルだけ。ほかは `worker_paths.EntryUnsafe`（`foreign`）を上げ、呼び手の `plan_failure` が precondition にする）。
    """
    try:
        with worker_paths.open_resolved(state_dir) as wd:
            return values_in(wd)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 保存先がまだ無ければ値も無い（`prepare_plan` が作って書く）
        return None


def values_in(wd: worker_paths.WorkDir) -> Optional[dict[str, Any]]:
    """開いた保存先の `values.json`。無い・壊れている・形が違えば None。

    呼んだ利用者が持ち、グループとほかの利用者が書き込めない（拡張 ACL を含む）リンク数 1 の通常のファイルだけを、開いた
    記述子から読み、ほかは `worker_paths.EntryUnsafe`（`foreign` など）で止める（共有の保存先で別の利用者が置いた値を、
    工程のプランの描画にも worker の指示と作業場所にも使わない）。工程（`read_values`）と `ndf answer work` が共有する。
    """
    text = worker_paths.read_file(wd, VALUES_FILE, owned=True)
    if text is None:
        return None
    try:
        return values_of(json.loads(text))
    except json.JSONDecodeError:  # noqa: NDF401 ファイル: 書きかけ・壊れた値は無いものとして扱う
        return None


def _has_shape(held: Any, shape: Mapping[str, type]) -> bool:
    """辞書で、`shape` のキーをどれもその型で持つか（ほかのキーは見ない）。"""
    return isinstance(held, dict) and all(isinstance(held.get(k), t) for k, t in shape.items())


def values_of(held: Any) -> Optional[dict[str, Any]]:
    """`values.json` の中身を読んだ値。辞書でない・キーか型が `VALUES_SHAPE` と違えば None。工程の入力の旧い名前は今の名前へ移す。"""
    if not _has_shape(held, VALUES_SHAPE):
        return None
    return {**held, "inputs": internal.stage_values(held["inputs"])}


# 単発のプラン（`ndf route render・run`）が既定の worker の入力を持つ、プランの最上位のキー（PR 4319 の指摘 r2-2・r2-3）
PLAN_KEY = "work_values"
# プランに持たせる入力の形（`VALUES_SHAPE` から書いた時刻を除いたもの。時刻は `ndf plan next` が書くときに足す）
PLAN_SHAPE = {k: t for k, t in VALUES_SHAPE.items() if k != "at"}


def plan_values(plan: str, values: Mapping[str, Any], local_tests: bool) -> dict[str, Any]:
    """単発のプランに `PLAN_KEY` で持たせる入力（単発のプランは工程の入力を持たないので `inputs` は空）。"""
    return {"plan": plan, "values": dict(values), "inputs": {}, "local_tests": local_tests}


def plan_values_error(raw: Mapping[str, Any]) -> Optional[str]:
    """プランの `PLAN_KEY` の形の誤り。無いか形が合えば None（`core.plan.model.parse` が `PlanError` にする）。"""
    if PLAN_KEY not in raw:
        return None
    if _has_shape(raw[PLAN_KEY], PLAN_SHAPE):
        return None
    return f"{PLAN_KEY} は {', '.join(PLAN_SHAPE)} を持つ辞書にする"


def hand_plan_values(state_dir: Path, raw: Mapping[str, Any]) -> bool:
    """プランが `PLAN_KEY` を持てば、その値を実行の保存先の `values.json` へ書いて真を返す。持たなければ何もせず偽。

    `ndf plan next` が回を始める・続けるたびに、実行が選んだ保存先（`--state-dir` を含む）へ、流すプランそのものが持つ値を
    書く。描画の時点の保存先へは書かないので、保存先を上書きして流しても worker が値を見つけ、プラン本体と値が別々に
    書き替わって食い違うことも無い。書けなければ `write_values` と同じく `OSError`・`worker_paths.EntryError` を上げる。
    """
    if PLAN_KEY not in raw:
        return False
    held = raw[PLAN_KEY]
    write_values(state_dir, held["plan"], held["values"], held["inputs"], held["local_tests"])
    return True
