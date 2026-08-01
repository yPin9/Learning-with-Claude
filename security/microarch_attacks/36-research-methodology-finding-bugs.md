# Ch 36 — 研究方法論：如何找到新的微架構漏洞

> **目標**：從「能複現 Spectre」跨越到「能發現下一個 Spectre」。掌握系統化的微架構漏洞挖掘方法論——差分測試、微架構 fuzzing、手冊分析、微碼 diff——並建立一套拿到新 CPU 就能上手的七步流程。

---

## 36.1 從學已知到找未知：那條鴻溝

課程走到這裡，你已經能端到端複現 Spectre-v1（Ch 35），理解 MDS/Downfall/Zenbleed 的分類學位置（Ch 21），也讀過完整的防禦全景（Ch 29–34）。這些是已知的。

但漏洞不是等人去讀的。Spectre 在 2018 年 1 月 3 日的那個早上，Google Project Zero 發佈報告之前，它只存在於少數人的腦子裡。有人先想到了，有人先測了，有人先寫 PoC，有人先去 PSIRT 通報。

**那條鴻溝不是知識量的問題，是方法論的問題。**

讀論文是接收已有的答案。找漏洞是在沒有答案的空間裡提問。這兩件事需要不同的肌肉。這一章練的是後者。

大多數微架構漏洞的發現路徑可以歸納成四類：

| 路徑 | 核心動作 | 代表工具 |
|------|----------|----------|
| 差分測試 / 黑箱觀察 | 跑微測試，比較 CPU 實際行為與 ISA 規格 | Revizor |
| 微架構 Fuzzing | 生成隨機指令序列，偵測 side-channel 洩漏 | Osiris, Medusa, Transynther |
| 手冊分析 + 推理 | 找「存取控制點」，問推測路徑在 check 前讀了什麼 | 人腦 + 測量 harness |
| 微碼更新 diff | 從廠商 patch 逆推被修補的行為 | iucode-tool + bindiff |

這四條路徑不互斥，實務上往往組合使用。

---

## 36.2 三條發現路徑的大圖

### 路徑一：差分測試（Differential Testing）

基本想法非常直接：**兩段邏輯等價的指令序列，如果執行後 cache 狀態不同，就找到了洩漏。**

「邏輯等價」的意思是：從 ISA 的角度，兩段程式讀/寫的架構狀態（architectural state）完全一樣。但微架構實作上，推測執行路徑可能差異很大。

Revizor（Intel 2021，開源於 GitHub）把這個想法形式化：

- **Hardware Contract**：ISA 說這段程式「應該」怎麼行為
- **Hardware**：CPU 實際怎麼行為
- **差異** = 潛在的 contract violation = 漏洞候選

Revizor 的工作流程：

```
1. spec：用 ISA spec 生成測試案例（一組指令序列 + 輸入）
2. observe：在真實 CPU 跑，量測 cache 狀態（PRIME+PROBE 或類似）
3. model：用 formal model 模擬「ISA-compliant」的 cache 狀態
4. diff：比較 observe vs model
5. report：如果有差異，輸出 counterexample
6. minimize：把 counterexample 縮到最小觸發條件
7. PoC：手刻 exploit 驗證是否真的可利用
```

這個方法的強項是**系統性**——它不依賴研究者的直覺，而是機械式地搜索 ISA 合約與硬體行為的差距。Revizor 在論文中報告了多個之前未被記錄的 contract violation，其中部分機制與 LVI（Load Value Injection）有交集。

對我們的建議：先把 Ch 6/14/35 的 PoC 複現，再用差分工具驗證你的理解是否有盲點。如果你認為「這段程式不應該洩漏」但工具說有，你理解的 ISA 合約和 CPU 的行為之間有條縫。

### 路徑二：微架構 Fuzzing

差分測試還是需要一個「基準模型」。Fuzzing 更野——它直接問：**有沒有哪個指令組合讓 cache 狀態變得可觀測？**

**Osiris**（Weber et al., USENIX Security 2021）的架構：

