# Ch 33 — 偵測（HPC-based）

> **目標**：讀完能說清楚 Hardware Performance Counters（HPC）偵測微架構攻擊的原理、哪些攻擊可偵測、哪些天然躲過，以及為何偵測無法取代緩解措施。

---

## 偵測的根本困境

防禦微架構攻擊時，最讓人沮喪的事實是：**攻擊者與防禦者共用同一顆 CPU**。沒有一個「站在外面」的觀察點，沒有獨立的信任執行環境可以監看所有快取狀態。一切偵測手段，本質上都是在觀察 CPU 行為的副作用，而不是直接讀取攻擊意圖。

這和傳統入侵偵測（IDS）有根本差異。網路 IDS 可以在流量旁路分析封包；主機 IDS 可以攔截系統呼叫。但微架構攻擊不走系統呼叫，不發封包，唯一的「痕跡」是快取狀態改變、分支預測器狀態改變、TLB 狀態改變——而這些都是正常程式也會做的事。

因此，HPC-based 偵測的邏輯是：**統計異常，不是語義識別**。我們不是在說「這段程式碼是 Spectre gadget」，而是在說「這個程式在這段時間內的 LLC miss rate 比正常高了三個標準差，觸發告警」。這個方向的天花板很低，但仍有實用價值，尤其在雲端多租戶環境作為早期預警系統。

---

## HPC（Hardware Performance Counters）回顧

### PMU 架構

每顆現代 CPU 都有一個 Performance Monitoring Unit（PMU），提供若干個可程式化的計數器，稱為 Hardware Performance Counters（HPC）。Intel 平台叫做 Intel PMU，在 Linux 核心中透過 `perf_events` 子系統存取；AMD 同理。每個計數器都能綁定一個「硬體事件」，例如：

| 計數器名稱 | 意義 |
|---|---|
| `cache-misses` | L1/L2/LLC 的 miss（平台相關） |
| `cache-references` | 快取存取總數 |
| `LLC-load-misses` | Last-Level Cache（LLC）讀取 miss |
| `LLC-store-misses` | LLC 寫入 miss |
| `branch-misses` | 分支預測失敗次數 |
| `instructions` | 已退休（retired）的指令數 |
| `cycles` | CPU 週期數 |

若平台暴露 `MEM_TRANS_RETIRED.LOAD_LATENCY` 等更細粒度的事件，還能分析記憶體存取延遲分佈。不同 CPU 微架構支援的事件集不完全相同，需查 `perf list` 確認本機可用。

### 用 perf stat 讀取（需原生 Linux / 裸機環境）

> **注意**：WSL2 下 Hyper-V 不允許 guest 直接訪問 PMU，`perf stat` 無法讀到 HPC 計數器。以下指令需在原生 Linux 或 KVM/VMware（有 vPMU 支援）環境執行。Ch 0 已說明此限制。

```bash
# 未實測（需裸機/原生 Linux）：
# 觀察目標程式（<victim-pid>）在 5 秒內的快取行為
sudo perf stat -e cache-misses,cache-references,LLC-load-misses,branch-misses \
     -p <victim-pid> -- sleep 5

# 期望輸出格式：
#  1,234,567      cache-misses              #    2.34% of all cache refs
#  52,734,891     cache-references
#    456,789      LLC-load-misses           #    0.87% of all LL-cache accesses
#    123,456      branch-misses             #    0.45% of all branches
```

正常桌面應用或伺服器程式的 LLC miss rate 通常在 1–5% 之間；記憶體密集型資料庫（如 Redis）在大量 key 存取時可達 10–15%，但行為穩定，不像攻擊者那樣突然激增。

---

## 偵測策略的兩種思路

在深入各個攻擊的偵測方法之前，先把偵測策略的全局框架建好。HPC-based 偵測可以從兩個角度切入：

**角度 A：監控受害者（victim-centric）**
- 對著受害程序的 PID 跑 `perf stat`，觀察它的 LLC miss rate 是否異常升高（被攻擊者 evict 導致）
- 優點：精確追蹤特定程序；缺點：只能看到攻擊的影響，看不到攻擊者本身

**角度 B：監控整個 CPU core（core-centric）**
- 對整個 CPU core 的所有程序做 HPC 聚合，觀察 core 層級的 LLC miss rate 變化
- 優點：可以同時看攻擊者和受害者的行為；缺點：多個程序混在一起，訊號稀釋

現實中的系統通常要結合兩者：先用 core-centric 偵測異常，再 drill-down 到 process-centric 定位可疑程序。

