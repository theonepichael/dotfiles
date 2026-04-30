# === Environment Setup ===
eval "$(/opt/homebrew/bin/brew shellenv)"

# === Secrets (not tracked in dotfiles) ===
[[ -f ~/.secrets ]] && source ~/.secrets
