"""承認資料の根拠の行の確かめ（`mvv.cite` と `ndf records mvv cite`。#3959 の決定 4・受け入れ条件 7）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import folded
import pytest

from ndf import cli
from ndf.adapters import github, publish
from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.domains.records import cli as records_cli
from ndf.domains.records import mvv
from ndf.domains.records import mvv_material_decide as mm

# 承認済みの MVV（バージョン 11）に実在する項目
ITEMS = ("Mission", "Vision", "Value 1", "Value 3", "Value 5", "P1", "C1")
APPROVED = mvv.Snapshot("approved", 11, "s" * 64, None, ITEMS, {}, "c0ffee", "p" * 64, None)
BASIS = "## 目指す姿の根拠\n\n"
OK_LINE = "- 根拠: Value 3（MVV バージョン 11）\n"

# 手順書（approval-request.md）の形の承認資料: 「目指す姿の根拠」の、決定の見出しの下に根拠の行
MATERIAL = f"""# 設計承認の資料

## MVV 判定

- 判定: 従う

{BASIS}### 決定 1: 題

{OK_LINE}
### 決定 2: 題

- 根拠: Value 5 / P1（MVV バージョン 11）
"""

# 設計文書 2 本の資料。文書 B は `{purpose}` を「目的」の下に、`{basis}` を最後に置く
TWO_DOCS = f"""# 文書 A（#100）

{BASIS}### 決定 1: 題

{OK_LINE}
# 文書 B（#200）

## 目的

