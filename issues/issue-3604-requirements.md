## 何が起きたか

11 系の NDF の中継（`ndf relay`。claude を包むラッパーが、カットポイントで次の claude をセグメントとして起動する）で、利用者が `ndf relay account add` で 2 つ目の claude のアカウントを登録し、`ndf relay account use <名前>` で選んでも、ラッパーが起動する子の claude は元のログインのまま動く。利用上限で別のアカウントへ替えたつもりのセッションが、上限に当たった同じアカウントで再起動する。

2026-11-19 の棚卸しで、古くなったパス・数値・関連課題の状態を今の main（2e43af7bc）に合わせた。2026-11-21 の要求の工程（設計のフェーズ、スプリント p12-lracct-r1）で、要求仕様の形へ書き直した。

## 種類

不具合（子の claude をアカウントの認証で起動することは、手順書 `relay-accounts.md` の「アカウントの設定ディレクトリが、そのアカウントで起動する claude の `CLAUDE_CONFIG_DIR` になる」が既に約束している）

## 今の姿

- ラッパーは子を起動するたびに `plugins/ndf/src/ndf/domains/relay/session_env.py` の `child_env` で環境を作る。子の中で動く `ndf plan` の worker の `claude -p` は `child_env` を通らず、子の環境をそのまま継ぐ（`adapters/claude.py` の `call_claude`）
- `child_env` は子へアカウントの名前（`NDF_CLAUDE_ACCOUNT`）だけを入れ、認証の入った設定ディレクトリ（`CLAUDE_CONFIG_DIR`）は親の値のまま渡す。子の環境の表（`plugins/ndf/contracts/domains/env.json`）でも `CLAUDE_CONFIG_DIR` の行に `child` の指定が無い
- アカウントの登録（`account_registry.py`・`account_login.py`）は 10 系と同じ保存先（`<共有の設定ディレクトリ>/ndf/accounts/<名前>/`）に、認証ファイル（`.credentials.json`）・`.claude.json`・`account.json` を置く。登録までは 11 系にある（#3569）
- 10 系の ndf は、子を起動する前にアカウントの設定ディレクトリを用意していた（`plugins/ndf/scripts/lib/claude_accounts.py`・`claude_account_dir.py`。11.0.0 の入れ替え（コミット 976513356）で無くなった）
  - 共有の設定ディレクトリの直下の項目（会話の記録 `projects`・`settings.json`・プラグインなど）へ、アカウントの設定ディレクトリから symlink を張る
  - `.claude.json` の `projects`（プロジェクトの信頼・MCP サーバーの許可）と `mcpServers` を、共有側とアカウント側で同期する（起動の前と終わり）
  - 子の環境から OAuth のトークンの変数（`CLAUDE_CODE_OAUTH_TOKEN` など）を外す
  - 認証ファイルが無い・リフレッシュトークンが空・登録と別の本人のアカウントでは起動せず、次の候補へ移る
- 手順書の約束の SSOT: [`relay-accounts.md`](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/skills/development-workflow/references/relay-accounts.md) の「アカウントの設定ディレクトリが…」の段落と子の環境の変数の表

## 事象

2026-11-19（main 2e43af7bc）にコードで確かめた。

- 子の環境の表で `NDF_CLAUDE_ACCOUNT` の `child` は `set`、`CLAUDE_CONFIG_DIR` は指定なし（親の値のまま）
- アカウントの子の目印 `NDF_SHARED_CONFIG_DIR` は `account_registry.py`・`identity.py` が読むだけで、入れる箇所が無い（`git grep -n "NDF_SHARED_CONFIG_DIR" -- plugins/ndf/src` の当たり 7 行はどれも読む側と説明）
- 2026-11-21（main d14325d76）: symlink を張る・`.claude.json` を同期する・トークンの変数を外す処理は `plugins/ndf/src` に無い（`git grep -n "symlink_to\|mcpServers\|CLAUDE_CODE_OAUTH_TOKEN" -- plugins/ndf/src/ndf/domains/relay` の当たりは、ログイン用の環境を作る `account_login.auth_env` と登録を外す `account_registry.detach` だけ）

## 期待する結果

`ndf relay account use <名前>` の後にラッパーが起動する子が、そのアカウントの認証で動き、アカウントを替えた後の `claude --resume <会話>` が前の会話を見つける。

## 再現手順

1. `plugins/ndf/bin/ndf-claude` で起動する
2. `ndf relay account add` で 2 つ目のアカウントを登録し、`ndf relay account use <名>` で選ぶ
3. ラッパーが次のセッションを起動したら、子の claude の中で `/status` を打ち、ログインのアカウントを見る（元のアカウントのまま）

