"""branch_strategy_write.py: ブランチ戦略の宣言を組み、検査を通した中身だけを書く・消す（#3672 の決定 4・5・I2）。

セクション `branch_strategy` を作る・更新する・移す・消すのは、解析の `project-decl.py branch-strategy` の動詞だけで、
書き込みはどれもこのモジュールの `commit_plan` を通る。

- `init_values`: 代表のリポジトリの種類の既定の行（`data/branch-strategy-presets.json`）に測った名前を入れる
- `apply_answers`: 問いへの答えを入れる。既定から変えたら種類を `custom` にする
- `plan_write`・`plan_remove`: 書く前の検査（`branch_strategy.validate`）を通し、差分とハッシュを作る。書かない
- `commit_plan`: 錠を取り、今のファイルから計画を作り直してハッシュが合うときだけ一時ファイルから置き換える

錠は git の共通ディレクトリの `ndf-branch-strategy.lock`（11.0 と同じ実ファイル）。使う側は
`deps.require("jsonschema", "locks")` を先に呼ぶ。
"""

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import branch_strategy as bs
import branch_strategy_legacy as legacy
import jsonio

PRESETS = Path(__file__).resolve().parents[1] / "data" / "branch-strategy-presets.json"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")
LOCK_TARGET = "ndf-branch-strategy"
LOCK_LIMIT = "branch-strategy.write-lock"
# 書く順（I1 の途中の状態を作らない順）。セクションを先に書き、旧い 3 ファイルのキーを後に消す。
# legacy.FILES は project を先頭に書いてあり、その並びをそのまま使う
WRITE_ORDER = tuple(legacy.FILES.values())
# 変える中身が無い計画のハッシュ
EMPTY_DIGEST = bs.strategy_digest({"diff": [], "before": {}})


class WriteError(Exception):
    """書けない（ハッシュの違い・錠・ファイルの破損）。`exit_code` は CLI の終了コード。"""

    def __init__(self, reason: str, exit_code: int = 1) -> None:
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code


@dataclass
class WritePlan:
    """書く計画。`files` はファイル名 → 書いた後の中身（変えないファイルは入れない）。"""

    files: dict[str, Any] = field(default_factory=dict)
    diff: list[dict] = field(default_factory=list)
    digest: str = ""
    failed: list[dict] = field(default_factory=list)
    rebuild: Callable[[], "WritePlan"] | None = None


def load_presets(path: Path | None = None) -> dict:
    return json.loads((path or PRESETS).read_text(encoding="utf-8"))


def init_values(kind: str, facts: dict, presets: dict) -> tuple[dict, list[dict]]:
    """種類の既定の行に測った名前を入れたセクションと、merge queue が分からないときの問いを返す。

    `facts` のキー: `production`（既定ブランチ）・`base`（ベースブランチ。無ければ既定の名前）・`plugin`・`runtimes`・
    `workflow`（CD のワークフロー。無ければ None）・`dev_workflow`（開発版のワークフロー）・`merge_queue`（真偽か None）。
    返す 2 つ目の値は `questions` の並び。各値の根拠は `evidence_of` で引く。
    """
    if kind not in KINDS:
        raise ValueError(f"種類は {'・'.join(KINDS)} のどれか: {kind!r}")
    row = presets["kinds"][kind]
    sec = copy.deepcopy(row["section"])
    names = presets["default_names"]
    prod = facts.get("production") or names["production"]
    base = prod if sec["branches"]["base"] == sec["branches"]["production"] else (facts.get("base") or names["base"])
    sec["branches"] = {"production": prod, "base": base}
    sec["channels"] = [{"name": "stable", "ref": prod}]
    if row.get("dev_channel") and facts.get("dev_workflow"):
        sec["channels"].append({"name": "dev", "ref": base, "workflow": facts["dev_workflow"]})
    if facts.get("plugin"):
        sec["release"]["plugin"] = facts["plugin"]
    if facts.get("runtimes"):
        sec["release"]["runtimes"] = list(facts["runtimes"])
    if facts.get("workflow"):
        sec["release"]["cd"] = {"workflow": facts["workflow"]}
        sec["release"]["tag"] = row["with_cd"]["tag"]
        sec["release"]["github_release"] = row["with_cd"]["github_release"]
    questions = []
    alt = row.get("merge_method_without_queue")
    if alt and sec["merge"]["method"] == "merge-queue":
        mq = facts.get("merge_queue")
        if mq is False:
            sec["merge"]["method"] = alt
        elif mq is None:
            questions.append({"pointer": "/merge/method", "why": "merge_queue_unknown", "candidates": ["merge-queue", alt], "evidence": []})
    return sec, questions


def evidence_of(section: dict, facts: dict) -> list[dict]:
    """`init` の値の根拠。測った名前は `repo`、ほかは種類の既定の行（`preset`）。"""
    ev = [{"source": "preset", "ref": section["kind"], "value": "/"}]
    for key, pointer in (("production", "/branches/production"), ("plugin", "/release/plugin"), ("workflow", "/release/cd")):
        if facts.get(key):
            ev.append({"source": "repo", "ref": pointer, "value": str(facts[key])})
    return ev


def set_pointer(doc: dict, pointer: str, value: Any) -> None:
    toks = legacy.pointer_tokens(pointer)
    cur = doc
    for t in toks[:-1]:
        cur = cur.setdefault(t, {})
    cur[toks[-1]] = value


