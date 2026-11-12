"""スプリントのリリースの経路（lib/delivery.py の routes）と、それを届けるステージの組み立て（#1336・#1454）。"""

from __future__ import annotations

import shlex

import delivery
from gate_answer import DESIGN_GATE, RELEASE_GATE
from pace import PaceError, read_pace
from sprint_mvv import MVV_PACES
from supervise_lib.decl import DeclError, decl_roots, delivery_decl
from supervise_lib.paths import HERE, SELF
from supervise_lib.plan import QUEUE_PRS, sprint_state_path
from supervise_lib.delivery_templates import GATE_2_MATERIAL, PromoteParams, plan_cd_result, plan_gate_2, plan_promote, plan_verify
from supervise_lib.release_templates import RELEASE_FORMS, material_path
from supervise_lib.verify_steps import plan_limits


def pace_decl(a) -> dict | None:
    """進め方の宣言（.ndf/pace.json）。宣言を探す場所の順に最初に見つかったもの。無ければ None、壊れていれば PaceError。"""
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    return next((read_pace(r) for r in roots if (r / ".ndf" / "pace.json").is_file()), None)


def dev_channel_of(a) -> delivery.DevChannel:
    """開発版のチャネルの形（lib/delivery.py の dev_channel）。宣言は apply_routes が載せた a.delivery を使う。"""
    decl = getattr(a, "delivery", None) or delivery_decl(a)
    return delivery.dev_channel(decl)


MANUAL_RELEASE = "/ndf:release"
CD_RESULT_STAGE = "CD の結果"


def with_cd_guidance(a, head: dict) -> tuple[dict, list[dict]]:
    """目録の頭へ CD の案内を載せ、(頭, 結果の items に足す並び) を返す。読むだけで、ステージにも終了コードにも効かない（I3）。
    `documentation` の提出と、宣言を読んでいない経路では出さない（I7）。"""
    decl = getattr(a, "delivery", None)
    got = None if decl is None or getattr(a, "mode", None) == "documentation" else delivery.cd_guidance(decl)
    return ({**head, "CD の案内": got.as_item()}, [got.as_item()]) if got else (head, [])


def direct_production(a, rs: list) -> str | None:
    """ベースブランチへのマージがそのまま本番系へ届く形の本番チャネル（無ければ None）。本番チャネルへ入れるリリースプランが無く、記録を書く主体が要る。"""
    decl = getattr(a, "delivery", None)
    production = decl.production if decl else None
    hit = any(r.stage == delivery.STAGE_MERGED_BY_CHECK and r.production is not False and r.branch == production for r in rs)
    return production if production and hit else None


def apply_routes(a) -> None:
    """a.routes（リリースの経路）を宣言から組む。雛形で組む経路でも版数は要らない（無ければデプロイのステップを流す時点に決める。#2164）。"""
    a.delivery = delivery_decl(a)
    a.routes = delivery.routes(a.delivery, tuple(RELEASE_FORMS))


def routes_of(a) -> list:
    """リリースの経路（apply_decls が載せる a.routes。無ければ release.form だけから導く）。"""
    rs = getattr(a, "routes", None)
    if rs is None:
        d = delivery.build({}, {}, a.release, base=a.base, production=getattr(a, "production_branch", None))
        rs = delivery.routes(d, tuple(RELEASE_FORMS))
    return rs


def route_rows(a) -> list[dict]:
    """sprint.json の「リリースの経路」の行。"""
    return [r.as_dict() for r in routes_of(a)]


def has_release_template(a) -> bool:
    """リリースの経路が release.form の雛形（開発版 → 本番）で組むものか。"""
    return any(r.stage == delivery.STAGE_TEMPLATE for r in routes_of(a))


RECORD_NONE = "0"  # 本番の記録なし（デプロイなし。sprint-close.py は閉じる条件 1・2 を見ない。#1336）
RECORD_FOUND = "{queue_record}"  # まとめを流し始める時点に queue が GitHub から探すリリース記録の PR（#2230）


