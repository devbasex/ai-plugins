"""ミッションの状態（`mission-state.py`、#1063）。

入力は `supervise.py queue` の done の JSON と `report.md` の形（r6 の実物の形）から作る。
LLM も gh も呼ばない。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "mission-state.py"
RELAY = SCRIPTS / "relay.py"


def report(phase: str, issues: str, res: str, pr: str, cost: str, reason: str = "無し") -> str:
    """supervise_lib/state.py の RunState.write_report と同じ形の報告。"""
    return f"""## フェーズの報告

- フェーズ: {phase}
- 課題: {issues}
- 結果: {res}
- 関門: 無し
- 次のフェーズ: 無し
- Pull Request: {pr}
- 最後に記録した工程: Pull Request
- 使った worker: 修正 1（claude -p）/ 判断 1（claude -p）
- 途中の報告: ステップ 7 / まだ動いている 0 / worker 4（形が違う 0）/ conductor 向け 0 / LLM へ回した 0 回・$0.000（x）
- 提示物: 無し
- 理由: {reason}
- 通ったステップ: impl(exit=0) → pr(exit=0)
- 件数: 無し
- LLM の使用量: 入力 56 / cache read 1423577 / cache write 78735 / 出力 24980 / ${cost}
- 記録: x

| ステップ | 往復 | 秒 | cache read | cache write | 出力 | 費用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| impl | 41 | 296.0 | 1423577 | 75699 | 24486 | $1.153 |
"""


def write_plan(d: Path, name: str, issues: list[int], phase: str, rep: str | None) -> str:
    plan = d / f"{name}.json"
    plan.write_text(json.dumps({"フェーズ": phase, "課題": issues, "steps": []}, ensure_ascii=False))
    if rep is not None:
        st = d / f"{name}-state"
        st.mkdir()
        (st / "report.md").write_text(rep)
    return str(plan)


@pytest.fixture()
def r6(tmp_path):
    """r6 と同じ形: 実装 3 本の queue（1 本止まった）と、本番の queue。"""
    a = write_plan(tmp_path, "plan-1053", [1053], "実装",
                   report("実装", "#1053", "完了", "https://github.com/devbasex/ai-plugins/pull/1056", "1.178"))
    b = write_plan(tmp_path, "plan-1054", [1054], "実装",
                   report("実装", "#1054", "止まった", "https://github.com/devbasex/ai-plugins/pull/1058", "2.170",
                          reason="merge のステップで衝突"))
    prod = write_plan(tmp_path, "plan-release-prod", [1053, 1054], "配布（本番）",
                      report("配布（本番）", "#1053 #1054", "完了", "無し", "0.000"))
    dev = write_plan(tmp_path, "plan-release-dev", [1053, 1054], "配布（開発版）", None)
    done = tmp_path / "queue-done.json"
    done.write_text(json.dumps({
        "tool": "supervise-queue", "status": "stopped", "summary": "2 本: 完了 1 / 関門 0 / 止まった 1",
        "items": [
            {"plan": a, "result": "完了", "exit": 0, "report": str(Path(a).with_suffix("")) + "-state/report.md",
             "seconds": 586.4},
            {"plan": b, "result": "止まった", "exit": 3,
             "report": str(Path(b).with_suffix("")) + "-state/report.md", "seconds": 816.0}],
        "metrics": {"plans": 2, "stopped": 1, "gate": 0, "not_run": 0, "max": 3, "done": str(done)}},
        ensure_ascii=False))
    pdone = tmp_path / "done-release-prod.json"
    pdone.write_text(json.dumps({
        "tool": "supervise-queue", "status": "ok", "summary": "1 本",
        "items": [{"plan": prod, "result": "完了", "exit": 0,
                   "report": str(Path(prod).with_suffix("")) + "-state/report.md", "seconds": 300.3}],
        "metrics": {}}, ensure_ascii=False))
    mission = tmp_path / "mission.json"
    return {"dir": tmp_path, "a": a, "b": b, "dev": dev, "prod": prod, "done": str(done), "pdone": str(pdone),
            "mission": str(mission)}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def ok(*args: str) -> dict:
    p = run(*args)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


GOAL = ("/goal /ndf:development-workflow https://github.com/devbasex/ai-plugins/milestone/{milestone}\n"
        "issues/handoff.md の「{heading}」から続ける。版は {prod}。")


def init(r6) -> None:
    ok("init", r6["mission"], "--name", "(a)(b) 置き換え", "--milestone", "26", "--issue", "1053", "--issue", "1054",
       "--plan", f"実装={r6['a']}", "--plan", f"実装={r6['b']}", "--plan", f"開発版={r6['dev']}",
       "--plan", f"本番={r6['prod']}", "--done", r6["done"], "--dev", "10.17.17-dev.1", "--prod", "10.17.17",
       "--goal", GOAL)


DOC = """# 引継ぎ

