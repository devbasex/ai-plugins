"""`ndf samples verify` の結果 JSON と終了コード（#3687）。記述は偽の取得で渡し、ネットワークへ出ない。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from contract_files import copy_contract_files

from ndf import cli
from ndf.adapters import http
from ndf.domains.samples import verify

from ._world import SECRET_KEY, SECRET_VALUE, URL, World, document, sample


@pytest.fixture
def world(world: World) -> World:
    """突き合わせが読むファイルに、結果 JSON と終了コードを決めるのに読むファイルを足す。"""
    copy_contract_files(
        world.root / "contracts",
        [
            *(f"causes/{kind}.json" for kind in ("config_broken", "infra_wait", "precondition", "samples_mismatch")),
            "facts/exit-codes.json",
            "facts/fact.schema.json",
            "limits/cause.infra-wait.json",
            "schemas/causes.schema.json",
            # 無いと、結果 JSON を出す 1 か所の確かめ（`tests/conftest.py`）が結果のスキーマを見ずに通る
            "schemas/io/samples.result.schema.json",
            "schemas/result.schema.json",
        ],
    )
    return world


def _unreachable(w: World) -> None:
    w.documents[URL] = http.FetchError("HTTP 503")
    w.define()


def _secrets(w: World) -> None:
    data = sample()
    data.update({"number": SECRET_VALUE, "state_reason": SECRET_VALUE, f"{SECRET_KEY}-B": SECRET_VALUE})
    data["meta"][SECRET_KEY] = {"name": 1, SECRET_KEY: SECRET_VALUE}
    data["tags"][SECRET_KEY] = SECRET_VALUE
    w.define(data=data)


def _old_document(w: World) -> None:
    url = f"https://example.test/{SECRET_VALUE}.json"
    w.documents[url] = {**document(), "openapi": "3.0.3"}
    w.define(url=url)


@pytest.mark.parametrize(
    ("change", "code", "cause", "kinds", "metrics"),
    [
        (lambda w: w.define(), 0, None, [], {"samples": 1, "calls": {"github": 1}}),
        (lambda w: None, 0, None, [], {"samples": 0, "calls": {}}),
        (_secrets, 1, "samples_mismatch", ["mismatch"] * 6, {"samples": 1, "calls": {"github": 1}}),
        (_unreachable, 75, "infra_wait", ["unreachable"], {"samples": 0, "calls": {"github": 1}}),
        (lambda w: w.define(data=False), 1, "samples_mismatch", ["mismatch"], {"samples": 1, "calls": {"github": 0}}),
        (
            lambda w: (_unreachable(w), w.define("a", data=False)),
            1,
            "samples_mismatch",
            ["mismatch", "unreachable"],
            {"samples": 1, "calls": {"github": 1}},
        ),
        (_old_document, 2, "config_broken", [], {}),
        (lambda w: w.define(peer="other"), 3, "precondition", [], {}),
    ],
    ids=["ok", "no-definition", "mismatch", "unreachable", "no-sample", "both", "openapi-3.0", "no-limit"],
)
def test_result_json_and_exit_code(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    last_result: Callable[[str], dict],
    change: Callable[[World], Any],
    code: int,
    cause: str | None,
    kinds: list[str],
    metrics: dict[str, Any],
) -> None:
    change(world)
    real = verify.verify
    monkeypatch.setattr(verify, "verify", lambda peer: real(peer, world.fetch))
    assert cli.main(["samples", "verify"]) == code
    out = capsys.readouterr()
    got = last_result(out.out)
    assert (got["cause"]["kind"] if "cause" in got else None) == cause
    assert [i["kind"] for i in got["items"]] == kinds
    assert got["metrics"] == metrics
    assert SECRET_VALUE not in out.out + out.err and SECRET_KEY not in out.out + out.err
