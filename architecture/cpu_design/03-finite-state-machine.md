# Ch 3 — 有限狀態機（FSM）：控制器的本質

> **目標**：把「有限狀態機（finite state machine, FSM）」這個貫穿全課的核心結構學會。你會懂 Moore 與 Mealy 兩種 FSM 的差別、state encoding（狀態怎麼編碼）、以及業界標準的 two-block / three-block 寫法。我們用一個序列偵測器（偵測位元串 "101"）當範例，Moore 和 Mealy 各做一版、verilate 跑過對照。最關鍵的認知：**CPU 的控制單元（control unit）本質就是一台 FSM**——你學會 FSM，就掌握了「CPU 怎麼一步步指揮自己」的骨架。

## 為什麼需要 FSM：有記憶還不夠，要「有規律地轉換」

Ch 2 給了我們記憶（flip-flop）。但光能記住一個值還不夠——真正有用的系統，狀態要**隨著輸入、有規律地一步步轉換**。

想想紅綠燈：綠 → 黃 → 紅 → 綠，循環。它「記得」現在是哪個燈（狀態），也知道「下一步該變成哪個」（轉換規則）。想想 CPU：它「取指令 → 解碼 → 執行 → 寫回」，一步接一步；遇到 branch 要跳、遇到中斷要進 trap handler。這些都是「當前在某個狀態，根據輸入決定下一個狀態」。

把這件事系統化的數學模型，就是 FSM。它有三個要素：

- **一組有限的狀態（states）**：例如紅綠燈的 {綠, 黃, 紅}。
- **狀態轉換規則（transitions）**：在某狀態、看某輸入 → 去哪個下一狀態。
- **輸出（outputs）**：每個狀態（或狀態+輸入）對應什麼輸出。

先建立直覺。FSM 就是一張「狀態圖」——圈圈是狀態，箭頭是轉換（標上觸發它的輸入）：

```
       din=0                 din=1
   ┌────────┐            ┌──────────┐
   ▼        │            │          ▼
 [IDLE]───────►[S_1]───────►（收到 1 了）
   ▲   din=1    │   din=0
   │            ▼
   └───────────[S_10]───din=1───►[S_101]  ← 偵測到 "101"！
      din=0
```

這張圖說：我在追蹤「有沒有出現 101」。看到 1 進 S_1（收到第一個 1）、再看到 0 進 S_10（收到 10）、再看到 1 就進 S_101（湊齊 101，輸出偵測到）。每個狀態記得「我目前湊到哪」。

## FSM 的硬體結構：Ch 2 那張圖的具體化

FSM 的硬體長什麼樣？就是 Ch 2「組合算 next + flip-flop 存狀態」那張圖，加上輸出邏輯：

```
   輸入 ──►┌─────────────┐  next   ┌──────┐  state  ┌─────────────┐
           │ 次態邏輯     ├────────►│ 狀態  ├────┬───►│ 輸出邏輯     ├──► 輸出
     ┌────►│（組合）      │         │ 暫存器│    │    │（組合）      │
     │     └─────────────┘         │ (FF) │    │    └─────────────┘
     │                             └──▲───┘    │
     │              state（回授）      │        │
     └──────────────────────────────────────────┘
                                     clk
```

三塊：

1. **狀態暫存器（state register）**：一組 flip-flop，記住「現在在哪個狀態」。時序邏輯（`always_ff`）。
2. **次態邏輯（next-state logic）**：組合邏輯，看「當前狀態 + 輸入」算出「下一個狀態」。
3. **輸出邏輯（output logic）**：組合邏輯，算出當前的輸出。

每個時鐘上緣，狀態暫存器把 next 存進去，狀態前進一步。**這就是 FSM 的全部**——組合邏輯決定「怎麼走」，flip-flop 負責「一步步走」。

## Moore vs Mealy：輸出看什麼

FSM 分兩種，差別在「輸出邏輯看什麼」：

- **Moore FSM**：輸出**只看當前狀態**。輸出 = f(state)。
- **Mealy FSM**：輸出**看當前狀態 + 當前輸入**。輸出 = f(state, input)。

