# #818: Claude Code と Codex で言語サーバを使えるようにする — 決定の記録

要求は [issue-818-requirements.md](issue-818-requirements.md)、設計は [issue-818-design.md](issue-818-design.md) にある。

**関門で決めるのは決定 1〜4 である。** 1 は置き場所、2〜4 は #818 の本文の作業項目・提案から外れる選び方である。残りは設計の中で決めた。

## 決定の記録

### 決定 1: 導入の Skill・スクリプト・hook は `mcp-serena` プラグインに置き、NDF には置かない（関門で最終決定）

Serena を使うのは `mcp-serena` を入れた利用者だけである。NDF に置くと、Serena を入れていない NDF の利用者にも Skill と hook が載り、hook は毎回「設定が無い」で空振りする。`mcp-serena` は Claude Code・Codex・Kiro へ同じディレクトリから配っている（`.claude-plugin` / `.codex-plugin` / `dev.kiro/install.sh`）。Skill と hook を足しても配り方は変わらない。

NDF に置く案は採らない。NDF の Skill の数（manifests が持つ）と hook の数が、Serena を使わない利用者の分まで増える。NDF 側に残すのは、コード改変系の Skill の 1 行とエージェント定義の Serena の行だけである（決定 10）。

### 決定 2: 言語を採るしきい値は「10 ファイル以上かつ 5% 以上」にする（関門で最終決定）

#818 の本文の提案は「10 ファイル以上、**または** 5% 以上」だった。「または」では、1 万ファイルのリポジトリに 10 個だけある言語も採り、#818 §8 の代償（メモリ 2.5 倍・初回 42 倍・道連れの失敗）を払う。割合の分母は、対応表の拡張子を持つファイルの数とする（`.md` や画像を数えない）。

このリポジトリで試すと、`python` 321（81%）・`bash` 71（18%）を採り、`.js` 4（1%）を外す。`carmo-system-serverside` は `python` 131・`typescript` 145（`.ts` 97 + `.js` 48）の両方を採る。

「または」を採らない理由は上のとおりである。しきい値を割合だけにする案も採らない。小さなリポジトリで 2 ファイルの言語が 5% を超える。

### 決定 3: Codex は `--context codex` で起動し、memory のツールが一覧に残ることを受け入れる（関門で最終決定）

#818 の作業項目 A は、Codex を `codex` の文脈で動かし、`no-memories` / `no-onboarding` を指定すると定める。Serena 1.7.0 の MCP の `tools/list` で実測すると、`codex` の文脈は `--mode` / `--add-mode` を渡してもツールを絞らなかった。返したのは 23 ツールで、`activate_project`・memory の 6 ツール・`onboarding`・`get_current_config`・`search_for_pattern` の 10 個は `claude-code` の文脈に無い。`claude-code` の文脈は 14 ツールで、`codex` の文脈に無い `replace_content` を持つ。違いは文脈の `single_project: true` の有無である。

**それでも `codex` の文脈を採る。** `claude-code` の文脈のプロンプトは、Claude Code の遅延読み込み（tool search）を前提にした指示を持ち、Codex には当てはまらない。memory のツールは呼ばれない限り費用を生まず、呼んだときにモードが拒むかは実装で確かめる（未確認 U3）。

Codex にも `claude-code` の文脈を渡す案は採らない。ツールは差し引き 9 個減る（10 個が消え、`replace_content` が 1 個増える）が、Codex に誤った前提の指示が渡る。自前の文脈の YAML を同梱する案も採らない。Codex が `.mcp.json` の中でプラグインのルートを展開するかを確かめられなかった（決定 4）。

### 決定 4: 起動のラッパースクリプト（導入済みの Serena を優先する）は作らない（関門で最終決定）

#818 の作業項目 A は、`${CLAUDE_PLUGIN_ROOT}/scripts/serena-launch.sh` を経由し、導入済みの Serena があればそれを使うと定める。ラッパーは `.mcp.json` の中でプラグインのルートが展開されることに頼る。Claude Code では展開を確かめた（#818 §10）が、**Codex では確かめられなかった**（隔離した `CODEX_HOME` にプラグインを入れて `codex exec` を走らせたが、起動の記録が残らなかった）。Kiro の installer は hook の中のプレースホルダだけを置き換え、`.mcp.json` は置き換えない。

