#!/usr/bin/env python3
"""project-decl.py: プロジェクトの宣言（`.ndf/project.json`）の判定・測定・書き出し（#1333）。

    python3 project-decl.py check   [--force] [--root DIR]
    python3 project-decl.py measure --out FILE [--budget 秒] [--root DIR]
    python3 project-decl.py write   --measure FILE [--answers FILE] [--dry-run] [--root DIR]
    python3 project-decl.py schema  [--out FILE]

`development-workflow` の手順 0 が `check` を打ち、2 なら measure → 答え（conductor が書く）→ write を通す。
答え方と再解析は `skills/development-workflow/references/project-analysis.md` にある。

- `check`: 0 = 新しい / 2 = 無い・古い（`--force` なら新しくても 2）/ 3 = 壊れている / 1 = 判定できない。
  LLM も `gh` も呼ばず、ファイルを書かない
- `measure`: 0 = 測定の結果を書けた（項目が不明でも 0）/ 1 = `--out` を書けない。標準ライブラリと `git`・`gh` だけで動く。
  書くのは `--out` だけで、`gh` へは読む要求だけを渡す
- `write`: 0 = 書けた・書くものが無い / 1 = 書けない / 3 = 既存の宣言が壊れている（作り直さない）
- `schema`: 宣言の JSON Schema（`skills/development-workflow/schemas/project.schema.json` の正本）を出す

結果は lib/step_result.py の形の 1 行の JSON で、その前に人が読む行を出す。宣言はメインディレクトリの `.ndf/` に置く。
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
import deps  # noqa: E402
import jsonio  # noqa: E402
import proc  # noqa: E402
import repo  # noqa: E402
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, main_with, result  # noqa: E402

from project_lib import ANALYZER, BRANCHES, DECL_FILE, ITEM_KEYS, WORKTREE_FILE, fingerprint, merge  # noqa: E402
from project_lib import measure_ci as mci  # noqa: E402
from project_lib import measure_repo as mr  # noqa: E402
from project_lib.secret import mask  # noqa: E402

TOOL = "project-decl"
EXIT_STALE = EXIT_UNREADABLE  # 2 = 無い・古い（手順 0 が解析へ進む）
P_NUMBER = {
    "languages": "P1",
    "test": "P2",
    "test_duration": "P3",
    "ci": "P4",
    "services": "P5",
    BRANCHES: "P6",
    "delivery": "P7",
    "issues": "P8",
    "checks": "P9",
    "ndf_policies": "P9",
    "instructions": "P10",
}
KIND_LABEL = {"written": "書いた", "kept": "手の値を保った", "mismatch": "食い違い", "unknown": "不明", "secret": "秘密の形"}


def _main_dir(root: Path) -> Path:
    return repo.main_dir(root) or root


def _load_model():
    deps.require("schema")
    from project_lib import model

    return model


def _read_decl_json(path: Path, name: str):
    """宣言の JSON。無ければ `None`、読めなければ 3 の `StepError`（作り直さない）。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"{name} を JSON として読めない（{e}）。直してから打ち直す", EXIT_PRECONDITION) from None
    if not isinstance(data, dict):
        raise StepError(f"{name} の根がオブジェクトでない。直してから打ち直す", EXIT_PRECONDITION)
    return data


def _load_decl(main: Path, model):
    data = _read_decl_json(main / DECL_FILE, str(DECL_FILE))
    if data is not None:
        try:
            model.validate_decl(data)
        except model.schema.ShapeError as e:
            raise StepError(f"{DECL_FILE} が形に合わない: {e}。直してから打ち直す", EXIT_PRECONDITION) from None
    return data


# --- check ------------------------------------------------------------------


