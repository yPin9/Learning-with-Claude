# Ch 0 — 環境與工具搭建

> **目標**：把整門課賴以為生的「量測環境」建起來——一個校準過的計時 harness，能穩定分辨「這條 cache line 現在在快取裡（快）還是被踢出去了（慢）」。讀完你手上會有一支真的跑得出乾淨 hit/miss 分佈的程式，以及一套判斷「我這台機器能重現哪些攻擊」的方法。

> **環境**：本課主機 **Intel Core i7-10700（Comet Lake, 8 核 16 執行緒, base 2.9 GHz）**，跑 **WSL2 上的 Ubuntu 22.04**。工具：gcc 11.4、msr-tools 1.3（rdmsr/wrmsr）、cpuid 20211210、util-linux 2.37（taskset）、dudect（constant-time 驗證，Ch 32 用）。**WSL2 是一層 Hyper-V VM**——這對微架構量測有真實影響，本章會誠實交代哪些能做、哪些要原生 Linux。ARM/RISC-V 的對照散在各章，但動手一律 x86-64。

## 為什麼需要這個？

微架構攻擊的全部武器，說穿了只有一把尺：**時間**。

你沒有 API 能問 CPU「這條資料在 L1 還是在 DRAM」。你能做的，是**存取它、數它花了多少時間**——快，代表它在快取裡；慢，代表它得從記憶體撈。cache 攻擊、Spectre 洩漏、KASLR 破解，最後全都收斂到同一個動作：`存取 → 計時 → 比門檻`。

所以這門課的第一件事不是攻擊，是**把尺磨準**。一把沒校準的尺，量什麼都是雜訊——微架構攻擊初學者 80% 的「原理我懂了但 PoC 跑不出來」，不是原理錯，是量測環境沒調好：CPU 在不同核心間跳、turbo 忽快忽慢、prefetcher 偷偷把資料抓進快取、編譯器把你的計時指令重排。這一章就是把這些雜訊源一個個壓掉，最後校準出**這台機器的 hit/miss 門檻**。

沒有這個門檻，後面每一章都是空談。有了它，後面每一章都只是「同一把尺，量不同的東西」。

## 先建立直覺：把快取當成一個會洩漏的碼表

```
   你要問的問題              你能做的動作                  你得到的答案
 ┌────────────────┐      ┌──────────────────┐        ┌─────────────┐
 │ 這條 cache line │      │  讀它一次         │        │ ~24 cycles  │
 │ 現在在快取裡嗎？│ ───► │  同時用 rdtsc 計時 │ ─────► │  → 在！(hit) │
 │                │      │                  │        │             │
 │                │      │                  │        │ ~245 cycles │
 │                │      │                  │        │  → 不在(miss)│
 └────────────────┘      └──────────────────┘        └─────────────┘
                                                   中間差 10 倍，門檻好抓
```

想像快取是一個碼表：你按下去（存取），它跑一段時間再停。如果資料就在手邊（L1 hit），碼表只跳一點點；如果資料遠在 DRAM（miss），碼表跳一大截。**這個時間差就是洩漏**——只要有人的行為會影響「某條 line 在不在快取裡」，你就能透過計時把那個行為讀出來。整門課都在玩這一招的各種變奏。

這章要做的，就是把這支「碼表」造出來、並確認它在你的機器上真的能把 24 和 245 分得清清楚楚。

## 計時原語：`rdtsc` / `rdtscp`

x86 上讀碼表的指令是 `rdtsc`（Read Time-Stamp Counter）——回傳一個從開機以來單調遞增的 64 位元計數器。它的變體 `rdtscp` 多了一個序列化保證（等前面的指令都 retire 才讀），更適合計時。

最小的計時函式：

```c
#include <x86intrin.h>
#include <stdint.h>

static inline uint64_t timed_access(volatile char *p) {
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);   // 讀開始時間（rdtscp 會等前面指令 retire）
    (void)*p;                        // 存取目標 line
    uint64_t t1 = __rdtscp(&junk);   // 讀結束時間
    return t1 - t0;
}
```

三個關鍵決策，每一個都不能省：

