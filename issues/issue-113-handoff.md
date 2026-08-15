# Issue 113: cross-refactoring 引き継ぎメモ

このブランチ（`claude/cross-refactoring-loop-skill-zmnhbx`）は **設計と事前調査のみ**で、
実装コードは 1 行も入っていない。次に着手する人が読む順序と、引き継ぎ時点で決まっている
こと・決まっていないことをまとめる。

## 1. 成果物

| ファイル | 内容 |
| --- | --- |
| [issue-113-cross-refactoring-loop.md](issue-113-cross-refactoring-loop.md) | 実施計画の本体。設計方針 / state スキーマ / 12 タスクの分解 / 受け入れ条件 |
| [issue-113-task3-cli-verification.md](issue-113-task3-cli-verification.md) | Task 3（CLI 非対話実行）の検証記録。実測値と出典、未確認項目のチェックリスト |
| 本ファイル | 進捗と引き継ぎ |

読む順序は **本ファイル → 実施計画 → 検証記録**。実施計画は長いので、実装に入る前に
§2（ランタイム輪番）・§5（ランタイム別の固有対応）・§6（bash 骨組み）だけ先に読めばよい。

## 2. この Skill が何をするものか（3 行）

- codex / gemini / kiro / claude のうち**ホストを除いた 3 CLI** にリファクタリング箇所を提案させる
- 1 提案ラウンドごとに **実装 1 : レビュー 2** で輪番を組み、採用 item をまとめて適用・
  まとめてレビューし、指摘が尽きるまで直す。**実装は gemini を除く 3 者（ホストを含む）から
  選び、ホストが担当する場合も CLI プロセスとして起動する**
- レビューが収束したら提案フェーズへ戻り、新しい提案が出なくなったら完了

## 3. 進捗

### 完了

- [x] 設計の確定（二重ループ / ランタイム輪番 / worktree 構成 / 終了条件 / state スキーマ）
- [x] 12 タスクへの分解と受け入れ条件の定義
- [x] **Task 3 のうち Claude CLI 部分**（実測で起動形式・完了検知・root 制約を確定）
- [x] **Task 3 のうち Kiro 部分の実機検証**（kiro-cli 2.18.0 / 2026-08-15。チェックリスト 6 項目すべて）
- [x] git worktree の同一ブランチ制約の実測
- [x] frontmatter 予算の実測（残余 588 文字）

### 未着手

- [ ] Task 3 の残り: `cli-kiro.md` / `cli-claude.md` の書き起こし（**実装のブロッカーではない**）
- [ ] Task 1〜2, 4〜12（実装・テスト・配布物同期）

## 4. 次にやること（この順序で）

### 4-1. Task 3 の実機確認は完了（2026-08-15）

kiro-cli 2.18.0 で [検証記録 §4](issue-113-task3-cli-verification.md) の 6 項目をすべて
実測し、**未解決は残っていない**。設計に効いた結果は次の 3 つ。

1. **`--no-interactive` と trust フラグは併用でき、シェル実行もファイル編集も通る**
   → 縮退設計（`impl_capable` から kiro を外す案）は**不要**になった
2. **承認は `--trust-all-tools` で全許可する**（利用者の判断で確定）
   → 起動形式は `cat prompt.md | kiro-cli chat --no-interactive --trust-all-tools`。
   `--trust-tools` の絞り込みは実効性こそあるが、シェルを許可した時点で迂回できるため
   防御力を持たず、綴り違いの事故リスクだけが残る（ツール名は内部名
   `execute_bash` / `fs_read` / `fs_write`、表示名 `shell` / `write` も可）
3. **承認漏れはハングではなく「拒否 + exit 0」**（#7483 は 2.18.0 で再現せず）
   → 検知は stderr のパターン照合。**kiro の終了コードは完了判定に使えない**

残るのは `cli-kiro.md` / `cli-claude.md` の書き起こしだけで、他タスクを止めない。

### 4-2. Task 9（`monitor.py` の汎用化）を先に片付ける

Task 1〜8 のどれもが `monitor.py` を呼ぶ。既存 Skill（cross-review）を壊さないことが
最重要なので、**既存テストを 1 つも変更せずに通す**ことを完了条件にして先に済ませる。

### 4-3. Task 1 → 2 → 4 → 5 → 6 → 7 → 8 の順で実装

state（Task 1）と worktree（Task 2）が無いと他が動かない。以降は
提案 → 適用 → レビュー → 修正 → 収束判定 の実行順と同じ。

## 5. 引き継ぎ時点で確定していること

判断の経緯も含めて残す。**同じ検討を繰り返さないための記録**。

