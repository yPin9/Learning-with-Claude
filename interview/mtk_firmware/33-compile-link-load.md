# Ch 33 — 編譯/組譯/連結/載入

> **目標**：搞懂 C 原始碼怎麼變成可執行檔——gcc 的四階段（預處理/編譯/組譯/連結）、object file、linker 做什麼、static vs dynamic library、symbol。MTK 考古題明確問過 compile/link 過程。

> **環境**：C，gcc。前置：Ch 1（連結 linkage）、Ch 6（前處理器）、Ch 11（記憶體段）。

## 為什麼考這個

「`gcc hello.c` 到底做了什麼」「static 和 dynamic library 差在哪」「linker 在做什麼」是計組/系統面試常考——MTK 考古題直接問 compile/link 過程。它測你懂不懂「原始碼到執行檔」的完整鏈，連結 Ch 1（linkage）、Ch 11（記憶體段）。

## 先建立直覺：原始碼到執行檔的四步

```
   hello.c（原始碼）
      │ ① 預處理（Preprocess）：gcc -E
      ▼
   hello.i（展開巨集/include 後的純 C）
      │ ② 編譯（Compile）：gcc -S
      ▼
   hello.s（組合語言）
      │ ③ 組譯（Assemble）：gcc -c / as
      ▼
   hello.o（object file，機器碼但還沒連結）
      │ ④ 連結（Link）：gcc / ld
      ▼
   a.out / hello（可執行檔）
```

`gcc hello.c` 一次做完這四步。理解每步在做什麼，是這章的核心。

## gcc 四階段詳解

### ① 預處理（Preprocess）`gcc -E hello.c`

前處理器（Ch 6）處理 `#` 開頭的指令：
- 展開 `#include`（把標頭檔內容貼進來）
- 展開 `#define` 巨集（文字替換）
- 處理 `#if`/`#ifdef` 條件編譯
- 移除註解

輸出 `.i`（純 C，沒有 `#` 指令了）。這就是 Ch 6 講的前處理器階段。

### ② 編譯（Compile）`gcc -S hello.i`

編譯器把 C 翻成**組合語言**（`.s`）。這是「編譯」最核心的一步——詞法/語法分析、最佳化、產生目標架構的組語（Ch 32）。

輸出 `.s`（人可讀的組語）。

### ③ 組譯（Assemble）`gcc -c hello.s`

組譯器（assembler）把組語翻成**機器碼**，產生 **object file（`.o`）**——機器碼 + 符號表 + 重定位資訊，但**還沒連結**（外部符號的位址還是未知的佔位）。

object file 的內容：
- 機器碼（`.text` 段，Ch 11）
- 資料（`.data`/`.bss`）
- **符號表（symbol table）**：這個檔定義了哪些符號（函式/全域變數）、引用了哪些外部符號
- **重定位資訊（relocation）**：哪些位址要在連結時填

### ④ 連結（Link）`gcc hello.o`

linker（連結器）把多個 `.o` 檔 + 函式庫**組合成一個可執行檔**：
- **符號解析（symbol resolution）**：把每個「引用」對應到「定義」（如 `hello.o` 引用 `printf`，連到 libc 的 printf）。找不到 → undefined reference 錯（Ch 1 的 extern 找不到）。
- **重定位（relocation）**：決定每個段/符號的最終位址，填進所有引用的地方。

輸出可執行檔。這解釋了 Ch 1 的 linkage——external linkage 的符號 linker 能跨檔連到、internal（static）連不到。

## object file 與符號

```
   每個 .o 有符號表，記錄：
   - 定義的符號（defined）：這個檔提供的函式/全域變數
   - 引用的符號（undefined）：這個檔用到但別處定義的（要 linker 解析）

   例：
   main.o：定義 main、引用 printf、引用 helper
   util.o：定義 helper、引用 printf
   linker：把 main.o 的「引用 helper」連到 util.o 的「定義 helper」
          把兩者的「引用 printf」連到 libc 的 printf
```

