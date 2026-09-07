"""改修計画の置き場所と本文（#436 決定 6）。

**改修計画は実行の記録であって、リポジトリの知識ではない。** 既定の置き場所は
**対象の Pull Request のコメント 1 件**で、ラウンドが進むたびに同じコメントを
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

from . import die, info
from .paths import _sh
from .rounds import TEST, item_kind, item_label
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
    """コメントを探すための印。**本文の先頭に置く。**

    状態ファイルの控えが失われても、この印で同じコメントを引き当てられる。
    引き当てられないと、ラウンドのたびに新しいコメントが積まれる。
    """
    return f"<!-- cross-refactoring plan rf{state.get('id')} -->"


def plan_comment_body(state: dict[str, Any]) -> str:
    """Pull Request のコメントとして投稿する本文。"""
    return plan_comment_marker(state) + "\n\n" + format_plan(state)


def _comment_payload(out: str) -> Optional[dict[str, Any]]:
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) and body.get("id") else None


def _find_plan_comment(state: dict[str, Any]) -> Optional[dict[str, Any]]:
    """印を持つ既存のコメントを探す。見つからなければ `None`。"""
    repo, pr = state.get("repo"), state.get("current_pr")
    out = _sh(
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


def publish_plan_comment(state: dict[str, Any]) -> Optional[str]:
    """改修計画のコメントを作るか、既にあるものを編集して URL を返す。

    **投稿できなくても進行は止めない。** 改修計画は実行の記録であり、これが
    残らないことと、変更が検証を通っていないことは別である。**通らなかったことは
    出力へ残す**（外へ出す文章の URL が「作成できていない」と書かれる）。
    """
    if plan_mode(state) != PLAN_COMMENT:
        return None
    repo = str(state.get("repo") or "")
    pr = state.get("current_pr")
    if not repo or not pr:
        return None

    known = state.get("plan_comment") or {}
    comment_id = known.get("id")
    if not comment_id:
        found = _find_plan_comment(state)
        comment_id = found.get("id") if found else None

    body = plan_comment_body(state)
    if comment_id:
        out = _sh(
            ["gh", "api", f"repos/{repo}/issues/comments/{comment_id}",
             "-X", "PATCH", "-f", f"body={body}"],
            check=False,
        )
    else:
        out = _sh(
            ["gh", "api", f"repos/{repo}/issues/{int(pr)}/comments",
             "-X", "POST", "-f", f"body={body}"],
            check=False,
        )
    payload = _comment_payload(out)
    if payload is None:
        info("⚠ 改修計画のコメントを投稿できませんでした（進行は止めません）")
        return known.get("url")
    record = {"id": payload["id"], "url": str(payload.get("html_url") or "")}
    state["plan_comment"] = record
    info(f"📝 改修計画を更新しました: {record['url']}")
    return record["url"]


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


def format_plan(state: dict[str, Any]) -> str:
    """改修計画の本文を組み立てる。**同じ状態からは同じ本文が出る。**

    提案の理由と手順は状態ファイルにしか残らず、そのディレクトリは差分から
    除外される。Pull Request を読む側からは、なぜ直したのかも、どう直す計画
    だったのかも見えない。ここで差分の中へ置く。
    """
    baseline = state.get("baseline_test") or {}
    lines = [
        f"# 改修計画 — {state['repo']} #{state['current_pr']}",
        "",
        "`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。",
        "理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。",
        "",
        f"- 対象範囲: {', '.join(state.get('target_scope') or []) or '（未指定）'}",
        f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}",
        "",
    ]
    for entry in state.get("rounds") or []:
        lines.extend(_plan_round_section(state, entry))
    if not (state.get("rounds") or []):
        lines.append("（改善項目なし）")
    lines.extend(_plan_deferred_section(state))
    return "\n".join(lines).rstrip() + "\n"


def _plan_deferred_section(state: dict[str, Any]) -> list[str]:
    """見送った項目の内訳。**内訳を持つのはここだけである**（決定 6-b）。

    Pull Request へ出す他の文章は件数だけを述べ、内訳はこの節へ譲る。同じ一覧を
    2 か所に置くと、片方だけが古くなる。
    """
    deferred = state.get("deferred_items") or []
    lines = ["## 見送った項目", ""]
    if not deferred:
        lines.extend(["（なし）", ""])
        return lines
    lines.extend([
        "| ラウンド | 対象 | 兆候・経路 | 理由 |",
        "| --- | --- | --- | --- |",
    ])
    for item in deferred:
        kind_label = item.get("case") if item_kind(item) == TEST else item.get("smell")
        lines.append(
            f"| {item.get('round', '—')} | `{item_label(item)}` | "
            f"{kind_label or '—'} | {item.get('defer_reason', '—')} |"
        )
    lines.append("")
    return lines


def _plan_round_section(state: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    """1 ラウンド分の見出しと、そのラウンドの改善項目を並べる。"""
    reviewers = " / ".join(entry.get("reviewers") or []) or "—"
    lines = [
        f"## ラウンド {entry['round']}"
        f"（実装 {entry.get('impl', '—')} / レビュー {reviewers}）",
        "",
    ]
    items = [i for i in state.get("items") or [] if i.get("round") == entry["round"]]
    if not items:
        lines.extend(["（採用した改善項目なし）", ""])
        return lines
    for item in items:
        lines.extend(_plan_item_section(item))
    return lines


def _plan_item_section(item: dict[str, Any]) -> list[str]:
    """項目 1 件の見出し・要約表・理由・手順。

    テスト項目は兆候と手法を持たない（決定 9）。代わりに固定する経路の種類と
    階層を同じ位置へ書く。
    """
    status = ITEM_STATUS_LABELS.get(item.get("status"), item.get("status") or "—")
    if item_kind(item) == TEST:
        first, second, severity = item.get("case"), item.get("level"), "—"
    else:
        first, second = item.get("smell"), item.get("technique")
        severity = item.get("severity") or "—"
    return [
        f"### {item['item_id']} — `{item_label(item)}`",
        "",
        "| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |",
        "| --- | --- | --- | --- | --- | ---: |",
        f"| {first or '—'} | {second or '—'} | {severity} | "
        f"{' / '.join(item.get('proposed_by') or []) or '—'} | {status} | "
        f"{len(item.get('commits') or [])} |",
        "",
        f"**なぜ**: {item.get('rationale') or '（記録なし）'}",
        "",
        f"**手順**: {item.get('plan') or '（記録なし）'}",
        "",
    ]
