# Ch 22 — 診斷與 inlay hints

> **目標**：Part 4 收尾，兩個讀碼時常被忽略、其實很有用的語意功能。**診斷（diagnostics）**：clangd 報的 error/warning——讀碼時它幫你標出可疑處（型別不符、可能空指標、未用變數），是免費的第一層「這裡怪怪的」提示。**inlay hints**：把 clangd 推斷的型別/參數名，以虛擬文字直接顯示在 code 裡——看清隱含型別（`auto`、macro 展開、匿名回傳）與「這個引數對應哪個參數」。學完你會用 `[d`/`]d` 跳診斷、`<leader>e` 看浮窗、把診斷送 quickfix，以及開關 inlay hints。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14。診斷輸出是 headless 對真 C 檔跑 clangd-14 照抄的。inlay hints 這節有一個**關於 clangd 14 的誠實限制**，見下文——API 已驗證存在，但 clangd 14 本身幾乎不吐 hint。

## 為什麼需要這個？讀碼不只是跳轉

前面五章都在講「怎麼移動」（跳定義、找引用、建地圖）。但讀碼還有兩件事，語意工具能免費幫你：

1. **標出可疑處**。你讀一段陌生 code，clangd 在旁邊標了紅線——「這裡型別不符」「這個變數宣告了沒用」「這個指標可能是 null」。這些**幫你看出作者的意圖或潛在 bug**，是找漏洞式讀碼（`reading_code` 的 sink 分析）的免費第一層提示。你不用主動查，clangd 打開檔就標好了。

2. **顯示隱含資訊**。C code 裡有很多「看不見的型別」——`auto`（C++）、macro 展開後的真實型別、一個函式呼叫 `foo(x, y, z)` 你看不出 `x`/`y`/`z` 對應哪個參數。inlay hints 把這些**推斷出來的資訊**用虛擬文字直接貼在 code 旁，讓隱含變顯性。

這兩個都是「clangd 主動給、你被動收」的資訊，成本低、讀碼收益高，卻常被忽略。這章把它們接進 config。

## 診斷：clangd 主動推送的 error/warning

### 底層：診斷是 server 主動推的

Ch 17 講的 LSP 方法大多是「你問、server 答」（request/response）。診斷不同——它是**少數 server 主動推給你**的訊息（`textDocument/publishDiagnostics`）。你一打開檔（或打字改動），clangd 分析完就主動把「這檔有哪些問題」推回 Neovim，Neovim 把它們畫成紅線/黃線、堆進一個列表。

這條「你改字 → clangd 重分析 → 主動推診斷 → 你看到紅線」的即時循環，就是「打錯馬上出紅線」的底層（`reading_code` Ch 13 講得更細）。讀碼時你不編輯，但一開檔 clangd 就把整檔的診斷推給你了。

### 實測：clangd 抓出的錯

一個故意寫錯的 `buggy.c`——`distance()` 要兩個參數卻只給一個。打開它，clangd 推的診斷（headless `vim.diagnostic.get(0)`，照抄）：

```
=== diagnostics in buggy.c: count=1 ===
  [ERROR] line 5: Too few arguments to function call, expected 2, have 1
```

clangd 精準指出第 5 行參數個數不對。**讀碼情境**：你讀一段不熟的 code，看到 clangd 標了紅——這是「這裡可能有問題」的線索，可能是真 bug（你在找漏洞），也可能是你還沒讀懂的地方（作者故意這樣寫，你去查為什麼）。診斷是**提示不是判決**（見踩雷），但它幫你把注意力導向「值得多看兩眼的地方」。

### 診斷導航：跳、看、送清單

Neovim 的診斷 API 是 `vim.diagnostic`（跟 LSP 解耦——診斷不一定來自 LSP，也可來自 linter，但 clangd 是主要來源）。三個核心動作：

- **跳診斷**：`]d` 跳下一個、`[d` 跳上一個。在一堆診斷間快速走訪。
- **看細節**：`<leader>e` 在游標處彈浮窗，顯示完整診斷訊息（紅線行內常被截斷，浮窗看全文）。
- **送 quickfix**：`vim.diagnostic.setqflist()` 把所有診斷堆進 quickfix list（Part 2 Ch 12），變成一張「這檔/這專案所有可疑處」的工作清單，逐一走訪。

> Neovim 0.11+ 內建了預設診斷跳轉鍵（`]d`/`[d` 走 `vim.diagnostic.jump`）。舊版用 `goto_next`/`goto_prev`。以你這版 `:help vim.diagnostic` 為準；下面 config 兩種都相容地寫。

### virtual text：紅線要不要顯示訊息

