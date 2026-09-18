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

import pytest

from workflow_helpers import GUARD, SKILL_DIR, STAGE_CHECK

SKILL = SKILL_DIR / "SKILL.md"
TRACKING = SKILL_DIR / "references/projects-tracking.md"
LOOKUP = SKILL_DIR / "references/scripts-lookup.md"
COMPLETENESS = SKILL_DIR / "references/stage-completeness.md"
MERGED = SKILL_DIR.parent / "merged/SKILL.md"
SKILLS_DIR = SKILL_DIR.parent
PROGRESS_TRACKING = SKILLS_DIR / "progress-tracking/SKILL.md"
RELEASE_VERIFICATION = SKILLS_DIR / "release-verification/SKILL.md"

# まとまりを閉じる手順を呼ぶ、終わりの工程の Skill（#623 の決定 8）。
CLOSING_CALLERS = ("release", "release-verification", "retrospective")

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


def test_the_skill_names_the_design_branch_prefix() -> None:
    """設計 Pull Request の見分けは head のブランチ名で行う（決定 4）。規約を本文へ書く。"""
    body = SKILL.read_text(encoding="utf-8")
    section = body.split("**ドキュメントレビューは")[1].split("\n## ")[0]

    assert "design/" in section


def test_the_reference_is_linked_from_the_skill() -> None:
    assert "references/stage-completeness.md" in SKILL.read_text(encoding="utf-8")
    assert COMPLETENESS.is_file()


def test_the_scripts_lookup_section_is_unchanged() -> None:
    """#221-6: 進行の記録を書く手順は変わらない。

    `$SCRIPTS` の解決は `scripts-lookup.md` へ移した（#282）。盤面の記録だけが使う値では
    ないためである。呼び方の側は `projects-tracking.md` に残る。
    """
    assert "## 候補の並び" in LOOKUP.read_text(encoding="utf-8")
    body = TRACKING.read_text(encoding="utf-8")
    assert 'bash "$SCRIPTS/projects-sync.sh" <issue番号> <キー> "<値>"' in body


def test_the_merged_report_carries_the_stage_report() -> None:
    """#221-2: 後片付けの完了報告に、スクリプトの出力として載る。"""
    body = MERGED.read_text(encoding="utf-8")

    assert "stage-check.sh" in body
    assert "report" in body


def test_the_closing_step_closes_issues_with_their_repository() -> None:
    """#229-2: 取り出した 2 つの値を `--repo` へ渡す書き方であること。

    閉じる手順は `merged` から `progress-tracking` の「まとまりを閉じる」へ移した
    （#623 の決定 7）。読む先だけを移し、確かめる書き方は変えない。
    """
    body = PROGRESS_TRACKING.read_text(encoding="utf-8")

    assert "gh issue close <番号> --repo <所有者>/<リポジトリ>" in body


def test_only_progress_tracking_closes_issues() -> None:
    """課題を閉じる手順を持つ `SKILL.md` は 1 つだけにする（#623 の C2 / C6 / C13）。

    3 つの終わりの工程（`release` / `release-verification` / `retrospective`）へ写しを
    置くと、どれを読んだかで閉じる時点が変わる。**正本は `progress-tracking` の
    「まとまりを閉じる」だけで、ほかはそこを指す。**

    `references/` の下は対象にしない。`issue-upkeep` の「やらない」と `out-of-scope` の
    起票先は、まとまりの工程とは別の契機で閉じる手順である。

    盤面の `Done` も同じ 1 か所に寄せる（C13）。工程の入口の進行の記録で `Done` を書くと、
    `Auto-close issue` が先に閉じて reopen の手段が報告から落ちる。
    """
    closes = sorted(
        path.relative_to(SKILLS_DIR).as_posix()
        for path in SKILLS_DIR.glob("*/SKILL.md")
        if "gh issue close" in path.read_text(encoding="utf-8")
    )
    assert closes == ["progress-tracking/SKILL.md"], closes

    done = sorted(
        path.relative_to(SKILLS_DIR).as_posix()
        for path in SKILLS_DIR.glob("*/SKILL.md")
        if 'status "Done"' in path.read_text(encoding="utf-8")
    )
    assert done == ["progress-tracking/SKILL.md"], done

    # 呼ぶ側は「まとまりを閉じる」を `issue-upkeep` より前に置く（C6）。後に置くと、
    # `issue-upkeep` の段 1 が読む「このまとまりで閉じた課題」がまだ閉じていない。
    for name in CLOSING_CALLERS:
        body = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        closing = body.find("まとまりを閉じる")
        upkeep = body.find("/ndf:issue-upkeep")
        assert closing >= 0, f"{name}: 「まとまりを閉じる」への参照が無い"
        assert upkeep >= 0, f"{name}: `issue-upkeep` の呼び出しが無い"
        assert closing < upkeep, f"{name}: 閉じる手順が `issue-upkeep` より後にある"


