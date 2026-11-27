"""起動の始めに PR を読み、前の実行が残した指摘を引き継ぐ。出し直された指摘から古いスレッドの記録を外すのもここである。

状態ファイルは書かない（状態が変わったかを返し、保存は呼び手の `drive` がする）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, Optional, Protocol

from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import prior_decide, report_decide, store, threads
from ndf.domains.crossreview.journal import BY_NDF, FindingJournal
from ndf.domains.crossreview.settings import RunConfig

# PR のコメントを読む口を PR の番号から作る（作れなければ例外。注意に載せて読まずに進む）
ReaderFor = Callable[[int], threads.Reader]


class CarryRun(Protocol):
    """引き継ぎが読み書きする、1 回の起動の値（`drive._Run`）。"""

    data: dict[str, Any]
    journal: FindingJournal
    warnings: list[str]
    process: list[dict[str, str]]
    mvv: Optional[str]

    @property
    def mvv_items(self) -> tuple[str, ...]: ...


class Prior(NamedTuple):
    """起動の始めに PR から読んだ、引き継ぎの確かめに要るもの（スレッドと PR コメント）と、スレッドと同じ応答から読んだ PR。読めなければ空。"""

    threads: Sequence[prior_decide.Thread] = ()
    comments: Sequence[prior_decide.Remark] = ()
    pr: Optional[threads.ReadPr] = None


def reader(cfg: RunConfig, reader_for: Optional[ReaderFor], warnings: list[str]) -> Optional[threads.Reader]:
    """PR のコメントを読む口。作れなければ注意に載せて None（読まずに進む）。"""
    if reader_for is None:
        return None
    pr = store.state_pr(cfg.state_dir, cfg.pr).current_pr
    try:
        return reader_for(pr)
    except Exception as exc:  # noqa: BLE001 ネットワーク: 読む口を作れなくてもレビューは進め、注意に残す
        warnings.append(f"PR #{pr} のコメントを読む口を作れない（既存の指摘を相手役へ渡さず、残った指摘を引き継がない）: {exc}")
        return None


def read_prior(prs: Optional[threads.Reader], warnings: list[str]) -> Prior:
    """引き継ぎの確かめに要るもの（PR のスレッドと PR コメント。1 回の起動で 1 回だけ読む）。読めなければ注意に載せて空。"""
    if prs is None:
        return Prior()
    read = None
    try:
        left = list(prs.open_threads())
        read = prs.read_pr()
    except Exception as exc:  # noqa: BLE001 ネットワーク: 読めなくてもレビューは進め、注意に残す
        warnings.append(f"PR のスレッドを読めない（前の実行が残した未解決の指摘を引き継がない）: {exc}")
        left = []
    try:
        comments = list(prs.comments())
    except Exception as exc:  # noqa: BLE001 ネットワーク: 読めなくてもレビューは進め、注意に残す
        warnings.append(f"PR のコメントを読めない（既存の指摘を相手役へ渡さず、記録のコメントの未解決の指摘を引き継がない）: {exc}")
        comments = []
    return Prior(left, comments, read)


def read_reviews(prs: Optional[threads.Reader], warnings: list[str]) -> list[prior_decide.Remark]:
    """相手役へ渡す参考資料のうち、インラインのレビューコメントとレビュー本文（相手役を起動するときだけ読む。PR 3944 の r2-10）。"""
    if prs is None:
        return []
    try:
        return list(prs.reviews())
    except Exception as exc:  # noqa: BLE001 ネットワーク: 読めなくてもレビューは進め、注意に残す
        warnings.append(f"PR のレビューコメントを読めない（既存の指摘を相手役へ渡さない）: {exc}")
        return []


# 別の実行がスレッドを resolve した指摘の理由（PR 3944 の r2-6）
RESOLVED_ELSEWHERE = "別の実行か人がスレッドを resolve した"


def _settle_resolved(run: CarryRun, left: Sequence[prior_decide.Thread], done: dict[str, Any]) -> int:
    """状態ファイルが追う自分のスレッドのうち、別の実行（か人）が resolve したものの未解決の指摘を、修正の対象から外す
    （直したとして、そのスレッドへ返信も resolve もし直さない。PR 3944 の r2-6）。外した件数を返す。NDF が機械で確かめて作った
    指摘は外さない（直ったかは次の確かめだけが決める。#4090）。"""
    resolved = {t.comment for t in left if t.resolved}
    settled = 0
    for fid, mark in done.items():
        row = run.journal.rows.get(fid)
        if not isinstance(mark, dict) or mark.get("comment") not in resolved or row is None or row.get("outcome") != fin.UNRESOLVED:
            continue
        if row.get("by") == BY_NDF:
            continue
        row |= {"outcome": fin.FIXED, "reason": RESOLVED_ELSEWHERE}
        mark |= {"replied": True, "resolved": True}
        settled += 1
    return settled


