"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017）。

工程の定義で承認ゲート（設計・リリース）の直前に置く判定のステップの本体。進め方が fast・auto のときだけ、
MVV 判定の前の機械の検査を当て、通れば LLM のアダプター（`ndf.adapters.llm`）で判定を打ち（`mvv.vet_sprint`。同じ材料の
「従う」は記録を再利用する）、承認ゲートを省けるか（`ndf.progress.gate.checks.may_skip`）を出力の `mvv_gate` に書く。
承認ゲートのステップ（`ndf.progress.run`）はその結果だけを読み、省けるなら答えた者 `service:mvv` の承認で通す。

判定の材料は、共通原則の本文（`contracts/data/ndf-common-principles.md`）・プロジェクト MVV の本文・PR の本文と差分。
レッドライン（必ず人の承認が要る操作）は共通原則とプロジェクト MVV の表から引き、材料の先に置く。
判定を打てない（PR が無い・GitHub や LLM が答えない）ときは承認ゲートを省かず、理由を `why` に書いて人へ問う。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ndf import contracts, identity
from ndf.adapters import llm
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError
from ndf.contracts import limits
from ndf.domains.records import files, jsonfile, mvv, preflight
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress.gate.checks import MvvFacts, may_skip, refusals
from ndf.progress.model import MVV_PACES

# 共通原則の本文（契約の置き場からの相対）と、判定の答えのスキーマ（contracts/schemas/mvv-verdict.schema.json）
PRINCIPLES = "data/ndf-common-principles.md"
VERDICT_SCHEMA = "mvv-verdict"
# 判定の 1 回の呼び出しの上限
JUDGE_LIMIT = "supervise.judge-step"
# 承認ゲートの区分（設計は作業場所のブランチの PR、リリースは文脈の pr を判定する）
DESIGN, RELEASE = "design", "release"
# 判定の 1 回の呼び出し（問い・材料・答えのスキーマ・上限の秒 → 答え）。テストはスタブへ差し替える
Ask = Callable[[str, str, dict, float], llm.JudgeAnswer]


class GateError(Exception):
    """判定を打てない（PR が無い・LLM が答えない）。承認ゲートは省かずに人へ問う。"""


def principles() -> str:
    """共通原則の本文。"""
    return (contracts.contracts_dir() / PRINCIPLES).read_text(encoding="utf-8")


def boundary_paths(root: Path) -> tuple[str, ...]:
    """進め方の宣言のレッドラインのパス（宣言が無ければ空。読めなければ `jsonfile.JsonFileError`）。"""
    path = files.path(root, "pace")
    decl = jsonfile.read_object(path) if path.is_file() else {}
    held = decl["boundary_paths"] if isinstance(decl.get("boundary_paths"), list) else []
    return tuple(p for p in held if isinstance(p, str))


def base_facts(root: Path, inputs: Mapping[str, Any]) -> tuple[MvvFacts, str]:
    """機械の検査の事実（PR の本文と変えたファイルを除く）と、プロジェクト MVV の本文。

    MVV が承認済みでなければ宣言と本文を読まない（検査 1 が止める）。
    """
    state = preflight.mvv_state(root)
    ref = inputs["mvv_ref"] if isinstance(inputs.get("mvv_ref"), str) else None
    paths = boundary_paths(root)
    if state != md.State.APPROVED:
        return MvvFacts(state, None, None, ref, str(inputs.get("mode")), boundary_paths=paths), ""
    decl = jsonfile.read_object(files.path(root, "mvv"))
    last = md.latest(decl)
    approved = last["sha256"] if last is not None else None
    body = preflight.mvv_body(root, decl)
    held = MvvFacts(state, preflight.sha256_of(body), approved, ref, str(inputs.get("mode")), boundary_paths=paths)
    return held, body.read_text(encoding="utf-8")


def pr_number(root: Path, gate: str, inputs: Mapping[str, Any], hub: GitHub) -> int:
    """判定する PR の番号。リリースは文脈の `pr`、設計は作業場所のブランチの開いた PR。無ければ `GateError`。"""
    if gate == RELEASE:
        if not isinstance(inputs.get("pr"), int):
            raise GateError("文脈にスプリントの PR の番号（pr）が無い")
        return int(inputs["pr"])
    branch = Git(root).run("rev-parse", "--abbrev-ref", "HEAD").strip()
    opened = [p for p in hub.pulls_by_head([branch])[branch] if p.state == "open"]
    if not opened:
        raise GateError(f"ブランチ {branch} の開いた設計の PR が無い")
    return opened[0].number


def judge_with(ask: Ask) -> mvv.Judge:
    """`mvv.vet_sprint` へ渡す判定の関数。答えの判定の語と、費用・判定の中身（理由・レッドライン・根拠）を返す。"""
    schema = contracts.load_json(contracts.schema_path(VERDICT_SCHEMA))

    def judge(inputs: Mapping[str, str]) -> tuple[str, Mapping[str, Any]]:
        got = ask(inputs["question"], inputs["material"], schema, limits.seconds(JUDGE_LIMIT))
        if not got.ok:
            raise GateError(f"MVV 判定の答えが無い: {got.cause}")
        return str(got.answer["verdict"]), {"usd": got.cost, **{k: got.answer[k] for k in md.VERDICT_DETAILS}}

    return judge


def _verdict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"verdict": row["verdict"], **{k: row[k] for k in md.VERDICT_DETAILS if k in row}}


def _judged(root: Path, gate_name: str, inputs: Mapping[str, Any], hub: GitHub, ask: Ask) -> dict[str, Any]:
    facts, project = base_facts(root, inputs)
    why = refusals(facts)
    if why:
        return mm.gate_outcome(gate_name, None, False, why)
    number = pr_number(root, gate_name, inputs, hub)
    rows = mvv.pr_files(hub, number)
    facts = mm.facts_from_pr(facts, hub.pull(number).body, mvv.changed_of(rows))
    materials = {
        "NDF の共通原則": principles(),
        "プロジェクト MVV": project,
        f"Pull Request #{number} の本文": facts.body if facts.body is not None else "",
        "変えたファイルと差分": mm.patch_material(rows),
    }
    text = mm.prompt(mm.redlines(principles(), project), materials)
    verdict = _verdict(mvv.vet_sprint(root, {"gate": gate_name, "question": mm.SYSTEM, "material": text}, judge_with(ask)))
    skip, why = may_skip(facts, verdict)
    return mm.gate_outcome(gate_name, verdict, skip, why)


def gate(root: Path, gate_name: str, inputs: Mapping[str, Any], hub: Callable[[Path], GitHub], ask: Ask = llm.ask) -> dict[str, Any]:
    """承認ゲートを省けるかを判定し、出力 `mvv_gate` の値を返す（`mm.gate_outcome`）。進め方が fast・auto でなければ判定しない。"""
    if inputs.get("pace") not in MVV_PACES:
        return mm.gate_outcome(gate_name, None, False, [f"進め方 {inputs.get('pace')} は承認ゲートを MVV 判定で省かない"])
    try:
        return _judged(root, gate_name, inputs, hub(root), ask)
    except (GateError, GitError, GitHubError, mvv.MvvError, jsonfile.JsonFileError, identity.IdentityError, OSError) as exc:
        return mm.gate_outcome(gate_name, None, False, [f"MVV 判定を打てない: {exc}"])
