# Ch 27 — Frame filter / decorator / Unwinder

> **目標**：客製化 backtrace 的呈現與重建。用 **frame filter** 過濾/美化/合併 backtrace 的 frame；用 **frame decorator** 改單一 frame 的顯示；用 **unwinder** 在 DWARF CFI 不可用（最佳化、手寫組語、stack 損壞）時自己重建 call stack。學完你能做出 gef 式的漂亮 backtrace，也能 debug 連 GDB 都 unwind 不了的 stack。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼需要動 backtrace

兩個獨立但相關的需求：

1. **美化/過濾**（frame filter）：預設 backtrace 充滿雜訊——一堆 `std::__detail::...`、libc 內部、framework 樣板。你想隱藏無聊的、合併同類的、給重要 frame 上色。這是「讓 backtrace 好讀」。

2. **重建**（unwinder）：當 GDB 自己 unwind 不下去——backtrace 顯示一堆 `??`、最佳化省了 frame pointer 又缺 CFI、手寫組語沒 DWARF、stack 被踩壞——你需要告訴 GDB「frame 該怎麼算」。這是「讓 backtrace 存在」。

gef/pwndbg 的漂亮 backtrace 用 frame filter；嵌入式/kernel/exploit 場景的 stack 重建用 unwinder。

## Frame filter：過濾與美化 backtrace

frame filter 在 backtrace 產生時介入，拿到 frame 序列，可以過濾、排序、修改、合併。

```python
# myfilter.py
import gdb
import itertools

class HideLibcFilter:
    def __init__(self):
        self.name = "hide_libc"
        self.priority = 100
        self.enabled = True
        gdb.frame_filters[self.name] = self     # 註冊到全域

    def filter(self, frame_iter):
        # frame_iter 是 FrameDecorator 的 iterator
        # 回傳一個（過濾/修改後的）iterator
        for fd in frame_iter:
            name = fd.function()
            if name and ("__libc" in str(name) or "std::__" in str(name)):
                continue                         # 跳過 libc/STL 內部 frame
            yield fd

HideLibcFilter()
```

```
(gdb) source myfilter.py
(gdb) bt                                  # libc 內部 frame 被濾掉，乾淨多了
(gdb) info frame-filter                   # 列出已註冊的 filter
(gdb) disable frame-filter global hide_libc
```

frame filter 是個有 `name`/`priority`/`enabled`/`filter()` 的物件。`filter()` 收到 frame decorator 的 iterator、回傳處理過的 iterator。多個 filter 按 priority 串接。

## Frame decorator：改單一 frame 的顯示

filter 裡你拿到的每個元素是 **FrameDecorator**——代表一個 frame 的可客製顯示。你可以包裝它來改顯示：

```python
import gdb
from gdb.FrameDecorator import FrameDecorator

class AnnotatedFrame(FrameDecorator):
    def __init__(self, fobj):
        super().__init__(fobj)
        self.fobj = fobj
    def function(self):
        # 改函式名的顯示（例如加標記）
        name = super().function()
        frame = self.inferior_frame()
        # 對「我們關心」的函式加標記
        if name and "process" in str(name):
            return f"★ {name}"
        return name
    # 也可覆寫 filename()、line()、frame_args()、frame_locals()...

class StarFilter:
    def __init__(self):
        self.name = "star"; self.priority = 90; self.enabled = True
        gdb.frame_filters[self.name] = self
    def filter(self, it):
        return map(AnnotatedFrame, it)

StarFilter()
```

FrameDecorator 可覆寫的方法：`function()`（函式名）、`filename()`、`line()`、`address()`、`frame_args()`、`frame_locals()`、`elided()`（合併的子 frame）。透過它你能完全掌控每個 frame 怎麼顯示——gef 漂亮 backtrace 的細節都在這。

## Unwinder：當 GDB 算不出 frame

承 Ch 10：backtrace 靠 DWARF CFI（`.eh_frame`）重建。當 CFI 不可用：

- 手寫組語沒產生 CFI
- 最佳化省 frame pointer 又 CFI 不全
- JIT 產生的程式碼
- stack 被踩壞
- 自訂 calling convention（某些 kernel / 嵌入式）

backtrace 就會在某層卡住、顯示 `??`。**unwinder** 讓你用 Python 教 GDB「從這個 frame 怎麼找到上一個」。

