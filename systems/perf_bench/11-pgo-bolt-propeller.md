# Ch 11 — PGO / BOLT / Propeller：profile-guided 全家族

> **目標**：理解 profile-guided optimization（PGO）的三種流派——PGO（compiler-level，用 profile 指導編譯）、BOLT（binary-level，優化已編譯的 binary 的 layout）、Propeller（linker-level）。理解它們怎麼用「程式實際執行的 profile」指導優化（哪些分支常走、哪些函式常呼叫、code layout 怎麼排）、各自的階段和取捨。這是 production release 常用的效能 booster，也是現代 compiler 優化最有效的技術之一。

> **環境**：Linux，gcc/clang（PGO 內建）、llvm-bolt（BOLT）。

## 為什麼 profile-guided 優化這麼有效？

普通的 compiler 優化（Ch 10）只能**猜測**程式的行為——哪個分支常走？哪個函式常呼叫？compiler 不知道（它只看靜態的 code），只能用啟發式（heuristic）猜。猜錯的代價：把不常走的分支排在前面（多餘的跳轉）、把常一起呼叫的函式放遠（I-cache miss）、inline 錯的函式。

**profile-guided optimization（PGO）** 解決這個——它先**實際跑程式收集 profile**（哪個分支常走、哪個函式常呼叫、執行的熱路徑），再用這個 profile **指導優化**（把常走的分支排前面、把常一起的函式放近、inline 熱函式）。因為有了「真實執行行為」的資訊，優化更精準。PGO 是現代 compiler 優化最有效的技術之一——對真實的大型程式（瀏覽器、資料庫、編譯器自己）常有 5-20% 的提升。這章講 PGO 全家族（compiler/binary/linker 三個層次）和它們的原理。

## 先建立直覺:用真實行為指導優化

```
PGO 的核心：用「真實執行 profile」指導優化

  普通優化：compiler 「猜」程式行為
    if (rare_condition) { ... } else { ... }
    → compiler 不知道哪邊常走，隨便排
        │
  PGO：先「跑程式」收集真實行為
    profile：rare_condition 99% 是 false（else 常走）
    → 優化：把 else 排前面（fall-through，少跳轉）
          把 if 的 code 移到「冷區」（不污染 I-cache）
        │
  PGO 的三步：
    1. Instrument（插樁編譯）：產生會「記錄行為」的版本
    2. Profile（跑）：用代表性的 workload 跑，收集 profile
    3. Optimize（用 profile 重新編譯）：用 profile 指導優化
        │
  PGO 能優化什麼（用了 profile 才能做的）：
    - 分支排序（常走的排前面）
    - 函式 layout（常一起呼叫的放近，減 I-cache miss）
    - inline 決策（inline 熱函式，不 inline 冷的）
    - 冷熱分離（冷 code 放一邊，熱 code 集中）
        │
  → PGO = 用真實行為（profile）讓優化精準
    比「猜」（heuristic）更準 → 5-20% 提升
```

關鍵心智：PGO 用「**真實執行的 profile**」指導優化——先實際跑程式收集行為（哪個分支常走、哪個函式常呼叫），再用這資訊精準優化（分支排序、函式 layout、inline 決策、冷熱分離）。三步：instrument（插樁）→ profile（跑收集）→ optimize（用 profile 重編）。比 compiler「猜」（heuristic）更準，對真實大型程式有 5-20% 提升。

> PGO 用到 Ch 5 的微架構概念（I-cache、分支預測——PGO 改善這些）和 Ch 7 的 profiling（收集 profile）。

## PGO:compiler-level

