# Ch 21 — Frame unwinding 與 inferior call

> 目標：搞懂 `bt` 背後的 frame unwinding 演算法（DWARF CFI）、以及 `p foo()` 怎麼在 tracee 裡呼叫一個函式而不搞壞它。這是 GDB 最精妙的兩個技術之一。

## Frame unwinding 的問題

你在 `bt` 時，GDB 需要從當前 RIP + RSP 開始，一路往回算出每個 caller 的 RIP、RSP、RBP... 最後到 `main`。

這看似簡單 — frame pointer（RBP）串起來就行：

```
current frame:  [local vars][saved RBP][return addr]
                                ▲
                                └── point to caller's saved RBP
```

但現代編譯器預設 **omit frame pointer**（`-fomit-frame-pointer`）— RBP 被當普通暫存器用，不再是 frame chain。

沒 RBP 怎麼 unwind？答案：**DWARF Call Frame Information (CFI)**，存在 `.eh_frame` 或 `.debug_frame` 段。

## DWARF CFI：另一段 bytecode

CFI 是一個表：對每個 PC，記下「怎麼從當前 register state 算出 caller 的 register state」。

表格長這樣（邏輯上）：

```
PC         CFA            RBX     RBP     RIP
0x1149     rsp+8          ?       ?       [cfa-8]         ; 函式第一條指令前，尚未 push
0x114a     rsp+16         [cfa-16] ?       [cfa-8]         ; push %rbx 之後
0x1150     rsp+16         [cfa-16] [cfa-24] [cfa-8]        ; 保存 rbp 之後
0x1180     rsp+16         [cfa-16] [cfa-24] [cfa-8]        ; 函式主體
0x11a0     rsp+8          ?       ?       [cfa-8]         ; epilogue 後
```

**CFA**（Canonical Frame Address）：這個 frame 被呼叫前 RSP 的值。其他 register 怎麼從 CFA 反算 — 這就是 unwinding。

實際上 CFI 不是一個展開的 table，而是一段 **bytecode program**（像 `.debug_line` 一樣）— 用 "從上一個狀態如何變化" 壓縮表示。GDB 要跑這個 interpreter 才能重建 table。

## `.eh_frame` vs `.debug_frame`

兩個格式幾乎一樣，差別：

| | `.debug_frame` | `.eh_frame` |
|---|---|---|
| 用途 | debugger unwind | C++ exception handling + debugger unwind |
| strip 會不會被移除 | 會 | **不會**（runtime 需要） |
| 位置 | loadable | loadable |
| 編碼細節 | 簡單 | 多了 augmentation data |

**關鍵：strip 後 `.debug_frame` 沒了，但 `.eh_frame` 還在**，因為 C++ exception / libunwind 都需要它。所以 GDB 在 strip binary 上還是能做基本 unwinding。

看一下：

```bash
readelf --debug-dump=frames hello | head -30
```

會看到 CIE（Common Information Entry）與 FDE（Frame Description Entry）。CIE 是共用的前綴資訊、FDE 是個別函式的 unwind info。

## Unwinding 演算法

```
current_regs = 當前 register state

while 1:
    pc = current_regs.rip
    cfi = lookup_cfi_for_pc(pc)                   # 查 FDE
    cfa = eval(cfi.cfa_rule, current_regs)        # CFA 的計算規則

    caller_regs = {}
    for reg in TRACKED_REGISTERS:
        if cfi.has_rule_for(reg):
            caller_regs[reg] = eval(cfi.rule[reg], current_regs, cfa)
        else:
            caller_regs[reg] = current_regs[reg]  # 預設 caller 繼承

    # caller's RSP = CFA (定義)
    caller_regs.rsp = cfa
    # caller's RIP 是 return address（通常 cfi.rule[rip] 指定從 cfa-8 讀）

    emit_frame(caller_regs)

    if caller_regs.rip == 0 or no more FDE:
        break

    current_regs = caller_regs
```

這就是 `bt` 內部執行的 pseudo-code（GDB 實際上有更多優化與 fallback）。

