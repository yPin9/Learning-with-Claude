# Ch 10 — Reverse debugging

> 目標：理解 `record` / `reverse-*` 的原理與限制，能在 GDB 裡讓程式**倒著跑** — 回到 crash 之前的狀態、往上找出 bug 的源頭。

## 為什麼要倒著 debug？

經典情境：

```
(gdb) r
... 跑跑跑 ...
Program received signal SIGSEGV, Segmentation fault.
0x... in bad_function () at bug.c:42
(gdb) bt
#0  bad_function at bug.c:42
#1  caller1 at bug.c:80
#2  caller2 at bug.c:110
#3  main at bug.c:150
```

你想知道「crash 之前那個指標是什麼時候變 NULL 的？」傳統做法：

- watchpoint + 重跑：但如果 bug 不穩定、或依賴外部狀態，不一定重現
- print 大法 + 重跑：改 code，感染測試環境
- 腦補 + bt 回推：慢

**reverse debugging 讓你直接在同一個 session 裡「倒轉時間」。** 不用重跑、不用猜。

## `record` — 開始錄製

```
(gdb) record               ; 開始錄製（預設 full execution log）
(gdb) record full          ; 同上，明確寫出
(gdb) record btrace         ; 用 CPU 的 branch trace hardware（Intel BTS/PT）
(gdb) record stop          ; 停止錄製
```

預設 `record full` 是**軟體錄製**：GDB 在每條指令後記下所有可能改變的暫存器和記憶體。精確但**慢 100–1000 倍**。

`record btrace` 用 Intel 的 Branch Trace Store（BTS）或 Processor Trace（PT），硬體紀錄分支，幾乎零額外 overhead。但只記控制流，不記資料 — 功能受限。

**實務上一般選 `record full`** 除非你有 Intel PT 硬體。

### 何時開始錄？

```
(gdb) start
(gdb) record                  ; 從這裡開始錄，之前的不錄
(gdb) continue
```

關鍵：**不要一開始就 record**，inferior 跑前 1000 萬條指令你不關心的。`start` 停在 main，找到懷疑區間前下個斷點，到那裡再 `record`。

## Reverse 指令

錄製中任何時候，都可以倒放：

```
(gdb) reverse-continue       ; 簡寫 rc，倒著跑直到斷點（或起點）
(gdb) reverse-step           ; rs，倒一步（進函式）
(gdb) reverse-next           ; rn，倒一步（不進函式）
(gdb) reverse-finish         ; 倒回到「當前函式被呼叫之前」
(gdb) reverse-stepi          ; 倒一條機器指令
(gdb) reverse-nexti
```

還有 `set exec-direction reverse` 讓之後所有的 step/next 都變成反向，直到你 `set exec-direction forward`。

## 殺手級組合：reverse + watchpoint

這是 reverse debugging 最強的用法：

```
(gdb) r
... 跑到 crash ...
Program received signal SIGSEGV, Segmentation fault.
0x... in bad_function (p=0x0) at bug.c:42

(gdb) bt
#0  bad_function (p=0x0) at bug.c:42
#1  caller at bug.c:80

(gdb) watch -l ptr            ; watch ptr 被寫入（-l 用 location，而非 expression）
Hardware watchpoint 2: ...

(gdb) reverse-continue        ; 倒著跑，看誰寫壞 ptr

Hardware watchpoint 2: ...
Old value = 0x7ffff...
New value = 0x0
0x... in some_function () at bug.c:30     ← 就是這裡！
```

**從「crash 現場」倒著跑到「寫壞 ptr 的那一瞬間」**。不用猜、不用重跑、不用改 code。

## 錄製的限制

GDB `record full` 有個嚴重限制：**無法錄製會跟外部交互的指令**。

不能錄：

- **system call**：read/write/mmap/fork 等 — GDB 不能回放 kernel 狀態
- **異步 signal**：錄的時序跟回放可能對不上
- **某些硬體指令**：RDTSC、CPUID 等

碰到這些 GDB 會停下：

```
Process record does not support instruction 0x??? at address 0x...
```

解法：`record stop` 結束錄製、繼續往前跑到安全區域、再 `record` 重啟。但你會失去前面的歷史。

實務上，reverse debugging 最適合「大部分計算都在 user space、少量 I/O」的程式 — compiler、interpreter、純算法、小 utility。網路 server 與多執行緒程式不太適合。

## 錄了多少？

```
(gdb) info record
Active process record target.
Record mode:
Lowest recorded instruction number is 1.
Highest recorded instruction number is 52813.
Log contains 52813 instructions.
Max logged instructions is 200000.
```

預設上限 200,000 條指令（超過了會覆蓋舊的）。改：

```
(gdb) set record full insn-number-max 10000000
```

或無限：

```
(gdb) set record full insn-number-max unlimited       ; 慎用，記憶體會爆
```

## 儲存 / 讀取 recording

```
(gdb) record save /tmp/session.record
(gdb) record restore /tmp/session.record
```

可以在不同 gdb session 之間傳遞錄製紀錄。但要求 binary 完全一致。

## rr — reverse debugging 的專業工具

Mozilla 開發的 `rr`（<https://rr-project.org/>）是 reverse debugging 的**現代王者**。它：

- 錄製時 overhead 比 `record full` 小 10–50 倍
- 能處理 syscall、非同步事件
- 跟 gdb 無縫整合（`rr replay` 打開一個 gdb session）