診斷的顯示方式可調。預設 clangd 的診斷會在行末顯示 **virtual text**（虛擬文字，如行末灰字 `Too few arguments...`）。有人覺得這干擾閱讀（訊息太長把 code 擠到看不到），可以關掉 virtual text、只留行號旁的符號（sign），需要時 `<leader>e` 浮窗看。讀碼取向的設定通常會把 virtual text 收斂一點，保持畫面乾淨。

## inlay hints：顯示推斷的型別/參數名

### 這是什麼

inlay hints 是 Neovim 0.10+ 內建的功能（`vim.lsp.inlay_hint`）：把 clangd 推斷的資訊，用**虛擬文字**（不佔實際字元、不改檔案）插進 code 顯示。兩類最有用：

- **type hints**：顯示推斷的型別。C++ 的 `auto x = foo();` 旁顯示 `: SomeType`，讓你不用跳定義就知道 `x` 是什麼。
- **parameter name hints**：函式呼叫 `memcpy(dst, src, n)` 的每個引數前顯示對應參數名 `to:`/`from:`/`size:`，讓你一眼看出「這個引數是幹嘛的」。

**讀碼情境**：讀一個 `set_flags(obj, 1, 0, 1)` 這種一堆布林/數字引數的呼叫，你根本看不出每個值什麼意思。parameter name hints 顯示 `set_flags(obj, enable: 1, verbose: 0, force: 1)`，語意立刻清楚——不用跳去看函式宣告。這對讀「引數是 magic number」的 C code 特別救命。type hints 則對 C++ 的 `auto`、模板推導型別是基本生存需求。

### API 驗證：功能在，但 clangd 14 幾乎不吐

先驗 Neovim 這邊的 API 存在（headless，照抄）：

```
=== inlay hint API ===
vim.lsp.inlay_hint type: table
vim.lsp.inlay_hint.enable type: function      ← 開關函式在
vim.diagnostic type: table
vim.diagnostic.open_float: function
vim.diagnostic.setqflist: function
```

`vim.lsp.inlay_hint.enable` 這個開關函式**確實存在**（0.10+ 內建），diagnostic API 也齊全。clangd 也宣告了 inlay hint 能力（`clangdInlayHintsProvider` 這個擴充 capability）。

**但這裡有個對 clangd 14 的誠實限制**：我實測對 C 檔和含 `auto` 的 C++ 檔請求 inlay hints，clangd 14 回傳**空的**——即使加了 `.clangd` 的 `InlayHints: {Enabled: Yes}`：

```
=== inlay hints in main.c ===
  (none)
=== inlay hints in hints.cpp (含 auto/參數名) ===
  (none)
$ clangd --help | grep -i inlay
（clangd 14 沒有 inlay-hint 相關旗標）
```

原因：**inlay hints 在 clangd 是 clangd 14 才引入的實驗性擴充，這個 14.0.0 build 支援得非常不完整**（宣告了 capability 卻幾乎不產生 hint）。inlay hints 要真正好用，實務上要 **clangd 15/16+**。所以：**Neovim 端的 API 我驗證了是好的（`vim.lsp.inlay_hint.enable` 可呼叫、`enable(true)` 不報錯），config 也是對的，但你在 clangd 14 上開了大概看不到 hint——換 clangd 16+ 就會出現。** 這是「工具版本決定功能可用性」的又一個實例，跟 Ch 0 的 nvim 版本、Ch 13 的 treesitter 分支同理。不藏這個坑，免得你照著開卻看不到還以為 config 錯了。

### config：開關 inlay hints

inlay hints 常駐會讓畫面變密（每個型別/參數都貼字），所以通常做成**可切換**。往 `LspAttach` 加：

```lua
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = function(desc) return { buffer = ev.buf, desc = desc } end
    -- ...（前幾章的 gd/gr/K 等）

    -- inlay hints 切換（需 clangd 15+ 才看得到，14 幾乎空）
    vim.keymap.set("n", "<leader>ih", function()
      local on = vim.lsp.inlay_hint.is_enabled({ bufnr = ev.buf })
      vim.lsp.inlay_hint.enable(not on, { bufnr = ev.buf })
    end, o("切換 inlay hints"))
  end,
})
```

要讓 clangd（15+）真的吐 hint，還要 `.clangd` 開：

```yaml
InlayHints:
  Enabled: Yes
  ParameterNames: Yes
  DeductionTypes: Yes
```

## config：診斷設定

診斷的顯示與鍵位，放在 config 的全域段（不用等 attach，因為 `vim.diagnostic` 是全域的）。加：

