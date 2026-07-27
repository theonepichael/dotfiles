-- ~/.config/nvim/lua/plugins/typescript.lua
return {
  {
    "pmizio/typescript-tools.nvim",
    ft = { "typescript", "typescriptreact", "javascript", "javascriptreact" },
    dependencies = {
      "nvim-lua/plenary.nvim",
      "neovim/nvim-lspconfig",
      "artemave/workspace-diagnostics.nvim",
    },
    config = function()
      local has_workspace_diag, workspace_diagnostics = pcall(require, "workspace-diagnostics")

      require("typescript-tools").setup({
        capabilities = require("blink.cmp").get_lsp_capabilities(),
        on_attach = function(client, bufnr)
          -- Diagnostics config + shared LSP keymaps are global now
          -- (config/diagnostics.lua). Only TS-specific bits live here.
          if has_workspace_diag then
            local ok, err = pcall(workspace_diagnostics.populate_workspace_diagnostics, client, bufnr)
            if not ok then
              vim.notify("Failed to initialize workspace diagnostics: " .. tostring(err), vim.log.levels.WARN)
            end
          end

          local function map(lhs, rhs, desc)
            vim.keymap.set("n", lhs, rhs, { buffer = bufnr, silent = true, desc = desc })
          end

          -- FIX: the old mappings called :TypescriptOrganizeImports etc.,
          -- which are typescript.nvim (deprecated plugin) commands.
          -- typescript-tools provides TSTools* commands.
          map("<leader>to", "<cmd>TSToolsOrganizeImports<CR>", "Organize imports")
          map("<leader>tA", "<cmd>TSToolsAddMissingImports<CR>", "Add missing imports")
          map("<leader>tR", "<cmd>TSToolsRenameFile<CR>", "Rename file and update imports")
          map("<leader>tu", "<cmd>TSToolsRemoveUnused<CR>", "Remove unused imports")

          if has_workspace_diag then
            map("<leader>tw", function()
              workspace_diagnostics.populate_workspace_diagnostics(client, bufnr)
              vim.notify("Refreshed workspace diagnostics")
            end, "Refresh workspace diagnostics")
          end
        end,

        settings = {
          tsserver_file_preferences = {
            importModuleSpecifierPreference = "relative",
            includeInlayParameterNameHints = "all",
            includeInlayParameterNameHintsWhenArgumentMatchesName = false,
            includeInlayFunctionParameterTypeHints = true,
            includeInlayVariableTypeHints = true,
            includeInlayPropertyDeclarationTypeHints = true,
            includeInlayFunctionLikeReturnTypeHints = true,
            includeInlayEnumMemberValueHints = true,
          },
          separate_diagnostic_server = true,
          publish_diagnostic_on = "insert_leave",
          expose_as_code_action = "all",
          complete_function_calls = true,
          include_completions_with_insert_text = true,
          tsserver_format_options = {
            insertSpaceAfterOpeningAndBeforeClosingEmptyBraces = true,
            insertSpaceAfterOpeningAndBeforeClosingNonemptyBraces = true,
            insertSpaceAfterOpeningAndBeforeClosingTemplateStringBraces = false,
            insertSpaceAfterTypeAssertion = true,
            indentSize = 2,
            tabSize = 2,
            convertTabsToSpaces = true,
            trimTrailingWhitespace = true,
          },
        },
        -- REMOVED vs old config:
        --  * `diagnose_all_project = true` — not a typescript-tools
        --    option (workspace-diagnostics.nvim handles project-wide)
        --  * `flags = { debounce_text_changes }` — lspconfig-era option
        --  * `commands = { TypescriptUpdateProjectDiagnostics = ... }` —
        --    not a typescript-tools option, and its table was malformed
        --    (the function was an unnamed array element). Recreated as a
        --    real user command below.
      })

      vim.api.nvim_create_user_command("TypescriptUpdateProjectDiagnostics", function()
        local clients = vim.lsp.get_clients({ name = "typescript-tools" })
        local bufnr = vim.api.nvim_get_current_buf()
        if #clients > 0 and has_workspace_diag then
          workspace_diagnostics.populate_workspace_diagnostics(clients[1], bufnr)
          vim.notify("Updated project diagnostics")
        else
          vim.notify("No active typescript-tools client", vim.log.levels.WARN)
        end
      end, { desc = "Update project diagnostics" })

      -- Snippet cheatsheet command + keymap
      require("config.ts_snippets_cheatsheet")
    end,
  },
}
