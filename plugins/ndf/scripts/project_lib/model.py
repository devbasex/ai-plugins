"""`.ndf/project.json` の形（決定 5）。JSON Schema はこのモデルから `project-decl.py schema` が生成する。

項目のキーはどれも、値の形か `Unknown`（`{"unknown": "<理由>"}`）のどちらか一方を取る（I4）。キーが無いのは未解析、
`Unknown` は解析したが決まらなかったことを表す。`pydantic` を使うので、読む側は `deps.require("schema")` を先に呼ぶ。
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Optional, Union

from pydantic import Field, StrictBool

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
    確かめ（`lib/container_reach.py`）、テスト環境の値を足して走らせる。見ていなければテスト環境を 1 度起こして確かめ直し、
    それでも見ていなければ走らせない。
    `compose_files` はテスト環境の割り当てが `COMPOSE_FILE` を持たないときだけ使う。"""

    service: str
    compose_files: list[str] = []


# 継続的統合のジョブの識別子（I1。#464）。`<ワークフローのファイルのパス>#<job id>` か、そのファイルのジョブすべてを指す
# `<ワークフローのファイルのパス>` の 2 形だけを取る
JobId = Annotated[str, Field(pattern=r"^[^#\s]+\.ya?ml(#[^#\s]+)?$")]


class Suite(schema.Shape):
    """テストの 1 まとまり。`scope_command` の `{paths}`（引用の外の、空白で区切った 1 語）に、シェルの引用で守った範囲の
    パスの並びが入る。入るパスの種類は場所で違う: cross-refactoring の改修の範囲テストではテストのファイルのパス
    （計画の `test_targets` の `::` より前）、全体テストを CI に任せる戦略の着手前のテストでは `--scope` のテストの置き場所
    （ディレクトリかファイル）、静的解析（`kind: lint`）では変更したファイルのパス。テストの `scope_command` はテストの
    ファイルのパスを受けるコマンドで書く（unittest なら `discover -s {paths}` ではなく `python3 -m unittest {paths}`）。
    ファイルを受けない `scope_command` は、cross-refactoring の `init` がテストのファイルを入れた試走で見分け、改修の前に
    止める。`command` と `scope_command` はシェルで走らせる。
    `junit` はコマンドが JUnit XML を書く作業ディレクトリからの相対パス（無ければ落ちたテストの見分けは走らせ直しへ落ちる）。
    `kind` は種別で、`test`（テスト）か `lint`（静的解析。整形の検査を含む）。書かなければ `test`。静的解析の suite の範囲は
    変更したファイルのうち `paths` に当たるもの。`paths` の要素は接頭辞（ディレクトリ）か glob（`*.sh` など）で、空ならすべて。
    `ci_jobs` はこの suite が同じ検査を手元で走らせる継続的統合のジョブの識別子で、cross-refactoring の `init` が
    宣言に無いジョブを知らせるときに見る。"""

    name: str
    runner: str
    command: str
    scope_command: Optional[str] = None
    junit: Optional[str] = None
    container: Optional[Container] = None
    needs: list[str] = []
    paths: list[str] = []
    kind: Literal["test", "lint"] = "test"
    ci_jobs: list[JobId] = []


class TestCi(schema.Shape):
    """全体テストを CI に任せるときに見る先。`check` は待つチェックの名前（空なら `ci.required_checks` のすべて）、
    `junit_artifacts` は JUnit を持つ成果物の名前の glob（空なら解析と同じ名前の規則）。"""

    check: Optional[str] = None
    junit_artifacts: Optional[str] = None


class CiExempt(schema.Shape):
    """手元の検証で走らせないと宣言した継続的統合のジョブ（除外したジョブ）。`reason` は空にしない（I2）。"""

    job: JobId
    reason: str = Field(min_length=1, pattern=r"\S")


class Test(schema.Shape):
    """テストの戦略（#1334）。`strategy` が空なら所要と suite から導く（`test_strategy.propose`）。
    `ci_exempt` は手元の検証で走らせない継続的統合のジョブと、その理由。"""

    strategy: Optional[Literal["local-full", "local-scoped-ci-whole", "round-only"]] = None
    ci: Optional[TestCi] = None
    suites: list[Suite]
    ci_exempt: list[CiExempt] = []


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


class DeliveryResult(schema.Shape):
    """CD の結果を読む手段（結果の読み方）。`workflow` と `command` の両方を書くと、読む側（lib/delivery.py）が読めない宣言として止める。"""

    workflow: Optional[str] = None  # GitHub Actions のワークフローのファイル名
    command: Optional[str] = None  # シェルのコマンド。{revision} を 40 桁のリビジョンへ置き換える。0 成功 / 75 待つ / 1 失敗 / ほか未確定
    timeout_seconds: Optional[Annotated[int, Field(gt=0)]] = None  # 待ちの上限（秒）


class Delivery(schema.Shape):
    target: str
    kind: Literal["auto", "manual"]
    trigger: str
    branch: Optional[str] = None  # channel の無い行だけで読む（ほかのブランチで動く行と、移行の読み）
    channel: Optional[Literal["production", "base"]] = None  # 本番のブランチ / ベースブランチへのマージで動く（名前を写さない。#2336）
    versioned: bool
    production: Optional[StrictBool] = None  # true: 本番系へ届く / false: 届かない（検証の環境）/ 無し: 決めない（#1454）
    manual_reason: Optional[str] = None  # kind: manual の行で読む。あえて CD を置かない理由（#2623）
    result: Optional[DeliveryResult] = None  # kind: auto の行で読む。無ければ CD の結果は未確定（#2623）
    publishes_release: Optional[StrictBool] = (
        None  # kind: auto・versioned: true の行で読む。真ならこの行の CD がタグと GitHub Release を作る（#2623）
    )


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


class Product(schema.Shape):
    kind: str
    users: Optional[str] = None


class UsageScope(schema.Shape):
    """利用の記録を読む範囲。`aggregate_only` が true の行だけを読む（#2315 の決定 9）。"""

    aggregate_only: StrictBool
    days: Optional[int] = None
    mask: list[str] = []


class Usage(schema.Shape):
    name: str
    command: str
    scope: UsageScope


class Market(schema.Shape):
    name: str
    url: Optional[str] = None
    viewpoints: list[str] = []


class Needs(schema.Shape):
    usage: list[Usage] = []
    market: list[Market] = []


class Library(schema.Shape):
    name: str
    ecosystem: Optional[str] = None
    alternatives: list[str] = []
    viewpoints: list[str] = []


class Environment(schema.Shape):
    name: str
    url: Optional[str] = None


class Seeds(schema.Shape):
    libraries: list[Library] = []
    environment: list[Environment] = []


class CauseType(schema.Shape):
    id: str
    name: str
    defect: bool = True


class CodeMeasure(schema.Shape):
    name: str
    command: str
    types: list[str] = []


class Materials(schema.Shape):
    """材料の宣言（#2315）。ニーズとシーズの調査・後回しのマイルストーン・原因の型・大整理の裏付けの計測。"""

    product: Optional[Product] = None
    needs: Optional[Needs] = None
    seeds: Optional[Seeds] = None
    deferred_milestones: list[str] = []
    cause_types: list[CauseType] = []
    code_measures: list[CodeMeasure] = []
    failures: list[str] = []


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
    materials: Optional[Union[Materials, Unknown]] = None
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
