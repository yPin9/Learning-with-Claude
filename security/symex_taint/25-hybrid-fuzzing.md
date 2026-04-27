# Ch 25 — Hybrid fuzzing：Driller / QSYM / SymCC 的取捨

> 目標：搞懂 fuzzer + symex 為什麼要合作、三個代表作的設計差異、什麼情況用哪一個。

## 為什麼要 hybrid

Ch 1 已講：fuzzing 是主力，symex 補 fuzzing 的弱點。

最常見的實際情況：

```
AFL 跑 target：
   iter 1k  → coverage 15%
   iter 10k → coverage 32%
   iter 100k → coverage 45%
   iter 1M  → coverage 47%   ← 卡住
```

卡的原因通常是：
- `if (magic == 0xcafebabe) {...}`：AFL 隨機 mutation 難碰到
- 複雜 checksum：AFL 更難
- Deep branch：需要前面某個 byte 等於特定值才能走到

**symex 對這些 precision 需求強**。理想：fuzzer 卡住時，呼叫 symex 解鎖。

## Driller (NDSS 2016)

CMU Shellphish 團隊出品，把 **AFL + angr 串起來**。

### 架構

```
   AFL (主力 fuzzer)
      │
      │ corpus / coverage
      ▼
   Driller Manager
      │
      │ 偵測到 "AFL 沒進展"
      ▼
   angr concolic (補充)
      │
      │ 對 AFL 的 stuck input 做 symex、翻 PC 產生新 input
      ▼
   把新 input 餵回 AFL corpus
```

### 運作

1. AFL 吃 seed corpus，跑 mutation fuzz
2. 每隔一段時間，檢查 AFL 進度
3. 發現 stuck（若干小時沒增 coverage），把 AFL 目前找不到的 edge list 交給 angr
4. angr 用 concolic 對每個 edge 嘗試：產生能走那條 edge 的 input
5. 新 input 丟回 AFL corpus，AFL 繼續 mutate

### 優點

- **Unmodified AFL**：Driller 是 orchestrator，不 fork AFL
- **unmodified angr**：同上
- **簡單**：概念清晰、容易改

### 缺點

- **angr 很慢**：每個 edge 的 symex 可能幾分鐘到幾小時
- **Corpus size 爆**：angr 產生 input 多樣性好但數量小，AFL 之後 mutation 才爆量
- **Synchronization 效率差**：兩個 process 的 feedback 不即時

### 實作

```bash
# Driller 是 CMU 提供的工具
pip install driller
driller-core -c corpus/ -b target-binary -t -- @@
```

（隨著時間 Driller repo 有時維護有時停滯，版本相容要看 upstream）

### 實戰效果

Driller 在 DARPA CGC 比賽表現優異。對 CGC 的 binary（small、self-contained），Driller 比 AFL 多找到 **~30%** 的 bug。

對真實世界大型 target，Driller 比 AFL 增幅小得多（10%-20%），因為 angr 跑不動。

## QSYM (USENIX Sec 2018)

**QSYM** = **Q**ueens College + **SYM**bolic。由 Insu Yun et al.。

### 核心洞見

Driller 的問題：angr 的 symex 是 "從頭重新跑"。每次要 fresh concolic execution、fork state、解 SMT。**成本巨大**。

QSYM：用 **更輕量的 symex** — 不維護完整 state、直接在 fuzzer 的 concrete execution trace 上**補 symbolic 資訊**。

```
   AFL 跑一條 input → concrete trace (instruction sequence)
      │
      ▼
   QSYM 拿 trace 做 "lightweight symbolic" ─ 只記 branch 的 symbolic cond
      │
      ▼
   對感興趣的 branch 翻 cond、SMT 解、產生新 input
```

QSYM 的 symex 因為**不 fork state、不 model external world**（就跟 concrete trace），極快。

### 實作

QSYM 是 Pin + Z3：
- Pintool 攔截 target execution，同時記 concrete state + branch symbolic condition
- 不追 memory 的 symbolic（只追 register）
- 不追 syscall（concrete 跑過就跑過）

這些簡化讓它**快 10–100× 於 angr**，但失去了 angr 能處理的某些 corner case。

### 優點

- 遠快於 Driller
- 對大型 target 才真 work
- Paper 裡在 libpng、libxml、tcpdump 上比 AFL + Driller 找更多 bug

### 缺點

- 精度降低（pure concolic，不像 angr 能 fork）
- Pin-based，不跨平台
- 實作複雜

### 仍活

<https://github.com/sslab-gatech/qsym>。2020 後期維護降低，但思想被 SymCC 繼承。

## SymCC (USENIX Sec 2020)

**SymCC** = **Sym**bolic **C**ompile-time instrumentation for **C**oncolic。Sebastian Poeplau, Aurélien Francillon。

### 核心洞見

前兩者（Driller、QSYM）都是**把 symex 從外面拉進來**。SymCC 翻轉：**compile time 直接注入 symbolic tracing code**。

### 架構

```
   source code
      │
      │ clang (SymCC) - 編譯時注入 symbolic tracing
      ▼
   target binary (同時具備 concrete 運算 + symbolic tracing)
      │
      │ AFL 直接跑
      ▼
   每跑一次 input，自動產出 concrete result + symbolic PC
      │
      │ 用 Z3 翻 PC 產生 new input
      ▼
   新 input 回 AFL
```

### 性能

symbolic tracing 直接 inline 進 native binary，**不需要 DBI**、**不需要 interpreter**。幾乎 **1:1 native speed**（只多幾倍 overhead，vs Driller/QSYM 的 10–100×）。

### 代價

- 需要 source code（compile-time instrumentation）
- compile toolchain 要客製（clang + SymCC pass）
- 比 QSYM 好，但仍是 "concolic"，不是 pure symex

