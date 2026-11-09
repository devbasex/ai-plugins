# #2813: supervisor が規約の文書を導入先のプラグインでなく別の checkout から読む

正は課題の本文（#2813）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

この課題の題・本文は、#2399 のマージ前の手動確認を行った AI が書いた（利用者の指示の原文は無い）。次の条件はすべて起票者の推定であり、設計承認ゲートで利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `claude -p --plugin-dir <プラグインルート>` で起動した設計のフェーズの supervisor が読む規約の文書は、すべてそのプラグインルートの下にある | 本文の受け入れ条件 1 | 受け入れ条件 1 |
| S2 | 読み込まれたプラグインの外の規約の文書を読んだことを検出する手段を設計で決める（例: 定義でプラグインルートを渡す・hook で見る） | 本文の受け入れ条件 2 | 受け入れ条件 3・4。手段の選択は設計へ渡す（未決 1） |
| S3 | 規約の文書には `agent-layers.md` と `design-template.md` を含む | 本文の「何を見つけたか」 | 用語「規約の文書」の定義 |
| S4 | 害が出るのは、読み込まれたプラグインと読んだ checkout の版が違うときである | 本文の「何を見つけたか」 | 前提 2 |

## 目的

- supervisor が worker の起動指示を、読み込まれたプラグインと同じ版の規約で組む状態にする。今は supervisor が別の checkout（開発中のリポジトリのメインディレクトリなど）から規約の文書を読むことがあり、版が違えば違う規則の起動指示が worker へ届く
- 読み込まれたプラグインの外の規約の文書を読んだとき、それを supervisor が気づける形で検出する。#2399 の読む Skill の guard は対応表の引き方と「読む Skill」の欄のパスだけを見ており、supervisor 自身が読む規約の文書の出どころは見ていない

## 前提

- 前提 1: 対象のランタイムは Claude Code だけである。supervisor（`ndf:supervisor` / `ndf:supervisor-waits`）の定義は Claude Code にだけ配られる
- 前提 2: 害は、読み込まれたプラグインと読んだ checkout の版が違うときに出る。版が同じでも、出どころが外であれば受け入れ条件 1 は不合格として扱う（版の比較で合否を変えない）
- 前提 3: 規約の文書の出どころは「読み込まれたプラグイン」に揃える。`$SCRIPTS` の解決の順（`resolve.sh` が開発中のリポジトリを先に採る規則）はこの課題では変えない。supervisor がプラグインルートをどう得るかは設計が決め、`resolve.sh` の候補 1 に頼らない形にする
- 前提 4: 起票の時点の観測（live2・live3、2026-11-09 00:20〜00:22 JST）では、作業場所は独立した git リポジトリ（`plugins/ndf` を持たない）で、supervisor は `find /work/ai-plugins/plugins/ndf/...` でメインディレクトリの規約の文書を探した（supervisor の記録の 17・27・33・39 行目）。supervisor の定義（`plugins/ndf/agents/supervisor.md`）は規約の文書を「`development-workflow` の `references/agent-layers.md`」と相対の名前で指すだけで、プラグインルートを渡していない

## 対象範囲

含む:
- supervisor（`ndf:supervisor` / `ndf:supervisor-waits`）が規約の文書を読み込まれたプラグインから読むようにすること
- 読み込まれたプラグインの外の規約の文書を supervisor が読んだときの検出
- 検出が働く範囲と働かない範囲の明記（確定仕様の「扱わないもの」に載せる）

