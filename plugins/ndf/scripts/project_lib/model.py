"""`.ndf/project.json` の形（決定 5）。JSON Schema はこのモデルから `project-decl.py schema` が生成する。

項目のキーはどれも、値の形か `Unknown`（`{"unknown": "<理由>"}`）のどちらか一方を取る（I4）。キーが無いのは未解析、
`Unknown` は解析したが決まらなかったことを表す。`pydantic` を使うので、読む側は `deps.require("schema")` を先に呼ぶ。
"""

from __future__ import annotations

import json
from typing import Literal, Optional, Union

from pydantic import Field

import schema

SCHEMA_URL = (
    "https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/development-workflow/schemas/project.schema.json"
)


class Unknown(schema.Shape):
    """測れなかった・決められなかった項目。ai-plugins の値も既定の値も入れない。"""

    unknown: str = Field(min_length=1)


class Language(schema.Shape):
    name: str
    version: Optional[str] = None
    frameworks: list[str] = []
    files: int = Field(ge=0)


class Container(schema.Shape):
    """suite を走らせるコンテナ。書いた suite は、テストを走らせる前にそのサービスのコンテナが worktree を見ているかを
    確かめ（`lib/container_reach.py`）、テスト環境の値を足して走らせる。見ていなければ走らせない。
    `compose_files` はテスト環境の割り当てが `COMPOSE_FILE` を持たないときだけ使う。"""

    service: str
    compose_files: list[str] = []


class Suite(schema.Shape):
    """テストの 1 まとまり。`scope_command` の `{paths}`（空白で区切った 1 語）に範囲のパスが入る。
    `junit` はコマンドが JUnit XML を書く作業ディレクトリからの相対パス（無ければ落ちたテストの見分けは走らせ直しへ落ちる）。"""

    name: str
    runner: str
    command: str
    scope_command: Optional[str] = None
    junit: Optional[str] = None
    container: Optional[Container] = None
    needs: list[str] = []
    paths: list[str] = []


class TestCi(schema.Shape):
    """全体テストを CI に任せるときに見る先。`check` は待つチェックの名前（空なら `ci.required_checks` のすべて）、
    `junit_artifacts` は JUnit を持つ成果物の名前の glob（空なら解析と同じ名前の規則）。"""

    check: Optional[str] = None
    junit_artifacts: Optional[str] = None


class Test(schema.Shape):
    """テストの戦略（#1334）。`strategy` が空なら所要と suite から導く（`test_strategy.propose`）。"""

    strategy: Optional[Literal["local-full", "local-scoped-ci-whole", "round-only"]] = None
    ci: Optional[TestCi] = None
    suites: list[Suite]


class Duration(schema.Shape):
    seconds: float = Field(ge=0)
    source: Literal["ndf-record", "ci-junit", "ci-steps"]
    detail: str


class TestDuration(schema.Shape):
    measured: list[Duration] = Field(min_length=1)


class Workflow(schema.Shape):
    path: str
    jobs: int = Field(ge=0)
    wall_seconds: Optional[float] = None


class Ci(schema.Shape):
    provider: str
    workflows: list[Workflow]
    required_checks: list[str] = []


class Services(schema.Shape):
    container: bool
    compose_files: list[str] = []
    databases: list[str] = []


class Delivery(schema.Shape):
    target: str
    kind: Literal["auto", "manual"]
    trigger: str
    branch: Optional[str] = None
    versioned: bool
    production: Optional[bool] = None  # true: 本番系へ届く / false: 届かない（検証の環境）/ 無し: 決めない（#1454）


class Issues(schema.Shape):
    primary: str
    others: list[str] = []


class Tool(schema.Shape):
    name: str
    config: str


class Checks(schema.Shape):
    tools: list[Tool]


class NdfPolicies(schema.Shape):
    """ai-plugins の方針の検査を掛けるか。キーが無ければどちらも掛けない（決定 8）。"""

    doc_lint: bool
    reject_md_wording_tests: bool


class Import(schema.Shape):
    from_: str = Field(alias="from")
    to: str

    model_config = {**schema.Shape.model_config, "populate_by_name": True}


class Instructions(schema.Shape):
    files: list[str]
    imports: list[Import] = []
    notes: list[str] = []


class BranchState(schema.Shape):
    head: Optional[str] = None
    present: list[str] = []


class Analysis(schema.Shape):
    analyzer: int = Field(ge=1)
    at: str
    inputs: dict[str, str]
    branches: BranchState
    written: dict[str, str] = {}


class Branches(schema.Shape):
    """P6 の答えの形。宣言では worktree.json の base_branch・production_branch へ書く。"""

    base: Optional[str] = None
    production: Optional[str] = None


class ProjectDecl(schema.Shape):
    """プロジェクトの宣言（`.ndf/project.json`）。"""

    schema_: Optional[str] = Field(default=None, alias="$schema")
    version: Literal[1]
    languages: Optional[Union[list[Language], Unknown]] = None
    test: Optional[Union[Test, Unknown]] = None
    test_duration: Optional[Union[TestDuration, Unknown]] = None
    ci: Optional[Union[Ci, Unknown]] = None
    services: Optional[Union[Services, Unknown]] = None
    delivery: Optional[Union[list[Delivery], Unknown]] = None
    issues: Optional[Union[Issues, Unknown]] = None
    checks: Optional[Union[Checks, Unknown]] = None
    ndf_policies: Optional[Union[NdfPolicies, Unknown]] = None
    instructions: Optional[Union[Instructions, Unknown]] = None
    analysis: Optional[Analysis] = None

    model_config = {**schema.Shape.model_config, "populate_by_name": True}


ITEM_MODELS = {name: ProjectDecl.model_fields[name].annotation for name in ProjectDecl.model_fields}


def validate_decl(data) -> ProjectDecl:
    """宣言を検証する。誤りは `schema.ShapeError`（箇所つきの 1 行）。"""
    return schema.load_shape(ProjectDecl, data, where="project.json")


def validate_item(key: str, value) -> None:
    """1 項目の値（答え）を、その項目の形で確かめる。`Unknown` は答えの `unknown` で渡すので、ここでは許さない。"""
    if key == "branches":
        schema.load_shape(Branches, value, where=key)
        return
    schema.load_shape(ProjectDecl, {"version": 1, key: value}, where="")
    if isinstance(value, dict) and "unknown" in value:
        raise schema.ShapeError(f"{key}: 不明は答えの unknown で渡す", [(key, "不明は答えの unknown で渡す")])


def json_schema() -> str:
    """公開する JSON Schema の本文（生成物。`project.schema.json` に置く）。"""
    body = ProjectDecl.model_json_schema(by_alias=True)
    body = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_URL, **body}
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"
