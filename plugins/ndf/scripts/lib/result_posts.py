#!/usr/bin/env python3
"""結果ファイルを投稿へ変える層（#730 #583）。

**GitHub と git へ書くのは、レビューを回す側だけである。** 担当は指摘の控えと結果
ファイルを書いて終わり、修正の担当はコミットまでを行う。この層が、その 2 種類の
結果ファイルを読んで投稿を組み立て、待ち行列を通して送り、送信の応答を返す。

**本文は引数にも標準出力にも出さない。** どの入口もファイルのパスを受け取り、本文は
この層の中だけを通る。収束ループを駆動している側の応答に本文が載ると、文脈の予算の
決めに反する。

**2 つの口を持つ。** 収束ループから呼ぶときは取り込みがこの層を呼び、単独で修正を
行うときは同じ処理を部分命令として直接呼ぶ。入口のスクリプトを別に作らない。

```bash
python3 "$SCRIPTS/lib/result_posts.py" fix --repo <所有者>/<リポジトリ> --pr <番号> \\
  --result <結果ファイル> --head <ブランチ名> --worktree <作業ツリー>
```
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any, NamedTuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import post_queue  # noqa: E402
import statefile  # noqa: E402

# レビューの本文の先頭行。**照合の鍵はラウンドと席までの前方一致である**ため、判定の
# 語はこの行の末尾に置く（`post_queue.review_match_key`）。
REVIEW_HEAD = "## 🤖 cross-review | round {round_no} | {seat} | {event}"
# 差分の外を指すために総評へ移した指摘の見出し。
EVACUATED_HEAD = "### 差分の外を指す指摘"
# 修正のまとめの先頭行。ラウンドとコミットを鍵の幅（先頭 80 文字）の中へ入れる。
FIX_HEAD = "## 🔧 /ndf:fix サマリ | round {round_no} | commit {commit}"
FIX_HEAD_NO_ROUND = "## 🔧 /ndf:fix サマリ | commit {commit}"

TMP_DIRNAME = ".cross_review"


# ---------------- 読み取り ----------------


def _read_json(path: pathlib.Path | str | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("comments")
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)]


def _dict_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [i for i in raw if isinstance(i, dict)]
    return []


# ---------------- レビューの投稿 ----------------


def _line_no(value: Any) -> int | None:
    """行番号として読めるときだけ正の int を返す。

    **行は担当が書き出す外部入力である。** `"L42"` や `"40-45"` を `int()` へ渡すと
    例外でレビュー全体が失われるため、読めない値は位置を持たないものとして扱う。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip()) or None
    return None


def _can_be_inline(finding: dict[str, Any]) -> bool:
    """指す先を持つか。**差分に含まれるかどうかは見ない。**

    含まれるかは送ってみた応答が決める（設計の決定 9）。ここで見るのは、そもそも
    指す位置があるかどうかだけである。行が数として読めない指摘は総評へ回す。
    """
    return bool(finding.get("path")) and _line_no(finding.get("line")) is not None


def _evacuated_line(finding: dict[str, Any]) -> str:
    where = str(finding.get("path") or "")
    line = finding.get("line")
    head = f"`{where}:{line}`" if where and line is not None else (
        f"`{where}`" if where else "")
    severity = str(finding.get("severity") or "")
    mark = f" [{severity}]" if severity else ""
    text = str(finding.get("body") or "").strip()
    return f"- {head}{mark} {text}".strip()


