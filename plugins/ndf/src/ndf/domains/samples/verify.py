"""テストの見本を、相手が公開する記述（OpenAPI 3.1 の bundled 形式）と突き合わせる（#3687）。

- 見本の定義は `contracts/live-samples/` に 1 件 1 ファイルで置く
- 記述は URL ごとに 1 回だけ取り、同じ URL を指す見本で使い回す。打ち直さず、転送も追わない（失敗は次の日のジョブに任せる）
- 呼び出しの上限は上限の設定 `samples.fetch` の `peers`（相手ごと）が持つ。最悪の要求の数が上限を超えれば 1 回も呼ばない
- 説明に出すのは、記述か見本の定義が名前を決めた語（`properties` のキー・検証の規則の名前・参照）と配列の添字と件数だけである。
  見本の値と、見本の側の辞書のキーの名前は出さない（見本は本物の応答で、値とキーに利用者の識別情報が入りうる）
- 参照（`$ref`）は文書の中（`#/...`）だけで解き、外の URL を取りに行かない
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Sequence
from enum import StrEnum
from typing import Any, NamedTuple
from urllib.parse import unquote

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import UnknownType, ValidationError

from ndf import contracts
from ndf.adapters import http
from ndf.contracts import limits
from ndf.core import json_pointer
from ndf.identity import ROOT

DEFINITIONS = "live-samples"
DEFINITION_SCHEMA = contracts.schema_path("live-sample")
FETCH_POLICY = "samples.fetch"
# パスで、定義が名前を決めない辞書のキーと、`absent_keys` の配列の要素を表す語
ANY = "*"
TOP = "(最上位)"
# 受ける記述のバージョンの頭（3.0 は null を取る列を nullable で書き、2020-12 の検証器では受けられない）
OPENAPI_PREFIX = "3.1."

# 見本の中の位置（辞書のキーと配列の添字の並び）
_Place = tuple[str | int, ...]


class Kind(StrEnum):
    """見本ごとの結果の種類（結果のスキーマの kind）。"""

    MISMATCH = "mismatch"
    UNREACHABLE = "unreachable"


class VersionError(Exception):
    """記述が OpenAPI 3.1 でない。"""


class RefError(Exception):
    """文書の中の参照の先が無い（か、文書の外を指す）。`str(e)` が参照。"""


class Layers(NamedTuple):
    """1 つの位置に効く定義の並び。`matched` は、`anyOf`・`oneOf` のどれかの枝が残ったか（枝を持たない定義は真）。"""

    nodes: list[dict[str, Any]]
    matched: bool


class Position(NamedTuple):
    """見本の中の辞書 1 つ。`dynamic` は、定義が名前を決めない辞書のキーの下にあるか。"""

    path: _Place
    value: dict[str, Any]
    layers: Layers
    dynamic: bool


class VerifyError(Exception):
    """見本を当てずに止める。`kind` は停止理由の種類。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


class DefinitionError(contracts.ContractError):
    """見本の定義の形が違う。"""


class Outcome(NamedTuple):
    """見本 1 件の食い違い 1 つ、または本物を取れなかったこと。"""

    sample: str
    peer: str
    kind: Kind
    detail: str


class VerifyResult(NamedTuple):
    """結果の並び（見本のパスの昇順）・当てた見本の数・相手ごとに実際に送った要求の数。"""

    outcomes: list[Outcome]
    samples: int
    calls: dict[str, int]


class Definition(NamedTuple):
    """見本の定義 1 つ。"""

    sample: str
    peer: str
    url: str
    pointer: str
    absent: list[str]


def fetch_document(url: str) -> dict[str, Any]:
    """公開の記述の文書の全体を 1 回の要求で取る。取れない・JSON のオブジェクトでなければ `http.FetchError`。"""
    max_bytes = int(limits.value(FETCH_POLICY, "max_bytes"))
    body = http.fetch_body(url, limits.seconds(FETCH_POLICY), max_bytes=max_bytes, follow=False)
    try:
        document = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise http.FetchError("本文が JSON でない") from exc
    if not isinstance(document, dict):
        raise http.FetchError("本文が JSON のオブジェクトでない")
    return document


