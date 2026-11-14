# 入れ替えの後の文書の行き先

#1142 の入れ替え（S2 #3304。`git rm -r plugins/ndf` の後に `git mv plugins/ndf-next plugins/ndf`）で、文書が指すパスの多くが消える。この表は、入れ替えで中身や参照が古くなる文書ごとに、扱い・持ち主の子・時期を決める。S2 は入れ替えの PR の中でこの表どおりに直す。

例: `docs/specifications/ndf-whole-test-duration.md` は、今のプラグインの `scripts/` の下のスクリプトへ相対リンクで張っている。入れ替えの後、そのパスには新しいプラグインのファイルが無く、リンクの検査（`scripts/check-markdown-links.py`）が落ちる。下の表の「確定仕様」の行により、S2 はこのリンクを入れ替えの起点のコミットの固定の URL（`https://github.com/devbasex/ai-plugins/blob/<起点のコミット>/plugins/ndf/scripts/...`）へ書き換える。中身は 10.x の確定仕様のまま残し、書き直しは S3 #3412（11.x）が行う。

## 扱いの種類

| 扱い | すること |
| --- | --- |
| そのまま移す | 中身を変えずに新しい置き場へ移す。入れ替えで切れるリンク・パス・コマンドだけを直す |
| 書き直す | 入れ替えの後の中身に合わせて本文を直す |
| 固定の URL へ向ける | 中身は変えない。入れ替えの後に無いパスを指す相対リンクを、入れ替えの起点のコミットの固定の URL へ書き換える |
| archive へ移す | `docs/archive/` の下へ移し、生きた文書から外す。移した文書の中の相対リンクは、移した先から同じ文書を指すように張り直す（例: `docs/ndf-experiments.md` の `](ndf-swap-doc-destinations.md)` は `](../ndf-swap-doc-destinations.md)` にする）。入れ替えの後に無いパスを指すものは、張り直さずに固定の URL へ向ける。移した文書を指す他の文書のリンクも、移した先へ直す |

**入れ替えの後に無いパス**は、入れ替えの PR のコミットで実在しないパスである。今の Skill 本文とエージェント定義は S1a #3303 が今のまま移すため、`skills/` と `agents/` の下を指すリンクの多くは残る。切れるのは主に `scripts/` と `contracts/` の下を指すリンクである。

**入れ替えの起点のコミット**は、入れ替えの PR が分かれた develop のコミット（`git merge-base origin/develop HEAD`）である。そのコミットには今のプラグインがそのまま残る。

## 行き先の表

| 文書 | 扱い | 持ち主 | 時期 |
| --- | --- | --- | --- |
| `plugins/ndf/README.md` | そのまま移す（新しいプラグインの根へ置く。スクリプトのパスとコマンドを `bin/ndf` の副命令へ直す） | S2 | 11.0.0 |
| `plugins/ndf/docs/field-test-records.md` | そのまま移す（新しいプラグインの `docs/` へ） | S2 | 11.0.0 |
| `.claude-plugin/marketplace.json` の `ndf` の `description` | 書き直す（版を 11.0.0 にし、Skill とエージェントの数を新しいプラグインの `manifests/` から数える。`source` は `./plugins/ndf` のまま） | S2 | 11.0.0 |
| `README.md`・`CONTRIBUTING.md`・`SUPPORT.md`・`CHANGELOG.md` | 書き直す（入れ替えで切れるリンクとパスだけ） | S2 | 11.0.0 |
| `AGENTS.md`・`CLAUDE.md` | 書き直す（入れ替えで切れるリンクとパスだけ。運用の節の変更はリリース承認ゲートで人が見る。C7） | S2 | 11.0.0 |
| `AGENTS.md` の「安定版と実験版」の置き場 | 書き直す（今の置き場 `scripts/experimental/` は入れ替えで消える。新しいプラグインの置き場は決まっていない） | S2（置き場を決めるのは #3388） | 11.0.0（置き場は 11.x） |
| `docs/ndf-experiments.md` | archive へ移す（実験版は作り直しで作らない。機能の対応表の `experimental:scripts/experimental`） | S2 | 11.0.0 |
| `docs/ndf-plugin-reference.md`・`docs/plugin-development-guide.md`・`docs/ndf-parallel-model.md`・`docs/project-overview.md` | 書き直す（入れ替えで切れるリンク・パス・コマンドだけ） | S2 | 11.0.0 |
| `docs/versioning-and-distribution.md` | 書き直す（11.0.0 の節を足す。文書の鮮度の検査 `scripts/check-doc-staleness.py` が版の節を読む） | S2 | 11.0.0 |
| `docs/specifications/` | 固定の URL へ向ける | S2 | 11.0.0 |
| `docs/specifications/` の中身 | 書き直す（確定仕様化と測り直し） | S3 | 11.x |
| `docs/scripts-map/` | archive へ移す（今のプラグインの `scripts/` の地図で、入れ替えで当たらなくなる） | S2 | 11.0.0 |
| `docs/glossary.md`・`docs/glossary/` | 固定の URL へ向ける。語ごとの中身の入れ替えは M7 #3302（3382-5） | S2・M7 | 11.0.0（中身は 11.x） |
| `docs/development-history/`・`docs/articles/`・`docs/presentations/`・`docs/archive/`・`docs/changelog/` | 固定の URL へ向ける（記録で、中身は書いた時点のまま） | S2 | 11.0.0 |
| `docs/metrics/`・`docs/claude-code-skills-official-reference/`・`docs/claude-code-skills-survey/` | そのまま（今のプラグインへのリンクを持たない） | — | — |
| `issues/` | 固定の URL へ向ける。完了した設計の扱いは S3（3382-6） | S2・S3 | 11.0.0（扱いは 11.x） |
| 書き方の規約（`AUTHORING.md`・`document-style`） | Skill 本文とともに今の置き場へ移る。置き場を変えるのは 3382-2 | S1a・S1c | 11.0.0（置き場は 11.x） |

## 入れ替えで切れるリンクの数

切れるリンクの大半は `docs/specifications/` の下にある。リポジトリの根で次を打つと、プラグインの外の Markdown から今のプラグインへ張った相対リンクを、張った元の置き場と指す先の 1 段目のディレクトリごとに数える。

```bash
python3 - <<'PY'
import collections, os, re, subprocess
from pathlib import Path
links, files = collections.Counter(), collections.defaultdict(set)
for f in subprocess.run(["git", "ls-files", "*.md"], capture_output=True, text=True).stdout.split():
    if f.startswith("plugins/"):
        continue
    for m in re.finditer(r"\]\(([^)\s]+)", Path(f).read_text(encoding="utf-8", errors="replace")):
        u = m.group(1).split("#")[0]
        if not u or "://" in u or u.startswith("mailto:"):
            continue
        t = os.path.normpath(os.path.join(os.path.dirname(f), u))
        if t.startswith("plugins/ndf/"):
            key = ("/".join(f.split("/")[:2]) if f.count("/") >= 2 else f, t.split("/")[2])
            links[key] += 1
            files[key].add(f)
for key, n in sorted(links.items()):
    print(*key, len(files[key]), n)
PY
```

2026-11-14 の develop では、`docs/specifications/` から `scripts/` へ 73 本・452 個、`contracts/` へ 11 本・17 個のリンクがあった。`skills/` へは 106 本・469 個あるが、Skill 本文は今のまま移るため多くは残る。`docs/specifications/` の外からのリンクは `README.md` と `skills/` を指すものだけで、合わせて 26 個である。
