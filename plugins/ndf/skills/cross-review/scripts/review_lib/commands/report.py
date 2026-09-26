"""副命令 `verify-sweep`・`unresolved-threads`・`report`（#1142 の C2）。

GitHub 上に残っている未解決の指摘を数え、完了報告の数を確かめる。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import sys
from typing import Any

import review_lib  # noqa: E402
import run_metrics  # noqa: E402
from review_lib import github, participants as participants_mod, posts, store  # noqa: E402


def _as_count(value: object) -> int:
    """申告された件数を整数として読む。読めない値は 0 として扱う。

    相手は LLM なので、文字列や `null` が入ることがある。読めない申告を
    「件数あり」と見なすと、突き合わせる相手が決まらないまま中断してしまう。
    """
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def cmd_unresolved_threads(args: argparse.Namespace) -> None:
    """Pull Request 上の未解決の指摘を数えて出力する。

    Exit code: 0=数えられた（0 件を含む）, 1=取得できなかった
    """
    st = store._load(args.pr)
    repo = str(st.get("repo") or "")
    pr = int(st.get("current_pr") or args.pr)
    threads = github._fetch_unresolved_threads(repo, pr)
    if threads is None:
        review_lib.die(
            f"未解決の指摘を取得できませんでした (repo={repo or '不明'}, PR #{pr})。"
            " 0 件として扱わず、取得し直してください"
        )
    print(f"UNRESOLVED_COUNT={len(threads)}")
    print(f"UNRESOLVED_THREAD_IDS={shlex.quote(' '.join(t['id'] for t in threads))}")
    for t in threads:
        review_lib.info(f"- {t['id']} {t.get('path', '')}:{t.get('line', '')}")


def _read_sweep_result(pr: int, file: str | None) -> dict[str, Any]:
    """最終スイープの結果ファイルを読む。読めない形はすべて中断する。

    結果が無いまま完了報告へ進むと、最終スイープを実行したかどうかが残らない。
    """
    path = pathlib.Path(file) if file else store._resolve_tmp_dir(pr) / f"sweep-pr{pr}-result.json"
    if not (path.exists() and path.stat().st_size > 0):
        review_lib.die(
            f"最終スイープの結果ファイルがありません ({path})。"
            " Step 7.5 を実行してから完了報告へ進んでください"
        )
    try:
        sweep = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        review_lib.die(f"最終スイープの結果ファイルを読めません ({path}): {exc}")
    if not isinstance(sweep, dict):
        review_lib.die(f"最終スイープの結果ファイルが dict ではありません ({path})")
    return sweep


def _reconcile_sweep_result(
    sweep: dict[str, Any], declared: int, threads: list | None
) -> tuple[int, bool, Any]:
    """申告された残件数と GitHub 上の未解決スレッドから (残件数, 照会できたか, 理由) を決める。"""
    if threads is None:
        review_lib.info(
            "⚠ 未解決の指摘を確認できません — 申告された残件数"
            f"（{declared} 件）をそのまま採用します"
        )
        remaining, verified = declared, False
    else:
        remaining, verified = len(threads), True

    reason = sweep.get("remaining_reason") or sweep.get("reason")
    if remaining > 0 and not reason:
        reason = "理由の記載なし"
    return remaining, verified, reason


def _sweep_record(
    sweep: dict[str, Any], declared: int, remaining: int, verified: bool, reason: Any
) -> dict[str, Any]:
    """状態ファイルへ保存する最終スイープの記録を作る。"""
    return {
        "declared_remaining_open": declared,
        "remaining_open": remaining,
        "remaining_reason": reason if remaining > 0 else None,
        "verified": verified,
        "resolved": sweep.get("resolved"),
        "fixed_in_sweep": sweep.get("fixed_in_sweep"),
        "commit": sweep.get("commit"),
        "checked_at": review_lib._now(),
    }


def cmd_verify_sweep(args: argparse.Namespace) -> None:
    """Step 7.5 後段 — 最終スイープの後に未解決の指摘が残っていないかを確かめる。

    結果ファイルに書かれた残件数は申告であり、GitHub 側の実数とは別のものである。
    申告のまま完了報告へ進むと、未解決の指摘が残ったまま「0 件」と報告される。

    Exit code: 0=残っていない, 6=残っている（件数と理由を完了報告へ入れる）, 1=エラー
    """
    pr = args.pr
    st = store._load(pr)
    sweep = _read_sweep_result(pr, args.file)

    declared = _as_count(sweep.get("remaining_open"))
    current_pr = int(st.get("current_pr") or pr)
    threads = github._fetch_unresolved_threads(str(st.get("repo") or ""), current_pr)
    remaining, verified, reason = _reconcile_sweep_result(sweep, declared, threads)
    st["sweep"] = _sweep_record(sweep, declared, remaining, verified, reason)
    store._save(pr, st)

    print(f"REMAINING_OPEN={remaining}")
    print(f"SWEEP_VERIFIED={'1' if verified else '0'}")
    if remaining == 0:
        review_lib.info("✅ 未解決の指摘は残っていません")
        sys.exit(0)
    review_lib.info(f"⚠ 未解決の指摘が {remaining} 件残っています（理由: {reason}）")
    sys.exit(6)


def _print_round_summary(rounds: list) -> None:
    """cmd_report のラウンドサマリ表を出す。"""
    print("## ラウンドサマリ")
    # **担当は 4 つの名前を取りうる。** 2 者を列にした表では、`claude` / `kiro` が
    # 担当したラウンドの結果が読めない。担当と判定を 1 つの列へまとめる。
    print("| round | PR | レビュー | fix | CI |")
    print("|---|---|---|---|---|")
    for r in rounds:
        reviewers = r.get("reviewers") or list(participants_mod.LEGACY_AGENTS)
        parts = []
        for name in reviewers:
            entry = r.get(name) or {}
            if entry.get("intent") == posts.NO_RESULT:
                # 結果なしは投稿の数を持たない。括弧には理由を出す（#729 の AC17）
                parts.append(f"{name}=NO_RESULT({entry.get('no_result_reason', '-')})")
            elif entry:
                parts.append(f"{name}={entry.get('intent', '-')} ({entry.get('comments', '-')})")
            else:
                parts.append(f"{name}=-")
        review_s = " / ".join(parts)
        fix = r.get("fix") or {}
        fix_s = "-"
        if fix:
            fix_s = f"{(fix.get('commit') or '')[:7]} ({fix.get('fixed', 0)} fixed, {fix.get('deferred', 0)} deferred)"
        ci_s = fix.get("ci") or "-"
        print(f"| {r['round']} | #{r['pr']} | {review_s} | {fix_s} | {ci_s} |")
    print()


def _print_participants(st: dict) -> None:
    """cmd_report の「参加した者」の節を出す（#727 の AC24）。

    途中から誰を外したか・誰が確認を通らなかったかを、完了報告だけで読めるようにする。
    参加者の記録を持たない状態ファイル（この変更の前に始めた実行）では「記録なし」と出す。
    """
    print("## 参加した者")
    p = st.get("participants")
    if not p:
        print("- 使える者: 記録なし")
        print()
        return

    def _names(values) -> str:
        return " / ".join(values) if values else "なし"

    unavailable = p.get("unavailable") or {}
    if unavailable:
        failed = " / ".join(f"{n}（{d}）" for n, d in unavailable.items())
    elif p.get("probe_skipped"):
        failed = "確認を飛ばした（NDF_SKIP_AUTH_CHECK）"
    else:
        failed = "なし"

    print(f"- 母集合: {_names(p.get('pool'))}")
    print(f"- 使える者: {_names(p.get('available'))}")
    print(f"- --exclude で外した者: {_names(p.get('excluded'))}")
    print(f"- --exclude で指定したが既定の母集合に無かった者: {_names(p.get('ignored_exclude'))}")
    print(f"- --include で足した者: {_names(p.get('included'))}")
    print(f"- 確認を通らなかった者: {failed}")
    print(f"- 席の埋め合わせ: {_names(p.get('fallback'))}")

    changes = st.get("resume_changes") or []
    if not changes:
        print("- 再開で変えた値: なし")
    else:
        print("- 再開で変えた値:")
        for c in changes:
            print(f"  - {c.get('at')} {c.get('field')}: "
                  f"{_resume_value(c.get('from'))} → {_resume_value(c.get('to'))}")
    print()


def _resume_value(value: object) -> str:
    """再開で変えた値の 1 つを 1 行へ収める。参加者の記録は使える者だけを出す。"""
    if isinstance(value, dict):
        available = value.get("available")
        if available is not None:
            return f"使える者={'/'.join(available) or 'なし'}"
        return "…"
    if isinstance(value, list):
        return ",".join(str(v) for v in value) or "なし"
    return str(value)


def _print_sweep(st: dict) -> None:
    """cmd_report の最終スイープの節を出す。"""
    sweep = st.get("sweep")
    if isinstance(sweep, dict):
        source = "GitHub 側で確認済み" if sweep.get("verified") else "申告のまま（確認できず）"
        print("## 最終スイープ")
        print(f"- 未解決の指摘: {sweep.get('remaining_open', 0)} 件（{source}）")
        if sweep.get("remaining_open"):
            print(f"- 残った理由: {sweep.get('remaining_reason') or '理由の記載なし'}")
        print()
    else:
        print("## 最終スイープ: 未検証")
        print("`state.py verify-sweep <PR>` を実行してから完了報告へ進んでください。")
        print()


def _print_deferred_nits(st: dict) -> None:
    """cmd_report の残 deferred nit の節を出す。"""
    nits = st.get("deferred_nits") or []
    if nits:
        print(f"## 残 deferred nit ({len(nits)} 件)")
        for n in nits:
            print(f"- [{n.get('severity')}] {n.get('path')}:{n.get('line')} — {n.get('summary')}")
        print()
        print("これらの nit を一括対応する場合は再度 `/ndf:fix <PR#>` を起動してください。")
        print()
    else:
        # **「なし」は nit の側の見出しである。** 却下した指摘の有無で出し分けると、
        # 一覧を出した直後に「なし」も出る（#535 のレビュー）。
        print("## 残 deferred nit: なし")
        print()


def _print_rejected(st: dict) -> None:
    """cmd_report の却下した指摘の節を出す。"""
    # **却下した指摘も一覧で出す**（#156）。次のラウンドで同じ論点が再提出されたとき、
    # 既に却下したものかどうかをここで照合できる。
    rejected = st.get("rejected_findings") or []
    if rejected:
        print(f"## 却下した指摘 ({len(rejected)} 件)")
        for r in rejected:
            print(
                f"- [round {r.get('round')}] [{r.get('severity')}] "
                f"{r.get('path')}:{r.get('line')} — {r.get('summary')}"
            )
            print(f"  却下の理由: {r.get('reason_for_rejection')}")


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — deferred nit + ラウンドサマリ表示。"""
    pr = args.pr
    st = store._load(pr)
    final = st.get("final") or "in_progress"
    total = len(st["rounds"])
    prs = [h["pr"] for h in st["pr_history"]]
    rotated = max(0, len(prs) - 1)

    print(f"## 最終ステータス: {final}")
    print(f"## 総ラウンド数: {total} / PR数: {len(prs)} (rotated {rotated} 回)")
    print()
    print("## PR 履歴")
    for h in st["pr_history"]:
        state_str = "closed" if h.get("closed_at") else "open"
        print(f"- #{h['pr']} ({state_str}, {h.get('rounds', 0)} rounds)")
    print()
    _print_participants(st)
    _print_round_summary(st["rounds"])
    _print_sweep(st)
    _print_deferred_nits(st)
    _print_rejected(st)
    # **最後の行に置く**（#662 の AC23）。作業ツリーを消した後に要約を探す手がかりになる。
    print()
    print(run_metrics.report_line(store._state_path(pr), st, "cross-review", store._summary_extra))