class _Documents:
    """URL ごとに 1 回だけ取った記述。`calls` は相手ごとに送った要求の数（取得の失敗も 1 回）。"""

    def __init__(self, fetch: Callable[[str], Any], peers: set[str]) -> None:
        self._fetch = fetch
        self._got: dict[str, Any] = {}
        self.calls = dict.fromkeys(sorted(peers), 0)

    def get(self, definition: Definition) -> Any:
        """定義の記述。取れなければ `http.FetchError`（同じ URL の 2 度目からも同じ失敗を返す）。"""
        if definition.url not in self._got:
            self.calls[definition.peer] += 1
            self._got[definition.url] = self._once(definition.url)
        got = self._got[definition.url]
        if isinstance(got, http.FetchError):
            raise got
        return got

    def _once(self, url: str) -> Any:
        try:
            return self._fetch(url)
        except http.FetchError as exc:
            return exc


def verify(peer: str | None = None, fetch: Callable[[str], Any] = fetch_document) -> VerifyResult:
    """定義した見本（`peer` を渡せばその相手だけ）を本物の記述と突き合わせる。

    呼び出しの上限を超える・上限の無い相手は、1 回も呼ばずに `VerifyError`（precondition）。記述が OpenAPI 3.1 でなければ
    `VerifyError`（config_broken）。1 つの URL が取れなくても、ほかの URL の見本は当て続ける。
    """
    definitions = [d for d in _definitions() if peer is None or d.peer == peer]
    _check_calls(definitions, peer)
    documents = _Documents(fetch, {d.peer for d in definitions})
    outcomes: list[Outcome] = []
    for definition in definitions:
        outcomes += _outcomes(definition, documents)
    missed = {o.sample for o in outcomes if o.kind is Kind.UNREACHABLE}
    return VerifyResult(outcomes, sum(1 for d in definitions if d.sample not in missed), documents.calls)


def _definitions() -> list[Definition]:
    """見本の定義を見本のパスの昇順で返す。形が違えば `DefinitionError`。"""
    out = []
    for name, raw in contracts.load_split(DEFINITIONS).items():
        contracts.require_schema(raw, DEFINITION_SCHEMA, DefinitionError, f"見本の定義 {DEFINITIONS}/{name}.json")
        source = raw["source"]
        out.append(Definition(raw["sample"], raw["peer"], source["schema"], source["pointer"], raw["absent_keys"]))
    return sorted(out)


def _check_calls(definitions: list[Definition], peer: str | None) -> None:
    """相手ごとの最悪の要求の数が上限に収まるか。収まらない・上限の無い相手なら `VerifyError`（precondition）。"""
    policy = limits.policy(FETCH_POLICY)
    if policy.peers is None:
        raise limits.LimitsError(f"上限の設定の方針 {FETCH_POLICY} に peers が無い")
    retries = int(limits.value(FETCH_POLICY, "retries"))
    peers = {d.peer for d in definitions} | ({peer} if peer is not None else set())
    over = []
    for name in sorted(peers):
        worst = len({d.url for d in definitions if d.peer == name}) * (1 + retries)
        cap = policy.peers[name] if name in policy.peers else None
        if cap is None or worst > cap:
            over.append(f"{name} は最悪 {worst} 回（上限 {'なし' if cap is None else cap}）")
    if over:
        raise VerifyError("precondition", f"記述の取得の要求の数が上限の設定 {FETCH_POLICY} に収まらない: {'、'.join(over)}")


def _outcomes(definition: Definition, documents: _Documents) -> list[Outcome]:
    """見本 1 件の結果。記述を取れなければ `unreachable` の 1 件、食い違いは 1 つを 1 件にする。"""
    try:
        kind, details = Kind.MISMATCH, _mismatches(definition, documents)
    except http.FetchError as exc:
        kind, details = Kind.UNREACHABLE, [str(exc)]
    return [Outcome(definition.sample, definition.peer, kind, detail) for detail in details]


def _mismatches(definition: Definition, documents: _Documents) -> list[str]:
    path = ROOT / definition.sample
    if not path.is_file():
        return ["見本のファイルが無い"]
    try:
        sample = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return ["見本が JSON として読めない"]
    try:
        schema = definition_schema(documents.get(definition), definition.pointer)
    except VersionError as exc:
        raise VerifyError("config_broken", f"{exc}: {definition.url}") from exc
    except RefError as exc:
        return [ref_detail(exc)]
    return compare(sample, schema, definition.absent)