## Fallback：用 RBP 猜

當沒有 CFI（舊 binary、手寫 asm、或 CFI corrupted），GDB fallback 到「RBP chain」：

```
while rbp != 0:
    return_addr = read_word(rbp + 8)
    prev_rbp = read_word(rbp)
    emit_frame(rip=return_addr)
    rbp = prev_rbp
```

這就是為什麼 `-fno-omit-frame-pointer` 在 stack 壞掉時仍能印 bt。Ch 5 提過。

## 實戰：手動 unwind

假設 crash，`bt` 壞掉。你想看 stack：

```
(gdb) info registers rsp rbp rip
rsp            0x7fffffffdfa0
rbp            0x7fffffffdfc0
rip            0xdead                 ; 不知道是什麼

(gdb) x/16gx $rbp - 0x10
0x7fffffffdfb0:  0x...      0xbadbadbadbad   ; saved rbp 壞了
0x7fffffffdfc0:  0x...      0x00005555...    ; 可能是 return address

(gdb) x/i 0x00005555...     ; 驗證那是不是個指令位址
   0x5555...: mov   ...
```

手工推論 caller。Ch 13 練習 C 挑戰 A 就是這個。

## Inlined function 的 unwinding

`-O2` 下 compiler 會把小函式 inline 到 caller 裡。source 層的「函式呼叫」在 asm 層不存在（沒有真的 `call` 指令）。

但 DWARF 仍記錄 inline 關係：`DW_TAG_inlined_subroutine` 標記「這段 PC 範圍是 foo 的 inline 展開、call 來自 bar 的 line 42」。

GDB 在 bt 印出時會把 inline 展開成虛擬 frame：

```
#0  some_inlined at source.c:30
#1  caller at source.c:42
```

雖然 asm 層只有一層，bt 印成兩層（讓邏輯層面上對齊）。

## Inferior function call（IFC）

現在另一個魔法：`p foo()` 或 `call foo()` 時，GDB 在 tracee 的 context 裡「跑」一個函式，最後回到你下指令的地方，tracee 以為什麼都沒發生。

### 實作

```
1. 保存當前 registers（GETREGS）
2. 保存當前要用的 stack 範圍（萬一要還原）
3. 安排呼叫：
   - 寫參數到對應 register（ABI）
   - 在 stack 上準備一個「假的 return address」— 通常是某個「我們知道的 breakpoint」位址
   - 把 RIP 設成 foo 的位址
4. CONT 讓 tracee 跑
5. foo 執行完，ret 到那個 fake return address（= 我們設的 breakpoint）
6. 觸發 SIGTRAP，GDB 拿回控制
7. 讀 RAX 當 return value
8. 還原 registers、stack，tracee 回到原本被打斷的位置
```

### 風險

- **foo 可能不 return**：無窮 loop 或自己 exit() 了
- **foo 改了 global state**：那就真的改了，undo 不了
- **foo 觸發 signal**：SIGSEGV 等會打斷，GDB 要處理
- **線程不安全**：同時另一個 thread 在跑 foo，可能 race

GDB 把這些包得還算好。但你用 `p strcpy(...)` 這種有 memory 副作用的東西時要心裡有數。

### 為什麼 `<optimized out>` 不能呼叫方法

C++ `p this->get()`，如果 `this` 是 `<optimized out>`，GDB 沒法填 `rdi`，IFC 失敗。這不是 IFC 的 bug，是 DWARF location 資訊不足。

## 跳出當前函式：`return` 的實作

Ch 5 提過 `return` 命令。它其實做：

```
1. 查 CFI，算 caller 的 rsp 和 rip
2. 把 tracee 的 rsp, rbp, rip 改成 caller 的值
3. 如果指定了 return value，把 rax 改成那個值
4. 繼續執行
```

和 IFC 反向 — IFC 是 "進入一個我們指定的函式"；return 是 "跳出當前函式"。

## 在實務 debug 中的用處

