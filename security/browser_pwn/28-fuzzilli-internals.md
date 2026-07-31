# Ch 28 — Fuzzilli 原理：FuzzIL、mutator、coverage feedback、REPRL

> **目標**：徹底搞懂 Fuzzilli 為什麼是打 JS engine 的標準 fuzzer，以及它怎麼運作。四個核心：(1) 為什麼 JS engine fuzzing 不能用 AFL 那套「翻位元組」，得改用 **IL（中間語言，FuzzIL）**；(2) FuzzIL 長什麼樣、它怎麼保證生成的程式「幾乎總是語法/語意合法」；(3) mutator（變異器）與 program building 怎麼在 IL 上做出既多樣又有效的變異；(4) **coverage feedback** 與 **REPRL** 這兩個把 Fuzzilli 和 V8 縫在一起的機制。這章是 Part 5 最深的一章——把它讀透，[Ch 29](./29-running-fuzzilli.md) 實跑時你才知道每個環節在幹嘛。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`。V8 樹裡的 **Fuzzilli 整合層**（`src/fuzzilli/fuzzilli.cc`、`cov.cc`、`cov.h`）是真跑可讀的——本章的 REPRL/coverage 片段都出自這裡，是**官方原始碼**不是二手轉述。**Fuzzilli 本體**（用 Swift 寫、需另裝 toolchain）本 batch **未實測**：凡涉及「跑一個 Fuzzilli session」的產出，本章與 [Ch 29](./29-running-fuzzilli.md) 一律標「**未實測，理論預期**」，以 saelo 的碩論《FuzzIL: Coverage Guided Fuzzing for JavaScript Engines》與官方 repo 為準，絕不捏造 crash 輸出。

## 為什麼需要這個？

你在 `binary_exploitation` 或一般 fuzzing 課學的 AFL/libFuzzer，是**位元組導向**的：它把輸入當成一串 bytes，翻幾個 bit、剪貼幾段、看 coverage 有沒有變。對「吃二進位格式」的目標（解析器、圖片庫、PDF）超好用。

**對 JS engine，這套幾乎完全失效**。原因是 JavaScript 是**高度結構化的文字語言**：

- 隨便翻 JS 原始碼的一個 byte，99.9% 的機率是**語法錯誤**（少個括號、變數名亂掉），parser 一秒就吐 `SyntaxError`，根本進不到你想打的優化器。
- 就算僥倖語法對了，也多半是**語意廢話**（用了沒宣告的變數、型別完全不 make sense），跑兩步就 `ReferenceError`/`TypeError` 死掉。

而 JS engine 最肥的洞在**優化編譯器深處**（TurboFan/Maglev，[Ch 2](./02-v8-architecture.md) 的主礦）。要摸到那裡，你的測試程式必須：(a) 語法合法、(b) 語意上跑得夠久、(c) 觸發足夠複雜的型別流動與優化。位元組翻轉一個都做不到。

**Fuzzilli 的洞見**：不要在「JS 原始碼文字」層變異，改在一個**專為變異設計的中間語言（FuzzIL）**上變異，變異完再 lower 成 JS。這樣「幾乎總是合法」變成**設計保證**，而不是碰運氣。這個洞見讓 Fuzzilli 成為找 V8/JSC/SpiderMonkey 洞最多產的 fuzzer——CTF 出題、真實 CVE 大量出自它。

## 先建立直覺：與其亂改文章，不如改「劇本大綱」

用位元組 fuzz JS，像是拿一篇文章隨機塗改字母——改出來的幾乎都是亂碼。

Fuzzilli 的做法，像是在**劇本大綱（IL）**層面動手：大綱是「宣告一個變數 v0 = 陣列」「呼叫 v0.push(v1)」「定義函式 v2」這種**結構化的積木**。你在積木層面加一塊、刪一塊、換一塊、複製一段——因為每塊積木本身合法、且積木之間的接法有規則約束，**組出來的東西天生就是合法的程式**。最後把這份積木大綱「翻譯」成真正的 JS 文字丟給 engine 跑。

```
   AFL 式（位元組層）              Fuzzilli 式（IL 層）
   ──────────────                ──────────────────
   "for(i=0;i<9;..."             v0 <- LoadInt 0
       │ 翻一個 byte                v1 <- LoadInt 9
       ▼                           BeginFor v0, v1
   "for(i=0;j<9;..."  → 廢       ...  ← 在這層加/刪/換積木
   （語法/語意常壞）              EndFor
                                     │ lower（翻成 JS）
                                     ▼
                                  合法的 JS，丟給 d8 跑
