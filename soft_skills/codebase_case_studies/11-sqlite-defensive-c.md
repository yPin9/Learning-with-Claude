# Ch 11 — 讀 SQLite 的防禦式 C

> **目標**：讀懂 SQLite 的**風格**——`assert`／`testcase()`／`ALWAYS()`／`NEVER()`／`SQLITE_CORRUPT_BKPT` 這些防禦式巨集，以及它舉世聞名的 100% MC/DC 測試文化如何反映在 code 的每一行。這不是讀某條路徑，而是學一種**可遷移的閱讀技能**：如何讀「高品質防禦式 C」的慣例，看穿哪些巨集是「編譯後消失的意圖標註」、哪些是「真的防線」，並反過來理解為什麼 SQLite 敢自稱地表最可靠的 C 之一。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）

## 為什麼需要這個？

前三章我們追路徑（text → bytecode → disk）。但你讀 `vdbe.c`／`btree.c` 時，一定被一種東西反覆打斷：每個 function 開頭一堆 `assert(...)`，程式中間冒出 `testcase(...)`，判斷式裡包著 `ALWAYS(...)`／`NEVER(...)`。第一次讀你會以為這些是雜訊、想跳過。

**這是讀 SQLite 最大的認知陷阱。** 這些巨集不是雜訊，是 SQLite 品質文化的**可見表層**。看數字——本課在真 clone 上數：

```bash
$ rg -c "assert\("   src/*.c | awk -F: '{s+=$2} END{print s}'
6318
$ rg -c "testcase\(" src/*.c | awk -F: '{s+=$2} END{print s}'
881
$ rg -c "ALWAYS\("   src/*.c | awk -F: '{s+=$2} END{print s}'
185
$ rg -c "NEVER\("    src/*.c | awk -F: '{s+=$2} END{print s}'
133
```

**6318 個 `assert`、881 個 `testcase`**——平均每 20 幾行 C 就有一個 assert。這不是強迫症，是刻意的工程策略。學會**讀**這些巨集，你才讀得懂 SQLite；而且這套「讀防禦式 C 的慣例」可以直接搬去讀任何高品質 C 專案（Linux kernel、OpenSSL、libpng 都有各自的版本）。這一章教的是**閱讀技能**，不是 SQLite 冷知識。

## 先建立直覺

先建立一個分類心智模型——這些巨集在「編譯後是否消失」和「是不是真防線」兩軸上分佈不同：

```
                        release build 會消失？
                     是                      否
                ┌──────────────────┬──────────────────┐
   是文件/意圖  │ assert(X)         │ ALWAYS(X)/NEVER(X)│
   標註（給人讀）│ testcase(X)       │ (在 release 退化為│
                │ → 編譯後蒸發      │    (X)，仍求值)   │
                ├──────────────────┼──────────────────┤
   是真的防線   │ (無——真防線不能  │ if(...)return     │
   （擋壞資料）  │  在 release 消失) │ SQLITE_CORRUPT_BKPT│
                │                   │ → 永遠在          │
                └──────────────────┴──────────────────┘
```

**關鍵洞見**：`assert`／`testcase` 是**寫給開發者和測試工具看的意圖標註**，release build 完全蒸發——它們不保護正式環境的使用者，保護的是 SQLite 開發者「這裡我假設 X 為真」的思路和測試覆蓋。而處理**壞掉的資料庫檔**（可能被惡意構造）的防線，是永遠存在的 `if(...) return SQLITE_CORRUPT_BKPT`。讀 code 時分清這兩者，你才知道哪句能當「作者告訴你的不變式」信、哪句是真的執行期檢查。

## 核心一：`assert` —— 作者寫給你的不變式

SQLite 用 `assert` 的密度遠超一般專案。看 Ch 9 讀過的 `OP_ResultRow` 開頭：

```c
case OP_ResultRow: {
  assert( p->nResColumn==pOp->p2 );
  assert( pOp->p1>0 || CORRUPT_DB );
  assert( pOp->p1+pOp->p2<=(p->nMem+1 - p->nCursor)+1 );
```
（`src/vdbe.c:1712-1715`，v3.47.2）

這三行 `assert` 對**讀者**的價值，比對執行期的價值還大——它們是**作者留給你的不變式清單**：「跑到這裡時，結果欄數一定等於 `pOp->p2`」「`p1` 一定 > 0（除非 db 壞了）」「暫存器範圍一定不越界」。你讀 `OP_ResultRow` 時不用自己推導這些前提，作者用 `assert` 直接告訴你了。

