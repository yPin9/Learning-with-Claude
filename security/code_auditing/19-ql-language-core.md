# Ch 19 — QL 語言核心

> **目標**：把 QL 語言本體講透——`from/where/select`、**predicate（謂詞）**、**class（類別）**、`exists`、遞迴 predicate、aggregation（`count`/`sum`）、charpred（characteristic predicate，特徵謂詞）。核心是把 QL 認成 **Datalog 家族的宣告式語言**，用集合語意與最小不動點的直覺去寫它，而不是帶著 C/Python 的命令式殘留。全章在 `vuln.c` 上跑一整組漸進 query，一路寫到遞迴找 call chain，並真的撞一次「變數沒 bound」的牆再修好。
> **環境**：CodeQL 2.26.2，WSL Ubuntu 22.04

Ch 18 建立了心智模型：程式是關聯式 db，漏洞是查詢。這章教「查詢怎麼寫」。QL 不難，但它宣告式、集合語意、遞迴一級公民——這三點跟你寫慣的命令式語言直覺相反，是所有初學者卡關的地方。我會**先給 Datalog 直覺，再逐個語言構件配真跑範例**，最後把踩雷集中在「命令式思維寫 QL」這條主線上。

## 先給直覺：QL 是 Datalog，不是迴圈

命令式語言你這樣想：「開一個 list，for 迴圈掃過所有 call，if 名字是 memcpy 就 append。」QL **不是這樣**。QL 你這樣想：

> **「所有滿足『是一個 FunctionCall，且 target 名字是 memcpy』這個邏輯條件的元組（tuple），構成一個集合。把這個集合 select 出來。」**

沒有迴圈、沒有順序、沒有可變狀態。你**描述一個集合的成員資格條件**，引擎去算出這個集合。這就是 Datalog：**用邏輯規則定義關係，引擎求解**。

三個直接後果，先記著，後面每個構件都會回來印證：

1. **每個變數都必須「被 bound」到 db 裡的有限集合。** `from int x` 而不約束 `x`，引擎沒辦法列舉「所有整數」——它會拒編（本章會真的撞給你看）。變數的值域必須來自 db 的某張關係。
2. **`where` 是合取（conjunction）**：多個條件用 `and` 串，語意是「同時滿足」，跟寫的順序無關。
3. **遞迴 predicate = 最小不動點**：predicate 自我呼叫，引擎從空集合開始反覆代入直到不再變大（fixpoint）。這正是 Ch 4 dataflow 的 fixpoint，只是換了語言外衣。

## `from / where / select`：查詢的骨架

一條完整 QL query 三段：

```ql
import cpp                              // 載入 C/C++ 標準庫

from FunctionCall c                     // 宣告變數與其型別（值域＝db 裡所有 FunctionCall）
where c.getTarget().getName() = "malloc"  // 過濾條件（合取）
select c, c.getLocation().getStartLine()  // 輸出哪些欄
```

- `from`：宣告變數，型別決定值域。`FunctionCall c` 意思是「c 跑遍 db 裡所有函式呼叫」。
- `where`：布林條件，把 `from` 的笛卡兒積過濾成滿足條件的元組集合。
- `select`：每個滿足的元組輸出哪些欄。

**真跑（query 1：找 malloc 呼叫）** `q1-malloc.ql`：

```ql
import cpp
from FunctionCall c
where c.getTarget().getName() = "malloc"
select c, c.getLocation().getStartLine()
```

輸出（照貼）：

```
Evaluation completed (167ms).
|       c        | col1 |
+----------------+------+
| call to malloc |    8 |
```

`vuln.c` 第 8 行的 `malloc(len)`。骨架跑通了。

## predicate：把條件抽成可復用的邏輯

**predicate（謂詞）** 是 QL 的函式，但語意是「一個關係 / 一個布林條件」。它有兩種：

- **回傳布林的 predicate**（用 `predicate` 宣告）：定義「一組元組滿不滿足某條件」。
- **回傳值的 predicate**（用回傳型別宣告，像 `int getSize()`）：定義一個從輸入到輸出的關係。

命名慣例（跟標準庫一致，照做省得別人看不懂）：

- 布林 predicate：動詞開頭，如 `isSource(...)`、`callsDirectly(...)`。
- 回傳值 predicate：`get` 開頭，如 `getTarget()`、`getArgument(int)`。
- 述語化的形容詞：`is` 開頭。

