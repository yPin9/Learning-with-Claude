# 練習 B — 用 llvm-mca 分析一段 hot loop

> **目標**：整合 Part 2-3（微架構 + profiling 工具，特別是 llvm-mca）的知識，挑一個真實的 hot loop，用 llvm-mca 深入分析它的 pipeline 行為、找出指令層瓶頸（resource 還是 dependency）、提出 compiler/source 改進並驗證。完成後你能做「指令層的效能分析」——這是 compiler 工作的核心技能，也是從 hot loop 提出具體優化的能力。

## 背景與動機

效能優化到最後常常落到「指令層」——一段 hot loop，為什麼它的 throughput 是這樣？瓶頸是某個執行單元（resource）還是相依鏈（dependency）？怎麼改能更快？這需要**指令層的分析**——而 llvm-mca（Ch 8）正是這個的工具（靜態分析一段組合語言的 pipeline 行為）。

這個練習讓你挑一個 hot loop，用 llvm-mca 深入分析、找出瓶頸、提出改進、驗證。這正是 compiler 工程師分析「compiler 產生的 code 好不好」的核心工作——你看一段組合語言，用 llvm-mca 理解它的 pipeline 行為，判斷 compiler 能不能產生更好的 code。完成這個練習，你建立了「指令層效能分析」的能力，這是 perf_bench 對 compiler 工作最深入的技能。

## 任務規格

挑一個 hot loop，用 llvm-mca 做完整分析：

| 部分 | 內容 | 對應章節 |
|---|---|---|
| 找 hot loop | profile 找出（或選一個經典的）| Ch 7 |
| llvm-mca 分析 | throughput、IPC、resource pressure | Ch 8 |
| 瓶頸判斷 | resource bound 還 dependency bound | Ch 8 |
| timeline 分析 | 看指令在 pipeline 怎麼流動 | Ch 8 |
| 提改進 | source/compiler 層的改進 | Ch 8/14 |
| 驗證 | 改進後 llvm-mca + perf 確認 | Ch 4/6 |

**核心要求**：用 llvm-mca 找出「指令層的瓶頸」（不是猜），提出針對性的改進，並驗證（llvm-mca 的 throughput 改善 + perf 的真實效能）。

## 如果你卡住了

1. 挑一個簡單但有瓶頸的 hot loop（如 reduction、dot product、有相依的迴圈）
2. 用 `clang -O2 -S` 產生組合語言，找出 hot loop 那段
3. llvm-mca 分析：先看 Block RThroughput（throughput）和 IPC
4. `-bottleneck-analysis` 看瓶頸（resource 還 dependency）
5. `-timeline` 看哪條指令「等待」（相依或資源衝突）
6. 改進：dependency bound → 打破相依鏈（多累加器）；resource bound → 換指令
7. 驗證：改進後 llvm-mca 看 throughput 降了嗎 + perf 看真實效能

## 完整參考解答

**自己分析一遍！** 親手用 llvm-mca 找瓶頸才學到指令層分析。

<details>
<summary>完整分析案例：dot product</summary>

