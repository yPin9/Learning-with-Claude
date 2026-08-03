# Ch 26 — 萃取 pattern：refcount / object protocol / C-API 邊界

> **目標**：把 Ch 22–25 讀到的 CPython 設計 idiom 結晶成五張可遷移的 pattern 卡片，每張標「beacon（怎麼一眼認出）／它在哪／可遷移到哪」。這章不引新 code，是把前四章的觀察收斂成你 pattern 字典裡的新條目，並接到本課其他 Part 遇過的同類 pattern。合上前面四章，先自己寫下「我認出了哪些 pattern」，再對照這章——pattern 要自己說出來才進長期記憶。

> **目標codebase**：CPython `v3.13.1`（commit `0671451`）

## 為什麼需要這個？

`reading_code` 教你怎麼讀，這門課教你**讀完之後留下什麼**。留下的不是「CPython 的 `PyObject` 第 163 行長怎樣」這種易忘的細節，而是「boxed object + type slot 這個 pattern，一眼認出、知道它解什麼問題、下次在別的 codebase 遇到能秒懂」。這才是能遷移、能複利的資產。

CPython 給你五個高價值 pattern，其中三個與前面 Part 的 codebase 直接對照（VM dispatch、記憶體管理策略），形成「同一個問題的不同解法光譜」——這種對照是 pattern 字典最值錢的部分，因為它教你的不是單一答案，是**設計空間**。

## 先建立直覺：pattern 卡不是筆記，是「一眼認出」的觸發器

pattern 卡和「讀書筆記」有本質差別。筆記記的是「這個專案怎麼做」；pattern 卡記的是「這個形狀出現時，我該想到什麼」。差別在**觸發方向**：

```
   讀書筆記（被動）                    pattern 卡（主動觸發）
   ┌────────────────────┐            ┌──────────────────────────────┐
   │ 問：CPython 的值    │            │ 看到：struct 第一個成員是一個   │
   │     怎麼表示？      │            │       header 巨集 + 一坨函式指標│
   │ 答：翻筆記找 PyObject│           │ 觸發：「這是 boxed object +     │
   │                    │            │        vtable，行為靠查 slot」  │
   │ → 需要先知道問什麼  │            │ → 不需要問，看到形狀就秒懂      │
   └────────────────────┘            └──────────────────────────────┘
```

所以每張卡的靈魂是 **beacon**——「怎麼一眼認出這個 pattern」。beacon 是 `reading_code`（與《The Programmer's Brain》）反覆講的概念：一個讓你 chunk 一整段 code 的視覺線索。`PyObject_HEAD` 當 struct 首成員、一個塞滿 `tp_*` 的 struct、`goto *table[opcode]`——這些都是 beacon。你練的不是「記住 CPython」，是「看到 beacon 就觸發正確的 chunk」。五張卡，五組 beacon，五個能遷移到無數其他 codebase 的觸發器。

下面每張卡的順序刻意固定：**一句話 → beacon → 為什麼這樣設計（取捨）→ 在哪（CPython 座標）→ 可遷移到哪**。合上前四章，先自己對每張卡默寫 beacon，再往下讀對照。

## Pattern 卡 1：boxed object + type slot（vtable / protocol）

**一句話**：每個值是 heap 上一個物件，前綴一個指向「型別物件」的指標；型別物件是一張函式指標表（vtable），值的所有行為都靠查這張表的對應 slot 來分派。

**beacon（怎麼一眼認出）**：
- struct 第一個成員是一個「header」巨集（`PyObject_HEAD`），讓子型別能 cast 成基底型別。
- 有一個「型別/類別」struct 塞滿函式指標（`tp_add`、`tp_hash`、`tp_dealloc`… 一堆 `tp_*`/`nb_*`/`sq_*`）。
- 操作函式不寫 `if (是int) else if (是float)`，而是 `obj->type->某slot(obj, ...)`。

**為什麼這樣設計**：C 沒有 class/virtual method，但大型系統需要多型（同一個 `+` 對不同型別做不同事）。boxed object + type slot 是 C 實現多型的標準解：把「怎麼做」的決定權從呼叫端（`PyNumber_Add`）移到資料端（`Py_TYPE(v)->nb_add`）。代價是每個值都要掛型別指標、多一次間接跳轉；回報是**新增型別不用改任何現有的運算函式**——`PyNumber_Add` 寫一次，之後加 `Decimal`、`Fraction`、你自訂的 class，只要各自填 `nb_add` slot 就自動支援。這就是開放-封閉原則在 C 的長相。

