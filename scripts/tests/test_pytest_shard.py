"""ファイル単位の割り当て（`scripts/pytest_shard.py`、#882・#4331）。

`files` の出力、NDF のテストを収集の前に外すプラグインのフック、`seconds` の実測秒の表の作り直しを確かめる。
`config` は `getini`・`rootpath`・`stash`・`pluginmanager` を持つ偽物を渡し、子プロセスの pytest は起動しない。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "pytest_shard.py"
sys.path.insert(0, str(REPO / "scripts"))
import pytest_shard  # noqa: E402

INI = {"python_files": ["test_*.py", "*_test.py"], "norecursedirs": [".*", "build"], "testpaths": ["tests"]}


class FakePluginManager:
    def __init__(self, xdist: bool) -> None:
        self.xdist = xdist
        self.registered: list[object] = []

    def hasplugin(self, name: str) -> bool:
        return self.xdist and name == "xdist"

    def register(self, plugin: object) -> None:
        self.registered.append(plugin)


def fake_config(rootpath: Path, *, xdist: bool = False, workerinput: dict | None = None) -> SimpleNamespace:
    config = SimpleNamespace(
        rootpath=rootpath,
        getini=INI.__getitem__,
        stash=pytest.Stash(),
        pluginmanager=FakePluginManager(xdist),
    )
    if workerinput is not None:
        config.workerinput = workerinput
    return config


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`plugins/ndf/tests` の下にテストのファイルを置いたリポジトリの木。`ROOT` と実測秒の表をここへ向ける。"""
    ndf = tmp_path / "plugins" / "ndf"
    for rel in ("tests/test_y.py", "tests/a/test_x.py", "tests/a/b_test.py", "tests/support/helper.py", "tests/.cache/test_z.py"):
        (ndf / rel).parent.mkdir(parents=True, exist_ok=True)
        (ndf / rel).write_text("", encoding="utf-8")
    monkeypatch.setattr(pytest_shard, "ROOT", tmp_path)
    monkeypatch.setattr(pytest_shard, "NDF_ROOT", ndf)
    monkeypatch.setattr(pytest_shard, "SECONDS", tmp_path / "seconds.json")
    monkeypatch.setenv("SHARD_TOTAL", "6")
    monkeypatch.setenv("SHARD_INDEX", "0")
    return tmp_path


def write_seconds(repo: Path, seconds: dict[str, float]) -> None:
    (repo / "seconds.json").write_text(json.dumps({"summary": "", "seconds": seconds}), encoding="utf-8")


def configured(repo: Path, index: int, **kw) -> SimpleNamespace:
    config = fake_config(repo / "plugins" / "ndf", **kw)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SHARD_INDEX", str(index))
        pytest_shard.pytest_configure(config)
    return config


# --- files（受け入れ条件 10 の前提の確認。今の出力が変わらないこと） ---


@pytest.mark.parametrize(("index", "expected"), [(0, "tests/a.py\n"), (1, "tests/b.py\ntests/c.py\n")])
def test_files_splits_by_item_count(index, expected, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("tests/a.py::t1\ntests/a.py::t2\ntests/b.py::t1\ntests/c.py::t1\n"))

    assert pytest_shard.main(["files", "--total", "2", "--index", str(index)]) == 0
    assert capsys.readouterr().out == expected


