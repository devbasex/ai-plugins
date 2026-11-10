"""担当の工程の起動と、結果を残さなかった担当の振り替え（drive.py の一部。#919）。

`Drive` が `launch`（担当の CLI の起動）・`monitor`・`rf` を持ち、ここはその上で「起動 → 監視 → 結果なしなら
`refactor.py reassign` の答えに従う」の繰り返しだけを持つ。起動し直すか・誰へ振り替えるかは規則
（`assignment.after_no_result`）が決め、ここは返された担当で同じ工程を起動し直すだけである。
回数は数えない。起動し直しは担当ごとに 1 度、振り替えは候補が尽きれば `reassign` が 3 を返す（記録が止める）。
"""

from __future__ import annotations

import time
from pathlib import Path

from drive_pause import Stop

# 監視が手順の上限で CLI を止めたときの終了コード（2 = TIMEOUT・5 = STALLED。表は monitor.py の冒頭）
MONITOR_STOPPED = (2, 5)
FIX_PHASES = ("fix", "final-fix")  # 修正の工程
LOOP_LIMIT = 100  # 検証と修正・最終ゲートの繰り返しの上限（暴走の止め）。修正を続けるかは verify の前進の判定が決める
CODING_PHASES = ("add-tests", "implement", "fix", "final-fix", "flaky-fix")  # 安全網に数える手順（#2457 決定 5）


class Halt(Stop):
    """止めて人へ渡す（安全網の上限・打ち消しの衝突・判定の担い手が居ないまま停滞。#2457）。結果は gate・終了コード 10。"""

    def __init__(self, reason: str):
        super().__init__(reason, 10)
        self.reason = reason