```lua
-- 診斷顯示：讀碼取向，收斂 virtual text 保持畫面乾淨
vim.diagnostic.config({
  virtual_text = { current_line = true },  -- 只在游標當前行顯示行內訊息（0.11+）
  signs = true,                            -- 行號旁留符號提示
  underline = true,
  severity_sort = true,                    -- error 排在 warning 前
})

-- 診斷鍵位（全域）
vim.keymap.set("n", "]d", function() vim.diagnostic.jump({ count = 1 }) end,  { desc = "下一個診斷" })
vim.keymap.set("n", "[d", function() vim.diagnostic.jump({ count = -1 }) end, { desc = "上一個診斷" })
vim.keymap.set("n", "<leader>e", vim.diagnostic.open_float,   { desc = "看診斷浮窗" })
vim.keymap.set("n", "<leader>q", vim.diagnostic.setqflist,    { desc = "診斷送 quickfix" })
```

> `vim.diagnostic.jump` 是 0.11+ 的統一入口；若你的版本沒有，用 `vim.diagnostic.goto_next`/`goto_prev`（0.12.4 兩者相容，`goto_*` 標記為 deprecated 但還能用）。`virtual_text = { current_line = true }` 讓行內訊息只在游標那行出現，其他行只留 sign，是讀碼防干擾的好設定。

## 鍵位表

| 模式 | 按鍵 | 作用 | 讀碼情境 |
|---|---|---|---|
| n | `]d` | 下一個診斷 | 走訪可疑處 |
| n | `[d` | 上一個診斷 | 走訪可疑處 |
| n | `<leader>e` | 診斷浮窗（看全文） | 行內被截斷時看完整訊息 |
| n | `<leader>q` | 所有診斷送 quickfix | 建「全檔可疑處」清單 |
| n | `<leader>ih` | 切換 inlay hints | 看隱含型別/參數名（需 clangd 15+） |

## 對比與取捨

| | 診斷 | inlay hints |
|---|---|---|
| 資訊來源 | clangd 主動推 | clangd 應答（type/param 推斷） |
| 讀碼用途 | 標可疑處（bug/意圖線索） | 看隱含型別、參數對應 |
| 顯示 | 紅線/sign/virtual text | 虛擬文字插在 code 中 |
| 干擾風險 | virtual text 太長擠版面 | 常駐會讓畫面變密 → 做成切換 |
| clangd 14 可用性 | **完全可用** | **幾乎不吐（要 15+）** |
| 對 C 的價值 | 高（型別/參數/空指標） | 中（C 沒 auto，主要是參數名） |
| 對 C++ 的價值 | 高 | **高**（auto/模板推導必備） |

## 踩雷集錦

1. **把診斷紅線當成「code 一定有錯」**：clangd 用它自己的 clang + CDB 給的旗標分析，**可能跟你實際的 gcc build 有出入**。專案用 gcc 特有 extension、或旗標裡有 clang 不認的東西時，會冒假紅線（誤報）。診斷是**提示不是判決**，真正的錯以你的 build 為準。假紅線多用 `.clangd` 的 `Diagnostics: Suppress` 收（Ch 21）。

2. **開了 inlay hints 卻什麼都沒出現**：如果你在 clangd 14，這是**預期的**——14 的 inlay hint 支援極不完整（本章實測空的）。換 clangd 15/16+ 才會出現。別懷疑你的 config，先 `clangd --version` 看版本。

3. **inlay hints 常駐把畫面搞得很密**：每個型別/參數都貼虛擬文字，密集的 code 會很擠。做成 `<leader>ih` 切換，需要看隱含型別時才開、看完就關。別預設常駐。

4. **`vim.diagnostic.jump` 在舊版不存在**：0.11 才有的統一入口。0.10 用 `goto_next`/`goto_prev`。跨版本 config 要判斷或用相容寫法。

5. **virtual text 干擾閱讀**：預設所有診斷都在行末貼灰字，長訊息會把 code 擠到螢幕外。用 `virtual_text = { current_line = true }`（只當前行）或直接 `virtual_text = false`（只留 sign，靠 `<leader>e` 浮窗看）。讀碼取向建議收斂它。

6. **診斷來自 clangd 就以為一定有 clangd**：`vim.diagnostic` 是 Neovim 全域框架，也能顯示非 LSP 來源（linter）的診斷。C 檔的診斷主要來自 clangd，但別假設「有紅線 = clangd attach 了」——確認 attach 還是看 `:checkhealth vim.lsp`。

## 進階：再往深一層

- **診斷當「理解檢查器」**：讀陌生 code 時，clangd 標的「未用變數」「這個 branch 永遠不會執行」「型別隱式轉換」常常洩漏作者的意圖或 bug。找漏洞式讀碼（`reading_code` 的 sink 分析）把 clangd 診斷當免費的第一層提示——它幫你把幾千行縮小到「這幾處值得深看」。

- **診斷的 severity 分層**：診斷有 ERROR/WARN/INFO/HINT 四級。`vim.diagnostic.config` 可以分級處理——只讓 ERROR 顯示 virtual text，WARN 以下只留 sign，避免雜訊。讀碼時通常只在意 ERROR 和你關心的特定 WARN。

