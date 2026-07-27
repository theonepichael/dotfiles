-- ~/.config/nvim/lua/plugins/lsp.lua
-- Neovim 0.11+ native LSP setup: server settings live in lsp/*.lua at
-- the config root and are activated with vim.lsp.enable(). This replaces
-- the old arrangement where lua_ls was configured THREE times (in
-- plugin/setup.lua, lua-lsp.lua and conform.lua) with conflicting
-- settings, and java_language_server was configured with the jdtls
-- binary (two different, incompatible servers).
return {
  {
    "neovim/nvim-lspconfig", -- provides base configs consumed by vim.lsp.config
    dependencies = {
      -- FIX: repos moved from williamboman/* to mason-org/*
      { "mason-org/mason.nvim", opts = {
        ui = {
          icons = {
            package_installed = "✓",
            package_pending = "➜",
            package_uninstalled = "✗",
          },
        },
      } },
      {
        "mason-org/mason-lspconfig.nvim",
        -- FIX: was lazy = true with a config function that therefore
        -- never ran, so ensure_installed never executed. Also removed
        -- "prettier" (a formatter, not an LSP — invalid here), "pyright"
        -- (you use jedi) and "ts_ls" (typescript-tools owns TS).
        opts = {
          ensure_installed = { "lua_ls", "jedi_language_server" },
          -- mason-lspconfig v2 auto-runs vim.lsp.enable() for installed
          -- servers, picking up lsp/lua_ls.lua etc. automatically
          automatic_enable = true,
        },
      },
      "saghen/blink.cmp",
    },
    config = function()
      -- Extend every server's capabilities with blink.cmp completion
      vim.lsp.config("*", {
        capabilities = require("blink.cmp").get_lsp_capabilities(),
      })

      -- jdtls: only if the real binary exists (see lsp/jdtls note in
      -- after/ftplugin/java.lua). Not mason-managed here on purpose.
      if vim.fn.executable("jdtls") == 1 then
        vim.lsp.enable("jdtls")
      end
    end,
  },

  {
    "folke/trouble.nvim",
    cmd = "Trouble",
    keys = {
      { "<leader>xx", "<cmd>Trouble diagnostics toggle<cr>", desc = "Diagnostics (Trouble)" },
      { "<leader>xX", "<cmd>Trouble diagnostics toggle filter.buf=0<cr>", desc = "Buffer Diagnostics (Trouble)" },
      { "<leader>cs", "<cmd>Trouble symbols toggle focus=false<cr>", desc = "Symbols (Trouble)" },
      { "<leader>cl", "<cmd>Trouble lsp toggle focus=false win.position=right<cr>", desc = "LSP Definitions/References (Trouble)" },
      { "<leader>xL", "<cmd>Trouble loclist toggle<cr>", desc = "Location List (Trouble)" },
      { "<leader>xQ", "<cmd>Trouble qflist toggle<cr>", desc = "Quickfix List (Trouble)" },
      { "<leader>xm", "<cmd>Trouble mydiags toggle<cr>", desc = "My Custom Diagnostics (Trouble)" },
    },
    -- FIX: the old config mixed Trouble v2 options (position, height,
    -- icons-as-strings, mode, group, indent_lines, ...) into a v3 setup,
    -- where they are invalid. Only the v3 custom mode survives; window
    -- placement is per-mode in v3.
    opts = {
      modes = {
        mydiags = {
          mode = "diagnostics",
          filter = {
            any = {
              { buf = 0 },
              {
                function(item)
                  if not item or not item.filename then
                    return false
                  end
                  local cwd = vim.uv.cwd()
                  if not cwd then
                    return false
                  end
                  return item.filename:find(cwd, 1, true) ~= nil
                end,
              },
              { severity = vim.diagnostic.severity.ERROR },
            },
          },
        },
      },
    },
  },
}
