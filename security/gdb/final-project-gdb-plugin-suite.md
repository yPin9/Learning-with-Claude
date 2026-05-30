# Final Project — 打造你自己的 GDB 插件套件（gef / pwndbg 風格）

> **目標**：整合整門課，用 GDB Python API 寫出一套你自己的 gef 風格插件——停下來自動顯示彩色 context（registers / code / stack / backtrace）、telescope、heap 分析、自訂指令、pretty-printer、設定系統。完成後你不只是 GDB 重度使用者，而是「能造工具的人」。這是「會用 → 能改」的最終證明。

## 為什麼是這個專案

Ch 0 你裝了 gef/pwndbg，我說「記住這個畫面，課程結束你會自己做出來」。現在兌現。

這個專案是整門課的縮影：

- 它是**真正有用的工具**（你做完天天會用，取代 gef）
- 它整合了 Part 5 幾乎每個 API（Command/Parameter/event/Value/Breakpoint/pretty-printer/colored output/TUI）
- 它證明你理解底層（Part 8）——你知道 telescope 在解什麼、heap chunk 是什麼、backtrace 怎麼來的
- 它是可發布、可分享、可持續擴充的作品

做完這個，你對任何人說「我會 GDB」都名副其實——因為你能造 GDB 的工具。

## 整合的知識點（至少 70% 的課程）

| 功能 | 用到的章節 |
|---|---|
| 主指令 + 子命令 | Ch 24（Command/prefix）|
| 設定系統（顏色/行數開關）| Ch 24（Parameter）|
| 停下來自動顯示 context | Ch 25（events.stop）|
| registers 顯示 + 旗標解碼 | Ch 11, 23（read_register）|
| code 反組譯顯示 | Ch 11（disassemble）|
| stack telescope（解指標、上色）| Ch 7, 11, 23, 28 |
| 記憶體區段分類（heap/stack/code）| Ch 11, 40（mappings）|
| backtrace（含 frame filter 美化）| Ch 10, 27 |
| heap chunk 分析 | Ch 23, 39, 練習 E |
| malloc/free 追蹤 | Ch 25（FinishBreakpoint）|
| 自訂 pretty-printer | Ch 26, 30 |
| 彩色輸出 | Ch 28 |
| 安裝/發布 | Ch 19（.gdbinit/auto-load）|
| 底層理解（你知道每個值是什麼）| Part 8 全部 |

## 任務規格

寫一個 Python 套件 `mygef.py`，提供以下功能。**這是規格，不是參考解答——自己設計實作。**

### 必做（核心）

1. **`ctx` 自動 context**：用 `events.stop`（Ch 25），每次程式停下來自動顯示一個分區的彩色面板：
   - **registers**：主要暫存器（值上色：指標一色、stack 一色、code 一色），加 EFLAGS 旗標解碼（ZF/SF/CF…）。
   - **code**：當前 `$pc` 起 5 條反組譯，標示當前指令。
   - **stack**：telescope 當前 `$sp` 起 8 個 slot，解讀並上色（指標跟隨、符號解析）。
   - **backtrace**：精簡的呼叫鏈（過濾 libc 雜訊）。
   - 可用 `ctxon`/`ctxoff` 開關，避免不想要時干擾。

2. **`tel <addr> [n]`**：獨立的 telescope 指令（context 的 stack 區塊複用它）——讀記憶體、遞迴跟隨指標（最多 N 層）、解析符號、依記憶體區段上色。

3. **`mygef` prefix command + 設定系統**：
   - `mygef config` 顯示/修改設定
   - 用 `gdb.Parameter`（Ch 24）做：`mygef-color`（開關顏色）、`mygef-ctx-lines`（context 各區行數）、`mygef-context-sections`（顯示哪些區塊）。

### 選做（進階，挑至少 2 個）

4. **`heap` 子命令**：解析 glibc heap（複用練習 E）——`mygef heap chunks`（列 chunk）、`mygef heap bins`（tcache/fastbins，進階）。

5. **`trace` malloc/free**：用 FinishBreakpoint（Ch 25）追蹤，偵測 double-free / UAF。

6. **`vmmap`**：美化的記憶體映射（彩色，標可寫可執行段）——pwn 必備。

7. **frame filter**：用 Ch 27 的 frame filter 讓 `bt` 自動隱藏 libc/STL 雜訊、標記使用者 frame。

8. **pretty-printer**：為某個你常用的資料結構（或目標程式的核心結構）寫 printer（Ch 26）。

9. **搜尋指令**：`search <pattern>`（在記憶體找字串/位元組/值，Ch 11 的 find 包裝）。

### 驗收標準

- [ ] `ctxon` 後每次停下自動顯示完整彩色 context（registers/code/stack/backtrace）
- [ ] registers/stack 的值依「是指標/stack/heap/code」正確上色
- [ ] `tel` 能遞迴跟隨指標並解析符號
- [ ] 用 prefix command 組織，有 `config` 與至少 3 個 `Parameter` 設定
- [ ] 顏色可透過設定關閉（pipe/logging 友善）
- [ ] 完成至少 2 個進階功能
- [ ] 模組化、可 source、可透過 `.gdbinit` 安裝
- [ ] 健壯：讀壞記憶體不崩潰（try/except）
- [ ] 寫一份 README：功能、安裝、用法