```bash
cd ~/perflab
# 一個經典的 hot loop：dot product（有相依瓶頸）
cat > dotprod.c <<'EOF'
float dot_product(float * restrict a, float * restrict b, int n) {
    float sum = 0;
    for (int i = 0; i < n; i++) {
        sum += a[i] * b[i];      // 相依：sum 累加（每次等前一次）
    }
    return sum;
}
EOF

# 產生組合語言
clang -O2 -march=native -S dotprod.c -o dotprod.s

# ===== Step 1-2: llvm-mca 分析 =====
# 找出 hot loop 那段（在 .s 裡），用 llvm-mca 分析
llvm-mca -mcpu=native dotprod.s 2>/dev/null | head -20
# Iterations: 100
# Total Cycles: ~400
# Block RThroughput: 4.0          ← 每 iteration 約 4 cycle（慢）
# IPC: ~1.0                        ← 沒餵飽（dispatch width 更高）

# ===== Step 3: 瓶頸分析 =====
llvm-mca -mcpu=native -bottleneck-analysis dotprod.s 2>/dev/null | grep -A5 'bottleneck'
# 顯示瓶頸：可能是「dependency」（sum 的累加鏈）
# 浮點加法延遲 ~4 cycle，每次累加要等前一次 → 4 cycle/iteration

# ===== Step 4: timeline 看指令流動 =====
llvm-mca -mcpu=native -timeline dotprod.s 2>/dev/null | head -20
# 看到 fadd（浮點加法）指令「等待」前一次的結果（相依）
# [0,2]  D===eeeeER  fadd   ← 等了（===）才能執行（相依於前一次 sum）

# ===== Step 5: 改進——打破相依鏈（多累加器）=====
cat > dotprod_opt.c <<'EOF'
float dot_product(float * restrict a, float * restrict b, int n) {
    float s0=0, s1=0, s2=0, s3=0;
    int i;
    for (i = 0; i + 3 < n; i += 4) {
        s0 += a[i]   * b[i];      // 4 個獨立的累加鏈
        s1 += a[i+1] * b[i+1];
        s2 += a[i+2] * b[i+2];
        s3 += a[i+3] * b[i+3];
    }
    float sum = s0 + s1 + s2 + s3;
    for (; i < n; i++) sum += a[i] * b[i];   // 處理剩餘
    return sum;
}
EOF
clang -O2 -march=native -ffast-math -S dotprod_opt.c -o dotprod_opt.s

# 驗證：llvm-mca 看 throughput 改善
llvm-mca -mcpu=native dotprod_opt.s 2>/dev/null | grep 'Block RThroughput'
# Block RThroughput: 1.X         ← 從 4.0 降到 ~1.X（4 個獨立鏈能平行）

# 驗證：perf 看真實效能
# （需要完整的 main 跑大 n，用 hyperfine 比較）

# 提出的建議：
echo "
分析結論：
- dot_product 的瓶頸是 dependency（sum 累加鏈），每次 fadd 等前一次
- Block RThroughput 4.0（浮點加法延遲），IPC 1.0（沒餵飽）
- 改進：打破相依鏈（4 個累加器），throughput 從 4.0 降到 1.X

compiler 建議：
- compiler 在 -ffast-math 下應該能自動做這個（loop unroll + 多累加器 reduction）
- 如果沒做，建議檢查 vectorizer/reduction 的處理
- 對 RISC-V 的 RVV，這個 reduction 可以用向量化（vfredsum）
- 資料支撐：llvm-mca 的 throughput 分析 + perf 的真實效能驗證
"
```

**分析說明**：

- **llvm-mca 找瓶頸**：Block RThroughput 4.0 + bottleneck 是 dependency → 相依鏈是瓶頸（不是猜，是 llvm-mca 顯示的）
- **timeline 確認**：看到 fadd 等待前一次的結果（相依）
- **改進對應瓶頸**：dependency bound → 打破相依鏈（多累加器，Ch 8）
- **驗證**：llvm-mca 的 throughput 從 4.0 降到 1.X（指令層改善）+ perf 看真實效能
- **compiler 建議**：用資料支撐（llvm-mca/perf），具體（多累加器 reduction），對 RISC-V 相關（RVV 的 vfredsum）
- **核心**：用 llvm-mca 在指令層找瓶頸、針對性改進、驗證——指令層的效能分析

</details>

## 測試用案例

| 步驟 | 工具 | 產出 |
|---|---|---|
| 找瓶頸 | llvm-mca -bottleneck-analysis | dependency/resource |
| 看流動 | llvm-mca -timeline | 哪條指令等待 |
| 改進 | 打破相依/換指令 | 新的 code |
| 驗證 | llvm-mca throughput + perf | 改善確認 |

## 延伸挑戰（加分）

- **挑戰一**：比較微架構——用不同 `-mcpu`（skylake vs sifive-u74）分析同段 code，看瓶頸在不同 CPU 的差別

- **挑戰二**：resource bound 案例——找一個 resource bound 的 loop（如太多除法），用 llvm-mca 找出哪個 port 是瓶頸，改進（換指令）

- **挑戰三**：向量化——對能向量化的 loop，比較純量 vs 向量化的 llvm-mca 分析（throughput 的差別）

- **挑戰四**：llvm-mca vs perf——對同段 code，比較 llvm-mca 的預測和 perf 的真實測量，理解差距（理想模型 vs 真實）

- **挑戰五**：寫 compiler proposal——把分析寫成正式的 compiler optimization proposal（瓶頸、改進、compiler 該怎麼做、資料支撐）

## 自我檢核

- [ ] 會用 llvm-mca 分析 hot loop 的 throughput 和瓶頸
- [ ] 能判斷瓶頸是 resource 還 dependency bound
- [ ] 會用 timeline 看指令的 pipeline 流動
- [ ] 能針對瓶頸提出改進（打破相依/換指令）並驗證
- [ ] 能寫出有資料支撐的 compiler 優化建議

這個練習訓練了「指令層效能分析」的能力。接下來 Final Project——完整的 performance case study + compiler optimization proposal，整合全課的能力。

→ [Final Project：Performance case study + compiler optimization proposal](./final-project-perf-case-study.md)
