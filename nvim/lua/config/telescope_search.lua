-- ~/.config/nvim/lua/config/telescope_search.lua
-- Custom Telescope pickers. Required from lua/plugins/telescope.lua's
-- config function, so telescope is guaranteed loaded; the old file's
-- safe_require scaffolding is no longer needed.
local M = {}

local actions = require("telescope.actions")
local action_state = require("telescope.actions.state")
local builtin = require("telescope.builtin")

local function with_close_mappings(extra)
  return function(prompt_bufnr, map)
    map({ "i", "n" }, "QQ", function()
      actions.close(prompt_bufnr)
    end)
    map({ "i", "n" }, "<Esc>", function()
      actions.close(prompt_bufnr)
    end)
    if extra then
      extra(prompt_bufnr, map)
    end
    return true
  end
end

-- Git-root toggle --------------------------------------------------------
M.use_git_root = false

function M.toggle_git_root()
  M.use_git_root = not M.use_git_root
  vim.notify("Now " .. (M.use_git_root and "using" or "ignoring") .. " git root for project files")
end

function M.get_project_root()
  -- Oil buffers: derive the directory being viewed
  if vim.bo.filetype == "oil" then
    local ok, oil = pcall(require, "oil")
    if ok then
      local oil_dir = oil.get_current_dir()
      if oil_dir and vim.fn.isdirectory(oil_dir) == 1 then
        return oil_dir
      end
    end
  end

  -- LSP root of a client attached to THIS buffer.
  -- FIX: the old loop asked vim.lsp.get_clients() with no filter, so the
  -- root of whatever server attached first anywhere in the session won
  -- (e.g. lua_ls from your config repo while editing a TS project).
  for _, client in ipairs(vim.lsp.get_clients({ bufnr = 0 })) do
    local lsp_root = client.config and client.config.root_dir
    if lsp_root and lsp_root ~= "" then
      return lsp_root
    end
  end

  if M.use_git_root then
    local result = vim.fn.systemlist("git rev-parse --show-toplevel")
    if vim.v.shell_error == 0 and result[1] and result[1] ~= "" then
      return result[1]
    end
  end

  return vim.uv.cwd()
end

function M.show_project_info()
  local cwd = vim.uv.cwd()
  local git_cmd_result = vim.fn.systemlist("git rev-parse --show-toplevel")
  local git_root = vim.v.shell_error == 0 and git_cmd_result[1] or "Not in a git repository"
  vim.notify("CWD: " .. cwd .. "\nGit Root: " .. git_root)
end

-- .gitignore toggle -------------------------------------------------------
local respect_gitignore = true

function M.toggle_git_ignore()
  respect_gitignore = not respect_gitignore
  vim.notify("Now " .. (respect_gitignore and "respecting" or "ignoring") .. " .gitignore")
end

function M.get_git_ignore_flag()
  return not respect_gitignore
end

-- Directory-scoped search with content/filename toggle --------------------
function M.search_at_path(dir, opts, search_content)
  opts = opts or {}
  opts.cwd = dir
  search_content = search_content or false

  local dir_label = vim.fn.fnamemodify(dir, ":t")
  if search_content then
    opts.prompt_title = "Search Contents (" .. dir_label .. ") (<C-t> to toggle)"
  else
    opts.prompt_title = "Find Lua Files (" .. dir_label .. ") (<C-t> to toggle)"
    opts.find_command = { "fd", "--type", "f", "--extension", "lua", "--hidden", "--exclude", ".git" }
  end

  opts.attach_mappings = with_close_mappings(function(prompt_bufnr, map)
    map({ "i", "n" }, "<C-t>", function()
      local query = action_state.get_current_line()
      actions.close(prompt_bufnr)
      M.search_at_path(dir, { default_text = query }, not search_content)
    end)
  end)

  if search_content then
    builtin.live_grep(opts)
  else
    builtin.find_files(opts)
  end
end