## 期望成果

```
(gdb) source mygef.py
[mygef] loaded. Commands: ctxon/ctxoff, tel, mygef config, ...
(gdb) ctxon
(gdb) break main
(gdb) run

────────────────────────[ registers ]────────────────────────
 rax  0x0                rbx  0x7fffffffe3d8 → (stack)
 rdi  0x1                rsi  0x555555558040 → (heap) "hello"
 rip  0x555555555149 → main+4 (code)
 eflags [ ZF PF IF ]
────────────────────────[ code ]──────────────────────────────
 → 0x555555555149 <main+4>:  mov  DWORD PTR [rbp-0x4], 0x0
   0x555555555150 <main+11>: ...
────────────────────────[ stack ]─────────────────────────────
 0x7fffffffe2a0|+0000: 0x00007ffff7da1d90 → __libc_start_call_main+128 (libc)
 0x7fffffffe2a8|+0008: 0x0000000000000000
────────────────────────[ backtrace ]─────────────────────────
 #0 → main () at demo.c:5
 #1   __libc_start_main (...)
───────────────────────────────────────────────────────────────
```

每次 `next`/`step`/`continue` 停下，這個面板自動刷新——你的 gef。

## 實作步驟建議

### Step 1：基礎設施（顏色 + 記憶體分類）

先做底層工具（複用練習 E）：`Color` 類別、`classify(addr)`（解析 mappings 判斷 heap/stack/code/libc）、`symbolize(addr)`。這些是上色與 telescope 的基礎。

### Step 2：各 context 區塊（獨立函式）

分別寫 `show_registers()`、`show_code()`、`show_stack()`、`show_backtrace()`，每個用 Part 5 的 API（read_register / disassemble / read_memory / frame walking）+ Step 1 的工具上色。先能手動呼叫。

### Step 3：自動 context（events.stop）

把各區塊組成 `show_context()`，掛 `events.stop`（Ch 25）。做 `ctxon`/`ctxoff` 開關（連接/斷開 callback，注意防重複，Ch 25 踩雷）。

### Step 4：telescope 獨立指令

把 stack 區塊抽成 `tel` 指令（Ch 24），加「遞迴跟隨指標」（一個值是指標就讀它指向的，最多 N 層）。

### Step 5：設定系統

用 `gdb.Parameter`（Ch 24）做 `mygef-color`、`mygef-ctx-lines` 等，讓各區塊讀設定。做 `mygef config` 顯示所有設定。

### Step 6：選做功能 + 打包

挑 2+ 個進階功能（heap/trace/vmmap/frame filter/printer）。模組化、寫 README、測試 `.gdbinit` 安裝。

## 設計提示

<details>
<summary>點開架構建議（不是完整解答，是骨架）</summary>

```python
# mygef.py — 架構骨架（細節自己填）
import gdb

# ===== 設定 =====
class Config:
    # 用 Parameter 包裝，這裡簡化成全域
    color = True
    ctx_lines = 8
    sections = ["registers", "code", "stack", "backtrace"]

# ===== 顏色（Ch 28）=====
class Color:
    PALETTE = {"red":"\033[31m","green":"\033[32m","yellow":"\033[33m",
               "blue":"\033[34m","magenta":"\033[35m","cyan":"\033[36m",
               "bold":"\033[1m","reset":"\033[0m"}
    @staticmethod
    def wrap(s, c):
        if not Config.color: return str(s)
        return f"{Color.PALETTE[c]}{s}{Color.PALETTE['reset']}"

# ===== 記憶體分類（Ch 11,40 + 練習 E）=====
class MemMap:
    def __init__(self):
        self.maps = self._parse()
    def _parse(self):
        # 解析 info proc mappings → [(start,end,name,perms)]
        ...
    def classify(self, addr):
        # 回傳 ("heap"/"stack"/"code"/"libc"/None, color)
        ...

def symbolize(addr):
    # info symbol addr → "func+off" 或 None（Ch 28）
    ...

def color_value(val, memmap):
    # 依 classify 給值上色 + 加註解（→ symbol / "string"）
    kind, color = memmap.classify(val)
    ...

# ===== context 區塊（Part 5 各 API）=====
def show_registers(frame, memmap):
    # frame.read_register + color_value + EFLAGS 解碼（Ch 11,23）
    ...
def show_code(frame):
    # gdb.execute("x/5i $pc") 或用 disassemble API，標當前指令
    ...
def show_stack(frame, memmap):
    # telescope $sp，複用 tel 的邏輯
    ...
def show_backtrace():
    # 走 frame.older()，過濾 libc 雜訊（Ch 10,27）
    ...

def show_context(event=None):
    try:
        frame = gdb.selected_frame()
    except gdb.error:
        return
    memmap = MemMap()
    for sec in Config.sections:
        print(Color.wrap(f"───[ {sec} ]───", "blue"))
        {"registers": lambda: show_registers(frame, memmap),
         "code":      lambda: show_code(frame),
         "stack":     lambda: show_stack(frame, memmap),
         "backtrace": lambda: show_backtrace()}[sec]()

# ===== 自動 context 開關（Ch 25）=====
_connected = [False]
class CtxOn(gdb.Command):
    """ctxon — enable auto context."""
    def __init__(self): super().__init__("ctxon", gdb.COMMAND_USER)
    def invoke(self, arg, ft):
        if not _connected[0]:
            gdb.events.stop.connect(show_context); _connected[0] = True
        print("auto-context on")
# CtxOff 類似（disconnect）

# ===== telescope（Ch 24,28 + 練習 E）=====
class Telescope(gdb.Command):
    """tel <addr> [n] — recursive memory telescope."""
    def __init__(self): super().__init__("tel", gdb.COMMAND_DATA, gdb.COMPLETE_EXPRESSION)
    def invoke(self, arg, ft):
        # 讀記憶體、遞迴跟隨指標、上色、解符號
        ...

# ===== prefix + 設定（Ch 24）=====
class MyGef(gdb.Command):
    """mygef — toolkit. Subcommands: config, heap, vmmap, ..."""
    def __init__(self): super().__init__("mygef", gdb.COMMAND_USER, prefix=True)
    def invoke(self, arg, ft): print("mygef <config|heap|vmmap|...>")

# ... Parameter 們、選做功能 ...

MyGef(); CtxOn(); Telescope()  # 註冊
print("[mygef] loaded.")
```

