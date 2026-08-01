# Ch 24 — Rowhammer 演進與防禦

> **目標**：走一遍 Rowhammer 從 2014 年到 DDR5 時代的完整軍備競賽：每個防禦出現 → 隔多久被繞 → 用什麼技術繞。理解這場競賽的結構，你才能看懂「現在的 DDR5 是不是真的安全了」這個問題的正確答案。

## 軍備競賽的結構

Rowhammer 的防禦歷史可以濃縮成一個重複出現的劇本：

```
業界找到防禦                  研究者找到繞法
      │                              │
      ▼                              ▼
TRR（2015）      ──────────►  TRRespass（2020）、Blacksmith（2021）
ECC（推薦使用）  ──────────►  ECCploit（2018）
Refresh 加速    ──────────►  Half-Double（2021）
DDR5 新機制     ──────────►  RowPress（2023）、持續研究中
```

每一個防禦都基於某個對 Rowhammer 物理行為的**假設**；每一個繞法都找到這個假設不成立的情況。

## 第一代防禦：增加 Refresh 頻率

### 直覺與問題

最直接的想法：如果 bit flip 需要在 64ms refresh 週期內累積夠多次 DRAM 存取，那麼縮短 refresh 週期讓電容充電更頻繁，干擾就來不及累積。

JEDEC 在 2015 年更新了一個機制：允許記憶體控制器用**兩倍 refresh 頻率**（2× rate，每 32ms refresh 一遍而非 64ms）在高溫環境下工作。部分系統廠商推薦使用者開啟這個選項。

### 為什麼不夠

兩倍 refresh 只把「需要翻位的存取次數」從 ~10 萬次增加到 ~20 萬次。但 DRAM 存取速度也在增加——DDR4-3200 的 CAS latency 讓一個 hammer loop 在 64ms 裡可以做到 **200 萬次 DRAM 存取**，兩倍 refresh 的門檻完全追不上。

此外，提高 refresh 頻率有明顯的**效能代價**：refresh 時 bank 無法存取，兩倍 refresh 讓記憶體頻寬損失 1–3%（在記憶體密集型任務上可量測到）。廠商不願意預設開啟。

## 第二代防禦：Target Row Refresh（TRR）

### JEDEC DDR4 的 TRR 機制

2017 年，JEDEC 把 Target Row Refresh 寫進 DDR4 標準。TRR 的想法：不是所有 row 都需要額外 refresh，只需要 refresh **被高頻存取的 row 的鄰居**（victim row）。

記憶體控制器或 DRAM 晶片自己追蹤「哪些 row 被存取的次數快要達到危險門檻」，當某個 row 的存取計數超過一個閾值（Act Count，activation count threshold），系統在下一個 refresh 指令時額外發出 TRR 指令，refresh 那個 row 的相鄰 row。

### TRR 的實作差異

「TRR」是一個傘狀名詞，不同廠商的實作差異極大：

| 實作類型 | 誰在追蹤 | 追蹤哪幾個 row | 是否公開規格 |
|---------|---------|--------------|-------------|
| on-DRAM TRR（pTRR） | DRAM 晶片本身 | 1–2 個（廠商決定）| 否（通常保密）|
| MAC-based TRR | 記憶體控制器 | 多個（取決於硬體計數器數量）| 部分開放 |
| PARA | DRAM 側 probabilistic | 機率性 refresh 鄰居 | JEDEC 開放 |

Samsung、SK Hynix、Micron 三大廠各自的 TRR 實作細節都是商業機密，沒有公開 JEDEC 規格。這讓攻擊者研究「如何繞 TRR」需要先逆向 DRAM 晶片的行為。

### TRRespass：暴力逆向 TRR 並繞過它

Frigo et al.（IEEE S&P 2020）發表了 **TRRespass**，系統性地繞過了各廠商的 TRR 實作。

**TRRespass 的核心觀察**：

