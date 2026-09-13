# #415: Kiro のインストーラに導入先の検査を足す（実装計画）

## 関連リンク

- issue: https://github.com/devbasex/ai-plugins/issues/415
- 要求: [issue-415-requirements.md](issue-415-requirements.md)
- 設計: [issue-415-design.md](issue-415-design.md)（設計 PR #502）

## モード

standard（配布物のシェルスクリプト 1 本の振る舞いを変え、自動テストを新設する。設計はマージ済み）

## 目的と非目的

達成したい状態:
- `--project` に誤ったパスを渡した利用者が、`ERROR:` と `HINT:` の案内から次の手を読み取れる

やらないこと:
- 他の選択肢の検査の追加、`WARN:` の変更、導入処理の変更、他ランタイムのインストーラの変更

## 受け入れ条件

要求文書の 7 件をそのまま使う。

1. 存在しないパス → `ERROR:` 1 行と `HINT: mkdir -p <パス>` 1 行を標準エラーへ、終了コード 2
2. `HINT:` のパスは空白やタブを含んでも bash の語 1 つとして読み戻せる
3. ファイル → `ERROR:` 1 行、`HINT:` なし、終了コード 2
4. `--scope global` と存在しないパスの併用 → 1 と同じ形
5. `cd:` のエラーが出力に現れない
6. 存在するディレクトリ → `--dry-run --yes` で終了コード 0
7. 1〜6 を検査する自動テストが `scripts/tests/` にあり、pytest が通る

## 修正対象

- 新設 `scripts/tests/test_kiro_installer_project.py`
- 変更 `plugins/ndf/dev.kiro/install.sh`

## タスク分解

### Task 1: 誤った導入先を案内つきで止める
- **対象ファイル:** 上の 2 つ
- **変更内容:** `--project` の分岐で `cd` の前に `[ -e ]` と `[ -d ]` を確かめ、落ちた側に応じた文言で終了コード 2。
  存在しないときだけ `printf '%q'` で整形したパスを `HINT: mkdir -p` に載せる
- **満たす受け入れ条件:** 1〜7
- **進め方:** 失敗するテストを先に書き、赤を確かめてから分岐を足す

## 影響範囲

- `plugins/ndf/dev.kiro/install.sh` を呼ぶ手順（`plugins/ndf/README.md` の Kiro の案内）。正しいパスでの振る舞いは変わらないため、案内の文面は変えない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 触る範囲は分岐 1 つで狭い | 実装の後の構造改善で足りる（テストで出力と終了コードを固定する） |

## 切り戻し手順

- 実装のコミットを revert すれば元の振る舞いに戻る。データ移行は無い

## 完了の定義

- [ ] 受け入れ条件 7 件が、テストと手での実行（存在しないパス / ファイル / 空白やタブを含むパス / 正常なパス）の出力と終了コードで裏付けられている
- [ ] `bash scripts/build-runtime-plugins.sh` と `bash scripts/validate-runtime-plugins.sh` が通る
