# Ch 8 — 字串陷阱

> 目標：掌握 C 字串的所有常見陷阱：strlen vs sizeof、strcpy 系列的邊界問題、null terminator 的隱患。

## C 字串的本質

C 沒有字串型別。字串是**以 null terminator（`'\0'`）結尾的 `char` 陣列**。所有字串函式都依賴找到 `'\0'` 才知道字串結尾——忘掉或破壞它，就是 bug 或安全漏洞。

```c
char s[] = "hello";
// 實際儲存：'h' 'e' 'l' 'l' 'o' '\0'
// sizeof(s) == 6，strlen(s) == 5
```

---

## `strlen` vs `sizeof`

```c
char arr[] = "hello";   // 陣列初始化，strlen=5, sizeof=6
char *p    = "hello";   // 指標，strlen=5, sizeof=8（指標大小）

// 函式參數中：
void foo(char str[]) {
    sizeof(str)   // 8（指標，陣列 decay 了）
    strlen(str)   // 5（正確的字串長度）
}
```

**面試常考**：

```c
char buf[10] = "hello";
sizeof(buf)   // 10（緩衝區大小）
strlen(buf)   // 5（字串內容長度）
```

`sizeof` 在編譯期求值，`strlen` 在執行期跑到找 `'\0'`。

---

## strcpy / strncpy 的陷阱

### strcpy：完全不做邊界檢查

```c
char dst[5];
strcpy(dst, "hello world");   // 緩衝區溢位！寫了 12 bytes 到 5-byte 緩衝區
```

攻擊者可以利用 `strcpy` 的溢位覆寫返回地址，這是棧溢位漏洞的根源。

### strncpy：比 strcpy 更危險，反而更不直覺

```c
char dst[5];
strncpy(dst, "hello world", sizeof(dst));
// 複製最多 5 個字元，但 dst 不一定有 '\0'！
// "hello" 被複製，但沒有 '\0'（沒空間了）
printf("%s\n", dst);   // UB：讀到隨機記憶體直到找到 '\0'
```

`strncpy` 的行為：
- 若源字串短於 n：用 `'\0'` 填滿剩餘空間（浪費）
- 若源字串長於或等於 n：不補 `'\0'`（陷阱！）

### 安全做法：strlcpy（BSD）或手動

```c
// 方法一：strlcpy（非 POSIX，但 BSD/macOS 有）
strlcpy(dst, src, sizeof(dst));  // 保證 null-terminate，返回 strlen(src)

// 方法二：snprintf
snprintf(dst, sizeof(dst), "%s", src);  // 保證 null-terminate

// 方法三：手動
strncpy(dst, src, sizeof(dst) - 1);
dst[sizeof(dst) - 1] = '\0';   // 強制補上 '\0'
```

---

## strcat 的陷阱

```c
char buf[10] = "hello";
strcat(buf, " world");   // 溢位！total = 11 bytes + '\0' = 12 > 10
```

`strcat` 先找到 dst 的 `'\0'`，再開始複製。如果 dst 沒有 `'\0`（`strncpy` 的後果），`strcat` 會讀到隨機記憶體。

安全做法：

```c
strncat(buf, src, sizeof(buf) - strlen(buf) - 1);
// 或 strlcat（BSD）
```

---

## strcmp 家族

```c
// strcmp：返回 0（相等）、負數（a<b）、正數（a>b）
if (strcmp(s1, s2) == 0)  // 相等
if (strcmp(s1, s2) < 0)   // s1 字典序在 s2 前面

// 常見錯誤：用 == 比較字串
if (s1 == s2)   // 比較的是指標地址，不是字串內容！
```

---

## 字串常數與修改

```c
char *p = "hello";
p[0] = 'H';     // UB：text segment 唯讀

char arr[] = "hello";
arr[0] = 'H';   // OK：arr 在 stack/data，可改
```

編譯器可能將相同的字串常數合併到同一位址（string interning），所以：

```c
char *a = "hello";
char *b = "hello";
a == b;   // 可能為真（相同地址），但不保證；未定義行為（比較不同物件的指標）
```

---

## 常見陷阱題

**Q1：輸出是什麼？**

```c
char s[3] = "abc";  // 不含 '\0'！'a' 'b' 'c'（剛好裝滿）
printf("%s\n", s);  // UB：讀到 s 後面直到找到 '\0'
```

C 允許這樣初始化（`sizeof(s) == strlen("abc")`），但 `printf` 時沒有 `'\0` 終止符。

**Q2：strncpy 後字串長度是多少？**

```c
char dst[5];
strncpy(dst, "hi", sizeof(dst));
// dst = 'h' 'i' '\0' '\0' '\0'（剩餘填 '\0'）
strlen(dst) == 2
```

```c
char dst[3];
strncpy(dst, "hello", sizeof(dst));
// dst = 'h' 'e' 'l'（無 '\0'！）
strlen(dst) == ???  // UB
```

---

## 自我檢核

- [ ] 能解釋 `strlen` 和 `sizeof` 的差異（執行期 vs 編譯期）
- [ ] 能說出 `strncpy` 不保證 null-terminate 的情況
- [ ] 知道 `s1 == s2` 比較的是指標，不是字串內容
- [ ] 知道 `char s[3] = "abc"` 的陷阱（沒有 `'\0'`）

→ [Ch 9 整數系統：有號/無號/提升/轉換](./09-integer-arithmetic.md)
