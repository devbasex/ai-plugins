"""gws-check.py の分岐（#744）。

gws と npm は PATH に置いた偽物で置き換える。偽物は呼ばれた引数を FAKE_CALLS の JSON Lines へ積む。
gws の `auth status` の応答は FAKE_GWS_STATUS（標準出力）と FAKE_GWS_RC（終了コード）で決める。
npm の `install -g` は FAKE_NPM_RC が 0 なら、PATH の偽物の置き場所へ gws を書き出す
（FAKE_NPM_PUTS_GWS が 0 なら書き出さない）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN_ROOT = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "gws-check.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable
SECRET = "ya29.secret-token-value"

FAKE_GWS = r"""#!{py}
import json, os, sys
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(["gws", *sys.argv[1:]]) + "\n")
sys.stderr.write("This is not an officially supported Google product.\n")
sys.stdout.write(os.environ.get("FAKE_GWS_STATUS", ""))
sys.exit(int(os.environ.get("FAKE_GWS_RC", "0")))
"""

FAKE_NPM = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(["npm", *a]) + "\n")
if a[:2] == ["prefix", "-g"]:
    print("/fake/npm-global")
    sys.exit(0)
rc = int(os.environ.get("FAKE_NPM_RC", "0"))
if a[:2] == ["install", "-g"] and rc == 0 and os.environ.get("FAKE_NPM_PUTS_GWS", "1") == "1":
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gws")
    with open(os.environ["FAKE_GWS_SRC"], encoding="utf-8") as src, open(dst, "w", encoding="utf-8") as out:
        out.write(src.read())
    os.chmod(dst, 0o755)
if rc:
    sys.stderr.write("npm ERR! code EACCES\n")
sys.exit(rc)
"""


