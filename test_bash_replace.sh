cmd='echo "foo && bar"'
spaced=${cmd//&&/__WT_ANDAND__}
spaced=${spaced//__WT_ANDAND__/"&&"}
echo "$spaced"
