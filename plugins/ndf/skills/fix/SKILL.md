---
name: fix
description: "Fix actionable PR review comments, then reply and resolve each thread. Use when responding to review feedback on a PR（PRコメント対応・PRレビュー修正・Resolveして）."
argument-hint: "[PR番号] [--classify-only] [--severity-min critical|major]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# PR コメント対応コマンド

指定 PR（省略時は直前 PR）のレビューコメントを **分類 → 修正 → 返信 → Resolve** まで
一貫して処理する。取得と集計はスクリプトが行い、LLM が持つのは **指摘の振り分けと修正** である。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | 対象 PR | 直前 PR（`gh pr view --json number -q .number`） |
| `--classify-only` | **分類・優先度判定のみ**で終了する（読み取り専用）。修正・返信・Resolve は行わない | OFF |
| `--severity-min LEVEL` | 直す重要度の下限（`critical` / `major`）。判定し直した後の重要度に効き、下限未満も無視しない。下限未満の `minor` / `nit` は `waived`、`critical` を指定したときの `major` は `deferred`（理由に `--severity-min critical` と書く）にし、どちらも返信を受ける。`minor` を受け取ったら `major` として扱うと知らせる | `major` |
| `--defer-nit` | 廃止。`nit` は常に見送るため、受け取ったら「nit は常に見送るため無視する」と知らせて続ける | — |

```
/ndf:fix 9352 --classify-only   # まず全体像を把握したいとき
```

## 起動モード

メインセッション直接実行と、サブエージェント（`general-purpose`）起動の両方に対応する。
`/ndf:cross-review` からは **必ずサブエージェント経由で起動** される。サブエージェントは
**修正 → コミット → 戻り値ファイル** までを行い、メインへの戻り値は最小限のサマリだけにする。

**修正担当は GitHub と git へ書かない。** 送信・返信・スレッドの決着・まとめの投稿は、
戻り値ファイルを読んだ側が行う。修正担当が送ると、送ったという報告と実物が食い違う状態と、
途中で止まったときに投稿だけが残る状態が作れる。

| 起動のされ方 | 書き込みを行う側 |
| --- | --- |
| `/ndf:cross-review` から | 修正の取り込み（`state.py merge-fix`）が行う |
| 単独で呼んだ | 手順 6 の 1 行（`result_posts.py fix`）が行う |

## 手順

スクリプトの置き場所 `$R` の決め方は `development-workflow/references/scripts-lookup.md` にある。
出力はどれも 1 行の JSON（形は `$R/scripts/lib/README.md`）。`status` が `ok`（0）なら次の手順へ
進み、`stopped`（1 / 2 / 3）なら `summary` と `items[].reason` を報告して止まる。

```bash
FIX=$(bash "$R/scripts/resolve.sh" scripts fix) || exit 3
```

1. **文脈を集める。** 未解決スレッド・3 種のコメント（インライン / レビュー body / PR レベル）・
   CI の現時点の状態（失敗した check と失敗ログの保存先）・本文の「やらないこと」「別 PR 対応」
   の節を 1 ファイルへ書き、振り分けの雛形を書き出す。**CI の完了は待たない**

   ```bash
   python3 "$FIX/fix-steps.py" context <PR番号> [--root <worktree>]
   ```

   `items[].name` が `context` のファイルを読む。末尾の「指摘の基準」の節が振り分けの基準である
   （正本は `$R/scripts/lib/review_criteria.py`。プロジェクトのレビューの重点は `.ndf/review.json`
   の宣言から入り、`/ndf:cross-review` の中では状態ファイルに写した重点を使う）。`review-focus` の
   項目が `unreadable` なら、宣言を読めずに基準 1・2・4 だけで続けたことを作業完了報告へ書く。`ci_failed` の項目があれば `ci_log` のログも
   読む。`metrics.unresolved` が対応の対象の全量である（レビュー結果の投稿数は使わない）
2. **指摘を振り分ける**（「重要度の判定」）。`items[].name` が `decisions` の JSON の各要素へ
   `severity` / `category` / `decision`（`fixed` / `waived` / `deferred` / `rejected` /
   `separate_pr`）/ `reason` を書き、`fixed` には当たった基準の番号を `criterion` へ、`waived` には
   見送りの種類を `waive_kind` へ書く。`separate_pr` は `/ndf:out-of-scope` で起票し、番号を `issue` へ書く。
   本文の除外の節に載る内容への指摘は `separate_pr`。`--classify-only` はここで
   「`--classify-only` の出力」を報告して終える
3. **修正する。** `fixed` の指摘と CI の失敗を直す（「CI の失敗の切り分け」）。`waived` の指摘の
   ためにコードを変えない。コミット前に手順 1 を打ち直し、新しい指摘・失敗があれば手順 2 へ戻る
