"""issue の本文への進行の記録（#243）。

**盤面の宣言が無いリポジトリでも進行が残る。** 記録先は本文の `## 進行` の節で、節の外は
書き換えない。人が本文へ書いた内容を消さないためである。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "progress-record.sh"


@pytest.fixture()
def fake_gh(tmp_path):
    """`gh` を差し替え、`issue view` の本文と `issue edit` の結果を記録する。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    body = tmp_path / "body.md"
    written = tmp_path / "written.md"
    (bin_dir / "gh").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$2" = "view" ] || [ "$1" = "issue" ] && [ "$2" = "view" ]; then\n'
        f'  cat {body}\n'
        "  exit 0\n"
        "fi\n"
        'for i in "$@"; do\n'
        '  case "$prev" in --body-file) cp "$i" ' f"{written}" '; exit 0 ;; esac\n'
        '  prev=$i\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)
    return type("G", (), {"bin": bin_dir, "body": body, "written": written})


def run(fake_gh, *args):
    env = {**os.environ, "PATH": f"{fake_gh.bin}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60
    )


def test_the_section_is_appended_when_it_is_missing(fake_gh):
    """節が無ければ本文の末尾へ足す。"""
    fake_gh.body.write_text("# 課題\n\n本文\n", encoding="utf-8")
    out = run(fake_gh, "123", "設計", "--mode", "architecture")
    assert out.returncode == 0, out.stderr
    written = fake_gh.written.read_text(encoding="utf-8")
    assert written.startswith("# 課題\n\n本文\n")
    assert "## 進行" in written
    assert "モード: architecture" in written
    assert "- [x] 設計 —" in written
    assert "- [ ] 実装" in written


def test_nothing_outside_the_section_changes(fake_gh):
    """**節の外は書き換えない。** 前後に別の節があっても残る。"""
    fake_gh.body.write_text(
        "# 課題\n\n## 概要\n\nこれは残る\n\n## 進行\n\nモード: —\n\n- [ ] 設計\n\n"
        "## 受け入れ条件\n\n- [ ] 何か\n",
        encoding="utf-8",
    )
    run(fake_gh, "123", "設計")
    written = fake_gh.written.read_text(encoding="utf-8")
    assert "## 概要\n\nこれは残る" in written
    assert "## 受け入れ条件\n\n- [ ] 何か" in written
    assert "- [x] 設計 —" in written


def test_the_marks_already_there_are_kept(fake_gh):
    """済んだ工程の印と記録は残る。飛ばした工程は空欄のままになる。"""
    fake_gh.body.write_text(
        "## 進行\n\nモード: standard / 作業ツリー: `.worktrees/x`\n\n"
        "- [x] 作業場所の用意 — 2026-09-04 06:12\n- [ ] 要求と受け入れ条件\n",
        encoding="utf-8",
    )
    run(fake_gh, "123", "設計")
    written = fake_gh.written.read_text(encoding="utf-8")
    assert "- [x] 作業場所の用意 — 2026-09-04 06:12" in written
    assert "- [x] 設計 —" in written
    # 飛ばした工程はチェックの穴として見える
    assert "- [ ] 要求と受け入れ条件" in written
    # モードと作業ツリーは引数で渡さなくても残る
    assert "モード: standard / 作業ツリー: `.worktrees/x`" in written


def test_writing_the_same_stage_twice_changes_nothing(fake_gh):
    """同じ操作を 2 度行っても結果が変わらない。"""
    fake_gh.body.write_text("## 進行\n\nモード: —\n\n- [ ] 設計\n", encoding="utf-8")
    run(fake_gh, "123", "設計")
    first = fake_gh.written.read_text(encoding="utf-8")
    fake_gh.body.write_text(first, encoding="utf-8")
    fake_gh.written.unlink()
    out = run(fake_gh, "123", "設計")
    assert out.returncode == 0
    # 時刻が同じ分のあいだは書き込みが起きない
    assert not fake_gh.written.exists() or fake_gh.written.read_text(encoding="utf-8") == first


def test_an_unknown_stage_is_rejected(fake_gh):
    """工程表に無い工程名は呼び出し側の誤りとして 2 を返す。"""
    fake_gh.body.write_text("本文\n", encoding="utf-8")
    out = run(fake_gh, "123", "でたらめ")
    assert out.returncode == 2
    assert "工程表に無い" in out.stderr


def test_missing_gh_is_not_an_error(tmp_path):
    """`gh` が無ければ何もせず終了コード 0 で終わる。工程を止めない。

    `gh` だけを隠す。**PATH を空にすると `bash` も消える**ため、必要なコマンドへの
    symlink を張った隔離したディレクトリを使う。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("bash", "python3", "date", "mktemp", "cmp", "cat", "grep", "sed",
                 "dirname", "cd", "rm", "cp"):
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    out = subprocess.run(
        [str(bin_dir / "bash"), str(SCRIPT), "123", "設計"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": str(bin_dir)},
    )
    assert out.returncode == 0
    assert out.stdout == ""