TRR 機制必然有一個**有限的追蹤計數器**（硬體成本不允許追蹤所有 row 的存取頻率）。如果攻擊者能找到一個 hammer 樣式，讓 TRR 追蹤的計數器被「無用」的 row 佔滿，真正的 aggressor row 就逃脫了 TRR 的追蹤：

```
假設 TRR 追蹤最多 4 個 aggressor row：

正常 double-sided hammering（被 TRR 抓住）：
  hammer A、hammer B（TRR 追蹤 A 和 B → 在 refresh 時 refresh 它們的鄰居）

TRRespass（讓 TRR 計數器溢出）：
  hammer A、B、C、D、E、F（6 個 aggressor）
  TRR 計數器只能追蹤 4 個，A 和 B 的計數被 evict 或稀釋
  結果：TRR 可能不 refresh A 和 B 的鄰居 → victim row 仍被翻
```

TRRespass 的工具自動探索不同的 n-sided hammering 樣式，找到每種 DRAM 的 TRR 追蹤計數器上限，然後選擇剛好超過這個上限的 aggressor 數量。

**TRRespass 的結果（引自論文）**：
- 測試了 42 條 DDR4 DIMM（Samsung、Hynix、Micron）
- 所有 42 條都找到了能觸發 bit flip 的 n-sided hammering 樣式（n 從 2 到 19）
- 結論：「TRR is insufficient to prevent Rowhammer attacks on DDR4」

## 第三代防禦：ECC 記憶體

### ECC 的工作原理

ECC（Error-Correcting Code）記憶體在每個 64-bit 資料字後面附加一個 8-bit 校驗碼（Hamming code 的變體），能夠：
- **偵測並糾正** 1-bit 錯誤（Single Error Correction, SEC）
- **偵測**（但無法糾正）2-bit 錯誤（Double Error Detection, DED）

ECC 的假設：Rowhammer 在一個 cache line 裡只翻一個 bit，ECC 可以糾正它，攻擊失效。

### ECCploit：繞過 ECC

Frigo et al.（CCS 2018，NDSS 2020 完整版）發表 **ECCploit**，展示了即使有 ECC，Rowhammer 仍然可以被利用。

**ECCploit 的兩個關鍵觀察**：

**觀察 1：ECC 糾正是計時可觀察的副作用**

ECC 在糾正錯誤時需要額外的時間（讀出 64-bit data + 8-bit ECC → 計算 syndrome → 糾正 → 回寫）。這個「糾正延遲」是可以被攻擊者計時觀察到的（~ 幾十 cycles）。

這意味著：即使 ECC 糾正了 bit flip，攻擊者仍然可以**偵測到 bit flip 發生的事實**。這讓攻擊者知道哪個 victim row 「快要翻」，可以協調攻擊時機。

**觀察 2：在 64ms 內翻同一個 cache line 的多個 bit 讓 ECC 失效**

如果在一個 ECC 糾正週期完成之前，攻擊者再翻同一 cache line 的另一個 bit，總共翻了 2 個 bit，ECC 的 SEC-DED 只能偵測到 2-bit 錯誤但無法糾正——這通常導致 memory controller 的 MCE（Machine Check Error），讓系統當機或進入不確定狀態。

更進一步，如果翻了 3 個 bit，某些 ECC 配置（3-bit 錯誤）的校驗碼可能「剛好」讓 syndrome 計算把它誤判為另一種 1-bit 錯誤，讓 ECC 「糾正」成了一個錯誤的值（controlled bit flip）。

**ECCploit 的結果**：
- 在特定 ECC DDR4 模組上，在 64ms 內製造 3 個 bit flip 的機率足夠高
- 利用 3-bit 同時翻的特定組合，讓 ECC 把一個 byte 的值「錯誤糾正」成攻擊者期望的值
- 在 OpenSSH 公鑰格式上展示了概念驗證（可讓一個公鑰 byte 翻成期望的值）

ECCploit 的成功率不高（需要特定 DRAM 型號，3-bit 同時翻的機率比 1-bit 低很多），但它打破了「ECC = 完全安全」的假設。

## 第四代攻擊：Half-Double 與非均勻 Hammering

