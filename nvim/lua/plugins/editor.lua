-- ~/.config/nvim/lua/plugins/editor.lua
return {
  { "tpope/vim-surround", event = "VeryLazy" },
  { "tpope/vim-abolish", event = "VeryLazy" },
  { "tpope/vim-fugitive", cmd = { "Git", "G", "Gdiffsplit", "Gblame" } },

  {
    "abecodes/tabout.nvim",
    event = "InsertEnter",
    dependencies = { "nvim-treesitter/nvim-treesitter" },
    opts = {
      ignore_beginning = true,
      completion = false,
      tabouts = {
        { open = "'", close = "'" },
        { open = '"', close = '"' },
        { open = "`", close = "`" },
        { open = "(", close = ")" },
        { open = "[", close = "]" },
        { open = "{", close = "}" },
        { open = "<", close = ">" },
      },
    },
  },

  {
    "echasnovski/mini.pairs",
    version = "*",
    event = "InsertEnter",
    opts = {
      modes = {
        insert = true,
        command = false,
        terminal = false,
      },
      mappings = {
        ["<"] = { action = "open", pair = "<>", neigh_pattern = "[^\\]." },
        [">"] = { action = "close", pair = "<>", neigh_pattern = "[^\\]." },
      },
      -- REMOVED from the old config: `pairs = {...}` and
      -- `disable_filetype = {...}` are not mini.pairs options (they were
      -- nvim-autopairs/nvim-cmp options pasted in) and were silently
      -- ignored. Default pairs plus the <> mapping above cover the same
      -- set.
    },
  },
}
