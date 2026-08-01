# Ch 23 — CPI 分析與 pipeline 效能建模

> **目標**：把「效能變好了」從感覺變成可量化的數字。你會建立 CPI（cycles per instruction，每指令週期數）的分解模型：理想 CPI = 1，再把 stall（load-use bubble）、flush（branch mispredict）各自的貢獻加上去。然後在 testbench 裡加 performance counter（數 cycle / instret / stall / flush），跑一段 benchmark，算出實測 CPI，並對照「有無 forwarding」「有無分支預測」三種配置的差異。全程真跑量測，數字不憑空編。
> **環境**：WSL + gcc（cycle-accurate 記帳模型）。所有 CPI 皆真跑量測。

## 為什麼需要 CPI 分析？

前面幾章我們一直說「forwarding 省了 stall」「分支預測省了 flush」，但省了**多少**？值不值得那些硬體？沒有數字，這些都是口號。CPU 效能工程的第一課就是：**把效能拆成可歸因的成分，量出每個成分的貢獻，才知道該優化哪裡**。

CPI 是連接微架構和實際效能的核心指標。它回答一個問題：**你的 pipeline 平均每執行一條指令，花了幾個 cycle？** 理想的 5 級 pipeline 穩態下每 cycle 完成一條，CPI = 1。但 hazard 讓它變差：每次 stall 插一個泡、每次 mispredict flush 一條，都讓某些 cycle「沒完成新指令」，CPI 就爬過 1。

CPI 的價值在於**可加性**。它能寫成：

```
   CPI = CPI_ideal + stall 貢獻 + flush 貢獻 + (cache miss 貢獻, Part 4)
       =    1      + (每指令平均插幾個泡) + (每指令平均 flush 幾次) + ...
```

每一項都能獨立量測、獨立歸因。看到 CPI = 1.4，你能說「其中 0.2 來自 load-use stall、0.2 來自 branch mispredict」，於是知道「再上 gshare 大概能砍掉 flush 那 0.2 的大部分」。這就是本章要建的分析框架。

## 先建立直覺：泡泡佔了幾成時間

把 pipeline 想成一條輸送帶，理想狀態每個 cycle 從帶尾掉出一個成品（一條指令完成）。CPI = 1 就是「每 cycle 一個成品」。hazard 就是往帶子裡塞泡泡（bubble）——泡泡佔了一個 cycle 的位置卻不是成品：

```
   理想（CPI=1）：
   cyc: 1  2  3  4  5  6  7  8
   out: -  -  -  -  I1 I2 I3 I4   ← 穩態後每 cyc 一個
        └ fill ┘

   有 stall（load-use 插泡）：
   cyc: 1  2  3  4  5  6  7  8
   out: -  -  -  -  I1 (泡) I2 I3  ← 第 6 cyc 沒成品 → CPI > 1
                       ↑ load-use bubble

   有 mispredict（flush）：
   cyc: ...  branch (錯的被丟) 正確target ...
   out: ...   B    (泡)         T      ...  ← flush 也是一個沒成品的 cyc
```

CPI 的本質就是：**(總 cycle 數) / (完成的指令數)**。分母是真正做完的活（instret，instructions retired，退休指令數），分子是花掉的所有 cycle（含泡泡）。泡泡越多，分子越大、CPI 越高。優化的目標就是**減少泡泡**——這正是 forwarding（消 data hazard 的泡）和分支預測（消 control hazard 的泡）在做的事。

## 核心概念：CPI 的分解與 performance counter

要量 CPI，硬體/testbench 裡放幾個計數器（performance counter，真實 CPU 叫 hardware performance counter，HPC，透過 CSR 讀）：

| counter | 數什麼 | 用途 |
|---|---|---|
| `cycles` | 總共走了幾個 clock | CPI 分子 |
| `instret` | 完成（retire）了幾條指令 | CPI 分母 |
| `stall_cycles` | 因 hazard 插了幾個泡 | 歸因 data hazard |
| `flush_cycles` | 因 mispredict flush 幾條 | 歸因 control hazard |

它們之間有恆等式（在只有這兩種 penalty 的簡化模型下）：

