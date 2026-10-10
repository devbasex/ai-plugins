# #1492: relay: /goal で再開した区間が条件の Skill を読み込まず、承認ゲートを AskUserQuestion で出さない

正は課題の本文（#1492）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> relay が次の区間を始めるときの最初の入力は `/goal <条件>` か定型の文だけで（`plugins/ndf/scripts/relay_lib/claude.py:485-487`）、**条件に書いた `/ndf:development-workflow` の Skill は読み込まれない。** 読まずに進んだ conductor は承認ゲートで `AskUserQuestion` を出さず、本文で承認を待って応答を終えた。`development-workflow/references/relay.md:338-346` にも再開したら Skill を読み込む決まりは無く、本文で承認を待つことを検知する hook も無い。
>
> ## どこで見つけたか
>
> マイルストーン 26 の関門（#1322 の場面）。利用者のメモリで回避中。振り返り（マイルストーン 26 の区切り、10.17.45 まで。材料は `issues/handoff-milestone26.md` の「振り返りの材料」）
>
> ## 依頼
>
> - 再開の最初の入力が、条件にある Skill を読み込む形になるようにする
> - 承認ゲートを本文で待って応答を終えたら止めて `AskUserQuestion` へ戻す仕組みを検討する
>
> ## 同じ形のもう 1 つの経路
>
> 利用上限での切り替え（`relay_lib/claude.py:487-490` の `resume_input`。最初の入力は `/goal <条件>` だけ）に加え、通常のカットポイントの `ndf-next` も中身の先頭に `/goal ` を付ける決まりである（`relay.md` の付則「次のセッションへ引き継ぐ」、427 行）。どちらも Skill を呼ぶ入力を作らない。直す場所は、`/goal` の条件と Skill の呼び出しを分ける規約（`ndf-next` の形と `resume_input` の両方）である。
>
> 依頼 2（本文で承認を待って終えたら止める）は #1286 の案 A / C と同じ検知が要る。どちらへ寄せるかを着手の時に決める。
>
> ## 受け入れ条件（案）
>
> - [ ] relay の偽の claude（`tests/fixtures/relay_fake_claude.py`）で、再開した区間が条件の Skill を読み込むことを確かめる試験がある
> - [ ] `relay.md` の `/goal` の付則に、再開したら条件の Skill を読み込むことが書かれている
> - [ ] カットポイントの `ndf-next` から始めた区間も、条件の Skill を読み込む

## 目的

- `/goal /ndf:development-workflow #<番号>` で始めたセッションを relay が切り替えても、次のセッションの conductor が作業の前に条件の Skill（`development-workflow` など）を読み込み、Skill が定める承認ゲートの止まり方（`AskUserQuestion`）で止まる
- Skill を読まずに本文で承認を待って応答を終えた場合も、その応答で止めて `AskUserQuestion` へ戻し、承認のないまま先へ進まない（`approval-request.md` の「止まる手段」）
- 利用者のメモリ（「再開したら goal の Skill を読み込む」）での回避を不要にする

## 前提

- 前提 1: 「条件の Skill」は、`/goal` の条件の先頭の語が `/<プラグイン>:<Skill>` の形（例: `/ndf:development-workflow`）のときのその Skill を指す。条件の途中に出る Skill 名と、プラグイン名の無い `/<名>` は対象にしない
- 前提 2: 条件の Skill を読み込む手段（最初の入力の形を変える・`SessionStart` hook で読み込みを指示する、など）は設計が決める。どの手段でも、次のセッションの目標（`/goal` の条件）は前のセッションと同じ文面のまま設定される
- 前提 3: 次のセッションの最初の入力を作る経路は 3 つある。カットポイントの `ndf-next`（`relay_lib/mark.py` が拾い `switch.py` が起動する）、利用上限での切り替え（`claude.py` の `resume_input`・`switch.py` の `limit_start`）、`/ndf:restart`（引数が無いとき目標の入力を再開コマンドにする）。3 つとも対象にする
- 前提 4: 依頼 2 の検知（本文で承認を待って応答を終えたら止める）は、この課題で入れる。#1286（`ndf-next` と同じ応答に書いた質問）とは判定の対象が同じ「本文の問い・承認待ち」なので、判定は 1 か所に置き、#1286 が使える形にする。#1286 の受け入れ条件（`ndf-next` を出す応答の扱い）はこの課題では満たさない
- 前提 5: 依頼 2 の検知が働くのは relay の直接の子の会話（`relay.py is-child` が 0）だけである。ラッパーの外の会話と `claude -p` の子（supervisor・worker）では止めない。`claude -p` では `AskUserQuestion` が使えないためである
- 前提 6: 依頼 2 の判定は文面のヒューリスティックで、誤検知がありうる。誤検知の害を 1 回分の応答の継続に抑えるため、同じ応答の続きでは 2 度止めない
- 前提 7: Codex / Kiro / agy は relay の対象外（relay は Claude Code の子を起動する）なので、この課題の対象に含めない

