# Ch 42 — Driller / QSYM

> **目標**: 理解 hybrid fuzzing 的兩種主流實作——Driller 與 QSYM——並掌握 QSYM 的 optimistic solving 為何能讓 concolic 在實務上真正跑起來。

---

## 為什麼需要 hybrid fuzzing

### Pure fuzzing 的瓶頸

AFL 的 bitmap-guided mutation 在淺層 coverage 的推進上極為有效，但在遇到「magic bytes」或複雜條件判斷時會撞牆。

典型案例：

```c
if (memcmp(buf, "FUZZ", 4) == 0) {
    // 只有輸入前 4 bytes 恰好是 0x46555A5A 才進這裡
    parse_payload(buf + 4);
}
```

AFL 靠隨機 mutation 碰到正確的 4 bytes 機率是 1/2³²，等同不可能。它能讓 coverage bitmap 收斂但無法越過這道門。實務上，AFL 跑 24 小時後 bitmap 新增 edge 的速度往往降到接近 0，表示 fuzzer 已在已探索的空間裡原地打轉。

### Pure concolic 的爆炸

另一個極端：把整個程式做完整的符號執行（KLEE 或單純用 angr），試圖解出每條 branch 的解。這在玩具程式上成立，但面對真實 binary 會炸在三個地方：

1. **路徑爆炸**：每個條件判斷讓狀態數翻倍，程式跑 100 個 if，潛在狀態就有 2¹⁰⁰ 條。
2. **環境模型不完整**：系統呼叫、動態連結庫、SIMD 指令——angr 全部需要手工 hook 或模擬，漏掉任何一個就卡死。
3. **SMT 求解器限制**：遇到非線性算術（CRC、SHA、AES）就超時或直接 unknown。

Driller 和 QSYM 的出發點是：兩種方法各自有對方沒有的優勢，不要擇一，讓它們協同工作。

---

## 先建立直覺