### Half-Double：新距離假設

Google Project Zero（Canella et al., 2021）發現了 **Half-Double**，一個 TRR 架構設計的根本漏洞：

TRR 的設計基於一個**距離假設**：只有「物理上直接相鄰」的 row 才會被干擾。因此 TRR 只 refresh 被 hammer 的 row 的直接鄰居（距離 1 的 row）。

Half-Double 展示：**在現代 DRAM 裡，距離 2 的 row 也可能受到顯著干擾**——弱一些，但足夠在累積後翻位。

```
  Row N-2  ←── 弱干擾（半距離，Half-Double 的目標）
  Row N-1  ←── 直接相鄰（TRR 會保護）
  Row N    ←── aggressor（被 hammer）
  Row N+1  ←── 直接相鄰（TRR 會保護）
  Row N+2  ←── 弱干擾（Half-Double 的另一個目標）
```

Half-Double hammer 樣式：大量 hammer Row N（主 aggressor），少量 hammer Row N-2（次 aggressor），讓 N-2 的干擾「傳播」到 N-1，再傳播到 N，同時 N 的大量 hammer 造成 N-1 的強干擾，兩個方向的累積干擾從兩側夾住 victim row：

```
Hammer 分配：
  Row N    : 99.9% 的存取（主 aggressor）
  Row N-2  :  0.1% 的存取（次 aggressor，「half」）

干擾傳播：
  N → N-1（強，TRR 試圖保護）
  N-2 → N-1（弱，TRR 忽視因為 N-2 存取次數太少）
  兩個方向的干擾在 N-1 累積 → bit flip
```

Half-Double 讓 TRR 的「只保護直接鄰居」假設失效，因為攻擊者可以透過距離 2 的間接干擾打到受 TRR 保護的 row。

### Blacksmith：非均勻 Hammering 繞 TRR

Jattke et al.（IEEE S&P 2022，Blacksmith）發現了另一個維度的 TRR 弱點：TRR 計數器追蹤的是「被 hammer 的 row」，且預設存取模式是**均勻**的（aggressor row 以固定節奏重複）。

TRR 的 Act Count 閾值是針對均勻 hammering 設計的——如果 hammer 樣式是不均勻的（不同 aggressor 的存取頻率不同、存取順序有變化），TRR 的計數器可能無法正確追蹤，閾值的判定失準。

Blacksmith 的創新：**非均勻（non-uniform）hammering 樣式**，讓 TRR 的計數器追蹤失效：

```
傳統均勻 hammering（TRR 容易追蹤）：
  A B A B A B A B A B ...  （固定節奏，2個 aggressor 各 50%）

Blacksmith 非均勻 hammering（TRR 難以追蹤）：
  A A A B A A A C A A A B C A ...  （頻率不均，有多個 aggressor，不規律）
```

Blacksmith 工具自動搜索非均勻 hammer 樣式，在每種 DRAM 上找到最有效的樣式：
- 測試了 40 條 DDR4 DIMM（Samsung、Hynix、Micron、SK Hynix，含 DDR4-2666 到 DDR4-3200）
- **所有 40 條都找到了 bit flip**，包括之前被認為有效 TRR 保護的型號
- 結論：DDR4 的 TRR 整體不足以防禦 Rowhammer

Blacksmith 是 2022 年前 Rowhammer 研究最重要的結論之一，直接推動了 JEDEC 重新設計 DDR5 的防禦機制。

## DDR5 的新機制

### PRAC（Per-Row Activation Counting）

DDR5 引入了 **PRAC（Per-Row Activation Counting）**，比 TRR 更系統化：

- DRAM 晶片本身維護一個計數器陣列，記錄**每個 row** 被 Activate 的次數
- 當任何 row 的計數超過 `RFM threshold`（Refresh Management 閾值），主動通知記憶體控制器
- 記憶體控制器發出 `RFM`（Refresh Management）指令，讓 DRAM refresh 被高頻存取的 row 的鄰居

