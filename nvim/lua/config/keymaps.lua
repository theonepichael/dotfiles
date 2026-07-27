-- ~/.config/nvim/lua/config/keymaps.lua
-- FIX: converted from vim.api.nvim_set_keymap (string rhs, manual noremap)
-- to vim.keymap.set, which is noremap by default and takes Lua callbacks.
local map = vim.keymap.set
local opts = { silent = true }

-- Splits and window movement
map("n", "<Leader>v", "<cmd>vsplit<CR>", { silent = true, desc = "Vertical split current buffer" })
map("n", "<Leader>V", "<cmd>vnew<CR>", opts)
map("n", "<Leader>U", "<cmd>new<CR>", opts)
map("n", "<Leader>u", "<cmd>split<CR>", { silent = true, desc = "Horizontal split current buffer" })
map("n", "<Leader>h", "<C-w>h", opts)
map("n", "<Leader>l", "<C-w>l", opts)
map("n", "<Leader>j", "<C-w>j", opts)
map("n", "<Leader>k", "<C-w>k", opts)
map("n", "<C-Up>", "<cmd>resize +2<CR>", opts)
map("n", "<C-Down>", "<cmd>resize -2<CR>", opts)
map("n", "<C-Right>", "<cmd>vertical resize +2<CR>", opts)
map("n", "<C-Left>", "<cmd>vertical resize -2<CR>", opts)

-- Editing / navigation
map("n", "<Leader>m", "zz", opts)
map("n", ";", ":")
map("i", "<C-c>", "<Esc>", opts)
map("i", "ii", "<Esc>", opts)
map("v", "ii", "<Esc>", opts)
map("i", "<C-l>", "<C-o>zz", opts)
map("n", "<C-d>", "<C-d>zz", opts)
map("n", "<C-u>", "<C-u>zz", opts)
map("n", "<C-L>", "<cmd>nohlsearch<CR>zz", opts)
map("n", "*", "*zz", opts)
map("n", "#", "#zz", opts)
map("n", "<Leader>w", "<cmd>w<CR>")
map("n", "<Leader>ya", "<cmd>%+y<CR>")
-- Removed: map("n", "Y", "y$") is Neovim's built-in default.

-- Buffers
map("n", "]b", "<cmd>bnext<CR>", opts)
map("n", "[b", "<cmd>bprevious<CR>", opts)

-- Quickfix
map("n", "]q", "<cmd>cnext<CR>", opts)
map("n", "[q", "<cmd>cprev<CR>", opts)
map("n", "<Leader>cq", function()
  require("config.functions").clear_quickfix_list()
end, { silent = true, desc = "Clear quickfix list" })

-- File explorer (nvim-tree keymaps live in lua/plugins/explorer.lua so
-- they lazy-load the plugin; the old duplicate neo-tree <Leader>e /
-- <Leader>fe mappings were removed together with the dead neo-tree file)
map("n", "<Leader>n", function()
  require("config.functions").toggle_explorer()
end, opts)

-- Line numbers
map("n", "<Leader>N", function()
  require("config.functions").toggle_line_numbers()
end, { silent = true, desc = "Toggle line numbers" })

-- Tabs
map("n", "<M-t>", "<cmd>tabnew<CR>", opts)
map("n", "<Leader>tn", "<cmd>tabnext<CR>", opts)
map("n", "<Leader>tp", "<cmd>tabprevious<CR>", opts)

-- Test window layout
-- FIX: old rhs required "utils.testing", which only resolved through the
-- package.path hack and loaded the module under a second name.
map("n", "<leader>tt", function()
  require("config.utils.testing").setup_test_layout()
end, { silent = true, desc = "Test: Setup 1|[2/3] Layout" })

map("n", "<Leader>sa", "<cmd>SelectAll<CR>", opts)

-- Insert full path of current buffer at cursor as a comment line
map("n", "<Leader>irp", function()
  local path = vim.fn.expand("%:~")
  vim.api.nvim_put({ "-- " .. path }, "l", true, true)
end, { silent = true, desc = "Insert buffer path as comment" })

-- vim-abolish coercions (documentation, provided by tpope/vim-abolish):
--   crp PascalCase | crs snake_case | crc camelCase on word under cursor
