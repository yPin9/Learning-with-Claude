# Ch 21 — clangd 進階與 macro/ifdef 的坑

> **目標**：前面幾章 clangd 一路開掛，這章講它的**局限與調教**——誠實面對它何時不可靠。核心是 macro/ifdef 的坑：**clangd 只看到「一種編譯組態」的世界**，讀 kernel 這種多 config 樹，它常跳錯或跳不到，因為它解析的是某個 `.config` 下的宇宙。我們會用一個真實例子親眼看到「同一份 code、改一個 `-D`、`gd` 跳到完全不同的定義」。順帶講 `.clangd` 設定檔（CompileFlags/Index/Diagnostics）、背景索引、`--background-index`、header 處理。學完你知道 clangd 什麼時候該信、什麼時候該退回 gtags（Part 5 伏筆）。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14。本章「改 `-D` 讓 `gd` 跳到不同定義」的對照，是 headless 對同一份 C 檔配兩份不同 compile_commands.json 跑 clangd-14 各一次照抄的。

## 為什麼需要這個？clangd 的精準是「有前提的精準」

Ch 19 說 clangd 給「唯一正確」的答案。這句話有個隱藏前提：**在某個特定的編譯組態下唯一正確**。clangd 不是看你的「原始碼檔案」，它看的是「這份 code 在**這組 `-D`/`-I`/`-std`** 下編出來的樣子」。同一份 source，換一組編譯旗標，它看到的就是不同的程式——因為真實編譯出來的本來就是不同程式。

這在單一組態的專案（大多數應用程式）沒問題。但在**重度條件編譯**的專案——kernel（幾千個 `CONFIG_*`）、跨平台函式庫（`#ifdef _WIN32` / `#ifdef __linux__`）、可抽換後端（epoll/kqueue/select）——clangd 只能看到**一種**組態的世界。它跳轉時走的是那個世界的路，另一個 `#ifdef` 分支對它來說**根本不存在**。

這不是 clangd 的 bug，是「基於真編譯」的必然代價。純語法工具（gtags）反而在這裡有優勢：它把所有 `#ifdef` 分支**都當存在**，全部索引——不精準，但至少每個分支都找得到。這是 Part 5 tags 後備存在的核心理由之一，這章先把坑講清楚。

## 先建立直覺：clangd 眼中的世界由 `-D` 定義

```
   同一份 backend.c：
   #ifdef USE_EPOLL
       poll_backend() { return 1; }   ← A 分支
   #else
       poll_backend() { return 2; }   ← B 分支
   #endif

   compile_commands.json 說 -DUSE_EPOLL     compile_commands.json 沒有 -DUSE_EPOLL
        │                                          │
        ▼                                          ▼
   clangd 眼中只有 A 分支存在              clangd 眼中只有 B 分支存在
   gd poll_backend → 跳 A                  gd poll_backend → 跳 B
   B 分支？它看不到，像被註解掉             A 分支？它看不到
```

clangd 對「非啟用分支」的處理，跟你的編譯器一樣：那段 code **不被編譯**，所以對 clangd 而言它不在 AST 裡、不可跳轉、不參與 index。你的游標放在非啟用分支的 code 上，clangd 對它一無所知（甚至可能把它灰掉顯示 inactive region）。

## 真實例子：改一個 -D，gd 跳到不同定義

不是空談，實測給你看。一個 `backend.c`：

```c
#include <stdio.h>

#ifdef USE_EPOLL
static int poll_backend(void) { return 1; }   /* epoll 版，line 4 */
#else
static int poll_backend(void) { return 2; }   /* select 版，line 6 */
#endif

int run_loop(void) {
    return poll_backend();                       /* line 10：在這呼叫 */
}
```

在 `run_loop` 裡的 `poll_backend()` 呼叫上按 `gd`，應該跳到哪個 `poll_backend` 定義？**取決於 compile_commands.json 給的 `-D`。**

**compile_commands.json 有 `-DUSE_EPOLL`**（headless `textDocument/definition`，照抄）：