## 環境

- 観測: ブランチ `sprint/lr-t2-3569`（#3569 の実装を取り込んだ後）、ndf-next（`plugins/ndf-next/`）、Claude Code
- 11 系（main 2e43af7bc・d14325d76）でコードを読んで同じ作りを確かめた。実機での再現は未確認
- Linux（コンテナを含む）。macOS の claude は認証を Keychain に置くため、アカウントの登録そのものが止まる（手順書の冒頭）

## なぜ困るか

利用上限でアカウントを切り替える運用（中継のアカウントの切り替え）が 11 系では働かない。上限に当たったセッションが同じアカウントのまま再起動し、上限が解けるまで進まない。`CLAUDE_CONFIG_DIR` だけを替えると、会話の記録がアカウントごとに分かれ、替えた後の `--resume` が会話を見つけられなくなる（今より悪くなる）。

## 目的

- ラッパーと `ndf plan` が起動する子の claude が、選んだ登録済みアカウントの認証で動き、アカウントを替えても会話・設定・プラグインがそのまま続く

## 対象範囲

含む:
- 子の環境: 登録済みアカウントで起動する子へ、アカウントの設定ディレクトリを `CLAUDE_CONFIG_DIR` として渡し、共有の設定ディレクトリを示す変数を入れ、OAuth のトークン・API キー・クラウドの接続の変数を外す
- 起動の前の用意: アカウントの設定ディレクトリから共有の設定ディレクトリの項目へ symlink を張り、`.claude.json` の共有する部分（`projects`・`mcpServers`）をそろえる
- 終わりの書き戻し: セッションの切れ目と終わりに、アカウント側で変わった共有する部分を共有側へ書き戻す
- 使えないアカウントを飛ばす: 認証ファイルが無い・リフレッシュトークンが空・登録と別の本人・会話の記録を共有できないアカウントでは起動せず、理由を 1 行出して次の候補（無ければ既定のログイン）で起動する
- アカウント側にだけある項目（`local_only`）を記録に名前で残す
- ラッパーの子と、その子の中で `ndf plan` が起動する `claude -p`（子の環境を継ぐ）の両方

含まない:
- どのアカウントへ替えるかの判断（使用率の取得・上限と認証の失敗での切り替え・指定の読み直し）。#3864 が持つ
- 手順書の、11 系に無い振る舞いの記述の直し。#3864 が持つ
- `ndf plan` の worker を API キーで動かす経路（#3864 で扱いを決める）
- macOS の Keychain の認証への対応
- 切り替えの閾値と選ぶ順の変更（#2588）

## 受け入れ条件

- [ ] 1. 前提: 登録済みアカウント `work2` があり、`ndf relay account use work2` で指定した
      操作: ラッパーが次のセグメントの子を起動する
      結果: 子の環境の `CLAUDE_CONFIG_DIR` が `<アカウントの保存先>/work2` の絶対パスで、`NDF_CLAUDE_ACCOUNT` が `work2`、`NDF_SHARED_CONFIG_DIR` が親の `CLAUDE_CONFIG_DIR`（無ければ空文字）である
