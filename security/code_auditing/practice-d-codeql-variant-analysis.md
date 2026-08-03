# 練習 D — CodeQL variant analysis（全課核心練習）

> **目標**：把 Part「CodeQL」（Ch 18-28）拼成一件能交付的成果——完整走一遍 **variant analysis（變體分析）**。你從一個「已知一處 bug」出發，建 CodeQL database、寫一條 **global taint 的 path-problem query**，在一個「藏了多處同型變體、又埋了誤報誘餌」的 C 專案上跑，要**剛好抓到所有未修變體、放過已修與誤報**，並輸出帶 flow path 的結果與 SARIF。做完你會真的擁有這條天梯的完整肌肉記憶：source/sink/barrier 怎麼定（Ch 09、22、23）、path-problem 怎麼寫、跨函式 flow 怎麼追、驗收怎麼卡真陽性與零誤報、SARIF 怎麼出（Ch 39）。這是全課的核心練習，不是熱身。
> **環境**：WSL，`codeql` 2.26.2（PATH 已含 `~/audit-tools/codeql`）。共用靶目錄 `~/audit-lab`。參考解答的 QL、建 db、跑 query **全部真跑並照貼輸出（含完整 flow path）**。

## 任務規格

### 靶：一份藏了變體與誘餌的 C 專案

你要分析一個小型 C 網路服務的解析路徑。它有一個**已知 bug 的形狀**：從 socket 讀進來的長度（attacker-controlled）直接當成 `memcpy` 的 size，沒經 bound check，造成 stack buffer 的 out-of-bounds write（CWE-787）。

這份 code **刻意**包含五種情況，你的 query 必須精確區分：

| 函式 | 情況 | 你的 query 應該 |
|---|---|---|
| `handle_fixed` | 已修：`memcpy` 前有 `if (len < 0 \|\| len > sizeof(buf)) return;` bound check | **放過**（negative） |
| `handle_v1` | 未修變體：`len` 直接進 `memcpy` size | **抓到**（true positive） |
| `handle_v2` | 未修變體：`len` 經算術（`len + 8`）再進 `memcpy` | **抓到** |
| `handle_v3` | 未修變體：`len` 經另一個函式（`copy_into`）跨函式進 `memcpy` | **抓到** |
| `handle_decoy` | 誤報誘餌：`len` 經 `clamp()` sanitizer 夾到安全範圍才用 | **放過**（negative） |

### 驗收標準（明確、可機器檢查）

- 命中數**恰好 3**：`handle_v1`、`handle_v2`、`handle_v3` 的 `memcpy` 各一。
- **0 誤報**：`handle_fixed`（有 bound check）與 `handle_decoy`（有 clamp sanitizer）都**不得**命中。
- 每個命中都輸出**完整 flow path**：從 source（socket read 寫進的變數）一路到 sink（`memcpy` 的 size 參數），跨函式的變體要能看到跨 `read_len` / `copy_into` 的步驟。
- 輸出一份 **SARIF**（[Ch 39](./39-sarif-ecosystem.md)），內含上述 3 個結果與它們的 code flow。

「恰好 3、0 誤報」是這個練習的靈魂：**抓到全部真的、同時放過已修與誤報**，才叫變體分析做對了。少一個是漏報（barrier 建錯把真的擋掉），多一個是誤報（barrier 沒建對把假的放進來）——兩邊都要卡準。

## 分五步

### Step 1：把靶寫出來、建 CodeQL database

把下面「參考解答」裡的 `proto.c` 放進一個目錄，用 `codeql database create` 建 C database（[Ch 20](./20-codeql-databases.md)）。C database 要能 build，這裡用 `gcc -c` 當 build command 即可。

### Step 2：定 source / sink（Ch 09、22）

- **source**：socket 讀進來的長度。在靶裡是 `read_len()` 裡 `read(fd, &n, sizeof(n))` 寫進的 `n`——用 `asDefiningArgument()` 抓「被 `read` 寫入的那個 out-parameter」。
- **sink**：`memcpy` 的第 3 個參數（size），即 `getArgument(2)`。

