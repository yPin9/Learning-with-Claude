# Ch 5 — Stepping 全家

> **目標**：徹底搞懂 GDB 的「一步一步走」家族——`step` / `next` / `stepi` / `nexti` / `until` / `finish` / `advance` / `return`，包括它們的差異、底層怎麼做到，以及什麼時候該用哪個。學完你不會再亂按 `s` 然後一頭栽進 `printf` 的內部。

> **環境**：GDB 13/14，Linux x86_64，範例用 `gcc -g -O0`。

## 為什麼 stepping 值得講細

「不就 `s` 跟 `n` 嗎？」——這兩個字打錯一個，你就從「在自己的程式裡慢慢走」變成「跌進 libc 的 `malloc` 內部出不來」。stepping 是 debug 時你**最高頻**的動作，每天按幾百次。把它的語意搞精準，省下的時間是以小時計的。

而且 stepping 的底層（source-level step 怎麼用 instruction-level step + DWARF line table 拼出來）是理解 Ch 38（DWARF）與 Ch 41（mini debugger）的關鍵伏筆。

## 先建立直覺：兩個維度

stepping 指令可以用兩個維度來分類，記住這張表就不會混：

```
                 │ 進入函式 (step in)  │ 跨過函式 (step over)
   ──────────────┼─────────────────────┼──────────────────────
   原始碼行為單位 │      step  (s)      │      next  (n)
   機器指令為單位 │      stepi (si)     │      nexti (ni)
```

- **垂直軸**：你走一步是「一行原始碼」還是「一條機器指令」。
- **水平軸**：碰到函式呼叫時，是「鑽進去」還是「把它當一步跨過」。

光是搞懂這個 2×2，你就解決了 90% 的 stepping 困惑。

## `step` vs `next`：進去還是跨過

```c
// step_demo.c
#include <stdio.h>
int square(int n) { return n * n; }       // line 2
int main(void) {
    int x = 5;                            // line 4
    int y = square(x);                    // line 5  ← 這行有函式呼叫
    printf("%d\n", y);                    // line 6
    return 0;                             // line 7
}
```

```
$ gcc -g -O0 step_demo.c -o step_demo && gdb -q ./step_demo
(gdb) break main
(gdb) run
(gdb) next            # 執行 line 4 (int x=5)，停在 line 5
5           int y = square(x);
(gdb) step            # line 5 有呼叫 → step「鑽進」square
square (n=5) at step_demo.c:2
2       int square(int n) { return n * n; }
```

如果在 line 5 用 `next` 而不是 `step`：

```
(gdb) next            # 把 square(x) 整個當一步「跨過」，停在 line 6
6           printf("%d\n", y);
```

口訣：**`step` = step into（進去），`next` = step over（跨過）。** 想看函式內部用 `step`，當它是黑盒用 `next`。

## `step` 的聰明之處：不會踩進沒符號的函式

一個常被誤解的點：`step` 碰到**沒有 debug info 的函式**（如 `printf`、`malloc`）時，**不會**鑽進去——它會自動當成 `next` 跨過。

```
(gdb) step           # 在 printf 那行 step
6           printf("%d\n", y);
(gdb) step           # 不會進 printf 內部！直接到下一行
7           return 0;
```

為什麼？因為 `step` 的定義是「走到**下一行有原始碼資訊的程式碼**」。`printf` 在 libc 裡沒有 DWARF（除非你裝了 libc debug info / debuginfod），所以 GDB 沒地方「停在某一行」，乾脆跳過整個呼叫。

> 這也是為什麼「step 一直跳進奇怪的地方」通常發生在你**有**裝 libc debug info 的時候——GDB 突然有 libc 的 line table 了，就真的鑽進去。要避免：用 `next`，或 Ch 6 的 `skip` 把某些檔案/函式標記為「step 時跳過」。

## `stepi` / `nexti`：指令級

當你在組語層工作（無原始碼、看最佳化過的程式、或 debug 一行內的多個運算），用指令級：

```
(gdb) stepi          # 執行一條機器指令（會進入 call）
(gdb) si             # 同上簡寫
(gdb) nexti          # 執行一條指令，但 call 當一步跨過
(gdb) x/i $pc        # 看下一條要執行的指令是什麼
```

搭配 `display/i $pc`（Ch 7）可以每步自動顯示當前指令，組語級 debug 的標配。練習 A 你已經用過 `stepi`。

## `finish`：跑完當前函式

「我不小心 `step` 進了一個函式，想趕快出來」或「我想看這個函式回傳什麼」——`finish`：

```
(gdb) finish
Run till exit from #0  square (n=5) at step_demo.c:2
0x...115e in main () at step_demo.c:5
5           int y = square(x);
Value returned is $1 = 25            # ← 順便告訴你回傳值！
```

