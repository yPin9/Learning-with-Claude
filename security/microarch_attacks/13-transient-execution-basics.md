# Ch 13 — 推測執行與瞬態指令

> **目標**：搞清楚「瞬態窗口」這個概念——推測執行為什麼會執行「架構上不該被執行」的指令、這些指令留下什麼痕跡、痕跡為什麼無法被 CPU 完全清除。這是 Spectre、Meltdown 等整個瞬態執行攻擊家族的共同根。

Part 2 讓我們掌握了一把尺（Flush+Reload）和幾種用法（Prime+Probe、Evict+Reload）。這把尺量的是「某條 cache line 在不在快取裡」。

這一章要回答的問題是：**誰把那條 line 放進去的？**

如果是正常程式執行放的，那你量到的是程式的記憶體存取行為——這就是傳統 cache 側信道（Ch 6–12）的工作原理。

但在瞬態執行攻擊裡，答案不同：**是 CPU 在「推測」一條錯誤路徑時放進去的——而且這條路徑從來就不應該被執行**。

## 從亂序執行說起

現代 x86-64 核心不是按指令順序一條一條執行的。Intel Comet Lake（這台 i7-10700）的執行引擎長這樣：

```
  程式順序（Architectural Order）
  ┌─────────────────────────────────────────────┐
  │  LOAD r1, [addr]   ← 可能需要 200 cycles    │
  │  ADD  r2, r1, 1    ← 等 r1 準備好才能跑     │
  │  STORE [addr2], r2                           │
  │  MUL  r3, r4, r5   ← 但這個跟上面完全獨立！ │
  └─────────────────────────────────────────────┘

  微架構執行順序（Microarchitectural Order）
  ┌──────────────────────────────────────────────────────┐
  │  LOAD r1, [addr]   ←── 送出 DRAM 請求，不等         │
  │  MUL  r3, r4, r5   ←── r4/r5 準備好就先跑！         │
  │  ── ~200 cycles 後 DRAM 回來 ──                      │
  │  ADD  r2, r1, 1    ←── r1 現在有值了，跑             │
  │  STORE [addr2], r2                                   │
  └──────────────────────────────────────────────────────┘
  架構狀態（register file 的最終值）仍按原始順序 commit
```

亂序執行（Out-of-Order Execution, OOO）讓 CPU 可以在等待某個慢操作（DRAM 讀取、除法）時，先把之後但不相依的指令跑完。這是現代 CPU 效能的核心來源之一。

關鍵機制：**Reorder Buffer（ROB）**。所有指令進到 ROB 排隊，可以亂序「執行」（execute），但要按原始程式順序「commit」（把結果寫進架構可見的 register 和記憶體）。如果某個指令發現自己不應該被執行（分支預測錯、存取違規），CPU 做「回滾」（rollback/squash）：把 ROB 裡那個點之後的所有指令的執行結果丟掉，架構狀態回到正確的樣子。

## 推測執行是什麼

推測執行（Speculative Execution）是亂序執行的延伸：不只亂順序，還「猜」。

### 分支推測

```c
if (condition) {
    do_a();
} else {
    do_b();
}
```

CPU 遇到 `if` 時，condition 可能還沒算完（或者是 DRAM 裡的值，要等 200 cycles）。與其乾等，CPU **猜** condition 的結果：

- 分支預測器（Branch Predictor）根據歷史紀錄猜「taken」或「not-taken」
- CPU 推測性地開始執行那條路徑上的指令（**推測執行**）
- 這些被推測執行的指令叫做 **瞬態指令（transient instructions）**
- 等 condition 的真實值算出來：
  - 猜對了：那些瞬態指令早就跑完，commit 即可，省下大量等待時間
  - 猜錯了：**回滾**——從架構角度看，那些指令從未執行過

這是 **Spectre-type** 攻擊的根基。

### 異常推測

CPU 在確認某個存取是否合法之前，也可以先推測性地執行後續指令：

```c
// CPU 看到這行，不等 permission check 完成
uint8_t val = *kernel_ptr;         // 可能違規，但 CPU 先讀
uint8_t y = probe[val * 512];      // 推測執行：依賴一個尚未確認合法的讀取
```

等 CPU 確認「等等，那個存取是非法的」，它要 raise 一個 fault、回滾。但在那之前，瞬態執行已經發生了。

這是 **Meltdown-type** 攻擊的根基。

## 瞬態窗口

瞬態窗口（Transient Execution Window）是「從 CPU 開始推測執行瞬態指令」到「CPU 發現推測錯誤並開始回滾」之間的時間段。

```
時間軸 →
──────────────────────────────────────────────────────────────────────
  T0            T1                      T2             T3
  │             │                       │               │
  ▼             ▼                       ▼               ▼
  觸發條件      開始推測執行            發現錯誤         回滾完成
  ─────────     ────────────            ────────         ────────
  if(x<size)    瞬態指令跑起來          size 的真值       架構狀態
  size 在 DRAM  ↓                       從 DRAM 回來      還原到 T0
  要等 ~200     array1[x] 被推測讀      x >= size         前的樣子
  cycles        probe[val*4096]         推測路徑錯了      ─────────
                被推測存取              觸發 squash       但 cache 裡
                ────────────────────    ─────────────     多出來的 line
                ←──── 瞬態窗口 ────►    (rollback)        沒被清掉！
                「不該執行的指令」在     ────────────
                這段時間真的在跑
                並把 cache line
                拉進了快取
──────────────────────────────────────────────────────────────────────
```