{{purpose}}
{{basis}}"""
B_BASIS = f"{BASIS}### 決定 1: 題\n"
# まとまりの見出しより前（PR 全体の欄）に置いた、決定と根拠の行を持つ「目指す姿の根拠」
PREAMBLE = f"{BASIS}### 決定 1: 題\n\n{OK_LINE}\n"
NO_SECOND = MATERIAL.replace("- 根拠: Value 5 / P1（MVV バージョン 11）\n", "")

# (資料, require_basis, 誤りの (種類, 行), 確かめた根拠の行の数)
CASES = {
    "version-1": ("# 資料\n\n### 決定 1: 題\n\n- 根拠: Value 3（MVV バージョン 1）\n", False, [("version", 5)], 1),
    "procedure-form": (MATERIAL, True, [], 2),
    "current-items": ("".join(f"- 根拠: {i}（MVV バージョン 11）\n" for i in ("Mission", "Value 1", "C1")), False, [], 3),
    "format": ("### 決定 1: 題\n\n- 根拠: Value 3\n", False, [("format", 3)], 1),
    "fenced-and-quoted": ("```\n- 根拠: Value 9（MVV バージョン 1）\n```\n\n例は「根拠: Value 9（MVV バージョン 1）」と書く。\n", False, [], 0),
    "outside-decisions-without-mvv": ("- 根拠: Value 3\n\n### 決定 1: 題\n\n- 根拠: Value 9（MVV バージョン 11）\n", False, [("unknown", 5)], 1),
    "basis-removed": (MATERIAL.replace(BASIS, ""), True, [("heading", 1)], 2),
    "basis-no-space": (MATERIAL.replace(BASIS, "##目指す姿の根拠\n\n"), True, [("heading", 1)], 2),
    "basis-bold": (MATERIAL.replace(BASIS, "**目指す姿の根拠**\n\n"), True, [("heading", 1)], 2),
    "basis-level-3": (MATERIAL.replace(BASIS, "### 目指す姿の根拠\n\n"), True, [("heading", 1)], 2),
    "basis-removed-release": (MATERIAL.replace(BASIS, ""), False, [], 2),
    "decision-without-basis": (NO_SECOND, True, [("heading", 13)], 1),
    "decision-without-basis-release": (NO_SECOND, False, [], 1),
    "two-docs-closed-by-the-next": (TWO_DOCS.format(purpose="", basis=B_BASIS), True, [("heading", 16)], 1),
    "two-docs-purpose-not-read": (TWO_DOCS.format(purpose="- 根拠: Value 3\n", basis=B_BASIS + "\n" + OK_LINE), True, [], 2),
    "two-docs-one-missing": (TWO_DOCS.format(purpose="", basis=""), True, [("heading", 9)], 1),
    "two-docs-preamble-not-counted": (PREAMBLE + TWO_DOCS.format(purpose="", basis=""), True, [("heading", 15)], 2),
    "procedure-form-without-version": (f"{BASIS}### 決定 1: 題\n\n- 根拠: Value 3\n", False, [("format", 5)], 1),
    "plain-list-not-read": ("## 決めたこと\n\n- 根拠: Value 3\n", False, [], 0),
    "under-another-heading": (f"{BASIS}### 決定 1: 題\n\n{OK_LINE}\n### 補足\n\n- 根拠: Value 3\n", False, [("heading", 9)], 1),
    "before-the-first-decision": (f"{BASIS}- 根拠: Value 3\n\n### 決定 1: 題\n\n{OK_LINE}", False, [("heading", 3)], 1),
    "no-decision-heading": (f"{BASIS}- 根拠: Value 3\n", False, [("heading", 1), ("heading", 3)], 0),
}  # fmt: skip


def _write(where: Path, name: str, text: str) -> Path:
    path = where / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("case", list(CASES))
def _cite_reads_basis_lines_and_headings_of_a_material(tmp_path: Path, case: str) -> None:
    text, require_basis, want, checked = CASES[case]
    got = mvv.cite(APPROVED, [_write(tmp_path, "a.md", text)], require_basis=require_basis)
    assert [(e["kind"], e["line"]) for e in got.errors] == want and got.checked == checked
    # 複数のファイルは順に確かめ、誤りはファイルごとのパスを持つ
    two = mvv.cite(APPROVED, [_write(tmp_path, "b.md", MATERIAL), tmp_path / "a.md"], require_basis=require_basis)
    assert [(e["path"], e["line"]) for e in two.errors] == [(str(tmp_path / "a.md"), line) for _, line in want]


@pytest.mark.parametrize("state", ["absent", "unapproved"])
def _without_an_approved_mvv_nothing_is_checked_but_files_are_read(tmp_path: Path, state: str) -> None:
    snap = mvv.Snapshot(state, None, None, None, (), {}, "c0ffee", "", None)
    doc = _write(tmp_path, "a.md", "- 根拠: Value 9（MVV バージョン 1）\n")
    assert mvv.cite(snap, [doc], require_basis=True) == mvv.Cited([], 0)
    with pytest.raises(OSError):
        mvv.cite(snap, [doc, tmp_path / "無い.md"])
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        mvv.cite(snap, [tmp_path / "bad.md"])


# --- ndf records mvv cite ------------------------------------------------------------------------

# 判定するリポジトリ（作り物）と、ほかのリポジトリ（作り物）
TARGET = "acme-fake/target"
PUB = "pub-owner/pub-repo"
PRIV = "priv-owner/priv-repo"
SECRET = "zq7xk9vw3jm2p8tt"
HOST = "git.corp-fake.internal"


class Graph:
    """公開範囲の GraphQL の照会を記録して返す偽物の GitHub。`visibilities` は本物の関数（100 件ごとに 1 回の照会）を使う。"""

    visibilities = github.GitHub.visibilities

    def __init__(self, visibility: dict[str, str]) -> None:
        self.repo: str = TARGET
        self.fail: Optional[Exception] = None
        self.visibility = {k.lower(): v for k, v in visibility.items()}
        self.queries: list[list[str]] = []

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        found = re.findall(r'(a[0-9]+): repository\(owner: "([^"]*)", name: "([^"]*)"\)', query)
        self.queries.append([f"{o}/{n}" for _, o, n in found])
        if self.fail is not None:
            raise self.fail
        return {a: {"visibility": self.visibility.get(f"{o}/{n}".lower(), "PRIVATE")} for a, o, n in found}


class Cite:
    """`_mvv_cite` を同じプロセスで呼ぶ。ベースの取得と `current` は偽物で、Git を起動しない。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.snap = tmp_path, APPROVED
        self.graph = Graph({TARGET: "PRIVATE", PUB: "PUBLIC"})
        self.fetch_error: Optional[Exception] = None
        self.current_error: Optional[Exception] = None
        self.guarded = 0
        monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
        monkeypatch.setenv("NDF_TEST_TOKEN", SECRET)
        monkeypatch.setattr(publish.getpass, "getuser", lambda: "fakeacct-x")
        monkeypatch.setattr(Git, "remote", lambda g: "origin")
        monkeypatch.setattr(Git, "fetch_branch_or_raise", lambda g, remote, name: self._fetch())
        monkeypatch.setattr(mvv, "current", self._current)
        monkeypatch.setattr(records_cli, "_hub", lambda root: self.graph)
        real = publish.Publisher.guarded

        def guarded(publisher: publish.Publisher, *texts: str, assume_public: bool = False) -> Any:
            self.guarded += 1
            return real(publisher, *texts, assume_public=assume_public)

        monkeypatch.setattr(publish.Publisher, "guarded", guarded)

    def _fetch(self) -> None:
        if self.fetch_error is not None:
            raise self.fetch_error

    def _current(self, root: Path, ref: str, allow_mismatch: bool = False, outside: Optional[Path] = None) -> mvv.Snapshot:
        assert ref == "refs/remotes/origin/main" and not allow_mismatch
        if self.current_error is not None:
            raise self.current_error
        return self.snap

    def run(self, *files: Path | str, gate: str = "design") -> dict[str, Any]:
        args = records_cli._parser().parse_args(["mvv", "cite", "--gate", gate, "--base", "main", *map(str, files)])
        out = args.handler(args, self.root)
        assert result.errors(out, result.code_for(out)) == []
        return out

    def material(self, *basis: str, name: str = "m.md") -> Path:
        lines = "".join(f"- 根拠: {b}（MVV バージョン 11）\n" for b in basis)
        return _write(self.root, name, f"{BASIS}### 決定 1: 題\n\n{lines}")

    def fields(self, *basis: str, seen: str = "PRIVATE") -> list[tuple[str, ...]]:
        """対象の公開範囲を `seen` にして打ち、誤りごとの（伏せた後の cited・unknown の要素）。"""
        self.graph.visibility[TARGET.lower()] = seen
        out = self.run(self.material(*basis))
        return [(i["cited"], *i["unknown"]) for i in out["items"]]


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Cite:
    return Cite(tmp_path, monkeypatch)


