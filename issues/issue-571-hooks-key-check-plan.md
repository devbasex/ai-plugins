# #571: hooks 定義の未知キーを継続的統合で落とす — 実装計画

## 関連リンク

- issue: #571
- 要求と受け入れ条件: [issue-571-hooks-key-check-requirements.md](issue-571-hooks-key-check-requirements.md)
- 設計: [issue-571-hooks-key-check-design.md](issue-571-hooks-key-check-design.md)（設計 PR #579 でマージ済み）

## モード

`standard`。継続的統合の `runtime-smoke` に検査を足し、Pull Request の合否が変わるため。

## 目的と非目的

達成したい状態:

- hooks 定義をランタイムが警告付きで読む状態を、`runtime-smoke (claude)` / `(codex)` で落とす

やらないこと:

- hooks 定義そのものの書き換え、ランタイムの版の固定
- agy / Kiro CLI の検査（設計の決定 8）
- `runtime-smoke` の失敗理由を継続的統合の画面へ出すこと（#577）

## 受け入れ条件

要求文書の 13 件をそのまま使う。条件ごとの確かめ方は設計の「テスト設計」の表にある。

**設計からの追加（実装中に 1 件足した）:**

- [ ] `scripts/runtime-smoke-test.sh` の実行が失敗しても、起動したコンテナが残らない

後片付けは `run_runtime` の `RETURN` の trap だけだった。`set -e` のもとでアダプタが失敗すると
スクリプトはその場で終わり、関数が戻らないため trap が動かない。この PR の検査を落とす確認を
手元で繰り返したことで `sleep infinity` のコンテナが 238 個たまり、ホストのメモリを圧迫した。
CI のランナーは使い捨てのため、継続的統合では表に出ていなかった。

## 修正対象

| 区分 | パス |
| --- | --- |
| 新設 | `tests/runtime-smoke/assertions/assert-hook-definitions.sh` |
| 新設 | `tests/runtime-smoke/lib/codex-hooks-list.py` |
| 新設 | `tests/runtime-smoke/fixtures/hooks-positive-control/`（マーケットプレイス定義・`plugin.json` 2 つ・hooks 定義 2 つ） |
| 変更 | `tests/runtime-smoke/adapters/claude.sh` / `adapters/codex.sh`（検査の呼び出しを 1 行ずつ） |
| 変更 | `tests/runtime-smoke/README.md` |
| 変更（設計からの追加） | `scripts/runtime-smoke-test.sh`（コンテナの後片付けを `EXIT` の trap へ移す） |

## タスク分解

### Task 1: Claude Code の検査を通す

- **対象ファイル:** `assert-hook-definitions.sh`（`claude` の分岐と共通部分）、陽性対照の Claude 側、`adapters/claude.sh`
- **変更内容:** マーケットプレイス定義から対象を見つけ、陽性対照 → 本物の順に `claude --debug-file ... plugin list` で読ませ、報告の行と読み込みの行で判定する
- **満たす受け入れ条件:** Claude Code の 5 件、共通の 4 件
- **進め方:** 先に陽性対照だけを読ませて警告が出ることをコンテナで確かめる（失敗するテストにあたる） → 本物の判定を足す → 受け入れ条件の差分を当てて落ちることを確かめる

### Task 2: Codex の検査を通す

- **対象ファイル:** `codex-hooks-list.py`、`assert-hook-definitions.sh`（`codex` の分岐）、陽性対照の Codex 側、`adapters/codex.sh`
- **変更内容:** 専用の `CODEX_HOME` にマーケットプレイスを登録して導入し、`codex app-server` の `hooks/list` の応答で判定する
- **満たす受け入れ条件:** Codex の 4 件、共通の 4 件
- **進め方:** Task 1 と同じ

### Task 3: 説明を書く

- **対象ファイル:** `tests/runtime-smoke/README.md`
- **変更内容:** 何を見て落とすか、古い像で陽性対照が落ちるときの直し方（キャッシュなしで作り直す）
- **進め方:** テスト駆動を適用しない（文書のみ）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| x86_64 の CI ランナーで app-server が起動しない | 実装の Pull Request の `runtime-smoke (codex)` で確かめる。起動しなければ取得の検査が失敗として知らせる |
| 手元の古い像（Claude Code 2.1.261）が警告を出さない | 像をキャッシュなしで作り直してから確かめる |
| 触る範囲 | 新設が中心で、既存のアダプタへは 1 行ずつ足すだけ。実装の後の構造改善で足りる |

### Task 4: 失敗した実行でもコンテナを残さない（設計からの追加）

- **対象ファイル:** `scripts/runtime-smoke-test.sh`
- **変更内容:** 起動したコンテナを配列へ積み、`EXIT` の trap で消す。`INT` / `TERM` も `exit` を経由させて同じ trap を通す
- **進め方:** 失敗する実行を 1 回だけ流し、前後で `ai-plugins-runtime-smoke-*` の像から作られた `sleep infinity` のコンテナの数が変わらないことを見る

## 切り戻し手順

- アダプタの呼び出し行を消せば、検査は走らない。配布物にもデータにも触れないため、戻しに順序の制約は無い

## 完了の定義

- [ ] 受け入れ条件 13 件のそれぞれに、実行の結果（終了コード・`smoke.log` の行・CI の run）が対応している
- [ ] マッチャーグループへ `description` を戻した一時コミットで CI が落ち、戻したコミットで通る
- [ ] 設計の「未確認のまま残ること」のうち実装で決める 2 件の結果を Pull Request に書く
