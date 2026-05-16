#!/usr/bin/env zsh

# ============================================================================
# HISTORY
# ============================================================================

skip_global_compinit=1

HISTFILE=~/.zsh_history
HISTSIZE=100000
SAVEHIST=100000

setopt SHARE_HISTORY
setopt APPEND_HISTORY
setopt INC_APPEND_HISTORY
setopt HIST_IGNORE_DUPS
setopt HIST_FIND_NO_DUPS
setopt HIST_REDUCE_BLANKS
unsetopt beep

bindkey -e

# ============================================================================
# COMPLETION
# ============================================================================

fpath=(~/.zsh/completions $fpath)

autoload -Uz compinit
compinit

zstyle ':completion:*:*:git:*' verbose true
zstyle ':completion:*:*:git-checkout:*' sort false
zstyle ':completion:*:descriptions' format '%F{yellow}%B%d%b%f'
zstyle ':completion:*:*:git-checkout:*:*' command 'git branch -v --sort=-committerdate'
zstyle ':completion:*:*:git:*:*' group-order 'common-commands' 'alias-commands' 'aliases'

# ============================================================================
# PATH
# ============================================================================

for pathdir in "$HOME/.local/bin" "$HOME/bin" "$HOME/.npm-global/bin" "/opt/homebrew/opt/python@3.13/libexec/bin"; do
    if [[ -d "$pathdir" && ":$PATH:" != *":$pathdir:"* ]]; then
        PATH="$pathdir:$PATH"
    fi
done

export NVM_DIR="$HOME/.nvm"
[[ -s "$NVM_DIR/nvm.sh" ]] && source "$NVM_DIR/nvm.sh"

export PATH

# ============================================================================
# SECRETS
# ============================================================================

[[ -f ~/.secrets ]] && source ~/.secrets

# ============================================================================
# COMMON ALIASES
# ============================================================================

if [[ -f "$HOME/.common_shell_aliases" ]]; then
    source "$HOME/.common_shell_aliases"
fi

# ============================================================================
# OH-MY-POSH
# ============================================================================

if command -v oh-my-posh >/dev/null 2>&1; then
    eval "$(oh-my-posh init zsh --config ~/.poshtheme.omp.json)"
fi

# ============================================================================
# ZOXIDE (must be last)
# ============================================================================

_zoxide_preview() {
    local path="${1##*$'\t'}"
    if [[ -d "$path" ]]; then
        if command -v eza >/dev/null 2>&1; then
            eza -lh "$path" | head -20
        else
            ls -lh "$path" | head -20
        fi
    else
        echo "[missing dir] $path"
    fi
}

if command -v zoxide >/dev/null 2>&1; then
    eval "$(zoxide init zsh)"
fi
