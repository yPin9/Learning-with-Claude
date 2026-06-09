# Ch 16 — endianness

> **目標**：徹底搞懂 big endian / little endian——是什麼、怎麼測、什麼時候會出事（跨平台、網路、型別雙關）、怎麼轉換。這是韌體跨平台與通訊協定的經典考點，幾乎必考。

> **環境**：C，假設 32-bit int。前置：Ch 4（指標）、Ch 8（union）。

## 為什麼考這個

韌體常常和「別的東西」交換二進位資料——網路封包、外部晶片、檔案格式、不同 CPU。如果兩邊的 byte 順序（endianness）不同，同樣的 byte 會被解讀成不同的數——這是跨平台最隱晦的 bug 之一。面試必問「endian 是什麼、怎麼測、什麼時候要轉」。

## 先建立直覺：多 byte 數字「哪個 byte 放前面」

```
   一個 32-bit 數 0x12345678（4 個 byte：12 34 56 78）
   要存進記憶體的 4 個連續位址，誰放低位址？

   Big Endian（大端）：高位 byte 放低位址（「大的在前」，像人類讀數字）
   位址:   0x00  0x01  0x02  0x03
   內容:    12    34    56    78        ← 最高位 0x12 在最前面

   Little Endian（小端）：低位 byte 放低位址（「小的在前」）
   位址:   0x00  0x01  0x02  0x03
   內容:    78    56    34    12        ← 最低位 0x78 在最前面
```

口訣：**big endian「最高位 byte 在最低位址」（符合人類書寫順序）；little endian「最低位 byte 在最低位址」（反過來）。** x86 和多數 ARM 是 little endian；網路協定（TCP/IP）是 big endian。

> 為什麼有兩種：歷史與設計取捨，沒有絕對好壞。little endian 在某些運算（型別轉換、取低位 byte）方便；big endian 符合人類閱讀、網路標準採用。兩派並存，所以才有「跨平台要轉換」的問題。

## 怎麼測（韌體招牌題）

### 方法一：union（Ch 8）

```c
int is_little_endian(void) {
    union { int i; char c; } u;
    u.i = 1;
    return u.c == 1;     // little: c=1（0x01 在最低位址）；big: c=0
}
```

`int i = 1` 的 4 byte：little 是 `01 00 00 00`、big 是 `00 00 00 01`。`u.c` 讀第一個 byte（最低位址）——little 得 1、big 得 0。

### 方法二：char 指標強轉

```c
int is_little_endian2(void) {
    int x = 1;
    char *p = (char *)&x;     // 指向 x 的第一個 byte（最低位址）
    return *p == 1;           // little: 1, big: 0
}
```

`(char *)&x` 取 x 的位址、當 char* 看——`*p` 是最低位址的 byte。原理同 union。

兩種方法本質相同：**看「整數 1 的最低位址那個 byte 是不是 1」**——是就 little endian。

## 什麼時候會出事

endian 只在「**跨 endian 邊界交換多 byte 二進位資料**」時才有問題。同一台機器內部不會（自己讀自己存的，一致）。出事場景：

```
   1. 網路通訊：你的 little endian 機器送 0x12345678 出去，
      對方 big endian 機器收到 → 解讀成 0x78563412！（要轉成 network byte order）

   2. 跨晶片/外設：和一個 big endian 的感測器/晶片交換資料

   3. 檔案格式：寫一個二進位檔在 little 機器，到 big 機器讀 → 數字錯亂

   4. 型別雙關：用 char* 逐 byte 讀一個 int → 順序依 endian

   不會出事：
   - 純算術運算（CPU 內部處理，endian 透明）
   - 同一台機器自己存自己讀
```

關鍵判斷：**只有「把多 byte 數字當 byte 序列傳給另一個系統」時，endian 才重要。** 純粹在自己機器內當數字用，endian 是透明的（CPU 自己處理）。

## network byte order 與轉換

網路協定統一用 **big endian（network byte order）**。所以送資料到網路前要從「主機順序（host）」轉成「網路順序」，收到要轉回來。標準函式：

```c
#include <arpa/inet.h>   // (在嵌入式可能要自己實作)
uint32_t htonl(uint32_t hostlong);    // host to network long（32-bit）
uint16_t htons(uint16_t hostshort);   // host to network short（16-bit）
uint32_t ntohl(uint32_t netlong);     // network to host long
uint16_t ntohs(uint16_t netshort);    // network to host short
```

- `htonl/htons`：送出前，主機 → 網路（big endian）。
- `ntohl/ntohs`：收到後，網路 → 主機。

在 little endian 機器上，這些會做 byte swap；在 big endian 機器上，它們是 no-op（已經是 network order）。所以用這些函式寫的網路 code **跨 endian 可攜**——不用自己判斷 endian。

手寫 16-bit / 32-bit byte swap（考古題）：

```c
uint16_t swap16(uint16_t x) {
    return (x >> 8) | (x << 8);     // 高低 byte 交換
}

uint32_t swap32(uint32_t x) {
    return ((x >> 24) & 0xFF)       // byte 3 → byte 0
         | ((x >> 8)  & 0xFF00)     // byte 2 → byte 1
         | ((x << 8)  & 0xFF0000)   // byte 1 → byte 2
         | ((x << 24) & 0xFF000000);// byte 0 → byte 3
}
```

`swap32` 把 4 個 byte 順序顛倒——這就是 little ↔ big 的轉換。用移位 + mask（Ch 7）。

## 考古題詳解

### Q1：寫一個函式判斷機器是 big 還是 little endian

