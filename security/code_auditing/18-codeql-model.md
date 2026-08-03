# Ch 18 — CodeQL 模型：extractor/database/QL

> **目標**：建立 CodeQL 的核心心智模型——**CodeQL 不「掃原始碼」，它把整個程式抽成一個關聯式資料庫（relational database），然後用 QL 這個 Datalog 風格的宣告式查詢語言去查這個資料庫**。把 Ch 3 講的「CPG 的另一種存法是關聯式 table」在這裡踩實：親手對共用靶 `vuln.c` 建 db、跑第一條 QL、看見程式變成一堆可 join 的關係表。
> **環境**：CodeQL 2.26.2，WSL Ubuntu 22.04

你在 Part 1 學過 dataflow / IFDS / points-to / taint 四要素，也在 Ch 3 知道 CPG 可以存成圖（Joern）或存成關聯式資料表（CodeQL）。Part 4 就是把「存成關聯式資料表」這條路走完。這一章不碰 QL 語言細節（Ch 19）、不碰多語言建 db 的實務坑（Ch 20），只做一件事：**把 CodeQL 的三段式 pipeline——extractor → database → QL——的每一段講清楚，並且對 `vuln.c` 真跑一遍**，讓你對「查的是 db 不是原始碼」這句話有肌肉記憶。

## 核心心智模型：程式是資料庫，漏洞是查詢

先把整個 CodeQL 一句話講完：

> **把程式的每一種語法/語意元素（expression、statement、function、variable、type、call、control-flow edge、data-flow edge……）各存成一張關聯式資料表，然後用 QL 寫一條邏輯查詢，join 這些表，select 出符合漏洞 pattern 的那幾列。**

這跟你直覺裡的「靜態分析工具」很不一樣。多數人以為 SAST 是「一個程式讀你的 `.c` 檔，邊讀邊找 pattern」。CodeQL **不是**這樣。CodeQL 分兩個完全分離的階段：

1. **建庫階段（一次性、慢）**：extractor 把程式碼嚼成一個 db。這一步之後，**原始碼就不再被查詢碰了**——查詢只碰 db。
2. **查詢階段（可反覆、相對快）**：你寫 QL query 去查那個 db。改 query 重跑不用重建 db；改原始碼才要重建。

這個分離是 CodeQL 一切行為的根。後面所有「踩雷」——「以為 CodeQL 讀原始碼」「db 過期」「build command 沒涵蓋所有檔案導致漏抽」——全是沒把這個分離放心上的後果。

## pipeline：extractor → database → QL

```
     原始碼                                                查詢結果
   (vuln.c ...)                                          (符合 pattern 的列)
       │                                                       ▲
       │  ┌──────────────┐    ┌──────────────┐   ┌──────────┐  │
       └─►│  EXTRACTOR   │───►│   DATABASE   │──►│    QL    │──┘
          │              │    │              │   │  QUERY   │
          │ C/C++: 跟著   │    │ 一堆關係表：  │   │          │
          │  build 走，每 │    │ exprs        │   │ from     │
          │  編一個 TU 抽 │    │ stmts        │   │ where    │
          │  一次         │    │ functions    │   │ select   │
          │ Py/JS: 直接   │    │ calls        │   └──────────┘
          │  parse        │    │ variables    │        │
          │              │    │ controlflow  │   查的是 db，
          │              │    │ dataflow ...  │   不是原始碼！
          └──────────────┘    └──────────────┘
              建庫階段              建庫階段        查詢階段
             （慢、一次）          （產物）        （快、反覆）
```

**三段各是什麼：**

