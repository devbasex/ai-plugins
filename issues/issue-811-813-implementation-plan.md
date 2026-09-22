# cross-review / cross-refactoring: codex と claude が利用上限で止まっても同じラウンドで起動し直され、PATH に読めないディレクトリがあると始まらない → 上限を理由として報告して起動し直さず、CLI が欠けても使える者だけで始まる（#811 #813）

## 関連リンク

- 要求と受け入れ条件: [issue-811-813-requirements.md](issue-811-813-requirements.md)（AC1〜AC14）
- 設計: [issue-811-813-design.md](issue-811-813-design.md)（決定 1〜7、実測の表）
- 課題: #811 / #813。直った後に閉じる課題: #478 #619 #729（10.16.1 のリリース後テストで AC12〜AC14 が合格したら）

## モード

`standard`。本番の振る舞いのバグ修正であり、対象にテストがある。

## 用語の対応表

本文は左の業務用語で書く。識別子は表とコードブロックにだけ置く（設計文書の表と同じ）。

| 業務用語 | 識別子 |
| --- | --- |
| 監視 | `plugins/ndf/scripts/lib/monitor.py` |
| 利用上限の照合の表 | `USAGE_LIMIT_FATAL` |
| 行単位の照合 | `_scan_patterns` |
| 標準エラーの記録 | 担当ごとの `<stem>-err.log` |
| 認証の確認 | `plugins/ndf/scripts/lib/auth.py` の `probe_auth` |
| 確認コマンドを 1 つ走らせる関数 | `auth._run_probe` |
| 起動できない例外 | `OSError` とその下位のすべて |
| 使える者の解決 | `plugins/ndf/scripts/lib/assignment.py` の `resolve_participants` |
| 開始の手順 | cross-review の `state.py init`、cross-refactoring の `refactor.py init` |

## 目的と非目的

達成したい状態:

- 利用上限で止まった担当が、実物の文言から理由「利用上限」として報告され、同じラウンドで起動し直されない
- 確認コマンドを起動できない CLI が、例外ではなく「通らない」として外れ、使える者だけで収束ループが始まる

やらないこと:

- 実物の出所が無い文言を照合へ足すこと（決定 4）
- 起動した claude の標準出力の照合を変えること（決定 5）
- 理由の語彙・監視の終了コード・標準出力のキーを変えること
- 必須の外部コマンドの呼び出しの例外処理を変えること

## 前提

- 前提 1: 足す 2 行の照合は、設計の「実測」の実物 11 入力に一致し、誤検知の 12 入力に一致しない。テストの入力はその表から写す
- 前提 2: 読めないディレクトリを使うテストは、権限が効かない実行者（root）では条件が成り立たない。その場合は例外を差し込む形のテストで同じ理由の文言を確かめる

## 受け入れ条件

要求の AC1〜AC11 をこの変更で満たす。AC12〜AC14 は 10.16.1 のリリース後テストで確かめるため、この Pull Request の範囲外である。

- [ ] AC1〜AC4: 実物の文言が利用上限として止まり、10.16.0 で合格した 4 形も止まり続ける（監視のテスト）
- [ ] AC5 / AC6: 誤検知の 12 形で止まらず、入力は実測の表の写しである（監視のテスト）
- [ ] AC7〜AC9: 起動できない CLI が「通らない」として返り、見つからない・時間切れの理由は変わらない（認証の確認のテスト）
- [ ] AC10 / AC11: 読めないディレクトリを含む PATH で CLI が 1 つ欠けても、2 つの開始の手順が終了コード 0 で終わる（開始の手順のテスト）
- [ ] 退行しないこと: 既存のテストがすべて通る（`uv run --with pytest pytest scripts/tests plugins/ndf -q`）

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 行頭に固定した 2 行の照合 | 書き出しと種類の差し込みを 1 行で読む | 採用 | 設計の決定 1〜3。文書の引用・差分の行に一致しない |
| 形ごとに 1 行ずつ足す | 週・セッション・支出などを個別に書く | 不採用 | CLI が種類を増やすたびに照合が遅れる |
| 権限の例外だけを捕まえる | `PermissionError` に限って「通らない」とする | 不採用 | 実行形式でないファイルで同じ落ち方をする（決定 6） |

