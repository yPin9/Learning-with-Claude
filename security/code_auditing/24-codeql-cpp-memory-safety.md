# Ch 24 — C/C++ 記憶體安全 query

> **目標**：把 [Ch 22](./22-codeql-global-taint.md) 學到的 global taint、[Ch 23](./23-codeql-flow-state-models.md) 的 flow state，落地到低階研究者最痛的類別——**記憶體安全**。逐類給你 query 的**思路**與**現成 library class**：越界寫入 / buffer overflow、整數溢位 → 小 alloc、UAF / double-free、format string。每一類都在真 database 上跑給你看（含一個「該漏而漏」的邊界失敗），再跑一次內建 `cpp-security-and-quality` suite 對照，最後把踩雷攤開。讀完你能自己寫出抓這四類 bug 的 query，也知道每一類的 CodeQL 建模在哪裡會騙你。
>
> **環境**：CodeQL 2.26.2

記憶體安全是 CodeQL 對低階研究者最有價值的戰場，原因有二。其一，這些 bug 的 root cause 幾乎都是**資料流問題**——某個攻擊者可控的長度 / 索引 / 指標，沒經檢查就到了危險運算，正是 taint 的主場。其二，這些類別 CodeQL 官方標準庫**已經建模得很完整**（有現成的 `cpp/uncontrolled-allocation-size`、`cpp/use-after-free` 這些 query 可直接跑），你既能直接用，也能拆開它們的 library 學怎麼自己寫變體版。

這章我全部在 WSL 的 CodeQL 2.26.2 上真跑。用兩個 database：共用靶 `~/audit-lab/vuln.c`（就是那個 `read` 出 `len`、不檢查就 `memcpy` 的經典），加一個我為這章自建、把四類 bug 都塞進去的 `mem.c`。每條 query 我貼真實命中；有一條會示範**該中沒中**，那是最好的老師。

## 先建立一個心智模型：記憶體安全 sink 看的是「參數」不是「函式名」

[Ch 11 sink 目錄](./11-cross-language-sink-catalog.md)講過一句話，這裡要再釘一次，因為它決定你的 query 會不會誤報漫天：

> `memcpy` 本身無罪。`memcpy(dst, src, tainted_len)` 才是 sink。sink 不是「有沒有呼叫 `memcpy`」，是「`memcpy` 的第三個參數是不是被污染的長度」。

所以每一類記憶體安全 query 的骨架都長這樣：**source（攻擊者可控值）→ 經過（或缺少）某個檢查 → 到危險運算的某個特定參數位置**。純語法 grep（[Ch 13](./13-semgrep-syntactic-patterns.md)）抓 `memcpy` 會滿地誤報，正是因為它只看函式名不看參數是否被污染。CodeQL 的價值就在能把「這個參數的值從哪來」算清楚。

下面逐類拆。

## 類別一：OOB / buffer overflow — 污染長度流到 memcpy

**bug 形狀**：一個攻擊者可控的長度，沒有 bound check，就當成 `memcpy` / `memmove` 的 size，或當成陣列寫入的 index。這是 CWE-787（Out-of-bounds Write）/ CWE-120。

共用靶 `vuln.c` 就是教科書級的例子：

```c
void handle(int fd) {
    char buf[64];
    int len;
    read(fd, &len, sizeof(len));      // source：攻擊者控制的 len
    char *data = malloc(len);
    read(fd, data, len);
    memcpy(buf, data, len);           // sink：len 沒 bound，OOB 寫穿 64-byte buf
}
```

`buf` 只有 64 bytes，`len` 是攻擊者從 fd 讀進來的任意 int，`memcpy(buf, data, len)` 直接寫穿。要用 CodeQL 抓，我們要表達的是：**`read` 寫進去的那個值，一路流到 `memcpy` 的第三個參數**。

### query：source 是 `read` 的輸出參數

這裡有個 CodeQL 建模細節值得停一下。`read(fd, &len, ...)` 的污染值不是 `read` 的**回傳值**（回傳的是讀了幾 bytes），而是它**經由第二個指標參數寫回去的 `len`**。CodeQL 把這種「函式透過指標參數輸出的值」建模成 `asDefiningArgument()`——你要的是「`read` 的 argument 1 這個位置，作為一個被定義的輸出」。