```
   cycles = instret + pipeline_fill + stall_cycles + flush_cycles

   pipeline_fill = 4  ← 5 級 pipeline 灌滿要 4 個 cycle（第 5 cyc 第一條才完成）
```

於是：

```
   CPI = cycles / instret
       = (instret + 4 + stalls + flushes) / instret
       ≈ 1 + (stalls + flushes) / instret     (指令數大時 fill 的 4 可忽略)
```

這條式子是本章的骨幹。stalls 和 flushes 是可以分開數、分開優化的兩坨。真實 CPU 的 CSR 就提供 `mcycle`（cycles）和 `minstret`（instret）這兩個計數器，本課 Part 5 會實作它們；這裡我們先在 testbench 的記帳模型裡把四個 counter 都攤開。

**為什麼用記帳模型而非跑完整 RTL？** 我們要的是「乾淨地隔離 stall / flush 各自貢獻」，需要能一鍵切換「有無 forwarding」「有無預測」四種配置跑同一段 benchmark。這用一個 cycle-accurate 的**記帳模型**最清楚——它按 Ch 16–20 的 hazard 規則數 cycle，行為和 RTL 一致，但能任意組合配置。RTL 的 CPI 量測（把 counter 接進真 pipeline）留給練習 C。

## 底層機制：記帳規則（與 Ch 16–20 一致）

模型按這些規則數 stall 和 flush，每條都對應前面章節的 pipeline 行為：

**stall（data hazard）：**
- **有 forwarding**：EX→EX、MEM→EX bypass 消掉一般 RAW 相依，唯一擋不住的是 **load-use**——load 的結果 MEM 級才出來，緊跟的指令 EX 級就要用，差一拍，插 1 泡（Ch 17）。
- **無 forwarding**：結果要等 WB 才能讀。相依距離 1 的指令要 stall 2 cycle（等到來源指令 WB）、距離 2 要 stall 1 cycle。這是「故意拔掉 forwarding」看代價（Ch 16 的對照組）。

**flush（control hazard）：**
- branch 在 ID 級 resolve（Ch 18）。預測錯就 flush IF 級那條，penalty 1 cycle。
- **有預測**：用 2-bit BHT，猜對不 flush，猜錯才 flush。
- **無預測（static always-not-taken）**：每個 taken branch 都 flush（因為靜態猜 NT，taken 就錯）。

## 範例：benchmark、performance counter、實測 CPI

`cpi_model.cpp`。核心是一段有代表性的 benchmark（一個含 load-use 和迴圈回跳的迴圈，展開成指令軌跡），跑三種配置量 CPI：