**在哪（CPython）**：`Include/object.h` 的 `PyObject`（`ob_type` 指標）、`Include/cpython/object.h` 的 `PyTypeObject`（`tp_*` slots）、`Objects/abstract.c` 的 `PyNumber_Add` 查 `nb_add` slot。`a + b` 的完整鏈（Ch 24）就是這張卡的活教材。beacon 的最短形態：`rg "PyObject_HEAD" Objects/*.c` 一下，每個內建型別的 struct 首成員都是它——那就是「這是一個可被當 `PyObject*` 的 boxed 型別」的信號。

**可遷移到哪**：
- **Lua 的 metatable**（Part 1 Ch 5）：同一個「行為靠查表分派」的 idea，但 Lua 的 tagged union 值不需要每個都掛型別指標——metatable 是可選的、按 type tag 查的。對照看，你會懂「boxed（CPython）vs tagged（Lua）」是同一個 protocol 概念的兩種載體。
- **nginx 的 module handler**（Part 3 Ch 16）：`ngx_http_module_t` 塞函式指標，request 處理靠查 module 的 handler slot——同一個 vtable/函式指標表 pattern，換到 web server 語境。
- **C 裡的 OO 模擬通則**：Linux kernel 的 `file_operations`、`struct device`，任何「struct 塞函式指標當 vtable」都是這張卡。認出它，你在任何 C 專案看到「一坨函式指標的 struct」都知道那是多型的實現。

## Pattern 卡 2：refcount + cyclic GC 混合（記憶體管理策略光譜）

**一句話**：以引用計數為主力（歸零即時回收，行為可預測、無 stop-the-world），額外跑一個標記式 GC 專門收引用計數抓不到的「環」。

**beacon**：
- 物件 header 有個計數欄位（`ob_refcnt`），到處是 `INCREF`/`DECREF` 巨集。
- 同時存在一個獨立的 GC 模組（`Python/gc.c`），有「代」（generation）、`traverse`/`clear` callback。
- 型別分兩類：葉子型別（不參與 GC）vs 容器型別（帶 `HAVE_GC` flag、實作 `tp_traverse`）。

**為什麼這樣設計**：純引用計數簡單、回收即時，但有一個無解的盲點——**環**（互指的物件計數永遠不歸零）。純標記-清除 GC 能收環，但要週期性掃全部物件、有 stop-the-world 暫停、且回收不即時。CPython 賭「絕大多數物件無環、且希望即時回收（尤其是大物件釋放要快、記憶體佔用要可預測）」，於是用 refcount 當主力吃掉 99% 的回收，只留「環」這個小眾情況給一個額外的分代 GC 收尾。這是「用便宜的主力機制處理常見情況，用昂貴的補充機制處理少數情況」的通用設計智慧。

**在哪（CPython）**：`Include/object.h` 的 `Py_INCREF`/`Py_DECREF`、`Objects/object.c` 的 `_Py_Dealloc`（查 `tp_dealloc`）、`Python/gc.c` 的 `update_refs`/`subtract_refs` 試除法收環（Ch 24 底層機制）。beacon：一個型別 struct 同時有「header 裡的計數欄位」和「一個獨立的 `gc.c` + `tp_traverse` callback」，就是這張混合卡。

**可遷移到哪 / 光譜對照**：這張卡最值錢的是它在**記憶體管理策略光譜**上的位置：
```
   純引用計數          混合（CPython）          純標記-清除 GC
   ─────────────────────────────────────────────────────►
   即時回收             refcount 主 + GC 補環      Lua（Part 1 Ch 6）
   但收不掉環           兩者分工                    週期性、能收環
                                                    但有 GC 暫停
```
- **Lua 的增量 GC**（Part 1 Ch 6）是光譜另一端：純標記-清除，沒有引用計數，天生能收環，代價是回收不即時、需要增量化來攤平暫停。
- 對照兩者你學到的不是「誰對」，是**取捨**：CPython 賭「大多數物件無環、要即時回收」，用 refcount 當主力、GC 當保險；Lua 賭「不想要 refcount 的到處 INCREF/DECREF 開銷」，純 GC。這是 Ch 27 三 VM 對照的核心議題之一。
- 可遷移：任何你看到「refcount + 補一個環收集器」（如某些 C++ shared_ptr + 手動斷環、Perl、PHP）都是這張卡。

## Pattern 卡 3：bytecode DSL 生成（用 DSL 生成 VM 的執行核心）

