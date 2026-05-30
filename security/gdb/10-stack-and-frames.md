# Ch 10 — Stack 與 frame

> **目標**：徹底搞懂 call stack 與 frame——`backtrace` 怎麼讀、`frame` / `up` / `down` 怎麼在呼叫鏈上移動、每個 frame 的區域變數與參數怎麼看，以及 stack frame 在記憶體裡的真實長相。這是 debug 崩潰、追呼叫來源的核心技能。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼 backtrace 是 debug 的第一招

程式崩潰了，你做的第一件事幾乎都是 `bt`。為什麼？因為 backtrace 回答了最關鍵的問題：**「程式是怎麼走到這裡的？」** 它顯示完整的函式呼叫鏈——誰呼叫了誰，一路到當前出事的地方。看懂 backtrace，等於拿到犯罪現場的時間線。

但很多人只會看 `bt` 的第一行就停了。這章要讓你能在呼叫鏈的**任何一層**自由穿梭、檢視每層的區域變數、並理解 stack frame 在記憶體裡到底是什麼。

## 先建立直覺：一疊盤子

call stack 就是一疊盤子。每呼叫一個函式，就往上疊一個盤子（frame）；函式 return，盤子拿走。

```c
// stack_demo.c — gcc -g -O0
#include <stdio.h>
int leaf(int n)   { int r = n * 2; return r; }      // 最上層
int middle(int x) { int y = leaf(x + 1); return y; }
int top(int a)    { return middle(a + 10); }
int main(void)    { return top(5); }                 // 最底層
```

呼叫 `top(5)` 時，stack 長成：

```
   高位址
   ┌─────────────────┐
   │ main 的 frame   │  ← frame #3（最外、最底）
   ├─────────────────┤
   │ top 的 frame    │  ← frame #2   a=5
   ├─────────────────┤
   │ middle 的 frame │  ← frame #1   x=15
   ├─────────────────┤
   │ leaf 的 frame   │  ← frame #0（最內、當前執行中）  n=16
   └─────────────────┘
   低位址（stack 向下成長）
```

GDB 的 frame 編號：**#0 是最內層（當前執行的）**，數字越大越外層。`main` 通常是編號最大的那個。這個編號方向很多人搞反，記住：**#0 = 你現在在哪**。

## `backtrace`：看完整呼叫鏈

```
(gdb) break leaf
(gdb) run
(gdb) backtrace          # 簡寫 bt
#0  leaf (n=16) at stack_demo.c:3
#1  0x...118a in middle (x=15) at stack_demo.c:4
#2  0x...11a8 in top (a=5) at stack_demo.c:5
#3  0x...11c1 in main () at stack_demo.c:6
```

每行讀法：`#編號  位址 in 函式名 (參數=值) at 檔案:行`。

- `#0 leaf (n=16)`：當前在 `leaf`，參數 `n=16`
- `#1 ... middle (x=15)`：`leaf` 是被 `middle` 呼叫的，當時 `x=15`
- 一路到 `#3 main`

backtrace 變體：

```
(gdb) bt 2               # 只看最內 2 層
(gdb) bt -2              # 只看最外 2 層
(gdb) bt full            # 連每層的區域變數都印出來！
(gdb) where              # = backtrace（別名）
```

`bt full` 特別好用——一次看到所有層的所有區域變數，崩潰時的完整現場快照。

## 在 frame 間穿梭

backtrace 只是「看」。要**檢視某一層的變數**，得先「移動」到那層：

```
(gdb) frame 2            # 切換到 frame #2 (top)；簡寫 f 2
#2  0x...11a8 in top (a=5) at stack_demo.c:5
5       int top(int a) { return middle(a + 10); }
(gdb) print a            # 現在 print 的是 top 的區域變數！
$1 = 5
(gdb) up                 # 往「外」走一層（編號變大）
#3  main () at ...
(gdb) down               # 往「內」走一層（編號變小）
#2  top ...
(gdb) frame              # 不帶參數：顯示當前在哪一層
```

關鍵觀念：**`print x` 印的是「當前 frame」的 `x`。** 同一個變數名 `r` 在 `leaf` 和別處可能都有，你 `print r` 印哪個，取決於你現在站在哪個 frame。切到 frame #0 印 leaf 的、切到別層印別層的。這是 debug 遞迴與同名變數時的關鍵。

`up`/`down` 的方向：`up` 朝呼叫者（編號大、更外層）、`down` 朝被呼叫者（編號小、更內層）。記法：盤子疊上去叫 stack「成長」，但呼叫**源頭**在底部，`up` 是往源頭走。

## 檢視單一 frame 的細節

