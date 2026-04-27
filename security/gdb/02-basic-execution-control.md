# Ch 2 — 基本執行控制

> 目標：熟練 `run` / `break` / `continue` / `step` / `next` / `finish` / `until`，這七個指令是 80% 的 debug 會用到的東西。

## 範例程式

本章範例：`sample.c`

```c
#include <stdio.h>

int square(int n) {
    return n * n;
}

int sum_of_squares(int n) {
    int total = 0;
    for (int i = 1; i <= n; i++) {
        total += square(i);
    }
    return total;
}

int main(void) {
    int n = 5;
    int result = sum_of_squares(n);
    printf("sum_of_squares(%d) = %d\n", n, result);
    return 0;
}
```

```bash
gcc -g -O0 sample.c -o sample
```

全程我們都開著這隻小程式搞。

## 進入 gdb

```bash
gdb -q ./sample
```

`-q` 是 `--quiet`，省掉開場白。我個人一律用 `-q`，建議你在 `~/.bashrc` 加：

```bash
alias gdb='gdb -q'
```

## `run` — 啟動 inferior

```
(gdb) run
```

如果需要參數或 stdin：

```
(gdb) run arg1 arg2 < input.txt
```

參數會記下來，後面直接 `run` 就會沿用上次的參數。想清掉：

```
(gdb) set args
```

### `start` — 進 `main` 就停

比 `run` 更常用：

```
(gdb) start
Temporary breakpoint 1 at 0x11b8: file sample.c, line 17.
Starting program: /tmp/sample

Temporary breakpoint 1, main () at sample.c:17
17          int n = 5;
```

它做的事等同於「`tbreak main` + `run`」— 下一個**臨時斷點**（tbreak，擊中一次就消失）在 main 的開頭，然後跑起來。

幾乎所有 debug session 都以 `start` 開頭。

## `break` — 下斷點

```
(gdb) break main
(gdb) break sample.c:9
(gdb) break sum_of_squares
(gdb) break 9                    # 當前檔案第 9 行
(gdb) break *0x11b8              # 絕對位址
```

縮寫 `b`。所以常見是：

```
(gdb) b main
(gdb) b sample.c:9
(gdb) b square
```

看所有斷點：

```
(gdb) info breakpoints
Num     Type           Disp Enb Address            What
1       breakpoint     keep y   0x00000000000011b8 in main at sample.c:17
2       breakpoint     keep y   0x000000000000115d in square at sample.c:4
```

刪除：

```
(gdb) delete 2               # 刪 #2
(gdb) delete                 # 全刪
```

暫時停用 / 重新啟用：

```
(gdb) disable 2
(gdb) enable 2
```

### tbreak — 臨時斷點

一次性的 break，擊中就消失：

```
(gdb) tbreak square
```

適合「我只想進去看一下這個函式的第一次呼叫」。

### rbreak — 用 regex 下一堆

```
(gdb) rbreak ^sum_
```

這會對所有名字以 `sum_` 開頭的函式下斷點。大專案找 pattern 很好用。

## `continue` — 放它跑

```
(gdb) continue
```

縮寫 `c`。跑到下一個斷點、signal、或程式結束。

`continue N` 表示「跳過接下來 N 次擊中，第 N+1 次才停」。例如在迴圈裡：

```
(gdb) b 10            # for 迴圈裡
(gdb) run
(gdb) continue 5      # 跳過 5 次，看第 6 次
```

## `step` vs `next` — 進函式或不進

這兩個最容易搞混，一次講清楚。

```
(gdb) next    # 簡寫 n，執行下一行。呼叫函式時「把函式整個當一行跑完」
(gdb) step    # 簡寫 s，執行下一行。呼叫函式時「進入函式，停在函式第一行」
```

圖示化：

```
sum_of_squares 裡：
  for (int i = 1; i <= n; i++) {
      total += square(i);      ← 游標在這
  }

打 next：一步跨過，游標跳到 `}`（下一行）。square 整個執行完。
打 step：進入 square，游標變成 `return n * n;`。
```

### 如果那一行沒有函式呼叫？

兩個行為一樣，都是「執行下一行」。

### step 進不去的狀況

有時候你 `step` 但沒進到目標函式，可能是：

1. **沒 debug info**：例如 libc 裡的 `printf`，step 會跳過去（相當於 next）。想進去要裝 `libc6-dbg`。
2. **inline 函式**：被 inlining 的函式沒有獨立函式呼叫，step 直接跑過。
3. **函式是 `static inline`**：跟上面類似。

想看仔細，用 Ch 8 會教的 `stepi`（逐機器指令）。

## `finish` — 跑到當前函式返回