def _shown(out: dict[str, Any]) -> str:
    return json.dumps({k: out.get(k) for k in ("items", "summary", "warnings")}, ensure_ascii=False)


def _the_result_carries_the_version_or_the_errors(run: Cite) -> None:
    out = run.run(_write(run.root, "a.md", MATERIAL))
    assert out["status"] == "ok" and out["items"] == [] and result.code_for(out) == 0
    assert out["summary"] == "根拠の行の誤り 0 件（MVV バージョン 11・1 ファイル）"
    assert out["metrics"] == {"mvv_version": 11, "commit": "c0ffee", "checked": 2}
    assert run.guarded == 0 and run.graph.queries == []
    out = run.run(run.material("Value 3", "Value 9"))
    path = str(run.root / "m.md")
    assert out["cause"]["kind"] == "refused" and result.code_for(out) == 1 and out["metrics"]["checked"] == 2
    assert out["items"] == [{"path": path, "line": 6, "cited": "Value 9", "kind": "unknown", "version": 11, "unknown": ["Value 9"]}]
    assert out["summary"] == f"根拠の行の誤り 1 件（最初: {path}:6 Value 9）"
    # 承認済みの MVV が無ければ、ファイルを読めたときだけ確かめずに ok
    run.snap = mvv.Snapshot("absent", None, None, None, (), {}, "c0ffee", "", None)
    out = run.run(run.material("Value 9（MVV バージョン 1）"))
    assert out["status"] == "ok" and out["summary"] == "承認済みの MVV が無いので確かめていない"
    assert not {"mvv_version", "commit", "checked"} & set(out["metrics"])
    assert run.run(run.root / "無い.md")["cause"]["kind"] == "precondition"