```
(gdb) info frame         # 當前 frame 的底層資訊（CFA、return addr、saved regs）
(gdb) info args          # 當前 frame 的參數
(gdb) info locals        # 當前 frame 的所有區域變數
(gdb) frame              # 當前 frame 的原始碼行
```

`info args` + `info locals` 是「我現在這層有什麼」的快速總覽。debug 時常切到某 frame 後馬上 `info locals` 看全貌。

## stack frame 在記憶體裡是什麼

理解底層（Ch 27 unwinder、Ch 41 mini debugger 會用）。一個典型的 x86-64 frame：

```
   高位址
   ┌───────────────────────┐
   │ 呼叫者壓入的參數（>6個）│
   ├───────────────────────┤
   │ return address        │ ← call 指令壓入：函式 return 後跳回哪
   ├───────────────────────┤ ← 這裡是 rbp（frame base pointer）
   │ saved rbp（舊的 rbp） │
   ├───────────────────────┤
   │ 區域變數              │
   │ ...                   │
   ├───────────────────────┤ ← rsp（stack top）
   低位址
```

GDB 怎麼從 `$rsp`/`$rbp` 重建整條 backtrace？這叫 **stack unwinding**：

1. 當前 `$pc` → 查 DWARF / `.eh_frame` 的 CFI（call frame information），知道這個函式的 frame 多大、return address 在哪。
2. 讀出 return address → 知道呼叫者是誰、它的 `$pc`。
3. 算出呼叫者的 frame base → 重複步驟 1。
4. 一路到 `main`（或無法再 unwind）。

```
(gdb) info frame
Stack level 0, frame at 0x7fffffffe2a0:
 rip = 0x...1149 in leaf (stack_demo.c:3); saved rip = 0x...118a
 called by frame at 0x7fffffffe2c0
 source language c.
 Arglist at 0x7fffffffe290, args: n=16
 Locals at 0x7fffffffe290, Previous frame's sp is 0x7fffffffe2a0
 Saved registers:
  rbp at 0x7fffffffe290, rip at 0x7fffffffe298
```

這些 `saved rip`、`called by frame` 就是 unwinding 的中間結果。Ch 27 會教你在 stack 損壞時自訂 unwinder。

> 認識論誠實：現代編譯器常用 `-fomit-frame-pointer`（省掉 `rbp` 當 frame pointer 以多一個暫存器用），這時 GDB **不能**靠 `rbp` 鏈走 backtrace，必須完全依賴 DWARF CFI（`.eh_frame`）。所以「backtrace 靠 rbp 鏈」是簡化模型；真實情況是「優先用 DWARF CFI，rbp 鏈只是 fallback」。最佳化過的 binary backtrace 會壞，就是 CFI 不全（Ch 32）。

## 一個崩潰分析的完整流程

```
(gdb) run
Program received signal SIGSEGV, Segmentation fault.
0x... in process_node (node=0x0) at tree.c:45
45      return node->value;              # node 是 NULL！
(gdb) bt                                 # 怎麼走到這的？
#0  process_node (node=0x0) at tree.c:45
#1  ... in traverse (root=0x555...) at tree.c:60
#2  ... in main () at tree.c:80
(gdb) frame 1                            # 上一層，看誰傳了 NULL
(gdb) print root                         # root 是有效的
(gdb) print root->left                   # 但 root->left 是 0！找到根因
$1 = (struct Node *) 0x0
```

從崩潰點 `bt` 找呼叫鏈 → `frame` 切到上層 → 檢視變數找出「誰傳了壞值」。這是 debug crash 的標準舞步，你會跳一輩子。

## 踩雷集錦

1. **frame 編號方向搞反**：#0 是**最內層當前**，不是最外層。`main` 是編號**最大**的。
2. **`print x` 印錯層的變數**：忘了先 `frame N` 切到對的層。同名變數在不同 frame 是不同東西。
3. **backtrace 顯示 `??` 或亂掉**：stack 被踩壞（buffer overflow）、或最佳化 binary CFI 不全、或 strip 無符號。`??` 通常代表 GDB unwind 不下去了。
4. **`Backtrace stopped: previous frame inner to this frame`**：unwind 邏輯偵測到異常（frame 位址方向不對），通常是 stack corruption——這本身就是個重要線索（可能 overflow）。
5. **看不到參數值（`<optimized out>`）**：最佳化把參數丟暫存器又覆蓋了。`-O0` 重編或用 `-Og`（Ch 32）。
6. **`up` / `down` 走錯方向**：`up` 往呼叫者（外層、編號大）。想到「出事的源頭在更外層」就不會反。