```
┌─────────────────────────────────────────────────────────────┐
│                     Driller 雙引擎架構                       │
│                                                             │
│  ┌──────────────────┐      新 input (magic bytes 解出)      │
│  │   AFL Fuzzer     │◄────────────────────────────────────┐ │
│  │  (廣度探索)       │                                     │ │
│  │  bitmap coverage │─── stuck at node N ──►┌──────────┐  │ │
│  └──────────────────┘                       │  angr    │  │ │
│           │                                 │ concolic │  │ │
│    探索新 coverage                           │  engine  │  │ │
│           │                                 └────┬─────┘  │ │
│           ▼                                      │        │ │
│     AFL queue (corpus)◄──────────────────────────┘        │ │
│                                                             │
│  AFL 負責廣度，angr 負責突破 magic-byte 障礙                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Driller 核心概念

### AFL 做廣度探索

Driller 沿用標準 AFL：對 input 做 bit-flip、havoc、splice 等 mutation，每次執行記錄 edge coverage（src_block → dst_block 的 bitmap）。只要有新 edge 被觸發，這個 input 就進 queue 繼續 mutate。

AFL 的本質是**機率性廣度優先**：能快速覆蓋「mutation 可達」的路徑，卡在需要精確數值的條件前。

### 在 AFL 卡住的節點上觸發 angr concolic

Driller 監控 AFL queue 的成長速度。當一段時間內沒有新的 bitmap edge 產生（stalled），就把目前的 corpus 中的 input 丟給 angr 做 concolic 分析。

angr 從 input 的 concrete 值出發，同時追蹤符號約束。每當執行到 branch 指令，angr 記錄 path constraint，然後對**尚未走過**的分支取反，呼叫 claripy（angr 內建的 SMT 求解前端，後端是 Z3）求解出能走那條分支的 concrete input。

### angr 的 claripy 求解器解出 input，餵回 AFL queue

求解成功後，angr 產出一個新的 input byte string，Driller 把它寫進 AFL 的 input directory。AFL 在下一次迭代掃到這個新 input，從此開始在「之前無法到達的分支」後面繼續 mutate。

這是 hybrid fuzzing 的核心循環：AFL 探索 → 卡住 → angr 求解突破點 → 新 input 回 AFL → AFL 繼續探索。

### Driller 的觸發策略

Driller 不是「AFL 一 stall 就馬上叫 angr」，而是有一套 stall detection 機制：

1. 追蹤 AFL 的 `paths_found` 計數器和時間戳
2. 若在一個滑動視窗（預設 5 分鐘）內 `paths_found` 沒有增加，判定為 stall
3. 從當前 queue 中選一個 input（通常是最新加入的那個）
4. 把這個 input 交給 angr 做 concolic，angr 探索「以這個 input 為出發點、但走不同 branch 的路徑」

angr 在跑 concolic 時使用 **unicorn engine 加速**（angr 有 `concretization` + unicorn 的混合模式），對純 concrete 執行段用 unicorn 跑，只在需要符號化的 branch 上切換回 angr 的 symbolic engine。這讓 angr 的 concrete 段有一定加速，但符號化段仍然慢。

### 限制：angr 速度慢

Driller 在學術上成立，在 CGC（DARPA Cyber Grand Challenge）的小型 binary 上也確實有效。但在真實世界的大型 binary 上，angr 跑一個 execution trace 可能要幾分鐘甚至更久，因為：

- angr 是 Python 實作，interpreter overhead 大
- 每個記憶體存取都要建 symbolic memory model
- 即使只跑 concrete mode，angr 也比 native 慢 100× 以上

CGC binary 平均只有幾十 KB，程式邏輯簡單，angr 能在合理時間內跑完。一個真實的 HTTP server binary 可能是幾 MB，angr 在初始化 state 上就要花幾分鐘。

在 fuzzing session 中，每次 AFL stall 就要等 angr 跑完，這個等待成本讓 Driller 在實務上很難對真實目標部署。QSYM 正是看到這個瓶頸才改走 native instrumentation 路線。

---

## QSYM 核心概念

QSYM（2018，Georgia Tech）是對 Driller 瓶頸的直接回應，也是目前 hybrid fuzzing 最重要的工程實作之一。

### Native 執行 + 細粒度插樁

QSYM 不用模擬器，不用 Python 層的 IR lifting。它基於 **Intel PIN**，在 native 執行過程中插入輕量 instrumentation，在每條指令執行前後記錄符號狀態。

這意味著 binary 以 native speed 跑，PIN 的 JIT 開銷遠小於 angr 的 Python + Vex IR 翻譯。QSYM 的 concrete execution 速度比 angr 快一到兩個數量級。

### Optimistic Solving（核心機制）

這是 QSYM 最重要的設計決策，也是讓它比 Driller 實用的關鍵。

傳統 concolic 在遇到以下情形時會卡死：

```c
if (crc32(input, len) == 0x1234ABCD) {
    process(input);
}
```

CRC32 在 SMT 層面展開後是幾百個位元的非線性方程式。Z3 或 STP 面對這類 constraint 要嘛超時，要嘛直接回傳 unknown。傳統做法是放棄這個 branch，或者把 crc32 完整展開（展開後的 bit-vector formula 會讓求解時間爆炸）。

QSYM 的 optimistic solving 選擇第三條路：**直接丟掉這條約束，對剩下的約束求解**。

具體步驟：

1. 執行到 `crc32(input, len) == 0x1234ABCD` 這個 branch condition
2. QSYM 嘗試求解完整約束集合（目標 branch + 所有 path constraint）
3. 如果 solver 超時或 unsatisfiable，把這個「難解的約束」從集合中移除
4. 對縮減後的約束集合再次求解

結果可能是：solver 解出一個 input，這個 input 進 `process()` 的概率很高，但 crc32 值是錯的。對於不在意 checksum 的後續路徑，這個 input 仍然有效；對於嚴格驗 checksum 的 binary，這個 input 會在 checksum 那一關被擋掉，但它仍然能探索 `process()` 內部的 branch（如果 binary 先 parse 再驗 checksum 的話）。

Optimistic solving 的本質是：**寧可產生一個「可能無效」的 input，也不要完全放棄這條路徑**。在實務上，大多數程式的複雜 constraint 只影響局部驗證，跳過它之後仍然能探索有意義的新 coverage。

### 只 Concretize 影響當前 Branch 的 Bytes

傳統 concolic 會把整個 input 向量符號化，這讓 constraint 的維度極高。QSYM 做 **lazy symbolization**：只把實際上流入當前 branch condition 的 input bytes 標記為 symbolic，其餘維持 concrete。

如果 `buf[0]` 和 `buf[1]` 決定了某個 branch，QSYM 的 constraint 只涉及 2 個符號變數而非整個 input 的每個 byte。這讓 SMT 問題規模大幅縮減。

### 為什麼 QSYM 比 Driller 快

| 比較維度 | Driller (angr) | QSYM |
|---------|---------------|------|
| 執行方式 | Python 模擬 + Vex IR | Intel PIN native JIT |
| Constraint 生成速度 | 慢（Python overhead） | 快（C++ PIN callbacks） |
| 對複雜 constraint 的處理 | 嘗試完整求解（超時） | Optimistic solving（丟掉難解約束） |
| Symbolization 範圍 | 整個 input | 影響當前 branch 的 bytes |
| 實測加速 | 基準 1× | 10× – 50× |

---

## QSYM 底層機制

```
┌─────────────────────────────────────────────────────────────────┐
│                   QSYM 執行架構                                   │
│                                                                  │
│  input (from AFL queue)                                          │
│        │                                                         │
│        ▼                                                         │
│  ┌─────────────┐    native JIT      ┌──────────────────────┐   │
│  │  Target ELF │──────────────────► │  PIN instrumentation  │   │
│  │  (x86-64)   │                    │  (每條 insn 插 hook)  │   │
│  └─────────────┘                    └──────────┬───────────┘   │
│                                                │               │
│                              記錄 symbolic 狀態 │               │
│                                                ▼               │
│                               ┌────────────────────────┐       │
│                               │  Constraint Collector   │       │
│                               │  - lazy symbolization   │       │
│                               │  - branch condition 追蹤│       │
│                               └──────────┬─────────────┘       │
│                                          │                      │
│                       optimistic solving │ (丟掉難解約束)        │
│                                          ▼                      │
│                               ┌────────────────────────┐       │
│                               │  Z3 SMT Solver          │       │
│                               │  (via C++ API, no Python)│      │
│                               └──────────┬─────────────┘       │
│                                          │                      │
│                                 新 input  │                      │
│                                          ▼                      │
│                               ┌────────────────────────┐       │
│                               │  AFL out/ directory     │◄──────┤
│                               │  (QSYM 寫入新 testcase) │       │
│                               └────────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

