#!/usr/bin/env python3
"""drive.py: cross-refactoring の 1 回の改修計画実行を、LLM の判断が要る地点まで進めて止まる。

    drive.py <PR> --scope ... [refactor.py init の引数...]   # テストの走らせ方は .ndf/project.json の test の戦略で決まる

init → 提案 → 改修計画 → テスト追加 → 実装 → 検証と修正 → 最終ゲート → finalize を順に進める。
参加者は全て CLI なので、止まるのは単独起動の最終ゲート（cross-review）だけである。

進みは耐久の記録（`lib/durable.py`。#1142 の決定 32）に持つ。1 回の実行は耐久ワークフロー `refactor_drive` の
1 つで、`refactor.py` ほかの子の呼び出し・状態ファイルの読み書きを 1 つずつ耐久ステップにする。同じコマンドを
打ち直すと、記録のある耐久ステップを流し直さずに続ける（`refactor.py init` は 1 つの実行の回で 1 回だけ。I19）。
最終ゲートの cross-review で止まるときは、耐久ワークフローがイベント `pause` を立てて続き（`resume`）を待ったまま
プロセスを抜ける。打ち直した `main` が続きを送り、結果ファイルが無ければ同じ止まりを番号を増やして返す。
耐久の記録の実行の鍵は `refactor-<状態の置き場の sha256 の先頭 12 字>`（置き場が求まらなければ
`<作業ディレクトリ>#<PR>`）。done で終わり、状態ファイルが残る実行の回は、打ち直すと記録した結果をそのまま返す。

止まるときの JSON の形と終了コードの表はライブラリの `scripts/lib/drive_pause.py` にある（使うのは 23 だけ。中断の `metrics.exit` が 4 なら refactor.py の中断）。
子の起動・KEY=VALUE の読み取り・最終ステータスの決定は `scripts/lib/loop_drive.py` にある。
件数（metrics）は状態ファイルから数える: items / adopted / reverted / deferred / fix_rounds（項目の修正の回数の和）/ final_gate。
採用（adopted）は最終ゲートが `passed` のときだけ数え、通っていなければ 0 にして残った改善項目の数を unconfirmed に出す。
unpublished は手元の HEAD が公開した地点より進んでいるか（plan-comment が判定できなければ null）。
done・stopped・pause の各出口で、結果 JSON を組む前に `refactor.py plan-comment` を 1 度だけ打ち、リファクタリング計画のコメントを書き直す（#1692）。
最終ゲートを経ずに終わった実行は完了にせず、中断（stopped・metrics.exit に最終ゲートの終了コード）で終える（#1482）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[2] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import deps  # noqa: E402

# `refactor_lib` を同じプロセスで読む（状態の置き場・origin の owner/repo）。呼び名の表は md で読む
deps.require("md", "mdtable", "durable")

import drive_pause as dp  # noqa: E402
import durable  # noqa: E402
from drive_pause import Stop  # noqa: E402
from loop_drive import call, durable_identity, parse_vars, review_status  # noqa: E402,F401  テストは `call` をこのモジュールの上で差し替える

if str(HERE) not in sys.path:
    sys.path.append(str(HERE))
from refactor_lib.vocabulary import PHASES  # noqa: E402

TOOL = "cross-refactoring-drive"
KIND = "refactor"  # 耐久の記録の種類
ORDER = PHASES  # 手順の順序の定義元は状態側の手順一覧（`refactor_lib.vocabulary.PHASES`）
# 修正の手順は検証の繰り返しの中にある。そこで止まった実行は検証から再開する（提案以降の CLI を起動し直さない）
RESUME_AS = {"fix": "verify", "final-fix": "final"}
# 監視が手順の上限で CLI を止めたときの終了コード（2 = TIMEOUT・5 = STALLED。表は monitor.py の冒頭）
MONITOR_STOPPED = (2, 5)
LOOP_LIMIT = 100  # 検証と修正・最終ゲートの繰り返しの上限。締め切りは verify が時計で見る
CR_DRIVE = HERE.parents[1] / "cross-review" / "scripts" / "drive.py"
FOCUS = (
    "項目をまたいだ整合を見る。個々の改善項目の妥当性は範囲テストで判定済みのため対象外とする。"
    "複数の項目で触った箇所の重複・打ち消し・命名の揺れ、取り消した項目の残骸、生成物と配布物の同期を確かめる"
)


def _ledger_module():
    """取り消しの判定（`refactor_lib.ledger`）。報告と同じ判定で採用を数える（I8）。"""
    if str(HERE) not in sys.path:
        sys.path.append(str(HERE))
    from refactor_lib import ledger

    return ledger


# --- 耐久ステップ（外の世界に触るものはすべてここを通す。耐久ワークフローの本体は記録から決まる値だけを読む） ---


@durable.step(name="refactor_drive.call")
def _call_step(cmd: list[str], extra_env: dict) -> tuple[int, str]:
    """子のスクリプトを 1 本打つ。環境は今のプロセスの環境に `extra_env` を足したもの（記録には足した分だけが残る）。"""
    rc, out = call(cmd, {**os.environ, **extra_env})
    return rc, out


@durable.step(name="refactor_drive.read_json")
def _read_json_step(path: str) -> tuple[str, object]:
    """JSON のファイルを読む。`("ok", 値)`・`("missing", None)`・`("invalid", None)` のどれかを返す。"""
    try:
        return "ok", json.loads(Path(path).read_text())
    except FileNotFoundError:
        return "missing", None
    except (OSError, json.JSONDecodeError):
        return "invalid", None


@durable.step(name="refactor_drive.write")
def _write_step(path: str, text: str) -> None:
    Path(path).write_text(text)


@durable.step(name="refactor_drive.unlink")
def _unlink_step(path: str) -> None:
    Path(path).unlink(missing_ok=True)


class Drive:
    def __init__(self, pr: int, init_args: list[str]):
        self.pr = pr
        self.init_args = init_args
        self.extra_env: dict = {}  # 子へ足す環境変数
        self.v: dict = {}
        self.final_rc = 1  # 最後に打った final-gate の終了コード
        self.comment_done = False  # done の出口でコメントを書き直したか（stopped へ移っても 2 度目を打たない。I8）
        self.paused = False  # main の側: 止まりで抜けるか

    def call(self, cmd: list[str]) -> tuple[int, str]:
        rc, out = _call_step(cmd, dict(self.extra_env))
        return rc, out

    def rf(self, *args: str, ok=(0,)) -> tuple[int, dict]:
        rc, out = self.call([sys.executable, str(HERE / "refactor.py"), *args])
        if rc == 4 or rc not in ok:
            raise Stop(f"refactor.py {args[0]} が終了コード {rc} で止まった", rc)
        vs = parse_vars(out)
        self.v.update(vs)
        return rc, vs

    @property
    def tmp(self) -> Path:
        return Path(self.v["TMP_DIR"])

    def known_tmp(self) -> Path | None:
        """init を打たずに、`refactor.py init` と同じ規則で状態の置き場を求める。求まらなければ None。"""
        if str(HERE) not in sys.path:
            sys.path.append(str(HERE))
        from refactor_lib import paths

        ap = argparse.ArgumentParser(add_help=False)
        ap.add_argument("--worktree-root")
        root = ap.parse_known_args(self.init_args)[0].worktree_root
        if root:
            return paths.tmp_dir_for(Path(root).resolve() / "work")
        if os.environ.get("CROSS_REFACTORING_TMP_DIR"):
            return paths.tmp_dir_for(Path())  # 環境変数が作業ディレクトリより先に効く
        return paths.default_tmp_dir(self.pr)

    def identity(self) -> str:
        """耐久の記録の実行の鍵の元。状態の置き場（求まらなければ作業ディレクトリと PR）。"""
        return durable_identity(self.known_tmp(), self.pr)

    def state_file(self) -> Path:
        return self.tmp / f"cross-refactoring-rf{self.v['ID']}-state.json"

    def state(self) -> dict:
        kind, value = _read_json_step(str(self.state_file()))
        return value if kind == "ok" and isinstance(value, dict) else {}

    def counts(self) -> dict:
        """結果 JSON の件数。4 つの件数は `ledger.tally`（コメントの件数の行と同じ集計。I1）から数える。"""
        s = self.state()
        items = s.get("items") or []
        t = _ledger_module().tally(s).as_metrics()
        # unconfirmed は結果 JSON の末尾側に置く（キーの並びを変えない）
        rest = {"unconfirmed": t.pop("unconfirmed")} if "unconfirmed" in t else {}
        c = {
            **t,
            "fix_rounds": sum(int(it.get("fix_count") or 0) for it in items),
            "final_gate": self.v.get("FINAL_GATE") or (s.get("final_gate") or {}).get("status"),
        }
        c.update(rest)
        if "PLAN_COMMENT" in self.v:
            # 未公開の改善項目があるか（決定 6）。plan-comment が判定できなければ null
            c["unpublished"] = {"1": True, "0": False}.get(self.v.get("UNPUBLISHED") or "")
        return c

    def refresh_plan_comment(self) -> None:
        """結果の出口でリファクタリング計画のコメントを書き直す（#1692 の決定 1）。

        **終了コードで分岐しない**（I5）。投稿に失敗しても結果 JSON と終了コードは変えず、1 行だけ残す。
        """
        if "TMP_DIR" not in self.v or "ID" not in self.v:
            return
        rc, out = self.call([sys.executable, str(HERE / "refactor.py"), "plan-comment", self.v["ID"]])
        self.v.update({k: v for k, v in parse_vars(out).items() if k in ("PLAN_COMMENT", "UNPUBLISHED", "PLAN_URL")})
        if rc != 0:
            print(f"⚠ リファクタリング計画のコメントを書き直せなかった（plan-comment の終了コード {rc}。結果は変えない）", file=sys.stderr)

    def todo(self, phase: str) -> bool:
        cur = self.v.get("PHASE") or "propose"
        cur = RESUME_AS.get(cur, cur)
        return ORDER.index(phase) >= ORDER.index(cur) if cur in ORDER else True

    def sh(self, what: str, cmd: list[str]) -> str:
        """子のスクリプトを打ち、非ゼロ終了なら駆動を止める。"""
        rc, out = self.call(cmd)
        if rc != 0:
            raise Stop(f"{what} が終了コード {rc} で止まった", rc)
        return out

    def monitor(self, agents: str, phase: str, stem: str) -> tuple[int, list[dict]]:
        """監視を打ち、終了コードと担当ごとの最終ステータス（1 行 1 JSON）を返す。"""
        extra = ["--timeout", self.v["PHASE_TIMEOUT"], "--stall-timeout", self.v["PHASE_TIMEOUT"]] if self.v.get("PHASE_TIMEOUT") else []
        rc, out = self.call(
            [
                sys.executable,
                str(LIB / "monitor.py"),
                self.v["ID"],
                "--agents",
                agents,
                "--tmp-dir",
                self.v["TMP_DIR"],
                "--stem-template",
                stem,
                "--phase",
                phase,
                *extra,
            ]
        )
        rows = []
        for line in out.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and "agent" in row:
                rows.append(row)
        return rc, rows

    def impl_phase(self, phase: str, impl: str | None = None, stem: str | None = None) -> None:
        """担当 1 者の工程。起動の失敗では止める。

        **監視が手順の上限で CLI を止めたとき（`timeout` / `stalled`）は止めない。** 上限での打ち切りは
        設計どおりの結末で（決定 23）、続く `merge-*` が止めたことを記録し（`note_stopped`）、
        結果なしの記録と取り消しを持つ。ここで止めると、打ち直しが同じ CLI を余裕の分だけの上限で
        起動し直して打ち切られ続け、`final-fix` では担当の途中のコミットを含む頭を最終ゲートへ渡す。

        **修正の工程（`fix` / `final-fix`）は監視のどの非ゼロ終了でも止めない。** 結果なしの取り込みが
        取り消しを持つためである。
        """
        self.v.pop("PHASE_TIMEOUT", None)
        self.rf("start-phase", self.v["ID"], phase)
        impl = impl or self.v["IMPL"]
        self.sh(f"launch-cli.sh（{impl}・{phase}）", ["bash", str(HERE / "launch-cli.sh"), impl, phase, self.v["ID"]])
        rc, _ = self.monitor(impl, phase, stem or f"{{agent}}-{phase}-rf{self.v['ID']}")
        if rc != 0 and rc not in MONITOR_STOPPED and phase not in ("fix", "final-fix"):
            raise Stop(f"monitor.py（{impl}・{phase}）が終了コード {rc} で止まった（結果なし・起動失敗・早期の異常）", rc)

    def propose(self) -> None:
        """全参加者の提案。1 者が欠けても続ける（`merge-proposals` が除く）が、全員が欠けたら止める。"""
        i = self.v["ID"]
        launched = []
        for a in self.v.get("RUNTIMES", "").split():
            rc, _ = self.call(["bash", str(HERE / "launch-cli.sh"), a, "propose", i])
            if rc == 0:
                launched.append(a)
        if not launched:
            raise Stop("提案の CLI を 1 者も起動できなかった", 1)
        rc, rows = self.monitor(",".join(launched), "propose", f"{{agent}}-propose-rf{i}")
        if rc != 0 and not any(r.get("exit_code") == 0 for r in rows):
            raise Stop(f"monitor.py（propose）が終了コード {rc} で止まり、提案を終えた参加者がいない", rc)

    # --- 進行（繰り返しは耐久ワークフロー `refactor_drive` が持つ） ---
    def start(self) -> None:
        """init（1 つの実行の回で 1 回だけ。I19）と worktree の用意。"""
        self.rf("init", str(self.pr), *self.init_args)
        if "TMP_DIR" not in self.v or "ID" not in self.v:
            raise Stop("refactor.py init が TMP_DIR / ID を返さない", 2)
        self.extra_env["CROSS_REFACTORING_TMP_DIR"] = self.v["TMP_DIR"]
        self.sh("prepare-worktrees.sh", ["bash", str(HERE / "prepare-worktrees.sh"), self.v["ID"]])

    def phases(self) -> bool:
        """提案から実装の取り込みまで。最終ゲートへ直に進むなら真を返す。"""
        i = self.v["ID"]
        go_final = False
        if self.todo("propose"):
            head = self.sh("git rev-parse HEAD", ["git", "-C", self.v["WORK"], "rev-parse", "HEAD"])
            self.sh("prepare-worktrees.sh sync", ["bash", str(HERE / "prepare-worktrees.sh"), i, "sync", head.strip()])
            # 提案の前に指標を 1 回だけ測る（#1319）。測定の失敗では止めない（4 だけが中断）。
            self.rf("measure", i, ok=(0, 1))
            self.rf("start-phase", i, "propose")
            self.propose()
            go_final = self.rf("merge-proposals", i, ok=(0, 2))[0] == 2
        if not go_final:
            go_final = self._phase_step("plan", "merge-plan")
        if not go_final and self.v.get("TESTS_NEEDED") == "1" and self.todo("add-tests"):
            go_final = self._phase_step("add-tests", "merge-tests")
        if not go_final:
            go_final = self._phase_step("implement", "merge-implement")
        return go_final

    def _phase_step(self, phase: str, merge_cmd: str) -> bool:
        """未了なら担当の工程を打ち、続けて取り込みを打つ。最終ゲートへ直に進むなら真を返す。"""
        if self.todo(phase):
            self.impl_phase(phase)
        return self.rf(merge_cmd, self.v["ID"], ok=(0, 2))[0] == 2

    def verify_round(self) -> bool:
        """検証を 1 回打ち、修正が要れば修正と取り込みまで進める。修正したなら真を返す。"""
        self.rf("verify", self.v["ID"])
        if self.v.get("VERIFY") != "fix":
            return False
        self.impl_phase("fix")
        self.rf("merge-fix", self.v["ID"])
        return True

    def merge_open_final_fix(self) -> None:
        """最終ゲートの修正の途中で止まった駆動の打ち直しは、先にその試行を取り込む（#674）。

        取り込まずに `final-gate` を打つと、担当が途中まで積んだ未検証のコミットを含む頭を判定・公開する。
        結果なしで閉じた試行は `merge-final-fix` が 2 で返す（`already_closed`）ため、2 でも進む。"""
        gate = self.state().get("final_gate") or {}
        if gate.get("status") == "failing" and gate.get("impl") and gate.get("fix_base_sha"):
            self.rf("merge-final-fix", self.v["ID"], ok=(0, 2))

    def final_gate(self) -> bool:
        """最終ゲートを 1 回打つ。判定が出た（通った・落ちた）なら真、修正か確かめ直しを経てもう一度打つなら偽を返す。"""
        i = self.v["ID"]
        self.v.pop("FINAL_GATE", None)
        rc, _ = self.rf("final-gate", i, ok=(0, 1, 2))
        self.final_rc = rc
        if rc in (0, 1):
            return True
        if self.v.get("FINAL_GATE") == "recheck":
            # 寄せた危険フラグの項目を取り消しただけで、修正の依頼ではない。修正の CLI を起動せずに確かめ直す。
            return False
        self.impl_phase("final-fix", self.v.get("FINAL_FIX_IMPL"), "{agent}-final-fix")
        self.rf("merge-final-fix", i)
        return False

    def report(self) -> Path:
        _, out = self.call([sys.executable, str(HERE / "refactor.py"), "report", self.v["ID"]])
        rp = self.tmp / f"drive-rf{self.v['ID']}-report.md"
        _write_step(str(rp), out)
        return rp

    def done(self, extra: dict | None = None) -> dict:
        self.refresh_plan_comment()
        self.comment_done = True
        c = {**self.counts(), **(extra or {})}
        if "unconfirmed" in c:
            raise Stop(f"最終ゲートを経ていないため、残った改善項目 {c['unconfirmed']} 件は採用と確定していない", self.final_rc)
        return dp.done(
            TOOL,
            f"改修計画の実行が終わった（項目 {c['items']}・採用 {c['adopted']}・取り消し {c['reverted']}・見送り {c['deferred']}）",
            self.report(),
            c,
        )

    def stopped(self, e: Stop) -> dict:
        if not self.comment_done:
            self.refresh_plan_comment()
        return dp.stopped(TOOL, str(e), self.counts() if "TMP_DIR" in self.v and "ID" in self.v else {}, e.code)

    def review_file(self) -> Path:
        return self.tmp / f"drive-rf{self.v['ID']}-cross-review.json"

    def pause_review(self, res: Path) -> dict:
        pf = self.tmp / f"drive-rf{self.v['ID']}-cross-review-prompt.md"
        cmd = f"python3 {CR_DRIVE} {self.pr} --focus {shlex.quote(FOCUS)}"
        _write_step(
            str(pf),
            f"""最終ゲート: Pull Request #{self.pr} 全体を cross-review で承認収束にかける。