先只寫 source/sink、**不加 barrier**，跑一次。你會看到**5 個命中**（3 個變體 + fixed + decoy 全中）——這是正常的中間狀態，證明 flow 有連通。

### Step 3：加 barrier 放過 fixed 與 decoy（Ch 23）

現在把誤報砍掉，這是最難也最關鍵的一步：

- **decoy 的 barrier**：`clamp()` 是顯式 sanitizer，它的回傳值不該再帶 taint。用 `isBarrier`：taint node 是「`clamp` 呼叫的回傳運算式」時，切斷。
- **fixed 的 barrier**：`handle_fixed` 用的是**在 sink 上的 guard**（`if (len > sizeof(buf)) return;` 控制了 `memcpy` 所在的 basic block）。這種「被 relational comparison guard 控制的 sink」用一個 `guardedSink` predicate 在最後把它濾掉。

### Step 4：跑出「恰好 3、0 誤報」並看 flow path

用 `codeql database analyze` 出 SARIF，確認命中數是 3、位置是三個變體的 `memcpy`。展開每個命中的 code flow，確認跨函式變體（v3）的 path 有走過 `copy_into`。

### Step 5：輸出 SARIF、收尾

SARIF 就是 Step 4 的產物。確認它含 3 個 result、每個帶 `codeFlows`。這份 SARIF 就是你能交付、能接進 triage 系統（[Ch 36](./36-false-positive-governance.md)）、能給 reviewer 看 flow path 的成果。

## 如果你卡住了

- **一個都沒命中**：多半是 source 定義錯。`read(fd, &n, ...)` 是把值**寫進** `n`，所以 source 是「`read` 第 2 個引數的 defining argument」（`asDefiningArgument()`），不是 `read` 的回傳值（`read` 回傳的是讀了幾個 byte，不是資料）。
- **命中 5 個（fixed / decoy 沒被放過）**：barrier 沒生效。先各別確認：decoy 的 barrier 有沒有抓到 `clamp` 的回傳？fixed 的 `guardedSink` 有沒有正確辨識「被 relational guard 控制的 memcpy」？分開驗證：先只加 clamp barrier 看 decoy 消不消失，再加 guardedSink 看 fixed 消不消失。
- **v3（跨函式）沒命中**：你用的是 `TaintTracking::Global`（跨函式）而不是 local flow 吧？跨函式變體要靠 global taint 的 inter-procedural 能力（[Ch 22](./22-codeql-global-taint.md)），local flow（[Ch 21](./21-codeql-local-dataflow.md)）追不過函式邊界。
- **v2（算術）沒命中**：確認你用的是 **TaintTracking** 而非純 DataFlow。`len + 8` 是「taint 經運算傳播」，這正是 taint tracking 比 data flow 多做的一步——純 `DataFlow::Global` 不會讓 taint 穿過 `+`。
- **barrier 語法報錯**（如 `getAChild` 之類 method 找不到）：不同 class 的 API 不一樣。`GuardCondition` 沒有 `getAChild()`；要判斷「guard 控制某 block」用 `g.controls(bb, _)`，要抓 guard 裡的變數用 `g.(RelationalOperation).getAnOperand()`。API 不確定時查標準庫的 `Guards.qll`。

## 參考解答

真跑過（`codeql` 2.26.2，WSL）。靶、建 db、query、跑法、輸出全在下面，**輸出照貼含 flow path**。

<details>
<summary>點開看完整參考解答 + 真實輸出</summary>

### 靶：`proto.c`

