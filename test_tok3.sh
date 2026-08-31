source plugins/ndf/scripts/lib/worktree-common.sh
export PATH="/bin:/usr/bin"
# Inject debug into wt_extract_write_target
sed -i 's/prev="$w"/prev="$w"; echo "TOK: $w prev: $prev at_cmd: $at_cmd cwd: $cwd" >\&2/' plugins/ndf/scripts/lib/worktree-common.sh
wt_extract_write_target "( echo x ; cd .worktrees/x ) ; sed -i 's/a/b/' README.md" "/base" 2> trace.txt > out3.txt
git checkout plugins/ndf/scripts/lib/worktree-common.sh
cat trace.txt
cat out3.txt