class AssigneeLaunch:
    """`Drive` に混ぜる。`self.v`・`self.rf`・`self.monitor`・`self.launch` を使う。"""

    def start_phase(self, phase: str) -> None:
        """起動の直前に `start-phase` を打つ。コーディングの手順は安全網の残りを上限にし、残りが無ければ止める（#2457 決定 5）。"""
        self.v.pop("PHASE_TIMEOUT", None)
        self.v.pop("HALT", None)
        self.rf("start-phase", self.v["ID"], phase)
        if self.v.get("HALT") == "1":
            raise Halt(self.v.get("HALT_REASON") or "安全網の上限に当たった")

    def coding_time(self, phase: str, seconds: float) -> None:
        """コーディングの CLI の起動 1 回の秒を安全網へ足す。

        **上限に届いてもここでは止めない。** 続く取り込み（`merge-*`）でその CLI のコミットを項目へ結んでから、
        次のコーディングの CLI の起動の前の `start_phase`（`HALT=1`）が止める（I8）。ここで止めると最後の CLI の
        コミットが項目に結ばれずに HEAD に残り、打ち直しで済んだ手順の CLI をもう 1 度起動する。"""
        if phase not in CODING_PHASES:
            return
        self.rf("coding-time", self.v["ID"], phase, "--seconds", f"{seconds:.1f}")
        self.v.pop("HALT", None)
        self.v.pop("HALT_REASON", None)

    def impl_phase(self, phase: str, impl: str | None = None, stem: str | None = None) -> None:
        """担当 1 者の工程。起動の失敗では止める。

        **監視が手順の上限で CLI を止めたとき（`timeout` / `stalled`）は振り替えない。** 続く `merge-*` が止めたことを
        記録する（`note_stopped`）。コーディングの手順は監視の秒を安全網へ足す（`coding_time`）。

        それ以外の非ゼロでは `reassign` を打つ。7 なら返された担当で同じ工程を起動し直し、2（修正の工程）は担当を
        替えて取り込みへ、3（振り替え先が無い）は `plan` / `add-tests` / `implement` では止まり、修正の工程では取り込みへ進む。
        """
        impl = impl or self.v["IMPL"]
        for _ in range(LOOP_LIMIT):
            self.start_phase(phase)
            if (lrc := self.launch(impl, phase)) != 0:
                raise Stop(f"launch-cli.sh（{impl}・{phase}）が終了コード {lrc} で止まった", lrc)
            began = time.monotonic()
            rc, _ = self.monitor(impl, phase, stem or f"{{agent}}-{phase}-rf{self.v['ID']}")
            self.coding_time(phase, time.monotonic() - began)
            if rc == 0 or rc in MONITOR_STOPPED:
                return
            rrc, vs = self.rf("reassign", self.v["ID"], phase, ok=(0, 2, 3, 7))
            if rrc == 3 and phase not in FIX_PHASES:
                raise Stop(
                    f"monitor.py（{impl}・{phase}）が終了コード {rc} で止まり、振り替え先も無い（結果なし・起動失敗・早期の異常）", rc
                )
            if rrc != 7:
                return
            impl = vs.get("IMPL") or impl

    def propose(self) -> None:
        """全参加者の提案。1 者が欠けても続ける（`merge-proposals` が除く）。全員が欠けたら `reassign` の答えで集め直し、無ければ止める。"""
        i = self.v["ID"]
        wanted = self.v.get("RUNTIMES", "").split()
        for _ in range(LOOP_LIMIT):
            self.start_phase("propose")
            launched = [a for a in wanted if self.launch(a, "propose") == 0]
            if not launched:
                raise Stop("提案の CLI を 1 者も起動できなかった", 1)
            rc, rows = self.monitor(",".join(launched), "propose", f"{{agent}}-propose-rf{i}")
            if rc == 0 or any(r.get("exit_code") == 0 for r in rows):
                return
            rrc, vs = self.rf("reassign", i, "propose", "--seats", *launched, ok=(0, 3, 7))
            if rrc == 3:
                raise Stop(f"monitor.py（propose）が終了コード {rc} で止まり、提案を終えた参加者も振り替え先もいない", rc)
            if rrc == 0:
                return
            wanted = vs.get("PROPOSERS", "").split()
            if vs.get("IMPL"):
                self.v["IMPL"] = vs["IMPL"]  # 実装担当を選び直したら、続く plan から使う

    def judge_phase(self, seat: str) -> None:
        """成立の判定を、実装担当と別のランタイムに読み取り用の作業ディレクトリで 1 回させ、答えを取り込む（決定 3）。
        起動できない・結果を残さないときも `judge` が「判定できない」として積む（初回は成立と同じに続け、判定し直しなら止める）。"""
        i = self.v["ID"]
        head = self.sh("git rev-parse HEAD", ["git", "-C", self.v["WORK"], "rev-parse", "HEAD"])
        self.call(["bash", str(Path(__file__).resolve().parent / "prepare-worktrees.sh"), i, "sync", head.strip()])
        self.start_phase("judge")
        if seat and self.launch(seat, "judge") == 0:
            self.monitor(seat, "judge", f"{{agent}}-judge-rf{i}")
        self.rf("judge", i)

    def flaky_requested(self) -> bool:
        return bool((self.state().get("flaky_request") or {}).get("tests"))

    def flaky_phase(self) -> None:
        """フレーキーと見分けたテストの直しを実装担当に依頼して取り込む（決定 9）。差し戻しは 1 回だけ起動し直す。

        振り替えはしない（直せなくても項目の合否に関わらない）。起動の秒は安全網に数える。"""
        i = self.v["ID"]
        for _ in range(2):
            self.start_phase("flaky-fix")
            impl = self.v["IMPL"]
            if self.launch(impl, "flaky-fix") == 0:
                began = time.monotonic()
                self.monitor(impl, "flaky-fix", f"{{agent}}-flaky-fix-rf{i}")
                self.coding_time("flaky-fix", time.monotonic() - began)
            self.v.pop("FLAKY_FIX", None)
            self.rf("merge-flaky-fix", i)
            if self.v.get("FLAKY_FIX") != "sendback":
                return
