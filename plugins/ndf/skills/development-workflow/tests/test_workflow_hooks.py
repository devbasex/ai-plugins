"""hook の結線と、手順書の記述が実体と合っていることを固定する（#221 / #266）。

hook が実際に登録されることは会話の単位を起こさないと確かめられないため、実機確認へ
分けている。ここで固定するのは**書式と結線**である。書式が崩れると Skill の読み込み
そのものが失敗し、モード判定を失う。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from workflow_helpers import GUARD, SKILL_DIR, STAGE_CHECK

SKILL = SKILL_DIR / "SKILL.md"
SKILLS_DIR = SKILL_DIR.parent
PROGRESS_TRACKING = SKILLS_DIR / "progress-tracking/SKILL.md"

# プラグインの根。`${CLAUDE_PLUGIN_ROOT}` が指す先で、Skill の実体はこの下の
# `skills/<名前>/` にある。
PLUGIN_ROOT = SKILL_DIR.parents[1]

# **この hook が書いてよい `${...}` は `${CLAUDE_PLUGIN_ROOT}` だけである。**
#
# 実行ファイルの実体（Claude Code 2.1.259）を読んで確かめた。Skill の hook で
# `${CLAUDE_PLUGIN_DATA}` を書くと、次の文言を出して実行そのものを拒む。
#
#     Hook command references ${CLAUDE_PLUGIN_DATA} but only ${CLAUDE_PLUGIN_ROOT} is
#     available for skill hooks (${CLAUDE_PLUGIN_DATA} is plugin-only).
#
# **拒否の検査が見るのはこの 2 つだけである。** `${CLAUDE_PROJECT_DIR}` はブレースを
# 付けても捕捉されず、値にもなる（シェル形式では hook の環境変数として、`args` を持つ
# 形では実行ファイルの置き換えとして届く）。**それでも一覧へは入れない。** この hook が
# 起動する判定のスクリプトはプラグインの下にあり、主ディレクトリの位置に依存させない。
# シェルの環境変数として展開させたいときは、ブレースを付けずに `$CLAUDE_PROJECT_DIR` と
# 書く。**下の正規表現はブレース付きだけを拾うため、書き分けがそのまま意図の表明になる。**
#
# 一覧の外の変数は、シェル形式では**空へ展開されるだけで拒否も警告も出ない**。
# `${CLAUDE_SKILL_DIR}` は SKILL.md の本文では使えるため、hook へ書いても誤りに見えない。
# 発火しないことに気づく手がかりが無い（#304）。
ALLOWED_HOOK_VARIABLES = frozenset({"CLAUDE_PLUGIN_ROOT"})

# 実行ファイルが変数を取り出す正規表現と同じもの。
VARIABLE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")

# 空白を含むプラグインの根。**実在させない。** 見るのは語の切れ目だけである。
ROOT_WITH_SPACE = "/tmp/with space/ndf"


def frontmatter() -> str:
    body = SKILL.read_text(encoding="utf-8")
    found = re.match(r"\A---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    assert found, "frontmatter を読み取れない"
    return found.group(1)


def test_frontmatter_rejects_body_without_delimiters(tmp_path, monkeypatch) -> None:
    """現状固定: 境界記号が無い本文は既存の AssertionError で拒否する。"""
    skill = tmp_path / "SKILL.md"
    skill.write_text("# frontmatter の無い本文\n", encoding="utf-8")
    monkeypatch.setitem(globals(), "SKILL", skill)

    with pytest.raises(AssertionError, match="frontmatter を読み取れない"):
        frontmatter()


def hook_command() -> str:
    """frontmatter が登録する hook のコマンドを、YAML の引用を解いて返す。

    読み取れないこと自体を失敗として扱う。行が消えるだけで、コマンドを見る検査が
    素通りになる形にしない。

    **引用を解いてから返す。** この行は YAML の二重引用の並びで、経路を囲む引用符は
    エスケープされた文字として書かれている。解かずに返すと、後段がエスケープの記号を
    経路の一部として読む。二重引用の並びのエスケープの規則は JSON の文字列と同じもの
    であるため、外部のライブラリを増やさずに `json` で解ける。
    """
    found = re.search(r"^\s*command:\s*(\".+\")\s*$", frontmatter(), re.MULTILINE)
    assert found, f"hook のコマンドを読み取れない: {frontmatter()}"
    return json.loads(found.group(1))


def test_the_frontmatter_registers_a_pre_tool_use_hook() -> None:
    body = frontmatter()

    assert re.search(r"^hooks:\s*$", body, re.MULTILINE), body
    assert re.search(r"^  PreToolUse:\s*$", body, re.MULTILINE), body
    assert re.search(r'^    - matcher: "Bash"\s*$', body, re.MULTILINE), body


def test_the_hook_points_at_the_guard_in_this_skill() -> None:
    """`${CLAUDE_PLUGIN_ROOT}` はプラグインの根を指す。作業ディレクトリに依存しない。"""
    body = frontmatter()

    assert "${CLAUDE_PLUGIN_ROOT}/skills/development-workflow/scripts/workflow-guard.sh" in body


def test_the_hook_command_only_uses_the_plugin_root_variable() -> None:
    """一覧の外の変数は使わない。**空へ展開されるだけで、拒否も警告も出ない**（#304）。

    文字列の一致だけを見ると、同じ間違いが戻ったときに気づけない。`${CLAUDE_SKILL_DIR}`
    は SKILL.md の本文では使えるため、hook へ書いても誤りに見えない。

    `${CLAUDE_PROJECT_DIR}` も落とす。**値にはなるが、この hook を主ディレクトリの位置へ
    依存させない。** シェルの展開を意図して書くなら、ブレースを付けずに
    `$CLAUDE_PROJECT_DIR` と書く。この検査はブレース付きだけを拾う。
    """
    used = set(VARIABLE.findall(hook_command()))

    assert used, f"hook のコマンドが変数を持たない: {hook_command()}"
    forbidden = sorted(used - ALLOWED_HOOK_VARIABLES)
    assert not forbidden, (
        f"この hook が使ってよい変数の外を書いている: {forbidden}。"
        f"書けるのは {sorted(ALLOWED_HOOK_VARIABLES)} だけである"
    )


def test_the_hook_command_resolves_to_the_guard_under_the_plugin_root() -> None:
    """`${CLAUDE_PLUGIN_ROOT}` を実体の根へ置き換えると、判定のスクリプトへ届く。

    変数の名前だけを見ると、その先の道筋が誤っていても通る。**置き換えた結果を実体と
    突き合わせる。**

    **語の切り分けは `shlex` に任せる。** `str.split` は空白だけで切るため、この
    リポジトリを空白を含む位置へ置いた利用者の手元では、置き換えた経路が途中で切れて
    落ちる。`shlex` はシェルと同じ規則で切るため、引用で囲まれた経路が 1 語のまま残る。
    部分一致で確かめる手もあるが、それでは経路の後ろに別の語が続いていても通ってしまう。
    """
    resolved = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT))
    path = shlex.split(resolved)[-1]

    assert path == str(GUARD), f"判定のスクリプトを指していない: {path}"
    assert GUARD.is_file(), GUARD


def test_the_hook_command_keeps_a_plugin_root_with_a_space_in_one_word() -> None:
    """空白を含む位置へ導入されても、経路が 1 語のまま `bash` へ渡る（#307）。

    置き換えは実行ファイルによる文字列の差し替えで、その結果をシェルが読む。経路を
    引用で囲まないと、空白のところで語が切れて `bash` が別のファイルを探す。**実機で
    確かめたときは hook が拒否も案内も出さないまま素通りした。** 止めるはずの操作が
    通るため、気づく手がかりが無い。

    上の検査は実体の位置を使うため、空白を含まない環境では引用を外しても通る。ここは
    空白を含む位置を作って、引用そのものを見る。
    """
    resolved = hook_command().replace("${CLAUDE_PLUGIN_ROOT}", ROOT_WITH_SPACE)
    words = shlex.split(resolved)

    assert words == [
        "bash",
        f"{ROOT_WITH_SPACE}/skills/development-workflow/scripts/workflow-guard.sh",
    ], f"空白のところで語が切れている: {words}"


def test_the_hook_is_not_removed_after_the_first_run() -> None:
    """`once: true` を置かない。工程は 1 回の判定では終わらない。"""
    assert "once:" not in frontmatter()


def test_the_hook_block_is_nested_in_the_documented_order() -> None:
    """公式ドキュメントが示す入れ子（hooks → PreToolUse → matcher → hooks → command）。

    書式が崩れると Skill の読み込みそのものが失敗し、モード判定を失う。**外部の
    ライブラリに頼らず確かめる。** 読み飛ばされる検査は、崩れても気づけない。
    """
    lines = [line for line in frontmatter().splitlines() if line.strip()]
    start = lines.index("hooks:")
    block = lines[start : start + 7]

    assert block[1] == "  PreToolUse:"
    assert block[2] == '    - matcher: "Bash"'
    assert block[3] == "      hooks:"
    assert block[4] == "        - type: command"
    assert block[5].startswith("          command: ")
    assert block[6] == "          timeout: 10"


@pytest.mark.parametrize("script", [GUARD, STAGE_CHECK])
def test_the_scripts_are_executable_shell(script) -> None:
    assert script.is_file(), script
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


# 疑似の `gh`。呼び出しを 1 行ずつ控え、課題の状態をファイルで持つ。通信しない。
FAKE_GH = r"""#!/usr/bin/env bash
printf 'gh %s\n' "$*" >>"$FAKE_DIR/calls"
case "$1 $2" in
  "repo view") echo "$FAKE_REPO" ;;
  "pr view")
    case " $* " in
      *" body,comments "*) cat "$FAKE_DIR/record" ;;
      *) cat "$FAKE_DIR/pr-body" ;;
    esac ;;
  "issue view") cat "$FAKE_DIR/state-$3" ;;
  "issue close") echo CLOSED >"$FAKE_DIR/state-$3"; echo "Closed issue #$3" ;;
  *) exit 1 ;;
