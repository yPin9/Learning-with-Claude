# Ch 25 — 逆 ARM bootloader / BootROM

> **目標**：能把 raw ARM/AArch64 binary（無 ELF/PE header、無符號）載入 Ghidra 並正確還原：base address 判定、ARM/Thumb 模式識別、vector table 定位 entry point、找驗簽函式（memcmp / RSA / SHA 呼叫附近的條件跳轉）。具體對象涵蓋 TF-A BL1/BL2/BL31 image 逆向、U-Boot ELF 與 raw binary 逆向、MTK Preloader/DA 格式解析。呼應本課 Ch 15-17（TF-A 架構、TF-A internals、U-Boot 攻擊面）。
> **環境**：Ghidra 桌面工具，本章的截圖描述為步驟指引。WSL 真跑段落用 `strings`、`binwalk`、`python3` 操作 binary 前置分析。真機 BootROM 逆向標「未實測」，相關操作說明原理與方法。

---

## 為什麼 ARM bootloader RE 比 x86 難

x86 UEFI RE（Ch 24）的困難是「格式齊全但符號缺失」——PE header 告訴你 section、entry point、架構，難的是還原 Boot Services 呼叫。ARM bootloader RE 的困難更基礎：

**問題一：根本沒有 header**

真實世界的 ARM bootloader binary：
- BootROM：直接燒在 SoC die 裡，研究者看到的是讀出的 raw bytes，沒有任何格式標記
- MTK Preloader：有廠商私有 header（`MMM MBOOT`），Ghidra 不認識
- TF-A BL1 FIP image：wrapped 在 FIP 容器裡，抽出後是 ELF 或 raw binary，視 build config
- U-Boot：大多以 ELF 發佈（有符號的 debug build），但 production 的 `u-boot.bin` 是 raw binary（`objcopy -O binary` 後），stripped

**問題二：ARM/Thumb 混合模式**

ARMv7（32-bit）同時存在 ARM（4 bytes/指令）和 Thumb（2 bytes/指令，有些是 Thumb-2 = 4 bytes）。呼叫慣例以 `BX lr` 或 `BLX reg` 做模式切換。Ghidra 在 raw binary 中如果從 wrong mode 開始分析，看到的會是亂碼指令序列，完全沒有意義。

**問題三：base address 未知**

ARM cortex SoC 的 SRAM、ROM、DRAM 分別映射到不同的位址（通常是 SoC 廠商 TRM 才有記載），而且：
- BL1 可能在 ROM 起始（`0x0` 或 `0x00000000`，實際位置 SoC-specific）
- BL2 被 BL1 載入到 SRAM（位址在幾十 KB 到幾 MB 之間）
- U-Boot 通常被複製到 DRAM 某個固定位址後執行（`CONFIG_SYS_TEXT_BASE`）

如果 base address 設錯，所有 PC-relative 跳轉（`B`、`BL`）的目標都算錯。

**問題四：加密 / OTP 保護**

真機 BootROM 在一些 SoC 上是加密的（MTK DA 有加密版本；Apple BootROM 完全不公開），或者即使可以讀出（JTAG / BH mode）也因為 OTP fuse 限制了某些暫存器讀取。這部分標「未實測」，本章給原理說明。

---

## 前置分析工具（WSL 真跑）

在丟進 Ghidra 之前，先用命令列工具做快速偵察，縮小後續分析範圍。

### strings：找人可讀的資訊

```bash
# 對 raw binary 跑 strings，長度設 8 避免雜訊
strings -n 8 u-boot.bin

# 常見有用的字串
# U-Boot:
#   "U-Boot 20xx.xx"       → 版本，對應開源 source
#   "CPU: ARMv7"           → 確認架構
#   "Net:   "              → 網路驅動初始化
#   "Hit any key to stop autoboot" → autoboot prompt
#   "verify_image"         → 如果有，直接定位驗簽相關字串

# TF-A:
#   "NOTICE:  BL1: "       → BL1 的 log 輸出前綴（若有 PLAT_LOG_LEVEL）
#   "ERROR:   Image id=xxx failed authentication"  ← 驗簽失敗訊息
#   "Booting BL"

# MTK Preloader:
#   "[SEC]"                → MTK security log prefix
#   "SBC enabled"          → Secure Boot 已啟用
#   "BROM"

# 根據字串找 offset 是第一步
strings -n 8 -t x u-boot.bin | grep -i "verify\|sign\|auth\|rsa\|sha"
```

```bash
# 實際輸出範例（假設 u-boot.bin）：
# 0x9abc4  verify_images
# 0x9ac10  Image SHA256:
# 0x9ac30  Bad RSA signature
# 0x9ac50  Verified OK

# 記住這些 offset，後面 Ghidra 裡 'G' 跳轉到這裡做 xref 反查
```

### binwalk：找 header / magic / 壓縮 section

```bash
# 掃描所有 magic bytes
binwalk u-boot.bin

# 常見輸出：
# DECIMAL    HEXADECIMAL  DESCRIPTION
# 0          0x0          uImage header, ... CRC: 0x..., Image Name: ...
# 64         0x40         LZMA compressed data, ...
# 1048576    0x100000     Linux kernel ARM boot executable zImage ...

# 提取所有識別到的 component
binwalk -e u-boot.bin

# 掃描 TF-A FIP
binwalk fip.bin
# 會看到 FIP ToC magic (0xAA640001)，各 image 的 LZMA 或 raw bytes
```

