-- ~/.config/nvim/lua/config/commandpeek.lua
local M = {}

M.output_bufnr = nil

local function set_lines_unlocked(bufnr, start_idx, end_idx, lines)
  vim.bo[bufnr].readonly = false
  vim.bo[bufnr].modifiable = true
  vim.api.nvim_buf_set_lines(bufnr, start_idx, end_idx, false, lines)
  vim.bo[bufnr].modifiable = false
  vim.bo[bufnr].readonly = true
end

local function finalize_buffer(bufnr, raw_lines, filetype)
  vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, {})
  for _, item in ipairs(raw_lines) do
    if item ~= nil then
      vim.api.nvim_buf_set_lines(bufnr, -1, -1, false, vim.split(tostring(item), "\n", { plain = true }))
    end
  end
  if vim.api.nvim_buf_line_count(bufnr) == 0 then
    vim.api.nvim_buf_set_lines(bufnr, 0, -1, false, { "[No output]" })
  end
  vim.bo[bufnr].filetype = filetype or "text"
  vim.bo[bufnr].modifiable = false
  vim.bo[bufnr].readonly = true
  vim.bo[bufnr].modified = false
  -- FIX: "normal! ggzz" ran in whatever window was current; scope the
  -- cursor move to a window actually showing the output buffer.
  local wins = vim.fn.win_findbuf(bufnr)
  if #wins > 0 then
    vim.api.nvim_win_call(wins[1], function()
      vim.cmd("normal! ggzz")
    end)
  end
end

function M.view_command_output(command)
  if not command or command == "" then
    vim.notify("No command provided", vim.log.levels.WARN)
    return
  end

  local bufnr
  if M.output_bufnr and vim.api.nvim_buf_is_valid(M.output_bufnr) then
    bufnr = M.output_bufnr
    local wins = vim.fn.win_findbuf(bufnr)
    if #wins == 0 then
      vim.cmd("botright split")
      vim.api.nvim_win_set_buf(0, bufnr)
    else
      vim.api.nvim_set_current_win(wins[1])
    end
  else
    vim.cmd("botright split")
    vim.cmd.enew()
    bufnr = vim.api.nvim_get_current_buf()
    M.output_bufnr = bufnr
    vim.bo[bufnr].buftype = "nofile"
    -- FIX: bufhidden=wipe made the "reuse existing buffer" branch above
    -- unreachable (the buffer was wiped the moment its window closed).
    vim.bo[bufnr].bufhidden = "hide"
    vim.bo[bufnr].swapfile = false
    vim.bo[bufnr].buflisted = false
  end

  vim.keymap.set("n", "q", "<cmd>close<CR>", { buffer = bufnr, silent = true })
  vim.bo[bufnr].modifiable = true

  local safe_name = command:gsub("%c", " "):sub(1, 50)
  if #command > 50 then
    safe_name = safe_name .. "..."
  end
  pcall(vim.api.nvim_buf_set_name, bufnr, "[Output: " .. safe_name .. "]")
  vim.cmd("redrawstatus!")

  if command:sub(1, 1) == "!" then
    local shell_command = command:sub(2)
    vim.api.nvim_buf_set_lines(bufnr, 0, 0, false, { "Executing: " .. shell_command, "", "Please wait..." })
    vim.cmd.redraw()

    vim.system({ "bash", "-c", shell_command }, { text = true }, function(obj)
      vim.schedule(function()
        if not vim.api.nvim_buf_is_valid(bufnr) then
          return
        end
        local header = {
          "Command: " .. shell_command,
          obj.code ~= 0 and ("Exit Code: " .. tostring(obj.code)) or nil,
          string.rep("-", 80),
          "",
        }
        finalize_buffer(bufnr, header, "sh")
        if obj.stdout and #obj.stdout > 0 then
          set_lines_unlocked(bufnr, -1, -1, vim.split(obj.stdout, "\n", { plain = true }))
        end
        if obj.stderr and #obj.stderr > 0 then
          set_lines_unlocked(
            bufnr,
            -1,
            -1,
            vim.list_extend({ "", "STDERR:", "" }, vim.split(obj.stderr, "\n", { plain = true }))
          )
        end
      end)
    end)
  else
    local vim_command = command:gsub("^:", "")
    -- FIX: nvim_cmd({ cmd = vim_command }) requires a bare command NAME;
    -- anything with arguments ("echo hi") errored. nvim_exec2 accepts a
    -- full command line and captures output.
    local ok, result = pcall(vim.api.nvim_exec2, vim_command, { output = true })
    if not ok then
      finalize_buffer(bufnr, {
        "[Error executing Vim command]",
        string.rep("-", 80),
        "",
        tostring(result),
      }, "vim")
      vim.notify("Error executing Vim command: " .. tostring(result), vim.log.levels.ERROR)
    else
      finalize_buffer(bufnr, {
        "Command: " .. vim_command,
        string.rep("-", 80),
        "",
      }, "vim")
      local output = result.output
      if output and #output > 0 then
        set_lines_unlocked(bufnr, -1, -1, vim.split(output, "\n", { plain = true }))
      end
    end
  end
end

return M
