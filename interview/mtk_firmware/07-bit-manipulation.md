# Ch 7 — 位元運算

> **目標**：把位元運算練到反射——set/clear/toggle/test bit、判 2 的次方、count bits、XOR swap、判 3 的倍數、bitfield。**韌體天天操作硬體暫存器的 bit，這是上機考與技術面的超高頻考點。**

> **環境**：C，`gcc -Wall`。前置：Ch 3（volatile，操作暫存器用）。

## 為什麼考這個

韌體工程師的日常就是「設定某個暫存器的第 3 個 bit、清掉第 5 個 bit、檢查第 0 個 bit 有沒有被硬體設起來」。位元運算是這份工作的基本功，所以面試幾乎必考——而且愛考「不用 `if`、不用乘除」的技巧題（判 2 次方、判 3 倍數、swap）。

## 先建立直覺：六個位元運算子

```
   &   AND    兩個都 1 才 1     →  用來「測試 / 清除（配 ~）」特定 bit
   |   OR     有一個 1 就 1     →  用來「設定」特定 bit
   ^   XOR    不同才 1          →  用來「反轉」特定 bit、swap
   ~   NOT    每個 bit 反轉     →  做 mask（遮罩）
   <<  左移   ×2^n（低位補 0）  →  做 mask（1<<n）、快速乘 2^n
   >>  右移   ÷2^n              →  快速除 2^n（注意有號數行為）
```

核心工具是 **mask（遮罩）**：`1 << n` 產生「第 n 位是 1、其他是 0」的數，拿它去 AND/OR/XOR 操作特定 bit。記住這四個慣用法，bit 題大半就破。

## 四個必背慣用法（set/clear/toggle/test）

操作「第 n 個 bit」（bit 0 是最低位）：

```c
unsigned int x;

x |=  (1u << n);    // SET   ：設定第 n 位為 1（其他不變）
x &= ~(1u << n);    // CLEAR ：清除第 n 位為 0（其他不變）—— ~ 做出「只有第 n 位是 0」的 mask
x ^=  (1u << n);    // TOGGLE：反轉第 n 位（其他不變）
(x &  (1u << n))    // TEST  ：取出第 n 位（非 0 表示該位是 1）
!!(x & (1u << n))   // TEST 轉成 0/1
```

逐一理解：

- **SET（`|=`）**：`1<<n` 第 n 位是 1，OR 上去 → 第 n 位變 1，其他位 OR 0 不變。
- **CLEAR（`&= ~`）**：`~(1<<n)` 第 n 位是 0、其他是 1，AND 下去 → 第 n 位變 0，其他位 AND 1 不變。
- **TOGGLE（`^=`）**：`1<<n` 第 n 位是 1，XOR → 第 n 位反轉，其他位 XOR 0 不變。
- **TEST（`&`）**：AND 後只留第 n 位，非 0 即該位是 1。

> 為什麼用 `1u`（unsigned）：`1 << 31` 在 `int`（32-bit signed）是未定義行為（移進符號位）。位元操作一律用 unsigned（`1u` 或 `1UL`），避免有號數的坑（Ch 9）。這是面試加分細節。

寫成巨集（韌體常見、考古題愛考）：

```c
#define SET_BIT(x, n)    ((x) |=  (1u << (n)))
#define CLEAR_BIT(x, n)  ((x) &= ~(1u << (n)))
#define TOGGLE_BIT(x, n) ((x) ^=  (1u << (n)))
#define TEST_BIT(x, n)   (((x) >> (n)) & 1u)
```

（注意巨集括號，Ch 6。）

## 技巧題（不用 if / 不用乘除）

### 判斷是不是 2 的次方

```c
int is_power_of_two(unsigned int x) {
    return x != 0 && (x & (x - 1)) == 0;
}
```

原理：2 的次方只有一個 bit 是 1（如 `1000`）。減 1 會把那個 1 變 0、後面全變 1（`0111`）。兩者 AND = 0。非 2 次方則 AND 非 0。`x != 0` 排除 0（0 不是 2 次方，但 `0 & -1 = 0` 會誤判）。

`x & (x-1)` 的另一個用途：**清掉最低位的 1**（Brian Kernighan 演算法，用於 count bits）。