`finish` 執行到**當前函式 return**為止，停在呼叫它的地方，並印出回傳值（`Value returned`）。練習 A 就靠它看 `transform` / `check` 的回傳值。

底層：`finish` 在當前函式的回返位址（return address，存在 stack 上）下一個臨時斷點，然後 `continue`。所以遞迴函式 `finish` 只跑完**當前這一層**。

## `until`：跨過迴圈

`until`（簡寫 `u`）有兩個模式，都跟迴圈有關：

```
(gdb) until           # 不帶參數：執行直到「比當前行號大」的行——跳出迴圈！
```

不帶參數的 `until` 超實用：你在迴圈體裡，按 `next` 會一圈圈轉，但 `until` 會跑到**迴圈結束後的那一行**。它的判斷是「停在原始碼行號 > 當前行的位置」，所以對 `for`/`while` 的最後一行用 `until`，會一口氣跑完剩餘迭代。

```
(gdb) until 50        # 帶行號：跑到第 50 行（且不會被中途的迴圈回跳卡住）
```

帶參數的 `until N` 像「跑到第 N 行」，但比 `advance N` 多一個保證：不會因為迴圈往回跳而停。

## `advance`：跑到某處（一次性）

```
(gdb) advance 100         # 跑到第 100 行就停（等於臨時斷點 + continue）
(gdb) advance funcname    # 跑到某函式
```

`advance` = 「在這個 location 下一個臨時斷點，continue 過去」，但如果**當前函式先 return 了**，它也會停（不會跑出函式還繼續找）。適合「我想直接跳到這個函式後段的某行」。

## `return`：強制提前返回

```
(gdb) return              # 立刻從當前函式返回（跳過剩下的程式碼！）
(gdb) return 42           # 返回並指定回傳值為 42
```

`return` 不是「跑完」而是「**立刻放棄**當前函式、強制返回」。它會跳過函式裡還沒執行的所有程式碼。配合指定回傳值，可以模擬「假設這函式成功回傳 X」來測試上層邏輯——強大但危險（跳過的清理程式碼不會跑，可能 leak）。

## 對照全表

| 指令 | 單位 | 碰到 call | 典型用途 |
|---|---|---|---|
| `step` (s) | 原始碼行 | 進入（有符號才進） | 想看函式內部 |
| `next` (n) | 原始碼行 | 跨過 | 把函式當黑盒 |
| `stepi` (si) | 機器指令 | 進入 | 組語級、無符號 |
| `nexti` (ni) | 機器指令 | 跨過 | 組語級但不進 call |
| `finish` | — | — | 跑完當前函式、看回傳值 |
| `until` (u) | — | — | 跳出迴圈 / 跑到某行 |
| `advance` | — | — | 跑到某 location（一次性） |
| `return` | — | — | 強制提前返回 |
| `continue` (c) | — | — | 一路跑到下個斷點 |

## 底層：source-level step 是怎麼做到的？

這是理解 Ch 38、Ch 41 的關鍵。CPU 只懂「執行一條指令」（對應 `PTRACE_SINGLESTEP`），它**沒有「一行原始碼」的概念**。那 `step`（走一整行）怎麼實作？

```
   一行 C    →   多條機器指令
   y = square(x)
        │
        ├─ mov  eax, [x]
        ├─ mov  edi, eax
        ├─ call square       ← step 要在這裡「進去」
        └─ mov  [y], eax

   GDB 的 step 演算法（簡化）：
   1. 查 DWARF line table，知道「當前行」涵蓋哪段位址範圍
   2. 反覆 PTRACE_SINGLESTEP（一條一條走）
   3. 每走一步檢查 $pc：
       - 還在「當前行」的位址範圍內？ → 繼續 single-step
       - 跳出範圍、到了「下一行」？     → 停！這就是 step 完成
       - 遇到 call 且目標有符號？       → 進入（step）或在 return 處設臨時斷點跨過（next）
```

所以一個 `step` 背後可能是幾十次 `PTRACE_SINGLESTEP` + 不斷查 DWARF line table 比對位址。GDB 把這個繁瑣過程包成一個指令。Ch 41 你會親手實作這個 step 演算法——那時你會真正感激 `step` 這個字。

> 認識論誠實：上面是簡化模型。真實 GDB 還要處理 inline 函式、跨行的最佳化、`step` 進 recursion 等 corner case，比這複雜得多。但「single-step + 查 line table + 比對範圍」這個核心是對的。

## 踩雷集錦

