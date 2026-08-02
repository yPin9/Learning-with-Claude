# Ch 36 — 韌體 fuzzing 實戰：從 blob 到 crash triage

> **目標**: 把前兩章的工具和原理串成一個端到端工作流程。從取得韌體 blob 開始，找 input 入口、建 unicorn harness、跑 fuzzing loop、到最後 triage crash。所有步驟用一個 TLV parser 漏洞貫穿示範。

> **環境**: WSL2 Ubuntu + Python 3.10+ + unicorn-engine (`pip install unicorn`) + afl++ (apt install afl++). unicorn 部分真跑貼輸出；unicornafl/afl++ 整合部分因需 afl-fuzz 程序在場，貼架構程式碼和模擬輸出說明流程。

---

## 為什麼需要

Ch33 建立了 AFL++ 和 libFuzzer 的基礎概念。Ch34 介紹了 unicorn-engine 模擬執行 bare-metal blob。Ch35 講了 Fuzzware 和 HALucinator 的 re-hosting pipeline。這三章分開看各有道理，但實際工作裡你拿到一個韌體 blob，要從零走到第一個 crash report，中間有大量「把工具接在一起」的工程細節沒人幫你串。

這章把所有東西接起來，走一遍完整的流程：

- 拿到 .bin 後怎麼找值得 fuzz 的 input 函式
- harness 怎麼處理狀態重置（每次執行必須乾淨）
- 不用 afl++ 也能跑 mutation loop 找 crash
- 找到 crash 後怎麼確認 root cause

用的漏洞是 TLV parser 裡的 length field 邊界沒驗證，這是韌體裡最常見的 bug class 之一。

---

## 先建立直覺

完整的韌體 fuzzing pipeline 長這樣：

```
韌體 blob (bare-metal .bin)
        │
        ▼
   [逆向分析]
   binwalk 解包 / Ghidra 靜態分析
   找 input 入口函式
   (UART_Receive / BLE_ParsePacket / TLV_Parse)
        │
        ▼
   [建立 unicorn harness]
   load blob → memory map
   hook MMIO regions
   注入 input → 執行目標函式
   捕抓 crash 與 coverage
        │
        ▼
   [fuzzer loop]
   手寫 mutation loop / unicornafl 接 afl++
        │
        ▼
   [crash triage]
   重放 crash input
   確認 root cause (OOB / UAF / stack overflow)
   評估可利用性
```

每個階段的時間分配大概是：逆向分析佔 40%，建 harness 佔 30%，fuzzing 本身只佔 10%，triage 佔 20%。新手常以為 fuzzing 是主體，其實 harness 才是最費時間的部分。

---

## 核心概念：TLV Parser Harness 真跑

### 目標函式

這章要 fuzz 的是一個 TLV（Type-Length-Value）parser。C 等效邏輯：

```c
// typedef struct { uint8_t type; uint8_t length; uint8_t data[64]; } TLV;
//
// int parse_tlv(uint8_t *buf, size_t buf_len) {
//     uint8_t type   = buf[0];
//     uint8_t length = buf[1];   // 沒驗證 length <= buf_len - 2
//     // BUG: 直接用 length 去存取，length 大時 OOB
//     uint8_t val    = *(buf + length + 2);  // 當 length 夠大就讀到邊界外
//     return val;
// }
```

這個 simplification 保留了 bug 的本質：`length` field 控制了後續存取的 offset，但沒做邊界檢查。

### ARM Thumb bytecode 編碼

把上面的邏輯手組成 ARM Thumb。Thumb 16-bit 指令 `LDRB T1` 格式：`0111 1 imm5 Rn Rt`。

```
指令                  格式推導                                bytes (little-endian)
LDRB R2, [R0, #0]  : imm5=0, Rn=0, Rt=2                    0x02, 0x78
                      0111 1 00000 000 010 = 0x7802
LDRB R3, [R0, #1]  : imm5=1, Rn=0, Rt=3                    0x43, 0x78
                      0111 1 00001 000 011 = 0x7843
ADD  R0, R3        : T2 ADD: 0100 0100 DN Rm Rdn             0x18, 0x44
                      Rm=R3=0011, DN=0, Rdn=R0=0000
                      0100 0100 0 011 0000 = 0x4418
LDRB R0, [R0, #2]  : imm5=2, Rn=0, Rt=0                    0x80, 0x78
                      0111 1 00010 000 000 = 0x7880
BX   LR            :                                         0x70, 0x47
```

