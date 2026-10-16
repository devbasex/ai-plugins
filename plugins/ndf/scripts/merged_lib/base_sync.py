"""merged-steps.py の base-sync・base-return（ベースブランチの取り込みと、マージの直前の衝突からの戻り。#1917 #2003）。

base-sync: ベースブランチの先端を作業ブランチへマージのコミットで取り込む。途中の取り込み（`MERGE_HEAD`）があれば
閉じ、無ければ fetch して進んでいるときだけ `git merge` を打つ。衝突が用語集（`.ndf/glossary.json` の `source` と
`document`）に及べば `glossary.py merge` で解く。`--push` なら、upstream より進んでいるときだけ push する。
rebase・強制 push は打たない。終了コードは 0 = 進めてよい / 1 = 衝突が残る・push が拒まれた（修正の worker へ）/
2 = 読めない（fetch・gh・git）/ 3 = 拒まれた（未コミットの変更が取り込みと重なり、取り込みが始まらない）。

base-return: プランの merge が衝突（終了コード 4）を見たとき、この実行で戻った回数を `state.json` の `log` から数え、
上限（`RETURN_LIMIT`）を超えたら終了コード 1 で止まる。`state.json` は読むだけで書かない。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from merged_lib.checks import TOOL, pr_state
from step_result import EXIT_PRECONDITION, StepError, emit, git, git_root, result, run

RETURN_LIMIT = 3  # 1 回のプランの実行で merge から取り込みへ戻る回数の上限（#1917 の前提 2）
GLOSSARY_DECL = ".ndf/glossary.json"  # 用語集の設定（glossary.py の DECLARATION）
GLOSSARY_PY = Path(__file__).resolve().parent.parent / "glossary.py"
NO_EDIT = {**os.environ, "GIT_EDITOR": "true", "GIT_MERGE_AUTOEDIT": "no"}


def _merging(root) -> bool:
    return git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0


def _unmerged(root) -> list[str]:
    out = git(root, "diff", "-z", "--name-only", "--diff-filter=U").stdout
    return [p for p in out.split("\0") if p]


def _glossary_files(root) -> set[str]:
    """用語集の設定の `source` と `document`。設定が無い・読めなければ空。"""
    try:
        raw = json.loads((Path(root) / GLOSSARY_DECL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {v for v in (raw.get("source"), raw.get("document")) if isinstance(v, str) and v} if isinstance(raw, dict) else set()


def _base_of(root, a) -> str:
    if a.base:
        return a.base
    try:
        base = (pr_state(root, a.pr) or {}).get("baseRefName") or ""
    except StepError as e:  # 読めないときは worker へ回さない（2）
        raise StepError(str(e), 2)
    if not base:
        raise StepError(f"#{a.pr} の宛先（baseRefName）を読めない", 2)
    return base


def _stop(summary, paths, metrics, code=1):
    items = [{"kind": "conflict", "path": p, "result": "unresolved"} for p in paths]
    nxt = "衝突を両方の変更を残して解き、git add してマージのコミットを作ってから打ち直す（push しない）" if paths else None
    emit(result(TOOL, "stopped", summary, items, metrics, None, nxt), code)


def _push_if_ahead(root, items, metrics) -> None:
    """upstream より進んでいれば push する（upstream が無ければ origin へ upstream を付けて送る）。拒まれたら 1 で止まる。"""
    up = git(root, "rev-list", "--count", "@{u}..HEAD", check=False)
    if up.returncode == 0 and up.stdout.strip() == "0":
        return
    cmd = ("push", "-q") if up.returncode == 0 else ("push", "-q", "-u", "origin", "HEAD")
    p = git(root, *cmd, check=False)
    if p.returncode != 0:
        _stop(f"取り込みを push できない（push 前の検査を直してから打ち直す）: {p.stderr.strip()[:300]}", [], metrics)
    items.append({"kind": "push", "result": "pushed"})
    metrics["pushed"] = True


def _merge(root, base, metrics) -> str:
    """origin/<base> を取り込み、結果（merged / glossary_merged）を返す。衝突が残れば 1、拒まれたら 3 で止まる。"""
    p = git(root, "merge", "-q", "--no-edit", f"origin/{base}", check=False)
    if p.returncode == 0:
        return "merged"
    if not _merging(root):
        _stop(f"origin/{base} の取り込みが始まらない（未コミットの変更と重なる）: {p.stderr.strip()[:300]}", [], metrics, EXIT_PRECONDITION)
    left = _unmerged(root)
    if set(left) & _glossary_files(root):
        g = run([sys.executable, str(GLOSSARY_PY), "merge", "--root", str(root)], check=False)
        if g.returncode == 0:
            return "glossary_merged"
        left = _unmerged(root)
    _stop(f"origin/{base} の取り込みで衝突が {len(left)} 件残る", left, metrics)
    return ""  # emit で抜けるため届かない


def sync(a):
    root = git_root(a.root)
    items, metrics = [], {"base": a.base, "behind": 0, "pushed": False}
    if _merging(root):  # 修正の worker が解いた後の打ち直し
        if left := _unmerged(root):
            _stop(f"取り込みの途中で衝突が {len(left)} 件残る", left, metrics)
        p = run(["git", "-C", str(root), "commit", "--no-edit"], check=False, env=NO_EDIT)
        if p.returncode != 0:
            _stop(f"取り込みのコミットを作れない: {(p.stderr or p.stdout).strip()[:300]}", [], metrics)
        base, state = a.base or "", "finished"
    else:
        base = _base_of(root, a)
        metrics["base"] = base
        if git(root, "fetch", "-q", "origin", base, check=False).returncode != 0:
            raise StepError(f"git fetch origin {base} が失敗した", 2)
        behind = git(root, "rev-list", "--count", f"HEAD..origin/{base}", check=False)
        if behind.returncode != 0:
            raise StepError(f"origin/{base} を読めない: {behind.stderr.strip()[:300]}", 2)
        metrics["behind"] = int(behind.stdout.strip() or 0)
        state = _merge(root, base, metrics) if metrics["behind"] else "up_to_date"
    items.append({"kind": "base", "name": base, "result": state})
    if a.push:
        _push_if_ahead(root, items, metrics)
    said = {"up_to_date": "進んでいない", "merged": "取り込んだ", "glossary_merged": "取り込み、用語集の衝突を解いた",
            "finished": "取り込みのコミットを作った"}[state]  # fmt: skip
    emit(result(TOOL, "ok", f"ベースブランチ {base or '（途中の取り込み）'}: {said}" + ("。push した" if metrics["pushed"] else ""), items, metrics))


def count_returns(state_dir) -> int:
    """この実行で取り込みへ戻った回数（`state.json` の `log` の、base-return の成功の行の数）。"""
    try:
        log = json.loads((Path(state_dir) / "state.json").read_text(encoding="utf-8")).get("log") or []
    except (OSError, ValueError, AttributeError) as e:
        raise StepError(f"{state_dir}/state.json を読めない: {e}", 2)
    return sum(1 for e in log if isinstance(e, dict) and e.get("id") == "base-return" and e.get("exit") == 0)


def back(a):
    k, limit = count_returns(a.state) + 1, a.limit
    metrics = {"returns": k, "limit": limit}
    if k > limit:
        why = f"ベースブランチが進み続けて衝突した（マージの直前の衝突 {k} 回目。戻りの上限 {limit} 回）"
        emit(result(TOOL, "stopped", why, [{"kind": "return", "result": "limit"}], metrics))
    emit(result(TOOL, "ok", f"ベースブランチの取り込みへ戻る（{k} 回目）", [{"kind": "return", "result": "back"}], metrics))


def add_parsers(sub, common):
    """merged-steps.py のサブコマンド base-sync と base-return を登録する。"""
    s = sub.add_parser("base-sync", parents=[common()], help="ベースブランチを作業ブランチへマージのコミットで取り込む")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--base", help="ベースブランチの名前")
    src.add_argument("--pr", type=int, help="宛先（baseRefName）をベースブランチにする Pull Request")
    s.add_argument("--push", action="store_true", help="取り込みの結果が upstream より進んでいれば push する")
    s.set_defaults(func=sync)
    r = sub.add_parser("base-return", parents=[common()], help="merge の衝突から取り込みへ戻る回数を数え、上限を超えたら止まる")
    r.add_argument("--state", required=True, help="プランの状態ディレクトリ（state.json の置き場）")
    r.add_argument("--limit", type=int, default=RETURN_LIMIT, help="戻りの上限（回）")
    r.set_defaults(func=back)