```c
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <stdint.h>

static int read_len(int fd) {
    int n = 0;
    read(fd, &n, sizeof(n));   /* SOURCE: attacker controls n */
    return n;
}

/* explicit sanitizer: returns a value clamped into [0, cap] */
static int clamp(int v, int cap) {
    if (v < 0) return 0;
    if (v > cap) return cap;
    return v;
}

/* ---- V0: FIXED. inline bound check on the sink -> NEGATIVE ---- */
void handle_fixed(int fd) {
    char buf[64];
    int len = read_len(fd);
    if (len < 0 || len > (int)sizeof(buf)) return;   /* guard controls sink */
    char tmp[256];
    memcpy(buf, tmp, len);                           /* safe */
}

/* ---- V1: UNFIXED. direct len -> memcpy size ---- */
void handle_v1(int fd) {
    char buf[64];
    int len = read_len(fd);
    char tmp[512];
    memcpy(buf, tmp, len);                           /* SINK: OOB write */
}

/* ---- V2: UNFIXED. len flows through arithmetic ---- */
void handle_v2(int fd) {
    char buf[64];
    int len = read_len(fd);
    int total = len + 8;                             /* taint propagates */
    char tmp[1024];
    memcpy(buf, tmp, total);                         /* SINK */
}

/* ---- V3: UNFIXED. len passed through another function ---- */
static void copy_into(char *dst, char *src, int n) {
    memcpy(dst, src, n);                             /* SINK (interproc) */
}
void handle_v3(int fd) {
    char buf[64];
    int len = read_len(fd);
    char tmp[256];
    copy_into(buf, tmp, len);
}

/* ---- DECOY: sanitized via clamp() -> NEGATIVE (誤報誘餌) ---- */
void handle_decoy(int fd) {
    char buf[64];
    int len = read_len(fd);
    int safe = clamp(len, sizeof(buf));              /* BARRIER: sanitizer */
    char tmp[256];
    memcpy(buf, tmp, safe);                          /* safe */
}

int main(void) { return 0; }
```

### 建 database

```bash
export PATH=$HOME/audit-tools/codeql:$PATH
mkdir -p ~/audit-lab/practice-d/src
# 把 proto.c 放進 src/
cd ~/audit-lab/practice-d
codeql database create protodb --language=cpp --source-root=src \
  --command="gcc -c proto.c -o /tmp/proto.o" --overwrite
```

輸出結尾（照貼）：

```
Finished zipping source archive (101.22 KiB).
Successfully created database at /home/ypp/audit-lab/practice-d/protodb.
```

### query：`UnvalidatedLenToMemcpy.ql`

搭配 `qlpack.yml`：

```yaml
name: audit-tests
version: 0.0.1
dependencies:
  codeql/cpp-all: "*"
```

```ql
/**
 * @name Unvalidated network length reaches memcpy size
 * @description A length read from a socket flows into a memcpy size argument
 *              without passing a bound check, allowing an out-of-bounds write.
 * @kind path-problem
 * @problem.severity error
 * @id audit/unvalidated-len-to-memcpy
 * @tags security
 */
import cpp
import semmle.code.cpp.dataflow.new.TaintTracking
import semmle.code.cpp.controlflow.Guards
import UnvalidatedLenFlow::PathGraph

module UnvalidatedLenConfig implements DataFlow::ConfigSig {
  predicate isSource(DataFlow::Node source) {
    // SOURCE：被 read(fd, &n, ...) 寫入的 out-parameter n
    exists(FunctionCall rd |
      rd.getTarget().getName() = "read" and
      source.asDefiningArgument() = rd.getArgument(1))
  }

  predicate isSink(DataFlow::Node sink) {
    // SINK：memcpy 的第 3 個引數（size）
    exists(FunctionCall mc |
      mc.getTarget().getName() = "memcpy" and
      sink.asExpr() = mc.getArgument(2))
  }

  predicate isBarrier(DataFlow::Node node) {
    // BARRIER 1：clamp() sanitizer 的回傳值切斷 taint（放過 decoy）
    exists(FunctionCall clamp |
      clamp.getTarget().getName() = "clamp" and
      node.asExpr() = clamp)
  }
}

module UnvalidatedLenFlow = TaintTracking::Global<UnvalidatedLenConfig>;

// BARRIER 2（sink 上的 guard）：memcpy 的 size 變數被 relational bound check
// 控制時，濾掉這個 sink（放過 fixed）。
predicate guardedSink(FunctionCall mc) {
  exists(GuardCondition g, Variable v |
    mc.getTarget().getName() = "memcpy" and
    mc.getArgument(2).(VariableAccess).getTarget() = v and
    g.controls(mc.getBasicBlock(), _) and
    g.(RelationalOperation).getAnOperand().(VariableAccess).getTarget() = v)
}

from UnvalidatedLenFlow::PathNode source, UnvalidatedLenFlow::PathNode sink, FunctionCall mc
where UnvalidatedLenFlow::flowPath(source, sink)
  and mc.getArgument(2) = sink.getNode().asExpr()
  and not guardedSink(mc)
select sink.getNode(), source, sink,
  "Unvalidated network length flows to memcpy size here."
```