QSYM 和 AFL 透過共享的 output directory 溝通：AFL 是 master，QSYM 是 slave。QSYM 定期掃 AFL 的 queue，挑 coverage 停滯的 input 做 concolic，把解出的新 input 丟回 AFL 的 output dir，AFL 的下一輪迭代就會把它 pick up。

### QSYM 的 Taint Tracking 細節

QSYM 在 PIN instrumentation 層做的不只是「記錄 branch condition」，它同時追蹤 taint：哪些 input bytes 的值流入了哪個寄存器或記憶體位置。

這個追蹤是 **byte-level taint**：每個寄存器的每個 byte 都有對應的 taint 標籤，記錄它來自 input 的哪個 offset。當一個 branch condition 被評估時，QSYM 從 taint 標籤反查這個 condition 涉及的 input bytes，只對這些 bytes 符號化。

實作上，QSYM 維護一個 shadow memory：

```
原始記憶體:  [0xAB][0xCD][0xEF][0x12] ...
Taint 標籤:  [in[2]][in[3]][none ][in[0]] ...
```

當 x86 指令 `cmp al, 0x41` 執行時（al 對應 taint 標籤 `in[2]`），QSYM 記錄約束 `input[2] == 0x41`（對 taken branch）或 `input[2] != 0x41`（對 not-taken branch）。

這個 byte-level precision 讓 QSYM 避免了「把整個 input 都符號化」的爆炸問題。

---

## 理論驗證步驟

**本段未實測，理論預期行為。** QSYM 依賴 Intel PIN，且整合 AFL 需要特定版本匹配，實際部署建議用官方 Docker image。

### 取得 QSYM

```bash
git clone https://github.com/sslab-gatech/qsym
cd qsym
```

QSYM 官方 repo 提供 Dockerfile，這是最穩的部署方式：

```bash
docker build -t qsym .
docker run -it qsym /bin/bash
```

### 準備目標 binary

以一個含 magic bytes 的測試程式為例：

