"""behavior-check.py: 振る舞いの確認の対象・記録・確認（#2925 の AC9・I6）。gh は偽物を PATH に置く。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "behavior-check.py"
HEAD = "a" * 40
OLD = "b" * 40

FAKE_GH = """#!{py}
import json, os, sys
state = os.environ["FAKE_GH_STATE"]
data = json.load(open(state))
args = sys.argv[1:]
if args[:1] == ["api"] and "/files" in args[-1]:
    print(json.dumps([{{"filename": f, "status": s}} for f, s in data["files"]]))
elif args[:1] == ["api"] and "/comments" in args[-1]:
    print(json.dumps([{{"body": b}} for b in data["comments"]]))
elif args[:2] == ["pr", "view"]:
    print(json.dumps({{"headRefOid": data["head"]}}))
elif args[:2] == ["pr", "comment"]:
    data["comments"].append(sys.stdin.read())
    json.dump(data, open(state, "w"))
else:
    sys.exit(9)
"""


@pytest.fixture
def gh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH.format(py=sys.executable))
    (bin_dir / "gh").chmod(0o755)
    state = tmp_path / "state.json"

    def setup(files, head=HEAD, comments=()):
        state.write_text(json.dumps({"files": [[f, s] for f, s in files], "head": head, "comments": list(comments)}))

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(state)}
    root = tmp_path / "repo"
    root.mkdir()
    return setup, env, root, state


def run(env, root, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True, env=env)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


SKILL_FILES = [
    ("plugins/ndf/skills/design/SKILL.md", "modified"),
    ("plugins/ndf/skills/design/references/a.md", "added"),
    ("plugins/ndf/agents/worker.md", "modified"),
    ("docs/guide.md", "modified"),
    ("plugins/ndf/skills/gone/SKILL.md", "removed"),
]


def test_targets_group_behavior_docs_by_skill_and_agent(gh):
    setup, env, root, _ = gh
    setup(SKILL_FILES)
    code, out = run(env, root, "targets", "--pr", "7")
    assert code == 0, out
    *targets, record = out["items"]
    assert [(t["target"], t["plugin_root"]) for t in targets] == [
        ("plugins/ndf/skills/design", "plugins/ndf"),
        ("plugins/ndf/agents/worker.md", "plugins/ndf"),
    ]
    assert record["record"]["head"] == HEAD and "post --pr 7" in record["record"]["post"]


def test_targets_without_behavior_docs_returns_three(gh):
    setup, env, root, _ = gh
    setup([("docs/guide.md", "modified"), ("src/a.py", "modified")])
    code, _ = run(env, root, "targets", "--pr", "7")
    assert code == 3


def _record(head, verdicts):
    return {
        "head": head,
        "checks": [
            {"target": t, "prompt": "p", "command": "claude -p ...", "observed": "o | x", "verdict": v} for t, v in verdicts.items()
        ],
    }


def _post(gh, record):
    setup, env, root, _ = gh
    f = root / "r.json"
    f.write_text(json.dumps(record, ensure_ascii=False))
    return run(env, root, "post", "--pr", "7", "--file", str(f))


BOTH = ("plugins/ndf/skills/design", "plugins/ndf/agents/worker.md")


def test_post_then_verify_passes_when_head_record_covers_all_targets(gh):
    setup, env, root, _ = gh
    setup(SKILL_FILES)
    code, _ = _post(gh, _record(HEAD, dict.fromkeys(BOTH, "通った")))
    assert code == 0
    code, out = run(env, root, "verify", "--pr", "7")
    assert code == 0, out


@pytest.mark.parametrize(
    "record",
    [
        _record(OLD, dict.fromkeys(BOTH, "通った")),  # 古い head の記録（確かめた後に文書を変えた）
        _record(HEAD, {BOTH[0]: "通った", BOTH[1]: "落ちた"}),
        _record(HEAD, {BOTH[0]: "通った", BOTH[1]: "確かめていない"}),
        _record(HEAD, {BOTH[0]: "通った"}),  # 足りない対象
    ],
    ids=["old-head", "failed", "unverified", "missing"],
)
def test_verify_fails_without_a_passing_head_record_for_every_target(gh, record):
    setup, env, root, _ = gh
    setup(SKILL_FILES)
    assert _post(gh, record)[0] == 0
    code, out = run(env, root, "verify", "--pr", "7")
    assert code == 1 and out["metrics"]["missing"] >= 1, out


def test_verify_reads_the_latest_head_record(gh):
    setup, env, root, _ = gh
    setup(SKILL_FILES)
    _post(gh, _record(HEAD, {BOTH[0]: "落ちた", BOTH[1]: "通った"}))
    _post(gh, _record(HEAD, dict.fromkeys(BOTH, "通った")))
    assert run(env, root, "verify", "--pr", "7")[0] == 0


def test_post_rejects_a_malformed_record(gh):
    setup, env, root, state = gh
    setup(SKILL_FILES)
    code, _ = _post(gh, {"head": HEAD, "checks": [{"target": "x", "verdict": "たぶん"}]})
    assert code == 2 and json.loads(state.read_text())["comments"] == []


def test_verify_without_targets_passes(gh):
    setup, env, root, _ = gh
    setup([("docs/guide.md", "modified")])
    assert run(env, root, "verify", "--pr", "7")[0] == 0
