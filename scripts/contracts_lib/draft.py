"""拾った事実からエントリポイントの項目の下書きを作り、呼び手を探す。"""

from __future__ import annotations

from pathlib import Path

from contracts_lib.model import args_of

# 終了コードの共通の意味（正本は plugins/ndf/scripts/lib/README.md の「結果 JSON の形」の表）
EXIT_MEANINGS = {
    "0": "成功（status ok）",
    "1": "違反があった・手順が失敗した（status stopped）",
    "2": "読めない・呼び出しの誤り（status stopped）",
    "3": "前提が無い、またはスクリプトが定めた正常な否定の結果（status stopped）",
    "4": "PR がベースブランチと衝突している（status stopped）",
    "6": "欠けが残る（status stopped・gaps あり）",
    "10": "承認ゲート（status gate）",
    "69": "依存の欠け（deps.require が外部パッケージを用意できない）",
    "75": "CI の基盤待ち（後で打ち直せば通りうる）",
}
CALLER_DIRS = ("plugins/ndf", ".github/workflows", "scripts", "CLAUDE.md", "AGENTS.md")
CALLER_SKIP = ("/tests/", "/experimental/", "plugins/ndf/contracts/", "scripts/contract-allow/", "scripts/script-structure-allow/")
CALLER_SUFFIX = (".md", ".py", ".sh", ".json", ".yml", ".yaml", ".toml")
MAX_CALLERS = 5


def exit_table(exits: dict[str, str]) -> dict[str, str]:
    out = {}
    for code in sorted(exits, key=int):
        if code in EXIT_MEANINGS:
            out[code] = EXIT_MEANINGS[code]
        elif 10 <= int(code) <= 19:
            out[code] = "承認ゲート（status gate）"
        elif 20 <= int(code) <= 29:
            out[code] = "LLM の判断待ち（status gate）"
        elif exits[code]:
            out[code] = f"定数 {exits[code]} が示す結果"
        else:
            return {"unknown": f"終了コード {code} の意味を読み取れない（定数でない数値）"}
    return out


class Callers:
    """エントリポイントの名前を含むファイル（テスト・契約の一覧・例外リストを除く）を探す。"""

    def __init__(self, root: Path):
        self.texts: dict[str, str] = {}
        for d in CALLER_DIRS:
            p = root / d
            files = [p] if p.is_file() else (sorted(p.rglob("*")) if p.is_dir() else [])
            for f in files:
                rel = f.relative_to(root).as_posix()
                if not f.is_file() or not rel.endswith(CALLER_SUFFIX) or any(s in "/" + rel for s in CALLER_SKIP):
                    continue
                self.texts[rel] = f.read_text(errors="ignore")

    def of(self, path: str) -> list[str]:
        name = Path(path).name
        return sorted(rel for rel, t in self.texts.items() if rel != path and name in t)

    def contains(self, rel: str, path: str) -> bool:
        return Path(path).name in self.texts.get(rel, "")


def draft(path: str, facts: dict, callers: list[str], hook: bool) -> dict:
    """項目の下書き。呼び手が無ければ `external` に補う理由の印を置く。"""
    args = (
        {"unparsed": "sys.argv を手で読む（柱 P8 で argparse へ移す）"}
        if facts["argv_manual"]
        else {"unparsed": "副命令か引数を受けるパーサーを静的に決められない"}
        if facts.get("args_unresolved")
        else args_of(facts["options"], facts["positionals"])
    )
    settings: dict[str, set] = {}
    for file, key in facts["setting_keys"]:
        settings.setdefault(file, set()).add(key)
    item = {
        "version": 1,
        "path": path,
        "summary": facts["summary"] or Path(path).name,
        "args": args,
        "env": sorted(facts["env"]),
        "settings": [{"file": f, "keys": sorted(k)} for f, k in sorted(settings.items())],
        "stdin": "hook-json" if hook else ("text" if facts["stdin"] else "none"),
        "stdout": facts["stdout"],
        "writes": sorted(facts["writes"]),
        "reads": [],
        "exit": exit_table(facts["exits"]),
    }
    if callers:
        item["callers"] = callers[:MAX_CALLERS]
    else:
        item["callers"] = []
    return item
