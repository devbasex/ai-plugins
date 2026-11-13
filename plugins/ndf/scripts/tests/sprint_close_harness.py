"""sprint-close.py・merged-steps.py ほかのテストに共通する土台（cross-refactoring I-013 で test_sprint_close_merge_green.py から移した）。

gh は PATH の先頭に置いた偽物で置き換える。偽物は FAKE_GH_STATE の JSON ファイルを読み書きし、
呼ばれた引数を calls に積む。fixture の `repo`（一時の git リポジトリ）と `gh`（偽の gh と隔離した環境）は、
使うテストのモジュールが import して使う。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402

sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable

FAKE_GH = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
def save():
    json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
def opt(name):
    return a[a.index(name) + 1] if name in a else None
out, code = None, 0
if a[:2] == ["repo", "view"]:
    out = st.get("repo", "o/r") if "-q" in a else json.dumps({{"owner": {{"login": "o"}}, "name": "r"}})
elif a[:2] == ["pr", "view"]:
    n = a[2]
    if "body,comments" in a:
        rec = st.get("records", {{}}).get(n)
        out, code = (json.dumps(rec), 0) if rec is not None else (None, 1)
    elif "title,body" in a:
        body = st.get("bodies", {{}}).get(n)
        title = st.get("titles", {{}}).get(n, "")
        out, code = (json.dumps({{"title": title, "body": body}}), 0) if body is not None else (None, 1)
    elif "-q" in a:
        body = st.get("bodies", {{}}).get(n)
        out, code = (body, 0) if body is not None else (None, 1)
    else:
        seq = st.get("pr_seq", {{}}).get(n)
        if seq:
            cur = seq.pop(0) if len(seq) > 1 else seq[0]
            out = json.dumps({{"baseRefName": "develop", **cur}})  # 宛先は本番チャネル（main）でない既定
        else:
            code = 1
elif a[:2] == ["pr", "merge"]:
    code = st.get("merge_code", 0)
    if code == 0:
        for s in st.get("pr_seq", {{}}).get(a[2], []):
            s["state"] = "MERGED"
    else:
        sys.stderr.write(st.get("merge_err", "merge failed") + "\n")
elif a[:2] == ["pr", "list"]:
    out = json.dumps(st.get("open_prs", []))
elif a[:2] == ["pr", "create"]:
    code = st.get("create_code", 0)
    if code == 0:
        out = "https://github.com/o/r/pull/" + str(st.get("create_number", 77))
    else:
        sys.stderr.write(st.get("create_err", "create failed") + "\n")
elif a[:2] == ["pr", "ready"]:
    code = st.get("ready_code", 0)
    if code == 0:
        for s in st.get("pr_seq", {{}}).get(a[2], []):
            s["isDraft"] = False
    else:
        sys.stderr.write("ready failed\n")
elif a[:2] == ["run", "view"]:
    seq = st.get("runs", {{}}).get(a[2])
    if seq:
        out = json.dumps(seq.pop(0) if len(seq) > 1 else seq[0])
    else:
        code = 1
elif a[:2] == ["run", "rerun"]:
    code = st.get("rerun_code", 0)
elif a[:2] == ["run", "list"]:
    out = json.dumps([{{"databaseId": i}} for i in range(st.get("queued_runs", 0))])
elif a[:2] == ["issue", "view"]:
    key = f"{{opt('--repo')}}#{{a[2]}}"
    seq = st.get("issues", {{}}).get(key)
    if seq is None:
        code = 1
    else:
        out = seq.pop(0) if len(seq) > 1 else seq[0]
        if out is None:
            code = 1
        elif "state,body" in a:
            out = json.dumps({{"state": out, "body": st.get("issue_bodies", {{}}).get(key, "")}})
elif a[:3] == ["api", "-X", "PATCH"]:
    m = __import__("re").match(r"repos/(.+)/issues/(\d+)$", a[4])
    key = f"{{m.group(1)}}#{{m.group(2)}}"
    if st.get("write_code", 0):
        code = 1
        sys.stderr.write("write failed\n")
    else:
        st.setdefault("issue_bodies", {{}})[key] = json.load(sys.stdin)["body"]
        st.setdefault("written", []).append(key)
        out = "HTTP/2.0 200 OK\n\n{{}}"
elif a[:2] == ["issue", "close"]:
    key = f"{{opt('--repo')}}#{{a[2]}}"
    st.setdefault("closed", []).append(key)
    seq = st.get("issues", {{}}).get(key)
    if seq is not None and st.get("close_works", True):
        st["issues"][key] = ["CLOSED"]
    out = "closed"
else:
    code = 1
save()
if out is not None:
    print(out)
sys.exit(code)
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    (root / "keep.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "branch", "main")  # 本番チャネル（既定ブランチ）。develop 宛ての PR はリリース承認ゲートに当たらない
    return root


@pytest.fixture
def gh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    f.chmod(0o755)
    state = tmp_path / "gh-state.json"
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["FAKE_GH_STATE"] = str(state)
    env["NDF_PRESENTATION_DIR"] = str(tmp_path / "pres")
    env["NDF_WORKTREE_BASE"] = str(tmp_path / "wtbase")
    env["TMPDIR"] = str((tmp_path / "systmp").resolve())  # 片付け（run_state.sweep_states）が手元の一時の置き場に触れない
    (tmp_path / "systmp").mkdir(exist_ok=True)
    env["NDF_LOCK_DIR"] = str(tmp_path / "locks")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GH_TOKEN", "GITHUB_TOKEN"):  # REST も偽の gh を通す
        env.pop(k, None)

    class G:
        def set(self, **kw):
            state.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

        def get(self):
            return json.loads(state.read_text(encoding="utf-8"))

    g = G()
    g.env = env
    g.set()
    return g


def call(script, args, env, cwd):
    p = inproc.run(SCRIPTS / script, *args, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    if out is not None:
        assert validate_result(out, p.returncode) == [], (out, p.returncode)
    return p.returncode, out, p.stderr


DIST_PROD = "## リリース記録\n段階: 本番（2026-09-24 承認）\n版: 1.0.0 → 1.1.0（MINOR）\nスプリント: PR #11 / #12\n"


def verify_block(ver, rows):
    table = "| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |\n| --- | --- | --- | --- | --- |\n"
    table += "".join(f"| {k} | c | x | t | {r} |\n" for k, r in rows)
    return f"## リリース後テスト\n対象の版: {ver}（2026-09-24）\n\n{table}\n合否: 済\n"


def issues_of(out):
    return {f"{i['repo']}#{i['number']}": i for i in out["items"] if i["kind"] == "issue"}


def load_sprint_close():
    spec = importlib.util.spec_from_file_location("sprint_close", SCRIPTS / "sprint-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