- **extractor（抽取器）**：語言專屬的前端。C/C++ 的 extractor **跟著你的 build 跑**——你下 `gcc -c vuln.c`，extractor 攔截這個編譯動作，看編譯器實際看到的每一個 translation unit（TU，翻譯單元＝一個 `.c` 加上它展開的所有 `#include`），把 AST/CFG/型別等抽成中介的 TRAP 檔。Python/JS 的 extractor **不需要 build**，直接 parse 原始檔（Ch 20 詳談為何有此差異）。
- **database（資料庫）**：extractor 產物匯入後的關聯式資料庫。它不是一個檔，是一個目錄，裡面 `db-cpp/`（或 `db-python/`）放的就是一堆壓縮過的關係表 + string pool。**QL 標準庫（standard library）把這些底層表包裝成好用的 class**（`FunctionCall`、`Expr`、`Function`……），你平常寫 query 面對的是這些 class，不是裸表。
- **QL query**：`from / where / select` 三段式（Ch 19 正式教）。宣告式：你描述「我要什麼」，不寫「怎麼找」。引擎（內部的 Datalog evaluator）決定 join 順序、求遞迴不動點。

## 為什麼 C/C++ 一定要跟著 build 走

這是 CodeQL 對 C/C++ 最關鍵、也最容易被低估的一點。**C/C++ 的「一份原始碼」不等於「一份實際編譯的程式」**，原因是前處理器（preprocessor）：

- `#ifdef` / `#if`：同一個 `.c` 檔，`gcc -DFOO` 跟 `gcc -DBAR` 編出來的是**兩份不同的 TU**。哪些 code 被編進去、哪些被 `#ifdef` 切掉，只有真正跑 build、帶著真正的 `-D` 旗標才知道。
- `#include`：一個 `.c` 展開所有 header 之後才是完整的 TU。header 在哪、哪個版本，取決於 build 的 `-I` include path。
- 每個 `.o` 對應一次編譯。一個大專案有幾百個 TU，**extractor 要看到每一次編譯呼叫**，才不會漏抽某些檔。

所以 C/C++ 建 db 的形狀是「**用一個 `--command` 把你平常的 build 包起來，讓 extractor 攔截其中每一次編譯**」：

```
codeql database create <db> --language=cpp --command="<你平常的 build 指令>" --source-root=.
```

`--command` 可以是 `make`、`gcc -c foo.c`、`cmake --build .`……只要它會觸發真正的編譯。**extractor 攔截的是編譯器呼叫，不是讀檔**——這就是「跟著 build 走」的字面意思。

這也是 CodeQL 的**進入門檻**：你得先讓專案**能 build**（依賴齊、toolchain 對）。build 不起來，就建不了完整的 db。這正是 Joern（Ch 29–32）拿來對比的賣點——Joern 免 build，直接 parse 給你 AST+CFG，代價是精度（尤其 alias/型別）較粗。**「要不要 build」是 CodeQL 與 Joern 最本質的分野**，Ch 32 會正面比。

## 真跑：對 vuln.c 建 db，再跑第一條 QL

共用靶 `~/audit-lab/vuln.c`：

```c
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
void handle(int fd) {
    char buf[64];
    int len;
    read(fd, &len, sizeof(len));      // source：attacker-controlled len
    char *data = malloc(len);
    read(fd, data, len);
    memcpy(buf, data, len);           // sink：OOB write，len 沒檢查
    free(data);
}
int main(){ handle(0); return 0; }
```

**建 db**（用 `gcc -c vuln.c` 當 build command）：

```bash
cd ~/audit-lab
codeql database create vuln-db --language=cpp --command="gcc -c vuln.c" --source-root=.
```

真跑輸出（節錄尾段，照貼）：

```
Running build command: [gcc, -c, vuln.c]
Running command in /home/ypp/audit-lab: [gcc, -c, vuln.c]
Finalizing database at /home/ypp/audit-lab/vuln-db.
Running TRAP import for CodeQL database at /home/ypp/audit-lab/vuln-db...
Importing TRAP files
Merging relations
Finished writing database (relations: 103.11 KiB; string pool: 2.13 MiB).
TRAP import complete (1s).
Successfully created database at /home/ypp/audit-lab/vuln-db.
```

讀這段輸出的關鍵：`Running build command: [gcc, -c, vuln.c]`——extractor **確實去跑了 gcc**，攔截這次編譯。`Merging relations` / `relations: 103.11 KiB`——產物是**關係（relations）**，也就是關聯式資料表。整個小檔約 3 秒（大專案幾分鐘到幾小時，看 TU 數量）。

**跑最簡 QL：找出所有 function call。** 先在一個 query 目錄放 `qlpack.yml` 宣告依賴標準庫：

