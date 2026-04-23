# Ch 1 — 為什麼要 symex：static / fuzzing / symbolic 三條路

> 目標：把 symbolic execution 放到「找 bug 的方法論光譜」裡看清楚。講完這章你要能回答：什麼題目該用 symex、什麼題目 symex 是錯的工具。

## 核心問題：給我 input，讓我經過那個 bug

程式分析的每一個子領域，解決的都是同一個問題：

> 「給定一個程式 P 與某個性質 φ，找到一個 input x，使得 P(x) 違反 φ。」

φ 可能是：不要 crash、不要 buffer overflow、不要把 secret 洩漏到 stdout、應該 return 0 而不是 1。找到 x 就是找到 bug（或 counter-example）。

找 x 的三條大路：

```
           static analysis ────── 只看 code，不跑
           fuzzing ──────────── 隨機丟，很多跑
           symbolic execution ── 用 symbolic 的方式「解」出 x
```

這三條路不是互相替代的；它們的 **cost / precision / reachability** profile 完全不同。選錯工具，你會把簡單問題做得很慘。

## 三條路的本質差異

### Static analysis：不跑，只推

程式碼當作數學物件分析。典型代表：
- **Abstract interpretation**（Astrée, Infer 的一部分）
- **Type-based**（Rust borrow checker、linear types）
- **Pattern matching**（clang-tidy、semgrep、CodeQL）

**優點**：
- 覆蓋所有 path（按定義）
- 不需要 input、不需要 runtime
- 速度快（pattern matching 級別的）

**缺點**：
- **Over-approximation**：把實際跑不到的 path 也算進去，false positive 很多
- 對 **pointer / alias / dynamic dispatch** 一籌莫展
- 只能找「語法上能看出來」的 bug（`strcpy` 呼叫、`memcmp` 結果未檢查）

**什麼時候選它**：
- code review 階段的 linting（CodeQL 掃常見 pattern）
- 寫新 code 時檢查 type safety
- 找「有 recipe」的 bug：已經知道有這類模式，想看 codebase 裡還有沒有沒補的

**不要用它找**：邏輯錯誤、需要特定 input 才觸發的 bug、memory corruption 的 root cause。

### Fuzzing：隨機丟，大力出奇蹟

丟大量（半）隨機 input 看 P 會不會 crash。典型代表：
- **AFL++**（coverage-guided，mutation-based）
- **libFuzzer**（in-process，per-target library）
- **Honggfuzz**、**LibAFL**

**優點**：
- 真正執行程式，看到的是 real behavior，幾乎沒有 false positive
- 對 memory corruption 極強（配 ASan）
- 上手快：寫個 harness、`afl-fuzz` 開跑，放一夜隔天看結果
- 真實世界**最有效**的找 bug 手段。openssl、libpng、curl、linux kernel 大多數公開 CVE 是 fuzzing 找的

**缺點**：
- **Magic byte / complex branch / checksum** 過不去：`if (crc32(input) == 0xdeadbeef)` 這種，AFL 要暴力碰大概 2^32 次才猜到
- 發現 bug 後，**不知道為什麼這個 input 觸發**：要再 reverse engineer
- input space 太大時（複雜 protocol、structured input），dumb fuzzing 根本動不起來，要寫 grammar

**什麼時候選它**：
- parser / 解析器 / codec（90% 的情況）
- 有明顯 input → output 介面的函式
- 只關心 crash / sanitizer trigger，不在乎路徑可解釋性

### Symbolic execution：把 input 當變數解

不把 input 當具體值，當成 **symbolic variable**。每走一條 branch，記下「走這邊 == path constraint 加上這個 predicate」。碰到 bug 狀態時，把 path constraint 丟 SMT solver，解出一個實際的 input。

```
     int x = <symbolic>;    // x = α
     if (x > 10) {           // branch: α > 10
         if (x < 20) {       //   branch: α < 20
             crash();        //   reach bug! PC = α > 10 ∧ α < 20
         }
     }
     
     SMT.solve(α > 10 ∧ α < 20) → α = 15
```

**優點**：
- 能**精確推理 path condition**：magic byte / crc 擋不住（只要 SMT 能 model）
- 找到的 input 自帶解釋：path constraint 本身就是「為什麼這個 input 觸發」
- 單路徑精度極高（在 model 之下）

**缺點**：
- **Path explosion**：每個 branch 乘 2，循環乘 N。真實軟體 path 是 2^100 級別
- **Symbolic memory** 是個噩夢：`a[i]` 當 i 是 symbolic 的時候要枚舉所有可能的 index 或用 array theory
- **Environment**：syscall、外部 library、network、時間都要 model 出來
- 跟 fuzzing 比**慢得離譜**（幾百 input/sec vs 幾千 exec/sec）

**什麼時候選它**：
- 解 crackme、CTF reverse 題（path 少、每條 path 有解釋）
- 為 fuzzer 產生**seed corpus** 或突破特定 magic byte
- 對一個小函式做**正確性證明**（給定 precondition，後條件恆成立？）
- exploit automation（給定目標 IP、找輸入）

**不要用它找**：大型軟體的 generic bug、input space 爆炸的題目、state 有 heavy external interaction 的題目。

## 一張決策表

| 情境 | 首選 | 備選 | 原因 |
|------|------|------|------|
| 檢查 code review 時的錯誤模式 | static (CodeQL) | — | 秒級回饋 |
| libpng / zlib / curl 找 CVE | fuzzing (AFL++) | + symex 突破 magic | 這是 fuzzing 的主場 |
| CTF crackme 解密 | symex (angr) | — | path 少、每條有解 |
| 驗證 crypto 實作恆等式 | symex (KLEE) | — | 要窮盡路徑 |
| fuzzer 卡在 checksum 過不去 | hybrid: AFL + symex (Driller) | — | 各自補弱點 |
| 一段 parser 的語意正確性 | symex + manual invariant | model checking | path 有上界時 |
| protocol state machine bug | grammar fuzzing | stateful symex | input 結構化 |
| 追查 secret 資料流向 | taint analysis | — | 目標不是找 crash |