PRAC 相比 TRR 的優勢：
- **真正 per-row 計數**，不是有限的計數器被 evict
- **DRAM 自主追蹤**，不依賴記憶體控制器的猜測
- **標準化**：JEDEC DDR5 規格明確定義，不再是廠商黑箱

### RFM（Refresh Management）

DDR5 的 RFM 指令讓記憶體控制器和 DRAM 晶片合作：

```
DRAM 發出 Alert_n 信號 → 記憶體控制器發 RFM 指令 → DRAM 執行 targeted refresh
```

RFM 的 `RAAIMT`（Rolling Accumulated ACT IMmediate Threshold）參數定義了觸發 RFM 的累積 activation 次數。這個值由 DRAM 廠商在 SPD（Serial Presence Detect）裡公開，比 TRR 的黑箱閾值透明得多。

### DDR5 是否真的安全？

**理論上更好，但研究仍在進行**。

2023 年，Kim et al.（RowPress, ISCA 2023）發現了 **RowPress**：與 Rowhammer 不同，RowPress 不是通過「高頻率存取一個 row 多次」，而是「讓一個 row 保持在 Open（Activated）狀態很長時間」來干擾相鄰 row：

```
傳統 Rowhammer：
  Activate Row N → 存取 → Precharge → Activate Row N → ...
  每次 Activate/Precharge 產生電流脈衝干擾
  需要幾十萬次脈衝

RowPress：
  Activate Row N → 保持 Open（不 Precharge）→ 等待 → Precharge
  持續的 Word Line 高電壓讓相鄰 row 的電容緩慢洩漏
  只需要保持 Open 夠長時間（例如 32ns × 很多次）
```

DDR5 的 PRAC 是針對 Rowhammer（activation 計數）設計的，RowPress 的 activation 次數很少（因為它讓每個 activation 保持很長時間），可能躲過 PRAC 的計數門檻。Kim et al. 在 DDR5 DIMM 上展示了 RowPress 誘發的 bit flip。

**現況**（截至 2024 年）：
- DDR5 的 PRAC/RFM 比 DDR4 的 TRR 更強，但研究社群仍在尋找繞法
- RowPress 展示 DDR5 不是絕對安全
- 這場軍備競賽預計還會繼續幾年

## 防禦機制全景

### 硬體層面

| 防禦機制 | 引入時間 | 原理 | 被繞了嗎 | 繞法 |
|---------|---------|------|---------|------|
| refresh 頻率加倍 | ~2015 | 縮短 refresh 週期，減少可累積干擾的時間 | 是 | DRAM 速度更快，hammer 次數增加更多 |
| TRR（on-DRAM） | DDR4 ~2017 | 追蹤被高頻存取的 row，refresh 其鄰居 | 是 | TRRespass（計數器溢出）、Blacksmith（非均勻 hammer） |
| ECC SEC-DED | 伺服器標配 | 糾正 1-bit 錯誤，偵測 2-bit | 部分 | ECCploit（3-bit 同時翻，利用 ECC 誤糾正）|
| PRAC + RFM | DDR5 ~2023 | Per-row 計數，更精確的 targeted refresh | 研究中 | RowPress（長保持 Open，不增加 activation 計數）|

### 軟體/OS 層面

| 防禦機制 | 原理 | 限制 |
|---------|------|------|
| KPTI（Kernel Page-Table Isolation） | 讓 kernel PTE 不在使用者 page table 裡，PTE flip 不直接給使用者讀寫 | 增加系統呼叫 overhead；不防 ECC bypass 場景 |
| /proc/pagemap 限制非 root 讀取 | 讓攻擊者看不到實體位址，DRAM 位址逆向更難 | 部分攻擊不需要 /proc/pagemap（用 hugepage） |
| 關閉 KSM（Kernel Samepage Merging） | 防止 Flip Feng Shui 利用共享物理頁 | 增加記憶體用量（KSM 本是省記憶體的） |
| THP（Transparent Hugepage）控制 | hugepage 讓 DRAM 位址逆向更容易，可關閉 | 關 THP 損失部分效能 |
| 記憶體分配隨機化（buddy allocator 改進） | 讓攻擊者更難預測 kernel 物件的物理位置 | 不能完全防禦，只是提高難度 |

