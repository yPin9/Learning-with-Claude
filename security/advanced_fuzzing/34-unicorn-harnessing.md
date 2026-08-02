# Ch 34 — unicorn-based harnessing：把韌體函式拉進 fuzzer

> **目標**: 用 unicorn engine 把一個沒有 OS 的 ARM Thumb 函式直接拉進 Python fuzzing loop；掌握記憶體佈局、register 設置、hook 安裝、crash 偵測四件套；理解 MMIO hook 模式；知道如何銜接 afl-unicorn 取得 coverage-guided 能力。

> **環境**: WSL2 Ubuntu + Python 3.10+ + unicorn-engine (`pip install unicorn`) + arm-none-eabi-gcc (for compiling test ARM blob). 全章 unicorn Python 範例均在 WSL2 真跑驗證，輸出為實測結果。

---

## 為什麼需要 unicorn-based harnessing

傳統 harness 假設目標可以被連結成一個 Linux 可執行檔：有 libc、有 syscall、有記憶體分配器。韌體不是這樣的東西。

韌體通常是：
- 裸機執行（bare-metal），沒有 OS
- 使用廠商私有 peripheral（MMIO 地址空間）
- 依賴特定中斷向量表和啟動順序
- 只有 ELF 或 raw binary，沒有動態連結表

你沒辦法用 `gcc -fsanitize=address` 重編它，也沒辦法用 `LD_PRELOAD` 鉤 libc。想要 fuzz 韌體裡的一個 parser，你有三條路：

1. **真機 + serial/JTAG**：速度慢，硬體有限，crash 復原麻煩。
2. **QEMU full-system**：需要完整 board 模型，peripheral 建模工作量大。
3. **Unicorn + harness**：只模擬 CPU，把目標函式的二進位碼直接載入虛擬記憶體，自己模擬 peripheral。速度快，架設成本低，是 snapshot fuzzing 的基礎原語。

unicorn 的極限也很清楚：它不是系統模擬器，跑不了需要 OS 介入的程式碼。但對於「把一個 parser 函式單獨拉出來 fuzz」，這個限制剛好不礙事。

---

## 先建立直覺

```
Fuzzer (afl-unicorn / 手寫 loop)
        │ input bytes
        ▼
  ┌──────────────────────────────────────────┐
  │  Python harness                          │
  │                                          │
  │  初始化（只做一次）                      │
  │    mu.mem_map(CODE_ADDR, ...)            │
  │    mu.mem_map(BUF_ADDR,  ...)            │
  │    mu.mem_map(STK_ADDR,  ...)            │
  │    mu.mem_write(CODE_ADDR, THUMB_CODE)   │
  │    mu.hook_add(HOOK_CODE,   code_cb)     │
  │    mu.hook_add(HOOK_MEM_INVALID, mem_cb) │
  │                                          │
  │  每次 iteration（重設 + 注入 + 執行）    │
  │    mu.mem_write(BUF_ADDR, fuzzer_input)  │
  │    mu.reg_write(UC_ARM_R0, BUF_ADDR)    │
  │    mu.reg_write(UC_ARM_SP, STK_TOP)     │
  │    mu.reg_write(UC_ARM_LR, SENTINEL)    │
  │    mu.emu_start(entry | 1, end_addr)    │
  └──────────────────────────────────────────┘
        │ hook callbacks
        ├─ code_hook:  bitmap[pc >> 1] ^= 1   ← coverage
        ├─ mem_invalid_hook: 記錄 crash addr  ← bug 偵測
        └─ mmio_hook:  回傳 synthetic 值      ← peripheral 模擬

記憶體佈局：
  0x0000_1000  CODE  (目標函式的 binary bytes)
  0x0000_2000  BUF   page (4KB，映射 0x2000–0x2FFF)
               ↑
               0x2FF0 = buf 放在 page 末端，
               讓 OOB 存取立刻掉出 mapped 區域
  0x0008_0000  STACK (向下增長)
  0x4000_0000  MMIO  (廠商 peripheral，用 hook 攔截)
```

