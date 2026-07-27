-- ~/.config/nvim/lua/config/utils/testing.lua
local M = {}

function M.setup_test_layout()
  vim.cmd.tabnew()
  -- FIX: dropped the redundant :enew (tabnew already opens an empty
  -- buffer) and the always-true `if vim.bo then` guard
  vim.bo.bufhidden = "wipe"
  vim.cmd.vsplit()
  vim.cmd.wincmd("l")
  vim.cmd.split()
  vim.cmd.wincmd("h")
  vim.cmd.wincmd("=")
end

return M
