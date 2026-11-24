"""cross-review の指摘のうち、プロジェクト MVV に反するとした指摘と根拠の記述の誤りを、MVV の記録と改訂の兆候へ残すこと（#4076・#4090）。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import git_sandbox
import pytest

from ndf.contracts import internal, limits
from ndf.core.converge import oscillation
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import mvv_decide as md

HEAD, NEXT_HEAD = "1" * 40, "2" * 40
PR = 41
SECRET = "AbCdEf0123456789-GhIj"
VALUE = {"id": "r1-1", "text": "方針に反する", "by": "codex", "mvv": "Value 5", "mvv_pinned": True}
REDLINE = {
    "id": "r1-2",
    "text": "人に聞く操作",
    "by": "kiro",
    "mvv": "P1",
    "redline": True,
    "redline_items": ["P1", "C7"],
    "outcome": "unresolved",
}
CITATION = {
    "id": "r1-n1",
    "text": "doc.md の根拠の記述 Value 99 が承認済みの MVV に無い",
    "by": "ndf",
    "path": "doc.md",
    "cited": "Value 99",
    "unknown": ["Value 99"],
}


def _snapshot(version: int = 11, sha: str = "a") -> mvv.Snapshot:
    return mvv.Snapshot(md.State.APPROVED, version, sha * 64, None, (), {}, HEAD, "c" * 64, None)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    return tmp_path


class Clean:
    """秘密を伏せる処理の偽物。呼ばれた回数と値を残し、`secret` を含む値を書き換える。"""

    def __init__(self, secret: str = SECRET):
        self.secret = secret
        self.calls: list[list[str]] = []

    def __call__(self, texts: Sequence[str]) -> list[str]:
        self.calls.append(list(texts))
        return [t.replace(self.secret, "[伏せた]") for t in texts]


def _deferred(reason: Optional[str] = "範囲の外", **how: Any) -> dict[str, Any]:
    return {**VALUE, "outcome": "deferred", "reason": reason, **how}


def _record(
    root: Path, *rows: dict[str, Any], snap: Optional[mvv.Snapshot] = None, head: str = HEAD, clean: Any = list, pr: int = PR
) -> int:
    """今の MVV で判定した指摘 `rows` を記録する。"""
    snap = snap if snap is not None else _snapshot()
    got = mvv.record_review(mvv.ReviewTarget(str(root), pr, head), snap, [{"judged_mvv": snap.identity, **r} for r in rows], clean)
    assert (got.missing, got.cause) == ((), None)
    return got.written


def _rows(kind: str) -> list[dict[str, Any]]:
    rows, broken = jsonl.read_rows(mvv.record_path(kind), kind=kind)
    assert broken == []
    return rows


def _reviews() -> list[dict[str, Any]]:
    return _rows("mvv-validation")


def _signals() -> list[dict[str, Any]]:
    return _rows("mvv-signals")


def _unknowns(root: Path, version: int = 11) -> int:
    return md.count_signals(_signals(), {"versions": [{"version": version, "sha256": "s"}]}, str(root)).unknown


# --- 記録の形 -------------------------------------------------------------------------------


def test_deferred_finding_is_recorded_and_counted_as_a_sign_of_revision(root: Path) -> None:
    """修正担当が見送った MVV の指摘 1 件で、理由つきの記録と、改訂の兆候が 1 件ずつ増える。"""
    assert _record(root, _deferred()) == 2
    [review], [signal] = _reviews(), _signals()
    key = mvv.record_key(VALUE["text"])
    shared = {
        "version": 1,
        "repo": str(root),
        "pr": PR,
        "key": key,
        "id": "r1-1",
        "mvv_version": 11,
        "sha256": "a" * 64,
        "at": review["at"],
    }
    assert review == {**shared, "kind": "mvv-review", "head": HEAD, "item": "Value 5", "outcome": "deferred", "reason": "範囲の外"}
    assert signal == {**shared, "kind": md.REVIEW_DISPUTED}
    assert _unknowns(root) == 1


def test_every_judged_finding_is_recorded_and_only_deferred_ones_are_signs(root: Path) -> None:
    fixed = {**VALUE, "id": "r1-3", "text": "直した指摘", "outcome": "fixed", "reason": None}
    assert _record(root, REDLINE, fixed, {**CITATION, "outcome": "fixed"}) == 3
    got = {r["id"]: r for r in _reviews()}
    assert {fid: (r["item"], r["outcome"], r.get("text"), r.get("reason")) for fid, r in got.items()} == {
        "r1-2": ("P1・C7", "redline", "人に聞く操作", None),
        "r1-3": ("Value 5", "fixed", None, None),
        "r1-n1": ("Value 99", "fixed", None, None),
    }
    assert _signals() == [] and _unknowns(root) == 0
    # 記録のコメントから引き継いだ根拠の記述の誤りは、引いた項目を持たない
    carried = {k: v for k, v in CITATION.items() if k not in ("cited", "unknown", "path")}
    _record(root, {**carried, "id": "p-r1", "text": "引き継いだ誤り", "outcome": "unresolved"})
    assert "item" not in _reviews()[-1] and _reviews()[-1]["outcome"] == "unresolved"


def _findings_judged_under_another_mvv_or_settled_plain_ones_are_not_recorded(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = {**_deferred(), "judged_mvv": _snapshot(10).identity}
    plain = {"id": "r1-9", "text": "普通の指摘", "outcome": "deferred", "reason": "範囲の外"}
    snap = _snapshot()
    # 区分が filed・not_applicable の通常の見送りと、直した時刻の無い通常の指摘の直しは、記録を読まずに何も書かない
    filed = {**plain, "defer": "filed"}
    fixed = {**plain, "id": "r1-8", "text": "直した普通の指摘", "outcome": "fixed", "reason": None}
    reads: list[Any] = []
    real = jsonl.read_rows
    monkeypatch.setattr(jsonl, "read_rows", lambda *a, **kw: (reads.append(a), real(*a, **kw))[1])
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [other, filed, fixed], list) == (0, (), None)
    assert reads == []
    # 直した時刻があっても、同じキーの保留の記録が無ければ書かない
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [{**fixed, md.REVIEW_FIXED_AT: LATER}], list) == (0, (), None)
    assert _reviews() == [] and _signals() == []


def _fixing_a_plain_finding_does_not_clear_a_redline_of_an_earlier_mvv(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前の MVV でレッドラインだった指摘は、MVV が変わると通常の指摘として扱われるが、その直しではレッドラインの保留を解かない。"""
    old = _snapshot(10, "b")
    _record(root, {**REDLINE, "text": "人に聞く操作"}, snap=old)
    snap = _snapshot()
    plain = {"id": "r2-1", "text": "人に聞く操作", "by": "kiro", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [plain], list).written == 0
    holds = md.review_holds(_reviews(), 0, str(root), [PR], (snap.version, snap.sha256), hidden="-")
    assert holds == [f"PR #{PR} の P1・C7（redline）: 人に聞く操作"]