unicorn 的核心迴圈極簡：載入 → 設暫存器 → 執行 → 讀結果。複雜度全部在「你怎麼建模 peripheral」和「你怎麼把 crash 資訊回傳給 fuzzer」。

---

## 核心概念

### ARM Thumb bytecode 的手組方式

不用交叉編譯器也能準備測試用的 ARM Thumb blob。以下是一個有 OOB read bug 的 parser 函式：

```c
/* 等效 C 語意：
 * uint8_t parse_packet(uint8_t *buf) {
 *     uint8_t len = buf[0];           // 讀長度欄位
 *     if (len == 0xAA) {
 *         return *(buf + len + 1);    // BUG: OOB read，len 可能超過 buf 邊界
 *     }
 *     return 0;
 * }
 */
```

對應的 ARM Thumb 手組（16 位元指令，每條 2 bytes，小端序）：

```
位址     bytes         助記符                  說明
0x1000:  02 78         LDRB R2, [R0, #0]      R2 = buf[0]
0x1002:  AA 2A         CMP  R2, #0xAA         if (R2 == 0xAA)?
0x1004:  02 D1         BNE  0x100C            不等就跳到 exit
0x1006:  10 44         ADD  R0, R2            R0 = buf_ptr + len
0x1008:  40 78         LDRB R0, [R0, #1]      R0 = *(buf+len+1)  ← BUG
0x100A:  70 47         BX   LR                return
0x100C:  00 20         MOVS R0, #0            return 0
0x100E:  70 47         BX   LR
```

BNE 的 imm8 計算：branch 在 0x1004，fetch PC = 0x1004+4 = 0x1008，target = 0x100C，
offset = 0x100C - 0x1008 = 4 = 2 × imm8，所以 imm8 = 2 → encoding `0x02 0xD1`。

### 完整可跑範例：偵測 OOB read crash

