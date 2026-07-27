-- ~/.config/nvim/lua/config/shellcheck_qf.lua
-- Opt-in: uncomment require("config.shellcheck_qf").setup() in init.lua
local M = {}

function M.run_shellcheck()
  if vim.bo.filetype ~= "sh" then
    return
  end
  if vim.fn.executable("shellcheck") == 0 then
    vim.notify("ShellCheck not found. Please install it first.", vim.log.levels.ERROR)
    return
  end

  local current_file = vim.fn.expand("%:p")
  -- FIX: async vim.system instead of blocking vim.fn.system on every save
  vim.system(
    { "shellcheck", "-f", "gcc", "-x", current_file },
    { text = true },
    vim.schedule_wrap(function(obj)
      if obj.code == 0 then
        vim.notify("No ShellCheck errors found")
        vim.cmd.cclose()
        return
      end
      local lines = vim.split(obj.stdout or "", "\n", { plain = true, trimempty = true })
      if #lines == 0 then
        vim.notify("ShellCheck reported an error without details.", vim.log.levels.WARN)
        return
      end
      -- FIX: errorformat was set on the buffer then restored BEFORE the
      -- quickfix list was populated; pass efm directly to setqflist so
      -- there is no global/buffer state mutation at all.
      vim.fn.setqflist({}, "r", {
        title = "ShellCheck",
        lines = lines,
        efm = "%f:%l:%c: %t%*[^:]: %m",
      })
      local qf_entries = vim.fn.getqflist()
      local qf_height = math.min(math.max(#qf_entries, 3), math.min(10, math.floor(vim.o.lines / 3)))
      vim.cmd(qf_height .. "copen")
    end)
  )
end

function M.setup_qf_mappings()
  local opts = { buffer = true, silent = true }
  vim.keymap.set("n", "q", "<cmd>cclose<CR>", opts)
  vim.keymap.set("n", "<CR>", "<cmd>cc<CR>", opts)
  vim.keymap.set("n", "dd", function()
    local current_line = vim.fn.line(".")
    local quickfix_list = vim.fn.getqflist()
    if current_line > 0 and current_line <= #quickfix_list then
      table.remove(quickfix_list, current_line)
      vim.fn.setqflist(quickfix_list, "r")
      if current_line > #quickfix_list and #quickfix_list > 0 then
        vim.cmd("normal! k")
      end
    end
  end, opts)
  vim.keymap.set("n", "p", "<cmd>cwindow<CR>", opts)

  vim.opt_local.wrap = false
  vim.opt_local.number = true
  vim.opt_local.relativenumber = false
  vim.opt_local.signcolumn = "no"
  vim.opt_local.cursorline = true
end

function M.toggle_quickfix()
  for _, win in ipairs(vim.fn.getwininfo()) do
    if win.quickfix == 1 then
      vim.cmd.cclose()
      return
    end
  end
  local quickfix_list = vim.fn.getqflist()
  if #quickfix_list == 0 then
    vim.notify("Quickfix list is empty", vim.log.levels.WARN)
    return
  end
  local max_height = math.floor(vim.o.lines / 3)
  local height = math.min(math.max(#quickfix_list, 3), math.min(10, max_height))
  vim.cmd(string.format("%dcopen", height))
end

function M.setup()
  local shellcheck_group = vim.api.nvim_create_augroup("ShellCheck", { clear = true })
  local qf_group = vim.api.nvim_create_augroup("QuickfixMappings", { clear = true })

  -- FIX: pattern was "*" (fired on every save of every file); scope to
  -- shell scripts at the autocmd level, not just inside the callback.
  vim.api.nvim_create_autocmd("BufWritePost", {
    pattern = { "*.sh", "*.bash" },
    callback = M.run_shellcheck,
    group = shellcheck_group,
  })

  vim.api.nvim_create_autocmd("FileType", {
    pattern = "qf",
    callback = M.setup_qf_mappings,
    group = qf_group,
  })

  vim.keymap.set("n", "<leader>q", M.toggle_quickfix, { silent = true, desc = "Toggle quickfix window" })
end

return M
