# 並行開発 バッチ 02 — 引き継ぎ

セッションのコンテキストが尽きるため、次に触る人へ状態を渡す。**作成日: 2026-08-31**

## 一言でいうと

issue #175 / #176 / #186 / #188 を並行で進め、**5 本の Pull Request が cross-review を
通って収束した**。ただし **#192 は収束後に大きな変更を 3 回加えたため、レビューを回し直す
必要がある**。マージはまだ 1 本も行っていない。

## Pull Request の状態

| PR | issue | 内容 | ベース | CI | cross-review |
| --- | --- | --- | --- | --- | --- |
| [#191](https://github.com/devbasex/ai-plugins/pull/191) | #186 | 同じコマンドの中の `cd` を書き込み先の起点へ反映 | main | 7 件 pass | **収束済み**（17 ラウンド / 31 件対応） |
| [#192](https://github.com/devbasex/ai-plugins/pull/192) | #188 | 配布の工程を新設 | main | 7 件 pass | **要・再レビュー**（下記） |
| [#194](https://github.com/devbasex/ai-plugins/pull/194) | #176 | 進行を GitHub Projects へ記録 | **#192 のブランチ** | `runtime-smoke (kiro)` が fail | 収束済み（5 ラウンド + スイープ / 14 件） |
| [#195](https://github.com/devbasex/ai-plugins/pull/195) | — | バッチ 02 の指示書と #175 の検証記録 | main | チェック無し | 収束済み（6 ラウンド / 12 件） |
| [#200](https://github.com/devbasex/ai-plugins/pull/200) | #199 | Kiro の文脈量の上限を外す | main | 7 件 pass | 収束済み（3 ラウンド / 4 件） |

### #192 に再レビューが要る理由

cross-review が収束した後、利用者の指摘で **3 回の追加変更**を入れた。これらはレビューを
受けていない。

| commit | 内容 |
| --- | --- |
| `60ee3f6` | `release/SKILL.md` がプラグインの配布だけを前提にしていたのを、Web アプリ / API / フロントエンド / デスクトップ / モバイルへ広げた。`references/distribution-forms.md` を新設 |
| `ec84306` | **配布の段階**（検証 / 本番）を分け、**本番への配布に承認を必須**にした。`development-workflow` が自動で進めてよいのは検証への配布まで |
| `3020e41` | `AGENTS.md` へ「版の付け方と開発版の配布」を定めた。`docs/plugin-development-guide.md` の危険な手順を削除 |

**次にすること: `/ndf:cross-review 192` を回し直す。**

### #194 の CI が落ちている理由

`runtime-smoke (kiro)` が context 予算超過で落ちる。**#200 をマージし、#194 のベースを
`main` へ切り替えれば通る。** 原因と対処は #199 / #200 にある。

## マージの順序

```
main → #200 → #192 → #194（ベースを main へ切替）
```

- **#191 と #195 は他と重ならないのでいつでも入れられる**（触るファイルが独立していることを確認済み）
- #194 は #192 のブランチをベースにしているため、#192 のマージ後に `gh pr edit 194 --base main` が要る

**マージは利用者の承認を得てから行う。** この引き継ぎの時点で承認は得ていない。

## 起票した issue（5 件）

| issue | 内容 | 状態 |
| --- | --- | --- |
| [#193](https://github.com/devbasex/ai-plugins/issues/193) | `worktree` の手順が参照する `$NDF_SCRIPTS` が未定義。書かれたとおりに実行すると失敗する | 未着手 |
| [#196](https://github.com/devbasex/ai-plugins/issues/196) | **cross-review がタイムアウトを収束と誤判定する。** このセッションで 4 回踏み、回し直すたびに未対応の指摘が出た | 未着手 |
| [#197](https://github.com/devbasex/ai-plugins/issues/197) | `wt_extract_write_target` がファイル記述子の番号を書き込み先として拾う（`main` にもある先行の不具合） | 未着手 |
| [#199](https://github.com/devbasex/ai-plugins/issues/199) | Kiro の context 予算超過 | **#200 で対応済み。マージ時にクローズ** |
| [#201](https://github.com/devbasex/ai-plugins/issues/201) | 関数定義の本体・`case` のフォールスルー・前置リダイレクトで `cd` を取り違える。構文解析器が要る | 未着手 |

## #175 の扱い

**完了している。** リリース後テストを 3 ランタイムで実施し、受け入れ条件 15 件すべてを
導入側で確認した（合格）。記録は `issues/issue-175-release-verification-retrospective.md`
にあり、#195 に含まれる。**GitHub の issue は開いたままなので、#195 のマージ後に閉じてよい。**

## 主ディレクトリの未コミットの変更

`issues/` の 4 件はすべて **#195 に含まれている**。主ディレクトリ側は複製なので、#195 の
マージ後に `git checkout -- issues/` で捨ててよい。

```
 M issues/issue-175-release-verification-retrospective.md
?? issues/issue-176-github-projects.md
?? issues/issue-188-release-step.md
?? issues/parallel-batch-02/
```

## 作業ツリー

| パス | ブランチ | 用途 |
| --- | --- | --- |
| `.worktrees/fix/issue-186-relative-write-target` | #191 | **古い**（`91687b7`）。リモートは `b85a90c` |
| `.worktrees/feature/issue-188-release-step` | #192 | 最新。ここで作業した |
| `.worktrees/feature/issue-176-github-projects` | #194 | 古い |
| `.worktrees/docs/parallel-batch-02` | #195 | 最新 |
| `.worktrees/fix/kiro-context-budget` | #200 | 最新 |
| `/tmp/ndf-worktrees/devbasex--ai-plugins/pr*` | — | cross-review が作った detached HEAD の作業ツリー。デバッグ用の残骸が多数ある |

**`/tmp` 側の作業ツリーには、レビュー用 CLI が残したファイルが多数ある。** `git add -A` を
使うと PR へ混入する（実際に 49 個を混入させ、レビューに指摘されて `cb294ba` で除去した）。
**コミットするファイルは明示的に指定する。**

## 環境について（重要）

このセッションで**利用者の Claude Code の設定を壊し、復旧した**。

- `claude plugin marketplace add <ローカルパス>` を試したところ、`marketplace.json` の `name`
  が同じであるため、`--scope local` を指定しても**グローバルの取得元が上書きされた**
- 続けて `marketplace remove` したところ、**clone と導入記録も消えた**
- 復旧済み。`ai-plugins` は git の取得元へ戻し、`ndf` 9.4.0 / `playwright-kit` 2.0.1 /
  `mcp-serena` 2.0.0 / `mcp-dbhub` 2.0.0 を入れ直した

**この手順は `docs/plugin-development-guide.md` が案内していたもので、#192 の `3020e41` で
削除した。** 同じことを繰り返さないこと。

あわせて、リリース後テストの一環で **NDF プラグインを v9.3.0 → v9.4.0 へ更新した**
（Claude Code / Codex とも）。これは意図した操作である。

## 実機で確かめた配布の仕組み

`AGENTS.md` の「版の付け方と開発版の配布」の根拠。**再調査の手間を省くため残す。**

| 項目 | 結果 |
| --- | --- |
| **`claude plugin marketplace add` の ref 指定** | **できる。** `owner/repo@ref` / `git-url#ref`。`--help` には出ないが[公式ドキュメント](https://code.claude.com/docs/en/plugin-marketplaces)に記載があり、実機でも確認した |
| `claude plugin install` | `--config` / `--scope` / `--yes` のみ。**版の指定は無い**（確定） |
| Codex の `codex plugin marketplace add` | `--ref` を持つ（`owner/repo[@ref]` も可） |
| Kiro | ref に相当する手段が無い。ローカルからの導入が唯一の公式手段 |
| 同名でローカル追加 | 取得元を**置き換える**。これは**仕様**（"Each user can register only one marketplace per name"）。登録の鍵は `marketplace.json` の `name` |
| 別名（`ai-plugins-dev`）で追加 | 共存でき、両方の版を導入できる。**ただし両方が有効になり、どちらが採用されるか不定になるため検証手段としては使えない** |
| 接尾辞付きの版（`9.5.0-dev.1`） | 検査は通る。版数を書く 4 箇所すべてを揃える必要がある |
| `claude --plugin-dir <パス>` | 動く（終了コード 0 で応答も返る）。取得元を書き換えない |
| `bash plugins/ndf/dev.kiro/install.sh --project <ディレクトリ>` | 動く。取得元を書き換えない |
| サードパーティのマーケットプレイス | **自動更新が既定で無効。** `main` へ出した版が即座に全利用者へ届くわけではない |

## 調査で分かったこと（`AGENTS.md` の根拠。前提が 2 つ覆った）

セッション終盤に「エージェント向けマーケットプレイスの開発版／本番版の区別」を調査した。
**当初の前提のうち 2 つが誤りだった。** `AGENTS.md` は `69f6b5a` で訂正済み。

### 覆った前提

| 私が最初に書いたこと | 事実 |
| --- | --- |
| ref を指定して別の版を配る手段は無い | **できる**（上の表） |
| ローカル追加の上書きは想定外の挙動 | **仕様**。名前ごとに 1 つしか登録できない |

### 公式に用意された手段

Claude Code の公式ドキュメントに **「Set up release channels」** の節がある。同一リポジトリの
別 ref を指す **2 つのマーケットプレイス**で stable と latest を分ける形で、マネージド設定で
利用者グループごとに割り当てる。**両チャネルが異なる版数へ解決される必要がある**（同じだと
更新が飛ばされる）という制約が明記されている。

### 接尾辞の意味

**接尾辞は人が読むための印にすぎない。** Claude Code の直接インストール経路は版数を
**キャッシュキーとしての文字列一致**でしか見ず、`-dev` や `-rc` を prerelease として扱わない。
Codex と Kiro も同様で、Agent Plugins Specification には解釈の規定が無い（仕様本文に
`prerelease` / `channel` は 0 件）。semver の順序で除外されるのは、プラグイン間の依存解決
（`dependencies`）の経路だけである。

**それでも接尾辞を付ける理由は変わらない。** 入れたくない利用者が版数を見て判断できるように
するためである。

### 採り得る選択肢（採否は未決）

| 案 | 内容 | 弱点 |
| --- | --- | --- |
| A | dev ブランチに別名の `marketplace.json` を置く（公式の release channel 方式） | 同名プラグインが両方有効になる。プラグイン名も分けるか、片方を無効にする案内が要る。検査が 2 系統になる |
| B | dev チャネルだけ `version` を省いて commit-SHA 版にする | 公式が「開発中の内部プラグイン」に推奨。ただし利用者から見て版が SHA になり読めない |
| C | git タグ + `claude plugin tag` で版を固定し、安定版は ref をタグへ固定 | **`main` へのマージと配布が分離できる**（現在の運用の核心を直接解く）。タグ運用の手順が増える |
| D | 開発版は配らず `--plugin-dir` / installer で回す | 維持コストが無い。ただし**開発版を他人へ配れない** |

**選択肢 C が、現在の「main = 即配布」という構図を最も直接に解く。** ただし採否は決めていない。

### 参考にならないと分かったもの

- **偶数／奇数のマイナー版**は、Linux（2.6 で廃止）・GNOME（40 で廃止）・Node.js（v27 で廃止）が
  いずれも semver プレリリースへ移行済み。現役は Perl と GStreamer。この慣習は
  **プラットフォームが prerelease を表現できないときの代用**で、ref によるチャネル分離を
  持つ Claude Code では番号にチャネルを担わせる理由が無い
- VS Code が偶数／奇数を推奨するのは、Marketplace が semver プレリリースを扱えないためである

## 次にすること（順に）

1. **`/ndf:cross-review 192` を回し直す**（収束後に 3 回の追加変更を入れたため）
2. 利用者の承認を得て `main` → #200 → #192 → #194 の順にマージ
3. #194 のベースを `main` へ切り替える（`gh pr edit 194 --base main`）
4. #191 と #195 をマージ
5. **配布**（`/ndf:release`）— #192 で定めた工程を最初に適用する。バッチ 02 の
   まとまり単位で版を 1 度だけ上げる
6. リリース後テストと振り返り
7. 主ディレクトリの `issues/` の複製を捨て、作業ツリーを片付ける（`/ndf:merged`）

## 参照

- [00-overview.md](00-overview.md) — このバッチの全体指示
- [../issue-188-release-step.md](../issue-188-release-step.md) — #192 の実装プラン
- [../issue-176-github-projects.md](../issue-176-github-projects.md) — #194 の実装プラン
- [../issue-175-release-verification-retrospective.md](../issue-175-release-verification-retrospective.md) — #175 の計画とリリース後テストの記録
- 盤面: https://github.com/orgs/devbasex/projects/1