### 雲端廠商的應對

2015 年 Seaborn 的 Project Zero 報告發布後，主要雲端廠商採取了一系列措施：

- **Google Cloud**：強制所有實例使用 ECC 記憶體（2015），關閉 KSM（2016），持續更新微碼以支援 TRR
- **AWS**：在多租戶環境強制 ECC，推薦 Nitro hypervisor 的記憶體隔離
- **Azure**：ECC + TRR + hypervisor 層隔離

但雲端環境的防禦也有盲點：RDMA-based Rowhammer（直接 DMA 繞過 OS 的記憶體存取）、VM-escape 後的 Rowhammer、以及針對 DDR5 環境的 RowPress 都是仍在研究的攻擊面。

## 一張圖看懂軍備競賽

```
2014  Kim et al. ISCA'14     ── 發現 Rowhammer 現象（DDR3）
       │
2015  Seaborn Project Zero   ── Rowhammer → 提權 exploit（PTE flip）
       │
       ├─ 防禦：ECC 推廣、refresh 加速
       │
2016  Flip Feng Shui         ── 跨 VM 攻擊（KSM）
      Drammer               ── ARM/Android 上的 Rowhammer
       │
       ├─ 防禦：DDR4 引入 TRR（廠商自定義、不透明）
       │
2018  ECCploit               ── 繞 ECC（3-bit 同時翻）
       │
2020  TRRespass              ── 繞 TRR（n-sided hammering 讓計數器溢出）
       │
2021  Half-Double            ── 繞 TRR（距離 2 的干擾傳播）
       │
2022  Blacksmith             ── 繞所有 DDR4 TRR（非均勻 hammering）
       │
       ├─ 防禦：DDR5 引入 PRAC + RFM（per-row 計數、標準化）
       │
2023  RowPress               ── 新機制：保持 row open 而非高頻存取
                                初步展示能繞過 DDR5 的 PRAC 門檻
       │
2024+ 研究繼續...
```

## 對比與取捨

| 防禦 | 成本 | 效能影響 | 防禦強度 | 已被繞 |
|------|------|---------|---------|--------|
| ECC | 高（需 ECC DIMM + 對應主機板）| 幾乎無（< 1%）| 中（能防 1-bit flip） | 部分（ECCploit） |
| 2× Refresh | 低（BIOS 設定）| 低（1-3%）| 弱 | 是（hammer 速度更快）|
| TRR（DDR4）| 無（廠商內建）| 幾乎無 | 中 | 是（TRRespass, Blacksmith）|
| PRAC/RFM（DDR5）| 無（DDR5 標準）| 低（refresh overhead）| 高（目前最強）| 部分（RowPress）|
| KPTI | 無（kernel 更新）| 低（1-5%，取決於 syscall 密度）| 防 PTE 攻擊 | 不完整 |

## 踩雷集錦

1. **「TRR 等於 Rowhammer 完全解決」——錯誤**：TRR 的各廠商實作細節保密，且 TRRespass 和 Blacksmith 分別找到計數器上限繞法和非均勻樣式繞法，在所有測試的 DDR4 DIMM 上都找到了 bit flip。TRR 是「提高門檻」，不是「完全消除」。

2. **「ECC 記憶體讓 Rowhammer 不可能成功」——被 ECCploit 打破**：ECC 的 SEC-DED 只能糾正 1-bit 錯誤。ECCploit 通過在一個 cache line 翻 3 個 bit 讓 ECC 誤糾正，或讓 ECC 的糾正操作本身的計時差異被觀察到。ECC 是非常有效的緩解，但不是不可繞。

3. **「DDR5 用了 PRAC 就徹底安全」——言之過早**：RowPress（ISCA 2023）展示了即使在 DDR5 上，通過「保持 row 長時間 Open」而非「高頻 activation」可以繞過 PRAC 的計數門檻。JEDEC 已在持續更新 DDR5 規格以應對 RowPress。

