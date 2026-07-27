-- ~/.config/nvim/lua/plugins/terminal.lua
return {
  {
    "akinsho/toggleterm.nvim",
    keys = { { "<C-\\>", desc = "Toggle terminal", mode = { "n", "t" } } },
    cmd = "ToggleTerm",
    opts = {
      size = 8,
      open_mapping = "<C-\\>",
      direction = "horizontal",
      shade_filetypes = {},
      shade_terminals = true,
      start_in_insert = true,
      persist_size = true,
      close_on_exit = true,
    },
  },
}