這張圖是整個瞬態執行攻擊的核心。記住這個不對稱性：

- **架構狀態**（暫存器值、記憶體）：T3 後完全回到 T0 前的樣子
- **微架構狀態**（cache 內容）：T1 到 T2 之間拉進來的 cache line **留著**

## 回滾為什麼「不乾淨」

CPU 回滾時能還原的，是**架構狀態（architectural state）**：
- 所有通用暫存器的值回到 T0 前的狀態
- 記憶體（RAM）的寫入被取消（Store Buffer 的 pending writes 被 flush）
- Flag registers（RFLAGS）還原

CPU **無法**還原的，是**微架構狀態（microarchitectural state）**：
- **Cache 內容**：瞬態指令把哪些 cache line 拉進來，**這些 line 還留著**
- TLB entries（TLB 被填了就填了）
- Branch predictor 的歷史紀錄（訓練過就訓練過了）
- Line Fill Buffer（DRAM 請求在這裡排隊，MDS 家族打的是這裡）

這個不對稱性是一切的根源：

```
             架構狀態（可見層）       微架構狀態（隱藏層）
             ──────────────────       ──────────────────
回滾後        完全還原 ✓              cache 不還原 ✗
攻擊者能讀    不能讀（保護有效）      能透過 F+R 間接讀 ✓
```

用人話說：CPU 的安全保護（邊界檢查、記憶體保護）是在**架構層**執行的。攻擊者的洩漏通道是**微架構層**（cache timing）。這兩層之間沒有完整的同步機制——這是 Spectre/Meltdown 的根本設計缺陷，不是偶然的 bug，而是「效能優化假設」與「安全隔離假設」之間的結構性衝突。

效能設計者的假設：「回滾後架構狀態乾淨，就等同於那些指令從未執行過。」

安全研究者的反駁：「架構狀態乾淨，不代表可觀測效果為零。cache 是可觀測的。」

## Spectre-type vs. Meltdown-type

了解了瞬態窗口，就能精確地把瞬態攻擊家族分成兩支：

```
                       瞬態執行攻擊
                       ─────────────
             ┌──────────────┴──────────────┐
        Spectre-type                 Meltdown-type
   （推測走錯誤路徑）            （推測越權存取）
             │                           │
   分支預測器被惡意訓練           存取檢查延遲在
   → 推測走了不該走的路           推測執行路徑上
             │                           │
   不需要目標記憶體                需要目標記憶體
   映射在攻擊者 AS                映射在攻擊者 AS
   （惡意的 gadget                （如 KPTI 前的
   在受害者 process 裡就行）       kernel 映射）
             │                           │
     v1: 邊界檢查繞過            Meltdown: 讀 kernel
     v2: 間接跳轉注入            L1TF/Foreshadow
     RSB: 返回位址篡改           MDS: 填充緩衝區
     BHI: 歷史注入
```

**Spectre-type**：
- 根源：分支預測器被訓練、猜錯了方向或目標
- 大部分硬體修不乾淨（因為要修就要殺掉預測能力）
- Part 3 Ch 14–17 的主線

**Meltdown-type**：
- 根源：存取檢查在推測執行路徑上延遲（fault 太慢）
- 需要目標記憶體映射在攻擊者的 AS 裡
- KPTI（kernel page-table isolation）幾乎直接廢掉原始 Meltdown
- Ch 18–19 詳細討論

## Gadget 是什麼

Gadget（在瞬態執行攻擊語境裡）是一段在受害者程式裡本來就存在的程式碼，當它被**瞬態**執行時，會把 secret 依賴的記憶體存取「編碼」進 cache 狀態。

經典的 Spectre-v1 gadget：

```c
// 這段程式碼本身沒問題，但在瞬態窗口裡，x 可以不合法
if (x < array1_size) {                        // 邊界檢查（可被推測繞過）
    uint8_t val = array1[x];                  // 推測讀取：x 可能越界
    volatile uint8_t y = probe[val * STRIDE]; // 把洩漏的值 encode 進 cache
}
```

Gadget 的三個必要元素：

1. **洩漏載入（leaky load）**：推測地讀取受保護的資料（`array1[x]` 的越界值）
2. **傳遞（transmit）**：把洩漏到的值用作某個 cache load 的 index（`probe[val * STRIDE]`）
3. **可觸發性（triggerable）**：攻擊者能控制 `x`，或能間接控制推測路徑的走向

這三個缺一不可：
- 沒有「傳遞」，secret 值在暫存器裡用完就被回滾清掉，外界看不到
- 沒有「可觸發性」，攻擊者沒辦法讓推測執行發生
- 沒有「洩漏載入」，沒什麼可傳遞的

## 為什麼 STRIDE 要大

`probe[val * STRIDE]` 裡的 STRIDE 需要足夠大，通常至少 512，在某些系統上需要 4096（page size）。