# 単独の完全名の書き方と、伏せた後の形（空なら伏せ字だけ）。後ろの区切りは残し、`.git` は名前と一緒に伏せる
BARE = [
    (TARGET, ""), (TARGET.upper(), ""), (f"Fix this in {TARGET}.", "Fix this in {}."), (f"{TARGET}、", "{}、"),
    (f"({TARGET})", "({})"), (f"{TARGET}/", "{}/"), (f"{TARGET}.git", "{}"),
]  # fmt: skip
NOT_BARE = [f"{TARGET}-2", f"{TARGET}.js", f"{TARGET}/sub", "other-owner/target"]


@pytest.mark.parametrize("seen", ["PRIVATE", "PUBLIC"])
def _references_to_the_target_itself_are_masked_unless_it_is_public(run: Cite, seen: str) -> None:
    hidden = seen == "PRIVATE"
    refs = (f"{TARGET}#12", f"https://github.com/{TARGET}/pull/34")
    assert run.fields(*refs, seen=seen) == ([(publish.PRIVATE_REF_TEXT,) * 2] * 2 if hidden else [(r, r) for r in refs])
    assert run.graph.queries == [[TARGET]]
    run.graph.queries.clear()
    first, second = run.fields(" / ".join(t for t, _ in BARE), " / ".join(NOT_BARE), seen=seen)
    # unknown の要素の数は変わらない
    assert list(first[1:]) == [(s.format(publish.PRIVATE_REF_TEXT) or publish.PRIVATE_REF_TEXT) if hidden else t for t, s in BARE]
    assert list(second[1:]) == NOT_BARE and run.graph.queries == [[TARGET]]
    # PR のコメントの経路（assume_public を渡さない）は、書き先自身への参照を照会せずに残す
    graph = Graph({TARGET: seen})
    assert publish.Publisher(graph, frozenset()).guarded(f"{TARGET}#12 を見た")[0] == [f"{TARGET}#12 を見た"]
    assert graph.queries == [[TARGET]]


@pytest.mark.parametrize(
    "basis",
    [
        (f"Value 9 / X{SECRET}",),
        ("/home/fakeacct-x/doc と someone@example.com と fakeacct-x",),
        (f"ssh://{SECRET}@github.com/{PUB}",),
        (f"https://git@github.com/{PUB}",),
        (f"https://x-access-token:{SECRET}@github.com/{PRIV}.git", f"https://***@github.com/{PRIV}"),
    ],
    ids=["secret", "home-mail-account", "ssh-secret-user", "https-git-user", "credentials"],
)
def _free_text_is_masked_like_a_public_target_without_a_lookup(run: Cite, basis: tuple[str, ...]) -> None:
    out = run.run(run.material(*basis, name=f"{SECRET}.md"))
    shown = _shown(out)
    assert out["cause"]["kind"] == "refused" and run.guarded == 1 and run.graph.queries == []
    for word in (SECRET, "/home/fakeacct-x", "someone@example.com", "fakeacct-x", "pub-owner", "priv-owner"):
        assert word not in shown
    assert [len(i["unknown"]) for i in out["items"]] == [len(re.split(" / |・", b)) for b in basis]


def _lookups_go_100_references_at_a_time_and_the_fixed_ssh_user_is_looked_up(run: Cite) -> None:
    run.run(run.material(" ".join(f"other-{n}/repo#1" for n in range(101))))
    assert [len(q) for q in run.graph.queries] == [100, 1]
    run.graph.queries.clear()
    (fields,) = run.fields(f"ssh://git@github.com/{PUB}.git と ssh://git@github.com/{PRIV}.git", seen="PUBLIC")
    assert sorted(run.graph.queries[0]) == [PRIV, PUB]
    assert PUB in fields[0] and PRIV not in fields[0] and publish.PRIVATE_REF_TEXT in fields[0]


@pytest.mark.parametrize("cited", [f"https://github.com/{SECRET}/{SECRET}", f"https://github.com/{PRIV}/issues/1{SECRET}"])
def _secrets_in_or_across_references_reach_neither_the_lookup_nor_the_result(run: Cite, cited: str) -> None:
    run.graph.visibility[TARGET.lower()] = "PUBLIC"
    out = run.run(run.material(cited))
    asked, shown = json.dumps(run.graph.queries), _shown(out)
    assert all(SECRET[i : i + 4] not in asked + shown for i in range(len(SECRET) - 3))


