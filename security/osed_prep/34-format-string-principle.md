# Ch 34 — Format string 原理（%x/%n/%s，Windows vs Linux）

> **目標**：搞懂 format string 漏洞的成因——`printf` 類函式怎麼被騙去讀 stack 上「不屬於它的」資料，`%x`/`%p`/`%s`/`%n` 各自在做什麼、為什麼能被攻擊者濫用。最關鍵的一點：**Windows 的 Visual C runtime 預設停用 `%n`**，這和 Linux glibc 不一樣，直接決定 OSED 這類題目能不能走到「任意寫」。

> **環境**：Win10 x64 + WinDbg（2026）+ Python 3

## 為什麼需要這一章

Part 2–3 練的 stack/SEH overflow，漏洞成因是「複製資料時沒檢查長度」——程式明確呼叫了 `strcpy`/`memcpy` 之類會溢出的函式。Format string 漏洞是另一種性質：程式呼叫的函式（`printf`、`sprintf`、`fprintf`...）本身**沒有任何長度問題**，問題出在**第一個引數**——那個決定「要印什麼格式」的格式字串（format string），被攻擊者直接控制了。

```c
// 安全寫法：使用者輸入永遠是「資料」，不是「格式」
printf("%s", user_input);

// 危險寫法：使用者輸入本身被當成格式字串解讀
printf(user_input);
```

兩行表面上都是「印出使用者輸入的字串」，結果看起來一樣——直到使用者送進去的字串裡含有 `%x`、`%s`、`%n` 這類格式指定符（format specifier）。那一刻，`printf` 不再是單純的輸出函式，而是變成一台「讀取/寫入任意記憶體」的機器，而操控它的方向盤握在攻擊者手上。

這類漏洞在真實世界已經少見（現代編譯器 `-Wformat-security`、靜態分析工具都會抓 `printf(user_input)` 這種模式），但 OSED 的 EXP-301 課程仍把它當成一個完整模組教，原因是它完美示範了「程式邏輯沒錯、呼叫慣例被濫用」這條攻擊思路，而且牽出 Windows vs Linux CRT 行為差異這個很容易踩雷又很少人講清楚的主題。

## 先建立直覺：printf 怎麼「知道」要印幾個引數

`printf` 是 **varargs（可變引數）函式**——它的宣告是 `int printf(const char *format, ...)`，呼叫端可以傳 0 個、1 個、10 個額外引數，`printf` 內部完全靠**解析 format string 裡的 `%` 符號**來決定要往後讀幾個引數、每個引數要當什麼型別處理。它**不知道**呼叫端實際傳了幾個引數，也**沒有辦法**知道——這個資訊只存在 format string 裡。

正常呼叫：

```c
printf("x=%d, y=%s\n", x, y);
```

這裡 `printf` 看到 2 個 `%`，就去 stack 上把對應位置的 2 個值撈出來，依序當 `int`、`char*` 解讀。一切正常，因為呼叫端確實傳了 2 個對應的引數。

現在來看 x86 cdecl 呼叫慣例下，stack 長什麼樣：

```
呼叫 printf("x=%d, y=%s\n", x, y) 之後，stack（由高位址到低位址，call 剛發生時）：

高位址
┌─────────────────────┐
│  y 的值（char* 指標） │   ← printf 讀第 2 個 %：%s
├─────────────────────┤
│  x 的值（int）        │   ← printf 讀第 1 個 %：%d
├─────────────────────┤
│  format string 指標   │   ← printf 的第 1 個引數（format）
├─────────────────────┤
│  return address       │   ← call printf 時 push 的回傳位址
└─────────────────────┘
低位址                   ← ESP 指向這裡（呼叫瞬間）
```

cdecl 呼叫慣例的引數是**右到左依序 push 到 stack**（Ch 3 已講過，這裡直接用），所以最後一個引數 `y` 離 `format string` 最近、`x` 第二近。`printf` 內部拿到 format string 指標後，每解析到一個 `%X`，就往「format string 指標的上方（高位址方向）」移動一個 slot 去撈值——這正是它找到 `x`、`y` 的方法。

**關鍵認知**：`printf` 讀取引數靠的完全是「相對位置」的推算，不是真的知道 stack 上哪裡是「它的」資料。只要你能讓它解析出比實際傳入引數更多的 `%`，它就會繼續往上層 stack（呼叫者的 local 變數、甚至更上層的 saved 暫存器、saved return address 區域）撈資料——而那些資料**根本不是要給它的**。

