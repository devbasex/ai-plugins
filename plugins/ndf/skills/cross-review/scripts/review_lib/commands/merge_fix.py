"""副命令 `merge-fix`（#1142 の C2）。"""
from __future__ import annotations

import argparse
from typing import Any

import review_lib  # noqa: E402
import result_posts  # noqa: E402
from review_lib import ci as ci_mod, findings as findings_mod, fix_result, posts, store  # noqa: E402


def _thread_ids(value: Any) -> list[str]:
    """fix の戻り値から、Resolve したと申告されたスレッドの識別子を取り出す。

    正は dict の list だが、件数(int) や単一 dict で返ることがある。識別子を
    取り出せない形は空の一覧として扱い、後段のチェックを行わない。
    """
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [
        str(d["thread_id"]) for d in items
        if isinstance(d, dict) and d.get("thread_id")
    ]


def _thread_positions(value: Any) -> list[dict[str, Any]]:
    """fix の戻り値から、Resolve したスレッドの位置を取り出す（#156）。

    効果の測定（`scripts/measure.py`）の上限の方式が、この位置と指摘の位置を
    結んで「修正された指摘」を決める。**位置は fix の戻り値にしか無い**ため、
    取り込みの時点で写しておかないと後から計算できない。

    **位置の欠けた要素も落とさない。** 落とすと、解決したスレッドの件数
    (`resolved_threads`) と位置の件数が食い違う。欠けた要素は測定の側で
    「どの指摘とも一致しないもの」として数える。

    件数(int) しか返らない劣化表現では位置を作れないため、空の一覧になる。
    """
    positions: list[dict[str, Any]] = []
    for d in _normalize_dict_items(value):
        thread_id = d.get("thread_id")
        path = d.get("path")
        try:
            line: int | None = int(d.get("line"))
        except (TypeError, ValueError):
            line = None
        positions.append({
            "thread_id": str(thread_id) if thread_id else None,
            "path": str(path) if path else None,
            "line": line,
        })
    return positions


def _count(v: Any) -> int:
    """int(件数) でも list でも None でも件数(int)に正規化する。

    fix 結果スキーマ上 deferred/rejected/resolved_threads は list が正だが、
    fix サブエージェントが int(件数) を書いてしまうケースがあり、その場合に
    len() が `TypeError: object of type 'int' has no len()` で落ちるのを防ぐ。
    """
    if isinstance(v, bool):
        # bool は int のサブクラスだが件数として扱わない
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        # LLM が単一要素を list ではなく dict 単体で返すケースを 1 件として扱う。
        return 1
    if isinstance(v, (list, tuple)):
        return len(v)
    if isinstance(v, str) and v.strip().isdigit():
        # LLM が件数を数値文字列 (例: "3" や " 3 ") で返すケースを許容する。
        # strip 後 isdigit() なので前後空白を許し、負号・小数点は引き続き弾く
        # (件数は非負整数なので十分)。
        return int(v.strip())
    return 0


