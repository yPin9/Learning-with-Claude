# Ch 40 — hybrid fuzzing 原理

> **目標**: 讀完你能說清楚 hybrid fuzzing 解決的根本問題、Driller 的分工架構、demand-driven 觸發策略，以及為什麼不乾脆全用 concolic execution。

---

## 為什麼 fuzzer 會卡住

AFL 或 libFuzzer 靠 coverage-guided mutation 探索程式路徑。做法是：對現有 corpus 的 input 做 bit flip、chunk splice、havoc 亂改，看有沒有觸發新的 coverage edge，有就把這個 input 加進 corpus 繼續變種。

這個策略對大多數程式有效，但有一個系統性弱點：**magic value 比對**。

典型場景：

```c
void parse_header(uint8_t *buf, size_t len) {
    if (len < 4) return;
    uint32_t magic = *(uint32_t *)buf;
    if (magic == 0xDEADBEEF) {   // <-- 窄門
        process_body(buf + 4, len - 4);  // 深層邏輯藏在這裡
    }
}
```

要進 `process_body`，input 的前 4 bytes 必須精確等於 `0xDEADBEEF`（小端序是 `EF BE AD DE`）。

Fuzzer 的 mutation 在 32-bit 空間裡亂射。每次 mutation 撞中這 4 個 bytes 全對的機率是 1/2^32 ≈ 2.3 × 10^-10。跑一天翻幾億個 input，也不見得碰到。就算開了 dictionary mode 把 `0xDEADBEEF` 放進 token 字典，多個 magic 組合、或 magic 是動態計算結果時依然失效。

這就是 **fuzzer coverage stagnation**：corpus 膨脹了，但 branch coverage 卡在某個數字不動，because 唯一能打開新路徑的門需要精確的值，而 mutation 沒辦法系統性地推導那個值。

---

## 先建立直覺：窄門問題

以下 ASCII 圖畫出一段 CFG，中間有一條「窄門」邊：

```
                   ┌─────────────────────┐
                   │  parse_header()      │
                   │  magic = buf[0..3]   │
                   └──────────┬──────────┘
                              │
              ┌───────────────▼──────────────────┐
              │    if (magic == 0xDEADBEEF) ?     │
              └──────────┬─────────────┬──────────┘
                         │ true        │ false
                         │             │
              ┌──────────▼──────┐   ┌──▼─────────┐
              │  process_body() │   │  return     │
              │  <<深層邏輯>>    │   │             │
              └─────────────────┘   └────────────┘

  Fuzzer mutation 的命中分佈：
  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░▓░░░░
  ↑                                          ↑
  大量 mutation 落在 false 分支              true 分支（0xDEADBEEF）
  （覆蓋不到的死角）                         只有這一點能進去
```

問題的本質是：fuzzer 的搜索是**隨機 + 啟發式的**，它不「理解」比較條件。對於一個需要滿足嚴格等式的分支，隨機搜索的效率接近零。

---

## 核心概念：demand-driven hybrid fuzzing

Hybrid fuzzing 的想法直接：**讓 concolic execution 專門負責打開 fuzzer 打不開的窄門**。

### Driller 的分工（NDSS 2016）

Driller 是第一個系統性實現 hybrid fuzzing 的工具，架構非常清晰：

- **AFL（廣度探索者）**：跑得快，能在幾秒內嘗試數萬個 mutation，找到所有「不需要精確值就能到的路徑」。AFL 的工作是盡量鋪廣 corpus。
- **angr concolic engine（精確攻堅者）**：跑得慢，但能對一個特定的路徑做符號執行，推導出「要走這條路，input 必須滿足哪些約束」，再用 SMT solver（Z3）求解出一個具體 input。

兩者的分工是**互補而非競爭**：AFL 做的事 concolic 做得很慢，concolic 做的事 AFL 做不到。

### 什麼是 demand-driven

Demand-driven 的關鍵是 **觸發時機**。concolic execution 代價高，不能對每個 AFL input 都啟動一次。Driller 的策略：

1. AFL 正常跑，監控 corpus 的 coverage 增長速度。
2. 如果一段時間內（例如 N 秒）沒有發現任何新 coverage edge，判定「AFL 卡住了」。
3. 把目前 corpus 裡的 input 送給 angr，angr 從 AFL 卡住的位置開始做符號執行，嘗試解出能通過「下一個未探索 branch」的 input。
4. 解出的 concrete input 寫回 AFL corpus，AFL 繼續從這些新 input 開始 mutation。
5. 循環：AFL 探索新打開的空間，直到再次卡住，再觸發 angr。

