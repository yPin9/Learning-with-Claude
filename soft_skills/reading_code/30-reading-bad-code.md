# Ch 30 — 讀爛 code / 義大利麵

> **目標**：建立一套防禦性讀法，用來對付現實裡佔多數的爛 code——god object、零抽象、全域變數滿天、深巢狀 if、複製貼上、命名騙人、零測試零註解。核心心法只有一句：**不信命名、不信註解，以行為為準**。本章給你一份「爛 code 求生 SOP」，並用一段真編真跑的壞味道 C 示範怎麼靠 characterization test 和 debugger 把說謊的 code 釘死。

## 為什麼這章存在

前面所有章節，或多或少假設了「code 是有意圖、可推理的」——你找 entry、追 data flow、猜 invariant，因為你相信作者當初想清楚了。爛 code 打破這個假設。爛 code 的作者當初**沒**想清楚，或者想清楚了但趕工，或者三個人接手改過留下互相矛盾的意圖。於是你面對的是：函式名叫 `calc` 什麼都算、註解寫「safe」其實是最危險的那個、全域變數在你看不到的地方被偷改。

先接受一個事實：**爛 code 是常態，不是例外。** 你職涯讀到的 code，好好寫的是少數。能讀漂亮的 code 只是基本盤；**能在爛 code 裡活下來、還能安全地動它，才是真本事**，也是資深與否的分水嶺。這章教的不是「怎麼把爛 code 變好」（那是重構，另一回事），而是「怎麼在不重寫的前提下，安全地理解並修改它」。

## 先給直覺：爛 code 為什麼特別難讀

好 code 幫你 chunk（Ch 3）：一個命名清楚的函式，你看一眼名字就打包成一個概念，不用讀本體。爛 code **反 chunk**：

- **命名騙人**：`calc()`、`process()`、`doStuff()`、`flag`、`tmp2`——名字不縮小意義，你被迫讀完全部本體才知道它幹嘛。chunk 失敗。
- **零抽象 / god function**：一個五百行的函式做二十件事，你的 working memory（Ch 3，容量只有 4–7 個 chunk）瞬間爆掉。
- **全域狀態**：函式的行為不只取決於參數，還取決於某個你不知道何時被改的全域變數。你**無法只看這個函式就理解它**——它的行為洩漏到別處。
- **深巢狀 if / 複製貼上**：控制流像義大利麵，你追一條路徑要在腦裡維護一疊 `if` 條件；複製貼上讓「改一處」變成「漏改五處」的地雷。

這些加起來的效果是：**你不能用讀好 code 的方式讀爛 code**。靜態閱讀（純看）在爛 code 上的錯誤率極高，因為你賴以推理的線索（名字、註解、結構）全都不可信。你需要換一套**以行為為準、可驗證**的讀法。

## 底層機制：防禦性讀法的四條原則

### 原則一：不信命名、不信註解，只信行為

名字和註解是**作者的宣稱**，不是**機器執行的事實**。編譯器不檢查註解，也不強制名字符合行為。所以在爛 code 裡它們的可信度趨近於零，甚至是**負的**——一個寫著 `// this is safe` 的函式，往往正是當年出事後有人心虛加上去的。

以行為為準的意思是：想知道這函式幹嘛，**跑它**（給輸入看輸出）、**測它**（寫 characterization test）、**斷它**（用 debugger 停下來看實際狀態），而不是讀名字和註解然後相信。

### 原則二：局部理解優先，不強求全局

爛 code 常常**沒有**乾淨的全局架構可理解——強求「先看懂整體」會讓你卡死。務實的做法是縮小戰場：只理解「你要改的那個函式 + 它直接碰到的狀態」，把其餘當黑箱。你不需要理解整坨義大利麵，你只需要理解你要下刀的那一段，以及它跟外界的**介面**（吃什麼全域、改什麼全域）。

### 原則三：動手前先用 characterization test 釘住現有行為