1. `{cmd}` を打ち、結果 JSON の status を見る（gate なら items[0] の prompt_file の指示を行い、同じコマンドを打ち直す）
2. 終わったら、cross-review の状態ファイル（`<cross-review の作業ツリー>/.cross_review/cross-review-pr{self.pr}-state.json`）から
   最終ステータスを決める: final が approved で sweep.verified が true・sweep.remaining_open が 0・
   sweep.commit が null なら approved、それ以外は final の値
3. 結果ファイルへ `{{"review_status": "<最終ステータス>"}}` を書く: {res}
""",
        )
        return dp.pause(TOOL, "cross-review", pf, res, 0, self.counts(), command=cmd)

    def review_result(self, res: Path) -> str | None:
        """最終ゲートの cross-review の結果ファイルの最終ステータス。まだ書かれていなければ None。"""
        kind, value = _read_json_step(str(res))
        if kind == "missing":
            return None
        if kind == "invalid":
            raise Stop(f"{res} を読めない", 2)
        return (value.get("review_status") if isinstance(value, dict) else None) or "unknown"

    # --- main の側（耐久の記録を開き、耐久ワークフローを始めるか続ける） ---
    def run(self) -> dict:
        identity = self.identity()
        try:
            opened = durable.launched()
            if opened is None or opened.path != durable.record_path(KIND, identity):
                if opened is not None:
                    durable.close()
                durable.launch(KIND, identity)
        except durable.DurableError as exc:
            raise Stop(str(exc), 1) from None
        ref = durable.resolve(durable.launch_key(KIND, identity), finished=finished)
        if ref.action == "done":
            return ref.output["result"]
        after = durable.resume_paused(ref)
        wid = durable.start(ref, refactor_drive, self.pr, self.init_args)
        out = durable.wait(wid, "pause", after=after)
        if out.kind == "event":
            self.paused = True
            return out.value["result"]
        if out.kind == "done":
            return out.value["result"]
        raise Stop(f"耐久ワークフロー {wid} が {out.kind} で終わった: {out.value}", 1)


def finished(out: object) -> bool:
    """done で終わり、状態ファイルがまだある実行の回は、打ち直しても流し直さない。"""
    if not isinstance(out, dict):
        return False
    return (out.get("result") or {}).get("status") == "ok" and Path(out.get("state_file") or "").is_file()


@durable.workflow(name="refactor_drive")
def refactor_drive(pr: int, init_args: list[str]) -> dict:
    """1 回の改修計画の実行。出力は `{"result": <結果 JSON>, "state_file": <状態ファイル>}`。"""
    d = Drive(pr, init_args)
    try:
        d.start()
        go_final = d.phases()
        for _ in range(LOOP_LIMIT):
            if go_final or not d.verify_round():
                break
        d.merge_open_final_fix()
        for _ in range(LOOP_LIMIT):
            if d.final_gate():
                break
        if d.v.get("FINAL_GATE") == "cross-review":
            res = d.review_file()
            _unlink_step(str(res))
            d.refresh_plan_comment()  # 続きを待つ前の 1 度（pause の結果の出口）
            status = None
            for seq in itertools.count(1):
                msg = durable.pause(seq, result=d.pause_review(res), code=dp.PAUSE_CODES["cross-review"])
                if msg is None:
                    raise Stop("最終ゲートの cross-review の続きを待つ上限を過ぎた", 1)
                status = d.review_result(res)
                if status is not None:
                    break
            d.rf("finalize", d.v["ID"], "--review-status", status)
            result = d.done({"review_status": status})
        else:
            d.rf("finalize", d.v["ID"])
            result = d.done()
    except Stop as e:
        result = d.stopped(e)
    return {"result": result, "state_file": str(d.state_file()) if "TMP_DIR" in d.v and "ID" in d.v else ""}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", type=int)
    a, rest = ap.parse_known_args(argv)
    d = Drive(a.pr, rest)
    try:
        dp.main(TOOL, d.run, dict)
    except SystemExit as e:
        if d.paused:
            # 耐久ワークフローは続きを待ったまま残す（DBOS の後始末で待たない）
            durable.exit_leaving_pending(int(e.code or 0))
        durable.close()
        raise


if __name__ == "__main__":
    main()