def carry(run: CarryRun, prior: Prior) -> bool:
    """前の実行が PR に残した未解決の自分の指摘を未解決の指摘としてジャーナルへ入れる。開いたままのスレッドは `threads` に
    結び（閉じたら返信して resolve する）、スレッドの無い指摘と、スレッドから引き継げなかった NDF の指摘（resolve 済みなど）は
    最新の記録のコメントから戻す。状態ファイルが既に持つスレッド・指摘は引き継がずに MVV の判定だけを重ね、そのうち別の実行が
    resolve したスレッドの指摘は修正の対象から外す。状態が変わったかを返す（保存は呼び手がする）。"""
    done = run.data.setdefault(threads.THREADS, {})
    settled = _settle_resolved(run, prior.threads, done)
    tracked = {m.get("comment") for m in done.values() if isinstance(m, dict)}
    listed, marks = prior_decide.recorded(prior.comments, run.data.get(report_decide.COMMENT_KEY), run.journal.rows)
    carried = prior_decide.carried(prior.threads, tracked, marks)
    held = tracked | {c.comment for c in carried}
    carried += [c for c in listed if c.thread is None or c.thread not in held]
    merged = _merge_tracked(run, prior.threads, done, marks, run.mvv_items)
    added = set(run.journal.carry(carried, run.mvv_items, run.mvv))
    run.warnings += run.journal.warnings
    detach_revived(run)
    merged.update(run.journal.merged)
    for c in carried:
        if c.id in added and c.comment is not None:
            done[c.id] = {"comment": c.comment}
    if settled:
        run.process.append({"run": "start", "result": f"別の実行が resolve したスレッドの指摘 {settled} 件を修正の対象から外した"})
    if added:
        run.process.append({"run": "start", "result": f"PR に残った未解決の指摘 {len(added)} 件を引き継いだ"})
    if merged:
        run.process.append({"run": "start", "result": f"記録のコメントにある MVV の判定を、既にある指摘 {len(merged)} 件に重ねた"})
    return bool(settled or added or merged)


def _merge_tracked(
    run: CarryRun,
    left: Sequence[prior_decide.Thread],
    done: Mapping[str, Any],
    marks: Mapping[int, Mapping[str, Any]],
    items: Sequence[str],
) -> set[str]:
    """状態ファイルが追うスレッドの指摘に、記録のコメントにある同じスレッドの MVV の判定を重ね、変わった指摘の id を返す。
    閉じた指摘には、スレッドが開いていると読めたときだけ重ねる（人が resolve した指摘を戻さない）。"""
    opened = {t.comment for t in left if not t.resolved}
    merged = set()
    for fid, mark in done.items():
        row = run.journal.rows.get(fid)
        comment = mark.get("comment") if isinstance(mark, dict) else None
        if row is None or comment not in marks or (row.get("outcome") != fin.UNRESOLVED and comment not in opened):
            continue
        if run.journal.merge_marks(fid, marks[comment], items, run.mvv):
            merged.add(fid)
    return merged


def restate(run: CarryRun) -> bool:
    """MVV が前の起動から変わっていれば、前の MVV で決めた判定を指摘から外す（最初のラウンドの前に 1 回）。
    外したかを返す（保存は呼び手がする）。"""
    stale = run.journal.restate(run.mvv)
    if stale:
        run.process.append({"run": "start", "result": f"MVV が変わったので、前の MVV で判定した指摘 {len(stale)} 件の判定を外した"})
    return bool(stale)


def detach_revived(run: CarryRun) -> None:
    """直した・見送ったのに出し直された指摘から、resolve した古いスレッドと巻き直しの前に済んだ記録を外す（PR 3944 の r6-6）。
    外さないと、同じ保存先の再開は古い resolve を根拠に再発を直したとし（`_settle_resolved`）、別の保存先の再開は
    閉じたスレッドと、スレッドを持つとした記録のコメントの双方から再発を取りこぼす。外した指摘は、記録の公開で新しい
    スレッドを開くか、スレッドの無い未解決の指摘として記録のコメントに残る。"""
    revived, run.journal.revived = run.journal.revived, []
    if not revived:
        return
    done = run.data.get(threads.THREADS)
    settled = run.data.get(report_decide.SETTLED_KEY)
    for fid in revived:
        if isinstance(done, dict):
            done.pop(fid, None)
        if isinstance(settled, list) and fid in settled:
            settled.remove(fid)