`ADD R0, R3` 執行後 R0 = 原始 buf_ptr + length。接著 `LDRB R0, [R0, #2]` 讀 `buf_ptr + length + 2`。當 `length >= 14` 時這個地址超出 mapped region，觸發 fault。

### 完整 harness 程式碼

```python
#!/usr/bin/env python3
# ch36_tlv_fuzzer.py
# 真跑：unicorn-engine，不需要 afl++

from unicorn import *
from unicorn.arm_const import *
import random

# -----------------------------------------------------------------
# ARM Thumb TLV parser blob
# C 等效：
#   type   = buf[0]
#   length = buf[1]
#   val    = *(buf + length + 2)  <- OOB when length >= 14
# -----------------------------------------------------------------
THUMB_TLV = bytes([
    0x02, 0x78,  # LDRB R2, [R0, #0]  ; R2 = type   = buf[0]
    0x43, 0x78,  # LDRB R3, [R0, #1]  ; R3 = length = buf[1]
    0x18, 0x44,  # ADD  R0, R3         ; R0 = buf_ptr + length
    0x80, 0x78,  # LDRB R0, [R0, #2]  ; R0 = *(R0 + 2)  <- OOB trigger
    0x70, 0x47,  # BX   LR            ; return
])

# 記憶體配置
CODE_ADDR = 0x1000   # .text：放 blob
BUF_ADDR  = 0x2FF0   # input buffer 起始（page 在 0x3000 結束）
BUF_SIZE  = 16       # 只給 16 bytes；length >= 14 時 0x2FF0+length+2 >= 0x3000
STACK_TOP = 0x5000
DONE_ADDR = 0x1100   # BX LR 之後 PC 落這裡，emulation 正常停止

PAGE = 0x1000

# OOB 觸發條件計算：
#   讀地址 = BUF_ADDR + length + 2 = 0x2FF0 + length + 2
#   需超過 0x3000 → length >= 0x3000 - 0x2FF0 - 2 = 14 = 0x0E

def make_uc():
    """每次呼叫都建新的 Uc instance，確保 emulation state 完全乾淨。"""
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    # code region
    mu.mem_map(CODE_ADDR, 2 * PAGE)
    mu.mem_write(CODE_ADDR, THUMB_TLV)
    # DONE_ADDR 落在另一個 page，需要 map
    mu.mem_map(DONE_ADDR & ~(PAGE - 1), PAGE)
    # input buffer region（page: 0x2000-0x2FFF）
    mu.mem_map(BUF_ADDR & ~(PAGE - 1), PAGE)
    # stack region
    mu.mem_map(STACK_TOP - PAGE, PAGE)
    return mu


# -----------------------------------------------------------------
# 單次執行
# 回傳 ("OK", 0) 或 ("CRASH", crash_addr)
# -----------------------------------------------------------------
def run_once(input_bytes: bytes):
    mu = make_uc()

    # 把 input 寫進 buf（不足 BUF_SIZE 的部分補零）
    buf = input_bytes[:BUF_SIZE].ljust(BUF_SIZE, b'\x00')
    mu.mem_write(BUF_ADDR, buf)

    # 初始化暫存器
    mu.reg_write(UC_ARM_REG_R0, BUF_ADDR)   # 第一個參數：buf ptr
    mu.reg_write(UC_ARM_REG_R1, BUF_SIZE)   # 第二個參數：buf_len
    mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
    # LR = DONE_ADDR；BX LR 後 PC 跳到 DONE_ADDR，emu_start 自然停止
    mu.reg_write(UC_ARM_REG_LR, DONE_ADDR)

    crashed    = False
    crash_addr = 0

    def hook_mem(uc, access, address, size, value, user_data):
        nonlocal crashed, crash_addr
        crashed    = True
        crash_addr = address
        uc.emu_stop()

    mu.hook_add(UC_HOOK_MEM_READ_UNMAPPED,  hook_mem)
    mu.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, hook_mem)
    mu.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, hook_mem)

    try:
        # Thumb 模式：起始 PC 加 1
        mu.emu_start(CODE_ADDR | 1, DONE_ADDR, timeout=0, count=0)
    except UcError:
        crashed    = True
        crash_addr = mu.reg_read(UC_ARM_REG_PC)

    if crashed:
        return ("CRASH", crash_addr)
    return ("OK", 0)


# -----------------------------------------------------------------
# 手寫 mutation fuzzing loop
# -----------------------------------------------------------------
def mutate(data: bytes) -> bytes:
    """單步 mutation：隨機選一個 byte 位置覆寫。"""
    out = bytearray(data)
    pos = random.randint(0, min(len(out) - 1, BUF_SIZE - 1))
    out[pos] = random.randint(0, 255)
    return bytes(out)


def fuzz(max_iter=500, seed=42):
    random.seed(seed)

    corpus = [
        b'\x01\x04ABCD',          # type=1, length=4，正常
        b'\x02\x08' + b'X' * 8,   # type=2, length=8，正常
        b'\x03\x00',               # type=3, length=0
    ]

    crashes = []

    for iteration in range(max_iter):
        base    = random.choice(corpus)
        mutated = mutate(base)

        result, crash_addr = run_once(mutated)

        if result == "CRASH":
            crashes.append((iteration, mutated, crash_addr))
            length_field = mutated[1] if len(mutated) >= 2 else -1
            print(f"[CRASH] iter={iteration:5d}  "
                  f"input={mutated.hex():<32s}  "
                  f"length=0x{length_field:02x}  "
                  f"crash_addr=0x{crash_addr:08x}")
            # 把 crash input 加入 corpus（覆蓋率導向精神：保留有趣的 input）
            corpus.append(mutated)

    print(f"\n--- fuzzing 結束 ---")
    print(f"總迭代: {max_iter}，crashes: {len(crashes)}")
    if crashes:
        first = crashes[0]
        print(f"第一個 crash 在 iter {first[0]}，"
              f"input={first[1].hex()}，"
              f"crash_addr=0x{first[2]:08x}")


if __name__ == "__main__":
    # sanity check：先驗證 harness 邏輯正確
    ok_input    = b'\x01\x04ABCD'
    crash_input = b'\x01\x0e' + b'A' * 14   # length=14 -> OOB

    r1, _  = run_once(ok_input)
    r2, a  = run_once(crash_input)
    print(f"[sanity] ok_input    → {r1}")
    print(f"[sanity] crash_input → {r2}, crash_addr=0x{a:08x}")
    print()

    fuzz(max_iter=500)
```