關鍵設計決策：

- **context 區塊解耦**：每個 section 一個函式，`show_context` 依設定組裝——好擴充、好測試、好讓使用者開關。
- **MemMap 每次停重建**：mappings 可能變（dlopen、mmap），每次 context 重新解析。可加快取最佳化。
- **健壯第一**：每個讀記憶體的地方 try/except `gdb.MemoryError`——debug 的程式狀態常常壞，工具不能跟著崩。
- **顏色集中管理**：`Color.wrap` 統一處理「設定關閉時不上色」——pipe/logging 友善（Ch 28）。
- **複用**：context 的 stack 區塊 = `tel` 的邏輯；heap = 練習 E；backtrace 過濾 = Ch 27 frame filter 概念。不要重複造輪子。

</details>

## 評估你的成果

做完後，這樣自評（這是「能造工具的人」的標準）：

1. **天天用得下去嗎？** 真正好的工具你會取代 gef 來用。用一週，把不順手的修掉。
2. **讀壞程式不崩嗎？** 故意 debug 一個記憶體損壞的程式，你的 context 該優雅顯示 `<invalid>` 而非整個炸掉。
3. **別人裝得起來嗎？** 給同事一個 README + 一行 `.gdbinit` 設定，他能用嗎？
4. **你懂每一行嗎？** 對照 gef 原始碼，你的每個功能你都知道底層在做什麼（這是 Part 8 給你的底氣）。
5. **能擴充嗎？** 加一個新 context 區塊（如顯示某個你關心的全域狀態）要多久？好架構應該 10 分鐘。

## 和 gef/pwndbg 對比

做完拿你的 `mygef` 和 gef/pwndbg 並排：

- 它們有幾百個指令、支援多架構、處理無數 corner case、有完整測試——這是多年累積。
- 但**核心機制和你的一模一樣**：events.stop 自動 context、telescope 解指標、heap chunk 解析、frame filter 美化。
- 你做的是「最小可用的 gef」。要長成 gef 是工程量問題，不是知識問題——你已經有全部的知識。

讀它們的原始碼，現在你會一行行看懂——因為每個 API 你都用過，每個底層概念你都懂。**這就是這門課的終點：GDB 對你不再有黑盒。**

## 交付清單

- [ ] `mygef.py`（核心套件，模組化）
- [ ] `README.md`（功能、安裝、用法、截圖/示意）
- [ ] 至少完成核心 1-3 + 進階 2 個
- [ ] 一段 demo（錄個 asciinema 或寫個操作流程）
- [ ] （加分）發到 GitHub，讓別人能裝

## 結語：你走完的路

從 Ch 0 的「能不能 debug 從怎麼編譯就決定」，到這裡的「我寫了一個 gef」——你走過了：

- **會用**：執行控制、檢視、進階斷點、並行（Part 1-3）
- **會自動化**：TUI、腳本、命令語言（Part 4）
- **會擴充**：Python API、pretty-printer、插件（Part 5）
- **會應對真實世界**：C++/Rust/Go、最佳化 binary、core/remote/embedded（Part 6-7）
- **懂底層**：ptrace、DWARF、斷點實作、ASLR，自寫 mini debugger（Part 8）
- **能造工具**：你自己的 gef（這個專案）

大部分人對 GDB 的掌握停在第一層的一小角（`break`/`run`/`bt`/`print`）。你走完了全程。GDB 不再是你「用」的工具，而是你「懂」並能「改」的工具。

去 debug 點難的東西吧。

← 回到 [課程首頁](./README.md)
