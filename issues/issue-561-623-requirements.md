# #561 / #623: 後片付けの実行前確認を「実際に取り消せるか」で決め、まとまりの課題を閉じる時点を 1 つにする

設計は [issue-561-623-design.md](issue-561-623-design.md) にある。この文書は「何を満たすか」だけを扱う。

**2 つの課題は 1 本の設計 Pull Request で扱う。** どちらも `merged` の手順 7「閉じ忘れた issue を
閉じる」に掛かる。#561 は閉じるときに**止まるか**を問う。#623 は**いつ閉じるか**を問う。片方だけを
決めると、同じ手順を 2 回書き換えることになる。

## 目的

| # | 達成したい状態 |
| --- | --- |
| 1 | マージ後の後片付けが、失っても戻せる操作のために利用者を止めない。止まるのは git が拒む操作だけになる |
| 2 | 実行前確認を置くかどうかを、Skill ごとの書き手の感覚ではなく `AUTHORING.md` の 1 つの基準で決められる |
| 3 | まとまりに属する課題が、**どの Skill の手順を読んでも同じ時点で閉じる**。開いている課題は「まとまりの工程がまだ終わっていない」を表す |
| 4 | まとまりの工程（配布・体裁レビュー・リリース後テスト・振り返り）の記録が、まとまりのすべての課題へ残る。誰が書くかが決まっている |

## 依頼（原文）

#561 の「提案」と「受け入れ条件」（該当節をそのまま写す）:

> **確認の要否を「取り消しが難しいか」ではなく「その操作が実際に取り消せるか」で決める。**
> 後片付けの既定を自動実行にし、`git` が拒む形へ踏み込むときだけ止める。

> - [ ] マージ済みの Pull Request に対し、未コミット変更の無い通常の経路で `/ndf:merged <PR番号>` を
>       実行したとき、**削除とクローズについて利用者の入力を求める箇所が 0 になる**
> - [ ] 未マージのコミットを持つブランチ、未コミット・未追跡ファイルを持つ worktree、起点ブランチ・
>       本番のチャネルが対象に入るときは、従来どおり対象を一覧で示して止まる
> - [ ] 作業完了報告に、消した対象と復元の手段（ハッシュ / Restore branch / reopen）が載る
> - [ ] `plugins/ndf/skills/AUTHORING.md` の守り方の表と `merged` の行が、新しい基準と一致する
> - [ ] 人手の承認を求める関門は 2 つのままで、増えても減ってもいない

#561 の「決まっていること」（利用者の指示、2026-09-12）:

> 1. **マージ済みリモートブランチの削除は自動にする。** 対象は「マージ済みかつ当該 Pull Request の
>    head」に限り、戻す手段（Restore branch）を完了報告へ載せる
> 2. **閉じ忘れた issue のクローズは自動にする。** reopen で戻せることを完了報告へ載せる

#623 の問い（本文の見出しと、コメント 2 件の結論）:

> **正式版と開発版のチャネルを分けたリポジトリで、まとまりの issue をいつ閉じるかが決まっていない。**
> 手順の記載と、今回の運用と、GitHub の自動クローズと、盤面の自動化の 4 つが別々の時点を指した。

> まとまりの工程を誰がどの issue へ記録するかを、閉じる時点と一緒に決める必要がある。

> このリポジトリでは、盤面の宣言がある限り **振り返りの記録が issue を閉じる時点** になっており、
> `merged` の手順 7（develop へのマージで閉じる）とも自動クローズ（main へのマージ）とも別の時点である。

## 前提

| # | 前提 |
| --- | --- |
| 1 | git 2.53.0 の実測を基準にする（下表）。`git branch -d` と `git worktree remove` の拒否の条件は、この版で確かめたものを使う |
| 2 | GitHub の閉じる語による自動クローズは、既定ブランチへのマージでしか働かない（#259） |
| 3 | **閉じる語を Pull Request の本文から外さない。** Pull Request の作成の時点の案内が、本文の閉じる語を入力にしている（`stage-completeness.md`） |
| 4 | このリポジトリの盤面では、組み込みの自動化の `Item closed`（閉じると Done）と `Auto-close issue`（Done で閉じる）が両方とも有効である。`gh api graphql` の `projectV2.workflows` で確かめた。**他のリポジトリで両方が有効とは限らない** |
| 5 | 本番への配布の承認（関門 2）の形と数は変えない |

実測（git 2.53.0、一時リポジトリ）:

| # | 操作 | 結果 |
| --- | --- | --- |
| 1 | upstream があり、起点へ squash でマージしたブランチを `git branch -d` | `warning: deleting branch 'feat/x' that has been merged to 'refs/remotes/origin/feat/x', but not yet merged to HEAD` / `Deleted branch feat/x (was 53bba27).` / 終了コード 0 |
| 2 | 同じブランチで、リモートのブランチを消して `fetch --prune` した後に `git branch -d` | `error: the branch 'feat/x' is not fully merged` / 終了コード 1 |
| 3 | 未追跡ファイルを持つ worktree を `git worktree remove` | `fatal: '.worktrees/feat/y' contains modified or untracked files, use --force to delete it` / 終了コード 128 |
| 4 | **無視されたファイルだけを持つ worktree を `git worktree remove`** | **終了コード 0。無視されたファイルは確認なしに消える** |
| 5 | 無視されたファイル（`.env` と `scripts/__pycache__/a.pyc`）を持つ worktree で、`git status --ignored --porcelain` の `!!` の行（`.env` / `scripts/`）を共通の git ディレクトリの下の退避先へ同じ相対パスで `mv` した後に `git worktree remove` | 終了コード 0。退避先に 2 ファイルが残る |
| 6 | 追跡されたディレクトリ `a/b/` の配下に無視された `a/b/__pycache__/x.pyc` を持つ worktree で、`!!` の行（`a/b/__pycache__/`。ディレクトリは末尾に `/` が付く）を、退避先に親を作らずに `mv` | `mv: cannot move '…/wt/a/b/__pycache__' to '…/a/b/__pycache__': No such file or directory` / 終了コード 1。`mkdir -p "$(dirname "<退避先>/<相対パス>")"` の後の `mv` と、続く `git worktree remove` はどちらも終了コード 0。`dirname` は末尾の `/` の有無で同じ親を返し、`mv` も末尾の `/` 付きで終了コード 0（git 2.53.0 / GNU coreutils） |

**2 行目と 4 行目は #561 の本文に無い境界である。**

- マージ後に GitHub がリモートのブランチを消す設定（このリポジトリの `delete_branch_on_merge: true`）がある。
  squash でマージするリポジトリでは、ローカルブランチが 2 行目の形になり、通常の経路で止まる
- 無視されたファイルは、従来の同意の提示（`git status --short`）にも載っていなかった

## 対象範囲

含む:

| 対象 | 課題 |
| --- | --- |
| `AUTHORING.md` の「取り消しの難しい操作をどちらで守るか」の基準と適用表 | #561 |
| `merged` の「削除前の同意取得」・クリーンアップの手順 4〜8・「閉じ忘れた issue を閉じる」・「マージ済みブランチの整理」・作業完了報告・frontmatter の `description` | #561 #623 |
| `development-workflow/SKILL.md` の「人手の承認を求める関門」の節への原則の追記 | #561 |
| `development-workflow/references/stage-notes.md` の後片付けの段落 | #561 |
| 実行前確認を持つ他の Skill（`pr` / `release` / `out-of-scope` / `official-skills-autoloader` / `issue-upkeep` の「やらない」）の分類し直し。**手順は変えない** | #561 の判断が要る点 1 |
| squash / rebase でマージしたブランチと、無視されたファイルを持つ worktree の扱い | 上の実測 |
| まとまりの課題を閉じる時点の決定と、それを書く Skill（`progress-tracking` / `release` / `release-verification` / `retrospective` / `merged`） | #623 |
| まとまりの工程を誰がどの課題へ記録するか | #623 のコメント 1 |
| コミットメッセージの閉じる語による自動クローズの揺れ（`pr`） | #623 の案 4 |

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| `development-workflow/SKILL.md` の「`/goal` の引数として呼ばれたとき」の節 | #550 #657 の設計が所有する。この変更が触るのは「人手の承認を求める関門」の節だけ |
| `parallel-work.md` の「守る下限」と並列の実行計画 | #540 #541 #621 の設計が所有する。工程が動く単位の表も**変えない**（記録の担い手は `progress-tracking` へ書く） |
| `issue-plan-strategy` / `issue-upkeep` のマイルストーン割り振り | #540 #541 #621 の設計が所有する |
| 関門の数と形 | #561 の受け入れ条件が「増えても減ってもいない」を求める |
| 盤面の組み込みの自動化の設定を変えること | リポジトリの設定であり、Skill が書き換えるものではない |
| 閉じる語を Pull Request の本文から外すこと | 前提 3 |
| 通過工程の控えの hook が、1 回の実行の 2 件目以降の記録を読まないこと（#487） | 控えの契約の変更になる。まとまりの工程は控えの検査対象に入っていない |
| 課題の指し方の一般化（#482） | 別の課題 |
| 版数・`CHANGELOG.md` | 配布の工程が書く |
| `docs/specifications/ndf-skill-inventory/02-frontmatter-and-triggers.md` と `docs/ndf-version-decisions.md` の `merged` の記述 | 当時の Pull Request で決めたことの記録である。現行の規約は `AUTHORING.md` が持つ |
| クラス図・ER 図・テーブル定義・CRUD 図・画面・API 仕様記述（設計の成果物） | 型・永続データ・画面・API を持たない変更である。変えるのは Skill の手順の文書と、その文面を読むテストだけ |

