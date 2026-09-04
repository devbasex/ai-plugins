"""pytest 共通フィクスチャ。

`scripts/state.py` は uv self-contained script として `#!/usr/bin/env -S uv run --script`
で起動される運用だが、テストでは関数を直接 import したい。
importlib.util で source loader 経由で読み込む。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import types

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "state.py"
_MONITOR = _HERE.parent / "scripts" / "monitor.py"


def _load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_state_module() -> types.ModuleType:
    return _load_module("cross_review_state", _SCRIPT)


def _load_monitor_module() -> types.ModuleType:
    return _load_module("cross_review_monitor", _MONITOR)


# 待ち行列は共通層に置く。指し方は `plugins/ndf/scripts/lib/README.md` の契約に従う
# （Python は `parents[3]` から解決する。ここは tests/ なので 1 つ浅い）。
_POST_QUEUE = _HERE.parents[2] / "scripts" / "lib" / "post_queue.py"


import pytest


# 既定で差し替える、GitHub を読みに行く関数。実物は `_REAL` へ退避する。
_GITHUB_LOOKUPS = ("_fetch_check_runs", "_fetch_pr_metadata")
_REAL: dict[str, object] = {}


@pytest.fixture(scope="session")
def state_mod() -> types.ModuleType:
    mod = _load_state_module()
    for name in _GITHUB_LOOKUPS:
        _REAL[name] = getattr(mod, name)
    return mod


@pytest.fixture(scope="session")
def monitor_mod() -> types.ModuleType:
    return _load_monitor_module()


@pytest.fixture(autouse=True)
def _no_github(monkeypatch, state_mod) -> None:
    """テストから GitHub を呼ばない。

    収束の判定は継続的統合を照会するようになった（#327）。差し替えを忘れると、
    テストが実物の `gh` を起動して枠を消費し、対象のリポジトリの状態で結果が変わる。
    **差し替えていない `gh` の実行はその場で落とす。**

    `subprocess.run` そのものを差し替えるテストは、この見張りを上書きして先へ進む。
    """
    real = subprocess.run

    def _guard(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]) == "gh":
            raise AssertionError(
                f"テストが gh を実行しようとしました: {list(cmd)}。"
                " 呼び出しを差し替えてください"
            )
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guard)
    # 照会は既定で「確かめられなかった」に倒す。判定は収束を止めない側へ倒すため、
    # 検査ジョブを見ない既存のテストは期待値を変えずに通る。
    monkeypatch.setattr(state_mod, "_fetch_check_runs", lambda repo, sha: None)
    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", lambda pr, repo=None: None)


@pytest.fixture()
def real_github(monkeypatch, state_mod):
    """既定の差し替えを外し、実物の取得を戻す。

    取得そのものの組み立てを見るテストが使う。GitHub へは `_gh_rest` か
    `subprocess.run` の差し替えで届かないようにする。
    """
    for name in _GITHUB_LOOKUPS:
        monkeypatch.setattr(state_mod, name, _REAL[name])


# ---- 模した `gh` を PATH の先頭へ置く（#291） ----
#
# 待ち行列は上限の応答の形（終了コード・標準出力の `message`・標準エラーの
# `(HTTP <番号>)`）で判断する。差し替えを関数の単位で行うと、その形そのものを
# 検査できない。**実物の `subprocess.run` で、模した `gh` を起動する。**
#
# 上の見張り（`_no_github`）は `subprocess.run` を差し替えて `gh` の実行を落とす。
# ここで素の実装へ戻すため、**この fixture を使うテストだけ**が見張りの外に出る。

# 見張りが入る前の実装を、import の時点で押さえる。
_REAL_RUN = subprocess.run

_FAKE_GH = '''#!/usr/bin/env python3
"""テスト用の `gh`。呼び出しを記録し、規則に沿った応答を返す。"""
import json, os, sys

argv = sys.argv[1:]
joined = " ".join(argv)
try:
    stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
except Exception:
    stdin = ""

log = os.environ.get("GH_FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False) + "\\n")

rules_file = os.environ.get("GH_FAKE_RULES")
rules = json.load(open(rules_file, encoding="utf-8")) if rules_file else []
for rule in rules:
    if rule.get("match", "") in joined:
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(int(rule.get("exit", 0)))

# 上限とほかの失敗を模す。形は実測に合わせた（gh 2.100.0）。
mode = os.environ.get("GH_FAKE_MODE", "ok")
SHAPES = {
    "rate_limit": (
        '{"message":"API rate limit exceeded for user ID 10234200.","status":"403"}',
        "gh: API rate limit exceeded for user ID 10234200. (HTTP 403)", 1),
    "secondary": (
        '{"message":"You have exceeded a secondary rate limit.","status":"403"}',
        "gh: You have exceeded a secondary rate limit. (HTTP 403)", 1),
    "forbidden": (
        '{"message":"Resource not accessible by integration","status":"403"}',
        "gh: Resource not accessible by integration (HTTP 403)", 1),
    "not_found": (
        '{"message":"Not Found","status":"404"}',
        "gh: Not Found (HTTP 404)", 1),
}
if mode in SHAPES:
    out, err, code = SHAPES[mode]
    sys.stdout.write(out)
    sys.stderr.write(err + "\\n")
    sys.exit(code)

sys.stdout.write("[]")
'''


class FakeGh:
    """模した `gh` の置き場所と、記録された呼び出しの読み出し。"""

    def __init__(self, directory: pathlib.Path, log: pathlib.Path,
                 rules: pathlib.Path, monkeypatch) -> None:
        self.dir = directory
        self.log = log
        self.rules_file = rules
        self._monkeypatch = monkeypatch

    def set_rules(self, rules: list[dict]) -> None:
        """先に当たった規則の応答を返す。`match` は引数を空白で連ねた文字列の部分一致。"""
        self.rules_file.write_text(json.dumps(rules), encoding="utf-8")
        self._monkeypatch.setenv("GH_FAKE_RULES", str(self.rules_file))

    def set_mode(self, mode: str) -> None:
        self._monkeypatch.setenv("GH_FAKE_MODE", mode)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def argv(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls()]

    def joined(self) -> list[str]:
        return [" ".join(c["argv"]) for c in self.calls()]


@pytest.fixture()
def fake_gh(monkeypatch, tmp_path) -> FakeGh:
    import json as _json  # noqa: F401  (FakeGh が使う)

    bindir = tmp_path / "fake-bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "gh"
    script.write_text(_FAKE_GH, encoding="utf-8")
    script.chmod(0o755)
    log = tmp_path / "gh-calls.log"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("GH_FAKE_LOG", str(log))
    monkeypatch.delenv("GH_FAKE_RULES", raising=False)
    monkeypatch.delenv("GH_FAKE_MODE", raising=False)
    # 見張りを外す。この fixture を使うテストだけが実物の `subprocess.run` を通る。
    monkeypatch.setattr(subprocess, "run", _REAL_RUN)
    return FakeGh(bindir, log, tmp_path / "gh-rules.json", monkeypatch)


@pytest.fixture(scope="session")
def queue_mod() -> types.ModuleType:
    """共通層の待ち行列モジュール（#291）。"""
    return _load_module("ndf_post_queue", _POST_QUEUE)