| 決定 | 理由 |
| --- | --- |
| **提案・レビューの母集合は「全ランタイム − ホスト」の 3 つ** | gemini がホストになり得ないため、どのホストでも必ず 3 つ揃う。輪番の式が全ホストで同一になる |
| **実装の母集合は「全ランタイム − gemini」の 3 つ**（ホストを含む） | gemini は NDF Skill を持たず、`refactoring` Skill の手順を踏む適用には向かない。ホスト非依存で常に `["claude", "codex", "kiro"]` になる |
| ホストは提案・レビューに参加せず、**実装にだけ参加する** | 提案とレビューから外れていれば「実装者と評価者が別モデル」は保たれる。実装は CLI 駆動なのでホストの context も汚れない |
| **impl がホストと同一ランタイムでも CLI プロセスとして起動する** | Agent tool を使うとホストの context に diff が載り、独立性が崩れる。`claude -p` を別プロセスで起動すれば同じ扱いにできる |
| `impl == host` のラウンドでもレビュアーは 2 者 | impl がレビュー母集合に居ないため候補が 3 者残るが、コスト方針に合わせて 2 者へ絞る。除外する 1 者はラウンドを跨いでローテートさせる |
| **`--model <rt>=<name>` で起動時にモデルを選べる** | ランタイム／モデルの優劣を比較する用途を満たすため。4 CLI すべてにモデル指定フラグがあることを実測済み。指定値は `init` で固定し全ラウンド不変にする（途中で変えると比較が成立しない） |
| **commit に trailer で実行主体を残す** | `Item-Id` / `Round` / `Impl-Runtime` / `Impl-Model` を git trailer で書かせ、`git log --format='%(trailers:...)'` で集計できるようにする。自由文だと集計に使えない。プロンプト指示だけでは守られないので `merge-apply` が検証する |
| **ランタイムとモデルは直交するものとして集計する** | kiro は claude 系も gpt 系も選べる（実測）。ランタイムを跨いだ比較はモデルを揃えないと交絡するため、`report --metrics` はランタイム × モデルの組で出す |
| 参加者は全員 CLI（claude も `claude -p`） | ホストの Agent tool を使わないため、**ループ全体を 1 本の bash で駆動できる**。cross-review の骨組みをそのまま流用できる |
| 読み取り用 worktree は `--detach` | git が同一ブランチの二重 checkout を拒否する（実測済み） |
| 適用は直列、提案とレビューは並列 | 同一ブランチへの同時コミットは競合とレビュー単位の曖昧化を招く |
| **レビューの単位は提案ラウンド**（item 単位ではない） | item 単位だと CLI 起動回数が採用件数に比例し、1 ラウンド 33 回（採用 5 件・fix 1 回）に膨らむ。ラウンド単位なら 9 回で済む。claude 参加時は 1 起動 $0.26 の実測があり、コストが実運用に耐えない |
| 輪番の単位もラウンド（1 ラウンド 1 impl） | 1 ラウンドの適用を 1 者に集約すると、レビュアーを「impl 以外の 2 者」として機械的に決められる。item ごとに impl を替えると 1 ラウンド内で実装者が複数になり分離が成立しない |
| 収束しない item は revert して捨てる | リファクタリングは任意作業。揉める提案を PR に残すより捨てる方が安全 |
| **放棄は item 単位**（レビューはラウンド単位でも） | 未解決指摘が紐づく item だけ revert し、合意済みの item は PR に残す。そのために finding の `item_id` を必須にし、適用を item ごとに 1 手 1 コミットへ分ける |
| Kiro の承認は**フラグ**で与える（agent JSON に依存しない） | agent JSON の `allowedTools` が honored されない報告があるため（Kiro#7467）。2.18.0 の実測でフラグ経路が正しく効くことを確認済み |
| Kiro も実装フェーズの輪番に含める | `--no-interactive` + trust フラグでシェル実行とファイル編集が通ることを実測（kiro-cli 2.18.0） |
| kiro の完了判定に終了コードを使わない | ツール拒否でもシェル失敗でも exit 0 を返すため（実測）。stderr のパターン照合と result.json で判定する |
| kiro の承認は `--trust-all-tools`（絞り込まない） | 絞り込んでもシェル経由で迂回できるうえ、ツール名の綴り違いが WARNING のみで素通りする。防御は worktree 隔離に一本化する |
| claude の権限は `acceptEdits` + `--allowed-tools` | `bypassPermissions` は root 実行で拒否される（実測済み）。CI / コンテナは root が多い |
| `monitor.py` は複製せず汎用化 | 多軸完了判定は運用で作り込まれた資産。複製すると片方だけ直る事故が起きる |
| PR ローテーションは v1 では作らない | 件数上限で総量を抑える方針を先に検証する（最小限のコード実装） |
| 配布は 3 ランタイムすべて | Agent tool 依存が消えて Skill がランタイム中立になったため |

