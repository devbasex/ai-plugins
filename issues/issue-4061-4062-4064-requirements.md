# #4061 #4062 #4064: 中継の子の hook だけが中継へ書き、環境変数の契約が中継の変数をすべて持ち、hook の締め切りが先の拒否を失わない

正は 3 つの課題の本文（#4061・#4062・#4064）で、この文書はその写しをまとめたものである。本文から作り直す。手で直さない。課題ごとの節（「何が起きたか」「受け入れ条件」）は、その課題の本文の同じ節の写しである。

中継のラッパーの子（中継の下で動く claude）の hook と、その子のシェルから起動した別の claude（入れ子の claude）の hook を見分け、中継の保存先へ書くのを子の会話の hook だけにする（#4061）。中継が子へ渡す環境変数を環境変数の契約にすべて載せ、載せ漏れをテストで落とす（#4062）。hook が処理の締め切りで打ち切られても、それまでに確定した拒否を出力に残し、後の guard にも知らせる（#4064）。

## 何が起きたか

### #4061: 入れ子の claude の hook が、親のラッパーの質問のファイルを書きうる

2026-11-22、#4049 の設計（ https://github.com/devbasex/ai-plugins/pull/4060 ）で背景の作業の記録を起動ごとのファイルにする理由を調べる中で、範囲の外として見つけた。中継のラッパーの子のシェルから起動した claude（`ndf plan queue` の worker・`claude -p` など）は、環境変数 `NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH` をそのまま引き継ぐ。止めた前の起動を見分ける起動の ID（#3814）も同じ値なので、入れ子の claude の hook は今の子の hook として通る。

2026-11-22 に main の `1c0b79b3e` で、中継の保存先へ書く・消す hook を読んで確かめた（パスは `plugins/ndf/src/ndf/domains/` からの相対）。

| guard | 事象 | 入れ子の claude で起きること | 場所 |
| --- | --- | --- | --- |
| `ask-forward` | PreToolUse（AskUserQuestion） | 親のラッパーの `question.json` を今の起動の質問として書き、オーガナイザーのレーンなら `questions/<レーン>.json` にも書く。ラッパーとオーガナイザーは、入れ子の claude の質問を子の質問として人へ回す | `hooks/guards/ask.py` |
| `ask-closed` | Stop・UserPromptSubmit | 入れ子の claude が応答を終えるたびに、子の回答待ちの質問を「閉じた」として消す。人が答える前に質問が消える | `hooks/guards/ask_clear.py` |
| `ask-answered` | PostToolUse（AskUserQuestion） | 同じ `tool_use_id` の質問だけを消すので、子の質問には当たらない（`tool_use_id` が違う） | 同上 |
| `relay-marker` | Stop | 入れ子の claude の最後の応答が次のセグメントのブロックを持てば、親の `next.json` を書く。ラッパーは子の会話を次のセグメントへ切り替える | `hooks/guards/relay_mark.py` |
| `context-limit` | PreToolUse | 入れ子の claude が運用上限を超えると、「次のセグメントのブロックを書いて終える」と案内する（入れ子の claude のブロックは中継に拾われない） | `hooks/guards/context_limit.py` |

中継の環境変数を外しているのは、アカウントの登録（`relay/account_login.py` の `AUTH_ENV`）だけである。lracct（#3604・#3864）の設計の問い 11（`issues/issue-3604-3864-design.md`）は、`ndf plan` の `claude -p` の子から中継の変数を外すかを「hook が何を書くかは確かめていない」ので変えないとし、確かめた結果で別の課題にするとした。この課題がその確かめである。

### #4062: 環境変数の契約に `NDF_RELAY_LAUNCH` が載っていない

#4049 の設計（PR #4060）で見つけた。中継のラッパーが子へ渡す `NDF_RELAY_LAUNCH`（`relay/signals.py` の `LAUNCH_ENV`・`launch_env`）が、環境変数の契約 `plugins/ndf/contracts/domains/env.json` に無い。契約の要約は「コードは名前をこの表から引き、表に無い名前を読まない」と書くが、hook の経路は契約を import しない（`signals.py` の冒頭・決定 111）ため、名前を文字列で持ち、載せ漏れを落とす検査が無い。#4049 は同じ経路で `NDF_RELAY_SESSION` を足す。

