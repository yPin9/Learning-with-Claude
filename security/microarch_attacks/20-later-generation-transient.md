# Ch 20 — 後續世代瞬態執行攻擊（2019 之後）

> **目標**：理解 2018 年「第一波」(Meltdown/Spectre/MDS) 之後的攻防演進；掌握 MMIO Stale Data、Retbleed、Downfall、Zenbleed、Inception 的核心原理與防禦成本；從本機 sysfs 的實際輸出讀懂自己的機器真正暴露在哪裡。

---

## 前言：軍備競賽不會停

2018 年 1 月 Meltdown 與 Spectre 公開之後，Intel 與 AMD 的第一反應是「打補丁，完工」。現實是：每次修補只是堵住了「已知攻擊面」。微架構的暗角仍在，下一批研究者會從不同角度鑽進去。

這不是供應商不認真。是因為現代 CPU 的複雜度本質上讓「全面審計」接近不可能。每個研究突破都揭露一個「上一次修補沒想到」的路徑。本章我們就按時間軸走一遍這場軍備競賽。

---

## 20.1 全景時間軸

```
2018 Jan  Meltdown (CVE-2017-5754) + Spectre v1/v2 (CVE-2017-5753/5715)
          洩漏來源：L1D cache（跨權限頁表讀取）+ BTB/PHT 分支預測器
          修補：KPTI（Meltdown）+ retpoline（Spectre-v2）+ bounds check（Spectre-v1）

2018 Aug  Foreshadow / L1TF (CVE-2018-3615/3620/3646)
          洩漏來源：SGX enclave 的 L1 cache 行，利用 P bit=0 頁表項
          修補：VM entry 時清空 L1D、SGX 禁用超執行緒

2019 May  MDS：ZombieLoad、RIDL、Fallout
          (CVE-2018-12126/12127/12130/11091)
          洩漏來源：LFB（Line Fill Buffer）、store buffer、L1D port
          修補：VERW + CPU buffer flush + MDS_NO microcode（第 8 代起）

2019 Nov  TAA（TSX Async Abort, CVE-2019-11135）
          洩漏來源：Intel TSX transaction abort 觸發 VERW 未覆蓋路徑
          修補：關閉 TSX（RTM 禁用）或更新 microcode

2022 Jul  Retbleed (CVE-2022-29900/29901)
          洩漏來源：ret 指令的 BTB fallback（retpoline 假設 ret 安全）
          修補：eIBRS（Intel）/ RSB stuffing + IBPB（AMD & older Intel）

2022 Jun  MMIO Stale Data (CVE-2022-21123/21125/21127/21166)
          洩漏來源：MMIO 路徑上的 CPU 內部緩衝（VERW 未覆蓋此路徑）
          修補：需要 microcode 協作；本機 i7-10700：尚未取得 microcode！

2023 Jul  Zenbleed (CVE-2023-20593)
          洩漏來源：AMD Zen 2 vzeroupper 觸發 register file 提前釋放
          修補：AMD microcode（MSR chicken bit）

2023 Aug  Downfall / GDS (CVE-2022-40982)
          洩漏來源：Intel AVX GATHER 指令的 Gather Data Buffer
          修補：Intel microcode（禁慢 gather，最高 -50% 效能）

2023 Aug  Inception / SRSO (CVE-2023-20569)
          洩漏來源：AMD phantom call + RSB overflow，Training in Transient Exec
          修補：AMD microcode；Zen 5 硬體層面修復（不受影響）

2024+     研究持續中...
```

每一列的左邊是「上一個防禦假設」，右邊是「本次研究推翻了什麼」。這是重點：**防禦不是終點，只是下一輪研究的起點。**

---

## 20.2 MMIO Stale Data（2022）——本機確認漏洞

### 20.2.1 攻擊原理

MDS 系列（2019）修補的核心邏輯是：在安全邊界切換（VM exit、系統呼叫返回、使用者態切換）時執行 `VERW` 指令，讓 CPU 清空 Line Fill Buffer 與 Store Buffer。

**MMIO 路徑的問題**：當 CPU 核心對 MMIO（Memory-Mapped I/O）暫存器執行讀寫時，資料流過的是另一組「非核心（uncore）」橋接緩衝。這些緩衝的清除指令序列在 VERW 的涵蓋範圍之外。換言之，MDS 的補丁沒把 MMIO 路徑算進去。

