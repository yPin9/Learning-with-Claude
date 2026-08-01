# Ch 22 — 讀懂巨集與 metaprogramming

> **目標**：學會對付 C 讀碼最大的障眼法——preprocessor。你在螢幕上看到的 `serverAssert(o)`、`SDS_HDR(8,s)`、`FMTARGS(...)` 常常不是編譯器真正看到的東西；巨集把 code「藏起來」了。本章的核心武器是 `gcc -E`（展開巨集）與 `gcc -dM -E`（列出實際定義了哪些巨集）——把藏起來的真相攤在你面前，再逐一拆解 function-like macro、token paste（`##`）、stringify（`#`）、變參計數、條件編譯迷宮、與 build 時 code generation。

> **環境**：WSL2 Ubuntu 22.04，沙包 `~/reading_code_lab/redis`（redis 7.4.0）。本章每個展開都是在 redis src 目錄下用真實 include 路徑跑 `gcc -E -P` / `gcc -dM -E` 後照抄。

## 為什麼巨集是讀碼的頭號障眼法

先建立一個殘酷認知：**在 C 裡，你讀的 source 和編譯器編的 code 是兩份不同的東西。** 中間隔了一層 preprocessor（前處理器），它在真正的編譯（詞法/語法分析，見 compiler_frontend Ch 1）之前，先做純文字的巨集展開、`#include` 貼上、`#ifdef` 刪除。

這造成三種讓靜態閱讀失效的狀況：

```
你看到的                         編譯器實際看到的
──────────────────────────────  ────────────────────────────────
serverAssert(o != NULL)     →   (__builtin_expect(!!(o!=..),1)?...)
SDS_HDR(8, s)               →   ((struct sdshdr8 *)((s)-sizeof...))
FMTARGS("a=%d ",1,"b",x)    →   "a=%d " "b", 1, x    ← 參數被重排！
#ifdef HAVE_EPOLL ...       →   （整段可能根本不存在）
```

- **文字被替換**：巨集名字底下藏著完全不同的一串 code，你用肉眼腦補會漏掉 `likely()`、`__FILE__` 之類的細節。
- **結構被生成**：`##`（token paste）能拼出型別名、函式名；變參巨集能數參數、重排參數——這些「憑空長出來的符號」，`grep` / `ctags` 常常索引不到，因為它們在展開前根本不以完整字串存在。
- **整段被刪除**：`#ifdef` 沒中的分支，對編譯器來說等於不存在。你讀了半天的某段 code，可能在這份 build 裡是死的。

對付這三種，只有一招是根本解：**別靠腦補，直接叫前處理器展開給你看。**

## 核心武器：`gcc -E` 與 `gcc -dM -E`

兩個指令貫穿本章，先講清楚差別：

| 指令 | 做什麼 | 何時用 |
|---|---|---|
| `gcc -E foo.c` | 跑完整前處理，印出**展開後的 source**（含 `#include` 全部貼進來） | 想看某段 code 展開成什麼真相 |
| `gcc -E -P foo.c` | 同上，但**省略 `# 12 "file"` 行號標記**，輸出乾淨可讀 | 實務上讀展開結果都加 `-P` |
| `gcc -dM -E foo.c` | 只印出「跑完前處理後，總共定義了哪些巨集」 | 想知道「這台機器/這份 build 下哪些 `#ifdef` 會中」 |

關鍵：**用真實的 `-I`/`-D` 旗標跑**，否則展開結果跟真正 build 不一樣。這正是 Ch 21 抓 `compile_commands.json` 的用處——把那份真實旗標套上來，展開才可信。

## 實戰一：展開 `serverAssert`——看清一個巨集藏了什麼

redis 到處在用 `serverAssert(x)`。它的定義（真實節錄 `server.h:665`）：

```c
#define serverAssert(_e) \
    (likely(_e)?(void)0 : (_serverAssert(#_e,__FILE__,__LINE__),redis_unreachable()))
```

光看定義已經有三個「藏起來的東西」：`likely()`（另一個巨集）、`#_e`（stringify 運算子）、`__FILE__`/`__LINE__`（預定義巨集）。到底展開成什麼？寫個一行測試檔真跑 `gcc -E -P`：

```
$ cd ~/reading_code_lab/redis/src
$ cat > /tmp/tassert.c <<'EOF'
#include "server.h"
void demo(robj *o) {
    serverAssert(o != NULL);
}
EOF
$ gcc -E -P -I. -I../deps/hiredis -I../deps/linenoise -I../deps/lua/src \
      -I../deps/hdr_histogram -I../deps/fpconv -DUSE_JEMALLOC \
      -I../deps/jemalloc/include /tmp/tassert.c | grep -A3 "void demo"
```

真實輸出：

```c
void demo(robj *o) {
    (__builtin_expect(!!(o != ((void *)0)), 1)?(void)0 : (_serverAssert("o != NULL","/tmp/tassert.c",3),__builtin_unreachable()));
}
```

一行展開，資訊爆炸。逐塊拆：

- **`likely(_e)` → `__builtin_expect(!!(...), 1)`**：分支預測提示，告訴編譯器「這條通常成立」。你不展開根本不知道 `likely` 是這個。
- **`#_e` → `"o != NULL"`**：`#` 是 **stringify**，把巨集參數的**原始文字**轉成字串字面量。這就是為什麼 assert 失敗時能印出「你 assert 的那個條件長什麼樣」——條件的文字被 `#` 抓下來當字串傳給 `_serverAssert`。
- **`__FILE__` → `"/tmp/tassert.c"`、`__LINE__` → `3`**：預定義巨集，展開成當前檔名與行號。assert 訊息裡的「檔案:行號」就是這麼來的。
- **`NULL` → `((void *)0)`**：`NULL` 自己也是巨集，一併展開。
- **`redis_unreachable()` → `__builtin_unreachable()`**：告訴編譯器「執行到這裡代表邏輯已崩，後面不可能到」，讓編譯器據此最佳化。

**讀碼啟示**：你在 `debug.c` 看到 `_serverAssert(estr, file, line)` 有三個參數卻不知道呼叫端怎麼填？因為呼叫端根本沒直接呼叫它——是 `serverAssert` 巨集用 `#`/`__FILE__`/`__LINE__` 幫你填的。**巨集是「呼叫端」與「真實函式」之間的隱形轉接層**，不展開你會以為 code 斷了。

## 實戰二：token paste（`##`）——憑空拼出型別名

redis 的 SDS（動態字串）用一個經典手法：同一個邏輯型別有 5 種 header（`sdshdr5/8/16/32/64`，長度欄位大小不同以省記憶體）。存取 header 的巨集用 `##` 把型別數字**拼**進型別名（真實節錄 `sds.h:60-61`）：

```c
#define SDS_HDR_VAR(T,s) struct sdshdr##T *sh = (void*)((s)-(sizeof(struct sdshdr##T)));
#define SDS_HDR(T,s)     ((struct sdshdr##T *)((s)-(sizeof(struct sdshdr##T))))
```

`##` 是 **token paste**：把左右兩個 token 黏成一個。`sdshdr##T`，當 `T=8` 時黏成 `sdshdr8`。展開 `SDS_HDR(8, s)` 看看（真跑）：

```
$ cat > /tmp/tsds.c <<'EOF'
#include "sds.h"
size_t f(sds s){ return SDS_HDR(8,s)->len; }
EOF
$ gcc -E -P -I. /tmp/tsds.c | grep "size_t f"
```

真實輸出：

```c
size_t f(sds s){ return ((struct sdshdr8 *)((s)-(sizeof(struct sdshdr8))))->len; }
```

`SDS_HDR(8,s)` 變成了 `(struct sdshdr8 *)...`——**`sdshdr8` 這個型別名是巨集當場拼出來的**。

這對讀碼者有個具體殺傷力：你在 `sds.c` 看到 `sdslen()` 裡的 `switch` 對每個型別呼叫 `SDS_HDR(8,s)`、`SDS_HDR(16,s)`...，如果你想 `grep "struct sdshdr8"` 找它被誰用，**會漏掉這些用法**——因為原始碼裡根本沒有 `sdshdr8` 這個連續字串，它是 `sdshdr` + `8` 在展開時才黏起來的。`##` 生成的符號對純文字工具（grep/ctags）是隱形的。**要找它們，得先展開，或用 clangd 這種真編譯的語意工具。**

## 實戰三：變參巨集計數 + 參數重排——redis 的 FMTARGS