```bash
# 找 ARM vector table magic
# ARMv7 reset vector 通常是：
#   E59FF018  (LDR PC, [PC, #0x18])  或
#   EA000000  (B 向前跳到 entry)
python3 -c "
import struct
data = open('bootrom.bin','rb').read()
# 搜 LDR PC 指令
for i in range(0, min(len(data), 0x200), 4):
    word = struct.unpack_from('<I', data, i)[0]
    if word & 0xFFFF0000 == 0xE59F0000:  # LDR Rx, [PC, ...]
        print(f'0x{i:04x}: {word:#010x}  LDR R{(word>>12)&0xf},[PC,+#0x{word&0xfff:x}]')
    if word & 0xFF000000 == 0xEA000000:  # B offset
        offset = (word & 0xFFFFFF)
        if offset & 0x800000: offset |= 0xFF000000  # sign extend
        target = i + 8 + (offset << 2)
        print(f'0x{i:04x}: {word:#010x}  B 0x{target:x}')
" 2>/dev/null | head -20
```

---

## Base Address 判定

### 方法一：從 UART log 讀

若裝置有 UART，開機 log 通常顯示各 image 的載入位址：

```
NOTICE:  BL1: v2.9(release):v2.9-dirty
NOTICE:  BL1: Built : 10:30:00, Aug  1 2026
NOTICE:  BL2: v2.9(release):v2.9-dirty
NOTICE:  BL2: Booting BL31
NOTICE:  BL31: v2.9(release):v2.9-dirty
NOTICE:  BL31 ENTRY POINT: 0x0e000000

# → BL31 base = 0x0e000000
```

U-Boot log：

```
U-Boot 2023.07 (Aug 01 2026 - 10:00:00 +0800)

DRAM:  2 GiB
relocate_code Pointer at: ffffffc07fce0000

# → 重定位後 base 在 0xffffffc07fce0000
# 但 U-Boot 在重定位前從 CONFIG_SYS_TEXT_BASE 執行
# 通常是 0x40200000 之類的 DRAM 地址
```

### 方法二：從 SoC TRM / linker script

U-Boot 的 `u-boot.lds` 或 `arch/arm/config.mk`：

```makefile
CONFIG_SYS_TEXT_BASE = 0x80800000  # 以 Allwinner A64 為例
```

TF-A 的 `plat/<vendor>/platform.mk`：

```makefile
BL1_BASE    := 0x00000000  # ROM 起始
BL2_BASE    := 0x0e000000  # Secure SRAM
BL31_BASE   := 0x0e040000
```

### 方法三：從 binary 內部的 PC-relative 引用反推

如果 binary 裡有絕對位址引用（例如 GOT 或某個 global pointer table），可以統計所有疑似指標的 4/8-byte 值，找最常出現的 alignment（通常對應 DRAM / SRAM 基址）：

```python
# 統計 binary 中出現的「疑似位址」（4B aligned，在合理範圍）
import struct, collections

data = open('u-boot.bin', 'rb').read()
candidates = collections.Counter()

for i in range(0, len(data)-4, 4):
    val = struct.unpack_from('<I', data, i)[0]
    # ARM 常見 DRAM 範圍
    if 0x40000000 <= val <= 0xC0000000:
        candidates[val & 0xFF000000] += 1

for addr, count in candidates.most_common(10):
    print(f'0x{addr:08x}  count={count}')
```

最高頻的 `0x??000000` 通常就是 base address 的「頁」對齊部分。

### 方法四：AArch64 的 ADRP 指令

AArch64 binary 大量使用 `ADRP X?, #page_offset` + `ADD X?, X?, #offset` 這個兩指令組合存取全域資料。`ADRP` 的 immediate 是 PC-relative 的 4KB 頁，如果 binary 裡有大量 ADRP，其 target page 集中在某個範圍，就是 binary 實際執行的 base 附近。

```python
# 找 AArch64 ADRP 指令目標（假設 base=0，找修正量）
import struct

data = open('bl31.bin', 'rb').read()
targets = []

for i in range(0, len(data)-4, 4):
    word = struct.unpack_from('<I', data, i)[0]
    # ADRP encoding: bits[31:24]=0x90, bits[23:5]=immhi, bits[30:29]=immlo
    if (word & 0x9F000000) == 0x90000000:
        immlo = (word >> 29) & 0x3
        immhi = (word >> 5) & 0x7FFFF
        imm = ((immhi << 2) | immlo) << 12
        if imm & (1 << 32): imm -= (1 << 33)  # sign extend
        # virtual_target = PC_page + imm = (base + i & ~0xFFF) + imm
        # relative_page = (i & ~0xFFF) + imm
        rel_page = (i & ~0xFFF) + imm
        targets.append(rel_page)

import collections
# 以 1MB 粒度統計
mb_buckets = collections.Counter(t & ~0xFFFFF for t in targets)
for bucket, count in mb_buckets.most_common(5):
    print(f'0x{bucket:016x}: {count} references')
```

---

## Ghidra 載入 ARM Binary

### AArch64（ARMv8-A，64-bit）

**File → Import → 選 raw binary**：

- **Language**：`AARCH64:LE:64:v8A:default`（小端，64-bit AArch64）
  - 若是 big-endian SoC（少見）：`AARCH64:BE:64:...`
- **Format**：`Raw Binary`（不是 ELF，除非 binary 本來就是 ELF 格式）
- **Options → Image Base**：輸入判定出的 base address（如 `0x0e040000`）

### ARMv7-A（32-bit，帶 Thumb/Thumb-2）

**Language**：`ARM:LE:32:v7:default`（或 `v8:default` 若目標是 Cortex-A53/A55 跑 AArch32）

**重要設定 — Thumb Mode**：

