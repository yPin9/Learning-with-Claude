# Ch 13 — 存取固定記憶體位址與 memory-mapped I/O

> **目標**：搞懂韌體怎麼用 C 存取硬體暫存器——把整數位址強轉成指標、memory-mapped I/O、為什麼一定要 volatile。這是韌體工程師的核心日常，也是 Nigel Jones 經典題（Q10）。

> **環境**：C，嵌入式（裸機/RTOS）。前置：Ch 3（volatile）、Ch 4（指標）、Ch 7（bit 操作）。

## 為什麼考這個

韌體和一般軟體最大的不同：**直接和硬體對話**。控制一個 GPIO 腳、讀感測器、設定 UART——都是「對某個固定的記憶體位址讀寫」。這個「把硬體當記憶體存取」的能力（memory-mapped I/O）是韌體的基本功，面試必問「怎麼把一個值寫到位址 0x1234」。答得出展現你真的碰過硬體。

## 先建立直覺：硬體暫存器就是「特殊的記憶體位址」

```
   一般記憶體：你讀寫位址 0x2000 = 存取一塊 RAM

   memory-mapped I/O：CPU 把硬體裝置的「暫存器」對映到記憶體位址空間
   位址 0x40000000 = GPIO 輸出暫存器（寫它 = 控制腳位高低）
   位址 0x40000004 = GPIO 狀態暫存器（讀它 = 看腳位狀態）

   → 對韌體來說，控制硬體 = 對特定位址讀寫，跟存取記憶體一樣的語法！
```

關鍵：**硬體暫存器被映射成記憶體位址**，所以你用「存取記憶體」的方式（指標讀寫）就能控制硬體。差別是這些位址「背後是硬體」——寫它會觸發硬體動作、讀它拿到硬體狀態（且值會被硬體在背後改變，所以要 volatile）。

## 核心技巧：整數位址強轉成指標

把一個固定位址（如 `0x40000000`）當指標來讀寫：

```c
// 寫一個值到位址 0x40000000
*(volatile unsigned int *)0x40000000 = 0xABCD;

// 從位址 0x40000004 讀一個值
unsigned int status = *(volatile unsigned int *)0x40000004;
```

拆解 `*(volatile unsigned int *)0x40000000`：
1. `0x40000000` 是一個整數（位址值）。
2. `(volatile unsigned int *)` 把它強轉成「指向 volatile unsigned int 的指標」。
3. `*` 解參照——讀寫那個位址的 4 個 byte。

**三個關鍵決策**：
- **`unsigned int *`**：暫存器通常是 32-bit、且是無號的位元集合（用 unsigned 避免有號數的坑，Ch 9）。位址要轉成「指向正確寬度型別」的指標（8-bit 暫存器用 `unsigned char *`，32-bit 用 `unsigned int *` 或 `uint32_t *`）。
- **`volatile`**：**必須！** 硬體會在背後改暫存器的值，且讀寫暫存器有副作用（觸發硬體動作）。沒有 volatile，編譯器可能把讀寫最佳化掉（快取、合併、消除）→ 硬體控制失效（Ch 3）。
- **位址值**：來自晶片的 datasheet / reference manual（每個暫存器的位址是硬體規格定義的）。

## 常見的暫存器存取慣用法

韌體常用 `#define` 把暫存器位址命名（可讀性 + 集中管理）：

```c
#define GPIO_BASE      0x40000000u
#define GPIO_OUT       (*(volatile unsigned int *)(GPIO_BASE + 0x00))
#define GPIO_IN        (*(volatile unsigned int *)(GPIO_BASE + 0x04))
#define GPIO_DIR       (*(volatile unsigned int *)(GPIO_BASE + 0x08))

// 用起來像普通變數，但其實在操作硬體：
GPIO_DIR |= (1u << 5);     // 設第 5 腳為輸出（set bit，Ch 7）
GPIO_OUT |= (1u << 5);     // 第 5 腳輸出高電位
GPIO_OUT &= ~(1u << 5);    // 第 5 腳輸出低電位
if (GPIO_IN & (1u << 3))   // 讀第 3 腳是不是高電位
    ...
```

注意這裡 **bit 操作（Ch 7）和 memory-mapped I/O 結合**——韌體控制硬體 = 對暫存器位址做 set/clear/test bit。這是最真實的韌體 code 樣貌。

進階：用 struct 描述一組暫存器（CMSIS 風格，ARM 常見）：

```c
typedef struct {
    volatile uint32_t OUT;    // offset 0x00
    volatile uint32_t IN;     // offset 0x04
    volatile uint32_t DIR;    // offset 0x08
} GPIO_TypeDef;

#define GPIO ((GPIO_TypeDef *)0x40000000)
GPIO->DIR |= (1u << 5);       // 用 struct 成員存取，可讀性好
```

用 struct 把「一組連續的暫存器」映射成結構成員——前提是 struct 佈局要和硬體一致（成員順序、對齊、不能有意外 padding，Ch 8；所以每個成員都 volatile、且通常確認無 padding）。

## 考古題詳解

### Q1（Nigel Jones Q10）寫一段 code，把值 0xABCD 寫到絕對位址 0x67A9

<details>
<summary>詳解</summary>

```c
*(volatile unsigned int *)0x67A9 = 0xABCD;
```

或分兩步較清楚：

```c
volatile unsigned int *ptr = (volatile unsigned int *)0x67A9;
*ptr = 0xABCD;
```