```yaml
# qlpack.yml
name: audit-tests
version: 0.0.1
dependencies:
  codeql/cpp-all: "*"
```

query `all-calls.ql`：

```ql
import cpp
from FunctionCall c
select c, "call to " + c.getTarget().getName()
```

跑：

```bash
codeql query run all-calls.ql -d ~/audit-lab/vuln-db
```

真跑輸出（照貼）：

```
Compiling query plan for /home/ypp/audit-lab/qltest/all-calls.ql.
[1/1 comp 19.4s] Compiled /home/ypp/audit-lab/qltest/all-calls.ql.
Starting evaluation of audit-tests/all-calls.ql.
Evaluation completed (791ms).
|             c             |           col1            |
+---------------------------+---------------------------+
| call to __builtin_bswap64 | call to __builtin_bswap64 |
| call to __builtin_bswap32 | call to __builtin_bswap32 |
| call to __builtin_bswap16 | call to __builtin_bswap16 |
| call to handle            | call to handle            |
| call to read              | call to read              |
| call to malloc            | call to malloc            |
| call to read              | call to read              |
| call to memcpy            | call to memcpy            |
| call to free              | call to free              |
```

兩件事值得停下來看：

1. **`read` 出現兩次**——因為原始碼裡 `read()` 呼叫了兩次。db 忠實記錄**每一個 call site**，不是每個被呼叫的函式。這是「關係表存的是事實列」的直接體現。
2. **冒出 `__builtin_bswap*`**——這些不在原始碼裡，是 `#include <string.h>` 展開後、header 裡的 inline/builtin 被 extractor 一起抽進來了。**這證明 db 存的是「編譯器看到的完整 TU」，不是你眼睛看到的那 12 行**。這正是「跟著 build 走」的價值：它抓到了展開後的真實編譯內容。

**收窄到只找 `memcpy`**（`memcpy-calls.ql`）：

```ql
import cpp
from FunctionCall c
where c.getTarget().getName() = "memcpy"
select c, "memcpy at line " + c.getLocation().getStartLine()
```

真跑輸出：

```
Evaluation completed (167ms).
|       c        |       col1        |
+----------------+-------------------+
| call to memcpy | memcpy at line 10 |
```

一條 `where` 就把 9 個 call 過濾成 1 個，並且透過 `getLocation()` 拿到它在第 10 行——**db 保存了位置資訊**，所以查詢結果能指回原始碼行號（給人看 / 產報告用）。注意：**同一個 db，換 query 重跑不用重建**。第二條 query 沒有再跑 gcc，直接查既有的 `vuln-db`。這就是「建庫慢一次、查詢快多次」的分離價值。

## QL 與 SQL：像在哪、差在哪

「查關聯式資料庫」很容易讓人想到 SQL。這個類比對，但要知道差在哪，否則會用 SQL 的思維寫 QL 撞牆（Ch 19 踩雷）。

| 面向 | SQL | QL |
|---|---|---|
| 資料 | 你設計的業務表 | extractor 產生的程式元素表（固定 schema） |
| 查詢語意 | 集合／關聯代數 | 邏輯／集合（Datalog 家族） |
| 基本形狀 | `SELECT ... FROM ... WHERE` | `from ... where ... select` |
| **遞迴** | 要 `WITH RECURSIVE`，笨重 | **一級公民**：predicate 直接遞迴，引擎自動求最小不動點 |
| **抽象** | 沒有型別階層 | **class 階層**：`FunctionCall` 是一種 `Expr`，可繼承、可 override predicate |
| join | 你手寫 `JOIN ... ON` | 多半隱含在 predicate 呼叫與變數共用裡 |

兩個差異對審計是**決定性**的：