ARMv7 binary 通常混合 ARM 和 Thumb 指令。Ghidra 預設從 base address 以 ARM mode 分析，但某些 binary（特別是 U-Boot 的 SPL）可能以 Thumb mode 開頭。

設定方式：
- **Edit → Tool Options → Language → Default ARM Processor Mode**：選 `THUMB` 或 `ARM`
- 或在 Listing 視窗手動：選擇 entry point bytes → 右鍵 → **Disassemble (ARM)** 或 **Disassemble (THUMB)**

識別 Thumb mode 的方法：

```
ARM 指令（4 bytes，big pattern）：
  E92DD800  PUSH {R11, LR}
  E28DB004  ADD R11, SP, #4
  E24DD00C  SUB SP, SP, #0xC

Thumb-2 指令（混合 2/4 bytes）：
  2DE9 0048  PUSH {R3-R5, LR}     (4 bytes Thumb-2)
  6868       LDR R0, [R5]          (2 bytes Thumb)
  8847       BLX R1                (2 bytes Thumb)

如果 Ghidra 以 ARM mode 分析 Thumb bytes，結果是亂碼。
如果 Ghidra 以 Thumb mode 分析 ARM bytes，同樣是亂碼。
```

---

## Vector Table：找 Entry Point

ARM/AArch64 binary 的 entry point 不一定在文件開頭，但 **vector table 一定在**某個固定位址（通常是 base address 的最開始）。

### ARMv7 Exception Vector Table

```
位址        指令                 用途
base+0x00   LDR PC, [PC, #0x18]  Reset (→ 真正的 entry)
base+0x04   LDR PC, [PC, #0x18]  Undefined Instruction
base+0x08   LDR PC, [PC, #0x18]  Supervisor Call (SWI)
base+0x0C   LDR PC, [PC, #0x18]  Prefetch Abort
base+0x10   LDR PC, [PC, #0x18]  Data Abort
base+0x14   LDR PC, [PC, #0x18]  (Reserved)
base+0x18   LDR PC, [PC, #0x18]  IRQ
base+0x1C   LDR PC, [PC, #0x18]  FIQ

base+0x20   <Reset handler 位址>       ← PC-relative 指標表開始
base+0x24   <Undef Instr handler 位址>
...
```

`LDR PC, [PC, #0x18]` 的 encoding 是 `E59FF018`。在 raw binary 中搜索這個 magic，找到後 base+0x20 開始的位址表就是各 handler 的絕對位址，Reset handler 指向 `_start` / `reset` / entry point。

```bash
# WSL 找 vector table
python3 -c "
data = open('u-boot.bin','rb').read()
# 找 LDR PC 指令序列（vector table 的 8 個連續 E59Fxxx）
for i in range(0, len(data)-32, 4):
    if data[i:i+4] == bytes([0x18, 0xF0, 0x9F, 0xE5]):  # E59FF018 LE
        # 確認後面也是同類指令
        valid = all(data[i+j*4+3] == 0xE5 for j in range(1, min(8, (len(data)-i)//4)))
        if valid:
            print(f'Vector table candidate at offset 0x{i:x}')
            # 讀取 handler 位址
            import struct
            for j in range(8):
                ptr_off = i + 0x20 + j*4
                if ptr_off+4 <= len(data):
                    ptr = struct.unpack_from('<I', data, ptr_off)[0]
                    names = ['Reset','Undef','SVC','PAbort','DAbort','Reserved','IRQ','FIQ']
                    print(f'  {names[j]}: 0x{ptr:08x}')
"
```

### AArch64 Exception Level Vector Table

AArch64 的 vector table 結構不同，每個 entry 是 128 bytes（32 條指令），而非一條跳轉指令：

```
VBAR_EL3 + 0x000  Current EL with SP0 - Sync
VBAR_EL3 + 0x080  Current EL with SP0 - IRQ
VBAR_EL3 + 0x100  Current EL with SP0 - FIQ
VBAR_EL3 + 0x180  Current EL with SP0 - SError
VBAR_EL3 + 0x200  Current EL with SPx - Sync
...
VBAR_EL3 + 0x600  Lower EL using AArch64 - Sync   ← SMC 呼叫從 EL2 進來的路徑
```

TF-A BL31 的 vector table 在 `bl31/aarch64/bl31_entrypoint.S` 和 `bl31/aarch64/runtime_exceptions.S`。在 Ghidra 中，找到 AArch64 binary 裡的 `VBAR_EL3` 初始化（`MSR VBAR_EL3, X?` 指令），那個 X? 暫存器的值就是 vector table 的位址，再套 128-byte 步進去讀每個 entry。

---

## TF-A BL1/BL2/BL31 image 逆向

### 取得可逆向的 binary

TF-A 的開源 build 在 `build/<plat>/release/` 或 `debug/` 下有：

```
bl1.bin   -- raw binary（ARM reset vector 在最前面）
bl2.bin   -- raw binary
bl31.bin  -- raw binary
fip.bin   -- FIP 容器（包含 bl2.bin, bl31.bin, bl33.bin + certs）
bl1.elf   -- ELF with symbols（debug build 才有）
bl2.elf   -- ELF with symbols
```

研究用途優先拿 ELF（有符號），分析廠商裝置時只有 raw binary。

**FIP 中取 BL31**（WSL 真跑）：