ラッパーの利点は、初回のダウンロードとオフラインの 2 つだけである（2 回目以降の `uvx` は 0.8 秒、#818 §10）。どちらも、コンテナの作成時に `uvx --from serena-agent==1.7.0 serena --version` を 1 度打って `uv` のキャッシュを温めれば得られる。これは devbasex/devbase#236 へ依頼する。

### 決定 5: プラグイン名とサーバ名は変えない

接頭辞 `mcp__plugin_mcp-serena_serena__`（30 文字）を短くしても、Claude Code ではツールが遅延読み込みで、一覧に載るのは名前だけである。14 ツールで数百文字しか変わらない。名前を変えると、利用者の許可の設定（`permissions.allow`）と、エージェント定義・Skill に書いたツール名がすべて変わる。`serena` への改名は、公式マーケットプレイスの `serena` と衝突する（#818 §10）。

### 決定 6: 誘導の hook は公式の `serena-hooks` を使わず、標準ライブラリで自前に持つ

公式の `serena-hooks remind` は、コードファイルを固定の拡張子の一覧で判定する（`.json` / `.yaml` / `.toml` / `.html` / `.css` を含む 60 個）。Serena が扱わない言語の読み込みまで拒否し、`configure` で設定していないリポジトリでも拒否する。自前の hook は、`configure` の印（`mcp_serena_excluded`）のある `project.yml` の採った言語の拡張子だけを数える（AC19）。閾値・待ちの秒数・数の有効期間・数を戻さないツール名の部分文字列は公式と同じにする。

公式を `uvx` で呼ぶ案は採らない。起動は温まっていれば 0.06 秒で速さの問題は無いが、拡張子を絞れない。公式の `activate` も使わない。`--project-from-cwd` で有効化は済んでおり、`activate` はモデルに `activate_project` と `initial_instructions` を促す。

### 決定 7: 起動の検証は、`project.yml` を 1 言語ずつ書き換えて `serena project health-check` を走らせる

`health-check` は、`project.yml` の言語で Serena を起動し、1 ファイルに `get_symbols_overview` / `find_symbol` / `find_referencing_symbols` を通す。失敗すれば終了コード 1 を返す。Serena 1.7.0 の実装で確かめ、実測では `python` だけの設定で 6 秒・0、ファイルの無い言語で 1 だった。言語を 1 つずつにするのは、Serena が 1 言語の失敗で全体の初期化を止めるためである（#818 §8）。

Serena の内部の API（`SolidLanguageServer`）で言語を 1 つずつ起動する案は採らない。版を上げたときに壊れやすい。一時ディレクトリに別の `project.yml` を置く案も採らない。言語サーバが見るファイルの根が変わる。

### 決定 8: `project.yml` は行単位で書き換え、YAML のライブラリを使わない

hook と検査は標準ライブラリだけで動かす（`uvx` を hook の経路に入れない）。書き換えるのは `language_servers` と `ignored_paths` と `mcp_serena_excluded` の 3 つの最上位のキーだけで、Serena の雛形は前の 2 つをブロックの形の配列で書く。**知らない形（流れの形の非空の配列・アンカー）を見つけたら書かずに止める**（終了コード 3）。注釈と他のキーは 1 バイトも変えない。

`uvx --from serena-agent==1.7.0 python` で Serena の同梱の YAML ライブラリを使う案は採らない。SessionStart の hook から呼ぶと 1 秒の上限（AC23）に収まらない見込みで、注釈の保ち方も Serena の実装に依る。

### 決定 9: SessionStart は、設定済みのリポジトリでは食い違いと欠けがあるときだけ、未設定のリポジトリでは未設定だけを知らせ、何も導入しない

知らせる中身は、`configure` の印（`mcp_serena_excluded`）の有無で分ける（設計の「hook の振る舞い（SessionStart）」、AC4b・AC17・AC18）。

| リポジトリの状態 | 出すもの |
| --- | --- |
| git のリポジトリでない・採る言語が 0 | 何も出さない |
| 印が無い（`project.yml` が無い・Serena が自動で作った）で、採る言語が 1 つ以上 | 未設定の 1 行と、Skill の名前と「プロジェクトごとに実行する」の 1 行の 2 行だけ。食い違いと欠けの行は出さない |
| 印がある | 食い違いと欠けがあるときだけ出す。揃っていれば何も出さない |

