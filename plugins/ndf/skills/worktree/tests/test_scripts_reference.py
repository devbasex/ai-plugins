"""手順書が使う変数と、`$SCRIPTS` の解決手順の対応を検査する（#193）。

`worktree` の手順書は、プラグイン配下のスクリプトを変数経由で呼ぶ。その変数を決める
手順は `development-workflow/references/projects-tracking.md` の「`$SCRIPTS` を決める」
節にしかない。**このテストは解決手順の写しを持たず、その節の bash を読み出して実行する。**
写しを持つと、写しだけが正しくて配布された手順が外れている状態を作れてしまう。

外部への通信は行わない。配置は `tmp_path` の上に作る。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_DIR.parent
LOOKUP_REFERENCE = (
    SKILLS_ROOT / "development-workflow" / "references" / "projects-tracking.md"
)
LOOKUP_HEADING = "### `$SCRIPTS` を決める"
# Claude Code が SKILL.md の中で置き換える語。テストでも同じ置き換えを行う。
PLUGIN_ROOT_TOKEN = "'${CLAUDE_PLUGIN_ROOT}'"

# 手順書。SKILL.md と参照の 3 本で 1 組になる。
DOCUMENTS = [SKILL_DIR / "SKILL.md"] + sorted((SKILL_DIR / "references").glob("*.md"))
RECEIVED_HEADING = "## この文書が受け取る値"

# 走査する言語。手順書は実行例を console でも書く。
CODE_BLOCK = re.compile(r"^```(?:bash|console)\n(.*?)^```$", re.S | re.M)
# 変数の使用。`$NAME` と `${NAME...}` の両方を拾う。
VARIABLE_USE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# 代入。`NAME=` / `export NAME=` / `for NAME in` / `read NAME` を拾う。
VARIABLE_ASSIGN = re.compile(
    r"(?:^|[;&|(]\s*|\b(?:export|local|declare|readonly)\s+)([A-Za-z_][A-Za-z0-9_]*)=",
    re.M,
)
VARIABLE_FOR = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b")
VARIABLE_READ = re.compile(r"\bread\s+(?:-r\s+)?([A-Za-z_][A-Za-z0-9_]*)")

# 位置引数と特殊変数。`$` の直後が数字か、変数名に使えない 1 文字である形を指す。
SPECIAL_VARIABLE = re.compile(r"\$(?:[0-9]|[?@*#$!\-_])")
# 環境が与える変数。決定 7 のとおり、`plugins/ndf/skills/` の実測で出た 4 つに限る。
ENVIRONMENT_VARIABLES = frozenset({"HOME", "PWD", "USER", "EDITOR"})

# 手順書が文書の外から受け取る変数。表の宣言と突き合わせる。
DECLARED = {
    "SKILL.md": {"SCRIPTS"},
    "declaration.md": {"SCRIPTS", "main_dir"},
    "local-environment.md": {"SCRIPTS", "APP_SERVICE", "SRC_TARGET"},
    "test-execution.md": {"SCRIPTS", "WT"},
}


# --- 解決手順を読み出して実行する -------------------------------------------------


def lookup_snippet() -> str:
    """「`$SCRIPTS` を決める」節の bash のコードブロックを取り出す。"""
    text = LOOKUP_REFERENCE.read_text(encoding="utf-8")
    head = text.index(LOOKUP_HEADING)
    block = re.search(r"^```bash\n(.*?)^```$", text[head:], re.S | re.M)
    assert block is not None, f"{LOOKUP_REFERENCE} の「{LOOKUP_HEADING}」に bash が無い"
    return block.group(1)


def resolve(cwd: Path, home: Path, plugin_root: Path | None = None) -> str:
    """解決手順を実行し、決まった `$SCRIPTS` を返す。空文字は「見つからなかった」。"""
    snippet = lookup_snippet()
    if plugin_root is not None:
        snippet = snippet.replace(PLUGIN_ROOT_TOKEN, f"'{plugin_root}'", 1)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["HOME"] = str(home)
    got = subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet}\nprintf "%s\\n" "$SCRIPTS"\n'],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def make_plugin(root: Path) -> Path:
    """プラグインの配布物を作る。`worktree` の手順が呼ぶスクリプトを持つ。"""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("projects-sync.sh", "worktree-setup.sh"):
        (root / "scripts" / name).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    for name in ("development-workflow", "worktree"):
        skill = root / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


def link_kiro(project: Path, plugin: Path) -> Path:
    """Kiro CLI のインストーラと同じ配置を作る。"""
    skills = project / ".kiro" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    for src in sorted((plugin / "skills").iterdir()):
        (skills / src.name).symlink_to(src, target_is_directory=True)
    return project


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """手順が実ユーザの導入物を拾わないよう、HOME を空のディレクトリにする。"""
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def elsewhere(tmp_path: Path) -> Path:
    """どの配置とも関係のない作業ディレクトリ。"""
    d = tmp_path / "elsewhere"
    d.mkdir()
    return d


# --- 手順書を読む ---------------------------------------------------------------


def code_blocks(text: str) -> list[str]:
    """bash と console のコードブロックの本文を返す。"""
    return CODE_BLOCK.findall(text)


def used_variables(text: str) -> set[str]:
    """コードブロックが使う変数の名前を返す。位置引数と特殊変数は含めない。"""
    used: set[str] = set()
    for body in code_blocks(text):
        used |= set(VARIABLE_USE.findall(SPECIAL_VARIABLE.sub("", body)))
    return used - ENVIRONMENT_VARIABLES


def assigned_variables(text: str) -> set[str]:
    """コードブロックの中で代入される変数の名前を返す。"""
    assigned: set[str] = set()
    for body in code_blocks(text):
        assigned |= set(VARIABLE_ASSIGN.findall(body))
        assigned |= set(VARIABLE_FOR.findall(body))
        assigned |= set(VARIABLE_READ.findall(body))
    return assigned


def received_table(path: Path) -> tuple[set[str], int]:
    """「この文書が受け取る値」の表の変数名と、表の終わった行の番号を返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        head = lines.index(RECEIVED_HEADING)
    except ValueError:  # pragma: no cover - 失敗時の案内
        pytest.fail(f"{path} に「{RECEIVED_HEADING}」が無い")
    names: set[str] = set()
    index = head + 1
    started = False
    while index < len(lines):
        line = lines[index]
        if line.startswith("|"):
            started = True
            cell = line.split("|")[1].strip()
            found = re.fullmatch(r"`\$([A-Za-z_][A-Za-z0-9_]*)`", cell)
            if found is not None:
                names.add(found.group(1))
        elif started:
            break
        index += 1
    assert started, f"{path} の「{RECEIVED_HEADING}」に表が無い"
    return names, index


