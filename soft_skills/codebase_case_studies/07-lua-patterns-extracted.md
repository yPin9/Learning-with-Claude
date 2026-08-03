# Ch 7 — 從 Lua 萃取的可遷移 pattern

> **目標**：Part 1 的收斂章。把 Ch 3–6 讀 Lua 讀到的可遷移設計 idiom，結晶成五張 pattern 卡片（照 Ch 2 的四欄格式）。每張卡片標好「怎麼一眼認出（beacon）／在 Lua 哪裡見過（真檔案:真函式）／可遷移到哪」。這些卡片是你 pattern 字典的第一批存款，後面讀 SQLite（Part 2）、CPython（Part 5）時會一張張被觸發、被驗證。Ch 27 三個 VM 橫向對照、Ch 30 總字典，都靠這裡先鋪好遷移連結。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼需要這個？

Ch 1 講過:讀碼變快靠 pattern 辨識(chunking),不是靠讀更用力。Ch 2 給了訓練協定:限時攻堅 → 深挖 → **萃取 pattern 卡片**。前四章你做了前兩步——現在做第三步,也是最容易被跳過、卻決定「三週後還記不記得」的一步。

**萃取的價值在「可遷移」那一欄**。你在 Lua 讀懂的「bytecode dispatch loop」,到 SQLite 的 VDBE、CPython 的 ceval 會**再遇到同一個骨架**。如果你只把它記成「Lua 的某個 for 迴圈」,下個 codebase 你得從頭讀;如果你把它結晶成一個帶 beacon 的 pattern,下次一眼就認出「啊這是 dispatch loop」,直接 chunk。這章就是把 Lua 這五章的收穫,轉成日後能「一眼認出」的形式。

用法(Ch 2 已交代):**理想上你該先合上 Ch 3–6,自己憑記憶擠出卡片,再來對照本章**。自己擠出來的才進長期記憶,抄本章的不會。本章是你的對照答案,不是抄寫範本。

## 先建立直覺:Lua 給了你五個 idiom

Ch 3–6 讀的東西可以收成五個可遷移設計。它們不是 Lua 獨有,是「語言 runtime 這個領域」的通用解法,Lua 只是把它們寫得最乾淨:

```
   ┌──────────────────────────────────────────────────────────┐
   │ 1. bytecode VM dispatch  ── 怎麼執行指令流（Ch 4）         │
   │ 2. tagged union 值表示    ── 怎麼裝動態型別的值（Ch 5）     │
   │ 3. 混合資料結構(array+hash)── 一個結構兼兩用（Ch 5）        │
   │ 4. 三色增量 GC           ── 怎麼無停頓自動回收（Ch 6）      │
   │ 5. stack-based C API 邊界 ── C 與腳本怎麼溝通（Ch 3/5）     │
   └──────────────────────────────────────────────────────────┘
        全部都會在後面的 codebase 以某種變體再出現
```

一張張過。

## Pattern 卡片 1：bytecode VM 的 dispatch loop

```
┌─────────────────────────────────────────────────────────────┐
│ Pattern 名  ： bytecode VM 的 opcode dispatch loop            │
├─────────────────────────────────────────────────────────────┤
│ 長怎樣的 beacon：                                             │
│   一個大 for(;;)，裡面：fetch 一條指令 → 解出 opcode → 依     │
│   opcode 跳到對應處理 → 改狀態 → 取下一條。信號：巨大 switch  │
│   或 computed goto 表（goto *tab[op]）；vmcase/vmdispatch 這   │
│   類巨集包裝；指令操作 R(A)/R(B)/R(C) 這種運算元欄位           │
├─────────────────────────────────────────────────────────────┤
│ 在哪個 codebase 見過：                                        │
│   Lua v5.4.7 lvm.c:luaV_execute（1151 行起）                  │
│   dispatch 用 vmdispatch/vmcase/vmbreak 三巨集抽象；          │
│   gcc/clang 上 ljumptab.h 把它重定義成 computed goto           │
├─────────────────────────────────────────────────────────────┤
│ 可遷移到哪：                                                  │
│   SQLite VDBE（Ch 9，sqlite3VdbeExec，也是巨大 dispatch）      │
│   CPython ceval.c（Ch 23，_PyEval_EvalFrameDefault，第三個 VM）│
│   任何 bytecode 直譯器 / regex 引擎 / 狀態機驅動系統          │
└─────────────────────────────────────────────────────────────┘
```

