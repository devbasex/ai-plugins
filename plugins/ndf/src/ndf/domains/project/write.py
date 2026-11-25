"""測定の結果と回答を設定のキーへマージする書き出しの規則と、差分・`.gitignore` の確かめ（#3569。旧い `project_lib/merge.py`・
`ci_tests.py`・`lib/ndf_tracking.py` と `project-decl.py write` の組み立ての移し先）。

設定の値は辞書のまま扱い、キーの順序と知らないキーを保つ。

- 秘密の形を最初に当てる: 書く値・既存の値のどちらかが秘密の形なら、その項目を `{"unknown": "秘密の形"}` にし、値を出さない
- 置き換えてよいのは、キーが無い項目と、前の `write` の値のまま（`analysis.written` の指紋が一致する）項目だけ
- 新しい解析で不明でも、前の値があれば残す
- CI テストの設定（`test.ci.only`）は、テストの suite の `ci_jobs` のファイルがリポジトリにあるときだけ書く。人が書いた値は変えない
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf import contracts
from ndf.adapters import git
from ndf.domains.project import check
from ndf.domains.records import files
from ndf.domains.project.items import MEASURED, QUESTION, obj, seq
from ndf.domains.project.secret import has_secret, redact

# 解析が測る設定の項目（スキーマの並びの順）。外すのは人が書く項目（検査が読む toolchain・budget を含む）・解析の記録・
# 形のバージョン・非推奨のキーと、ndf project branch-strategy だけが書くブランチ戦略の設定
ITEM_KEYS = tuple(
    k
    for k, v in contracts.load_json(check.SCHEMA)["properties"].items()
    if k not in ("$schema", "version", "docs", "analysis", "workflow", "branch_strategy", "toolchain", "budget") and not v.get("deprecated")
)
SECRET_REASON = "秘密の形"
CI_KEY = "test.ci.only"  # `analysis.written` に持つ指紋のキー

Check = Callable[[str, Any], Optional[str]]


class WriteError(Exception):
    """書き出せない（測定の結果か回答が読めない・回答が表に合わない・書き出す設定が形に合わない）。"""


class File(NamedTuple):
    """書く 1 つのファイル（保存先・表示の名前・前の中身・新しい中身）。"""

    path: Path
    name: str
    old: Optional[str]
    new: str


class Plan(NamedTuple):
    files: list[File]
    rows: list[dict[str, Any]]


def value_digest(value: Any) -> str:
    """値を正規化した JSON の SHA-256（書いた値の指紋）。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_unknown(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"unknown"}


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def item_problem(key: str, value: Any) -> Optional[str]:
    """設定の 1 項目の値がスキーマに合わなければその誤り。合えば None。"""
    errs = contracts.schema_errors({"version": 1, key: value}, check.SCHEMA)
    return "; ".join(errs[:3]) if errs else None


def resolve_item(key: str, m: Optional[dict[str, Any]], answers: dict[str, Any], problem_of: Check) -> tuple[Any, str]:
    """1 項目の新しい値と出所（`測った` / `判断した` / `不明`）。`problem_of` は形の誤りを返す（合えば None）。"""
    if not m:
        return {"unknown": "測定の結果に無い"}, "不明"
    status = m.get("status")
    if status == MEASURED:
        return m.get("value"), "測った"
    if status != QUESTION:
        return {"unknown": str(m["reason"]) if m.get("reason") else "測れない"}, "不明"
    ans = answers.get(key)
    if not isinstance(ans, dict):
        return {"unknown": "回答が無い"}, "不明"
    if "unknown" in ans:
        return {"unknown": str(ans["unknown"]) if ans["unknown"] else "決められない"}, "不明"
    problem = problem_of(key, ans.get("value"))
    if problem:
        return {"unknown": f"回答が形に合わない: {problem}"}, "不明"
    return ans.get("value"), "判断した"


def _put_row(key: str, value: Any, origin: str) -> dict[str, Any]:
    if is_unknown(value):
        return {"kind": "unknown", "key": key, "reason": value["unknown"]}
    return {"kind": "written", "key": key, "origin": origin}


def _replace_ours(key: str, cur: Any, new: Any, origin: str) -> tuple[Any, list[dict[str, Any]]]:
    """前の `write` の値のままの項目を置き換える。新しい値が不明なら前の値を残す。"""
    if is_unknown(new) and not is_unknown(cur):
        return cur, [{"kind": "unknown", "key": key, "reason": new["unknown"], "kept_previous": True}]
    return new, [_put_row(key, new, origin)]


def _owned_decision(tag: str, cur: Any, new: Any, written_before: dict[str, Any]) -> Optional[dict[str, Any]]:
    """前の値 `cur` のオーナーを指紋で見る。前の `write` の値のまま（指紋が一致）なら None（置き換えてよい）。
    手の値なら、新しい値が不明か同じ値で `kept`、違えば `mismatch` の行を返す。"""
    if written_before.get(tag) == value_digest(cur):
        return None
    if is_unknown(new) or value_digest(new) == value_digest(cur):
        return {"kind": "kept", "key": tag}
    return {"kind": "mismatch", "key": tag, "declared": cur, "analyzed": new}


