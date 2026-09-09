# 518 / 524: 工程が止まる 2 件の実装計画

## 関連リンク

- 要求と受け入れ条件: `issues/issue-518-524-workflow-blockers.md`
- 設計: `issues/issue-518-524-design.md`（設計 Pull Request #528 でマージ済み）
- 課題: #518 / #524

## モード

`standard`。`refactor.py` の判定と出力（本番の振る舞い）を変えるバグ修正である。

## 目的と非目的

達成したい状態:

- `cross-refactoring` を手順書どおりに起動して、提案フェーズと `init` の関門を通過できる
- credential helper が応答しない環境でも、push の時点で工程が止まらない

やらないこと:

- credential helper 自体の修復（利用者の環境の問題である）
- `git push` を書く Skill の全数調査（#524 が挙げた 3 本に絞る）
- `cross-review` の証拠ベース化（#156。同じマイルストーンだが触る対象が重ならない）

## 前提

- 前提 1: 退避は `gh` の認証が通っていることを条件とする。`gh` も未認証なら失敗として扱う
- 前提 2: `--scope` の実体の走査は配下 1 段に限る

## 受け入れ条件

要求のファイルにある 9 件をそのまま引き継ぐ。タスクの「満たす受け入れ条件」は次の番号で指す。

| # | 条件 | 検証手段 |
| --- | --- | --- |
| 1 | `start-round` が母集合（`RUNTIMES` / `RUNTIMES_CSV`）を返す | `<crf>/tests/test_start_round_emits_runtimes.py` |
| 2 | 骨組みが参照する変数の出所を機械で検査する | `scripts/tests/test_skill_shell_vars.py` |
| 3 | 提案が母集合の全員に対して起動する | 同上 |
| 4 | 実体を持つ親ディレクトリが関門を通る | `<crf>/tests/test_scope_gate.py` |
| 5 | 関門が返す置き場所は当たった配下の側になる | 同上 |
| 6 | 実体も名前も当たらない `--scope` は止まる | 同上 |
| 7 | 名前で渡す経路はこれまでどおり通る | 同上 |
| 8 | 進行側の push が退避して再試行する | `<crf>/tests/test_git_facts.py` |
| 9 | 手順書 3 本に退避の手がある | `scripts/tests/` の文書検査 |

`<crf>` は `plugins/ndf/skills/cross-refactoring` を指す。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `refactor.py start-round` の出力 | `RUNTIMES` / `RUNTIMES_CSV` を足す | 追加のみ。既存の値と失敗の形は変えない |
| `scope.is_test_location` / `test_locations` | 引数に作業ツリーのパスを足す | 呼び出し側は `refactor_lib` の中だけ。同じ変更で直す |
| `gitfacts.push_head` | 失敗時の退避を足す | 既定の経路は変えない |

## 修正対象

```text
plugins/ndf/scripts/lib/git-credential.sh                     # 新設
plugins/ndf/skills/cross-refactoring/
├── SKILL.md                                                  # 骨組みと退避の記載
├── scripts/refactor_lib/scope.py                             # 判定
├── scripts/refactor_lib/gitfacts.py                          # push_head
├── scripts/refactor_lib/commands/setup.py                    # cmd_start_round
└── tests/
    ├── test_start_round_emits_runtimes.py                    # 新設
    ├── test_scope_gate.py                                    # 追加
    └── test_git_facts.py                                     # 追加
plugins/ndf/skills/pr/SKILL.md                                # 退避の案内
plugins/ndf/skills/fix/SKILL.md                               # 退避の案内
scripts/check-skill-shell-vars.py                             # 新設
scripts/tests/test_skill_shell_vars.py                        # 新設
scripts/tests/test_push_fallback_docs.py                      # 新設
```

## タスク分解

**機能の単位で分ける。** 4 つのタスクはそれぞれ独立して検証でき、受け入れ条件を 1 つ以上満たす。

### Task 1: 母集合を `start-round` が返す

- **対象ファイル:** `<crf>/scripts/refactor_lib/commands/setup.py` /
  `<crf>/tests/test_start_round_emits_runtimes.py`（新設）
- **変更内容:** `cmd_start_round` の `statefile.emit` へ、状態ファイルの `runtimes` から
  作った `RUNTIMES` と `RUNTIMES_CSV` を足す。`_emit_init` と同じ形にする
