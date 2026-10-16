"""進捗記録のスクリプトが自分で通過記録を積む（#725）。

hook がコマンドの文字列から推測して積む形をやめ、`progress-record.sh` が引数のチェックを通った
呼び出しごとに積む。呼び方（並べる・番号を変数で書く・片方だけを呼ぶ）、ボードの宣言・`gh` の
有無、子プロセスからの記録のどれでも、課題の本文の「進行」と通過記録が食い違わないことを確かめる。

置き場所は `CLAUDE_PLUGIN_DATA` と `XDG_STATE_HOME` で一時ディレクトリへ隔離し、`gh` は偽物に
差し替える（課題ごとの本文をファイルで持つ）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SYNC = SCRIPTS / "projects-sync.sh"
RECORD = SCRIPTS / "progress-record.sh"
AGY_RECORD = SCRIPTS.parent / "dev.agy" / "scripts" / "progress-record.sh"
WF_LIB = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"
STAGE_CHECK = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "stage-check.sh"
GUARD = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "workflow-guard.sh"
SLUG = "acme/demo"
BODY = "# 課題\n\n本文\n"


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "remote", "add", "origin", f"git@github.com:{SLUG}.git"], cwd=self.root, check=True)
        self.bodies = tmp_path / "bodies"
        self.bodies.mkdir()
        self.data = tmp_path / "data"
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        # 課題ごとの本文を `bodies/<番号>.md` に持つ偽の gh。`issue view` は本文を返し、`issue edit` は書き戻す
        (self.bin / "gh").write_text(
            f"""#!/usr/bin/env bash
