"""項目の単位の取り消し（#933 の AC10 AC15、実装計画 I6）。

**起点（`plan.base_sha`）から HEAD までを新しい順に取り消し、残す項目のコミットを
古い順に積み直す。** 項目のコミットだけを戻すと、取り消す項目より新しい別の項目の
コミットが同じ箇所を触っているときに必ず競合する（v10.17.x までの実測）。範囲全体を
逆再生する取り消しは競合しない。

積み直しが競合したときは、次の順で広げる。

| 段 | 何を取り消すか | `mode` |
| --- | --- | --- |
| 1 | 指定した項目と、どの項目にも属さないコミットだけ | `item` |
| 2 | 指定した項目と同じファイルを触った項目まで広げる | `widened` |
| 3 | 起点より後の項目をすべて（全件の取り消しへ退避） | `all` |

どの項目にも属さないコミット（進行側の同期のコミット・過去の取り消し）は積み直さない。
同期のコミットは次の公開で作り直される。

**中断しても再開できる形で記録する。** 着手の前に `pending_drop` と `pending_push` を
立てて保存し、終わったら印を消して保存する。印が残ったまま再開したら、最初から
やり直す（取り消し済みの項目は `status: reverted` で飛ばす）。
"""
from __future__ import annotations

import pathlib
from typing import Any, Iterable, Optional

import statefile

from . import info
from .gitfacts import (
    reset_hard,
    replay_commits,
    revert_range,
    commit_files,
    commits_in_range,
)
from .items import LIVE, REVERTED, find_item, item_shas
from .paths import git_out, work_dir


def _full(work: str, sha: str) -> str:
    return git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"]) or sha


def _owner(work: str, state: dict[str, Any]) -> dict[str, str]:
    """`コミット（完全な SHA）→ 項目 ID`。取り消されていない項目だけを見る。"""
    owner: dict[str, str] = {}
    for item in state.get("items") or []:
        if item.get("status") not in LIVE:
            continue
        for sha in item_shas(item):
            owner[_full(work, sha)] = item["id"]
    return owner


def _files_of(work: str, state: dict[str, Any], item_ids: Iterable[str]) -> set[str]:
    files: set[str] = set()
    for item_id in item_ids:
        item = find_item(state, item_id, required=False)
        for sha in item_shas(item or {}):
            files.update(commit_files(work, sha))
    return files


def _attempt(
    work: str, ordered: list[str], owner: dict[str, str], keep: set[str],
    before: Optional[str],
) -> Optional[dict[str, str]]:
    """範囲を取り消し、`keep` の項目のコミットを古い順に積み直す。競合したら `None`。

    競合したときは HEAD を `before`（取り消しの前）へ戻してから返す。呼び出し側が
    広げた範囲でやり直せるようにするためである。
    """
    revert_range(work, ordered, before)
    replay = [s for s in reversed(ordered) if owner.get(s) in keep]
    mapping = replay_commits(work, replay)
    if mapping is None:
        reset_hard(work, before)
    return mapping


def _remap(state: dict[str, Any], work: str, mapping: dict[str, str]) -> None:
    """積み直しで変わった SHA を項目の記録へ書き戻す。**書き戻さないと次の取り消しが
    履歴に無い SHA を指す。**"""
    for item in state.get("items") or []:
        commits = item.get("commits") or {}
        for key in ("test", "implement"):
            sha = commits.get(key)
            if sha:
                commits[key] = mapping.get(_full(work, sha), sha)
        commits["fix"] = [mapping.get(_full(work, s), s) for s in commits.get("fix") or []]


def _close_commitless(state: dict[str, Any], targets: list[str], reason: str) -> list[str]:
    """コミットを持たない対象を git に触れず閉じ、残る対象を返す。"""
    remaining = list(targets)
    for item_id in [i for i in remaining if not item_shas(find_item(state, i))]:
        item = find_item(state, item_id)
        item["status"] = REVERTED
        item.setdefault("failure_reason", reason)
        remaining.remove(item_id)
    return remaining