esac
"""

# 疑似の `projects-sync.sh`。盤面の自動化は閉じない（`gh issue close` が閉じる経路）。
FAKE_SYNC = """#!/usr/bin/env bash
printf 'projects-sync %s\\n' "$*" >>"$FAKE_DIR/calls"
"""

# 複数世代の配布記録。最後の配布と、その本番版に一致する最後のリリース後テストを
# 選ぶことを確かめるための入力。旧版の記録・同版の先行記録・異なる版（dev 接尾辞）を
# 混ぜ、末尾の「選ぶ記録」だけが選ばれることを示す。
MULTI_GENERATION_RECORD = """## 配布の記録

段階: 本番（2026-09-01 10:00 に承認）
版: 10.13.0 → 10.14.0（MINOR: 旧まとまり）
まとまり: PR #700

## リリース後テスト

対象の版: 10.14.0（2026-09-01 11:00）
合否: 合格（旧版）

## 配布の記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 新まとまり）
まとまり: PR #717 / #718

## リリース後テスト

対象の版: 10.15.0（2026-09-18 11:00）
合否: 合格（同版の先行記録）

## リリース後テスト

対象の版: 10.15.0-dev.1（2026-09-18 12:00）
合否: 合格（異なる版）

## リリース後テスト

対象の版: 10.15.0（2026-09-18 13:00）
合否: 合格（選ぶ記録）
"""

# 本番への配布の記録と、全条件が合格したリリース後テストの記録。
DISTRIBUTION_RECORD = """## 概要

