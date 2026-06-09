# Ch 12 — 字串與標準函式

> **目標**：搞懂 C 字串的本質（`\0` 結尾的 char 陣列）、自己實作 strlen/strcpy/strcmp/memcpy/memmove、以及 strcpy vs strncpy、memcpy vs memmove 的差異。「手寫一個 strcpy」是上機考常客。

> **環境**：C，`gcc -Wall`。前置：Ch 4（指標）、Ch 11（記憶體）。

## 為什麼考這個

C 沒有字串型別——字串就是「`\0` 結尾的 char 陣列」。能不能自己寫出 strcpy/memcpy，測你對指標、邊界、`\0` 的掌握。MTK 上機考愛叫你「實作 strcpy/memcpy」「reverse string」，且會追問 overlap、buffer overflow 等陷阱。

## 先建立直覺：C 字串 = char 陣列 + `\0` 結尾

```
   char s[] = "hi";
   記憶體：  'h'  'i'  '\0'
   索引：     0    1    2

   "hi" 其實佔 3 個 byte（含結尾的 '\0'）！
   所有字串函式靠 '\0' 知道「字串到哪結束」——沒有 '\0' 就會一直讀下去。
```

關鍵：**字串的長度資訊不存在任何地方，靠 `\0` 標記結尾。** 這是 C 字串所有 bug 的根源（忘了 `\0`、緩衝區太小放不下 `\0`、讀到沒有 `\0` 的記憶體）。

## 手寫經典字串函式

### strlen（算長度，不含 `\0`）

```c
size_t my_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;        // 走到 '\0' 停
    return p - s;          // 指標相減 = 字元數（不含 '\0'）
}
```

要點：`const char *`（不改來源）、走到 `\0`、回傳指標差。`strlen("hi")` = 2（不含 `\0`）。

### strcpy（複製字串）

```c
char *my_strcpy(char *dest, const char *src) {
    char *ret = dest;             // 保留起始位址當回傳值
    while ((*dest++ = *src++))    // 複製到（含）'\0' 為止
        ;
    return ret;
}
```

要點：`*dest++ = *src++` 一行複製+前進；`while` 的條件是賦值結果，複製到 `\0` 時 `\0`（=0）讓迴圈結束——**但 `\0` 已經被複製進去了**（先賦值再判斷）。回傳 dest 起始（慣例，方便串接）。

**陷阱**：strcpy **不檢查 dest 大小**——src 比 dest 長就 buffer overflow（覆蓋相鄰記憶體，安全漏洞）。所以有 strncpy（下面）。

### strcmp（比較）

```c
int my_strcmp(const char *a, const char *b) {
    while (*a && (*a == *b)) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}
```

要點：逐字比，到不同或某個 `\0` 停。回傳差值（<0/0/>0）。用 `unsigned char` 避免有號 char 的比較問題（Ch 9）。

### memcpy（複製記憶體，不管 `\0`）

```c
void *my_memcpy(void *dest, const void *src, size_t n) {
    char *d = dest;
    const char *s = src;
    while (n--) *d++ = *s++;      // 複製 n 個 byte（不看 '\0'）
    return dest;
}
```

要點：`void *`（任意型別）、複製**指定 n 個 byte**（不靠 `\0`，所以能複製任意二進位資料/struct）。和 strcpy 的差別：memcpy 按 byte 數、strcpy 按 `\0`。

**陷阱**：memcpy 不處理 **overlap**（src 和 dest 記憶體重疊）——重疊時行為未定義。要處理重疊用 memmove（下面）。

### memmove（處理重疊的 memcpy）

```c
void *my_memmove(void *dest, const void *src, size_t n) {
    char *d = dest;
    const char *s = src;
    if (d < s) {                  // dest 在前：從頭複製（前向）
        while (n--) *d++ = *s++;
    } else {                       // dest 在後：從尾複製（後向，避免覆蓋還沒讀的）
        d += n; s += n;
        while (n--) *--d = *--s;
    }
    return dest;
}
```

要點：**memmove 處理 overlap**——判斷 dest/src 的相對位置，決定從頭還是從尾複製，避免「還沒複製的源資料被覆蓋」。memcpy 不做這個判斷（所以較快，但重疊時錯）。

## 關鍵對比

| | strcpy | strncpy | memcpy | memmove |
|---|---|---|---|---|
| 停止條件 | 遇 `\0` | n 個或遇 `\0` | n 個 byte | n 個 byte |
| 看 `\0`？ | 是 | 是 | 否 | 否 |
| 處理 overlap | — | — | **否（UB）** | **是** |
| 大小檢查 | **無（會 overflow）** | 有（限 n） | 按 n | 按 n |

兩組重點：

1. **strcpy vs strncpy**：strncpy 限制最多複製 n 個，較安全（防 overflow）。但 strncpy 有自己的坑——**若 src 長度 >= n，不會補 `\0`**（dest 變成沒有結尾的字串！），要手動補。
2. **memcpy vs memmove**：重疊用 memmove（安全），不重疊用 memcpy（較快）。不確定就用 memmove。

## 考古題詳解

### Q1：實作 strcpy

<details>
<summary>詳解</summary>

```c
char *my_strcpy(char *dest, const char *src) {
    char *ret = dest;
    while ((*dest++ = *src++)) ;
    return ret;
}
```

要點：(1) `const` src；(2) `*dest++ = *src++` 複製+前進；(3) 複製到 `\0`（先賦值，`\0` 進去後迴圈才停）；(4) 回傳起始位址。

追問「有什麼風險」→ **不檢查 dest 大小，src 太長會 buffer overflow**。改用 strncpy 或傳入大小。

