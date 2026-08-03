# 練習 E — 追一個 Python 語意到 C

> **目標**：限時把一個 Python 語意從 bytecode 一路追到 C 的型別 slot 實作，全程外化（畫 call chain）、能 build 就用 gdb 驗證。這是 Part 5 的畢業考——把 Ch 22–26 的偵察、eval loop、object model、分而治之、pattern 全部用上，親手走一遍你沒被人標好座標的路。

> **目標codebase**：CPython `v3.13.1`（commit `0671451`）

## 任務

**主任務（建議）**：追 `a + b`（兩個 int）的完整 dispatch——從 `BINARY_OP` opcode，經 `PyNumber_Add`，經型別 slot `nb_add`，到 `int` 的實作 `long_add`。畫出完整 call chain，每一站引真實檔案:行號，能 build 就用 gdb 印出真 stack 佐證。

**替代任務（想換口味）**：追 `len(x)`（x 是 list）——從內建 `len`，經 `PyObject_Size`，經型別 slot `sq_length`，到 `list` 的實作 `list_length`。結構與主任務同構（協定函式 → type slot → 具體型別實作），可換著練。

**交付物**：
1. 一張 ASCII call chain，每站標 `檔案:行號 (v3.13.1)`。
2. 每站一句話：它做什麼、把控制權交給誰、依據什麼決定下一站。
3.（能 build）一段 gdb `bt` 真輸出，證明你的 chain 與執行時一致。
4. 一句話回答：這條 chain 用到了 Part 5 哪幾張 pattern 卡？

## 時限

- **偵察 + 定位入口**：15 分鐘（找到 `BINARY_OP` 的 opcode body 在哪）。
- **追鏈**：25 分鐘（從 opcode body 追到 `long_add`，每站引真 source）。
- **gdb 驗證**：15 分鐘（能 build 的話下中斷點印 stack）。
- **總計**：約 55 分鐘。計時。逼出策略，不要漫遊。

## 開始前

確認你 clone 的是釘死版本、行號才對得上：

```bash
$ cd ~/cbcs/cpython && git rev-parse HEAD
067145177975eadd61a0c907d0d177f7b6a5a3de
```

想用 gdb 驗證就先 build（約幾分鐘）：

```bash
$ ./configure --with-pydebug && make -j"$(nproc)"
$ ./python -c "print(3+4)"     # 印出 7 就 build 成功
7
```

（build 需要 gcc/make，在 Linux/WSL 環境。debug build 帶符號，gdb 追起來最清楚。）

## 先建立攻堅心態：這條路的形狀你已經見過

別把這當成「在 83 萬行大海撈針」。這條 chain 有一個固定形狀，你在 Ch 24/25 見過兩次了：

```
   語言層動作（a+b / len(x)）
     │  eval loop 把它變成一個 opcode 呼叫
     ▼
   泛型協定函式（PyNumber_Add / PyObject_Size）   ← 不做型別分類，只查 slot
     │  查 Py_TYPE(操作對象) 的某個 slot
     ▼
   型別的 slot（nb_add / sq_length）              ← vtable 分派
     │
     ▼
   具體型別的實作（long_add / list_length）        ← 停在這，任務答完
```

**每一站的過渡都有固定的線索**：opcode → 協定函式是「查一張 `_PyEval_BinaryOps` 表」；協定函式 → slot 是「查 `Py_TYPE(v)->tp_as_*->某slot`」；slot → 實作是「型別的 `PyNumberMethods`/`PySequenceMethods` struct 填了哪個函式」。你要做的不是發明路，是**認出這個形狀、在真 source 裡把每一站的座標填實**。這就是 chunking：形狀已知，只補座標。攻堅時腦裡先擺這張圖，你就不會漫遊。

## 如果你卡住了（5 條方向提示，指真實檔案，不直接給答案）

1. **在 `ceval.c` 裡 grep `case BINARY_OP` 找不到 opcode 實作？** 那是對的——回想 Ch 23：3.13 的 opcode 不手寫在 `ceval.c`。實作在 DSL `Python/bytecodes.c`，生成到 `Python/generated_cases.c.h`，被 `ceval.c` include。要找 `BINARY_OP` 的語意去 `bytecodes.c`，找展開後的 C 去 `generated_cases.c.h`。

