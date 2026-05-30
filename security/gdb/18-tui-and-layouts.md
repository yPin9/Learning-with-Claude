# Ch 18 — TUI 與 layout

> **目標**：把 GDB 的純文字介面升級成「看得到原始碼/組語/暫存器」的 TUI（Text User Interface）。掌握 `tui enable`、layout 切換、winheight、focus、SingleKey 模式、自訂 layout，以及 TUI 的坑與替代方案。讓你的 debug 從「盲打」變成「看著畫面走」。

> **環境**：GDB 13/14，Linux x86_64，terminal 支援 curses（多數現代 terminal 都行）。

## 為什麼要 TUI

預設 GDB 是純命令列：你 `list` 看一段原始碼，`step` 一下，再 `list`，畫面一直往下捲，很難維持「我在哪」的空間感。TUI 把畫面切成幾塊，**固定顯示**原始碼（並高亮當前行）、組語、暫存器、命令——你 step 一步，原始碼視窗的高亮自動移動，像個簡易 IDE。

對 source-level debug，TUI 大幅降低認知負擔。對組語級逆向，TUI 的 asm + reg 視窗讓你同時看指令和暫存器變化。不過 TUI 也有坑（重繪、程式輸出干擾），這章一併講，並告訴你什麼時候該用 gef/pwndbg 的 context 視窗（Final Project 目標）取代它。

## 開關 TUI

```
(gdb) tui enable          # 進入 TUI 模式
(gdb) tui disable         # 退出回純 CLI
```

快捷鍵：

- **Ctrl-X + A**：切換 TUI 開/關（最常用）
- **Ctrl-X + 1**：單視窗 layout
- **Ctrl-X + 2**：雙視窗 layout（循環切換）
- **Ctrl-L**：重繪畫面（畫面亂掉時的救命鍵）

或啟動時直接進：`gdb -tui ./prog`。

進入後畫面分成：上方一個或多個資料視窗（原始碼/組語/暫存器），下方是命令列。當前執行行用高亮 + 左邊的標記顯示，斷點也會標示。

## layout：決定看哪些視窗

```
(gdb) layout src          # 原始碼 + 命令（最常用）
(gdb) layout asm          # 組語 + 命令
(gdb) layout split        # 原始碼 + 組語 + 命令（上下對照）
(gdb) layout regs         # 在現有 layout 上加暫存器視窗
(gdb) layout next         # 循環到下一個 layout
(gdb) layout prev
```

常見用法：

- source-level debug → `layout src`
- 逆向 / 看最佳化過的 code → `layout asm` 或 `layout split`
- 組語級且要盯暫存器 → `layout asm` + `layout regs`（regs 疊加在上面）

```
   layout split 的畫面
   ┌────────────────────────────────┐
   │ source: hello.c                │
   │   9   int main(void) {         │
   │ > 10      int x = 5;           │  ← 高亮當前行
   │  11      foo(x);               │
   ├────────────────────────────────┤
   │ asm:                           │
   │ > 0x1149 mov $5, -0x4(%rbp)    │  ← 對應的組語
   │   0x1150 ...                   │
   ├────────────────────────────────┤
   │ (gdb) next                     │  ← 命令列
   └────────────────────────────────┘
```

## focus：鍵盤給誰用

TUI 裡，方向鍵 / PageUp/Down 可以捲動視窗，但**捲哪個視窗**取決於 focus 在哪：

```
(gdb) focus src           # focus 給原始碼視窗（方向鍵捲原始碼）
(gdb) focus cmd           # focus 給命令列（方向鍵變成命令歷史）
(gdb) focus next          # 循環 focus
```

快捷鍵 **Ctrl-X + O** 切換 focus。

> 常見困惑：在 TUI 裡按上下鍵，預期是叫出上一個指令（命令歷史），結果卻是捲動原始碼——因為 focus 在 src 視窗。`focus cmd`（或 Ctrl-X O）切回命令列就正常了。這是 TUI 新手最常卡的點。

## 調整視窗大小

```
(gdb) winheight src +5    # 原始碼視窗加高 5 行
(gdb) winheight asm -3
(gdb) info win            # 看目前各視窗與大小
```

或 focus 到某視窗後用快捷鍵調整。原始碼視窗太小看不到上下文時很有用。

## SingleKey 模式：單鍵 debug

```
   按 Ctrl-X + S 進入 SingleKey 模式
```

在 SingleKey 模式下，常用指令變成**單一按鍵**，不用打整個字 + Enter：

- `c` = continue
- `n` = next
- `s` = step
- `f` = finish
- `u` = until
- `v` = info locals
- `w` = where (backtrace)
- `q` = 離開 SingleKey

step-heavy 的 debug session 用 SingleKey 飛快——一直按 `n` 就一直 next。要打完整指令時按其他鍵會暫時跳回命令列。

## 自訂 layout（GDB 10+）

GDB 10+ 可以用 `tui new-layout` 自訂視窗組合與比例：

```
(gdb) tui new-layout mylayout src 2 regs 1 cmd 1
# 名為 mylayout：原始碼佔 2 份高、暫存器 1 份、命令 1 份
(gdb) layout mylayout
```

`tui new-layout 名字 視窗1 權重1 視窗2 權重2 ...`。可用的視窗有 `src`、`asm`、`regs`、`cmd`、`status`，還能水平分割（用 `{...}`）。把你愛的 layout 寫進 `.gdbinit`（Ch 19），每次啟動就有。

這是「自訂 GDB 介面」的第一步。Python TUI window API（Ch 28）能更進一步寫出完全自訂的視窗（顯示 heap、自訂資料）——那是 Final Project 插件的能力。

