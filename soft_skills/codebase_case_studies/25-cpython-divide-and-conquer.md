# Ch 25 — 大型專案的分而治之實戰

> **目標**：把 `reading_code` Ch 31 的六把刀，用在 CPython 這個真·大專案上。前三章我替你標好了 `a + b` 的座標；這章換你當主角：面對 83 萬行、你沒讀過的語意（我們拿 `len(x)` 當靶），用系統化策略自己從零定位。重點不是又追一條鏈，是**遷移一套面對超大 codebase 的攻堅姿態**——怎麼決定不讀什麼、用 test 當入口、用生成標記與 Argument Clinic 定位、跟著一個內部 API 邊界走。

> **目標codebase**：CPython `v3.13.1`（commit `0671451`）

## 為什麼需要這個？

Ch 22–24 有個隱藏的作弊：**我事先知道路，替你標好每一站的座標**。真實世界沒有這種好事。你被指派「查 CPython 的 `len()` 對自訂型別怎麼運作」，面對的是 83 萬行、沒人幫你標座標的荒野。

`reading_code` Ch 31 給了六把刀（任務裁剪、子系統隔離、介面優先、按需深潛、build target 切範圍、主動無視），並在 redis（14 萬行）上示範把範圍砍到 59 行。CPython 是 redis 的 6 倍大，而且多了兩層 redis 沒有的複雜性：**生成碼**（`bytecodes.c`）和 **Argument Clinic**（另一套生成器）。這章示範同一套刀在這個尺度、這些額外複雜性下怎麼用——並且**由你操刀**，我只在旁邊講解每一刀砍在哪。

## 先建立直覺：CPython 是城市，你要辦一件具體的事

Ch 31 的城市比喻在 CPython 上更貼切。83 萬行 C、439 個 `.c`、98 個 `Modules/`、12 萬行 `Lib/`——你不可能逛完，也不該逛完。你今天只辦一件事：

> **任務**：「`len(x)` 對一個自訂 class（有 `__len__`）是怎麼運作的？從 Python 的 `len` 呼叫到 C 的 slot。」

任務越具體，裁剪越狠。「理解 CPython 的內建函式」→ 無限；「`len` 怎麼從呼叫走到 `__len__`」→ 收斂到兩三個檔案裡的三段。這就是刀一（任務是最鋒利的裁剪器）。

```
   壞任務：「理解 CPython」          好任務：「len(x) 怎麼走到 __len__」
   ┌────────────────────┐          ┌──────────────────────────────┐
   │ 範圍無限，逛一年    │          │ builtin_len → PyObject_Size    │
   │ 在 Modules/ 淹死    │          │   → tp_as_sequence->sq_length  │
   └────────────────────┘          │ 三站，兩三個檔                 │
                                    └──────────────────────────────┘
```

## 刀一 + 刀二：任務裁剪 + 子系統隔離——先砍掉 99%

任務「`len` 怎麼運作」屬於「內建函式 + object protocol」這個子系統。立刻對 CPython 的目錄做主動無視分類（刀六），全憑檔名 + Ch 22 建立的先驗：

```
Modules/  (98 個 .c，各種擴充模組)        → len 是核心內建，不在擴充模組 → 無視
Parser/   (tokenizer/PEG parser)          → len 的語法早解析完了 → 無視
Lib/      (12 萬行純 Python 標準庫)        → len 是 C 內建，不在 .py → 無視
Python/gc.c, import.c, compile.c ...       → 跟「len 怎麼算」無關 → 無視
Python/bltinmodule.c                       → 內建函式的家 → 相關！
Objects/abstract.c                         → object protocol（PyObject_*）→ 相關！
Objects/listobject.c / 你的型別            → 具體型別的 sq_length → 按需
```

**你一個檔案都還沒打開，就砍掉了 630 多個 `.c` 和 12 萬行 Python。** 這不是偷懶，是有依據的快速分類——`len` 是內建函式（→ `bltinmodule.c`）、操作任意物件（→ object protocol 在 `abstract.c`）。剩下要碰的是個位數檔案。

