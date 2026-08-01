# Ch 1 — 微架構攻擊全景

> **目標**：在動手刻任何攻擊原語之前，先把整個領域的版圖攤開——為什麼 CPU 效能優化和安全隔離之間存在根本矛盾、side channel 與 covert channel 的分野、微架構狀態當通道的基本直覺、這門課六個 Part 怎麼拼成一個完整的攻防故事。這章概念為主，不寫 exploit；你在這裡建立的框架，往後每一章都會往這個框架填細節。

## 一個根本矛盾

現代 CPU 有兩個相互衝突的設計目標。

**目標一：效能最大化。** 硬體工程師把幾十年的工夫花在讓 CPU 跑得更快：快取讓記憶體存取從幾百 cycles 縮到個位數、亂序執行讓 pipeline 不因相依停住、推測執行讓 CPU 在等分支決策時先做下去、TLB 讓每次虛擬位址轉換不用走整條 page table chain。每一個優化機制的本質，都是**根據過去行為預測未來需求，提前準備好資源**。

**目標二：安全隔離。** 同一台機器上跑著不同信任等級的程式——kernel vs user、同主機不同容器、雲端不同租戶的 VM。安全的基本假設是：A 的執行不能洩漏資訊給 B，除非透過明確定義的 API。

矛盾在哪裡？效能優化機制的「共享狀態」打破了隔離。

快取是共享的，L3（LLC）在多核之間共享——你和另一個 process 的資料被放進同一個 sram 結構。分支預測器的歷史表是共享的（或可被你影響的）。TLB 在某些配置下共享。DRAM row buffer 在同一個記憶體 rank 內共享。這些共享的微架構狀態，在安全模型的語境裡叫**側信道（side channel）**：一條不在 API 規格裡、但真實存在的資訊傳遞路徑。

```
        效能優化設計                      安全後果
  ┌─────────────────────┐          ┌────────────────────────┐
  │  快取：避免重複存取   │ ───────► │  快取狀態洩漏存取樣式   │
  │  亂序執行：榨取 IPC  │ ───────► │  推測的副作用殘留      │
  │  分支預測：省 stall  │ ───────► │  預測器狀態可被觀測    │
  │  TLB：省 page walk  │ ───────► │  TLB 衝突洩漏位址      │
  │  DRAM row buffer    │ ───────► │  bank 衝突洩漏存取對象  │
  └─────────────────────┘          └────────────────────────┘
         「快」的代價 = 「可觀測的共享狀態」
```

這個矛盾沒有免費的修法。所有你會在 Part 5 看到的防禦——KPTI、retpoline、IBRS、SerialIze 指令——本質上都是「為了隔離，承受一部分效能損失」。Spectre/Meltdown 之後 CPU 廠商每季都在補，是因為這個矛盾根植於 CPU 的設計哲學，不是一個 bug 修掉就消失的東西。

## Side Channel vs Covert Channel：分野非常重要

這兩個詞常被混用，但攻擊模型截然不同。

**Side channel（側信道）**：受害者（victim）完全不配合，甚至不知道自己在被觀測。攻擊者（attacker）透過觀測受害者執行時留下的微架構副作用，推斷受害者的秘密行為或資料。

```
  受害者（不知情）          共享微架構資源        攻擊者
  ┌──────────────┐         ┌──────────────┐    ┌──────────────┐
  │ 存取 secret  │ ──改變─► │ cache state  │ ◄──│ 計時/觀測    │
  │  key[0]?     │         │ TLB state    │    │ → 推斷 key   │
  └──────────────┘         └──────────────┘    └──────────────┘
        「我完全不知道有人在看我」
```

典型場景：同主機上的兩個 process，一個是 AES 加密服務，一個是攻擊者。攻擊者無法直接讀加密服務的記憶體，但可以觀測它在 cache 留下的查表痕跡。這是 Flush+Reload（Ch 6）、Prime+Probe（Ch 8）的本質。

**Covert channel（隱蔽信道）**：發送方（sender）和接收方（receiver）兩個共謀方，刻意利用微架構資源當傳輸介質，偷偷傳遞資料——繞過不允許他們直接通訊的安全策略。

```
  發送方（共謀，有秘密）     共享微架構資源        接收方（共謀）
  ┌──────────────┐         ┌──────────────┐    ┌──────────────┐
  │ 要傳 bit=1   │ ──操作─► │ cache state  │ ◄──│ 計時/解碼    │
  │ → flush line  │         │（當傳輸通道）  │    │ → 得到 bit   │
  └──────────────┘         └──────────────┘    └──────────────┘
        「我們刻意用 cache 當傳話筒」
```

