source plugins/ndf/scripts/lib/worktree-common.sh
export PATH="/bin:/usr/bin"
wt_extract_write_target "( echo x ; cd .worktrees/x ) ; sed -i 's/a/b/' README.md" "/base" > out3.txt
cat out3.txt
