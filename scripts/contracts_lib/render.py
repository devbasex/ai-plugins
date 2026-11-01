"""契約の一覧の逆引き（`contracts/index.md`）を項目から作る（設計の決定 6）。"""

from __future__ import annotations

from contracts_lib.model import Contracts

HEADER = """# NDF の契約の一覧: 読み手と書き手の逆引き

この文書は `python3 scripts/check-ndf-contracts.py --render` が `plugins/ndf/contracts/` の項目から生成する。
手で直さず、項目（`entrypoints/`・`files/`・`settings/`・`env/`）を直して生成し直す。
"""


def _cell(xs) -> str:
    return "<br>".join(f"`{x}`" for x in xs) if xs else "—"


def render(c: Contracts) -> str:
    lines = [HEADER]
    lines += [
        "## 設定（`.ndf/`）",
        "",
        "| 設定 | 置き場 | 範囲 | 読み手の関数 | 読むエントリポイント（キー） |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, s in sorted(c.settings.items()):
        users = [
            f"{p}（{', '.join(x['keys'])}）" if x["keys"] else p
            for p, e in sorted(c.entrypoints.items())
            for x in e["settings"]
            if x["file"] == name
        ]
        lines.append(f"| `{name}` | {s['place']} | {s['scope']} | {_cell(s['readers'])} | {_cell(users)} |")
    lines += [
        "",
        "## 受け渡しのファイル",
        "",
        "| 名前 | 型 | 種類 | 置き場 | 書くエントリポイント | 読むエントリポイント・Skill |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, f in sorted(c.files.items()):
        writers = [p for p, e in sorted(c.entrypoints.items()) if name in e["writes"]]
        readers = [p for p, e in sorted(c.entrypoints.items()) if name in e["reads"]] + list(f["skill_readers"])
        lines.append(f"| `{name}` | `{f['pattern']}` | {f['kind']} | {f['place']} | {_cell(writers)} | {_cell(readers)} |")
    lines += ["", "## 環境変数", "", "| 名前 | 誰が設定するか | 読むエントリポイント |", "| --- | --- | --- |"]
    for name, v in sorted(c.env.items()):
        users = [p for p, e in sorted(c.entrypoints.items()) if name in e["env"]]
        lines.append(f"| `{name}` | {v['set_by']} | {_cell(users)} |")
    return "\n".join(lines) + "\n"