2. **`BINARY_OP` 怎麼知道要做「加法」而不是「減法」？** opcode 有個 `oparg` 參數。看 `bytecodes.c` 裡 `_BINARY_OP` 的 body——它查一張表 `_PyEval_BinaryOps[oparg]`。那張表定義在哪？`rg "_PyEval_BinaryOps\[\]" Python/ceval.c`。表裡 `[NB_ADD]` 對應哪個函式？

3. **追到 `PyNumber_Add` 之後怎麼走？** 它不做 `if (是int)` 分類。回想 Ch 24：它查型別的 slot。看 `Objects/abstract.c` 的 `PyNumber_Add`——它呼叫 `BINARY_OP1(v, w, NB_SLOT(nb_add), "+")`。`NB_SLOT(nb_add)` 是什麼（提示：`offsetof`）？`binary_op1` 拿這個偏移做什麼？

4. **怎麼從「slot」跳到 `int` 的具體實作？** slot 是 `Py_TYPE(v)->tp_as_number->nb_add`。`int` 的 `tp_as_number` 是哪個 struct？`rg "long_as_number" Objects/longobject.c`——那個 `PyNumberMethods` 的 `nb_add` 欄位填的是哪個函式？

5. **想用 gdb 但不知道在哪下中斷點？** 三個關鍵函式各下一個：`break PyNumber_Add`、`break binary_op1`、`break long_add`。跑 `run` 執行一段 `def f(a,b): return a+b` 然後 `f(3,4)` 的腳本，每個中斷點 `bt` 印 stack。你會親眼看到呼叫 `PyNumber_Add` 的地方在 `generated_cases.c.h`，不在 `ceval.c`。

## 分段步驟

**Step 1 — 定位 opcode body（別在 ceval.c 硬找）**。先用 `dis` 確認 `a+b` 編成哪些 opcode（放進函式才走 `LOAD_FAST`）：

```bash
$ ./python -c "
import dis
def f(a, b): return a + b
dis.dis(f)
"
```

看到 `BINARY_OP 0 (+)`。那個 `0` 就是 `oparg`。接著去 DSL 找它的定義，別在 `ceval.c` 找：

```bash
$ rg -n "op\(_BINARY_OP," Python/bytecodes.c       # DSL 源
$ rg -n "TARGET\(BINARY_OP\) \{" Python/generated_cases.c.h  # 生成後的 C
```

**Step 2 — opcode body → 協定函式**。在 `_BINARY_OP` 的 body（`bytecodes.c:4064`）找到 `res = _PyEval_BinaryOps[oparg](lhs, rhs)`。那張表在哪、`[NB_ADD]` 是誰：

```bash
$ rg -n "_PyEval_BinaryOps\[\]|\[NB_ADD\]" Python/ceval.c
```

你會看到 `[NB_ADD] = PyNumber_Add`。`oparg=0=NB_ADD` → 下一站 `PyNumber_Add`。

**Step 3 — 協定函式 → type slot**。讀 `PyNumber_Add`（`Objects/abstract.c:1139`）→ 它呼叫 `BINARY_OP1(v, w, NB_SLOT(nb_add), "+")`。追 `NB_SLOT`/`NB_BINOP`（`abstract.c:910-912`）與 `binary_op1`（`abstract.c:926`），看清「用 slot 偏移從 `Py_TYPE(v)->tp_as_number` 取函式指標」。這裡是本練習的認知核心——`PyNumber_Add` 完全不知道 `v` 是 int 還是 float，它只查 slot。

**Step 4 — type slot → 具體實作**。`int` 的 `tp_as_number` 是哪個 struct、它的 `nb_add` 填誰：

```bash
$ rg -n "long_as_number|long_add," Objects/longobject.c | head
```

`long_as_number.nb_add = long_add`（`longobject.c:6549`）→ 落到 `long_add`（`longobject.c:3784`）。任務答完。

**Step 5 — gdb 驗證**（能 build）。三個中斷點，`bt` 印 stack，比對你畫的 chain（見下方參考解答的真輸出）。重點看：呼叫 `PyNumber_Add` 的 frame 顯示在 `generated_cases.c.h` 還是 `ceval.c`？