### 跑 query，出 SARIF

```bash
codeql database analyze protodb --additional-packs=. UnvalidatedLenToMemcpy.ql \
  --format=sarifv2.1.0 --output=results.sarif --rerun
```

### 真實輸出：命中數與 flow path（照貼，經腳本解讀 SARIF）

```
TOTAL RESULTS: 3

### sink @ proto.c:33   (handle_v1 —— 直接流)
   step 0: L8   read output argument      // read(fd,&n,...) 寫進 n
   step 1: L9   n                         // return n
   step 2: L6   *read_len                 // read_len 回傳
   step 3: L31  call to read_len          // int len = read_len(fd)
   step 4: L31  call to read_len
   step 5: L33  len                       // memcpy(buf, tmp, len)  ← SINK

### sink @ proto.c:42   (handle_v2 —— 經算術 len+8)
   step 0: L8   read output argument
   step 1: L9   n
   step 2: L6   *read_len
   step 3: L39  call to read_len          // int len = read_len(fd)
   step 4: L39  call to read_len
   step 5: L40  ... + ...                 // int total = len + 8  ← taint 穿過 +
   step 6: L42  total                     // memcpy(buf, tmp, total)  ← SINK

### sink @ proto.c:47   (handle_v3 —— 跨函式 copy_into)
   step 0: L8   read output argument
   step 1: L9   n
   step 2: L6   *read_len
   step 3: L51  call to read_len          // int len = read_len(fd)
   step 4: L51  call to read_len
   step 5: L53  len                       // copy_into(buf, tmp, len)
   step 6: L46  n                         // copy_into 的參數 n（跨函式進入）
   step 7: L47  n                         // memcpy(dst, src, n)  ← SINK
```

**驗收核對**：

- 命中**恰好 3**：`proto.c:33`（v1）、`:42`（v2）、`:47`（v3）。✅
- `handle_fixed`（`memcpy` @ L25，被 `if (len > sizeof(buf)) return;` 這個 `guardedSink` 濾掉）**未命中**。✅
- `handle_decoy`（`memcpy` @ L62，size 來自 `clamp()` 回傳，被 `isBarrier` 切斷）**未命中**。✅
- 3 條 flow path 都完整：v2 的 path 明確經過 `... + ...`（taint 穿過算術），v3 的 path 明確跨進 `copy_into` 的參數 `n`（inter-procedural）。✅

這就是變體分析做對的樣子：**一條抽象 query，抓齊三種不同寫法的同型 bug，同時放過已修與加了 sanitizer 的誤報誘餌**。

### 中間狀態的證據：先不加 barrier 會命中 5 個

把 `isBarrier` 清空、把 `guardedSink` 條件拿掉，重跑，命中數是 **5**（3 變體 + fixed + decoy）。這證明：flow 本身連通到所有 5 處，barrier 的加入是**精準地**把 fixed 與 decoy 這兩個「flow 有連通但語意上安全」的位置濾掉——這正是 [Ch 23](./23-codeql-flow-state-models.md) barrier 建模的價值：漏報與誤報的分水嶺就在 barrier 建得準不準。

</details>

## 測試用例表

