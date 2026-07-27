# Neovim Config Refactor: Changes

Target: Neovim 0.11+. Full lazy.nvim restructure: plugin config lives in
`lua/plugins/*.lua` specs, core editor config in `lua/config/`, native
LSP configs in `lsp/`. The old `plugin/setup.lua` manual require chain is
gone.

## New layout

```
init.lua
lsp/
  lua_ls.lua
  jedi_language_server.lua
lua/config/
  lazy.lua  options.lua  keymaps.lua  autocmds.lua  diagnostics.lua
  functions.lua  commandpeek.lua  splitswap.lua  shellcheck_qf.lua
  telescope_search.lua  ts_snippets_cheatsheet.lua
  utils/url.lua  utils/testing.lua  utils/dir_utils.lua
lua/plugins/
  colorscheme.lua  ui.lua  editor.lua  explorer.lua  telescope.lua
  treesitter.lua  lsp.lua  typescript.lua  completion.lua
  formatting.lua  git.lua  dap.lua  terminal.lua  sessions.lua
  markdown.lua
after/ftplugin/lua.lua  after/ftplugin/java.lua
scripts/ts-diag-test.lua   (manual :luafile script, not auto-loaded)
```

## Logical errors fixed

1. **lua_ls configured 3x with conflicting settings** (setup.lua inline,
   lua-lsp.lua, conform.lua). Consolidated into `lsp/lua_ls.lua`.
2. **Java LSP could never start**: `lspconfig.java_language_server` was
   given `cmd = { "jdtls" }` (two incompatible servers). Now jdtls is
   enabled natively when the binary exists; see `after/ftplugin/java.lua`.
3. **Triple format-on-save for Lua** (conform `format_on_save` + two
   separate BufWritePre autocmds). One path remains, in
   `plugins/formatting.lua`. Also `lua = {}` in conform means "no
   formatter", not "use LSP" — replaced with `lsp_format = "fallback"`.
4. **splitswap sort comparator** was not a strict weak ordering and could
   crash `table.sort`; window state capture also saved global options as
   window options and `winsaveview()` always read the active window.
5. **`vim.diagnostic.config()` (global) called from three on_attach
   callbacks** with different values; display depended on attach order.
   Now set once in `config/diagnostics.lua`, along with a single
   CursorHold float autocmd (previously duplicated per attach) and shared
   LspAttach keymaps.
