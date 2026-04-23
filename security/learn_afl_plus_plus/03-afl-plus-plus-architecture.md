# Ch 3 — AFL++ 架構總覽：一張大圖串起所有元件

> 目標：給出一張涵蓋 fuzzer process、shared memory bitmap、forkserver、target、各種 mode 的全景圖；之後每一章都是在 zoom in 這張圖的某一塊。

## 從兩個 process 開始看

AFL++ 最外層只有兩個 process：**fuzzer** 和 **target**。所有複雜性都是為了讓這兩邊高頻率、低成本地溝通。

```
┌────────────────────────┐           ┌────────────────────────┐
│    afl-fuzz (fuzzer)   │           │     target binary      │
│                        │           │                        │
│  - queue manager       │           │  - 你要 fuzz 的程式   │
│  - mutator             │           │  - 被 instrumentation  │
│  - scheduler           │           │    插樁過，每條 edge   │
│  - bitmap comparator   │           │    會寫一 byte 到 SHM  │
│  - UI                  │           │  - main() 前接管給     │
│                        │           │    forkserver          │
└────────────────────────┘           └────────────────────────┘
```

兩邊怎麼溝通？三條通道：

1. **Shared memory（SHM）**：64KB 的 bitmap。target 跑時 instrumentation 往這裡寫，fuzzer 讀。
2. **Forkserver pipe**：兩條 pipe（fd 198 與 199），做 control / status 訊號。
3. **檔案系統**：input 透過檔案（或 stdin）交給 target，output 進 `out/queue/`、`out/crashes/`。

## 全景圖

把整個架構攤平：

```
      ┌─────────────────────────── afl-fuzz ───────────────────────────┐
      │                                                                 │
      │  ┌────────────┐    ┌────────────┐    ┌────────────┐             │
      │  │  queue/    │───▶│  mutator   │───▶│ scheduler  │             │
      │  │  (on disk) │    │ det + havoc│    │ power sched│             │
      │  └────────────┘    └────────────┘    └────────────┘             │
      │        ▲                                  │                     │
      │        │ add if                           ▼                     │
      │        │ new coverage              ┌─────────────┐              │
      │        │                           │ run target  │              │
      │        │                           │ (1 iter)    │              │
      │        │                           └──────┬──────┘              │
      │        │                                  │                     │
      │        │                                  ▼                     │
      │        │                           ┌─────────────┐              │
      │        └───────────────────────────┤ has_new_bits│              │
      │                                    └──────┬──────┘              │
      │                                           │                     │
      │                                           ▼                     │
      │                                    ┌─────────────┐              │
      │                                    │  bitmap     │              │
      │                                    │   (virgin)  │              │
      │                                    └─────────────┘              │
      └────────────────┬──────────────────────────┬─────────────────────┘
                       │ SHM (64KB trace_bits)    │ pipes (fd 198/199)
                       ▼                          ▼
      ┌─────────────────────────── target ─────────────────────────────┐
      │                                                                │
      │  ┌─────────────────────────────────┐                           │
      │  │  __afl_start_forkserver()       │  ← 在 main() 前執行       │
      │  │  (afl-compiler-rt.o.c)          │                           │
      │  └──────────────┬──────────────────┘                           │
      │                 │ fork                                         │
      │                 ▼                                              │
      │          ┌──────────────────────┐                              │
      │          │  child (真的跑一次)  │                              │
      │          │                      │  ← 每條 edge 都會            │
      │          │  if (input_ok) {...} │     trace_bits[loc]++        │
      │          │  parse()             │                              │
      │          │  process()           │                              │
      │          │  ...                 │                              │
      │          │  exit                │                              │
      │          └──────────────────────┘                              │
      └────────────────────────────────────────────────────────────────┘
```

這張圖的每一塊都有對應章節：

| 區塊 | 章節 |
|---|---|
| queue 管理、cull_queue、favored | Ch 8 |
| mutator（det / havoc / splice） | Ch 9 |
| scheduler / power schedule | Ch 10 |
| dictionary → mutator | Ch 11 |
| CmpLog → mutator | Ch 12 |
| has_new_bits、bitmap | Ch 4 |
| forkserver protocol | Ch 7 |
| instrumentation（編譯期） | Ch 5 |
| instrumentation（執行期） | Ch 6 |
| persistent mode 改 loop 結構 | Ch 13 |

## 一次 iteration 的生命週期

Zoom in 到「跑一次 input」這個最小單位（`common_fuzz_stuff()` 在 `src/afl-fuzz-run.c`）：

```
fuzzer                           forkserver          child
───────                          ──────────          ─────
write(ctl_fd, &go, 4) ──────────▶
                                  fork() ─────────▶   (new process)
                                                      run target code
                                                      edges → trace_bits
                                                      exit(status)
                                  wait4() ◀────────   status
                                    │
                                    │
◀──────────── write(st_fd, &pid, 4)
◀──────────── write(st_fd, &status, 4)

read trace_bits from SHM
compute has_new_bits?
  yes → save to queue/ (讀完才落盤)
  crash? → save to crashes/
```

這個迴圈每秒跑 1000–10000 次，是 fuzzer 的熱迴圈。所有優化（forkserver、persistent mode、shared memory）都是為了讓這個迴圈更快。

## 幾個關鍵資料結構