### 攻擊特徵的 HPC 指紋

每種攻擊在 HPC 上留下的「指紋」是不同的。這張表是後面幾節的路線圖：

```
攻擊類型        攻擊者端 HPC 異常          受害者端 HPC 異常
──────────────  ───────────────────────    ──────────────────────────
Flush+Reload    LLC-load-miss↑↑, clflush↑  LLC-load-miss 稍↑
Prime+Probe     LLC-load-miss 中↑           LLC-load-miss 稍↑
Flush+Flush     clflush↑ (無 load miss!)   幾乎無變化
Spectre covert  取決於傳輸端技術           取決於 gadget 實作
Rowhammer       DRAM row activation↑↑      無快取層異常
```

這個指紋表解釋了為什麼「單靠 LLC-miss rate」的偵測器覆蓋範圍有限：它只能抓住最左邊兩個攻擊，對 F+F 完全盲，對 Rowhammer 也看不到（Rowhammer 的洩漏不走快取）。

---

## 偵測 Flush+Reload（F+R）

### 攻擊特徵

Flush+Reload 攻擊者在每輪量測前都呼叫 `clflush` 把目標快取行驅逐，然後在受害者執行後 reload 並計時。這個流程有兩個可觀察的副作用：

1. **`clflush` 呼叫次數異常**：若平台有暴露 `clflush` 計數器（例如 Intel 的 `MEM_INST_RETIRED.CLFLUSH` 或透過 perf probe），攻擊者密集呼叫 `clflush` 會產生明顯異常。
2. **LLC-load-miss 數量激增**：每次 reload 都是從記憶體讀取（因為 `clflush` 已把快取行踢掉），LLC miss rate 在攻擊期間可輕易飆到 25–50%，遠超正常範圍。

### 監控策略

最簡單的偵測架構是「滾動視窗閾值」：

```
每隔 T 毫秒取樣一次 LLC-load-miss rate
若連續 K 個視窗的 miss rate > 閾值 θ，發出告警
```

典型參數（文獻中的數字，需針對工作負載調整）：
- T = 100ms
- K = 3（要求持續異常，減少誤報）
- θ = 15–20%

這個方法的問題是粒度粗：HPC 通常是對整個 process 或整個 CPU core 計數，無法精確定位到哪條快取行被 flush。偵測到的只是「有異常」，不是「誰在攻擊誰」。

---

## 偵測 Prime+Probe（P+P）

### 攻擊特徵

P+P 不需要共享記憶體，攻擊者用自己的記憶體建立 eviction set 把 LLC 某個 set 填滿（prime），等受害者執行後再 probe 這些記憶體（probe 階段存取慢表示受害者用了該 cache set）。

P+P 的特徵：
- 攻擊者自己的 LLC miss 數量偏高（probe 階段）
- 若受害者在攻擊期間，受害者端的 LLC miss 也會升高（被 evict）

### 為何相對難偵測

P+P 的 access pattern 比 F+R 更接近正常的記憶體密集型程式：攻擊者在「掃描」一組自己的記憶體，這和正常的 sequential scan 沒有統計上的顯著差異。LLC miss rate 確實會升高，但往往只是中等程度的升高，落在誤報率高的灰色地帶。

更麻煩的是，現代 P+P 攻擊（如 Rowhammer-style eviction set construction）本身就有一個「自適應速度」：攻擊者可以刻意減慢 prime 和 probe 的頻率，讓每個取樣視窗內的 miss 數維持在閾值以下，用低速洩漏換取隱蔽性。

---

## 偵測 Spectre / Meltdown

### 直接量測的困難

Spectre-v1 的利用路徑是訓練分支預測器讓受害者 speculatively 執行洩漏 gadget，然後透過快取 covert channel 把資料傳出來。理論上，`branch-misses` 計數器應該能抓到「分支預測器被大量誤訓練」的訊號，但實際上：

1. 攻擊者在自己的程式裡訓練預測器，這些 branch 是「正確預測」（攻擊者刻意讓跳轉結果一致），不會貢獻到 `branch-misses`。
2. 受害者的 branch 是 speculatively 執行，最終被 squash，但 PMU 通常只計數 **retired** 指令，speculative squashed 指令不計入。
3. Meltdown 的核心是 exception 後的 squash，PMU 計數器同樣看不見。

### 可觀察的殘留訊號