```ql
/**
 * @name Tainted length flows to memcpy size
 * @kind path-problem
 * @id audit/oob-memcpy
 * @problem.severity error
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import OobFlow::PathGraph

module OobCfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    // read(fd, &len, sizeof(len))：len 是經由 arg1 指標寫回的輸出值
    exists(FunctionCall fc |
      fc.getTarget().getName() = "read" and
      source.asDefiningArgument() = fc.getArgument(1))
  }
  predicate isSink(DataFlow::Node sink) {
    exists(FunctionCall mc |
      mc.getTarget().getName() = ["memcpy", "memmove"] and
      sink.asExpr() = mc.getArgument(2))     // memcpy 的第 3 個參數（size）
  }
}
module OobFlow = TaintTracking::Global<OobCfg>;

from OobFlow::PathNode src, OobFlow::PathNode sink
where OobFlow::flowPath(src, sink)
select sink.getNode(), src, sink, "tainted length to memcpy size"
```

**真跑**（`codeql query run --database=~/audit-lab/vuln-db ./oob-memcpy.ql`）命中：

```
#select
| col0 |         src          | sink |             col3              |
+------+----------------------+------+-------------------------------+
| len  | read output argument | len  | tainted length to memcpy size |

edges
| read output argument | len | provenance |
```

path 讀出來是：`read output argument`（`&len` 那個定義點）→ `len`（`memcpy` 的 size）。這正是我們要的——`TaintTracking` 幫我們把「`read` 寫進 `len`、`len` 傳到 `memcpy`」這條 flow 自動接起來，中間 `malloc(len)`、`read(fd,data,len)` 都不影響 taint 傳播。

用 `path-problem` 而非 `problem`（[Ch 22](./22-codeql-global-taint.md) 教過）是刻意的：記憶體安全 bug 的 triage 極度依賴「這條污染是怎麼走過來的」，path 讓你一眼看出中間有沒有被你漏掉的 sanitizer。

### 現成的 library class

上面我手寫了 source/sink，是為了教你骨架。實務上 CodeQL 標準庫有更精緻的抽象，你該優先用：

- **`FunctionCall`**（`semmle.code.cpp.Call`）：任何函式呼叫，`.getTarget().getName()`、`.getArgument(n)` 就是上面用的。這是最穩的基礎 class。
- **`AllocationExpr`**（`semmle.code.cpp.models.interfaces.Allocation`）：把 `malloc`/`calloc`/`new`/自訂 allocator 統一抽象，比寫死 `"malloc"` 好——它會涵蓋你想不到的 allocator。
- **記憶體複製類**：標準庫在 `semmle.code.cpp.models.implementations.Memcpy` 等把 `memcpy`/`memmove`/`strcpy` 這族建模成有「buffer 參數」「size 參數」語意的 class。**類名會隨 bundle 版本變動——以你 bundle 版本的 library 為準**，寫 query 前先在 VS Code 裡 `Ctrl+Click` 進標準庫確認實際 class 名。我這裡用最基礎、跨版本最穩的 `FunctionCall` 手寫，代價是要自己列函式名。

**判斷準則**：能用標準庫的高階 class 就用（涵蓋面廣、跨版本它們自己維護）；但**別假設 class 名跨版本穩定**（見踩雷）。基礎 `FunctionCall` + 手列名字是最保險的 fallback。

## 類別二：整數溢位 → 小 alloc（CWE-190）

**bug 形狀**：兩個以上攻擊者可控的整數相乘，乘積在傳給 `malloc` 前**沒有溢位檢查**；之後寫入用的是「未溢位的邏輯尺寸」，導致 alloc 小、寫入大。這是影像 / 媒體解析庫最經典的 heap overflow 來源（[Ch 43](./43-case-study-variant-hunt.md) 整章在追這個）。

我自建 `mem.c`，把危險版與安全版擺一起：

```c
/* CWE-190：整數溢位 → 小 alloc */
void *make_buf(int fd) {
    unsigned int count, sz;
    read(fd, &count, sizeof(count));   // 可控
    read(fd, &sz, sizeof(sz));         // 可控
    unsigned int total = count * sz;   // ← 溢位點
    char *p = malloc(total);           // ← 小 alloc
    read(fd, p, count * sz);           // 用原始邏輯尺寸寫入
    return p;
}

/* 安全版：checked multiplication，該被 barrier 切掉 */
void *make_buf_safe(int fd) {
    size_t count, sz, total;
    read(fd, &count, sizeof(count));
    read(fd, &sz, sizeof(sz));
    if (__builtin_mul_overflow(count, sz, &total)) return NULL;  // barrier
    return malloc(total);
}
```

