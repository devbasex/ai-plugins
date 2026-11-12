"""課題のまとまり（#3081）: 並列の計画のスプリントのレーンの入力に書く `bundles` の形と検査（I1〜I3）・補い（I4）・
動いている計画のこのレーンのまとまりの読み出し（`bundles_of`）。

    {"kind": "sprint", "issues": [3001, 3004, 3010],
     "bundles": [{"name": "review-scope", "issues": [3004, 3001]}]}
    → まとまりは review-scope（代表は #3004）・issue-3010 の 2 つ

課題の集合の正本は `input.issues` で、`bundles` は課題をどのまとまりに入れるかの対応だけを持つ。欄を省いたレーンは
「まとまりの記録なし」（今の組み方）、空の配列は「すべての課題が 1 件のまとまり」である。検証の誤りの種類は `bundle` で、
`parallel_plan._validation_items` が `items[].rule` へ写す。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")  # まとまりの名前の形（英小文字・数字・`-`・`_`、32 字まで。I3）
ACTIVE = ("running", "lost")  # レーンを当てる計画の状態（`parallel_lane.lane_of` と同じ範囲）


class Bundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    issues: list[int] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise PydanticCustomError("bundle", "まとまりの名前の形が違う（{pattern}）: {name}", {"pattern": NAME_RE.pattern, "name": v})
        return v

    def lead(self) -> int:
        """代表の課題（先頭）。まとまりの設計文書と実装の Pull Request はこの番号で作る。"""
        return self.issues[0]


def single_name(n: int) -> str:
    """どのまとまりにも入っていない課題の 1 件のまとまりの名前（I4）。"""
    return f"issue-{n}"


def check_bundles(bundles: Optional[list[Bundle]], issues: list[int]) -> Optional[list[Bundle]]:
    """1 つのレーンのまとまりの I1（課題が 2 つ以上のまとまりに入らない）・I2（課題は `input.issues` にある）・
    I3（名前は補う名前を含めてレーンの中で一意）を確かめる。破れたら種類 `bundle` の検証の誤り。"""
    if bundles is None:
        return None
    listed = [n for b in bundles for n in b.issues]
    dup = sorted({n for n in listed if listed.count(n) > 1})
    if dup:
        raise PydanticCustomError("bundle", "2 つ以上のまとまりに入る課題がある: {issues}", {"issues": _refs(dup)})
    missing = [n for n in listed if n not in issues]
    if missing:
        raise PydanticCustomError("bundle", "レーンの課題（input.issues）に無い課題がまとまりにある: {issues}", {"issues": _refs(missing)})
    names = [b.name for b in bundles] + [single_name(n) for n in issues if n not in listed]
    same = sorted({v for v in names if names.count(v) > 1})
    if same:
        raise PydanticCustomError("bundle", "同じレーンの中でまとまりの名前が重なる: {names}", {"names": ", ".join(same)})
    return bundles


def checked_input(inp):
    """スプリントのレーンの入力の検証の後段（`SprintInput` の model_validator）。"""
    check_bundles(inp.bundles, inp.issues)
    return inp


def input_bundle_list(inp) -> Optional[list[Bundle]]:
    """`SprintInput.bundle_list()` の本体。"""
    return bundle_list(inp.issues, inp.bundles)


def bundle_list(issues: list[int], bundles: Optional[list[Bundle]]) -> Optional[list[Bundle]]:
    """書かれたまとまりと、どのまとまりにも無い課題の 1 件のまとまり（I4）を `issues` の順に返す。欄が無ければ None。"""
    if bundles is None:
        return None
    owner = {n: b for b in bundles for n in b.issues}
    out: list[Bundle] = []
    for n in issues:
        b = owner.get(n) or Bundle(name=single_name(n), issues=[n])
        if b not in out:
            out.append(b)
    return out


def name_of(lane, issue: int) -> str:
    """レーンの課題が入るまとまりの名前。まとまりの欄の無いレーン（ループのレーンを含む）は `issue-<番号>`。"""
    got = getattr(lane.input, "bundle_list", lambda: None)()
    return next((b.name for b in got or [] if issue in b.issues), single_name(issue))


def _refs(issues: list[int]) -> str:
    return " ".join(f"#{n}" for n in issues)


def render_rows(lanes) -> list[str]:
    """`plan.md` のまとまりの表（まとまりの欄のあるレーンだけ）。無ければ空。"""
    rows = [(ln.name, b) for ln in lanes for b in (getattr(ln.input, "bundle_list", lambda: None)() or [])]
    if not rows:
        return []
    lines = ["", "## 課題のまとまり", "", "| レーン | まとまり | 課題（先頭が代表） |", "| --- | --- | --- |"]
    return lines + [f"| {lane} | {b.name} | {_refs(b.issues)} |" for lane, b in rows]


# ---- レーンの側の読み出し ----


class BrokenPlanError(ValueError):
    """このレーンを持つ動いている計画が検証に通らない（起動の後の手の直しで I1〜I3 が破れたなど）。"""

    def __init__(self, plan: str, error: str):
        super().__init__(f"並列の計画 {plan} が検証に通らない: {error}")
        self.plan, self.error = plan, error


def bundles_of(main: Path, lane: Optional[str] = None, status=None) -> Optional[list[Bundle]]:
    """動いている計画のこのレーン（名前を省けば `NDF_RELAY_NAME`）のまとまりの一覧。レーンでない・まとまりの欄が無ければ None。

    検証に通らない計画が、このレーンの名前・自分のメインディレクトリ `main` と一致するリポジトリ・動いているか失われた状態を
    すべて満たせば `BrokenPlanError` を投げる（`lane_of` は検証に通らない計画を飛ばすため、黙って「レーンでない」にしない）。"""
    import parallel_lane as pl

    status = status or pl.run_status
    name = lane if lane is not None else os.environ.get("NDF_RELAY_NAME")
    if name:
        _raise_broken(Path(main), name, status)
    got = pl.lane_of(main, lane, status=status)
    if not got:
        return None
    inp = got[1].input
    return inp.bundle_list() if getattr(inp, "bundles", None) is not None else None


def _raise_broken(main: Path, lane: str, status) -> None:
    import parallel_homes
    import parallel_ledger as plg
    import parallel_plan as pp
    from parallel_files import plans_in, resolved_path

    me = resolved_path(main)
    for home in parallel_homes.search_homes(main):
        for plan in plans_in(home):
            try:
                pp.load_valid_plan(home, plan)
                continue
            except OSError:
                continue
            except ValueError as exc:
                error = str(exc)
            try:
                raw = pp.load_plan(home, plan)
            except (OSError, ValueError):
                continue
            lanes = raw.get("lanes") if isinstance(raw, dict) else None
            hit = next((ln for ln in lanes or [] if isinstance(ln, dict) and ln.get("name") == lane), None)
            if hit is None or resolved_path(Path(hit.get("repo") or home)) != me:
                continue
            if plg.plan_state(home, plan, status(home, plan)) in ACTIVE:
                raise BrokenPlanError(plan, error)