**這是讀 SQLite 的一個技巧**：把 function 開頭那排 `assert` 當成「這個 function 的前置條件文件」來讀。它比註解可靠——因為註解會腐爛（改了 code 忘了改註解），但 `assert` 在 debug build 會被測試跑到、假的會爆，所以它們**保證是最新的真話**。`reading_code` Ch 10（假設驅動讀碼）教你「邊讀邊記假設再驗證」，SQLite 的 assert 幫你把一半假設寫好了。

**`assert` 在 release build 消失**（`-DNDEBUG`）。所以：`assert` 裡**絕不能**放有副作用的運算式，否則 release 版行為會變。SQLite 嚴守這條——你不會看到 `assert( x = foo() )` 這種東西。讀到 `assert` 你可以放心它是純檢查。

## 核心二：`ALWAYS()` / `NEVER()` —— 「應該不可能，但萬一呢」

`assert` 是「這裡必為真，不然就是 bug」。但有些條件，SQLite 認為「照理說不可能發生，可是萬一真發生了，我不想崩、想優雅處理」。這就是 `ALWAYS`／`NEVER` 的地盤。看定義和它自帶的設計說明：

```c
/*
** The ALWAYS and NEVER macros surround boolean expressions which
** are intended to always be true or false, respectively.  Such
** expressions could be omitted from the code completely.  But they
** are included in a few cases in order to enhance the resilience
** of SQLite to unexpected behavior - to make the code "self-healing"
** or "ductile" rather than being "brittle" and crashing at the first
** hint of unplanned behavior.
*/
#if defined(SQLITE_OMIT_AUXILIARY_SAFETY_CHECKS)
# define ALWAYS(X)      (1)
# define NEVER(X)       (0)
#elif !defined(NDEBUG)
# define ALWAYS(X)      ((X)?1:(assert(0),0))
# define NEVER(X)       ((X)?(assert(0),1):0)
#else
# define ALWAYS(X)      (X)
# define NEVER(X)       (X)
#endif
```
（`src/sqliteInt.h:526-543`，v3.47.2）

讀懂這三個分支，你就懂 `ALWAYS`／`NEVER` 的精髓——**它在不同 build 下有三種人格**：

- **debug build**（`!NDEBUG`）：`ALWAYS(X)` = `(X)?1:(assert(0),0)`。也就是「求值 X；如果 X 竟然是假，`assert(0)` 立刻爆給開發者看」。這時它是嚴格檢查。
- **release build**（`NDEBUG`）：`ALWAYS(X)` = `(X)`。assert 蒸發了，但 **X 仍然被求值**——所以包著 `ALWAYS(X)` 的那個 `if` 在 release 仍會照 X 的真假走。這就是「self-healing/ductile」：release 版遇到「不該發生」的情況，不崩、按 X 實際值走安全路徑。
- **coverage test build**（`SQLITE_OMIT_AUXILIARY_SAFETY_CHECKS`）：`ALWAYS(X)` 硬編成 `1`。**為什麼？** 因為 `ALWAYS(X)` 標記的分支「照理永遠成立」，那條 else 是不可達的防禦碼。測覆蓋率時，若不把它固定成 1，那條永遠跑不到的 else 會被算成「未覆蓋」，拉低 SQLite 引以為傲的 100% 覆蓋率。硬編成 1，讓覆蓋率工具**忽略**這條刻意的不可達防禦碼。

> **失敗是教材**：第一次看到 `if( NEVER(pX==0) ) return ...`，你會困惑「`NEVER` 是說它永遠不會 0，那這個 if 不就永遠不執行？寫它幹嘛？」。答案：作者在說「我**認為**它不可能是 0（debug 版會 assert 幫我驗證這個信念），但**萬一**在某個我沒想到的 release 情境下真的是 0，我寧可讓程式優雅返回，也不要 null deref 崩掉」。`ALWAYS`／`NEVER` 是**「信念 + 保險」的合體**——用 debug assert 驗證信念，用 release 求值買保險。

**讀碼判讀**：看到 `ALWAYS(X)`／`NEVER(X)`，讀作「作者強烈相信 X（會被 debug 驗證），但為防禦保留了 X 為假的分支」。這句本身就是很強的語意——它告訴你「這條 else 是罕見的防禦路徑，不是正常邏輯」，你可以先當雜訊略過主線閱讀，需要時再回來。