def has_record(record_pr: str) -> bool:
    """まとめの close が読むリリース記録の PR（番号か {queue_pr:…}・{queue_record}）があるか。"""
    return record_pr != RECORD_NONE


def record_pr_ref(a) -> str:
    """まとめの close が読むリリース記録の PR。経路がすべて届けない経路なら RECORD_NONE、それ以外は RECORD_FOUND
    （まとめより前の本番の記録。まとめの後ろに置いた最終の検査の開発版・本番はまとめより後に流れ、まとめから参照できない。
    #2230 の決定 5・#3126 の決定 1）。"""
    if all(r.stage == delivery.STAGE_NONE for r in routes_of(a)):
        return RECORD_NONE
    return RECORD_FOUND


def manual_release_wave(a) -> dict:
    """手で届ける経路のために最後に置く、手で行うリリースのステージ（プランを持たない）。note に経路ごとの理由を書く。"""
    notes = [r.note for r in routes_of(a) if r.stage == delivery.STAGE_MANUAL and r.note]
    why = "。".join(notes) or "手で届ける経路がある"
    return {"name": "リリース", "manual": MANUAL_RELEASE, "note": f"{why}。検査の後に {MANUAL_RELEASE} で行う"}


def closing_pace(a) -> str:
    """読む進め方の節。MVV の進め方でなければ fast と読む（new close は fast のスプリントの終わり）。"""
    return a.pace if getattr(a, "pace", None) in MVV_PACES else "fast"


def pace_verify(a) -> str:
    """選んだ進め方の節の verify（導入の確認のコマンド）。new close は fast のスプリントの終わりとして fast を読む。
    宣言が壊れている・節に verify が無ければ DeclError。"""
    pace = closing_pace(a)
    try:
        sec = (pace_decl(a) or {}).get(pace) or {}
    except PaceError as e:
        raise DeclError(str(e)) from e
    if not sec.get("verify"):
        raise DeclError(f".ndf/pace.json の {pace}.verify（導入の確認のコマンド）が無い")
    return sec["verify"]


def _plan_wave(name: str, key: str, plan: dict, then_of: str, note: str | None) -> dict:
    """1 本のプランのステージ。note が真なら note を足す。"""
    wave = {"name": name, "plans": {key: plan}, "then_of": then_of}
    if note:
        wave["note"] = note
    return wave


def route_waves(a, repo: str, then_of: str, mvv: str | None = None, condition: dict | None = None, note: str | None = None) -> list[dict]:
    """雛形で組まない経路のステージ（#1336）。リリースの Pull Request で届く経路（promote）があれば「本番」（本番チャネルへ入れるリリースプラン）を、手で届ける経路
    （manual）があれば「リリース」（手で行う）をこの順に置く。fast / auto（mvv あり）では、本番チャネルへ入れるリリースプランの先頭で導入の確認を
    走らせ、リリースの Pull Request が無くベースブランチへのマージで届く経路（merged-by-check）があれば「導入の確認」のステージを置く（#1457）。
    その経路のブランチが本番チャネルなら、続けて「CD の結果」のステージを置く（#2623 の決定 9）。
    normal の merged-by-check と、届けない経路（none）はステージを置かない。"""
    if mvv and dev_channel_of(a).value == delivery.MANUAL_PRODUCTION:
        return manual_production_waves(a, repo, then_of, mvv, condition)
    return channel_waves(a, repo, then_of, mvv, condition, note)