4. 直したものがあればコミットする。**送らない**。`fixed` が無ければコミットしない
5. **戻り値ファイルを組む。** 件数と `by_severity` を数え、設計 PR なら本文の「決めたこと」の
   節を設計文書に揃える（対象かどうかはスクリプトが決める。コミットが無くても行う）

   ```bash
   python3 "$FIX/fix-steps.py" finalize --decisions <雛形の JSON> [--root <worktree>]
   ```

   `fix_commit` を省くと HEAD を採る。`fixed` が 0 件なら `fix_commit` を捨てて `null` にし（`items`
   の `fix-commit` が `dropped`）、送信も CI も起きない。振り分けが「重要度の判定」の規則を破れば
   `stopped` で止まり、戻り値ファイルを書かない。`items[].name` が `pr-body-decisions` の `result`
   （`synced` / `mismatch` / `unreadable` / `invalid_call`）と `code` を作業完了報告へ写す。
   `unreadable` を一致と書かない。`invalid_call` は呼び出しの誤りで `stopped` になる
6. 単独で呼んだときだけ、`next` の 1 行（`result_posts.py fix --pr <PR> --result <戻り値>`）を
   実行して送信と投稿を終える。送り先のブランチを決められないときはこの行が終了コード 1 で
   止まる。続けて残数を数え直す。見送り・却下として残したもの以外が残っていれば `stopped`
   （対応の漏れ）

   ```bash
   python3 "$FIX/fix-steps.py" remaining <PR番号>
   ```

## 重要度の判定

`[重要度 / カテゴリ]` プレフィックス（`/ndf:pr-review` の出力規約）を手がかりにするが、
**重要度ラベルを鵜呑みにしない**。各指摘ごとにコード・仕様を独自に調査し、文脈のファイルの
「指摘の基準」の 1〜4 のどれかに当たるかで重要度を判定し直してから下表の動作を適用する。
基準の番号は重点の宣言が無くても 1・2・4 のまま詰めない。

| 判定し直した重要度 | 動作 | ユーザ問い合わせ |
|---|---|---|
| `critical` / `major`（基準 1〜4 のどれかに当たる） | **必ず修正**（`fixed`、`criterion` に番号） | なし |
| `minor` / `nit`（基準に当たらない） | **直さない**（`waived`、`waive_kind` に見送りの種類）。コードを変えず、見送りの返信を付けてスレッドを閉じる | なし |

- **基準 2（秘密・認証認可・利用者のデータ・戻せない操作）に当たる指摘は、起きる確率が低くても、
  `minor` のラベルで届いても `major` 以上として直す。** 逆に `critical` のラベルでも基準に
  当たらなければ `minor` として見送る
- `finalize` は次を守らない振り分けを止める: `fixed` は `critical` / `major` だけ。`waived` は
  `minor` / `nit` だけで、`criterion` を持たず、`waive_kind` が見送りの種類のどれか。`criterion` の
  3 は重点の宣言があるときだけ
- 見送りの種類（`waive_kind`）: `wording`（字句や言い回しの修正）/ `unlikely`（まず起きない条件での
  異常処理）/ `doc_mismatch`（実装に影響しない文書の食い違い）/ `alignment`（番号や表記の揃え）/
  `preference`（好みの設計）。返信の本文は `finalize` が雛形
  （`scripts/lib/review_criteria.py` の `REPLY`）から組む。括弧の中は見送りの種類の名前で、重点の宣言が
  あるときだけ重点の名前を足す
- ロジック・仕様逸脱・セキュリティ: コード / 仕様を確認してから修正可否を判断
- bot 指摘が **明らかに誤読** している場合（例: 意図的な変数展開を「クオート不足」と指摘）:
  `rejected` に理由を書く。Resolve されない
- 仕様判断が必要な指摘（API 変更、互換性破壊など）: ユーザ問い合わせ対象
- **自動判断できない場合**（安易にユーザへ投げない）: 仕様文書（`docs/`, `README`）→ 既存
  テスト → 関連コードの慣例の順に読む。それでも不明なら `deferred` に「要ユーザ判断」と書き、
  最後にまとめて問い合わせる

**範囲外の指摘と flaky テストは分けて扱う。** flaky テスト・CI の失敗は PR の範囲外でも
この PR で直し、修正コミットに含める（直さないと後続の PR が CI の結果を読めなくなる）。
それ以外で範囲外と判断した指摘は直さず `separate_pr` にする（起票先のリポジトリは
`/ndf:out-of-scope` が決める）。

## `--classify-only` の出力

