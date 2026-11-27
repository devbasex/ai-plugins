#!/usr/bin/env python3
"""MVV 判定の比較（実験版。#2912 の決定 4〜6）。

過去の設計 PR の材料を 1 回だけ組んで保存し、指定した指示文で判定し直して数える。導入した版のゲートを、GitHub への書き込みを
捕まえて打つ。PR のコメントと判定の記録は書かない（ゲートのモードの記録は一時ディレクトリへ隔離する）。単体テストは持たず、手元で
打って確かめる（決定 5）。

    uv run --frozen --project . python experimental/ndf/mvv_replay.py material --prs 2214 2295 --store S.json --out M.json
    uv run --frozen --project . python experimental/ndf/mvv_replay.py material --case experimental/ndf/mvv_replay_cases/with-handling --out C.json
    uv run --frozen --project . python experimental/ndf/mvv_replay.py judge --materials M.json --model <ID> [--prompt-commit c93e690ed] [--expect 2213=C2,C4] [--expect-claude-version <版>] [--expect-prompt-sha256 <SHA-256>] --result J.json
    uv run --frozen --project . python experimental/ndf/mvv_replay.py bodies --materials M.json --judged J_new.json J_old.json [--reviewed R.jsonl] --store S.json --out B.json
    uv run --frozen --project . python experimental/ndf/mvv_replay.py tally --materials M.json --judged J_new.json J_old.json [--judged ...] --reviewed R.jsonl --out T.json
    uv run --frozen --project . python experimental/ndf/mvv_replay.py guard-check --result G.json
    NDF_STAGE_INPUT='{...}' uv run --frozen --project . python experimental/ndf/mvv_replay.py gate --answer stand-in --result X.json [--plugin-root <置き場>/plugins/ndf]

`--plugin-root` を渡すと、その下の `src` から `ndf` を読み込む（導入した版の指示文・材料の組み立て・判定の部品）。省けばこのリポジトリの
`plugins/ndf/src` を読む。本文の確認の結果（`--reviewed`）は追記だけで書く JSON Lines で、1 行が
`{"at": <時刻>, "pr": <番号>, "base_sha": ..., "head_sha": ..., "paths": [...], "pr_body_sha256": ..., "flagged": <印の有無>, "verdict": "含まない|含む|判断できない", "numbers": [...], "basis": ...}` である。
`pr`・`base_sha`・`head_sha`・`paths`・`pr_body_sha256`・`flagged` は、`bodies` の出力の件の同じ名前の欄をそのまま写す。
`tally` と `bodies` は、ベースと head の SHA・PR 本文の SHA-256 が材料と一致し、`paths` が材料の変えたファイルをすべて含む記録だけを採る。

`judge` は、`--result` に同じモデル・Claude Code の版・指示文のコミット・指示文のハッシュの結果ファイルがあれば、同じ測定の続きとして扱う。今の材料で
有効な回答を得た行は打ち直さずに残し、欠けた行と失敗した行だけを打つ。同じ材料で `JUDGE_TRIES` 回まで打っても回答が無い行は
打ち直さない（`tally` が保留の理由に挙げる）。
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

REPO = Path(__file__).resolve().parents[2]
PREV_COMMIT = "c93e690ed"
MATERIAL_MODULE = "plugins/ndf/src/ndf/domains/records/mvv_material_decide.py"
REQUIREMENTS = "issues/issue-{number}-requirements.md"
BLOB = "{ ... on Blob { oid text isTruncated } }"
TREE = "{ ... on Tree { entries { name } } }"
FETCHED, ABSENT, FAILED = "取得済み", "本文が無い", "未取得"
EXCLUDES, INCLUDES, UNKNOWN = "含まない", "含む", "判断できない"
STAND_IN = {"verdict": "follow", "reasons": ["判定の代役（実物の LLM を呼ばない）"], "boundary": [], "basis": ["Value 1"]}


class Material(NamedTuple):
    """1 件の PR の材料（決定 5）。`tabled` は表の番号（空のタプルは番号なし、None は未確認）。"""

    base_sha: str
    head_sha: str
    text: str
    sha256: str
    tabled: Optional[tuple[str, ...]]
    incomplete: tuple[str, ...]
    changed: tuple[str, ...]
    pr_body: Optional[str]
    pr_body_sha256: Optional[str]


class ReviewTarget(NamedTuple):
    number: int
    head_sha: str
    changed: tuple[str, ...]


class ReviewPaths(NamedTuple):
    pairs: tuple[tuple[str, str], ...]
    listed: bool


class Refused(Exception):
    """許可の一覧に無い GitHub への要求（送らずに止めた）。"""


class Stop(Exception):
    """実験版を止める（標準エラーへ理由を書き、終了コードを返す）。"""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _load(plugin_root: Optional[Path]) -> Path:
    """`ndf` を読み込む置き場を決めて `sys.path` の先頭へ足す。"""
    root = (plugin_root or REPO / "plugins" / "ndf").resolve()
    sys.path.insert(0, str(root / "src"))
    return root


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    """所有者だけが読み書きできる権限（0600）で、同じディレクトリの一時ファイルに書いてから名前を置き換える。

    認証して取得した PR の本文・差分・ファイルの全文を保存するため、umask に任せると同じホストのほかの利用者が読める。
    置き換えなので、書く途中でプロセスが止まっても、前に保存した中身は壊れない（`judge` は判定 1 件ごとに書く）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(json.dumps(value, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _material(raw: Mapping[str, Any]) -> Material:
    got = {f: raw.get(f) for f in Material._fields}
    for key in ("incomplete", "changed"):
        got[key] = tuple(got[key] or ())
    got["tabled"] = None if got["tabled"] is None else tuple(got["tabled"])
    return Material(**got)


def _materials(path: Path) -> dict[int, Optional[Material]]:
    """材料のファイル（PR 番号 → 材料。欠けた件は null）。"""
    return {int(k): None if v is None else _material(v) for k, v in _read_json(path)["materials"].items()}


# --- 材料 ---------------------------------------------------------------------------------------------------------


def prompt_at(repo: Path, commit: Optional[str]) -> str:
    """指示文。`commit` があればそのコミットの `SYSTEM` を読む（読めなければ終了コード 2）。無ければ読み込んだ `ndf` の `SYSTEM`。"""
    if commit is None:
        from ndf.domains.records import mvv_material_decide as mm

        return mm.SYSTEM
    done = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{MATERIAL_MODULE}"], capture_output=True, text=True)
    if done.returncode != 0:
        raise Stop(f"{commit} の指示文を読めない: {done.stderr.strip()}", 2)
    for node in ast.parse(done.stdout).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SYSTEM" for t in node.targets):
            return str(ast.literal_eval(node.value))
    raise Stop(f"{commit} の {MATERIAL_MODULE} に SYSTEM が無い", 2)


def _project_mvv(root: Path, remote: str) -> str:
    from ndf.domains.records import mvv

    snap = mvv.current(root, f"{remote}/main", allow_mismatch=True)
    return snap.path.read_text(encoding="utf-8") if snap.path is not None else ""


def _prompt_text(project: str, body: str, rows: Sequence[Mapping[str, Any]], number: Optional[int]) -> str:
    """本番のゲートと同じ材料の組み立て（差分は切らない）。"""
    from ndf.domains.records import mvv
    from ndf.domains.records import mvv_material_decide as mm

    shared = mvv.principles().decode("utf-8")
    materials = {
        "NDF の共通原則": shared,
        "プロジェクト MVV": project,
        f"Pull Request #{number} の本文" if number is not None else "Pull Request の本文": body,
        "変えたファイルと差分": mm.patch_material(rows, limit=None),
    }
    return mm.prompt(mm.redlines(shared, project), materials)


def _lacking(rows: Sequence[Mapping[str, Any]]) -> bool:
    """内容を変えたのに差分を持たないファイルがあるか（内容を変えない改名は数えない）。"""
    return any(not r.get("patch") and r.get("changes") != 0 for r in rows)


def material_of(hub: Any, g: Any, number: int, remote: str = "origin", project: str = "") -> Optional[Material]:
    """決定 5 の 4 つの順で PR の材料の全文を組む。全文を取れなければ None。`tabled` は未確認（None）のまま返す。"""
    from ndf.adapters import git as git_mod
    from ndf.domains.records import mvv
    from ndf.domains.records import mvv_material_decide as mm

    raw = hub.rest("GET", f"/repos/{hub.repo}/pulls/{number}")
    base, head = str(raw["base"]["sha"]), str(raw["head"]["sha"])
    body = (raw["body"] or "") if "body" in raw else None
    rows = mvv.compare_files(hub, g, remote, base, head)
    if _lacking(rows):
        g.fetch(remote, head)
        try:
            rows = mm.diff_rows(g.diff_between(base, head, *mvv.LOCAL_DIFF))
        except (git_mod.GitError, mm.DiffError):
            return None
        if _lacking(rows):
            return None
    text = _prompt_text(project, body or "", rows, number)
    return Material(
        base_sha=base,
        head_sha=head,
        text=text,
        sha256=_sha(text),
        tabled=None,
        incomplete=tuple(mm.incomplete_patches(rows)),
        changed=tuple(str(r["filename"]) for r in rows if r["status"] != "removed"),
        pr_body=body,
        pr_body_sha256=None if body is None else _sha(body),
    )


def _case_material(case: Path, project: str) -> Material:
    """作り物の材料（要求仕様と設計書を足しただけの PR として組む）。SHA は作り物の名前。"""
    rows = []
    for name, path in (("requirements.md", "issues/issue-1-requirements.md"), ("design.md", "issues/issue-1-design.md")):
        src = case / name if (case / name).exists() else case.parent / name
        lines = src.read_text(encoding="utf-8").splitlines()
        rows.append({"filename": path, "status": "added", "changes": len(lines), "patch": "\n".join(f"+{x}" for x in lines)})
    text = _prompt_text(project, "", rows, None)
    return Material(f"case:{case.name}", f"case:{case.name}", text, _sha(text), (), (), tuple(r["filename"] for r in rows), "", _sha(""))


def _store(path: Path) -> dict[str, Any]:
    return _read_json(path) if path.exists() else {}


def read_pairs(hub: Any, pairs: Sequence[tuple[str, str]], store: Path) -> dict[tuple[str, str], Optional[str]]:
    """（head の SHA, パス）の組の本文を、件数の上限ずつに分けて読む。結果と読みに行った回数を `store` へ書く（未取得は 2 回まで）。"""
    from ndf.adapters.github import GitHubError
    from ndf.contracts import limits

    saved = _store(store)
    wanted = [p for p in dict.fromkeys(pairs) if _due(saved.get(f"{p[0]}:{p[1]}"))]
    size = int(limits.value("github.graphql-batch", "count"))
    for start in range(0, len(wanted), size):
        chunk = wanted[start : start + size]
        keys = [f"{sha}:{path}" for sha, path in chunk]
        try:
            got = hub.aliased({f"p{i}": f"object(expression: {json.dumps(k)}) {BLOB}" for i, k in enumerate(keys)})
        except GitHubError:
            got = None
        for i, key in enumerate(keys):
            tries = int(saved.get(key, {}).get("tries", 0)) + 1
            if got is None:
                saved[key] = {"state": FAILED, "tries": tries}
                continue
            node = got.get(f"p{i}")
            text = node.get("text") if isinstance(node, dict) and node.get("isTruncated") is False else None
            saved[key] = {"state": FETCHED, "tries": tries, "text": text} if isinstance(text, str) else {"state": ABSENT, "tries": tries}
        _write_json(store, saved)
    return {p: saved.get(f"{p[0]}:{p[1]}", {}).get("text") for p in pairs}


def _due(entry: Optional[Mapping[str, Any]]) -> bool:
    """読みに行く組か（未読か、1 回だけ読んで未取得）。"""
    return entry is None or (entry["state"] == FAILED and int(entry["tries"]) < 2)


def _tabled(material: Material, texts: Mapping[tuple[str, str], Optional[str]]) -> Optional[tuple[str, ...]]:
    from ndf.core import issue_docs
    from ndf.domains.records import design_check_rules

    numbers: dict[str, None] = {}
    for path in (p for p in material.changed if issue_docs.DESIGN_FILE.match(p)):
        text = texts.get((material.head_sha, path))
        if text is None:
            return None
        numbers.update({n: None for r in design_check_rules.redline_rows(text) for n in r.numbers})
    return tuple(numbers)


def _hub(root: Path) -> Any:
    from ndf.adapters import github
    from ndf.adapters.git import Git

    return github.GitHub(Git(root).remote_repo(None), github.client())


def _cmd_material(args: argparse.Namespace) -> int:
    from ndf.adapters.git import Git
    from ndf.core import issue_docs

    g = Git(REPO)
    remote = g.remote()
    project = _project_mvv(REPO, remote)
    if args.case is not None:
        _write_json(args.out, {"materials": {"0": _case_material(args.case, project)._asdict()}, "made_at": _now()})
        return 0
    hub = _hub(REPO)
    made = {n: material_of(hub, g, n, remote, project) for n in args.prs}
    pairs = [(m.head_sha, p) for m in made.values() if m is not None for p in m.changed if issue_docs.DESIGN_FILE.match(p)]
    texts = read_pairs(hub, pairs, args.store)
    out = {str(n): None if m is None else m._replace(tabled=_tabled(m, texts))._asdict() for n, m in made.items()}
    _write_json(args.out, {"materials": out, "made_at": _now(), "remote": remote})
    return 0


# --- 判定 ---------------------------------------------------------------------------------------------------------


def _claude_version() -> str:
    done = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def _source_commit(root: Path) -> str:
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else f"plugin-root:{root}"


def judge_once(question: str, material: Material, model: str) -> dict[str, Any]:
    """`llm.ask` を、`--model` を足した `call` で 1 回呼ぶ。回答の形が違えば読めない回答として返す。"""
    from ndf import contracts
    from ndf.adapters import claude, llm
    from ndf.contracts import limits

    schema = contracts.load_json(contracts.schema_path("mvv-verdict"))

    def call(argv: Sequence[str], text: str, timeout: float) -> Any:
        return claude.call_claude([*argv, "--model", model], text, timeout)

    got = llm.ask(question, material.text, schema, limits.seconds("supervise.judge-step"), call=call)
    return {"ok": got.ok, "answer": got.answer, "cause": got.cause, "cost": got.cost}


def _skips(row: Optional[Mapping[str, Any]]) -> bool:
    """判定が follow で boundary が空か（省ける回答）。"""
    answer = (row or {}).get("answer") or {}
    return bool((row or {}).get("ok")) and answer.get("verdict") == "follow" and not answer.get("boundary")


def _expect_misses(row: Mapping[str, Any], wanted: Sequence[str]) -> list[str]:
    """期待した番号のうち、回答の boundary に挙がらなかったもの（挙がった操作が合っているかは人が読む）。"""
    boundary = " ".join(str(b) for b in ((row.get("answer") or {}).get("boundary") or []))
    return [n for n in wanted if n not in boundary]


def _cmd_judge(args: argparse.Namespace, root: Path) -> int:
    question = prompt_at(REPO, args.prompt_commit)
    commit = args.prompt_commit or _source_commit(root)
    # 指示文のコミットだけでは、作業ツリーで書き換えた指示文や Git の外の置き場の指示文を見分けられない。送る指示文そのものの
    # ハッシュも判定の条件に入れ、違えば前の結果を引き継がない
    info = {
        "model": args.model,
        "claude_version": _claude_version(),
        "prompt_commit": commit,
        "prompt_sha256": _sha(question),
        "plugin_root": str(root),
    }
    # 再判定では、残す結果と同じ条件で打たないと tally が拒むので、LLM を呼ぶ前に照合して止める（費用を払ってから拒まれない）
    for key, want in (("claude_version", args.expect_claude_version), ("prompt_sha256", args.expect_prompt_sha256)):
        if want is not None and info[key] != want:
            raise Stop(f"{key} が期待した値と違う（期待: {want} / 今: {info[key]}）。判定せずに止める", 2)
    # 材料は最初の保存の前に読む（読めずに止まるとき、前の結果ファイルを書き換えない）
    materials = _materials(args.materials)
    # 結果は名前の置き換えで保存するので、最後の要素がシンボリックリンクだと初回の保存でリンクが消え、次の起動が別の宛先を読む
    if args.result.is_symlink():
        raise Stop(f"--result（{args.result}）はシンボリックリンク。リンクの先のパスを渡す")
    # 同じ結果ファイルを補う起動を並べると、起動時に読んだ状態で互いの行を上書きするので、結果ファイルのディレクトリを排他で
    # ロックし、そのディレクトリに保存する judge を 1 つだけ動かす。ディレクトリそのものをロックするので、ロックのためのファイルを
    # 作らず（片付けるものが残らない）、状態の保存先の環境変数が違う起動どうしでも同じロックを取る
    folder = args.result.absolute().parent
    folder.mkdir(parents=True, exist_ok=True)
    lock = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop(
                f"同じディレクトリ（{folder}）に結果を保存する judge が動いている。終わってから打ち直すか、別のディレクトリの --result を渡す"
            ) from None
        return _judge_locked(args, question, info, materials)
    finally:
        os.close(lock)


def _judge_locked(args: argparse.Namespace, question: str, info: Mapping[str, Any], materials: Mapping[int, Optional[Material]]) -> int:
    prev = _read_json(args.result) if args.result.exists() else None
    # 判定の条件が違う結果ファイルは別の測定なので、保存済みの判定を消さないよう書かずに止める
    if prev is not None and not all(prev.get(k) == info[k] for k in JUDGE_CONDITIONS):
        diff = [k for k in JUDGE_CONDITIONS if prev.get(k) != info[k]]
        raise Stop(f"--result（{args.result}）は判定の条件 {diff} が違う測定の結果なので書かない。新しい --result を渡す")
    # 同じ判定の条件の結果ファイルなら、同じ測定の続きとして実行の識別子と行を引き継ぎ、欠けた行だけを補う。識別子は判定の前に
    # 決めて保存し、途中で止まっても、同じ --result で打ち直せば同じ測定として続く
    results: dict[str, Any] = dict(prev["results"]) if prev is not None else {}
    state = {"run_id": str(prev["run_id"]) if prev is not None else str(uuid.uuid4()), **info, "results": results}

    def save() -> None:
        expect = {str(n): _expect_misses(results.get(str(n), {}), nums) for n, nums in (args.expect or {}).items()}
        state.update(at=_now(), expect_missing=expect)
        _write_json(args.result, state)

    save()
    for number, material in materials.items():
        if args.prs and number not in args.prs:
            continue
        if material is None:
            results[str(number)] = {"ok": False, "cause": "材料が欠けた", "answer": None}
            save()
            continue
        old = results.get(str(number))
        if _reusable(old, material) or _exhausted(old, material):
            continue
        tries = int((old or {}).get("tries", 1)) + 1 if _same_material(old, material) else 1
        mark = {**info, "base_sha": material.base_sha, "head_sha": material.head_sha, "material_sha256": material.sha256}
        # 判定を呼ぶ前に試行の回数を保存する（判定の途中で止まっても、その回を数えて上限を超えて打たない）
        results[str(number)] = {"ok": False, "cause": "判定の途中で止まった", "answer": None, **mark, "tries": tries}
        save()
        results[str(number)] = {**judge_once(question, material, args.model), **mark, "tries": tries}
        save()
    return 1 if any(state["expect_missing"].values()) else 0


# 前の結果ファイルを同じ測定の続きとみなす判定の条件
JUDGE_CONDITIONS = ("model", "claude_version", "prompt_commit", "prompt_sha256")
# 1 件の PR の判定を打つ回数の上限（同じ材料で失敗が続いたら打ち直さず、その PR は `tally` が保留の理由に挙げる）
JUDGE_TRIES = 2


def _same_material(row: Optional[Mapping[str, Any]], material: Material) -> bool:
    """前の行が、今の材料（ハッシュ・ベース・head）で打った判定か。"""
    if row is None:
        return False
    return (row.get("material_sha256"), row.get("base_sha"), row.get("head_sha")) == (
        material.sha256,
        material.base_sha,
        material.head_sha,
    )


def _reusable(row: Optional[Mapping[str, Any]], material: Material) -> bool:
    """今の材料で打って有効な回答を得た行か（打ち直さずに残す）。"""
    return _same_material(row, material) and bool((row or {}).get("ok")) and isinstance((row or {}).get("answer"), dict)


def _exhausted(row: Optional[Mapping[str, Any]], material: Material) -> bool:
    """今の材料で上限の回数まで打って、まだ有効な回答が無い行か。"""
    return _same_material(row, material) and int((row or {}).get("tries", 1)) >= JUDGE_TRIES


def _expect(value: str) -> tuple[int, list[str]]:
    number, _, nums = value.partition("=")
    return int(number), [n for n in nums.split(",") if n]


# --- 本文の確認と集計 ---------------------------------------------------------------------------------------------


def _checked_judged(paths: Sequence[Path], materials: Mapping[int, Optional[Material]]) -> list[dict[str, Any]]:
    """判定結果を読み、PR ごとの材料のハッシュ・ベース・head が今回の材料と合うかを確かめる（合わなければ止める）。"""
    loaded = []
    for path in paths:
        judged = _read_json(path)
        for key, row in judged["results"].items():
            m = materials.get(int(key))
            if m is None or "material_sha256" not in row:
                continue
            if (row["material_sha256"], row["base_sha"], row["head_sha"]) != (m.sha256, m.base_sha, m.head_sha):
                raise Stop(f"判定結果 {path} の PR #{key} が今回の材料と合わない")
        loaded.append(judged)
    return loaded


def latest_review(records: Sequence[Mapping[str, Any]], material: Material, number: int) -> Optional[Mapping[str, Any]]:
    """PR 番号・ベースと head の SHA・PR 本文の SHA-256 が材料と一致し、確認したパス（`paths`）が材料の変えたファイルを
    すべて含む記録のうち、時刻が最も新しいもの。ベースが動いて確かめる対象のファイルが増えたのに、前の確認を採らないため。"""
    found = [
        r
        for r in records
        if int(r["pr"]) == number
        and r.get("base_sha") == material.base_sha
        and r.get("head_sha") == material.head_sha
        and r.get("pr_body_sha256") == material.pr_body_sha256
        and isinstance(r.get("paths"), list)
        and set(material.changed) <= set(r["paths"])
    ]
    return max(found, key=lambda r: str(r["at"])) if found else None


def _reviews(path: Optional[Path]) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def review_paths(hub: Any, prs: Sequence[ReviewTarget]) -> dict[int, ReviewPaths]:
    """本文の確認で読む組（PR が足したか変えたファイルと、同じ課題の要求仕様・設計書・詳細設計書）と、`issues/` の一覧を読めたか。"""
    from ndf.adapters.github import GitHubError
    from ndf.core import issue_docs

    try:
        got: Optional[dict[str, Any]] = hub.aliased(
            {str(t.number): f"object(expression: {json.dumps(t.head_sha + ':issues')}) {TREE}" for t in prs}
        )
    except GitHubError:
        got = None
    out: dict[int, ReviewPaths] = {}
    for t in prs:
        own = [(t.head_sha, p) for p in t.changed]
        node = (got or {}).get(str(t.number))
        if not isinstance(node, dict) or not isinstance(node.get("entries"), list):
            out[t.number] = ReviewPaths(tuple(own), False)
            continue
        issues = {n for p in t.changed for n in issue_docs.design_issues(p)}
        listed = [f"issues/{e['name']}" for e in node["entries"]]
        same = [p for p in listed if p in {REQUIREMENTS.format(number=n) for n in issues} or issue_docs.design_issues(p) & issues]
        out[t.number] = ReviewPaths(tuple(dict.fromkeys([*own, *((t.head_sha, p) for p in same)])), True)
    return out


def _cmd_bodies(args: argparse.Namespace) -> int:
    materials = _materials(args.materials)
    judged = _checked_judged(args.judged, materials)
    reviewed = _reviews(args.reviewed)
    chosen: list[ReviewTarget] = []
    for number, m in materials.items():
        if m is None or m.tabled != ():
            continue
        if not any(_skips(j["results"].get(str(number))) for j in judged):
            continue
        last = latest_review(reviewed, m, number)
        if last is not None and (last["verdict"] == INCLUDES or (last["verdict"] == EXCLUDES and not last.get("flagged"))):
            continue
        chosen.append(ReviewTarget(number, m.head_sha, m.changed))
    hub = _hub(REPO)
    paths = review_paths(hub, chosen)
    texts = read_pairs(hub, [p for rp in paths.values() for p in rp.pairs], args.store)
    store = _store(args.store)
    out = []
    for t in chosen:
        m = materials[t.number]
        assert m is not None
        rp = paths[t.number]
        states = {f"{s}:{p}": store.get(f"{s}:{p}", {}).get("state", FAILED) for s, p in rp.pairs}
        reasons = [*(["issues/ の一覧を読めない"] if not rp.listed else []), *(["PR 本文を取得できなかった"] if m.pr_body is None else [])]
        reasons += [f"{k}: {v}" for k, v in states.items() if v != FETCHED]
        out.append(
            {
                "pr": t.number,
                "base_sha": m.base_sha,
                "head_sha": t.head_sha,
                # 確認の記録に写すパスの集合（`latest_review` は、材料の変えたファイルをすべて含む記録だけを採る）
                "paths": [p for _, p in rp.pairs],
                "pr_body": m.pr_body,
                "pr_body_sha256": m.pr_body_sha256,
                "listed": rp.listed,
                "flagged": bool(reasons),
                "flag_reasons": reasons,
                "files": [{"path": p, "state": states[f"{s}:{p}"], "text": texts.get((s, p))} for s, p in rp.pairs],
            }
        )
    _write_json(args.out, {"made_at": _now(), "bodies": out})
    return 0


def _side(
    materials: Mapping[int, Optional[Material]], judged: Mapping[str, Any], reviewed: Sequence[Mapping[str, Any]], new: bool
) -> dict[str, Any]:
    """1 つの側の 3 通りの値（判定上・確認後・経路全体）と、確認後の扱いが未確認の PR と「含む」の PR。"""
    judged_n = confirmed = route = table_stopped = 0
    unconfirmed: list[int] = []
    includes: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for number, m in materials.items():
        skip = _skips(judged["results"].get(str(number)))
        judged_n += skip
        if skip and m is not None and not m.incomplete:
            if not new or m.tabled == ():
                route += 1
            elif new:
                table_stopped += 1
        if not skip or m is None:
            continue
        if m.tabled is None:
            unconfirmed.append(number)
            rows.append({"pr": number, "after": "未確認", "why": "表を確かめられない"})
            continue
        if m.tabled:
            rows.append({"pr": number, "after": "省けない", "why": "表で止まる"})
            continue
        last = latest_review(reviewed, m, number)
        verdict = None if last is None else last["verdict"]
        if verdict == INCLUDES:
            includes.append({"pr": number, "numbers": list((last or {}).get("numbers") or [])})
            rows.append({"pr": number, "after": "省けない", "why": "本文に承認必須の操作を含む"})
        elif verdict == EXCLUDES and not (last or {}).get("flagged"):
            confirmed += 1
            rows.append({"pr": number, "after": "省ける", "why": "本文の確認で含まない"})
        else:
            unconfirmed.append(number)
            why = "材料と一致する本文の確認の記録が無い" if last is None else f"本文の確認が {verdict}（印 {bool(last.get('flagged'))}）"
            rows.append({"pr": number, "after": "未確認", "why": why})
    total = len(materials)
    return {
        "judged": judged_n,
        "confirmed": confirmed,
        "route": route,
        "table_stopped": table_stopped if new else None,
        "rates": {k: round(v / total, 3) for k, v in (("judged", judged_n), ("confirmed", confirmed), ("route", route))},
        "unconfirmed": unconfirmed,
        "includes": includes,
        "rows": rows,
    }


def _round(
    materials: Mapping[int, Optional[Material]], pair: Sequence[Mapping[str, Any]], reviewed: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    new, old = _side(materials, pair[0], reviewed, True), _side(materials, pair[1], reviewed, False)
    gaps = _gaps(materials, pair)
    lower = new["confirmed"] - (old["confirmed"] + len(old["unconfirmed"]))
    upper = new["confirmed"] + len(new["unconfirmed"]) - old["confirmed"]
    record = [{"pr": i["pr"], "numbers": i["numbers"], "side": "変更後"} for i in new["includes"]]
    for i in old["includes"]:
        same = next((r for r in record if r["pr"] == i["pr"]), None)
        if same is not None:
            same["side"] = "両方"
        else:
            record.append({"pr": i["pr"], "numbers": i["numbers"], "side": PREV_COMMIT})
    return {
        "new": new,
        "old": old,
        "range": [lower, upper],
        "missed": bool(new["includes"]),
        "possible_miss": bool(new["unconfirmed"]),
        "gaps": gaps,
        "record_to_3017": record,
    }


def _gaps(materials: Mapping[int, Optional[Material]], pair: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """材料か、どちらかの側の有効な判定結果（`ok` が真で回答を持つ）が欠けた PR。省けない回答と区別して保留にするため。"""
    out = []
    for number, m in materials.items():
        rows = [side["results"].get(str(number)) for side in pair]
        why = ["材料が欠けた"] if m is None else []
        why += [f"{name}の判定結果が無い" for name, row in zip(("変更後", PREV_COMMIT), rows) if row is None]
        why += [
            f"{name}の判定に失敗した"
            for name, row in zip(("変更後", PREV_COMMIT), rows)
            if row is not None and not (row.get("ok") and isinstance(row.get("answer"), dict))
        ]
        if why:
            out.append({"pr": number, "why": why})
    return out


def _verdict(rounds: Sequence[Mapping[str, Any]]) -> tuple[str, Optional[str]]:
    """回をまたぐ合否（詳細設計書の「回をまたぐ合否」の 5 つの順）。"""
    if any(r["missed"] for r in rounds):
        return "不合格", None
    if any(r["gaps"] for r in rounds):
        return "保留", "判定結果か材料が欠けた PR がある"
    if any(r["possible_miss"] for r in rounds):
        return "保留", "未確認が残る"
    lower, upper = rounds[0]["range"]
    if lower >= 3:
        return "合格", None
    if upper <= 0:
        return "不合格", None
    if not (1 <= lower <= 2 and 1 <= upper <= 2):
        return "保留", "未確認が残る"
    if len(rounds) < 3:
        return "保留", "再測定が要る"
    if all(r["range"][0] >= 1 for r in rounds[1:3]):
        return "合格", None
    return "保留", "再測定でも差が残らない"


def _is_sha256(value: Any) -> bool:
    """16 進 64 桁の文字列か（判定結果の `prompt_sha256` として有効か）。"""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _redo_hint(args: argparse.Namespace, pairs: Sequence[Sequence[Mapping[str, Any]]], i: int, side: int, name: str) -> str:
    """指示文のハッシュが無い 1 つの結果ファイルを判定し直す案内。

    残す結果と同じ Claude Code の版と、同じ側のハッシュを持つ回の指示文のハッシュ（あれば）を照合の引数に入れ、judge が LLM を呼ぶ前に
    不一致で止まるようにする。指示文の取得元がコミットで決まるのは比較の基準の側だけである。変更後の側は `--prompt-commit` を
    省いた起動（読み込んだ ndf の指示文）と見分けられないので、コマンドを示さず、元の起動方法を確かめる案内にする。
    """
    # 期待する条件は残す結果（同じ側のハッシュを持つ回。呼び出し元が条件の一致を確かめ済み）から取る。無ければこの結果の条件
    kept = next((p[side] for p in pairs if _is_sha256(p[side].get("prompt_sha256"))), None)
    row = kept or pairs[i - 1][side]
    check = ["--expect-claude-version", str(row.get("claude_version"))]
    if kept is not None:
        check += ["--expect-prompt-sha256", str(kept["prompt_sha256"])]
    where = f"{i}回目の{name}: {args.judged[i - 1][side]}"
    if side == 0:
        return (
            f"{where}（指示文の取得元を結果ファイルから特定できない。元の judge の起動方法（--prompt-commit・--plugin-root）を確かめ、"
            f"同じ起動に次の引数を足して新しい --result へ判定する: {shlex.join(['--model', str(row.get('model')), *check])}）"
        )
    head = ["uv", "run", "--frozen", "--project", str(REPO), "python", str(REPO / "experimental" / "ndf" / "mvv_replay.py")]
    if row.get("plugin_root"):
        head += ["--plugin-root", str(row["plugin_root"])]
    argv = [*head, "judge", "--materials", str(args.materials), "--model", str(row.get("model"))]
    argv += ["--prompt-commit", str(row.get("prompt_commit")), *check]
    return f"{where}（<新しいパス> を新しい結果ファイルのパスに置き換えて打つ: {shlex.join(argv)} --result <新しいパス>）"


def _cmd_tally(args: argparse.Namespace) -> int:
    materials = {n: m for n, m in _materials(args.materials).items()}
    reviewed = _reviews(args.reviewed)
    pairs = [_checked_judged(p, materials) for p in args.judged]
    ids = [j["run_id"] for pair in pairs for j in pair]
    if len(ids) != len(set(ids)):
        raise Stop(f"同じ判定結果（実行の識別子）が 2 回以上ある: {sorted({i for i in ids if ids.count(i) > 1})}")
    for new, old in pairs:
        if not str(old["prompt_commit"]).startswith(PREV_COMMIT) or str(new["prompt_commit"]).startswith(PREV_COMMIT):
            raise Stop(f"組の指示文が違う（1 つ目は変更後、2 つ目は {PREV_COMMIT}）: {new['prompt_commit']} / {old['prompt_commit']}")
        if (new["model"], new["claude_version"]) != (old["model"], old["claude_version"]):
            raise Stop("比べる 2 つの判定でモデル ID か Claude Code のバージョンが違う")
    # 回ごとに指示文や実行環境を変えると、公開する指示文で再現を確かめていない改善が合格になるので、側ごとに全回の条件を揃える
    # 照合は指示文のハッシュを持つ結果どうしで先に行う。残す結果どうしの条件が両立しなければ、欠けた結果を判定し直しても集計へ
    # 進めないので、判定し直しを案内する前に止める（費用を払ってから止まらない）
    for side, name in ((0, "変更後"), (1, PREV_COMMIT)):
        conditions = {tuple(pair[side].get(k) for k in JUDGE_CONDITIONS) for pair in pairs if _is_sha256(pair[side].get("prompt_sha256"))}
        if len(conditions) > 1:
            raise Stop(f"{name}の判定の条件（{', '.join(JUDGE_CONDITIONS)}）が回によって違う: {sorted(map(str, conditions))}")
    # 指示文のハッシュを持たない結果（この変更より前の形式）は、実際に送った指示文を照合できないので比べない。前の結果ファイルは
    # judge が条件の違いで続きに使わないので、欠けた側と回のファイルだけを挙げ、新しい保存先で打ち直す打ち方を示す
    redo = [
        _redo_hint(args, pairs, i, side, name)
        for i, pair in enumerate(pairs, 1)
        for side, name in ((0, "変更後"), (1, PREV_COMMIT))
        if not _is_sha256(pair[side].get("prompt_sha256"))
    ]
    if redo:
        raise Stop(
            "送った指示文の SHA-256 が無い判定結果がある。次の側と回だけを判定し直す。以前の結果ファイルは残す。"
            "次の tally では、該当する --judged の引数に新しい結果ファイルのパスを渡す:\n" + "\n".join(redo)
        )
    rounds = [_round(materials, pair, reviewed) for pair in pairs]
    verdict, reason = _verdict(rounds)
    selected = {n: latest_review(reviewed, m, n) for n, m in materials.items() if m is not None}
    skipped = sum(1 for r in reviewed if r is not selected.get(int(r["pr"])))
    _write_json(args.out, {"at": _now(), "verdict": verdict, "hold_reason": reason, "rounds": rounds, "unused_review_records": skipped})
    print(json.dumps({"verdict": verdict, "hold_reason": reason, "ranges": [r["range"] for r in rounds]}, ensure_ascii=False))
    return 0


# --- 導入した版のゲート -------------------------------------------------------------------------------------------


@dataclasses.dataclass
class _Reply:
    """githubkit の応答の代わり（`content`・`headers`・`json`・`text` だけを持つ）。"""

    body: Any

    @property
    def content(self) -> bytes:
        return json.dumps(self.body).encode("utf-8")

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": "application/json"}

    @property
    def text(self) -> str:
        return json.dumps(self.body)

    def json(self) -> Any:
        return self.body


def _operation(query: str) -> Optional[str]:
    """GraphQL の文の操作の種類。読み取りなら `query`、書き込みを含めば None（拒む）。"""
    lines = [line.split("#", 1)[0] for line in query.splitlines()]
    text = " ".join(" ".join(lines).split())
    if "mutation" in text or "subscription" in text:
        return None
    return "query" if text.startswith(("query", "{")) else None


class _GuardedGraphql:
    def __init__(self, owner: GuardedApi) -> None:
        self.owner = owner

    def _request(self, query: str, variables: Any = None) -> Any:
        if _operation(query) is None:
            self.owner.refused.append({"method": "graphql", "operation": "mutation_or_unreadable"})
            raise Refused("GraphQL の書き込みか読めない文")
        return self.owner.inner.graphql._request(query, variables)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.owner.inner.graphql, name)


class GuardedApi:
    """`GitHub.api` に差し替える送る口。読み取りだけを送り、確認用 PR へのコメントだけを捕まえ、ほかは送らずに拒む。"""

    def __init__(self, inner: Any, repo: str, pr: int) -> None:
        self.inner, self.repo, self.pr = inner, repo, pr
        self.captured: list[Any] = []
        self.refused: list[dict[str, Any]] = []
        self.graphql = _GuardedGraphql(self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def request(self, method: str, path: str, **kw: Any) -> Any:
        if method.upper() == "GET":
            return self.inner.request(method, path, **kw)
        if method.upper() == "POST" and path == f"/repos/{self.repo}/issues/{self.pr}/comments":
            self.captured.append(kw.get("json"))
            return _Reply({"id": 0, "html_url": f"https://github.com/{self.repo}/pull/{self.pr}#capture"})
        self.refused.append({"method": method.upper(), "path": path})
        raise Refused(f"{method} {path}")


def guarded_hub(hub: Any, pr: int) -> tuple[Any, GuardedApi]:
    """送る口だけを差し替えた `GitHub`。"""
    api = GuardedApi(hub.api, hub.repo, pr)
    return dataclasses.replace(hub, api=api), api


class _FakeInner:
    """`guard-check` の送る口の先。届いた要求を記録し、作り物の応答を返す（GitHub へは何も送らない）。"""

    def __init__(self) -> None:
        self.reached: list[str] = []
        self.config = type("Config", (), {"throttler": None})()
        inner = self

        class _Graphql:
            def _request(self, query: str, variables: Any = None) -> Any:
                inner.reached.append("graphql:" + (_operation(query) or "?"))
                return _Reply(
                    {
                        "data": {
                            "repository": {"nameWithOwner": "o/r", "config": None, "object": None},
                            "viewer": {"login": "x", "databaseId": 1},
                        }
                    }
                )

            def parse_graphql_response(self, resp: Any) -> Any:
                return resp.json()["data"]

        self.graphql = _Graphql()

    def request(self, method: str, path: str, **kw: Any) -> Any:
        self.reached.append(f"{method} {path}")
        return _Reply({})


def guard_check(result: Path) -> int:
    """書き込みの経路を順に通し、読み取りの 2 件だけが送る口の先へ届き、コメントは捕まり、ほかは拒まれることを確かめる。"""
    from ndf.adapters import github, publish

    inner = _FakeInner()
    api = GuardedApi(inner, "o/r", 1)
    hub = github.GitHub("o/r", api, gate=object())  # type: ignore[arg-type]

    class _Hub(github.GitHub):
        def visibilities(self, repos: Any) -> dict[str, str]:  # 公開範囲の照会は確かめる経路の外（読み取り）
            return {r: "public" for r in repos}

    offline = _Hub("o/r", api, gate=object())  # type: ignore[arg-type]
    steps = {
        "Publisher.comment（確認用 PR）": lambda: publish.Publisher(offline).comment(1, "確かめ"),
        "別の番号へのコメント": lambda: publish.Publisher(offline).comment(2, "確かめ"),
        "課題の作成": lambda: publish.Publisher(offline).create_issue("題", "本文"),
        "PATCH による本文の書き換え": lambda: hub.rest("PATCH", "/repos/o/r/issues/1", {"body": "x"}),
        "ラベルの付け替え": lambda: hub.rest("POST", "/repos/o/r/issues/1/labels", {"labels": ["x"]}),
        "mutation を含む GraphQL": lambda: hub.graphql("mutation { addComment(input: {}) { clientMutationId } }", {}),
        "api.request の GET": lambda: api.request("GET", "/repos/o/r/actions/artifacts/1/zip"),
        "branch_file の query": lambda: hub.branch_file("main", ".ndf/project.json"),
    }
    outcomes = {}
    for name, step in steps.items():
        try:
            step()
            outcomes[name] = "通った"
        except Refused:
            outcomes[name] = "拒んだ"
        except Exception as exc:  # noqa: BLE001 作り物の応答で後段が読めなくても、送る口の判定は記録済み
            outcomes[name] = f"送る口の後で失敗: {type(exc).__name__}"
    ok = (
        len(api.captured) == 1
        and len(api.refused) == 5
        and sorted(inner.reached) == sorted(["GET /repos/o/r/actions/artifacts/1/zip", "graphql:query"])
    )
    _write_json(result, {"ok": ok, "outcomes": outcomes, "captured": len(api.captured), "refused": api.refused, "reached": inner.reached})
    return 0 if ok else 1


def gate_once(root: Path, answer: str, result: Path, plugin_root: Path) -> int:
    """導入した版の `mvv_gate.gate` を、書き込みを捕まえる送る口と一時ディレクトリの記録で打つ。"""
    from ndf.adapters import git as git_mod
    from ndf.adapters import llm
    from ndf.domains.records import cli, mvv_gate

    raw = os.environ.get("NDF_STAGE_INPUT")
    if raw is None:
        raise Stop("工程の入力（環境変数 NDF_STAGE_INPUT）が無い", 2)
    inputs = cli._env_inputs(mvv_gate.DESIGN, json.loads(raw))
    worktree = Path(str(inputs.get("worktree") or root))
    branch = git_mod.Git(worktree).run("branch", "--show-current").strip()
    real = _hub(worktree)
    opened = real.open_pulls_of_head(branch, 2)
    if len(opened) != 1:
        raise Stop(f"ブランチ {branch} の開いた PR が 1 件でない（{len(opened)} 件）")
    guarded, api = guarded_hub(real, opened[0].number)

    def stand_in(question: str, material: str, schema: dict, timeout: float, before_retry: Any = None) -> Any:
        return llm.JudgeAnswer(True, dict(STAND_IN), None, 0, 0.0)

    with tempfile.TemporaryDirectory() as state:
        os.environ["NDF_MVV_STATE_DIR"] = state
        try:
            out: Any = mvv_gate.gate(
                worktree, mvv_gate.DESIGN, inputs, lambda r, remote=None: guarded, stand_in if answer == "stand-in" else llm.ask
            )
        except Refused as exc:
            out = {"refused_at": str(exc)}
    version = (
        _read_json(plugin_root / ".claude-plugin" / "plugin.json").get("version")
        if (plugin_root / ".claude-plugin" / "plugin.json").exists()
        else None
    )
    record = {
        "outcome": out,
        "captured": api.captured,
        "captured_count": len(api.captured),
        "refused": api.refused,
        "plugin_root": str(plugin_root),
        "version": version,
        "answer": answer,
    }
    _write_json(result, record)
    return 0 if len(api.captured) >= 1 and not api.refused else 1


# --- 入口 ---------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mvv_replay.py")
    p.add_argument("--plugin-root", type=Path, default=None, help="ndf を読み込む置き場（導入した版の plugins/ndf に当たるディレクトリ）")
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("material")
    m.add_argument("--prs", type=int, nargs="*", default=[])
    m.add_argument("--case", type=Path, default=None, help="作り物の材料のディレクトリ（mvv_replay_cases/ の下）")
    m.add_argument("--store", type=Path, default=Path("mvv-replay-store.json"))
    m.add_argument("--out", type=Path, required=True)
    j = sub.add_parser("judge")
    j.add_argument("--materials", type=Path, required=True)
    j.add_argument("--model", required=True)
    j.add_argument("--prompt-commit", default=None)
    j.add_argument("--prs", type=int, nargs="*", default=[])
    j.add_argument("--expect", type=_expect, nargs="*", default=[], help="<PR>=<番号>,<番号> の並び（順 1）")
    j.add_argument("--result", type=Path, required=True)
    j.add_argument("--expect-claude-version", default=None, help="Claude Code の版がこれと違えば判定せずに止める")
    j.add_argument("--expect-prompt-sha256", default=None, help="送る指示文の SHA-256 がこれと違えば判定せずに止める")
    b = sub.add_parser("bodies")
    b.add_argument("--materials", type=Path, required=True)
    b.add_argument("--judged", type=Path, nargs=2, required=True)
    b.add_argument("--reviewed", type=Path, default=None)
    b.add_argument("--store", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    t = sub.add_parser("tally")
    t.add_argument("--materials", type=Path, required=True)
    t.add_argument("--judged", type=Path, nargs=2, action="append", required=True)
    t.add_argument("--reviewed", type=Path, default=None)
    t.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("gate")
    g.add_argument("--answer", choices=("llm", "stand-in"), required=True)
    g.add_argument("--result", type=Path, required=True)
    c = sub.add_parser("guard-check")
    c.add_argument("--result", type=Path, required=True)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    root = _load(args.plugin_root)
    try:
        if args.command == "material":
            return _cmd_material(args)
        if args.command == "judge":
            args.expect = dict(args.expect)
            return _cmd_judge(args, root)
        if args.command == "bodies":
            return _cmd_bodies(args)
        if args.command == "tally":
            return _cmd_tally(args)
        if args.command == "guard-check":
            return guard_check(args.result)
        return gate_once(REPO, args.answer, args.result, root)
    except Stop as exc:
        print(str(exc), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
