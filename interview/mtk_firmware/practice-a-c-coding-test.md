# 練習 A — C 上機考模擬

> **目標**：模擬一場 MTK 風格的 C 上機/筆試，把 Part 1（Ch 1–12）的考點綜合驗收。**先遮住答案自己作答、限時、能寫的就在電腦上打出來編譯**——這才接近真實上機考的壓力。

> **環境**：C，`gcc -Wall`。建議限時 60–75 分鐘。前置：Part 1 全部。

## 怎麼用這份模擬考

1. **限時**：給自己 60–75 分鐘，模擬上機壓力。
2. **先自己答**：每題遮住 `<details>` 的答案，自己寫——觀念題口頭答、實作題在電腦上打出來編譯跑。
3. **對答案 + 找弱點**：對完答案，把錯的題目對應回該章（題後標了 Ch）重讀。
4. **第二輪只做錯的**：隔天重做錯題，確認真的懂了。

題型混合：觀念輸出（印什麼/會不會編譯）、手寫實作、找 bug——對應真實上機考的三類。

---

## 第一部分：觀念輸出題（印出什麼 / 會不會編譯）

### Q1（Ch 1, 2）這段印什麼？

```c
#include <stdio.h>
void f(void) {
    int a = 0;
    static int b = 0;
    printf("%d %d\n", ++a, ++b);
}
int main(void) { f(); f(); f(); return 0; }
```

<details>
<summary>答案</summary>

```
1 1
1 2
1 3
```
`a` 是 auto（每次重設為 0，++a=1）；`b` 是 static（保值，++b 遞增）。Ch 2 核心。
</details>

### Q2（Ch 9）這段用 `%d` 印什麼？用 `%u` 呢？

```c
printf("%d\n", -1 + 2u);
printf("%u\n", -1 + 2u);
```

<details>
<summary>答案</summary>

`-1 + 2u`：`-1` 轉 unsigned = 4294967295，+2 環繞 = 1。
- `%d`：**1**
- `%u`：**1**

這題剛好兩種都是 1（因為 -1+2 = 1，環繞後仍是 1）。但若是 `-3 + 2u` = unsigned 的 4294967295（-1 的 bit pattern），`%d` 印 -1、`%u` 印 4294967295。重點是理解「signed 轉 unsigned」的機制（Ch 9）。
</details>

### Q3（Ch 4）這段印什麼？

```c
#include <stdio.h>
int main(void) {
    int arr[] = {1, 2, 3, 4, 5};
    int *p = arr;
    printf("%d %d %d\n", *p++, *p, *(p+1));
    return 0;
}
```

<details>
<summary>答案</summary>

注意：printf 參數求值順序未指定（Ch 10），這題嚴格說有未指定行為的疑慮。若忽略求值順序問題、假設由左到右：
- `*p++` = arr[0]=1，然後 p 指向 arr[1]
- `*p` = arr[1]=2
- `*(p+1)` = arr[2]=3
→ `1 2 3`（但不同編譯器因求值順序可能不同——這也是個陷阱，理想的題目不會這樣寫，面試遇到可指出）。

純考 `*p++`（後置）的話：用 *p 再 p++。Ch 4。
</details>

### Q4（Ch 8）這個 struct 的 sizeof 是多少？（64-bit）怎麼重排最小？

```c
struct S { char a; int b; char c; short d; };
```

<details>
<summary>答案</summary>

```
a: offset 0
   1-3 padding (b 對齊 4)
b: offset 4-7
c: offset 8
d: offset 10-11 (short 對齊 2, offset 9 padding)
total: 12 → 已是 4 的倍數
```
**sizeof = 12**。

重排 `int b; short d; char a; char c;` → 4+2+1+1 = 8，**sizeof = 8**。Ch 8。
</details>

### Q5（Ch 6）這段印什麼？（`#define SQR(x) x*x`）

```c
#define SQR(x) x*x
printf("%d\n", SQR(3+1));
```

<details>
<summary>答案</summary>

