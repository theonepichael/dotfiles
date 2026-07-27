-- ~/.config/nvim/lua/plugins/colorscheme.lua
return {
  {
    "sainnhe/everforest",
    lazy = false,
    priority = 1000, -- load before everything else so highlights apply
    config = function()
      vim.g.everforest_enable_italic = true
      vim.cmd.colorscheme("everforest")
    end,
  },
}
