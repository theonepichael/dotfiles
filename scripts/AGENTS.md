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
- Its flag routing table (`SHARED_FLAGS`, `AGENT_TOOLKIT_ONLY_FLAGS`,
  `DOTFILES_ONLY_FLAGS`, `REFUSED_FLAGS`) must classify every flag either installer
  declares. Adding a flag to either `install.py` fails
  `claude/scripts/test_install_with_agent_toolkit.py`'s drift test until it is listed
  there. A one-sided flag must be zero-argument with no short alias, or be refused.

## Local Conventions

Shell scripts must be POSIX-compliant (`#!/usr/bin/env sh`) or explicitly bash if
required. Python scripts must use the standard library only and conform to `STYLE.md`.
