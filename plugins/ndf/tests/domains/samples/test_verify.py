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
    return verify.compare(data, verify.definition_target(doc, POINTER), verify.Known(absent, [], []))


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
    got = _compare({}, document(), list(properties(document(), "issue")))
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
        # 最後のキーが消えて `properties` が空になっても、自由な辞書とみなさない
        (
            lambda d: properties(d, "label").clear(),
            ABSENT,
            [
                "labels/*: 記述から消えたキー archived_at",
                "labels/0: 定義に無いキー 1 件",
                "labels/1: 定義に無いキー 1 件",
                "meta/*: 定義に無いキー 1 件",
            ],
        ),
        # 見本が通らない枝に同じ名前のキーが残っていても、通る枝から消えれば食い違い
        (
            lambda d: (
                properties(d, "issue")["user"]["anyOf"].append(
                    {"type": "object", "required": ["company"], "properties": {"starred_at": {"type": "string"}}}
                ),
                properties(d, "simple-user").pop("starred_at"),
            ),
            ABSENT,
            ["creator: 定義に無いキー 1 件", "user: 記述から消えたキー starred_at"],
        ),
        # 見本で null の位置は、どの枝のキーも在るとみなす
        (lambda d: None, [*ABSENT, "assignee/starred_at"], []),
        (lambda d: properties(d, "issue").pop("body"), ABSENT, ["(最上位): 定義に無いキー 1 件"]),
        (lambda d: properties(d, "issue")["body"].update(type="string"), ABSENT, ["body: type"]),
        (
            lambda d: d["components"]["schemas"].pop("label"),
            ABSENT,
            ["記述に参照の先が無い: #/components/schemas/label"],
        ),
        (
            lambda d: properties(d, "issue").update(number={"$ref": f"https://example.test/x.json?sig={SECRET_VALUE}"}),
            ABSENT,
            ["記述に参照の先が無い: (文書の外)"],
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
    assert verify.safe_path(path, verify.definition_target(document(), POINTER)) == expected


@pytest.mark.parametrize(
    "wrap",
    [
        lambda ref: {"unevaluatedProperties": False, "$ref": ref},
        lambda ref: {"unevaluatedProperties": False, "$dynamicRef": ref},
        lambda ref: {"$schema": Draft202012Validator.META_SCHEMA["$id"], "$ref": ref},
        lambda ref: {"$schema": "http://json-schema.org/draft-07/schema#", "allOf": [{"$ref": ref}]},
    ],
    ids=["unevaluated-ref", "unevaluated-dynamic-ref", "nested-schema", "other-dialect"],
)
def test_compare_resolves_references_only_inside_the_document(wrap: Callable[[str], Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """jsonschema が registry で参照を解く経路でも、文書の中の参照は解き、外の URL は取りに行かず、値を出さずに食い違いにする。"""
    sent: list[Any] = []
    monkeypatch.setattr("urllib.request.urlopen", lambda req, *a, **kw: sent.append(req))
    doc = {**document(), "x-free": {"type": "object", "additionalProperties": True}}
    got = {}
    outside = [
        f"https://user:{SECRET_VALUE}@example.test/x.json",
        f"//user:{SECRET_VALUE}@example.test/x.json",
        f"/x?sig={SECRET_VALUE}",
        f"{Draft202012Validator.META_SCHEMA['$id']}#/{SECRET_VALUE}",
    ]
    for ref in ("#/x-free", "#/x-gone", *outside):
        properties(doc, "issue")["meta"] = wrap(ref)
        got[ref] = _compare(sample(), doc, ABSENT)
    hidden = [["記述に参照の先が無い: (文書の外)"]] * len(outside)
    assert list(got.values()) == [[], ["記述に参照の先が無い: #/x-gone"], *hidden]
    assert sent == []


@pytest.mark.parametrize("pointer", ["#/paths/~1issues/get/schema", "#/x-shapes/0"])
def test_compare_resolves_references_from_the_document_root(pointer: str) -> None:
    doc = document()
    shape = {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"login": {"type": "string"}, "id": {"type": "integer"}}}]}
    doc.update({"paths": {"/issues": {"get": {"schema": shape}}}, "x-shapes": [shape]})
    properties(doc, "issue")["user"] = {"$ref": pointer}
    absent = [key for key in ABSENT if key != "user/starred_at"]
    assert _compare(sample(), doc, absent) == []
    assert _compare({**sample(), "user": {"login": 1}}, doc, absent) == ["user: anyOf"]


