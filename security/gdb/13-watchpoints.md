# Ch 13 — Watchpoint

> **目標**：掌握 watchpoint——「監視一塊記憶體/一個表示式，當它被讀或寫就停」。理解硬體 watchpoint（debug register）與軟體 watchpoint 的天壤之別、`watch`/`rwatch`/`awatch` 的差異、scope 限制，以及為什麼它是 debug「資料被誰改壞」的終極武器。

> **環境**：GDB 13/14，Linux x86_64。watchpoint 行為高度依賴硬體與 OS——本章以 x86-64 為準。

## 為什麼 watchpoint 是「原因 debug」的關鍵

breakpoint 回答「程式跑到**哪裡**」，watchpoint 回答「**誰**動了這個值」。

練習 B 的核心痛點就是：某個指標被默默改壞，崩潰在很久以後。如果你能對那個指標說「誰敢動你，立刻給我停」，真兇當場落網——這就是 watchpoint。它把「在崩潰點往回猜」的痛苦 debug，變成「在污染發生的瞬間抓現行」的精準打擊。

任何「這個變數為什麼變成這樣？」的問題，watchpoint 都是首選答案。

## 三種 watchpoint

```
(gdb) watch  x            # 寫入 watchpoint：x 被「寫」時停（最常用）
(gdb) rwatch x            # 讀取 watchpoint：x 被「讀」時停
(gdb) awatch x            # access：x 被讀或寫都停
```

| 指令 | 觸發時機 | 典型用途 |
|---|---|---|
| `watch` | 值被修改 | 「誰改了這個變數」（最常用） |
| `rwatch` | 值被讀取 | 「誰讀了這個敏感資料」 |
| `awatch` | 讀或寫 | 全面監控 |

監視的對象可以是變數、欄位、解參、甚至任意表示式：

```
(gdb) watch global_counter           # 全域變數
(gdb) watch node->next               # 結構欄位（練習 B 的真兇捕手）
(gdb) watch *(int *)0x555555558040   # 某位址的 int
(gdb) watch buf[index]               # 陣列元素
(gdb) watch a + b                    # 表示式！a 或 b 變化導致 a+b 變就停
```

## 硬體 vs 軟體：差了好幾個數量級

這是 watchpoint 最重要、最多人不懂的一點。

### 硬體 watchpoint（你想要的）

x86 CPU 有 4 個 **debug register**（DR0–DR3），可以設定「監視這個位址，被存取時 CPU 自己觸發例外」。GDB 優先用硬體 watchpoint：

- **幾乎零成本**：CPU 硬體層做檢查，程式全速跑，被碰到才停。
- **限制**：只有 4 個（DR0-3），且每個只能監視 1/2/4/8 byte 的對齊區塊。硬體斷點（`hbreak`）也搶這 4 個 register。

```
(gdb) watch x
Hardware watchpoint 1: x         # ← "Hardware"，太好了，全速
```

### 軟體 watchpoint（災難）

如果監視的範圍超過硬體能力（例如監視一整個大結構、或一個會變動位址的表示式），GDB 退回**軟體 watchpoint**：

```
(gdb) watch huge_struct
Watchpoint 1: huge_struct        # ← 沒有 "Hardware"！要命了
```

軟體 watchpoint 的做法是**每執行一條指令就 single-step + 檢查值有沒有變**。這意味著程式變成龜速（慢幾百到幾千倍），一個本來 1 秒的程式可能跑半小時。

```
                硬體 watchpoint              軟體 watchpoint
   程式速度       全速                        慢數百~數千倍
   機制           CPU debug register          每條指令 single-step + 比對
   數量限制       4 個（與 hbreak 共用）       無限（但每個都拖垮速度）
   範圍限制       1/2/4/8 byte 對齊            任意大小/表示式
```

> 鐵則：**看到 watchpoint 沒有 "Hardware" 字樣，立刻警覺。** 通常表示你監視的範圍太大或是 GDB 不支援硬體 watch 的情境。縮小到一個 4/8 byte 的純量、或一個固定位址，盡量讓它變成硬體的。`show can-use-hw-watchpoints` 確認硬體支援有沒有開。