```

**核心口號：把「生成合法程式」從碰運氣，變成語言設計層的保證。**

## FuzzIL：一個為變異而生的中間語言

FuzzIL 是 Fuzzilli 的心臟。它不是給人寫的，是給 fuzzer 變異的。幾個關鍵設計：

### 1. 線性的操作序列 + SSA 風格的變數

一個 FuzzIL 程式是一串**操作（operation）**，每個操作可能產生一個新變數（`v0`, `v1`, …），後面的操作引用前面的變數。概念上像這樣（示意，非精確語法）：

```
   v0 <- LoadInt '42'
   v1 <- CreateArray [v0, v0]
   v2 <- LoadProperty v1, 'length'
   v3 <- LoadBuiltin 'Object'
   CallMethod v1, 'push', [v0]
```

- **每個變數只被定義一次**（SSA-ish）、且**永遠已宣告**——這就從結構上消滅了「用到沒宣告變數」這種語意錯誤。
- 變數的引用只能指向**當前作用域可見**的、**已經定義**的變數。Fuzzilli 生成/變異時強制這個規則，所以不會生出「用了還沒出生的變數」。

### 2. 作用域與區塊用「配對指令」表達

`if`、`for`、`function` 這種有巢狀結構的東西，FuzzIL 用**成對的 Begin/End 操作**表示（`BeginFor`/`EndFor`、`BeginFunction`/`EndFunction`）。變異器維護這些配對的完整性——這樣就不會生出「有 `{` 沒 `}`」的語法災難。作用域規則也附在這套配對上：`BeginFunction` 開一個新作用域，`EndFunction` 關掉。

### 3. 「幾乎總是合法」而非「保證合法」

注意是 *almost* always valid。FuzzIL 保證**語法幾乎必然合法、變數引用必然合法**，但不保證**執行期語意**（例如對一個非陣列呼叫陣列方法，跑起來會 `TypeError`）。這是刻意的取捨：

- 太嚴格（保證語意全對）會讓生成空間變窄，錯過很多有趣的邊界。
- Fuzzilli 選擇「語法與變數層面嚴格、執行期語意寬鬆」——讓大部分程式跑得起來、又保留探索怪異型別組合的自由。實測合法率（能跑完不 early-abort）約在**八九成**這個量級（依 saelo 論文，未實測數字以論文為準）。

### 4. lifting：FuzzIL → JavaScript

要真的跑，得把 FuzzIL「lift（抬升/降階）」成 JS 文字。`CallMethod v1, 'push', [v0]` 變成 `v1.push(v0);` 之類。lifter 是 Fuzzilli 裡「FuzzIL ↔ 具體 JS 語法」的橋。反過來，Fuzzilli 也能把一段真實 JS **compile 成 FuzzIL**（用於把已知 PoC 餵進 corpus 當種子）。

把前面那段 FuzzIL 示意 lift 成 JS，感受這個對應：

```
   FuzzIL（fuzzer 變異的層）              lift 後的 JS（丟給 d8 的層）
   ─────────────────────────            ──────────────────────────
   v0 <- LoadInt '42'                    const v0 = 42;
   v1 <- CreateArray [v0, v0]            const v1 = [v0, v0];
   v2 <- LoadProperty v1, 'length'       const v2 = v1.length;
   v3 <- LoadBuiltin 'Object'            const v3 = Object;
   CallMethod v1, 'push', [v0]           v1.push(v0);