```python
# myunwinder.py
import gdb
from gdb.unwinder import Unwinder

class FramePointerUnwinder(Unwinder):
    """A naive rbp-chain unwinder for frames GDB can't handle."""
    def __init__(self):
        super().__init__("rbp_chain")
    def __call__(self, pending_frame):
        # pending_frame: 當前還沒 unwind 的 frame
        # 我們要：讀當前暫存器 → 算出上一個 frame 的 pc/sp/rbp
        rbp = pending_frame.read_register("rbp")
        rsp = pending_frame.read_register("rsp")
        pc = pending_frame.read_register("rip")

        # 判斷適不適用（這裡簡化：只在某條件下接手）
        if not self.should_handle(pc):
            return None                          # 不接手，讓 GDB 用預設

        # 經典 rbp 鏈：saved_rbp = [rbp], return_addr = [rbp+8]
        try:
            inf = gdb.selected_inferior()
            saved_rbp = int.from_bytes(inf.read_memory(int(rbp), 8), "little")
            ret_addr  = int.from_bytes(inf.read_memory(int(rbp)+8, 8), "little")
        except gdb.MemoryError:
            return None

        # 建立 unwind info：告訴 GDB 上一個 frame 的暫存器值
        unwind_info = pending_frame.create_unwind_info(
            FrameId(rbp, pc))                    # FrameId 需要 sp 與 pc
        unwind_info.add_saved_register("rip", ...) # 設定 caller 的 rip
        unwind_info.add_saved_register("rbp", ...)
        return unwind_info

    def should_handle(self, pc):
        return False   # 預設不接手；改成你的判斷條件
```

> 認識論誠實：unwinder 是 Python API 裡最硬的部分之一，且高度架構相關（這裡的 rbp 鏈是 x86-64 的；ARM/RISC-V 完全不同）。完整可用的 unwinder 需要正確處理 `FrameId`、`create_unwind_info`、`add_saved_register` 的細節，本範例是骨架示意。實務上你只在「GDB 預設 unwind 失敗」的特殊場景（JIT、自訂 ABI、kernel）才需要寫 unwinder——多數時候 DWARF CFI 夠用。

unwinder 的真實用途：

- **JIT runtime**（JVM、V8）：JIT 程式碼沒 DWARF，runtime 提供自己的 unwind 資訊，unwinder 橋接它。
- **kernel / 嵌入式**：自訂 stack 佈局。
- **exploit / 故意損壞的 stack**：手動指定怎麼讀。

## 三者的分工

```
   unwinder    →  決定「frame 序列是什麼」（從哪到哪、怎麼算上一層）
        │           ← 沒有 frame，後面都不用談
   frame filter →  決定「這串 frame 要顯示哪些、順序、合併」
        │
   frame decorator → 決定「每個 frame 顯示成什麼樣子」
        │
        ▼
   bt 的最終輸出
```

unwinder 在最底層（建構 frame），filter/decorator 在上層（呈現 frame）。多數人只需要 filter/decorator（美化），unwinder 是進階救援。

## 一個實用的 frame filter：gef 式 backtrace

```python
# nicebt.py — 隱藏雜訊 + 標記使用者 frame
import gdb
from gdb.FrameDecorator import FrameDecorator

NOISE = ("__libc", "std::__", "__gnu_cxx", "_start", "__libc_start")

class NiceFrame(FrameDecorator):
    def function(self):
        name = super().function()
        if name is None:
            return None
        s = str(name)
        # 對使用者自己的 frame 加箭頭
        fname = self.filename()
        if fname and "/usr/" not in fname:
            return f"→ {s}"
        return s

class NiceFilter:
    def __init__(self):
        self.name = "nicebt"; self.priority = 100; self.enabled = True
        gdb.frame_filters[self.name] = self
    def filter(self, it):
        for fd in it:
            name = fd.function()
            if name and any(n in str(name) for n in NOISE):
                continue
            yield NiceFrame(fd)

NiceFilter()
```

`bt` 後，libc/STL 雜訊消失、你自己的 frame 加箭頭——backtrace 終於好讀。Final Project 的 backtrace 區塊就用這個。

## 踩雷集錦

1. **frame filter 沒生效**：`enabled` 沒設 True、沒註冊到 `gdb.frame_filters`、或被更高 priority 的覆蓋。`info frame-filter` 檢查。
2. **filter 改壞 iterator**：`filter()` 必須回傳 iterator（用 `yield` 或 `map`/`filter`），回傳 list 也行但別回傳 None。
3. **過濾掉太多 frame 導致 backtrace 失真**：隱藏 frame 是雙面刃——把關鍵 frame 也藏了會誤導。提供開關（Parameter，Ch 24）讓使用者切換。
4. **unwinder 寫錯導致 backtrace 更糟**：unwinder 接手後算錯，backtrace 比原本還亂。`should_handle` 要保守，不確定就 `return None` 讓 GDB 處理。
5. **unwinder 的架構相關性**：x86-64 的 rbp 鏈在 ARM 完全不適用。寫跨架構要用 `pending_frame.architecture()` 分支。
6. **FrameDecorator 覆寫方法回傳型別錯**：`function()` 回字串、`line()` 回 int、`frame_args()` 回特定物件——型別錯會讓顯示崩。