## 刀三：介面優先——用 test 和 `dis` 當入口，不讀實作

面對陌生語意，最快的入口常常不是 source，是**它的測試**或**它的介面**。CPython 的 `Lib/test/` 是巨大的可執行文件——想知道 `len` 的行為邊界（對什麼型別有效、錯誤時丟什麼），讀 `Lib/test/test_len` 相關測試比讀實作快。這是刀三「介面優先」：test 描述「做什麼」，你還沒碰「怎麼做」。

看 `Lib/test/test_builtin.py` 的 `test_len` 這段真 code——它就是 `len` 行為的可執行規格：

```python
    def test_len(self):
        self.assertEqual(len('123'), 3)
        self.assertEqual(len([1, 2, 3, 4]), 4)
        self.assertEqual(len({'a':1, 'b': 2}), 2)
        class BadSeq:
            def __len__(self):
                raise ValueError
        self.assertRaises(ValueError, len, BadSeq())
        class InvalidLen:
            def __len__(self):
                return None
        self.assertRaises(TypeError, len, InvalidLen())
        class NegativeLen:
            def __len__(self):
                return -10
        self.assertRaises(ValueError, len, NegativeLen())
```
（`Lib/test/test_builtin.py:1142-1163`，v3.13.1，節錄）

**這段 test 一次告訴你 `len` 的所有行為邊界**，你一行實作都沒讀：它接受序列/映射（`str`/`list`/`dict`）、走 `__len__`、`__len__` 回非整數 → `TypeError`、回負數 → `ValueError`、回超大值 → `OverflowError`。這些正是你追實作時該預期在 C 裡看到的檢查。**先讀 test 建立「應該有哪些檢查」的假設**（`reading_code` Ch 10 假設驅動），再去 source 驗證，比裸讀實作快得多——這是介面優先（刀三）在有優質測試的專案上的最強形態。

另一個介面級入口：直接問直譯器。你不確定 `len(x)` 底層呼叫什麼 C 函式？用 `ctypes` 或直接看 `bltinmodule` 的 export。但最省力的是**用你 Ch 22 學的招**——CPython 的內建函式幾乎都在 `Python/bltinmodule.c`，一個 grep 定位（導航，不是漫遊）：

```
$ rg -n "builtin_len|len as builtin_len" Python/bltinmodule.c
1754:len as builtin_len
1763:builtin_len(PyObject *module, PyObject *obj)
```

命中 `Python/bltinmodule.c:1763`。**一個 grep，從 83 萬行導航到一個函式。** 這就是刀一裁剪 + 導航的威力：你不是「讀 `bltinmodule.c`」（它幾千行），是「跳到 `builtin_len` 那一個函式」。

## 踩雷即教材：Argument Clinic——第二個生成陷阱

打開 `builtin_len` 附近，你會先撞到一段看起來不是 C 的東西：

```c
/*[clinic input]
len as builtin_len

    obj: object
    /

Return the number of items in a container.
[clinic start generated code]*/

static PyObject *
builtin_len(PyObject *module, PyObject *obj)
/*[clinic end generated code: output=fa7a270d314dfb6c input=bc55598da9e9c9b5]*/
{
    Py_ssize_t res;
    res = PyObject_Size(obj);
    ...
}
```
（`Python/bltinmodule.c:1754-1775`，v3.13.1，節錄）

`/*[clinic input] ... [clinic start generated code]*/` 是 **Argument Clinic**——CPython 的另一套 DSL 生成器（跟 Ch 23 的 `bytecodes.c` 是不同的生成系統）。它從那段宣告生成「參數解析樣板碼」（放在同名 `.c.h` 檔裡），讓你不用手寫繁瑣的 `PyArg_Parse*`。