揃っているリポジトリで毎回出す通知は読み飛ばされる（今の echo と同じ）。未設定の 2 行は、`configure` を打つまで出続けるが、打てば止まる（AC4b）。印の無いリポジトリで食い違いと欠けを出さないのは、Serena が自動で作った `project.yml` と検出の差を毎回知らせないためである（AC18）。導入（`uv tool install`・`npm install -g`・`claude plugin install`）は利用者の手元へ書き込み、ネットワークへ出て、失敗しても気付きにくい（#818 §10）。セッションの開始では検査だけを行い、直すのは Skill の手順で利用者の確認を取ってからにする。

検証に失敗して外した言語と、`--only` で名指しされなかった言語は、`project.yml` の最上位のキー `mcp_serena_excluded` に残し、突き合わせから除く。残さないと、外した言語が以後も検出で選ばれ、印のあるリポジトリで毎回食い違いとして知らせることになる。別の状態ファイルに置く案は採らない。`project.yml` を追跡すれば作業ツリーにも同じ記録が揃い、追跡から外すファイルを増やさずに済む。逆向き（`language_servers` にあってしきい値に届かない言語）は知らせない。利用者の名指しを尊重する。

`language_servers` のブロックの注釈に置く案は採らない。Serena 1.7.0 は項目の欠けた `project.yml` を読むと `ProjectConfig.save` で書き直し、そのとき `language_servers` を作り直して注釈が消える。知らないキーは書き直しの後も残る（2026-09-24 実測。`project create --ls python` の後、ブロックに `# mcp-serena: excluded ...` の行と最上位の `mcp_serena_excluded:` を足し、`encoding:` の行を消して `ProjectConfig.load('.', SerenaConfig.from_config_file())` を打つと、`encoding:` が足され、注釈の行だけが消えた）。このキーは「`configure` で設定した」印も兼ねる。Serena は `--project-from-cwd` で開いた根に `project.yml` が無ければ自動で作るため、ファイルの有無では設定の有無を判定できない。

### 決定 10: エージェント定義は、#818 では Serena の行だけを直す。構成の整理は #877 と #869 に残す

#877（知識系 Skill とエージェント定義の整理）と #869（`corder.md` の置き換え）は、同じファイルを構成ごと変える。#818 が直すのは次の 2 種類の行だけにする。

| 種類 | 行 |
| --- | --- |
| 今のツールに届かない名前 | `qa.md` の `mcp__plugin_ndf_serena__*`、`corder.md` の `mcp__serena__*` |
| 使えなくなる機能の記述 | `director.md` の「メモリー管理」 |

`corder.md` の Context7 の扱いは #869 が決める。

先に #877 を待つ案は採らない。#877 は別のマイルストーン（25）で、待つ間は古いツール名がモデルを誤らせ続ける。

### 決定 11: `initial_instructions` は一覧に残す

Serena のサーバの説明（初期化の `instructions`、130 文字）は、コードの作業の前に `initial_instructions` を読むよう求める。一覧から外すと、説明が存在しないツールを指す。読むと約 2.6k トークンの固定費になり、見積もりでは検査・仕上げ・取り込みの supervisor が持ち出しになる（設計の「効果の見積もり」）。**外すかどうかは、AC26 の実測で持ち場ごとに読まれた回数を見てから決める**（未確認 U6）。

Claude Code の `LSP` ツールと Serena の `get_diagnostics_for_file` も一覧に残す。どちらも遅延読み込みで、文脈はほとんど増えない（#818 §3）。`project.yml` の `excluded_tools` で外すと、Codex からも消える（`project.yml` はランタイムで共有する）。

### 決定 12: `.serena/project.yml` を追跡するかは利用者が選び、既定では `.gitignore` に触らない

