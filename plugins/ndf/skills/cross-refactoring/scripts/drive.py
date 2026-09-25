#!/usr/bin/env python3
"""drive.py: cross-refactoring の 1 回の計画実行を、LLM の判断が要る地点まで進めて止まる。

    drive.py <PR> --scope ... --baseline-test CMD [refactor.py init の引数...]

init → 提案 → 計画 → テスト追加 → 実装 → 検証と修正 → 最終ゲート → finalize を順に進める。
参加者は全て CLI なので、止まるのは単独起動の最終ゲート（cross-review）だけである。
同じコマンドを打ち直すと続きから進む（init が終わったフェーズを返し、最終ゲートの後の進みは
`$TMP_DIR/drive-rf<ID>.json`）。

止まるときの JSON の形と終了コードの表は共通層の `scripts/lib/drive_pause.py` にある（使うのは 23 だけ。中断の `metrics.exit` が 4 なら refactor.py の中断）。
件数（metrics）は状態ファイルから数える: items / adopted / reverted / deferred / fix_rounds（項目の修正の回数の和）/ final_gate。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[2] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import drive_pause as dp  # noqa: E402
from drive_pause import Stop  # noqa: E402

TOOL = "cross-refactoring-drive"
ORDER = ("propose", "plan", "add-tests", "implement", "verify", "final", "done")
CR_DRIVE = HERE.parents[1] / "cross-review" / "scripts" / "drive.py"
FOCUS = ("項目をまたいだ整合を見る。個々の改善項目の妥当性は範囲テストで判定済みのため対象外とする。"
         "複数の項目で触った箇所の重複・打ち消し・命名の揺れ、取り消した項目の残骸、生成物と配布物の同期を確かめる")


def call(cmd: list[str], env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)
    if p.stderr:
        sys.stderr.write(p.stderr)
    return p.returncode, p.stdout


def parse_vars(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        for w in words:
            k, sep, v = w.partition("=")
            if sep and k.isidentifier():
                out[k] = v
    return out


def review_status(state: dict) -> str:
    """cross-review の状態ファイルから finalize へ渡す最終ステータスを決める。"""
    sw = state.get("sweep") or {}
    if (state.get("final") == "approved" and sw.get("verified") is True
            and (sw.get("remaining_open") or 0) == 0 and sw.get("commit") is None):
        return "approved"
    if state.get("final") == "approved":
        return "unverified"  # 最後の HEAD（スイープの修正・残り・未検証）は承認されていない
    return state.get("final") or "unknown"


class Drive:
    def __init__(self, pr: int, init_args: list[str]):
        self.pr = pr
        self.init_args = init_args
        self.env = dict(os.environ)
        self.v: dict = {}

    def rf(self, *args: str, ok=(0,)) -> tuple[int, dict]:
        rc, out = call([sys.executable, str(HERE / "refactor.py"), *args], self.env)
        if rc == 4 or rc not in ok:
            raise Stop(f"refactor.py {args[0]} が終了コード {rc} で止まった", rc)
        vs = parse_vars(out)
        self.v.update(vs)
        return rc, vs

    @property
    def tmp(self) -> Path:
        return Path(self.v["TMP_DIR"])

    def ds_path(self) -> Path:
        return self.tmp / f"drive-rf{self.v['ID']}.json"

    def state(self) -> dict:
        try:
            return json.loads((self.tmp / f"cross-refactoring-rf{self.v['ID']}-state.json").read_text())
        except (OSError, json.JSONDecodeError, KeyError):
            return {}

    def counts(self) -> dict:
        s = self.state()
        items = s.get("items") or []
        by = {}
        for it in items:
            by[it.get("status")] = by.get(it.get("status"), 0) + 1
        return {"items": len(items), "adopted": by.get("verified", 0), "reverted": by.get("reverted", 0),
                "deferred": by.get("deferred", 0),
                "fix_rounds": sum(int(it.get("fix_count") or 0) for it in items),
                "final_gate": self.v.get("FINAL_GATE") or (s.get("final_gate") or {}).get("status")}

    def todo(self, phase: str) -> bool:
        cur = self.v.get("PHASE") or "propose"
        return ORDER.index(phase) >= ORDER.index(cur) if cur in ORDER else True

    def monitor(self, agents: str, phase: str, stem: str) -> None:
        extra = ["--timeout", self.v["PHASE_TIMEOUT"], "--stall-timeout", self.v["PHASE_TIMEOUT"]] \
            if self.v.get("PHASE_TIMEOUT") else []
        call([sys.executable, str(LIB / "monitor.py"), self.v["ID"], "--agents", agents, "--tmp-dir",
              self.v["TMP_DIR"], "--stem-template", stem, "--phase", phase, *extra], self.env)

    def impl_phase(self, phase: str, impl: str | None = None, stem: str | None = None) -> None:
        self.v.pop("PHASE_TIMEOUT", None)
        self.rf("start-phase", self.v["ID"], phase)
        impl = impl or self.v["IMPL"]
        call(["bash", str(HERE / "launch-cli.sh"), impl, phase, self.v["ID"]], self.env)
        self.monitor(impl, phase, stem or f"{{agent}}-{phase}-rf{self.v['ID']}")

    # --- 進行 ---
    def phases(self) -> None:
        i = self.v["ID"]
        go_final = False
        if self.todo("propose"):
            _, head = call(["git", "-C", self.v["WORK"], "rev-parse", "HEAD"])
            call(["bash", str(HERE / "prepare-worktrees.sh"), i, "sync", head.strip()], self.env)
            self.rf("start-phase", i, "propose")
            for a in self.v.get("RUNTIMES", "").split():
                call(["bash", str(HERE / "launch-cli.sh"), a, "propose", i], self.env)
            self.monitor(self.v.get("RUNTIMES_CSV", ""), "propose", f"{{agent}}-propose-rf{i}")
            go_final = self.rf("merge-proposals", i, ok=(0, 2))[0] == 2
        if not go_final:
            if self.todo("plan"):
                self.impl_phase("plan")
            go_final = self.rf("merge-plan", i, ok=(0, 2))[0] == 2
        if not go_final and self.v.get("TESTS_NEEDED") == "1" and self.todo("add-tests"):
            self.impl_phase("add-tests")
            go_final = self.rf("merge-tests", i, ok=(0, 2))[0] == 2
        if not go_final:
            if self.todo("implement"):
                self.impl_phase("implement")
            go_final = self.rf("merge-implement", i, ok=(0, 2))[0] == 2
        for _ in range(100):  # 検証と修正の繰り返し。締め切りは verify が時計で見る
            if go_final:
                break
            self.rf("verify", i)
            if self.v.get("VERIFY") != "fix":
                break
            self.impl_phase("fix")
            self.rf("merge-fix", i)

    def final_gate(self) -> None:
        i = self.v["ID"]
        for _ in range(100):
            rc, _ = self.rf("final-gate", i, ok=(0, 1, 2))
            if rc in (0, 1):
                return
            self.impl_phase("final-fix", self.v.get("FINAL_FIX_IMPL"), "{agent}-final-fix")
            self.rf("merge-final-fix", i)

    def report(self) -> Path:
        _, out = call([sys.executable, str(HERE / "refactor.py"), "report", self.v["ID"]], self.env)
        rp = self.tmp / f"drive-rf{self.v['ID']}-report.md"
        rp.write_text(out)
        return rp

    def done(self, extra: dict | None = None) -> dict:
        c = {**self.counts(), **(extra or {})}
        return dp.done(TOOL, f"計画の実行が終わった（項目 {c['items']}・採用 {c['adopted']}・"
                       f"取り消し {c['reverted']}・見送り {c['deferred']}）", self.report(), c)

    def run(self) -> dict:
        self.rf("init", str(self.pr), *self.init_args)
        if "TMP_DIR" not in self.v or "ID" not in self.v:
            raise Stop("refactor.py init が TMP_DIR / ID を返さない", 2)
        self.env["CROSS_REFACTORING_TMP_DIR"] = self.v["TMP_DIR"]
        try:
            ds = json.loads(self.ds_path().read_text())
        except (OSError, json.JSONDecodeError):
            ds = {}
        res = self.tmp / f"drive-rf{self.v['ID']}-cross-review.json"
        if ds.get("stage") == "cross-review":
            if not res.is_file():
                return self.pause_review(res)
            try:
                status = json.loads(res.read_text()).get("review_status") or "unknown"
            except json.JSONDecodeError:
                raise Stop(f"{res} を読めない", 2)
            self.rf("finalize", self.v["ID"], "--review-status", status)
            self.ds_path().write_text(json.dumps({"stage": "done", "review_status": status}))
            return self.done({"review_status": status})
        if ds.get("stage") == "done":
            return self.done({"review_status": ds.get("review_status")})
        call(["bash", str(HERE / "prepare-worktrees.sh"), self.v["ID"]], self.env)
        self.phases()
        self.final_gate()
        if self.v.get("FINAL_GATE") == "cross-review":
            res.unlink(missing_ok=True)
            self.ds_path().write_text(json.dumps({"stage": "cross-review"}))
            return self.pause_review(res)
        self.rf("finalize", self.v["ID"])
        self.ds_path().write_text(json.dumps({"stage": "done"}))
        return self.done()

    def pause_review(self, res: Path) -> dict:
        pf = self.tmp / f"drive-rf{self.v['ID']}-cross-review-prompt.md"
        cmd = f"python3 {CR_DRIVE} {self.pr} --focus {shlex.quote(FOCUS)}"
        pf.write_text(f"""最終ゲート: Pull Request #{self.pr} 全体を cross-review で承認収束にかける。

1. `{cmd}` を打ち、結果 JSON の status を見る（gate なら items[0] の prompt_file の指示を行い、同じコマンドを打ち直す）
2. 終わったら、cross-review の状態ファイル（`<cross-review の作業ツリー>/.cross_review/cross-review-pr{self.pr}-state.json`）から
   最終ステータスを決める: final が approved で sweep.verified が true・sweep.remaining_open が 0・
   sweep.commit が null なら approved、それ以外は final の値
3. 結果ファイルへ `{{"review_status": "<最終ステータス>"}}` を書く: {res}
""")
        return dp.pause(TOOL, "cross-review", pf, res, 0, self.counts(), command=cmd)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", type=int)
    a, rest = ap.parse_known_args(argv)
    d = Drive(a.pr, rest)
    dp.main(TOOL, d.run, lambda: d.counts() if "TMP_DIR" in d.v and "ID" in d.v else {})


if __name__ == "__main__":
    main()
