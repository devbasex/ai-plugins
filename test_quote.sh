source plugins/ndf/scripts/lib/worktree-common.sh
cmd='echo "a \" b" | sed -i s/foo/bar/ README.md'
_wt_tokenize "$cmd"