例：把「危險函式呼叫」抽成一個 predicate：

```ql
predicate isDangerousCall(FunctionCall c) {
  c.getTarget().getName() = ["memcpy", "strcpy", "strcat", "sprintf", "gets"]
}
```

`["memcpy", ...]` 是 QL 的**集合字面量**：`= [...]` 意思是「等於其中任一個」（不是陣列，是「屬於這個集合」）。這比寫五個 `or` 乾淨。

## class：有性質的一組 program element

**class 是 QL 最核心的抽象**，但它的意思跟 OOP 的 class 不一樣。QL 的 class **不是「物件的模板」，而是「一個子集合的定義」**：

> **一個 class 定義了「db 裡符合某條件的那一組 program element」。** `class DangerousFunction extends Function { ... }` 的意思是「`DangerousFunction` 是所有 `Function` 裡，滿足某條件的那個子集合」。

決定「哪些 element 屬於這個 class」的，是 **charpred（characteristic predicate，特徵謂詞）**——一個與 class 同名的 predicate，寫在 class body 裡：

```ql
class DangerousFunction extends Function {
  DangerousFunction() {                        // ← charpred：與 class 同名，無回傳型別
    this.getName() = ["memcpy", "strcpy", "strcat", "sprintf", "gets"]
  }
}
```

`this` 指「候選的 element」，charpred 是「這個候選要不要算進這個 class」的條件。**class 的成員 = 所有讓 charpred 為真的 element**。定義了 class，就能像用型別一樣用它：`from DangerousFunction f` 讓 `f` 跑遍所有危險函式。

**真跑（query 3：用 class 抓危險函式呼叫）** `q3-dangerous-class.ql`：

```ql
import cpp

class DangerousFunction extends Function {
  DangerousFunction() {
    this.getName() = ["memcpy", "strcpy", "strcat", "sprintf", "gets"]
  }
}

from FunctionCall c, DangerousFunction f
where c.getTarget() = f
select c, "dangerous call: " + f.getName()
```

輸出（照貼）：

```
Evaluation completed (219ms).
|       c        |          col1          |
+----------------+------------------------+
| call to memcpy | dangerous call: memcpy |
```

`c.getTarget() = f` 這一行是關鍵：`c` 跑遍所有 call、`f` 跑遍所有危險函式，`= f` 把兩者 join——只留下「target 剛好是某個危險函式」的 call。這就是 Datalog 的 join：**兩個變數在同一條件裡相等，就是一次關聯**。

class 為什麼是審計的地基：**你可以把一整套審計概念（危險 sink、taint source、sanitizer）各定義成一個 class，之後所有 query 復用**。CodeQL 官方那套龐大的 CWE 查詢庫，就是靠 class 階層組織起來的——`RemoteFlowSource`、`SystemCommandExecution` 這些都是 class。

## exists：引入「存在一個中間值」的局部變數

`exists(<宣告> | <條件>)` 意思是「**存在**至少一個滿足條件的值」。它的用途是**在 `where` 裡引入一個臨時變數而不把它放進 `from`**（因為你不想輸出它，只想用它當中介）。

例：找「同一個函式裡同時呼叫了 malloc 和 free」的函式——你需要兩個 call 當中介，但只想輸出函式：

```ql
import cpp
from Function f
where exists(FunctionCall a | a.getEnclosingFunction() = f and a.getTarget().getName() = "malloc")
  and exists(FunctionCall b | b.getEnclosingFunction() = f and b.getTarget().getName() = "free")
select f, "allocates and frees"
```

`a`、`b` 是 `exists` 的局部變數，不出現在 `select`。**沒有 `exists` 的話，把 `a`、`b` 放進 `from` 也能查，但那樣會輸出「函式 × call」的組合列，重複又囉嗦**。`exists` 讓你說「我只要知道存在，不要列舉」。

## aggregation：count / sum，與必須先 bound 的坑

**aggregation（聚合）** 對一個集合算數量/總和。`count(<宣告> | <條件>)` 數滿足條件的元組個數。

我先寫一個**會失敗**的版本，因為它踩到 QL 最經典的坑，值得親眼看。`q5-count.ql`：