典型場景：沙箱裡的程式想把資料傳到沙箱外的共謀方；或者在 Spectre 攻擊裡，瞬態（transient）執行的程式用 cache 把偷到的資料傳給正常執行路徑——那個 cache 通道就是 covert channel。

兩者的關係：**瞬態執行攻擊（Spectre/Meltdown）就是 side channel + covert channel 的組合**——side channel 部分是推測執行突破了存取控制，covert channel 部分是用 cache 把突破後讀到的資料傳出來。

## 微架構狀態當通道：五個家族

整門課的攻擊，按照用哪個微架構結構當通道，可以分成五個家族：

### 1. Cache（快取）— Part 2 的主題

最成熟、最通用的通道。無論攻擊者和受害者是否共享 page，只要共享 LLC（L3），都能用 cache 計時做側信道。

原語（primitive）：
- **Flush+Reload**：攻擊者 flush 一條 line、等受害者存取、再 reload 計時——命中就代表受害者存取了。需要共享頁面（shared library/共享記憶體）。
- **Prime+Probe**：攻擊者先填滿一個 cache set、等受害者執行、再 probe 計時——慢表示受害者用了這個 set，把你的 line 踢走了。不需要共享頁面，但解析度略低。
- **Evict+Reload**：用 eviction set 代替 clflush，讓沒有 clflush 的環境（如 ARM、JS）也能做。
- **Flush+Flush**：觀測 clflush 本身的執行時間——line 在快取時 flush 較慢。極低雜訊。

### 2. 推測執行（Speculative/Transient Execution）— Part 3 的主題

CPU 在分支或記憶體保護檢查結果確認之前，推測性地往下執行——「瞬態（transient）指令」。如果推測錯了，CPU 退回（retire 前捨棄架構狀態），但微架構副作用（如 cache 狀態）**不會被退回**。攻擊者透過 cache covert channel 讀出這些殘留副作用。

- **Spectre-v1（Bounds Check Bypass）**：訓練分支預測器繞過陣列邊界檢查，讓 CPU 推測性地讀越界資料並把它編碼進 cache。（軟體緩解：`lfence`、array_index_mask）
- **Spectre-v2（Branch Target Injection）**：毒化間接分支預測器（BTB），讓受害者的間接跳轉推測到攻擊者選擇的 gadget。
- **Meltdown**：利用 CPU 在記憶體保護檢查之前就執行 load 的時間窗，讀 kernel 記憶體（Intel 特有；Comet Lake 已硬體修，本課只讀原理）。
- **MDS（Microarchitectural Data Sampling）**：Line Fill Buffer / Store Buffer / Load Port 的資料在特定情況下可被採樣。

### 3. TLB — Ch 27

TLB 在某些架構下跨行程共享（沒有 ASID/PCIDs 的 x86 歷史行為）。TLB 未命中（page walk）顯著慢於命中——可作計時通道。也可用 TLB 衝突判斷受害者的存取樣式。

### 4. DRAM Row Buffer — Ch 22–24（Rowhammer）

DRAM 內部有 row buffer：最近存取的 row 留在 sense amplifier，同 row 的下次存取極快（hit），不同 row 則要 precharge+activate（慢）。反覆激活同一 row，會讓相鄰 row 的電容電荷洩漏——**bit flip**。Rowhammer 不是計時側信道，是**主動改變記憶體內容**，把 1 翻成 0 或 0 翻成 1，直接導致提權或逃逸。

### 5. Execution Ports / 功耗 — Ch 25–26

- **Port Contention（執行埠競爭）**：SMT（超執行緒）兩條執行緒共享 CPU 的執行埠。攻擊者狂佔某個埠，受害者使用同一個埠的指令就會因競爭而變慢。精細到可以從每條指令的延遲推斷受害者在做什麼操作（用哪個埠）。
- **Hertzbleed（頻率/功耗）**：現代 CPU 的 DVFS（動態電壓頻率調節）讓功耗影響頻率，功耗影響計時——連遠端計時都能洩漏。Ch 25 深入。

## 瞬態執行：一句話預告

「瞬態執行」是整個 Part 3 的核心，先給一個直覺：

CPU 的 pipeline 在提交（commit/retire）結果之前，可能已經推測性地執行了幾十甚至上百條後續指令。如果推測錯了（分支預測失敗、記憶體保護違規），CPU 把**架構狀態**回滾——暫存器、記憶體寫入全部撤銷——但它沒有撤銷**微架構副作用**，特別是已經載入 cache 的資料。