```bash
# 安裝 fiptool（TF-A source 的 tools/fiptool/）
# 或用 binwalk 直接解
binwalk -e fip.bin
# 在 _fip.bin.extracted/ 找到各 image

# 或用 Python：FIP ToC 解析
python3 -c "
import struct

with open('fip.bin', 'rb') as f:
    data = f.read()

# FIP magic
magic, serial, flags = struct.unpack_from('<IIQ', data, 0)
assert magic == 0xAA640001, f'Not a FIP: {magic:#x}'
print(f'FIP magic OK, serial={serial:#x}')

# 讀 ToC entries（每個 40 bytes）
off = 16
while True:
    uuid = data[off:off+16]
    img_off, size, fl = struct.unpack_from('<QQQ', data, off+16)
    off += 40
    if uuid == bytes(16):  # 全零 UUID = end marker
        break
    print(f'UUID: {uuid.hex()}  offset={img_off:#x}  size={size:#x}')
    # BL31 UUID 末尾特徵: ...47 47（見 Ch 16 表格）
"
```

### 逆向重點：TBBR 驗簽路徑

Ch 16 告訴你 TF-A 的驗簽由 `auth_mod_verify_img()` → `crypto_mod_verify_signature()` → MbedTLS 完成。逆向 TF-A BL1（或廠商 Preloader）時，目標就是找這條呼叫鏈，然後分析：

1. 驗簽失敗後的 error path（有沒有 `plat_error_handler()` 真的不返回）
2. 傳入驗簽函式的 buffer 和 length 是否有邊界驗證（CVE-2022-47630 的根因）

**在 raw binary 中找驗簽函式的策略**：

```
策略一：從錯誤字串反查
  Ghidra → Search → Search for Strings → "authentication"
  → 找到字串 "failed authentication"
  → 右鍵 → References → Show References to this string
  → 看是哪個函式引用了這個字串
  → 那個函式的 caller 就是驗簽呼叫點，失敗時才會走到這行

策略二：找 memcmp / SHA / RSA 呼叫
  strings binary | grep -i "sha\|rsa\|sign\|hash" 找到 MbedTLS 殘留的字串
  → 在 Ghidra 做 string reference，找到 MbedTLS 函式
  → 從那裡往 caller 追，找驗簽的入口

策略三：找條件跳轉模式（見「找驗簽函式 pattern」節）
```

---

## U-Boot 逆向

### ELF vs raw binary

debug build 的 U-Boot ELF 有符號（`u-boot` 不帶 `.bin`，用 `nm u-boot | grep verify` 直接找）。production 的 `u-boot.bin` 是 stripped raw binary，但：

```bash
# 從 open source U-Boot 取得對應版本的 ELF（許多 vendor BSP 是開源的）
# 例如 Raspberry Pi U-Boot：
git clone https://github.com/raspberrypi/firmware
# 找 bootcode.bin / start.elf
# 或用 Buildroot / Yocto 自行 build 同版本的 U-Boot ELF，用符號輔助逆向 stripped binary
```

### 找 verify_image / board_init

U-Boot 的 verified boot 入口（若啟用 CONFIG_FIT_SIGNATURE）：

```c
// include/image.h / common/image-fit.c
int fit_image_verify_with_data(...)   // FIT image 的驗簽主邏輯
int fit_config_verify(...)            // 驗 FIT configuration
int bootm_load_os(...)                // 載入 OS，呼叫 verify
```

逆向 stripped binary 找 `verify_image` 系列函式的策略：

```
1. strings u-boot.bin | grep -n "Verifying Hash Integrity"
   → U-Boot 驗簽成功時印 "Verifying Hash Integrity ... OK"
   → 失敗時印 "Bad Data Hash"
   → 在 Ghidra 找這些字串的 reference，反查呼叫函式

2. 找 SHA256 / RSA 的函式特徵：
   SHA256 在 AArch64 可能使用 ARM crypto extension（SHA256H, SHA256H2 指令）
   在 ARMv7 Cortex-A（無 crypto ext）用純軟體實作，特徵是大量 ROR/EOR/ADD 操作

3. board_init 位於 U-Boot 開機序列早期，在 _start → start.S → board_init_f → board_init
   從 entry point 往前追呼叫圖，大約第 3-5 層 call depth 找到 board_init
```

**U-Boot board_init 的特徵**（ARMv7 stripped binary 的 Ghidra 輸出）：

```c
// Decompiler 輸出（函式名稱是自動生成的 FUN_xxxxx）
void FUN_80800000(void)  // _start
{
    // 設定 stack pointer
    // 清 BSS
    FUN_8081xxxx();  // board_init_f
}

void FUN_8081xxxx(void)  // board_init_f
{
    // 初始化 DRAM、UART、時鐘
    FUN_8082xxxx();  // initcall sequence
    FUN_8083xxxx();  // relocate_code
    FUN_8084xxxx();  // board_init_r（重定位後）
}
```

---

## MTK Preloader / DA 格式

MTK（MediaTek）的 Download Agent（DA）和 Preloader 是研究者在 Android 手機韌體分析中最常遇到的 ARM binary。

### Preloader 格式

MTK Preloader 有一個私有 header：

```
offset  size  說明
0x000   8     magic: "ANDRBOOT" 或 "MMM MBOOT" 視版本
0x008   4     block size
0x00C   4     file size（不含 header）
0x010   4     Preloader 的 load address
0x014   4     jump address（entry point）
0x018   4     signature 開始 offset（若有 Secure Boot）
0x01C   4     signature 大小
0x020   ...   實際的 ARM binary 開始
```

抽取 binary：

