# Ch 30 — Intel PT 當 coverage source（深挖章）

> **目標：** 理解 Intel Processor Trace 如何在沒有插樁的情況下提供 edge coverage，它的 packet 格式是什麼，以及解碼後的 coverage 怎麼對映到 AFL-style bitmap——最後討論它和插樁 coverage 的精度差異與使用場景。
>
> **環境：** Intel PT 需要 Intel Broadwell（2015）或更新的 CPU，以及 OS 的配合（Linux 4.1+，Windows 10 1703+）。WSL2 通常**不暴露 Intel PT 功能**（因為 Hyper-V 的限制）。本章的 **packet 格式分析和解碼邏輯為理論為主，標注「[未實測]」**；用 `perf` 觀察 PT 是否可用的指令可以嘗試，但 WSL2 上多半失敗。有裸金屬 Linux 的讀者可以用 `perf` 驗證。

---

## 先建立直覺

插樁 coverage 的工作原理：你在編譯時在每個 edge 上加一行程式碼，讓它執行時更新 bitmap。

```c
// 插樁後的 target（偽碼）
void process_input(char *buf) {
    bitmap[EDGE_A]++;  // ← 插樁加的
    if (buf[0] == 'X') {
        bitmap[EDGE_B]++;  // ← 插樁加的
        handle_x(buf);
    } else {
        bitmap[EDGE_C]++;  // ← 插樁加的
        handle_other(buf);
    }
}
```

這個方式的根本限制：**你需要能修改 target**。

Intel PT 的思路反過來：讓 CPU 硬體自己記錄執行流，你再從那個記錄裡解出 coverage。Target 完全不用修改。

---

## Intel PT 的硬體行為

Intel PT 是 Intel Broadwell（2015）引入的 CPU feature，在 CPU 流水線內部記錄控制流資訊。以下是它記錄的主要事件類型：

```
程式執行：
  A → B → C → D（順序執行，不分支）
  D → 條件分支（taken → E，not taken → F）
  E → 呼叫函數 G
  G → ret → E+4

Intel PT 記錄：
  不記錄順序執行（太多）
  記錄分支：
    條件分支 taken/not taken     ← TNT packet
    間接跳躍目的地               ← TIP packet
    call/ret 目的地              ← TIP packet（可選）
    異常/中斷                    ← 特殊 packet
```

設計選擇：**只記錄分支**，不記錄每條指令。這大幅壓縮了資料量（通常 10–20x 壓縮比），代價是解碼時需要 binary image 來重建完整執行流。

---

## Packet 格式：TNT 和 TIP

Intel PT 的輸出是壓縮的 packet stream，寫入一個環形緩衝區（PT buffer，通常 4MB）。最重要的兩種 packet：

### TNT（Taken / Not Taken）Packet

記錄連續的條件分支決定：

```
TNT packet（短格式，1 byte）：
  bit 7:   stop bit（標記這是 TNT packet）
  bit 6–1: 最多 6 個分支決定，1 = taken, 0 = not taken
  bit 0:   結束 bit

例：
  CPU 執行：taken, not taken, taken, taken
  TNT byte：1[1011]10（假設 4 個分支，剩餘位元用 stop bit 填）
  解碼器讀到 TNT packet，配合 binary image 知道「下一個條件跳躍 taken」
```

**[上述 packet bit 格式為 Intel 手冊描述，未實測解碼。]**

### TIP（Target IP）Packet

記錄「目的地地址」——用於間接跳躍（call [rax]、jmp [rbx]）、回傳（ret）：

```
TIP packet（2–8 bytes）：
  header byte：指示壓縮格式
  payload：目的地地址（壓縮，只傳跟上次不同的位元組）

例：
  ret 從函數 G 回到 E+4
  TIP payload：0x555500401234（完整地址）
  下次同一個 ret：可能只傳低 2 bytes（如果高位沒變）
```

