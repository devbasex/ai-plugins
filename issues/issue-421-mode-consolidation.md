# #421: `architecture` を廃止し `standard` へ改名する（実装 1 本目）

## 関連リンク

- 課題: #421
- 要求と受け入れ条件: [issue-421-423-391-modes-and-stages/01-requirements.md](issue-421-423-391-modes-and-stages/01-requirements.md)
- 設計: [issue-421-423-391-modes-and-stages/02-design.md](issue-421-423-391-modes-and-stages/02-design.md)
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/434

## モード

`architecture`。モード名という公開インタフェースの削除を含み、変更が Skill・共通層・テスト・
説明文書の複数モジュールにまたがる。

## 目的と非目的

**達成したい状態**: モードの一覧から `architecture` が消え、その内容が `standard` として
残っている。**改名であり、工程の中身は変えない。**

**やらないこと**:

- 運用モード `operation` の追加（本 2）
- 工程表の行の追加と改名（本 3）
- 記録済みの名前の移行（本 4）
- 工程の必須・条件付きの別の変更（**旧 `architecture` の値をそのまま引き継ぐ**）

## 前提

| # | 前提 |
| --- | --- |
| 1 | 旧 `standard` に固有の記述は、新 `standard` の記述に置き換えられて消える。残すと同じモード名に 2 つの水準が並ぶ |
| 2 | この本では列の数が 4 から 3 へ減る。`operation` は本 2 で足す |
| 3 | 列の並びを高さ順にするのは本 2 で行う（`operation` が入ってから並べ替える） |

## 受け入れ条件

要求文書の A 群に対応する。

- [ ] A1. `WF_MODES` が `light` / `standard` / `legacy-refactor` になり、`architecture` を含まない
- [ ] A2. `WF_STAGE_MATRIX` の列数が `WF_MODES` の要素数と一致する
- [ ] A3. `WF_MODE_HEIGHT` が全要素を持ち、列の位置から導いていない
- [ ] A4. `SKILL.md` の判定の基準の表と工程表が、同じ一覧を同じ並びで示す
- [ ] A6. `architecture` を現行のモード名として書いている箇所が 0 件になる（**対象 54 か所**）
- [ ] A7. 周辺 Skill が旧 `architecture` に課していた条件を、新 `standard` へそのまま引き継ぐ

**検証手段**: `uv run --with pytest pytest scripts/tests plugins/ndf -q` と
`git grep architecture`、および検査 10 本。

## 置換の対象と対象外

**59 か所のうち 5 か所は置換しない。** 工程名でもモード名でもないため。

| 箇所 | 件数 | 対象外である理由 |
| --- | ---: | --- |
| `plugins/ndf/skills/plan-to-spec/SKILL.md` | 2 | `docs/architecture/` は仕様書の置き場所の候補となるディレクトリ名 |
| `references/stage-completeness.md:123` | 1 | 「#161 の通過工程（architecture）」は過去の実測の記録 |
| `CLAUDE.md` | 2 | 版ごとの履歴（v10.3.0 / v10.5.1 の記述） |

**`stage-completeness.md:56` の JSON の例示は対象に含める。** 読み手が現行の語彙で読むため。

## 修正対象

| ファイル | 件数 | 変更の性質 |
| --- | ---: | --- |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 10 | 判定の基準の表・工程表の列・標準フローの図 |
| `references/workflow-modes.md` | 9 | 境界事例とモード別の詳細 |
| `tests/test_stage_check.py` | 8 | モード名を含むテスト |
| `tests/test_workflow_guard.py` | 3 | 同上 |
| `tests/test_workflow_evidence.py` | 3 | 同上 |
| `scripts/lib/workflow-common.sh` | 3 | `WF_MODES` / `WF_STAGE_MATRIX` / `WF_MODE_HEIGHT` |
| `plugins/ndf/skills/design/SKILL.md` | 3 | モードごとの成果物の表 |
| `references/stage-notes.md` | 2 | レビューと構造改善の記述 |
| `references/stage-completeness.md` | 1 | JSON の例示 |
| `plugins/ndf/scripts/tests/test_progress_record.py` | 2 | モード名を含むテスト |
| `docs/specifications/ndf-design-phase.md` | 2 | 確定仕様 |
| `plugins/ndf/skills/refactoring/SKILL.md` | 1 | 構造改善の指し先 |
| `plugins/ndf/skills/quality-gates/references/definition-of-done.md` | 1 | 完了の定義の節名 |
| `plugins/ndf/skills/quality-gates/SKILL.md` | 1 | 段階の表 |
| `references/projects-tracking.md` | 1 | 盤面のモードの値の一覧 |
| `plugins/ndf/skills/design/references/design-template.md` | 1 | 図の水準 |
| `plugins/ndf/skills/cross-refactoring/SKILL.md` | 1 | 構造改善の指し先 |
| `plugins/ndf/scripts/lib/projects-common.sh` | 1 | 盤面の値 |
| `docs/specifications/ndf-workflow-unit-and-gates.md` | 1 | 確定仕様 |