- **遞迴是一級公民**。「A 呼叫 B、B 呼叫 C…… transitively 誰能到達 `memcpy`」——在 SQL 要 `WITH RECURSIVE` 手刻，在 QL 就是一條會自我呼叫的 predicate，引擎自動算到不動點。這正是 Ch 4 的 fixpoint 在 QL 裡的化身：**QL 的遞迴 predicate ＝ 求最小不動點（least fixpoint）**。call graph 的傳遞閉包、taint 的傳播閉包，本質都是這個。Ch 19 會親手寫一條遞迴 call-chain query 看它跑。
- **class 階層讓你抽象漏洞概念**。你可以定義 `class DangerousFunction extends Function`，把「危險函式」這個審計概念變成一個型別，之後所有 query 復用。SQL 沒有這層抽象。這是 CodeQL 能把一大套 CWE 查詢庫組織起來的根。

一句話：**QL ≈ 「有 class 階層、遞迴當一級公民的 Datalog」，跑在一個「程式被抽成關係表」的資料庫上**。把 SQL 當入門直覺可以，但真正的血緣是 Datalog（Ch 19 展開）。

## 踩雷集錦

**錯誤直覺：「CodeQL 讀我的原始碼去找 pattern。」**
正確認識：CodeQL 查的是 **db**，不是原始碼。原始碼只在**建庫**時被 extractor 碰一次，之後查詢完全不看原始檔。這條的實務後果：(a) 改了原始碼、db 沒重建，你查的還是舊碼（下一條）；(b) 沒進 db 的東西（被 `#ifdef` 切掉的、build 沒編到的檔）**查詢永遠看不到**，不管 query 寫多好。「query 沒報 = 沒 bug」在 db 不完整時是空話。

**錯誤直覺：「db 建一次就永遠能用。」**
正確認識：**db 是原始碼在某個時間點的快照。** 改了原始碼，db 就過期——你查的是舊事實。實務上最陰的版本是：你在 debug 一條 query，改 code 想看行為變化，卻忘了重建 db，於是「怎麼改結果都一樣」，浪費半小時。鐵律：**改 query 不用重建 db，改被分析的原始碼一定要重建 db。**

**錯誤直覺：「build command 隨便給一個能過就好。」**
正確認識：C/C++ 的 db 完整性**完全等於 build command 的覆蓋度**。`--command` 只編了一半的 TU（例如只 `make` 了某個子目錄、或某些 `#ifdef` 分支沒被觸發），另一半就**根本不在 db 裡**。這種漏抽是**靜默**的——db 建成功、query 跑得動、就是掃不到那些檔，你以為乾淨其實半個專案沒看。建完 db 一定要抽查「該進來的檔 / 該有的行數進來了沒」（Ch 20 教怎麼驗）。

**錯誤直覺：「query 慢是 db 太大，換小 db 就好。」**
正確認識：查詢慢多半是 **query 寫法**（沒 bound 的變數、笛卡兒積、遞迴沒收斂），不是 db 大小。同一個 `vuln-db` 上，`all-calls.ql` 第一次要 19 秒是**編譯 query plan**（Compiling），真正 evaluation 只有 791ms；換 query 那 19 秒又要付一次（不同 query 不同 plan）。分清「compile 時間」與「evaluation 時間」，才不會誤診效能問題（Ch 28 深談）。

**錯誤直覺：「QL 就是 SQL 換個語法。」**
正確認識：像但不是。用 SQL 的命令式殘留思維（想「先掃這張表，再迴圈 join 那張」）寫 QL 會撞「變數沒 bound」的牆（Ch 19 會真的撞給你看）。QL 是宣告式 + Datalog：你描述關係，引擎決定執行。而且它有 SQL 沒有的遞迴一級公民與 class 階層——這兩個才是它為審計而生的地方。

## 進階延伸

- **TRAP 檔與 db 內部**：extractor 不直接寫 db，先產 **TRAP（.trap）** 中介檔（一堆「插入這條關係」的指令），再 import/merge 成 db。想理解「漏抽」為什麼靜默，可以看 db 目錄的 `log/` 與 `diagnostic/`——extractor 的抽取診斷（哪些檔沒抽到、為何）都在裡面。Ch 20 會用它驗證 db 完整性。
- **db 是可攜的**：db 目錄可以打包丟給別人，對方不用你的原始碼與 toolchain 就能查。GitHub 的 code scanning、以及 Ch 27 的 MRVA（多 repo 變體分析），底層都靠「db 可攜」這個性質——先各自建好 db，再對一堆 db 跑同一條 query。
- **QL 標準庫是 db 之上的抽象層**：你 `import cpp` 拿到的 `FunctionCall`、`Expr`、`DataFlow` 這些 class，全是標準庫在裸關係表上封裝出來的。真正想搞懂某個 class 對到哪張底層表，可以讀標準庫原始碼（bundle 裡的 `.qll`）。Ch 19 會拆這層。

