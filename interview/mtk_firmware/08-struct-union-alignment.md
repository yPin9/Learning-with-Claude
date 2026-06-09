# Ch 8 — struct / union / enum 與記憶體對齊

> **目標**：搞懂 struct 的記憶體對齊（padding）與 `sizeof` 計算、union 的共用記憶體與用途（含測 endian）、enum 的本質、以及 `#pragma pack`。「這個 struct 的 sizeof 是多少」是必考計算題。

> **環境**：C，`gcc -Wall`，64-bit。前置：Ch 4（指標/sizeof）。

## 為什麼考這個

「算 struct 的 sizeof」是 C 面試的經典計算題——它測你懂不懂**記憶體對齊（alignment/padding）**，而對齊直接影響韌體的記憶體佈局、暫存器映射、跨平台資料交換。union 測 endian 更是韌體招牌題。答錯 sizeof 等於不懂編譯器怎麼擺資料。

## 先建立直覺：對齊是為了 CPU 讀得快

```
   CPU 讀記憶體是「以字組（word）為單位」抓的，不是一個 byte 一個 byte。
   如果一個 4-byte int 跨在兩個 word 邊界上，CPU 要讀兩次再拼 → 慢（甚至某些
   架構直接禁止、會 crash）。

   所以編譯器把每個成員放在「它大小的倍數位址」上（自然對齊），
   不夠的地方塞 padding（填充 byte）補齊。
```

對齊規則（自然對齊，多數平台預設）：

- 每個成員放在「自身大小的倍數」位址（int 放 4 的倍數、double 放 8 的倍數、char 任意）。
- struct 整體大小是「最大成員對齊需求」的倍數（結尾補 padding）。

## struct sizeof：手算範例

```c
struct A {
    char  c;     // 1 byte，offset 0
    int   i;     // 4 bytes，要對齊 4 → offset 4（offset 1-3 是 padding）
    char  d;     // 1 byte，offset 8
};               // 目前用到 offset 9，但要對齊最大成員(int=4)的倍數 → 補到 12
// sizeof(A) = 12
```

排列圖：

```
   offset:  0    1  2  3    4  5  6  7    8    9  10 11
   內容:    c   [padding]   i (4 bytes)   d   [padding]
            └1┘ └──3──┘     └────4────┘   └1┘ └──3──┘
   total = 12（= 最大成員 4 的倍數）
```

**重排成員可省空間**：

```c
struct B {
    int  i;      // offset 0-3
    char c;      // offset 4
    char d;      // offset 5
};               // offset 6，補到 8（4 的倍數）
// sizeof(B) = 8  ← 比 A 的 12 省！
```

同樣的成員，**把大的放前面、小的放後面**（或相近大小放一起）能減少 padding。這是韌體省記憶體的實用技巧，也是考古題愛問「怎麼讓這個 struct 更小」。

更複雜的例子：

```c
struct C {
    char  c;      // offset 0
    double d;     // 對齊 8 → offset 8（1-7 padding）
    int   i;      // offset 16-19
};                // offset 20，補到 24（8 的倍數，因 double 對齊 8）
// sizeof(C) = 24
```

## union：所有成員共用同一塊記憶體

```c
union U {
    int   i;      // 4 bytes
    char  c[4];   // 4 bytes
    short s;      // 2 bytes
};
// sizeof(U) = 4（最大成員的大小，不是相加！）
```

union 的所有成員**從同一個位址開始、共用記憶體**——同時只有一個成員有意義，寫了 `i` 再讀 `c` 是讀同一塊 byte 的不同詮釋。`sizeof(union)` = 最大成員的大小（+ 對齊）。

union 用途：

1. **省記憶體**：同一塊記憶體在不同時候存不同型別（韌體 memory-constrained 常用）。
2. **型別雙關（type punning）**：用不同型別看同一塊 byte——例如**測 endian**（下面）。
3. **變體型別**：配一個 tag 表示「現在裝的是哪種」（discriminated union）。

## 用 union 測 endianness（韌體招牌題）

```c
int is_little_endian(void) {
    union {
        int i;
        char c;
    } u;
    u.i = 1;
    return u.c == 1;   // little endian：低位 byte 在低位址，c（第一個 byte）= 1
}
```

原理：`int i = 1` 的 4 個 byte 是 `01 00 00 00`（little）或 `00 00 00 01`（big）。`u.c` 讀第一個 byte（最低位址）——little endian 得 1、big endian 得 0。union 讓 `c` 和 `i` 共用記憶體，所以 `c` 看到 `i` 的第一個 byte。

（Ch 16 會把 endian 整個講一遍，這裡先當 union 的應用。）

## enum：具名整數常數

```c
enum Color { RED, GREEN, BLUE };       // RED=0, GREEN=1, BLUE=2（自動遞增）
enum Status { OK = 0, ERR = -1, BUSY = 5, NEXT };   // NEXT = 6（接續 BUSY+1）
```

要點：

- enum 本質是 **int 常數**（具名的整數），預設從 0 遞增，可指定值。
- 比 `#define` 好的地方：有作用域、debugger 看得到名字、編譯器可做型別檢查（弱）。
- `sizeof(enum)` 通常是 `sizeof(int)`（implementation-defined，但多半 4）。

## #pragma pack：取消對齊

```c
#pragma pack(1)        // 取消 padding，1-byte 對齊
struct Packed {
    char c;            // offset 0
    int  i;            // offset 1（沒 padding！）
};                     // sizeof = 5（不是 12）
#pragma pack()         // 恢復預設
```

`#pragma pack(1)` 讓編譯器不補 padding（緊密排列）。用途：**和硬體/網路協定交換資料時**，封包格式是固定的 byte 佈局，不能有 padding（否則對不上）。代價：存取未對齊成員較慢（或某些架構不允許）。

