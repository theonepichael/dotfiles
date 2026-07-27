-- ~/.config/nvim/lua/plugins/telescope.lua
return {
  {
    "nvim-telescope/telescope.nvim",
    cmd = "Telescope",
    dependencies = {
      "nvim-lua/plenary.nvim",
      { "nvim-telescope/telescope-fzf-native.nvim", build = "make" },
    },
    keys = {
      { "<leader>ff", function() require("telescope.builtin").find_files() end, desc = "Find files (cwd)" },
      { "<leader>fg", function() require("telescope.builtin").live_grep() end, desc = "Live grep (cwd)" },
      { "<M-b>", function() require("config.telescope_search").search_buffers() end, desc = "List open buffers" },
      { "<leader>fh", function() require("config.telescope_search").show_help() end, desc = "Show Telescope custom help" },
      { "<leader>fp", function() require("config.telescope_search").find_project_files() end, desc = "Find files in project" },
      { "<leader>sp", function() require("config.telescope_search").grep_project() end, desc = "Search text in project" },
      { "<C-e>", function() require("config.telescope_search").search_nvim_config() end, desc = "Find in Neovim config" },
      { "<leader>ti", function() require("config.telescope_search").toggle_git_ignore() end, desc = "Toggle .gitignore respect" },
      { "<leader>tr", function() require("config.telescope_search").toggle_git_root() end, desc = "Toggle git root for project files" },
      { "<leader>fi", function() require("config.telescope_search").show_project_info() end, desc = "Show project root info" },
    },
    config = function()
      local telescope = require("telescope")
      local actions = require("telescope.actions")

      local has_rg = vim.fn.executable("rg") == 1
      if not has_rg then
        vim.notify("Ripgrep (rg) not found", vim.log.levels.WARN)
      end
      if vim.fn.executable("fd") == 0 then
        vim.notify("fd not found", vim.log.levels.WARN)
      end

      telescope.setup({
        defaults = {
          vimgrep_arguments = has_rg and {
            "rg",
            "--color=never",
            "--no-heading",
            "--with-filename",
            "--line-number",
            "--column",
            "--smart-case",
          } or {
            -- FIX: GNU grep has no --column/--smart-case; use flags grep
            -- actually understands so the fallback works at all
            "grep",
            "--color=never",
            "-RIn",
          },
          prompt_prefix = "🔍 ",
          selection_caret = "➤ ",
          path_display = { "truncate" },
          file_ignore_patterns = {},
          mappings = {
            i = {
              ["<Esc>"] = actions.close,
            },
          },
        },
        pickers = {
          find_files = {
            hidden = false,
            no_ignore = false,
          },
        },
      })

      pcall(telescope.load_extension, "fzf")
    end,
  },
}