```cpp
// cpi_model.cpp — 五級 pipeline 的 cycle-accurate 記帳模型
// 規則與 Ch16-20 一致：forwarding / load-use stall / branch mispredict flush
#include <cstdint>
#include <cstdio>
#include <vector>

enum Kind { ALU, LOAD, STORE, BRANCH, OTHER };
struct Insn { Kind kind; int rd, rs1, rs2; int br_taken; };

struct Config { bool forwarding; bool predict; const char *name; };
struct Result { long cycles, instret, stalls, flushes; };

static Result run(const std::vector<Insn> &prog, const Config &cfg,
                  int (*predict)(uint32_t, void*), void *pstate,
                  void (*update)(uint32_t, int, void*)) {
    long instret = (long)prog.size();
    long stalls = 0, flushes = 0;
    int prev_rd = -1, prev_kind = -1;      // 距離 1 的前一條
    int prev2_rd = -1;                     // 距離 2

    for (size_t i = 0; i < prog.size(); i++) {
        const Insn &in = prog[i];
        int u1 = in.rs1, u2 = in.rs2;

        if (!cfg.forwarding) {
            // 無 forwarding：距離 1 需 stall 2、距離 2 需 stall 1
            bool dep1 = (prev_rd > 0) && (prev_rd == u1 || prev_rd == u2);
            bool dep2 = (prev2_rd > 0) && (prev2_rd == u1 || prev2_rd == u2);
            if (dep1) stalls += 2; else if (dep2) stalls += 1;
        } else {
            // 有 forwarding：只有 load-use（距離 1 且前一條是 LOAD）stall 1
            bool loaduse = (prev_kind == LOAD) && (prev_rd > 0) &&
                           (prev_rd == u1 || prev_rd == u2);
            if (loaduse) stalls += 1;
        }

        if (in.kind == BRANCH) {
            int pred = cfg.predict ? predict((uint32_t)i, pstate) : 0; // 靜態 NT
            if (pred != in.br_taken) flushes += 1;
            if (cfg.predict) update((uint32_t)i, in.br_taken, pstate);
        }

        prev2_rd = prev_rd; prev_rd = in.rd; prev_kind = in.kind;
    }
    long cycles = instret + 4 + stalls + flushes;   // +4 = pipeline fill
    return {cycles, instret, stalls, flushes};
}

// 簡單 2-bit BHT（64 條），供 predict 配置用
struct Bht { uint8_t c[64]; };
static int bht_predict(uint32_t pc, void *s){ return ((Bht*)s)->c[pc & 63] >> 1; }
static void bht_update(uint32_t pc, int taken, void *s){
    uint8_t &v = ((Bht*)s)->c[pc & 63];
    if (taken){ if (v!=3) v++; } else { if (v!=0) v--; }
}

int main() {
    // benchmark：迴圈本體 lw / addi(load-use) / add / addi / bne(回跳)，跑 N 次
    std::vector<Insn> prog;
    const int N = 1000;
    for (int i = 0; i < N; i++) {
        prog.push_back({LOAD,   5, 10, -1, 0});               // lw   x5, 0(x10)
        prog.push_back({ALU,    6,  5, -1, 0});               // addi x6, x5, 1  ← load-use
        prog.push_back({ALU,    7,  7,  6, 0});               // add  x7, x7, x6
        prog.push_back({ALU,   10, 10, -1, 0});               // addi x10, x10, 4
        prog.push_back({BRANCH,-1, 10, 11, (i!=N-1)?1:0});    // bne  x10,x11,loop
    }

    Config configs[] = {
        {false, false, "no-fwd, static-NT"},
        {true,  false, "fwd,    static-NT"},
        {true,  true,  "fwd,    2-bit BHT "},
    };

    printf("benchmark: %zu insns (%d loop iters)\n\n", prog.size(), N);
    printf("%-20s  %8s %8s %7s %7s  %6s\n",
           "config","cycles","instret","stalls","flushes","CPI");
    for (auto &c : configs) {
        Bht bht; for (int k=0;k<64;k++) bht.c[k]=1;   // weakly NT
        Result r = run(prog, c, bht_predict, &bht, bht_update);
        printf("%-20s  %8ld %8ld %7ld %7ld  %6.3f\n",
               c.name, r.cycles, r.instret, r.stalls, r.flushes,
               (double)r.cycles / r.instret);
    }
    return 0;
}
```

編譯執行：

```bash
g++ -O2 -o cpi_model cpi_model.cpp
./cpi_model
```

真實輸出：

```
benchmark: 5000 insns (1000 loop iters)

config                  cycles  instret  stalls flushes     CPI
no-fwd, static-NT        13002     5000    6999     999   2.600
fwd,    static-NT         7003     5000    1000     999   1.401
fwd,    2-bit BHT         6069     5000    1000      65   1.214
```

這張表把三章的工作全部量化了，逐行讀懂它：

**第 1 列 `no-fwd, static-NT`（CPI 2.600）** —— 什麼優化都沒有的基準。5000 條指令花 13002 cycle。stalls 高達 6999：每輪迴圈裡 `addi x6,x5` 用剛 load 的 x5（無 forwarding 距離 1，stall 2）、`add x7,x7,x6` 用剛算的 x6（距離 1，stall 2）、`bne` 用剛更新的 x10（距離 1，stall 2）……RAW 相依密集，泡泡淹沒了 pipeline。flushes 999 是每次迴圈回跳（taken）都被靜態 NT 猜錯。**CPI 2.6 意味著平均每條指令花 2.6 個 cycle，pipeline 大半時間在吹泡泡。**