### count bits（數有幾個 1，popcount）

```c
int count_bits(unsigned int x) {
    int count = 0;
    while (x) {
        x &= (x - 1);    // 每次清掉最低位的 1
        count++;
    }
    return count;
}
```

`x &= (x-1)` 每次消去一個 1，迴圈幾次就有幾個 1（只跑「1 的個數」次，比逐位檢查快）。

### XOR swap（不用暫存變數交換）

```c
void swap(int *a, int *b) {
    if (a == b) return;     // 重要！a==b 時會把自己 XOR 成 0
    *a ^= *b;
    *b ^= *a;
    *a ^= *b;
}
```

原理：XOR 三次互換。**陷阱**：`a == b`（指向同一個）時，三次 XOR 會把它變 0！所以要先檢查。（實務上 XOR swap 沒比暫存變數快、還有陷阱，但面試愛考原理。）

### 判斷 3 的倍數（不用 % 和 /）

MTK 考古題。一個方法：把 32-bit 數每 2 bit 一組，用查表或位元統計。簡單可接受的答案是說明思路（如用「奇數位 1 的數 - 偶數位 1 的數」是否為 3 倍數，類似十進位判 11 倍數）或老實說「位元法較複雜，一般用 `% 3 == 0`」。面試重點是展現你理解「為什麼能不用除法」的思路。

```c
// 一個常見的位元解法骨架（迭代縮減）：把數字不斷折疊相加直到能判斷
int divisible_by_3(unsigned int n) {
    int odd = 0, even = 0;
    while (n) {
        if (n & 1) odd++;      // 偶數位（bit 0,2,4...）
        n >>= 1;
        if (n & 1) even++;     // 奇數位（bit 1,3,5...）
        n >>= 1;
    }
    int diff = (odd > even) ? (odd - even) : (even - odd);
    return divisible_by_3(diff);   // 遞迴縮減，base case: diff==0 → 是
    // base: diff 為 0 回 1；diff 為 0~2 的處理需補（這裡示意思路）
}
```

（這題重點是思路與「2^(2k) ≡ 1 (mod 3)」的觀察，細節實作可討論；面試能講出原理就好。）

## bitfield（結構位元欄位）

```c
struct flags {
    unsigned int ready : 1;     // 佔 1 bit
    unsigned int mode  : 2;     // 佔 2 bits
    unsigned int error : 1;
    unsigned int       : 4;     // 4 bits padding（無名）
};
```

bitfield 讓你用「位元」為單位定義 struct 成員——韌體描述硬體暫存器佈局時常用。但有陷阱：**位元順序、對齊是 implementation-defined**（不同編譯器/平台可能不同），所以跨平台的暫存器存取**不建議用 bitfield**，改用 mask + shift 較可移植。

## 考古題詳解

### Q1：寫巨集設定/清除一個整數的第 n 位

<details>
<summary>詳解</summary>

```c
#define SET_BIT(x, n)   ((x) |=  (1u << (n)))
#define CLEAR_BIT(x, n) ((x) &= ~(1u << (n)))
```

要點：SET 用 `|= (1<<n)`；CLEAR 用 `&= ~(1<<n)`（`~` 做出「只有第 n 位是 0」的 mask）。用 `1u` 避免有號移位 UB。巨集括號（Ch 6）。

**考點**：bit set/clear，韌體基本功，必考。
</details>

### Q2：不用迴圈判斷一個數是不是 2 的次方

<details>
<summary>詳解</summary>

```c
return x != 0 && (x & (x - 1)) == 0;
```

2 次方只有一個 bit 是 1，`x-1` 把它變 0、低位全 1，AND = 0。`x!=0` 排除 0。

**考點**：`x & (x-1)` 技巧，超高頻。
</details>

### Q3：數一個 unsigned int 有幾個 1

<details>
<summary>詳解</summary>

```c
int count = 0;
while (x) { x &= (x - 1); count++; }
return count;
```

`x &= (x-1)` 每次清最低位的 1，迴圈次數 = 1 的個數（比逐位檢查 32 次快）。