linker error 的兩種（常考）：
- **undefined reference**：引用了但沒人定義（忘了連某個 `.o`/函式庫、或 static 藏起來了，Ch 1）。
- **multiple definition**：同一個符號被定義多次（兩個 `.o` 都定義同名全域函式，沒加 static，Ch 1/2）。

## static library vs dynamic library（必考）

把常用的 `.o` 打包成函式庫，給多個程式用。兩種：

```
   static library（靜態庫，.a / .lib）：
   - 連結時把用到的部分「複製進可執行檔」
   - 可執行檔自包含（不依賴外部庫）→ 大、但獨立

   dynamic / shared library（動態庫，.so / .dll）：
   - 連結時只記「我要用這個庫」，不複製
   - 執行時才載入庫（多個程式共享同一份庫在記憶體）→ 小、但依賴庫存在
```

對比：

| | static（.a）| dynamic（.so/.dll）|
|---|---|---|
| 連結時機 | 編譯連結時複製進執行檔 | 執行時載入 |
| 執行檔大小 | 大（含庫的副本）| 小（不含庫）|
| 記憶體 | 每個程式各一份 | 多程式共享一份 |
| 依賴 | 自包含（獨立）| 需庫存在（缺庫跑不了）|
| 更新庫 | 要重新編譯連結 | 換 .so 即可（程式不用重編）|
| 啟動速度 | 快（已包含）| 略慢（要載入庫）|

選擇：
- **static**：要獨立、可攜（不依賴目標環境的庫）、韌體（嵌入式常 static，環境固定、要可預測）。
- **dynamic**：省記憶體（多程式共享）、庫可獨立更新（修 bug 不用重編所有程式）、現代桌面/伺服器常用。

> 韌體常用 static link：嵌入式環境固定、要自包含、可預測（沒有 OS 動態載入器，或要把所有東西打進一個 image）。

## 考古題詳解

### Q1：`gcc hello.c` 的完整過程是什麼？

<details>
<summary>詳解</summary>

四階段：
1. **預處理（-E）**：展開 #include/#define、條件編譯、移註解 → `.i`
2. **編譯（-S）**：C → 組語 → `.s`
3. **組譯（-c）**：組語 → 機器碼 object file → `.o`
4. **連結**：多個 `.o` + 函式庫 → 可執行檔（符號解析 + 重定位）

`gcc hello.c` 一次做完。各階段可用 `-E`/`-S`/`-c` 停在中間看。

**考點**：gcc 四階段，MTK 直接考過，必背。
</details>

### Q2：linker 做什麼？

<details>
<summary>詳解</summary>

linker 把多個 object file + 函式庫組合成可執行檔，主要兩件事：
1. **符號解析（symbol resolution）**：把每個「引用」對應到「定義」（如 main.o 引用 printf → 連到 libc）。找不到 → undefined reference。
2. **重定位（relocation）**：決定每個段/符號的最終位址，填進所有引用處。

連結錯誤：undefined reference（沒人定義）、multiple definition（定義多次）——連 Ch 1 的 linkage。

**考點**：linker 的工作，高頻。
</details>

### Q3：static library 和 dynamic library 差在哪？

<details>
<summary>詳解</summary>

- **static（.a）**：連結時把用到的部分複製進可執行檔。執行檔大、自包含（獨立）、更新庫要重編。
- **dynamic（.so/.dll）**：只記依賴，執行時載入庫。執行檔小、多程式共享一份、庫可獨立更新（不用重編程式）、但需庫存在。

選擇：要獨立/可攜/韌體 → static；省記憶體/庫易更新 → dynamic。

**考點**：static vs dynamic library，必考。
</details>

### Q4：undefined reference 和 multiple definition 各是什麼錯？

<details>
<summary>詳解</summary>