## 漏洞核心：`printf(user_input)` 讓攻擊者控制 % 的數量與種類

當程式寫成：

```c
char buf[256];
recv(sock, buf, sizeof(buf), 0);
printf(buf);              // 漏洞：buf 直接當 format string
```

`buf` 裡的內容完全由攻擊者決定。如果攻擊者送入 `"%x%x%x%x"`，`printf` 就會乖乖去 stack 上**它認為該在的位置**撈 4 個值，印成 16 進位——但這次呼叫端根本沒有傳任何額外引數。那 4 個值從哪來？就是呼叫 `printf(buf)` 這行當下，stack 上剛好在那個位置的任意資料——可能是上一個函式呼叫留下的殘餘值、區域變數、暫存器 spill，什麼都有可能。

```
攻擊者送入 "%x%x%x%x" 之後，呼叫 printf(buf) 的 stack：

高位址
┌─────────────────────┐
│  (不屬於這次呼叫的值)  │  ← 第 4 個 %x 撈到這裡（可能是呼叫者的區域變數）
├─────────────────────┤
│  (不屬於這次呼叫的值)  │  ← 第 3 個 %x
├─────────────────────┤
│  (不屬於這次呼叫的值)  │  ← 第 2 個 %x
├─────────────────────┤
│  buf 指標（format）    │  ← printf 唯一真正收到的引數
├─────────────────────┤
│  return address       │
└─────────────────────┘
低位址

printf 完全不知道、也不檢查這次呼叫到底傳了幾個「額外引數」，
它只看 format string 裡有幾個 %，就往上撈幾個 slot。
```

這就是為什麼這類漏洞叫「format string 漏洞」而不是「printf 漏洞」——根因是**把不可信輸入當格式字串解析**，`printf`／`sprintf`／`fprintf`／`_snprintf` 等所有吃 format string 的 C 函式都有一樣的風險。

## 核心格式指定符逐一拆解

### %x — 讀出下一個 slot，印成 16 進位（不解參考）

```c
printf("%x");
```

`printf` 把 stack 上「下一個引數位置」的 **4 bytes 值本身**當成數字，印成 16 進位文字。它**不會**把這個值當指標去讀取記憶體內容——`%x` 只是把 stack 上那個 slot 的原始 bit pattern印出來。

這個特性正是 Ch 35「任意讀」技巧的起點：連續送一串 `%x`，就能把 stack 往深處一路印出來，直到印到攻擊者自己送進去的某個已知值（例如 `AAAA` = `0x41414141`）——藉此算出「第幾個 `%x` 對應到攻擊者可控的 buffer」，這個數字叫 **offset**。

### %p — 跟 %x 類似，印出帶位址格式的 16 進位

`%p` 本質上也是讀 stack 下一個 slot 印成 16 進位，差異只在印出格式（通常帶 `0x` 前綴、對齊方式依 CRT 實作）。在 exploit 開發裡常拿來快速掃一輪 stack 找看起來像指標的值。

### %s — 把 slot 的值當指標解參考，印出字串

```c
printf("%s");
```

這裡性質變了：`%s` 把撈到的 slot 值**當成一個指標**，然後去那個位址讀取記憶體內容，當成 C 字串（直到遇到 `\0`）印出來。

這一步是「任意讀」的核心武器：如果攻擊者能控制某個 slot 的值（例如透過 `%x` 定位出 offset 後，讓那個 slot 剛好落在自己送入的 buffer 上），就能把 buffer 裡放的任意 4-byte 值當成**要讀取的位址**，用 `%s` 把那個位址的記憶體內容洩漏（leak）出來。

**邊界情況**：如果 slot 裡的值不是一個合法可讀位址（例如剛好是個小整數、或指向未對映記憶體），`%s` 解參考會直接造成 access violation，程式當掉。這在真實攻擊中是常態——offset 算錯一格，或 leak 用的位址猜錯，服務就會崩潰，必須重新連線再試。

### %n — 把「目前已輸出的字元數」寫到 slot 指向的位址

```c
printf("AAAA%n");
```

`%n` 的行為和前面三個完全不同：它**不印任何東西**，而是把「截至目前 `printf` 已經輸出了多少個字元」這個數字，**寫入**到 slot 值所指向的記憶體位址。上例中 `AAAA` 已輸出 4 個字元，所以 `%n` 會把整數 `4` 寫到「下一個 slot 的值」所指的位址。

