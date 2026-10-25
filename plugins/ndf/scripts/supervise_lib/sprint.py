"""new sprint / close の組み立てと、pace: fast / auto と MVV の拒否の判定（#1142 の C1・#1370）。"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import project_mvv
from pace import PaceError
from step_result import result
from supervise_lib import limits, sprint_inputs
from supervise_lib.decl import decl_roots, with_decls
from supervise_lib.sprint_waves import (
    sprint_branch,
    plan_fast_check,
    plan_fast_impl,
    plan_sprint_branch,
    plan_sprint_check,
    plan_sprint_design,
    plan_sprint_impl,
    plan_sprint_release,
    plan_advise_design,
    design_read,
    plan_design_results,
    plan_mvv_design,
    plan_mvv_release,
)
from supervise_lib.new_args import NEW_ARGS
from supervise_lib.paths import CHECK_PY, HERE, SELF
from supervise_lib.procedures import sprint_out, with_record
from supervise_lib.release_templates import release_verify_step
from supervise_lib.sprint_routes import (
    MVV_PACES,
    dev_channel_of,
    has_record,
    has_release_template,
    pace_decl,
    record_pr_ref,
    route_rows,
    route_waves,
)
from supervise_lib.verify_steps import merge_steps
from supervise_lib.sprint_close_steps import _close_step, _retro_refine_steps, _spec_pr_steps


def prod_version(version: str) -> str:
    return re.sub(r"-.*$", "", version)


DESIGN_RESULTS_STAGE = "設計の結果"


def design_results_wave(a, repo: str, **then) -> list[dict]:
    """承認ゲート 1 の直後の「設計の結果」のステージ（決定 2）。設計の課題も設計を承認済みの課題も無ければ置かない。"""
    if not design_read(a):
        return []
    return [{"name": DESIGN_RESULTS_STAGE, "plans": {"design-results": plan_design_results(a, repo)}, **then}]


def mvv_design_waves(a, repo: str, gate: str) -> list[dict]:
    """MVV 判定つきの設計のステージと、その後の関門 1。設計の課題が無ければ空。"""
    if not a.design:
        return []
    return [
        {"name": "設計", "plans": {f"design-{n}": plan_mvv_design(a, n, repo) for n in a.design}},
        {"name": "関門 1", "gate": gate},
    ]


def fast_sprint_plans(a) -> list[dict]:
    """pace: fast のスプリントのステージ。スプリントブランチを作らず、実装は起点のブランチへ直接入れる。
    実装の queue が --then のステージで 検査（実行の条件）→ 実装レビュー（開発版ごと）→ 開発版 → 本番（先頭が MVV 判定）を
    順に流す。検査が立てばその中でレビューも通るため、実装レビューのステージは範囲が空になり流れない。"""
    repo = str(Path(a.worktree).resolve())
    waves = mvv_design_waves(
        a, repo, "設計の計画がすべて完了なら通過する。結果が関門の計画の Pull Request だけ、利用者の承認を取ってマージする"
    )
    # 設計の課題があれば、設計の結果のステージが実装の queue の先頭になり、実装以降はその --then で続く
    waves += design_results_wave(a, repo)
    first = DESIGN_RESULTS_STAGE if design_read(a) else "実装"
    impl = {"name": "実装", "plans": {f"impl-{n}": plan_fast_impl(a, n, repo) for n in a.issue}}
    if design_read(a):
        impl["then_of"] = first
    waves += [
        impl,
        {"name": "検査", "plans": {"check": plan_fast_check(a, repo, f"{a.name}-1")}, "then_of": first},
        {"name": "実装レビュー", "plans": {"review": plan_fast_check(a, repo, f"{a.name}-review", review_only=True)}, "then_of": first},
    ]
    if not has_release_template(a):
        return waves + route_waves(a, repo, first, mvv=a.state)  # 開発版のステージは置かない（#1336 の決定 12）
    waves += [
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev")}, "then_of": first},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, prod_version(a.version), "prod")}, "then_of": first},
    ]
    return waves


AUTO_GATE_1 = (
    "設計のプランの MVV 判定が通せばマージ済みで、このステージは通過する。設計のプランが関門を返したら、承認を取り "
    '`sprint-state.py gate <状態> "関門 1" --by user` と `run <プラン> --from approve` の後に次のステージの resume を打つ'
)


def auto_sprint_plans(a) -> list[dict]:
    """pace: auto のスプリントのステージ。並びは normal と同じ（設計 → 関門 1 → スプリントブランチ → 実装 → 検査）で、
    後ろに開発版と本番（先頭が MVV 判定の関門 2）を続ける。設計と開発版・本番だけを MVV 判定つきのプランで作り、
    スプリントブランチ以降を最初のステージの --then で 1 本の queue に流す（#1370 の決定 3）。check-trigger.py は通らない。"""
    repo = str(Path(a.worktree).resolve())
    waves = mvv_design_waves(a, repo, AUTO_GATE_1)
    # 設計の課題が無くても設計を承認済みの課題があれば、設計の結果のステージが queue の先頭になる
    first = "設計" if a.design else DESIGN_RESULTS_STAGE if design_read(a) else "スプリントブランチ"
    then = {"then_of": first} if design_read(a) else {}
    impl = {}
    for n in a.issue:
        plan = plan_sprint_impl(a, n, repo)
        plan["進め方"] = "auto"  # 課題の本文の見出し行と通過記録へ進め方を書く（supervise_lib/state.py）
        impl[f"impl-{n}"] = plan
    waves += design_results_wave(a, repo, **({"then_of": first} if a.design else {}))
    waves += [
        {"name": "スプリントブランチ", "plans": {"sprint-branch": plan_sprint_branch(a, repo)}, **then},
        {"name": "実装", "plans": impl, "then_of": first},
        {"name": "検査", "plans": {"check": plan_sprint_check(a, repo)}, "then_of": first},
    ]
    if not has_release_template(a):
        return waves + route_waves(a, repo, first, mvv=a.state)
    prs = ["{queue_pr:check}"]  # 出す版の PR は検査のステージのスプリントの PR（関門 2 の判定のコメントの宛先）
    return waves + [
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev", prs=prs)}, "then_of": first},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, prod_version(a.version), "prod", prs=prs)}, "then_of": first},
    ]


