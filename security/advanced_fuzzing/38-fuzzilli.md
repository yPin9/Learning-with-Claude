# Ch 38 — Fuzzilli

> **目標**: 理解 Fuzzilli 的整體架構——FuzzIL 中介語言、mutator 家族、REPRL 執行介面、coverage 回饋迴路——以及它為什麼是目前語意有效 JS fuzzing 的標竿。重點在架構解析，build 引擎的步驟誠實標注未實測。

> **環境**: macOS / Linux（Swift 5.x 工具鏈）+ Fuzzilli 源碼（`github.com/googleprojectzero/fuzzilli`）+ patched V8 / SpiderMonkey / JavaScriptCore（需要 REPRL patch + coverage instrumentation；build 約需 1–2 小時，本章架構分析為主，build 步驟標注「未實測」）。

---

## 為什麼需要

Ch 37 說清楚了問題：在 JS 字串層做 mutation，稍微改一個 token 就可能生出語法錯誤的程式，引擎在 parse 階段就拒絕了，根本進不到 JIT 或 bytecode 解釋器。就算語法正確，`undefined` 的屬性存取、型別不對的算術，也會在 runtime 早早拋出例外而結束。傳統字串 fuzzer 在 JS 引擎這個目標上真實有效的 test case 比率極低。

Fuzzilli 的答案是：**把 mutation 的戰場移到中介語言層**。

它設計了一個叫 FuzzIL 的 IR，IR 的每個 operation 都帶型別約束，mutator 只被允許產生型別合法的程式，再由 Lifter 把 FuzzIL 翻譯成 JavaScript。語意有效性不是靠事後過濾，而是靠結構保證：你無法在 FuzzIL 層生出「使用尚未宣告的變數」或「對數字型別呼叫 Array 方法」。

這讓 Fuzzilli 和 jsfunfuzz / CodeAlchemist / LangFuzz 等前輩有本質差異：不是「盡量生有效的 JS」，而是「在 IR 層保證，再翻譯出 JS」。

---

## 先建立直覺

Fuzzilli 的整體資料流：

```
┌─────────────────────────────────────┐
│        Corpus (FuzzIL programs)     │
│   每個 program 是一組 FuzzIL 指令   │
└─────────────────┬───────────────────┘
                  │  選一個 program + 一種 mutator
                  ▼
┌─────────────────────────────────────┐
│            Mutator                  │
│  InputMutator / OperationMutator /  │
│  CodeGenerationMutator / Splicing   │
│  → 在 FuzzIL 層操作，型別約束保證  │
└─────────────────┬───────────────────┘
                  │  產生新 FuzzIL program
                  ▼
┌─────────────────────────────────────┐
│         FuzzIL program              │
│  變數保證已宣告，型別一致            │
│  operation 有 input/output 型別標注 │
└─────────────────┬───────────────────┘
                  │  Lifter
                  ▼
┌─────────────────────────────────────┐
│         JavaScript source           │
│  lift 出的 JS 語意等同 FuzzIL       │
└─────────────────┬───────────────────┘
                  │  寫入 shared memory
                  ▼
┌─────────────────────────────────────┐
│      Executor / REPRL 介面          │
│  patched V8 / SpiderMonkey / JSC    │
│  REPRL = Read-Eval-Print-Reset-Loop │
│  不重啟進程，只重置 JS context      │
└─────────────────┬───────────────────┘
                  │  coverage bitmap (SHM)
                  ▼
┌─────────────────────────────────────┐
│           Feedback                  │
│  新 edge → 保留到 corpus            │
│  crash → 記錄 reproducible case     │
└─────────────────────────────────────┘
```

整個迴路裡最關鍵的設計決策有兩個：
1. **FuzzIL 層 mutation**：語意有效性由 IR 型別系統保證
2. **REPRL 不重啟進程**：JS 引擎初始化成本高，重用進程才能維持足夠的執行速度

---

## FuzzIL 中介語言

### 核心設計：型別系統保語意

FuzzIL 的每個 operation 都帶有靜態型別標注。Operation 宣告自己期望的 input 型別和會產生的 output 型別。Mutator 在選擇把什麼 operation 插到哪個位置時，必須讓 input 型別和 scope 內現有變數的型別吻合。

