"""`waiting.md` の待ちの雛形を取り出し、テストで打てる形に差し替える補助（#1297 の決定 3）。

雛形の写しは持たない。`waiting.md` の `bash` の囲みのうち `until` を含むものを集め、
置き換えの目印（`<プラン>-state` / `<置き場所>`）と `bash -c` の最後の引数（上限の秒数）を差し替える。
形が変わったとき（囲みが 2 つでない・目印が無い）は例外で落ち、雛形の変化を知らせる。
"""

from __future__ import annotations

import re
from pathlib import Path

WAITING_MD = Path(__file__).resolve().parents[2] / "skills" / "development-workflow" / "references" / "waiting.md"

_FENCE = re.compile(r"^```bash\n(.*?)^```", re.S | re.M)
# bash -c の引数の並びの最後にある上限の秒数（`_ ... 3600;`）
_LIMIT = re.compile(r"(' _ [^;\n]*?) (\d+);")


def _templates() -> tuple[str, str]:
    blocks = [b for b in _FENCE.findall(WAITING_MD.read_text(encoding="utf-8")) if "until" in b]
    if len(blocks) != 2:
        raise AssertionError(f"waiting.md の until を含む bash の囲みが 2 つでない: {len(blocks)}")
    attention = [b for b in blocks if "<プラン>-state" in b]
    done = [b for b in blocks if "<置き場所>" in b]
    if len(attention) != 1 or len(done) != 1:
        raise AssertionError("waiting.md の雛形に置き換えの目印（<プラン>-state / <置き場所>）が見つからない")
    return attention[0], done[0]


def _with_limit(cmd: str, limit: int) -> str:
    out, k = _LIMIT.subn(lambda m: f"{m.group(1)} {limit};", cmd)
    if k != 1:
        raise AssertionError("雛形の bash -c の最後の引数（上限の秒数）が見つからない")
    return out


def attention_template(state_dir: str, limit: int) -> str:
    """attention と report.md を待つ雛形。状態ディレクトリと上限を差し替える。"""
    return _with_limit(_templates()[0].replace("<プラン>-state", state_dir), limit)


def done_template(place: str, limit: int) -> str:
    """完了マーカー `<置き場所>.done` を待つ雛形。置き場所と上限を差し替える。"""
    return _with_limit(_templates()[1].replace("<置き場所>", place), limit)
