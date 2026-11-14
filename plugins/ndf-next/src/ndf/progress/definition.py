"""工程の定義（`contracts/workflow/`）の読み込みと検査。

モードの文書（`modes/<モード>.json`）は工程の文書（`stages/<工程>.json`）を順に呼ぶだけで、工程の文書が
ステップを持つ（決定 4）。ステップは作業を呼ぶ `call` か、承認ゲートの答えを待つ `listen` である。どちらも Serverless Workflow 1.0.3 の書式で書き、公式のスキーマ
（`dsl/workflow-1.0.3.json`）で検査する（決定 3）。NDF に固有の条件と属性は `metadata.ndf` に置き、
閉じた語彙（`ndf.schema.json`）で検査する（決定 5）。

入出力の規約の参照は `file:///<contracts からのパス>#<JSON Pointer>` の形で書く。規約のファイルどうしの
`$ref`（`context.schema.json`・`../schemas/result.schema.json`）は、規約のファイルを 1 つの束へ入れて解く。

承認ゲートの待ち方（#3092 の決定 7・決定 77）は `listen` の要素で書く:

    {"design-gate": {"listen": {"to": {"one": {"with": {"type": "answered"}}}}, "metadata": {"ndf": {"kind": "gate", ...}}}}

- recv: `ndf progress next`（`work`）は `listen` のステップで行を実行中のまま抜け、答えを待つ（`gate` の結果を返す）
- answer: 待つ事象は `to.one.with.type` に書いた進行の事象（`answered`。`ndf answer` が書く。表 `workflow/progress.json`）
- next: 答えが書かれた後の `ndf progress next` が答えを読み、次のステップへ進む
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from jsonschema import Draft202012Validator

from ndf import contracts, identity
from ndf.progress import model, stages

DSL = "workflow/dsl/workflow-1.0.3.json"
NDF_SCHEMA = "workflow/ndf.schema.json"
CONTEXT = "workflow/context.schema.json"
MODES_DIR = "workflow/modes"
STAGES_DIR = "workflow/stages"
# 規約の束へ入れるスキーマの置き場（contracts からのパス）
SCHEMA_DIRS = ("workflow", contracts.SCHEMAS)
# 入出力の規約の参照の接頭辞（この後ろが contracts からのパス）
URI_ROOT = "file:///"
NAMESPACE = identity.FIXED_WORDS["workflow_namespace"]
# 文書の種類ごとに使える要素（モードは工程の文書を呼ぶだけ、工程はステップを呼ぶだけ）
TASKS = {"mode": frozenset({"run"}), "stage": frozenset({"call", "listen"})}
# `listen` の要素のステップの call（呼ぶものの表 `invoke.CALLS` の承認ゲートの行）
LISTEN_CALL = "ndf.listen"
# `listen` の要素が待つ進行の事象（`ndf answer` が書く答え。表 `workflow/progress.json` のスプリントの行。無ければ import の時点で `KeyError`）
LISTENED_EVENT = model.rules()[("sprint", "answered")].kind


class WorkflowDefError(contracts.ContractError):
    """工程の定義が読めない・検査を通らない。`errors` は理由の一覧。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("工程の定義が検査を通らない: " + "; ".join(errors))
        self.errors = errors


# --- スキーマ ----------------------------------------------------------------------------


@contracts.cached_reader
def _dsl_validator() -> Draft202012Validator:
    return Draft202012Validator(contracts.load_json(DSL))


@contracts.cached_reader
def _task_kinds() -> frozenset[str]:
    """公式のスキーマの要素の名前（`call`・`do`・`run` など）。`$defs/task` の候補の参照から引く。"""
    refs = [branch["$ref"] for branch in contracts.load_json(DSL)["$defs"]["task"]["oneOf"]]
    return frozenset(r.rsplit("/", 1)[1].removesuffix("Task") for r in refs)


def _schema_files() -> list[str]:
    root = contracts.contracts_dir()
    found = []
    for d in SCHEMA_DIRS:
        found += sorted(str(p.relative_to(root)) for p in (root / d).glob("*.schema.json"))
    return found


