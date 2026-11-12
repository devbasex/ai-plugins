"""`scripts/check-repo-secrets.py`（秘密・.env・インフラの定義の検査。#2169・#2646）のテスト。

違反を 1 つずつ入れた一時の git リポジトリを入力にする。秘密の形の値はテストの中で組み立て、
このファイル自身が検査に当たらないようにする。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-repo-secrets.py"

spec = importlib.util.spec_from_file_location("check_repo_secrets", CHECK)
crs = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = crs
spec.loader.exec_module(crs)

ACCESS_KEY = "AKIA" + "QWERTYUIOP123456"
OTHER_KEY = "ASIA" + "ZXCVBNMLKJ987654"
PEM_LINE = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Repo:
    """一時の git リポジトリ。違反の無い木から始める。"""

    def __init__(self, root: Path):
        self.root = root
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        self.write("plugins/p/README.md", "# p\n")
        self.allow([])

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", rel], check=True)

    def allow(self, rows: list[dict], **extra) -> None:
        self.write("scripts/repo-secrets-allow.json", json.dumps({"version": 1, "secrets": rows, **extra}))

    def run(self, capsys) -> tuple[int, list[str]]:
        code = crs.main(["--root", str(self.root)])
        lines = capsys.readouterr().out.splitlines()
        return code, [line.split(":")[0] for line in lines if not line.startswith("[")]


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "r")


def test_clean_tree_passes(repo, capsys):
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 4・I3 -------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("infra/cdk.json", '{"app": "npx ts-node bin/app.ts"}\n'),
        ("infra/package.json", '{"dependencies": {"aws-cdk-lib": "^2.0.0"}}\n'),
        ("infra/main.tf", 'resource "aws_s3_bucket" "b" {}\n'),
        ("infra/stack.yaml", "Resources:\n  B:\n    Type: AWS::S3::Bucket\n"),
        ("infra/stack.json", '{"AWSTemplateFormatVersion": "2010-09-09"}\n'),
    ],
)
def test_infra_definition_fails(repo, capsys, rel, text):
    repo.write(rel, text)
    assert repo.run(capsys) == (1, ["インフラの定義"])


def test_infra_words_in_markdown_pass(repo, capsys):
    repo.write("docs/infra.md", "`aws-cdk-lib` と `AWS::S3::Bucket` と cdk.json は private リポジトリに置く\n")
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 4・I4 -------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "text", "kind"),
    [
        ("src/a.py", f'KEY = "{ACCESS_KEY}"\n', "秘密らしい値"),
        ("src/b.md", f"{OTHER_KEY}\n", "秘密らしい値"),
        ("src/key.pem", f"{PEM_LINE}\nMIIE\n", "秘密らしい値"),
        ("src/.env", "AWS_REGION=ap-northeast-1\n", ".env の実ファイル"),
        ("src/.env.local", "A=1\n", ".env の実ファイル"),
    ],
)
def test_secret_like_values_fail(repo, capsys, rel, text, kind):
    repo.write(rel, text)
    assert repo.run(capsys) == (1, [kind])


def test_allowed_pair_passes_but_other_value_in_same_file_fails(repo, capsys):
    repo.write("src/t.py", f'FAKE = "{ACCESS_KEY}"\n')
    repo.allow([{"path": "src/t.py", "sha256": sha(ACCESS_KEY), "reason": "偽の値"}])
    assert repo.run(capsys) == (0, [])
    repo.write("src/t.py", f'FAKE = "{ACCESS_KEY}"\nREAL = "{OTHER_KEY}"\n')
    assert repo.run(capsys) == (1, ["秘密らしい値"])


def test_allowed_value_in_other_file_fails(repo, capsys):
    repo.write("src/t.py", f'FAKE = "{ACCESS_KEY}"\n')
    repo.write("src/u.py", f'FAKE = "{ACCESS_KEY}"\n')
    repo.allow([{"path": "src/t.py", "sha256": sha(ACCESS_KEY), "reason": "偽の値"}])
    assert repo.run(capsys) == (1, ["秘密らしい値"])


PEM_END = "-----" + "END RSA PRIVATE KEY" + "-----"


def test_allowed_pem_block_passes_but_replaced_key_fails(repo, capsys):
    fake = f"{PEM_LINE}\nFAKEFAKE\n{PEM_END}"
    repo.write("src/key.pem", fake + "\n")
    repo.allow([{"path": "src/key.pem", "sha256": sha(fake), "reason": "偽の鍵"}])
    assert repo.run(capsys) == (0, [])
    repo.write("src/key.pem", f"{PEM_LINE}\nREALREAL\n{PEM_END}\n")
    assert repo.run(capsys) == (1, ["秘密らしい値", "古い許可"])


def test_allowing_only_pem_begin_line_fails(repo, capsys):
    repo.write("src/key.pem", f"{PEM_LINE}\nMIIE\n{PEM_END}\n")
    repo.allow([{"path": "src/key.pem", "sha256": sha(PEM_LINE), "reason": "偽の鍵"}])
    assert repo.run(capsys)[0] == 1


@pytest.mark.parametrize("text", [f"{PEM_LINE}\r\nFAKE\r\n{PEM_END}\r\ntrailer\r\n", f"{PEM_LINE}\nFAKE\n", f'K = "{ACCESS_KEY}"\n'])
def test_printed_sha256_can_be_copied_into_allow(repo, capsys, text):
    repo.write("src/key.pem", text)
    assert crs.main(["--root", str(repo.root)]) == 1
    out = capsys.readouterr().out
    digests = re.findall(r"（sha256=([0-9a-f]{64})）", out)
    assert len(digests) == 1
    repo.allow([{"path": "src/key.pem", "sha256": digests[0], "reason": "偽の鍵"}])
    assert repo.run(capsys) == (0, [])


def test_untracked_env_file_is_ignored(repo, capsys):
    (repo.root / ".env").write_text("A=1\n", encoding="utf-8")
    assert repo.run(capsys) == (0, [])


# --- I5 -----------------------------------------------------------------------


def test_stale_allow_row_fails(repo, capsys):
    repo.write("src/t.py", "no secret\n")
    repo.allow([{"path": "src/t.py", "sha256": sha(ACCESS_KEY), "reason": "偽の値"}])
    assert repo.run(capsys) == (1, ["古い許可"])


@pytest.mark.parametrize(
    "decl",
    [
        {"secrets": [{"path": "src", "sha256": "0" * 64, "reason": "r"}]},
        {"secrets": [{"path": "src/*.py", "sha256": "0" * 64, "reason": "r"}]},
        {"secrets": [{"path": "src/t.py", "sha256": "0" * 64, "reason": "r", "extra": 1}]},
        {"secrets": [{"path": "src/t.py", "sha256": "xyz", "reason": "r"}]},
        {"secrets": [], "unknown": 1},
    ],
)
def test_malformed_allow_returns_2(repo, capsys, decl):
    repo.write("src/t.py", "x\n")
    repo.write("scripts/repo-secrets-allow.json", json.dumps({"version": 1, **decl}))
    assert repo.run(capsys) == (2, [])


def test_outside_git_returns_2(tmp_path, capsys):
    assert crs.main(["--root", str(tmp_path), "--allow", str(tmp_path / "a.json")]) == 2


# --- .env.example ------------------------------------------------------------


def test_env_example_is_not_checked(repo, capsys):
    repo.write("src/.env.example", "API_KEY=your-key-here\n")
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 5 -----------------------------------------------------------


def test_this_repository_fails_without_allow(tmp_path, capsys):
    (tmp_path / "allow.json").write_text(json.dumps({"version": 1, "secrets": []}), encoding="utf-8")
    assert crs.main(["--root", str(REPO), "--allow", str(tmp_path / "allow.json")]) == 1


# --- read_allow の現状固定 ----------------------------------------------------


def read_allow_with(tmp_path: Path, text: str) -> list[dict]:
    path = tmp_path / "allow.json"
    path.write_text(text, encoding="utf-8")
    return crs.read_allow(path, tmp_path)


def test_read_allow_returns_rows_as_written(tmp_path):
    row = {"path": "src/t.py", "sha256": "a" * 64, "reason": "偽の値"}
    assert read_allow_with(tmp_path, json.dumps({"version": 1, "secrets": [row]})) == [row]


def test_read_allow_without_secrets_is_empty(tmp_path):
    assert read_allow_with(tmp_path, json.dumps({"version": 1})) == []


def test_read_allow_missing_file_is_unreadable(tmp_path):
    with pytest.raises(crs.Unreadable, match="^許可の一覧を読めない: "):
        crs.read_allow(tmp_path / "none.json", tmp_path)


@pytest.mark.parametrize(
    ("text", "prefix"),
    [
        ("{", "許可の一覧を読めない: "),
        ("[]", "許可の一覧の形が違う"),
        (json.dumps({"secrets": []}), "許可の一覧の形が違う"),
        (json.dumps({"version": 2, "secrets": []}), "許可の一覧の形が違う"),
        (json.dumps({"version": "1", "secrets": []}), "許可の一覧の形が違う"),
        (json.dumps({"version": 1, "secrets": {}}), "許可の一覧の secrets が並びでない: "),
        (json.dumps({"version": 1, "secrets": ["x"]}), "許可の行の鍵は "),
        (json.dumps({"version": 1, "secrets": [{"path": "a", "sha256": "0" * 64}]}), "許可の行の鍵は "),
        (json.dumps({"version": 1, "secrets": [{"path": "", "sha256": "0" * 64, "reason": "r"}]}), "許可の行の path と reason が空: "),
        (json.dumps({"version": 1, "secrets": [{"path": 1, "sha256": "0" * 64, "reason": "r"}]}), "許可の行の path と reason が空: "),
        (json.dumps({"version": 1, "secrets": [{"path": "a", "sha256": "0" * 64, "reason": "  "}]}), "許可の行の path と reason が空: "),
        (json.dumps({"version": 1, "secrets": [{"path": "a?.py", "sha256": "0" * 64, "reason": "r"}]}), "許可の path はファイル 1 つだけ"),
        (
            json.dumps({"version": 1, "secrets": [{"path": "a[0].py", "sha256": "0" * 64, "reason": "r"}]}),
            "許可の path はファイル 1 つだけ",
        ),
        (json.dumps({"version": 1, "secrets": [{"path": "src/", "sha256": "0" * 64, "reason": "r"}]}), "許可の path はファイル 1 つだけ"),
        (
            json.dumps({"version": 1, "secrets": [{"path": "a", "sha256": "A" * 64, "reason": "r"}]}),
            "許可の sha256 が 64 字の 16 進でない: ",
        ),
        (json.dumps({"version": 1, "secrets": [{"path": "a", "sha256": 0, "reason": "r"}]}), "許可の sha256 が 64 字の 16 進でない: "),
    ],
)
def test_read_allow_rejects_malformed(tmp_path, text, prefix):
    with pytest.raises(crs.Unreadable) as e:
        read_allow_with(tmp_path, text)
    assert str(e.value).startswith(prefix)


def test_read_allow_rejects_existing_directory(tmp_path):
    (tmp_path / "src").mkdir()
    text = json.dumps({"version": 1, "secrets": [{"path": "src", "sha256": "0" * 64, "reason": "r"}]})
    with pytest.raises(crs.Unreadable, match="^許可の path はファイル 1 つだけ"):
        read_allow_with(tmp_path, text)


def test_read_allow_checks_rows_in_order(tmp_path):
    rows = [
        {"path": "a", "sha256": "0" * 64, "reason": "r"},
        {"path": "b", "sha256": "bad", "reason": "r"},
        {"path": "*", "sha256": "0" * 64, "reason": "r"},
    ]
    with pytest.raises(crs.Unreadable, match="^許可の sha256"):
        read_allow_with(tmp_path, json.dumps({"version": 1, "secrets": rows}))