**這是本課最重要的 pattern**,因為它在六個目標裡出現三次(三個 VM)。認出它的 beacon:一個永遠不會自然結束的大迴圈 + 一條指令的取指 + 依 opcode 分派。變體差異:Lua 是 register-based(指令帶 R(A) 定址 slot)、CPython 是 stack-based(指令操作運算元堆疊)、SQLite VDBE 介於中間(暫存器式但為 SQL 特化)。dispatch 實作也有變體:`switch` vs computed goto。認出骨架後,你要問的問題只剩「這台是 register 還是 stack?dispatch 是 switch 還是 goto?」——而不是從頭理解一個 2000 行的函式。**Ch 27 就是把三張這個 pattern 的卡片並排引爆的地方**。

## Pattern 卡片 2:tagged union 值表示

```
┌─────────────────────────────────────────────────────────────┐
│ Pattern 名  ： tagged union（值 + 型別標籤）                  │
├─────────────────────────────────────────────────────────────┤
│ 長怎樣的 beacon：                                             │
│   一個 struct = 一個 union（能裝多種型別）+ 一個小 tag 欄位。 │
│   到處是「先看 tag、再 switch 決定怎麼解讀 union」的 code。    │
│   信號：ttype/ttypetag 這類「取型別」巨集；一個值能是整數/   │
│   指標/浮點看 tag；型別常數如 LUA_T*/PyXxx_Type                │
├─────────────────────────────────────────────────────────────┤
│ 在哪個 codebase 見過：                                        │
│   Lua v5.4.7 lobject.h:TValue = Value(union) + tt_(1 byte)    │
│   tt_ 一 byte 塞三層：基本型別/變體/可回收 bit                 │
├─────────────────────────────────────────────────────────────┤
│ 可遷移到哪：                                                  │
│   CPython PyObject（Ch 24，但改用 heap 物件 + ob_type 指標）  │
│   SQLite 的 Mem/sqlite3_value（欄位值也是 tagged）             │
│   任何動態型別 runtime / 序列化格式 / variant 型別（C++ variant）│
└─────────────────────────────────────────────────────────────┘
```

動態型別的核心解法。beacon:union + tag,加上滿地的「看 tag 再 switch」。遷移時注意變體:Lua 把小值(整數/浮點)**直接存在 union**(不進堆),CPython 把**所有值都裝箱**成 heap 上的 `PyObject`(頭部帶型別指標和 refcount)。同一個 pattern,兩種取捨——Lua 省堆配置、CPython 求一致性。這個對比在 Ch 24 讀 CPython object model 時是核心。

## Pattern 卡片 3:混合資料結構(array + hash)

```
┌─────────────────────────────────────────────────────────────┐
│ Pattern 名  ： 混合資料結構（一個結構、兩種儲存策略）          │
├─────────────────────────────────────────────────────────────┤
│ 長怎樣的 beacon：                                             │
│   一個容器 struct 同時有兩個(以上)儲存區，存取時先判斷「這筆  │
│   走哪塊」。信號：struct 裡同時有 array 指標 + hash 節點指標； │
│   getter 開頭 if (在陣列範圍) 走陣列 else 走雜湊；有 rehash/  │
│   resize 邏輯在兩塊間重新分配                                  │
├─────────────────────────────────────────────────────────────┤
│ 在哪個 codebase 見過：                                        │
│   Lua v5.4.7 ltable.c:Table = array part + hash part          │
│   luaH_getint 先試 array[key-1]，落空才走 hash 的碰撞鏈；      │
│   rehash 用 2 次方直方圖 computesizes 決定兩塊大小             │
├─────────────────────────────────────────────────────────────┤
│ 可遷移到哪：                                                  │
│   多數語言的字串/小整數優化（small-string、small-int cache）  │
│   PostgreSQL/SQLite 的頁面內混合佈局                          │
│   任何「常見情況走快路徑、罕見情況走通用路徑」的容器設計       │
└─────────────────────────────────────────────────────────────┘
```

這張卡片更廣的形式是「**快路徑 + 慢路徑雙軌**」:對最常見的用法(連續整數 key)走一條無開銷的快路徑(直接陣列索引),罕見用法走通用但較貴的慢路徑(雜湊)。你會在無數地方看到這個形狀——Lua VM 的 `OP_GETTABLE` 本身也有 fast path(`luaV_fastget`)。認出 beacon:「一個容器有兩塊儲存 + getter 開頭先判斷走哪塊」。