這使得幾種常見的「爛 mutation」從結構上不可能發生：
- 使用尚未定義的變數（scope 追蹤保證）
- 對整數型別呼叫 `Array.prototype.push`（型別約束保證）
- 把 closure 的回傳值當物件 property 存取，但 closure 型別標注為 void

### FuzzIL 片段示意

下面是論文（Groß et al., IEEE S&P 2023）描述的概念性表示，**不是**真實的 Swift 型別或內部 API，僅作架構說明：

```
// FuzzIL 概念示意（基於論文 conceptual representation，非真實語法）
//
// v0 = LoadInteger(42)             output: .integer
// v1 = CreateObject()              output: .object
// v2 = LoadString("hello")         output: .string
//      SetProperty(v1, "x", v0)   input: .object, .string, .integer
// v3 = GetProperty(v1, "x")        input: .object, .string  → output: .unknown
// v4 = BinaryOp(v0, v3, "+")       input: .integer, .unknown → output: .number
//      Print(v4)                   input: .number
```

對應 Lifter 翻出的 JavaScript：

```javascript
const v0 = 42;
const v1 = {};
const v2 = "hello";
v1.x = v0;
const v3 = v1.x;
const v4 = v0 + v3;
fuzzilli("FUZZILLI_PRINT", v4);
```

FuzzIL 程式的每個變數只被定義一次（SSA-like 風格），Lifter 決定要用 `const` / `let` / `var`，選哪個關鍵字本身也可以是 mutation 維度之一。

### 為何字串層 mutation 保不了語意

傳統做法：拿一段 JS 字串，隨機替換 token、刪行、插入隨機 token。問題：

```
// 原始
function foo(a) { return a + 1; }

// 字串 mutation：把 "return" 刪掉
function foo(a) { a + 1; }   ← 語法合法，語意改變，但算是好的 case

// 更常發生的：把 "+" 換成 "["
function foo(a) { return a [ 1; }   ← 語法錯誤，引擎 parse 直接拒絕

// 或者插入隨機 token
function foo(a) { return a + b + 1; }   ← b 未定義，ReferenceError
```

Fuzzilli 在 IR 層 mutation：改的是 operation，變數宣告和型別由系統維護，結果一定是語法合法且「引用合法」的 JS。

---

## Mutator 家族

Fuzzilli 有幾種不同的 mutator，設計上各自攻不同的路徑空間：

```
┌──────────────────────┬──────────────────────────────────────────┬───────────────────────┐
│ Mutator              │ 操作                                     │ 攻的覆蓋路徑           │
├──────────────────────┼──────────────────────────────────────────┼───────────────────────┤
│ InputMutator         │ 改現有 operation 的參數值                │ 邊界值 / 特殊常數      │
│                      │ 例：把整數 42 改成 -1 / 0 / MAX_SAFE    │ 觸發 JIT 邊界條件      │
├──────────────────────┼──────────────────────────────────────────┼───────────────────────┤
│ OperationMutator     │ 換 operation 種類，維持 input/output 型別│ 操作語意突變           │
│                      │ 例：GetProperty → GetElement              │ 同型別不同操作的路徑   │
├──────────────────────┼──────────────────────────────────────────┼───────────────────────┤
│ CodeGenerationMutator│ 在程式中插入全新的合法 code fragment      │ 引入新的操作序列       │
│                      │ 從 profile 允許的 operation 集合中生成   │ 觸發引擎從未見過的序列 │
├──────────────────────┼──────────────────────────────────────────┼───────────────────────┤
│ SplicingMutator      │ 把兩個 corpus program 的片段拼接起來     │ 最強：組合已知路徑     │
│                      │ 需要對齊型別邊界                         │ 容易生出複雜的新行為   │
├──────────────────────┼──────────────────────────────────────────┼───────────────────────┤
│ CombinationMutator   │ 依機率混用以上多種 mutator               │ 廣度覆蓋              │
└──────────────────────┴──────────────────────────────────────────┴───────────────────────┘
```

**SplicingMutator 特別值得一說**：它可以把 corpus 裡兩個完全不相關的 FuzzIL program 拼起來，前半段建立某個物件狀態，後半段用另一個 program 的操作序列去操作它。由於兩段各自在 corpus 裡是語意有效的，拼起來後型別邊界只需要在接縫處對齊，Lifter 仍能生出有效 JS。這是單純在 JS 字串層 splicing 做不到的——字串拼接幾乎一定語法爛掉。