`SQR(3+1)` → `3+1*3+1` = 3+3+1 = **7**（不是 16）。巨集無括號陷阱。正解 `#define SQR(x) ((x)*(x))`。Ch 6。
</details>

### Q6（Ch 10）這段是 UB 嗎？

```c
int i = 1;
i = i++ + ++i;
```

<details>
<summary>答案</summary>

**是 UB（未定義行為）**。`i` 在一個表達式裡被修改多次（`++i`、`i++`、`=`），沒有 sequence point 隔開。不要給數字答案——答「UB」。Ch 10。
</details>

---

## 第二部分：手寫實作題（在電腦上打出來編譯）

### Q7（Ch 7）寫巨集：設定、清除、反轉、測試一個整數的第 n 位

<details>
<summary>參考解答</summary>

```c
#define SET_BIT(x, n)    ((x) |=  (1u << (n)))
#define CLEAR_BIT(x, n)  ((x) &= ~(1u << (n)))
#define TOGGLE_BIT(x, n) ((x) ^=  (1u << (n)))
#define TEST_BIT(x, n)   (((x) >> (n)) & 1u)
```

要點：CLEAR 要 `~`；用 `1u`（unsigned）；巨集括號。Ch 7。
</details>

### Q8（Ch 7）寫一個函式判斷 unsigned int 是不是 2 的次方

<details>
<summary>參考解答</summary>

```c
int is_power_of_two(unsigned int x) {
    return x != 0 && (x & (x - 1)) == 0;
}
```

2 次方只有一個 bit，`x & (x-1)` 清掉它 = 0；`x != 0` 排除 0。Ch 7。
</details>

### Q9（Ch 12）實作 strcpy，並說出它的安全問題

<details>
<summary>參考解答</summary>

```c
char *my_strcpy(char *dest, const char *src) {
    char *ret = dest;
    while ((*dest++ = *src++)) ;   // 複製到（含）'\0'
    return ret;
}
```

安全問題：**不檢查 dest 大小，src 太長會 buffer overflow**。緩解：用 strncpy 限大小（但 strncpy 可能不補 `\0`，要手動補）。Ch 12。
</details>

### Q10（Ch 12, 7）in-place reverse 一個字串

<details>
<summary>參考解答</summary>

```c
#include <string.h>
void reverse(char *s) {
    int i = 0, j = (int)strlen(s) - 1;
    while (i < j) {
        char t = s[i]; s[i] = s[j]; s[j] = t;
        i++; j--;
    }
}
```

雙指標頭尾交換，到中間停。`j = len-1`（不含 `\0`）。Ch 12。
</details>

### Q11（Ch 4, 5）寫一個用函式指標陣列實作的計算機（+、-、*）

<details>
<summary>參考解答</summary>

```c
int add(int a,int b){return a+b;}
int sub(int a,int b){return a-b;}
int mul(int a,int b){return a*b;}

int calc(int op, int a, int b) {
    int (*ops[])(int,int) = {add, sub, mul};
    if (op < 0 || op >= 3) return 0;   // 邊界檢查必加！
    return ops[op](a, b);
}
```

要點：函式指標陣列、邊界檢查（op 越界）。Ch 5。
</details>

---

## 第三部分：找 bug / 評論題

### Q12（Ch 11）這段有什麼問題？

```c
char *make_greeting(void) {
    char buf[64];
    sprintf(buf, "Hello");
    return buf;
}
```

<details>
<summary>答案</summary>

**回傳 stack 區域變數 `buf` 的位址（dangling）**——函式返回後 buf 失效，呼叫者用回傳值 = UB。修法：用 `static`（非可重入）、`malloc`（呼叫者 free）、或讓呼叫者傳入緩衝區。Ch 11。
</details>

### Q13（Ch 3, 11）這段為什麼可能無窮迴圈？

```c
int flag = 0;
void isr(void) { flag = 1; }     // 中斷時被呼叫
int main(void) {
    while (flag == 0) { }        // 等中斷
    return 0;
}
```

<details>
<summary>答案</summary>