4. **「只要在軟體層面打補丁就能完全防禦 Rowhammer」——不可能**：Rowhammer 的根源是 DRAM 的物理特性（cell 電容大小、cell 間距縮小後的電磁耦合），軟體補丁（KPTI、/proc/pagemap 限制）只能縮小攻擊面，無法阻止 bit flip 的物理發生。根本修復需要更好的 DRAM 設計或硬體層面的徹底解決。

5. **「Rowhammer 只對 DDR3 有效，DDR4 已經安全」——被 Blacksmith 否定**：這個誤解在 TRR 引入初期很流行。Blacksmith 2022 年系統性地測試了 40 條 DDR4 DIMM 並在全部上找到 bit flip，明確否定了這個觀點。

6. **「Half-Double 只是理論」——不對，是實際威脅**：Google Project Zero 2021 年展示了 Half-Double 在特定 DDR4 DIMM 上能在幾分鐘內翻出 bit flip，並討論了這對 TRR 架構的根本性影響。這是對 TRR「只保護直接鄰居」設計假設的實際否定。

## 進階：再往深一層

**Rowhammer 的物理根源：cell 縮小帶來的困境**

每一代 DRAM 工藝節點縮小，cell 電容和鄰近 cell 之間的距離都在減少，導致電磁耦合更強。這是 Rowhammer 隨工藝演進而變嚴重的根本原因：最早的 DDR3（2007 年）並沒有 Rowhammer 問題；2010 年後的工藝縮小讓 disturbance errors 變得可觸發。DDR5 的新機制（PRAC）是在不改變工藝的前提下用電路補丁，但 DRAM 廠商同時也在硬體設計上做改進（更厚的 Word Line 電場屏蔽、更大的 sense amplifier margin）。

**JEDEC LPDDR5 與 LPDDR5X（行動裝置 DRAM）**

行動裝置用的 LPDDR（Low Power DDR）也有 Rowhammer 問題，且比桌機 DRAM 更難處理——LPDDR 的存取樣式由 SoC 記憶體控制器決定，且工藝縮小更激進。Arm 和高通都在 SoC 的記憶體控制器裡加入了類似 TRR 的機制（稱為 Targeted Refresh Management, TTRM），但同樣面臨 TRRespass 類型的繞法風險。

**DRAM 測試工具**：Intel/AMD 記憶體控制器的性能計數器（如 `UNC_M_ACT_COUNT`，Uncore Memory Controller activation count）可以讓你看到「真實的 DRAM activation 頻率」，是研究 TRR 觸發條件的有用工具。在原生 Linux + perf 上可以用 `perf stat -e uncore_imc/cas_count_read/` 等事件觀察。WSL2 不支援 uncore PMU，需要裸機。

**Hammer Suite 與系統性 DRAM 測試**：Pessl et al.、TRRespass 和 Blacksmith 都提供了開源的 DRAM 測試工具鏈。其中 Blacksmith 的 `fuzzer` 模組能自動搜索 2 到 30+ 個 aggressor 的非均勻 hammer 樣式，在幾小時到幾天內對一條 DIMM 完成系統性測試，並輸出「哪些 row 組合在哪個 hammering 頻率下最容易翻位元」的熱力圖。這類工具是 DRAM 廠商用來測試自家新品的同樣方法，也是研究者驗證新防禦是否有效的標準流程。

**Rowhammer 的未來：工藝縮小的終點**

DRAM cell 縮小的趨勢不會停止——DDR5、LPDDR5X、HBM（High Bandwidth Memory）的工藝都在持續縮小。隨著 cell 越來越小，每個 cell 漏電時間越短（因為電容越小），TRR/PRAC 的閾值設定就越難達到「既不影響效能又足夠保護」的平衡。