def _replay_without_dropped(
    work: str, state: dict[str, Any], ordered: list[str], targets: list[str], head: Optional[str],
) -> dict[str, Any]:
    """item、widened、all の順で積み直し、採用した方式と対応表を返す。"""
    owner = _owner(work, state)
    live = {i for i in owner.values()}
    dropped = set(targets)
    mode = "item"
    mapping = _attempt(work, ordered, owner, live - dropped, head)
    if mapping is None:
        files = _files_of(work, state, dropped)
        widened = {i for i in live
                   if i not in dropped and _files_of(work, state, [i]) & files}
        if widened:
            info(f"⚠ 積み直しが競合したため、同じファイルを触った {len(widened)} 件も取り消します")
            dropped |= widened
            mode = "widened"
            mapping = _attempt(work, ordered, owner, live - dropped, head)
    if mapping is None:
        info("⚠ 積み直しが競合したため、計画の項目をすべて取り消します")
        dropped = set(live)
        mode = "all"
        revert_range(work, ordered, head)
        mapping = {}
    return {"mode": mode, "dropped": dropped, "mapping": mapping}


def _record_drop(
    state: dict[str, Any], work: str, result: dict[str, Any], context: dict[str, Any],
) -> dict[str, Any]:
    """積み直し結果を項目の状態、履歴、pending 状態へ反映する。"""
    mode, dropped, mapping = result["mode"], result["dropped"], result["mapping"]
    _remap(state, work, mapping)
    for item_id in sorted(dropped):
        item = find_item(state, item_id, required=False)
        if item is None:
            continue
        item["status"] = REVERTED
        item.setdefault(
            "failure_reason",
            context["reason"] if item_id in context["targets"]
            else f"{context['reason']}（{mode} の取り消しに巻き込まれた）",
        )
    record = {
        "at": statefile.now(), "mode": mode, "reason": context["reason"],
        "dropped": sorted(dropped), "extra": sorted(context["extra"]),
        "reverted_commits": len(context["ordered"]), "replayed": len(mapping),
    }
    state.setdefault("drops", []).append(record)
    state["pending_drop"] = None
    return record


def drop(
    path: pathlib.Path,
    state: dict[str, Any],
    item_ids: list[str],
    reason: str,
    extra_shas: Iterable[str] = (),
) -> dict[str, Any]:
    """項目（と、どの項目にも属さないコミット）を取り消す。取り消した項目 ID を返す。

    `extra_shas` は項目に属さない取り消し対象（計画に無い `Item-Id` のコミットなど）。
    戻り値は `{"mode", "dropped", "reverted_commits", "replayed"}`。取り消した項目は
    `status: reverted`、`failure_reason` に理由を持つ。**見送り（`deferred_items`）へ
    入れるかは呼び出し側が決める**（`test_failed` / `not_done` は見送り、検証の失敗は
    項目の状態だけ）。
    """
    work = work_dir(state)
    base = (state.get("plan") or {}).get("base_sha")
    targets = [i for i in item_ids
               if (find_item(state, i, required=False) or {}).get("status") in LIVE]
    targets = _close_commitless(state, targets, reason)
    extra = {_full(work, s) for s in extra_shas}
    if not targets and not extra:
        statefile.save(path, state)
        return {"mode": "skip", "dropped": [], "reverted_commits": 0, "replayed": 0}

    head = git_out(work, ["rev-parse", "HEAD"])
    ordered = commits_in_range(work, base, head or "HEAD")
    if ordered is None:
        from . import die
        die(f"取り消しの範囲を確定できません（起点 {base} / HEAD {head}）")
        raise SystemExit(4)

    state["pending_drop"] = {"items": targets, "extra": sorted(extra), "reason": reason}
    state["pending_push"] = True
    statefile.save(path, state)

    result = _replay_without_dropped(work, state, ordered, targets, head)
    context = {"reason": reason, "targets": targets, "extra": extra, "ordered": ordered}
    record = _record_drop(state, work, result, context)
    statefile.save(path, state)
    info(f"↩ 取り消し {len(ordered)} コミット / 積み直し {record['replayed']} コミット（{record['mode']}）")
    return record


def resume_pending_drop(path: pathlib.Path, state: dict[str, Any]) -> None:
    """前回終わらなかった取り消しをやり直す。取り込み・検証の入口で呼ぶ。

    取り消しは範囲全体の逆再生なので、途中で落ちた後にやり直しても結果は同じ木になる
    （取り消し済みの項目は `status` で飛ばす）。
    """
    pending = state.get("pending_drop")
    if not pending:
        return
    info("↻ 前回終わらなかった取り消しをやり直します")
    drop(path, state, list(pending.get("items") or []), str(pending.get("reason") or ""),
         pending.get("extra") or [])