這是爛 code 求生最關鍵的一招，來自 Michael Feathers 的《Working Effectively with Legacy Code》。**characterization test（特徵測試）不是驗證「正確行為」，而是釘住「現有行為」**——包括 bug。你不知道這坨 code「應該」做什麼，但你能觀察它「現在」做什麼，把觀察寫成 assert。這樣一來：

- 你動它的時候，測試會在你**不小心改變現有行為**時炸給你看。
- 這給你一張安全網，讓你敢在看不懂全局的情況下局部修改。

### 原則四：用 debugger 確認實際流程，別靠腦補

深巢狀 if + 全域狀態的組合，靠肉眼在腦裡模擬執行的錯誤率極高。與其猜「這條路徑應該會走 else」，不如**斷點停下來看實際走哪、全域變數實際是多少**（Ch 18）。爛 code 是 debugger 價值最高的地方，因為靜態推理在這裡最不可靠。

## 真跑範例：一段說謊的 C，如何把它釘死

我在 `~/reading_code_lab/ch30/disc.c` 寫了一段刻意的壞味道 code（一個「定價引擎」），真編真跑。它集齊了幾種病：騙人的命名與註解、god function、深巢狀 if、以及最陰險的**全域狀態污染**。

```c
/* legacy pricing engine. do NOT touch. -- original author, 2013 */
int g;                       /* ??? */
char buf[256];
int flag;

/* calc: computes something. safe. */
int calc(int a, int b, int c, int t) {
    int r = a;
    if (t == 1) {
        if (b > 0) {
            if (c > 0) {
                if (a > 100) { r = a - a/10; flag = 1; }   /* <-- 偷改全域 */
                else { r = a; }
            } else r = a + b;
        } else r = a;
    } else if (t == 2) {
        r = a * b;
        if (r > 1000) r = r - r/20;
        g = r;
    } else {
        r = a;
    }
    if (flag) { r = r - 5; }   /* the mysterious fiver */
    return r;
}
```

爛味道盤點：`calc` 什麼都算（god function）、參數叫 `a b c t`（命名騙人，看不出意義）、註解說 `safe`（不可信）、四層巢狀 if、全域 `g`/`buf`/`flag` 且註解是 `???`。最陰險的是 `flag`：它在 `a > 100` 那個分支被設成 1，然後在函式尾巴 `if (flag) r = r - 5` 被讀——**跨呼叫污染**。

### 第一步：不讀懂就先跑它，觀察行為

`main` 呼叫四次（真跑輸出）：

```c
printf("%d\n", calc(120, 3, 2, 1));  /* premium single */
printf("%d\n", calc(50, 20, 0, 2));  /* bulk */
printf("%d\n", calc(120, 3, 2, 1));  /* premium single AGAIN, 同樣輸入 */
printf("%d\n", calc(10, 1, 1, 1));   /* cheap single */
```

```
$ gcc -O0 -o disc disc.c && ./disc
103
995
103
5
```

第一個異常出現了：第四次 `calc(10,1,1,1)`，一個 `t==1, a=10` 的便宜單，按 code 的 `else { r = a; }` 應該回 `10`，卻回了 `5`。**行為跟你讀 code 的直覺不符**——這正是爛 code 的典型陷阱。（注意第一次和第三次同樣輸入 `calc(120,3,2,1)` 都回 103，看起來「穩定」，更容易讓人放鬆警覺。）

### 第二步：寫 characterization test 釘住「現在的行為」

在你搞懂為什麼之前，先把**現狀**釘死。注意：我們**不**assert「正確答案」（我們還不知道正確是什麼），我們 assert「它現在吐什麼」：