---

## REPRL 介面

### 為何不用 forkserver

AFL++ 對 C/C++ 程式用 forkserver：在 main() 入口前 fork，每次執行 fork 一個子進程，不需要重複 loader + linker 初始化。JS 引擎初始化比一般 C 程式貴得多：

- V8 要 snapshot + JIT compiler 初始化
- built-in prototype 鏈建構
- JIT compiler 相關的 zone allocator 設置

fork 確實省掉了這些成本，但每次 fork 後仍要把整個堆積複製（或 copy-on-write 觸發），對於執行時間極短（1–10ms）的 JS test case 來說，fork 的 overhead 比例不可忽視。

**REPRL**（Read-Eval-Print-Reset-Loop）的方案：引擎進程**不重啟**，每次執行完後只重置 JS context（清掉 heap 裡的 JS 物件、重建 built-in 原型），引擎本身的 native 狀態保留。這讓每次測試的 overhead 幾乎只剩 JS 層 reset。

### REPRL Protocol

```
Fuzzilli (fuzzer 進程)          JS 引擎 (REPRL patch 後)
         │                              │
         │  write: JS source to SHM    │
         │─────────────────────────────►│
         │                              │ eval(source)
         │  write: "EXEC\n" to pipe    │ 執行完畢
         │─────────────────────────────►│
         │                              │ write coverage bitmap to SHM
         │  read: exit status / crash  │ write status to pipe
         │◄─────────────────────────────│
         │                              │ reset JS context
         │  (next iteration)            │ 等待下一個 EXEC
```

這個 protocol 靠兩個 shared memory region 實作：
1. **Input SHM**：Fuzzilli 寫入 JS source，引擎從這裡讀取
2. **Coverage SHM**：引擎填寫 edge coverage bitmap（和 AFL++ 的 `__AFL_SHM_ID` 格式類似，是 edge bitmap，不是 block bitmap）

### Coverage 傳回機制

Coverage instrumentation 在 build 引擎時插入，和 AFL++ 的 `__sanitizer_cov_trace_pc_guard` 機制類似。每條 edge（BB pair）對應 bitmap 的一個 byte，執行到時加一（或 hit）。每次 JS context 重置時 bitmap 清零，下次執行重新累積。

Fuzzilli 讀回 bitmap 後，用和 AFL++ 相同的「新 edge 出現就保留」邏輯更新 corpus。

---

## Build Patched 引擎

**本節未實測，為理論預期行為。依本節步驟 build 後需自行驗證。**

Fuzzilli repo 在 `Targets/` 目錄下提供三個主要引擎的 patch：

```
Targets/
  V8/
    Patches/           ← V8 REPRL + coverage patch
    README.md          ← 對應的 V8 commit hash
  SpiderMonkey/
    Patches/
    README.md
  JavaScriptCore/
    Patches/
    README.md
```

以 V8 為例的 build 步驟（理論流程）：

```bash
# 本段未實測，為理論預期行為
# 驗證方式：完成 build 後執行以下命令確認 REPRL 功能正常
# echo 'fuzzilli("FUZZILLI_PRINT", 42);' | ./d8 --reprl
# 應輸出 "42" 而不是 segfault 或 unknown flag 錯誤

# 1. 安裝 depot_tools
git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git
export PATH="$PATH:$(pwd)/depot_tools"

# 2. fetch V8（注意：需對應 Fuzzilli repo README 指定的 commit）
mkdir v8 && cd v8
fetch v8
# 切到 Fuzzilli README 指定的版本，否則 patch 幾乎必掛
git -C v8 checkout <commit_hash_from_fuzzilli_readme>

# 3. 套用 patch
cd v8
git apply ../../Fuzzilli/Targets/V8/Patches/*.patch

# 4. 編譯（release build with coverage，約 1-2 小時）
tools/dev/v8gen.py x64.release
# 在 out/x64.release/args.gn 中確認 v8_fuzzilli = true
ninja -C out/x64.release d8

# 5. 基本驗證
echo 'fuzzilli("FUZZILLI_PRINT", 1+1);' | out/x64.release/d8 --reprl
# 期望輸出：2
```

