# Ch 11 — 暫存器與記憶體

> **目標**：掌握 GDB 的最底層檢視——暫存器（`info registers`、`$rax`/`$pc`/`$sp`）、x86-64 暫存器的角色、EFLAGS 旗標解碼、記憶體映射（`info proc mappings`）、反組譯（`disassemble`）。沒有符號時，這是你唯一的依靠；逆向與 pwn 全在這層工作。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`（也會看無符號情境）。

## 為什麼要下到暫存器層

前面幾章都站在「有原始碼、有型別」的舒適圈。但真實世界常常沒有：strip 過的 binary、最佳化到變數消失、第三方 `.so`、惡意程式、CTF。這時 `print x` 沒用，你只剩**暫存器**和**原始記憶體**。能在這層工作，你才算真的會 debug——逆向工程師整天住在這裡。

而且，就算有原始碼，理解暫存器層能讓你看穿「`<optimized out>` 的變數其實在 `$rbx`」、「函式回傳值在 `$rax`」這些 DWARF 之下的真相。

## x86-64 暫存器地圖

```
(gdb) info registers          # 簡寫 i r；印所有通用暫存器
rax  0x10    16               # 回傳值 / 第一個 syscall 號
rbx  0x0     0                # callee-saved
rcx  ...                      # 第 4 個參數 / 雜用
rdx  ...                      # 第 3 個參數
rsi  ...                      # 第 2 個參數
rdi  ...                      # 第 1 個參數
rbp  0x7fff...               # frame base pointer
rsp  0x7fff...               # stack pointer（stack top）
r8 ~ r15                      # 額外通用暫存器（r8-r9 是第5,6參數）
rip  0x...1149  <leaf+8>     # program counter（下一條要執行的指令）
eflags 0x202  [ IF ]         # 旗標暫存器
cs/ss/ds/es/fs/gs            # 段暫存器
```

你不用背全部，但這幾個必記（System V AMD64 ABI，Ch 10 提過）：

| 暫存器 | 角色 |
|---|---|
| `rdi, rsi, rdx, rcx, r8, r9` | 函式的第 1–6 個整數/指標參數（依序） |
| `rax` | **回傳值**；syscall 號 |
| `rsp` | stack pointer（指向 stack top） |
| `rbp` | frame base pointer（frame 基準） |
| `rip` | program counter（下一條指令位址） |

所以練習 A 的「`finish` 後看回傳值」其實就是看 `$rax`；「函式參數」就是進入時的 `$rdi`、`$rsi`…。

## 把暫存器當變數用

承 Ch 8，每個暫存器都是一個 convenience variable：

```
(gdb) print $rax               # 印單一暫存器
$1 = 16
(gdb) print/x $rsp             # hex 印 stack pointer
(gdb) print $pc                # = $rip（GDB 的架構無關別名）
(gdb) print $sp                # = $rsp
(gdb) print $eax               # 32 位元版（rax 的低 32 bit）
(gdb) print $rdi               # 第一個參數
(gdb) set $rax = 1             # 改暫存器！（練習 A 強闖用過）
(gdb) set $pc = 0x...1170      # 改 PC = 改變執行流（危險但強大）
```

`$pc`、`$sp`、`$fp` 是 GDB 提供的**架構無關別名**——在 ARM 上 `$pc` 自動對到 ARM 的 PC。寫跨架構腳本時用這些別名，不要寫死 `$rip`。

## EFLAGS：條件跳躍的真相

`eflags` 是一堆 1-bit 旗標的集合，決定條件跳躍（`je`/`jne`/`jg`…）怎麼走。逆向時改它就能扭轉分支：

```
(gdb) info registers eflags
eflags  0x202  [ IF ]          # [ ] 裡列出「設定為 1」的旗標
```

重要旗標：

| 旗標 | 名稱 | 意義 |
|---|---|---|
| `ZF` | Zero Flag | 上次運算結果為 0（`cmp` 相等時設） |
| `SF` | Sign Flag | 結果為負 |
| `CF` | Carry Flag | 無號溢位/借位 |
| `OF` | Overflow Flag | 有號溢位 |
| `IF` | Interrupt Flag | 中斷開啟 |
| `DF` | Direction Flag | 字串操作方向 |

`cmp a, b` 後，`je`（jump if equal）看的就是 ZF。所以逆向強闖的另一招（練習 A 提過）是直接改 ZF：

```
(gdb) set $eflags |= (1 << 6)      # 設 ZF（第 6 bit）→ 讓 je 跳 / jne 不跳
(gdb) set $eflags &= ~(1 << 6)     # 清 ZF
```

理解 `cmp` + 條件跳躍 + EFLAGS 三者關係，是讀組語控制流的關鍵。

## `disassemble`：看組語

```
(gdb) disassemble              # 反組譯當前函式；簡寫 disas
(gdb) disassemble main         # 指定函式
(gdb) disassemble 0x1149, 0x1180     # 位址範圍
(gdb) disassemble /r main      # 連 raw bytes 一起顯示
(gdb) disassemble /s main      # 組語 + 對應原始碼交錯（有 -g 時超讚）
```

```
(gdb) disassemble leaf
Dump of assembler code for function leaf:
   0x...1149 <+0>:   push   %rbp
   0x...114a <+1>:   mov    %rsp,%rbp
