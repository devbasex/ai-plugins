source plugins/ndf/scripts/lib/worktree-common.sh
cmd='( cd .worktrees/x; echo \); sed -i s/a/b/ README.md )'
wt_extract_write_target "$cmd" "/base"