def next_content_line(path: Path, index: int) -> str:
    """指定の行から後ろで、最初の空でない行を返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[index:]:
        if line.strip():
            return line
    pytest.fail(f"{path} の表の後ろに本文が無い")


# --- 条件 1: 代入の無い変数名が残っていない ---------------------------------------


def test_ndf_scripts_is_gone() -> None:
    """`NDF_SCRIPTS` はどこにも代入されない。手順書から消えていることを確かめる。"""
    remains = {
        path.relative_to(SKILL_DIR).as_posix()
        for path in SKILL_DIR.rglob("*.md")
        if "NDF_SCRIPTS" in path.read_text(encoding="utf-8")
    }
    assert remains == set(), f"NDF_SCRIPTS が残っている: {sorted(remains)}"


# --- 条件 2: 未定義の変数を含むコマンドが無い -------------------------------------


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_no_undefined_variable(path: Path) -> None:
    """使う変数は、同じ文書で代入されるか、受け取る値の表に載っている。"""
    text = path.read_text(encoding="utf-8")
    declared, _ = received_table(path)
    undefined = used_variables(text) - assigned_variables(text) - declared
    assert undefined == set(), f"{path.name}: 出所の無い変数 {sorted(undefined)}"


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_received_table_declares_scripts(path: Path) -> None:
    """4 文書すべてが、受け取る値として `$SCRIPTS` を宣言している。"""
    declared, _ = received_table(path)
    assert declared == DECLARED[path.name]
    assert "SCRIPTS" in declared


def test_environment_variables_are_excluded_by_name() -> None:
    """環境が与える変数の一覧は、決定 7 の実測で出た 4 つに限る。"""
    assert set(ENVIRONMENT_VARIABLES) == {"HOME", "PWD", "USER", "EDITOR"}


def test_special_variables_are_not_treated_as_undefined() -> None:
    """`$?` のような特殊変数は、代入も宣言も無くても失敗にしない。"""
    body = "```bash\nbash run.sh; echo $?\nls \"$1\" \"$@\"\n```\n"
    assert used_variables(body) == set()


def test_environment_variable_is_not_treated_as_undefined() -> None:
    """一覧に載せた環境の変数も使用から外れる。"""
    assert used_variables('```bash\nls "$HOME"\n```\n') == set()


# --- 条件 3: 解決手順は 1 本しかない ----------------------------------------------


def test_lookup_procedure_is_the_only_one() -> None:
    """「`$SCRIPTS` を決める」の見出しを持つ手順書は 1 つだけである。"""
    owners = [
        path.relative_to(SKILLS_ROOT).as_posix()
        for path in sorted(SKILLS_ROOT.rglob("*.md"))
        if LOOKUP_HEADING in path.read_text(encoding="utf-8")
    ]
    assert owners == [
        "development-workflow/references/projects-tracking.md"
    ], f"手順が 1 本ではない: {owners}"


# --- 条件 4: 4 ランタイムの配置で解決できる ---------------------------------------


def test_claude_code_layout_resolves_worktree_setup(tmp_path, elsewhere, home) -> None:
    """Claude Code: `${CLAUDE_PLUGIN_ROOT}` が絶対パスへ置き換わる。"""
    plugin = make_plugin(tmp_path / "claude" / "cache" / "ai-plugins" / "ndf" / "9.8.0")
    scripts = resolve(elsewhere, home, plugin_root=plugin)
    assert Path(scripts, "worktree-setup.sh").is_file()


def test_kiro_layout_resolves_worktree_setup(tmp_path, home) -> None:
    """Kiro CLI: `.kiro/skills/<Skill名>` の symlink からプラグインへ戻る。"""
    plugin = make_plugin(tmp_path / "kiro-plugin" / "plugins" / "ndf")
    project = link_kiro(tmp_path / "project", plugin)
    scripts = resolve(project, home)
    assert Path(scripts, "worktree-setup.sh").is_file()


def test_codex_layout_resolves_worktree_setup(elsewhere, home) -> None:
    """Codex: マーケットプレイスのスナップショットの下から見つける。"""
    make_plugin(home / ".codex" / ".tmp" / "marketplaces" / "ai-plugins" / "plugins" / "ndf")
    scripts = resolve(elsewhere, home)
    assert Path(scripts, "worktree-setup.sh").is_file()


def test_agy_layout_resolves_worktree_setup(elsewhere, home) -> None:
    """agy: `agy plugin install` が複製した固定の位置から見つける。

    候補を足したのに片方のテストしか直さないと、こちらが前のランタイムの数のまま通り続ける
    （`00-overview.md` の「`worktree/tests/` は 3 担当が触る」の例外）。
    """
    make_plugin(home / ".gemini" / "config" / "plugins" / "ndf")
    scripts = resolve(elsewhere, home)
    assert Path(scripts, "worktree-setup.sh").is_file()


# --- 条件 5: 解決できないときは止まる ---------------------------------------------


def guard_line() -> str:
    """SKILL.md の手順 0 から、解決できないときに止める行を取り出す。"""
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for body in code_blocks(text):
        for line in body.splitlines():
            if "SCRIPTS:-" in line:
                return line
    pytest.fail("SKILL.md に `${SCRIPTS:-}` を見る行が無い")


def test_skill_stops_when_scripts_is_unresolved() -> None:
    """`$SCRIPTS` が決まっていなければ、案内を出して終了コード 1 で止まる。"""
    got = subprocess.run(
        ["bash", "-c", f"set -u\n{guard_line()}\necho ここへは来ない\n"],
        capture_output=True, text=True,
    )
    assert got.returncode == 1, got.stdout
    assert got.stderr.strip() != "", "案内が標準エラーへ出ていない"
    assert "ここへは来ない" not in got.stdout


def test_lookup_leaves_empty_value_when_nothing_is_found(elsewhere, home) -> None:
    """盤面への記録は従来どおり飛ばす。解決手順は空の値を残し、止めない。"""
    assert resolve(elsewhere, home) == ""


# --- 条件 6: まとめる推奨が表の直後にある -----------------------------------------


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_bundling_is_recommended_after_the_table(path: Path) -> None:
    """受け取る値の表の直後に、1 つの bash ブロックへまとめる推奨がある。"""
    _, end = received_table(path)
    line = next_content_line(path, end)
    assert "1 つの bash ブロック" in line and "まとめる" in line, line
