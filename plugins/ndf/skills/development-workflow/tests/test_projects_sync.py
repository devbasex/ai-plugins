"""入口のスクリプトを検証する（受け入れ条件 1〜3）。

GitHub へは通信しない。`gh` を PATH から外すか、記録するだけの偽物へ差し替える。
"""
from __future__ import annotations

import json
import os
import shutil
import stat

from projects_helpers import run_sync, write_declaration

VALID = json.dumps({"version": 1, "owner": "devbasex", "number": 1})


def fake_gh(tmp_path, log, exit_code: int = 0, stderr: str = "") -> dict:
    """`gh` の代わりに引数を記録するだけの実行ファイルを PATH の先頭へ置く。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        + (f'printf "%s" {stderr!r} >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}


def test_no_declaration_is_silent(repo, tmp_path) -> None:
    """宣言が無いリポジトリでは何も出力せず 0 で終わる。"""
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert got.stdout == "" and got.stderr == ""
    assert not log.exists(), "宣言が無いのに gh を呼んでいる"


def without_gh(tmp_path) -> dict:
    """`gh` だけを PATH から外す。実行に要る他のコマンドは残す。

    PATH を空にすると `bash` 自体が見つからず、検証したい経路へ届かない。
    """
    bindir = tmp_path / "nogh"
    bindir.mkdir(exist_ok=True)
    for name in ("bash", "git", "jq", "grep", "dirname", "printf"):
        found = shutil.which(name)
        if found:
            link = bindir / name
            if not link.exists():
                link.symlink_to(found)
    return {"PATH": str(bindir)}


def test_missing_gh_is_silent(repo, tmp_path) -> None:
    """`gh` が無い環境でも 0 で終わる。進行管理を開発の前提条件にしない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=without_gh(tmp_path))
    assert got.returncode == 0, got.stderr
    assert got.stdout == ""


def test_gh_failure_is_silent(repo, tmp_path) -> None:
    """権限不足などで `gh` が落ちても 0 で終わる。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    env = fake_gh(tmp_path, log, exit_code=1, stderr="missing scope 'project'")
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=env)
    assert got.returncode == 0


def test_unknown_key_is_an_error(repo, tmp_path) -> None:
    """知らないキーは呼び出し側の誤りである。黙って進まない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "nope", "x", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2
    assert "nope" in got.stderr


def test_unknown_stage_is_an_error(repo, tmp_path) -> None:
    """工程名は工程表の行と一致する。綴りの誤りを盤面へ書き込まない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", "レビューする", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2
    assert "レビューする" in got.stderr


def test_unknown_mode_is_an_error(repo, tmp_path) -> None:
    write_declaration(repo, VALID)
    got = run_sync("186", "mode", "medium", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2


def test_missing_arguments_is_an_error(repo, tmp_path) -> None:
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2


def test_known_stage_reaches_gh(repo, tmp_path) -> None:
    """宣言があり `gh` があるときは、盤面の更新を試みる。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert log.exists(), "gh が呼ばれていない"
    assert "devbasex" in log.read_text(encoding="utf-8")


def test_text_key_reaches_gh(repo, tmp_path) -> None:
    """文字列のフィールドは値を検査しない。任意のパスが入る。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "plan", "issues/issue-186.md", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0
    assert log.exists()


def test_stage_with_a_space_reaches_gh(repo, tmp_path) -> None:
    """空白を含む工程名も 1 つの値として通る。

    `Pull Request` は工程表で唯一の空白を含む行名である。呼び出し側が引用を落とすと
    4 引数になり、引数の検査で終了コード 2 になる。スクリプトは 1 つの値として渡され
    さえすれば扱えることを、この経路で確かめる。
    """
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "Pull Request", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0, got.stderr
    assert log.exists(), "gh が呼ばれていない"


def test_stage_split_by_a_space_is_an_error(repo, tmp_path) -> None:
    """引用を落として 4 引数になった呼び方は、呼び出し側の誤りとして 2 を返す。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "Pull", "Request", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 2
    assert not log.exists(), "誤った呼び方で gh を呼んでいる"


def scripted_gh(tmp_path, items: list[int]) -> dict:
    """盤面の応答を返す `gh` を PATH の先頭へ置く。

    `item-list` が返すアイテムの並びだけをテストごとに差し替える。取得の上限に達した
    ことは、返ってきた件数が上限と等しいかどうかで判断される。
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "gh-data"
    data.mkdir(exist_ok=True)
    (data / "view.json").write_text(json.dumps({"id": "PVT_1"}), encoding="utf-8")
    (data / "items.json").write_text(
        json.dumps({"items": [{"id": f"IT_{n}", "content": {"number": n}} for n in items]}),
        encoding="utf-8",
    )
    (data / "fields.json").write_text(
        json.dumps({"fields": [
            {"id": "FLD_1", "name": "進行",
             "options": [{"id": "OPT_1", "name": "レビュー"}]},
        ]}),
        encoding="utf-8",
    )
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1 $2" in\n'
        f'  "project view") cat "{data}/view.json" ;;\n'
        f'  "project item-list") cat "{data}/items.json" ;;\n'
        f'  "project field-list") cat "{data}/fields.json" ;;\n'
        "  *) : ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}


def test_item_below_the_limit_is_updated(repo, tmp_path) -> None:
    """上限より少ない盤面では、対象が見つかって更新される。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, list(range(180, 190)))
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=env)
    assert got.returncode == 0, got.stderr
    assert "#186 進行 = レビュー" in got.stdout
    assert got.stderr == ""


def test_missing_item_below_the_limit_is_silent(repo, tmp_path) -> None:
    """盤面へ登録していないだけなら黙って抜ける。これは正常な状態である。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, [1, 2, 3])
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=env)
    assert got.returncode == 0
    assert got.stdout == "" and got.stderr == ""


def test_missing_item_at_the_limit_is_reported(repo, tmp_path) -> None:
    """取得が上限に達したときは知らせる。登録していない場合と区別できないためである。

    終了コードは 0 のままにする。進行管理が理由で工程を止めない。
    """
    write_declaration(repo, VALID)
    # 1000 件ちょうどを返し、その中に #186 は含めない。
    env = scripted_gh(tmp_path, list(range(1000, 2000)))
    got = run_sync("186", "stage", "レビュー", cwd=repo, env=env)
    assert got.returncode == 0
    assert got.stdout == ""
    assert "上限 1000" in got.stderr
    assert "#186" in got.stderr
