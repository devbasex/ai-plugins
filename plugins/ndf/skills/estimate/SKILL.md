---
name: estimate
description: "Estimate how much a task costs when AI agents do it, from similar past runs instead of human-era effort. Use when sizing an issue or task before starting it（工数を見積もる・工数算出・作業の大きさを見積もる）."
argument-hint: "[課題番号 | 作業の説明]"
allowed-tools:
  - Bash
  - Read
  - Grep
---

# AI エージェントが実施する前提で工数を見積もる

課題番号か作業の説明を受け取り、近い条件の実績から A（AI の実行量）・H（人の作業時間）・L（完了までの時間）・
C（利用費用）の見込みを、値ごとに根拠を付けて返す。**人の工数の基準で見積もらない。人日や AI の倍率から換算しない。**
4 つの値を足して 1 つの「工数」にしない。定義と規則の正本は [references/rules.md](references/rules.md) にあり、
この手順は写さずにそれを指す。

## 例: 課題 #1856 を実装の完了まで見積もる

```bash
SCRIPTS=$(bash "$R/scripts/resolve.sh" scripts) || exit 3
python3 "$SCRIPTS/estimate.py" predict --issue 1856 --done 実装 \
  --items requirements=軽,information=軽,spread=重,verification=軽,environment=軽 --save
```

返った JSON の `items[0].values` の A・H・L・C を、印（`実績` / `未校正` / `材料なし`）と根拠（比べた課題と実測）と
ともに表にして示す。`$R` の決め方は `development-workflow/references/scripts-lookup.md` にある。

## 手順

1. **対象を読む。** 課題番号なら `gh issue view <番号>` で本文を読む。作業の説明なら、その文と、触るファイルの
   見当を手元のリポジトリで確かめる。部品は GitHub も LLM も呼ばないため、読むのはこの手順の側である
2. **完了条件を決める。** `設計`・`実装`・`検査`・`配布（開発版）`・`配布（本番）` のどこまでを見積もるか
   （規約の「見積もりの完了条件」）。利用者が言わなければ `実装` にし、そう決めたことを答えに書く
3. **着手前の 6 項目を判定する。** 規約の「着手前の 6 項目」の表で `軽`・`重`・`判定できない` を付ける。
   着手前に分かることだけで判定し、分からない項目は `判定できない` のままにする
4. **近い課題が分かれば選ぶ。** 同じ種類の変更をした閉じた課題を知っていれば `--like 1601,1631` で渡す。
   知らなければ渡さない（部品が 6 項目の近い実績を選ぶ）
5. **部品を呼ぶ。** `estimate.py predict`（上の例）。作業の説明なら `--issue` の代わりに `--task "<説明>"`。
   後で誤差を測るため、課題番号で見積もるときは `--save` を付ける
6. **実績が無い値を扱う。** `未校正` の値には、想定の手順と既知のテストの所要（`materials.test_seconds`）から
   暫定の範囲を作れるときだけ、`--provisional A=1800-5400` を足して呼び直す。作れなければ空のまま示す。
   人日や倍率で埋めない
7. **答える。** 値ごとに見込み（通常 / 上振れ、または P50 / P80）・印・根拠を表にし、`other_config`（構成の違う
   実績）があれば別に示す。`未校正` と `材料なし` を隠さない。6 項目のうち `重` のものを、見込みが膨らむ理由
   として添える

## そのほかのコマンド

| コマンド | 使う場面 |
| --- | --- |
| `estimate.py human --issue N --minutes M [--what 説明]` | 人が判断・レビュー・確認に手を動かした時間を残す（H の材料） |
| `estimate.py import [--sv-dir DIR]` | 導入の初回に、既存の supervise の記録から実績の記録を作る（何度流しても同じ）。既定の置き場所（全プロジェクト共通）からは、このリポジトリのプランだけを取り込む |
| `estimate.py errors [--since 日付]` | 見積もりの記録と、その後の実績を突き合わせ、A・L・C の誤差の比と A の順位相関を出す |
| `estimate.py actuals [--issue N] [--done 実装]` | 課題ごとの実績の合計と、その材料の実行（終わり方を含む）を見る |

どのコマンドも結果は 1 行の JSON で、終了コード 0 は結果を返したこと、2 は引数の誤りである。

## 関連

- 棚卸しの Size は `backlog-refinement` が同じ部品の見込みから決める（`references/ranking.md` の「大きさ」）
- 実装のステップの上限は `supervise.py new` が同じ記録から決める（規約の「実装の上限」）
