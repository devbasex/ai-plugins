import sys

from ndf.adapters import claude
from ndf.cli import main
from ndf.domains.relay import plan_claude

# 下の層の call_claude は中継の部品を読めないので、全層より上のエントリポイントで確かめを差し込む（#3604 #3864 の U12）
claude.set_relay_checks(plan_claude)
sys.exit(main())