```

**注意每個 FuzzIL 操作對應一句合法 JS、每個 `vN` 對應一個已宣告的 `const`**。這就是「變異在 IL 層、合法性由設計保證」的具體長相：fuzzer 在左邊那欄加/刪/換一行（都是合法操作、引用合法變數），lift 出來的右邊 JS 就仍然是合法的 JS。對比 AFL 直接在右邊那欄翻 byte——`const v0 = 42;` 翻一個字元變 `const v0 = 4R;`，SyntaxError。**IL 層的抽象，就是「幾乎總是合法」的來源。**

## Mutator 與 program building：怎麼變異、怎麼從零長出程式

Fuzzilli 產生新測試程式有兩條路：**變異既有程式**、與**從頭建構**。

### Mutators（變異器）

在 FuzzIL 層做的變異，常見幾類（Fuzzilli 內建一組 mutator，隨機挑用）：

| Mutator | 做什麼 | 為什麼有效 |
|---|---|---|
| **InputMutator** | 把某操作的輸入變數換成另一個可見變數 | 製造新的型別組合、新的資料流 |
| **OperationMutator** | 改一個操作的參數（如整數常數、屬性名） | 探索邊界值（0、-1、大數、特殊屬性） |
| **SpliceMutator** | 從語料庫另一個程式**剪一段**接進來 | 把兩個「有趣」的片段組合，撞出新路徑 |
| **CodeGenMutator / ExplorationMutator** | 在程式某處**插入新生成的一段** | 增加複雜度、觸發新的優化 |
| **ProbingMutator** | 插入對物件/型別的探測 | 引導後續變異往「有型別互動」的方向 |

關鍵是：**因為變異發生在 FuzzIL 層、且變異器遵守作用域/變數規則，變異後的程式仍然（幾乎）合法**。這是它和 AFL 的根本差異——AFL 翻位元組後合法率趨近 0，Fuzzilli 變異後合法率八九成。

### Program building（從零建構）

當語料庫太小、或要注入新鮮血液時，Fuzzilli 會**從空程式開始逐步 append 操作**建一個新程式：每一步從「當前可用的變數、可用的操作」裡挑一個合法的接上去。這個 builder 內建對 JS 語意的大量知識（哪些內建函式存在、要幾個參數、大概回什麼型別），所以建出來的東西不是瞎湊，而是**結構上像真人會寫的怪 JS**。

**CodeGen 的權重**是可調的——你可以偏好生成更多陣列操作、更多優化觸發（迴圈、`%OptimizeFunctionOnNextCall` 類的內部函式），把 fuzzing 往你想打的攻擊面推。這在針對性 fuzzing（只想轟 TurboFan）時很重要（[Ch 29](./29-running-fuzzilli.md)）。

## Coverage feedback：怎麼知道「這個變異有沒有走到新地方」

Fuzzilli 是 **coverage-guided（覆蓋率導向）**：它保留「走到過去沒走過的程式碼路徑」的程式，丟掉沒新意的。這需要 V8 回報「這次執行覆蓋了哪些程式碼邊（edge）」。V8 樹裡的 `src/fuzzilli/cov.cc` / `cov.h` 就是這套機制的 V8 端——**這是官方原始碼，直接讀**。

覆蓋率靠 compiler 埋的 **`__sanitizer_cov_trace_pc_guard`** 樁點（SanitizerCoverage，`-fsanitize-coverage=trace-pc-guard`）。每個基本區塊邊界會呼叫這個回呼，把「這條 edge 被走到」記進一塊共享記憶體。看 `cov.h` 的真實定義：

```
$ sed -n '20,46p' ~/v8build/v8/src/fuzzilli/cov.h
#define SHM_SIZE 0x200000
#define MAX_EDGES ((SHM_SIZE - 4) * 8)

struct shmem_data {
  uint32_t num_edges;
  unsigned char edges[];
};