安裝：

```bash
sudo apt install rr
```

使用：

```bash
rr record ./sample              ; 錄一次執行
rr replay                       ; 在 gdb 裡回放
(gdb) reverse-continue          ; reverse 指令全部可用
```

**實戰中我們多半用 rr，而不是 GDB 內建的 `record`。** 但原理一樣、指令一樣，所以 GDB 內建的要先懂。

### rr 的缺點

- 只支援 Linux x86_64（ARM 實驗性）
- 需要 CPU 支援 Intel performance counters（多數近代 CPU 都有）
- 對 timing 敏感的 bug 可能不重現（但它內建 chaos mode 幫你找）

## Reverse debugging 的心智模型

可以把 reverse 想成「GDB 在每條指令執行前存了 snapshot，回放時就恢復到舊 snapshot」。實際上 `record full` 是這種行為的極端版（每條指令後存 diff）。

這也解釋了：

- **為什麼慢**：每條指令都要記錄
- **為什麼不能錄 syscall**：kernel 狀態沒 snapshot
- **為什麼 `reverse-step` 還是執行完整 step**：它重放的是存下來的 diff，不是真的倒著執行 CPU

rr 用更聰明的方法：它只紀錄**非確定性輸入**（syscall 結果、signal、硬體亂數），其他讓 CPU 自己重跑。這樣空間 / 時間 overhead 都小很多。

## 一個真實故事

假設某天你遇到這個 bug：

```c
// 某個大函式裡面
char *msg = get_message(user);     // ← 回傳 NULL 有時發生
// ... 50 行中間 ...
process(msg);                       // ← NULL deref
```

沒 reverse debug 的世界：
1. crash 在 `process(msg)`，`msg = NULL`
2. 你要猜：`get_message` 為什麼回 NULL？
3. 重跑、在 `get_message` 裡下斷點，希望能重現
4. 如果 bug 依賴 user 狀態，可能要花 1 小時設定 reproduction
5. 最後才找到「哦 user 是 blocked，get_message 回 NULL」

有 reverse debug：
1. crash 現場：`msg = NULL`
2. `watch msg`、`reverse-continue`
3. 一秒內停在 `msg = get_message(user)`
4. `print user` 看 user 當下狀態，案子破了

## 常見坑

1. **`record` 開始後 GDB 慢到不能動**：`record full` 確實慢，降低 `insn-number-max` 或只錄 suspect 區段。
2. **`Process record does not support instruction ...`**：遇到 syscall 了。大部分情況下唯一解是改用 rr。
3. **`reverse-*` 指令回報 `Not in reverse execution direction`**：沒啟動 record，或 record 已結束。
4. **rr record 報 `/proc/sys/kernel/perf_event_paranoid` too high**：按指示 `sudo sysctl kernel.perf_event_paranoid=1`。
5. **多執行緒錄製失敗**：`record full` 多執行緒支援不完整，用 rr。
6. **反覆 reverse/forward 亂掉**：某些版本 gdb 的 state 會錯亂，退出重錄乾淨。

## 動手練習

### 練習一：基本 reverse-step

```c
// reverse_demo.c
#include <stdio.h>

int x = 0;
int y = 0;

int main(void) {
    x = 1;
    y = 2;
    x = 3;
    y = 4;
    printf("x=%d y=%d\n", x, y);
    return 0;
}
```

```
gcc -g reverse_demo.c -o rd
gdb -q ./rd
(gdb) b main
(gdb) r
(gdb) record
(gdb) n   ; x = 1
(gdb) n   ; y = 2
(gdb) n   ; x = 3
(gdb) n   ; y = 4
(gdb) p x    → 3
(gdb) p y    → 4
(gdb) reverse-next   ; 倒一步
(gdb) p y    → 2 ??? 等等
(gdb) p x    → 3
(gdb) rn     ; x = 3 退回 x = 1
(gdb) p x    → 1
```

### 練習二：reverse + watchpoint

改範例讓 `y` 在某步被不小心改：

```c
int main(void) {
    int x = 1;
    int y = 100;
    char buf[8];
    strcpy(buf, "toolong!toolong");   // overflow 寫到 y
    printf("y = %d\n", y);
    return 0;
}
```

```
(gdb) b main
(gdb) r
(gdb) record
(gdb) watch y
(gdb) c
   ... watchpoint 觸發 ...
```

或者先讓它跑到印 y 的地方，觀察 y 不是 100：

```
(gdb) watch y
(gdb) reverse-continue
   → 停在 strcpy
```

### 練習三：用 rr

```bash
rr record ./rd
rr replay
(gdb) b main
(gdb) c
(gdb) reverse-continue
```

感受 rr 比 gdb 內建 record 快很多。

## 自我檢核

- [ ] 我知道 `record full` 跟 `record btrace` 的差別
- [ ] 我能用 `reverse-continue` 配合 watchpoint 找變數被改壞的時間點
- [ ] 我知道 `record full` 不能錄 syscall，rr 可以
- [ ] 我會用 `rr record` + `rr replay` 做 reverse debug
- [ ] 我理解 reverse debugging 改變了「只能往前猜」的 debug 哲學

到這裡，Part 3 結束。練習 B 把這些進階工具跟 valgrind 搭起來，抓一個真實的 heap corruption。

→ [練習 B：debug heap corruption（配合 valgrind）](./practice-b-heap-corruption.md)
