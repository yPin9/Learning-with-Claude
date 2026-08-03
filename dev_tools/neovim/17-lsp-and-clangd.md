# Ch 17 — LSP 與 clangd

> **目標**：進入全課的語意核心。前面 treesitter 讓你看懂**結構**（這是函式、這是 if），但它不懂**語意**——它不知道 `list->free` 這個 `free` 到底綁到哪個定義。這章開始，我們讓 Neovim 接上 clangd，一個真正把你的 code 編譯成 AST 的 language server，從此跳轉是「算出來的」不是「猜出來的」。學完你會懂 LSP 這套協定的本體（編輯器與後台程式之間的 JSON-RPC 對話）、`vim.lsp.enable("clangd")` 到底做了什麼、clangd attach 的流程，並會用 headless 驗證 clangd 真的接上了。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14。本章所有 clangd 輸出——attach、capabilities、跳轉——都是在隔離 XDG 環境對真檔案跑 clangd-14 照抄的。

## 為什麼需要這個？先看文字工具的天花板

到 Part 3 為止，你手上的工具都是**純語法**的：telescope + ripgrep 找字串、treesitter 認結構。它們都很快、很通用，但有一個共同的根本限制——**它們不懂型別與作用域**。

看一個具體的例子。假設你在讀一段 C，游標停在 `list->free(node->value)` 這行的 `free` 上，你想跳到它的定義。用 ctags（純語法索引）查 `free`，一個中型專案可能給你這個：

```
free  src/adlist.h   /^    void (*free)(void *ptr);$/     ← linked list struct 的函式指標欄位
free  src/module.c   /^        moduleTypeFreeFunc free;$/  ← 某 struct 的型別欄位
free  src/server.h   /^    moduleTypeFreeFunc free;$/      ← 另一個 struct 的欄位
free  src/zmalloc.c  /^#define free(/                       ← macro 重定義
```

**四個 `free`，ctags 全列出來讓你自己猜。** 它無法告訴你「游標下這一個 `free` 是哪一個」——因為它不知道 `list` 是什麼型別，就不能推斷 `list->free` 指向哪個欄位。這是純語法工具的天花板：**不懂 scope，不懂 type，只認名字**。

clangd 懂。它做的事跟 ctags 完全不同層次：**它把你的 code 真的編譯成 AST**，知道 `list` 的宣告型別是 `list *`，知道那個 struct 有個叫 `free` 的欄位，於是 `list->free` 的 `free` 精準指向那**唯一一個**定義——不猜，是算出來的。

這就是 Part 4 要做的事：把一個「懂編譯」的後台程式接進 Neovim，讓「跳到定義」「找所有引用」「這是什麼型別」變成精準操作。這正是 `reading_code` Ch 13 的落地——那章教你 LSP 的原理與手動驅動協定，這門課教你在 Neovim 裡把它變成肌肉。

## 先建立直覺：LSP 是一個協定，不是一個功能

在 LSP 出現之前，每個編輯器要支援每個語言的智慧功能（跳轉、補全、重構），都得各自實作一遍。M 個編輯器 × N 個語言 = `M×N` 份重複工。VS Code 要懂 C++、要懂 Rust、要懂 Python，各寫一套；Vim 要懂這些，再各寫一套。災難。

微軟 2016 年提出 **Language Server Protocol（LSP，語言伺服器協定）** 把這件事拆開：

```
        沒有 LSP：M×N                        有 LSP：M+N
   ┌──────┬──────┬──────┐              編輯器           語言 server
   │ VS   │ Vim  │Emacs │            ┌────────┐        ┌──────────┐
   │ Code │      │      │            │VS Code │─┐   ┌─▶│ clangd   │(C/C++)
   ├──────┼──────┼──────┤            ├────────┤ │   │  ├──────────┤
C++│ 各寫 │ 各寫 │ 各寫 │            │Neovim  │─┼─LSP─┼─▶│rust-anal.│(Rust)
   ├──────┼──────┼──────┤            ├────────┤ │   │  ├──────────┤
Rust│ 各寫 │ 各寫 │ 各寫 │           │Emacs   │─┘   └─▶│ pyright  │(Python)
   └──────┴──────┴──────┘            └────────┘        └──────────┘
   每格都要重寫一遍智慧功能           編輯器只要會講 LSP，各語言只要出一個 server
```