**第 2 列 `fwd, static-NT`（CPI 1.401）** —— 只加 forwarding。stalls 從 6999 暴跌到 1000。這 1000 是什麼？正是每輪那條 **load-use**（`lw x5` → `addi x6,x5`），forwarding 消不掉，每輪 stall 1，1000 輪剛好 1000。其他 RAW 全被 bypass 消掉了。**光是 forwarding 就把 CPI 從 2.6 砍到 1.4——這是 Part 2 的核心價值，現在有數字了。** flushes 仍是 999（還沒做預測）。

**第 3 列 `fwd, 2-bit BHT`（CPI 1.214）** —— 再加分支預測。stalls 不變（1000，forwarding 已經做完，預測不影響 data hazard），flushes 從 999 崩到 **65**。迴圈回跳 999 次幾乎全同方向，2-bit BHT 熱身幾次後就穩定猜 taken，只在最後一次退出（實 NT）和少數 index 別名處猜錯。**分支預測把 CPI 從 1.4 再砍到 1.21——這是 Part 3 的核心價值，也有數字了。**

把三章的貢獻攤開歸因（用 CPI 差）：

```
   CPI 2.600  ← 什麼都沒有
     -1.199   ← forwarding 消掉大量 RAW stall（Part 2）
   CPI 1.401
     -0.187   ← 分支預測消掉大量 mispredict flush（Part 3）
   CPI 1.214  ← 距離理想 1.0 還剩 0.214：
                 0.2 是 load-use stall（1000/5000），~0.014 是殘餘 flush + fill
```

剩下那 0.2 的 load-use stall 怎麼消？靠**排程**——compiler 把不相依的指令插到 load 和 use 之間填那個泡（Ch 17 提過）。這已經是軟硬體協同的地盤了。**CPI 分解讓你一眼看出「還剩什麼沒優化、該找誰優化」**，這就是它的價值。

## CPI stack：把最好配置的 CPI 攤成一疊

把最好的配置（fwd + 2-bit BHT，CPI 1.214）的每一坨畫成堆疊，就是業界叫的 **CPI stack**——base 1.0 疊上各種 penalty，一眼看出誰最厚。用上面的實測數字（instret=5000, stalls=1000, flushes=65, fill=4）算：

```
   CPI = base + stall + flush + fill
       = 1.000 + 1000/5000 + 65/5000 + 4/5000
       = 1.000 + 0.200    + 0.013    + 0.0008
       = 1.214  ✓ （和實測 1.214 對得上）

   ┌───────────────────────────────────┐ 1.214
   │ flush 0.013  ▏                     │  ← 分支預測做完後，這坨已很薄
   │ stall 0.200  ████                  │  ← 唯一還厚的：load-use，該優化這裡
   │                                    │
   │ base  1.000  ████████████████████  │  ← 理想單發射，消不掉的地板
   └───────────────────────────────────┘ 0
```

這張圖把「該優化誰」講得再清楚不過：**flush 只剩 0.013，再上更強的 gshare/TAGE 頂多把它砍到 0，CPI 最多降到 1.201——收益被它的佔比鎖死**（這是 Ch 24 會講的 Amdahl）。真正該動的是 stall 那 0.2，靠 compiler 排程填 load-use 泡。CPI stack 讓你不憑感覺、直接看數字決定資源往哪投。

## 對比取捨

| 配置 | CPI | 相對基準 | 硬體成本 | 主要殘餘 penalty |
|---|---|---|---|---|
| no-fwd, static-NT | 2.600 | 1.00× | 最省 | RAW stall（巨量） |
| fwd, static-NT | 1.401 | 1.86× | +forwarding 網路 | mispredict flush |
| fwd, 2-bit BHT | 1.214 | 2.14× | +BHT/BTB | load-use stall |
| 理想上限 | 1.000 | 2.60× | — | 無（單發射天花板） |

「相對基準」= 基準 CPI / 本配置 CPI，也就是同樣指令數快幾倍。forwarding 帶來 1.86× 加速，再加預測到 2.14×——**這兩塊硬體加起來讓同一段程式快超過兩倍**，這就是為什麼它們是 pipeline 的標配。

CPI 分析的層次：