**這是 Ch 23 陷阱的近親**：CPython 有**不只一套**生成系統。Ch 23 的 `bytecodes.c` 生成 eval loop；Argument Clinic 生成參數解析。讀 CPython 你會反覆遇到「這段 code 是生成的、上游在別處」。`reading_code` Ch 22 的通則再說一次：**看到 `generated code` / `clinic` / `Do not edit` 標記，就知道你在讀葉子，要改要理解得往上游找 DSL 宣告**。對讀懂 `len` 而言，Clinic 生成的參數解析不重要（`obj` 就是那個物件），你的視線直接落在函式 body 的 `res = PyObject_Size(obj);`——這是刀四（按需深潛）：Clinic 樣板不深潛，直接跳到真正做事的那行。

## 刀四：按需深潛——只追命中任務的那一站

`builtin_len` 的 body 就一行核心：`res = PyObject_Size(obj)`。跟著它進 `abstract.c`（object protocol 的家）：

```c
Py_ssize_t
PyObject_Size(PyObject *o)
{
    ...
    PySequenceMethods *m = Py_TYPE(o)->tp_as_sequence;
    if (m && m->sq_length) {
        Py_ssize_t len = m->sq_length(o);
        assert(_Py_CheckSlotResult(o, "__len__", len >= 0));
        return len;
    }
    return PyMapping_Size(o);
}
```
（`Objects/abstract.c`，v3.13.1，節錄）

**這裡你認出了 Ch 24 的 pattern**——又是查 slot：`Py_TYPE(o)->tp_as_sequence->sq_length`。`len` 對序列走 `sq_length` slot，對映射（dict）走 `mp_length`（`PyMapping_Size` 裡）。這是 chunking 生效的瞬間：你在 `a + b` 學過「協定函式 → type slot」，這裡一眼認出同一個形狀，不用從頭推。**這就是這門課的目的**——第二次遇到就快。

到這裡任務其實答完了大半：`len(x)` → `builtin_len` → `PyObject_Size` → `Py_TYPE(x)->tp_as_sequence->sq_length`。對「有 `__len__` 的自訂 class」，那個 slot 是 CPython 建型別時從 `__len__` 包出來的 wrapper（Ch 24「進階」的 `slotdefs` 那條線）。對 list，slot 是 `list_length`：

```
$ rg -n "list_length|list_as_sequence" Objects/listobject.c
646:list_length(PyObject *a)
3524:static PySequenceMethods list_as_sequence = {
3525:    list_length,                                /* sq_length */
```

`list_length`（`Objects/listobject.c:646`）就是 `list` 的 `sq_length`。**你停在這**——`list_length` 內部怎麼讀 `ob_size` 是實作細節，任務沒逼你深潛，主動無視（刀六）。

## 刀五 + git 考古：跟著一個內部 API 邊界走

任務答完了，但真實工作常有下一問：「這個 slot 機制什麼時候引入的、為什麼這樣設計？」這時 build target 與 git 考古上場。

**用 git 考古定位一個決策**（`reading_code` Ch 17）：你想知道 `PyObject_Size` 為什麼分 `sq_length`/`mp_length` 兩條路，`git log -S "sq_length"` 或 `git blame Objects/abstract.c` 對那幾行，能挖出引入它的 commit 和 PR 討論。CPython 的 commit message 常帶 `bpo-`/`gh-` issue 編號，順藤摸到設計討論。（釘死 tag 是 `--depth 1` clone，考古需要歷史時 `git fetch --unshallow` 補抓——這是 Ch 0 埋的伏筆。）

**跟著內部 API 邊界走**：`PyObject_Size` 是一個公開 C-API（`Include/` 有宣告），`sq_length` 是型別實作的內部合約。這條「公開 API → 內部 slot」的邊界正是 Ch 26 要萃取的「穩定 C-API 邊界」pattern。你追 `len` 時無意間走過了這條邊界——公開的 `PyObject_Size` 對外保證穩定，底下的 slot 分派是內部細節。認出這條邊界，你就知道「哪些能依賴、哪些會變」。

## 安全帶：反查呼叫者，評估局部改動的漣漪

任務驅動法讓你只讀一小塊，但它有個盲點（Ch 31 反覆警告）：**你讀的那塊有你沒看到的呼叫者依賴它的行為**。假設你的任務不是「理解 len」而是「改 `PyObject_Size` 的行為」，動手前務必反查誰呼叫它：