## 進め方

- 手で書いた行

## 今の会話の進み（06:53〜07:33 UTC）

| 計画 | 状態 | 次 |
| --- | --- | --- |
| 古い行 | 古い | — |

```text
## 囲みの中の見出しは節の終わりではない
```

### 小見出しも節の中

## 前の会話の進み（05:23〜06:53 UTC）

- 前の区間

## 次に実行するコマンド

```ndf-next
/goal 古い文面
```
"""


def test_update_fills_rows_and_render_writes_table(r6):
    init(r6)
    out = ok("update", r6["mission"], "--done", r6["pdone"], "--next", f"{r6['a']}=次のミッションからチェインで流す")
    rows = {i["plan"]: i for i in out["items"]}
    assert (rows[r6["a"]]["result"], rows[r6["a"]]["pr"], rows[r6["a"]]["seconds"], rows[r6["a"]]["cost"]) == \
        ("完了", "#1056", 586.4, 1.178)
    assert (rows[r6["b"]]["result"], rows[r6["b"]]["exit"], rows[r6["b"]]["reason"]) == ("止まった", 3, "merge のステップで衝突")
    assert rows[r6["dev"]]["result"] == "まだ"
    assert (rows[r6["prod"]]["result"], rows[r6["prod"]]["pr"], rows[r6["prod"]]["seconds"]) == ("完了", "", 300.3)

    doc = r6["dir"] / "handoff.md"
    doc.write_text(DOC)
    ok("render", r6["mission"], str(doc), "--section", "今の会話の進み")
    text = doc.read_text()
    assert "| 実装 #1053 | 完了 | #1056 | 586.4 | $1.178 | 次のミッションからチェインで流す |" in text
    assert "| 実装 #1054 | 止まった（exit=3）。理由: merge のステップで衝突 | #1058 | 816 | $2.170 | — |" in text
    assert "| 開発版 10.17.17-dev.1 | まだ | — | — | — | — |" in text
    assert "| 本番 10.17.17 | 完了 | — | 300.3 | $0.000 | — |" in text
    assert "古い行" not in text


def split_around(text: str, head: str, next_head: str) -> tuple[str, str]:
    i = text.index(head)
    j = text.index(next_head)
    return text[: i + len(head)], text[j:]


def test_render_keeps_bytes_outside_section(r6):
    init(r6)
    ok("update", r6["mission"])
    doc = r6["dir"] / "handoff.md"
    doc.write_bytes(DOC.encode())
    before = split_around(DOC, "## 今の会話の進み（06:53〜07:33 UTC）\n", "## 前の会話の進み")
    ok("render", r6["mission"], str(doc), "--section", "今の会話の進み")
    after_text = doc.read_bytes().decode()
    assert split_around(after_text, "## 今の会話の進み（06:53〜07:33 UTC）\n", "## 前の会話の進み") == before
    # 囲みの中の見出しと小見出しは節の本文として置き換わる
    assert "囲みの中の見出し" not in after_text and "小見出しも節の中" not in after_text
    # 2 度目は 1 バイトも変えない
    ok("render", r6["mission"], str(doc), "--section", "今の会話の進み")
    assert doc.read_bytes().decode() == after_text


def test_render_stops_without_section(r6):
    init(r6)
    doc = r6["dir"] / "handoff.md"
    doc.write_text(DOC)
    p = run("render", r6["mission"], str(doc), "--section", "無い節")
    assert p.returncode == 1
    out = json.loads(p.stdout)
    assert out["status"] == "stopped" and "無い節" in out["summary"]
    assert doc.read_text() == DOC


def test_render_demote_adds_new_section_before(r6):
    init(r6)
    ok("update", r6["mission"])
    doc = r6["dir"] / "handoff.md"
    doc.write_text(DOC)
    ok("render", r6["mission"], str(doc), "--section", "今の会話の進み", "--demote", "前の会話の進み",
       "--heading", "今の会話の進み（07:33〜 UTC）")
    text = doc.read_text()
    i_new = text.index("## 今の会話の進み（07:33〜 UTC）\n")
    i_old = text.index("## 前の会話の進み（06:53〜07:33 UTC）\n")
    assert i_new < text.index("| 実装 #1053 |") < i_old
    assert "古い行" in text[i_old:]
    assert text[:i_new] == DOC[: DOC.index("## 今の会話の進み")]


def test_update_twice_is_same(r6):
    init(r6)
    ok("update", r6["mission"], "--done", r6["pdone"])
    first = Path(r6["mission"]).read_bytes()
    out1 = run("update", r6["mission"], "--done", r6["pdone"]).stdout
    assert Path(r6["mission"]).read_bytes() == first
    assert run("update", r6["mission"], "--done", r6["pdone"]).stdout == out1


def test_init_stops_on_other_shape_json(r6):
    """同じパスに supervise.py new mission の目録（別の形の JSON）があれば、上書きせずに止まる（#1082）。"""
    catalog = {"ミッション": "m", "ブランチ": "fix/x", "ステージ": [{"計画": ["a.json"]}]}
    path = Path(r6["mission"])
    path.write_text(json.dumps(catalog, ensure_ascii=False))
    before = path.read_bytes()
    p = run("init", r6["mission"], "--name", "m")
    assert p.returncode != 0
    out = json.loads(p.stdout)
    assert out["status"] == "stopped" and r6["mission"] in out["summary"]
    assert path.read_bytes() == before