```c
// target.c
#include <stdio.h>
#include <string.h>
int main(int argc, char *argv[]) {
    FILE *f = fopen(argv[1], "rb");
    char buf[64];
    fread(buf, 1, 64, f);
    fclose(f);
    if (buf[0] == 'Q' && buf[1] == 'S' && buf[2] == 'Y' && buf[3] == 'M') {
        if (buf[4] > 0x80) {
            // 只有 QSYM 能到這裡
            printf("deep branch!\n");
        }
    }
    return 0;
}
```

```bash
gcc -o target target.c
```

### 啟動 AFL master

```bash
# 建立初始 corpus
mkdir -p /tmp/in && echo "AAAA" > /tmp/in/seed

afl-fuzz -i /tmp/in -o /tmp/out -M afl-master -- ./target @@
```

AFL 很快會 stall，因為 mutation 幾乎不可能碰出 `QSYM`。

### 啟動 QSYM slave（理論指令）

```bash
# QSYM 讀 AFL 的 out dir，執行 concolic
python3 -m qsym.afl \
    -o /tmp/out \
    -n qsym-slave \
    -- ./target @@
```

理論上，QSYM 會對 AFL queue 中 stall 的 input 做 concolic，解出 `buf[0..3] == "QSYM"` 的約束，產生新 input 寫回 `/tmp/out/qsym-slave/queue/`，AFL master 在下一次 sync 時掃到這個 input，coverage bitmap 新增 edge。

### 版本相容性注意事項

QSYM 的官方 repo 最後更新在 2020 年前後，對應的 AFL 版本是 AFL 2.52b（非 AFL++）。若要與 AFL++ 整合，需要確認 sync directory 格式相容，或改用 AFL++ 內建的 QSYM 整合選項（AFL++ 3.x 後有部分原生支援）。

Intel PIN 的版本也需要匹配：QSYM 測試過的 PIN 版本是 3.7，較新的 PIN 版本（3.19+）在部分 syscall instrumentation 上行為不同。Docker 方式部署可以固定這些版本依賴，避免環境差異。

---

## 架構對比表

| 特性 | 純 AFL | Driller | QSYM Hybrid | SymCC Hybrid |
|------|--------|---------|-------------|-------------|
| Magic bytes | 幾乎無解 | angr 求解 | QSYM 求解 | 編譯期插樁 |
| 執行速度 | 最快 | 慢（Python angr） | 快（native PIN） | 快（原生編譯） |
| 需要原始碼 | 否 | 否 | 否 | 是（SymCC） |
| Optimistic solving | 無 | 無 | 有 | 無 |
| 對大型 binary | 擴展性佳 | 差 | 中等 | 好（若有原始碼） |
| 環境限制 | 跨平台 | 跨平台 | x86-64 Linux | 編譯目標平台 |
| 主要弱點 | magic bytes | angr 太慢 | stripped binary | 需原始碼 |

---

## 踩雷

### 以為 QSYM 能跑任何 binary

QSYM 基於 Intel PIN，只支援 **x86-64 Linux ELF**。對以下情形不支援或效果極差：

- **Stripped binary**：PIN 可以插樁，但 QSYM 的 taint 追蹤依賴函式邊界識別，stripped 後部分分析失效
- **Packed binary**（UPX、VMProtect）：binary 在記憶體中解壓自身，PIN 的 JIT 跟不上 self-modifying code
- **Windows PE、ARM ELF**：架構不支援，直接失敗
- **含大量 SIMD 指令的 binary**：部分 AVX-512 指令的符號語義未實作

遇到「QSYM 啟動後立刻 crash」，第一個懷疑點是 binary 格式或 packing 問題。

### Optimistic solving 會漏掉 exact constraint

QSYM 丟掉「難解約束」的設計在多數情況下是優點，但對需要精確值的 branch 是盲點。

典型情境：

```c
if (adler32(buf, len) == expected) {   // 計算 checksum
    if (buf[8] == 0xFF) {              // 只有通過 checksum 才能到這裡
        vulnerable_func(buf);
    }
}
```

QSYM 的 optimistic solving 可能解出 `buf[8] == 0xFF`，但因為 checksum 約束被丟掉，產出的 input adler32 值不對，binary 在第一個 if 就返回，永遠無法到達 `vulnerable_func`。

解法：對已知的 checksum 函式手工 hook（告訴 QSYM「這個 call 永遠回傳 true」），或在外層先 patch binary 繞過 checksum。

### QSYM 跑太久沒有新 coverage

QSYM 有時會陷入「一直在解 constraint，但解出的 input 沒有新 edge」的狀態。常見原因：

