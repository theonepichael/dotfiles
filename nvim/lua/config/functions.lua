-- ~/.config/nvim/lua/config/functions.lua
local M = {}

-- quickfix utilities -----------------------------------------------------
function M.clear_quickfix_list()
  vim.fn.setqflist({})
  vim.cmd.cclose()
  vim.notify("Quickfix list cleared.")
end

function M.toggle_line_numbers()
  local on = vim.wo.number and vim.wo.relativenumber
  vim.wo.number = not on
  vim.wo.relativenumber = not on
end

-- Scroll messages to the bottom in a split window
function M.scroll_messages_to_bottom()
  vim.cmd("botright split")
  vim.cmd.enew()
  local buf = vim.api.nvim_get_current_buf()
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  local messages = vim.api.nvim_exec2("messages", { output = true }).output
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(messages, "\n", { plain = true }))
  vim.api.nvim_buf_set_name(buf, "Messages")
  vim.bo[buf].readonly = true
  vim.bo[buf].modifiable = false
  vim.cmd("silent normal! Gzz")
  vim.keymap.set("n", "q", "<cmd>bd<CR>", { buffer = buf, silent = true })
end

-- Toggle file explorer
-- FIX: the old toggle_netrw called :NetrwToggle, a command that does not
-- exist in stock netrw, and netrw is disabled in init.lua anyway.
function M.toggle_explorer()
  vim.cmd.NvimTreeToggle()
end

function M.debug_print(message)
  print(vim.inspect(message))
end

-- Directory-follow logic -------------------------------------------------
function M.should_skip_directory_change()
  local buftype = vim.bo.buftype
  if buftype == "help" or buftype == "quickfix" or buftype == "terminal" or buftype == "nofile" then
    return true
  end
  if vim.wo.diff then
    return true
  end
  if vim.fn.bufname("%") == "" then
    return true
  end
  -- FIX: the old pattern "^\\w\\+://" was a vim-regex escaped string used
  -- as a LUA pattern; it never matched anything. Lua pattern below
  -- actually detects protocol-prefixed paths (oil://, scp://, ...).
  if vim.fn.expand("%:p"):match("^%w+://") then
    return true
  end
  return false
end

function M.maybe_change_directory()
  if M.should_skip_directory_change() then
    return
  end
  local dir = vim.fn.expand("%:p:h")
  if vim.fn.isdirectory(dir) == 1 and vim.fn.getcwd() ~= dir then
    vim.cmd("lcd " .. vim.fn.fnameescape(dir))
  end
end

-- Open files given on the command line
function M.open_files_if_specified()
  if vim.fn.argc() == 0 then
    vim.g.started_with_file = 0
    return
  end
  vim.g.started_with_file = 1
  local files = {}
  for i = 0, vim.fn.argc() - 1 do
    local file = vim.fn.argv(i)
    -- FIX: filereadable() returns 0/1 and 0 is truthy in Lua, so the old
    -- `if vim.fn.filereadable(file) or ...` accepted every argument.
    if vim.fn.filereadable(file) == 1 or vim.fn.isdirectory(file) == 1 then
      table.insert(files, file)
    end
  end
  vim.cmd("argdelete!")
  for _, file in ipairs(files) do
    vim.cmd("argadd " .. vim.fn.fnameescape(file))
  end
  if #files > 0 then
    if vim.fn.isdirectory(files[1]) == 1 then
      -- FIX: was :Explore (netrw), which is disabled; open the tree
      vim.cmd.NvimTreeOpen(files[1])
    else
      vim.cmd("edit " .. vim.fn.fnameescape(files[1]))
      vim.cmd("normal! gg")
    end
  end
end

function M.view_command_output(command)
  require("config.commandpeek").view_command_output(command)
end

function M.close_all_but_current(prompt_save)
  local current_buf = vim.api.nvim_get_current_buf()
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if buf ~= current_buf and vim.api.nvim_buf_is_loaded(buf) then
      local buftype = vim.bo[buf].buftype
      local is_listed = vim.bo[buf].buflisted
      local modified = vim.bo[buf].modified
      local filetype = vim.bo[buf].filetype

      if buftype == "" or buftype == "acwrite" or not is_listed then
        if modified then
          if prompt_save then
            local choice = vim.fn.confirm(
              "Buffer " .. buf .. " (" .. filetype .. ") has unsaved changes. Save?",
              "&Yes\n&No\n&Cancel"
            )
            if choice == 1 then
              vim.api.nvim_buf_call(buf, function()
                vim.cmd.write()
              end)
              vim.api.nvim_buf_delete(buf, { force = true })
            elseif choice == 2 then
              vim.api.nvim_buf_delete(buf, { force = true })
            else
              vim.notify("Operation cancelled.")
              return
            end
          else
            vim.notify("Buffer " .. buf .. " (" .. filetype .. ") has unsaved changes. Skipping.")
          end
        else
          vim.api.nvim_buf_delete(buf, { force = true })
        end
      end
    end
  end
end

function M.delete_buffers(buffer_ids)
  for _, bufnr in ipairs(buffer_ids) do
    if vim.api.nvim_buf_is_valid(bufnr) and vim.bo[bufnr].buflisted then
      vim.api.nvim_buf_delete(bufnr, { force = true })
    else
      vim.notify("Buffer " .. bufnr .. " is not valid or not listed.", vim.log.levels.WARN)
    end
  end
end

function M.toggle_spell()
  vim.wo.spell = not vim.wo.spell
  vim.notify("Spell check " .. (vim.wo.spell and "enabled" or "disabled"))
end

-- Git diff to clipboard --------------------------------------------------
-- FIX: process_git_diff / process_diff_output / copy_to_clipboard /
-- show_in_buffer were accidental GLOBALS in the old file. All local now.

local function show_in_buffer(diff_output)
  vim.cmd.new()
  local buf = vim.api.nvim_get_current_buf()
  -- FIX: vim.split(s, "\n", true) is the removed positional form
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(diff_output, "\n", { plain = true }))
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].filetype = "diff"
  vim.api.nvim_buf_set_name(buf, "[Git Diff]")
  vim.notify("Git diff displayed in buffer (clipboard tools unavailable)")