**Step 6 — 收斂**。畫出完整 ASCII chain，標所有 `檔案:行號`，回答用到哪幾張 pattern 卡。把它講給別人（或對空氣）聽一遍——講不順的站就是你沒真懂的站。

---

<details>
<summary>參考解答（先自己做完再看）</summary>

### 完整 call chain

```
  Python:  def f(a, b): return a + b   ;   f(3, 4)
    │
    │  compile.c 編成 bytecode（用 dis 確認）：
    │      LOAD_FAST_LOAD_FAST 1 (a, b)
    │      BINARY_OP           0 (+)      ← oparg=0=NB_ADD
    │      RETURN_VALUE
    ▼
  ① BINARY_OP opcode body
     DSL 定義：Python/bytecodes.c:4064   op(_BINARY_OP, (lhs, rhs -- res))
     生成後：  Python/generated_cases.c.h:101  TARGET(BINARY_OP) { ... }
        核心行（generated_cases.c.h:132）：
            res = _PyEval_BinaryOps[oparg](lhs, rhs);
    │  查表：oparg=NB_ADD
    ▼
  ② _PyEval_BinaryOps[NB_ADD] == PyNumber_Add
     Python/ceval.c:313   const binaryfunc _PyEval_BinaryOps[] = {
     Python/ceval.c:314       [NB_ADD] = PyNumber_Add,
    ▼
  ③ PyNumber_Add(v, w)
     Objects/abstract.c:1139
        呼叫 BINARY_OP1(v, w, NB_SLOT(nb_add), "+")
    │  NB_SLOT(nb_add) = offsetof(PyNumberMethods, nb_add)  （abstract.c:910）
    ▼
  ④ binary_op1(v, w, op_slot=NB_SLOT(nb_add))
     Objects/abstract.c:926
        slotv = NB_BINOP(Py_TYPE(v)->tp_as_number, op_slot)  （abstract.c:934）
        → 從 int 型別的 PyNumberMethods 的 nb_add 偏移取出函式指標
    ▼
  ⑤ int 的 nb_add slot == long_add
     Objects/longobject.c:6548  static PyNumberMethods long_as_number = {
     Objects/longobject.c:6549      (binaryfunc)long_add,   /*nb_add*/
    ▼
  ⑥ long_add(a, b)
     Objects/longobject.c:3784
        CHECK_BINOP(a, b);
        return _PyLong_Add(a, b);   ← 真正的大數加法
```

### 每站一句話

- **① BINARY_OP body**：opcode 的實作不在 `ceval.c` 手寫，而在 DSL `bytecodes.c` 定義、生成到 `generated_cases.c.h`、被 `ceval.c:787` include。核心動作是查 `_PyEval_BinaryOps[oparg]` 表拿到運算函式。依據 `oparg=NB_ADD` 決定下一站是 `PyNumber_Add`。
- **② 查表**：`_PyEval_BinaryOps` 把運算種類（`NB_ADD`/`NB_SUBTRACT`…）映射到 C 函式。`+` = `NB_ADD` → `PyNumber_Add`。
- **③ PyNumber_Add**：不做型別分類，改用 slot 偏移 `NB_SLOT(nb_add)` 呼叫 `binary_op1`。`NB_SLOT` 是 `offsetof`，把「要哪個運算 slot」參數化，讓一個 `binary_op1` 服務所有二元運算。
- **④ binary_op1**：`NB_BINOP(Py_TYPE(v)->tp_as_number, op_slot)` 從 `v` 的型別的 `PyNumberMethods` 表、按偏移取出 `nb_add` 函式指標。這是 object model 的 vtable 分派。
- **⑤ slot 填的是 long_add**：`int` 型別（`PyLong_Type`）的 `tp_as_number` 指向 `long_as_number`，其 `nb_add` 欄位填 `long_add`。
- **⑥ long_add**：`int` 的加法實作，`CHECK_BINOP` 確認兩邊都是 int 後委派給 `_PyLong_Add` 做實際大數運算。

### gdb 驗證（真輸出）