- **Language Server（語言伺服器）**：一個獨立行程，懂某個語言的一切語意。C/C++ 是 clangd、Rust 是 rust-analyzer、Python 是 pyright。
- **Client（客戶端，就是編輯器）**：只要會講 LSP 這套協定，就能用上任何 language server 的智慧。

`M×N` 變成 `M+N`。這解釋了一件重要的事：**VS Code、Neovim、Emacs 用 clangd 得到的「跳到定義」品質完全一樣**——因為它們背後跑的是同一個 clangd 行程，只是各自用 LSP 跟它對話。你這章學的不是「Neovim 的功能」，是所有現代編輯器共享的底層機制。換編輯器不會失去語意能力，因為能力在 server，不在編輯器。

## Neovim 內建 LSP client：說協定，接任何 server

關鍵事實：**Neovim 內建一個 LSP client**（`vim.lsp`），它本身不懂 C、不懂 Rust，它只會講 LSP 這套協定。它是那個「會講 LSP 的編輯器」，接上哪個 server 就懂哪個語言。

這跟老 Vim 的外掛生態完全不同。以前 Vim 靠 `YouCompleteMe`、`coc.nvim` 這種重量級外掛把 LSP 能力塞進來。Neovim 從 0.5 開始把 LSP client **做進核心**，`vim.lsp.*` 是內建 API，不需要外掛就能跟 language server 對話。

那 `nvim-lspconfig` 是什麼？它**不是** LSP client，它只是一包「各家 server 的預設設定」——clangd 要怎麼啟動、rust-analyzer 的參數是什麼、root 目錄怎麼認。Neovim 的 client 是引擎，lspconfig 只是一本「各廠牌 server 的說明書」。這個區分很重要：卡住時你要分清楚是 client（Neovim 核心）的問題，還是設定（lspconfig）的問題。

```
   你的 .c buffer
        │
        ▼
   Neovim LSP client（vim.lsp，內建，只會講協定）
        │  JSON-RPC over stdio
        ▼
   clangd 行程（懂 C/C++，真的在編譯）
        │
        ▼
   回一則 JSON：定義在哪、型別是什麼、有哪些引用
```

## clangd 是什麼：基於真編譯前端，所以最準

clangd 是 **LLVM 出品**的 C/C++ language server。它的殺手鐧只有一句話：**它是真正的編譯前端**。它用 Clang（LLVM 的 C/C++ 編譯器前端）把你的檔案編成完整 AST，含每個符號的型別、每個名字的作用域綁定。

這是它相對 ctags/gtags 最深的差別：

| | ctags / gtags | clangd |
|---|---|---|
| 怎麼運作 | 掃文字、記 pattern | **真的編譯成 AST** |
| 懂型別/scope | 否 | **是** |
| `list->free` 跳轉 | 給你一堆同名候選 | **唯一正確那個** |
| macro 展開後 | 看不到（只看展開前文字） | **看展開後真相** |
| 前提 | 無 | **要 `compile_commands.json`**（Ch 18） |
| 成本 | 極低 | 高（建 index、吃記憶體） |

「基於真實編譯前端」的代價，是它需要知道**每個檔怎麼編**——用哪些 `-I`（include 路徑）、哪些 `-D`（macro 定義）、哪個 `-std`。這份資訊就是 `compile_commands.json`，Ch 18 整章在講它，因為它是 clangd 精準的前提，也是讀碼者最常卡的地方。**這章你只要記住：clangd attach ≠ clangd 能精準跳轉，後者還缺 compile_commands.json。**

## 底層機制：nvim ↔ clangd 的 JSON-RPC 對話

LSP 的本體樸素得驚人：**Neovim 與 clangd 透過 stdin/stdout 互丟 JSON-RPC 訊息**。每則訊息是一個 HTTP 風格的 header（`Content-Length: N\r\n\r\n`）加一段 JSON body。核心方法就那幾個，名字直白：

| LSP method | 對應功能 | 你在 Neovim 按的鍵 |
|---|---|---|
| `initialize` | 握手、交換能力 | （啟動時自動） |
| `textDocument/didOpen` | 告訴 server「我開了這檔」 | （開檔時自動） |
| `textDocument/definition` | 跳到定義 | `gd` |
| `textDocument/references` | 找所有引用 | `gr` |
| `textDocument/hover` | 顯示型別/簽名 | `K` |
| `textDocument/documentSymbol` | 當前檔符號大綱 | `<leader>ds` |
| `callHierarchy/incomingCalls` | 誰呼叫我 | （Ch 19） |

