source plugins/ndf/scripts/lib/worktree-common.sh
wt_extract_write_target "( echo x ; cd .worktrees/x ) ; sed -i 's/a/b/' README.md" "/base"