## 受け入れ条件

### A. 後片付けで止まる回数（#561）

- [ ] A1: `merged/SKILL.md` の「クリーンアップの手順」に、次の 3 つの操作について**実行の前に同意を求める記述が無い**
      - 未コミット・未追跡ファイルの無い worktree の削除
      - `git branch -d` によるローカルブランチの削除
      - マージした Pull Request の head のリモートブランチの削除
- [ ] A2: マージコミットでマージした Pull Request に対し、未コミット変更の無い作業ツリーで
      `/ndf:merged <PR番号>` を実行する。**削除について利用者の入力を求める箇所が 0 になる**。
      リリース後テストで、このリポジトリの Pull Request 1 本に対して確かめる
- [ ] A3: 次のどちらかに当たる対象は、一覧で示して止まる。同意の無い対象を削除しないことが `merged/SKILL.md` に書かれている
      - `git branch -d` が拒んだブランチ（`-D` に当たる）
      - `git worktree remove` が拒んだ作業ツリー（`--force` に当たる）
- [ ] A4: 起点ブランチ（`base_branch`）・本番のチャネル（`production_branch`、無ければ既定ブランチ）・
      現在のブランチは、同意の有無にかかわらず削除の対象に入らない。作業完了報告に「対象外」として載る
- [ ] A5: `merged/SKILL.md` に、`git branch -D` と `git worktree remove --force` を同意を得る手順の外で実行する記述が無い
- [ ] A6: 同意なしに消すリモートブランチが「マージ済みかつその Pull Request の head」に限られる。
      「マージ済みブランチの整理」で見つかり、Pull Request の head と対応付かないリモートブランチの扱いが書かれている
- [ ] A7: squash / rebase でマージしたために `git branch -d` が拒むブランチの扱いが `merged/SKILL.md` に書かれている。
      その扱いが A5 を破らない
- [ ] A8: 作業ツリーに無視されたファイル（`git status --ignored --porcelain` の `!!` の行）があるとき、消さずに退避してから
      作業ツリーを消すことと、退避先が作業完了報告に載ることが `merged/SKILL.md` に書かれている
- [ ] A9: 作業完了報告に、消した対象ごとの復元の手段が載る

      | 対象 | 復元の手段 |
      | --- | --- |
      | ローカルブランチ | 削除時のハッシュと `git branch <名前> <ハッシュ>` |
      | リモートブランチ | Pull Request の URL と Restore branch |

      `merged` は課題を閉じないため、課題の reopen の手段は C8（閉じた終わりの工程の完了報告）が持つ

- [ ] A10: `merged` の frontmatter の `description` から "after listing them for approval" が消える。
      `python3 scripts/check-skill-frontmatter.py` が終了コード 0 で終わる
- [ ] A11: このマージがまとまりの最後か判断できないときに、`merged` が運用者の回答を待たない。
      判断できないことを報告へ書いて終わる。まとまりの範囲の確認は `release` の開始条件が持つ

### B. 実行前確認の基準（#561）

- [ ] B1: `AUTHORING.md` の「取り消しの難しい操作をどちらで守るか」に、**その操作が実際に取り消せるかで
      実行前確認の要否を決める基準**がある。git が拒む操作と、事後の復元の手段がある操作を対象から外すと書かれている
- [ ] B2: `AUTHORING.md` の適用表の `merged` の行が、A1〜A8 の手順と一致する。自動で行う操作と止まる条件が同じ語で並ぶ
- [ ] B3: `pr` / `release` / `out-of-scope` / `official-skills-autoloader` / `issue-upkeep`（やらないの判断）が、
      新しい基準のどの理由で確認を残すのかが `AUTHORING.md` に書かれている。**この 5 つの `SKILL.md` の実行前確認の手順は差分に現れない**
