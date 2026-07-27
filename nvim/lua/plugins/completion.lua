-- ~/.config/nvim/lua/plugins/completion.lua
-- blink.cmp is the completion engine. The old nvim-cmp config file (and
-- its five hrsh7th/cmp-* dependencies, plus MunifTanjim/prettier.nvim)
-- are removed: nvim-cmp was commented out of the load chain, its config
-- required nvim-autopairs which was never installed, and prettier.nvim
-- duplicated conform's prettierd formatting.
return {
  {
    "L3MON4D3/LuaSnip",
    -- FIX: was pinned to exactly v2.3.0; track the v2 line instead so
    -- bugfix releases arrive
    version = "v2.*",
    dependencies = { "rafamadriz/friendly-snippets" },
    event = "InsertEnter",
    config = function()
      local luasnip = require("luasnip")

      require("luasnip.loaders.from_vscode").lazy_load()
      luasnip.filetype_extend("typescript", { "javascript" })
      luasnip.filetype_extend("typescriptreact", { "typescript", "javascript", "javascriptreact" })

      -- REMOVED vs old config: the luasnip.config.setup({ snippets =
      -- {expand/active/jump}, disable_filetype = ... }) block. Those keys
      -- are blink.cmp's `snippets` interface and nvim-cmp's
      -- `disable_filetype`, not LuaSnip options; LuaSnip ignored the
      -- whole table.

      local snippet = luasnip.snippet
      local text_node = luasnip.text_node
      local insert_node = luasnip.insert_node

      luasnip.add_snippets("all", {
        snippet({
          trig = "fence",
          name = "Code Fence",
          -- FIX: key is `desc` (or dscr); `descr` was ignored
          desc = "Create a markdown code fence with optional language",
        }, {
          text_node("```"),
          insert_node(1),
          text_node({ "", "" }),
          insert_node(2),
          text_node({ "", "```" }),
        }),
      })

      -- Snippet list command
      local has_snippet_list, snippet_list = pcall(require, "luasnip.extras.snippet_list")
      if has_snippet_list then
        vim.api.nvim_create_user_command("SnippetList", function()
          snippet_list.open()
        end, { desc = "Show available snippets for current filetype" })
        vim.keymap.set("n", "<leader>sl", "<cmd>SnippetList<CR>", {
          desc = "Show available snippets",
          silent = true,
        })
      end

      -- Manual snippet navigation when blink doesn't handle it.
      -- FIX: jumpable() is deprecated in current LuaSnip; locally_jumpable
      -- also stops you jumping back into snippets you've left.
      vim.keymap.set({ "i", "s" }, "<C-k>", function()
        if luasnip.locally_jumpable(-1) then
          luasnip.jump(-1)
        end
      end, { silent = true, desc = "Jump to previous snippet placeholder" })

      vim.keymap.set({ "i", "s" }, "<C-j>", function()
        if luasnip.locally_jumpable(1) then
          luasnip.jump(1)
        end
      end, { silent = true, desc = "Jump to next snippet placeholder" })
    end,
  },

  {
    "saghen/blink.cmp",
    -- FIX: version = "*" matched ANY tag; pin to the 1.x line so the
    -- prebuilt fuzzy-matcher binary always matches
    version = "1.*",
    event = "InsertEnter",
    dependencies = {
      "rafamadriz/friendly-snippets",
      "L3MON4D3/LuaSnip",
      -- REMOVED: leiserfg/blink_luasnip — obsolete bridge; blink has a
      -- native luasnip preset (snippets.preset below)
    },
    opts = {
      keymap = { preset = "default" },
      appearance = {
        nerd_font_variant = "mono",
        -- REMOVED: use_nvim_cmp_as_default (deprecated/no-op in blink 1.x)
      },
      snippets = {
        preset = "luasnip",
      },
      sources = {
        default = { "lsp", "path", "snippets", "buffer" },
      },
      signature = { enabled = true },
      cmdline = {
        enabled = false,
      },
      fuzzy = { implementation = "prefer_rust_with_warning" },
    },
    opts_extend = { "sources.default" },
  },
}
