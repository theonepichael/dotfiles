-- ~/.config/nvim/lsp/lua_ls.lua
-- Single, consolidated lua_ls config. Replaces the three conflicting
-- setups from the old plugin/setup.lua, lua-lsp.lua and conform.lua.
---@type vim.lsp.Config
return {
  settings = {
    Lua = {
      runtime = {
        version = "LuaJIT",
        -- REMOVED: path = vim.split(package.path, ";") from the old
        -- inline setup; it fought the workspace.library below.
      },
      diagnostics = {
        globals = { "vim", "describe", "it", "before_each", "after_each", "teardown", "pending" },
        disable = { "missing-parameter", "missing-fields" },
      },
      workspace = {
        library = vim.api.nvim_get_runtime_file("", true),
        checkThirdParty = false,
      },
      -- FIX: the key is telemetry.enable; one of the three old configs
      -- used "enabled", which lua_ls ignores
      telemetry = { enable = false },
      format = {
        enable = true,
        defaultConfig = {
          indent_style = "space",
          indent_size = "2",
          quote_style = "double",
          call_arg_parentheses = "keep",
        },
      },
    },
  },
}
