"""worker の作業の定義（`contracts/worker/kinds.json`）を読み、作業ごとの入力と作業場所の欠けを返す（#3908 の決定 3）。

作業の名前の唯一の基準は `kinds.json` のキーである。`progress.invoke`（工程がプランを組む層）と `progress.gate.entry`
（`ndf answer work`）の両方から読むので、どちらも読まずに `ndf.contracts` だけを読む。差し込みの出所の文
（`invoke.FILL_SOURCES`）は呼び手が `sources` で渡す。

作業が読む値の受け渡し（工程が組んだ差し込みの値を残す `values.json`。#3908 の決定 2）の読み書きもここに置く。書くのは
工程（`invoke.prepare_plan`）、読むのは工程の描画（`invoke._prepared`）と `ndf answer work` で、どちらも保存先を記述子で
開き、共有の保存先で別の利用者が置いた・書き換えられる値を使わない。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import worker_paths

KINDS_FILE = "worker/kinds.json"
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
    got = kind_values(kind, values, inputs)
    spared = {n for name, names in covers.items() if _filled(got.get(name)) for n in names}
    gaps = {
        n: sources[n] if n in sources else f"工程の入力 {n}（工程の定義の metadata.ndf.inputs）"
        for n in work_kinds()[kind]["inputs"]
        if not _filled(got.get(n)) and n not in spared
    }
    if work_dir_of(kind, values) is None:
        cwd = work_kinds()[kind]["cwd"]
        gaps[cwd] = f"作業 {kind} の作業場所（差し込み {cwd}）"
    return gaps


# プランを組んだ差し込みの値を、プランの実行の保存先に残すファイル（#3908 の決定 2）
VALUES_FILE = "values.json"
# values.json のキー → 値の型（形が違えば読まなかったものとして扱う）
VALUES_SHAPE = {"plan": str, "values": dict, "inputs": dict, "local_tests": bool, "at": str}


def write_values(state_dir: Path, plan: str, values: Mapping[str, Any], inputs: Mapping[str, Any], local_tests: bool) -> None:
    """プランの名前・差し込みの値・工程の入力・手元のテストを含めるか・書いた時刻を `<保存先>/values.json` へ書く。

    保存先は祖先のリンクを解きながら 1 回だけ開き（`worker_paths.open_resolved`。macOS の `/tmp`・`/var` も通る）、その記述子の
    直下の私有の一時ディレクトリへ排他的に作った新しいファイルに、グループとほかの利用者の書き込み権限を外してから中身を書き、
    `values.json` へ公開する（`worker_paths.write_file(private=True)`）。固定名の一時ファイルを開かず、公開した後にパスを引き
    直さないので、共有の保存先で名前をリンクへ差し替えられても、呼んだ利用者の別のファイルを切り詰めることもアクセス権を
    変えることも無い。公開したファイルは `read_values` と `ndf answer work` の `read_file(owned=True)` を通る。
    書けなければ `OSError`・`worker_paths.EntryError` をそのまま上げる（呼び手の `plan_failure` が precondition にする）。
    """
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    held = {"plan": plan, "values": dict(values), "inputs": dict(inputs), "local_tests": local_tests, "at": at}
    with worker_paths.open_resolved(state_dir) as wd:
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


def values_of(held: Any) -> Optional[dict[str, Any]]:
    """`values.json` の中身を読んだ値。辞書でない・キーか型が `VALUES_SHAPE` と違えば None。"""
    if not isinstance(held, dict) or any(not isinstance(held.get(k), t) for k, t in VALUES_SHAPE.items()):
        return None
    return held
