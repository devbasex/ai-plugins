## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | フォアグラウンドのループの中の `sleep` は秒によらず止める | `waiting.md` の「禁じる待ち方」の 1 項目め | 受け入れ条件 1 |
| S2 | `pgrep -f` / `pkill -f` は待ちの条件に使ったときだけ止め、単発の確かめ（`pgrep -x dockerd` など）は止めない | `docker-container-access` の Skill が `pgrep -x` を単発で使う | 受け入れ条件 5・6 |

## 事象

`waiting.md`（`plugins/ndf/skills/development-workflow/references/waiting.md` の「禁じる待ち方」）が禁じる 2 つの待ち方を、hook が止めない。

| 禁じる待ち方 | hook の今の扱い（main 2e43af7bc） |
| --- | --- |
| フォアグラウンドの `while` / `until` のループの本体で `sleep` する形 | `sleep` の guard（`plugins/ndf/src/ndf/domains/hooks/guards/sleep.py`）は、1 回の `sleep` の秒を上限（`plugins/ndf/contracts/limits.json` の `hook.sleep`、30 秒）と比べるだけで、ループを見ない。`sleep 20` をループで繰り返すと通る |
| プロセス名で待つ（`pgrep -f` / `pkill -f`） | 照らす guard が無い。待つ側のコマンド行に同じ文字列が入るため自分に一致し、終わらない |

あわせて、次の形の `sleep` も上限によらず通る（10 系の判定はどれも見ていた）。

- `bash -c` / `sh -c` / `zsh -c` / `dash -c` / `eval` の中身の `sleep`（`bash -c 'sleep 600'` が通る）
- ラッパーのコマンドの後ろの `sleep`（`timeout 590 sleep 600`・`nohup sleep 600` が通る）

`waiting.md` の「hook」の表は 10 系の値（上限 `NDF_SLEEP_MAX_SEC` の既定 5・外し方 `NDF_SLEEP_GUARD=0`）を書いたままで、11 系のコードはどちらも読まない。

#3812 の通し（2026-11-19）で、supervisor と worker が `until ! pgrep -f "ndf refactor run --pr 3845"; do sleep 20; done` などを 12 回打った（前景 9 回・背景 3 回）。うち 1 回は自分に一致して約 2 時間止まった。

## 期待する結果

- フォアグラウンドの `while` / `until` のループの中の `sleep` を、1 回の秒によらず止め、Monitor か背景の起動を案内する
- `pgrep -f` / `pkill -f` を待ちの条件に使う呼び出しを止め、「終わりはファイルで待つ」を案内する

## 再現手順

1. Claude Code の NDF 11.0.8 の下で、Bash で `until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do sleep 20; done` を前景で打つ
2. hook に止められずに走り、自分のコマンド行に一致して終わらない

## 環境

NDF 11.0.8（導入先 `~/.claude/plugins/cache/ai-plugins/ndf/11.0.8`、main 2e43af7bc と同じ判定）・Claude Code・Linux

## 前提

- 前提 1: 上限の秒は今の値（`limits.json` の `hook.sleep`、30 秒）のまま変えない。10 系の既定 5 秒へは戻さない
- 前提 2: guard を外す手段は 11 系の `NDF_HOOK_OFF=<guard の名前>` だけとし、10 系の `NDF_SLEEP_GUARD` / `NDF_SLEEP_MAX_SEC` は読まない
- 前提 3: hook を置くのは Claude Code だけで、Codex・Kiro・agy の待ち方は今のまま（`waiting.md` の「hook」の表のランタイムの行）

## 対象範囲

含む:
- `sleep` の guard が、`while` / `until` のループの本体の `sleep` を秒によらず止める
- `sleep` の guard が、`bash -c` 系・`eval` の中身とラッパーのコマンドの後ろの `sleep` も判定する
- `pgrep -f` / `pkill -f` を `while` / `until` のループの条件か本体に置いた待ちを止める guard を足す
- `waiting.md` の「hook」の表を 11 系の値と新しい guard に合わせる