準備腳本 `/tmp/addtest.py`：

```python
def f(a, b):
    return a + b
print(f(3, 4))
```

在三個關鍵函式下中斷點，跑起來印 stack：

```
$ gdb -q ./python \
    -ex "break long_add" \
    -ex "run /tmp/addtest.py" \
    -ex "bt 4"

Breakpoint 1, long_add (a=..., b=...) at Objects/longobject.c:3785
#0  long_add (a=..., b=...) at Objects/longobject.c:3785
#1  binary_op1 (v=..., w=..., op_slot=0, op_name=0x... "+") at Objects/abstract.c:961
#2  PyNumber_Add (v=..., w=...) at Objects/abstract.c:1141
#3  _PyEval_EvalFrameDefault (...) at Python/generated_cases.c.h:132
```

（WSL2 / Python 3.13.1 `--with-pydebug` build 真 gdb 輸出。）

這段 stack 是你畫的 chain 的實體證據，逐行對上：
- `#3 _PyEval_EvalFrameDefault ... at Python/generated_cases.c.h:132`——呼叫 `PyNumber_Add` 的地方**在生成檔 `generated_cases.c.h`，不在 `ceval.c`**。這證明了 Ch 23 的核心論點：eval loop 的 body 是 include 進來的生成碼。
- `#2 PyNumber_Add ... abstract.c:1141` → `#1 binary_op1 ... op_slot=0`——`op_slot=0` 正是 `nb_add` 在 `PyNumberMethods` 裡的偏移（它是第一個欄位，offset 0）。這證明「用 slot 偏移分派」不是教材說法，是 gdb 能印出的真實參數。
- `#0 long_add ... longobject.c:3785`——落到 `int` 的具體實作。

另外印證「小整數是永生單例」：在 `PyNumber_Add` 下中斷點時看 `v`、`w` 的位址，`3` 和 `4` 會顯示成 `<_PyRuntime+...>`（預建的 interned 小整數），不是新配置的物件——呼應 Ch 24 的 immortal objects（PEP 683）。

### 用到的 pattern 卡（Ch 26）

- **卡 1 boxed object + type slot**：整條 chain 的 ③④⑤ 就是「查 `Py_TYPE(v)->tp_as_number->nb_add` slot」的 vtable 分派。
- **卡 3 bytecode DSL 生成**：① 的 opcode body 來自 `bytecodes.c` DSL 生成到 `generated_cases.c.h`，gdb 的 `#3` 位址證實了它。
- **卡 4 computed-goto dispatch**：`BINARY_OP` 是在 eval loop 的 computed-goto 分派下被跳到執行的。
- **卡 5 穩定 C-API 邊界**：`PyNumber_Add` 是公開 C-API（`Include/` 有宣告），`nb_add` slot 是內部實作細節——這條 chain 跨過了公開/內部的邊界。

</details>

---

## 替代任務參考鏈（len(list)）

如果你追的是 `len(x)`，結構同構，參考鏈：

```
  len(x)  →  builtin_len          Python/bltinmodule.c:1763（body 呼叫 PyObject_Size）
          →  PyObject_Size        Objects/abstract.c（查 tp_as_sequence->sq_length）
          →  list 的 sq_length == list_length   Objects/listobject.c:646
                                  （list_as_sequence，listobject.c:3524-3525）
```

同樣是「協定函式（`PyObject_Size`）→ type slot（`sq_length`）→ 具體實作（`list_length`）」，只是換成序列協定——**跟主任務的 `a+b` 是同一個形狀**，只是協定從 `nb_add`（數值）換成 `sq_length`（序列）。這正是本課要你認出的「chunk」：第二次遇到，你不用重新推。

gdb 驗證（`break builtin_len` 後 step 進去，或 `break PyObject_Size`）：

```
$ gdb -q ./python -ex "break builtin_len" -ex "run /tmp/lentest.py" -ex "step" -ex "bt 2"
Breakpoint 1, builtin_len (module=..., obj=...) at Python/bltinmodule.c:1765
#0  PyObject_Size (o=...) at Objects/abstract.c:63
#1  builtin_len (module=..., obj=...) at Python/bltinmodule.c:1768
```

