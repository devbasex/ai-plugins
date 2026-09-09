"""巻き直しの投稿を待ち行列と再実行へ通す（#291、受け入れ条件 4）。

`rotate-pr.sh` が行う投稿は 4 種ある。**扱いは 2 つに分かれる。**

| 投稿 | 上限のときの扱い | なぜ |
| --- | --- | --- |
| `gh pr comment` | 待ち行列へ積み、終了コード 0 で先へ進む | 宛先は決まっており、後から送れる |
| `gh pr close` / `gh pr create` / `gh pr reopen` | 回復を待って再実行する | 作成が終わるまで新しい番号が決まらず、番号が決まらないと以降のすべての項目の宛先が決まらない |

待つあいだラウンドは進まないが、巻き直しは 8 ラウンドに 1 度しか起きない。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROTATE = HERE.parent / "scripts" / "rotate-pr.sh"
QUEUE_PY = HERE.parents[2] / "scripts" / "lib" / "post_queue.py"

# 上限のときに待ち行列か再実行を通さなければならない投稿。
POSTING_COMMANDS = ("gh pr comment", "gh pr close", "gh pr create", "gh pr reopen")


def _code_lines(path: pathlib.Path) -> list[str]:
    """説明文を除いた行。書かれている手順だけを見る。"""
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]


@pytest.mark.parametrize("command", POSTING_COMMANDS, ids=lambda c: c.replace(" ", "-"))
def test_no_posting_command_is_called_bare(command: str) -> None:
    """素の呼び出しが残っていると、その 1 箇所だけが上限で止まる。"""
    bare = [line.strip() for line in _code_lines(ROTATE)
            if command in line and "gh_retry" not in line and "post_pr_comment" not in line]
    assert bare == [], bare


def test_the_posting_helpers_are_defined() -> None:
    body = ROTATE.read_text(encoding="utf-8")
    assert "post_pr_comment()" in body
    assert "gh_retry()" in body


def test_the_shared_layer_is_reached_without_cd() -> None:
    """指し方の契約（`plugins/ndf/scripts/lib/README.md`）に従う。"""
    body = ROTATE.read_text(encoding="utf-8")
    assert "../../../scripts/lib/post_queue.py" in body
    assert 'cd -- "$SCRIPT_DIR/../../../scripts/lib"' not in body
    assert (ROTATE.parent / "../../../scripts/lib/post_queue.py").exists()


# ---- 再実行と積み込みの振る舞い（模した `gh` を通す） ----


def _queue_cli(*args: str, env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(QUEUE_PY), *args],
                          capture_output=True, text=True, timeout=120, env=env)


def _env(fake_gh, **over) -> dict:
    import os
    env = {**os.environ, "PATH": f"{fake_gh.dir}{os.pathsep}{os.environ['PATH']}",
           "GH_FAKE_LOG": str(fake_gh.log)}
    env.pop("GH_FAKE_MODE", None)
    env.pop("GH_FAKE_RULES", None)
    env.update(over)
    return env


def test_the_rollback_commands_wait_and_run_again(fake_gh) -> None:
    """上限のあいだ再実行し、回復したら成功する。"""
    fake_gh.set_rules([
        {"match": "", "calls_lt": 3,
         "stdout": '{"message":"API rate limit exceeded.","status":"403"}',
         "stderr": "gh: API rate limit exceeded. (HTTP 403)\n", "exit": 1},
        {"match": "", "stdout": "https://github.com/o/r/pull/9\n"},
    ])
    out = _queue_cli("retry", "--interval", "0.01", "--max-wait", "1",
                     "--", "gh", "pr", "close", "8",
                     env=_env(fake_gh, GH_FAKE_RULES=str(fake_gh.rules_file)))

    assert out.returncode == 0, out.stderr
    assert len(fake_gh.calls()) == 4          # 上限 3 回 + 回復した 1 回
    assert "https://github.com/o/r/pull/9" in out.stdout


def test_the_rollback_commands_do_not_retry_other_failures(fake_gh) -> None:
    """権限の誤りは待っても直らない。1 回で返す。"""
    out = _queue_cli("retry", "--interval", "0.01", "--max-wait", "1",
                     "--", "gh", "pr", "close", "8",
                     env=_env(fake_gh, GH_FAKE_MODE="forbidden"))

    assert out.returncode == 1
    # 403 は上限とも権限の誤りとも読めるため、残り回数を 1 度だけ引いて決める。
    # 対象のコマンドは 1 度しか実行しない。
    assert [c for c in fake_gh.joined() if c.startswith("pr close")] == ["pr close 8"]
    assert any("rate_limit" in c for c in fake_gh.joined())


def test_a_comment_is_queued_instead_of_waiting(fake_gh, tmp_path) -> None:
    """コメントは待たずに積む。待ち行列があれば工程は止まらない。"""
    qdir = tmp_path / "pending"
    body = tmp_path / "body.txt"
    body.write_text("ℹ️ 巻き直しの案内", encoding="utf-8")
    out = _queue_cli("post", "--dir", str(qdir), "--kind", "pr-comment",
                     "--repo", "o/r", "--pr", "8", "--body-file", str(body),
                     "--actor", "takemi",
                     env=_env(fake_gh, GH_FAKE_MODE="rate_limit"))

    assert out.returncode == 0, out.stderr
    assert "QUEUED=1" in out.stdout
    assert len(list(qdir.glob("*.json"))) == 1


# ---- execute --mode light の失敗時の旧 PR 復旧（R2-001, 現状固定） ----
#
# 既存の検査は投稿コマンドの静的検査と retry の単独実行にとどまり、`execute` における
# 旧 PR の close → 新 PR 作成失敗 → ERR trap による旧 PR 復旧のつながりを実行していない。
# 公開入口から通し、git は成功する代替・gh は PR の開閉状態を記録する代替に差し替える
# （外部サービスへは接続しない）。正しさを主張しない現状固定テスト。

_STATE_PR = 9
_OLD_PR = 9
_NEW_PR = 10
_REPO = "o/r"
_HEAD_BRANCH = "feature/x"

# gh の代替。呼び出しを記録し、PR の開閉状態を `GH_STORE` に書く。`create` の成否だけを
# 環境変数 `GH_CREATE` で切り替える（"ok" で成功、それ以外は 422 で失敗）。
_FAKE_GH_EXECUTE = '''#!/usr/bin/env python3
import sys, json, os
store = os.environ["GH_STORE"]
calllog = os.environ["GH_CALLS"]
argv = sys.argv[1:]
with open(calllog, "a", encoding="utf-8") as f:
    f.write(" ".join(argv) + "\\n")

def load():
    with open(store, encoding="utf-8") as f:
        return json.load(f)

def save(s):
    with open(store, "w", encoding="utf-8") as f:
        json.dump(s, f)

if argv[:1] == ["api"]:
    try:
        sys.stdin.read()
    except Exception:
        pass
    sys.stdout.write('{"id":1}')
    sys.exit(0)
if argv[:2] == ["pr", "close"]:
    s = load(); s[argv[2]] = "closed"; save(s); sys.exit(0)
if argv[:2] == ["pr", "reopen"]:
    s = load(); s[argv[2]] = "open"; save(s); sys.exit(0)
if argv[:2] == ["pr", "create"]:
    try:
        sys.stdin.read()
    except Exception:
        pass
    if os.environ.get("GH_CREATE") == "ok":
        s = load(); s[str(%(new_pr)d)] = "open"; save(s)
        sys.stdout.write("https://github.com/o/r/pull/%(new_pr)d\\n")
        sys.exit(0)
    sys.stderr.write("gh: Validation Failed (HTTP 422)\\n")
    sys.exit(1)
sys.stderr.write("unexpected gh call: %%r\\n" %% argv)
sys.exit(3)
''' % {"new_pr": _NEW_PR}

_FAKE_GIT = "#!/usr/bin/env bash\nexit 0\n"


class _Rotation:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.worktree = tmp_path / "wt"
        self.worktree.mkdir()
        self.tmp = tmp_path / "tmp"
        self.tmp.mkdir()
        self.store = tmp_path / "pr-state.json"
        self.calls = tmp_path / "gh-calls.log"

        (self.bin / "git").write_text(_FAKE_GIT, encoding="utf-8")
        (self.bin / "git").chmod(0o755)
        (self.bin / "gh").write_text(_FAKE_GH_EXECUTE, encoding="utf-8")
        (self.bin / "gh").chmod(0o755)

        self.store.write_text(json.dumps({str(_OLD_PR): "open"}), encoding="utf-8")
        self.calls.write_text("", encoding="utf-8")

        (self.tmp / f"cross-review-pr{_STATE_PR}-state.json").write_text(
            json.dumps({
                "worktree_path": str(self.worktree),
                "current_pr": _OLD_PR,
                "repo": _REPO,
                "viewer_login": "tester",
                "rounds": [{"round": 1, "pr": _OLD_PR}],
            }),
            encoding="utf-8",
        )
        (self.tmp / f"rotate-pr{_STATE_PR}-prepare.json").write_text(
            json.dumps({
                "head_branch": _HEAD_BRANCH,
                "base_branch": "develop",
                "is_draft": False,
                "old_title": "t",
            }),
            encoding="utf-8",
        )
        (self.tmp / f"rotate-pr{_STATE_PR}-newtext.json").write_text(
            json.dumps({"title": "New title", "body": "New body"}),
            encoding="utf-8",
        )

    def run(self, create_ok: bool) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "CROSS_REVIEW_TMP_DIR": str(self.tmp),
            "GH_STORE": str(self.store),
            "GH_CALLS": str(self.calls),
            "GH_CREATE": "ok" if create_ok else "fail",
        }
        return subprocess.run(
            ["bash", str(ROTATE), "execute", str(_STATE_PR), "--mode", "light"],
            capture_output=True, text=True, timeout=180, env=env,
        )

    def pr_states(self) -> dict:
        return json.loads(self.store.read_text(encoding="utf-8"))

    def gh_calls(self) -> list[str]:
        return [c for c in self.calls.read_text(encoding="utf-8").splitlines() if c.strip()]


@pytest.fixture()
def rotation(tmp_path) -> _Rotation:
    return _Rotation(tmp_path)


def test_a_create_failure_leaves_the_old_pr_open_and_no_new_pr(rotation: _Rotation) -> None:
    """新 PR 作成が失敗すると、非ゼロ終了で旧 PR が open へ戻り、NEW_PR は出ない。"""
    out = rotation.run(create_ok=False)

    assert out.returncode != 0, out.stderr
    states = rotation.pr_states()
    assert states[str(_OLD_PR)] == "open"          # reopen で戻る
    assert str(_NEW_PR) not in states              # 新 PR は作られていない
    assert "NEW_PR=" not in out.stdout             # 成功結果を出力していない
    joined = rotation.gh_calls()
    assert any(c.startswith(f"pr close {_OLD_PR}") for c in joined)
    assert any(c.startswith(f"pr reopen {_OLD_PR}") for c in joined)


def test_a_create_success_closes_the_old_pr_and_opens_the_new_pr(rotation: _Rotation) -> None:
    """比較用: 作成が成功すると旧 PR は closed、新 PR は open、NEW_PR が作成結果を指す。"""
    out = rotation.run(create_ok=True)

    assert out.returncode == 0, out.stderr
    states = rotation.pr_states()
    assert states[str(_OLD_PR)] == "closed"
    assert states[str(_NEW_PR)] == "open"
    assert f"NEW_PR={_NEW_PR}" in out.stdout
    assert not any(c.startswith("pr reopen") for c in rotation.gh_calls())
