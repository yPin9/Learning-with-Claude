# Ch 12 — 條件斷點與 breakpoint commands

> **目標**：把斷點從「每次都停」升級成「只在我要的條件停、停下來自動做事」。掌握 `break ... if`、`condition`、`ignore`、`commands`、`dprintf`，以及它們合起來能做到的半自動化 debug。這是擺脫「continue 按到死」的關鍵一章。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼這章能救你的人生

你在一個跑一百萬次的迴圈裡 debug，bug 只在第 738291 次出現。普通斷點讓你按 `continue` 按到地老天荒。條件斷點一行解決：`break loop.c:10 if i == 738291`。

或者：你想知道某函式每次被呼叫時某參數的值，但不想每次手動 `print`。`commands` 讓斷點命中時自動印再自動繼續。

這些技巧把 debug 從「人肉重複勞動」變成「設定好讓 GDB 自己跑」。學會它們，你的 debug 效率是別人的好幾倍。

## 範例程式

```c
// cond_demo.c — gcc -g -O0
#include <stdio.h>
int process(int id, int value) {
    int result = value * 2;        // line 4
    return result;
}
int main(void) {
    int total = 0;
    for (int i = 0; i < 1000000; i++)
        total += process(i, i % 100);   // 跑一百萬次
    printf("%d\n", total);
    return 0;
}
```

## 條件斷點：`break ... if`

```
(gdb) break process if id == 500000        # 只在 id==500000 時停
(gdb) break cond_demo.c:4 if value > 95     # 只在 value>95 時停
(gdb) break process if id > 100 && value == 50    # 複合條件
```

條件就是一個 Ch 8 的 GDB 表示式，每次斷點命中時求值，**非 0（true）才真的停**。條件可以用：

- 參數、區域變數、全域變數
- 暫存器：`break *0x... if $rax == 0`
- convenience function：`break malloc if $_caller_is("plugin_init")`（只在被特定函式呼叫時停）
- 字串比較：`break log if $_streq(tag, "ERROR")`

### 事後加 / 改條件

斷點下好了才想加條件，不用刪掉重下：

```
(gdb) condition 2 i == 999            # 給斷點 2 加上條件
(gdb) condition 2                     # 拿掉斷點 2 的條件（變回無條件）
```

## `ignore`：忽略前 N 次

有時你不知道精確條件，只知道「大概第幾次」：

```
(gdb) ignore 1 500000                # 斷點 1 的前 500000 次命中都忽略
Will ignore next 500000 crossings of breakpoint 1.
```

`ignore` 和條件斷點的差別：`ignore` 是「數次數」，條件是「看狀態」。當你只想跳過固定次數（例如「跳過暖身的前 1000 次」），`ignore` 比寫條件簡單。

> 效能提醒：條件斷點**不是免費的**。GDB 軟體斷點每次命中都要：觸發 SIGTRAP → 凍結 → 求值條件 → 不符就還原繼續。在跑一百萬次的迴圈上設條件斷點，可能慢到爬。Ch 13 的 watchpoint、或下面的「硬體加速」、或乾脆改用 `ignore` 計數，都是解法。極端情況可改程式邏輯或用 dprintf。

## `commands`：命中時自動執行指令

斷點命中時，自動執行一串指令——這是半自動化 debug 的核心：

```
(gdb) break process
(gdb) commands
Type commands for breakpoint(s) 1, one per line.
End with a line saying just "end".
>print id
>print value
>backtrace 2
>continue                    # ← 印完自動繼續，不停下來！
>end
```

設好後，每次 `process` 被呼叫，GDB 自動印 `id`、`value`、兩層 backtrace，然後**自動繼續**——你完全不用動手，就得到一份「每次呼叫的記錄」。

`commands` + `continue` 的組合 = 「logging 斷點」：不打斷執行，但每次經過就記錄。配合 `set logging on`（把輸出存檔，Ch 20）可以蒐集完整的呼叫軌跡。

常見模式：

```
(gdb) commands 1
>silent                      # 不印「Breakpoint 1, ...」那行雜訊
>printf "process(%d, %d)\n", id, value
>continue
>end
```

`silent` 抑制斷點命中的預設訊息，只留你要的輸出，乾淨。

## `dprintf`：專為「印了就走」設計

上面的 `commands` + `silent` + `printf` + `continue` 模式太常用，GDB 給了一個專用指令 `dprintf`（dynamic printf）：

```
(gdb) dprintf process, "process(%d, %d) result=...\n", id, value
```

`dprintf 位置, 格式字串, 參數...` = 在該位置「印這個然後繼續」，**完全不停**。等於在不改原始碼、不重編譯的情況下，動態插入一行 `printf`。

這解決了「printf debug」最大的痛點——不用改 code、不用重編、不用重啟，隨時加隨時刪：

```
(gdb) dprintf parser.c:120, "depth=%d token=%s\n", depth, tok
(gdb) info breakpoints              # dprintf 也列在這
(gdb) delete 3                      # 不要了就刪
```

`dprintf` 還能設定輸出去向：

```
(gdb) set dprintf-style call         # 用 inferior 的 printf（會經過程式的 stdout）
(gdb) set dprintf-style gdb          # 用 GDB 印（預設）
(gdb) set dprintf-channel ...        # 甚至導到檔案
```

## 組合技：條件 + commands + 自動繼續

把這章串起來，做一個「只在出問題時印詳細狀態」的智慧斷點：

```
(gdb) break process if value > 95
(gdb) commands
>silent
>printf "ANOMALY: id=%d value=%d\n", id, value
>info registers rdi rsi
>continue
>end
(gdb) run
ANOMALY: id=96 value=96
ANOMALY: id=97 value=97
...
```

