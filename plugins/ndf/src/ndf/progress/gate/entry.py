"""サブコマンド `ndf answer`（`main`）と `ndf progress`（`progress_main`）のエントリポイント（`ndf.progress.gate` の部品）。

ndf answer <ステートマシン> --gate <質問の id> --result <回答> --reason <理由> --by <回答した者> --key <冪等のキー>
           [--notes <指摘ファイル>] [--head <見た head の SHA>] [--material-sha256 <見た承認資料の sha256>]
           [--body-sha256 <見た PR の本文の sha256>] [--title-sha256 <見た PR の題の sha256>]
           [--presentation <見た資料の提示の識別子>]
           [--drop <課題の番号>...]
ndf answer ask [--gate design|release]   # プランの中の承認ゲート（テンプレートの {{gate}}）。回答を待つ結果 JSON を返す
ndf answer list <ステートマシン>                 # 書いた回答（選択肢・指摘・理由・回答した者・時刻）を古い順に返す
ndf answer head <ステートマシン> [--gate release] # 承認で見た PR の head（マージの側が --expect-head へ渡して照らす。#2147）
ndf answer work --state-dir <保存先> [--cwd <作業場所>] <作業名>
                                         # 起動指示を <保存先>/<作業名>.prompt.md へ書いてから ndf worker run へ置き換わる。
                                         # 材料は工程か ndf plan next が書いた <保存先>/values.json から取り、--cwd を省くと作業の定義の cwd で動く
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from string import Template
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import claude_choice
from ndf.adapters.git import Git, GitError
from ndf.contracts import internal, result, worker_paths
from ndf.contracts.worker_kinds import (
    DELIVERY,
    PRODUCT_VERSION,
    kind_gaps,
    kind_values,
    released_of,
    values_in,
    work_dir_of,
    work_kinds,
)
from ndf.core import branch_strategy, route
from ndf.progress import command, invoke, model, run, store
from ndf.progress.gate.answering import (
    Answer,
    Observe,
    Post,
    answer,
    git_docs_only,
    github_observe,
    github_post,
    lane_env,
    no_post,
    recheck,
    reread,
    shared_hub,
)
from ndf.progress.gate.asking import (
    RELEASE_GATE,
    TOOL,
    Target,
    gate_result,
    present_material,
    question,
)
from ndf.progress.gate.records import answers, approved_head
from ndf.progress.gate.release import request_release, take_reply
from ndf.progress.plan import external


def present(
    st: store.Store, machine_id: str, cwd: Path, out: dict[str, Any], observe: Observe, env: Optional[Mapping[str, str]] = None
) -> dict[str, Any]:
    """`ndf progress` の結果（next・wait・work）が承認ゲート（`ndf.listen`）の待ちなら、質問・承認資料・回答ごとのルートの結果に
    差し替える。リリース承認ゲートならレーンのリリースのリクエストも出す（`request_release`）。"""
    v = run.read(st, machine_id)
    m = run.decide(v)
    if out["status"] != result.GATE or m.action != run.Action.GATE or m.step is None or not invoke.is_listen(m.step):
        return out
    try:
        repo: Optional[str] = Git(cwd).remote_repo()
    except GitError:
        repo = None
    # 対象（承認の対象と本番へ反映されるか）を承認資料の前に読み、承認資料と提示した対象に載せる（#4409 の決定 7）。
    # その head の資料があり、提示した対象も変わっていなければ、書き直さない（#3959）
    first = question(v, m, repo=repo)
    seen = observe(first)
    asked = replace(first, target=replace(seen, material=None))
    # 資料・そのハッシュ・提示の識別子（リリースのリクエストの ID に使う）は、同じ排他の中でまとめて受け取る
    path, sha, held = present_material(st.root, v, asked, private=st.private)
    q = replace(asked, target=replace(seen, material=sha, presentation=held.presentation))
    metrics = dict(out["metrics"]) if isinstance(out.get("metrics"), dict) else {}
    warned = list(out["warnings"]) if "warnings" in out else []
    try:
        sent = request_release(st, v, q, env if env is not None else os.environ)
    except (model.Refused, external.ExchangeError, OSError) as exc:
        sent, warned = None, [*warned, f"リリースのリクエストを出せなかった（人が ndf answer で回答する）: {exc}"]
    if sent is not None:
        metrics["release_request"] = sent
    return command.with_warnings(gate_result(q, metrics, path), before=warned)


# --- サブコマンド ndf answer ----------------------------------------------------------------------------


def ask(gate: Optional[str]) -> dict[str, Any]:
    """プランの中の承認ゲート（テンプレートの `{{gate}}`）: 回答を待つ結果 JSON。回答はプランのルートが受ける。

    回答の語（routes のキー）だけを返し、値は null（このステップは何も打たない）にする。このステップはステートマシンと工程を
    知らず、回答を書くコマンドはプランの実行（`ndf plan next ... --answer`）と工程の流れ（`ndf progress answer
    <ステートマシン> <工程>`）がそれぞれ自分の routes で組むためである。"""
    if gate is not None and gate not in model.table()["gates"]:
        raise model.Refused(f"承認ゲートの区分 {gate} は無い（{', '.join(model.table()['gates'])}）")
    label = model.gate_label(gate) if gate else "プランの中の承認ゲート"
    out: dict[str, Any] = {a: None for a in (run.APPROVED, run.REJECTED)}
    return result.result(TOOL, result.GATE, f"{label}の回答を待つ", metrics={"gate": {"kind": gate, "label": label}}, routes=out)


log = logging.getLogger(__name__)

# 起動指示の共通のテンプレート（コントラクトの保存先からの相対パス）。読む Skill と手順の抜粋はプラグインの根から引く
PROMPT_FILE = "worker/prompt.md"


class WorkUnready(Exception):
    """worker を起動する前に足りないもの（差し込みの値を渡す `values.json` が無い・読めない）。"""


def _known(kind: str) -> dict[str, Any]:
    """作業の定義（`contracts/worker/kinds.json`）の 1 件。定義に無い名前は `model.Refused`（`invalid`）。"""
    # 受ける作業名は作業の定義のキーだけ（テンプレートの `["{{worker}}", "<作業名>"]` の末尾）
    if kind not in work_kinds():
        raise model.Refused(f"作業名 {kind} はテンプレートの作業名（{', '.join(work_kinds())}）のどれでもない")
    return work_kinds()[kind]


def _shown(value: Any) -> str:
    """起動指示の入力の欄に載せる字面。JSON の値にし、値の中の改行や見出しが作業の指示の行にならないようにする。"""
    return json.dumps(value, ensure_ascii=False)


def _inputs_of(kind: str, saved: Mapping[str, Any]) -> str:
    """作業が読む値（`kinds.json` の `inputs`）を 1 行 1 件で並べる。欠けは呼び手が `kind_gaps` で先に確かめる。"""
    got = kind_values(kind, saved["values"], saved["inputs"])
    return "\n".join(f"- {name}: {_shown(value)}" for name, value in got.items())


def _skills_of(kind: str) -> str:
    """読む Skill の名前と `SKILL.md` の絶対パス（無ければ「無し」）。"""
    names = work_kinds()[kind]["skills"]
    base = identity.ROOT / "skills"
    return "\n".join(f"- {n}: {base / n / 'SKILL.md'}" for n in names) if names else "無し"


def _text_of(path: Path, label: str) -> str:
    """プラグインの中のファイルを読む。読めなければ `contracts.ContractError`（worker を起動しない）。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # ファイル: リリースの欠けは worker を起動せずに止める（`core/worker.py` と同じ方針）
        raise contracts.ContractError(f"{label} {path} を読めない: {exc.strerror or exc}") from None