## 進階：再往深一層

- **`elided()` 合併 frame**：FrameDecorator 的 `elided()` 回傳「被這個 frame 合併進來的子 frame」——把一組相關 frame 顯示成一層（如把 STL 內部摺疊）。
- **frame filter 的 priority 鏈**：多個 filter 按 priority 從高到低串接，前一個的輸出是後一個的輸入。設計時注意順序。
- **per-objfile frame filter**：註冊到 `objfile.frame_filters`，只對特定 library 生效。
- **unwinder 註冊範圍**：`gdb.unwinder.register_unwinder(locus, unwinder)`，locus 可為 None（全域）/objfile/progspace。
- **與 `bt` 的 `-no-filters`**：`bt -no-filters` 暫時跳過所有 filter 看原始 backtrace——debug filter 本身時用。
- **JIT unwinding 的真實案例**：看 V8/JVM 的 GDB JIT interface 怎麼配合 unwinder，是這塊的高階應用。

## 動手練習

1. source 本章的 `nicebt.py`，對一個會呼叫 STL/libc 的 C++ 程式 `bt`，比較有無 filter 的差異。
2. 用 `info frame-filter` 看註冊的 filter，`disable frame-filter global nicebt` 關掉再 `bt`，`bt -no-filters` 看原始。
3. 寫一個 FrameDecorator 只改 `function()`，對你關心的函式加 emoji/標記。
4. 寫一個 filter 把連續的 recursion frame 合併顯示成「`foo (×15)`」（用計數）。
5. （進階）研究 `bt` 在一個 `-O2 -fomit-frame-pointer` + strip CFI 的程式上怎麼壞掉，理解為什麼這時才需要 unwinder。
6. 讀 gef 的 backtrace 實作，對照本章的 filter/decorator 概念。

## 本章重點整理

- frame filter：過濾/排序/合併 backtrace 的 frame；物件有 `name`/`priority`/`enabled`/`filter()`，註冊到 `gdb.frame_filters`。
- frame decorator：改單一 frame 的顯示（`function()`/`line()`/`frame_args()`/`elided()` 等）。
- unwinder：當 DWARF CFI 不可用（JIT、最佳化、自訂 ABI、壞 stack）時，用 Python 教 GDB 怎麼重建 frame——進階救援，架構相關。
- 分工：unwinder 建構 frame → filter 選擇/排序 → decorator 呈現。多數人只需 filter/decorator。
- `bt -no-filters` 看原始；提供開關避免過濾誤導。

## 自我檢核

- [ ] frame filter、frame decorator、unwinder 三者的分工是什麼？
- [ ] 想隱藏 backtrace 裡的 libc/STL 雜訊，用哪個、怎麼做？
- [ ] 什麼情況 GDB 自己 unwind 不了、需要寫 unwinder？
- [ ] 過濾 backtrace 有什麼風險？怎麼緩解？
- [ ] 怎麼暫時看「未經 filter」的原始 backtrace？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Frame Filter API](https://sourceware.org/gdb/current/onlinedocs/gdb/Frame-Filter-API.html)**、**[Frame Decorator API](https://sourceware.org/gdb/current/onlinedocs/gdb/Frame-Decorator-API.html)**、**[Unwinding Frames in Python](https://sourceware.org/gdb/current/onlinedocs/gdb/Unwinding-Frames-in-Python.html)**
  - **讀哪裡**：三節對應本章三主題；unwinder 那節的 PendingFrame/UnwindInfo/FrameId 是寫 unwinder 的關鍵 reference。
  - **和本章的關聯**：本章核心的完整權威。

### 原始碼

- **[gef 的 backtrace / context_trace](https://github.com/hugsy/gef)**
  - **讀哪裡**：搜 backtrace 相關指令。
  - **和本章的關聯**：漂亮 backtrace 的工業實作；Final Project 對標。

- **[libstdc++ 的 frame filter（StdMapFilter 等）](https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libstdc%2B%2B-v3/python)**
  - **為什麼值得讀**：官方怎麼用 frame filter 美化 STL 相關 backtrace。

下一章補完 Python API 的剩餘武器：xmethod（自訂方法/索引）、convenience function、彩色輸出，以及 Python TUI window——做出完全自訂的視窗。

→ [Ch 28 Xmethod、彩色輸出、Python TUI window](./28-xmethods-and-tui-windows.md)
