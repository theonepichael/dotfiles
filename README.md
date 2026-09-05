# dotfiles

Personal cross-platform system configuration for macOS and Linux/WSL. Manages shell environment (`zsh`), editor (`nvim`, `vim`), terminal (`alacritty`), window management (`tmux`, `karabiner`, `rectangle`, `vscode`), background daemons (`watchcommit`, `opencode-skills-sync`), and personal coding-agent instruction overlays.

## Shared coding-agent toolkit vs. personal dotfiles

Following the separation of the cross-harness agent toolkit into `agent-toolkit`:

- **Shared agent toolkit (`agent-toolkit`)**: The upstream repository at `~/Workspace/agent-toolkit` (or your clone location) owns shared multi-harness agent tooling: slash commands, skills, hooks, MCP server configurations, and cross-harness parity for Claude Code, GitHub Copilot, opencode, Google Antigravity, and Pi. Refer to `agent-toolkit/README.md` for shared agent tooling documentation.
- **Personal dotfiles (`dotfiles`)**: This repository owns your personal operating system configuration plus your **personal agent overlay**:
  - `claude/personal-overlay.md`: Personal workflow rules (backlog management via `dev_status.py`, worktree-first policy, proactive capture, verification standards).
  - `claude/global-instructions.md`: Composed dynamically by combining upstream core instructions with `personal-overlay.md` via `gen_core_instructions.py`.
  - `claude/settings.json` and `opencode/opencode.jsonc`: Personal settings and permission allowlist seeds.
  - Personal daemons: `watchcommit` (automatic commit and push) and `opencode-skills-sync`.

---

## Dual-repo machines: installation ordering

> **IMPORTANT: Installation on dual-repo machines**
>
> `agent-toolkit`'s installer symlinks bare upstream core instructions (`CORE_INSTRUCTIONS.md`) to `~/.claude/CLAUDE.md` and equivalent harness paths. If `agent-toolkit/install.py` runs *after* `dotfiles/install.sh`, it will overwrite and silently detach your personal overlay.
>
> On any machine that has both repositories checked out, **always install via the wrapper script**:
>
> ```sh
> ./scripts/install-with-agent-toolkit.sh --harness=claude,copilot,opencode,agy,pi
> ```
>
> The wrapper guarantees the correct installation order: it runs `agent-toolkit`'s installer first, then immediately runs `dotfiles`' installer second to reassert the composed personal overlay.

---

## Quick start (standalone)

For single-repo setups or personal machines without a separate `agent-toolkit` checkout:

```sh
git clone <repo-url> ~/dotfiles
cd ~/dotfiles
chmod +x install.sh
./install.sh --harness=claude                        # personal machine, Claude Code overlay
./install.sh --profile=work --harness=copilot         # work machine, Copilot overlay
./install.sh --harness=claude,opencode                # multiple harnesses
./install.sh --dry-run --harness=claude               # preview only, nothing written
```

`./install.sh` is a ~20-line POSIX bootstrap: it finds Python 3.12+ on PATH and hands off to **`install.py`**, which is the actual installer. You can also run `python3 install.py --harness=...` directly; flags and behavior are identical.

The symlink table lives in **`links.toml`** at the repo root. Each entry can be gated on `harness`, `platform`, `wsl`, and `profile_exclude`. Copy-once seed files (`claude/settings.json`, `opencode/opencode.jsonc`, and WSL VS Code settings) are handled directly by `install.py`.

`--harness` is required on an install run: choose any combination of `claude`, `copilot`, `opencode`, `agy`, `pi` (comma-separated). In `dotfiles`, this controls which harness personal overlays and settings seeds are wired up.