差別的後果很實際：

- **Moore 的輸出跟時鐘同步、穩定**（狀態一拍才變一次，輸出跟著），但反應慢一拍（要等狀態轉換完才反映）。
- **Mealy 的輸出反應快一拍**（輸入一變、當拍就可能反映），但輸出可能在一拍之內跟著輸入跳動（帶 glitch 風險），比較難掌控。

用一句話記：**Moore「先變狀態、下一拍才反映到輸出」；Mealy「輸入一來、這拍就可能反映」。** Mealy 通常狀態數更少（省一個狀態），但輸出時序較敏感。CPU 控制單元兩種都會用到，多數教學設計偏好 Moore（時序乾淨好推理）。

## 業界標準寫法：two-block / three-block

FSM 的 SV 寫法有幾種流派。最推薦的是把「時序」和「組合」拆開，避免混用賦值出錯（Ch 2 那個雷）。

**two-block 寫法**：一個 `always_ff`（狀態暫存器）+ 一個 `always_comb`（次態邏輯 + 輸出邏輯合在一起）。

**three-block 寫法**：一個 `always_ff`（狀態）+ 一個 `always_comb`（純次態）+ 一個 `always_comb` 或 `assign`（純輸出）。分得更乾淨。

本課用 two/three-block 混合的清楚寫法。**絕不用 one-block（把所有東西塞進一個 `always_ff`）**——那會逼你在時序區塊裡處理輸出，容易踩阻塞/非阻塞的雷。

## 動手一：Moore 版 "101" 序列偵測器

偵測器持續讀入一串 0/1，每當「最近三個 bit 是 101」就輸出 detected=1。而且要**可重疊**：例如 `10101` 裡有兩個 101（位置 1-3 和 3-5）。

狀態設計（Moore，四個狀態）：

- `S_IDLE`：還沒看到有用的開頭。
- `S_1`：剛看到一個 1（101 的第一位）。
- `S_10`：看到 10（前兩位到位）。
- `S_101`：湊齊 101，**在這個狀態輸出 detected=1**。

轉換要小心處理「重疊」：在 `S_101` 又收到 0 時，不能回 IDLE——因為「這個 0」接在剛才的 1 後面，等於已經有了「10」，所以去 `S_10`。這是序列偵測器最容易錯的地方。

```systemverilog
module seq_detect (
    input  logic clk,
    input  logic rst,
    input  logic din,
    output logic detected
);
    // 用 enum 命名狀態，比裸的 2'b00 好讀太多
    typedef enum logic [1:0] {S_IDLE, S_1, S_10, S_101} state_t;
    state_t state, next;

    // ── 區塊 1：狀態暫存器（時序，用 <=）──
    always_ff @(posedge clk) begin
        if (rst) state <= S_IDLE;
        else     state <= next;
    end

    // ── 區塊 2：次態邏輯（組合，用 =）──
    always_comb begin
        next = state;                          // 預設維持（避免 latch）
        case (state)
            S_IDLE: next = din ? S_1   : S_IDLE;
            S_1:    next = din ? S_1   : S_10;  // 已有 1，再來 1 還是「最新的 1」
            S_10:   next = din ? S_101 : S_IDLE;
            S_101:  next = din ? S_1   : S_10;  // 重疊：101 後來 0 → 已有 10
            default: next = S_IDLE;
        endcase
    end

    // ── 區塊 3：輸出邏輯（Moore：只看 state）──
    assign detected = (state == S_101);
endmodule
```

幾個關鍵點：

- **`typedef enum` 命名狀態**：用 `S_IDLE` 等名字而非裸的 `2'b00`，可讀性天差地遠，也讓工具能檢查你有沒有漏 case。
- **`next = state;` 當開頭預設**：這是避免意外推斷 latch 的關鍵（Ch 1、Ch 4 的雷）。`always_comb` 每條路徑都要給 next 賦值，先給個「維持原狀」的預設最保險。
- **狀態暫存器用 `<=`，次態/輸出邏輯用 `=`**：完美對應 Ch 2 的規則，這也是拆 block 的好處——你不會搞混。

