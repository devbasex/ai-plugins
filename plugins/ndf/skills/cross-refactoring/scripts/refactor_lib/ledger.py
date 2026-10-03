"""取り消しの判定（#1482）。改善項目を取り消したかと、各コミットを残すか・消すか・戻すか・公開してよいかを決める。

**判定はここ 1 か所に置く。** 取り消しの 4 つの経路（`undo.drop` / `intake.discard_unverified` /
`converge._apply_fix_result` / 最終ゲート修正のマージ処理）と push の直前の照合が、どれもここを呼ぶ。
git を書き換えるのは `undo`、push するのは `publish` で、どちらも判定の結果に従うだけである。

**git を書き換えず、終了もしない。** 読むのは `rev-parse`・`rev-list`・`show --name-only`・`log` だけ。

| `kind` | 何か | 残すか |
| --- | --- | --- |
| `item` | 取り消されていない改善項目に記録されたコミット | 残す |
| `final_fix` | 最終ゲート修正のマージ処理が受け入れたコミット（`final_gate.fix_commits`） | 残す |
| `orchestrator` | 台帳の `orchestrator_commits` にあるコミット（同期・計画の記録・公開済みの取り消しの revert） | 残す |
| `stray` | 上のどれでもない | 消す（未公開）／公開を止める |

**残すかは状態ファイルに記録した SHA の一致だけで決める。** トレーラーは実装担当も書けるため、
`Item-Id` の値は出力に添えるためだけに読む（設計の決定 3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import statefile

from . import gitfacts  # 属性は呼ぶ時点で引く（gitfacts → publish → ledger の循環を避ける）
from .items import DEFERRED, LIVE, REVERTED, VERIFIED, item_shas
from .paths import full_commit, git_out

ITEM = "item"
FINAL_FIX = "final_fix"
ORCHESTRATOR = "orchestrator"
STRAY = "stray"

# 最終ゲートへ入った後の `state["phase"]`。公開はこの間だけ行う（設計の決定 7）。
FINAL_PHASES = ("final", "done")


@dataclass
class CommitVerdict:
    sha: str
    kind: str
    item_id: str = ""  # 記録からたどった改善項目（`stray` でも取り消した項目のコミットなら入る）
    trailer_item_id: str = ""  # コミットのトレーラーの `Item-Id`（出力に添えるだけ）
    subject: str = ""


@dataclass
class RebuildPlan:
    """取り消し 1 回分の git の並び。`undo` はこれに従って打つだけにする。"""

    before: str  # 取り消しの前の HEAD
    origin: str  # 積み直しの起点（`reset --hard` の先）
    remove: list[str] = field(default_factory=list)  # 未公開の範囲から消すコミット（古い順）
    replay: list[str] = field(default_factory=list)  # 起点の後に積み直すコミット（古い順）
    revert: list[str] = field(default_factory=list)  # 公開済みの範囲で戻すコミット（新しい順）
    dropped: list[str] = field(default_factory=list)  # 取り消す改善項目
    widened: list[str] = field(default_factory=list)  # 同じファイルを触ったため広げた改善項目
    stray: list[str] = field(default_factory=list)  # 消すコミットのうち、どの項目にも記録されていないもの
    span: list[str] = field(default_factory=list)  # 起点より後の元の履歴（古い順）。保存済みの地点の書き直しに使う
    error: str = ""  # 計画を作れなかった理由（`undo` が終了コード 4 で止まる）

    def empty(self) -> bool:
        return not self.remove and not self.revert


# ---------- 改善項目の採否 ----------


def is_live(item: Optional[dict[str, Any]]) -> bool:
    """改善項目が取り消されていないか（コミットを持ちうる状態か）。"""
    return bool(item) and item.get("status") in LIVE


def mark_dropped(item: dict[str, Any], reason: str) -> None:
    """改善項目を取り消した記録にする。**`reverted` へ落とす遷移はここだけが持つ。**"""
    item["status"] = REVERTED
    item.setdefault("failure_reason", reason)


def adoption_confirmed(state: dict[str, Any]) -> bool:
    """残った改善項目を「採用」と数えてよいか。最終ゲートが `passed` のときだけ真（I8）。"""
    return (state.get("final_gate") or {}).get("status") == "passed"


def remaining_count(state: dict[str, Any]) -> int:
    """取り消されずに残った改善項目の数。"""
    return sum(1 for i in state.get("items") or [] if is_live(i))


# ---------- 表示の状態と件数（#1692 の決定 2。コメント・結果 JSON・報告がここを呼ぶ） ----------

ADOPTED = "adopted"
UNCONFIRMED = "unconfirmed"


def display_status(state: dict[str, Any], item: dict[str, Any]) -> str:
    """項目の表示の状態。**「採用」は最終ゲートが `passed` のときの `verified` だけ**（I2）。

    最終ゲートが `passed` でなければ、取り消しでも見送りでもない項目（`LIVE`）はすべて「未確認」にする。
    `passed` のとき、`verified` でない途中の状態は `status` のまま返す。
    """
    status = str(item.get("status") or "")
    if status in (REVERTED, DEFERRED):
        return status
    if not adoption_confirmed(state):
        return UNCONFIRMED if status in LIVE else status
    return ADOPTED if status == VERIFIED else status


@dataclass
class Tally:
    """表示の状態の件数。結果 JSON の `metrics` とリファクタリング計画のコメントの件数の行の元（I1）。"""

    items: int
    adopted: int
    unconfirmed: int
    reverted: int
    deferred: int
    confirmed: bool

    def as_metrics(self) -> dict[str, Any]:
        """結果 JSON の `metrics` の件数のキー。採用と確定していなければ `unconfirmed` を足す（adopted は 0 になる）。"""
        out: dict[str, Any] = {"items": self.items, "adopted": self.adopted, "reverted": self.reverted, "deferred": self.deferred}
        if not self.confirmed:
            out["unconfirmed"] = self.unconfirmed
        return out


def tally(state: dict[str, Any]) -> Tally:
    """表示の状態を数える。**件数の数え方はここだけが持つ。**"""
    items = state.get("items") or []
    shown = [display_status(state, i) for i in items]
    return Tally(
        items=len(items),
        adopted=shown.count(ADOPTED),
        unconfirmed=shown.count(UNCONFIRMED),
        reverted=shown.count(REVERTED),
        deferred=shown.count(DEFERRED),
        confirmed=adoption_confirmed(state),
    )


# ---------- プランの外の取り消し（#1692 の I7。`plan-comment --scan-reverts` だけが呼ぶ） ----------


def mark_outside_revert(item: dict[str, Any], revert_sha: str) -> None:
    """プランの外で取り消された項目を取り消しにし、取り消す前の状態を `outside_revert` に残す。"""
    item["outside_revert"] = {
        "revert": revert_sha,
        "prior_status": item.get("status"),
        "prior_failure_reason": item.get("failure_reason"),
    }
    item.pop("failure_reason", None)
    mark_dropped(item, f"プランの外で取り消した（{revert_sha[:12]}）")


def restore_outside_revert(item: dict[str, Any]) -> bool:
    """プランの外の取り消しが取り消された項目を、取り消す前の状態へ戻す。**`reverted` から戻す遷移はここだけ。**

    `outside_revert` を持たない項目（スクリプトが取り消した項目）は戻さず偽を返す。
    """
    mark = item.get("outside_revert")
    if not isinstance(mark, dict):
        return False
    item["status"] = mark.get("prior_status")
    if mark.get("prior_failure_reason") is None:
        item.pop("failure_reason", None)
    else:
        item["failure_reason"] = mark["prior_failure_reason"]
    item.pop("outside_revert", None)
    return True


# ---------- 公開の台帳 ----------


def _book(state: dict[str, Any]) -> dict[str, Any]:
    ledger = state.get("ledger")
    if not isinstance(ledger, dict):
        ledger = state["ledger"] = {}
    ledger.setdefault("orchestrator_commits", [])
    return ledger


def published_point(state: dict[str, Any]) -> Optional[str]:
    """公開した地点。まだ push していなければ `plan.base_sha`。"""
    ledger = state.get("ledger") if isinstance(state.get("ledger"), dict) else {}
    return ledger.get("published_sha") or (state.get("plan") or {}).get("base_sha")


def in_final_gate(state: dict[str, Any]) -> bool:
    """最終ゲートへ入った後か。公開はこの間だけ行う。"""
    return state.get("phase") in FINAL_PHASES


def note_published(state: dict[str, Any], sha: str) -> None:
    _book(state)["published_sha"] = sha


def note_publication(state: dict[str, Any], status: str, sha: str = "", reason: str = "") -> None:
    """最後に試みた公開の結果（`publication`）を残す。`pushed`・`refused`・`observed` で、`refused` だけが理由を持つ。

    `published_sha`（次の push の照合の起点）とは分ける。失敗や観測で照合の範囲を変えない（#1692 の決定 3）。
    """
    record: dict[str, Any] = {"status": status, "head": state.get("head_branch"), "at": statefile.now()}
    if sha:
        record["sha"] = sha
    if reason:
        record["reason"] = reason
    state["publication"] = record


def note_orchestrator_commit(state: dict[str, Any], sha: str) -> None:
    commits = _book(state)["orchestrator_commits"]
    if sha and sha not in commits:
        commits.append(sha)


def remap_orchestrator_commits(state: dict[str, Any], mapping: dict[str, str]) -> None:
    """積み直しで変わった SHA を書き直す。対応表に無い SHA はそのまま残す。"""
    ledger = _book(state)
    ledger["orchestrator_commits"] = [mapping.get(s, s) for s in ledger["orchestrator_commits"]]


# ---------- コミットの分類 ----------


def _owners(state: dict[str, Any], work: str) -> dict[str, dict[str, Any]]:
    """`完全な SHA → 改善項目`。取り消した項目も含める（`stray` の出力に項目を添えるため）。"""
    owners: dict[str, dict[str, Any]] = {}
    for item in state.get("items") or []:
        for sha in item_shas(item):
            owners[full_commit(work, sha)] = item
    return owners


def keepers(state: dict[str, Any], work: str) -> dict[str, str]:
    """残すコミット（`完全な SHA → kind`）。"""
    kept: dict[str, str] = {}
    for sha in _ledger_commits(state):
        kept[full_commit(work, sha)] = ORCHESTRATOR
    for sha in (state.get("final_gate") or {}).get("fix_commits") or []:
        kept[full_commit(work, sha)] = FINAL_FIX
    for item in state.get("items") or []:
        if is_live(item):
            for sha in item_shas(item):
                kept[full_commit(work, sha)] = ITEM
    return kept


def _ledger_commits(state: dict[str, Any]) -> list[str]:
    ledger = state.get("ledger") if isinstance(state.get("ledger"), dict) else {}
    return list(ledger.get("orchestrator_commits") or [])


def classify_commits(state: dict[str, Any], work: str, shas: list[str]) -> list[CommitVerdict]:
    """コミットを分類する。`stray` にはトレーラーの `Item-Id` と件名を添える。"""
    kept = keepers(state, work)
    owners = _owners(state, work)
    verdicts: list[CommitVerdict] = []
    for sha in shas:
        full = full_commit(work, sha)
        owner = owners.get(full) or {}
        verdict = CommitVerdict(sha=full, kind=kept.get(full, STRAY), item_id=str(owner.get("id") or ""))
        if verdict.kind == STRAY:
            verdict.trailer_item_id = str(gitfacts.commit_trailers(work, full).get("Item-Id") or "").strip()
            verdict.subject = git_out(work, ["log", "-1", "--format=%s", full]) or ""
        verdicts.append(verdict)
    return verdicts


def _oldest_first(work: str, spec: list[str]) -> Optional[list[str]]:
    out = git_out(work, ["rev-list", "--reverse", *spec])
    return None if out is None else out.split()


# ---------- 積み直しの計画 ----------


def _split(
    state: dict[str, Any],
    work: str,
    unpublished: list[str],
    published: list[str],
    dropped: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """未公開の範囲から消すコミット・公開済みの範囲で戻すコミット・消すうちの `stray` を返す。"""
    owners = _owners(state, work)
    kept = keepers(state, work)
    remove: list[str] = []
    stray: list[str] = []
    for sha in unpublished:
        owner = str((owners.get(sha) or {}).get("id") or "")
        if owner in dropped:
            remove.append(sha)
        elif sha not in kept:
            remove.append(sha)
            stray.append(sha)
    revert = [s for s in reversed(published) if str((owners.get(s) or {}).get("id") or "") in dropped]
    return remove, revert, stray


def _widen(state: dict[str, Any], work: str, touched: list[str], dropped: set[str]) -> list[str]:
    """消す・戻すコミットが触ったファイルを触った、取り消されていない改善項目（1 段だけ）。"""
    files: set[str] = set()
    for sha in touched:
        files.update(gitfacts.commit_files(work, sha))
    widened: list[str] = []
    for item in state.get("items") or []:
        if not is_live(item) or item["id"] in dropped:
            continue
        if any(files & set(gitfacts.commit_files(work, sha)) for sha in item_shas(item)):
            widened.append(item["id"])
    return widened


def plan_rebuild(state: dict[str, Any], work: str, targets: list[str], widen: bool = False) -> RebuildPlan:
    """改善項目 `targets` を取り消す git の並びを決める（設計の「`plan_rebuild` の決め方」）。

    未公開のコミットは積み直しで除き、公開済みのコミットだけを revert する。`widen` なら、消す・戻す
    コミットが触ったファイルを触った改善項目まで 1 段だけ広げる。
    """
    before = git_out(work, ["rev-parse", "HEAD"]) or ""
    point = published_point(state)
    base = (state.get("plan") or {}).get("base_sha")
    plan = RebuildPlan(before=before, origin=before, dropped=list(targets))
    if not point:
        # 改修計画の前はどのコミットも改善項目に属さない。取り消すものが無い
        return plan
    point = full_commit(work, point)
    if git_out(work, ["merge-base", "--is-ancestor", point, before or "HEAD"]) is None:
        plan.error = f"公開した地点 {point[:12]} が HEAD の祖先にないため取り消せません"
        return plan
    unpublished = _oldest_first(work, [f"{point}..{before}"]) or []
    published = (_oldest_first(work, [f"{full_commit(work, base)}..{point}"]) or []) if base else []
    dropped = set(targets)
    remove, revert, stray = _split(state, work, unpublished, published, dropped)
    if widen:
        plan.widened = _widen(state, work, remove + revert, dropped)
        dropped |= set(plan.widened)
        remove, revert, stray = _split(state, work, unpublished, published, dropped)
    plan.dropped = sorted(dropped)
    plan.remove, plan.revert, plan.stray = remove, revert, stray
    if remove:
        first = unpublished.index(remove[0])
        plan.origin = unpublished[first - 1] if first else point
        plan.span = unpublished[first:]
        plan.replay = [s for s in plan.span if s not in set(remove)]
    return plan


def unpublishable(state: dict[str, Any], work: str, remote_tip: str) -> list[CommitVerdict]:
    """push が origin へ足すコミット（`remote_tip..HEAD`）のうち、残すコミットでないもの。

    改修計画の起点より前のコミット（この実行が作っていないもの）は照らさない。
    """
    spec = ["HEAD", f"^{remote_tip}"]
    base = (state.get("plan") or {}).get("base_sha")
    if base:
        spec.append(f"^{base}")
    shas = _oldest_first(work, spec)
    if shas is None:
        return [CommitVerdict(sha=remote_tip, kind=STRAY, subject="送るコミットを確定できません")]
    return [v for v in classify_commits(state, work, shas) if v.kind == STRAY]