def _steps_of(kind: str) -> str:
    """手順の抜粋（`kinds.json` の `excerpt` のファイルの中身）。抜粋を持たない作業は 1 行で返す。"""
    rel = work_kinds()[kind]["excerpt"]
    if not rel:
        return "手順の抜粋は無い。読む Skill と入力から判断して進める。"
    return _text_of(identity.ROOT / rel, f"作業 {kind} の手順の抜粋")


def _arg(value: Any) -> str:
    """コマンドの見本に差し込む値。シェルで 1 つの引数になるよう引用する（空白・`$`・引用符を含むパスでも割れない）。"""
    return shlex.quote(str(value))


def _outputs_of(kind: str, result_file: Path, version_number: bool = False) -> str:
    """結果ファイルに必ず入れる `outputs` のキーと `ndf contracts emit --outputs` の見本（無ければ「無し」の 1 行）。

    出力に `product_version` を持つ作業には、返す値の形を 1 行で書く。形は `version_number`（`_work_handoff` が決め、
    `work_argv` の `--require-version-number` と同じ値）に従う（#4513）。
    """
    names = work_kinds()[kind]["outputs"]
    if not names:
        return "結果ファイルに必ず入れる outputs: 無し"
    shown = _arg(json.dumps({n: "<値>" for n in names}, ensure_ascii=False))
    sample = f'ndf contracts emit --tool worker --status ok --summary "<1 行>" --outputs {shown} --out {_arg(result_file)}'
    lines = [f"結果ファイルに必ず入れる outputs: {'・'.join(names)}", f"見本: {sample}"]
    if PRODUCT_VERSION in names:
        lines.append(VERSION_NUMBER_LINE if version_number else DISTRIBUTION_ID_LINE)
    return "\n".join(lines)