```c
#include <assert.h>
extern int calc(int,int,int,int);
extern int flag;
int main(void) {
    /* Characterization test: 釘住 CURRENT 行為，連 bug 一起釘。
       不是斷言「對」，是斷言「它現在做什麼」。 */
    flag = 0;
    assert(calc(120,3,2,1) == 103);   /* premium single */
    assert(calc(50,20,0,2)  == 995);  /* bulk */
    assert(calc(120,3,2,1)  == 103);  /* 同輸入 */
    assert(calc(10,1,1,1)   == 5);    /* NOT 10 -- bug 被釘住了 */
    printf("all characterization assertions hold\n");
    return 0;
}
```

真跑（把 `disc.c` 去掉自己的 `main` 編成 lib 再連結）：

```
$ gcc -O0 -c disc_lib.c -o disc_lib.o
$ gcc -O0 char_test.c disc_lib.o -o char_test && ./char_test
all characterization assertions hold
```

現在你有一張安全網。**在你理解為什麼 `calc(10,1,1,1)==5` 之前**，這張網就已經保護你了：等你之後動 `calc`，只要不小心改到這些既有行為，assert 立刻炸。這是原則三的實戰——你先釘行為，才動 code。

### 第三步：用 debugger 抓出說謊的真相

肉眼盯著那四層 if 你可能腦補出十種錯誤解釋。別腦補，斷點停下來看 `flag` 在每次進入 `calc` 時到底是多少（真跑輸出）：

```
$ gcc -O0 -g -o disc_g disc.c
$ gdb -q -batch \
    -ex "break calc" -ex "run" \
    -ex "printf \"entry #1 flag=%d\n\", flag" -ex "continue" \
    -ex "printf \"entry #2 flag=%d\n\", flag" -ex "continue" \
    -ex "printf \"entry #3 flag=%d\n\", flag" -ex "continue" \
    -ex "printf \"entry #4 flag=%d\n\", flag" ./disc_g

Breakpoint 1, calc (a=120, b=3, c=2, t=1) at disc.c:11
entry #1 flag=0
Breakpoint 1, calc (a=50, b=20, c=0, t=2) at disc.c:11
entry #2 flag=1
Breakpoint 1, calc (a=120, b=3, c=2, t=1) at disc.c:11
entry #3 flag=1
Breakpoint 1, calc (a=10, b=1, c=1, t=1) at disc.c:11
entry #4 flag=1
```

真相大白：`flag` 在第一次呼叫（`a=120>100`）被設成 1，然後**再也沒被清掉**，污染了後面每一次呼叫。第四次那個便宜單，`10` 被尾巴的 `if (flag) r = r - 5` 扣成了 `5`。debugger 一次就把「命名/註解都在騙、實際靠隱藏全域狀態」這件事攤在陽光下——這是原則一（信行為不信註解）和原則四（用 debugger 確認）的合體。

那句 `/* the mysterious fiver */` 註解只說了「有這麼一扣」，沒說「它取決於別次呼叫留下的髒狀態」。你要是信了註解、沒斷 debugger，永遠抓不到這個跨呼叫 bug。

### 第四步：現在，而且只在現在，動它

你有了安全網（characterization test）和真相（debugger 確認的污染路徑），才可以動手。最小修正是進 `calc` 時把 `flag` 歸零，或把 `flag` 從全域改成區域變數。改完**再跑一次 characterization test**——這時你會**期待某些 assert 炸掉**（因為你正在改行為），你要人工判斷每個炸掉的 assert 是「我要的修正」還是「我不小心弄壞的東西」。這就是安全網的用法：它把「無聲的行為改變」變成「大聲的 assert 失敗」，逼你逐一 review。

## 第二種病：複製貼上的沉默分歧

爛 code 另一個高發病是**複製貼上後其中一份悄悄改了**。三段看起來一模一樣的 code，你讀了第一段就以為懂了全部——正中陷阱。看這段真編真跑的例子（`~/reading_code_lab/ch30/cp.c`）：

