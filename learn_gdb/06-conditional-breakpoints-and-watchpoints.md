# Ch 6 — 條件斷點、watchpoint、catchpoint

> 目標：讓斷點會思考。條件斷點在符合條件時才停、watchpoint 在變數被改時才停、catchpoint 抓 exception / syscall。

## 條件斷點：`break ... if ...`

```
(gdb) b square if n == 3
```

每次 `square` 被呼叫，GDB 都會 eval `n == 3`，為真才停下。

情境：迴圈裡某次 iteration 出錯：

```c
for (int i = 0; i < 10000; i++) {
    process(arr[i]);     ← 第 7843 次會 crash
}
```

你不想 7843 次 `continue`：

```
(gdb) b process if i == 7843
```

一次就到位。

### 為已經存在的斷點加條件

```
(gdb) b square
Breakpoint 1 ...
(gdb) condition 1 n == 3       # 對 #1 加條件

(gdb) condition 1              # 拿掉條件
```

### 條件可以是任何 C 運算式

```
(gdb) b update_user if user != NULL && user->id == 42
(gdb) b process if strncmp(name, "admin", 5) == 0
```

條件裡可以呼叫函式（這是 inferior call，Ch 3 提過）。慎用 — 每次 breakpoint 檢查都會呼叫一次，可能有副作用。

## ignore count：跳過前 N 次擊中

```
(gdb) b square
(gdb) ignore 1 100        # 前 100 次擊中都不停，第 101 次才停
```

`ignore` 可以跟 `condition` 搭：

```
(gdb) b square if n > 0
(gdb) ignore 1 50        # 前 50 次符合條件的擊中都跳過
```

這招在「每 iteration 都符合條件但只有某次出錯」時很好用。

## Commands：斷點自動執行

讓斷點每次擊中時跑一段 GDB command：

```
(gdb) b square
Breakpoint 1 ...
(gdb) commands 1
> silent
> printf "square called with n=%d\n", n
> continue
> end
```

`silent` 讓 GDB 不印斷點命中訊息。`continue` 讓它自動跑下去。結果：

```
square called with n=1
square called with n=2
square called with n=3
square called with n=4
square called with n=5
```

**這就是土法煉鋼的 tracing。** 不用改 code 就能 log 一個函式的所有呼叫。

用途：

- log 變數隨時間的變化
- 統計某個函式被呼叫幾次
- 產生呼叫序列給另一個工具分析

commands 語法完整版在 Ch 14（GDB command script）會再深入。

## Watchpoint：變數被改時停

```
(gdb) watch variable         # 寫（被改）時停
(gdb) rwatch variable        # 讀時停（需硬體支援）
(gdb) awatch variable        # 讀或寫都停（A = access）
```

最常用情境：「某個變數被改成了奇怪的值，但不知道在哪」。

```c
int global = 0;

void bad_function(void) {
    global = -1;     // ← 想抓這裡
}
```

```
(gdb) start
(gdb) watch global
Hardware watchpoint 2: global
(gdb) continue

Hardware watchpoint 2: global

Old value = 0
New value = -1
bad_function () at bug.c:5
5           global = -1;
```

**注意「Old value / New value」** — GDB 會告訴你改之前跟改之後是什麼。

### Watchpoint 不是斷點

Breakpoint 是「執行到這個**位址**就停」，watchpoint 是「這個**記憶體位址被寫入**就停」。兩者底層完全不同：

- **breakpoint**：改 inferior 記憶體為 `int3`，靠 SIGTRAP。
- **watchpoint**：用 CPU 的 debug registers（x86 DR0–DR3，最多 4 個），或用**軟體模擬**（超慢，慢 100 倍以上）。

```
(gdb) info breakpoints
Num     Type              Disp Enb Address    What
1       breakpoint        keep y   0x11b8     in main
2       hw watchpoint     keep y              global
```

看到 `hw watchpoint` 代表用的是硬體 — 高效。看到 `watchpoint`（沒 hw）代表軟體模擬 — 慢到怕。

### 為什麼可能退化成軟體模擬

- 超過 4 個 watchpoint（x86 硬體上限）
- 要監視的資料超過 8 byte（一個大結構）
- `set can-use-hw-watchpoints 0` 被強制關掉

### watch 的作用域

`watch i`（i 是 local variable）只對這個 frame 有效 — 函式 return 後 watchpoint 會自動消失，因為 local variable 的記憶體位置不再是 `i`。

想監視全域、heap 位址、或一個結構 field，直接用位址或完整 expression：

```
(gdb) watch *(int *)0x7fffffffe140        # 監視這個位址的 4 byte
(gdb) watch g_config.max_connections      # 監視全域結構的 field
```

## Catchpoint：抓特殊事件

**非「停在位址」的斷點**。它們停在「事件發生」時。

### `catch throw` — 抓 C++ exception

```
(gdb) catch throw
(gdb) catch catch         # 抓被 catch 住的
(gdb) catch rethrow
```