## 踩雷集錦

1. **方向鍵不叫命令歷史反而捲原始碼**：focus 在 src 視窗。`focus cmd` 或 Ctrl-X O 切回。
2. **程式的 stdout 把 TUI 畫面弄亂**：inferior 的輸出和 TUI 搶同一個 terminal，會打亂排版。Ctrl-L 重繪救急；根本解法是把程式輸出導到別處（`run > /tmp/out`）或用 `tty` 指令把 inferior I/O 分到另一個 terminal。
3. **畫面整個花掉**：terminal 太小、resize、或 curses 相容問題。Ctrl-L 重繪；不行就 `tui disable` 再 `enable`。
4. **TUI 下貼上多行指令出問題**：TUI 的輸入處理對大量貼上不友善。複雜腳本用 `source` 檔案（Ch 19）而非貼上。
5. **以為 TUI = IDE**：TUI 是輕量文字介面，沒有滑鼠、沒有變數懸停。要更強的視覺化用 gef/pwndbg 的 context（或自己寫，Final Project），或乾脆用 VS Code/CLion 的 GDB 後端。
6. **regs 視窗只顯示通用暫存器**：SIMD/FPU 要 `layout regs` 後在命令列 `info all-registers`，TUI regs 視窗本身有限。

## 進階：再往深一層

- **`tty /dev/pts/N`**：把 inferior 的輸入輸出綁到另一個 terminal，徹底解決「程式輸出弄亂 TUI」。在另一個 terminal 跑 `tty` 拿到路徑，在 GDB 裡 `tty /dev/pts/N`。
- **`set tui border-kind` / `set tui ... -style`**：客製 TUI 邊框與配色（GDB 對 TUI 有一系列 style 設定）。
- **Python TUI window（Ch 28）**：`gdb.TuiWindow` API 讓你寫完全自訂的視窗——顯示 heap chunk、自訂的記憶體 telescope、任何你要的東西。這是 gef context 與 Final Project 的進階能力。
- **gef/pwndbg 的 context**：它們不用 TUI，而是每次停下來時「印出」一大塊彩色 context（registers/stack/code/backtrace）。優點：不受 TUI 重繪問題困擾、可高度自訂、可彩色;缺點：每次停都重印（畫面往下捲）。Final Project 你會做這種。
- **`focus` 的程式化**：在 `.gdbinit` 裡預設 focus、layout，配 `define hook-run`（Ch 20）每次 run 自動進 TUI。

## 動手練習

1. 對任意 `-g` 程式，`tui enable` + `layout src`，`break main` + `run`，連按 `next`，觀察高亮行自動移動。
2. 切到 `layout split`，看原始碼與組語對照；再 `layout asm` + `layout regs`，組語級單步看暫存器變化。
3. 故意讓程式 `printf` 大量輸出，觀察 TUI 被弄亂，用 Ctrl-L 重繪；再用 `run > /tmp/o` 導開輸出比較。
4. 進 SingleKey 模式（Ctrl-X S），用單鍵 `n`/`s`/`c`/`f` debug 一輪。
5. 用 `tui new-layout` 自訂一個「src 大、regs 小」的 layout，套用它，再寫進 `~/.gdbinit`。
6. 玩 focus：在 src focus 下按上下鍵（捲原始碼）vs cmd focus 下（命令歷史），體會差別。

## 本章重點整理

- TUI 把畫面切成原始碼/組語/暫存器/命令視窗，高亮當前行——輕量 IDE 感。
- `tui enable`、Ctrl-X A 切換；`layout src/asm/split/regs` 選視窗；Ctrl-L 重繪救命。
- focus 決定方向鍵捲哪個視窗（Ctrl-X O 切換）——新手最常卡這。
- SingleKey 模式（Ctrl-X S）讓 n/s/c/f 變單鍵，step-heavy debug 飛快。
- TUI 易被程式輸出弄亂；`tty` 分流或 gef/pwndbg context（Final Project）是進階解。

## 自我檢核

- [ ] TUI 裡方向鍵不叫命令歷史，怎麼回事？怎麼修？
- [ ] source-level、組語逆向、看暫存器，各該用哪個 layout？
- [ ] 程式輸出把 TUI 弄亂，有哪些解法？
- [ ] SingleKey 模式適合什麼場景？
- [ ] TUI 和 gef/pwndbg 的 context 視窗各有什麼優缺點？

## 延伸閱讀

### 官方文件

- **[GDB Manual: TUI](https://sourceware.org/gdb/current/onlinedocs/gdb/TUI.html)**
  - **讀哪裡**：TUI Overview、Keys、Commands、Configuration、`tui new-layout`。
  - **和本章的關聯**：本章所有 TUI 功能的權威，含自訂 layout 與 style。

### 部落格 / 文章

- **[GDB TUI mode tips](https://sourceware.org/gdb/wiki/GDB%20Front%20Ends)** — GDB Wiki
  - **這篇說什麼**：TUI 與各種 GDB 前端（含 IDE 整合）的選擇。
  - **為什麼值得讀**：幫你決定何時用 TUI、何時用 gef、何時用 IDE 後端。

- **[gef / pwndbg context](https://github.com/hugsy/gef)**
  - **和本章的關聯**：TUI 的替代方案；Final Project 你會做出類似的 context 視窗。

下一章把你在 session 裡調好的所有偏好（含 TUI、print 設定）持久化，並學會寫命令腳本：`.gdbinit` 與 auto-load 安全模型。

→ [Ch 19 .gdbinit、auto-load 與安全模型](./19-gdbinit-and-autoload.md)
