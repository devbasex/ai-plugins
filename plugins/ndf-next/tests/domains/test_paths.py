"""言語・テストのファイル・変更の分類の表（D2）を、表の要約が決める照らし方で確かめる。

照らし方の関数はこのテストの中の最小の写しで、読み手（cross-review・cross-refactoring の子）が同じ規則で表を読む。
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

import pytest

from ndf.contracts import facts


def language(path: str) -> str | None:
    """ファイル名の末尾に当たる最も長い拡張子の言語。"""
    name = PurePosixPath(path).name.lower()
    hits = [(len(ext), lid) for lid, exts in facts.column("languages", "extensions").items() for ext in exts if name.endswith(ext)]
    return max(hits)[1] if hits else None


def is_test(path: str) -> bool:
    p = PurePosixPath(path)
    dirs = set(p.parts[:-1])
    return any(
        (r["where"] == "dir" and r["pattern"] in dirs) or (r["where"] == "name" and fnmatchcase(p.name, r["pattern"]))
        for r in facts.rows("test-files")
    )


def words(path: str) -> set[str]:
    out = set()
    for part in re.split(r"[/._-]+", path):
        for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", part):
            w = w.lower()
            out.add(w[:-3] + "y" if w.endswith("ies") else w[:-1] if w.endswith("s") and not w.endswith("ss") else w)
    return out


def categories(path: str) -> set[str]:
    p = PurePosixPath(path)
    name, dirs, ws = p.name.lower(), {d.lower() for d in p.parts[:-1]}, words(path)
    return {
        r["id"]
        for r in facts.rows("path-categories")
        if ws & set(r["words"]) or dirs & set(r["dirs"]) or name in r["names"] or any(name.endswith(e) for e in r["extensions"])
    }


def test_extensions_belong_to_one_language():
    exts = [e for v in facts.column("languages", "extensions").values() for e in v]
    assert len(exts) == len(set(exts))
    assert language("resources/views/home.blade.php") == "blade"
    assert language("app/Models/User.php") == "php"
    assert language("components/Card.vue") == "vue"


def test_sql_is_production_code():
    assert facts.value("languages", language("dbt/models/orders.sql"), "code") is True


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/latest_price.py", False),
        ("src/contest_rules.ts", False),
        ("plugins/x/latest_version.py", False),
        ("app/Services/UserServiceTest.php", True),
        ("pkg/init_test.py", True),
        ("src/widget.spec.ts", True),
        ("src/__tests__/widget.ts", True),
        ("tests/helpers.py", True),
        ("cmd/server_test.go", True),
    ],
)
def test_test_files_are_judged_by_the_name_shape(path, expected):
    assert is_test(path) is expected


def test_name_patterns_end_with_their_languages_extension():
    exts = facts.column("languages", "extensions")
    for r in facts.rows("test-files", where="name"):
        assert any(r["pattern"].endswith(e) for e in exts[r["language"]]), r["id"]
    assert facts.column("test-files", "language").keys() == set(facts.ids("test-files", where="name"))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("app/Policies/UserPolicy.php", {"auth_security"}),
        ("app/Jobs/SendMail.php", {"performance"}),
        ("resources/views/home.blade.php", {"frontend"}),
        ("src/latest_price.py", set()),
    ],
)
def test_categories_split_camel_case_and_plurals(path, expected):
    assert categories(path) == expected