長期來看，可能的硬體根本解法包括：
- **cell 設計改進**：更大的電容對電晶體比例，更好的 Word Line 電場屏蔽
- **3D DRAM**（如 High Bandwidth Memory, HBM）：垂直堆疊讓 row 之間的物理距離特性不同，但也帶來新的 disturbance 行為
- **Processing In Memory（PIM）**：把 logic 移到 DRAM 旁邊，讓 refresh 邏輯能在更細的粒度上響應干擾

這些不是近期解法，但顯示 DRAM 工業界已在認真思考「不能只靠 refresh 邏輯的修補」。

## 動手練習

1. **TRR 概念模擬**：用 C 實作一個簡化的 TRR 計數器模型：維護一個大小為 K 的計數器陣列（代表 TRR 能同時追蹤的 aggressor row 數量），接受一串 row activation 序列，輸出「哪些 activation 被 TRR 追蹤到、哪些逃脫了追蹤」。然後實作 TRRespass 的攻擊邏輯：找出讓計數器溢出的最小 aggressor 數量。

2. **ECCploit 數學驗證**：查詢 JEDEC 的 SEC-DED Hamming code（72-bit codeword = 64-bit data + 8-bit ECC）的 parity check 矩陣。計算：如果同時翻 data bit 7、15、21 三個 bit，新的 syndrome 是什麼？SEC-DED 電路會把這個 3-bit 錯誤誤判為哪個 1-bit 錯誤？（這就是 ECCploit 的原理）

3. **Blacksmith 工具試用**（需裸機 + root）：如果你有裸機 Linux 環境：
   ```bash
   git clone https://github.com/comsec-group/blacksmith
   cd blacksmith && make
   sudo ./blacksmith --dimm-id 0 --runtime-limit 300
   ```
   工具會自動搜索非均勻 hammer 樣式並測試你的 DRAM，輸出是否找到 bit flip。

4. **防禦效果文獻追蹤**：讀 JEDEC 的 DDR5 標準（公開部分）裡關於 PRAC 和 RFM 的規格（搜尋「JEDEC JESD79-5C」或「DDR5 SDRAM Standard」），找出 `RAAIMT` 參數的定義範圍，思考攻擊者需要在 64ms 內做多少 activation 才會觸發 RFM。

## ECCploit 的計時偵測機制

ECCploit 最精妙的部分是「偵測 ECC 正在糾正錯誤」的計時側信道。當 ECC 偵測到 1-bit 錯誤並糾正時，記憶體讀取的延遲會比正常情況多幾十個 cycles——因為糾正流程需要讀 64-bit data + 8-bit ECC → 計算 syndrome → 識別錯誤位置 → 糾正 → 回傳正確值。

這個延遲差異（~20–60 cycles，取決於 DRAM 和 memory controller 型號）可以用 `rdtscp` 計時偵測：

```
正常讀取（無錯誤）：   244 cycles（DRAM MISS）
ECC 糾正讀取：         264–300 cycles（多了糾正流程的 overhead）
門檻設在兩者之間，一旦偵測到「比正常 MISS 更慢」的讀取，說明 ECC 正在糾正某個 bit flip
```

這讓攻擊者能在「ECC 尚未讓系統崩潰（2-bit 錯誤）」之前就知道 bit flip 正在發生，精準掌握翻位的時機窗口。ECCploit 就是在這個窗口裡快速再翻第二個 bit，讓 ECC 看到的 syndrome 對應到一個 1-bit 誤糾正（由兩個 bit 的組合合成一個「假」1-bit 錯誤）。

這個計時側信道本身也可以獨立使用：不需要真正翻位，只需要偵測 ECC 糾正的計時特徵，就能知道對方的記憶體發生了多少次 Rowhammer 干擾——這是一個「Rowhammer 偵測」工具的基礎，但反過來攻擊者也可以用它來驗證自己的 hammer 是否正在生效。

## 本章重點整理