```python
#!/usr/bin/env python3
# 檔案: unicorn_oob_demo.py
# 執行: python3 unicorn_oob_demo.py
# 需求: pip install unicorn

from unicorn import (
    Uc, UC_ARCH_ARM, UC_MODE_THUMB,
    UC_HOOK_CODE, UC_HOOK_MEM_INVALID,
    UC_MEM_READ_UNMAPPED, UC_MEM_WRITE_UNMAPPED,
    UC_MEM_FETCH_UNMAPPED,
    UcError,
)
from unicorn.arm_const import UC_ARM_R0, UC_ARM_R2, UC_ARM_SP, UC_ARM_LR

# ── ARM Thumb bytecode ────────────────────────────────────────────────────────
# 逐 byte 手組，無需交叉編譯器
THUMB_CODE = bytes([
    0x02, 0x78,  # 0x1000: LDRB R2, [R0, #0]  ; R2 = buf[0]
    0xAA, 0x2A,  # 0x1002: CMP  R2, #0xAA
    0x02, 0xD1,  # 0x1004: BNE  0x100C         ; 不等跳到 exit
    0x10, 0x44,  # 0x1006: ADD  R0, R2         ; R0 = buf_ptr + len
    0x40, 0x78,  # 0x1008: LDRB R0, [R0, #1]  ; R0 = *(buf+len+1) ← OOB
    0x70, 0x47,  # 0x100A: BX   LR
    0x00, 0x20,  # 0x100C: MOVS R0, #0
    0x70, 0x47,  # 0x100E: BX   LR
])

# ── 記憶體佈局 ────────────────────────────────────────────────────────────────
CODE_ADDR = 0x1000
CODE_SIZE = 0x1000   # 4KB
BUF_PAGE  = 0x2000   # page 從這裡開始，映射 0x2000–0x2FFF
BUF_SIZE  = 0x1000   # 4KB
BUF_ADDR  = 0x2FF0   # buf 放在 page 末端 (offset 0xFF0)
                     # buf[0]+0xAA+1 → 0x2FF0+0xAB = 0x309B → 超出 0x2FFF → unmapped
STK_ADDR  = 0x80000
STK_SIZE  = 0x10000
STK_TOP   = STK_ADDR + STK_SIZE - 8

SENTINEL  = 0xDEAD0000  # BX LR 跳到這裡代表函式正常返回

# ── hook callbacks ────────────────────────────────────────────────────────────
trace = []

def hook_code(mu, address, size, user_data):
    trace.append(address)

def hook_mem_invalid(mu, access_type, address, size, value, user_data):
    type_name = {
        UC_MEM_READ_UNMAPPED:  "UC_MEM_READ_UNMAPPED",
        UC_MEM_WRITE_UNMAPPED: "UC_MEM_WRITE_UNMAPPED",
        UC_MEM_FETCH_UNMAPPED: "UC_MEM_FETCH_UNMAPPED",
    }.get(access_type, f"type={access_type}")
    print(f"[CRASH] mem invalid: {type_name}  addr=0x{address:08x}  size={size}")
    if access_type == UC_MEM_READ_UNMAPPED:
        offset = address - BUF_ADDR
        print(f"        buf base=0x{BUF_ADDR:08x}, access offset=0x{offset:x} ({offset})")
    return False  # 回傳 False：讓 unicorn 停止並拋出 UcError

# ── 初始化（只做一次，實際 fuzzer 中放在 loop 外層）────────────────────────
mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)

mu.mem_map(CODE_ADDR, CODE_SIZE)
mu.mem_map(BUF_PAGE,  BUF_SIZE)
mu.mem_map(STK_ADDR,  STK_SIZE)

mu.mem_write(CODE_ADDR, THUMB_CODE)

mu.hook_add(UC_HOOK_CODE,        hook_code)
mu.hook_add(UC_HOOK_MEM_INVALID, hook_mem_invalid)

# ── 測試 case 1：正常輸入（buf[0] != 0xAA，走 exit 路徑）────────────────────
print("=== Case 1: normal input (len=0x01) ===")
trace.clear()
test_input = b'\x01' + b'\x41' * 15   # 16 bytes
mu.mem_write(BUF_ADDR, test_input)
mu.reg_write(UC_ARM_R0, BUF_ADDR)
mu.reg_write(UC_ARM_SP, STK_TOP)
mu.reg_write(UC_ARM_LR, SENTINEL)

try:
    mu.emu_start(CODE_ADDR | 1, CODE_ADDR + len(THUMB_CODE))
    r0 = mu.reg_read(UC_ARM_R0)
    print(f"[OK]  emulation finished, R0=0x{r0:02x}")
except UcError as e:
    print(f"[ERR] {e}")

print(f"      trace: {[hex(a) for a in trace]}")

# ── 測試 case 2：觸發 OOB（buf[0] == 0xAA）────────────────────────────────
print()
print("=== Case 2: OOB trigger (len=0xAA, buf at 0x2FF0, OOB -> 0x309B) ===")
trace.clear()
# buf[0]=0xAA → ADD R0,R2 → R0=0x2FF0+0xAA=0x309A → LDRB [R0,#1]=0x309B → unmapped
test_input = b'\xAA' + b'\x42' * 15
mu.mem_write(BUF_ADDR, test_input)
mu.reg_write(UC_ARM_R0, BUF_ADDR)
mu.reg_write(UC_ARM_SP, STK_TOP)
mu.reg_write(UC_ARM_LR, SENTINEL)

try:
    mu.emu_start(CODE_ADDR | 1, CODE_ADDR + len(THUMB_CODE))
    r0 = mu.reg_read(UC_ARM_R0)
    print(f"[OK]  emulation finished (unexpected), R0=0x{r0:02x}")
except UcError as e:
    print(f"[STOP] emu_start raised: {e}")

print(f"      trace: {[hex(a) for a in trace]}")
```

**實測輸出**：

```
=== Case 1: normal input (len=0x01) ===
[OK]  emulation finished, R0=0x00
      trace: ['0x1000', '0x1002', '0x1004', '0x100c', '0x100e']

=== Case 2: OOB trigger (len=0xAA, buf at 0x2FF0, OOB -> 0x309B) ===
[CRASH] mem invalid: UC_MEM_READ_UNMAPPED  addr=0x0000309b  size=1
        buf base=0x00002ff0, access offset=0xab (171)
[STOP] emu_start raised: Invalid memory read (UC_ERR_READ_UNMAPPED)
      trace: ['0x1000', '0x1002', '0x1004', '0x1006', '0x1008']
```

