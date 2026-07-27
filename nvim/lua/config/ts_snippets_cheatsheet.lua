-- ~/.config/nvim/lua/config/ts_snippets_cheatsheet.lua
local M = {}

local general_snippets = {
  { trigger = "imp", description = "Import statement" },
  { trigger = "imn", description = "Import no brackets" },
  { trigger = "imd", description = "Import destructured" },
  { trigger = "ime", description = "Import everything" },
  { trigger = "log", description = "Console log" },
  { trigger = "cl", description = "Console log with variable" },
  { trigger = "clo", description = "Console log object" },
  { trigger = "clj", description = "Console log JSON" },
  { trigger = "fn", description = "Arrow function" },
  { trigger = "afn", description = "Async arrow function" },
  { trigger = "nfn", description = "Named function" },
  { trigger = "anfn", description = "Async named function" },
  { trigger = "dnfn", description = "Default exported named function" },
  { trigger = "dafn", description = "Default exported async function" },
  { trigger = "if", description = "If statement" },
  { trigger = "el", description = "Else statement" },
  { trigger = "ife", description = "If/else statement" },
  { trigger = "tc", description = "Try/catch" },
  { trigger = "tcf", description = "Try/catch/finally" },
  { trigger = "for", description = "For loop" },
  { trigger = "forin", description = "For in loop" },
  { trigger = "forof", description = "For of loop" },
  { trigger = "map", description = "Array map" },
  { trigger = "filter", description = "Array filter" },
  { trigger = "reduce", description = "Array reduce" },
  { trigger = "while", description = "While loop" },
  { trigger = "enum", description = "Enum" },
  { trigger = "int", description = "Interface" },
  { trigger = "tp", description = "Type" },
  { trigger = "class", description = "Class" },
  { trigger = "con", description = "Constructor" },
  { trigger = "cp", description = "Class with constructor" },
  { trigger = "met", description = "Method" },
  { trigger = "pget", description = "Property getter" },
  { trigger = "pset", description = "Property setter" },
  { trigger = "tsg", description = "TypeScript Generic" },
}

local react_snippets = {
  { trigger = "rfc", description = "React Functional Component" },
  { trigger = "rfce", description = "React Functional Component with export" },
  { trigger = "rafce", description = "React Arrow Function Component with export" },
  { trigger = "rmc", description = "React Memo Component" },
  { trigger = "hook", description = "Custom React Hook" },
  { trigger = "us", description = "useState Hook" },
  { trigger = "ue", description = "useEffect Hook" },
  { trigger = "ur", description = "useRef Hook" },
  { trigger = "uc", description = "useCallback Hook" },
  { trigger = "um", description = "useMemo Hook" },
  { trigger = "uctx", description = "useContext Hook" },
}

function M.show_cheatsheet()
  vim.cmd("botright new")
  local buf = vim.api.nvim_get_current_buf()

  vim.api.nvim_buf_set_name(buf, "TypeScript Snippets Cheatsheet")
  vim.bo[buf].filetype = "markdown"
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false

  local content = {
    "# TypeScript Snippets Cheatsheet",
    "",
    "These snippets are available in `.ts` and `.tsx` files via friendly-snippets.",
    "Press TAB after typing the trigger to expand the snippet.",
    "",
    "## General TypeScript",
    "",
  }
  for _, snippet in ipairs(general_snippets) do
    table.insert(content, string.format("- `%s` - %s", snippet.trigger, snippet.description))
  end
  vim.list_extend(content, { "", "## React TypeScript", "" })
  for _, snippet in ipairs(react_snippets) do
    table.insert(content, string.format("- `%s` - %s", snippet.trigger, snippet.description))
  end
  vim.list_extend(content, {
    "",
    "## Usage",
    "",
    "1. Type the snippet trigger in a TypeScript file",
    "2. Press Tab to expand the snippet",
    "3. Use Tab/Shift+Tab to navigate between placeholders",
    "4. Use `<leader>sl` to see all available snippets",
    "",
    "Press `q` to close this window",
  })

  vim.api.nvim_buf_set_lines(buf, 0, -1, false, content)
  vim.bo[buf].modifiable = false
  vim.keymap.set("n", "q", "<cmd>close<CR>", { buffer = buf, nowait = true, silent = true })
end

vim.api.nvim_create_user_command("TypeScriptSnippets", M.show_cheatsheet, {
  desc = "Show TypeScript snippets cheatsheet",
})

vim.keymap.set("n", "<Leader>tsc", M.show_cheatsheet, {
  desc = "Show TypeScript snippets cheatsheet",
  silent = true,
})

return M
