-- ~/.config/nvim/lua/config/autocmds.lua
local augroup = vim.api.nvim_create_augroup
local autocmd = vim.api.nvim_create_autocmd
local usercmd = vim.api.nvim_create_user_command

local general_group = augroup("GeneralSettings", { clear = true })

vim.api.nvim_set_hl(0, "MatchParen", { bold = true, bg = "#5c5c5c", fg = "blue", ctermbg = 8, ctermfg = 0 })

-- Disable automatic comment continuation
autocmd("FileType", {
  pattern = "*",
  command = "setlocal formatoptions-=cro",
  group = general_group,
})

-- Wrap + linebreak in help buffers
autocmd("FileType", {
  pattern = "help",
  group = general_group,
  callback = function()
    vim.wo.wrap = true
    vim.wo.linebreak = true
  end,
})

-- Follow the current file's directory
autocmd("BufEnter", {
  group = general_group,
  callback = function()
    require("config.functions").maybe_change_directory()
  end,
})

-- Terminal windows: no line numbers
-- FIX: was setting vim.opt (global); use window-local options
autocmd("TermOpen", {
  group = general_group,
  callback = function()
    vim.wo.number = false
    vim.wo.relativenumber = false
  end,
})

autocmd("VimEnter", {
  group = general_group,
  callback = function()
    require("config.functions").open_files_if_specified()
  end,
})

-- REMOVED: the BufWritePre conform.format autocmd that lived here. It
-- duplicated conform's own format_on_save AND a third autocmd in the old
-- conform.lua, formatting every buffer two or three times per save.
-- Format-on-save is now configured once, in lua/plugins/formatting.lua.

autocmd("TextYankPost", {
  desc = "Highlight selection on yank",
  group = general_group,
  callback = function()
    -- FIX: vim.highlight.on_yank is deprecated on 0.11; use vim.hl
    vim.hl.on_yank({ higroup = "IncSearch", timeout = 500 })
  end,
})

-- Dashboard: no line numbers
-- FIX: the old version matched pattern="*" and set global vim.opt.number
-- on EVERY FileType event, fighting your window-local toggles. Scope to
-- the dashboard filetype and window-local options only.
autocmd("FileType", {
  pattern = "dashboard",
  group = general_group,
  callback = function()
    vim.wo.number = false
    vim.wo.relativenumber = false
  end,
})

--------------------------------------------------------------------------
-- :Grep with automatic quickfix handling
--------------------------------------------------------------------------
local last_grep_args = nil

local function close_qflist_if_open()
  -- FIX: old loop re-called nvim_list_wins() inside the loop while
  -- closing windows, indexing a shifting list. Snapshot once.
  local wins = vim.api.nvim_list_wins()
  for i = #wins, 1, -1 do
    local win = wins[i]
    if vim.api.nvim_win_is_valid(win) then
      local buf = vim.api.nvim_win_get_buf(win)
      if vim.bo[buf].buftype == "quickfix" then
        vim.api.nvim_win_close(win, true)
      end
    end
  end
end