def test_module_loads_without_pytest(monkeypatch, capsys) -> None:
    """pytest の無い `python3` でも `files` を打てる（pytest はフックの中でだけ import する）。"""
    monkeypatch.setitem(sys.modules, "pytest", None)
    spec = importlib.util.spec_from_file_location("pytest_shard_without_pytest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "stdin", io.StringIO("tests/a.py::t1\n"))

    assert module.main(["files", "--total", "1", "--index", "0"]) == 0
    assert capsys.readouterr().out == "tests/a.py\n"


# --- shard_env と _stash_keys ---


def test_shard_env() -> None:
    assert pytest_shard.shard_env({"SHARD_TOTAL": "6", "SHARD_INDEX": "2"}) == (2, 6)
    assert pytest_shard.shard_env({"SHARD_TOTAL": "6", "SHARD_INDEX": "5"}) == (5, 6)
    assert pytest_shard.shard_env({}) is None


@pytest.mark.parametrize("index", ["6", "x"])
def test_shard_env_rejects_out_of_range(index) -> None:
    with pytest.raises(pytest.UsageError):
        pytest_shard.shard_env({"SHARD_TOTAL": "6", "SHARD_INDEX": index})


def test_stash_keys_are_stable() -> None:
    assert pytest_shard._stash_keys() == pytest_shard._stash_keys()


# --- _test_files ---


def test_test_files_lists_matching_files_from_repo_root(repo) -> None:
    files = pytest_shard._test_files(fake_config(repo / "plugins" / "ndf"))

    assert files == ["plugins/ndf/tests/a/b_test.py", "plugins/ndf/tests/a/test_x.py", "plugins/ndf/tests/test_y.py"]


# --- pytest_configure と _XdistHooks ---


def test_configure_puts_heavy_file_on_free_job(repo, monkeypatch) -> None:
    """重いファイルが 1 つのジョブを占め、残りは空いたジョブへ入る（受け入れ条件 8）。"""
    monkeypatch.setenv("SHARD_TOTAL", "2")
    write_seconds(
        repo,
        {"plugins/ndf/tests/test_y.py": 100.0, "plugins/ndf/tests/a/test_x.py": 1.0, "plugins/ndf/tests/a/b_test.py": 1.0},
    )

    config = configured(repo, 0)

    owner = config.stash[pytest_shard._stash_keys()[0]]
    assert owner["plugins/ndf/tests/test_y.py"] == 0
    assert owner["plugins/ndf/tests/a/test_x.py"] == 1
    assert owner["plugins/ndf/tests/a/b_test.py"] == 1


def test_configure_without_shard_env_does_nothing(repo, monkeypatch) -> None:
    monkeypatch.delenv("SHARD_TOTAL")
    config = fake_config(repo / "plugins" / "ndf", xdist=True)

    pytest_shard.pytest_configure(config)

    assert pytest_shard._stash_keys()[0] not in config.stash
    assert config.pluginmanager.registered == []


def test_worker_takes_owner_from_controller(repo, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(pytest_shard, "_test_files", lambda config: calls.append("files") or [])
    monkeypatch.setattr(pytest_shard, "read_seconds", lambda path: calls.append("seconds") or {})

    config = configured(repo, 3, workerinput={pytest_shard.OWNER_KEY: {"plugins/ndf/tests/test_y.py": 3}})

    assert calls == []
    assert config.stash[pytest_shard._stash_keys()[0]] == {"plugins/ndf/tests/test_y.py": 3}
    assert config.stash[pytest_shard._stash_keys()[1]] == 3


def test_worker_without_owner_stops(repo) -> None:
    with pytest.raises(pytest.UsageError, match="controller から割り当てを受け取れなかった"):
        configured(repo, 0, workerinput={})


def test_xdist_hooks_registered_only_with_xdist(repo) -> None:
    assert configured(repo, 0).pluginmanager.registered == []
    (hooks,) = configured(repo, 0, xdist=True).pluginmanager.registered
    assert isinstance(hooks, pytest_shard._XdistHooks)


def test_configure_node_passes_owner(repo) -> None:
    config = configured(repo, 0, xdist=True)
    node = SimpleNamespace(workerinput={})

    config.pluginmanager.registered[0].pytest_configure_node(node)

    assert node.workerinput[pytest_shard.OWNER_KEY] == config.stash[pytest_shard._stash_keys()[0]]


def test_configure_node_without_owner_puts_nothing(repo) -> None:
    node = SimpleNamespace(workerinput={})

    pytest_shard._XdistHooks(fake_config(repo)).pytest_configure_node(node)

    assert node.workerinput == {}


# --- pytest_ignore_collect（受け入れ条件 10） ---


def kept(repo: Path, config: SimpleNamespace, rels: list[str]) -> set[str]:
    return {r for r in rels if pytest_shard.pytest_ignore_collect(repo / r, config) is None}


def test_six_jobs_partition_files(repo) -> None:
    files = pytest_shard._test_files(fake_config(repo / "plugins" / "ndf"))

    shares = [kept(repo, configured(repo, i), files) for i in range(6)]

    assert sorted(f for s in shares for f in s) == files


def test_unlisted_file_belongs_to_job_zero(repo) -> None:
    extra = "plugins/ndf/tests/test_new.py"
    configs = [configured(repo, i) for i in range(6)]
    (repo / extra).write_text("", encoding="utf-8")

    assert [bool(kept(repo, c, [extra])) for c in configs] == [True, False, False, False, False, False]


def test_subdirectory_file_on_job_three_is_kept(repo, monkeypatch) -> None:
    config = configured(repo, 3, workerinput={pytest_shard.OWNER_KEY: {"plugins/ndf/tests/a/test_x.py": 3}})

    assert pytest_shard.pytest_ignore_collect(repo / "plugins/ndf/tests/a", config) is None
    assert pytest_shard.pytest_ignore_collect(repo / "plugins/ndf/tests/a/test_x.py", config) is None
    assert pytest_shard.pytest_ignore_collect(repo / "plugins/ndf/tests/test_y.py", config) is True


def test_ignore_collect_without_owner_returns_none(repo) -> None:
    config = fake_config(repo / "plugins" / "ndf")

    assert pytest_shard.pytest_ignore_collect(repo / "plugins/ndf/tests/test_y.py", config) is None
    assert pytest_shard.pytest_ignore_collect(repo / "plugins/ndf/tests", configured(repo, 5)) is None


# --- _file_of と seconds ---


def test_file_of_maps_classname_under_base(repo) -> None:
    ndf = repo / "plugins" / "ndf"

    assert pytest_shard._file_of("tests.test_y.TestX", ndf) == "plugins/ndf/tests/test_y.py"
    assert pytest_shard._file_of("tests.a.test_x.TestX.test_inner", ndf) == "plugins/ndf/tests/a/test_x.py"
    assert pytest_shard._file_of("tests.test_none.TestX", ndf) is None


def junit(path: Path, *cases: tuple[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<testcase classname="{c}" name="t" time="{t}"/>' for c, t in cases)
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")


def test_seconds_reads_root_and_ndf_xml(repo, capsys) -> None:
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "scripts" / "tests" / "test_s.py").write_text("", encoding="utf-8")
    store = repo / "junit"
    junit(store / "pytest-junit-0" / "pytest-junit-0.xml", ("scripts.tests.test_s.TestA", 1.5), ("scripts.tests.test_s", 1.0))
    junit(store / "pytest-ndf-junit-0" / "pytest-ndf-junit-0.xml", ("tests.test_y.TestB", 2.25), ("tests.a.test_x", 4.0))

    assert pytest_shard.main(["seconds", str(store)]) == 0

    body = json.loads((repo / "seconds.json").read_text(encoding="utf-8"))
    assert body["seconds"] == {
        "plugins/ndf/tests/a/test_x.py": 4.0,
        "plugins/ndf/tests/test_y.py": 2.2,
        "scripts/tests/test_s.py": 2.5,
    }


def test_seconds_stops_on_broken_xml(repo, capsys) -> None:
    write_seconds(repo, {"keep.py": 1.0})
    store = repo / "junit"
    junit(store / "pytest-junit-0" / "pytest-junit-0.xml", ("x", 1.0))
    broken = store / "pytest-ndf-junit-0" / "pytest-ndf-junit-0.xml"
    broken.parent.mkdir(parents=True)
    broken.write_text("<testsuites><testcase", encoding="utf-8")

    assert pytest_shard.main(["seconds", str(store)]) == 1

    assert str(broken) in capsys.readouterr().err
    assert json.loads((repo / "seconds.json").read_text(encoding="utf-8"))["seconds"] == {"keep.py": 1.0}