**[TIP 的壓縮細節來自 Intel SDM Vol 3 Chapter 35，未實測解碼。]**

---

## 從 Packet Stream 到 Edge Coverage

**[本節解碼流程為理論描述，基於 libipt 文件和 Nyx 論文，未實測完整流程。]**

解碼流程：

```
PT buffer（raw packets）
        │
        ▼
  libipt 解碼器
  （Intel 官方庫）
        │
        ├── 讀 binary image（target 的 .text section）
        ├── 按 TNT/TIP 指示跟蹤執行流
        └── 輸出：(from_addr, to_addr) 邊列表
        │
        ▼
  Edge → bitmap 對映
  bitmap[(from >> 4 XOR to >> 4) & 0xFFFF]++
  （AFL 標準的雜湊函數）
        │
        ▼
  AFL-compatible edge coverage bitmap（8KB）
```

libipt 的核心 API 使用模式（概念）：

```c
// [未實測，為 libipt API 概念範例]
#include "intel-pt.h"

// 建立解碼器
struct pt_insn_decoder *decoder = pt_insn_alloc_decoder(&config);

// 載入 binary image
struct pt_image *image = pt_image_alloc("target");
pt_image_add_file(image, "/path/to/target", 0, (uint64_t)-1, 0);
pt_insn_set_image(decoder, image);

// 解碼
struct pt_insn insn;
while (1) {
    int status = pt_insn_next(decoder, &insn, sizeof(insn));
    if (status < 0) break;
    // insn.ip 是指令地址
    // 從連續地址跳躍就是一條 edge
}
```

---

## 與 Edge Coverage 的對映

AFL-style edge coverage 的 bitmap 對映：

```
Edge (A → B)：
  key = (A >> 4 XOR B >> 4) & 0xFFFF
  bitmap[key]++

Intel PT 解碼出邊之後，用相同的雜湊函數更新 bitmap
→ 跟插樁 coverage 格式相容
→ 可以直接把同樣的 AFL 變異邏輯接上
```

雜湊碰撞：AFL 的 16-bit key 空間（65536 個 bucket）不可避免會碰撞。這在插樁版本裡也存在，Intel PT 版本不會更差——問題不是 PT 帶來的，是 AFL bitmap 設計的 trade-off。

---

## 實際可以觀察的部分（WSL2 嘗試）

以下指令在 WSL2 可能成功，也可能因為缺乏 PT 支援而失敗：

```bash
# 檢查 CPU 是否有 intel_pt feature bit
grep intel_pt /proc/cpuinfo
# WSL2 上通常顯示有（CPU feature 通過），但 perf 使用 PT 會失敗

# 嘗試用 perf 查詢 intel_pt event 是否可用
perf list 2>/dev/null | grep -i intel_pt
# WSL2 上通常是空的或出錯

# 如果上面有輸出（代表跑在裸金屬或支援 PT 的 VM）：
# perf record -e intel_pt// -- ls /
# perf script  # 解碼 trace
```

**[如果 `grep intel_pt /proc/cpuinfo` 有輸出但 `perf list` 沒有 intel_pt，代表 WSL2 的 Hyper-V 層把 PT 能力遮掉了——這是正常的 WSL2 限制，不是 CPU 問題。]**

有裸金屬 Linux + Intel CPU 時的完整實測：

```bash
# 確認 PT 可用
perf list | grep intel_pt

# 記錄一個簡單程式的 PT trace
perf record -e intel_pt// --call-graph=lbr -- ls /tmp

# 用 perf script 解碼
perf script --no-itrace

# 用 perf script 輸出邊記錄
perf script -F +srcline,+addr,+insn --ns 2>/dev/null | head -50

# 查看 PT buffer 溢出情況
perf stat -e intel_pt// -e r0c:u -- ./your_target 2>&1 | grep aux_bytes
```

---

## Intel PT 的開銷與精度

### 開銷