含まない:
- 上限の秒の値の変更（前提 1）
- `Read` の繰り返しの guard（`read-repeat`）と NDF のコマンドの連結の guard（`script-chain`）の判定
- Codex・Kiro・agy への hook の追加

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | Bash の呼び出しを hook が受けた | エージェントが Bash を打つ（PreToolUse） | 入力を読めなければ判定せずに通す | — |
| E2 | コマンド行を構文木にした | E1 | 構文木を作れなければ判定せずに通す | E1 |
| E3 | 禁じる待ち方を見つけて止めた | E2 の中に、ループの本体の `sleep`・上限を超える `sleep`・ループの条件か本体のプロセス名のマッチがある | — | E2 |
| E4 | 止めた理由と代わりの待ち方を返した | E3 | — | E3 |

## 用語

| 用語 | 意味 |
| --- | --- |
| プロセス名の待ち | `pgrep -f` / `pkill -f` を `while` / `until` のループの条件か本体に置いて、プロセスの終わりを待つ形 |

## 受け入れ条件

- [ ] 1. フォアグラウンドの Bash で、`while` / `until` のループの本体（入れ子のループを含む）にある `sleep` を秒によらず止める。`while true; do sleep 1; done` と `until [ -s out ]; do sleep 5; done` が拒否され、理由に `Monitor` か `run_in_background` の待ち方が載る
- [ ] 2. ループの外の `sleep` は今の上限で判定する。`sleep 5` は通り、`sleep 600` は止まる。`for` のループの中の上限以下の `sleep`（`for i in 1 2; do sleep 3; done`）は通る
- [ ] 3. `bash -c` / `sh -c` / `zsh -c` / `dash -c` / `eval` の中身と、ラッパーのコマンド（`timeout`・`nohup`・`env`・`nice`・`time`・`exec`・`command`）の後ろの `sleep` も 1・2 と同じに判定する。`bash -c 'while true; do sleep 1; done'` と `timeout 590 sleep 600` が止まる
- [ ] 4. 背景になる形の `sleep` は止めない。`run_in_background: true` の呼び出しと、末尾の `&` で背景になる形（`while true; do sleep 1; done &`）は通る
- [ ] 5. `pgrep` / `pkill` に `-f`（`--full` と、`-af` のように束ねたフラグを含む）を付けて `while` / `until` のループの条件か本体に置いた呼び出しを、フォアグラウンドでも `run_in_background: true` でも止める。再現手順 1 のコマンドが拒否され、理由に「終わりはファイルで待つ」（完了マーカーか `Monitor`）の待ち方が載る
- [ ] 6. ループの外の単発の `pgrep -f` / `pkill -f` と、`-f` を付けない `pgrep` / `pkill`（ループの中を含む）は止めない。`pgrep -x dockerd >/dev/null 2>&1 || echo x` と `until ! pgrep -x dockerd; do sleep 1; done &` は通る
- [ ] 7. 5 の guard は `NDF_HOOK_OFF=<その guard の名前>` で外せ、外すと 5 のコマンドが通る
- [ ] 8. `waiting.md` の「hook」の表が、上限の出所（`limits.json` の `hook.sleep`）と既定の 30 秒・外し方 `NDF_HOOK_OFF`・5 の guard の行を書き、`NDF_SLEEP_GUARD` と `NDF_SLEEP_MAX_SEC` を書かない
- [ ] 9. 退行しない: 既存の hook のテスト（`plugins/ndf/tests/domains/hooks/`）が CI で通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 構文木は 1 回の事象で 1 度だけ作る今の形を保つ。`bash -c` 系・`eval` の中身を読み直す深さには上限を置く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。hook が止める Bash の呼び出しが増える（受け入れ条件 1・3・5）。互換の経路は `NDF_HOOK_OFF` で guard を外すこと |
| データ | 変わらない |
| 既存の振る舞い | ループの中で `sleep` を繰り返す待ち・プロセス名の待ちが hook に止められる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | PR の CI（`.ndf/project.json` の `test.ci.only`）。hook のエントリポイントを同じプロセスで呼び、拒否の理由の有無で判定する |
| 手動確認 | リリース後テストで、Claude Code の下で再現手順 1 のコマンドを打ち、拒否されることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | guard は `plugins/ndf/src/ndf/domains/hooks/guards/`、guard の一覧は `plugins/ndf/contracts/hooks/guards.json`、閾値は `plugins/ndf/contracts/limits.json`（AGENTS.md・#1142 の設計） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。判定は純粋な関数に置く |
| テスト戦略 | 単体テスト（hook のエントリポイントを呼ぶ表のテスト）。`.md` の文言を照らすテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 受け入れ条件の各形をテストの表に入れる |
| 確認してから行う | 上限の秒の値の変更（設計承認ゲート） |
| 行わない | `read-repeat`・`script-chain` の判定の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 無し | — | — |

