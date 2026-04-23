# Ch 5 — Stack 與 frame

> 目標：熟練 `backtrace` / `frame` / `up` / `down` / `info locals` / `info args`，能在呼叫鏈中上下跳躍、看每一層的 local variable 與參數。

## Stack frame 是什麼

每次函式被呼叫，CPU 會在 stack 上配一塊 **frame**，放這個函式的 local variable、參數、saved registers、return address。

```
高位址
┌──────────────────┐
│ main 的 frame    │  最早被 push
│  - local vars    │
│  - saved regs    │
├──────────────────┤
│ sum_of_squares   │  main 呼叫進來
│  - n, i, total   │
│  - saved regs    │
├──────────────────┤
│ square           │  sum_of_squares 呼叫進來
│  - n             │
│  - return addr   │
├──────────────────┤ ← RSP（stack pointer）
低位址
```

x86 stack **往低位址長**。RSP 指向最低的那個 byte，也就是最新 frame 的頂端。RBP 是 frame pointer（若有啟用），指向當前 frame 的底。

當 `square` return：RSP 往高位址彈回 `sum_of_squares` 的 frame，彈出 return address 放進 RIP，接著跑。

GDB 的 backtrace / frame 指令就是在這個結構上操作。

## `backtrace` — 看呼叫鏈

```
(gdb) b square
(gdb) r
...
Breakpoint 1, square (n=1) at sample.c:4
4           return n * n;

(gdb) backtrace
#0  square (n=1) at sample.c:4
#1  0x000000000000119c in sum_of_squares (n=5) at sample.c:10
#2  0x00000000000011da in main () at sample.c:18
```

縮寫 `bt`。讀法：

- **frame #0 是最內層**（目前停下的地方）
- frame 編號往上遞增，**最大編號是最外層**（通常是 main、或更外面的 `__libc_start_main`）

選項：

```
(gdb) bt 3            # 只印最內 3 層
(gdb) bt -3           # 只印最外 3 層
(gdb) bt full         # 每個 frame 都順便印 local variable
(gdb) bt no-filters   # 繞過 Python frame filters（Ch 16）
```

`bt full` 在 debug 複雜呼叫鏈時是神器 — 一次看清每層的狀態。

## `frame N` — 切換 frame

預設你停在 #0。切到上層看：

```
(gdb) frame 1
#1  0x000000000000119c in sum_of_squares (n=5) at sample.c:10
10                  total += square(i);

(gdb) p i
$1 = 1

(gdb) p total
$2 = 0
```

**切換 frame 不影響 inferior 的執行狀態**，只是改變「gdb 目前看事情的視角」。`$pc`、`$rbp` 等會跟著變。

`frame`（不加編號）印當前 frame。

## `up` / `down`

```
(gdb) up             # 跳到上一層（frame 編號 +1）
(gdb) up 2           # 跳 2 層
(gdb) down           # 跳下一層（frame 編號 -1）
```

比 `frame N` 常用，不用算編號。

## `info locals` / `info args`

看當前 frame 的 local 與參數：

```
(gdb) frame 1
(gdb) info args
n = 5

(gdb) info locals
total = 0
i = 1
```

`info args` 常常能讓你一眼發現「哦這個 caller 傳錯了」。

## `info frame` — 當前 frame 的元資料

```
(gdb) info frame
Stack level 1, frame at 0x7fffffffdfa0:
 rip = 0x119c in sum_of_squares (sample.c:10); saved rip = 0x11da
 called by frame at 0x7fffffffdfb0, caller of frame at 0x7fffffffdf80
 source language c.
 Arglist at 0x7fffffffdf90, args: n=5
 Locals at 0x7fffffffdf90, Previous frame's sp is 0x7fffffffdfa0
 Saved registers:
  rbx at 0x7fffffffdf88, rbp at 0x7fffffffdf90, rip at 0x7fffffffdf98
```

看得到：當前 frame 的位址、saved registers 存哪、caller 是誰、return 位址。這是 Ch 21 做 frame unwinding 會用到的資訊。

## 實際情境：segfault 用 bt 定位

segfault 常常在一個跟你「寫 bug 的地方」很遠的地方炸。bt 能告訴你怎麼走到那裡。

```c
void dereference(int *p) {
    printf("%d\n", *p);
}

void caller(int *p) {
    dereference(p);
}

int main(void) {
    caller(NULL);
    return 0;
}
```

```
(gdb) r
Program received signal SIGSEGV, Segmentation fault.
0x00000000000011b9 in dereference (p=0x0) at bug.c:4
4           printf("%d\n", *p);

(gdb) bt
#0  0x00000000000011b9 in dereference (p=0x0) at bug.c:4
#1  0x00000000000011d7 in caller (p=0x0) at bug.c:8
#2  0x00000000000011ec in main () at bug.c:12
```

