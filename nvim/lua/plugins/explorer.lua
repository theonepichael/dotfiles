-- ~/.config/nvim/lua/plugins/explorer.lua
-- neo-tree removed: its config file was never required, the plugin was
-- never in the spec, and its keymaps duplicated nvim-tree's <Leader>e /
-- <Leader>fe. nvim-tree stays as the tree; oil stays as the
-- edit-directories-as-buffers tool.
return {
  {
    "nvim-tree/nvim-tree.lua",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    cmd = { "NvimTreeToggle", "NvimTreeFocus", "NvimTreeOpen", "NvimTreeFindFile" },
    keys = {
      { "<Leader>e", "<cmd>NvimTreeToggle<CR>", desc = "Toggle file explorer" },
      { "<Leader>fe", "<cmd>NvimTreeFocus<CR>", desc = "Focus file explorer" },
    },
    opts = {
      disable_netrw = true,
      hijack_netrw = true,
      auto_reload_on_write = true,
      hijack_cursor = false,
      -- FIX: update_cwd was renamed sync_root_with_cwd years ago; the old
      -- key was silently ignored
      sync_root_with_cwd = true,
      actions = {
        open_file = {
          quit_on_open = false,
          window_picker = { enable = false },
        },
      },
      update_focused_file = {
        enable = true,
        update_root = true, -- FIX: renamed from update_cwd
        ignore_list = {},
      },
      view = {
        width = 30,
        side = "left",
      },
      renderer = {
        highlight_git = true,
        highlight_modified = "name",
        icons = {
          show = {
            file = true,
            folder = true,
            folder_arrow = true,
            modified = true,
          },
          glyphs = {
            folder = {
              arrow_open = "▾",
              arrow_closed = "▸",
            },
            git = {
              unstaged = "✗",
              staged = "✓",
              unmerged = "",
              renamed = "➜",
              untracked = "★",
            },
          },
        },
        -- REMOVED: decorators = { "Modified", "Open" } — the decorators
        -- option expects decorator classes/full list, and passing just
        -- two names dropped git/diagnostics decoration entirely.
      },
    },
  },

  {
    "stevearc/oil.nvim",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    lazy = false, -- oil needs to load at startup to take over `nvim <dir>`
    keys = {
      {
        "-",
        function()
          require("oil").open()
        end,
        desc = "Open parent directory",
      },
    },
    opts = {
      keymaps = {
        ["g?"] = "actions.show_help",
        ["gs"] = {
          desc = "Git status",
          callback = function()
            local oil = require("oil")
            local current_dir = oil.get_current_dir()
            if not current_dir then
              vim.notify("Cannot determine current directory", vim.log.levels.WARN)
              return
            end
            vim.system(
              { "git", "-C", current_dir, "rev-parse", "--is-inside-work-tree" },
              {},
              vim.schedule_wrap(function(obj)
                if obj.code ~= 0 then
                  vim.notify("Not in a git repository", vim.log.levels.WARN)
                  return
                end
                local ok, err = pcall(vim.cmd, "lcd " .. vim.fn.fnameescape(current_dir))
                if not ok then
                  vim.notify("Failed to set directory: " .. tostring(err), vim.log.levels.ERROR)
                  return
                end
                pcall(vim.cmd, "Git")
              end)
            )
          end,
        },
      },
      view_options = {
        show_hidden = false,
      },
    },
    config = function(_, opts)
      require("oil").setup(opts)

      vim.api.nvim_create_autocmd("FileType", {
        pattern = "oil",
        group = vim.api.nvim_create_augroup("OilGitIntegration", { clear = true }),
        callback = function()
          -- FIX: the old toggle tracked vim.b.oil_show_hidden manually AND
          -- called oil's own toggle, so the notification drifted out of
          -- sync with reality; just call oil's action.
          vim.keymap.set("n", "<Leader>th", function()
            require("oil.actions").toggle_hidden.callback()
          end, { buffer = true, desc = "Toggle hidden files" })

          vim.keymap.set("n", "gb", function()
            local oil = require("oil")
            local current_dir = oil.get_current_dir()
            if not current_dir then
              vim.notify("Cannot determine current directory", vim.log.levels.WARN)
              return
            end
            pcall(vim.cmd, "lcd " .. vim.fn.fnameescape(current_dir))
            pcall(vim.cmd, "Git blame")
          end, { buffer = true, desc = "Git blame" })
        end,
      })
    end,
  },
}