含まない:
- worker が読む文書の出どころ（「読む Skill」の欄のパスは #2399 の guard が見る）
- conductor（メインセッション）が読む文書の出どころ
- Codex など Claude Code 以外のランタイム
- `resolve.sh` の候補の順の変更
- 規約の文書の中身の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | conductor が supervisor を起動した | conductor の Agent の呼び出し（`subagent_type` が `ndf:supervisor` / `ndf:supervisor-waits`） | 起動が失敗すれば conductor が扱う（この課題の外） | — |
| E2 | supervisor が読み込まれたプラグインのプラグインルートを得た | supervisor の定義か起動指示が渡す（手段は設計。未決 1） | 得られなければ supervisor は推測で探さず、`結果: 判断が要る` で返す | E1 |
| E3 | supervisor が規約の文書を読んだ | worker の起動指示を組む前の読み込み（定義の「その節を読んでいなければ読む」） | 外の規約の文書を読めば E4 が働く | E2 |
| E4 | 外の規約の文書の読み込みを検出した | 読み込まれたプラグインの外の規約の文書のパスへの読み込み | 検出が働かない経路（Bash の `cat`・`grep` など）は、設計が「扱わないもの」として明記するか、検出の対象に入れる（未決 2） | E3 |
| E5 | supervisor が読み込まれたプラグインの中の同じ文書を読み直した | E4 の検出の結果（読み込まれたプラグインの中の絶対パス）を受け取った | 読み直さずに worker を起動しても、外の規約の文書だけに基づく起動指示は E4 の記録から追える | E4 |
| E6 | supervisor が worker を起動した | 起動指示を組み終えた | 既存の読む Skill の guard が止める（#2399） | E3（外を読んだときは E5） |

## 用語

| 用語 | 意味 |
| --- | --- |
| 読み込まれたプラグイン | 起動したセッションに Claude Code が読み込んだ NDF。`--plugin-dir` で指したディレクトリか、導入したプラグインのキャッシュのディレクトリで、起動時の `init` の `plugins` の `ndf` の `path` に出る |
| 規約の文書 | supervisor が worker の起動指示を組むために読む、NDF の Skill の `SKILL.md` と `references/` の下の文書（`development-workflow` の `references/agent-layers.md`、`design` の `references/design-template.md` など） |
| プラグインルート | リリースした先で `scripts/` と `skills/` が並ぶディレクトリ（用語集の既存の語） |

## 受け入れ条件

- [ ] 1. 作業場所を `plugins/ndf` を持たない独立した git リポジトリにし、`claude -p --plugin-dir <プラグインルート>` で conductor に設計のフェーズの supervisor を起動させたとき、supervisor の記録（サブエージェントの `jsonl`）に残る規約の文書の読み込み（Read の `file_path`、Bash のコマンドに現れる規約の文書のパス）がすべて `<プラグインルート>` の下にある。`<プラグインルート>` とメインディレクトリの checkout（`/work/ai-plugins/plugins/ndf`）が同じマシンに両方ある状態で確かめる
- [ ] 2. supervisor が読み込まれたプラグインのプラグインルートを得られないとき、別の checkout を探して読まずに `結果: 判断が要る` で返す（理由にプラグインルートを得られなかったことを書く）
- [ ] 3. 読み込まれたプラグインの外の規約の文書を supervisor が読もうとしたとき、検出の手段が反応し、読み込まれたプラグインの中の同じ文書の絶対パスを supervisor へ返す。合成した hook の入力（外のパスへの Read）を与える単体テストで確かめる
- [ ] 4. 検出の手段と、検出が働かない経路（あれば）が設計文書と確定仕様に書かれている
- [ ] 5. 退行しない: supervisor 以外の Agent（メインセッション・`ndf:worker`・他の NDF のエージェント）の読み込みは、検出の対象にならない（止めない・記録しない）。単体テストで確かめる
- [ ] 6. 退行しない: 読み込まれたプラグインの中の規約の文書の読み込みと、規約の文書でないファイル（作業場所の文書・課題の仕様のコピーなど）の読み込みは、検出の対象にならない。単体テストで確かめる
- [ ] 7. 退行しない: 既存の読む Skill の guard のテスト（`plugins/ndf/scripts/tests/test_worker_skills_guard.py`）と全体テストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 検出を hook で行う場合、supervisor 以外の呼び出しでは呼び手の判定だけで抜け、会話の記録を読まない |
| 運用・保守性 | 検出は環境変数で切れる（既存の guard の `NDF_WORKER_SKILLS_GUARD=0` と同じ形）。切り方を確定仕様に書く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（supervisor の定義の本文と hook の判定が変わる。Skill の入出力・結果 JSON の契約は変えない） |
| データ | 無し |
| 既存の振る舞い | supervisor が外の規約の文書を読むと、検出の結果が返る。supervisor の起動の手順が 1 つ増えうる（プラグインルートを得る手順。設計で決める） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`、`python3 plugins/ndf/scripts/instructions-check.py --root .`、`claude plugin validate .` |
| 手動確認（マージ前） | 受け入れ条件 1・2: #2399 の手動確認と同じ形（作業場所を独立した git リポジトリにし、`claude -p --plugin-dir <スプリントの worktree の plugins/ndf>` で conductor から supervisor を起動）で 1 回ずつ実起動し、supervisor の記録から規約の文書の読み込みのパスを抜き出して判定する。条件 2 は、設計が決めたプラグインルートの渡し方を外した状態で起動する。記録のパスを Pull Request に残す |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`。安定版の経路（hook・エージェントの本文）であり、`plugins/ndf/scripts/experimental/` へ置かない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。エージェントの本文は `plugins/ndf/skills/AUTHORING.md` |
| テスト戦略 | 検出の判定は単体テスト（合成した hook の入力）。実際の supervisor の読み込みはマージ前の手動確認。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の読む Skill の guard のテストと全体テストの実行 |
| 確認してから行う | supervisor の定義（`supervisor.md` / `supervisor-waits.md`）の本文の変更（設計承認ゲートで人が見る） |
| 行わない | `resolve.sh` の候補の順の変更、規約の文書の中身の変更、worker・conductor の読み込みの検出 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. supervisor へプラグインルートを渡す手段（定義の本文で `${CLAUDE_PLUGIN_ROOT}` が展開されるか、起動指示の項目に足すか、hook が返すか）。エージェントの定義の本文で変数が展開されるかは未確認で、設計で実起動して確かめる | 設計（`design`） | 設計承認ゲート |
| 2. 検出の手段（PreToolUse の hook で止める・警告だけ返す・記録だけ残す）と、Bash の `cat`・`grep`・`find` で読む経路を対象に入れるか | 設計（`design`） | 設計承認ゲート |
| 3. 「規約の文書」の判定の範囲（パスに `plugins/ndf/skills/` を含むものか、別のプラグインルートの形も含むか） | 設計（`design`） | 設計承認ゲート |

