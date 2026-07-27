-- ~/.config/nvim/lua/config/diagnostics.lua
-- Single source of truth for diagnostics display and shared LSP keymaps.
--
-- FIX: vim.diagnostic.config() is GLOBAL. The old config called it from
-- inside lua_ls, jedi and typescript-tools on_attach callbacks with
-- slightly different values, so display behavior depended on which
-- server attached last. Configure it exactly once, here.

vim.diagnostic.config({
  virtual_text = false,
  signs = true,
  underline = true,
  update_in_insert = false,
  severity_sort = true,
  float = {
    border = "rounded",
    -- FIX: source = "always" is deprecated; boolean on 0.11
    source = true,
    header = "",
    prefix = "",
  },
})

-- Auto-show diagnostic float on hover.
-- FIX: previously registered via vim.cmd from three on_attach callbacks,
-- creating duplicate un-grouped autocmds (one per attach). One grouped
-- autocmd, registered once.
vim.api.nvim_create_autocmd("CursorHold", {
  group = vim.api.nvim_create_augroup("DiagnosticFloat", { clear = true }),
  callback = function()
    vim.diagnostic.open_float(nil, {
      focusable = false,
      close_events = { "BufLeave", "CursorMoved", "InsertEnter", "FocusLost" },
      scope = "cursor",
    })
  end,
})

-- Shared LSP keymaps, applied on every attach instead of duplicated in
-- each server config. (K, grn, gra, gri, grr are already Neovim 0.11
-- defaults; the leader variants below preserve your muscle memory.)
vim.api.nvim_create_autocmd("LspAttach", {
  group = vim.api.nvim_create_augroup("LspKeymaps", { clear = true }),
  callback = function(args)
    local buf = args.buf
    local function map(lhs, rhs, desc)
      vim.keymap.set("n", lhs, rhs, { buffer = buf, silent = true, desc = desc })
    end
    map("gD", vim.lsp.buf.declaration, "Go to declaration")
    map("gd", vim.lsp.buf.definition, "Go to definition")
    map("gi", vim.lsp.buf.implementation, "Go to implementation")
    map("<leader>sh", vim.lsp.buf.signature_help, "Signature help")
    map("<leader>rn", vim.lsp.buf.rename, "Rename symbol")
    map("<leader>ca", vim.lsp.buf.code_action, "Code actions")
    map("<leader>lf", function()
      -- Route through conform so the same formatter chain runs on
      -- keypress and on save (falls back to LSP when no formatter).
      require("conform").format({ async = true, lsp_format = "fallback" })
    end, "Format buffer")
  end,
})
