# Ch 22 — CPython 偵察：object model 與 eval 入口

> **目標**：把一個近 84 萬行 C/H、超過 12 萬行 Python 標準函式庫的大型 runtime，在 60 分鐘偵察內收斂成一張你能導航的地圖。不是讀懂 CPython，是找到「eval 入口」和「object model 入口」這兩根主樑，其餘刻意當黑箱。這章示範：當 codebase 大到 Lua 的 40 倍，`reading_code` 的收斂技巧不是加分項，是唯一能活下來的姿態。

> **目標codebase**：CPython `v3.13.1`（commit `0671451`）

## 為什麼需要這個？

Part 1 的 Lua 約 2 萬行，你可以把整個 `src/` 攤開來讀。Part 2 的 SQLite 是 amalgamation，一個 `sqlite3.c` 塞下全部。到了 CPython，尺度變了：

```
$ cd ~/cbcs/cpython
$ find . \( -name '*.c' -o -name '*.h' \) | xargs cat | wc -l
837430
$ find . -name '*.py' -path './Lib/*' | xargs cat | wc -l
127217
$ find . -name '*.c' | wc -l
439
```

83 萬行 C/H、12 萬行標準函式庫 Python、439 個 `.c` 檔。這已經跨進 `reading_code` Ch 31「大型專案的分而治之」的地界——**「讀懂 CPython」是一個做不到、也有害的目標**。核心開發者沒有一個人腦裡裝著完整的 CPython 架構。

所以這章的偵察目標不是「理解 CPython」，是回答兩個具體問題，其餘 99% 主動無視：

1. **一段 Python 程式碼是怎麼被執行的？**（eval 入口）
2. **一個 Python 值（`3`、`"hi"`、一個 list）在 C 裡長什麼樣？**（object model 入口）

這兩根樑撐起後面三章：Ch 23 攻 eval loop、Ch 24 攻 object model、Ch 25 示範怎麼在這個尺度用分而治之攻一個具體語意。

## 先建立直覺：CPython 是「編譯器前端 + bytecode VM + object 系統」三件事

在打開任何 `.c` 之前，先在腦裡立一張最粗的地圖。你在 `compilers/compiler_frontend` 學過「source → token → AST → …」，CPython 前半段正是這條線，後半段接上一台 bytecode VM——這是本課第三台 VM（前兩台：Lua Ch 4、SQLite Ch 9）：

```
   你打的 .py 原始碼
        │
        │  Parser/ （tokenizer + PEG parser，3.9 後改 PEG）
        ▼
      AST（抽象語法樹）
        │
        │  Python/compile.c （AST → bytecode）
        ▼
   code object（一串 bytecode + 常數表 + 變數名表）
        │
        │  Python/ceval.c  _PyEval_EvalFrameDefault()  ← eval loop，本 Part 主戰場
        ▼
   執行：一個 opcode 一個 opcode 跑，操作 PyObject*
        │
        └──► 每個值都是 PyObject*，行為由 ob_type 指的 PyTypeObject 決定
             （Objects/ 目錄，Ch 24 主戰場）
```

**兩個入口對應兩個目錄**：eval loop 在 `Python/`，object model 在 `Objects/`。記住這兩個目錄，你就有了導航的起點。剩下的目錄（`Modules/`、`Parser/`、`Include/`）在你今天的任務裡是黑箱。

## 核心一：目錄結構——用檔名 + 領域知識做主動無視

`reading_code` Ch 31 的第一課：面對大 codebase，**你不打開任何檔案，光靠檔名 + 領域先驗就能砍掉 90% 的範圍**。CPython 的頂層目錄命名極有紀律，是練這招的好材料：

