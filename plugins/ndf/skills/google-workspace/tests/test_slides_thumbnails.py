"""slides-thumbnails.py の振る舞い（#2065）。

gws は PATH に置いた偽物で置き換える。偽物は呼ばれた引数を FAKE_CALLS の JSON Lines へ積み、
`presentations get` には FAKE_IDS のページの ID を、`getThumbnail` にはページの ID ごとの
`file://` の画像の URL を返す。FAKE_FAIL_ON の ID でだけ失敗し、FAKE_AUTH_FAIL で未認証を返す。
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
SCRIPT = SKILL / "scripts" / "slides-thumbnails.py"
sys.path.insert(0, str(SKILL.parents[1] / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

FAKE_GWS = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as f:
    f.write(json.dumps(a) + "\n")
sys.stderr.write("This is not an officially supported Google product.\n")
if os.environ.get("FAKE_AUTH_FAIL"):
    sys.stderr.write("error[auth]: Access denied. No credentials provided.\n")
    sys.exit(1)
params = json.loads(a[a.index("--params") + 1])
if a[:3] == ["slides", "presentations", "get"]:
    print(json.dumps({"slides": [{"objectId": i} for i in os.environ["FAKE_IDS"].split(",")]}))
    sys.exit(0)
if a[:4] == ["slides", "presentations", "pages", "getThumbnail"]:
    oid = params["pageObjectId"]
    if oid == os.environ.get("FAKE_FAIL_ON"):
        print(json.dumps({"error": {"code": 500, "message": "backend error"}}))
        sys.exit(1)
    img = os.path.join(os.environ["FAKE_IMG_DIR"], oid + ".png")
    with open(img, "wb") as f:
        f.write(b"PNG-" + oid.encode())
    print(json.dumps({"width": 1600, "height": 900, "contentUrl": "file://" + img}))
    sys.exit(0)
sys.exit(3)
"""


@pytest.fixture
def env(tmp_path: Path) -> dict:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    gws = bin_ / "gws"
    gws.write_text(FAKE_GWS.replace("{py}", sys.executable), encoding="utf-8")
    gws.chmod(0o755)
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    e = dict(os.environ)
    e.update(PATH=f"{bin_}{os.pathsep}{e['PATH']}", FAKE_CALLS=str(tmp_path / "calls.jsonl"), FAKE_IMG_DIR=str(imgs), FAKE_IDS="g1,p2_x,abc")
    return e


def _run(env: dict, *args: str):
    cp = subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    assert validate_result(out, cp.returncode) == []
    calls = [json.loads(line) for line in Path(env["FAKE_CALLS"]).read_text(encoding="utf-8").splitlines()] if Path(env["FAKE_CALLS"]).exists() else []
    return cp.returncode, out, calls


def test_saves_every_page_in_order(env: dict, tmp_path: Path) -> None:
    out_dir = tmp_path / "thumbs"
    code, out, calls = _run(env, "PRES", str(out_dir))
    assert code == 0
    assert out["metrics"]["pages"] == 3
    assert [(i["name"], i["objectId"]) for i in out["items"]] == [("p1", "g1"), ("p2", "p2_x"), ("p3", "abc")]
    for no, oid in enumerate(["g1", "p2_x", "abc"], start=1):
        assert (out_dir / f"p{no}.png").read_bytes() == b"PNG-" + oid.encode()
    thumbs = [json.loads(c[c.index("--params") + 1]) for c in calls if "getThumbnail" in c]
    assert [t["pageObjectId"] for t in thumbs] == ["g1", "p2_x", "abc"]
    assert {t["thumbnailProperties.thumbnailSize"] for t in thumbs} == {"LARGE"}
    assert all(t["presentationId"] == "PRES" for t in thumbs)


def test_gws_failure_stops_with_1_and_shows_output(env: dict, tmp_path: Path) -> None:
    env["FAKE_FAIL_ON"] = "p2_x"
    code, out, _ = _run(env, "PRES", str(tmp_path / "thumbs"))
    assert code == 1
    assert out["status"] == "stopped"
    assert "backend error" in out["items"][0]["detail"]
    assert not (tmp_path / "thumbs" / "p2.png").exists()


def test_unauthenticated_returns_11(env: dict, tmp_path: Path) -> None:
    env["FAKE_AUTH_FAIL"] = "1"
    code, out, _ = _run(env, "PRES", str(tmp_path / "thumbs"))
    assert code == 11
    assert out["status"] == "gate"
    assert out["metrics"]["state"] == "unauthenticated"


def test_bad_arguments_return_2(env: dict) -> None:
    code, out, calls = _run(env, "PRES")
    assert code == 2
    assert calls == []
