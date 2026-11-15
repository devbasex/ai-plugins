"""`ndf gws` のテスト。偽のコマンドの置き場（`Fakes`）と gws の `auth status` の規則（`status_rule`）を 2 つのテストのファイルで使う（fixture は conftest）。

偽のコマンドは呼ばれた引数を `<名前>.log` へ 1 行ずつ書き、`<名前>.json` の規則（引数の先頭が合う最初の行）で応える。
規則の項目: `prefix`（引数の先頭）・`code`・`out`・`err`・`sleep`（秒）・`copy`（元と先。npm の導入で gws を置く）・
`write_after`（この引数の次のパスへ最後の引数を書く。curl の `-o`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ndf import cli
from ndf.contracts import facts, result

FAKE = """#!{python}
import json, pathlib, shutil, sys, time
here = pathlib.Path(__file__).resolve()
args = sys.argv[1:]
with open(here.with_suffix(".log"), "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\\n")
for rule in json.loads(here.with_suffix(".json").read_text(encoding="utf-8")):
    if args[:len(rule["prefix"])] != rule["prefix"]:
        continue
    time.sleep(rule.get("sleep", 0))
    if "copy" in rule:
        shutil.copy(rule["copy"][0], rule["copy"][1])
    if "write_after" in rule:
        pathlib.Path(args[args.index(rule["write_after"]) + 1]).write_bytes(args[-1].encode())
    sys.stdout.write(rule.get("out", ""))
    sys.stderr.write(rule.get("err", ""))
    sys.exit(rule.get("code", 0))
sys.exit(97)
"""


class Fakes:
    """偽のコマンドの置き場（PATH はここだけ）。"""

    def __init__(self, root: Path) -> None:
        self.bin = root / "bin"
        self.staged = root / "staged"
        self.bin.mkdir()
        self.staged.mkdir()

    def add(self, name: str, rules: list[dict[str, Any]], *, staged: bool = False) -> Path:
        """偽のコマンド `name` を置く。`staged` なら PATH の外に置き、規則と記録は PATH の置き場に持つ。"""
        (self.bin / f"{name}.json").write_text(json.dumps(rules), encoding="utf-8")
        exe = (self.staged if staged else self.bin) / name
        exe.write_text(FAKE.format(python=sys.executable), encoding="utf-8")
        exe.chmod(0o755)
        return exe

    def calls(self, name: str) -> list[list[str]]:
        log = self.bin / f"{name}.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @classmethod
    def on_path(cls, root: Path, monkeypatch: pytest.MonkeyPatch) -> "Fakes":
        """置き場を作り、PATH をそこだけにする。環境変数のトークンは外す。"""
        fakes = cls(root)
        monkeypatch.setenv("PATH", str(fakes.bin))
        monkeypatch.delenv("GOOGLE_WORKSPACE_CLI_TOKEN", raising=False)
        return fakes


def status_rule(source: str, method: str = "none", client: bool = True, **extra: Any) -> dict[str, Any]:
    """偽の gws の `auth status` の規則（3 つの必須キーと `extra` を持つ JSON を返す）。"""
    return {
        "prefix": ["auth", "status"],
        "out": json.dumps({"credential_source": source, "auth_method": method, "client_config_exists": client, **extra}),
    }


def run_gws(capsys: pytest.CaptureFixture[str], last_result, *argv: str) -> tuple[int, dict]:
    """`ndf gws <argv>` を受付から打ち、終了コードと結果 JSON（結果の契約に合う）を返す。"""
    code = cli.main(["gws", *argv])
    got = last_result(capsys.readouterr().out)
    assert result.errors(got, code) == []
    return code, got


def assert_routes(got: dict) -> None:
    """routes のキーが答えの語彙の中にあり、行き先の argv の先頭が ndf。"""
    assert set(got["routes"]) <= set(facts.ids(result.ANSWERS))
    assert all(r is None or r["argv"][0] == "ndf" for r in got["routes"].values())
