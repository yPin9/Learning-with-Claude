# Ch 6 — 執行期 instrumentation：沒原始碼怎麼辦

> 目標：說明三種 runtime instrumentation 方式 — QEMU mode 的 TCG basic-block translation、Frida mode 的 Stalker dynamic rewriting、Unicorn mode 的 user-supplied harness；解釋它們各自的 overhead 與適用場景。

## 問題重設

Ch 5 講的是「我有 source，讓 compiler 幫我插樁」。但現實世界的 fuzz target 不少是 closed-source：商用軟體、firmware、閉源 library、商用 SDK。你手上只有 binary，該怎麼讓它寫 bitmap？

三個選擇：

1. **QEMU mode**：用 QEMU user-mode emulator 跑 binary，在它把 target code 翻譯成 host code 時偷插 instrumentation。
2. **Frida mode**：用 Frida 的 Stalker 在執行時動態 rewrite basic block，把 instrumentation 塞進去。
3. **Unicorn mode**：把 target 的某段 raw code 抽出來，用 Unicorn engine 在 emulator 裡跑，你寫 harness。

全部殊途同歸：最終都要把 `trace_bits[prev ^ cur]++` 寫進那塊 SHM。

## QEMU mode

### QEMU 在做什麼

QEMU user-mode emulator（`qemu-x86_64`、`qemu-arm` 等）是個 **動態二進位翻譯器**：它把 guest architecture 的指令一段一段翻成 host architecture 的指令，用 JIT 式機制執行。中間那層叫 **TCG**（Tiny Code Generator）。

流程大致：

```
guest binary  ─read─▶  guest instruction stream
                          │
                          │  TCG frontend: guest isa → TCG ops (中間 IR)
                          ▼
                       TCG ops
                          │
                          │  TCG backend: TCG ops → host isa
                          ▼
                       host machine code ──cache──▶ translation block (TB)
                          │
                          ▼
                       jump to TB, run
```

每個 TB 對應一個 basic block 左右的 guest code。QEMU 快取 TB 以便後續重複使用。

### AFL 的 patch

AFL++ 的 `qemu_mode/` 是一個被大幅 patch 過的 QEMU 5.x fork。在 TCG frontend 產生一個 TB 時，會做：

1. 算出這個 TB 的 guest address（就是 `cur_loc` 的來源，但現在是 real address，不再是隨機值）。
2. 在 TB 的開頭 TCG ops 前面插入一段 TCG micro-ops，邏輯等同於：
   ```
   tmp = prev_loc ^ (tb_pc & MAP_MASK);
   __afl_area_ptr[tmp]++;
   prev_loc = (tb_pc & MAP_MASK) >> 1;
   ```
3. 這段 TCG ops 會被 TCG backend 一併翻成 host code，和 target 指令融合在同一個 TB 裡執行。

**優勢**：target 指令走到哪，instrumentation 就跟到哪。完整的 edge coverage。

**代價**：
- QEMU 本身的 dynamic translation overhead：2–5x 慢於原生執行。
- TB cache 預熱需要時間，冷啟動特別慢（AFL 的 forkserver 讓第二次之後快很多，但還是明顯慢過編譯期插樁）。
- bitmap 用 `tb_pc & MAP_MASK` 映射，和 Ch 4 討論的一樣會 collision。

### compare-transform 也在 QEMU 裡

`qemu_mode/` 不只有 coverage，也移植了 laf-intel 的 compare 拆分 — 遇到 64-bit compare 會在 TCG 裡拆成 byte-wise。配合 CMPLOG 的 QEMU 版，效果不差。

### 跑法

```bash
# build AFL++ 時 make distrib 會一併 build QEMU mode
afl-fuzz -Q -i seeds/ -o out/ -- ./closed_source_binary @@
#         ^^ 開 QEMU mode
```

AFL 會啟動 `afl-qemu-trace` 來跑 target。

### 適用場景

- 有 binary、沒 source。
- 支援的 guest 架構：x86_64、i386、arm、aarch64、mips、mips64、sparc...
- Target 是 standalone binary（不是 kernel 模組、不是 driver）。

## Frida mode

### Frida 在做什麼

Frida 是個著名的 dynamic instrumentation framework（跟 QEMU 不同，它在原生 CPU 上跑，不是 emulator）。核心元件叫 **Stalker** — 在執行時攔截 basic block，把它複製到自己的 cache 裡，在複製品的開頭插入你要的 instrumentation，然後把控制流跳到複製品。

```
原程式            Stalker cache
─────             ────────────
BB1 ─┐            
     │  dispatch  ┌─▶ BB1' = [instrumentation] + BB1 copy
     └──────────▶ │   end: dispatch back to Stalker
                  │
BB2 ─┐            
     │            └─▶ BB2' = [instrumentation] + BB2 copy
     └──────────▶
```

AFL++ 的 `frida_mode/` 就是一個 Frida Gadget，把 `afl-frida-trace.so` preload 到 target，hook 進 Stalker，把 instrumentation 改寫成 AFL 的 bitmap 邏輯。

### 跑法

```bash
afl-fuzz -O -i seeds/ -o out/ -- ./closed_source_binary @@
#         ^^ 開 Frida mode
```

（`-O` 是「persistent mode under frida」的旗標）

### 和 QEMU 比