```
=== gd on poll_backend (compiled with -DUSE_EPOLL) ===
  -> backend.c:4  (static int poll_backend(void) { return 1; }   /* epoll 版 */)
```

**把 compile_commands.json 的 `-DUSE_EPOLL` 拿掉，同一個 `gd`**（照抄）：

```
=== gd on poll_backend (no -DUSE_EPOLL) ===
  -> backend.c:6  (static int poll_backend(void) { return 2; }   /* select 版 */)
```

**同一份 code、同一個游標位置、同一個 `gd`，只因為 `-D` 不同，跳到完全不同的定義**（line 4 vs line 6）。clangd 沒有錯——它忠實反映了「這組編譯旗標下，`poll_backend` 真的就是那一個」。但如果你**以為**當前 build 用 select、實際 CDB 記的是 epoll，你的 `gd` 就把你帶到你以為不存在的那個實作，讀錯 code 還不自知。

**這就是讀 kernel 的日常噩夢**。kernel 一個函式常有 `#ifdef CONFIG_SMP` / `#ifdef CONFIG_64BIT` 好幾個版本，clangd 只認你 `.config` 生成的 CDB 那個版本。你想讀的可能是另一個 config 的實作，clangd 帶你去的是它「編出來的」那個。跳錯了你還以為是唯一答案。

## clangd 對 generated code、conditional compilation 的局限

把 clangd 的局限講全：

1. **conditional compilation（條件編譯）**：上面那個。只看啟用分支，非啟用分支等於不存在。多 config 樹的頭號坑。

2. **generated code（生成的 code）**：很多專案的部分 code 是 build 時由腳本/工具生成的（protobuf 的 `.pb.c`、bison 生的 parser、kernel 的 `syscalls.h`）。如果生成步驟沒跑（你只 clone 沒 build），這些檔不存在，clangd 對「引用它們的地方」解不開——跳到生成符號會失敗。要先跑 build 讓生成檔就位，CDB 才完整。

3. **macro 生成的符號**：C 常用 macro 拼出函式名/符號（`#define DEFINE_HANDLER(x) void handle_##x(void)`）。clangd 在 AST 層看得到展開結果（比 grep 強），但**跳轉到 macro 拼出來的符號**有時不穩——因為那符號在文字上根本不存在，是 token 黏接出來的。kernel 大量用這招（`SYSCALL_DEFINE`、`EXPORT_SYMBOL`），是 clangd 在 kernel 常「跳不到」的另一原因。

4. **超大專案的 index 成本**：整個 kernel 建 clangd index 要很久、吃大量記憶體，有時實務上跑不動或不值得。這時 gtags（秒建、輕量）是唯一可行的全樹索引。

**一句話**：clangd 精準，但精準的前提是「單一、確定、能編譯、無 macro 黏接」的組態。越偏離這個前提（多 config、生成 code、macro 拼符號、超大樹），它越不可靠，越該退回 gtags（Part 5）。

## `.clangd` 設定檔：調教 clangd

專案根放一個 `.clangd`（YAML）可以調教 clangd 的行為。三個最常用的區塊：

### CompileFlags：加減旗標

CDB 給的旗標有時對 clangd 水土不服——專案用了 gcc 特有旗標 clang 不認得，冒出一堆假紅線；或你想強制加個 `-I`。`.clangd` 可以在 CDB 之上**增減**旗標：

```yaml
CompileFlags:
  Add: [-I/usr/include/mystuff, -DDEBUG]   # 追加
  Remove: [-mno-*, -fno-tree-*]             # 移除 clang 不認的 gcc 旗標
```

讀第三方專案 clangd 一片紅時，先看紅線是不是「clang 不認得某旗標」造成的，用 `Remove` 拿掉。這是止住假紅線的救命工具。

### Index：控制背景索引

```yaml
Index:
  Background: Build      # 背景建 index（預設就是）
  StandardLibrary: No    # 不索引標準庫（省資源）
```

