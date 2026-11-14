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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol, Union

from ndf.adapters import github
from ndf.contracts.exit_codes import OK, STOPPED
from ndf.core import durable
from ndf.core.plan import queue_decide
from ndf.core.plan.queue_run import QUEUE_PRS
from ndf.core.plan.files import read_json
from ndf.core.plan.records import PROGRESS_FILE
from ndf.core.plan.runner import RUN_FILE, result_path
from ndf.core.refs.resume import SKIP, DesignPr

# 参照の宣言の鍵（種類・参照の先・読む outputs の鍵・空に解けたときの実行条件）
KIND, OF, KEY, WHEN_EMPTY = "kind", "of", "key", "when_empty"
# 参照の種類（プランの `refs` の `kind`）
VERSION, RECORD_PR, PLAN_RESULT, COUNTS = "version", "record_pr", "plan_result", "counts"
QUEUE_PRS_KIND = QUEUE_PRS
# 空に解けたら飛ばす実行条件の値（`when_empty`）。流す前の判断の「流さない」と同じ語（`resume.SKIP`）
WHEN_EMPTY_SKIP = SKIP
# 版の形（数 3 つ。後ろに pre-release か build の印を持てる）
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
# 解けていないことを表す値。参照の値として受けない
_PLACEHOLDERS = frozenset({"", "0", "0.0.0", "unknown", "null"})
# 進捗記録の行の種類（回の始まりと、ステップの件数）
EVENT_RUN, EVENT_STEP = "run", "step"
# 課題を閉じる PR を 1 つの課題から読む件数（超えたら読めないとして止まる）
CLOSING_PER_ISSUE = 100
# 課題を閉じる PR の GraphQL の接続の名前（クエリの選択と、答えの節点の鍵）
_CLOSED_BY = "closedByPullRequestsReferences"
_CLOSING_FIELDS = f"{_CLOSED_BY}(first: {CLOSING_PER_ISSUE}, includeClosedPrs: true) {{ totalCount nodes {{ number merged }} }}"


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


def _text_fields(name: str, raw: Mapping[str, Any]) -> dict[str, str]:
    """宣言の `of`・`key` のうち書かれたもの。書かれていて空でない文字列でなければ `RefError`。"""
    texts = {k: raw[k] for k in (OF, KEY) if k in raw}
    bad = [k for k, v in texts.items() if not isinstance(v, str) or not v]
    if bad:
        raise RefError(f"参照 {name} の {'・'.join(bad)} は空でない文字列にする")
    return texts


def _skip_when_empty(name: str, raw: Mapping[str, Any]) -> bool:
    """宣言の `when_empty` が `skip` なら True、無ければ False。ほかの値は `RefError`。"""
    when = raw.get(WHEN_EMPTY)
    if when not in (None, WHEN_EMPTY_SKIP):
        raise RefError(f"参照 {name} の {WHEN_EMPTY} は {WHEN_EMPTY_SKIP} だけ: {when!r}")
    return when is not None


def _ref(name: str, raw: Any) -> Ref:
    if not isinstance(raw, Mapping) or raw.get(KIND) not in REF_KINDS:
        raise RefError(f"参照 {name} の {KIND} は {'・'.join(REF_KINDS)} のどれか: {raw!r}")
    texts = _text_fields(name, raw)
    if raw[KIND] != QUEUE_PRS_KIND and OF not in raw:
        raise RefError(f"参照 {name} に参照の先（{OF}）が無い")
    return Ref(name, raw[KIND], texts.get(OF), texts.get(KEY), _skip_when_empty(name, raw))


def declared(plan_raw: Mapping[str, Any]) -> list[Ref]:
    """プランの `refs` の宣言を読む。無ければ空。形が違えば `RefError`。"""
    raw = plan_raw["refs"] if "refs" in plan_raw else {}
    if not isinstance(raw, Mapping):
        raise RefError("プランの refs は参照名 → 宣言の辞書にする")
    return [_ref(name, value) for name, value in raw.items()]


# --- 種類ごとの解決 -----------------------------------------------------------------------

Got = Union[str, Unresolved, _Empty]


def _canonical_json(value: Any) -> str:
    """文字列でない値を参照の値にするときの JSON（日本語をそのまま、鍵は昇順）。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def _checked(ref: Ref, ctx: Context, src: Sources, default_key: str, check: Callable[[Any], Got]) -> Got:
    """参照の先の outputs の値を読み、読めて空でなければ `check` で参照の値（か解けない理由・空）にする。"""
    got = _output(ref, ctx, src, default_key)
    if isinstance(got, (Unresolved, _Empty)):
        return got
    return check(got)


def _version_text(got: Any) -> Union[str, Unresolved]:
    if not _SEMVER.match(str(got)):
        return Unresolved("input_missing", f"版の形でない: {got!r}")
    return str(got)


def _pr_number_text(got: Any) -> Got:
    """`#` を外した番号。数でなければ解けない理由、0（`#0`・`00` も）なら空（#2408）。"""
    text = str(got).lstrip("#")
    if not (text.isascii() and text.isdigit()):
        return Unresolved("input_missing", f"記録の PR の番号でない: {got!r}")
    if int(text) == 0:
        return _Empty(f"記録の PR の番号が 0: {got!r}")
    return text


def _result_text(got: Any) -> str:
    return got if isinstance(got, str) else _canonical_json(got)