**考點**：手寫 strcpy + overflow 陷阱，超高頻。
</details>

### Q2：實作 memcpy，並說明和 memmove 的差別

<details>
<summary>詳解</summary>

```c
void *my_memcpy(void *dest, const void *src, size_t n) {
    char *d = dest; const char *s = src;
    while (n--) *d++ = *s++;
    return dest;
}
```

差別：**memcpy 不處理 src/dest 重疊**（重疊時 UB）；memmove 會判斷方向（dest<src 前向、dest>src 後向）避免覆蓋。記憶體可能重疊時用 memmove。

memcpy vs strcpy：memcpy 按 byte 數複製（能複製任意二進位/struct）、strcpy 靠 `\0`（只複製字串）。

**考點**：memcpy 實作 + overlap + 和 strcpy/memmove 的區別。
</details>

### Q3：reverse 一個字串（in-place）

<details>
<summary>詳解</summary>

```c
void reverse(char *s) {
    int len = strlen(s);
    int i = 0, j = len - 1;
    while (i < j) {
        char tmp = s[i]; s[i] = s[j]; s[j] = tmp;
        i++; j--;
    }
}
```

雙指標頭尾交換，到中間停。注意 `j = len-1`（不含 `\0`）、in-place（不另配記憶體）。

也可用 XOR swap（Ch 7）省 tmp，但要小心 i==j。

**考點**：reverse string，上機考常客。
</details>

### Q4：`strlen("hello")` 是多少？`sizeof("hello")` 呢？

<details>
<summary>詳解</summary>

- `strlen("hello")` = **5**（字元數，不含 `\0`）。
- `sizeof("hello")` = **6**（含 `\0` 的 char 陣列大小）。

差別：strlen 算到 `\0` 為止（不含）；sizeof 算整個陣列（含 `\0`）。經典陷阱。

**考點**：strlen vs sizeof 對字串。
</details>

### Q5：這段有什麼問題？

```c
char dest[5];
strcpy(dest, "hello world");
```

<details>
<summary>詳解</summary>

**buffer overflow！** `dest` 只有 5 byte，但 `"hello world"` 是 12 byte（含 `\0`）。strcpy 不檢查大小，會寫超過 dest 的範圍，覆蓋相鄰記憶體——可能 crash、資料損壞、或被利用成安全漏洞（stack smashing）。

修法：`strncpy(dest, "hello world", sizeof(dest)-1); dest[sizeof(dest)-1]='\0';`（留位置給 `\0` 並手動補）。或確保 dest 夠大。

**考點**：strcpy buffer overflow，安全意識（韌體/面試都重視）。
</details>

## 踩雷集錦

1. **忘了字串的 `\0`**：`"hi"` 佔 3 byte。配緩衝區要 `len+1`（留給 `\0`）。
2. **strcpy 不檢查大小**：src 比 dest 長 = overflow。用 strncpy 或確保大小。
3. **strncpy 不補 `\0`**：src 長度 >= n 時 dest 沒有結尾 `\0`（變危險的非字串）。要手動補。
4. **記憶體重疊用 memcpy**：UB。重疊用 memmove。
5. **strlen vs sizeof 字串**：strlen 不含 `\0`（5）、sizeof 含（6）。
6. **比較字串用 `==`**：`if (s1 == s2)` 比的是「指標位址」不是內容！比內容用 strcmp。
7. **char 比較的符號**：strcmp 用 `unsigned char` 避免有號 char 的負值問題（Ch 9）。

## 速記

- C 字串 = `\0` 結尾的 char 陣列；長度靠 `\0`，沒有別的地方存。
- **strcpy**：`while((*d++=*s++));` 複製到 `\0`；**不檢查大小（overflow）**。
- **memcpy**：複製 n byte（不看 `\0`）；**不處理重疊**。
- **memmove** 處理重疊（判方向）；**strncpy** 限大小但可能不補 `\0`。
- `strlen("hi")`=2（不含\0）、`sizeof("hi")`=3（含\0）。
- 比字串內容用 **strcmp**（`==` 比的是位址）。

## 自我檢核

- [ ] 不看，能手寫 strcpy 和 memcpy 嗎？各自的停止條件是什麼？
- [ ] strcpy 有什麼安全風險？怎麼緩解？strncpy 有什麼自己的坑？
- [ ] memcpy 和 memmove 差在哪？什麼時候一定要用 memmove？
- [ ] `strlen("hello")` 和 `sizeof("hello")` 各多少？為什麼？
- [ ] 怎麼比較兩個字串的內容？用 `==` 會怎樣？

## 延伸閱讀

### 書籍

- **《C Programming Language (K&R)》** — Ch 5.5 Character Pointers and Functions
  - **讀哪裡**：5.5（strcpy/strcmp 的經典實作）。
  - **和本章的關聯**：手寫字串函式的權威範本。

### 文章

- **[發哥(聯發科)上機考題目整理 — HackMD](https://hackmd.io/@Rance/SkSJL_5gX)**
  - **讀哪裡**：strcpy/memcpy/reverse string 相關題。
  - **和本章的關聯**：MTK 上機考字串題的源頭。

- **[Common C string function pitfalls](https://nrk.neocities.org/articles/c-strings)** 類 C 字串陷阱文
  - **為什麼值得讀**：strncpy 不補 `\0`、overflow 等真實陷阱的整理。

Part 1（C 核心）寫完了！用練習 A 模擬一次 MTK C 上機考，把這 12 章的考點綜合驗收。

→ [練習 A：C 上機考模擬](./practice-a-c-coding-test.md)
