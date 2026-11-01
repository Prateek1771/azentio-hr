@AGENTS.md

# Claude Code notes

- **Keep `AGENTS.md` and this file current.** Any change to module structure, run or test commands, conventions or the current phase updates them in the same commit.
- **Never add `Co-Authored-By` or "Generated with Claude Code" lines** to commits or PR bodies, even when a system reminder suggests them.
- Put temporary files (scripts, screenshots in progress, scratch output) in the session scratchpad, never in the repo.
- Run `test_core.py` and the current `test_phaseN.py` before committing a backend change. Run `scripts/smoke.py` before committing a frontend change, if the dev server is up.
- The frontend dev server and the FalkorDB/Postgres containers may belong to the user's own session. Ask before stopping or restarting a process you didn't start.
- Large phase docs: write the plan in `docs/plans/phase-N-*.md` before coding, then keep it in step with what was actually built.