case "$1 $2" in
  "issue view")
    f={self.bodies}/$3.md; [ -f "$f" ] || printf '%s' {json.dumps(BODY)} > "$f"; cat "$f" ;;
  "issue edit")
    prev=; for a in "$@"; do [ "$prev" = --body-file ] && cp "$a" {self.bodies}/$3.md; prev=$a; done ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        (self.bin / "gh").chmod(0o755)

    def env(self, **extra: str) -> dict:
        return {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "LC_ALL": "C.UTF-8",
            "CLAUDE_PLUGIN_DATA": str(self.data),
            "XDG_STATE_HOME": str(self.data / "xdg"),
            "NDF_STAGE_LOCK_TIMEOUT": "1",
            "NDF_HOOK_PYTHON": sys.executable,
            **extra,
        }

    def run(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", *args], cwd=self.root, env=env or self.env(), capture_output=True, text=True, timeout=60)

    def sh(self, script: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", script], cwd=self.root, env=env or self.env(), capture_output=True, text=True, timeout=60)

    def state_file(self, issue: int, slug: str = SLUG) -> Path:
        return self.data / "stages" / f"{slug.replace('/', '__')}__{issue}.json"

    def state(self, issue: int, slug: str = SLUG) -> dict:
        f = self.state_file(issue, slug)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    def stages(self, issue: int, slug: str = SLUG) -> list[str]:
        return self.state(issue, slug).get("stages", [])

    def body(self, issue: int) -> str:
        f = self.bodies / f"{issue}.md"
        return f.read_text(encoding="utf-8") if f.exists() else ""


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _checked(body: str) -> list[str]:
    return [line[len("- [x] ") :].split(" — ")[0] for line in body.splitlines() if line.startswith("- [x] ")]


def test_four_records_in_one_run_are_all_kept(env: Env) -> None:
    """#487: 1 回の実行に 4 行並べると、通過記録にも本文にも 4 つの工程が入る。"""
    names = ["要求と受け入れ条件", "作業場所の用意", "設計", "計画"]
    script = "\n".join(f'bash "{SYNC}" 42 stage "{n}"' for n in names)
    out = env.sh(script)
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == names
    assert _checked(env.body(42)) == names


def test_an_issue_number_in_a_variable_is_recorded(env: Env) -> None:
    """#487: 番号を変数で書いても、それぞれの課題の通過記録に入る。"""
    out = env.sh(f'for n in 11 12; do bash "{SYNC}" "$n" stage "設計"; done')
    assert out.returncode == 0, out.stderr
    assert env.stages(11) == ["設計"]
    assert env.stages(12) == ["設計"]


def test_calling_only_progress_record_is_recorded(env: Env) -> None:
    """devbase#249: `progress-record.sh` だけを呼んでも通過記録に入る。"""
    out = env.run(str(RECORD), "42", "実装")
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == ["実装"]
    assert _checked(env.body(42)) == ["実装"]


def test_one_sync_adds_exactly_one_stage(env: Env) -> None:
    """`projects-sync.sh` が中で `progress-record.sh` を呼んでも 2 件にならない（I1）。"""
    env.run(str(SYNC), "42", "stage", "設計")
    assert env.stages(42) == ["設計"]
    env.run(str(SYNC), "42", "stage", "計画")
    assert env.stages(42) == ["設計", "計画"]


def test_one_record_with_mode_and_pace_adds_each_key_once(env: Env) -> None:
    out = env.run(str(RECORD), "42", "設計", "--mode", "standard", "--pace", "fast")
    assert out.returncode == 0, out.stderr
    state = env.state(42)
    assert state["stages"] == ["設計"]
    assert state["mode"] == "standard"
    assert state["pace"] == "fast"


def test_mode_and_pace_are_recorded(env: Env) -> None:
    env.run(str(SYNC), "42", "mode", "standard")
    env.run(str(SYNC), "42", "pace", "fast")
    state = env.state(42)
    assert state["mode"] == "standard"
    assert state["pace"] == "fast"
    assert state["stages"] == []


@pytest.mark.parametrize("key,value", [("worktree", ".worktrees/feat/x"), ("plan", "issues/x.md"), ("status", "Done")])
def test_worktree_plan_and_status_do_not_touch_the_record(env: Env, key: str, value: str) -> None:
    env.run(str(SYNC), "42", "stage", "設計")
    before = env.state_file(42).read_bytes()
    out = env.run(str(SYNC), "42", key, value)
    assert out.returncode == 0, out.stderr
    assert env.state_file(42).read_bytes() == before


def test_a_repository_without_a_board_declaration_is_recorded(env: Env) -> None:
    assert not (env.root / ".ndf" / "projects.json").exists()
    out = env.run(str(SYNC), "42", "stage", "設計")
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == ["設計"]


def test_without_gh_the_stage_is_still_recorded(env: Env, tmp_path: Path) -> None:
    """`gh` が無くても積み、終了コードは 0 である。"""
    nogh = tmp_path / "nogh"
    nogh.mkdir()
    for name in (
        "bash",
        "git",
        "python3",
        "date",
        "mktemp",
        "cmp",
        "cat",
        "grep",
        "sed",
        "dirname",
        "rm",
        "cp",
        "jq",
        "mkdir",
        "mv",
        "tr",
        "head",
        "printf",
        "sleep",
        "rmdir",
        "find",
        "stat",
        "touch",
        "ls",
    ):
        found = shutil.which(name)
        if found:
            (nogh / name).symlink_to(found)
    out = env.run(str(SYNC), "42", "stage", "設計", env=env.env(PATH=str(nogh)))
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == ["設計"]
    assert env.body(42) == ""


def test_another_repository_is_recorded_under_its_own_key(env: Env) -> None:
    """`--repo` の記録は、そのリポジトリの鍵へ積み、いまのリポジトリの同じ番号は変えない（I4）。"""
    out = env.run(str(RECORD), "42", "設計", "--repo", "other/repo")
    assert out.returncode == 0, out.stderr
    assert env.stages(42, "other/repo") == ["設計"]
    assert not env.state_file(42).exists()


def test_the_refinement_stage_is_recorded_and_required_in_every_mode(env: Env) -> None:
    """棚卸しは工程表の最後の行で、5 つのモードすべてで必須である（#842）。"""
    out = env.run(str(SYNC), "42", "stage", "棚卸し")
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == ["棚卸し"]
    got = env.sh(
        f'. "{WF_LIB}"; wf_stages | tail -n 1; '
        'for m in light operation legacy-refactor standard documentation; do wf_stage_class "$m" 棚卸し; done'
    ).stdout.split()
    assert got == ["棚卸し", "R", "R", "R", "R", "R"]


def test_the_release_lists_the_missing_stages_on_stdout(env: Env) -> None:
    env.run(str(SYNC), "42", "mode", "standard")
    env.run(str(SYNC), "42", "stage", "設計")
    out = env.run(str(SYNC), "42", "stage", "配布")
    assert out.returncode == 0, out.stderr
    assert "記録なし:" in out.stdout
    assert "実装" in out.stdout
    # 案内は本文の行より先に出る
    assert out.stdout.index("記録なし:") < out.stdout.index("#42 進行 = 配布")


def test_the_release_without_a_gap_says_nothing_more(env: Env) -> None:
    env.run(str(SYNC), "42", "mode", "light")
    # 必須と条件付きの工程をすべて記録してから配布へ進む
    required = env.sh(
        f'. "{WF_LIB}"; wf_stages | while IFS= read -r s; do case "$(wf_stage_class light "$s")" in R|C) printf "%s\\n" "$s" ;; esac; done'
    )
    for name in required.stdout.splitlines():
        if name != "配布":
            env.run(str(SYNC), "42", "stage", name)
    out = env.run(str(SYNC), "42", "stage", "配布")
    assert out.returncode == 0, out.stderr
    assert "記録なし:" not in out.stdout
    assert "条件付き:" not in out.stdout
    assert out.stdout.strip() == "#42 進行 = 配布"


def test_a_conditional_stage_without_a_record_is_shown_apart(env: Env) -> None:
    """条件付きの工程は、記録が無くても必須の欠落と同じ列に並べない（light の「設計」）。"""
    env.run(str(SYNC), "42", "mode", "light")
    for name in ("要求と受け入れ条件", "作業場所の用意", "実装", "実装レビュー", "完了判定", "Pull Request", "後片付け"):
        env.run(str(SYNC), "42", "stage", name)
    out = env.run(str(SYNC), "42", "stage", "配布")
    assert "条件付き: 設計" in out.stdout
    assert "記録なし:" not in out.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("42", "tier", "x"),
        ("42", "stage", "でたらめ"),
        ("42", "mode", "でたらめ"),
        ("42", "pace", "slow"),
        ("42", "stage"),
    ],
)
def test_a_caller_error_exits_2_and_records_nothing(env: Env, args: tuple[str, ...]) -> None:
    out = env.run(str(SYNC), *args)
    assert out.returncode == 2
    assert not env.state_file(42).exists()
    assert env.body(42) == ""