這就是 MMIO Stale Data 的本質：用 MMIO 操作觸發資料流經 uncore 緩衝 → 在下一個安全邊界切換前，某核心上的另一個執行緒（攻擊者）在 speculative window 內取樣這些緩衝殘留。

四個 CVE 對應四種略有差異的觸發路徑：

| CVE            | 縮寫  | 觸發路徑                                                         |
|----------------|-------|------------------------------------------------------------------|
| CVE-2022-21123 | SBDR  | Shared Buffer Data Read：MMIO 讀取後殘留在共享緩衝中            |
| CVE-2022-21125 | SBDS  | Shared Buffer Data Sampling：取樣共享緩衝中的跨境資料            |
| CVE-2022-21127 | SRBDS Update | Special Register Buffer Data Sampling（已有 SRBDS 修補的延伸） |
| CVE-2022-21166 | DRPW  | Device Register Partial Write：部分寫入 MMIO 暫存器觸發殘留     |

### 20.2.2 本機狀態：實際讀 sysfs

```bash
# 一次看完所有漏洞狀態
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    printf "%-35s %s\n" "$(basename $f):" "$(cat $f)"
done
```

本機 i7-10700（Comet Lake）的輸出節錄：

```
mmio_stale_data:          Vulnerable: Clear CPU buffers attempted, no microcode
retbleed:                 Mitigation: Enhanced IBRS
meltdown:                 Not affected
spectre_v1:               Mitigation: usercopy/swapgs barriers and __user pointer sanitization
spectre_v2:               Mitigation: Enhanced IBRS; IBPB: conditional; RSB filling; ...
mds:                      Not affected
tsx_async_abort:          Not affected
```

重點解碼 `mmio_stale_data` 那一行：

- **`Clear CPU buffers attempted`**：核心在特權轉換時確實有執行 VERW，試圖清空緩衝。
- **`no microcode`**：Intel 針對 i7-10700 (Comet Lake) 這一批 CPU **尚未發布能讓 VERW 覆蓋 MMIO 路徑的 microcode 更新**。因此 VERW 的清除動作對 MMIO 相關緩衝實際上沒有效果。
- **`Vulnerable`**：最終結論——這台機器在 MMIO Stale Data 這個攻擊向量上是漏洞狀態。

受影響範圍：Intel 第 6 代（Skylake）到第 11 代（Tiger Lake），Comet Lake 正好在其中。AMD 不受影響。

### 20.2.3 實際風險量化

這個「Vulnerable」對不同環境的意義截然不同：

**雲端多租戶伺服器（高風險）**：
- 多個 VM 共享同一個 CPU 核心（超執行緒）
- 攻擊者租用 VM → 控制一個超執行緒
- 受害者 VM 執行 MMIO 操作（幾乎所有 VM 都會）
- 攻擊者在同核心的 SMT 兄弟執行緒上取樣緩衝殘留
- 這是 CVE-2022-21123 系列的核心威脅模型

**單使用者桌機（低風險）**：
- 沒有不受信任的程式碼與核心同時在同一個 SMT 執行緒上執行
- 現代桌機 OS 上 MMIO 呼叫路徑通常在驅動程式中，攻擊者需要先取得程式碼執行能力
- 「Vulnerable」是技術準確描述，但實際利用條件相當嚴苛

結論：**i7-10700 使用者不需要恐慌，但若是在跑多租戶服務，必須確認 microcode 更新狀態。**

---

## 20.3 Retbleed（2022）

**CVE-2022-29900（AMD）、CVE-2022-29901（Intel 第 6-8 代）**
研究者：Johannes Wikner 與 Kaveh Razavi，ETH Zurich COMSEC，2022 年 7 月

### 20.3.1 Retpoline 的隱性假設

Spectre-v2 的修補核心是 **retpoline**：把所有間接跳轉（`jmp [rax]`）替換成透過 `call`/`ret` 序列轉向一個「捕獲 ret」的無窮迴圈，讓 BTB 無法預測真正目標。

這個設計背後有一個**未明言的假設**：`ret` 指令本身是安全的——它依賴 RSB（Return Stack Buffer）而非 BTB，而 RSB 無法被外部污染。

Retbleed 的貢獻是：**這個假設在特定條件下是錯的。**

### 20.3.2 RSB underflow → BTB fallback

