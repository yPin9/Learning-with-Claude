# Ch 7 — TUI 模式與 layout

> 目標：熟悉 GDB 的 Text User Interface（TUI），能用 source / asm / regs 視圖組合，用快捷鍵切換 focus 與 layout。

## TUI 是什麼

純 CLI 的 GDB 需要你 `list` 看 source、再下 `n`、再 `list`、再 `p`... 上下文斷裂。TUI 把 terminal 分割成多個 window：

```
┌─────────────────────────────────────────────────┐
│    1  #include <stdio.h>                         │  ← source window
│    2                                             │
│    3  int square(int n) {                        │
│ >  4      return n * n;                          │  ← 當前行
│    5  }                                          │
│    6                                             │
│    7  int sum_of_squares(int n) {                │
│    8      int total = 0;                         │
│    9      for (int i = 1; i <= n; i++) {         │
│   10          total += square(i);                │
├─────────────────────────────────────────────────┤
│ (gdb) next                                       │  ← command window
│ (gdb) print n                                    │
│ $1 = 3                                           │
│ (gdb)                                            │
└─────────────────────────────────────────────────┘
```

你一邊輸入指令、一邊看 source 自動跟著游標跳，**上下文不再斷裂**。

## 進入 / 離開 TUI

```
(gdb) tui enable        # 進
(gdb) tui disable       # 離開
```

或直接用快捷鍵：

```
Ctrl-x a                # toggle TUI
```

**個人建議**：啟動 gdb 時直接進 TUI：

```bash
gdb -tui ./sample
```

或 `~/.gdbinit` 加 `tui enable`（但某些版本在啟動階段開 tui 會畫面錯亂，依版本斟酌）。

## Layout：幾個預設組合

```
(gdb) layout src        # source + command（最常用）
(gdb) layout asm        # assembly + command
(gdb) layout split      # source + assembly + command
(gdb) layout regs       # 在當前 layout 上加一個 registers window
(gdb) layout next       # 循環切換
(gdb) layout prev
```

圖示：

```
layout src:                    layout asm:
┌────────────┐                 ┌────────────┐
│   source   │                 │     asm    │
├────────────┤                 ├────────────┤
│  command   │                 │  command   │
└────────────┘                 └────────────┘

layout split:                  layout regs (+src):
┌────────────┐                 ┌────────────┐
│   source   │                 │ registers  │
├────────────┤                 ├────────────┤
│     asm    │                 │   source   │
├────────────┤                 ├────────────┤
│  command   │                 │  command   │
└────────────┘                 └────────────┘
```

## focus：哪個 window 收鍵盤輸入

TUI 下有個「焦點」概念 — 某個 window 收游標鍵、PageUp/PageDown 的輸入。

```
(gdb) focus cmd         # 命令列（預設）
(gdb) focus src         # 原始碼 window
(gdb) focus asm
(gdb) focus regs
(gdb) focus next        # 循環切
```

焦點在 cmd 時：你正常輸入 gdb 指令。
焦點在 src 時：游標鍵捲動 source，PageUp/PageDown 翻頁。

快捷鍵：`Ctrl-x o` 切下一個 focus。

## 常用快捷鍵

記下這六個就夠：

| 快捷鍵 | 作用 |
|---|---|
| `Ctrl-x a` | 進/出 TUI |
| `Ctrl-x o` | 切換 focus（cmd ↔ src ↔ asm ↔ regs） |
| `Ctrl-x 1` | 切到單 window layout（只留 source） |
| `Ctrl-x 2` | 切到雙 window layout |
| `Ctrl-l` | 重畫畫面（tui 偶爾畫面錯亂用這個救） |
| `Ctrl-p` / `Ctrl-n` | 上一個 / 下一個歷史命令（focus 在 cmd 時） |

**注意**：focus 在 src/asm 時，`Ctrl-p` / `Ctrl-n` 變成捲動 window，不是歷史命令。要打新指令先 `Ctrl-x o` 切回 cmd。

## 看 assembly：`layout asm` 與 `layout split`

當你要看「原始碼的每行對應什麼組語」，`layout split` 是殺手：

```
┌──────────────────────────────────────────┐
│   9      for (int i = 1; i <= n; i++) {  │
│  10          total += square(i);         │
│> 11      }                                │
├──────────────────────────────────────────┤
│=> 0x11a3: mov    -0x4(%rbp),%eax          │
│   0x11a6: cmp    -0x18(%rbp),%eax         │
│   0x11a9: jle    0x1188                   │
│   0x11ab: mov    -0xc(%rbp),%eax          │
│   0x11ae: leave                           │
│   0x11af: ret                             │
├──────────────────────────────────────────┤
│ (gdb)                                     │
└──────────────────────────────────────────┘
```