SpiderMonkey 和 JavaScriptCore 的步驟在各自的 `Targets/*/README.md` 裡，原理相同，工具鏈不同（Firefox 用 `mach`，JSC 用 `cmake`）。

---

## 引擎 Profile

Fuzzilli 對每個引擎有對應的 **Profile**，定義了：
1. **允許的 operation 集合**：不同引擎有引擎特有的 builtin（V8 的 `%IntlRoundingIncrement` 之類的 internal API 只在 V8 能用）
2. **型別規則的引擎特化**：SpiderMonkey 對某些 edge case 的型別行為和 V8 不同
3. **Engine-specific builtins**：每個引擎有自己的 native function，profile 負責告訴 Fuzzilli 哪些可以用、型別是什麼

這意味著：**相同的 FuzzIL program，在不同 profile 下 Lifter 會生出不同的 JS**。V8 profile 會插入 V8 的 `--allow-natives-syntax` 呼叫（如 `%OptimizeFunctionOnNextCall`）來強制觸發 JIT 路徑，SpiderMonkey profile 用的是 `%EnforceRangeUnsigned` 或 `gcPreserveCode()` 之類的 JSAPI。

這讓同一套 FuzzIL mutation 引擎可以針對三個不同的 JS 引擎分別運作，核心邏輯共用，引擎差異收在 profile 裡。

---

## 跑 Fuzzilli

**本節未實測，為理論預期行為。**

Fuzzilli 的入口是 `FuzzilliCli`，用 Swift Package Manager 管理：

```swift
// 本段未實測，為理論預期行為
// 驗證方式：執行後觀察 log 輸出是否有「Fuzzer started」及定期的統計行
// 如果 d8 路徑錯誤或 --reprl flag 不被接受，會立即報錯而非靜默失敗

// build Fuzzilli（在 Fuzzilli repo 根目錄）
swift build -c release

// 跑針對 V8 的 fuzzing session
swift run -c release FuzzilliCli \
  --profile=v8 \
  --logLevel=info \
  --exportStatistics \
  -- ./path/to/v8/out/x64.release/d8 --reprl
```

正常運行時的輸出格式（概念示意）：

```
[Fuzzer] Started (pid: 12345)
[Fuzzer] Corpus size: 1  |  Edges: 5234 / 98765  |  Execs: 0
[Fuzzer] Corpus size: 15 |  Edges: 12453 / 98765 |  Execs: 1000  |  Exec/s: 312
[Fuzzer] Corpus size: 47 |  Edges: 23891 / 98765 |  Execs: 5000  |  Exec/s: 287
[Fuzzer] *** Crash found! Saved to crashes/crash_0.js ***
```

欄位解讀：
- **Corpus size**：目前 corpus 裡的 FuzzIL program 數量
- **Edges**：已覆蓋的 edge 數 / 總 edge 數（來自 coverage bitmap）
- **Exec/s**：每秒執行次數；正常的 REPRL 模式應在 200–500 之間，如果遠低於這個範圍，通常是 d8 路徑有問題或 REPRL patch 沒正確套用
- **Crash found**：輸出的是可重現的 JS 腳本，直接可以餵給 d8 重現

crash 輸出的 JS 已經是 lifted 後的 JS，不是 FuzzIL 原始 IR。這是設計上的選擇——讓 crash case 對引擎開發者可讀，不需要 Fuzzilli 工具鏈也能重現。但這也意味著你看到的 crash JS 不一定是「最小化」的形式，通常需要再跑 testcase minimization（Ch 39 會涵蓋）才能拿到精簡的 PoC。

---

## 對比取捨表

