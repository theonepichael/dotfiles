-- ~/.config/nvim/lua/plugins/treesitter.lua
-- MIGRATED to the nvim-treesitter `main` branch (the `master` branch is
-- frozen/legacy). On main there is no "nvim-treesitter.configs" module:
-- parsers are installed with require("nvim-treesitter").install() and
-- highlighting is started per-buffer with vim.treesitter.start().
--
-- This also fixes the old spec's `run = function() ... end` key, which
-- is a packer.nvim option lazy.nvim ignores entirely, so parser updates
-- never actually ran.

local ensure_installed = {
  "lua",
  "vim",
  "vimdoc",
  "bash",
  "java",
  "typescript",
  "javascript",
  "tsx",
  "json",
  "markdown",
  "markdown_inline",
  "python",
}

-- Filetypes to enable treesitter features for
local ts_filetypes = {
  "lua",
  "vim",
  "sh",
  "bash",
  "java",
  "typescript",
  "typescriptreact",
  "javascript",
  "javascriptreact",
  "json",
  "markdown",
  "python",
}

return {
  {
    "nvim-treesitter/nvim-treesitter",
    branch = "main",
    lazy = false,
    build = ":TSUpdate",
    config = function()
      require("nvim-treesitter").install(ensure_installed)

      vim.api.nvim_create_autocmd("FileType", {
        pattern = ts_filetypes,
        group = vim.api.nvim_create_augroup("TreesitterStart", { clear = true }),
        callback = function(args)
          -- Highlighting (replaces highlight = { enable = true })
          pcall(vim.treesitter.start, args.buf)
          -- Folding is configured globally in config/options.lua via
          -- v:lua.vim.treesitter.foldexpr() (replaces fold = { enable })
        end,
      })
    end,
  },

  {
    "nvim-treesitter/nvim-treesitter-textobjects",
    branch = "main",
    dependencies = { "nvim-treesitter/nvim-treesitter" },
    event = "VeryLazy",
    config = function()
      require("nvim-treesitter-textobjects").setup({
        select = {
          lookahead = true,
        },
        move = {
          set_jumps = true,
        },
      })

      local select = require("nvim-treesitter-textobjects.select")
      local move = require("nvim-treesitter-textobjects.move")

      local function sel(lhs, query, desc)
        vim.keymap.set({ "x", "o" }, lhs, function()
          select.select_textobject(query, "textobjects")
        end, { desc = desc })
      end
      sel("af", "@function.outer", "Around function")
      sel("if", "@function.inner", "Inside function")
      sel("ac", "@class.outer", "Around class")
      sel("ic", "@class.inner", "Inside class")
      sel("ai", "@conditional.outer", "Around conditional")
      sel("ii", "@conditional.inner", "Inside conditional")

      local function mov(lhs, fn, query, desc)
        vim.keymap.set({ "n", "x", "o" }, lhs, function()
          move[fn](query, "textobjects")
        end, { desc = desc })
      end
      mov("]w", "goto_next_start", "@function.outer", "Next function start")
      mov("]]", "goto_next_start", "@class.outer", "Next class start")
      mov("]W", "goto_next_end", "@function.outer", "Next function end")
      mov("][", "goto_next_end", "@class.outer", "Next class end")
      mov("[w", "goto_previous_start", "@function.outer", "Prev function start")
      mov("[[", "goto_previous_start", "@class.outer", "Prev class start")
      mov("[W", "goto_previous_end", "@function.outer", "Prev function end")
      mov("[]", "goto_previous_end", "@class.outer", "Prev class end")
    end,
  },
}