**一句話**：VM 的 opcode 實作不手寫在直譯器主檔，而用一套領域 DSL 宣告（含 stack 效果），由生成器展開成 C，再 include 進 eval loop——把「宣告意圖」與「產生樣板」分離。

**beacon**：
- 一個看起來像 C 但不能編譯的檔（`bytecodes.c`，檔頭常自述「被工具讀取生成 X」）。
- 一個「Do not edit!」的生成檔（`generated_cases.c.h`），被主檔 `#include`。
- opcode 定義用非標準語法標 stack 效果（`(lhs, rhs -- res)`）。

**為什麼這樣設計**：一台 VM 有幾百個 opcode，每個都要手寫「更新指令指標、算 stack pointer 怎麼動、錯誤時 pop 幾個、DECREF 哪些」——這些樣板佔了 opcode 實作的一大半，且極易出錯（多 pop 一個、漏 DECREF 一個就是記憶體 bug）。CPython 把「意圖」（`(lhs, rhs -- res)` 這行宣告消耗兩個產出一個）和「樣板」（生成器據此產 `stack_pointer` 運算、`Py_DECREF`、`goto pop_2_error`）分離。人只寫意圖，機器產樣板。額外好處：同一份 DSL 能生成多個目標（tier-1 直譯器、tier-2 optimizer、metadata 表），一次宣告多處受益。代價是多一層生成、debug 時要在生成檔和 DSL 之間對照。

**在哪（CPython 3.13）**：`Python/bytecodes.c`（DSL 源）→ `Tools/cases_generator/tier1_generator.py`（生成器）→ `Python/generated_cases.c.h`（生成）→ `Python/ceval.c:787` include（Ch 23）。CPython 還有第二套生成系統 Argument Clinic（Ch 25）。beacon：一個「看似 C 但檔頭寫『被工具讀取』」的源檔 + 一個「Do not edit!」的生成檔被主檔 `#include`。

**可遷移到哪**：
- **接 `reading_code` Ch 22（讀懂巨集與 metaprogramming）**：這張卡是那章「生成碼」主題在真專案的高階形態。認出「Do not edit / generated」標記 → 知道要往上游 DSL 找，是讀任何有 code generation 的大專案的核心紀律。
- **其他有 opcode 生成的 VM**：Ruby 的 YARV、V8 的部分 builtins（你在 `browser_pwn` 見過的 Torque/CodeStubAssembler）都是「用 DSL/生成器產 VM 執行核心」的同族。CPython 的 cases_generator 是這個 idea 的一個乾淨範例。
- 可遷移的判斷力：看到大 codebase 就先問「它有哪些生成系統」，摸清了才不會在生成葉子檔裡瞎找上游。

## Pattern 卡 4：computed-goto dispatch（三個 VM 共通的分派引擎）

**一句話**：VM 的取指-分派用 computed goto（`goto *table[opcode]`）取代大 switch，讓每個 opcode 結尾各有一個 dispatch 點，餵飽 CPU 分支預測器。

**beacon**：
- 一張「opcode → label 位址」的表（`opcode_targets[]`，用 `&&label` 取址）。
- `goto *某表[opcode]` 這種 computed goto 語法（GCC/Clang 擴充）。
- 常有 `#if USE_COMPUTED_GOTOS ... #else switch ...` 的雙路 fallback。

**為什麼這樣設計**：一個大 `switch(opcode)` 每次分派都從同一個位址跳出，CPU 的間接分支預測器只有一條歷史，對「這次是哪個 opcode」幾乎猜不準，一直預測失敗（pipeline flush）。computed goto 讓**每個 opcode 的結尾各有一個獨立的 dispatch 點**，於是分支預測器能對「`LOAD_FAST` 後面常接什麼」「`COMPARE_OP` 後面常接什麼」各自建立預測——bytecode 序列有強規律，命中率大增，實測可省下可觀的直譯開銷。代價是依賴編譯器擴充（`&&label`，非標準 C），所以保留 switch 當可攜 fallback。

**在哪（CPython）**：`Python/ceval_macros.h:73-79` 的 `DISPATCH_GOTO()`、`Python/ceval.c:689` include 的 `opcode_targets.h`（Ch 23）。beacon：`goto *某表[opcode]` 這行 computed goto，加上一張用 `&&label` 建的位址表。

