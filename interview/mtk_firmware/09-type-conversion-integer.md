# Ch 9 — 型別轉換與整數陷阱

> **目標**：搞懂 signed/unsigned 混用的自動轉型、整數提升（integer promotion）、溢位、以及可攜性陷阱（`0xFFFF`、`~0`）。`-20 + 6u` 變超大正數是上機考經典陷阱。

> **環境**：C，`gcc -Wall`，32-bit int。前置：Ch 7（位元/unsigned）。

## 為什麼考這個

C 的隱式型別轉換是「安靜地」發生的——你寫 `if (x < y)`，編譯器可能偷偷把 signed 轉 unsigned，結果跟你想的相反，還不報錯。這種「看起來對、其實錯」的題目最能篩人。韌體常處理 unsigned 的硬體值、不同寬度的暫存器，這些轉型陷阱天天遇到。

## 先建立直覺：混用時 signed 會「投降」變 unsigned

```
   int x = -20;  unsigned int y = 6;
   x + y = ?

   直覺：-20 + 6 = -14
   實際：當 signed 和 unsigned 同寬度混用，signed 會被轉成 unsigned！
        -20 轉成 unsigned（32-bit）= 4294967276
        4294967276 + 6 = 4294967282  ← 超大正數，不是 -14！
```

核心規則：**signed 和 unsigned 同寬度做運算時，signed 那邊轉成 unsigned**（unsigned「贏」）。負數轉 unsigned = 加上 2^n（環繞），變超大正數。這是無數 bug 的源頭。

## usual arithmetic conversions（一般算術轉換）

兩個運算元型別不同時，編譯器按規則統一型別（簡化版，由「大」到「小」決定誰轉誰）：

```
   1. 先做 integer promotion（見下）：比 int 小的型別（char/short）先升成 int
   2. 若一邊是浮點，另一邊轉浮點（int → double 等）
   3. 整數間：
      - 同號：轉成「較大寬度」的型別（int + long → long）
      - 不同號且 unsigned 寬度 >= signed：signed 轉 unsigned ← 陷阱所在
      - 不同號且 signed 寬度 > unsigned：unsigned 轉 signed（較安全）
```

實務上最常踩的是第 3 點的「同寬度 signed vs unsigned → signed 轉 unsigned」。

## integer promotion（整數提升）

**比 int 小的型別（char、short、bit-field）在運算時會先「升級」成 int。**

```c
char a = 100, b = 100, c;
c = a + b;     // a, b 先升成 int → 100+100 = 200（int 運算，不溢位）
               // 再存回 char c → 200 超過 char(若 signed -128~127) → 實作定義/截斷

unsigned char x = 255;
x = x + 1;     // x 升 int → 255+1=256（int），存回 unsigned char → 256 % 256 = 0（環繞）
```

關鍵：`char + char` 的運算其實是在 `int` 上做的（先提升），結果再存回去才可能溢位/截斷。這解釋很多「char 運算結果怪怪的」題目。

`sizeof` 的陷阱也來自這：

```c
char c;
printf("%zu\n", sizeof(c));      // 1（c 是 char）
printf("%zu\n", sizeof(c + 0));  // 4（c+0 觸發 integer promotion，變 int）
```

## signed vs unsigned 比較陷阱

```c
int i = -1;
unsigned int u = 1;
if (i < u)  printf("less\n");
else        printf("not less\n");
```

**印 "not less"！** 因為 `i < u` 把 `i`（-1）轉成 unsigned = 4294967295，遠大於 1。所以 `-1 < 1` 在 signed/unsigned 混用下是「假」。

更陰險的迴圈：

```c
unsigned int n = 10;
for (unsigned int i = n; i >= 0; i--) {   // 無窮迴圈！
    ...
}
// i 是 unsigned，永遠 >= 0（減到 0 再 -- 環繞成 4294967295），條件永真
```