- **為什麼 `volatile`**：不加的話編譯器會發現 `*p` 的結果沒被用到，直接把整行存取優化掉——你的碼表會量到 0。`volatile` 強迫它真的去記憶體讀。
- **為什麼 `rdtscp` 而非 `rdtsc`**：`rdtsc` 不保證前面的指令都做完才讀，CPU 亂序執行下你的 `t0` 可能在存取之後才被讀到，量出負數或亂數。`rdtscp` 帶一個 load fence 語意，把時間窗釘死。（更嚴謹還會在前後加 `_mm_lfence()`，Ch 4 深入。）
- **為什麼回傳的單位要小心叫「cycles」**：現代 Intel 的 TSC 是 **invariant TSC**——它以**固定的參考頻率**遞增（約等於 base 2.9 GHz），**跟核心當下實際跑多快無關**。核心 turbo 到 4.8 GHz 時，一個 TSC tick 蓋掉的實際核心週期數會變。所以我們量到的「cycles」精確說是 **TSC ticks ≈ base-clock 週期**，當**相對**尺度用完全可靠，但別把它當成精準的核心週期數。

> **WSL2 誠實交代（第一個）**：這台機器的 `/proc/cpuinfo` **看不到 `constant_tsc`／`nonstop_tsc` 旗標**——不是硬體沒有，是 WSL2 這層 VM 把 flags 列表裁短了。TSC 在 Comet Lake 硬體上仍是 invariant 的，量測照樣可靠；但這提醒你：**在 WSL2 裡別太相信 `/proc/cpuinfo` 的完整性**，要看硬體真相時以原生開機或 `cpuid` 指令為準。

## 壓掉雜訊：CPU pinning 與那些會騙你的東西

碼表準了，還要讓被量的東西別亂動。三大雜訊源：

**1. 行程在核心間漂移**。OS 排程器隨時會把你的行程搬到別的核心，而 cache 狀態是**每核心**的——搬一次，你精心佈置的快取狀態全沒了。用 `taskset` 把行程釘死在一個核心：

```bash
taskset -c 2 ./calibrate      # 只在 CPU 2 上跑
```

**2. Turbo / 頻率縮放**。核心頻率浮動雖然不影響 invariant TSC 的 tick 率，但會影響「同樣一段工作花幾個 TSC tick」的穩定度。原生 Linux 上可以固定頻率：

```bash
# 原生 Linux（非 WSL2）：關 turbo、鎖 performance governor
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo
sudo cpupower frequency-set -g performance
```

> **WSL2 誠實交代（第二個）**：這台機器 `/sys/devices/system/cpu/cpu0/cpufreq/` **根本不存在**——WSL2 的 VM 不把 cpufreq 介面透給你。也就是說**turbo 與頻率的控制得在 Windows host 端做**（BIOS 關 turbo、或 Windows 電源計畫鎖頻），VM 內部無能為力。這是 WSL2 做微架構量測的真實限制：雜訊會比原生 Linux 大，得靠多取樣、取中位數來壓。

**3. Prefetcher 偷跑**。硬體 prefetcher 會預測你的存取樣式、提前把資料抓進快取——它會在你不知情時把「本該 miss」的 line 變成 hit，污染量測。Intel 用 MSR `0x1A4` 控制四個 prefetcher：

```bash
sudo modprobe msr
sudo rdmsr -p 0 0x1a4        # 讀目前狀態
# 本機實測輸出：0   （0 = 四個 prefetcher 全開）
sudo wrmsr -p 0 0x1a4 0xf    # 低 4 bit 設 1 = 四個 prefetcher 全關
```

> **WSL2 意外之喜**：跟 cpufreq 相反，`rdmsr`/`wrmsr` 在這台 WSL2 上**可以用**（`sudo modprobe msr` 後 `rdmsr -p 0 0x1a4` 真的回 `0`）。所以 prefetcher 控制你做得到。這不保證每台 WSL2 都行（取決於 Hyper-V 設定），但值得一試——關掉 prefetcher 常讓 Prime+Probe（Ch 8）的分佈乾淨一個檔次。

## 校準：量出你這台機器的 hit/miss 門檻

現在把碼表和降噪組起來，做全課最重要的一次量測。程式邏輯：對同一條 line，先反覆「存取後再計時」（保證 hit），再反覆「clflush 踢出去後計時」（保證 miss），各累積十萬次的直方圖：