**可遷移到哪 / 三 VM 共通**：這張卡是本課**三台 VM 的最大公約數**：
```
   Lua luaV_execute        SQLite sqlite3VdbeExec       CPython _PyEval_EvalFrameDefault
   computed goto / switch   大 switch                    computed goto（可退 switch）
   （Part 1 Ch 4）          （Part 2 Ch 9）              （Part 5 Ch 23）
```
三台都在解同一個問題（怎麼快速把 opcode 分派到它的實作），選了同族的解法。認出這張卡，你日後看任何 bytecode 直譯器（Java HotSpot 的 template interpreter、WASM runtime…）都能秒懂它的 dispatch 核心。Ch 27 專門把三者並排。

## Pattern 卡 5：穩定 C-API 邊界（區分「對外保證」與「內部細節」）

**一句話**：大型 runtime 用標頭的分層明確劃出「哪些 API 對外穩定保證、哪些是內部實作可隨時變」，讓擴充生態能安全依賴前者、核心能自由重構後者。

**beacon**：
- 標頭分層：穩定公開（`Include/*.h`）／半公開（`Include/cpython/*.h`）／純內部（`Include/internal/pycore_*.h`）。
- 公開 API 是函式（`PyObject_Size`），內部細節是 struct 欄位/slot（`sq_length`）。
- 有「limited API / stable ABI」之類的機制標記哪些跨版本保證。

**為什麼這樣設計**：CPython 有一個龐大的 C 擴充生態（numpy、lxml、無數 binding），它們編譯時連結 CPython 的 C-API。如果每次改內部實作都可能破壞這些擴充，CPython 就寸步難行。解法是**明確劃線**：公開層（`Include/*.h`）對外承諾穩定，擴充可安全依賴；內部層（`internal/pycore_*.h`）不對外保證，核心可隨時重構。這條線讓「生態穩定」和「核心自由演進」兩個矛盾的需求共存——3.11+ 的 frame 攤平、3.13 的 free-threading 這些大改動能發生，正是因為它們動的是內部層。讀碼上的意義：撞到公開 API 邊界，你可以「介面優先」停在合約層（Ch 31 刀三），不必深潛；只有任務逼你進實作才跨線。

**在哪（CPython）**：`Include/` 三層結構（Ch 22 進階）——`PyObject`/`PyNumber_Add` 在公開層，`_PyInterpreterFrame` 在 `internal/pycore_frame.h`（Ch 23）。Ch 25 追 `len` 時走過的「公開 `PyObject_Size` → 內部 `sq_length` slot」正是這條邊界。beacon：標頭路徑帶 `internal/` 或名字帶 `_Py` 前綴（底線開頭）= 內部；乾淨的 `PyXxx_` = 公開。

**可遷移到哪**：
- **git 的 plumbing vs porcelain**（Part 4 Ch 18）：git 也劃「穩定的 plumbing 命令（腳本可依賴）vs 會變的 porcelain（人用的介面）」——同一個「穩定邊界」概念，換到命令列工具語境。
- **任何有插件/擴充生態的系統**：Linux 的 syscall ABI（絕不破壞）vs 內部 API（隨時變）、瀏覽器的 Web API vs 內部 C++。認出「哪條線是對外承諾、哪條是內部自由」，你就知道讀碼時哪些能當穩定合約依賴、哪些下個版本可能消失。
- 讀碼上的用途：追一個功能時，撞到公開 API 邊界可以「介面優先」停在合約層（Ch 31 刀三）；只有任務逼你進實作才跨過邊界深潛。

## 五張卡串起來：一次讀碼裡它們怎麼協同觸發

pattern 卡不是孤立的知識點，讀真 code 時它們會**接連觸發**。想像你第一次打開 `Objects/floatobject.c` 找 `float` 的加法（一個你沒被教過座標的地方），五張卡怎麼一張接一張幫你 chunk：

```
   你打開 floatobject.c，掃過去──
   ①「struct 開頭 PyObject_HEAD」 → 卡1 觸發：這是 boxed 型別，找它的 type struct
   ②「static PyNumberMethods float_as_number = { float_add, ... }」→ 卡1 觸發：
        這是 float 的 vtable，nb_add 填 float_add，跟 int 同構
   ③「float_add 裡有 Py_DECREF / 沒有 tp_traverse」→ 卡2 觸發：
        float 是葉子型別，不參與 cyclic GC，純 refcount
   ④「PyFloat_Type 在哪被公開？Include/floatobject.h」→ 卡5 觸發：
        PyFloat_FromDouble 是公開 API，float_add 是內部 slot
```