```bash
cd ~/perflab
# === GCC PGO（三步）===
# 1. Instrument：插樁編譯（產生會記錄行為的版本）
gcc -O2 -fprofile-generate demo.c -o demo_instr

# 2. Profile：用代表性的 workload 跑（收集 profile 到 .gcda 檔）
./demo_instr            # 跑一次（或多次用不同的代表性輸入）
# 產生 demo.gcda（profile 資料）

# 3. Optimize：用 profile 重新編譯
gcc -O2 -fprofile-use demo.c -o demo_pgo
# compiler 讀 .gcda 的 profile，做 profile-guided 優化

# 比較 PGO vs 普通（驗證提升）
hyperfine './demo_O2' './demo_pgo'
# './demo_pgo' ran 1.1X faster   ← PGO 提升（對有分支/呼叫的程式更明顯）

# === Clang PGO（類似，用 llvm-profdata）===
# clang -O2 -fprofile-instr-generate demo.c -o demo_instr
# ./demo_instr  → 產生 default.profraw
# llvm-profdata merge default.profraw -o demo.profdata
# clang -O2 -fprofile-instr-use=demo.profdata demo.c -o demo_pgo
```

```
PGO 的關鍵：profile 的「代表性」

  PGO 的效果取決於「profile 的 workload 代表真實使用嗎」：
    用代表性的 workload 收集 profile → 優化精準（真實提升）
    用不代表性的 workload → 優化錯方向（可能更慢！）
        │
  例：用「全是 cache hit」的 workload 收集 profile
    → PGO 以為某分支總走某邊（但真實情況不同）
    → 優化錯了 → 真實使用反而更慢
        │
  → PGO 的 profile 要用「代表真實使用的 workload」
    這是 PGO 成敗的關鍵（profile 不代表 = 優化錯方向）
    通常用「典型的真實 workload」或「多種 workload 的混合」
```

> **PGO 的成敗取決於「profile 的代表性」——用代表真實使用的 workload 收集 profile，否則優化錯方向反而更慢**。GCC PGO 的三步：**instrument**（`-fprofile-generate`，產生會記錄行為的版本）→ **profile**（跑，收集 .gcda 資料）→ **optimize**（`-fprofile-use`，用 profile 重編）。clang 類似（用 llvm-profdata）。PGO 能提升 5-20%（對有分支/函式呼叫的真實程式）。但**關鍵是 profile 的「代表性」**——PGO 用 profile 指導優化，所以 **profile 要代表「真實使用」**：用代表性的 workload 收集 → 優化精準（真實提升）；用**不代表性**的 workload → 優化**錯方向**（PGO 以為某分支總走某邊、某函式常呼叫，但真實情況不同）→ **真實使用反而可能更慢**！這是 PGO 的成敗關鍵——**profile 不代表真實 = 優化錯方向 = 適得其反**。所以 PGO 的 profile 要用「典型的真實 workload」或「多種 workload 的混合」（涵蓋真實的使用模式）。例子：如果你的程式有「快路徑」和「慢路徑」，profile 要包含真實的比例（如 90% 快路徑），PGO 才能正確地優化快路徑；如果 profile 只跑慢路徑，PGO 會優化錯地方。這對 compiler 工作很重要——部署 PGO 要選對 profiling workload（代表真實使用），否則 PGO 不只沒幫助甚至有害。這也是 PGO 比普通優化「更強但更需要謹慎」的地方——它用真實資訊（強），但資訊不對就錯（要選對 workload）。

## BOLT:binary-level

```
BOLT（Binary Optimization and Layout Tool，Meta 開發）：

  和 PGO 的差別：BOLT 優化「已編譯的 binary」（不重編譯）
        │
  BOLT 的流程：
    1. 對已編譯的 binary，用 perf 收集 profile（執行行為）
    2. BOLT 讀 binary + profile
    3. 重新排列 binary 的 code layout（不改邏輯，改佈局）
       - 把熱函式/熱 code 集中放（減少 I-cache/iTLB miss）
       - 冷 code 移到一邊
       - 優化函式排序、basic block 排序
    4. 輸出優化後的 binary
        │
  BOLT 的價值：
    優化「code layout」（PGO 也做但 BOLT 在 binary 層更徹底）
    對「I-cache/iTLB bound」的大型程式效果顯著（5-15%）
    （大型程式的 code 大，I-cache miss 是瓶頸 → layout 優化有效）
        │
  → BOLT 在 binary 層優化 layout（PGO 之後再做）
    PGO + BOLT 常結合（PGO 編譯時優化 + BOLT binary 層 layout）
    Meta 用 BOLT 優化它的大型服務（顯著提升）
```

