#!/usr/bin/env python3
"""持ち場をスクリプトで駆動する（#827 の試作）。

supervisor（サブエージェント）の代わりに、このスクリプトが持ち場の手順を順に進める。
判断の要らない段（コマンドの実行・待ち・進行の記録）はスクリプトが行い、LLM は
次の 2 つの段でだけ、毎回新しい最小構成の `claude -p` として起動する。

| 段 | 何をするか | LLM |
| --- | --- | --- |
| run   | コマンドを実行して終わるまで待ち、出力をファイルへ残す | 使わない |
| work  | 1 つの作業（修正・調査）を worker として行わせる | 道具あり（Read/Edit/Write/Bash/Grep/Glob） |
| judge | 結果ファイルと規則の抜粋だけを渡し、次の段を決めさせる | 道具なし |
| pr    | push して Draft の Pull Request を作る。本文は計画の値・コミット・変更の統計・run の結果から組む | 使わない |

使い方:
    supervise.py run <plan.json> [--state-dir DIR]
    supervise.py example            # 計画の例を出す

計画（JSON）:
    {
      "持ち場": "検査", "課題": [818], "モード": "standard",
      "作業場所": "/abs/worktree",
      "記録": "/abs/projects-sync.sh",      # 省略可。stage を記録する
      "規則": "判断の規則の抜粋（文字列）",   # judge へ毎回渡す
      "上限": 30,                           # 実行する段の数の上限（ループの歯止め）
      "steps": [
        {"id": "test", "type": "run", "cmd": "pytest -q", "stage": "完了判定",
         "timeout": 1800, "on_fail": "judge-test", "next": "end"},
        {"id": "judge-test", "type": "judge", "inputs": ["test"],
         "question": "テストの失敗を直すか、止めるか", "choices": ["fix", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test"],
         "prompt": "失敗したテストを直してコミットする", "next": "test"}
      ]
    }

段の遷移:
- `next` に `end` を書くと、そこで持ち場を完了として終える
- run: 終了コード 0 なら `next`（無ければ次の段）。0 以外なら `on_fail`（無ければ止まる）
- work: 終了後に `next`（無ければ次の段）
- judge: 答えの `decision` が段の id ならその段へ、`next` なら次の段へ、`stop` なら止まる、
  `gate` なら関門として止まる。`choices` を渡すとその中から選ばせる

最後に `## 持ち場の報告` を標準出力と `<state-dir>/report.md` へ書く。conductor はこの
スクリプトを背景の Bash で起動し、終わりの通知で報告を読む。
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

WORK_TOOLS = "Read,Edit,Write,Bash,Grep,Glob"
TAIL = 6000  # LLM へ渡す出力の末尾の文字数

WORK_SYSTEM = """あなたは NDF の worker である。1 つの作業だけを行う。
- 人間へ問わない。別のサブエージェントを起動しない。進行を記録しない
- 作業場所の外を触らない。push しない
- 判断が要るときは、作業をせずに「結果: 判断が要る」と理由を書いて終える
- 最後に次の形で終える:
## 作業の報告
- 作業: <種類>
- 結果: 完了 / 判断が要る / できなかった
- 見つけたもの: <件数と場所。無ければ 無し>
- 次にすること: <1 行。無ければ 無し>"""

JUDGE_SYSTEM = """あなたは NDF の持ち場の判断だけを行う。道具は無い。
渡された結果と規則だけを根拠に、次の段を 1 つ選ぶ。
答えは JSON 1 つだけを返す: {"decision": "<選んだ値>", "reason": "<1 行>"}"""


def claude_cmd(system: str, tools: str | None, cwd: str) -> list[str]:
    base = shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE", "claude"))
    cmd = base + [
        "-p", "--output-format", "json", "--no-session-persistence",
        "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
        "--system-prompt", system,
    ]
    if tools:
        cmd += ["--tools", tools, "--allowed-tools", tools, "--permission-mode", "acceptEdits",
                "--add-dir", cwd]
    else:
        cmd += ["--tools", ""]
    model = os.environ.get("NDF_SUPERVISE_MODEL")
    if model:
        cmd += ["--model", model]
    return cmd


def call_claude(system: str, prompt: str, tools: str | None, cwd: str, timeout: int) -> dict:
    """最小構成の claude -p を 1 回呼び、結果の本文と使用量を返す。"""
    started = time.time()
    try:
        p = subprocess.run(claude_cmd(system, tools, cwd), input=prompt, capture_output=True,
                           text=True, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": f"打ち切り（{timeout} 秒）", "usage": {}, "seconds": timeout}
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    return {
        "ok": p.returncode == 0 and not data.get("is_error"),
        "text": data.get("result") or p.stderr[-TAIL:],
        "usage": data.get("usage") or {},
        "cost": data.get("total_cost_usd"),
        "seconds": round(time.time() - started, 1),
    }


def parse_decision(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"decision": "stop", "reason": f"判断の答えを読めない: {(text or '')[:200]}"}


class Supervisor:
    def __init__(self, plan: dict, state_dir: Path):
        self.plan = plan
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.order = [s["id"] for s in plan["steps"]]
        self.cwd = plan["作業場所"]
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict] = {}
        self.log: list[dict] = []
        self.llm = {"work": 0, "judge": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                    "output": 0, "cost": 0.0}
        self.last_stage = "無し"

    # --- 共通 ---
    def out_path(self, n: int, sid: str) -> Path:
        return self.dir / f"{n:02d}-{sid}.out"

    def record_stage(self, stage: str | None) -> None:
        if not stage or stage == self.last_stage:
            return
        self.last_stage = stage
        rec = self.plan.get("記録")
        if not rec:
            return
        for issue in self.plan.get("課題", []):
            subprocess.run(["bash", rec, str(issue), "stage", stage], cwd=self.cwd,
                           capture_output=True, text=True)

    def inputs_text(self, step: dict) -> str:
        parts = []
        for sid in step.get("inputs", []):
            r = self.results.get(sid)
            if r:
                parts.append(f"### 段 {sid}（exit={r.get('exit')}）\n{r['text'][-TAIL:]}")
        return "\n\n".join(parts) or "（入力なし）"

    def add_usage(self, kind: str, res: dict) -> None:
        u = res.get("usage", {})
        self.llm[kind] += 1
        self.llm["input"] += u.get("input_tokens", 0)
        self.llm["cache_read"] += u.get("cache_read_input_tokens", 0)
        self.llm["cache_write"] += u.get("cache_creation_input_tokens", 0)
        self.llm["output"] += u.get("output_tokens", 0)
        self.llm["cost"] += res.get("cost") or 0.0

    def next_of(self, sid: str, step: dict) -> str | None:
        if step.get("next"):
            return None if step["next"] == "end" else step["next"]
        i = self.order.index(sid)
        return self.order[i + 1] if i + 1 < len(self.order) else None

    # --- 段 ---
    def do_run(self, step: dict) -> tuple[bool, str]:
        started = time.time()
        try:
            p = subprocess.run(step["cmd"], shell=True, cwd=self.cwd, capture_output=True,
                               text=True, timeout=step.get("timeout", 3600))
            code, text = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, text = 124, f"打ち切り（{e.timeout} 秒）"
        self.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return code == 0, text

    def do_work(self, step: dict) -> tuple[bool, str]:
        prompt = (f"作業: {step.get('kind', '修正')}\n作業場所: {self.cwd}\n\n"
                  f"{step['prompt']}\n\n## 入力\n{self.inputs_text(step)}")
        res = call_claude(WORK_SYSTEM, prompt, WORK_TOOLS, self.cwd, step.get("timeout", 1800))
        self.add_usage("work", res)
        self.cur.update(exit=0 if res["ok"] else 1, text=res["text"], seconds=res["seconds"])
        return res["ok"], res["text"]

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, text=True).stdout.rstrip()

    def do_pr(self, step: dict) -> tuple[bool, str]:
        """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す。LLM を使わない。"""
        base = step.get("base", "develop")
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD")
        push = subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=self.cwd,
                              capture_output=True, text=True)
        if push.returncode != 0:
            self.cur.update(exit=push.returncode, text=push.stderr)
            return False, push.stderr
        rng = f"origin/{base}..HEAD"
        commits = self.git("log", "--reverse", "--format=- %s", rng) or "- （無し）"
        stat = self.git("diff", "--stat", rng).splitlines()
        tests = []
        for sid, r in self.results.items():
            if r.get("type") == "run":
                last = next((l for l in reversed(r.get("text", "").splitlines()) if l.strip()), "")
                tests.append(f"| {sid} | {r.get('exit')} | {last[:120]} |")
        issues = " ".join(f"#{i}" for i in self.plan.get("課題", []))
        docs = "\n".join(f"- `{d}`" for d in step.get("docs", [])) or "- 無し"
        title = step.get("title") or (self.git("log", "--reverse", "--format=%s", rng).splitlines() or [branch])[0]
        body = f"""{step.get('summary', '')}

