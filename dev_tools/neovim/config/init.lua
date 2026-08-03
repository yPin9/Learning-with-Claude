-- 讀碼機器 完整 config（全課累加終態）── dev_tools/neovim
-- 從 Ch0 骨架長到 Ch30，每一塊都在對應章節解釋過。
-- 注意：nvim-treesitter-textobjects 的 move（]f/[f）在 nvim 0.12 + master 分支有相容性 bug（見 Ch14），select（af/if）正常。
vim.g.mapleader = " "
vim.opt.number = true
vim.opt.relativenumber = true
vim.opt.mouse = "a"
vim.opt.ignorecase = true
vim.opt.smartcase = true
vim.opt.scrolloff = 8
vim.opt.hlsearch = true
vim.opt.incsearch = true
vim.opt.grepprg = "rg --vimgrep --smart-case"           -- Ch10
vim.opt.grepformat = "%f:%l:%c:%m"
vim.opt.tags = "./tags;,tags"                            -- Ch24

-- bootstrap lazy.nvim
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.uv.fs_stat(lazypath) then
  vim.fn.system({ "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git", "--branch=stable", lazypath })
end
vim.opt.rtp:prepend(lazypath)

require("lazy").setup({
  -- Part 3: treesitter（釘 master，見 Ch13）+ textobjects + context
  { "nvim-treesitter/nvim-treesitter", branch = "master", build = ":TSUpdate",
    dependencies = {
      { "nvim-treesitter/nvim-treesitter-textobjects", branch = "master" },
    },
    opts = {
      ensure_installed = { "c", "lua" },
      highlight = { enable = true },
      incremental_selection = { enable = true, keymaps = {
        init_selection = "<CR>", node_incremental = "<CR>", node_decremental = "<BS>" } },
      textobjects = {
        select = { enable = true, lookahead = true, keymaps = {
          ["af"] = "@function.outer", ["if"] = "@function.inner",
          ["aa"] = "@parameter.outer", ["ia"] = "@parameter.inner" } },
        move = { enable = true, set_jumps = true, goto_next_start = {
          ["]f"] = "@function.outer" }, goto_previous_start = { ["[f"] = "@function.outer" } },
      },
    },
    config = function(_, opts) require("nvim-treesitter.configs").setup(opts) end },
  { "nvim-treesitter/nvim-treesitter-context", opts = {} },   -- Ch15 sticky context

  -- Part 2: telescope + fzf-native
  { "nvim-telescope/telescope.nvim", branch = "0.1.x",
    dependencies = { "nvim-lua/plenary.nvim",
      { "nvim-telescope/telescope-fzf-native.nvim", build = "make" } },
    config = function()
      local t = require("telescope")
      t.setup({})
      pcall(t.load_extension, "fzf")
    end,
    keys = {
      { "<leader>ff", "<cmd>Telescope find_files<cr>" },
      { "<leader>fg", "<cmd>Telescope live_grep<cr>" },
      { "<leader>fb", "<cmd>Telescope buffers<cr>" },
      { "<leader>fw", "<cmd>Telescope grep_string<cr>" },
      { "<leader>fs", "<cmd>Telescope lsp_dynamic_workspace_symbols<cr>" },
    } },

  { "neovim/nvim-lspconfig" },
  { "ludovicchabant/vim-gutentags" },                        -- Ch24 自動 ctags

  -- Part 6
  { "ThePrimeagen/harpoon", branch = "harpoon2", dependencies = { "nvim-lua/plenary.nvim" },
    config = function()
      local h = require("harpoon"); h:setup()
      vim.keymap.set("n", "<leader>a", function() h:list():add() end)
      vim.keymap.set("n", "<C-e>", function() h.ui:toggle_quick_menu(h:list()) end)
      for i = 1,4 do vim.keymap.set("n", "<leader>"..i, function() h:list():select(i) end) end
    end },
  { "folke/persistence.nvim", event = "BufReadPre", opts = {} },
}, { rocks = { enabled = false } })

-- gutentags：ctags module，快取不弄髒 repo（Ch24）
vim.g.gutentags_modules = { "ctags" }
vim.g.gutentags_cache_dir = vim.fn.stdpath("cache") .. "/tags"
vim.g.gutentags_ctags_extra_args = { "--fields=+niazS" }

-- Quickfix 導航（Ch12）
vim.keymap.set("n", "]q", "<cmd>cnext<cr>zz")
vim.keymap.set("n", "[q", "<cmd>cprev<cr>zz")

-- LSP: clangd + 完整讀碼鍵位（Ch0/Ch19/Ch22）
vim.lsp.enable("clangd")
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = { buffer = ev.buf }
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, o)
    vim.keymap.set("n", "gD", vim.lsp.buf.declaration, o)
    vim.keymap.set("n", "gr", vim.lsp.buf.references, o)
    vim.keymap.set("n", "gi", vim.lsp.buf.implementation, o)
    vim.keymap.set("n", "K",  vim.lsp.buf.hover, o)
    vim.keymap.set("n", "<leader>ds", vim.lsp.buf.document_symbol, o)
    vim.keymap.set("n", "<leader>ci", vim.lsp.buf.incoming_calls, o)
    vim.keymap.set("n", "<leader>co", vim.lsp.buf.outgoing_calls, o)
  end,
})
vim.diagnostic.config({ virtual_text = true, severity_sort = true })
vim.keymap.set("n", "]d", function() vim.diagnostic.jump({ count = 1 }) end)
vim.keymap.set("n", "[d", function() vim.diagnostic.jump({ count = -1 }) end)

-- treesitter 折疊（Ch15）
vim.opt.foldmethod = "expr"
vim.opt.foldexpr = "v:lua.vim.treesitter.foldexpr()"
vim.opt.foldenable = false

-- GNU Global 查詢灌 quickfix（Ch25；nvim 0.12 無內建 cscope）
local function global_query(flag)
  local sym = vim.fn.expand("<cword>")
  local out = vim.fn.systemlist({ "global", "--result=grep", flag, sym })
  if #out == 0 then vim.notify("global: no results for " .. sym); return end
  vim.fn.setqflist({}, " ", { title = "global " .. flag .. " " .. sym, lines = out })
  vim.cmd("copen")
end
vim.keymap.set("n", "<leader>gd", function() global_query("-x") end)   -- 定義
vim.keymap.set("n", "<leader>gr", function() global_query("-rx") end)  -- 所有 reference/caller
vim.api.nvim_create_user_command("Gtags", function() vim.fn.system({ "global", "-u" }) end, {})