你**沒讀一行實作細節**，光靠五張卡的 beacon 就 chunk 出「這是一個 boxed 葉子型別，加法透過 vtable slot `float_add`，公開介面是 `PyFloat_*`」。這就是 pattern 字典的複利：讀第一個 codebase（Lua）你逐行推；讀到 CPython 的 float，你掃過去就認出形狀。**「更會讀」的物理意義就是這個——同樣的 code，你 chunk 得更大、跳過得更多、停留得更準。**

## 如何自己造 pattern 卡（可遷移到本課之外的元技能）

這門課給你六個 codebase 的現成 pattern 卡，但真正的資產是**你能自己造卡**。造一張好卡的四步：

1. **讀到「咦這個形狀我好像見過」時停下**——那個既視感就是 pattern 的訊號。
2. **抽出 beacon**：這個形狀最短的識別線索是什麼？（一個 struct 佈局？一個命名慣例？一種 control flow？）beacon 越短越好，因為它要能在掃讀時觸發。
3. **問「它解什麼問題、取捨是什麼」**：pattern 是「問題 + 一種解法 + 代價」的三元組。只記解法不記問題，下次遇到變體就認不出。
4. **主動找第二個實例**：同一個 pattern 在別的 codebase 長怎樣？找到第二個實例，這張卡才從「CPython 知識」升級成「可遷移 pattern」。

本課每張卡都是這樣造的——beacon（步驟 2）、為什麼這樣設計（步驟 3）、可遷移到哪（步驟 4）。你日後攻任何新 codebase，讀完就用這四步結晶一兩張卡，你的字典就持續複利。這是《The Programmer's Brain》講的「刻意擴充 chunk 庫」的具體操作法。

## 對比與取捨：五張卡在設計空間的位置

| Pattern | CPython 的選擇 | 對照組 | 取捨軸 |
|---|---|---|---|
| 值表示 | boxed object + type slot | Lua tagged union | 一致性/擴充友善 ↔ 輕量/cache 友善 |
| 記憶體管理 | refcount + cyclic GC | Lua 純標記 GC | 即時回收 ↔ 無 refcount 開銷 |
| VM 執行核心 | bytecode DSL 生成 | Lua/SQLite 手寫 | 一致性/可維護 ↔ 直接/無生成複雜度 |
| dispatch | computed goto | SQLite switch | 分支預測友善 ↔ 簡單可攜 |
| API 邊界 | 三層標頭 | git plumbing/porcelain | 生態穩定 ↔ 核心自由重構 |

**重點不是 CPython 都選對**——是每個選擇都在一條取捨軸上，另一個 codebase 可能合理地選另一端。pattern 字典的價值就是讓你看到整條軸，而不是背單一答案。

## 踩雷集錦

1. **錯誤直覺：pattern 卡是「CPython 的知識」。** → 正確認識：pattern 卡的價值在**可遷移**。「boxed object + type slot」不是 CPython 專屬，是你在 nginx module、kernel `file_operations` 都會再認出的通用 idiom。只記 CPython 細節=沒萃取到 pattern。
2. **錯誤直覺：refcount 和 GC 是二選一的競爭方案。** → 正確認識：CPython 兩個都用，分工（refcount 收非環、GC 收環）。把它們放在「記憶體管理策略光譜」上理解（純 refcount ↔ 混合 ↔ 純 GC），比「誰贏」有用得多。
3. **錯誤直覺：computed goto 只是 CPython 的小優化，記不記無所謂。** → 正確認識：它是**三台 VM 共通**的 dispatch 引擎，是本課橫向對照的高光。認出它，你看任何 bytecode 直譯器都能秒懂 dispatch 核心——這正是「一眼認出」的複利。
4. **錯誤直覺：讀完就會了，不用自己寫 pattern 卡。** → 正確認識：pattern 要自己說出來才進長期記憶（本課學習法第 3 點）。合上前四章、憑記憶寫出五張卡的 beacon，寫不出來的就是沒真讀懂。被動讀過 ≠ 主動能認出。
5. **錯誤直覺：C-API 邊界是無聊的工程細節。** → 正確認識：它是「哪些能依賴、哪些會變」的地圖，直接決定你讀碼時停在哪層、依賴什麼。git 的 plumbing/porcelain 是同一個概念——認出邊界是高階讀碼能力。

## 進階：再往深一層