```ql
import cpp
from string name, int n
where n = count(FunctionCall c | c.getTarget().getName() = name)
select name, n order by n desc
```

意圖：對每個函式名 `name`，數它被呼叫幾次。真跑輸出（照貼）：

```
Compiling query plan for /home/ypp/audit-lab/qltest/q5-count.ql.
ERROR: 'name' is not bound to a value. (/home/ypp/audit-lab/qltest/q5-count.ql:2,13-17)
```

**這就是「變數沒 bound」的牆。** `name` 宣告成 `string`，但 `where` 裡唯一用到 `name` 的地方是在 `count(...)` 的**內部**當條件。QL 要先能**列舉 `name` 的所有可能值**，才能對每個值去數。可是「所有 string」是無限的、不在 db 的某張關係裡——引擎沒辦法列舉，於是拒編。**Datalog 的鐵律：每個變數必須被 bound 到 db 的某個有限關係。**

修法：先讓 `name` 從 db 的某張關係取值（「所有實際被呼叫的函式名」），再去數。`q5-count-fixed.ql`：

```ql
import cpp
from string name, int n
where name = any(FunctionCall fc).getTarget().getName()   // ← 先 bound name
  and n = count(FunctionCall c | c.getTarget().getName() = name)
select name, n order by n desc
```

`any(FunctionCall fc).getTarget().getName()` 意思是「任一 FunctionCall 的 target 名字」——這把 `name` 綁定到「db 裡實際出現過的函式名」這個有限集合。真跑輸出（照貼）：

```
Evaluation completed (364ms).
|       name        | n |
+-------------------+---+
| read              | 2 |
| malloc            | 1 |
| __builtin_bswap64 | 1 |
| memcpy            | 1 |
| __builtin_bswap16 | 1 |
| free              | 1 |
| handle            | 1 |
| __builtin_bswap32 | 1 |
```

`read` 被叫 2 次（對回 Ch 18 的觀察），其餘各 1。**同一個查詢差一行——bound 沒 bound——差在能不能編。** 這是 QL 最該內化的直覺：寫 `where` 時永遠問「這個變數的值從 db 的哪張關係來？」

## 遞迴 predicate：call chain 與最小不動點

壓軸。遞迴 predicate 是 QL 相對 SQL 的殺手級能力，也是 taint / call graph 傳遞閉包的引擎。

先定義「直接呼叫」關係，再用它遞迴定義「傳遞可達」：

```ql
import cpp

predicate callsDirectly(Function caller, Function callee) {
  exists(FunctionCall c |
    c.getEnclosingFunction() = caller and c.getTarget() = callee)
}

predicate reaches(Function src, Function dst) {
  callsDirectly(src, dst)                                  // base case：直接呼叫
  or
  exists(Function mid | callsDirectly(src, mid) and reaches(mid, dst))  // 遞迴：src→mid→...→dst
}

from Function f, Function target
where reaches(f, target) and target.getName() = "memcpy" and f.hasDefinition()
select f, "transitively reaches -> " + target.getName()
```

`reaches` 自我呼叫：一個 base case（直接呼叫）加一個遞迴 case（先走一步到 `mid`，再從 `mid` 遞迴到 `dst`）。**引擎怎麼算它？從空集合開始，反覆代入 `callsDirectly` 與已算出的 `reaches`，直到 `reaches` 不再變大——這就是最小不動點（Ch 4）。** 遞迴 predicate 一定要有 base case（這裡是 `callsDirectly(src, dst)`），否則永遠是空集合（下面踩雷）。

真跑輸出（照貼）：

```
Evaluation completed (358ms).
|   f    |              col1              |
+--------+--------------------------------+
| main   | transitively reaches -> memcpy |
| handle | transitively reaches -> memcpy |
```

`handle` 直接呼叫 `memcpy`（base case 命中）；`main` 呼叫 `handle`、`handle` 呼叫 `memcpy`，所以 `main` **傳遞**可達 `memcpy`（遞迴 case 命中）。**這正是 call graph 的傳遞閉包，一條遞迴 predicate 就表達完**——SQL 要 `WITH RECURSIVE` 手刻的東西，在 QL 是語言的自然形狀。Ch 22 的 global taint 底層就是這種可達性，只是 source/sink 換成 taint 的邊。