1. **`step` 跌進 libc 出不來**：你裝了 libc debug info，`step` 真的進了 `printf`。用 `finish` 出來，下次改用 `next`，或用 `skip` 設定永久跳過某些檔案。
2. **以為 `next` 完全不進函式**：`next` 跨過函式呼叫，但如果被跨過的函式裡**有斷點**，還是會停在那個斷點！`next` 不會停用斷點。
3. **遞迴裡 `finish` 只出一層**：`finish` 只跑完當前那一層遞迴，不是整串。要全出來得連按或用 `until`。
4. **`return` 造成資源洩漏**：強制返回會跳過 `free`、解鎖、檔案關閉等清理。debug 時無妨，但別誤以為程式「正常」走過。
5. **在沒停下來時 step**：程式還沒 `run` 或已結束，stepping 指令會報錯或無意義。先確認 inferior 是 stopped 狀態。
6. **`until` 不帶參數誤用**：它是「行號大於當前」才停，在非迴圈情境行為可能不如預期。它是為迴圈設計的。

## 進階：再往深一層

- **`set step-mode on`**：讓 `step` 在沒有 line info 的函式也停在第一條指令（而非跨過）。debug 沒符號的函式入口時有用。
- **`skip`**：`skip file libc.so` / `skip function std::__detail::...` 把指定檔案/函式永久標記為 step 跳過。debug C++（一堆 STL 樣板）時救命。`info skip` 看清單。
- **reverse stepping**：`reverse-step` / `reverse-next` / `reverse-stepi`——往回走！需要 record（Ch 34）或 rr（Ch 35）。「我 step 過頭了」不再是災難。
- **`set scheduler-locking step`**：多執行緒時，`step` 預設只讓當前 thread 動、其他凍結（避免你 step 一步結果別的 thread 插隊）。Ch 16 細講。

## 動手練習

1. 對 `step_demo.c`，在 line 5 分別用 `step` 和 `next`，確認一個進 `square`、一個跨過。
2. 在 `square` 內用 `finish`，確認回到 line 5 並看到 `Value returned is $1 = 25`。
3. 寫一個有 `for` 迴圈跑 1000 次的程式，在迴圈體內用 `until`（不帶參數）一口氣跳出迴圈，對比用 `next` 一圈圈轉的痛苦。
4. 對 `printf` 那行用 `step`，觀察它有沒有進 libc（取決於你有沒有 libc debug info / debuginfod）。再 `set debuginfod enabled off` 重來比較。
5. 進入某函式後用 `return 99`，`continue`，看上層拿到的回傳值被你竄改成 99。

## 本章重點整理

- 用 2×2 記 stepping：行 vs 指令（step/next vs stepi/nexti）× 進入 vs 跨過。
- `step` 只進「有 debug info」的函式，沒符號的自動跨過。
- `finish` 跑完當前函式並印回傳值；遞迴只出一層。
- `until`（不帶參）跳出迴圈；`advance` 跑到某 location；`return` 強制提前返回。
- 底層：source-level step = 不斷 single-step + 查 DWARF line table 比對位址範圍（Ch 38/41 細講）。

## 自我檢核

- [ ] 不看表，講得出 step/next/stepi/nexti 的四格差異嗎？
- [ ] 為什麼 `step` 有時進 libc、有時不進？取決於什麼？
- [ ] 想「跑完當前函式看它回傳什麼」用哪個指令？遞迴時要注意什麼？
- [ ] CPU 沒有「一行原始碼」的概念，GDB 怎麼實作 `step`？
- [ ] 困在迴圈裡想跳出來，用哪個指令最省事？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Continuing and Stepping](https://sourceware.org/gdb/current/onlinedocs/gdb/Continuing-and-Stepping.html)**
  - **讀哪裡**：整節；step/next/stepi/until/finish/advance/return 的精確定義都在這。
  - **和本章的關聯**：本章的權威來源；`set step-mode`、`skip` 的細節也在附近章節。

- **[GDB Manual: Skipping Over Functions and Files](https://sourceware.org/gdb/current/onlinedocs/gdb/Skipping-Over-Functions-and-Files.html)**
  - **讀哪裡**：`skip` 的 file/function/regex 三種用法。
  - **和本章的關聯**：解決「step 跌進 STL/libc」的標準手段。

### 部落格 / 文章

- **[Reverse debugging with GDB](https://sourceware.org/gdb/wiki/ReverseDebug)** — GDB Wiki
  - **這篇說什麼**：reverse-step/next 的能力與限制。
  - **和本章的關聯**：本章「step 過頭」的解藥；Ch 34 會完整展開。

下一章補上 stepping 與 breakpoint 都依賴的東西：符號與原始碼是怎麼被 GDB 找到、對應、顯示的。

→ [Ch 6 原始碼與符號](./06-source-and-symbols.md)