**原因 1：每個 secret byte 值佔一條獨立的 cache line**。一條 cache line 是 64 bytes。如果 STRIDE = 64，那麼 probe[0x41\*64] 和 probe[0x42\*64] 在相鄰的 cache line 上，但它們很可能在同一個 cache set（L1 是 8-way set-associative，64 sets，line size 64 bytes——相鄰 64-byte block 落在不同 set，但 L2 prefetcher 可能在讀 probe[0x41\*64] 時也抓入 probe[0x42\*64]）。STRIDE >= 64 bytes 讓每個 secret byte 對應一條獨立的 cache line，但不夠阻擋 prefetcher。

**原因 2：打敗 hardware prefetcher**。Intel 的 L1/L2 有 stride prefetcher：連續存取 `probe[1*64], probe[2*64], probe[3*64], ...`，prefetcher 學到步幅 64 就會預先抓後面的 line。把步幅拉大，prefetcher 學不到規律（或不跨 page boundary）。

**原因 3：page 邊界是天然屏障**。Intel 的硬體 prefetcher 一般不跨 page boundary（4 KB = 4096 bytes）抓 data。STRIDE = 4096 讓每個 probe slot 在不同的 page 上，prefetcher 無法跨頁預測。

在這台 i7-10700 上：
- STRIDE = 512：L2 stream prefetcher 在訓練迴圈的連續存取（probe[1*512], probe[2*512], ...）後會跟進相鄰 slot，產生嚴重噪音（Ch 14 實測：~60–100 個 slot 被誤判為 hit）
- STRIDE = 4096：prefetcher 被頁邊界阻斷，噪音大幅下降（Ch 14 實測：0–3 個 slot 誤判）

### Gadget 的典型變體

除了最簡單的「if + 陣列存取」，實際的 Spectre-v1 gadget 還有以下常見形式：

```c
/* 變體 1：透過指標間接讀取 */
if (untrusted_idx < max_entries) {
    struct Entry *e = table[untrusted_idx];  /* 越界指標 */
    probe[e->type * 512];                    /* 用 entry 的欄位當 index */
}

/* 變體 2：多層間接 */
if (user_offset < buf_size) {
    uint8_t *ptr = buf + user_offset;        /* 越界指標 */
    uint8_t val = *ptr;                      /* 越界讀 */
    probe[val * 512];
}

/* 變體 3：透過 size 計算的越界（整數溢出觸發）*/
if (a + b < max_size) {                      /* a+b 溢出為 0，通過檢查 */
    probe[buffer[a + b] * 512];              /* 實際存取越界 */
}
```

所有這些變體都共享同一個結構：**某個條件檢查** + **依賴被檢查值的記憶體讀取** + **用讀取結果當 probe index**。Spectre-v1 的防禦（如 `lfence` 或 IndexMask）必須對每個這樣的 pattern 都生效。

## Micro-op 視角：指令在 pipeline 裡的生命週期

要真正理解「回滾清掉什麼、留下什麼」，需要追蹤一條指令從 fetch 到 commit 的完整旅程。

```
Comet Lake Pipeline（簡化，實際有更多 stage）：

Frontend（指令供應端）
  ┌──────────────────────────────────────────────────────┐
  │ L1-I fetch → Predecode → Decode → Allocate/Rename   │
  │ (Branch Predictor 在 Fetch 階段就介入預測下一條 IP)   │
  └──────────────────────────────────────────────────────┘
                          │ micro-ops 流
                          ▼
  ┌──────────────────────────────────────────────────────┐
  │                  ROB（Reorder Buffer）                │
  │  每個 entry = 一個 micro-op + 它的執行狀態           │
  │                                                      │
  │  [LOAD r1, [x]] → DISPATCHED                        │
  │  [ADD r2, r1, 1] → WAITING (dep on r1)             │
  │  [MUL r3, r4, r5] → EXECUTING                      │  ← 亂序
  │  [STORE [y], r2] → WAITING (dep on r2)             │
  │  [LOAD r6, [z]] → EXECUTING                        │  ← 亂序跑
  │  ...                                                 │
  └──────────────────────────────────────────────────────┘
                          │ 按程式順序 commit（retire）
                          ▼
  Architecture State（架構狀態，外界看得到的）
  ┌──────────────────────────────────────────────────────┐
  │ register file（rax, rbx, ... 的最終值）              │
  │ memory（DRAM 的最終內容）                            │
  │ RFLAGS、IP 等                                        │
  └──────────────────────────────────────────────────────┘
```

**瞬態指令在 ROB 的生命週期**：

1. Frontend fetch 了越界路徑上的指令（推測執行），送進 ROB
2. 這些指令 execute（讀記憶體、計算），結果暫存在 ROB entry 和物理暫存器裡
3. **執行期間，cache fill 就發生了**——Load 請求送出，cache line 被拉進 L1
4. 上游的分支指令 retire，發現預測錯誤，發出 squash 信號
5. ROB 從 squash 點之後的所有 entry 全部清除
6. 架構狀態回到分支點之前的樣子，IP 重設為正確路徑
7. **但已經填入 cache 的 line，沒有 invalidate 機制**——cache controller 不追蹤「哪些 fill 是來自推測路徑的」