> **BOLT 在「已編譯的 binary」層優化 code layout（不重編譯）——對 I-cache bound 的大型程式效果顯著，常和 PGO 結合**。**BOLT**（Meta 開發）和 PGO 的差別是它優化「**已編譯的 binary**」（不重新編譯，直接動 binary）。流程：對 binary 用 **perf 收集 profile**（執行行為）→ BOLT 讀 binary + profile → **重新排列 code layout**（不改邏輯，改佈局——把熱函式/熱 code 集中、冷 code 移開、優化函式和 basic block 的排序）→ 輸出優化後的 binary。BOLT 的核心是**優化 code layout**——把常一起執行的 code 放近（在同一個 I-cache line/page），減少 **I-cache miss 和 iTLB miss**（Ch 5——指令也要進 cache 和 TLB）。這對「**I-cache bound 的大型程式**」效果顯著（5-15%）——大型程式（瀏覽器、資料庫、編譯器）的 code 很大，I-cache 塞不下，I-cache miss 是瓶頸，BOLT 的 layout 優化（把熱 code 集中）大幅改善。**PGO + BOLT 常結合**——PGO 在編譯時做優化（分支/inline）、BOLT 在 binary 層做更徹底的 layout 優化。Meta 用 BOLT 優化它的大型服務（顯著提升，這是 BOLT 誕生的動機——Meta 的服務太大，I-cache 是瓶頸）。對 compiler/效能工作，BOLT 是「榨出最後幾 % 」的工具（特別是大型 I-cache bound 程式）。理解 BOLT，你知道「code layout」是效能的一個維度（不只是「執行什麼指令」，還有「指令放在哪」影響 I-cache），以及怎麼用 profile 優化它。BOLT 是 binary-level 的 profile-guided 優化——它和 PGO（compiler-level）互補，一個在編譯時、一個在 binary 層。

## 三個層次的對比

```
PGO 全家族的三個層次：

  PGO（compiler-level）：
    在「編譯時」用 profile 優化
    能做：分支排序、inline 決策、向量化決策、layout（部分）
    需要：重新編譯（要原始碼）
        │
  BOLT（binary-level）：
    在「已編譯的 binary」優化 layout
    能做：code layout（函式/basic block 排序，I-cache 優化）
    需要：binary + profile（不用重編譯，但要 relocation 資訊）
        │
  Propeller（linker-level）：
    在「連結時」用 profile 優化 layout（Google 開發）
    類似 BOLT 但整合進 build（linker 階段做 layout）
    能做：類似 BOLT 的 layout，但整合進編譯流程
        │
  → 三個層次各有角色：
    PGO：編譯時的全面優化（分支/inline/layout）
    BOLT：binary 層的徹底 layout 優化（最強的 layout）
    Propeller：整合進 build 的 layout 優化
        │
  實務：PGO 為主（最全面）+ BOLT/Propeller 補 layout（大型程式）
    一起用能榨出最多（PGO + BOLT 常見組合）
```

