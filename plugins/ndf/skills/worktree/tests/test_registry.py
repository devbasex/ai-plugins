"""テスト環境の採番と台帳を検証する（受け入れ条件 26 の前提）。

割り当てを解放しても行を消さず、解放の時刻を書き込む（詳細設計 06 の決定 7）。
同じ番号を別の作業ツリーが使った履歴と、外部公開の記録を残すためである。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import LIB, run_lib


def registry_path(main_repo: Path) -> Path:
    return main_repo / ".git" / "ndf" / "worktree-registry.json"


def read_registry(main_repo: Path) -> dict:
    return json.loads(registry_path(main_repo).read_text(encoding="utf-8"))


def acquire(main_repo: Path, worktree: str, branch: str = "feature/x") -> str:
    got = run_lib(
        f'wt_slot_acquire "{main_repo}" "{worktree}" "{branch}" "env-{branch}"',
        cwd=main_repo,
    )
    return got.stdout.strip()


# --- 環境名 -----------------------------------------------------------------


def test_env_name_is_deterministic(main_repo: Path) -> None:
    first = run_lib(f'wt_env_name "{main_repo}" "feature/x"', cwd=main_repo).stdout.strip()
    second = run_lib(f'wt_env_name "{main_repo}" "feature/x"', cwd=main_repo).stdout.strip()
    assert first == second
    assert first.startswith("main-wt-feature-x-"), first


def test_env_name_uses_only_lowercase_and_dashes(main_repo: Path) -> None:
    name = run_lib(f'wt_env_name "{main_repo}" "Feature/Fix_ISSUE 146"', cwd=main_repo).stdout.strip()
    assert name == name.lower()
    assert all(c.isalnum() or c == "-" for c in name), name


def test_env_name_is_capped_at_40_characters(main_repo: Path) -> None:
    long_branch = "feature/" + "a" * 80
    name = run_lib(f'wt_env_name "{main_repo}" "{long_branch}"', cwd=main_repo).stdout.strip()
    assert len(name) == 40, name


def test_env_name_differs_per_branch(main_repo: Path) -> None:
    a = run_lib(f'wt_env_name "{main_repo}" "feature/a"', cwd=main_repo).stdout.strip()
    b = run_lib(f'wt_env_name "{main_repo}" "feature/b"', cwd=main_repo).stdout.strip()
    assert a != b


# --- ポート -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("band", "slot", "role", "expected"),
    [
        (20000, 0, 0, 20000),
        (20000, 0, 1, 20001),
        (20000, 1, 0, 20020),
        (20000, 3, 6, 20066),
    ],
)
def test_port_numbering(band: int, slot: int, role: int, expected: int) -> None:
    got = run_lib(f"wt_port_for {band} {slot} {role}")
    assert got.stdout.strip() == str(expected), got.stderr


def test_port_rejects_non_numeric() -> None:
    got = run_lib("wt_port_for 20000 a 0; echo rc=$?")
    assert got.stdout.strip() == "rc=1", got.stdout


# --- 台帳 -------------------------------------------------------------------


def test_first_acquire_takes_slot_zero(main_repo: Path) -> None:
    assert acquire(main_repo, "/wt/a") == "0"


def test_same_worktree_keeps_its_slot(main_repo: Path) -> None:
    first = acquire(main_repo, "/wt/a")
    second = acquire(main_repo, "/wt/a")
    assert first == second
    assert len(read_registry(main_repo)["assignments"]) == 1


def test_second_worktree_takes_the_next_slot(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    assert acquire(main_repo, "/wt/b", "feature/y") == "1"


def test_release_keeps_the_row_and_records_the_time(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)

    rows = read_registry(main_repo)["assignments"]
    assert len(rows) == 1, "行は消さない"
    assert rows[0]["released_at"] is not None, "解放の時刻を書き込む"


def test_released_slots_are_reusable(main_repo: Path) -> None:
    """空きの判定は解放済みの行を見ない。"""
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    assert acquire(main_repo, "/wt/b", "feature/y") == "0"


def test_reassignment_adds_a_row_and_keeps_the_past(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    acquire(main_repo, "/wt/a")

    rows = read_registry(main_repo)["assignments"]
    assert len(rows) == 2, "新しい行を足す"
    assert rows[0]["released_at"] is not None, "過去の行は変わらない"
    assert rows[1]["released_at"] is None


def test_slot_of_returns_nothing_after_release(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(f'wt_slot_release "{main_repo}" "/wt/a"', cwd=main_repo)
    got = run_lib(f'wt_slot_of "{main_repo}" "/wt/a"; echo rc=$?', cwd=main_repo)
    assert got.stdout.strip() == "rc=1", got.stdout


def test_registry_lives_outside_the_worktree(main_repo: Path, worktree: Path) -> None:
    """作業ツリーを消しても割り当ての記録が残るよう、共通の git ディレクトリへ置く。"""
    got = run_lib(f'wt_registry_path "{main_repo}"', cwd=worktree)
    path = Path(got.stdout.strip())
    assert path.parent.parent.name == ".git", got.stdout
    assert str(worktree) not in str(path)


def test_ports_are_recorded(main_repo: Path) -> None:
    acquire(main_repo, "/wt/a")
    run_lib(
        f"""wt_slot_set_ports "{main_repo}" "/wt/a" '{{"http":20000,"db":20001}}'""",
        cwd=main_repo,
    )
    rows = read_registry(main_repo)["assignments"]
    assert rows[0]["ports"] == {"http": 20000, "db": 20001}


def test_broken_registry_is_treated_as_empty(main_repo: Path) -> None:
    path = registry_path(main_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert acquire(main_repo, "/wt/a") == "0"


# --- 排他（#297 / #308） ----------------------------------------------------
#
# 同時に走らせたときの振る舞いは、繰り返したうえで件数で見る。1 回の実行では、
# 取りこぼしがあっても通ることがある。受け入れ条件が求める試行数（30 回 / 20 回）は
# 完了判定として手元で回し、テストへ入れるのは 1 件あたり数秒に収まる回数にする。

WF_LIB = Path(__file__).resolve().parents[3] / "skills/development-workflow/scripts/lib/workflow-common.sh"

# 実装は `scripts/lib/lock-common.sh` の 1 箇所にあり、2 つの読み込む側が既存の名前へ
# 結んでいる（#293）。**両方の読み込む側へ同じ検査をかける。**
LOCK_LIBS = [
    pytest.param(LIB, "wt_lock_acquire", "wt_lock_release", id="worktree"),
    pytest.param(WF_LIB, "wf_lock_acquire", "wf_lock_release", id="workflow"),
]

# 担当ごとに別のプロセスにする。同じシェルの部分シェルでは `$$` が親の番号を返し、
# 見張りのファイルが 1 つにまとまってしまう。
LOCK_WORKER = """#!/usr/bin/env bash
# $1 読み込むライブラリ / $2 取得の関数 / $3 解放の関数 / $4 置き場所 / $5 上限
set -uo pipefail
. "$1"
while [ ! -e "$4/go" ]; do :; done
if "$2" "$4/lock" "$5"; then
  : >"$4/in.$$"
  if [ "$(ls "$4"/in.* 2>/dev/null | wc -l)" -gt 1 ]; then : >"$4/over.$$"; fi
  sleep 0.02
  rm -f "$4/in.$$"
  "$3" "$4/lock"
