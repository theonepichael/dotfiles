# AGENTS.md — scripts

<!-- Paired with CLAUDE.md symlink in the same directory -->

## Responsibilities & Boundary

Repo-maintenance entrypoints run directly by the user or installer — not
harness-runtime code. None of these scripts have a `links.toml` entry or get
installed into harness config directories.

## Hazards & Signposts

- `install-with-agent-toolkit.sh` must be used for installations on this dual-checkout
  machine. It exports `AGENT_TOOLKIT_INSTALL_WRAPPER` to run agent-toolkit first and
  dotfiles second, guaranteeing that dotfiles' composed `global-instructions.md`
  always asserts the personal overlay across all harnesses. Direct unwrapped calls to
  `agent-toolkit/install.py` are blocked.
- `opencode_skills_sync.py` manages skill configuration sync for OpenCode.

## Local Conventions

Shell scripts must be POSIX-compliant (`#!/usr/bin/env sh`) or explicitly bash if
required. Python scripts must use the standard library only and conform to `STYLE.md`.
