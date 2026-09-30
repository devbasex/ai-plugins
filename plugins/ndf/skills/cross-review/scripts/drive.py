#!/usr/bin/env python3
"""drive.py: cross-review の収束ループを、LLM の判断が要る地点まで進めて止まる。

    drive.py <PR> [--rotate-mode light|squash] [state.py init の引数...]

init → ラウンド（起動・監視・取り込み・根拠の検証・判定）→ 振動の検知 → 修正 → 巻き直し →
最終スイープ → 検証 → 報告を順に進める。LLM が要る地点（fix / sweep / newtext）で止まる。
同じコマンドを打ち直すと続きから進む。進みは耐久の記録（`lib/durable.py`、種類 `review`・鍵の元は状態の置き場）に
あり、耐久ワークフロー `review_drive` の 1 回が収束ループの 1 回である。`state.py init` とラウンドは耐久ステップで、
記録のある耐久ステップは打ち直しで流れない（init は 1 回の中で 1 回だけ。#1142 の I19）。止まりはイベント `pause` を
立てて続きを待ち、打ち直しが続き（`resume`）を送る。結果ファイルが無ければ同じ止まりを番号を増やして返す。
途中のスクリプトが失敗したときも stopped を返して同じように待ち、打ち直しで同じ段階をやり直す。

止まるときの JSON の形と終了コードの表は `scripts/lib/drive_pause.py` にある。

件数（metrics）は状態ファイルから数える: rounds / prs / findings / fixed / deferred / rejected /
unresolved / final / review_status。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
LIB = HERE.parents[2] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import deps  # noqa: E402

deps.require("durable")
import drive_pause as dp  # noqa: E402
import durable  # noqa: E402
import step_result as sr  # noqa: E402
from drive_pause import Stop  # noqa: E402
from loop_drive import call, durable_identity, parse_vars, review_status  # noqa: E402,F401  テストは `call` をこのモジュールの上で差し替える

TOOL = "cross-review-drive"
DOCS02 = SKILL / "docs" / "02-fix-and-rotation.md"


def review_paths():
    """置き場の規則を init と揃えるため、`review_lib` の `github` と `workspace` を読み込む（打ち直しのときだけ呼ぶ）。"""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from review_lib import github, workspace

    return github, workspace


def sweep_verify_lines(worktree) -> str:
    """最終スイープの検証のコマンドの段落（#1483 I13）。宣言の `test` があれば全体テスト（両方の種別）を名指しする。

    宣言が無い・読めなければ空で、プロンプトは今の探し方（Step 7.5）のまま。`test` があって解けない（不正な
    `kind` など）ときは、どのキーが不正かを添えて `Stop` で止める。`--verify-command` は読まない。
    """
    if not worktree:
        return ""
    import project_decl
    import test_strategy as ts

    try:
        decl = project_decl.read_project_decl(str(worktree))
    except Exception:  # 宣言が読めなければ今の探し方に任せる
        return ""
    try:
        commands = ts.verify_commands(decl)
    except ts.StrategyError as e:
        raise Stop(f".ndf/project.json の test が不正: {e}", 2) from e
    if not commands:
        return ""
    listed = "\n".join(f"  - {c}" for c in commands)
    return (
        "- 修正をコミットしたら、検証は次のコマンドを作業ディレクトリで順に走らせる（.ndf/project.json の test）。"
        f"Step 7.5 の探し方は使わない:\n{listed}\n"
    )


class Held(Exception):
    """耐久ステップが Stop を返した。耐久ワークフローは stopped の止まりを立て、続きで同じ段階をやり直す。"""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.msg, self.code = msg, code


class Drive:
    def __init__(self, pr: int, rotate_mode: str, init_args: list[str]):
        self.pr = pr
        self.rotate_mode = rotate_mode
        self.init_args = init_args
        self.env = dict(os.environ)
        self.v: dict = {}
        self.rotated: dict = {}

    # --- 呼び出し ---
    def st(self, *args: str) -> tuple[int, str]:
        return call([sys.executable, str(HERE / "state.py"), *args], self.env, self.v.get("WORKTREE"))

    def sh(self, name: str, *args: str) -> tuple[int, str]:
        return call(["bash", str(HERE / name), *args], self.env, self.v.get("WORKTREE"))

    def must(self, rc_out: tuple[int, str], what: str, ok=(0,)) -> str:
        rc, out = rc_out
        if rc not in ok:
            raise Stop(f"{what} が終了コード {rc} で止まった", rc)
        return out

    # --- 状態 ---
    @property
    def tmp(self) -> Path:
        return Path(self.v["TMP_DIR"])

    def path(self, kind: str) -> Path:
        return self.tmp / f"{kind}-pr{self.pr}-result.json" if kind in ("fix", "sweep") else self.tmp / f"rotate-pr{self.pr}-{kind}.json"

    def state(self) -> dict:
        try:
            return json.loads((self.tmp / f"cross-review-pr{self.pr}-state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def counts(self) -> dict:
        """件数。**指摘（findings）と修正（fixed）は修正担当の単位でそろえる**（#1317）。

        `findings` は修正担当が扱った指摘（各ラウンドの直した・見送った・却下したの和）で、`findings >= fixed` が
        数え方で成り立つ。レビュー担当が出したコメントの数は `comments` で別に出す（1 つのコメントに複数の指摘が
        入り、2 者が同じ所を指せば 2 件になるため、指摘の数と比べない）。収束の判定はこの値を読まない。"""
        s = self.state()
        rounds = s.get("rounds") or []
        comments = fixed = deferred = rejected = 0
        for r in rounds:
            for who in r.get("reviewers") or []:
                comments += int((r.get(who) or {}).get("comments") or 0)
            fx = r.get("fix") or {}
            fixed += int(fx.get("fixed") or 0)
            deferred += int(fx.get("deferred") or 0)
            rejected += int(fx.get("rejected") or 0)
        sweep = s.get("sweep") or {}
        return {
            "rounds": len(rounds),
            "prs": len(s.get("pr_history") or []) or 1,
            "comments": comments,
            "findings": fixed + deferred + rejected,
            "fixed": fixed,
            "deferred": deferred,
            "rejected": rejected,
            "unresolved": sweep.get("remaining_open"),
            "final": s.get("final"),
            "review_status": review_status(s),
        }

    # --- 止まる ---
    def pause(self, kind: str, fresh: bool) -> dict:
        """止まりの結果を組む。`fresh`（その段階へ入った最初の止まり）なら前の実行の結果ファイルを消す。"""
        res = self.path(kind)
        if fresh:
            res.unlink(missing_ok=True)  # 前の実行の結果を読まない
        prompt = {"fix": self.fix_prompt, "sweep": self.sweep_prompt, "newtext": self.newtext_prompt}[kind]()
        pf = self.tmp / f"drive-pr{self.pr}-{kind}-prompt.md"
        pf.write_text(prompt)
        c = self.counts()
        wt = self.state().get("worktree_path")
        return dp.pause(TOOL, kind, pf, res, c["rounds"], c, **({"cwd": str(wt)} if wt else {}))

    def has_result(self, kind: str) -> bool:
        return self.path(kind).is_file()

    def stopped(self, msg: str, code: int) -> dict:
        return dp.stopped(TOOL, msg, self.counts() if "TMP_DIR" in self.v else {}, code)

    # --- プロンプト（手順は写さず、呼び出しと PR 固有の穴埋めだけ） ---
    def fix_prompt(self) -> str:
        s = self.state()
        r = (s.get("rounds") or [{}])[-1]
        pr = s.get("current_pr") or self.pr
        reviews = (
            "\n".join(
                f"  - {who}: intent={(r.get(who) or {}).get('intent')}, posted_as={(r.get(who) or {}).get('posted_as')}, "
                f"{(r.get(who) or {}).get('comments', 0)} 件, {(r.get(who) or {}).get('review_url', '')}"
                for who in r.get("reviewers") or []
            )
            or "  - 無し"
        )
        return f"""`/ndf:fix {pr}` を行う。修正の手順・方針・戻り値ファイルの形は `/ndf:fix` が持つ。