testbench，餵一串 `1 1 0 1 0 1`：

```cpp
#include "Vseq_detect.h"
#include "verilated.h"
#include <cstdio>
int main(int c, char** v) {
    Verilated::commandArgs(c, v);
    Vseq_detect* t = new Vseq_detect;
    auto tick = [&]() { t->clk=0; t->eval(); t->clk=1; t->eval(); };
    t->rst = 1; t->din = 0; tick(); t->rst = 0;
    int seq[] = {1, 1, 0, 1, 0, 1};
    int n = 6;
    printf("in : "); for (int i=0;i<n;i++) printf("%d ", seq[i]); printf("\n");
    printf("det: ");
    for (int i = 0; i < n; i++) { t->din = seq[i]; tick(); printf("%d ", t->detected); }
    printf("\n"); delete t; return 0;
}
```

實際輸出（真跑）：

```
in : 1 1 0 1 0 1 
det: 0 0 0 1 0 1 
```

追一遍：輸入 `1`(→S_1) `1`(→S_1) `0`(→S_10) `1`(→S_101，**detected!**) `0`(→S_10，重疊起作用) `1`(→S_101，**又 detected!**)。第 4 位和第 6 位都偵測到 101，中間那個 0 沒讓它回到起點——重疊處理正確。

## 動手二：Mealy 版對照

同一個偵測器，改用 Mealy——輸出看「狀態 + 當前輸入」。這樣可以少一個狀態（不需要 `S_101`，直接在「已看到 10 且當前輸入是 1」時輸出）：

```systemverilog
module seq_mealy (input logic clk, input logic rst, input logic din, output logic detected);
    typedef enum logic [1:0] {S_IDLE, S_1, S_10} state_t;   // 只要三個狀態
    state_t state, next;

    always_ff @(posedge clk) begin
        if (rst) state <= S_IDLE; else state <= next;
    end

    always_comb begin
        next = state;
        case (state)
            S_IDLE: next = din ? S_1  : S_IDLE;
            S_1:    next = din ? S_1  : S_10;
            S_10:   next = din ? S_1  : S_IDLE;   // 看到 10 再來 1 → 偵測到，同時這個 1 又是新開頭 → S_1
            default: next = S_IDLE;
        endcase
    end

    // Mealy：輸出同時看 state 和 din——在 S_10 且當前 din=1 時「立刻」拉高
    assign detected = (state == S_10) && (din == 1'b1);
endmodule
```

差別看 `detected` 那行：Moore 版是 `state == S_101`（純看狀態），Mealy 版是 `(state == S_10) && din`（看狀態**和**輸入）。Mealy 少了一個狀態，且輸出在「進入 S_101 之前的那一拍、輸入還是 1 時」就反映——理論上快一拍。

跑同樣的 `1 1 0 1 0 1`（testbench 在設好 din、上緣前先 `eval()` 抓 Mealy 的當拍輸出）：

實際輸出（真跑）：

```
in : 1 1 0 1 0 1 
det: 0 0 0 1 0 1 
```

在這個取樣點兩者結果相同，但**機制不同**：Moore 是「狀態走到 S_101 才輸出」，Mealy 是「在 S_10 看到輸入 1 的當拍就輸出」。若你在波形上看，Mealy 的 detected 會比 Moore 早半拍出現（在上緣前就跟著 din 變高），Moore 的則整齊對齊在狀態暫存器更新後。這半拍差異，在把 FSM 接進更大系統時會影響時序，是選 Moore/Mealy 的實際考量。

## 失敗案例：忘了處理重疊

序列偵測器最經典的 bug，是把 `S_101` 的轉換寫成「無論來 0 或 1 都回 IDLE」：

```systemverilog
    S_101:  next = S_IDLE;    // 錯！忽略了 din，把 101 後的 0 浪費掉
```

