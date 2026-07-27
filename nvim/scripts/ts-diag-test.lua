-- ~/.config/nvim/scripts/ts-diag-test.lua
-- Standalone diagnostic script (NOT loaded by the config). Run manually:
--   :luafile ~/.config/nvim/scripts/ts-diag-test.lua
-- The old copy lived in plugin/config/ next to real config modules even
-- though nothing required it; it now lives in scripts/.

local has_plugin = pcall(require, "typescript-tools")
if not has_plugin then
  print("ERROR: typescript-tools.nvim is not installed")
  return
end

local function check_tsserver()
  -- FIX: replaced `which tsserver` shelling-out with exepath()
  local path = vim.fn.exepath("tsserver")
  if path == "" then
    print("ERROR: tsserver executable not found in PATH")
    print("Please install typescript globally: npm install -g typescript")
    return false
  end
  print("SUCCESS: tsserver found at: " .. path)
  return true
end

local function check_ts_version()
  -- FIX: `npm tsc --version` is not a valid npm invocation; use npx
  local result = vim.fn.system({ "npx", "--no-install", "tsc", "--version" })
  if vim.v.shell_error ~= 0 then
    print("ERROR: Failed to get TypeScript version")
    return false
  end
  print("TypeScript version: " .. vim.trim(result))
  return true
end

local test_dir = vim.fn.expand("~/ts-diag-test")

local function create_test_file()
  local test_file = test_dir .. "/test.ts"
  if vim.fn.isdirectory(test_dir) == 0 then
    vim.fn.mkdir(test_dir, "p")
  end
  local file = io.open(test_file, "w")
  if not file then
    print("ERROR: Failed to create test file")
    return nil
  end
  file:write([[
// TypeScript diagnostic test file
const myString: string = 42; // Type error
const unusedVar = "test"; // Unused variable

interface TestInterface {
  requiredProp: string;
}

// Missing required property
const test: TestInterface = {};

export {};
]])
  file:close()
  print("Created test file: " .. test_file)
  return test_file
end

local function create_tsconfig(dir)
  local tsconfig_path = dir .. "/tsconfig.json"
  local file = io.open(tsconfig_path, "w")
  if not file then
    print("ERROR: Failed to create tsconfig.json")
    return
  end
  file:write([[
{
  "compilerOptions": {
    "target": "es2016",
    "module": "commonjs",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["./*.ts"]
}
]])
  file:close()
  print("Created tsconfig.json: " .. tsconfig_path)
end

local function cleanup_test()
  vim.defer_fn(function()
    print("Press 'y' to clean up test files or any other key to keep them")
    local choice = vim.fn.nr2char(vim.fn.getchar())
    if choice:lower() == "y" then
      vim.cmd("bdelete!")
      if vim.fn.isdirectory(test_dir) == 1 then
        vim.fn.delete(test_dir, "rf")
        print("Test files cleaned up")
      end
    else
      print("Test files kept at " .. test_dir)
    end
  end, 5000)
end

local function run_diagnostics_test()
  if not check_tsserver() or not check_ts_version() then
    return false
  end
  local test_file = create_test_file()
  if not test_file then
    return false
  end
  create_tsconfig(test_dir)
  vim.cmd("edit " .. vim.fn.fnameescape(test_file))

  vim.defer_fn(function()
    print("Active LSP clients:")
    -- FIX: vim.lsp.get_active_clients() removed in 0.11; get_clients()
    for _, client in ipairs(vim.lsp.get_clients()) do
      print("- " .. client.name .. " (id: " .. client.id .. ")")
    end

    vim.defer_fn(function()
      local diags = vim.diagnostic.get(0)
      print("Diagnostics count: " .. #diags)
      if #diags > 0 then
        print("Diagnostics are working!")
        for i, diag in ipairs(diags) do
          if i <= 3 then
            print("- Line " .. diag.lnum .. ": " .. diag.message)
          end
        end
      else
        print("No diagnostics found. Something is wrong.")
      end
    end, 2000)
  end, 1000)

  return true
end

if run_diagnostics_test() then
  cleanup_test()
end