當 RSB 溢空（underflow）——例如深度遞迴、信號處理器返回、或某些系統呼叫路徑——CPU 在 RSB 為空時仍需預測 `ret` 目標。此時：

- **AMD Zen 1/2**：fallback 到 BTB（Branch Target Buffer）進行預測
- **Intel Skylake/Kabylake（第 6-7 代）**：相同行為

攻擊者可以用 Spectre-v2 同款的 BTB 污染手法（cross-privilege BTB injection）控制這個 fallback 預測 → `ret` 跳到攻擊者選擇的 gadget → 洩漏 kernel 記憶體。

本機 i7-10700（第 10 代，Comet Lake）有 eIBRS（Enhanced IBRS），硬體層面阻止了跨特權 BTB 污染，所以：
```
retbleed: Mitigation: Enhanced IBRS
```
這台機器的 Retbleed 防禦是完整的。

舊款 Skylake/Kabylake 沒有 eIBRS，核心需要改為 RSB stuffing（每次進核心時人工填滿 RSB）+ IBRS，代價約 26% 系統呼叫效能下降。

---

## 20.4 Downfall / GDS（2023）

**CVE-2022-40982**
研究者：Daniel Moghimi，Google，2023 年 8 月

### 20.4.1 AVX GATHER 指令的隱藏緩衝

Intel 的 AVX GATHER 系列指令（`vgatherdps`、`vgatherdpd` 等）設計目的是從記憶體的**分散位置**一次收集多個元素到向量暫存器中，用於向量化稀疏存取模式。

```
vgatherdps ymm0, [rsi + ymm1*4], ymm2
; 從 8 個分散的記憶體位置收集 8 個 float，存入 ymm0
; ymm2 是 mask，ymm1 是偏移向量
```

這類指令在科學計算（HPC）、加密庫（OpenSSL 的 RSA/AES）、資料庫引擎中大量使用。

CPU 為了加速 gather，在微架構層面引入了 **Gather Data Buffer（GDB）** 作為中間暫存。Downfall 發現：這個 GDB 在使用完畢後不會清零，下一次執行 gather 的程式可以用 Flush+Reload 側通道取樣殘留資料。

攻擊者只需：
1. 讓受害者執行包含 gather 指令的程式（例如 AES-NI 表查找、機器學習推論）
2. 在相鄰超執行緒反覆執行 `vgatherdps` 並 F+R 觀察快取狀態
3. 從 GDB 殘留取出受害者的中間值 → 還原 AES 金鑰

受影響 CPU：Intel 第 6 代（Skylake）到第 11 代（Ice Lake），Comet Lake（第 10 代）在範圍內，但需要確認 Intel microcode advisory 是否包含該確切 stepping。修補成本：**gather-heavy 工作負載效能下降最高 50%**。這是目前瞬態執行修補中代價最高的一個。

---

## 20.5 Zenbleed（2023）

**CVE-2023-20593**
研究者：Tavis Ormandy，Google Project Zero，2023 年 7 月
受影響：**AMD Zen 2 ONLY**（Ryzen 3000、EPYC Rome）

### 20.5.1 這不是 speculative execution，是 CPU Bug

Zenbleed 的成因與前幾章討論的「speculative execution」路徑不同，它是 **CPU register renaming 邏輯中的 bug**。

背景：`vzeroupper` 是 Intel/AMD x86-64 ABI 中常用的指令，功能是清零所有 YMM 暫存器的上 128 位元（bit 128-255）。用途是避免 AVX→SSE 轉換懲罰（legacy SSE 指令不碰 YMM 上半部，但 CPU 仍需追蹤它是否為「已知零」以優化 store-forwarding）。

在 AMD Zen 2 上，特定的微架構條件（涉及投機路徑 + speculative `vzeroupper`）會觸發：
- CPU 的 register renaming 邏輯把某個 YMM 實體暫存器「提前歸還」給空閒池
- 但這個暫存器上仍有未完成的運算
- 下一個程式分配到同一個實體暫存器時，可以讀到前一個使用者的殘留資料

這個 bug 的觸發需要投機執行路徑，但核心不是「利用投機視窗看受保護記憶體」，而是「利用 CPU bug 造成暫存器值在程式間洩漏」。