def _version(ref: Ref, ctx: Context, src: Sources) -> Got:
    return _checked(ref, ctx, src, VERSION, _version_text)


def _record_pr(ref: Ref, ctx: Context, src: Sources) -> Got:
    return _checked(ref, ctx, src, RECORD_PR, _pr_number_text)


def _plan_result(ref: Ref, ctx: Context, src: Sources) -> Got:
    if ref.key is None:
        return Unresolved("invalid", f"参照 {ref.name} に読む outputs の鍵（key）が無い")
    return _checked(ref, ctx, src, ref.key, _result_text)


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
    return {row["step"]: row["counts"] for row in _last_section(rows) if _is_counts_row(row)}


def _last_section(rows: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
    """最後の頭からの回（`start` が null の回の始まり）から後の行。頭からの回が無ければ全部。"""
    heads = [i for i, r in enumerate(rows) if r.get("event") == EVENT_RUN and r.get("start") is None]
    return rows[heads[-1] :] if heads else rows


def _is_counts_row(row: Mapping[str, Any]) -> bool:
    """ステップの件数の行（`step` が文字列で `counts` が辞書）か。"""
    return row.get("event") == EVENT_STEP and isinstance(row.get("step"), str) and isinstance(row.get("counts"), dict)


def _counts(ref: Ref, ctx: Context, src: Sources) -> Got:
    where = _target(ref, ctx)
    found = counts_of(src.progress(where))
    if not found:
        return _Empty(f"{where} の進捗記録に件数の行が無い")
    if ref.key is not None:
        if ref.key not in found:
            return _Empty(f"{where} の進捗記録にステップ {ref.key} の件数が無い")
        return _canonical_json(found[ref.key])
    return _canonical_json(found)


_RESOLVERS = {VERSION: _version, RECORD_PR: _record_pr, QUEUE_PRS_KIND: _queue_prs, PLAN_RESULT: _plan_result, COUNTS: _counts}
# 受ける参照の種類は解決の対応表が正本（並びはエラー文の列挙の順）
REF_KINDS = tuple(_RESOLVERS)


def _one(ref: Ref, ctx: Context, src: Sources) -> Got:
    try:
        return _RESOLVERS[ref.kind](ref, ctx, src)
    except Unreadable as exc:
        return Unresolved(exc.cause, exc.reason)


def _source(ref: Ref, ctx: Context) -> str:
    if ref.kind == QUEUE_PRS_KIND:
        return f"前のステージの PR と課題 {', '.join(f'#{n}' for n in ctx.issues)} を閉じる PR"
    return str(_target(ref, ctx))


def _place(out: Resolved, skipped: list[str], ref: Ref, got: Got, ctx: Context) -> None:
    """解いた結果 1 つを、値・解けない参照・飛ばす参照のどれかへ振り分ける。"""
    if isinstance(got, Unresolved):
        out.unresolved[ref.name] = got
    elif isinstance(got, _Empty) and ref.skip_when_empty:
        skipped.append(ref.name)
    elif isinstance(got, _Empty):
        out.unresolved[ref.name] = Unresolved("input_missing", got.reason)
    else:
        out.values[ref.name] = got
        out.sources[ref.name] = _source(ref, ctx)


def resolve(refs: Sequence[Ref], ctx: Context, src: Sources) -> Resolved:
    """参照をすべて値へ解く。解けない参照は種類と理由つきで `unresolved` に、空で飛ばす参照は `skipped` に入る。"""
    out = Resolved()
    skipped: list[str] = []
    for ref in refs:
        _place(out, skipped, ref, _one(ref, ctx, src), ctx)
    return replace(out, skipped=tuple(skipped))


def resolve_plan(plan_raw: Mapping[str, Any], ctx: Context, src: Sources) -> Resolved:
    """プランの `refs` の宣言を読んで解く。宣言の形が違えば、その誤りを invalid の unresolved として返す。"""
    try:
        refs = declared(plan_raw)
    except RefError as exc:
        return Resolved(unresolved={"refs": Unresolved("invalid", str(exc))})
    return resolve(refs, ctx, src)


# --- 読み手（ファイルと GitHub） ----------------------------------------------------------


def _unreadable(exc: github.GitHubError, what: str) -> Unreadable:
    """GitHub の失敗を、打ち直せば通りうるもの（不達・5xx・上限）は transient、ほかは precondition へ写す。

    状態が 0 でも届いていない失敗（GraphQL の誤り・repository が null・ページの上限・認証が無い）は precondition。
    """
    passing = exc.unreached or exc.status >= 500 or exc.retry_after is not None or exc.status == 429
    return Unreadable("transient" if passing else "precondition", f"{what}を読めない: {exc}")


def _closing_numbers(issue: int, node: Mapping[str, Any]) -> list[int]:
    """課題を閉じる PR のうち、マージされた PR の番号。閉じた未マージ・開いたままの PR は入れない。"""
    conn = node[_CLOSED_BY] if _CLOSED_BY in node else None
    if not isinstance(conn, Mapping):
        raise Unreadable("precondition", f"#{issue} は課題でないか読めない")
    if int(conn["totalCount"]) > len(conn["nodes"]):
        raise Unreadable("precondition", f"#{issue} を閉じる PR が {conn['totalCount']} 本あり、{CLOSING_PER_ISSUE} 本を超える")
    return [int(n["number"]) for n in conn["nodes"] if n.get("merged") is True]


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