### query：taint 到 malloc size，且「經過一次可控相乘」，且沒被安全乘法 barrier 切斷

天真的寫法是「任何 tainted 值到 malloc」——但那太寬（單一 tainted size 到 malloc 未必溢位），核心語意是**兩個可控值相乘**。我用一個輔助 predicate `taintedMul` 表達「一個乘法，它左右運算元都能 local-taint 追溯到 `read` 輸出」，再要求它和 sink 在同函式：

```ql
/**
 * @name Tainted multiplication reaches allocation size
 * @kind path-problem
 * @id audit/intoverflow-malloc
 * @problem.severity error
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import Flow::PathGraph

// 一個乘法：兩個運算元都能追溯到 read() 的輸出
predicate taintedMul(MulExpr m) {
  forall(Expr op | op = m.getAnOperand() |
    exists(DataFlow::Node s, DataFlow::Node t, FunctionCall fc |
      fc.getTarget().getName() = "read" and s.asDefiningArgument() = fc.getArgument(1) and
      t.asExpr() = op and TaintTracking::localTaint(s, t)))
}

module Cfg implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    exists(FunctionCall fc |
      fc.getTarget().getName() = "read" and source.asDefiningArgument() = fc.getArgument(1))
  }
  predicate isSink(DataFlow::Node sink) {
    exists(FunctionCall mc |
      mc.getTarget().getName() = ["malloc", "calloc"] and sink.asExpr() = mc.getArgument(0))
  }
  // barrier：用了 __builtin_mul_overflow / reallocarray 的安全乘法，切斷 taint
  predicate isBarrier(DataFlow::Node n) {
    exists(FunctionCall safe |
      safe.getTarget().getName() = ["__builtin_mul_overflow", "reallocarray"] and
      n.asExpr() = safe.getAnArgument())
  }
}
module Flow = TaintTracking::Global<Cfg>;

from Flow::PathNode src, Flow::PathNode sink, MulExpr m
where Flow::flowPath(src, sink)
  and taintedMul(m)
  and m.getEnclosingFunction() = sink.getNode().asExpr().getEnclosingFunction()
select sink.getNode(), src, sink,
  "tainted multiplication " + m.toString() + " feeds allocation size (CWE-190)"
```

**真跑**命中：

```
#select
| col0  |         src          | sink  |                       col3                       |
+-------+----------------------+-------+--------------------------------------------------+
| total | read output argument | total | tainted multiplication ... * ... ... (CWE-190)   |
| total | read output argument | total | tainted multiplication ... * ... ... (CWE-190)   |
```

兩列命中，都在 `make_buf`（兩個 source arg：`count` 與 `sz` 各成一條 path）。關鍵是——**`make_buf_safe` 沒有命中**，因為 `__builtin_mul_overflow` 被建模成 barrier，把 taint 在進 malloc 前切斷了。這就是 barrier 建模的價值：它讓 query 分得清「危險相乘」與「已檢查相乘」。

我要**誠實標一個近似**：「乘法與 sink 在同函式」（`m.getEnclosingFunction() = ...`）是粗關聯，不是嚴謹地證明「這條 taint 真的經過那個乘法」。嚴謹做法是用 flow state（[Ch 23](./23-codeql-flow-state-models.md)）把「已經過 tainted 乘法」記成狀態，讓 sink 只接受帶此狀態的 flow。我這裡用同函式近似是為了骨架好懂；上真專案時該升級成 flow state 版，精度會顯著提高。[Ch 43](./43-case-study-variant-hunt.md) 詳細走過這個 v1→v2 迭代。

## 類別三：UAF / double-free（CWE-416 / CWE-415）

**bug 形狀**：`free(p)` 之後，在控制流上仍存在對 `p` 的存取（UAF），或第二次 `free(p)`（double-free）。這類的 query 核心不是 taint，是**控制流順序**——「free 之後、還用它」。

`mem.c` 裡放了兩個：

```c
void uaf(int fd) {
    char *b = malloc(128);
    read(fd, b, 128);
    free(b);
    char x = b[0];      // ← UAF：free 後讀 b
    printf("%c", x);
}
void df(int cond) {
    char *b = malloc(64);
    free(b);
    if (cond) free(b);  // ← double free：第二個 free 也是「free 後再用 b」
}
```

### query：free 後、在 CFG 上仍能到達的變數存取