Intel 的官方數字：Intel PT 的 CPU overhead 通常在 **3–10%**，PT buffer 寫入的記憶體 bandwidth overhead 通常 **<5%**。

這遠低於 QEMU TCG binary translation（30–50% overhead）或 DynamoRIO/Valgrind 插樁（通常 5–20x 慢）。

### PT buffer 溢出：最大的精度風險

PT buffer 是環形緩衝區，有容量上限（預設 4MB）。如果 target 執行太多分支，buffer 滿了就從頭覆寫——這段被覆寫的 trace 就丟失了。

```
PT buffer（4MB）
[─────────────────────────────────────────]
                                  ↑
                            寫指標

buffer 滿了，繼續寫：
[新資料覆寫───────────────────────────────]
      ↑
   舊資料丟失
```

**實務影響**：長時間執行的輸入（>幾百萬條指令）會丟失部分 coverage。解法：
1. 加大 PT buffer（16MB 甚至更大）
2. 定期中斷 target，flush PT buffer，再繼續
3. 用 timeout 限制每個輸入的執行時間

---

## Intel PT vs 其他 coverage 方案對比

```
方案              需要原始碼  Overhead   精度          適用目標
─────────────────────────────────────────────────────────────────
AFL 插樁（clang）  是          ~0%        高（無碰撞外）  有源碼的 userland
QEMU user-mode    否          ~30–50%   高              userland binary
Intel PT          否          ~3–10%    高*（PT 溢出時低） 任意（kernel/hypervisor）
DBI（Pin/DynRIO） 否          ~5–20x    高              userland binary
QEMU full-system  否（但慢）  高         高              kernel（需要 VM 管理）

* PT 溢出不常見，但存在
```

結論：Intel PT 是「不能插樁、不能接受高 overhead」的情境下的最佳選擇。如果有原始碼，clang 插樁精度更高、overhead 更低。

---

## Intel PT 在 fuzzing 之外的用途

理解 Intel PT 在 fuzzing 以外的應用，有助於判斷什麼場景值得投資：

- **RCA（Root Cause Analysis）**：記錄 crash 前的完整執行流，不需要重現
- **逆向工程**：追蹤一個 binary 的執行路徑，不需要 debug symbol
- **Exploit 開發**：精確追蹤 heap 操作順序，幫助理解 use-after-free 的觸發條件
- **Coverage-guided corpus 建立**：對無法重新編譯的 target 建立 seed corpus 覆蓋率統計

---

## 踩雷

**錯誤直覺一：「Intel PT 只能在 userland 用，打 kernel 要靠 KCOV」**

正確理解：Intel PT 可以同時追蹤 userland 和 kernel 的執行，只需要在啟動 PT 時設置 `CPL0` bit（允許記錄 ring 0 執行）。kAFL/Nyx 正是這樣用它的——同一個 PT session 裡同時拿到 userland agent 和 kernel 的 coverage。KCOV 是軟體插樁，需要重新編譯 kernel，功能不同。

**錯誤直覺二：「PT buffer 滿了會讓 fuzzer 崩潰或停止執行」**

正確理解：PT buffer 滿了的預設行為是「wrap around 覆請」，目標程式繼續執行，只是那段 trace 資料丟失。Fuzzer 不會停止，只是那段執行的 coverage 沒有記錄到。這是精度問題，不是可靠性問題。可以設定 `STOP` 模式讓 target 在 buffer 滿時暫停，但這會影響 exec/s。

**錯誤直覺三：「Intel PT coverage 和 AFL 的 bitmap coverage 是不同格式，不相容」**

正確理解：Intel PT 解碼後給你 (from, to) 邊列表，你用 AFL 的標準雜湊函數（`(from >> 4 XOR to >> 4) & 0xFFFF`）就能填進 AFL bitmap。Nyx 和 libafl_qemu 都這樣做。格式完全相容，AFL/LibAFL 的分析邏輯可以直接使用。

