"""`scripts/check-service-boundary.py`（サービスのディレクトリの境界の検査。#2169）のテスト。

違反を 1 つずつ入れた一時の git リポジトリを入力にする。秘密の形の値はテストの中で組み立て、
このファイル自身が検査に当たらないようにする。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-service-boundary.py"

spec = importlib.util.spec_from_file_location("check_service_boundary", CHECK)
csb = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = csb
spec.loader.exec_module(csb)

ACCESS_KEY = "AKIA" + "QWERTYUIOP123456"
OTHER_KEY = "ASIA" + "ZXCVBNMLKJ987654"
PEM_LINE = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
MIT = "MIT License\n\nPermission is hereby granted, free of charge.\n"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Repo:
    """一時の git リポジトリ。プラグイン 1 つとサービスのディレクトリを持つ、違反の無い木から始める。"""

    def __init__(self, root: Path):
        self.root = root
        self.allow_path = root / "scripts" / "service-boundary-allow.json"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        self.marketplace([{"name": "p", "source": "./plugins/p"}])
        self.write("plugins/p/README.md", "# p\n")
        self.write("factory/LICENSE", MIT)
        self.write("factory/README.md", "# factory\n")
        self.write("factory/.env.example", "# 名前だけ\nAWS_REGION=\n")
        self.allow([])

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", rel], check=True)

    def remove(self, rel: str) -> None:
        subprocess.run(["git", "-C", str(self.root), "rm", "-qf", rel], check=True)

    def link(self, rel: str, target: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        subprocess.run(["git", "-C", str(self.root), "add", rel], check=True)

    def marketplace(self, plugins: list[dict]) -> None:
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "m", "plugins": plugins}))

    def allow(self, rows: list[dict], **extra) -> None:
        self.write("scripts/service-boundary-allow.json", json.dumps({"version": 1, "secrets": rows, **extra}))

    def run(self, capsys) -> tuple[int, list[str]]:
        code = csb.main(["--root", str(self.root)])
        lines = capsys.readouterr().out.splitlines()
        return code, [line.split(":")[0] for line in lines if not line.startswith("[")]

    def plugin_trees(self) -> list[str]:
        out = subprocess.run(["git", "-C", str(self.root), "ls-files", "plugins"], capture_output=True, text=True, check=True)
        return out.stdout.splitlines()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "r")


def test_clean_tree_passes(repo, capsys):
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 1・I2 -------------------------------------------------------


def test_service_files_do_not_change_result_or_distributed_tree(repo, capsys):
    before = repo.plugin_trees()
    repo.write("factory/web/index.ts", "export {}\n")
    assert repo.run(capsys) == (0, [])
    repo.remove("factory/README.md")
    assert repo.run(capsys) == (0, [])
    assert repo.plugin_trees() == before


@pytest.mark.parametrize("source", ["./", ".", "./factory", "./factory/web"])
def test_source_overlapping_service_fails(repo, capsys, source):
    repo.marketplace([{"name": "p", "source": source}])
    code, kinds = repo.run(capsys)
    assert code == 1
    assert "配布物の木の重なり" in kinds


# --- 受け入れ条件 2・I1 -------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("plugins/p/skills/a/SKILL.md", "読む: ../../../factory/README.md\n"),
        ("plugins/p/plugin.json", '{"x": "factory/api"}\n'),
    ],
)
def test_reference_to_service_from_plugins_fails(repo, capsys, rel, text):
    repo.write(rel, text)
    assert repo.run(capsys) == (1, ["サービスへの参照"])


def test_reference_from_marketplace_fails(repo, capsys):
    repo.marketplace([{"name": "p", "source": "./plugins/p", "description": "see factory/README.md"}])
    assert repo.run(capsys) == (1, ["サービスへの参照"])


def test_symlink_into_service_fails(repo, capsys):
    repo.link("plugins/p/svc", "../../factory")
    assert repo.run(capsys) == (1, ["サービスへの参照"])


def test_similar_words_and_references_outside_plugins_pass(repo, capsys):
    repo.write("plugins/p/a.md", "software-factory/ と my_factory/ は別の語\n")
    repo.write("README.md", "サービスは factory/ にある\n")
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 3・I7 -------------------------------------------------------


def test_replacing_license_keeps_result(repo, capsys):
    repo.write("factory/LICENSE", "Proprietary. All rights reserved.\n")
    assert repo.run(capsys) == (0, [])


def test_secret_in_license_fails(repo, capsys):
    repo.write("factory/LICENSE", MIT + ACCESS_KEY + "\n")
    assert repo.run(capsys) == (1, ["秘密らしい値"])


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
        ("factory/.env", "AWS_REGION=ap-northeast-1\n", ".env の実ファイル"),
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


def test_untracked_env_file_is_ignored(repo, capsys):
    (repo.root / "factory" / ".env").write_text("A=1\n", encoding="utf-8")
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
    repo.write("scripts/service-boundary-allow.json", json.dumps({"version": 1, **decl}))
    assert repo.run(capsys) == (2, [])


def test_outside_git_returns_2(tmp_path, capsys):
    assert csb.main(["--root", str(tmp_path), "--allow", str(tmp_path / "a.json")]) == 2


# --- I6 -----------------------------------------------------------------------


def test_env_example_with_value_in_service_fails(repo, capsys):
    repo.write("factory/.env.example", "# 注釈=値\nAWS_REGION=\nBEDROCK_REGION=us-east-1\n")
    assert repo.run(capsys) == (1, ["値を持つ .env.example"])


def test_env_example_outside_service_is_not_checked(repo, capsys):
    repo.write("plugins/p/.env.example", "API_KEY=your-key-here\n")
    assert repo.run(capsys) == (0, [])


# --- 受け入れ条件 5 -----------------------------------------------------------


def test_this_repository_passes(capsys):
    assert csb.main(["--root", str(REPO)]) == 0


def test_this_repository_fails_without_allow(tmp_path, capsys):
    (tmp_path / "allow.json").write_text(json.dumps({"version": 1, "secrets": []}), encoding="utf-8")
    assert csb.main(["--root", str(REPO), "--allow", str(tmp_path / "allow.json")]) == 1