每個測試案例由三段組成：

```
(init, trigger, measure)
```

- `init`：初始化 CPU 微架構狀態（清快取、設定暫存器...）
- `trigger`：一條或多條被測指令
- `measure`：FLUSH+RELOAD 測量特定記憶體是否被快取

Osiris 自動組合這三段，搜索哪個 (init, trigger) 組合讓 `measure` 偵測到異常的 cache hit。它在論文中找到了超過 1200 個 microarchitectural channel，包括 Hertzbleed 的前驅（頻率側信道）的早期信號。

**Medusa** 則針對更窄的問題：memory disambiguation（記憶體消歧義）這個微架構特性。Store-to-load forwarding 在某些條件下會 bypass 正常的存取順序，Medusa 自動找這類 side channel。

**Transynther** 生成 Spectre-style gadget，測試哪些指令模式在推測路徑能被利用——它不只找洩漏，還篩選「可利用的洩漏」。

三個工具的共同邏輯：

```
cache 測量是唯一 oracle
fuzzer 是指令空間的搜索策略
信號 = cache timing anomaly
過濾 = 排除 prefetcher 雜訊、OS 干擾
```

實務注意：Osiris 在 GitHub 公開可跑，但需要 **native Linux**。WSL2 的 perf 事件和 cache 隔離不夠乾淨，會讓 false positive 爆炸。需要 `isolcpus=` kernel 參數把測試 CPU 從 scheduler 移除。

### 路徑三：手冊分析 + 推理

這是最古老、也仍然有效的方法。Spectre 的發現者 Paul Kocher 在 2018 年的採訪裡說過，他的起點就是讀 Intel 手冊，對「推測執行」這個描述感到不安。

**核心直覺**：

> 手冊說「檢查 X 後才執行 Y」——CPU 真的在推測執行時先做 Y 嗎？

系統化問法：

1. 找**存取控制點**（permission check、bounds check、type tag check、branch condition）
2. 問：這個 check 的結果在 cache miss 時幾 cycles 才回來？
3. 在那段延遲窗口內，CPU 推測路徑會讀什麼資料？
4. 那個推測讀的資料通過哪條微架構 channel 可觀測？

把已知漏洞套進去驗證這個問法的威力：

| 漏洞 | 存取控制點 | 延遲來源 | 推測路徑讀的資料 | 可觀測 channel |
|------|-----------|----------|----------------|---------------|
| Spectre-v1 | bounds check（`array1_size` 在 DRAM） | DRAM 訪問 ~200 cycles | `array2[array1[x] * 512]` | FLUSH+RELOAD |
| Meltdown | page permission bit（US=0） | TLB miss + page walk | kernel 記憶體任意位址 | FLUSH+RELOAD |
| Downfall | gather 指令 mask check | AVX gather 內部微操作 | 跨 SIMD lane 的暫存器資料 | FLUSH+RELOAD |
| Spectre-v2 | indirect branch 目標 | BTB 查詢 | 任意 gadget 讀的記憶體 | FLUSH+RELOAD |

---

## 36.3 微碼更新 Diff：從 Patch 逆推漏洞

Intel 和 AMD 定期發布微碼更新（microcode update），修補微架構行為。這些 patch 本身就是情報——**廠商修了什麼，就代表那裡曾經有問題。**

### 工具鏈

```bash
# 查看系統當前微碼版本
$ grep microcode /proc/cpuinfo | head -1

# 掃描系統上的微碼更新檔
$ iucode-tool --scan-system

# 列出已載入的微碼 signature
$ iucode-tool --list-all /lib/firmware/intel-ucode/
```

微碼更新檔（`.bin` 格式）可從 Intel 官網或 linux-firmware 倉庫取得。對比 before/after 的二進位 diff：

```bash
$ radiff2 microcode-old.bin microcode-new.bin
# 或
$ bindiff  # 如果有 IDA Pro 環境
```

新加入的微碼通常是：