# 起動指示に書く product_version の形の 1 行（#4513）
VERSION_NUMBER_LINE = "product_version はバージョン番号（例 1.2.3）"
DISTRIBUTION_ID_LINE = "product_version は出したものを指す空白だけでない 1 行の文字列（例 main abc1234）。バージョン番号を作らない"


def _version_aware(kind: str) -> bool:
    """作業の出力に `product_version` があるか（作業の定義の `outputs` で決め、作業の名前を比べない）。"""
    return PRODUCT_VERSION in work_kinds()[kind]["outputs"]


def _strategy_at(workdir: Path) -> Optional[branch_strategy.Strategy]:
    """作業場所のリポジトリのブランチ戦略の設定。読めなければ None（バージョン番号を持つ扱い）。

    根は `command.settings_root`（`git rev-parse --show-toplevel` を 1 回）で決める。作業場所（`--cwd` は任意の場所を指せる）に
    `.ndf/project.json` があっても根とはみなさない（#4513）。
    """
    try:
        return branch_strategy.load(command.settings_root(workdir))
    except (branch_strategy.StrategyError, GitError, OSError) as exc:  # 設定: 読めなければバージョン番号を持つ扱いで形を確かめる
        log.info("ブランチ戦略の設定を読めないのでバージョン番号を持つ扱いにする", extra={"workdir": str(workdir), "error": str(exc)})
        return None


def _needs_version_number(strategy: Optional[branch_strategy.Strategy]) -> bool:
    """設定が無いか、バージョンの保存先の種類が `none` でなければ、`product_version` をバージョン番号の形で確かめる。"""
    return strategy is None or strategy.has_version_number


def work_prompt(
    kind: str, saved: Mapping[str, Any], workdir: Path, paths: worker_paths.ReportPaths, *, version_number: bool = False
) -> str:
    """共通のテンプレート（`contracts/worker/prompt.md`）と作業の定義と `values.json` の中身から起動指示の本文を組む。

    差し込みの欠けは `substitute` が `KeyError` で止める（決定 6）。作業が読む値の内訳の欠けは 1 つの字面に隠れるので、
    呼び手が `kind_gaps` で名前ごとに先に確かめる。表示に使うパス（`report`・`done`・`result`）と、コマンドの見本に
    差し込む引用済みの値（`report_arg`・`result_arg`）を分ける。
    """
    spec = work_kinds()[kind]
    filled = {
        "work": spec["work"],
        "summary": spec["summary"],
        "inputs": _inputs_of(kind, saved),
        "outputs": _outputs_of(kind, paths.result, version_number),
        "workdir": str(workdir),
        "steps": _steps_of(kind),
        "skills": _skills_of(kind),
        "report": str(paths.report),
        "done": str(paths.done),
        "result": str(paths.result),
        "report_arg": _arg(paths.report),
        "result_arg": _arg(paths.result),
    }
    template = Template(_text_of(contracts.contracts_dir() / PROMPT_FILE, "起動指示のテンプレート"))
    try:
        return template.substitute(filled)
    except (KeyError, ValueError) as exc:
        raise contracts.ContractError(f"起動指示のテンプレート {PROMPT_FILE} の差し込みを埋められない: {exc}") from None


def _write_prompt(wd: worker_paths.WorkDir, paths: worker_paths.ReportPaths, body: str) -> None:
    """開いた保存先で報告の一式を初期化し、起動指示を `<保存先>/<作業名>.prompt.md` へ書く。

    初期化は `ndf worker prepare` と同じ `worker_paths.reset_report`（空の報告を作り、前の完了マーカーと結果ファイルを
    消す）で、起動ごとに行う。起動指示が指す `ndf worker append --done` は報告が無いと書けず、前の完了マーカーが
    残ると今回の報告より先に見えるためである。どの名前もリンクをたどらず、リンクなら何も変えずに `EntryUnsafe`
    で止める（`ndf worker run` が結果ファイルのリンクの先を消さない。#3877）。起動指示は初期化と同じ置き換えの中で
    報告より先に書き（`write`）、書けない・公開できないときは前の報告・完了マーカー・結果ファイルを残して止まる
    （拡張 ACL・所有者を保てないなど、書き込み可否の確かめに無い理由でも）。
    """
    worker_paths.reset_report(wd, paths, write=((paths.prompt.name, body),))


