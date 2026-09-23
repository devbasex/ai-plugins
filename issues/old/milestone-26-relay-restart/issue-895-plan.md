# #895: 区間の切れ目の再起動と次のコマンドの入力を、前景の中継で自動にする — 実装計画

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/895
- 設計 Pull Request: #908（2026-09-23 マージ、`2cab7076`）
- 要求と受け入れ条件: [issue-895-requirements.md](issue-895-requirements.md)（AC1〜AC26）
- 設計: [issue-895-design.md](issue-895-design.md)
- 決定の記録: [issue-895-design-decisions.md](issue-895-design-decisions.md)（決定 1〜20。実装で決めた 3 件を追記する）

## モード

standard（新しいスクリプトと hook の追加・利用者のシェル設定へ書く処理を含み、設計の関門を通した）。

## 目的と非目的

達成したい状態:

- 設計どおりに `relay.py`（`run` / `stop` / `mark` / `install`）・hook の登録・文脈量の hook の強化・文書の規約を実装し、AC1〜AC26 を満たす

やらないこと:

- Codex / Kiro / agy の hook の変更（AC16・AC22）
- `waiting.md` と `agent-layers.md` の変更（#892 #901 の実装が触っている）
- #827 の supervisor の駆動の先取り
- `.md` の文言を固定するテスト（利用者の方針。AC1・AC2 は読んで確かめる）

## 前提

- 前提 1: テストで利用者のシェル設定を書き換える操作は、すべて一時の HOME（`tmp_path`）で行い、本物の `~/.bashrc` / `~/.zshrc` / `~/.local` を変えない
- 前提 2: 中継の単体テストは、子の claude を擬似端末の上で動く短い試験用の Python プログラムに差し替え、`claude plugin ...` も差し替えの実行ファイル（`NDF_RELAY_CLAUDE` で指す 1 本が `plugin` の副命令と区間の起動の両方を受ける）で行う
- 前提 3: 未確認のうち実装で決める 3 件（`/goal` を位置引数で渡せるか・背景のサブエージェントが `background_tasks` に載るか・`goal_status` の記録の形）は実測（worker の調査）で決め、決定の記録へ追記する

## 受け入れ条件

要求の文書の AC1〜AC26 をそのまま使う。検証手段は要求の文書の「検証手段」と設計の「テスト設計」の表のとおり。

## 代替案と採否

設計の決定 1〜20 で決めたとおり。実装の中で決めるのは次の 3 件（実測の結果で埋める）。

| 項目 | 既定の案 | 替える条件 |
| --- | --- | --- |
| `/goal` を含む中身の渡し方 | 位置引数 1 つで渡す | 位置引数の `/goal` が働かなければ、`/goal` を外した中身で起動し、`/goal ...` を 2 つ目の入力として子の端末へ書く |
| 背景のサブエージェントの見分け | `background_tasks` の `status: running` | サブエージェントが載らなければ、会話の記録から背景の Agent の未完了を数える |
| 目標のある区間の判定の記録 | 記録の `goal_status` の行（印の `written_at` より後） | 実測した行の形に合わせる |

## 修正対象

| パス | 区分 |
| --- | --- |
| `plugins/ndf/scripts/relay.py` | 新設 |
| `plugins/ndf/scripts/tests/test_relay.py` | 新設 |
| `plugins/ndf/hooks/claude.json` | Stop に `mark`、SessionStart（`startup`）に `install` を 1 件ずつ足す |
| `plugins/ndf/scripts/token-guard.sh` | 理由の文を `ndf-next` の形へ。中継の直接の子の conductor では 1 度の通しをやめる |
| `plugins/ndf/scripts/tests/test_token_guard.py` | AC23 のテストを足す |
| `plugins/ndf/skills/development-workflow/references/context-window.md` | 「新しい会話で戻す」に `ndf-next` の形を定める |
| `plugins/ndf/skills/development-workflow/references/relay.md` | 新設（始め方・止め方・上限・記録の読み方・落ちたときの続け方） |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 引き継ぎの段落と「`/goal` の引数として呼ばれたとき」 |
| `plugins/ndf/README.md` | hook の一覧に中継の印と `install` を足す |
| `issues/issue-895-design-decisions.md` | 実装で決めた 3 件を追記する |

## タスク分解

各タスクは失敗するテスト → 通す最小実装 → 整理で進める（`tdd-cycle`）。文書のタスク（Task 8）だけはテスト駆動を適用しない（文言を固定するテストを書かない方針のため）。

### Task 1: `mark`（印を書く・消す）

- **対象:** `relay.py` の `mark`・ブロックの読み取り・親のたどり、`test_relay.py`
- **変更内容:** 設計の「`mark` の判定」1〜6。外側の囲みの中を除くブロックの数え方。`background_tasks` の `running` で書かない。親のたどりは `/proc/<pid>/stat`（macOS は `ps`）で、テストでは環境変数 `NDF_RELAY_TEST_CLAUDE_PID` ではなく関数の差し替え（モジュールを読み込んで呼ぶ）で与える
- **満たす受け入れ条件:** AC3・AC4・AC4b・AC24・AC16（hook 側）

