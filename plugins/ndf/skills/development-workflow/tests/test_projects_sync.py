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
        f'printf "%s\\n" "$*" >> "{log}"\n' + (f'printf "%s" {stderr!r} >&2\n' if stderr else "") + f"exit {exit_code}\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}


def result(got) -> dict:
    """標準出力は 1 行の結果 JSON である（#2227。形は scripts/lib/README.md の「手順のスクリプトの結果」）。"""
    lines = got.stdout.splitlines()
    assert len(lines) == 1, got.stdout
    return json.loads(lines[0])


def board_gaps(got) -> list[str]:
    """ボードへ書けなかった理由（欠けの board の要素）。"""
    return [g["reason"] for g in result(got).get("gaps", []) if g["kind"] == "board"]


def test_no_declaration_writes_only_the_issue_body(repo, tmp_path) -> None:
    """宣言が無いリポジトリではボードへ問い合わせず、issue の本文だけを更新する（#828）。"""
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 0 and got.stderr == ""
    res = result(got)
    assert res["status"] == "ok" and res["summary"] == "#186 進行 = 実装レビュー"
    assert {"kind": "board", "name": "#186 stage", "result": "not_declared"} in res["items"]
    calls = log.read_text(encoding="utf-8")
    assert "issue edit" in calls
    assert "project" not in calls, "宣言が無いのにボードへ問い合わせている"


def without_gh(tmp_path) -> dict:
    """`gh` だけを PATH から外す。実行に要る他のコマンドは残す。

    PATH を空にすると `bash` 自体が見つからず、検証したい経路へ届かない。
    """
    bindir = tmp_path / "nogh"
    bindir.mkdir(exist_ok=True)
    for name in ("bash", "git", "jq", "grep", "dirname", "printf", "python3"):
        found = shutil.which(name)
        if found:
            link = bindir / name
            if not link.exists():
                link.symlink_to(found)
    return {"PATH": str(bindir)}


def test_missing_gh_is_a_missing_precondition(repo, tmp_path) -> None:
    """`gh` が無ければ何も書かず、前提が無い（3）の結果 JSON を返す。止めるかは呼ぶ側が決める（#2227）。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=without_gh(tmp_path))
    assert got.returncode == 3, got.stderr
    assert result(got)["status"] == "stopped"


def test_gh_failure_is_reported_as_gaps(repo, tmp_path) -> None:
    """権限不足などで `gh` が落ちたら ok で返さず、`gh` の標準エラーの要旨を欠けに載せる（#2227）。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    env = fake_gh(tmp_path, log, exit_code=1, stderr="missing scope 'project'")
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 6, got.stderr
    res = result(got)
    assert res["status"] == "stopped"
    assert all("missing scope 'project'" in g["reason"] for g in res["gaps"]) and len(res["gaps"]) == 2


def test_unknown_key_is_an_error(repo, tmp_path) -> None:
    """知らないキーは呼び出し側の誤りである。黙って進まない。"""
    write_declaration(repo, VALID)
    got = run_sync("186", "nope", "x", cwd=repo, env=fake_gh(tmp_path, tmp_path / "gh.log"))
    assert got.returncode == 2
    assert "nope" in got.stderr


def test_unknown_stage_is_an_error(repo, tmp_path) -> None:
    """工程名は工程表の行と一致する。綴りの誤りをボードへ書き込まない。"""
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
    """宣言があり `gh` があるときは、ボードの更新を試みる。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=fake_gh(tmp_path, log))
    assert log.exists(), "gh が呼ばれていない"
    assert "devbasex" in log.read_text(encoding="utf-8")
    # 偽の gh はボードの応答を返さないため、ボードは欠けとして返る
    assert got.returncode == 6 and board_gaps(got) == ["ボードの識別子が無い"]


def test_text_key_reaches_gh(repo, tmp_path) -> None:
    """文字列のフィールドは値をチェックしない。任意のパスが入る。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "plan", "issues/issue-186.md", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 6 and board_gaps(got) == ["ボードの識別子が無い"]
    assert log.exists()


def test_stage_with_a_space_reaches_gh(repo, tmp_path) -> None:
    """空白を含む工程名も 1 つの値として通る。

    `Pull Request` は工程表で唯一の空白を含む行名である。呼び出し側が引用を落とすと
    4 引数になり、引数のチェックで終了コード 2 になる。スクリプトは 1 つの値として渡され
    さえすれば扱えることを、この経路で確かめる。
    """
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "Pull Request", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 6 and board_gaps(got) == ["ボードの識別子が無い"], got.stderr
    assert log.exists(), "gh が呼ばれていない"


def test_stage_split_by_a_space_is_an_error(repo, tmp_path) -> None:
    """引用を落として 4 引数になった呼び方は、呼び出し側の誤りとして 2 を返す。"""
    write_declaration(repo, VALID)
    log = tmp_path / "gh.log"
    got = run_sync("186", "stage", "Pull", "Request", cwd=repo, env=fake_gh(tmp_path, log))
    assert got.returncode == 2
    assert not log.exists(), "誤った呼び方で gh を呼んでいる"