用 `getASuccessor+()`（控制流圖的傳遞後繼）表達「這個 use 發生在 free 之後」：

```ql
/**
 * @name Use after free (local, control-flow ordered)
 * @kind problem
 * @id audit/uaf
 * @problem.severity error
 */
import cpp
import semmle.code.cpp.dataflow.new.DataFlow

from FunctionCall free, Expr use, Variable v
where free.getTarget().getName() = "free"
  and free.getArgument(0) = v.getAnAccess()
  and use = v.getAnAccess()
  and use != free.getArgument(0)
  and free.getASuccessor+() = use          // use 在 CFG 上位於 free 之後
  and use.getEnclosingFunction() = free.getEnclosingFunction()
select use, "use of $@ after it was freed", v, v.getName()
```

**真跑**命中：

```
| use |             col1             | v | col3 |
+-----+------------------------------+---+------+
| b   | use of $@ after it was freed | b | b    |
| b   | use of $@ after it was freed | b | b    |
```

兩列：一是 `uaf()` 裡 `b[0]` 這個 UAF，二是 `df()` 裡第二個 `free(b)`——注意 **double-free 被同一條 query 抓到了**，因為「第二次 free 的參數 `b`」本身就是「free 之後對 `b` 的一次存取」。一個控制流條件同時涵蓋 UAF 與 double-free，漂亮。

**這條 query 的誠實邊界**（也是踩雷）：它是 **path-insensitive** 的近似。`getASuccessor+()` 只問「CFG 上有沒有一條路徑從 free 走到 use」，不問「那條路徑實際可達嗎、指標中間有沒有被重新賦值指向新配置」。所以它會對「free 後 `b = malloc(...)` 重新賦值再用」這種安全寫法誤報。真正嚴謹的 UAF 分析要 path-sensitive + 追蹤指標是否被重新指向——標準庫的 `cpp/use-after-free`（下面 suite 會跑到）做得比這條精細得多。我這條是給你看「控制流順序 query 的骨架長怎樣」，不是給你當生產規則。

## 類別四：format string（CWE-134）

**bug 形狀**：`printf`（或 `syslog`、`fprintf`…）的**格式字串參數不是常數字面**，而是可能被污染的變數。攻擊者塞 `%n`、`%x` 就能讀寫記憶體。

`mem.c`：

```c
void logit(int fd) {
    char msg[128];
    read(fd, msg, 127);
    printf(msg);        // ← 格式串是 tainted 變數
}
```

### query：printf 的格式參數不是 StringLiteral

最簡單也最有效的近似——格式參數只要不是常數字面就是 candidate：

```ql
/**
 * @name Non-constant format string
 * @kind problem
 * @id audit/format-string
 * @problem.severity error
 */
import cpp

from FunctionCall fc, Expr fmt
where fc.getTarget().getName() = "printf"
  and fmt = fc.getArgument(0)
  and not fmt instanceof StringLiteral   // 格式參數不是常數字面
select fc, "printf with non-literal format string $@ (CWE-134)", fmt, fmt.toString()
```

**真跑**命中：

```
|       fc       |                        col1                        | fmt | col3 |
+----------------+----------------------------------------------------+-----+------+
| call to printf | printf with non-literal format string $@ (CWE-134) | msg | msg  |
```

只命中 `logit()` 的 `printf(msg)`。**注意它沒有命中 `uaf()` 裡的 `printf("%c", x)`**——因為那個格式參數是 `StringLiteral`，`x` 是後面的資料參數不是格式參數。這個 query 用「格式參數位置 + 是否字面」就精準區分了，不需要 taint。

要更精準（區分「非字面但實際不可控」與「真被污染」），再加一層 taint：source 是 remote input、sink 是這個非字面格式參數。但對 format string 而言，「格式串非常數」本身就是很強的訊號，很多場合這個語法級 query 就夠用了。

## 跑內建 suite 對照：`cpp-security-and-quality`

自己寫 query 學原理，但生產審計你會先跑官方 suite 打底。對 `mem.c` 跑一次：

```bash
codeql database analyze ~/audit-lab/ch24-db \
  cpp-security-and-quality.qls \
  --format=sarif-latest --output=/tmp/ch24.sarif --rerun
```

**真跑 SARIF 摘要**（用 python 從 sarif 取 ruleId + 位置）：

