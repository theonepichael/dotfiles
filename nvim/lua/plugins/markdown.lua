-- ~/.config/nvim/lua/plugins/markdown.lua
return {
  {
    "MeanderingProgrammer/render-markdown.nvim",
    ft = { "markdown" },
    dependencies = {
      "nvim-treesitter/nvim-treesitter",
      "nvim-tree/nvim-web-devicons", -- icons without pulling all of mini.nvim
    },
    opts = {
      completions = { blink = { enabled = true } },
    },
  },

  {
    "jghauser/follow-md-links.nvim",
    ft = { "markdown" },
  },
}
