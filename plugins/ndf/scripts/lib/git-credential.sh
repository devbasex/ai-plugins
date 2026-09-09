#!/usr/bin/env bash
# NDF plugin: credential helper が応答しない環境で git を通すための退避。
#
#   . <プラグインルート>/scripts/lib/git-credential.sh
#   ndf_git_with_fallback push -u origin <ブランチ>
#
# **`gh` は認証済みなのに `git push` だけが落ちる環境がある**（#524）。helper が
# 応答を返さないためで、`gh auth git-credential` を helper として渡すと通る。
#
# **空の値を先に置くことが退避の本体である。** `credential.helper` は複数の値を
# 持てる設定で、`git` は宣言された順に問い合わせる。空の値だけが一覧を空へ戻す。
# 先に置かないと、応答しない helper が先に当たり続け、足した経路へ到達しない
# （実測）。
#
# **既定の経路は変えない。** 1 度目は素のまま実行し、失敗したときだけ退避して
# 1 度だけ再試行する。helper が正しく動く環境の振る舞いを変えないためと、認証
# 以外の理由（参照の競合・ネットワークの不通）で同じ失敗を繰り返さないためである。

# 退避に使う `git` のオプションを 1 行 1 語で出す。**この 1 か所だけが値を持つ。**
# 手順書と実装が別々に同じ文字列を持つと、片方だけが更新される。
ndf_git_credential_fallback_args() {
  printf '%s\n' -c 'credential.helper=' \
                 -c 'credential.helper=!gh auth git-credential'
}

# `gh` を使えるか。使えなければ退避しても通らない。
ndf_gh_available() {
  command -v gh >/dev/null 2>&1
}

# `git` を実行し、失敗したときだけ退避して 1 度だけ再試行する。
# 終了コードは `git` のものをそのまま返す。
ndf_git_with_fallback() {
  git "$@" && return 0
  local rc=$?
  ndf_gh_available || return "$rc"
  echo "↻ credential helper を退避して push をやり直します（gh の認証を使う）" >&2
  local args=()
  while IFS= read -r line; do args+=("$line"); done < <(ndf_git_credential_fallback_args)
  git "${args[@]}" "$@"
}