- **undefined reference**：引用了某符號但沒人定義——忘了連某個 `.o`/函式庫、函式名拼錯、或符號被 `static` 藏起來別檔連不到（Ch 1/2）。
- **multiple definition**：同一符號被定義多次——兩個 `.o` 都定義同名全域函式/變數（沒加 static，external linkage 衝突，Ch 1）。

都是 linker（連結階段）的錯，不是編譯階段。

**考點**：兩種 linker error，串 Ch 1/2。
</details>

### Q5：為什麼韌體常用 static link？

<details>
<summary>詳解</summary>

幾個原因：
1. **環境固定**：嵌入式硬體環境確定，不需要動態更新庫。
2. **自包含**：要把所有東西打進一個 firmware image（可能沒有 OS 的動態載入器）。
3. **可預測**：static link 的記憶體佈局、行為在編譯期確定（即時系統要可預測，Ch 18）。
4. **沒有動態載入基礎設施**：裸機/簡單 RTOS 可能沒有 .so 的載入機制。

所以韌體幾乎都 static link 成一個 image。

**考點**：韌體 static link，串 Ch 18/19。
</details>

## 踩雷集錦

1. **四階段順序/名稱記錯**：預處理 → 編譯（C→組語）→ 組譯（組語→機器碼）→ 連結。「編譯」狹義只到組語，「組譯」才到機器碼。
2. **以為 linker error 是編譯錯**：undefined reference / multiple definition 是**連結**階段（所有 .o 編譯都過了才連結）。
3. **static/dynamic library 搞反**：static 複製進執行檔（大、獨立）；dynamic 執行時載入（小、共享、依賴庫）。
4. **以為 .o 能直接執行**：`.o` 是 object file（未連結，外部符號未解析），不能直接跑，要 link 成可執行檔。
5. **printf 找不到（undefined reference）**：通常是忘了連函式庫（如數學庫要 `-lm`），或符號被 static 藏。
6. **不知道韌體為何 static**：環境固定、自包含、可預測（Ch 18/19）。

## 速記

- **gcc 四階段**：**預處理**（-E，展開 #include/#define）→ **編譯**（-S，C→組語）→ **組譯**（-c，組語→機器碼 .o）→ **連結**（.o + 庫 → 可執行檔）。
- **linker 做**：符號解析（引用↔定義）+ 重定位（填位址）。錯誤：undefined reference（沒定義）、multiple definition（定義多次）——連 Ch 1 linkage。
- **static library（.a）**：複製進執行檔（大/獨立/更新要重編）；**dynamic（.so/.dll）**：執行時載入（小/共享/庫可獨立更新/依賴庫）。
- 韌體常 **static link**（環境固定、自包含、可預測，Ch 18/19）。
- `.o` 不能直接跑（未連結）。

## 自我檢核

- [ ] `gcc hello.c` 的四階段是什麼？各產生什麼檔？
- [ ] linker 做哪兩件事？undefined reference 和 multiple definition 各是什麼？
- [ ] static 和 dynamic library 差在哪？各的優缺點？
- [ ] 為什麼韌體常用 static link？
- [ ] linker error 是編譯階段還是連結階段的錯？

## 延伸閱讀

### 本 repo

- **[compilers/elf_linking](../../compilers/elf_linking/README.md)**
  - **這門課的定位**：ELF / relocation / linker script 的完整課程。本章只是面試概觀，想深入連結/載入讀這門。

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — Ch 7 Linking
  - **讀哪幾章**：7.1–7.6（編譯系統、object file、符號解析、重定位、static/dynamic library）。
  - **和本章的關聯**：連結的權威，把符號解析/重定位/庫講到底，本章源頭。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：compile/link 過程題。
  - **和本章的關聯**：MTK 考過 compile/link（GeeksforGeeks 也提到）。

連結載入懂了，下一章是 CPU 怎麼和 I/O 裝置溝通——I/O、DMA、匯流排。

→ [Ch 34 I/O 與 DMA、匯流排](./34-io-dma-bus.md)
