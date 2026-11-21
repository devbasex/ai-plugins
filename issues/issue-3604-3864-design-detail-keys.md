# #3604 #3864 の詳細設計（キーの子）: 環境変数の宣言の API キー・キーの子の認証の失敗の停止・設定の変更での停止

詳細設計書 [issue-3604-3864-design-detail.md](./issue-3604-3864-design-detail.md) から、API キーで動く子の節を分けた文書である。設計文書は [issue-3604-3864-design.md](./issue-3604-3864-design.md) で、決定 1・6・12 と「#4018 の cross-review の設計の指摘」の表がこの文書の行を指す。パスは `plugins/ndf/` からの相対で書く。

**キーの子**は、API キーで動く次の 2 つの子をまとめた呼び方である。この文書と詳細設計書は、この 2 つを同じ規則で扱う。

| 子 | 子のアカウントの名前 | キーの出所 |
| --- | --- | --- |
| 登録簿の API キーの子 | `key:<名前>` | API キーの登録簿（`api-keys.json`） |
| API キーの宣言の従量の接続の子 | `metered` で、`account_registry.metered_kind(宣言)` が `API_KEY_KIND` | 環境変数の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`）の `ANTHROPIC_API_KEY` |

クラウド事業者の従量の接続（Bedrock など）の子は、キーの子に入らない。

## 宣言の提供元の確定（`account_registry.metered_kind`・`API_KEY_KIND`・`API_KEY_WITH`）

| 名前 | 種類 | シグネチャ・値 | 責務 |
| --- | --- | --- | --- |
| `metered_kind` | 新設 | `(env: Mapping[str, str]) -> Optional[str]` | 従量の接続の宣言の提供元を 1 つに確定する。`PROVIDER_ENV` のうち `provider_on` が真のものがちょうど 1 つで、ほかの `PROVIDER_ENV` がどれも偽（None の値があれば外れる）で、`ANTHROPIC_API_KEY` を持たなければ、その変数の名前を返す。どれも真でなく、`ANTHROPIC_API_KEY` が空白でない値で入っていて、ほかの `METERED_CHECKED` の変数が `API_KEY_WITH` と偽の提供元の選択だけなら `API_KEY_KIND` を返す。それ以外は None |
| `API_KEY_KIND` | 新設 | 定数 `"api-key"` | API キーの宣言を表す値 |
| `API_KEY_WITH` | 新設 | 定数。`ANTHROPIC_API_KEY` と「提供元ごとの照合する変数」の接続先の組の変数 | API キーの宣言が併用できる照合の変数 |

API キーの宣言で None になる組み合わせ:

| 組み合わせ | 例 | None にする理由 |
| --- | --- | --- |
| 提供元の選択と API キーの混在 | `CLAUDE_CODE_USE_BEDROCK=1 ANTHROPIC_API_KEY=…`（キーが空でも） | 同じ宣言を Bedrock として起動するか、候補から外すかが分かれる |
| ほかの認証の値 | `ANTHROPIC_API_KEY=… ANTHROPIC_AUTH_TOKEN=…`・`CLAUDE_CODE_OAUTH_TOKEN` | 認証の優先順位（STD2）で、指紋で固定したキーと別の資格情報が使われる |
| 資格情報とアカウントの選択 | `ANTHROPIC_PROFILE`・`AWS_PROFILE` | 同上 |
| 要求に足すヘッダー | `ANTHROPIC_API_KEY=… ANTHROPIC_CUSTOM_HEADERS=…` | ヘッダーの `Authorization` で別の資格情報を送れる |

使う場所: `wrapper._metered` の検証の「提供元を 1 つに選べる」の行（`metered_kind` が None でない）・`metered_env` が親の値を外すか・`account_choice.build` が helper を足すか・「従量の実効値の照合」の提供元の数。どれもこの 1 つの結果で決め、キーの有無や提供元の変数を別々に見ない。保存した設定の `check_decl` は、`PROVIDER_ENV` の名前を返す宣言だけを通す（保存した設定は資格情報の変数を持てない）。

**API キーの経路で使えるゲートウェイの設定は、接続先の変数だけである。** 追加のヘッダーが要るゲートウェイは、API キーの経路（環境変数の宣言の API キーと登録簿の `key:<名前>`。後者は詳細設計書の「`verify`」の表でヘッダーを拒む）では使えない。そうした宣言では、`_tell_at_start` の理由の 1 行に `ANTHROPIC_CUSTOM_HEADERS` の名前と「API キーの宣言ではヘッダーを足せない。ヘッダーの要らない接続先にするか、宣言から外す」を出し、従量の接続を候補に入れない（退避先が知らせなしに消えない）。値は出さない。

## 環境変数の宣言の API キー（`session_env.metered_env`・`account_choice.build`・`cli_account._acc_key_helper`）

**宣言の `ANTHROPIC_API_KEY` は、子の環境へ入れず、登録簿の API キーの子と同じく起動引数の `--settings` の `apiKeyHelper` で渡す。** 環境の `ANTHROPIC_API_KEY` で対話の claude を起動すると、キーを使うかを問う画面（「Detected a custom API key in your environment」）が出る。未承認のキーでは、切り替えた子が利用者の回答待ちで止まる。`apiKeyHelper` では問いが出ない（どちらも設計文書の「未確認のまま残ること」の「API キーの渡し方」で確かめた）。

| 部品 | API キーの宣言での振る舞い |
| --- | --- |
| `metered_env` | `account-drop` に加え、`METERED_CHECKED` の変数をすべて親の環境から外してから宣言を重ねる。宣言の `ANTHROPIC_API_KEY`（`session_env.METERED_HELPER_KEY`）は重ねない。親だけにある `ANTHROPIC_BASE_URL`・`ANTHROPIC_CUSTOM_HEADERS` は子に残らない。宣言にある接続先の変数は入る |
| 照合の期待値 | `metered_env` の出力。`METERED_CHECKED` の期待値は宣言の値だけで、親だけにある接続先とヘッダーは期待値に無い。設定ファイルから入っても、親から残っても外れる |
| `account_choice.build` | `metered` で `metered_kind(宣言)` が `API_KEY_KIND` のとき、`key:<名前>` と同じ「`--settings` の合成」で helper を足す。helper の文字列は `shlex.join([<ndf の実行ファイルの絶対パス>, "relay", "account", "api-key-helper", "metered", <指紋>])` で、ラッパーの起動では末尾に下の「helper の記録」の `--record`・`--id` が付く。合成は照合用の起動引数（`fallbacks`・`verify` に渡す JSON の文字列）と子の実際の起動引数（`target_env` の一時ファイル）の両方に同じ条件で入れる（照合と起動で helper の有無を違えない） |
| `<指紋>` | ラッパーが起動の前に検証した宣言の `ANTHROPIC_API_KEY` の値の SHA-256 の 16 進（`hashlib.sha256(値.encode()).hexdigest()`）。値そのものは起動引数にも `--settings` のファイルにも入れない（キーは推測できない長さの乱数なので、指紋から値は戻せない） |
| `api-key-helper metered <指紋>` | 自分の環境（Claude Code が設定の `env` を重ねた後の子の環境）の `NDF_SUPERVISE_CLAUDE_FALLBACK` を `wrapper._metered` と同じ検証で解き、`metered_kind` が `API_KEY_KIND` で、キーの値の SHA-256 が `<指紋>` と同じときだけ値を出す。宣言が無い・解けない・API キーの宣言でない・指紋が違えば、何も出さずに終了コード 1（下の「キーの子の認証の失敗の停止」へ入る）。設定の `env` が宣言を別のキーに差し替えても、検証したキーと違うキーは出さない |
| 起動の前の照合 | 詳細設計書の「従量の実効値の照合」の表の、helper の行（値が上の helper の文字列で、出所が `--settings`）と `NDF_SUPERVISE_CLAUDE_FALLBACK` の行（実効値が `metered_env` の継いだ値と同じ）。起動の後の差し替えは、下の「設定の変更でキーの子を止める範囲」で検出する |

保存した設定は資格情報の変数を持てないので、この経路は環境変数の宣言だけにある。

## キーの子の認証の失敗の停止

キーの子が 401 を受けたときと、helper が終了コード 1 を返したときの規則である。**どちらも、替え先の有無によらず子を残さない（`stalled` にしない）。** 子を残すと Claude Code が helper を呼び直し続け、止まる時点が無い。止めた後に替え先が無ければ、直し方の 1 行を出して `Ended("account_unavailable")` で終える。登録済みアカウントとクラウド事業者の従量の接続の子は、今どおり替え先が無ければ `stalled` で残す。

| 規則 | 中身 |
| --- | --- |
| helper の記録 | `api-key-helper`（`metered` と登録簿の `<名前>` の両方）は、記録先と ID を起動引数の `--record <記録先> --id <helper の ID>` だけから決める。Claude Code が設定の `env` を重ねた後の `NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH` は、設定で書き換えられるので使わない。ラッパーは `_launch_target` で `target_env` を打つ前に helper の ID（`Target.helper_id`。`secrets.token_hex(16)`）を作り、記録先を、ラッパーが確定した中継の保存先（`self.dir`）の `key-helper.jsonl` の絶対パスにして、`build`・`target_env` のキーワード引数 `record=(記録先, helper の ID)` で渡す。子を起動する前に、保存先が `lstat` で symlink でなく自分の所有の 0700 のディレクトリであることを確かめ、古い記録と失敗の印（同じディレクトリの `key-helper.fail`）を `unlink` してから `os.open(記録先, O_WRONLY \| O_CREAT \| O_EXCL \| O_NOFOLLOW, 0o600)` で空の記録を作り、`(st_ino, st_dev)` を控える。確かめか作成が落ちれば、その起動先を理由 `key_record` で飛ばす（記録の無いキーの子を起動しない） |
| helper の書き方 | 呼ばれるたびに記録を `os.open(記録先, O_RDWR \| O_APPEND \| O_NOFOLLOW \| O_CLOEXEC)` で開き（作らない）、`fstat` で通常のファイル・`st_nlink` が 1・所有が自分・権限が 0600 であることを確かめ、`fcntl.flock(LOCK_EX)` を取る。下の払い出しの上限の照合と 1 行の追記を、同じ錠の中で済ませる。1 行は `{"id": <helper の ID>, "at": <時刻>, "out": <キーを出したか>}` で、キーの値も指紋も書かない。キーを標準出力へ出すのは、`out: true` の行を書いて錠を放した後だけである。開けない・確かめが外れる・錠が `relay.key-helper` の `lock`（0.5 秒）で取れない・追記が落ちるときは、キーを出さずに終了コード 1。このとき記録には行を足せないので、終える前に、錠を取らずに記録と同じディレクトリの失敗の印 `key-helper.fail` を `os.open(…, O_WRONLY \| O_CREAT \| O_APPEND \| O_NOFOLLOW \| O_CLOEXEC, 0o600)` で開き、`{"id": <helper の ID>, "cause": "lock" か "append" か "record"}` の 1 行を足す（印を作れない・書けないときもそのまま終了コード 1）。記録が正常なまま行が増えない失敗も、ラッパーがこの印で観測する`--record` と `--id` は両方あるか両方無いかで、片方だけなら終了コード 1。両方無い起動（`ndf plan`。下の「入れ子の `ndf plan`」）は記録も上限も持たない |
| 払い出しの上限 | 錠の中で記録を読み、同じ helper の ID の行について次の順に確かめる。(a) ラッパーが書いた拒否の印（`{"id", "deny": true}`）か、`out` が偽の行が 1 つでもあれば出さない。(b) `out` が真の行が直近の `relay.key-helper` の `seconds`（60 秒）の間に `attempts`（3 回）以上あれば出さない。出さないときは `out: false` の行を足して終了コード 1。照合と追記が 1 つの錠の中にあるので、helper が同時に何本起動しても、`out: true` の行はどの 60 秒の間でも 3 行を超えない。(a) により、一度拒んだ後と拒否の印の後は、60 秒を過ぎても同じ ID へ出さない。正常時は起動の時点の 1 回と 5 分ごとの 1 回なので当たらない。指紋が違う・宣言が無い・使い方が `off`・無い名前で終了コード 1 を返すときも `out: false` の行を足す。`relay.key-helper` は `contracts/limits.json` に新設し、`seconds`・`attempts`・`lock` の 3 つを持つ |
| 検出（helper の終了コード 1） | `_key_auth` は `key-helper.jsonl` を `lstat` し、印が変わったときだけ `O_NOFOLLOW` で開いて読む。今の helper の ID の `out: false` の行があれば、画面の引き金と会話の記録を待たずに認証の失敗を確かめたとみなす。記録の異常（無い・symlink になった・控えた `(st_ino, st_dev)` と違う・`st_nlink` が 1 でない・読めない・行が JSON でない）も同じく確かめたとみなす（払い出しを数えられない子を動かし続けない）。加えて、見回りのたびに失敗の印 `key-helper.fail` を `lstat` し、在れば（今の helper の ID の行があるか、symlink・読めない・JSON でない行があれば。子の起動の前に消すので、在るのは今のセグメントの helper の失敗）同じく確かめたとみなす。錠の取得を打ち切った・追記が落ちた helper は記録に行を残さないので、記録の印だけでは観測できない。保存先のディレクトリ自体が消えた・symlink になった・所有か権限が変わったときも、記録の異常として確かめたとみなす（印を作れない失敗をここで拾う）。設定の変更の関所（下の「反映の前に拒む」）が書いた `gate` の行も同じく確かめたとみなす |
| 検出（401） | 詳細設計書の「認証の失敗の確かめ」（画面の引き金と、会話の行の最後の `assistant` の記録）で確かめる。キーの子でこれを打つのは `_key_auth` で、`_auth_pick` ではない。確かめたら、旧子を止める前に、helper と同じ錠の中で記録へ拒否の印 `{"id": <helper の ID>, "deny": true}` を足す（錠が取れない・書けないときもそのまま止める） |
| 検出の順 | `_loop` の (2) の `_drift` の直後、(3) の停止の判定より前に、キーの子だけ `_key_auth(sec, screen)` を打つ（返りは無く、フラグだけを立てる）。確かめたら、今の起動先を `tried` に入れ、`Section.end_cause` を `auth` にして `must_end`・`must_end_at`・`end_now` を立て、入力の転送を止める。候補も `after_auth` も読まず、`hold` もしない。同じ見回りの (3) で `_end_point` が `end_now` で真になり、`_stop_revoked` が旧子を止める |
| 同時の当たり | `must_end` が既に `drift` で立っているとき、または同じ見回りの `_drift` がキーの削除・登録し直し・`off` だけを当てて `must_end` を立てた（`end_now` は偽）ときは、helper の `out: false`・記録の異常・401 のどれでも `end_now` を立てず、`end_cause` も `drift` のままにして、下の「止める時点」の待ちを続ける。削除・`off` の後の helper の失敗はキーを出さない結果で、401 は変更前の宛先がキーを断った結果であり、どちらも許していない宛先へ送る経路ではないためである。401 のときも拒否の印は足す。逆に、`_key_auth` が先に `end_now` を立てた後に `off`・削除を検出しても `end_now` は下ろさない。`_drift` が `end_now` の当たりを当てた見回りは、`_key_auth` の結果によらず `end_now` が立っている |
| `_auth_pick` | キーの子では何もせずに None を返す（`hold` も `after_auth` も打たない）。`_state` はキーの子へ `auth_pick=None` を渡すので、キーの子に `stalled` の手は返らない |
| 止め方 | 旧子を止めるのは `_loop` の (3) の `_stop_revoked` だけで、下の「止める時点の表」の `_end_point` が真の行と同じ順である。止めた見回りの `_state` で `_revoked_pick` が初めて替え先を読み（起動の理由 `revoked`。今の起動先は `tried` にあるので除く）、`_revoked` が手を返す。替え先が無ければ、`_on_revoked` が、`end_cause` が `auth` のとき直し方の 1 行（`tried` の名前と名前ごとの直し方。詳細設計書の「試して失敗した接続先の規則」）を出してから `Ended("account_unavailable")` で終える |
| 終点 | 替え先の起動（`--resume`）か `Ended`（`account_unavailable`・`refused`・`spin`）のどれかで必ず終わり、子を残したまま待つ経路は無い |
| 時間の上限 | 確かめた見回り（401 を確かめた見回りか、`out: false` の行・記録の異常・失敗の印・`gate` の行を読んだ見回り）から旧子の終わりまで、`relay.child-exit` の 5 秒以内（検出と停止は同じ見回りの (2)〜(3)）。記録の印が変わってから読むまでの最大 `relay.tick` の 0.5 秒と合わせて 5.5 秒である |
| 払い出しの回数 | 1 つの helper の ID でキーを出すのは、どの 60 秒の間でも 3 回までで、拒んだ後と拒否の印の後は 0 回である。401 の打ち直しの間（実測で会話の記録の最後の `assistant` が書かれるまで約 256 秒）も、この上限で 4 回目の呼び出しが終了コード 1 になり、上の検出に入る |
| CLI の起動の回数 | 払い出しの上限が縛るのはキーを出す回数だけで、helper の CLI の起動（`ndf` の起動 1 回）の回数は縛らない。拒んでから旧子が止まるまでの起動の回数は Claude Code の呼び直しの間隔で決まり、最短の間隔は公開されていないので設計からは導けない。そのため U11a の PR で、手元の Claude Code の対話の子を使い、(i) helper が最初から終了コード 1（指紋の合わない宣言）、(ii) helper はキーを出し模擬の API が 401、の 2 通りで、子の起動から止まるまでの helper の起動の回数と間隔を測り、PR の本文に残す。受け入れ条件は、1 つのセグメントで (i) が計 4 回以下（拒んだ 1 回と、止まるまでの 3 回）、(ii) が計 6 回以下（払い出し 3 回と、止まるまでの 3 回）である。超えたら実装を止めて設計へ戻す（止めるまでの時間を縮めるか、拒んだ起動を軽くする） |
| 入れ子の `ndf plan` | relay の子の Claude Code の中で打った `ndf plan` の `call_claude` は、`build` を `record=None` で打つので、内側の helper は `--record`・`--id` を持たない。内側の helper は外側の記録を書かず、外側の ID の払い出しの枠を使わず、外側の子の停止を起こさない（外側の helper の文字列は外側の子の起動引数にだけあり、環境で継がれない）。内側の取り直しは `call_claude` の timeout で必ず終わる。`call_claude` の子の環境から `NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH` を外すかは、この設計では変えない（設計文書の「人へ残す判断」の 11） |

### helper の呼び出しの回数と所要時間

正常時の追加のコストは、起動の時点の 1 回（`claude -p` の 1 回の実行ごとに 1 回）と、動いているセッションで Claude Code の既定の有効期間の 5 分ごとに最大 1 回（1 時間で最大 12 回）である。1 回は `ndf` の起動 1 回で、`api-key-helper` は登録簿の読みと宣言の解釈と記録の 1 行だけにし、ほかの部品を読み込まない（起動の時間を `ndf relay account --help` の水準より増やさない）。認証の失敗の間の回数は、上の表の「CLI の起動の回数」で縛る。

| 経路 | 初回の取得 | 再取得 | 認証の失敗の間 |
| --- | --- | --- | --- |
| 対話のセッション | 起動の時点で 1 回（入力の前） | 最初の取得から 5 分を過ぎた後の最初の要求で 1 回。空いている間は呼ばれない | 取り直しが続く |
| `claude -p`（`ndf plan` と同じ起動の形） | 1 回の応答で 1 回 | 同じ規則 | 同上。`call_claude` の timeout で終わる |

表の回数と、`ndf relay account --help` の 1 回が 0.65〜0.75 秒（最初の 1 回は 1.1 秒）という所要時間は、U1 の PR #4018 の修正担当が手元（2026-11-22 JST、Claude Code 2.1.330）で、キーを出すだけのスクリプトを `apiKeyHelper` にし、応答を返す模擬の API を `ANTHROPIC_BASE_URL` に向けて数えた値である（無効なキーでは 120 秒に 9 回、平均約 13 秒の間隔）。測った手順と出力は残っておらず、未確認として扱う。U11a の PR で、上の「CLI の起動の回数」の実測と一緒に測り直し、PR の本文に残す。

## 設定の変更でキーの子を止める範囲

詳細設計書の「起動の後の設定の変更」で印が変わり、打ち直しで当たったときの、キーの子の扱いである。キーの子は替え先の有無によらず `_revoked` の経路で止める（`stalled` にしない）。helper の指紋はキーの差し替えだけを止め、宛先・ヘッダー・別の資格情報の追加・変更・削除は止めないためである。

| 当たり | 止める時点 | 理由 |
| --- | --- | --- |
| 登録簿のキーの削除・同じ名前の登録し直し（`generation` が起動のときと違う）・使い方の `off`（`key:<名前>` だけ） | 下の「止める時点」の待ち（止めてよい時点か `relay.end-wait` の 60 秒の早い方） | キーは変更前の宛先にしか送られない。登録し直した新しいキーは、helper が起動のときの世代（`--generation`）と違うので出さず、動いているセグメントでは送られない |
| 上の 2 つのほかの、送信先や認証を変え得る不一致すべて | 検出した見回り（`end_now`） | 道具の実行の途中でも、その結果を使う次の要求は利用者の入力なしに出て、変更後の宛先や資格情報でキーと会話を送る |

2 行目に入るのは、宛先・ヘッダー・資格情報・提供元の選択の変数・`apiKeyHelper` の追加に加え、既存の値の変更と削除である。たとえば、宣言した `ANTHROPIC_BASE_URL` を設定ファイルの `env` が別の URL へ置き換える・消して既定の宛先へ戻す、管理の設定が `--settings` の helper より上に別の `apiKeyHelper` を置く、がどれも当たる。API キーの宣言の従量の子では、宣言との照合（「従量の実効値の照合」）の外れすべてが当たる。

### 反映の前に拒む（`ConfigChange` の hook・`config-gate`）

**キーの子では、利用者・プロジェクト・ローカルの設定の変更を、Claude Code が実行中のセッションへ反映する前に拒む。** 見回りで検出して止めるだけでは、保存の直後に道具の結果が返ると、次の見回り（最大 `relay.tick` の 0.5 秒後）より先に、キャッシュ済みのキーと会話が変更後の宛先へ送られる。`end_now` は検出した後の待ちを省くだけで、送信の前の制約にはならない。

| 規則 | 中身 |
| --- | --- |
| hook の合成 | `build` は、キーの子の `--settings` の合成に、helper と並べて `hooks.ConfigChange`（matcher 無し）の command の hook を 1 つ足す。文字列は `shlex.join([<ndf の実行ファイルの絶対パス>, "relay", "account", "config-gate"])` に、ラッパーの起動では helper と同じ `--record <記録先> --id <helper の ID>` を付けたもの。照合用の起動引数と子の実際の起動引数の両方に同じ条件で入れる |
| 拒み方 | Claude Code は、実行中のセッションで設定ファイルの変更を反映する前に `ConfigChange` の hook を打ち、hook が終了コード 2 か `{"decision": "block"}` を返すと変更を反映しない。`config-gate`（`cli_account._acc_config_gate`。新設）は標準入力の `source` が `policy_settings` 以外なら、中身を読まず・宛先に関わるかを判定せずにすべて拒む（判定の誤りで反映させない）。入力を読めない・解けないときも終了コード 2 で拒む |
| 記録 | 拒んだら、helper と同じ錠の中で記録へ `{"id": <helper の ID>, "gate": <source>}` を足す（錠・追記の失敗は helper と同じく失敗の印 `key-helper.fail` へ）。`_key_auth` はこの行を helper の `out: false` と同じく確かめたとみなし、`end_now` で子を止める。拒んだ変更はファイルには残るので、替え先の起動で起動の前の照合（`verify`）に当てられる |
| 拒めない変更 | `policy_settings`（管理の設定）の変更は Claude Code が拒ませない。これは今の見回りの検出（上の表の 2 行目で `end_now`）に残り、検出までに送られる要求は設計文書の「レッドラインの表」の行に残す。管理の設定を書けるのは管理者だけで、脅威（うっかりの利用者と権限の無い人）の外にある |
| 起動の前の確かめ | `verify` は、実効の設定で `disableAllHooks` が真か、管理の設定の `allowManagedHooksOnly` が真で `--settings` の hook が働かないときは `key_gate` で外し、理由の 1 行（項目名と出所だけ）を出す（関所の無いキーの子を起動しない）。`disableAllHooks` を後から足す変更も `ConfigChange` を通るので拒まれる |
| 入れ子の `ndf plan` | `record=None` の起動では `--record`・`--id` を付けず、`config-gate` は拒むだけで記録を書かない（`claude -p` は 1 回の応答で終わり、外側の子の停止を起こさない） |
| 確かめ | Claude Code が反映の前に hook を打つこと・拒んだ変更の `env` が要求に使われないことは公開の文書（hooks の `ConfigChange`）による。U11a の PR で、手元の Claude Code の対話の子で、下の単体テストの観点の「設定の変更」の (iv) の順を実際に起こし、模擬の新しい宛先が要求を 1 件も受けないことを確かめて PR の本文に残す。受けたら実装を止めて設計へ戻す |

### 止める時点

キーの子で `_drift` が当たるか、`_key_auth` が 401・helper の失敗を確かめると、次の順に進める。上限・認証の失敗・再起動ループの検出・次のセグメントのマーカーのどれと重なっても、この順を先にする（決定 12・13）。

| 時点 | すること |
| --- | --- |
| 当たった見回り | `Section.must_end`・`must_end_at` を立て、当たりが上の表の 2 行目か `_key_auth` の確かめなら `end_now` も立てる（`end_now` なら同じ見回りで `_end_point` が真になり、`_state` より前に下の行へ進む。`_loop` の (2)・(3)）。以後このセグメントの間、利用者の入力・最初のプロンプト・質問の回答を子の端末へ送らない。ラッパーが控えている未送の入力も送らずに捨て、画面に 1 回 `ndf-relay: API キーの設定が変わったので入力を子へ送らない。子が止まるのを待つ（入力は替えた後に打ち直す）` を出す。捨てた入力は後継にも渡さない |
| `_end_point` が真 | 先に旧子を止め、終わりを確かめてから替え先を選ぶ。`end_now` の見回りも同じ順で、候補を読む前に止める（読む間に旧子が道具の結果から次の要求を出さないため）。(1) `_stop_revoked` がセグメントの終わりの時刻を書き、質問の hook と同じロックの中で旧子の起動 ID を外し、`_stop_if_clear` の `held`（ロックを取れない・質問のファイルが在る）で見送らずに `_stop_child` で子を止める（`must_end` の間は答えが届かないので、質問を残しても待ちが終わらない。ロックを取れなくても、ロックの操作が `OSError` で落ちても止めるのを先送りせず、止めた後にロックの中で外し直す）。旧子の質問のファイルも起動 ID を外したのと同じロックの中で消す。終わりを確かめられない・起動 ID を外したと確かめられない・質問を消したと確かめられなければ `Ended("refused")` で終え、替え先を選ばず後継を起動しない。(2) 確かめられたら、同じ見回りで `_revoked_pick` が替え先を取り直す（起動の理由 `revoked`。今のキーは除く。ただし同じ名前の登録し直しで当たったときは、新しい世代のその名前を候補に残す。読み取り・照合が落ちれば替え先無し）。(3) `_revoked` が 1 つの手を返す。替え先があり再起動ループの検出に当たらなければ `account`（替え先で `--resume`）、当たれば `spin`（`_on_spin` が後継を起動せずに `Ended("spin")`）、替え先が無ければ `revoked`（`_on_revoked` が `Ended("account_unavailable")`）。3 つの手はどれも (1) の後にだけ返り、子を止め直さない。道具の実行の途中で止めた場合も、会話の記録に残った分から `--resume` で続ける |

「外して消す」は、`signals.set_launch(self.dir, None)` を打ち、`signals.current_launch` が None かを確かめ、続けて同じロックの中で質問のファイル（`self.dir / signals.QUESTION_FILE`）を `unlink(missing_ok=True)` で消し、消えたこと（在らないこと）を確かめる処理である。起動 ID を外した後は旧子の hook が質問を書けないので、同じセグメントで消せば後から質問が戻らない。`signals.drop_question` は自分でロックを取るので、このロックの中では呼ばない。`key:key-main` の子で `ndf relay account api-key-use off` を打った直後の例は、詳細設計書のラッパーの単体テストの観点の決定 12 の行が持つ。

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `metered_kind`・`_metered` の検証 | Bedrock の宣言で `CLAUDE_CODE_USE_BEDROCK`、`ANTHROPIC_API_KEY` だけの宣言と `ANTHROPIC_BASE_URL` を添えた宣言で `API_KEY_KIND` | `CLAUDE_CODE_USE_BEDROCK=1` に `CLAUDE_CODE_USE_VERTEX=0` を添えた宣言で `CLAUDE_CODE_USE_BEDROCK`。`CLAUDE_CODE_USE_BEDROCK=0` と `ANTHROPIC_API_KEY` で `API_KEY_KIND` | 上の「None になる組み合わせ」の表の 4 行が、それぞれ None で設定なしとして扱われ、`_tell_at_start` が 1 行。ヘッダーの宣言では、その行に `ANTHROPIC_CUSTOM_HEADERS` の名前とヘッダーを足せない旨が出る。どの行にも値が出ない。保存した設定の `check_decl` が API キーの宣言を拒む |
| `metered_env`（API キーの宣言） | 子の環境に `ANTHROPIC_API_KEY` が無く、宣言の `ANTHROPIC_BASE_URL` が入る | 親だけにある `ANTHROPIC_BASE_URL`・`ANTHROPIC_CUSTOM_HEADERS`・`AWS_PROFILE` が子に無い。提供元の環境変数の宣言（Bedrock）では、親の AWS の資格情報の選択が残る | `extra` に `NDF_CLAUDE_ACCOUNT` を入れても子の値は `metered` |
| `build`・`verify`（API キーの宣言） | 照合用の起動引数と子の実際の起動引数の両方に `api-key-helper metered <指紋>` の helper がある。指紋が宣言のキーの SHA-256 で、起動引数とファイルにキーの値が無い | 子の起動引数に既に `--settings` があれば 1 つの JSON に合成する | 管理の設定の `apiKeyHelper` が `--settings` の helper より上に来る・設定ファイルの `env` が `NDF_SUPERVISE_CLAUDE_FALLBACK` を別の宣言に替えるか消す・設定ファイルの `env` が `ANTHROPIC_CUSTOM_HEADERS` を足すと、`metered_overridden` で候補から外れ 1 行（名前と出所だけ） |
| `_acc_key_helper`（`metered`） | 指紋の合う宣言でキーの値だけを標準出力へ出し、終了コード 0 | 指紋の合う宣言を、設定の `env` で同じ値のまま渡しても出す | 指紋が違う・宣言が無い・壊れている・提供元の宣言で、何も出さずに終了コード 1。標準エラーと記録の行にキーの値も指紋も無い |
| helper の記録と払い出しの上限 | 偽の claude が helper を 0.1 秒ごとに呼ぶ子（偽の時計）で、キーを出すのが 3 回で、4 回目から終了コード 1 と `out: false` の行になり、61 秒後の呼び出しも出さない | 同じ ID で helper を 10 本同時に起動しても `out: true` の行は 3 行以下で、キーを出した本数も 3 本以下。1 回目の払い出しの後に 401 を確かめると拒否の印が書かれ、その後の helper は出さない | 記録を別のファイルへの symlink・ハードリンクに替える・消すと、helper はキーを出さず、指した先のファイルの中身と更新時刻が変わらず、ラッパーは記録の異常として同じ見回りで子を止める。設定ファイルの `env` で `NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH` を別の値にしても、helper は起動引数の記録先へ書き、指定先に `key-helper.jsonl` ができない。`--record` だけ・`--id` だけで終了コード 1。正常な記録ファイルのまま、テストが記録の `flock` を持ち続けて helper の錠の取得を 0.5 秒で打ち切らせると、記録の行は増えず、helper は終了コード 1 で `key-helper.fail` に今の ID の行を足し、ラッパーはその印を読んだ見回りの (2)〜(3) で子を止め（5.5 秒以内）、`stalled` にならない。追記だけを失敗させた場合も同じ。保存先のディレクトリを書けなくして印も作れないときは、helper は終了コード 1 で、ラッパーはディレクトリの権限の変化を記録の異常として止める |
| `_key_auth`・`_loop` の順 | `out: false` の行を読んだ見回り・401 を確かめた見回りから 5.5 秒以内に子が止まり、替え先があれば替え先で `--resume`、無ければ直し方の 1 行と `account_unavailable` で終わり、`stalled` にならない | `_key_auth` が当たった見回りで、`_stop_revoked` より前に候補の読み取り（`_inputs`・`ready`・`fallbacks` の偽）が 1 回も呼ばれず、`_auth_pick` が `after_auth` を呼ばない。指紋の合わない宣言の子では、最初の呼び出しの `out: false` で同じ経路に入る | 同じ見回りで `off` と helper の `out: false`、`off` と 401 を当てると、どちらも `end_now` が偽のまま、`_end_point` が真になるまで子を止めない（401 では拒否の印は書かれる）。`key:k1`・`key:k2` が両方失効しているとき、`key:k1` → `key:k2` の後に子を止め、2 つの名前を並べた直し方の 1 行を出して `account_unavailable` で終わる |
| 設定の変更（宛先の追加と `off` を別のケースで） | (i) 道具の実行の途中（`observe.tool_pending` が真で、子の出力が続き `_safe_point` が偽の偽の子）の `key:key-main` の子で、設定ファイルの `env` へ `ANTHROPIC_BASE_URL=https://gateway.example.com` を足すと、次の見回りで当たり、同じ見回りの `_loop` の (3) で子を止める（安全な時点も `relay.end-wait` も待たない）。偽の子は道具の結果を受けたら次の要求を模擬の API へ送るが、模擬の API はその要求を 1 件も受けない | (ii) 同じ道具の実行の途中で `api-key-use off` だけを打つと、次の見回りで `must_end` は立つが `end_now` は立たず、その見回りでは子が生きている。止めるのは `_end_point` が真になった見回りで、応答の終わりで `_safe_point` が真になる場合と、終わらないまま `relay.end-wait`（60 秒、偽の時計）を過ぎる場合の 2 通りを見る | (iii) `key:key-main` の子が動いている間に、同じ名前を `add-api-key` で別のキーに登録し直すと、次の見回りで (ii) と同じく `must_end` だけが立ち、その後に Claude Code が helper を呼び直しても新しいキーは出ず（終了コード 1）、模擬の API は新しいキーの要求を 1 件も受けない。替え先の選び直しでは、登録し直した `key:key-main` を新しい世代の候補として扱う。(iv) 関所: 見回りを止めた偽の時計で、道具の実行の途中の `key:key-main` の子の利用者の設定ファイルへ `ANTHROPIC_BASE_URL=https://gateway.example.com` を保存し、見回りより先に偽の子が道具の結果を受けて次の要求を出す順にすると、`config-gate` が拒み（終了コード 2 と `gate` の行）、要求は元の宛先へ行き、模擬の新しい宛先は 1 件も受けない。見回りを進めると `gate` の行で `end_now` が立って子が止まる。`disableAllHooks` を実効の設定に持つ環境では、`verify` が `key_gate` で外して起動しない。`ANTHROPIC_BASE_URL=https://gw-a.example.com` を宣言した API キーの宣言の従量の子で、設定ファイルの `env` がそれを `https://gw-b.example.com` へ置き換える・消す、管理の設定が `--settings` の helper より上に別の `apiKeyHelper` を置くと、それぞれ次の見回りで当たり、`end_now` で止めてよい時点を待たずに子を止める。どのケースも、登録済みの替え先があればそちらで `--resume`、無ければ `account_unavailable` で終わり、キーの子を起動し直さない |
| 入れ子の `ndf plan` | relay の子の環境（`NDF_RELAY_DIR`・`NDF_RELAY_LAUNCH` を持つ）から `key:k1` の `call_claude` を打つと、偽の `claude -p` の起動引数の helper に `--record`・`--id` が無い | 内側の helper が何回呼ばれても外側の `key-helper.jsonl` の行が増えない | 内側の helper が終了コード 1 を返しても、外側の子が止まらない |
