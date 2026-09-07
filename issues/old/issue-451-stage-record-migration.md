# #451 控えの工程名の移行を案内へ入れる

## 依頼

> **v10.6.0 の工程名の改名に対して、通過工程の控えを書き換える手順がどこにも書かれていない。**
> 控えは `~/.local/state/ndf/stages/<所有者>__<リポジトリ>__<課題番号>.json` に課題ごとに残り、
> `stage-check.sh report` と `workflow-guard.sh` の 2 か所が読む。旧い名前のまま残ると、
> **実際に通った工程が「記録なし」として出る**。

（issue #451 の本文より）

## 目的

**利用者の手元の控えが旧い工程名のまま残り、通った工程が「記録なし」として案内される状態を
無くす。** 案内が毎回出ると読まれなくなる。

## 何が誤っていたか

**配布物が「控えは放置してよい」と書いている。**

| 場所 | いまの記述 | 事実 |
| --- | --- | --- |
| `development-workflow/references/projects-tracking.md:95` | 「控え（実行のたびに作り直される）｜放置してよい」 | 控えは課題ごとに残り続ける。`wf_record` が `.stages = ((.stages // []) + [$v])` で追記する |
| `CHANGELOG.md` の `[ndf 10.6.0]` の「移行」表 | 書き換えの対象が課題の本文だけ | 控えも書き換えが要る |
| `plugins/ndf/README.md` の「記録の移行が要ります」 | 同上 | 同上 |

**根本は 1 つ目である。** 移行の案内 2 か所は、この記述に従って書かれた。

## 受け入れ条件

- [ ] `projects-tracking.md` の「工程名が変わったとき」の表が、控えを**書き換えの対象**として
      挙げている。位置の 3 段（`CLAUDE_PLUGIN_DATA` / `XDG_STATE_HOME` / `TMPDIR`）を示す
- [ ] `CHANGELOG.md` の `[ndf 10.6.0]` の「移行」表に控えの行がある
- [ ] `plugins/ndf/README.md` の「記録の移行が要ります」に控えの書き換えが書かれている
- [ ] 書き換えのコマンドが載っており、**このリポジトリで実行して結果を確かめてある**
      （出力を Pull Request の本文へ残す）
- [ ] コマンドが 3 段の候補すべてを探し、**無い候補で失敗しない**
- [ ] コマンドが `stages` の値と `mode` の値だけを書き換え、他のキーを変えない
- [ ] 検査が終了コード 0（`check-doc-line-limit.py` / `check-markdown-links.py` /
      `check-doc-staleness.py` / `check-cross-skill-refs.py` / `check-skill-frontmatter.py` /
      `check-skill-repo-assumptions.py` / `build-runtime-plugins.sh --check` /
      `validate-runtime-plugins.sh`）
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る

## 検証手段

| 条件 | 手段 |
| --- | --- |
| 記述の追加 | 該当ファイルの差分 |
| コマンドが動くこと | 控えの写しを作った上で実行し、`stage-check.sh report` の出力が変わることを見る |
| 検査 | 上のコマンドを順に実行し、終了コードを見る |

## 範囲

| 含む | 含まない |
| --- | --- |
| `projects-tracking.md` の記述の訂正 | 控えを自動で移行するスクリプトの新設（今回限りの移行であり、配布物を増やす理由が無い） |
| 配布物 2 か所の移行の案内 | `stage-check.sh` / `workflow-guard.sh` の実装 |
| 移行のコマンドの記載 | 過去の設計文書（`issues/issue-421-423-391-modes-and-stages/02-design.md`）の訂正。記録であり、当時の判断をそのまま残す |
| 手元の控えの書き換え | 盤面の単一選択の値（利用者側の操作） |

## 前提

- **手元の控え 18 件は移行済みである**（旧い名前を持つファイルは 0 件。2026-09-07 に実測）。
  それでも配布物の案内は要る。利用者の手元の控えは旧い名前のまま残っている
- 移行のコマンドは `jq` を使う。控えの読み書きは既に `jq` に依存している
