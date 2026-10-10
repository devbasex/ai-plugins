"""本番の配布の後に書くリリース記録の組み立てと投稿（`release-steps.py` の record から分けた。#1273）。

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


def body_of(root, plugin, ver, tag, prev_tag, prs):
    """リリース記録の本文を返す。"""
    prev = prev_tag[len(f"{plugin}--v") :] if prev_tag else None
    at = tag_time_jst(root, tag)
    return dist_record.format_record(
        prev,
        ver,
        prs,
        stage_note=f"承認ゲート 2 の承認の後、{at + ' に ' if at else ''}{tag} を出した",
        version_note=f"タグ {tag}。直前はタグ {prev_tag}" if prev_tag else f"タグ {tag}。直前の正式版のタグは無い",
    )


def promote_body_of(n, head, base, prev_sha, sha, prs):
    """昇格の Pull Request（head → base）のマージで本番へ出たときのリリース記録の本文。版は `<本番チャネル> <短い SHA>`。"""
    return dist_record.format_record(
        f"{base} {prev_sha}" if prev_sha else None,
        f"{base} {sha}",
        prs,
        stage_note=f"承認ゲート 2 の承認の後、昇格の Pull Request #{n}（{head} → {base}）をマージした",
        version_note=f"#{n} のマージのコミット。直前は {base} の {prev_sha}" if prev_sha else f"#{n} のマージのコミット",
    )


def parent_of(root, base, sha):
    """マージのコミットの 1 つ目の親（直前の版）の短い SHA。読めなければ None。"""
    git(root, "fetch", "-q", "origin", base, check=False)
    return git(root, "rev-parse", "--short", f"{sha}^1", check=False).stdout.strip() or None


def write(root, n, ver, prev, body, prs, label):
    """PR #n へリリース記録を書き、(要約, items, metrics) を返す。同じ記録が既にあれば書かない。"""
    text, url = release_pr_text(root, n)
    metrics = {"release_pr": n, "release_pr_url": url, "version": ver, "prev_version": prev, "sprint_prs": list(prs)}
    if exists(text, ver, prs):
        return f"#{n} に {label} のリリース記録は既にある", [{"kind": "comment", "name": f"#{n}", "result": "exists"}], metrics
    post_comment(root, n, body)
    return f"#{n} へ {label} のリリース記録を書いた", [{"kind": "comment", "name": f"#{n}", "result": "posted"}], metrics


def exists(text, ver, prs):
    """PR の最後のリリース記録が同じ版・同じスプリントの PR の本番の記録なら True。"""
    last = dist_record.parse_record(text)
    return bool(last["found"] and (last["stage"] or "").startswith("本番") and last["version"] == ver and last["sprint_prs"] == list(prs))


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