## 課題と設計

- 課題: {issues}
{docs}

## コミット

{commits}

## 変更の統計

```text
{chr(10).join(stat[-15:])}
```

## テスト（supervise.py の run の段）

| 段 | exit | 最後の行 |
| --- | ---: | --- |
{chr(10).join(tests) or '| 無し | | |'}

🤖 Generated with [Claude Code](https://claude.com/claude-code)
"""
        found = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url",
                                "--jq", ".[0].url"], cwd=self.cwd, capture_output=True, text=True).stdout.rstrip()
        if found:
            p = subprocess.run(["gh", "pr", "edit", found, "--body", body], cwd=self.cwd,
                               capture_output=True, text=True)
            url = found
        else:
            p = subprocess.run(["gh", "pr", "create", "--draft", "--base", base, "--title", title,
                                "--body", body], cwd=self.cwd, capture_output=True, text=True)
            url = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if url:
            self.plan["Pull Request"] = url
        self.cur.update(exit=p.returncode, text=(url + "\n" + p.stderr).strip())
        return p.returncode == 0, url

    def do_judge(self, step: dict) -> dict:
        choices = step.get("choices")
        prompt = (f"持ち場: {self.plan.get('持ち場')} / 課題: {self.plan.get('課題')}\n"
                  f"問い: {step['question']}\n"
                  + (f"選べる値: {', '.join(choices)}（関門なら gate、止めるなら stop）\n" if choices else "")
                  + f"\n## 規則\n{self.plan.get('規則', '（無し）')}\n\n## 結果\n{self.inputs_text(step)}")
        res = call_claude(JUDGE_SYSTEM, prompt, None, self.cwd, step.get("timeout", 600))
        self.add_usage("judge", res)
        d = parse_decision(res["text"]) if res["ok"] else {"decision": "stop", "reason": res["text"][:200]}
        self.cur.update(exit=0, text=json.dumps(d, ensure_ascii=False), seconds=res["seconds"])
        return d

    # --- 駆動 ---
    def run(self) -> str:
        sid = self.order[0]
        result, reason = "完了", "無し"
        limit = self.plan.get("上限", 30)
        n = 0
        while sid:
            n += 1
            if n > limit:
                result, reason = "止まった", f"段の数が上限 {limit} を超えた"
                break
            step = self.steps.get(sid)
            if step is None:
                result, reason = "止まった", f"知らない段: {sid}"
                break
            self.record_stage(step.get("stage"))
            self.cur = {"id": sid, "type": step["type"]}
            if step["type"] == "judge":
                d = self.do_judge(step)
                dec = d.get("decision", "stop")
                self.cur["decision"] = dec
                if dec == "next":
                    nxt = self.next_of(sid, step)
                elif dec == "stop":
                    result, reason, nxt = "止まった", d.get("reason", "判断が止めた"), None
                elif dec == "gate":
                    result, reason, nxt = "関門", d.get("reason", ""), None
                elif dec in self.steps:
                    nxt = dec
                else:
                    result, reason, nxt = "止まった", f"判断が知らない値を返した: {dec}", None
            else:
                do = {"run": self.do_run, "work": self.do_work, "pr": self.do_pr}[step["type"]]
                ok, _ = do(step)
                if ok:
                    nxt = self.next_of(sid, step)
                elif step.get("on_fail"):
                    nxt = step["on_fail"]
                else:
                    result, reason, nxt = "止まった", f"段 {sid} が失敗した（exit={self.cur['exit']}）", None
            self.results[sid] = dict(self.cur)
            self.out_path(n, sid).write_text(self.cur.get("text", ""))
            self.log.append({k: v for k, v in self.cur.items() if k != "text"})
            (self.dir / "state.json").write_text(json.dumps(
                {"log": self.log, "llm": self.llm}, ensure_ascii=False, indent=1))
            sid = nxt
        return self.report(result, reason)

    def report(self, result: str, reason: str) -> str:
        l = self.llm
        steps = " → ".join(f"{e['id']}" + (f"[{e['decision']}]" if "decision" in e else
                                           f"(exit={e.get('exit')})") for e in self.log)
        text = f"""## 持ち場の報告

- 持ち場: {self.plan.get('持ち場')}
- 課題: {' '.join('#' + str(i) for i in self.plan.get('課題', []))}
- 結果: {result}
- 関門: {'本番の系へ届く操作' if result == '関門' else '無し'}
- 次の持ち場: {self.plan.get('次の持ち場', '無し') if result == '完了' else '無し'}
- Pull Request: {self.plan.get('Pull Request', '無し')}
- 最後に記録した工程: {self.last_stage}
- 使った worker: 修正 {l['work']}（claude -p）/ 判断 {l['judge']}（claude -p）
- 提示物: 無し
- 理由: {reason}
- 通った段: {steps}
- LLM の使用量: 入力 {l['input']} / cache read {l['cache_read']} / cache write {l['cache_write']} / 出力 {l['output']} / ${l['cost']:.3f}
- 記録: {self.dir}
"""
        (self.dir / "report.md").write_text(text)
        return text


EXAMPLE = {
    "持ち場": "検査", "課題": [0], "モード": "light", "作業場所": "/abs/worktree",
    "規則": "テストが落ちたら、失敗が変更に起因するなら fix、環境や揺れなら stop。",
    "上限": 10,
    "steps": [
        {"id": "test", "type": "run", "cmd": "pytest -q", "stage": "完了判定", "on_fail": "judge-test",
         "next": "pr"},
        {"id": "judge-test", "type": "judge", "inputs": ["test"],
         "question": "テストの失敗を直すか止めるか", "choices": ["fix", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test"],
         "prompt": "失敗したテストを直してコミットする（push しない）", "next": "test"},
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": "develop",
         "title": "変更の要約（#0）", "summary": "何を変えたかの 1〜2 文", "docs": ["issues/issue-0-design.md"],
         "next": "end"},
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("plan")
    r.add_argument("--state-dir")
    sub.add_parser("example")
    a = ap.parse_args()
    if a.cmd == "example":
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    plan = json.loads(Path(a.plan).read_text())
    state = Path(a.state_dir) if a.state_dir else Path(a.plan).parent / (
        Path(a.plan).stem + "-state")
    text = Supervisor(plan, state).run()
    print(text)
    return 0 if "結果: 完了" in text or "結果: 関門" in text else 3


if __name__ == "__main__":
    sys.exit(main())