Spectre 和 Meltdown 最終都要透過快取 covert channel 把 bit 傳出來（通常是 Flush+Reload 或 Flush+Flush 的某種變體）。這個傳輸端（transmission side）仍然會觸發 LLC miss 異常。所以偵測 Spectre/Meltdown 的實際路徑是：**偵測傳輸端的快取行為**，而不是偵測推測執行本身。這是一個間接且低準確率的方式，誤報率高。

---

## Flush+Flush 的設計目標：躲過 HPC 偵測

這是 F+F 最核心的設計動機。Ch 10 介紹 Flush+Flush 時說它是「隱形攻擊」，原因就在 HPC 層面：

**F+F 完全不做 load 操作**：測量快取行狀態的方式是呼叫 `clflush` 並計時——`clflush` 在快取行 present 時比 absent 時慢（因為需要將 dirty line 寫回）。整個量測流程是：

```
flush → 等受害者存取 → flush（再次）→ 計時第二次 flush 的延遲
```

沒有 load，沒有 LLC-load-miss。偵測器如果只看 `LLC-load-misses`，F+F 攻擊者的數字會很正常。若平台不暴露 `clflush` 計數，攻擊者幾乎完全躲過基於快取 miss 的偵測器。

Gruss et al. 在 DIMVA 2016 的論文（"Flush+Flush: A Fast and Stealthy Cache Attack"）明確把「躲過 HPC 偵測」列為設計目標，並在實驗中驗證了這一點。這是 HPC 偵測方案最重要的邊界——任何只看 cache miss 的偵測器對 F+F 都是盲的。

---

## 機器學習分類器

### 多維特徵的優勢

單一閾值偵測的問題是一維：只用 LLC-miss rate。現實中攻擊者有辦法把單一指標壓低（降速攻擊）。如果同時觀察多個計數器，形成特徵向量：

```
x = [LLC-load-misses-rate, branch-misses-rate, cache-references/sec,
     instructions/cycle (IPC), TLB-misses-rate, ...]
```

然後用機器學習分類器（Random Forest、SVM、LSTM）在「攻擊狀態」vs「正常狀態」上做二元分類，能顯著降低誤報率。

### 研究成果

**CloudRadar**（Zhang et al., RAID 2016）：針對雲端多租戶環境，在 hypervisor 層收集 guest 的 HPC 事件（vPMU），用 Random Forest 分類 Flush+Reload 等攻擊。在受控環境中達到 97%+ 的偵測率，但在多種正常工作負載混合時誤報率上升。

**CacheShield**（研究原型）：結合 perf_events 和 eBPF，在 kernel 層做細粒度的 HPC 取樣，用 SVM 分類，嘗試解決取樣粒度問題。

### 機器學習方案的固有侷限

分類器的效果完全依賴訓練資料的分佈假設。攻擊者若知道偵測器的存在和大致架構，可以做「對抗規避」（adversarial evasion）：刻意調整攻擊的 HPC 特徵，讓特徵向量落入分類器的「正常」區域。這和對抗機器學習（adversarial ML）是同一個問題，目前沒有根本性解法。

---

## perf 在 WSL2 的限制（補充說明）

WSL2 執行在 Hyper-V 之上，Hyper-V 預設不把 PMU 訪問權限下放給 guest VM。執行 `perf stat` 時：

```bash
$ perf stat ls
# 會出現：
# WARNING: perf not compiled with PMU support
# 或計數器全部顯示 <not supported>
```

若需要實際執行本章練習：
- **選項 A**：裸機安裝 Linux（雙開機或專用機器）
- **選項 B**：VMware Workstation Pro / VirtualBox（有部分 vPMU 支援），設定虛擬化層開放 PMU passthrough
- **選項 C**：KVM/QEMU（`-cpu host` 選項可傳遞 PMU 能力給 guest）

在 AWS EC2、GCP Compute Engine 等雲端 VM，perf 的 HPC 存取情況因執行個體類型而異，金屬（bare metal）執行個體（如 `c5.metal`）有完整 PMU 存取。

---

## 對比與取捨

| 偵測方法 | 可偵測的攻擊 | 對 F+F 效果 | 誤報率 | 效能開銷 | 規避難易度 |
|---|---|---|---|---|---|
| LLC miss rate 閾值 | F+R、部分 P+P | 盲（F+F 無 miss） | 中-高 | 極低 | 容易（降速） |
| clflush 計數器 | F+R、F+F | 可偵測 | 中 | 極低 | 需更換傳輸方式 |
| branch-miss rate | Spectre 訓練端 | 無關 | 高 | 極低 | 容易（攻擊者端無誤預測） |
| 多維 HPC + ML | F+R、P+P、部分 Spectre | 仍有盲點 | 中 | 低-中 | 需對抗樣本 |
| eBPF 細粒度 HPC | F+R、P+P | 取決於計數器選擇 | 低-中 | 中 | 需精心構造 |
| Intel PT（處理器追蹤） | 幾乎全部 | 可 | 低 | 高（5–30%） | 較難 |