```
┌──────────────────┬──────────────┬───────────┬──────────────┬────────────┬──────────────┐
│ Fuzzer           │ 語意有效性   │ 需 patch  │ Mutation 品質│ Setup 難度 │ 維護難度     │
│                  │              │ 引擎       │              │            │              │
├──────────────────┼──────────────┼───────────┼──────────────┼────────────┼──────────────┤
│ Fuzzilli         │ 高（IR 保證）│ 是        │ 高（型別感知）│ 高        │ 高（Swift+  │
│                  │              │           │              │            │ patch 維護） │
├──────────────────┼──────────────┼───────────┼──────────────┼────────────┼──────────────┤
│ CodeAlchemist    │ 中（語法樹  │ 否        │ 中（語義感知 │ 中         │ 中          │
│                  │ 語義規則）   │           │ 但非型別精準）│           │              │
├──────────────────┼──────────────┼───────────┼──────────────┼────────────┼──────────────┤
│ jsfunfuzz        │ 低（字串    │ 否        │ 低（字串     │ 低         │ 低          │
│                  │ template）   │           │ mutation）   │            │              │
├──────────────────┼──────────────┼───────────┼──────────────┼────────────┼──────────────┤
│ 純文法 fuzzer    │ 高（文法    │ 否        │ 低（文法限制 │ 中         │ 中          │
│ （如 Dharma）    │ 保語法）     │           │ mutation 空間）│          │              │
│                  │ 語意不保     │           │              │            │              │
└──────────────────┴──────────────┴───────────┴──────────────┴────────────┴──────────────┘
```

核心取捨：Fuzzilli 在語意有效性和 mutation 品質上領先，但代價是必須維護與引擎版本對齊的 patch，setup 和維護成本明顯高於其他方案。對於個人研究者，patch 跟不上引擎更新是最常見的痛點。

純文法 fuzzer（如 Dharma）的「語意不保」需要解釋清楚：文法可以保證語法結構正確（`if` 後面一定有條件句），但不保證變數宣告順序（可以生出使用了未宣告變數的合法語法樹）也不保證型別一致（可以生出對整數呼叫 `.length` 的語法正確但語意無意義的程式碼）。Fuzzilli 的 IR 型別系統同時處理了這兩個問題。

---

## 踩雷

### 1. 「Fuzzilli 不需要 patched 引擎，直接用 release build 就可以」

這個誤解來自「Fuzzilli 是 fuzzer 框架，引擎是外部工具」的直覺。但 Fuzzilli 和引擎之間有兩層深度耦合：

- **REPRL 介面**：需要引擎實作 Read-Eval-Print-Reset-Loop 的 socket/pipe protocol，release build 沒有這個
- **Coverage instrumentation**：需要引擎在 build time 插入 edge coverage bitmap 的回寫邏輯，一般 release build 沒有

沒有 REPRL patch，Fuzzilli 可以讓引擎跑（作為外部進程），但每次執行都要重啟進程，exec/s 會掉到個位數甚至更低，基本上無法有效 fuzzing。沒有 coverage instrumentation，Fuzzilli 等同 dumb fuzzer，不會有 corpus 成長。

### 2. 「FuzzIL 保證語意有效，所以 test case 一定能觸發 JIT」

語意有效是**必要條件**，不是**充分條件**。

V8 的 Maglev / Turbofan 只有在函式被執行「夠熱」之後才觸發 JIT 編譯。一個語意有效的 FuzzIL 程式，如果只執行某個函式一次，引擎會用 Ignition interpreter 跑，根本進不到 JIT。要觸發 JIT 相關的 bug，test case 必須讓目標函式達到 JIT 門檻（通常是數百到數千次呼叫）。

Fuzzilli 的 V8 profile 針對這點有補丁：會插入 `%OptimizeFunctionOnNextCall` 之類的 V8 internal API 強制 JIT。但這本身也是 profile 設計的問題，不是 FuzzIL 的 IR 能自動保證的。

### 3. 「直接 clone 最新 Fuzzilli，再 clone 最新 V8，套上 patch 就能跑」

Fuzzilli 提供的 patch 是針對**特定 V8 commit** 寫的。V8 的代碼變動頻繁，REPRL 相關的結構（Isolate 初始化、Context reset 路徑）幾個月就可能重構。舊 patch 套在新 V8 上幾乎必然衝突，就算 patch 能強行套上，行為也可能是未定義的。

正確做法：

1. 查 `Targets/V8/README.md` 裡指定的 V8 commit hash
2. 把 V8 checkout 到那個 commit
3. 套 patch
4. build

不要嘗試「把 patch 手動 port 到新 V8」，除非你熟悉 V8 internals。正確的路是等 Fuzzilli repo 更新 patch，或者自己貢獻更新後的 patch。