1. AFL 的 corpus 已收斂，QSYM 拿到的都是「深層 path」的 input，這些路徑的新 branch 需要多個約束同時滿足，但 optimistic solving 每次只解一個
2. Z3 對某些約束形式的求解時間分布極不穩定（bimodal：要嘛很快，要嘛幾分鐘後超時）

處理方式：為 QSYM 的每個 input 設定 **solving timeout**（官方建議 30 秒），超過就跳下一個 input。不加 timeout 的話 QSYM 可能在一個 hard instance 上卡幾十分鐘。

另外，如果 QSYM 的 `new_path` 計數超過一段時間後不再增長，通常代表 fuzzing campaign 已到達當前配置的上限，要考慮換 seed corpus 或調整 mutation 策略。

### 忽略 QSYM 的 Input 選取策略

QSYM 在挑「要做 concolic 的 input」時有自己的優先順序邏輯：優先挑 AFL 最近新加進 queue 的 input（因為它們代表尚未被充分 mutate 的新 path）。但如果使用者不了解這個機制，可能會誤以為 QSYM 是對所有 queue 做完整 concolic，實際上它是依序處理、有 budget 限制的。

若 QSYM 的 `--run-timeout` 設太短（如 5 秒），對較大的 binary 每個 input 都會超時，QSYM 幾乎產不出任何新 testcase。需要依 binary 大小調整，通常 30–120 秒是合理範圍。

---

## 進階延伸

### QSYM 的後繼工作

**SymSan（2022）**：把 QSYM 的插樁邏輯移植到編譯期（LLVM pass），不再依賴 PIN 的 JIT overhead。對有原始碼的目標，SymSan 比 QSYM 再快 2–5×，且 coverage instrumentation 和 taint tracking 整合更緊密。

**CollabFuzz（2021）**：把多個 fuzzer（AFL、QSYM、Radamsa、Honggfuzz）組成協作框架，用 coverage 共享協定讓各個 engine 互補。QSYM 在 CollabFuzz 裡是「concolic oracle」，其他 fuzzer 都可以把 stalled input 丟給它。

**Fuzzolic**：用 QEMU（而非 PIN）做 concolic 插樁，支援 ARM binary，試圖解決 QSYM 只支援 x86-64 的限制。

**SymQEMU**：在 QEMU 的 TCG IR 層注入符號語義，概念類似 SymCC 對 LLVM IR 的做法。整合 symcc 的 runtime library 做 constraint collecting，跨架構支援更好。

這些後繼工作的共同趨勢是：把符號追蹤推向更底層（編譯期 or TCG IR），減少執行期 overhead，同時維持 optimistic solving 的務實精神。

### Hybrid Fuzzing 的現狀評估

2024 年後，hybrid fuzzing 的主流玩法已不是「單一 concolic slave + AFL master」，而是多工具協作的鬆耦合框架：

- AFL++ 內建支援多種 concolic backend（可選 symcc、symqemu、coresight），使用者在啟動時選擇 `-c symcc` 等選項即可整合
- Google 的 FuzzBench 持續追蹤各工具在標準化 benchmark 上的 coverage 和 bug-found 數據，可以直接查 benchmark 結果比較工具
- 實務上，對有原始碼的目標，AFL++ + SymCC 是比 QSYM 更常見的選擇；對 binary-only 目標，AFL++ + QSYM 仍是最常見的組合

QSYM 的論文成果在當時（2018）是顯著突破，但它的 codebase 維護不活躍，遇到奇怪 bug 時 debug 很耗時。生產環境部署前要評估維護成本。

---

## 動手練習

1. **觀察 AFL stall 現象**：用一個含 `memcmp(buf, "MAGIC", 5)` 的程式跑 AFL 30 分鐘，記錄 `execs/sec` 和 `paths found` 的變化曲線。確認在 magic bytes 關卡前 AFL 確實停滯。

2. **手動模擬 optimistic solving**：寫一個 angr 腳本，對以下 binary 做 concolic：
   ```c
   // 含 crc32 + 後續 parse
   if (crc32(buf) == 0xDEAD && buf[4] == 0xFF) { ... }
   ```
   先嘗試完整求解（觀察超時），再手動移除 crc32 那條 constraint 後重新求解，對比兩次的成功率和時間。