<details>
<summary>詳解</summary>

```c
int is_little_endian(void) {
    int x = 1;
    return *((char *)&x) == 1;    // 看 int=1 的最低位址 byte
}
// 或用 union（Ch 8）
```

原理：`int x=1` 的最低位址 byte——little endian 是 1、big endian 是 0。

**考點**：endian 偵測，韌體必考。
</details>

### Q2：little endian 機器上，`int x = 0x12345678`，`((char*)&x)[0]` 是多少？

<details>
<summary>詳解</summary>

**0x78**。little endian 最低位 byte（0x78）放最低位址，`[0]` 是最低位址 byte = 0x78。

`[0]=0x78, [1]=0x56, [2]=0x34, [3]=0x12`。

若是 big endian：`[0]=0x12`。

**考點**：endian 對 byte 排列的影響，理解儲存順序。
</details>

### Q3：什麼情況下 endian 會造成 bug？什麼情況不會？

<details>
<summary>詳解</summary>

**會出事**：跨 endian 系統交換多 byte 二進位資料——網路通訊、跨晶片、二進位檔案格式、用 char* 逐 byte 解讀多 byte 數。

**不會出事**：純算術運算（CPU 內部 endian 透明）、同一台機器自己存自己讀。

關鍵：endian 只在「把多 byte 數當 byte 序列傳給另一個系統」時重要。

**考點**：endian 何時重要，理解本質（不是「所有多 byte 操作都受影響」）。
</details>

### Q4：手寫一個 32-bit byte swap

<details>
<summary>詳解</summary>

```c
uint32_t swap32(uint32_t x) {
    return ((x >> 24) & 0x000000FF)
         | ((x >> 8)  & 0x0000FF00)
         | ((x << 8)  & 0x00FF0000)
         | ((x << 24) & 0xFF000000);
}
```

把 4 個 byte 順序顛倒（byte0↔byte3、byte1↔byte2），用移位 + mask。這是 little↔big 轉換的核心。

**考點**：byte swap 手寫，結合 bit 操作（Ch 7）。
</details>

### Q5：為什麼用 htonl/ntohl 寫網路 code 比自己判斷 endian 好？

<details>
<summary>詳解</summary>

因為 `htonl/ntohl` **自動處理可攜性**——它們在 little endian 機器上做 byte swap、在 big endian 機器上是 no-op。你寫 `htonl(x)` 不用自己判斷機器 endian，code 在任何 endian 的機器上都對。自己判斷 endian + 手動 swap 容易出錯、不可攜。

**考點**：network byte order 與可攜性。
</details>

## 踩雷集錦

1. **以為所有多 byte 操作都受 endian 影響**：純算術不受影響（CPU 透明）。只有「跨系統交換 byte 序列」才重要。
2. **跨平台直接傳 struct/int 的二進位**：不同 endian 解讀不同。網路用 htonl/ntohl，或定義明確的 byte order。
3. **big/little 記反**：big = 高位 byte 在低位址（符合人類書寫）；little = 低位 byte 在低位址。
4. **自己判斷 endian + 手動 swap 寫網路 code**：易錯不可攜。用 htonl/ntohs。
5. **bitfield 跨平台**（Ch 8）：bitfield 的位元順序也受 endian/實作影響，跨平台不可靠。
6. **以為 endian 影響 bit 順序**：endian 是 **byte** 順序，不是 bit 順序（單個 byte 內的 bit 順序由硬體一致處理）。

## 速記

- **big endian**：高位 byte 在低位址（人類書寫順序）；**little endian**：低位 byte 在低位址。x86/多數 ARM 是 little，網路是 big。
- **測法**：`int x=1; *((char*)&x)==1` → little（看最低位址 byte 是不是 1）。
- **何時重要**：跨 endian 系統交換多 byte 二進位（網路、跨晶片、檔案）。純算術/同機自用不受影響。
- **network byte order = big endian**；用 `htonl/htons/ntohl/ntohs` 自動可攜轉換。
- byte swap 用移位 + mask（Ch 7）。endian 是 byte 順序不是 bit 順序。

## 自我檢核

- [ ] big 和 little endian 各是「哪個 byte 放低位址」？哪個符合人類書寫？
- [ ] 不看，能寫出判斷 endian 的函式嗎？原理是什麼？
- [ ] `int x=0x12345678` 在 little endian，`((char*)&x)[0]` 是多少？
- [ ] 什麼情況 endian 會出 bug？什麼情況不會？
- [ ] 為什麼網路 code 用 htonl 比自己判斷 endian 好？

## 延伸閱讀

### 文章

- **[On Holy Wars and a Plea for Peace](https://web.archive.org/web/20060109132311/http://www.networksorcery.com/enp/data/danny.htm)** — Danny Cohen（endian 一詞的起源論文）
  - **這篇說什麼**：「endian」這個詞的由來（出自《格列佛遊記》大小端之爭）與 byte order 的討論。
  - **為什麼值得讀**:endian 概念的原始出處，有趣且經典。

- **[韌體工程師的0x10個問題 / 聯發科考古題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**:endian 偵測相關題。
  - **和本章的關聯**：MTK 風格 endian 考題。

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — §2.1 Byte Ordering
  - **讀哪裡**：2.1.3（byte ordering）。
  - **和本章的關聯**：endian 的權威說明，含 show_bytes 範例。

跨平台的 byte 順序懂了，下一章補處理器知識——ARM 基礎，韌體跑在什麼硬體上。

→ [Ch 17 ARM 與處理器基礎](./17-arm-processor-basics.md)
