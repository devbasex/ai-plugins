# #313: 被演算子の走査がリダイレクトを読み飛ばして続く形にする（実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-313-requirements.md](issue-313-requirements.md)（受け入れ条件 15 件）
- 設計文書: [issue-313-design.md](issue-313-design.md)（決定 6 件）
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/630 （マージ済み）
- 課題: #313

## モード

`standard`（親の判定に従う）。抽出関数の走査の順序が変わり、hook の案内の出る場面が増えるため。

## 目的と非目的

達成したい状態:

- リダイレクトを挟んだ `sed -i` / `cp` / `mv` / `tee` でも、bash が書き換えるファイルを書き込み先として出す
- 入力側のリダイレクトの語（`<in` `< in` `<<<word` `<<EOF` `<&0` `<>rw` と、前の語に密着した `b<in`）を
  命令の被演算子と取り違えない

やらないこと:

- `>|` の取りこぼし（#625）
- `cd` の枝の変更（設計の決定 5）
- 字句解析（`_wt_tokenize`）と印への置き換えの段の変更（決定 3）
- 引用符で囲んだ `<` を含む名前の区別（要求の前提 3）
- `_redir_target` を末尾の `unset -f` へ足すこと（#633。範囲外）。新設する `_redir_span` は足す

## 前提

- 前提 1: リダイレクトの書き込み先（`>log` の `log`、`<>rw` の `rw`）は、これまでどおり主の走査の印の枝が出す。
  被演算子を走査する枝は読み飛ばすだけで出さない
- 前提 2: 出力の順序は契約に含めない。テストは集合で比べる
- 前提 3: 設計文書の「入出力の契約」の表がそのまま `_redir_span` の仕様である。新しい設計判断はしない

## 受け入れ条件

**15 件は [issue-313-requirements.md](issue-313-requirements.md) の「受け入れ条件」にある。** ここでは写さない。
タスクごとに、そのタスクが満たす条件の番号を指す。

## 修正対象

| 区分 | ファイル |
| --- | --- |
| 改訂 | `plugins/ndf/scripts/lib/worktree-common.sh`（`_redir_span` の新設、`_wt_extract_sed_targets` / `_wt_extract_cp_mv_target` / `tee` の枝の先頭、末尾の `unset -f`） |
| 改訂 | `plugins/ndf/skills/worktree/tests/test_write_target.py`（受け入れ条件のテストを追記） |

**同じファイルの他の関数（`wt_declaration`・`wt_follow_target`・ロック関連）は触らない。** 並行する担当が触る。

## タスク分解

### Task 1: 出力側のリダイレクトを読み飛ばす

- **対象ファイル:** 上の 2 ファイル
- **変更内容:** `_redir_span` を抽出関数の内側に新設し、印（`__WT_REDIR__` / `__WT_APPEND__`）に当たれば
  `_redir_target` に読み終えた位置を決めさせる。3 つの枝のループ先頭で `_redir_span` を呼び、当たれば
  `_WT_REDIR_END` まで進める。`skip_next` / `take_next` の判定より前に置く（決定 4）。`unset -f` に足す
- **満たす受け入れ条件:** AC1〜AC5、AC11、AC12
- **進め方:** 失敗するテスト（AC1〜AC5、AC11）→ 最小実装 → AC12 が通ったままであることを確かめる

### Task 2: 入力側のリダイレクトを読み飛ばす

- **対象ファイル:** 同上
- **変更内容:** `_redir_span` を、語の中の最初の `<` で前置と残りに分ける形へ広げる。残りが `<(` なら
  リダイレクトでない（決定 6）。残りが演算子だけなら次の語（印なら `_redir_target` の位置）まで読み、
  演算子の後ろに文字を持てばその語で終える。前置は `_WT_REDIR_HEAD` に置き、数字だけなら空にする。
  3 つの枝は前置が空でなければそれを今の語として後の判定へ渡す
- **満たす受け入れ条件:** AC6〜AC10、AC13、AC14
- **進め方:** 失敗するテスト（AC6〜AC10）→ 最小実装 → AC13・AC14 が通ったままであることを確かめる

### Task 3: 退行の確認

- **対象ファイル:** なし（検証のみ）
- **変更内容:** `uv run --with pytest pytest scripts/tests plugins/ndf -q` と、AC1〜AC10 の命令を一時
  ディレクトリで bash に走らせた結果との突き合わせ
- **満たす受け入れ条件:** AC15
- **進め方:** 既存テストの差分が追加だけであることを `git diff` で確かめる

## 影響範囲

- `worktree-guard.sh`（PreToolUse）の案内が出る場面が増える。案内は編集を止めない
- 抽出関数の出力の順序が変わる（`x.md` `log` → `x.md` `y.md` `log`）。呼び出し側は集合として扱う

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `worktree-common.sh` は 1700 行を超え、並行する担当が同じファイルの別の関数を触る | 触るのは抽出関数の内側だけに限る。構造改善（cross-refactoring）の項目も抽出関数とその補助関数に限るよう指示する |
| 引用符の中の `<` を含む語（`sed 's/<br>/x/'`）を分けることで被演算子の数が変わる | 前置が被演算子として残るため数は変わらない（設計で実測済み）。AC14 で固定する |
| 記述子の複製 `2<&1` を字句解析が 1 語に繋げる前提が崩れる | 字句解析は触らない。既存の `cd sub && cat 2<&1 x; echo hi > f` 系のテストで確かめる |

## 切り戻し手順

- 1 ファイルの関数の内側の変更とテストの追記だけである。コミットを戻せば元に戻る。データ移行は無い

## 完了の定義

- [ ] 受け入れ条件 15 件をすべて満たし、条件ごとに `test_write_target.py` のテストが対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `claude plugin validate .` と `bash scripts/build-runtime-plugins.sh --check` が exit 0
- [ ] cross-refactoring が収束し、最後の cross-review が `APPROVE`