**關鍵洞察**：你按的每一個跳轉鍵，本質上就是 Neovim 把游標的 `{檔案 URI, 行, 欄}` 包成一則 JSON 丟給 clangd，clangd 算完回一則 `{檔案 URI, 行, 欄}` 的 JSON，Neovim 據此開檔跳行。就這麼回事。`reading_code` Ch 13 用 Python 手動扮演編輯器把這對話跑了一遍，想看協定原文的細節去讀那章；這裡我們專注在「Neovim 怎麼自動化這一切」。

### attach 流程：從 enable 到接上

Ch 0 的 config 骨架有這行：

```lua
vim.lsp.enable("clangd")   -- Neovim 0.11+ 內建的 LSP 啟用 API
```

`vim.lsp.enable("clangd")` 做的事，是向 Neovim **註冊**一個規則：「以後只要開的 buffer 符合 clangd 的條件（filetype 是 c/cpp、且往上能找到 root 標記如 `compile_commands.json` 或 `.git`），就自動啟動 clangd 並 attach 上去。」它不是立刻啟動 clangd——是**登記一個 autocmd**，等你真的打開 `.c` 檔才觸發。

完整的 attach 鏈：

```
你 :edit foo.c
   │
   ├─ Neovim 判定 filetype=c
   │
   ├─ vim.lsp.enable 登記的規則觸發
   │     └─ 往上找 root（compile_commands.json / .clangd / .git）
   │     └─ 啟動 clangd 行程（若同 root 還沒有一個在跑）
   │
   ├─ Neovim client 送 initialize → clangd 回它的 capabilities
   │     （definitionProvider? referencesProvider? …）
   │
   ├─ 送 textDocument/didOpen（把 foo.c 內容給 clangd）
   │
   └─ 觸發 LspAttach autocmd
         └─ 你的 config 在這裡掛上 gd / gr / K 鍵位
```

最後那步就是 Ch 0 config 的這段——**它為什麼掛在 `LspAttach` 裡而不是全域**？因為 `gd`（跳定義）只有在有 LSP 的 buffer 才有意義。掛在 attach 時機，才能保證這 buffer 真的接上了 server：

```lua
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = { buffer = ev.buf }   -- 只綁這個 buffer
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, o)
    vim.keymap.set("n", "gr", vim.lsp.buf.references, o)
    vim.keymap.set("n", "K",  vim.lsp.buf.hover, o)
    vim.keymap.set("n", "<leader>ds", vim.lsp.buf.document_symbol, o)
  end,
})
```

`vim.lsp.buf.definition` 這些函式，就是「把游標位置包成 `textDocument/definition` JSON 丟給 clangd，收到結果就跳過去」的封裝。你按 `gd`，底下跑的就是這條 JSON 往返。

## headless 驗證：clangd 真的 attach 了嗎

這門課的信條是不信黑箱。我們用 headless 模式（不開 UI）驗證 clangd 真的接上了。準備一個小 C 專案（`main.c` / `geometry.c` / `geometry.h`），用 bear 生了 `compile_commands.json`（Ch 18 詳講），然後跑這段 lua：

```lua
-- check_attach.lua
vim.cmd("edit /tmp/demo/main.c")
vim.cmd("setfiletype c")
-- 等 clangd attach（headless 要主動 wait）
vim.wait(20000, function() return #vim.lsp.get_clients({bufnr=0}) > 0 end, 200)
local cs = vim.lsp.get_clients({bufnr=0})
print("clients attached: " .. #cs)
for _, c in ipairs(cs) do
  local caps = c.server_capabilities
  print("  name=" .. c.name)
  print("  definitionProvider=" .. tostring(caps.definitionProvider))
  print("  referencesProvider=" .. tostring(caps.referencesProvider))
  print("  callHierarchyProvider=" .. tostring(caps.callHierarchyProvider))
  print("  documentSymbolProvider=" .. tostring(caps.documentSymbolProvider))
end
```

真跑輸出（照抄）：

```
$ nvim --headless -u init.lua -l check_attach.lua
clients attached: 1
  name=clangd
  definitionProvider=true
  referencesProvider=true
  callHierarchyProvider=true
  documentSymbolProvider=true
```

