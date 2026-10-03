"""改修計画の置き場所と本文（#436 決定 6）。

**改修計画は実行の記録であって、リポジトリの知識ではない。** 既定の置き場所は
**対象の Pull Request のコメント 1 件**で、公開のたびに同じコメントを
編集する。ファイルとして残すのは `--plan-file` を明示したときだけである。

| 置き場所 | URL の安定 | 差分に混ざるか | 更新の手数 |
| --- | --- | --- | --- |
| **Pull Request のコメント 1 件**（既定） | **永続** | 混ざらない | 編集 1 回 |
| ファイル（`--plan-file`） | `<ref>` に依存。ブランチが消えると切れる | **混ざる** | コミットと push |
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import mdtable

import statefile

from . import die, info, ledger, timeline
from .codemetrics_view import record_lines
from .paths import sh
from .items import item_label
from .vocabulary import ITEM_STATUS_LABELS

# 置き場所の 3 態。**宣言の無い状態ファイルは書き出し先から読む**（この版より前で
# 始めた実行を、再開の時点でコメントへ移さないため）。
PLAN_COMMENT = "comment"
PLAN_FILE = "file"
PLAN_NONE = "none"


def plan_mode(state: dict[str, Any]) -> str:
    """この実行の改修計画の置き場所。"""
    declared = str(state.get("plan_mode") or "").strip()
    if declared in (PLAN_COMMENT, PLAN_FILE, PLAN_NONE):
        return declared
    return PLAN_FILE if state.get("plan_file") else PLAN_NONE


def plan_comment_marker(state: dict[str, Any]) -> str:
    """コメントを探すための目印。**本文の先頭に置く。**

    状態ファイルの記録が失われても、この目印で同じコメントを引き当てられる。
    引き当てられないと、ラウンドのたびに新しいコメントが積まれる。
    """
    return f"<!-- cross-refactoring plan rf{state.get('id')} -->"


def plan_comment_body(state: dict[str, Any], unpublished: Optional[bool] = None) -> str:
    """Pull Request のコメントとして投稿する本文。公開の行と書き直した時刻はコメントだけに置く。

    ファイルの置き場所（`--plan-file`）へは書かない。書くと、公開のたびに本文が変わってコミットが積まれる。
    """
    head = [f"- 公開: {publication_line(state, unpublished)}", f"- 書き直した時刻: {statefile.now()}"]
    return plan_comment_marker(state) + "\n\n" + format_plan(state, head)


def counts_line(state: dict[str, Any]) -> str:
    """件数の行。4 つの件数は結果 JSON の `metrics` と同じ集計（`ledger.tally`）から出る（I1）。"""
    t = ledger.tally(state)
    gate = (state.get("final_gate") or {}).get("status") or "未実行"
    return f"採用 {t.adopted}・未確認 {t.unconfirmed}・取り消し {t.reverted}・見送り {t.deferred}（最終ゲート: {gate}）"


def publication_line(state: dict[str, Any], unpublished: Optional[bool] = None) -> str:
    """公開の行（`publication` の最後の試み）。`unpublished` が真なら未公開の改善項目があることを添える。"""
    pub = state.get("publication") or {}
    status = pub.get("status")
    sha = str(pub.get("sha") or "")[:12]
    if status == "pushed":
        return f"{sha} を push した" + ("。その後の手元のコミットは未公開" if unpublished else "")
    if status == "refused":
        reason = " / ".join(str(pub.get("reason") or "理由の記録なし").splitlines())
        return f"push できなかった（{reason}）" + ("。未公開の改善項目がある" if unpublished else "")
    if status == "observed":
        return f"origin の {pub.get('head') or state.get('head_branch')} は {sha}（プランの外の取り消しを読んだ地点）"
    return "まだ push していない"


def _comment_payload(out: str) -> Optional[dict[str, Any]]:
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) and body.get("id") else None


def _find_plan_comment(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """目印を持つ既存のコメントを探す。見つからなければ `None`。"""
    repo, pr = state.get("repo"), state.get("current_pr")
    out = sh(
        ["gh", "api", f"repos/{repo}/issues/{int(pr)}/comments", "--paginate"],
        check=False,
    )
    if not out:
        return None
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, list):
        return None
    marker = plan_comment_marker(state)
    for comment in body:
        if isinstance(comment, dict) and marker in str(comment.get("body") or ""):
            return comment
    return None


def publish_plan_comment(state: dict[str, Any], unpublished: Optional[bool] = None) -> Optional[str]:
    """改修計画のコメントを作るか、既にあるものを編集して URL を返す（`write_plan_comment` の URL）。"""
    return write_plan_comment(state, unpublished)[1]


def write_plan_comment(state: dict[str, Any], unpublished: Optional[bool] = None) -> tuple[str, Optional[str]]:
    """改修計画のコメントを作るか編集し、`(結果, URL)` を返す。結果は `created`・`updated`・`skipped`・`failed`。

    **投稿できなくても進行は止めない。** 改修計画は実行の記録であり、これが
    残らないことと、変更が検証を通っていないことは別である。**通らなかったことは
    出力へ残す**（外へ出す文章の URL が「作成できていない」と書かれる）。
    `gh api` は、コメントの ID を持っていれば編集 1 回、無ければ検索 1 回と作成か編集 1 回（I8）。
    """
    if plan_mode(state) != PLAN_COMMENT:
        return "skipped", None
    repo = str(state.get("repo") or "")
    pr = state.get("current_pr")
    if not repo or not pr:
        return "skipped", None

    known = state.get("plan_comment") or {}
    comment_id = known.get("id")
    if not comment_id:
        found = _find_plan_comment(state)
        comment_id = found.get("id") if found else None

    body = plan_comment_body(state, unpublished)
    if comment_id:
        out = sh(
            ["gh", "api", f"repos/{repo}/issues/comments/{comment_id}", "-X", "PATCH", "-f", f"body={body}"],
            check=False,
        )
    else:
        out = sh(
            ["gh", "api", f"repos/{repo}/issues/{int(pr)}/comments", "-X", "POST", "-f", f"body={body}"],
            check=False,
        )
    payload = _comment_payload(out)
    if payload is None:
        info("⚠ 改修計画のコメントを投稿できませんでした（進行は止めません）")
        return "failed", known.get("url")
    record = {"id": payload["id"], "url": str(payload.get("html_url") or "")}
    state["plan_comment"] = record
    info(f"📝 改修計画を更新しました: {record['url']}")
    return ("updated" if comment_id else "created"), record["url"]


def normalize_plan_file(value: Optional[str]) -> str:
    """改修計画の書き出し先を検証して正規化する。空文字は「記録しない」。

    **作業ディレクトリの外へ書かせない。** 進行側は利用者のリポジトリを触るので、
    絶対パスと親へ抜ける経路は受け取った時点で拒む。

    正規化するのは、判定に使うパスを git の出力と揃えるためでもある。
    `./issues/plan.md` のまま持つと、`git status` が返す `issues/plan.md` と
    一致せず、公開のコミットメッセージが取り違えられる。
    """
    rel = str(value or "").strip()
    if not rel:
        return ""
    if os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
        die(f"--plan-file には相対パスを指定してください: {rel}", code=4)
    normalized = os.path.normpath(rel)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        die(
            f"--plan-file が作業ディレクトリの外を指しています: {rel}",
            code=4,
        )
    return normalized


def baseline_line(baseline: dict[str, Any]) -> str:
    """着手前の全体のテストの結果（通過か失敗・秒・HEAD の短い SHA）。報告と改修計画が共有する。

    危険フラグの全体のテストが落ちたとき、変更が原因か元からの失敗かを読む基準になる。
    """
    status = {"green": "通過", "red": "失敗"}.get(str(baseline.get("status") or ""), "—")
    seconds = baseline.get("seconds")
    head = str(baseline.get("head") or "")[:7] or "—"
    return f"{status}（{'—' if seconds is None else f'{seconds} 秒'} / HEAD {head}）"


def existing_failures_line(baseline: dict[str, Any]) -> str:
    """既存失敗の件数。JUnit を読めなかったときは理由。"""
    ids = baseline.get("existing_failures")
    if ids is None:
        return f"読めない（{baseline.get('existing_failures_reason') or '—'}）" if baseline.get("status") == "red" else "0"
    return str(len(ids))


def strategy_lines(state: dict[str, Any]) -> list[str]:
    """テストの戦略・根拠・範囲テストの雛形・最終ゲートへ寄せた危険フラグ（#1334）。報告と改修計画が共有する。"""
    strategy = state.get("strategy") or {}
    templates = [
        f"{s['scope_command']}（静的解析）" if s.get("kind") == "lint" else s["scope_command"]
        for s in strategy.get("suites") or []
        if s.get("scope_command")
    ]
    deferred = (state.get("whole_test") or {}).get("deferred") or {}
    lines = [
        f"- テストの戦略: {strategy.get('name') or '—'}（根拠 {strategy.get('source') or '—'}）",
        f"- 範囲テストの雛形: {' / '.join(templates) or (strategy.get('round_command') or '—')}",
    ]
    # 行わなかったこと（テストの種別の suite が無い戦略。#1483 AC16）。
    lines.extend(f"- 行わなかったこと: {note}" for note in state.get("plan_notes") or [])
    if deferred:
        lines.append(
            f"- 最終ゲートへ寄せた危険フラグ: {', '.join(deferred.get('flags') or [])}（項目 {', '.join(deferred.get('items') or [])}）"
        )
    return lines


