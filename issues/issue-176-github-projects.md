# issue #176: development-workflow の進行管理に GitHub Projects を使う

## 関連リンク

- [issue #176](https://github.com/devbasex/ai-plugins/issues/176)
- 盤面: https://github.com/orgs/devbasex/projects/1 （「NDF 開発の進行」）
- [issues/parallel-batch-02/00-overview.md](parallel-batch-02/00-overview.md) — このバッチの全体指示
- 先行する変更: [issue-188-release-step.md](issue-188-release-step.md)（工程表へ「配布」を足す。同じ表を触る）
- 参考にする設計: `plugins/ndf/skills/worktree/`（宣言ファイルがあるときだけ動く形）

## モード

`architecture`。工程の進行を記録する先を新設し、複数の Skill にまたがる。外部サービスへの
書き込みを伴い、認証の権限が要る。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 盤面 | GitHub Projects v2 のプロジェクト 1 つ |
| アイテム | 盤面に載る 1 行。1 つの issue に対応する |
| ビュー | 1 つの盤面に対する表示の設定（フィルタ・グループ化・表示形式） |
| 工程 | `development-workflow` が振り分ける開発の段階 |
| 宣言 | リポジトリに置く設定ファイル。これが無ければ Skill は何もしない |

## 目的と非目的

達成したい状態:

- 進行中の変更が、いまどの工程にいるかを 1 か所で見られる
- 判定したモードと、対応する作業ツリー・計画ファイルがセッションをまたいで残る
- 盤面が使えない環境でも、開発の工程がそのまま通る

やらないこと:

- **計画ファイルと仕様をアイテム本文へ移すこと。** 後述の「決めること 7」を参照
- **進行の自動推定。** Pull Request の状態から工程の段階までは決められない
- **他リポジトリの運用の変更。** 盤面は組織単位だが、この変更が触るのは ai-plugins だけである

## 着手前に確かめたこと

issue の本文は「リポジトリに Projects が作られているかは未確認。確認には `read:project`
スコープが要り、現在の認証には含まれていない」としていた。トークンを差し替えて確かめた。

```console
$ gh auth status
  - Token scopes: 'admin:org', ..., 'project', 'repo', 'user', 'workflow', ...

$ gh api graphql -f query='{ repository(owner:"devbasex",name:"ai-plugins"){projectsV2{totalCount}}
                             organization(login:"devbasex"){projectsV2{totalCount}} }'
{"data":{"repository":{"projectsV2":{"totalCount":0}},"organization":{"projectsV2":{"totalCount":0}}}}
```

**盤面は 1 つも無かった。** 作成から始める。あわせて、この利用者は組織 devbasex の `admin`
であり、組織単位の盤面を作れることも確かめた。

## 決めること への回答

issue が挙げた 8 件に答える。1・2・6 は実施済みで、盤面に反映されている。

| # | 論点 | 回答 | 理由 |
| --- | --- | --- | --- |
| 1 | 盤面の単位 | **組織単位（devbasex）** | 組織に 3 リポジトリ（ai-plugins / devbase / devbase-samples）があり、#113 の実機試行は devbase を対象にした。1 つの盤面で横断して見られる。Projects v2 は組織所有とリポジトリ所有を後から変換できないため、広い側を選ぶ |
| 2 | 進行の値を工程名と一致させるか | **一致させる**（14 値） | 工程表がそのまま値の一覧になる。#188 で「配布」が増えたように工程は増える。同じ表から Projects 側も更新できる |
| 3 | 誰が更新するか | **工程の切れ目を持つ Skill が、共通スクリプト経由で更新する** | 下の「案 D を採る理由」 |
| 4 | 認証スコープ | **`project`**（読み書き）。無い環境は 5 と同じ扱い | 読み取りだけの `read:project` ではフィールドを書けない |
| 5 | 使えない環境での動作 | **`.ndf/projects.json` があるときだけ動く。無ければ何も出力せず終了コード 0** | `worktree` と同じ形。進行管理が理由で開発が止まってはいけない |
| 6 | 既存の open issue を遡って登録するか | **登録する**（13 件） | 件数が少なく一度きりである。登録しないと、盤面に載る変更と載らない変更が混ざる |
| 7 | 計画ファイルと仕様の置き場所 | **リポジトリに残す。アイテムからは参照だけ** | 下の「決めること 7 への答え」 |
| 8 | 1 issue から複数 Pull Request | **1 issue = 1 アイテム。Pull Request は組み込みの `Linked pull requests` に複数ぶら下げる** | 既定のフィールドで足り、新しい対応付けを作らずに済む |

### 案 D を採る理由（決めること 3）

issue が挙げた 3 案には、それぞれ確かめられた難点がある。

| 案 | 難点 |
| --- | --- |
| A: 各工程の Skill が自分で更新する | 32 個すべてに手が入る。工程を持たない Skill にも判断が要る |
| B: `development-workflow` だけが更新する | **成立しない。** 工程の途中で `development-workflow` を読み直さないことが v9.3.0 の設計判断として確定している（そのために 6 Skill へ横断の案内を 1 行ずつ置いた） |
| C: hook が Pull Request とブランチから推定する | issue 本文のとおり、工程の段階までは推定できない |

**案 D: 工程の切れ目を持つ Skill だけが、共通スクリプトを 1 行呼ぶ。** 判定と API の扱いは
`plugins/ndf/scripts/lib/projects-common.sh` が持ち、各 `SKILL.md` は呼び出しを 1 行書くだけに
する。`worktree` と同じ構造であり、対象は 14 個の `SKILL.md` になる。工程表の 14 行のうち
「設計」だけは専用の Skill を持たないため、書き込む Skill は無い。「レビュー」は
`cross-review` と `pr-review` の 2 つが書き込む。

| Skill | 書き込む値 |
| --- | --- |
| `worktree` | 進行=作業場所の用意、モード、作業ツリー |
| `requirements-design` | 進行=要求と受け入れ条件 |
| `implementation-plan` | 進行=計画、計画ファイル |
| `tdd-cycle` | 進行=実装 |
| `refactoring` | 進行=構造改善 |
| `cross-review` | 進行=レビュー |
| `pr-review` | 進行=レビュー |
| `quality-gates` | 進行=完了判定 |
| `pr` | 進行=Pull Request |
| `plan-to-spec` | 進行=確定仕様化 |
| `merged` | 進行=後片付け |
| `release` | 進行=配布 |
| `release-verification` | 進行=リリース後テスト |
| `retrospective` | 進行=振り返り、Status=Done |

### 決めること 7 への答え

issue は「マージ前の文書を他の作業から読めない状態は変わらない」と書いている。そのとおりで、
**この問題は Projects では解決しない。** アイテム本文へ移すと次の 3 つを失う。

- 版管理。誰がいつ何を変えたかが git の履歴から消える
- レビュー。計画と仕様の変更が Pull Request の差分に現れなくなる
- 検査。`check-markdown-links.py` などの機械的な突き合わせが届かなくなる

失うものが、読めるようになる利得より大きい。計画ファイルはリポジトリに残し、アイテムからは
パスを参照するだけにする。マージ前に他の作業から読ませたい場合は、作業ツリーのパスを
`作業ツリー` フィールドへ書いておく。同じ機械の上なら読める。

## 前提

- 前提 1: 盤面は作成済みで、フィールドは `進行`（14 値）・`モード`（4 値）・`作業ツリー`・
  `計画ファイル` が入っている。`Status` / `Linked pull requests` / `Repository` は既定のまま使う
- 前提 2: open issue 13 件は登録済み。進行中の 4 件（#175 / #176 / #186 / #188）はフィールドも入れた
- 前提 3: 工程表は #188（PR #192）で「配布」の行が増える。この変更は同じ表を触るため、
  #192 の後に積む

## 受け入れ条件

- [ ] 1. `.ndf/projects.json` の形式が決まっており、無いリポジトリでは何も起きない
- [ ] 2. `plugins/ndf/scripts/lib/projects-common.sh` が宣言の読み取りと値の解決を持ち、
      入口のスクリプトは入出力の整形だけを行う
- [ ] 3. 宣言が無い / `gh` が無い / スコープが足りない のいずれでも、終了コード 0 で
      何も出力せずに終わる
- [ ] 4. 工程の切れ目を持つ 14 個の `SKILL.md` に、進行を書き込む案内が 1 行ずつ入っている
- [ ] 5. `development-workflow` に、判定結果（モード）の記録先としての位置づけが書かれている
- [ ] 6. `issue-plan-strategy` に、1 issue = 1 アイテムで Pull Request を複数ぶら下げる旨がある
- [ ] 7. 判定のテストが `plugins/ndf/skills/*/tests/` にあり、外部への通信を伴わない
- [ ] 8. `python3 scripts/check-skill-frontmatter.py` が終了コード 0 で終わる
- [ ] 9. `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] 10. `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 盤面の更新に失敗して工程が止まる | 失敗しても終了コード 0 で抜ける。進行管理は開発の前提条件にしない |
| フィールドの値と工程表がずれる | 値は工程表の行名と一致させる。ずれたときに気づけるよう、値の一覧を検査の対象へ入れることを次の課題とする |
| 認証スコープを持たない利用者が案内に従えない | 宣言が無ければ何も起きない。宣言を置くのは盤面を使う利用者だけである |
| 組織単位の盤面に他リポジトリの課題が混ざる | ビューのフィルタ（`Repository`）で分ける。盤面は分けない |

## 切り戻し手順

宣言ファイルを消せば、すべての呼び出しが何もせずに終わる。盤面そのものは GitHub 側で
削除できる。リポジトリ側の変更は `git revert` で戻る。

## 完了の定義

- [ ] 受け入れ条件 1〜10 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `architecture` モードの検証の段階 1〜4 を通す
- [ ] バッチ 02 の配布後にリリース後テストを行い、実際の開発 1 件で盤面が更新されることを確かめる
- [ ] 振り返りを `docs/development-history/` へ残す
