# #3847: 中継: 確定仕様と用語集が 11 系に無いラッパーの入れ替え（reexec・handover.json）と update-failed を書いたまま

正は課題の本文（#3847）で、この文書はその写しである。本文から作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者（課題を書いた AI と、設計の工程の AI）が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 10 系の記述は消すか 11 系の振る舞いへ書き直す（archive へ移すかは直す側が決める） | #3812 の本文が写す利用者の方針「10 系の課題を引きずらず、11 系の設計思想に合わせる」（起票者） | 受け入れ条件 1〜6。archive へは移さない（下の S3） |
| S2 | 直す文書を、課題の本文の 3 つに加え、触ってよい範囲（`docs/specifications/ndf-relay-*`）の中で同じ入れ替えの記述を持つ 5 つ（`ndf-relay-multi-session-supervisor.md`・`ndf-relay-lane-parent-session-env.md`・`ndf-relay-auth-failure.md`・`ndf-relay-install-and-restart.md`・`ndf-relay-launch-target.md`）へ広げる | 設計の工程の推定。2026-11-19 の調査で同じ語が見つかった。残すと期待する結果（確定仕様が 10 系の語を持たない）を満たさない | 受け入れ条件 1・4 |
| S3 | `ndf-relay-segment-restart.md` は archive へ移さず、入れ替え・`update-failed`・起動の失敗の記述だけを 11 系へ直す。ほかの 10 系の記述（置き場 `relay_lib`・再起動ループ・記録の形 `event`・導入の形）は別の課題にする | 設計の工程の推定。文書は確定仕様と手順書の 15 か所から参照され、移すと触ってよい範囲の外の参照が切れる。全体の書き直しは #3815（上限）・#3818（リレーハブ）と同じ判断が要る | 受け入れ条件 2。残りは含まない（#3865） |
| S4 | 用語 `relay_launcher` の意味（`relay_lib` を読み込む）も 11 系の起動の部品へ書き直す | 設計の工程の推定。消す 3 用語と同じ表に並び、同じ 10 系の置き場を意味に持つ | 受け入れ条件 3 |
| S5 | 範囲の外の文書 `docs/specifications/ndf-parallel-plan-ledger.md` の `relay.current` の記述は別の課題にする | 設計の工程の推定。計画の触ってよい範囲の外 | 含まない（#3865） |

## 目的

- 確定仕様と用語集を読む利用者と AI が、11 系にも入れ替えがあると読み誤らない。中継の確定仕様と用語集は、入れ替えをしない・更新に失敗しても止まらない 11 系の振る舞いだけを書く

## 事象

確定仕様と用語集が、11 系に無い 10 系のラッパーの入れ替え（reexec・`handover.json`）を書いたまま残っている。

| ファイル | 書いているもの |
| --- | --- |
| `docs/specifications/ndf-relay-segment-restart.md` | ラッパーの入れ替え・`update-failed`（46・116・126・226・236〜241・359・450 行ほか）・`start-failed` |
| `docs/glossary/glossary.json` と生成した `docs/glossary/ndf-relay.md` | 用語 `relay_version_dir`・`relay_reexec`・`relay_handover`。`relay_launcher` の意味の `relay_lib`。`docs/glossary/ndf-rebuild.md` の「プラグインの入れ替え」の意味の末尾「ラッパーの入れ替えとは別」 |
| `docs/specifications/ndf-development-loop-limits.md` 343・425 行 | 上限「ラッパーの入れ替えの用意の待ち」（`relay_lib/handover.py`）・「新しい版の判定は、ラッパーの入れ替えの後に効く」 |
| `docs/specifications/ndf-relay-multi-session-supervisor.md` 195・271・370 行 | 入れ替えの後の `claim_hub`・`hub-log.jsonl` の `version-switched`・#1587 への参照 |
| `docs/specifications/ndf-relay-lane-parent-session-env.md` 83〜124 行 | `handover.py` の `startup_env`・図の「ラッパーの入れ替えの後か」の分岐 |
| `docs/specifications/ndf-relay-auth-failure.md` 136 行 | `Relay.from_handover` |
| `docs/specifications/ndf-relay-install-and-restart.md` 123 行 | 「動いているラッパーは入れ替えない」 |
| `docs/specifications/ndf-relay-launch-target.md` 69 行 | 区切りの置き場として `handover.py` |

#3812（PR #3845、11.0.8）で、11 系のラッパーは入れ替えを作らず、ラッパー自身のコードの変更は `claude` を打ち直した後に効くと決めた。手順書 `relay.md` からは入れ替えの記述を消し、上限 `relay.handover-prepare` を `limits.json` から消した。同じ PR で足した確定仕様 `docs/specifications/ndf-relay-plugin-update.md` は、`update-failed` を 10 系との対比として書いており、直す対象に含まない。

3 つの用語は、`glossary.json` と生成物 `ndf-relay.md` の自分の定義の外から参照が 0 件である（2026-11-19 の調査）。

## 期待する結果

確定仕様と用語集が 11 系の振る舞い（入れ替えをしない・更新に失敗しても止まらない）だけを書き、10 系の語（`relay_reexec` など）を持たない。

## 再現手順

1. `grep -rln "relay_reexec\|relay_version_dir\|relay_handover\|update-failed" docs --include=*.md --include=*.json | grep -v archive` で上のファイルが出る（`ndf-relay-plugin-update.md` も出るが、10 系との対比の記述である）
2. `grep -n "入れ替え" docs/specifications/ndf-development-loop-limits.md` で 343・425 行が出る

## 環境

NDF `origin/main` 2e43af7bc（11.0.8 の後）

## 前提

- 前提 1: 11 系の振る舞いの正は、手順書 `relay.md` の「プラグインの更新とラッパーのバージョン」と確定仕様 `ndf-relay-plugin-update.md` である
- 前提 2: 起動の失敗の 11 系の振る舞いは #3842（同じ設計 Pull Request）が決める。`ndf-relay-segment-restart.md` の `start-failed` の記述はそれに合わせて書き直す

## 対象範囲

含む:
- 上の表の 8 つの文書の、ラッパーの入れ替え・`update-failed`・`start-failed`・`relay.current`・`handover`・`reexec` の記述を消すか 11 系の振る舞いへ書き直す
- 用語集から `relay_version_dir`・`relay_reexec`・`relay_handover` を消し、`relay_launcher` の意味と「プラグインの入れ替え」の意味の末尾を直し、生成物を作り直す

含まない:
- `ndf-relay-segment-restart.md` のほかの 10 系の記述（S3）と、`ndf-development-loop-limits.md` の `relay_lib` を指すほかの行。範囲外の課題に起票する
- `ndf-parallel-plan-ledger.md` の `relay.current`（S5）。#3865 が持つ
- `ndf-relay-plugin-update.md` の 10 系との対比の記述
- `docs/archive/` の文書

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 用語集の語を消した | 実装 | 消した語を参照する文書が残る: `ndf records glossary meanings` か文書の検査が落ちる | — |
| E2 | 生成物を作り直した | E1 | `render --check` が差分を出す | E1 |
| E3 | 読み手が確定仕様と用語集を読んだ | — | 10 系の語が残る: 11 系にも入れ替えがあると読む（直す対象） | E2 |

## 用語

用語集の語を消すだけで、新しい語を足さない。

## 受け入れ条件

