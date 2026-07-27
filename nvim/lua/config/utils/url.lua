-- ~/.config/nvim/lua/config/utils/url.lua
local M = {}

-- Extract valid URL from text under cursor
function M.extract_url()
  local cfile = vim.fn.expand("<cfile>")
  if cfile == "" then
    return nil
  end
  -- FIX: `a and b or c` precedence in the old check meant "^www%." URLs
  -- passed even when cfile was nil-ish; explicit branches instead.
  if cfile:match("^https?://") then
    return cfile
  end
  if cfile:match("^www%.") then
    return "http://" .. cfile
  end
  return nil
end

function M.open_url()
  local url = M.extract_url()
  if not url then
    vim.notify("No valid URL found under cursor", vim.log.levels.WARN)
    return
  end
  -- MODERNIZED: vim.ui.open (0.10+) replaces the whole hand-rolled
  -- xdg-open/wslview/gnome-open/cmd.exe dispatch table.
  local cmd, err = vim.ui.open(url)
  if not cmd then
    vim.notify("Failed to open URL: " .. (err or url), vim.log.levels.ERROR)
  end
end

vim.keymap.set("n", "gx", M.open_url, { silent = true, desc = "Open URL under cursor" })

return M
