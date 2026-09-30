# #1523: ラッパーの下の claude で claude.ai のコネクタが読み込まれない（CLAUDE_CODE_OAUTH_TOKEN で認証を渡すため）

正は課題の本文（#1523）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何を見つけたか
>
> ラッパー（`relay.py run`）の下で起動した claude では、claude.ai のコネクタ（Slack・Notion・Google Drive など）が一つも読み込まれない。ラッパーを通さずに起動した claude では、同じアカウントで読み込まれる。
>
> `plugins/ndf/scripts/lib/claude_accounts.py` の `_account_env()`（537 行目付近）が、選んだアカウントのアクセストークンを環境変数 `CLAUDE_CODE_OAUTH_TOKEN` で子に渡している（#1389 の設計）。Claude Code は、この環境変数で認証すると claude.ai のコネクタを取りに行かない。
>
> 同じアカウントのトークンで、渡し方だけを変えて `claude mcp list` を比べた（ndf 10.17.46、Claude Code 2.1.285、2026-09-30）。
>
> | 認証の渡し方 | claude.ai のコネクタ |
> |---|---|
> | 設定ディレクトリの `.credentials.json` | Slack・Notion・Google Drive・Google Calendar・Claude Docs がすべて `✔ Connected` |
> | 同じアカウントのアクセストークンを `CLAUDE_CODE_OAUTH_TOKEN` で渡す | 一覧に出ない（プロジェクトの `.mcp.json` のサーバーだけが出る） |
>
> トークンのスコープには `user:mcp_servers` が含まれている。スコープの不足ではなく、環境変数での渡し方が原因と判断した。
>
> ## 直さないと何が起きるか
>
> ラッパーの下の作業では、claude.ai 側で接続したコネクタが使えない。利用者はコネクタが消えた理由がわからず、`.mcp.json` に同じサービスのサーバーを別に立てるなどの回避をすることになる（今回は Slack で起きた）。
>
> ## 期待する振る舞い
>
> ラッパーの下の claude でも、選んだアカウントの claude.ai のコネクタが読み込まれる。
>
> 案:
>
> - 子の起動時に、トークンを環境変数で渡す代わりに、アカウントの置き場 `<名前>/`（`.credentials.json` がある）を `CLAUDE_CONFIG_DIR` として渡す。この場合、共有の設定（プラグイン・`settings.json`・`.claude.json`）の読み込みに影響するため、置き場への連携の方法を設計する必要がある
> - 環境変数での認証を続ける場合は、コネクタが読み込まれないことを利用者向けの文書に書き、ラッパーを外して起動する手順を示す
>
> ## 由来
>
> 利用者のプロジェクトでの作業中、ラッパーの下で claude を再起動しても claude.ai の Slack コネクタが出ないことから調査した。
>
> ## 追加の確認（2026-09-30 20:35 JST、利用者の報告から調べ直した）
>
> ラッパーの複製 10.17.47（`relay-10.17.47-ed04630a`）・Claude Code 2.1.285 で、同じ現象を確かめた。今のセッションの claude（ラッパーの子）の環境には `CLAUDE_CODE_OAUTH_TOKEN` と `NDF_CLAUDE_ACCOUNT=nyle-personal` が入っている。`claude mcp list` の claude.ai のコネクタの件数を、渡し方ごとに比べた。
>
> | 渡し方 | claude.ai のコネクタ |
> |---|---|
> | ラッパーと同じ（`nyle-personal` のトークンを `CLAUDE_CODE_OAUTH_TOKEN` で） | 0 件 |
> | **共有の設定ディレクトリ `~/.claude` の既定のログインのトークン**を `CLAUDE_CODE_OAUTH_TOKEN` で | **0 件** |
> | 変数を外す（`~/.claude/.credentials.json` で認証） | 5 件すべて `✔ Connected` |
> | 変数を外し、`CLAUDE_CONFIG_DIR=~/.claude/ndf/accounts/nyle-personal`（アカウントの置き場） | **5 件すべて `✔ Connected`** |
>
> - 既定のログインのトークンでも 0 件なので、**どのアカウントのトークンかに依らず、環境変数で渡す経路そのものでコネクタが読まれない**。2 つの `.credentials.json` のスコープは同じ（`user:inference`・`user:mcp_servers`・`user:profile` ほか 6 つ）
> - 案 1（置き場を `CLAUDE_CONFIG_DIR` にする）は、コネクタについては実物で成り立つ。残るのは、共有の設定（プラグイン・`settings.json`・`.claude.json`・履歴）をどう引き継ぐかの設計である
> - 手元の回避: ラッパーを外して `command claude` で起動すると、共有のログインでコネクタが使える（アカウントの切り替えは効かない）

