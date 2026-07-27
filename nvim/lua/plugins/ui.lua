-- ~/.config/nvim/lua/plugins/ui.lua
return {
  {
    "nvimdev/dashboard-nvim",
    event = "VimEnter",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    opts = {},
  },

  -- Only mini.statusline and mini.pairs are used, so pull the individual
  -- modules instead of all of mini.nvim.
  {
    "echasnovski/mini.statusline",
    version = "*",
    event = "VimEnter",
    opts = { use_icons = true },
  },
}