## リクエスト（原文）

> ## 事象
>
> `waiting.md`（`plugins/ndf/skills/development-workflow/references/waiting.md` の「禁じる待ち方」）が禁じる 2 つの待ち方を、hook が止めない。
>
> | 禁じる待ち方 | hook の今の扱い |
> | --- | --- |
> | フォアグラウンドの `while` / `until` のループの本体で `sleep` する形 | `sleep` の guard（`plugins/ndf/src/ndf/domains/hooks/guards/sleep.py`）は 1 回の `sleep` の秒を `hook.sleep`（30 秒）と比べるだけ。`sleep 20` をループで繰り返すと通る |
> | プロセス名で待つ（`pgrep -f` / `pkill -f`） | 照らす guard が無い。待つ側のコマンド行に同じ文字列が入るため自分に一致し、終わらない |
>
> #3812 の通し（2026-11-19）で、supervisor と worker が `until ! pgrep -f "ndf refactor run --pr 3845"; do sleep 20; done` などを 12 回打った（前景 9 回・背景 3 回。3 つのセッション d440513f・2fff1c60 の subagents の記録を `pgrep -f` で数えた）。うち 1 回は自分に一致して約 2 時間止まった。
>
> ## 期待する結果
>
> - フォアグラウンドの `while` / `until` のループの中の `sleep` を、1 回の秒によらず止め、Monitor か背景の起動を案内する
> - `pgrep -f` / `pkill -f` を待ちの条件に使う呼び出し（ループの条件・`until` の本体）を止め、「終わりはファイルで待つ」を案内する
>
> ## 再現手順
>
> 1. Claude Code の NDF 11.0.8 の下で、Bash で `until ! pgrep -f "ndf ci watch --pr 1" >/dev/null; do sleep 20; done` を前景で打つ
> 2. hook に止められずに走り、自分のコマンド行に一致して終わらない
>
> ## 環境
>
> NDF 11.0.8（導入先 `~/.claude/plugins/cache/ai-plugins/ndf/11.0.8`）・Claude Code・Linux
>
> ## 影響
>
> supervisor と worker が待ちで長く止まり、conductor が気づくまで工程が進まない。手順書の禁止は読まれないことがあり、hook でしか防げない。
>
> ## 起票者の推定
>
> - フォアグラウンドのループの中の `sleep` は秒によらず止める（推した出所: `waiting.md` の「禁じる待ち方」の 1 項目め）
> - `pgrep -f` / `pkill -f` は待ちの条件に使ったときだけ止め、単発の確認（`pgrep -x dockerd` など）は止めない（推した出所: `docker-container-access` の Skill が `pgrep -x` を単発で使う）
>
> ## 由来
>
> issue #3812 の通しの振り返り（仕上げのフェーズ）。設計・検査のフェーズの supervisor の Bash の記録で見つけた。#3812 は中継のプラグインの更新を足す変更で、hook の待ちの guard は受け入れ条件にも直す対象にも含まない。根拠: Value 3「同じ手順は毎回同じ結果になる」（MVV バージョン 9）。

（書き手: 起票者、日付: 2026-11-19。書き手が利用者でない文は、利用者の指示の原文として扱わない）