else
  : >"$4/miss.$$"
fi
exit 0
"""


def _run_lock_race(
    tmp_path: Path, lib: Path, acquire: str, release: str, parallel: int, trials: int,
    timeout: int = 6,
) -> dict:
    """同じロックを `parallel` 個のプロセスで取りに行く試行を `trials` 回行う。

    臨界区間が重なった試行・上限に達して取れなかった回数・持ち主の決まらない
    ロックが残った試行を数えて返す。
    """
    worker = tmp_path / "lock-worker.sh"
    worker.write_text(LOCK_WORKER, encoding="utf-8")
    base = tmp_path / "race"
    base.mkdir(exist_ok=True)
    lock = base / "lock"
    result = {"overlap": 0, "miss": 0, "ownerless": 0}

    for _ in range(trials):
        for stray in base.iterdir():
            if stray.is_dir():
                shutil.rmtree(stray, ignore_errors=True)
            else:
                stray.unlink()
        procs = [
            subprocess.Popen(
                ["bash", str(worker), str(lib), acquire, release, str(base), str(timeout)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(parallel)
        ]
        (base / "go").touch()
        for proc in procs:
            proc.wait()
        if any(base.glob("over.*")):
            result["overlap"] += 1
        result["miss"] += len(list(base.glob("miss.*")))
        # 誰も持ち主にならないまま残ったロックは、以後だれも取れない（#308）。
        if lock.is_dir():
            owner = lock / "pid"
            if not owner.is_file() or not owner.read_text(encoding="utf-8").strip():
                result["ownerless"] += 1
    return result


@pytest.mark.parametrize(("parallel", "trials"), [(6, 7), (12, 3)])
@pytest.mark.parametrize(("lib", "acquire", "release"), LOCK_LIBS)
def test_many_at_once_never_share_the_critical_section(
    tmp_path: Path, lib: Path, acquire: str, release: str, parallel: int, trials: int
) -> None:
    """#297-1 / 2 / 3 と #308-5 を 1 つの測定で見る。

    並列数を 6 と 12 で変えても結果が変わらないことが、持ち主の決定が時間に依らない
    ことの担保になる。
    """
    got = _run_lock_race(tmp_path, lib, acquire, release, parallel=parallel, trials=trials)

    assert got["overlap"] == 0, f"臨界区間が重なった試行 {got['overlap']} 件"
    assert got["miss"] == 0, f"上限に達して取れなかった回数 {got['miss']} 回"
    assert got["ownerless"] == 0, f"持ち主の決まらないロックが残った試行 {got['ownerless']} 件"


@pytest.mark.parametrize(("lib", "acquire", "release"), LOCK_LIBS)
def test_the_lock_does_not_leave_noclobber_on_the_caller(
    tmp_path: Path, lib: Path, acquire: str, release: str
) -> None:
    """#297-7: 取得の成否のどちらでも、呼び出し側のシェルの `$-` に `C` を残さない。"""
    lock = tmp_path / "flag.lock"
    script = (
        f'set -uo pipefail\n. "{lib}"\n'
        f'{acquire} "{lock}" 1; echo taken=$-\n'
        f'{acquire} "{lock}" 1; echo missed=$-\n'
    )
    got = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    for line in got.stdout.split():
        assert "C" not in line.split("=", 1)[1], f"{line} に noclobber が残った"