def _normalize_dict_items(raw: object) -> list[dict]:
    """不定形データから dict 要素のみを抽出・正規化する。

    LLM が返すスキーマ違反（文字列リスト、単一 dict、件数数値など）の劣化表現を受理する。
    - list の場合: dict 要素のみを抽出
    - dict の場合: 単一 dict を 1 件の list にラップ
    - それ以外: 空 list
    """
    if isinstance(raw, list):
        return [d for d in raw if isinstance(d, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _merge_fix_records(st: dict, fix: dict, pr: int) -> dict:
    """fix の戻り値を正規化して記録へ反映し、ラウンドの fix 辞書を返す。"""
    normalized = _normalize_fix_result(fix)
    st["rounds"][-1]["fix"] = _build_round_fix(normalized)
    st["rounds"][-1]["ended_at"] = review_lib._now()
    _record_fix_history(st, normalized, pr)
    return st["rounds"][-1]["fix"]


def _resolve_fix_aliases(fix: dict) -> tuple[object, object]:
    """fix の commit と fixed 件数を正規 key と別名から解決する。"""
    fix_commit = fix.get("fix_commit") or fix.get("commit_sha")
    fixed_count = fix.get("fixed_count")
    if fixed_count is None:
        fixed_count = fix.get("fixed", 0)
    return fix_commit, fixed_count


def _normalize_deferred_like(raw: object) -> tuple[list[dict], int]:
    """deferred の項目と、劣化表現を考慮した保存件数を返す。"""
    items = _normalize_dict_items(raw)
    if isinstance(raw, (list, dict)):
        return items, len(items)
    return items, _count(raw)


def _normalize_fix_result(fix: dict) -> dict[str, Any]:
    """fix の戻り値から別名と劣化表現を吸収し、記録へ写す値にそろえる。"""
    # key 名 fallback (サブエージェントが別名で書いた場合の救済)。
    # 正規は fix_commit / fixed_count、別名は commit_sha / fixed のみ受理する。
    fix_commit, fixed_count = _resolve_fix_aliases(fix)

    # deferred は list が正だが、LLM がスキーマを無視して文字列リスト
    # (例: ["nit: ..."]) や単一 dict、int(件数) を返すケースがある。後段の
    # deferred_nits 展開ループは dict 以外をスキップするため、まず dict 要素のみへ
    # 正規化する (単一 dict は 1 件として包む)。
    # 保存件数の単一整合ルール:
    #   - 構造化データ (list / dict) は per-item を保持できるので、展開件数
    #     (len(_deferred_nits)) を保存し deferred_nits の件数と一致させる。
    #   - int / 数値文字列は per-item データを失った「劣化表現」なので、件数を
    #     失わないよう _count() の値を保存する (展開はできないので nits は空)。
    _deferred_nits, _deferred_count = _normalize_deferred_like(fix.get("deferred"))

    # 却下も同じ正規化を通す。**件数だけが返る劣化表現（int）では per-item を作れない**
    # ため、そのときは記録を空にし、件数は `_count()` の値で残す。
    _rejected_items = _normalize_dict_items(fix.get("rejected"))

    return {
        "commit": fix_commit,
        "fixed": fixed_count,
        "deferred": _deferred_count,
        "deferred_nits": _deferred_nits,
        "rejected": _count(fix.get("rejected")),
        "rejected_items": _rejected_items,
        "resolved_threads": fix.get("resolved_threads"),
        "ci": fix.get("ci_status"),
        "ci_failed_checks": fix.get("ci_failed_checks", []) or [],
        "ci_note": fix.get("ci_note"),
        "by_severity": fix.get("by_severity", {}),
    }


def _build_round_fix(normalized: dict[str, Any]) -> dict[str, Any]:
    """正規化済みの値から `rounds[-1].fix` に置く辞書を作る。"""
    resolved_threads = normalized["resolved_threads"]
    return {
        "commit": normalized["commit"],
        "fixed": normalized["fixed"],
        # deferred は上記の単一整合ルールで算出した件数を保存する。
        # resolved_threads は件数しか保存せず後段ループが無いため _count() で可。
        # **rejected は per-item の記録を持つが、件数は raw のまま数える**（#156）。
        # dict にできない要素も却下 1 件であり、記録に残せないことと、却下が
        # 何件あったかを失うことは別である。そのため deferred と違い、この件数と
        # `rejected_findings` の件数は一致しないことがある。
        "deferred": normalized["deferred"],
        "rejected": normalized["rejected"],
        "resolved_threads": _count(resolved_threads),
        # 次のラウンドの開始時に、申告どおり Resolve されたかを突き合わせる。
        "resolved_thread_ids": _thread_ids(resolved_threads),
        # **位置は効果の測定だけが読む**（#156）。収束ループの判断は増やさない。
        # ここで写さないと、上限の方式（`oracle`）を後から計算できない。
        "resolved_thread_positions": _thread_positions(resolved_threads),
        "ci": normalized["ci"],
        "ci_failed_checks": normalized["ci_failed_checks"],
        "ci_note": normalized["ci_note"],
        "by_severity": normalized["by_severity"],
    }


def _record_fix_history(st: dict, normalized: dict[str, Any], pr: int) -> None:
    """引継ぎの消化と、見送り・却下の項目別履歴を state へ積む。"""
    round_no = st["rounds"][-1]["round"]
    # 引き継いだ指摘は、修正の工程を 1 度通した時点で収束の抑止から外す。
    # 残りは最終スイープ (Step 7.5) が受け持つ。
    carried = findings_mod._carried_over_pending(st)
    if carried is not None:
        carried["fixed_in_round"] = round_no
        review_lib.info(f"↻ 引き継いだ指摘を round {round_no} の修正の工程へ通しました")
    for d in normalized["deferred_nits"]:
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})

    # **却下した指摘も per-item で残す**（#156）。`rounds[].fix.rejected` の件数は
    # ラウンドごとの報告が読むため残し、こちらは理由と位置を持つ記録として積む。
    # **項目が欠けた要素も落とさない。** 落とすと却下そのものが記録から消える。
    rejected_findings = st.setdefault("rejected_findings", [])
    for r in normalized["rejected_items"]:
        rejected_findings.append({**r, "pr": pr, "round": round_no})


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 5 後段 — fix サブエージェント戻り値を state にマージ + CI 分類。

    Exit code: 0=continue, 3=ci-code-fail (final=error)
    """
    pr = args.pr

    # state を先に読み、fallback 検証用の round 開始時刻を取得する
    # (round 開始前の古いファイルや、別リポジトリの同番号 PR の戻り値を
    # 誤マージするのを防ぐ)。
    st = store._load(pr)
    if not st.get("rounds"):
        review_lib.die("state.rounds が空。`state.py start-round` を先に呼んでください", code=3)
    round_started_ts = fix_result._round_started_unixtime(st["rounds"][-1])

    fix = fix_result._read_fix_result(pr, args.file, round_started_ts)

    # **送信と投稿は取り込む側が行う**（#730）。修正の担当はコミットまでで止まる。
    # 送れない・報告されたコミットが送り先に載っていないときは、記録も投稿もせずに
    # 止まる。同じ取り込みをやり直せば、同じ手順を最初から通る。
    commit = fix.get("fix_commit") or fix.get("commit_sha")
    pushed = result_posts.push_fix(str(st.get("worktree_path") or ""),
                                   str(st.get("head_branch") or ""), commit)
    if not pushed.ok:
        review_lib.die(f"修正を送れないか、報告されたコミットが送り先に載っていません: {pushed.detail}")
    print(f"PUSHED={1 if pushed.pushed else 0} COMMIT_ON_HEAD={1 if pushed.contains else 0}")

    round_fix = _merge_fix_records(st, fix, pr)
    store._save(pr, st)

    posted = result_posts.post_fix(
        posts._queue(pr), fix[fix_result.FIX_SOURCE_KEY], str(st.get("repo") or ""),
        int(st.get("current_pr") or pr), round_no=st["rounds"][-1].get("round"),
        actor=str(st.get("viewer_login") or "") or None)
    st["rounds"][-1]["fix"]["summary_comment_url"] = posted.summary_url
    store._save(pr, st)
    if posted.summary_url:
        print(f"POSTED summary_url={posted.summary_url}")
    print(f"REPLIED={posted.replied} RESOLVED={posted.resolved} QUEUED={posted.queued}"
          f" DROPPED={posted.dropped}")
    if posted.dropped and not posted.failed:
        review_lib.info(f"⚠️ 送れない項目を {posted.dropped} 件飛ばしました ({posted.detail})")
    if posted.failed:
        review_lib.die(f"返信・決着・まとめを投稿できませんでした ({posted.detail})")

    # CI 分類
    if (fix.get("ci_status") or "").upper() != "FAILURE":
        review_lib.info(f"✅ fix マージ完了 (commit={round_fix['commit']} fixed={round_fix['fixed']})")
        return

    # 振り分けは `_classify_ci` が 1 か所で持つ。ここが読むのは修正の担当が申告した
    # 失敗の名前で、進行側が照会し直す段ではない。申告は完了した失敗として渡す。
    failed = fix.get("ci_failed_checks") or []
    classified = ci_mod._classify_failed_names(failed)

    if classified.code_failed:
        st["final"] = "error"
        st["ended_at"] = review_lib._now()
        store._save(pr, st)
        review_lib.die(f"コード関連 CI 失敗。中断: {failed}", code=3)

    # meta only: 継続
    note = f"メタチェックのみ失敗: {failed} — コードと無関係のため継続"
    st["rounds"][-1]["fix"]["ci_note"] = note
    store._save(pr, st)
    review_lib.info(f"⚠ メタチェックのみ失敗 ({failed}) — 継続")