3. **分析 QSYM 論文的 evaluation**：閱讀 QSYM USENIX Security 2018 論文的 Section 5（Evaluation），找出 QSYM 在哪些 benchmark 上顯著超越 AFL，在哪些上面差距不大，分析原因。

4. **探索 SymCC**（若有 Linux 環境）：從 [https://github.com/eurecom-s3/symcc](https://github.com/eurecom-s3/symcc) 取得 SymCC，對一個簡單程式做編譯期插樁，觀察它如何在不依賴 PIN 的情況下做 concolic。

---

## 本章重點

- **Driller** = AFL（廣度） + angr（突破 magic bytes），學術上驗證了 hybrid fuzzing 可行，但 angr 的 Python 速度讓它難以部署在真實大型 binary 上。
- **QSYM** 的核心貢獻是兩點：native 執行（Intel PIN，非模擬器）+ optimistic solving（丟掉難解約束，只解能解的）。
- **Optimistic solving** 不是「偷懶」，是務實取捨：寧可產出「可能繞不過 checksum」的 input，也要能繼續探索後續 branch；大多數程式的 checksum 只是局部驗證，跳過它仍能挖到新 coverage。
- **QSYM 的限制**：只支援 x86-64 Linux ELF，不支援 stripped/packed binary，需要設 solving timeout 避免 stall。
- QSYM 的後繼（SymSan、SymQEMU、CollabFuzz）朝向「更底層插樁、更廣架構支援、多 engine 協作」演進。

---

## 自我檢核

- [ ] 能解釋 AFL 在 magic bytes 面前為何會 stall（從 bitmap coverage 的角度）
- [ ] 能畫出 Driller 的雙引擎循環（AFL stall → angr → 新 input → AFL queue）
- [ ] 能說明 QSYM 為何比 Driller 快：native PIN vs Python angr
- [ ] 能解釋 optimistic solving 對 `crc32(input) == 0x1234` 的處理方式
- [ ] 知道 lazy symbolization 如何縮減 SMT 問題規模
- [ ] 能列出 QSYM 的三個實務限制，並知道各自的 workaround

---

## 延伸閱讀

1. **Driller: Augmenting Fuzzing Through Selective Symbolic Execution**
   Stephens et al., NDSS 2016
   [https://sites.cs.ucsb.edu/~vigna/publications/2016_NDSS_Driller.pdf](https://sites.cs.ucsb.edu/~vigna/publications/2016_NDSS_Driller.pdf)
   — 原始論文，CGC binary 上的評估，angr 整合的設計細節。

2. **QSYM: A Practical Concolic Execution Engine Tailored for Hybrid Fuzzing**
   Yun et al., USENIX Security 2018
   [https://www.usenix.org/conference/usenixsecurity18/presentation/yun](https://www.usenix.org/conference/usenixsecurity18/presentation/yun)
   — QSYM 原始論文，Section 3 的 optimistic solving 設計、Section 5 的 benchmark 比較是核心。

3. **CollabFuzz: A Multi-Tool Collaborative Fuzzing Framework**
   Osterlund et al., EuroSec / AST 2021
   [https://dl.acm.org/doi/10.1145/3459784.3459790](https://dl.acm.org/doi/10.1145/3459784.3459790)
   — QSYM 作為多 engine 框架裡的 concolic oracle，展示 hybrid fuzzing 的協作架構演進。

4. **SymSan: Time and Space Efficient Concolic Execution via Dynamic Data-flow Analysis**
   Chen et al., USENIX Security 2022
   [https://www.usenix.org/conference/usenixsecurity22/presentation/chen-ju](https://www.usenix.org/conference/usenixsecurity22/presentation/chen-ju)
   — QSYM 的編譯期後繼，對有原始碼的目標去掉 PIN 的 JIT 開銷，值得與 QSYM 對比閱讀。

---

本章從 Driller 的學術驗證出發，深挖了 QSYM 讓 hybrid fuzzing 從「理論可行」到「實務部署」的三個關鍵設計：native execution、optimistic solving、lazy symbolization。這三個設計都是對「純 concolic 在實務上為何失敗」的直接回應，不是理論上的優化，是工程上的妥協——有時候「解不完整的 constraint」比「解不出來」更有用。

接下來看另一個維度的定向化——把 fuzzer 引導向**特定目標位置**而非廣義 coverage。

→ [下一章](./43-directed-fuzzing-aflgo.md)