```bash
# 找 magic
python3 -c "
data = open('preloader.bin','rb').read()
magics = [b'ANDRBOOT', b'MMM MBOOT', b'\x4d\x4d\x4d\x20']
for magic in magics:
    off = data.find(magic)
    if off >= 0:
        print(f'Magic {magic} at 0x{off:x}')
"

# 解析 header 取出 load address 和 entry
python3 -c "
import struct
data = open('preloader.bin','rb').read()

# 假設 header 在 offset 0
magic = data[0:8]
block_size = struct.unpack_from('<I', data, 8)[0]
load_addr = struct.unpack_from('<I', data, 0x10)[0]
jump_addr = struct.unpack_from('<I', data, 0x14)[0]
sig_offset = struct.unpack_from('<I', data, 0x18)[0]
sig_size = struct.unpack_from('<I', data, 0x1C)[0]

print(f'Load address: 0x{load_addr:08x}')
print(f'Entry point:  0x{jump_addr:08x}')
print(f'Signature at: 0x{sig_offset:x}, size={sig_size}')

# 抽出 ARM binary（去掉 header）
arm_binary = data[0x20:]
open('preloader_arm.bin', 'wb').write(arm_binary)
print(f'ARM binary extracted, size={len(arm_binary)}')
"
```

### DA（Download Agent）

DA 是 MTK BROM 用來接管 USB 下載的 second-stage binary，比 Preloader 更接近 BootROM 層。DA 的格式更複雜（有的有 AES 加密，有的沒有），但 BROM 解密後載入到固定位址（通常是 internal SRAM，`0x00200000` 附近，具體 SoC-specific）。

**研究 DA 的主要目標**：找出 BROM 如何驗 DA 的完整性，以及 DA 本身驗 Preloader 的邏輯。

MTK BROM 漏洞研究（如 CVE-2022-26320 等，brom_bypass 公開工具鏈）的核心就是讓 BROM 接受惡意 DA，因此 DA 的驗簽路徑是最高優先分析目標。（此類研究屬於「真機 BootROM」範疇，本章標**未實測**，以概念說明為主。）

---

## 找驗簽函式的 Pattern

ARM bootloader 裡找驗簽函式，不管是 TF-A、U-Boot 還是 MTK，都可以用以下幾個 pattern：

### Pattern A：字串 → xref → 逆向追蹤

已在上面說明。最快，成功率高。

### Pattern B：memcmp 呼叫附近的條件跳轉

驗簽的最後一步幾乎都是比較 hash 或 signature bytes，用 `memcmp`（或自己的比較迴圈）。

**在 Ghidra 找 memcmp 的呼叫**：

1. Search → Search for Strings → 找 "memcmp" 或 `XREF to memcmp`（若有 libc 殘留符號）
2. 若 stripped 沒有符號，找 byte comparison loop 的模式：
   - ARM: `LDRB` + `CMP` + `BNE`（loop over bytes）
   - AArch64: `LDR W?,` + `CMP` + `B.NE`

**重要**：`memcmp` 呼叫後通常緊跟：

```c
result = memcmp(computed_hash, expected_hash, SHA256_DIGEST_LENGTH);
if (result != 0) {
    // 驗簽失敗路徑  ← 這裡是攻擊目標
    plat_error_handler(AUTH_ERR_HASH_MISMATCH);
    panic();
}
// 驗簽成功，繼續執行  ← 若能跳到這裡就繞過驗簽
```

在 Ghidra Decompiler 看到這個 pattern，失敗路徑的條件跳轉（`CBZ/CBNZ`、`CMP + BEQ/BNE`）就是分析重點。

### Pattern C：RSA 函式特徵（AArch64）

RSA 驗簽的核心是大整數模冪運算，計算密集，在 stripped binary 裡的特徵：

```
- 函式很大（幾百條指令）
- 大量 MUL / UMULH 指令（用於 2048-bit 整數乘法）
- 有迴圈（LOOP 結構反覆操作固定大小的 buffer，通常 256 bytes = RSA-2048 key size）
- 緊接著一個 memcmp（把 RSA 解密結果和 expected hash 比對）

在 Ghidra 的 Function Call Graph：
  auth_mod_verify_img
    └─► crypto_mod_verify_signature
          └─► mbedtls_rsa_rsassa_pkcs1_v15_verify
                └─► mbedtls_mpi_exp_mod          ← RSA 大數模冪（最複雜的函式）
                      └─► mbedtls_mpi_mul_mpi     ← 大數乘法
```

用 Ghidra 的 Function Complexity 分析：**Window → Function Call Graph** + **Metrics** 找最複雜（最多 MUL）的幾個函式，往往能定位 MbedTLS 的 RSA 核心。

### Pattern D：SHA256 硬體加速指令（ARMv8 Crypto Extension）

許多現代 Cortex-A SoC（A53/A55/A72 等）支援 ARMv8 Crypto Extension，SHA256 計算用專用指令：

```
SHA256H   Q0, Q1, V2.4S    ← SHA256 round（處理 4 words）
SHA256H2  Q1, Q0, V2.4S
SHA256SU0 V2.4S, V3.4S     ← SHA256 message schedule
SHA256SU1 V2.4S, V0.4S, V1.4S
```

在 Ghidra 的 Listing 視窗用 Ctrl+F 搜索 `SHA256H`，找到就找到了 SHA256 函式，往上追 caller 就到驗簽主路徑。

---

## Ghidra ARM/AArch64 設定要點

### Language 選擇表

| 目標 | Language ID | 備注 |
|------|------------|------|
| AArch64 LE (Cortex-A53/A72...) | `AARCH64:LE:64:v8A:default` | 大多數 ARM64 bootloader |
| AArch64 BE | `AARCH64:BE:64:v8A:default` | 罕見，某些網路設備 |
| ARMv7-A LE (Cortex-A7/A9...) | `ARM:LE:32:v7:default` | 多數 Android SoC |
| ARMv7-A LE Thumb entry | `ARM:LE:32:v7:default` + Thumb mode | Cortex-M 的 SPL 等 |
| ARMv8-A AArch32 (EL0 only) | `ARM:LE:32:v8:default` | 特殊 SoC |