「Demand-driven」的意思是：**concolic 只在需要時才跑，而不是一直跑**。這避免了把 concolic 的龐大開銷攤到每個 input 上。

### 可執行示意（Python pseudocode，說明主循環）

```python
import subprocess, time

# 假設 AFL 和 angr 各有一個介面
corpus = initial_seeds()
last_coverage = measure_coverage(corpus)
last_improvement_time = time.time()

STAGNATION_THRESHOLD = 60  # 60 秒沒新 coverage 就觸發 concolic

while True:
    # Phase 1: AFL 跑一輪
    new_inputs = afl_fuzz_round(corpus)
    corpus.extend(new_inputs)

    current_coverage = measure_coverage(corpus)
    if current_coverage > last_coverage:
        last_coverage = current_coverage
        last_improvement_time = time.time()
        print(f"[AFL] New coverage: {current_coverage} edges")
    
    # Phase 2: 檢查是否卡住
    elapsed = time.time() - last_improvement_time
    if elapsed > STAGNATION_THRESHOLD:
        print(f"[Hybrid] AFL stagnated for {elapsed:.0f}s, invoking concolic...")
        
        # 從 corpus 挑一個接近未探索 branch 的 input
        candidate = select_candidate(corpus)
        
        # angr concolic：解出能通過卡住分支的 input
        solved_inputs = angr_concolic_solve(candidate, target_branches=uncovered_edges())
        
        if solved_inputs:
            corpus.extend(solved_inputs)
            print(f"[Concolic] Solved {len(solved_inputs)} new inputs")
            last_improvement_time = time.time()  # 重置計時器
        else:
            print("[Concolic] No solution found, continuing AFL")
```

執行這段 pseudocode 的輸出示意：

```
[AFL] New coverage: 142 edges
[AFL] New coverage: 189 edges
[AFL] New coverage: 201 edges
... (AFL 探索到容易到達的路徑)
[Hybrid] AFL stagnated for 60s, invoking concolic...
[Concolic] Solved 3 new inputs
[AFL] New coverage: 215 edges   <- concolic 打開了新路徑
[AFL] New coverage: 234 edges
...
```

---

## 底層機制：fuzzer 與 concolic 的分工循環

```
┌─────────────────────────────────────────────────────────────────┐
│                        Hybrid Fuzzing Loop                       │
│                                                                  │
│   ┌─────────────┐    mutation      ┌──────────────────────┐     │
│   │             │─────────────────▶│                       │     │
│   │  AFL/fuzzer │                  │   Target Program      │     │
│   │  (廣度探索)  │◀─────────────────│   (instrumented)     │     │
│   │             │  coverage trace  │                       │     │
│   └──────┬──────┘                  └───────────┬───────────┘     │
│          │                                     │                  │
│    corpus│                            coverage │                  │
│    update│                            feedback │                  │
│          ▼                                     ▼                  │
│   ┌─────────────┐                  ┌──────────────────────┐     │
│   │   Corpus    │                  │  Coverage Monitor    │     │
│   │  (inputs)   │                  │  stagnation check    │     │
│   └──────┬──────┘                  └───────────┬──────────┘     │
│          │                                     │ 卡住！           │
│          │                                     ▼                  │
│          │ candidate input         ┌──────────────────────┐     │
│          └────────────────────────▶│   angr concolic      │     │
│                                    │   (符號執行 + SMT)    │     │
│                                    │                       │     │
│          new concrete inputs       │   1. 具體執行 candidate│     │
│          ◀───────────────────────── │   2. 在卡住點轉符號    │     │
│          加回 corpus                │   3. Z3 求解約束      │     │
│                                    └──────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘

關鍵流向：
  AFL corpus → angr（提供起點）
  angr solved inputs → AFL corpus（打開新空間）
  兩者共享 coverage 資訊（誰發現新 edge 都算進去）
```

### 符號執行在 Driller 裡做什麼

angr 對一個 AFL 選出的 input 做 **concolic execution**（concrete + symbolic 混合）：

1. **Concrete 執行前半段**：用真實的 input 值跑程式，直到到達 AFL 卡住的位置（那條未覆蓋的分支前）。這段不用符號化，因為 AFL 已經能到達這裡了。
2. **Symbolic 執行後半段**：把 input 中關鍵欄位（例如前 4 bytes）標記為符號變數，繼續往未探索的方向執行，收集路徑約束（path constraints）。
3. **SMT 求解**：把收集到的約束丟給 Z3，如果有解，Z3 給出滿足條件的具體值（例如 `buf[0..3] = 0xDEADBEEF`）。
4. **具體化**：用這個具體值組成一個新 input，送回 AFL。