unsigned 永遠 `>= 0`——這個迴圈永遠不結束。用 unsigned 做遞減迴圈要特別小心。

## 溢位：signed UB vs unsigned 環繞

```c
int x = INT_MAX;
x + 1;            // signed 溢位 = 未定義行為（UB）！編譯器可假設不發生、做激進最佳化

unsigned int y = UINT_MAX;
y + 1;            // unsigned 溢位 = 定義良好，環繞成 0（模 2^n）
```

**重要差異**：
- **unsigned 溢位**：定義良好，環繞（wrap around，模 2^n）。
- **signed 溢位**：**未定義行為（UB）**！編譯器可假設「signed 不會溢位」做最佳化，導致詭異結果。

所以位元操作、需要環繞的計數器用 unsigned；別依賴 signed 溢位的行為。

## 可攜性陷阱：0xFFFF 與 ~0

```c
// 想要「全 1」的 mask
unsigned int mask = 0xFFFF;    // 壞！只在 16-bit 系統是全 1
                               // 32-bit 系統：0x0000FFFF（只有低 16 位是 1）
unsigned int mask = ~0u;       // 對！~0 = 全 1，不管多少位（可攜）
```

`0xFFFF` 假設了字組寬度是 16 位——換到 32/64 位就錯。要「全 1」用 `~0`（NOT 0 = 所有 bit 反轉成 1），它在任何寬度都對。這是 Nigel Jones 經典題（Q13）。

同理，別寫死位元寬度相關的常數（用 `sizeof`、`CHAR_BIT`、`~0` 等）。

## 考古題詳解

### Q1：`-20 + 6u` 印出什麼？（32-bit）

```c
printf("%d\n", -20 + 6u);
printf("%u\n", -20 + 6u);
```

<details>
<summary>詳解</summary>

`-20 + 6u`：`6u` 是 unsigned，`-20`（int）轉成 unsigned = 4294967276，+ 6 = **4294967282**（環繞）。

- `%d`（當 signed 印）：4294967282 的 bit pattern 當 signed = **-14**（繞回來剛好）。
- `%u`（當 unsigned 印）：**4294967282**。

所以用 `%d` 印「看起來對」（-14），用 `%u` 印才露出「其實是超大正數」。值的 bit pattern 是同一個，差在怎麼解讀。

**考點**：signed/unsigned 混用 → signed 轉 unsigned，超經典。
</details>

### Q2：這個 for 迴圈會怎樣？

```c
for (unsigned int i = 5; i >= 0; i--)
    printf("%u\n", i);
```

<details>
<summary>詳解</summary>

**無窮迴圈。** `i` 是 unsigned，永遠 `>= 0`。`i` 減到 0 後再 `i--` 環繞成 4294967295（不是 -1），條件 `i >= 0` 永真。

修正：用 signed `int i`，或改條件（如 `for(unsigned i=5; i-- > 0;)` 的技巧）。

**考點**：unsigned 永遠 >= 0，遞減迴圈陷阱。
</details>

### Q3：印出什麼？（integer promotion）

```c
char a = 0xFF;        // signed char: -1
unsigned char b = 0xFF;  // 255
printf("%d %d\n", a, b);
```

<details>
<summary>詳解</summary>

```
-1 255
```
- `a` 是 `char`（多數平台 signed），`0xFF` = 全 1 bit = signed char 的 -1。印時提升成 int（符號擴展）→ 0xFFFFFFFF = -1。
- `b` 是 `unsigned char` = 255。提升成 int（零擴展）→ 255。

陷阱：`char` 是 signed 還 unsigned 是 implementation-defined！`0xFF` 存進 signed char 是 -1。要明確用 `signed char`/`unsigned char` 避免歧義。

**考點**：char 的符號 + integer promotion 的符號/零擴展。
</details>

### Q4：怎麼寫一個可攜的「全 1」mask？

<details>
<summary>詳解</summary>