（3.13.1 debug build 真輸出。）`builtin_len:1768` 呼叫 `PyObject_Size`，證實 chain 第一段。`PyObject_Size` 內部再查 `Py_TYPE(o)->tp_as_sequence->sq_length`（對 list 即 `list_length`）完成分派。

**兩個踩雷**：(1) `builtin_len` 附近有 `/*[clinic input]*/`——那是 Argument Clinic 生成標記（Ch 25），別被它騙以為是可略過的註解，也別在生成的 `.c.h` 裡找它的上游。(2) 直接 `break list_length` 你會先撞到**非 len 造成的命中**——`list_length` 同時是 list 的 truthiness（`if lst:` 走 `PyObject_IsTrue` → `sq_length`）用的 slot，直譯器啟動過程就會呼叫它。要乾淨追 `len`，從 `builtin_len` 或 `PyObject_Size` 下手，別從葉子 slot 下手。這本身是一課：**同一個 slot 被多條路徑共用，從葉子下中斷點會混入雜訊，從入口下才乾淨**。

## 為什麼這個練習的形狀值得反覆練

你可能覺得「追一條 call chain」很機械。但這個練習訓練的是大 codebase 工作的核心動作：**從一個高層語意，穿過幾層 indirection，落到真正做事的葉子，全程只讀路徑上的 code、其餘全部無視**。這個動作你日後會做無數次——onboarding 新專案追一個功能怎麼實現、debug 一個 bug 追它從哪冒出來、找漏洞追一個危險輸入流到哪。CPython 的 `a+b` 只是一個乾淨的練習靶。

而且這條 chain 刻意選在有 **indirection + 生成碼 + vtable 分派**三種讀碼陷阱交會的地方：indirection（`_PyEval_BinaryOps` 查表、`eval_frame` 函式指標）、生成碼（`bytecodes.c` → `generated_cases.c.h`）、vtable（`nb_add` slot）。你能乾淨追過這條，代表你能應付這三種陷阱同時出現的真實 code——這比追十條沒陷阱的直路更練功。

## 驗證你的解答

- [ ] 每一站都引了**真實檔案:行號**，不是憑記憶寫的函式名。
- [ ] 你能解釋為什麼在 `ceval.c` grep `case BINARY_OP` 找不到，而該去 `bytecodes.c`/`generated_cases.c.h`。
- [ ] （能 build）你的 gdb `bt` 真輸出裡，呼叫 `PyNumber_Add` 的 frame 顯示在 `generated_cases.c.h` 而非 `ceval.c`。
- [ ] 你能講出 `binary_op1` 的 `op_slot` 參數是什麼（slot 偏移），以及為什麼用偏移而非直接寫 `->nb_add`。
- [ ] 你能把這條 chain 對回 Ch 26 至少三張 pattern 卡。

## 延伸挑戰

1. **追 float 版本**：把腳本改成 `f(3.0, 4.0)`，重跑 gdb。這次 slot 落到哪個函式（提示：`float_as_number.nb_add`，`Objects/floatobject.c:1844-1845`）？畫出這條分岔——同一個 `PyNumber_Add`、同一個 slot 機制，只因 `Py_TYPE(v)` 不同而落到 `float_add`。這最能體現 vtable 多型。真跑驗證（`break float_add`）：

   ```
   Breakpoint 1, float_add (v=..., w=...) at Objects/floatobject.c:590
   #1  binary_op1 (..., op_slot=0, op_name=... "+") at Objects/abstract.c:961
   #2  PyNumber_Add (...) at Objects/abstract.c:1141
   #3  _PyEval_EvalFrameDefault (...) at Python/generated_cases.c.h:132
   ```

   （3.13.1 debug build 真輸出。）注意 `#1`/`#2`/`#3` 跟 int 版**一模一樣**——同一個 `PyNumber_Add`、同一個 `binary_op1`、`op_slot=0`（`nb_add` 偏移不變）。唯一的差別在 `#0`：因為 `Py_TYPE(v)` 是 `float` 不是 `int`，`binary_op1` 從型別表取出的 slot 是 `float_add` 而非 `long_add`。**這就是 vtable 多型的本質**——分派邏輯完全相同，只因資料端的型別指標不同而落到不同實作。這是把 pattern 卡 1 看穿的最好實驗：換型別，前三 frame 不動，只有葉子換。