### 反編譯器效果改善 tips

1. **標記 non-returning 函式**：`plat_error_handler()`、`panic()`、無條件 `B .`（死迴圈）應該標為 non-returning，否則 Ghidra 會在呼叫它們後繼續分析廢碼，搞亂 function boundary。做法：找到函式 → 右鍵 → **Function → Edit Function** → 勾 `No Return`。

2. **手動設定 entry point**：Analysis → Auto Analyze 後，可能有很多 disassembly hole。對著 vector table 中的每個 handler 地址：在那個位址上按 'D' 強制反彙編，再按 'F' 建立函式。

3. **ARM calling convention**：ARMv7 用 AAPCS（R0-R3 為參數，R0 為回傳值），AArch64 用 AAPCS64（X0-X7 為參數，X0 為回傳值）。Ghidra 的 ARM processor 應該已預設正確 convention，但如果反編譯結果參數個數不對，手動 Edit Function Signature 可以修正。

4. **CPSR / PSTATE 旗標**：ARM 的條件執行（`MOVEQ`、`ADDCS`）依賴 CPSR，Ghidra 反編譯時通常能正確處理，但複雜的條件執行序列偶爾會讓 decompiler 輸出 `if (z_flag) ...`，需要手動對照 ASM 理解。

5. **系統暫存器 MSR/MRS**：AArch64 binary 頻繁出現 `MRS X0, CurrentEL`、`MSR VBAR_EL3, X0` 等指令。Ghidra 能正確反彙編，但在 decompiler 輸出中可能顯示為 `X0 = CurrentEL`（較難閱讀）。建議在 Listing 視窗看 ASM 而非純靠 Decompiler 處理系統暫存器相關段落。

---

## 動手：用 strings + binwalk 定位 U-Boot 驗簽邏輯

以下操作在 WSL Ubuntu 22.04 真跑，以 Raspberry Pi 的 U-Boot 為例（可合法從 rpi-firmware GitHub 取得）。

```bash
# 取得 RPi U-Boot（含 arm64 版本）
cd /tmp
git clone --depth=1 https://github.com/raspberrypi/firmware rpi-firmware
ls rpi-firmware/boot/
# 找 u-boot*.bin（如 u-boot.bin，若有的話）
# 若沒有，下載 Raspberry Pi OS 並取出 /boot/u-boot-rpi4.bin

# 步驟 1：確認架構和 magic
file rpi-firmware/boot/bootcode.bin
hexdump -C rpi-firmware/boot/bootcode.bin | head -5
# 注意：bootcode.bin 是 VideoCore（非 ARM），這裡只示範流程

# 改用 U-Boot 官方 test binary（從 CI artifact 取得，或自己 build）
# 以下假設已有 u-boot.bin（ARM64，Raspberry Pi 4 target）

# 步驟 2：strings 偵察
strings -n 8 u-boot.bin | grep -iE "verify|sign|rsa|sha|hash|bad|error|auth" | head -30
```

預期輸出（若此 U-Boot build 有啟用 CONFIG_FIT_SIGNATURE）：

```
Verifying Hash Integrity
Bad Data Hash
RSA: Verify OK
Bad FIT configuration
Bad signature
...
```

```bash
# 步驟 3：找字串的 offset
strings -n 8 -t x u-boot.bin | grep "Verifying Hash Integrity"
# 輸出範例：
#   9a1c4  Verifying Hash Integrity

# 步驟 4：在 binary 裡確認 offset
python3 -c "
data = open('u-boot.bin','rb').read()
target = b'Verifying Hash Integrity'
off = data.find(target)
print(f'Found at binary offset 0x{off:x}')
# 若 image base 是 0x40200000:
# Ghidra 中的地址 = 0x40200000 + 0x9a1c4 = 0x40299a1c4... （調整 base）
"

# 步驟 5：binwalk 結構分析
binwalk u-boot.bin
# 預期看到：
# 0   0x0     u-boot image header（若有 mkimage 包裝）
# 64  0x40    gzip/lzma 壓縮 payload（若有）
```

```bash
# 步驟 6：找 SHA/hash 相關字串的 cluster
python3 -c "
import subprocess
result = subprocess.run(['strings', '-n', '8', '-t', 'x', 'u-boot.bin'],
                       capture_output=True, text=True)
lines = result.stdout.splitlines()

# 找驗簽相關字串並按 offset 排序
keywords = ['verify', 'hash', 'rsa', 'sign', 'sha', 'bad', 'ok', 'error', 'auth']
matches = []
for line in lines:
    parts = line.strip().split(None, 1)
    if len(parts) == 2:
        off, s = parts
        if any(k in s.lower() for k in keywords):
            matches.append((int(off, 16), s))

matches.sort()
for off, s in matches:
    print(f'0x{off:06x}: {s}')
" 2>/dev/null
```

預期輸出顯示驗簽相關字串集中在某個 offset 範圍（例如 `0x9a000-0x9c000`），這就是 `lib/rsa/` 和 `common/image-fit.c` 被編譯進去的大致位置。