`flag` 沒宣告 **volatile**——編譯器可能把 `flag` 讀進暫存器一次後不再讀記憶體（最佳化），導致 ISR 改了 flag、主迴圈卻看不到，永遠卡死。修法：`volatile int flag = 0;`。Ch 3（volatile 三場景之一）。
</details>

### Q14（Ch 11）這段為什麼會 crash？

```c
char *p = "hello";
p[0] = 'H';
```

<details>
<summary>答案</summary>

`"hello"` 是 text 段的**唯讀**字串字面值，`p[0]='H'` 試圖改唯讀記憶體 = UB（多半 segfault）。修法：`char arr[] = "hello"; arr[0]='H';`（arr 在 stack，是可改副本）。Ch 11。
</details>

### Q15（Ch 9）這個迴圈會怎樣？

```c
unsigned int i;
for (i = 10; i >= 0; i--)
    printf("%u ", i);
```

<details>
<summary>答案</summary>

**無窮迴圈**。`i` 是 unsigned，永遠 `>= 0`；減到 0 後 `i--` 環繞成 4294967295，條件永真。修法：用 signed `int i`。Ch 9。
</details>

---

## 自評與弱點分析

對完答案後，統計各章錯題：

| 題號 | 章節 | 考點 |
|---|---|---|
| Q1 | Ch 1,2 | static 保值 |
| Q2 | Ch 9 | signed/unsigned 轉型 |
| Q3 | Ch 4,10 | 指標運算 + 求值順序 |
| Q4 | Ch 8 | struct 對齊 sizeof |
| Q5 | Ch 6 | 巨集括號 |
| Q6 | Ch 10 | sequence point UB |
| Q7-8 | Ch 7 | bit 操作 |
| Q9-10 | Ch 12 | 字串函式 |
| Q11 | Ch 5 | 函式指標陣列 |
| Q12,14 | Ch 11 | 記憶體（dangling/唯讀）|
| Q13 | Ch 3 | volatile |
| Q15 | Ch 9 | unsigned 迴圈 |

- **錯 3 題以上某類**（如 bit、指標、記憶體）→ 那章回去重讀 + 重做題。
- **手寫題（Q7–11）寫不出來**→ 上機考會卡，務必練到能默寫。
- **找 bug 題（Q12–15）看不出來**→ 這些是韌體最在意的（記憶體安全、volatile），重點補。

## 如果你卡住了

1. **觀念題不確定**：別硬猜，回對應章看「踩雷集錦」——那裡列了最常錯的點。
2. **手寫題寫不出**：先寫「能跑的笨版本」再優化。上機考能跑 > 漂亮但編不過。
3. **找 bug 看不出**：問自己「這個變數住哪段記憶體（Ch 11）」「有沒有 volatile/邊界/`\0` 問題」。
4. **時間不夠**：先掃完所有題目挑會的做（觀念題快、手寫題慢），別卡在一題。

## 延伸挑戰

1. **把所有手寫題真的編譯跑過**（`gcc -Wall`），看編譯器的警告——很多陷阱 `-Wall` 會提醒。
2. **再做一輪 [發哥上機考整理](https://hackmd.io/@Rance/SkSJL_5gX)** 的原始題目，對照本練習。
3. **限時做**：第二次嘗試壓到 45 分鐘，逼近真實上機考速度。

## 自我檢核

- [ ] 觀念輸出題我能準確說出「印什麼/會不會編譯」，且知道哪些是 UB（不亂給答案）
- [ ] 手寫題（bit 操作、strcpy、reverse、函式指標陣列）我能在電腦上默寫並編譯通過
- [ ] 找 bug 題我能看出 dangling、唯讀字串、缺 volatile、unsigned 迴圈這些經典問題
- [ ] 我找出了自己最弱的 2-3 類，並回該章補強

Part 1（C 核心）綜合驗收完成。Part 2 進入這個職位的差異化關鍵——嵌入式/韌體專屬考點，從存取固定記憶體位址開始。

→ [Ch 13 存取固定記憶體位址與 memory-mapped I/O](./13-memory-mapped-io.md)