### #4064: hook の締め切りの出力が、先に確定した拒否を失う

#4049 の設計 PR #4060 の cross-review の指摘 r2-1・r2-2・r3-1・r3-2（ https://github.com/devbasex/ai-plugins/pull/4060#issuecomment-7073958501 ）で出た。hook のプロセスが締め切り（PreToolUse は起動から 1 秒）の割り込みで打ち切られると、出力（`hooks/main.py` の `_deadline_out`）は `fail_closed` の guard の `on_deadline` だけを見て、それまでに `dispatch.run` が確定した `fail_closed` でない guard の拒否（`context-limit`・`full-test` など）を出さない。#3995 の設計の決定 6 は「この変更は変えない」とし、#4049 も範囲の外として外した。

あわせて、後の guard は先の guard の拒否を知らない。#4049 の `relay-busy` は、先の guard が拒否した背景の作業の開始にも「1 件以上」を書く（#4049 の決定 2 の代償）。今の `ask-forward`（`order` 60）も、先の `context-limit`（`order` 20）が拒否した AskUserQuestion の質問を中継へ書く。

## 種類

- #4061: 不具合の修正（入れ子の claude が親のラッパーの状態を書き換える）
- #4062: 不具合の修正（契約の載せ漏れ）
- #4064: 不具合の修正（確定した拒否が締め切りで失われる）。hook のエントリポイントと dispatch の間の契約が変わる

## 対象範囲

含む:

- 中継の保存先とオーガナイザーの質問の保存先へ書く・消す hook の guard（`ask-forward`・`ask-answered`・`ask-closed`・`relay-marker`・`relay-busy`）と、中継の下かで案内を変える `context-limit`
- 中継の変数の環境変数の契約（`env.json` の行と `child` の列）と、ラッパーが子へ渡す名前と契約を結ぶテスト
- hook のエントリポイント（`hooks/main.py`）と dispatch（`hooks/dispatch.py`）の間の、確定した拒否を渡す契約。`fail_closed` の guard の `on_deadline` の引数
- 手順書（`plugins/ndf/skills/development-workflow/references/relay.md` の hook の一覧）と生成文書（`plugins/ndf/docs/contracts/env.md`・`hooks.md`）の直し

含まない:

- 入れ子の claude を起動する側（`ndf plan queue`・`ndf supervise`・`external-ai` の `claude -p`）の環境の組み立ての変更
- オーガナイザーの変数（`NDF_ORGANIZER_DIR`・`NDF_ORGANIZER_LANE`）を `ndf` のコマンドが読む経路（ゲートのリクエストなど）
- 環境変数の契約に無いほかの名前の棚卸し（`NDF_RUN_ID` など。この課題の外）
- 締め切りの値（`hooks.json` の予算）と、変更要求・案内の締め切りでの扱い
- Codex（中継のラッパーは Claude Code だけを包む）

## 前提

- 前提 1: #4049 の実装の後に実装する。#4049 がラッパーから子へ子の会話の ID（`NDF_RELAY_SESSION`）を渡し、`relay-busy` を足す
- 前提 2: Claude Code の hook の入力の `session_id` は、サブエージェントの中の hook でも親の会話と同じで、`claude -p` などで起動した別の claude では違う（#4049 の設計の「文脈と制約」。2026-11-22 に 2.1.330 で実測）
- 前提 3: `--resume` で起動し直した子の会話は、前の起動と同じ `session_id` を持つ（`wrapper.py` の `_start` が同じ会話の ID を渡す）
- 前提 4: NDF は普及のフェーズの前で、環境変数・hook のエントリポイントの契約を変えても互換の経路を持たない（MVV 版 11 の Value 4）
- 前提 5: hook のプロセスの締め切りは `SIGALRM` の割り込みで、guard の実行の途中で来る。割り込まれた guard の判定は分からない

## 受け入れ条件

### #4061

