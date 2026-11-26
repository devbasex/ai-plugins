# 作り直しの設計文書 36 本を消した後: 読む経路と、消すときに決めたこと

NDF のスクリプトの作り直し（#1142 と、その続きの #3347・#3388・#3395）の要求仕様と設計文書 36 本は、中身を確定仕様 6 本（索引 [ndf-rebuild.md](ndf-rebuild.md) と、その索引が挙げる 5 本）へ移した後、#3990 で `issues/` から消した。作り直しの決定の正本は確定仕様 6 本だけである。この文書は、消した 36 本を読む経路と、消すときに決めた 3 つのことを持つ。

## 概要

**例: 開いている Issue の本文から、消した #3388 の設計の対応表へのリンクをたどった読み手。** リンクはブランチを指すので開けない。読み手は [ndf-review-items-11x.md](ndf-review-items-11x.md) の「出所」にある「消す前のコミットの `issues/` の一覧」を開き、一覧から目当てのファイルを選ぶ。決定の中身だけが要るなら、[ndf-rebuild.md の「番号の対応表」](ndf-rebuild.md#番号の対応表)で元の系統と番号から確定仕様の番号を引く。

## 仕様

### 消した 36 本を読む経路

次の 2 つのリンクから読める。どちらもファイルではなくコミットの `issues/` の一覧を指すので、36 本を消した後も開ける。GitHub のコミットを指す URL は、ブランチを指す URL と違い、内容が変わらない（[GitHub Docs「Getting permanent links to files」](https://docs.github.com/en/repositories/working-with-files/using-files/getting-permanent-links-to-files)）。

| リンク | 指すコミット | 見える版 |
| --- | --- | --- |
| [ndf-rebuild.md の「番号の対応表」](ndf-rebuild.md#番号の対応表)の「設計の材料」 | `784110e5`（2026-11-21） | 36 本のすべて。2 本（#1142 の設計レビューの振り分け表と #3395 の設計）は、その後に書き換わる前の版 |
| [ndf-review-items.md](ndf-review-items.md) と [ndf-review-items-11x.md](ndf-review-items-11x.md) の「出所」の「消す前のコミット」 | `eb4d652d`（2026-11-24。#3990 のブランチが分かれた元の `main`） | 消す直前の版 |

### Issue と Pull Request の本文のリンクは書き換えない

Issue と Pull Request の本文にある 36 本へのリンクは、消す前のコミットを指す形へ書き換えない（利用者の判断、2026-11-24）。ブランチを指すリンクは、`main` を指すものは #3990 が `main` へ入った時点で、`develop` を指すものは `develop` を消した時点で開けなくなる。読み手は上の 2 つのリンクか git の履歴から元の文書を探す。

書き換えを安全に行う手順（書き込む間に入ったほかの編集を上書きしない・書き換える組を人が許したかを確かめる）を、設計で閉じられなかったためである。GitHub の Issue の更新には条件付きの書き込みが無く、このリポジトリの Issue を編集できるアカウントは 1 つなので、人の編集と AI の書き込みを履歴で見分ける手順が場合分けを増やした。そのため、#3412 の受け入れ条件 6（開いている Issue のリンクを固める）は満たしていない。

### 確定仕様の本文に 36 本のファイル名を書かない

確定仕様から元の文書を指すときは、ファイル名の代わりに「#3388 の設計の対応表」「#1142 の要求仕様のコピー」のように課題の番号と表の名前を文で書き、リンクはコミットの `issues/` の一覧（`https://github.com/devbasex/ai-plugins/tree/<SHA>/issues`）へ向ける。もとからコミットを指していたリンクは同じ SHA のまま一覧へ替え、行番号や中身の時点を変えていない。

36 本のファイル名を文字の並びで数える検査（#3412 の受け入れ条件 5。下の「テスト観点」）を、除外を広げずに通すためである。リンクを消して文だけにすると、読み手が元の表を開けなくなる。代わりに、読み手は一覧から目当てのファイルを選ぶ。

### 用語集の語の出所を、誰が打っても同じになる規則で決めた

用語集（[docs/glossary/glossary.json](../glossary/glossary.json)）で 36 本を暫定的な出所に持っていた 51 語は、確定仕様を出所に持つ。`ndf records spec finalize` が決めるのは、渡した確定仕様のうち 1 本だけに現れる語だけなので、残りの語は次の規則で決めて渡した。語ごとに意味を読んで選ぶと、選ぶ人ごとに結果が変わるためである。

| 語の現れ方 | 出所 |
| --- | --- |
| 確定仕様 6 本のどれにも現れない | 6 本以外の確定仕様で最も多く現れる 1 本 |
| 2 本以上に現れる | 最も多く現れる 1 本。同数なら索引の並び（`ndf-rebuild.md` が先）で先の 1 本 |
| どれにも現れない | 括弧の補足を除いた語で数えて同じ規則で決め、それでも現れなければ `ndf-rebuild.md` |

この規則で `ndf-rebuild.md` を出所にした 8 語（「一時コピーの検査」「drive の状態」など）は、出所の本文に現れない。使われなくなった語を廃止するかは、語の意味と存否を変える判断で、決めていない。

## テスト観点

- 36 本のファイル名を含む行が無いこと。次の `git grep` が 0 行を返す

  ```bash
  git grep -nE 'issue-(1142|3347|3388|3395)-' -- \
    plugins scripts docs .github AGENTS.md CLAUDE.md \
    ':!docs/archive' ':!docs/ndf-12-survey' ':!scripts/ndf-next-parity' ':!**/CHANGELOG.md'
  ```

- 用語集の語が `pending_source` に 36 本を持たず、`ndf records glossary render --check` が `ok` を返すこと
- 上の 2 つのリンクが開け、`scripts/check-markdown-links.py` が通ること

## 関連リンク

- [ndf-rebuild.md](ndf-rebuild.md)（作り直しの索引・番号の対応表）・[ndf-review-items.md](ndf-review-items.md)・[ndf-review-items-11x.md](ndf-review-items-11x.md)・[ndf-code-decision-references.md](ndf-code-decision-references.md)
- [#3990](https://github.com/devbasex/ai-plugins/issues/3990)（36 本を消す）・[#3412](https://github.com/devbasex/ai-plugins/issues/3412)（確定仕様化と測り直し）・[#3960](https://github.com/devbasex/ai-plugins/issues/3960)（`plugins/ndf` の参照の書き換え）