（解釈: 「ラッパーの下の claude」は、`lib/claude_accounts.py` の `account_env()` で登録済みアカウントの環境を組み立てて起動するすべての claude を指す。ラッパー（`relay_lib/claude.py`）が起動する区間の claude（conductor）と、`supervise.py`（`supervise_lib/claude.py`）が起動する claude -p の両方である。「コネクタが読み込まれる」は、同じアカウントの置き場を `CLAUDE_CONFIG_DIR` にして変数なしで起動したときと同じ claude.ai のコネクタが、`claude mcp list` に `✔ Connected` で並ぶことを指す。案の 2 つは設計の候補であり、要求はどちらにも縛らない）

## 目的

- 登録済みアカウントで起動した claude でも、ラッパーを通さずに起動したときと同じく、そのアカウントの claude.ai のコネクタが使える
- #1389 で決めたアカウントの切り替えの性質（共有の設定ディレクトリの認証情報に触れない・会話の記録の置き場を分けない・切り替えた先で `--resume` できる・トークンの更新は NDF が行う）を保つ

## 調べたこと（2026-09-30、Claude Code 2.1.285 の本体と実測）

- **コネクタを取りに行く条件は、トークンに付いたスコープの一覧に `user:mcp_servers` があることである。** 本体の claude.ai のコネクタの取得（`[claudeai-mcp]` のログを出す関数）は、`ENABLE_CLAUDEAI_MCP_SERVERS` と `disableClaudeAiConnectors` の設定・安全モード・サードパーティの接続（Bedrock など）・API キーの優先を順に見た後、認証情報の `scopes` が `user:mcp_servers` を含まなければ `Missing user:mcp_servers scope` で取得をやめる
- **環境変数 `CLAUDE_CODE_OAUTH_TOKEN` のトークンには、スコープが `user:inference` だけとして付く。** 本体は変数のトークンを `refreshToken: null`・`expiresAt: null` の認証情報として組み立て、スコープを `CLAUDE_CODE_OAUTH_SCOPES`（空白区切り）から読み、無ければ既定の `["user:inference"]` にする。課題の表で「既定のログインのトークンでも 0 件」になった理由はこれで説明がつく（トークンの持ち主やトークンが持つ本当のスコープに依らない）
- アカウント `nyle-personal` の置き場の `.credentials.json` のスコープは `user:file_upload user:inference user:mcp_servers user:plugins user:profile user:sessions:claude_code` である
- 変数が無い今のセッションで、`CLAUDE_CODE_OAUTH_SCOPES` に上のスコープを入れて `claude mcp list` を打つと、claude.ai のコネクタ 5 件が `✔ Connected` で並んだ。**ただしこのとき `CLAUDE_CODE_OAUTH_TOKEN` は入っておらず、認証は共有の `.credentials.json` だった。** 変数のトークンと `CLAUDE_CODE_OAUTH_SCOPES` を組み合わせたときにコネクタが読まれるかは、この工程では確かめていない（トークンを取り出して扱う操作は共通原則の C1 に当たるため、未決 1 へ回した）
- 本体には、スコープを変数で渡す使い方の前例がある（リモートの環境の起動で `CLAUDE_CODE_OAUTH_TOKEN` と `CLAUDE_CODE_OAUTH_SCOPES` を組にして子へ渡している）

これで設計の候補は次の 3 つになる。どれを採るかは `design` が決める。

| 候補 | 中身 | 要求から見た懸念 |
| --- | --- | --- |
| A（課題の案 1） | アカウントの置き場を `CLAUDE_CONFIG_DIR` にして渡す | #1389 の決定 1 で採らなかった形。会話の記録（`projects/`）・プラグイン・設定・`.claude.json` が置き場へ移る。Claude Code 自身が置き場の `.credentials.json` を更新し、NDF の更新（決定 3）と取り合う。子の中で `CLAUDE_CONFIG_DIR` から導くアカウントの置き場（`store_dir()`）の場所がずれる |
| B | トークンは今のまま変数で渡し、アカウントの `.credentials.json` のスコープを `CLAUDE_CODE_OAUTH_SCOPES` で添える | 変数のトークンと組み合わせたときに読まれるかが未検証（未決 1）。本体の内部の振る舞いに頼る（前提 2） |
| C（課題の案 2） | 今のまま渡し、読まれないことを文書に書く | 期待する振る舞いを満たさない。A と B がどちらも成り立たないときの退路 |

## 前提