-- FIX: search_nvim_config was a full copy-paste of search_at_path with
-- cwd hardcoded; it now delegates.
function M.search_nvim_config(opts, search_content)
  M.search_at_path(vim.fn.stdpath("config"), opts, search_content)
end

function M.toggle_line_numbers()
  require("config.functions").toggle_line_numbers()
end

function M.search_buffers()
  local dropdown = require("telescope.themes").get_dropdown({
    ignore_current_buffer = true,
    previewer = false,
    show_all_buffers = true,
    sort_mru = true,
    sort_lastused = true,
    prompt_title = "Open Buffers",
    attach_mappings = with_close_mappings(),
  })
  builtin.buffers(dropdown)
  -- REMOVED: the trailing pcall(M.toggle_line_numbers) that flipped your
  -- line numbers globally every time you listed buffers.
end

function M.find_project_files()
  local root = M.get_project_root()
  local ok = pcall(builtin.git_files, { cwd = root, show_untracked = true, prompt_title = "Project Files" })
  if not ok then
    builtin.find_files({
      cwd = root,
      prompt_title = "Project Files",
      hidden = false,
      no_ignore = M.get_git_ignore_flag(),
      attach_mappings = with_close_mappings(),
    })
  end
end

function M.grep_project()
  local root = M.get_project_root()
  builtin.live_grep({
    cwd = root,
    prompt_title = "Search in Project",
    additional_args = function(_)
      -- FIX: "--glob=!git/*" was missing the dot; corrected to .git
      local args = { "--hidden", "--glob=!node_modules/*", "--glob=!.next/*", "--glob=!.git/*" }
      if M.get_git_ignore_flag() then
        table.insert(args, "--no-ignore")
      end
      return args
    end,
    attach_mappings = with_close_mappings(),
  })
end

function M.show_help()
  for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_get_name(bufnr):match("TelescopeSearchHelp$") then
      vim.api.nvim_buf_delete(bufnr, { force = true })
      break
    end
  end

  vim.cmd("topleft 38vnew")
  local buf = vim.api.nvim_get_current_buf()
  local win = vim.api.nvim_get_current_win()

  local lines = {
    "Telescope Search Shortcuts",
    "==========================",
    "",
    "Navigation",
    "----------",
    "<C-space>  Toggle fuzzy mode",
    "<C-n>/<C-p>  Next/Prev item",
    "<C-u>/<C-d>  Scroll preview up/down",
    "<C-q>       Send to quickfix",
    "<C-c>       Close Telescope",
    "",
    "File Search",
    "-----------",
    "<leader>fp   Files in project root",
    "<C-e>        Files in Neovim config",
    "<M-b>        List open buffers",
    "",
    "Content Search",
    "--------------",
    "<leader>sp   Search in project root",
    "<C-t>        Toggle content/filename search",
    "",
    "Utilities",
    "---------",
    "<leader>ti   Toggle .gitignore respect",
    "<leader>fi   Show project root info",
    "<leader>fh   Show this help",
    "",
    "Press q to close this window",
  }

  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_buf_set_name(buf, "TelescopeSearchHelp")

  -- FIX: options must be set with the right scope; "modifiable" after
  -- content, "readonly" via buffer options
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].swapfile = false
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].modifiable = false
  vim.bo[buf].readonly = true
  vim.bo[buf].filetype = "help"

  local win_opts = {
    number = false,
    relativenumber = false,
    cursorline = false,
    signcolumn = "no",
    foldcolumn = "0",
    wrap = true,
    spell = false,
    list = false,
  }
  for opt, val in pairs(win_opts) do
    vim.api.nvim_set_option_value(opt, val, { scope = "local", win = win })
  end

  for _, lhs in ipairs({ "q", "QQ", "<Esc>" }) do
    vim.keymap.set("n", lhs, "<cmd>close<CR>", { buffer = buf, nowait = true, silent = true })
  end

  vim.api.nvim_win_set_cursor(win, { 1, 0 })
end

return M