```
cpython/
├── Python/     直譯器核心：eval loop、compiler、import、GIL、GC   ← 相關（eval 入口）
│              ceval.c bytecodes.c compile.c gc.c import.c ...
├── Objects/    所有內建型別的實作：int/float/str/list/dict/type    ← 相關（object 入口）
│              object.c typeobject.c longobject.c dictobject.c ...
├── Include/    公開與內部標頭：PyObject、PyTypeObject、所有 struct  ← 相關（型別定義）
│              object.h  cpython/object.h  internal/pycore_*.h
├── Parser/     tokenizer + PEG parser（source → AST）              ← 今天黑箱
├── Modules/    C 寫的擴充模組：_io、_socket、math、_json...        ← 今天黑箱（最大，98 個 .c）
├── Lib/        純 Python 標準函式庫（12 萬行 .py）                 ← 今天黑箱
├── Grammar/    語言文法定義（python.gram，PEG）                    ← 今天黑箱
├── Tools/      內部工具，含 cases_generator（Ch 23 會回來）        ← 特殊，Ch 23 相關
└── InternalDocs/  核心開發者寫給自己的內部文件                     ← 偵察黃金
```

各目錄的 `.c` 數量（真跑）：

```
$ for d in Python Objects Modules Parser; do
    echo "$d: $(find $d -maxdepth 1 -name '*.c' | wc -l) .c"
  done
Python:  92 .c
Objects: 43 .c
Modules: 98 .c
Parser:   8 .c
```

`Modules/` 檔數最多（98 個）但**今天全部無視**——它們是各種擴充模組（`_socketmodule.c`、`mathmodule.c`…），跟「一段 Python 怎麼執行」「一個值長怎樣」無關。這正是 Ch 31 刀六「主動無視當一等公民技能」：98 個檔，看檔名就知道無關，心安理得地闔上。你今天真正要碰的是 `Objects/` 裡的少數幾個、`Python/` 裡的兩三個。