程式全速跑，只在 `value > 95` 時自動吐出一行異常記錄。這就是「設好讓 GDB 自己 debug」的境界——你去喝杯咖啡，回來看記錄。

## 踩雷集錦

1. **條件斷點拖慢百萬迴圈**：軟體條件斷點每次命中都要陷入 GDB 求值，超慢。對熱點迴圈考慮：用 `ignore` 計數、改條件位置到較少命中的地方、或 Ch 13 硬體 watchpoint。
2. **`commands` 忘了加 `continue` 結果還是停**：如果你要「印了就走」，`commands` 裡最後一定要有 `continue`，否則印完還是停在那。
3. **`commands` 裡沒 `silent` 一堆雜訊**：每次命中都印「Breakpoint 1, process (...)」。加 `silent` 在第一行。
4. **條件用了 inferior call 有副作用**：`break f if expensive_check()` 會在每次命中時真的呼叫 `expensive_check`（inferior call，Ch 8），有副作用又慢。盡量用純讀的條件。
5. **條件表示式作用域錯**：`break main if i == 5` 但 `i` 在 `main` 此刻還不存在（在某迴圈內才有）——條件求值失敗，GDB 會警告或當作不符。確認變數在斷點位置可見。
6. **dprintf 的格式字串對不上型別**：`%s` 配一個 int 會崩。和 C 的 printf 一樣要型別對齊。

## 進階：再往深一層

- **`tbreak ... if`**：臨時的條件斷點，命中一次（且符合條件）就刪。
- **`commands` 裡可呼叫 Python**：`python ...` 或自訂 Python 指令（Ch 24），讓命中時執行任意邏輯——條件斷點 + Python = 強大的執行期分析。
- **`$_hit_bpnum` / `$_hit_locno`**（GDB 10+）：在 commands 裡知道是哪個斷點/location 命中的，寫共用 command 時有用。
- **conditional breakpoint 的硬體加速**：某些情況 GDB 能把條件 offload，但一般軟體斷點條件仍在 GDB 端求值。真正的硬體條件要靠 watchpoint。
- **`save breakpoints`** 會連條件與 commands 一起存（Ch 4），長期 debug 必備。
- **用條件斷點抓 race / 特定狀態**：`break f if global_state == CORRUPTED`——當某全域進入壞狀態才停，配合 Ch 13 watchpoint 是 debug 狀態污染的黃金組合（練習 B、練習 C 都用得上）。

## 動手練習

1. 對 `cond_demo.c`，`break process if id == 500000`，確認一次就停在第 50 萬次。
2. 用 `ignore` 達到類似效果（忽略前 50 萬次），比較兩種寫法。
3. 設一個 `commands` 斷點：命中時 `silent` + `printf` 印 `id`/`value` + `continue`，跑完看蒐集到的記錄（先把迴圈次數改小到 20，不然刷螢幕）。
4. 用 `dprintf process, "id=%d val=%d\n", id, value` 達到同樣效果，比較 `dprintf` 的簡潔。
5. 做一個「只在異常時記錄」的智慧斷點（`break ... if value > 95` + commands + continue），體會「設好讓 GDB 自己跑」。
6. 用 `$_caller_is()` 設一個「只在被特定函式呼叫時停」的條件斷點。

## 本章重點整理

- `break ... if cond` / `condition N cond`：條件成立才停；條件是 GDB 表示式（可用變數、暫存器、convenience function）。
- `ignore N count`：忽略前 count 次（數次數，不看狀態）。
- `commands`：命中時自動執行指令；配 `silent`（去雜訊）+ `continue`（印了就走）= logging 斷點。
- `dprintf 位置, fmt, args`：動態插入一行 printf，不停、不改 code、不重編——printf debug 的終極形態。
- 條件斷點在熱點迴圈很慢（每次陷入 GDB 求值）；視情況改用 ignore / watchpoint。

## 自我檢核

- [ ] 在跑一百萬次的迴圈裡只想在第 N 次停，有哪兩種做法？各自取捨？
- [ ] 怎麼讓斷點「命中時印幾個值然後自動繼續、不停下來」？
- [ ] `dprintf` 解決了傳統「printf debug」的什麼痛點？
- [ ] 為什麼條件斷點在熱點迴圈會很慢？底層發生了什麼？
- [ ] 怎麼設「只在被某函式呼叫時才停」的斷點？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Break Conditions](https://sourceware.org/gdb/current/onlinedocs/gdb/Conditions.html)** 與 **[Breakpoint Command Lists](https://sourceware.org/gdb/current/onlinedocs/gdb/Break-Commands.html)**
  - **讀哪裡**：condition/ignore、commands/silent 兩節。
  - **和本章的關聯**：本章核心指令的權威定義。

- **[GDB Manual: Dynamic Printf](https://sourceware.org/gdb/current/onlinedocs/gdb/Dynamic-Printf.html)**
  - **讀哪裡**：dprintf 語法與 `set dprintf-style/channel`。
  - **和本章的關聯**：dprintf 的完整選項。

### 部落格 / 文章

- **[Give me 15 minutes & I'll change your view of GDB](https://www.youtube.com/watch?v=PorfLSr3DDI)** — Greg Law（CppCon talk）
  - **這篇說什麼**：用 dprintf、conditional breakpoint、reverse debugging 等招式徹底改變 debug 工作流的經典演講。
  - **為什麼值得讀/看**：本章與後面幾章的精神濃縮版；看完你會想立刻試。

下一章是「條件停」的硬體版、也是 debug 資料污染的神器：watchpoint——監視一塊記憶體，被讀/寫就停。

→ [Ch 13 Watchpoint](./13-watchpoints.md)