### Task 2: `run` の素通しと本物の claude の解決

- **対象:** `relay.py` の `run` の条件 1〜6、`resolve_claude`
- **変更内容:** 素通しの 6 条件、`NDF_RELAY_DEPTH`、ラッパーを飛ばす探索、`NDF_RELAY_CLAUDE`、`plugin list --json` の読み取りと打ち切り
- **満たす受け入れ条件:** AC15・AC20

### Task 3: `run` の中継（1 区間）

- **対象:** `relay.py` の擬似端末の中継・作業ディレクトリ・記録
- **変更内容:** `NDF_RELAY_DIR` の作成（`0700`、起動ごとに新しく）・`relay.lock` / `relay.pid` / `child.pid`、同期のパイプと結果のパイプによる起動、入出力と SIGWINCH の中継、端末の属性の保存と復元、印なしの終わりで子の終了コードを返す
- **満たす受け入れ条件:** AC7・AC12・AC19・AC26

### Task 4: 切れ目の切り替え（`/exit` → 更新 → 次の区間）

- **対象:** `relay.py` の静まりの待ち・終わらせる・起動する
- **変更内容:** 静まり（印・記録・入力・目標の判定の記録）、`/exit` と `\r`、30 秒で SIGTERM・10 秒で SIGKILL、`marketplace update` / `plugin update -y` / `plugin list --json`、区切りの 1 行、`cwd` の落とし先、`start` / `end` の記録
- **満たす受け入れ条件:** AC6・AC8・AC9

### Task 5: 上限と止め方

- **対象:** `relay.py` の `stop`・1 日の起動回数（`count.lock`）・空回り・停止の印、落ちるときの 1 行と `stop` の行
- **満たす受け入れ条件:** AC10・AC11・AC13・AC14

### Task 6: `install`

- **対象:** `relay.py` の `install`
- **変更内容:** I1〜I7（`install.lock`・安定した場所への写し・bash / zsh の設定への囲み・`rc-added` / `rc-skipped`・バックアップ・`systemMessage`）。テストは一時の HOME だけで行う
- **満たす受け入れ条件:** AC21

### Task 7: hook の登録と文脈量の hook

- **対象:** `hooks/claude.json`、`token-guard.sh`、`test_token_guard.py`
- **変更内容:** Stop と SessionStart へ 1 件ずつ。文脈量の hook の理由の文と、中継の直接の子の conductor では通しをやめる判定（親のたどりは `relay.py` の関数を呼ぶ）
- **満たす受け入れ条件:** AC22・AC23

### Task 8: 文書

- **対象:** `context-window.md`・`relay.md`・`SKILL.md`・`README.md`・決定の記録
- **満たす受け入れ条件:** AC1・AC2（読んで確かめる）
- **進め方:** テスト駆動を適用しない（文言を固定するテストは書かない方針）

### Task 9: 通しの確かめと全体テスト

- **対象:** 擬似端末の上で本物の claude（`--model haiku`）を使う通しの確かめ（AC17・AC25・AC5）と全体テスト（AC18）。記録は実装の Pull Request に残す

## 影響範囲

- ndf を入れた Claude Code の利用者（bash / zsh）は、次に開くシェルから `claude` が中継を挟む。非対話・副命令・`-p` は素通しで変わらない
- 文脈量の hook の理由の文が変わる（中継の外の振る舞いは同じ）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 擬似端末のテストが遅い・不安定になる | 待ちの秒数（静まり・打ち切り・終わらせる待ち）を環境変数で短く差し替えられるようにし、テストでは 1 秒未満にする。タスクごとにテストを通す |
| 利用者の本物のシェル設定を書き換える | `install` のテストは必ず一時の HOME と `XDG_*` を与え、テストの補助関数で本物の HOME を指していないことを確かめる |
| 触る既存のファイル（`token-guard.sh`）の構造 | 変更は文脈量の判定の関数の中に閉じる。実装の後の構造改善で足りる |

## 切り戻し手順

- 利用者の側: `NDF_RELAY_AUTO=0` で `install` を止め、`~/.bashrc` の囲みを消す（バックアップ `<設定>.ndf-bak-<時刻>` もある）。`NDF_RELAY=0` で中継を常に素通しにする
- リポジトリ: この Pull Request を revert すれば元に戻る（データの移行は無い）

## 完了の定義

- [ ] AC1〜AC26 をすべて満たし、条件ごとに検証手段と結果が Pull Request に対応している
- [ ] `test_relay.py` と `test_token_guard.py` が通り、全体テスト（`-n 4`）が通る
- [ ] `claude plugin validate .` が終了コード 0