def apply_answers(result: dict, answers: dict) -> dict:
    """問いへの答え（pointer → 値）をセクションの値へ入れる。既定から変えたら種類を `custom` にする。"""
    out = copy.deepcopy(result)
    values = out.setdefault("values", {})
    before = copy.deepcopy(values)
    for pointer, value in answers.items():
        set_pointer(values, pointer, value)
        out.setdefault("evidence", []).append({"source": "answer", "ref": pointer, "value": json.dumps(value, ensure_ascii=False)})
    answered = set(answers)
    out["questions"] = [q for q in out.get("questions", []) if q["pointer"] not in answered]
    if values.get("kind") in KINDS and any(bs.canonical_json(get_pointer(before, p)) != bs.canonical_json(v) for p, v in answers.items()):
        values["kind"] = "custom"
    out["status"] = "ask" if out["questions"] else "ok"
    return out


def get_pointer(doc: Any, pointer: str) -> Any:
    cur = doc
    for t in legacy.pointer_tokens(pointer):
        if not isinstance(cur, dict) or t not in cur:
            return None
        cur = cur[t]
    return cur


def read_decl(path: Path) -> dict | None:
    try:
        return jsonio.read(path, missing=None, want=dict)
    except jsonio.JsonReadError as e:
        raise WriteError(f"{path}: 読めない（{e}）", 2) from e


def json_patch(before: Any, after: Any, prefix: str = "") -> list[dict]:
    """2 つの JSON の値の差分（RFC 6902 の add・replace・remove。キーの昇順）。"""
    if isinstance(before, dict) and isinstance(after, dict):
        ops = []
        for k in sorted(set(before) | set(after)):
            p = f"{prefix}/{k.replace('~', '~0').replace('/', '~1')}"
            if k not in after:
                ops.append({"op": "remove", "path": p})
            elif k not in before:
                ops.append({"op": "add", "path": p, "value": after[k]})
            else:
                ops.extend(json_patch(before[k], after[k], p))
        return ops
    if bs.canonical_json(before) != bs.canonical_json(after):
        return [{"op": "replace", "path": prefix, "value": after}]
    return []


def plan_files(root: Path, after: dict[str, Any], rebuild: Callable[[], WritePlan]) -> WritePlan:
    """`.ndf/` のファイル名 → 書いた後の中身（None は消す）から、差分とハッシュの計画を作る。"""
    ndf = Path(root) / ".ndf"
    plan = WritePlan(rebuild=rebuild)
    befores = {}
    for name in WRITE_ORDER:
        if name not in after:
            continue
        before = read_decl(ndf / name)
        befores[name] = before
        ops = json_patch(before or {}, after[name] or {})
        if ops:
            plan.files[name] = after[name]
            plan.diff.extend({"file": f".ndf/{name}", **op} for op in ops)
    plan.digest = bs.strategy_digest({"diff": plan.diff, "before": befores})
    return plan


def plan_write(root: Path, section: dict) -> WritePlan:
    """書く前に `validate` を通し、今の `.ndf/project.json` との差分とハッシュを作る。落ちれば `failed` だけを持つ。"""
    failed = bs.validate_section(section)
    if failed:
        return WritePlan(failed=failed)
    project = read_decl(Path(root) / ".ndf" / "project.json")
    after = dict(project) if project else {"version": 1}
    after[bs.SECTION] = section
    return plan_files(root, {"project.json": after}, lambda: plan_write(root, section))


def plan_remove(root: Path) -> WritePlan:
    """セクションを消す差分とハッシュを作る。セクションが無ければ空の差分。"""
    project = read_decl(Path(root) / ".ndf" / "project.json")
    if not project or bs.SECTION not in project:
        return WritePlan(digest=EMPTY_DIGEST, rebuild=lambda: plan_remove(root))
    after = {k: v for k, v in project.items() if k != bs.SECTION}
    return plan_files(root, {"project.json": after}, lambda: plan_remove(root))


def _common_dir(root: Path) -> Path:
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    if r.returncode != 0:
        raise WriteError(f"{root}: git の共通ディレクトリを読めない（{r.stderr.strip()}）", 2)
    return Path(r.stdout.strip())


def commit_plan(root: Path, plan: WritePlan, digest: str | None) -> WritePlan:
    """錠を取り、今のファイルから計画を作り直す。`digest` を渡したときは一致するときだけ書く。書いた計画を返す。"""
    import limits
    import locks

    if plan.failed:
        raise WriteError("検査に落ちた計画は書かない")
    try:
        with locks.exclusive(_common_dir(root) / LOCK_TARGET, timeout=limits.seconds(LOCK_LIMIT)):
            fresh = plan.rebuild() if plan.rebuild else plan
            if fresh.failed:
                raise WriteError("今のファイルから作り直した計画が検査に落ちた")
            if digest is not None and fresh.digest != digest:
                raise WriteError("示した差分と今の差分が違います。打ち直してください")
            ndf = Path(root) / ".ndf"
            for name in WRITE_ORDER:
                if name in fresh.files:
                    jsonio.write_atomic(ndf / name, fresh.files[name])
            return fresh
    except locks.LockTimeout as e:
        raise WriteError("別の実行が宣言を書いています。終わってから打ち直してください") from e
    except OSError as e:
        raise WriteError(f"書けない（{e}）") from e
