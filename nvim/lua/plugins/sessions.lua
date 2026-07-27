-- ~/.config/nvim/lua/plugins/sessions.lua
return {
  {
    "olimorris/persisted.nvim",
    -- FIX: the old spec had lazy = false AND cmd = {...}; the cmd list
    -- was dead weight since the plugin always loaded. autostart = true
    -- requires eager loading, so lazy = false is the one that stays.
    lazy = false,
    opts = {
      autostart = true,
      follow_cwd = false,
      use_git_branch = true,
      autoload = false,
      on_autoload_no_session = function()
        vim.notify("No existing session to load.")
      end,
      allowed_dirs = {
        "~/.config/nvim",
        "/data/projects",
      },
      ignored_dirs = {
        "~/.local/nvim",
        { "/", exact = true },
        { "/tmp", exact = true },
      },
    },
    config = function(_, opts)
      require("persisted").setup(opts)
      local has_telescope, telescope = pcall(require, "telescope")
      if has_telescope then
        pcall(telescope.load_extension, "persisted")
      end
    end,
  },
}