```c
// calibrate.c（核心片段；完整版見本章練習）
#define N 100000
static char buf[4096];
volatile char *p = &buf[1024];
uint64_t hist_hit[400]={0}, hist_miss[400]={0};

for (int i=0;i<N;i++){ (void)*p; uint64_t d=timed_access(p); hist_hit[d>399?399:d]++; }        // 先 touch → 保證在快取
for (int i=0;i<N;i++){ _mm_clflush((void*)p); uint64_t d=timed_access(p); hist_miss[d>399?399:d]++; } // clflush → 保證不在
```

在本機 `taskset -c 2 ./calibrate` 的**真實輸出**：

```
HIT  median = 24 cycles
MISS median = 244 cycles
--- HIT distribution (cycles: count) ---
  23: 9335
  24: 75669
  25: 9074
  26: 2895
--- MISS distribution ---
 240: 7228
 242: 32166
 244: 31293
 246: 13718
 248: 3967
```

這張圖是整門課的地基，讀懂它：

- **HIT 擠在 24 cycles**（L1 命中），分佈極窄（23–26 佔了 96%）。
- **MISS 擠在 244 cycles**（從 DRAM 撈），分佈也窄（240–248）。
- **兩堆之間隔了 10 倍、中間完全沒有樣本**——這就是為什麼 cache 計時側信道這麼好用：訊號雜訊比高到你隨便抓個門檻都行。

**門檻**取兩峰中間，我用 **150 cycles**：量到的時間 `< 150` 判為 hit（line 在快取）、`>= 150` 判為 miss。這個 `THRESHOLD = 150` 會在後面每一章的 Flush+Reload 出現。

> **這個數字是你機器特有的**。24 和 244 是 i7-10700 + 這套 WSL2 的值。你在別的 CPU（尤其 AMD、或不同世代 Intel）跑，數字會不一樣——所以**每個人都得自己跑一次校準**，抄我的門檻不一定對。這正是本章不能跳過的原因。

## 認識你的機器：它對哪些攻擊脆弱？

攻擊之前先問：這台 CPU 哪些洞還在、哪些硬體修了？Linux 把已知微架構漏洞的狀態放在 sysfs：

```bash
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    printf "%-22s %s\n" "$(basename $f):" "$(cat $f)"; done
```

本機**真實輸出**（節錄）：

```
meltdown:          Not affected                    ← 硬體已修，Ch 18 的 PoC 這台跑不出來
mds:               Not affected                    ← 同上，Ch 19
l1tf:              Not affected
spectre_v1:        Mitigation: usercopy/swapgs barriers ...
spectre_v2:        Mitigation: Enhanced / Automatic IBRS ...
retbleed:          Mitigation: Enhanced IBRS
mmio_stale_data:   Vulnerable: Clear CPU buffers attempted, no microcode ← 這台還脆弱！
```

這張表直接決定你這台機器的**課程動手範圍**：

- **能親手跑出來的**：Flush+Reload、Prime+Probe、covert channel、Spectre-v1（這些是「同一個 process 內訓練預測器 + cache readout」，不靠跨權限漏洞，Comet Lake 照樣中）。
- **跑不出來、只能讀原理的**：Meltdown、MDS、L1TF——硬體與微碼已修（`Not affected`）。這些章節我們講透機制、給「什麼 CPU + 什麼設定能重現」，但不假裝在這台跑出了洩漏。
- **這台竟然還脆弱的**：`mmio_stale_data` 顯示 `Vulnerable`——Ch 20 會談這代表什麼。

`cpuid` 指令能看更細的微架構能力（cache 大小、TLB、關聯度），Ch 3 會用它挖 cache 幾何。

## 對比與取捨

| 量測手段 | 精度 | 需要權限 | WSL2 可用 | 適用 |
|---|---|---|---|---|
| `rdtsc` | 高（~1 tick） | 無 | ✓ | 快速計時，但不序列化 |
| `rdtscp` + lfence | 最高 | 無 | ✓ | 本課預設；cache 計時標配 |
| `perf` HW counters | 高（事件級） | 通常要 root/paranoid 設定 | **✗（WSL2 無）** | 原生 Linux 上做偵測/驗證 |
| MSR 直讀（rdmsr） | — | root | 這台✓（未必每台） | 控 prefetcher、讀 RAPL |