```c
int check_user(const char *name, int age) {
    if (strlen(name) == 0) return 0;
    if (age < 0 || age > 150) return 0;
    return 1;
}
int check_admin(const char *name, int age) {   /* 跟 check_user 逐字相同 */
    if (strlen(name) == 0) return 0;
    if (age < 0 || age > 150) return 0;
    return 1;
}
int check_guest(const char *name, int age) {
    if (strlen(name) == 0) return 0;
    if (age < 0 || age > 15) return 0;          /* 分歧：15 不是 150 */
    return 1;
}
```

真跑（`Bob`, 40 歲，三個角色都驗一次）：

```
$ gcc -O0 -o cp cp.c && ./cp
user(Bob,40)=1
admin(Bob,40)=1
guest(Bob,40)=0
```

`check_guest` 對同一個 40 歲的 Bob 回 `0`（驗證失敗）——因為那份複製的上限是 `15` 不是 `150`。這是複製貼上病的典型：**三份 code 的「意圖」看起來一致（都是驗證），但其中一份的「行為」偷偷分歧了**。而你根本不知道那個 `15` 是打錯字、還是「guest 真的只准 15 歲以下」的刻意設計——**光讀 code 分不出「bug」和「刻意的怪」**。

這帶出爛 code 的一個核心防禦動作：**看到複製貼上，逐字 diff 而不是掃過去**。

```
$ diff <(sed -n '/int check_user/,/^}/p' cp.c) <(sed -n '/int check_guest/,/^}/p' cp.c)
1c1
< int check_user(const char *name, int age) {
---
> int check_guest(const char *name, int age) {
3c3
<     if (age < 0 || age > 150) return 0;
---
>     if (age < 0 || age > 15) return 0;   /* diverged: 15 not 150 (typo? or intent?) */
```

`diff` 一秒鐘揪出那個 `150` vs `15` 的分歧。**在爛 code 裡「看起來一樣」是最危險的四個字**——你的大腦會自動 chunk 掉「這段跟上面一樣」然後跳過，而分歧就藏在你跳過的那一行。對付它的唯一辦法是不信「看起來」，用工具逐字比。（要不要「修」那個 `15`？先去 `git blame` 那行、找當初的 commit 訊息或問人，確認是 bug 還是刻意——見進階的考古。）

## 對比與取捨

| 面對爛 code 的策略 | 適用時機 | 代價 / 風險 |
|---|---|---|
| **靜態純讀**（信名字/註解） | 幾乎不適用於爛 code | 錯誤率極高，名字註解都在騙 |
| **characterization test 釘行為** | 你要動它、但看不懂全局 | 要能把目標函式獨立編譯/呼叫（可能要拆相依） |
| **debugger 確認實際流程** | 深巢狀 + 全域狀態、行為反直覺 | 要能跑起來、能觸發那條路徑 |
| **重構成好 code 再讀** | 你有時間、有測試網、且長期維護它 | 高風險：沒測試網就重構等於盲改，可能引入新 bug |
| **整段重寫** | 小、獨立、且你完全理解需求 | 對大的/理解不全的 god object 是災難，常見的自負陷阱 |

**實戰選擇**：預設走「characterization test + debugger」路線——先釘行為、確認真相、局部最小修改。**不要**未經測試網就大重構，也**不要**對看不懂的 god object 整段重寫（你會用新 bug 換掉舊 bug，而且失去對照）。「這 code 太爛我重寫比較快」是資深工程師最常低估風險的一句話。

## 踩雷集錦

