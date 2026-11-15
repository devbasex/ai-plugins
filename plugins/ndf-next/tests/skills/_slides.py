"""スライドの生成スクリプト（`skills/release/assets/slides`）を node で動かす補助。

テストは node の小さなプログラムに対象のモジュールを読ませ、結果を JSON で標準出力へ書かせて読む。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ndf import identity

SLIDES = identity.ROOT / "skills" / "release" / "assets" / "slides"
DECK_FRAME = SLIDES / "lib" / "deck-frame.js"
SCRIPT_TIME = SLIDES / "lib" / "script-time.js"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node が無い")

# 呼び出しを記録するだけの pptxgenjs。`createDeck` の baseDir の node_modules に置くと、本物より先に解決される
FAKE_PPTX = """
class Slide {
  constructor(log) { this.log = log; this.calls = []; this.notes = null; this.background = null; }
  addText(t, o) { this.calls.push(["text", t, o]); }
  addShape(k, o) { this.calls.push(["shape", k, o]); }
  addImage(o) { this.calls.push(["image", o]); }
  addTable(r, o) { this.calls.push(["table", r, o]); }
  addNotes(n) { this.notes = n; }
}
class PptxGenJS {
  constructor() { this.slides = []; this.ShapeType = { rect: "rect", line: "line" }; this.written = null; }
  addSlide() { const s = new Slide(); this.slides.push(s); return s; }
  async writeFile(o) { this.written = o; return o.fileName; }
}
module.exports = PptxGenJS;
"""


def fake_pptx(base: Path) -> Path:
    pkg = base / "node_modules" / "pptxgenjs"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "index.js").write_text(FAKE_PPTX, encoding="utf-8")
    (pkg / "package.json").write_text('{"name": "pptxgenjs", "main": "index.js"}', encoding="utf-8")
    return base


def node(code: str, *args: str, cwd: Path | None = None) -> object:
    """`code` を node で走らせ、標準出力の JSON を返す。`code` は `out(値)` で結果を書く。"""
    prog = f"const out = (v) => process.stdout.write(JSON.stringify(v));\n(async () => {{\n{code}\n}})().catch((e) => {{ console.error(e.stack); process.exit(99); }});"
    r = subprocess.run([NODE, "-e", prog, "--", *args], capture_output=True, text=True, cwd=cwd, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def raises(expr: str) -> str:
    """`expr` を評価して投げた DeckError を `{message, code}` で返す node の式。"""
    return f"(() => {{ try {{ {expr}; return null; }} catch (e) {{ return {{ message: e.message, code: e.code, deck: e instanceof m.DeckError }}; }} }})()"
