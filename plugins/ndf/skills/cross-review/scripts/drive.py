#!/usr/bin/env python3
"""drive.py: cross-review の収束ループを、LLM の判断が要る地点まで進めて止まる。

    drive.py <PR> [--rotate-mode light|squash] [state.py init の引数...]

init → ラウンド（起動・監視・取り込み・根拠の検証・判定）→ 振動の検知 → 修正 → 巻き直し →
最終スイープ → 検証 → 報告を順に進める。LLM が要る地点では 1 行の JSON（`step_result` の形）を出して
止まる。同じコマンドを打ち直すと続きから進む（進みは `$TMP_DIR/drive-pr<PR>.json`）。

    {"tool": "cross-review-drive", "status": "gate", "next": "fix",
     "items": [{"pause": "fix", "prompt_file": "...", "result_file": "...", "round": 3}], ...}

| 終了コード | status | 意味 | 起こす側がすること |
| --- | --- | --- | --- |
| 0 | ok | 完了（報告は items[0].report） | 件数（metrics）を報告へ写す |
| 20 | gate | fix 待ち | prompt_file の指示で直し、result_file を書かせて打ち直す |
| 21 | gate | sweep 待ち | 同上（最終スイープ） |
| 22 | gate | 新しい title・body 待ち | 同上（light の巻き直し） |
| 23 | gate | 最終ゲートの cross-review 待ち | cross-refactoring だけが使う |
| 1 | stopped | 中断（metrics.exit に元の終了コード） | 理由（summary）を報告する |

件数（metrics）は状態ファイルから数える: rounds / prs / findings / fixed / deferred / rejected /
unresolved / final / review_status。
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
SKILL = HERE.parent
LIB = HERE.parents[2] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import step_result as sr  # noqa: E402

TOOL = "cross-review-drive"
PAUSES = {"fix": 20, "sweep": 21, "newtext": 22, "cross-review": 23}
DOCS02 = SKILL / "docs" / "02-fix-and-rotation.md"
FIX_SKILL = HERE.parents[1] / "fix" / "SKILL.md"


class Stop(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def call(cmd: list[str], env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    """スクリプトを 1 本実行し、終了コードと標準出力を返す。標準エラーはそのまま流す。"""
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
    """最後の HEAD が承認されたなら approved、それ以外は final の値（cross-refactoring の finalize が読む）。"""
    sw = state.get("sweep") or {}
    if (state.get("final") == "approved" and sw.get("verified") is True
            and (sw.get("remaining_open") or 0) == 0 and sw.get("commit") is None):
        return "approved"
    if state.get("final") == "approved":
        return "unverified"  # 最後の HEAD（スイープの修正・残り・未検証）は承認されていない
    return state.get("final") or "unknown"


class Drive:
    def __init__(self, pr: int, rotate_mode: str, init_args: list[str]):
        self.pr = pr
        self.rotate_mode = rotate_mode
        self.init_args = init_args
        self.env = dict(os.environ)
        self.v: dict = {}

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
        return self.tmp / f"{kind}-pr{self.pr}-result.json" if kind in ("fix", "sweep") else \
            self.tmp / f"rotate-pr{self.pr}-{kind}.json"

    def ds_path(self) -> Path:
        return self.tmp / f"drive-pr{self.pr}.json"

    def load_ds(self) -> dict:
        try:
            return json.loads(self.ds_path().read_text())
        except (OSError, json.JSONDecodeError):
            return {"stage": "round", "rotate_mode": self.rotate_mode}

    def save_ds(self, ds: dict) -> None:
        self.ds_path().write_text(json.dumps(ds, ensure_ascii=False))

    def state(self) -> dict:
        try:
            return json.loads((self.tmp / f"cross-review-pr{self.pr}-state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def counts(self) -> dict:
        s = self.state()
        rounds = s.get("rounds") or []
        findings = fixed = rejected = 0
        for r in rounds:
            for who in r.get("reviewers") or []:
                findings += int((r.get(who) or {}).get("comments") or 0)
            fx = r.get("fix") or {}
            fixed += int(fx.get("fixed") or 0)
            rejected += int(fx.get("rejected") or 0)
        sweep = s.get("sweep") or {}
        return {"rounds": len(rounds), "prs": len(s.get("pr_history") or []) or 1, "findings": findings,
                "fixed": fixed, "deferred": len(s.get("deferred_nits") or []), "rejected": rejected,
                "unresolved": sweep.get("remaining_open"), "final": s.get("final"),
                "review_status": review_status(s)}

    # --- 止まる ---
    def pause(self, ds: dict, kind: str, prompt: str) -> dict:
        res = self.path(kind)
        if ds.get("stage") != kind:
            res.unlink(missing_ok=True)  # 前の実行の結果を読まない
            ds["stage"] = kind
            self.save_ds(ds)
        pf = self.tmp / f"drive-pr{self.pr}-{kind}-prompt.md"
        pf.write_text(prompt)
        rnd = self.counts()["rounds"]
        item = {"pause": kind, "prompt_file": str(pf), "result_file": str(res), "round": rnd}
        return sr.result(TOOL, "gate", f"{kind} 待ち（round {rnd}）。prompt_file の指示で result_file を書き、"
                         "同じコマンドを打ち直す", [item], self.counts(), next=kind)

    # --- プロンプト（手順は写さず、呼び出しと PR 固有の穴埋めだけ） ---
    def fix_prompt(self) -> str:
        s = self.state()
        r = (s.get("rounds") or [{}])[-1]
        pr = s.get("current_pr") or self.pr
        reviews = "\n".join(
            f"  - {who}: intent={(r.get(who) or {}).get('intent')}, posted_as={(r.get(who) or {}).get('posted_as')}, "
            f"{(r.get(who) or {}).get('comments', 0)} 件, {(r.get(who) or {}).get('review_url', '')}"
            for who in r.get("reviewers") or []) or "  - 無し"
        return f"""`/ndf:fix {pr} --defer-nit` を行う。手順は {FIX_SKILL} にある。