**洩漏速率**：在同一核心（不需 SMT）的跨程式洩漏可達 **30 kB/s**，可跨 VM 洩漏。修補方式：AMD 發布 microcode，設定一個 MSR "chicken bit" 改變 `vzeroupper` 的行為。

---

## 20.6 Inception / SRSO（2023）

**CVE-2023-20569**
研究者：ETH Zurich COMSEC，2023 年 8 月
受影響：AMD Zen 1 到 Zen 4；**Zen 5 不受影響**（硬體層面修正）

### 20.6.1 兩個技術的組合拳

Inception 是兩個較小發現的組合：

**Phantom Speculation（幻影投機）**：AMD CPU 在某些條件下，對非 `call` 指令也會短暫地**投機性地當成 call 來執行**——包括插入一個虛假的返回地址到 RSB。這個行為在指令退出（retire）時會被撤銷，但 RSB 的污染效果已經發生。

**Training in Transient Execution（TTE）**：在 speculative window 內訓練分支預測器。由於 speculative window 結束後架構狀態會回滾，但微架構的 BTB/RSB 副作用**不會**被回滾，所以可以用 transient 執行訓練預測器，效果在正常（non-speculative）執行中持續有效。

組合起來：
```
攻擊者在 transient window 內觸發 phantom call
→ RSB 被植入攻擊者控制的返回地址
→ 受害者的 ret 指令使用被污染的 RSB 預測
→ 跳到攻擊者選定的 kernel gadget → 洩漏核心記憶體
```

因為整個訓練過程在 transient execution 內完成，傳統的 indirect branch 監控不會察覺。「Inception」這個名字就是從「在夢中植入念頭」的電影概念來的——攻擊在 transient 世界裡植入訓練，效果在真實世界裡生效。

AMD Zen 5 是第一個在硬體層面修復 RSB 邏輯的 AMD 世代，不需要 microcode 緩解。

---

## 對比與取捨

### 攻擊全景比較表

| 攻擊             | 年份 | CVE                | 廠商  | 受影響世代              | 洩漏來源          | 本機狀態    | 修補代價               |
|------------------|------|--------------------|-------|------------------------|-------------------|-------------|------------------------|
| MDS/ZombieLoad   | 2019 | CVE-2018-12126等   | Intel | 第 1-8 代              | LFB / Store Buf   | Not affected| 超執行緒停用（部分）    |
| MMIO Stale Data  | 2022 | CVE-2022-21123等   | Intel | 第 6-11 代             | Uncore MMIO buf   | **Vulnerable** | microcode（本機缺）  |
| Retbleed         | 2022 | CVE-2022-29900/901 | 兩者  | AMD Zen1/2, Intel 6-8代| BTB via ret       | Mitigated   | ~26% syscall overhead  |
| Zenbleed         | 2023 | CVE-2023-20593     | AMD   | Zen 2 only             | Register file     | Not affected| AMD microcode          |
| Downfall/GDS     | 2023 | CVE-2022-40982     | Intel | 第 6-11 代             | Gather Data Buf   | 待確認      | 最高 -50% gather 效能  |
| Inception/SRSO   | 2023 | CVE-2023-20569     | AMD   | Zen 1-4                | RSB (phantom call)| Not affected| AMD microcode          |

### 防禦相互關係：每次攻擊如何打破前一個防禦

```
Spectre-v2 攻擊 BTB
    → retpoline 修補（假設 ret 安全）
        ← Retbleed 打破此假設（ret → BTB fallback）

MDS 攻擊 LFB/Store Buffer
    → VERW + MDS_NO microcode（假設 VERW 覆蓋所有緩衝）
        ← MMIO Stale Data 打破此假設（MMIO 路徑未覆蓋）

IBPB/eIBRS 防禦跨特權 BTB 污染
    → （假設所有 RSB 使用是安全的）
        ← Inception 用 phantom call 污染 RSB（非 BTB）
```

---

## 踩雷集錦

**雷 1：「Retpoline 永久修好了 Spectre-v2」**

Retpoline 是針對 BTB-based indirect branch injection 的修補，它確實防住了原始的 Spectre-v2 攻擊。但它的假設是「`ret` 不會回退到 BTB」。Retbleed 用 RSB underflow 情境推翻了這個假設。沒有任何防禦是「永久的」，只有「當時已知攻擊面的防禦」。

**雷 2：「sysfs 顯示 Not affected 就是免疫」**