2. **追混合型別 `f(3, 4.0)`**：`int + float`。`binary_op1` 怎麼處理兩個運算元型別不同的情況（提示：讀 `binary_op1` 裡 `slotv`/`slotw` 那段，它會嘗試兩個型別的 slot，並處理 `PyType_IsSubtype`）？這是 Python `__radd__` 機制的 C 底層。真跑驗證（`break float_add`，`v=3`、`w=4.0`）：

   ```
   Breakpoint 1, float_add (v=<int 3>, w=<float 4.0>) at Objects/floatobject.c:590
   #1  binary_op1 (..., op_slot=0, ...) at Objects/abstract.c:969   ← 注意：969，不是 961
   ```

   關鍵細節：`3 + 4.0` 走到 `float_add`（而非 `long_add`），且 `binary_op1` 的呼叫點是 `abstract.c:969` 而非 int 版的 `961`——因為 `int` 的 `nb_add`（`long_add`）遇到 float 會回 `Py_NotImplemented`，`binary_op1` 於是改試 `w`（float）的 `slotw`。讀 `binary_op1`（`Objects/abstract.c:926` 起）裡 `slotv`/`slotw` 兩個分支怎麼輪流嘗試、`PyType_IsSubtype` 怎麼決定先試誰——這就是 Python `__add__`/`__radd__` 分派規則的 C 底層。追這個你會真正理解「為什麼 `3 + 4.0` 是 float 加法而非 int 加法」。
3. **看特化改寫**：連跑同一個 `BINARY_OP` 很多次（迴圈裡 `a+b`），CPython 的 adaptive specialization 會把它就地改寫成 `BINARY_OP_ADD_INT`（Ch 23 進階）。用 `dis` 的 `adaptive=True`（`dis.dis(f, adaptive=True)` 在跑過幾次後）觀察 opcode 變化，或在 `Python/specialize.c` 的 `_Py_Specialize_BinaryOp` 下中斷點看特化發生。
4. **追一個你沒被教過的語意**：完全自己選一個——`x in lst`（`sq_contains`）、`str(obj)`（`tp_str`）、`-x`（`nb_negative`）、`d[k]`（`mp_subscript`）。從零用本練習的策略追到 slot 實作，全程不看前面各章的座標。這才是真正的畢業考。
5. **觀察永生單例**：在 `PyNumber_Add` 下中斷點跑 `f(3, 4)`，`print v` 看 `3` 的位址——它會顯示成 `<_PyRuntime+...>`（預建的 interned 小整數，Ch 24 的 immortal objects / PEP 683），不是 heap 上新配置的物件。改成 `f(1000, 2000)`（超出小整數快取範圍 -5～256），位址就變成一般 heap 位址。這個對照讓你親眼看到「哪些物件是永生單例、哪些是即時配置」——refcount pattern 的一個關鍵優化的實證。

## 自我檢核

- [ ] 我計時了，知道自己在哪一步卡最久（那就是我最該補的讀碼技能）。
- [ ] 我全程外化——畫了 call chain、每站標了檔案:行號，不是只在腦裡追。
- [ ] 我沒有在 `ceval.c` 裡找 opcode 實作找到崩潰，而是第一時間想到去 `bytecodes.c`/`generated_cases.c.h`。
- [ ] （能 build）我用 gdb 驗證了假設，而不是只信教材的 chain。
- [ ] 我能把這條 chain 講給別人聽（費曼測試），並指出它用到哪幾張 pattern 卡。

追完這條 chain，你把 Part 5 的偵察、eval loop、object model、分而治之、pattern 全部串成了一次真實攻堅。接下來 Part 6 會把三台 VM（Lua/SQLite/CPython）並排對照，讓你親眼看見 pattern 遷移的複利——同一組 dispatch / 值表示 / 記憶體管理的取捨，在三個 codebase 上如何各自展開。

→ [Ch 27 三個 VM 橫向對照：pattern 遷移的高光](./27-three-vms-compared.md)
