# 改修計画 — devbasex/ai-plugins #791

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/scripts/lib, plugins/ndf/skills/cross-review/scripts, plugins/ndf/scripts/tests, plugins/ndf/skills/cross-review/tests
- 着手前のテスト: uv run --with pytest pytest scripts/tests plugins/ndf -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | kiro | 採用 | 1 |

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
| normal | unit | — | agy | 採用 | 1 |

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

## ラウンド 2（実装 agy / レビュー codex / kiro）

### R2-001 — `plugins/ndf/scripts/lib/refresh.py#compare`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | kiro | 採用 | 1 |

**なぜ**: compare は取得失敗・前回記録なし・一致・不一致の 4 分岐を返す純関数だが、対象範囲のどこでも固定されていない。instructions-check の既存テストは refresh.fetch をスタブへ差し替えるため、compare 本体は一度も通らない

**手順**: 1. ok=False の FetchResult を作り compare(result, 'sha256:aa') を呼ぶ
2. ok=True で fingerprint を持つ FetchResult を作り、previous を None・空文字・同じ指紋・異なる指紋の 4 通りで呼ぶ
3. 実行して得た戻り値（取得失敗・前回記録なし・一致・不一致を表す文字列）を期待値として固定する

### R2-002 — `plugins/ndf/scripts/lib/refresh.py#fetch`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | kiro | 取り消し | 1 |

**なぜ**: fetch は取得の失敗を例外にせず FetchResult(ok=False, error=...) へ畳む分岐を持ち、error の文言は _reason が例外の種類ごとに書き分ける。この経路は未固定で、instructions-check のテストは fetch 自体を差し替えるため通らない。opener は差し替え用に設計された引数である

**手順**: 1. opener に OSError を送出する呼び出し可能を渡し fetch(url, timeout, opener) を呼ぶ
2. urllib.error.HTTPError と urllib.error.URLError を送出する opener でも同様に呼ぶ
3. 3 通りとも ok が False で、実行して得た error の文言（種類ごとに異なる）を期待値として固定する

### R2-003 — `plugins/ndf/scripts/lib/refresh.py#refresh`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | integration | — | kiro | 未着手 | 0 |

**なぜ**: refresh は fetch・row・compare をつないで全件の 1 行と失敗件数を返す公開入口だが、この配線を通す固定が対象範囲に無い。取れなかった URL を黙って落とさず件数へ数える振る舞いが未固定である

**手順**: 1. 成功と失敗を混ぜて返す opener を差し替えで用意する
2. name/checked_at/claim を持つ複数の source を渡し refresh(sources, timeout, opener) を呼ぶ
3. 返る行数が source 数と一致し、失敗件数が失敗した source 数と一致することを固定する
4. 各行に source の name と取得の成否が含まれることを、実行して得た値で固定する

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | error | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/scripts/lib/refresh.py#fetch` | error | テストの期待する振る舞いが変わっています（plugins/ndf/scripts/tests/test_refresh.py）。構造改善では期待出力を変えません。振る舞いの変更は別の変更に分けてください |