---

## 進階延伸

**libipt**：Intel 官方的 PT 解碼庫（C）。https://github.com/intel/libipt — 讀 `ptdump` 工具的原始碼是理解 packet 格式的最直接方法。

**perf-pt-convert**：Linux `perf` 工具支援 Intel PT，`perf script` 可以輸出解碼後的指令流。比 libipt 更方便做初步觀察。

**PEBS（Precise Event-Based Sampling）**：另一個 Intel 硬體 feature，可以做精確到指令的取樣 profile。比 PT 輕量，但不是全量 trace。fuzzing 比較少用，但效能分析常用。

---

## 動手練習

1. 在你的環境上執行 `grep intel_pt /proc/cpuinfo` 和 `perf list | grep intel_pt`，記錄輸出，確認是否有 PT 能力。如果沒有（WSL2 常見），查閱 WSL2 的 Issue tracker 確認這是已知限制。

2. 閱讀 libipt 的 `README.md`（https://github.com/intel/libipt），找到「Quick Start」部分，理解 `ptdump` 工具如何把 PT raw data 轉成人類可讀格式。

3. 閱讀 Nyx 論文 Section 5.2（Intel PT Integration），找出他們如何處理 PT buffer 溢出問題，以及為什麼選擇特定的 buffer 大小。

4. **如果有裸金屬 Linux + Intel CPU（非 WSL2）**：用 `perf record -e intel_pt// -- /bin/ls` 記錄一個簡單程式的 trace，再用 `perf script --no-itrace` 解碼，數一下解出幾條邊。

---

## 本章重點

- Intel PT 在 CPU 執行期間記錄分支決定（TNT packet）和間接跳躍目的地（TIP packet），不需要插樁
- 解碼需要 binary image + libipt，輸出 (from, to) 邊列表，可以直接用 AFL 雜湊函數填 bitmap
- Overhead ~3–10%，遠低於 QEMU/DBI 方案；精度受 PT buffer 容量限制（溢出會丟失 coverage）
- WSL2 / 多數雲端環境遮蔽了 PT 能力，需要裸金屬 Linux 或支援 nested PT 的 VM
- 最佳使用場景：closed binary、kernel、hypervisor——無法插樁但需要 coverage 的目標

---

## 自我檢核

- [ ] Intel PT 記錄什麼資訊？不記錄什麼？
- [ ] TNT packet 和 TIP packet 各自記錄什麼？
- [ ] 從 PT packet stream 到 AFL edge bitmap 需要哪些步驟？
- [ ] PT buffer 溢出會造成什麼問題？有什麼緩解方法？
- [ ] 什麼情況下選 Intel PT，什麼情況下選軟體插樁？

---

## 延伸閱讀

1. **kAFL: Hardware-Assisted Feedback Fuzzing for OS Kernels**（Schumilo et al., USENIX Security 2017）
   - 讀 Section 4（Intel PT-Based Coverage Tracing）——Intel PT 用在 fuzzing 的第一個系統性論文，解釋了 packet 格式、buffer 管理、coverage 對映的完整方法，是本章的主要技術來源
   - https://www.usenix.org/conference/usenixsecurity17/technical-sessions/presentation/schumilo

2. **Intel 64 and IA-32 Architectures Software Developer's Manual, Volume 3, Chapter 35（Intel PT）**
   - 讀 Chapter 35.4（Packets）——Intel PT 的 authoritative spec，TNT/TIP 的 bit-level 格式定義在這裡，是所有解碼器的依據；按需查閱，不用全讀
   - https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html

3. **libipt：Intel Processor Trace Decoder Library**
   - 讀 `README.md` 和 `doc/` 目錄——Intel 官方解碼庫的文件和範例，`ptdump` 工具源碼是理解 packet 格式的最好切入點
   - https://github.com/intel/libipt

---

→ [Ch 31 snapshot 機制](./31-snapshot-mechanics.md)