- リポジトリ: {s.get("repo")}
- PR: #{pr}（round {r.get("round")}）
- 作業ディレクトリ: {s.get("worktree_path")}（外を触らない）
- ブランチ: {s.get("head_branch")} / ベース: {s.get("base_branch")}
- 作業ディレクトリは detached HEAD（PR の head）のままでよい。ブランチへ切り替えず、そこでコミットする。送る（push）のは取り込み
- 前ラウンドのレビュー（件数はそのラウンドで投稿した数。対象は reviewThreads を数え直して決める）:
{reviews}
- 既存コメントのスナップショット: {self.tmp}/cross-review-pr{self.pr}-existing-comments.txt

コミットまでで、送らない。GitHub へ書かない（送信・返信・決着は取り込みが行う）。
戻り値ファイル: {self.path("fix")}（環境変数 `CROSS_REVIEW_TMP_DIR={self.tmp}` を渡すと `/ndf:fix` がここへ書く）
`fix-steps.py context` には環境変数 `CROSS_REVIEW_STATE={self.tmp}/cross-review-pr{self.pr}-state.json` も渡す（担当と同じ指摘の基準を使う）
"""

    def sweep_prompt(self) -> str:
        s = self.state()
        pr = s.get("current_pr") or self.pr
        return f"""最終スイープを行う: `/ndf:fix {pr}` を再実行し、PR の open review thread を 1 件も残さない。