def channel_waves(a, repo: str, then_of: str, mvv: str | None, condition: dict | None, note: str | None) -> list[dict]:
    """手動反映の本番系の形でない経路のステージ: 本番か導入の確認 → CD の結果 → 手で行うリリース（並びは `route_waves`）。"""
    rs = routes_of(a)
    waves = []
    promote = next((r for r in rs if r.stage == delivery.STAGE_PROMOTE), None)
    merge = any(r.stage == delivery.STAGE_MERGED_BY_CHECK for r in rs)
    verify = pace_verify(a) if mvv and (promote or merge) else None
    if promote:
        ci_wait = int(plan_limits(a)["ci_wait_timeout"])
        plan = plan_promote(a, repo, ci_wait, PromoteParams(mvv=mvv, production=promote.branch, verify=verify), condition)
        waves.append(_plan_wave("本番", "promote", plan, then_of, note))
    elif verify:
        waves.append(_plan_wave("導入の確認", "verify", plan_verify(a, repo, verify, condition), then_of, note))
    if not promote and (production := direct_production(a, rs)):
        cd = plan_cd_result(a, repo, production, condition)
        waves.append(_plan_wave(CD_RESULT_STAGE, "cd-result", cd, then_of, note if not waves else None))  # 先行のステージが無いときだけ
    if any(r.stage == delivery.STAGE_MANUAL for r in rs):
        waves.append(manual_release_wave(a))
    return waves


def manual_production_waves(a, repo: str, then_of: str, mvv: str, condition: dict | None = None) -> list[dict]:
    """手動反映の本番系の形（#1454）の fast / auto の手で届ける経路: 開発版（production: false の行。あれば）→
    リリース承認ゲート（gate-2 のプラン）→ 本番（ほかの手で届ける行。production を書いていない行もここ）。開発版があれば
    リリース承認ゲートは単独の queue（conductor が開発版の後に command を打つ）、無ければ then_of で続けて流す。"""
    rs = [r for r in routes_of(a) if r.stage == delivery.STAGE_MANUAL]
    dev = [r for r in rs if r.production is False]
    prod = [r for r in rs if r.production is not False]
    pace = closing_pace(a)
    verify = pace_verify(a)
    # auto は検査のスプリントの PR（単独の queue でも同じディレクトリの検査の報告から埋まる）、fast は前のステージの PR のすべて。
    # fast の単独の queue は前のステージの PR を集められないため、MVV 判定を打たずに利用者の承認ゲートにする（決定 6）
    prs = "{queue_pr:check}" if pace == "auto" else None if dev else QUEUE_PRS
    gate = {"name": RELEASE_GATE, "plans": {"gate-2": plan_gate_2(a, repo, mvv, verify, prs, condition)}}
    waves = []
    if dev:
        why = "。".join(r.note for r in dev if r.note)
        waves.append(
            {
                "name": "開発版",
                "manual": MANUAL_RELEASE,
                "note": f"{why}。承認は要らない。検査の後に {MANUAL_RELEASE} で届け、済んだらリリース承認ゲートのステージの command を打つ",
            }
        )
    else:
        gate["then_of"] = then_of
    why = "。".join(r.note for r in prod if r.note) or "手で届ける経路がある"
    waves += [
        gate,
        {
            "name": "本番",
            "manual": MANUAL_RELEASE,
            "note": f"{why}。リリース承認ゲートが通過（リリース承認ゲートの記録が by: mvv か by: user）してから、承認資料（リリース承認ゲートのプランの "
            f"{GATE_2_MATERIAL}）のコミットを checkout して {MANUAL_RELEASE} で trigger を打つ。ベースブランチの先頭がそのコミットと"
            f"違えば、リリース承認ゲートの command からやり直す。{MANUAL_RELEASE} へはこの note と承認資料のパスを引き継ぐ",
        },
    ]
    return waves


def user_gate_cmd(a, gate: str, pr: bool, outcomes: str = "approved|rejected") -> str:
    """利用者の答えを状態と差し戻しの記録へ書くコマンド（覆しの記録。#1400 の AC6・#2183）。設計承認ゲートは答えた設計 PR を --pr で渡す。"""
    state = shlex.quote(sprint_state_path(a))
    return (
        f"python3 {shlex.quote(str(HERE / 'sprint-state.py'))} gate {state} {shlex.quote(gate)} --what <要約> --by user"
        + (" --pr <設計 PR>" if pr else "")
        + f" --outcome {outcomes}"
    )