QL 也提供**傳遞閉包運算子** `+`（一次或多次）與 `*`（零次或多次）當語法糖：`callsDirectly+(f, target)` 等價於上面手寫的 `reaches`。手寫遞迴讓你看清機制，實務多用 `+`/`*` 更簡潔。

## QL library 結構與 import

你每條 query 開頭的 `import cpp`，載入的是 C/C++ 標準庫（`cpp.qll` 及其一路 import 的一大堆 `.qll`）。標準庫把 db 的裸關係表封裝成 class（`Expr`、`Function`、`FunctionCall`、`Variable`、`ControlFlowNode`……）與 predicate。**你平常寫 query 面對的全是這層封裝，不碰裸表。**

- `.ql` 檔：可執行的 query（有 `from/where/select` 或 `select`）。
- `.qll` 檔：library，放 class 與 predicate 給別的檔 `import`，本身不執行。
- `import cpp`（C/C++）、`import python`（Python）、`import javascript`（JS）——各語言的頂層庫。dataflow 另外 `import semmle.code.cpp.dataflow.new.DataFlow`（Ch 21 起）。

命名慣例再強調（跟標準庫一致才好維護）：class 大寫駝峰（`DangerousFunction`）、predicate 小寫駝峰動詞開頭（`isDangerousCall`、`getSize`）、charpred 與 class 同名。

## 踩雷集錦

**錯誤直覺：「用 for 迴圈的思維寫 QL——先掃這個集合，再對每個元素做那個。」**
正確認識：QL 沒有迴圈、沒有順序、沒有可變狀態。你**描述元組集合的成員條件**，引擎求解。「掃過所有 call」在 QL 是 `from FunctionCall c`（c 就是集合成員），不是你寫迴圈。帶著命令式殘留，你會想「先算出這個 list 再傳給下一步」——QL 裡沒有「步驟」，只有一組同時成立的邏輯條件。

**錯誤直覺：「變數宣告了就能用。」**
正確認識：每個變數必須 **bound** 到 db 的某個有限關係。`from string name` 而 `name` 只出現在某個 `count`/`exists` 的**內部條件**裡，引擎無法列舉「所有 string」，直接拒編（`'name' is not bound to a value`，本章真的撞了）。永遠問：「這個變數的值從 db 的哪張關係來？」bound 它的最常見手段是讓它 `= 某個 predicate 的回傳` 或 `= any(...)`。

**錯誤直覺：「遞迴 predicate 不用 base case 也能跑。」**
正確認識：遞迴 predicate 用最小不動點求解，**從空集合開始**。沒有 base case（非遞迴的那一支），第一輪代入什麼都得不到，永遠是空集合——query 跑得動、就是**結果永遠空**，最陰的那種「沒報 = 以為沒 bug」。`reaches` 一定要有 `callsDirectly(src, dst)` 這條非遞迴的 base case 撐起第一輪。

**錯誤直覺：「charpred 可以隨便寫，反正 class 就是個殼。」**
正確認識：charpred **就是 class 的定義本身**——它決定「哪些 element 屬於這個 class」。charpred 寫太寬，class 成員太多，之後所有用到這個 class 的 query 全部 over-match（誤報源頭）；寫太窄則漏。而且 charpred 裡的 `this` 必須被約束到有限集合（跟 bound 同一條鐵律），charpred 條件全空 = 這個 class 等於它的父型別（`DangerousFunction` 變成「所有 Function」）。

**錯誤直覺：「`exists` 只是省變數，可有可無。」**
正確認識：`exists` 有語意——它說「**存在**至少一個」，而且把變數**局部化**（不出現在 `select`、不參與輸出的笛卡兒積）。該用 `exists` 卻把中間變數丟進 `from`，結果會多出重複列（每個滿足的中間值各一列），你以為有很多 hit 其實是同一個目標被列了 N 次。「我只要知道存在、不要列舉」就用 `exists`。

## 進階延伸