這個設計選擇不是疏忽，是有意的：rollback cache fill 的代價極高（cache 是多層的，要通知所有層級；且正確路徑的執行也可能需要同一條 cache line），性能不合算。設計者的隱含假設：「cache fill 的結果是資料載入了什麼值，但不暴露值本身——只有計算結果才是 secret，而計算結果在 rollback 後消失了。」

Spectre 攻擊揭穿了這個假設：cache 的 occupancy（一條 line 在不在裡面）本身就是信息，透過 timing 可以讀出來。

## 從 CPU 設計者的視角看這個 tradeoff

理解「為什麼 CPU 沒有修這個」需要理解設計約束：

**1. 效能不對稱**：推測執行讓 CPU 速度提升 30–50%（在真實工作負載下）。如果為了「rollback 時清 cache fill」而付出效能代價，代價可能超過推測執行帶來的收益。

**2. 推測深度很大**：現代 CPU 的 ROB 可以有 224–352 條 in-flight 指令，其中很多在推測路徑上。rollback 時如果要追蹤哪些 cache fill 是推測的，需要為每條 in-flight 指令的每個 memory access 記錄 metadata——cache controller 的面積和複雜度倍增。

**3. 推測路徑的 cache fill 通常是有益的**：即使在正確路徑，某些被推測執行的 load 結果在 rollback 後「確實不需要了」，但那條 cache line 很可能很快會被正確路徑再次存取。保留 cache fill 通常比清除更有利於效能。

**4. 沒有人預料到 cache occupancy 本身是信息**：在 Flush+Reload 這個攻擊原語被發現（2014 年 Yarom & Falkner）之前，「cache 存了什麼」和「程式讀到什麼值」被認為是兩個不同的 secret——前者是 timing 可見的，但後者才是真正的 secret。Spectre 把這個假設打破了：讓推測讀取把 secret 值 encode 進 cache occupancy，然後 timing 讀出 cache occupancy 就等於讀出 secret 值。

## 在這台機器上的具體數字

i7-10700 Comet Lake + WSL2 Ubuntu 22.04：

| 量測項目 | 數值 | 來源 |
|---------|------|------|
| L1 cache hit | ~24 cycles | Ch 0 calibrate |
| DRAM miss | ~214 cycles | Ch 0 calibrate |
| ROB 大小 | 224 entries | Intel Architecture 文件 |
| array1_size flush 到 DRAM | ~200 cycles | 造成推測窗口 |
| 有效 Spectre-v1 信號（5000 輪）| ~1–2% hit rate | Ch 14 實測 |
| Misprediction penalty | ~17 cycles | Ch 15 實測 |
| eIBRS status | 啟用（ibrs_enhanced flag）| /proc/cpuinfo |

WSL2 的影響：clflush 可正常工作，但 VM overhead 讓每輪的計時雜訊更大，需要更多輪次積分才能看到信號。eIBRS 主要保護跨 context 的間接分支預測，不影響 intra-process 的條件分支推測（Spectre-v1 的目標）。

## Spectre 發現史：為什麼這麼重要

Spectre 和 Meltdown 在 2018 年 1 月的公開方式也是電腦安全史上的重要事件，值得了解背景：

### 發現時間線

- **2017 年 6 月**：Jann Horn（Google Project Zero）獨立發現了 Meltdown 和 Spectre-v1/v2，通知 Intel/AMD/ARM
- **2017 年 6 月**：Paul Kocher 帶領的獨立研究團隊也發現了 Spectre，與業界協調披露
- **2017 年 7 月–12 月**：業界在保密下開發緩解（KPTI、retpoline、微碼更新），這段時間代號「Project Zero」的 embargo（禁止披露期）維持了約 6 個月
- **2018 年 1 月 3 日**：The Register 的記者從 Linux kernel 的 KPTI patch 倒推出 Meltdown，提前披露；Google、Intel、AMD、ARM 當天緊急協調公開全部細節
- **2018 年 1 月 3 日晚**：Kocher et al. 和 Lipp et al. 同時公開論文預印本

### 為什麼影響這麼大

1. **無法純 SW 修**：Meltdown 需要 KPTI（OS patch），Spectre 需要 retpoline + 微碼更新，缺一不可。沒有哪個廠商單獨能解決。
2. **覆蓋所有現代 CPU**：Intel 2010 年後的幾乎所有型號、AMD 部分型號、ARM 的 Cortex-A 家族都受影響。全球幾乎所有電腦和伺服器。
3. **不能快修**：從 embargo 到完整 patch 需要 6+ 個月，因為涉及 kernel、hypervisor、compiler、microcode，協調多個廠商。
4. **CVSS score**：Spectre-v2 的 CVSS 3.1 score 是 5.6（Medium），但實際危害遠比 CVSS 顯示的嚴重——CVSS 難以捕捉「資訊洩漏類攻擊」的複雜性。

### 對業界的長期影響