def _run_lock_lib(lib: Path, snippet: str) -> subprocess.CompletedProcess:
    """`lib` を読み込んだうえで `snippet` を bash で実行する。2 つの読み込む側へ同じ検査をかける。"""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n. "{lib}"\n{snippet}\n'],
        capture_output=True, text=True,
    )


# 判定と取り除きは接頭辞だけが違う。両方の読み込む側へ同じ検査をかける。
LOCK_HELPERS = [
    pytest.param(LIB, "_wt", id="worktree"),
    pytest.param(WF_LIB, "_wf", id="workflow"),
]


@pytest.mark.parametrize(("lib", "prefix"), LOCK_HELPERS)
def test_a_lock_that_changed_hands_is_not_judged_stale(
    tmp_path: Path, lib: Path, prefix: str
) -> None:
    """#297-1 / 2: 判定の間に持ち主が替わったロックを、陳腐化と読まない。

    `kill -0` が偽になるのは、見始めたときの持ち主が離れたときにも起きる。離れた後に
    別の担当が取り直していれば、いま置かれているのは別のロックであり、捨ててよいもの
    ではない。番号だけを見て捨ててよいと読むと、生きているロックを外すことになる。
    """
    lock = tmp_path / "handover.lock"
    lock.mkdir()
    (lock / "held").touch()
    (lock / "pid").write_text("999999\n", encoding="utf-8")
    (lock / "token").write_text("new-owner\n", encoding="utf-8")

    got = _run_lock_lib(lib, f'{prefix}_lock_is_stale "{lock}" "old-owner"; echo rc=$?')

    assert "rc=1" in got.stdout, got.stdout


