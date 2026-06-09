# Ch 14 — 中斷與 ISR

> **目標**：搞懂中斷（interrupt）的機制與 ISR（中斷服務常式）的設計準則——ISR 不能做什麼、為什麼、怎麼和主程式安全溝通。「評論這個 ISR 寫得對不對」是 Nigel Jones 經典必考題（Q11）。

> **環境**：C，嵌入式。前置：Ch 3（volatile）、Ch 13（memory-mapped I/O）。

## 為什麼考這個

中斷是嵌入式系統「即時回應硬體事件」的核心機制——按鈕按下、資料到達、計時器到時，都靠中斷。ISR 寫不好會造成系統卡死、資料遺失、難以 debug 的詭異 bug。所以面試必考「ISR 該怎麼寫、不該做什麼」——這直接反映你有沒有真正的嵌入式經驗。

## 先建立直覺：中斷是「硬體打斷 CPU 說『有急事』」

```
   CPU 正在跑主程式...
        │
   硬體事件發生（按鈕、資料到、計時器到）→ 發出中斷訊號
        │
   CPU 立刻：
   1. 暫停主程式、保存現場（暫存器、PC 壓進 stack）
   2. 跳去執行對應的 ISR（中斷服務常式）
   3. ISR 處理完、回復現場
   4. 回到主程式被打斷的地方繼續
```

對比 polling（輪詢）——主程式一直問「有事嗎？有事嗎？」（浪費 CPU）。中斷是「沒事你做你的，有事我叫你」（效率高、即時）。這跟 Ch 對照的 epoll 直覺類似。

關鍵特性：**ISR 是「非同步」被觸發的——它可能在主程式的任何一行之間插進來執行。** 這是 ISR 所有設計準則的根源。

## 中斷向量表

CPU 怎麼知道「哪個中斷對應哪個 ISR」？靠 **中斷向量表（interrupt vector table）**——一張「中斷號 → ISR 函式位址」的表。

```
   中斷向量表（本質是函式指標陣列，Ch 5！）
   中斷號  ISR 位址
   0       reset_handler
   1       nmi_handler
   ...
   16      timer_isr        ← 計時器中斷觸發時，CPU 查表跳到這
   17      uart_isr
```

中斷發生時，CPU 用中斷號查表、跳到對應 ISR。這就是 Ch 5「函式指標陣列」的真實應用——中斷向量表是一個函式指標陣列。

## ISR 設計準則（必考）

因為 ISR 是「非同步插入、要快速返回、執行環境受限」，有嚴格的設計規則：

```
   ISR 不應該/不能：
   1. 不能有回傳值        ← 沒人「呼叫」它（硬體觸發），回傳值給誰？
   2. 不能接收參數        ← 同上，硬體不傳參數
   3. 不要呼叫 printf     ← printf 慢、可能用 buffer/鎖、不可重入 → 拖垮即時性、可能 deadlock
   4. 不要用浮點運算（多數）← 浮點暫存器可能沒被保存、慢；許多嵌入式 ISR 禁浮點
   5. 不要做耗時的事       ← ISR 要快進快出（其他中斷被擋、主程式被卡）
   6. 不要呼叫不可重入函式 ← malloc、strtok 等（Ch 15）
   7. 不要 busy-wait / 阻塞 ← 會卡死整個系統
```

核心原則：**ISR 要「快進快出」，只做最緊急的事（讀資料、設旗標），剩下的丟給主程式做。**

典型的好 ISR 模式：

```c
volatile int data_ready = 0;        // 和主程式溝通的旗標（必 volatile！）
volatile unsigned char rx_data;

void uart_isr(void) {               // 無回傳、無參數
    rx_data = UART_DATA_REG;        // 1. 快速讀走資料（不讀會遺失）
    data_ready = 1;                 // 2. 設旗標通知主程式
    UART_CLEAR_IRQ();               // 3. 清中斷旗標（不清會一直觸發）
    // 就這樣，快速返回。複雜處理交給主程式：
}

int main(void) {
    while (1) {
        if (data_ready) {           // 主程式看旗標
            data_ready = 0;
            process(rx_data);       // 耗時的處理在這做（不在 ISR）
        }
    }
}
```