def _review_body(payload: dict[str, Any], round_no: int, seat: str, intent: str,
                 evacuated: list[dict[str, Any]]) -> str:
    parts = [REVIEW_HEAD.format(round_no=round_no, seat=seat, event=intent)]
    summary = str(payload.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    if evacuated:
        parts.append(EVACUATED_HEAD)
        parts.append("\n".join(_evacuated_line(f) for f in evacuated))
    return "\n\n".join(parts) + "\n"


def _split_findings(findings: list[dict[str, Any]], evacuate_all: bool,
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """指摘をインラインと総評へ振り分ける。"""
    inline = [] if evacuate_all else [f for f in findings if _can_be_inline(f)]
    evacuated = [f for f in findings if f not in inline]
    return inline, evacuated


def _review_fields(body: str, posted_as: str, inline: list[dict[str, Any]],
                   head_sha: str | None, since: str | None) -> dict[str, Any]:
    """レビュー API へ渡す fields を組み立てる。"""
    fields: dict[str, Any] = {"body": body, "event": posted_as}
    if head_sha:
        fields["commit_id"] = head_sha
    if since:
        fields["since"] = since
    if inline:
        fields["comments"] = [
            {"path": str(f.get("path")), "line": _line_no(f.get("line")),
             "side": "RIGHT", "body": str(f.get("body") or "")}
            for f in inline
        ]
    return fields


def review_posts(payload_path: pathlib.Path | str, result_path: pathlib.Path | str,
                 repo: str, pr: int, round_no: int, seat: str,
                 head_sha: str | None, is_own_pr: bool,
                 evacuate_all: bool = False,
                 since: str | None = None) -> list[dict[str, Any]]:
    """指摘の控えと結果ファイルから、待ち行列へ積む項目の列を組み立てる。

    **判定の格下げはこの層が決める**（設計の決定 14）。自分の Pull Request へは変更を
    求めるレビューを送れないため、送る形だけを `COMMENT` へ落とす。本来の判定は
    先頭行と `extra` に残り、収束の判定はそちらを読む。

    `since` はそのラウンドが始まった時刻である。二度書かない照合をこの時刻より後に
    出たレビューへ絞るために、待ち行列の項目へ持たせる。

    `evacuate_all` が真のとき、位置を持つ指摘もすべて総評へ移す。送った要求が位置を
    解決できずに拒まれた後の送り直しで使う。
    """
    payload = _read_json(payload_path)
    result = _read_json(result_path)
    findings = _findings(payload)
    intent = str(result.get("event") or result.get("intent") or "COMMENT")
    posted_as = "COMMENT" if is_own_pr else intent

    inline, evacuated = _split_findings(findings, evacuate_all)
    body = _review_body(payload, round_no, seat, intent, evacuated)
    fields = _review_fields(body, posted_as, inline, head_sha, since)
    extra = {"ident": f"{seat}-r{round_no}", "agent": seat, "seat": seat,
             "round": round_no,
             "intent": intent, "posted_as": posted_as,
             "inline": len(inline), "body": len(evacuated)}
    return [{"kind": "review-post", "fields": fields, "extra": extra}]


class ReviewOutcome(NamedTuple):
    """レビューを 1 件送った結果。**本文は持たない。**"""

    review_url: str | None
    posted_inline: int
    posted_body: int
    queued: int
    findings: int
    failed: bool
    posted_as: str
    intent: str
    detail: str


def _find(items: list[dict[str, Any]], seq: Any) -> dict[str, Any] | None:
    return next((i for i in items if i.get("seq") == seq), None)


def _response_url(item: dict[str, Any] | None) -> str | None:
    response = (item or {}).get("response")
    if not isinstance(response, dict):
        return None
    url = response.get("html_url")
    if url:
        return str(url)
    if response.get("id"):
        return f"#pullrequestreview-{response['id']}"
    return None


def _write_destinations(payload_path: pathlib.Path | str, inline_count: int) -> str:
    """控えへ、送れた先を書き戻す。**決めるのは投稿する側である。**

    **原子的に書き、失敗は呼び出し元へ返す。** 半端な控えが残ると、再実行で読めずに
    空として扱われ、記録済みの指摘を 0 件で置き換える。戻り値は失敗の説明で、
    書けたときは空文字である。
    """
    path = pathlib.Path(payload_path)
    payload = _read_json(path)
    findings = _findings(payload)
    if not findings:
        return ""
    inline_ids = {id(f) for f in findings if _can_be_inline(f)} if inline_count else set()
    for f in findings:
        f["posted_to"] = "inline" if id(f) in inline_ids else "body"
    try:
        statefile.write_json_atomic(path, payload)
    except OSError as exc:
        return f"控えへ送れた先を書けない ({path.name}: {exc})"
    return ""


def post_review(queue: post_queue.Queue, payload_path: pathlib.Path | str,
                result_path: pathlib.Path | str, repo: str, pr: int, round_no: int,
                seat: str, head_sha: str | None, is_own_pr: bool,
                actor: str | None = None, since: str | None = None) -> ReviewOutcome:
    """レビューを 1 件、待ち行列を通して送る。

    **位置を解決できずに拒まれたら、その要求のインラインをすべて総評へ移して送り直す。**
    応答はどの項目が原因かを指さないため、1 件ずつの特定はできない（実測）。
    同じ状態で返る別の拒まれ方（判定の値の誤り・基準のコミットの誤り）は退避せず、
    失敗として残す。

    **退避するのは、今回積んだ項目が拒まれたときだけである。** 先に積まれていた項目
    （先客）の拒まれ方を今回分のものと取り違えると、先客を消して今回分を二重に積む。
    今回分が送れていない限り、控えへ送れた先を書かない。
    """
    findings = len(_findings(_read_json(payload_path)))
    item = review_posts(payload_path, result_path, repo, pr, round_no, seat,
                        head_sha, is_own_pr, since=since)[0]
    path = post_queue.enqueue(queue, item["kind"], repo, pr, item["fields"],
                              actor=actor, extra=item["extra"])
    seq = (post_queue.read_item(path) or {}).get("seq")
    flushed = queue.flush()

    ours_failed = flushed.failed is not None and flushed.failed.get("seq") == seq
    if ours_failed and post_queue.rejected_by_position(flushed.failed):
        queue.drop(flushed.failed.get("seq"))
        item = review_posts(payload_path, result_path, repo, pr, round_no, seat,
                            head_sha, is_own_pr, evacuate_all=True, since=since)[0]
        path = post_queue.enqueue(queue, item["kind"], repo, pr, item["fields"],
                                  actor=actor, extra=item["extra"])
        seq = (post_queue.read_item(path) or {}).get("seq")
        flushed = queue.flush()

    done = _find(flushed.sent, seq) or _find(flushed.skipped, seq)
    # 送れた後に控えを書けなければ、取り込みを止める。再実行は既投稿として照合し直す。
    note_error = _write_destinations(payload_path, item["extra"]["inline"]) if done else ""
    return ReviewOutcome(
        review_url=_response_url(done),
        posted_inline=item["extra"]["inline"] if done else 0,
        posted_body=item["extra"]["body"] if done else 0,
        queued=0 if done else 1,
        findings=findings,
        # 先客に止められた場合も、今回分は送れていない。上限だけは待てば流れる。
        failed=bool(note_error) or bool(not done and flushed.failed is not None
                                        and not flushed.rate_limited),
        posted_as=item["extra"]["posted_as"],
        intent=item["extra"]["intent"],
        detail=note_error or str((flushed.failed or {}).get("last_error") or ""),
    )


# ---------------- 修正の投稿 ----------------


def _reply(comment_id: Any, body: str) -> dict[str, Any] | None:
    try:
        target = int(comment_id)
    except (TypeError, ValueError):
        return None
    return {"kind": "review-reply", "fields": {"in_reply_to": target, "body": body},
            "extra": {"ident": f"reply-{target}"}}


def _fix_summary_body(fix: dict[str, Any], round_no: int | None,
                      resolved: int, deferred: int, rejected: int,
                      body_notes: list[str] | None = None) -> str:
    commit = str(fix.get("fix_commit") or fix.get("commit_sha") or "(なし)")
    head = (FIX_HEAD.format(round_no=round_no, commit=commit) if round_no is not None
            else FIX_HEAD_NO_ROUND.format(commit=commit))
    by = fix.get("by_severity") or {}
    counts = " / ".join(f"{k}={by.get(k, 0)}" for k in ("critical", "major", "minor"))
    lines = [
        head,
        "",
        f"対応件数: {counts}（合計 {fix.get('fixed_count', fix.get('fixed', 0))} 件）",
        f"決着: {resolved} 件 / 見送り: {deferred} 件 / 却下: {rejected} 件",
        f"CI: {fix.get('ci_status') or 'NONE'}",
    ]
    if body_notes:
        lines += ["", "レビュー本文の指摘への返事:", *body_notes]
    note = str(fix.get("ci_note") or "").strip()
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def _reply_items(resolved: list[dict[str, Any]], deferred: list[dict[str, Any]],
                 rejected: list[dict[str, Any]],
                 commit: str) -> tuple[list[dict[str, Any]], list[str]]:
    """決着・見送り・却下の各要素へ付ける返信の項目を、その順に組み立てる。

    返すのは (返信の項目, まとめへ載せる行)。**スレッドを持たない要素（レビュー本文の
    指摘）には返信を積まず、まとめへ載せる。** 本文の指摘の `comment_id` はレビューの
    ID で、GitHub はレビューへの返信を受け付けない。送れない項目が待ち行列の先頭に
    残ると、後ろの決着とまとめまで止まる（#962）。
    """
    # (要素の列, 返信の定型句, 理由を取り出すキー)。決着は理由の代わりにコミットを添える
    reply_rules = (
        (resolved, "対応しました。", None),
        (deferred, "見送ります。", "reason_for_deferral"),
        (rejected, "この指摘は採らない判断です。", "reason_for_rejection"),
    )
    items: list[dict[str, Any]] = []
    body_notes: list[str] = []
    for entries, lead, reason_key in reply_rules:
        for entry in entries:
            if reason_key is None:
                note = f"（{commit}）" if commit else ""
            else:
                note = str(entry.get(reason_key) or entry.get("reason") or "")
            text = f"{lead}{note}".strip()
            if not entry.get("thread_id"):
                summary = str(entry.get("summary") or "").strip()
                body_notes.append(f"- {summary}: {text}" if summary else f"- {text}")
                continue
            reply = _reply(entry.get("comment_id"), text)
            if reply:
                items.append(reply)
    return items, body_notes


def _closing_thread_items(resolved: list[dict[str, Any]], deferred: list[dict[str, Any]],
                          rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """スレッドを決着させる項目を組み立てる。"""
    # 見送り・却下は既定では決着させない（次のラウンドで見直す）。最終スイープは
    # スレッドを残さないため、要素の `resolve` を真にして決着まで求める。
    closing = resolved + [e for e in deferred + rejected if e.get("resolve")]
    items: list[dict[str, Any]] = []
    for entry in closing:
        thread_id = entry.get("thread_id")
        if thread_id:
            items.append({"kind": "thread-resolve",
                          "fields": {"thread_id": str(thread_id)},
                          "extra": {"ident": f"resolve-{thread_id}"}})
    return items


def fix_posts(result_path: pathlib.Path | str, repo: str, pr: int,
              round_no: int | None = None) -> list[dict[str, Any]]:
    """修正の結果ファイルから、待ち行列へ積む項目の列を組み立てる。

    並びは「返信 → 決着 → まとめ」である。返信を先に置くのは、決着したスレッドが
    畳まれた後に返信が届くと、読み手がその返信を開かないためである。
    """
    fix = _read_json(result_path)
    resolved = _dict_items(fix.get("resolved_threads"))
    deferred = _dict_items(fix.get("deferred"))
    rejected = _dict_items(fix.get("rejected"))
    commit = str(fix.get("fix_commit") or fix.get("commit_sha") or "")

    items, body_notes = _reply_items(resolved, deferred, rejected, commit)
    items += _closing_thread_items(resolved, deferred, rejected)
    items.append({
        "kind": "pr-comment",
        "fields": {"body": _fix_summary_body(fix, round_no, len(resolved),
                                             len(deferred), len(rejected), body_notes)},
        "extra": {"ident": f"fix-summary-{round_no if round_no is not None else commit}"},
    })
    return items


class FixOutcome(NamedTuple):
    """修正の投稿を送った結果。"""

    summary_url: str | None
    replied: int
    resolved: int
    queued: int
    failed: bool
    detail: str
    # 恒久的な失敗で飛ばした項目の数（#962）。飛ばしても失敗にはしない。
    dropped: int = 0


def post_fix(queue: post_queue.Queue, result_path: pathlib.Path | str, repo: str,
             pr: int, round_no: int | None = None,
             actor: str | None = None) -> FixOutcome:
    """返信・決着・まとめを待ち行列へ積んで流す。"""
    seqs: dict[int, str] = {}
    for item in fix_posts(result_path, repo, pr, round_no):
        path = post_queue.enqueue(queue, item["kind"], repo, pr, item["fields"],
                                  actor=actor, extra=item["extra"])
        seq = (post_queue.read_item(path) or {}).get("seq")
        if seq is not None:
            seqs[int(seq)] = item["kind"]
    flushed = queue.flush()

    done = {int(i["seq"]): i for i in (flushed.sent + flushed.skipped)
            if i.get("seq") is not None}
    summary_url = None
    for seq, kind in seqs.items():
        if kind == "pr-comment" and seq in done:
            response = done[seq].get("response")
            if isinstance(response, dict):
                summary_url = response.get("html_url") or response.get("url")
    failed = flushed.failed is not None and not flushed.rate_limited
    ours_dropped = [i for i in flushed.dropped if i.get("seq") is not None
                    and int(i["seq"]) in seqs]
    detail = str((flushed.failed or {}).get("last_error") or "")
    if not detail and ours_dropped:
        detail = "; ".join(str(i.get("last_error") or "") for i in ours_dropped)
    return FixOutcome(
        summary_url=str(summary_url) if summary_url else None,
        replied=sum(1 for s, k in seqs.items() if k == "review-reply" and s in done),
        resolved=sum(1 for s, k in seqs.items() if k == "thread-resolve" and s in done),
        queued=flushed.remaining,
        failed=bool(failed),
        detail=detail,
        dropped=len(ours_dropped),
    )


# ---------------- 修正の送信 ----------------


class PushResult(NamedTuple):
    """送信の結果と、報告されたコミットが送り先に載っているか。"""

    ok: bool
    pushed: bool
    contains: bool
    detail: str


# 認証の退避の値は共通層 1 か所が持つ（`git-credential.sh`）。ここへ写さない。
_CREDENTIAL_LIB = pathlib.Path(__file__).resolve().parent / "git-credential.sh"


def _credential_fallback_args() -> list[str]:
    r = subprocess.run(
        ["bash", "-c", f'. "{_CREDENTIAL_LIB}"; ndf_git_credential_fallback_args'],
        capture_output=True, text=True)
    return [line for line in r.stdout.split("\n") if line] if r.returncode == 0 else []


def _git(worktree: pathlib.Path | str, *args: str) -> subprocess.CompletedProcess:
    """git を 1 度実行し、認証で落ちたときだけ helper を退避して 1 度だけやり直す（#524）。"""
    cmd = ["git", "-C", str(worktree), *args]
    first = subprocess.run(cmd, capture_output=True, text=True)
    if first.returncode == 0 or args[0] not in ("push", "fetch"):
        return first
    fallback = _credential_fallback_args()
    if not fallback:
        return first
    return subprocess.run(["git", "-C", str(worktree), *fallback, *args],
                          capture_output=True, text=True)


def push_fix(worktree: pathlib.Path | str, head_branch: str,
             fix_commit: str | None) -> PushResult:
    """現在の頭を送り先のブランチへ送り、報告されたコミットが載ったことを確かめる。

    **ブランチ名だけを指定しない。** 作業ツリーが切り離された頭で作られている場合、
    ブランチ名だけの指定では現在の頭が送られないまま終了コード 0 で終わる。
    """
    if not fix_commit:
        return PushResult(True, False, True, "コミットが無いため送らない")
    if not (str(worktree or "") and head_branch):
        return PushResult(False, False, False,
                          "送る先（作業ツリーとブランチ）が分からない")
    pushed = _git(worktree, "push", "origin", f"HEAD:{head_branch}")
    if pushed.returncode != 0:
        return PushResult(False, False, False,
                          (pushed.stderr or pushed.stdout or "").strip()[:300])
    fetched = _git(worktree, "fetch", "origin", head_branch)
    if fetched.returncode != 0:
        return PushResult(False, True, False,
                          (fetched.stderr or "").strip()[:300])
    contains = _git(worktree, "merge-base", "--is-ancestor", fix_commit, "FETCH_HEAD")
    if contains.returncode != 0:
        return PushResult(False, True, False,
                          f"報告されたコミット {fix_commit} が origin/{head_branch} に載っていない")
    return PushResult(True, True, True, "")


# ---------------- 部分命令 ----------------


def _sh(*cmd: str, cwd: pathlib.Path | str | None = None) -> str:
    r = subprocess.run(list(cmd), capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


def queue_for(worktree: pathlib.Path) -> post_queue.Queue:
    """待ち行列の置き場所。**引数では渡さない。** 作業ツリーの下の決まった名前から導く。"""
    base = os.environ.get("CROSS_REVIEW_TMP_DIR") or str(worktree / TMP_DIRNAME)
    return post_queue.Queue(pathlib.Path(base) / post_queue.QUEUE_DIRNAME)


class FixInputs(NamedTuple):
    worktree: pathlib.Path
    repo: str
    head: str
    result: pathlib.Path
    fix: dict[str, Any]


def _resolve_fix_inputs(args: argparse.Namespace) -> tuple[FixInputs | None, str]:
    """単独 fix 命令の入力を引数と既定値から解決する。

    **リポジトリと頭は作業ツリーの中で解決する。** 呼び出し元の cwd が作業ツリーの
    外だと、`gh` が別のリポジトリを読むか解決に失敗し、頭が空のまま送信を飛ばす。
    """
    worktree = pathlib.Path(args.worktree or os.getcwd()).resolve()
    repo = args.repo or _sh("gh", "repo", "view", "--json", "nameWithOwner",
                            "-q", ".nameWithOwner", cwd=worktree)
    if not repo:
        return None, "リポジトリを決められない（--repo を渡す）"
    head = args.head or _sh("gh", "pr", "view", str(args.pr), "-R", repo,
                            "--json", "headRefName", "-q", ".headRefName",
                            cwd=worktree)
    if not head:
        # 送れない修正へ「対応しました」と返信しないため、返信へ進まず止める。
        return None, "送り先のブランチを決められない（--head を渡す）"
    result = pathlib.Path(args.result) if args.result else (
        pathlib.Path(os.environ.get("CROSS_REVIEW_TMP_DIR")
                     or str(worktree / TMP_DIRNAME)) / f"fix-pr{args.pr}-result.json")
    fix = _read_json(result)
    if not fix:
        return None, f"修正の結果ファイルを読めない: {result}"
    return FixInputs(worktree, repo, head, result, fix), ""


def cmd_fix(args: argparse.Namespace) -> int:
    inputs, error = _resolve_fix_inputs(args)
    if inputs is None:
        print(error, file=sys.stderr)
        return 1
    worktree, repo, head, result, fix = inputs

    pushed = push_fix(worktree, head, fix.get("fix_commit") or fix.get("commit_sha"))
    print(f"PUSHED={1 if pushed.pushed else 0} "
          f"COMMIT_ON_HEAD={1 if pushed.contains else 0}")
    if not pushed.ok:
        print(pushed.detail, file=sys.stderr)
        return 1

    actor = args.actor or _sh("gh", "api", "user", "-q", ".login") or None
    outcome = post_fix(queue_for(worktree), result, repo, int(args.pr),
                       round_no=args.round, actor=actor)
    if outcome.summary_url:
        print(f"POSTED summary_url={outcome.summary_url}")
    print(f"REPLIED={outcome.replied} RESOLVED={outcome.resolved} "
          f"QUEUED={outcome.queued} DROPPED={outcome.dropped}")
    if outcome.dropped and not outcome.failed:
        print(f"⚠️ 送れない項目を {outcome.dropped} 件飛ばしました ({outcome.detail})",
              file=sys.stderr)
    if outcome.failed:
        print(outcome.detail, file=sys.stderr)
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="結果ファイルから投稿を組み立てて送る")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fix", help="修正の結果ファイルの返信・決着・まとめと送信")
    f.add_argument("--repo")
    f.add_argument("--pr", required=True)
    f.add_argument("--result")
    f.add_argument("--head")
    f.add_argument("--worktree")
    f.add_argument("--round", type=int)
    f.add_argument("--actor")
    f.set_defaults(func=cmd_fix)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