方針・必須の手順・戻り値の形は {DOCS02} の「Step 5」にある。

- リポジトリ: {s.get('repo')}
- PR: #{pr}（round {r.get('round')}）
- 作業ディレクトリ: {s.get('worktree_path')}（外を触らない）
- ブランチ: {s.get('head_branch')} / ベース: {s.get('base_branch')}
- 前ラウンドのレビュー（件数はそのラウンドで投稿した数。対象は reviewThreads を数え直して決める）:
{reviews}
- 既存コメントの控え: {self.tmp}/cross-review-pr{self.pr}-existing-comments.txt

コミットまでで、送らない。GitHub へ書かない（送信・返信・決着は取り込みが行う）。
戻り値ファイル: {self.path('fix')}
"""

    def sweep_prompt(self) -> str:
        s = self.state()
        pr = s.get("current_pr") or self.pr
        return f"""最終スイープを行う: `/ndf:fix {pr}` を再実行し、PR の open review thread を 1 件も残さない。
手順は {FIX_SKILL}、スイープの規約と結果ファイルの形は {DOCS02} の「Step 7.5」にある。

- リポジトリ: {s.get('repo')}
- PR: #{pr}
- 作業ディレクトリ: {s.get('worktree_path')}（外を触らない）
- ループの終わり: {s.get('final')}

GitHub と git の送信をしない。結果ファイル: {self.path('sweep')}
"""

    def newtext_prompt(self) -> str:
        s = self.state()
        return f"""light の巻き直しで作る新しい Pull Request の title / body を書く。
書いてよいこと・書いてはいけないことと出力の形は {DOCS02} の「Step 6b」にある。

- 素材: {self.path('prepare')}
- 作業ディレクトリ: {s.get('worktree_path')}（読むだけ）