ISR 只做「讀資料 + 設旗標 + 清中斷」三件快事，把 `process()`（可能慢、可能用 printf）留給主程式。

## ISR 與主程式的溝通：volatile + 原子性

ISR 和主程式共享的變數（如上面的 `data_ready`、`rx_data`），有兩個必須注意：

1. **必須 volatile**（Ch 3）：ISR 在背後改它，主程式的迴圈要每次重讀（否則編譯器快取舊值 → 主程式永遠看不到 ISR 的修改 → 卡死）。

2. **原子性問題**：如果共享變數是「多 byte、或 read-modify-write」，主程式讀到一半被 ISR 打斷改掉 = 讀到不一致的值（race condition，Ch 15）。

```c
volatile uint32_t counter;       // 32-bit，在 8-bit MCU 上讀寫不是原子的！

void timer_isr(void) { counter++; }   // ISR 改 counter

int main(void) {
    uint32_t snapshot = counter;       // 在 8-bit MCU 上，讀 32-bit 要 4 次 byte 讀
                                       // 讀到一半被 ISR 打斷改 counter → snapshot 不一致！
}
```

解法：讀共享的多 byte 變數時**暫時關中斷**（critical section）：

```c
disable_interrupts();
uint32_t snapshot = counter;     // 原子地讀
enable_interrupts();
```

或用硬體支援的 atomic 操作。這串到 Ch 15（reentrancy）和 Ch 22（OS 同步）。

## 考古題詳解

### Q1（Nigel Jones Q11）評論這個 ISR 寫得對不對

```c
__interrupt double compute_area(double radius) {
    double area = 3.14159 * radius * radius;
    printf("\nArea = %f", area);
    return area;
}
```

<details>
<summary>詳解</summary>

**這個 ISR 至少有四個問題**：

1. **有回傳值（`double`）**：ISR 由硬體觸發、沒人「呼叫」它，回傳值無處可去——不該有回傳值（void）。
2. **有參數（`double radius`）**：硬體中斷不傳參數——ISR 不該有參數。
3. **用浮點運算**：很多嵌入式系統 ISR 禁浮點（浮點暫存器可能沒保存、慢）——危險。
4. **呼叫 printf**：printf 慢、可能不可重入、用 buffer/鎖——在 ISR 裡會拖垮即時性、甚至 deadlock。

正確的 ISR 應該：`void`、無參數、不用浮點、不呼叫 printf、快進快出。

**考點**：ISR 設計準則，超經典必考（Nigel Jones Q11）。能講出 4 個問題 = 滿分。
</details>

### Q2：ISR 和主程式共享的變數為什麼要 volatile？

<details>
<summary>詳解</summary>

因為 ISR 非同步地在背後改它，而主程式（如 `while(!flag)`）裡編譯器看不到「ISR 會改 flag」——可能把 flag 快取在暫存器、不再讀記憶體，導致 ISR 改了 flag 主程式卻看不到 → 卡死。volatile 強制主程式每次重讀（Ch 3 三場景之一）。

**考點**：ISR 共享變數 + volatile，高頻。
</details>

### Q3：為什麼 ISR 不能呼叫 printf？

<details>
<summary>詳解</summary>

幾個原因：
1. **慢**：printf 要格式化、I/O，耗時——ISR 要快進快出，慢會卡住其他中斷和主程式。
2. **不可重入 / 用全域 buffer 或鎖**（Ch 15）：如果主程式正在 printf 時被中斷、ISR 又 printf，可能損壞 printf 的內部狀態、或 deadlock（搶同一個鎖）。
3. **可能阻塞**：等 I/O 完成。

ISR 要 debug 改用「設旗標讓主程式印」或專用的 ISR-safe log（如寫進 ring buffer，主程式再印）。

**考點**：ISR 不能 printf 的原因（快、可重入、阻塞）。
</details>

### Q4：ISR 處理完忘了清中斷旗標會怎樣？

