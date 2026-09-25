"""一覧に含まれるかの判定が pipefail の下でも揺れない（#906）。

`printf | grep -q` では grep が一致で先に終わると printf が SIGPIPE を受け、pipefail の下で
パイプ全体が 141 になる。一覧をパイプの緩衝（64 KiB）より大きくし、先頭で一致させると
この競合を毎回起こせる。
"""
from __future__ import annotations

import pathlib
import subprocess

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib" / "projects-common.sh"


def test_a_match_early_in_a_long_list_is_found_under_pipefail():
    script = (
        "set -o pipefail\n"
        f". '{LIB}'\n"
        'list="設計"\n'
        'filler=$(printf "x%.0s" $(seq 200))\n'
        'for i in $(seq 1000); do list="$list"$\'\\n\'"$filler$i"; done\n'
        '_pj_in_list "$list" "設計"; echo "rc=$?"\n'
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr


def test_a_value_not_in_the_list_is_rejected():
    script = f"set -o pipefail\n. '{LIB}'\n_pj_in_list $'a\\nb' c; echo \"rc=$?\"\n"
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip().endswith("rc=1"), out.stdout + out.stderr