def format_plan(state: dict[str, Any], head: Optional[list[str]] = None) -> str:
    """改修計画の本文を組み立てる。**同じ状態と `head` からは同じ本文が出る。**

    冒頭に件数の行を置き、`head`（コメントの公開の行と書き直した時刻）を続ける。

    提案の理由と手順は状態ファイルにしか残らず、そのディレクトリは差分から
    除外される。Pull Request を読む側からは、なぜ直したのかも、どう直す改修計画
    だったのかも見えない。ここで差分の中へ置く。
    """
    baseline = state.get("baseline_test") or {}
    plan = state.get("plan") or {}
    lines = [
        f"# 改修計画 — {state['repo']} #{state['current_pr']}",
        "",
        "`/ndf:cross-refactoring` が提案し、改修計画し、適用した改善項目の記録である。",
        "理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。",
        "",
        f"- 件数: {counts_line(state)}",
        *(head or []),
        f"- 対象範囲: {', '.join(state.get('target_scope') or []) or '（未指定）'}",
        *strategy_lines(state),
        f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}（{baseline.get('mode') or 'whole'}）",
        f"- 着手前のテストの結果: {baseline_line(baseline)}",
        f"- 既存失敗の件数: {existing_failures_line(baseline)}",
        f"- 想定最大時間: {state.get('budget_minutes')} 分 / 改修計画の時点で使えた時間: {plan.get('available_minutes', '—')} 分",
        f"- 実装担当: {state.get('implementer') or '—'}",
        "",
        "## 改善項目",
        "",
    ]
    items = state.get("items") or []
    if not items:
        lines.extend(["（採用した改善項目なし）", ""])
    for item in items:
        lines.extend(_plan_item_section(state, item))
    lines.extend(record_lines(state))
    lines.extend(limits_section(state.get("limits") or {}))
    lines.extend(_plan_deferred_section(state))
    return "\n".join(lines).rstrip() + "\n"