=> 0x...114d <+4>:   mov    %edi,-0x14(%rbp)     # => 標當前 $pc 位置！
   0x...1150 <+7>:   mov    -0x14(%rbp),%eax
   ...
   0x...115e <+21>:  ret
End of assembler code.
```

`=>` 箭頭標示 `$pc` 當前在哪一條——配合 `stepi` 一條條走，看 CPU 真正在做什麼。

切換組語語法（Intel vs AT&T）：

```
(gdb) set disassembly-flavor intel    # Intel 語法（mov dst, src）
(gdb) set disassembly-flavor att      # AT&T 語法（mov src, dst，GDB 預設）
```

AT&T（`mov %rsp, %rbp`，src 在前、暫存器加 `%`、立即數加 `$`）是 GDB 預設但很多人不習慣；Intel（`mov rbp, rsp`，dst 在前）比較像主流書籍。挑你順的，寫進 `.gdbinit`。

## 記憶體映射：程式的記憶體長什麼樣

```
(gdb) info proc mappings       # inferior 的完整記憶體佈局（需要 /proc）
          Start Addr   End Addr   Size   Offset  Perms  objfile
      0x555555554000  ...557000  ...      0x0    r--p   /path/prog   # 程式 .text/.rodata
      0x555555557000  ...558000  ...      ...    r-xp   ...          # 可執行段
      0x7ffff7d... ... libc.so                                       # 共享庫
      0x7ffffffde000 ...fff000   ...      ...    rw-p   [stack]      # stack
                                                          [heap]     # heap
