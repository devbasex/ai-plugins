# #3572 の実物の確かめ: 検証用マーケットプレイスと merge queue

設計文書 [issue-3572-design.md](issue-3572-design.md) の実物の確かめ（要求の受け入れ条件 2d・2g）を置く。打ったコマンドと出力をそのまま載せる。
用語は設計文書の「用語」の表の意味で使う。

## 検証用マーケットプレイス（受け入れ条件 2d）

使い捨てのディレクトリに、正式版の `main` と検証用ブランチ `verify/next` を持つ git のリポジトリを作り、HTTPS で配った
（`git http-backend`。`claude plugin marketplace add` は `file://` と `git://` を「Invalid marketplace source format」で受け付けない）。
`HOME`・`CLAUDE_CONFIG_DIR`・`CLAUDE_CODE_PLUGIN_CACHE_DIR` を使い捨てのディレクトリへ向け、Claude Code 2.1.325 で打った（2026-11-15）。
`main` の `marketplace.json` の名前は `exp-plugins`、`verify/next` は `exp-plugins-next` である。

**確かめ 1: 同じリポジトリを違う名前と違う ref で登録すると、両方が残る**

```text
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git
✔ Successfully added marketplace: exp-plugins (declared in user settings)
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git#verify/next
Cloning repository (timeout: 120s): https://127.0.0.1:18443/repo.git (ref: verify/next)
✔ Successfully added marketplace: exp-plugins-next (declared in user settings)
$ claude plugin marketplace list
  ❯ exp-plugins       Source: Git (https://127.0.0.1:18443/repo.git)
  ❯ exp-plugins-next  Source: Git (https://127.0.0.1:18443/repo.git@verify/next)
```

登録（`known_marketplaces.json`）では、`exp-plugins` が `{"source": "git", "url": ...}` を持つ。
`exp-plugins-next` は同じ URL に `"ref": "verify/next"` を持つ。
同じプラグイン `demo` を両方から入れると、`demo@exp-plugins` と `demo@exp-plugins-next` が両方とも有効になる。
`claude plugin disable demo@exp-plugins` で片方だけを止められた。

**確かめ 2: 検証用ブランチを作り直した後に、更新で新しい中身が入る**

```text
（作り直しの前の exp-plugins-next の marketplace.json の説明）v1.1.0-pr1
$ claude plugin marketplace update exp-plugins-next
Cloning repository (timeout: 120s): https://127.0.0.1:18443/repo.git (ref: verify/next)
Replacing the existing marketplace clone…
✔ Successfully updated marketplace: exp-plugins-next
（作り直しの後）v1.2.0-pr2、head のコミット a1f94fe verify2
```

**確かめ 3: 正式版の登録が変わらない**

`exp-plugins` の `marketplace.json` の SHA-256 は、作り直しの前後とも `89aeee40…5265f7` だった。head は `2b72bc9 main1`、登録の取得元は
`{'source': 'git', 'url': 'https://127.0.0.1:18443/repo.git'}` のままだった。

**あわせて確かめたこと: 名前を正式版と同じにした ref は登録できない**

```text
$ claude plugin marketplace add https://127.0.0.1:18443/repo.git#verify/same
✘ Failed to add marketplace: Cannot add marketplace "exp-plugins": its source doesn't match its extraKnownMarketplaces entry in user or managed settings; ...
```

利用者の登録への影響: 利用者の登録の取得元は前後で同じだった（`ai-plugins` は `https://github.com/devbasex/ai-plugins.git`）。
ただし、`CLAUDE_CODE_PLUGIN_CACHE_DIR` を向ける前の 1 回（取得に失敗した試み）で、利用者の登録の `lastUpdated` が 2026-11-15T11:53:54Z
（20:53 JST）に書き換わった。取得元と ref は変わっていない。この 1 回は受け入れ条件 2d の「利用者の登録を変えない場所」を満たしておらず、
扱いを設計文書の「人へ残す判断」の C6 の行で人が判断する。使い捨ての確かめには、3 つの変数をそろえて向ける。

## merge queue（受け入れ条件 2g）

ai-plugins の main には merge queue が無く、足すには ruleset の書き換えが要る。設計の工程では ruleset を書き換えず、外部へ公開する
リポジトリも作らない（要求の「含まない」）。そのため、確かめは (1) GitHub の API の型と ai-plugins の設定を読む、(2) merge queue を使って
いる公開リポジトリの記録を読む、の 2 つで行った。キューへ入れて落とす操作は実装のときに行う（設計文書の「未確認のまま残ること」と
「人へ残す判断」の C2 の行）。どれも 2026-11-15 に打った。

**確かめ 4: キューへ入れる操作は、承認した head を引数に取れる**

```text
$ gh api graphql -f query='{ __type(name:"EnqueuePullRequestInput"){ inputFields{ name description } } }'
clientMutationId  A unique identifier for the client performing the mutation.
pullRequestId     The ID of the pull request to enqueue.            （必須）
jump              Add the pull request to the front of the queue.
expectedHeadOid   The expected head OID of the pull request.
```

`enqueuePullRequest` は `expectedHeadOid` を持つ。マージの API の `sha`（STD1）と同じ役割で、承認した head を渡せる。head が違うときの
応答の形は、キューの無いブランチでは確かめられない（キューの有無の判定が先に断る見込みで、確かめにならない）。

**確かめ 5: キューのエントリの状態と、PR の型が持つ項目**