def merge_item(
    key: str, current: tuple[bool, Any], new: Any, origin: str, written: dict[str, Any]
) -> tuple[Any, list[dict[str, Any]], bool]:
    """1 項目を決める。`current` は (前の値が有るか, 前の値)。返すのは (書く値, 出力の行, 解析の値か)。"""
    has_cur, cur = current
    if has_cur and has_secret(cur):
        return {"unknown": SECRET_REASON}, [{"kind": "secret", "key": key}], True
    rows: list[dict[str, Any]] = []
    if has_secret(new):
        new, origin = {"unknown": SECRET_REASON}, "不明"
        rows.append({"kind": "secret", "key": key})
    if not has_cur:
        return new, rows + [_put_row(key, new, origin)], True
    decided = _owned_decision(key, cur, new, written)
    if decided is not None:
        return cur, rows + [decided], False
    value, got = _replace_ours(key, cur, new, origin)
    return value, rows + got, True


def written_digests(doc: Optional[dict[str, Any]]) -> dict[str, Any]:
    """前の `write` が書いた値の指紋（設定の `analysis.written`）。無ければ空。"""
    return obj(obj(obj(doc).get("analysis")).get("written"))


def merge_project(
    existing: Optional[dict[str, Any]],
    measure: dict[str, Any],
    answers: dict[str, Any],
    analysis: dict[str, Any],
    written_before: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """新しい `project.json` と出力の行。`analysis` は `written` を除いたセクション（ここで指紋を作る）。`written_before` は前の指紋。"""
    doc = dict(existing) if existing else {"version": 1}
    written, rows = {}, []
    items = obj(measure.get("items"))
    for key in ITEM_KEYS:
        new, origin = resolve_item(key, items.get(key), answers, item_problem)
        value, got, ours = merge_item(key, (key in doc, doc.get(key)), new, origin, written_before)
        doc[key] = value
        rows += got
        if ours:
            written[key] = value_digest(value)
    doc.pop("analysis", None)
    doc["analysis"] = {**analysis, "written": written}
    return doc, rows


def unified(path: str, old: Optional[str], new: str) -> str:
    """差分（秘密の形の行は伏せる）。"""
    lines = difflib.unified_diff((old if old is not None else "").splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="")
    return "\n".join(line[:1] + redact(line[1:]) if not line.startswith(("+++", "---", "@@")) else line for line in lines)


def ci_job_files(test: dict[str, Any]) -> list[str]:
    """テストの suite（`kind` が `test` か未指定）の `ci_jobs` が指すワークフローのファイル（`#` より前）。"""
    paths = (
        str(job).split("#", 1)[0]
        for suite in seq(test.get("suites"))
        if isinstance(suite, dict) and suite.get("kind") in (None, "test")
        for job in seq(suite.get("ci_jobs"))
    )
    return list(dict.fromkeys(p for p in paths if p))


def _put_ci(doc: dict[str, Any], value: Optional[bool]) -> dict[str, Any]:
    """`doc["test"]["ci"]["only"]` を `value` にした写し。None なら消し、空になった `ci` も消す。"""
    test = dict(doc["test"])
    ci = dict(obj(test.get("ci")))
    if ci.get("only") == value or (value is None and "only" not in ci):
        return doc
    ci.pop("only", None)
    if value is not None:
        ci["only"] = value
    test.pop("ci", None)
    return {**doc, "test": {**test, **({"ci": ci} if ci else {})}}


def declare_ci(
    doc: dict[str, Any], previous: Optional[dict[str, Any]], written_before: dict[str, Any], exists: Callable[[str], bool]
) -> tuple[dict[str, Any], str, Optional[str]]:
    """書き出す設定の `test.ci.only` を決め、(設定, 出力の 1 行, 指紋か None) を返す。"""
    test = doc.get("test")
    if not isinstance(test, dict) or is_unknown(test):
        return doc, "変えない（test が不明）", None
    cur = obj(obj(obj(previous).get("test")).get("ci")).get("only")
    ours = cur is not None and written_before.get(CI_KEY) == value_digest(cur)
    if cur is False or (cur is True and not ours):
        return _put_ci(doc, cur), "変えない（人が書いた値）", None
    if any(exists(p) for p in ci_job_files(test)):
        return _put_ci(doc, True), ("変えない（条件を満たす）" if cur is True else "書いた"), value_digest(True)
    verb = "消した" if cur is True else "変えない"
    return _put_ci(doc, None), f"{verb}（テストの suite の CI のジョブが無い）", None


def _declare_ci_tests(
    doc: dict[str, Any], existing: Optional[dict[str, Any]], written_before: dict[str, Any], root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """CI テストの設定を決める。`test` を解析が持つなら、その指紋を書き足した値で取り直す。"""
    doc, line, digest = declare_ci(doc, existing, written_before, lambda p: (root / p).is_file())
    written = doc["analysis"]["written"]
    written.pop(CI_KEY, None)
    if digest is not None:
        written[CI_KEY] = digest
    if "test" in written:
        written["test"] = value_digest(doc["test"])
    return doc, {"kind": "note", "key": CI_KEY, "text": f"CI テストの設定: {line}"}


def ignored_notes(root: Path, rels: list[str]) -> list[dict[str, Any]]:
    """`rels` のうち `.gitignore` の対象のもの（kind note・key gitignore の行）。否定の行に当たったもの・git の外は外す。"""
    try:
        done = subprocess.run(["git", "check-ignore", "-v", "--", *rels], cwd=root, capture_output=True, text=True, check=False)
    except OSError:  # 確かめられないことを除外と読ませない
        return []
    if done.returncode not in (0, 1):
        return []
    out = []
    for line in done.stdout.splitlines():
        head, sep, path = line.partition("\t")
        src, _, rest = head.partition(":")
        num, _, pattern = rest.partition(":")
        if sep and not pattern.startswith("!"):
            text = f"{path} は {src}:{num} の `{pattern}` が除外している（コミットできない。.gitignore は変えない）"
            out.append(
                {
                    "kind": "note",
                    "key": "gitignore",
                    "name": path,
                    "result": "ignored",
                    "source": f"{src}:{num}",
                    "pattern": pattern,
                    "text": text,
                }
            )
    return out


def summary_note(rows: list[dict[str, Any]]) -> str:
    """要約へ添える 1 文（除外が無ければ空）。"""
    hits = [n for n in rows if n.get("key") == "gitignore"]
    if not hits:
        return ""
    names = "・".join(n["name"] for n in hits)
    rules = "・".join(dict.fromkeys(f"{n['source']} の `{n['pattern']}`" for n in hits))
    return f"。設定をコミットできない（{rules} が除外している: {names}）"


def _same_but_time(old: Optional[dict[str, Any]], new: dict[str, Any]) -> bool:
    if old is None:
        return False

    def strip(d: dict[str, Any]) -> dict[str, Any]:
        return {**d, "analysis": {k: v for k, v in obj(d.get("analysis")).items() if k != "at"}}

    return value_digest(strip(old)) == value_digest(strip(new))


class Inputs(NamedTuple):
    """書き出しの入力（作業場所の git・メインディレクトリ・今の設定・測定の結果・回答）。"""

    root: Path
    main: Path
    existing: Optional[dict[str, Any]]
    measure: dict[str, Any]
    answers: dict[str, Any]


def plan(inp: Inputs) -> Plan:
    """書くファイルと出力の行を決める（何も書かない）。書き出す設定が形に合わなければ `WriteError`。"""
    questions = {k for k, m in obj(inp.measure.get("items")).items() if obj(m).get("status") == QUESTION}
    rows = [{"kind": "ignored", "key": k} for k in inp.answers if k not in questions]
    written_before = written_digests(inp.existing)
    analysis = {
        "analyzer": check.ANALYZER,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "inputs": obj(inp.measure.get("inputs")),
        "branches": check.branch_state(git.Git(inp.root), check.declared_branches(inp.existing)),
    }
    doc, item_rows = merge_project(inp.existing, inp.measure, inp.answers, analysis, written_before)
    doc, ci_row = _declare_ci_tests(doc, inp.existing, written_before, inp.root)
    errs = contracts.schema_errors(doc, check.SCHEMA)
    if errs:
        raise WriteError(f"書き出す設定が形に合わない（解析器の誤り）: {'; '.join(errs[:3])}")
    out: list[File] = []
    if not _same_but_time(inp.existing, doc):
        path = files.path(inp.main, "project")
        out.append(File(path, files.relpath("project"), path.read_text(encoding="utf-8") if inp.existing is not None else None, dumps(doc)))
    notes = [{"kind": "note", "key": "notes", "text": n} for n in seq(inp.measure.get("notes"))]
    return Plan(out, item_rows + [ci_row] + rows + notes + ignored_notes(inp.main, [files.relpath("project")]))


def p_number(key: str, default: str) -> str:
    """項目のキー（`#` より前を見る）の P の番号。表に無ければ `default`。"""
    numbers = {
        "languages": "P1",
        "test": "P2",
        "ci": "P4",
        "services": "P5",
        "issues": "P8",
        "checks": "P9",
        "ndf_policies": "P9",
        "instructions": "P10",
    }
    head = key.split("#")[0]
    return numbers[head] if head in numbers else default


def row_line(row: dict[str, Any]) -> str:
    """出力の行 1 つを人が読む 1 行にする。"""
    key, kind = row["key"], row["kind"]
    p = p_number(key, "")
    if kind == "mismatch":
        return f"  {p} {key}: 食い違い 設定 {json.dumps(row['declared'], ensure_ascii=False)} / 解析 {json.dumps(row['analyzed'], ensure_ascii=False)}"
    if kind == "unknown":
        return f"  {p} {key}: 不明（{row.get('reason')}）{'（前の値を残した）' if row.get('kept_previous') else ''}"
    labels = {"written": str(row.get("origin")), "kept": "手の値を保った", "secret": "秘密の形の値を捨てて不明にした"}
    return f"  {p} {key}: {labels[kind] if kind in labels else kind}"