這樣 `10101` 只會偵測到第一個 101，第二個漏掉——因為偵測到後直接重置，「後半段可重用的 10」被丟了。這種 bug 不會編譯錯、不會警告，只在特定重疊輸入下才錯。**FSM 的 corner case 幾乎都藏在轉換的「偵測到之後往哪走」這一步**。設計 FSM 時，每個狀態的每個輸入分支都要問一句「這個輸入能不能是下一次匹配的開頭」。

## CPU 的控制單元就是一台 FSM

現在點破全章的重點。CPU 怎麼指揮自己「取指 → 解碼 → 執行 → 存記憶體 → 寫回」？——用一台 FSM。

- **狀態**就是執行階段：FETCH、DECODE、EXECUTE、MEMORY、WRITEBACK（多週期 CPU 的經典狀態）。
- **輸入**是指令的 opcode、以及 branch 條件、中斷訊號等。
- **輸出**是控制訊號：這一拍 ALU 要做什麼、要不要寫暫存器、要不要讀記憶體、PC 下一步去哪。

一顆多週期（multi-cycle）CPU 的控制單元，字面上就是一個大 FSM：FETCH 狀態發出「讀記憶體、PC+4」的控制訊號，走到 DECODE，再依 opcode 分岔到不同 EXECUTE 路徑。單週期 CPU（Part 1）把這些壓成一拍，控制邏輯退化成純組合的 decoder；pipeline（Part 2）則是把各階段攤開同時跑。但**「控制 = 根據當前狀態與指令決定發什麼訊號」這個 FSM 本質，貫穿所有微架構**。你在這章學的 two-block 寫法、Moore/Mealy 取捨、狀態編碼，Part 5 做 trap 機制（進出中斷 handler 的狀態轉換）時會原封不動地再用一次。

## 對比：Moore vs Mealy

| 面向 | Moore | Mealy |
|---|---|---|
| 輸出取決於 | 只看狀態 | 狀態 + 當前輸入 |
| 狀態數 | 通常較多 | 通常較少（可省一個） |
| 輸出時序 | 跟時鐘同步、穩定，慢一拍 | 反應快一拍，可能跟輸入跳動 |
| glitch 風險 | 低（輸出來自暫存器後的狀態） | 較高（輸出直通輸入） |
| 好推理程度 | 高 | 較低 |
| 典型用途 | 多數控制器、CPU control | 需要快速反應、想省狀態時 |

心法：**不確定就用 Moore**（時序乾淨、好 debug）；**要省狀態或要當拍反應才考慮 Mealy**。

## 踩雷集錦

1. **「FSM 忘了處理某個狀態的某個輸入，應該沒事」** — 錯誤直覺：以為漏掉的轉換會「維持原狀」或「回起點」。正確認識：在 `always_comb` 的次態邏輯裡漏賦值，會**推斷出 latch**（Ch 1 的雷），行為變得不可預測。解法是開頭放 `next = state;` 當預設，並在 `case` 加 `default`。**每個狀態的每個輸入都要有明確的下一步。**

2. **「序列偵測器偵測到就重置回 IDLE 最乾淨」** — 錯誤直覺：以為匹配成功後從頭開始最簡單。正確認識：這會漏掉**重疊**的匹配（`10101` 只抓到一個）。偵測到之後要問「剛才這個 bit 能不能當下一次匹配的開頭」，正確地轉到中間狀態而非 IDLE。這是 FSM 設計最常見的邏輯錯。

3. **「Moore 和 Mealy 只是寫法不同，功能一樣」** — 錯誤直覺：把兩者當同義詞。正確認識：它們的**輸出時序不同**。Mealy 輸出直通當前輸入、快一拍但可能帶 glitch；Moore 輸出來自狀態、慢一拍但穩定。把 Mealy 的輸出接到對 glitch 敏感的地方（例如當另一個 FSM 的時鐘 enable）會出事。選哪個是時序決策，不是風格。

4. **「用 one-block 把狀態、次態、輸出全塞進一個 always_ff 比較省事」** — 錯誤直覺：以為 block 越少越簡潔。正確認識：那會逼你在時序區塊裡處理組合性質的次態/輸出邏輯，賦值符號容易混用（Ch 2 的災難）。two/three-block 把時序（`<=`）和組合（`=`）物理隔開，是業界標準，出錯機率低得多。多打幾行字換來的是不會默默算錯。