### Diagnostics：抑制誤報

```yaml
Diagnostics:
  Suppress: [unused-includes, -Wunused-parameter]   # 抑制特定診斷
  UnusedIncludes: None
```

讀碼時 clangd 對第三方 code 冒一堆「你不在乎」的警告（未用參數、風格建議），`Suppress` 讓畫面乾淨。

> `.clangd` 是**專案級**設定（放專案根），還有**使用者級**（`~/.config/clangd/config.yaml`）套用到所有專案。讀別人的專案別亂改人家的 `.clangd`（會進 git），臨時調教用使用者級的。

## 背景索引：大專案首次索引要時間

clangd 的跨檔能力（`gr`、跳到別檔的定義、workspace symbol）靠**背景索引**（`--background-index`，預設開）。它在你開專案後，背景掃描所有編譯單元，建一個「符號 → 定義/引用位置」的索引，存到 `.cache/clangd/`。

**關鍵行為**：大專案首次索引要時間（幾十秒到幾分鐘，看規模）。這段期間：

- **當前檔內**的跳轉/hover 立刻可用（靠 AST，不等 index）。
- **跨檔**的 `gr`、跳到別檔定義、workspace symbol **會不完整**——index 還沒建到那裡。

這解釋了 Ch 19/20 反覆提的「剛開大專案時 `gr` 漏引用」：不是壞了，是 index 還在建。clangd 通常在狀態列有進度提示（`indexing: 340/512`），等它跑完再用跨檔功能。index 建好後存快取，**下次開同專案快很多**（不用重建）。

AST（管當前檔精準）+ index（管跨檔位置）的分工，`reading_code` Ch 13 講得很細，這裡只提醒它的**時間特性**：AST 即時、index 要等。

## header 處理：clangd 怎麼看 .h

C 的 header 沒有自己的編譯命令（`.h` 不是編譯單元，是被 `#include` 進 `.c` 的）。clangd 怎麼分析一個你單獨打開的 `.h`？它靠 **"header 被誰 include" 的推斷**：從 index 找一個 include 了這個 header 的 `.c`，借用那個 `.c` 的編譯旗標來分析這個 header。

後果：

- 一個 header 若**從沒被任何 CDB 裡的 `.c` include**，clangd 不知道用什麼旗標分析它，退回猜——可能不準。
- 一個 header 被多個 `.c` 用不同旗標 include（有的 `-DFOO` 有的沒有），clangd 挑一個，可能不是你想看的那個組態。這又是 conditional compilation 的坑在 header 層的體現。

讀碼實務：在**被 include 它的 `.c` 檔裡**跳進 header（`gd`）比直接打開 header 裸讀更準——因為前者 clangd 有明確的編譯上下文。

## 何時退回 gtags（Part 5 伏筆）

把「clangd 該退場」的訊號列清楚，這是 Part 5 的引子：

| 症狀 | 原因 | 對策 |
|---|---|---|
| `gd` 跳到「你以為不存在」的 `#ifdef` 分支 | clangd 只看 CDB 那個 config | 換 config 重建 CDB，或退 gtags（它看所有分支） |
| 想讀的分支 clangd 跳不到 | 那分支非啟用、不在 AST | gtags（全分支索引） |
| kernel 整樹想全域搜符號但 index 跑不動 | 太大、太久、吃記憶體 | gtags（秒建、輕量） |
| macro 拼出來的符號跳不到 | token 黏接、文字上不存在 | gtags/grep 對 `##` 前綴 |
| 樹根本 build 不起來（缺 toolchain） | 沒 CDB，clangd 單檔模式 | gtags（不需能編譯） |

**分層策略**：clangd 是精準層（單一組態、能編譯時最強），gtags 是廣度層（全分支、不需編譯、超大樹）。讀 kernel 這種樹，實務上兩個都開——clangd 給精準（當你確定在對的 config），gtags 給「至少每個分支都找得到」的兜底。Part 5 Ch 23-25 把 gtags/cscope 這層講透。