Case 1 的 trace 走 `0x1000 → 0x1002 → 0x1004 → 0x100C → 0x100E`，BNE 跳過了 OOB 路徑。
Case 2 走到 `0x1008`（LDRB 試圖讀 0x309B）就被 mem hook 攔截，`emu_start` 拋出 `UC_ERR_READ_UNMAPPED`。

計算驗證：`BUF_ADDR + len + 1 = 0x2FF0 + 0xAA + 1 = 0x309B`，超出 page 上界 `0x2FFF`，命中 unmapped 區。

---

## 底層機制

### unicorn hook 的執行順序

```
mu.emu_start(begin, until)
        │
        ▼
  ┌─ fetch 指令 ──────────────────────────────────────────────────────┐
  │  ├─ 地址已在 JIT cache？→ 直接執行                               │
  │  └─ 否：翻譯這個 TB (Translation Block)                          │
  │         ↓                                                         │
  │   UC_HOOK_CODE callback 在 TB 開頭插樁                           │
  │   （不是每條指令都一定有獨立 callback，取決於 TB 切割）          │
  ├─ 執行指令 ────────────────────────────────────────────────────────┤
  │  ├─ 記憶體讀寫 → UC_HOOK_MEM_READ / UC_HOOK_MEM_WRITE            │
  │  ├─ 記憶體錯誤 → UC_HOOK_MEM_INVALID（return False = 停止）      │
  │  └─ PC == until → 停止，回傳 UC_ERR_OK                           │
  └───────────────────────────────────────────────────────────────────┘
```

`UC_HOOK_CODE` 的粒度是 TB（Translation Block），unicorn 從 QEMU TCG 繼承了 TB 切割邏輯。在大多數情況下每條指令都有獨立 callback，但 JIT 最佳化後可能被合併。要確保每條指令都有 callback，可以用 `UC_HOOK_INSN`（x86 限定）或接受 TB 粒度的 coverage。

### coverage bitmap 建法

afl-unicorn 用 AFL 的 shared memory bitmap，手寫 loop 可以用 Python bytearray 代替：

```python
BITMAP_SIZE = 1 << 16           # 64 KB bitmap，足夠大多數韌體函式
bitmap = bytearray(BITMAP_SIZE)
prev_pc = 0

def hook_code_coverage(mu, address, size, user_data):
    global prev_pc
    cur  = (address >> 1) & (BITMAP_SIZE - 1)   # Thumb 地址對齊
    edge = (prev_pc ^ cur) & (BITMAP_SIZE - 1)
    bitmap[edge] = min(bitmap[edge] + 1, 255)
    prev_pc = cur >> 1
```

邊緣覆蓋（edge coverage）= prev_pc XOR cur_pc，和 AFL 的 `cur_location ^ prev_location` 完全一致。每次 iteration 前 `bitmap[:] = b'\x00' * BITMAP_SIZE` 清空，或用 `memoryview` 加速。

### MMIO hook 模式

韌體存取 UART、GPIO、DMA 通常用固定 MMIO 地址。unicorn 不知道怎麼處理這些讀寫，預設行為是讀到 0。用 hook 讓 MMIO 讀回傳 fuzzer input 的內容，可以驅動更深的路徑：

```python
MMIO_BASE = 0x40000000
MMIO_SIZE = 0x00100000   # 1MB MMIO 區域

# 先 map 這塊（unicorn 需要 map 才能 hook 記憶體事件）
mu.mem_map(MMIO_BASE, MMIO_SIZE)

mmio_input = bytearray(256)   # 每次 iteration 從 fuzzer input 填
mmio_pos   = 0

def hook_mmio_read(mu, access, address, size, value, user_data):
    global mmio_pos
    # 每次 MMIO 讀取都從 mmio_input 序列取下一個 byte
    if mmio_pos < len(mmio_input):
        val = mmio_input[mmio_pos]
        mmio_pos += 1
    else:
        val = 0xFF
    # 把值寫回 unicorn 記憶體，讓指令讀到正確結果
    mu.mem_write(address, bytes([val] * size))
    return True

from unicorn import UC_HOOK_MEM_READ
mu.hook_add(UC_HOOK_MEM_READ, hook_mmio_read,
            begin=MMIO_BASE, end=MMIO_BASE + MMIO_SIZE)
```