def reject_resume(what: str) -> str:
    """差し戻しの後の続き: 直してから計画を `--from reject` で流す（差し戻しを差し戻しの記録へ書いて再提示する。#2183）。"""
    return f"差し戻されたら直してから `python3 {shlex.quote(str(SELF))} run <{what}> --from reject` で流す（差し戻しを記録して再提示する）"


def normal_gate_1(a, advise: bool = True) -> str:
    """設計承認ゲートの文。承認の証跡（状態の記録か承認ラベル）を書いてから、設計 PR のマージのステージの command を打つ（#1426 の決定 1）。"""
    after = "。後続のステージは --then で続けて流れる。証跡の無い設計 PR のマージは承認ゲートで止まり、後続は流れない"
    if not getattr(a, "state", None):
        return (
            "設計 Pull Request の承認を取り、承認された PR ごとに `gh pr edit <設計 PR> --add-label design-approved` を打ってから「設計 PR のマージ」のステージの command を打つ"
            + after
        )
    return (
        "設計 Pull Request の承認を取る。"
        + ("承認資料には各設計 PR の助言の MVV 判定（PR のコメント。無ければ「MVV なし」）を載せる。" if advise else "")
        + f"利用者が答えたら、設計 PR ごとに `{user_gate_cmd(a, DESIGN_GATE, True, 'approved')}` を打ってから「設計 PR のマージ」のステージの command を打つ"
        + after
        + "。"
        + reject_resume("その設計 PR の設計のプラン")
    )


def normal_gate_2(a) -> str:
    return (
        "リリース承認ゲート: 承認資料の末尾に助言の MVV 判定が載る（無ければ「MVV なし」）。利用者がリリースに答えたら、"
        f"`{user_gate_cmd(a, RELEASE_GATE, False)}` を打つ"
    )


def normal_gate_2_stage(a, repo: str | None, prod: str | None, advise: bool) -> str:
    """pace: normal のリリース承認ゲートのステージの文。承認の手順はここの 1 か所に置き、リリースの note に重ねない（#1914 の決定 1）。
    承認資料は本番のプランの release と同じく元のリポジトリの下の絶対パスで示す（作業場所の git の根に依らない）。
    本番の版を明示していなければ、承認資料の版は開発版のプランの版の記録（状態ディレクトリの version.json）の base である（#2164）。"""
    plugin = (a.release or {}).get("plugin")
    approval = material_path(repo, f"issues/approval-{plugin}-v{prod or '<base>'}.md")
    where = "" if prod else "。<base> は開発版のプラン release の状態ディレクトリの version.json の base"
    if getattr(a, "state", None):  # 本番のプランの先頭の approve が状態の記録を証跡に承認の記録を書く（#1426 の決定 1）
        return (
            f"開発版のデプロイが書いた承認資料（{approval}{where}）を示して本番へのリリースの承認を取り、利用者が答えたら "
            f"`{user_gate_cmd(a, RELEASE_GATE, False)}` を打ち、承認されたら「本番」のステージの command を打つ（差し戻されたら打たない）。"
            "本番のプランの先頭の approve が、開発版の facts の承認したコミットとこの記録を照らして承認資料へ承認の記録を書く"
            + ("。承認資料の末尾に助言の MVV 判定が載る（無ければ「MVV なし」）" if advise else "")
        )
    return (
        f"開発版のデプロイが書いた承認資料（{approval}{where}）を示して本番へのリリースの承認を取り、承認されたら `python3 {shlex.quote(str(HERE / 'release-steps.py'))} approve --approval {shlex.quote(approval)} "
        "--approved-sha <approval-facts の metrics.approved_sha> --by user` で承認を記録してから「本番」のステージの command を打つ（差し戻されたら打たない）"
    )