出力ファイル（JSON: {{"title": ..., "body": ...}}）: {self.path('newtext')}
"""

    # --- 段 ---
    def init(self) -> None:
        out = self.must(call([sys.executable, str(HERE / "state.py"), "init", str(self.pr), *self.init_args],
                             self.env), "state.py init")
        self.v.update(parse_vars(out))
        if "TMP_DIR" not in self.v:
            raise Stop("state.py init が TMP_DIR を返さない", 2)
        self.env["CROSS_REVIEW_TMP_DIR"] = self.v["TMP_DIR"]

    def review_round(self) -> str:
        """1 ラウンド。戻り値: fix / done。"""
        rc, out = self.st("start-round", str(self.pr))
        if rc == 1:
            return "done"
        if rc != 0:
            raise Stop(f"state.py start-round が終了コード {rc} で止まった", rc)
        rv = parse_vars(out)
        rnd = rv.get("ROUND", "")
        agents = rv.get("REVIEWERS", "").split()
        relaunched = False
        while True:
            for a in agents:
                self.sh("launch-reviewer.sh", a, str(self.pr), rnd)
            call([sys.executable, str(HERE / "monitor.py"), str(self.pr), "--phase", "review", "--agents",
                  ",".join(agents)], self.env, self.v.get("WORKTREE"))
            for a in agents:
                self.st("read-result", str(self.pr), a)
            self.st("verify-findings", str(self.pr))
            self.sh("critique-round.sh", str(self.pr), rnd, *rv.get("REVIEWERS", "").split())
            jrc, jout = self.st("judge", str(self.pr))
            if jrc == 8:
                self.st("flush", str(self.pr))
                jrc, jout = self.st("judge", str(self.pr))
            if jrc == 7 and not relaunched:
                agents = parse_vars(jout).get("RELAUNCH_AGENTS", "").split()
                relaunched = True
                if agents:
                    continue
            break
        if jrc == 0:
            return "done"
        if jrc != 2:
            raise Stop(f"state.py judge が終了コード {jrc} で止まった", jrc)
        orc, _ = self.st("check-oscillation", str(self.pr))
        if orc == 4:
            return "done"  # final = oscillation。最終スイープへ
        if orc not in (0, 2):
            raise Stop(f"state.py check-oscillation が終了コード {orc} で止まった", orc)
        return "fix"

    def after_fix(self, ds: dict) -> dict | None:
        """修正の取り込みと巻き直し。止まるなら pause の結果を返す。"""
        rc, _ = self.st("merge-fix", str(self.pr))
        if rc == 3:
            ds["stage"] = "sweep-start"  # final = error。最終スイープへ
            return None
        if rc != 0:
            raise Stop(f"state.py merge-fix が終了コード {rc} で止まった", rc)
        ds["stage"] = "round"
        rrc, _ = self.st("should-rotate", str(self.pr))
        if rrc != 0:
            return None
        self.must(self.sh("rotate-pr.sh", "prepare", str(self.pr)), "rotate-pr.sh prepare")
        if ds.get("rotate_mode", "light") == "light":
            return self.pause(ds, "newtext", self.newtext_prompt())
        return self.rotate_execute(ds)

    def rotate_execute(self, ds: dict) -> None:
        out = self.must(self.sh("rotate-pr.sh", "execute", str(self.pr), "--mode", ds.get("rotate_mode", "light")),
                        "rotate-pr.sh execute")
        rv = parse_vars(out)
        self.must(self.st("set-current-pr", str(self.pr), rv.get("NEW_PR", ""), "--head-branch",
                          rv.get("NEW_BRANCH", "")), "state.py set-current-pr")
        ds["stage"] = "round"
        return None

    def finish(self, ds: dict) -> dict:
        s = self.state()
        pr = str(s.get("current_pr") or self.pr)
        self.must(call([sys.executable, str(LIB / "result_posts.py"), "fix", "--pr", pr, "--result",
                        str(self.path("sweep")), "--worktree", str(self.v.get("WORKTREE", ""))], self.env),
                  "result_posts.py fix")
        self.must(self.st("verify-sweep", str(self.pr)), "state.py verify-sweep", ok=(0, 6))
        _, rep = self.st("report", str(self.pr))
        rp = self.tmp / f"drive-pr{self.pr}-report.md"
        rp.write_text(rep)
        ds["stage"] = "done"
        self.save_ds(ds)
        return self.done(rp)

    def done(self, rp: Path) -> dict:
        c = self.counts()
        return sr.result(TOOL, "ok", f"収束ループが終わった（final={c['final']}・{c['rounds']} ラウンド・"
                         f"指摘 {c['findings']}・未解決 {c['unresolved']}）", [{"report": str(rp)}], c)

    def run(self) -> dict:
        self.init()
        ds = self.load_ds()
        for _ in range(1000):
            stage = ds.get("stage", "round")
            if stage == "done":
                return self.done(self.tmp / f"drive-pr{self.pr}-report.md")
            if stage == "fix":
                if not self.path("fix").is_file():
                    return self.pause(ds, "fix", self.fix_prompt())
                paused = self.after_fix(ds)
                self.save_ds(ds)
                if paused:
                    return paused
                continue
            if stage == "newtext":
                if not self.path("newtext").is_file():
                    return self.pause(ds, "newtext", self.newtext_prompt())
                self.rotate_execute(ds)
                self.save_ds(ds)
                continue
            if stage in ("sweep", "sweep-start"):
                if stage == "sweep" and self.path("sweep").is_file():
                    return self.finish(ds)
                return self.pause(ds, "sweep", self.sweep_prompt())
            nxt = self.review_round()
            if nxt == "fix":
                return self.pause(ds, "fix", self.fix_prompt())
            ds["stage"] = "sweep-start"
            self.save_ds(ds)
        raise Stop("段の数が上限を超えた", 1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("--rotate-mode", choices=["light", "squash"], default="light")
    a, rest = ap.parse_known_args(argv)
    d = Drive(a.pr, a.rotate_mode, rest)
    try:
        out = d.run()
    except Stop as e:
        m = d.counts() if "TMP_DIR" in d.v else {}
        sr.emit(sr.result(TOOL, "stopped", str(e), [], {**m, "exit": e.code}), 1)
    code = PAUSES.get(out.get("next"), 0) if out["status"] == "gate" else 0
    sr.emit(out, code)


if __name__ == "__main__":
    main()