# 上限の表の行（決定 24）。式は `docs/02-plan-and-implement.md` の「締め切り」の節にある。
_LIMIT_ROWS = (
    ("propose_end_at", "提案の枠の終わり（監視の上限 = 残り + 余裕）"),
    ("plan_end_at", "改修計画の枠の終わり（同上）"),
    ("add_tests_end_at", "テストの追加の終わり = 最後の項目の完了の締め切り"),
    ("implement_end_at", "実装の終わり = 最後の項目の完了の締め切り"),
    ("fix_end_at", "直しの試行の打ち切り"),
    ("final_end_at", "最終ゲートの修正の打ち切り（想定最大時間の終わり）"),
    (
        "stop_revert_end_at",
        f"打ち切りの後の取り消し（案 A）の締め切り = 最終ゲートの修正の打ち切り + {timeline.STOP_REVERT_SHARE:.2f}·B（工程の 1 つとして起動したとき）",
    ),
    ("final_fix_seconds", "最終ゲートの修正の 1 回目に必ず渡す長さ（秒。予備時間の final_fix）"),
    ("measure_timeout", "指標の測定の上限（秒。提案の枠の中から割く）"),
    ("init_test_timeout", "着手前のテスト 1 回の上限（秒）"),
    ("test_timeout", "範囲テスト 1 回の上限（秒）"),
    ("whole_timeout", "手元の全体テスト 1 回の上限（秒）"),
    ("ci_wait_timeout", "CI の待ちの上限（秒）"),
    ("margin_seconds", "余裕（秒。手順の上限と CLI の上限に足す）"),
)