def work_argv(
    kind: str,
    state_dir: Path,
    cwd: Path,
    *,
    prompt_file: Optional[str] = None,
    result_dir_id: Optional[tuple[int, int]] = None,
    version_number: bool = False,
) -> list[str]:
    """作業名を `ndf worker run` の必須の引数へ合わせる。4 つのファイルの名前は `report_paths` が持つ（字面を持たない）。

    `prompt_file` は起動指示を読む場所（`ndf answer work` は継いだ記述子の `/dev/fd/<番号>` を渡す。省くと保存先の起動指示）。
    `result_dir_id` は開いた保存先の `(st_dev, st_ino)` で、`ndf worker run` が結果ファイルの保存先を開き直したときに突き合わせる。
    """
    spec = _known(kind)
    try:
        paths = worker_paths.report_paths(state_dir, kind, resolve=False)
    except worker_paths.NameInvalid as exc:
        raise model.Refused(str(exc)) from None
    files = ("--prompt-file", prompt_file or str(paths.prompt), "--result-file", str(paths.result))
    held = ("--result-dir-id", f"{result_dir_id[0]}:{result_dir_id[1]}") if result_dir_id is not None else ()
    required = [arg for name in spec["outputs"] for arg in ("--require-output", name)]
    shape = ["--require-version-number"] if version_number else []
    return ["run", "--kind", kind, *files, *held, "--cwd", str(cwd), "--state-dir", str(state_dir), *required, *shape]


def work_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} work")
    ap.add_argument("--state-dir", required=True, help="プランの状態の保存先（テンプレートの {state_dir}）")
    ap.add_argument("--cwd", help="worker が動く作業場所（省くと作業の定義の cwd が指す差し込みの値で動く）")
    ap.add_argument("kind", help="作業名（" + "・".join(work_kinds()) + "）")
    return ap


# `ndf answer work` が worker を起動する前に止まる型 → 停止理由の語（`KindInputsMissing` は `ContractError` より先に見る）
WORK_STOPS: tuple[tuple[type[Exception], str], ...] = (
    (model.Refused, "invalid"),
    (invoke.KindInputsMissing, "precondition"),
    (WorkUnready, "precondition"),
    (contracts.ContractError, "precondition"),
    (worker_paths.EntryError, "precondition"),
    (OSError, "precondition"),
)
WORK_STOP_TYPES = tuple(t for t, _ in WORK_STOPS)


def _work_dir(kind: str, saved: Mapping[str, Any], cwd: Optional[str]) -> Path:
    """worker が動く作業場所の絶対パス。`--cwd` があればそれ、無ければ作業の定義の `cwd` が指す差し込みの値。

    `ndf worker run` と同じく呼び出し元の場所で解く（`.resolve()`）。起動指示と argv に同じ絶対パスを載せ、移動した
    worker が相対パスを自分の場所から読み違えないようにする。
    """
    if cwd is not None:
        return Path(cwd).resolve()
    found = work_dir_of(kind, saved["values"])
    if found is None:  # `kind_gaps` が空なら決まっているので、ここへは来ない
        raise WorkUnready(f"作業 {kind} の作業場所（差し込み {work_kinds()[kind]['cwd']}）が {invoke.VALUES_FILE} に無い")
    return Path(found).resolve()


class Released(NamedTuple):
    """worker を起動せずに終えるときの結果 JSON（`ok` か `stopped`。#4513）。`_cmd_work` がそのまま出す。"""

    outcome: dict[str, Any]