def test_a_wrong_option_of_progress_record_records_nothing(env: Env) -> None:
    out = env.run(str(RECORD), "42", "設計", "--mode", "でたらめ")
    assert out.returncode == 2
    assert not env.state_file(42).exists()


def test_without_an_origin_nothing_is_recorded_but_the_body_is_written(env: Env) -> None:
    subprocess.run(["git", "remote", "remove", "origin"], cwd=env.root, check=True)
    out = env.run(str(RECORD), "42", "設計", "--mode", "standard", "--pace", "fast")
    assert out.returncode == 0, out.stderr
    assert out.stderr.count("NOTE:") == 1
    assert not (env.data / "stages").exists() or not list((env.data / "stages").glob("*.json"))
    assert _checked(env.body(42)) == ["設計"]


def test_a_failed_lock_skips_only_that_key(env: Env) -> None:
    """排他を 1 回目だけ取れないと `mode` だけが積まれず、残りは積まれる（I3）。"""
    script = (
        f'. "{WF_LIB}"\n'
        "n=0\n"
        'wf_lock_acquire() { n=$((n + 1)); [ "$n" -gt 1 ] && ndf_lock_acquire "$1" "$2"; }\n'
        f"wf_record_progress {SLUG} 42 設計 standard fast\n"
    )
    out = env.sh(script)
    assert out.returncode == 0, out.stderr
    assert out.stderr.count("NOTE:") == 1
    state = env.state(42)
    assert "mode" not in state
    assert state["pace"] == "fast"
    assert state["stages"] == ["設計"]