> **PGO（編譯時，全面）+ BOLT（binary 層，最強 layout）+ Propeller（linker 層，整合進 build）——三個層次各有角色，常結合使用**。profile-guided 優化有三個層次：**PGO**（compiler-level，編譯時用 profile 做**全面優化**——分支排序、inline 決策、向量化、部分 layout，需要重編譯）；**BOLT**（binary-level，在已編譯的 binary 做**最徹底的 layout 優化**——函式/basic block 排序，I-cache 優化，不用重編譯但要 relocation 資訊）；**Propeller**（linker-level，Google 開發，在**連結時**做 layout 優化，類似 BOLT 但**整合進 build 流程**——比 BOLT 更易整合進現有的編譯系統）。三者各有角色——**PGO 最全面**（編譯時的各種優化）、**BOLT 的 layout 最強**（binary 層最徹底）、**Propeller 最易整合**（linker 階段）。實務上 **PGO 為主 + BOLT/Propeller 補 layout**（特別是大型 I-cache bound 程式）——PGO 做編譯時的全面優化、BOLT/Propeller 做 binary/link 層的 layout 優化，一起用榨出最多（PGO + BOLT 是常見的組合，如 Meta/Google 的大型服務）。對 compiler 工作，理解這三個層次讓你知道「profile-guided 優化可以在不同階段做」（編譯/連結/binary）、各自的能力和取捨、以及怎麼組合。這是現代 production 效能優化的前沿——大型科技公司（Meta/Google）用這些技術榨出大型服務的最後幾 %（在它們的規模，幾 % 是巨大的資源節省）。這也是 SiFive 等 compiler 廠商要支援的——讓 RISC-V 的 compiler 工具鏈支援 PGO/BOLT 等 profile-guided 優化，幫客戶榨出效能。理解 PGO 全家族，你掌握了現代 compiler 優化最有效的技術之一。

## 故意弄壞:PGO 對分支密集程式的效果

```bash
cd ~/perflab
# PGO 對「分支密集且分支有明顯傾向」的程式效果最明顯
cat > branchy.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
int process(int x) {
    // 多個分支，但有明顯傾向（99% 走某邊）
    if (x % 100 == 0) {        // rare (1%)
        return x * x % 7;       // 冷路徑
    } else {                    // common (99%)
        return x + 1;           // 熱路徑
    }
}
int main() {
    long total = 0;
    for (long i = 0; i < 100000000L; i++) {
        total += process(i);
    }
    printf("%ld\n", total);
    return 0;
}
EOF

# 普通 -O2
gcc -O2 branchy.c -o branchy_O2

# PGO（三步）
gcc -O2 -fprofile-generate branchy.c -o branchy_instr
./branchy_instr > /dev/null        # 收集 profile（真實的分支行為）
gcc -O2 -fprofile-use branchy.c -o branchy_pgo

# 比較
hyperfine './branchy_O2' './branchy_pgo'
# './branchy_pgo' ran 1.1-1.2X faster   ← PGO 提升！
# → PGO 知道「else 99% 走」→ 優化：
#   - 把熱路徑（else）排前面（fall-through，少跳轉）
#   - 把冷路徑（if）的 code 移到冷區（不污染 I-cache）
#   - 分支預測 hint（告訴 CPU 哪邊常走）

# 看 branch-misses 的改善（PGO 改善分支預測）
echo "=== O2 ==="; perf stat -e branch-misses ./branchy_O2 2>&1 | grep branch-misses
echo "=== PGO ==="; perf stat -e branch-misses ./branchy_pgo 2>&1 | grep branch-misses
# PGO 版的 branch-misses 可能更少（layout 優化讓分支更可預測）
```