攻擊者利用這個窗口，讓 CPU 「推測性地」執行讀取禁止記憶體的指令，然後用 cache covert channel 把讀到的值偷渡出來。這就是 Spectre 的本質，也是為什麼我們在 Part 2 先把 cache 攻擊原語打穩——沒有那個 covert channel，瞬態執行攻擊就沒有讀出資料的辦法。

## 威脅模型：你在打哪種場景？

微架構攻擊不是一刀切的威脅，攻擊成功與否高度依賴攻擊者和受害者的相對位置：

| 場景 | 攻擊者能力 | 代表攻擊 | 難度 |
|---|---|---|---|
| 同進程，不同權限（kernel vs user） | 能執行用戶態程式 | Meltdown、Spectre-v1 讀 kernel | 2018年最驚人的場景 |
| 同主機，不同進程（Linux user space） | 能跑任意 C 程式 | Flush+Reload、Prime+Probe、Port Contention | 經典研究場景 |
| 跨 VM，同實體主機（雲端） | 只有 VM 內執行能力 | LLC Prime+Probe（共享 LLC）、Rowhammer | 現實威脅模型 |
| 瀏覽器 JavaScript 沙箱 | 只能跑 JS，無 clflush | Spectre-v1 via JS、counting-thread 計時 | 2018年觸達所有用戶的場景 |
| 遠端（網路） | 只有計時 API 呼叫 | Hertzbleed、netleak | 最困難，但真實存在 |

**這門課主要打前兩個場景**（同主機用戶態），Part 3 的瞬態執行深入 kernel/VM 邊界，Ch 25/26 觸及最後兩個。

## 歷史脈絡：這個領域怎麼從學術到主流

這段歷史不只是年表，它解釋了為什麼現在每一個微架構細節都需要從安全角度重新審視。

### 2005–2013：學術地下室

Cache 計時攻擊的概念很早有人提：2005 年 Percival 用 cache 時序攻擊 OpenSSL 的 AES（Hyper-Threading 上）；Bernstein 2005 年在論文裡討論對 AES-128 的 cache timing；Page 2002 年就寫了 cache 共享的攻擊理論。但這些大多停在學術圈——「有趣的研究，但條件很苛刻」。

### 2014：Flush+Reload 改變遊戲規則

**Yarom & Falkner 2014（USENIX Security）**，「FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack」。這篇論文把 cache 計時攻擊從理論推向實用：不再需要複雜的驅動程式或特殊權限，一個普通的用戶態程式、一個 shared library，就能高解析度重建出加密操作的密鑰存取樣式。攻擊成功率夠高，噪音夠低，產業界開始認真看待。整個 Part 2 建在這篇的工具上。

### 2016–2017：推測執行的前夜

Gruss 等人（TU Graz）在 2016 年的多篇論文裡，系統地研究了 rowhammer、prefetch 側信道、KASLR 破解，逐漸觸碰到推測執行的邊界。Jann Horn（Google Project Zero）和 Gruss/Schwarz/Lipp 等人各自在 2017 年獨立發現了利用推測執行讀跨特權邊界記憶體的漏洞，並且謹慎地 coordinate disclosure（業界歷史上規模最大的 CPU 漏洞協調披露之一）。

### 2018 年 1 月 3 日：Spectre/Meltdown 公開，產業地震

原定 2018 年 1 月 9 日披露，因為消息提前洩漏，Google 等廠商在 1 月 3 日提前公開。隔天全球主要媒體頭版，因為這不是某個 app 的漏洞——**這是每一顆現代 Intel/AMD/ARM CPU 的設計缺陷**。

- **Meltdown**（Lipp, Schwarz et al.）：用戶程式讀 kernel 記憶體，繞過所有 page table 保護。在打完補丁（KPTI）之前，任何 Linux x86 都可以在幾秒內 dump kernel 記憶體。
- **Spectre**（Kocher et al.）：更廣的攻擊面，影響 Intel/AMD/ARM，且難以完全緩解（因為推測執行是 CPU 效能的核心）。

修復成本：KPTI（Kernel Page Table Isolation）——kernel 和 user 用不同的 page table，防止 Meltdown——讓 syscall 變貴了 5–30%（workload 相依）。這讓很多 data center 運營商在量化損失，因為你不是在測試環境跑 benchmark，而是真正為了「安全」在生產環境降速。

### 2018–今：持續軍備競賽