---

## 踩雷集錦

**踩雷一：「只監控 cache-miss rate 就夠了」**

最常見的錯誤假設。Flush+Flush 存在的意義就是顛覆這個假設。任何只看 LLC-load-misses 的偵測系統，對 F+F 攻擊者是透明的。設計偵測系統時必須考慮攻擊者的規避動機。

**踩雷二：「在 VM 裡裝 perf 來偵測 side-channel」**

WSL2 和大多數公有雲 VM 都拿不到 PMU 存取權限。這不是 perf 的 bug，是虛擬化安全設計的結果。把偵測邏輯放在 hypervisor 層（如 CloudRadar 的做法），才能看到所有 guest 的 HPC 事件，這需要 hypervisor 層的特權和工程支援。

**踩雷三：「機器學習分類器訓練完就永久有效」**

安全領域的 ML 有個獨特問題：對手是主動的，會針對你的防禦做對抗樣本。攻擊者一旦得知偵測器的特徵偏好，可以在訓練集的正常分佈附近調整自己的攻擊 pattern。模型需要定期重訓、結合 threat intelligence，不是一次性產出。

**踩雷四：「高 LLC-miss rate 就一定是攻擊」**

誤報導致防禦疲勞（alert fatigue）。Redis、MySQL、Elasticsearch 等記憶體密集型服務在工作尖峰時 LLC miss rate 本來就高。如果閾值設太低，告警淹沒 SOC；如果調高閾值，真正的攻擊又躲過去。這個兩難是 HPC-based 偵測不可避免的工程挑戰。一個實用的做法是結合「程序白名單」：對已知高 miss 的服務用更高的閾值，對非預期的低特權程序用更嚴格的閾值。

---

## 偵測方案的現實侷限

即使把上述方法全部組合使用，仍有幾個結構性問題無法解決：

**時間粒度問題**

HPC 通常用 PMI（Performance Monitoring Interrupt）驅動：設定溢出閾值，計數器溢出時觸發中斷，kernel handler 記錄事件。這個機制的最小粒度約是幾十萬到幾百萬條指令（否則中斷開銷會拖垮系統）。細粒度的微架構攻擊（例如只偷幾個 bit）可能在一個 PMI 週期內完成，然後「融化」到正常行為的統計平均值裡，被偵測器完全看不到。

**VM / Container 邊界問題**

在多租戶雲端環境中，攻擊者（tenant A）和受害者（tenant B）通常在不同 VM 裡。受害者 VM 的 HPC 偵測到的是受害者自己的 LLC miss 行為，看不到攻擊者的 Prime 或 Flush 動作。Hypervisor 層的偵測理論上可以同時看兩邊，但這要求 hypervisor 有足夠的 HPC channel 同時監控所有 guest，且需要跨 VM 關聯分析，實作複雜。

**規避的非對稱性**

攻擊者可以犧牲頻寬換取隱蔽：降低攻擊速度，讓每個統計視窗的 HPC 數字維持正常。一個偷 AES 金鑰的攻擊，即使速度降到 1/10，仍能在幾秒到幾分鐘內完成，而偵測器卻需要把閾值降到能捕捉低強度攻擊的水準，此時誤報率可能高得無法接受。

**規避的非對稱性**

攻擊者可以犧牲頻寬換取隱蔽：降低攻擊速度，讓每個統計視窗的 HPC 數字維持正常。一個偷 AES 金鑰的攻擊，即使速度降到 1/10，仍能在幾秒到幾分鐘內完成，而偵測器卻需要把閾值降到能捕捉低強度攻擊的水準，此時誤報率可能高得無法接受。

**務實結論**

偵測是輔助層，不是主防線。真正的防線是：
- **隔離**（Ch 30）：讓攻擊者無法與受害者共享 L3 快取
- **推測抑制**（Ch 31）：IBRS/STIBP/Retpoline 等讓 gadget 無法被 speculate
- **Constant-time 實作**（Ch 32）：從根源消除 timing side-channel

