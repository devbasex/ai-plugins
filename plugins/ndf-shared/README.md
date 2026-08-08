# NDF Shared Source

`plugins/ndf-shared` is the editable source for NDF runtime plugins.

Runtime plugin directories are generated artifacts. Edit shared skills and
scripts here first, then run:

```bash
bash scripts/build-runtime-plugins.sh
```

Check generated files without modifying the working tree:

```bash
bash scripts/build-runtime-plugins.sh --check
```

## Layout

- `skills/` - shared Skill implementations.
- `skills/README.md` - frontmatter conventions for Skill authoring.
- `scripts/` - shared helper scripts copied into runtime plugins.
- `manifests/claude-skills.txt` - Claude Code published Skill set.
- `manifests/codex-skills.txt` - Codex published Skill set.
- `manifests/kiro-skills.txt` - Kiro published Skill set.

Generated runtime files must be committed with shared source changes so users
can install plugins without running the build script.