## タスク分解

**機能単位で分ける。** 各タスクの完了時点でテストが通る状態にする。

### Task 1: 共通層のモードの一覧を変える

- **対象**: `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh`
- **変更**: `WF_MODES` から `architecture` を除き、`WF_STAGE_MATRIX` の列を 4 から 3 へ。
  旧 `standard` の列を消し、旧 `architecture` の列を `standard` の位置へ。
  `WF_MODE_HEIGHT` から `architecture` を除く
- **満たす条件**: A1 / A2 / A3
- **進め方**: 先に `SKILL.md` の工程表を直す（テストが工程表を正とするため）。
  `test_workflow_stage_matrix.py` が突き合わせる

### Task 2: 判定の基準と工程表を直す

- **対象**: `development-workflow/SKILL.md`
- **変更**: 判定の基準の表から `architecture` の行を消し、その条件を `standard` の行へ。
  工程表の列を 3 へ。標準フローの図の経路を直す
- **満たす条件**: A4 / A6
- **進め方**: Task 1 と同じコミットにする（テストが両方を突き合わせるため分けられない）

### Task 3: 参照の記述を直す

- **対象**: `references/workflow-modes.md` / `stage-notes.md` / `stage-completeness.md` /
  `projects-tracking.md`
- **変更**: モード名の置き換え。`workflow-modes.md` の判定に使う問いと境界事例、
  モード別の詳細から旧 `standard` の節を消す
- **満たす条件**: A6
- **進め方**: 置換ではなく 1 件ずつ読んで直す（旧 `standard` の記述が消える箇所がある）

### Task 4: 周辺 Skill の記述を引き継ぐ

- **対象**: `quality-gates/SKILL.md` / `quality-gates/references/definition-of-done.md` /
  `design/SKILL.md` / `design/references/design-template.md` / `refactoring/SKILL.md` /
  `cross-refactoring/SKILL.md`
- **変更**: `architecture` を `standard` へ。**旧 `standard` に固有の記述は消える**
  （`definition-of-done.md` の `standard` の節、`design/SKILL.md` の「仕様と同じファイルの
  別の節でもよい」）。条件や必須の別は変えない
- **満たす条件**: A6 / A7
- **進め方**: 決定 12 に従う。条件を緩めない

### Task 5: 盤面の値とテストを直す

- **対象**: `plugins/ndf/scripts/lib/projects-common.sh` / `tests/test_stage_check.py` /
  `test_workflow_guard.py` / `test_workflow_evidence.py` / `scripts/tests/test_progress_record.py`
- **変更**: モード名の置き換え
- **満たす条件**: A6
- **進め方**: テストを直したら実行して通ることを確かめる

### Task 6: 確定仕様を直す

- **対象**: `docs/specifications/ndf-design-phase.md` /
  `docs/specifications/ndf-workflow-unit-and-gates.md`
- **変更**: モード名の置き換え
- **満たす条件**: A6

## 影響範囲

**モードを受け取る側の Skill すべて。** 判定基準を持つのは `development-workflow` だけで、
他は判定結果を受け取る。名前が変われば受け取る側の記述も変わる。

**記録済みの値は本 4 で移行する。** この本では触らない。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 機械的な置換で対象外の 5 か所を壊す | 置換ではなく 1 件ずつ読んで直す |
| 旧 `standard` の記述が残り、同じ名前に 2 つの水準が並ぶ | Task 3 と 4 で消えることを明示的に確かめる |
| 工程表と `WF_STAGE_MATRIX` が食い違う | `test_workflow_stage_matrix.py` が突き合わせる |

## 切り戻し手順

Pull Request をマージする前は、ブランチを捨てれば戻る。マージ後は revert で戻る。
**データの移行を伴わないため、巻き戻しの制約は無い。**

## 完了の定義

- [ ] 受け入れ条件 A1 / A2 / A3 / A4 / A6 / A7 をすべて満たす
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が終了コード 0
- [ ] 検査 10 本が終了コード 0
- [ ] `git grep architecture` の結果が対象外の 5 か所だけになる