`si`（step instruction）時，游標在 asm window 裡跳。

## 看 registers：`layout regs`

```
(gdb) layout regs
```

會多出一個 window 顯示所有一般暫存器。每次執行一步後，**有變動的暫存器會被 highlight**（顏色不同、或加記號）。

```
rax 0x7                        7
rbx 0x0                        0
rcx 0x7ffff7fbd0b0  ...
rdx 0x7fffffffe1a8  ...
rsi 0x5                        5    ← 變了
rdi 0x2                        2    ← 變了
...
```

這對「step 一次、看哪些 register 被動到」非常直觀。

## `winheight` — 調整 window 大小

```
(gdb) winheight src +5          # source window 變高 5 行
(gdb) winheight asm -3          # asm 變矮 3 行
(gdb) winheight cmd 10          # cmd 變成 10 行
```

TUI 初學者常抱怨 source window 太小，五六行看不到上下文 — 調大就對了。

## `refresh` — 畫面錯亂救援

gdb TUI 跟 terminal multiplexer（tmux、screen）搭配時偶爾畫面亂七八糟。解法：

```
(gdb) refresh
```

或 `Ctrl-l`。

還是不行？離開 TUI 再進：`Ctrl-x a` 兩次。

實在不行？退出 gdb、重進。

## 一個常見抱怨：TUI 會跟某些 plugin 打架

gef、pwndbg 這類 plugin 會自己印大量輸出，跟 TUI 的 window 搶版面。通常只能二擇一。

## 實務上我怎麼用 TUI

- **快速 debug**：不開 TUI，純 CLI 就好，快。
- **一行一行細讀**：開 `layout src`，省去一直 `list`。
- **看組語**：開 `layout split`，配合 `stepi`。
- **看 stack 破壞現場**：`layout regs` + `x/20gx $rsp`，眼睛不用離開畫面。
- **長時間 session**：用 tmux 分一個 pane 開 gdb TUI，另一個 pane 看 log。

## 替代品：gdb --dashboard / .gdbinit hack

社群有各種「自製 dashboard」的方案，例如：

- **gdb-dashboard**（`cyrus-and/gdb-dashboard`）：一個 `.gdbinit` 腳本，自己用 Python 畫多 pane UI，不用進 TUI。
- **VS Code / CLion 的 GDB 整合**：GUI 化。
- **CGDB**：`cgdb`，外掛一個介面包 gdb，分割畫面更穩定。
- **Emacs GUD mode**：Emacs 使用者才會用。

這些都好東西，但學習階段先把原生 TUI 打熟，之後要換才知道你失去了什麼、得到了什麼。

## 常見坑

1. **啟動時直接開 TUI 但看到空白畫面**：有些 gdb 版本 + terminal 組合會這樣。解法：啟動後再 `tui enable`。
2. **畫面寬度不對**：terminal 被 resize 後，gdb 不會自動重算。按 `Ctrl-l` 重畫。
3. **source window 顯示 `[ No Source Available ]`**：不是沒 source，是 gdb 找不到檔案。`set substitute-path old new` 或 `directory /path/to/source` 指給它看。
4. **TUI 模式下 tab 補齊失靈**：某些 gdb 版本的 bug。暫時退出 TUI 完成指令再回來。
5. **高 refresh rate 的畫面讓終端效能吃緊**：例如用 tmux + nested SSH 時。`set tui border-kind ascii` 關掉 Unicode 邊框可以稍微緩解。

## 動手練習

1. 用 `gdb -tui ./sample` 進 TUI，`layout src`，跑一次你已經熟的 session。
2. `Ctrl-x 2` 切到 `layout split`，`stepi` 幾次，看組語跟源碼同時動。
3. `layout regs`，`si` 一次，看哪個暫存器被 highlight。
4. 調 source window 到 30 行：`winheight src 30`。
5. 退出 TUI（`Ctrl-x a`），純 CLI 走一次。感受差別。
6. 試把 `tui enable` 加進 `~/.gdbinit`，重啟 gdb 看是否順暢（不順的話 comment out）。

## 自我檢核

- [ ] 我能進出 TUI，切換 layout
- [ ] 我知道 focus 是什麼，能在 cmd / src / asm 之間切
- [ ] 我能同時看 source 跟 asm（layout split）
- [ ] 我能看暫存器視窗（layout regs）
- [ ] 畫面亂掉我知道怎麼救（Ctrl-l、refresh、重開）

下一章深入機器層次 — 反組譯、暫存器、stepi/nexti。不熟 asm 也沒關係，會教你看懂最常見的 x86_64 輸出。

→ [Ch 8 反組譯與暫存器](./08-disassembly-and-registers.md)