```text
$ gh api graphql -f query='{ __type(name:"MergeQueueEntryState"){ enumValues{ name } } }'
QUEUED / AWAITING_CHECKS / MERGEABLE / UNMERGEABLE / LOCKED
$ gh api graphql -f query='{ __type(name:"MergeQueueEntry"){ fields{ name } } }'
baseCommit, enqueuedAt, enqueuer, estimatedTimeToMerge, headCommit, id, jump, mergeQueue, position, pullRequest, solo, state
$ gh api graphql -f query='{ __type(name:"PullRequest"){ fields{ name } } }'   （queue を含む項目だけ）
isInMergeQueue, isMergeQueueEnabled, mergeQueue, mergeQueueEntry
```

キューで待つ間は `mergeQueueEntry.state` を、終わった後は PR の `merged` と `isInMergeQueue` を読めば、マージされたかキューから外れたかを
区別できる。

**確かめ 6: ai-plugins の今の設定**

```text
$ gh api graphql -f query='{ repository(owner:"devbasex",name:"ai-plugins"){ visibility owner{__typename} mergeQueue(branch:"main"){ id } } }'
{'visibility': 'PUBLIC', 'owner': {'__typename': 'Organization'}, 'mergeQueue': None}
$ gh api repos/devbasex/ai-plugins/rulesets/22332172 --jq '{bypass:.bypass_actors, rules:[.rules[].type]}'
bypass: [{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"pull_request"}]
rules:  deletion / non_fast_forward / pull_request（allowed_merge_methods: ["merge"]、承認 1、dismiss_stale_reviews_on_push: false）/
        required_status_checks（17 個、strict_required_status_checks_policy: false）
$ grep -l merge_group .github/workflows/*.yml | wc -l
0
```

ai-plugins は Organization が持つ public のリポジトリで、merge queue を使える条件（STD9）に当たる。必須のチェックを出す workflow は、
キューの一時のコミットで起動する `merge_group` を 1 つも持たない。キューを足す前に、17 個のチェックを出す workflow へ `merge_group` を
足す必要がある（STD7・STD8）。

**確かめ 7: キューの一時のコミットで必須のチェックが走り、確かめたコミットがそのまま本番ブランチへ入る（公開リポジトリの記録）**

`microsoft/vscode` は main に merge queue を持つ。2026-11-15 にキューを通ってマージされた PR #348333 の記録を読んだ。

```text
$ gh run list -R microsoft/vscode --event merge_group --limit 2 --json headBranch,conclusion,headSha,workflowName
{"conclusion":"success","headBranch":"gh-readonly-queue/main/pr-348333-827998555230f67dee66b1ae1521d8398cacb933",
 "headSha":"0fbab1451fed17c969feb57b0ab596adf895ba0b","workflowName":"Code OSS"}  ほか 1 件
$ gh pr view 348333 -R microsoft/vscode --json headRefOid,state,mergeCommit
{"head":"29e2cc9d20c3352eb744c55a671f4f59de388af2","merge":"0fbab1451fed17c969feb57b0ab596adf895ba0b","state":"MERGED"}
$ gh api repos/microsoft/vscode/commits/0fbab1451fed17c969feb57b0ab596adf895ba0b --jq '[.parents[].sha]'
["827998555230f67dee66b1ae1521d8398cacb933"]
$ gh api repos/microsoft/vscode/issues/348333/timeline --jq '.[]|select(.event|test("queue|merged"))|{event,actor:.actor.login,commit_id}'
{"event":"added_to_merge_queue","actor":"benibenj","commit_id":null}
{"event":"merged","actor":"benibenj","commit_id":"0fbab1451fed17c969feb57b0ab596adf895ba0b"}
{"event":"removed_from_merge_queue","actor":"github-merge-queue[bot]","commit_id":null}
$ gh api repos/microsoft/vscode/pulls/348333 --jq '{merged_by:.merged_by.login, merge_commit_sha, head:.head.sha}'
{"merged_by":"benibenj","merge_commit_sha":"0fbab1451fed17c969feb57b0ab596adf895ba0b","head":"29e2cc9d20c3352eb744c55a671f4f59de388af2"}
```

読み取れること:

| 項目 | 記録 | 設計への反映 |
| --- | --- | --- |
| 一時のコミット | ブランチ `gh-readonly-queue/main/pr-<番号>-<main の SHA>` に作られ、`merge_group` の workflow がその SHA で走った | 必須のチェックを出す workflow に `merge_group` を足す（決定 23） |
| マージしたコミット | PR のマージコミット（`merge_commit_sha`）は、キューで確かめた一時のコミットと同じ SHA。親はキューへ入れた時点の main | 組み合わせを確かめたコミットがそのまま main へ入る（I14） |
| マージした人 | `merged_by` と `merged` の出来事の主はキューへ入れた人。外したのは `github-merge-queue[bot]` | 人がキューへ入れたときの承認した人は `merged_by` で読める（経路 (b)。決定 4） |
| PR の head | PR の head（29e2cc9）は一時のコミット（0fbab14）と別の SHA | 承認した head は PR の head で照らし、マージした結果は `merge_commit_sha` で読む（I3・I14） |

vscode のマージは squash で、一時のコミットの親は 1 つである。ai-plugins の ruleset は `merge` だけを許すため、キューの一時のコミットは
main と PR の head を親に持つマージコミットになる見込みで、これは実装のときの確かめで見る。

**GitHub Docs の記述（STD7〜STD9）**: キューの一時のコミットで必須のチェックが落ちるか、本番ブランチと衝突すると、PR はキューから外れる。
タイムラインには理由が出る（「if there are failed required status checks or conflicts with the base branch, the pull request will be removed from
the queue」）。外れる理由は、チェックの失敗・チェックの待ちの時間切れ・API か画面からの取り外し・自動で解けない保護の違反の 4 つである。
キューへ入れられるのは、PR が保護の条件（承認・必須のチェック）をすべて満たした後で、書き込みの権限を持つ利用者である。