### 變體

- **SymQEMU**：SymCC 的 binary-only 版本，用 QEMU JIT 注入 symbolic tracing。給 closed-source target 用
- **SymSan**：另一個 fork，加強 memory sanitization

### 實作

<https://github.com/eurecom-s3/symcc>。

```bash
symcc hello.c -o hello_sym
SYMCC_INPUT_FILE=/path/to/stdin ./hello_sym
# 產生 symbolic trace，配合 AFL 的 harness 整合
```

跟 AFL 整合通常是：

```bash
# 用 symcc build
symcc -O3 target.c -o target_sym
afl-clang-fast -O3 target.c -o target_afl

# AFL fuzzing
afl-fuzz -i corpus -o output -- ./target_afl @@

# 同時跑 SymCC loop
symcc-concolic-execute output/queue ./target_sym
```

AFL 跑 instance 1，SymCC 吃 AFL 的 queue 產生新 input 丟回 AFL。類似 Driller 的 orchestration 但速度快太多。

## 三者對比

| 面向 | Driller | QSYM | SymCC |
|------|---------|------|-------|
| 發表 | 2016 | 2018 | 2020 |
| symex 引擎 | angr | 自家 Pin+Z3 | compile-time clang pass |
| 速度 | 慢 | 中 | 快 |
| 精度 | 高（angr 完整） | 中（簡化 concolic） | 中（同 QSYM） |
| Source 需求 | 否（binary OK） | 否 | 是（SymCC）/ 否（SymQEMU） |
| 跨平台 | Linux/Win angr 支援 | x86 only (Pin) | x86/ARM (clang LTO) |
| 大 target 表現 | 差 | 好 | 最好 |
| 代表 benchmark | CGC | LAVA-M, libpng | OSS-Fuzz |

## 選哪個

- **CTF、小 binary**：Driller（或直接 angr，省一層 orchestration）
- **有 source 的 real-world target**：SymCC（產業首選）
- **閉源 x86 target**：SymQEMU 或 QSYM
- **學術比較**：三個都跑

實務上：如果你有 source，用 SymCC + AFL++。這是 2020 後的 state-of-the-art hybrid fuzzing setup。

## SymCC 細節：compile pass 做什麼

LLVM pass 對每個 IR instruction 注入：

```llvm
; 原
%1 = add i32 %a, %b

; symcc-instrumented
%a_sym = call @get_symbolic(i32 %a)
%b_sym = call @get_symbolic(i32 %b)
%1_sym = call @sym_add(%a_sym, %b_sym)
call @set_symbolic(i32 %1, %1_sym)

; 原 add 保留
%1 = add i32 %a, %b
```

每個 register / memory 有 shadow 放 symbolic expression。branch 時查 shadow + 呼叫 Z3。

**跟 DTA 的 shadow memory 概念幾乎一樣**，只是 shadow 裡放 symbolic expression 不是 taint bit。

## Fuzzer 跟 symex 的 feedback loop 設計

好的 hybrid 的 key：**feedback 的頻率 / 品質**。

- 太稀 → AFL 一直卡 / symex 從零開始
- 太密 → fuzzer 來不及 mutate symex 產的 input，synchronization overhead 超大

SymCC 的策略：每次 AFL 輸入的 new seed 被 symex 跑、產生 0 到 N 個新 input。AFL 的 mutator 會 interleave 地 prefer new seed（SymCC 的 tag）。

## 你在實戰用什麼

假設你今天要 fuzz 一個 C library：

1. 先只用 **AFL++ + ASan** 跑一天。看 coverage
2. 覆蓋率停 → 看 uncovered edge 是什麼 nature
   - Magic byte 類：上 SymCC
   - Protocol structure 類：寫 grammar mutator
   - Crypto-like：手動 reverse、寫 Z3 model
3. 如果 fuzzer 產的 input 超過 mem / disk 限制，調整 corpus minimizer
4. 評估多少 coverage 提升值得加 SymCC 的 build complexity

**永遠讓 fuzzer 主力**，symex 只在 coverage 卡住時 deploy。

## 陷阱

- **symex 產 input 品質差**：如果 symex 只看到 simplified state（QSYM、SymCC 都簡化），產的 input 可能在 "只對這條 path 有效、對實際 trigger bug 沒幫助"
- **Symbolic bloat**：SymCC 對每個 instruction instrument，binary 大幾倍、跑慢幾倍（相對原生，雖相對 angr 快）
- **Corpus 爆量**：fuzzer 吃 symex 的 seed 後 mutation 更多，corpus 動輒幾 GB。對 CI 整合壓力
- **依賴地獄**：SymCC 鎖 clang version、LLVM version；AFL++ 要特定 LLVM plugin。build 一次環境要一整天

## 心法

Hybrid fuzzing 是 "fuzzer 作主、symex 補刀" 的工程。

- symex 是**昂貴武器**，不要濫用
- fuzzer 是**主力**，symex 幫它突破 barrier
- 結果評估看 **coverage delta** 跟 **bug found delta**，不看 symex 本身多漂亮

架好 SymCC + AFL++ 的 workflow，是 2026 做 vuln research 的標配技能。

## 自我檢核

- [ ] 能解釋為什麼 Driller / QSYM / SymCC 這條演化路徑
- [ ] 知道 Driller、QSYM、SymCC 各自的 symex 引擎
- [ ] 選正確工具：source available vs binary only
- [ ] 理解 SymCC 的 compile-time instrumentation 原理
- [ ] 知道 hybrid fuzzing 的 feedback loop 設計原則

下一章講 under-constrained symbolic execution — 為什麼大型 library / kernel 分析得這樣玩。

→ [Ch 26 — Under-constrained symbolic execution](./26-ucse.md)
