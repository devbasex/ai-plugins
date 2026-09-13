"""設計 Pull Request の本文の決めたことの節と、設計文書の決定の見出しの突き合わせ（#545）。

`gh` を差し替え、Pull Request・変更したファイル・ファイルの中身を固定の値で返す。
差し替えた `gh` は受け取った引数を 1 行ずつ記録するため、**読んでいないこと・書いて
いないこと**も確かめられる。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import urllib.parse

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "pr-body-decisions.sh"
REPO = "owner/repo"
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
MARKER = "<!-- 設計文書の「決定の記録」の見出しから pr-body-decisions.sh sync が作る。手で書き換えない -->"

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys, urllib.parse
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args, ensure_ascii=False) + "\n")
if not args or args[0] != "api":
    sys.exit(1)
method, endpoint, input_file = "GET", None, None
i = 1
while i < len(args):
    a = args[i]
    if a in ("-X", "--method"):
        method = args[i + 1]; i += 2; continue
    if a in ("-H", "--header"):
        i += 2; continue
    if a == "--input":
        input_file = args[i + 1]; i += 2; continue
    if a.startswith("-"):
        i += 1; continue
    endpoint = a; i += 1
path, _, query = endpoint.partition("?")
parts = path.split("/")
if method == "PATCH":
    if state.get("fail_patch"):
        sys.exit(1)
    sent = json.load(open(input_file, encoding="utf-8"))
    with open(os.environ["FAKE_GH_PATCHED"], "w", encoding="utf-8", newline="") as f:
        f.write(sent["body"])
    if state.get("patch_effective", True):
        state["pr"]["body"] = sent["body"]
    state["reads"] = state.get("reads", 0)
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False)
    sys.stdout.write(json.dumps(state["pr"]))
    sys.exit(0)
if len(parts) == 5 and parts[3] == "pulls":
    state["reads"] = state.get("reads", 0) + 1
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False)
    if state.get("fail_pr") or (state.get("fail_reread") and state["reads"] >= 2):
        sys.stderr.write("HTTP 404\n")
        sys.exit(1)
    sys.stdout.write(json.dumps(state["pr"]))
    sys.exit(0)
if len(parts) == 6 and parts[5] == "files":
    if "files_response" in state:
        sys.stdout.write(state["files_response"])
        sys.exit(0)
    files = state["files"]
    # 2 ページに分けて返し、--paginate の連結した出力を読めることを確かめる
    half = len(files) // 2
    sys.stdout.write(json.dumps(files[:half]) + json.dumps(files[half:]))
    sys.exit(0)
if len(parts) >= 5 and parts[3] == "contents":
    name = urllib.parse.unquote("/".join(parts[4:]))
    if name not in state["contents"]:
        sys.exit(1)
    sys.stdout.write(state["contents"][name])
    sys.exit(0)
sys.exit(1)
'''


class Fake:
    def __init__(self, tmp_path: pathlib.Path):
        self.dir = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH, encoding="utf-8")
        (self.bin / "gh").chmod(0o755)
        self.state = tmp_path / "state.json"
        self.log = tmp_path / "calls.jsonl"
        self.patched = tmp_path / "patched.md"

    def setup(self, *, body, head="design/issue-1-x", files=None, contents=None, **flags):
        files = files or {}
        state = {
            "pr": {"number": 7, "body": body, "head": {"ref": head, "sha": HEAD_SHA}},
            "files": [{"filename": name, "status": status} for name, status in files.items()],
            "contents": contents or {},
            **flags,
        }
        self.state.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def run(self, *args, path=None):
        env = {
            **os.environ,
            "PATH": path or f"{self.bin}:{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_PATCHED": str(self.patched),
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def patched_body(self):
        return self.patched.read_bytes().decode("utf-8") if self.patched.exists() else None


@pytest.fixture()
def fake(tmp_path):
    return Fake(tmp_path)


DESIGN = (
    "# 設計\n\n## 決定の記録\n\n"
    "### 決定 1: 実行してよいコマンドは起動の引数で受け取る\n\n理由。\n\n"
    "### 決定 2: 実行の結果を担当の支持より先に見る\n\n理由。\n\n"
    "## 配置\n\n### 配置の小見出しは数えない\n"
)
OLD_DESIGN = DESIGN.replace("実行してよいコマンドは起動の引数で受け取る", "実行してよいコマンドをリポジトリが宣言する")


def section(*blocks):
    lines = ["## 決めたこと", "", MARKER, ""]
    for path, headings in blocks:
        lines += [f"`{path}`", ""] + [f"- {h}" for h in headings] + [""]
    return "\n".join(lines).rstrip("\n") + "\n"


EXPECTED = section((
    "issues/issue-1-design.md",
    ["決定 1: 実行してよいコマンドは起動の引数で受け取る", "決定 2: 実行の結果を担当の支持より先に見る"],
))
OLD_SECTION = EXPECTED.replace("実行してよいコマンドは起動の引数で受け取る", "実行してよいコマンドをリポジトリが宣言する")


def design_pr(fake, body, design=DESIGN, **flags):
    fake.setup(
        body=body,
        files={"issues/issue-1-design.md": "added", "issues/issue-1-requirements.md": "added"},
        contents={"issues/issue-1-design.md": design, "issues/issue-1-requirements.md": "# 要求\n\n本文\n"},
        **flags,
    )


def writes(fake):
    return [c for c in fake.calls() if "PATCH" in c]


# --- 突き合わせ（check） -------------------------------------------------------


def test_1_matching_section_returns_0(fake):
    design_pr(fake, f"## Summary\n\n- 要約\n\n{EXPECTED}\n## Test plan\n\n- [ ] 何か\n")
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr


def test_2_changed_heading_returns_1_and_shows_both_lines(fake):
    """#539 の形: 設計文書の決定の見出しが変わったのに、本文は古い見出しのまま。"""
    design_pr(fake, f"## Summary\n\n{OLD_SECTION}\n## Test plan\n")
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "実行してよいコマンドは起動の引数で受け取る" in out.stdout
    assert "実行してよいコマンドをリポジトリが宣言する" in out.stdout