- 前提 1: 目標は「ラッパーを通さずに起動したときと同じ振る舞い」である。supervise.py が起動する claude -p にも同じくコネクタを読み込ませる（ラッパーを通さない起動では claude -p も読み込むため）。コネクタのツールの定義で増える読み込み量は、ラッパーを通さない起動と同じ水準として受け入れる
- 前提 2: 振る舞いの基準は Claude Code 2.1.285 である。コネクタを取りに行く条件と、変数のトークンのスコープの決まり方は本体の内部の実装で、公開の文書には無い。将来の版で変わったときは、受け入れ条件 1 の実測の手順で見つける
- 前提 3: 従量の接続（Bedrock・API キー）では、Claude Code がコネクタを読まない（サードパーティの接続・API キーの優先の判定）。従量の接続の子にコネクタを読ませることは対象にしない
- 前提 4: 登録が 1 つ以下で、アカウントの環境を組み立てずに起動するとき（#1389 の受け入れ条件 9）は、今でもコネクタが読まれる。この経路は変えない
- 前提 5: 置き場の `.credentials.json` の `scopes` は `claude auth login` が書いたもので、その値がトークンの実際のスコープと一致する

## 対象範囲

含む:
- 登録済みアカウントの子の環境の組み立て（ラッパーと supervise.py が共有する 1 か所）
- 従量の接続へ移るとき、アカウント由来の変数を外す処理
- 認証の渡し方を書いた利用者向けの文書（`development-workflow` の `references/relay.md`）と、`supervise_lib/plan.py` の説明
- #1389 の設計の決定 1 の見直しの記録（採った形と理由）

含まない:
- 従量の接続でコネクタを使えるようにすること（前提 3）
- claude.ai のコネクタとは別の、プロジェクトの `.mcp.json`・利用者の設定の MCP サーバーの扱い
- アカウントの登録（`account add`）・選び方・上限の検知・使用量の取得の変更
- コネクタの読み込みで増えるトークン消費を減らすこと

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 区間か呼び出しのアカウントを選んだ | ラッパーの区間の起動・supervise.py の呼び出しの起動かやり直し | 使えるアカウントが無い → 従量の接続か待ち（#1389 のまま） | — |
| E2 | アカウントのトークンを読んだ（期限が近ければ更新した） | E1 | 読めない・更新を断られた → `needs_relogin` にして次の候補（#1389 のまま） | E1 |
| E3 | アカウントのスコープを読んだ | E2 と同じ排他の中 | `scopes` が無い・壊れている → 前提 5 が崩れる。受け入れ条件 5 のとおり今と同じ起動を続ける | E2 |
| E4 | 子の環境を組み立てた | E2・E3 | — | E3 |
| E5 | 子の claude が起動し、認証の方式を決めた | E4 | 認証に失敗 → 今の失敗の経路（上限・401 の扱い）のまま | E4 |
| E6 | 子の claude が claude.ai のコネクタの一覧を取得した | E5 | スコープが足りない・取得先の失敗 → コネクタ無しで起動を続ける（Claude Code の振る舞い。止めない） | E5 |
| E7 | 区間を切り替え、別のアカウントで `--resume` した | 上限の検知・閾値 | 会話の記録が見つからない → `--resume` が通らない（受け入れ条件 7 で防ぐ） | E1〜E6 が新しいアカウントで再び起きる |
| E8 | 従量の接続へ移った | 登録済みアカウントがすべて上限 | — | E4 で足したアカウント由来の変数が外れている（受け入れ条件 6） |

## 用語

| 用語 | 意味 |
| --- | --- |
| claude.ai のコネクタ | 利用者が claude.ai で接続した MCP サーバー（Slack・Notion・Google Drive など）。Claude Code は起動時に claude.ai から一覧を取り、`claude mcp list` に `claude.ai <名前>` として並べる |

## 受け入れ条件