```

`info proc mappings` 告訴你哪段記憶體是程式碼、哪段是 stack/heap、哪段是哪個 `.so`、各自的權限（`r/w/x`）。逆向與 pwn 必看：

- 算 ASLR 的載入基址（Ch 40）
- 確認某位址落在哪個區段（程式碼？stack？）
- 找可寫可執行的段（exploit）

相關指令：

```
(gdb) info proc all            # process 的全部資訊
(gdb) maintenance info sections    # ELF section 的載入位址
(gdb) info files               # 載入的 objfile 與各 section 位址
(gdb) p/x $rsp                 # 配合 mappings 確認 stack 範圍
```

## 一個無符號逆向的完整流程

把這章串起來——對一個 strip 過的函式：

```
(gdb) break *0x555555555149    # 用位址下斷（無符號）
(gdb) run
(gdb) info registers rdi rsi   # 看傳進來的參數
rdi  0x7fffffffe3f0            # 第一個參數（可能是指標）
rsi  0x5                       # 第二個參數
(gdb) x/s $rdi                 # 把 rdi 當字串看
0x7fffffffe3f0:  "input"
(gdb) disassemble $pc, +40     # 看接下來的組語
(gdb) display/i $pc            # 每步顯示指令
(gdb) stepi                    # 一條條走
(gdb) info registers eflags    # 在 cmp 後看旗標決定跳哪
```

「位址斷點 → 看暫存器參數 → 把指標當資料看 → 反組譯 → 單步 + 看旗標」——這是逆向的核心循環，練習 A 已經讓你走過一遍，這章補上完整的暫存器與記憶體視角。

## 踩雷集錦

1. **以為改 `$pc` 很安全**：`set $pc = X` 直接改執行流，但你得確保 X 是合法指令邊界、stack 狀態一致，否則下一步就崩。逆向強闖好用，亂跳會災難。
2. **參數暫存器在函式中段已被覆蓋**：`$rdi` 只在**函式入口**保證是第一個參數。函式跑幾條指令後 `rdi` 可能被拿去做別的。要看參數請在 prologue 後、被覆蓋前。
3. **`$eax` 和 `$rax` 混用**：`$eax` 是 `$rax` 低 32 bit。寫 32-bit 值到 `$eax` 會清掉 `$rax` 高 32 bit（x86-64 規則），改值時注意位寬。
4. **AT&T 語法 src/dst 看反**：AT&T `mov %a, %b` 是 a→b（src 在前）。習慣 Intel 的人最常栽這。設 `intel` flavor 省事。
5. **`info proc mappings` 說沒有資訊**：core dump 或遠端 target 可能拿不到 `/proc`。用 `maintenance info sections` / `info files` 替代。
6. **floating point / SIMD 暫存器看不到**：`info registers` 預設只給通用暫存器。`info all-registers` 才含 xmm/ymm（SIMD）、x87（FPU）。

## 進階：再往深一層

- **`info all-registers`**：含 SSE/AVX（xmm0-15、ymm）、FPU（st0-7）暫存器，debug SIMD / 浮點時用。`print $xmm0` 看向量內容。
- **`$eflags` 的逐位元操作**：在 Python（Ch 23）裡解碼 EFLAGS 成可讀旗標，是 gef/pwndbg context 視窗的標準功能、Final Project 的一環。
- **記憶體搜尋**：`find` 指令——`find 0x..., 0x..., 0xdeadbeef` 在某段記憶體找特定值/字串，找 pattern、找 leak、找 magic number。
- **watchpoint 用 debug register**：`$dr0-$dr7` 是 x86 的硬體 debug register，硬體斷點/watchpoint 用它們（Ch 13、Ch 39）。
- **`x/i $pc` vs `disassemble`**：前者看單條、後者看整段。組語級 debug 常配 `display/i $pc` 自動顯示。
- **PIE 與位址**：PIE binary 載入位址隨機（Ch 40），`info proc mappings` 看到的基址每次 run 不同（除非 `set disable-randomization on`）。`$pc - 載入基址` = 檔案內 offset。

## 動手練習

1. 對 `stack_demo.c` 的 `leaf`，`break leaf` 後 `info registers rdi`，確認它是參數 `n`；`finish` 後 `print $rax`，確認是回傳值。
2. `disassemble /s leaf` 看組語與原始碼交錯，找出哪條指令對應 `n * 2`。
3. `display/i $pc` + 連續 `stepi` 走完 `leaf`，觀察 `=>` 箭頭移動與暫存器變化。
4. 寫一個有 `if (a == b)` 的程式，在 `cmp` 後 `info registers eflags` 看 ZF，再 `set $eflags` 翻轉 ZF 改變分支走向。
5. `info proc mappings` 看你的程式記憶體佈局，找出 `[stack]`、`[heap]`、libc 的範圍；確認 `$rsp` 落在 `[stack]` 內。
6. `set disassembly-flavor intel`，重看 `disassemble`，比較 AT&T 與 Intel 語法差異，挑一個寫進 `.gdbinit`。

## 本章重點整理

- x86-64 必記：參數 `rdi/rsi/rdx/rcx/r8/r9`、回傳值 `rax`、`rsp`(stack)、`rbp`(frame)、`rip`(PC)。
- 暫存器即 convenience variable，可 `print` 可 `set`；用架構無關別名 `$pc`/`$sp`。
- EFLAGS 的 ZF/SF/CF/OF 決定條件跳躍；改它能扭轉分支（逆向強闖）。
- `disassemble`（`/s` 配原始碼、`/r` 配 raw bytes）看組語；`=>` 標 `$pc`。
- `info proc mappings` 看記憶體佈局——逆向/pwn 算基址、找區段必用。

## 自我檢核

- [ ] 函式的前三個參數、回傳值分別在哪些暫存器？
- [ ] `$pc`/`$sp` 和 `$rip`/`$rsp` 有什麼關係？為什麼跨架構腳本要用前者？
- [ ] `cmp` 之後 `je` 看哪個旗標？怎麼改它扭轉分支？
- [ ] 拿到一個裸位址 `$rax`，怎麼判斷它落在 stack、heap、還是程式碼段？
- [ ] 為什麼「函式中段 `$rdi` 不一定是第一個參數」？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Registers](https://sourceware.org/gdb/current/onlinedocs/gdb/Registers.html)**
  - **讀哪裡**：`info registers`、`info all-registers`、`$pc`/`$sp`/`$fp` 別名說明。
  - **和本章的關聯**：本章暫存器指令的權威。

- **[GDB Manual: Examining Memory & Searching Memory](https://sourceware.org/gdb/current/onlinedocs/gdb/Memory.html)**
  - **讀哪裡**：`find` 指令、`info proc` 系列。
  - **和本章的關聯**：記憶體搜尋與映射檢視。

### 規格 / 參考

- **[System V AMD64 ABI](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：§3.2.1 Registers（暫存器角色）、§3.2.3 Parameter Passing。
  - **和本章的關聯**：「哪個暫存器放第幾個參數」的權威來源。

- **[Intel 64 SDM Vol.1, Ch 3.4 — EFLAGS](https://www.intel.com/sdm)**
  - **讀哪裡**：EFLAGS 各旗標定義。
  - **注意**：很長，只查 EFLAGS 那節即可。

### 部落格

- **[x86-64 assembly crash course](https://gpfault.net/posts/asm-tut-0.txt.html)** 類入門
  - **為什麼值得讀**：不熟組語的話，補一輪 x86-64 基礎，本章與 Ch 39 會好懂很多。

Part 2 的工具都齊了。用練習 B 把「看穿狀態」的本事綜合起來：當一個資料結構壞掉，靠 print/x/frame 把它還原。

→ [練習 B：資料結構偵探](./practice-b-data-structure-detective.md)