修正は一切行わず、次の分類で結果だけを報告する。サマリー（総数と分類別の件数）、詳細の表
（# / ファイル / 行 / 指摘内容 / 分類 / 対応判断）、推奨アクション（対応すべき → 対応推奨 →
別 PR で対応）の順に書き、分類の根拠を簡潔に添える。

| カテゴリ | 説明 | 対応判断 |
|---|---|---|
| 🔴 重大 | セキュリティ、データ整合性、クラッシュの可能性 | **対応必須** |
| 🟡 改善推奨 | コード品質、保守性、ベストプラクティス | **対応推奨** |
| 🟢 軽微 | タイポ、フォーマット、命名規則（指摘の基準に当たらない） | **対応しない（見送り）** |
| ⚪ 参考 | 提案、質問、情報共有 | **対応任意** |
| 🔵 別 PR 対応 | PR 本文で別 PR 対応と明記されている内容 | **対応不要** |

## CI の失敗の切り分け

`context` が拾うのは **現時点で失敗している check** だけで、実行中は無視する。CI の失敗は全件
修正対象で、review 指摘と **同じ PR で一緒に修正** する。同じファイル・機能に関するものは
1 コミットにまとめ、独立しているなら別コミットに分ける。

| エラー種別 | 対応方針 |
|---|---|
| **lint/format** | 自動修正ツール実行（`ruff`, `prettier`, `eslint --fix` 等）→ コミット |
| **型チェック** | 型定義・アノテーションを修正。無視コメントは原則禁止（根本対応） |
| **テスト失敗** | 失敗テストを読み、実装 / テストどちらが正しいか判断してから修正 |
| **ビルドエラー** | 依存関係・構文・設定ファイルを確認 |
| **依存脆弱性** | 可能ならバージョン更新、無理なら除外ルール追加（理由明記） |
| **タイムアウト/flaky** | retry 設定、テスト分割。**PR 範囲外の flaky も見つけ次第修正** |
| **インフラ一時障害** | `gh run rerun <RUN_ID>` を先に試す |

## 戻り値ファイル

`finalize` が `$TMP_DIR/fix-pr<番号>-result.json` へ書く（`$TMP_DIR` は環境変数
`CROSS_REVIEW_TMP_DIR` があればそれ、なければ `/tmp`）。`cross-review` の `state.py merge-fix`
が読む契約で、`pr` / `fix_commit` / `ci_status` / `ci_failed_checks` / `ci_note` / `fixed_count` /
`by_severity` / `resolved_threads` / `deferred` / `rejected` を持つ。

| 配列 | 由来 | 送られるもの |
| --- | --- | --- |
| `resolved_threads` | `fixed` | 「対応しました（<コミット>）」の返信と、スレッドの決着 |
| `deferred` | `deferred` / `separate_pr` / `waived` | 理由（`reason_for_deferral`）の返信。`separate_pr` は起票番号を添えて決着する。`waived` は `reply`（見送りの返信の本文）・`resolve: true`・`waived`（見送りの種類）を持ち、`reply` だけを返信して決着する |
| `rejected` | `rejected` | 理由（`reason_for_rejection`）の返信。決着しない。`path` / `line` / `severity` を持ち、次のラウンドの再提出と照合される |
| （すべて） | | 対応件数・決着・見送り・却下・CI を並べた PR のまとめ。`waived` があれば「基準外の見送り: N 件」の行を決着の行の直後に足す |

- `thread_id` を持たない要素（レビュー本文の指摘）には返信を送らず、理由を PR のまとめへ
  載せる。GitHub はレビュー本文への返信を受け付けないためである
- `ci_failed_checks` は `cross-review` 側で code-related と meta-only に分類され、メタチェックのみ
  失敗ならループを継続する。code-related ではない失敗の補足は雛形の `ci_note` に書く
- まとめの参照（`summary_comment_url`）は投稿する側が記録へ書く

## 作業完了報告（必須）

- 対応した指摘の件数（重要度別）/ 基準外の見送りの件数 / deferred 件数 / rejected 件数（各々理由付き）
- **`remaining` で数え直した未解決の指摘の残数**（0 でない場合は残した理由）
- 対応した CI エラーと flaky テストの一覧（ジョブ名、エラー内容、修正方法。PR 範囲外も含む）
- 修正コミット SHA / 修正ファイル一覧 / 戻り値ファイルパス
- `pr-body-decisions` の `result` と `code`
- **PR URL を最後に必ず記載**

## 関連

- `/ndf:pr-review` — PR / ブランチのレビュー（Approve / Request Changes 判定）
- `/ndf:cross-review` — codex + agy の収束レビュー。内部からこの Skill を呼ぶ
