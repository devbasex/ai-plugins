"""`test-run.py whole` / `scope` — 手元の全体テストと範囲テストの上限は suite 群全体で 1 つ（#1334）。"""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "test-run.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("ndf_test_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _whole(mod, monkeypatch, tmp_path, seconds_per_suite, lint=(), tests=("a", "b", "c")):
    """suite が各 `seconds_per_suite` 秒かかる全体テスト（静的解析 `lint` → テスト `tests`）を、上限 100 秒で走らせる。
    渡した上限の並びを返す。"""
    strategy = types.SimpleNamespace(
        name="local-full",
        source="test",
        whole_on_ci=False,
        suites=[],
        whole_commands=lambda kind=None: list(lint) if kind == "lint" else list(tests),
    )
    monkeypatch.setattr(mod, "_resolve", lambda root, template: (strategy, {"whole_timeout": 100, "test_timeout": 100}, []))
    monkeypatch.setattr(mod.test_triage, "clear_junit", lambda work, s: None)
    now = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    given: list[int] = []

    def run(command, cwd, timeout, log=None):
        given.append(timeout)
        if seconds_per_suite > timeout:
            now[0] += timeout
            return None, True
        now[0] += seconds_per_suite
        return 0, False

    monkeypatch.setattr(mod.test_triage, "run_command", run)
    with pytest.raises(SystemExit) as e:  # `step_result.emit` は結果を出して終える
        mod.cmd_whole(types.SimpleNamespace(root=str(tmp_path), template=None, base=None))
    return e.value.code, given


def test_later_suites_get_only_the_seconds_left(mod, monkeypatch, tmp_path, capsys):
    code, given = _whole(mod, monkeypatch, tmp_path, 30)
    assert code == 0
    assert given == [100, 70, 40]


def test_lint_and_tests_share_one_limit(mod, monkeypatch, tmp_path, capsys):
    """静的解析の後のテストは、静的解析が使った残りの秒だけで走る（種別ごとに上限を丸ごと渡さない）。"""
    code, given = _whole(mod, monkeypatch, tmp_path, 30, lint=("l",), tests=("a", "b"))
    assert code == 0
    assert given == [100, 70, 40]


def test_tests_after_lint_stop_at_the_shared_limit(mod, monkeypatch, tmp_path, capsys):
    code, given = _whole(mod, monkeypatch, tmp_path, 40, lint=("l",), tests=("a", "b"))
    assert code != 0
    assert given == [100, 60, 20], "静的解析の 40 秒を差し引いた残りでテストが打ち切られる"
    assert "全体テストが 100 秒で終わらなかった" in capsys.readouterr().out


def test_the_limit_is_shared_so_the_suites_cannot_run_n_times_longer(mod, monkeypatch, tmp_path, capsys):
    code, given = _whole(mod, monkeypatch, tmp_path, 60)
    assert code != 0
    assert given == [100, 40], "2 本目で合計の上限に届いて止まる（suite ごとに 100 秒を渡さない）"
    assert "全体テストが 100 秒で終わらなかった" in capsys.readouterr().out


def _scope(mod, monkeypatch, tmp_path, seconds_per_suite):
    """2 本の suite の範囲テストが各 `seconds_per_suite` 秒かかるのを、上限 100 秒で走らせる。渡した上限の並びを返す。"""
    strategy = mod.ts.Strategy(
        "local-full",
        "test",
        [mod.ts.Suite("a", "run a", "run-a {paths}", paths=["a"]), mod.ts.Suite("b", "run b", "run-b {paths}", paths=["b"])],
    )
    monkeypatch.setattr(mod, "_resolve", lambda root, template: (strategy, {"whole_timeout": 100, "test_timeout": 100}, []))
    monkeypatch.setattr(mod.test_triage, "clear_junit", lambda work, s: None)
    now = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    given: list[int] = []

    def run(command, cwd, timeout, log=None):
        given.append(timeout)
        if seconds_per_suite > timeout:
            now[0] += timeout
            return None, True
        now[0] += seconds_per_suite
        return 0, False

    monkeypatch.setattr(mod.test_triage, "run_command", run)
    with pytest.raises(SystemExit) as e:
        mod.cmd_scope(types.SimpleNamespace(root=str(tmp_path), template=None, base=None, paths=["a/t.py", "b/t.py"]))
    return e.value.code, given


def test_scope_suites_share_the_limit(mod, monkeypatch, tmp_path, capsys):
    code, given = _scope(mod, monkeypatch, tmp_path, 30)
    assert code == 0
    assert given == [100, 70]


def test_scope_suites_cannot_run_n_times_longer(mod, monkeypatch, tmp_path, capsys):
    code, given = _scope(mod, monkeypatch, tmp_path, 60)
    assert code != 0
    assert given == [100, 40], "2 本目は残りの 40 秒だけを受け取る"


# ---------- #1483: 種別・シェル経由・起動の失敗（一時リポジトリで実際に走らせる） ----------


