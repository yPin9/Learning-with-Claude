# Ch 1 — Micro vs macro benchmark：選哪個、怎麼避免錯誤

> 目標：區分 micro-benchmark（測單個 function/迴圈）跟 macro-benchmark（測完整 workload）的差別，了解各自的適用情境、陷阱、怎麼避免「測了半天結果沒意義」。

## 兩種 benchmark 的光譜

```
Micro                                                 Macro
 │─────────────────────────────────────────────────────│
 ↓                                                     ↓
測 memcpy()      測 SQLite 查詢    測完整 video encode   測整個 Linux distro
幾 ns            幾 ms               幾秒                 幾分鐘
重複 10^9 次      重複 100 次         重複 10 次           重複 3 次
```

選哪個取決於你的問題。

## Micro-benchmark：測 primitive

### 定義

- 測試 scope：**一個 function、一條 instruction、一個 data-structure operation**
- 時間尺度：nanoseconds ~ microseconds
- 重複次數：10^6 ~ 10^9

### 典型場景

```c
// 測 popcount 的實作速度
for (int i = 0; i < 1000000000; i++) {
    s += __builtin_popcount(data[i & 0xFFF]);
}
```

### 好處

- **可控**：其他 variable 少、可以 isolate 特定 aspect
- **快迭代**：一次 run 幾秒、改一改 re-run
- **容易解讀**：只測一件事、結果直接
- **對 compiler 改進敏感**：改 backend pattern 可能讓這個 function 快 50%

### 陷阱

#### 陷阱 1：Compiler 把 loop 優化掉

```c
int sum() {
    int s = 0;
    for (int i = 0; i < 1000000000; i++) s += 1;
    return s;
}
```

`-O2` 編 → compiler 發現「這是 sum of N ones」→ 直接 return N → loop 消失。

**perf stat 顯示 1 μs、不是你想測的東西**。

**解法**：

```c
volatile int s = 0;
// 或
__asm__("" :: "r"(data[i]));     // compiler barrier
```

#### 陷阱 2：Cold cache / warm cache 差很大

第一次 access memory → L1/L2 miss → load from DRAM。之後 access → cache hit。

```
First iteration:  100 cycles
Steady state:       3 cycles
```

測 benchmark 沒 warmup → 噪音極大。

**解法**：warmup loop、丟掉前 N 次結果。

#### 陷阱 3：Branch predictor learn

```c
if (x > 0) a = 1; else a = 2;
```

如果 `x` pattern 固定 → predictor 100% 準確 → branch 幾乎 free。
如果 `x` random → 50% miss → 每次 10 cycle penalty。

真實負載多半不是 pure random 也不是 pure fixed。**Micro-bench 很難模擬**。

#### 陷阱 4：Spectre/Speculation 的影響

現代 CPU 會 speculate branch、prefetch memory。某些 benchmark 模式「偶然」對 speculation 友好、數字過於樂觀。

---

## Macro-benchmark：測 workload

### 定義

- 測試 scope：**完整 application、整個 benchmark suite**
- 時間尺度：seconds ~ minutes
- 重複：3-10 次夠

### 典型

- SPEC CPU 2017 整套跑
- FFmpeg transcode 一個 video
- Nginx 跑 wrk 測 throughput
- Linux kernel `make -j` build 時間

### 好處

- **擬真**：反映實際 workload
- **涵蓋 E2E effect**：memory footprint、cache pollution、branch entropy、I/O 等
- **對客戶有說服力**：「SPEC int 提升 3%」比「popcount 快 50%」有商業意義

### 陷阱

#### 陷阱 1：雜訊巨大

整個 SPEC run 一次 3-5 小時。不同 run 差 1-3% 是 normal。要有 statistical 方法才能結論。

#### 陷阱 2：Amdahl's Law

改進某個 hot function 10% → 對整體影響可能只 0.5%（如果這 function 只佔 5% 時間）。

Macro benchmark 沒 reveal 哪裡改得好 → 要 profile 確定。

#### 陷阱 3：硬體 variation

Turbo boost、thermal throttle、NUMA effect、background process 都讓 macro benchmark 更難 reproduce。

#### 陷阱 4：Input dependence

SPEC 有 reference input 跟 train input。跑 train 比較快、但數字不能代表 reference。