| 層次 | 量什麼 | 工具 |
|---|---|---|
| 記帳模型（本章） | 隔離各 penalty 貢獻 | 自寫 C++ 模型 |
| RTL + counter（練習 C） | 真 pipeline 的 CPI | testbench 數 cycle/instret |
| CSR（Part 5） | 程式自己讀 mcycle/minstret | RISC-V HPC |
| 真硬體 | 真 workload 的微架構事件 | `perf`、PMU counter |

## 踩雷區

**雷 1：以為 CPI 越低程式就一定越快。**
- 錯誤直覺：「CPI 從 1.4 降到 1.2，效能就升 17%」。
- 正確認識：效能是 `time = IC × CPI × cycle_time`（iron law，Ch 24 詳談）。CPI 只是三項之一。降 CPI 若靠加深 forwarding/預測邏輯而**拉長了 cycle_time**（clock 變慢），可能整體反而變慢。而且 IC（指令數）也可能因不同 compiler/ISA 而變。**CPI 要和 cycle time、IC 一起看**，不能單看。這是 Ch 24 的核心。

**雷 2：忘了 pipeline fill，短程式 CPI 算不準。**
- 錯誤直覺：「CPI = cycles / instret 直接算就對」。
- 正確認識：這公式對，但要知道 `cycles` 裡含 pipeline **fill 的 4 個 cycle**（5 級要 4 cycle 才吐第一條）。指令數大（本章 5000）時這 4 可忽略；但量一段只有 10 條指令的 kernel，那 4 cycle 佔比大，CPI 會被 fill 灌水虛高。量 CPI 要用**夠長的 steady-state 區段**，別拿冷啟動的短程式下結論。

**雷 3：把 stall 和 flush 的貢獻混在一起，不知道該優化誰。**
- 錯誤直覺：「CPI 高就是 pipeline 爛，全面優化」。
- 正確認識：CPI 的價值就在**分解**。本章 fwd 配置下殘餘 0.2 幾乎全是 load-use stall，flush 只剩 0.013——這時砸資源上更強的 gshare 幾乎沒用（flush 已經很低），該做的是 compiler 排程消 load-use。**先量出瓶頸在 stall 還是 flush，再對症下藥**，別憑感覺全面優化。

**雷 4：以為理想 CPI 一定是 1，比 1 小是算錯了。**
- 錯誤直覺：「單發射 pipeline CPI 下限就是 1」。
- 正確認識：對**單發射（scalar）**pipeline，CPI 下限確實是 1（一 cycle 頂多完成一條）。但**superscalar / 亂序**（Ch 36）一 cycle 能 retire 多條，CPI 可以小於 1（這時常改用 **IPC = instructions per cycle** = 1/CPI，講「每 cycle 幾條」更順）。本課主線是單發射，CPI ≥ 1；看到別人講 CPI 0.5 別驚訝，那是 superscalar。

## 進階延伸

- **CPI stack（歸因堆疊）是業界標準視覺化**：真效能團隊把 CPI 畫成堆疊長條——base 1.0 + branch penalty + L1 miss + L2 miss + ... 每種微架構事件一層，一眼看出哪層最厚。本章的「2.6→1.4→1.2 逐項歸因」就是最小版的 CPI stack。Intel 的 Top-Down 分析法（frontend bound / backend bound / bad speculation / retiring）是它的工業化框架。
- **cache miss 是還沒登場的大魔王**：本章的 penalty 只有 stall 和 flush，CPI 頂多 2.x。等 Part 4 的 cache 進來，一次 L1 miss（stall 數十 cycle）、L2/記憶體 miss（stall 數百 cycle）會讓 CPI 的分解裡多出一大坨 memory 貢獻，往往比 branch/data hazard 加起來還大。「記憶體牆」（memory wall）是現代效能的頭號敵人，CPI 分析框架到那時會更能顯出威力。
- **mispredict penalty 在深 pipeline 是放大器**：本章 flush penalty 是 1 cycle（resolve 在 ID）。真 CPU 15–20 級 pipeline，一次 mispredict 是 15–20 cycle penalty。同樣的 mispredict rate，在深 pipeline 對 CPI 的傷害是本課的十幾倍——這就是為什麼高頻深 pipeline CPU 願意砸巨大面積做 TAGE。CPI 模型讓你算得出「pipeline 加深幾級、mispredict rate 不變，CPI 會漲多少」。
- **Amdahl's law 管著你的優化上限**：就算把 flush 完全消到 0，本章 CPI 也只從 1.214 降到約 1.201（flush 只佔 0.013）。優化的收益被「它佔多少比例」鎖死——這是 Amdahl 定律。CPI 分解正是幫你算出「這塊最多能省多少」，避免把力氣花在佔比極小的地方。