def _a_plain_finding_left_to_the_user_holds_the_design_gate_until_it_is_fixed(root: Path, defer: dict[str, Any]) -> None:
    """MVV の項目の無い通常の指摘でも、利用者の判断に委ねた見送り（区分 needs_user・区分なし）は承認ゲートの保留にする（#3959）。"""
    plain = {"id": "r1-9", "text": "普通の指摘", "by": "codex", "outcome": "deferred", "reason": "利用者が決める", **defer}
    snap = _snapshot()
    mine = (snap.version, snap.sha256)
    got = mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [plain], list)
    assert (got.written, got.missing, got.cause) == (1, (), None)
    [review] = _reviews()
    assert (review["outcome"], review.get(md.REVIEW_DEFER), review["reason"], "item" in review) == (
        "deferred",
        defer.get("defer"),
        "利用者が決める",
        False,
    )
    # 通常の指摘の見送りは、MVV の改訂の兆候に数えない
    assert _signals() == []
    kind = defer.get("defer", "区分なし")
    assert md.review_holds(_reviews(), 0, str(root), [PR], mine, hidden="-") == [
        f"PR #{PR} の 指摘 r1-9（deferred・{kind}）: 利用者が決める"
    ]
    # 後の起動で直せば、見送りを記録した後に直した（修正担当の結果を読むときに直した時刻が付く）ので、直した記録を書き、保留が外れる
    fixed = {**plain, "id": "r2-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [fixed], list).written == 1
    assert md.review_holds(_reviews(), 0, str(root), [PR], mine, hidden="-") == []
    # 記録を書く前に止まったら、通常の指摘の見送りも欠けた必須の行に数える
    assert [e["outcome"] for e in mvv.unwritable(snap, [plain], "止まった").missing] == ["deferred"]


# 直した時刻（記録の見送りより前・後）
EARLIER, LATER = "2000-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"
PLAIN = {"id": "r1-9", "text": "普通の指摘", "by": "codex", "outcome": "deferred", "reason": "利用者が決める"}


def _plain_holds(root: Path) -> list[str]:
    snap = _snapshot()
    return [h for h in md.review_holds(_reviews(), 0, str(root), [PR], (snap.version, snap.sha256), hidden="-") if "r1-9" in h]


def _a_plain_fix_is_not_withheld_and_an_old_withheld_fix_is_completed(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 自由記述の無い直しは、同じ起動で別の指摘の自由記述を伏せても `withheld` を付けずに書き、保留を解く。"""
    snap = _snapshot()
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [PLAIN], list).written == 1
    fixed = {**PLAIN, "id": "r2-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    other = {**_deferred(), "judged_mvv": snap.identity}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [fixed, other], None).cause is None
    assert [r.get("withheld") for r in _reviews() if r["outcome"] == "fixed"] == [None]
    assert _plain_holds(root) == []


def _a_fix_withheld_by_an_earlier_version_is_written_again(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """前のバージョンが自由記述の無い直しに `withheld` を付けて書いた記録は、次の起動で伏せずに書き直して保留を解く。"""
    snap = _snapshot()
    mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [PLAIN], list)
    [held] = _reviews()
    later = {**held, "at": "2098-01-01T00:00:00+00:00", "id": "r2-1", "outcome": "fixed", "withheld": True}
    jsonl.append_locked(
        mvv.record_path("mvv-validation"), {k: v for k, v in later.items() if k not in ("reason", "version")}, kind="mvv-validation"
    )
    assert _plain_holds(root) != []
    fixed = {**PLAIN, "id": "r2-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [fixed], list).written == 1
    assert _reviews()[-1].get("withheld") is None


def _a_fix_made_before_another_state_dir_deferred_it_does_not_clear_the_hold(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 別の状態ディレクトリが後から利用者の判断に委ねた指摘は、それより前に直した記録の再送では解かない。"""
    snap = _snapshot()
    mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [PLAIN], list)
    before = {**PLAIN, "id": "r1-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: EARLIER}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [before], list).written == 0
    assert _plain_holds(root) != []


def _a_hold_added_by_another_state_dir_after_the_last_check_is_cleared_by_a_later_fix(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 状態ディレクトリ A が別件の直しを照らし終えた後に、B が指摘を利用者に委ねて見送っても、A でその指摘を再掲して直せば、
    記録の最新の状態と照らして保留を解く。照らし終えた時刻より前の直しは、記録を読まずに照らし直さない。"""
    snap = _snapshot()
    other = {**PLAIN, "id": "r1-2", "text": "別件", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: EARLIER}
    # A: 別件の直しを照らした（保留が無いので書かない）。照らし終えた時刻を since として次の起動へ渡す
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [other], list).written == 0
    since = jsonl.now()
    # B: 同じ PR で指摘を利用者に委ねて見送る
    mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [PLAIN], list)
    reads: list[Any] = []
    real = jsonl.read_rows
    monkeypatch.setattr(jsonl, "read_rows", lambda *a, **kw: (reads.append(a), real(*a, **kw))[1])
    # A: 照らし終えた時刻より前の直しだけなら、記録を読まない
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [other], list, since) == (0, (), None) and reads == []
    # A: 再掲された指摘を直せば、追記の前の 1 回の読み取りで最新の保留と照らして解く
    fixed = {**PLAIN, "id": "r2-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [other, fixed], list, since).written == 1
    assert len(reads) == 1 and _plain_holds(root) == []


def _a_plain_fix_does_not_hide_a_redline_with_the_same_key(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 伏せた後に同じキーになる通常の直しとレッドラインが同居しても、レッドラインは保留と照らさずに書く。"""
    snap = _snapshot()
    fixed = {"id": "r1-1", "text": REDLINE["text"], "by": "codex", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    got = mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [fixed, {"judged_mvv": snap.identity, **REDLINE}], list)
    assert (got.missing, got.cause) == ((), None)
    assert [r["outcome"] for r in _reviews()] == ["redline"]


def _a_fix_in_the_same_second_as_the_deferral_or_the_last_check_is_told_apart(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 見送りの記録・照らし終えた時刻と同じ秒の中で後に直した指摘も、保留を解く候補にする。"""
    snap = _snapshot()
    mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [PLAIN], list)
    since = jsonl.now_exact()
    fixed = {**PLAIN, "id": "r2-1", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: jsonl.now_exact()}
    assert [f.row["id"] for f in md.judged_findings([fixed], snap.identity, holds=True, since=since)] == ["r2-1"]
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [fixed], list, since).written == 1
    assert _plain_holds(root) == []


def _the_hold_candidates_skip_rereading_until_another_state_dir_adds_a_hold(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#3959: 保留が変わらない間は、直した通常の指摘が出ても記録をロックして読み直さない。別の状態ディレクトリが保留を足せば、
    足された行だけを読んで気付き、追記の前の読み取りで解く。"""
    snap = _snapshot()
    _record(root, {**VALUE, "outcome": "fixed", "reason": None})
    index = mvv.HoldIndex(PR)
    first = {**PLAIN, "id": "r1-1", "text": "一つ目", "outcome": "fixed", "reason": None, md.REVIEW_FIXED_AT: LATER}
    # 初回は確かめていないので、追記の前の読み取りで候補を確かめる
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [first], list, None, index).written == 0
    assert index.size > 0 and index.keys == set()
    locks: list[Any] = []
    real_lock = jsonl.lock
    monkeypatch.setattr(jsonl, "lock", lambda path: (locks.append(path), real_lock(path))[1])
    second = {**first, "id": "r2-1", "text": "二つ目"}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [second], list, None, index) == (0, (), None) and locks == []
    # 別の状態ディレクトリが同じ PR に保留を足す（ここでロックを 1 回取る）
    mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [{**PLAIN, "text": "三つ目"}], list)
    third = {**first, "id": "r3-1", "text": "三つ目"}
    assert mvv.record_review(mvv.ReviewTarget(str(root), PR, NEXT_HEAD), snap, [third], list, None, index).written == 1
    assert len(locks) == 2 and mvv.record_key("三つ目") not in index.keys
    assert md.review_holds(_reviews(), 0, str(root), [PR], (snap.version, snap.sha256), hidden="-") == []


def _the_check_time_and_the_candidates_are_kept_per_pr(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ndf.domains.crossreview import drive, store

    state = {store.MVV_FIXES_CHECKED: {"pr": PR, "at": EARLIER, "size": 10, "keys": ["k"]}}
    since, index = drive._fixes_checked(SimpleNamespace(ref=SimpleNamespace(current_pr=PR), data=state))
    assert (since, index) == (EARLIER, mvv.HoldIndex(PR, 10, {"k"}))
    # 巻き直した後の別の PR では、前の PR の時刻と候補を使わない
    assert drive._fixes_checked(SimpleNamespace(ref=SimpleNamespace(current_pr=PR + 1), data=state)) == (None, mvv.HoldIndex(PR + 1))


def test_broken_shape_is_refused(root: Path) -> None:
    """種類ごとに要る欄の無い記録は書けない。"""
    common = {"at": "2026-11-24T00:00:00+00:00", "key": "k", "repo": "/m"}
    for kind, row in (
        ("mvv-validation", {**common, "kind": "mvv-review", "verdict": md.FOLLOW}),
        ("mvv-validation", {**common, "kind": md.MVV_VALIDATION_SPRINT}),
        ("mvv-signals", {**common, "kind": md.REVIEW_DISPUTED, "mvv_version": 11}),
    ):
        with pytest.raises(internal.InternalError):
            internal.write_record(kind, row)


# --- 重ねない -------------------------------------------------------------------------------


def test_same_result_is_not_recorded_twice_even_under_another_id(root: Path) -> None:
    rows = [_deferred(), REDLINE, {**CITATION, "outcome": "unresolved"}]
    assert _record(root, *rows) == 4
    assert _record(root, *rows) == 0
    assert _record(root, *({**r, "id": f"r7-{n}"} for n, r in enumerate(rows))) == 0
    assert _unknowns(root) == 1


@pytest.mark.parametrize(
    ("change", "added"),
    [
        ({"reason": "別の理由"}, (1, 0)),
        ({"outcome": "fixed", "reason": None}, (1, 0)),
        ({"mvv": "Value 6"}, (1, 0)),
    ],
)
def test_changed_result_is_recorded_once_without_another_sign(root: Path, change: dict[str, Any], added: tuple[int, int]) -> None:
    _record(root, _deferred())
    _record(root, {**_deferred(), **change})
    assert (len(_reviews()) - 1, len(_signals()) - 1) == added


def test_finding_deferred_again_after_a_fix_or_a_redline_on_another_item_or_head_is_recorded(root: Path) -> None:
    """同じ head で「見送った・直した・見送った」と戻れば 3 件になる（最後の記録と比べる）。"""
    for row in (_deferred(), {**VALUE, "outcome": "fixed", "reason": None}, _deferred()):
        _record(root, row)
    assert [r["outcome"] for r in _reviews()] == ["deferred", "fixed", "deferred"]
    # レッドラインは、項目か head が変われば書く
    _record(root, {**REDLINE, "redline_items": ["P1"]})
    _record(root, REDLINE)
    _record(root, REDLINE, head=NEXT_HEAD)
    assert [(r["item"], r["head"]) for r in _reviews()[3:]] == [("P1", HEAD), ("P1・C7", HEAD), ("P1・C7", NEXT_HEAD)]


@pytest.mark.parametrize("snap", [_snapshot(12), _snapshot(11, "b")])
def test_new_mvv_is_recorded_and_counted_apart(root: Path, snap: mvv.Snapshot) -> None:
    """バージョンか本文の sha256 が変われば、前の MVV の記録があっても記録と兆候の両方を書く。"""
    _record(root, _deferred())
    assert _record(root, _deferred(), snap=snap) == 2


def test_missing_sign_is_added_and_another_pull_request_does_not_hide_one(root: Path) -> None:
    _record(root, _deferred())
    mvv.record_path("mvv-signals").unlink()
    assert _record(root, _deferred()) == 1
    assert len(_reviews()) == 1 and _unknowns(root) == 1
    # 別の PR の記録は、この PR の記録と兆候を隠さない
    _record(root, _deferred(), pr=PR + 1)
    assert _unknowns(root) == 2


def _last_record_is_chosen_by_time_and_then_by_order(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """後から追記された古い時刻の記録を、最後の記録とみなさない。"""
    _record(root, _deferred())
    [last] = _reviews()
    path = mvv.record_path("mvv-validation")
    jsonl.append_locked(path, {**last, "outcome": "fixed", "at": "2020-01-01T00:00:00+00:00"}, kind="mvv-validation")
    assert _record(root, _deferred()) == 0
    jsonl.append_locked(path, {**last, "outcome": "fixed"}, kind="mvv-validation")
    assert _record(root, _deferred()) == 1


def test_findings_with_the_same_text_are_recorded_by_the_one_that_is_left(root: Path) -> None:
    """引き継いで直したになった誤りと、同じ本文で見つけ直した誤りは 1 件の記録にし、打ち直しても増やさない。"""
    old, new = {**CITATION, "id": "p-r1-n1", "outcome": "fixed"}, {**CITATION, "outcome": "unresolved"}
    for rows in ([old, new], [new, old], [old, new]):
        _record(root, *rows)
    assert [(r["id"], r["outcome"]) for r in _reviews()] == [("r1-n1", "unresolved")]


# --- 秘密を書かない ---------------------------------------------------------------------------


def _written() -> str:
    paths = [mvv.record_path(kind) for kind in ("mvv-validation", "mvv-signals")]
    return "".join(p.read_text(encoding="utf-8") for p in paths if p.is_file())


def test_issue_4090_7_secrets_are_not_written_in_any_field(root: Path, monkeypatch) -> None:
    """理由・本文・引いた項目の秘密を 1 回でまとめて伏せ、記録のキーにも本文の断片を残さない。打ち直しても増えない。"""
    monkeypatch.setenv("NDF_TEST_API_TOKEN", SECRET)
    rows = [
        _deferred(f"理由 {SECRET}", text=f"{SECRET} を引いた指摘"),
        {**REDLINE, "text": f"{SECRET} を引いた操作"},
        {**CITATION, "outcome": "unresolved", "cited": SECRET},
    ]
    clean = Clean()
    assert _record(root, *rows, clean=clean) == 4
    assert len(clean.calls) == 1 and sorted(clean.calls[0]) == sorted([f"理由 {SECRET}", f"{SECRET} を引いた操作", SECRET])
    written = _written()
    assert SECRET not in written and SECRET.casefold() not in written.casefold()
    assert {len(r["key"]) for r in [*_reviews(), *_signals()]} == {64}
    assert _record(root, *rows, clean=clean) == 0


def test_issue_4090_7_key_does_not_depend_on_case_spaces_or_the_secret(monkeypatch) -> None:
    monkeypatch.setenv("NDF_TEST_API_TOKEN", SECRET)
    width = int(limits.value(oscillation.BODY_MATCH, "count"))
    assert mvv.record_key("Same  Text\n") == mvv.record_key("same text") != mvv.record_key("other text")
    # 伏せてから小文字にして切り詰めるので、切り詰める位置にまたがる秘密も、ハッシュの元に入らない
    for at in (0, width - 4):
        with monkeypatch.context() as other:
            other.setenv("NDF_TEST_API_TOKEN", SECRET[::-1])
            swapped = mvv.record_key("x" * at + SECRET[::-1])
        assert mvv.record_key("x" * at + SECRET) == swapped


def test_issue_4090_7_findings_that_differ_only_in_a_hidden_reference_stay_apart(root: Path) -> None:
    """伏せた後の理由が同じでも、別々の指摘は別のキーになる。片方を直しても、もう片方の見送りを打ち消さない。"""
    one, two = (_deferred("理由", id=f"r1-{n}", text=f"o/private-{n}#1 に反する") for n in (1, 2))
    _record(root, one, two)
    assert len({r["key"] for r in _reviews()}) == len(_signals()) == _unknowns(root) == 2
    _record(root, {**one, "outcome": "fixed", "reason": None}, two)
    assert {r["id"]: r["outcome"] for r in _reviews()} == {"r1-1": "fixed", "r1-2": "deferred"}


def test_reason_hidden_in_another_way_is_recorded_under_the_same_key(root: Path) -> None:
    """参照先の公開範囲が変わって伏せ方が変わると、記録は 1 件増えるが、キーは同じで兆候は増えない。"""
    _record(root, _deferred("o/ref#1 で扱う"), clean=Clean("無い"))
    assert _record(root, _deferred("o/ref#1 で扱う"), clean=Clean("o/ref#1")) == 1
    assert len({r["key"] for r in _reviews()}) == len(_signals()) == 1


# --- 参照先の公開範囲を確かめられなかった起動 --------------------------------------------------------

ALL = [_deferred(), REDLINE, {**CITATION, "outcome": "fixed"}]


def _issue_4090_7_withheld_record_has_no_free_text(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """自由記述を書かずに、キー・項目の番号・終わり方・head と、伏せたことだけを書く。兆候はふだんどおり書く。"""
    assert _record(root, *ALL, clean=None) == 4
    got = {r["id"]: r for r in _reviews()}
    assert all(r["withheld"] is True and r["head"] == HEAD and r["mvv_version"] == 11 for r in got.values())
    assert all("reason" not in r and "text" not in r for r in got.values())
    assert [(r.get("item"), r["outcome"]) for r in got.values()] == [("Value 5", "deferred"), ("P1・C7", "redline"), (None, "fixed")]
    assert "範囲の外" not in _written() and "Value 99" not in _written() and _unknowns(root) == 1


def _issue_4090_7_withheld_record_is_completed_and_does_not_replace_a_complete_one(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """伏せた記録は、伏せずに書ける起動で書き直す。伏せずに書いた記録の後に、同じ結果を伏せて書かない。"""
    _record(root, *ALL, clean=None)
    assert _record(root, *ALL, clean=None) == 0
    assert _record(root, *ALL) == 3
    assert _record(root, *ALL, clean=None) == 0
    assert [r.get("withheld") for r in _reviews()] == [True] * 3 + [None] * 3
    assert len(_signals()) == 1
    # 結果が変わっていれば、伏せてでも書く
    assert _record(root, *ALL, clean=None, head=NEXT_HEAD) == 3
    # 伏せた見送りの後に直しても、兆候は 1 件のまま
    _record(root, {**VALUE, "outcome": "fixed", "reason": None}, head=NEXT_HEAD)
    assert _reviews()[-1]["outcome"] == "fixed" and _unknowns(root) == 1


# --- 見送りの区分と、承認ゲートの判定に要る行の欠け（#3959） ------------------------------------------------


def _holds(root: Path) -> list[str]:
    rows, broken = mvv.validation_rows(root)
    return md.review_holds(mvv.review_rows(rows), len(broken), str(root), [PR], (11, "a" * 64), hidden="（伏せた）")


@pytest.mark.parametrize(
    ("steps", "outcomes"),
    [
        # 区分だけが変わった見送りは、通常の行も伏せた行も書き、利用者の判断が要る区分なら保留になる
        ([("filed", list), ("needs_user", list)], [("filed", None), ("needs_user", None)]),
        ([("filed", None), ("needs_user", None)], [("filed", True), ("needs_user", True)]),
        # 伏せずに書いた後の同じ区分の伏せた行は書かず、伏せた行の後の伏せない行は書く（記録の復旧）
        ([("needs_user", list), ("needs_user", None)], [("needs_user", None)]),
        ([("filed", None), ("filed", list)], [("filed", True), ("filed", None)]),
        # 知らない区分はジャーナルが持たないので、区分の無い見送りとして書く
        ([("later", list)], [(None, None)]),
    ],
)
def test_defer_is_written_on_deferred_rows_and_read_back(root: Path, steps: list[tuple[str, Any]], outcomes: list[tuple]) -> None:
    for defer, clean in steps:
        _record(root, _deferred(defer=defer) if defer in md.DEFERS else _deferred(), clean=clean)
    rows, broken = mvv.validation_rows(root)
    assert broken == [] and [(r.get("defer"), r.get("withheld")) for r in mvv.review_rows(rows)] == outcomes
    last, held = outcomes[-1], _holds(root)
    expected = last[0] in (None, "needs_user")
    assert len(held) == int(expected) and (not held or ("記録の自由記述が未完了" in held[0]) is bool(last[1]))


class Fail:
    """記録の追記の偽物。`at` 回目（1 から）の追記で `OSError` を投げ、ほかは本物で書く。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, at: int, kind: str = "mvv-validation"):
        self.n, self.at, self.kind, real = 0, at, kind, jsonl.append

        def append(path: Path, row: Any, *, kind: str) -> Any:
            if kind == self.kind:
                self.n += 1
                if self.n == self.at:
                    raise OSError(f"{path} に空きが無い https://x-access-token:{SECRET}@github.com/o/r.git")
            return real(path, row, kind=kind)

        monkeypatch.setattr(jsonl, "append", append)


FIXED = {**VALUE, "id": "r1-3", "text": "直した指摘", "outcome": "fixed", "reason": None}


def _broken_bytes() -> None:
    path = mvv.record_path("mvv-validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\n")


def _newer() -> None:
    path = mvv.record_path("mvv-validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 999, "kind": "mvv-review", "repo": "x", "pr": PR}) + "\n", encoding="utf-8")


def _no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def lock(path: Path) -> Any:
        raise OSError("ロックの時間切れ")

    monkeypatch.setattr(jsonl, "lock", lock)


@pytest.mark.parametrize(
    ("rows", "prepare", "missing", "step"),
    [
        # 渡した順が fixed・redline でも必須の行を先に書くので、通常の行の失敗は欠けを作らない
        ([FIXED, REDLINE], lambda m: Fail(m, 2), [], "追記"),
        ([FIXED, REDLINE, _deferred()], lambda m: Fail(m, 1), ["redline", "deferred"], "追記"),
        ([REDLINE], lambda m: Fail(m, 1, "mvv-signals"), [], None),
        ([_deferred()], lambda m: Fail(m, 1, "mvv-signals"), [], "mvv-signals.jsonl の追記"),
        ([REDLINE, _deferred(), FIXED], lambda m: _broken_bytes(), ["redline", "deferred"], "既存の行の読み取り"),
        ([FIXED], lambda m: _broken_bytes(), [], "既存の行の読み取り"),
        ([REDLINE, _deferred()], lambda m: _newer(), ["redline", "deferred"], "既存の行の読み取り"),
        ([REDLINE, _deferred()], _no_lock, ["redline", "deferred"], "ロックの取得"),
    ],
)
def test_rows_that_cannot_be_written_are_returned_without_raising(
    root: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], prepare: Any, missing: list[str], step: Optional[str]
) -> None:
    monkeypatch.setenv("NDF_TEST_API_TOKEN", SECRET)
    prepare(monkeypatch)
    path = mvv.record_path("mvv-validation")
    before = path.read_bytes() if path.is_file() else None
    snap = _snapshot()
    got = mvv.record_review(mvv.ReviewTarget(str(root), PR, HEAD), snap, [{"judged_mvv": snap.identity, **r} for r in rows], list)
    assert [m["outcome"] for m in got.missing] == missing
    assert (got.cause is None, (got.cause or "").split(": ")[0]) == (step is None, step or "")
    assert SECRET not in (got.cause or "")
    if before is not None:
        assert path.read_bytes() == before


def _a_retry_writes_only_the_rows_left_missing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """先頭の必須の行の追記が失敗しても、後ろの同じ必須の行が既に記録にあれば欠けと報告しない。打ち直しは欠けた行だけを書く。"""
    _record(root, REDLINE)
    with monkeypatch.context() as m:
        Fail(m, 1)
        snap = _snapshot()
        got = mvv.record_review(
            mvv.ReviewTarget(str(root), PR, HEAD), snap, [{"judged_mvv": snap.identity, **r} for r in (_deferred(), REDLINE)], list
        )
    assert [r["outcome"] for r in got.missing] == ["deferred"] and got.cause is not None
    assert _record(root, _deferred(), REDLINE) == 2
    assert [r["outcome"] for r in _reviews()] == ["redline", "deferred"]


# --- 記録の場面（それぞれ別の記録の保存先で打つ） ---------------------------------------------------------

CASES = {
    "last-record-by-time-then-order": _last_record_is_chosen_by_time_and_then_by_order,
    "withheld-has-no-free-text": _issue_4090_7_withheld_record_has_no_free_text,
    "withheld-is-completed": _issue_4090_7_withheld_record_is_completed_and_does_not_replace_a_complete_one,
    "another-mvv-or-settled-plain": _findings_judged_under_another_mvv_or_settled_plain_ones_are_not_recorded,
    "plain-fix-keeps-an-earlier-redline": _fixing_a_plain_finding_does_not_clear_a_redline_of_an_earlier_mvv,
    "left-to-the-user-needs-user": lambda root, m: _a_plain_finding_left_to_the_user_holds_the_design_gate_until_it_is_fixed(
        root, {"defer": "needs_user"}
    ),
    "left-to-the-user-no-defer": lambda root, m: _a_plain_finding_left_to_the_user_holds_the_design_gate_until_it_is_fixed(root, {}),
    "plain-fix-not-withheld": _a_plain_fix_is_not_withheld_and_an_old_withheld_fix_is_completed,
    "old-withheld-fix-rewritten": _a_fix_withheld_by_an_earlier_version_is_written_again,
    "fix-before-another-deferral": _a_fix_made_before_another_state_dir_deferred_it_does_not_clear_the_hold,
    "hold-added-after-the-check": _a_hold_added_by_another_state_dir_after_the_last_check_is_cleared_by_a_later_fix,
    "plain-fix-and-redline-same-key": _a_plain_fix_does_not_hide_a_redline_with_the_same_key,
    "fix-in-the-same-second": _a_fix_in_the_same_second_as_the_deferral_or_the_last_check_is_told_apart,
    "hold-candidates": _the_hold_candidates_skip_rereading_until_another_state_dir_adds_a_hold,
    "check-time-per-pr": _the_check_time_and_the_candidates_are_kept_per_pr,
    "retry-writes-the-missing": _a_retry_writes_only_the_rows_left_missing,
}


@pytest.mark.parametrize("case", list(CASES))
def test_review_records_in_each_case(root: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    CASES[case](root, monkeypatch)