> **偵察省時技巧**：CPython 有 `InternalDocs/` 目錄，是核心開發者寫給彼此的內部文件。加上官方 [devguide.python.org/internals](https://devguide.python.org/internals/) 的「CPython source code layout」，這兩份是「作者自述架構」——`reading_code` Ch 5 的鐵律「讀 code 前先讀作者怎麼介紹它」。在 CPython 這種尺度，先讀自述能省你好幾小時亂逛。

## 核心二：找 eval 入口——一段 code 從哪裡開始跑

問題：「`python foo.py` 之後，一段 bytecode 是在哪個 function 裡被一個一個執行的？」

`reading_code` Ch 6「找 entry point 與主迴圈」的做法：對一個 VM，主迴圈通常是一個巨大的 function，名字帶 `eval`/`exec`/`run`/`dispatch`。直接 grep：

```
$ rg -n "_PyEval_EvalFrameDefault" Python/ceval.c
682:_PyEval_EvalFrameDefault(PyThreadState *tstate, _PyInterpreterFrame *frame, int throwflag)
```

`_PyEval_EvalFrameDefault`（`Python/ceval.c:682`，v3.13.1）就是 eval loop——CPython 的心臟。它接三個參數：`tstate`（執行緒狀態）、`frame`（當前執行的 frame，即一次函式呼叫的執行上下文）、`throwflag`（是否有例外要丟進來）。原始碼自己都在提醒它有多大：

```c
/* _PyEval_EvalFrameDefault() is a *big* function,
```
（`Python/ceval.c:677`）

**這裡有一個 3.13 的大陷阱，先埋一個伏筆**：你打開 `_PyEval_EvalFrameDefault`，想找「BINARY_OP 這個 opcode 在哪裡處理」，你會 grep 不到完整的 `case BINARY_OP:`。因為 3.13 的 opcode 實作**不是手寫在 `ceval.c` 裡**，而是寫在一個 DSL 檔 `Python/bytecodes.c`、經由工具生成 `Python/generated_cases.c.h`、再被 `ceval.c` include 進來。這個生成關係是本 Part 最重要的一課，Ch 23 專門拆它。偵察階段你只要先知道：**eval loop 的 body 是 `#include` 進來的生成碼**，別在 `ceval.c` 裡硬找 opcode。

```
$ rg -n "generated_cases.c.h" Python/ceval.c
787:#include "generated_cases.c.h"
```

### 一個偵察陷阱：eval 入口其實隔著一層函式指標

你以為「呼叫 eval loop」就是直接 call `_PyEval_EvalFrameDefault`？偵察時多看一步，會發現中間隔著一層 indirection（`reading_code` Ch 23 讀 indirection 的經典場景）：

```
$ rg -n "_PyEval_EvalFrame\b|eval_frame\(" Include/internal/pycore_ceval.h
115:_PyEval_EvalFrame(PyThreadState *tstate, struct _PyInterpreterFrame *frame, int throwflag)
121:    return tstate->interp->eval_frame(tstate, frame, throwflag);
```

`_PyEval_EvalFrame`（`Include/internal/pycore_ceval.h:115`）不直接呼叫 `_PyEval_EvalFrameDefault`，而是透過 `tstate->interp->eval_frame` 這個**函式指標**。預設它指向 `_PyEval_EvalFrameDefault`，但這層 indirection 讓「換掉整個 eval loop」變可能——debugger、profiler、甚至 JIT 都靠 hook 這個指標插入。偵察時認出這層很重要：它解釋了「為什麼 gdb 追進去有時多一跳」，也是「可替換的 eval loop」這個擴充點的所在。**偵察不只要找到入口，還要看清入口前有沒有 indirection**——否則你 debugger 追時會被那一跳搞糊塗。

## 核心三：找 object model 入口——一個值長什麼樣

問題：「Python 裡的 `3`、`"hi"`、`[1,2]`，在 C 裡是什麼型別？」

答案是「everything is a `PyObject*`」。這句話你可能聽過，但偵察要親眼看到它。`PyObject` 定義在 `Include/object.h`：

```
$ rg -n "struct _object \{" Include/object.h
163:struct _object {
```

```c
struct _object {
    union {
       Py_ssize_t ob_refcnt;
       ...
    };
    PyTypeObject *ob_type;
};
```
（`Include/object.h:163`，v3.13.1，非 free-threaded 版本；已省略 free-threaded 分支）

兩個欄位就是整個 object model 的地基：`ob_refcnt`（引用計數，記憶體管理用）、`ob_type`（指向這個物件的「型別」，一個 `PyTypeObject*`，決定它的所有行為）。**每一個 Python 值在 C 裡都以這兩個欄位開頭。** 一個 int 是 `PyLongObject`、一個 float 是 `PyFloatObject`，但它們的前綴都是這個 `PyObject`，所以都能被當成 `PyObject*` 統一處理。這是 Ch 24 的主題，偵察只要確認入口在這。

行為由誰決定？`ob_type` 指的 `PyTypeObject`——一張巨大的函式指標表（vtable）：

```
$ rg -n "struct _typeobject \{" Include/cpython/object.h
147:struct _typeobject {
```

`3 + 4` 怎麼加、`len(x)` 怎麼算，都是查 `ob_type` 這張表裡對應的 slot（`tp_as_number->nb_add`、`tp_as_sequence->sq_length`…）。偵察階段你只要建立這個心智模型：**值 = 資料 + 一個指向 vtable 的指標**。細節留給 Ch 24。

## 底層機制：偵察一條 `a + b` 的粗略骨架（不深潛，只畫骨架）

把兩個入口串起來，畫出 `a + b` 從 bytecode 到型別實作的**骨架**——注意這是偵察，我們只確認「路徑經過哪些站」，不進站深潛（那是 Ch 23/24/練習 E 的事）：

```
  Python:  a + b
    │  compile.c 編成 bytecode
    ▼
  LOAD_FAST a │ LOAD_FAST b │ BINARY_OP(+)      ← 三個 opcode
    │
    │  在 _PyEval_EvalFrameDefault 的 dispatch loop 裡一個個執行
    ▼
  BINARY_OP 這個 opcode 的 body（來自 bytecodes.c，生成到 generated_cases.c.h）
    │  它查一張表：_PyEval_BinaryOps[oparg]
    ▼
  _PyEval_BinaryOps[NB_ADD] == PyNumber_Add     （Python/ceval.c:313-314）
    │
    ▼
  PyNumber_Add(v, w)                             （Objects/abstract.c:1139）
    │  查 v 的 ob_type->tp_as_number->nb_add slot
    ▼
  對 int：long_add（Objects/longobject.c）
  對 float：float_add（Objects/floatobject.c）
```

這條骨架每一站我都在真 source 裡確認過（後面各章會逐站深潛引真碼）。偵察的產出就是這張骨架——它告訴你「要理解 `a+b`，該讀 `ceval.c` 的 dispatch、`ceval.c:313` 的 ops 表、`abstract.c` 的 `PyNumber_Add`、`longobject.c` 的 slot」，其餘 83 萬行今天不碰。**這就是把 83 萬行收斂成四個檔案裡的四段。**

```
$ rg -n "_PyEval_BinaryOps\[\]" Python/ceval.c
313:const binaryfunc _PyEval_BinaryOps[] = {
$ rg -n "\[NB_ADD\]" Python/ceval.c
314:    [NB_ADD] = PyNumber_Add,
$ rg -n "^PyNumber_Add" Objects/abstract.c
1139:PyNumber_Add(PyObject *v, PyObject *w)
```

三個 grep，骨架的三個關鍵站都在真 source 裡對上了。這是偵察該有的產出：不是讀懂，是**確認路徑存在、標好座標**，把深潛留給後面。

### 量化：偵察把 83 萬行收斂到多少

把這次偵察的收斂算出來，你才體會「主動無視」的威力有多大：

```
   起點：83 萬行 C/H、439 個 .c、98 個 Modules/、12 萬行 Lib/
   偵察後鎖定的深潛目標（後面各章要碰的）：
     Python/ceval.c         eval loop（Ch 23）
     Python/bytecodes.c     opcode DSL（Ch 23）
     Include/object.h       PyObject（Ch 24）
     Include/cpython/object.h  PyTypeObject（Ch 24）
     Objects/abstract.c     PyNumber_Add（Ch 24）
     Objects/longobject.c   long_add（Ch 24）
     Python/gc.c            cyclic GC（Ch 24）
   ─────────────────────────────────────────
   相關檔案：~7 個（439 個裡的 1.6%）
   今天真讀的骨架：每個檔幾行，合計 < 100 行
```

**偵察 60 分鐘的產出，是把「該深潛哪 7 個檔」標出來，同時心安理得地把另外 432 個 `.c` 闔上。** 這不是讀了 1.6%、漏了 98.4%——是精準命中了任務需要的 1.6%，其餘對「eval + object model」這兩個入口確實無關。這正是 `reading_code` Ch 31 說的：大 codebase 上 95% 的動作是「判斷無關並跳過」，把跳過做好，剩下的 5% 才有餘裕細讀。

## 先自己攻堅：60 分鐘偵察清單（對照教材前先跑一遍）

`reading_code` Ch 5 的 60 分鐘偵察，套到 CPython 是這張清單。先自己跑，再看上面的教材，你的地圖和教材的差距就是你要補的。

**0–10 分：讀作者自述，不碰 code**。開 `devguide.python.org/internals` 的 layout 頁、`InternalDocs/`、`README.rst`。目標：知道有 `Python/`、`Objects/`、`Parser/` 這些目錄各管什麼。

**10–25 分：量戰場、標無視**。

```
$ find . -name '*.c' | wc -l                    # 439 個 .c
$ for d in Python Objects Modules Parser; do
    echo "$d: $(find $d -maxdepth 1 -name '*.c' | wc -l)"; done
$ wc -l Python/ceval.c Python/bytecodes.c Objects/typeobject.c
```

看到 `Modules/` 98 個、`typeobject.c` 上萬行——立刻決定今天不碰哪些。這一步的產出是一張「相關/無視」的目錄清單。

**25–40 分：找兩個入口**。

```
$ rg -n "_PyEval_EvalFrameDefault\(" Python/ceval.c     # eval 入口
$ rg -n "struct _object \{" Include/object.h            # object 入口
$ rg -n "struct _typeobject \{" Include/cpython/object.h # 型別 vtable
```

三個 grep，兩個入口 + vtable 定位完成。

**40–55 分：畫骨架、埋問號**。從一段 `a+b` 或 `len(x)` 出發，用 grep 追出粗略骨架（像本章「底層機制」那張），追不下去的地方**標問號**別硬鑽——那些問號就是後面各章要深潛的點。

**55–60 分：外化**。把目錄清單 + 兩入口 + 骨架 + 問號寫成一頁筆記。這頁就是你的 CPython 地圖。

**自我評估**：60 分鐘後你若能回答「eval loop 在哪個函式、值長什麼樣、今天哪些目錄不碰」，偵察就成功了——注意這三題都不是「讀懂 CPython」，是「知道去哪找」。這正是大 codebase 偵察的正確產出。

## 對比與取捨

| 面向 | Lua（Part 1） | SQLite（Part 2） | CPython（本 Part） |
|---|---|---|---|
| 規模 | ~2 萬行，讀得完 | ~15 萬行 amalgamation | 83 萬行 C + 12 萬行 Lib |
| 偵察策略 | 可攤開全部 `src/` | 分層文件領路 | **只找兩個入口，其餘主動無視** |
| eval loop | `luaV_execute`（手寫 switch） | `sqlite3VdbeExec`（手寫 switch） | `_PyEval_EvalFrameDefault`（**生成碼 include**） |
| 值表示 | tagged union `TValue` | `Mem` struct | boxed `PyObject*` + type slot |
| 「讀懂全部」 | 可行且值得 | 勉強可行 | **不可行且有害** |

CPython 偵察的關鍵轉變：Lua/SQLite 你還能奢望「大致讀完」，CPython 你必須從第一分鐘就抱著「我只攻兩個入口」的心態。這不是消極，是 Ch 31 講的「在城市裡導航而非逛遍城市」。

## 踩雷集錦

1. **錯誤直覺：「先把 CPython 大致讀一遍再說」。** → 正確認識：83 萬行沒人讀得完，包括核心開發者。偵察的目標是找到 eval 入口（`Python/ceval.c`）和 object 入口（`Include/object.h`），把 `Modules/`（98 個 `.c`）、`Lib/`（12 萬行）當黑箱。抱著「讀懂全部」進場，你會在 `Modules/` 裡淹死。
2. **錯誤直覺：opcode 的實作應該在 `ceval.c` 裡用 `switch/case` 手寫。** → 正確認識（3.13 專屬）：opcode 定義在 DSL 檔 `Python/bytecodes.c`，經工具生成 `generated_cases.c.h`，被 `ceval.c:787` include。你在 `ceval.c` 裡 grep `case BINARY_OP` 找不到完整實作，會以為讀錯——其實實作在別的檔。Ch 23 專門拆這個生成關係。
3. **錯誤直覺：`PyObject` 一定有一個 `ob_refcnt` 的簡單整數欄位。** → 正確認識：3.13 的 `ob_refcnt` 是一個 union（配合 PEP 683 immortal objects），而且 free-threaded（`Py_GIL_DISABLED`）build 的 `struct _object` 是完全不同的定義（per-object mutex + split refcount）。偵察時看到兩個 `struct _object` 別慌，那是 `#ifndef Py_GIL_DISABLED` 的兩個分支（`Include/object.h:163` 與 `:207`）。
4. **錯誤直覺：`Objects/` 的檔案要全部讀。** → 正確認識：43 個 `.c` 裡你今天只需要 `object.c`（通用物件協定）、`typeobject.c`（型別系統）、加上你追的那個型別（`longobject.c`）。`dictobject.c`、`unicodeobject.c`、`setobject.c`… 除非任務逼你，否則黑箱。
5. **錯誤直覺：不 build 就無法驗證。** → 正確認識：偵察階段純讀 + `rg` 就能建骨架，不需要 build。但 CPython 能 build（`./configure && make -j`），build 後你能用 `dis` 看真 bytecode、用 gdb 在 `_PyEval_EvalFrameDefault` 下中斷點動態驗證（Ch 23、練習 E 會用）。偵察先靠讀，深潛時再祭出 build。

## 進階：再往深一層

- **用 `dis` 反推 bytecode，不用讀 compiler**：你今天把 `Parser/` 和 `compile.c` 當黑箱，但你可以用標準函式庫的 `dis` 模組直接看某段 code 編成什麼 bytecode：`python -c "import dis; dis.dis('a+b')"`。這是「介面優先」（Ch 31 刀三）的漂亮應用——你不讀 compiler 實作，用它的輸出當合約。Ch 23 會貼真 `dis` 輸出。
- **`InternalDocs/` 與 devguide 是偵察加速器**：CPython 對「作者自述」做得比多數專案好。`InternalDocs/`、devguide 的 internals 章、以及 `Tools/cases_generator/interpreter_definition.md`（bytecode DSL 的官方說明）——這三份在你深潛前先讀，能讓你少走很多冤路。
- **`Include/` 的三層結構是理解「穩定邊界」的鑰匙**：`Include/*.h`（穩定公開 API）、`Include/cpython/*.h`（CPython 專屬、較不穩定的公開 API）、`Include/internal/pycore_*.h`（純內部，不對外保證）。這個分層本身就是 Ch 26 要萃取的「穩定 C-API 邊界」pattern 的地圖。偵察時記住這三層，後面看得懂為什麼 `PyObject` 在 `object.h` 而 `_PyInterpreterFrame` 在 `internal/pycore_frame.h`。

## 本章重點整理

- CPython 是「編譯器前端（Parser/ + compile.c）+ bytecode VM（ceval.c）+ object 系統（Objects/）」三件事；本 Part 只攻後兩者的入口。
- 兩個入口：**eval loop** = `_PyEval_EvalFrameDefault`（`Python/ceval.c:682`）；**object model** = `PyObject`（`Include/object.h:163`），每個值都以 `ob_refcnt` + `ob_type` 開頭。
- 目錄導航：`Python/`（直譯器核心）、`Objects/`（型別）、`Include/`（定義）是相關；`Modules/`（98 個 `.c`）、`Lib/`（12 萬行）、`Parser/` 今天主動無視。
- 3.13 大陷阱：opcode 實作不在 `ceval.c` 手寫，而是 `bytecodes.c` DSL → 生成 `generated_cases.c.h` → `ceval.c` include（Ch 23 詳解）。
- 偵察產出不是「讀懂」，是一張標好座標的骨架——把 83 萬行收斂到四個檔案裡的四段。

## 自我檢核

- [ ] 有人叫你「熟悉一下 CPython」，你能不能先反問「為了做什麼具體任務」，再據此只攻對應入口？
- [ ] 你能說出 eval 入口和 object 入口分別在哪個目錄、哪個 function/struct 嗎（不查資料）？
- [ ] 你能解釋為什麼在 `ceval.c` 裡 grep `case BINARY_OP` 找不到完整 opcode 實作嗎？
- [ ] `Modules/` 有 98 個 `.c`，你今天為什麼一個都不用讀？講出主動無視的依據。
- [ ] `Include/object.h` 裡有兩個 `struct _object`，那是什麼造成的（提示：一個 build 開關）？

## 延伸閱讀

- **[CPython Internals — devguide.python.org/internals](https://devguide.python.org/internals/)（官方）。**
  - **讀哪裡**：「CPython source code layout」與「Interpreter / compiler」兩節。
  - **學到什麼**：核心團隊自己怎麼描述目錄結構與執行流程——偵察前先讀作者自述（`reading_code` Ch 5）的最佳實踐。
  - **前提**：讀得懂 C，會用 `rg`。
- **[`Tools/cases_generator/interpreter_definition.md`](https://github.com/python/cpython/blob/v3.13.1/Tools/cases_generator/interpreter_definition.md)（repo 內）。**
  - **讀哪裡**：開頭「Background」與 DSL 語法簡介。
  - **學到什麼**：3.13 為什麼把 opcode 寫成 DSL、生成關係是什麼——本章埋的伏筆，Ch 23 的預習。
  - **前提**：知道 bytecode VM 是什麼（Lua Ch 4 / SQLite Ch 9 已建立）。
- **《The Programmer's Brain》— Felienne Hermans（Manning, 2021），第 1、7 章。**
  - **讀哪裡**：chunking（第 1 章）與 working memory / 大專案認知負荷（第 7 章）。
  - **學到什麼**：為什麼在 83 萬行裡「一次只碰一個入口」是生理必需而非偷懶——支撐本章主動無視的科學根據。
  - **前提**：無。

偵察完成，兩個入口就位。下一章我們深潛第一個入口：eval loop。但在讀 dispatch 之前，得先拆穿 3.13 埋的那個陷阱——opcode 到底怎麼從 `bytecodes.c` 生成出來、`ceval.c` 又是怎麼把它 include 進去的。

→ [Ch 23 ceval.c：bytecode eval loop（三個 VM 的第三個）](./23-cpython-eval-loop.md)