## 核心三：`testcase()` —— 逼出 100% MC/DC 覆蓋的工具

SQLite 宣稱它的測試達到 **100% MC/DC（Modified Condition/Decision Coverage）**——一種比「行覆蓋」嚴格得多的標準：不只每行要跑到，每個布林**子條件**都要獨立地被驗證能影響結果。`testcase()` 就是為這個目標存在的：

```c
#if defined(SQLITE_COVERAGE_TEST) || defined(SQLITE_DEBUG)
# ifndef SQLITE_AMALGAMATION
    extern unsigned int sqlite3CoverageCounter;
# endif
# define testcase(X)  if( X ){ sqlite3CoverageCounter += (unsigned)__LINE__; }
#else
# define testcase(X)
#endif
```
（`src/sqliteInt.h:477-484`，v3.47.2）

release build 裡 `testcase(X)` 是**空的**（完全蒸發）。在 coverage build 裡，它變成「如果 X 成立，摸一下全域計數器」。它本身**什麼邏輯都不做**——它的唯一目的是在覆蓋率報告裡留一個「這個邊界情況有沒有被測到」的印記。檔案裡的說明講得很直白：

```c
** ... testcase() can be used to make sure boundary values are tested.
** For bitmask tests, testcase() can be used to make sure each bit
** is significant and used at least once.  On switch statements
** where multiple cases go to the same block of code, testcase()
** can insure that all cases are evaluated.
```
（`src/sqliteInt.h:470-475`，v3.47.2）

**讀碼判讀**：看到 `testcase(X)`，讀作「作者在提醒：X 這個邊界/位元/分支是需要被測試涵蓋的重要情況」。它對執行邏輯零影響，讀主線時直接跳過——但它反過來是**一張地圖**：`rg 'testcase' src/vdbe.c` 能告訴你 SQLite 認為哪些邊界值得警惕（整數溢位邊界、UTF 編碼邊界、bitmask 每一位…）。想理解一個 function 有哪些容易出錯的邊界，讀它的 `testcase` 比讀註解快。

## 核心四：真正的防線 —— `SQLITE_CORRUPT_BKPT`

前面三個都是 debug/測試設施，release 會消失或退化。那**真正保護使用者**的防禦碼長什麼樣？看 SQLite 處理「資料庫檔可能被損壞或惡意構造」時用的：

```c
#define SQLITE_CORRUPT_BKPT sqlite3CorruptError(__LINE__)
```
（`src/sqliteInt.h:4600`，v3.47.2）

`SQLITE_CORRUPT_BKPT` 在**任何 build 都存在**。它展開成 `sqlite3CorruptError(__LINE__)`——回傳 `SQLITE_CORRUPT` 錯誤碼，並（在 debug 下）記下是哪一行偵測到損壞（`__LINE__`）方便下中斷點。B-tree 讀 page 時，任何「這頁的內容不符合格式」都會 `return SQLITE_CORRUPT_BKPT` 而不是往下 deref 壞指標。

這帶出 SQLite 一個**極漂亮的複合慣例**——`assert( X || CORRUPT_DB )`：

```c
  assert( nSize==debuginfo.nSize || CORRUPT_DB );
```
（`src/btree.c:1442`，v3.47.2；此模式在 btree.c 出現數十次）

拆開讀：`CORRUPT_DB` 定義是 `(sqlite3Config.neverCorrupt==0)`（`src/sqliteInt.h:4344`）——測試時可以設 `neverCorrupt=1` 宣告「我保證這次測的 db 沒壞」。所以 `assert( X || CORRUPT_DB )` 的意思是：

> 「`X` 應該為真——**除非**資料庫檔是壞的。如果我們正在測一個保證沒壞的 db（`neverCorrupt=1`，`CORRUPT_DB` 為假），那 `X` 必須為真，否則 assert 爆（這是真 bug）。但如果 db 可能是壞的，`X` 為假是合理的（壞資料造成），別 assert。」

這一句就把「內部不變式」和「外部可能壞的輸入」漂亮地分開了：**對可信的內部狀態，X 必為真；對不可信的外部檔案，X 為假是要防禦處理的正常情況，不是 bug。** 這是防禦式 C 的高手手筆——一行 assert 同時服務「抓自己的 bug」和「不誤判外部損壞為 bug」兩個目的。

## 核心五：`VVA_ONLY` / `TESTONLY` 與 `EVIDENCE-OF`——兩個容易誤讀的慣例

