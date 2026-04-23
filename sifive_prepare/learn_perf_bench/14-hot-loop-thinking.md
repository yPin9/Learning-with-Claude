# Ch 14 — 從 hot loop 倒推「該加什麼 optimization」

> 目標：本課終章。把前面 13 章的工具整合成一個 thinking framework。給你一個 hot loop，如何系統化判斷「compiler 該加什麼 optimization」。這是 SiFive job spec 第二條 responsibility 的直接對應。

## 情境：典型工作流

Benchmarking team 告訴你：

> "我們的 SPEC mcf 比競爭對手慢 15%。"

你的 job：

1. Profile → 找 hot function
2. 分析 hot function → 找 bottleneck
3. **Propose compiler-level optimization** ← 這是本章重點
4. Implement + validate

本章講 step 3 的思考框架。

## 整體思考框架

```
Hot loop
   │
   ├─► What's the bottleneck?
   │    - IPC 低 → memory, branch, 或 latency
   │    - IPC 高但不夠快 → 需要 parallelism (vector / SMT)
   │    - Cache miss 高 → layout / prefetch
   │    - Branch miss 高 → cmov / Zicond
   │
   ├─► Can compiler help?
   │    - 是 generic 優化 → 改 LLVM pass
   │    - 是 target specific → 改 RISC-V backend
   │    - 是 pattern match → 加 DAGCombine / InstCombine rule
   │    - 是 scheduling → 改 sched model
   │
   └─► How to verify?
        - Micro: check asm diff
        - Macro: regression test + benchmark
```

## 常見 bottleneck + compiler opt

### Bottleneck 1：整數運算多、IPC 可再提升

**Symptom**：hot loop 用大量基本 integer 操作。

**Possible compiler optimization**：

- Combine pattern (`a + b*3` → `sh1add`)
- Pattern matching 補缺（Zbb 的 `rev8` for byte swap）
- 更好的 scheduling

**Action**：

1. 看 asm 找出 pattern
2. 看 LLVM backend 有沒有對應 `DAGCombine` rule
3. 缺就加

**對應 code**：`RISCVISelLowering.cpp` 的 `performADDCombine` 等。

### Bottleneck 2：Memory bound

**Symptom**：IPC 低、L1/L2/LLC miss 高。

**Possible opt**：

- Loop tiling/blocking（改 memory access pattern）
- Prefetch intrinsic 插入
- 數據結構重排

**Action**：

1. 考慮 compiler 是否能 auto-tile（不太行，多半靠 programmer）
2. Compiler 加 prefetch pass (GCC `-fprefetch-loop-arrays`)
3. 判斷是 data layout 問題 → 跟客戶討論 source 改

**難度**：Memory bound 多半 compiler 幫不上太多。Source code 改動或 hardware 優化（bigger cache）是主道。

### Bottleneck 3：Branch mispredict 高

**Symptom**：branch-miss rate > 5%、IPC 低。

**Possible opt**：

- Zicond (conditional move)
- Branch-free pattern
- Profile-guided branch layout (PGO)

**Action**：

1. 看 hot branch 是哪個
2. 是 data-dependent random → Zicond / czero
3. 是 predictable → PGO 重 layout 就夠

**Compiler modification**：DAGCombiner 加「transform to czero」的 pattern。

### Bottleneck 4：Vector 沒 vectorize

**Symptom**：loop 是 vectorizable pattern 但產 scalar code。

**Possible opt**：

- 修 auto-vectorizer 的 heuristic
- 加 cost model 讓它願意 vectorize
- 手動 intrinsic（workaround）

**Action**：

1. 用 `-Rpass-missed=loop-vectorize` 看 fail 原因
2. 若是 cost model 太保守 → tune
3. 若是 legality 問題 → legalizer fix

**典型 code**：`llvm/lib/Transforms/Vectorize/LoopVectorize.cpp`、`RISCVTargetTransformInfo.cpp`。

### Bottleneck 5：RVV code generate 不佳

**Symptom**：auto-vec 有走、但 output asm 多 vsetvli 或 register pressure 大。

**Possible opt**：

- VSETVLI insertion 優化 (`RISCVInsertVSETVLI.cpp`)
- LMUL 選擇 heuristic
- Fractional LMUL 處理

**Action**：

1. 看 `-stop-after=riscv-insert-vsetvli` 的 MIR
2. 對 redundant vsetvli 加 DFA rule
3. Report LLVM 或 upstream fix

### Bottleneck 6：Function call overhead

**Symptom**：small function call 多。

**Possible opt**：

- Inline threshold 調整
- Tail call optimization
- Function outlining (反向)

**Action**：

1. PGO 可以 auto-adjust
2. Compiler flag `-finline-limit=N`
3. Source 加 `always_inline` / `noinline` hint

### Bottleneck 7：Spill / reload 多

**Symptom**：asm 大量 `lw/sw` 對 `sp` 的 access。

**Possible opt**：

- Register allocator 改進
- Reduce register pressure (break long live range)
- Callee-saved 選擇調整

**Action**：

1. 看 `greedy` RA 的 heuristic
2. 試看 scheduler pre-RA 是否 increase pressure
3. Custom extension 可能需要更多 physical reg

## 選擇 target layer

從簡單到複雜：

```
1. Compiler flag (-O3 / -flto / -fvectorize)           # 最簡
2. Pragma / attribute (__restrict, always_inline)
3. DAGCombine / InstCombine rule (加一條 pattern)
4. Target-specific pass 改 (e.g., VSETVLI pass)
5. Pass order / pipeline 改 (最難)                      # 最複雜
```