@contracts.cached_reader
def _io_validator(ref: str) -> Draft202012Validator:
    """規約のファイルを束にし、絶対の `$id` を付けて、`ref` を引く検証器を作る。"""
    defs = {rel: {**contracts.load_json(rel), "$id": URI_ROOT + rel} for rel in _schema_files()}
    bundle = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": URI_ROOT + "bundle.json", "$defs": defs, "$ref": ref}
    return Draft202012Validator(bundle)


def _errors(validator: Draft202012Validator, instance: Any) -> list[str]:
    found = sorted(("/".join(str(p) for p in e.absolute_path) or "(最上位)", e.message) for e in validator.iter_errors(instance))
    return [f"{where}: {message}" for where, message in found]


def ref_target(ref: str) -> Any:
    """規約の参照が指す値。指す先が無ければ None。"""
    if not ref.startswith(URI_ROOT):
        return None
    rel, _, pointer = ref[len(URI_ROOT) :].partition("#")
    try:
        node: Any = contracts.load_json(rel)
    except contracts.ContractError:  # noqa: NDF401 ファイル: 読めない規約のファイルは指す先が無いとして返す
        return None
    for part in (p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/") if p):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def validate_io(ref: str, obj: Any) -> list[str]:
    """規約の参照で入力か出力を確かめ、誤りを返す。空なら合う（I3・I17）。"""
    if ref_target(ref) is None:
        return [f"規約 {ref} が無い"]
    return _errors(_io_validator(ref), obj)


def _dig(node: Any, *keys: str) -> Any:
    """入れ子の dict を鍵の順にたどる。途中で dict でないか鍵が無ければ None。"""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _missing_keys(obj: Any, part: str, keys: tuple[str, ...]) -> list[str]:
    held = _dig(obj, part)
    have = held if isinstance(held, dict) else {}
    return [f"{part}: 文脈の鍵 {k} が無い" for k in keys if k not in have]


def _validate_stage(stage: stages.StageDef, obj: Any, ref: str | None, keys: tuple[str, ...], part: str, what: str) -> list[str]:
    if ref is None:
        return [f"工程 {stage.name} に{what}の規約が無い"]
    return validate_io(ref, obj) + _missing_keys(obj, part, keys)


def validate_stage_output(stage: stages.StageDef, obj: Any) -> list[str]:
    """工程の出力を、工程の出力の規約と、工程が書く文脈の鍵（`outputs`）で確かめる。"""
    return _validate_stage(stage, obj, stage.output_ref, stage.outputs, "outputs", "出力")


def validate_stage_input(stage: stages.StageDef, obj: Any) -> list[str]:
    """工程の入力を、工程の入力の規約と、工程が読む文脈の鍵（`inputs`）で確かめる。"""
    return _validate_stage(stage, obj, stage.input_ref, stage.inputs, "context", "入力")


# --- 文書の検査 --------------------------------------------------------------------------


def check_dsl(doc: Any) -> list[str]:
    """1 つの文書が Serverless Workflow 1.0.3 の公式スキーマに合うかを確かめる。"""
    return _errors(_dsl_validator(), doc)


def _tasks(doc: Mapping[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for item in doc.get("do") if isinstance(doc.get("do"), list) else []:
        if isinstance(item, dict):
            yield from ((name, body) for name, body in item.items() if isinstance(body, dict))


def _ndf_meta(node: Mapping[str, Any]) -> Any:
    return _dig(node, "metadata", NAMESPACE)


def _context_keys() -> set[str]:
    return set(contracts.load_json(CONTEXT)["properties"])


def _context_key_errors(where: str, meta: dict[str, Any]) -> list[str]:
    """工程の inputs・outputs（配列のときだけ）の鍵が、スプリントの文脈の鍵にあるかを確かめる。"""
    known = _context_keys()
    errs = []
    for part in ("inputs", "outputs"):
        held = meta.get(part)
        if isinstance(held, list):
            errs += [f"{where}: {part} の {k} はスプリントの文脈の鍵に無い" for k in held if k not in known]
    return errs


def _meta_errors(where: str, meta: Any, level: str) -> list[str]:
    errs = [f"{where}: metadata.ndf {e}" for e in contracts.schema_errors(meta, f"{NDF_SCHEMA}#/$defs/{level}")]
    if level == "stage" and isinstance(meta, dict):
        errs += _context_key_errors(where, meta)
    return errs


def _task_errors(where: str, name: str, body: dict[str, Any], level: str) -> list[str]:
    errs = []
    if "if" in body:
        errs.append(f"{where}/{name}: 要素の if は使わない（条件は metadata.ndf.when に書く）")
    kinds = set(body) & _task_kinds()
    errs += [f"{where}/{name}: 要素 {k} は使わない（使えるのは {', '.join(sorted(TASKS[level]))}）" for k in sorted(kinds - TASKS[level])]
    if level == "stage":
        errs += _meta_errors(f"{where}/{name}", _ndf_meta(body), "step")
    if "listen" in body:
        errs += _listen_errors(f"{where}/{name}", body["listen"])
    return errs


def _listen_errors(where: str, listen: Any) -> list[str]:
    """`listen` の要素は 1 つの事象（`to.one`）を待ち、その種類は答えの事象（`LISTENED_EVENT`）である。"""
    kind = _dig(listen, "to", "one", "with", "type")
    if kind != LISTENED_EVENT:
        return [f"{where}: listen が待つのは事象 {LISTENED_EVENT} の 1 つ（to.one.with.type。渡された値 {kind}）"]
    return []


def _document(doc: Mapping[str, Any]) -> dict[str, Any]:
    held = _dig(doc, "document")
    return held if isinstance(held, dict) else {}


def check_vocabulary(doc: Mapping[str, Any], level: str) -> list[str]:
    """使う要素と `metadata.ndf` の語を閉じた一覧に限る（決定 5）。`level` は mode か stage。"""
    where = f"{level} {_document(doc).get('name')}"
    errs = _meta_errors(where, _ndf_meta(doc), level)
    namespace = _document(doc).get("namespace")
    if namespace != NAMESPACE:
        errs.append(f"{where}: document.namespace は {NAMESPACE}（渡された値 {namespace}）")
    for name, body in _tasks(doc):
        errs += _task_errors(where, name, body, level)
    return errs


def _endpoint(node: Mapping[str, Any], part: str) -> Any:
    return _dig(node, part, "schema", "resource", "endpoint")


def _ref_errors(where: str, ref: Any, what: str) -> list[str]:
    if not isinstance(ref, str):
        return [f"{where}: {what}の規約の参照が無い"]
    if ref_target(ref) is None:
        return [f"{where}: {what}の規約 {ref} の指す先が無い"]
    return []


def check_contracts(doc: Mapping[str, Any]) -> list[str]:
    """工程とステップのすべてが入出力の規約を参照し、参照の指す先があることを確かめる（I7）。"""
    where = f"stage {_document(doc).get('name')}"
    errs = _ref_errors(where, _endpoint(doc, "input"), "入力") + _ref_errors(where, _endpoint(doc, "output"), "出力")
    for name, body in _tasks(doc):
        errs += _ref_errors(f"{where}/{name}", _endpoint(body, "output"), "出力")
    return errs


# --- 組み立て ----------------------------------------------------------------------------


def _step_def(stage: str, name: str, body: dict[str, Any]) -> stages.StepDef:
    meta = _ndf_meta(body)
    return stages.StepDef(
        id=name,
        stage=stage,
        call=body["call"] if "call" in body else LISTEN_CALL,
        kind=meta["kind"],
        range=meta["range"],
        subject=meta.get("subject"),
        gate=meta.get("gate"),
        args=dict(body["with"]) if "with" in body else {},
        output_ref=_endpoint(body, "output"),
    )


def _in_mode(body: Mapping[str, Any], mode: str) -> bool:
    """ステップをモード `mode` で流すか（`metadata.ndf.modes` が無ければすべてのモード）。"""
    meta = _ndf_meta(body)
    return "modes" not in meta or mode in meta["modes"]


def _mode_errors(doc: Mapping[str, Any], known: Iterable[str]) -> list[str]:
    """ステップの `modes` が定義の無いモードを指さない。"""
    have = set(known)
    return [
        f"stage {doc['document']['name']} のステップ {n}: modes の {m} は定義の無いモード"
        for n, b in _tasks(doc)
        for m in (_ndf_meta(b)["modes"] if "modes" in _ndf_meta(b) else [])
        if m not in have
    ]


def _stage_def(doc: Mapping[str, Any], mode: str) -> stages.StageDef:
    meta = _ndf_meta(doc)
    name = meta["stage"]
    return stages.StageDef(
        id=doc["document"]["name"],
        name=name,
        phase=meta["phase"],
        steps=tuple(_step_def(name, n, b) for n, b in _tasks(doc) if _in_mode(b, mode)),
        when=tuple(meta["when"]) if "when" in meta else (),
        per_issue=bool(meta.get("per_issue")),
        inputs=tuple(meta["inputs"]) if "inputs" in meta else (),
        outputs=tuple(meta["outputs"]) if "outputs" in meta else (),
        input_ref=_endpoint(doc, "input"),
        output_ref=_endpoint(doc, "output"),
    )


def _called(mode_doc: Mapping[str, Any]) -> list[tuple[str, str]]:
    """モードの文書が呼ぶ（工程表の行名, 工程の文書の名前）の並び。"""
    out = []
    for name, body in _tasks(mode_doc):
        out.append((name, _dig(body, "run", "workflow", "name")))
    return out


def _stage_doc_errors(row: str, ref: Any, stage_docs: Mapping[str, Any]) -> list[str]:
    if ref not in stage_docs:
        return [f"mode の行 {row}: 呼ぶ工程の文書 {ref} が無い"]
    doc = stage_docs[ref]
    errs = [f"stage {ref}: {e}" for e in check_dsl(doc)]
    if errs:
        return errs
    errs = check_vocabulary(doc, "stage") + check_contracts(doc)
    stage_name = _ndf_meta(doc).get("stage") if isinstance(_ndf_meta(doc), dict) else None
    if not errs and stage_name != row:
        errs.append(f"mode の行 {row}: 工程の文書 {ref} の metadata.ndf.stage が {stage_name}（行名と同じにする）")
    return errs


def _digest(mode_doc: Mapping[str, Any], docs: list[Any]) -> str:
    text = json.dumps([mode_doc, *docs], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def build(mode_doc: Mapping[str, Any], stage_docs: Mapping[str, Any]) -> stages.Snapshot:
    """モードの文書と工程の文書から、検査を通したスナップショットを作る。通らなければ `WorkflowDefError`。"""
    errs = [f"mode: {e}" for e in check_dsl(mode_doc)]
    if errs:
        raise WorkflowDefError(errs)
    errs = check_vocabulary(mode_doc, "mode")
    called = _called(mode_doc)
    for row, ref in called:
        errs += _stage_doc_errors(row, ref, stage_docs)
    if errs:
        raise WorkflowDefError(errs)
    mode = mode_doc["document"]["name"]
    errs = [e for _, ref in called for e in _mode_errors(stage_docs[ref], [*modes(), mode])]
    if errs:
        raise WorkflowDefError(errs)
    defs = tuple(_stage_def(stage_docs[ref], mode) for _, ref in called)
    errs = (
        stages.check_unique_stage_names(defs)
        + stages.check_gate_conditions(defs)
        + stages.check_order([st for s in defs for st in s.steps])
    )
    if errs:
        raise WorkflowDefError(errs)
    doc = mode_doc["document"]
    digest = _digest(mode_doc, [stage_docs[ref] for _, ref in called])
    return stages.Snapshot(doc["name"], doc["version"], digest, _ndf_meta(mode_doc)["height"], defs)


def modes() -> list[str]:
    """定義のあるモードの名前（辞書順）。"""
    return sorted(p.stem for p in (contracts.contracts_dir() / MODES_DIR).glob("*.json"))


def _stage_docs() -> dict[str, Any]:
    return {p.stem: contracts.load_json(f"{STAGES_DIR}/{p.name}") for p in sorted((contracts.contracts_dir() / STAGES_DIR).glob("*.json"))}


def load_definition(mode: str) -> stages.Snapshot:
    """モードの文書と、それが呼ぶ工程の文書を読み、検査を通したスナップショットを返す。"""
    if mode not in modes():
        raise WorkflowDefError([f"モード {mode} の定義が無い（あるのは {', '.join(modes())}）"])
    return build(contracts.load_json(f"{MODES_DIR}/{mode}.json"), _stage_docs())


def mode_heights() -> dict[str, int]:
    """モード → 高さ。範囲の変更でモードを下げる操作を断るのに使う。"""
    return {m: _ndf_meta(contracts.load_json(f"{MODES_DIR}/{m}.json"))["height"] for m in modes()}