```
total results: 5
  1  cpp/double-free
  1  cpp/use-after-free
  1  cpp/tainted-format-string
  1  cpp/integer-multiplication-cast-to-long
  1  cpp/uncontrolled-allocation-size

cpp/uncontrolled-allocation-size:   allocation size derived from user input @ mem.c:12
cpp/integer-multiplication-cast-to-long: multiplication may overflow ...    @ mem.c:13
cpp/use-after-free:                 memory may have been freed              @ mem.c:31
cpp/double-free:                    may already have been freed             @ mem.c:39
cpp/tainted-format-string:          format arg may come from buffer read    @ mem.c:46
```

**每一類我手寫的 query 抓到的東西，官方 suite 都獨立命中了**——而且官方版更精緻（`cpp/integer-multiplication-cast-to-long` 專抓「乘法在轉成更寬型別前就溢位」這個更細的形狀；`cpp/uncontrolled-allocation-size` 有完整的 taint 建模）。

這給你兩個實務判斷：**其一**，先跑官方 suite，它涵蓋這四類且維護良好，你不用重造輪子。**其二**，你手寫 query 的價值不在「重複官方能抓的」，而在「官方**沒**建模的專案特有 sink / source」——自訂 allocator、自訂 reader、專案內的間接 wrapper（[Ch 26](./26-codeql-cve-to-query.md) 的 CVE→variant 就是這種官方抓不到、要你自己寫的場景）。

## 一個「該漏而漏」的邊界：memcpy-only query 漏掉手寫 loop copy

前面 OOB query 只認 `memcpy`/`memmove`。但真實 codebase 常有**手寫的 byte-by-byte copy loop**，語意上一模一樣的 OOB，卻沒有任何 `memcpy` 呼叫。我在 `mem.c` 放了一個：

```c
void loop_copy(int fd) {
    char dst[32];
    unsigned int n;
    read(fd, &n, sizeof(n));
    char src[256];
    read(fd, src, 255);
    for (unsigned int i = 0; i < n; i++) dst[i] = src[i]; // OOB，但沒有 memcpy
}
```

拿前面那條 `oob-memcpy.ql` **對 `mem.c` 跑**：

```
#select
| col0 | src | sink | col3 |
+------+-----+------+------+
（空）
```

**0 命中。** query 完全漏掉 `loop_copy`——因為它的 sink 只建模 `memcpy`/`memmove`，而這裡的越界寫入是 `dst[i] = src[i]` 這個**污染 index 的陣列寫入**，沒有任何 library 函式呼叫。這不是 CodeQL 的錯，是我的 sink 建模不全。要抓這種，sink 得改成「用 tainted index 的 `ArrayExpr` / `PointerArithmeticOperation` 寫入」，或用標準庫更廣的 buffer-overflow 建模。**這是記憶體安全 query 最常見的漏報來源之一**，記住它。

## 踩雷集錦

**錯誤直覺：「抓記憶體 copy 漏洞，把 `memcpy`/`memmove`/`strcpy` 這族列進 sink 就完整了。」**
正確認識：漏掉**手寫 loop copy** 與**污染 index 的陣列寫入**（`dst[tainted_i] = ...`）。上面那個真跑就是活證：`loop_copy` 的 OOB 用 `dst[i]=src[i]`，我的 memcpy-only query 0 命中。真實 codebase 有大量手寫複製迴圈（效能、無 libc、特殊對齊），只建模 library 函式必漏一大片。sink 要同時涵蓋「library copy 的 size 參數」與「用污染 index/長度的陣列/指標寫入」。

**錯誤直覺：「整數溢位，有號無號都一樣，抓到相乘進 malloc 就對了。」**
正確認識：**有號 / 無號的溢位語意天差地別，且影響你的 sink 與 barrier 建模**。C 的**無號**溢位是良好定義的回繞（`UINT_MAX+1 == 0`），這正是 `make_buf` 那種 `unsigned int total = count*sz` 溢位回小值的機制；**有號**溢位是 UB，編譯器可能整段最佳化掉你以為存在的檢查（`if (a+b < a)` 這種「檢查」在有號 UB 下會被編譯器判定不可能而刪掉——這本身就是一類 bug）。寫 query 時，「用了 `if (total < count)` 這種 wrap-around 檢查」對無號是有效 barrier、對有號可能根本沒被生成。別把兩者當同一回事一律建模成 barrier。

