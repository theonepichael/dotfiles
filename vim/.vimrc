" .vimrc - Sensible Vim Configuration for macOS

" ===========================================================================
" Basic Settings
" ===========================================================================
" Note: 'nocompatible' is automatically set when a .vimrc is found, so removed

if has('filetype')
  filetype plugin indent on
endif

if has('syntax')
  syntax enable
endif

set encoding=utf-8

" Allow buffer switching without saving
set hidden

" Auto-reload files changed outside vim
set autoread

" Increase command history
set history=1000

" More undo levels
set undolevels=1000

" Detect file changes more quickly
set updatetime=300

" Brief highlight on yank (requires Vim 8.0.1394+)
augroup yank_highlight
    autocmd!
    autocmd TextYankPost * silent! call FlashYank()
augroup END

function! FlashYank()
    if v:event.operator !=# 'y' || len(v:event.regcontents) > 200
        return
    endif

    let l:lnum_start = line("'[")
    let l:lnum_end   = line("']")
    let l:positions  = []

    if v:event.regtype ==# 'V'
        for l:lnum in range(l:lnum_start, l:lnum_end)
            call add(l:positions, [l:lnum])
        endfor
    elseif v:event.regtype ==# 'v'
        let l:col_start = col("'[")
        let l:col_end   = col("']")
        if l:lnum_start == l:lnum_end
            call add(l:positions, [l:lnum_start, l:col_start, l:col_end - l:col_start + 1])
        else
            call add(l:positions, [l:lnum_start, l:col_start, col([l:lnum_start, '$']) - l:col_start])
            for l:lnum in range(l:lnum_start + 1, l:lnum_end - 1)
                call add(l:positions, [l:lnum])
            endfor
            call add(l:positions, [l:lnum_end, 1, l:col_end])
        endif
    else
        for l:lnum in range(l:lnum_start, l:lnum_end)
            call add(l:positions, [l:lnum])
        endfor
    endif

    if empty(l:positions)
        return
    endif

    let l:matches = []
    for l:i in range(0, len(l:positions) - 1, 8)
        call add(l:matches, matchaddpos('IncSearch', l:positions[l:i : l:i + 7]))
    endfor

    redraw
    let l:win = win_getid()
    call timer_start(200, {tid -> ClearFlash(l:win, l:matches)})
endfunction

function! ClearFlash(win, matches)
    if win_getid() !=# a:win
        return
    endif
    for l:id in a:matches
        silent! call matchdelete(l:id)
    endfor
    redraw
endfunction

" ===========================================================================
" UI Configuration
" ===========================================================================
set number
set relativenumber
set ruler
set showcmd
set noshowmode
set laststatus=2
set wildmenu
set wildmode=list:longest,full
set showmatch
set scrolloff=3         " Keep 3 lines above/below cursor
set sidescrolloff=5     " Keep 5 columns left/right of cursor
set backspace=indent,eol,start  " Make backspace work as expected
set noerrorbells
set visualbell          " Use visual bell (no beeping)
set mouse=a
set wrap
set linebreak           " Wrap at word boundaries
set colorcolumn=80
set cursorline

" ===========================================================================
" Search Settings
" ===========================================================================
set incsearch
set hlsearch
set ignorecase
set smartcase           " Override ignorecase when search contains uppercase

" ===========================================================================
" Indentation & Tabs
" ===========================================================================
set expandtab           " Use spaces instead of tabs
set tabstop=4
set softtabstop=4
set shiftwidth=4
set shiftround          " Round indent to multiple of shiftwidth
set autoindent
set smartindent

" ===========================================================================
" Leader Key & Shortcuts
" ===========================================================================
let mapleader = ' '     " Set leader key to space

" Map ; to : for easier command input
nnoremap ; :

" Config reloading
nnoremap <leader>r :source $MYVIMRC <bar> redraw <bar> echo "Config reloaded!" <bar> sleep 1500m<CR>

" Quick save and quit
nnoremap <leader>w :w<CR>
nnoremap <leader>q :q<CR>
nnoremap <leader>wq :wq<CR>

" Use 'ii' to exit insert mode quickly
inoremap ii <Esc>

" Quick split creation
nnoremap <leader>v :vsplit<CR>
nnoremap <leader>u :split<CR>

