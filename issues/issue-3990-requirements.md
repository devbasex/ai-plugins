## 何が起きたか

NDF のスクリプトを作り直したときの設計文書 36 本（[#1142](https://github.com/devbasex/ai-plugins/issues/1142) の系統）が、まだリポジトリの `issues/` に残っている。中身は確定仕様 6 本（`docs/specifications/ndf-rebuild*.md`）へ移し終えた（設計文書を確定仕様へ移して測り直す [#3412](https://github.com/devbasex/ai-plugins/issues/3412)。閉じている）。残っている作業は、36 本を消すことと、消すと開けなくなるリンクを先に直すことである。

2026-11-21 に、並行開発の計画を進めていた AI（オーガナイザー）が、この残りを #3412 の作業から切り出してこの Issue にした。36 本を消すと `plugins/ndf` の説明文書のリンク 2 本が壊れ、その書き換えは別の Issue（`plugins/ndf` のコメントが指す決定を確定仕様の決定へ書き換える [#3960](https://github.com/devbasex/ai-plugins/issues/3960)）が受け持っていたためである。#3960 の Pull Request（ https://github.com/devbasex/ai-plugins/pull/4444 ）は 2026-11-24 に `main` へ入り、そのリンクは無くなった。

## 種類

機能追加（`issues/README.md` は確定仕様へ移した文書を消す手順を持つが、36 本を消すことは #3412 の受け入れ条件が新たに約束した作業の残りであり、壊れた振る舞いを戻す変更ではないため）

## 今の姿

確定仕様へ移した `issues/` の文書は、`ndf records spec finalize` で消すと決まっている（[issues/README.md](https://github.com/devbasex/ai-plugins/blob/main/issues/README.md)）。36 本のそれぞれの移し先は、確定仕様の対応表（[docs/specifications/ndf-rebuild.md](https://github.com/devbasex/ai-plugins/blob/main/docs/specifications/ndf-rebuild.md)）にある。消し方とリンクの固め方は、#3412 の詳細設計書（[issues/issue-3412-design-detail.md の「確定仕様化」](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-3412-design-detail.md#確定仕様化)）の手順 7・8 が決めている。

2026-11-24 に `main` で数えた結果は次のとおりである。

| 確かめたこと | 結果 |
| --- | --- |
| 消す文書 | 36 本（`issues/issue-1142-*` 21 本・`issue-3347-*` 4 本・`issue-3388-*` 5 本・`issue-3395-*` 6 本） |
| 36 本のファイル名を含む行（`plugins/`・`scripts/`・`.github/`・`AGENTS.md`・`CLAUDE.md`） | 0 行 |
| 36 本のファイル名を含む行（`docs/`） | 56 行。用語集の語の出所（`docs/glossary/glossary.json` の `pending_source`）が 51 行、#3960 が足した確定仕様 3 本の説明が 5 行 |
| 36 本を消すと壊れる、リポジトリの文書のリンク | 0 本 |
| 開いている Issue 426 件の本文にある、36 本へのリンク | 14 件に 34 本。どれも `main` などのブランチを指すので、36 本を消すと開けなくなる |

## 何を足すか・変えるか

| | 今 | 終えた後 |
| --- | --- | --- |
| 設計文書 36 本 | `issues/` にある | 無い。消す前のコミットを指すリンクから読める |
| 開いている Issue の本文の 36 本へのリンク 34 本 | ブランチを指す | 消す前の `main` のコミット（40 桁の SHA）を指す |
| 確定仕様 3 本の説明の 5 行 | 36 本のファイル名を書いている | 36 本のファイル名を書かない |
| 用語集の語 51 語の出所 | 36 本のどれか（確定する前の出所） | 確定仕様のどれか |

## なぜ困るか

- 36 本が残ると、`issues/` を読む人と AI が、確定仕様と食い違う古い決定を正しいものとして読む
- リンクを固めずに消すと、開いている 14 件の Issue から 34 本のリンクが開けなくなり、11.x の作業の材料が読めなくなる
- #3412 の受け入れ条件 1・5・6（36 本の片付けと参照が 0 件であること）が満たされないまま残る

## 目的

作り直しの設計の正しい記録を確定仕様 6 本だけにする。36 本を消した後も、開いている Issue のリンクから消す前の 36 本を読めるようにする。

## 対象範囲

含む:
- 開いている Issue の本文の 36 本へのリンクを、消す前の `main` のコミットへ固める置換案を作る。Pull Request の本文に、Issue の番号と替えるリンクの数の表と、置換案を当てるコマンドを載せる
- 置換案を当てた後に、開いている Issue の本文を読み直して確かめる
- #3960 が足した確定仕様 3 本の 5 行（`ndf-review-items.md` の 3 行・`ndf-review-items-11x.md` の 1 行・`ndf-code-decision-references.md` の 1 行）から、36 本のファイル名を無くす
- 36 本を `ndf records spec finalize` で消し、用語集の語の出所を確定仕様へ替えて、`docs/glossary/` の文書を作り直す

含まない:
- 確定仕様 6 本の本文（#3412 で済み）
- `plugins/ndf` のコメントの書き換え（#3960 が持つ。`main` で 0 行）
- 入れ替えの後の測り直し（#3412 の受け入れ条件 9・10）
- 閉じた Issue と Pull Request の本文のリンク（#3412 の詳細設計書が、固める対象を開いている Issue に限っている）
- 置換案を Issue の本文へ当てる操作そのもの（Issue の編集者である利用者が打つ。前提 2）

## 前提

- 前提 1: リンクを固めるコミットは、36 本を消すコミットの親にあたる `origin/main` のコミットで、作業の中で 1 回だけ取る（#3412 の詳細設計書の手順 7）
- 前提 2: AI は、同じ時間にほかの人が Issue の本文を書き換えないことを確かめられない。そのため置換案は Issue の編集者（利用者）が当て、その間は対象のリポジトリで NDF のほかの実行を止めておく（#3412 の詳細設計書の「本文の書き戻しと同時の編集」）

## 受け入れ条件

- [ ] 1. 設計文書 36 本が `issues/` から無くなり、確定仕様の対応表（`docs/specifications/ndf-rebuild.md`）のすべての行が処理済みである（#3412 の受け入れ条件 1）
- [ ] 2. `plugins/`・`scripts/`・`docs/`・`.github/`・`AGENTS.md`・`CLAUDE.md` で、36 本のファイル名を含む行が 0 行である。コミットに固めたリンクの中のファイル名も数える。除くのは `docs/archive/`・`docs/ndf-12-survey/`・`scripts/ndf-next-parity/`・`CHANGELOG.md` だけである（#3412 の受け入れ条件 5）
- [ ] 3. 開いている Issue の本文にある 36 本へのリンクのうち、40 桁の SHA 以外（ブランチ・タグ・短い SHA）を指すものが 0 本である（#3412 の受け入れ条件 6）
- [ ] 4. 今回替えたリンクのそれぞれについて、指すコミットにそのファイルがあり、`#` の後ろの見出しがそのコミットの時点のファイルの見出しに当たる
- [ ] 5. 置換案を当てた後の編集の履歴に、当てる直前の取り直しから書き込みまでの間の、ほかの人の編集が 0 件である。1 件以上あれば、AI はその本文を書き直さず、Issue の番号・編集者・時刻を Pull Request の本文に挙げる
- [ ] 6. 受け入れ条件 3・4 は、この作業の Pull Request が `main` へ入る前に満たされている（入った時点で、ブランチを指すリンクが壊れるため）
- [ ] 7. 36 本を確定する前の出所に持っていた用語集の語が、確定仕様を出所に持ち、確定する前の出所を持たない
- [ ] 8. 退行しない: `ndf records glossary render --check` が `ok` を返す。Markdown のリンクの検査（`scripts/check-markdown-links.py`）・文書の古さの検査・CI の必須のジョブが、この作業の Pull Request で通る（#3412 の受け入れ条件 11）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（配るコマンド・引数・結果 JSON・Skill の手順は変えない） |
| データ | 無し |
| 既存の振る舞い | 変わらない。`issues/` から 36 本が消え、開いている Issue 14 件の本文のリンクが指す先と、用語集の語の出所が替わる |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | リンクを固めるコミットを決めた | 作業を始めた | — | — |
| E2 | 置換案を作った | 開いている Issue をすべて読んだ | 一覧の取得が途中で切れたら止めて読み直す | E1 |
| E3 | 確定仕様 3 本の 5 行を書き換えた | 受け入れ条件 2 の検査が 5 行を返した | — | — |
| E4 | 利用者が置換案を Issue の本文へ当てた | Pull Request の本文の、置換案を当てるコマンド | 書いた後に本文が変わった・上書きの疑いがある → AI は書き直さず番号を挙げる（受け入れ条件 5） | E2 |
| E5 | AI が開いている Issue を読み直して確かめた | E4 が終わった | 受け入れ条件 3・4 が通らなければ、その Issue を挙げて利用者へ返す | E4 |
| E6 | 36 本を消した | `ndf records spec finalize` | 36 本に未コミットの変更があれば消さずに止める。語の出所が決まらなければ語ごとに出所を指定して打ち直す | E1・E3 |
| E7 | 用語集の文書を作り直した | E6 が出所を替えた | — | E6 |
| E8 | Pull Request が `main` へ入った | リリース承認 | — | E5・E6・E7（受け入れ条件 6） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析・型検査 | 受け入れ条件 2: #3412 の詳細設計書の「参照の検査」の 1。受け入れ条件 1・7: 消した後の `git ls-files` と `docs/glossary/glossary.json`。受け入れ条件 8: `ndf records glossary render --check`・`scripts/check-markdown-links.py`（36 本を `--removing` で渡す形も含む）・CI の必須のジョブ |
| 手動確認（マージ前） | 利用者が Pull Request の本文のコマンドで置換案を Issue の本文へ当て、AI の読み直し（受け入れ条件 3〜5）が通ったことを Pull Request の本文で確かめる |

### エンジニア向けの補足

受け入れ条件 2 の検査は次のコマンドで、0 行を合格とする。

```bash
git grep -n -e issue-1142- -e issue-3347- -e issue-3395- -e issue-3388- -- \
  plugins scripts docs .github AGENTS.md CLAUDE.md \
  ':!docs/archive' ':!docs/ndf-12-survey' ':!scripts/ndf-next-parity' ':!**/CHANGELOG.md'
```

受け入れ条件 3・4 の列挙は `gh api --paginate 'repos/devbasex/ai-plugins/issues?state=open&per_page=100'` で行い、`gh search issues` は使わない（既定の上限 30 件に縛られる）。置換案の形・当てるコマンド・消すコマンドは、#3412 の詳細設計書の「開いている Issue の列挙」「本文の書き戻しと同時の編集」「消すコマンド」にある。

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 36 本の片付けを #3412 から切り出し、#3960 の後に行う | 起票者（並行開発の計画のオーガナイザーの判断、2026-11-21） | 対象範囲・何が起きたか |
| S2 | 置換案は Pull Request の本文に載せ、利用者が当てる | 起票者（#3412 の詳細設計書の手順 7 から写した、2026-11-21） | 対象範囲・前提 2・受け入れ条件 5 |
| S3 | #3960 が足した確定仕様 3 本の 5 行（コミットに固めたリンクと、ファイル名をコードの書式で書いた説明）も書き換える | 要求を書いた worker（受け入れ条件 2 の検査は 36 本のファイル名を文字の並びで数え、固めたリンクと説明も当たる。除外に足すと #3412 の受け入れ条件 5 の範囲を変えるため、書き換える側を選んだ。2026-11-24） | 対象範囲・受け入れ条件 2 |
| S4 | 置換案を当てて確かめ終えることを、Pull Request のマージの前の条件にする | 要求を書いた worker（#3412 の詳細設計書の手順 7 の注記「適用はその PR のマージより前に終える」から、2026-11-24） | 受け入れ条件 6・手動確認（マージ前） |
| S5 | 閉じた Issue と Pull Request の本文のリンクは固めない | 要求を書いた worker（#3412 の詳細設計書の列挙が開いている Issue だけを対象にしているため、2026-11-24） | 含まない |

## リクエスト（原文）

<details>
<summary>起票したときの本文（書き手: 起票者、日付: 2026-11-21。「着手の条件」の括弧は 2026-11-24 の追記）</summary>

> ## 目的
>
> #3412（#1142 の作り直しの仕様を確定仕様 6 本へ移し、入れ替えの前後を同じ物差しで測る）の実装計画 [issues/issue-3412-plan.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-3412-plan.md) の単位 u7（Task 10）を受け持つ。作り直しの設計文書 36 本（`issues/issue-1142-*`・`issue-3347-*`・`issue-3395-*`・`issue-3388-*`）を消し、それらを指す開いている Issue のリンクを固定リンクへ替える。
>
> ## 経緯
>
> - 36 本を消すと `plugins/ndf/src/ndf/checks/README.md:94` の 2 本の相対リンク（`issue-3347-design-detail.md`・`issue-3395-design.md`）が壊れ、Markdown のリンクの検査が落ちる。この行の書き換えは #3960（`plugins/ndf` の「決定 N」の参照の書き換え）が持つ
> - #3960 は計画 p9 で lz1-contract の 3 行目にあり、ls3-spec の u7 がそれを待つと ls3-spec の実装の工程を閉じられない
> - 計画 p9 のオーガナイザーの判断（2026-11-21）: u7 を ls3-spec から外し、#3960 の行の後ろの行として足す。ls3-spec は u8（測り直し）までで工程「実装」を閉じる
>
> ## 対象範囲
>
> - 含む: 実装計画の Task 10（詳細設計書 [issues/issue-3412-design-detail.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-3412-design-detail.md) の手順 7・8）。置換案を作り、PR 本文に Issue の番号と替えるリンクの数の表と適用のコマンドを載せる。消す直前に 36 本の未コミットの変更が 0 行であることを確かめ、`ndf records spec finalize` で消す。`docs/glossary/glossary.json`・`docs/glossary/ndf-rebuild.md` の生成の更新
> - 含まない: 確定仕様の本文（#3412 の u4・u5 で済み）・`plugins/ndf` の参照の書き換え（#3960）・計測（#3412 の u8）
>
> ## 着手の条件
>
> - #3960 の `plugins/ndf/src/ndf/checks/README.md` の書き換えが `main` へ入っている（満たした。#3960 のスプリントの PR https://github.com/devbasex/ai-plugins/pull/4444 が 2026-11-24 に `main` へ入り、`checks/README.md` から `issue-3347-`・`issue-3395-` を指すリンクが無くなった）
>
> ## 受け入れ条件
>
> - [ ] 1. 作り直しの設計文書 36 本が `issues/` から無くなり、確定仕様の対応表（`docs/specifications/ndf-rebuild.md`）のすべての行が処理済みである（#3412 の受け入れ条件 1）
> - [ ] 2. `plugins/`・`scripts/`・`docs/`・`.github/`・`AGENTS.md`・`CLAUDE.md` から 36 本のファイル名を指す参照が 0 件である（除外は #3412 の受け入れ条件 5 と同じ）
> - [ ] 3. 36 本を指す開いている Issue のリンクが、消す前のコミットの固定リンクへ替わっている（#3412 の受け入れ条件 6）
> - [ ] 4. `ndf records glossary render --check` が `ok`、`scripts/check-markdown-links.py` と文書の古さの検査と CI の必須のジョブが通る（#3412 の受け入れ条件 11）

</details>

> 修正: requirements-design の手順に従い、課題ごとに要求と受け入れ条件を書いて課題の本文へ反映する

（書き手: AI の起動指示、日付: 2026-11-24。書き手が利用者でない文は、利用者の指示の原文として扱わない）