- CPU 廠商建立了「Confidential Computing」研究部門，SGX/TDX/SEV 等技術部分是回應
- NIST 修改了安全評估框架，增加了「timing side channel」的評估要求
- 「Constant-time programming」（Ch 32）從密碼學界的小眾技術成為 systems 工程師的必備知識
- Intel 建立了 Bug Bounty 程式，最高獎金 $10 萬 USD 用於 side channel 類漏洞

這些背景告訴你：你在學的不只是一個技術技巧——這是現代低階安全研究最核心的攻防領域。

## 瞬態執行攻擊的分類架構

在 Canella et al. 2019 的框架之前，各種攻擊（Spectre-v1, v2, Meltdown, L1TF...）的分類混亂。他們提出的二維矩陣至今是業界標準：

```
Y 軸：觸發推測執行的原因（Exception Trigger）
  ├─ 分支預測失誤（Branch Predictor Misprediction）
  │    ├─ 條件分支（PHT）→ Spectre-v1
  │    ├─ 間接跳轉（BTB）→ Spectre-v2
  │    └─ Return（RSB）→ Spectre-RSB
  └─ 存取違規推測（Exception Speculation）
       ├─ 記憶體保護（perm/mapping）→ Meltdown-type
       ├─ Supervisor bit（U→S）→ Meltdown-US（原始 Meltdown）
       └─ Present bit（swap out page）→ L1TF / Foreshadow

X 軸：微架構洩漏通道（Covert Channel）
  ├─ L1 Data Cache → 大多數攻擊
  ├─ Line Fill Buffer（LFB）→ MDS/RIDL
  ├─ Store Buffer → MDS/Fallout
  └─ Port Contention → SMT side channel
```

這個矩陣的意義：每個格子代表一類可能的攻擊，有些格子已知有攻擊（已公開 CVE），有些格子理論上可能但還沒有公開 PoC。安全研究者看新的 CPU 功能時，第一步是「把它放進這個矩陣的哪個格子」。

Part 3 這幾章按 Y 軸（觸發原因）組織：Ch 14–17 是 Spectre-type（Branch Predictor Misprediction），Ch 18–19 是 Meltdown-type（Exception Speculation）。

## 瞬態執行與傳統安全模型的衝突

傳統資安的「攻擊面」分析（Threat Model）假設：
- 攻擊者只能透過**明確的 API**（syscall、IPC、網路）觀察受害者的狀態
- CPU 的內部操作（暫存器、pipeline）是不可觀測的
- 「記憶體隔離」（page table）提供了完整的 process 間隔離

這三個假設在瞬態執行攻擊下全部鬆動：

**假設 1 的問題**：`rdtsc` 和 `rdtscp` 是 ISA 規定的計時指令，是合法的 API——攻擊者可以用它觀察 cache 狀態（一個 CPU 內部的時序效應）。

**假設 2 的問題**：pipeline 的時序行為（cache fill）透過 timing 可觀測，即使攻擊者沒有 privilege 存取那些暫存器。

**假設 3 的問題**：page table 提供了「讀/寫記憶體」的隔離，但沒有隔離「cache 佔用狀態」——兩個不同的 process 可以「共享」同一條 cache line（透過物理頁面的共享，F+R 就利用這個），或者 attacker 透過 timing 觀察 victim 的 cache 狀態（不需要共享頁面）。

Spectre 的出現讓 CPU 廠商和 OS 設計者意識到：**信息流不只透過暫存器和記憶體流動，也透過計時流動**。這個認識促成了「speculation barriers」（`lfence`）、「cache partition」（Intel CAT）、「timing noise addition」（瀏覽器降低 timer 精度）等各種防禦措施。

## 對比與取捨

| 維度 | 傳統 cache 側信道（Ch 6–12） | 瞬態執行攻擊（Ch 13+） |
|------|-----------------------------|----------------------|
| 洩漏來源 | 受害者的正常存取行為 | 推測窗口內的「不該執行」指令 |
| 需要 victim gadget | 不需要 | Spectre 需要，Meltdown 需要 race |
| 通道 | Cache timing（F+R/P+P） | Cache timing（完全相同） |
| 跨 context 攻擊 | 需要 shared memory | Spectre-v2 可跨 context |
| 防禦難度 | 中（shared memory 隔離） | 高（要犧牲效能） |
| 攻擊面規模 | 受限於 shared memory 範圍 | Spectre-v1: 每個有 bounds check 的程式 |
| 修法影響效能 | 低（flush 是低頻操作） | 高（每個 syscall 可能需要 IBPB 等） |

注意：洩漏通道（F+R）是**完全相同**的。瞬態執行攻擊只是換了個「誰把 line 放進快取裡」的答案。這個觀察說明 Part 2 的 F+R/P+P 訓練為 Part 3 提供了完整的工具箱——不需要學新的測量技術，只需要理解新的洩漏源。

## 為什麼各種防禦措施的效能代價很高

理解了瞬態執行的機制，就能理解為什麼修法這麼費事：

### KPTI（Kernel Page Table Isolation，修 Meltdown）

Meltdown 的成立條件是「kernel 記憶體映射在 user space 的 page table 裡」（為了加速 syscall 的 kernel/user 切換）。KPTI 把 user mode 和 kernel mode 用**兩套完全分開的 page table**：