- [ ] 1. 登録済みアカウントが 2 つ以上あり、スコープに `user:mcp_servers` を持つアカウントで起動した区間の claude の中で `claude mcp list` を打つと、同じアカウントの置き場を `CLAUDE_CONFIG_DIR` にして変数を外して打ったときと同じ claude.ai のコネクタが、同じ件数だけ `✔ Connected` で並ぶ（実物のアカウントで 1 回。件数が 0 件のアカウントでは確かめたことにならない）
- [ ] 2. supervise.py が登録済みアカウントで起動する claude -p の環境は、受け入れ条件 1 の区間の環境と、コネクタの読み込みを決める変数について同じになる（単体テストで、ラッパーと supervise.py が組み立てる環境を比べる）
- [ ] 3. `account_env()` がアカウントの環境を組み立てるとき、置き場の `.credentials.json` の `scopes` が `user:mcp_servers` を含めば、その環境で起動した Claude Code 2.1.285 がコネクタを取りに行く条件（`scopes` に `user:mcp_servers` がある・API キーとサードパーティの接続の変数が無い）を満たす（単体テスト。偽のアカウントの置き場で確かめる）
- [ ] 4. アカウントを切り替えて次の区間を起動したとき、新しい区間の claude は新しいアカウントのスコープで起動する（前のアカウントのスコープが環境に残らない。単体テスト）
- [ ] 5. 置き場の `.credentials.json` に `scopes` が無いか壊れているアカウントでも、今と同じく子が起動する（起動を止めず、失敗を利用者へ求めない。単体テスト）
- [ ] 6. 従量の接続で起動する子の環境には、アカウント由来の変数（トークン・アカウントの名前・スコープなど、この変更で足したものを含む）が残らない（単体テスト。#1389 の I16 を保つ）
- [ ] 7. 会話の記録の置き場は変わらない。アカウントを切り替えた次の区間で、前の区間の会話を `--resume` で続けられる（#1389 の受け入れ条件の退行を見る既存テストが通る）
- [ ] 8. 共有の設定ディレクトリの `.credentials.json` を読まず、書かない。子の中で求めたアカウントの置き場（`store_dir()`）が、ラッパーの中で求めたものと同じ場所になる（単体テスト）
- [ ] 9. トークンの更新は今と同じく NDF だけが行い、置き場の `.credentials.json` を Claude Code の子が書き換えない（候補 A を採る場合は、この条件を満たす手当てを設計が示す）
- [ ] 10. トークンの値が、子の引数・画面・`log.jsonl` に現れない（#1389 のまま。既存テストが通る）
- [ ] 11. `references/relay.md` と `supervise_lib/plan.py` の認証の渡し方の説明が、採った形と一致する。候補 C を採る場合は、コネクタが読まれないことと、パススルー（`command claude`）で起動する手順が `references/relay.md` にある
- [ ] 12. 既存の全体テストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 子の環境の組み立てに、ネットワークへの要求を足さない（スコープは置き場のファイルから、トークンと同じ排他の中で読む） |
| セキュリティ | トークンの値を引数・記録・画面に出さない。共有の `.credentials.json` に触れない。子へ渡すスコープは置き場の `scopes` にあるものだけで、足したり広げたりしない |
| 移行性 | 既に登録済みのアカウントを登録し直さずに効く（置き場の形を変えない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `relay.py account` のコマンドと出力は変わらない |
| データ | 置き場のファイルの形は変わらない |
| 既存の振る舞い | 登録済みアカウントで起動した claude と claude -p に claude.ai のコネクタが並ぶ（候補 A・B）。コネクタのツールの定義の分だけ読み込みが増える（前提 1） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_claude_accounts.py plugins/ndf/scripts/tests/test_relay_account.py -q`、全体は `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`python3 plugins/ndf/scripts/instructions-check.py --root .` |
| 手動確認 | 受け入れ条件 1。ラッパーの区間の claude の中で `claude mcp list \| grep 'claude.ai'` の行数と状態を数え、`env -u CLAUDE_CODE_OAUTH_TOKEN -u CLAUDE_CODE_OAUTH_SCOPES CLAUDE_CONFIG_DIR=<置き場>/<名前> claude mcp list \| grep 'claude.ai'` と比べる。利用者か、利用者が許した conductor が行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 子の環境の組み立ては `lib/claude_accounts.py` の 1 か所に置き、ラッパーと supervise.py に分けない（`AGENTS.md`・MVV の Value 6） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストを書かない |
| テスト戦略 | 環境の組み立ては偽のアカウントの置き場（`tests/account_fake.py`）で単体テストにする。Claude Code 本体の振る舞い（コネクタが並ぶか）は単体テストにできないため、受け入れ条件 1 の実測で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、#1389 の不変条件（I4・I5・I8・I16）を保つ |
| 確認してから行う | 実物のアカウントのトークンを使う確かめ（C1）、認証の渡し方の変更の承認（C2。設計の承認で人が見る） |
| 行わない | 共有の `.credentials.json` の読み書き、置き場の形の変更、`.mcp.json` にコネクタの代わりのサーバーを足すこと |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 変数のトークンと `CLAUDE_CODE_OAUTH_SCOPES` を組み合わせたとき、コネクタが読まれるか（候補 B が成り立つか）。登録済みアカウントのトークンを NDF の `account_env()` の経路で子へ渡して `claude mcp list` を打てば確かめられるが、実物のトークンを使うため共通原則の C1 に当たる | 利用者が確かめを許すか決め、`design` が結果で候補を選ぶ | 設計の承認の前 |
| 2. 認証の渡し方を変えるこの変更は、共通原則の C1（秘密に触れる）・C2（認証を変える）に当たる疑いがある。`pace` が承認ゲート 1 を MVV の判定に任せる設定でも、設計の承認は人が行うか | 利用者 | 設計の承認のとき |
| 3. 候補 A を採る場合、共有の設定（プラグイン・`settings.json`・`.claude.json`・`projects/`）を置き場へどうつなぐか | `design` | 設計の承認の前 |
