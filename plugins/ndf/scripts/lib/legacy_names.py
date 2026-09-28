"""旧名の対応表（#1407）。工程の単位「ミッション」を「スプリント」へ改名した後も、旧名で呼ばれたら新しい名前へ渡す。

旧名はこの表にだけ書く（#1407 の I2）。入口（`supervise.py`・`mvv-gate.py`・`project-mvv.py`・旧名のスクリプト）は
`rewrite_argv` / `forward` で表を引き、読み手（プランのキー・記録のキー・ブランチの頭・記録の行の見出し）は
`read_key` / `branch_prefixes` / `record_labels` で表を引く。旧名のファイルとキーは読むだけで、書くのは常に新しい名前である。

受け付けをやめる版（改名を載せた正式版の次の正式版）で `MODE` を `"refuse"` にする。呼び出しの 3 種類（引数・選択肢・
スクリプト）は新しい名前を示して終了コード 2 で止まる。読み取りの 2 種類（キー・頭と見出し）は `MODE` に関わらず読み、
受け付けをやめる課題で行ごと消す（#1407 の決定 3）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MODE = "forward"  # "forward"（受け付ける）か "refuse"（やめる）

# 引数と選択肢: 入口ごとに (旧名の語の並び, 新しい名前の語の並び)
ARGS: dict[str, tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]] = {
    "supervise.py": ((("new", "mission"), ("new", "sprint")), (("--mission",), ("--sprint",))),
    "mvv-gate.py": ((("--mission",), ("--sprint",)),),
    "project-mvv.py": ((("--mission",), ("--sprint",)), (("--kind", "mission"), ("--kind", "sprint"))),
}
# スクリプト: 旧名のファイル → 渡し先
SCRIPTS = {"mission-state.py": "sprint-state.py", "mission-close.py": "sprint-close.py", "bundle-close.py": "sprint-close.py"}
# キー: 新しいキー → 旧名のキー（プランの `スプリント状態`・記録の `sprint`）
KEYS = {"スプリント状態": "ミッション状態", "sprint": "mission", "sprint_mvv": "mission_mvv"}
# 頭: ブランチの頭（新しい名前 → 旧名）
BRANCH_PREFIXES = {"sprint/": "mission/"}
# 見出し: リリース記録の行（新しい名前 → 旧名の並び）
RECORD_LABELS = {"スプリント: ": ("ミッション: ", "まとまり: ")}

WHEN = "旧名の受け付けは、改名を載せた正式版の次の正式版でやめる"


def rename_notice(old: str, new: str) -> str:
    """旧名で呼ばれたときの案内の 1 行。"""
    return f"{old} は {new} へ改名した。{new} で呼ぶ（{WHEN}）"


def refusal(old: str, new: str) -> str:
    """受け付けをやめた後の断りの 1 行。"""
    return f"{old} の受け付けはやめた。{new} で呼ぶ"


def _tell(old: str, new: str, mode: str | None = None) -> None:
    """案内を標準エラーへ 1 行出す。やめた後なら断りを出して終了コード 2 で止まる。"""
    if (mode or MODE) == "refuse":
        print(refusal(old, new), file=sys.stderr)
        sys.exit(2)
    print(rename_notice(old, new), file=sys.stderr)


def rewrite_argv(entry: str, argv: list[str], mode: str | None = None) -> list[str]:
    """入口 `entry` の引数の旧名を新しい名前へ置き換えた並びを返す。置き換えるたびに案内を 1 行出す。

    `--opt=<値>` の形も置き換える。オプションの値（`--kind mission` の `mission`）は、直前の語がそのオプションのときだけ置き換える。"""
    out = list(argv)
    for old, new in ARGS.get(entry, ()):
        i = 0
        while i < len(out):
            n = len(old)
            if tuple(out[i : i + n]) == old:
                _tell(f"{entry} {' '.join(old)}", " ".join(new), mode)
                out[i : i + n] = new
                i += n
                continue
            if n == 1 and out[i].startswith(old[0] + "="):
                _tell(f"{entry} {old[0]}", new[0], mode)
                out[i] = new[0] + out[i][len(old[0]) :]
            elif n == 2 and out[i] == f"{old[0]}={old[1]}":
                _tell(f"{entry} {' '.join(old)}", " ".join(new), mode)
                out[i] = f"{new[0]}={new[1]}"
            i += 1
    return out


def forward(old_script: str, mode: str | None = None) -> None:
    """旧名のスクリプトから呼ぶ。案内を 1 行出し、同じ引数で新しいスクリプトを実行する（戻らない）。"""
    old = Path(old_script).resolve()
    new = old.parent / SCRIPTS[old.name]
    _tell(old.name, new.name, mode)
    os.execv(sys.executable, [sys.executable, str(new), *sys.argv[1:]])


def read_key(d: dict, key: str, default=None):
    """新しいキーを読み、無ければ旧名のキーを読む。どちらも書き換えない。"""
    if key in d:
        return d[key]
    old = KEYS.get(key)
    return d.get(old, default) if old else default


def branch_prefixes(new: str) -> tuple[str, ...]:
    """ブランチの頭。新しい名前を先に、旧名を後に並べる。"""
    old = BRANCH_PREFIXES.get(new)
    return (new, old) if old else (new,)


def record_labels(new: str = "スプリント: ") -> tuple[str, ...]:
    """リリース記録の行の見出し。新しい名前を先に、旧名を後に並べる。"""
    return (new, *RECORD_LABELS.get(new, ()))