def cmd_check(a):
    try:
        root = proc.git_root(a.root)
    except StepError as e:
        raise StepError(str(e), 1) from None  # 2 は「無い・古い」なので、判定できないときは 1 で返す
    if proc.git_out(root, "rev-parse", "--show-toplevel") is None:
        raise StepError(f"{root} は git の作業ツリーではない", 1)
    model = _load_model()
    main = _main_dir(root)
    data = _load_decl(main, model)
    reasons = []
    if data is None:
        reasons.append("宣言が無い")
    elif not data.get("analysis"):
        reasons.append("解析の記録（analysis）が無い（手で書いた宣言）")
    else:
        an = data["analysis"]
        if an.get("analyzer", 0) < ANALYZER:
            reasons.append(f"解析器の版が古い（{an.get('analyzer')} < {ANALYZER}）")
        changed = fingerprint.diff(an.get("inputs") or {}, fingerprint.inputs(root))
        reasons += [f"変わった入力: {p}" for p in changed]
        now = fingerprint.branch_state(root, fingerprint.declared_branches(main))
        if (an.get("branches") or {}) != now:
            reasons.append(f"変わった入力: ブランチの構成（{an.get('branches')} → {now}）")
    if not reasons and a.force:
        reasons.append("変わった入力: 強制（--force）")
    if reasons:
        print(f"プロジェクトの宣言: {'無い' if data is None else '古い'}（{DECL_FILE}）")
        for r in reasons:
            print(r)
        emit(
            result(TOOL, "stopped", "；".join(reasons), [{"kind": "reason", "name": r, "result": "stale"} for r in reasons]),
            EXIT_STALE,
        )
    print(f"プロジェクトの宣言: 新しい（{DECL_FILE}）")
    emit(result(TOOL, "ok", "宣言は新しい", [], {"inputs": len((data.get("analysis") or {}).get("inputs") or {})}))


# --- measure ----------------------------------------------------------------


def _github_repo(root) -> str | None:
    url = proc.git_out(root, "remote", "get-url", "origin") or ""
    return repo.owner_repo_from_url(url) if "github.com" in url else None


def _measure_tree_items(tree, step, items: dict) -> dict:
    """git の木から測る項目を `items` へ入れ、リポジトリの中の課題の置き場（`issues` の材料）を返す。"""
    dep = step("languages", lambda: mr.dependencies(tree))
    if dep is None:
        dep = {"php": {}, "javascript": {}, "python": {}}
    else:
        v = step("languages", lambda: mr.measure_languages(tree, dep))
        if v is not None:
            items["languages"] = v
    got = step("services", lambda: mr.measure_services(tree))
    services = []
    if got:
        items["services"], services = got
    for key, fn in (
        ("test", lambda: mr.measure_test(tree, dep, services)),
        (BRANCHES, lambda: mr.measure_branches(tree)),
        ("delivery", lambda: mr.measure_delivery(tree)),
        ("checks", lambda: mr.measure_checks(tree, dep)),
        ("ndf_policies", lambda: mr.measure_policies(tree)),
        ("instructions", lambda: mr.measure_instructions(tree)),
    ):
        v = step(key, fn)
        if v is not None:
            items[key] = v
    return step("issues", lambda: mr.measure_issues_repo(tree)) or {"markdown_files": 0, "templates": [], "hosts": []}


def _measure_ci_items(tree, step, items: dict, root: Path, deadline: float, repo_issues: dict) -> list:
    """`gh` と CI から測る項目を `items` へ入れ、指示ファイルと CI の注記を返す。"""
    head = fingerprint.branch_state(root)["head"]
    ci_part = step("ci", lambda: mci.measure_ci(tree, _github_repo(root), head, deadline))
    notes = list((items.get("instructions") or {}).get("value", {}).get("notes") or [])
    if ci_part:
        items["ci"], items["test_duration"] = ci_part["ci"], ci_part["test_duration"]
        notes += ci_part["notes"]
        if ci_part.get("merges") and items.get(BRANCHES, {}).get("status") == "question":
            counts = "・".join(f"{k} {v}" for k, v in sorted(ci_part["merges"].items(), key=lambda kv: -kv[1]))
            items[BRANCHES]["evidence"].append({"text": f"直近 100 件のマージ先: {counts}"})
    else:
        items.setdefault("test_duration", items.get("ci") or mr.unknown("時間切れ"))
    if "issues" not in items:
        items["issues"] = mci.measure_issues(repo_issues, ci_part or {})
    return notes


def measure_project(root: Path, budget: float) -> dict:
    """P1〜P10 を測る。git の木から測る項目を先に、残りの時間で `gh` の項目を測る。"""
    start = time.monotonic()
    deadline = start + budget
    tree = mr.Tree(root, deadline)
    items: dict = {}

    def step(key, fn):
        if time.monotonic() >= deadline:
            items[key] = mr.unknown("時間切れ")
            return None
        try:
            return fn()
        except mr.TimeUp:  # 途中で締め切りを越えた項目は、読めた分だけで測ったことにしない
            items[key] = mr.unknown("時間切れ")
            return None
        except Exception as e:  # noqa: BLE001  1 項目の失敗で測定を止めない（I12）
            items[key] = mr.unknown(f"測れない: {type(e).__name__}: {str(e)[:200]}")
            return None

    repo_issues = _measure_tree_items(tree, step, items)
    notes = _measure_ci_items(tree, step, items, root, deadline, repo_issues)
    if tree.skipped:
        notes.append(f"秘密の名前のファイルを開かなかった: {'・'.join(tree.skipped[:10])}")
    return {
        "analyzer": ANALYZER,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "items": {k: items[k] for k in (*ITEM_KEYS, BRANCHES) if k in items},
        "inputs": fingerprint.inputs(root, tree.files),
        "branch_state": fingerprint.branch_state(root, fingerprint.declared_branches(_main_dir(root))),
        "skipped_files": tree.skipped,
        "notes": notes,
    }