### 設計変更の履歴（初版から変わった点）

1. **参加者に claude を含める案 → ホストを除く 3 者の輪番へ**
   初版は「codex / gemini / claude が参加、claude は Agent サブエージェント」だった。
   利用者の指摘で「ホストを除いた 3 者」へ変更した結果、Agent tool 依存が消え、
   独自に作る予定だった ACTION 状態機械が不要になった（**新規機構が 1 つ減った**）。
2. **Kiro 専用 agent JSON の生成 → 廃止**
   `allowedTools` で事前承認する案は Kiro#7467 の報告により破棄。フラグ指定へ移行し、
   `prepare-worktrees.sh` の該当処理も削除した。
3. **kiro の承認漏れ検知: stall timeout → stderr のパターン照合**
   初版は #7483 を根拠に「承認漏れ＝無限ハング」を前提にしていた。kiro-cli 2.18.0 の
   実機検証では**ハングせず 9 秒で拒否して exit 0** になったため、検知手段を stderr の
   パターン照合へ変更した（stall timeout は別要因への保険として残す）。
4. **kiro の trust フラグ: `--trust-all-tools` で確定**
   実測では `--trust-tools` の絞り込みも機能したため一度は明示列挙を既定にしたが、
   **シェルを許可する以上どのみち迂回できる**（`echo > file` で書き込み制限を突破するのを
   実測）ため、防御力を持たないまま綴り違いの事故リスクだけが残ると判断し、
   利用者の判断で `--trust-all-tools` に確定した。codex の
   `--dangerously-bypass-approvals-and-sandbox` / gemini の `--skip-trust` と同じ整理になる。
   セキュリティ境界は **worktree 隔離だけ**に一本化する。
5. **三重ループ → 二重ループ（レビュー単位を item からラウンドへ）**
   初版は「提案ラウンド → item → レビュー収束」の三重ループで、item 1 件ごとに
   適用・レビュー・修正を回していた。**CLI 起動回数が採用 item 数に比例して膨らみ、
   コストが実運用に耐えない**という利用者の指摘により、1 ラウンドで採用した item を
   まとめて適用し、まとめてレビューする形へ変更した（1 ラウンド 33 起動 → 9 起動）。
   これに伴い輪番の単位もラウンドになり（`impl = RUNTIMES[outer_round % 3]`）、
   `items[]` から `impl` / `reviewers` / `fix_rounds` / `reviews` が `rounds[]` へ移った。
   `next-item` サブコマンドは不要になり、`abandon-item` は `abandon-items` になった。

6. **ホストを実装に参加させ、gemini を実装から外す**
   初版は「ホストはどのフェーズにも参加しない / 参加者 3 者が実装も担当する」だった。
   利用者の判断で、**ホストは impl としてなら参加してよい**（ただし Agent 駆動ではなく
   CLI 駆動）、**gemini は NDF Skill を持たないので impl から外す**へ変更した。
   結果として母集合が役割ごとに分かれ、`runtimes`（提案・レビュー = 全 − ホスト）と
   `impl_capable`（実装 = 全 − gemini）は別物になった。`impl == host` のラウンドでは
   レビュー候補が 3 者残るため、2 者へ絞る規則を §2 に置いた。

7. **ランタイム／モデルの計測を要件に追加**
   利用者の要望で「どのランタイムのどのモデルが実装者として優秀か」を測れるようにした。
   起動時の `--model <rt>=<name>`、commit trailer と PR への実行主体の明記、
   `report --metrics` による集計を計画 §10 に追加した。
   調査の過程で **kiro がランタイムとモデルの直交する組み合わせを提供する**（claude 系も
   gpt 系も選べる）ことが分かり、集計単位を「ランタイム × モデル」にした。

## 6. 落とし穴（実装前に必ず読む）

- **kiro の既定モデルは `auto`。** 「タスクに応じて最適なモデルを選ぶ」ため、
  **既定のまま計測すると何が動いたか分からない**。比較目的の実行では
  `--model kiro=<name>` を必ず明示する
- **モデル比較の数値を鵜呑みにしない。** item の難易度・提案との相性・レビュアーの厳しさで
  簡単に揺れる。厳密に比べるなら同じ scope で `--model` だけ変えて複数回走らせる