```
(gdb) finish
```

在函式裡面用，會一口氣跑到這個函式 return 出去，然後停在呼叫點的下一行。

情境：你 `step` 進到一個函式，發現看錯了，想退出來：

```
(gdb) s
square (n=1) at sample.c:4
4           return n * n;
(gdb) finish              ← 馬上 return
Run till exit from #0  square (n=1) at sample.c:4
0x000000000000119c in sum_of_squares (n=5) at sample.c:10
10                  total += square(i);
Value returned is $1 = 1
```

**注意它會告訴你 return value**（`Value returned is $1 = 1`），這招很好用，不用額外 print。

## `until` — 跳出迴圈

```
(gdb) until       # 簡寫 u
```

這個指令有點微妙：

- 在**非迴圈**的程式碼裡，`until` 等同 `next`。
- 在**迴圈**裡，`until` 會執行到「行號 > 當前行號」的位置才停 — 實際效果就是**跳出迴圈**。

例子：

```c
for (int i = 1; i <= n; i++) {    ← line 9
    total += square(i);            ← line 10，游標在這
}                                  ← line 11
return total;                      ← line 12
```

在 line 10 打 `until`，GDB 會跑到 line 12（跳過整個迴圈剩下的 iteration）。

### `until LINE` — 跑到指定行

更常用的變形：

```
(gdb) until 20           # 跑到第 20 行才停
```

這等同於「下一個臨時斷點在第 20 行然後 continue」，少打幾個字。

## 快速 cheat sheet

| 縮寫 | 指令 | 作用 |
|---|---|---|
| `r` | run | 啟動 inferior（從頭） |
| — | start | 啟動並停在 main 開頭 |
| `b` | break | 下斷點 |
| — | tbreak | 一次性斷點 |
| — | rbreak | regex 批次下斷點 |
| `c` | continue | 繼續 |
| `n` | next | 執行下一行（不進函式） |
| `s` | step | 執行下一行（進函式） |
| — | finish | 跑到當前函式 return |
| `u` | until | 跑到下一行 / 指定行 |
| — | info break | 看所有斷點 |
| `d` | delete | 刪斷點 |

## 「上一個指令」

空白指令（直接按 Enter）會重複上一個指令。這讓「step、step、step、step...」變成「`s`、Enter、Enter、Enter...」。debug 時手感差很多。

## Debug session 的典型節奏

實務上一次 debug 的動作大概像這樣：

```
gdb -q ./sample              # 開 gdb
(gdb) start                  # 啟動停在 main
(gdb) b sum_of_squares       # 有疑慮的函式下斷點
(gdb) c                      # 跑到那個函式
(gdb) n                      # 一行一行看
(gdb) n
(gdb) p total                # 印中間變數（Ch 3 會教）
(gdb) s                      # 想進 square 看
(gdb) finish                 # 看完 return 出來
(gdb) c                      # 繼續
```

節奏是「下斷點 → continue 跳到目標 → next/step 細看 → print 驗證」。

## 常見坑

1. **`run` 卡在某個 signal**：例如 SIGPIPE 一丟，inferior 就停了。解法在 Ch 9，先記得 `handle SIGPIPE nostop noprint pass` 可以忽略。
2. **`step` 跳進 libc / 組語一片**：用 `next`。或者 `finish` 出來。
3. **`b main` 回 `No symbol "main" in current context`**：你的 binary 沒 `-g`，或者 strip 過了。
4. **改完 source、rebuild 完，gdb 不知道**：GDB 沒有 auto-reload。退出重進 gdb，或打 `file sample` 讓它重讀 binary。

## 動手練習

1. 在 `sample` 裡，用 `start` 進 main，用 `b square` + `c` 進 square，驗證 `n` 從 1 開始。
2. 用 `next` 跑迴圈一次，感受它不進 `square`。重開一次，改用 `step`，感受它每次都進去。
3. 在迴圈中間打 `until`，看它直接跳出迴圈。
4. 在 square 裡用 `finish`，看它印出 return value。
5. 試試 `rbreak ^sum_`，觀察它對 `sum_of_squares` 一個函式下了斷點。

## 自我檢核

- [ ] 我能說出 `run` 和 `start` 的差別
- [ ] 我能說出 `step` 和 `next` 的差別
- [ ] 我知道 `finish` 會印出 return value
- [ ] 我知道 `until` 在迴圈裡的特殊行為
- [ ] 我知道 `tbreak` 和 `rbreak` 各自的用途

下一章我們看「停下來之後」能做什麼 — 印變數、改變數、看型別。

→ [Ch 3 看資料：print / display / ptype](./03-inspecting-data.md)