`--profile` controls machine-level concerns (`personal` by default). `--profile=work` excludes `watchcommit` and rejects `opencode`. See [Work profile](#work-profile) below.

Add `--dry-run` to preview any run (including `--rollback`) without writing or removing anything.

## What's included

| Source | Destination | Notes |
|---|---|---|
| `vim/.vimrc` | `~/.vimrc` | All platforms |
| `nvim/` | `~/.config/nvim` | Neovim 0.11+ configuration |
| `zsh/.zshrc` | `~/.zshrc` | Core shell configuration |
| `zsh/.zprofile` | `~/.zprofile` | macOS only (Homebrew shellenv) |
| `zsh/.common_shell_aliases` | `~/.common_shell_aliases` | Cross-platform shell aliases |
| `shell/agent-tools.zsh` | `~/.agent-tools.zsh` | Shell helper integrations |
| `shell/.poshtheme.omp.json` | `~/.poshtheme.omp.json` | oh-my-posh prompt theme |
| `tmux/.tmux.conf` | `~/.tmux.conf` | Terminal multiplexer config |
| `alacritty/alacritty.toml` | `~/.config/alacritty/alacritty.toml` | Terminal emulator config |
| `herdr/config.toml` | `~/.config/herdr/config.toml` | Herdr agent supervisor config |
| `karabiner/karabiner.json` | `~/.config/karabiner/karabiner.json` | macOS keyboard modifications |
| `vscode/settings.json` | VS Code user settings | Per-OS path (macOS, Linux, WSL) |
| `vscode/keybindings.json` | VS Code keybindings | Per-OS path (macOS, Linux, WSL) |
| `claude/global-instructions.md` | `~/.claude/CLAUDE.md`, `~/.copilot/copilot-instructions.md`, `~/.gemini/GEMINI.md`, `~/.pi/agent/AGENTS.md` | Composed personal instructions overlay |
| `claude/output-styles/PlainEngineer.md` | `~/.claude/output-styles/PlainEngineer.md` | Custom Claude Code output style |
| `scripts/watchcommit.py` | `~/.local/bin/watchcommit` | Background auto-commit daemon (`--profile=personal`) |
| `scripts/wc-guard` | `~/.local/bin/wc-guard` | Watchcommit pause/resume wrapper |
| `systemd/watchcommit.service` | `~/.config/systemd/user/watchcommit.service` | Linux systemd user service |
| `launchd/com.user.watchcommit.plist` | `~/Library/LaunchAgents/com.user.watchcommit.plist` | macOS launchd agent |
| `scripts/opencode_skills_sync.py` | `~/.local/bin/opencode-skills-sync` | Skills synchronization daemon (Linux personal) |
| `systemd/opencode-skills-sync.service` | `~/.config/systemd/user/opencode-skills-sync.service` | Linux systemd user service |

### Copy-once seeds

- `claude/settings.json` (or `settings.work.json` under `--profile=work`): Seeded once to `~/.claude/settings.json`. Live drift is reported in install summaries rather than overwritten.
- `opencode/opencode.jsonc`: Seeded once to `~/.config/opencode/opencode.jsonc` (`personal` profile only).
- VS Code `settings.json` and `keybindings.json`: Under WSL, copied to the Windows-side AppData roaming directory via the `code` CLI on PATH.

Use `--adopt --harness=...` to pull drifted copy-once settings back into the repository.

## The installer does

### Both platforms
1. Installs CLI packages: `tmux`, `zoxide`, `eza`, `bat`, `ripgrep`, `lsd`, `ncdu`, `tldr`, `oh-my-posh`, `neovim`, `fd`, `uv`, `ruff`.
2. Installs NVM and Node/npm if `claude` or `copilot` is selected in `--harness`.
3. Symlinks every applicable entry in `links.toml` (existing non-symlinks are backed up to `*.bak`).
4. Seeds copy-once configuration files (`claude/settings.json`, `opencode/opencode.jsonc`, WSL VS Code).
5. Bootstraps Neovim plugins (`lazy.nvim` sync) if `nvim` is >=0.11.

### macOS only
- Installs Homebrew (Apple Silicon and Intel).
- Installs casks: Karabiner-Elements, Rectangle, Ghostty, VS Code, AltTab, JetBrainsMono Nerd Font.
- Symlinks macOS configs (`.zprofile`, Karabiner, launchd plist).
- Imports Rectangle window management shortcuts.
- Maps Caps Lock → Escape via macOS modifier keys.
- Loads the `watchcommit` launchd agent (personal profile).

### Linux/WSL only
- Installs distro packages via `apt` (Ubuntu/Debian) or `dnf` (Fedora).
- Creates `~/.local/bin/bat` shim where packaged as `batcat`.
- Installs JetBrainsMono Nerd Font (pinned version) to `~/.local/share/fonts/JetBrainsMonoNerdFont`.
- Enables and starts `watchcommit.service` under systemd `--user`, enabling user lingering via `loginctl`.

## Work profile

`--profile=work` controls machine-level concerns:

- **watchcommit is excluded entirely**: No binary, no service. Watchcommit auto-pushes to personal remotes and has no place on work hardware.
- **opencode is excluded entirely**: `--profile=work --harness=opencode` is rejected at argument parsing.
- **Claude settings**: Seeded from `claude/settings.work.json` (no auto-approval bypasses, no model pin).
- **Profile marker**: Written to `~/.local/state/dotfiles/profile`. Subsequent runs with `--profile=personal` will refuse unless `--force` is provided.

## Failures, skips, and rollback

The installer never aborts on a recoverable failure. Unmet dependencies or skipped steps are highlighted in yellow in the summary; exit code is 1 if anything was skipped.

File mutations are recorded in `~/.local/state/dotfiles/history.jsonl`. `--rollback` reverses every mutation recorded there:

```sh
./install.sh --rollback
```

To perform a complete clean slate undo, removing backups and sweeping Neovim and service states:

```sh
./install.sh --rollback --wipe
```

## Auditing the symlinks (`--check-links`)

`--check-links` compares live filesystem symlinks against `links.toml`. It is read-only and safe to run at any time:

```sh
./install.sh --check-links
./install.sh --check-links --harness=claude
```

Reported categories:
- **broken-source**: Symlink exists but repo source was removed (dangling).
- **wrong-target**: Symlink points to something other than `links.toml` source.
- **not-a-symlink**: Real file or directory sits where a symlink belongs.
- **orphaned**: Live symlink recorded by a previous install that is no longer in `links.toml`. (Normal install runs remove orphans automatically; `--check-links` only reports them).
- **unmanaged**: Files in directories marked exclusive via `[[managed_dir]]` not produced by any link.

## Departure mode (`--depart`)

`--depart` removes or restores everything installed on Ubuntu/WSL (apt) or Fedora (dnf), leaving no local trace that `install.sh` was run:

```sh
./install.sh --depart              # interactive confirmation
./install.sh --depart --dry-run    # preview only
./install.sh --depart --yes        # non-interactive
```

Operates strictly from a baseline snapshot captured at install time (`~/.local/state/dotfiles/departure.jsonl`). See [Nuclear reset (WSL)](#nuclear-reset-wsl) if full image re-creation is required.

## Nuclear reset (WSL)

To completely and irreversibly wipe a WSL distribution and start from a stock image:

```powershell
# From Windows PowerShell:
wsl --list --verbose
wsl --unregister <DistroName>     # irreversibly deletes distro filesystem
wsl --install -d <DistroName>     # recreate from stock image
```

## Keyboard and window management

- **Karabiner-Elements (macOS)**: Configuration in `karabiner/karabiner.json`. Maps Caps Lock to Control when held, Escape when tapped.
- **AltTab (macOS)**: Windows-style Alt+Tab application switching.
- **Rectangle (macOS)**: Window snapping shortcuts:
  - `Ctrl+Option+Enter`: Maximize
  - `Ctrl+Option+Left/Right`: Left/Right half
  - `Ctrl+Option+C`: Center

## Personal daemons

### watchcommit

Background daemon that automatically commits and pushes dotfiles changes within ~90 seconds:
- Managed on Linux via `systemd --user` (`watchcommit.service`) and macOS via launchd (`com.user.watchcommit.plist`).
- Automatically detects active coding agents and pauses itself to avoid racing session edits.
- Use `wc-guard <command>` or `wc-pause` / `wc-resume` to manually pause synchronization during git history edits or testing.

### opencode-skills-sync

Synchronizes local skills in `~/.config/opencode/skills` to a local commit-only git branch to prevent accidental loss of interactive skill edits.

## Notes & testing

- **intel Mac**: `install.py` and `.zprofile` detect `/usr/local/bin/brew` automatically.
- **Secrets**: `~/.secrets` is gitignored — created manually per machine for environment tokens.
- **Testing**:
  - Fast tests: `uv run pytest test/ claude/scripts/` (includes `conftest.py` sandboxing to prevent real filesystem/subprocess mutations).
  - Linting: `uv run ruff check .` and `uv run ruff format --check .`.
  - Lifecycle tests: `test/run.sh` drives containerized install scenarios against Ubuntu and Fedora Docker images.