作業ツリー（`.worktrees/<ブランチ名>`）で Serena を使うには、作業ツリーにも `project.yml` が要る。追跡していれば作業ツリーを作った時点で揃う。追跡しないと、作業ツリーの中で `--project-from-cwd` が作業ツリーの根を選び、Serena が印の無い `project.yml` を自動で作る（hook は数えも知らせもしない。そこで選ばれる言語は未確認 U4）。Skill はこの違いを示して選ばせ、追跡しないと決めたときだけ `--gitignore` を渡す。

`.serena/` の他のファイルは追跡しない側に倒す。Serena 1.7.0 が作る `.serena/.gitignore` は `/cache` と `/project.local.yml` だけで、`SERENA_HOME=.serena` で作られる `serena_config.yml`（このリポジトリの `.gitignore` は「認証の秘密を持つため追跡しない」として外している）・`logs/`・`language_servers/` が追跡の候補に残る（2026-09-24 実測）。これも `.gitignore` の書き換えなので、Skill が確認を取ってから `--serena-gitignore` を渡す。

### 決定 13: クラス図を作らない

作るスクリプトは関数の集まりで、型を定義しない。データの形は JSON（対応表・出力・数の記録）と YAML のキーで、設計の「データ構造」と「入出力の契約」の 2 節が持つ。

### 決定 14: スクリプトにするのは、規則だけで決まる手順のすべてである

検出・設定の生成・起動の検証・失敗した言語の除外・導入の検査・食い違いの通知・誘導・自動許可はスクリプトが持つ。モデルに残すのは 4 つの判断である: 導入のコマンドを打つか（利用者の確認）、`project.yml` を追跡するか（利用者の確認）、検証に失敗した言語の原因と次の手、検出の誤りを名指しで直すか。境界の一覧は設計の「スクリプトにする範囲」にある。

起動の検証の失敗から、言語サーバのキャッシュを退避して再試行するところまでをスクリプトにする案は採らない。退避は導入先の `.serena/language_servers/`（`SERENA_HOME=.serena`）を書き換え、失敗の原因がキャッシュの破損でないとき（依存の不足・ネットワーク）には効かない。

### 決定 15: Kiro は Claude Code と同じ起動定義と hook の定義を受け取る

Kiro の installer（生成物）は `.mcp.json` と `hooks/hooks.json` を読む。Serena には Kiro の文脈が無い。今の `ide-assistant` は `claude-code` の旧名で、Kiro の動きは変わらない。hook は Kiro のイベント名の変換で PreToolUse がそのまま残り、Kiro が読まない名前なら動かない。SessionStart は `agentSpawn` へ写され、`--client claude-code` のまま走る。そのままでは `installed_plugins.json` の無い Kiro の利用者に「Claude Code の LSP が足りません」を毎回出すため、`--client claude-code` の hook は環境に `CLAUDE_PLUGIN_ROOT` が無ければ何も出さない（Claude Code は hook に置き、Kiro は置かない）。hook のスクリプトは知らない入力で何も出さずに 0 で終わる。Kiro 専用の定義を足す案は、Kiro の受け入れを確かめる手段が無いため、この課題では採らない（前提 3）。

### 決定 16: `SERENA_HOME` は `.serena`（cwd からの相対）のまま保ち、作業ツリーごとの言語サーバの取得を受け入れる

相対の値は Serena を起動した cwd から解決される（Serena 1.7.0 の `SerenaPaths`）。`.worktrees/<ブランチ名>` で起動したセッションは作業ツリーごとに `.serena/language_servers/` を持ち、作業ツリーを作るたびに、取得を伴う言語サーバの取得と初回の起動（#818 §8）をやり直す。`AGENTS.md` は開発を作業ツリーで行うと定めるので、この費用は作業ツリーの数だけ繰り返される。大きさは AC12 の手動確認で測る。

それでも今の値を保つ。変えると、既に使っている利用者の手元の設定と言語サーバの置き場所が移る。共有する案は次の理由で採らない。

| 案 | 採らない理由 |
| --- | --- |
| `~/.serena` | 公式の `serena` と利用者の全体の設定（`serena_config.yml`）を共有する。`configure` の検証が全体の設定を書き換え得る |
| 絶対パス（`${HOME}/...` などの展開） | `.mcp.json` の中の展開に頼る。Codex で展開を確かめられていない（決定 4・U1） |

AC12 の測定で費用が大きいと分かったら、共有する置き場所を別の課題で考える。
