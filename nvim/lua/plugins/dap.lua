-- ~/.config/nvim/lua/plugins/dap.lua
return {
  {
    "mfussenegger/nvim-dap",
    dependencies = {
      { "rcarriga/nvim-dap-ui", dependencies = { "nvim-neotest/nvim-nio" } },
      "mfussenegger/nvim-dap-python",
    },
    ft = "python",
    keys = {
      { "<leader>dc", function() require("dap").continue() end, desc = "Debug: Continue" },
      { "<leader>db", function() require("dap").toggle_breakpoint() end, desc = "Debug: Toggle breakpoint" },
      { "<leader>do", function() require("dap").step_over() end, desc = "Debug: Step over" },
      { "<leader>di", function() require("dap").step_into() end, desc = "Debug: Step into" },
      { "<leader>du", function() require("dap").step_out() end, desc = "Debug: Step out" },
    },
    config = function()
      local dap = require("dap")
      local dapui = require("dapui")
      local dap_python = require("dap-python")

      -- FIX: hardcoded /data/python-env/debugpy/bin/python with no
      -- existence check; fall back to whatever python3 is on PATH
      local debugpy_python = "/data/python-env/debugpy/bin/python"
      if vim.fn.executable(debugpy_python) == 0 then
        debugpy_python = vim.fn.exepath("python3")
      end
      dap_python.setup(debugpy_python)

      dapui.setup()
      dap.listeners.before.attach.dapui_config = function()
        dapui.open()
      end
      dap.listeners.before.launch.dapui_config = function()
        dapui.open()
      end
      dap.listeners.before.event_terminated.dapui_config = function()
        dapui.close()
      end
      dap.listeners.before.event_exited.dapui_config = function()
        dapui.close()
      end

      -- Breakpoint sign.
      -- FIX: in the old file this sign_define() call sat INSIDE the
      -- dap.configurations.python list, so its return value became a
      -- bogus third launch configuration.
      vim.fn.sign_define("DapBreakpoint", { text = "●", texthl = "Error", linehl = "", numhl = "" })

      -- dap-python.setup() already registers sensible "Launch file"
      -- configs; extend rather than replace.
      table.insert(dap.configurations.python, {
        type = "python",
        request = "launch",
        name = "Launch with config-dir args",
        program = "${file}",
        pythonPath = function()
          return vim.fn.exepath("python3")
        end,
        args = { vim.fn.expand("~/.config/nvim"), "lua", "-r" },
      })
    end,
  },
}
