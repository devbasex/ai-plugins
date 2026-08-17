# cross-refactoring 再々検証の引継ぎ

`/ndf:cross-refactoring` の**未到達 2 項目**を通すための作業メモ。
ここには**次に何をどの順で行うか**だけを書く。経緯と実測値は次の 2 つにある。

- [issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) — 1 回目の実機検証（不具合 1〜9 を発見）
- [issue-113-cross-refactoring-retrial.md](issue-113-cross-refactoring-retrial.md) — 2 回目（再検証。不具合 10・11 を発見）

## 現在地

| 回 | PR | 到達点 | 見つけた不具合 |
| --- | --- | --- | --- |
| 1 回目 | #118 | 適用結果の検証で破綻 | 9 件 |
| 2 回目 | #120（CLOSED） | レビュー・判定・輪番・集計まで到達 | 2 件 |
| **3 回目** | **未着手** | **指摘の修正と再レビューを通す** | — |

修正はすべて main に入っている（#119 / #121）。

```
6fce71f Docs: cross-refactoring 修正後の再検証レポートを残す (#122)
a415242 Fix: 公開の責務を進行側へ一本化し、適用失敗の項目を対象外へ記録する (#121)
26eb4eb Fix: cross-refactoring の実機検証で見つかった不具合 9 件を修正（v8.2.0） (#119)
```

## 通したい 2 項目

これだけが**一度も実行されていない**。

- **指摘の修正と再レビューの繰り返し**（`merge-fix` → 再 `review` → 再 `judge-review`）
- **上限到達時の項目単位の見送り**（`should-abandon` → `abandon-items`）

どちらも**レビューで指摘が出ること**が前提である。2 回目はラウンド 1 で
両レビュー担当とも指摘 0 件だったため到達できなかった。

## 先に決めること

指摘が出る確率をどう上げるか。**何もしないと 3 回目も到達しない可能性がある。**

| 案 | 内容 | 見込み |
| --- | --- | --- |
| A | 前回と同条件で素直に回す | 自然な結果が得られるが、また 0 件の可能性がある |
| B | `--max-fix-rounds 1` にする | 指摘が出れば**見送りへ早く到達**する。修正の繰り返しは 1 回しか見られない |
| C | `--max-items-per-round` を上げる | 採用件数が増え、指摘が出る確率が上がる。1 ラウンドが長くなる |
| D | 範囲を広げて質の粗い箇所を含める | 指摘は出やすいが、検証の趣旨から外れる |

B と C は併用できる。**D は最後の手段**とする。

## 実行手順

### 1. 対象の Draft PR を作る

`/ndf:cross-refactoring` は**既存の Draft PR** を対象に動く。main から新しい
ブランチを切り、種となるコミットを 1 つ置いて Draft PR を作る（2 回目は
実行条件を書いた md を置いた）。

**2 回目の PR（#120）は再利用しない。** 不具合 10 に起因する失敗ラウンドが
履歴に残っているため。

### 2. 実行

```bash
# 配布物はリポジトリ内のものを使う。**プラグインキャッシュは古い**
export PLUGIN_ROOT=/work/ai-plugins/plugins/ndf-claude

/ndf:cross-refactoring <PR番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
          plugins/ndf-shared/skills/cross-review/tests \
  --sync-command "bash scripts/build-runtime-plugins.sh" \
  --baseline-test "uv run --with pytest python -m pytest \
      plugins/ndf-shared/skills/cross-refactoring/tests \
      plugins/ndf-shared/skills/cross-review/tests -q" \
  --max-outer-rounds 3
```

## 実行上の落とし穴（実測で踏んだもの）

| 落とし穴 | 対処 |
| --- | --- |
| **プラグインキャッシュが古い** | `~/.claude/plugins/cache/.../ndf/8.1.0` には修正が入っていない。`PLUGIN_ROOT` をリポジトリ内の `plugins/ndf-claude` へ向ける |
| **`--sync-command` を省くと push が全て落ちる** | このリポジトリは `.githooks/pre-push` で生成物の同期を検査する。必ず指定する |
| **`--scope` にテストの置き場所を含め忘れる** | 範囲内の各ソースに対応するテスト置き場を**すべて**入れる。2 回目は `cross-review/tests` を忘れて 1 項目落とした |
| **Bash ツールの上限は 10 分** | 適用フェーズは 15〜30 分かかる。監視は**背景実行**にする（`run_in_background`） |
| **`monitor.py` に実行権限がない** | `uv run --script "$LIB/monitor.py" ...` で起動する |
| **進行を駆動する作業ディレクトリが対象ブランチを掴んでいると失敗する** | 同じブランチを 2 か所へ展開できない。PR を作ったら `git checkout main` してから実行する |
| **`monitor.py` の `elapsed` は監視開始からの秒数** | CLI の実起動時間ではない。実所要は結果ファイルの `elapsed_seconds` を見る |

## 所要時間の目安（2 回目の実測）

| フェーズ | 実測 |
| --- | --- |
| 初期化（認証確認 + 着手前テスト） | 約 40 秒 |
| 提案（3 CLI 並列） | 135〜195 秒 |
| 適用（codex / 11 コミット） | 約 12 分 |
| 適用（kiro / 12 コミット） | 約 12 分（監視は 30 分待った） |
| 適用結果の検証 | コミット数 × 約 30 秒（テストを実走するため） |
| レビュー（2 CLI 並列） | 約 3.5 分 |

**1 ラウンドで 30〜45 分**を見込む。

## 確認すること

未到達の 2 項目に加えて、#121 で入れた経路を実機で見る。

| 対象 | 何が観測できれば通ったと言えるか |
| --- | --- |
| 指摘の修正と再レビュー | `merge-fix` が修正コミットを取り込み、再レビューで判定が変わる |
| 項目単位の見送り | `should-abandon` が上限到達を返し、`abandon-items` が未解決の項目だけ取り消す |
| 公開の責務（不具合 10） | 実装担当が push せず、`merge-apply` の後に進行側が push する |
| 生成物の同期 | `Chore: 生成物を同期する（cross-refactoring 進行側）` が push の直前に積まれる |
| 対象外への記録（不具合 11） | 適用で失敗した項目が次ラウンドの提案で**再採用されない** |

## 別件で残っているタスク

再々検証とは独立に、次の 1 つが未対応である。
（バージョンは **v8.3.0** へ更新済み。#121 の破壊的変更を反映した）

### cross-review が投稿の成否を突き合わせていない

`cross-review` は「AI 自身が `gh api` で投稿する」設計だが、**投稿が失敗しても
結果ファイルの申告だけで判定が進む**。#121 のラウンド 3 で実際に起きた
（`comments_count=2` の申告に対し、GitHub 上にスレッドが 1 つも作られなかった）。
今回は payload ファイルから内容を拾えたが、気付かなければ指摘を取りこぼす。

`state.py read-result` が GitHub 側のスレッド数と突き合わせるべきである。