def cmd_measure(a):
    root = proc.git_root(a.root)
    out = Path(a.out).resolve()
    try:
        out.relative_to(root.resolve())
        raise StepError(f"--out はリポジトリの外（一時ディレクトリ）に置く: {out}", 1)
    except ValueError:
        pass
    data = measure_project(root, a.budget)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(out, data)
    except OSError as e:
        raise StepError(f"--out を書けない: {e}", 1) from None
    counts: dict[str, int] = {}
    for m in data["items"].values():
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    print(f"測定の結果: {out}（{data['elapsed_seconds']} 秒）")
    for key, m in data["items"].items():
        print(f"  {P_NUMBER.get(key, '')} {key}: {m['status']}{'（' + m['reason'] + '）' if m.get('reason') else ''}")
    emit(
        result(
            TOOL,
            "ok",
            f"測った（{data['elapsed_seconds']} 秒）",
            [],
            {**counts, "elapsed_seconds": data["elapsed_seconds"], "out": str(out)},
        )
    )


# --- write ------------------------------------------------------------------


def _checker(model):
    def check(key, value):
        try:
            model.validate_item(key, value)
        except model.schema.ShapeError as e:
            return str(e)
        return None

    return check


def _same_but_time(old: dict | None, new: dict) -> bool:
    if old is None:
        return False
    strip = lambda d: {**d, "analysis": {k: v for k, v in (d.get("analysis") or {}).items() if k != "at"}}  # noqa: E731
    return merge.value_digest(strip(old)) == merge.value_digest(strip(new))


def _row_line(row: dict) -> str:
    key, kind = row["key"], row["kind"]
    p = P_NUMBER.get(key.split("#")[0], "P6" if key.startswith("worktree.json") else "")
    if kind == "mismatch":
        return f"  {p} {key}: 食い違い 宣言 {json.dumps(row['declared'], ensure_ascii=False)} / 解析 {json.dumps(row['analyzed'], ensure_ascii=False)}"
    if kind == "unknown":
        tail = "（前の値を残した）" if row.get("kept_previous") else ""
        return f"  {p} {key}: 不明（{row.get('reason')}）{tail}"
    if kind == "secret":
        return f"  {p} {key}: 秘密の形の値を捨てて不明にした"
    if kind == "written":
        return f"  {p} {key}: {row.get('origin')}"
    return f"  {p} {key}: {KIND_LABEL.get(kind, kind)}"


def _put_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        raise StepError(f"{path} を書けない: {e}", 1) from None


def _merge_worktree_part(main, wt, meas, answers, check, written_before):
    files = []
    if wt is None:
        rows = [
            {
                "kind": "note",
                "key": str(WORKTREE_FILE),
                "text": "worktree.json が無い（worktree-setup.sh init を先に通す）。起点と本番を書かない",
            }
        ]
        return files, rows, {}, []
    branches, origin = merge.branch_answer(meas, answers, check)
    new_wt, rows, wt_written = merge.merge_worktree(wt, branches, origin, written_before)
    if new_wt != wt:
        files.append((main / WORKTREE_FILE, str(WORKTREE_FILE), (main / WORKTREE_FILE).read_text(encoding="utf-8"), merge.dumps(new_wt)))
    declared = fingerprint.branch_names(new_wt)
    return files, rows, wt_written, declared


def _gitignore_note(main) -> list:
    ignored = proc.git_out(main, "check-ignore", str(DECL_FILE), str(WORKTREE_FILE))
    if not ignored:
        return []
    return [{"kind": "note", "key": "gitignore", "text": f".gitignore の対象: {ignored.replace(chr(10), '・')}（.gitignore は変えない）"}]


