"""担当の工程の起動と、結果を残さなかった担当の振り替え（drive.py の一部。#919）。

`Drive` が `launch`（担当の CLI の起動）・`monitor`・`rf` を持ち、ここはその上で「起動 → 監視 → 結果なしなら
`refactor.py reassign` の答えに従う」の繰り返しだけを持つ。起動し直すか・誰へ振り替えるかは規則
（`assignment.after_no_result`）が決め、ここは返された担当で同じ工程を起動し直すだけである。
回数は数えない。起動し直しは担当ごとに 1 度、振り替えは候補が尽きれば `reassign` が 3 を返す（記録が止める）。
"""

from __future__ import annotations

from drive_pause import Stop

# 監視が手順の上限で CLI を止めたときの終了コード（2 = TIMEOUT・5 = STALLED。表は monitor.py の冒頭）
MONITOR_STOPPED = (2, 5)
FIX_PHASES = ("fix", "final-fix")  # 修正の工程
LOOP_LIMIT = 100  # 検証と修正・最終ゲートの繰り返しの上限。締め切りは verify が時計で見る


class AssigneeLaunch:
    """`Drive` に混ぜる。`self.v`・`self.rf`・`self.monitor`・`self.launch` を使う。"""

    def start_phase(self, phase: str) -> None:
        """起動の直前に `start-phase` を打つ。起動し直すたびに打ち、工程の終わりの時刻から残りの上限を出し直す。

        工程の起点（`started_at`・`base_sha`）は最初の 1 回だけ記録され、締め切りは振り替えの後も延びない。
        """
        self.v.pop("PHASE_TIMEOUT", None)
        self.rf("start-phase", self.v["ID"], phase)

    def impl_phase(self, phase: str, impl: str | None = None, stem: str | None = None) -> None:
        """担当 1 者の工程。起動の失敗では止める。

        **監視が手順の上限で CLI を止めたとき（`timeout` / `stalled`）は振り替えない。** 上限での打ち切りは
        設計どおりの結末で（決定 23）、続く `merge-*` が止めたことを記録し（`note_stopped`）、結果なしの記録と取り消しを持つ。

        それ以外の非ゼロでは `reassign` を打つ。7 なら返された担当で同じ工程を起動し直し、2（修正の工程）は担当を
        替えて取り込みへ、3（振り替え先が無い）は `plan` / `add-tests` / `implement` では止まり、修正の工程では取り込みへ進む。
        """
        impl = impl or self.v["IMPL"]
        for _ in range(LOOP_LIMIT):
            self.start_phase(phase)
            if (lrc := self.launch(impl, phase)) != 0:
                raise Stop(f"launch-cli.sh（{impl}・{phase}）が終了コード {lrc} で止まった", lrc)
            rc, _ = self.monitor(impl, phase, stem or f"{{agent}}-{phase}-rf{self.v['ID']}")
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
