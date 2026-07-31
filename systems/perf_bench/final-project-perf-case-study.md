# Final Project — Performance case study + compiler optimization proposal

> **目標**：整合整門課（Ch 0–14）的所有能力——選一個真實的 workload、嚴謹地 profile、找出瓶頸、分析為什麼、提出 compiler-level 的優化建議、驗證效果，產出一份**專業的 performance case study + compiler optimization proposal**（2-3 頁）。這份報告整合了測量嚴謹（Part 1）、微架構分析（Part 2）、profiling 工具（Part 3）、compiler 優化（Part 4）和思考框架（Ch 14）。完成後你有一份能直接當 portfolio 的作品——展示「分析效能、提出 compiler 改進」的完整能力，這正是 SiFive job spec 的核心。

## 專案總覽

你要對一個真實 workload 做完整的 performance case study：

```
Performance case study 的完整流程：

  1. 選 workload（真實的，有優化空間）
     如：一個演算法（排序/矩陣/壓縮）、一個 benchmark 的子項、自己的程式
        │
  2. 測量基準（baseline）
     嚴謹測量（控制環境、多次、統計）—— Part 1
        │
  3. Profile 找熱點
     perf 找出 hot function/loop —— Part 3
        │
  4. 分析瓶頸
     perf events（IPC/cache/branch）判斷瓶頸類型 —— Part 2
     llvm-mca/vectorization report 分析結構 —— Part 3/4
        │
  5. 提出優化（source + compiler）
     瓶頸 → 優化對應（Ch 14 的思考框架）
     source 改進 + compiler-level 建議
        │
  6. 驗證
     優化後嚴謹測量（統計顯著嗎）+ perf 確認瓶頸改善
        │
  7. 寫報告
     case study（分析過程）+ compiler optimization proposal（建議）
        │
  → 從「一個 workload」到「完整的分析 + compiler 建議」
    這是 SiFive compiler 工程師的核心交付
```

這個 case study 整合了全課——它展示你能「拿到一個 workload，系統地分析效能、定位瓶頸、提出有資料支撐的 compiler 優化建議、驗證」。這是 perf_bench 的終極能力，也是 SiFive job spec「analyze performance results and suggest new compiler optimizations」的完整展現。

## 為什麼做這個專案？

這正是 SiFive compiler 工程師的核心工作——benchmarking team 找出某個 workload 慢，你要做完整的分析：profile 找瓶頸、理解為什麼、提出 compiler 能做的優化、驗證效果。前面的練習各做了一塊（練習 A 報告、練習 B llvm-mca），這個 Final 整合成「完整的 case study + compiler proposal」。

完成它，你獲得：一份能直接當 **portfolio 的作品**（展示完整的效能分析和 compiler 優化能力）、把全課知識整合應用的經驗、以及「面對任何 workload，系統地分析並提出 compiler 改進」的能力。這份報告可以直接給 SiFive 或其他 compiler/效能職位的面試當作品集——它證明你具備這個領域的核心能力。

## 整合的課程概念

| 階段 | 整合的章節 |
|---|---|
| 嚴謹測量 | Ch 0（環境）、Ch 4（統計）|
| benchmark 選擇 | Ch 1-3（micro/macro、SPEC、Coremark）|
| 微架構分析 | Ch 5-6（微架構、perf events、top-down）|
| profiling | Ch 7-9（perf、llvm-mca、flame graph）|
| compiler 優化 | Ch 10-13（flag、PGO、LTO、vectorization）|
| 思考框架 | Ch 14（hot loop → 優化）|

整門課至少 70% 的核心概念都用上了——這是 Final Project 的標準。

## 任務規格

選一個 workload，產出 2-3 頁的 case study + compiler proposal：

### 報告結構

1. **Workload 介紹**：是什麼、為什麼選它、有什麼優化空間
2. **測量環境與方法**（Ch 0/4）：完整揭露、嚴謹的方法
3. **Baseline 測量**（Ch 4）：基準效能 + 統計
4. **Profiling**（Ch 7-9）：熱點在哪（perf/flame graph）
5. **瓶頸分析**（Ch 5-6）：瓶頸類型（perf events/top-down）+ 結構分析（llvm-mca/report）
6. **優化**（Ch 10-14）：source 改進 + compiler-level 建議
7. **驗證**（Ch 4/6）：優化後的效能（統計顯著）+ 瓶頸改善（perf）
8. **Compiler optimization proposal**：具體的 compiler 改進建議（有資料支撐）