def close_plan(a, repo: str) -> dict:
    """まとめ: 確定仕様化 → Pull Request →（配布の記録があれば）リリース後テスト → 課題を閉じる → 振り返り → 棚卸し。"""
    issues = ",".join(map(str, a.issue))
    refs = " ".join(f"#{i}" for i in a.issue)
    branch = f"spec/{a.name}"
    stats = f"{CHECK_PY} stats --root {shlex.quote(repo)}"
    record_pr = str(a.record_pr) if getattr(a, "record_pr", None) else record_pr_ref(a)
    plan = with_decls(
        {
            "フェーズ": "まとめ",
            "課題": a.issue,
            "モード": a.mode,
            "作業場所": f"{repo}/.worktrees/{branch}",
            "branch": branch,
            "起点": f"origin/{a.base}",
            "リポジトリ": repo,
            "規則": "",
            "進め方": "auto" if getattr(a, "pace", None) == "auto" else "fast",
            "steps": [
                *_spec_pr_steps(a, refs),
                *merge_steps(a, next="release-verify" if has_record(record_pr) else "close"),
                *([release_verify_step(a.name, refs, record_pr)] if has_record(record_pr) else []),
                _close_step(a, repo, record_pr, issues),
                *_retro_refine_steps(a, refs, stats),
            ],
        },
        a,
    )
    return with_record(plan)


def close_waves(a) -> list[dict]:
    """スプリントの終わりのステージ。最終の検査で変更があったときだけ開発版と本番が流れる。"""
    repo = str(Path(a.worktree).resolve())
    if getattr(a, "pace", None) == "auto":
        # 開発ループの周回（#2089 の決定 10）: 検査と本番はスプリントのステージで済んでいるため、まとめだけを組む
        return [{"name": "まとめ", "plans": {"close": close_plan(a, repo)}}]
    final = f"{a.name}-final"
    changed = {"cmd": f"{CHECK_PY} changed --id {final} --root {shlex.quote(repo)}", "skip_code": 3}
    check = {"name": "最終の検査", "plans": {"check": plan_fast_check(a, repo, final, final=True)}}
    close = {"name": "まとめ", "plans": {"close": close_plan(a, repo)}, "then_of": "最終の検査"}
    if not has_release_template(a):
        return [check, *route_waves(a, repo, "最終の検査", mvv=a.state, condition=changed), close]
    return [
        check,
        {"name": "開発版", "plans": {"release": plan_mvv_release(a, repo, a.version, "dev", changed)}, "then_of": "最終の検査"},
        {"name": "本番", "plans": {"release-prod": plan_mvv_release(a, repo, a.prod, "prod", changed)}, "then_of": "最終の検査"},
        close,
    ]