讀 SQLite 還會撞到兩個小慣例，不認得會誤判 code。

**`VVA_ONLY(X)` / `TESTONLY(X)`——只在 debug 存在的輔助碼**。有時一個 `assert` 需要先算個中間值才能檢查，但那個中間值在 release 是浪費（release 沒 assert）。SQLite 把這種「只為了 assert 服務的 setup 碼」包進 `VVA_ONLY`：

```c
#ifndef NDEBUG
# define VVA_ONLY(X)  X
#else
# define VVA_ONLY(X)
#endif
```
（`src/sqliteInt.h:506-510`，v3.47.2；`VVA` = Verification, Validation, and Accreditation）

`VVA_ONLY(int nField = pCur->nField;)` 這行變數宣告在 release build **整個消失**（連變數都不配置）。`TESTONLY`（sqliteInt.h:492）同理，範圍略廣（涵蓋 `testcase` 用的 setup）。**讀碼判讀**：看到 `VVA_ONLY(...)` 包著的 code，讀作「這只在 debug 存在、純為驗證服務，不是正式邏輯」——release 版根本沒有它。第一次讀你可能困惑「這個變數怎麼只在後面 assert 用到」，答案就是它是 VVA_ONLY 的驗證輔助。

**`EVIDENCE-OF: R-xxxxx` 註解——把文件和 code 綁在一起**。SQLite 有份逐句編號的官方需求文件，每句需求有個 `R-nnnnn-nnnnn` 編號。code 裡凡是實作某條需求的地方，就留一行 `/* EVIDENCE-OF: R-... 需求原文 */`。真 clone 上數：

```bash
$ rg -c "EVIDENCE-OF" src/*.c | awk -F: '{s+=$2} END{print s}'
104
$ rg -n "EVIDENCE-OF" src/vdbeapi.c | head -1
242:/* EVIDENCE-OF: R-12793-43283 Every value in SQLite has one of five ...
```

這是「可追溯性」的極致——每條需求都能反查「在 code 哪裡實作、有沒有測」，每段 code 都能正查「它實作哪條需求」。**讀碼判讀**：`EVIDENCE-OF` 註解對你是免費的白話文件——它把該段 code 對應的官方需求原文直接貼在旁邊，比你去猜「這段在幹嘛」快得多。看到它就當「作者附贈的權威註解」讀。

## 底層機制：這些慣例怎麼撐起「地表最可靠」的宣稱

把散落的點連起來，SQLite 的可靠性不是玄學，是一套互相咬合的機制：

```
   開發時                測試時                  release 時
   ┌────────────┐       ┌──────────────────┐   ┌──────────────┐
   │ 6318 assert│ ──►   │ 每個 assert 都被  │   │ assert/testcase│
   │ 標註不變式 │       │ 測試套件跑到驗證  │   │ 蒸發，零開銷  │
   ├────────────┤       ├──────────────────┤   ├──────────────┤
   │ 881 testcase│ ──►  │ 逼出 100% MC/DC   │   │ ALWAYS/NEVER │
   │ 標註邊界   │       │ 每個布林子條件驗證│   │ 退化為求值仍防│
   ├────────────┤       ├──────────────────┤   ├──────────────┤
   │ ALWAYS/NEVER│ ──► │ 硬編 1/0 使不可達 │   │ CORRUPT_BKPT │
   │ 標註防禦碼 │       │ 防禦碼不拉低覆蓋率│   │ 永遠在，擋壞檔│
   └────────────┘       └──────────────────┘   └──────────────┘
        意圖                  驗證                    保護
```

- **開發時**：作者把每個假設寫成 `assert`、每個邊界寫成 `testcase`、每條防禦碼標成 `ALWAYS/NEVER`。code 本身就是一份可執行的規格文件。
- **測試時**：SQLite 有數萬個測試、跑出 100% MC/DC。`assert` 全被驗證、`testcase` 全被涵蓋、`ALWAYS/NEVER` 被硬編避免污染覆蓋率統計。任何假設破了、任何邊界沒測到，CI 就紅。
- **release 時**：debug 設施全蒸發（零效能開銷），只留真防線（`CORRUPT_BKPT` 等）。所以 SQLite 既極度防禦、又極度快——**因為防禦的成本在測試期付掉了，不在 runtime**。

