# #3846: 中継: 手順書 relay-accounts.md が 11 系のラッパーの書かない記録の行（account_dir）とキー（rule・account_rule）を読み方として書いている

正は課題の本文（#3846）で、この文書はそのコピーである。本文から作り直す。手で直さない。

## 何が起きたか

2026-11-19、#3812（PR #3845、11.0.8）の実装で、中継の手順書 `relay.md` の「記録の読み方」を 11 系のラッパーに合わせて直していたとき、同じ中継のもう 1 つの手順書 [relay-accounts.md](../plugins/ndf/skills/development-workflow/references/relay-accounts.md)（利用上限でアカウントを替えて続ける規約）が、11 系のラッパーが書かない記録の行とキーを「こう引けば分かる」と書いているのを見つけた。#3812 の範囲は `relay.md` の「記録の読み方」と、`relay-accounts.md` の「ラッパーの入れ替え」への参照だけで、アカウントの切り替えの記録は入っていなかったため、別の課題として起票した。

## 種類

不具合（手順書が、ラッパーの実際の記録と違う読み方を書いている。直すのは手順書の側で、ラッパーは変えない）

## 今の姿

中継のラッパー（`ndf relay`）は、claude を会話の切れ目ごとに起動し直し、その経過を中継の記録（`log.jsonl`）に 1 行 1 つの JSON で残す。