這是 redis 裡最精巧的 metaprogramming，值得完整看一遍。問題背景：redis 常要組 INFO 這種「一堆 `key=value`」的長字串，`printf` 風格要求「所有 format 字串在前、所有 value 在後」，但人類想寫成「format, value, format, value...」成對。`FMTARGS` 巨集負責**把成對寫法自動拆成 format 群 + value 群**。

先看它的計數核心（真實節錄 `fmtargs.h`）：

```c
#define NARG(...)   NARG_I(__VA_ARGS__, RSEQ_N())
#define NARG_I(...) ARG_N(__VA_ARGS__)
#define VFUNC_N_(name, n) name##n          /* token paste: 把函式名接上數字 */
#define VFUNC_N(name, n)  VFUNC_N_(name, n)
#define VFUNC(func, ...)  VFUNC_N(func, NARG(__VA_ARGS__)) (__VA_ARGS__)
```

`ARG_N` 與 `RSEQ_N` 是那兩行超長的自動生成巨集（`generate-fmtargs.py` 產的，見 Ch 21 code generation）：

```c
#define ARG_N(_1,_2,...,_120, N, ...) N          /* 把第 121 個參數挑出來 */
#define RSEQ_N() 120,119,118,...,2,1,0           /* 一串遞減數列 */
```

**這個計數技巧的原理**：把使用者的參數塞在 `RSEQ_N()` 這串遞減數列前面，一起餵給 `ARG_N`。`ARG_N` 固定回傳第 121 個位置的東西。使用者參數越多，就把數列往後推得越多，第 121 位剛好落在「數列裡等於參數個數」的那個數字上。於是 `NARG(a,b,c)` 展開成 `3`。這是純 preprocessor 的「數數」——C 巨集沒有迴圈、沒有算術，全靠這種位置對齊的把戲。

有了計數，`VFUNC(COMPACT_FMT_, ...)` 就能用 `##` 接成 `COMPACT_FMT_4`（4 個參數時）這種「按參數個數分派」的巨集。最終 `FMTARGS` 把成對的 `(fmt,value)` 拆成兩群。展開一個真實例子：

```
$ cat > /tmp/tfmt.c <<'EOF'
#include "fmtargs.h"
X = FMTARGS("a=%d ", 1, "b=%s", "hi");
EOF
$ gcc -E -P -I. /tmp/tfmt.c | grep "^X ="
```

真實輸出：

```c
X = "a=%d " "b=%s", 1, "hi";
```

看清楚發生了什麼：輸入是**交錯**的 `"a=%d ", 1, "b=%s", "hi"`，輸出把兩個 format 字串拉到前面（`"a=%d " "b=%s"`，相鄰字串字面量自動相連）、兩個 value 拉到後面（`1, "hi"`）。**巨集在編譯前就把參數重排了。**

`server.c:5595` 的真實用法：

```c
info = sdscatfmt(info, "# Server\r\n" FMTARGS(
    "redis_version:%s\r\n", REDIS_VERSION,
    "redis_git_sha1:%s\r\n", redisGitSHA1(),
    ...));
```

**讀碼啟示**：你若不展開，看到 `sdscatfmt(info, "..." FMTARGS(...))` 會完全誤解參數怎麼傳——你以為 format 和 value 是交錯的，實際傳進 `sdscatfmt` 的是「format 全在前、value 全在後」。這種「巨集重排參數」的手法，**光讀 source 一定讀錯，唯一解是 `gcc -E`**。

## 實戰四：條件編譯迷宮——`gcc -dM -E` 找出哪條分支活著

`#ifdef` 平台/版本分支是讀碼的另一種暈眩來源。redis 的 `config.h` 是教科書級的迷宮（真實節錄）：

```c
#ifdef __linux__
#define HAVE_EPOLL 1
#endif

#if (defined(__APPLE__) && defined(MAC_OS_10_6_DETECTED)) || defined(__FreeBSD__) \
    || defined(__OpenBSD__) || defined (__NetBSD__)
#define HAVE_KQUEUE 1
#endif

#ifdef __sun
#include <sys/feature_tests.h>
#ifdef _DTRACE_VERSION
#define HAVE_EVPORT 1
#endif
#endif
```

三個 event backend 巨集（`HAVE_EPOLL`/`HAVE_KQUEUE`/`HAVE_EVPORT`）都寫在 header 裡，但**它們互斥、由平台決定哪個生效**。你在 Linux 上讀 redis，到底哪個活著？別用眼睛推巢狀 `#if`——用 `gcc -dM -E` 直接問：

