"""工程の定義の読み込みと検査（#3092 の決定 3・4・5・8）。

工程の定義は `plugins/ndf/contracts/workflow/` の Serverless Workflow 1.0.3 の書式の JSON である。

- `modes/<モード>.json`: 工程の文書を `run.workflow` で順に呼ぶだけ（決定 4）
- `stages/<工程の ID>.json`: 工程ごとのアクティビティの並び（`call`・`listen`）と入出力規約の参照
- `schemas/*.schema.json`: 入出力規約（JSON Schema draft 2020-12）。参照は `ndf://schemas/<ファイル>#<ポインタ>`

NDF に固有の属性は `metadata.ndf` に閉じた語彙で置き、`if` を使わない（決定 5）。ファイルを読むのは
`load_definition` と `mode_heights` だけで、ほかは純粋な処理である。誤りは理由の一覧で返す。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

DSL_VERSION = "1.0.3"
WORKFLOW_DIR = Path(__file__).resolve().parents[2] / "contracts" / "workflow"
DSL_SCHEMA = f"serverless-workflow-{DSL_VERSION}.schema.json"
COMMON_SH = Path(__file__).resolve().parents[2] / "skills" / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"
MODES = ("light", "operation", "legacy-refactor", "standard", "documentation")
REF_PREFIX = "ndf://schemas/"

# 使う要素は 5 種類（決定 5）。`metadata.ndf` のキーごとの値の一覧（設計の runtime 文書の「metadata.ndf の語彙」）
TASK_KINDS = ("do", "run", "call", "listen", "for")
TASK_FIELDS = ("with", "metadata", "input", "output")  # 5 種類の要素に添える欄（`if`・`then`・`export` などは使わない）
VOCABULARY: dict[str, Optional[tuple]] = {
    "stage": None,  # 工程表の行名（自由な字）
    "when": (
        "code_in_diff",
        "docs_only_diff",
        "behavior_docs_in_diff",
        "ci_declared",
        "prod_branch",
        "release_record",
        "retro_enabled",
        "design_pr",
    ),
    "kind": ("review", "test", "release", "verify", "merge", "work", "gate", "record"),
    "range": ("sprint_diff", "issue_diff", "released", "lane", "none"),
    "subject": ("code", "docs", "design"),
    "per_issue": (True,),
}
DIFF_RANGES = ("sprint_diff", "issue_diff")
LISTEN_CALL = "ndf.listen"
RETRO_STAGE = "振り返り"


class WorkflowDefError(ValueError):
    """定義を読めないか検査で断った。`errors` に理由の一覧を持つ。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class ActivityDef:
    id: str
    stage: str
    call: str
    kind: str
    range: str
    subject: Optional[str] = None
    with_: dict = field(default_factory=dict)
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.stage}/{self.id}"


@dataclass(frozen=True)
class StageDef:
    name: str
    id: str
    when: tuple = ()
    per_issue: bool = False
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
    activities: tuple = ()


@dataclass(frozen=True)
class Enablement:
    enabled: bool
    reason: Optional[str] = None
    source: str = "definition"  # definition / project / sprint


@dataclass(frozen=True)
class Snapshot:
    mode: str
    version: str
    sha256: str
    stages: tuple
    schemas: dict

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self.stages]

    def stage(self, name: str) -> StageDef:
        for s in self.stages:
            if s.name == name:
                return s
        raise KeyError(name)

    def activities(self) -> list[ActivityDef]:
        return [a for s in self.stages for a in s.activities]

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "version": self.version,
            "sha256": self.sha256,
            "stages": [
                {**{k: v for k, v in asdict(s).items() if k != "activities"}, "when": list(s.when),
                 "activities": [asdict(a) for a in s.activities]}
                for s in self.stages
            ],
            "schemas": self.schemas,
        }

    @staticmethod
    def from_dict(d: dict) -> "Snapshot":
        stages = tuple(
            StageDef(
                name=s["name"], id=s["id"], when=tuple(s.get("when") or ()), per_issue=bool(s.get("per_issue")),
                input_ref=s.get("input_ref"), output_ref=s.get("output_ref"),
                activities=tuple(ActivityDef(**a) for a in s.get("activities") or ()),
            )
            for s in d["stages"]
        )
        return Snapshot(d["mode"], d["version"], d["sha256"], stages, dict(d.get("schemas") or {}))