## 対象範囲

含む:
- 3 つの経路（前提 3）で、次のセッションが条件の Skill を読み込む形にすること
- `relay.md` の付則「`/goal` を付けた場合」と `context-window.md` の「新しい会話で戻す」へ、再開したら条件の Skill を読み込む決まりを書くこと
- relay の直接の子で、`AskUserQuestion` を呼ばずに承認待ちの文で応答を終えたとき、Stop を 1 度止めて `AskUserQuestion` で出し直すよう伝えること
- 偽の claude（`tests/fixtures/relay_fake_claude.py`）を使う試験と、判定の単体の試験

含まない:
- #1286 の受け入れ条件（`ndf-next` と同じ応答の質問で切り替えを見送る・規約に書く）
- `/goal` を付けずに始めたセッションの最初の入力の形（今のまま `/ndf:development-workflow #<番号>` を使う）
- Claude Code 本体の `/goal` の振る舞い（条件の中の Skill を展開しないこと）を変えること
- Codex / Kiro / agy の再開の手順

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | セッションを終えるシグナルファイルを書いた | `ndf-next` のブロックを出した応答の Stop（`mark`）・利用上限の StopFailure（`limit`）・`/ndf:restart` | シグナルファイルを書けなければ切り替えない（今の振る舞い） | — |
| E2 | 次のセッションの最初の入力を作った | E1 のシグナルファイル | 未達の目標も `ndf-next` も無ければ定型の文にする（今の振る舞い） | E1 |
| E3 | 次のセッションを起動した | E2 の入力 | 起動に失敗したら `stop`（`start-failed`）を記録して終わる（今の振る舞い） | E2 |
| E4 | 次のセッションで目標を設定した | E3 の最初の入力の `/goal <条件>` | 目標が設定されなければ未達の判定が働かない。受け入れ条件 4 で防ぐ | E3 |
| E5 | 次のセッションで条件の Skill を読み込んだ | E3 の最初の入力かセッションの起動の hook（前提 2） | 条件の Skill が導入されていない・名前が解決できないときは、読み込まずに目標の条件のまま続ける（受け入れ条件 6） | E3。E4 との前後は問わないが、どちらも作業の最初の Tool の呼び出しより前 |
| E6 | conductor が `AskUserQuestion` を呼ばずに承認待ちの文で応答を終えた | 承認ゲートで Skill の止まり方を守らなかった | — | E5 が起きなかったか、Skill を読んでも守らなかった |
| E7 | Stop を 1 度止めて `AskUserQuestion` で出し直すよう伝えた | E6 を Stop hook が判定した | 判定を誤ったときは、続いた応答で同じ判定をしても 2 度止めない（前提 6） | E6。relay の直接の子のときだけ（前提 5） |
| E8 | 止めた事実を記録した | E7 | 記録に失敗しても Stop の判定は変えない | E7 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 条件の Skill | `/goal` の条件の先頭の語が名指しする Skill（前提 1） |
| 承認待ちの文 | `AskUserQuestion` を呼ばずに、本文で利用者の承認・判断を待つと書いた文（例: 「承認を待ちます」「よろしいでしょうか」）。判定の語の一覧は設計が決める |

## 受け入れ条件