## Pattern 卡片 4:三色增量 GC + write barrier

```
┌─────────────────────────────────────────────────────────────┐
│ Pattern 名  ： 三色標記增量 GC（配 write barrier 維持不變式）  │
├─────────────────────────────────────────────────────────────┤
│ 長怎樣的 beacon：                                             │
│   物件頭有 marked/color 位元（白灰黑）；一個狀態機 GCS* 分     │
│   propagate/atomic/sweep 階段；一個 single-step 函式每次只推   │
│   進一點；到處是 barrier 巨集在「寫引用」時被呼叫。信號：      │
│   iswhite/isblack/gray 佇列、gcstate switch、luaC_barrier      │
├─────────────────────────────────────────────────────────────┤
│ 在哪個 codebase 見過：                                        │
│   Lua v5.4.7 lgc.c:singlestep（狀態機）、propagatemark（推進） │
│   luaC_barrier_（黑指白時 reallymarkobject 補救維持不變式）    │
│   由 GCdebt 記帳、配物件時 checkGC/luaC_condGC 觸發，無背景執行緒│
├─────────────────────────────────────────────────────────────┤
│ 可遷移到哪：                                                  │
│   CPython 的 cyclic GC（Ch 24，但主用 refcount，標記只解循環） │
│   Go / JVM(G1,ZGC) / V8 的並發 GC（同三色 + barrier，更並發）  │
│   任何「一邊掃一邊改」的增量/並發演算法（不只 GC）             │
└─────────────────────────────────────────────────────────────┘
```

Part 1 的難章,也是最有遷移價值的 pattern 之一。核心 chunk 是「**三色不變式 + write barrier**」這個組合——凡是「掃描一張會被同時修改的圖」的演算法,都需要某種 barrier 維持不變式。遷移注意變體:Lua 是「交錯但不並行」(GC 和程式同執行緒輪流),Go 是真並發(barrier 更複雜)、CPython 主用 refcount(Ch 24 對照,兩種完全不同的哲學)。認出 GC pattern 的 beacon:物件頭有 color 位元 + 狀態機 + barrier 巨集。

## Pattern 卡片 5:stack-based C API 邊界

```
┌─────────────────────────────────────────────────────────────┐
│ Pattern 名  ： stack-based 語言嵌入邊界（host C ↔ 腳本）       │
├─────────────────────────────────────────────────────────────┤
│ 長怎樣的 beacon：                                             │
│   host 和 runtime 之間所有值都經過一個虛擬 stack：push 參數上  │
│   去、call、pop 結果。API 函式收「stack 索引」而非直接值。     │
│   信號：lua_push*/lua_to*/lua_pcall 這類；引數用 index(-1,1)   │
│   定位；沒有「直接回傳 Lua 值給 C」的介面，一切經 stack        │
├─────────────────────────────────────────────────────────────┤
│ 在哪個 codebase 見過：                                        │
│   Lua v5.4.7 lapi.c（lua_* 對外 API）、lua.c:main 的           │
│   luaL_newstate → lua_push* → lua_pcall → lua_close 慣用法      │
├─────────────────────────────────────────────────────────────┤
│ 可遷移到哪：                                                  │
│   CPython C-API（Ch 26，但用 PyObject* 直傳 + 手動 refcount）  │
│   任何嵌入式腳本引擎 / FFI 邊界 / VM 的 host 介面設計          │
└─────────────────────────────────────────────────────────────┘
```

這張是「兩個世界怎麼安全溝通」的 pattern。Lua 選 stack-based:C 不直接碰 Lua 的 heap 物件(那會讓 GC 難管),而是透過一個虛擬 stack 間接操作,GC 知道 stack 上的東西都活著。對照 CPython 選 `PyObject*` 直傳 + 手動 refcount(`Py_INCREF`/`Py_DECREF`),把生命週期管理的責任丟給 C 程式設計師——這是兩種嵌入邊界哲學,Ch 26 會對照。認出 beacon:API 收 stack 索引而非值、一切經 push/pop。

## 底層機制:這五張卡片怎麼互相咬合