def cmd_write(a):
    root = proc.git_root(a.root)
    model = _load_model()
    main = _main_dir(root)
    existing = _load_decl(main, model)
    wt = _read_decl_json(main / WORKTREE_FILE, str(WORKTREE_FILE))
    try:
        meas = jsonio.read(a.measure, want=dict)
        answers = jsonio.read(a.answers, want=dict) if a.answers else {}
    except jsonio.JsonReadError as e:
        raise StepError(f"測定の結果か答えを読めない: {e}", 1) from None
    check = _checker(model)
    questions = {k for k, m in (meas.get("items") or {}).items() if m.get("status") == "question"}
    rows = [{"kind": "ignored", "key": k} for k in answers if k not in questions]
    written_before = ((existing or {}).get("analysis") or {}).get("written") or {}

    files, wt_rows, wt_written, declared = _merge_worktree_part(main, wt, meas, answers, check, written_before)
    rows += wt_rows
    analysis = {
        "analyzer": ANALYZER,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "inputs": meas.get("inputs") or {},
        "branches": fingerprint.branch_state(root, declared),
    }
    doc, item_rows = merge.merge_project(existing, meas, answers, check, analysis, model.SCHEMA_URL)
    doc["analysis"]["written"].update(wt_written)
    rows = item_rows + rows
    try:
        model.validate_decl(doc)
    except model.schema.ShapeError as e:
        raise StepError(f"書き出す宣言が形に合わない（解析器の誤り）: {e}", 1) from None
    if not _same_but_time(existing, doc):
        old = (main / DECL_FILE).read_text(encoding="utf-8") if existing is not None else None
        files.insert(0, (main / DECL_FILE, str(DECL_FILE), old, merge.dumps(doc)))
    rows += [{"kind": "note", "key": "notes", "text": n} for n in meas.get("notes") or []]
    rows += _gitignore_note(main)
    if not a.dry_run:
        for path, _, _, text in files:
            _put_text(path, text)
    _report_write(files, rows, a.dry_run)


def _report_write(files, rows, dry_run):
    rows = mask(rows)  # 表に無い形の秘密も、出力の直前でもう一度伏せる
    verb = "書く（--dry-run）" if dry_run else "書いた"
    for path, name, old, new in files:
        print(f"{verb}: {path}")
        print(merge.unified(name, old, new))
    if not files:
        print("書くものが無い（宣言は解析と同じ）")
    print("項目ごとの出所:")
    for row in rows:
        if row["kind"] in ("note", "ignored"):
            continue
        print(_row_line(row))
    for row in rows:
        if row["kind"] == "note":
            print(f"案内: {row['text']}")
        elif row["kind"] == "ignored":
            print(f"無視した答え: {row['key']}（問いでない）")
    if files and not dry_run:
        print("差分を見て、要らなければコミットしない（承認は求めない）")
    unknown = sorted({P_NUMBER.get(r["key"].split("#")[0], "P6") for r in rows if r["kind"] in ("unknown", "secret")})
    names = "・".join(n for _, n, _, _ in files)
    summary = (
        f"{'作成した' if any(o is None for _, _, o, _ in files) else '更新した'}（{names}。不明 {'・'.join(unknown) or '無し'}）"
        if files
        else "書くものが無い"
    )
    kinds: dict[str, int] = {}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    metrics = {"files": [str(p) for p, _, _, _ in files], "unknown_items": unknown, "dry_run": dry_run, "kinds": kinds}
    emit(result(TOOL, "ok", summary, rows, metrics))


# --- schema -----------------------------------------------------------------


def cmd_schema(a):
    model = _load_model()
    text = model.json_schema()
    if a.out:
        _put_text(Path(a.out), text)
        emit(result(TOOL, "ok", f"schema を書いた: {a.out}"))
    sys.stdout.write(text)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="project-decl.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="宣言が無いか古いかを判定する")
    c.add_argument("--root")
    c.add_argument("--force", action="store_true", help="手動の再解析。新しくても 2 を返す")
    c.set_defaults(func=cmd_check)
    m = sub.add_parser("measure", help="リポジトリと CI を測る")
    m.add_argument("--root")
    m.add_argument("--out", required=True, help="測定の結果の置き場（一時ディレクトリの中）")
    m.add_argument("--budget", type=float, default=120.0, help="締め切りの秒（既定 120）")
    m.set_defaults(func=cmd_measure)
    w = sub.add_parser("write", help="測定の結果と答えから宣言を書き出す")
    w.add_argument("--root")
    w.add_argument("--measure", required=True)
    w.add_argument("--answers")
    w.add_argument("--dry-run", action="store_true")
    w.set_defaults(func=cmd_write)
    s = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    s.add_argument("--out")
    s.set_defaults(func=cmd_schema)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