- **満たす受け入れ条件:** 1
- **進め方:** 失敗するテスト（emit に `RUNTIMES` が無いこと）→ 足す → 既存テストを通す

### Task 2: 骨組みの変数の出所を検査する

- **対象ファイル:** `scripts/check-skill-shell-vars.py`（新設）/
  `scripts/tests/test_skill_shell_vars.py`（新設）/ `<crf>/SKILL.md`
- **変更内容:** `SKILL.md` の「実行」節の bash から参照する変数を集め、代入・`for`・
  `refactor.py` の副コマンドが `emit` するキーを出所として突き合わせる。`emit` のキーは
  構文木から読み、`cmd_<名前>` の関数名を副コマンド名へ対応させる。ヘルパー関数
  （`_emit_init`）を経由する呼び出しも 1 段だけたどる
- **満たす受け入れ条件:** 2 / 3
- **進め方:** 未定義の変数を混ぜた骨組みで落ちるテスト → 実装 → 現行の骨組みで通ること

### Task 3: 関門が実体を見る

- **対象ファイル:** `<crf>/scripts/refactor_lib/scope.py` / 同じ関数を呼ぶ
  `commands/setup.py` と `refactor_lib/gitfacts.py` / `<crf>/tests/test_scope_gate.py`
- **変更内容:** `is_test_location(path, work)` が、名前で当たらないときに `work` を起点に
  配下 1 段を走査する。`test_locations(scope, work)` は**当たった置き場所の側**を返す
- **満たす受け入れ条件:** 4 / 5 / 6 / 7
- **進め方:** 4 つの入力（実体を持つ親 / 名前で当たる / 実体も名前も無い / まだ無い置き場所）の
  テスト → 実装 → `covered_by_roots` と組み合わせた通し

### Task 4: push の退避

- **対象ファイル:** `plugins/ndf/scripts/lib/git-credential.sh`（新設）/
  `<crf>/scripts/refactor_lib/gitfacts.py` / `<crf>/SKILL.md` /
  `plugins/ndf/skills/pr/SKILL.md` / `plugins/ndf/skills/fix/SKILL.md` /
  `<crf>/tests/test_git_facts.py` / `scripts/tests/test_push_fallback_docs.py`（新設）
- **変更内容:** 共通層へ `ndf_git_with_fallback` を置き、`push_head` が失敗時に 1 度だけ
  退避して再試行する。**空の値を先に置く**（`-c credential.helper=` →
  `-c credential.helper='!gh auth git-credential'`）。手順書 3 本は同じコマンドを案内する
- **満たす受け入れ条件:** 8 / 9
- **進め方:** 1 度目の失敗を模したテスト（2 度目の引数の順序を見る）→ 実装 → 手順書の記載と
  共通層のコマンドが一致することの検査

## 影響範囲

| 影響を受けるもの | 内容 |
| --- | --- |
| `cross-refactoring` の起動 | 骨組みの変数と `--scope` の関門が変わる |
| `refactor_lib` の内部の呼び出し | `is_test_location` / `test_locations` の署名が変わる |
| 手順書 3 本 | push の手順に退避の記載が増える |
| 継続的統合 | 検査が 1 本増える |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `scope.py` の署名変更が、走査していない呼び出し元を壊す | タスクごとにテストを通す。呼び出し元は `refactor_lib` の中に閉じることを実装の前に数える |
| 退避のテストが `git` の実物を呼び、環境で結果が変わる | 呼び出しを差し替えて引数の並びだけを見る。実物の `git` は呼ばない |
| 骨組みの検査が既存の `SKILL.md` を落とす | 現行の骨組みで通ることをテストで固定してから、検査を継続的統合へ載せる |

**「先に構造を整える」は選ばない。** 触るのは 3 ファイルの関数 4 つで、いずれも
`refactor_lib` の中に閉じている。

## 切り戻し手順

いずれのタスクも 1 コミットで戻せる。データの移行を伴わない。共通層の新設ファイルは
呼び出し元と同じコミットへ入れる（片方だけ残ると読み込みに失敗する）。

## 完了の定義

- [ ] 受け入れ条件 9 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` の終了コードが 0
- [ ] `python3 scripts/check-skill-shell-vars.py` の終了コードが 0
- [ ] `python3 scripts/check-doc-line-limit.py` / `check-skill-frontmatter.py` /
      `check-skill-repo-assumptions.py` の終了コードが 0