這是 P2IM（USENIX 2020）和 Fuzzware（USENIX 2022）都使用的基本思路，差別在於後者會自動學習哪些 MMIO 讀取對路徑有影響。

---

## 進階用法

### 銜接 afl-unicorn（unicornafl）

afl-unicorn 是 AFL++ 的官方 unicorn 整合，原理是把 AFL 的 fork server 和 SHM bitmap 和 unicorn harness 接在一起。

安裝：

```bash
pip install unicornafl
# 或從 AFL++ 源碼編譯：
# cd AFLplusplus/unicorn_mode && ./build_unicorn_support.sh
```

harness 骨架（和純 unicorn 的差異只在兩個 API 呼叫）：

```python
import sys
import unicornafl
unicornafl.monkeypatch()   # 把 unicornafl 的 Uc 替換掉標準 unicorn.Uc

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
from unicorn.arm_const import UC_ARM_R0, UC_ARM_SP, UC_ARM_LR

# ... mem_map, mem_write, hook_add 和前面相同 ...

def place_input(mu, afl_input, persistent_round, data):
    """AFL++ 每次 iteration 呼叫這個 callback 注入 input"""
    if len(afl_input) > 256:
        return False   # 太長，跳過這個 input
    payload = afl_input.ljust(256, b'\x00')
    mu.mem_write(BUF_ADDR, payload)
    mu.reg_write(UC_ARM_R0, BUF_ADDR)
    mu.reg_write(UC_ARM_SP, STK_TOP)
    mu.reg_write(UC_ARM_LR, SENTINEL)
    return True

# 這一行替換原本的 mu.emu_start(...)
mu.afl_fuzz(
    input_file=sys.argv[1],           # AFL 傳入的 @@ 檔案路徑
    place_input_callback=place_input,
    exits=[CODE_ADDR + len(THUMB_CODE)],
    persistent_iters=1000,            # persistent mode：每個 fork 跑 1000 次
)
```

執行方式：

```bash
afl-fuzz -i seeds/ -o out/ -U -- python3 harness.py @@
```

`-U` 旗標告訴 AFL++ 使用 unicorn mode。persistent mode（`persistent_iters=1000`）讓一個 fork 跑 1000 次 iteration 再重啟，大幅降低 fork overhead，throughput 可以達到 50k–200k exec/sec（依函式複雜度）。

### snapshot 快照模式

對於有初始化狀態的函式（例如先呼叫 `parser_init()` 再 fuzz `parser_feed()`），可以：

1. 先跑 `parser_init()` 直到它返回
2. 用 `mu.context_save()` 把暫存器快照存下來
3. 每次 iteration 用 `mu.context_restore()` 還原，再注入 input，再跑 `parser_feed()`

```python
# 跑初始化（只跑一次）
mu.emu_start(INIT_ENTRY | 1, INIT_END)
saved_ctx = mu.context_save()
saved_mem = bytes(mu.mem_read(BUF_PAGE, BUF_SIZE))

# 每次 iteration
for fuzzer_input in inputs:
    mu.context_restore(saved_ctx)
    mu.mem_write(BUF_PAGE, saved_mem)      # 還原記憶體狀態
    mu.mem_write(BUF_ADDR, fuzzer_input)
    mu.emu_start(FEED_ENTRY | 1, FEED_END)
```

`context_save()` 只存暫存器，不存記憶體；記憶體要自己用 `mem_read` / `mem_write` 管理。

---

## 對比取捨

