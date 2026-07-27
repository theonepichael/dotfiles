-- ~/.config/nvim/init.lua

-- Leader keys must be set before lazy.nvim loads any plugin
vim.g.mapleader = " "
vim.g.maplocalleader = "\\"

-- Disable netrw early (nvim-tree replaces it)
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1

-- Core configuration (order matters: options before plugins so plugin
-- windows inherit correct defaults)
require("config.options")
require("config.lazy") -- bootstraps lazy.nvim and loads lua/plugins/*.lua
require("config.diagnostics")
require("config.keymaps")
require("config.autocmds")
require("config.utils.url")
-- require("config.shellcheck_qf").setup() -- opt-in shellcheck-on-save

-- Notes on what was removed from the old init.lua:
--  * The manual package.path mangling is gone. stdpath("config")/lua is
--    already on the runtimepath, so modules are required with their full
--    names ("config.functions", never bare "functions"). The old dual
--    paths caused the same file to load twice as two module instances.
--  * vim.g.clipboard xclip block removed: Neovim 0.10+ auto-detects
--    xclip/wl-copy; see clipboard settings in config/options.lua.
--  * vim.lsp.set_log_level("debug") removed: it permanently grew the LSP
--    log. Re-enable temporarily with :lua vim.lsp.set_log_level("debug")
--    when actually debugging.