1. **錯誤直覺：函式名/註解說什麼就是什麼。** → 正確認識：在爛 code 裡名字與註解可信度趨近零甚至為負（`// safe` 常是出事後心虛加的）。以**行為**為準：跑它、測它、斷它。本章那個 `calc` 註解寫 `safe`，實際藏著跨呼叫全域污染 bug。
2. **錯誤直覺：「我一定要先看懂整體才能動」。** → 正確認識：爛 code 常常沒有可理解的整體。局部理解優先——搞懂你要改的那段 + 它碰的狀態就夠下刀，其餘當黑箱。強求全局會卡死。
3. **錯誤直覺：靠肉眼在腦裡模擬執行深巢狀 if。** → 正確認識：巢狀 if + 全域狀態的組合，腦補錯誤率極高。斷點停下來看**實際**走哪、全域**實際**是多少。debugger 在爛 code 上價值最高，正因靜態推理在這裡最不可靠。
4. **錯誤直覺：沒測試網就直接重構/重寫。** → 正確認識：沒有 characterization test 就改爛 code，等於盲改——你根本不知道自己有沒有弄壞既有行為。永遠**先釘後改**。「重寫比較快」是低估風險的自負。
5. **錯誤直覺：全域變數的影響看得到。** → 正確認識：全域狀態讓函式行為洩漏到函式外，`grep` 那個全域名可能出現在幾十個你沒讀的地方（本章 `flag` 就是隔次呼叫才發作）。看到全域讀寫，要用 cscope 反查所有寫入點（Ch 14），別假設「這裡沒改就是沒改」。
6. **錯誤直覺：三段「看起來一樣」的 code 讀一段就懂全部。** → 正確認識：「看起來一樣」是複製貼上病最危險的偽裝，你的大腦會 chunk 掉重複段直接跳過，而分歧就藏在你跳過的那一行（本章 `check_guest` 的 `15` vs `150`）。看到複製貼上，用 `diff` 逐字比，別信「看起來」。而且分歧到底是 bug 還是刻意，`git blame` 那行確認來歷再說。

## 進階：再往深一層

- **Seam（接縫）與依賴打破**：Feathers 的核心概念。爛 code 難測，常因為它跟外界（全域、單例、硬連結的相依）纏死，無法獨立呼叫。「seam」是你能在不改行為下插入替身的接縫點——找到 seam 才能把 god function 拉出來獨立做 characterization test。這是把「不可測的爛 code」變「可測」的關鍵技巧。
- **黃金主檔測試（golden master）**：當函式輸出複雜到手寫 assert 不切實際（吐一大坨 JSON/文字），改用 golden master：跑一次把輸出存成「黃金檔」，之後每次改動 diff 對照。這是 characterization test 的重量級版本，對付大 god object 的輸出很有效。
- **命名騙人的系統性偵測**：可以用「名字宣稱 vs 行為事實」的落差當 code smell 雷達。函式叫 `get*` 卻有副作用（偷改狀態）、叫 `is*`/`has*` 卻回傳非 bool、參數叫 `flag` 卻是 enum——這些落差本身就是 bug 的高發區，值得優先 debugger 確認。
- **爛 code 的考古價值**：`git blame`/`git log`（Ch 17）在爛 code 上特別值錢。那句 `/* do NOT touch */` 背後往往有個 commit 訊息寫著「fix crash, don't ask」——歷史會告訴你這坨義大利麵是怎麼一層層長出來的，哪些是原始意圖、哪些是後來的補丁疤痕。理解疤痕的來歷，能避免你把「刻意的醜」（繞過某個平台 bug 的 workaround）當成「無意的爛」而誤刪。

## 動手練習

1. **重現本章實驗**：在 `~/reading_code_lab/ch30/` 重建 `disc.c`，`gcc -O0 -o disc disc.c && ./disc`，確認你也得到 `103 995 103 5`。解釋為什麼第四行是 `5` 不是 `10`。
2. **寫你自己的 characterization test**：不看本章的 test，自己給 `calc` 餵五組不同輸入、記下輸出、寫成 assert。故意包含一組「先呼叫 premium 再呼叫 cheap」來捕捉全域污染。跑通它。
3. **用 debugger 抓另一個全域**：`t==2` 分支會寫全域 `g`。用 gdb 在 `calc` 設斷點，`watch g`，觀察 `g` 何時被改、改成多少。體會「watchpoint 抓全域寫入」。
4. **最小修正 + 驗證**：把 `flag` 從全域改成 `calc` 的區域變數（每次進來歸零）。重跑步驟 2 的 characterization test，判斷哪些 assert 炸了、每個是「我要的修正」還是「意外弄壞」。
5. **在真專案找命名騙人**：到 `~/reading_code_lab/redis`，`rg -n "void .*Command\(client" src/t_string.c`，挑一個看似單純的 command，讀它有沒有偷改 `server.dirty` 這種全域狀態。體會「連好 code 也有必要的全域副作用，關鍵是它有沒有誠實命名/註解」。