它們不是五個孤立知識點,是一個語言 runtime 的五個協作部件。把 Ch 4–6 的機制串成一張「一段 Lua code 從執行到回收」的全景,五張卡片各就各位:

```
   Lua 原始碼
      │ （前端，本課不讀）
      ▼  產出 bytecode
   ┌──────────────────────────────────────────────┐
   │ [卡1] dispatch loop：luaV_execute 一條條跑指令 │
   │        指令操作的值 → [卡2] tagged union TValue │
   │        指令 OP_GETTABLE/SETTABLE 存取 →         │
   │              [卡3] 混合 table（array+hash）      │
   │        指令配置新物件 → 欠 GC step →             │
   │              [卡4] 三色增量 GC（barrier 在寫時攔）│
   └──────────────────────────────────────────────┘
      ▲
      │ host C 程式透過 [卡5] stack-based API 驅動這一切
   宿主（lua.c 或任何嵌入 Lua 的 C 程式）
```

**每張卡片是一個部件,合起來是一台完整的 runtime**。這就是為什麼 Lua 適合當第一個目標:它小到你能把五個部件全讀完、看清它們怎麼咬合。之後的 SQLite/CPython 太大,你只能挑部件讀,但因為在 Lua 見過完整的協作,你知道那些部件在整體裡的位置。

## 對比與取捨:同一 pattern 在不同 codebase 的變體

| Pattern | Lua 的做法 | 預期在其他 codebase 的變體 |
|---|---|---|
| dispatch loop | register-based,computed goto | SQLite:暫存器式為 SQL 特化;CPython:stack-based |
| tagged union | 小值直接存 union,不進堆 | CPython:全裝箱成 heap PyObject |
| 混合結構 | table = array + hash | 各語言的 small-x 優化、DB 頁面佈局 |
| 增量 GC | 三色標記,同執行緒交錯 | CPython:refcount 為主;Go:並發三色 |
| C API 邊界 | stack-based,GC 友善 | CPython:PyObject* + 手動 refcount |

這張表本身就是 Ch 27/Ch 30 的預告:同一欄的不同做法,就是 pattern 遷移時你要調整的參數。**認出 pattern 是一眼、辨明變體是一秒**——這比從頭讀快一個數量級。

## 踩雷集錦

1. **抄本章的卡片當作自己萃取了**。Ch 2 講得很白:自己憑記憶擠出來的卡片才進 LTM,抄的不會。正確用法是先合上 Ch 3–6 自己寫,再用本章對照補漏。跳過「自己撞」這步,卡片對你就只是幾段文字,不是可提取的 chunk。
2. **pattern 名取太粗**。取「VM」不如取「register-based VM 的 dispatch loop」。名字是 LTM 的索引 key,越精準,日後越容易被 beacon 觸發。粗名字提取不出來,等於沒存。
3. **「可遷移到哪」欄空著**。這欄是卡片的靈魂,也是最多人偷懶不填的。就算你還沒讀 SQLite/CPython,也要**預測**「這個 pattern 應該會在別的 VM 出現」。這個預測是遷移連結的種子,等 Part 2 真讀到 VDBE「被我料中」,chunk 就遷移完成了。空著 = 浪費預建連結。
4. **以為認出 pattern 就等於讀懂了**。pattern 卡片檢驗「你認得出形狀」,但機制細節要靠費曼複述(Ch 2 產出物二)檢驗。認出「這是三色 GC」很快,但講不清「barrier 為什麼只在黑指白時動作」就是沒真懂。兩者都要做。
5. **把變體差異當成「Lua 特有,別處用不上」**。恰恰相反,變體差異正是遷移的重點。你在 Lua 學到「tagged union 可以把小值存進 union」,到 CPython 看到「它反而全裝箱」,這個對比讓你更深理解「為什麼有人選這樣、有人選那樣」——這才是專家級的 pattern 知識,不只是認形狀。

## 進階:再往深一層