> **PGO 對「分支密集且有明顯傾向」的程式效果最明顯——它用 profile 知道「哪邊常走」，優化分支 layout 和預測**。這個例子展示 PGO 最擅長的場景——**分支密集且分支有明顯傾向**的程式。`process` 函式有分支，但 99% 走 else（熱路徑）、1% 走 if（冷路徑）。普通 -O2 **不知道**這個傾向（只能猜），PGO **收集 profile 知道「else 99% 走」**，於是優化：(1) **把熱路徑（else）排前面**（fall-through，CPU 順著執行不用跳轉）；(2) **把冷路徑（if）的 code 移到冷區**（不和熱 code 搶 I-cache）；(3) **分支預測 hint**（告訴 CPU 哪邊常走，配合硬體分支預測，Ch 5）。結果 PGO 版快 10-20%（perf 顯示 branch-misses 可能更少——layout 優化讓分支更可預測）。這展示了 PGO 的核心價值——**用真實行為（profile）做普通優化做不到的精準優化**（普通優化不知道分支傾向，PGO 知道）。PGO 對這類程式（有明顯熱/冷路徑、分支密集、大型有複雜呼叫關係的）效果最好；對「沒有明顯傾向」的程式（如純計算、分支均勻）效果有限（沒有 profile 能利用的傾向）。這對 compiler 工作的啟示——PGO 是「用真實資訊精準優化」的強大技術，但要選對 workload（有可利用的行為傾向 + 代表性的 profile）。Part 4 的 PGO 讓你理解現代 compiler 優化最有效的技術——用 profile 指導，比靜態的 heuristic 精準。這也是 SiFive 等 compiler 工作要支援的（讓 RISC-V 工具鏈支援 PGO，幫客戶榨效能）。

## 動手練習

1. PGO 三步：對一個分支密集的程式做 PGO（instrument→profile→optimize），用 hyperfine 看提升

2. profile 代表性：用不同的 workload 收集 profile，看對最終效能的影響（代表性的重要）

3. branch-misses：用 perf 比較 PGO 前後的 branch-misses，理解 PGO 怎麼改善分支

4. 理解三層次：對照 PGO/BOLT/Propeller 的層次和能力，知道各自的角色

5. 跑「故意弄壞」：對 branchy.c 做 PGO，理解「用 profile 知道分支傾向」的優化

## 本章重點整理

- PGO 用「真實執行 profile」指導優化（分支排序、inline、layout、冷熱分離），比 compiler 猜更準（5-20%）
- PGO 三步：instrument（插樁）→ profile（跑收集）→ optimize（用 profile 重編）；GCC -fprofile-generate/-use
- profile 的「代表性」是 PGO 成敗關鍵——不代表真實的 workload 會讓優化錯方向（反而更慢）
- BOLT（binary 層優化 code layout，I-cache 優化）、Propeller（linker 層）；對 I-cache bound 大型程式顯著
- 三層次各有角色：PGO（編譯時全面）+ BOLT（binary 層最強 layout）+ Propeller（整合 build），常結合

## 自我檢核

- [ ] 理解 PGO 怎麼用 profile 指導優化，為什麼比 compiler 猜更準
- [ ] 會做 PGO 的三步（instrument/profile/optimize）
- [ ] 知道 profile 代表性的重要（不代表會優化錯方向）
- [ ] 知道 BOLT/Propeller 在 binary/linker 層優化 layout
- [ ] 理解三個層次的角色和怎麼結合

## 延伸閱讀

### 文章

- **[PGO 詳解](https://johnnysswlab.com/the-price-of-the-pgo/) / [GCC PGO 文件](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html)**
  - **這篇說什麼**：PGO 的原理、效果、怎麼用
  - **為什麼值得讀**：本章 PGO 的權威

- **[BOLT paper](https://research.facebook.com/publications/bolt-a-practical-binary-optimizer-for-data-centers-and-beyond/)** — Meta（CGO 2019）
  - **核心貢獻**：BOLT 的設計（binary 層 layout 優化）
  - **為什麼值得讀**：BOLT 的權威，理解 binary 層優化

### 工具

- **[llvm-bolt](https://github.com/llvm/llvm-project/tree/main/bolt)** + **[Propeller](https://github.com/google/llvm-propeller)**
  - **為什麼值得讀**：BOLT 和 Propeller 的實作和用法

下一章看 LTO（Link-Time Optimization）——跨檔案的優化。理解它怎麼運作、為什麼有用、以及怎麼量測它的效果（破除「LTO 總是有用」的迷思）。

→ [Ch 12 LTO 效果量測](./12-lto-measurement.md)