### trace_bits（64KB bitmap）

就是一個 `u8 trace_bits[65536]`。target 插樁寫它，fuzzer 讀它。每個 byte 是一條 edge 的 hit count（分桶到 `1`、`2`、`3`、`4–7`、`8–15`、...，詳見 Ch 4）。

Shared memory ID 透過環境變數 `__AFL_SHM_ID` 傳給 target；target 的 `afl-compiler-rt.o` 會 `shmat()` 連上。

### virgin_bits

fuzzer 側另一份 64KB buffer，初始化為全 `0xFF`（「還沒被點亮過」）。每次 target 跑完，fuzzer 做 `has_new_bits()`：

```c
for (i = 0; i < MAP_SIZE; i++) {
    if (trace_bits[i] && (trace_bits[i] & virgin_bits[i])) {
        virgin_bits[i] &= ~trace_bits[i];
        // 有新 bit → interesting input
    }
}
```

這個 bitmap-AND 是整個 fuzzer 最常執行的操作之一。`src/afl-fuzz-bitmap.c` 有 SIMD 加速版本。

### queue_entry 結構

```c
struct queue_entry {
    u8 *fname;              // queue 檔案路徑
    u32 len;                // input 長度
    u8 cal_failed,          // calibration 有沒有失敗
       trim_done,           // 已經 trim 過
       was_fuzzed,          // 至少 fuzz 過一輪
       favored,             // 在 favored minset 裡
       passed_det;          // 做過 deterministic
    u64 exec_us,            // 單次執行時間
        handicap,           // 多晚才被找到
        depth;              // queue 深度
    u8 *trace_mini;         // 濃縮的 bitmap footprint
    u32 tc_ref;             // 被幾個 top_rated 引用
    struct queue_entry *next;
};
```

幾乎每個欄位都連接一個 scheduling 決策。`favored`、`handicap`、`depth` 是 Ch 10 power schedule 的輸入。

## 各種 Mode 塞在哪

這張圖是「有 source、編譯期插樁」的標準配置。其他 mode 只是換 instrumentation 來源：

```
                  ┌─ compile-time instrumentation (有 source)
                  │   └─ afl-clang-fast / afl-clang-lto / afl-gcc-fast
                  │      （寫 trace_bits 的 code 在編譯時插入）
                  │
  instrumentation─┤
   來源           │─ QEMU mode (只有 binary)
                  │   └─ afl-qemu-trace，TCG block 時插 instrumentation
                  │
                  │─ Frida mode (只有 binary、無需重編 QEMU)
                  │   └─ Frida Stalker 在執行時 rewrite basic blocks
                  │
                  │─ Unicorn mode (raw code / firmware / bootloader)
                  │   └─ 自己寫 harness，用 Unicorn engine emulate
                  │
                  └─ Nyx mode (kernel / hypervisor fuzzing)
                      └─ KVM-based snapshot，每次 restore 狀態
```

不管哪種 mode，最終都要填滿同一塊 `trace_bits`。fuzzer 那邊的程式碼完全不知道也不關心 instrumentation 是怎麼來的 — 只負責讀 bitmap、做決策。**這個介面的乾淨是 AFL 設計的核心漂亮之處**。

## CmpLog 是一條側支

CMPLOG 不在主 loop 上，它是**另一個 target binary**（用特殊旗標編譯，插 cmp operand log），fuzzer 在 redqueen 階段額外跑它來收 compare 的 operand 值：

```
fuzzer 主 loop:  mutate → 跑主 target → 檢查 bitmap
                           │
                           ▼ (如果發現不錯的 input)
                 redqueen 階段: 跑 CMPLOG target → 收 compare operand
                                → 用 operand 做替換式 mutation
                                → 再跑主 target
```

這個側支讓 AFL++ 能破 magic bytes（Ch 12 詳述）。

## 心智模型總結

如果只記一張圖，就記這個：

```
      input → [mutator] → 餵 target → [bitmap] → 有新 bit？→ 留住
                                                  沒 → 丟掉
                  ↑                                    │
                  └─────── 從 queue 挑下一個 ─────────┘
```

後面所有章節都是在這個骨架上加東西：
- 讓 `[mutator]` 更聰明（Ch 9, 11, 12, 14）
- 讓「餵 target」更便宜（Ch 7, 13）
- 讓 `[bitmap]` 更精確（Ch 4, 5, 6）
- 讓「挑下一個」更有效率（Ch 8, 10）
- 讓這整個迴圈能並行（Ch 16）

## 自我檢核

- [ ] 能畫出 fuzzer ↔ SHM ↔ forkserver ↔ target 的溝通圖
- [ ] 知道 `trace_bits` 和 `virgin_bits` 各自在哪邊、做什麼
- [ ] 能說出一次 iteration 最少發生幾次 `read/write` 系統呼叫（提示：兩條 pipe 通訊 + target 本身 I/O）
- [ ] 了解 instrumentation 來源（compile-time / QEMU / Frida / Unicorn / Nyx）都通往同一塊 `trace_bits`

下一章深入那塊 64KB bitmap — 它為什麼是 64KB、edge 怎麼映射、為什麼用 `prev_loc` shift trick。

→ [Ch 4 Edge coverage 原理](./04-edge-coverage-bitmap.md)
