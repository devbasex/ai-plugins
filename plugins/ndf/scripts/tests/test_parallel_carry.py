"""承認の後に加わった範囲が文書だけなら出し直さずに新しい範囲で出す（#3020 の受け入れ条件 1〜10・I1〜I8）。

判定（`parallel_release.carry_check`）は本物の git の範囲で確かめ、耐久ワークフローの分岐は判定を差し替えて確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_run as tpr  # noqa: E402

import deps  # noqa: E402
import durable  # noqa: E402
import parallel_cli as pcli  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_release as prl  # noqa: E402
import parallel_run as prun  # noqa: E402

deps.require("pathmatch")

env = tpr.env
event, until, start, resume, read, build_question = tpr.event, tpr.until, tpr.start, tpr.resume, tpr.read, tpr.build_question
SCRIPTS = tpr.SCRIPTS


# ---- 判定（carry_check） ----


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


def commit(root: Path, files: dict[str, str]) -> str:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "c")
    return git(root, "rev-parse", "HEAD")


def prs_of(*numbers):
    return lambda _root, _first, _head: list(numbers)


def test_docs_only_range_carries_with_the_added_prs(repo):
    """受け入れ条件 1・I1。テストが読む文書（tested）も文書として数える（起票者の推定 S6）。"""
    first = git(repo, "rev-parse", "HEAD")
    decl = {"docs": {"tested": [{"paths": ["issues/**"], "tests": ["tests/t.py"]}]}}
    head = commit(repo, {".ndf/project.json": json.dumps(decl), "issues/x.md": "a\n", "docs/y.md": "b\n"})
    # 宣言のファイル自体はコードなので、宣言を足すコミットの後から比べる
    second = commit(repo, {"issues/x.md": "a2\n", "docs/y.md": "b2\n"})
    got = prl.carry_check(repo, head, second, prs_of=prs_of(15, 12))
    assert got == {"carry": True, "reason": "", "added": [12, 15], "files": ["docs/y.md", "issues/x.md"]}
    assert prl.carry_check(repo, first, head, prs_of=prs_of())["carry"] is False  # .ndf/project.json はコード


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({"plugins/ndf/skills/s/SKILL.md": "x\n", "docs/a.md": "y\n"}, "振る舞いを決める文書"),
        ({"plugins/ndf/agents/w.md": "x\n"}, "振る舞いを決める文書"),
        ({"docs/a.md": "y\n", "lib/b.py": "z\n"}, "コードのファイル"),
    ],
)
def test_behavior_docs_or_code_do_not_carry(repo, files, said):
    """受け入れ条件 2・3・I1。"""
    first = git(repo, "rev-parse", "HEAD")
    head = commit(repo, files)
    got = prl.carry_check(repo, first, head, prs_of=prs_of(1))
    assert got["carry"] is False and said in got["reason"]


def test_unreadable_inputs_do_not_carry(repo):
    """受け入れ条件 4・I2: 提示 1 のコミットが無い・宣言が壊れている・Pull Request の一覧が取れない。"""
    first = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {"docs/a.md": "y\n"})
    assert prl.carry_check(repo, "0" * 40, head, prs_of=prs_of(1))["carry"] is False
    assert prl.carry_check(repo, "", head, prs_of=prs_of(1))["carry"] is False

    def boom(*_a):
        raise RuntimeError("GitHub に届かない")

    got = prl.carry_check(repo, first, head, prs_of=boom)
    assert got["carry"] is False and "GitHub に届かない" in got["reason"]
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"docs": "壊れた"}))  # 作業ツリーの宣言を読む
    got = prl.carry_check(repo, first, head, prs_of=prs_of(1))
    assert got["carry"] is False and "docs" in got["reason"]


def test_an_empty_diff_does_not_carry(repo):
    """前提 3: ファイルが 0 件で先頭だけが進んだら出し直す。"""
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "-q", "--allow-empty", "-m", "空")
    got = prl.carry_check(repo, first, git(repo, "rev-parse", "HEAD"), prs_of=prs_of())
    assert got["carry"] is False


# ---- 提示のプラン（I4・決定 3） ----


def test_carried_present_plan_rechecks_then_records_then_approves(tmp_path):
    import supervise_lib  # noqa: F401
    from supervise_lib import limits

    d = tmp_path / "releases" / "4" / "2"
    present, _ = prl.plan_parallel_release(SCRIPTS.parents[2], "p1", d, "1.2.3", carried=True)
    by = {s["id"]: s for s in present["steps"]}
    assert limits.run_error(present) is None
    assert "mvv" not in by and "note" not in by
    assert (by["present"]["next"], by["carried"]["next"], by["record"]["next"]) == ("carried", "record", "approve")
    assert by["carried"]["gate_next"] == "end" and "carried p1 4" in by["carried"]["cmd"]  # 10 なら record を打たずに終わる
    assert "提示 1 の承認を引き継ぐ" in by["record"]["cmd"] and str(d / "answer.json") in by["record"]["cmd"]
    plain, _ = prl.plan_parallel_release(SCRIPTS.parents[2], "p1", tmp_path / "releases" / "4" / "1", "1.2.3")
    assert "mvv" in {s["id"] for s in plain["steps"]} and "carried" not in {s["id"] for s in plain["steps"]}


# ---- 耐久ワークフロー ----


@pytest.fixture
def carry(env, monkeypatch):
    """判定と組み立ての呼び出しを記録する。`result` を書き換えて判定の結果を決める。"""
    seen = {"checks": [], "built": [], "result": {"carry": True, "reason": "", "added": [12], "files": ["docs/a.md"]}}

    def check(_root, first, head, prs_of=None):
        seen["checks"].append((first, head))
        return seen["result"]

    monkeypatch.setattr(prl, "carry_check", check)
    inner = prl.build_plans

    def build(m, n, s, k, v, builder_=None, **kw):
        seen["built"].append((s, k, kw.get("carried", False)))
        return inner(m, n, s, k, v, builder_, **kw)

    monkeypatch.setattr(prl, "build_plans", build)
    return seen


def presented(main, k, sha, prs):
    (prl.release_dir(main, "p1", 1, k) / "presented.json").write_text(json.dumps({"sha": sha, "prs": prs}))


def to_confirm(env, tmp_path):
    """依頼 1 件で提示 1 を出し、先頭を進めて承認を送る。依頼の ID を返す。"""
    main = env["main"]
    start(main)
    rid = pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    presented(main, 1, "sha1", [10])
    env["heads"]["v"] = "sha2"
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    return rid


def confirmed(main):
    return until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed" and e)


def test_docs_only_range_confirms_the_new_range_without_representing(env, tmp_path, carry, monkeypatch):
    """受け入れ条件 1・5・7・8、I5・I8: 出し直しの提示を出さず提示 2 で確定し、確かめ直しの範囲の PR を結果に載せる。"""
    main = env["main"]
    monkeypatch.setattr(prun, "dev_version", lambda _m: "1.2.3")
    rid = to_confirm(env, tmp_path)
    ev = confirmed(main)
    assert ev["k"] == 2 and not ev.get("represent") and ev["carried"] == {"from": "sha1", "head": "sha2", "prs": [12]}
    assert carry["checks"] == [("sha1", "sha2")] and (1, 2, True) in carry["built"]
    task = prun.helper_task(main, "p1", 1, 2)  # 一時レーンは提示と同じ流し方（提示のプランが確かめ直して承認を書く）
    assert task["phase"] == "present" and "present.json" in task["pipeline"]
    got, _ = pcli.wait_event(main, "p1", 0, 10)  # オーガナイザーが利用者へ 1 行で伝える知らせ
    assert got.kind == "event" and got.value["key"] == "pause" and got.value["carried"]["prs"] == [12]
    # 判定の後に文書だけの #13 も入った（確かめ直しの範囲）。結果には #13 も載る（I8）
    presented(main, 2, "sha3", [10, 12, 13])
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.4"}, "s1")
    ans, _ = until(lambda: pl.read_answer(main, "p1", rid)[0] and pl.read_answer(main, "p1", rid))
    assert ans["result"] == "released"
    rng = event(main, "p1", "range-1")
    assert (rng["sha"], rng["prs"], rng["added"], rng["version"]) == ("sha3", [10, 12, 13], [12, 13], "1.2.4")


@pytest.mark.parametrize("why", ["振る舞いを決める文書が 1 件ある", "コードのファイルが 1 件ある", "差分の一覧を取れない"])
def test_not_docs_only_represents(env, tmp_path, carry, why):
    """受け入れ条件 2・3・4: 文書だけではない・判定できないなら出し直しの提示が出る。"""
    carry["result"] = {"carry": False, "reason": why, "added": [], "files": []}
    main = env["main"]
    to_confirm(env, tmp_path)
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("represent") and e)
    assert ev["k"] == 2 and (1, 2, False) in carry["built"]


def test_unmoved_head_does_not_check(env, tmp_path, carry):
    """受け入れ条件 9・I6。"""
    main = env["main"]
    start(main)
    pl.send_release_request(main, "p1", "la", [10], [1], tmp_path / "st")
    until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "release")
    presented(main, 1, "sha1", [10])
    resume(main, {"n": 1, "phase": "confirm"}, "c1")
    ev = confirmed(main)
    assert ev["k"] == 1 and "carried" not in ev and carry["checks"] == []
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    assert until(lambda: event(main, "p1", "range-1"))["added"] == []


def test_build_failure_after_carry_answers_failed_and_asks(env, tmp_path, carry, monkeypatch):
    """受け入れ条件 6。"""
    main = env["main"]

    def boom(*_a, **_k):
        raise RuntimeError("組み立ての失敗")

    monkeypatch.setattr(prl, "build_plans", lambda m, n, s, k, v, builder_=None, **kw: boom() if k == 2 else tpr._build(
        m, n, s, k, v, lambda *_a, **_k: ({"steps": []}, {"steps": []}), **kw))  # fmt: skip
    rid = to_confirm(env, tmp_path)
    ans = read(main, rid)
    assert ans["result"] == "failed" and "組み立ての失敗" in ans["reason"] and build_question(main)["kind"] == "build"


def test_answer_to_the_second_presentation_does_not_check_and_drops_carried(env, tmp_path, carry):
    """受け入れ条件 10・I3・I8: 確かめ直しが文書だけでなく提示 2 の問いへ答えたら、判定せず `take` で確定し、`added` は空。"""
    main = env["main"]
    rid = to_confirm(env, tmp_path)
    confirmed(main)
    presented(main, 2, "sha3", [10, 12, 14])  # 判定の後にコードの #14 が入り、parallel.py carried が 10 を返した
    env["heads"]["v"] = "sha4"
    resume(main, {"n": 1, "phase": "confirm", "take": "new"}, "c2")
    ev = until(lambda: (e := event(main, "p1", "pause")) and e.get("action") == "confirmed" and "carried" not in e and e)
    assert ev["k"] == 2 and carry["checks"] == [("sha1", "sha2")]
    assert prun.helper_task(main, "p1", 1, 2)["phase"] == "confirmed"  # 確定の後の 3 手
    resume(main, {"n": 1, "phase": "settle", "result": "released", "version": "1.2.3"}, "s1")
    until(lambda: pl.read_answer(main, "p1", rid)[0])
    rng = event(main, "p1", "range-1")
    assert rng["prs"] == [10, 12, 14] and rng["added"] == []


def test_take_first_after_represent_keeps_the_first_range(env, tmp_path, carry):
    """受け入れ条件 10: 出し直しの提示への `--take first` は今のとおり提示 1 で確定する。"""
    carry["result"] = {"carry": False, "reason": "コードのファイルが 1 件ある", "added": [], "files": []}
    main = env["main"]
    to_confirm(env, tmp_path)
    until(lambda: (e := event(main, "p1", "pause")) and e.get("represent"))
    resume(main, {"n": 1, "phase": "confirm", "take": "first"}, "c2")
    ev = confirmed(main)
    assert ev["k"] == 1 and "carried" not in ev and len(carry["checks"]) == 1


def test_an_old_record_without_the_mark_represents_without_checking(env, tmp_path, carry, monkeypatch):
    """I7: 印 `parallel-docs-carry` の無い記録の打ち直しは判定せずに出し直す。"""
    real = durable.patched
    monkeypatch.setattr(durable, "patched", lambda n: False if n == "parallel-docs-carry" else real(n))
    main = env["main"]
    to_confirm(env, tmp_path)
    until(lambda: (e := event(main, "p1", "pause")) and e.get("represent"))
    assert carry["checks"] == []


def test_wait_skips_a_confirmed_pause_without_carried():
    """受け入れ条件 7: `carried` の無い確定は返さない。"""
    assert pcli.carried_pause({"action": "confirmed", "carried": {"prs": [1]}})
    assert not pcli.carried_pause({"action": "confirmed"}) and not pcli.carried_pause({"action": "release", "carried": {"prs": [1]}})


# ---- parallel.py carried（I4） ----


def run_carried(main: Path, name: str, n: int) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), "carried", name, str(n)], cwd=main, capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_parallel_carried_returns_0_for_docs_and_10_for_code(repo, monkeypatch):
    first = git(repo, "rev-parse", "HEAD")
    docs = commit(repo, {"docs/a.md": "y\n"})
    for k, sha, prs in ((1, first, [10]), (2, docs, [10, 12])):
        d = prl.release_dir(repo, "p1", 1, k)
        d.mkdir(parents=True)
        (d / "presented.json").write_text(json.dumps({"sha": sha, "prs": prs}))
    # 加わった Pull Request の一覧は GitHub を読むため、マージの無い範囲（空の一覧）になる git だけの範囲で確かめる
    code, out = run_carried(repo, "p1", 1)
    assert code == 0 and out["status"] == "ok" and out["metrics"]["added"] == [12]
    code_sha = commit(repo, {"lib/b.py": "z\n"})
    (prl.release_dir(repo, "p1", 1, 2) / "presented.json").write_text(json.dumps({"sha": code_sha, "prs": [10, 12, 14]}))
    code, out = run_carried(repo, "p1", 1)
    assert code == 10 and out["status"] == "gate" and "コード" in out["summary"]
    (prl.release_dir(repo, "p1", 1, 2) / "presented.json").unlink()
    code, out = run_carried(repo, "p1", 1)
    assert code == 2 and out["status"] == "stopped"
