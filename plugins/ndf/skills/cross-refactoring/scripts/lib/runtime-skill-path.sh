#!/usr/bin/env bash

skill_dir_for() {
  case "$1" in
    claude) echo ".claude/skills" ;;
    codex|agy) echo ".agents/skills" ;;
    kiro) echo ".kiro/skills" ;;
    *) return 1 ;;
  esac
}