def test_a_held_lock_skips_every_key_but_the_body_is_written(env: Env) -> None:
    """排他を握られたままでも、終了コード 0 で本文は書き換わる。"""
    lock = Path(str(env.state_file(42)) + ".lockdir")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.mkdir()
    out = env.run(str(RECORD), "42", "設計", "--mode", "standard", "--pace", "fast")
    assert out.returncode == 0, out.stderr
    assert out.stderr.count("NOTE:") == 3
    assert not env.state_file(42).exists()
    assert _checked(env.body(42)) == ["設計"]


def test_the_body_lists_the_stages_of_the_one_stage_table(env: Env) -> None:
    """本文へ並べる工程は `wf_stages` の並びと同じである（#459。一覧は 1 か所だけ）。"""
    env.run(str(RECORD), "42", "設計")
    listed = [line[6:].split(" — ")[0] for line in env.body(42).splitlines() if line.startswith(("- [ ] ", "- [x] "))]
    table = env.sh(f'. "{WF_LIB}"; wf_stages').stdout.splitlines()
    assert listed == table
    common = (SCRIPTS / "lib" / "projects-common.sh").read_text(encoding="utf-8")
    for name in ("PJ_STAGES=", "PJ_MODES=", "PJ_PACES="):
        assert name not in common


def test_the_agy_layout_records_through_the_symlink(env: Env) -> None:
    """agy の配置（dev.agy/scripts は symlink）から呼んでも積まれる。"""
    assert AGY_RECORD.exists()
    out = env.run(str(AGY_RECORD), "42", "設計")
    assert out.returncode == 0, out.stderr
    assert env.stages(42) == ["設計"]


def test_a_stage_recorded_twice_reports_the_same(env: Env) -> None:
    """旧い hook と二重に積まれても、報告は工程の集合で判定する（前提 7）。"""
    env.run(str(SYNC), "42", "mode", "standard")
    env.run(str(SYNC), "42", "stage", "設計")
    once = env.run(str(STAGE_CHECK), "report", "42").stdout
    env.run(str(STAGE_CHECK), "record", "42", "stage", "設計")
    assert env.stages(42) == ["設計", "設計"]
    assert env.run(str(STAGE_CHECK), "report", "42").stdout == once


@pytest.mark.parametrize(
    "command",
    [
        f'bash "{SYNC}" 42 stage "設計"',
        'cat <<EOF\nbash "$SCRIPTS/projects-sync.sh" 42 stage "設計"\nEOF',
        '# bash "$SCRIPTS/projects-sync.sh" 42 stage "設計"\necho ok',
    ],
)
def test_the_hook_does_not_record_from_the_command_text(env: Env, command: str) -> None:
    """#580: hook はコマンドの文字列から通過記録を作らない（I5）。"""
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(env.root), "tool_input": {"command": command}}
    out = subprocess.run(
        ["bash", str(GUARD)],
        cwd=env.root,
        env=env.env(),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == ""
    assert not env.state_file(42).exists()


def test_the_record_stage_of_supervise_is_recorded(env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """#961: `supervise.py` の `record_stage` が子プロセスで呼んだ記録も、課題ごとに積まれる。"""
    sys.path.insert(0, str(SCRIPTS / "lib"))
    sys.path.insert(0, str(SCRIPTS))
    from supervise_lib.state import RunState  # noqa: PLC0415

    for k, v in env.env().items():
        monkeypatch.setenv(k, v)
    st = RunState(tmp_path / "sv", {})
    st.record_stage("設計", {"記録": str(SYNC), "課題": [11, 12], "進め方": "fast"}, str(env.root))
    for issue in (11, 12):
        assert env.stages(issue) == ["設計"]
        assert env.state(issue)["pace"] == "fast"


def test_one_record_adds_less_than_a_second(env: Env, tmp_path: Path) -> None:
    """排他を取れたときに足される手元の処理は 1 秒未満である（gh を除く）。"""
    script = f'. "{WF_LIB}"; wf_record_progress {SLUG} 42 設計 standard fast'
    start = time.monotonic()
    out = env.sh(script)
    elapsed = time.monotonic() - start
    assert out.returncode == 0, out.stderr
    assert elapsed < 1.0