- [ ] 2. 登録済みアカウントで起動する子の環境に、`CLAUDE_CODE_OAUTH_TOKEN`・`CLAUDE_CODE_OAUTH_SCOPES`・`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`・`CLAUDE_CODE_USE_FOUNDRY` が無い（親の環境にあっても）
- [ ] 3. アカウントの指定も環境変数（`NDF_CLAUDE_ACCOUNT`）も無く、既定のログインで起動する子の環境は、今と同じ（`CLAUDE_CONFIG_DIR` は親の値のまま、トークンの変数も外さない）。指定か環境変数に名前があるのに、そのアカウントの用意に落ちて既定のログインへ移った子は、`NDF_CLAUDE_ACCOUNT` を外し、ほかの変数は今と同じにする（名前が残ると実際の認証と食い違う）
- [ ] 4. 起動の前に、アカウントの設定ディレクトリの直下に、共有の設定ディレクトリの直下の項目のうちアカウント固有の項目（認証ファイル・`.claude.json`・更新の排他・NDF の記録など）を除いたものへの symlink がある。共有側に `projects` が無ければ作ってから張る
- [ ] 5. アカウントを `work1` から `work2` へ替えた後のセグメントで、`work1` で始めた会話を `claude --resume <会話の ID>` で開ける（会話の記録が共有側の `projects` の 1 か所にある）
- [ ] 6. 起動の前に、共有側の `.claude.json` の `projects` と `mcpServers` がアカウント側の `.claude.json` へ写り、アカウント側の `oauthAccount` は変わらない
- [ ] 7. セッションの切れ目とラッパーの終わりに、アカウント側で変わった `projects`・`mcpServers` の値が共有側の `.claude.json` へ書き戻る。両側で同じ値が変わったときは共有側の値を採る
- [ ] 8. 次のどれかに当たるアカウントでは子を起動せず、理由を画面に 1 行と `log.jsonl` に 1 行残し、次の候補（無ければ既定のログイン）で起動する: 認証ファイルが無い・読めない／リフレッシュトークンが空／アカウント側の `.claude.json` の本人（`oauthAccount` のメールと組織）が登録と違う／アカウント側の `.claude.json` が JSON として読めない／アカウント側の `projects` が共有側を指していない／旧形式の `.config.json` が残っている
- [ ] 9. 8 の理由のうち、リフレッシュトークンが空・本人の食い違い・旧形式の `.config.json` が残っているときは（直す操作が再登録だから）、`account.json` の `needs_relogin` が真になり、`ndf relay account list` の `state` が `再登録が要る` になる
- [ ] 10. アカウント側にだけある実体の項目（共有側に無く、symlink でなく、アカウント固有でもない）は、消さず・移さずに、名前だけが `log.jsonl` に残る
- [ ] 11. 登録済みアカウントで動く子の中で `ndf plan` が起動する worker の `claude -p` は、子と同じアカウントの設定ディレクトリ（`CLAUDE_CONFIG_DIR`）で動き、親の子と別のアカウントを選び直さない。例外として、利用上限に当たったときだけ、替え先のアカウントで同じ呼び出しを 1 回打ち直す
- [ ] 12. 起きてはいけないこと: 共有側の `.credentials.json` を読み書きしない。アカウント側の実体の項目を上書き・削除しない。トークンの値・キーの値を画面と記録に出さない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。子の環境の表（`contracts/domains/env.json`）の `CLAUDE_CONFIG_DIR`・`NDF_SHARED_CONFIG_DIR`・トークンの変数の行に、登録済みアカウントの子での扱いが足される。コマンドの引数と結果の形は変わらない |
| 重さ | 中継のアカウントの切り替えが働かない。上限に当たったセッションが止まり、利用者が端末を見るまで進まない。回避は、`/exit` してから `CLAUDE_CONFIG_DIR=<アカウントの設定ディレクトリ> claude` を手で打つこと（会話の記録は共有されない） |
| データ | アカウントの設定ディレクトリに symlink と同期のバックアップ（`.ndf-shared-base.json`）が増える。保存先の形は 10 系と同じで、移行は要らない |
| 既存の振る舞い | 登録済みアカウントを指定したときの子の起動が変わる。指定も登録も無い利用者の起動は変わらない |

## 用語

