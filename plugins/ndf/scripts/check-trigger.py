#!/usr/bin/env python3
"""check-trigger.py: 検査（構造改善と実装レビュー）のトリガーを判定し、検査の範囲の用意と後始末と記録を持つ（#1078）。

    check-trigger.py eval [--final | --review] [--id <計画名>] [--since <ref>] [--root DIR]
    check-trigger.py prepare --id <名> --state <計画の状態ディレクトリ> [--review] [--root DIR]
    check-trigger.py scope --id <名> --state <DIR>
    check-trigger.py finish --id <名> --pr N [--root DIR]
    check-trigger.py record --id <名> --state <DIR> (--pr N | --failed [--pr N] | --target-pr N [--advance-done] [--failed]) [--review] [--root DIR]
    check-trigger.py escape --pr N [--of M] [--root DIR]
    check-trigger.py changed --id <名> [--root DIR]
    check-trigger.py stats [--root DIR]

`pace: fast` の検査は Pull Request ごとに通さず、次のトリガーのどれかが立ったときに、次の開発版の前に
1 回通す。閾値と領域はリポジトリの `.ndf/pace.json` の宣言から読む（無い値は初期値）。

| トリガー | 立つ条件 |
| --- | --- |
| score | 前回の検査からの Pull Request の点数 ≥ triggers.score（共通層を触った PR は common_weight 点、他は 1 点） |
| lines | 前回の検査からの変更（追加 + 削除）> triggers.lines |
| escapes | 前回の検査の後に、同じ領域で逃げた不具合の記録 ≥ triggers.escapes |
| hours | 前回の検査から triggers.hours 時間以上たち、その間に PR が 1 本以上ある |
| final | --final を渡し、範囲に PR が 1 本以上ある（スプリントの終わり） |
| review | --review を渡し、前回のレビューからの範囲に PR が 1 本以上ある（開発版ごとの実装レビュー。ほかのトリガーは見ない） |

**範囲の起点（前回の検査）は、origin の `check-done/review`（`--review`）か `check-done/check`（それ以外）→
手元の記録 → `--since` → 正式版のタグ → 起点のブランチとの分岐点の順に決める。** `record` が結果 merged / no_change の
とき `to` をこのブランチへ送る（構造改善を含む検査は両方、`--review` は `check-done/review` だけ）。落ちた検査は
送らないため、次の回は同じ起点から数え直す。ブランチは origin にあるため、手元の記録が消えても、別のマシンから
続けても、検査を通らずに配布された変更を範囲から落とさない。初めて使うリポジトリでは、どこまで見たかを
`--since <ref>` で渡す。

**`--review` は実装レビューだけの検査である。** 範囲の起点は、レビューだけの回を含む前回の検査である。記録には
`only: review` が付き、構造改善を含む検査（`--review` 無し）の範囲の起点にはならない。レビューを開発版ごとに
通しても、構造改善のトリガーは前回の構造改善からの差分で数える。

終了コード（eval）: 立った = 0（ok）/ 立たない = 3（stopped。正常な否定の結果）/ 宣言が読めない・git が無い・
範囲を決められない = 2。`finish` と `changed` の 3 は「変更なし」。eval は立ったかどうかにかかわらず
検査の記録へ 1 行を足す（閾値を見直す材料）。

検査の記録は通過記録と同じ置き場（`${CLAUDE_PLUGIN_DATA}` → `${XDG_STATE_HOME:-~/.local/state}/ndf`
→ `${TMPDIR:-/tmp}/ndf-checks`）の `checks/<所有者>__<リポジトリ>.jsonl` に、事象を追記するだけで持つ。
前回の検査は result が merged か no_change の check の最新の行で、無ければ --since、リポジトリの配布の宣言
（`.ndf/supervise.json` の release）が決める正式版のタグの最新、起点のブランチとの分岐点の順に使う。
トリガーの評価は通信しない（git の履歴だけを読む）。

**検査の記録の書き手は `record` の 1 つである（#1317）。** 前回の検査からの差分の検査（`scope: since`）も、
PR を指す検査（`--target-pr N`。`scope: pr`）も、終わり方（マージ・変更なし・落ちた）にかかわらず 1 行を書く。
PR を指す検査の行は `pr` と `to` を持たないため、範囲の起点にも、範囲から外す PR にもならない。件数は計画の
`state.json` の `counts`（フェーズレポートと同じ出所）から写す。`stats` は、流出不具合を持ち込んだ PR（`of`）を
範囲（`prs`）に含めた最新の検査へ結び付ける。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import EXIT_OK, EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION, emit, result  # noqa: E402
from pace import PaceError, matches, read_pace  # noqa: E402
import clock  # noqa: E402
import gh_call  # noqa: E402
import gh_rest  # noqa: E402
import jsonio  # noqa: E402
import proc  # noqa: E402
import repo  # noqa: E402

TOOL = "check-trigger"
MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+) from [^/\s]+/(\S+)")
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)$")  # squash merge の既定の件名「<題> (#N)」
SKIP_BRANCHES = ("release/", "check/")
BASE_PREFIX = "check-base/"
DONE_PREFIX = "check-done/"  # 見終えた位置を origin に残すブランチ（review = 実装レビュー、check = 構造改善を含む検査）
ENDED = ("merged", "no_change")  # 前回の検査になる check の終わり方
# 実装レビューの件数（cross-review の drive の counts）。findings / fixed / deferred / rejected は修正担当が扱った
# 指摘の単位、comments はレビュー担当のコメントの単位、unresolved はスレッドの単位（#1317）
REVIEW_COUNTS = ("rounds", "comments", "findings", "fixed", "deferred", "rejected", "unresolved")


def read_findings(f: dict | None) -> dict:
    """検査の行の件数を今の単位で読む。`comments` を持たない古い行の `findings` はコメントの数なので、
    `comments` へ読み替え、指摘と修正は空にする（単位の違う値を指摘として並べない）。行は書き換えない。"""
    f = dict(f or {})
    if "comments" not in f and "findings" in f:
        f["comments"] = f.pop("findings")
        f.update(findings=None, fixed=None)
    return f


class Stop(Exception):
    def __init__(self, summary: str, code: int = EXIT_UNREADABLE):
        super().__init__(summary)
        self.code = code


# ---------------------------------------------------------------- 宣言・git・置き場


def git_or_stop(root: Path, *args: str, check: bool = True) -> str:
    """`git -C <root>` の標準出力（`lib/proc.py`）。`check` なら失敗は Stop、そうでなければ空。"""
    try:
        p = proc.git(root, *args, check=False)
    except OSError:
        raise Stop("git が無い")
    if check and p.returncode != 0:
        raise Stop(f"git {' '.join(args)}: {p.stderr.strip()[:300]}")
    return p.stdout.strip() if p.returncode == 0 else ""


def gh_or_stop(root: Path, *args: str) -> str:
    """`gh` を 1 回呼ぶ（`lib/gh_call.py`）。失敗は Stop（終了コード 1）。"""
    r = gh_call.gh(list(args), cwd=str(root))
    if r.returncode == 127:
        raise Stop("gh が無い", EXIT_VIOLATION)
    if r.returncode != 0:
        raise Stop(f"gh {' '.join(args)}: {r.stderr.strip()[:300]}", EXIT_VIOLATION)
    return r.stdout


def repo_root(arg: str | None) -> Path:
    root = git_or_stop(Path(arg or "."), "rev-parse", "--show-toplevel")
    if not root:
        raise Stop("git の作業ツリーではない（--root を渡す）")
    return Path(root)


def json_or_stop(path: Path, what: str) -> dict:
    """JSON のオブジェクトを読む（`lib/jsonio.py`）。無い・読めない・形が違うときは Stop。"""
    try:
        return jsonio.read(path, want=dict)
    except jsonio.JsonReadError as e:
        raise Stop(
            {
                "missing": f"{what} が無い: {path}",
                "broken": f"{what} を読めない: {path}: {e.detail}",
                "type": f"{what} はオブジェクトで書く: {path}",
            }[e.kind]
        )


def load_decl(root: Path) -> dict:
    try:
        return read_pace(root)
    except PaceError as e:
        raise Stop(str(e))


def optional_decl(root: Path, name: str) -> dict:
    try:
        return json_or_stop(root / ".ndf" / name, name)
    except Stop:
        return {}


def area_of(path: str, decl: dict) -> tuple[str, bool]:
    """(領域の名前, 共通層か)。どの領域にも当たらなければ、ディレクトリの先頭 3 階層を名前にする。"""
    for a in decl["areas"]:
        if matches(path, a["paths"]):
            return a["name"], bool(a.get("common"))
    parts = Path(path).parts[:-1][:3]
    return ("/".join(parts) or "."), False


def slug_of(root: Path) -> str:
    url = git_or_stop(root, "config", "--get", "remote.origin.url", check=False)
    found = repo.owner_repo_from_url(url)
    if not found:
        raise Stop("origin の URL から所有者とリポジトリを決められない")
    return found.replace("/", "__", 1)


def state_base() -> Path:
    """通過記録と同じ順で置き場を決める。"""
    fallback = Path(os.environ.get("TMPDIR", "/tmp")) / "ndf-checks"
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        base = Path(os.environ["CLAUDE_PLUGIN_DATA"])
    elif os.environ.get("XDG_STATE_HOME"):
        base = Path(os.environ["XDG_STATE_HOME"]) / "ndf"
    elif os.environ.get("HOME"):
        base = Path(os.environ["HOME"]) / ".local" / "state" / "ndf"
    else:
        return fallback
    try:
        (base / "checks").mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        return fallback


def log_path(root: Path) -> Path:
    base = state_base()
    if base.name == "ndf-checks":
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{slug_of(root)}.jsonl"
    return base / "checks" / f"{slug_of(root)}.jsonl"


def read_events(root: Path) -> list[dict]:
    p = log_path(root)
    if not p.is_file():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("kind"):
            rows.append(d)
    return rows


def append_event(root: Path, row: dict) -> None:
    p = log_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": row.pop("kind"), "at": clock.now_iso("utc"), **row}, ensure_ascii=False) + "\n")


def range_base(root: Path) -> str:
    """起点のブランチ: `.ndf/worktree.json` の base_branch、無ければ origin の HEAD。"""
    base = repo.declared_base(root)
    if base:
        return base
    head = git_or_stop(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if head.startswith("origin/"):
        return head[len("origin/") :]
    raise Stop("起点のブランチが分からない（.ndf/worktree.json の base_branch）")


def release_tag_glob(root: Path) -> str | None:
    """配布の宣言から正式版のタグの形を決める。package-plugin は `<plugin>--v*`。"""
    rel = optional_decl(root, "supervise.json").get("release") or {}
    if isinstance(rel, dict) and rel.get("form") == "package-plugin" and rel.get("plugin"):
        return f"{rel['plugin']}--v"
    return None


def commit_at(root: Path, ref: str) -> datetime:
    """コミットの時刻（UTC）。"""
    return datetime.fromisoformat(git_or_stop(root, "log", "-1", "--format=%cI", ref)).astimezone(timezone.utc)


def parse_at(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ---------------------------------------------------------------- 範囲とトリガー


def last_check(events: list[dict], review: bool = False) -> dict | None:
    """前回の検査。構造改善を含む検査（review=False）は、レビューだけの回（only: review）を数えない。"""
    ended = [
        e for e in events if e["kind"] == "check" and e.get("result") in ENDED and e.get("to") and (review or e.get("only") != "review")
    ]
    return ended[-1] if ended else None


def done_branch(review: bool) -> str:
    return DONE_PREFIX + ("review" if review else "check")


def is_ancestor(root: Path, old: str, new: str) -> bool:
    return proc.git(root, "merge-base", "--is-ancestor", old, new, check=False).returncode == 0


def range_start(root: Path, events: list[dict], since: str | None, review: bool = False) -> tuple[str, datetime, str]:
    """(from のコミット, 期限の起点, 決め方)。

    **origin の `check-done/*` を手元の記録より先に見る。** 手元の記録は置き場が消えれば失われ、別のマシンからは
    見えない。失われたまま正式版のタグへ戻ると、検査を通らずに配布された変更が範囲から外れる。"""
    prev = last_check(events, review)
    ref = f"refs/remotes/origin/{done_branch(review)}"
    done = git_or_stop(root, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    if done and prev and prev["to"] != done and is_ancestor(root, done, prev["to"]):
        done = ""  # 送れなかった記録のほうが先にある（origin の位置は古い）
    if done:
        at = parse_at(prev["at"]) if prev and prev["to"] == done else commit_at(root, done)
        return done, at, f"origin/{done_branch(review)}"
    if prev:
        return prev["to"], parse_at(prev["at"]), f"前回の検査 {prev.get('id', '')}"
    if since:
        sha = git_or_stop(root, "rev-parse", f"{since}^{{commit}}")
        return sha, commit_at(root, sha), f"--since {since}"
    prefix = release_tag_glob(root)
    if prefix:
        for tag in git_or_stop(root, "tag", "--list", f"{prefix}*", "--sort=-v:refname").split():
            if "-" not in tag[len(prefix) :]:
                sha = git_or_stop(root, "rev-parse", f"{tag}^{{commit}}")
                return sha, commit_at(root, sha), f"正式版のタグ {tag}"
    base = range_base(root)
    sha = git_or_stop(root, "merge-base", f"origin/{base}", "HEAD")
    return sha, commit_at(root, sha), f"origin/{base} との分岐点"


def _scan_log(root: Path, frm: str, to: str, skip: set[int]) -> tuple[list[tuple], list[str]]:
    """範囲の first-parent のログから (sha, PR 番号, ブランチ名 or None) を拾い、skip の PR のコミットを外す。"""
    found = []
    excluded = []
    for line in git_or_stop(root, "log", "--first-parent", "--format=%H%x09%P%x09%s", f"{frm}..{to}").splitlines():
        sha, parents, subject = line.split("\t", 2)
        m = MERGE_SUBJECT.match(subject) if " " in parents else SQUASH_SUBJECT.search(subject.rstrip())
        if m and int(m.group(1)) in skip:
            excluded.append(sha)
        elif m:
            found.append((sha, int(m.group(1)), m.group(2) if m.re is MERGE_SUBJECT else None))
    return found, excluded


def _resolve_branches(found: list[tuple], root: Path) -> dict:
    """squash の PR（ブランチ名が None）のブランチ名を GitHub から読む。"""
    squashed = [n for _, n, branch in found if branch is None]
    heads, err = gh_rest.pr_head_branches(squashed, cwd=str(root)) if squashed else ({}, "")
    if heads is None:
        raise Stop(f"squash merge の PR のブランチを読めない: {err}", EXIT_VIOLATION)
    return heads


def _score_prs(found: list[tuple], heads: dict, root: Path, decl: dict) -> tuple[list[dict], list[str]]:
    """`SKIP_BRANCHES` のブランチを外し、残りの PR に共通層の判定と点数を付ける。"""
    out = []
    excluded = []
    for sha, n, branch in found:
        branch = heads.get(n, "") if branch is None else branch
        if branch.startswith(SKIP_BRANCHES):
            excluded.append(sha)
            continue
        files = git_or_stop(root, "diff", "--name-only", f"{sha}^1", sha).splitlines()
        common = any(area_of(f, decl)[1] for f in files)
        out.append({"pr": n, "branch": branch, "common": common, "points": decl["triggers"]["common_weight"] if common else 1})
    return out, excluded


def merged_prs(root: Path, frm: str, to: str, decl: dict, skip: set[int] = frozenset()) -> tuple[list[dict], list[str]]:
    """(範囲へ入った PR, 外した PR のコミット)。merge commit と squash merge（件名の末尾 `(#N)`）を数える。squash の
    件名にはブランチ名が残らないため、ブランチは GitHub から読んで `SKIP_BRANCHES` を当てる。検査の PR は記録の番号
    （skip）でも外す。外したコミットは行数からも差し引くために返す。"""
    found, excluded = _scan_log(root, frm, to, skip)
    heads = _resolve_branches(found, root)
    out, skipped = _score_prs(found, heads, root, decl)
    return out, excluded + skipped


def changed_lines(root: Path, frm: str, to: str, excluded: list[str] = ()) -> int:
    """範囲の変更行数。外した PR（検査・リリース）のコミットの分は差し引く。検査の修正が次の検査を立てないためである。"""

    def count(a: str, b: str) -> int:
        stat = git_or_stop(root, "diff", "--shortstat", a, b)
        return sum(int(n) for n in re.findall(r"(\d+) (?:insertion|deletion)", stat))

    return max(0, count(frm, to) - sum(count(f"{sha}^1", sha) for sha in excluded))


def escapes_since(events: list[dict], since: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        if e["kind"] == "escape" and parse_at(e["at"]) >= since:
            for a in e.get("areas") or []:
                counts[a] = counts.get(a, 0) + 1
    return counts


def evaluate(root: Path, final: bool, since: str | None, to_ref: str | None = None, review: bool = False) -> dict:
    decl = load_decl(root)
    events = read_events(root)
    frm, since_at, how = range_start(root, events, since, review)
    to = git_or_stop(root, "rev-parse", to_ref or f"origin/{range_base(root)}")
    prs, excluded = merged_prs(root, frm, to, decl, {e["pr"] for e in events if e["kind"] == "check" and isinstance(e.get("pr"), int)})
    t = decl["triggers"]
    esc = escapes_since(events, since_at)
    hours = round((clock.now(utc=True) - since_at).total_seconds() / 3600, 2)
    metrics = {
        "prs": len(prs),
        "score": sum(p["points"] for p in prs),
        "lines": changed_lines(root, frm, to, excluded),
        "hours": hours,
        "escapes": max(esc.values(), default=0),
        "from": frm,
        "to": to,
    }
    fired = []
    if review:
        if prs:
            fired.append({"trigger": "review", "value": len(prs), "threshold": 1})
        return {"decl": decl, "fired": fired, "metrics": metrics, "escape_areas": esc, "how": how, "prs": [p["pr"] for p in prs]}
    if metrics["score"] >= t["score"]:
        fired.append({"trigger": "score", "value": metrics["score"], "threshold": t["score"]})
    if metrics["lines"] > t["lines"]:
        fired.append({"trigger": "lines", "value": metrics["lines"], "threshold": t["lines"]})
    if metrics["escapes"] >= t["escapes"]:
        fired.append({"trigger": "escapes", "value": metrics["escapes"], "threshold": t["escapes"]})
    if hours >= t["hours"] and prs:
        fired.append({"trigger": "hours", "value": hours, "threshold": t["hours"]})
    if final and prs:
        fired.append({"trigger": "final", "value": len(prs), "threshold": 1})
    return {"decl": decl, "fired": fired, "metrics": metrics, "escape_areas": esc, "how": how, "prs": [p["pr"] for p in prs]}


# ---------------------------------------------------------------- 副命令


def cmd_eval(a, root: Path) -> tuple[dict, int]:
    ev = evaluate(root, a.final, a.since, review=a.review)
    m = ev["metrics"]
    names = [f["trigger"] for f in ev["fired"]]
    append_event(
        root,
        {
            "kind": "eval",
            "id": a.id or "",
            "from": m["from"],
            "to": m["to"],
            "fired": names,
            "metrics": {k: m[k] for k in ("prs", "score", "lines", "hours", "escapes")},
        },
    )
    rng = f"{m['from'][:10]}..{m['to'][:10]}（{ev['how']}から）"
    if names:
        return result(TOOL, "ok", f"検査のトリガーが立った（{' / '.join(names)}）: 範囲 {rng}・PR {m['prs']} 本", ev["fired"], m), EXIT_OK
    return result(
        TOOL,
        "stopped",
        f"検査のトリガーは立たない: 範囲 {rng}・PR {m['prs']} 本・点数 {m['score']}・{m['lines']} 行・{m['hours']} 時間",
        [],
        m,
    ), EXIT_PRECONDITION


def check_json(state: str) -> Path:
    return Path(state) / "check.json"


def delete_base(root: Path, name: str) -> None:
    branch = BASE_PREFIX + name
    git_or_stop(root, "push", "-q", "origin", "--delete", branch, check=False)
    git_or_stop(root, "branch", "-D", branch, check=False)


def cmd_prepare(a, root: Path) -> tuple[dict, int]:
    ev = evaluate(root, False, a.since, to_ref="HEAD", review=a.review)
    m = ev["metrics"]
    prev = [e for e in read_events(root) if e["kind"] == "eval" and e.get("id") == a.id]
    fired = prev[-1].get("fired", []) if prev else [f["trigger"] for f in ev["fired"]]
    branch = BASE_PREFIX + a.id
    git_or_stop(root, "branch", "-f", branch, m["from"])
    try:
        git_or_stop(root, "push", "-q", "-f", "origin", f"{branch}:refs/heads/{branch}")
    except Stop as e:
        raise Stop(f"{branch} を送れない: {e}", EXIT_VIOLATION)
    git_or_stop(root, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", check=False)
    files = git_or_stop(root, "diff", "--name-only", m["from"], m["to"]).splitlines()
    data = {
        "id": a.id,
        "from": m["from"],
        "to": m["to"],
        "fired": fired,
        "metrics": m,
        "files": files,
        "prs": ev["prs"],
        "escape_areas": ev["escape_areas"],
        "base": branch,
    }
    check_json(a.state).parent.mkdir(parents=True, exist_ok=True)
    check_json(a.state).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    return result(
        TOOL,
        "ok",
        f"{branch} を {m['from'][:10]} に作って送った（範囲の PR {m['prs']} 本・ファイル {len(files)} 件）",
        [{"branch": branch, "from": m["from"], "to": m["to"]}],
        {k: m[k] for k in ("prs", "score", "lines")},
    ), EXIT_OK


def scope_dirs(root: Path, data: dict) -> list[str]:
    decl = load_decl(root)
    escaped = set(data.get("escape_areas") or {})
    groups: list[list[str]] = [[], [], []]
    for f in data.get("files") or []:
        d = str(Path(f).parent)
        if d == ".":
            continue
        name, common = area_of(f, decl)
        g = 0 if common else 1 if name in escaped else 2
        if not any(d in grp for grp in groups):
            groups[g].append(d)
    return [d for grp in groups for d in grp] or ["."]


def cmd_scope(a, root: Path) -> int:
    p = check_json(a.state)
    if not p.is_file():
        print(f"check.json が無い: {p}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(" ".join(scope_dirs(root, json.loads(p.read_text()))))
    return EXIT_OK


def cmd_finish(a, root: Path) -> tuple[dict, int]:
    base = range_base(root)
    stat = git_or_stop(root, "diff", "--numstat", f"origin/{base}...HEAD")
    lines = sum(int(x) for ln in stat.splitlines() for x in ln.split("\t")[:2] if x.isdigit())
    files = len(stat.splitlines())
    if not files:
        gh_or_stop(root, "pr", "close", str(a.pr), "--comment", "検査で変更が無かったため閉じる（check-trigger.py finish）")
        return result(TOOL, "stopped", f"検査で変更が無い。#{a.pr} を閉じた（変更なし）", [], {"files": 0, "lines": 0}), EXIT_PRECONDITION
    try:
        gh_or_stop(root, "pr", "edit", str(a.pr), "--base", base)
    except Stop as e:
        raise Stop(f"#{a.pr} の宛先を {base} へ付け替えられない: {e}", EXIT_VIOLATION)
    return result(
        TOOL, "ok", f"#{a.pr} の宛先を {base} へ付け替えた（検査の修正 {files} ファイル・{lines} 行）", [], {"files": files, "lines": lines}
    ), EXIT_OK


def findings_of(state: Path) -> tuple[dict, str]:
    """計画の state.json から (件数, 最後に落ちたステップ) を読む。"""
    try:
        log = json.loads((state / "state.json").read_text()).get("log") or []
    except (OSError, ValueError):
        log = []
    counts = {e.get("id"): e.get("counts") or {} for e in log if e.get("counts")}
    ref, rev = counts.get("refactor", {}), counts.get("review", {})
    findings = {
        "applied": ref.get("adopted", ref.get("applied")),
        "reverted": ref.get("reverted"),
        **({"unconfirmed": ref["unconfirmed"]} if "unconfirmed" in ref else {}),  # 最終ゲートを経ていない実行だけ（#1652）
        # drive が返したキーだけを写す（古い drive の `findings` はコメントの数で、stats が読むときに読み替える）
        **{k: rev[k] for k in REVIEW_COUNTS if k in rev},
    }
    failed = [
        e.get("id") for e in log if e.get("exit") not in (0, None) and not e.get("gate") and not str(e.get("id", "")).startswith("abort")
    ]
    return findings, (failed[-1] if failed else "")


PR_END_RESULT = {"MERGED": "merged", "CLOSED": "no_change"}


def _append_failed(root: Path, row: dict) -> str:
    """落ちた検査の行を書き、書けたかの文言を返す（書けなくても止めない）。"""
    try:
        append_event(root, row)
        return "記録した"
    except OSError as e:
        return f"記録できない（{e}）"


def _ended_result(root: Path, n: int) -> str:
    """PR の状態を検査の終わり方（merged / no_change）へ写す。マージも閉じもされていなければ止める。"""
    state = json.loads(gh_or_stop(root, "pr", "view", str(n), "--json", "state")).get("state")
    res = PR_END_RESULT.get(state)
    if not res:
        raise Stop(f"#{n} がマージも閉じられもしていない（{state}）", EXIT_VIOLATION)
    return res


def _append_or_stop(root: Path, row: dict) -> None:
    try:
        append_event(root, row)
    except OSError as e:
        raise Stop(f"検査の記録へ書けない: {e}", EXIT_VIOLATION)


def merged_at(root: Path, n: int) -> str:
    """マージした PR のマージのコミット。手元へ取り込んでから返す（送る前に手元にコミットが要る）。読めなければ空。"""
    try:
        d = json.loads(gh_or_stop(root, "pr", "view", str(n), "--json", "state,mergeCommit,baseRefName"))
    except (Stop, ValueError):
        d = None
    sha = ((d.get("mergeCommit") or {}).get("oid") or "") if isinstance(d, dict) and d.get("state") == "MERGED" else ""
    if sha and d.get("baseRefName"):
        git_or_stop(root, "fetch", "-q", "origin", d["baseRefName"], check=False)
    return sha


def _pushed_result(root: Path, a_id: str, res: str, n: int, row: dict, to: str, review: bool) -> dict:
    """`check-done/*` を `to` へ進め、進めた ref と送れなかった ref を添えた記録の結果。"""
    pushed, unpushed = push_done(root, to, review)
    note = f"・origin の {' / '.join(pushed)} を進めた" if pushed else ""
    note += f"・{' / '.join(unpushed)} を送れない（次の範囲は手元の記録から決まる）" if unpushed else ""
    return result(TOOL, "ok", f"検査 {a_id} を記録した（{res}・#{n}）{note}", [row], {"pushed": pushed, "unpushed": unpushed})


def record_target(a, root: Path) -> tuple[dict, int]:
    """PR を指す検査（`new check --pr`）の記録。範囲の起点（`to`）と検査の PR（`pr`）を持たないため、差分の検査の
    範囲に影響しない。`check-base` を消さず、`check-done/*` を進めず、PR を閉じない（検査した PR は実装の PR である）。

    `--advance-done`（スプリントの検査。#1485 の決定 8）なら、検査が成功してマージした PR のマージのコミットを
    `to` にして `check-done/*` を進める。落ちた検査とマージしなかった検査は進めない。"""
    findings, failed_at = findings_of(Path(a.state))
    n = a.target_pr
    row = {
        "kind": "check",
        "id": a.id,
        "scope": "pr",
        "target_pr": n,
        "prs": [n],
        "from": "",
        "to": "",
        "metrics": {},
        "findings": findings,
    }
    if a.failed:
        row.update(result="failed", failed_at=failed_at or "不明")
        written = _append_failed(root, row)
        return result(TOOL, "stopped", f"検査 {a.id}（#{n}）が {row['failed_at']} で落ちた。{written}", [row], {}), EXIT_VIOLATION
    res = _ended_result(root, n)
    row["result"] = res
    row["to"] = merged_at(root, n) if a.advance_done and res == "merged" else ""
    _append_or_stop(root, row)
    if not a.advance_done:
        return result(TOOL, "ok", f"検査 {a.id} を記録した（{res}・#{n}）", [row], {}), EXIT_OK
    return _pushed_result(root, a.id, res, n, row, row["to"], False), EXIT_OK


def cmd_record(a, root: Path) -> tuple[dict, int]:
    if a.advance_done and a.target_pr is None:
        raise Stop("--advance-done は --target-pr と一緒に渡す")
    if a.target_pr is not None:
        if a.pr is not None or a.review:
            raise Stop("--target-pr は --pr / --review と同時に渡せない")
        return record_target(a, root)
    p = check_json(a.state)
    data = json.loads(p.read_text()) if p.is_file() else {}
    findings, failed_at = findings_of(Path(a.state))
    row = {
        "kind": "check",
        "id": a.id,
        "from": data.get("from", ""),
        "to": data.get("to", ""),
        "metrics": data.get("metrics", {}),
        "findings": findings,
        "scope": "since",
    }
    if isinstance(data.get("prs"), list):
        row["prs"] = data["prs"]
    if a.review:
        row["only"] = "review"
    if a.pr:
        row["pr"] = a.pr
    if a.failed:
        row.update(result="failed", failed_at=failed_at or "不明")
        written = _append_failed(root, row)
        delete_base(root, a.id)
        if a.pr:
            gh_call.gh(["pr", "close", str(a.pr), "--comment", "検査が途中で落ちたため閉じる"], cwd=str(root))
        return result(
            TOOL, "stopped", f"検査 {a.id} が {row['failed_at']} で落ちた。{written}・{BASE_PREFIX}{a.id} を消した", [row], {}
        ), EXIT_VIOLATION
    if not a.pr:
        raise Stop("record には --pr か --failed が要る")
    res = _ended_result(root, a.pr)
    row["result"] = res
    _append_or_stop(root, row)
    delete_base(root, a.id)
    return _pushed_result(root, a.id, res, a.pr, row, row["to"], a.review), EXIT_OK


def push_done(root: Path, to: str, review: bool) -> tuple[list[str], list[str]]:
    """見終えた位置を origin の check-done/* へ送る。構造改善を含む検査は実装レビューも通すため両方を進める。
    fast-forward だけで送り、origin が既に `to` と同じか先なら保つ（古い検査が後に終わっても見終えた位置を戻さない）。"""
    if not to:
        return [], []
    pushed, unpushed = [], []
    for name in [done_branch(True)] + ([] if review else [done_branch(False)]):
        ok = proc.git(root, "push", "-q", "origin", f"{to}:refs/heads/{name}", check=False).returncode == 0
        if not ok:
            git_or_stop(root, "fetch", "-q", "origin", f"+refs/heads/{name}:refs/remotes/origin/{name}", check=False)
            ok = is_ancestor(root, to, f"refs/remotes/origin/{name}")
        (pushed if ok else unpushed).append(name)
    return pushed, unpushed


def cmd_escape(a, root: Path) -> tuple[dict, int]:
    decl = load_decl(root)
    r = gh_rest.pr_files(a.pr, cwd=str(root))
    if r.returncode != 0:
        raise Stop(
            "gh が無い" if r.returncode == 127 else f"PR #{a.pr} の変更したファイルを読めない: {r.stderr.strip()[:300]}", EXIT_VIOLATION
        )
    areas: list[str] = []
    for f in json.loads(r.stdout):
        name = area_of(f.get("path", ""), decl)[0]
        if name not in areas:
            areas.append(name)
    row = {"kind": "escape", "pr": a.pr, "of": a.of, "areas": areas}
    try:
        append_event(root, row)
    except OSError as e:
        raise Stop(f"検査の記録へ書けない: {e}", EXIT_VIOLATION)
    of = f"#{a.of}" if a.of else "不明"
    return result(
        TOOL, "ok", f"逃げた不具合を記録した（直した #{a.pr}・持ち込んだ {of}・領域 {' / '.join(areas)}）", [row], {"areas": len(areas)}
    ), EXIT_OK


def cmd_changed(a, root: Path) -> tuple[dict, int]:
    rows = [e for e in read_events(root) if e.get("id") == a.id and e["kind"] in ("eval", "check")]
    checks = [e for e in rows if e["kind"] == "check"]
    if checks:
        res = checks[-1].get("result")
        if res == "merged":
            return result(TOOL, "ok", f"検査 {a.id} は変更をマージした", [checks[-1]], {}), EXIT_OK
        if res == "no_change":
            return result(TOOL, "stopped", f"検査 {a.id} は変更なし", [checks[-1]], {}), EXIT_PRECONDITION
        return result(TOOL, "stopped", f"検査 {a.id} は落ちた（{checks[-1].get('failed_at', '')}）", [checks[-1]], {}), EXIT_VIOLATION
    if rows and not rows[-1].get("fired"):
        return result(TOOL, "stopped", f"検査 {a.id} はトリガーが立たず流れていない（変更なし）", [rows[-1]], {}), EXIT_PRECONDITION
    raise Stop(f"検査 {a.id} の記録が無い")


def link_escape(e: dict, checks: list[dict]) -> tuple[dict | None, str]:
    """流出不具合を、持ち込んだ PR（`of`）を範囲（`prs`）に含めた最新の検査へ結び付ける。(検査, 結び付かない理由)。
    理由は `of_unknown`（`of: 0`）/ `no_check`（範囲に含めた検査が無い）/ `no_range`（その時点より前に範囲を持たない
    検査の行しか無い）。`scope` は問わない。"""
    of = e.get("of") or 0
    if not of:
        return None, "of_unknown"
    at = parse_at(e["at"])
    before = [c for c in checks if parse_at(c["at"]) < at]
    hit = [c for c in before if of in (c.get("prs") or [])]
    if hit:
        return hit[-1], ""
    return None, "no_range" if any(not isinstance(c.get("prs"), list) for c in before) else "no_check"


def link_escapes(escapes: list[dict], checks: list[dict]) -> tuple[dict[int, list[int]], list[dict]]:
    """流出不具合を検査へ結び付ける。(id(検査) -> 直した PR の列, 結び付かない流出不具合の列)。"""
    linked: dict[int, list[int]] = {}
    unlinked = []
    for e in escapes:
        c, reason = link_escape(e, checks)
        if c is None:
            unlinked.append({"pr": e.get("pr"), "of": e.get("of") or 0, "reason": reason})
        else:
            linked.setdefault(id(c), []).append(e.get("pr"))
    return linked, unlinked


def escapes_in_window(c: dict, i: int, windows: list[dict], escapes: list[dict]) -> int:
    """窓の i 番目の検査 c の後、次の記録までに記録された流出不具合の数。"""
    # 実装レビューだけの回は次の記録（どちらの検査もレビューを通る）までで切り、
    # 構造改善を含む検査は同じ種類の次の記録までで切る
    review = c.get("only") == "review"
    nxt = next((d for d in windows[i + 1 :] if review or d.get("only") != "review"), None)
    until = parse_at(nxt["at"]) if nxt else clock.now(utc=True)
    return sum(1 for e in escapes if parse_at(c["at"]) < parse_at(e["at"]) <= until)


def check_row(c: dict, linked: dict[int, list[int]]) -> dict:
    return {
        "id": c.get("id"),
        "at": c["at"],
        "result": c.get("result"),
        "scope": c.get("scope", "since"),
        "pr": c.get("pr"),
        "target_pr": c.get("target_pr"),
        "prs": c.get("prs"),
        "only": c.get("only"),
        "findings": read_findings(c.get("findings")),
        "escapes_linked": linked.get(id(c), []),
    }


def _fired_counts(evals: list[dict]) -> dict[str, int]:
    """トリガー別に立った回数を数える。"""
    fired: dict[str, int] = {}
    for e in evals:
        for t in e.get("fired") or []:
            fired[t] = fired.get(t, 0) + 1
    return fired


def _check_rows(checks: list[dict], windows: list[dict], escapes: list[dict], linked) -> list[dict]:
    """検査ごとの行に、窓を切る検査なら窓の中の逃げた不具合を足す。"""
    rows = []
    for c in checks:
        row = check_row(c, linked)
        i = next((k for k, w in enumerate(windows) if w is c), None)
        if i is not None:
            row["escapes_after"] = escapes_in_window(c, i, windows, escapes)
        rows.append(row)
    return rows


def _stats_metrics(root: Path, evals: list[dict], checks: list[dict], escapes: list[dict], unlinked) -> dict:
    return {
        "evals": len(evals),
        "fired": sum(1 for e in evals if e.get("fired")),
        "by_trigger": _fired_counts(evals),
        "checks": len(checks),
        "checks_pr": sum(1 for c in checks if c.get("scope") == "pr"),
        "failed": sum(1 for c in checks if c.get("result") == "failed"),
        "escapes": len(escapes),
        "escapes_linked": len(escapes) - len(unlinked),
        "escapes_unlinked": len(unlinked),
        "unlinked": unlinked,
        "log": str(log_path(root)),
    }


def cmd_stats(a, root: Path) -> tuple[dict, int]:
    events = read_events(root)
    evals = [e for e in events if e["kind"] == "eval"]
    checks = [e for e in events if e["kind"] == "check"]
    escapes = [e for e in events if e["kind"] == "escape"]
    ended = [e for e in checks if e.get("result") in ENDED]
    linked, unlinked = link_escapes(escapes, checks)
    # 時刻の窓を切るのは範囲が時刻で連続する検査（scope: since）だけ。PR を指す検査は窓を切らない
    windows = [e for e in ended if e.get("scope", "since") == "since"]
    rows = _check_rows(checks, windows, escapes, linked)
    metrics = _stats_metrics(root, evals, checks, escapes, unlinked)
    return result(
        TOOL,
        "ok",
        f"評価 {metrics['evals']} 回（立った {metrics['fired']}）・検査 {len(checks)} 回（PR を指す {metrics['checks_pr']}）"
        f"・逃げた不具合 {metrics['escapes']} 件（結び付いた {metrics['escapes_linked']}）",
        rows,
        metrics,
    ), EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="check-trigger.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, need_id: bool = False, state: bool = False) -> argparse.ArgumentParser:
        s = sub.add_parser(name)
        s.add_argument("--root")
        if need_id:
            s.add_argument("--id", required=True)
        if state:
            s.add_argument("--state", required=True, help="計画の状態ディレクトリ（check.json と state.json の置き場）")
        return s

    s = add("eval")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--final", action="store_true")
    g.add_argument("--review", action="store_true", help="実装レビューだけの検査（前回のレビューから PR が 1 本以上で立つ）")
    s.add_argument("--id", default="")
    s.add_argument("--since")
    s = add("prepare", True, True)
    s.add_argument("--since")
    s.add_argument("--review", action="store_true", help="範囲の起点をレビューだけの回を含む前回の検査にする")
    add("scope", True, True)
    s = add("finish", True)
    s.add_argument("--pr", type=int, required=True)
    s = add("record", True, True)
    s.add_argument("--pr", type=int)
    s.add_argument("--target-pr", type=int, help="PR を指す検査として記録する（検査した PR。範囲の起点にしない）")
    s.add_argument(
        "--advance-done",
        action="store_true",
        help="--target-pr の PR をマージしたら、そのマージのコミットへ check-done/* を進める（スプリントの検査）",
    )
    s.add_argument("--failed", action="store_true")
    s.add_argument("--review", action="store_true", help="レビューだけの回として記録する（only: review）")
    s = add("escape")
    s.add_argument("--pr", type=int, required=True)
    s.add_argument("--of", type=int, default=0, help="不具合を持ち込んだ PR（分からなければ 0）")
    add("changed", True)
    add("stats")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        root = repo_root(a.root)
        if a.cmd == "scope":
            return cmd_scope(a, root)
        fn = {
            "eval": cmd_eval,
            "prepare": cmd_prepare,
            "finish": cmd_finish,
            "record": cmd_record,
            "escape": cmd_escape,
            "changed": cmd_changed,
            "stats": cmd_stats,
        }[a.cmd]
        out, code = fn(a, root)
    except Stop as e:
        out, code = result(TOOL, "stopped", str(e), [], {}), e.code
    emit(out, code)


if __name__ == "__main__":
    sys.exit(main())