def released_outcome(work: str, saved: Mapping[str, Any], strategy: Optional[branch_strategy.Strategy]) -> Optional[dict[str, Any]]:
    """記録済みの値で、worker を起動せずに返す結果 JSON（`ok` か `stopped`）。起動するときは None（#4513）。

    作業の出力が `product_version` と `delivery` を持ち（今は `release`）、記録済みの値があるときだけ、承認の時点の保存先の種類で
    決める。`none` ならオーガナイザーが出し終えているので、記録した届け先があれば ok、無ければ止まる。`unknown` も止まる。
    `versioned` は起動する。種類が記録に無い（この変更の前に承認されたか、外のオーガナイザーが書かなかった）ときだけ、作業の時点の設定 `strategy` で決める。
    届け先は今の設定から選び直さない（設定は後から直せるので、過去の届け先の根拠にならない）。
    """
    outputs = work_kinds()[work]["outputs"]
    recorded = released_of(saved)
    if recorded is None or PRODUCT_VERSION not in outputs or DELIVERY not in outputs:
        return None
    if recorded.with_number:
        return None
    unknown = recorded.store_unknown
    if recorded.store is None and _needs_version_number(strategy):
        return None
    if not unknown and recorded.delivery is not None:
        out = {PRODUCT_VERSION: recorded.version, DELIVERY: dict(recorded.delivery)}
        summary = f"作業 {work} はオーガナイザーが出した値で終えた（worker を起動しない。バージョン番号を持たないリポジトリ）"
        return result.result(TOOL, result.OK, summary, outputs=out)
    if unknown:
        why = "承認を記録した時点で設定を読めず"
    elif recorded.store is None:
        why = "承認の記録に届け先が無く"
    else:
        why = "承認を記録した時点で本番の届け先が 1 つに決まらず"
    detail = (
        f"オーガナイザーが出した後だが、{why}、このリリースの届け先が記録から確かめられない（worker を起動しない）。"
        "設定を直して retry しても終わらない。次の操作: 実際の届け先を確かめ、次の JSON をファイルに書き、"
        "ndf progress finish <ID> デプロイ --output <JSONファイル> --reason <理由> で工程を終える: "
        f"{json.dumps(manual_finish_example(recorded.version), ensure_ascii=False)}。{MANUAL_FINISH_FILL}"
        "finish は書く前に同じ確かめをし、合わなければ何も書かずに断る。書かずに形だけ確かめたいときは、"
        "代わりに ndf progress check <ID> デプロイ --output <JSONファイル> を打てる"
    )
    return result.stopped(TOOL, "precondition", detail)


# 手で終える出力の例（`manual_finish_example`）の置き換える所の説明。SSOT はここで、詳細設計書は写さずにここを指す
MANUAL_FINISH_FILL = (
    "outputs.delivery は、実際にリリースした先に対応する届け先のオブジェクト全体で置き換える"
    "（設定 project.json の branch_strategy.release.deliveries の 1 行と同じ形）。"
    "outputs.mvv_gate は、ndf progress show <ID> の metrics.history で payload.step が release-mvv の最後の記録から、"
    "payload.report.outputs.mvv_gate をそのままコピーする。"
)


def manual_finish_example(version: str) -> dict[str, Any]:
    """届け先が記録から決まらずに止まったデプロイの工程を、`ndf progress finish` で手で終える出力の例（#4513）。

    工程の出力の規約（`stage-io.schema.json` の `stage-out`）が必須とする `summary`・`steps` と、デプロイの工程の `outputs` の
    3 つ（`product_version`・`delivery`・`mvv_gate`）を持つ。`product_version` は記録済みのリリースの識別子を入れる。`delivery` と
    `mvv_gate` は値全体を置き換える目印の文字列にする（届け先の一部だけを固定値で示すと、利用者が実際と違う属性を残しうる）。
    `finish` は文脈にある値で出力の欠けを補わないので、記録済みの `mvv_gate` も入れる。
    """
    return {
        "summary": "届け先を確かめてデプロイの工程を手で終えた",
        "steps": [],
        "outputs": {
            PRODUCT_VERSION: version,
            DELIVERY: "<実際にリリースした先に対応する届け先のオブジェクト>",
            "mvv_gate": "<記録済みの mvv_gate のオブジェクト>",
        },
    }


class WorkHandoff(NamedTuple):
    """`ndf worker run` へ渡すもの。保存先は開いて解いた場所（`state_dir`）とその `(st_dev, st_ino)`（`dir_id`）。

    `fd` は起動指示の本文を持つ継ぐ記述子（`_held_prompt`）で、閉じるのは受け取った側である。
    """

    kind: str
    state_dir: Path
    dir_id: tuple[int, int]
    workdir: Path
    prompt: str
    fd: int
    mode: Optional[str] = None  # 工程の入力のモード（#2944）
    # 起動指示に書いた product_version の形。`work_argv` の `--require-version-number` へ同じ値で渡す（#4513）
    version_number: bool = False