```
$ cat > /tmp/tcfg.c <<'EOF'
#include "config.h"
EOF
$ gcc -dM -E -I. /tmp/tcfg.c | grep -E "HAVE_EPOLL|HAVE_KQUEUE|HAVE_EVPORT|HAVE_BACKTRACE"
```

真實輸出（在我的 WSL Linux 上）：

```
#define HAVE_BACKTRACE 1
#define HAVE_EPOLL 1
```

**只有 `HAVE_EPOLL` 被定義**，`HAVE_KQUEUE` / `HAVE_EVPORT` 雖然寫在 header 裡卻沒生效——因為它們的守衛是 `__APPLE__` / `__sun`，這台 Linux 不中。這和 Ch 21 的 `ae.c` 完美對上：`ae.c` 裡 `#ifdef HAVE_EPOLL` 選了 `#include "ae_epoll.c"`，現在你有硬證據「這份 build 走 epoll」。

**讀碼啟示**：面對成堆 `#ifdef`，**不要靠肉眼推巢狀邏輯**（很容易數錯 `#else` 配對）。`gcc -dM -E` 一次列出「所有實際定義的巨集」，你拿它去比對 code 裡的 `#ifdef`，就知道哪些分支是活的、哪些是別的平台的、可以直接跳過。這把讀「跨平台 C」的閱讀量砍掉一大半。

## 對比與取捨

| 手段 | 解決什麼 | 限制 |
|---|---|---|
| `gcc -E -P foo.c` | 看某段 code 展開後的真相（含 `##`/`#`/`__LINE__`） | 輸出把 `#include` 全貼進來，很長，要 `grep` 定位 |
| `gcc -dM -E foo.c` | 列出實際定義了哪些巨集 → 判斷 `#ifdef` 哪條活 | 只給巨集清單，不給展開後 code |
| `clang -E`（同理） | 同 gcc，clang 訊息有時更清楚 | 需 clang；行為 99% 一致 |
| clangd（語意工具） | 展開後跳轉、灰掉 dead branch、找 `##` 生成符號 | 要 `compile_commands.json`（Ch 13/21） |
| 肉眼腦補 | 快，簡單巨集夠用 | 遇到 `##`/變參/巢狀 `#ifdef` 必翻車 |

**策略**：簡單的 `#define X 1` 直接讀；一旦看到 `##`、`#`、`__VA_ARGS__`、或超過兩層的 `#ifdef`，**立刻 `gcc -E -P`**，別跟前處理器賭。要判斷哪些 feature 分支活著，用 `gcc -dM -E` 配 Ch 21 抓來的真實 `-D`。

## 踩雷集錦

1. **錯誤直覺：「grep `sdshdr8` 就能找到它所有用法」。** 正確認識：`##` 生成的符號在原始碼裡不以連續字串存在（是 `sdshdr` + `8` 展開時黏的），純文字工具索引不到。**要找 token-paste 生成的符號，先 `gcc -E` 展開，或用 clangd。** 這也是為什麼有些「明明有用到卻 grep 不到」的鬼故事。

2. **錯誤直覺：「巨集參數就是照順序傳給函式」。** 正確認識：變參巨集（如 `FMTARGS`）能重排、複製、丟棄參數。redis 的 `FMTARGS` 把交錯的 fmt/value 拆成兩群——不展開你 100% 讀錯呼叫語意。看到變參巨集包住函式呼叫，**先展開再談**。

3. **錯誤直覺：「不加旗標的 `gcc -E` 展開結果就是真相」。** 正確認識：展開結果取決於 `-D`/`-I`。不套真實 build 旗標，`#ifdef USE_JEMALLOC` 之類的分支會走錯條。**一定用 Ch 21 從 `compile_commands.json` 抓來的旗標展開。**

4. **錯誤直覺：「header 裡 `#define HAVE_KQUEUE` 存在，代表這段 code 會編」。** 正確認識：它被平台守衛包著，在你這台可能根本沒生效。**用 `gcc -dM -E` 確認它是否真的被定義**，別看到 `#define` 就以為活著。