```
$ rg -n "PyObject_Size\(" Objects/*.c Python/*.c Modules/*.c
Python/bltinmodule.c:1768         res = PyObject_Size(obj);          # 就是 len
Objects/descrobject.c:1047        return PyObject_Size(pp->mapping);
Objects/dictobject.c:5917         len_self = PyObject_Size(self);
Modules/_interpretersmodule.c:825 Py_ssize_t size = PyObject_Size(...)
...
```

**`len` 不是 `PyObject_Size` 的唯一呼叫者**——dict 的比較、descriptor、子直譯器模組都依賴它。你改它的行為會漣漪到這些地方。這就是任務驅動法的安全帶（`reading_code` Ch 9 反查呼叫者、Ch 33 code review 補盲點）：**只讀一小塊沒問題，但改一小塊前要拉出所有呼叫者評估影響**。`PyNumber_Add` 同理——反查會發現 `sum()`（`bltinmodule.c:2619`）、`range`（`rangeobject.c` 多處）、AST 常數摺疊（`ast_opt.c:476`）都呼叫它。局部理解 + 全局責任，這條反查是兩者之間的橋。

## 底層機制：這次攻堅砍掉了多少

量化這次分而治之的收斂（對照 Ch 31 在 redis 砍到 59 行）：

```
   起點：CPython v3.13.1
   ├─ C/H：       837,430 行
   ├─ .c 檔：         439 個
   └─ Lib .py：   127,217 行

   任務「len 怎麼運作」實際讀的：
   ├─ Python/bltinmodule.c   builtin_len       ~15 行
   ├─ Objects/abstract.c     PyObject_Size     ~15 行
   └─ Objects/listobject.c   list_length（掃一眼確認）
                             ─────────────────
                             實讀約 30–40 行
```

**從 83 萬行收斂到約 30–40 行，主動無視率 > 99.99%。** 六把刀在 CPython 上比在 redis 上砍得更狠（因為基數更大），方法完全相同。這印證 Ch 31 的話：尺度放大時方法不變，只是每一刀砍掉更多、你更依賴導航（grep 定位而非漫遊）和「認出生成標記」（`bytecodes.c` DSL、Argument Clinic）避免在葉子檔裡瞎找上游。

## 換第三個語意驗證：方法不能只在一個靶上有效

一套讀碼方法若只在 `len` 上有效，那是巧合不是方法。換完全不同的語意——`d[k]`（dict 取值）——用同一套刀再收斂一次。任務：「`d[k]` 怎麼從 subscript 走到 dict 的查值？」

**刀一裁剪 + 導航**（真跑）：先 `dis` 看 `d[k]` 編成什麼——`BINARY_SUBSCR`。去 DSL 找它，不在 `ceval.c` 找：

```
$ rg -n "BINARY_SUBSCR" Python/bytecodes.c | head -2
568:        family(BINARY_SUBSCR, ...) = {
569:            BINARY_SUBSCR_DICT,
```

`BINARY_SUBSCR` 的泛型 body 呼叫 `PyObject_GetItem`（object protocol，`Objects/abstract.c:150`）。它查什麼 slot？

```
$ rg -n "dict_subscript|dict_as_mapping|mp_subscript" Objects/dictobject.c | head -3
3300:dict_subscript(PyObject *self, PyObject *key)
3343:static PyMappingMethods dict_as_mapping = {
3345:    dict_subscript, /*mp_subscript*/
```

**同一個形狀第三次出現**：`BINARY_SUBSCR` opcode → `PyObject_GetItem`（協定函式）→ 查 `Py_TYPE(o)->tp_as_mapping->mp_subscript` slot → `dict` 的 `dict_subscript` 實作。跟 `a+b`（`nb_add`）、`len`（`sq_length`）一模一樣的「opcode → 協定函式 → type slot → 具體實作」結構，只是換成映射協定 `mp_subscript`。