版を上げる。

## 配布の記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 機能追加）
まとまり: PR #717

## リリース後テスト

対象の版: 10.15.0（2026-09-18 11:00）

| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |
| --- | --- | --- | --- | --- |
| #12 | 閉じる | 手順を通した | 11:05 | 合格 |
| #12 | 報告する | 報告を読んだ | 11:06 | 合格 |

合否: 合格
"""


def closing_section(heading: str) -> str:
    """「まとまりを閉じる」の下の `### <heading>` 節を返す。"""
    body = PROGRESS_TRACKING.read_text(encoding="utf-8")
    closing = body.split("\n## まとまりを閉じる\n")[1].split("\n## ")[0]
    return closing.split(f"\n### {heading}\n")[1].split("\n### ")[0]


def closing_bash(heading: str) -> str:
    found = re.findall(r"^```bash\n(.*?)^```$", closing_section(heading), re.MULTILINE | re.DOTALL)
    assert len(found) == 1, f"{heading} の bash が 1 つでない: {len(found)} 個"
    return found[0]


def run_record_reader(record: str) -> str:
    """「配布の記録」の読み取り手順を、記録を差し替えて実行し標準出力を返す。

    手順書の `record=$(gh pr view ...)` を環境変数 `RECORD` の読み取りへ置き換え、
    末尾に計測用の区切りで `selected` / `last` / `fields` を書き出す。
    """
    script = closing_bash("配布の記録")
    script = re.sub(
        r"^record=\$\(gh pr view .*\)$",
        'record="$RECORD"',
        script,
        count=1,
        flags=re.MULTILINE,
    )
    script += (
        '\nprintf "\\n---last---\\n%s\\n---fields---\\n%s|%s|%s\\n" '
        '"$last" "$stage" "$ver" "$bundle_prs"\n'
    )

    done = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "RECORD": record},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def parse_record_reader_output(stdout: str) -> tuple[str, str, str]:
    """計測用の区切りで区切られた出力を `(selected, last, fields)` の 3 値へ分解する。"""
    selected, remainder = stdout.split("\n---last---\n", maxsplit=1)
    last, fields = remainder.split("\n---fields---\n", maxsplit=1)
    return selected.strip(), last.strip(), fields.strip()


def test_the_record_reader_selects_the_latest_distribution_and_matching_release_test() -> None:
    """現状固定: 最後の配布と、その本番版に一致する最後のリリース後テストを選ぶ。"""
    selected, last, fields = parse_record_reader_output(
        run_record_reader(MULTI_GENERATION_RECORD)
    )

    assert selected == """## リリース後テスト