- [ ] 1. 偽の claude の試験で、`/goal /ndf:development-workflow #895` で始めたセッションがカットポイントの `ndf-next`（中身 `/goal /ndf:development-workflow #895`）を出して切り替わると、次のセッションに `development-workflow` の Skill を読み込ませる入力（最初の入力か、セッションの起動の hook の出力。前提 2）が届く
- [ ] 2. 偽の claude の試験で、会話の記録の最後の `goal_status` が未達（条件 `/ndf:development-workflow #895`）のセッションが利用上限で切り替わると、次のセッションに受け入れ条件 1 と同じ読み込みの入力が届く
- [ ] 3. `/ndf:restart` を引数なしで打った `/goal /ndf:development-workflow #928` の会話から作る再開コマンドで起動したセッションにも、受け入れ条件 1 と同じ読み込みの入力が届く（試験か、手順の再開コマンドの形で確かめる）
- [ ] 4. 受け入れ条件 1〜3 の次のセッションでも、目標の条件は前のセッションと同じ文面（`/ndf:development-workflow #895` など）で設定される（最初の入力か起動の記録で確かめる）
- [ ] 5. 条件の先頭が Skill 名でない `/goal`（例: `/goal 引継ぎ文書の続きから`）と、`/goal` の無い `ndf-next` では、次のセッションの最初の入力が今と同じになる（退行しない）
- [ ] 6. 条件の Skill が導入されていないとき（例: `/goal /nosuch:skill #1`）も、次のセッションは起動し、目標は設定される（読み込みの失敗でセッションを止めない）
- [ ] 7. `relay.md` の付則「`/goal` を付けた場合」の表に、次のセッションが条件の Skill を読み込むことと、その手段（前提 2 で設計が決めたもの）が書かれている。`context-window.md` の「新しい会話で戻す」の `/goal ` の行が同じ決まりを指している
- [ ] 8. relay の直接の子の会話で、最後の応答が `AskUserQuestion` を呼ばずに承認待ちの文で終わると、Stop hook が Stop を 1 度止め、`AskUserQuestion` で出し直すことを伝える文を返す（試験で確かめる）
- [ ] 9. 受け入れ条件 8 で止めた後の続きの応答が再び承認待ちの文で終わっても、Stop hook は 2 度目を止めない
- [ ] 10. ラッパーの外の会話と、承認待ちの文を含まない応答と、`AskUserQuestion` を呼んで終えた応答では、受け入れ条件 8 の Stop の止めは起きない
- [ ] 11. 受け入れ条件 8 で止めたとき、relay の作業ディレクトリの `log.jsonl` に 1 行残り、止めた回数を後から数えられる
- [ ] 12. 全体の試験が通る（`uv run --frozen --project . --all-extras pytest . -q -n 4` の終了コード 0）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 受け入れ条件 8 の判定が失敗（例外・記録が読めない）しても Stop を止めない。切り替えの判定（`mark`）の結果を変えない |
| 運用・保守性 | 承認待ちの文の判定は 1 か所に置き、#1286 が同じ判定を使える（前提 4）。判定の語の一覧はコードの定数の 1 か所に置く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`ndf-next` の中身の形か、セッションの起動の hook の出力が変わる（前提 2）。`/goal` の無い `ndf-next` と既存の引継ぎ文書の `ndf-next` はそのまま使える（互換あり） |
| データ | `log.jsonl` に Stop を止めた行が足される。既存の行の形は変えない |
| 既存の振る舞い | relay の直接の子で、承認待ちの文で終えた応答が 1 度だけ続く |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（relay の試験は `plugins/ndf/scripts/tests/` の偽の claude を使うもの） |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/doc-lint.py`（Skill の文書を変えたとき）・`claude plugin validate .` |
| 手動確認 | リリース後に、ラッパーの下で `/goal /ndf:development-workflow #<番号>` の会話から `ndf-next` で切り替え、次のセッションの会話の記録に `development-workflow` の Skill の読み込み（Skill の展開か Skill の Tool の呼び出し）があることを `~/.claude/projects/` の記録で確かめる（リリース後テストへ回す） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | relay の振る舞いは `plugins/ndf/scripts/relay_lib/`、規約は `plugins/ndf/skills/development-workflow/references/relay.md` と `context-window.md`（`AGENTS.md` の「NDFプラグインについて」）。安定版の経路（hook・結果の契約に当たる）なので `experimental/` に置かない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。Skill の文書は `plugins/ndf/skills/AUTHORING.md` |
| テスト戦略 | 切り替えの 3 経路は偽の claude を使う結合の試験、承認待ちの文の判定は単体の試験。`.md` の文言を照合する試験は書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体の試験の実行、既存の `ndf-next` の形との互換の確認 |
| 確認してから行う | `ndf-next` の形の変更と Stop hook の追加（承認ゲート 1・2 で人が見る） |
| 行わない | #1286 の切り替えの見送り、Claude Code 本体の `/goal` への手当て、Codex / Kiro / agy の手順の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 条件の Skill を読み込ませる手段（最初の入力の形か `SessionStart` hook か）と、Claude Code が 1 つの入力で目標の設定と Skill の展開を両立できるか | 設計（実機の `claude` で確かめてから決める。`AGENTS.md` の「外部コマンドも書く前に実行して確かめる」） | 設計の承認（ゲート 1）まで |
| 承認待ちの文の判定の語の一覧と、止めたことを数える記録の形 | 設計 | 設計の承認（ゲート 1）まで |