def pace_refusal(a) -> str | None:
    """pace: fast / auto を使ってよい条件を確かめる。外れた理由を返す（満たせば None）。
    読む宣言の節が進め方の名前（fast / auto）である以外は同じ条件で、ほかの節の値は使わない。"""
    name = a.pace
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    try:
        pace = pace_decl(a)
    except PaceError as e:
        return str(e)
    if pace is None:
        return "進め方の宣言（.ndf/pace.json）が無い"
    sec = pace[name]
    if not sec["enabled"]:
        return f".ndf/pace.json の {name}.enabled が true でない"
    if not sec["verify"]:
        return f".ndf/pace.json の {name}.verify（導入の確認のコマンド）が無い"
    if a.mode not in sec["modes"]:
        return f"モード {a.mode} は {name} に入れられない（入れられるモード: {' / '.join(sec['modes'])}）"
    channel = dev_channel_of(a)
    if not channel.ok:
        return channel.reason
    mroot = next((r for r in roots if any((r / ".ndf" / f).is_file() for f in ("mvv.md", "mvv.json"))), roots[0])
    return mvv_refusal(a.state, mroot, name)


def mvv_refusal(state_path: str | None, root=None, pace: str = "fast") -> str | None:
    """MVV の承認の照合（`lib/project_mvv.approval_refusal`）。スプリント MVV の承認の記録とハッシュの一致、
    無ければ承認済みのプロジェクト MVV と状態に残した参照の一致を見る。外れた理由を返す。"""
    if not state_path:
        return f"--pace {pace} には --state（スプリントの状態）が要る"
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as e:
        return f"スプリントの状態を読めない: {e}"
    if not isinstance(state, dict):
        return "スプリントの状態を読めない: オブジェクトでない"
    return project_mvv.approval_refusal(state, root or Path.cwd())


def advises(a, closing: bool = False) -> bool:
    """--state を渡した normal だけ助言の MVV 判定を置く（#1400 の決定 4）。new close（closing）には置かない。"""
    return bool(getattr(a, "state", None)) and not closing


def sprint_state_path(a) -> str:
    return str(Path(a.state).resolve())


PACE_PLANS = {"fast": fast_sprint_plans, "auto": auto_sprint_plans}  # 進め方ごとのステージの組み立て（normal は sprint_plans）


def sprint_plans(a) -> list[dict]:
    """スプリントのステージを順に返す。ステージの中の計画は queue --max 3 で同時に流してよい。
    リリースの経路が雛形で組むものでなければ、経路ごとのステージ（route_waves）を最後に置く。"""
    build = PACE_PLANS.get(getattr(a, "pace", "normal"))
    if build:
        return build(a)
    repo = str(Path(a.worktree).resolve())
    advise = advises(a)
    design = plan_advise_design if advise else plan_sprint_design
    waves = []
    if a.design:
        waves.append({"name": "設計", "plans": {f"design-{n}": design(a, n, repo) for n in a.design}})
        waves.append({"name": "関門 1", "gate": normal_gate_1(a) if advise else "設計 Pull Request をまとめて承認してマージする"})
    waves += design_results_wave(a, repo)
    waves += [
        {"name": "スプリントブランチ", "plans": {"sprint-branch": plan_sprint_branch(a, repo)}},
        {"name": "実装", "plans": {f"impl-{n}": plan_sprint_impl(a, n, repo) for n in a.issue}},
        {"name": "検査", "plans": {"check": plan_sprint_check(a, repo)}},
    ]
    if not has_release_template(a):
        return waves + route_waves(a, repo, "検査", note=normal_gate_2(a) if advise else None)
    release = {"name": "配布", "plans": {"release": plan_sprint_release(a, repo, advise)}, "then_of": "検査"}
    if advise:
        release["note"] = normal_gate_2(a)
    return waves + [release]