" Easy buffer navigation
nnoremap ]b :bnext<CR>
nnoremap [b :bprevious<CR>
nnoremap <leader>b :buffers<CR>:buffer<Space>

" Better split navigation with leader key (h,j,k,l)
nnoremap <leader>j <C-W><C-J>
nnoremap <leader>k <C-W><C-K>
nnoremap <leader>l <C-W><C-L>
nnoremap <leader>h <C-W><C-H>

" Yank all
nnoremap <leader>ya :%y+<CR>

" Map Option-L to clear search highlights and center cursor in normal mode
nnoremap ì :nohlsearch<CR>zz

" Center the cursor while in insert mode with Ctrl-L (stay in insert mode)
inoremap ì <C-o>zz

" Move vertically by visual line (for wrapped lines)
nnoremap j gj
nnoremap k gk

" Center the buffer after half/full-page scrolling.
" Keeps the cursor locked to the middle of the screen
" as you move through the file.
nnoremap <C-d> <C-d>zz
nnoremap <C-u> <C-u>zz

" ===========================================================================
" Cursor Settings (Mode-dependent cursor shape)
" ===========================================================================
" Function to set cursor shape based on mode and terminal
function! SetCursorStyle()
  " Modern ANSI escape sequences that work in most terminals
  if &term =~ "xterm\\|rxvt\\|ghostty\\|tmux"
    " Use the DECSCUSR escape sequences (works in most modern terminals)
    " 1 or 0 -> blinking block
    " 2 -> steady block
    " 3 -> blinking underscore
    " 4 -> steady underscore
    " 5 -> blinking vertical bar
    " 6 -> steady vertical bar
    let &t_SI = "\e[5 q" " Insert mode - vertical bar (as requested)
    let &t_SR = "\e[3 q" " Replace mode - underscore
    let &t_EI = "\e[1 q" " Normal mode - block (as requested)
  endif

  " Additional support for iTerm2/Ghostty on macOS
  if exists('$TERM_PROGRAM') && ($TERM_PROGRAM =~ "iTerm" || $TERM_PROGRAM =~ "Ghostty")
    " iTerm2/Ghostty proprietary sequences (may work better in some cases)
    let &t_SI .= "\<Esc>]50;CursorShape=1\x7" " Vertical bar in insert mode
    let &t_SR .= "\<Esc>]50;CursorShape=2\x7" " Underline in replace mode
    let &t_EI .= "\<Esc>]50;CursorShape=0\x7" " Block in normal mode
  endif

  " Tmux requires special handling to pass escape sequences through
  if exists('$TMUX')
    " Encapsulate the escape sequences for tmux
    let &t_SI = "\<Esc>Ptmux;\<Esc>" . &t_SI . "\<Esc>\\"
    let &t_SR = "\<Esc>Ptmux;\<Esc>" . &t_SR . "\<Esc>\\"
    let &t_EI = "\<Esc>Ptmux;\<Esc>" . &t_EI . "\<Esc>\\"
  endif
endfunction

call SetCursorStyle()

" ===========================================================================
" Clipboard Settings
" ===========================================================================
" Simplified clipboard handling for macOS
set clipboard=unnamed

" Additional clipboard mappings for explicit control
vnoremap <leader>y "+y
nnoremap <leader>p "+p
nnoremap <leader>P "+P

" ===========================================================================
" File Management
" ==========================================================================
" Function to safely create directory if it doesn't exist
function! EnsureDirectory(dir)
  let expanded_dir = expand(a:dir)
  if !isdirectory(expanded_dir)
    try
      call mkdir(expanded_dir, "p")
      return 1
    catch
      echohl WarningMsg
      echom "Warning: Unable to create directory: " . expanded_dir
      echom "Error: " . v:exception
      echohl None
      return 0
    endtry
  endif
  return 1
endfunction

" Set up backup, swap, and undo files
if EnsureDirectory('~/.vim/backup//')
  set backup
  set backupdir=~/.vim/backup//
else
  set nobackup
endif

if EnsureDirectory('~/.vim/swap//')
  set directory=~/.vim/swap//
else
  set noswapfile
endif

if has('persistent_undo')
  if EnsureDirectory('~/.vim/undo//')
    set undofile
    set undodir=~/.vim/undo//
  endif
endif

" ===========================================================================
" Performance Enhancements
" ===========================================================================
set lazyredraw          " Don't redraw during macros
set ttyfast             " Faster terminal connections

" ===========================================================================
" Color Scheme & Aesthetics
" ===========================================================================
" If you have a preferred color scheme, uncomment and replace 'desert'
" colorscheme desert

" ===========================================================================
" Auto Commands
" ===========================================================================
" Return to last edit position when opening files
augroup last_edit
  autocmd!
  autocmd BufReadPost *
       \ if line("'\"") > 0 && line("'\"") <= line("$") |
       \   exe "normal! g`\"" |
       \ endif
augroup END

" Strip trailing whitespace on save (safely)
augroup strip_whitespace
  autocmd!
  autocmd BufWritePre * if !&binary | silent! %s/\s\+$//e | endif
augroup END

" Filetype-specific settings
augroup filetype_settings
  autocmd!
  " Use 2 spaces for web files
  autocmd FileType html,css,javascript,typescript,json setlocal tabstop=2 softtabstop=2 shiftwidth=2
  " Use 4 spaces for Python
  autocmd FileType python setlocal tabstop=4 softtabstop=4 shiftwidth=4 textwidth=88
  " Use tabs for Makefiles
  autocmd FileType make setlocal noexpandtab
augroup END

" ===========================================================================
" Plugin Management (using vim-plug)
" ===========================================================================
" Automatically install vim-plug if not installed
let data_dir = '~/.vim'
if empty(glob(data_dir . '/autoload/plug.vim'))
  silent execute '!curl -fLo '.data_dir.'/autoload/plug.vim --create-dirs https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
endif

" Plugins will be downloaded under the specified directory
call plug#begin('~/.vim/plugged')

" Declare the list of plugins
" Uncomment or add plugins you want to use
Plug 'tpope/vim-sensible'
Plug 'tpope/vim-commentary'
" Plug 'preservim/nerdtree'
" Plug 'ctrlpvim/ctrlp.vim'
Plug 'vim-airline/vim-airline'
Plug 'catppuccin/vim', { 'as': 'catppuccin' }

" List ends here. Plugins become visible to Vim after this call
call plug#end()

" ===========================================================================
" Plugin Configuration
" ===========================================================================
" Add plugin-specific settings here
" NERDTree configuration example:
" let g:NERDTreeShowHidden = 1
" nnoremap <leader>n :NERDTreeToggle<CR>

" ===========================================================================
" Version-specific Settings
" ===========================================================================
" Enable features only in Vim 8.0+ or Neovim
if v:version >= 800 || has('nvim')
  set breakindent          " Indent wrapped lines
  set termguicolors        " Use GUI colors in terminal (if supported)
endif

" ===========================================================================
" End of Configuration
" ===========================================================================
" Auto-install any plugins not yet present on first run
if len(filter(values(g:plugs), '!isdirectory(v:val.dir)'))
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif
