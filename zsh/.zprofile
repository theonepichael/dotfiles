# === Environment Setup ===
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

# === Secrets (not tracked in dotfiles) ===
[[ -f ~/.secrets ]] && source ~/.secrets