## scope：watchpoint 的生命週期

watchpoint 監視的東西可能離開作用域。監視一個區域變數，函式 return 後那變數就不存在了：

```
(gdb) watch local_var
Hardware watchpoint 2: local_var
(gdb) continue
...函式 return...
Watchpoint 2 deleted because the program has left the block in
which its expression is valid.
```

GDB 偵測到「監視的變數離開了作用域」會**自動刪除** watchpoint。這通常是對的（變數沒了，監視無意義）。但如果你想監視的是「那塊記憶體」而非「那個變數名」，就改成監視位址：

```
(gdb) watch *(long *)&local_var      # 監視位址，不綁變數作用域 → 函式 return 後仍有效
```

這個區別在練習 B、練習 C 抓污染源時很關鍵——你要監視的是**記憶體位置**，即使持有它的變數名換了。

## 一個經典應用：抓「誰改壞了我的變數」

```c
// watch_demo.c — gcc -g -O0
#include <stdio.h>
int config = 42;
void evil(void) { config = -1; }       // 偷偷改壞
void normal(void) { printf("working\n"); }
int main(void) {
    normal();
    evil();                            // 罪魁禍首
    normal();
    printf("config = %d\n", config);   // 這裡才發現 config 變了
    return 0;
}
```

```
(gdb) break main
(gdb) run
(gdb) watch config
Hardware watchpoint 2: config
(gdb) continue
Hardware watchpoint 2: config
Old value = 42
New value = -1
evil () at watch_demo.c:4              # ← 當場抓到是 evil() 改的！
4       void evil(void) { config = -1; }
(gdb) backtrace                        # 完整呼叫鏈
#0  evil () at watch_demo.c:4
#1  ... in main () at watch_demo.c:8
```

watchpoint 直接停在 `config = -1` 那刻，`Old value`/`New value` 告訴你變化、`backtrace` 告訴你誰幹的。對比「在最後 `printf` 發現 config 錯了再往回猜」，這是天與地的差別。

## watchpoint 配條件與 commands

watchpoint 和 breakpoint 共用條件與 commands 機制（Ch 12）：

```
(gdb) watch x if x > 100             # 只在 x 被改成 >100 時停
(gdb) watch x
(gdb) commands
>printf "x changed to %d\n", x
>continue
>end
```

「監視 + 條件 + 自動記錄」可以做出「追蹤某變數每次變化的完整歷史」。

## 踩雷集錦

1. **沒注意到變成軟體 watchpoint，程式跑超慢**：以為 GDB 卡死，其實是軟體 watchpoint 在 single-step。看到沒 "Hardware" 就縮小監視範圍。
2. **監視區域變數，函式一 return 就被刪**：這是預期行為。要監視記憶體位置就用 `watch *(T*)&var`。
3. **4 個硬體 watchpoint 用完了**：`watch` 第 5 個會報錯或退回軟體。記得 `hbreak`（硬體斷點）也搶這 4 個 register。`delete` 不用的。
4. **監視的位址在多執行緒下**：硬體 debug register 是 **per-CPU/per-thread** 的，GDB 會幫你在所有 thread 設。但要理解監視範圍與 thread 的關係（Ch 16）。
5. **watch 一個還沒配置的指標目標**：`watch *ptr` 但 `ptr` 還是 NULL/野指標——GDB 監視的是「現在 `*ptr` 算出的位址」。`ptr` 之後改指向別處，watchpoint 還盯著舊位址。要小心表示式 watchpoint 的「快照」語意。
6. **在 attach 的 process 上 watchpoint 失效**：某些容器/虛擬化環境硬體 watchpoint 不可用，全退回軟體。`show can-use-hw-watchpoints`。

## 進階：再往深一層