| 環境 | 優點 | 缺點 |
|---|---|---|
| WSL2（本課主環境） | 開箱即用、能真跑 F+R/Spectre-v1 | VM 雜訊大、無 cpufreq、perf 缺、flags 被裁 |
| 原生 Linux / 雙開機 | 雜訊最低、工具齊、能控頻率 | 要另外裝機 |
| 裸機 + 隔離核心（isolcpus） | 研究級乾淨度 | 設定成本高 |

## 踩雷集錦

1. **忘了 `volatile`，量到 0 cycles**：錯誤直覺是「我明明存取了」；正確認識是編譯器看你沒用結果就把存取刪了。計時碼裡的目標指標一律 `volatile`，或用 inline asm 擋優化。
2. **用 `rdtsc` 不加序列化，量出負數或爆表**：錯誤直覺是「rdtsc 就是計時啊」；正確認識是亂序執行會讓 `t0`/`t1` 跟存取的相對順序錯位。用 `rdtscp`，必要時前後夾 `lfence`（Ch 4）。
3. **沒 pin CPU，分佈糊成一團**：錯誤直覺是「跑很多次平均就好」；正確認識是行程被搬到別的核心後 cache 狀態歸零，你平均的是兩顆核心的混合雜訊。先 `taskset -c N`。
4. **把我的門檻 150 直接抄去別的機器**：錯誤直覺是「hit/miss 門檻是固定的」；正確認識是它跟 CPU 型號、記憶體、甚至 VM 層都有關。每台機器自己校準。
5. **在 WSL2 裡想關 turbo 卻找不到 cpufreq**：錯誤直覺是「Linux 就能控頻率」；正確認識是 WSL2 的 VM 不透出 cpufreq，turbo 得在 Windows host / BIOS 關。VM 內只能靠多取樣壓雜訊。

## 進階：再往深一層

- **更狠的降噪**：原生 Linux 上用 `isolcpus=` 把一顆核心從排程器隔離、`nohz_full` 關 tick、把 IRQ 綁到別的核心，能把量測乾淨度推到研究等級。Ch 4 會給一套完整的「乾淨量測 checklist」。
- **prefetcher 的四個位元**：MSR `0x1A4` 的 bit 0–3 分別控 L2 HW prefetcher、L2 adjacent line、L1 DCU、L1 IP prefetcher。做 Prime+Probe（Ch 8）常只需關 L2 相關兩個。細節在 Intel SDM Vol 4。
- **RAPL 能量計數**：MSR 也能讀 running average power limit（能量消耗），那是 Ch 25 Hertzbleed 的量測基礎——但 RAPL 在 VM/近期核心常被限權，屆時誠實處理。
- **不靠 rdtsc 計時**：某些環境（如 JavaScript 沙箱）沒有 `rdtsc`，攻擊者會用「計數器執行緒」（一條狂加變數的 thread 當土製碼表）。Ch 4 會示範這個在瀏覽器 side-channel 的經典替代。

## 動手練習

1. **把校準跑起來**：完整 `calibrate.c` 自己打一遍（把上面片段補成完整程式：`main` 裡宣告直方圖、跑兩個迴圈、印中位數與分佈），`gcc -O0 calibrate.c -o calibrate`，`taskset -c 2 ./calibrate`。記下**你這台**的 hit/miss 中位數與你要用的門檻。
2. **證明 prefetcher 會騙你**：把校準改成「循序存取一個陣列的每條 line」再計時，觀察 prefetcher 如何把後面幾條的 miss 時間壓低；然後 `wrmsr -p N 0x1a4 0xf` 關掉 prefetcher 再跑，對照差異。
3. **讀你機器的脆弱性表**：跑 sysfs vulnerabilities 那段，列出哪些 `Not affected`、哪些 `Vulnerable`/`Mitigation`。對照本課地圖，標出「我這台能親手跑」與「只能讀原理」的章節。
4. **感受 WSL2 雜訊**：同一支 calibrate，不 pin 跑一次、`taskset` 跑一次，比較 MISS 分佈的寬度。體會 pinning 的效果。

