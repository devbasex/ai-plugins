#!/usr/bin/env python3
"""CI の pytest をファイル単位でジョブへ分ける割り当て。根の conftest.py と pytest.yml の NDF（plugins/ndf/）のテストの手順が使う。

    python3 scripts/pytest_shard.py seconds <JUnit XML の置き場>      # scripts/pytest-shard-seconds.json を作り直す
    pytest --collect-only -q | python3 scripts/pytest_shard.py files --total 6 --index 0 [--seconds <JSON>]

`files` は収集した項目の id（`<ファイル>::<名前>`）を標準入力から読み、そのジョブが受け持つファイルを 1 行ずつ出す。
重さは `--seconds` の実測秒（無いファイルは中央値）、指定が無ければファイルの項目数で数える。
値が古くても割り当ては成り立ち、偏りが戻るだけである。偏ってきたら `seconds` を打ち直す。

`seconds` は成果物の名前で根（`pytest-junit-<番号>`）と NDF（`pytest-ndf-junit-<番号>`）の XML を見分け、
どちらもリポジトリのルートから見たパスをキーにする。

NDF のテストは、このモジュールを pytest のプラグインとして読み込み、収集の前に持ち分でないファイルを外す（#4331 の決定 6）。

    cd plugins/ndf && SHARD_TOTAL=6 SHARD_INDEX=0 PYTHONPATH=<リポジトリ>/scripts uv run --frozen --project . pytest -p pytest_shard

割り当ては xdist の controller（か並列でない実行）が 1 回だけ実測秒の表から決め、worker へ `workerinput` で渡す。
pytest はフックの中でだけ import するので、pytest の無い `python3` でも `files` と `seconds` を打てる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parent.parent
SECONDS = ROOT / "scripts" / "pytest-shard-seconds.json"
NDF_ROOT = ROOT / "plugins" / "ndf"
# 根と NDF のテストの JUnit XML を見分ける成果物の名前の頭（.github/workflows/pytest.yml と同じ値）
ROOT_JUNIT = "pytest-junit-"
NDF_JUNIT = "pytest-ndf-junit-"
# 割り当てを controller から worker へ渡す workerinput のキー
OWNER_KEY = "pytest_shard_owner"
SHARD_TOTAL_ENV = "SHARD_TOTAL"
SHARD_INDEX_ENV = "SHARD_INDEX"
_KEYS: tuple[Any, Any] | None = None


def assign_files(files: list[str], total: int, weight: dict[str, float]) -> dict[str, int]:
    """重いファイルから順に、いちばん空いているジョブ（同じなら番号の小さい方）へ割り当てる。重さの無いファイルは中央値で数える。"""
    known = sorted(weight[f] for f in files if f in weight)
    default = known[len(known) // 2] if known else 1.0
    load = [0.0] * total
    owner = {}
    for f in sorted(files, key=lambda f: (-weight.get(f, default), f)):
        i = min(range(total), key=lambda j: (load[j], j))
        owner[f] = i
        load[i] += weight.get(f, default)
    return owner


def read_seconds(path: Path = SECONDS) -> dict[str, float]:
    """ファイルごとの実測秒。読めなければ空（重さは中央値の既定に倒れる）。"""
    try:
        return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8"))["seconds"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def shard_env(environ: Mapping[str, str]) -> tuple[int, int] | None:
    """`(index, total)` を返す。`SHARD_TOTAL` が無ければ `None`（分けない）。"""
    total = environ.get(SHARD_TOTAL_ENV, "")
    if total == "":
        return None
    index = environ.get(SHARD_INDEX_ENV, "")
    if not (total.isdigit() and index.isdigit() and int(index) < int(total)):
        import pytest

        raise pytest.UsageError(
            f"{SHARD_TOTAL_ENV}={total!r} / {SHARD_INDEX_ENV}={index!r}: "
            f"0 <= {SHARD_INDEX_ENV} < {SHARD_TOTAL_ENV} の整数を指定してください"
        )
    return int(index), int(total)


def _stash_keys() -> tuple[pytest.StashKey[dict[str, int]], pytest.StashKey[int]]:
    """`config.stash` のキー（割り当て・このジョブの番号）。最初の呼び出しで作り、以後は同じキーを返す。"""
    global _KEYS
    if _KEYS is None:
        import pytest

        _KEYS = (pytest.StashKey[dict[str, int]](), pytest.StashKey[int]())
    return _KEYS


def _rel(path: Path) -> str:
    """リポジトリのルートから見たパス。外のパスは絶対パス。"""
    path = path.resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def _is_test_file(name: str, config: pytest.Config) -> bool:
    return any(fnmatch(name, p) for p in config.getini("python_files"))


def _test_files(config: pytest.Config) -> list[str]:
    """`testpaths` の下で `python_files` に当たるファイルを、リポジトリのルートから見たパスで辞書順に並べる。"""
    norecurse = config.getini("norecursedirs")
    found: set[str] = set()
    for t in config.getini("testpaths") or ["."]:
        for top, dirs, names in os.walk(config.rootpath / t):
            dirs[:] = [d for d in dirs if not any(fnmatch(d, p) for p in norecurse)]
            found.update(_rel(Path(top) / n) for n in names if _is_test_file(n, config))
    return sorted(found)


class _XdistHooks:
    """xdist があるときだけ登録するフック（xdist が無い環境で未知のフックにならないようにクラスへ置く）。"""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_configure_node(self, node: Any) -> None:
        owner = self.config.stash.get(_stash_keys()[0], None)
        if owner is not None:
            node.workerinput[OWNER_KEY] = owner


def _worker_owner(workerinput: Mapping[str, Any]) -> dict[str, int]:
    """worker の割り当て。controller が `workerinput` に入れたものを受け取る。"""
    owner = workerinput.get(OWNER_KEY)
    if owner is None:
        import pytest

        raise pytest.UsageError("pytest_shard: controller から割り当てを受け取れなかった")
    return owner


def _controller_owner(config: pytest.Config, total: int) -> dict[str, int]:
    """controller の割り当て。テストのファイルを振り分け、xdist があれば worker へ渡すフックを登録する。"""
    owner = assign_files(_test_files(config), total, read_seconds(SECONDS))
    if config.pluginmanager.hasplugin("xdist"):
        config.pluginmanager.register(_XdistHooks(config))
    return owner


def pytest_configure(config: pytest.Config) -> None:
    """割り当てを決めて（worker は controller から受け取って）`config.stash` に置く。"""
    shard = shard_env(os.environ)
    if shard is None:
        return
    index, total = shard
    owner_key, index_key = _stash_keys()
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None:
        owner = _worker_owner(workerinput)
    else:
        owner = _controller_owner(config, total)
    config.stash[owner_key] = owner
    config.stash[index_key] = index


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    """持ち分でないテストのファイルを収集から外す。`False` は返さない（ほかのフックの判断を打ち消すため）。"""
    owner_key, index_key = _stash_keys()
    owner = config.stash.get(owner_key, None)
    if owner is None or collection_path.is_dir() or not _is_test_file(collection_path.name, config):
        return None
    if owner.get(_rel(collection_path), 0) != config.stash[index_key]:
        return True
    return None


def _file_of(classname: str, base: Path) -> str | None:
    """`a.b.test_x.TestY` を、`base` の下にある `a/b/test_x.py` へ戻し、リポジトリのルートから見たパスで返す。"""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        path = base / ("/".join(parts[:n]) + ".py")
        if path.is_file():
            return _rel(path)
    return None


def _junit_seconds(junit: Path) -> Counter[str] | None:
    """`junit` の下の JUnit XML を読み、ファイルごとの秒を合計する。読めない XML があれば None を返す。"""
    seconds: Counter[str] = Counter()
    for xml in sorted(junit.rglob("*.xml")):
        base = NDF_ROOT if NDF_JUNIT in xml.relative_to(junit).as_posix() else ROOT
        try:
            cases = list(ET.parse(xml).getroot().iter("testcase"))
        except ET.ParseError as e:
            sys.stderr.write(f"{xml} を読めない: {e}\n")
            return None
        for case in cases:
            rel = _file_of(case.get("classname", ""), base)
            if rel:
                seconds[rel] += float(case.get("time") or 0)
    return seconds


def _seconds(a: argparse.Namespace) -> int:
    seconds = _junit_seconds(Path(a.junit))
    if seconds is None:
        return 1
    if not seconds:
        sys.stderr.write(f"{a.junit} に JUnit XML の項目が無い\n")
        return 1
    body = {
        "summary": "テストのファイルごとの実測秒（CI の JUnit XML の合計）。根の conftest.py と NDF のテストの手順のプラグインが"
        "ジョブの割り当てに使う。作り直しは scripts/pytest_shard.py seconds",
        "seconds": {k: round(v, 1) for k, v in sorted(seconds.items())},
    }
    SECONDS.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{SECONDS.relative_to(ROOT)}: {len(seconds)} ファイル・合計 {sum(seconds.values()):.0f} 秒")
    return 0


def _files(a) -> int:
    counts = Counter(line.split("::", 1)[0] for line in sys.stdin.read().splitlines() if "::" in line)
    weight = read_seconds(Path(a.seconds)) if a.seconds else dict(counts)
    owner = assign_files(sorted(counts), a.total, weight)
    for f in sorted(f for f, i in owner.items() if i == a.index):
        print(f)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("seconds")
    s.add_argument("junit")
    f = sub.add_parser("files")
    f.add_argument("--total", type=int, required=True)
    f.add_argument("--index", type=int, required=True)
    f.add_argument("--seconds")
    a = p.parse_args(argv)
    return _seconds(a) if a.verb == "seconds" else _files(a)


if __name__ == "__main__":
    sys.exit(main())