- **2018**：KAISER/KPTI 緩解 Meltdown；retpoline 緩解 Spectre-v2；lfence / array_index_mask 緩解 v1。
- **2019**：MDS 家族（Zombieload/RIDL/Fallout）爆出；新的推測執行漏洞（Spectre-v3a, v4, SpectreRSB）；Canella 等人在 USENIX 2019 發表整個瞬態執行攻擊的系統分類學。
- **2020–2022**：SGX enclave 被反覆打穿（LVI、SGAxe）；Rowhammer 在 DDR4 上仍有效（TRRespass）；Hertzbleed（遠端功耗計時洩漏 AES key）。
- **2022–2023**：Retbleed（繞過 retpoline）；Inception（Zenbleed 後 AMD 繼續被打）；Downfall/Gather Data Sampling（Intel Skylake 到 Ice Lake 的 AVX gather 指令洩漏）。
- **今**：每一代新 CPU 設計都要先問「我加的這個推測優化會不會成為下一個 Spectre 變體」，微架構安全已成為 CPU 設計的一等公民考量。

## 這門課的六個 Part：一張地圖

```
Part 1（Ch 0–5）：地基
  ├── Ch 0  環境搭建、校準 hit/miss 門檻
  ├── Ch 1  微架構攻擊全景（本章）
  ├── Ch 2  CPU 微架構最小集（OoO/推測/pipeline）
  ├── Ch 3  Cache 階層與 set-associative 組織
  ├── Ch 4  計時方法學（rdtsc/rdtscp/lfence/雜訊源）
  └── Ch 5  虛擬記憶體與位址轉換對攻擊的意義

Part 2（Ch 6–12）：Cache 側信道原語    ← 動手重點，每個都真跑
  ├── Ch 6–7  Flush+Reload + Covert Channel
  ├── Ch 8–9  Evict+Reload / Prime+Probe / 建 eviction set
  ├── Ch 10   Flush+Flush 等變體
  ├── Ch 11   打 AES/RSA（現實目標）
  └── Ch 12   跨核/跨 VM 的 LLC 攻擊

Part 3（Ch 13–21）：瞬態執行攻擊      ← 概念最重，需 Part 2 打底
  ├── Ch 13   推測執行基礎 / 瞬態指令
  ├── Ch 14–16 Spectre v1/v2、分支預測器
  ├── Ch 17   RSB / ret2spec
  ├── Ch 18–19 Meltdown / MDS / L1TF
  ├── Ch 20   Downfall/Zenbleed/Inception/Retbleed
  └── Ch 21   整個瞬態執行家族的分類學

Part 4（Ch 22–28）：其他微架構通道
  ├── Ch 22–24 Rowhammer（bit flip 提權）
  ├── Ch 25    Hertzbleed（功耗洩漏）
  ├── Ch 26    Port Contention（SMT 側信道）
  └── Ch 27–28 TLB 側信道 / KASLR 破解

Part 5（Ch 29–34）：防禦               ← 每個攻擊都對照它的防禦讀
  ├── Ch 29   防禦全景
  ├── Ch 30–31 隔離 / 推測抑制（KPTI/retpoline/IBRS）
  ├── Ch 32   Constant-time 程式設計（攻擊者的視角看防禦程式設計）
  └── Ch 33–34 偵測（HPC-based）/ 硬體防禦

Part 6（Ch 35–36）：整合
  ├── Ch 35   串起來：end-to-end 真實洩漏鏈
  └── Ch 36   研究方法論：怎麼找新微架構洞
```

**建議讀法**：Part 1 → Part 2（親手刻每個原語）→ Part 3（瞬態執行）→ Part 4 選讀（至少讀 Rowhammer）→ Part 5（對照你學過的每個攻擊找它的防禦）→ Part 6。每個攻擊章讀完，立刻去 Part 5 找對應防禦，而不是等讀完 Part 4 才看防禦。攻防對照才能建立真正的直覺。

## 對比與取捨

| 通道類型 | 需要共享頁面 | 需要 clflush | 跨 VM 有效 | 雜訊 | 代表攻擊 |
|---|---|---|---|---|---|
| Flush+Reload | 是（shared library） | 是 | 否（不同 PA） | 極低 | AES key 洩漏 |
| Prime+Probe | 否 | 否 | **是** | 中 | 跨 VM LLC 偵測 |
| Evict+Reload | 是 | 否 | 否 | 低 | JS 環境 cache 攻擊 |
| 瞬態執行 | 否 | 視 covert channel | 是（Spectre in VM） | 視 covert channel | Spectre-v1/Meltdown |
| Port Contention | 否 | 否 | 否（需 SMT 同核） | 高 | SMT 跨 HT 偵探 |
| Rowhammer | 否 | 否 | **是** | — （主動攻擊） | 頁表 bit flip 提權 |
| Hertzbleed | 否 | 否 | 是（遠端計時） | 高 | AES-NI key 遠端洩漏 |