5. **錯誤直覺：「巨集展開只是文字替換，沒副作用」。** 正確認識：function-like macro 對有副作用的參數（`f(i++)`）會**重複求值**——若巨集內用了參數兩次，`i++` 會跑兩次。讀到把參數用多次的巨集（很多 `MIN`/`MAX` 都有此坑），要特別注意呼叫端傳的是不是有副作用的表達式。redis 的 `serverAssert` 只用 `_e` 一次算安全，但不是所有巨集都這麼乖。

## 進階：再往深一層

- **X-macro（本課沒在 redis 核心撞到，但你會在別處遇到）**：一種「用一份清單生成多份重複結構」的手法。典型長相：一個 `#define FOREACH_CMD(X) X(GET) X(SET) X(DEL)` 清單，配不同的 `X` 定義，能一次生成 enum、字串表、dispatch 表，保證三者永遠同步。讀到 `#include "xxx.def"` 被夾在兩個不同的 `#define X(...)` 之間、或同一個 header 被 include 多次配不同巨集，就是 X-macro。**讀法一樣：`gcc -E` 展開看它到底生成了什麼三件套。** Linux kernel、QEMU、很多 VM 的 opcode 表都用它。

- **`_Generic`（C11 的「泛型」）**：C11 加的關鍵字，能依參數**型別**選不同表達式，是 C 版的極簡多載。長相 `_Generic((x), int: f_int, double: f_double)(x)`。它不是巨集，但常被巨集包起來用。展開巨集後若看到 `_Generic`，要知道「真正呼叫哪個函式由 `x` 的靜態型別決定」——這是編譯期 dispatch，和 Ch 23 的執行期 indirection 是兩回事。

- **巨集展開的多趟掃描與 `#`/`##` 的求值時機坑**：preprocessor 展開有「參數先展開、但緊鄰 `#`/`##` 的參數不先展開」的規則，所以 redis 才要 `VFUNC_N` 包一層 `VFUNC_N_`——多一層間接是為了強迫 `NARG(...)` 先算出數字、再拿去 `##`。你讀到「為什麼同一件事要包兩層巨集」多半是這個原因。深究看 C 標準的 6.10.3 節，但實務上記住「多包一層 = 強迫先展開」即可。

- **展開後再回頭讀原始碼的節奏**：`gcc -E` 的輸出很長（`#include` 全貼進來），別想通讀。實務節奏是：`gcc -E -P ... | grep -A3 你關心的函式`，只看那幾行展開，理解後回到原始碼——**你現在「戴著展開後的眼鏡」讀原始碼**，看到 `serverAssert(x)` 腦中自動浮現那串 `__builtin_expect...`。展開是為了校準理解，不是為了取代原始碼閱讀。

## 動手練習

1. **展開你自己挑的巨集**：在 redis 選 `serverAssertWithInfo` 或 `serverPanic`（`server.h:664-666`），用真實旗標 `gcc -E -P` 展開，逐塊解釋它藏了什麼（提示：`serverPanic` 用了 `__VA_ARGS__`）。
2. **token paste 抓漏**：`grep -rn "struct sdshdr8" src/` 看你能找到幾處，再 `gcc -E -P` 展開 `sds.c` 的 `sdslen`，數一數展開後真正出現 `sdshdr8` 的地方，體會 grep 漏掉了多少。
3. **拆 FMTARGS 計數**：手動追 `NARG("a", 1, "b", 2)` 為什麼展開成 `4`——把它塞進 `RSEQ_N()` 前面，數第 121 個位置落在哪。再用 `gcc -E -P` 驗證你的推導。
4. **`-dM` 判活分支**：用 `gcc -dM -E -I. /tmp/tcfg.c | grep HAVE_` 列出你這台所有 `HAVE_*`，對照 `config.h` 的 `#ifdef`，說出哪些是別的平台的、可以跳過。
5. **（選）找生成檔的源頭**：`fmtargs.h` 下半是 `generate-fmtargs.py` 生成的，讀那支 python，說明它為什麼要生成到 120 個參數的 `ARG_N`/`RSEQ_N`——連到 Ch 21 的 code generation。

## 本章重點整理

