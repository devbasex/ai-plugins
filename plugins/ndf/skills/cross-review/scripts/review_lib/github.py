"""gh と REST の呼び出し・リポジトリの特定・PR とコメントとスレッドの取得（#1142 の C2・D2）。

GitHub へはライブラリの `gh_call`（`gh` の CLI と REST の 1 回の要求）を通して届き、ここは応答の読み方と
失敗の扱いを持つ。テストは関数をこのモジュールの上で差し替えるため、ほかの
モジュールは `github._gh_rest(...)` のようにモジュールの属性として呼ぶ。

**尽きるのは GraphQL 側である**（#271）。`gh pr view` は項目を増やしても
1 リクエストのままだが、REST 側は上限 5,000 のうち大半が残ったまま進行が止まる。
項目をまとめる先を REST にして、GraphQL の消費を実行ごと・ラウンドごとに 0 点へ寄せる。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
from typing import Any, NamedTuple

import review_lib  # noqa: E402
import gh_call  # noqa: E402
import gh_parts  # noqa: E402
import gh_rest  # noqa: E402
import repo as repo_ids  # noqa: E402  `repo` は引数の名前と紛れる


# `owner/name` を path-safe なディレクトリ名 `owner--name` に変換する
_repo_slug = repo_ids.slug


# 応答の形とヘッダの読み方は共通層（`gh_parts`、#849）が持つ。残量は通常の要求の
# 応答ヘッダからしか読めない（`gh api rate_limit` は同じ時刻でも消費を反映しない、実測）。
RestResponse = gh_parts.RestResponse
_parse_rest_headers = gh_parts.parse_rest_headers


def _gh_rest(path: str) -> RestResponse | None:
    """REST の 1 回の要求を投げ、ヘッダと本文を返す。失敗は `None`。

    **例外を投げず、進行を止めない側へ倒す**（#291 の待ち行列を挟む位置）。
    呼び出し側は `None` を「確かめられなかった」として扱う。積む・待つ・流すは
    ここではなく呼び出し側が持つ。要求はライブラリの `gh_call.request` が送る。
    """
    resp = gh_call.request(path)
    if not resp.ok or resp.error:
        review_lib.info(f"⚠ REST が失敗 ({path}, status={resp.status}): {resp.error.strip()[:200]}")
        return None
    return resp


def _git_remote_url() -> str:
    """`origin` の取得元を返す。読めなければ空文字。"""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True,
        )
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _repo_from_resume(pr: int, worktree: str | None) -> str | None:
    """キャッシュとしての状態ファイルから `owner/repo` を読む。**GitHub へは問い合わせない。**

    `origin` が無い、または URL を読めない環境では、リポジトリ名の解決が
    `gh repo view` へ落ちる。上限に達しているとそこで止まるため、再開できるキャッシュが
    残っていても再開の経路へ入れない。**#291 が塞ごうとしている状態そのものである。**

    読めるのは、置き場所がリポジトリ名抜きで決まるときだけである。既定の作業ツリーの
    位置は名前を含むため、環境変数も明示された作業ツリーも無ければ `None` を返す。
    """
    env_tmp = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env_tmp:
        base = pathlib.Path(env_tmp).resolve()
    elif worktree:
        base = pathlib.Path(worktree).resolve() / ".cross_review"
    else:
        return None
    try:
        st = json.loads(
            (base / f"cross-review-pr{pr}-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(st, dict):
        return None
    return str(st.get("repo") or "") or None


def _repo_from_git() -> str | None:
    """git の設定から `owner/repo` を求める。求まらなければ `None`。

    **求めた名前はそのまま使わない。** `repos/{owner}/{repo}/pulls/{PR}` の応答が
    そのまま検証になるため、誤った名前は失敗として現れる（`_fetch_pr_metadata`）。
    """
    return repo_ids.owner_repo_from_url(_git_remote_url())


def _repo_from_gh() -> str:
    """`gh repo view` が返す `owner/repo`。求まらなければ空文字（GitHub へ問い合わせる最後の落とし先）。"""
    r = gh_call.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return r.stdout.strip() if r.returncode == 0 else ""


def _viewer_login() -> str | None:
    """認証している利用者の login。求まらなければ `None`。"""
    return gh_rest.viewer_login()


class PrMetadata(NamedTuple):
    """REST の 1 回の応答から取れる、Pull Request のメタデータ。"""

    repo: str
    author: str
    head_branch: str
    head_sha: str
    base_branch: str
    is_fork: bool
    rate_remaining: int | None
    rate_reset: str | None


def _pr_metadata_of(repo: str, resp: RestResponse) -> PrMetadata | None:
    body = resp.body
    if not isinstance(body, dict) or not body.get("number"):
        return None
    head = body.get("head") or {}
    base = body.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name") or ""
    return PrMetadata(
        repo=repo,
        author=str((body.get("user") or {}).get("login") or ""),
        head_branch=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        base_branch=str(base.get("ref") or ""),
        is_fork=bool(head_repo) and head_repo != repo,
        rate_remaining=resp.rate_remaining,
        rate_reset=resp.rate_reset,
    )


def _fetch_pr_metadata(pr: int, repo: str | None = None) -> PrMetadata | None:
    """作成者・head・base・head の commit を REST の 1 回で取る。

    リポジトリ名は渡された値、無ければ git の設定から求める。**その名前が誤って
    いれば応答が失敗するため、そのときだけ `gh repo view` で解決し直す。**
    確かめる手段が同じ呼び出しに含まれるので、追加の消費なしで誤りを塞げる。
    """
    tried: list[str] = []
    for candidate in (repo, _repo_from_git()):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        resp = _gh_rest(f"repos/{candidate}/pulls/{int(pr)}")
        if resp is None:
            continue
        meta = _pr_metadata_of(candidate, resp)
        if meta is not None:
            return meta
    resolved = _repo_from_gh()
    if not resolved or resolved in tried:
        return None
    resp = _gh_rest(f"repos/{resolved}/pulls/{int(pr)}")
    return _pr_metadata_of(resolved, resp) if resp is not None else None


# 既存コメントの 3 ソースを一括で取る fix skill の共有スクリプト。テストが偽物へ差し替える。
FETCH_COMMENTS_SCRIPT = (pathlib.Path(__file__).resolve().parents[3]
                         / "fix" / "scripts" / "fetch-pr-comments.sh")


def _fetch_existing_comments(repo: str, pr: int, path: pathlib.Path, *,
                             strict: bool) -> str | None:
    """既存コメントのスナップショットを取り、成功なら `path` へ書いて None、失敗なら理由の文を返す。

    `strict=True` は `--strict` を付け（3 ソースのどれか 1 つの失敗でも失敗にする）、一時の
    名前へ書いてから成功したときだけ `path` へ改名する。一部だけのスナップショットで前のスナップショットを上書き
    すると、前のラウンドの指摘が重複の検出から消えるためである（#542 の決定 6）。
    """
    cmd = [str(FETCH_COMMENTS_SCRIPT), *(["--strict"] if strict else []), repo, str(pr)]
    # 起動できない（スクリプトが無い・実行権が無い）ときも失敗の理由として返す。例外で
    # 抜けると、呼び出し側が「失敗したら前のスナップショットのまま進める」を選べない。
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        return f"起動できません: {e}"[:200]
    if r.returncode != 0:
        return (r.stderr or "").strip()[:200] or f"終了コード {r.returncode}"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(r.stdout, encoding="utf-8")
    tmp.replace(path)
    return None


def _normalize_pr_file_status(value: object) -> str:
    """GitHub の変更種別を分類用の一文字へ正規化する。"""
    status = str(value or "modified").lower()
    status_map = {
        "added": "A",
        "modified": "M",
        "deleted": "D",
        "removed": "D",
        "renamed": "R",
        "copied": "C",
        "changed": "M",
    }
    return status_map.get(status, status[:1].upper() or "M")


def _parse_pr_files_payload(output: str) -> list[dict[str, Any]]:
    """`gh pr view --json files` の JSON を分類用の最小構造に正規化する。"""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return []

    entries: list[dict[str, Any]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        if not isinstance(path, str) or not path:
            continue
        status = _normalize_pr_file_status(f.get("changeType"))
        paths = []
        previous = f.get("previousPath") or f.get("previous_filename")
        if isinstance(previous, str) and previous and previous != path:
            paths.append(previous)
        paths.append(path)
        entries.append({"status": status, "paths": paths})
    return entries


def _parse_pr_files_api_lines(output: str) -> list[dict[str, Any]]:
    """GitHub API の PR files を TSV(JSON jq) 出力から分類用構造に変換する。"""
    entries: list[dict[str, Any]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        cols = raw.split("\t")
        status_raw = cols[0].strip() if cols else "modified"
        path = cols[1].strip() if len(cols) > 1 else ""
        previous = cols[2].strip() if len(cols) > 2 else ""
        if not path:
            continue
        paths = []
        if previous and previous != path:
            paths.append(previous)
        paths.append(path)
        entries.append({"status": _normalize_pr_file_status(status_raw), "paths": paths})
    return entries


def _fetch_changed_files(pr: int, repo: str) -> list[dict[str, Any]]:
    r = gh_call.gh([
        "api", f"repos/{repo}/pulls/{pr}/files",
        "--paginate",
        "--jq", '.[] | [.status, .filename, (.previous_filename // "")] | @tsv',
    ])
    if r.returncode == 0:
        entries = _parse_pr_files_api_lines(r.stdout)
        if entries:
            return entries
        review_lib.info("⚠ PR files API の結果が空、または解析できません。gh pr view fallback を試行")

    else:
        review_lib.info(f"⚠ PR files API 取得に失敗。gh pr view fallback を試行: {r.stderr.strip()[:200]}")

    fallback = gh_call.gh(["pr", "view", str(pr), "--json", "files"])
    if fallback.returncode != 0:
        review_lib.info(f"⚠ PR 変更ファイル一覧の取得に失敗。自動レビュー観点は共通のみ: {fallback.stderr.strip()[:200]}")
        return []
    entries = _parse_pr_files_payload(fallback.stdout)
    if not entries:
        review_lib.info("⚠ PR 変更ファイル一覧が空、または解析できません。自動レビュー観点は共通のみ")
    return entries


def _review_exists(repo: str, pr: int, review_url: str | None) -> bool | None:
    """`review_url` の指すレビューが GitHub 側にあるか。

    取得できなければ `None` を返す。**「取得できなかった」と「無い」を区別する。**
    取得の失敗で中断すると、GitHub 側の一時的な不調でループが止まる。

    上限で積んだ投稿を後から流したとき、その直後に 1 度だけ呼ぶ（`_confirm_flushed`）。
    取り込みが自分で送った投稿は、送信の応答をそのまま記録にするため照会しない（#730）。
    """
    if not repo or not review_url:
        return False
    m = re.search(r"pullrequestreview-(\d+)", str(review_url))
    if not m:
        return False
    text = gh_call.gh(["api", f"repos/{repo}/pulls/{pr}/reviews/{m.group(1)}", "--jq", ".id"]).stdout.strip()
    if not text:
        return None
    return text.split()[0] == m.group(1)


def _gh_output(cmd: list[str]) -> str | None:
    """`gh` を実行して標準出力を返す。実行に失敗したときは `None` を返す。

    **「取得できなかった」と「0 件」を区別する。** 失敗を空の出力として返すと、
    GitHub 側の一時的な不調が「未解決の指摘は無い」と読まれてしまう。
    """
    r = gh_call.gh(cmd[1:] if cmd[:1] == ["gh"] else cmd)
    if r.returncode != 0:
        review_lib.info(f"⚠ gh が失敗 (exit={r.returncode}): {r.stderr.strip()[:200]}")
        return None
    return r.stdout


def _fetch_unresolved_threads(repo: str, pr: int) -> list[dict[str, Any]] | None:
    """Pull Request 上の未解決の指摘を GraphQL で数え、識別子つきで返す。

    **投稿数とは別のものを数えている。** 投稿数はそのラウンドで外部の AI が新しく
    投稿した件数で、ここで数えるのは前のラウンドの分も含む Pull Request 上の総数である。

    Returns:
      未解決の指摘の一覧（`{"id", "path", "line"}`）。0 件なら空の一覧。
      取得できなければ `None`。

    取得は共通層の `gh_parts.unresolved_threads` が持つ。ここは `thread_id` を `id` へ
    写すだけで、出力の形は変えない。
    """
    threads = gh_parts.unresolved_threads(repo, pr, output=lambda cmd: _gh_output(cmd))
    if threads is None:
        return None
    return [{"id": t["thread_id"], "path": t["path"], "line": t["line"]} for t in threads]