| 維度 | QEMU mode | Frida mode |
|---|---|---|
| 需不需要 emulator | 要，dynamic translation | 不要，原生執行 |
| Overhead | 2–5x | 5–10x |
| 架構支援 | 多（靠 QEMU 支援） | 靠 Frida 支援，主流 OK |
| Build 負擔 | 要 build QEMU | 只要 Frida .so |
| 跨平台（Windows） | 有 patch 但不穩 | 較好 |
| 支援 macOS / iOS | 差 | 好（Frida 強項） |

一般規則：**Linux 優先用 QEMU，Windows / macOS / mobile 優先用 Frida**。

## Unicorn mode

### Unicorn 是什麼

Unicorn 是從 QEMU 抽出來的輕量 CPU emulator library — 它只做「跑一段 raw 機器碼」，不管 OS、syscall、ELF loader。你提供：

- 一塊 buffer，裡面是機器碼。
- 記憶體佈局（mmap 哪些 region）。
- 起始 PC 與終止條件。

然後 Unicorn 幫你跑完。

### 為什麼需要 Unicorn mode

有些 fuzz target 根本不是 OS 層的 binary：
- firmware 的某個函式（raw binary blob，無 header）
- bootloader / MBR
- 虛擬機 bytecode（dalvik、JVM）
- 嵌入式 RTOS 片段

這些東西用 QEMU 跑不起來（沒 ELF、沒 syscall）、用 Frida 也不行。唯一選擇是**自己寫 harness**：用 Unicorn 設好 memory、把 input 塞進某個 register / buffer、跑到某個 address 停下。

Unicorn mode 的 harness 大致長這樣（Python）：

```python
from unicornafl import UcAfl

uc = UcAfl(UC_ARCH_ARM, UC_MODE_ARM)
uc.mem_map(0x00000000, 0x10000, UC_PROT_ALL)
uc.mem_write(0x00000000, firmware_blob)
uc.reg_write(UC_ARM_REG_SP, 0x0F000)

def place_input(uc, input_bytes, _iteration, _data):
    uc.mem_write(0x1000, input_bytes)   # 把 fuzzer 生的 input 放進特定地址
    return True

uc.afl_fuzz(
    input_file="@@",
    place_input_callback=place_input,
    exits=[0x20000],   # 跑到這裡算結束
    iters=10000,
)
```

Unicorn engine 內部也是 TCG，每個 block 翻譯時 AFL++ 的 patch 讓它寫 bitmap — 機制和 QEMU mode 類似。

### 適用場景

| 場景 | 用 Unicorn mode |
|---|---|
| IoT firmware 某函式 fuzzing | ✓ |
| 獨立 algorithm（crypto、checksum） | ✓ |
| Shellcode 分析 | ✓ |
| 完整 ELF binary | ✗（用 QEMU） |

## 其他路線：Intel PT / Nyx / CoreSight

還有幾條相對進階的 runtime 路線，AFL++ 支援程度不一：

- **Intel PT**（Processor Trace）：Intel CPU 的硬體級 branch trace。Honggfuzz 是這條路線的代表。AFL++ 有 `Nyx mode` 用到 PT 的 packet，但 setup 門檻高。
- **Nyx mode**：KVM-based snapshot fuzzing，每次 iteration 把 VM 還原到快照。適合 kernel fuzzing、複雜狀態的 target。Sadelik 等 2021 paper 起的。
- **CoreSight**（ARM 對應的硬體 trace）：實驗性，AFL++ 沒內建。

這些都超出入門範圍，知道存在、知道關鍵字就夠。

## 效能對比（粗略量級）

假設編譯期 instrumentation 跑 10000 exec/s，其他 mode 的相對速度：

| Mode | 相對 exec/s | 備註 |
|---|---|---|
| `afl-clang-lto` | 1.0x（10000） | 基準 |
| `afl-clang-fast` PCGUARD | 0.9x | 稍慢於 LTO |
| QEMU mode | 0.2–0.4x | 熱起來後 |
| Frida mode | 0.1–0.2x | 依平台差異大 |
| Unicorn mode | 0.3–0.5x | 視 harness 複雜度 |
| Nyx mode | 0.05–0.2x | Snapshot restore 成本 |

**有 source 絕對先用編譯期**。Runtime 是下下策。

## 常見誤解

- **「QEMU mode 和完整 system emulation 一樣」**：不一樣。AFL++ 用的是 **user-mode QEMU**（跑單一 user process），不是 system-mode（跑整個 OS）。功能差很多。
- **「Frida mode 比 QEMU mode 新、一定比較好」**：不見得。Frida 在 Linux 下通常比 QEMU 慢，但在 macOS、Windows、mobile 比較穩。看平台選。
- **「沒 source 就用 QEMU mode」**：更好的路徑有時候是**反組譯 + 寫 harness**。例如只想 fuzz 某 library 的 parse 函式，與其整 binary 跑，不如拿 IDA 找到函式簽章、用 `dlsym` 呼叫、讓它像 in-process 跑。

## 自我檢核

- [ ] 能解釋 QEMU mode 為什麼 overhead 2–5x（因為 dynamic translation）
- [ ] 知道 QEMU mode 在 TCG 哪一層插 instrumentation
- [ ] 能說出 Frida mode 和 QEMU mode 在什麼平台各有優勢
- [ ] 知道 Unicorn mode 是「自己寫 harness」的場景，不是通用替代
- [ ] 記得有 source 永遠先用編譯期插樁

下一章講 AFL 最漂亮的一個設計 — forkserver 為什麼能讓每秒 iteration 數從幾百變成幾千。

→ [Ch 7 Forkserver：AFL 最漂亮的設計](./07-forkserver.md)
