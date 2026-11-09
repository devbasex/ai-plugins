"""リリースの後に書くリリース記録の組み立てと投稿（`release-steps.py` の record から分けた。#1273）。

記録の形は `lib/dist_record.py` が持つ。ここはタグの時刻・PR の本文とコメントの読み取り・同じ記録の有無の判定と
コメントの投稿を持ち、出す（emit）のは呼び手である。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import dist_record
import gh_parts
from step_result import StepError, git

JST = timezone(timedelta(hours=9))


def tag_time_jst(root, tag):
    """注釈付きタグの作成時刻（軽量タグはコミットの時刻）を JST の `YYYY-MM-DD HH:MM` で返す。読めなければ None。"""
    out = git(root, "for-each-ref", "--format=%(creatordate:unix)", f"refs/tags/{tag}", check=False).stdout.strip()
    return datetime.fromtimestamp(int(out), JST).strftime("%Y-%m-%d %H:%M") if out.isdigit() else None


def release_pr_text(root, n):
    """本番のリリースの PR の本文とコメントを投稿の順に 1 つの文字列にし、URL と並べて返す。読めなければ 2 で止める。"""
    r = gh_parts.view_json("pr", n, "body,comments,url", cwd=str(root))
    if r.returncode != 0:
        raise StepError(f"gh pr view {n} が失敗: {r.stderr.strip()[:300]}", 2)
    try:
        d = json.loads(r.stdout or "null") or {}
    except ValueError:
        raise StepError(f"gh pr view {n} の出力を読めない", 2)
    parts = [d.get("body") or ""] + [(c or {}).get("body") or "" for c in d.get("comments") or []]
    return "\n".join(parts), d.get("url") or ""


def cd_fields(cd) -> dict:
    """CD の結果（`release_lib/cd_result.py` の CdResult か None）を format_record の引数へ直す。"""
    if cd is None:
        return {}
    urls = " ".join(dict.fromkeys(e["url"] for e in cd.evidence if e.get("url")))
    failed = (cd.reason or "CD が失敗した") if cd.value == "failure" else None
    return {"cd_line": dist_record.cd_result_line(cd.value, cd.revision, " ".join(x for x in (urls, cd.reason) if x)), "failed": failed}


def body_of(root, plugin, ver, tag, prev_tag, prs, cd=None, by_cd=False):
    """リリース記録の本文を返す。`by_cd` なら公開物は CD が作るため、タグの時刻を事実として書かない。"""
    prev = prev_tag[len(f"{plugin}--v") :] if prev_tag else None
    at = None if by_cd else tag_time_jst(root, tag)
    done = f"{tag} の公開は CD が担う" if by_cd else f"{at + ' に ' if at else ''}{tag} を出した"
    return dist_record.format_record(
        prev,
        ver,
        prs,
        stage_note=f"リリース承認ゲートの承認の後、{done}",
        version_note=f"タグ {tag}。直前はタグ {prev_tag}" if prev_tag else f"タグ {tag}。直前の正式版のタグは無い",
        **cd_fields(cd),
    )


def promote_body_of(n, head, base, prev_sha, sha, prs, cd=None):
    """昇格の Pull Request（head → base）のマージで本番へ出たときのリリース記録の本文。版は `<本番チャネル> <短い SHA>`。"""
    return dist_record.format_record(
        f"{base} {prev_sha}" if prev_sha else None,
        f"{base} {sha}",
        prs,
        stage_note=f"リリース承認ゲートの承認の後、昇格の Pull Request #{n}（{head} → {base}）をマージした",
        version_note=f"#{n} のマージのコミット。直前は {base} の {prev_sha}" if prev_sha else f"#{n} のマージのコミット",
        **cd_fields(cd),
    )


def merged_body_of(n, base, prev_sha, sha, prs, cd=None):
    """Pull Request を本番チャネルへ直接マージして CD が届けたときのリリース記録の本文。版は `<本番チャネル> <短い SHA>`。"""
    return dist_record.format_record(
        f"{base} {prev_sha}" if prev_sha else None,
        f"{base} {sha}",
        prs,
        stage_note=f"Pull Request #{n} を本番チャネル {base} へマージした",
        version_note=f"#{n} のマージのコミット",
        **cd_fields(cd),
    )


def parent_of(root, base, sha):
    """マージのコミットの 1 つ目の親（直前の版）の短い SHA。読めなければ None。"""
    git(root, "fetch", "-q", "origin", base, check=False)
    return git(root, "rev-parse", "--short", f"{sha}^1", check=False).stdout.strip() or None


def write(root, n, ver, prev, body, prs, label, cd=None):
    """PR #n へリリース記録を書き、(要約, items, metrics) を返す。同じ記録が既にあれば書かない。"""
    text, url = release_pr_text(root, n)
    metrics = {"release_pr": n, "release_pr_url": url, "version": ver, "prev_version": prev, "sprint_prs": list(prs)}
    if cd is not None:
        metrics["cd_result"] = cd.value
    if exists(text, ver, prs, dist_record.CD_WORDS[cd.value] if cd else None):
        return f"#{n} に {label} のリリース記録は既にある", [{"kind": "comment", "name": f"#{n}", "result": "exists"}], metrics
    post_comment(root, n, body)
    return f"#{n} へ {label} のリリース記録を書いた", [{"kind": "comment", "name": f"#{n}", "result": "posted"}], metrics


def exists(text, ver, prs, cd_word=None):
    """PR の最後のリリース記録が同じ版・同じスプリントの PR・同じ CD の結果の本番の記録なら True（再実行の成功は書き足す）。"""
    last = dist_record.parse_record(text)
    same = last["found"] and (last["stage"] or "").startswith("本番") and last["version"] == ver and last["sprint_prs"] == list(prs)
    return bool(same and last["cd_result"] == cd_word)


def post_comment(root, n, body):
    """PR へ本文をコメントで書く。失敗は 1 で止める。"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        body_file = f.name
    try:
        p = gh_parts.gh(["pr", "comment", str(n), "--body-file", body_file], cwd=root)
    finally:
        os.unlink(body_file)
    if p.returncode != 0:
        raise StepError(f"gh pr comment {n} が失敗: {p.stderr.strip()[:300]}")