```c
unsigned int mask = ~0u;     // 對：全 1，任何寬度
// 不要用 0xFFFF（只在 16-bit 對）或 0xFFFFFFFF（只在 32-bit 對）
```

`~0` 把所有 bit 反轉成 1，不依賴特定字組寬度。

**考點**：Nigel Jones Q13，可攜性（用 `~0` 而非寫死十六進位）。
</details>

### Q5：`sizeof('A')` 在 C 是多少？

<details>
<summary>詳解</summary>

在 **C 是 4**（`sizeof(int)`）！因為 C 的字元常數 `'A'` 的型別是 **int**（不是 char）。

陷阱：在 **C++** 中 `sizeof('A')` 是 1（C++ 字元常數是 char）。這是 C 和 C++ 的差異，面試考 C 要答 4。

**考點**：C 的字元常數是 int（C/C++ 差異）。
</details>

## 踩雷集錦

1. **signed/unsigned 混用以為按數學算**：`-20+6u` 不是 -14（在 unsigned 詮釋下是超大正數）。signed 會轉 unsigned。
2. **unsigned 遞減迴圈到負**：unsigned 永遠 >= 0，`for(unsigned i; i>=0; i--)` 無窮迴圈。
3. **依賴 signed 溢位**：signed 溢位是 UB，編譯器可做激進最佳化。需要環繞用 unsigned。
4. **char 當定值符號**：`char` signed/unsigned 是 implementation-defined。要明確 `signed/unsigned char`。
5. **用 `0xFFFF` 當全 1 mask**：只在 16-bit 對。用 `~0`。
6. **忘了 integer promotion**：`char+char` 在 int 上算（先提升），`sizeof(c+0)` 是 4 不是 1。
7. **C 裡以為 `sizeof('A')` 是 1**：是 4（字元常數是 int）。C++ 才是 1。

## 速記

- **混用 signed/unsigned（同寬度）→ signed 轉 unsigned**；負數轉 unsigned = 超大正數。
- **unsigned 永遠 >= 0**：遞減迴圈陷阱、`-1 < 1u` 為假。
- **溢位**：unsigned 環繞（定義良好）、**signed UB**（別依賴）。
- **integer promotion**：char/short 運算前升成 int；`sizeof('A')` 在 C 是 4。
- **可攜全 1 mask 用 `~0`**，不要 `0xFFFF`。
- `char` 的符號是 implementation-defined，明確用 signed/unsigned char。

## 自我檢核

- [ ] `-20 + 6u` 用 `%d` 和 `%u` 各印什麼？為什麼？
- [ ] `for(unsigned i=5; i>=0; i--)` 會怎樣？為什麼？
- [ ] unsigned 溢位和 signed 溢位的行為差在哪（環繞 vs UB）？
- [ ] 為什麼「全 1 mask」要用 `~0` 而不是 `0xFFFF`？
- [ ] C 裡 `sizeof('A')` 是多少？和 C++ 一樣嗎？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — §2.2–2.3 Integer Representations & Arithmetic
  - **讀哪裡**：2.2.5（signed vs unsigned 轉換）、2.3（整數運算、溢位）。
  - **和本章的關聯**：整數轉型/溢位的權威，含 two's complement 與轉換規則。

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q12（signed/unsigned 轉型 `-20+6`）、Q13（`0xFFFF` 可攜性）。
  - **和本章的關聯**：本章兩大考點的源頭。

- **[What Every C Programmer Should Know About Undefined Behavior](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner (LLVM)
  - **讀哪裡**：signed overflow 那段。
  - **為什麼值得讀**：解釋為什麼 signed 溢位 UB 會導致編譯器做意外最佳化。

整數陷阱破解，下一章是另一類「印出什麼」殺手——運算子優先序與表達式，`a+++b` 怎麼解析。

→ [Ch 10 運算子優先序與表達式](./10-operator-precedence-expressions.md)