def _work_handoff(kind: str, state_dir: Path, cwd: Optional[str]) -> WorkHandoff | Released:
    """作業の名前と差し込みの値を確かめ、作業場所を決め、起動指示を書いて、`ndf worker run` へ渡すものを返す。

    保存先は祖先のリンクを解きながら 1 回だけ開き（`open_resolved`。macOS の `/tmp`・`/var` を経る保存先も開く。`..` が打ち消す
    要素がリンクなら止める）、`values.json` の読み取り（`values_in`）と報告の初期化・起動指示の書き込みを同じ記述子で
    行う。読んだ場所と書く場所が食い違わない。記録済みの値で終える作業は、起動指示も報告の初期化もせずに `Released` を返す
    （#4513）。起動指示を継ぐ記述子（`_held_prompt`）は初期化より先に用意し、用意できなければ
    前の報告一式を残して止まる。モデルと effort の値の誤り（`claude_choice.precheck_values`。`precondition`）も初期化の前に
    確かめ、誤りなら前の報告一式を残して止まる（#2944）。起動指示とコマンドの見本のパスは解いた場所で組む（worker の
    `ndf worker append`・`ndf contracts emit --out` がリンクを経ずに開ける）。
    """
    _known(kind)
    worker_paths.report_paths(state_dir, kind, resolve=False)  # 名前の形を開く前に確かめる
    with worker_paths.open_resolved(state_dir) as wd:
        paths = worker_paths.report_paths(wd.path, kind, resolve=False)
        saved = values_in(wd)
        if saved is None:
            raise WorkUnready(
                f"保存先 {state_dir} に差し込みの値（{invoke.VALUES_FILE}）が無い（工程が組むか、ndf route render・run で組んだプランを"
                " ndf plan next で流すと書く）"
            )
        gaps = kind_gaps(kind, saved["values"], saved["inputs"], sources=invoke.FILL_SOURCES, covers=route.LAUNCH_SOURCES)
        if gaps:
            raise invoke.KindInputsMissing(saved["plan"], gaps)
        workdir = _work_dir(kind, saved, cwd)
        recorded = released_of(saved)
        aware = _version_aware(kind)
        # 承認の時点の種類が記録にあれば、作業の時点の設定で決め直さない（読まない）
        strategy = _strategy_at(workdir) if aware and (recorded is None or recorded.store is None) else None
        finished = released_outcome(kind, saved, strategy) if aware else None
        if finished is not None:
            return Released(finished)
        if recorded is not None and recorded.store is not None:
            version_number = aware and recorded.with_number
        else:
            version_number = aware and _needs_version_number(strategy)
        # モデルと effort の値の誤り（`ndf worker run` が起動の前に止まるもの）を、前の報告一式を初期化する前に確かめる（#2944）
        retry = os.environ.get(identity.env_name("retry-cause"))
        claude_choice.precheck_values(cwd=workdir, retry=None if retry == "" else retry)
        body = work_prompt(kind, saved, workdir, paths, version_number=version_number)
        fd = _held_prompt(body)  # 前の報告一式を初期化する前に用意する（用意できなければ何も消さずに止まる）
        try:
            _write_prompt(wd, paths, body)
            given = saved["inputs"].get("mode")
            mode = given if isinstance(given, str) else None
            return WorkHandoff(kind, wd.path, worker_paths.dir_id(wd), workdir, body, fd, mode=mode, version_number=version_number)
        except BaseException:
            os.close(fd)
            raise


def _anonymous_fd() -> int:
    """名前の無い私有のファイルの記述子。`memfd_create` が無いか拒まれれば、名前をすぐ消す一時ファイルへ落とす。"""
    if hasattr(os, "memfd_create"):
        try:
            return os.memfd_create("ndf-prompt", 0)
        except OSError as exc:  # 環境: seccomp などで拒まれたら一時ファイルへ落とす（属性の有無では使えるか分からない）
            log.info("memfd_create を使えないので一時ファイルで起動指示を渡す", extra={"error": str(exc)})
    with tempfile.TemporaryFile() as f:
        return os.dup(f.fileno())


