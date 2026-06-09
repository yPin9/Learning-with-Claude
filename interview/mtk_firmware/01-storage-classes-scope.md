# Ch 1 — 變數、儲存類別與作用域

> **目標**：把 C 變數的「住哪裡（儲存）、活多久（生命週期）、誰看得到（作用域）」三件事一次釐清。這是 static/extern 考題、記憶體模型、初始化值等一堆 C 題的共同地基。

> **環境**：C（C99/C11），`gcc -Wall`。

## 為什麼考這個

幾乎每個 C 上機觀念題都繞著「這個變數初始值是多少、能不能被別的檔案看到、函式返回後還在不在」打轉。這些答案全由**儲存類別（storage class）**決定。把這章的表記熟，static（Ch 2）、記憶體模型（Ch 11）、整數陷阱（Ch 9）都會順。

## 先建立直覺：三個獨立的問題

一個變數有三個常被混為一談、其實獨立的屬性：

```
   1. 儲存位置（storage）：住哪？  → stack / data / bss / 暫存器
   2. 生命週期（lifetime）：活多久？ → 整個程式 / 進入區塊到離開
   3. 連結 / 作用域（linkage/scope）：誰看得到？ → 此檔 / 此區塊 / 全專案
```

「儲存類別關鍵字」（auto/register/static/extern）就是在設定這三個屬性的組合。記住這三軸分開想，就不會亂。

## 概念複習：四個儲存類別

| 關鍵字 | 儲存位置 | 生命週期 | 作用域/連結 | 預設初始值 |
|---|---|---|---|---|
| `auto`（區域變數預設）| stack | 進入區塊~離開 | 區塊內 | **不初始化（垃圾值）** |
| `register` | 建議放暫存器 | 進入區塊~離開 | 區塊內 | 垃圾值 |
| `static`（區域）| data/bss | **整個程式** | 區塊內 | **0** |
| `static`（全域/函式）| data/bss | 整個程式 | **僅本檔（internal linkage）** | 0 |
| `extern` | data/bss | 整個程式 | 全專案（external linkage）| 0 |

幾個關鍵：

- **區域變數（auto）不會自動歸零**——預設是垃圾值（stack 上殘留）。這是無數 bug 的來源，也是考古題愛考的（「印出什麼？」答案是「未定義」）。
- **static 和全域變數預設歸零**——它們在 data/bss 段，C 標準保證靜態儲存期變數初始化為 0（指標為 NULL）。
- **bss vs data**：初始化成非 0 的放 data 段（值要存在執行檔裡）；初始化成 0 或沒初始化的靜態變數放 bss 段（執行檔不存內容、載入時清零，省檔案大小）。Ch 11 會再提。

## 作用域 vs 連結（容易混）

- **作用域（scope）**：原始碼中「這個名字看得見」的範圍。block scope（`{}` 內）、file scope（檔案內）。
- **連結（linkage）**：跨「翻譯單元（檔案）」時，同名符號是否指同一個東西。
  - **external linkage**：全專案唯一（一般全域變數、函式）——別的檔案 `extern` 得到。
  - **internal linkage**：僅本檔（`static` 全域變數/函式）——別的檔案看不到。
  - **no linkage**：區域變數。

口訣：**`static` 對全域變數的作用是「藏起來，只給本檔用」（internal linkage）；對區域變數的作用是「延長壽命到整個程式」（static lifetime）**。同一個關鍵字，兩種看似不同的效果——其實都是「靜態儲存期 + 限制可見性」（Ch 2 細講）。

## 考古題詳解

### Q1：以下程式印出什麼？

```c
#include <stdio.h>
void counter(void) {
    int a = 0;
    static int b = 0;
    a++; b++;
    printf("a=%d b=%d\n", a, b);
}
int main(void) {
    counter(); counter(); counter();
    return 0;
}
```

<details>
<summary>詳解</summary>

```
a=1 b=1
a=1 b=2
a=1 b=3
```

- `a` 是 auto，每次進 `counter` 重新建立、初始化為 0、`a++` 後是 1，函式返回就消失——所以每次都是 1。
- `b` 是 static，**只初始化一次**，生命週期是整個程式，跨呼叫保留值——所以 1、2、3 遞增。

**考點**：static 區域變數「初始化一次、跨呼叫保值」。這是 static 最經典的考法。
</details>

### Q2：這段印出的 x 是多少？