這是整個 format string 漏洞系列裡最具破壞力的指定符：它把「讀取」升級成「寫入」。只要能控制 slot 的值（指向哪裡）以及控制「目前已輸出字元數」（寫入什麼值，靠寬度指定符如 `%100x` 撐大輸出字元數來控制），理論上就能**任意寫任意值到任意位址**——Ch 36 整章都在拆這條路。

## 底層機制：為什麼 cdecl + varargs 讓這一切可能

把三件事串起來看：

```
① cdecl 呼叫慣例：所有引數由呼叫者 push 到 stack，printf 內部用 ESP/EBP 相對位移存取。
② varargs 機制：printf 的宣告只固定第一個引數（format string 指標），
   其餘引數「有幾個、是什麼型別」完全不在函式簽章裡，
   printf 內部純粹靠解析 format string 字串動態決定要讀幾個、怎麼讀。
③ printf(user_input)：format string 本身變成攻擊者送入的資料，
   「要讀幾個、用哪個指定符讀」的決定權，從程式開發者手上轉移到攻擊者手上。

      攻擊者輸入                      printf 內部邏輯
   ┌───────────────┐             ┌───────────────────────┐
   │ "%x%x%s%n"     │ ──解析──▶  │ 看到 4 個 %，往 stack   │
   └───────────────┘             │ 上方依序撈 4 個 slot：  │
                                  │  slot1 → %x（印數字）   │
                                  │  slot2 → %x（印數字）   │
                                  │  slot3 → %s（當指標解參考）│
                                  │  slot4 → %n（當指標寫入）│
                                  └───────────────────────┘
```

這三個條件缺一個，漏洞都不成立：如果是 x64 的 Windows 呼叫慣例（前 4 個整數/指標引數走暫存器 `RCX/RDX/R8/R9`，不是全部在 stack 上），`printf` 的 varargs 存取方式會更複雜（仍然可利用，但定位 offset 的手法不同，這也是 OSED 鎖死 x86 的原因之一，Ch 40 會補 x64 差異）；如果 `printf` 不是 varargs 而是固定引數型別檢查的函式（像某些語言的格式化 API 在編譯期就檢查型別數量），攻擊者送再多 `%x` 也沒用。

## 關鍵誠實標注：Windows VC runtime 預設停用 %n

這是本章、也是 Part 9 全程最重要的一個事實，必須說清楚：

> **Windows 的 Visual C runtime（MSVCRT / UCRT）預設讓 `%n` 失效**。從 Visual Studio 2005 起（官方文件可查，見延伸閱讀），微軟把 `%n` 的處理關閉，當 `printf` 系列函式解析到 `%n` 時，**不會執行寫入**，行為依版本可能是忽略、或呼叫 invalid parameter handler（可能直接觸發 CRT 的例外處理，導致程式中止）。這是微軟針對 format string 漏洞類別做的**預設安全加固**。
>
> 要讓 `%n` 重新啟用，程式必須在執行期**主動呼叫** `_set_printf_count_output(1)`（UCRT/MSVCRT 提供的 API，詳見延伸閱讀的 MSDN 頁）。只有目標程式自己做了這件事，`%n` 才會真的寫入記憶體。
>
> 這和 **Linux 的 glibc 完全不同**——glibc 的 `printf` 家族預設就支援 `%n`，不需要任何額外設定，這也是為什麼早期（2000 年代）Linux/Unix 的 format string exploit 教材幾乎都直接演示 `%n` 任意寫，而不特別強調「前提」。

**這對 OSED 的意義**：如果你在 OSED 的靶機或考試情境遇到一個 format string 漏洞，**先不要預設 `%n` 能用**。能走到「任意寫」的題目，通常是出題者刻意讓目標程式呼叫了 `_set_printf_count_output(1)`，或者目標用的不是標準 VC runtime（例如某些自行實作、或移植自 Linux 的格式化函式庫，行為跟著 glibc 走，天生支援 `%n`）。遇到題目時第一件要查證的事：**這個二進位用的 `printf` 實作，`%n` 到底有沒有被啟用**——可以從原始碼找 `_set_printf_count_output` 呼叫，或在 WinDbg 裡實際送 `%n` 觀察行為（有寫入 vs 直接 access violation/無反應）來確認。