三件事確認了：clangd 這個 client 成功 attach（`clients attached: 1`）、它宣告自己**能跳定義、能找引用、能建呼叫樹、能列符號**。這些 `*Provider=true` 就是握手時 clangd 回報的 capabilities——**Neovim 據此決定「哪些鍵要啟用」**。如果某個 server 沒宣告 `renameProvider`，Neovim 就不會給你 rename。功能是 server 宣告出來的，不是編輯器寫死的。

> `vim.lsp.get_clients()` 是你 debug LSP 的第一個工具。在真正的 Neovim 裡（非 headless），打開一個 `.c` 檔後跑 `:lua =vim.lsp.get_clients()` 或用內建的 `:checkhealth vim.lsp`，看 clangd 有沒有在清單裡。沒有 = attach 失敗，先修這個再說跳轉。

## 鍵位表

Part 4 這一路會累積一整套 LSP 鍵位。這章先確立 attach 後的核心四鍵（Ch 0 已有，這裡是它們的正式定義），後面幾章往上加：

| 模式 | 按鍵 | 作用 | 底層 LSP method |
|---|---|---|---|
| n | `gd` | 跳到定義 | `textDocument/definition` |
| n | `gr` | 找所有引用 | `textDocument/references` |
| n | `K` | hover 看型別/簽名 | `textDocument/hover` |
| n | `<leader>ds` | 當前檔符號大綱 | `textDocument/documentSymbol` |
| cmd | `:checkhealth vim.lsp` | 體檢 LSP 狀態 | — |
| cmd | `:lua =vim.lsp.get_clients()` | 列出 attach 的 client | — |

## 對比與取捨

| | 純語法（ctags/gtags/rg） | clangd（LSP） |
|---|---|---|
| 精準度 | 名字級（同名全中） | 符號級（唯一正確） |
| 需要能編譯 | 否 | **是（compile_commands.json）** |
| 啟動成本 | 極低 | 高（建 index、吃記憶體） |
| 大 kernel 這種樹 | 跑得動 | 可能建 index 很久或跳錯（Ch 21） |
| 何時用 | 掃讀、找字串、樹編不起來 | 需要「這符號到底是哪個」的精準 |

**取捨的本質是「精準 vs 成本」**。clangd 給最精準的答案，代價是要能編譯、啟動慢、大專案吃資源。實戰是分層：ripgrep 秒級定位大概位置（Part 2）→ 需要精準時才動用 clangd（Part 4）→ 樹編不起來時退回 gtags（Part 5）。不要企圖全程只靠 clangd，也不要在需要「這個 `free` 是哪個」時還硬用 grep。

## 踩雷集錦

1. **以為 `vim.lsp.enable("clangd")` 會立刻啟動 clangd**：它只是**登記規則**，等你開 `.c` 檔才觸發 attach。config 裡放了這行、開 nvim 卻沒看到 clangd，先確認你真的打開了一個 C 檔、且 filetype 被認成 `c`（`:set filetype?` 看）。

2. **把 `nvim-lspconfig` 當成 LSP client**：它不是。Neovim 的 client 是**內建**的 `vim.lsp`，lspconfig 只是一包各家 server 的預設設定。搞混這點會讓你在 debug 時找錯地方。0.11+ 之後很多設定甚至可以不靠 lspconfig，直接用 `vim.lsp.config`。

3. **attach 成功就以為能精準跳轉**：`definitionProvider=true` 只代表 clangd「能做」跳定義，不代表它「跳得準」。沒有 `compile_commands.json`，clangd 退化成單檔模式，跨檔跳轉會歪甚至跳錯（Ch 18 專講）。attach ≠ 精準，是兩回事。

4. **系統沒裝 clangd**：`vim.lsp.enable("clangd")` 登記了規則，但 PATH 裡找不到 `clangd` 二進位，attach 會靜默失敗。`:checkhealth vim.lsp` 會告訴你 clangd 找不到。先 `which clangd` 確認裝了。

5. **headless 忘了 `vim.wait`**：clangd attach 是非同步的，headless 腳本跑太快，你 `vim.lsp.get_clients()` 時它還沒接上，回 0 個 client 讓你以為壞了。要用 `vim.wait(...)` 主動等到 attach 完成（上面驗證腳本那樣）。

## 進階：再往深一層

- **一個 clangd 服務多個 buffer**：同一個專案（同 root）下開好幾個 `.c` 檔，Neovim **不會**為每個檔各啟一個 clangd，而是共用同一個 clangd 行程。`vim.lsp.get_clients()` 你會看到同一個 client 的 `attached_buffers` 有多個。這是為什麼開第二個檔比第一個快——clangd 已經在跑、index 已經在建。

