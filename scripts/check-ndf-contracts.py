#!/usr/bin/env python3
"""NDF のエントリポイントの契約の一覧（`plugins/ndf/contracts/`）とコードを突き合わせる構造チェック（#2336）。

エントリポイントは `plugins/ndf/scripts` 直下と `plugins/ndf/skills/*/scripts` 直下の、最上位に
`if __name__ == "__main__":` を持つ `.py` と 1 行目が `#!` の `.sh`（テストと `experimental/` を除く）。
コードから拾った引数・環境変数の読み・`.ndf/` の設定のキーの読み・受け渡しのファイルの書きが契約にあるか、
契約にあるものがコードから拾えるか、呼び手がいるか、逆引き（`contracts/index.md`）が項目と一致するかを見る。
今の外れは例外リスト（`scripts/contract-allow/`。1 項目 1 ファイル）に載せ、ベースブランチとの merge-base より
項目が増えた変更を `allow-added` で落とす。形と規則は `issues/issue-2336-design.md`。

    python3 scripts/check-ndf-contracts.py [--root <リポジトリ>] [--allow <ディレクトリ>] [--base <ref>] [<ファイル>...]
    python3 scripts/check-ndf-contracts.py --render [--root <リポジトリ>]
    python3 scripts/check-ndf-contracts.py --draft <エントリポイントのパス> [--root <リポジトリ>]

結果は 1 行 JSON（`{"tool", "status", "summary", "items", "metrics"}`）。終了コードは 0 違反なし・1 違反あり・
2 項目か例外の項目が読めない（`--draft` のパスがエントリポイントでない）・3 CI の中で起点の ref を決められない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from fnmatch import fnmatch
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
from allowlist import AllowError, allow_file_name, load_allow  # noqa: E402
from contracts_lib import extract_sh, ratchet  # noqa: E402
from contracts_lib.draft import Callers, draft  # noqa: E402
from contracts_lib.extract_py import PyExtractor, is_runnable  # noqa: E402
from contracts_lib.model import CONTRACTS, ContractError, Contracts, entry_file_name, flat_args, load  # noqa: E402
from contracts_lib.render import render  # noqa: E402

REPO = HERE.parent
SELF = "scripts/check-ndf-contracts.py"
ALLOW = "scripts/contract-allow"
INDEX = CONTRACTS + "/index.md"
ALLOW_KINDS = (
    "args-unparsed",
    "env-dynamic",
    "file-dynamic",
    "setting-open-outside-reader",
    "setting-duplicate",
    "exit-unknown",
    "no-caller",
    "entry-undeclared",
    "arg-undeclared",
    "env-undeclared",
    "setting-undeclared",
    "file-undeclared",
)


def candidates(root: Path) -> list[str]:
    """直下の `.py`・`.sh`（テストを除く）。"""
    out = []
    for d in [root / "plugins/ndf/scripts", *sorted((root / "plugins/ndf/skills").glob("*/scripts"))]:
        for f in sorted(d.glob("*")):
            if f.is_file() and f.suffix in (".py", ".sh") and not f.name.startswith("test_"):
                out.append(f.relative_to(root).as_posix())
    return out


def is_entry(root: Path, rel: str) -> bool:
    text = (root / rel).read_text(errors="ignore")
    if rel.endswith(".sh"):
        return text.startswith("#!")
    import ast

    try:
        return is_runnable(ast.parse(text))
    except SyntaxError:
        return False


def hook_entries(root: Path) -> set[str]:
    out = set()
    for f in (root / "plugins/ndf/hooks").glob("*.json"):
        text = f.read_text(errors="ignore")
        out |= {p for p in candidates(root) if Path(p).name in text and p.startswith("plugins/ndf/scripts/")}
    return out


def v(kind: str, path: str, name: str, detail: str, via: str = "") -> dict:
    d = {"kind": kind, "path": path, "name": name, "result": "violation", "detail": detail}
    if via:
        d["via"] = via
    return d


class Checker:
    def __init__(self, root: Path, c: Contracts):
        self.root, self.c = root, c
        self.py = PyExtractor(root, c.settings)
        self.callers = Callers(root)
        self.hooks = hook_entries(root)

    def facts(self, rel: str) -> dict:
        return extract_sh.facts(self.root, rel, self.c.settings) if rel.endswith(".sh") else self.py.facts(rel)

    def draft(self, rel: str) -> dict:
        return draft(rel, self.facts(rel), self.callers.of(rel), rel in self.hooks)

    def file_of(self, written: str, names: list[str]) -> str | None:
        for n in names:
            f = self.c.files.get(n)
            if f and (written == f["pattern"] or fnmatch(written, f["pattern"])):
                return n
        return None

    def entry(self, rel: str, e: dict, out: list[dict], code: set) -> None:
        f = self.facts(rel)
        self.match_args(rel, e, f, out)
        self.match_env(rel, e, f, out)
        self.collect_code_items(f, code)
        self.match_setting_keys(rel, e, f, out)
        self.match_files(rel, e, f, out)
        self.match_exit_and_callers(rel, e, out)

    def match_args(self, rel: str, e: dict, f: dict, out: list[dict]) -> None:
        cf = self.c.entry_files[rel]
        if f["argv_manual"] or "unparsed" in e["args"]:
            out.append(v("args-unparsed", rel, "", "sys.argv を手で読む（引数を静的に拾えない）"))
        if "unparsed" in e["args"]:
            return
        got = {("" if not s else s, "option", o) for s, o in f["options"]} | {(s, "positional", p) for s, p in f["positionals"]}
        want = flat_args(e["args"])
        for s, k, n in sorted(got - want):
            out.append(v("arg-undeclared", rel, f"{s}:{n}" if s else n, f"契約の args に無い {k}。{cf} の args へ足す"))
        for s, k, n in sorted(want - got):
            out.append(
                v("contract-stale", rel, f"arg:{s + ':' if s else ''}{n}", f"契約の args の {k} がコードから拾えない。{cf} から消す")
            )

    def match_env(self, rel: str, e: dict, f: dict, out: list[dict]) -> None:
        cf = self.c.entry_files[rel]
        for name, via in sorted(f["env"].items()):
            if name not in e["env"] or name not in self.c.env:
                where = f"{cf} の env" if name not in e["env"] else f"{CONTRACTS}/env/{name}.json"
                out.append(v("env-undeclared", rel, name, f"契約に無い環境変数の読み。{where} へ足す", via))
        for name in sorted(set(e["env"]) - set(f["env"])):
            out.append(v("contract-stale", rel, f"env:{name}", f"契約の env の {name} をコードが読まない。{cf} から消す"))

    @staticmethod
    def collect_code_items(f: dict, code: set) -> None:
        for (mod, fn), _ in f["env_dynamic"].items():
            code.add(("env-dynamic", mod, fn, "名前を組み立てる・走査する環境変数の読み"))
        for (mod, file, fn), via in f["setting_opens"].items():
            code.add(("setting-open-outside-reader", mod, file, f"読み手の関数の外で .ndf/{file} を開く（{fn}。{via}）"))

    def match_setting_keys(self, rel: str, e: dict, f: dict, out: list[dict]) -> None:
        cf = self.c.entry_files[rel]
        want_keys = {(s["file"], k) for s in e["settings"] for k in s["keys"]}
        for (file, key), via in sorted(f["setting_keys"].items()):
            item = self.c.settings.get(file, {"keys": {}})
            if (file, key) not in want_keys or key not in item["keys"]:
                out.append(
                    v(
                        "setting-undeclared",
                        rel,
                        f"{file}#{key}",
                        f"契約に無い設定のキーの読み。{cf} の settings と {CONTRACTS}/settings/{file}.json の keys へ足す",
                        via,
                    )
                )
        for file, key in sorted(want_keys - set(f["setting_keys"])):
            out.append(
                v("contract-stale", rel, f"setting:{file}#{key}", f"契約の settings の {file}#{key} をコードが読まない。{cf} から消す")
            )

    def match_files(self, rel: str, e: dict, f: dict, out: list[dict]) -> None:
        cf = self.c.entry_files[rel]
        for written, via in sorted(f["writes"].items()):
            if not self.file_of(written, e["writes"]):
                out.append(
                    v(
                        "file-undeclared",
                        rel,
                        written,
                        f"契約に無い受け渡しのファイルの書き。{CONTRACTS}/files/ に項目を作り {cf} の writes へ足す",
                        via,
                    )
                )
        for name in e["writes"]:
            pat = self.c.files.get(name, {}).get("pattern")
            if pat is None or not any(w == pat or fnmatch(w, pat) for w in f["writes"]):
                out.append(
                    v("contract-stale", rel, f"write:{name}", f"契約の writes の {name} をコードが書かない（か項目が無い）。{cf} から消す")
                )
        for name in e["reads"]:
            if name not in self.c.files:
                out.append(v("contract-stale", rel, f"read:{name}", f"契約の reads の {name} に項目が無い"))
        for s in e["settings"]:
            if s["file"] not in self.c.settings:
                out.append(v("contract-stale", rel, f"setting:{s['file']}", f"{CONTRACTS}/settings/{s['file']}.json が無い"))

    def match_exit_and_callers(self, rel: str, e: dict, out: list[dict]) -> None:
        cf = self.c.entry_files[rel]
        if "unknown" in e["exit"]:
            out.append(v("exit-unknown", rel, "", e["exit"]["unknown"]))
        if "callers" not in e:
            return
        if not e["callers"]:
            out.append(v("no-caller", rel, "", f"呼び手が無い。{cf} の callers か external を書く"))
        for c in e["callers"]:
            if not self.callers.contains(c, rel):
                out.append(v("caller-stale", rel, c, f"呼び手 {c} が {Path(rel).name} を含まない。{cf} の callers を直す"))

    def run(self) -> tuple[list[dict], dict]:
        out: list[dict] = []
        cands = candidates(self.root)
        entries = [p for p in cands if is_entry(self.root, p)]
        code: set = set()
        for rel in entries:
            e = self.c.entrypoints.get(rel)
            if e is None:
                d = json.dumps(self.draft(rel), ensure_ascii=False)
                out.append(
                    v(
                        "entry-undeclared",
                        rel,
                        "",
                        f"契約の項目が無い。{CONTRACTS}/entrypoints/{entry_file_name(rel)} に次の下書きを補って置く: {d}",
                    )
                )
                continue
            self.entry(rel, e, out, code)
        for rel in sorted(set(self.c.entrypoints) - set(entries)):
            out.append(v("entry-stale", rel, "", f"エントリポイントでないファイルの項目。{self.c.entry_files[rel]} を消す"))
        for kind, path, name, detail in sorted(code):
            out.append(v(kind, path, name, detail))
        for file, s in sorted(self.c.settings.items()):
            for key, k in s["keys"].items():
                if "duplicates" in k:
                    out.append(
                        v("setting-duplicate", f"{CONTRACTS}/settings/{file}.json", f"{file}#{key}", f"{k['duplicates']} と同じ値を持つ")
                    )
        used_env = {n for e in self.c.entrypoints.values() for n in e["env"]}
        for name in sorted(set(self.c.env) - used_env):
            out.append(v("contract-stale", f"{CONTRACTS}/env/{name}.json", name, "どのエントリポイントも読まない環境変数の項目。消す"))
        used_files = {n for e in self.c.entrypoints.values() for n in e["writes"] + e["reads"]}
        for name in sorted(set(self.c.files) - used_files):
            out.append(
                v("contract-stale", f"{CONTRACTS}/files/{name}.json", name, "どのエントリポイントも書き読みしないファイルの項目。消す")
            )
        idx = self.root / INDEX
        if not idx.is_file() or idx.read_text(encoding="utf-8") != render(self.c):
            out.append(v("index-stale", INDEX, "", "逆引きが項目と一致しない。python3 scripts/check-ndf-contracts.py --render を打つ"))
        metrics = {
            "entrypoints": len(entries),
            "candidates": len(cands),
            "files": len(self.c.files),
            "settings": len(self.c.settings),
            "env": len(self.c.env),
        }
        return out, metrics


def judge(root: Path, found: list[dict], allow: list[dict], targets: set[str]) -> tuple[list[dict], int, int]:
    allowed = {(r["path"], r["name"], r["kind"]): r for r in allow}
    hit, items, outside = set(), [], 0
    everything = not targets or any(SELF == t or t.startswith(CONTRACTS) or t.startswith(ALLOW) for t in targets)

    def mine(path: str) -> bool:
        return everything or path in targets

    for x in found:
        k = (x["path"], x["name"], x["kind"])
        if k in allowed and x["kind"] in ALLOW_KINDS:
            hit.add(k)
            continue
        if mine(x["path"]):
            items.append(x)
        else:
            outside += 1
    for r in allow:
        if (r["path"], r["name"], r["kind"]) not in hit:
            if mine(r["path"]):
                items.append(
                    v("unused-allow", r["path"], r["name"], f"{r['kind']} の例外に当たる違反が無い。{ALLOW}/{allow_file_name(r)} を消す")
                )
            else:
                outside += 1
    items.sort(key=lambda i: (i["path"], i["kind"], i["name"]))
    return items, len(hit), outside


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    print(
        json.dumps(
            {"tool": "check-ndf-contracts", "status": status, "summary": summary, "items": items, "metrics": metrics}, ensure_ascii=False
        )
    )


def allow_ratchet(root: Path, allow_dir: Path, allow, base_arg: str | None) -> tuple[list[dict], str, str]:
    """例外リストの追加違反と、比べた起点・ラチェットの状態を返す。起点が決まらないときは RatchetError を投げる。"""
    base = ratchet.base_ref(root, base_arg)
    added: list[dict] = []
    sha = base.rsplit("@", 1)[1]
    state = "initial" if ratchet.initial(root, sha) else "checked"
    if state == "checked":
        rel_allow = allow_dir.resolve().relative_to(root).as_posix() if allow_dir.resolve().is_relative_to(root) else ALLOW
        for r in ratchet.added(root, sha, rel_allow, allow, allow_file_name):
            added.append(
                v(
                    "allow-added",
                    r["path"],
                    r["name"],
                    f"例外リストへ項目を足した（{allow_file_name(r)}）。例外を足さずに契約かコードを直す",
                )
            )
    return added, base, state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    ap.add_argument("--allow", type=Path, help=f"例外リストの置き場（既定は <root>/{ALLOW}/）")
    ap.add_argument("--base", help="例外リストを比べる ref（既定は origin/<GITHUB_BASE_REF か .ndf/worktree.json の base_branch>）")
    ap.add_argument("--render", action="store_true", help=f"{INDEX} を書き直す")
    ap.add_argument("--draft", metavar="PATH", help="エントリポイントの項目の下書きを標準出力へ出す")
    ap.add_argument("files", nargs="*", help="合否を決めるファイル（省くと木全体の違反で決める）")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    allow_dir = a.allow or root / ALLOW
    try:
        c = load(root)
        allow = load_allow(allow_dir, ALLOW_KINDS)
    except (ContractError, AllowError) as e:
        emit("stopped", str(e), [], {})
        return 2
    if a.render:
        text, idx = render(c), root / INDEX
        changed = not idx.is_file() or idx.read_text(encoding="utf-8") != text
        if changed:
            idx.write_text(text, encoding="utf-8")
        emit(
            "ok",
            "逆引きを書き直した" if changed else "逆引きは変わらない",
            [{"path": INDEX, "result": "changed" if changed else "unchanged"}],
            {},
        )
        return 0
    ck = Checker(root, c)
    if a.draft:
        rel = Path(a.draft).resolve().relative_to(root).as_posix() if Path(a.draft).is_absolute() else a.draft
        if rel not in candidates(root) or not is_entry(root, rel):
            emit("stopped", f"エントリポイントでない: {rel}", [], {})
            return 2
        print(json.dumps(ck.draft(rel), ensure_ascii=False, indent=2))
        return 0
    found, metrics = ck.run()
    targets = {Path(t).resolve().relative_to(root).as_posix() if Path(t).is_absolute() else t for t in a.files}
    items, allowed, outside = judge(root, found, allow, targets)
    try:
        added, base, state = allow_ratchet(root, allow_dir, allow, a.base)
    except ratchet.RatchetError as e:
        if os.environ.get("CI"):
            emit("stopped", str(e), [], metrics)
            return 3
        added, base, state = [], None, "skipped"
    items.extend(added)
    metrics.update(allowed=allowed, violations=len(items), base=base, ratchet=state)
    if targets:
        metrics.update(targets=len(targets), outside=outside)
    if items:
        for i in items:
            print(f"{i['kind']}: {i['path']}: {i['name']}: {i['detail'][:200]}", file=sys.stderr)
        emit("stopped", f"契約の違反 {len(items)} 件（例外リストで許した {allowed} 件を除く）", items, metrics)
        return 1
    emit("ok", f"違反なし（エントリポイント {metrics['entrypoints']} 本・例外リストで許した {allowed} 件）", items, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