- C 讀碼的頭號障眼法是 preprocessor：你看到的 source 與編譯器看到的是兩份東西（文字被替換、結構被生成、整段被刪）。
- 兩把根本武器：**`gcc -E -P`** 看某段展開後的真相；**`gcc -dM -E`** 列出實際定義了哪些巨集（判斷 `#ifdef` 哪條活）。務必套 Ch 21 抓來的真實 `-D`/`-I`。
- `#`（stringify）把參數文字轉字串（redis assert 訊息來源）；`##`（token paste）拼 token 生成型別/函式名（redis `SDS_HDR` → `sdshdr8`），對 grep/ctags 隱形。
- 變參巨集能計數、重排參數（redis `FMTARGS` 把交錯的 fmt/value 拆兩群）——光讀必錯，唯一解是展開。
- 條件編譯迷宮別靠肉眼推巢狀 `#if`，`gcc -dM -E` 一次告訴你哪些巨集真的定義了（redis 在 Linux 上只有 `HAVE_EPOLL`，對上 Ch 21 的 ae backend）。

## 自我檢核

- [ ] 給我一個含 `##` 的巨集，我能不能不靠猜、直接用 `gcc -E -P` 看它拼出什麼符號？
- [ ] redis assert 失敗時能印出「你 assert 的條件文字」，這是靠哪個運算子做到的？我能指出展開後那個字串從哪來嗎？
- [ ] `FMTARGS("a",1,"b",2)` 傳進 `sdscatfmt` 的實際參數順序是什麼？我能說出巨集怎麼重排的嗎？
- [ ] 我這台 Linux 上，redis 走 epoll 還是 kqueue？我用哪個指令一秒確認，而不是肉眼推 `config.h`？
- [ ] 為什麼 `grep "sdshdr8"` 會漏掉 `SDS_HDR(8,s)` 的用法？

## 延伸閱讀

- **[GCC Manual — "Preprocessor Output" 與 cpp 的 `-E`/`-dM`/`-P` 選項](https://gcc.gnu.org/onlinedocs/cpp/Preprocessor-Output.html)**
  - **讀哪裡**："Preprocessor Output"（`# linenum` 標記的意義）與 `cpp` 手冊的 `-dM`/`-dD` 一節。
  - **學到什麼**：`-E`/`-P`/`-dM` 精確在做什麼、輸出格式怎麼讀。本章兩把武器的權威說明。
  - **前提**：知道編譯分「前處理→編譯→組譯→連結」四階段。

- **[cppreference — Replacing text macros（`#`、`##`、可變參數巨集）](https://en.cppreference.com/w/c/preprocessor/replace)**
  - **讀哪裡**："# operator"、"## operator"、"Variadic macros" 三段與其範例。
  - **學到什麼**：stringify / token paste / `__VA_ARGS__` 的精確語意與求值順序坑（本章 `VFUNC` 為何要包兩層的理論根據）。
  - **前提**：讀過本章、看過 redis 的實例後再讀，會很有共鳴。

- **[Redis 原始碼：`src/fmtargs.h` 與 `utils/generate-fmtargs.py`](https://github.com/redis/redis/blob/7.4.0/src/fmtargs.h)**
  - **讀哪裡**：`fmtargs.h` 上半段人寫的 `NARG`/`VFUNC`，配 `generate-fmtargs.py` 看下半段怎麼被生成到 120 個參數。
  - **學到什麼**：一個生產級變參計數 + code generation 的完整實例，本章實戰三的原始出處。
  - **前提**：懂本章的 `##` 與變參計數原理。

- **[「X Macros」— Wikipedia 條目與其引用](https://en.wikipedia.org/wiki/X_Macro)**
  - **讀哪裡**：條目本身 + "Usage" 範例（enum + 字串表同步生成）。
  - **學到什麼**：X-macro 這個你遲早會在 kernel/QEMU/VM 撞到的手法——一份清單生成多份同步結構。redis 核心少見，但泛系統程式極常見。
  - **前提**：懂 `#include` 可被多次含入、`#define` 可在 include 前後改。

搞定了 preprocessor 的障眼法，你看到的每一行 code 都是編譯器真正編的東西了。但還有一種「看不到終點」的困難：`fn_ptr()`、`cmd->proc(c)`、虛函式——呼叫寫在這裡，實際跳去哪卻要執行期才知道。下一章我們處理 indirection（動態 dispatch），並用 gdb 在程式真跑時抓出「這一跳到底落在哪個函式」。

→ [Ch 23 讀懂 indirection](./23-reading-indirection.md)