HPC 偵測的價值在於：讓 SOC 知道「有人在探測」，觸發事件調查和取證。它是 defense-in-depth 的一層，不是唯一一層。在雲端環境，哪怕偵測率只有 60%，對攻擊者仍有威懾效果（攻擊成本增加）。

---

## 實際可用的偵測工具鏈（需原生 Linux）

下面把幾個最常用的監控指令整理成「偵測 playbook」。全部標注需裸機或 KVM guest，在 WSL2 上均不可用。

### 基礎：perf stat 靜態快照

```bash
# 未實測（需原生 Linux）：
# 對特定 PID 觀察 5 秒
sudo perf stat -e LLC-load-misses,LLC-load-misses:u,cache-misses,cache-references,branch-misses \
     -- sleep 5 -p <pid>

# 對整個系統（所有 CPU）觀察
sudo perf stat -a -e LLC-load-misses,cache-references -- sleep 10
```

`LLC-load-misses:u` 的 `:u` 過濾器只計算使用者態的 miss（排除 kernel），有助於分離 kernel 本身的 cache 行為。

### 動態：perf stat -I（時間序列）

```bash
# 未實測（需原生 Linux）：
# 每 200ms 輸出一次，觀察 LLC miss rate 的時間序列
sudo perf stat -a -I 200 -e LLC-load-misses,cache-references 2>&1 | \
  awk 'NF>0 {
    if (/LLC-load-misses/) miss=$1
    if (/cache-references/) {
      refs=$1
      if (refs>0) printf "LLC miss rate: %.2f%%\n", miss*100/refs
    }
  }'
```

這個 one-liner 把時間序列轉成可讀的 miss rate，便於人工觀察攻擊期間的峰值。

### 進階：perf record + 火焰圖

如果想知道「哪個函式造成最多 LLC miss」（用來 pinpoint 攻擊者的 probe 迴圈）：

```bash
# 未實測（需原生 Linux）：
sudo perf record -e LLC-load-misses:u -p <attacker-pid> -- sleep 10
sudo perf report --stdio | head -30
# 攻擊者的 probe 迴圈在 call chain 中佔比應該非常高
```

### 判斷異常的基準線（baseline）

偵測器的閾值必須針對具體工作負載校準。以下是一些文獻中的參考數字（實際值依 CPU 型號和工作負載差異很大）：

| 工作負載 | LLC-load-miss rate 預期範圍 |
|---|---|
| idle 桌面 | < 1% |
| Web server（nginx/Apache）| 1–5% |
| 記憶體密集型 DB（Redis 大量 GET）| 5–15% |
| F+R 攻擊中的攻擊者程序 | 25–60% |
| P+P 攻擊中的攻擊者程序 | 10–30% |
| F+F 攻擊中的攻擊者程序 | **< 2%（接近 idle）** |

F+F 那行是關鍵：它和 idle 程序幾乎一樣「安靜」，就是為了騙過這張表。

---

## 進階：再往深一層

### eBPF-based 細粒度 HPC 監控

Linux perf 工具的架構是「使用者空間程式 → syscall → kernel perf_events」，每次取樣都有上下文切換開銷。eBPF 的優勢是把分析邏輯下沉到 kernel，在 PMI 觸發時直接在核心態執行 eBPF program，把統計結果寫入 BPF map，使用者空間只讀最終結果。這可以把取樣粒度提高一個量級，同時維持合理的系統開銷。

使用 `bpf_perf_event_open()` 或 libbpf 的 `perf_buffer` 介面可以在 eBPF 程式裡直接掛接 HPC 事件。BCC 工具集的 `perf_sample` 示例是一個起點。

### Intel PT（Processor Trace）

Intel PT 是一個硬體追蹤機制，可以記錄程式的控制流（taken/not-taken branch）。若結合解碼（用 `perf script` 或 `ptdump`），理論上可以看到攻擊者的 `clflush` 呼叫序列和 timing loop，準確率遠高於 HPC 計數器，但開銷也高得多（5–30%），不適合全時開啟，適合在事件響應（incident response）階段針對可疑程序開啟。

### 虛擬化環境下的 vPMU

KVM 透過 `KVM_SET_PMU_EVENT_FILTER` 允許 guest 存取部分 PMU 事件，同時 hypervisor 可以設定 PMU 事件攔截（intercept）把 guest 的 HPC 資訊傳給 host 上的監控程序。這是雲端原生偵測方案的架構基礎：hypervisor 扮演「透明的 HPC 收集器」，把多個 guest 的事件流匯總後做跨租戶相關分析。

---

## 動手練習