def _a_failed_lookup_withholds_every_field_and_stays_refused(run: Cite) -> None:
    run.graph.fail = github.GitHubError(f"https://x-access-token:{SECRET}@github.com/other-owner/x at {HOST}")
    out = run.run(run.material(f"{PRIV}#1 / Value 9"))
    item = out["items"][0]
    assert out["cause"]["kind"] == "refused"
    assert item["cited"] == item["path"] == publish.WITHHELD and item["unknown"] == [publish.WITHHELD] * 2
    (warning,) = out["warnings"]
    assert warning.startswith(
        "根拠の行の誤りの自由記述を伏せた（参照先の公開範囲を照会できない）: " + mm.STOP_REASONS["cite_lookup_failed"]
    )
    assert SECRET not in warning and HOST not in warning and "other-owner" not in warning


def _headings_follow_the_gate_and_the_gate_is_required(run: Cite, capsys: pytest.CaptureFixture[str], last_result) -> None:
    out = run.run(_write(run.root, "a.md", f"{BASIS}- 根拠: Value 3\n"))
    assert out["cause"]["kind"] == "refused" and {i["kind"] for i in out["items"]} == {"heading"}
    bare = _write(run.root, "b.md", "# 資料\n\n例は「根拠: Value 9（MVV バージョン 1）」と書く。\n")
    assert [i["kind"] for i in run.run(bare)["items"]] == ["heading"]
    assert run.run(bare, gate="release")["status"] == "ok"
    code = cli.main(["records", "mvv", "cite", "--base", "main", str(bare)])
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid" and code != 0


def _a_base_or_file_that_cannot_be_read_is_a_precondition_without_the_cause(run: Cite) -> None:
    run.fetch_error = GitError(f"origin の main をマージできない: https://x-access-token:{SECRET}@github.com/other-owner/x {HOST}")
    out = run.run(run.material("Value 9"))
    assert out["cause"]["kind"] == "precondition" and run.graph.queries == []
    assert re.fullmatch(re.escape(mm.STOP_REASONS["cite_precondition"]) + r"（診断の記録 [0-9a-f]{16}）", out["summary"])
    assert all(word not in _shown(out) for word in (SECRET, HOST, "other-owner"))
    rows = mvv.record_path("mvv-gate-failures").read_text(encoding="utf-8").splitlines()
    assert HOST in json.loads(rows[-1])["cause"]
    # 本文のパスは作り物（本物の MVV の文書を読まない）
    run.fetch_error, run.current_error = None, mvv.MvvError(mvv.mismatch_detail({"a": "1", "本文.md": "2"}))
    assert run.run(run.material("Value 3"))["cause"]["kind"] == "precondition"
    run.current_error = None
    assert run.run(run.root / "無い.md")["cause"]["kind"] == "precondition"


# #3959 で足した場合を 1 つのテストの関数へ束ねる（テストの関数の数の予算。束ね方は tests/support/folded.py）
@pytest.mark.parametrize(
    "case",
    folded.cases(
        _cite_reads_basis_lines_and_headings_of_a_material,
        _without_an_approved_mvv_nothing_is_checked_but_files_are_read,
        _the_result_carries_the_version_or_the_errors,
        _references_to_the_target_itself_are_masked_unless_it_is_public,
        _free_text_is_masked_like_a_public_target_without_a_lookup,
        _lookups_go_100_references_at_a_time_and_the_fixed_ssh_user_is_looked_up,
        _secrets_in_or_across_references_reach_neither_the_lookup_nor_the_result,
        _a_failed_lookup_withholds_every_field_and_stays_refused,
        _headings_follow_the_gate_and_the_gate_is_required,
        _a_base_or_file_that_cannot_be_read_is_a_precondition_without_the_cause,
    ),
)
def test_mvv_cite_cases(case: Any, request: pytest.FixtureRequest) -> None:
    folded.run(case, request)