```
(gdb) r
Catchpoint 1 (exception thrown), 0x00007ffff7ce1b94 in __cxa_throw () from /lib/x86_64-linux-gnu/libstdc++.so.6
(gdb) bt
#0  __cxa_throw (...)
#1  0x...    throwing_function () at app.cpp:42
...
```

這樣你不用猜 exception 從哪來。

### `catch syscall` — 抓 syscall

```
(gdb) catch syscall open
(gdb) catch syscall 2          # 用 syscall number（Linux: 2 = open）
(gdb) catch syscall                 # 任何 syscall 都停
```

情境：「這個程式為什麼會讀到那個檔案？」下 `catch syscall openat`，一次看到 bt，謎底揭曉。

### `catch fork` / `exec` / `vfork`

```
(gdb) catch fork
(gdb) catch exec
```

抓 process 事件。Ch 9 會細講。

### `catch signal`

```
(gdb) catch signal SIGSEGV
(gdb) catch signal all
```

跟 `handle SIGSEGV stop` 效果類似。Ch 9 會詳談 signal handling。

### `catch load` / `unload`

抓 shared library 載入 / 卸載：

```
(gdb) catch load libfoo.so
```

Ch 20 在談 symbol resolution 時會用。

## 實戰：condition + watchpoint 組合技

情境：`list->count` 被改成了負數，但只發生在某個特定的 `user_id` 流程。

```
(gdb) watch list->count if list->count < 0
```

watchpoint 本身沒直接支援 `if`，但可以這樣曲線救國：

```
(gdb) watch list->count
(gdb) condition 1 list->count < 0      # 把 watchpoint 當作有條件
```

或用 commands：

```
(gdb) watch list->count
(gdb) commands 1
> if list->count >= 0
>   cont
> end
> end
```

## 實戰：count a function's calls

```
(gdb) set $count = 0
(gdb) b square
(gdb) commands 1
> silent
> set $count = $count + 1
> cont
> end
(gdb) c

... 等到程式結束 ...

(gdb) p $count
$1 = 15
```

不用改一行 source code，就知道 `square` 被呼叫 15 次。

## 常見坑

1. **condition 算錯**：條件裡的 `=` 寫成指派而不是 `==`。GDB 不一定會報錯，條件永遠真 / 永遠假。
2. **condition 產生 side effect**：條件裡呼叫的函式改了全域狀態 — 每次斷點檢查就改一次，可能改幾百次。
3. **watchpoint 慢到不能動**：退化成軟體模擬。`info break` 確認是 `hw watchpoint` 才用。
4. **local watchpoint 失效**：函式 return 後自動失效，上下文換了。
5. **C++ exception 抓不到**：你的程式用了 `-fno-exceptions`、或在某個 try/catch 裡被吞掉。
6. **commands 忘記寫 `continue`**：每次擊中都停下來，你會以為斷點沒生效。
7. **rwatch 在很多 CPU 架構上不支援**：x86 硬體 watchpoint 不能純「讀」觸發 — GDB 會退化成軟體模擬或報錯。

## 動手練習

用這個範例 `state.c`：

```c
#include <stdio.h>
#include <string.h>

struct User {
    int id;
    char name[32];
    int balance;
};

static struct User users[100];

void create_user(int id, const char *name, int balance) {
    users[id].id = id;
    strcpy(users[id].name, name);
    users[id].balance = balance;
}

void deduct(int id, int amount) {
    users[id].balance -= amount;
}

int main(void) {
    create_user(1, "alice", 100);
    create_user(2, "bob", 50);
    create_user(3, "eve", 200);

    for (int i = 0; i < 50; i++) {
        deduct(1, 3);
    }

    printf("alice balance = %d\n", users[1].balance);
    return 0;
}
```

1. 條件斷點：在 `deduct` 下斷點，條件是 `id == 1 && amount > 2`。重跑，看停在什麼時候。
2. Commands：在 `deduct` 下 silent 斷點，自動 print `users[id].balance`，然後 continue。看 balance 如何遞減。
3. Watchpoint：在 main 開頭下 `watch users[1].balance`，continue。應該每次 `deduct` 都停一次。
4. 硬體斷點數量上限：下 4 個 watchpoint（monitor 4 個 user 的 balance），再加第 5 個看會發生什麼。
5. Catch syscall：`catch syscall write`，看 printf 最後是呼叫哪個 syscall。

## 自我檢核

- [ ] 我能下條件斷點，並用它跳過前幾千次擊中
- [ ] 我知道 `commands` 可以把斷點變成 tracing tool
- [ ] 我能區分 breakpoint 和 watchpoint 底層原理不同
- [ ] 我知道 watchpoint 有硬體和軟體兩種，軟體的很慢
- [ ] 我會用 `catch throw` 抓 C++ exception
- [ ] 我會用 `catch syscall` 抓系統呼叫

這章結束後你應該已經具備了「抓一隻普通 bug」的能力。Practice A 就是驗收 — 一個經典的 segfault，用到目前學的所有東西。

→ [練習 A：抓一隻經典 segfault](./practice-a-segfault-hunt.md)