end

local function copy_to_clipboard(diff_output)
  local clipboard_tools = {
    { cmd = "xclip", args = { "-selection", "clipboard" } },
    { cmd = "pbcopy" },
    { cmd = "clip.exe" },
    { cmd = "wl-copy" },
  }
  for _, tool in ipairs(clipboard_tools) do
    if vim.fn.executable(tool.cmd) == 1 then
      local argv = { tool.cmd }
      vim.list_extend(argv, tool.args or {})
      vim.system(argv, { stdin = diff_output }, function(obj)
        vim.schedule(function()
          if obj.code == 0 then
            vim.notify("Git diff copied to clipboard using " .. tool.cmd)
          else
            vim.notify("Clipboard copy failed (" .. tool.cmd .. ")", vim.log.levels.WARN)
          end
        end)
      end)
      return
    end
  end
  vim.schedule(function()
    vim.notify("No clipboard tool available (tried xclip, pbcopy, clip.exe, wl-copy)", vim.log.levels.WARN)
    show_in_buffer(diff_output)
  end)
end

local function process_diff_output(unstaged_diff, staged_diff)
  local pieces = {}
  if unstaged_diff and unstaged_diff ~= "" then
    table.insert(pieces, unstaged_diff)
  end
  if staged_diff and staged_diff ~= "" then
    if #pieces > 0 then
      table.insert(pieces, "\n--- STAGED CHANGES ---\n")
    end
    table.insert(pieces, staged_diff)
  end
  if #pieces == 0 then
    vim.schedule(function()
      vim.notify("No changes found (working or staged)")
    end)
    return
  end
  copy_to_clipboard(table.concat(pieces, "\n"))
end

local function process_git_diff(current_dir)
  vim.system({ "git", "-C", current_dir, "diff" }, { text = true }, function(unstaged_obj)
    if unstaged_obj.code ~= 0 then
      vim.schedule(function()
        vim.notify("Error running git diff: " .. (unstaged_obj.stderr or "unknown error"), vim.log.levels.ERROR)
      end)
      return
    end
    vim.system({ "git", "-C", current_dir, "diff", "--cached" }, { text = true }, function(staged_obj)
      if staged_obj.code ~= 0 then
        vim.schedule(function()
          vim.notify("Error running git diff --cached: " .. (staged_obj.stderr or "unknown error"), vim.log.levels.ERROR)
        end)
        return
      end
      process_diff_output(unstaged_obj.stdout, staged_obj.stdout)
    end)
  end)
end

-- User commands ----------------------------------------------------------
local usercmd = vim.api.nvim_create_user_command

usercmd("GitDiffToClipboard", function()
  local current_file = vim.api.nvim_buf_get_name(0)
  if current_file == "" then
    vim.notify("No file open in current buffer", vim.log.levels.WARN)
    return
  end
  local current_dir = vim.fn.fnamemodify(current_file, ":p:h")
  vim.system({ "git", "-C", current_dir, "rev-parse", "--is-inside-work-tree" }, { text = true }, function(obj)
    if obj.code ~= 0 then
      vim.schedule(function()
        vim.notify("Current directory is not a git repository", vim.log.levels.WARN)
      end)
      return
    end
    process_git_diff(current_dir)
  end)
end, { desc = "Copy git diff to clipboard (unstaged + staged)" })

usercmd("Splitswap", function()
  require("config.splitswap").toggle_split_orientation()
end, { desc = "Toggle split orientation between vertical and horizontal" })

usercmd("Messages", function()
  M.scroll_messages_to_bottom()
end, { desc = "Show Vim messages in a split, scrolled to bottom" })

usercmd("ViewCommandOutput", function(opts)
  if opts.args == "" then
    vim.notify("No command provided.", vim.log.levels.WARN)
    return
  end
  M.view_command_output(opts.args)
end, { desc = "Execute a command and view its output", nargs = "+" })

usercmd("BufOnly", function()
  M.close_all_but_current(false)
end, {})

usercmd("BufOnlySave", function()
  M.close_all_but_current(true)
end, {})

usercmd("ToggleSpellCheck", function()
  M.toggle_spell()
end, { desc = "Toggle spell checking on/off" })

usercmd("DeleteBuffers", function(opts)
  local buffer_ids = {}
  for bufnr in opts.args:gmatch("%d+") do
    table.insert(buffer_ids, tonumber(bufnr))
  end
  M.delete_buffers(buffer_ids)
end, { nargs = 1, desc = "Delete a collection of buffers by their numbers" })

usercmd("SelectAll", "normal! ggVG", { desc = "Visually select entire buffer" })

return M