- User mode page table：只有 user space 的映射，沒有 kernel 的映射
- Kernel mode page table：完整的 kernel + user 映射

代價：每次 syscall 都要切換 CR3（page table base register），觸發 TLB flush——大約 200–1000 cycles 的額外 overhead，在 syscall 密集的工作負載（如資料庫）下可見影響 5–30%。

Intel 後來加入 PCID（Process Context IDentifier）讓 TLB 可以同時保存多個 context 的 entry，減少了 TLB flush 的代價，但 CR3 切換本身仍有 overhead。

### Retpoline（修 Spectre-v2）

把每個間接跳轉（全系統、每個 library、每個 kernel module 裡）都替換成 retpoline thunk：每個間接 call 從 1 條指令變成 6+ 條。代價：

- 指令數增加 → pipeline 壓力增加
- call/ret 的額外 RSB push/pop → 對巢套深的呼叫有額外 overhead
- 對間接跳轉密集的程式（vtable dispatch、函式指標陣列）：5–15% 效能下降

### `lfence`（修 Spectre-v1 gadget）

在 Spectre gadget 的邊界檢查後面插入 `lfence` 讓 CPU 停止推測執行，等確認結果後才繼續。但：

- `lfence` 是 strong fence，它阻止整條 pipeline 的推測，代價約 10–40 cycles
- 如果每個可能的 gadget 前面都加 `lfence`，對 array-heavy 的程式影響極大
- 難以自動找出所有 gadget（NP-hard 問題），只能靠 compiler hint 或 pattern matching

### eIBRS（修 Spectre-v2，Cascade Lake+）

比 IBRS 便宜得多（常開不需要 per-syscall MSR 切換），代價幾乎可以忽略（< 1%）。但它只修了「user → kernel 的 BTB 污染」，留下了 BHB 的問題（後來由 BHI SW sequence 補丁）。

整體來看：每一個防禦都對應一個效能代價，而且這些代價是累加的。一個有 KPTI + retpoline + eIBRS + BHI-SW + RSB stuffing 的現代 kernel，在最壞情況下比沒有這些防禦的 kernel 慢 15–25%。這就是為什麼從事資料庫、HPC、雲端運算的工程師對 Spectre 的感受比一般用戶強烈得多——他們的工作負載對 CPU 效能最敏感。

## 踩雷集錦

**1. 「回滾後 CPU 的狀態完全乾淨了」**

不對。架構狀態乾淨了，微架構狀態沒有。這個區別是整個瞬態執行攻擊的存在依據。如果回滾真的 100% 乾淨，Spectre/Meltdown 就不可能存在。Intel、AMD、ARM 都在它們的 errata 文件裡確認了這個行為。

**2. 「瞬態窗口很長，能做很多事」**

實際上，瞬態窗口受 ROB 大小限制（Comet Lake: 224 entry）和實際有多少其他 in-flight 指令佔位。在「等 array1_size 從 DRAM 回來」這個場景下，窗口通常只夠執行幾十條指令。Kocher 的 PoC gadget 只需兩次記憶體存取就夠了，因此不需要很長的窗口。

**3. 「關閉 hyperthreading 就能防住 Spectre」**

關 SMT 能防住部分側信道（Port contention，Ch 26），但對 Spectre-v1 intra-process 洩漏沒有影響——攻擊者和受害者在同一個 process 裡，根本沒有跨核心的問題。

**4. 「只有 kernel code 有 Spectre gadget」**

不對。任何有 `if (x < size) use(array[x])` 模式的程式碼都可能是 gadget。瀏覽器的 JIT 引擎（V8、SpiderMonkey）過去也會生成這類模式（Ch 20 補充），JIT 引擎因此被迫加入 gadget 消除邏輯。

**5. 「Spectre 和 Meltdown 是同一個漏洞」**

它們共享「瞬態執行」這個根，但觸發條件不同：Spectre 打的是分支預測器（惡意訓練讓分支走錯），Meltdown 打的是記憶體存取 permission check 的延遲（存取違規被推測執行）。這個區別決定了修法：KPTI 修 Meltdown，retpoline/eIBRS 修 Spectre-v2，這兩者不能互換。

## 架構模型 vs. 微架構模型：兩個不同的世界

「架構」和「微架構」這兩個詞在 CPU 文件裡到處出現，Spectre 讓它們的區別變得至關重要。

### 架構（Architecture / ISA）

架構是 **CPU 和軟體的合約**。它規定：
- 有哪些暫存器（rax, rbx, ..., RFLAGS, IP...）
- 指令的語意（`ADD rax, 1` 把 rax 加 1）
- 記憶體模型（load/store 的順序保證）
- 中斷/異常的語意（什麼時候 fault，fault 後 IP 指哪裡）

x86-64 的 ISA 說：「如果一個 load 指令引發 page fault，所有在該 load 之後的指令效果必須消除，CPU 的架構狀態恢復到 fault 之前的樣子。」

這是合約。合約保了架構層面的正確性。

### 微架構（Microarchitecture）

微架構是**合約的實現方式**。Intel 的 Skylake、AMD 的 Zen 2、ARM 的 Cortex-A77 都實作同一個 x86-64 ISA（前兩個），但微架構完全不同：pipeline 深度、ROB 大小、分支預測器的算法、cache 的層數和大小、物理暫存器的數量...