- **把這五張卡片存成一個檔案,當 spaced repetition 材料**。Ch 2 提過 chunk 要間隔複習才固化。把卡片丟進一個筆記檔(或 Anki),每隔幾天重看「beacon 長怎樣」那欄、試著回想在哪見過。Ch 30 會把六個 Part 的卡片收斂成總字典,那就是你整門課的複習卡組。現在先養成存卡片的習慣。
- **主動去找第六張卡片**。本章給了五張,但 Ch 3–6 還藏著別的可遷移 idiom:字串駐留(interning,`lstring.c`)、共同表頭 + tag 分派做多型(`CommonHeader` + `gco2t`)、記帳式節流(`GCdebt` 觸發 GC)。試著自己也做成卡片——主動萃取比被動接收更能練出「一眼認出」的肌肉。
- **預先為 Part 2 下注**。合上本章前,寫下你對 SQLite 的三個預測:「它的 VDBE dispatch loop 會是 switch 還是 computed goto?」「它的欄位值是 tagged union 嗎?」「它有 GC 嗎(提示:SQLite 沒有 GC,它用不同的記憶體管理)?」帶著預測去讀 Part 2,對錯都是學習——料中強化遷移,料錯暴露你的盲點。

## 本章重點整理

- Part 1 從 Lua 萃取五張 pattern 卡片:**bytecode VM dispatch / tagged union 值表示 / 混合資料結構(array+hash)/ 三色增量 GC + barrier / stack-based C API 邊界**。
- 卡片格式固定四欄(Ch 2):**pattern 名(精準)/ beacon(怎麼一眼認出)/ 在哪見過(真檔案:真函式)/ 可遷移到哪(預建連結)**。
- 五張卡片不是孤立知識,是一台 runtime 的五個協作部件:host 透過 stack API 驅動 dispatch loop,loop 操作 tagged union 值、存取混合 table、配物件觸發增量 GC。
- 「可遷移到哪」是靈魂欄:它預先鋪好連結,讓你 Part 2 讀 SQLite VDBE、Part 5 讀 CPython 時「一眼認出同一 pattern」,而非從頭學。Ch 27 三個 VM 對照、Ch 30 總字典在此引爆。
- 認出 pattern 是一眼、辨明變體是一秒:同一 pattern 在不同 codebase 有變體(register vs stack VM、值存 union vs 裝箱、GC 交錯 vs 並發),變體差異正是遷移時要調的參數。
- 用法紀律:**先自己憑記憶擠卡片,再對照本章**。抄的不進長期記憶。

## 自我檢核

- [ ] 我能不看本章,憑記憶說出 Lua 五張 pattern 卡片的名字
- [ ] 每張卡片我都能講出至少一個 beacon(什麼形狀讓我一眼認出它)
- [ ] 每張卡片我都填得出「可遷移到哪」,至少一個後面 Part 的預測
- [ ] 我能畫出五個 pattern 怎麼咬合成一台完整 runtime(host→dispatch→值→table→GC)
- [ ] 我對 dispatch loop / tagged union / GC 三張卡片,各能說出 Lua 版和預期的 CPython 版差在哪
- [ ] 我把卡片存成了檔案,準備日後間隔複習(不是讀完就丟)

## 延伸閱讀

- **`soft_skills/codebase_case_studies` Ch 2「訓練協定」與 Ch 30「你的 pattern 字典」**（本 repo）
  - **讀哪裡**:Ch 2 的「產出物一:pattern 卡片」複習格式與用法;Ch 30(尚未讀到)是本章五張卡片的最終歸宿——六個 Part 的卡片收斂成一張總字典。回頭看 Ch 2、預覽 Ch 30,理解這章在整門課的位置。
  - **前提**:讀過本章。
- **《The Programmer's Brain》— Ch 1–3（chunking / beacon / LTM）**（Manning, 2021）
  - **讀哪裡**:第 3 章講 chunk 怎麼進長期記憶、beacon 怎麼觸發提取。本章的五張卡片就是在刻意製造可被 beacon 觸發的 chunk,讀這幾章理解背後的認知科學。
  - **前提**:無。
- **[The Architecture of Open Source Applications](https://aosabook.org/)**（線上書系）
  - **讀哪裡**:找裡面關於 VM / 直譯器 / GC 的章節,看其他作者怎麼描述同類 pattern。用不同人的講法交叉驗證你的卡片,能發現自己漏掉的 beacon。
  - **前提**:讀過本章,有自己的卡片可對照。

五張卡片入袋,Part 1 的理解該用一次限時攻堅來檢驗。下一份是練習 A:給你一個明確的 Lua 執行路徑,計時追到底,把這五章的機制在一條真實 call chain 上串起來。

→ [練習 A：限時攻堅一條 Lua 執行路徑](./practice-a-lua-trace-a-path.md)
