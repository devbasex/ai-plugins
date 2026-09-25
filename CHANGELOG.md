# 変更履歴

書式は [Keep a Changelog 1.1.0](https://keepachangelog.com/ja/1.1.0/) に、版の付け方は
[Semantic Versioning](https://semver.org/lang/ja/) に従う。**複数のプラグインを配布するため、
版の見出しにプラグイン名を含める。** 版が動くのは主に `ndf` と `playwright-kit` で、MCP プラグインは変更があった版だけ載せる。

**ここに書くのは「何が変わったか」である。** 「なぜそう変えたか」と、その版で決めた規約は
`docs/ndf-version-decisions.md` に、詳細な経緯は `issues/` の記録と Pull Request にある。
**開発版（接尾辞の付いた版）は載せない。** `9.8.0` は `9.8.0-dev.1` までしか出ておらず、
その内容は `10.0.0` で届いている。

## [ndf 10.17.11] - 2026-09-25

### 変更

- **`development-workflow` は conductor → supervisor → worker の 3 層で進める**。/goal を付けたときも、
  作業の分け方と承認の関門は同じである
- **中継は区間の切れ目で新しい会話へ切り替える。/goal の目標の判定は待たず、目標が未達のままでも切り替える**。
  切り替えた後の会話は引き継ぎ文書から作業を続ける

### 追加

- **Skill のスクリプトの置き場所を `scripts/resolve.sh` の 1 コマンドで引ける**。`resolve.sh scripts <Skill名>` などで
  絶対パスを出し、見つからなければ終了コード 3 で理由を出す
- **各 Skill の手順をスクリプトで回せる**。`merged` / `pr` / `plan-to-spec` / `release` / `release-verification` は
  `*-steps.py`（`merged-steps.py`・`pr-steps.py`・`plan-to-spec-steps.py`・`release-steps.py`・
  `release-verification-steps.py`）、`fix` は `fix-steps.py` で文脈を集める。`progress-tracking` の手順も
  スクリプトで進める。どのスクリプトも結果を同じ形の JSON（`tool` / `status` / `summary` / `items` / `next`）で返す
- **まとまりを閉じる作業を `bundle-close.py` で行える**。CI を待ってからのマージもスクリプトで行う
- **`supervise.py` に副命令 `new` / `queue` / `note` / `sync-check` を足した**。`new` は雛形から計画を作り、`queue` は
  計画を同時に `--max` 本まで順に流し、`note` は報告から引き継ぎ文書の表へ 1 行を足し、`sync-check` は生成物の同期と
  検査 4 本を走らせる
- **`supervise.py` の計画に `drive` の段を書ける**。外部 CLI の起動と `cross-review` / `cross-refactoring` の収束ループを
  スクリプトが回し、判断や修正が要るとき（`pause`）だけ worker が入る

## [ndf 10.17.10] - 2026-09-24

### 追加

- **区間の切れ目の告知を出す副命令 `relay.py notice` を足した**（#980）。1 行目に `relay` / `outside`、2 行目に告知の
  1 文を出し、終了コードは常に 0。中継の下では「約 N 秒後に自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、
  そのまま待つ」（N は `NDF_RELAY_QUIET` を丸めた秒数）を出す
- **リポジトリが `.ndf/release.json` で宣言した配布の段を走らせる `release-steps.py` を足した**（#893）。`run` は段階
  （`production` / `verification` / `any`）に合う段を書いた順に実行し、書いてよい場所（`writes`）の外を変えた段を失敗に
  する。宣言の形は `release/schemas/release.schema.json`、書き方は `release/references/release-steps.md` にある

### 変更

- **`restart` と `token-guard.sh` の拒否文が、区間の切れ目の告知に `relay.py notice` の 2 行目を使うようにした**（#980）
- **区間の切れ目の再起動は関門ではないと定め、切り替えの前に承認を求めないようにした**（#980）。
  `development-workflow` の関門の節と `references/context-window.md` に書いた
- **`release` の手順 3 で、版と説明文書を上げた後に宣言された配布の段を走らせるようにした**（#893）。完了の判定に
  「段が 0 で終わり、手引きの判断を済ませた」を足した

## [ndf 10.17.9] - 2026-09-24

### 変更

- **エージェント定義が指す Serena のツール名を、mcp-serena プラグインが出す名前へ揃えた**（#818）。`corder` / `qa` は
  `mcp__plugin_mcp-serena_serena__*`（Codex では `mcp__serena__*`）を指し、`director` は memory のツールを使わない旨を書いた
- **`refactoring` / `tdd-cycle` / `problem-solving` と `debugger` エージェントが、Serena が使えるときはシンボル単位で
  読み書きする手順を示すようにした**（#818）。`find_symbol` / `find_referencing_symbols` / `replace_symbol_body` を使い、
  ファイルを丸ごと読まない。使えなければ Grep と Read を使う

## [mcp-serena 2.1.0] - 2026-09-24

### 追加

- **言語サーバを設定する Skill `/mcp-serena:language-servers` を足した**（#818）。追跡しているファイルの拡張子を数えて
  言語を採り、`.serena/project.yml` を書き、1 言語ずつ起動を検証する。Claude Code の公式 LSP プラグインと言語サーバの
  本体の欠けを、導入のコマンドとともに示す
- **SessionStart の hook が、設定の食い違いと導入の欠けだけを知らせるようにした**（#818）
- **PreToolUse の hook を足した**（#818）。設定した言語のファイルの grep・読み込みが続くと 1 度だけ止め、シンボル単位の
  手順を示す。Claude Code の許可のモードが `acceptEdits` / `auto` のとき、Serena のツールを自動で許可する
- **Codex 向けの起動の定義（`.codex.mcp.json`、`--context codex`）と hook（`hooks/codex.json`）を足した**（#818）

### 変更

- **Serena を `serena-agent==1.7.0` に固定し、`--project-from-cwd` と `no-memories` / `no-onboarding` で起動するようにした**
  （#818）。Claude Code / Kiro CLI の文脈は `ide-assistant` から `claude-code` へ変えた

## [ndf 10.17.8] - 2026-09-24

### 変更

- **`/ndf:cross-refactoring` を、想定最大時間（`--budget-minutes`、既定 30 分）に収まる計画を 1 回だけ実行する
  形へ改めた**（#933）。参加者の全員が 1 度だけ提案し、実装担当 1 者（`--implementer` → ホスト → 参加者の
  先頭）が計画・テスト追加・実装・検証/修正を通す。提案と適用のラウンド制と、適用担当の輪番を廃した
- **計画は配分テーブルで見積もり、「想定最大時間 − 経過 − 控え」に収まる件数だけを採る**（#933）。
  配分テーブルは履歴の直近 10 回から集計し、初期値は #917 の実測を同梱する。見送った提案は理由
  （`budget` / `rank` / `duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done`）とともに
  改修計画に残る
- **時間に関わる数値をすべて `--budget-minutes` と着手前の全体のテストの実測から算術で出す**（#933）。
  段の監視の上限・テスト 1 回の上限・無音の打ち切り・直しの打ち切りを `state["limits"]` と改修計画の
  「時間の上限」の表へ書き出す。監視はその段の終わり + 余裕で CLI を止め、修正は回数でなく締め切りまで試みる
- **最終ゲートの修正 1 回分を控えとして計画の時点で予算から差し引き、予算を使い切った後に最終ゲートが
  落ちても必ず 1 度は直しを試みる**（#933）。2 回目からは想定最大時間の終わりで打ち切る
- **項目の検証は、`--round-test` か `--baseline-test` の対象を計画の `test_targets` へ差し替えた限ったテストで
  走らせる**（#933）。全体のテストは着手前・危険の印（D1〜D5）が立ったときの 1 回・最終ゲートだけで、
  印の 1 回が落ちたら落ちたテストだけを走らせ直して揺れと元からの失敗を除く
- **計画の後で LLM が動くのは作業の CLI だけにした**（#933）。D5（公開の入出力が変わりうるか）の Jev への
  問いを計画の段へ前倒しし、テストの差分の判定の段 2 を規則（最終ゲートのレビューへ引き継ぐ）に替えた
- **状態ファイルを新しい形（`schema: 2`）にした**（#933）。ラウンド制の途中の状態ファイルでは `init` が
  終了コード 4 で止まる
- **中継の静まりの既定を 15 秒から 5 秒へ短くした**（PR #964。`NDF_RELAY_QUIET` で変えられる）

### 非推奨

- `/ndf:cross-refactoring` の `--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` /
  `--max-fix-rounds` / `--test-timeout`（#933）。受け取ると廃止を知らせて無視する。次の版で外す

## [ndf 10.17.7] - 2026-09-24

### 追加

- **`/ndf:install-wrapper` を足した**（#928。Claude Code だけ・明示指示でだけ動く）。`install`（既定）/
  `uninstall` / `status` の 3 つを引数で分ける。`install` は中継の写しを `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py`、
  `claude` の関数を同じ親の `shellrc` に置き、シェルの設定へは `shellrc` を読む 1 行だけを囲みで足す
  （`DEVBASE_SHELLRC_DIR` があれば `$DEVBASE_SHELLRC_DIR/ndf-relay.sh` に置く）。10.17.4〜10.17.6 が
  足した囲み（alias を直に持つ）は、中だけを読み込みの行へ置き換える。`uninstall` は `~/.bashrc` と
  `~/.zshrc` の両方の囲みを外し、`shellrc`・写し・旧い写しを消す。書く前に `<ファイル>.ndf-bak-<UTC>` へ
  バックアップを取る
- **`/ndf:restart` を足した**（#928。Claude Code だけ）。中継の下では再開用のコマンドを `ndf-next` の
  ブロックで出し、中継が静まりの後に `/exit` → プラグインの更新 → 起動し直しを行う。中継の外では、
  貼り付ける中身を示す。再開用のコマンドに承認・同意・判断の結果を書かない
- **中継が質問（`AskUserQuestion`）の答えを代わりに送らない守りを足した**（#928）。`AskUserQuestion` の
  PreToolUse / PostToolUse hook が質問の表示中の印を作る・消し、中継は印がある間と、印の後に応答が
  再開した間は子の端末へ何も書かない。印の確かめ直しと `/exit` の書き込みを質問の始まりと排他にし、
  排他のロックが取れない質問は拒否してモデルに呼び直させる
- **`relay.py` に `uninstall` / `status` / `startup` / `question open|close` の副命令を足した**（#928）
- **中継の 2 つ目以降の区間へ、最初の区間の起動の方針の引数を引き継ぐ**（#936）。
  `--dangerously-skip-permissions`・`--model` などを引き継ぎ、前の会話を指す・会話に名前を付ける・
  起動の形を変える選択肢（`--resume`・`--continue`・`--session-id`・`--name`・`--worktree` など）と
  位置引数は落とす。引き継いだ引数は `start` の行の `carried` に残る

### 変更

- **SessionStart hook がシェルの設定を書かなくなった**（#928）。`relay.py install` を外し、`relay.py startup`
  に替えた。`startup` は在る写しを今の版で置き直し（新しい版の写しを古い版で置き直さない。無い写しは
  作らない）、10.17.4〜10.17.6 が自動で足した囲みが残っていれば 1 度だけ知らせる
- **中継が `/exit` と改行を 1 回の書き込みで送るようにした**（#928）
- **中継の知らせ・`/ndf:install-wrapper status`・`relay.md`・`install-wrapper` が自動導入の版を「10.17.4〜10.17.6」と書くようにした**（#953）。
  `/ndf:install-wrapper` を持たない版へ戻す手順の見出しも「10.17.6 以前へ戻す」にした
- **公開 Skill は Claude Code 向けが 47 個になった**（`install-wrapper` と `restart`。Codex / Kiro CLI / agy へは配らない）

### 削除

- **環境変数 `NDF_RELAY_AUTO` と `NDF_RELAY_EXIT_GAP` を読まなくなった**（#928）。置いたままでも害は無い

## [ndf 10.17.6] - 2026-09-24

### 追加

- **cross-review の `start-round` が 2 ラウンド目以降で既存コメントの控えを取り直す**（#542）。同じ実行の
  前のラウンドの指摘と返信が担当へ渡る。取得元の 1 つでも失敗すれば前の控えのまま `⚠` の 1 行を出して
  続ける。取り直しは round エントリの保存の前に行い、PR の巻き直しの後は新しい PR から取る
- **`fix` の `fetch-pr-comments.sh` に `--strict` を足した**（#542）。3 つの取得元のどれか 1 つでも
  失敗すれば終了コード 1 にする。付けなければ終了コードも出力の形も前と同じ
- **cross-review が設計 Pull Request を `design` に分類し、設計向けの観点テンプレートを渡す**（#542）。
  `issues/` の `-requirements.md` / `-design.md` / `-design-decisions.md` を含む変更が対象で、
  `common` / `docs_only` の分類は消さない
- **レビューのプロンプトに `## 出し切り` の節と、テストを実行しない・背景で処理を起動しない指示を
  足した**（#542 #786）。cross-refactoring の適用のプロンプトには、テストを前景で実行して終わるまで
  待つ指示を足した

### 変更

- **cross-review の既定の母集合を claude / codex / kiro とホストにした**（#786）。agy は `--include agy`
  で戻す。ホストが agy なら 4 者。`--only` で名指しした者は母集合に無くても参加者にする
- **参加の母集合に無い者の `--exclude` を止めずに無視する**（#786。共通層のため cross-refactoring も
  同じ）。無視した名前は `participants.ignored_exclude` に残し、`ℹ` の 1 行と完了報告の 1 行で知らせる。
  `--exclude` を渡さない再開は無視した除外も足し戻す（`--include` か `--only` に渡した名前は除く）

## [ndf 10.17.5] - 2026-09-23

### 追加

- **cross-refactoring の `init` に `--round-test CMD`（範囲のテスト）を足した**（#880）。群の検証と修正
  ラウンドの修正のコミットは範囲のテストで検証し、全体のテスト（`--baseline-test`）は `init` と最終
  ゲートの 2 回だけ走らせる。範囲のテストが `--scope` のテストの置き場所を走らせなければ `init` が終了
  コード 4 で止まる。省くと前と同じく全体のテストで検証し、全体のテストが範囲より広ければ 1 行の案内を
  出す。検証の記録と最終ゲートの記録に `seconds` を足した
- **構造改善を飛ばしてよいかを差分から判定する `refactor.py assess --base <ref>` を足した**（#494）。
  `<ref>...HEAD` の本番コードの変更が無いか `--max-lines`（既定 10）行以下なら終了コード 3（飛ばして
  よい）、それ以外は 0（通す）、ref を解けない・引数の誤りは 2（判定できない）

### 変更

- **単独で起動した cross-refactoring の最終ゲートは、2 つのテストが違えば全体のテストを 1 回走らせる**
  （#880。`--ci-check` があれば継続的統合で代える）。落ちれば修正ラウンドへ入る
- **テストの打ち切りが子の終了で戻る**（#883）。点検のたびに親シェルを回収し、グループに生きた
  プロセスが無くなった時点で戻る。残る子には猶予の後に SIGKILL を送る
- **cross-refactoring が `.md` の文言を固定するテストを採らない**（#723）。テスト整備の提案のうち
  `target` が `.md` を指すものを見送りへ入れ、適用ラウンドで追跡している `.md` を指すテストを足した
  群を失敗として取り消す（`prompts/propose-tests.md` の「守ること」に 1 項目を足した）
- **`development-workflow` の構造改善の手順を assess → `--round-test` → `--baseline-test` の順にした**
  （#494。`references/stage-notes.md`）。`references/workflow-modes.md` の「構造改善の退避先」で、
  飛ばす条件と退避する条件を分け、飛ばしたときは工程「構造改善」を記録して Pull Request の本文に
  1 行残すとした

## [ndf 10.17.4] - 2026-09-23

### 追加

- **区間の切れ目の再起動と次のコマンドの入力を、前景の中継で自動にした**（#895。Claude Code だけ）。
  `scripts/relay.py` を足した。副命令は `run`（中継を始める）/ `stop`（停止の印を置く）/ `mark`
  （Stop hook から最後の応答の `ndf-next` のブロックを印へ写す）/ `install`（SessionStart hook から
  中継を `${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py` へ置き直し、bash / zsh の設定へ
  `alias claude=...` を印のついた囲みで 1 度だけ足す）。中継は印を受けると `/exit` を入力し、
  プラグインを更新し、同じ端末でブロックの中身を渡して claude を起動し直す。非対話の起動・副命令・
  パイプ・中継の下の `claude -p`・`NDF_RELAY=0` は中継を挟まずに素通しする。上限は 1 日の起動回数
  （`NDF_RELAY_MAX_STARTS`、既定 20）・空回り（3 区間続けて起動から 120 秒未満）・静まり
  （`NDF_RELAY_QUIET`、既定 15 秒）。`NDF_RELAY_AUTO=0` で `install` が何もしなくなる
  （`development-workflow/references/relay.md`）

### 変更

- **中継の直接の子の conductor では、文脈量の hook が上限を超えた起動を 1 度の通しなしに止め続ける**（#895）。
  新しい会話で打つコマンドは `ndf-next` の囲みのブロック 1 つで出す
  （`development-workflow/references/context-window.md` の「新しい会話で戻す」）
- **`.md` の文言を照合するテストを書かない規約にした**（#885）。`tdd-cycle/references/test-quality.md` に
  例 8「`.md` の文言を照合する」と「削除してよいテスト」の 1 行を、`quality-gates` の「受け入れ条件との
  対応」に文書の文言照合を検証手段にしない段落を足した。見たいものごとの代わり（参照切れは検査
  スクリプト、正しさはレビュー、埋め込んだ bash は取り出して実行するテスト、スクリプトが実行時に読む
  `.md` はそのスクリプトを通すテスト）を表にした

### 削除

- **配布物のテストから `.md` の文言を照合するテストを削った**（#885）。ファイルごと 17 本と補助モジュール
  `retrospective/tests/retrospective_helpers.py`、17 ファイルの文言照合の関数を外した。文書に埋め込んだ
  bash を実行するもの・スクリプトが実行時に読む `.md` を通すもの・配線と構文の検査は残した。全体の
  テストは 5365 件から 4641 件になった

## [ndf 10.17.3] - 2026-09-23

### 変更

- **cross-review の母集合をホストを含む全ランタイムの 4 者にした**（#892）。`lib/assignment.py` の
  `review_pool(host)` は 4 者を返し、使える者から毎ラウンド 2 席を選ぶ。ホストを別に確かめて席の
  埋め合わせに使うことはやめ、新しく作る状態の `participants.fallback` は空になる。使える者が 1 者なら
  その者と同じランタイムの 2 つ目（`<名前>-2`）で埋め、0 者なら `init` が終了コード 1 で終わる。
  ホストだけを持つ古い状態の再開は、変更の前の母集合（全ランタイム − ホスト）で席を決める
- **supervisor が worker の途中の通知で止まらないようにした**（#901）。起動指示の `置き場所` を必須にし、
  worker は最後の応答の前に報告の写しを `置き場所` の末尾へ書いて完了の目印 `<置き場所>.done` を作る。
  supervisor は途中の通知を受けたら、目印の出現を待つ until ループ（上限 3600 秒）を
  `run_in_background: true` で起動してから応答を終える（`development-workflow/references/waiting.md` の
  「途中の通知を受けたとき」、`agent-layers.md` の supervisor の規則 4 と worker の規則 5）

## [ndf 10.17.2] - 2026-09-23

### 追加

- **3 層の worker のエージェント定義 `ndf:worker`（`plugins/ndf/agents/worker.md`）を足した（Claude Code。agy へも
  同じ定義が配られる）**（#828）。frontmatter の `disallowedTools: Skill, Agent` で Skill と Agent のツールを外す。
  `plugin.json` の `agents` に載せ、説明の数は「専門エージェント 8 個と worker の定義 1 個」と別枠で書く
- **Skill の抜粋の規約 `plugins/ndf/skills/EXCERPTS.md` と、`progress-tracking` の抜粋
  `references/excerpt.md` を足した**（#828）。抜粋は「呼び出し・結果の読み方・判断の基準」の 3 見出しで、
  40 行かつ 2,000 文字以内に収める
- **仕事を分ける器の比較表 `development-workflow/references/work-vessels.md` を足した**（#680）。その場 /
  サブエージェント / CLI 実行 / 最小構成の `claude -p` / スクリプトの 5 つを比べ、小さな作業には
  サブエージェントを起こさない線引きを置く

### 変更

- **`projects-sync.sh` の 1 行で issue の本文と盤面の両方へ進行を残すようにした**（#828）。`stage` / `mode` /
  `worktree` / `plan` は、盤面の宣言の有無にかかわらず先に `progress-record.sh` で issue の本文を更新する。
  `progress-record.sh` は工程名の位置に `-` を受けると見出し行だけを更新する
- **工程の Skill 20 個の末尾の記録の文を、記録のコマンド 1 行の形にした**（#828）
- **起動指示の雛形（`agent-layers.md`）から Skill 本文の読み込みを外した**（#828 #680）。supervisor は
  `development-workflow` を起動せず、conductor が `$SCRIPTS` を解いた絶対パスの記録のコマンドを使う。
  worker は `ndf:worker` で起動し、要る手順を起動指示に写した抜粋で受け取る

## [ndf 10.17.1] - 2026-09-23

### 変更

- **全体テストを並列で回し、継続的統合ではファイル単位で 2 つのジョブへ分けた**（#882）。案内するコマンドを
  `-n auto` の形にし、根の `conftest.py` が `SHARD_TOTAL` / `SHARD_INDEX` を読んで自分の分のファイルだけを
  残す。まとめジョブの名前を `pytest` にしたため、必須の検査は変わらない。Skill・スクリプト・hook の振る舞いは変わらない
- **重複した競合試験を共通実装への 1 通りへ寄せ、繰り返しの回数を減らした**（#884）。
  `release/**` の push を継続的統合の契機から外し、2 重の実行をやめた

## [ndf 10.17.0] - 2026-09-23

### 追加

- **PreToolUse hook `scripts/token-guard.sh` を足した（Claude Code だけ）**（#829 #830）。前景の Bash で
  `while` / `until` のループの本体にある `sleep` と 5 秒を超える `sleep`、変わらないファイルの同じ範囲を
  3 回続けて読む Read、文脈が 200,000 を超えた conductor が工程 Skill か持ち場の supervisor を起動することを、
  理由の欄に代わりの手段を書いて止める。文脈量の拒否は起動ごとに 1 度で、同じ起動をもう一度行えば通る。
  環境変数 `NDF_SLEEP_GUARD` / `NDF_SLEEP_MAX_SEC` / `NDF_READ_REPEAT_GUARD` / `NDF_READ_REPEAT_LIMIT` /
  `NDF_CONTEXT_GUARD` / `NDF_CONTEXT_LIMIT` で止める・上限を変える
- **待ち方の規約 `development-workflow/references/waiting.md` を足した**（#829）。`agent-layers.md` の
  supervisor / worker の規則と、前景の待ちのループを持つ 4 文書（`cli-codex.md` / `cli-agy.md` /
  `qa-security-scan/03-report-template.md` / `release/references/completion-check.md`）から指す

### 変更

- **`context-window.md` の「前提: 実測ではない」を #827 の実測値に置き換え、「上限を超えたら hook が止める」
  「新しい会話で戻す」の節を足した**（#830）。`development-workflow/SKILL.md` に、4 つの切れ目で conductor が
  引き継ぎの 1 行（`/ndf:development-workflow #<課題>`）を出す規約を足した

## [ndf 10.16.1] - 2026-09-22

### 修正

- **codex と claude の利用上限の実物の文言を、監視の照合の表へ足した**（#811）。
  `^(?:ERROR:\s*)?You['’]ve hit your (?:[\w'’ ]+ )?(?:limit|budget)\b` と
  `^(?:ERROR:\s*)?exceeded retry limit, last status: 429\b` の 2 行で、codex の 2 形と claude の 5 形に
  一致する。結末の理由が「結果ファイル無し」ではなく「利用上限」になり、同じラウンドで起動し直さない。
  **行頭で始まる形だけを読む**ため、担当が差分・文書・テストを読み上げた行では止まらない。再試行の上限は
  最後の状態が 429 のときだけ利用上限と読む（503 などの一時的な誤りは起動し直せば解けうる）
- **認証の確認が、確認コマンドを起動できない理由を問わず「通らない」として返すようにした**（#813）。
  `PATH` に読めないディレクトリがあると、コマンドがどこにも無いときに見つからない例外ではなく権限の例外が
  上がり、`cross-review` と `cross-refactoring` の開始の手順ごと落ちていた。実行形式でないファイルも
  同じ形で落ちる。どちらも理由 `コマンドを実行できません（<理由>）` として返り、使える者だけで始まる

### 変更

- 収束ループの共通層の内部構造を整理した（`lib/result_posts.py` の投稿の組み立て、`lib/run_metrics.py` の
  集計、`lib/transcript_agents.py` の記録の読み取り）。振る舞いは変えていない

## [ndf 10.16.0] - 2026-09-22

### 追加

- **`cross-review` と `cross-refactoring` の `init` に `--exclude` / `--include` / `--require-all` を足した**（#664 #727）。
  参加者を名指しで足し引きでき（カンマ区切り・繰り返し可・再開で `none` を渡すと空へ戻す）、`--require-all` を
  付けたときだけ確認を通らない者が 1 者でもいれば止まる
- **`scripts/lib/result_posts.py` を新設した**（#730 #583）。結果ファイルからレビュー・返信・スレッドの決着・
  まとめを組み立てて待ち行列から送る共通層で、部分命令 `fix` が `/ndf:fix` を単独で使うときの送信と投稿を行う
- **`cross-refactoring/scripts/refactor_lib/intake.py` を新設した**（#728）。適用・修正・最終ゲートの修正の
  3 つの取り込みが、範囲の確定・未検証のコミットの取り消し・結末の記録を共有する
- `cross-review/scripts/classifications.py` を新設し、指摘の区分の語彙を 1 か所に置いた（#732）
- 状態ファイルに参加者の記録（`participants`）と再開で変えた値の記録（`resume_changes`）を足し、完了報告に
  参加した者の節を足した（#727 #648 #664）
- **statusline に実行中のサブエージェントのコンテキスト使用量を並べる**（#806）。使用量の多い順に 3 本まで、
  残りは本数だけを出す。500k（Haiku 4.5 は 150k）を超えたら赤で表示し、NDF 標準の statusLine に
  `refreshInterval: 5` を持たせる（既存の設定には `ensure` / `set` が足し、利用者の値は変えない）

### 変更

- **認証の確認を関門から外した**（#478 #727 #664）。確認を通らない CLI があっても `init` はその者を外して
  続け、外した者と理由を状態ファイルと完了報告へ残す。使える者が 0 者なら止まる
- **`cross-review` が毎ラウンド 2 席を確保する**（#687 #727）。使える者が足りなければホスト、次に同じ
  ランタイムの 2 つ目（`codex-2` など）で埋める。`--only` のときは埋め合わせをしない
- **`cross-refactoring` の既定の参加者を codex / kiro とホストにした**（#664）。agy は `--include agy` で戻す。
  提案と適用を同じ参加者で回し、レビュー担当の役を無くした
- **再開で渡した引数を反映するか、反映しないと知らせる**（#648）。上限の引数の既定を未指定にし、既定値は
  状態の側で補う
- **担当 1 回の起動の結末を共通の語彙で読む**（#729 #619 #584）。利用上限は理由「利用上限」として残り、
  同じラウンドで同じ担当を起動し直さない。監視は CLI を独立したプロセスグループで起動し、止めた後に子
  プロセスが結果を書かない
- **収束の判定で数えない指摘を、棄却と `minor` 以下に限った**（#732 #624 #706）。誤りを示されていない
  `major` 以上は `unrefuted` として数える
- **GitHub と git へ書くのをレビューを回す側だけにした**（#730 #583）。レビューの担当は指摘の控えと
  結果ファイルを書いて終わり、`/ndf:fix` の担当はコミットまでで止まる。投稿は `state.py read-result`、
  送信・返信・決着・まとめは `state.py merge-fix` が行い、4 種別すべてで二度書かない照合を掛ける
- **同じ適用ラウンドを開き直すのを 2 回までにした**（#728 #647 #592）。2 回目は別の担当が試し、2 回とも
  結果が残らなければ取り消して見送る。採用 0 件の提案ラウンドと項目の無い適用ラウンドでは担当を起動しない
- `launch-reviewer.sh` / `critique.sh` の第 1 引数をランタイム名から席の名前にした（ランタイム名は
  そのまま受け付ける）（#727）
- statusline のメインの表示から上限・使用率・コンテナ名・ホスト名を外し、モデル名を詰めた
  （`Opus 5 (1M context)` → `Opus5`）（#806）

### 修正

- 帰属の段落がコミットの後ろに足されても、必須の記名を末尾の段落から読むようにした（#553）
- テストの実行中は `MONITOR_` で始まる環境変数を外し、監視の上限を延ばしたシェルでも同じ件数が通るように
  した（#678）

### 削除

- 共通層の `check_auth` / `impl_pool` / `review_assign` / `assign` を消した（#664）。置き換え先は
  `probe_auth` / 参加者の一覧 / `review_seats` / `impl_assign`
- `cross-review` の担当の申告件数と GitHub の実数の突き合わせをやめた（#730）

## [ndf 10.15.1] - 2026-09-19

### 修正

- **`development-workflow/references/agent-layers.md` の「並行の本数」の節で、実行計画の持ち主の
  記載を直した**（#762）。「本数の測り方と実行計画は `parallel-work.md` が持つ」と書いていた 1 文を、
  「本数を抑える下限は `parallel-work.md`、本数の測り方と実行計画は `issue-plan-strategy` の
  `references/execution-plan.md` が持つ」へ改め、`parallel-work.md` の境界の表と揃えた

## [ndf 10.15.0] - 2026-09-18

### 追加

- **エージェント向け指示書を適切に保つ検査 `scripts/instructions-check.py` を配る**（#554）。
  `AGENTS.md` / `CLAUDE.md` / `KIRO.md` の参照先の無い即時読み込み・許可していない即時読み込み・
  出た版の段落・読み込みの量の上限の 4 つで落とし、読み込みの量と指示の数を報告する。閾値は
  配布物に持たず、リポジトリの `.ndf/instructions.json` が判定の強さを決める。観点と出典は
  `scripts/data/instruction-criteria.json` が持ち、`--refresh` で出典を調べ直す。宣言の書き方と
  落ちたときの直し方は `release/references/instruction-files.md`、宣言の定義は
  `release/schemas/instructions.schema.json` にある
- **`development-workflow/references/agent-layers.md` を新設した**（#550 #657）。`/goal` を
  conductor / supervisor / worker の 3 層で運転するときの責務・持ち場の表・起動の指示・報告の形・
  報告なしで続けさせる回数・モデルの基準・委譲しない 5 つの判断・並行の本数の単位・到達点の
  置き直し・中断と再開を持つ。`cross-review` の「メイン」を収束ループを駆動している supervisor と
  定義した（`references/context-budget.md`）
- **`scripts/lib/transcript_agents.py` を新設した**（#550 #657）。会話の記録を 3 層で読み、
  `list` が層・持ち場・固定費・最大充填・実作業・終わり方を出す。`interrupted` が利用上限（429）で
  終わった記録だけを解除時刻つきで出し、`wait-reset` が最も早い解除時刻まで眠る
- **`skill-stats --agents` を足した**（#550）。`--session` / `--layer` / `--window-limit` で絞り、
  記録ごと・束ね・層ごとの合計・持ち場ごとの worker の使い方の 4 つの表を出す。`retrospective` の
  手順 2 の観点と記録の雛形に context window を足した
- **`scripts/parallel-measure.py` を新設した**（#621）。`capacity` が空きメモリ・cgroup の残り・
  スワップの空き・`oom_kill` から起動してよい本数を出し（測るだけで起動は止めない）、
  `concurrency` が Pull Request の一覧から並行度・最大同時本数・期間を出す。本数の初期値は
  このスクリプトの定数だけが持つ
- **`issue-plan-strategy/references/execution-plan.md` を新設した**（#540）。進行側が最初の担当を
  起動する前に書く実行計画の置き場所（`issues/execution-plan-<キー>.md`、開いている間は
  コミットしない）・行と測った値の形・組の列との対応・見直す 5 つの契機・閉じ方を持つ
- `issue-upkeep/references/milestones.md` に、課題をマイルストーンへ入れる時点で組（触る場所の
  見込みと依存）を説明の末尾へ書く手順を足した（#541）。触る場所は段 2A の修正レイヤーから写し、
  既存の説明は一括で書き直さない
- `progress-tracking` に「工程の単位と記録する課題」と「まとまりを閉じる」を足した（#623）。
  課題を閉じる唯一の手順と、配布の記録の読み方を持つ
- `release` の配布の Pull Request の本文に `## 配布の記録` のブロックを置く（#623）。飛ばした
  ときも `段階: 配布なし（<理由>）` で残す
- `development-workflow/references/parallel-work.md` に「重なりの目安」の節（別の節 / 足すだけ /
  書き換え）を足し、下限 6 の理由にホストのメモリと実測を書いた（#540 #621）
- `development-workflow/SKILL.md` の関門の節に「関門の外で工程の側が実行前確認を足さない」
  原則を足した（#561）

### 変更

- **`merged` が削除の前に同意を求めなくなった**（#561）。「削除前の同意取得（必須）」を
  「止まる条件」に置き換え、止まるのは git が削除を拒んだ対象だけにした。起点・本番のチャネル・
  現在のブランチは同意を求めずに対象外にする。リモートブランチは先端が Pull Request の
  `headRefOid` と一致するものだけを消し、無視されたファイルは消さずに退避する（退避が失敗した
  ものは「未完了」として消さない）。**課題を閉じない**
- **課題を閉じる時点を、起点へのマージからその実行の終わりの工程の後へ移した**（#623）。
  `release` / `release-verification` / `retrospective` のうち最後に通る工程が「まとまりを閉じる」を
  行い、その後に `issue-upkeep` を呼ぶ。`retrospective` の入口の進行の記録から `Done` を外し、
  `release-verification` の記録の置き場所を配布の記録の Pull Request にした。`pr` は
  コミットメッセージに閉じる語を書かない
- **`AUTHORING.md` の実行前確認の要否を、3 つの問い（戻せないものを拒まれずに消すか・新しい
  内容を外へ出すか・判断を問うか）で決める基準にした**（#561）。守り方に「自動発動 + 事後の
  報告」を足し、適用表を 7 行にした
- `development-workflow/SKILL.md` の `/goal` の節を入口だけにし、「他者の承認が要るときは、
  到達点を置き直す」を `agent-layers.md` へ移した（#550）。`references/context-window.md` に
  モデルに依る目安とリポジトリに依る固定費の区別、粒度の比の基準を足した。3 つの規約の文書から
  「窓」と「親」の語を無くし、`conductor` / `supervisor` / `worker` と `context window` に揃えた
- `parallel-work.md` の下限 4 を「依存する工程が終わる前に、それを入力にする工程を始めない」へ、
  下限 5 を「競合を解いた差分を、レビューを通さずにマージしない」へ書き換えた（#540）
- `issue-plan-strategy/SKILL.md` の Step 1 の「PR 分割計画」の列を工程の対の依存へ、Step 5 の
  「レビュー観点で分担」を重なりの目安へ置き換え、`description` に `実行計画を作って` などの
  トリガ語を足した（#540）
- `release/SKILL.md` の開始条件に、どこまでがまとまりかを確かめるのはこの工程であることを足した
  （#623）。退避の直後に指示書の検査を実行する（#554）
- `development-workflow/scripts/lib/workflow-common.sh` / `workflow-merge.sh`、
  `scripts/lib/run_metrics.py` に構造改善を入れた（振る舞いは変えていない）。
  `scripts/lib/README.md` の表に `refresh.py` / `transcript_agents.py` を足した

## [ndf 10.14.0] - 2026-09-16

### 追加

- **`issue-upkeep` に 8 つ目の判定「ルートコーズ」を足した**（#712 #713）。課題が現れている
  場所（現象レイヤー）と原因を直すべき場所（修正レイヤー）を段 2A で分けて控え、2 つが違う
  課題に付ける。**条件はこの 1 つで、件数は条件にしない。** 分岐は「要判断」へ倒さない
- **`issue-upkeep/references/grouping.md` を新設した**（#712 #713）。ルートコーズの条件、
  段 3 での対応（本文へ書く / 親 issue をつくる / 重複として正本へ寄せる）、修正レイヤーの
  決め方、採る手 5 つ、親 issue と子 issue の結び付け（GitHub のサブイシュー関係を既定にする）を持つ
- 段 1 に 4 つ目の経路を足した（#712 #713）。このまとまりで閉じた親 issue の子 issue を拾う
- 段 2B が同じ修正レイヤーを指すクラスタを突き合わせる（#712 #713）。重複の突き合わせを先に行う
- 親 issue の起票と子 issue の結び付けを、一括で提示して承認を得る変更へ足した（#712 #713）。
  「手入れ」は起票を含まず、例外は親 issue の起票だけである
- `milestones.md` に親 issue の割り当て方を足した（#712）。重要度はクラスタの最高値を採る
- 判断（価値 / 構造）と見る対象（発見の瞬間 / 溜まった課題）の表を `issue-upkeep` に置き、
  `out-of-scope` / `problem-solving` / `retrospective` がその表を指すようにした（#713）
- `issue-upkeep` の現状固定テストを足した（`tests/test_related_skills_characterization.py` を新設）

### 変更

- **`no-work.md` の条件 2 の行き先を 3 つにした**（#712）。寄せ先が同じ課題なら重複、同じ
  修正レイヤーを指すものが 2 件以上ならルートコーズ、どちらでもなければ条件は欠けていない。
  「要判断」へ倒す文は見積りである条件 1 だけに掛かる
- **`milestones.md` の直近を、open のうち名前の先頭の連番が最も小さいものにした**（#712）。
  説明が別の着手の順序を書いていれば説明を採る。新しく作るときの名前は `<2 桁の連番> <主題>` で、
  連番は再利用しない。着手の時期を指す「まとまり」は「マイルストーン」へ揃えた
- `retrospective` の Pull Request 番号の引き方を 3 段（開発の起点 / 記録対象の基準ブランチ /
  基準コミットから引く）に分けた。手順の結果は変わらない
- `problem-solving` の型不一致の組み合わせを「型不一致の検出パターン」の表へ寄せ、
  `out-of-scope` の由来の形を「起票した課題の辿り方」の 1 か所へ寄せた。手順の結果は変わらない

## [ndf 10.13.0] - 2026-09-16

### 追加

- **上限の表 `plugins/ndf/scripts/lib/limits.py` を新設した**（#598 #537）。監視の上限・CLI の
  上限・無進捗の許容の既定値をこの 1 つの表だけが持つ。監視の上限は工程ごと（review /
  critique / propose / judge-test-changes は 1200 秒、apply / fix / final-fix は 3600 秒）、
  無進捗の許容は担当ごと（codex 180 / agy 480 / kiro 480 / claude 900）、CLI の上限は
  「環境変数で解決した監視の上限 + 120 秒」。**全 28 組で「許容 < 監視 < CLI」をテストで固定する**
- `monitor.py --phase <工程>` を足した（#598 #537）。上限は `--timeout` →
  `MONITOR_TIMEOUT_<担当>` → `MONITOR_TIMEOUT` → 表の順で解決し、省略時は review の値になる。
  解決後に許容 ≥ 上限になった担当を標準エラーへ 1 行で警告する（**終了コードと標準出力は
  変えない**）。監視の結果ファイルと記録へ `phase` が増える
- `launch-cli.sh` の第 7 引数に工程名を足した（#598）。agy の `--print-timeout` を表から導く
- **`cross-review/scripts/bg-wait.sh` を新設した**（#537）。`run` が背景で起動して終了コードを
  rc ファイルへ残し、`wait` が 540 秒以内で区切って待つ（124 = まだ終わっていない）。
  Claude Code の Bash の 1 回の上限（600 秒）を超える監視を待てる
- **監視の結果と実行の要約を作業ツリーの外へ残すようにした**（#662）。監視が担当ごとに
  `<stem>-monitor.json` と追記だけの `monitor-outcomes.jsonl` を残し、理由の語彙と読み書きは
  新設の共通層 `lib/monitor_outcome.py` が持つ。実行の要約は `lib/run_metrics.py` が
  `NDF_METRICS_DIR` → `$XDG_STATE_HOME/ndf/metrics` → `~/.local/state/ndf/metrics` の順で
  決めた先へ書き直す。**レビュー用の作業ツリーを消した後も集計できる**
- `run_metrics.py aggregate` を足した（#662）。要約を種類・ラウンド数・理由で束ねて表で出す。
  `state.py report` の最後の行が要約のパスを出す

### 変更

- cross-refactoring の監視 8 か所を `--phase` にした（#598）。`--timeout` を外し、工程の所要の
  判定は記録の `phase` を優先する

### 修正

- **cross-refactoring の修正と最終ゲートの修正が 420 秒で打ち切られていた**（#598）。工程ごとの
  上限（3600 秒）を使う。あわせて `cross-review` の agy がハードタイムアウト 420 秒で常に
  打ち切られ、無進捗の許容の既定 480 秒へ到達できなかった状態を解いた
- **agy へ渡す `--print-timeout` が 600 秒固定で、大きな差分のレビューが終わらなかった**（#537）。
  利用者が上書きした監視の上限からも + 120 秒で導くため、CLI が監視より先に打ち切らない

### 削除

- `docs/id_rsa.pub` を削除した（#688）。GitHub Pages の公開元が `main` の `docs/` であるため
  取得できる状態だった。公開鍵で秘密情報ではないが、知識を置く `docs/` にある理由が読み取れない

## [ndf 10.12.0] - 2026-09-15

### 追加

- **宣言項目 `follow_branch` を足した**（#610）。セッション開始時の hook が主ディレクトリの
  ブランチを稼働中の作業ツリーへ追従させるのは、`.ndf/worktree.json` に
  `"follow_branch": true` を書いたときだけになった。**既定では主ディレクトリの HEAD を
  動かさない。** 真偽値以外（`"true"` / `1` / `null`）は `false` と同じ。未コミットの変更が
  あるときに出る「追従しません」の断りも、有効にしたときだけ出る
- **個人の宣言 `.ndf/worktree.local.json` を足した**（#495）。機械ごとに違う値を、共有の
  宣言を書き換えずに置ける。反映するのは `localenv` / `testenv`（`expose` を除く）/
  `follow_branch` の 3 つだけで、`base_branch` / `production_branch` / `guard` と未知の項目は
  反映しない。オブジェクトは深く併合し、**配列は置き換え**、`null` は反映せず、型が合わない
  項目はその項目だけ落ちる。`expose` を落として空になった `testenv` は節ごと反映しない
- `worktree-setup.sh init` が `.ndf/.gitignore` を作る（#495）。個人の宣言を追跡から外す
  登録で、**既に宣言があるリポジトリでは作られない**。`status` が登録の無い状態を 1 行で伝える
- `status` / `check` が個人の宣言の状態を報告する（#495）。「個人の宣言:」の行と、
  「個人の宣言で反映しない項目:」の行を出す。**ファイルが無いときは何も出さない**
- 作業ツリーの宣言のスキーマへ `follow_branch` を足し、`base_branch` の説明を追従の条件付きへ
  直した（#610）。`worktree` の `SKILL.md` と `references/declaration.md`、`AGENTS.md`、
  `KIRO.md`、`development-workflow` の `SKILL.md` の記述も揃えた
- 作業ツリーの現状固定テストを 4 ファイル・約 1,700 行足した（#312 #313 #315 #495 #610）。
  `tests/test_declaration_local.py` と `tests/test_write_target.py` を新設した

### 修正

- **`ndf_lock_is_held` を陳腐化の判定の否定にした**（#312）。持ち主（`pid`）を書く前に落ちた
  プロセスのロックを「握られている」と読んでいたため、時間が経っても `reap` の対象へ戻らな
  かった。5 分（`NDF_LOCK_STALE_MINUTES`）を過ぎたものは握られていないと読む。判定の規則は
  `_ndf_lock_is_stale` の 1 か所に置いた
- **台帳の更新の失敗を呼び出し元が受け取る**（#315）。`env` はポートと解放の記録の失敗を受け、
  `up` は基準のタグの記録の失敗で止まり（時刻の記録は警告に留める）、`down` / `expose` /
  `unexpose` は記録の失敗を知らせる。これまでは黙って続けていた
- **書き込み先の走査がリダイレクトを読み飛ばして命令の区切りまで続く**（#313）。
  `sed -i 's/a/b/' x.md >log y.md` の `y.md` を書き込み先として出す。読み飛ばしはオプションの
  引数を取る判定より前に置き、プロセス置換 `<(` はリダイレクトとして扱わない。入力側の語は
  書き込み先として出さない
- **`worktree-setup.sh init` が読めない宣言を失敗として報告する**（#573）。JSON として壊れて
  いる・`version` が未対応のときは、状態の行と案内を標準エラーへ出して終了コード 1 で終わる。
  「既にあります」とは報告しない。`worktree` の手順 0 はそこで止まり、エージェントは宣言を
  書き換えない
- 個人の宣言の `testenv` が `expose` だけのとき、空の節を重ねない（#495）

## [ndf 10.11.0] - 2026-09-13

### 追加

- **`development-workflow` が起動時に作業ツリーの宣言を確かめる**（#527）。判定の手順へ手順 0 を
  足した。`.ndf/worktree.json` が無ければ `worktree` の宣言の用意を通し、読めなければ先へ進まない。
  判定結果の出力に `宣言:` の行を足した。**拒否はしない**
- `worktree-setup.sh check` を新設した（#527）。宣言の状態を終了コードで返す（0 あり / 2 なし /
  3 読めない / 1 判定できない）。ファイルを作らず、通信しない。状態の判定は
  `worktree-common.sh` の `wt_declaration_state` の 1 か所に置き、`status` も同じ関数から読む
- **`pr-body-decisions.sh` を新設した**（#545）。`check`（0 一致・対象外 / 1 食い違い / 2 読めない /
  3 呼び出しの誤り）と `sync`（本文の `## 決めたこと` の節だけを設計文書の `## 決定の記録` の
  見出しから書き直す）を持つ。対象は head が `design/` で始まる Pull Request
- `pr` の作成直後と本文の更新後、`fix` の修正手順 12 で `pr-body-decisions.sh sync` を呼ぶ（#545）。
  `approval-request.md` は設計 Pull Request のマージの提示の前に `check` を通し、判断に使うものへ
  「本文との一致」を足した
- 継続的統合に `pr-body-decisions.yml` を足した（#545）。`opened` / `synchronize` / `edited` /
  `reopened` で `check` を走らせる。**必須の検査にはしていない**
- **リンク検査が見出しへの参照を照合する**（#445）。`#見出し` と `other.md#見出し` を、相手の
  文書の ATX 見出しから GitHub の規則で作った名前と突き合わせ、一致しなければ落とす
- リンク検査の走査へ `issues/`（`issues/old/` を含む）を入れた（#543）。インラインコードの中の
  記法の例はリンクとして読まない。壊れていた記録の参照 27 件を `](…)` の中だけ直した
- **検査 J に版の形の表と次の開発の例を例どうしで比べる規則を足した**（#566）。正式版・開発版・
  公開前の確認版の行の接尾辞と基底の大小、「の次を開発するなら」の左右の大小を見る
- **`runtime-smoke` が hooks 定義をランタイム自身に読ませる**（#571）。Claude Code と Codex の
  読み込みの報告（警告・誤り）が 1 件でもあれば `runtime-smoke (claude)` / `(codex)` を落とす。
  受け取るキーの一覧はこちらで持たない。陽性対照の fixture を置いた
- `design` に、表から導ける値を数え直す規則と、値の集合へ値を足す設計で既存の規則を集める
  手順を足した（#526）。体裁設計の共通規則を `references/layout-common.md` へ集めた
- 開発ガイドに「既存プラグインへ Skill を足す」節を足した（#525）。触る 15 箇所を実行順に並べ、
  各箇所に取りこぼしを拾う検査の名前か「検査が拾わない」を書いた

### 変更

- **版数と配布の手順・実測・一覧を `docs/versioning-and-distribution.md` へ移した**（#499）。
  `AGENTS.md` には判断の基準を「版と配布の方針」として残し、478 行 → 214 行。検査 J の読む先を
  正本の章 2 へ動かした
- **Skill 執筆の規約を `plugins/ndf/skills/README.md` から `plugins/ndf/skills/AUTHORING.md` へ
  改名した**（#500）。本文は変えていない。根の `README.md` を入口の役割へ戻し（492 → 183 行）、
  `plugins/ndf/README.md` の「検証」「開発者向け」を「変更するとき」の 1 節にまとめた
- マイルストーン 16 の要求・設計・計画 33 本を確定仕様へまとめた。
  `docs/specifications/doc-consistency-checks.md` を新設し、既存の仕様 4 本へ統合した

### 修正

- **進行の記録の値に制御演算子が密着しても、通過工程の控えに積む**（#565）。`"設計";` のように
  `;` `&` `|` などが密着すると値の一部として読まれ、工程名ではないとして黙って捨てられていた。
  `wf_split` が引用の外の演算子と改行で区切りを出す
- Kiro のインストーラが、`--project` に存在しないパスかファイルを渡されたときに `ERROR:` と
  終了コード 2 で止まる（#415）。これまでは `cd` の裸のエラーと終了コード 1 で終わっていた
- `--model` の例の claude の識別子を `opus-5` から正式名 `claude-opus-5` へ直した（#563）。
  `opus-5` を写すと `unrecognized_model` で 404 になっていた
- `development-workflow` のテストの補助 `path_with` が、読めないディレクトリを PATH に持つ環境で
  `PermissionError` を出して落ちていた（#555）。一覧するのを隠したいコマンドを持つディレクトリ
  だけにした
- `runtime-smoke` が失敗した実行でもコンテナを残さない（#571）。後片付けを `EXIT` の trap へ移した

## [ndf 10.10.1] - 2026-09-12

### 修正

- Claude Code の起動時に出ていた `hooks.json: unknown key "description" ... ignored` の警告を
  解消した（#568）。hooks 定義のマッチャーグループ（`hooks.<イベント>[n]`）から、Claude Code が
  認識しないキーを外した。`ndf` は `PreToolUse[0]` と `SessionStart[0]` の `description`
- 同じ警告を出していた `mcp-serena` の `SessionStart[0]` の `description` と、`mcp-playwright` の
  `SessionStart[0]` の `description` / `priority` / `enabled` を外した（#568、mcp-serena 2.0.1 /
  mcp-playwright 2.0.1）。**Claude Code は版数でキャッシュを分けるため、版を上げないと利用者の
  手元の実体が入れ替わらない**

## [ndf 10.10.0] - 2026-09-12

### 追加

- **指摘に根拠と反証条件を求める規約**（#156）。`cross-review` の指摘は位置・再現の筋道・
  反証条件の 3 項目を持ち、揃ったものだけが `has_evidence` になる。`docs/06-evidence.md` を
  新設し、根拠の求め方・独立発見・走らせる順序・区分・効果の測定を 1 本にまとめた
- **独立発見の規約**（#156）。担当が参照してよい既存コメントを起動時のスナップショットに
  限った。同じラウンドで先に投稿した担当の指摘は渡さない。**同じ指摘が 2 者から出たことに
  意味があるのは、互いを見ていない場合だけである**
- **反証と実行検証**（#156）。収束ループへ Step 2.5 を足した。`state.py verify-findings` と
  `scripts/critique.sh` / `scripts/critique-round.sh` を新設し、レビュー担当が互いの指摘へ
  支持・反証を出す。`--verify-command` を渡したラウンドは、そのコマンドを実行して再現を
  確かめる
- **証拠ベース集約**（#156）。指摘を `verified_blocking` / `verified_non_blocking` /
  `rejected` / `needs_human_judgment` / `insufficient_evidence` の 5 区分へ集約する。
  収束の判定が数えるのは `verified_blocking` と `needs_human_judgment` の 2 つだけで、
  棄却した指摘はラウンドを増やさない
- `--verify-command` / `--verify-exit-code` を `cross-review` へ足した（#156）。渡さなければ
  実行検証を行わない。再現とみなす終了コードは既定 `1` で、**「0 でない」を再現としない**
- **却下した指摘を per-item で残す**（#156）。`rejected_findings` へ位置と理由を蓄積し、
  次のラウンドで同じ論点が再提出されたときに突き合わせる
- **効果の測定**（#156）。`scripts/measure.py` を新設した。状態ファイル 1 つから
  `single` / `majority` / `proposed` / `oracle` の 4 方式を読み、費用と収束の様子を出す。
  収束ループの外にあり、手順の途中では呼ばない
- **コンテキストの窓を工程の単位として扱う規約**（#546）。`development-workflow` へ
  `references/context-window.md` を新設し、切ってよい点・委譲する対象・残量の見方を定めた。
  工程表の行は増やしていない

### 変更

- `fix` が返す `rejected` の各要素が `path` / `line` / `severity` を持つ（#156）。位置が無いと、
  却下した論点が再提出されたときに同じ指摘だと判定できない（実測では同じ論点が 5 ラウンド
  続けて提出された）
- `release` の手順へ、出た版の判断を開発の指示書から退避する規約を足した（#551）。指示書に
  残すと、その内容を全セッションと全サブエージェントが毎回読む
- `cross-review` の `SKILL.md` から `<worktree-base>` の解決順を `docs/04-contracts.md` へ
  移した（#156）
- `CLAUDE.md` の v10.0.0〜v10.5.1 の判断を `docs/ndf-version-decisions.md` へ移した（#551）。
  `CLAUDE.md` は 333 行 → 79 行。参照から `@` を外し、毎回の読み込みから外した
- 計画と設計 9 本 2207 行を `docs/specifications/cross-review-evidence-based.md` 464 行の
  確定仕様 1 本へまとめた（#156）

## [ndf 10.9.1] - 2026-09-09

### 修正

- **`refactor.py start-round` が提案・レビューの母集合を返す**（#518）。`RUNTIMES` と
  `RUNTIMES_CSV` を足した。`init` だけが返していたため、状態ファイルから再開する経路と、
  骨組みを抜粋して写す経路で `unbound variable` になっていた
- **`--scope` の関門が、名前で当たらないときだけ実体を 1 段だけ走査する**（#518）。
  `tests/` を実体として持つ親ディレクトリが通るようになった。返す値は当たった置き場所
  そのもので、渡された親ではない
- **進行側の push が credential helper の不全で止まらない**（#524）。失敗したときに
  `gh auth git-credential` へ退避して 1 度だけ再試行する。既定の経路は変えていない

### 追加

- `plugins/ndf/scripts/lib/git-credential.sh` を新設した（#524）。退避に使う `git` の
  オプションを 1 か所で持つ
- `scripts/check-skill-shell-vars.py` を新設した（#518）。手順書の bash が参照する変数が、
  その行より前のコマンドで得られるかを検査する。継続的統合のジョブは 14 個になった
- `pr` / `fix` / `cross-refactoring` の `SKILL.md` へ退避の手を書いた（#524）

### 変更

- `refactor_lib` の `is_test_location` / `test_locations` が走査の起点を受け取る（#518）

## [ndf 10.9.0] - 2026-09-09

### 追加

- **`development-workflow` に `documentation` モードを足した**（#507）。工程表が 18 行 × 5 列に
  なり、`素材の収集と出典の確定`（`設計` の後）と `体裁レビュー`（`配布` の後）の 2 行が増えた。
  判定の順序は `operation` → `documentation` → `standard` → `legacy-refactor` → `light`
- `development-workflow/references/document-types.md` を新設した（#507）。6 タイプ（提案・企画 /
  決裁・稟議 / 定例報告 / 指標定義 / 運用マニュアル / 説明・研修）の判定条件と境界事例を持つ
- `development-workflow/references/document-destinations.md` を新設した（#508）。文書の提出先の
  宣言（`.ndf/document.json`）の形と、本番の提出先ごとに対の下書き先を求める契約を持つ
- **`document-systems` を新設した**（#515）。Google Drive / Notion / Confluence / SharePoint /
  リポジトリ自身の 5 システムを 1 システム 1 ファイルで持つ。各ファイルは同じ 8 項目
  （認証 / 取り込みの手段と取れないもの / 投稿の手段 / 本文の表現 / 版の扱い / 図の扱い /
  描画して見る手段 / 既知の失敗）を持つ
- `document-systems/references/import.md` を新設した（#514）。外部の文書を取り込む 3 つの用途、
  正規化、格納先、継続的統合で動かすかを持つ
- **`document-sources` を新設した**（#509）。出所として残す 4 項目（場所 / 位置 / 時点 / 手段）と、
  実績 / 見込み / 概算の区別を持つ。取得の手段は持たない
- **`document-drafting` を新設した**（#510）。6 タイプの参照を持ち、各参照は同じ 4 つの節
  （必ず書く節 / 書かない節 / 読み手が最初に問うこと / よくある欠落）を持つ
- **`layout-review` を新設した**（#513）。生成物を描画して版面を見る。機械で測る項目と人が画像を
  見る項目を分け、**描画できない・画像を読めないときは止める**
- `release` に出力の形を 4 つ足した（#511）。`form-slide.md` / `form-document.md` /
  `form-spreadsheet.md` / `form-page.md`。**形の参照はシステム固有の手順を持たない**
- `design` に体裁設計を足した（#513）。`references/layout-slide.md` / `layout-document.md` /
  `layout-spreadsheet.md` / `layout-page.md` の 4 本
- `requirements-design/references/document-requirements.md` を新設した（#509）。読み手・目的・
  読み手に求める判断を受け入れ条件として書く形

### 変更

- `workflow-common.sh` の `WF_MODES` が 5 値、`WF_STAGE_MATRIX` が 18 行 × 5 列、
  `WF_MODE_HEIGHT` が 5 値になった（#507）。`wf_stage_class` が 5 列目を読む
- `projects-common.sh` の `PJ_STAGES` が 18 値、`PJ_MODES` が 5 値になった（#512）。
  **工程名の並びを持つ箇所が 5 つになり、すべて一致する**
- `quality-gates` のモード別の表へ `documentation` の行を足した（#509）。必須の段は 3 で、
  追加で**事実確認**（書かれた値と出典の突き合わせ）を求める。**事実確認は書いた本人だけでは
  完了しない**（`cross-review` のレビュワーが行う）
- 承認の 2 つの関門へ文書での意味（企画承認 / 制作物承認）を写像した（#508）。**関門は 2 つの
  ままで、`WF_APPROVAL_LABEL` と `WF_DESIGN_PREFIX` は変えていない**
- 配布 Skill が 41 / 40 / 39 / 39 から **45 / 44 / 43 / 43** になった
  （Claude Code / Kiro CLI / Codex / agy）

## [ndf 10.8.0] - 2026-09-08

### 追加

- **`design/references/deliverables.md` を新設した**（#375）。モード × 成果物の要否表 17 行と、
  成果物ごとの中身・書き先を持つ。水準は「必須」と「該当時」の 2 つで、**「不要」を表す値を
  置かない**
- `design/references/structure-behavior.md` を新設した（#375）。クラス図・オブジェクト図・
  処理の流れ・状態遷移図の書き分けと粒度
- `design/references/system-architecture.md` を新設した（#375）。文脈・構成要素・配置の
  3 階層と、パッケージ・モジュール構成
- `design/references/nonfunctional.md` を新設した（#375）。6 大項目に対する実現方式と、
  非機能設計表の 4 列（大項目・要求の条件・実現方式・確かめ方）
- `requirements-design/references/nonfunctional-requirements.md` を新設した（#376）。
  IPA 非機能要求グレードの 6 大項目それぞれの書き方・例・該当の判定表
- `design/references/design-template.md` へ「進む前に突き合わせる対」の 6 つを置いた（#463）。
  いずれも同じ文書の中だけで確かめられ、**確かめた結果は残さない**
- `design/references/data-structure.md` へ ER 図・テーブル定義・CRUD 図を足した（#375）
- `design/references/interface-ui.md` へ項目定義と、画面一覧・遷移・レイアウトの粒度を足した（#375）
- `plugins/ndf/docs/kiro-cli.md` を新設した（#416）。`plugins/ndf/README.md` から Kiro CLI の
  運用と制限（99 行）を移した

### 変更

- **工程表の「設計」の行を `design`（該当時）/ `design`（該当時）/ `design` / `design` へ、
  `WF_STAGE_MATRIX` の同じ行を `C\tC\tR\tR` へ変えた**（#375）。`light` と `operation` でも、
  触る領域が「すべての変更」以外に 1 つ以上当たれば `design` を通る。**独立した設計文書は
  作らない**（受け入れ条件か実行の記録と同じファイルの節へ書く）
- `design/SKILL.md` の「モードごとの成果物」を、水準の定義・設計の書き先・省いた理由の書き先の
  3 つの表へ置き換えた（#375）。「設計で重視する 3 点」は「構造と振る舞い」を加えて 4 点になった
- `design/SKILL.md` の「触る領域を決める」へ 3 行を足した（#375）。構成要素の追加・変更または
  配置の変更、型・クラスの変更または状態遷移か処理の順序の変更、非機能の条件
- `requirements-design` の非機能を 6 大項目へ広げた（#376）。**現行 4 種（性能・容量・権限・
  記録）の書き方と例は、親を付け替えて残している**
- `development-workflow/references/stage-notes.md` / `workflow-modes.md` /
  `operation-run.md` を、設計が条件付きで通る形へ改めた（#375）
- `plan-to-spec/SKILL.md` へクラス図の同期の時点を書いた（#375）。**`docs/` へ移すときの
  1 回だけである**
- **`AGENTS.md` の DO で確かめる対象を、外部コマンドから入力と出力の形まで広げた**（#432）。
  実際に使う入力の形を 1 度通すことと、内部で決めた区切りを読む側と書く側の両方で見ること
- `plugins/ndf/README.md` が 458 行から 365 行になった（#416）。配布のたびに書き直す更新案内が
  伸びる余裕を 135 行確保した

## [ndf 10.7.0] - 2026-09-07

### 追加

- **兆候と手法の呼び名の表を `refactoring/references/vocabulary.md` へ置いた**（#444）。
  兆候 20 組・手法 18 組の識別子と日本語の名前、手法ごとの差分予算の倍率を持つ。
  **ここが呼び名を持つ唯一の場所である**
- `refactoring/references/test-changes.md` を新設した（#443）。テストの変更を 3 つに分け、
  判定を機械 → AI エージェント → 人の 3 段で行う。段階（移送 → テストを寄せる → 仕掛けを
  外す）の分け方も置いた
- 兆候を 3 つ足した（#443）。`test_coupled_to_internals` /
  `test_bypasses_module_boundary` / `mock_targets_implementation_detail`
- `implementation-plan/references/pre-refactoring.md` を新設した（#442）。実装の前に構造を
  整えるかの判断。**工程表の行は増やしていない**
- `cross-refactoring` に `judge-test-changes` フェーズと `merge-test-judgements` を足した
  （#443）。機械で決まらないテストの差分を AI エージェントへ渡し、答えを取り込む
- `cross-refactoring/tests/test_module_boundaries.py` を新設した（#441）。依存の循環・
  モジュールをまたぐ非公開名・`commands` 層の横の依存を見る

### 変更

- **`cross-refactoring` が呼び名を自分で持たなくなった**（#444）。`refactoring` の表を読み、
  読めなければ `init` の時点で止まる
- `refactor.py` から再エクスポートと `__setattr__` の仕掛けを外した（#441）。入口は
  argparse と `main` だけを持つ（244 行 → 190 行）
- モジュールをまたぐ非公開名を 27 個から 0 個にした（#441）。`commands` 層どうしの横の
  依存も 3 本から 0 本になった
- `cross-refactoring` のテストをモジュール境界へ寄せた（#440）。`conftest.py` が
  モジュールごとのフィクスチャを持つ
- `check-cross-skill-refs.py` の走査を `references` まで広げた（#444）。**配布物にテストは
  含まれない**ため、走査から `tests` を外した
- `cross-refactoring` の検証がテストの期待値の変更を見る（#443）。**機械が「変わっていない」
  と言えるのは前後が同一のときだけ**で、値が失われたときに落とし、それ以外は AI へ回す

### 削除

- `cross-refactoring` からレビュー機構の残骸を取り除いた（#446）。`prompts/review.md`、
  `launch-cli.sh` の `review` フェーズ、投稿の event の注記
- `commands/review.py` を `commands/converge.py` へ改名した（#446）。持つのは収束ループの
  判定と取り消しであり、レビューではない

### 修正

- 通過工程の控えの移行を、更新の案内へ入れた（#451）。控えは課題ごとに残り続けるため、
  旧い名前のままだと通った工程が「記録なし」として案内される

## [ndf 10.6.0] - 2026-09-07

### 追加

- **運用モード `operation` を新設した**（#423）。コードも文書も変えず、外部の系の状態だけを
  変える変更のためのモードである。判定の順序では 1 番に置く
- `development-workflow/references/operation-run.md` を新設した（#423）。実行の範囲・
  取り消しの手段・記録の書式・失敗したときの止め方を置いた
- **`document-restructuring` を新設した**（#391）。書き上げた文書を章立てから組み直す手順で、
  測る → 組み直す → 整える → 測り直すの 4 段を持つ。言語ごとの数え方は
  `references/lang-<言語>.md` にある
- 工程表へ「ドキュメント再構成」を足した（#391）
- **`cross-refactoring` にテスト整備ラウンドを新設した**（#436）。提案より前に、テストの
  薄い箇所へ現状固定テストを足す。上限は `--max-test-rounds`（既定 2）
- **適用ラウンドを新設した**（#436）。書き換えるファイルが重ならない項目だけを 1 つの群にし、
  **1 件の失敗が群の外へ及ばない**
- `--ci-check` を足した（#436）。最終ゲートを継続的統合のテストで代替する

### 変更

- **破壊的: モードの一覧を変えた**（#421 / #423）。`architecture` を廃止して `standard` へ
  統合し、`operation` を足した。母集合は `light` / `operation` / `legacy-refactor` /
  `standard`（**高さ順**）になる
- **破壊的: 工程名を 2 件改名した**（#391）。設計レビュー → **ドキュメントレビュー**、
  レビュー → **実装レビュー**
- **破壊的: `cross-refactoring` の副コマンド 2 つを削除した**（#436）。`judge-review` と
  `review-targets`。レビューをテストへ置き換えたため呼び出し元が無くなった
- **`cross-refactoring` の Step 5 をテストの工程にした**（#436）。2 CLI のレビューは起動せず、
  `--baseline-test` の合否で判定する
- コミットの粒度を**適用ラウンドごとに 1 つ**へ変えた（#436）
- 改修計画を `issues/refactoring-plan-rf<PR>.md` から **Pull Request のコメント 1 件**へ
  移した（#436）
- `--max-outer-rounds` の既定を 4 から **3** へ下げた（#436）
- `--scope` にテストの置き場所が無ければ**止める**ようにした（#436）
- 承認の関門 2 を「本番のチャネルへ届く操作」から「**本番の系へ届く操作**」へ広げた（#423）。
  配布と運用モードの実行の両方を含む。**関門は 2 つのまま**
- `refactor.py`（3706 行）を `refactor_lib/` の 13 モジュールへ分割した（#438）

### 移行

**後方互換の仕組みは持たない。** 記録済みの値は移行で 1 度だけ書き換える。

| 書き換え | 対象 |
| --- | --- |
| `モード: architecture` → `モード: standard` | 課題の本文の `## 進行` の節 |
| `- [ ] 設計レビュー` → `- [ ] ドキュメントレビュー` | 同上 |
| `- [ ] レビュー` → `- [ ] 実装レビュー` | 同上 |
| `- [ ] ドキュメント再構成` の挿入 | 「設計」の次の行 |
| `設計レビュー` → `ドキュメントレビュー` / `レビュー` → `実装レビュー` / `architecture` → `standard` | **通過工程の控え**（下のコマンド） |

**`## 進行` の節だけを書き換え、節の外は書き換えない。** 対象は版を出す時点で数え直す。

**通過工程の控えも書き換える。** 課題ごとに 1 つのファイルが残り続けるため、旧い名前のまま
では**実際に通った工程が「記録なし」として案内される**。`mode` が `architecture` のままだと
モードが解決できず、**必須の工程の欠落を検知できなくなる**。

置き場所は環境で変わるため、次の 4 つを順に探す。**無い候補は読み飛ばす。**

```bash
# 1. 数える（書き換える前に対象を確かめる）
for dir in ${CLAUDE_PLUGIN_DATA:+"$CLAUDE_PLUGIN_DATA/stages"} \
           ${XDG_STATE_HOME:+"$XDG_STATE_HOME/ndf/stages"} \
           "$HOME/.local/state/ndf/stages" \
           "${TMPDIR:-/tmp}/ndf-stages"; do
  [ -d "$dir" ] || continue
  n=$(grep -lE '"(設計レビュー|レビュー)"|"architecture"' "$dir"/*.json 2>/dev/null | wc -l)
  printf '%s: %s 件\n' "$dir" "$n"
done

# 2. 書き換える（jq が要る。控えの読み書きは元から jq に依存している）
for dir in ${CLAUDE_PLUGIN_DATA:+"$CLAUDE_PLUGIN_DATA/stages"} \
           ${XDG_STATE_HOME:+"$XDG_STATE_HOME/ndf/stages"} \
           "$HOME/.local/state/ndf/stages" \
           "${TMPDIR:-/tmp}/ndf-stages"; do
  [ -d "$dir" ] || continue
  for f in "$dir"/*.json; do
    [ -f "$f" ] || continue
    jq '(.stages // []) |= map(if . == "設計レビュー" then "ドキュメントレビュー"
                               elif . == "レビュー" then "実装レビュー"
                               else . end)
        | if .mode == "architecture" then .mode = "standard" else . end' "$f" >"$f.new" \
      && mv "$f.new" "$f" || rm -f "$f.new"
  done
done
```

**`ドキュメント再構成` は控えへ挿入しない。** 控えは記録が書かれた工程の並びであり、通って
いない工程を書き加えると、通ったことにならない工程が通ったものとして数えられる。

書き換えた結果は `stage-check.sh report <課題番号>` で確かめる。

盤面（GitHub Projects）の単一選択へ新しい値を足すのは、盤面を持つリポジトリ側の操作である
（値が無くても工程は止まらない）。

**ブランチ名の接頭辞 `design/` と承認ラベル `design-approved` は変えていない。**

## [ndf 10.5.1] - 2026-09-06

### 追加

- Pull Request を作る時点で実行証跡の欠落を案内する（#424）。`gh pr create` を tool 実行前の
  hook が観測し、本文の閉じる語が指す課題の控えからモードの記録・必須の工程の欠落・モードの
  食い違いを読む。**拒否はしない**
- 本番のチャネルを `.ndf/worktree.json` の `production_branch` で宣言できるようにした（#424）。
  宣言が無ければ既定ブランチ。**開発の起点である `base_branch` は流用しない**
- 並行開発の参照（`development-workflow/references/parallel-work.md`）を新設した（#424）。
  課題と Pull Request の 4 つの形、工程が動く単位、任せるうえでの下限 6 つ、
  `issue-plan-strategy` との境界を置いた
- 確定仕様 `docs/specifications/ndf-workflow-unit-and-gates.md` を新設した

### 変更

- **モードを判定する単位を Pull Request にした**（#420）。工程の順序は「要求と受け入れ条件 →
  モード判定 → 作業場所の用意」になり、`light` にも要求と受け入れ条件が掛かる
- **`light` のレビューを `cross-review` にした**（#418）。レビューは 4 モードすべてで通る
- **`light` を gate の検査から外さない**（#422）
- 人手の承認を求める関門を 2 つ（設計 Pull Request のマージ / 本番のチャネルへ届く操作）に
  集約し、要否の決まり方の違いを関門ごとの節に分けた（#424）。`merged` と `pr` から `release`
  の規則への導線を置いた
- 閉じる語の読み取りを `plugins/ndf/skills/merged/scripts/` から
  `plugins/ndf/scripts/lib/closing-issues.sh` へ移した（#424）。**写しは持たない**
- 語の分割（`wf_split`）の区切りを改行から NUL へ変えた（#424）

### 修正

- 行数の検査が非 ASCII のファイル名を拾う（#417）。`git ls-files -z` を使う。除外の後に対象が
  0 件のときと、`EXEMPT` の指し先が無いときも失敗にする
- agy の hook の導入で `--plugin-dir` の相対パスを絶対化し、command を `shlex.quote` した
  絶対パスで組み立て直す（#417）。`--uninstall` の対象を配布する定義の名前に限り、
  書き込みを一時ファイル経由の置き換えにした
- `notion-writing` のバッククォートを含むコード表記を二重の囲みにした（#417）
- スモークの assertion に残っていた `awk … | grep -qw` を、結果を変数で受ける形へ寄せた（#417）
- `development-workflow` の手順書に並んでいたほぼ同一の段落を 1 つにした（#392）

## [playwright-kit 2.0.3] - 2026-09-06

### 修正

- `_drive_auth.py` の案内から「どの公開セットにも同梱していない」を外し、`_CANDIDATES` へ
  agy の導入先を足した（#417）。`google-auth` は 4 つの manifest すべてに載っている

## [ndf 10.5.0] - 2026-09-05

### 追加

- 配らなかった Skill 4 個（`google-auth` / `google-drive` / `ml-model-structure` /
  `skill-stats`）を 4 ランタイムすべてへ配布へ回した（#116）。`optional-skills/` の
  置き場所は無くした
- `notion-writing` を新設した（#144）。Notion-flavored Markdown の記法、テーブルの列幅、
  `<page>` タグを落とすと子ページが削除されること、編集の進め方を扱う
- agy へ hook を差し込む `dev.agy/install-hooks.sh` を新設した（#305）。agy は導入した
  プラグインの `hooks.json` を読み込まないため、利用者の `~/.gemini/config/hooks.json` へ
  差し込む。冪等で、他の項目には触れない
- 説明文書と配布物の Markdown が分割の基準（501 行以上）を超えていないかを見る
  `scripts/check-doc-line-limit.py` を新設し、継続的統合へ配線した（#354）
- 図表ガイドへ状態遷移図（`stateDiagram-v2`）の記法例を足した（#377）
- `release` へ、更新案内に載せたコマンドをランタイムごとに実行して確かめる手順を足した（#328）

### 変更

- 検査の合否を出力ではなく**終了コード**で判定することを `quality-gates` と `pr` へ
  明記した（#383）
- `pr` へ、`gh pr create` が GraphQL の上限で失敗したときの REST への退避を書いた（#384）
- `out-of-scope` を案内する 6 Skill の 12 行へ、起票先のリポジトリをその Skill が決めることを
  足した（#283）
- `cross-refactoring` の手順書を Step 4〜5 と Step 6〜8 の 2 本へ分けた（#354）
- `cross-refactoring` の副コマンドの一覧を `argparse` の `help` 1 か所へ寄せた（#356）
- `playwright-evidence` の証跡の置換の説明を実装へ合わせた（#181、playwright-kit 2.0.2）
- `markdown-writing` へ、書式が 1 ファイルであることを前提にする文書を分割の基準の
  対象外とする書き方を足した（#399）

### 修正

- `state.py` が指す手順の番号を手順書へ揃えた（#355）
- 共通層の移設を確かめるテストが、`__pycache__` の残った作業ディレクトリで落ちていた（#388）
- スモークの `find | grep -q .` が SIGPIPE で終了コード 141 を返していた

### リポジトリの整備（配布物には含まれない）

- 正式版を `main` へ進める手順を Pull Request 経由へ書き直した（#401）。ruleset の bypass が
  `pull_request` のため、`git push origin develop:main` は管理者でも拒まれる
- `claude plugin validate` に必須の位置引数を足した（#279）
- `README.md` の「`develop` はまだ作られていません」を外した（#300）
- agy の更新手段を実測で確定した（#289）。`install` は上書きするが、取得元から取り除いた
  ファイルは実体に残る
- 設定ディレクトリを隔離して開発版チャネルの取得経路を確かめる手順を置いた（#277）
- 「書く前に実行して確かめる」の対象へ、コードが呼ぶ外部コマンドを入れた（#224）
- 基準を超えていた文書 4 本を分割した（#354）
- 確定仕様の `cross-refactoring` の母集合を実装へ合わせ（#353）、配布 Skill の数を
  外した（#288）

## [playwright-kit 2.0.2] - 2026-09-05

### 変更

- 証跡リンクの置換の説明を実装へ合わせた（#181）。対象は証跡フィールドとリンク記法の 2 つで、
  ケースのディレクトリ名は `TC-` 始まりに限らない
- `google-auth` の置き場所の案内を、配布へ回した後のパスへ向けた（#116）

## [ndf 10.4.0] - 2026-09-05

### 追加

- 版を上げる手順（`release`）へ、変更履歴の文書を持つリポジトリではその版の節を足すことを
  組み込んだ（#240）

### 変更

- 課題の手入れ（`issue-upkeep`）の 3 つ目の経路を、マイルストーンの有無だけで決める形にした
  （#387）

### リポジトリの整備（配布物には含まれない）

- 参加する側が読む 4 文書（`CONTRIBUTING` / `CODE_OF_CONDUCT` / `SECURITY` / `SUPPORT`）を
  置いた（#238）
- issue / Pull Request のテンプレート、`CODEOWNERS`、`dependabot.yml` を置いた（#239）
- `main` / `develop` を ruleset で保護し、必須の検査 11 個を通るまでマージできないようにした
  （#237）。Pull Request の検査は絞り込まずに常に起動する
- `README.md` から `CHANGELOG.md` を分離した（#240）。773 → 475 行
- `GOVERNANCE.md` を置き、メンテナーを募る導線を作った（#241）

## [ndf 10.3.1] - 2026-09-05

### 変更

- 課題の手入れ（`issue-upkeep`）の対象を、起票した主体で絞らないことを明記した（#385）

## [ndf 10.3.0] - 2026-09-05

### 追加

- 蓄積した課題を手入れする Skill `issue-upkeep` を新設（#331）。価値の判断（やらない）を
  持つのはこの Skill だけである

### 変更

- `cross-review` のレビュワーを「全ランタイム − ホスト」の輪番 2 者にした（#371）。`--host`
  を足し、推定できないときは既定を置かずに失敗する
- `cross-review` の終了基準を「新しい指摘が出ない」へ変えた。全員 `APPROVE` は最も止まらない
  参加者に律速される
- `architecture` と `legacy-refactor` の構造改善の既定を `cross-refactoring` へ移し、
  再レビューを変更要求を出した担当だけに絞った（#370 / #372）
- 進行の記録を `progress-tracking` へ集めた（#243 / #282 / #281 / #287）。盤面の宣言が無い
  リポジトリでも issue の本文へ残る
- 承認の提示物を 2 層に分け（#318）、マージに他者の承認が要るリポジトリでは到達点を提出と
  レビューの収束へ置き直す（#321）

### 修正

- 認証の確認を共通層（`lib/auth.py`）へ移し、レビュワーの起動を `launch-reviewer.sh` 1 本へ
  寄せた
- 盤面へは識別子を控えて全件取得をやめた。10 件へ 2 つのキーを書いた時点で GraphQL の上限に
  達し、以後の記録が終了コード 0 のまま捨てられていた

## [ndf 10.2.0] - 2026-09-04

### 変更

- 収束ループの共通層を `plugins/ndf/scripts/lib/` へ移した（#285 / #280）。共通層が
  `cross-review` の中にあると、片方の Skill を配らない配布先から読めない
- 進行側の GitHub の呼び出しを REST へ寄せた（#271 / #327）。初期化の 4 点とラウンドごとの
  7 点が GraphQL 0 点になる
- `cross-refactoring` の適用の母集合を 4 CLI すべてにし、`--max-outer-rounds` の既定を 3 から
  4 へ上げた（#216 / #284）。上限 3 では輪番が 4 者目の順番へ届かない
- Skill 本文から自リポジトリ前提を外し、検査（`check-skill-repo-assumptions.py`）を新設した
  （#292）。`cross-review` の手順書の契約は `docs/04-contracts.md` へ分けた（#330）

### 追加

- 収束の判定が継続的統合の失敗を見る。読むのは `check-runs` の 1 回で、`status` は使わない
  （GitHub Actions では常に `pending` を返すため）
- 上限に達している間も収束ループを止めない（#291）。投稿は待ち行列へ積み、**待ち行列が空に
  なるまで収束させない**

## [ndf 10.1.0] - 2026-09-04

### 修正

- 作業ツリー運用の排他を 2 段の関門にした（#297 / #308）。同じ名前へ 6 プロセスが同時に
  `mkdir` コマンドで作る試行を 200 回行うと、overlayfs で 34 件、ext4 で 6 件の複数成功が出る
  （`os.mkdir` を直接呼ぶ経路はどちらも 0 件）
- 書き込み先の走査が、関数定義の本体・`case` のフォールスルー・前置リダイレクトで `cd` の
  位置を取り違えていた（#201 / #197）。リダイレクトの左に添えたファイル記述子の番号を
  書き込み先として拾う誤りも直した

### 変更

- 排他の実装を `plugins/ndf/scripts/lib/lock-common.sh` へ寄せた（#293）。既存の名前
  （`wt_lock_acquire` / `wf_lock_acquire` など）は薄い委譲として残る

## [ndf 10.0.0] - 2026-09-03

### 変更（互換性を壊す）

- **外部 AI の委譲先を `gemini` から `agy`（Antigravity CLI）へ移した。** 母集合から `gemini`
  を外し、あった位置へ `agy` を入れた（並べ替えると担当の割り当てが変わる）

### 追加

- **配布先ランタイムに agy を加えて 4 ランタイム構成にした**（#215）。定義は
  `plugins/ndf/dev.agy/`、配る Skill の基準は `manifests/agy-skills.txt` が持つ
- 工程の飛ばしとマージを機械で見る仕組み（#221 / #266）。設計 Pull Request の見分けは head の
  接頭辞 `design/`、承認の印はラベル `design-approved`。判定できないときは拒否する
- 範囲外の課題の起票先を決める判断表（#229）と、`release` の配備の完了の確かめ方（#228）

### 変更

- 振り返りの記録先を、起点の issue へのコメント 1 件へ移した（#242）
- `cross-review` が投稿の届いていないレビューを収束へ数えない（#261）。振動の検知は位置・
  近傍（3 行）・本文の 3 つの一致で測る（#246）
- `merged` が閉じ忘れた issue を拾う（#259）。GitHub の自動クローズは既定ブランチへマージ
  したときにだけ働く
- 説明文書の版数を検査の対象へ広げ（#178）、書式を `lib/version_pattern.py` へ集約した（#258）

### 修正

- `pytest_plugins` の宣言を根の `conftest.py` へ移し、根から 1 回の起動で全件が通るように
  した（#232 / #233 / #235）
- `worktree` の手順が参照する `$NDF_SCRIPTS` に定義が 1 件も無かった（#193）

### 削除

- 起動前の設定整形（`_gemini-env.sh`）

## [ndf 9.7.0] - 2026-09-02

### 追加

- 説明文書の本文に書かれた版数を検査の対象へ入れた（#209）
- 継続的統合で pytest を実行する（#182）。開発の起点を `develop` として宣言する

### 修正

- 更新案内の見出しと定義ファイルの `description` の検査が、接尾辞付きの版数を読めるように
  した（#248 / #254）。節の版数の走査は囲みで位置を固定し、他のソフトの版数を拾わない
- `cross-review` のラウンド開始時に作業ツリーを Pull Request の head へ揃える（#217）
- 結果を残さなかったレビュワーを収束と判定しない（#196）

## [ndf 9.6.0] - 2026-09-01

### 追加

- 設計工程の Skill `design` を新設し、工程表へ「設計」と「設計レビュー」を結んだ（#161）。
  作る文書はモードが決め、読ませる参照は変更が触る領域が決める
- 設計レビューの工程。`standard` と `architecture` では、設計だけを載せた Pull Request を
  実装より先にマージする。新しい Skill は作らず `pr` → `cross-review` → `merged` を順に呼ぶ
- 開発の起点ブランチを `.ndf/worktree.json` の `base_branch` で宣言できるようにした（#202）

## [ndf 9.5.0] - 2026-09-01

### 追加

- 配布の工程 `release` を新設し、版を上げる担い手と時期を決めた（#188）。配布は「検証への
  配布」と「本番への配布」の 2 段階に分かれ、**本番への配布は承認を得るまで進めない**
- 工程の進行を GitHub Projects の盤面へ記録できるようにした（#176）

### 変更

- 配布チャネルを 2 つに分けた。**`main` が正式版、`develop` が開発版**で、正式版として出す
  ときだけ `main` を `develop` の位置へ進める

### 修正

- 作業ツリーの書き込み先の判定が同じコマンドの中の `cd` を反映するようにした（#186）
- `cross-review` が既存の作業ツリーを Pull Request の head へ同期する（#203）。実測では
  8 コミット古い作業ツリーがそのまま使われていた
- Kiro の文脈量の上限を外し、`runtime-smoke (kiro)` の予算超過を解消した（#199）

## [ndf 9.4.0] - 2026-08-31

### 変更

- `cross-review` の収束判定へ未解決の指摘を入れた（#33 / #37）。再開の時点で残っていた指摘を、
  修正の工程を 1 度通すまで収束させない
- 説明文書の記載（公開 Skill 数・カテゴリ内訳・配布先の表・更新案内の見出しの版数）を検査の
  対象へ広げた（#178）

### 修正

- `eval "$(スクリプト)"` はコマンド置換の終了コードを飲むため、新設した停止条件で止まらない。
  変数で受けて終了コードを見る形へ統一した

## [ndf 9.3.0] - 2026-08-31

### 追加

- 工程を 2 つ（`release-verification` / `retrospective`）と、順序を持たない横断的な手順を
  1 つ（`out-of-scope`）追加した（#175）
- リリース後テストの起点は、版の配布が終わった後に置く。マージ直後に置くと、配布の過程で
  壊れるものが対象から外れる
- 範囲外の課題は見つけたその場で起票する。判断は 3 択（起票する / 範囲内へ入れる /
  起票しない）に限り、3 つ目も理由を 1 行残す

## [ndf 9.2.1] - 2026-08-30

### 修正

- 作業ツリー運用の実機確認（#173）で見つかった 5 件。`reap` が `SLOT` 未定義のまま
  `compose stop` を呼ぶ、`--kind` の案内が `printf` の書式として解釈される、主ディレクトリを
  現在地から解決していた、基準のタグが対象の不在と空の内容を区別しない、書き込み先の抽出が
  ヒアドキュメントの本文を含む

## [ndf 9.2.0] - 2026-08-30

### 追加

- Skill `worktree` を追加し、開発の変更を作業ツリーの中で行う運用を 3 ランタイムへ結んだ。
  誘導（tool 実行前の hook）・逸脱検知（セッション開始時の hook）・是正（Skill の移送手順）の
  3 層で支える
- **主ディレクトリの編集は拒否しない。** リポジトリ側に `.ndf/worktree.json` があるときだけ
  動き、無ければ何も出力せず終了コード 0 で終わる

## [ndf 9.1.2] - 2026-08-30

### 変更

- 多義語のルールの適用範囲をガイドと `SKILL.md` へ明記した。新しく書く文書と改訂する文書に
  適用し、既存の文書を一括で直す必要はない

## [ndf 9.1.1] - 2026-08-30

### 修正

- 多義語のセルフチェックを `if` 文へ直した。`[ 条件 ] && echo` の形は、候補が 1 つも出ない
  ときにループ最後の判定が偽になり、終了コードが 1 になる

## [ndf 9.1.0] - 2026-08-30

### 追加

- `markdown-writing` に「指す対象が文脈で変わる語に、この文書での意味を与える」（ルール 2）を
  追加し、以降のルール番号を 1 つ繰り下げた（#163）。既定の対応は一意な語への言い換えで、
  定義は言い換えが不自然な場合に置く
- セルフチェックは同一ファイル内で 5 回以上出た語だけを候補にする（有無だけで拾うと 130
  ファイル中 91 件が候補になり、閾値 5 で 30 件へ絞れる）

## [playwright-kit 2.0.1] - 2026-08-31

### 修正

- 証跡リンクの置換が働かない状態を直した（#81）。拾う対象がリンク記法かつ `TC-` 始まりに
  限られており、報告書が出すコード表記と噛み合っていなかった

## [ndf 9.0.0] - 2026-08-28

### 変更（互換性を壊す）

- **配布物を単一ディレクトリへ統合した。** `plugins/ndf-{shared,claude,codex,kiro}/` を
  `plugins/ndf/` へ、`plugins/playwright-kit-*/` を `plugins/playwright-kit/` へ、
  `plugins/mcp/{shared,claude,codex,kiro}/<名前>/` を `plugins/mcp/<名前>/` へまとめた
- **再インストールが要る。** Kiro CLI の installer は `plugins/ndf/dev.kiro/install.sh` へ移った

### 削除

- Codex 用の `.agents/plugins/marketplace.json`。Codex は `.claude-plugin/marketplace.json` へ
  フォールバックする

## [ndf 8.6.0] - 2026-08-23

### 変更

- `cross-refactoring` のコミット粒度を 1 改善項目 = 1 コミットにした（現状固定テストが要る
  項目のみ 2 コミット）。テストの回数も項目の単位に合わせた（実測 44 手で 88 回、約 38 分）

### 追加

- 改修計画を `--plan-file`（既定 `issues/refactoring-plan-rf<PR>.md`）へ書き出し、生成物の
  同期と同じコミットで公開する。理由と手順は提案の時点でしか残らなかった

## [ndf 8.5.4] - 2026-08-22

### 修正

- 抽出系の手法で差分予算を超える不具合。呼び出し側の書き換え・import の追加・引数の受け渡しが
  固定費として乗る。実測で落ちた 4 件はいずれも見積の 2.03〜2.31 倍で、範囲の逸脱ではなかった。
  抽出系の 7 手法だけ倍率を 3 にした

### 追加

- `init` が kiro の既定 `auto` を検知し、そのラウンドが集計から分離されることを着手前に知らせる

## [ndf 8.5.3] - 2026-08-22

### 変更

- 投稿できていないレビューを判定に使わない。結果ファイルの `review_url` を必須にし、URL の
  識別子から GitHub 側にレビューがあるかを確かめる
- 投稿に失敗したときも `post_error` 付きの結果ファイルを書かせ、「レビュー担当が動かなかった」と
  「投稿できなかった」を区別できるようにした

## [ndf 8.5.2] - 2026-08-22

### 修正

- 自分の Pull Request でレビューを投稿できない不具合。GitHub は自分の Pull Request への
  `APPROVE` と `REQUEST_CHANGES` をどちらも `HTTP 422` で拒む。`init` が作成者を照合し、
  自分の Pull Request なら投稿の event だけを `COMMENT` へ倒す。収束判定は変わらない

## [ndf 8.5.1] - 2026-08-21

### 修正

- レビュー結果が欠け続けたときに進行が終わらない不具合。差し戻し上限の出口が修正フェーズの
  起点を記録せず、`merge-fix` が範囲を確定できないまま `fix_rounds` が進まなかった
- レビュー結果の欠落を実装担当へ回さない。結果ファイルを残さなかった担当がいれば、変更要求
  ではなく進行の中断（終了コード 4）として扱う

## [ndf 8.5.0] - 2026-08-19

### 修正

- 生成物の同期が止まる不具合。`git status --porcelain` を固定幅で読む箇所が出力全体を
  `strip()` していたため、変更パスの先頭 1 文字が欠けて `git add` が失敗していた
- 提案の直前に読み取り用の作業ディレクトリを同期する。取り消しで進んだ HEAD が届かず、
  消えたコードへの提案が返っていた

### 変更

- 実装担当の置き土産（コミットされなかった変更）を、取り込みの前に捨てる。`cross-review` は
  申告されたコメント数を GitHub 側の実数と突き合わせ、届いていなければ中断する

## [ndf 8.4.0] - 2026-08-18

### 追加

- `markdown-writing` に「敬意と節度のある表現で書く」（ルール 4）を追加し、以降のルール番号を
  1 つ繰り下げた。強い否定語・過剰な装飾語・根拠の曖昧な断定の 3 種を扱う

### 変更

- `01-diagram-guide.md` を図表ルールの冒頭から手順として読ませ、上限値や記法は `SKILL.md` へ
  書かずガイド側に置く構成にした
- `pr` の完了報告を `### 6. 完了報告` として手順に組み込んだ。**PR URL は生の URL のまま書く**
  （Markdown リンクにすると番号しか表示されない）

### 修正

- 差分外の行を指すインラインで指摘が丸ごと消える。`422` はレビュー本体ごと落とすため、
  差分外は body に書き、`422` 時はインラインを body へ移して再投稿する
- 投稿に失敗すると前ラウンドの結果で判定が続く。`monitor.py` が実行権限を持たず止まる

## [ndf 8.3.0] - 2026-08-17

### 変更（互換性を壊す）

- **`cross-refactoring` の公開（push）の責務を進行側へ一本化した。** 実装担当は push せず、
  進行側が検証を通してから push する。適用・修正のプロンプトから push の指示が外れた

### 追加

- `--sync-command` を新設。push の直前に進行側が実行し、差分はどの改善項目にも属さない
  コミットとして積む。生成物を持つリポジトリでは指定する

### 修正

- 適用に失敗した項目が次ラウンドで再採用される。項目別の失敗とラウンド全体の取り消しの両方
  から対象外（`deferred_items`）へ記録する

## [ndf 8.2.0] - 2026-08-16

### 修正

- `cross-refactoring` の実機検証（PR #118）で見つかった 9 件。取り消しが他項目のコミットと
  競合する、取り消し失敗を握り潰して進行する（中断を終了コード 4 で表す）、適用結果が状態に
  残らない、未検証の変更が公開されたまま残る、範囲外の変更を検証しない、提案の記録が次
  ラウンドで上書きされる、委譲先の CLI が手順書を読めない、語彙の許容値をプロンプトが列挙
  しない、初期化が CLI の認証を確認しない

### 変更

- 提案の結果ファイル名が `<ランタイム>-propose-rf<ID>-r<ラウンド>-result.json` へ変わった。
  `--scope` には現状固定テストの置き場所も含める（検証に効くため）

## [ndf 8.1.0] - 2026-08-16

### 追加

- 多ランタイム・リファクタリング収束ループ `cross-refactoring` を追加した。提案・レビューは
  「全ランタイム − ホスト」の 3 者、適用は輪番で 1 者。**実装した者と評価する者が同一モデルに
  ならない**。収束しない改善項目は項目単位で取り消し、合意済みの項目は残る
- `--model <ランタイム>=<モデル>` でモデルを固定でき、`report --metrics` がランタイム × モデル
  で集計する
- `external-ai` に Kiro CLI と `claude -p` の非対話実行手順を追加し、対象 CLI が 4 つになった

### 変更

- 収束ループの共通層を切り出した（のちに `plugins/ndf/scripts/lib/` へ移設）。レビューは提案
  ラウンドの差分全体に対して 1 回だけ回す（1 ラウンド 33 回 → 9 回）

## [ndf 8.0.0] - 2026-08-15

### 変更（互換性を壊す）

- **`safe-refactoring` を `refactoring` へ改名した。** 引数と手順は変わらない

### 追加

- 分岐・反復・定数の表現を決める観点を統合した。兆候の一覧に 3 件（業務ルールの埋め込み /
  一件ずつの反復 / 検証のない外部化）を追加
- 判断材料を `references/data-representation.md` に置いた。「分岐が多いから表にする」ではなく
  **変化するから表にする**という切り分けを示す
- 言語固有の手段は `references/lang-<言語>.md` に 1 言語 1 ファイルで置き、`SKILL.md` が対象
  言語のファイルだけを読ませる

## [ndf 7.0.0] - 2026-08-14

### 変更（互換性を壊す）

- **ブラウザ自動テストの 4 Skill を `playwright-kit` プラグインへ分離した。** Skill 名は
  変わらず、変わるのはプラグイン接頭辞だけ。利用には `playwright-kit` の導入が要る
- Skill の `description` を圧縮した。挙動は変わらない。1 個あたり平均 237 → 148 文字、
  Claude Code 初期一覧の合計 7,772 → 4,990 文字、frontmatter 合計 13,017 → 7,578 文字
- トリガ語の書式を `Triggers: 'a', 'b'` から `Use when …（a・b）` へ変更（旧書式は廃止）

`allowed-tools` は削っていない。これは利用制限ではなく事前承認（確認プロンプトのスキップ）で、
外すと手順のたびに承認を求められる。

## [ndf 6.1.0] - 2026-08-14

### 追加

- 開発方法論の Skill を 5 個追加した（`development-workflow` / `requirements-design` /
  `tdd-cycle` / `safe-refactoring`（現 `refactoring`）/ `quality-gates`）
- 変更を 4 モード（`light` / `standard` / `architecture` / `legacy-refactor`）へ分類し、必要な
  工程だけへ振り分ける。**判定基準を持つのは `development-workflow` の 1 箇所だけである**
- `upstream-skills.lock.yaml`。Skill の設計で参照した外部リポジトリと固定コミットを記録する

### 変更

- 既存 6 Skill をこのレイヤーへ接続した。`implementation-plan` は受け入れ条件・不変条件・
  互換性・切り戻し手順をプラン書式へ追加、`problem-solving` は修正前の再現テストを必須化、
  `pr-review` は仕様適合とコード品質の二段構成へ再編

## [ndf 6.0.0] - 2026-08-12

### 変更（互換性を壊す）

- **`review` を `pr-review` へ改名した**（#83）。引数と挙動は変わらない。`review` は
  `code-review` / `security-review` / `cross-review` の末尾要素で、候補に埋もれる

### 追加

- Skill の命名規約に「ランタイム組み込みや主要プラグインの Skill 名の末尾要素にしない」を
  追加し、`scripts/check-skill-frontmatter.py` が既知の外部 Skill 名との衝突を警告する

### 削除

- v5.0.0 で載せた旧コマンド名の対応表（`ndf-policies` から）

## [ndf 5.0.0] - 2026-08-08

### 変更（互換性を壊す）

- **Skill を利用実績にもとづいて棚卸し、49 個から 29 個へ整理した。** 旧コマンド名から新
  コマンド名への対応表は `ndf-policies` に 1 リリース分だけ載せた
- 統合（-11）: `review-branch` → `review --branch`、`review-pr-comments` /
  `resolve-pr-comments` → `fix`、`clean` / `sync-main` → `merged`、`branch-fix-strategy` →
  `cherry-pick-pr`、`codex` / `gemini` → `external-ai`、ブラウザ自動テスト 9 個 → 4 個
- Kiro CLI のエージェント名が `default` → `ndf` に変わった。`install.sh` の再実行が要る

### 削除

- 起動実績がなく、現在のモデルの標準能力か汎用コマンドで足りる Skill 9 個。移行先を用意せず
  消したのは 8 件

### 変更

- `merged` / `pr` / `pr-tests` / `review` から明示指示専用の設定を外し、自然文で発動するように
  した。取り消しの難しい手順の前には対象を提示して確認を取る
- 発動判定に必要な情報を `description` へ集約し、`check-skill-frontmatter.py` で検査する
- Kiro CLI に `--set-default` と `--scope` を追加し、常時指示を `.kiro/steering/` へ移した

## [ndf 4.20.1] - 2026-08-06

### 修正

- Kiro CLI 版のエージェント定義に `tools` を宣言した。未宣言のままでは Kiro CLI がツールを
  1 つも持たないエージェントとして読み込み、Skill が `SKILL.md` を読むことも git / gh を
  実行することもできなかった

### 追加

- runtime smoke test に、生成された Kiro エージェント定義が `tools` を宣言しているかの検査

## [ndf 4.20.0] - 2026-07-21

### 変更

- `markdown-writing` を、体裁ルールから**第三者可読性のルール**へ拡張した。適用対象に仕様書・
  Pull Request 本文・調査レポート・レビューコメントを追加

### 追加

- 説明文に内部識別子やローカル略語を持ち込まないルール、検討過程の痕跡と変更履歴を本文に
  残さないルール、否定的な結論にエビデンスを必須とするルール、個人情報・認証情報を含めない
  ルール
- 書き終えた後の grep セルフチェックとチェックリスト

## [ndf 4.19.0] - 2026-06-29

### 追加

- `plan-to-spec` Skill。実装完了後の計画を `docs/` 配下の確定仕様書へ移動・書き直し・
  レビューする標準の流れを定義した
- 完了報告のテンプレート。元の計画・確定仕様書・レビュー結果・検証内容を一貫した形式で
  報告できる

**v4.19.0 より前の版は記録していない。** 変更点は git の履歴と Pull Request にある。
