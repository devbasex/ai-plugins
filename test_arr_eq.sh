source /tmp/ndf-worktrees/devbasex--ai-plugins/pr191/plugins/ndf/scripts/lib/worktree-common.sh
cmd="FOO=(a b c) cd /tmp && sed -i 's/a/b/' README.md"
wt_extract_write_target "$cmd" "/base"