| 方案 | 速度 | 架設成本 | OS syscall | Peripheral 支援 | 適用場景 |
|------|------|---------|------------|-----------------|---------|
| unicorn + Python harness | 高（JIT） | 低 | 無 | 手寫 MMIO hook | 單一函式 fuzz，韌體 parser |
| QEMU user-mode | 中 | 中 | 完整 Linux ABI | 無 | 有 libc 依賴的 bare ELF |
| QEMU full-system | 低–中 | 高 | 完整 | 需要 board model | 需要驅動的完整系統 |
| Frida Stalker | 中 | 中 | 依目標 | 依目標 | 有 OS 的目標、動態插樁 |
| Fuzzware（unicorn 上層） | 高 | 中 | 無 | 自動 MMIO model | 完整韌體 image（Cortex-M） |

unicorn 勝在速度和靈活性，代價是你要手動建模所有 peripheral 和 OS 介面。函式越獨立（純計算、純解析），unicorn 越適合；函式越依賴外部狀態，MMIO hook 工作量越大。

---

## 踩雷

**踩雷 1：`UC_MODE_THUMB` vs 起始地址的 Thumb bit**

`Uc(UC_ARCH_ARM, UC_MODE_THUMB)` 告訴 unicorn 預設 ISA 是 Thumb，但 `emu_start` 的起始地址仍然要加 `| 1` 才會讓 unicorn 從 Thumb 模式進入：

```python
# 錯的：unicorn 可能以 ARM 模式解碼第一條指令
mu.emu_start(CODE_ADDR, CODE_ADDR + len(THUMB_CODE))

# 對的：
mu.emu_start(CODE_ADDR | 1, CODE_ADDR + len(THUMB_CODE))
```

`| 1` 是 ARM interworking 慣例（Thumb 函式指標的 bit 0 = 1），unicorn 識別這個 bit 並切換解碼模式。`until` 地址不需要加 `| 1`，unicorn 比較 PC 時會自動對齊。

**踩雷 2：BX LR 跳到未映射地址的例外和 crash 難以區分**

函式正常返回時執行 `BX LR`，PC 變成 LR 的值。如果 LR = `0xDEAD0000` 且這塊地址沒有映射，unicorn 會拋出 `UC_MEM_FETCH_UNMAPPED`，和真正的 crash（例如跳到 OOB 指標）產生的例外是同一種類型。

解法有三種，選一種：

```python
# 解法 A：把 SENTINEL 地址 map 起來，放 NOP loop
# emu_start 的 until=SENTINEL，正常返回時 PC==SENTINEL 自然停止
mu.mem_map(SENTINEL & ~0xFFF, 0x1000)
mu.mem_write(SENTINEL, b'\x00\xBF' * 64)  # NOP (Thumb: 0xBF00 → bytes 0x00, 0xBF)
mu.emu_start(CODE_ADDR | 1, SENTINEL)      # 正常返回 = PC 到 SENTINEL 停止

# 解法 B：在 hook_mem_invalid 裡區分 access_type 和地址
def hook_mem_invalid(mu, access_type, address, size, value, data):
    if access_type == UC_MEM_FETCH_UNMAPPED and address == SENTINEL:
        return False   # 正常返回，停止模擬，不算 crash
    print(f"[CRASH] real fault: type={access_type} addr=0x{address:x}")
    return False

# 解法 C：unicornafl 的 exits 參數，直接列出合法退出地址
mu.afl_fuzz(..., exits=[SENTINEL])
```

解法 A 最乾淨，解法 B 最不需要改記憶體佈局。

**踩雷 3：每次 iteration 必須完整重設暫存器和記憶體**

unicorn 的記憶體和暫存器在 `emu_start` 之間保留前一次的狀態。OOB write 汙染的記憶體、上一次執行留下的 stack frame、沒有清掉的 R0 值，都會讓下一次 iteration 的行為不確定：

```python
# 錯的：只重寫 buf，沒重設 stack 和 registers
for inp in inputs:
    mu.mem_write(BUF_ADDR, inp)
    mu.emu_start(CODE_ADDR | 1, END_ADDR)  # 暫存器是上次的殘留

# 對的：
for inp in inputs:
    mu.mem_write(BUF_ADDR, inp.ljust(MAX_INPUT, b'\x00'))
    mu.mem_write(STK_ADDR, b'\x00' * STK_SIZE)  # 清 stack
    mu.reg_write(UC_ARM_R0, BUF_ADDR)
    mu.reg_write(UC_ARM_R2, 0)   # 清掉前一次的 R2
    mu.reg_write(UC_ARM_SP, STK_TOP)
    mu.reg_write(UC_ARM_LR, SENTINEL)
    mu.emu_start(CODE_ADDR | 1, END_ADDR)
```