**練習一：對比正常與攻擊狀態的 HPC 特徵**（需原生 Linux 或 KVM guest）

在一個終端機跑你的 F+R PoC（可用 Ch 9 的實作），在另一個終端機執行：
```bash
sudo perf stat -e LLC-load-misses,LLC-load-misses:u,cache-misses,cache-references \
     -I 500 -p <your-victim-pid>
```
`-I 500` 表示每 500ms 輸出一次，可以看到 LLC miss rate 的時間序列。記錄攻擊開始前後的數值。預期：攻擊期間 LLC-load-miss rate 明顯上升（依平台可能從 2% 漲到 20%+）。

**練習二：解釋 Flush+Flush 的偵測盲點**

用文字說明：為什麼把上面的 perf 指令對著一個 F+F 攻擊程序執行，LLC-load-misses 數字不會有顯著變化？從 F+F 的操作步驟（flush → 等待 → flush → 計時）推導每個步驟對哪些 HPC 計數器有貢獻，對哪些沒有。

**練習三：設計「自曝」版的 Flush+Flush**

F+F 的隱蔽性來自「不做 load」。思考並回答：如果攻擊者被迫用 load 取代第二次 flush 來測量快取狀態（即退化成 Flush+Reload），則哪些 HPC 計數器會暴露它？如果改為「用 timing 差異但刻意插入一個假 cache-miss-generating load 讓自己看起來正常」，這在統計上能成功欺騙基於 LLC-miss rate 的偵測器嗎？為什麼？

---

## 本章重點整理

- 微架構攻擊偵測的本質是「觀察 CPU 副作用的統計異常」，沒有語義層的識別能力
- PMU/HPC 提供 LLC-load-misses、branch-misses、cache-references 等計數器，可透過 `perf stat` 存取（需原生 Linux）
- Flush+Reload 會造成 LLC-load-miss 大幅激增，是最容易偵測的攻擊
- Prime+Probe 的 miss pattern 接近正常記憶體密集負載，較難偵測
- Spectre/Meltdown 的 speculative 路徑對 PMU 不可見，只能間接偵測傳輸端
- Flush+Flush 刻意避開 load 操作，對 cache-miss 類偵測器天然免疫
- 多維 HPC 特徵 + 機器學習可提升準確率，但攻擊者可做對抗規避
- HPC 偵測有三大結構性問題：時間粒度粗、VM 邊界遮蔽、規避的非對稱優勢
- 偵測是 defense-in-depth 的一環，真正的防線是隔離 + 推測抑制 + constant-time 實作

---

## 自我檢核

1. `perf stat -e LLC-load-misses` 在 WSL2 上執行的結果是什麼？原因是什麼？
2. 一個正常的 web server 在高並行請求下 LLC miss rate 可能是多少？這對閾值設定有什麼影響？
3. 為什麼 Spectre-v1 的攻擊者在自己的 `branch-misses` 計數器上看不到異常？
4. Flush+Flush 攻擊者的 LLC-load-misses 計數為什麼不會顯著升高？從操作流程推導。
5. 什麼是「對抗規避」（adversarial evasion）在 HPC 偵測場景中的具體做法？
6. CloudRadar 把偵測邏輯放在哪一層？為什麼不放在 guest VM 裡？
7. Intel PT 和 HPC 偵測的主要差異是什麼？各自適合什麼場景？
8. eBPF-based HPC 監控比傳統 `perf stat` 的優勢在哪個方向？

---

## 延伸閱讀

- Chiappetta, M., Savas, E., & Yilmaz, C. (2016). *Real time detection of cache-based side-channel attacks using Hardware Performance Counters*. Applied Soft Computing. — HPC 偵測的早期系統性研究，確立了滾動視窗統計方法。

- Gruss, D., Maurice, C., & Mangard, S. (2016). *Flush+Flush: A Fast and Stealthy Cache Attack*. DIMVA. — 設計目標明確是躲避 HPC 偵測，閱讀 Section 4 的「Stealthiness」分析。

- Zhang, X., et al. (2016). *CloudRadar: A Real-Time Side-Channel Attack Detection System in Clouds*. RAID. — 雲端多租戶場景，hypervisor 層的 HPC 收集架構與 Random Forest 分類器設計。

- Payer, M. (2016). *HexPADS: A Platform to Detect "Stealth" Attacks*. ESSoS. — 更廣義的平台行為異常偵測，含 HPC 整合方案，討論誤報率控制。

---

→ [下一章：微碼與硬體防禦的未來](34-microcode-hardware-future.md)