| 函式 | 情況 | 觸發的技術點 | 預期 | 為什麼 |
|---|---|---|---|---|
| `handle_v1` | `len` 直接進 `memcpy` | 基本 global taint source→sink | **命中** | 無 barrier，直接流 |
| `handle_v2` | `len + 8` 再進 `memcpy` | taint 經算術傳播 | **命中** | TaintTracking 讓 taint 穿過 `+`（DataFlow 不會） |
| `handle_v3` | 經 `copy_into()` 進 `memcpy` | inter-procedural flow | **命中** | Global taint 跨函式追 |
| `handle_fixed` | `memcpy` 前有 bound check | sink 上的 guard barrier | **放過** | `guardedSink` 濾掉被 relational guard 控制的 memcpy |
| `handle_decoy` | 經 `clamp()` sanitizer | 顯式 sanitizer barrier | **放過** | `isBarrier` 切斷 clamp 回傳的 taint |

自我檢查：改 query 後每次都重跑這 5 個，命中必須**恰好是前 3 個**。任何一個 negative 變命中（誤報）或任何一個 positive 消失（漏報），都代表 barrier 或 source/sink 建錯了。

## 延伸挑戰

任選往 Part「CodeQL 之後」與跨工具的方向延伸：

- **加 flow state 區分 source 種類（接 Ch 23）**：讓 source 分成「來自 socket」與「來自檔案」兩種 flow state，只對某一種報。體會 flow-state 怎麼在同一條 query 裡承載額外的 tracking 維度。
- **抽象 pattern 抓更多寫法**：目前 source 綁死 `read`、sink 綁死 `memcpy`。把 source 擴成 `read`/`recv`/`recvfrom`，sink 擴成 `memcpy`/`memmove`/`strncpy` 的 size 參數，再造幾個用這些 API 的變體，看你的 query 抓不抓得到。這是把 query 往「可攜、可上 [Ch 27](./27-codeql-mrva.md) MRVA」推的必經之路。
- **對真實開源專案跑**：拿一個你熟的小型 C 專案（能 build 的），建 database，把這條 query 套上去。真專案上你會第一次遇到真實世界的誤報與漏報——這是把練習變成能力的關鍵一步。
- **對回 Semgrep 版（練習 C）**：練習 C 你用 Semgrep taint mode 寫過類似的東西。把這兩版並排：CodeQL 的 inter-procedural（v3 跨函式）Semgrep 追不追得到？barrier 的表達力誰強？各自的建 database / 免建 database 成本差在哪？這個對比讓你對「什麼場景選哪個工具」有第一手判斷（接 [Ch 35](./35-funnel-combining-tools.md)）。

## 本練習你該帶走的

- **變體分析的完整肌肉記憶**：建 db → 定 source/sink → 加 barrier → 跑出真陽性零誤報 → 出 SARIF，你走完了整條天梯，這副流程換到任何真 target 都一樣。
- **「恰好 N、0 誤報」是變體分析的驗收靈魂**：抓齊真的、放過已修與誤報，兩邊都卡準才算做對。少一個是 barrier 把真的擋掉（漏報），多一個是 barrier 沒把假的濾掉（誤報）。
- **barrier 是漏報與誤報的分水嶺**（Ch 23）：本練習用兩種 barrier（顯式 sanitizer 的 `clamp` 回傳、sink 上的 relational guard）精準放過兩種安全情況——建準了才有零誤報。
- **TaintTracking 與 Global 各解決一種變體**：算術傳播（v2）靠 taint tracking 而非純 data flow，跨函式（v3）靠 global 而非 local——你親手驗證了這兩個能力邊界。
- 這條 query 打磨到可攜、低誤報後，就是能放上 [Ch 27](./27-codeql-mrva.md) MRVA 一次掃一片 repo 的武器——本練習是那件事的單 repo 前置。

整條 CodeQL 天梯你已走完並親手驗收。接下來換一把不同哲學的刀：Joern——一個免 build、以 CPG（code property graph，程式屬性圖）直接查詢的工具，補上 CodeQL「必須能 build」的那塊短板。

→ [Ch 29 Joern 上手](./29-joern-getting-started.md)