対象の版: 10.15.0（2026-09-18 13:00）
合否: 合格（選ぶ記録）"""
    assert last == """## 配布の記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 新まとまり）
まとまり: PR #717 / #718"""
    assert fields == "本番（2026-09-18 10:00 に承認）|10.15.0|717\n718"


def test_the_closing_step_closes_an_open_issue_after_the_board_is_done(tmp_path) -> None:
    """現状固定: 本番へ配布し全条件が合格した OPEN の課題を、盤面の Done の後で閉じる。

    手順書の 2 つのコード例（「配布の記録」の読み取りと「手順」）を、疑似の `gh` と
    `projects-sync.sh` へ向けてそのまま実行する。置き換えるのは `<...>` の差し込み口だけ。
    """
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "lib").symlink_to(PLUGIN_ROOT / "scripts/lib")
    for path, text in ((fake / "bin/gh", FAKE_GH), (scripts / "projects-sync.sh", FAKE_SYNC)):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    (fake / "record").write_text(DISTRIBUTION_RECORD, encoding="utf-8")
    (fake / "pr-body").write_text("Fixes devbasex/ai-plugins#12\n", encoding="utf-8")
    (fake / "state-12").write_text("OPEN\n", encoding="utf-8")

    script = closing_bash("配布の記録") + closing_bash("手順")
    for placeholder, value in {
        "<記録のPR番号>": "717",
        "<PR番号>": "$bundle_prs",
        "<所有者>/<リポジトリ>": "devbasex/ai-plugins",
        "<番号>": "12",
        "<マイルストーン>": "01 テスト",
        "<工程名>": "振り返り",
    }.items():
        script = script.replace(placeholder, value)
    script += '\nprintf "stage=%s\\nver=%s\\nbefore=%s\\nnow=%s\\nafter=%s\\n" "$stage" "$ver" "$before" "$now" "$after"\n'

    env = {
        "PATH": f"{fake / 'bin'}:{os.environ.get('PATH', '')}",
        "SCRIPTS": str(scripts),
        "FAKE_DIR": str(fake),
        "FAKE_REPO": "devbasex/ai-plugins",
        "LC_ALL": "C.UTF-8",
    }
    done = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    out = done.stdout.splitlines()

    # 閉じる条件: 本番への配布で、本番の版のリリース後テストの行がすべて合格。
    assert "stage=本番（2026-09-18 10:00 に承認）" in out, out
    assert "ver=10.15.0" in out, out
    rows = [line for line in out if line.startswith("| #12 ")]
    assert len(rows) == 2 and all(row.split("|")[5].strip().startswith("合格") for row in rows), out
    # まとまりの課題はリポジトリまで含めて取り出される。
    assert "devbasex/ai-plugins\t12" in out, out

    calls = (fake / "calls").read_text(encoding="utf-8").splitlines()
    views = [i for i, call in enumerate(calls) if call.startswith("gh issue view 12 ")]
    board = calls.index("projects-sync 12 status Done")
    close = next(i for i, call in enumerate(calls) if call.startswith("gh issue close 12 "))
    # (a) 状態を読む → (b) 盤面を Done → (c) 読み直す → 閉じる → (d) 読み直す。
    assert len(views) == 3, calls
    assert views[0] < board < views[1] < close < views[2], calls
    assert "--repo devbasex/ai-plugins" in calls[close], calls[close]
    assert "before=OPEN" in out and "now=OPEN" in out and "after=CLOSED" in out, out