**錯誤直覺：「UAF query 用 `getASuccessor+()` 抓到 free 後的 use，就是真 UAF。」**
正確認識：那是 **path-insensitive 近似**，會誤報。`getASuccessor+()` 只證明「CFG 上存在一條 free→use 路徑」，不證明那條路徑實際可達、也不管指標中間有沒有被 `p = malloc(...)` 重新指向新配置。「free 後重新賦值再用」是安全的，但這條 query 會報。真正的 UAF 要 path-sensitive + 追蹤指標重指向——這正是標準庫 `cpp/use-after-free` 比我這條骨架精細的地方。我這條給你看結構，triage 時務必人工確認「free 到 use 之間指標沒被重指向、那條路徑真可達」。

**錯誤直覺：「format string 要用完整 taint 才能抓。」**
正確認識：多數場合**「格式參數不是 `StringLiteral`」這個語法級條件就是很強的訊號**，不必先上 taint。上面真跑證明它精準命中 `printf(msg)`、精準略過 `printf("%c", x)`——因為後者格式參數是字面。當然要區分「非字面但常數不可控（例如來自唯讀設定）」與「真被污染」時再加 taint；但別一上來就把簡單問題複雜化。這也是提醒：**不是每類記憶體 bug 都需要 taint，選對武器**。

**錯誤直覺：「標準庫的 class 名（`MemcpyCall`、`AllocationExpr`…）跨版本穩定，query 寫死照抄就行。」**
正確認識：CodeQL 標準庫的 class 名與模組路徑**會隨 bundle 版本演進**（`dataflow.new` vs 舊 `dataflow`、models-as-data 的重構等）。我這章刻意多用最基礎、最穩的 `FunctionCall` + 手列函式名，代價是涵蓋面窄但不會因升版壞掉。用高階 class（`AllocationExpr` 等）涵蓋面廣、但**寫之前先在你的 bundle 裡 `Ctrl+Click` 進標準庫確認實際 class 名與 API**，別當它跨版本恆定。凡我沒把握的類名，我都標了「以你 bundle 版本的 library 為準」——你也該養成這習慣。

## 進階延伸

- **把 OOB sink 擴到「污染 index 的陣列寫入」**：本章 memcpy-only query 漏掉 `loop_copy` 的核心教訓，是 sink 要涵蓋 `ArrayExpr`（`dst[i]`）與 `PointerArithmeticOperation` 用污染值當偏移的情形。把 sink 從「memcpy 的 size 參數」擴成「任何用 tainted 值當 index/offset 的記憶體寫入」，是把這章 query 從玩具升級成堪用的關鍵一步。標準庫的 buffer-overflow query 就是這樣建的，拆開它看。
- **整數溢位精修成 flow state**：本章用「乘法與 sink 同函式」近似關聯。升級成 [Ch 23](./23-codeql-flow-state-models.md) 的 flow state——把「已流經一次 tainted 相乘」記成 state，sink 只接受帶此 state 的 flow——就能正確處理跨函式的相乘、也砍掉「同函式但無關的乘法」造成的誤配。[Ch 43](./43-case-study-variant-hunt.md) 走過完整的 v1→v2 迭代。
- **UAF 升級到 path-sensitive**：本章 CFG 近似的下一步，是引入 guard / range 分析區分可達路徑，並追蹤指標是否被重新指向。這是 CodeQL C/C++ 分析裡技術最深的一塊，直接讀標準庫 `cpp/use-after-free` 的實作是最好的教材。
- **自訂 allocator / reader 的建模**：真專案很少直接呼叫 `malloc`/`read`，多半包一層（`my_alloc`、`buf_read`）。用 models-as-data（[Ch 23](./23-codeql-flow-state-models.md)）把這些專案特有 wrapper 建模成 allocation / source，本章所有 query 才能在真 codebase 上運作。這是官方 suite 抓不到、非你自己補不可的部分。

## 本章重點整理