def test_3_missing_section_returns_1(fake):
    design_pr(fake, "## Summary\n\n- 要約\n\n## Test plan\n")
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 1
    assert "決定 1: 実行してよいコマンドは起動の引数で受け取る" in out.stdout


def test_4_section_without_design_document_returns_1(fake):
    fake.setup(
        body=f"## Summary\n\n{EXPECTED}\n## Test plan\n",
        files={"README.md": "modified"},
        contents={"README.md": "# 説明\n\n## 使い方\n\n### 手順\n"},
    )
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 1, out.stdout + out.stderr


def test_4_no_section_and_no_design_document_returns_0(fake):
    """期待する節が「無し」で、本文にも無ければ一致である。"""
    fake.setup(body="## Summary\n", files={"README.md": "modified"}, contents={"README.md": "# 説明\n"})
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr


def test_4_empty_decisions_without_subheadings_is_treated_as_zero_decisions(fake):
    """現状固定: 「## 決定の記録」があっても「### 」見出しが無ければ決定 0 件として扱う。"""
    empty_design = "# 設計\n\n## 決定の記録\n\n決定事項はまだありません。\n\n## その他\n"
    # 本文に「## 決めたこと」節がない状態で check を実行し、決定 0 件として一致（終了コード 0）
    design_pr(fake, body="## Summary\n\n- 要約\n\n## Test plan\n", design=empty_design)
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "設計文書 0 本 / 決定 0 件" in out.stdout

    # 本文に「## 決めたこと」節がある状態で check を実行し、決定がないのに節が存在する食い違い（終了コード 1）
    design_pr(fake, body=f"## Summary\n\n{EXPECTED}\n## Test plan\n", design=empty_design)
    out_with_section = fake.run("check", "7", "--repo", REPO)
    assert out_with_section.returncode == 1, out_with_section.stdout + out_with_section.stderr
    assert "食い違い" in out_with_section.stdout


def test_5_headings_inside_code_fences_are_not_counted(fake):
    template = (
        "# 雛形\n\n```markdown\n## 決定の記録\n\n### 決定 1: {結論を 1 文で}\n```\n\n"
        "~~~~\n## 決定の記録\n### 決定 9: チルダの囲み\n~~~~\n"
    )
    body = "## Summary\n\n```markdown\n## 決めたこと\n\n- 例\n```\n\n## Test plan\n"
    fake.setup(body=body, files={"skills/decisions.md": "modified"}, contents={"skills/decisions.md": template})
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr


def test_5_fence_inside_decisions_does_not_end_or_add_headings(fake):
    design = DESIGN.replace("理由。\n\n### 決定 2", "```text\n## 例\n### 例の見出し\n```\n\n### 決定 2")
    design_pr(fake, f"{EXPECTED}", design=design)
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr


def test_5_long_fence_closes_only_with_same_marker_and_sufficient_length(fake):
    """現状固定: 長い fence は異なる記号や短い同記号では閉じない。"""
    design = (
        "# 設計\n\n## 決定の記録\n\n"
        "### 決定 1: fence 外の決定\n\n"
        "``````markdown\n"
        "```\n"
        "~~~~~~\n"
        "### 決定 9: fence 内の見出し\n"
        "``````\n\n"
        "### 決定 2: 閉じ fence 後の決定\n"
    )
    expected = section((
        "issues/issue-1-design.md",
        ["決定 1: fence 外の決定", "決定 2: 閉じ fence 後の決定"],
    ))
    design_pr(fake, expected, design=design)

    out = fake.run("check", "7", "--repo", REPO)

    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.parametrize("flag", ["fail_pr"])
def test_6_unreadable_pull_request_returns_2(fake, flag):
    design_pr(fake, EXPECTED, **{flag: True})
    for sub in ("check", "sync"):
        out = fake.run(sub, "7", "--repo", REPO)
        assert out.returncode == 2, (sub, out.stdout, out.stderr)


def test_6_unreadable_design_document_returns_2(fake):
    fake.setup(body=EXPECTED, files={"issues/issue-1-design.md": "added"}, contents={})
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 2, out.stdout + out.stderr


@pytest.mark.parametrize(
    "files_response",
    ["{", json.dumps([{"status": "added"}])],
    ids=["broken-json", "missing-filename"],
)
def test_6_unreadable_changed_files_returns_2(fake, files_response):
    """現状固定: 一覧 API が成功しても応答を解釈できなければ読み取り失敗にする。"""
    fake.setup(body=EXPECTED, files_response=files_response)
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "変更したファイルの一覧を読めません" in out.stderr


def test_6_missing_gh_returns_2(fake, tmp_path):
    """`gh` だけを隠す。PATH を空にすると `bash` と `python3` も消えるため、symlink を張る。"""
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    for name in ("bash", "python3", "dirname", "git"):
        found = shutil.which(name)
        if found:
            (isolated / name).symlink_to(found)
    design_pr(fake, EXPECTED)
    out = fake.run("check", "7", "--repo", REPO, path=str(isolated))
    assert out.returncode == 2, out.stdout + out.stderr


def test_7_documents_are_read_at_the_head_commit(fake):
    design_pr(fake, EXPECTED)
    fake.run("check", "7", "--repo", REPO)
    contents = [c for c in fake.calls() if any("/contents/" in a for a in c)]
    assert contents, fake.calls()
    for call in contents:
        endpoint = next(a for a in call if "/contents/" in a)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(endpoint).query)
        assert query["ref"] == [HEAD_SHA]


def test_7_removed_and_non_markdown_files_are_not_read(fake):
    fake.setup(
        body=EXPECTED,
        files={"issues/issue-1-design.md": "added", "old.md": "removed", "script.sh": "added"},
        contents={"issues/issue-1-design.md": DESIGN},
    )
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    read = [a for c in fake.calls() for a in c if "/contents/" in a]
    assert all("old.md" not in a and "script.sh" not in a for a in read)


def test_8_non_design_pull_request_is_out_of_scope(fake):
    design_pr(fake, "## Summary\n", head="feature/issue-1-x")
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0
    assert len(out.stdout.strip().splitlines()) == 1
    assert "対象外" in out.stdout
    assert not [c for c in fake.calls() if any("/files" in a or "/contents/" in a for a in c)]


