# #3990: 作り直しの設計文書 36 本を消し、確定仕様だけを正本にする

## 関連リンク

- 要求仕様: [issue-3990-requirements.md](./issue-3990-requirements.md)（正本は https://github.com/devbasex/ai-plugins/issues/3990 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3990-design.md](./issue-3990-design.md)（詳細設計は「どこを変えるか」に畳んである）
- モード: `standard`。実装計画は省けない（触るファイルが、工程の文書と消す 36 本を除いて 10 本で、省ける上限の 4 を超える）

## 修正対象

設計書の「どこを変えるか」の表から引き写す。

| ファイル | 変える中身 |
| --- | --- |
| `docs/specifications/ndf-review-items.md` | 6 行・16 行・18 行の 3 か所で、ファイル名とファイルへのリンクを、課題の番号と表の名前の文と、コミットの `issues/` の一覧へのリンクに替える（設計書の決定 3 の対応表の 1〜3 行目）。「列の読み方」の表と前書きの行だけを書き換え、`ndf check` の規則 `review-items` が読む「行き先の表」は変えない |
| `docs/specifications/ndf-review-items-11x.md` | 5 行の 2 か所（ファイル名とファイルへのリンク）を、決定 3 の対応表の 4 行目のとおり替える |
| `docs/specifications/ndf-code-decision-references.md` | 74 行のファイル名の型 2 つを、「作り直しの設計文書（#1142・#3347・#3388・#3395 の設計文書）へのパス」という文に替える。リンクは付けない |
| `docs/glossary/glossary.json` | 36 本を暫定的な出所（`pending_source`）に持つ 51 語の出所を、確定仕様へ替える。手で直さず `ndf records spec finalize` が書く。`--spec` には決定 2 の一式、`--source-of` には設計書の「用語集の語の出所」の 35 語を渡す |
| `docs/glossary/ndf-rebuild.md`、`docs/glossary/ndf-code-structure.md`、`docs/glossary/ndf-durable.md`、`docs/glossary/ndf-supervise.md`、`docs/glossary/ndf-design.md`、`docs/glossary/ndf-cross-review.md` | 用語集の正本から作り直す文書。手で直さず `ndf records spec finalize` が作り直す |
| `issues/issue-1142-*`（21 本）、`issues/issue-3347-*`（4 本）、`issues/issue-3388-*`（5 本）、`issues/issue-3395-*`（6 本） | 消す 36 本（一覧は設計書の「どこを変えるか」）。`ndf records spec finalize` が `--remove` で消してコミットする。開いている Issue の本文のリンクは書き換えない（決定 1） |

## タスク分解

コードを変えない作業なので、テスト駆動は適用しない。各タスクは、設計書の「処理の流れ」の手順 1〜4 に 1 つずつ対応し、終わりの条件を検査のコマンドで確かめる。変更の種類は機能追加なので、最初のタスクで今の検査が通ることを確かめ、退行を測る基準にする。

### Task 1: 消す前のコミットを取り、今の検査の結果を基準として残す（手順 1）

- **対象ファイル:** 無し（確かめるだけ）
- **変更内容:** `git fetch origin main` の後に `git merge-base HEAD origin/main` と `git rev-parse origin/main` を打ち、同じ 40 桁の SHA を消す前のコミットとする。違えば `origin/main` へリベースしてから打ち直す。リベースで強制 push が要るときは、打つ前に人へ戻して承認を得る（設計書の「消す前のコミットの決め方」）。その木に 36 本があること、`git ls-tree` で取った消すパスの一覧が 36 行であることを確かめる。あわせて `ndf records glossary render --check`・`scripts/check-markdown-links.py`・`scripts/check-doc-staleness.py`・`ndf check` が今の状態で通ることを確かめる
- **満たす受け入れ条件:** 4 の基準（手を入れる前に通っていたことを示す）
- **進め方:** テスト駆動は適用しない（コードを変えない）。終わりの条件は「SHA が 40 桁で、その木に 36 本がある」

### Task 2: 確定仕様 3 本の 5 行から 36 本のファイル名を無くす（手順 2）

- **対象ファイル:** `docs/specifications/ndf-review-items.md`、`docs/specifications/ndf-review-items-11x.md`、`docs/specifications/ndf-code-decision-references.md`
- **変更内容:** 設計書の決定 3 の対応表のとおり 5 行を書き換え、Task 1 の SHA を新しいリンクに使う。3 本だけをコミットする
- **満たす受け入れ条件:** 2 の一部
- **進め方:** テスト駆動は適用しない。設計書の参照の検査の 1 が、用語集の正本の `pending_source` の 51 行だけを返すことを確かめる。`ndf check`（規則 `review-items`）が通ることも確かめる

### Task 3: 36 本を消し、用語集の語の出所を確定仕様へ替える（手順 3）