## 進階：再往深一層

- **`frame` 的位址形式**：`frame 0x7fffffffe2a0` 直接用 frame 位址定位，stack 損壞時手動指定 frame 救援。
- **`select-frame`**：和 `frame` 像但不印原始碼，寫腳本時用（不想要輸出雜訊）。
- **`bt full` 在大型 backtrace 的妙用**：core dump 分析（Ch 33）時，`bt full` 一次撈出所有層所有變數，存成報告。
- **`set backtrace limit N`**：限制 backtrace 深度，避免無窮遞迴炸出十萬行。
- **`set backtrace past-main on`**：看 `main` 之前的 frame（`__libc_start_main`、`_start`），debug C runtime 啟動時用。
- **frame filter**（Ch 27）：用 Python 自訂 backtrace 的呈現——隱藏無聊的 frame、美化顯示。gef/pwndbg 的漂亮 backtrace 就靠這個。
- **inline frame**：最佳化把函式 inline 後，DWARF 仍可記錄「邏輯上的」呼叫層，GDB 會顯示 `(inlined)` frame。`info frame` 看得到。

## 動手練習

1. 對 `stack_demo.c`，`break leaf` + `run` + `bt`，對照本章的「一疊盤子」圖，指出每個 frame。
2. 用 `frame 2` 切到 `top`，`print a`；再 `frame 0`，`print n`；體會「print 印當前 frame」。
3. `bt full` 一次看所有層的區域變數。
4. `info frame` 看當前 frame 的 saved rip、saved rbp，對照本章的記憶體佈局圖。
5. 寫一個解 NULL 指標的崩潰程式，重現 SIGSEGV，用「bt → frame → print」流程找出誰傳了 NULL。
6. （進階）用 `-O2` 重編，看 backtrace 怎麼變糊（參數 `<optimized out>`、層級被 inline 合併）。

## 本章重點整理

- backtrace 回答「程式怎麼走到這」；frame #0 = 當前最內層，編號越大越外層。
- `bt` / `bt full` / `bt N` 看呼叫鏈；`frame N` / `up` / `down` 在層間穿梭。
- `print x` 印「當前 frame」的 x——切錯層印錯變數。
- `info args` / `info locals` / `info frame` 看單層細節。
- backtrace 靠 stack unwinding：優先用 DWARF CFI（`.eh_frame`），rbp 鏈只是 fallback；最佳化 binary CFI 不全所以 backtrace 會壞。

## 自我檢核

- [ ] frame #0 是最內還是最外？`main` 通常是哪個編號？
- [ ] 為什麼 `print x` 有時印出非預期的值？怎麼確保印對 frame 的？
- [ ] `up` 往哪個方向走（呼叫者還是被呼叫者）？
- [ ] backtrace 出現一堆 `??` 可能代表什麼？其中哪個情況本身就是 bug 線索？
- [ ] 為什麼最佳化過的 binary backtrace 容易壞？跟 frame pointer 有什麼關係？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Examining the Stack](https://sourceware.org/gdb/current/onlinedocs/gdb/Stack.html)**
  - **讀哪裡**：Backtrace、Selecting a Frame、Frame Info 各節。
  - **和本章的關聯**：本章所有指令的完整參考；`set backtrace` 系列開關也在這。

### 部落格 / 文章

- **[How debuggers work: Part 3 (Debugging information)](https://eli.thegreenplace.net/2011/02/07/how-debuggers-work-part-3-debugging-information)** — Eli Bendersky
  - **這篇說什麼**：DWARF 怎麼支援變數與 frame 的定位。
  - **和本章的關聯**：本章 unwinding 的資訊來源解析；Ch 38 的預習。

- **[Stack frame layout on x86-64](https://eli.thegreenplace.net/2011/09/06/stack-frame-layout-on-x86-64)** — Eli Bendersky
  - **這篇說什麼**：x86-64 stack frame 的精確佈局與 calling convention。
  - **讀哪裡**：整篇；本章的記憶體佈局圖在這有暫存器級的完整版。
  - **為什麼值得讀**：理解 frame 記憶體佈局是 Ch 27 unwinder、Ch 41 mini debugger 的硬需求。

### 規格

- **[System V AMD64 ABI](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：§3.2 Function Calling Sequence（stack frame、參數傳遞）。
  - **和本章的關聯**：frame 佈局與參數放哪的權威；Ch 11、Ch 40 也會回來。

stack 看完，下一章鑽到最底層：暫存器與原始記憶體——沒有符號時你唯一的依靠。

→ [Ch 11 暫存器與記憶體](./11-registers-and-memory.md)
