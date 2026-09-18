# #561 / #623: 後片付けを止めずに通し、まとまりの課題を終わりの工程で閉じる（実装プラン）

## 関連リンク

- 課題: [#561](https://github.com/devbasex/ai-plugins/issues/561) / [#623](https://github.com/devbasex/ai-plugins/issues/623)（マイルストーン 18）
- 要求と受け入れ条件: [issue-561-623-requirements.md](issue-561-623-requirements.md)（41 件）
- 設計: [issue-561-623-design.md](issue-561-623-design.md)
- 決定の記録: [issue-561-623-design-decisions.md](issue-561-623-design-decisions.md)（11 件）

## モード

`standard`。Skill の手順（本番の振る舞い）を変える。公開インタフェース（`/ndf:merged [PR番号]` の引数）は変えない。

## 目的と非目的

達成したい状態:

- マージ後の後片付けが、戻せる操作のために止まらない。止まるのは git が拒んだ対象だけになる
- 実行前確認の要否が、`AUTHORING.md` の 3 つの問いで決まる
- まとまりの課題を閉じる手順が `progress-tracking` の 1 か所にあり、終わりの工程から呼ばれる

やらないこと（要求文書「含まない」のまま）:

- 関門の数と形を変える／盤面の自動化の設定を変える
- `development-workflow/SKILL.md` の「`/goal` の引数として呼ばれたとき」の節を触る（#550 #657 が所有）
- `parallel-work.md` を触る（#540 #541 #621 が所有）
- 閉じる語を Pull Request の本文から外す
- 版数・`CHANGELOG.md`（配布の工程が書く）

## 前提

- 前提 1: `development-workflow/SKILL.md` は 500 行ちょうどで、**行数を増やさない**（決定 11）。増えていないことを `wc -l` で確かめる
- 前提 2: `AUTHORING.md` は 494 行で上限まで 6 行。足す分を節の散文を詰めて吸収する（設計「未確認のまま残ること」）
- 前提 3: `release/SKILL.md` は #554 の実装担当も触る。競合したら後からマージする側が解く（#741 の決定 3）
- 前提 4: 新しいスクリプトは作らない。`closing-issues.sh` / `progress-record.sh` / `projects-sync.sh` は変えない（設計「変えないもの」）

## 受け入れ条件

要求文書の A1〜A12 / B1〜B8 / C1〜C13 / D1〜D3 / E1〜E5 の 41 件をそのまま受ける。
検証手段は設計の「テスト設計」の表が持つ。

## 代替案と採否

設計の工程で決着済み（決定 1〜11）。実装では代替案を作らない。
実装で残っていた選択は次の 2 つだけで、どちらも前提のとおりに進める。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `AUTHORING.md` の散文を詰めて 500 行に収める | 採用 | 「判断根拠」の列は棚卸台帳への参照としてこの表にしかない |
| B | 適用表の「判断根拠」の列を棚卸台帳へ移す | 不採用 | A で収まれば参照が 1 段増えない（収まらなければ B へ落ちる） |

## ドメイン用語

要求文書の「用語」の表をそのまま使う（実行前確認 / 関門 / 取り消せる操作 / まとまり /
まとまりの課題 / 終わりの工程 / 進行側 / チャネルを分けたリポジトリ）。

## 不変条件

- 関門は常に 2 つ（`development-workflow/SKILL.md` の表が 2 行）
- `plugins/ndf/skills/*/SKILL.md` のうち `gh issue close` を書くのは `progress-tracking` だけ
- `plugins/ndf/skills/*/SKILL.md` のうち `status "Done"` を書くのは `progress-tracking` だけ
- `merged` は課題を閉じない
- 退避が 1 件でも失敗した作業ツリーは、同意の有無にかかわらず消さない

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `/ndf:merged [PR番号]` | 引数は変えない。止まる回数と完了報告の項目が変わる | 変えない |
| `/ndf:progress-tracking` | 「まとまりを閉じる」を節として足す。既存の呼び方（工程名の記録）は変えない | 追加のみ |
| `closing-issues.sh` / `progress-record.sh` / `projects-sync.sh` | 変えない | 変えない |
| データスキーマ | 無い | — |

## 修正対象

```text
plugins/ndf/
├── README.md                                       # 292 行目の段落
├── scripts/lib/README.md                           # closing-issues.sh の行
└── skills/
    ├── AUTHORING.md                                # 2 節
    ├── development-workflow/
    │   ├── SKILL.md                                # 関門の節の冒頭 2 段落だけ（行数を増やさない）
    │   ├── references/stage-notes.md               # 冒頭の後片付けの段落
    │   └── tests/test_workflow_hooks.py            # 1 件を移し、3 件を新設
    ├── merged/SKILL.md
    ├── pr/SKILL.md                                 # git commit の行の直後 2 か所
    ├── progress-tracking/SKILL.md                  # 2 節を新設
    ├── release/SKILL.md
    ├── release-verification/SKILL.md
    └── retrospective/SKILL.md
```

## タスク分解

**進め方は全タスク共通で「失敗するテストを足す → 文面を直す → 検査を通す」。** 変えるのは
Skill の手順の文書であるため、テストは既存の `test_workflow_hooks.py` と同じ形（本文の
文字列の検査）で書く。**文面の突き合わせだけで足りる条件（A3・A4・A9 など）はテストを
増やさず、レビューで確かめる**（設計のテスト設計の表が担当を分けている）。

### Task 1: 閉じる手順を `progress-tracking` の 1 か所にする

- **対象ファイル:** `plugins/ndf/skills/progress-tracking/SKILL.md`、`plugins/ndf/skills/merged/SKILL.md`、`plugins/ndf/skills/development-workflow/tests/test_workflow_hooks.py`
- **変更内容:**
  - `progress-tracking` に「工程の単位と記録する課題」と「まとまりを閉じる」を新設する。
    冒頭の「この Skill は順序を持たない」に例外を足し、「呼び方」の `status` の段落を
    「終わりの工程で `Done` を書くのは『まとまりを閉じる』だけ」に書き換える
  - `merged` の「閉じ忘れた issue を閉じる」の節を、まとまりの課題の OPEN を報告するだけの
    節へ差し替える。閉じる語の注意は `progress-tracking` へ移す
  - テスト: `test_the_merged_skill_closes_issues_with_their_repository` の読む先を
    `progress-tracking` へ移す。`test_only_progress_tracking_closes_issues` を新設
- **満たす受け入れ条件:** C2 C8 C9 C10 C11 C13 D1 D2 D3 E1
- **進め方:** 先に新設テストを足して落ちることを見る → 文面を直す → `pytest` で通す

### Task 2: 後片付けを止めずに通す

- **対象ファイル:** `plugins/ndf/skills/merged/SKILL.md`
- **変更内容:** frontmatter の `description`、「削除前の同意取得（必須）」を「止まる条件」へ
  差し替え、クリーンアップの手順 4〜8、「マージ済みブランチの整理」、「作業完了報告」、
  「次の工程」、末尾の進行の記録の行。無視されたファイルの退避（決定 5）と、退避の失敗の
  「未完了」（A12）を書く
- **満たす受け入れ条件:** A1 A3 A4 A5 A6 A7 A8 A9 A10 A11 A12
- **進め方:** テスト駆動を適用しない（文面の突き合わせでしか確かめられない条件が大半）。
  代わりに**決定 5 の退避のコード例を一時リポジトリで実行し、実測 5・6・7・8 行目を再現する**。
  A10 は `check-skill-frontmatter.py` で機械的に見る

### Task 3: 実行前確認の基準を `AUTHORING.md` へ置く

- **対象ファイル:** `plugins/ndf/skills/AUTHORING.md`、`plugins/ndf/skills/development-workflow/SKILL.md`、`plugins/ndf/skills/development-workflow/references/stage-notes.md`、`plugins/ndf/README.md`、`plugins/ndf/skills/development-workflow/tests/test_workflow_hooks.py`
- **変更内容:** 3 つの問い・守り方 3 つ・適用表（7 行）へ書き換える。
  `allowed-tools` の節の 1 行を基準と矛盾しない形にする。関門の節の冒頭 2 段落へ原則を織り込む
  （**行数を増やさない**）。`stage-notes.md` の後片付けの段落と `README.md` の段落を追随させる。
  テスト `test_the_gates_stay_two` を新設
- **満たす受け入れ条件:** B1 B2 B3 B4 B5 B6 B7 B8
- **進め方:** 先に `test_the_gates_stay_two` を足す → 文面を直す → `wc -l` で 500 行以下を確かめる

### Task 4: 終わりの工程から「まとまりを閉じる」を呼ぶ

- **対象ファイル:** `plugins/ndf/skills/release/SKILL.md`、`plugins/ndf/skills/release-verification/SKILL.md`、`plugins/ndf/skills/retrospective/SKILL.md`、`plugins/ndf/skills/pr/SKILL.md`、`plugins/ndf/scripts/lib/README.md`
- **変更内容:**
  - `release`: 開始条件、手順 3 に `## 配布の記録` のブロック、出力物の置き場所、
    飛ばしたときの `段階: 配布なし`、「蓄積した課題を手入れする」の条件
  - `release-verification`: 手順 4 の進み先、出力物の置き場所と表の `課題` の列、
    「まとまりを閉じる」を呼ぶ節を新設
  - `retrospective`: 進行の記録の行から `Done` を外し、「まとまりを閉じる」へ一本化
  - `pr`: `git commit` の行の直後 2 か所に「コミットメッセージに閉じる語を書かない」
  - `scripts/lib/README.md`: `closing-issues.sh` の使い手
  - テスト: `test_the_closing_step_comes_before_issue_upkeep` を新設
- **満たす受け入れ条件:** C3 C5 C6 C7 C12
- **進め方:** 先に新設テストを足す → 文面を直す → 設計の awk のコード例を実測で確かめる

### Task 5: 生成物の同期と検査

- **対象ファイル:** `plugins/ndf/dev.*` などの生成物
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行し、生成された差分をコミットする
- **満たす受け入れ条件:** E2 E3 E4 E5
- **進め方:** 検査コマンドの終了コードで見る

## 影響範囲

- `merged` を使う全員（止まる回数が減り、完了報告の項目が増える）
- チャネルを分けたリポジトリの課題の閉じる時点（起点へのマージ → 終わりの工程）
- `release` / `release-verification` の出力物の置き場所（会話 → Pull Request）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `release/SKILL.md` を #554 の担当も触る | **実装の後の構造改善で足りる。** 触る節が違う（あちらは 4 行）。競合したら後からマージする側が解く |
| `development-workflow/SKILL.md` を #550 が後から触る | **タスクごとにテストを通す。** 触るのは関門の節だけで、`/goal` の節を開かない。行数を増やさないことを `wc -l` で固定する |
| 退避の手順が実測と食い違う | **先に構造を整える対象ではない。** 決定 5 のコード例を一時リポジトリで再現し、実測 5〜8 行目を実装の検証に含める（AGENTS.md「書く前に実行して確かめる」） |
| 設計の awk のコード例が実際の `awk` で動かない | 同上。C11 / C12 / D2 の入力例をそのまま流して出力を確かめる |

## 切り戻し手順

Skill の文書だけを変える。データ移行は無い。Pull Request を閉じるか revert すれば
すべて戻る。生成物は `bash scripts/build-runtime-plugins.sh` で作り直せる。

## 完了の定義

- [ ] 受け入れ条件 41 件について、満たした／満たせなかった（理由）が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q`（E1）
- [ ] `python3 scripts/check-skill-frontmatter.py`（A10）
- [ ] `claude plugin validate .`（E2）
- [ ] `python3 scripts/check-cross-skill-refs.py` / `python3 scripts/check-markdown-links.py`（E3）
- [ ] `bash scripts/build-runtime-plugins.sh` の後に `git status --short` が空（E4）
- [ ] `wc -l plugins/ndf/skills/development-workflow/SKILL.md` が 500 以下（決定 11）
- [ ] 構造改善（`cross-refactoring`）と実装レビュー（`cross-review`）が収束している
