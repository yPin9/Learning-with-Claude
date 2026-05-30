# Ch 34 — Reverse debugging

> **目標**：掌握 GDB 內建的時間旅行——record/replay 與 reverse 執行。`record` 記錄執行、`reverse-continue`/`reverse-step`/`reverse-next` 往回走、reverse watchpoint 抓「值是何時被改的」。理解它的能力與嚴重限制，以及為什麼下一章的 rr 是更實用的替代。

> **環境**：GDB 13/14，Linux x86_64。GDB 內建 record 限 x86/x86-64。

## 為什麼要「往回走」

普通 debug 的致命弱點：**你只能往前。** step 過頭了？重來。想知道「這個變數是何時、被誰改成這個壞值的」？只能設 watchpoint 重跑。崩潰了想看崩潰前一刻的狀態？太遲了，已經崩了。

reverse debugging 打破這個限制：程式能**往回走**。step 過頭就 reverse-step 回來；崩潰了就往回走看崩潰前發生什麼；值被改壞就 reverse 到改它的那一刻。這對「結果已經發生、要找原因」的 bug 是降維打擊。

```
   普通 debug：只能 →
   ────●────────●────────X
       想回到這？只能重跑

   reverse debug：能 ← 也能 →
   ────●───◄───●───◄────X
       reverse-continue 回到上一個事件
```

## record：開始記錄

GDB 內建的 record 功能記錄每條指令的執行與記憶體變化，之後可以重播與反向：

```
(gdb) break main
(gdb) run
(gdb) record                      # 從這裡開始記錄（也叫 record full）
(gdb) continue                    # 正常往前跑，但全程被記錄
... 跑到崩潰或斷點 ...
(gdb) reverse-continue            # 往回跑到上一個斷點/記錄起點！
(gdb) reverse-step                # 往回一行
(gdb) reverse-next                # 往回一行（跨過函式）
(gdb) reverse-stepi               # 往回一條指令
(gdb) reverse-finish              # 回到當前函式被呼叫的地方
```

開了 record 後，所有正向指令照常，外加一整套 `reverse-*` 反向版本。`record stop` 停止記錄。

## 經典應用：抓「值是何時被改壞的」

這是 reverse debugging 最殺的用法。配 reverse + watchpoint：

```c
// rev_demo.c — gcc -g -O0
#include <stdio.h>
int state = 100;
void corrupt(void){ state = -1; }
void work(void){ /* 一堆操作 */ }
int main(void){
    work();
    corrupt();           // 何時 state 變壞的？
    work();
    printf("state=%d\n", state);   // 這裡才發現 state 錯了
    return 0;
}
```

傳統做法要 watchpoint 重跑。reverse 做法——**發現錯了，往回找**：

```
(gdb) break main
(gdb) run
(gdb) record
(gdb) continue           # 跑到底
(gdb) print state
$1 = -1                  # 發現壞了
(gdb) watch state        # 設 watchpoint
(gdb) reverse-continue   # 往回跑！會停在「最後一次改 state」的地方
Hardware watchpoint 2: state
Old value = 100
New value = -1
corrupt () at rev_demo.c:4    # 抓到了！是 corrupt 改的
```

`watch` + `reverse-continue` = 「從結果往回找到原因」。不用重跑、不用猜，直接時間倒流到污染點。這對練習 B/C 那種「值被默默改壞」的 bug 是神器。

## reverse 的執行模型

理解它怎麼做到（為什麼有限制）：

```
   record 模式下，GDB 每執行一條指令就記錄：
     - 這條指令改了哪些記憶體（舊值）
     - 改了哪些暫存器（舊值）
   
   reverse-step 就是「反向套用」這些記錄：
     - 把記憶體/暫存器恢復成執行前的狀態
   
   所以 reverse 不是「真的倒著執行 CPU」，
   而是「用記錄把狀態回滾」。
```

這個模型解釋了它的限制：每條指令都要記錄狀態變化 → **超級慢、超級吃記憶體**。

## 嚴重限制（為什麼下一章要學 rr）

GDB 內建 record 有很現實的限制：

1. **極慢**：每條指令都記錄，比正常執行慢幾十到上百倍。記錄一個跑幾秒的程式可能要等很久。
2. **記憶體爆炸**：記錄所有狀態變化，長時間執行的記錄佔用巨大記憶體。`set record full insn-number-max` 限制記錄長度（滿了丟棄最舊的）。
3. **不支援所有指令**：遇到無法記錄的指令（某些 SIMD、syscall 行為）會停止記錄。
4. **syscall / 外部互動難處理**：record full 對有大量 I/O、syscall 的程式效果差。
5. **只限 x86/x86-64**：ARM 等不支援內建 record。

```
(gdb) record full                 # 完整記錄（預設）
(gdb) record btrace               # 用 Intel PT 硬體 trace（快很多，但只記錄控制流，不記憶體）
```

`record btrace`（用 Intel Processor Trace 硬體）快得多，但只記錄**執行流**（能 reverse-step 走控制流），不記錄記憶體變化（不能看「當時某變數的值」）。各有取捨。

> 認識論誠實：GDB 內建 record 在實務上**很少用於長程式**——太慢太佔記憶體。它適合「短程式、最後幾千條指令、想 reverse 看一下」的場景。真正生產級的 reverse debugging 是下一章的 **rr**——它用完全不同的機制（記錄非確定性輸入而非每條指令），快得多、實用得多。本章建立 reverse 的概念，下一章給你真正會用的工具。