`meltdown: Not affected` 只代表 Comet Lake 不受 CVE-2017-5754 的「原始」Meltdown 影響。Downfall 是一個完全不同的 Intel 洞，針對 gather 指令的緩衝，且 Comet Lake 世代仍需確認是否在受影響範圍內。每個 CVE 條目是獨立的。

**雷 3：「AMD 比 Intel 安全」**

Meltdown 確實主要影響 Intel（AMD 在硬體層面就阻止了跨特權推測讀取）。但 Zenbleed（Zen 2 register file bug）、Inception/SRSO（Zen 1-4 RSB）、Retbleed AMD CVEs 都是 AMD 自己的漏洞。每個廠商都有自己的死角，沒有一方在「speculative execution security」上佔有系統性優勢。

**雷 4：「修補的效能代價可以忽略」**

這是最危險的謊言。Downfall microcode 修補在 gather-heavy HPC / ML 工作負載上造成最高 **50% 效能下降**。Retbleed IBRS 修補在系統呼叫密集型工作負載（如資料庫）上造成約 **26% overhead**（Linux kernel 開發者實測數字）。雲端供應商為了這些補丁承受了真實的成本，部分決定延遲部署或讓客戶選擇是否啟用。

---

## 進階：再往深一層

### Microcode 的角色：為什麼「no microcode」是嚴重的

現代 CPU 的 microcode 是一層可更新的韌體，位於 ISA 指令解碼器與實際硬體執行引擎之間。Intel 可以透過 microcode 更新改變 `VERW` 的行為——讓它在執行時額外清除 MMIO 路徑相關的緩衝。

但 Intel 不一定對所有受影響 CPU 世代發布 microcode。商業考量、CPU 壽命、工程資源都是因素。i7-10700 的「no microcode」意味著：

```
核心嘗試的 mitigation：VERW 在邊界切換時執行 ✓
VERW 能否清除 MMIO 相關緩衝：需要 microcode 協作 ✗
結果：MMIO Stale Data 的 mitigation 不完整
```

對比 MDS（2019）：第 8 代起的 CPU 有 MDS_NO microcode 旗標，VERW 完整地清除了 LFB 與 store buffer。Comet Lake 的 `mds: Not affected` 就是因為它有這個 microcode 支援。

### 從研究者的角度：如何找新的瞬態執行攻擊

每次成功的新攻擊都遵循同一個方法論：

1. **找到一個微架構緩衝/結構**，它在不同安全域之間共享且未被現有 VERW/IBPB/清除指令覆蓋
2. **找到一個觸發路徑**，讓攻擊者能讓受害者的資料流過那個結構
3. **建立洩漏原語**：F+R、Prime+Probe 或 port contention 讀出結構中的資料
4. **找到現有防禦的假設邊界**，然後設計一個不在那個假設內的攻擊（Retbleed 打破「ret 不走 BTB」；MMIO Stale Data 打破「VERW 覆蓋所有緩衝」）

這個框架解釋了為什麼攻擊不會停止：微架構結構的數量遠超過公開文件的描述，每一個都是潛在的洩漏源。

---

## 動手練習

**練習 1：枚舉並理解本機的全部漏洞狀態**

```bash
# 完整輸出，不遺漏任何項目
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    name=$(basename "$f")
    status=$(cat "$f")
    echo "[$name]"
    echo "  $status"
    echo ""
done
```

對每一個 `Vulnerable` 或 `Mitigation:` 項目，查閱對應的 CVE，確認：
- 受影響的 CPU 世代
- 攻擊觸發條件（需要 SMT？需要跨 VM？）
- 是否有 microcode 版本需求

**練習 2：驗證 MMIO Stale Data 的 microcode 狀態**

```bash
# 確認目前 microcode 版本
grep -i "microcode" /proc/cpuinfo | head -1

# 查看 dmesg 中 microcode 更新訊息
dmesg | grep -i microcode

# 確認 kernel 的 mitigation 旗標
cat /sys/devices/system/cpu/vulnerabilities/mmio_stale_data
```

比對 Intel 的官方 microcode advisory（INTEL-SA-00615）確認 i7-10700 是否列在「需要更新但尚未發布」的清單中。

**練習 3：效能代價量化**

在支援 Intel microcode 更新的系統上（或 VM 中）：