這就是為什麼 SQLite 敢說自己是被驗證得最徹底的 C 程式之一。它不是「寫得小心」而已，是**把小心用巨集固化成 code、用測試強制執行**。讀懂這套，你讀任何一個 SQLite function 都多一層 X 光視角：這行 assert 告訴我作者的假設、這個 testcase 告訴我危險邊界、這個 CORRUPT_BKPT 告訴我這裡在防惡意輸入。

## 對比與取捨

| 巨集 | release 是否消失 | 是真防線嗎 | 讀碼時當它是 |
|---|---|---|---|
| `assert(X)` | 消失 | 否 | 作者告訴你的不變式/前置條件（可信的真話） |
| `testcase(X)` | 消失（release 空） | 否 | 危險邊界的地圖標記（主線可略過） |
| `ALWAYS(X)`/`NEVER(X)` | 退化為 `(X)` | 半是（release 仍求值防禦） | 「強信念 + 保險」，else 是罕見防禦路徑 |
| `SQLITE_CORRUPT_BKPT` | 永遠在 | 是 | 對壞檔/惡意輸入的真實防線 |
| `assert( X \|\| CORRUPT_DB )` | assert 消失、語意留 | 半是 | 「內部必真、外部可壞」的雙目的檢查 |

**取捨視角**：這套慣例的代價是 code 看起來「很吵」——一個 20 行的 function 可能有 8 行是 assert/testcase。新手覺得雜訊多。但這正是 SQLite 的取捨：**用閱讀時的視覺雜訊，換開發期的假設外化 + 測試期的強制驗證**。一旦你學會「把 assert 當文件讀、把 testcase 當雜訊跳」，那些「雜訊」反而變成加速你理解的路標。

## 踩雷集錦

1. **把 `assert`/`testcase` 當雜訊全部跳過**：`testcase` 可以跳（它對邏輯零影響），但 `assert` **不能跳**——它是作者免費送你的不變式清單，是理解這個 function 前置條件的最快途徑。跳過 assert 讀 SQLite，等於扔掉一半的路標。
2. **以為 `NEVER(X)` 的 if 分支是死碼可以刪**：不能刪。debug 版靠它 `assert(0)` 驗證信念；release 版靠它在「不該發生但發生了」時優雅返回而非崩潰。它是刻意的防禦保險，不是忘了刪的殘骸。
3. **以為 `assert` 在 release 也會保護使用者**：不會。`-DNDEBUG` 下 assert 完全蒸發。真正在 release 保護使用者的是 `SQLITE_CORRUPT_BKPT` 這類永遠存在的 `return 錯誤碼`。分不清這兩者，你會誤判 SQLite 對惡意輸入的實際防線在哪。
4. **在 `assert` 裡看到有副作用的運算式並依賴它**：SQLite 嚴禁 assert 帶副作用（否則 release 行為變）。如果你在某專案看到 `assert(x = f())`，那是那個專案的 bug；SQLite 不會這樣，讀到 SQLite 的 assert 可放心當純檢查。
5. **不懂 `CORRUPT_DB` 就誤讀 `assert( X || CORRUPT_DB )`**：這不是「X 或某個叫 CORRUPT_DB 的旗標」隨便一個成立就好。它是精心設計的雙目的：`CORRUPT_DB` 為假（測乾淨 db）時強制 X 為真抓 bug，`CORRUPT_DB` 為真（可能壞檔）時允許 X 為假走防禦。讀成「隨便一個真就過」會完全誤解它抓 bug 的能力。

## 進階：再往深一層

- **`reading_code` Ch 22（讀懂巨集）的實戰**：SQLite 這些巨集全是「同一個名字、不同 build 展開成不同東西」的多形巨集。讀它們的正確方法就是本章示範的——**先讀 `#if/#else` 每個分支各展開成什麼**，再回頭看使用點。不先看定義就讀使用點，你會完全誤解 `ALWAYS`/`testcase` 在幹嘛。
- **把這套技能遷移出去**：Linux kernel 有 `WARN_ON`/`BUG_ON`/`likely()`/`unlikely()`，OpenSSL 有它自己的 `OPENSSL_assert`，Chromium 有 `DCHECK`/`CHECK`（debug-only vs always）。都是同一組概念的變體：「debug-only 檢查 vs 永遠存在的防線」「意圖標註 vs 真防禦」。學會 SQLite 這套，你讀那些專案時能直接套用「這個巨集 release 會不會消失？是文件還是防線？」的判讀框架。
- **`sqlite3Config.neverCorrupt` 是怎麼被設的**：它由測試工具（如 fuzz check 的相反面）在「我保證餵乾淨 db」時設 1。追它的 setter 能看到 SQLite 測試基礎設施如何在「測正常路徑」和「測損壞路徑（fuzzing）」之間切換——這接 `advanced_fuzzing` 課的 SQLite 是被 OSS-Fuzz 打得最兇的目標之一的脈絡。