- **用 `call` 觸發某段 code**：`call reload_config()` 測試不同 config path。
- **用 IFC 呼叫 printf**：debug 時 inspect 狀態。但要注意 stdio buffer、reentrancy 問題。
- **在 watchpoint / breakpoint commands 裡 call custom logger**：自訂 trace workflow。

## 常見坑

1. **`bt` 印一半變 `??`**：CFI 斷了（可能是 JIT code、inline asm、或損壞的 stack）。GDB 就停不了繼續往上。
2. **`call fn()` hang**：fn 跑了但沒回來。Ctrl-C 中斷，tracee 會停在 fn 裡面的某處。可能需要手動 return 或 kill。
3. **`call fn()` 後程式行為變怪**：fn 有副作用，你剛剛真的執行了它。
4. **IFC 在 multi-thread 程式危險**：同時另一個 thread 持有 mutex、你的 IFC 想拿同個 mutex → deadlock。
5. **`-fno-asynchronous-unwind-tables`**：有些舊編譯選項會去掉 `.eh_frame`。會讓 unwinding 完全失敗。別用。
6. **JIT 產生的 code 沒有 CFI**：V8、HotSpot 等 JIT 跑的函式 bt 會斷。這些 runtime 通常有自己的 gdb plugin 解決。

## 動手練習

### 練習一：CFI 觀察

```bash
gcc -g -O0 hello.c -o hello
readelf --debug-dump=frames hello > frames.txt
less frames.txt
```

找一個 FDE，嘗試解讀「CIE augmentation、initial location、address range」。

### 練習二：`-fno-omit-frame-pointer` vs default

```bash
gcc -g -O2 -fomit-frame-pointer hello.c -o hello_noframe
gcc -g -O2 -fno-omit-frame-pointer hello.c -o hello_frame

gdb -q ./hello_noframe
(gdb) b square
(gdb) r
(gdb) bt full            ; 兩個都會正常（有 .eh_frame）

# 壞 stack 測試 — 手工改 rbp 到 0
(gdb) set $rbp = 0
(gdb) bt                 ; 看哪個還能印
```

### 練習三：Inferior call

```
(gdb) start
(gdb) call square(7)
(gdb) p $?
```

然後玩一個有副作用的：

```c
static int counter = 0;
int bump(void) { return ++counter; }
```

```
(gdb) call bump()
$1 = 1
(gdb) call bump()
$2 = 2
(gdb) p counter
$3 = 2                   ← 真的被改了
```

### 練習四：手動 unwinding

寫個 stack corruption 範例：

```c
void corrupt(void) {
    char buf[8];
    memset(buf, 0x41, 100);     // overflow
}
int main(void) { corrupt(); return 0; }
```

```
(gdb) r
Program received signal SIGSEGV, ...

(gdb) bt
#0  0x4141414141414141 in ?? ()     ← RIP 被寫壞了
#1  ?? from ??

(gdb) info registers rsp rbp
(gdb) x/20gx $rsp
... 看一堆 0x41 ...
```

try：`set $rip = <某個合理位址>` + `bt` 看能不能推出 corruption 前的 state。

## 自我檢核

- [ ] 我能解釋 frame unwinding 的高層概念：給當前 register state，用 CFI 算出 caller 的 state
- [ ] 我知道 `.eh_frame` 跟 `.debug_frame` 差別（後者 strip 後會不見）
- [ ] 我能說出沒 CFI 時，GDB fallback 到 frame pointer chain 的機制
- [ ] 我知道 inlined function 的 bt 是「虛擬 frame」
- [ ] 我知道 inferior function call 的步驟：save → 安排 stack → CONT → trap → restore
- [ ] 我知道 IFC 的 risk（副作用、deadlock、signal）

Part 6 結束。你現在不只會用 GDB，你懂它。最後 Final Project：把 ptrace + DWARF + breakpoint 實作的觀念合起來，從零寫一個 minidbg。

→ [Final Project：minidbg（ptrace + DWARF 版）](./final-project-minidbg.md)