## 對比：Windows VC runtime vs Linux glibc 的 format string 行為

| 特性 | Linux glibc | Windows VC runtime（MSVCRT/UCRT） |
|---|---|---|
| `%n` 預設行為 | 直接啟用，寫入字元數到指標位址 | **預設停用**，需呼叫 `_set_printf_count_output(1)` 才會寫入 |
| `%n` 停用時的行為 | N/A（本來就啟用） | 依版本可能忽略該指定符、或觸發 invalid parameter handler（可能中止程式） |
| `%N$`（位置參數，direct parameter access） | 支援（如 `%3$x` 直接讀第 3 個引數） | 支援程度**以目標 CRT 實測為準**——不同 MSVCRT/UCRT 版本行為有落差，寫 exploit 前務必在目標環境實測確認 |
| 引數傳遞方式（x86） | cdecl，全部走 stack | 同樣 cdecl，全部走 stack（x86 上行為接近，這也是 OSED 鎖 x86 的原因之一） |
| 歷史上出現頻率 | 2000 年代大量真實 CVE | 較少見真實世界案例，多為刻意構造的教學/考試情境 |

> **位置參數 `%N$` 的提醒**：glibc 支援 `%N$x` 這種「直接指定要讀第幾個引數」的語法（direct parameter access），對定位 offset 非常方便。Windows CRT 對這個語法的支援**不一致**，不同版本（VS2015 runtime、UCRT、不同的 `_vsnprintf` 變體）行為可能不同，甚至可能完全不支援或解析方式不同。本課後續範例遇到需要位置參數時，會明確標「以目標 CRT 實測為準」，不假設它一定能用。

## 踩雷集錦

1. **預設 `%n` 能寫入，結果毫無反應或直接當機**：這是本章反覆強調的重點——先確認目標是否呼叫過 `_set_printf_count_output(1)`，否則整條任意寫的路直接走不通。
2. **把 `%s` 讀到的任意位址當成「安全」操作**：`%s` 本質是解參考，撈到的值如果不是合法可讀位址，直接 access violation。送出前先用 `%x` 定位清楚 offset，別一步跳到 `%s`。
3. **誤以為 x86 和 x64 的 format string 攻擊手法一樣**：x64 Windows 呼叫慣例前 4 個引數走暫存器，varargs 函式（像 `printf`）仍會把所有引數 spill 到 stack 上供 `va_list` 存取，但位置計算和 x86 不同，照抄 x86 的 offset 公式會算錯。OSED 全程 x86，先把 x86 模型吃透。
4. **照搬 Linux glibc 的 payload 範例直接丟到 Windows 目標**：網路上大量 format string 教學是 glibc 情境，`%n` 直接可用、位置參數語法成熟。搬到 Windows 目標前，务必重新驗證每個指定符的實際行為。
5. **忽略 `%` 指定符對齊問題導致誤判 offset**：`%x` 每次撈 4 bytes（32-bit int），但如果 payload 裡混用 `%s`（撈指標，一樣 4 bytes 但語意不同）或寬度/precision 修飾符，數錢（計算第幾個 slot）容易算錯一格，Ch 35 會給出具體定位流程。

## 進階：再往深一層

- **`_vsnprintf`/`vsprintf_s` 等變體的差異**：Windows CRT 有一大家族格式化函式（`printf`/`sprintf`/`fprintf`/`_snprintf`/`vsprintf_s`...），`_s`（safe）結尾的變體通常對格式字串本身或緩衝區大小多一層檢查，但**不代表 `%n` 的啟用狀態不同**——`%n` 的停用是 CRT 層級的全域行為，不是某個函式單獨決定的。
- **UCRT vs 舊版 MSVCRT 的差異**：Windows 10 開始，CRT 拆成通用層（UCRT）和各版本的 VC runtime。`_set_printf_count_output` 的行為細節（Ch 36 會再提）在不同 UCRT 版本間可能有微調，考場遇到舊版（VS2010/2013 runtime 編譯的老靶機）要留意行為可能和最新 UCRT 不完全一致。
- **編譯器層的防禦**：現代 MSVC/GCC 在編譯期就會對 `printf(user_input)` 這種模式發警告（`-Wformat-security` / `/analyze`），OSED 的範例程式通常刻意關閉警告或用動態拼接繞過靜態檢查，練習時留意這點，不代表真實世界這種漏洞還很常見。