- **code action 洩漏語意**：LSP 還有 `textDocument/codeAction`（快速修復建議，綁 `vim.lsp.buf.code_action`）。讀碼時它的價值不在「套用修復」，而在**看 clangd 想幫你改什麼**——它提議「加上缺的 include」「展開這個 macro」，這些提議本身透露 clangd 對這段 code 的語意理解。即使不套用，看它的建議也是讀懂 code 的線索。

- **inlay hints 的其他種類**：除了 type/parameter，clangd（新版）還能顯示 `designator` hints（struct 初始化時 `{.x = 1}` 的欄位名）、default argument hints 等。讀密集初始化的 struct 時很有用。`.clangd` 的 `InlayHints` 可分別開關。

- **inlay hints vs hover 的取捨**：hover（`K`）是「主動問一個」，inlay hints 是「被動全顯示」。精讀某一行用 hover 針對性看；掃讀整段想快速抓所有型別/參數用 inlay hints 全開。兩者互補，不是替代。

## 本章重點整理

- **診斷**是 clangd **主動推**（`publishDiagnostics`）的 error/warning，讀碼時幫你標可疑處（bug/意圖線索），是找漏洞的免費第一層提示。
- 診斷導航：`]d`/`[d` 跳、`<leader>e` 浮窗看全文、`<leader>q` 送 quickfix 建工作清單。
- 診斷是**提示不是判決**——clangd 用自己的 clang + CDB 旗標，可能跟你 gcc build 有出入，會有假紅線。
- **inlay hints**（0.10+ `vim.lsp.inlay_hint.enable`）把推斷的型別/參數名以虛擬文字顯示——parameter name hints 對讀「magic number 引數」的 C 特別救命，type hints 對 C++ 的 `auto` 是必備。
- **誠實限制**：Neovim 的 inlay hint API 驗證存在可用，但 **clangd 14 幾乎不吐 hint（要 15/16+）**——API 對、config 對，看不到是 clangd 版本問題。
- inlay hints 做成 `<leader>ih` 切換（常駐會讓畫面太密），診斷 virtual text 收斂成當前行以防干擾。

## 自我檢核

- [ ] 我知道診斷是 clangd 主動推的，且它是「提示不是判決」（會有假紅線）
- [ ] 我會用 `]d`/`[d` 跳診斷、`<leader>e` 看浮窗、`<leader>q` 送 quickfix
- [ ] 我知道 inlay hints 顯示推斷的型別/參數名，以及 parameter name hints 對讀 C 的價值
- [ ] 我理解「clangd 14 幾乎不吐 inlay hint、要 15+」這個版本限制，不會誤判成 config 錯
- [ ] 我會把 inlay hints 做成切換、把診斷 virtual text 收斂以防干擾閱讀
- [ ] 我能說出診斷/inlay hints 如何當「理解 code 的線索」而不只是編輯輔助

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help vim.diagnostic`**
  - **讀哪裡**：`vim.diagnostic.config`（virtual_text/signs/severity_sort）、`jump`/`open_float`/`setqflist`。本章診斷 config 與鍵位的權威依據。
- **Neovim `:help vim.lsp.inlay_hint`**
  - **讀哪裡**：`enable`/`is_enabled` 的用法與版本需求。確認你這版的 API 形狀。

### clangd 端

- **[clangd — Inlay hints](https://clangd.llvm.org/config#inlayhints)**
  - **讀哪裡**：`InlayHints` 的設定選項（ParameterNames/DeductionTypes/Designators）與**版本需求**。本章那個「14 幾乎不吐」的限制，這裡有版本說明。
- **[clangd — Diagnostics](https://clangd.llvm.org/guides/include-cleaner)** 與 config 的 Diagnostics 段
  - **讀哪裡**：clangd 診斷的來源、如何 suppress。呼應 Ch 21 的假紅線處理。

### 橫向連結

- **`soft_skills/reading_code` Ch 13** 的「診斷與 code action」段
  - 那章把診斷當「理解檢查器」、code action 當「語意線索」的觀點，是本章讀碼用途的方法論來源。

Part 4 到此完整：LSP 接上（Ch 17）→ 餵飽 CDB（Ch 18）→ 語意導航五探針（Ch 19）→ symbol 地圖（Ch 20）→ 認清 clangd 局限（Ch 21）→ 診斷與 hints 收尾（Ch 22）。你現在有一整套「懂語意」的操作。練習 D 把它們串起來——用 clangd 在一個真專案裡追一條完整的 call chain。

→ [練習 D：clangd 追一條 call chain](./practice-d-trace-a-call-chain.md)