6. **treesitter spec used packer's `run =`** which lazy.nvim ignores, so
   parser updates never ran. Migrated to the `main` branch (`master` is
   frozen): `require("nvim-treesitter").install()` + `vim.treesitter.start()`
   per filetype; textobjects migrated to the main-branch API with the same
   keymaps (af/if/ac/ic/ai/ii, ]w ]] [w [[ etc.).
7. **mason-lspconfig spec was `lazy = true` with a `config` that never
   executed**, so `ensure_installed` never ran; the list also contained
   `prettier` (not an LSP), `pyright` (you use jedi) and `ts_ls`
   (typescript-tools owns TS). Repos updated `williamboman/*` →
   `mason-org/*`.
8. **persisted.nvim spec had `lazy = false` and `cmd = {...}`**
   simultaneously; the cmd list was dead. Kept eager load (autostart).
9. **nvim-cmp config was dead and broken**: commented out of the load
   chain, and it required `nvim-autopairs`, which was never installed.
   Removed along with the five `hrsh7th/cmp-*` deps and
   `MunifTanjim/prettier.nvim` (duplicated conform's prettierd).
   blink.cmp is the single completion engine; dropped the obsolete
   `leiserfg/blink_luasnip` bridge (native `snippets.preset = "luasnip"`).
10. **neo-tree config was dead code** (plugin never in the spec, module
    never required) whose keymaps duplicated nvim-tree's `<Leader>e` /
    `<Leader>fe`. Removed; nvim-tree stays.
11. **focus.nvim was configured but never installed** (not in the plugin
    spec); its config file and the `vim.b.focus_disable` autocmd were
    dead. Removed. Re-add `nvim-focus/focus.nvim` as a spec if you want it.
12. **luasnip.config.setup received blink/nvim-cmp option shapes**
    (`snippets = {expand/active/jump}`, `disable_filetype`) that LuaSnip
    ignores. Removed. `descr` → `desc`. `jumpable` → `locally_jumpable`.
13. **typescript-tools keymaps called `:Typescript*` commands** from the
    deprecated typescript.nvim plugin; the real commands are `:TSTools*`.
    The malformed `commands` table (invalid option, anonymous function as
    array element) became a real user command.
14. **dap: `vim.fn.sign_define(...)` sat inside the
    `dap.configurations.python` list**, registering its return value as a
    bogus launch config. Hardcoded debugpy path now falls back to
    `exepath("python3")`.
15. **Truthiness bugs**: `vim.fn.filereadable(file)` used as a boolean
    (0 is truthy in Lua) in `open_files_if_specified`; `buf ~= ""`
    compared a buffer *number* against a string in dir_utils;
    `expand("%p")` typo for `"%:p"`; the remote-file check used a
    vim-regex string as a Lua pattern and never matched.
16. **Accidental globals**: `process_git_diff`, `process_diff_output`,
    `copy_to_clipboard`, `show_in_buffer` are now locals.
17. **`:Grep`**: `-g` globs now only added when grepprg is actually rg;
    quickfix context set only after a successful grep; the
    close-quickfix loop no longer re-lists windows while closing them;
    the BufWritePost re-run checks list context before membership;
    stray debug `print` removed.
18. **options.lua**: `suffixesadd:append(".py", ".lua", ...)` appended
    only `.py` (single-arg method) — now a table; `showbreak = "↪\\"`
    embedded a literal backslash; the dashboard FileType autocmd matched
    `*` and set *global* number options on every filetype change — now
    scoped to `dashboard` + window-local.
19. **commandpeek**: `nvim_cmd({cmd = ...})` rejects command lines with
    arguments — replaced with `nvim_exec2`; `bufhidden=wipe` made the
    buffer-reuse branch unreachable — now `hide`; cursor reset scoped to
    the output window.
20. **shellcheck_qf**: BufWritePost pattern was `*` (ran on every save of
    every file); errorformat was restored *before* the list was parsed —
    now passed directly via `setqflist{efm=}`; shellcheck runs async.
21. **telescope**: `get_project_root` filtered LSP clients to the current
    buffer (previously any client session-wide won); grep glob
    `!git/*` → `!.git/*`; GNU-grep fallback used rg-only flags; buffer
    picker no longer flips your line numbers as a side effect;
    `search_nvim_config` deduplicated into `search_at_path`.
22. **oil**: hidden-files toggle no longer tracks its own drifting state;
    git checks are async.
23. **gitsigns**: `next_hunk`/`prev_hunk` → `nav_hunk`,
    `undo_stage_hunk` → toggling `stage_hunk`, `toggle_deleted` →
    `preview_hunk_inline`, `watch_gitdir.interval` removed.
24. **trouble**: v2 options (position/height/icons/mode/...) removed from
    the v3 setup; your custom `mydiags` mode kept.
25. **nvim-tree**: `update_cwd` → `sync_root_with_cwd` /
    `update_focused_file.update_root`; invalid `decorators` list removed.

## Deprecated APIs replaced

`vim.loop` → `vim.uv` · `vim.highlight.on_yank` → `vim.hl.on_yank` ·
`vim.lsp.get_active_clients` → `vim.lsp.get_clients` ·
`nvim_err_writeln` → `vim.notify(..., ERROR)` ·
`nvim_set_keymap`/`nvim_buf_set_keymap` → `vim.keymap.set` ·
`source = "always"` → `source = true` · `lsp_fallback` → `lsp_format` ·
`nvim_treesitter#foldexpr()` → `v:lua.vim.treesitter.foldexpr()` ·
hand-rolled URL opener → `vim.ui.open` · `vim.fn.system` (blocking) →
`vim.system` where async matters.

## Removed on purpose

- `package.path` mangling in init.lua (modules now always required with
  full `config.*` names; the old dual paths loaded modules twice).
- `vim.lsp.set_log_level("debug")` + startup log-path print.
- Manual `vim.g.clipboard` xclip table (auto-detected since 0.10).
- `.editorconfig` file-writing side effect in conform.lua.
- `Y` → `y$` mapping (Neovim default).
- `echasnovski/mini.nvim` mega-plugin → `mini.statusline` + `mini.pairs`
  individually.

## External tools expected on PATH

rg, fd (telescope) · prettierd, isort, black, shfmt, jq, xmlformatter,
yamlfmt (conform) · debugpy env (dap) · shellcheck (optional module) ·
jdtls (optional Java LSP). lua_ls and jedi-language-server are installed
by mason automatically.