「啊，`dereference` 的 `p` 是 NULL。往上看，caller 也收到 NULL。再往上，main 傳了 NULL。bug 在 main。」

這個「沿 bt 往上找」的動作是 debug 的基本功。

## 呼叫鏈被破壞

有時候 `bt` 印出奇怪的東西：

```
(gdb) bt
#0  0x000000000000dead in ?? ()
#1  0x00000000000011ec in main ()
#2  Cannot access memory at address 0x0
```

`??` 表示 GDB 找不到 symbol，`0x0` 代表 frame 的 saved rbp 被寫成 0 或類似。可能原因：

- **stack overflow**：函式 return address 被蓋掉
- **stack buffer overflow**：你的 strcpy 寫爆了 local buffer
- **call through function pointer 到無效位址**
- **ROP / exploit 成功改寫 stack**

bt 壞掉本身就是個強訊號：你的 stack 被搞了。

## frame pointer 與 `-fno-omit-frame-pointer`

x86_64 上，現代 compiler 為了效能常常**不存 RBP** 當 frame pointer（`-fomit-frame-pointer`，這幾乎是預設）。沒有 RBP 的話，frame 之間只能靠 DWARF 的 CFI（call frame info）來 unwind。

絕大多數情況下 DWARF unwinding 就夠用，`bt` 一樣能印。但：

- **stack 破壞嚴重時**，DWARF CFI 也會算錯 — 這時候若有 RBP 可以「瞎猜」至少印一點東西。
- **某些動態 perf tool（perf、eBPF）** 依賴 RBP 來 unwind，需要 `-fno-omit-frame-pointer`。

debug 時建議開：

```bash
gcc -g -O0 -fno-omit-frame-pointer sample.c -o sample
```

`-O0` 其實預設會保留 RBP，但加上這個 flag 保險。

Ch 21 會徹底拆 unwinding 怎麼做。

## `return` 指令 — 強制從當前函式返回

```
(gdb) return
Make sum_of_squares return now? (y or n) y
```

不執行 `sum_of_squares` 剩下的 code，直接彈 frame 返回 caller。

```
(gdb) return 999
```

返回並設 return value 為 999。

用途：跳過某個你不想看的函式、模擬 early return 測試 caller 行為。

## frame filter 預覽

大型 C++ 專案的 bt 常常因為 lambda、template、std 實作層疊而爆長：

```
#0  std::__detail::__variant::__raw_visit<...>(...) at variant:1345
#1  std::visit<...>(...) at variant:1789
#2  some_namespace::detail::_Wrapper<...>::call(...) at wrapper.hpp:42
#3  ...
```

Ch 16 會教 Python frame filter 把這些雜訊壓掉，讓 bt 只顯示你關心的幾層。

## 常見坑

1. **`up` 會 segfault**：通常意味 saved registers 的位址算錯 — 可能 inferior 的 stack 已經被寫壞。
2. **`info locals` 看不到預期的變數**：在 `-O0` 下應該不會，`-O2` 會看到一堆 `<optimized out>`。
3. **frame 編號不穩定**：每次中斷可能略有不同（特別在 inlining 情境下）。用函式名或行號做判斷比編號保險。
4. **bt 只看得到一層**：inferior 剛 crash 在函式 prologue 還沒完，saved rbp 還沒存進 stack，GDB 無法 unwind。這種情況 `x/20gx $rsp` 硬看更實在。

## 動手練習

用這個範例 `deep.c`：

```c
#include <stdio.h>

int level_3(int x) {
    int y = x * 2;
    return y + 1;
}

int level_2(int x) {
    int a = x + 10;
    int b = level_3(a);
    return b;
}

int level_1(int x) {
    int z = x - 1;
    return level_2(z);
}

int main(void) {
    int result = level_1(100);
    printf("%d\n", result);
    return 0;
}
```

1. 在 `level_3` 下斷點，跑起來。
2. `bt` 看完整呼叫鏈。
3. `frame 2` 切到 `level_1`，`info args`、`info locals` 看當時狀態。
4. `up`、`down` 幾次，感受切換。
5. `bt full` 一口氣看所有 frame 的狀態。
6. 在 `level_3` 裡 `return 999`，看 main 收到的 `result` 是 `999`（不是原本的 223）。

## 自我檢核

- [ ] 我知道 frame #0 是最內層、最大編號是最外層
- [ ] 我能用 `frame N`、`up`、`down` 切換視角
- [ ] 我能用 `info locals` / `info args` 看 frame 狀態
- [ ] 我知道 `bt full` 跟 `bt` 的差別
- [ ] 我知道 frame pointer / DWARF CFI 是做 unwinding 的兩種手段
- [ ] 我能用 `return` 強制退出函式

下一章進入斷點的進階世界 — 條件斷點、watchpoint、catchpoint。讓斷點「會思考」。

→ [Ch 6 條件斷點、watchpoint、catchpoint](./06-conditional-breakpoints-and-watchpoints.md)
