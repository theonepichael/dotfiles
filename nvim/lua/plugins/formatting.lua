-- ~/.config/nvim/lua/plugins/formatting.lua
-- SINGLE format-on-save path. The old setup formatted Lua buffers up to
-- three times per save (conform.format_on_save + a BufWritePre autocmd
-- in conform.lua + another BufWritePre autocmd in autocommands.lua).
return {
  {
    "stevearc/conform.nvim",
    event = "BufWritePre",
    cmd = "ConformInfo",
    opts = {
      formatters_by_ft = {
        -- FIX: lua = {} means "no formatter" in conform, NOT "use LSP".
        -- Omitting lua entirely + lsp_format = "fallback" below is what
        -- actually routes Lua formatting to lua_ls.
        python = { "isort", "black", "ruff" },
        xml = { "xmlformatter" },
        -- FIX: yamlfix AND yamlfmt ran sequentially; one is enough
        yaml = { "yamlfmt" },
        typescript = { "prettierd" },
        javascript = { "prettierd" },
        json = { "jq" },
        -- FIX: shellcheck and shellharden are linters/hardeners, not
        -- conform formatters; they made every bash save error out
        bash = { "shfmt" },
        sh = { "shfmt" },
      },
      default_format_opts = {
        -- FIX: lsp_fallback was renamed lsp_format = "fallback"
        lsp_format = "fallback",
      },
      format_on_save = {
        timeout_ms = 2000,
      },
      log_level = vim.log.levels.ERROR,
    },
    -- REMOVED vs the old conform.lua:
    --  * a full third lua_ls setup (now consolidated in lsp/lua_ls.lua)
    --  * side-effect code that WROTE an .editorconfig file into your
    --    config repo on startup (Neovim 0.11 reads .editorconfig natively;
    --    commit one to the repo if you want it)
    --  * on_attach forcing documentFormattingProvider = true
  },
}