def definition_schema(document: dict[str, Any], pointer: str) -> dict[str, Any]:
    """文書の全体を根に持ったまま、`pointer` の定義を指す検証用のスキーマを返す。

    3.1 でない文書は `VersionError`、ポインタの先が無ければ `RefError`。
    """
    version = document["openapi"] if "openapi" in document else None
    if not isinstance(version, str) or not version.startswith(OPENAPI_PREFIX):
        raise VersionError(f"記述が OpenAPI {OPENAPI_PREFIX}x でない")
    _target(document, pointer)
    return {"$schema": Draft202012Validator.META_SCHEMA["$id"], "components": document["components"], "$ref": pointer}


def compare(sample: Any, schema: dict[str, Any], absent: list[str]) -> list[str]:
    """見本と定義の食い違いの説明を昇順で返す。空なら合う。`absent` は見本の定義の `absent_keys`。"""
    try:
        found = {describe(error, schema) for error in _validator(schema).iter_errors(sample)}
        return sorted(found.union(unknown_keys(sample, schema), added_keys(sample, schema, absent)))
    except RefError as exc:
        return [ref_detail(exc)]
    except (UnknownType, re.error):
        return ["記述の定義がスキーマとして読めない"]


def ref_detail(exc: RefError) -> str:
    return f"記述に参照の先が無い: {exc}"


def describe(error: ValidationError, schema: dict[str, Any]) -> str:
    """検証の誤り 1 件を `<パス>: <検証の規則>` で返す（`required` は足りないキーの名前を添える）。

    `error.message` は見本の値を含みうるので使わない。`error.instance` は、足りないキー（スキーマの語）の有無だけを見る。
    """
    rule = str(error.validator)
    if rule == "required":
        rule += " " + ", ".join(sorted(k for k in error.validator_value if k not in error.instance))
    return f"{safe_path(list(error.absolute_path), schema)}: {rule}"


def unknown_keys(sample: Any, schema: dict[str, Any]) -> list[str]:
    """見本の辞書のキーのうち、定義の `properties` に無いものの件数を位置ごとに返す。

    `additionalProperties`・`patternProperties` を書く定義と、`properties` を持たない定義（自由な辞書）は数えない。
    """
    out = []
    for at in _positions(sample, schema):
        if any(_is_open(node) for node in at.layers.nodes) or not at.layers.matched:
            continue
        names = _names(at.layers.nodes)
        count = sum(1 for key in at.value if key not in names)
        if names and count:
            out.append(f"{safe_path(at.path, schema)}: 定義に無いキー {count} 件")
    return sorted(out)


def added_keys(sample: Any, schema: dict[str, Any], absent: list[str]) -> list[str]:
    """記述の定義のキーのうち基準の形（見本のキーと `absent`）に無いものと、`absent` のうち記述から消えたものを返す。"""
    known = set(absent)
    found = set()
    for at in _positions(sample, schema):
        if at.dynamic or not at.layers.matched:
            continue
        base = tuple(ANY if isinstance(part, int) else part for part in at.path)
        new = {key for key in _names(at.layers.nodes) - set(at.value) if "/".join((*base, key)) not in known}
        found |= {f"{safe_path(base, schema)}: 記述に足されたキー {key}" for key in new}
    return sorted(found | {line for entry in absent for line in _gone(entry, schema)})


def safe_path(path: Sequence[str | int], schema: dict[str, Any]) -> str:
    """見本の中の位置を `/` 区切りにする（最上位は `(最上位)`）。定義の `properties` に無い辞書のキーは `*` に伏せる。"""
    labels = []
    nodes = [schema]
    for part in path:
        layers = _layers(nodes, schema, _every).nodes
        label = str(part) if isinstance(part, int) or part in _names(layers) else ANY
        labels.append(label)
        nodes = [child for node in layers for child in _children(node, part)]
    return "/".join(labels) if labels else TOP