- [ ] B4: `AUTHORING.md` の「`allowed-tools` の意味と付け方」の「取り消しの難しい操作を無確認にしない」の行が、B1 の基準と矛盾しない
- [ ] B5: `development-workflow/SKILL.md` の「人手の承認を求める関門」に、**関門の外で工程の側が実行前確認を足さない**原則がある。
      その例外が B1 の基準であることも書かれている
- [ ] B6: 同じ節の「関門は 2 つで、増やさない」と関門の表（2 行）が変わっていない
- [ ] B7: `stage-notes.md` の後片付けの段落に「削除の対象を一覧で示して同意を取ってから消す」が残っておらず、B1 の基準を指す
- [ ] B8: `plugins/ndf/README.md` の「取り消しの難しい手順の直前に対象を提示して同意を得る」の段落が、
      `merged` を止まらない側として書く。`pr` の同意は残る

### C. まとまりの課題を閉じる時点（#623）

- [ ] C1: [決定の記録](issue-561-623-design-decisions.md) に、チャネルを分けたリポジトリでまとまりの課題を閉じる時点の決定がある。#623 の 4 案を比べた理由も書かれている
- [ ] C2: 決めた時点が、`merged` / `progress-tracking` / `release` / `release-verification` / `retrospective` の 5 つの `SKILL.md` で食い違わない。
      **課題を閉じる手順を持つのは 1 か所だけで、ほかはそこを指す**
- [ ] C3: 終わりの工程が振り返りでないモード（`light`、リリース後テストだけを通る `operation`、どちらも通らない `operation`）でも、課題を閉じる手順へ辿り着く。
      そのモードの終わりの工程の Skill に、閉じる手順を呼ぶ記述がある
- [ ] C4: 盤面の宣言があるリポジトリと無いリポジトリで、閉じる時点が同じである。
      `Auto-close issue` が有効でも無効でも、閉じる条件を満たした課題が OPEN のまま残らない
- [ ] C5: チャネルを分けていないリポジトリで、既定ブランチへのマージによる自動クローズが先に閉じた場合の扱いが書かれている。
      二重に閉じようとしても結果が変わらない
- [ ] C6: `issue-upkeep` の段 1 が読む「このまとまりで閉じた課題」が、`issue-upkeep` を呼ぶ時点で閉じている。
      `release`・`release-verification`・`retrospective` のどれでも、閉じる手順が `issue-upkeep` の呼び出しより前に置かれている
- [ ] C7: コミットメッセージの閉じる語による自動クローズの扱いが決まり、`pr/SKILL.md` に書かれている
- [ ] C8: 課題を閉じるときに利用者の入力を求めない。閉じた課題と reopen の手段が、閉じた工程の完了報告に載る
- [ ] C9: `progress-tracking/SKILL.md` に、工程に入った時点で呼ぶ記録と、終わりの工程を出るときに行う「まとまりを閉じる」が
      別の契機であることが書かれている。後者の手順の正本がこの Skill にあり、`release`・`release-verification`・`retrospective` が呼ぶ
- [ ] C10: 閉じられなかった課題が 1 件でもあれば、終わりの工程の完了報告にその課題と理由とやり直すコマンドが載り、完了と報告しない
- [ ] C11: 検証への配布で止めたまとまりと、リリース後テストが不合格・保留の条件を持つ課題は閉じず、`開いたまま` と理由が報告に載る

### D. まとまりの工程の記録（#623 のコメント 1）

- [ ] D1: まとまり単位の工程（配布・体裁レビュー・リリース後テスト・振り返り）を**誰が**記録するかが `progress-tracking/SKILL.md` に書かれている
- [ ] D2: 記録する**先の課題**の集め方（まとまりの課題の一覧を取り出すコマンド）が `progress-tracking/SKILL.md` に書かれている。
      そのコマンドをこのリポジトリの Pull Request 1 本に対して実行すると、本文の閉じる語が指す番号が出る
- [ ] D3: 後片付け（Pull Request 単位）の記録を、マージした側がその Pull Request の閉じる語が指す課題すべてへ書くと書かれている

### E. 退行しないこと

- [ ] E1: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る。
      `gh issue close <番号> --repo <所有者>/<リポジトリ>` の書き方の検査は、閉じる手順を移した先で掛かる