def closing_fake(tmp_path, *, pr_body: str, state_12: str, record: str) -> tuple[Path, dict]:
    """「手順」のコード例を疑似の `gh` / `projects-sync.sh` へ向けて実行する土台を作る。

    返すのは `(呼び出しを控える先, 環境)` である。呼び出しの並びを見て、止まるはずの
    経路がその先のコマンドを呼んでいないことを確かめる。
    """
    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "lib").symlink_to(PLUGIN_ROOT / "scripts/lib")
    for path, text in ((fake / "bin/gh", FAKE_GH), (scripts / "projects-sync.sh", FAKE_SYNC)):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    (fake / "record").write_text(record, encoding="utf-8")
    (fake / "pr-body").write_text(pr_body, encoding="utf-8")
    if state_12 is not None:
        (fake / "state-12").write_text(state_12, encoding="utf-8")
    env = {
        "PATH": f"{fake / 'bin'}:{os.environ.get('PATH', '')}",
        "SCRIPTS": str(scripts),
        "FAKE_DIR": str(fake),
        "FAKE_REPO": "devbasex/ai-plugins",
        "LC_ALL": "C.UTF-8",
    }
    return fake, env


def test_the_closing_step_stops_when_the_bundle_list_is_empty(tmp_path) -> None:
    """まとまりの一覧が空なら、手順 2 の `gh pr view` を呼ばずに止まる。

    番号を省いた `gh pr view` は現在のブランチの Pull Request を選ぶ。空のまま進むと、
    **別のまとまりの課題を閉じうる**（#747 のレビュー指摘）。
    """
    fake, env = closing_fake(
        tmp_path, pr_body="Fixes devbasex/ai-plugins#12\n", state_12="OPEN\n",
        record="（配布の記録が無い）\n",
    )
    script = 'bundle_prs=""\n' + closing_bash("手順").replace("<PR番号>", "$bundle_prs")

    done = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 1, done.stdout + done.stderr
    assert "推測せず運用者に一覧を聞く" in done.stderr, done.stderr
    calls = (fake / "calls").read_text(encoding="utf-8") if (fake / "calls").exists() else ""
    assert "gh pr view" not in calls, calls
    assert "gh issue close" not in calls, calls


def test_the_closing_step_does_not_touch_the_board_when_the_first_read_fails(tmp_path) -> None:
    """(a) の状態を読めなければ、盤面も課題も変えない。

    読めないまま盤面を Done にすると、`Auto-close issue` が有効なリポジトリでは結果を
    4 つのどれにも分類できないまま課題が閉じる（#747 のレビュー指摘）。
    """
    fake, env = closing_fake(
        tmp_path, pr_body="Fixes devbasex/ai-plugins#12\n", state_12=None,
        record="（配布の記録が無い）\n",
    )
    # 手順 3 の (a)〜(d) だけを、条件を満たした 1 件に対して実行する。
    script = "for n in 12; do\n" + "\n".join(
        line for line in closing_bash("手順").splitlines()
        if line.startswith(("before=", "[ \"<所有者>", "now=", "[ \"$now\"", "after="))
    ) + "\ndone\nprintf 'end=%s\\nfailed=%s\\n' \"${after-未設定}\" \"${failed- }\""
    for placeholder, value in {
        "<所有者>/<リポジトリ>": "devbasex/ai-plugins",
        "<番号>": "12",
        "<マイルストーン>": "01 テスト",
        "<工程名>": "振り返り",
    }.items():
        script = script.replace(placeholder, value)
    script = 'RECORD_REPO=devbasex/ai-plugins\n' + script

    done = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )

    calls = (fake / "calls").read_text(encoding="utf-8") if (fake / "calls").exists() else ""
    assert "projects-sync" not in calls, calls
    assert "gh issue close" not in calls, calls
    assert "end=未設定" in done.stdout, done.stdout + done.stderr
    # 黙って次の課題へ飛ばさない。`失敗` が 1 件でもあれば `issue-upkeep` を呼ばずに
    # 止まるため、控えないとその規則が働かない。
    assert "failed= 12" in done.stdout, done.stdout + done.stderr