HOST_REPO = "devbasex/ai-plugins"
OTHER_REPO = "devbasex/devbase"


def scripted_gh(tmp_path, items, repo: str = HOST_REPO, total_count: int | None = None) -> dict:
    """ボードの応答を返す `gh` を PATH の先頭へ置く。

    `items` はアイテムの並びで、要素は issue 番号か `(リポジトリ, 番号)` である。番号だけの
    要素は `repo` に属するものとして扱う。`repo` は `gh repo view` が返す、いま開いている
    リポジトリである。`total_count` はボードの総数で、省略すると並びの長さになる。

    呼ばれた引数は `tmp_path/gh.log` へ残る。どのアイテムを更新したかはここから読む。
    """
    entries = [(repo, n) if isinstance(n, int) else n for n in items]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "gh-data"
    data.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    (data / "view.json").write_text(json.dumps({"id": "PVT_1"}), encoding="utf-8")
    (data / "items.json").write_text(
        json.dumps(
            {
                "items": [{"id": item_id(r, n), "content": {"number": n, "repository": r}} for r, n in entries],
                "totalCount": len(entries) if total_count is None else total_count,
            }
        ),
        encoding="utf-8",
    )
    (data / "fields.json").write_text(
        json.dumps(
            {
                "fields": [
                    {"id": "FLD_1", "name": "進行", "options": [{"id": "OPT_1", "name": "実装レビュー"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case "$1 $2" in\n'
        f'  "repo view") printf "%s\\n" {repo!r} ;;\n'
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


def item_id(repo: str, number: int) -> str:
    """リポジトリごとに区別できるアイテムの識別子。"""
    return f"IT_{repo.split('/')[-1]}_{number}"


def test_item_below_the_limit_is_updated(repo, tmp_path) -> None:
    """上限より少ないボードでは、対象が見つかって更新される。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, list(range(180, 190)))
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 0, got.stderr
    assert result(got)["summary"] == "#186 進行 = 実装レビュー / #186 進行 = 実装レビュー（ボード）"
    assert got.stderr == ""


def test_missing_item_below_the_limit_is_added_or_reported(repo, tmp_path) -> None:
    """ボードへ登録していなければ載せる。載せられなければ（偽の gh は課題の URL を返さない）欠けとして返す（#2227）。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, [1, 2, 3])
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 6 and got.stderr == ""
    assert board_gaps(got) == ["課題の URL が空"]


def test_another_repository_with_the_same_number_is_not_updated(repo, tmp_path) -> None:
    """同じ番号のアイテムが 2 つのリポジトリ分あるとき、いま開いている側だけを選ぶ。

    ボードは組織単位で、issue 番号はリポジトリごとに独立している。番号だけで選ぶと、
    並び順によっては別のリポジトリのアイテムを書き換える。
    """
    write_declaration(repo, VALID)
    # 別のリポジトリのアイテムを先に置く。番号だけで選ぶ実装ならこちらが当たる。
    env = scripted_gh(tmp_path, [(OTHER_REPO, 186), (HOST_REPO, 186)])
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 0, got.stderr
    assert result(got)["status"] == "ok"
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert item_id(HOST_REPO, 186) in log
    assert item_id(OTHER_REPO, 186) not in log, "別のリポジトリのアイテムを更新している"


def test_only_another_repository_has_the_number_is_not_updated(repo, tmp_path) -> None:
    """同じ番号が別のリポジトリにしか無いときは、そのアイテムを書き換えない。載せられなければ欠けとして返す。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, [(OTHER_REPO, 186)])
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 6 and got.stderr == ""
    assert board_gaps(got) == ["課題の URL が空"]
    log = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "item-edit" not in log, "別のリポジトリのアイテムを更新している"


def test_missing_item_beyond_the_limit_is_reported(repo, tmp_path) -> None:
    """総数が上限を超えるときは知らせる。登録していない場合と区別できないためである。

    書けなかったことを欠け（6）で返す。止めるかは呼ぶ側が決める（#2227）。
    """
    write_declaration(repo, VALID)
    # 1000 件を返し、ボードの総数は 1200 件だと申告する。その中に #186 は含めない。
    env = scripted_gh(tmp_path, list(range(1000, 2000)), total_count=1200)
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 6
    assert any("上限 1000" in r and "1200" in r for r in board_gaps(got))
    assert "上限 1000" in got.stderr
    assert "1200" in got.stderr
    assert "#186" in got.stderr


def test_missing_item_exactly_at_the_limit_is_not_a_truncation(repo, tmp_path) -> None:
    """総数がちょうど上限のときは切れていない。上限の警告を出さず、載せる側へ進む。"""
    write_declaration(repo, VALID)
    env = scripted_gh(tmp_path, list(range(1000, 2000)), total_count=1000)
    got = run_sync("186", "stage", "実装レビュー", cwd=repo, env=env)
    assert got.returncode == 6 and got.stderr == ""
    assert board_gaps(got) == ["課題の URL が空"]