def test_the_gates_stay_two() -> None:
    """関門は 2 つのままで、工程の側が関門の外へ実行前確認を足さない（#561 の B5 / B6）。

    実行前確認の要否は `AUTHORING.md` の基準が決める。関門の節はその原則を指すだけで、
    基準の中身を写さない（決定 11。このファイルは 500 行の上限に達している）。
    """
    body = SKILL.read_text(encoding="utf-8")
    section = body.split("## 人手の承認を求める関門")[1].split("\n### ")[0]

    assert "**関門は 2 つで、増やさない。**" in section
    assert "関門の外で工程の側が実行前確認を足さない" in section
    assert "AUTHORING.md" in section

    rows = [line for line in section.splitlines() if line.startswith("| ") and " | " in line]
    # 見出しの行と区切りの行を除いた残りが関門そのものである。
    assert len(rows) - 2 == 2, rows


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


def test_the_record_reader_selects_the_latest_distribution_and_matching_release_test() -> None:
    """現状固定: 最後の配布と、その本番版に一致する最後のリリース後テストを選ぶ。"""
    record = """## 配布の記録

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
    selected, remainder = done.stdout.split("\n---last---\n", maxsplit=1)
    last, fields = remainder.split("\n---fields---\n", maxsplit=1)
    assert selected.strip() == """## リリース後テスト

対象の版: 10.15.0（2026-09-18 13:00）
合否: 合格（選ぶ記録）"""
    assert last.strip() == """## 配布の記録

段階: 本番（2026-09-18 10:00 に承認）
版: 10.14.0 → 10.15.0（MINOR: 新まとまり）
まとまり: PR #717 / #718"""
    assert fields.strip() == "本番（2026-09-18 10:00 に承認）|10.15.0|717\n718"


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

    # 結果の報告: before が OPEN で after が CLOSED なら `閉じた`。戻し方を載せる。
    report = closing_section("結果の報告")
    closed = [line for line in report.splitlines() if line.startswith("| `閉じた` |")]
    assert len(closed) == 1, report
    condition, carried = [cell.strip() for cell in closed[0].strip("|").split("|")][1:3]
    assert "`before` が OPEN" in condition and "`after` が CLOSED" in condition, condition
    assert "`gh issue reopen <番号> --repo <所有者>/<リポジトリ>`" in carried, carried


def release_verification_output_template() -> str:
    """`release-verification` の「出力物」節にある markdown 雛形を返す。

    節は `## 出力物` から次の実在の節見出しまで。**節の中の ```markdown``` の柵で
    囲まれた雛形だけを取り出す。** 柵の中の `## リリース後テスト` は雛形の一部であり、
    節の境目ではない。柵で切ると、その混同を避けられる。
    """
    body = RELEASE_VERIFICATION.read_text(encoding="utf-8")
    section = body.split("\n## 出力物\n", maxsplit=1)[1]
    found = re.search(r"^```markdown\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)
    assert found, f"出力物の markdown 雛形を読み取れない: {section[:200]}"
    return found.group(1)


def test_the_release_verification_template_carries_the_target_version_once() -> None:
    """現状固定: 雛形は `対象の版:` の行を 1 つ持つ。

    「まとまりを閉じる」の「配布の記録」の読み取りは、この行で本番の版のブロックを
    選ぶ。行が無い・複数あると、どの版を確かめたのかが決まらない。
    """
    template = release_verification_output_template()
    targets = [line for line in template.splitlines() if line.startswith("対象の版:")]

    assert len(targets) == 1, template
    assert "<配布した版>" in targets[0], targets[0]


def test_the_release_verification_template_starts_the_table_with_the_issue_column() -> None:
    """現状固定: 表の先頭の列が `課題` で、`結果` の列も持つ。

    課題ごとに閉じる判定を読むため、行がどの課題の受け入れ条件だったかを先頭の列で
    引く。結果の列が合否を持つ。どちらが欠けても課題別の閉じる判定へ渡せない。
    """
    template = release_verification_output_template()
    rows = [line for line in template.splitlines() if line.startswith("| ")]
    # 見出しの行・区切りの行・各データ行。
    assert len(rows) >= 3, rows

    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert header[0] == "課題", header
    assert header[-1] == "結果", header
    assert "受け入れ条件" in header, header


def test_the_release_verification_template_maps_each_condition_to_an_issue() -> None:
    """現状固定: 各受け入れ条件の行が、課題の列と結果の列を対応付けて読める。

    区切りの行を除いた各データ行で、先頭の課題の列が `#<番号>` の形を持ち、結果の列が
    合格・保留のいずれかの語を持つ。これがまとまりの課題別の閉じる判定へ渡す形である。
    """
    template = release_verification_output_template()
    rows = [line for line in template.splitlines() if line.startswith("| ")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    issue_at = header.index("課題")
    result_at = header.index("結果")

    # 見出しの行と区切りの行（各セルが `---`）を除いた残りがデータ行。
    def is_separator(row: str) -> bool:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        return all(set(cell) == {"-"} for cell in cells)

    data = [
        [cell.strip() for cell in row.strip("|").split("|")]
        for row in rows[1:]
        if not is_separator(row)
    ]
    assert data, rows

    issues = set()
    for cells in data:
        assert re.match(r"^#\d+$", cells[issue_at]), cells
        assert re.search(r"合格|保留", cells[result_at]), cells
        issues.add(cells[issue_at])

    # 雛形は複数の課題（#561 / #623）を、それぞれの受け入れ条件の行へ対応付けて示す。
    assert issues == {"#561", "#623"}, issues