@pytest.mark.parametrize(
    ("changes", "known", "expected"),
    [
        ({"number": "x"}, ["number: type"], []),
        ({"number": "x", "body": 1}, ["number: type"], ["body: type"]),
        ({}, ["number: type"], ["既知の食い違いが消えた: number: type"]),
    ],
)
def test_compare_excludes_only_the_known_mismatches(changes: dict[str, Any], known: list[str], expected: list[str]) -> None:
    target = verify.definition_target(document(), POINTER)
    assert verify.compare({**sample(), **changes}, target, verify.Known(ABSENT, [], known)) == expected


@pytest.mark.parametrize(
    ("change", "extra", "expected"),
    [
        (lambda d: None, ["labels/*/name"], ["既知の、定義に無いキーが消えた: 1 件"]),
        (lambda d: properties(d, "label").pop("name"), ["labels/*/name"], ["meta/*: 定義に無いキー 1 件"]),
        (lambda d: properties(d, "label").pop("name"), ["labels/*/name", "meta/any/name"], []),
        # 定義に無いキーの件数が同じでも、挙げたキーと入れ替われば食い違い
        (
            lambda d: properties(d, "simple-user").pop("id"),
            ["user/id", "creator/login"],
            ["creator: 定義に無いキー 1 件", "既知の、定義に無いキーが消えた: 1 件"],
        ),
    ],
)
def test_compare_excludes_only_the_listed_extra_keys(change: Change, extra: list[str], expected: list[str]) -> None:
    doc = document()
    change(doc)
    assert verify.compare(sample(), verify.definition_target(doc, POINTER), verify.Known(ABSENT, extra, [])) == expected


@pytest.mark.parametrize("word", ["oneOf", "anyOf"])
@pytest.mark.parametrize("turn", [1, -1])
def test_compare_narrows_to_the_branch_of_the_sample_type(word: str, turn: int) -> None:
    """枝の順に依らず、見本の型を受けるただ 1 つの枝で突き合わせ、食い違いを枝の中の位置で出す。"""
    doc = document()
    branches = [{"type": "string"}, {"$ref": "#/components/schemas/label"}]
    doc["components"]["schemas"]["issue"] = {"description": "x", word: branches[::turn]}
    assert _compare({"name": "bug"}, doc, ["archived_at"]) == []
    assert _compare({"name": 1}, doc, ["archived_at"]) == ["name: type"]


@pytest.mark.parametrize(
    ("openapi", "pointer", "error"),
    [
        ("3.0.3", POINTER, verify.VersionError),
        (3.1, POINTER, verify.VersionError),
        ("3.1.0", "#/components/schemas/no-such", verify.RefError),
        ("3.1.0", "#/components/no-such/issue", verify.RefError),
    ],
)
def test_definition_target_refuses(openapi: Any, pointer: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        verify.definition_target({**document(), "openapi": openapi}, pointer)


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
        world.define(f"s{i}", url=f"{URL}/{i}")
        world.documents[f"{URL}/{i}"] = document()
    if ok:
        assert verify.verify(peer, world.fetch).outcomes == []
        return
    with pytest.raises(verify.VerifyError) as caught:
        verify.verify(peer, world.fetch)
    assert caught.value.kind == "precondition"
    assert world.fetched == []


@pytest.mark.parametrize(
    "change",
    [
        {"peer": "Git Hub"},
        {"sample": "tests/samples/../x.json"},
        {"extra": 1},
        {"url": f"http://x.test/{SECRET_VALUE}"},
        {"url": f"https://user:{SECRET_VALUE}@x.test/d.json"},
        {"url": f"https://x.test/d.json?sig={SECRET_VALUE}"},
        {"url": f"https://x.test/d.json#{SECRET_VALUE}"},
    ],
)
def test_verify_refuses_a_definition_of_wrong_shape_without_its_values(world: World, change: dict[str, Any]) -> None:
    world.define(data=False, **change)
    with pytest.raises(verify.DefinitionError) as caught:
        verify.verify(fetch=world.fetch)
    assert SECRET_VALUE not in str(caught.value)
    assert world.fetched == []


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