## 鍵位表

這章偏概念與設定，鍵位沿用前幾章。新增的是「診斷/index 狀態」的檢查動作：

| 場景 | 命令/操作 | 作用 |
|---|---|---|
| 看 clangd 用什麼旗標編某檔 | `clangd --check=<file>`，看 "Compile command" 那行 | 確認 `-D`/`-I` 是你以為的那組 |
| 疑心跳錯 config | 對 `#ifdef` 附近符號 `gd`，看跳去啟用還是非啟用分支 | 判斷 clangd 在哪個組態 |
| clangd 一片紅 | 檢查 `.clangd` 的 `CompileFlags: Remove` | 拿掉 clang 不認的 gcc 旗標 |
| 看 index 建完沒 | `:LspInfo` / 狀態列 indexing 進度 | 決定跨檔功能能不能信 |

## 對比與取捨

| | clangd | gtags（Part 5） |
|---|---|---|
| 精準度 | 符號級（單一組態） | 名字級（全中） |
| conditional compilation | 只看啟用分支 | **全分支都索引** |
| 需要能編譯 | 是 | **否** |
| 超大樹（kernel）index | 慢/吃記憶體/可能跑不動 | **秒建、輕量** |
| macro 拼接符號 | 不穩 | 純文字，`##` 前綴找得到 |
| 何時勝出 | 確定在對的 config、要精準 | 多 config、編不起來、超大樹兜底 |

## 踩雷集錦

1. **在 kernel 上盲信 clangd 的 `gd`**：它只認你 `.config` 那個組態。你想讀的實作可能在另一個 `#ifdef` 分支，clangd 帶你去它編出來的那個，跳錯還以為唯一答案。讀 kernel 要意識到「我現在在哪個 config」，並隨時準備退 gtags。

2. **以為非啟用分支的 code 能跳轉**：`#else` 分支對 clangd 等於被註解掉。游標放上去 `gd`/hover 沒反應是正常的——它不在 AST 裡。想看非啟用分支，改 CDB 的 `-D` 讓它啟用，或用純文字工具。

3. **clangd 一片紅就以為 code 有錯**：多半是「clang 不認得某個 gcc 特有旗標/extension」的假紅線，或缺生成檔。真正的編譯錯誤以你的 gcc build 為準。用 `.clangd` 的 `CompileFlags: Remove` / `Diagnostics: Suppress` 止血。

4. **改了 `.config` / build 旗標，clangd 還跳舊分支**：CDB 是快照（Ch 18 踩雷 2）。改 config 後要**重建 CDB**（重跑 bear/kernel 的 gen 腳本），clangd 才會看到新組態。舊 CDB + 新想法 = 跳去舊組態。

5. **裸讀 header 不準**：單獨打開 `.h` 裸讀，clangd 缺編譯上下文（header 沒自己的 compile command）。從 include 它的 `.c` 裡 `gd` 進去更準。

6. **index 沒建完就下結論「clangd 找不到」**：大專案剛開，跨檔查詢不全是因為 index 在建。看狀態列進度，別在 index 半成品時判 clangd 死刑。

## 進階：再往深一層

- **`clangd --check` 洩漏 compile command**：`clangd --check=<file> 2>&1 | grep "Compile command"` 會印出 clangd 對這檔實際用的完整旗標。想確認「clangd 眼中這檔是哪個組態」，這是最直接的證據——看那行有沒有 `-DUSE_EPOLL`、`-DCONFIG_SMP`。

- **`clangd-indexer` 離線預建 index**：對超大專案，可以用 `clangd-indexer` 離線把整個專案的 index 先建好（`.idx` 檔），clangd 啟動時直接載入，省掉開檔才建的延遲。這是讓 clangd 在 kernel 這種規模「勉強可用」的關鍵手段之一，但仍受 conditional compilation 局限（只索引啟用分支）。

