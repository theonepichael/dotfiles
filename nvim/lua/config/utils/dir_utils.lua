-- ~/.config/nvim/lua/config/utils/dir_utils.lua
-- Note: nothing in the rest of the config currently requires this
-- module; it is kept (fixed) because it is a standalone utility.
local M = {}

M.project_markers = {
  ".git",
  "package.json",
  "Cargo.toml",
  "pom.xml",
  "go.mod",
  "Makefile",
  ".projectroot",
}

M.root_cache = {}
M.cache_keys = {}
M.last_search_mode = "project"
M.max_cache_size = 100
M.project_detection = "shallow" -- "shallow" or "deep"
M.large_dir_threshold = 1000
M.skip_confirmation = false
-- REMOVED: is_calculating_root "lock". Neovim Lua is single-threaded, so
-- it could never guard a real race; all it did was silently return the
-- wrong (uncached, unresolved) directory if a nested call ever happened.

-- Persist search mode in stdpath("data"), not inside the config repo.
local data_file = vim.fn.stdpath("data") .. "/telescope_search_mode.txt"

function M.update_search_mode(mode)
  M.last_search_mode = mode
  vim.notify("Search mode: " .. mode)
  M.save_search_mode()
end

function M.save_search_mode()
  local file = io.open(data_file, "w")
  if file then
    file:write(M.last_search_mode)
    file:close()
  end
end

function M.load_search_mode()
  local file = io.open(data_file, "r")
  if file then
    local mode = file:read("*l")
    file:close()
    if mode then
      M.last_search_mode = mode
    end
  end
end

-- MODERNIZED: vim.fs.root does the marker search in one call.
function M.find_project_root(start_dir)
  start_dir = start_dir or vim.fn.expand("%:p:h")
  if vim.fn.isdirectory(start_dir) == 0 then
    return vim.fn.getcwd()
  end
  start_dir = vim.fn.resolve(start_dir)

  if M.project_detection == "deep" then
    -- Deepest (most specific) marker wins: walk up collecting every
    -- directory that contains any marker.
    local roots = {}
    for dir in vim.fs.parents(start_dir .. "/x") do
      for _, marker in ipairs(M.project_markers) do
        if vim.uv.fs_stat(vim.fs.joinpath(dir, marker)) then
          table.insert(roots, dir)
          break
        end
      end
    end
    if #roots > 0 then
      table.sort(roots, function(a, b)
        return #a > #b
      end)
      return roots[1]
    end
  else
    local root = vim.fs.root(start_dir, M.project_markers)
    if root then
      return root
    end
  end
  return start_dir
end

function M.get_cached_project_root(start_dir)
  start_dir = start_dir or vim.fn.expand("%:p:h")
  if M.root_cache[start_dir] then
    return M.root_cache[start_dir]
  end

  local root = M.find_project_root(start_dir)

  if #M.cache_keys >= M.max_cache_size then
    local oldest_key = table.remove(M.cache_keys, 1)
    M.root_cache[oldest_key] = nil
  end
  M.root_cache[start_dir] = root
  table.insert(M.cache_keys, start_dir)
  return root
end

function M.get_buffer_parent_dir()
  -- FIX: expand("%p") was a typo for "%:p" (it expanded to the filename
  -- with a literal p appended in some contexts)
  local current_file = vim.fn.expand("%:p")
  if current_file == "" then
    return vim.fn.getcwd()
  end
  return vim.fn.fnamemodify(current_file, ":h")
end

function M.get_visual_selection()
  local save_previous = vim.fn.getreg("a")
  vim.cmd('normal! "ay')
  local selection = vim.fn.getreg("a")
  vim.fn.setreg("a", save_previous)
  local escaped_selection = selection:gsub("\n", ""):gsub("[%(%)%.%+%-%*%?%[%]%^%$%%]", "\\%1")
  return escaped_selection
end

function M.find_closest_buffer_to_root()
  local closest_buffer_dir = nil
  local project_root = nil
  local min_depth = math.huge

  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) then
      local bufname = vim.api.nvim_buf_get_name(buf)
      -- FIX: the old check was `buf ~= ""` (comparing a buffer NUMBER to
      -- a string, always true); the intent was the buffer NAME.
      if bufname ~= "" and vim.fn.filereadable(bufname) == 1 then
        local buf_dir = vim.fn.fnamemodify(bufname, ":h")
        local buf_project_root = M.get_cached_project_root(buf_dir)

        local path_separator = package.config:sub(1, 1)
        local relative_path = buf_dir
        if buf_dir:find(buf_project_root, 1, true) == 1 then
          relative_path = buf_dir:sub(#buf_project_root + 2)
        end
        local depth = select(2, relative_path:gsub(path_separator, ""))

        if depth < min_depth then
          min_depth = depth
          closest_buffer_dir = buf_dir
          project_root = buf_project_root
        end
      end
    end
  end

  if not closest_buffer_dir then
    closest_buffer_dir = vim.fn.getcwd()
    project_root = M.get_cached_project_root(closest_buffer_dir)
  end
  return closest_buffer_dir, project_root
end

function M.clear_cache()
  M.root_cache = {}
  M.cache_keys = {}
  vim.notify("Project root cache cleared")
end

function M.setup(opts)
  opts = opts or {}

  if opts.additional_markers then
    vim.list_extend(M.project_markers, opts.additional_markers)
  end
  if opts.max_cache_size then
    M.max_cache_size = opts.max_cache_size
  end
  if opts.project_detection then
    if opts.project_detection ~= "shallow" and opts.project_detection ~= "deep" then
      vim.notify("Invalid project_detection value. Using 'shallow'.", vim.log.levels.WARN)
    else
      M.project_detection = opts.project_detection
    end
  end
  if opts.large_dir_threshold then
    M.large_dir_threshold = opts.large_dir_threshold
  end
  if opts.skip_confirmation ~= nil then
    M.skip_confirmation = opts.skip_confirmation
  end

  local group = vim.api.nvim_create_augroup("DirUtilsCache", { clear = true })
  vim.api.nvim_create_autocmd("DirChanged", {
    group = group,
    callback = M.clear_cache,
  })
  vim.api.nvim_create_autocmd({ "BufNewFile", "BufReadPost" }, {
    group = group,
    callback = function()
      local buf_dir = vim.fn.expand("%:p:h")
      if buf_dir ~= "" and not M.root_cache[buf_dir] then
        M.get_cached_project_root(buf_dir)
      end
    end,
  })

  M.load_search_mode()
end

return M