- [ ] 1. 中継の下で、hook の入力の `session_id` が子の会話の ID と違う会話（入れ子の claude）の hook は、中継の保存先のファイル（`question.json`・`next.json`・背景の作業の記録）を書かず、消さず、AskUserQuestion の質問をオーガナイザーの質問の保存先へも書かない。`context-limit` は中継の外の案内を出す
- [ ] 2. 子の会話の hook（サブエージェントの中の hook を含む）は、今のとおり質問を書き、閉じた質問を消し、次のセグメントのマーカーを書く。子の会話で中継の保存先が開けない（ディレクトリが消えた）ときは、質問を中継の外として通さずに拒否し、オーガナイザーの質問の保存先と知らせにも書かない
- [ ] 3. 止めた前の起動の hook（同じ会話の ID で、起動の ID が今の起動と違う）は、今のとおり今の起動の質問を書かず、消さない（#3814）
- [ ] 4. `NDF_RELAY_DIR` があり、子の会話の ID を受け取っていない会話（`NDF_RELAY_SESSION` が無いか空）の hook は、入れ子の claude と同じに扱い、中継の保存先とオーガナイザーの質問の保存先へ書かない。`NDF_RELAY_DIR` が無い会話は今のとおり中継の外として扱う
- [ ] 5. 1〜4 を、6 つの guard（`ask-forward`・`ask-answered`・`ask-closed`・`relay-marker`・`relay-busy`・`context-limit`）それぞれに、子の会話・入れ子の claude・止めた前の起動の 3 つの入力を hook のエントリポイントから流し、書いたファイルの有無と中身で確かめるテストがある

### #4062

- [ ] 6. 環境変数の契約（`env.json`）に `NDF_RELAY_LAUNCH` の行があり、意味・値を入れる者・読む者・中継が起動する子への渡し方を持つ。`NDF_RELAY_DIR`・`NDF_RELAY_SESSION` の行も同じ項目を持つ
- [ ] 7. 中継のラッパーが子へ渡す環境変数の名前（`signals.launch_env` が返す名前）が、どれも契約の `relay` の行にあることをテストが確かめる。契約に無い名前を `launch_env` に足すとテストが落ちる
- [ ] 8. 生成文書 `plugins/ndf/docs/contracts/env.md` に 3 つの行が出る（`ndf check --write-spec` で作り、手で書かない）

### #4064

- [ ] 9. hook のプロセスが締め切りの割り込みで打ち切られたとき、出力は、割り込みの前に確定した拒否（`fail_closed` でない guard が返し、案内へ下げられなかった `deny`）の理由を含む。`fail_closed` の guard の `on_deadline` の拒否とともに 1 つの拒否として出す
- [ ] 10. 割り込みの前に確定した拒否が無ければ、出力は今と同じ（`fail_closed` の guard の `on_deadline` だけで決まる）。案内へ下げた拒否・変更要求・案内は締め切りの出力へ引き継がない
- [ ] 11. 後に呼ばれる guard と `fail_closed` の guard の `on_deadline` は、先に拒否を確定した guard の名前を受け取る
- [ ] 12. 先の guard が拒否した AskUserQuestion では、`ask-forward` は中継とオーガナイザーへ質問を書かない（締め切りの割り込みのときも書いた質問を残さない）
- [ ] 13. 先の guard が拒否した背景の作業の開始では、`relay-busy` は背景の作業の記録を書かない（#4049 の決定 2 の「多めに倒す」代償を外す）
- [ ] 14. 9〜13 を、割り込みを起こす差し替え（時計か割り込みの関数）と guard の呼ばれた回数・出力の中身で確かめるテストがある（時間で区切らない）。.md の文言を照らすテストにはしない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。hook の guard の `on_deadline` の引数が増え、guard へ渡す部品に先の拒否が増える。環境変数の契約の行が増え、中継の変数の子への渡し方が決まる。コマンドの引数と結果 JSON は変わらない |
| データ | 変わらない（シグナルファイルの形は今のとおり） |
| 既存の振る舞い | 入れ子の claude の質問は中継とオーガナイザーへ回らなくなる（入れ子の claude の画面に出るだけ）。締め切りで打ち切られた呼び出しが、確定した拒否どおりに止まる。旧いラッパー（子の会話の ID を渡さない）の下の新しい hook は、質問を中継へ回さない |

## 用語

| 用語 | 意味 |
| --- | --- |
| 中継のラッパー | 利用者の claude を包んで常駐し、文脈が大きくなった会話を区切って次の会話（セグメント）を起動し直し、子の質問を人やオーガナイザー（並列のレーンを束ねる会話）へ回す NDF の仕組み（`ndf relay run`） |
| guard | hook の中で事象ごとに呼ばれる判定の単位。拒否（`deny`）・変更要求（`pushback`。応答を続けさせる）・案内（`advise`）・通すのどれかを返す。同じ拒否が続くと拒否を案内へ下げる（数え手）。`fail_closed` の guard は締め切りの後も `on_deadline` で拒否を返せる |
| 知らせ | 質問を待つことを利用者の通知の宛先へ送る送信待ち（`outbox`） |
| 子の会話 | 中継のラッパーが起動した claude の会話。hook の入力の `session_id` がラッパーの渡した会話の ID と同じ。サブエージェントの中の hook も含む |
| 入れ子の claude | 子のシェルから起動した別の claude（`ndf plan queue` の worker・`claude -p` など）。中継の環境変数を引き継ぐが、`session_id` が違う |
| 起動の ID | ラッパーが子を起動するたびに作る ID（`NDF_RELAY_LAUNCH`・`launch.json`）。止めた前の起動の hook を見分ける（#3814） |
| 確定した拒否 | dispatch が guard を呼び終え、案内へ下げないと決めた `deny` |
| 締め切りの出力 | hook のプロセスが締め切りの割り込みで打ち切られたときにエントリポイントが出す出力（`hooks/main.py` の `_deadline_out`） |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | ラッパーが子を起動した | セグメントの始まり・起動し直し | 今のとおり止まる | — |
| E2 | 子のシェルが入れ子の claude を起動した | `ndf plan queue`・`claude -p` など | — | E1 |
| E3 | hook が呼ばれた | 子の会話か入れ子の claude の事象 | — | E1 |
| E4 | hook が子の会話かを決めた | E3 | 決められなければ子の会話でないとする | E3 |
| E5 | guard が拒否を確定した | dispatch が guard を呼び終えた | — | E3 |
| E6 | 締め切りの割り込みが来た | 起動から予算の秒が過ぎた | — | E3（E5 の前でも後でも起こる） |
| E7 | エントリポイントが出力を出した | dispatch の終わりか E6 | — | E5・E6 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/hooks plugins/ndf/tests/domains/relay -q -n 4`（CI テストの設定があれば PR の CI の結果で決める） |
| 静的解析・型検査 | CI の既存のジョブ（PR の CI の結果で決める） |
| 生成文書 | `ndf check --write-spec` の後に差分が無いこと（CI の既存のジョブ） |
| 手動確認 | リリースの後、中継の下のレーンで `ndf plan queue` の worker が動く間に、子の回答待ちの質問が消えないこと・入れ子の claude の質問が `ndf relay ls` と計画のリクエストに出ないことを、ラッパーの記録（`log.jsonl`）で利用者が見る |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 直し方は「子を起動する口で外すか、hook が自分の起動かを確かめる」のどちらか | #4061 の本文の「期待する結果」 | 設計で決める（受け入れ条件は振る舞いだけを書く） |
| S2 | `NDF_RELAY_LAUNCH` を「使わないなら消す」 | #4062 の本文 | 使う（止めた前の起動を見分けるのに要る。前提 3）。載せる |
| S3 | 先の拒否を「後の guard と締め切りの出力へ渡す」 | #4064 の本文 | 受け入れ条件 9〜13 |

## リクエスト（原文）

- #4061: 「入れ子の claude の hook が親のラッパーの状態を書かない（子を起動する口で中継の環境変数を外すか、hook が自分の起動かを確かめる）」
- #4062: 「契約に載せ、意味・書き手・読み手を書く（または使わないなら消す）」
- #4064: 「先に確定した拒否を、後の guard と締め切りの出力へ渡す契約を dispatch と hook のエントリポイントに置く」

## 由来

- #4061・#4062: #4049 の設計（PR #4060）の中の範囲の外の発見
- #4064: #4049 の設計 PR #4060 の cross-review の指摘 r2-1・r2-2・r3-1・r3-2
- 全体の方針: #4069（並列開発の全体の設計方針）