### 驗收標準

- 測量嚴謹（控制環境、多次、統計顯著）
- profiling 找到真實的熱點（不是猜）
- 瓶頸分析正確（用 perf/llvm-mca 的資料）
- 優化有效（驗證統計顯著）
- compiler proposal 具體可行動（有資料支撐）
- 報告專業（可信、可重現、誠實）

## 建議的 workload

```
適合的 workload（有明顯瓶頸和優化空間）：

  1. 矩陣運算（matmul、transpose）：
     cache-bound（存取模式）+ 能向量化 → loop tiling/interchange/向量化
        │
  2. 排序/搜尋：
     branch-bound（比較分支）→ branchless/PGO
        │
  3. reduction（sum/dot product）：
     dependency-bound（累加鏈）→ 打破相依鏈/向量化
        │
  4. 字串/壓縮處理：
     混合瓶頸 → 多種優化
        │
  5. 一個 benchmark 的子項（Coremark/Embench 的某個）：
     真實的 workload → 完整分析
        │
  → 選一個你能完整分析的（有明顯瓶頸、優化空間、能驗證）
    矩陣運算是好的起點（cache + 向量化，經典且豐富）
```

## 完整參考解答

**這是 Final Project，務必自己做一個完整的 case study！** 下面是一個範例的骨架，你要選自己的 workload 完整做。

<details>
<summary>範例骨架：矩陣轉置的 case study</summary>

```markdown
# Performance Case Study: 矩陣轉置優化

## 1. Workload
矩陣轉置（transpose）—— B[j][i] = A[i][j]。
選它因為：cache 行為是經典的優化案例，有明顯的瓶頸和優化空間。

## 2. 測量環境
- CPU: [型號], performance governor, turbo off
- GCC [版本], -O2 -march=native
- 矩陣大小: 4096×4096 float

## 3. Baseline（naive 轉置）
```c
void transpose(float *A, float *B, int n) {
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            B[j*n+i] = A[i*n+j];   // B 按列寫（cache 不友善！）
}
```
hyperfine: 85ms ± 2ms（n=4096）

## 4. Profiling
perf record/report: transpose 是 hot（99%，明顯）。

## 5. 瓶頸分析
perf stat:
- IPC: 0.4（低 → CPU 在等）
- LLC-load-misses: 高
- → **cache-bound**（B[j*n+i] 按列寫，每次跳一整行 = cache miss）
cachegrind: 確認 B 的寫入是 miss 源（D1 miss rate 高）

## 6. 優化：cache blocking (tiling)
```c
void transpose_tiled(float *A, float *B, int n, int block) {
    for (int ii = 0; ii < n; ii += block)
        for (int jj = 0; jj < n; jj += block)
            for (int i = ii; i < ii+block && i < n; i++)
                for (int j = jj; j < jj+block && j < n; j++)
                    B[j*n+i] = A[i*n+j];   // 分塊：block 放進 cache
}
```
原理：把矩陣分成 block×block 的塊，每塊放得進 cache → 減少 cache miss。

## 7. 驗證
hyperfine: 35ms ± 1ms（block=64）→ **快 2.4 倍**（CI 不重疊，顯著）
perf stat: IPC 從 0.4 升到 1.2，LLC miss 大幅減少 → 瓶頸改善確認

## 8. Compiler Optimization Proposal
**發現**: naive transpose 是 cache-bound（perf: IPC 0.4, LLC miss 高），
cache blocking 讓它快 2.4 倍（IPC 1.2）。

**Compiler 建議**:
1. compiler 的 loop tiling pass（如 GCC 的 -floop-block / Polly）應該能自動做這個。
   建議檢查為什麼預設沒觸發（cost model? 啟用條件?）。
2. 對 RISC-V，tiling 的 block size 應該根據目標 core 的 cache 大小調整
   （SiFive U74 的 L1/L2 大小 → 對應的 block size）。
3. 結合向量化：tiling 後的內層 loop 可以向量化（RVV）進一步加速。

**資料支撐**: perf（cache-bound 的證據）、cachegrind（miss 源）、
hyperfine（2.4x 提升，統計顯著）。
```

**case study 說明**：

