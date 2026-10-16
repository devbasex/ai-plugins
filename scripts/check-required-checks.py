#!/usr/bin/env python3
"""ワークフローのジョブから決まるチェックの名前と、宛先のブランチの必須のチェックを突き合わせる（#653）。

Pull Request で走るジョブを足したのに ruleset の必須のチェックへ入れ忘れると、そのチェックは落ちても
マージを塞がない。このスクリプトは次の 3 つを読んで差を出す（どれも書き換えない）。

- ワークフロー（`--workflows`）: `pull_request` / `pull_request_target` で走るジョブのチェックの名前
  （`name:` か job id に matrix の値を付けたもの。Pull Request に載る名前と同じ形）
- 宛先のブランチの必須のチェック: `gh api --method GET repos/<repo>/rules/branches/<branch>`
  （`--rules-file` を渡せば、その応答の JSON を API の代わりに読む）
- 必須のチェックの宣言（`--allow`）: 必須にしないジョブ（`not_required`）と、ruleset へ足す承認を待つ
  チェック（`awaiting`）を、理由と組で持つ

出力は 1 件 1 行で `<種類>: <チェックの名前>（<ワークフローのパス>#<job id>）→ <直し方>`、最後に件数の行。

終了コード: 0 = 失敗の種類が無い（知らせだけはあってよい）/ 1 = 失敗の種類が 1 件以上 /
2 = 読めない（必須の一覧・ワークフロー・宣言。宛先のブランチが存在しないときを含む）。2 は差を出さない。
`required_status_checks` の規則を持たない実在のブランチは「対象外」を知らせて 0 で終える。

リポジトリ名・ブランチ・置き場は引数で受ける（既定は GitHub Actions の環境変数と相対パスだけ）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("yamlio")
import ci_workflows  # noqa: E402  必須の一覧の取り出しは解析と共有する（#653 の決定 6）
import yamlio  # noqa: E402  matrix と name: を構造として読む（#653 の決定 7）

CALL_LIMIT = 30  # gh の 1 回の呼び出しの上限（秒）
PR_EVENTS = ("pull_request", "pull_request_target")
MATRIX_EXPR = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}")
DECL_KEYS = {"version", "not_required", "awaiting"}
ENTRY_KEYS = {"name", "reason"}

# 種類 → (失敗か, 直し方)。設計の「入出力の契約」の表と同じ並び
KINDS = {
    "未登録": (True, "ruleset の必須のチェックへ足す（追加待ちに置く）か、必須にしないジョブとして理由つきで宣言する"),
    "消えた必須": (True, "ruleset から外すか、ジョブの名前を戻す"),
    "名前の重なり": (True, "どちらかのジョブの name: か job id を変える"),
    "名前を決められない": (True, "name: を matrix の式だけにするか、必須にしないジョブとして宣言する"),
    "理由の無い宣言": (True, "reason を書く"),
    "古い宣言": (True, "宣言から外す"),
    "宣言と ruleset の食い違い": (True, "必須にしないジョブの宣言から外すか、ruleset から外す"),
    "絞り込みのあるジョブ": (True, "paths の絞り込みを外すか、必須にしないジョブとして宣言する"),
    "追加待ち": (False, "承認ゲート 2 で ruleset へ足す"),
    "追加済み": (False, "追加待ちの宣言から外す"),
    "対象外": (False, "何もしない（宛先のブランチは必須のチェックを持たない）"),
}


class Unreadable(Exception):
    """読めない（終了コード 2）。"""


class CheckName(NamedTuple):
    name: str
    workflow: str  # 根からの相対パス
    job_id: str
    filtered: bool  # pull_request の起動を paths / paths-ignore で絞っている


class Finding(NamedTuple):
    kind: str
    name: str
    where: str

    def line(self) -> str:
        return f"{self.kind}: {self.name}（{self.where}）→ {KINDS[self.kind][1]}"


# --- 宣言 -------------------------------------------------------------------


class Declaration(NamedTuple):
    not_required: list[dict]
    awaiting: list[dict]


def read_declaration(path: Path) -> Declaration:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Unreadable(f"宣言を読めない: {path}（{e}）") from None
    if not isinstance(data, dict):
        raise Unreadable(f"宣言がオブジェクトでない: {path}")
    if unknown := set(data) - DECL_KEYS:
        raise Unreadable(f"宣言に未知のキーがある: {path}（{', '.join(sorted(unknown))}）")
    lists = []
    for key in ("not_required", "awaiting"):
        rows = data.get(key) or []
        if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"] for r in rows):
            raise Unreadable(f"宣言の {key} が {{name, reason}} の並びでない: {path}")
        for r in rows:
            if unknown := set(r) - ENTRY_KEYS:
                raise Unreadable(f"宣言に未知のキーがある: {path} の {key}（{', '.join(sorted(unknown))}）")
        lists.append(rows)
    return Declaration(*lists)


# --- ワークフロー -----------------------------------------------------------


def _glob(pattern: str) -> re.Pattern:
    """GitHub のブランチの絞り込みの書き方（`*` は `/` を除く、`**` はすべて）を正規表現へ直す。"""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + ".", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def _branch_matches(spec: dict, branch: str) -> bool:
    if "branches" in spec:
        hit = False
        for p in spec.get("branches") or []:
            neg = str(p).startswith("!")
            if _glob(str(p)[1:] if neg else str(p)).match(branch):
                hit = not neg
        return hit
    if "branches-ignore" in spec:
        return not any(_glob(str(p)).match(branch) for p in spec.get("branches-ignore") or [])
    return True


def pr_trigger(on: object, branch: str) -> tuple[bool, bool]:
    """`on:` → (宛先のブランチへの Pull Request で走るか, paths で絞っているか)。"""
    if isinstance(on, str):
        on = [on]
    if isinstance(on, list):
        return any(e in PR_EVENTS for e in on), False
    if not isinstance(on, dict):
        return False, False
    runs, filtered = False, False
    for event in PR_EVENTS:
        if event not in on:
            continue
        spec = on.get(event) or {}
        if not isinstance(spec, dict) or _branch_matches(spec, branch):
            runs = True
            filtered = filtered or (isinstance(spec, dict) and ("paths" in spec or "paths-ignore" in spec))
    return runs, filtered


def _scalar(v: object) -> str | None:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (str, int, float)):
        return str(v)
    return None


def matrix_combos(matrix: object) -> list[dict[str, str]] | None:
    """matrix → 組の並び（GitHub の exclude / include の規則で作る）。静的に決められなければ None。"""
    if not isinstance(matrix, dict):
        return None
    base: dict[str, list[str]] = {}
    for key, values in matrix.items():
        if key in ("include", "exclude"):
            continue
        if not isinstance(values, list) or not values:
            return None
        vals = [_scalar(v) for v in values]
        if None in vals:
            return None
        base[str(key)] = vals

    def objects(key: str) -> list[dict[str, str]] | None:
        rows = matrix.get(key) or []
        if not isinstance(rows, list):
            return None
        out = []
        for row in rows:
            if not isinstance(row, dict):
                return None
            obj = {str(k): _scalar(v) for k, v in row.items()}
            if None in obj.values():
                return None
            out.append(obj)
        return out

    include, exclude = objects("include"), objects("exclude")
    if include is None or exclude is None:
        return None
    combos = [dict(zip(base, vals)) for vals in itertools.product(*base.values())] if base else []
    combos = [c for c in combos if not any(all(c.get(k) == v for k, v in e.items()) for e in exclude)]
    originals = len(combos)
    for obj in include:
        added = False
        for c in combos[:originals]:
            if all(c[k] == v for k, v in obj.items() if k in base):
                c.update(obj)
                added = True
        if not added:
            combos.append(dict(obj))
    return combos or None


def job_check_names(job_id: str, job: object) -> list[str] | None:
    """1 ジョブ → チェックの名前の並び。静的に決められなければ None（設計の I2）。"""
    if not isinstance(job, dict) or "uses" in job:
        return None
    raw = job.get("name", job_id)
    if not isinstance(raw, str):
        return None
    name = str(raw)
    matrix = (job.get("strategy") or {}).get("matrix") if isinstance(job.get("strategy"), dict) else None
    if matrix is None:
        return None if "${{" in name else [name]
    combos = matrix_combos(matrix)
    if combos is None:
        return None
    if "${{" in name:
        if "${{" in MATRIX_EXPR.sub("", name):
            return None  # matrix 以外の式
        if any(k not in c for c in combos for k in MATRIX_EXPR.findall(name)):
            return None
        return [MATRIX_EXPR.sub(lambda m, c=c: c[m.group(1)], name) for c in combos]
    # 名前に matrix の式が無いときは GitHub が「名前 (値, 値)」を付ける。キーが 2 つ以上のときの並びは
    # 実測していないため、キーが 1 つのときだけ決める（設計の「未確認のまま残ること」）
    keys = {k for c in combos for k in c}
    if len(keys) != 1:
        return None
    return [f"{name} ({next(iter(c.values()))})" for c in combos]


def read_workflows(root: Path, wf_dir: Path, branch: str) -> tuple[list[CheckName], list[Finding]]:
    names: list[CheckName] = []
    findings: list[Finding] = []
    if not wf_dir.is_dir():
        raise Unreadable(f"ワークフローの置き場が無い: {wf_dir}")
    for path in sorted(p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()):
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        try:
            data = yamlio.load_yaml(path.read_text(encoding="utf-8"), rel)
        except (OSError, yamlio.YamlError) as e:
            raise Unreadable(f"ワークフローを読めない: {e}") from None
        if not isinstance(data, dict):
            raise Unreadable(f"ワークフローがオブジェクトでない: {rel}")
        runs, filtered = pr_trigger(data.get("on", data.get(True)), branch)
        if not runs:
            continue
        for job_id, job in (data.get("jobs") or {}).items():
            got = job_check_names(str(job_id), job)
            if got is None:
                findings.append(
                    Finding(
                        "名前を決められない",
                        str((job or {}).get("name", job_id)) if isinstance(job, dict) else str(job_id),
                        f"{rel}#{job_id}",
                    )
                )
                continue
            names += [CheckName(n, rel, str(job_id), filtered) for n in got]
    return names, findings


# --- 必須の一覧 -------------------------------------------------------------


def _gh_get(endpoint: str) -> tuple[int, str, str]:
    """`gh api --method GET` を 1 回打つ → (終了コード, 標準出力, 標準エラー)。書き込みのメソッドは打たない。"""
    try:
        p = subprocess.run(["gh", "api", "--method", "GET", endpoint], capture_output=True, text=True, timeout=CALL_LIMIT)
    except FileNotFoundError:
        raise Unreadable("gh が無い") from None
    except subprocess.TimeoutExpired:
        raise Unreadable(f"gh api {endpoint} が {CALL_LIMIT} 秒で終わらない（時間切れ）") from None
    return p.returncode, p.stdout, p.stderr


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0]


def read_required(repo: str, branch: str, rules_file: Path | None) -> list[str] | None:
    """必須のチェックの名前。規則を持たない実在のブランチなら None。読めなければ Unreadable。"""
    endpoint = f"repos/{repo}/rules/branches/{quote(branch, safe='/')}"
    if rules_file is not None:
        try:
            text = rules_file.read_text(encoding="utf-8")
        except OSError as e:
            raise Unreadable(f"必須の一覧のファイルを読めない: {e}") from None
    else:
        code, text, err = _gh_get(endpoint)
        if code != 0:
            raise Unreadable(f"必須の一覧を読めない: gh api {endpoint} が終了コード {code}（{_first_line(err)}）")
    try:
        contexts = ci_workflows.required_contexts(json.loads(text))
    except ValueError as e:
        raise Unreadable(f"必須の一覧を読めない: 応答が規則の JSON の配列でない（{e}）") from None
    if contexts is not None:
        return contexts
    # 規則が無い: 規則を持たない実在のブランチか、ブランチ名の誤りかを見分ける（設計の決定 5）
    code, _, err = _gh_get(f"repos/{repo}/branches/{quote(branch, safe='/')}")
    if code == 0:
        return None
    if "HTTP 404" in err:
        raise Unreadable(f"宛先のブランチが無い: {repo} の {branch}")
    raise Unreadable(f"宛先のブランチを確かめられない: 終了コード {code}（{_first_line(err)}）")


# --- 突き合わせ -------------------------------------------------------------


def compare(names: list[CheckName], undetermined: list[Finding], required: list[str], decl: Declaration, allow_rel: str) -> list[Finding]:
    """差の一覧。名前を決められないジョブは、その name:（無ければ job id）を必須にしないジョブとして宣言すれば数えない。"""
    out: list[Finding] = []
    by_name: dict[str, list[CheckName]] = {}
    for c in names:
        by_name.setdefault(c.name, []).append(c)
    for n, cs in by_name.items():
        if len(cs) > 1:
            out.append(Finding("名前の重なり", n, "・".join(f"{c.workflow}#{c.job_id}" for c in cs)))
    req = set(required)
    not_req = {r["name"] for r in decl.not_required}
    awaiting = {r["name"] for r in decl.awaiting}
    out += [u for u in undetermined if u.name not in not_req]
    known = set(by_name) | {u.name for u in undetermined}
    for n, cs in by_name.items():
        where = f"{cs[0].workflow}#{cs[0].job_id}"
        if n not in req | not_req | awaiting:
            out.append(Finding("未登録", n, where))
        elif any(c.filtered for c in cs) and n in req | awaiting:
            out.append(Finding("絞り込みのあるジョブ", n, where))
    out += [Finding("消えた必須", n, "ruleset の必須のチェック") for n in sorted(req - set(by_name))]
    for key, rows in (("not_required", decl.not_required), ("awaiting", decl.awaiting)):
        for r in rows:
            where = f"{allow_rel} の {key}"
            if not str(r.get("reason") or "").strip():
                out.append(Finding("理由の無い宣言", r["name"], where))
            if r["name"] not in known:
                out.append(Finding("古い宣言", r["name"], where))
    out += [Finding("宣言と ruleset の食い違い", n, f"{allow_rel} の not_required") for n in sorted(not_req & req)]
    for r in decl.awaiting:
        kind = "追加済み" if r["name"] in req else "追加待ち"
        out.append(Finding(kind, r["name"], f"{allow_rel} の awaiting"))
    return out


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()

    def under(p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else root / p

    try:
        if not args.branch:
            raise Unreadable("宛先のブランチが空（--branch も GITHUB_BASE_REF も無い）")
        if not args.repo:
            raise Unreadable("リポジトリが空（--repo も GITHUB_REPOSITORY も無い）")
        allow = under(args.allow)
        decl = read_declaration(allow)
        names, undetermined = read_workflows(root, under(args.workflows), args.branch)
        required = read_required(args.repo, args.branch, under(args.rules_file) if args.rules_file else None)
    except Unreadable as e:
        print(f"読めない: {e}", file=sys.stderr)
        return 2
    if required is None:
        print(Finding("対象外", args.branch, f"{args.repo} の必須のチェックの規則が無い").line())
        print("失敗 0 件・知らせ 1 件")
        return 0
    allow_rel = allow.relative_to(root).as_posix() if allow.is_relative_to(root) else str(allow)
    findings = compare(names, undetermined, required, decl, allow_rel)
    findings.sort(key=lambda f: (not KINDS[f.kind][0], list(KINDS).index(f.kind), f.name))
    for f in findings:
        print(f.line())
    failed = sum(KINDS[f.kind][0] for f in findings)
    print(f"失敗 {failed} 件・知らせ {len(findings) - failed} 件")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--branch", default=os.environ.get("GITHUB_BASE_REF", ""))
    ap.add_argument("--workflows", default=ci_workflows.WORKFLOW_DIR)
    ap.add_argument("--allow", default="scripts/required-checks-allow.json")
    ap.add_argument("--rules-file", default=None, help="rules/branches の応答の JSON を API の代わりに読む")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