## 本章重點整理

- 爛 code 是常態不是例外；能安全地讀並修改爛 code，才是資深的分水嶺。
- 最高心法：**不信命名、不信註解，以行為為準**。名字/註解是宣稱，行為是事實；在爛 code 裡前者可信度趨近零甚至為負。
- 求生 SOP：跑它觀察行為 → 寫 characterization test 釘住現狀（連 bug 一起釘）→ 用 debugger 確認真實流程與全域狀態 → 有了安全網和真相才動手，且局部最小修改。
- characterization test 釘的是「現有行為」不是「正確行為」，它讓「無聲的行為改變」變成「大聲的 assert 失敗」。
- 全域狀態讓函式行為洩漏到函式外、跨呼叫污染；看到全域讀寫要 cscope 反查所有寫入點，別假設沒讀到就沒改。
- 別在沒有測試網時重構/重寫爛 code——那是用新 bug 換舊 bug 的自負陷阱。

## 自我檢核

- [ ] 有人問「這函式註解寫 safe，那它安全嗎」，你能不能講出為什麼在爛 code 裡不能信註解、該怎麼驗證？
- [ ] 你能區分 characterization test 和一般單元測試的差別（釘現狀 vs 驗正確）嗎？
- [ ] 面對一個五百行 god function 你要改其中一段，你的前三步是什麼？
- [ ] 為什麼說 debugger 在爛 code 上價值最高？你能舉出全域狀態污染這種靜態讀不出來的例子嗎？
- [ ] 有人說「這 code 太爛我重寫比較快」，你能講出兩個這句話低估的風險嗎？

## 延伸閱讀

- **《Working Effectively with Legacy Code》— Michael Feathers（Prentice Hall, 2004）。**
  - **讀哪裡**：Ch 6–13，尤其是 characterization test、seam、依賴打破那幾章。
  - **學到什麼**：本章四條原則的權威來源。Feathers 定義「legacy code = 沒有測試的 code」，整本書就是「怎麼在沒測試的爛 code 上安全加測試再動它」。讀碼者的聖經。
  - **關聯**：直接支撐原則三與進階的 seam。

- **[Michael Feathers, "Characterization Testing" — 概念說明文](https://understandlegacycode.com/blog/characterization-tests-node-example/)（understandlegacycode.com）。**
  - **讀哪裡**：整篇不長，看它如何在一個真實函式上，用「跑一次看輸出 → 把輸出寫成 assert」建立特徵測試。
  - **學到什麼**：characterization test 的實作手感，包括 golden master 的變體。補足本章 C 範例之外、在動態語言裡的做法。
  - **關聯**：把本章第二步的方法論落地到另一個語言生態。

- **《A Philosophy of Software Design》— John Ousterhout（第 2 版，2021），"Comments" 與 "Naming" 兩章。**
  - **讀哪裡**：講註解與命名如何洩漏（或掩蓋）意圖的那幾章。
  - **學到什麼**：反過來讀——理解「好命名/好註解該長怎樣」，你就能一眼認出「這名字/註解在騙人」的落差，把它當 code smell 雷達。
  - **關聯**：支撐踩雷 1 與進階的「命名騙人系統性偵測」。

搞定了爛 code 的求生，接下來要放大尺度：不是一個 god function 爛，而是**整個專案有百萬行**。Linux、Chromium、LLVM 級的巨獸，你連 grep 都會被結果淹死。下一章講分而治之。

→ [Ch 31 大型專案的分而治之](./31-divide-and-conquer-large-codebases.md)