## 本章重點整理

- SQLite 的防禦式 C 慣例是它品質文化的可見表層，**不是雜訊**：真 clone 上數到 6318 個 `assert`、881 個 `testcase`、185 個 `ALWAYS`、133 個 `NEVER`。
- **`assert`**：作者留給你的不變式/前置條件清單，比註解可靠（會被測試驗證），release 蒸發、不保護使用者。讀 SQLite 一定要讀 assert。
- **`ALWAYS`/`NEVER`**：三種人格——debug 嚴格 assert、release 退化為求值仍防禦（self-healing）、coverage build 硬編 1/0 避免污染覆蓋率。讀作「強信念 + 保險」。
- **`testcase`**：逼出 100% MC/DC 的工具，對邏輯零影響，是「危險邊界地圖」；主線閱讀可略過，找邊界時反查它。
- **`SQLITE_CORRUPT_BKPT`**：永遠存在的真防線，擋壞檔/惡意輸入。`assert( X || CORRUPT_DB )` 是「內部必真、外部可壞」的雙目的傑作。
- 可靠性機制 = 開發時外化假設（巨集）→ 測試時 100% 覆蓋強制驗證 → release 蒸發設施只留防線。防禦成本付在測試期，所以 SQLite 既防禦又快。**這套判讀框架可遷移到任何高品質 C 專案。**

## 自我檢核

- [ ] 我能對每個巨集回答「release build 會不會消失？它是文件還是真防線？」
- [ ] 我能解釋 `ALWAYS(X)` 在 debug / release / coverage 三種 build 下各展開成什麼、為什麼
- [ ] 我能讀懂 `assert( X || CORRUPT_DB )` 的雙目的，並說出 `CORRUPT_DB` 為真/為假時各代表什麼
- [ ] 我知道讀 SQLite 時 `assert` 該讀、`testcase` 可略過、`CORRUPT_BKPT` 標示對惡意輸入的防線
- [ ] 我能把這套「debug-only 檢查 vs 永遠防線」的框架，說出至少一個其他專案的對應物（如 kernel 的 `BUG_ON` vs 錯誤返回）

## 延伸閱讀

- **[How SQLite Is Tested](https://www.sqlite.org/testing.html)**（官方）
  - **讀哪裡**：整頁，尤其「Anomaly testing」「MC/DC」「The TH3 test harness」幾節。這份文件解釋了本章那些巨集背後的測試哲學——你會懂為什麼 `testcase` 存在、100% MC/DC 是什麼、為什麼 SQLite 敢那樣宣稱可靠。讀完再回看 code，那些巨集全部「活」起來。
  - **前提**：讀完本章。
- **`src/sqliteInt.h` 中 `ALWAYS`/`NEVER`/`testcase`/`OK_IF_ALWAYS_TRUE` 幾段巨集定義前的檔案內註解**
  - **讀哪裡**：`sqliteInt.h` 第 460–550 行一帶，SQLite 自己解釋每個巨集為何存在。SQLite 的巨集註解品質極高，是「讀巨集先讀作者自述」的最佳範例。
  - **前提**：無。
- **`reading_code` Ch 22「讀懂巨集與 metaprogramming」**
  - **讀哪裡**：多形巨集的讀法（先讀 `#if/#else` 各分支、再看使用點）。本章對 `ALWAYS`/`testcase` 的拆解就是這套方法的實戰，回頭對照能把方法內化。
  - **前提**：無。

四章讀下來，SQLite 的路徑（text→disk）和風格（防禦式 C）都拿下了。下一章收網——把這四章讀到的可遷移 pattern 結晶成卡片：「編譯到 bytecode + VM 直譯」「pager/page-cache 抽象」「VFS 可插拔後端」「防禦式 C」「amalgamation build」。每張卡片講清楚 beacon（怎麼一眼認出）、在哪、可遷移到哪，存進你的 pattern 字典。

→ [Ch 12 萃取 pattern：VM 分派 / pager / amalgamation](./12-sqlite-patterns-extracted.md)