def _gone(entry: str, schema: dict[str, Any]) -> list[str]:
    """`absent_keys` の 1 つが、記述の定義の `properties` に無ければその説明。"""
    *parents, key = entry.split("/")
    nodes = [schema]
    for part in parents:
        nodes = [child for node in _layers(nodes, schema, _every).nodes for child in _children(node, part)]
    if key in _names(_layers(nodes, schema, _every).nodes):
        return []
    return [f"{safe_path(parents, schema)}: 記述から消えたキー {key}"]


def _positions(sample: Any, schema: dict[str, Any]) -> Iterator[Position]:
    """見本の辞書を、その位置に効く定義とともに順に返す（定義の無い位置の下へは進まない）。"""
    validator = _validator(schema)
    queue: list[tuple[_Place, Any, list[Any], bool]] = [((), sample, [schema], False)]
    for path, value, nodes, dynamic in queue:
        layers = _layers(nodes, schema, lambda branch, value=value: validator.evolve(schema=branch).is_valid(value))
        if isinstance(value, dict):
            yield Position(path, value, layers, dynamic)
        names = _names(layers.nodes)
        for part, inner in _entries(value):
            below = [child for node in layers.nodes for child in _children(node, part)]
            if below:
                queue.append(((*path, part), inner, below, dynamic or (isinstance(part, str) and part not in names)))


def _entries(value: Any) -> list[tuple[str | int, Any]]:
    if isinstance(value, dict):
        return list(value.items())
    return list(enumerate(value)) if isinstance(value, list) else []


def _every(branch: Any) -> bool:
    return True


def _layers(nodes: list[Any], root: dict[str, Any], keep: Callable[[Any], bool]) -> Layers:
    """`nodes` と、`$ref` の先・`allOf` の枝・`anyOf` と `oneOf` の枝のうち `keep` が真のもの。"""
    queue = list(nodes)
    out: list[dict[str, Any]] = []
    matched = True
    seen: set[int] = set()
    for node in queue:
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        queue += [_target(root, node["$ref"])] if "$ref" in node else []
        queue += node["allOf"] if "allOf" in node else []
        for word in [w for w in ("anyOf", "oneOf") if w in node]:
            kept = [branch for branch in node[word] if keep(branch)]
            matched = matched and bool(kept)
            queue += kept
    return Layers(out, matched)


def _names(nodes: list[dict[str, Any]]) -> set[str]:
    return {key for node in nodes if "properties" in node for key in node["properties"]}


def _is_open(node: dict[str, Any]) -> bool:
    return "additionalProperties" in node or "patternProperties" in node


def _children(node: dict[str, Any], part: str | int) -> list[Any]:
    """定義 1 つの、`part`（辞書のキー・配列の添字・`*`）の先の定義。"""
    if isinstance(part, int):
        return _items(node, part)
    if "properties" in node and part in node["properties"]:
        return [node["properties"][part]]
    named = node["patternProperties"] if "patternProperties" in node else {}
    found = [child for pattern, child in named.items() if part == ANY or re.search(pattern, part)]
    if "additionalProperties" in node and (part == ANY or not found):
        found.append(node["additionalProperties"])
    return found + (_items(node, None) if part == ANY else [])


def _items(node: dict[str, Any], index: int | None) -> list[Any]:
    """配列の要素の定義。`index` が None なら、どの要素の定義も。"""
    prefix = node["prefixItems"] if "prefixItems" in node else []
    rest = [node["items"]] if "items" in node else []
    if index is None:
        return [*prefix, *rest]
    return [prefix[index]] if index < len(prefix) else rest


def _target(root: dict[str, Any], ref: Any) -> Any:
    """文書の中の参照の先。文書の外を指す・先が無ければ `RefError`。"""
    local = isinstance(ref, str) and ref.startswith("#/")
    found, value = json_pointer.resolve(root, unquote(ref[1:])) if local else (False, None)
    if not found:
        raise RefError(str(ref))
    return value


def _validator(schema: dict[str, Any]) -> Any:
    """`schema` の検証器。参照は `_target` だけで解く。"""

    def follow(validator: Any, ref: Any, instance: Any, _: Any) -> Iterator[ValidationError]:
        yield from validator.descend(instance, _target(schema, ref))

    return validators.extend(Draft202012Validator, {"$ref": follow, "$dynamicRef": follow})(schema)