def test_init_overwrites_own_state(r6):
    """同じ形（状態のファイル）なら、これまでどおり作り直す。"""
    init(r6)
    out = ok("init", r6["mission"], "--name", "作り直し")
    assert out["status"] == "ok"
    assert json.loads(Path(r6["mission"]).read_text())["name"] == "作り直し"


def test_gate_and_status(r6):
    init(r6)
    ok("update", r6["mission"], "--done", r6["pdone"])
    ok("gate", r6["mission"], "関門 2", "--what", "本番 10.17.17", "--at", "2026-09-25T07:22:00+00:00")
    ok("gate", r6["mission"], "関門 2", "--what", "本番 10.17.17", "--at", "2026-09-25T07:23:00+00:00")
    m = json.loads(Path(r6["mission"]).read_text())
    assert m["gates"] == [{"name": "関門 2", "what": "本番 10.17.17", "at": "2026-09-25T07:23:00+00:00"}]
    p = run("status", r6["mission"])
    lines = p.stdout.splitlines()
    assert lines[0] == "ミッション: (a)(b) 置き換え（マイルストーン 26）"
    assert "実装 #1053: 完了（#1056・586.4 秒・$1.178）。次: —" in lines
    assert lines[-1] == "関門 2: 承認 2026-09-25T07:23:00+00:00"


def load_relay():
    spec = importlib.util.spec_from_file_location("relay_for_mission", RELAY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_next_is_ndf_next_block_for_relay(r6):
    init(r6)
    doc = r6["dir"] / "handoff.md"
    doc.write_text(DOC)
    p = run("next", r6["mission"], "--doc", str(doc), "--section", "今の会話の進み")
    assert p.returncode == 0, p.stderr
    blocks = load_relay().next_blocks("前置き\n\n" + p.stdout)
    assert blocks == ["/goal /ndf:development-workflow https://github.com/devbasex/ai-plugins/milestone/26\n"
                      "issues/handoff.md の「今の会話の進み（06:53〜07:33 UTC）」から続ける。版は 10.17.17。"]


def test_next_replace_rewrites_command_section(r6):
    init(r6)
    doc = r6["dir"] / "handoff.md"
    doc.write_text(DOC)
    ok("next", r6["mission"], "--doc", str(doc), "--replace")
    text = doc.read_text()
    assert "古い文面" not in text
    assert text[: text.index("## 次に実行するコマンド")] == DOC[: DOC.index("## 次に実行するコマンド")]
    blocks = load_relay().next_blocks(text)
    assert len(blocks) == 1 and "版は 10.17.17" in blocks[0]


def test_no_llm_calls():
    """LLM を呼ばない。外へ出るのは MVV を写すときの gh api だけである。"""
    src = SCRIPT.read_text()
    assert "claude" not in src
    assert src.count("subprocess.run(") == 1 and '["gh", "api"' in src


# ---------- pace: fast の MVV（#1078） ----------

MILESTONE = """マイルストーン 26 の説明

## Mission

速く届ける

## Vision

止まらずに回る

## Value

1. スクリプトで判定する
2. 実測で決める

## 備考

写さない節
"""


def gh_env(tmp_path: Path, description: str | None) -> dict:
    import os
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    if description is None:
        gh.write_text("#!/bin/sh\necho 'HTTP 404' >&2\nexit 1\n")
    else:
        (tmp_path / "desc.txt").write_text(description)
        gh.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path / 'gh-calls.txt'}\ncat {tmp_path / 'desc.txt'}\n")
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}


