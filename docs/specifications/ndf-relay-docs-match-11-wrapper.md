# 中継の手順書・確定仕様・用語集が書く記録と振る舞いを、11 系のラッパーのものだけにする

中継のラッパー（`ndf relay`。claude を包み、カットポイントで次の claude を新しい会話（セグメント）として起動する）の
文書は、11 系のラッパーが実際に書く記録の行・キーと、11 系のラッパーの振る舞いだけを書く。10 系のラッパーにだけあった
記録のキー（`account_dir` の行・`rule`・`account_rule`・`evidence` など）と、ラッパーの入れ替え（reexec・`handover.json`）・
更新の失敗での停止（`update-failed`）は、文書から消すか 11 系の振る舞いへ書き直し、11 系の正を指す。ラッパーのコードは
変えない。この文書は、その決定と理由・常に成り立つ条件・テスト観点を残す。中身の詳細は下の表の正本にある。

| 何を読むか | 正本 |
| --- | --- |
| `log.jsonl` の行の種類とキーの一覧・`jq` の例 | [relay.md の「記録の読み方」](../../plugins/ndf/skills/development-workflow/references/relay.md#記録の読み方) |
| アカウントを替えたときの `account` の行の読み方 | [relay-accounts.md](../../plugins/ndf/skills/development-workflow/references/relay-accounts.md) の、`log.jsonl` の `account` の行を書く段落（「worker の claude -p を API キーで動かす」の手前） |
| プラグインの更新・ラッパー自身の変更が効く時点 | [relay.md の「プラグインの更新とラッパーのバージョン」](../../plugins/ndf/skills/development-workflow/references/relay.md#プラグインの更新とラッパーのバージョン)、[ndf-relay-plugin-update.md](ndf-relay-plugin-update.md) |
| 更新と起動の失敗での 1 行と続け方 | [relay.md の「落ちたときの続け方」](../../plugins/ndf/skills/development-workflow/references/relay.md#落ちたときの続け方)、[ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) の「概要」の、落ちたときの表 |

## 概要

**例: アカウントが替わった理由を調べる。** 利用者か AI が `relay-accounts.md` を開き、そこに書かれたとおりに `log.jsonl` を
`jq` で引く。11 系のラッパーが書く `account` の行は次の形だけなので、手順書もこの形だけを書く。

```json
{"at": "2026-11-19T10:00:00+09:00", "section": 3, "kind": "account", "reason": "work1 の使用率 92% が閾値 85% 以上。work2（10%）へ替える"}
```

手順書が規則の名前（`rule`）や組（`account_rule`）を引くよう書いていると、11 系の記録にはそのキーが無いので結果は 0 件になり、
「替わっていない」と読み誤る。確定仕様と用語集がラッパーの入れ替えを書いていると、プラグインを更新すれば動いている
ラッパーも新しいバージョンになると読み、ラッパー自身の直しを届けるために `claude` を打ち直す必要に気付かない。

直した文書と、それぞれが 11 系の正として指す先は次のとおりである。

| 文書 | 11 系の記述 |
| --- | --- |
| 手順書 `relay-accounts.md` | 記録の行は `account` の行だけ。キーの一覧は `relay.md` の「記録の読み方」を指す |
| `ndf-relay-segment-restart.md` | 更新に失敗しても次のセグメントを起動する。子の claude を起動できなければ停止理由 `precondition`・終了コード 3 |
| `ndf-development-loop-limits.md`・`ndf-relay-multi-session-supervisor.md`・`ndf-relay-lane-parent-session-env.md`・`ndf-relay-auth-failure.md`・`ndf-relay-install-and-restart.md`・`ndf-relay-launch-target.md` | 入れ替えの記述を消す。ラッパー自身の変更は `/exit` で抜けて `claude` を打ち直した後に効く |
| 用語集（`docs/glossary/glossary.json` と生成物） | ラッパーのバージョンディレクトリ・ラッパーの入れ替え・入れ替えの申し送りの 3 語を持たない。ランチャーとプラグインの入れ替えの意味は 11 系のもの |

## 用語

ラッパー・セグメント・カットポイント・ランチャー・プラグインの入れ替えの定義は、設定の用語集（`.ndf/glossary.json` の
`document` が指す [docs/glossary.md](../glossary.md)）にある。この文書で「ラッパーの入れ替え」は、10 系のラッパーが
プラグインの更新の後に自分のプロセスを新しいバージョンのコードで起動し直していた仕組み（reexec）を指す。11 系には無い。

## 背景

- 11.0.0 の作り直し（#3304）で中継のラッパーを作り直したとき、10 系のラッパーの記録と振る舞いを書いた中継の文書を書き直さなかった。
  その後 #3812（11.0.8）が手順書 `relay.md` だけを 11 系へ直したため、同じ記録と同じ仕組みについて 11 系と 10 系の 2 つの説明が並んだ
- 文書の記述をコードと照らす検査は無い（`.md` の文言を照らすテストは書かない。`AGENTS.md` の DON'T）。食い違いは、文書を直す作業の途中で見つかった
- 手順書の記録のキー（#3846）と、確定仕様と用語集の入れ替え（#3847）は、原因が同じで直し方も同じなので、1 つの変更で直した

## 対象範囲

| 含む | 含まない |
| --- | --- |
| `relay-accounts.md` の、`log.jsonl` と進捗ログの行・キーを読み方として書く文と、同じ文が書く画面の 1 行 | `relay-accounts.md` のほかの 10 系の記述（アカウントの選び方・上限と認証の失敗での切り替え・`ndf plan` の API キー）。#3864 が持つ |
| 上の「概要」の表の確定仕様 7 本の、ラッパーの入れ替え・`update-failed`・`start-failed` の記述 | 確定仕様のほかの 10 系の記述（置き場 `relay_lib`・`relay.current`・再起動ループ・記録の形）。#3865 が持つ |
| 用語集の 3 語の廃止と 2 語の意味 | ラッパーのコードの変更、`docs/archive/` の文書、[ndf-relay-plugin-update.md](ndf-relay-plugin-update.md) の 10 系との対比の記述 |

## 仕様

### 決定と理由

| 決定 | 理由 |
| --- | --- |
| 文書を 11 系の記録と振る舞いに合わせる側で直す。ラッパーに 10 系のキーや入れ替えを足さない | 10 系の課題を引きずらず、11 系の設計に合わせる方針である。使われていない仕組みを作り直すことになる |
| 11 系に無い振る舞いの結果を述べる文は、行やキーの名前だけを替えずに文ごと消す | 名前だけを 11 系の名前へ替えると、11 系に無い振る舞いを 11 系の記録が示すと読める |
| 直した文書は 11 系の振る舞いの中身を写さず、正本（`relay.md`・`ndf-relay-plugin-update.md`）を指す | 同じ振る舞いの説明を複数の文書に持つと、片方だけが古くなる |
| `ndf-relay-segment-restart.md` は `docs/archive/` へ移さず、入れ替え・更新の失敗・起動の失敗の記述だけを直す | 確定仕様と手順書の 15 か所から参照されており、移すと参照が切れる |
| 作るか消すかの判断が要る残りの 10 系の記述は、別の課題（#3864・#3865）にする | 記録の読み方の直しと同じ変更に入れると判断が混ざる。代償として、それらが閉じるまで、直した箇所と 10 系の記述が残った箇所が同じ文書に並ぶ |
| 用語集は語を決める設計の変更（PR #3867）で直した | 設計の手順は、新しい語・意味の変わった語・廃止する語を同じ変更で用語集へ反映すると決めている。消した 3 語は、自分の定義の外から参照が 0 件だった |

### 常に成り立つ条件

1. **文書が `log.jsonl` の行とキーとして書くのは、11 系のラッパーが書くものに限る。** アカウントの切り替えの行は `account` の行だけで、
   キーは共通の `at`（`signals.append_jsonl` が付ける）・`section`（`Wrapper._log` が付ける）と、固有の `kind`・`reason` である。
   カットポイントで使用率を見て替えたとき（`Wrapper._on_switch`）も、利用上限で替えて同じ会話を続けるとき（`Wrapper._note`。
   判定の手の種類 `account`）も同じ形である
2. **`reason` は替えた理由の説明の文で、規則の名前ではない。** 文書は `reason` を `rule`・`pinned` などの名前として書かない
3. **文書が書く画面の `ndf-relay:` の 1 行は、`plugins/ndf/src` が出す文に限る。** アカウントの切り替えの 1 行（「替えて続ける」「を使わない」）は書かない
4. **中継の確定仕様と用語集は、ラッパーの入れ替えを今の振る舞いとして書かない。** ラッパー自身のコードの変更は、`/exit` で抜けて
   `claude` を打ち直した後に効く。文書に残る「入れ替え」は、プラグインの入れ替え（作り直した NDF への移行）の意味だけである
5. **プラグインの更新に失敗しても、ラッパーは止まらず今のバージョンで次のセグメントを起動する。** 子の claude を起動できないときは、
   停止理由 `precondition`・終了コード 3 で終わる（#3842）。文書は終了コード 2 で止まること・`update-failed`・`start-failed` を書かない
6. **用語集は 10 系の語（ラッパーのバージョンディレクトリ・ラッパーの入れ替え・入れ替えの申し送り）を持たない。** 用語集の生成物は
   `glossary.json` から `ndf records glossary render` で作り、手で直さない

## テスト観点

テストのコードは足さない。どれも文書の直しで、確かめは検索と検査のコマンドで行う。

| 観点 | 確かめ方 | 壊れたときに落ちるもの |
| --- | --- | --- |
| 手順書に 10 系のキーが残らない（条件 1） | `grep -n "account_dir\|local_only\|account_rule\|to_key\|launch_rule\|evidence" plugins/ndf/skills/development-workflow/references/relay-accounts.md` が 0 件 | 10 系のキーを 1 つでも書く |
| `account` の行と `reason` の書き方、画面の 1 行（条件 1〜3） | レビューで、直した文を `wrapper.py` の `Wrapper._on_switch`・`Wrapper._note` と照らす。`api_key` の行と `rule` のキーは、#3864 へ残す API キーの説明と `account_rule` などへの部分一致があり、検索の 0 件を条件にできないのでここで見る | `reason` を規則の名前として書く・11 系に無い行を書く |
| 確定仕様に入れ替えの語が残らない（条件 4） | `grep -rn "relay_reexec\|relay_version_dir\|relay_handover\|relay_bundle\|update-failed\|handover.json\|relay.current\|reexec\|from_handover\|handover.py\|handover-prepare" docs --include=*.md --include=*.json` の当たりが、`/archive/`・`ndf-relay-plugin-update.md`・`ndf-parallel-plan-ledger.md`・`ndf-shared-venv-and-relay-ls-failure.md`・`ndf-deps-require-groups-check.md` だけ（後ろの 2 つはスクリプトを起動し直すテストの名前） | 入れ替えの記述が 1 つでも残る |
| 「入れ替え」の意味（条件 4） | `grep -n "入れ替え" docs/specifications/ndf-development-loop-limits.md docs/specifications/ndf-relay-*.md` の当たりが、`ndf-relay-plugin-update.md` のほかはプラグインの入れ替えの意味だけ | ラッパーの入れ替えを今の振る舞いとして書く |
| 更新と起動の失敗（条件 5） | レビューで、`ndf-relay-segment-restart.md` を `relay.md` の「プラグインの更新とラッパーのバージョン」と #3842 の振る舞いと照らす | 終了コード 2・`start-failed`・`update-failed` を書く |
| 用語集（条件 6） | `ndf records glossary render --check` と `ndf records glossary meanings` が `ok` | 生成物を作り直さない・消した語を意味の説明が参照する |
| リンク | `python3 scripts/check-markdown-links.py` が通る（見出しまで照らす） | `relay.md` の節の名前が変わり、指す先が切れる |

## 関連リンク

- 課題: [#3846](https://github.com/devbasex/ai-plugins/issues/3846)（手順書の記録のキー）・[#3847](https://github.com/devbasex/ai-plugins/issues/3847)（確定仕様と用語集の入れ替え）
- 分けた残り: [#3864](https://github.com/devbasex/ai-plugins/issues/3864)（`relay-accounts.md` のアカウントの選び方・切り替え・API キー）・[#3865](https://github.com/devbasex/ai-plugins/issues/3865)（`ndf-relay-segment-restart.md` ほかの置き場・再起動ループ・記録の形）
- 起動の失敗の振る舞い: #3842（停止理由 `precondition`・終了コード 3）
- スプリントの Pull Request: https://github.com/devbasex/ai-plugins/pull/3899
- 11 系の更新の仕様: [ndf-relay-plugin-update.md](ndf-relay-plugin-update.md)