- **`runtimes` と `impl_capable` を同一視しない。** 前者はホストを除いた 3 者
  （提案・レビュー）、後者は gemini を除いた 3 者（実装）で、**重なるが一致しない**。
  「参加者リストにホストが含まれたら失敗」の検査は `runtimes` にだけ掛ける
- **impl がホストと同じランタイムでも、Agent tool を使わず CLI を起動する。**
  `launch-cli.sh` はランタイム名だけで分岐し、ホストか否かを見てはいけない

- **`kiro-cli agent set-default` を呼んではいけない。** 既定エージェントは
  `~/.local/share/kiro-cli/data.sqlite3` に保存される**マシン全体の設定**であり、
  利用者の既存設定を奪う。`kiro-cli agent create` も `$EDITOR`（vim）を対話的に開くため、
  非対話スクリプトから呼ぶと**タイムアウトまで止まる**（検証中に踏んだ）
- **kiro の終了コードは成否を表さない。** ツール承認漏れでも、実行したシェルコマンドが
  `exit 1` を返しても、kiro-cli は **exit 0** で終わる。`exit 1` は未認証と入力なしだけ。
  検知は stderr の `is rejected because it matches one or more rules on the denied list` /
  `WARNING: --trust-tools arg for custom tool` で行う
- **kiro の出力から ANSI エスケープを除去してからパースする。** `NO_COLOR=1` も
  `TERM=dumb` も非 TTY も効かず、色コードが必ず混ざる
- **kiro で `--trust-tools` を使うなら綴りに注意。** `--trust-tools=bogus` は WARNING を
  出して exit 0。何も信頼しない状態で全ツールが拒否されるので、「モデルが何もせず正常終了
  した」ように見える。**既定は `--trust-all-tools` なのでこの経路には入らない**が、
  絞り込みへ戻す変更をするときに必ず踏む
- **`--trust-all-tools` は worktree 隔離が前提。** 参加 CLI の cwd を worktree に固定し、
  ホストのリポジトリ本体を渡さないこと。書き込みを許すのは `work/` だけで、
  他は `--detach` にする
- **`claude --permission-mode bypassPermissions` は root で必ず失敗する。** ローカルの
  非 root 環境でだけ動作確認すると、CI / コンテナで初めて詰まる
- **frontmatter 予算の残余は 588 文字しかない**（上限 11200 / 現在 10612）。
  cross-review の frontmatter は 407 文字。新 Skill + `external-ai` の description 拡張で
  ほぼ使い切るため、`argument-hint` を短く保つ。超えたら `FRONTMATTER_TOTAL_MAX` の
  見直しか既存 `description` の圧縮を同 PR で行う
- **`monitor.py` の変更で cross-review を壊さない。** 追加オプションはすべて既定値で
  現行挙動を維持し、既存テストは 1 行も変更しない
- **参加ランタイムに NDF が入っている前提を置かない。** 対象リポジトリは任意なので、
  `/ndf:*` 形式のコマンドをプロンプトに書かず、worktree 内の `refs/` を明示パスで読ませる

## 7. 判断が要る未決事項

実装者が勝手に決めず、利用者に確認した方がよいもの。

| # | 未決事項 | 選択肢 |
| --- | --- | --- |
| ~~1~~ | ~~Kiro でシェル実行が通らなかった場合の扱い~~ | **解消**。kiro-cli 2.18.0 でシェル実行・ファイル編集とも通ることを実測したため、縮退は不要 |
| ~~2~~ | ~~kiro の trust フラグをどこまで絞るか~~ | **決定済み（利用者判断）**: `--trust-all-tools` を使う。絞り込みはシェル経由で迂回できて防御力が無く、綴り違いの事故リスクだけが残るため |
| 3 | claude 参加時の実行コスト上限 | 単純な 3 ターンで $0.26。レビューをラウンド単位にして 1 ラウンド 33 起動 → 9 起動に抑えたが、最低でも 6 回は走る。上限値の既定（`--max-items-per-round` = 5 / `--max-outer-rounds` = 3）をさらに絞るか |
| 4 | 対象スコープ（`--scope`）を必須にするか | 必須にすると誤爆しないが手間が増える。現計画では必須 |

## 8. 検証コマンド

このブランチの内容（ドキュメントのみ）に対して通るもの:

```bash
python3 scripts/check-markdown-links.py
```

実装に入ったあと、PR を出す前に通すもの:

```bash
python3 scripts/check-skill-frontmatter.py
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
python3 scripts/check-markdown-links.py
claude plugin validate
```

## 9. 関連

- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/113
- 参考実装: `plugins/ndf-shared/skills/cross-review/`（骨組みの流用元）
- 手順の委譲先: `plugins/ndf-shared/skills/refactoring/`