- **Flush 操作**：強制清空某個微架構緩衝區
- **序列化 barrier**：在某個操作前後插入 microcode-level fence
- **條件跳轉變成序列化流**：把原本推測執行的路徑改為等待

### 案例：Downfall (CVE-2022-40982) 的微碼 diff

2023 年 8 月，Intel 發布 Downfall 的公告，同時 release 微碼 patch。在公告發布之前幾個月，就有研究者注意到 Intel 悄悄更新了 Alder Lake / Tiger Lake 的微碼，改動集中在 `VGATHER*` 系列指令的處理路徑。

diff 顯示新增了一個 microcode-level serialization barrier，在 GATHER 指令的第一條 speculative load 之前插入。這個改動的語義非常清楚：原本的實作在 gather 指令內部做 speculative load 時，沒有正確隔離不同 SIMD lane 的資料，導致 cross-lane 資料洩漏。

對研究者來說，看到這個 diff，就可以合理推斷：**有人在 VGATHER 的推測路徑找到了跨 lane 的 side channel。** 剩下的就是去驗證。

---

## 36.4 新指令分析：以 Downfall / AVX GATHER 為例

每次 Intel 或 AMD 發布新指令集延伸（AVX-512、AMX、未來的 AI 加速指令），就開了一個新的攻擊面。新指令 = 新微操作序列 = 新的推測執行路徑 = 新的洩漏候選。

**Downfall 分析流程**：

1. `VGATHERDPS`（AVX gather 指令）需要讀多個記憶體位址
2. 每個位址對應到不同的 cache line，需要多次 load
3. 內部用 speculative load 批次讀取，提升 throughput
4. Mask 寄存器控制哪些 lane 參與——但 mask check 在推測路徑的處理有問題
5. 結果：未 mask 的 lane 的資料被暫時放進了可觀測的微架構狀態

**新指令分析 checklist**：

```
□ 這條指令讀了哪些記憶體位址？一次幾條 cache line？
□ 有沒有條件式 mask？mask check 的結果幾 cycles 回來？
□ 在 mask check 結果回來之前，CPU 對未確認 lane 做了什麼 speculative load？
□ 這些 speculative load 的資料經過哪條微架構 channel 可觀測？
□ 跨 lane、跨 privilege level、跨 hyper-thread 的隔離是否完整？
□ 微碼實作有沒有插入足夠的 serialization barrier？
```

AMX（Advanced Matrix Extensions）和未來的 AI tile 指令是下一個值得關注的面——它們操作大塊記憶體（tile 最大 1KB），內部必然有複雜的 speculative load 序列。

---

## 36.5 拿到新 CPU 的七步流程

把前面的方法論整合成可操作的步驟。

### Step 1：建立 baseline

```bash
# 確認 FLUSH+RELOAD 在這台 CPU 上的 HIT/MISS 分布
# 用 Ch 6 的 calibrate harness
$ ./calibrate
HIT  threshold: ~35 cycles
MISS threshold: ~180 cycles
Gap: 145 cycles — 可工作
```

沒有乾淨的 baseline，後面的信號全是雜訊。在不同的 CPU 型號、不同的負載下，threshold 可能差很多。

### Step 2：讀廠商漏洞資訊

```bash
# 這台 CPU 已知有哪些漏洞？哪些已修？
$ cat /sys/devices/system/cpu/vulnerabilities/*
```

同時讀：