## 本章重點整理

- Format string 漏洞根因：把不可信輸入直接當 `printf` 的 format string 參數，使攻擊者能決定「讀幾個引數、用什麼指定符讀」。
- `%x`/`%p`：讀 stack 下一個 slot 的原始值，印成 16 進位，**不解參考**。
- `%s`：把 slot 值當指標**解參考**，印出該位址的字串內容——任意讀的起點。
- `%n`：把「目前已輸出字元數」**寫入**到 slot 值指向的位址——任意寫的起點。
- **Windows VC runtime 預設停用 `%n`**，需要目標程式呼叫 `_set_printf_count_output(1)` 才會真正寫入；Linux glibc 預設啟用。這是 OSED 情境下判斷「這題能不能打到任意寫」的第一個查證項。
- `%N$`（位置參數）在 Windows CRT 上的支援程度不一致，務必以目標實測為準。
- 全程基於 x86 cdecl：引數右到左 push 到 stack，`printf` 靠解析 format string 的 `%` 數量往 stack 高位址方向依序撈值。

## 自我檢核

- [ ] 能解釋為什麼 `printf(user_input)` 和 `printf("%s", user_input)` 行為天差地遠
- [ ] 能畫出 cdecl 呼叫 `printf` 時 stack 上引數的排列順序
- [ ] 能說出 `%x`、`%s`、`%n` 三者分別是「讀值」「解參考讀」還是「寫入」
- [ ] 能講出 Windows VC runtime 和 Linux glibc 在 `%n` 預設行為上的具體差異，以及啟用 `%n` 需要呼叫哪個 API
- [ ] 遇到一個新的 format string 題目時，知道第一件要查證的事是什麼

## 延伸閱讀

- **"Exploiting Format String Vulnerabilities"** — scut / team teso（原始論文，2001）
  - **讀哪裡**：第 2–3 節，format string 基本原理與 `%n` 任意寫的最早系統化描述。
  - **學什麼**：這類漏洞最原始的理論框架，本章 `%x`/`%s`/`%n` 的拆解基本沿用這篇的分類方式。
  - **前提**：原文以 Linux/glibc 為背景，讀時留意本章已標注的 Windows 行為差異，不要直接假設 `%n` 能用。

- **[`printf`, `wprintf`, `_printf_l`, `_wprintf_l` — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/printf-printf-l-wprintf-wprintf-l)**
  - **讀哪裡**：格式指定符表格（Format Specification Syntax 連結頁）與備註段落。
  - **學什麼**：Windows CRT 對各個格式指定符的官方定義，寫 payload 前的第一手查證來源。
  - **前提**：無，但要留意文件版本對應的 Visual Studio 世代。

- **[`_set_printf_count_output` — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/set-printf-count-output)**
  - **讀哪裡**：整頁，特別是「備註」段落解釋為何預設停用 `%n`。
  - **學什麼**：本章「Windows 預設停用 %n」這個關鍵事實的官方出處，以及如何啟用。
  - **前提**：無，這是本章最該親自核對一次的頁面。

- **[Connor McGarr — Windows Exploit Development 系列（format string 相關文章）](https://connormcgarr.github.io/)**
  - **讀哪裡**：站內搜尋 format string 相關文章，若當下沒有專文，讀其 ROP/shellcode 系列建立 Windows exploit dev 寫作風格的對照感。
  - **學什麼**：現代 Windows 視角寫 exploit 文章的拆解方式，和 OSED 的敘事風格高度一致。
  - **前提**：先讀完本章與 Ch 3 的呼叫慣例基礎。

- **[Corelan — Exploit Writing Tutorial part 7: Unicode (from 0 to 0wn)](https://www.corelan.be/index.php/articles/)**（系列總覽頁）
  - **讀哪裡**：系列目錄頁，找 format string 相關篇章（若有收錄）。
  - **學什麼**：Corelan 系列對 Windows exploit 各類漏洞的一致敘事框架，交叉比對本課風格。
  - **前提**：先掌握 Part 2–3 的 stack overflow 基礎，Corelan 系列術語與本課一致。

下一章把「讀」這件事玩到極致：用 `%x` 定位 offset、用 `%s` 洩漏任意記憶體、拿洩漏結果繞過 ASLR。

→ [Ch 35 任意讀：leak 記憶體 / 繞 ASLR](./35-format-string-arbitrary-read.md)