```bash
# 測量 syscall overhead（模擬 Retbleed 修補影響）
# 使用 syscall benchmark
time (for i in $(seq 100000); do ls /dev/null > /dev/null 2>&1; done)

# 比較啟用/停用 mitigations 的差異
# （謹慎：僅在測試環境中使用 mitigations=off）
# grub: append "mitigations=off" 到 kernel 參數
```

注意：`mitigations=off` 是研究/基準測試用途，不應用於生產或任何網路連線系統。

---

## 本章重點整理

- 2018 年後的瞬態執行攻擊研究是持續進行的軍備競賽，每一個修補都只堵住「當時已知的攻擊向量」，新研究從「修補的假設」找缺口。
- **MMIO Stale Data（2022）** 的核心洞見：VERW（MDS 的修補工具）沒有覆蓋 MMIO 路徑上的 uncore 緩衝；本機 i7-10700 因缺少 microcode 支援而處於 Vulnerable 狀態。
- **Retbleed（2022）** 打破「retpoline 假設 `ret` 安全」；AMD Zen1/2 與 Intel 第 6-8 代的 `ret` 指令在 RSB underflow 時回退到 BTB 並可被毒化。
- **Downfall / GDS（2023）** 利用 AVX GATHER 指令的 Gather Data Buffer 洩漏跨域資料；修補代價最高達 -50% gather 效能。
- **Zenbleed（2023）** 是 AMD Zen 2 的 register file bug，透過 `vzeroupper` 的投機路徑觸發，並非傳統 speculative execution 攻擊。
- **Inception / SRSO（2023）** 組合 phantom speculation 與 Training in Transient Execution 污染 AMD 的 RSB；Zen 5 是首個硬體修復的世代。
- 評估「Vulnerable」的實際風險必須考慮攻擊觸發條件（需要 SMT 共用核心？需要程式碼執行？需要雲端多租戶環境？）。

---

## 自我檢核

1. 解釋 MMIO Stale Data 與 MDS（ZombieLoad）的差異——為什麼 VERW 修補了 MDS 但沒修補 MMIO Stale Data？
2. Retbleed 如何使 retpoline 失效？retpoline 的「安全假設」是什麼，Retbleed 如何打破它？
3. 本機 sysfs 輸出 `mmio_stale_data: Vulnerable: Clear CPU buffers attempted, no microcode` 中，「no microcode」具體指什麼？沒有 microcode 配合，`Clear CPU buffers attempted` 能有效防禦嗎？
4. Zenbleed 和 Spectre 系列在根本成因上有什麼差異？
5. Inception 的「Training in Transient Execution」與傳統的 Spectre-v2 BTB 訓練有何不同？為什麼「在 transient window 內訓練」有效？
6. Downfall 修補在 HPC 工作負載上的最高效能代價是多少？這對雲端供應商的修補決策有什麼影響？

---

## 延伸閱讀

- Moghimi, Daniel. "Downfall: Exploiting Speculative Data Gathering." USENIX Security Symposium, 2023. https://downfall.page/
- Ormandy, Tavis. "Zenbleed." Project Zero Blog, July 2023. https://lock.cmpxchg8b.com/zenbleed.html
- Wikner, Johannes & Razavi, Kaveh. "RETBLEED: Arbitrary Speculative Code Execution with Return Instructions." USENIX Security Symposium, 2022. https://comsec.ethz.ch/research/microarch/retbleed/
- ETH Zurich COMSEC. "Inception: Exposing New Attack Surfaces with Training in Transient Execution." USENIX Security Symposium, 2023. https://comsec.ethz.ch/research/microarch/inception/
- Intel Security Advisory INTEL-SA-00615. "2022 IA32/MMIO Stale Data Advisory." https://www.intel.com/content/www/us/en/security-center/advisory/intel-sa-00615.html
- AMD Security Bulletin AMD-SB-7008. "Return Address Predictor Vulnerability (Inception)." https://www.amd.com/en/resources/product-security/bulletin/amd-sb-7008.html

---

前面幾章我們建立了第一波攻擊（Meltdown/Spectre/MDS）的基礎，本章則展示了後續的軍備競賽如何以每年發現新變體的節奏繼續。下一章我們退後一步，建立一個系統性的分類框架，把所有瞬態執行攻擊統一整理成一張可查閱的分類表。

→ [下一章：瞬態執行攻擊分類法](21-transient-execution-taxonomy.md)