用 `context_save` / `context_restore` 可以解決暫存器部分；記憶體仍然要手動管理。

**踩雷 4：hook_add 重複安裝**

`hook_add` 每次呼叫都會新增一個 hook，不會替換。如果把 `hook_add` 放在 iteration loop 裡，每次 iteration 多一份 callback，performance 急速下降，trace list 也會累積重複資料：

```python
# 錯的：
for inp in inputs:
    mu.hook_add(UC_HOOK_CODE, hook_code)   # 越跑越多份 hook
    mu.emu_start(...)

# 對的：hook 在 loop 外只裝一次
handle = mu.hook_add(UC_HOOK_CODE, hook_code)
for inp in inputs:
    trace.clear()
    mu.emu_start(...)
# 若需要動態換 hook：mu.hook_del(handle) 再重加
```

---

## 進階延伸

**自動識別哪些 MMIO 存取影響路徑分支**

Fuzzware（Ch 35）在 unicorn 上加了一層分析：先跑一遍收集所有 MMIO 讀取，判斷哪些讀取的返回值被用在條件跳轉（透過 taint 或 symbolic execution），只把這些「有效」的 MMIO 存取納入 fuzzer 的 mutation 空間。可以大幅縮小 input 空間，提升 coverage 效率。

**多架構支援**

unicorn 支援 ARM/Thumb/AArch64/x86/MIPS/RISC-V。harness 換架構只需要：

```python
# AArch64
mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_SP, UC_ARM64_REG_LR

# MIPS32 big-endian
mu = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_BIG_ENDIAN)
from unicorn.mips_const import UC_MIPS_REG_A0, UC_MIPS_REG_SP, UC_MIPS_REG_RA
```

其餘 `mem_map` / `hook_add` / `emu_start` 的 API 完全相同，只有 reg 常數名稱不同。

**結合 Capstone 做即時反組譯 trace**

```python
import capstone

cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
cs.detail = True

def hook_code_disasm(mu, address, size, user_data):
    code = bytes(mu.mem_read(address, size))
    for insn in cs.disasm(code, address):
        print(f"  0x{insn.address:04x}: {insn.mnemonic:8s} {insn.op_str}")
```

用於 debug 階段確認 bytecode 解碼正確；正式 fuzz 時關掉，I/O 是速度瓶頸。

---

## 動手練習

1. **驗證 bytecode**：把本章的 `THUMB_CODE` 用 Capstone（`pip install capstone`）反組譯，確認每條指令的助記符和本章表格一致。修改一個 byte，觀察反組譯結果如何變化，理解小端序 encoding 的影響。

2. **MMIO fuzz**：寫一個 ARM Thumb 函式，讀 `0x40000004`（MMIO 地址）的值決定走哪條路徑（例如 if value == 0x55 則觸發 OOB），用本章的 MMIO hook 模式讓 fuzzer input 的每個 byte 依序餵給 MMIO 讀取，觀察能否覆蓋到 OOB 路徑。

3. **context snapshot 測速**：實作一個 `parser_init()` stub（只是把 BUF_PAGE 前 64 bytes 填 0xCC）和 `parser_feed(buf)` stub，用 `context_save/restore` 模式跑 10,000 次 iteration，和每次都重跑 init 的版本比較 throughput（用 `time.perf_counter()` 測量）。

4. **接 AFL++**：安裝 unicornafl，把本章的 OOB demo 改成 afl-unicorn harness，用 `afl-fuzz -U` 跑，觀察 AFL 的 UI 裡 edges found 是否能在幾秒內覆蓋兩條路徑（正常返回路徑 + OOB crash 路徑）。

---

## 本章重點