架構合約沒有規定微架構的實現細節。設計者可以選擇：
- 用多大的 ROB 來裝 in-flight 指令
- 推測執行時 cache fill 是否 roll back（ISA 不要求！）
- TLB 在 context switch 時是否完整 flush（某些 ISA 有 hint，但實作自由）

### 安全假設的裂縫

安全分析傳統上只看「架構層面的可觀測狀態」。如果攻擊者只能透過 ISA 規定的介面（讀暫存器、讀記憶體、系統呼叫）觀察 CPU 的狀態，那麼 ISA 的安全保證就夠了。

但 Flush+Reload 告訴我們：**存取時間（cache latency）也是可觀測的**，而且這個可觀測量**不在 ISA 規範裡**——ISA 只說「load 指令讀取記憶體的值」，沒說「load 要花幾個 cycles」。

這個「可觀測量不在 ISA 規範裡但確實可觀測」的裂縫，是所有微架構側信道攻擊的共同基礎：

```
ISA 規定可見的              微架構額外可觀測的（ISA 未規定）
──────────────              ─────────────────────────────────
暫存器值                    cache 佔用狀態（via timing）
記憶體內容                  TLB 狀態（via memory access timing）
RFLAGS                     port contention（via execution timing）
FP 狀態暫存器               branch predictor 狀態（via timing）
```

瞬態執行攻擊是這個裂縫的最精密利用：它同時利用了「架構合約保證了 rollback 清除架構狀態」和「微架構實現沒有清除 cache」這兩者的不一致性。

## 進階：再往深一層

### ROB 大小與瞬態窗口的理論上限

ROB（Reorder Buffer）是「已 dispatch 但未 commit」的指令的記錄結構。Comet Lake 延續 Skylake 微架構，ROB 大小為 **224 entry**（Intel Sunny Cove/Ice Lake 擴到 352）。這意味著同時最多有 224 條 in-flight 指令——瞬態指令要在這個空間裡搶位置。

實際有效的瞬態 gadget 指令數更少，因為：
- 其他 in-flight 指令（訓練 loop 的尾部、訓練 loop 本身的 fetch）也在 ROB 裡佔位
- 投機路徑的前端（Frontend）需要時間 fetch+decode 瞬態指令

### 微架構洩漏通道有多少個

除了 cache，以下微架構結構也曾被用作洩漏通道（Ch 19–20 詳細講）：
- **Line Fill Buffer（LFB）**：DRAM 讀取請求排隊的地方。MDS 家族（RIDL/TAA）打的是這裡
- **Store Buffer**：尚未提交到 cache 的寫入暫存。MDS 的 MFBDS/Fallout 打這裡
- **L1 Data Cache**：L1TF / Foreshadow 在 SGX 環境下打這裡

### 為什麼不能直接觀察 ROB 或暫存器

這個問題很多人問：「如果瞬態指令在 ROB 裡執行，為什麼不直接 dump ROB？」

因為 ROB 不是架構可見的記憶體，它是 CPU 內部的一個佇列結構，沒有任何 ISA 指令能讀 ROB 的內容。暫存器（rax、rbx...）有硬體 rename table 對應到物理暫存器，但在 rollback 之後，那些物理暫存器被回收，值消失。唯有 cache 是一個有 CLFLUSH 等 ISA 指令可以間接操作的可觀測結構，所以成了通道。

## 用 CPUID 確認這台機器的特性

在深入 Spectre 之前，值得花 2 分鐘確認這台 CPU 的相關特性：

```bash
# 在 WSL2 Ubuntu 裡
cpuid -1 | grep -i 'spec\|ibrs\|stibp\|ssbd\|arch_cap' | head -20

# 或直接看 cpuinfo
cat /proc/cpuinfo | grep -i 'ibrs\|stibp\|ssbd\|spec' | head -5

# 看 vulnerability 狀態
ls /sys/devices/system/cpu/vulnerabilities/
cat /sys/devices/system/cpu/vulnerabilities/spectre_v1
cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
```

在這台 i7-10700 上的輸出：
```
$ cat /sys/devices/system/cpu/vulnerabilities/spectre_v1
Mitigation: usercopy/swapgs barriers and __user pointer sanitization

$ cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
Mitigation: Enhanced / Automatic IBRS; IBPB: conditional; PBRSB-eIBRS: SW sequence; BHI: SW loop, KVM: SW loop
```

解讀：
- `spectre_v1`：「Mitigation」表示有軟體緩解，但 **hardware 仍然 vulnerable**（kernel 標記為有問題但有修）。intra-process Spectre-v1 仍然可以觸發。
- `spectre_v2`：「Enhanced / Automatic IBRS」是 eIBRS，這是 Comet Lake 的硬體特性，防止 user → kernel 的 BTB 污染。這解釋了為什麼跨 privilege 的 Spectre-v2 在這台機器上不可重現。

還可以查看 flags：
```bash
grep flags /proc/cpuinfo | head -1 | tr ' ' '\n' | grep -E 'ibrs|stibp|ssbd|ibpb'
```
輸出：`ssbd ibrs ibpb stibp ibrs_enhanced`——確認了 eIBRS 和其他緩解機制都在。