def test_multiple_documents_are_listed_in_path_order(fake):
    contracts = "# 契約\n\n## 決定の記録\n\n### 決定 6: 契約の決定\n"
    fake.setup(
        body="",
        files={"issues/issue-1-design.md": "added", "issues/issue-1-contracts.md": "added"},
        contents={"issues/issue-1-design.md": DESIGN, "issues/issue-1-contracts.md": contracts},
    )
    expected = section(
        ("issues/issue-1-contracts.md", ["決定 6: 契約の決定"]),
        ("issues/issue-1-design.md", [
            "決定 1: 実行してよいコマンドは起動の引数で受け取る",
            "決定 2: 実行の結果を担当の支持より先に見る",
        ]),
    )
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    assert fake.patched_body().strip() == expected.strip()


# --- 書き直し（sync） ----------------------------------------------------------


def test_9_only_the_section_changes_with_crlf(fake):
    before = "## Summary\r\n\r\n- 要約  \r\n\r\n"
    after = "## Test plan\r\n\r\n- [ ] 何か\r\n\r\n<!-- I want to review in Japanese. -->\r\n"
    old = OLD_SECTION.replace("\n", "\r\n") + "\r\n"
    design_pr(fake, before + old + after)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    written = fake.patched_body()
    assert written.startswith(before)
    assert written.endswith(after)
    assert "実行してよいコマンドは起動の引数で受け取る" in written[len(before):-len(after)]


def test_9_section_is_removed_when_no_design_document(fake):
    before = "## Summary\n\n- 要約\n\n"
    after = "## Test plan\n"
    fake.setup(body=before + EXPECTED + "\n" + after, files={"README.md": "modified"}, contents={"README.md": "# x\n"})
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    assert fake.patched_body() == before + after


def test_10_section_is_inserted_before_test_plan(fake):
    body = "## Summary\n\n- 要約\n\n## Test plan\n\n- [ ] 何か\n"
    design_pr(fake, body)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    written = fake.patched_body()
    head = "## Summary\n\n- 要約\n\n"
    assert written.startswith(head)
    assert written.endswith("## Test plan\n\n- [ ] 何か\n")
    assert written.index("## 決めたこと") < written.index("## Test plan")


def test_10_section_is_appended_without_test_plan(fake):
    body = "## Summary\n\n- 要約"
    design_pr(fake, body)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    written = fake.patched_body()
    assert written.startswith(body)
    assert written.rstrip("\n").endswith("- 決定 2: 実行の結果を担当の支持より先に見る")


def test_11_matching_section_is_not_written(fake):
    design_pr(fake, f"## Summary\n\n{EXPECTED}\n## Test plan\n")
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr
    assert not writes(fake)


def test_12_write_that_does_not_stick_returns_1(fake):
    design_pr(fake, "## Summary\n", patch_effective=False)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 1, out.stdout + out.stderr
    assert writes(fake)


def test_12_unreadable_after_write_returns_2(fake):
    design_pr(fake, "## Summary\n", fail_reread=True)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 2, out.stdout + out.stderr


def test_12_failed_write_returns_2(fake):
    design_pr(fake, "## Summary\n", fail_patch=True)
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 2, out.stdout + out.stderr


def test_12_sync_then_check_returns_0(fake):
    design_pr(fake, "## Summary\n\n## Test plan\n")
    assert fake.run("sync", "7", "--repo", REPO).returncode == 0
    out = fake.run("check", "7", "--repo", REPO)
    assert out.returncode == 0, out.stdout + out.stderr


# --- 退行しないこと・呼び出しの誤り --------------------------------------------


def test_20_sync_does_not_write_to_non_design_pull_request(fake):
    design_pr(fake, "## Summary\n", head="feature/issue-1-x")
    out = fake.run("sync", "7", "--repo", REPO)
    assert out.returncode == 0
    assert not writes(fake)


@pytest.mark.parametrize("args", [[], ["verify", "7"], ["check", "abc"], ["check"], ["sync", "7", "--bogus"]])
def test_22_usage_errors_return_3_without_reading(fake, args):
    design_pr(fake, EXPECTED)
    out = fake.run(*args)
    assert out.returncode == 3, (args, out.stdout, out.stderr)
    assert not fake.calls()


def test_22_repo_flag_without_value_returns_3_without_reading(fake):
    """現状固定: `--repo` に値を続けないと `[ -n "${2:-}" ] || usage` で終了コード 3。GitHub を読まない。"""
    design_pr(fake, EXPECTED)
    out = fake.run("check", "7", "--repo")
    assert out.returncode == 3, (out.stdout, out.stderr)
    assert not fake.calls()