def user_gate_cmd(a, gate: str, pr: bool) -> str:
    """利用者の答えを状態へ書くコマンド（覆しの記録。#1400 の AC6）。承認ゲート 1 は答えた設計 PR を --pr で渡す。"""
    state = shlex.quote(sprint_state_path(a))
    return (
        f"python3 {shlex.quote(str(HERE / 'sprint-state.py'))} gate {state} {shlex.quote(gate)} --what <要約> --by user"
        + (" --pr <設計 PR>" if pr else "")
        + " --outcome approved|rejected"
    )


def normal_gate_1(a) -> str:
    return (
        "設計 Pull Request をまとめて承認してマージする。承認資料には各設計 PR の助言の MVV 判定（PR のコメント。無ければ「MVV なし」）を"
        f"載せる。利用者が答えたら、設計 PR ごとに `{user_gate_cmd(a, '関門 1', True)}` を打つ"
    )


def normal_gate_2(a) -> str:
    return (
        "承認ゲート 2: 承認資料の末尾に助言の MVV 判定が載る（無ければ「MVV なし」）。利用者が本番への配布に答えたら、"
        f"`{user_gate_cmd(a, '関門 2', False)}` を打つ"
    )


def sprint_command(a, drop: tuple[str, ...] = (), design: list[int] | None = None) -> str:
    """sprint が受ける引数をすべて写した起動の形。drop の引数を外し、design を渡せば --design へ足す。
    進め方を断ったときは --pace と --state を外した normal の形を示す。"""
    words = ["python3", str(SELF), "new", "sprint"]
    for name, _, allowed in NEW_ARGS:
        value = getattr(a, name.lstrip("-").replace("-", "_"), None)
        if name == "--design" and design:
            value = [*(value or []), *(n for n in design if n not in (value or []))]
        if "sprint" not in allowed.split() or name in drop or value in (None, [], False):
            continue
        words += [name, *map(str, value)] if isinstance(value, list) else [name] if value is True else [name, str(value)]
    return " ".join(map(shlex.quote, words))


def add_resume(index: list[dict]) -> None:
    """then_of のステージへ resume（そのステージから最後までの then_of のステージを流す queue のコマンド）を書く。
    承認ゲートで止まった後に続きを流すときに写す（#1370 の決定 4）。"""
    for i, entry in enumerate(index):
        if "then_of" not in entry:
            continue
        rest = [e for e in index[i + 1 :] if e.get("then_of") == entry["then_of"]]
        cmd = f"python3 {shlex.quote(str(SELF))} queue " + " ".join(map(shlex.quote, entry["plans"])) + " --max 3"
        entry["resume"] = cmd + "".join(" --then " + " ".join(map(shlex.quote, e["plans"])) for e in rest)


def cmd_new_sprint(a, waves: list[dict] | None = None) -> dict:
    """スプリントの計画をステージごとのファイルへ書き出す。ステージは番号の順に queue で流す。
    then_of のステージは、そのステージの queue へ --then のステージとして足す（ステージは書いた順に流れる）。"""
    pace = getattr(a, "pace", "normal")
    if waves is None and pace in MVV_PACES:
        why = pace_refusal(a)
        if why:
            return result(
                "supervise-new",
                "stopped",
                f"pace: {pace} を使えない: {why}。計画を書かない",
                [],
                {"pace": pace},
                next=f"normal で進める（{sprint_command(a, drop=('--pace', '--state'))}）か、条件を満たしてから打ち直す",
            )
    closing = waves is not None
    if not closing and (stop := sprint_inputs.criteria_refusal(a, lambda design=None: sprint_command(a, design=design))):
        return stop
    waves = waves if waves is not None else sprint_plans(a)
    plans = [plan for wave in waves for plan in (wave.get("plans") or {}).values()]
    for plan in plans if getattr(a, "state", None) else []:
        plan["スプリント状態"] = sprint_state_path(a)  # worker と judge がスプリント MVV を状態から読む（#1400 の決定 12）
    if bad := limits.fill_all(plans):  # 上限は遷移から求め、知らない行き先があればどのファイルも書かない（#1909）
        return result("supervise-new", "stopped", bad, [], {"plans": len(plans)})
    out = sprint_out(a)
    out.mkdir(parents=True, exist_ok=True)
    index = write_stage_index(waves, out)
    add_resume(index)
    manifest = out / "sprint.json"
    head = manifest_head(a, pace, closing)
    manifest.write_text(json.dumps({**head, "リリースの経路": route_rows(a), "ステージ": index}, ensure_ascii=False, indent=2) + "\n")
    plans = sum(len(e.get("plans", [])) for e in index)
    return result(
        "supervise-new",
        "ok",
        f"スプリント {a.name} の計画を {plans} 本・{len(index)} ステージで書いた: {manifest}",
        index,
        {"waves": len(index), "plans": plans, "manifest": str(manifest)},
        next=next_text(pace, index, advise=pace == "normal" and advises(a, closing)),
    )