def _repo(tmp_path, decl=None):
    import json
    import subprocess

    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True
    )
    if decl is not None:
        (root / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    return root


def _run(root, *args):
    import json
    import subprocess
    import sys

    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_a_launch_failure_is_not_a_failure(tmp_path):
    """AC12 — 起動できないコマンドは終了コード 2 と `items[].launch_failed`、落ちたコマンドは 1。"""
    root = _repo(tmp_path)
    (root / "a.sh").write_text("echo hi\n", encoding="utf-8")
    code, out = _run(root, "scope", "--paths", "a.sh", "--template", "nosuchcmd_1483 {paths}")
    assert code == 2 and out["items"][0]["launch_failed"]["code"] == 127
    assert "nosuchcmd_1483 a.sh" in out["summary"] and out["items"][0]["launch_failed"]["command"] == "nosuchcmd_1483 a.sh"
    code, out = _run(root, "whole", "--template", "nosuchcmd_1483 {paths}")
    assert code == 2 and out["items"][0]["launch_failed"]["command"] == "nosuchcmd_1483 ."
    code, out = _run(root, "scope", "--paths", "a.sh", "--template", "false {paths}", "--test-kind", "lint", "--changed", "a.sh")
    assert code == 1 and not any("launch_failed" in i for i in out["items"])


def test_a_scope_template_with_cd_runs_through_the_shell(tmp_path):
    """AC9 — `(cd sub && ... {paths})` の範囲テストが、起動の失敗にならずに走る。"""
    root = _repo(tmp_path)
    (root / "sub").mkdir()
    (root / "sub" / "t.txt").write_text("x", encoding="utf-8")
    code, out = _run(root, "scope", "--paths", "t.txt", "--template", "(cd sub && test -f {paths})")
    assert code == 0, out


def test_a_lint_template_never_runs_on_the_whole_directory(tmp_path):
    """AC5・AC17（test-run.py）— 静的解析の雛形は `{paths}` を `.` にせず、`--paths` で埋める。"""
    root = _repo(tmp_path)
    (root / "a.sh").write_text("echo hi\n", encoding="utf-8")
    template = "sh -c 'for f; do test -f \"$f\" || exit 9; done' x {paths}"
    code, out = _run(root, "whole", "--template", template, "--test-kind", "lint", "--paths", "a.sh")
    assert code == 0, out
    code, out = _run(root, "whole", "--template", template, "--test-kind", "lint")
    assert code != 0 and any(i.get("note", "").startswith("静的解析の雛形に") for i in out["items"])


def test_the_declared_lint_suite_runs_on_the_changed_files(tmp_path):
    """AC8（test-run.py scope）— 宣言の静的解析の suite は、変更したファイルのうち `paths` に当たるものにかかる。"""
    decl = {
        "test": {
            "suites": [
                {
                    "name": "sh",
                    "runner": "sh",
                    "kind": "lint",
                    "scope_command": "sh -c 'for f; do sh -n \"$f\" || exit 1; done' x {paths}",
                    "paths": ["*.sh"],
                },
            ]
        }
    }
    root = _repo(tmp_path, decl)
    (root / "good.sh").write_text("echo hi\n", encoding="utf-8")
    (root / "bad.sh").write_text("if\n", encoding="utf-8")
    (root / "README.md").write_text("if\n", encoding="utf-8")
    code, _ = _run(root, "scope", "--paths", "x", "--changed", "good.sh", "README.md")
    assert code == 0
    code, out = _run(root, "scope", "--paths", "x", "--changed", "good.sh", "bad.sh")
    assert code == 1 and "x good.sh bad.sh" in str(out["items"])


def test_a_declared_round_only_lint_failure_is_a_caused_lint_failure(tmp_path):
    """宣言から導いた round-only の静的解析の suite が落ちたら、判断不能（2）でなく変更起因の静的解析（1）。"""
    decl = {
        "test": {
            "strategy": "round-only",
            "suites": [
                {"name": "unit", "runner": "sh", "kind": "test", "command": "true"},
                {"name": "sh", "runner": "sh", "kind": "lint", "command": "false"},
            ],
        }
    }
    root = _repo(tmp_path, decl)
    code, out = _run(root, "scope", "--paths", "x")
    assert code == 1, out
    assert "JUnit を読まない" not in out["summary"] and "静的解析が落ちた" in out["summary"]


def test_a_round_only_lint_failure_wins_over_an_undecidable_test_failure(tmp_path):
    """round-only でテストと静的解析が同時に落ちても、確定した静的解析の失敗で変更起因（1）にする。"""
    decl = {
        "test": {
            "strategy": "round-only",
            "suites": [
                {"name": "unit", "runner": "sh", "kind": "test", "command": "false"},
                {"name": "sh", "runner": "sh", "kind": "lint", "command": "false"},
            ],
        }
    }
    root = _repo(tmp_path, decl)
    code, out = _run(root, "scope", "--paths", "x")
    assert code == 1, out
    assert "静的解析が落ちた" in out["summary"]


# ---------- #1437: 雛形から組んだ全体テストの注記が結果に残る ----------


def _ts():
    import sys

    sys.path.insert(0, str(SCRIPT.parent / "lib"))
    import test_strategy

    return test_strategy


def _notes(out):
    return [i["note"] for i in out["items"] if "note" in i]


def test_a_failed_whole_test_from_the_template_keeps_the_note(tmp_path):
    """#1437 AC2・F3 — 宣言の無いリポジトリで雛形から組んだ全体テストが落ちても、`items` に注記が残る。"""
    ts = _ts()

    root = _repo(tmp_path)
    code, out = _run(root, "whole", "--template", 'sh -c \'test -f "$1" && ! test -d "$1"\' x {paths}')
    assert code == 1, out
    assert _notes(out).count(ts.WHOLE_FROM_TEMPLATE) == 1


def test_a_lint_template_without_paths_keeps_no_lint_whole(tmp_path):
    """#1437 AC6 — 静的解析の雛形で範囲のパスが無ければ、全体テストを組まず `NO_LINT_WHOLE` を残す。"""
    ts = _ts()

    root = _repo(tmp_path)
    code, out = _run(root, "whole", "--template", "echo {paths}", "--test-kind", "lint")
    assert ts.NO_LINT_WHOLE in _notes(out) and ts.WHOLE_FROM_TEMPLATE not in _notes(out)
    assert not any(i.get("command") == "echo ." for i in out["items"])


# ---------- #2925: 差分の中身で全体テストを選ぶ ----------


def _branch_then_commit(root, files):
    """今の HEAD を起点のブランチ `base` にし、`files` を足したコミットを積む。"""
    import subprocess

    subprocess.run(["git", "-C", str(root), "branch", "-f", "base"], check=True)
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A", "--", *files], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c"], check=True)