def _status(credential_source="none", auth_method="none", client_config_exists=True, **extra) -> str:
    d = {
        "auth_method": auth_method,
        "client_config_exists": client_config_exists,
        "credential_source": credential_source,
        "storage": "none",
    }
    d.update(extra)
    return json.dumps(d)


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gws_src = tmp_path / "gws.src"
    gws_src.write_text(FAKE_GWS.format(py=PY), encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    e = {
        "PATH": str(bindir),
        "FAKE_CALLS": str(calls),
        "FAKE_GWS_SRC": str(gws_src),
        "NDF_PRESENTATION_DIR": str(tmp_path / "present"),
        "FAKE_GWS_STATUS": _status(),
    }
    return {"bin": bindir, "env": e, "calls": calls, "gws_src": gws_src}


def _put(env, name):
    p = env["bin"] / name
    src = env["gws_src"].read_text(encoding="utf-8") if name == "gws" else FAKE_NPM.format(py=PY)
    p.write_text(src, encoding="utf-8")
    p.chmod(0o755)


def _run(env, *args, **overrides):
    e = {**env["env"], **overrides}
    cp = subprocess.run([PY, str(SCRIPT), *args], capture_output=True, text=True, env=e, check=False)
    lines = cp.stdout.strip().splitlines()
    assert len(lines) == 1, cp.stdout + cp.stderr
    out = json.loads(lines[0])
    assert validate_result(out, cp.returncode) == [], out
    calls = [json.loads(x) for x in env["calls"].read_text(encoding="utf-8").splitlines()]
    return cp.returncode, out, calls


def _gws_calls(calls):
    return [c[1:] for c in calls if c[0] == "gws"]


def _npm_installs(calls):
    return [c[1:] for c in calls if c[0] == "npm" and "install" in c]


def test_authenticated_by_token_env_var(env):
    """auth_method が none でも、credential_source が none でなければ認証済み（決定 2）。"""
    _put(env, "gws")
    code, out, calls = _run(env, FAKE_GWS_STATUS=_status(credential_source="token_env_var", auth_method="none"))
    assert (code, out["status"], out["metrics"]["state"]) == (0, "ok", "authenticated")
    assert out["metrics"]["credential_source"] == "token_env_var"
    assert _gws_calls(calls) == [["auth", "status"]]


def test_unauthenticated_is_gate_11(env):
    _put(env, "gws")
    _put(env, "npm")
    code, out, calls = _run(env)
    assert (code, out["status"], out["metrics"]["state"]) == (11, "gate", "unauthenticated")
    assert "gws auth login" in out["next"]
    assert "gws auth setup" not in out["next"]
    assert _gws_calls(calls) == [["auth", "status"]]
    assert _npm_installs(calls) == []


def test_unauthenticated_without_client_config_guides_setup_first(env):
    _put(env, "gws")
    code, out, _ = _run(env, FAKE_GWS_STATUS=_status(client_config_exists=False))
    assert (code, out["metrics"]["state"], out["metrics"]["client_config_exists"]) == (11, "unauthenticated", False)
    assert out["next"].index("gws auth setup") < out["next"].index("gws auth login")


def test_missing_without_install_does_not_install(env):
    """同意の印が無ければ npm install を打たず、承認資料を書いて 10 で返す（I1）。"""
    _put(env, "npm")
    code, out, calls = _run(env)
    assert (code, out["status"], out["metrics"]["state"]) == (10, "gate", "missing")
    assert _npm_installs(calls) == []
    assert _gws_calls(calls) == []
    body = Path(out["presentation_path"]).read_text(encoding="utf-8")
    assert "npm install -g @googleworkspace/cli" in body
    assert "npm uninstall -g @googleworkspace/cli" in body


def test_uninstallable_without_npm(env):
    code, out, calls = _run(env, "--install")
    assert (code, out["status"], out["metrics"]["state"]) == (3, "stopped", "uninstallable")
    assert calls == []


def test_install_then_rechecks(env):
    """--install で 1 度だけ入れ、入った gws で確かめ直す（多くは未認証）。"""
    _put(env, "npm")
    code, out, calls = _run(env, "--install")
    assert _npm_installs(calls) == [["install", "-g", "@googleworkspace/cli"]]
    assert (code, out["metrics"]["state"]) == (11, "unauthenticated")
    assert _gws_calls(calls) == [["auth", "status"]]


def test_install_when_gws_present_does_nothing(env):
    _put(env, "gws")
    _put(env, "npm")
    code, out, calls = _run(env, "--install", FAKE_GWS_STATUS=_status(credential_source="keyring"))
    assert (code, out["metrics"]["state"]) == (0, "authenticated")
    assert _npm_installs(calls) == []


def test_install_failure_stops_with_1(env):
    _put(env, "npm")
    code, out, calls = _run(env, "--install", FAKE_NPM_RC="243")
    assert (code, out["status"]) == (1, "stopped")
    assert "243" in out["summary"]
    assert "EACCES" in out["items"][0]["detail"]
    assert _npm_installs(calls) == [["install", "-g", "@googleworkspace/cli"]]
    assert _gws_calls(calls) == []


def test_installed_but_not_on_path_stops_with_1(env):
    _put(env, "npm")
    code, out, _ = _run(env, "--install", FAKE_NPM_PUTS_GWS="0")
    assert (code, out["status"]) == (1, "stopped")
    assert "/fake/npm-global/bin" in out["next"]


@pytest.mark.parametrize(
    ("stdout", "rc"),
    [
        ("not json", "0"),
        (_status(), "1"),
        (json.dumps({"auth_method": "none", "client_config_exists": True}), "0"),
        (json.dumps(["credential_source"]), "0"),
    ],
)
def test_unreadable_status_stops_with_2(env, stdout, rc):
    """読めない出力を未認証に倒さず、stopped と 2 で返す（I3）。"""
    _put(env, "gws")
    code, out, _ = _run(env, FAKE_GWS_STATUS=stdout, FAKE_GWS_RC=rc)
    assert (code, out["status"]) == (2, "stopped")
    assert "state" not in out["metrics"]


@pytest.mark.parametrize("args", [[], ["--install"]])
@pytest.mark.parametrize("credential_source", ["none", "oauth"])
def test_never_calls_gws_other_than_auth_status(env, args, credential_source):
    """どの引数と状態でも、gws の副命令は auth status だけ（I2）。"""
    _put(env, "gws")
    _put(env, "npm")
    _run(env, *args, FAKE_GWS_STATUS=_status(credential_source=credential_source))
    calls = [json.loads(x) for x in env["calls"].read_text(encoding="utf-8").splitlines()]
    assert _gws_calls(calls) == [["auth", "status"]]


def test_does_not_copy_other_keys(env):
    """auth status の 3 キー以外の値を結果へ写さない（I4）。"""
    _put(env, "gws")
    code, out, _ = _run(env, FAKE_GWS_STATUS=_status(credential_source="oauth", access_token=SECRET))
    assert code == 0
    assert SECRET not in json.dumps(out)
    assert set(out["metrics"]) == {"state", "credential_source", "auth_method", "client_config_exists"}


def test_unknown_argument_is_rejected(env):
    e = {**env["env"]}
    cp = subprocess.run([PY, str(SCRIPT), "--yes"], capture_output=True, text=True, env=e, check=False)
    assert cp.returncode == 2
    assert os.path.getsize(env["calls"]) == 0