- 記憶體安全 sink 看的是**參數是否被污染**（長度 / index / 指標），不是函式名——`memcpy` 無罪，`memcpy(dst,src,tainted_len)` 才是 sink。
- 四類 query 的骨架：**OOB** = tainted 長度到 `memcpy` size 參數（`asDefiningArgument` 建模 `read` 輸出）；**整數溢位** = 兩可控值相乘到 malloc size，安全乘法（`__builtin_mul_overflow`）當 barrier；**UAF/double-free** = `free` 後 CFG 上仍可達的變數存取（`getASuccessor+()`，一條件同抓兩類）；**format string** = printf 格式參數非 `StringLiteral`。
- 四類我都真跑命中；`make_buf_safe` 因 barrier 未命中、`printf("%c",x)` 因字面未命中——證明建模分得清危險與安全。
- 內建 `cpp-security-and-quality` suite 對 `mem.c` 獨立命中全部五個對應規則（`uncontrolled-allocation-size`/`integer-multiplication-cast-to-long`/`use-after-free`/`double-free`/`tainted-format-string`），且比手寫版精細——**先跑官方 suite 打底，手寫 query 用來補官方沒建模的專案特有 sink**。
- 真跑示範一個關鍵漏報：memcpy-only query **0 命中 `loop_copy`**，因為它的 OOB 是手寫 `dst[i]=src[i]` 沒有 library 呼叫。
- 邊界誠實：UAF query 是 path-insensitive 近似會誤報；整數溢位的「同函式」關聯是近似；標準庫 class 名別當跨版本穩定。

## 自我檢核

- [ ]（主動回憶）不看內文，寫出四類記憶體安全 bug 各自的 source、sink、（若有）barrier 分別建模成什麼。哪一類**不需要** taint 就能抓得不錯？
- [ ]（理解）為什麼 `read(fd, &len, ...)` 的 source 要用 `asDefiningArgument()` 而不是 `read` 的回傳值？回傳值是什麼？
- [ ]（理解）`make_buf_safe` 為什麼沒被整數溢位 query 命中？把 barrier 那段刪掉，你預期會發生什麼？
- [ ]（應用）memcpy-only query 為什麼 0 命中 `loop_copy`？要抓到它，sink 該怎麼改？
- [ ]（理解）為什麼說 UAF 的 `getASuccessor+()` 版本會誤報？舉一個它會誤報的安全寫法。
- [ ]（綜合）你在真專案發現它把 `malloc` 包成 `xmalloc`、把 `read` 包成 `net_read`。本章四條 query 哪些會因此全部失效？你要做什麼才能讓它們重新運作（連到哪章）？

## 延伸閱讀

- **CodeQL 標準庫 C/C++ 的 `Security/CWE/` query 原始碼**（你 bundle 內 `qlpacks/codeql/cpp-queries` 下，或 GitHub `github/codeql` repo 的 `cpp/ql/src/Security`）——`cpp/uncontrolled-allocation-size`、`cpp/use-after-free`、`cpp/tainted-format-string` 的**官方實作**。用法：拿本章手寫版對照官方版，看它們怎麼把我的近似升級成精細建模（barrier、path-sensitivity、models-as-data）。前提：本章 + [Ch 22](./22-codeql-global-taint.md)。這是把玩具 query 變生產 query 的最佳教材。
- **MITRE CWE-787 / CWE-190 / CWE-416 / CWE-134 官方條目**（[cwe.mitre.org](https://cwe.mitre.org/)）——四類 bug 的權威定義、Demonstrative Examples、Potential Mitigations。用法：寫某類 query 前讀對應 CWE 的 example，建立「這類 bug 的變寫詞彙庫」，抽 pattern 更快。前提：無。
- **本課 [Ch 23 flow state](./23-codeql-flow-state-models.md) 與 [Ch 43 完整 variant hunt](./43-case-study-variant-hunt.md)**——把本章整數溢位的「同函式近似」升級成 flow state 嚴謹版的方法論母章。用法：Ch 23 學 state 與 models-as-data，Ch 43 看整數溢位 query 從 v1 漏報到 v2 精準的完整迭代。前提：本章。
- **[`advanced_fuzzing`](../advanced_fuzzing/README.md) 的 ASAN / libFuzzer 章**——本章 query 給你「flow 存在」的候選，可達性與 crash 要動態證。用法：學給解碼函式包 harness、用 `-fsanitize=address` build 讓 heap overflow / UAF 當場現形（[Ch 37](./37-static-plus-dynamic.md) 靜態接動態）。前提：本課 [Ch 37](./37-static-plus-dynamic.md)。

四類記憶體安全 query 你都能寫、也知道每類的建模在哪騙你了。但攻擊面不只 native——現代 target 一大半是 web 服務，注入類（deserialization、command injection、SSRF、path traversal）是另一片大陸。下一章跨到 Java/JS/Python，看 web 語言的 source/sink 建模跟 native 差在哪。

→ [Ch 25 Java/JS/Python query](./25-codeql-web-languages.md)