## 本章重點整理

- FSM = 有限狀態 + 轉換規則 + 輸出。硬體結構就是 Ch 2 的「組合算 next + flip-flop 存 state」再加輸出邏輯。
- Moore 輸出只看狀態（同步、穩定、慢一拍）；Mealy 輸出看狀態+輸入（快一拍、可省狀態、但時序敏感）。不確定就用 Moore。
- 業界標準寫法：two-block（`always_ff` 狀態 + `always_comb` 次態/輸出）或 three-block。狀態暫存器用 `<=`，次態/輸出用 `=`。用 `typedef enum` 命名狀態，`always_comb` 開頭放 `next = state;` 防 latch。
- "101" 偵測器：Moore 版四狀態、Mealy 版三狀態，都真跑得 `0 0 0 1 0 1`。重疊處理（偵測後轉到中間狀態而非 IDLE）是關鍵易錯點。
- **CPU 的控制單元本質就是一台 FSM**：狀態=執行階段，輸入=opcode/條件，輸出=控制訊號。Part 5 的 trap 機制會直接重用這套。

## 自我檢核

- [ ] 我能不看講義，畫出 "101" 偵測器的狀態圖（含每個轉換的輸入條件）。
- [ ] 我能說出 FSM 硬體結構的三塊，以及哪塊是時序、哪塊是組合。
- [ ] 我能講清楚 Moore 和 Mealy 的差別，以及各自的輸出時序特性和取捨。
- [ ] 我能解釋序列偵測器為什麼偵測到後不能直接回 IDLE，重疊要怎麼處理。
- [ ] 我能說出 two-block 寫法為什麼比 one-block 安全（跟賦值符號的關係）。
- [ ] 我能用一句話說明「CPU 控制單元是 FSM」是什麼意思，並舉出狀態、輸入、輸出各對應什麼。

## 延伸閱讀

- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 3 章（3.4 Finite State Machines）**：FSM 的紙本主線。Moore/Mealy 的狀態圖、狀態編碼（binary vs one-hot）、以及把狀態圖翻成 HDL 的完整流程。本章的序列偵測器在這裡有交通號誌等更多範例，狀態編碼那段特別值得補（本章刻意簡化用了 enum 讓工具自選）。
- **同書 第 7 章（Microarchitecture）的 multi-cycle processor 那節**：直接印證「CPU 控制單元是 FSM」。它把多週期 RISC-V 的控制器畫成一張完整的 FSM 狀態圖（FETCH→DECODE→…），是本章結尾那個論點的最佳具體化。Part 1 開始前讀它，你會提前看到全課的骨架。
- **HDLBits — Finite State Machines 章節**：大量 FSM 互動練習，從簡單的 `Fsm_serial`（序列偵測）到複雜的多輸入 FSM。特別做序列偵測那幾題，親手處理重疊 corner case。這是把 two-block 寫法練熟的最好場所。
- **nandland.com — "What is a State Machine?"**：給零基礎的 FSM 入門短文，用實體例子（自動販賣機）建立直覺。若本章的抽象狀態圖你想先看一個更生活化的版本，從這篇開始，它和本章的形式化描述互補。

Ch 3 我們把 FSM 這個貫穿全課的結構學會了——它是控制器的骨架。到這裡，數位邏輯的三大塊（組合、時序、FSM）都齊了，你已經有能力理解任何數位電路的「怎麼算、怎麼記、怎麼轉換」。下一章我們回頭把工具磨利：系統性地整理 **SystemVerilog 的語法核心**——`module`/port、`logic`/`wire`/`reg` 到底差在哪、`always_comb`/`always_ff` 的正確用法、以及那些會讓你 debug 到崩潰的常見雷（意外的 latch、不完整的 sensitivity、多重驅動）。把語言吃透，後面寫 CPU 才不會被工具絆倒。

→ [Ch 4 SystemVerilog 語法核心：module / logic / always 與常見雷](./04-systemverilog-core.md)
