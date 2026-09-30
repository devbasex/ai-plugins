"""supervise.py のエントリポイントの契約を、DBOS へ移す前の版で固定する（#1142 のスプリント 2c・C-3a・C-3b）。

- C-3a: `new <種別>`（sprint・impl・fix・check・release・close）が書き出すプランの JSON を、同じ引数で
  `fixtures/supervise-contract/plans/` と突き合わせる。パスは `<TMP>`（テストの一時ディレクトリ）と `<NDF>`
  （プラグインの根）に置き換えて比べる。他のプランの無いディレクトリで書き出す
- C-3a の後半: 移す前に書き出したプラン（`fixtures/supervise-contract/runnable/`）を今の `run` で流せる
- C-3b: 副命令 10 個（と `new` の種別・`history import`）の usage の引数、代表の結果 JSON の鍵と終了コード
  （`run` の完了・関門で 0 / ほかは 3、`wait` の done 0 / attention 20 / 上限 3）を `contract.json` と突き合わせる

固定した値は移す前の版で書き出したものである。移す移行ステップは、この固定を直さずに通す。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "supervise-contract"
SUBCOMMANDS = ["run", "history", "expected", "example", "new", "queue", "wait", "design-glossary", "note", "sync-check"]
KINDS = ["sprint", "impl", "fix", "check", "release", "close"]
USAGE_OF = [[s] for s in SUBCOMMANDS] + [["new", k] for k in KINDS] + [["history", "import"]]


def cli(*args: str, cwd: Path, timeout: float = 120) -> subprocess.CompletedProcess:
    env = dict(os.environ, COLUMNS="200")
    return subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)


def normalize(text: str, tmp: Path) -> str:
    for real, mark in ((tmp, "<TMP>"), (PLUGIN, "<NDF>")):
        for form in {str(real.resolve()), str(real)}:
            text = text.replace(form, mark)
    return text


def last_json(stdout: str) -> dict:
    return json.loads(stdout.strip().splitlines()[-1])


def shape(res: dict) -> dict:
    """結果 JSON の鍵の形（最上位の鍵・items の要素の鍵・metrics の鍵）。"""
    items = res.get("items") or []
    return {
        "keys": sorted(res),
        "item_keys": sorted({k for it in items if isinstance(it, dict) for k in it}),
        "metric_keys": sorted(res.get("metrics") or {}),
    }


# ---- C-3a: new <種別> のプランの JSON ----


def make_repo(tmp: Path) -> Path:
    root = tmp / "repo"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "main", "production_branch": "main"}\n')
    decl = {
        "version": 1,
        "test": {"command": "make test {paths}"},
        "release": {"form": "package-plugin", "plugin": "foo", "runtimes": ["claude"]},
    }
    (root / ".ndf" / "supervise.json").write_text(json.dumps(decl) + "\n")
    return root


def new_argv(root: Path, out: Path) -> dict[str, list[str]]:
    w = ["--worktree", str(root)]
    return {
        "sprint": ["new", "sprint", *w, "--issue", "16", "17", "--design", "16", "--name", "m9", "--version", "1.2.0-dev.1", "--out", str(out / "sprint")],
        "impl": ["new", "impl", *w, "--issue", "11", "--title", "Impl: x", "--files", "src/a.py", "--tests", "tests/test_a.py", "--out", str(out / "impl.json")],
        "fix": ["new", "fix", *w, "--issue", "12", "--title", "Fix: x", "--tests", "tests/test_x.py", "--out", str(out / "fix.json")],
        "check": ["new", "check", *w, "--issue", "13", "--pr", "5", "--out", str(out / "check.json")],
        "release": ["new", "release", *w, "--issue", "14", "--version", "1.2.0-dev.1", "--prs", "5", "6", "--channel", "dev", "--repo", "o/r", "--out", str(out / "release.json")],
        "close": [
            "new", "close", *w, "--issue", "15", "--name", "m9", "--version", "1.2.0-dev.2", "--prod", "1.2.0",
            "--state", str(out / "sprint" / "sprint.json"), "--out", str(out / "close"),
        ],
    }  # fmt: skip


def render_plans(tmp: Path) -> dict[str, dict]:
    """種別ごとに `new` を打ち、書き出したファイル（種別/相対パス → 中身）を返す。close は sprint の状態を読む。"""
    root = make_repo(tmp)
    out = tmp / "out"
    out.mkdir()
    files: dict[str, dict] = {}
    for kind, argv in new_argv(root, out).items():
        p = cli(*argv, cwd=root)
        assert p.returncode == 0, (kind, p.stderr)
        target = Path(argv[argv.index("--out") + 1])
        written = sorted(target.rglob("*.json")) if target.is_dir() else [target]
        for f in written:
            name = f"{kind}/{f.relative_to(target).as_posix()}" if target.is_dir() else f"{kind}.json"
            files[name] = json.loads(normalize(f.read_text(), tmp))
    return files


def test_new_writes_the_same_plans_as_before_the_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    got = render_plans(tmp_path)
    want = {f.relative_to(FIXTURES / "plans").as_posix(): json.loads(f.read_text()) for f in sorted((FIXTURES / "plans").rglob("*.json"))}
    assert sorted(got) == sorted(want)
    for name in want:
        assert got[name] == want[name], name


# ---- C-3a の後半と C-3b: run・queue・wait の結果と終了コード ----


def runnable(tmp: Path, name: str) -> Path:
    """移す前に書き出したプランを、作業場所を今の一時ディレクトリにして置く。"""
    dst = tmp / f"{name}.json"
    dst.write_text((FIXTURES / "runnable" / f"{name}.json").read_text().replace("<TMP>", str(tmp)))
    return dst


def observe_results(tmp: Path) -> dict:
    """代表の打ち方の終了コードと結果の形。"""
    obs: dict = {}
    for name in ("ok", "gate", "stop"):
        p = cli("run", str(runnable(tmp, name)), cwd=tmp)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("- 結果: ")), None)
        obs[f"run {name}"] = {"exit": p.returncode, "result": line}
    p = cli("example", cwd=tmp)
    obs["example"] = {"exit": p.returncode, "keys": sorted(json.loads(p.stdout))}
    p = cli("expected", str(runnable(tmp, "ok")), cwd=tmp)
    obs["expected"] = {"exit": p.returncode, **shape(last_json(p.stdout))}
    root = make_repo(tmp / "new")
    p = cli(*new_argv(root, tmp / "new")["impl"], cwd=root)
    obs["new impl"] = {"exit": p.returncode, **shape(last_json(p.stdout))}

    done = tmp / "q" / "done.json"
    p = cli("queue", str(runnable(tmp, "ok")), "--max", "1", "--poll", "0.2", "--done", str(done), cwd=tmp)
    obs["queue ok"] = {"exit": p.returncode, **shape(last_json(p.stdout))}
    p = cli("wait", str(done), "--timeout", "5", "--poll", "0.2", cwd=tmp)
    obs["wait done"] = {"exit": p.returncode, **shape(last_json(p.stdout))}

    done = tmp / "q2" / "done.json"
    p = cli("queue", str(runnable(tmp, "stop")), "--poll", "0.2", "--done", str(done), cwd=tmp)
    obs["queue stop"] = {"exit": p.returncode, **shape(last_json(p.stdout)), "results": [it.get("result") for it in last_json(p.stdout)["items"]]}

    done = tmp / "q3" / "done.json"
    p = cli("queue", str(runnable(tmp, "gate")), "--then", str(runnable(tmp, "ok")), "--poll", "0.2", "--done", str(done), cwd=tmp)
    events = [json.loads(ln) for ln in p.stdout.strip().splitlines()[:-1] if ln.startswith("{")]
    obs["queue gate"] = {
        "exit": p.returncode,
        **shape(last_json(p.stdout)),
        "results": [it.get("result") for it in last_json(p.stdout)["items"]],
        "events": sorted({e.get("event") for e in events}),
        "event_keys": sorted({k for e in events for k in e}),
    }
    done.unlink()  # 終わりを消すと、wait は一覧から attention の行を読む
    p = cli("wait", str(done), "--timeout", "5", "--poll", "0.2", cwd=tmp)
    obs["wait attention"] = {"exit": p.returncode, **shape(last_json(p.stdout))}
    p = cli("wait", str(tmp / "none" / "done.json"), "--timeout", "0.3", "--poll", "0.1", cwd=tmp)
    obs["wait timeout"] = {"exit": p.returncode, **shape(last_json(p.stdout))}
    return obs


def observe_usage(tmp: Path) -> dict:
    """副命令ごとの usage（空白を 1 つに詰めたもの）。"""
    usage = {}
    for argv in USAGE_OF:
        p = cli(*argv, "--help", cwd=tmp)
        assert p.returncode == 0, (argv, p.stderr)
        block = p.stdout.split("\n\n", 1)[0]
        usage[" ".join(argv)] = re.sub(r"\s+", " ", block).strip()
    return usage


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads((FIXTURES / "contract.json").read_text())


def test_subcommands_keep_their_arguments(tmp_path, contract):
    assert observe_usage(tmp_path) == contract["usage"]


def test_results_keep_their_keys_and_exit_codes(tmp_path, monkeypatch, contract):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    got = observe_results(tmp_path)
    for name, want in contract["results"].items():
        assert got[name] == want, name
    assert sorted(got) == sorted(contract["results"])


def test_the_fixture_plans_run_without_claude(tmp_path, monkeypatch):
    """runnable/ のプランは run のステップだけで、claude も gh も呼ばない（PATH から外しても流れる）。"""
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("claude", "gh"):
        stub = bindir / tool
        stub.write_text("#!/bin/sh\necho called >&2\nexit 99\n")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    p = cli("run", str(runnable(tmp_path, "ok")), cwd=tmp_path)
    assert p.returncode == 0 and "called" not in p.stderr, p.stderr
    assert shutil.which("claude") == str(bindir / "claude")