## 本章重點整理

- **CodeQL 的心智模型：程式 → 關聯式資料庫；漏洞 → 對這個 db 的 QL 查詢。** 這是 Ch 3「CPG 存成關聯式 table」的字面落地。
- **pipeline 三段**：extractor（跟 build 走或直接 parse）→ database（一堆關係表 + 標準庫封裝的 class）→ QL query（`from/where/select`，宣告式）。建庫慢且一次，查詢快且可反覆。
- **查的是 db，不是原始碼**。原始碼只在建庫時被碰一次。沒進 db 的東西查詢永遠看不到；改原始碼要重建 db，改 query 不用。
- **C/C++ 必須跟 build 走**，因為 `#ifdef`/`#include`/多 TU 讓「一份原始碼」≠「一份實際編譯的程式」；build 覆蓋度 = db 完整度。這也是 CodeQL 相對 Joern 的門檻（Ch 32）。
- **QL ≈ 帶 class 階層、遞迴為一級公民的 Datalog**。遞迴 predicate ＝ 求最小不動點（對回 Ch 4 fixpoint），這是 call graph / taint 傳播閉包的引擎。

## 自我檢核

- 不看上文，畫出 extractor → database → QL 的 pipeline，並說明每一段的輸入/輸出、哪段慢哪段快、哪段碰原始碼哪段不碰。
- 為什麼 `all-calls.ql` 的輸出裡會出現原始碼裡沒寫的 `__builtin_bswap64`？這證明了 db 存的是什麼？
- 用主動回憶說出：改了原始碼要不要重建 db？改了 query 要不要？各為什麼？
- C/C++ 為什麼一定要用 `--command` 跟 build 走，而 Python 不用？用 `#ifdef` 舉一個「build command 沒覆蓋導致靜默漏抽」的具體情境。
- QL 跟 SQL 最關鍵的兩個差異是什麼？為什麼這兩個差異對「寫漏洞查詢」是決定性的？

## 延伸閱讀

- **CodeQL 官方文件 *About the CodeQL database*（codeql.github.com/docs → CodeQL overview → “About the CodeQL database”）**——親眼確認「程式被存成關聯式 table」這句話，看官方怎麼描述 extractor 與 db 的關係。前提：本章。這是 Ch 3「圖的另一種存法」與本章的權威對照。
- **CodeQL 官方文件 *Preparing your code for CodeQL analysis*（同站 → “Creating CodeQL databases”）**——C/C++ 為何要 `--command`、autobuild 與手動 build command 的差別，本章「跟著 build 走」的官方版。前提：本章。銜接 Ch 20 建 db 實務。
- **CodeQL 官方文件 *QL language reference* 開頭的 *QL for Datalog programmers* / *for SQL programmers* 對照頁**——官方給的「你懂 SQL/Datalog，QL 對你來說是什麼」對照，把本章「QL ≈ 有 class 階層與遞迴的 Datalog」講到位。前提：本章 + 你對 SQL 的基本熟悉。銜接 Ch 19。
- **Yamaguchi et al., *Code Property Graphs*, IEEE S&P 2014, Section III**——回頭讀 CPG 定義，對照「同一個 CPG 概念，CodeQL 用關聯式 table 存、Joern 用圖存」。前提：Ch 3。這條把 Part 4 接回理論根。

我們已經知道 CodeQL 把程式變成 db、查的是 db、pipeline 長怎樣，也跑了兩條最簡查詢感受過 `from/where/select`。下一章正式拆 QL 語言本體——predicate、class、`exists`、遞迴、aggregation——把「怎麼寫查詢」講透，並在 `vuln.c` 上跑一整組漸進的 query，一路寫到遞迴找 call chain。

→ [Ch 19 QL 語言核心](./19-ql-language-core.md)