# --- 読み込み ---------------------------------------------------------------


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkflowDefError([f"{path}: ファイルが無い"]) from None
    except json.JSONDecodeError as e:
        raise WorkflowDefError([f"{path}: JSON の誤り（{e}）"]) from None


def dsl_body(raw: dict) -> dict:
    """借りたスキーマの本文（取得元を書いた `$comment` を外したもの）。"""
    return {k: v for k, v in raw.items() if k != "$comment"}


def body_sha256(raw: dict) -> str:
    """借りたスキーマの本文の sha256（キーを並べた JSON。`$comment` の値と照らす）。"""
    text = json.dumps(dsl_body(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ndf_meta(task: dict) -> dict:
    return ((task.get("metadata") or {}).get("ndf")) or {}


def _ref_of(task: dict, side: str) -> Optional[str]:
    try:
        return task[side]["schema"]["resource"]["endpoint"]
    except (KeyError, TypeError):
        return None


def _items(do: list) -> list[tuple[str, dict]]:
    return [next(iter(item.items())) for item in do if isinstance(item, dict) and item]


def _activity(stage: str, aid: str, task: dict) -> ActivityDef:
    meta = _ndf_meta(task)
    call = LISTEN_CALL if "listen" in task else task.get("call", "")
    return ActivityDef(
        id=aid, stage=stage, call=call, kind=meta.get("kind", ""), range=meta.get("range", ""),
        subject=meta.get("subject"), with_=dict(task.get("with") or {}),
        input_ref=_ref_of(task, "input"), output_ref=_ref_of(task, "output"),
    )


def load_definition(mode: str, root: Optional[Path] = None) -> Snapshot:
    """モードの文書と、それが呼ぶ工程の文書・入出力規約を読み、検査を通したスナップショットを返す。"""
    if mode not in MODES:
        raise WorkflowDefError([f"知らないモード: {mode}（{' / '.join(MODES)}）"])
    base = root or WORKFLOW_DIR
    dsl = dsl_body(_read(base / DSL_SCHEMA))
    mode_doc = _read(base / "modes" / f"{mode}.json")
    errors = [f"modes/{mode}.json: {e}" for e in check_dsl(mode_doc, dsl) + check_vocabulary(mode_doc)]
    stages: list[StageDef] = []
    hasher = hashlib.sha256(json.dumps(mode_doc, sort_keys=True).encode())
    for name, task in _items(mode_doc.get("do") or []):
        wf = (task.get("run") or {}).get("workflow") or {}
        sid = wf.get("name", "")
        path = base / "stages" / f"{sid}.json"
        try:
            doc = _read(path)
        except WorkflowDefError as e:
            errors.extend(e.errors)
            continue
        hasher.update(json.dumps(doc, sort_keys=True).encode())
        errors += [f"stages/{sid}.json: {e}" for e in check_dsl(doc, dsl) + check_vocabulary(doc)]
        dmeta = ((doc.get("document") or {}).get("metadata") or {}).get("ndf") or {}
        if doc.get("document", {}).get("version") != wf.get("version"):
            errors.append(f"modes/{mode}.json: {name} は版 {wf.get('version')} を呼ぶが stages/{sid}.json は {doc.get('document', {}).get('version')}")
        if dmeta.get("stage") != name:
            errors.append(f"modes/{mode}.json: {name} が呼ぶ stages/{sid}.json の stage は {dmeta.get('stage')}")
        when = tuple(dmeta.get("when") or ()) + tuple(w for w in _ndf_meta(task).get("when") or () if w not in (dmeta.get("when") or ()))
        acts = tuple(_activity(name, aid, t) for aid, t in _items(doc.get("do") or []))
        stages.append(StageDef(name, sid, when, bool(dmeta.get("per_issue")), _ref_of(doc, "input"), _ref_of(doc, "output"), acts))
    schemas = {p.name: _read(p) for p in sorted((base / "schemas").glob("*.json"))}
    errors += check_contracts(stages, schemas)
    errors += check_order([a for s in stages for a in s.activities])
    if errors:
        raise WorkflowDefError(errors)
    version = (mode_doc.get("document") or {}).get("version", "")
    return Snapshot(mode, version, hasher.hexdigest(), tuple(stages), schemas)


@lru_cache(maxsize=4)
def mode_heights(root: Optional[Path] = None) -> dict[str, int]:
    """モードの高さを正本（`workflow-common.sh` の `WF_MODE_HEIGHT`）から読む。"""
    path = root or COMMON_SH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise WorkflowDefError([f"{path}: 読めない（{e}）"]) from None
    m = re.search(r"^WF_MODE_HEIGHT=\$'(.*?)'", text, re.S | re.M)
    if not m:
        raise WorkflowDefError([f"{path}: WF_MODE_HEIGHT の行が無い"])
    out = {}
    for line in m.group(1).replace("\\t", "\t").splitlines():
        name, _, h = line.partition("\t")
        if name.strip() and h.strip().isdigit():
            out[name.strip()] = int(h)
    if set(out) != set(MODES):
        raise WorkflowDefError([f"{path}: WF_MODE_HEIGHT のモードがそろわない（{sorted(out)}）"])
    return out


# --- 検査（純粋） ------------------------------------------------------------


def check_dsl(doc: dict, schema: dict) -> list[str]:
    """1 つの文書が借りた標準のスキーマに合うか。"""
    from jsonschema import Draft202012Validator

    return [f"{e.json_path}: {e.message[:200]}" for e in Draft202012Validator(schema).iter_errors(doc)]


def _walk_tasks(do: list, path: str):
    for name, task in _items(do):
        here = f"{path}.{name}"
        yield here, task
        if isinstance(task, dict):
            for sub in ("do",):
                if isinstance(task.get(sub), list):
                    yield from _walk_tasks(task[sub], here)


def _check_meta(meta: dict, where: str, is_activity: bool) -> list[str]:
    errs = []
    for key, value in meta.items():
        if key not in VOCABULARY:
            errs.append(f"{where}: metadata.ndf の知らないキー {key}")
            continue
        allowed = VOCABULARY[key]
        values = value if isinstance(value, list) else [value]
        if allowed is not None:
            errs += [f"{where}: metadata.ndf.{key} の知らない値 {v}" for v in values if v not in allowed]
    if is_activity:
        kind = meta.get("kind")
        if not kind:
            errs.append(f"{where}: アクティビティに metadata.ndf.kind が無い")
        if not meta.get("range"):
            errs.append(f"{where}: アクティビティに metadata.ndf.range が無い")
        if kind == "review" and not meta.get("subject"):
            errs.append(f"{where}: kind: review に subject が無い")
        if kind and kind != "review" and meta.get("subject"):
            errs.append(f"{where}: kind: {kind} に subject は書かない")
    return errs


def check_vocabulary(doc: dict) -> list[str]:
    """使う要素を 5 種類に、`metadata.ndf` を閉じた語彙に限る（決定 5）。"""
    errs = []
    dmeta = ((doc.get("document") or {}).get("metadata") or {}).get("ndf") or {}
    errs += _check_meta(dmeta, "document", False)
    for where, task in _walk_tasks(doc.get("do") or [], "do"):
        if not isinstance(task, dict):
            errs.append(f"{where}: 要素が辞書でない")
            continue
        if "if" in task:
            errs.append(f"{where}: if は使わない（条件は metadata.ndf.when）")
        if not any(k in TASK_KINDS for k in task):
            errs.append(f"{where}: 使う 5 種類（{' / '.join(TASK_KINDS)}）の要素でない")
        errs += [f"{where}: 使わない要素 {k}" for k in task if k not in TASK_KINDS + TASK_FIELDS]
        is_activity = "call" in task or "listen" in task
        errs += _check_meta(_ndf_meta(task), where, is_activity)
    return errs


def _resolve_ref(ref: str, schemas: dict[str, dict]) -> Optional[str]:
    """参照先があれば None、無ければ理由。"""
    if not ref.startswith(REF_PREFIX):
        return f"参照 {ref} が {REF_PREFIX} で始まらない"
    rest = ref[len(REF_PREFIX):]
    name, _, pointer = rest.partition("#")
    if name not in schemas:
        return f"参照先のファイル {name} が無い"
    node: Any = schemas[name]
    for part in [p for p in pointer.split("/") if p]:
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return f"参照 {ref} の {part} が無い"
        node = node[part]
    return None


def check_contracts(stages: list[StageDef], schemas: dict[str, dict]) -> list[str]:
    """工程とアクティビティのすべてが入出力規約を参照し、参照先があるか（I7）。"""
    errs = []
    targets = [(s.name, s.input_ref, s.output_ref) for s in stages]
    targets += [(a.key, a.input_ref, a.output_ref) for s in stages for a in s.activities]
    for where, inp, out in targets:
        for side, ref in (("input", inp), ("output", out)):
            if not ref:
                errs.append(f"{where}: {side} の規約の参照が無い（I7）")
                continue
            why = _resolve_ref(ref, schemas)
            if why:
                errs.append(f"{where}: {side} の{why}（I7）")
    return errs


def check_order(activities: list[ActivityDef]) -> list[str]:
    """モードの全アクティビティを並びの順に平らにした一覧で、順序・範囲・重複の決まりを確かめる（I8〜I10）。"""
    errs = []
    released: list[tuple[int, str]] = [(i, a.range) for i, a in enumerate(activities) if a.kind == "release"]
    seen_review: dict[tuple, str] = {}
    seen_test: dict[str, str] = {}
    for i, a in enumerate(activities):
        if a.kind in ("review", "test"):
            if a.range not in DIFF_RANGES:
                errs.append(f"{a.key}: {a.kind} の範囲 {a.range} が差分でない（I9）")
            later = [j for j, rng in released if j < i and rng == a.range]
            if later:
                errs.append(f"{a.key}: 同じ範囲 {a.range} の本番へ出すアクティビティ {activities[later[0]].key} より後ろにある（I8a）")
        if a.kind == "verify":
            if a.range != "released":
                errs.append(f"{a.key}: verify の範囲 {a.range} がリリースした版でない（I9）")
            if not any(j < i for j, _ in released):
                errs.append(f"{a.key}: verify より前に本番へ出すアクティビティが無い（I8b）")
        if a.kind == "review":
            k = (a.range, a.subject)
            if k in seen_review:
                errs.append(f"{a.key}: 同じ範囲 {a.range} と対象 {a.subject} のレビューが {seen_review[k]} にもある（I10）")
            seen_review.setdefault(k, a.key)
        if a.kind == "test":
            if a.range in seen_test:
                errs.append(f"{a.key}: 同じ範囲 {a.range} のテストが {seen_test[a.range]} にもある（I10）")
            seen_test.setdefault(a.range, a.key)
    return errs


def resolve_enabled(
    snap: Snapshot, facts: dict[str, bool], declared: dict[str, dict], disables: dict[str, Optional[str]]
) -> dict[str, Enablement]:
    """3 つの順位（定義の条件 → プロジェクトの宣言 → 引数と conductor の操作）で工程ごとの有効を決める（決定 8）。

    `disables` は工程の名前 → 理由。値が None なら conductor が有効へ戻したもの（順位 3 の有効）。
    `retro_enabled` は、振り返りの工程があればその解決の結果とも組む（決定 11: 振り返りが無効なら組み換えも無効）。
    """
    missing = sorted({w for s in snap.stages for w in s.when} - set(facts))
    if missing:
        raise WorkflowDefError([f"条件の事実が無い: {', '.join(missing)}"])
    local = dict(facts)
    out: dict[str, Enablement] = {}
    for s in snap.stages:
        if s.name in disables:
            reason = disables[s.name]
            out[s.name] = Enablement(reason is None, reason, "sprint")
        elif s.name in declared and "enabled" in declared[s.name]:
            d = declared[s.name]
            out[s.name] = Enablement(bool(d["enabled"]), None if d["enabled"] else d.get("reason") or "プロジェクトの宣言", "project")
        else:
            false = next((w for w in s.when if not local[w]), None)
            out[s.name] = Enablement(false is None, false, "definition")
        if s.name == RETRO_STAGE:
            local["retro_enabled"] = bool(local.get("retro_enabled", True)) and out[s.name].enabled
    return out


def validate_io(snap: Snapshot, ref: str, obj: object) -> list[str]:
    """スナップショットに入れたスキーマで入力か出力を検査する（I3・I17）。"""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    why = _resolve_ref(ref, snap.schemas)
    if why:
        return [why]
    registry = Registry().with_resources(
        [(REF_PREFIX + name, Resource.from_contents(body)) for name, body in snap.schemas.items()]
    )
    validator = Draft202012Validator({"$ref": ref}, registry=registry)
    return [f"{e.json_path}: {e.message[:200]}" for e in validator.iter_errors(obj)]