def _write_plans(wave: dict, i: int, out: Path) -> list[str]:
    """ステージのプランを 1 つずつファイルへ書き、パスの一覧を返す。"""
    paths = []
    for key, plan in wave["plans"].items():
        p = out / f"{i}-{key}.json"
        p.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        paths.append(str(p))
    return paths


def _stage_entry(wave: dict, i: int, out: Path) -> dict:
    """ステージ 1 件の一覧の項目。then_of のステージは command を持たず、前のステージへ連結される。"""
    entry = {"wave": i, "name": wave["name"]}
    if "gate" in wave:
        entry["gate"] = wave["gate"]
    elif "manual" in wave:
        entry.update(manual=wave["manual"], note=wave["note"])
    else:
        paths = _write_plans(wave, i, out)
        entry["plans"] = paths
        if "note" in wave:
            entry["note"] = wave["note"]
        if "then_of" in wave:
            entry["then_of"] = wave["then_of"]
        else:
            entry["command"] = f"python3 {shlex.quote(str(SELF))} queue " + " ".join(map(shlex.quote, paths)) + " --max 3"
    return entry


def write_stage_index(waves: list[dict], out: Path) -> list[dict]:
    """ステージごとのプランをファイルへ書き、ステージの一覧（command / then_of の連結を含む）を返す。"""
    index = []
    for i, wave in enumerate(waves, 1):
        entry = _stage_entry(wave, i, out)
        if "then_of" in entry:
            # 前のステージの queue が --then で続けて流す
            prev = next(e for e in index if e["name"] == entry["then_of"])
            prev["command"] += " --then " + " ".join(map(shlex.quote, entry["plans"]))
        index.append(entry)
    return index


def manifest_head(a, pace: str, closing: bool = False) -> dict:
    """manifest の見出し（スプリント / 進め方 / 状態 / ブランチ）。"""
    head = {"スプリント": a.name}
    if closing and pace == "auto":  # new close --pace auto（開発ループの周回のまとめ。#2089）
        head.update({"進め方": "auto", "状態": sprint_state_path(a)})
        return head
    if pace in MVV_PACES:
        head.update({"進め方": pace, "状態": sprint_state_path(a)})
        if pace != "fast":
            head["ブランチ"] = sprint_branch(a)
        return head
    # new close（waves を渡す）は --pace を持たず、状態があれば fast のスプリントの終わり
    if getattr(a, "state", None) and closing:
        head.update({"進め方": "fast", "状態": sprint_state_path(a)})
        return head
    if getattr(a, "state", None):  # normal と --state（#1400）
        head.update({"進め方": "normal", "状態": sprint_state_path(a)})
    head["ブランチ"] = sprint_branch(a)
    return head


def next_text(pace: str, index: list[dict], advise: bool = False) -> str:
    """次に打つ手の文面。auto だけ別の文で、manual のステージがあれば追記する。`advise`（normal と --state）なら
    承認資料に助言の MVV 判定を載せ、答えを状態へ書くことを足す。"""
    nxt = "ステージの番号の順に command を打つ。関門のステージでは承認を取ってから次へ進む"
    if advise:
        nxt += (
            "。承認資料には助言の MVV 判定を載せ、利用者が答えたら関門 1 のステージの gate・配布のステージの note にある "
            "sprint-state.py gate（--what と --by user。関門 1 は --pr も）を打つ"
        )
    if pace == "auto":
        nxt = (
            "最初のステージの command を打つ（後ろのステージは --then で続く）。queue が gate を返したら、承認資料に判定の理由と"
            "根拠の項目を添えて承認を取り、関門のステージの説明に沿って続きのステージの resume を打つ"
        )
    for manual in (e for e in index if "manual" in e):
        nxt += f"。{manual['name']}は {manual['manual']} で行う（{manual['note']}）"
    return nxt