- **完整流程**：workload → baseline（嚴謹測量）→ profile（找熱點）→ 瓶頸分析（cache-bound，用 perf/cachegrind 的資料）→ 優化（tiling）→ 驗證（統計顯著 + perf 確認）→ compiler proposal
- **資料驅動**：每個結論有資料支撐（perf 的 IPC/cache miss、cachegrind 的分析、hyperfine 的統計）
- **compiler proposal**：具體（loop tiling）、可行動（檢查 pass 為什麼沒觸發、為 RISC-V 調 block size）、有資料支撐
- **誠實/嚴謹**：統計顯著（CI 不重疊）、揭露條件（可重現）
- **核心**：這展示了「從 workload 到 compiler 建議」的完整能力——SiFive 工作的核心

</details>

## 測試用案例（自我檢查報告品質）

| 檢查項 | 標準 |
|---|---|
| 測量嚴謹 | 控制環境、多次、CI、顯著性 |
| profiling | 找到真實熱點（perf/flame graph）|
| 瓶頸分析 | 用 perf/llvm-mca 的資料（不是猜）|
| 優化驗證 | 統計顯著 + perf 確認瓶頸改善 |
| compiler proposal | 具體、可行動、資料支撐 |
| 專業度 | 可信、可重現、誠實 |

## 延伸挑戰（加分）

- **挑戰一**：多種優化——對一個 workload 嘗試多種優化（tiling + 向量化 + 多累加器），分析各自的貢獻

- **挑戰二**：RISC-V——在 RISC-V 上做 case study，包含 RVV 向量化的分析（SiFive 最相關）

- **挑戰三**：PGO/LTO——對一個較大的 workload，加入 PGO/LTO 的效果分析

- **挑戰四**：對比 compiler——比較 gcc vs clang 對同 workload 的 code，分析優化差別，提出建議

- **挑戰五**：寫成 blog/talk——把 case study 寫成一篇技術 blog 或準備成一個 talk（這是真實工程師分享的形式）

- **挑戰六**：投稿/PR——如果你的分析發現了 compiler 的真實改進機會，考慮在 LLVM/GCC 的 issue 提出（真實的貢獻）

## 自我檢核

完成這個專案後，你應該能回答：

- [ ] 我能對任何 workload 做完整的效能分析（測量→profile→瓶頸→優化→驗證）
- [ ] 我的測量嚴謹（控制環境、統計顯著、可重現）
- [ ] 我能用 perf/llvm-mca 等工具定位瓶頸（不是猜）
- [ ] 我能從瓶頸提出有資料支撐的 compiler 優化建議
- [ ] 面試被問「你怎麼分析效能、提出 compiler 優化」，我能展示這份 case study
- [ ] 我理解這整合了全課（測量嚴謹 + 微架構 + 工具 + compiler 優化 + 思考框架）

## 結語：你現在站在哪裡

完成這門課和這個 case study，你已經從「會寫程式但效能靠感覺」進化到「能量化分析效能、提出 compiler 優化」。你知道：

- 怎麼做嚴謹的測量（控制環境、統計、破除迷思）（Part 1）
- CPU 微架構和效能事件（IPC/cache/branch/top-down）（Part 2）
- profiling 工具（perf 找熱點、llvm-mca 分析指令、flame graph 視覺化）（Part 3）
- compiler 優化的真相（flag/PGO/LTO/vectorization，破除「越高越快」迷思）（Part 4）
- 從 hot loop 倒推「該加什麼優化」的思考框架（Ch 14）

這些不是「會用工具」，是**量化分析效能、提出 compiler 改進的能力**。你能面對任何 workload，系統地分析、定位瓶頸、提出有資料支撐的優化建議——這正是 SiFive job spec「analyze performance results and suggest new compiler optimizations」要的能力。

最重要的是 perf_bench 的核心信條——**實測取代空談、破除迷思、資料驅動**。你不再說「這段 code 很慢」（沒用），而是說「這個 function 的 IPC 0.3、LLC miss 40%，是 cache-bound，建議 loop tiling，預期提升 X%，資料如下」（有用、可信、可行動）。這是效能工程師和「憑感覺優化」的人的根本差異。

接下來往哪去？這門課的「參考資料」（見 [README](./README.md)）列了進階方向：Brendan Gregg 的《Systems Performance》（效能分析的全面權威）、Agner Fog 的微架構手冊（指令層優化的極致）、Denis Bakhvalov 的書（現代 CPU 效能分析）。但更重要的是——**去分析真實的 workload、去提出真實的優化**。你的工具和框架是放大鏡，真實的效能問題是最好的老師。如果你的目標是 SiFive 或 compiler 效能工作，這份 case study 就是你的入場券。

恭喜你走到這裡。你現在有了量化效能、提出 compiler 優化的能力。
