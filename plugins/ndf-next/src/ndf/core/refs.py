"""起動の外の成果を、流す直前に参照で解く部品（N1）。

プランとループの 1 回は、今の起動の外にある成果（版・記録の PR・前のステージの PR・検査の件数・問いの当て先・
再開の位置・設計の起点）を、組む時点の値でなく参照で持つ。ここはその参照を、ステップを流す直前に値へ解く。

- 解けない参照は、空・0・unknown で埋めずに、止まった理由の種類つきで返す（`Resolved.unresolved`）
- 空に解けたら飛ばす実行条件（`when_empty: skip`）を持つ参照は、空のときに `Resolved.skipped` へ入る。流さずに
  ok の印を付ける経路は無い（#2408）
- 外の値は読み手（`Sources`）を引数で受けて読む。判断の関数はファイルも GitHub も直に読まない
- GitHub は 1 回のクエリでまとめて読み、ループの中で打たない

プランの参照の宣言は `plan.json` の `refs` に置く:

```json
{"refs": {"version": {"kind": "version", "of": "release.state"},
          "prs": {"kind": "queue_prs", "when_empty": "skip"}}}
```

`of` は参照の先のプランの状態の置き場（呼び手の基準のディレクトリからの相対か絶対のパス）で、`key` は読む outputs の鍵。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol, Union

from ndf.adapters import github
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable
from ndf.core.plan import queue_decide
from ndf.core.plan.queue_run import QUEUE_PRS
from ndf.core.plan.files import read_json
from ndf.core.plan.records import PROGRESS_FILE
from ndf.core.plan.runner import RUN_FILE, result_path

# 参照の種類（プランの `refs` の `kind`）
VERSION, RECORD_PR, PLAN_RESULT, COUNTS = "version", "record_pr", "plan_result", "counts"
QUEUE_PRS_KIND = QUEUE_PRS
REF_KINDS = (VERSION, RECORD_PR, QUEUE_PRS_KIND, PLAN_RESULT, COUNTS)
# 空に解けたら飛ばす実行条件の値（`when_empty`）。流す前の判断と再開の扱いの「流さない」も同じ語
SKIP = "skip"
WHEN_EMPTY_SKIP = SKIP
# 版の形（数 3 つ。後ろに pre-release か build の印を持てる）
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
# 解けていないことを表す値。参照の値として受けない
_PLACEHOLDERS = frozenset({"", "0", "0.0.0", "unknown", "null"})
# 進捗記録の行の種類（回の始まりと、ステップの件数）
EVENT_RUN, EVENT_STEP = "run", "step"
# 課題を閉じる PR を 1 つの課題から読む件数（超えたら読めないとして止まる）
CLOSING_PER_ISSUE = 100
_CLOSING_FIELDS = f"closedByPullRequestsReferences(first: {CLOSING_PER_ISSUE}, includeClosedPrs: true) {{ totalCount nodes {{ number }} }}"
# ループの 1 回の状態のうち、問いを当ててよいもの（進行中と、締め直しの途中の保留）
LAP_RUNNING, LAP_HELD = "running", "held"


class RefError(ValueError):
    """参照の宣言の形が違う。止まった理由は invalid。"""


class Unreadable(Exception):
    """外の値を読めなかった（GitHub の失敗・上限）。`cause` は止まった理由の種類。"""

    def __init__(self, cause: str, reason: str) -> None:
        super().__init__(reason)
        self.cause = cause
        self.reason = reason


class Ref(NamedTuple):
    """参照の宣言 1 つ。`of` は参照の先（プランの状態の置き場）、`key` は読む outputs の鍵。"""

    name: str
    kind: str
    of: Optional[str] = None
    key: Optional[str] = None
    skip_when_empty: bool = False


class Unresolved(NamedTuple):
    """解けなかった参照。`cause` は止まった理由の表の種類。"""

    cause: str
    reason: str


class _Empty(NamedTuple):
    """参照の先は読めたが、値が空・0 だった。"""

    reason: str


@dataclass(frozen=True)
class Resolved:
    """参照を解いた結果。`values` は参照名 → 値、`sources` は参照名 → 出所、`unresolved` は解けなかった参照。

    `skipped` は空に解けて飛ばす実行条件に当たった参照。空・0・unknown で埋めた値は `values` に入らない。
    """

    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    unresolved: dict[str, Unresolved] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    @property
    def decision(self) -> str:
        """流す前の判断。解けない参照があれば stopped、飛ばす実行条件に当たれば `skip`、ほかは ok（流す）。"""
        if self.unresolved:
            return STOPPED
        return SKIP if self.skipped else OK

    def gaps(self) -> list[str]:
        """結果 JSON の gaps に載せる行（`ref:<名前>`）。"""
        return [f"ref:{name}" for name in sorted(self.unresolved)]


class PlanRecord(NamedTuple):
    """参照の先のプランの、最後の回の結果。`status` は結果 JSON の status、`outputs` はその outputs。"""

    status: Optional[str]
    outputs: Mapping[str, Any]


class DesignPr(NamedTuple):
    """課題の設計 PR の状態。PR が無ければ `number` が None。"""

    number: Optional[int]
    merged: bool = False
    state: str = ""
    head_sha: str = ""
    head_ref: str = ""


class Context(NamedTuple):
    """今の起動の文脈。`prior` は前のステージの結果、`issues` は計画の課題、`base` は `of` の相対のパスの基準。"""

    prior: Sequence[queue_decide.Outcome]
    issues: tuple[int, ...]
    base: Path
    accepts_gate: bool = False


class Sources(Protocol):
    """参照の解決に要る外の値の読み手。"""

    def plan_record(self, state_dir: Path) -> Optional[PlanRecord]: ...

    def progress(self, state_dir: Path) -> list[dict[str, Any]]: ...

    def closing_prs(self, issues: Sequence[int]) -> list[int]: ...

    def design_pr(self, branch: str) -> DesignPr: ...


# --- 宣言 ---------------------------------------------------------------------------------


def _ref(name: str, raw: Any) -> Ref:
    if not isinstance(raw, Mapping) or raw.get("kind") not in REF_KINDS:
        raise RefError(f"参照 {name} の kind は {'・'.join(REF_KINDS)} のどれか: {raw!r}")
    texts = {k: raw[k] for k in ("of", "key") if k in raw}
    bad = [k for k, v in texts.items() if not isinstance(v, str) or not v]
    if bad:
        raise RefError(f"参照 {name} の {'・'.join(bad)} は空でない文字列にする")
    if raw["kind"] != QUEUE_PRS_KIND and "of" not in raw:
        raise RefError(f"参照 {name} に参照の先（of）が無い")
    when = raw["when_empty"] if "when_empty" in raw else None
    if when not in (None, WHEN_EMPTY_SKIP):
        raise RefError(f"参照 {name} の when_empty は {WHEN_EMPTY_SKIP} だけ: {when!r}")
    return Ref(name, raw["kind"], texts["of"] if "of" in texts else None, texts["key"] if "key" in texts else None, when is not None)


def declared(plan_raw: Mapping[str, Any]) -> list[Ref]:
    """プランの `refs` の宣言を読む。無ければ空。形が違えば `RefError`。"""
    raw = plan_raw["refs"] if "refs" in plan_raw else {}
    if not isinstance(raw, Mapping):
        raise RefError("プランの refs は参照名 → 宣言の辞書にする")
    return [_ref(name, value) for name, value in raw.items()]


# --- 種類ごとの解決 -----------------------------------------------------------------------

Got = Union[str, Unresolved, _Empty]


def _is_placeholder(value: Any) -> bool:
    return value is None or str(value).strip().lower() in _PLACEHOLDERS


def _target(ref: Ref, ctx: Context) -> Path:
    path = Path(ref.of) if ref.of is not None else ctx.base
    return Path(os.path.normpath(path if path.is_absolute() else ctx.base / path))


def _output(ref: Ref, ctx: Context, src: Sources, default_key: str) -> Union[Any, Unresolved, _Empty]:
    """参照の先のプランの最後の回が完了で、outputs に鍵があればその値。"""
    where, key = _target(ref, ctx), ref.key if ref.key is not None else default_key
    record = src.plan_record(where)
    if record is None:
        return Unresolved("input_missing", f"{where} に前の回の結果が無い")
    if record.status != OK:
        return Unresolved("input_missing", f"{where} の前の回が完了でない（{record.status}）")
    if key not in record.outputs or _is_placeholder(record.outputs[key]):
        return _Empty(f"{where} の outputs の {key} が無いか空")
    return record.outputs[key]


def _version(ref: Ref, ctx: Context, src: Sources) -> Got:
    got = _output(ref, ctx, src, VERSION)
    if isinstance(got, (Unresolved, _Empty)):
        return got
    if not _SEMVER.match(str(got)):
        return Unresolved("input_missing", f"版の形でない: {got!r}")
    return str(got)


def _record_pr(ref: Ref, ctx: Context, src: Sources) -> Got:
    got = _output(ref, ctx, src, RECORD_PR)
    if isinstance(got, (Unresolved, _Empty)):
        return got
    text = str(got).lstrip("#")
    if not text.isdigit():
        return Unresolved("input_missing", f"記録の PR の番号でない: {got!r}")
    return text


def _plan_result(ref: Ref, ctx: Context, src: Sources) -> Got:
    if ref.key is None:
        return Unresolved("invalid", f"参照 {ref.name} に読む outputs の鍵（key）が無い")
    got = _output(ref, ctx, src, ref.key)
    if isinstance(got, (Unresolved, _Empty)):
        return got
    return got if isinstance(got, str) else json.dumps(got, ensure_ascii=False, sort_keys=True)


def queue_prs_of(prior: Sequence[queue_decide.Outcome], closing: Sequence[int], accepts_gate: bool = False) -> list[int]:
    """前のステージで完了したプランの PR と、計画の課題を閉じる PR の和を、重ねず昇順で返す（#2568）。"""
    return sorted(set(queue_decide.queue_prs(prior, accepts_gate)) | {int(n) for n in closing})