### 為什麼不直接全程跑 concolic

這是理解 hybrid fuzzing 必須釐清的問題。**路徑爆炸（path explosion）**：

```
程式有 N 個分支，理論上最多 2^N 條路徑。
concolic 每條路徑要解一次 SMT，而 SMT 是 NP-complete。
實際的程式（例如 OpenSSL、libpng）有幾千個分支，
全程 concolic 在幾分鐘內就會因路徑爆炸而無法推進。
```

AFL 的 mutation 不做完整推理，所以能在幾秒內嘗試 10 萬個 input，覆蓋大量「容易到達的」路徑。Hybrid 的價值是：**讓 AFL 負責覆蓋容易的部分，只把難的交給 concolic**，兩者加總的效率遠高於任一方單獨跑。

---

## 進階用法

### 多輪分工策略

生產級的 hybrid fuzzer（例如 QSYM）不是只觸發一次 concolic，而是持續交替：

- AFL 跑 X 分鐘或 Y 個 mutation cycle。
- 自動偵測卡住點，批次挑 K 個 candidate input 送 concolic。
- Concolic 對每個 candidate 解出多個 concrete input（翻轉不同分支方向）。
- 全部送回 AFL corpus。

QSYM 的關鍵貢獻是把 concolic engine 改成「輕量化」版本：不追求精確，容許偶爾解不出來，換取 10 倍的速度提升，讓 hybrid loop 能跑更多輪。

### 選哪個 candidate 送 concolic

Driller 選「最接近未覆蓋 branch 的 input」。具體做法：

1. 對 corpus 裡的每個 input 跑一次，記錄它的 execution trace。
2. 找出 corpus 整體的 uncovered edges。
3. 選一個 input，它的 trace 裡有「最多步驟接近某個 uncovered edge 的 predecessor block」的 input。
4. 讓 concolic 從那個 predecessor block 開始處理，嘗試翻轉那條 uncovered edge 的條件。

這個選擇策略避免讓 concolic 每次都從程式入口開始，大幅減少需要符號化的路徑長度。

### 與 taint analysis 結合

更進一步的系統（如 CollAFL、GreyOne）在 AFL 的 mutation 之外加上 taint tracking：

1. 動態追蹤哪些 input bytes 影響了某個比較指令。
2. 只對那些 bytes 做 mutation（focused mutation）。
3. 卡住時才啟動 concolic，而且 concolic 只需要符號化那些 taint 過的 bytes。

這進一步壓縮了 concolic 需要處理的符號變數數量，緩解路徑爆炸。

### 在 CTF 和漏洞研究裡的應用

CTF binary 常用 magic value 做保護（模擬現實協議格式）。實際流程：

```bash
# 1. 先跑 AFL++ 幾小時，收集基礎 corpus
afl-fuzz -i seeds/ -o afl_out/ -- ./target @@

# 2. 偵測到 coverage 停滯後，用 angr 做 concolic
python3 driller_harness.py --binary ./target --corpus afl_out/queue/

# 3. 把 concolic 解出的 input 加回 AFL
cp concolic_out/*.bin afl_out/queue/

# 4. AFL 繼續從新 corpus 出發
afl-fuzz -i afl_out/queue/ -o afl_out2/ -- ./target @@
```

---

## 對比取捨表

| 維度 | 純 AFL/fuzzer | 純 concolic execution | Hybrid fuzzing |
|------|--------------|----------------------|----------------|
| 探索速度 | 極快（μs/input） | 極慢（秒~分鐘/path） | 快（AFL 主跑） |
| Magic value 攻堅 | 無效（機率近零） | 有效（SMT 精確求解） | 有效（concolic 補強） |
| 路徑爆炸 | 無此問題（隨機撞） | 嚴重（指數爆炸） | 緩解（只做局部） |
| 深層邏輯覆蓋 | 依賴運氣 | 理論上可達 | 實用上可達 |
| 工程複雜度 | 低 | 中 | 高（需整合兩套工具） |
| 記憶體消耗 | 低 | 高（符號狀態） | 中（concolic 只偶發） |
| 適合場景 | 普通 mutation-friendly 目標 | 小程式全路徑分析 | 有結構化 magic value 的協議/格式解析器 |

---

## 踩雷