**認真記這張表**。你往後每選一個工具之前都要對一下：「這題是 CRC 擋路的 parser 嗎？是的話 fuzzing + symex；是 crackme 嗎？直接 symex；是要看 secret 流到哪裡？taint。」

## 最常見的誤會

### 誤會一：symex 比 fuzzing「更強」

不對。**symex 跟 fuzzing 是互補**，不是上下位。

真實 metric：
- 過去十年最大規模的漏洞挖掘成果（OSS-Fuzz、Project Zero）絕大多數來自 fuzzing
- symex 的殺手鐧是**突破 fuzzing 卡住的 barrier**，不是取代 fuzzing

正確的 mental model：
> fuzzing 是地毯式搜索，symex 是精準定位。你不用精準定位做地毯式搜索，地毯式搜索也找不到需要精準定位的東西。

### 誤會二：symex 能解所有 program

不能。symex 的實用 target 通常：
- **不超過幾萬行**（angr 解上百萬行 binary 的 CFG 就已經在 OOM 邊緣）
- **input space 有界**（一個固定長度的 buffer 好處理；任意長度的 stream 難處理）
- **外部世界互動少**（純 computation > 有 network > 有 DB > full system）

看到「我用 symex 分析整個 Chrome」這種說法，要嘛是吹牛要嘛是用了 under-constrained execution（Ch 26 會講）+ 大量 hack。

### 誤會三：symex 是 black magic

Symbolic execution 在概念上**很簡單**：interpreter + SMT。KLEE 的 core 大約是幾千行 C++；angr 的 engine 層面幾千行 Python。難的是**工程化**：path explosion 怎麼控、symbolic memory 怎麼算、環境怎麼 model。

Ch 7 你會自己寫一個 100 行左右的 mini concolic executor，看完就不會覺得神秘。

## 工具光譜上的位置

從「精確但慢」到「粗糙但快」排：

```
  形式化驗證 (Coq, Dafny)
  ─────────────────── 需要人類寫 spec
  Model checking (CBMC, CPAchecker)
  ─────────────────── bound 受限
  Symbolic execution (KLEE, angr)   ← 我們學這個
  ─────────────────── path explosion
  Concolic execution (DART, SAGE)   ← 我們學這個
  ─────────────────── 拿 concrete 降爆炸
  Hybrid fuzzing (Driller, QSYM)    ← 我們學這個
  ─────────────────── symex 救 fuzzer
  Coverage-guided fuzzing (AFL++)
  ─────────────────── 隨機但不瞎
  Random fuzzing (zzuf)
  ─────────────────── 全盲
```

這門課的內容從中間那三層展開。上下兩端不教（形式化驗證是另一門課、random fuzzing 沒什麼好學的）。

Taint analysis 不在這張表上 — 它**不是找 bug 的方法**，它是**資料流追蹤的工具**。它常搭配 symex 或 fuzzing 使用（「taint-guided」），Part 5 會細講。

## 三個具體例子

### 例子 1：libpng 有個 heap OOB

用什麼？**AFL++**。丟一萬個 PNG corpus，加 ASan，跑幾小時。99% 機率出 crash。symex 從頭來會死在 zlib decompressor 的複雜 loop。

### 例子 2：CTF 有個 `check_flag(char* s)`，長度固定 16，每個 byte 有條件

用什麼？**angr**。16 byte 的 symbolic input，path 頂多幾萬條，SMT 秒解。丟 AFL 也會跑出來但慢很多且沒解釋。

### 例子 3：閉源 VPN 軟體，擔心有後門會把 credential 送到可疑 IP

用什麼？**Taint analysis**。把 credential buffer 標 source，把 socket send 標 sink，看有沒有 flow。symex / fuzzing 都答不了這題。

### 例子 4：有個 parser，fuzzing 跑兩天都卡在 `if (magic == 0xCAFEBABE)`

用什麼？**Hybrid**。AFL 保留，加上 Driller / QSYM — 當 AFL 卡太久，呼叫 symex 把 `magic == 0xCAFEBABE` 解出來當新 seed 丟回去。

## 為什麼這門課兩樣一起教

Symbolic execution 與 dynamic taint analysis 在**工程核心**上幾乎是同一回事：

- symex 在每個 instruction 把 **symbolic formula** 沿著資料流往前傳
- DTA 在每個 instruction 把 **taint label** 沿著資料流往前傳

兩個都需要：
- 一個 DBI 或 IR level 的執行引擎
- shadow state（你的 meta-data 放哪）
- 每個 instruction 的 propagation rule
- source（標記）與 sink（觀察）機制

現代工具（Triton、S2E、Angora）把它們合成同一個引擎。你把 symex 與 taint 分開學、再合起來看，會非常順。

## 自我檢核

- [ ] 能說出 static / fuzzing / symex 三者各自的 precision × cost 取捨
- [ ] 能判斷一個題目該用哪種分析方法，給出理由
- [ ] 理解「symex 不是 fuzzing 的升級版」
- [ ] 看得懂 path constraint 的基本形式（`α > 10 ∧ α < 20`）
- [ ] 能說出 symex 跟 DTA 在工程上為什麼相似

下一章把 symex 的核心循環（state、path constraint、SMT query）拆開看，先建立最基本的 mental model。

→ [Ch 2 — Symbolic execution 的核心循環](./02-core-loop.md)