**這證明兩件事**：(1) 分而治之六把刀不挑靶——換子系統，同一套 grep 導航 + 認出生成標記 + 主動無視就收斂；(2) CPython 的 object protocol 是高度規律的，你認出一次形狀，之後每個運算子/內建都是同一個 chunk。這正是這門課的複利：`a+b` 你逐站追，`d[k]` 你掃過去就知道去哪找 slot。方法遷移 + pattern 遷移，兩者在這裡疊加。

## 對比與取捨

| 策略 | 在 redis（Ch 31） | 在 CPython（本章） |
|---|---|---|
| 任務裁剪 | 「SETRANGE 防爆記憶體」→ 59 行 | 「len 怎麼走到 slot」→ ~35 行 |
| 導航工具 | `rg` + `cscope` 夠用 | `rg` 夠用，但要**認出生成標記** |
| 額外複雜性 | 無 | 兩套生成器（bytecodes DSL、Argument Clinic） |
| test 當入口 | 可選 | 更值得（`Lib/test/` 是巨大可執行文件） |
| 主動無視率 | ~99.95% | ~99.99% |
| 陷阱 | grep 雜訊 | grep 到生成碼，誤以為是手寫實作 |

**關鍵取捨**：CPython 比 redis 多了「生成碼」這層。好處是 code 更整齊（DSL 保證一致性），代價是**讀碼時你得先辨識「這是生成的葉子還是手寫的源」**。攻堅大 codebase 時，第一件事常常是摸清它有哪些生成系統——摸清了，你就不會在 `generated_cases.c.h` 或 clinic 的 `.c.h` 裡找上游找到崩潰。

## 踩雷集錦

1. **錯誤直覺：「要理解 len 得先讀懂 `bltinmodule.c` 整個檔」。** → 正確認識：`bltinmodule.c` 有幾千行（所有內建函式）。你只需要 `builtin_len` 那 15 行。用 grep 導航到那個函式，不讀整檔。刀一裁剪 + 導航。
2. **錯誤直覺：`/*[clinic input]*/` 那段是註解，可略過。** → 正確認識：那是 Argument Clinic 的 DSL 宣告，會生成參數解析碼（到同名 `.c.h`）。它跟 `bytecodes.c` 是**不同的**生成系統。對「讀懂 len 語意」它不重要（跳到 body 的 `PyObject_Size`），但你得認出它是生成標記，別以為函式簽章附近那段是可有可無的註解。
3. **錯誤直覺：CPython 只有 `bytecodes.c` 一套生成器。** → 正確認識：至少兩套——eval loop 的 cases_generator，和 Argument Clinic。大 codebase 常有多套生成/元編程系統，攻堅前先摸清有哪些，才不會在生成的葉子檔裡找上游找瘋。
4. **錯誤直覺：追到 `list_length` 要繼續讀它內部怎麼算長度。** → 正確認識：任務是「len 怎麼走到 slot」，追到「slot 是 `list_length`」就答完了。`list_length` 內部（讀 `ob_size`）是實作細節，任務沒逼你深潛就主動無視。深潛是昂貴操作，只在任務命中的那一站動用（刀四）。
5. **錯誤直覺：`--depth 1` 的 clone 不能做 git 考古。** → 正確認識：淺 clone 沒有歷史，但要考古時 `git fetch --unshallow` 補抓完整歷史，就能 `git log -S`/`blame` 挖一個決策的來龍去脈。釘死 tag 讀當前碼、按需補歷史考古，兩者不衝突（Ch 0、Ch 17）。

## 進階：再往深一層

- **用 `Lib/test/` 當可執行規格**：CPython 的測試覆蓋率極高，`Lib/test/test_*.py` 描述了每個功能的行為邊界與 corner case。想理解一個語意「應該怎樣」，讀它的 test 比讀實作快——test 是「做什麼」的可執行文件，實作是「怎麼做」。這是介面優先（刀三）在有優質測試的專案上的最強形態。對照 `reading_code` Ch 10 假設驅動：test 幫你先建假設，再去 source 驗證。
- **`grep` 生成器輸入而非輸出**：當你要改一個 opcode 或一個內建函式的簽章，grep `generated_cases.c.h` 或 clinic 的 `.c.h` 是死路（那是輸出）。要 grep **輸入**：opcode 去 `bytecodes.c`，內建簽章去 `/*[clinic input]*/` 那段。「往上游 grep」是讀有生成系統的大 codebase 的核心紀律。
- **build 圖告訴你子系統邊界**：CPython 的 `Makefile.pre.in` 與 `Modules/Setup` 宣告了哪些 `.c` 編進核心、哪些是可選模組。想知道「哪些是 CPython 一定會有的核心、哪些是外掛」，讀 build 宣告比讀 source 快（`reading_code` Ch 21）。你今天無視 `Modules/` 98 個檔的依據，build 圖能幫你更精確地確認。

