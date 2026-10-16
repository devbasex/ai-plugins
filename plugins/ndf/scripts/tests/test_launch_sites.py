"""LLM の CLI を起動の共有処理の外で起動する箇所を落とす（#1953 の受け入れ条件 10・決定 2）。

NDF が起動した LLM の呼び出しは、起動の共有処理（`launch-cli.sh`・`call_claude`・`auth.py` のモデルの確認・
`jev._post`）が使用量の帳簿へ 1 行を書く。起動の経路を足すときに書き忘れないよう、`plugins/ndf/` の
`scripts/`・`skills/`・`hooks/` のテスト以外のコードから、LLM を呼ぶ CLI の起動（`claude -p`・`codex exec`・
`kiro-cli chat`・`agy … -p`・`claude_agent_sdk` の `query`）を探し、共有処理と除外の一覧の外にあれば落とす。

LLM を呼ばない起動（`claude plugin`・`claude auth`・`codex login`・`agy models`・版の確認）は見つける規則に
当てはまらない。当てはまるが LLM を起動しないものは `EXCLUDED` に理由を添えて並べる。実験版の置き場
（`scripts` の下の `experimental`。利用者が呼んだときだけ働く試行で、本体ではない）は見ない。本体へ移すときに
起動の共有処理を通す。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
ROOTS = ("scripts", "skills", "hooks")

# 起動の共有処理（ここでの起動は帳簿へ書く。jev は HTTP の要求で、CLI の起動ではない）
SHARED = {
    "scripts/lib/launch-cli.sh": "CLI を背景で起動し、包みのサブシェルが帳簿へ書く",
    "scripts/supervise_lib/claude.py": "call_claude が claude -p を起動し、帳簿へ書く",
    "scripts/lib/auth.py": "参加者の確認のモデルの呼び出しを record_probe で書く",
}
# 規則に当てはまるが、帳簿へ書く起動ではないもの
EXCLUDED = {
    "scripts/lib/wait_notice.py": "利用者へ示す再開のコマンドの文字列（kiro-cli chat --resume）。起動しない",
}

CLIS = {"claude": ("-p", "--print"), "codex": ("exec", "e"), "kiro-cli": ("chat",), "agy": ("-p", "--prompt")}
LAUNCH = (
    r"\s*(?:(?:nohup|exec|command|then|do|else)\s+)*(?:\w+=\S*\s+)*"
    r"(?:claude(?:\s+\S+)*?\s+(?:-p|--print)\b|codex\s+(?:exec|e)\b|kiro-cli\s+chat\b|agy(?:\s+\S+)*?\s+-p(?:=|\b))"
)
# コマンドの位置（行の頭・区切りの後）。シェルの行の引用符の中（echo の文など）は起動ではない
SHELL_LAUNCH = re.compile(r"(?:^|[;&|({`]|\$\()" + LAUNCH)
# hook の command は `sh -c '…'` の引用符の中も起動として見る
QUOTED_LAUNCH = re.compile(r"(?:^|[;&|({`'\"]|\$\()" + LAUNCH)


def _is_launch_list(items: list) -> bool:
    words = [x.value for x in items if isinstance(x, ast.Constant) and isinstance(x.value, str)]
    if not words or not isinstance(items[0], ast.Constant):
        return False
    head = words[0]
    if head == "-p" and "--output-format" in words:  # 起動のコマンドを後から前へ足す claude の引数（minimal_args など）
        return True
    marks = CLIS.get(head)
    return bool(marks) and any(w in marks or (head == "agy" and w.startswith("-p=")) for w in words[1:])


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            ids.add(id(body[0].value))
    return ids


def python_sites(text: str) -> list[int]:
    """Python の起動の箇所（行番号）。コマンドの配列・シェルへ渡す文字列・claude_agent_sdk の query の import。"""
    tree = ast.parse(text)
    docs = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and _is_launch_list(node.elts):
            found.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if SHELL_LAUNCH.search(node.value):
                found.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("claude_agent_sdk"):
            if any(a.name == "query" for a in node.names):
                found.append(node.lineno)
    return sorted(set(found))


def shell_sites(text: str) -> list[int]:
    """シェルの起動の箇所（行番号）。コメントの行を除き、行の続き（`\\`）をつないで見る。"""
    found, buf, start = [], "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
            if line.lstrip().startswith("#"):
                continue
        buf += line[:-1] + " " if line.endswith("\\") else line
        if line.endswith("\\"):
            continue
        if SHELL_LAUNCH.search(buf):
            found.append(start)
        buf = ""
    return found


def json_sites(text: str) -> list[int]:
    """hook の設定の command の起動。"""
    out = []

    def walk(o) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "command" and isinstance(v, str) and QUOTED_LAUNCH.search(v):
                    out.append(1)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(text))
    return out


def sites(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return python_sites(text)
    if path.suffix == ".json":
        return json_sites(text) if path.parent.name == "hooks" else []
    return shell_sites(text)


def code_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(PLUGIN), "ls-files", "--", *ROOTS], capture_output=True, text=True, check=True).stdout
    keep = []
    for rel in out.splitlines():
        p = Path(rel)
        if p.suffix not in (".py", ".sh", ".json") or "tests" in p.parts or "test" in p.parts or p.name.startswith("test_"):
            continue
        if p.parts[:2] == ("scripts", "experimental"):
            continue
        if (PLUGIN / rel).is_file():
            keep.append(rel)
    return keep


def test_no_llm_launch_outside_the_shared_launchers() -> None:
    bad = {}
    for rel in code_files():
        if rel in SHARED or rel in EXCLUDED:
            continue
        found = sites(PLUGIN / rel)
        if found:
            bad[rel] = found
    assert not bad, f"起動の共有処理（{', '.join(SHARED)}）を通さずに LLM の CLI を起動している: {bad}"


def test_the_lists_point_at_real_launches() -> None:
    """共有処理と除外の一覧の各項目に、規則が見つける起動が 1 つ以上ある（古くなった項目を残さない）。"""
    empty = [rel for rel in [*SHARED, *EXCLUDED] if not (PLUGIN / rel).is_file() or not sites(PLUGIN / rel)]
    assert not empty


def test_an_added_launch_is_found(tmp_path) -> None:
    """起動を足したら見つかる（Python の配列・シェルへ渡す文字列・シェルの行・続きの行・hook の command）。"""
    py = 'import subprocess\nsubprocess.run(["codex", "exec", "-C", "."], input="x")\nsubprocess.run("claude -p --output-format json", shell=True)\n'
    assert python_sites(py) == [2, 3]
    sh = '# claude -p はコメント\necho "claude -p を呼ぶ"\nnohup kiro-cli chat --no-interactive < p > o &\nagy --output-format json \\\n  -p="$(cat p)"\n'
    assert shell_sites(sh) == [3, 4]
    hook = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "sh -c 'claude -p hi'"}]}]}}
    assert json_sites(json.dumps(hook)) == [1]


def test_non_llm_commands_are_not_launches() -> None:
    py = '"""claude -p を呼ぶ説明"""\nA = ("claude", "auth", "status")\nB = ("agy", "models")\nC = ["codex", "login", "status"]\nD = ["claude", "plugin", "list"]\n'
    assert python_sites(py) == []
    assert shell_sites("claude plugin marketplace update ai-plugins\ncodex --version\n") == []