## 不変条件

- 監視の結末の理由の語彙は `usage_limit` / `early_error` / `cli_timeout` / `missing` / `ok` のままである
- 認証の確認は例外を上げない。どの CLI の確認が失敗しても、残りの確認が続く

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（監視の終了コード・標準出力のキー・開始の手順の引数） | 無し | 変えない |
| データ（状態ファイル・監視の結果ファイル） | 無し | 変えない |

## 修正対象

- `plugins/ndf/scripts/lib/monitor.py`
- `plugins/ndf/scripts/lib/auth.py`
- `plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py`
- `plugins/ndf/scripts/tests/test_auth_probe.py`
- `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py`
- `plugins/ndf/skills/cross-refactoring/tests/test_init.py`
- 配布物の同期（`bash scripts/build-runtime-plugins.sh` が揃える生成物）

## タスク分解

### Task 1: 実物の文言を利用上限として読む

- **対象ファイル:** `plugins/ndf/skills/cross-review/tests/test_monitor_usage_limit.py`、`plugins/ndf/scripts/lib/monitor.py`
- **変更内容:** 設計の「実測」の実物 11 入力と誤検知 12 入力をテストの定数にし、利用上限の照合の表へ 2 行を足す。足す行は行頭で始まる形だけを読み、再試行の上限は最後の状態が 429 のときだけ一致する
- **満たす受け入れ条件:** AC1〜AC6
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: 起動できない確認コマンドを「通らない」として返す

- **対象ファイル:** `plugins/ndf/scripts/tests/test_auth_probe.py`、`plugins/ndf/scripts/lib/auth.py`
- **変更内容:** 確認コマンドを 1 つ走らせる関数が起動できない例外を捕まえ、理由に起動できなかった理由を入れて返す。見つからない例外と時間切れの理由は変えない。テストは実際に権限を外した一時ディレクトリを PATH に置く形と、例外を差し込む形の両方を置く
- **満たす受け入れ条件:** AC7〜AC9
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 読めない PATH でも 2 つの開始の手順が始まる

- **対象ファイル:** `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py`、`plugins/ndf/skills/cross-refactoring/tests/test_init.py`
- **変更内容:** 認証の確認を差し替えずに、読めないディレクトリを含む PATH と、CLI を 1 つ欠いた状態で開始の手順を通すテストを足す
- **満たす受け入れ条件:** AC10 / AC11
- **進め方:** 失敗するテスト → 通す（Task 2 の実装で通る）→ 整理

## 影響範囲

- 収束ループ 2 つ（cross-review / cross-refactoring）の開始の手順と、担当の監視
- 配布物（4 ランタイム分の生成物）は同期のコマンドで揃える

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 照合の追加で、文書やテストの本文を読み上げた行に一致する | 行頭に固定し、誤検知 12 形をテストの入力にする。触る範囲が狭くテストが厚いため、実装の後の構造改善で足りる |
| 権限を外したディレクトリが root では効かない | 例外を差し込むテストを併置し、どちらの実行者でも理由の文言を確かめる |
| 照合の表を写した配布物が古いまま残る | 同期のコマンドを実行し、差分をコミットに含める |

## 切り戻し手順

- この Pull Request を revert すれば元へ戻る。データ移行も設定の変更も伴わない

## 完了の定義

- [ ] AC1〜AC11 を満たし、条件ごとにテストが対応している
- [ ] 全体テストが通る（`uv run --with pytest pytest scripts/tests plugins/ndf -q`）
- [ ] 配布物の同期と Skill の frontmatter の検査が通る