- **記憶體管理光譜的觸發時機細節**：卡 2 的「refcount 主 + GC 補環」還有一個常被忽略的維度——GC**什麼時候跑**。CPython 不是定時跑 GC，而是「配置量驅動」：每配置一個 GC-tracked 物件，第 0 代計數 +1（`Python/gc.c:1819`），超過 threshold（`generations[0].count > threshold`，`gc.c:1820`）才觸發一次收集，熬過收集的物件升代（分代假設：老物件更可能繼續活）。這個「配置驅動 + 分代」的觸發策略是 GC 光譜上的另一個設計旋鈕——你調 `gc.set_threshold()` 就是在調它。認出「GC 觸發是配置量驅動而非時間驅動」，你才懂為什麼某些「產生大量短命物件」的 workload 會頻繁觸發 GC。
- **pattern 之間會互相加強**：CPython 選 stack VM + boxed object，讓 bytecode 好生成（stack 效果好算），這又餵了「DSL 生成」pattern；boxed object 的統一 `PyObject*` 讓 refcount 能通用地掛在每個值上。五張卡不是獨立的，是一套互相支撐的設計決策。讀懂它們的相互作用，比逐張背更深。
- **把卡片接回你既有的字典**：你在 `browser_pwn`（V8）、`kernel_internals`（Linux）已經有一批 pattern。CPython 的五張卡有幾張直接對接（V8 也是 boxed object + hidden class、kernel 也是函式指標 vtable）。主動去連——新 pattern 接到舊 pattern 上，字典才會長成網而非散條目。
- **Ch 27 是這章的放大**：本章卡 1/2/4 都指向「三 VM 對照」。下一章把 Lua/SQLite/CPython 三台 VM 在值表示、dispatch、記憶體管理三軸上完整並排，是 pattern 遷移的高光時刻——本章先埋好卡片，Ch 27 收割對照。

## 本章重點整理

- 五張可遷移 pattern 卡：boxed object + type slot（vtable/protocol）、refcount + cyclic GC 混合、bytecode DSL 生成、computed-goto dispatch、穩定 C-API 邊界。
- 每張卡標 beacon（怎麼一眼認出）／在哪（CPython 座標）／可遷移到哪（本課其他 Part 或通用 C 專案）。
- 三張卡與前面 Part 直接對照：值表示 vs Lua tagged union、記憶體管理 vs Lua 純 GC、dispatch 是三 VM 共通——這些對照教你設計空間而非單一答案。
- pattern 的價值在可遷移，不在 CPython 細節；自己憑記憶寫出 beacon 才算真萃取。

## 自我檢核

- [ ] 合上前四章，你能憑記憶寫出五張卡各自的 beacon（怎麼一眼認出）嗎？
- [ ] 「boxed object + type slot」你能舉出 CPython 以外至少兩個地方（本課或別的 C 專案）嗎？
- [ ] 記憶體管理光譜上，CPython 和 Lua 分別在哪、各賭什麼取捨？
- [ ] computed-goto dispatch 為什麼是「三 VM 共通」的高光？
- [ ] C-API 邊界 pattern 和 git 的 plumbing/porcelain 是不是同一個概念？講出共通點。

## 延伸閱讀

- **[本課 Ch 27 — 三個 VM 橫向對照](./27-three-vms-compared.md)。**
  - **讀哪裡**：整章（值表示 / dispatch / 記憶體管理三軸並排）。
  - **學到什麼**：本章卡 1/2/4 的收割——Lua/SQLite/CPython 三台 VM 完整對照，pattern 遷移的高光。
  - **前提**：讀完三個 Part 的 VM 章（Ch 4、9、23）。
- **[`reading_code` Ch 22 — 讀懂巨集與 metaprogramming](../reading_code/22-reading-macros-metaprogramming.md)。**
  - **讀哪裡**：生成碼與「往上游找 DSL」那節。
  - **學到什麼**：pattern 卡 3（bytecode DSL 生成）的方法論母章；把「認出生成標記」練成反射。
  - **前提**：無。
- **《A Philosophy of Software Design》— John Ousterhout，"Modules Should Be Deep" 與 "Interface vs Implementation" 章。**
  - **讀哪裡**：介面/實作分離、深模組那幾章。
  - **學到什麼**：pattern 卡 5（C-API 邊界）背後的設計原則——為什麼「窄而穩的公開介面 + 自由的內部實作」是好設計，反向強化你對這張卡的理解。
  - **前提**：無。

五張卡進了字典。最後用一個限時攻堅把它們變成肌肉：不看前面四章，自己從 Python 的一個語意追到 C 的 slot 實作，全程外化、計時。

→ [練習 E：追一個 Python 語意到 C](./practice-e-cpython-trace-a-semantic.md)