@pytest.mark.parametrize(("lib", "prefix"), LOCK_HELPERS)
def test_a_lock_that_changed_hands_is_never_moved_out(
    tmp_path: Path, lib: Path, prefix: str
) -> None:
    """#297-1 / 2: 判定と違うロックは、いっとき外へ出すこともしない。

    外へ出している間はロックの名前が空く。持ち主が臨界区間にいるまま、別の担当が
    関門を通れてしまう。**戻せば済むという扱いにはできない。** 戻すより先に取られると
    戻せず、持ち主のロックは捨てられる。
    """
    holder = tmp_path / "holder"
    holder.mkdir()
    lock = holder / "live.lock"
    lock.mkdir()
    (lock / "held").touch()
    (lock / "pid").write_text("999999\n", encoding="utf-8")
    (lock / "token").write_text("new-owner\n", encoding="utf-8")
    parent_before = holder.stat().st_mtime_ns

    got = _run_lock_lib(lib, f'{prefix}_lock_discard "{lock}" "old-owner" "tok"; echo rc=$?')

    assert "rc=1" in got.stdout, got.stdout
    assert lock.is_dir(), "判定と違うロックを取り除いた"
    # 名前の付け替えは、外へ出す側も戻す側も親のディレクトリの更新時刻を動かす。
    # ロックの中へ関門を置く手は動かさないため、外へ出したことだけを拾える。
    assert holder.stat().st_mtime_ns == parent_before, "外へ出した跡が親のディレクトリに残った"
    assert (lock / "held").exists(), "持ち主の握りの印が失われた"
    assert (lock / "token").read_text(encoding="utf-8").strip() == "new-owner"
    assert (lock / "pid").read_text(encoding="utf-8").strip() == "999999"


# 確かめてから取り除くまでの間へ割り込むための `cat`。1 度目の呼び出しが返った直後に、
# 同じ陳腐化を判定した別の担当が取り除いて取り直す様子を作る。取り直しに成功したら
# `rival` を残す。**割り込む側は本物の `cat` で動かす**（PATH を戻して起動する）。
LOCK_CAT_SHIM = """#!/usr/bin/env bash
REAL_CAT "$@"
rc=$?
if [ ! -e "$SHIM_STATE/first" ]; then
  : >"$SHIM_STATE/first"
  if PATH="$SHIM_PATH" bash -c '
      set -uo pipefail
      . "$1"
      "$2" "$4" "old-owner" "rival" || exit 1
      "$3" "$4" 1
    ' _ "$SHIM_LIB" "$SHIM_DISCARD" "$SHIM_ACQUIRE" "$SHIM_LOCK" >/dev/null 2>&1
  then
    : >"$SHIM_STATE/rival"
  fi
fi
exit $rc
"""


@pytest.mark.parametrize(("lib", "prefix"), LOCK_HELPERS)
def test_a_discard_shuts_out_another_discard_of_the_same_lock(
    tmp_path: Path, lib: Path, prefix: str
) -> None:
    """#297-1 / 2: 確かめてから取り除くまでの間に、別の担当が取り直せない。

    確かめることと取り除くことが別々だと、その間に別の担当が同じロックを捨てて取り直す。
    取り除く側は、確かめたものとは別の、**持ち主が臨界区間にいるロック**を取り除く。
    取り除きにも関門を置き、同じロックの取り除きを 1 つに限ることで塞ぐ。
    """
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    lock = tmp_path / "stale.lock"
    lock.mkdir()
    (lock / "held").touch()
    (lock / "token").write_text("old-owner\n", encoding="utf-8")
    real_cat = shutil.which("cat")
    assert real_cat is not None
    (shim_dir / "cat").write_text(
        LOCK_CAT_SHIM.replace("REAL_CAT", real_cat), encoding="utf-8"
    )
    (shim_dir / "cat").chmod(0o755)

    acquire = f"{prefix[1:]}_lock_acquire"
    got = _run_lock_lib(
        lib,
        f'export SHIM_STATE="{state}" SHIM_LOCK="{lock}" SHIM_LIB="{lib}"\n'
        f'export SHIM_DISCARD="{prefix}_lock_discard" SHIM_ACQUIRE="{acquire}"\n'
        f'export SHIM_PATH="$PATH"\n'
        f'export PATH="{shim_dir}:$PATH"\n'
        f'{prefix}_lock_discard "{lock}" "old-owner" "tok"; echo rc=$?',
    )

    assert (state / "first").exists(), f"割り込みが起きていない: {got.stdout} {got.stderr}"
    assert not (state / "rival").exists(), "取り除きの最中に、別の担当が同じロックを取り直した"
    assert "rc=0" in got.stdout, got.stdout
    assert not lock.exists(), "判定したロックが残った"