def _held_prompt(body: str) -> int:
    """起動指示の本文を、名前の無い私有のファイルへ書いて先頭へ戻し、置き換えた先へ継ぐ記述子を返す。

    `ndf worker run` は `--prompt-file /dev/fd/<番号>` でこれを読む。共有の保存先の起動指示を読み直さないので、書いた後に
    別の利用者が起動指示を差し替えても、worker へ渡る指示は確かめた `values.json` から組んだ本文のままである。
    Linux は `memfd_create`、ほかと、コンテナの seccomp などで `memfd_create` が拒まれる環境は作ってすぐ名前を消す
    一時ファイル（`tempfile.TemporaryFile`）を使う。呼び手は前の報告一式を初期化する前にこれを呼ぶ。
    """
    fd = _anonymous_fd()
    try:
        view = memoryview(body.encode("utf-8"))
        while view:
            view = view[os.write(fd, view) :]
        os.lseek(fd, 0, os.SEEK_SET)
        os.set_inheritable(fd, True)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _cmd_work(argv: list[str], exec_: Callable[[str, list[str]], Any] = os.execv) -> int:
    """起動指示を書いてから `ndf worker run` を同じプロセスの置き換えで打つ（worker の層は読まない。IG1）。

    起動指示の本文は継いだ記述子で渡し（`_held_prompt`）、結果ファイルの保存先は開いた保存先の識別子（`--result-dir-id`）で
    `ndf worker run` に突き合わせさせる（共有の保存先を、確かめた後に差し替えられても別の指示・別の保存先で動かない）。
    """
    a = work_parser().parse_args(argv)
    try:
        handoff = _work_handoff(a.kind, Path(a.state_dir), a.cwd)
    except WORK_STOP_TYPES as exc:
        kind = next(c for t, c in WORK_STOPS if isinstance(exc, t))
        items = exc.items if isinstance(exc, invoke.KindInputsMissing) else []
        return result.emit(result.stopped(TOOL, kind, str(exc), items=items))
    except internal.InternalError as exc:  # モデルと effort の値の誤り（`claude_choice.precheck_values`）。前の報告一式は残っている
        return result.emit(result.stopped(TOOL, exc.kind, str(exc)))
    if isinstance(handoff, Released):
        log.info("worker を起動せずに作業を終えた", extra={"kind": a.kind, "status": handoff.outcome["status"]})
        return result.emit(handoff.outcome)
    fd = handoff.fd
    try:
        args = work_argv(
            handoff.kind,
            handoff.state_dir,
            handoff.workdir,
            prompt_file=f"/dev/fd/{fd}",
            result_dir_id=handoff.dir_id,
            version_number=handoff.version_number,
        )
        if handoff.mode:  # 工程の入力のモードは `--mode` で渡す（使用量ログと実験の記録に残す。#2944）
            args += ["--mode", handoff.mode]
        # -I: 作業場所と呼び出し元の PYTHONPATH の ndf.py・ndf/ を NDF の src より先に読まない。置き換えた先も、呼び出し元が読む
        # 今の標準出力・標準エラーの文字コードで書く（プランのステップはロケールで読む。#4029）
        argv = invoke.ndf_command("worker", *args, keep_streams=True)
        exec_(argv[0], argv)
    finally:
        os.close(fd)  # 置き換えに落ちたとき（と置き換えを差し替えたテスト）だけここへ来る
    return 0


def _cmd_give(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    seen = Target(a.head, a.material_sha256, presentation=a.presentation, body=a.body_sha256, title=a.title_sha256)
    given = Answer(a.gate, a.result, a.reason, a.by, a.key, Path(a.notes) if a.notes else None, seen, tuple(a.drop))
    hub = shared_hub(cwd)
    return answer(st, a.machine_id, given, github_observe(cwd, st.root, hub), github_post(cwd, hub))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("machine_id", help="回答するステートマシンの ID")
    ap.add_argument("--gate", required=True, help="質問の id（承認ゲートの結果の metrics.gate.id）")
    ap.add_argument("--result", required=True, help="回答の語（承認ゲートの結果の routes のキー）")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--by", required=True, help="回答した者（human・conductor・organizer か service:<主体>）")
    ap.add_argument("--key", required=True, help="冪等のキー（同じキーの回答し直しは書かない）")
    ap.add_argument("--notes", help="変更要求（request_changes）の指摘ファイル")
    ap.add_argument("--head", help="見た PR の head の SHA")
    ap.add_argument("--material-sha256", help="見た承認資料の sha256")
    ap.add_argument("--body-sha256", help="見た PR の本文の sha256（#4446）")
    ap.add_argument("--title-sha256", help="見た PR の題の sha256（承認の質問の文は題から作る。#4446）")
    ap.add_argument(
        "--presentation",
        help="利用者に示した承認資料を識別する値。提示時に承認対象の PR が特定され、routes にこの値が入っている場合は必須。提示時のコマンドから値をそのまま渡す（資料を出し直すと変わる）",
    )
    ap.add_argument("--drop", type=int, nargs="*", default=[], help="設計承認ゲートで外す課題の番号")
    return ap


def _cmd_head(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    head = approved_head(run.read(st, a.machine_id), a.gate, st.root)
    if head is None:
        raise model.Refused(f"ステートマシン {a.machine_id} に、{model.gate_label(a.gate)}で head を見て承認した回答が無い")
    summary = f"{model.gate_label(a.gate)}で承認済みのコミットは {head}"
    return result.result(TOOL, result.OK, summary, metrics={"machine_id": a.machine_id, "gate": a.gate, "head": head})


def head_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} head")
    ap.add_argument("machine_id")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]), default=RELEASE_GATE)
    return ap


def _cmd_list(a: argparse.Namespace, _cwd: Path, st: store.Store) -> dict[str, Any]:
    got = answers(run.read(st, a.machine_id))
    return result.result(
        TOOL, result.OK, f"ステートマシン {a.machine_id} の回答 {len(got)} 件", items=got, metrics={"machine_id": a.machine_id}
    )


def list_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} list")
    ap.add_argument("machine_id")
    return ap