---

## 進階延伸

### Statistics 解讀：Edge Coverage 成長曲線

正常的 Fuzzilli session 前幾小時 edge coverage 成長很快（corpus 從空到有，容易找新路徑），之後進入緩慢爬升期。如果 edge coverage 幾乎不動，通常有三個原因：

1. Corpus 裡的 program 都在做類似的事，缺少多樣性 → 看 SplicingMutator 的命中率
2. 引擎某些功能沒被 profile 覆蓋 → 看 profile 的 allowed operations 是否包含你想測的 subsystem
3. Coverage bitmap 太滿（collision 上升）→ bitmap 預設大小是 64KB，對大型引擎可能不夠，需要調整

### 多進程 / 分散式 Fuzzilli

Fuzzilli 支援 master-worker 架構：

```
[Master]
  ├── 維護全局 corpus
  ├── 同步 interesting case 給所有 worker
  └── 匯整 crash
        │
        ├── [Worker 1] 跑 V8 instance 1
        ├── [Worker 2] 跑 V8 instance 2
        └── [Worker N] ...
```

Worker 透過 TCP socket 和 master 同步 corpus，master 負責去重和篩選。這讓 Fuzzilli 可以橫向擴展到多核心甚至多機器。在一台有 16 個 CPU 核心的機器上跑 16 個 worker 是標準做法。

分散式部署時要注意 **corpus 同步頻率**：如果 master 和 worker 之間的網路頻寬不足，corpus 同步會成為瓶頸，worker 各自獨立 evolve 而沒有共享收益，等同各自跑獨立 fuzzer。建議在同一台機器上跑多 worker，或者跨機器時確保低延遲網路。

**corpus 品質 vs corpus 數量**是另一個常被忽略的維度。Fuzzilli 的 corpus 保留策略是「發現新 edge 就保留」，隨著 session 拉長，corpus 可能累積大量「稍微不同但實質覆蓋類似路徑」的 program。corpus 過大會讓每次選 parent 的成本上升，並稀釋有效 program 被選到的機率。如果觀察到 exec/s 隨時間下降而 corpus size 持續膨脹，可以考慮定期 corpus minimization（類似 AFL++ 的 cmin）。

### 自訂新 Operation 擴充 FuzzIL

如果你要 fuzz 引擎的某個特定功能（例如 WebAssembly、Temporal API），可以為 FuzzIL 新增 Operation：

1. 在 Swift 裡繼承 `FuzzILOperation`，定義 input/output 型別
2. 在對應 profile 的 `allowedOperations` 裡加入
3. 在 Lifter 裡加入對應的 JS 程式碼生成邏輯

這樣做的好處是 mutation 引擎自動就能感知到這個新 operation，不需要另外寫 mutator。

---

## 動手練習

1. clone `github.com/googleprojectzero/fuzzilli`，閱讀 `Sources/Fuzzilli/FuzzIL/` 目錄下的 Swift 源碼，找出 `Operation` 的基礎型別定義，列出三個有不同 output 型別的 operation。

2. 閱讀 `Targets/V8/README.md`，找出目前 Fuzzilli 支援的 V8 版本對應的 commit hash，在 V8 changelog 裡確認那個版本的 release 日期。

3. 閱讀論文（Groß et al., IEEE S&P 2023）的 Section III（FuzzIL Design），用自己的話解釋為什麼 FuzzIL 選擇 SSA-like 的「每變數只定義一次」設計，而不是允許重新賦值。

4. 查 Fuzzilli 的 `Sources/Fuzzilli/Mutators/` 目錄，數一數實際有幾個 mutator 類別，和本章列的五種有什麼出入（版本可能有新增）。

5. 閱讀 Fuzzilli 的 `Sources/Fuzzilli/Lifting/JavaScriptLifter.swift`（或同等路徑的 Lifter 實作），找出 `LoadInteger` operation 對應生成的 JavaScript 片段是什麼，確認 Lifter 如何決定要用 `const` 還是 `let`。

6. 從論文 Section V（Evaluation）找出 Fuzzilli 在論文評測期間發現的 CVE 數量和涉及哪些引擎，思考 corpus 裡哪類 operation 最可能觸發這些 CVE（從 CVE 描述反推）。

