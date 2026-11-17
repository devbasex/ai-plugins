from __future__ import annotations

import json
import subprocess
import textwrap

import pytest

from ndf.checks import lint
from ndf.checks.model import CheckError, Source


def sources(plugin, files: dict[str, str]) -> list[Source]:
    return [Source(plugin.write(rel, textwrap.dedent(text)), rel) for rel, text in files.items()]


def test_ruff_rules_use_thresholds_from_rules(plugin, rules):
    srcs = sources(
        plugin,
        {
            "src/ndf/a.py": """
            def f(a, b, c, d, e, g, h):
                try:
                    x()
                except Exception:  # noqa: BLE001 ファイル: 抑制コメントは ruff に読ませない
                    pass

            class C:
                def m(self, a, b, c, d, e, g):
                    pass
        """,
            "src/ndf/b.py": """
            def deep(a):
                if a:
                    for b in a:
                        with b:
                            while b:
                                pass
        """,
        },
    )
    hits = lint.scan(srcs, rules)
    assert sorted(hits[srcs[0].path.resolve()]) == [(2, "PLR0913"), (5, "BLE001"), (5, "S110")]
    assert hits[srcs[1].path.resolve()] == [(3, "PLR1702")]


def test_no_sources_does_not_start_ruff(rules):
    assert lint.scan([], rules) == {}


def test_except_rules_land_on_the_except_line(plugin, rules):
    srcs = sources(
        plugin,
        {
            "src/ndf/a.py": """
            def f():
                try:
                    x()
                except (
                    Exception,
                ):
                    recover()
                try:
                    x()
                except:
                    recover()
        """,
        },
    )
    assert sorted(lint.scan(srcs, rules)[srcs[0].path.resolve()]) == [(5, "BLE001"), (11, "E722")]


# 現状固定テスト（I-006: scan）


def _fake_run(monkeypatch, *, returncode=0, stdout="[]", stderr="", raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(lint.subprocess, "run", run)
    return calls


def _item(path, row, code):
    return {"filename": str(path), "location": {"row": row}, "code": code}


def test_scan_raises_check_error_when_ruff_cannot_start(plugin, rules, monkeypatch):
    srcs = sources(plugin, {"src/ndf/a.py": "x = 1\n"})
    _fake_run(monkeypatch, raises=FileNotFoundError("ruff"))
    with pytest.raises(CheckError, match="^ruff を起動できない: ruff"):
        lint.scan(srcs, rules)


@pytest.mark.parametrize("code", [2, 127, -9])
def test_scan_raises_check_error_on_unexpected_exit_code(plugin, rules, monkeypatch, code):
    srcs = sources(plugin, {"src/ndf/a.py": "x = 1\n"})
    _fake_run(monkeypatch, returncode=code, stderr="  bad config\n" + "e" * 400)
    with pytest.raises(CheckError) as err:
        lint.scan(srcs, rules)
    msg = str(err.value)
    assert msg.startswith(f"ruff が終了コード {code} で止まった: bad config")
    assert len(msg.split(": ", 1)[1]) == 300


def test_scan_raises_check_error_when_output_is_not_json(plugin, rules, monkeypatch):
    srcs = sources(plugin, {"src/ndf/a.py": "x = 1\n"})
    _fake_run(monkeypatch, returncode=1, stdout="not json")
    with pytest.raises(CheckError, match="^ruff の結果を読めない: "):
        lint.scan(srcs, rules)


def test_scan_with_exit_zero_and_no_items_returns_empty(plugin, rules, monkeypatch):
    srcs = sources(plugin, {"src/ndf/a.py": "x = 1\n"})
    calls = _fake_run(monkeypatch)
    assert lint.scan(srcs, rules) == {}
    cmd, kw = calls[0]
    assert cmd == lint.command(rules, [srcs[0].path])
    assert kw == {"capture_output": True, "text": True, "check": False}


def test_scan_moves_only_except_rules_in_known_sources(plugin, rules, monkeypatch, tmp_path):
    text = """
    try:
        x()
    except (
        Exception,
    ):
        pass
    """
    srcs = sources(plugin, {"src/ndf/a.py": text})
    path = srcs[0].path.resolve()
    other = tmp_path / "other.py"
    items = [
        _item(path, 6, "BLE001"),  # except の見出しの中 → except の行へ
        _item(path, 6, "PLR0913"),  # except の規則でない → そのまま
        _item(path, 7, "S110"),  # 見出しの外 → そのまま
        _item(other, 5, "BLE001"),  # 見るファイルの外 → そのまま
    ]
    _fake_run(monkeypatch, returncode=1, stdout=json.dumps(items))
    hits = lint.scan(srcs, rules)
    assert hits == {path: [(4, "BLE001"), (6, "PLR0913"), (7, "S110")], other.resolve(): [(5, "BLE001")]}


def test_scan_reads_each_source_tree_only_for_except_rules(plugin, rules, monkeypatch):
    srcs = sources(plugin, {"src/ndf/a.py": "x = (\n"})  # 構文の誤り: 木を作ると SyntaxError
    path = srcs[0].path.resolve()
    _fake_run(monkeypatch, returncode=1, stdout=json.dumps([_item(path, 1, "PLR0913")]))
    assert lint.scan(srcs, rules) == {path: [(1, "PLR0913")]}
    _fake_run(monkeypatch, returncode=1, stdout=json.dumps([_item(path, 1, "E722")]))
    with pytest.raises(SyntaxError):
        lint.scan(srcs, rules)


def test_handler_rows_maps_each_header_line_to_the_except_line(plugin):
    srcs = sources(
        plugin,
        {
            "src/ndf/a.py": """
            try:
                x()
            except (
                A,
                B,
            ):
                pass
            except C: y()
            """,
        },
    )
    assert lint.handler_rows(srcs[0]) == {4: 4, 5: 4, 6: 4, 7: 4, 9: 9}