- **対象ファイル:** `docs/glossary/glossary.json`、`docs/glossary/` の 6 本の `.md`、消す 36 本
- **変更内容:** 36 本に未コミットの変更が無いことを確かめてから、`ndf records spec finalize` を `-m <コミットの件名>` 付きで打つ。`--spec` に決定 2 の一式、`--remove` に 36 本、`--source-of` に決定 2 の語を渡す。打つ前に決定 2 の規則で一式と語を数え直し、2026-11-24 の実測（8 本と 35 語）と違えば数え直した結果を渡す。`-m` を省いたときは、返したパスだけを `git add -A -- <パス…>` と `git commit -m <件名> -- <パス…>` でコミットする
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** テスト駆動は適用しない。コミットの後に、参照の検査の 1 と 2 が 0 行、`git ls-files` で 36 本が 0 件、`ndf records glossary render --check` が `ok` であることを確かめる。51 語のすべてが `source` に `docs/specifications/` のファイルを持ち、`pending_source` を持たないことを `glossary.json` で確かめる。確定仕様の対応表（`docs/specifications/ndf-rebuild.md`）のすべての行の「扱い」の列が埋まっていることも確かめる

### Task 4: リリース承認を求める直前に確かめ直し、結果を PR の本文に書く（手順 4）

- **対象ファイル:** 無し（PR の本文に結果を書く）
- **変更内容:** 参照の検査の 1・2 と、消す前のコミットから最新の `main` までに 36 本を変えたコミットの一覧（`git log --full-history --format=%H <消す前のコミット>..origin/main -- <36 本>`）を打ち直す。一覧が空でなければ、設計書の「消す前のコミットの決め方」の 4 のとおり Task 1 から取り直す。受け入れ条件 1〜4 の結果を PR の本文に書く。承認を求めた後に PR へコミットを足したときも、このタスクを打ち直す
- **満たす受け入れ条件:** 1・2・3・4（確かめの結果を利用者へ示す）
- **進め方:** テスト駆動は適用しない。終わりの条件は、参照の検査の 1・2 が 0 行で、36 本を変えたコミットが無いこと

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| #3990 | Task 1・2・3・4 | 3990:1・3990:2・3990:3・3990:4 | 約 120（確定仕様 3 本で約 10、`glossary.json` で約 110） | （実装の後に書き足す） |

数えないパスは、`ndf records spec finalize` が作り直す `docs/glossary/` の 6 本の `.md` と、消す 36 本（2026-11-24 の実測で 14,511 行の削除）である。消す 36 本は中身を読む対象ではなく、消したファイルの一覧が設計書の 36 本と一致するかだけを見るので、行数に入れない。数えるコマンドは次のとおりである。

```bash
git diff --shortstat <ベース>...HEAD -- . \
  ':(exclude)issues/issue-1142-*' ':(exclude)issues/issue-3347-*' \
  ':(exclude)issues/issue-3388-*' ':(exclude)issues/issue-3395-*' \
  ':(exclude)issues/issue-3990-*' ':(exclude)docs/glossary/*.md'
```

36 本の削除は、設計書の手順 3 で 1 回の `ndf records spec finalize` が行う。課題ごとに分けて別の単位にすると、用語集の語の出所を替える処理も分かれ、設計を変えることになるので分けない。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | コードを変えない。触るのは確定仕様 3 本の 5 行と、コマンドが書く用語集だけである |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | しない | スプリントの課題は 1 つで、レビューの単位も 1 つである。単位をまたぐ重複と構造の候補が無い |

## リスクと対処

設計書の「影響と危険」から、実装の途中で手順を変えるものだけを挙げる。

| リスク | 対処 |
| --- | --- |
| 用語集を触る別の Pull Request が先に `main` へ入る | `ndf records glossary render --check` が食い違いを返す。Task 3 のコミットを外して 36 本が残る状態へ戻し、`main` の変更を取り込んでから Task 3 を打ち直す（36 本が無いまま打つと、コマンドが用語集を処理する前に止まる） |
| 分岐点の後に 36 本を変える別の変更が `main` へ入る | Task 4 とマージキューへ入れる直前の比較で拾い、Task 1 から取り直す。強制 push は人の承認を得てから打つ |
| 36 本へのリンクを新しく足す別の変更が入る | Task 4 の参照の検査の 1・2 が拾う。その行に Task 2 と同じ書き換えを当て、Task 4 を打ち直す |

## 切り戻し手順

- 3 つのコミット（Task 2 の確定仕様、Task 3 の削除と用語集）は、どれも `git revert` で戻せる。36 本は消す前のコミットに残っているので、中身は失われない
- `main` へ入った後に戻すときも、同じコミットを `git revert` する Pull Request を出す

## 完了の定義

- [ ] 受け入れ条件 1〜4 をすべて満たし、条件ごとに検証手段（設計書の「テスト設計」の表）と結果が対応している
- [ ] 受け入れ条件 1〜4 の確かめの結果を PR の本文に書いてある（Task 4）
- [ ] CI の必須のジョブが、この作業の Pull Request で通っている