def limits_section(limits: dict[str, Any]) -> list[str]:
    """実行時の値の表（テストの時間）。読み手が実行の前にすべての時刻を見られるようにする（決定 24）。

    無音の打ち切りは手順の監視の上限と同じ値のため、行を分けない。上限を出した入力（`basis`）を並べる。
    """
    if not limits:
        return []
    rows = [(label, "—" if limits.get(key) is None else str(limits[key])) for key, label in _LIMIT_ROWS]
    lines = ["## 時間の上限", "", mdtable.table_markdown(["値", "中身"], rows)]
    basis = limits.get("basis") or {}
    if basis:
        lines.extend(
            [
                "",
                f"入力: 予算 {basis.get('budget_minutes')} 分 / 全体テストの所要 w {basis.get('w')}（{basis.get('w_source') or '—'}）"
                f" / 着手前の実測 x {basis.get('x')} / CI の壁時計 c {basis.get('c')} / 戦略 {basis.get('strategy')}",
            ]
        )
    lines.extend(["", "無音の打ち切りは手順の監視の上限と同じ値である。項目ごとの締め切りは各項目の節にある。", ""])
    return lines


def _plan_deferred_section(state: dict[str, Any]) -> list[str]:
    """見送った提案の内訳。**内訳を持つのはここだけである**（決定 6-b）。

    Pull Request へ出す他の文章は件数だけを述べ、内訳はこの節へ譲る。同じ一覧を
    2 か所に置くと、片方だけが古くなる。
    """
    deferred = state.get("deferred_items") or []
    lines = ["## 見送った提案", ""]
    if not deferred:
        lines.extend(["（なし）", ""])
        return lines
    rows = [
        (
            f"`{item_label(item)}`",
            item.get("smell") or "—",
            str(item.get("defer_reason", "—")),
            " / ".join(item.get("mvv_basis") or []) or "—",
            item.get("detail") or "—",
        )
        for item in deferred
    ]
    lines.extend([mdtable.table_markdown(["対象", "兆候", "理由", "根拠（MVV）", "補足"], rows), ""])
    return lines


def status_label(state: dict[str, Any], item: dict[str, Any]) -> str:
    """項目の表示の状態（`ledger.display_status`）を読む側の語へ直す。語の無い状態はそのまま返す。"""
    shown = ledger.display_status(state, item)
    return ITEM_STATUS_LABELS.get(shown, shown)


def _plan_item_section(state: dict[str, Any], item: dict[str, Any]) -> list[str]:
    """項目 1 件の見出し・要約表・理由・手順。状態は表示の状態（`ledger.display_status`）で書く。"""
    status = status_label(state, item) or "—"
    commits = item.get("commits") or {}
    count = len([s for s in (commits.get("test"), commits.get("implement"), *(commits.get("fix") or [])) if s])
    lines = [
        f"### {item['id']} — `{item_label(item)}`",
        "",
        mdtable.table_markdown(
            ["兆候", "手法", "重要度", "等級", "提案元", "状態", "コミット"],
            [
                (
                    item.get("smell") or "—",
                    item.get("technique") or "—",
                    item.get("severity") or "—",
                    item.get("tier") or "—",
                    " / ".join(item.get("proposed_by") or []) or "—",
                    status,
                    count,
                )
            ],
        ),
        "",
        f"**なぜ**: {item.get('rationale') or '（記録なし）'}",
        "",
        f"**手順**: {item.get('plan') or '（記録なし）'}",
        "",
    ]
    deadlines = [
        f"{label} {item[key]}"
        for key, label in (("test_start_deadline", "テストの追加の着手"), ("start_deadline", "実装の着手"))
        if item.get(key)
    ]
    if deadlines:
        lines.extend([f"**締め切り**: {' / '.join(deadlines)}", ""])
    if item.get("review_test_judgements"):
        paths = ", ".join(f"`{p}`" for p in item["review_test_judgements"])
        lines.extend([f"**レビューで確かめるテストの差分**: {paths}（機械で期待値が変わったか決まらなかった）", ""])
    if item.get("failure_reason"):
        lines.extend([f"**取り消した理由**: {item['failure_reason']}", ""])
    return lines