def test_six_registrations_at_once_all_survive(main_repo: Path, tmp_path: Path) -> None:
    """#297-4: 同時に登録しても行が欠けず、同じ番号が 2 つへ配られない。"""
    worker = tmp_path / "slot-worker.sh"
    worker.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        '. "$1"\n'
        'while [ ! -e "$3/go" ]; do :; done\n'
        'wt_slot_acquire "$2" "/wt/$4" "feature/$4" "env-$4" >/dev/null 2>&1\n'
        "exit 0\n",
        encoding="utf-8",
    )
    base = tmp_path / "slots"
    base.mkdir()
    path = registry_path(main_repo)

    short = 0
    duplicated = 0
    for _ in range(5):
        path.unlink(missing_ok=True)
        (base / "go").unlink(missing_ok=True)
        procs = [
            subprocess.Popen(
                ["bash", str(worker), str(LIB), str(main_repo), str(base), str(i)],
                cwd=str(main_repo), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for i in range(6)
        ]
        (base / "go").touch()
        for proc in procs:
            proc.wait()

        rows = [r for r in read_registry(main_repo)["assignments"] if r["released_at"] is None]
        if len(rows) != 6:
            short += 1
        if len({r["slot"] for r in rows}) != len(rows):
            duplicated += 1

    assert short == 0, f"有効な行が 6 件そろわなかった試行 {short} 件"
    assert duplicated == 0, f"同じ番号が 2 つ以上へ配られた試行 {duplicated} 件"


def _lock_body(path: Path, name: str) -> str:
    """関数の本体から、コメントと空行と余分な空白を除いた文字列を返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{name}() {{")
    end = lines.index("}", start)
    kept = [
        " ".join(line.split())
        for line in lines[start + 1:end]
        if line.strip() and not line.strip().startswith("#")
    ]
    body = "\n".join(kept)
    # 2 つの読み込む側で違ってよいのは、接頭辞と上限の既定値だけである。
    body = re.sub(r'timeout="\$\{2:-[^}]*\}"', 'timeout=DEFAULT', body)
    body = re.sub(r"\bW[TF]_LOCK_", "LOCK_", body)
    return body.replace("_wt_", "_lock_").replace("_wf_", "_lock_")


@pytest.mark.parametrize("name", ["lock_acquire", "lock_is_stale", "lock_discard"])
def test_the_two_lock_implementations_share_one_procedure(name: str) -> None:
    """#297-6: 片方だけに残る手を作らない。

    取得の本体だけでなく、判定と取り除きも突き合わせる。持ち主を決める手順は
    3 つに分かれており、どれか 1 つが片方だけ古いと、同じ症状が片側にだけ残る。

    #293 で本体を `lock-common.sh` へ寄せた後は、2 つの読み込む側が同じ形の委譲で
    あることを見る。**片方だけが共通ファイルを迂回して手を持ち直すと落ちる。**
    """
    wt = "wt_" + name if name == "lock_acquire" else "_wt_" + name
    wf = "wf_" + name if name == "lock_acquire" else "_wf_" + name
    assert _lock_body(LIB, wt) == _lock_body(WF_LIB, wf)