| 用語 | 意味 |
| --- | --- |
| アカウントの設定ディレクトリ | 登録済みアカウントごとのディレクトリ（アカウントの保存先の `<名前>/`）。そのアカウントで起動する claude の `CLAUDE_CONFIG_DIR` になる |
| 共有の設定ディレクトリ | ラッパーを通さずに起動した claude が使う設定ディレクトリ（`${CLAUDE_CONFIG_DIR:-~/.claude}`） |
| アカウント固有の項目 | アカウントの設定ディレクトリに実体で持ち、共有側への symlink にしない項目 |
| 共有する設定の部分 | `.claude.json` のうち、共有側を正とする `projects` と `mcpServers` |
| 同期のバックアップ | 前の同期で両側へ書いた共有する設定の部分の写し（`.ndf-shared-base.json`） |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 起動するアカウントを決めた | セグメントの起動・`ndf plan` の呼び出し | 指定が壊れていれば理由を出して止まる（今と同じ） | — |
| E2 | アカウントの設定ディレクトリを用意した（symlink・`.claude.json` の同期） | E1 で登録済みアカウントに決まった | 受け入れ条件 8 の理由で起動せず、次の候補で E1 からやり直す | E1 |
| E3 | アカウントの環境で子を起動した | E2 が済んだ | 起動の失敗は今の扱い（理由の 1 行で終わる） | E2 |
| E4 | 共有する設定の部分を共有側へ書き戻した | セッションの切れ目・ラッパーの終わり | 書けなければ理由を 1 行出し、次の起動の前の同期でそろえる | E3 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf -q -n 4`（CI テストの設定のあるリポジトリなので、合否は PR の CI で決める） |
| 静的解析・型検査 | PR の CI の検査 |
| 手動確認 | リリースの後に、2 つの登録済みアカウントで `ndf-claude` を起動し、`ndf relay account use` で替えた後のセグメントで `/status` のアカウントと `--resume` の会話を見る |

## リリース後の確認の計画

| 条件 | 経路を動かすバージョン | 確かめる時点 |
| --- | --- | --- |
| 1. 指定したアカウントの認証で子が動く | 配ったバージョン | このリリースの後 |
| 5. アカウントを替えた後に `--resume` で会話を開ける | 配ったバージョン | このリリースの後 |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `CLAUDE_CONFIG_DIR` だけを渡すと `--resume` が会話を見つけられなくなるので、symlink・`.claude.json` の同期・OAuth トークンの環境変数を外す・使えないアカウントを飛ばす判定をまとめて移す | 本文の「起票者の推定」（10 系の `claude_accounts.py` の作り） | 受け入れ条件 2・4〜10 |
| S2 | 行き先は閉じた子 M3b #3407（relay）の後続。#1142 の子の表へ載せるかはオーガナイザーが決める | 本文の「起票者の推定」 | 含まない（課題の置き場の判断で、要求ではない） |
| S3 | 11.0.0 の ndf-next への入れ替えの前に直す | 本文の「申し送り」（オーガナイザーの申し送り） | 採らない（11.0.0 は既に出た。今のスプリントで直す） |
| S4 | 機能の対応表の該当の行が試走で passed になる | 本文の「期待する結果」 | 採らない（対応表は 11.0.0 の入れ替えの確かめの材料で、アカウントの子の起動を直接扱う行が無い。同じことは受け入れ条件 1・5 とリリース後の確認で確かめる） |
| S5 | `ndf plan` の worker の `claude -p` も同じ環境で起動する | 手順書の子の環境の段落（「ラッパーと `ndf plan` は…用意する」） | 受け入れ条件 11（`ndf plan` は `child_env` を通らず、子の環境を継ぐ。継いだ環境で同じアカウントになることを条件にし、呼び出しごとの選び直しは #3864 で扱う） |

## リクエスト（原文）

> ## 事象
>
> 11 系の NDF（作り直しの間の ndf-next）で `ndf relay account add` → `ndf relay account use` でアカウントを選んでも、ラッパーが起動する子の claude は元のログインのまま動く。
>
> `plugins/ndf/src/ndf/domains/relay/session_env.py` の `child_env` は、子へアカウントの名前（`NDF_CLAUDE_ACCOUNT`）しか渡さず、認証の入った設定ディレクトリ（`CLAUDE_CONFIG_DIR`）を替えない。10 系の ndf は `plugins/ndf/scripts/lib/claude_accounts.py` で設定ディレクトリを子へ渡していた（10 系のファイルは入れ替えで無くなった）。
>
> ## 期待する結果
>
> - `ndf relay account use <名>` の後にラッパーが起動する子が、そのアカウントの認証で動く
> - アカウントを替えた後の `--resume` が前の会話を見つける
> - 機能の対応表（今は `scripts/ndf-next-parity/parity/features.json`）の該当の行が試走で passed になる
>
> ## 由来
>
> PR #3590 https://github.com/devbasex/ai-plugins/pull/3590 の cross-review の 3・4 ラウンドで codex が指摘した（`plugins/ndf-next/skills/development-workflow/references/relay-accounts.md:18`、major）。#3569 が移したのはアカウントの登録までで、子の起動の環境は範囲の外のため見送った。

（書き手: 起票者、日付: 2026-11-15〜2026-11-19。課題の本文の事象・期待する結果・由来の節。利用者の指示の原文は含まない）

> - 11.0.0 の入れ替えの前に直す（オーガナイザーの申し送り）

（書き手: 起票者（オーガナイザーの申し送り）、日付: 2026-11-16）

> 課題: #3604 #3864 … #3604: 11 系の中継で `ndf relay account use` で選んだアカウントが、ラッパーが起動する子の claude に効かない（`plugins/ndf/src/ndf/domains/relay/session_env.py` の `child_env` が `CLAUDE_CONFIG_DIR` を替えない。会話の記録の共有・`.claude.json` の同期・OAuth トークンの環境変数を外す・使えないアカウントを飛ばす判定を合わせて移す要がある）

（書き手: AI の起動指示（conductor が supervisor を起動した指示）、日付: 2026-11-21）