```c
#include <stdio.h>
int main(void) {
    int x;            // 區域、未初始化
    printf("%d\n", x);
    return 0;
}
```

<details>
<summary>詳解</summary>

**未定義（垃圾值，可能任何數）。** 區域變數 `x` 是 auto，不會自動歸零，值是 stack 上的殘留。

陷阱：很多人答 0。**只有靜態儲存期（static/全域）才保證歸零，區域變數不會。** `gcc -Wall` 會警告 `x is used uninitialized`。

**考點**：區分「區域變數（垃圾）vs 靜態/全域（0）」的預設初始值。
</details>

### Q3：static 全域變數 + 多檔案，會編譯成功嗎？

```c
/* file1.c */
static int g = 5;
void f1(void) { g++; }

/* file2.c */
extern int g;     // 想用 file1 的 g
void f2(void) { g++; }
```

<details>
<summary>詳解</summary>

**連結錯誤（linker error）：file2 找不到 `g`。**

`file1.c` 的 `g` 是 `static`（internal linkage），只在 file1 可見。file2 的 `extern int g` 想找一個 external linkage 的 `g`，但找不到——linker 報 undefined reference。

如果把 file1 的 `static` 拿掉（變成一般全域，external linkage），file2 的 `extern` 就能連到它。

**考點**：`static` 全域 = internal linkage = 別檔 extern 不到。這考「static 限制可見性」的作用。
</details>

### Q4：register 變數能取址嗎？

```c
register int x = 10;
int *p = &x;       // ?
```

<details>
<summary>詳解</summary>

**不行，編譯錯誤。** `register` 建議編譯器把變數放暫存器，而暫存器沒有記憶體位址，所以**不能對 register 變數取址（`&`）**。

（註：現代編譯器多半忽略 `register` 提示、自己決定，但「不能取址」這個語言限制仍在。`register` 在現代 C 幾乎沒用，但面試愛考這個限制。）

**考點**：register 不能取址。
</details>

## 踩雷集錦

1. **「區域變數預設是 0」**：錯，是垃圾值。只有 static/全域才預設 0。這是最常答錯的。
2. **把作用域和連結搞混**：作用域是「原始碼看得見的範圍」，連結是「跨檔案是否同一個」。static 全域是「file scope + internal linkage」。
3. **以為 static 區域變數每次呼叫重新初始化**：不，只初始化一次（程式啟動時），之後跨呼叫保值。
4. **register 取址**：不能。`&register_var` 編譯錯。
5. **`extern int x = 5;` 在函式內**：`extern` 是宣告不是定義；在區域加初值語意混亂、多數情況不對。`extern` 通常放檔案層宣告「這個變數定義在別處」。

## 速記

- 三軸獨立想：**住哪（storage）/ 活多久（lifetime）/ 誰看得到（scope+linkage）**。
- 區域 auto = stack + 進出區塊 + 垃圾值；static/全域 = data/bss + 整個程式 + **0**。
- `static` 全域 → internal linkage（藏本檔）；`static` 區域 → static lifetime（保值）。
- `register` 不能取址。

## 自我檢核

- [ ] 不看表，能說出 auto / static 區域 / static 全域 / extern 的「住哪、活多久、誰看得到、預設值」嗎？
- [ ] 面試官問「區域變數沒初始化是 0 嗎」，你會怎麼答？
- [ ] 為什麼 `static` 全域變數別的檔案 `extern` 不到？
- [ ] static 區域變數初始化幾次？跨呼叫保值嗎？

## 延伸閱讀

### 書籍

- **《C Programming Language (K&R)》** — Kernighan & Ritchie
  - **讀哪幾章**：4.6 Static Variables、A8.1 Storage Class（附錄的語言定義）。
  - **和本章的關聯**：儲存類別的權威定義，言簡意賅。

- **《Expert C Programming》** — Peter van der Linden
  - **讀哪幾章**：談 linkage、segment（data/bss/text）的章節。
  - **為什麼值得讀**：把「變數住哪個段、為什麼」講得很透，連 Ch 11 記憶體模型一起補。

### 文章

- **[常見 C 語言觀念題目總整理 — Mr. Opengate](https://www.mropengate.com/2017/08/cc-c.html)**
  - **讀哪裡**：storage class、static、scope 相關題目。
  - **和本章的關聯**：本章考點的延伸題庫。

下一章把 static 單獨拉出來深講——它是 C 面試最高頻的關鍵字，兩種用途都要能秒答。

→ [Ch 2 static 全解](./02-static.md)