- [ ] 1. `grep -rn "relay_reexec\|relay_version_dir\|relay_handover\|relay_bundle\|update-failed\|handover.json\|relay.current\|reexec\|from_handover\|handover.py\|handover-prepare" docs --include=*.md --include=*.json | grep -v "/archive/\|ndf-relay-plugin-update.md\|ndf-parallel-plan-ledger.md\|ndf-shared-venv-and-relay-ls-failure.md\|ndf-deps-require-groups-check.md"` が 0 件になる（除いた 2 つは、スクリプトを起動し直すテストの名前で、ラッパーの入れ替えではない）
- [ ] 2. `ndf-relay-segment-restart.md` が、プラグインの更新に失敗しても次のセグメントを起動すると書き、起動に失敗したときは #3842 の振る舞い（停止理由 `precondition`・終了コード 3）を書く。終了コード 2 で止まる・`start-failed`・`update-failed` を書かない
- [ ] 3. 用語集（`docs/glossary/glossary.json`）に `relay_version_dir`・`relay_reexec`・`relay_handover` のエントリが無く、`relay_launcher` の意味が `relay_lib` を含まず、「プラグインの入れ替え」の意味が「ラッパーの入れ替えとは別」を含まない
- [ ] 4. `grep -n "入れ替え" docs/specifications/ndf-development-loop-limits.md docs/specifications/ndf-relay-*.md | grep -v ndf-relay-plugin-update.md` の当たりが、プラグインの入れ替え（旧いプラグインからの移行）の意味のものだけになる
- [ ] 5. `ndf records glossary render --check` と `ndf records glossary meanings` が `ok` で終わる
- [ ] 6. 退行しない: `python3 scripts/check-markdown-links.py` が通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（文書と用語集だけ） |
| データ | 用語集のエントリが 3 つ減る。生成物 `docs/glossary/ndf-relay.md`・`docs/glossary.md` が変わる |
| 既存の振る舞い | 変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 静的解析 | 受け入れ条件 1・4 の `grep`、`ndf records glossary render --check`・`ndf records glossary meanings`、`python3 scripts/check-markdown-links.py` |
| 手動確認（マージ前） | 実装の Pull Request のレビューで、書き直した文が `relay.md` の「プラグインの更新とラッパーのバージョン」と一致することを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 確定仕様は `docs/specifications/`、用語集の正は `docs/glossary/glossary.json`（生成物は手で直さない。`ndf records glossary render`） |
| コーディング規約 | `.md` の文言を照合するテストを書かない（`AGENTS.md` の DON'T） |
| テスト戦略 | テストを足さない。検査のコマンドで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 用語集の生成と検査、リンクの検査 |
| 確認してから行う | 確定仕様を archive へ移すこと（この変更ではしない） |
| 行わない | 生成物の手編集、`ndf-relay-plugin-update.md` の対比の記述の直し |

## 由来

PR #3845（#3812 の実装）。手順書 `relay.md` を 11 系へ直す作業で、「記録の読み方」の末尾が指す確定仕様と用語集に同じ 10 系の記述が残っているのを見つけた。#3812 の要求の「対象範囲」は手順書 `relay.md`・`relay-accounts.md` だけで、詳細設計は用語集を「実装では触らない」としたため範囲外とした。根拠: Value 14「判断の理由が残り、語がそろっている」（MVV バージョン 9）。

## リクエスト（原文）

> ## 事象
>
> 確定仕様と用語集が、11 系に無い 10 系のラッパーの入れ替え（reexec・`handover.json`）を書いたまま残っている。
>
> #3812（PR #3845、11.0.8）で、11 系のラッパーは入れ替えを作らず、ラッパー自身のコードの変更は `claude` を打ち直した後に効くと決めた。手順書 `relay.md` からは入れ替えの記述を消し、上限 `relay.handover-prepare` を `limits.json` から消した。同じ PR で足した確定仕様 `docs/specifications/ndf-relay-plugin-update.md` は、`update-failed` を 10 系との対比として書いており、直す対象に含まない。
>
> ## 期待する結果
>
> 確定仕様と用語集が 11 系の振る舞い（入れ替えをしない・更新に失敗しても止まらない）だけを書き、10 系の語（`relay_reexec` など）を持たない。
>
> ## 影響
>
> 確定仕様と用語集を読む利用者と AI が、11 系にも入れ替えがあると読み誤る。

（書き手: 起票者、日付: 2026-11-19。利用者の指示の原文ではない。「10 系の課題を引きずらず、11 系の設計思想に合わせる」は #3812 の本文が写す利用者の方針の引用）