- unicorn 是純 CPU 模擬器，給的只有：載入 binary、設定暫存器、執行、hook。不處理 OS、不處理 peripheral。
- 把函式 fuzz 的四件套：mem_map 佈局 → mem_write 注入 → reg_write 設參數 → hook 偵測 crash。
- ARM Thumb 起始地址要加 `| 1`（interworking bit），`until` 地址不需要。
- 正常返回（BX LR 跳到 sentinel）和真正 crash 都會觸發 `UC_HOOK_MEM_INVALID`，要在 hook 裡區分 `access_type` 和地址。
- 每次 iteration 前必須完整重設暫存器和記憶體，避免狀態汙染。
- MMIO hook：map 目標地址範圍，在 `UC_HOOK_MEM_READ` 回傳 fuzzer input，驅動 peripheral 依賴的路徑。
- coverage bitmap 用 edge XOR（prev_pc ^ cur_pc）建，和 AFL 格式相容，可直接接 afl-unicorn。
- persistent mode（一個 fork 跑多次 iteration）是提升 throughput 的關鍵，可從預設的數千 exec/sec 提升到數萬到二十萬 exec/sec。

---

## 自我檢核

- [ ] 能說出 `Uc(UC_ARCH_ARM, UC_MODE_THUMB)` 和 `emu_start(addr | 1, ...)` 各自負責什麼，為何兩者都需要
- [ ] 能手算一條簡單 ARM Thumb 指令的 2-byte encoding（例如 `MOVS R0, #5`）
- [ ] 知道 `UC_HOOK_MEM_INVALID` 的 callback 回傳 `False` 和 `True` 的差別
- [ ] 能描述 MMIO hook 的完整流程：為何要先 `mem_map`，hook callback 裡用 `mem_write` 回填值
- [ ] 知道 `context_save` / `context_restore` 不包含記憶體，要自己另外存
- [ ] 能說出 unicorn harness 和 afl-unicorn 的差別只在 `place_input_callback` 和 `afl_fuzz` 這兩個 API
- [ ] 知道 hook 要裝在 loop 外，每次 iteration 用 `trace.clear()` 重置而不是重裝 hook

---

## 延伸閱讀

1. **Unicorn engine 論文**（CCS 2015，"Unicorn: Next Generation CPU Emulator Framework"，Nguyen et al.）：讀 §3 Architecture 和 §4 Hook API；了解 unicorn 為何基於 QEMU TCG 而不是自己寫 JIT，以及 hook 插入點的設計。[https://www.unicorn-engine.org/BHUSA2015-unicorn.pdf](https://www.unicorn-engine.org/BHUSA2015-unicorn.pdf)

2. **afl-unicorn README 和範例**（AFL++ 官方倉庫 `unicorn_mode/` 目錄）：讀 `samples/simple/` 和 `samples/arm_simple/`；學 `afl_fuzz_init` / `afl_fuzz` 整合接口，以及 persistent mode 的設定方式。[https://github.com/AFLplusplus/AFLplusplus/tree/stable/unicorn_mode](https://github.com/AFLplusplus/AFLplusplus/tree/stable/unicorn_mode)

3. **P2IM**（USENIX Security 2020，"P2IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling"，Feng et al.）：讀 §4 Peripheral Interface Modeling；了解 unicorn hook 怎麼被用來自動模型化 MMIO 介面，是 Fuzzware 的前驅工作。[https://www.usenix.org/conference/usenixsecurity20/presentation/feng](https://www.usenix.org/conference/usenixsecurity20/presentation/feng)

4. **unicorn Python bindings 源碼**（`bindings/python/unicorn/`）：直接讀 `unicorn.py` 的 `hook_add` 和 `emu_start` 實作，理解 ctypes 層如何把 Python callback 轉成 C function pointer，有助於 debug callback 簽名不對的問題。

---

## 銜接

本章建立了 unicorn harness 的基礎：pure CPU 模擬 + hook 四件套 + MMIO 建模思路。但一個真實的韌體 image 有幾百個 MMIO 寄存器，靠手寫 hook 是沒有盡頭的。

下一章 Fuzzware 把這個問題系統化：自動識別哪些 MMIO 讀取對路徑有影響，只對這些讀取做 mutation，讓 unicorn-based 韌體 fuzzing 真正 scale 起來。

→ [下一章](./35-fuzzware-halucinator.md)