## 何時用哪個

```
我要改 compiler 的某個 pattern match → Micro
我要 showcase 整體效能給客戶       → Macro
我要找 optimization 方向           → Macro 先找 hot、再 Micro 深挖
我要 reproduce bug 的效能迴歸     → Micro（快 iterate）
我要 release 決定（GO/NO-GO）      → Macro
```

兩種互補，不是 either/or。**現實工作流程**：

```
Macro 跑一次 → 找 hot function
    ↓
Micro 針對 hot function → 確認可以改善
    ↓
改 compiler
    ↓
Micro 驗證改善
    ↓
Macro 驗證沒 regression
    ↓
Ship
```

## Benchmark 的「代表性」

選 benchmark 永遠要問：**這代表我的 workload 嗎？**

### 例 1：嵌入式 MCU

不適合 SPEC CPU（太大、假設有 OS）。
適合：Coremark、Embench、Dhrystone（老但還在用）。

### 例 2：Server CPU

SPEC CPU 標配。但也要跑自家 target workload：web server、database、VM。

### 例 3：AI inference

SPEC / Coremark 沒 cover。用 MLPerf、TensorFlow benchmark、自家 kernel。

### 例 4：Crypto / video

專屬 benchmark：AES-NI throughput、H.264 encode FPS。

**不要「拿到的 benchmark 就跑」**。想清楚它測什麼、是否代表你關心的。

## Nano-benchmark：更小的尺度

Micro 的 extreme 版：測**單條指令**。

```
藥廠做新藥要做 lab test。你要研究 `add` 指令？
  nano-bench: 10^12 次 ADD、量每次 cycle
```

用途：

- Compiler backend 驗證 scheduling model 正確
- 硬體工程師量 pipeline delay

工具：`perf stat -e cycles,instructions` + 精心寫 micro-benchmark + 跑 1 billion iteration。

## 現代 CPU 挑戰 micro benchmark 的有效性

OoO + speculation + hyperthreading + turbo 讓 micro bench 越來越失真：

- Dispatch / execute 界線模糊
- 同時 in-flight 幾十條指令
- 單獨測一條指令不等於 workload 裡那條指令

**所以 benchmarking 工程師越來越依賴 macro**。Micro 只作為「**假設驗證**」工具。

## 常見誤會

1. **「Micro 比 macro 精準」**：**反過來**。micro 噪音小、但代表性差。macro 噪音大、但更 meaningful。
2. **「跑越多次 iteration 越準」**：到某 point 邊際效益變零。看 standard deviation 是否穩定、不是看 iteration 數。
3. **「一次測量夠」**：**絕對不夠**。至少 5 次、取中位數或 mean。Ch 4 專講。
4. **「Release build 一定有代表性」**：debug build 跑 benchmark 沒用。但 release build 也有 compiler flag 差異 (`-O2` vs `-O3`)。
5. **「Benchmark 數字有絕對意義」**：永遠相對。「這個改進讓 X benchmark 在 Y 硬體上快 Z%」是有意義 statement。

## 動手練習

1. 寫一個 simple loop、用 `-O0` vs `-O2` 跑，看被優化掉的情境。
2. 寫一個 micro-benchmark 測 `__builtin_popcount` vs 軟體版本，用 `perf stat` 對比。
3. 同個 benchmark 跑 10 次，記錄數字、算 mean / stddev。
4. 查 SPEC CPU 2017 的 benchmark 列表，選 3 個 integer、3 個 floating-point，寫下每個測什麼。
5. 對 `libpng` 的 `png_read_image` 函式做一個 mini-benchmark：給固定輸入、測 decode 時間。

## 自我檢核

- [ ] 我能分辨 micro / macro benchmark 的用途
- [ ] 我知道 compiler 可能把 benchmark 優化掉，能用 volatile / asm barrier 避免
- [ ] 我知道 cold cache / warm cache / branch predictor 對 micro bench 的影響
- [ ] 我能選對的 benchmark 給對的 workload
- [ ] 我能說「這個 benchmark 的意義跟限制」

下一章深入 SPEC CPU — 業界最重要的 benchmark。

→ [Ch 2 SPEC CPU：業界 benchmark 之王](./02-spec-cpu.md)