def _queue_prs(ref: Ref, ctx: Context, src: Sources) -> Got:
    closing = src.closing_prs(ctx.issues) if ctx.issues else []
    prs = queue_prs_of(ctx.prior, closing, ctx.accepts_gate)
    if not prs:
        return _Empty("前のステージの完了した PR も、計画の課題を閉じる PR も無い")
    return " ".join(map(str, prs))


def counts_of(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """進捗記録の行から、最後の頭からの回より後の区間で、ステップごとに最後の件数を返す（#2569）。

    回の始まりの行は `{"event": "run", "start": <開始のステップか null>}`、件数の行は
    `{"event": "step", "step": <id>, "counts": {...}}`。途中から流し直した回は区間を切らず、前の回の件数を残す。
    """
    heads = [i for i, r in enumerate(rows) if r.get("event") == EVENT_RUN and r.get("start") is None]
    section = rows[heads[-1] :] if heads else rows
    out: dict[str, Any] = {}
    for row in section:
        if row.get("event") == EVENT_STEP and isinstance(row.get("step"), str) and isinstance(row.get("counts"), dict):
            out[row["step"]] = row["counts"]
    return out


def _counts(ref: Ref, ctx: Context, src: Sources) -> Got:
    where = _target(ref, ctx)
    found = counts_of(src.progress(where))
    if not found:
        return _Empty(f"{where} の進捗記録に件数の行が無い")
    if ref.key is not None:
        if ref.key not in found:
            return _Empty(f"{where} の進捗記録にステップ {ref.key} の件数が無い")
        return json.dumps(found[ref.key], ensure_ascii=False, sort_keys=True)
    return json.dumps(found, ensure_ascii=False, sort_keys=True)


_RESOLVERS = {VERSION: _version, RECORD_PR: _record_pr, QUEUE_PRS_KIND: _queue_prs, PLAN_RESULT: _plan_result, COUNTS: _counts}


def _one(ref: Ref, ctx: Context, src: Sources) -> Got:
    try:
        return _RESOLVERS[ref.kind](ref, ctx, src)
    except Unreadable as exc:
        return Unresolved(exc.cause, exc.reason)


def _source(ref: Ref, ctx: Context) -> str:
    if ref.kind == QUEUE_PRS_KIND:
        return f"前のステージの PR と課題 {', '.join(f'#{n}' for n in ctx.issues)} を閉じる PR"
    return str(_target(ref, ctx))


def resolve(refs: Sequence[Ref], ctx: Context, src: Sources) -> Resolved:
    """参照をすべて値へ解く。解けない参照は種類と理由つきで `unresolved` に、空で飛ばす参照は `skipped` に入る。"""
    out = Resolved()
    skipped: list[str] = []
    for ref in refs:
        got = _one(ref, ctx, src)
        if isinstance(got, Unresolved):
            out.unresolved[ref.name] = got
        elif isinstance(got, _Empty) and ref.skip_when_empty:
            skipped.append(ref.name)
        elif isinstance(got, _Empty):
            out.unresolved[ref.name] = Unresolved("input_missing", got.reason)
        else:
            out.values[ref.name] = got
            out.sources[ref.name] = _source(ref, ctx)
    return Resolved(out.values, out.sources, out.unresolved, tuple(skipped))


def resolve_plan(plan_raw: Mapping[str, Any], ctx: Context, src: Sources) -> Resolved:
    """プランの `refs` の宣言を読んで解く。宣言の形が違えば、その誤りを invalid の unresolved として返す。"""
    try:
        refs = declared(plan_raw)
    except RefError as exc:
        return Resolved(unresolved={"refs": Unresolved("invalid", str(exc))})
    return resolve(refs, ctx, src)


# --- 再開の位置 ---------------------------------------------------------------------------


def match_plan(plans: Mapping[str, Path], given: str, cwd: Path) -> list[str]:
    """`--from` に渡された名前・フルパス・相対パスを、前の起動の一覧のプラン（名前 → 絶対パス）へ照らす（#2806）。

    プランの名前・ファイル名の stem・末尾のパスの一致・`cwd` から解いた絶対パスの一致のどれかで当たった名前を返す。
    """
    want = given.strip()
    absolute = Path(os.path.normpath(Path(want) if Path(want).is_absolute() else cwd / want))
    tail = "/" + want.removeprefix("./")
    return [
        name
        for name, path in plans.items()
        if want in (name, path.stem) or path.as_posix().endswith(tail) or Path(os.path.normpath(path)) == absolute
    ]


class ResumeTarget(NamedTuple):
    """再開のときのプラン 1 本の扱い。`action` は ok（流す）・`skip`（完了で流さない）・gate（人へ戻す）。"""

    name: str
    action: str
    start: Optional[str]
    reason: str


def resume_target(name: str, digest: str, recorded: Optional[Mapping[str, Any]], start: Optional[str]) -> ResumeTarget:
    """前の起動の記録から、プラン 1 本を流すか・流さないか・人へ戻すかを決める。`--from` の開始点は常に勝つ。

    完了でハッシュが同じなら流さない。完了の後にプランが変わった（ハッシュが違う・記録にハッシュが無い）なら、
    黙って飛ばさずに人へ戻す。
    """
    if start is not None:
        return ResumeTarget(name, OK, start, f"--from で {start} から流す")
    if recorded is None or recorded.get("status") != OK:
        return ResumeTarget(name, OK, None, "前の起動で完了していない")
    if "digest" not in recorded:
        return ResumeTarget(name, GATE, None, "完了の記録にプランのハッシュが無く、変わっていないかを確かめられない")
    if recorded["digest"] != digest:
        return ResumeTarget(name, GATE, None, "完了の後にプランが変わった（--from <プラン>=<ステップ> で流し直すか決める）")
    return ResumeTarget(name, SKIP, None, "前の起動で完了し、プランは変わっていない")


# --- 設計の起点と問いの当て先 -------------------------------------------------------------


class DesignStart(NamedTuple):
    """次のループの 1 回の設計の起点。`skip` なら設計を飛ばす。`commit` は設計を続ける起点のコミット（無ければ新しく始める）。"""

    skip: bool
    commit: Optional[str]
    reason: str


def design_start(pr: DesignPr) -> DesignStart:
    """設計 PR の状態から、次のループの 1 回の設計の起点を決める（#2528・#2489・#2665）。"""
    if pr.number is None:
        return DesignStart(False, None, "設計 PR が無い（設計から始める）")
    if pr.merged:
        return DesignStart(True, None, f"設計 PR #{pr.number} はマージ済み（設計を飛ばす）")
    if pr.state == "open" and pr.head_sha:
        return DesignStart(False, pr.head_sha, f"設計 PR #{pr.number} の最新 {pr.head_sha[:12]} から続ける")
    return DesignStart(False, None, f"設計 PR #{pr.number} はマージされずに閉じた（設計から始める）")


class Lap(NamedTuple):
    """ループの 1 回。`status` は running・held など、`closing` は保留の 1 回を締め直している途中か。"""

    name: str
    status: str
    plans: tuple[str, ...]
    closing: bool = False


def question_target(laps: Sequence[Lap], plan: str) -> Optional[Lap]:
    """問いを出したプランが属するループの 1 回（進行中か、締め直しの途中の保留）を返す。無ければ None（#2638・#2664）。"""
    wanted = os.path.normpath(plan)
    open_laps = [lap for lap in laps if lap.status == LAP_RUNNING or (lap.status == LAP_HELD and lap.closing)]
    found = [lap for lap in open_laps if wanted in {os.path.normpath(p) for p in lap.plans}]
    return found[-1] if found else None


# --- 読み手（ファイルと GitHub） ----------------------------------------------------------


def _unreadable(exc: github.GitHubError, what: str) -> Unreadable:
    """GitHub の失敗を、打ち直せば通りうるもの（不達・5xx・上限）は transient、ほかは precondition へ写す。"""
    passing = exc.status == 0 or exc.status >= 500 or exc.retry_after is not None or exc.status == 429
    return Unreadable("transient" if passing else "precondition", f"{what}を読めない: {exc}")


def _closing_numbers(issue: int, node: Mapping[str, Any]) -> list[int]:
    conn = node["closedByPullRequestsReferences"] if "closedByPullRequestsReferences" in node else None
    if not isinstance(conn, Mapping):
        raise Unreadable("precondition", f"#{issue} は課題でないか読めない")
    numbers = [int(n["number"]) for n in conn["nodes"]]
    if int(conn["totalCount"]) > len(numbers):
        raise Unreadable("precondition", f"#{issue} を閉じる PR が {conn['totalCount']} 本あり、{CLOSING_PER_ISSUE} 本を超える")
    return numbers


@dataclass(frozen=True)
class RepoSources:
    """状態の置き場のファイルと、GitHub のアダプターから外の値を読む読み手。"""

    hub: github.GitHub

    def plan_record(self, state_dir: Path) -> Optional[PlanRecord]:
        run = read_json(state_dir / RUN_FILE)
        if run is None or not isinstance(run.get("key"), str):
            return None
        found = read_json(result_path(state_dir, durable.run_name(run["key"])))
        if found is None:
            return None
        outputs = found["outputs"] if isinstance(found.get("outputs"), dict) else {}
        return PlanRecord(found.get("status"), outputs)

    def progress(self, state_dir: Path) -> list[dict[str, Any]]:
        try:
            text = (state_dir / PROGRESS_FILE).read_text(encoding="utf-8")
        except FileNotFoundError:
            # まだ 1 行も書いていない回は記録が無い
            return []
        return [row for row in map(_json_line, text.splitlines()) if row is not None]

    def closing_prs(self, issues: Sequence[int]) -> list[int]:
        try:
            got = self.hub.nodes(issues, "issue", _CLOSING_FIELDS)
        except github.GitHubError as exc:
            raise _unreadable(exc, "課題を閉じる PR ") from None
        return sorted({n for issue, node in got.items() for n in _closing_numbers(issue, node)})

    def design_pr(self, branch: str) -> DesignPr:
        try:
            pulls = self.hub.pulls_by_head([branch])[branch]
        except github.GitHubError as exc:
            raise _unreadable(exc, f"設計 PR（{branch}）") from None
        if not pulls:
            return DesignPr(None)
        p = pulls[0]
        return DesignPr(p.number, p.merged, p.state, p.head_sha, p.head_ref)


def _json_line(line: str) -> Optional[dict[str, Any]]:
    """進捗記録の 1 行。空・壊れた行・辞書でない行は None（書きかけの末尾の行を読み飛ばす）。"""
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:  # noqa: NDF401 ファイル: 書きかけの行は無いものとして読み飛ばす
        return None
    return row if isinstance(row, dict) else None