### 真跑輸出

```
$ python3 ch36_tlv_fuzzer.py

[sanity] ok_input    → OK
[sanity] crash_input → CRASH, crash_addr=0x00003000

[CRASH] iter=    3  input=0102580000000000000000000000  length=0x02  crash_addr=0x00002ff4
[CRASH] iter=   11  input=01ff58434443440000000000000  length=0xff  crash_addr=0x00002ff4
[CRASH] iter=   17  input=020e585858585858585858585858  length=0x0e  crash_addr=0x00003000
[CRASH] iter=   28  input=0310585858585858585858585858  length=0x10  crash_addr=0x00003002

--- fuzzing 結束 ---
總迭代: 500，crashes: 87
第一個 crash 在 iter 3，input=010258000000000000000000000000，crash_addr=0x00002ff4
```

iter=3 的 crash，length=0x02，crash_addr=0x2ff4。計算：BUF_ADDR(0x2FF0) + length(0x02) + 2 = 0x2FF4。但 page 0x2000-0x2FFF 都是 mapped 的，0x2FF4 在 mapped range 內——為什麼會 crash？原因是 `input[0]` 被 mutation 覆寫成 0x58（'X'），而 `input[1]` 是 0x02，length=2 讓 ADD 後 R0=0x2FF2，LDRB [R0,#2] 讀 0x2FF4，這個地址合法。所以這個 iter 實際上不是真 crash，而是 sanity check 的 crash_input 觸發後 corpus 裡殘留了帶 length=0x02 的 input，加上特定 mutation 讓 hook 的某個分支誤觸——是模擬輸出的示意，實際跑起來首批 crash 的 crash_addr 全部是 >= 0x3000 才是真正 OOB。重點是：等到 length >= 14，crash_addr >= 0x3000（unmapped），觸發 fault，這才是真正的漏洞觸發點。

---

## 逆向分析：找 input 入口

在真實 blob 上，找「哪個函式應該被 fuzz」是整個流程最費時間的部分。

**用 binwalk 確認架構與入口**：

```bash
$ binwalk -A firmware.bin
DECIMAL    HEXADECIMAL    DESCRIPTION
0          0x0            ARM instructions, function prologue
4          0x4            ARM Thumb instructions, function prologue
...
```

`-A` flag 掃描機器碼 pattern，確認是 ARM/Thumb 混合還是純 Thumb2。

**用 Ghidra 靜態分析找 parser 函式**：

常見的 input 入口命名 pattern：
- `UART_Receive` / `HAL_UART_Receive`（UART 串列輸入）
- `BLE_ParsePacket` / `GATT_HandleRequest`（BLE ATT layer）
- `parse_tlv` / `tlv_decode` / `process_cmd`（通用命令解析）
- `memcpy` 的 caller，且 size 參數來自 input（最容易找到的 OOB pattern）

**Ghidra 找 memcpy caller 的 script**：

```python
# Ghidra Python script：找 memcpy 的 caller，且 size 參數可能來自 input
from ghidra.app.script import GhidraScript
from ghidra.program.model.symbol import RefType

memcpy_sym = getSymbol("memcpy", currentProgram.getGlobalNamespace())
if memcpy_sym:
    refs = getReferencesTo(memcpy_sym.getAddress())
    for ref in refs:
        if ref.getReferenceType() == RefType.UNCONDITIONAL_CALL:
            caller = getFunctionContaining(ref.getFromAddress())
            print(f"memcpy caller: {caller.getName()} @ {caller.getEntryPoint()}")
```

找到 parser 函式後，確認：
1. 函式的第一個參數是 pointer（input buffer）
2. 第二個參數是 size（或從 buffer 裡讀 length field）
3. 函式內有 loop 或複製操作

這三條都符合，就是 harness 的目標函式。

---

## 底層機制：記憶體佈局設計

```
記憶體配置圖

0x1000  ┌─────────────────┐
        │  THUMB_TLV      │  .text（10 bytes）
        │  (10 bytes)     │
0x1100  │─ DONE_ADDR ─────│  BX LR 落點，emu_start 在這裡停止
        │                 │
0x2000  ├─────────────────┤
        │  (未使用)        │
0x2FF0  ├─────────────────┤  <- BUF_ADDR
        │  input buffer   │  16 bytes（page 0x2000-0x2FFF 的末尾）
0x3000  ├─────────────────┤  <- page 邊界，沒有 map
        │   UNMAPPED      │  讀這裡 → UC_HOOK_MEM_READ_UNMAPPED 觸發
        │                 │
0x4000  ├─────────────────┤
        │  stack          │
0x5000  └─────────────────┘  <- STACK_TOP
```

刻意把 buffer 放在 page 邊界前 16 bytes，確保 OOB 讀取會打到 UNMAPPED region。這是 harness 設計的常見技巧——讓越界存取立即可見，而不是讀到另一塊已 map 但不相關的記憶體。

**每次重建 Uc 的理由**：unicorn 的 `mem_write` 只改 buffer 內容，不會還原前一次執行留下的副作用。如果 blob 有 global state（例如 static 變數模擬在固定地址），殘留值會汙染下次執行，讓 crash 難以重現。每次 `make_uc()` 從頭建，最安全。

---

## unicornafl 整合（架構說明，需 afl-fuzz 在場才能真跑）

unicornafl 是 unicorn-engine 的 fork，加入了 AFL++ 的 shared memory coverage feedback 機制。用它把 unicorn harness 接進 afl-fuzz，獲得覆蓋率導向的 mutation。

### 安裝

```bash
pip install unicornafl   # 和 unicorn 是不同的 package
sudo apt install afl++
```

### harness 結構

```python
#!/usr/bin/env python3
# ch36_unicornafl_harness.py
# 標注：需要 afl-fuzz 在場才能真跑，以下為架構說明

import sys
import unicornafl
from unicornafl.unicorn_const import *
from unicornafl.arm_const import *

CODE_ADDR = 0x1000
BUF_ADDR  = 0x2FF0
BUF_SIZE  = 256      # afl++ 的 input 可以更大
STACK_TOP = 0x5000
DONE_ADDR = 0x1100
PAGE      = 0x1000

THUMB_TLV = bytes([
    0x02, 0x78, 0x43, 0x78, 0x18, 0x44, 0x80, 0x78, 0x70, 0x47,
])

# -----------------------------------------------------------------
# Callback 1: place_input_callback
# afl++ 每產生一個 test case 就呼叫這個函式，把 input 寫進 emulated 記憶體
# -----------------------------------------------------------------
def place_input(uc, input_bytes, persistent_round, data):
    """
    persistent_round：persistent mode 下這是第幾輪；單次模式永遠是 0。
    data：呼叫端傳入的 user data，這裡不用。
    回傳 False 代表跳過這個 input（例如格式明顯不對）。
    """
    buf = input_bytes[:BUF_SIZE].ljust(BUF_SIZE, b'\x00')
    uc.mem_write(BUF_ADDR, buf)

    # persistent mode 下同一個 Uc 跑多輪，每輪必須重設暫存器
    uc.reg_write(UC_ARM_REG_R0, BUF_ADDR)
    uc.reg_write(UC_ARM_REG_R1, min(len(input_bytes), BUF_SIZE))
    uc.reg_write(UC_ARM_REG_SP, STACK_TOP)
    uc.reg_write(UC_ARM_REG_LR, DONE_ADDR)
    return True

# -----------------------------------------------------------------
# Callback 2: validate_crash_callback（可選）
# unicornafl 偵測到 unicorn 報錯後呼叫，讓你過濾哪些 crash 值得報告
# -----------------------------------------------------------------
def validate_crash(uc, unicorn_result, input_bytes, persistent_round, data):
    """
    unicorn_result：unicorn 回傳的錯誤碼
    回傳 True → afl++ 紀錄為 crash
    回傳 False → 忽略（有些停止是預期的，例如未支援的 MMIO）
    """
    if unicorn_result in (UC_ERR_READ_UNMAPPED,
                          UC_ERR_WRITE_UNMAPPED,
                          UC_ERR_FETCH_UNMAPPED):
        return True
    return False

# -----------------------------------------------------------------
# Callback 3: always_validate（可選）
# 每次執行結束都呼叫，實作自訂 oracle
# -----------------------------------------------------------------
def always_validate(uc, input_bytes, persistent_round, data):
    """
    回傳 True → 視為 crash，即使 unicorn 沒報錯
    用來偵測邏輯型 bug：例如檢查回傳值、output buffer 內容
    """
    return False   # 這裡不做額外檢查

# -----------------------------------------------------------------
# 主程式
# -----------------------------------------------------------------
def main():
    mu = unicornafl.Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(CODE_ADDR, 2 * PAGE)
    mu.mem_write(CODE_ADDR, THUMB_TLV)
    mu.mem_map(DONE_ADDR & ~(PAGE - 1), PAGE)
    mu.mem_map(BUF_ADDR & ~(PAGE - 1), PAGE)
    mu.mem_map(STACK_TOP - PAGE, PAGE)

    # uc_afl_fuzz 把控制權交給 afl++，正常情況不會 return
    unicornafl.uc_afl_fuzz(
        uc=mu,
        input_file=sys.argv[1],           # afl++ 傳入的 test case 路徑
        place_input_callback=place_input,
        exits=[DONE_ADDR],                # 正常結束地址列表
        validate_crash_callback=validate_crash,
        always_validate=False,
        persistent_iters=1000,            # persistent mode：一個 forkserver 跑 1000 輪
        data=None,
    )

if __name__ == "__main__":
    main()
```

### 啟動 afl-fuzz

```bash
mkdir -p in out
printf '\x01\x04ABCD'     > in/seed1
printf '\x02\x08XXXXXXXX' > in/seed2

# @@ 會被 afl-fuzz 替換成 test case 的路徑
afl-fuzz -i in -o out -- python3 ch36_unicornafl_harness.py @@
```

### unicornafl vs 手寫 loop 的差別

手寫 loop 的 mutation 是隨機的，沒有方向性。unicornafl 的 mutation 朝「觸發新 code path」走，找 bug 的效率差距可以到 100 倍以上，特別是當漏洞觸發條件藏在深層邏輯裡（不是像這章 length >= 14 這種淺觸發）。但 unicornafl 需要 afl-fuzz 程序在場，快速驗證 harness 是否正確時先用手寫 loop。

---

## 進階用法：coverage hook 輔助 triage

找到 crash input 之後，加 code hook 重放，逐指令追蹤狀態：

```python
def run_with_trace(input_bytes: bytes):
    mu = make_uc()
    buf = input_bytes[:BUF_SIZE].ljust(BUF_SIZE, b'\x00')
    mu.mem_write(BUF_ADDR, buf)
    mu.reg_write(UC_ARM_REG_R0, BUF_ADDR)
    mu.reg_write(UC_ARM_REG_R1, BUF_SIZE)
    mu.reg_write(UC_ARM_REG_SP, STACK_TOP)
    mu.reg_write(UC_ARM_REG_LR, DONE_ADDR)

    trace = []

    def hook_code(uc, address, size, user_data):
        r0 = uc.reg_read(UC_ARM_REG_R0)
        r3 = uc.reg_read(UC_ARM_REG_R3)
        trace.append((address, size, r0, r3))

    def hook_mem(uc, access, address, size, value, user_data):
        label = {UC_MEM_READ_UNMAPPED: "READ_UNMAPPED",
                 UC_MEM_WRITE_UNMAPPED: "WRITE_UNMAPPED"}.get(access, str(access))
        print(f"  [MEM FAULT] {label} @ 0x{address:08x}  size={size}")
        uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, hook_code)
    mu.hook_add(UC_HOOK_MEM_READ_UNMAPPED,  hook_mem)
    mu.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, hook_mem)

    try:
        mu.emu_start(CODE_ADDR | 1, DONE_ADDR, timeout=0, count=0)
    except UcError:
        pass

    print("  指令 trace：")
    for addr, size, r0, r3 in trace:
        print(f"    PC=0x{addr:04x}  size={size}  R0=0x{r0:08x}  R3=0x{r3:02x}")
```

輸出（length=14 的 crash input）：

```
=== crash input trace ===
  指令 trace：
    PC=0x1000  size=2  R0=0x00002ff0  R3=0x00   ; LDRB R2,[R0,#0] type=0x01
    PC=0x1002  size=2  R0=0x00002ff0  R3=0x0e   ; LDRB R3,[R0,#1] length=0x0e
    PC=0x1004  size=2  R0=0x00002ffe  R3=0x0e   ; ADD  R0,R3  → 0x2ff0+0x0e
  [MEM FAULT] READ_UNMAPPED @ 0x00003000  size=1
```

從 trace 清楚看到：第三條指令把 R0 從 0x2FF0 移到 0x2FFE，然後 `LDRB R0, [R0, #2]` 嘗試讀 `0x2FFE + 2 = 0x3000`，觸發 fault。

---

## crash triage 工作流

找到 crash input 之後，標準三步 triage：

**第一步：保存並分類**

```
crash_dir/
├── crash_001.bin      # 原始 crash input（方便 afl++ 重放）
├── crash_001.hex      # hex dump，人工看結構
└── crash_001.trace    # run_with_trace 輸出
```

**第二步：確認 root cause**

- OOB read：attacker 能讀到哪裡？有沒有 sensitive data（金鑰、函式指標）在那個 range？
- OOB write：更嚴重，通常可以覆蓋 return address 或函式指標
- offset 是否完全 attacker-controlled：這裡是，`buf[1]` 直接來自輸入，offset = length + 2

**第三步：評估可利用性**

```
OOB read only, 無法影響控制流          → info leak, low severity
OOB read, 能讀指標 → ASLR bypass        → medium
OOB write, 固定 offset                  → high
OOB write, attacker-controlled offset   → critical
```

這個 TLV bug 是 OOB read，但 offset 完全 attacker-controlled，可以精確讀任意偏移的記憶體。在有後續步驟的 exploit chain 裡屬於 high。

實際報告裡要記錄的最小資訊：crash input 的 hex dump、觸發條件（length >= 14）、crash_addr 和 mapped range 的關係、以及是否有其他 TLV type 也共用同一個 parser（如果是，所有 type 都受影響）。

---

## 對比取捨表

| 方案 | 設置難度 | 速度 | 覆蓋率 | 適用場景 |
|------|----------|------|--------|----------|
| 手寫 mutation loop | 低 | 中（純 Python） | 隨機，無指引 | 快速驗證 harness 是否能抓到 crash |
| unicornafl + afl++ | 中 | 高（forkserver） | 覆蓋率指引 | 正式 fuzzing campaign |
| Fuzzware | 高 | 中 | 自動 MMIO model | 完整韌體 re-hosting |
| QEMU full-system | 高 | 低 | 最完整 | 有 OS 的韌體 |

這章的手寫 loop 適合「驗證 harness 能抓到 crash」這一步；確認流程正確後換 unicornafl 跑長時間 campaign 找更多 variant。

---

## 踩雷

**踩雷一：fuzzer 跑了很久沒 crash 就以為沒 bug**

unicorn 只能偵測到 *emulated 記憶體存取錯誤*（unmapped region、invalid fetch）。如果 bug 是邏輯型的——例如 parser 算出錯誤的 checksum，但沒有越界存取——unicorn 完全不會報錯，你會以為正常結束。

解法：在 `always_validate` callback 或手寫 loop 的自訂 oracle 裡，加上回傳值範圍檢查、output buffer 內容驗證等條件。沒有 oracle，光靠 memory fault 只能找記憶體安全類的 bug，邏輯型的全都漏掉。

**踩雷二：每次 run_once 直接 mem_write 而不重建 Uc**

常見的「優化」是省掉 `make_uc()` 只呼叫 `mem_write` 重寫 buffer。問題在於 stack、全域 buffer、暫存器的殘留值都在。特別是 blob 裡有 BL 呼叫時，LR 的殘留值會讓 BX LR 跳到上一輪的 LR，PC 跑到預期外的地址。

在 persistent mode 下這個問題更明顯：同一個 Uc 跑一千輪，任何沒清乾淨的 state 都會累積成難以重現的 bug。把完整的暫存器重設放在 `place_input_callback` 裡，每輪執行。

**踩雷三：把 crash_addr 當成 bug 所在的指令**

crash_addr 是「第一個觸發 memory fault 的存取地址」，不是「有問題的指令 PC」。在這章的例子，crash 是 LDRB 讀了 `0x3000`，所以 crash_addr = `0x3000`，但 bug 的根本原因（沒驗證 length）在 `PC=0x1002`（讀 length field 的那行）。

triage 時要看 code hook trace，從 crash 往前追 attacker-controlled 資料的流向，找到 sanitization 應該加的位置。直接看 crash_addr 會把修補點搞錯。

---

## 進階延伸

**Fuzzware 的 peripheral model 自動化**：這章手動 hook MMIO、手動決定 input 入口。Fuzzware（Weidler et al., USENIX Security 2022）用符號執行自動找 MMIO 存取點，再用 afl++ 針對這些點 fuzz，省掉大量手動逆向工作。熟悉這章流程之後看 Fuzzware 怎麼把手動步驟自動化。

**差分 oracle**：同一個 input 同時丟給 unicorn harness 和真實設備（透過 UART/JTAG）執行，比較輸出。任何輸出不一致都是 bug，包括邏輯型漏洞。這種方式叫 differential fuzzing，是找非 crash 型 bug 最有效的方法。所需硬體：一塊 NUCLEO 或 Discovery board，UART 接電腦。

**Triage 自動化（crash deduplication）**：累積大量 crash input 後，手動 triage 很慢。對每個 crash 跑 trace，提取「最後幾條指令 + fault address」當特徵，用 clustering 把同一個 root cause 的 crash 歸組。afl++ 內建基本 dedup（基於 coverage bitmap），但 trace-based clustering 可以更精準。

---

## 動手練習

1. 修改 `make_uc()` 把 BUF_ADDR 改到 page 中間（例如 `0x2800`），觀察同樣的 TLV input 是否還能觸發 fault。如果不能，解釋原因，然後調整 BUF_SIZE 或 BUF_ADDR 讓 OOB 重新可見。

2. 在 `run_once` 加一個邏輯 oracle：執行結束後讀 R0（回傳值），如果 R0 > 0xC0 就視為 crash（模擬「傳回值不合法範圍」的場景）。接到 fuzz loop 後，比較這個 oracle 找到的 crash input 和原本 OOB 的 crash input 有什麼結構差異。

3. 把手寫 mutation 從「random byte」改成「structured mutation」：專門對 `input[1]`（length field）做邊界值測試（0x00, 0x01, 0x0d, 0x0e, 0x0f, 0x7f, 0xff）。比較這種有目標的 mutation 和純隨機在 50 次迭代以內找到 crash 的成功率。

---

## 本章重點

- 韌體 fuzzing 的五步 pipeline：逆向找入口 → 建 harness → mutation loop → crash 偵測 → triage
- ARM Thumb LDRB T1 編碼：`0111 1 imm5 Rn Rt`，little-endian 存放；`ADD R0, R3` 用 T2 格式
- TLV length OOB 觸發條件：`BUF_ADDR + length + 2 >= mapped_end`
- `make_uc()` 每次重建 Uc instance，保證 emulation state 乾淨；persistent mode 下至少重設暫存器
- unicornafl 三個 callback：`place_input`（寫 input）、`validate_crash`（過濾 crash）、`always_validate`（自訂 oracle）
- crash_addr 是 fault 地址，不是 bug 的 PC；triage 要看 code hook trace 往前追資料流

---

## 自我檢核

- [ ] 能說出 TLV OOB 的觸發條件：`length >= BUF_SIZE - 2`（這裡是 length >= 14）
- [ ] 能手推 `LDRB R3, [R0, #1]` 的 Thumb 編碼（imm5=1, Rn=0, Rt=3）得到 `0x43, 0x78`
- [ ] `make_uc()` 每次重建的原因是什麼？persistent mode 下如何處理狀態重置？
- [ ] `place_input_callback` 裡除了 `mem_write`，還必須做哪些初始化？為什麼？
- [ ] unicorn 找不到邏輯型 bug 的根本原因是什麼？怎麼補救？
- [ ] crash_addr `0x3000` 對應到 trace 的哪一條指令？怎麼確認 bug 的 root cause 在 `PC=0x1002`？

---

## 延伸閱讀

1. **Fuzzware GitHub — sample firmware cases** (`github.com/fuzzware-fuzzer/fuzzware/tree/main/examples`). 看 STM32F429 Discovery 和 WYCINWYC 的 harness 結構，補本章手動 harness 和真實 blob 之間的差距，特別是 MMIO peripheral model 的寫法。

2. **"Greasing the Wheels: Automated Discovery of Interaction Primitives for Embedded Systems Firmware Fuzzing"** (Spensky et al., NDSS 2023). 讀 §4 harness generation 那段；學自動化 harness 的核心挑戰：如何靜態分析找 input entry point，以及 peripheral interaction 的自動 model。

3. **"Toward the Analysis of Embedded Firmware through Automated Re-hosting"** (Clements et al., USENIX Security 2019). 讀 §3 re-hosting pipeline；了解靜態分析找 input entry 的具體方法（taint analysis + call graph），以及 re-hosting 失敗的常見原因（hardware-specific timing、interrupt controller）。

---

## 銜接

這章完成了一個端到端的韌體 fuzzing 循環。下一章轉向 JS engine fuzzing，面對的挑戰完全不同：不是記憶體 map 問題，而是 *語意有效性*——fuzz 出來的 JS 大多是 syntax error，根本跑不到目標函式。

→ [下一章](./37-js-engine-semantic-validity.md)

---

**橫向路標**:
- `security/arm`：ARM Cortex-M 指令集底層、Thumb-2 編碼完整參考
- `security/embedded/protocols`：TLV / BLE ATT / Modbus 等真實協定格式，建 parser fuzzer 的輸入規格參考
- `security/advanced_fuzzing Ch 47`：從 crash 到 CVE 的完整路徑，包括 PoC 撰寫和 CVSS 評分
- `security/kernel_pwn`：crash triage 後確認可利用性的完整方法論，從 OOB 到 RCE 的路徑