```bash
# 步驟 7：Ghidra 操作指引（截圖描述）
# 截圖描述 7-1：
#   Ghidra → Import u-boot.bin
#   Language: AARCH64:LE:64:v8A:default
#   Format: Raw Binary
#   Image Base: 0x40200000（從 U-Boot CONFIG_SYS_TEXT_BASE 取得）

# 截圖描述 7-2：
#   Auto Analyze 完成後，Search → Search for Strings
#   輸入 "Verifying Hash Integrity"，找到對應 data object

# 截圖描述 7-3：
#   右鍵 data object → References → Show References to Address
#   看到哪個函式在 xref list 裡（通常是 fit_image_verify_required_sigs 或類似名稱）

# 截圖描述 7-4：
#   點擊 xref → 跳到呼叫這個字串的函式
#   Function Call Graph 往上展開（右鍵 → Show Call Graph），找到完整驗簽呼叫鏈

# 截圖描述 7-5：
#   找到 memcmp 的呼叫點（就在 "Verifying Hash Integrity" print 附近）
#   分析 memcmp 之後的條件跳轉：
#     CBZ W0, success_path     ← W0=0 表示 memcmp 相等（hash 匹配）
#     B.NE fail_path           ← 不相等則驗簽失敗
#   記錄這個 branch 的 offset，這是 fault injection / patch bypass 的目標
```

---

## BootROM 逆向的困難（未實測說明）

真機 BootROM 的逆向在研究者社群有案可查（Checkm8 / Fusée Gelée / MTK bypass），但過程需要真實硬體，本課無法實測。說明主要困難：

### 難點一：BootROM 讀取

| 方法 | 適用 | 限制 |
|------|------|------|
| JTAG / SWD 接線讀取 | 有開放 debug port 的 SoC | fuse 燒了之後 JTAG disabled |
| BH Mode / Test Mode | 部分 SoC 有廠商測試接口 | 需知道 secret handshake sequence |
| exploit 讀出（bootstrap） | Fusée Gelée / Checkm8 | 需要 BROM 漏洞作為入口 |
| 已洩漏或逆向社群共享 | 少數 SoC 已有公開版本 | Apple BootROM 有人用 checkm8 dump |

### 難點二：加密 / 混淆

部分 SoC 廠商（華為 / Samsung 特定型號）的 BootROM 或 BL1 是加密的，必須先找解密 key（有時在 SoC 內部 AES key SRAM，用 fault injection 讀取）才能看到明文指令。

### 難點三：無任何參考符號

BootROM 裡沒有任何字串（連 log 都沒有），所有函式完全靠逆向類型推斷。但有規律可循：
- 必定有 vector table（找 `0xE59FF018` 或 AArch64 的 64x128 bytes 向量結構）
- 必定有 USB / UART 初始化（找 MMIO 操作序列，對照 SoC datasheet）
- 必定有 SHA / RSA 函式（找前述特徵）
- 必定有 efuse 讀取（找對應的 MMIO 位址，廠商 TRM 是唯一參考）

---

## 踩雷

1. **Thumb/ARM mode 設錯是最常見的第一個坑**：ARMv7 binary 如果從錯誤 mode 開始分析，整份 disassembly 都是廢碼，幾乎看不到任何 `PUSH {LR}` / `POP {PC}` 的函式框架。正確識別方法：找 vector table 的 reset vector，如果 reset vector 地址的 bit 0 是 1（例如 `0x00000021` 而非 `0x00000020`），那就是 Thumb mode（ARM CPU 的 INTERWORK bit 慣例）。

2. **base address 設錯導致 call graph 斷掉**：如果 `BL` 指令的 target 落到 Ghidra 尚未分析的 range 外面，Ghidra 會把那個 BL 當 external call（畫成 `thunk_FUN`），function call graph 就斷了。症狀：大量 `thunk_FUN_xxxxxxxx` 且都是 undefined。解決：先用 python3 腳本統計疑似位址範圍（前述方法）再修正 base address，重跑 Auto Analyze。

3. **data / code 邊界**：ARM binary 常有 literal pool（`LDR PC, [PC, #xxx]` 後面接一些 4-byte 常數），如果 Ghidra 把 literal pool 分析成 code，後面的函式起始點就判錯。看到一串沒有意義的 `MOV R0, #0x????????` 序列（而且都是 `E3A0xxxx` 這類 encoding），那是 literal pool 被誤認了。右鍵 → Clear Code Bytes，再手動 Define Data。

4. **MTK Preloader 的 header 需要手動剝離**：直接把含 header 的 Preloader 載入 Ghidra，header 的 8 bytes magic 會被分析成 ARM 指令（`ANDRBOOT` = `4D 4D 4D 20...` = `STMIB R1, {R13, R14}` 之類的合法但無意義指令）。先用 Python 剝去 header，只餵 ARM binary 部分給 Ghidra，並把 Image Base 設成 header 裡的 load_address。

5. **ADRP 只在 AArch64 有，ARMv7 沒有**：前面的 ADRP 分析腳本只適用 AArch64。ARMv7 binary 用 `MOVW/MOVT` 組合載入 32-bit 絕對位址，或用 LDR PC + literal pool。兩者的逆向策略不同，不要混用。

6. **SoC-specific MMIO map 要查 TRM**：ARM binary 中大量出現的 `0xXXXXXXXX` 立即數很多是 MMIO 位址（GPIO controller、UART base、clock controller 等）。不查 SoC TRM，這些看起來像亂數。建立一份「目標 SoC 的 MMIO map」，在 Ghidra 的 Memory Map 裡加入對應的 overlay，或建立 label（`UART_BASE = 0x10007000`），大大改善 decompiler 輸出的可讀性。

---

## 進階延伸

- **Fusée Gelée 的 BootROM RE 細節**：Kate Temkin 的原始報告描述了如何在沒有 JTAG 的情況下逆向 Tegra X1 的 BROM，靠的是 USB recovery mode 的洩漏（讓 BROM 回傳 SRAM 內容）+ 漏洞利用後 dump ROM。這是「靠漏洞自舉讀 ROM」的典型手法，值得詳讀流程。