**先試簡單的**。加一條 pattern 解掉 bottleneck → 省幾週工作。

## 典型 case study：如何省一個 `slli + add`

**發現**：SPEC benchmark 裡某 hot loop 產：

```asm
slli t0, a2, 2     ; i * 4
add  t0, t0, a1    ; &arr[i]
lw   t0, 0(t0)
```

`(i<<2) + base` pattern → Zba 有 `sh2add`！

**Check**：`-march` 有開 Zba 嗎？

```bash
clang --target=riscv64 -march=rv64gc_zba ...
```

開了。為什麼 compiler 沒用？

**Grep LLVM source**：

```bash
grep "sh2add" llvm/lib/Target/RISCV/*.td
```

看 `RISCVInstrInfoZba.td` 有 pattern。為什麼沒 match？

**Debug with `-debug-only=isel`**：看 pattern match 順序。

可能原因：pattern complexity 不夠、DAGCombiner 先 canonicalize 成別的 form → 必須再加一條 combine rule 或 adjust pattern complexity。

**Fix**：加 DAGCombine 認這個新 form、轉成 Zba canonical form。寫 patch、test、提 PR。

**This is compiler optimization work in a nutshell**。

## 手寫 pattern 是 anti-pattern

**絕不**：在 source 寫 inline asm 讓 benchmark 跑快。

- 不 portable
- 不 generic
- 只這個 benchmark 受益

**應該**：找出為何 compiler 漏此 pattern、修 compiler、**所有類似 code 受益**。

這是 compiler 工程師 vs application 工程師的差異。

## 客戶 report 的解讀

客戶常 report：

> "X benchmark 比 ARM 慢 20%"

務實步驟：

1. **Reproduce**：在你的 machine 能 reproduce 嗎？
2. **Profile**：hot function 是啥？
3. **比對 ARM asm**：ARM compiler 怎麼產？我方缺什麼？
4. **Fix gap**
5. **Verify**：修復後數字多少？
6. **Regression**：其他 benchmark 沒退步？
7. **Upstream**

### 不該做：

- 對 specific benchmark hack（SPEC-spec code）
- 關 optimization 去 bench（僞數字）
- 只 fix 不 regression test

## 跟硬體團隊的對話

某些瓶頸 compiler 解不了，需要硬體：

- **Branch predictor 太弱** → 硬體升級
- **Cache too small** → 物理架構問題
- **某 extension 不支援** → 下一代

此時你的 job：**清楚 articulate「這是硬體問題，不是 compiler 的錯」**。用 profiling data 說服 management。

## Case study: Coremark on SiFive U74

假設看到：

- Coremark 4500 at 1.2 GHz = 3.75 Coremark/MHz
- 比 Cortex-M7 (~5.0) 差

**Profile**：list processing (struct traversal) 佔 50%+。

**Look at asm**：很多 `lw + addi` pattern、沒用 Zba 或 Zbb。

**Check**：U74 沒有 Zba/Zbb (only has `rv64gc`)。

**Conclusion**：這是硬體 limitation。next-gen U74b 加 Zba → estimated Coremark 5000+。

**Action**：跟硬體 team 確認 roadmap、對客戶 communicate 時間線。

## 最高境界：預測未來瓶頸

Senior engineer 不只 react，還 predict：

- 新 AI workload 常見 matrix operation → RVV + `sf.vqmacc` 的重要性
- RVV binary 日益常見 → ABI 需要 consider VLEN
- Chiplet 帶來 cross-die communication → compiler 可能需要新 primitive

這是 staff+ compiler engineer 做的事。**先學戰術、再學戰略**。

## 總結：一張思考卡

```
Hot Loop 出現?
│
├─► IPC 多少?
│    < 0.5: Memory / Branch 看哪個
│    0.5-2: Instruction pattern / scheduling
│    > 2: 可能已近 optimal，再提升困難
│
├─► 我能改哪一層?
│    - Pattern: DAGCombine / InstCombine
│    - Scheduling: sched model
│    - Legalize: legalizer info
│    - Vector: Loop / SLP vectorizer
│
├─► 預期 gain 多少?
│    Amdahl's law: hot function 佔比決定 ceiling
│
└─► Regression risk?
     - 其他 benchmark 跑一下
     - 不同 -mcpu 試
```

## 自我檢核

- [ ] 我能讀 perf output 判斷 bottleneck 類型
- [ ] 我能給 7 種 bottleneck 都 propose compiler-level 解法
- [ ] 我能估算 optimization 的 expected gain
- [ ] 我能跟硬體團隊討論「這問題誰該解」
- [ ] 我能在 SiFive 面試回答「客戶 report perf regression 怎麼處理」

## 動手練習

1. 選一個 open-source benchmark（e.g., x264 sample），perf 找 hot function。
2. 看 asm，找 3 個你覺得「可能有更好 pattern」的地方。
3. 跟 ARM NEON / x86 SSE 對照，看別人的 compiler 怎麼產。
4. 寫一份 mock proposal："我建議對 LLVM RISC-V 加這個 optimization 因為 ..."（即使 fake 也練習論述）。
5. 讀 LLVM RISC-V recent commit history，找 3 個 "add DAGCombine for xxx" 類 commit，看 real 優化 pattern。

---

**課程至此結束**。Part 4 的 5 章涵蓋 benchmark → profile → bottleneck → compiler improvement 的完整 workflow。

→ [練習 A：寫一份 Coremark 效能報告](./practice-a-coremark-report.md)
→ [練習 B：用 llvm-mca 分析一段 hot loop](./practice-b-llvm-mca-case.md)
→ [Final Project：Performance case study](./final-project-perf-case-study.md)
