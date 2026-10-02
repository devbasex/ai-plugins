# #1597: LLM へ入力を渡す前に、そのアカウントが入力を学習に使わない設定かを確かめるエントリポイントを足す

正は課題の本文（#1597）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を足すか
>
> LLM に入力を渡す前に、その LLM のアカウントが入力を学習に使わない設定になっているかを確かめるエントリポイント（スクリプト）を足す。結果を 1 行の JSON で返し、利用側のリポジトリが「確かめた LLM にだけ渡す」という規則を、プランの段や hook から機械で守れるようにする。
>
> ## なぜ要るか
>
> 利用規約やデータの利用条件が「AI の学習データとしての利用」を禁じるデータ（外部サイトの API で取ったデータなど）を扱うリポジトリでは、入力を学習に使わない設定を確かめた LLM にだけ渡す、という規則を置くことがある。今は確かめる手段が人の手（設定画面を開く）しか無く、プランの段や hook が規則を守っているかを判定できない。
>
> ## Claude での確かめ方（確かめた事実）
>
> - Claude Code と同じ OAuth の認証で `GET https://api.anthropic.com/api/oauth/account/settings`（ヘッダ `anthropic-beta: oauth-2025-04-20`）を読むと、`grove_enabled` と `grove_updated_at` が返る
> - Claude Code 2.1.287 の `/privacy-settings` は、この値を読み書きする（切り替えは `PATCH /api/oauth/account/settings {grove_enabled}`）。`grove_enabled` が設定画面の「Help improve our AI models」で、`false` なら入力を学習に使わない
> - `/api/claude_code_grove` は 403 を返した（使えない）
> - 確かめた環境: Claude Max（個人向けのプラン）
>
> ## 求める形
>
> - 例: `python3 "$SCRIPTS/training-optout.py" check --runtime claude`
> - 返す JSON の例: `{"tool": "training-optout", "status": "ok", "items": [{"runtime": "claude", "training": false, "source": "oauth/account/settings.grove_enabled", "updated_at": "..."}]}`
> - 確かめられない（認証が無い・応答の形が違う・403）ときは `status: "stopped"` と理由を返し、「学習に使わない」と扱わない
> - 認証情報とアカウントの ID を出力とログに出さない
> - 他の CLI（codex / kiro / agy）は、確かめる手段があるものから足す。手段が無いものは `unsupported` を返す
>
> ## 受け入れ条件の案
>
> - [ ] claude で `grove_enabled` の true / false / 読めない、の 3 つが区別されて返る（応答はテストで差し替える）
> - [ ] 出力に認証情報とアカウントの ID が現れない
> - [ ] 利用側のリポジトリが hook かプランから呼ぶ例が SKILL か README にある
>
> 🤖 Generated with [Claude Code](https://claude.com/claude-code)
>
> https://claude.ai/code/session_013H578Sc5MV4Sk47MuZrmAN
>
>
> ## 追加の要求（利用者 2026-10-02）: NDF を使うときは Off にする
>
> 確かめるだけでなく、**NDF を使うとき（Claude Code で NDF が読み込まれたセッション）は、そのアカウントの「Help improve our AI models」を Off（`grove_enabled: false`）にする。**
>
> - 確かめる部分は実験版で先に入れた（`plugins/ndf/scripts/experimental/training-optout.py`、PR #1606）。Off にする振る舞いは既定で働くため、安定版の経路（設計 → 承認ゲート 1 → 実装）で本体へ移す
> - 書き換えの実測（2026-10-02、Claude Max）: `PATCH https://api.anthropic.com/api/oauth/account/settings`、本文 `{"grove_enabled": false}`、ヘッダは GET と同じ → `202`・本文 `null`。既に false のときは `grove_updated_at` が変わらない（同じ値は書き換えにならない）。true → false の切り替えは、実物のアカウントを true へ戻す必要があるため未確認
> - 設計で決めること: 起動の契機（SessionStart の hook など）・利用者へどう知らせるか・Off にしたくない利用者が外す手段・失敗したとき（認証が無い・401・ネットワーク）にセッションを止めないこと・Claude Code 以外のランタイムの扱い・`claude -p` の worker やアカウントの切り替え（relay の `CLAUDE_CONFIG_DIR`）でも効くか
>
> ### 追加の受け入れ条件の案
>
> - [ ] `grove_enabled` が true のアカウントで NDF のセッションを始めると false になり、そのことが利用者に分かる
> - [ ] 既に false なら書き換えない（PATCH を送らない）
> - [ ] 書き換えに失敗してもセッションは止まらず、失敗が利用者に分かる
> - [ ] 利用者が Off にする動作を外す手段がある
> - [ ] トークンとアカウントの ID が出力とログに現れない
>

## 目的

- 利用者が NDF を使う Claude Code のセッションを始めると、そのアカウントの学習の設定が Off（`grove_enabled: false`）になっている状態にする。利用者は設定画面を開かなくてよい
- 利用側のリポジトリ（利用条件で AI の学習への利用を禁じるデータを扱うもの）が「学習の設定を確かめた LLM にだけ入力を渡す」という規則を、プランや hook から機械で守れるようにする
- 確かめられないとき・書き換えに失敗したときは「学習に使わない」と扱わず、セッションも止めない

## 前提

- 前提 1: 学習の設定を読み書きする手段は、Claude Code と同じ OAuth の認証で呼ぶ `GET` / `PATCH https://api.anthropic.com/api/oauth/account/settings`（ヘッダ `anthropic-beta: oauth-2025-04-20`）だけとする。公開の API ではないため、応答の形が変わったら「確かめられない」として扱う（原文の「確かめた事実」と 2026-10-02 の実測）
- 前提 2: 学習の設定の書き換えは、利用者が 2026-10-02 に要求した振る舞いである。既定で働くため、安定版の経路（設計 → 承認ゲート 1 → 実装）で本体へ入れる（`AGENTS.md` の「安定版と実験版」）
- 前提 3: 学習の設定の確認（`check`）も安定版へ移す。書き換えの hook が確認と同じ読み取りを使い、既定で動くものから実験版を参照できないためである。実験版の `training-optout.py`（PR #1606）は本体へ移した時点で消し、台帳の「行き先」に移した先を書く
- 前提 4: PR #1606 は 2026-10-02 時点でマージされていない。実装の時点でもマージされていなければ、確認の振る舞いもこの課題の実装に含める（受け入れ条件は同じ）
- 前提 5: 書き換えの起動の契機は、Claude Code の SessionStart（`startup`）の hook とする。`claude -p` で起動する worker も同じ契機で動く。アカウントの切り替え（`CLAUDE_CONFIG_DIR` がアカウントの設定ディレクトリを指す起動）でも、その起動の認証で読み書きする
- 前提 6: 認証が OAuth でない起動（API キー・Bedrock・Vertex など、従量の接続）には学習の設定が無い。この起動では読み書きせず、確認の結果は「確かめられない」とする（「学習に使わない」と扱わない）
- 前提 7: 学習の設定を Off にすると、Anthropic 側の会話の保存期間も変わる（一般向けの規約で、学習に使う設定の保存期間と使わない設定の保存期間が違う）。利用者の要求はこの変化を含むものとして扱い、設計の承認で人が確かめる
- 前提 8: true → false の切り替えは実物のアカウントで未確認（原文）。false のときに `PATCH` が `202`・本文 `null` を返し、`grove_updated_at` が変わらないことだけを確かめてある

## 対象範囲

含む:
- 学習の設定の確認（`check`）を安定版のエントリポイントとして置く（claude は読み取り、codex / kiro / agy は `unsupported`）
- Claude Code の SessionStart の hook で、学習の設定が true なら false へ書き換え、利用者へ知らせる
- 利用者が書き換えの動作を外す手段
- 失敗（認証が無い・401・403・ネットワーク・応答の形の違い）でセッションを止めないこと
- 利用側のリポジトリが hook かプランから確認を呼ぶ例（README か SKILL）
- 実験版の `training-optout.py` を消し、台帳の行き先を書く

含まない:
- codex / kiro / agy の学習の設定の書き換え（確かめる手段が無い。`unsupported` を返すだけ）
- 組織が管理するアカウント（Team / Enterprise）で、組織の設定が学習の設定を決める場合の扱い。書き換えが拒まれたら失敗として知らせるだけにする
- 学習の設定を true へ戻す操作（利用者が設定画面で行う）
- 入力を LLM へ渡す前に確認を強制する hook（利用側のリポジトリが例を見て置く）
- 期限切れのトークンの更新（Claude Code 自身が行う）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | NDF を読み込んだ Claude Code のセッションが始まった | 利用者か worker の起動（SessionStart の `startup`） | — | — |
| E2 | 書き換えの動作が外されていると分かった | 利用者が外す手段を使っている | 外す手段の値が読めない → 外されていないとして扱う | E1 |
| E3 | OAuth のトークンを読んだ | E1 の後、E2 で外されていない | トークンが無い・OAuth でない（前提 6） → E7（失敗を知らせる。従量の接続では知らせない） | E1・E2 |
| E4 | 学習の設定を読んだ | E3 | 401・403・ネットワーク・応答に真偽値が無い → E7 | E3 |
| E5 | 学習の設定が既に false だと分かった | E4 の値が false | — | E4。書き換えを送らずに終わる |
| E6 | 学習の設定を false へ書き換えた | E4 の値が true | `PATCH` が 2xx 以外・ネットワーク → E7 | E4 |
| E7 | 書き換えか読み取りに失敗したことを利用者へ知らせた | E3・E4・E6 の失敗 | 知らせの出力自体が失敗 → 何もせず終わる（セッションは続く） | — |
| E8 | 書き換えたことを利用者へ知らせた | E6 | — | E6 |
| E9 | 学習の設定の確認を返した | 利用側のプランか hook が `check` を呼んだ | 確かめられない → `status: "stopped"` と理由 | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| 学習の設定 | アカウントの「Help improve our AI models」。`account/settings` の `grove_enabled` で、true なら入力を学習に使う |
| 学習の設定の確認 | 学習の設定を読み、1 行の JSON で返すこと（`check`）。書き換えない |
| 学習の設定の書き換え | NDF のセッションの開始で、true の学習の設定を false にすること |

## 受け入れ条件

確認（`check`）:

- [ ] 応答を差し替えたテストで、`grove_enabled` が false なら `status: "ok"`・終了コード 0・`items[0].training` が false になる
- [ ] 応答を差し替えたテストで、`grove_enabled` が true なら `status: "stopped"`・`items[0].training` が true になる
- [ ] 認証が無い・401・403・ネットワークの失敗・`grove_enabled` が真偽値でない、のそれぞれで `status: "stopped"`・`training` が null・`reason` に理由が入る（「学習に使わない」と扱わない）
- [ ] `--runtime codex` / `kiro` / `agy` は `unsupported` を理由に返し、`status: "stopped"` になる
- [ ] 確認のエントリポイントが `plugins/ndf/scripts/experimental/` の外にあり、実験版の `training-optout.py` が消えて、台帳の行に行き先が書かれている

書き換え（SessionStart の hook）:

- [ ] 前提: 学習の設定が true のアカウント（応答を差し替える）
      操作: SessionStart の hook を起動する
      結果: `PATCH` が本文 `{"grove_enabled": false}` で 1 回送られ、hook の出力に「学習の設定を Off にした」旨の利用者向けの知らせが入る
- [ ] 学習の設定が既に false のアカウントでは `PATCH` を送らず、利用者向けの知らせも出さない
- [ ] 認証が無い・401・403・ネットワークの失敗・`PATCH` が 2xx 以外、のそれぞれで hook の終了コードが 0 で、hook の出力に失敗した旨の利用者向けの知らせが入る
- [ ] 認証が OAuth でない起動（前提 6）では読み書きを送らず、終了コードが 0 になる
- [ ] 利用者が書き換えの動作を外すと、`PATCH` を送らない（読み取りも送らない）
- [ ] hook の定義が SessionStart の `startup` に載り、`claude plugin validate .` が終了コード 0 で終わる

退行しないこと:

- [ ] 確認と書き換えの出力（標準出力・標準エラー・hook の出力）とログに、OAuth のトークンとアカウントの ID（`account/settings` の応答に含まれる識別子）が現れない（テストで応答とトークンに目印の文字列を入れて確かめる）
- [ ] 既存の SessionStart の hook（ensure-retention・statusline・worktree-session・relay）の振る舞いが変わらない（既存のテストが通る）

利用者への説明:

- [ ] 利用側のリポジトリが hook かプランから確認を呼ぶ例と、書き換えの動作を外す手段が、README か SKILL に書かれている

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | どの失敗でも hook は終了コード 0 で終わり、セッションの開始を止めない |
| 性能・拡張性 | hook の 1 回の実行は hook の `timeout` に収まる。読み書きの HTTP には打ち切りの時間を置き、その合計が `timeout` を超えない（値は設計で決める） |
| セキュリティ | トークンは環境変数か認証ファイルから読むだけで、書き出さない。出力とログにトークンとアカウントの ID を出さない。送り先は `api.anthropic.com` だけ |
| システム環境 | Claude Code の SessionStart の hook として動く。codex / kiro / agy では書き換えない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。確認のエントリポイント（`check`）を安定版に足す。SessionStart の hook を 1 つ足す |
| データ | 利用者のアカウントの学習の設定（Anthropic 側）を true → false へ書き換える。ローカルのファイルの形は変わらない |
| 既存の振る舞い | NDF を読み込んだ Claude Code のセッションの開始で、学習の設定の読み取り（毎回）と書き換え（true のときだけ）が起きる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（応答は差し替える。実物の API を呼ばない） |
| 静的解析 | `claude plugin validate .`・`python3 scripts/check-skill-frontmatter.py`（SKILL を変えたとき） |
| 実験版の参照 | `plugins/ndf/scripts/tests/test_experimental.py` が通る |
| 手動確認 | 利用者が実物のアカウントで、(1) 学習の設定が false のまま `claude` を起動して知らせが出ないこと、(2) 設定画面で true にしてから起動し、false に戻り知らせが出ること、を見る（前提 8 の未確認を埋める。リリース後テストで行う） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 安定版は `plugins/ndf/scripts/` と `plugins/ndf/hooks/claude.json`。実験版の置き場から既定の振る舞いを参照しない（`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | 結果の JSON は `lib/step_result.py` の形。Skill の frontmatter は `plugins/ndf/skills/AUTHORING.md` |
| テスト戦略 | 単体テストで HTTP の応答を差し替え、確認の 3 区別・書き換えの有無・失敗の経路・秘密が出ないことを見る。実物のアカウントの true → false は手動確認 |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行・`claude plugin validate .`・出力に秘密が出ないことのテスト |
| 確認してから行う | Anthropic 以外の送り先を足すこと・認証ファイルへ書くこと・SessionStart 以外の契機を足すこと |
| 行わない | トークンの更新・学習の設定を true にする書き換え・codex / kiro / agy の設定の書き換え |

## MVV との突き合わせ

判定: **進める**。ただし承認ゲート 1 は人が承認する（MVV の判定に任せない）。

| 根拠 | 理由 |
| --- | --- |
| 上位の原則 | 入力を学習に使わない設定へ寄せることは利用者のデータを守る側の変更で、利用者が要求している |
| C1 | OAuth のトークン（秘密）を読んで外部へ送る。共通原則が人の承認を必ず要するため、ゲート 1 を MVV の判定に任せない |
| C3 | 利用者のアカウントの設定（保存期間を含む。前提 7）を変える。同じく人の承認が要る |
| C6 | 「黙って書き換える」を避けるため、書き換えたら必ず知らせ、外す手段を置く（受け入れ条件） |
| Value 2 | 書き換えのたびに確認を求めない。知らせと外す手段で、利用者の手を止めずに選べる形にする |
| Value 5 | 外す手段はプロジェクトに依らない形にする（利用者のアカウント単位の設定のため） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 書き換えの動作を外す手段の形（環境変数・利用者の設定ファイルなど。プロジェクトの `.ndf/` に置くかを含む） | 設計（`design`） | 承認ゲート 1 |
| 利用者へ知らせる経路（`systemMessage`・`additionalContext`・標準出力のどれか）と文面 | 設計 | 承認ゲート 1 |
| hook の `timeout` と HTTP の打ち切りの値 | 設計 | 承認ゲート 1 |
| `PATCH` が `202` を返した後に読み直して false を確かめるか（反映の遅れの扱い） | 設計 | 承認ゲート 1 |
| macOS のキーチェーンに認証を置く環境でトークンを読む手段（読めなければ「確かめられない」になる） | 設計 | 承認ゲート 1 |
| 確認の安定版での置き場と名前（独立のスクリプトか、既存の `hook.py` の副命令か） | 設計 | 承認ゲート 1 |
| SessionStart の `resume` でも動かすか | 設計 | 承認ゲート 1 |
| 前提 7（保存期間の変化）を含めて書き換えてよいか | 利用者（承認ゲート 1） | 承認ゲート 1 |
