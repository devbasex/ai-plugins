#!/usr/bin/env python3
"""範囲外の課題の起票の決まった手順（#851）。`out-of-scope` と `retrospective` から呼ぶ。

    python3 issue-file.py resolve-target
    python3 issue-file.py dup       --repo <起票先> --query <語>
    python3 issue-file.py note      --repo <R> --number <N> (--origin <由来> | --counterpart <R>#<N>)
    python3 issue-file.py create    --repo <起票先> --title <題> --body-file <本文> --origin <由来>
                                    [--label <名前>]... [--counterpart <R>#<N>] [--approved <提示の要約値>]
    python3 issue-file.py by-origin --origin <由来>... [--repo <R>]... [--with-upstream]

結果は `lib/step_result.py` の形の 1 行の JSON（`tool` は `issue-file`）。終了コード:

    0   済んだ（0 件の検索も含む）
    1   本文の骨格が欠ける・提示の要約値が合わない・書き込みが失敗した（create / note）
    2   引数の形の誤り・GitHub か git を読めない
    10  承認資料を書いた。同意の後に `--approved <metrics.digest>` を足して打ち直す（create）
    20  上流リポジトリを 1 つに絞れない。候補を示して利用者に選んでもらう（resolve-target）

由来は `PR #<番号>` か `issue #<番号>`、リポジトリは `<所有者>/<リポジトリ>`、相手の課題は `<所有者>/<リポジトリ>#<番号>`。
形が違えば GitHub を呼ばずに 2 で終える。GitHub は `lib/gh_rest.py`（下は `gh_call`）だけを通して呼ぶ。

## 上流リポジトリの解決（resolve-target）

上流リポジトリは NDF の Skill・エージェント・hook の実体を持つリポジトリ、開発対象リポジトリは `gh repo view` が返す
リポジトリである。上流は次の順で決め、決まった時点で止める。

1. 環境変数 `NDF_SKILL_REPO`（空は無いのと同じ）
2. 取得元の clone の `remote.origin.url`。1 つに絞れたときだけ採る。見る場所は
   `~/.claude/plugins/marketplaces/*/`（Claude Code）・`~/.codex/.tmp/marketplaces/*/`（Codex）・現在地の clone の根
   （Kiro と agy は clone した作業ディレクトリから導入するため、決まった置き場所が無い）

- **`plugins/ndf/` を持つ clone だけを候補にする。** 取得元の置き場所には登録したすべての取得元が並び（名前順で、
  上流が先に来る保証は無い）、GitHub の取得元であることだけを条件にすると別のリポジトリが決まる。取得元の名前や
  `marketplace.json` の `name` は fork や登録名の変更で変わり、同じ名前を別の取得元が名乗れる
- **現在地は `git rev-parse --show-toplevel` で根へ戻してから見る。** 現在地にも同じ絞り込みを掛ける（いま開いている
  リポジトリが上流とは限らない）
- 版ごとの配置（`~/.claude/plugins/cache/<取得元>/<名前>/<版>/`）は `.git` を持たないため見ない
- URL は `github.com` の後の `:` か `/` から後ろを取り、末尾の `.git` を落とす。GitHub でない URL と origin の無い
  clone は候補にしない。同じ名前が 2 か所から出たら 1 つにまとめる。fork と本家のように 2 つ以上残れば決めない（20）
- 決まらないときに推測で起票先を渡すと、読む人のいない場所に課題が残り、作った側からは成功に見える
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import gh_call  # noqa: E402
import gh_rest  # noqa: E402
import gh_sections  # noqa: E402
import proc  # noqa: E402
from step_result import EXIT_GATE, EXIT_OK, EXIT_PAUSE, EXIT_UNREADABLE, EXIT_VIOLATION  # noqa: E402
from step_result import approval_present, emit, presentation_dir, result  # noqa: E402

TOOL = "issue-file"
_REPO_FORM = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"  # <所有者>/<リポジトリ>
REPO_RE = re.compile(rf"^{_REPO_FORM}$")
ORIGIN_RE = re.compile(r"^(PR|issue) #[1-9][0-9]*$")
COUNTERPART_RE = re.compile(rf"^(?P<repo>{_REPO_FORM})#(?P<number>[1-9][0-9]*)$")
GITHUB_URL_RE = re.compile(r"github\.com[:/]")
SKELETON = ("何を見つけたか", "どこで見つけたか", "なぜこの変更の範囲外なのか", "直さないと何が起きるか", "由来")
ORIGIN_HEADING = "## 由来"
# 取得元の clone の置き場所（HOME からの相対）。Claude Code と Codex の順
MARKETPLACE_DIRS = (".claude/plugins/marketplaces", ".codex/.tmp/marketplaces")
NDF_MARK = Path("plugins") / "ndf"
SEARCH_LIMIT = 100  # by-origin の 1 回の検索で読む件数
DUP_LIMIT = 30  # dup の検索で読む件数（gh の既定と同じ）
ERROR_EXCERPT = 300  # 失敗の理由を結果へ載せるときの最大の長さ


class _Parser(argparse.ArgumentParser):
    """引数の誤りも結果 JSON（2）で返す。"""

    def error(self, message: str) -> "NoReturn":  # noqa: F821
        emit(result(TOOL, "stopped", f"引数の誤り: {message}"), EXIT_UNREADABLE)


def _stop_with(summary: str, code: int, items=None, metrics=None) -> "NoReturn":  # noqa: F821
    emit(result(TOOL, "stopped", summary, items, metrics), code)


def _require_form(value: str, pattern: re.Pattern, what: str) -> str:
    if not pattern.match(value or ""):
        _stop_with(f"{what}の形が違う: {value!r}", EXIT_UNREADABLE)
    return value


def _counterpart(value: str) -> tuple[str, int]:
    m = COUNTERPART_RE.match(value or "")
    if not m:
        _stop_with(f"相手の課題の形が違う（<所有者>/<リポジトリ>#<番号>）: {value!r}", EXIT_UNREADABLE)
    return m.group("repo"), int(m.group("number"))


# --- 上流リポジトリと開発対象リポジトリ ------------------------------------------------


def _slug_of(url: str) -> str | None:
    """GitHub の URL から `<所有者>/<リポジトリ>` を取る。GitHub でなければ `None`。"""
    if not GITHUB_URL_RE.search(url):
        return None
    trimmed = url[: -len(".git")] if url.endswith(".git") else url
    return re.sub(r".*github\.com[:/]", "", trimmed) or None


def _clone_candidates() -> list[Path]:
    home = Path(os.environ.get("HOME") or Path.home())
    found: list[Path] = []
    for rel in MARKETPLACE_DIRS:
        base = home / rel
        if base.is_dir():
            found += sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))
    here = proc.git_out(Path.cwd(), "rev-parse", "--show-toplevel")
    if here:
        found.append(Path(here))
    return found


def _candidate_names(items: list[dict]) -> list[str]:
    """候補の clone が指すリポジトリ名（重複を除いて並べたもの）。"""
    return sorted({it["repo"] for it in items})


def resolve_upstream() -> tuple[str | None, str, list[dict]]:
    """上流リポジトリを `(名前か None, 決めた手段, 候補)` で返す。`NDF_SKILL_REPO` の形の誤りは 2 で終える。"""
    env = os.environ.get("NDF_SKILL_REPO") or ""
    if env:
        return _require_form(env, REPO_RE, "NDF_SKILL_REPO "), "env", []
    items: list[dict] = []
    for clone in _clone_candidates():
        if not (clone / NDF_MARK).is_dir():
            continue
        slug = _slug_of(proc.git_out(clone, "config", "--get", "remote.origin.url") or "")
        if slug:
            items.append({"repo": slug, "path": str(clone)})
    names = _candidate_names(items)
    return (names[0] if len(names) == 1 else None), "clone", items


def development_repo() -> str | None:
    """開発対象リポジトリ（`gh repo view`）。読めなければ `None`。"""
    r = gh_call.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    name = r.stdout.strip() if r.returncode == 0 else ""
    return name if REPO_RE.match(name) else None


def cmd_resolve_target(a) -> None:
    upstream, source, items = resolve_upstream()
    target = development_repo()
    same = bool(upstream and target and upstream.lower() == target.lower())
    metrics = {"upstream": upstream, "target": target, "same": same, "source": source}
    if upstream is None:
        names = _candidate_names(items)
        emit(
            result(
                TOOL,
                "gate",
                f"上流リポジトリを 1 つに絞れない（候補 {len(names)} 件: {', '.join(names) or '無し'}）",
                items,
                metrics,
                next="候補を示して利用者に選んでもらう。推測で起票先を渡さない",
            ),
            EXIT_PAUSE,
        )
    emit(result(TOOL, "ok", f"上流 {upstream}・開発対象 {target or '読めない'}", items, metrics), EXIT_OK)


# --- 検索 ---------------------------------------------------------------------------


def cmd_dup(a) -> None:
    repo = _require_form(a.repo, REPO_RE, "起票先")
    found = gh_rest.issue_search(repo, a.query, "open", DUP_LIMIT)
    if not found.ok:
        _stop_with(f"{repo} の検索が失敗した: {found.error[:ERROR_EXCERPT]}", EXIT_UNREADABLE)
    items = [{"number": d.get("number"), "title": d.get("title"), "url": d.get("url")} for d in found.value]
    emit(result(TOOL, "ok", f"{repo} の open の課題で {len(items)} 件が当たった", items, {"count": len(items)}), EXIT_OK)


def _dedupe(repos: list[str]) -> list[str]:
    out: list[str] = []
    for r in repos:
        if r.lower() not in {x.lower() for x in out}:
            out.append(r)
    return out


def _targets_for_by_origin(a) -> tuple[list[str], list[str], str | None]:
    """by-origin の検索対象 `(由来, リポジトリ, 上流リポジトリ)`。リポジトリが無ければ 2 で終える。"""
    origins = _dedupe([_require_form(o, ORIGIN_RE, "由来") for o in a.origin])
    repos = [_require_form(r, REPO_RE, "リポジトリ") for r in a.repo or []]
    upstream = None
    if a.with_upstream:
        upstream = resolve_upstream()[0]
        repos += [upstream] if upstream else []
    repos = _dedupe(repos)
    if not repos:
        _stop_with("検索するリポジトリが無い（--repo か、決まる --with-upstream を渡す）", EXIT_UNREADABLE)
    return origins, repos, upstream


def _merge_row(merged: dict[tuple[str, int], dict], repo: str, origin: str, d: dict) -> None:
    """検索の 1 件を `merged` へ足す。同じ課題なら由来だけを足す。"""
    key = (repo, int(d.get("number")))
    row = merged.setdefault(
        key,
        {"repo": repo, "number": key[1], "title": d.get("title"), "url": d.get("url"), "state": d.get("state"), "origins": []},
    )
    if origin not in row["origins"]:
        row["origins"].append(origin)


def _merge_origin_hits(repos: list[str], origins: list[str]) -> tuple[dict[tuple[str, int], dict], int]:
    """リポジトリ × 由来で検索して課題ごとにまとめる。`(まとめた結果, 検索の回数)`。"""
    merged: dict[tuple[str, int], dict] = {}
    searches = 0
    for repo in repos:
        for origin in origins:
            found = gh_rest.issue_search(repo, f'"{origin}"', "all", SEARCH_LIMIT)
            searches += 1
            if not found.ok:
                _stop_with(f"{repo} で {origin} の検索が失敗した: {found.error[:ERROR_EXCERPT]}", EXIT_UNREADABLE)
            for d in found.value:
                _merge_row(merged, repo, origin, d)
    return merged, searches


def cmd_by_origin(a) -> None:
    origins, repos, upstream = _targets_for_by_origin(a)
    merged, searches = _merge_origin_hits(repos, origins)
    items = list(merged.values())
    metrics = {"searches": searches, "count": len(items), "upstream": upstream}
    summary = f"{len(repos)} リポジトリ × {len(origins)} 由来で {len(items)} 件"
    nxt = None
    if a.with_upstream and upstream is None:
        # 止めない（設計 #851 の AC8）。ただし上流を外した検索を「取りこぼし 0 件」と読ませない
        summary += "。上流リポジトリを決められず、上流は検索していない"
        nxt = "上流リポジトリを --repo で名指しして打ち直す"
    emit(result(TOOL, "ok", summary, items, metrics, next=nxt), EXIT_OK)


# --- 書き込み -------------------------------------------------------------------------


def _note_line(a) -> str:
    if bool(a.origin) == bool(a.counterpart):
        _stop_with("--origin と --counterpart のどちらか 1 つを渡す", EXIT_UNREADABLE)
    if a.origin:
        return f"同じ事象を {_require_form(a.origin, ORIGIN_RE, '由来')} の作業中に確認した。"
    _counterpart(a.counterpart)
    return f"開発対象の側は {a.counterpart} として残した。"


def cmd_note(a) -> None:
    repo = _require_form(a.repo, REPO_RE, "リポジトリ")
    if a.number < 1:
        _stop_with(f"番号の形が違う: {a.number}", EXIT_UNREADABLE)
    line = _note_line(a)
    done = gh_rest.comment(repo, a.number, line)
    if not done.ok:
        _stop_with(f"{repo}#{a.number} へのコメントが失敗した: {done.error[:ERROR_EXCERPT]}", EXIT_VIOLATION)
    item = {"repo": repo, "number": a.number, "url": done.value.get("url", "")}
    emit(result(TOOL, "ok", f"{repo}#{a.number} へ 1 行を足した", [item], {}), EXIT_OK)


def _line_with(lines: list[str], text: str) -> int | None:
    """`text` を含む最初の行の位置（`PR #19` が `PR #190` に当たらないよう、後ろの数字を見る）。"""
    pat = re.compile(re.escape(text) + r"(?!\d)")
    return next((i for i, line in enumerate(lines) if pat.search(line)), None)


def assemble(body: str, origin: str, counterpart: str | None = None) -> str:
    """「由来」の節に由来（と上流リポジトリの側の課題）が無ければ入れる。節が無ければ変えない。"""
    section = gh_sections.get_section(body, ORIGIN_HEADING)
    if section is None:
        return body
    lines = section.split("\n") if section.strip() else []
    changed = False
    at = _line_with(lines, origin)
    if at is None:
        lines.insert(0, origin)
        at, changed = 0, True
    if counterpart and _line_with(lines, counterpart) is None:
        lines.insert(at + 1, f"上流リポジトリの側: {counterpart}")
        changed = True
    if not changed:
        return body
    out = gh_sections.replace_section(body, ORIGIN_HEADING, "\n".join(lines))
    if gh_sections.SECTION_END not in body:  # 置換が足す節の終わりの目印は、元の本文に無ければ残さない（本文の形を変えない）
        out = "\n".join(line for line in out.split("\n") if line.strip() != gh_sections.SECTION_END)
    return out


def skeleton_gaps(body: str) -> list[dict]:
    """骨格の 5 つの見出しのうち、無いもの・中身が空のもの。囲みの中の `#` は見出しに数えない。"""
    gaps = []
    for name in SKELETON:
        content = gh_sections.get_section(body, f"## {name}")
        if content is None:
            gaps.append({"heading": name, "result": "missing"})
        elif not content.replace(gh_sections.SECTION_END, "").strip():
            gaps.append({"heading": name, "result": "empty"})
    return gaps


def digest_of(repo: str, title: str, body: str, labels: list[str]) -> str:
    shown = {"repo": repo, "title": title, "body": body, "labels": sorted(labels)}
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_body_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _stop_with(f"本文のファイルを読めない: {path}（{exc}）", EXIT_UNREADABLE)


def _gate(repo: str, title: str, body: str, labels: list[str], digest: str, other: bool) -> "NoReturn":  # noqa: F821
    body_path = presentation_dir() / f"{TOOL}-{digest[:12]}-body.md"
    body_path.write_text(body, encoding="utf-8")
    path = approval_present(
        TOOL,
        f"create-{digest[:12]}",
        title=f"課題の起票: {title}",
        targets=[{"url": f"https://github.com/{repo}/issues", "title": repo}],
        change=f"課題 1 件（本文 {len(body)} 文字）",
        judge=[
            ("起票先", repo),
            ("開発対象リポジトリと別か", "別（他のリポジトリへの公開に当たる）" if other else "同じ"),
            ("題", title),
            ("ラベル", ", ".join(labels) or "無し"),
            ("本文のファイル", str(body_path)),
        ],
        consent=[f"{repo} へ、この題・本文・ラベルで課題を 1 件作る"],
        rollback=f"作った課題は `gh issue close <番号> --repo {repo}` で閉じる（課題の削除はリポジトリの管理者だけができる）",
    )
    metrics = {"digest": digest, "other_repo": other, "body_path": str(body_path)}
    emit(
        result(
            TOOL,
            "gate",
            f"{repo} への起票の承認資料を書いた",
            [],
            metrics,
            presentation_path=path,
            next=f"承認資料を示し、同意を得たら同じ引数に --approved {digest} を足して打ち直す",
        ),
        EXIT_GATE,
    )


def _create_inputs(a) -> tuple[str, str, tuple[str, int] | None, str, str, list[str]]:
    """起票の入力 `(起票先, 由来, 相手の課題, 題, 本文, ラベル)` を確かめて確定する。骨格が欠ければ 1 で終える。"""
    repo = _require_form(a.repo, REPO_RE, "起票先")
    origin = _require_form(a.origin, ORIGIN_RE, "由来")
    counterpart = _counterpart(a.counterpart) if a.counterpart else None
    if not a.title.strip():
        _stop_with("題が空", EXIT_UNREADABLE)
    labels = list(a.label or [])
    body = assemble(_read_body_file(a.body_file), origin, a.counterpart)
    gaps = skeleton_gaps(body)
    if gaps:
        names = "・".join(g["heading"] for g in gaps)
        _stop_with(f"本文の骨格が欠ける: {names}。課題は作っていない", EXIT_VIOLATION, gaps)
    return repo, origin, counterpart, a.title, body, labels


def _create_gate_or_check(repo: str, title: str, body: str, labels: list[str], approved: str | None) -> tuple[str, bool]:
    """承認が無ければゲートへ回し、あれば要約値を照合する。`(要約値, 開発対象リポジトリと別か)`。"""
    digest = digest_of(repo, title, body, labels)
    target = development_repo()
    other = target is None or target.lower() != repo.lower()
    if not approved:
        _gate(repo, title, body, labels, digest, other)
    if approved != digest:
        _stop_with(
            "提示の要約値が合わない（示した後に起票先・題・本文・ラベルが変わった）。課題は作っていない",
            EXIT_VIOLATION,
            metrics={"digest": digest},
        )
    return digest, other


def cmd_create(a) -> None:
    repo, _origin, counterpart, title, body, labels = _create_inputs(a)
    digest, other = _create_gate_or_check(repo, title, body, labels, a.approved)
    made = gh_rest.issue_create(repo, title, body, labels or None)
    if not made.ok:
        _stop_with(f"{repo} への起票が失敗した: {made.error[:ERROR_EXCERPT]}", EXIT_VIOLATION)
    number, url = made.value.get("number"), made.value.get("url", "")
    nxt = None
    if counterpart:
        nxt = f'python3 "$SCRIPTS/issue-file.py" note --repo {counterpart[0]} --number {counterpart[1]} --counterpart {repo}#{number}'
    item = {"repo": repo, "number": number, "url": url}
    emit(result(TOOL, "ok", f"{repo}#{number} を作った", [item], {"digest": digest, "other_repo": other}, next=nxt), EXIT_OK)


# --- 入口 ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="issue-file.py", description="範囲外の課題の起票の決まった手順")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve-target", help="上流リポジトリと開発対象リポジトリの名前").set_defaults(func=cmd_resolve_target)
    p = sub.add_parser("dup", help="起票先の open の課題から重複の候補を返す")
    p.add_argument("--repo", required=True)
    p.add_argument("--query", required=True)
    p.set_defaults(func=cmd_dup)
    p = sub.add_parser("note", help="既存の課題へ由来か相手の課題の 1 行をコメントする")
    p.add_argument("--repo", required=True)
    p.add_argument("--number", required=True, type=int)
    p.add_argument("--origin")
    p.add_argument("--counterpart")
    p.set_defaults(func=cmd_note)
    p = sub.add_parser("create", help="骨格を検査し、承認資料を示し、同意の後に由来を付けて起票する")
    p.add_argument("--repo", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--origin", required=True)
    p.add_argument("--label", action="append")
    p.add_argument("--counterpart")
    p.add_argument("--approved")
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("by-origin", help="由来で、その変更から出た課題を集める")
    p.add_argument("--origin", required=True, action="append")
    p.add_argument("--repo", action="append")
    p.add_argument("--with-upstream", action="store_true")
    p.set_defaults(func=cmd_by_origin)
    return ap


def main(argv=None) -> None:
    a = parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
