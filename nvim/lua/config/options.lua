-- ~/.config/nvim/lua/config/options.lua
local o = vim.opt
local is_windows = vim.fn.has("win32") == 1

-- OS-specific settings
local undodir
if is_windows then
  o.shell = "cmd.exe"
  o.shellcmdflag = "/c"
  undodir = vim.fn.expand("$HOME\\vimfiles\\undodir")
else
  o.shell = "/bin/sh"
  o.shellcmdflag = "-c"
  undodir = vim.fn.expand("$HOME/.vim/undodir")
end

-- GUI settings
if vim.fn.has("gui_running") == 1 then
  o.guifont = "JetBrainsMono NFM ExtraBold:h16"
  o.guicursor = "n-v-c:block,i-ci-ve:ver25,r-cr:hor20,o:hor50"
end

-- Persistent undo
if vim.fn.isdirectory(undodir) == 0 then
  vim.fn.mkdir(undodir, "p")
end
o.undofile = true
o.undodir = undodir

-- General
o.confirm = true
o.visualbell = true
o.showmatch = true
o.matchtime = 2
o.matchpairs:append("<:>")
o.number = true
o.relativenumber = true
o.tabstop = 4
o.shiftwidth = 4
o.expandtab = true
o.wildmode = { "list:longest", "full" }
o.termguicolors = true
-- FIX: the old value "↪\\" put a literal backslash in the marker
o.showbreak = "↪ "
o.sessionoptions = "buffers,curdir,folds,globals,tabpages,winpos,winsize"
o.dictionary:append("/usr/share/dict/words")
o.spell = true
o.spelllang = "en_us"

-- Removed vs the old file (defaults in Neovim, redundant to set):
-- backspace, autoindent, hidden, laststatus=2, wildmenu, hlsearch.
-- Custom statusline removed: mini.statusline owns the statusline.

-- Treesitter-based folding
-- FIX: "nvim_treesitter#foldexpr()" is the legacy vimscript shim; the
-- native expression works on 0.11+ with any installed parser.
o.foldmethod = "expr"
o.foldexpr = "v:lua.vim.treesitter.foldexpr()"
o.foldlevelstart = 1

-- 'gf' extension search
-- FIX: :append() takes a single value; the old call
-- suffixesadd:append(".py", ".lua", ...) silently appended only ".py".
o.suffixesadd:append({ ".py", ".lua", ".js", ".cpp", ".java", ".sh", ".rs" })

-- Text wrapping
o.textwidth = 150
o.linebreak = true
o.formatoptions:append("t")

-- Ignore binaries in navigation/completion
o.wildignore:append({ "*.o", "*.obj", "*.exe", "*.dll", "*.pyc", "*.class", "*/.git/*", "*/.svn/*", "*/.hg/*" })

-- Mouse
o.mousemodel = "extend"
o.mouse = "a"

-- Grep program (ripgrep if available)
if vim.fn.executable("rg") == 1 then
  o.grepprg = "rg --vimgrep --no-heading --smart-case --hidden --ignore-file " .. vim.fn.expand("~/.ignore")
  o.grepformat = "%f:%l:%c:%m,%f:%l:%m"
else
  o.grepprg = "grep -nRH $*"
  o.grepformat = "%f:%l:%m"
end

-- Cursor line
o.cursorline = true
vim.api.nvim_set_hl(0, "CursorLine", { bold = true, underline = true, ctermbg = 0, ctermfg = 15 })

-- Clipboard: unnamedplus is enough on Neovim; providers are auto-detected.
o.clipboard = "unnamedplus"

-- Faster CursorHold (used by the diagnostic float autocmd).
-- FIX: previously this was set from inside three different LSP on_attach
-- callbacks; it is a global option and belongs here, set once.
o.updatetime = 250

-- Windows console cursor shape fix
if is_windows then
  vim.cmd([[
    let &t_SI = "\e[6 q"
    let &t_EI = "\e[2 q"
    let &t_SR = "\e[4 q"
  ]])
  vim.api.nvim_create_autocmd("VimEnter", {
    group = vim.api.nvim_create_augroup("ResetCursorShape", { clear = true }),
    command = [[silent !echo -ne "\e[2 q"]],
  })
end