shmem_data* shmem;
uint32_t *edges_start, *edges_stop;
uint32_t builtins_edge_count;
...
// - Optimization: `*guard = 0` in `__sanitizer_cov_trace_pc_guard` disables
//   the edge after the first hit. This prevents redundant writes to shared
//   memory for hot edges. Fuzzilli resets these guards between iterations
//   via `sanitizer_cov_reset_edgeguards`.
```

逐點拆這段（每一行都對應一個你該懂的機制）：

- **`SHM_SIZE 0x200000`（2 MB 共享記憶體）**：Fuzzilli 和 V8 之間用一塊 **shared memory** 傳覆蓋率。V8 每執行一段 JS，就把「這次踩到哪些 edge」寫進這塊記憶體，Fuzzilli（另一個 process）讀它。共享記憶體 = 零複製、極快，這對「每秒跑幾千個 testcase」的 fuzzing 是命脈。
- **`edges[]` bitmap + `MAX_EDGES`**：覆蓋率用**位元圖**表示，每個 edge 一個 bit。`(2MB - 4) * 8` ≈ 一千六百萬個 edge 位。一個 edge 被走到就把對應 bit 設 1。
- **`*guard = 0` 走過一次就關掉該 edge**：這是效能優化——一條 edge 第一次被踩後就停止再記（避免熱迴圈把同一個 edge 記幾百萬次拖慢速度）。所以 Fuzzilli 蒐集的是**「有沒有走到」（edge hit / 布林）**，不是「走了幾次」（不像 AFL 記 hit count 分桶）。
- **`sanitizer_cov_reset_edgeguards` 每輪重置**：因為 guard 走過就關，下一個 testcase 開跑前要把所有 guard 重新打開（reset），否則第二個 testcase 會看不到第一個已經「用掉」的 edge。這個 reset 每個 iteration 做一次。

Fuzzilli 端的邏輯：跑完一個 testcase → 讀共享記憶體的 edge bitmap → 和「歷史累積覆蓋」比對 → **有新 edge 被點亮** → 這個 testcase 是「有趣的」，收進 corpus，之後拿它當變異的種子；否則丟掉。這就是 coverage-guided 的閉環：**新覆蓋 = 保留，往新地方鑽得越來越深**。

### trace_pc_guard 的真實實作細節

看 `cov.cc` 裡 `__sanitizer_cov_trace_pc_guard` 的真實開頭（官方原始碼），它比你想像的細心：

```
$ sed -n '197,211p' ~/v8build/v8/src/fuzzilli/cov.cc
__sanitizer_cov_trace_pc_guard(uint32_t* guard) {
  uint32_t index = *guard;
  // ... 若 shmem 還沒初始化、或 edge 已被 disable(*guard==0)，直接 return
  if (!index) return;
```

- **`if (!index) return;`**：`*guard == 0` 有兩個意思——「這 edge 已經走過被關掉」或「coverage 還沒初始化」。兩種情況都不記，直接返回。原始碼註解還提到一個**多執行緒 race**：兩條執行緒同時踩同一 edge，第一條先把 guard 設 0，第二條讀到 0——會漏記一次，但這無害（覆蓋率只是布林，漏記一次不影響「這 edge 走過了」的結論）。這種對併發的容忍是 fuzzing instrumentation 追求速度的典型取捨。
- **單 DSO vs 多 DSO（Chromium）模式**：`cov.cc` 有個 `USE_CHROMIUM_FUZZILLI` 分支。純 d8 是**單 DSO**，可以用「走過就關 guard」的優化（快）。但完整 Chromium 有很多動態庫（多 DSO），沒有全域 guard 註冊表能一次 reset 全部，所以那個模式**關掉優化、每次都寫共享記憶體**（`edge persistence`，慢但正確）。這解釋了為什麼「fuzz d8」和「fuzz 完整 Chrome」的 coverage 成本不同——你主線打 d8，享受單 DSO 的快。

這個細節的意義：**coverage instrumentation 不是魔法，它是 compiler 埋的樁 + 一塊共享記憶體 + 一套 guard 開關管理**。看懂它你就知道 fuzzing 的 overhead 花在哪、為什麼 d8 比整個 Chrome 好 fuzz。

## REPRL：為什麼不能一個 testcase fork 一次 process

最後一塊拼圖，也是 Fuzzilli 快的關鍵：**REPRL（Read-Eval-Print-Reset-Loop）**。

樸素的 fuzzer 是「每個 testcase 開一個新 process 跑，跑完 process 死掉」。對 V8 這**災難級的慢**——V8 光啟動（初始化 isolate、載 snapshot、暖 JIT）就要幾十毫秒，比跑一個小 testcase 本身還久。每秒可能只跑得了幾十個，fuzzing 效率極低。

REPRL 的解法：**讓 V8 process 活著，一個接一個地餵 testcase，每跑完把狀態重置（reset）到乾淨初始態，再跑下一個**。省掉了反覆的 process 啟動成本，吞吐能拉高一兩個數量級（每秒數百到數千 exec）。V8 端要配合：

- **接收管道**：Fuzzilli 透過特定 fd 把下一段 JS 餵進來。看 `fuzzilli.cc` 裡 `FUZZILLI_PRINT` 走的是 `REPRL_DWFD`（REPRL data write fd）這類專用 fd：
  ```
  $ grep -n 'REPRL\|FUZZILLI_PRINT' ~/v8build/v8/src/fuzzilli/fuzzilli.cc | head
  ...  static FILE* fzliout = fdopen(REPRL_DWFD, "w");   // FUZZILLI_PRINT 用
  ```
- **狀態重置**：每個 testcase 之間，把 heap、全域物件等重置乾淨，避免前一個 testcase 污染後一個（否則 crash 難以重現、覆蓋率不準）。
- **回報結果**：把 exit status（正常結束？crash？逾時？）回給 Fuzzilli。crash 才是我們要的訊號。

`--fuzzilli` 這個 flag（要 `v8_fuzzilli=true` build 才有）就是打開這整套 REPRL + coverage + `Fuzzilli(...)` 內建函式的總開關。這也是為什麼 fuzzing 用的 d8 是**另一套 build config**（[Ch 29](./29-running-fuzzilli.md) 詳談 build）。

### 內建的 `Fuzzilli(...)` 函式與自我測試

`fuzzilli.cc` 裡註冊了一個特殊內建函式 `Fuzzilli(op, arg)`，其中 `FUZZILLI_CRASH` 分支是 Fuzzilli 用來**驗證「我的 crash 偵測管線通不通」**的自我測試——它能按 `arg` 故意觸發各種 crash：

```
$ sed -n '63,90p' ~/v8build/v8/src/fuzzilli/fuzzilli.cc
  if (strcmp(*operation, "FUZZILLI_CRASH") == 0) {
    auto arg = info[1]->Int32Value(...).FromMaybe(0);
    switch (arg) {
      case 0: IMMEDIATE_CRASH();  break;   // 直接 crash
      case 1: CHECK(false);       break;   // CHECK 失敗
      case 2: DCHECK(false);      break;   // DCHECK（debug build 才觸發）
      case 3: perform_wild_write(); break; // 對 0x414141414141 亂寫
      case 4: /* use-after-free, ASan 抓 */ ...
      case 5: /* std::vector OOB(1), libc++ hardening 抓 */ ...
      case 6: /* OOB(2), ASan 抓 */ ...
      ...
```

這幾個 case 是 Fuzzilli「開機自檢」的一部分：跑 `FUZZILLI_CRASH` 各個 arg，確認「當 V8 真的爆炸時，Fuzzilli 能正確偵測到 crash 並存下 testcase」。這也順帶給了你一份**現成的、可控的 crash 清單**——[Ch 30](./30-exploitability-triage.md) 的 triage、[練習 E](./practice-e-fuzzilli-crash-triage.md) 都會用它當「人工植入的 crash」來練分類，因為它涵蓋了 wild write / UAF / OOB 幾種主要類型。

## 對比：AFL/libFuzzer vs Fuzzilli

| 面向 | AFL / libFuzzer | Fuzzilli |
|---|---|---|
| 變異層 | **位元組**（翻 bit、剪貼 bytes） | **IL（FuzzIL）**，變異後仍幾乎合法 |
| 對 JS engine 合法率 | 趨近 0（一改就 SyntaxError） | 八九成能跑（設計保證） |
| 覆蓋率機制 | 相同的 SanitizerCoverage / edge bitmap | 同源（`__sanitizer_cov_trace_pc_guard`），edge 布林非 hit-count |
| 執行模型 | fork-server / persistent mode | **REPRL**（長活 process + 每輪 reset） |
| 適合目標 | 二進位解析器、圖片/文件庫 | **結構化語言引擎**（JS/WASM），能鑽進優化器 |
| 種子 | 語料檔（bytes） | FuzzIL 程式（可從真實 JS compile 而來） |

一句話：**Fuzzilli = 把 coverage-guided fuzzing 的閉環，套到一個「為 JS 結構量身打造」的 IL 上**。覆蓋率與 REPRL 是「引擎」，FuzzIL 是讓引擎不空轉的「合法燃料」。

## 踩雷集錦

1. **想用 AFL 直接 fuzz d8 的 JS 檔**：合法率趨近 0，浪費算力在 `SyntaxError` 上，摸不到優化器。JS engine 就是要 IL-based fuzzer。
2. **以為 Fuzzilli 生成的程式「保證合法」**：是 *almost*。語法與變數引用幾乎必然合法，但執行期語意（型別對不對）刻意寬鬆——那正是它探索怪異型別組合的自由度來源。
3. **忘了 fuzzing build 是另一套 config**：一般 `out/x64.release` 沒開 coverage 埋點與 `--fuzzilli`（要 `v8_fuzzilli=true` + SanitizerCoverage flag）。拿你 Part 1 那顆 d8 直接餵 Fuzzilli 會 REPRL 握手失敗。
4. **把 edge bitmap 當 hit-count 用**：V8 這邊 `*guard=0` 走過一次就關，蒐的是「有沒有走到」的布林，不是次數分桶。別預期能區分「走了 1 次 vs 1000 次」。
5. **忘了每輪 reset edge guards / 狀態**：不 reset，第二個 testcase 的覆蓋率會被第一個污染、crash 也難重現。這是 REPRL 正確性的關鍵，V8 整合層已處理，但你自己改 harness 時容易漏。
6. **把 `FUZZILLI_CRASH` 的 crash 當成「找到真漏洞」**：那是 Fuzzilli 的自檢函式，crash 是**你叫它 crash 的**，不是引擎有洞。它只用來驗證偵測管線與練 triage。

## 進階：再往深一層

- **語意合法率 vs 覆蓋深度的取捨**：拉高「保證語意也對」的比例會讓程式更常跑完，但生成空間變窄、錯過邊界。Fuzzilli 的 mutator 權重、CodeGen 分佈都是在調這個平衡。針對性 fuzzing 常刻意調高「陣列操作 + 觸發優化」的權重。
- **differential fuzzing**：不只找 crash，還可以**比對兩個 engine（或同 engine 開/關優化）對同一段 JS 的輸出**，不一致 = 潛在 miscompilation（正是 [Ch 27](./27-patch-diffing.md) 那類 bug！）。Fuzzilli 可配合這種模式挖出「不 crash 但算錯」的深洞。
- **corpus 蒸餾（minimization）**：跑久了 corpus 會膨脹。定期做 minimization——保留能維持相同覆蓋的最小程式集，讓後續變異更聚焦。
- **HeapType / JIT-oriented 生成**：進階版 Fuzzilli / 衍生工具會加入對「怎麼可靠觸發優化」的知識（插入暖身迴圈、`%OptimizeFunctionOnNextCall`），把火力集中在 TurboFan/Maglev——這是把通用 fuzzer 變成「type confusion 專用鑽頭」的關鍵調校。
- **REPRL 的 crash 語意細節**：逾時（timeout）、OOM、真 crash 要分開處理——只有「明確的記憶體錯誤/abort」才是漏洞訊號，timeout/OOM 多半是雜訊。triage 時這個分類很重要（[Ch 30](./30-exploitability-triage.md)）。

## 動手練習

1. 讀 `~/v8build/v8/src/fuzzilli/cov.cc` 的 `__sanitizer_cov_trace_pc_guard` 實作與 `sanitizer_cov_reset_edgeguards`。用自己的話說明：一個 edge 第一次被踩、與同輪第二次被踩，分別發生什麼？為什麼要 `*guard = 0`？下一輪怎麼讓它復活？
2. 讀 `fuzzilli.cc` 的 `FUZZILLI_CRASH` 全部 case（0–11）。把它們**按 crash 型別分類**：哪些是 assertion（CHECK/DCHECK）、哪些是 wild write、哪些要 ASan 才抓得到（UAF/OOB）、哪些是 SIGILL。這份分類表你 [Ch 30](./30-exploitability-triage.md) 會直接用。
3. 紙上題：為什麼「每個 testcase fork 新 process」對 V8 特別慢、而對「一個小 C 函式的 libFuzzer target」沒那麼糟？用 V8 的啟動成本（isolate + snapshot + JIT 暖機）解釋 REPRL 的必要性。
4. 找 saelo 碩論裡「FuzzIL 一個範例程式」的圖，對照本章的 FuzzIL 示意，確認你能把 `v0 <- LoadInt` / `CallMethod` / `BeginFor...EndFor` 這些對應到真實 JS 長什麼樣。

## 本章重點整理

- JS engine 不能用位元組 fuzzer（合法率趨近 0）；Fuzzilli 改在 **FuzzIL（IL 層）** 變異，讓「幾乎總是合法」成為**設計保證**。
- **FuzzIL**：線性操作序列 + SSA-ish 變數（永遠已宣告）+ Begin/End 配對表達區塊/作用域；lift 成 JS 才執行，也能把真實 JS compile 回 FuzzIL 當種子。
- **Mutator**（Input/Operation/Splice/CodeGen/Probing）在 IL 層做變異、遵守作用域規則，所以變異後仍合法；program builder 能從零長出結構合理的怪 JS。
- **Coverage feedback**：SanitizerCoverage `trace_pc_guard` → 2MB 共享記憶體 edge **bitmap**（布林，走過關 guard，每輪 reset）→ 新 edge = 收進 corpus。
- **REPRL**：長活 V8 process、逐個餵 testcase、每輪 reset，吞吐比 fork-per-testcase 高一兩個數量級。`--fuzzilli`（`v8_fuzzilli=true` build）是總開關。
- `Fuzzilli("FUZZILLI_CRASH", n)` 是自檢用的可控 crash，涵蓋 wild write / UAF / OOB / assertion，拿來練 triage 剛好。

## 自我檢核

- [ ] 能解釋為什麼 AFL 式位元組變異對 JS engine 幾乎無效，Fuzzilli 的 IL 層變異怎麼解決
- [ ] 說得出 FuzzIL 的三個關鍵設計（SSA-ish 已宣告變數、Begin/End 配對、almost-valid 取捨）
- [ ] 能描述 coverage 從「compiler 埋 trace_pc_guard」到「Fuzzilli 讀共享記憶體 edge bitmap 判斷新覆蓋」的完整資料流
- [ ] 解釋 REPRL 是什麼、為什麼對 V8 這種啟動昂貴的目標是吞吐關鍵、每輪為什麼要 reset
- [ ] 知道 `*guard=0` 意味覆蓋率是 edge 布林而非 hit-count，以及這對變異回饋的含義
- [ ] （面試題）「為什麼專打 JavaScript 引擎要用 Fuzzilli 而不是 AFL？請從『合法率』與『執行吞吐』兩個角度講。」

## 延伸閱讀

- **[saelo 碩論《FuzzIL: Coverage Guided Fuzzing for JavaScript Engines》](https://saelo.github.io/papers/thesis.pdf)**
  - **這篇說什麼**：Fuzzilli 的第一手、最完整的設計論述——FuzzIL 語意、mutator、合法率實測、覆蓋率與 REPRL 的動機。
  - **和本章的關聯**：本章是它的濃縮導讀。凡本章「未實測、以論文為準」的數字，去這裡查原文。**必讀**。

- **[Fuzzilli 官方 repo — github.com/googleprojectzero/fuzzilli](https://github.com/googleprojectzero/fuzzilli)**
  - **讀哪裡**：README + `Docs/` 的 FuzzIL 說明、`Targets/` 裡 V8 的整合腳本。看它怎麼描述 REPRL 與 profile。
  - **和本章的關聯**：[Ch 29](./29-running-fuzzilli.md) 實跑就照它走；本章讀完 repo 的名詞你會全懂。

- **[V8 原始碼 `src/fuzzilli/`（cov.cc / cov.h / fuzzilli.cc）](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/fuzzilli/)**
  - **讀哪裡**：本章引用的那幾段。這是 coverage/REPRL 的 **V8 端真相**，比任何二手教材權威。
  - **和本章的關聯**：本章的共享記憶體 bitmap、guard reset、`FUZZILLI_CRASH` 都出自這裡。

- **[Project Zero blog — “Fuzzing ClusterFuzz” / JS engine fuzzing 系列](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：Fuzzilli 在真實漏洞挖掘中的戰果與方法論脈絡。
  - **和本章的關聯**：把「原理」接到「真的找到 CVE」，理解 fuzzing 在整個漏洞研究流程的位置。

原理懂了。下一章把它跑起來：怎麼裝 Swift toolchain、怎麼 build 一顆帶 coverage 的 fuzzing d8、怎麼開一個 session、怎麼讀 statistics 與 corpus——以及一場真實 campaign 大概長什麼樣（本 batch 未實測部分明確標註）。

→ [Ch 29 — 實跑 Fuzzilli：build、session、statistics、corpus](./29-running-fuzzilli.md)