**錯誤直覺：concolic 解出 input 後，coverage 一定會增加。**
正確認識：concolic 只保證「符號執行路徑上」的約束被滿足，但真實執行時如果有環境依賴（例如時間、隨機數、外部資源），solving 出來的 input 可能在 concrete 執行時走不同路徑，導致沒有新 coverage。這種情況叫做 **constraint mismatch**，QSYM 等工具有部分緩解但無法徹底解決。

**錯誤直覺：把 stagnation threshold 設很短（幾秒）能讓 concolic 盡早介入，探索更快。**
正確認識：太短會讓 concolic 頻繁啟動，而 concolic 啟動有固定開銷（載入 binary、建立符號狀態）。AFL 在卡住的前幾秒可能只是還沒嘗試到正確的 mutation 組合，不代表真的卡住了。Driller 的建議是看 queue cycle 數量而非時間，通常跑完整個 corpus 一輪仍無新 edge 才算卡住。

**錯誤直覺：Hybrid fuzzing 能解決所有 coverage 問題，包含 stateful 協議。**
正確認識：Hybrid fuzzing 主要解決「單一輸入欄位有 magic value 比對」的問題。**有狀態**的漏洞（例如需要先發 packet A 建立狀態，再發 packet B 才能觸發漏洞）需要 stateful fuzzing（如 AFLnet、StateAFL）配合，concolic 在多輪互動的狀態空間裡的路徑爆炸問題更嚴重，hybrid 的優勢在有狀態場景下大幅縮小。

**錯誤直覺：angr 直接接 AFL 就是 Driller，隨便整合就能用。**
正確認識：Driller 的難點在「選哪個 candidate 送 concolic」和「concolic 從哪個 execution point 切入」。如果直接對所有 corpus input 從頭跑 angr，路徑爆炸立刻就死。真實的整合需要實作 execution replay（concolic 跟著 AFL 的 trace 走到卡住點再切換符號模式），這部分工程複雜度不低。

---

## 進階延伸

### Hybrid fuzzing 的演化方向

- **QSYM（USENIX 2018）**：放棄追求完整的符號語義，改用 Intel PIN 的動態二進位插樁做「輕量化 concolic」，速度比 angr 快一個數量級，代價是偶爾解不出來。適合需要高頻 concolic 觸發的場景。
- **SymCC / SymQEMU**：直接在編譯時或 QEMU 層插入符號化邏輯，concolic 和 concrete 執行同步進行，不需要切換模式，進一步加速（見下一章）。
- **CollAFL + GreyOne**：把 taint analysis 的結果用來指導 AFL mutation，減少 concolic 的觸發頻率，同時讓 AFL 的 mutation 更精準。

### 和 coverage 演算法的關係

不同的 coverage 定義會影響 hybrid 的效果：

- **Edge coverage**（AFL 預設）：concolic 在解約束時以翻轉 edge 為目標。
- **Path coverage**：更細，concolic 更容易爆炸。
- **Value-based coverage**（AFL++ 的 cmpcov）：把比較指令的操作數差值也算進 coverage，幫助 AFL 自己「感覺到」接近 magic value 了，減少對 concolic 的依賴。

---

## 動手練習

**練習 1：手動模擬 Driller 分工**

1. 寫一個有三層 magic value 的 C 程式：
   ```c
   // magic1: buf[0..3] == 0xCAFEBABE
   // magic2: *(uint16_t*)(buf+4) == 0x1337
   // magic3: buf[6] == 0xFF
   // 通過三層後觸發 crash（*((int*)0) = 1）
   ```
2. 用 AFL++ 跑 10 分鐘，記錄 coverage 和 crash 狀況。
3. 用 angr 對 AFL 的一個 corpus input 做 concolic：
   ```python
   import angr
   proj = angr.Project('./magic_target', auto_load_libs=False)
   state = proj.factory.entry_state(stdin=angr.SimFile)
   # 設定路徑：要到達 crash 點
   simgr = proj.factory.simgr(state)
   simgr.explore(find=CRASH_ADDR, avoid=RETURN_ADDR)
   if simgr.found:
       print(simgr.found[0].posix.dumps(0))
   ```
4. 把 angr 解出的 input 加回 AFL corpus，觀察 coverage 變化。

**練習 2：量化 stagnation threshold 的影響**

用相同的目標程式，分別把 stagnation threshold 設成 10 秒、60 秒、300 秒，跑 hybrid fuzzing 1 小時，比較：
- 觸發 concolic 的次數
- 最終 coverage
- 找到 crash 的時間