- **MTK Preloader 的靜態分析自動化**：研究者（xiaomi RE、xen0n 等社群）對 MTK Preloader 做過系統性 RE，在 GitHub 有帶 symbol 的 IDA / Ghidra project。對照已有符號的版本和你的目標版本做 binary diffing（Ch 26 的主題），可以快速在新版找到舊版已識別的函式。

- **LLVM-based lifter（RetDec / McSema）**：對於 Ghidra 反編譯品質不佳的 ARM binary，可以試用 RetDec 把 ARM binary lift 成 LLVM IR，再用 IR-level 工具做資料流分析。特別適合找 memcmp 的 constant-time 替換版本（有的驗簽函式故意不用 memcmp，用自訂的 constant-time 比較）。

---

## 動手練習

1. **vector table 偵察**：取任意一個 ARM Linux kernel zImage（從 Raspberry Pi OS 的 `/boot/kernel8.img`），用 Python 腳本在前 512 bytes 找 AArch64 exception vector 的特徵，計算並列出所有 handler 的偏移量。

2. **U-Boot strings 到 Ghidra**：下載一份開源路由器韌體（OpenWRT 的 `openwrt-*.bin`），用 binwalk 解出 U-Boot binary，用 strings 找驗簽相關字串，記錄 offset，在 Ghidra 中設定正確的 base address（查 OpenWRT 的 `target/linux/<platform>/` config），跳到對應位址確認確實找到了可讀的 ARM 指令。

3. **RSA 函式識別**：在 Ghidra 對一個帶驗簽的 U-Boot binary 開 Function Complexity 分析（**Window → Script Manager → 找 complexity 相關腳本**），列出最複雜的 10 個函式，判斷哪個是 RSA 大數模冪（特徵：大量 MUL/UMULH、固定 256-byte buffer 操作）。

---

## 本章重點

- ARM/AArch64 raw binary 載入 Ghidra 的三個關鍵設定：**正確 Language（v7/v8A、LE/BE、32/64）、正確 Image Base、正確 ARM/Thumb 模式**
- Base address 判定方式：UART log 最準確，次之是 linker script，再次是 ADRP 統計（AArch64）或 MOVT/MOVW 統計（ARMv7）
- **Vector table 是 ARM binary 的入口標記**：ARMv7 找 `E59FF018`，AArch64 找 128-byte 對齊的 handler 序列
- 找驗簽函式的四個 pattern：字串→xref、memcmp 附近條件跳轉、RSA 大數運算特徵、SHA256 硬體指令
- MTK Preloader 有私有 header 需手動剝離，load_address 和 entry point 在 header 偏移 `0x10/0x14`
- 真機 BootROM 逆向需要硬體工具或已知漏洞協助讀取，本課標「未實測」，Fusée Gelée 是公開的最佳教學案例

---

## 自我檢核

- [ ] 能說出 ARMv7 exception vector table 的格式（每個 entry 是什麼指令，共幾個），以及如何在 raw binary 中找到它
- [ ] 能說明 Thumb mode 的識別方法（bit 0 的含義）以及如何在 Ghidra 中切換 disassemble mode
- [ ] 能列出至少三種 ARM binary base address 判定的方法，並說明各自的適用場景
- [ ] 能用 `strings` + `binwalk` 在 5 分鐘內定位一個未知 U-Boot binary 的驗簽相關程式碼 cluster
- [ ] 能解釋為什麼 MTK Preloader header 需要剝離，以及 load_address 和 jump_addr 的正確提取方式
- [ ] 能在 Ghidra 中用 xref 從「Bad Data Hash」字串反查到 memcmp 呼叫，並識別成功/失敗的條件跳轉

---

## 延伸閱讀

1. **"Fusée Gelée: Tegra BROM exploit" — Kate Temkin（2018）（`https://github.com/Qyriad/fusee-launcher`）**
   讀哪裡：README 的完整技術分析；特別是「How We Found the Exploit」段落描述 BROM 逆向方法
   學什麼：在沒有 JTAG 的情況下如何靠 USB recovery mode 洩漏資訊、逆向推斷 BROM 函式、最終 dump ROM 並驗證漏洞——從零到 exploit 的完整 ARM BootROM RE 教學
   關聯：Ch 21 T5 類型（替代路徑/USB RCM mode）的最佳實作案例；本章 ARM RE 技能的終極應用場景

2. **U-Boot "Verified Boot" 設計文件（`https://docs.u-boot.org/en/latest/usage/fit/verified-boot.html`）**
   讀哪裡：整份文件約 2000 字；重點是 FIT image 格式說明和 `verify_image` 的呼叫路徑描述
   學什麼：U-Boot 驗簽的設計意圖——CONFIG_FIT_SIGNATURE 控制哪些邏輯、verify_required 和 verify_optional 的語義差異、哪些函式是 RE 的優先目標
   關聯：Ch 17（U-Boot 深入與攻擊面）補充本章的 RE 視角；動手練習 2 的理論支撐

3. **"Reversing MTK Preloader with Ghidra" — 社群文章（多篇，搜尋 "MTK preloader Ghidra"）**
   讀哪裡：搜尋 xda-developers 或 GitHub 的 `mtk-preloader-re` 相關 repository，找帶截圖的操作記錄
   學什麼：本章 MTK Preloader 格式說明的實際操作驗證——header 解析、load_address 設定、以 Ghidra 找 `[SEC] SBC` 字串到驗簽函式的完整流程
   關聯：Ch 20（MTK vendor SoC 韌體）提供的 SoC 背景知識；Ch 26（找後門與 diffing）的 MTK 版本比對

→ [下一章](./26-backdoors-and-diffing.md)