> 認識論誠實：`#pragma pack` 是編譯器擴充（非 C 標準，但 GCC/Clang/MSVC 都支援）。對齊行為、padding 規則是 implementation-defined——上面的範例是常見平台（x86/ARM 64-bit）的典型結果，不同編譯器/架構可能不同。面試講「一般情況」並說明對齊原則即可。

## 考古題詳解

### Q1：算這個 struct 的 sizeof（64-bit）

```c
struct S {
    char  a;
    int   b;
    char  c;
    double d;
};
```

<details>
<summary>詳解</summary>

```
a: offset 0           (char, 1 byte)
   offset 1-3 padding (b 要對齊 4)
b: offset 4-7         (int, 4 bytes)
c: offset 8           (char, 1 byte)
   offset 9-15 padding(d 要對齊 8)
d: offset 16-23       (double, 8 bytes)
total = 24（已是 8 的倍數，不用補）
```
**sizeof(S) = 24**。

如果重排成 `double d; int b; char a; char c;` → 8+4+1+1=14 補到 16 → **sizeof = 16**（省 8）。

**考點**：對齊計算 + 重排省空間，必考計算題。
</details>

### Q2：union 的 sizeof？

```c
union U { char c[5]; int i; double d; };
```

<details>
<summary>詳解</summary>

**sizeof = 8**。union 大小 = 最大成員（double = 8），且整體對齊到最大成員對齊需求（8）。`c[5]` 只 5 byte、`i` 4 byte，但 union 取最大 + 對齊 = 8。

對比：若是 struct，sizeof 會是各成員相加 + padding（大得多）。

**考點**：union 共用記憶體（取最大，不相加）。
</details>

### Q3：寫一個函式判斷系統是 big 還是 little endian

<details>
<summary>詳解</summary>

```c
int is_little_endian(void) {
    union { int i; char c; } u;
    u.i = 1;
    return u.c == 1;   // little: 1, big: 0
}
// 或用指標：
int is_little_endian2(void) {
    int x = 1;
    return *((char *)&x) == 1;
}
```

兩種寫法（union 或 char* 強轉）都是看「int=1 的第一個 byte 是不是 1」。

**考點**：endian 偵測，韌體必考（Ch 16 深入）。
</details>

### Q4：`#pragma pack(1)` 後上面 Q1 的 struct sizeof？

<details>
<summary>詳解</summary>

`#pragma pack(1)` 取消 padding：`char(1) + int(4) + char(1) + double(8) = 14`，**sizeof = 14**（緊密排列，無 padding）。

用途：和固定 byte 佈局的硬體/協定交換資料。代價：未對齊存取較慢。

**考點**：pack 取消對齊，連結「為什麼協定/暫存器佈局要 pack」。
</details>

## 踩雷集錦

1. **算 sizeof 忘了 padding**：以為是成員相加。要算對齊與 padding（每成員對齊自身大小、整體對齊最大成員）。
2. **以為 union sizeof 是成員相加**：是最大成員（+對齊），因為共用記憶體。
3. **忘了 struct 結尾 padding**：整體要補到「最大成員對齊」的倍數。
4. **跨平台傳 struct 不 pack / 不管對齊**：不同平台對齊不同，直接傳 struct 二進位會對不上。協定要 pack 或逐欄位序列化。
5. **以為對齊規則是 C 標準保證的固定值**：是 implementation-defined。原則（自然對齊）通用，但具體數字依平台。
6. **enum 當成獨立型別有強型別檢查**：C 的 enum 本質是 int，型別檢查很弱（C++ 較強）。

## 速記

- **對齊**：每成員放「自身大小倍數」位址，不足補 padding；struct 整體大小 = 最大成員對齊的倍數。
- **省空間**：大成員放前、小的放後，減少 padding。
- **union sizeof = 最大成員（+對齊）**，共用記憶體，同時只一個有效。
- **測 endian**：`union{int i;char c;} u; u.i=1; return u.c==1;`（或 `*(char*)&x==1`）。
- **enum** = 具名 int 常數（預設 0 遞增）。
- **`#pragma pack(1)`** 取消 padding（協定/硬體佈局用），代價是未對齊存取。

## 自我檢核

- [ ] 不看，能手算 `struct{char;int;char;double;}` 的 sizeof 嗎？怎麼重排省空間？
- [ ] union 的 sizeof 怎麼算？為什麼不是成員相加？
- [ ] 怎麼用 union（或指標）測 endian？原理是什麼？
- [ ] `#pragma pack(1)` 做什麼？什麼時候要用？代價是什麼？
- [ ] 為什麼跨平台傳 struct 二進位資料有風險？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — §3.9 Heterogeneous Data Structures
  - **讀哪裡**：3.9.1 Structures、3.9.3 Data Alignment。
  - **和本章的關聯**：對齊與 struct 佈局的權威，含為什麼要對齊。

### 文章

- **[The Lost Art of Structure Packing](http://www.catb.org/esr/structure-packing/)** — Eric S. Raymond
  - **這篇說什麼**：struct padding 的完整原理與「重排省空間」的實務。
  - **讀哪裡**：整篇；本章對齊主題的最佳延伸。
  - **為什麼值得讀**：把 alignment/padding 講到能手算任何 struct。

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：struct/union/endian 相關題。
  - **和本章的關聯**：MTK 風格的 struct/union 考題。

資料佈局懂了，下一章是上機考超愛的陷阱——型別轉換與整數，`-20 + 6u` 為什麼變超大正數。

→ [Ch 9 型別轉換與整數陷阱](./09-type-conversion-integer.md)
