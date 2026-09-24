"""対応表の読み込み境界: version 1 の形だけを通し、壊れた形は各入口へ届く前に止める。"""
import json

import pytest

from serena_lsp import check, table


def _valid():
    return table.load()


def _write(tmp_path, data):
    path = tmp_path / "languages.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def test_bundled_table_is_version_1_and_loads():
    data = _valid()
    assert data["version"] == 1
    assert [lang["serena"] for lang in data["languages"]][:3] == ["python", "typescript", "php"]


def _broken(mutate):
    data = _valid()
    mutate(data)
    return data


@pytest.mark.parametrize("data", [
    "{",
    [],
    _broken(lambda d: d.update(version=2)),
    _broken(lambda d: d.pop("version")),
    _broken(lambda d: d.pop("threshold")),
    _broken(lambda d: d["threshold"].update(min_files="10")),
    _broken(lambda d: d.update(languages={})),
    _broken(lambda d: d["languages"][0].pop("extensions")),
    _broken(lambda d: d["languages"][0].update(claude_plugin=1)),
    _broken(lambda d: d["languages"][0].update(binaries=[{"command": "x"}])),
    _broken(lambda d: d["languages"][0].pop("extra_checks")),
    _broken(lambda d: d["languages"].append("zig")),
])
def test_invalid_table_is_rejected_at_load(tmp_path, data):
    with pytest.raises(ValueError):
        table.load(_write(tmp_path, data))


def test_invalid_table_makes_check_exit_2(tmp_path, monkeypatch):
    root = tmp_path / "r"
    (root / ".serena").mkdir(parents=True)
    (root / ".serena/project.yml").write_text("language_servers:\n- python\nmcp_serena_excluded: []\n")
    monkeypatch.setenv("SERENA_LSP_TABLE", str(_write(tmp_path, _broken(lambda d: d.update(version=2)))))
    out, code = check.run(root, runtime="codex")
    assert (code, out["missing"]) == (2, [])
    assert out["error"].startswith("対応表を読めません")