def ask_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} ask")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]))
    return ap


def _progress(a: argparse.Namespace, cwd: Path, st: store.Store, observe: Optional[Observe] = None) -> dict[str, Any]:
    """`ndf progress` の工程を流すサブコマンド。レーンの行を終える `Env` で流し、回答を読む前に対象を見直し、承認ゲートの
    待ちは質問と回答ごとのルートの結果にする。"""
    hub = shared_hub(cwd)
    seen = observe if observe is not None else github_observe(cwd, st.root, hub)
    took = _took(st, a.machine_id, seen, github_post(cwd, hub) if observe is None else no_post)
    why = recheck(st, a.machine_id, seen, git_docs_only(cwd))
    out = run.COMMANDS[a.action](run.with_env(a, lane_env(run.Env(facts=command.facts_of))), cwd, st)
    return command.with_warnings(present(st, a.machine_id, cwd, out, seen), before=[w for w in (took, why) if w])


def _took(st: store.Store, machine_id: str, observe: Observe, post: Post = no_post) -> Optional[str]:
    """リリースのリクエストへの回答を承認ゲートの回答として書く。書けなければ理由を警告に返す（待ちは残る）。"""
    try:
        got = take_reply(st, machine_id, os.environ, observe, post)
    except (model.Refused, external.ExchangeError) as exc:
        return f"リリースのリクエストへの回答を承認ゲートの回答にできなかった: {exc}"
    return got["summary"] if got is not None else None


def _cmd_reread(a: argparse.Namespace, cwd: Path, st: store.Store, observe: Optional[Observe] = None) -> dict[str, Any]:
    """`ndf progress reread <ID>`: 承認の前の確認をやり直し、承認ゲートの待ちを今の対象（head・本文・題）で出し直す（#4446）。"""
    seen = observe if observe is not None else github_observe(cwd, st.root)
    out = reread(st, a.machine_id, cwd, lane_env(run.Env(facts=command.facts_of)))
    return present(st, a.machine_id, cwd, out, seen)


def reread_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} progress reread", description="承認の前の確認をやり直し、承認ゲートの待ちを出し直す")
    ap.add_argument("machine_id", help="承認ゲートで人の回答を待っているスプリントのステートマシンの ID")
    return ap


Dispatch = Callable[[argparse.Namespace, Path], dict[str, Any]]
# `ndf answer` のサブコマンドの名前 → （引数の解析器を作る関数, 受ける関数）。`work` は exec するので表の外で受ける
ANSWER_VERBS: dict[str, tuple[Callable[[], argparse.ArgumentParser], Dispatch]] = {
    "ask": (ask_parser, lambda a, _cwd: ask(a.gate)),
    "head": (head_parser, lambda a, cwd: command.with_store(cwd, _cmd_head, a)),
    "list": (list_parser, lambda a, cwd: command.with_store(cwd, _cmd_list, a)),
}


def main(argv: list[str]) -> int:
    """サブコマンド `ndf answer`（サブコマンドの表の `answer` の行がこのパッケージを指す）。

    `ask` はプランの中の承認ゲート、`list` は書いた回答、`head` は承認で見た head、`work` は作業名から `ndf worker run` を打つ、
    ステートマシンの ID で始まれば承認ゲートへの回答。`ndf progress` は `progress_main` が受ける（`work` の名前が両方にあるため、
    エントリポイントのサブコマンドで分ける）
    """
    if argv and argv[0] in ANSWER_VERBS:
        make, dispatch = ANSWER_VERBS[argv[0]]
        return command.run_main(make(), dispatch, argv[1:])
    if argv and argv[0] == "work":
        return _cmd_work(argv[1:])
    return command.run_main(parser(), lambda a, cwd: command.with_store(cwd, _cmd_give, a), argv)


def progress_main(argv: list[str]) -> int:
    """サブコマンド `ndf progress`（サブコマンドの表の `progress` の行が `ndf.progress.gate.progress_cli` を指す）。

    `reread` は承認ゲートの待ちの出し直し（#4446）。工程を流すサブコマンド（next・wait・answer・resync・work・show）は、スプリントの完了と同じトランザクションでレーンの行を
    終える `Env`（`lane_env`。決定 17）で流す。ほか（new・disable など）は `command` へ渡す。P1b（`run`）はレーンを読まない
    """
    if argv and argv[0] in run.COMMANDS:
        return command.run_main(run.parser(), lambda a, cwd: command.with_store(cwd, _progress, a), argv)
    if argv and argv[0] == "reread":
        return command.run_main(reread_parser(), lambda a, cwd: command.with_store(cwd, _cmd_reread, a), argv[1:])
    return command.main(argv)