進階：可提 lookup table（查表，O(1) 每 byte）或 GCC `__builtin_popcount`（硬體指令）。

**考點**：Brian Kernighan 數 bit 法。
</details>

### Q4：不用第三個變數交換兩個數

<details>
<summary>詳解</summary>

```c
a ^= b; b ^= a; a ^= b;   // XOR swap
```

但**必須檢查 a、b 不是同一個位址**（XOR swap 對自己會變 0）。實務上用暫存變數更安全、不一定慢——面試可提這點展現你懂取捨。

**考點**：XOR swap + 自我交換陷阱。
</details>

### Q5：`1 << 31` 在 32-bit int 有什麼問題？

<details>
<summary>詳解</summary>

`1` 是 `int`（signed），`1 << 31` 移進符號位 = **未定義行為（UB）**。應該用 `1u << 31`（unsigned）。

這是有號數位元操作的經典陷阱——位元操作一律用 unsigned。

**考點**：有號移位 UB（連結 Ch 9 整數陷阱）。
</details>

## 踩雷集錦

1. **CLEAR bit 忘了 `~`**：`x &= (1<<n)` 會把「其他位全清掉、只留第 n 位」，不是清第 n 位。要 `x &= ~(1<<n)`。
2. **用 signed 做位元操作**：`1 << 31`、有號右移補符號位——UB 或行為依實作。一律用 unsigned（`1u`）。
3. **判 2 次方忘了排除 0**：`(0 & -1) == 0` 會誤判 0 是 2 次方。要 `x != 0 &&`。
4. **XOR swap 對同一變數**：`swap(&x, &x)` 把 x 變 0。要先檢查。
5. **跨平台用 bitfield 對暫存器**：位元順序/對齊 implementation-defined，不可移植。用 mask+shift。
6. **TEST bit 直接當 0/1 用**：`x & (1<<n)` 結果是 `0` 或 `1<<n`（不是 1）。要 bool 用 `!!(x & (1<<n))` 或 `(x>>n)&1`。

## 速記

- mask 是核心：`1u << n` = 第 n 位的 mask。
- **SET** `|= (1u<<n)`、**CLEAR** `&= ~(1u<<n)`、**TOGGLE** `^= (1u<<n)`、**TEST** `(x>>n)&1u`。
- **判 2 次方**：`x && !(x & (x-1))`。**count bits**：`while(x){x&=x-1;cnt++;}`。
- **XOR swap**：`a^=b;b^=a;a^=b;`（小心 a==b）。
- 位元操作一律 **unsigned**（`1u<<31` 不是 `1<<31`）。
- 跨平台暫存器用 mask+shift，不用 bitfield。

## 自我檢核

- [ ] 不看，能默寫 set/clear/toggle/test 第 n 位的 4 個寫法嗎？CLEAR 為什麼要 `~`？
- [ ] 怎麼不用迴圈判斷 2 的次方？原理是什麼？
- [ ] 怎麼數一個數有幾個 1（比逐位快的方法）？
- [ ] XOR swap 怎麼寫？有什麼陷阱？
- [ ] 為什麼位元操作要用 unsigned？`1 << 31` 有什麼問題？

## 延伸閱讀

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q9（bit set/clear）、Q13（整數寬度 `~0`）。
  - **和本章的關聯**：本章 bit 操作考點的源頭。

- **[Bit Twiddling Hacks](https://graphics.stanford.edu/~seander/bithacks.html)** — Sean Anderson (Stanford)
  - **這篇說什麼**：各種位元技巧的大全（count bits、判 2 次方、reverse bits…）。
  - **讀哪裡**：count bits、power of 2、swap 段。
  - **為什麼值得讀**：位元技巧的權威集，面試遇到的位元題幾乎都在裡面。

### 書籍

- **《Hacker's Delight》** — Henry Warren
  - **這本的定位**：位元運算的聖經（深，面試夠用看上面的 bithacks 即可）。
  - **讀哪幾章**：power of 2、population count 章。

位元練熟了，下一章看資料怎麼在記憶體排列——struct/union 與記憶體對齊，sizeof struct 是經典考題。

→ [Ch 8 struct/union/enum 與記憶體對齊](./08-struct-union-alignment.md)