## 本章重點整理

- CPython（83 萬行）比 redis（14 萬行）大 6 倍，但 `reading_code` Ch 31 的六把刀完全適用——只是每刀砍得更狠、更依賴導航與辨識生成標記。
- 任務「len 怎麼走到 slot」→ 一個 grep 導航到 `builtin_len` → `PyObject_Size` → `Py_TYPE(o)->tp_as_sequence->sq_length`，實讀約 30–40 行，主動無視率 > 99.99%。
- CPython 有**多套生成系統**：eval loop 的 cases_generator（`bytecodes.c`）+ Argument Clinic（`/*[clinic input]*/`）。攻堅前先摸清，避免在生成葉子檔裡找上游。
- chunking 生效：`len` 的「協定函式 → type slot」是 `a+b` 學過的同一個形狀，第二次一眼認出——這就是本課要練的「更會讀」。
- 介面優先在 CPython 上更強：`Lib/test/` 是巨大的可執行規格，讀 test 建假設比讀實作快；git 考古（`fetch --unshallow` 後 `log -S`/`blame`）補「為什麼這樣設計」。

## 自我檢核

- [ ] 給你「查 CPython 某內建函式怎麼運作」，你能不能不打開任何檔就砍掉 630+ 個 `.c`？講出依據。
- [ ] 你能認出 `/*[clinic input]*/` 是什麼、它跟 `bytecodes.c` 的生成系統有何不同嗎？
- [ ] 追到 `list_length` 時，為什麼你該停而不是繼續讀它內部？（提示：任務有沒有逼你）
- [ ] 要改一個 opcode 的行為，你 grep `generated_cases.c.h` 對不對？該 grep 哪個檔？
- [ ] `--depth 1` clone 之後想做 git 考古，你怎麼補歷史？

## 延伸閱讀

- **[`reading_code` Ch 31 — 大型專案的分而治之](../reading_code/31-divide-and-conquer-large-codebases.md)。**
  - **讀哪裡**：六把刀那節與 redis 收斂範例。
  - **學到什麼**：本章是它在 CPython 上的實戰版；回頭讀它把方法論補完整，再對照本章看尺度放大時的差異。
  - **前提**：無，本課的方法論母章。
- **[CPython devguide — "Argument Clinic"](https://devguide.python.org/development-tools/clinic/)（官方）。**
  - **讀哪裡**：概觀與 "How to use" 前段。
  - **學到什麼**：本章撞到的第二個生成系統的官方說明——CPython 有多套 DSL 的具體證據。
  - **前提**：讀過 Ch 23 的 bytecodes DSL 生成關係。
- **[CPython devguide — "Running & writing tests"](https://devguide.python.org/testing/run-write-tests/)（官方）。**
  - **讀哪裡**：測試組織與 `Lib/test/` 佈局。
  - **學到什麼**：怎麼把 `Lib/test/` 當可執行規格用——介面優先（刀三）在 CPython 上的最強形態。
  - **前提**：無。

你已經能自己在 CPython 這個尺度攻堅了。最後一步，把三章讀到的東西結晶成可遷移的 pattern 卡片——boxed object + type slot、refcount + cyclic GC 混合、bytecode DSL 生成、computed-goto dispatch、穩定 C-API 邊界——並把它們接到本課其他 Part 遇過的同類 pattern。

→ [Ch 26 萃取 pattern：refcount / object protocol / C-API 邊界](./26-cpython-patterns-extracted.md)