## reverse 的其他用法

```
(gdb) reverse-stepi               # step 過頭了，倒回來
(gdb) until 與 reverse 配合         # 在迴圈裡前後移動
(gdb) record goto 100             # 跳到記錄中的第 100 條指令
(gdb) record goto start/end       # 跳到記錄頭/尾
(gdb) info record                 # 看記錄狀態（記了幾條、用多少記憶體）
```

`record goto N` 讓你直接跳到記錄中的任意時間點——在記錄的時間軸上自由移動。

## 踩雷集錦

1. **record 後程式慢如蝸牛**：正常現象（每條指令記錄）。只 record「你關心的那段」——跑到接近問題處才 `record`，別從頭記。
2. **記憶體爆掉 / 記錄被截斷**：`set record full insn-number-max` 滿了會丟最舊的。聚焦短區段。
3. **reverse 不能跨越記錄起點**：`reverse-continue` 最多回到 `record` 開始的地方，記錄之前的去不了。
4. **以為能 reverse 任意程式**：只限 x86/x86-64、record full 對重 I/O 程式效果差。複雜場景用 rr。
5. **record btrace 想看變數值卻看不到**：btrace 只記控制流不記記憶體。要記憶體用 record full。
6. **syscall 後 reverse 行為怪**：外部世界（檔案、網路）不能真的回滾，record 對 syscall 的處理有限。

## 進階：再往深一層

- **record full vs btrace 的取捨**：full 記憶體完整可看但慢；btrace 快但只有控制流。依需求選。
- **`set record full memory-query`**：記錄記憶體不足時怎麼處理。
- **reverse + 條件**：`reverse-continue` 也受斷點/watchpoint 影響——可以設條件斷點再 reverse。
- **記錄的儲存**：`record save file` / `record restore`——把記錄存檔，之後重載分析（類似可 reverse 的 core）。
- **與 rr 的關係**：rr（Ch 35）不用 GDB 的 record，而是自己 record 一次執行成 trace，再用 GDB 連到 rr replay——機制完全不同，效能天差地遠。
- **硬體 trace（Intel PT）**：`record btrace` 背後是 CPU 的 Processor Trace 功能，硬體記錄控制流，開銷小。理解它是 btrace 快的原因。

## 動手練習

1. 對 `rev_demo.c`，`record` 後 `continue` 到底，`print state` 發現壞了，`watch state` + `reverse-continue` 抓到 `corrupt`——體驗「從結果往回找原因」。
2. `reverse-step` / `reverse-next` 在程式裡前後走動，體會時間雙向。
3. `record goto start` 回到記錄起點、`record goto end` 到尾，`info record` 看記了多少。
4. 故意 `record` 一個跑很久的迴圈，感受它有多慢、`info record` 看記憶體用量——理解為什麼長程式不實用。
5. 試 `record btrace` 對同一程式，比較速度，並確認它能 reverse-step（控制流）但 `print` 不到歷史變數值。
6. step 過頭時用 `reverse-step` 回來——體會「再也不用因為按過頭而重跑」。

## 本章重點整理

- reverse debugging 讓程式往回走，解決「結果已發生、要找原因」的 bug。
- `record` 開始記錄後，所有 `reverse-*`（continue/step/next/stepi/finish）可用；`record goto N` 跳到記錄中任意點。
- 殺手用法：`watch X` + `reverse-continue` = 時間倒流到「X 被改壞」的那一刻。
- 機制：record 記錄每條指令的狀態變化，reverse 是回滾這些記錄——所以**極慢、極吃記憶體**。
- 限制多（慢、記憶體、限 x86、I/O 難處理）→ 生產級 reverse 用下一章的 **rr**。

## 自我檢核

- [ ] reverse debugging 解決了普通 debug 的什麼根本弱點？
- [ ] 「某變數不知何時被改壞」用 reverse 怎麼一招解決？
- [ ] reverse 是「真的倒著執行 CPU」嗎？實際機制是什麼？這帶來什麼限制？
- [ ] record full 和 record btrace 各記錄什麼、各有什麼取捨？
- [ ] 為什麼 GDB 內建 record 不適合長程式？實務上用什麼替代？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Reverse Execution](https://sourceware.org/gdb/current/onlinedocs/gdb/Reverse-Execution.html)** 與 **[Process Record and Replay](https://sourceware.org/gdb/current/onlinedocs/gdb/Process-Record-and-Replay.html)**
  - **讀哪裡**：reverse-* 指令、record full/btrace、record goto/save。
  - **和本章的關聯**：本章核心的權威，含 record 的所有設定。

### 部落格 / 文章

- **[Reverse debugging in GDB](https://sourceware.org/gdb/wiki/ReverseDebug)** — GDB Wiki
  - **這篇說什麼**：reverse 的能力、限制、btrace 與硬體 trace。
  - **為什麼值得讀**：把「何時用內建 record、何時該換 rr」講清楚。

下一章是 reverse debugging 的真正生產級工具：rr——用 record-replay 做到快速、確定性的時間旅行，把不可重現的 bug 變可重現。

→ [Ch 35 rr：record-replay 時間旅行](./35-rr-record-replay.md)