- 記録の行の種類とキーの一覧の正は、手順書 [relay.md の「記録の読み方」](../plugins/ndf/skills/development-workflow/references/relay.md#記録の読み方) である（#3812 で 11 系へ直した）。どの行も時刻（`at`）・セグメントの番号（`section`）・行の種類（`kind`）を持つ
- 11 系のラッパーがアカウントについて書く記録は、次の 2 つだけである。どちらも `{"at": …, "section": …, "kind": "account", "reason": <説明の文>}` の形で、`reason` はアカウントを選ぶ関数（`account_decide`）が返す説明の文である
  - 会話の切れ目（カットポイント）で使用率を見て替えたとき（[wrapper.py](../plugins/ndf/src/ndf/domains/relay/wrapper.py) の `_account_for_next`）
  - 利用上限で替えて同じ会話を続けるとき（同じファイルの `_note`。判定の操作の種類 `account` を書く）
- `relay-accounts.md` は 10 系のラッパーに合わせて書いたまま、11.0.0 の作り直し（#3304）で書き直されなかった。記録の読み方のほかにも、アカウントの選び方・上限と認証の失敗での切り替え・`ndf plan` の API キーなど 11 系に無い振る舞いを書いており、それらは #3864 が扱う

## 事象

`relay-accounts.md` が、11 系のラッパーの書かない `log.jsonl` の行とキーを読み方として書いている。

| 行 | 手順書が書くもの |
| --- | --- |
| 48 | 組を出すのは `log.jsonl` の `account` の行 |
| 106・125 | `account_dir` の行（`local_only` のキー） |
| 133 | `account` の行の `reason` が `rule` |
| 171〜173 | `account` の行の `rule`・`account_rule`・根拠の値（`evidence`）と、画面の 1 行「…アカウントを work1 から work2（b@example.com）へ替えて続ける」 |
| 235〜237 | 進捗ログの `account` の行の `to_key`・`rule`・`evidence`、`reason` が `launch_rule` の行、`api_key` の行 |

設計の工程の調査（2026-11-19、`origin/main` の 2e43af7bc）で、`plugins/ndf/src` に `account_dir` の行・`local_only`・`rule`・`account_rule`・`evidence`・`to_key`・`launch_rule`・`api_key` の行を書く処理が 0 件、画面の「替えて続ける」「を使わない」の 1 行も 0 件と確かめた。

## 期待する結果

手順書が書く記録の行とキーが、11 系のラッパーが書く `log.jsonl` と一致する（無いキーを読み方として書かない）。

## 再現手順

1. `grep -n "account_dir\|account_rule\|to_key" plugins/ndf/skills/development-workflow/references/relay-accounts.md` で上の行が出る
2. `grep -n '"kind": "account' plugins/ndf/src/ndf/domains/relay/wrapper.py` で `account` の行が `reason` だけを持つと分かる

## 環境

NDF `origin/main` 2e43af7bc（11.0.8 の後）・Claude Code・Linux

## なぜ困るか

アカウントが替わった理由を知りたい利用者と AI は、手順書のとおりに `jq` で `rule` や `account_dir` を引く。11 系の記録にはそのキーが無いので結果は 0 件になり、「替わっていない」「起きていない」と読み誤る。

## 目的

- 手順書 `relay-accounts.md` を読んで `log.jsonl` を引く利用者と AI が、11 系のラッパーが書く行とキーだけを引き、0 件を「起きていない」と読み誤らない

## 対象範囲

含む:

- `relay-accounts.md` の、`log.jsonl` と進捗ログの行・キーを読み方として書く文と、同じ文が書く画面の 1 行（「事象」の表の行）を、11 系の記録に合わせて直すか消す
- 記録を読む先として `relay.md` の「記録の読み方」を指す

含まない:

- `relay-accounts.md` のほかの 10 系の記述（起票者の推定 S2）。#3864 が持つ（アカウントの設定ディレクトリを子へ渡す処理は #3604）
- ラッパーのコードの変更（10 系のキーをラッパーに足すことも含む）

## 受け入れ条件

- [ ] 1. `grep -n "account_dir\|local_only\|account_rule\|to_key\|launch_rule\|evidence" plugins/ndf/skills/development-workflow/references/relay-accounts.md` が 0 件になる
- [ ] 2. `relay-accounts.md` が `log.jsonl` の行として書くのは `account` の行（~~`kind` と `reason`~~ → `at`・`section`・`kind`・`reason`。2026-11-19、設計レビューの指摘で変更）だけで、その `reason` を規則の名前（`rule` など）として書かない。11 系に無い振る舞いの結果を記録の行として述べる文が無い
- [ ] 3. `relay-accounts.md` が書く画面の `ndf-relay:` の 1 行のうち、アカウントの切り替えの文（「替えて続ける」「を使わない」）が無い（`plugins/ndf/src` で 0 件の文を書かない）
- [ ] 4. `relay-accounts.md` で記録の読み方を書く箇所は、行の種類とキーの一覧として `relay.md` の「記録の読み方」を指す
- [ ] 5. 退行しない: `python3 scripts/check-markdown-links.py`（リンクの検査）が通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（手順書の文だけ） |
| 重さ | 記録を読み誤るだけで、ラッパーの振る舞いは正しい。ただしアカウントの切り替えを調べるたびに、手順書どおりの引き方が 0 件を返す |
| データ | 変わらない |
| 既存の振る舞い | 変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析 | 受け入れ条件 1 の `grep`・`python3 scripts/check-markdown-links.py`。`.md` の文言を照合するテストは足さない（`AGENTS.md` の DON'T） |
| 手動確認（マージ前） | 実装の Pull Request のレビューで、直した文が `wrapper.py` の `_account_for_next`・`_note` と一致することを見る |

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（課題を書いた AI と、設計の工程の AI）が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 手順書を 11 系の記録に合わせる側で直す（ラッパーに 10 系のキーを足さない） | #3812 の本文が写す利用者の方針「10 系の課題を引きずらず、11 系の設計思想に合わせる」（起票者） | 受け入れ条件 1〜4 |
| S2 | 直すのは記録の行とキーの記述と、同じ文が書く画面の 1 行だけにする。`relay-accounts.md` のほかの 10 系の記述（アカウントごとの設定ディレクトリ・選ぶ順の 4 組・`ndf plan` の API キーの経路・401 の扱い・`StopFailure` の `limit.json`）は別の課題にする | 設計の工程の推定。2026-11-19 の調査で、手順書の大半が 11 系に無い振る舞いを書いていると分かった。どれも「作るか、消すか」を決める設計が要り、記録の読み方の直しと同じ変更に入れると判断が混ざる | 含まない（#3864） |
| S3 | 記録の行を書く文が、11 系に無い振る舞いの結果を述べているときは、行の名前だけでなく文ごと消す | 設計の工程の推定。行の名前だけを 11 系の名前へ替えると、11 系に無い振る舞いを 11 系の記録が示すと読める | 受け入れ条件 2 |

## 依頼（原文）

> ## 事象
>
> 手順書 `plugins/ndf/skills/development-workflow/references/relay-accounts.md` が、11 系のラッパーの書かない `log.jsonl` の行とキーを読み方として書いている。
>
> 11 系のラッパー（`plugins/ndf/src/ndf/domains/relay/wrapper.py` の 338 行、`origin/main` の 2e43af7bc）が書く `account` の行は `{"kind": "account", "reason": ...}` だけで、`account_dir` の行は書かない。
>
> ## 期待する結果
>
> 手順書が書く記録の行とキーが、11 系のラッパーが書く `log.jsonl` と一致する（無いキーを読み方として書かない）。
>
> ## 影響
>
> 手順書のとおりに `jq` で `rule` や `account_dir` を引く利用者と AI が、0 件を「起きていない」と読み誤る。

（書き手: 起票者、日付: 2026-11-19。利用者の指示の原文ではない。「10 系の課題を引きずらず、11 系の設計思想に合わせる」は #3812 の本文が写す利用者の方針の引用）