## 動手練習

1. **量化瞬態窗口長度**：在 victim gadget 裡的 leaky load 和 transmit load 之間插入 N 個 `asm volatile("nop")` NOP 指令（N 從 0 到 200），觀察 F+R signal（score/rounds）如何隨 N 增加而下降。這個曲線就是瞬態窗口長度的經驗估計。

2. **比較有無 flush size 的差異**：用 Ch 14 的 PoC，分別在 flush `array1_size` 和不 flush 的條件下跑 2000 輪，記錄 signal。這個對比直接量化「推測窗口長度」對攻擊可行性的影響。

3. **用 CPUID 確認 ROB 大小**：Intel 不直接透過 CPUID 揭露 ROB 大小，但可以透過 `cpuid -l 0x1a --one-cpu` 和 Intel Architecture 文件對照。試著找到這台 CPU 的 `CPUID.(EAX=4, ECX=3)` 輸出，確認 L3 cache 的大小和 associativity。

4. **觀察 rollback 的架構可見性**：在 victim() 裡加 `printf("x=%zu\n", x)` 並故意呼叫越界版本。觀察 printf 有沒有在回滾的情況下被呼叫。（提示：如果推測的 printf 真的跑了，你應該會看到輸出——但由於 I/O 是有副作用的架構操作，CPU 通常不在推測路徑上跑它。這個實驗說明 gadget 為什麼只能是記憶體讀取，不能是 I/O。）

5. **閱讀 CPU 的 errata 文件**：從 [Intel ARK](https://ark.intel.com/) 找 i7-10700 的 errata 或 specification update PDF，搜索「speculative」。看看 Intel 是如何在官方文件裡描述這些問題的（通常用非常謹慎的語言）。與 Kocher 2019 論文的描述對照，看兩邊對同一個問題的措辭差異。

## 本章重點整理

- **亂序執行**：CPU 不按程式順序執行，但按程式順序 commit，來隱藏 DRAM 延遲。
- **推測執行**：亂序執行的延伸——猜分支方向，先執行猜測路徑上的指令。
- **瞬態指令**：被推測執行的指令。從架構角度「永不存在」，但微架構角度「真的跑了」。
- **ROB 回滾**：清除架構狀態（暫存器、記憶體寫入），但**不清除 cache**。
- **洩漏本質**：瞬態指令的 cache 副作用在回滾後留下來，被 F+R 讀出。
- **兩支家族**：Spectre-type（分支預測錯誤）和 Meltdown-type（越權讀取推測）。
- **Gadget 三要素**：leaky load + transmit（cache side effect）+ triggerable。

## 自我檢核

1. 為什麼 CPU 設計者要做「推測執行」？如果沒有推測執行，現代 CPU 的理論效能會下降多少？
2. ROB 回滾之後，下列哪些東西被還原，哪些沒有：通用暫存器值、L1 cache 內容、TLB entry、DRAM 中的寫入？
3. Spectre-v1 和 Meltdown 都是瞬態執行攻擊。請說明它們「觸發推測執行的原因」有何不同，以及為什麼 KPTI 能修 Meltdown 但不能修 Spectre-v1。
4. Gadget 的「傳遞」步驟（`probe[val * STRIDE]`）為什麼必須是記憶體存取，而不能是計算（如 `val + 1`）？計算的結果為什麼在回滾後消失？
5. 為什麼把 `array1_size` 用 clflush 踢出快取可以「拉長推測窗口」？如果 `array1_size` 在 L1 cache 裡，推測窗口大概有多短？

## 延伸閱讀

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  優先讀 Section II（Background）和 Section III（Spectre v1）。Section II 的 2.1–2.3 把推測執行的背景、ROB 的角色、瞬態指令的定義都交代得很清楚。本章的模型直接來自這裡。

- **[A Systematic Evaluation of Transient Execution Attacks and Defenses](https://arxiv.org/abs/1811.05441)** — Canella et al., USENIX Security 2019
  讀 Section III（Systematization Framework）。他們把瞬態執行攻擊按「哪個結構觸發推測」和「透過哪個微架構結構傳遞」分成一個二維表格。本章的「兩支家族」就是那張表的簡化版。關聯：Ch 21 會用整章重建這個分類框架。

- **[Intel 64 and IA-32 Architectures Optimization Reference Manual](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)** — Intel
  讀 Chapter 2（Processor Architecture Overview），特別是 2.1（Overview of Execution）和 2.3（The Out-of-Order Execution Engine）。ROB 大小 224 entry 的數字來自這裡的 micro-op fusion 和 ROB 相關章節。

- **[Transient.fail](https://transient.fail/)** — 瞬態執行攻擊即時分類追蹤
  按照「哪個硬體結構被利用」×「攻擊面」的矩陣持續更新所有已知的瞬態執行變體。本課 Part 3 結束後，這個網站是追蹤新洞的最快入口。

---

→ [下一章：Ch 14 Spectre v1（Bounds Check Bypass）](14-spectre-v1-bounds-check-bypass.md)
