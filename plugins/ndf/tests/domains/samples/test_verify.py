"""見本と公開の記述の突き合わせ（`ndf.domains.samples.verify`。#3687）。記述は偽の取得で渡し、ネットワークへ出ない。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from ndf import contracts, identity
from ndf.adapters import http
from ndf.contracts import limits
from ndf.domains.samples import verify

from ._world import ABSENT, POINTER, SAMPLE, SECRET_KEY, SECRET_VALUE, URL, World, document, properties, sample

Change = Callable[[Any], Any]
OTHER_URL = "https://example.test/other.json"


def _compare(data: Any, doc: dict[str, Any], absent: list[str]) -> list[str]:
    return verify.compare(data, verify.definition_schema(doc, POINTER), absent)


def test_compare_accepts_the_recorded_shape() -> None:
    assert _compare(sample(), document(), ABSENT) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda s: s.update(number=SECRET_VALUE), ["number: type"]),
        (lambda s: s.update(state_reason=SECRET_VALUE), ["state_reason: enum"]),
        (lambda s: s["labels"][1].update(name=1), ["labels/1/name: type"]),
        (lambda s: s["creator"].update(id=SECRET_VALUE), ["creator/id: type"]),
        (lambda s: s["user"].update(id=SECRET_VALUE), ["user: anyOf"]),
        (
            lambda s: s.pop("number") and s.pop("user"),
            [
                "(最上位): required number, user",
                "(最上位): 記述に足されたキー number",
                "(最上位): 記述に足されたキー user",
            ],
        ),
        (lambda s: s["meta"].update({SECRET_KEY: {"name": 1}}), ["meta/*/name: type"]),
        (lambda s: s["meta"].update({SECRET_KEY: {SECRET_KEY: 1}}), ["meta/*: 定義に無いキー 1 件"]),
        (lambda s: s["tags"].update({SECRET_KEY: SECRET_VALUE}), ["tags/*: type"]),
        (lambda s: s["tags"].update({"other": SECRET_VALUE}), []),
        (lambda s: s["free"].update({SECRET_KEY: SECRET_VALUE}), []),
        (lambda s: s.update({SECRET_KEY: 1}), ["(最上位): 定義に無いキー 1 件"]),
        (lambda s: s.update({SECRET_KEY: {"x": 1}, "other": 1}), ["(最上位): 定義に無いキー 2 件"]),
        (lambda s: s["user"].update({SECRET_KEY: 1}), ["user: 定義に無いキー 1 件"]),
        (lambda s: s["creator"].update({SECRET_KEY: 1}), ["creator: 定義に無いキー 1 件"]),
        (lambda s: s["labels"][0].update({SECRET_KEY: 1}), ["labels/0: 定義に無いキー 1 件"]),
        # どの枝にも通らない辞書は検証の誤りが出るので、未知のキーと足されたキーを数えない
        (lambda s: s.update(user={SECRET_KEY: 1}), ["user: anyOf"]),
    ],
)
def test_compare_reports_a_changed_sample_without_its_values_and_keys(change: Change, expected: list[str]) -> None:
    data = sample()
    change(data)
    got = _compare(data, document(), ABSENT)
    assert not any(SECRET_VALUE in line or SECRET_KEY in line for line in got)
    assert got == expected


def test_compare_reports_an_empty_sample() -> None:
    got = verify.compare({}, verify.definition_schema(document(), POINTER), list(properties(document(), "issue")))
    assert got == ["(最上位): required labels, number, user"]


@pytest.mark.parametrize(
    ("change", "absent", "expected"),
    [
        (lambda d: properties(d, "issue").update(fresh={}), ABSENT, ["(最上位): 記述に足されたキー fresh"]),
        (lambda d: properties(d, "issue").update(fresh={}), [*ABSENT, "fresh"], []),
        (
            lambda d: properties(d, "simple-user").update(fresh={}),
            ABSENT,
            ["creator: 記述に足されたキー fresh", "user: 記述に足されたキー fresh"],
        ),
        # 配列の要素が 2 つでも 1 件。定義が名前を決めない位置（meta）の下は見ない
        (lambda d: properties(d, "label").update(fresh={}), ABSENT, ["labels/*: 記述に足されたキー fresh"]),
        (lambda d: properties(d, "issue").pop("body_html"), ABSENT, ["(最上位): 記述から消えたキー body_html"]),
        (
            lambda d: properties(d, "simple-user").pop("starred_at"),
            ABSENT,
            ["creator: 定義に無いキー 1 件", "user: 記述から消えたキー starred_at"],
        ),
        (lambda d: properties(d, "label").pop("archived_at"), ABSENT, ["labels/*: 記述から消えたキー archived_at"]),
        (lambda d: properties(d, "issue").pop("body"), ABSENT, ["(最上位): 定義に無いキー 1 件"]),
        (lambda d: properties(d, "issue")["body"].update(type="string"), ABSENT, ["body: type"]),
        (
            lambda d: d["components"]["schemas"].pop("label"),
            ABSENT,
            ["記述に参照の先が無い: #/components/schemas/label"],
        ),
        (
            lambda d: properties(d, "issue").update(number={"$ref": "https://example.test/x.json"}),
            ABSENT,
            ["記述に参照の先が無い: https://example.test/x.json"],
        ),
        (lambda d: properties(d, "issue").update(number={"type": "no-such-type"}), ABSENT, ["記述の定義がスキーマとして読めない"]),
        (lambda d: properties(d, "issue")["state_reason"].update(pattern="("), ABSENT, ["記述の定義がスキーマとして読めない"]),
    ],
)
def test_compare_reports_a_changed_description(change: Change, absent: list[str], expected: list[str]) -> None:
    doc = document()
    change(doc)
    assert _compare(sample(), doc, absent) == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ([], "(最上位)"),
        (["labels", 0, "name"], "labels/0/name"),
        (["user", "login"], "user/login"),
        (["creator", "note"], "creator/note"),
        (["meta", SECRET_KEY, "name"], "meta/*/name"),
        (["tags", SECRET_KEY], "tags/*"),
        ([SECRET_KEY, SECRET_KEY, 0], "*/*/0"),
        (["free", SECRET_KEY], "free/*"),
        (["labels", "*", "name"], "labels/*/name"),
    ],
)
def test_safe_path_shows_only_names_the_definition_decides(path: list[str | int], expected: str) -> None:
    assert verify.safe_path(path, verify.definition_schema(document(), POINTER)) == expected


def test_definition_schema_is_a_valid_schema() -> None:
    Draft202012Validator.check_schema(verify.definition_schema(document(), POINTER))


@pytest.mark.parametrize(
    ("openapi", "pointer", "error"),
    [
        ("3.0.3", POINTER, verify.VersionError),
        (3.1, POINTER, verify.VersionError),
        ("3.1.0", "#/components/schemas/no-such", verify.RefError),
        ("3.1.0", "#/components/no-such/issue", verify.RefError),
    ],
)
def test_definition_schema_refuses(openapi: Any, pointer: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        verify.definition_schema({**document(), "openapi": openapi}, pointer)


def test_verify_without_definitions_sends_nothing(world: World) -> None:
    assert verify.verify(fetch=world.fetch) == ([], 0, {})
    assert world.fetched == []


def test_verify_fetches_a_shared_url_once_and_selects_the_peer(world: World) -> None:
    world.define("b")
    world.define("a")
    world.define("c", peer="other", url=OTHER_URL)
    assert verify.verify("github", world.fetch) == ([], 2, {"github": 1})
    assert world.fetched == [URL]


@pytest.mark.parametrize(
    ("change", "kind", "detail"),
    [
        (lambda w: w.define(data=False), verify.Kind.MISMATCH, "見本のファイルが無い"),
        (lambda w: w.define(data="{"), verify.Kind.MISMATCH, "見本が JSON として読めない"),
        (lambda w: w.define(pointer="#/components/schemas/gone"), verify.Kind.MISMATCH, "記述に参照の先が無い: #/components/schemas/gone"),
        (lambda w: w.define(data={**sample(), "number": "x"}), verify.Kind.MISMATCH, "number: type"),
        (lambda w: w.documents.update({URL: http.FetchError("HTTP 503")}) or w.define(), verify.Kind.UNREACHABLE, "HTTP 503"),
    ],
)
def test_verify_reports_a_sample(world: World, change: Callable[[World], Any], kind: verify.Kind, detail: str) -> None:
    change(world)
    got = verify.verify(fetch=world.fetch)
    assert got.outcomes == [(SAMPLE, "github", kind, detail)]
    assert got.samples == (1 if kind is verify.Kind.MISMATCH else 0)


def test_verify_keeps_comparing_after_an_unreachable_url(world: World) -> None:
    world.documents[OTHER_URL] = http.FetchError("接続できない（ConnectError）")
    world.define("z", data={**sample(), "number": "x"})
    world.define("a", url=OTHER_URL)
    world.define("b", url=OTHER_URL)
    got = verify.verify(fetch=world.fetch)
    assert [(o.sample.rsplit("/", 1)[1], o.kind.value) for o in got.outcomes] == [
        ("a.json", "unreachable"),
        ("b.json", "unreachable"),
        ("z.json", "mismatch"),
    ]
    assert (got.samples, got.calls, len(world.fetched)) == (1, {"github": 2}, 2)


@pytest.mark.parametrize(
    ("urls", "peer", "policy", "ok"),
    [
        (2, None, {}, True),
        (3, None, {}, False),
        (1, None, {"retries": 1}, True),
        (2, None, {"retries": 1}, False),
        (1, None, {"peers": {"github": 0}}, False),
        (1, None, {"peers": {"other": 9}}, False),
        (0, "no-such-peer", {}, False),
        (0, "github", {}, True),
    ],
)
def test_verify_stops_before_the_calls_exceed_the_limit(
    world: World, urls: int, peer: str | None, policy: dict[str, Any], ok: bool
) -> None:
    path = world.root / "contracts" / limits.NDF_SETTINGS / f"{verify.FETCH_POLICY}.json"
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **policy}), encoding="utf-8")
    contracts.use_dir(world.root / "contracts")
    for i in range(urls):
        world.define(f"s{i}", url=f"{URL}?{i}")
        world.documents[f"{URL}?{i}"] = document()
    if ok:
        assert verify.verify(peer, world.fetch).outcomes == []
        return
    with pytest.raises(verify.VerifyError) as caught:
        verify.verify(peer, world.fetch)
    assert caught.value.kind == "precondition"
    assert world.fetched == []


@pytest.mark.parametrize("change", [{"peer": "Git Hub"}, {"sample": "tests/samples/../x.json"}, {"url": "http://x.test"}, {"extra": 1}])
def test_verify_refuses_a_definition_of_wrong_shape(world: World, change: dict[str, Any]) -> None:
    world.define(data=False, **change)
    with pytest.raises(verify.DefinitionError):
        verify.verify(fetch=world.fetch)


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (httpx.Response(307, headers={"Location": "/new"}), "HTTP 307（転送は追わない）"),
        (httpx.Response(503), "HTTP 503"),
        (httpx.Response(200, content=b"{"), "本文が JSON でない"),
        (httpx.Response(200, content=b"[]"), "本文が JSON のオブジェクトでない"),
        (httpx.Response(200, content=b"x" * 11), "本文が 10 バイトを超えた"),
    ],
    ids=["redirect", "status", "not-json", "not-object", "too-large"],
)
def test_fetch_document_failures_are_unreachable(
    world: World, monkeypatch: pytest.MonkeyPatch, response: httpx.Response, detail: str
) -> None:
    seen: list[str] = []
    real = http.fetch_body
    transport = httpx.MockTransport(lambda req: seen.append(str(req.url)) or response)
    cap = 10 if "バイト" in detail else None

    def fetch_body(url: str, timeout: float, *, max_bytes: int, follow: bool) -> bytes:
        assert (timeout, max_bytes) == (limits.seconds(verify.FETCH_POLICY), limits.policy(verify.FETCH_POLICY).max_bytes)
        return real(url, timeout, transport, max_bytes=cap if cap is not None else max_bytes, follow=follow)

    monkeypatch.setattr(http, "fetch_body", fetch_body)
    world.define()
    got = verify.verify()
    assert got.outcomes == [(SAMPLE, "github", verify.Kind.UNREACHABLE, detail)]
    assert (got.calls, seen) == ({"github": 1}, [URL])


def test_shipped_definitions_point_at_recorded_samples() -> None:
    """配る定義の見本が在り、`absent_keys` のキーを見本が持たない（持てば古い一覧）。"""
    for name, raw in contracts.load_split(verify.DEFINITIONS).items():
        assert contracts.schema_errors(raw, verify.DEFINITION_SCHEMA) == [], name
        assert raw["peer"] in limits.policy(verify.FETCH_POLICY).peers
        data = json.loads((identity.ROOT / raw["sample"]).read_text(encoding="utf-8"))
        for entry in raw["absent_keys"]:
            *parents, key = entry.split("/")
            at = data
            for part in parents:
                at = at[0] if part == verify.ANY else at[part]
            assert key not in at, entry