- [transient.fail](https://transient.fail/)：完整的瞬態執行漏洞地圖
- Intel Platform Update 頁面：最新 Security Advisory
- AMD Product Security 頁面

**不要在已修的漏洞上浪費時間。** 如果 sysfs 說 `Not affected`，這台 CPU 硬體已打了 mitigation，PoC 不會有信號。這不是你的 PoC 壞了，是漏洞已修。

### Step 3：複現已知攻擊

在這台機器上跑 Ch 6 的 FLUSH+RELOAD PoC、Ch 14 的 Spectre-v1 PoC、Ch 35 的 end-to-end leak。

這一步是**驗證 harness**，不是在學已知攻擊。目的是確認：在這台 CPU 上，你的測量基礎設施工作正常。如果 Ch 35 的 40 bytes 跑不到 90% 準確率，先修 harness，再做研究。

### Step 4：差分掃描

用 Revizor 或自己手刻的差分測試（見 36.6 節的範例），找在這台 CPU 上什麼指令組合有觀測到 cache state 洩漏。

偽碼邏輯：

```python
# 偽碼示意（未實測，理論預期）
# seq_A：有 lfence 序列化的讀
# seq_B：無序列化的讀
# 在某些條件下，seq_B 的 cache 結果若和 seq_A 不同 → 找到 microarchitectural difference

def test_pair(seq_A, seq_B, measure_target):
    flush(measure_target)
    run(seq_A)
    state_A = probe(measure_target)   # HIT or MISS

    flush(measure_target)
    run(seq_B)
    state_B = probe(measure_target)   # HIT or MISS

    if state_A != state_B:
        return "CONTRACT VIOLATION CANDIDATE"
    return "OK"
```

這個偽碼沒有處理 noise（需要多次重複取 median），但核心邏輯就是這樣。

### Step 5：手冊掃描

重點目標：Intel 64 and IA-32 Architectures Software Developer's Manual（SDM）中：

- Volume 3, Chapter 8: Memory Ordering
- Volume 3, Chapter 11: Memory Cache Control（特別是 prefetch 相關）
- 各指令的 Operation 欄位——找「If ...」條件分支

**尋找 fence/barrier 的地方**：Intel 在已知危險的操作前後加了 fence，那些 fence 是在說「這裡不加的話有問題」。把所有 LFENCE/MFENCE/SFENCE 的使用場景列出來，問自己：如果少了這個 fence，會洩漏什麼？

### Step 6：新指令/擴展集分析

每個新指令按 36.4 的 checklist 走一遍。重點關注：

- 操作 SIMD 寬暫存器的指令（cross-lane 隔離問題）
- Gather / Scatter 類型指令（多個記憶體位址的 speculative load）
- Prefetch 相關指令（直接操作微架構狀態）
- 含 mask 操作的條件式指令（mask check 在推測路徑的時序）

### Step 7：最小化 PoC + 撰寫報告

找到信號後，縮小觸發條件：

1. 去掉不必要的指令，確認哪條是核心觸發指令
2. 測試觸發條件的邊界（需要 cache miss？特定 CPU 狀態？跨特定邊界？）
3. 測試是否在多個同系列 CPU 上重現
4. 寫出機制解釋（不只是「有信號」，要說明「為什麼有信號」）

CVE 描述的基本要素：
- 受影響的 CPU 型號和微碼版本
- 觸發條件（需要什麼特權？什麼 CPU 狀態？）
- 攻擊者能洩漏什麼資料（什麼粒度、什麼範圍）
- 建議的 mitigation

---

## 36.6 差分測試概念範例

用一個具體的想法說明差分測試的設計思路，這不是已驗證的 PoC，而是展示「怎麼問問題」的結構。

**假設**：不帶 `lfence` 的記憶體讀取（seq_B）和帶 `lfence` 的讀取（seq_A），在 branch misprediction 的場景下，是否對 cache 產生不同影響？

```nasm
; seq_A — 序列化讀（ISA 合約：lfence 後才讀記憶體）
mov rax, [rcx]
lfence
mov rbx, [rdx + rax * 512]   ; 依賴 rax 的讀，lfence 確保 rax 已定

; seq_B — 不序列化讀（推測執行可能提前讀 [rdx + rax * 512]）
mov rax, [rcx]
mov rbx, [rdx + rax * 512]   ; 可能在 rax 確定前推測讀
```

測量：在 seq_B 之後，PROBE `rdx + secret * 512` 是否出現 cache hit？

如果 seq_A 和 seq_B 在特定條件下（例如 `rcx` 指向 DRAM 慢速記憶體，branch predictor 被訓練往特定方向）產生不同的 cache 結果，就找到了 contract violation 候選。

這個結構就是 Spectre-v1 的核心，只是 Spectre-v1 多了一層 bounds check 的推測繞過。差分測試把這個思考過程自動化：機器幫你找哪對 (seq_A, seq_B) 的 cache 結果有差異。

---

## 36.7 進階方向

### 學術界

微架構攻擊的頂級發表場所：

- **USENIX Security**：Osiris、Medusa、Hertzbleed、ZenBleed 都在這裡
- **IEEE S&P**（Oakland）：Spectre/Meltdown 首發、MDS 相關
- **CCS**：多篇 Rowhammer 和 cache 攻擊
- **NDSS**：分類學和防禦面較多

建議的閱讀策略：先把 [transient.fail](https://transient.fail/) 的每個漏洞找到原始論文，按時間線讀，看每篇論文怎麼構建在前人工作上。這條時間線從 2018 年的 Spectre/Meltdown 到 2023 年的 Downfall/Inception，是一個完整的演化史。

### 工業界 Bug Bounty

- **Intel Bug Bounty**：psirt@intel.com，最高賞金 $100,000（針對 speculative execution 漏洞）
- **AMD BugBounty**：security@amd.com，賞金依嚴重度
- **ARM Security**：arm-security@arm.com

通報前需要準備：PoC 代碼、受影響型號列表、攻擊場景說明。Intel 的 PSIRT 通常 7 個工作天內回應。負責任披露（responsible disclosure）的協調期通常是 90 天，期間廠商準備 patch，研究者不公開細節。

### 下一個攻擊面

幾個值得長期關注的方向：

**AI 加速器的 cache side channel**：GPU 的 L1/L2 cache 沒有和 CPU 一樣嚴格的 privilege 隔離，而 CUDA 的 warp 推測執行機制目前研究較少。TPU 的矩陣乘法單元有類似 gather 的批次記憶體存取模式。

**Confidential Computing 的微架構洞**：Intel TDX 和 AMD SEV-SNP 提供 VM-level isolation，但微架構 channel 不在它們的威脅模型之內。TDX guest 和 TDX host 之間是否存在 cache timing channel 是開放的研究問題。

**跨核心的微架構 channel**：大多數已知攻擊在同一個 SMT thread pair 上效果最好。跨 physical core 的攻擊（透過 LLC contention）是更難但更廣泛的威脅面——攻擊者不需要在同一個 core 上。

---

## 踩雷集錦

### 陷阱一：把「cache hit」當成「找到 bug」

最常見的錯誤。看到 timing anomaly 就興奮，其實可能是：

- **Prefetcher 雜訊**：CPU 的 hardware prefetcher 看到 access pattern 就預先讀進 cache，你量到的 hit 是 prefetcher 的功勞，不是洩漏
- **OS 或另一個進程的合法 cache 共享**：共享 library（如 libc）的 code section 本來就在多個進程間共享
- **False positive 統計**：FLUSH+RELOAD 本來就有 noise，偶發的 hit 不等於信號

真正的 bug 需要：信號強（hit rate 明顯高於 baseline noise）、可重複（跑 1000 次有 >90% 一致）、機制可解釋（能說出「哪條推測執行路徑讀了這個記憶體位址」）。

### 陷阱二：在已修的漏洞上猛力 debug

現代 CPU（Ice Lake 之後）對 Meltdown 是硬體免疫，MDS 也多半已修。如果你在 2024 年的機器上跑 Meltdown PoC 跑不出來，先查 sysfs，不是先懷疑你的 PoC 壞了：

```bash
$ cat /sys/devices/system/cpu/vulnerabilities/meltdown
Not affected
```

`Not affected` 意思是硬體層面已修，任何 software PoC 都不會有信號。這不是錯誤，是正確結果。

### 陷阱三：在 WSL2 或 VM 裡跑 fuzzing tool

WSL2 的問題：

- `perf` 事件在 WSL2 核心裡有限制，部分 PMU counter 不可用
- Hypervisor（Hyper-V）會干擾 cache timing 的精度
- 沒有辦法設定 `isolcpus=` 讓測試 CPU 脫離 scheduler

結果：大量 false positive，信號雜訊比差到無法判斷。需要 native Linux + `isolcpus=2,3`（kernel 參數）+ `taskset` 把 fuzzer 鎖在特定 core。如果只有 Windows 機器，考慮找一台裸機 Linux 或用實驗室環境。

### 陷阱四：找到奇怪的 timing anomaly 就直接去發 CVE

正確流程是：anomaly → 假設機制 → 驗證假設（修改實驗排除替代解釋）→ 最小化觸發條件 → 確認在多台機器重現 → 撰寫機制說明 → 通報廠商 → 等協調披露期（通常 90 天）→ 公開

跳過中間步驟直接發 CVE 的後果：如果是 false positive，損失可信度；如果機制說明不完整，廠商很難做 patch；如果沒等協調披露期，可能被視為 responsible disclosure 違規。

---

## 動手練習

### 練習一：漏洞分類學複習

進入 [transient.fail](https://transient.fail/) 網站，找到 Downfall、Zenbleed、Inception 三個漏洞的詳細描述。

把每個漏洞填入 Ch 21 的 Canella et al. 分類學框架：
- **Trigger**：什麼指令或操作觸發推測執行？
- **Data source**：洩漏的資料來自哪個微架構緩衝區或結構？
- **Channel**：用什麼側信道把資料送出？（快取？執行埠？功耗？）
- **Attacker / Victim 關係**：需要同一個 SMT thread？同一個 process？

完成後，思考：這三個漏洞分屬分類學的哪三個不同格子？他們的交集（都有的特性）和差異是什麼？

### 練習二：微碼版本稽核

在你的 Linux 環境（native 或 VM）上執行：

```bash
# 查看 CPU 基本資訊和微碼版本
$ grep -m1 "model name" /proc/cpuinfo
$ grep -m1 "microcode" /proc/cpuinfo

# 掃描系統上已安裝的微碼更新
$ sudo iucode-tool --scan-system --list

# 查看已知漏洞緩解狀態
$ for v in /sys/devices/system/cpu/vulnerabilities/*; do
    echo "$(basename $v): $(cat $v)"
  done
```

然後到 Intel 的 Processor Microcode Update Guidance（在 Intel Security Center 網站）或 `linux-firmware` 的 `intel-ucode` 目錄，找你的 CPU CPUID 對應的最新微碼版本。

比較當前版本和最新版本的差距，並查看中間更新的 Release Notes 修了什麼安全問題。

### 練習三：設計差分測試

選一個你懷疑有洩漏可能性的指令對（不需要真的跑出信號，這是設計練習）。

格式：

**假設**：我懷疑 `[指令 A]` 在推測路徑中，比 `[指令 B]`（兩者邏輯等價但序列化程度不同）多讀了什麼微架構狀態。

**序列 A**（有序列化保護）：
```nasm
; 描述你的 A 序列
```

**序列 B**（無序列化保護）：
```nasm
; 描述你的 B 序列
```

**測量方式**：FLUSH+RELOAD 哪個記憶體位址？flush 在哪個時間點？probe 在哪個時間點？

**預期信號**：如果假設正確，序列 B 的 probe 應該比序列 A 快幾 cycles？

**排除替代解釋**：怎麼排除 prefetcher 干擾？（例如：在測試前 disable 相關 prefetcher，或者改變 access pattern 讓 prefetcher 無法預測）

---

## 本章重點整理

- 微架構漏洞發現有四條系統化路徑：差分測試、微架構 fuzzing、手冊分析推理、微碼 diff 逆推
- **Revizor** 把「ISA 合約 vs 硬體行為」的差異形式化，機械式搜索 contract violation
- **Osiris / Medusa / Transynther** 在指令空間裡 fuzz，cache timing 是唯一 oracle
- **手冊分析**的核心問法：找存取控制點 → 問 cache miss 延遲窗口 → 問推測路徑讀了什麼
- **微碼 diff**：廠商修了什麼地方，那裡曾經有問題；iucode-tool + radiff2 是基本工具鏈
- 新指令 = 新攻擊面；SIMD gather、masked 操作、批次記憶體存取是重點
- 拿到新 CPU 的七步流程：baseline → 讀 advisory → 複現已知 → 差分掃描 → 手冊掃描 → 新指令分析 → 最小化 PoC
- 不能把 cache hit 直接等同 bug：需要強信號、可重複、機制可解釋
- 在 WSL2 跑 fuzzing 工具結果不可靠；需要 native Linux + isolcpus

---

## 自我檢核

1. Revizor 的「Hardware Contract」和「Hardware」分別指什麼？兩者的差異在形式上代表什麼？

2. Osiris 的 (init, trigger, measure) 三段架構，每一段各自扮演什麼角色？為什麼需要 init 段而不只是 (trigger, measure)？

3. 在手冊分析路徑中，「存取控制點的 cache miss 延遲」為什麼是關鍵？以 Spectre-v1 的 bounds check 為例說明。

4. 微碼更新 diff 顯示某個指令新增了 microcode-level barrier。這在漏洞研究上的意義是什麼？後續的驗證步驟是什麼？

5. 為什麼要在七步流程的 Step 3 先複現已知攻擊，而不是直接開始差分掃描？

6. 找到 timing anomaly 之後，怎麼判斷這是真正的 microarchitectural leak，而不是 prefetcher 雜訊或 OS 干擾？列出至少三個判斷標準。

---

## 延伸閱讀

**核心論文**

- Niemetz et al., "Revizor: Testing Black-Box CPUs against Speculation Contracts," ASPLOS 2021 — 差分測試方法論的奠基論文
- Weber et al., "Osiris: Automated Discovery of Microarchitectural Side Channels," USENIX Security 2021 — 三段式 fuzzing 架構，找到 1200+ channel
- Canella et al., "A Systematic Evaluation of Transient Execution Attacks and Defenses," USENIX Security 2019 — Ch 21 的分類學來源，同時也是方法論的系統化框架
- Moghimi, "Downfall: Exploiting Speculative Data Gathering," USENIX Security 2023 — VGATHER 分析的完整版，AVX gather 攻擊面的標準參考

**工具和資源**

- [transient.fail](https://transient.fail/) — 瞬態執行漏洞的互動式分類地圖，持續更新
- Revizor GitHub: `hw-model/revizor` — 可直接 clone 跑差分掃描
- Osiris GitHub: `HexHive/osiris` — fuzzing tool，需要 native Linux
- Intel Platform Innovation Blog / IPAS Security Blog — 微碼更新分析和 advisory 的第一手來源；`iucode-tool` man page 有 microcode blob 格式說明

**進一步探索**

- Hertzbleed (2022)：頻率側信道，展示 power side channel 可以轉換成 timing side channel，打破了「只有 cache 才是 channel」的假設
- ÆPIC Leak (2022)：不需推測執行，直接從 APIC MMIO 讀到 stale 資料，展示微架構洩漏不限於推測執行
- CrossTalk / SRBDS (2020)：跨 physical core 的微架構洩漏，攻擊面從 SMT 擴展到所有 core

---

至此，本課程從 CPU 基礎（Part 1）、側信道原語（Part 2）、瞬態執行攻擊（Part 3）、現代變種（Part 4）、防禦全景（Part 5），到這一章的研究方法論，構成了一條完整的路徑。

能複現攻擊只是起點，知道如何找未知才是終點。

這門課教了你工具，方法論這一章教了你怎麼用工具去問正確的問題。Final Project 是第一次實際演練：在真實硬體上，用本章的方法，確認或否定一個具體的洩漏假設。

→ [Final Project — microarch-leak-lab](final-project-microarch-leak-lab.md)