## 踩雷集錦

1. **「微架構攻擊 = Spectre/Meltdown」**——錯誤直覺：以為這個領域就是 2018 年那兩個漏洞。正確認識：Spectre/Meltdown 是瞬態執行攻擊的代表，但整個微架構攻擊的家族從 2002 年就開始了，快取側信道、Rowhammer、功耗洩漏都是獨立的分支，都持續有新洞出現。

2. **「這台已經打了 KPTI/retpoline 的機器就安全了」**——錯誤直覺：patch 覆蓋等於安全。正確認識：每個緩解只針對特定攻擊；Retbleed 繞過了 retpoline、新的 MDS 變體繼續出現。這是軍備競賽，沒有「打完全部補丁就不用管了」。

3. **「side channel 不是真正的攻擊，只是理論」**——錯誤直覺：因為條件複雜，所以現實威脅低。正確認識：2014 年的 Flush+Reload 就在現實的 cross-VM 場景證明了高保真密鑰洩漏；Rowhammer 已有公開的 root exploit 在 Android 上跑；Hertzbleed 在遠端計時場景就洩漏了 AES key。現實利用條件確實比 buffer overflow 高，但不代表是玩具。

4. **「只有 Intel 受影響」**——錯誤直覺：2018 報導的焦點是 Intel，以為 AMD/ARM 沒事。正確認識：Spectre-v1/v2 影響 AMD/ARM；Zenbleed 是 AMD 的洞；ARM 上的 Flush+Reload 可以在沒有 clflush 的情況下用 DC CIVAC 做；Rowhammer 在 ARM 上更猛（因為 ARM 伺服器記憶體常沒 ECC）。

5. **「快取大了攻擊就沒用了」**——錯誤直覺：LLC 更大表示 eviction 更難，攻擊失效。正確認識：LLC 更大的確讓部分 Prime+Probe 攻擊需要更大的工作集，但 LLC 組數增加也意味著更多可分辨的 set，反而給攻擊者更細的粒度。Flush+Reload 完全不受 LLC 大小影響。

6. **「我只跑防禦工具（sandboxing/seccomp）就夠了」**——錯誤直覺：加沙箱就能擋側信道。正確認識：側信道繞過沙箱——seccomp 擋的是 syscall，不擋 cache 計時；Spectre-v1 在瀏覽器 JS 沙箱裡照樣能洩漏，因為 `Array.prototype.reduce` 不是 syscall。

## 進階：再往深一層

