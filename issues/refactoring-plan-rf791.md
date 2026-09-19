# 改修計画 — devbasex/ai-plugins #791

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/scripts/lib, plugins/ndf/skills/cross-review/scripts, plugins/ndf/scripts/tests, plugins/ndf/skills/cross-review/tests
- 着手前のテスト: uv run --with pytest pytest scripts/tests plugins/ndf -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | kiro | 検証中 | 1 |

**なぜ**: 終了コード 0 でも UNAUTHENTICATED_MARKERS を含む出力を未認証と判定する分岐が固定されていない。これはこのモジュールの存在理由そのものだが未固定である

**手順**: 1. subprocess.run を差し替え、returncode 0 かつ stdout に 'not logged in' を含む結果を返す
2. check_auth(['codex'], ...) を呼ぶ
3. results['codex']['ok'] が False であることを確認する
4. failed があるため die が一度呼ばれることを確認する

### R1-002 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | kiro | 未着手 | 0 |

**なぜ**: 確認コマンドが見つからないとき（FileNotFoundError）に ok=False とし detail を 'コマンドが見つかりません' にする分岐が固定されていない

**手順**: 1. subprocess.run を差し替え、FileNotFoundError を送出させる
2. check_auth(['codex'], ...) を呼ぶ
3. results['codex']['ok'] が False であることを確認する
4. results['codex']['detail'] に見つからない旨が入り、die が一度呼ばれることを確認する

### R1-003 — `plugins/ndf/scripts/lib/monitor_outcome.py#append_journal`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | unit | — | agy | 検証中 | 1 |

**なぜ**: monitor_outcome.py には read_journal の単体テストはあるが、ペアとなる append_journal の単体テストが存在しない。親ディレクトリの自動生成や非ASCII文字（UTF-8）の保持を含め、複数回の追記によって順序通りに記録・復元できる正常系が単体レベルで未固定である。

**手順**: 1. 一時ディレクトリと複数の outcome 辞書を準備する
2. append_journal を複数回呼び出して追記する
3. read_journal で読み出し、追記された順序と内容が期待通り一致することを検証する

### R1-004 — `plugins/ndf/scripts/lib/monitor_outcome.py#default_result_path`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | unit | — | agy | 未着手 | 0 |

**なぜ**: read_launch_outcome の既定フォールバック先として使われる公開関数 default_result_path について、パス命名規則（<tmp_dir>/<stem>-result.json）を直接検証する単体テストが存在しない。

**手順**: 1. tmp_dir と stem を準備する
2. default_result_path(tmp_dir, stem) を呼び出す
3. 返された Path が期待される <tmp_dir>/<stem>-result.json と等しいことを検証する

### R1-005 — `plugins/ndf/scripts/lib/monitor_outcome.py#relaunch_same_agent`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | agy | 未着手 | 0 |

**なぜ**: relaunch_same_agent は再試行可否の判定に使われる。既存テストは定義済み語彙（REASONSの9語）のみをテストしており、reason=None や空文字列 ""、未知の理由文字列が渡された場合に True を返す境界値の挙動が固定されていない。

**手順**: 1. None、空文字列 ""、未知の理由文字列を引数として準備する
2. relaunch_same_agent を呼び出す
3. いずれも戻り値が True であることを検証する

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | error | 1 ラウンドの採用上限 5 件を超えた |