def _diff_kinds_item(out):
    return next(i["diff_kinds"] for i in out["items"] if "diff_kinds" in i)


def test_docs_only_diff_skips_the_test_suite_and_says_why(tmp_path):
    """#2925 AC2 — 文書だけの差分では、テストの種別の全体テストを起動せず、飛ばした理由を出す。"""
    root = _repo(tmp_path)
    _branch_then_commit(root, {"docs/a.md": "# a\n", "README.md": "# r\n"})
    code, out = _run(root, "whole", "--base", "base", "--template", "false {paths}")
    assert code == 0, out
    assert out["summary"].startswith("全体テストを飛ばした（差分が文書のファイルだけ: 2 ファイル）")
    assert _diff_kinds_item(out)["docs"] == ["README.md", "docs/a.md"]


def test_docs_only_diff_still_runs_the_lint_suite(tmp_path):
    """#2925 AC13 — 文書だけの差分でも、静的解析の全体テストは今と同じに走る。"""
    decl = {"test": {"suites": [{"name": "lint", "runner": "sh", "kind": "lint", "command": "false"}]}}
    root = _repo(tmp_path, decl)
    _branch_then_commit(root, {"docs/a.md": "# a\n"})
    code, out = _run(root, "whole", "--base", "base")
    assert code == 1, out


def test_mixed_diff_runs_the_whole_tests(tmp_path):
    """#2925 AC3 — コードと文書が混ざった差分では全体テストが今と同じに走る。"""
    root = _repo(tmp_path)
    _branch_then_commit(root, {"docs/a.md": "# a\n", "src/a.py": "x = 1\n"})
    code, out = _run(root, "whole", "--base", "base", "--template", "false {paths}")
    assert code != 0 and "全体テスト" in out["summary"] and "飛ばした" not in out["summary"]


def _tested_decl():
    return {
        "test": {"suites": [{"name": "unit", "runner": "sh", "kind": "test", "command": "false", "scope_command": "sh {paths}"}]},
        "docs": {"tested": [{"paths": ["docs/glossary.md"], "tests": ["t/test_g.sh"]}]},
    }


@pytest.mark.parametrize("body,want", [("exit 0\n", 0), ("exit 1\n", 1)])
def test_tested_docs_run_only_their_tests_and_failures_block(tmp_path, body, want):
    """#2925 AC4 — テストが読む文書だけの差分では、宣言の対応のテストだけを範囲の雛形で走らせ、落ちたら 0 で返さない。"""
    import subprocess

    root = _repo(tmp_path, _tested_decl())
    (root / "t").mkdir()
    (root / "t" / "test_g.sh").write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "d"], check=True)
    _branch_then_commit(root, {"docs/glossary.md": "# g\n", "docs/other.md": "# o\n"})
    code, out = _run(root, "whole", "--base", "base")
    assert (code == 0) == (want == 0), out
    assert out["summary"].startswith("文書を読むテスト（全体テストの代わり。1 ファイル） 1 本")
    assert _diff_kinds_item(out)["tests"] == ["t/test_g.sh"]


def test_unreadable_docs_decl_runs_the_whole_tests(tmp_path):
    """#2925 AC5 — 分け方の宣言が読めなければ、文書だけの差分でも全体テストを走らせる。"""
    import subprocess

    root = _repo(tmp_path)
    (root / ".ndf" / "project.json").write_text('{"version": 1, "docs": {"files": "x"}}', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "d"], check=True)
    _branch_then_commit(root, {"docs/a.md": "# a\n"})
    code, out = _run(root, "whole", "--base", "base", "--template", "false {paths}")
    assert code != 0 and _diff_kinds_item(out)["unknown"]