三要素：(1) 把整數位址強轉成指標；(2) **volatile**（硬體暫存器）；(3) 正確的型別寬度（這裡假設 32-bit unsigned int）。

**考點**：memory-mapped I/O 的核心寫法，韌體必考（Nigel Jones 原題）。
</details>

### Q2：為什麼存取硬體暫存器的指標一定要 volatile？

<details>
<summary>詳解</summary>

兩個原因（Ch 3）：
1. **硬體會在背後改它的值**：讀一個狀態暫存器，值由硬體更新。沒 volatile，編譯器可能讀一次就快取在暫存器、之後用舊值——看不到硬體的新狀態。
2. **讀寫有副作用**：寫一個暫存器會觸發硬體動作（如清中斷旗標、發送資料）。沒 volatile，編譯器可能認為「寫了沒人讀，最佳化掉」——硬體動作就沒發生。

volatile 強制「每次都真的讀寫記憶體（暫存器），不准最佳化」。

**考點**：volatile 在 memory-mapped I/O 的必要性，超高頻。
</details>

### Q3：怎麼設定位址 0x40000000 的暫存器的第 3 個 bit 為 1，不影響其他 bit？

<details>
<summary>詳解</summary>

```c
*(volatile unsigned int *)0x40000000 |= (1u << 3);
```

用 `|= (1u << 3)`（set bit，Ch 7）——只設第 3 位，其他位 OR 0 不變。注意 volatile + bit 操作的結合。

陷阱：這是 read-modify-write（讀暫存器→改→寫回），不是原子的。如果這個暫存器也會被中斷改，可能有 race（Ch 14/15）——某些硬體提供專門的 set/clear 暫存器（寫 1 到 SET 暫存器只設對應 bit）避免 RMW。

**考點**：memory-mapped I/O + bit 操作 + RMW 陷阱。
</details>

### Q4：一個 8-bit 的暫存器在位址 0x50，怎麼讀它？

<details>
<summary>詳解</summary>

```c
unsigned char val = *(volatile unsigned char *)0x50;
```

關鍵：8-bit 暫存器要用 `unsigned char *`（1 byte），不是 `unsigned int *`（會讀 4 byte，錯誤存取相鄰暫存器！）。**型別寬度要對應暫存器寬度**——這是 memory-mapped I/O 的細節，答對展現你懂硬體。

**考點**：指標型別寬度要對應暫存器寬度。
</details>

## 踩雷集錦

1. **存取暫存器的指標忘了 volatile**：編譯器最佳化掉讀寫 → 硬體控制失效、讀到舊狀態。**必加 volatile**。
2. **指標型別寬度不對**：8-bit 暫存器用 `int *`（讀 4 byte）會誤存取相鄰暫存器。型別要對應暫存器寬度。
3. **直接寫整個暫存器而非 bit 操作**：`*reg = (1<<3)` 會把其他 bit 清 0！要 `|= (1<<3)`（set）或 `&= ~(...)`（clear）保留其他位。
4. **RMW 的原子性**：`reg |= bit` 是讀-改-寫三步，若中斷也改同暫存器有 race。用硬體的 SET/CLEAR 暫存器或關中斷（Ch 14/15）。
5. **位址寫錯/沒對齊**：位址來自 datasheet；32-bit 存取通常要 4-byte 對齊，未對齊在某些架構會 fault。
6. **用 signed**：暫存器是位元集合，用 unsigned（避免有號移位/比較的坑，Ch 9）。

## 速記

- memory-mapped I/O：硬體暫存器映射成記憶體位址，用指標讀寫 = 控制硬體。
- 核心寫法：`*(volatile unsigned int *)0xADDR = value;`（整數位址強轉指標 + 解參照）。
- 三要素：**volatile**（必須，硬體背後改+副作用）、**正確型別寬度**（對應暫存器位寬）、**位址**（來自 datasheet）。
- 操作特定 bit 用 set/clear/test（Ch 7），別整個寫（會清掉其他 bit）。
- RMW（`reg |= bit`）非原子，和中斷共用暫存器有 race（Ch 14/15）。

## 自我檢核

- [ ] 不看，能寫出「把 0xABCD 寫到位址 0x67A9」嗎？三個要素是什麼？
- [ ] 為什麼存取硬體暫存器一定要 volatile？不加會怎樣？
- [ ] 8-bit 暫存器用 `int *` 讀會有什麼問題？
- [ ] 怎麼設某暫存器的第 N 位、不影響其他位？這有什麼原子性風險？

## 延伸閱讀

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q10（存取固定記憶體位址）、Q8（volatile）。
  - **和本章的關聯**：本章核心考點的源頭（Nigel Jones Q10）。

- **[Memory-Mapped I/O — Barr Group / embedded.com](https://barrgroup.com/embedded-systems/how-to/c-volatile-keyword)**
  - **讀哪裡**：volatile + 暫存器存取那段。
  - **和本章的關聯**：memory-mapped I/O 與 volatile 的嵌入式權威。

### 書籍

- **《Making Embedded Systems》** — Elecia White
  - **讀哪幾章**：談 register、memory-mapped I/O、與硬體互動的章節。
  - **為什麼值得讀**：嵌入式韌體實務的好書，把「用 C 控硬體」講得很實際。

存取硬體會了，下一章是韌體的另一核心——中斷與 ISR，ISR 設計準則是必考評論題。

→ [Ch 14 中斷與 ISR](./14-interrupts-isr.md)
