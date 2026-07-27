-- ~/.config/nvim/lua/config/splitswap.lua
local M = {}

local is_busy = false
local DEBUG = false

local function debug_print(...)
  if DEBUG then
    print("[splitswap]", table.concat(vim.tbl_map(tostring, { ... }), " "))
  end
end

local function is_normal_window(winid)
  if not vim.api.nvim_win_is_valid(winid) then
    return false
  end
  return vim.api.nvim_win_get_config(winid).relative == ""
end

-- FIX: the old comparator `a.winrow < b.winrow or a.wincol < b.wincol` is
-- not a strict weak ordering (for a=(1,2), b=(2,1): both cmp(a,b) and
-- cmp(b,a) are true), which makes table.sort error with "invalid order
-- function" on some layouts. Proper row-then-column ordering:
local function window_position_cmp(a, b)
  local ia, ib = vim.fn.getwininfo(a)[1], vim.fn.getwininfo(b)[1]
  if ia.winrow ~= ib.winrow then
    return ia.winrow < ib.winrow
  end
  return ia.wincol < ib.wincol
end

local function capture_window_state(winid)
  local opts = {}
  -- FIX: iterate only window-scoped options; the old loop pcall-probed
  -- every option in existence (including global and buffer options) and
  -- captured/restored globals as window state.
  for name, info in pairs(vim.api.nvim_get_all_options_info()) do
    if info.scope == "win" then
      local ok, val = pcall(vim.api.nvim_get_option_value, name, { win = winid })
      if ok then
        opts[name] = val
      end
    end
  end

  -- FIX: winsaveview() reads the CURRENT window; evaluate it inside the
  -- target window or every saved view was the active window's view.
  local view = vim.api.nvim_win_call(winid, vim.fn.winsaveview)

  return {
    bufnr = vim.api.nvim_win_get_buf(winid),
    cursor = vim.api.nvim_win_get_cursor(winid),
    view = view,
    options = opts,
  }
end

local function restore_window_state(winid, state)
  if not (vim.api.nvim_win_is_valid(winid) and vim.api.nvim_buf_is_valid(state.bufnr)) then
    debug_print("Skipping restore: invalid window or buffer")
    return
  end
  pcall(vim.api.nvim_win_set_buf, winid, state.bufnr)
  for name, value in pairs(state.options) do
    pcall(vim.api.nvim_set_option_value, name, value, { win = winid })
  end
  vim.api.nvim_win_call(winid, function()
    pcall(vim.fn.winrestview, state.view)
  end)
  pcall(vim.api.nvim_win_set_cursor, winid, state.cursor)
end

local function list_normal_windows()
  return vim.tbl_filter(is_normal_window, vim.api.nvim_tabpage_list_wins(0))
end

local function check_modified_buffers(wins)
  for _, win in ipairs(wins) do
    local bufnr = vim.api.nvim_win_get_buf(win)
    if vim.bo[bufnr].modified then
      local choice = vim.fn.confirm(
        string.format("Save changes to %s?", vim.api.nvim_buf_get_name(bufnr)),
        "&Yes\n&No\n&Cancel"
      )
      if choice == 3 then
        return false
      elseif choice == 1 then
        -- FIX: `%dbuffer | write` wrote whatever buffer was current after
        -- a bar-separated command; write the actual buffer directly.
        vim.api.nvim_buf_call(bufnr, function()
          vim.cmd("silent! write")
        end)
      end
    end
  end
  return true
end

local function detect_dominant_layout(windows)
  local horizontal, vertical = 0, 0
  for i = 1, #windows do
    local info1 = vim.fn.getwininfo(windows[i])[1]
    for j = i + 1, #windows do
      local info2 = vim.fn.getwininfo(windows[j])[1]
      if info1.winrow == info2.winrow and math.abs((info1.wincol + info1.width) - info2.wincol) <= 1 then
        vertical = vertical + 1
      elseif info1.wincol == info2.wincol and math.abs((info1.winrow + info1.height) - info2.winrow) <= 1 then
        horizontal = horizontal + 1
      end
    end
  end
  debug_print(string.format("Detected layout: horizontal=%d, vertical=%d", horizontal, vertical))
  return horizontal > vertical and "horizontal" or "vertical"
end

local function transform_global(split_cmd, layout_label)
  local wins = list_normal_windows()
  if #wins <= 1 then
    vim.notify("Not enough windows to transform.", vim.log.levels.INFO)
    return
  end
  if not check_modified_buffers(wins) then
    return
  end

  local states = {}
  for _, win in ipairs(wins) do
    states[win] = capture_window_state(win)
  end

  table.sort(wins, window_position_cmp)
  vim.api.nvim_set_current_win(wins[1])

  local prev_lazyredraw = vim.o.lazyredraw
  vim.o.lazyredraw = true

  for _ = 2, #wins do
    vim.cmd.close()
  end
  for _ = 2, #wins do
    vim.cmd[split_cmd]()
  end
  vim.cmd.wincmd("=")

  local new_wins = list_normal_windows()
  table.sort(new_wins, window_position_cmp)

  for i = 1, math.min(#new_wins, #wins) do
    restore_window_state(new_wins[i], states[wins[i]])
  end

  if #new_wins > 0 then
    vim.api.nvim_set_current_win(new_wins[1])
    vim.cmd("normal! zz")
  end

  vim.o.lazyredraw = prev_lazyredraw
  vim.notify("Switched to " .. layout_label .. " Layout")
end

function M.toggle_split_orientation()
  if is_busy then
    vim.notify("Split swap already in progress. Skipping...", vim.log.levels.WARN)
    return
  end
  is_busy = true
  vim.schedule(function()
    local ok, err = pcall(function()
      local wins = list_normal_windows()
      if detect_dominant_layout(wins) == "horizontal" then
        transform_global("vsplit", "Vertical")
      else
        transform_global("split", "Horizontal")
      end
    end)
    if not ok then
      vim.notify("Error toggling splits: " .. tostring(err), vim.log.levels.ERROR)
    end
    is_busy = false
  end)
end

return M