def run_env(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=30, env=env)


def test_init_fast_copies_the_three_sections_and_hashes_them(tmp_path):
    import hashlib
    env = gh_env(tmp_path, MILESTONE)
    mission = tmp_path / "state" / "mission-state.json"
    p = run_env(env, "init", str(mission), "--name", "m", "--pace", "fast", "--milestone", "26")
    assert p.returncode == 0, p.stdout + p.stderr
    m = json.loads(mission.read_text())
    mvv = Path(m["mvv"]["path"])
    assert m["pace"] == "fast" and mvv == tmp_path / "state" / "mvv.md"
    text = mvv.read_text()
    assert "## Mission" in text and "## Vision" in text and "2. 実測で決める" in text
    assert "写さない節" not in text and "マイルストーン 26 の説明" not in text
    assert m["mvv"]["sha256"] == hashlib.sha256(mvv.read_bytes()).hexdigest()
    assert "milestones/26" in (tmp_path / "gh-calls.txt").read_text()


@pytest.mark.parametrize("description", [MILESTONE.replace("## Vision", "## 展望"), None])
def test_init_fast_without_the_sections_writes_nothing_and_returns_three(tmp_path, description):
    env = gh_env(tmp_path, description)
    mission = tmp_path / "state" / "mission-state.json"
    p = run_env(env, "init", str(mission), "--name", "m", "--pace", "fast", "--milestone", "26")
    assert p.returncode == 3, p.stdout + p.stderr
    assert not mission.exists()


def test_init_fast_without_a_source_returns_two(tmp_path):
    p = run("init", str(tmp_path / "m.json"), "--name", "m", "--pace", "fast")
    assert p.returncode == 2
    assert not (tmp_path / "m.json").exists()


def test_init_normal_keeps_the_old_shape(r6):
    init(r6)
    m = json.loads(Path(r6["mission"]).read_text())
    assert m["pace"] == "normal" and "mvv" not in m


def test_mvv_approval_and_the_gate_by_the_judgement(tmp_path):
    import hashlib
    mvv = tmp_path / "given.md"
    mvv.write_text("## Mission\nx\n## Vision\ny\n## Value\nz\n")
    mission = tmp_path / "mission-state.json"
    ok("init", str(mission), "--name", "m", "--pace", "fast", "--mvv", str(mvv))
    ok("gate", str(mission), "MVV", "--what", "MVV を承認")
    ok("gate", str(mission), "関門 2", "--what", "本番 10.17.30", "--by", "mvv", "--verdict", "follow",
       "--reasons", '["Value 1"]', "--log", "/x/mvv-gate.jsonl")
    g = {x["name"]: x for x in json.loads(mission.read_text())["gates"]}
    assert g["MVV"]["sha256"] == hashlib.sha256(mvv.read_bytes()).hexdigest()
    assert g["MVV"].get("by", "user") == "user"
    assert (g["関門 2"]["by"], g["関門 2"]["verdict"], g["関門 2"]["reasons"], g["関門 2"]["log"]) == \
        ("mvv", "follow", ["Value 1"], "/x/mvv-gate.jsonl")
    p = run("status", str(mission))
    assert "関門 2: MVV 判定 " in p.stdout


def test_mvv_approval_without_an_mvv_stops(r6):
    init(r6)
    assert run("gate", r6["mission"], "MVV", "--what", "x").returncode == 1


def test_update_adds_plans_from_done_without_init_plan(r6):
    ok("init", r6["mission"], "--name", "計画を渡さない", "--dev", "10.17.17-dev.1", "--prod", "10.17.17")
    out = ok("update", r6["mission"], "--done", r6["done"], "--done", r6["pdone"])
    rows = {i["plan"]: i for i in out["items"]}
    assert set(rows) == {r6["a"], r6["b"], r6["prod"]}
    assert (rows[r6["a"]]["result"], rows[r6["a"]]["pr"]) == ("完了", "#1056")
    m = json.loads(Path(r6["mission"]).read_text())
    assert {p["plan"]: p["label"] for p in m["plans"]}[r6["prod"]] == "本番 10.17.17"
    assert {p["plan"]: p["kind"] for p in m["plans"]}[r6["a"]] == "実装"


def load_mission_state():
    spec = importlib.util.spec_from_file_location("mission_state", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("key", ["フェーズ", "持ち場"])
def test_plan_kind_reads_the_old_key_too(tmp_path, key):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({key: "配布（開発版）"}, ensure_ascii=False))
    assert load_mission_state().plan_kind(str(plan)) == "開発版"