修正の手順は `/ndf:fix` が持つ。スイープの規約と結果ファイルの形は {DOCS02} の「Step 7.5」にある。

- リポジトリ: {s.get("repo")}
- PR: #{pr}
- 作業ディレクトリ: {s.get("worktree_path")}（外を触らない）
- 作業ディレクトリは detached HEAD（PR の head）のままでよい。ブランチへ切り替えず、そこでコミットする。送る（push）のは取り込み
- ループの終わり: {s.get("final")}
- `fix-steps.py context` には環境変数 `CROSS_REVIEW_STATE={self.tmp}/cross-review-pr{self.pr}-state.json` を渡す（ループと同じ指摘の基準を使う）
{sweep_verify_lines(s.get("worktree_path"))}
GitHub と git の送信をしない。結果ファイル: {self.path("sweep")}
"""

    def newtext_prompt(self) -> str:
        s = self.state()
        return f"""light の巻き直しで作る新しい Pull Request の title / body を書く。
書いてよいこと・書いてはいけないことと出力の形は {DOCS02} の「Step 6b」にある。

- 素材: {self.path("prepare")}
- 作業ディレクトリ: {s.get("worktree_path")}（読むだけ）

出力ファイル（JSON: {{"title": ..., "body": ...}}）: {self.path("newtext")}
"""

    # --- 耐久ステップの中身（1 つのメソッドを 1 つの耐久ステップで流す） ---
    def known_tmp(self) -> Path | None:
        """init を打たずに、`state.py init` と同じ規則で状態の置き場を求める。求まらなければ None。"""
        env = os.environ.get("CROSS_REVIEW_TMP_DIR")
        if env:
            return Path(env).resolve()
        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--worktree")
        wt = ap.parse_known_args(self.init_args)[0].worktree
        if wt:
            return Path(wt).resolve() / ".cross_review"
        github, workspace = review_paths()
        repo = github._repo_from_git()
        return workspace._default_worktree_base() / github._repo_slug(repo) / f"pr{self.pr}" / ".cross_review" if repo else None

    def identity(self) -> str:
        """耐久の記録の鍵の元。状態の置き場（求まらなければ作業ディレクトリと PR）。"""
        return durable_identity(self.known_tmp(), self.pr)

    def init(self) -> dict:
        out = self.must(call([sys.executable, str(HERE / "state.py"), "init", str(self.pr), *self.init_args], self.env), "state.py init")
        iv = parse_vars(out)
        if "TMP_DIR" not in iv:
            raise Stop("state.py init が TMP_DIR を返さない", 2)
        return iv

    def adopt(self, iv: dict) -> None:
        """init の出力（耐久ステップの記録）を持つ。打ち直しでは記録から組み直し、init を打たない（I19）。"""
        self.v.update(iv)
        self.env["CROSS_REVIEW_TMP_DIR"] = self.v["TMP_DIR"]

    def review_round(self) -> str:
        """1 ラウンド。戻り値: fix / round（修正を挟まずに次のラウンド）/ done。"""
        rc, out = self.st("start-round", str(self.pr))
        if rc == 1:
            return "done"
        if rc != 0:
            raise Stop(f"state.py start-round が終了コード {rc} で止まった", rc)
        jrc, jout = self.collect_reviews(parse_vars(out))
        return self.after_judge(jrc, jout)

    def run_reviewers(self, agents: list[str], rnd: str, reviewers: list[str]) -> None:
        """担当を起動して監視し、結果が揃っていれば検証と反証まで通す。"""
        for a in agents:
            self.sh("launch-reviewer.sh", a, str(self.pr), rnd)
        call(
            [sys.executable, str(HERE / "monitor.py"), str(self.pr), "--phase", "review", "--agents", ",".join(agents)],
            self.env,
            self.v.get("WORKTREE"),
        )
        missing = [a for a in agents if self.st("read-result", str(self.pr), a)[0] != 0]
        if not missing:
            # 結果の欠けた担当がいれば、検証と反証は judge の起動し直し・中断の後へ回す。
            # 先に通すと、起動し直した後に全担当分をもう一度通すため 1 回分が捨てられる。
            self.st("verify-findings", str(self.pr))
            self.sh("critique-round.sh", str(self.pr), rnd, *reviewers)

    def judge(self) -> tuple[int, str]:
        """judge を打つ。8 なら flush してからもう一度打つ。"""
        jrc, jout = self.st("judge", str(self.pr))
        if jrc == 8:
            self.st("flush", str(self.pr))
            jrc, jout = self.st("judge", str(self.pr))
        return jrc, jout

    def collect_reviews(self, rv: dict) -> tuple[int, str]:
        """担当の結果を集めて judge する。judge が 7 なら 1 度だけ指名された担当を起動し直す。"""
        rnd = rv.get("ROUND", "")
        agents = rv.get("REVIEWERS", "").split()
        relaunched = False
        while True:
            self.run_reviewers(agents, rnd, rv.get("REVIEWERS", "").split())
            jrc, jout = self.judge()
            if jrc == 7 and not relaunched:
                agents = parse_vars(jout).get("RELAUNCH_AGENTS", "").split()
                relaunched = True
                if agents:
                    continue
            return jrc, jout

    def after_judge(self, jrc: int, jout: str) -> str:
        """judge の終了コードから done / round / fix を決める。"""
        if jrc == 0:
            return "done"
        if jrc != 2:
            raise Stop(f"state.py judge が終了コード {jrc} で止まった", jrc)
        if parse_vars(jout).get("MODEL_CONFIRMED") == "1":
            return "round"  # 設計 PR のモデルの段が承認された。修正を挟まずに詳細の段へ進む（#1111）
        orc, _ = self.st("check-oscillation", str(self.pr))
        if orc == 4:
            return "done"  # final = oscillation。最終スイープへ
        if orc not in (0, 2):
            raise Stop(f"state.py check-oscillation が終了コード {orc} で止まった", orc)
        return "fix"

    def merge_fix(self) -> str:
        """修正の取り込み。取り込めたら rotate、final = error なら最終スイープ（sweep-start）。"""
        rc, _ = self.st("merge-fix", str(self.pr))
        if rc == 3:
            return "sweep-start"
        if rc != 0:
            raise Stop(f"state.py merge-fix が終了コード {rc} で止まった", rc)
        return "rotate"

    def rotate_prepare(self) -> str:
        """巻き直しの要否と準備。戻り値: round（巻き直さない）/ newtext（light）/ execute（squash）。"""
        rrc, _ = self.st("should-rotate", str(self.pr))
        if rrc != 0:
            return "round"
        self.must(self.sh("rotate-pr.sh", "prepare", str(self.pr)), "rotate-pr.sh prepare")
        return "newtext" if self.rotate_mode == "light" else "execute"

    def rotate_execute(self) -> dict:
        """新しい PR を作る。作った PR は耐久ステップの出力に残り、set-current-pr で止まった打ち直しは execute を流し直さない。"""
        out = self.must(self.sh("rotate-pr.sh", "execute", str(self.pr), "--mode", self.rotate_mode), "rotate-pr.sh execute")
        rv = parse_vars(out)
        return {"new_pr": rv.get("NEW_PR", ""), "new_branch": rv.get("NEW_BRANCH", "")}

    def set_current(self, rot: dict) -> None:
        self.must(
            self.st("set-current-pr", str(self.pr), rot.get("new_pr", ""), "--head-branch", rot.get("new_branch", "")),
            "state.py set-current-pr",
        )

    def finish(self) -> dict:
        s = self.state()
        pr = str(s.get("current_pr") or self.pr)
        posts = [sys.executable, str(LIB / "result_posts.py"), "fix", "--pr", pr, "--result", str(self.path("sweep"))]
        self.must(call([*posts, "--worktree", str(self.v.get("WORKTREE", ""))], self.env), "result_posts.py fix")
        self.must(self.st("verify-sweep", str(self.pr)), "state.py verify-sweep", ok=(0, 6))
        _, rep = self.st("report", str(self.pr))
        rp = self.tmp / f"drive-pr{self.pr}-report.md"
        rp.write_text(rep)
        c = self.counts()
        return dp.done(
            TOOL,
            f"収束ループが終わった（final={c['final']}・{c['rounds']} ラウンド・コメント {c['comments']}・指摘 {c['findings']}・修正 {c['fixed']}・未解決 {c['unresolved']}）",
            rp,
            c,
        )

    # --- 起動: 耐久の記録を開き、耐久ワークフローを始めるか続けて、止まりか終わりを待つ ---
    def finished(self, out) -> bool:
        """記録した終わりをそのまま返してよいか。レビューの状態のファイルが消えていれば頭から流す。"""
        if not isinstance(out, dict) or (out.get("result") or {}).get("status") != "ok":
            return False
        return (Path(out.get("tmp") or "") / f"cross-review-pr{self.pr}-state.json").is_file()

    def run(self) -> tuple[dict, int]:
        identity = self.identity()
        durable.launch("review", identity)
        ref = durable.resolve(f"review-{durable.key_hash(identity)}", finished=self.finished)
        if ref.action == "done":
            return ref.output["result"], ref.output["code"]
        seen = durable.resume_paused(ref)  # 止まりの続き。結果ファイルが無ければ同じ段階がもう一度止まる
        durable.start(ref, review_drive, self.pr, self.rotate_mode, self.init_args)
        got = durable.wait(ref.id, "pause", after=seen)
        if got.kind in ("event", "done"):
            return got.value["result"], got.value["code"]
        raise Stop(f"駆動の耐久ワークフロー {ref.id} が {got.kind} で終わった: {got.value}", 1)


# ---- 耐久ワークフロー（段階の遷移）と耐久ステップ ----

STEP_LIMIT = 1000  # 段階の数の上限
# 登録名は読み込んだモジュールの名前で修飾する（本番は常に `__main__`）。同じファイルを別の名前で読んだ写しどうしが、
# 互いの耐久ワークフローの回復を奪わない（DBOS は同じ名前の登録を後勝ちで置き換える）
NAME = f"{__name__}:review_drive"


@durable.step(name=f"{NAME}.act")
def act(d: Drive, method: str, *args):
    """`Drive` のメソッドを 1 つ流し、出力を記録する。Stop は例外にせず出力として残す（打ち直しで終了コードを保つ）。"""
    try:
        return {"ok": getattr(d, method)(*args)}
    except Stop as e:
        return {"stop": str(e), "code": e.code}


def ok(res: dict):
    if "stop" in res:
        raise Held(res["stop"], res["code"])
    return res["ok"]


def advance(d: Drive, stage: str) -> tuple[str, tuple | None, dict | None]:
    """段階を 1 歩進める。戻り値: (次の段階, 見せる止まり (種類, 新しく入ったか) か None, 終わりの結果か None)。"""
    if stage == "init":
        d.adopt(ok(act(d, "init")))
        return "round", None, None
    if stage == "round":
        nxt = ok(act(d, "review_round"))
        if nxt == "fix":
            return "fix", ("fix", True), None
        return ("round" if nxt == "round" else "sweep-start"), None, None
    if stage == "sweep-start":
        return "sweep", ("sweep", True), None
    if stage == "rotate":
        nxt = ok(act(d, "rotate_prepare"))
        return nxt, (("newtext", True) if nxt == "newtext" else None), None
    if stage == "execute":
        d.rotated = ok(act(d, "rotate_execute"))
        return "set-current", None, None
    if stage == "set-current":
        ok(act(d, "set_current", d.rotated))
        return "round", None, None
    # fix / sweep / newtext: 結果ファイルがあれば進み、無ければ同じ止まりをもう一度見せる
    if not ok(act(d, "has_result", stage)):
        return stage, (stage, False), None
    if stage == "fix":
        return ok(act(d, "merge_fix")), None, None
    if stage == "newtext":
        return "execute", None, None
    return "done", None, ok(act(d, "finish"))


@durable.workflow(name=NAME)
def review_drive(pr: int, rotate_mode: str, init_args: list[str]) -> dict:
    """収束ループの 1 回。止まりはイベント `pause` を立てて続き（`resume`）を待ち、終わりの結果を返す。"""
    d = Drive(pr, rotate_mode, init_args)
    seq, stage = 0, "init"

    def outcome(res: dict) -> dict:
        return {"result": res, "code": dp.exit_code(res), "tmp": d.v.get("TMP_DIR")}

    for _ in range(STEP_LIMIT):
        try:
            stage, show, final = advance(d, stage)
            if final is not None:
                return outcome(final)
            shown = ok(act(d, "pause", *show)) if show else None
        except Held as h:
            shown = ok(act(d, "stopped", h.msg, h.code))  # 同じ段階を続きでやり直す
        if shown is not None:
            seq += 1
            if durable.pause(seq, result=shown, code=dp.exit_code(shown)) is None:
                return outcome(ok(act(d, "stopped", "止まりの待ちが上限を過ぎた", 1)))
    return outcome(ok(act(d, "stopped", "段階の数が上限を超えた", 1)))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("--rotate-mode", choices=["light", "squash"], default="light")
    a, rest = ap.parse_known_args(argv)
    d = Drive(a.pr, a.rotate_mode, rest)
    try:
        out, code = d.run()
    except (Stop, durable.DurableError) as e:
        out, code = dp.stopped(TOOL, str(e), {}, getattr(e, "code", 1)), dp.EXIT_STOPPED
    try:
        sr.emit(out, code)
    except SystemExit as e:
        # 止まりのまま耐久ワークフローを残して抜ける（DBOS の後始末で待たない）
        durable.exit_leaving_pending(e.code if isinstance(e.code, int) else 1)


if __name__ == "__main__":
    main()