## 依頼（原文）

この課題の本文（受け入れ条件を含む）は、#2399 のマージ前の手動確認の記録（`/work/ai-plugins/.ndf/loop/lproc/lap-2/sprint/6-check-state/work/manual-2399/finding.md`）から起票者が書いた。書き換えの前の本文を次に写す。

> ## 何を見つけたか
>
> Agent の 3 層で回すフェーズで、`ndf:supervisor` が規約の文書（`agent-layers.md`・`design-template.md`）を、読み込まれたプラグイン（`--plugin-dir` の導入先）でなくメインディレクトリの checkout（`/work/ai-plugins/plugins/ndf`、develop）から読んだ。導入先と版が違えば、違う版の規約で worker の起動指示が組まれうる。
>
> #2399 の guard（ce02d25a4）は「読む Skill」の lookup とパスが hook 自身のプラグインのものかだけを見るため、規約の文書の出どころは止めない。
>
> | 回 | プラグイン | supervisor が読んだ規約 |
> | --- | --- | --- |
> | live2（依頼の文だけ） | `/work/ai-plugins/.worktrees/sprint/lproc-2/plugins/ndf`（f295622db） | `/work/ai-plugins/plugins/ndf/skills/development-workflow/references/agent-layers.md` ほか |
> | live3（10 項目の形） | 同じ | 同じ |
>
> ## 由来
>
> #2399 のマージ前の手動確認（スプリント PR 2812、2026-11-09 00:20〜00:22 JST）の実起動で見つけた。lookup も初めはメインディレクトリの `stage-skills.py` で打ち、guard に止められてから打ち直した。
>
> - 記録: `/work/ai-plugins/.ndf/loop/lproc/lap-2/sprint/6-check-state/work/manual-2399/finding.md`（各回の `stream.jsonl` とサブエージェントの記録のパスを含む）
>
> ## 受け入れ条件
>
> - [ ] `claude -p --plugin-dir <導入先>` で設計のフェーズの supervisor を起動したとき、supervisor が読む規約の文書（`agent-layers.md` ほか）のパスがすべて導入先の下にある
> - [ ] 導入先の外の規約を読んだときに検出する手段（定義でプラグインの根を渡す・hook で見る、など）が設計で決まっている

（書き手: 起票者、日付: 2026-11-09）

> #2813。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-09）