- **`:LspInfo`（或 `:checkhealth vim.lsp`）看全貌**：告訴你哪些 client attach 了、root 目錄認到哪、有沒有 error。工具不動時第一個跑它。

- **`--log=verbose` 抓 LSP 通訊**：clangd 加這旗標會把每則收到/送出的 JSON 記下來。跳轉突然失效時，看 log 裡那則 `textDocument/definition` request 有沒有送出、clangd 回了什麼（空陣列 = 它找不到、error = 設定壞了）。這是把黑箱打開的終極手段。Neovim 端也可 `vim.lsp.set_log_level("debug")` + `:LspLog`。

- **capabilities 是雙向協商**：不只 server 宣告它能什麼，client 也宣告它支援什麼（例如「我支援 semanticTokens」「我支援 snippet」）。雙方取交集。這是為什麼有些功能你的 Neovim 版本太舊就用不到——client 沒宣告支援，server 就不會給。

## 本章重點整理

- **LSP 把「編輯器 × 語言」的 `M×N` 拆成 `M+N`**：一個 language server 服務所有會講 LSP 的編輯器。微軟 2016 年提出。
- **Neovim 內建 LSP client**（`vim.lsp`），只會講協定、接哪個 server 就懂哪個語言；`nvim-lspconfig` 只是各家 server 的設定包，不是 client。
- **clangd 是 C/C++ 的 language server**（LLVM 出品），基於真實編譯前端，所以最準——但需要 `compile_commands.json`（Ch 18）。
- 協定本體是 **JSON-RPC over stdio**；你按的每個跳轉鍵 = 一則 JSON 往返。
- `vim.lsp.enable("clangd")` **登記規則**，開 `.c` 檔才觸發 attach；attach 完成觸發 `LspAttach`，你在那裡掛鍵位。
- **attach ≠ 精準跳轉**：capabilities `true` 只代表「能做」，跳得準還要 compile_commands.json。

## 自我檢核

- [ ] 我能說出 LSP 解決了什麼 `M×N` 問題，以及為何 VS Code/Neovim 用 clangd 品質相同
- [ ] 我能分清「Neovim 內建 LSP client」與「nvim-lspconfig」的差別
- [ ] 我知道 clangd 為何最準（真編譯前端），以及它的前提是什麼
- [ ] 我能講出 `vim.lsp.enable("clangd")` → attach → `LspAttach` 掛鍵位的完整流程
- [ ] 我能用 `vim.lsp.get_clients()` / `:checkhealth vim.lsp` 驗證 clangd 有沒有 attach
- [ ] 我知道「attach 成功」和「跳得準」是兩件事，後者還缺什麼

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help lsp`**
  - **讀哪裡**：開頭 overview、`vim.lsp.enable`、`vim.lsp.config`、`LspAttach`。這章講的 attach 流程在這裡是權威定義。
  - **注意**：LSP API 在 0.10→0.11→0.12 演進很大（`vim.lsp.enable` 是較新的入口），以你這版 0.12.4 的 `:help` 為準。
- **Neovim `:help vim.lsp.buf`**
  - **讀哪裡**：`vim.lsp.buf.definition` / `references` / `hover` 等，就是你 `gd`/`gr`/`K` 底下呼叫的函式。看它們接受什麼參數。

### 規格與 server

- **[Language Server Protocol Specification 3.17](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)**
  - **讀哪裡**：Base Protocol（訊息格式）、`textDocument/definition`/`references`/`hover` 三節。想真懂那則 JSON 往返就讀這個。
- **[clangd 官方文件](https://clangd.llvm.org/)**
  - **讀哪裡**：Features 與 "How clangd works"。它的 AST + index 架構是後面幾章的底層。

### 橫向連結

- **`soft_skills/reading_code` Ch 13「LSP 與語意導航」**
  - 本課 Part 4 是它的深化與鏡像。那章用 Python 手動驅動 LSP 協定、把 JSON 往返攤開；讀完你會懂「按 `gd` 底下到底發生什麼」。強烈建議兩章對照讀。

clangd 接上了，但它現在多半還跳不準——因為它還不知道你的檔案「怎麼編」。下一章我們補上那塊拼圖：`compile_commands.json`，clangd 精準的前提，也是讀碼者最常卡的地方。

→ [Ch 18 compile_commands.json：clangd 精準的前提](./18-compile-commands.md)