## 本章重點整理

- 微架構攻擊的唯一武器是**時間**：存取 → `rdtscp` 計時 → 比門檻，判斷 line 在不在快取。
- 一支能用的 harness 需要：`volatile` 擋優化、`rdtscp` 序列化、`taskset` pin 核心、（可選）關 prefetcher。
- 本機校準結果 **HIT 24 / MISS 244 cycles，門檻取 150**——這是你這台機器特有的，別人得自己校。
- WSL2 能真跑 Flush+Reload/Spectre-v1，但有真實限制（無 cpufreq、perf 缺、flags 被裁、VM 雜訊大）；Meltdown/MDS 這台硬體已修，只能讀原理。
- sysfs `vulnerabilities/` 是你這台機器的「動手範圍地圖」。

## 自我檢核

- [ ] 不看筆記，能不能說出「為什麼 cache 計時能洩漏資訊」的一句話直覺？
- [ ] 能解釋計時函式裡 `volatile`、`rdtscp`、`taskset` 各擋掉哪一種會毀掉量測的雜訊嗎？
- [ ] 有人說「我抄你門檻 150 就好」，你要怎麼跟他解釋為什麼他得自己校準？
- [ ] 面試問「你怎麼在一台陌生 CPU 上判斷它能不能重現 Meltdown」，你會怎麼查、怎麼答？
- [ ] rdtsc 量到的「cycles」到底是不是核心週期？turbo 時會發生什麼？

## 延伸閱讀

### 官方文件 / 手冊

- **[Intel 64 and IA-32 Architectures Software Developer's Manual, Vol 3B — RDTSC/RDTSCP & TSC](https://www.intel.com/sdm)**
  - **讀哪裡**：`RDTSC`/`RDTSCP` 指令頁，以及 "Time-Stamp Counter" 一節談 invariant TSC。
  - **學到什麼**：TSC 為何 invariant、rdtscp 的序列化語意——本章計時原語的權威依據。
  - **前提**：會看指令參考手冊即可。

- **[Intel SDM Vol 4 — Model-Specific Registers（MSR 0x1A4 prefetcher control）](https://www.intel.com/sdm)**
  - **讀哪裡**：MSR 表裡搜 `MSR_MISC_FEATURE_CONTROL (0x1A4)`。
  - **學到什麼**：四個 prefetcher 各對應哪個 bit，本章關 prefetcher 的依據。

### 論文

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - **核心貢獻**：奠定 clflush + 計時的 cache 攻擊範式；本章的校準就是在為它鋪路。
  - **讀哪裡**：Section 3（threat model 與 timing），Section 4 的門檻選取方法正是本章在做的事。
  - **和本章的關聯**：我們量的 hit/miss 分佈，就是這篇圖 1 的實機版。

### 部落格 / 技術文章

- **[Mastik: A Micro-Architectural Side-Channel Toolkit](https://cs.adelaide.edu.au/~yval/Mastik/)** — Yuval Yarom
  - **這是什麼**：cache 攻擊原語的參考實作工具箱（F+R、P+P、eviction set）。
  - **讀哪裡**：先讀 `README` 與 `src/L3.c`；對照本課你自己刻的原語，看研究級實作怎麼處理雜訊。
  - **為什麼值得**：作者是 Flush+Reload 論文作者，這是「正確做法」的黃金對照。

- **[gruss.cc — Daniel Gruss 的 publications](https://gruss.cc/)** — TU Graz
  - **這是什麼**：微架構攻擊產出最密集的研究組首頁。
  - **讀哪裡**：先掃 Rowhammer.js、Prefetch Side-Channel、KASLR 相關幾篇的 abstract，建立「這領域有哪些洞」的地圖。
  - **為什麼值得**：本課後面十幾章的原始論文有一半出自這個組。

環境備好、尺磨準了。下一章我們拉高一層，把整個微架構攻擊的版圖攤開——side channel 與 covert channel 的分野、瞬態執行是什麼、這門課的每一塊怎麼拼起來——讓你在鑽進 Flush+Reload 之前先有張全景地圖。

→ [Ch 1 微架構攻擊全景](./01-microarch-attacks-overview.md)