- **漏洞資料庫 transient.fail**：[https://transient.fail/](https://transient.fail/) 維護了一張截至今天所有已知瞬態執行攻擊的分類表，按 CPU 型號、已知緩解、CVE 號整理。每次讀到新洞，第一步就來這裡歸類。
- **機密運算（Confidential Computing）的困境**：Intel SGX、AMD SEV、ARM CCA 的目標是讓 VM/hypervisor 都無法讀到 enclave 內的秘密——但微架構攻擊不需要讀記憶體，它只需要觀測「你在什麼時間點做了什麼操作」。SGX 被 LVI/SGAxe/CacheZoom 反覆打穿，說明在最嚴格的隔離模型下，微架構通道依然是最難根除的威脅。
- **形式化語意的缺口**：傳統程式語言語意（C/C++/x86 ISA 規範）對推測執行下的行為**沒有定義**——ISA 規範說「推測執行的結果不影響架構狀態」，但並沒有說「微架構狀態也不受影響」。Spectre 的根本問題是安全模型建立在一個不完整的規範上。現在有研究組在做「推測語意的形式化模型」（如 Spectector、Binsec/Haunted）。

## 本章重點整理

- 微架構攻擊的根源是**效能優化（共享可觀測狀態）與安全隔離之間的根本矛盾**，沒有免費的解法。
- **Side channel**：受害者不知情，攻擊者觀測微架構副作用推斷秘密。**Covert channel**：共謀雙方刻意用微架構資源傳訊。瞬態執行攻擊是兩者的組合。
- 五個微架構通道家族：cache、推測執行、TLB、DRAM row buffer、執行埠/功耗。
- 2014 年 Flush+Reload 奠定工具基礎；2018 年 Spectre/Meltdown 讓這個領域從學術走向全球資安現實。
- 這門課先打穩 cache 原語（Part 2），再理解瞬態執行（Part 3），再看其他通道（Part 4），最後看防禦（Part 5）——每個攻擊都要對照防禦讀。

## 自我檢核

- [ ] 用一句話解釋「為什麼效能優化和安全隔離之間存在根本矛盾」，不用術語。
- [ ] 分辨 side channel 和 covert channel：一個 AES 伺服器的 cache 側信道攻擊，哪個是受害者？哪個是攻擊者？受害者「配合」了嗎？
- [ ] Spectre-v1 用到了哪兩種通道（side / covert），各在哪個地方？
- [ ] 這台機器（i7-10700 + 最新微碼）能親手重現哪些攻擊？哪些因為硬體修了只能讀原理？（翻 Ch 0 的 sysfs vulnerabilities 輸出）
- [ ] 「打了 KPTI 就修掉所有微架構漏洞」——這句話哪裡錯？

## 延伸閱讀

### 原始論文

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - **讀哪裡**：先讀 Section I（Introduction）和 Section III（Spectre-v1 Bounds Check Bypass），建立整個攻擊的心智模型。
  - **學什麼**：推測執行攻擊的完整形式化描述、威脅模型（哪些場景可利用）、最初的幾個 PoC（C 和 JS 版本）。
  - **和本章的關聯**：你在本章看的瞬態執行直覺，這篇論文給了精確的技術描述。Part 3 讀這篇。

- **[Meltdown: Reading Kernel Memory from User Space](https://meltdownattack.com/meltdown.pdf)** — Lipp et al., USENIX Security 2018
  - **讀哪裡**：Section 3（Meltdown 的三個構件）與 Section 6（Performance evaluation of KPTI）。
  - **學什麼**：Meltdown 的工作原理（exception 延遲 + cache covert channel）、KPTI 的代價有多大。
  - **和本章的關聯**：你理解了「瞬態窗口」之後，這篇給了它在 kernel 讀取場景的完整實現。

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - **讀哪裡**：Section 3（攻擊描述）、Section 4（對 GnuPG 的端到端攻擊）。
  - **學什麼**：現代 cache 側信道攻擊的原始設計；hit/miss 門檻的選取方法（正是 Ch 0 做的事）。
  - **和本章的關聯**：本課 Part 2 的所有 cache 原語都建在這篇的基礎上。

### 綜述 / 分類學

- **[A Systematic Evaluation of Transient Execution Attacks and Defenses](https://arxiv.org/abs/1811.05441)** — Canella et al., USENIX Security 2019
  - **讀哪裡**：先讀 Fig. 1（整個攻擊分類樹）和 Table 1（哪些 CPU 受哪些攻擊影響）。這是 Ch 21 的主要參考。
  - **學什麼**：瞬態執行攻擊的統一分類框架——所有 Spectre-type 和 Meltdown-type 漏洞的歸類方法。
  - **為什麼值得**：讀完本課之後，每當有新洞出現，你能把它放進這個框架定位。

### 部落格 / 入口

- **[transient.fail](https://transient.fail/)** — Canella, Gruss et al. 維護
  - **這是什麼**：所有已知瞬態執行攻擊的活地圖，按 CPU 型號、CVE、緩解狀態持續更新。
  - **讀哪裡**：先看首頁的分類表，再點入你的 CPU 型號看哪些還 vulnerable。
  - **為什麼值得**：本課的「活」版課程大綱，每個新洞都能在這找到歸類。

- **[gruss.cc — Daniel Gruss 的 publications](https://gruss.cc/)** — TU Graz
  - **這是什麼**：微架構攻擊產出最密集的研究組的論文首頁（KASLR bypass、Rowhammer.js、Meltdown、ZombieLoad、Downfall 都有他的名字）。
  - **讀哪裡**：掃 2016–2023 年的論文列表，按你感興趣的主題點進去看 abstract。
  - **為什麼值得**：本課後半部大量 Part 3/4 的原始論文幾乎一半出自這組。

地圖有了。下一章我們鑽進 CPU 微架構的最小集合——攻擊者需要知道的 pipeline/亂序執行/推測執行，不超過你攻擊需要用到的深度，但每一個都要能腦中畫出執行流程。

→ [Ch 2 你必須先懂的 CPU 微架構](./02-cpu-microarchitecture-primer.md)