<details>
<summary>詳解</summary>

**中斷會一直重複觸發**——多數硬體中斷需要 ISR 手動清除中斷旗標（acknowledge），告訴硬體「我處理了」。沒清，硬體認為中斷還在 pending，ISR 返回後立刻又被觸發 → 無窮進 ISR、主程式跑不動（卡死在 ISR）。

**考點**：ISR 要清中斷旗標，實務細節。
</details>

### Q5：中斷和 polling 的差別？各適合什麼？

<details>
<summary>詳解</summary>

| | polling（輪詢） | interrupt（中斷） |
|---|---|---|
| 機制 | 主程式一直問「有事嗎」 | 有事硬體才叫 CPU |
| CPU 效率 | 低（一直忙問） | 高（沒事做別的/睡眠） |
| 即時性 | 取決於輪詢頻率 | 快（立即回應） |
| 複雜度 | 簡單 | 較複雜（ISR、同步） |
| 適合 | 簡單、頻繁、可預測的事件 | 偶發、需即時回應、省電 |

低功耗系統（Ch 19）尤其偏好中斷——沒事就睡眠，中斷喚醒。

**考點**：interrupt vs polling，常考。
</details>

## 踩雷集錦

1. **ISR 有回傳值/參數**：硬體觸發，沒人呼叫——void、無參數。
2. **ISR 裡 printf / 浮點 / 耗時操作**：拖垮即時性、可能 deadlock/不可重入。ISR 快進快出，複雜的丟主程式。
3. **ISR 共享變數忘了 volatile**：主程式看不到 ISR 的修改，卡死。
4. **共享多 byte 變數不管原子性**：主程式讀到一半被 ISR 改 → 不一致。關中斷讀（critical section）或用 atomic。
5. **ISR 忘了清中斷旗標**：中斷一直重複觸發，系統卡死。
6. **ISR 裡 busy-wait / 阻塞**：卡死整個系統（其他中斷、主程式都動不了）。
7. **以為 ISR 像普通函式**：它非同步插入、執行環境受限、不可重入要求——完全不同。

## 速記

- 中斷 = 硬體打斷 CPU、跳去執行 ISR、處理完回來；比 polling 省 CPU、即時。
- 中斷向量表 = 函式指標陣列（中斷號 → ISR 位址，Ch 5）。
- **ISR 準則**：void + 無參數、不 printf、不浮點、不耗時、不阻塞、不呼叫不可重入函式——**快進快出**。
- 好模式：ISR 只「讀資料 + 設旗標 + 清中斷」，耗時處理丟主程式。
- ISR 共享變數要 **volatile**（Ch 3）+ 注意**原子性**（多 byte/RMW 要關中斷或 atomic，Ch 15）。

## 自我檢核

- [ ] ISR 的設計準則有哪些？（至少講 4 個：void/無參、不 printf、不浮點、快進快出）
- [ ] 看到 `__interrupt double f(double r){ ...printf...return... }` 你能指出幾個問題？
- [ ] ISR 和主程式共享變數為什麼要 volatile？多 byte 變數還要注意什麼？
- [ ] ISR 忘了清中斷旗標會怎樣？
- [ ] interrupt vs polling 的差別與適用情境？

## 延伸閱讀

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q11（ISR 函式設計評論）、Q8（volatile）。
  - **和本章的關聯**：本章 ISR 評論題的源頭（Nigel Jones Q11）。

### 書籍

- **《Making Embedded Systems》** — Elecia White — Interrupts 章
  - **讀哪幾章**：中斷與 ISR 章。
  - **為什麼值得讀**：ISR 設計、與主程式溝通、原子性的實務講解。

- **《Programming Embedded Systems》** — Barr & Massa — Interrupts 章
  - **和本章的關聯**：中斷機制與 ISR 的經典嵌入式教材。

ISR 講完，自然帶出下一個關鍵概念——reentrancy（可重入），ISR 和多工環境下哪些函式能安全共用。

→ [Ch 15 reentrancy 與 thread-safe](./15-reentrancy-thread-safe.md)