- [ ] E2: `claude plugin validate .` が終了コード 0 で終わる
- [ ] E3: `python3 scripts/check-cross-skill-refs.py` と `python3 scripts/check-markdown-links.py` が終了コード 0 で終わる
- [ ] E4: `bash scripts/build-runtime-plugins.sh` の後に、生成物の差分が残らない
- [ ] E5: `design/SKILL.md` の「設計 Pull Request の本文に閉じる語を書かない」規則が変わっていない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 止まらずに行った削除とクローズは、**すべて完了報告に復元の手段つきで残る**（A9 / C8）。報告に載らない自動の削除を作らない |
| セキュリティ | 同意なしに消せるリモートブランチは、マージ済みの Pull Request の head に限る（A6）。起点・本番のチャネルは常に対象外（A4） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `/ndf:merged [PR番号]` の引数は変わらない。止まる回数と完了報告の項目が変わる |
| 既存の振る舞い | 後片付けが止まらなくなる。チャネルを分けたリポジトリでは、課題が起点へのマージで閉じず、終わりの工程で閉じる |
| データ | 無い |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py` / `python3 scripts/check-cross-skill-refs.py` / `python3 scripts/check-markdown-links.py` / `claude plugin validate .` |
| 生成物 | `bash scripts/build-runtime-plugins.sh` の後に `git status --short` が空 |
| 手動確認（A2） | リリース後テストで、このリポジトリの実装 Pull Request 1 本に `/ndf:merged` を実行し、利用者への問いが出ないことを見る |
| 手動確認（C4） | このマイルストーンの課題が起点へのマージで閉じず、終わりの工程の後に閉じたことを、`gh api graphql` の ClosedEvent の時刻で見る |
| 手動確認（C11） | リリース後テストで、このマイルストーンが検証への配布だけの時点では課題が OPEN のままで、報告に `開いたまま` と理由が載ることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | Skill の実体は `plugins/ndf/skills/` の 1 か所。変更は `.worktrees/<ブランチ名>` の作業ツリーで行う（`AGENTS.md`） |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`。検査は `scripts/check-skill-frontmatter.py` |
| テスト戦略 | Skill の文面に掛かる規則は、既存の `development-workflow/tests/test_workflow_hooks.py` と同じ形（本文の文字列の検査）で最小限だけ足す。実地の振る舞いはリリース後テストで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストと検査の実行、生成物の同期、止まる条件の一覧化 |
| 確認してから行う | 関門の節の書き換え（#550 #657 の設計と同じファイル）、`progress-tracking` の呼び方の契約の変更 |
| 行わない | 関門の数を変える、盤面の設定を変える、`/goal` の節を触る、`parallel-work.md` を触る |

## 未決

設計の工程で決めた。承認は設計 Pull Request のマージで得る。決定の番号は [issue-561-623-design-decisions.md](issue-561-623-design-decisions.md) の見出しを指す。

| 項目 | 決めた場所 |
| --- | --- |
| まとまりの課題を閉じる時点（C1） | 決定 7 |
| squash / rebase のブランチ（A7） | 決定 2 |
| 無視されたファイル（A8） | 決定 5 |
| Pull Request の head と対応付かないリモートブランチ（A6） | 決定 4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 実行前確認 | Skill の手順の途中で、操作の対象を示して利用者の同意を得ること。**関門（承認）とは別枠** |
| 関門 | `development-workflow` の「人手の承認を求める関門」の 2 つ（設計 Pull Request のマージ / 本番の系へ届く操作） |
| 取り消せる操作 | 失う状態を git 自身が拒むか、事後の手段（ハッシュからの復元・Restore branch・reopen）で元へ戻せる操作 |
| まとまり | 1 回の配布で出す変更の集合（`parallel-work.md` の定義。マイルストーンがこれに当たる）。単独の変更は 1 件のまとまり |
| まとまりの課題 | まとまりに含まれる Pull Request の本文が、閉じる語で指す課題 |
| 終わりの工程 | その実行で最後に通る工程。振り返りを通るなら振り返り（`retrospective`）、通らずリリース後テストを通るならリリース後テスト（`release-verification`）、どちらも通らなければ配布（`release`） |
| 進行側 | まとまりの最後のマージを行う側（`release` の「担い手」） |
| チャネルを分けたリポジトリ | 開発の起点（`base_branch`）と本番のチャネル（`production_branch`、無ければ既定ブランチ）が別のブランチであるリポジトリ |
