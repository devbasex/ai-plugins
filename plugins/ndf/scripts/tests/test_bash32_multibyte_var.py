"""bash 3.2 の UTF-8 のロケールで `$VAR` の直後の多バイト文字が変数名に取り込まれる形を落とす（#1200）。

bash 3.2 は `"（$X）"` の `）` のバイトまで変数名として読み、`set -u` の下で
`X\\xef: unbound variable` で落ちる。`${X}` と書けば落ちない。shellcheck はこの形を検出しない。
"""
import re
import subprocess
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[3]
REPO = PLUGINS.parent
PATTERN = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")


def bash_files():
    """plugins/ 以下の追跡されている .sh と、bash の shebang を持つファイル。"""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", str(PLUGINS)], cwd=REPO, capture_output=True, check=True
    ).stdout
    for rel in filter(None, out.decode().split("\0")):
        p = REPO / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        first = text.split("\n", 1)[0]
        if p.suffix == ".sh" or (first.startswith("#!") and "bash" in first):
            yield p, text


def test_pattern_catches_the_bash32_form():
    """正規表現が #1200 の形に当たり、直した形に当たらないこと。"""
    assert PATTERN.search('echo "（$PREV_SHA）"')
    assert not PATTERN.search('echo "（${PREV_SHA}）"')
    assert not PATTERN.search('echo "$X を"')


def test_no_multibyte_right_after_bare_var():
    """`$VAR` の直後に ASCII 以外の文字を置かない（`${VAR}` と書く）。"""
    hits = []
    for p, text in bash_files():
        for no, line in enumerate(text.splitlines(), 1):
            for m in PATTERN.finditer(line):
                hits.append(f"{p.relative_to(REPO)}:{no}: {m.group()}")
    assert hits == []