- Rowhammer 的防禦歷史是「防禦假設 → 研究者找到違反假設的場景」的軍備競賽，至今沒有完整解決。
- TRR（DDR4）：追蹤被高頻存取的 row 並 refresh 其鄰居；被 TRRespass（計數器溢出）和 Blacksmith（非均勻 hammer）繞過。
- ECC：糾正 1-bit 錯誤；被 ECCploit（3-bit 同時翻讓 ECC 誤糾正）部分繞過。
- Half-Double：展示 TRR「只保護直接鄰居」的假設不成立，距離 2 的 row 也能被間接打到。
- DDR5 的 PRAC/RFM：per-row 計數，比 TRR 強；RowPress（保持 row open 而非高頻 activate）展示 DDR5 也不是無懈可擊。
- 根本解決需要 DRAM 工藝和電路設計的改進，軟體補丁只能縮小攻擊面。

## 自我檢核

- [ ] TRR 和 PRAC 的根本差異是什麼？哪個更系統化，原因是什麼？
- [ ] ECCploit 如何讓 ECC 的「糾正」操作反而成為攻擊的利用點（計時觀察）？
- [ ] Half-Double 為何讓「只保護 ±1 鄰居」的 TRR 設計失效？在數學上距離 2 的干擾是線性可疊加的嗎？
- [ ] Blacksmith 的非均勻 hammer 為什麼比均勻 hammer 更難被 TRR 追蹤？
- [ ] RowPress 和 Rowhammer 在物理機制上的根本差異是什麼？（activation 次數 vs. row open 持續時間）

## 延伸閱讀

### 論文

- **[TRRespass: Exploiting TRR's Blind Spots for Fun and Profit](https://download.vusec.net/papers/trrespass_sp20.pdf)** — Frigo et al., IEEE S&P 2020
  - **讀哪裡**：Section 3（TRR 的架構分析與弱點）、Section 4（n-sided hammering 方法論）、Section 6（42 DIMM 的測試結果）。
  - **學到什麼**：如何系統性地逆向 TRR 計數器容量；為什麼增加 aggressor 數量能讓 TRR 失效；測試方法論（可以用來測你自己的 DRAM）。
  - **為什麼值得**：這篇終結了「DDR4 TRR = 解決 Rowhammer」的誤解，逼迫 JEDEC 重新設計 DDR5 防禦。

- **[Blacksmith: Scalable Rowhammering in the Frequency Domain](https://comsec.ethz.ch/research/dram/blacksmith/)** — Jattke et al., IEEE S&P 2022
  - **讀哪裡**：Section 4（非均勻 hammer 的理論框架）、Section 5（fuzzing 搜索空間的設計）、Section 6（40 DIMM 的結果）。
  - **學到什麼**：為什麼 TRR 的計數器設計對非均勻樣式不 robust；頻率域分析如何描述 hammer 樣式；Blacksmith 工具的使用。
  - **為什麼值得**：2022 年最重要的 Rowhammer 論文，直接推動了 DDR5 PRAC 的設計。

- **[RowPress: Amplifying Read Disturbance in Modern DRAM Chips](https://rowpress.github.io/)** — Kim et al., ISCA 2023
  - **讀哪裡**：Section 3（RowPress 物理機制）、Section 5（DDR4 和 DDR5 上的實驗）、Section 6（與 Rowhammer 的比較）。
  - **學到什麼**：Row open time（不是 activation 次數）如何成為干擾的第二個維度；RowPress 和 Rowhammer 可以組合使用（降低各自的閾值）；對 DDR5 PRAC 的影響。
  - **為什麼值得**：最新的大突破，展示 DDR5 的 PRAC 防禦被一個完全不同維度的攻擊繞過。

---

Rowhammer 的軍備競賽還沒結束——DDR5 和 RowPress 是目前的最前線，JEDEC 仍在更新規格。這場攻防展示了一個更大的教訓：**當攻擊面是硬體物理特性而非軟體 bug，「修補」的本質是不斷提高門檻而非根本消除**。

下一章轉向另一個微架構通道——不是快取、不是 DRAM，而是**頻率與功耗**：Hertzbleed 如何把 CPU 的動態頻率調整本身變成一個洩漏通道。

→ [Ch 25 頻率/功耗側信道：Hertzbleed](./25-hertzbleed-frequency-power.md)