## 本章重點整理

- **CPI = cycles / instret**，理想單發射 pipeline = 1；hazard 讓它爬過 1。CPI 的核心價值是**可加、可歸因**：`CPI ≈ 1 + stalls/instret + flushes/instret`。
- performance counter 數四個量：`cycles`（分子）、`instret`（分母）、`stall_cycles`（data hazard 歸因）、`flush_cycles`（control hazard 歸因）。真 CPU 用 CSR `mcycle`/`minstret`。
- 真跑實測三配置：**no-fwd/static 2.600 → fwd/static 1.401 → fwd/2-bit BHT 1.214**。forwarding 砍掉大量 RAW stall（1.86× 加速）、預測砍掉大量 flush（再到 2.14×）。
- 殘餘 0.2 CPI 幾乎全是 **load-use stall**，該靠 compiler 排程消，不是加更強預測器。CPI 分解告訴你瓶頸在哪。
- 量 CPI 要用夠長的 steady-state 區段（避開 pipeline fill 灌水），且要和 cycle time、IC 一起看（iron law，Ch 24）。

## 自我檢核

- [ ] 我能寫出 `cycles = instret + fill + stalls + flushes` 並解釋每一項，說明為什麼 fill = 4。
- [ ] 我能解釋為什麼 no-fwd 配置 stalls 高達 6999，而加 forwarding 後只剩 1000（那 1000 是什麼）。
- [ ] 我能解釋加 2-bit BHT 後 flushes 從 999 掉到 65 的原因，以及為什麼 stalls 不變。
- [ ] 我能把 CPI 2.600→1.401→1.214 逐項歸因到 forwarding 和分支預測。
- [ ] 我能指出 fwd 配置下殘餘 0.2 CPI 的來源，並說出該用什麼手段消它。
- [ ] 我能解釋為什麼「CPI 降了效能不一定升」，以及量短程式 CPI 為何會虛高。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.5–4.8 節與 1.6 節「Performance」**：1.6 節建立 CPI 與 execution time 的定義（含 iron law 雛形），第 4 章各 hazard 節給出各自的 CPI 貢獻公式。本章的分解框架就是把這些串起來。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 第 1.9 節「Quantitative Principles of Computer Design」與附錄 C 的 pipeline CPI 公式**：把 `CPI = ideal_CPI + structural + data + control stalls per instruction` 寫成標準式，並教你怎麼用 benchmark 量測。是本章記帳模型的理論母體。
- **Brendan Gregg, "Systems Performance" 的 CPU 章 / Intel Top-Down 方法論文件**：把 CPI 分析帶到真硬體——教你用 `perf stat` 讀 PMU counter、算真 workload 的 CPI，以及 Top-Down（frontend/backend/bad-speculation/retiring）怎麼把 CPI stack 系統化。讀完本課想量自己機器的 CPI，從這開始。
- **RISC-V Privileged Spec 的 `mcycle` / `minstret` / `mhpmcounter` 段**：定義硬體 performance counter 的 CSR 介面。本章的 counter 到 Part 5 就是實作這些 CSR，讓 core 上跑的程式自己讀 cycle/instret 算 CPI。先掃一眼知道真硬體怎麼暴露這些數字。

CPI 告訴你「每指令幾 cycle」，但一個 cycle 到底能有多短？clock 能拉多快由**關鍵路徑（critical path）**決定。下一章我們看 pipeline 怎麼縮短關鍵路徑、setup/hold 怎麼定死 cycle time，以及「加深 pipeline 換更高 Fmax」的取捨——並用 iron law 把 CPI、cycle time、指令數三者合起來看真正的效能。

→ [Ch 24 關鍵路徑、時脈與 hazard 的量化代價](./24-critical-path-timing.md)