- **inactive region 的視覺提示**：clangd 會把非啟用的 `#ifdef` 分支標成 inactive（Neovim 可配成灰掉顯示）。這個視覺提示很有用——你一眼看出「這段 code 在當前 config 下沒被編」，就知道別在上面期待 `gd`。config 裡開 `vim.lsp` 的 semantic tokens / 用支援的 colorscheme 可顯示。

- **多組態同時讀的土法**：想同時看 epoll 和 select 兩個分支的實作，一個實務招是開兩個 CDB（或 `.clangd` 切換）分別讀。但更常見的是：clangd 讀你當前 config 的分支，另一個分支用 gtags/grep 直接看文字。這正是「精準層 + 廣度層」並用的日常。

## 本章重點整理

- clangd 的精準是「**單一組態下**的精準」——它只看到 CDB 給的那組 `-D`/`-I` 編出來的世界。
- **conditional compilation 是頭號坑**：實測同一份 code、改一個 `-D`，`gd` 跳到完全不同定義（line 4 epoll 版 vs line 6 select 版）。非啟用分支對 clangd 等於不存在。
- 其他局限：generated code（沒 build 就沒有）、macro 拼接符號（跳不穩）、超大樹 index 跑不動。
- **`.clangd`** 調教：`CompileFlags`（加減旗標，止假紅線）、`Index`、`Diagnostics: Suppress`（抑誤報）。
- **背景索引**要時間：當前檔即時（AST），跨檔要等 index 建完；建好存快取，下次快。
- **何時退 gtags**：多 config、想讀非啟用分支、超大樹 index 跑不動、macro 拼接、編不起來——這些 clangd 弱的地方正是 Part 5 tags 後備的用武之地。

## 自我檢核

- [ ] 我能解釋為什麼 clangd 只看到「一種編譯組態」，以及這在 kernel 為何是坑
- [ ] 我能說出「改一個 `-D` 讓 `gd` 跳到不同定義」的機制，並知道跳錯 config 不會報錯
- [ ] 我知道非啟用 `#ifdef` 分支對 clangd 等於不存在，不能跳轉
- [ ] 我會用 `.clangd` 的 CompileFlags/Diagnostics 止假紅線與抑誤報
- [ ] 我理解背景索引的時間特性（當前檔即時、跨檔要等）
- [ ] 我能列出至少三種「該退回 gtags」的訊號

## 延伸閱讀

### 官方文件（優先）

- **[clangd — Configuration（.clangd）](https://clangd.llvm.org/config)**
  - **讀哪裡**：`CompileFlags`、`Index`、`Diagnostics` 三區塊的完整選項。本章調教設定的權威依據。
- **[clangd — How clangd works（Indexing）](https://clangd.llvm.org/design/indexing)**
  - **讀哪裡**：背景索引、dynamic vs static index、clangd-indexer。理解 index 的時間與資源特性。

### 針對 kernel 的實務

- **[Linux kernel — clangd 使用文件](https://docs.kernel.org/dev-tools/clangd.html)**
  - **讀哪裡**：kernel 官方教你怎麼對 kernel 用 clangd（`gen_compile_commands.py`、`.clangd` 設定）。親眼看多 config 樹的實務配置與局限。
  - **前提**：懂 kernel 的 `.config` 與 build。

### 橫向連結

- **本課 Part 5 Ch 23**「為什麼需要 tags 後備」
  - 這章列的「clangd 該退場」訊號，Ch 23 正面接手——為什麼 gtags 在多 config/編不起來/超大樹的場景不可取代。兩章是同一個問題的兩面。
- **`soft_skills/reading_code` Ch 13** 的踩雷集錦
  - 那章的「compile_commands.json 是快照」「診斷是提示不是判決」與本章互補。

clangd 的能與不能都攤開了。Part 4 最後一章收尾兩個讀碼時常被忽略但很有用的語意功能：診斷（clangd 幫你標可疑處）與 inlay hints（顯示推斷的型別/參數名，看清隱含型別）。

→ [Ch 22 診斷與 inlay hints](./22-diagnostics-inlay-hints.md)