記錄結果，思考 threshold 和目標程式複雜度的關係。

**練習 3：觀察 concolic 的失敗模式**

找一個使用外部亂數或時間戳的程式（例如 `if (rand() % 100 == 42)`），用 angr 嘗試解出能通過這個條件的 input。觀察 angr 的行為，記錄 constraint mismatch 如何發生，以及有什麼 workaround（例如 hooking `rand()`）。

---

## 本章重點

- **Coverage stagnation 根因**：fuzzer mutation 撞中特定常數的機率近零，magic value 比對是最常見的卡死場景。
- **Hybrid fuzzing 的分工**：AFL 做快速廣度探索，concolic 只在 AFL 卡住時精確攻堅，解出的 input 餵回 AFL。
- **Demand-driven 的意義**：concolic 不是一直跑，而是由「coverage 停滯」事件觸發，控制開銷。
- **路徑爆炸是不可迴避的限制**：完整 concolic 在大程式上行不通，hybrid 的價值是把 concolic 限制在局部難關上。
- **Driller 的核心貢獻**：系統性地整合 AFL + angr，定義了 hybrid fuzzing 的基本架構，後續工作（QSYM、SymCC）都是在這個框架上加速或精化。
- **concolic 失敗情境**：constraint mismatch、路徑爆炸、有狀態協議都是 hybrid 無法完全覆蓋的場景。

---

## 自我檢核

讀完本章後，能不看筆記回答以下問題就算掌握：

- [ ] 為什麼對 `x == 0xDEADBEEF` 這種條件，AFL mutation 的效率接近零？能算出機率嗎？
- [ ] Demand-driven hybrid fuzzing 的「demand」指什麼？觸發條件是什麼？
- [ ] Driller 的兩個組件各負責什麼，解出的 input 如何在兩者之間流動？
- [ ] 路徑爆炸為什麼讓純 concolic 在大程式上不可行？
- [ ] QSYM 相比 angr-based concolic 的主要改進是什麼？它犧牲了什麼換取速度？
- [ ] Constraint mismatch 是什麼？什麼情況下會發生？
- [ ] Hybrid fuzzing 對 stateful 協議為什麼效果有限？

---

## 延伸閱讀

1. **Driller: Augmenting Fuzzing Through Selective Symbolic Execution**
   Stephens et al., NDSS 2016
   https://www.ndss-symposium.org/ndss2016/ndss-2016-programme/driller-augmenting-fuzzing-through-selective-symbolic-execution/
   讀 Section 3（架構設計）和 Section 4（實驗，尤其是 coverage 對比圖）。這篇確立了 AFL+concolic 的基本分工模型，demand-driven 觸發策略的原始定義在這裡。是理解 hybrid fuzzing 的必讀基礎。

2. **QSYM: A Practical Concolic Execution Engine Tailored for Hybrid Fuzzing**
   Yun et al., USENIX Security 2018
   https://www.usenix.org/conference/usenixsecurity18/presentation/yun
   讀 Section 2（動機，解釋 angr-based concolic 的瓶頸）和 Section 3（QSYM 的設計）。這篇回答了「Driller 為什麼實用性有限」的問題：angr 太慢、IR 轉換失真。QSYM 的 PIN-based 輕量化是關鍵工程貢獻，和 Driller 對比讀最有收穫。

3. **Hybrid Fuzz Testing: Discovering Software Bugs via Fuzzing and Symbolic Execution**
   Majumdar & Sen, CMU Tech Report 2007
   https://people.eecs.berkeley.edu/~ksen/papers/hybrid.pdf
   讀 Section 1（Introduction）和 Section 3（hybrid 策略定義）。這是 hybrid fuzzing 概念的最早形式化，寫在 AFL 存在之前，用的是白盒 fuzzer（DART/SAGE 的前身）和 concolic 的組合。理解為什麼這個想法在 2007 年就提出、卻等到 2016 年 Driller 才成熟有用，是理解工具鏈演化的好切入點。

---

## 銜接

本章講的是 hybrid fuzzing 的分工原理和 Driller 的架構。Driller 使用 angr 做 concolic，angr 的速度瓶頸限制了 hybrid loop 的頻率。下一章看 SymCC 和 SymQEMU，它們把符號化邏輯直接嵌入編譯器或 QEMU，讓 concolic 和 concrete 執行**同步進行**，不需要切換模式，速度再快一個數量級——這是目前最實用的 concolic 加速方案。

→ [下一章](./41-symcc-symqemu.md)