- **debug register 直視**：`info registers $dr0` ~ `$dr7`——`DR0-3` 存監視位址、`DR6` 狀態、`DR7` 控制。Ch 39 會看 GDB 怎麼設定它們。
- **`watch -location expr`**：明確要 GDB 監視「表示式現在算出的位址」而非綁變數作用域，避免上面踩雷 5。
- **watchpoint + reverse debugging**：抓到「值被改」後，配 reverse-continue（Ch 34）往回走看改之前發生什麼——時間雙向的污染分析。
- **大範圍監視的替代**：要監視一整個結構/緩衝區又不想軟體 watchpoint 龜速，考慮：(a) AddressSanitizer / Valgrind（編譯期工具）；(b) mprotect 把該頁設唯讀，靠 SIGSEGV catch（Ch 14/15）；(c) 只監視結構裡最關鍵的那個 8-byte 欄位。
- **硬體 watchpoint 的對齊**：監視位址要落在硬體支援的對齊邊界（1/2/4/8 byte）。監視一個 3-byte 的東西可能被迫退回軟體或拆成多個。

## 動手練習

1. 對 `watch_demo.c`，`watch config`，確認停在 `evil()` 並看到 Old/New value。
2. 故意 `watch` 一個大結構（`watch some_big_struct`），觀察它變成軟體 watchpoint（無 "Hardware"），感受程式變慢。
3. 監視一個區域變數，讓函式 return，觀察 watchpoint 被自動刪除的訊息；再改用 `watch *(int*)&var` 看它撐過 return。
4. 用 `watch x if x < 0` 只在變負時停。
5. 把練習 B 的 detective.c 拿來，對壞節點的 `next` 下 watchpoint，當場抓住 `strcpy` 寫越界的瞬間（這是練習 B 的進階步驟）。
6. `info registers $dr0 $dr7`，看 GDB 設了硬體 watchpoint 後 debug register 的內容。

## 本章重點整理

- watchpoint 監視記憶體：`watch`（寫）/`rwatch`（讀）/`awatch`（讀寫）；回答「誰改了這個值」。
- 硬體 watchpoint（debug register，全速，限 4 個、1/2/4/8 byte）vs 軟體 watchpoint（single-step 比對，慢數百倍，無限量）——看到沒 "Hardware" 要警覺。
- 監視區域變數會在離開作用域時自動刪除；要監視記憶體位置用 `watch *(T*)&var`。
- 配條件與 commands 可做「追蹤變數變化歷史」。
- 抓資料污染源的終極武器：把「崩潰點往回猜」變成「污染瞬間抓現行」。

## 自我檢核

- [ ] `watch` / `rwatch` / `awatch` 各監視什麼動作？
- [ ] 硬體與軟體 watchpoint 差在哪？怎麼從輸出判斷自己拿到哪種？為什麼軟體的那麼慢？
- [ ] 硬體 watchpoint 有幾個？還跟誰搶資源？
- [ ] 監視區域變數函式 return 後會怎樣？想監視「那塊記憶體」該怎麼寫？
- [ ] 「某變數不知被誰改壞」的 bug，你的第一招是什麼？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Setting Watchpoints](https://sourceware.org/gdb/current/onlinedocs/gdb/Set-Watchpoints.html)**
  - **讀哪裡**：watch/rwatch/awatch、硬體 vs 軟體說明、scope 自動刪除、`can-use-hw-watchpoints`。
  - **和本章的關聯**：本章的權威來源，含多執行緒下的行為細節。

### 部落格 / 文章

- **[Hardware Breakpoints / Watchpoints under the hood](https://ld-debug.blogspot.com/)** 類 debug register 解析文，或 **[Intel SDM Vol.3 Ch.17 Debug Registers]**
  - **讀哪裡**：DR0-DR7 的角色（DR7 control、DR6 status）。
  - **和本章的關聯**：硬體 watchpoint 底層怎麼用 debug register；Ch 39 會實作。

- **[Catching memory corruption with watchpoints](https://developers.redhat.com/)** 類實戰文
  - **為什麼值得讀**：把「抓污染源」的流程放進真實 case，呼應練習 B/C。

下一章是 watchpoint 的近親：catchpoint——不監視資料，而是攔截「事件」：syscall、signal、C++ exception、fork/exec。

→ [Ch 14 Catchpoint](./14-catchpoints.md)