- **predicate 的 binding set 與 magic 優化**：QL 編譯器會分析每個 predicate 的哪些參數必須先 bound（binding set），並做 "magic sets" 之類的優化把約束往內推。你寫 query 時的「先 bound 再用」直覺，其實是在配合這套機制。Ch 28（query performance）會把「join order / binding」跟跑得快不快連起來。
- **abstract class 與多 override**：class 可以 `abstract`，由多個子 class 各自貢獻成員（成員是各子 class charpred 的**聯集**）。標準庫的 `RemoteFlowSource`（Ch 22 taint source）就是 abstract class，各語言/各框架的 source 各自 extend 它——這是「把 source 定義分散給各模組再自動合流」的機制。
- **傳遞閉包運算子的精確語意**：`p+` 是「p 一次以上」，`p*` 是「p 零次以上（含自反）」。用錯（該 `+` 用了 `*`）會把「自己到自己」也算進去，在「找 A 是否能到達不同的 B」時混入自環。手寫遞迴時對應的就是「base case 用 `callsDirectly` 還是也含 `src = dst`」。

## 本章重點整理

- **QL 是 Datalog 家族的宣告式語言**：描述元組集合的成員條件，引擎求解。沒有迴圈/順序/可變狀態。`where` 是合取。
- **每個變數必須 bound 到 db 的有限關係**——這是最常撞的牆（`not bound to a value`）。寫條件時永遠問「這變數的值從哪張關係來」。
- **class = db 裡符合 charpred 的那個子集合**；charpred（與 class 同名的 predicate）就是 class 的定義。class 是把審計概念（sink/source/sanitizer）抽象復用的地基。
- **predicate** 是關係/布林條件，命名慣例：`is*` 布林、`get*` 回傳值。**`exists`** 引入不輸出的局部中介變數。**aggregation**（`count`/`sum`）對集合算量，內部變數同樣要先 bound。
- **遞迴 predicate = 最小不動點**（對回 Ch 4），是 call graph / taint 傳遞閉包的引擎；一定要有 base case，否則結果恆空。`+`/`*` 是傳遞閉包語法糖。

## 自我檢核

- 不看上文，用「集合成員條件」而非「迴圈」的說法，解釋 `from FunctionCall c where c.getTarget().getName() = "malloc" select c` 在做什麼。
- 為什麼 `from string name where n = count(...name...)` 會報 `'name' is not bound`？怎麼修？「bound」到底要求什麼？
- class 的 charpred 是什麼、決定什麼？如果一個 class 的 charpred body 完全空著，這個 class 的成員是誰？
- 手寫一條遞迴 predicate `reaches(a, b)` 表達「a 傳遞呼叫到 b」，指出哪一支是 base case、少了它會怎樣。它跟 `callsDirectly+` 什麼關係？
- 什麼時候該用 `exists` 而不是把變數放進 `from`？不用會有什麼可觀察的後果？

## 延伸閱讀

- **CodeQL 官方文件 *QL language reference*（codeql.github.com/docs → “QL language reference”）的 *Predicates*、*Classes*、*Aggregations*、*Recursion* 四節**——本章每個構件的權威定義，尤其 *Recursion* 節對「遞迴＝最小不動點」與 stratification 的說明。前提：本章。務必讀 *Variables and binding* 那段，把「bound」講死。
- **CodeQL 官方文件 *CodeQL for C/C++* → *Introducing the QL libraries for C and C++***——`Expr`/`Function`/`FunctionCall` 這些 class 的階層與常用 predicate，本章 `import cpp` 之後拿到的那一層。前提：本章。這是你之後寫任何 C/C++ query 的查表起點。
- **CodeQL 官方文件 *Learning CodeQL* → *QL tutorials*（含 “Introduction to QL”）**——官方的漸進練習，把 `from/where/select`、predicate、class、遞迴一路帶過，跟本章互補（本章重機制，教程重手感）。前提：本章。做完再回來寫 Ch 21 的 dataflow query 會順很多。
- **CodeQL 標準庫原始碼裡的 `RemoteFlowSource.qll` 與 `Security` 相關 `.qll`（bundle 內 `cpp/ql/lib/semmle/...`）**——看真實世界的 class/charpred 怎麼組織一整套 source/sink 定義。前提：本章 + 你敢讀 library 原始碼。銜接 Ch 22 taint。

QL 語言本體、class 抽象、遞迴不動點都到手了，也親手撞過「沒 bound」的牆並修好。下一章回到工程面：把「怎麼對各種語言建 db」講清楚——C/C++ 追 build 的坑、Python/JS 免 build、自建一個 python 檔建 python db 證明多語言都通，並學會驗證「該進 db 的東西真的進來了」。

→ [Ch 20 建 database](./20-codeql-databases.md)