---@usage :Grep [path] <pattern> [@filetype=type1,type2]
usercmd("Grep", function(cmd_opts)
  local args = vim.fn.expandcmd(cmd_opts.args)
  if args:match("^%s*$") then
    vim.notify("Search pattern required", vim.log.levels.INFO)
    close_qflist_if_open()
    return
  end

  -- Parse arguments with quote handling
  local parts = {}
  local current = ""
  local in_quotes = false
  local quote_char = nil
  for i = 1, #args do
    local c = args:sub(i, i)
    if (c == '"' or c == "'") and (i == 1 or args:sub(i - 1, i - 1) ~= "\\") then
      if not in_quotes then
        in_quotes = true
        quote_char = c
      elseif c == quote_char then
        in_quotes = false
        table.insert(parts, current)
        current = ""
      else
        current = current .. c
      end
    elseif c:match("%s") and not in_quotes then
      if #current > 0 then
        table.insert(parts, current)
        current = ""
      end
    else
      current = current .. c
    end
  end
  if #current > 0 then
    table.insert(parts, current)
  end

  -- Split out @filetype= filters
  local file_types, regular_args = {}, {}
  for _, part in ipairs(parts) do
    local filetypes = part:match("^@filetype=(.+)")
    if filetypes then
      for ft in filetypes:gmatch("[^,]+") do
        table.insert(file_types, vim.trim(ft))
      end
    else
      table.insert(regular_args, part)
    end
  end

  local path, pattern
  if #regular_args == 0 then
    vim.notify("No search pattern provided", vim.log.levels.ERROR)
    return
  elseif #regular_args == 1 then
    pattern = regular_args[1]
    path = "."
  else
    path = regular_args[1]
    pattern = regular_args[2]
  end

  local expanded_path = vim.fn.expand(path)
  if vim.fn.isdirectory(expanded_path) == 0 and vim.fn.filereadable(expanded_path) == 0 then
    vim.notify(string.format("Invalid path: '%s'", expanded_path), vim.log.levels.ERROR)
    return
  end

  local function strip_matching_quotes(str)
    local first, last = str:sub(1, 1), str:sub(-1)
    if (first == "'" and last == "'") or (first == '"' and last == '"') then
      return str:sub(2, -2)
    end
    return str
  end

  last_grep_args = cmd_opts.args

  local grep_cmd = "silent grep! "
  -- FIX: -g globs are ripgrep-specific; guard on grepprg actually being rg
  if #file_types > 0 and vim.o.grepprg:match("^rg") then
    for _, file_type in ipairs(file_types) do
      grep_cmd = grep_cmd .. "-g " .. vim.fn.shellescape("*." .. file_type) .. " "
    end
  end
  pattern = strip_matching_quotes(pattern)
  grep_cmd = grep_cmd .. vim.fn.shellescape(pattern) .. " " .. vim.fn.shellescape(expanded_path)

  local ok, err = pcall(vim.cmd, grep_cmd)
  -- FIX: was set unconditionally BEFORE checking ok, and clobbered any
  -- error state; only tag the list after a successful grep.
  if not ok then
    vim.notify("Grep failed: " .. tostring(err), vim.log.levels.ERROR)
    return
  end
  vim.fn.setqflist({}, "r", { title = "Grep Search", context = { type = "grep_search" } })

  local qf_list = vim.fn.getqflist()
  if #qf_list == 0 then
    vim.notify("No matches found", vim.log.levels.INFO)
    close_qflist_if_open()
    return
  end

  local max_height = math.floor(vim.o.lines * 0.2)
  local height = math.min(#qf_list, max_height)

  local saved_view = vim.fn.winsaveview()
  local cur_win = vim.api.nvim_get_current_win()

  close_qflist_if_open()
  vim.cmd("botright copen " .. height)
  vim.cmd("cfirst")

  if vim.api.nvim_win_is_valid(cur_win) then
    vim.api.nvim_set_current_win(cur_win)
    vim.fn.winrestview(saved_view)
  end
end, {
  nargs = "+",
  complete = "file",
  desc = "Grep with automatic quickfix: Grep [path] <pattern> [@filetype=type1,type2]",
})

local function rerun_last_grep()
  if last_grep_args then
    vim.cmd("Grep " .. last_grep_args)
  end
end

autocmd("BufWritePost", {
  group = augroup("UpdateQuickfixOnSave", { clear = true }),
  callback = function(args)
    -- FIX: order of checks inverted vs the old version (it first scanned
    -- entries, then invalidated the result; also left a stray debug
    -- print). Check the list context first, then membership.
    local qf_info = vim.fn.getqflist({ context = 0 })
    if not (qf_info.context and qf_info.context.type == "grep_search") then
      return
    end

    local in_quickfix = false
    for _, entry in ipairs(vim.fn.getqflist()) do
      if entry.bufnr == args.buf then
        in_quickfix = true
        break
      end
    end
    if not in_quickfix then
      return
    end

    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if vim.fn.getwininfo(win)[1].quickfix == 1 then
        rerun_last_grep()
        return
      end
    end
  end,
})