---

## 本章重點

- Fuzzilli 把 mutation 戰場移到 FuzzIL 中介語言層，IR 的型別系統保證 mutation 結果語意有效
- FuzzIL 的每個 operation 有 input/output 型別約束，mutator 只能產生型別合法的程式，再由 Lifter 翻成 JS
- REPRL 介面讓引擎不重啟進程，只重置 JS context，大幅提升 exec/s
- Coverage 透過 shared memory bitmap 回傳，和 AFL++ 機制類似
- 四種主要 mutator（Input / Operation / CodeGeneration / Splicing），SplicingMutator 攻的路徑最廣
- 引擎 Profile 定義允許的 operation 集合和引擎特化型別規則，讓同一套 FuzzIL 框架支援 V8 / SpiderMonkey / JSC
- Build patched 引擎是最高門檻：patch 必須對齊引擎版本，過期 patch 套新引擎幾乎必掛
- 語意有效不等於觸發 JIT，還需要靠 profile 裡的 internal API call 強制 JIT 路徑

---

## 自我檢核

- [ ] 能解釋 FuzzIL 的型別系統如何在結構上保證 mutation 結果不會有未定義變數
- [ ] 能說明 REPRL 和 forkserver 的本質差異，以及各自適用的場景
- [ ] 能指出 SplicingMutator 在四種 mutator 裡特別強的原因
- [ ] 能解釋為什麼直接用 release build V8 跑 Fuzzilli 得到的是 dumb fuzzing
- [ ] 能說明引擎 Profile 在 Fuzzilli 架構裡的角色，以及為什麼相同 FuzzIL 在不同 profile 下生出不同 JS
- [ ] 能解釋「語意有效不等於觸發 JIT」這個踩雷點
- [ ] 能描述分散式 Fuzzilli 的 master-worker 架構是如何同步 corpus 的

---

## 延伸閱讀

1. **Fuzzilli: Fuzzing for JavaScript JIT Compiler Vulnerabilities** — Samuel Groß, Simon Koch, Lukas Bernhard, Thorsten Holz, Martin Johns（IEEE S&P 2023 / arXiv:2007.12899）— 讀 Section II（Background）、Section III（FuzzIL Design）、Section IV（Mutators）— 學習 FuzzIL 型別系統的完整設計決策，以及各 mutator 的 coverage 統計 — 本課 Ch 38 的直接理論基礎，論文 Fig. 2 和 Fig. 3 是 FuzzIL 架構的權威圖示

2. **Fuzzilli GitHub Repository** — googleprojectzero/fuzzilli（README + `Sources/Fuzzilli/` Swift 源碼）— 讀 `FuzzIL/` 目錄（Operation 型別定義）、`Mutators/` 目錄（mutator 實作）、`Targets/*/README.md`（各引擎 build 說明）— 學習真實實作和論文之間的差距，以及如何擴充新 Operation — 本課 Ch 38 動手練習的第一手資料

3. **saelo (Samuel Groß) 的技術 Blog / Project Zero 文章** — `saelo.github.io` 及 Google Project Zero Blog — 讀「Attacking JavaScript Engines」系列（2016–2020）和 Fuzzilli 相關的 write-up — 學習 JS 引擎漏洞的思考框架，以及 Fuzzilli 的設計動機（為什麼論文作者要從 jsfunfuzz 和 LangFuzz 的限制出發重新設計）— 連接本課 Ch 37（語意有效性問題）和 Ch 39（crash triage 可利用性判斷）

4. **LangFuzz: Fuzzing Languages with Language Grammars** — Holler et al.（USENIX Security 2012）— 讀 Abstract + Section 3（核心 mutation 機制）— 學習 Fuzzilli 之前的「語法感知 JS fuzzer」是什麼，以及 Fuzzilli 論文對 LangFuzz 的批評在哪裡 — 理解 Fuzzilli 設計決策的歷史對照

---

## 銜接

Fuzzilli 解決了「如何生出語意有效 JS」的問題。下一個問題是：引擎 crash 了之後，如何快速判斷是真的可利用 bug 還是雜訊？如何從 crash log 推斷受影響的 IR 節點、確定是哪個 JIT pass 出了問題？

→ [下一章](./39-js-engine-triage.md)
