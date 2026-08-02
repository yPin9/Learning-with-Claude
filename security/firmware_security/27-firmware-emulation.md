# Ch 27 — 韌體 emulation 做動態分析

> **目標**：理解為什麼靜態逆向不夠、動態分析能補什麼；掌握四種韌體動態分析工具（QEMU+OVMF、Qiling、Unicorn、firmadyne/FirmAE）的適用場景和操作流程；建立一個可以下 GDB 中斷點的 UEFI 除錯環境（真跑）。

---

## 靜態逆向的天花板

靜態逆向給你的是「程式可能走的路」，動態分析給你的是「程式實際走的路」。兩者的差距在以下情境最明顯：

```
問題場景一：條件分支
  if (EFI_ERROR(gRT->GetVariable(...))) → 哪邊？
  靜態：兩條路都分析
  動態：直接告訴你執行到哪條

問題場景二：自解碼 / 自改 code
  UEFI 的 PEI decompressor、某些 DRM 保護的嵌入式韌體
  靜態：看到加密的 blob，分析 decompressor 邏輯
  動態：跑起來，在 decompressor 之後 dump 記憶體，直接看解密後的 code

問題場景三：Protocol callback 追蹤
  UEFI 的 protocol 是執行時期安裝的；靜態分析要手動追 gBS->InstallProtocol xref
  動態：在 protocol 安裝的時刻下斷點，直接看哪個模組裝了哪個 handler

問題場景四：周邊互動
  讀一個 GPIO register，判斷是否插著 JTAG → 只有動態才能觀察實際值
```

---

## 方案一：QEMU + OVMF（完整 UEFI 模擬，真跑）

QEMU + OVMF 是最「完整」的 UEFI 動態分析環境。它模擬一台 x86-64 機器，OVMF 就是跑在上面的 UEFI firmware。

優點：完整的 UEFI 環境（SEC/PEI/DXE/BDS 全部跑），可以 GDB attach，可以注入自製 DXE driver 測試。  
缺點：只能模擬 OVMF，不能直接跑廠商 binary（廠商 BIOS 有 SMM handler 和 vendor-specific 元件，QEMU 不模擬）。

### 安裝

```bash
# ── 真跑 ──
sudo apt-get install -y qemu-system-x86 ovmf gdb
ls /usr/share/OVMF/
# OVMF_CODE.fd  OVMF_VARS.fd  （code = firmware ROM，vars = NVRAM）
```

### 基本啟動（帶 GDB stub）

```bash
# ── 真跑 ──
# 複製 VARS（每次啟動前建議用新的，OVMF 會修改 VARS）
cp /usr/share/OVMF/OVMF_VARS.fd /tmp/OVMF_VARS_debug.fd

qemu-system-x86_64 \
  -machine q35 \
  -m 256M \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/OVMF_VARS_debug.fd \
  -nographic \
  -serial mon:stdio \
  -S \
  -gdb tcp::1234
# -S：啟動後立刻暫停（等 GDB attach）
# -gdb tcp::1234：GDB remote stub 監聽在 localhost:1234
# -nographic：不開視窗，輸出到 terminal
```

### GDB Attach 與設定

```bash
# ── 真跑 ──（另一個 terminal）
gdb
(gdb) target remote localhost:1234
# 此時 QEMU 暫停在最早的 reset vector（0xFFFFFFF0）
(gdb) info registers
# rip = 0x000000000000fff0 → x86 real mode 的第一條指令

# OVMF 是 64-bit binary，但從 real mode 開始
# 先 continue 讓 OVMF 進入 64-bit long mode
(gdb) continue
# 在另一個 terminal 看 QEMU 輸出，等 UEFI shell 出現後 Ctrl+C 暫停
```

### 追蹤 UEFI 模組的載入

OVMF 開機時 DXE Core 會依序載入 DXE driver。每個 driver 在 PE image 被解壓縮並重定位後，UEFI 才跳到它的 entry point。GDB 沒有自動知道哪個地址對應哪個模組——這需要一個「gdb helper」。

```bash
# ── 真跑 ──
# edk2 提供了 GDB script 幫助載入符號（如果有 debug build）
# 下載 tianocore edk2 debug symbols（需自行 build OVMF debug version）
# https://github.com/tianocore/tianocore.github.io/wiki/How-to-debug-OVMF-with-GDB

# OVMF debug build（WSL 上需要 EDK2 build tools）
# 以下是概念步驟，WSL 環境實際 build 需要 30-60 分鐘

git clone https://github.com/tianocore/edk2.git
cd edk2
git submodule update --init
make -C BaseTools
source edksetup.sh

# 設定 build config（OvmfPkg，debug 版）
build -a X64 -t GCC5 -p OvmfPkg/OvmfPkgX64.dsc -b DEBUG

# build 輸出在：Build/OvmfX64/DEBUG_GCC5/FV/OVMF.fd
# .debug 符號在：Build/OvmfX64/DEBUG_GCC5/X64/*.debug
```

### 不 build 也能用的 debug 方法：硬體斷點 + 記憶體搜尋

沒有符號也能做有效的動態分析：

```bash
# ── 真跑 ──
# 讓 OVMF 啟動，等到 UEFI shell 出現後，Ctrl+C 暫停

# 在記憶體中搜尋 OVMF 載入的 DXE 模組
# UEFI PE image 以 MZ (0x4D 0x5A) 開頭
(gdb) find /b 0x0, 0x100000000, 0x4D, 0x5A
# 找到所有 MZ header 的地址，對應各個已載入的模組

# 設定記憶體 watchpoint（監視特定地址被讀寫）
(gdb) watch *0x7E000000   # 某個可疑的 NVRAM region

# 設定硬體 breakpoint（在某個地址）
(gdb) hbreak *0x7F000000  # 某個模組的 entry point（從靜態分析的 rva 加上 base 算）

# 繼續執行
(gdb) continue
```

### UEFI Shell 下的動態分析技巧

```bash
# ── 真跑 ──
# 在 QEMU 的 UEFI shell 下：

# 1. 列出所有已載入的 image 及其 base address
Shell> dh -b    # dump handle database

# 2. 用 UEFI shell 的 mm 命令讀記憶體
Shell> mm 0x7F000000 0x100 -n   # 讀 0x100 bytes

# 3. 載入自製 DXE driver（測試 exploit）
Shell> fs0:\MyTestDxe.efi

# 4. 在 GDB 端配合：看到 shell 載入新 .efi 後，從 0 搜 MZ，找到新載入的 base
# (gdb) find /b 0x0, 0x100000000, 0x4D, 0x5A  → 再搜一次，找新出現的地址
```

---

## 方案二：Qiling Framework（UEFI / BootROM 沙箱模擬）

Qiling 是一個多架構的 userspace emulator，底層使用 Unicorn（CPU 模擬），上層提供 OS 和 UEFI 的 API hook。

優點：不需要完整 QEMU 環境，可以只模擬單一 DXE driver，hook 任意 UEFI protocol 呼叫，Python 腳本化，速度快。  
缺點：UEFI 支援是「部分」的——gBS/gRT 常見的 API 都有模擬，但複雜的 protocol interaction 和真正的 DXE Core lifecycle 不在 scope 裡。

```bash
# ── 真跑 ──
pip3 install qiling
```

### Qiling 模擬 UEFI DXE driver

```python
#!/usr/bin/env python3
# qiling_uefi_demo.py
# 用 Qiling 跑一個 DXE .efi，hook 所有 gBS 呼叫
# ── 真跑（需要從 OVMF dump 取得 efi 檔案）──

from qiling import Qiling
from qiling.const import QL_VERBOSE
from qiling.os.uefi.type import EFI_STATUS

# OVMF 的 UEFI vars 和系統表（Qiling 需要這些來模擬環境）
# 取得方式：從 OVMF VARS.fd 解析，或用 Qiling 附帶的 test profile
ROOTFS = "/path/to/qiling/examples/rootfs/x8664_efi"  # Qiling repo 裡有範例

def hook_GetVariable(ql):
    """攔截 EFI_RUNTIME_SERVICES.GetVariable"""
    # 讀取 VariableName 參數（rcx 是第一個參數在 x86-64 Windows calling conv）
    # UEFI 用 MS calling convention
    var_name_ptr = ql.arch.regs.rcx
    # 這裡可以 log、修改回傳值、注入假資料
    print(f"[hook] GetVariable called, VariableName @ 0x{var_name_ptr:X}")

def hook_InstallProtocol(ql):
    """攔截 InstallMultipleProtocolInterfaces"""
    print(f"[hook] InstallProtocol called")
    # 可以記錄哪些 GUID 被安裝，追蹤 protocol 依賴關係

# 建立 Qiling 實例，設定 UEFI 執行環境
ql = Qiling(
    ["./SecurityStubDxe.efi"],   # 目標 DXE driver
    ROOTFS,
    verbose=QL_VERBOSE.DEBUG
)

# Hook gRT->GetVariable（gRT 的 offset 在 UEFI spec 裡固定）
# Qiling 的 uefi OS 層已知 gRT 位址，可以直接用 hook 名稱
ql.os.set_api("GetVariable", hook_GetVariable)
ql.os.set_api("InstallMultipleProtocolInterfaces", hook_InstallProtocol)

# 執行
ql.run()
```

### Qiling 做 BootROM 片段模擬

```python
#!/usr/bin/env python3
# qiling_bootrom_snippet.py
# 模擬 ARM BootROM 的一段驗簽邏輯（不需要完整 BootROM，只取片段）
# ── 概念示範，需要真實 BootROM binary 才能跑 ──

from qiling import Qiling
from qiling.const import QL_ARCH, QL_OS, QL_VERBOSE

# 讀取從 BootROM dump 取得的片段
BOOTROM_SEGMENT = open("bootrom_segment.bin", "rb").read()
BASE_ADDR = 0x00008000  # ARM BootROM 典型基址

def hook_verify_signature(ql):
    """假設驗簽函式在 0x8420，攔截它並觀察輸入"""
    # 在 ARM AArch32，函式參數在 r0-r3
    r0 = ql.arch.regs.r0  # 通常是 image ptr
    r1 = ql.arch.regs.r1  # 通常是 size
    r2 = ql.arch.regs.r2  # 通常是 signature ptr
    print(f"[hook] verify_signature(img=0x{r0:X}, size=0x{r1:X}, sig=0x{r2:X})")
    
    # 可以在這裡 patch：讓函式永遠 return 0（繞過驗簽）
    # ql.arch.regs.r0 = 0  # 回傳值 = 0（success）
    # ql.arch.regs.pc = 返回地址  # 跳過函式執行

ql = Qiling(
    code=BOOTROM_SEGMENT,
    rootfs="/",
    arch=QL_ARCH.ARM,
    ostype=QL_OS.BLOB,  # 沒有 OS，純 code 執行
    verbose=QL_VERBOSE.DEBUG
)

ql.mem.map(BASE_ADDR, 0x10000)
ql.mem.write(BASE_ADDR, BOOTROM_SEGMENT)
ql.hook_address(hook_verify_signature, BASE_ADDR + 0x420)

ql.run(begin=BASE_ADDR, end=BASE_ADDR + 0x1000)
```

---

## 方案三：Unicorn Engine（手動模擬一段 ARM BootROM）

Unicorn 是純 CPU 模擬，沒有 OS 層——你要手動設定記憶體、暫存器、hook，一切自己來。適合「只需要跑一段 code，不需要 OS API」的情境。

```python
#!/usr/bin/env python3
# unicorn_arm_demo.py
# 用 Unicorn 手動模擬一段 ARM BootROM，驗簽繞過示範
# ── 真跑（需要 unicorn 和 capstone）──

from unicorn import *
from unicorn.arm_const import *
from capstone import *

pip3_install = "pip3 install unicorn capstone"

# --- 模擬目標 ---
# 假設從 MTK BROM dump 提取到以下驗簽片段（純示範用 shellcode）：
# 這段 ARM Thumb code 執行一個簡化的「對比 magic word」驗章：
#   LDR r0, [r1]      ; 讀取 image 開頭的 magic
#   LDR r1, =0xDEADBEEF ; 期望值
#   CMP r0, r1
#   BEQ success       ; 相等 → 驗通
#   MOV r0, #1        ; 失敗 return
#   BX lr
# success:
#   MOV r0, #0        ; 成功 return
#   BX lr

CODE = bytes([
    0x09, 0x68,              # LDR r1, [r1]  (Thumb2)
    0x4F, 0xF0, 0xEF, 0x72, # MOV.W r2, #0xDEADBEEF
    # ... （簡化示意，實際要跑需要完整 Thumb bytecode）
])

# 真實案例：用 Unicorn 驗証「patch r0→0 能否繞過驗簽」
def emulate_verify(image_data: bytes, patch_bypass: bool = False):
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    
    CODE_ADDR = 0x8000
    DATA_ADDR = 0x10000
    STACK_ADDR = 0x20000
    
    # 映射記憶體
    mu.mem_map(CODE_ADDR, 0x4000)   # code
    mu.mem_map(DATA_ADDR, 0x4000)   # 待驗 image
    mu.mem_map(STACK_ADDR, 0x1000)  # stack
    
    # 寫入 code 和 image data
    mu.mem_write(CODE_ADDR, CODE)
    mu.mem_write(DATA_ADDR, image_data)
    
    # 設定暫存器
    mu.reg_write(UC_ARM_REG_R0, DATA_ADDR)   # r0 = image ptr
    mu.reg_write(UC_ARM_REG_SP, STACK_ADDR + 0x800)
    mu.reg_write(UC_ARM_REG_LR, 0x9000)     # 假設 return 到 0x9000
    
    result = [None]
    
    def hook_return(mu, address, size, user_data):
        """在 LR（return address）處 hook，抓回傳值"""
        if address == 0x9000:
            result[0] = mu.reg_read(UC_ARM_REG_R0)
            mu.emu_stop()
    
    def hook_code(mu, address, size, user_data):
        """Disassemble 每條指令（觀察執行流）"""
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        code_bytes = mu.mem_read(address, size)
        for insn in md.disasm(bytes(code_bytes), address):
            print(f"  0x{insn.address:X}: {insn.mnemonic} {insn.op_str}")
    
    mu.hook_add(UC_HOOK_CODE, hook_code)
    mu.hook_add(UC_HOOK_BLOCK, hook_return)
    
    # patch bypass：在執行前直接把 r0 設成 0xDEADBEEF（讓比對通過）
    if patch_bypass:
        image_data_patched = (0xDEADBEEF).to_bytes(4, 'little') + image_data[4:]
        mu.mem_write(DATA_ADDR, image_data_patched)
    
    try:
        # Thumb 執行時 start address 要 OR 1
        mu.emu_start(CODE_ADDR | 1, CODE_ADDR + len(CODE), timeout=5*UC_SECOND_SCALE)
    except UcError as e:
        print(f"[!] Unicorn 錯誤: {e}")
    
    return result[0]

# 測試正常 image（magic 不對）
FAKE_IMAGE = b'\x00\x00\x00\x00' + b'\x00' * 100  # magic=0
ret = emulate_verify(FAKE_IMAGE, patch_bypass=False)
print(f"正常 image 驗簽回傳: {ret}")  # 預期 1（失敗）

# 測試 patch bypass（magic 被 patch 成正確值）
ret = emulate_verify(FAKE_IMAGE, patch_bypass=True)
print(f"patch 後驗簽回傳: {ret}")  # 預期 0（成功）
```

---

## 方案四：firmadyne / FirmAE（Linux-based 韌體 rehosting）

路由器、NAS、IoT 裝置的韌體通常是「Linux-based」：一個壓縮的 Linux kernel + userspace（BusyBox 等）。firmadyne/FirmAE 的目標是讓這類韌體在 QEMU 上「整個跑起來」，包括 web interface。

```bash
# ── 未實測（需要真實 router firmware） ──
# FirmAE 環境需要 docker，安裝相對複雜

git clone --recursive https://github.com/pr0v3rbs/FirmAE.git
cd FirmAE
./download.sh   # 下載 binaries
./install.sh    # 安裝依賴

# 下載一個路由器韌體（以 D-Link 為例）
wget https://example.com/DIR-815_FIRMWARE.bin

# 用 FirmAE 分析
sudo ./run.sh -a Dlink DIR-815_FIRMWARE.bin
# -a：automatically run，嘗試用 QEMU 開機

# 成功後，FirmAE 告訴你 web interface 的 IP
# 然後可以對這個 IP 做 web 漏洞測試
```

### firmadyne 的內在限制

```
真實挑戰：
  周邊 stub：路由器韌體讀 GPIO、UART、watchdog timer
  → QEMU 不實作，程式 hang 或 crash
  
  NVRAM stub：很多路由器的設定存在 NVRAM（flash），QEMU 沒有
  → firmadyne 提供 libnvram.so 的 fake 實作
  
  Multi-process：路由器跑 httpd + dnsmasq + iptables + wdt
  → 要讓這些 daemon 都在 QEMU 裡起來

  Network：路由器有 WAN/LAN 分開，QEMU 的 network 要特殊設定

FirmAE 的統計：在 1,900 個 firmware 上，能跑起 web interface 的大約 79%
其餘 21% 卡在周邊 / NVRAM / 特定 SoC 指令集不支援
```

---

## 周邊模擬的困難與 stub 技巧

這是動態分析韌體最大的現實障礙：

```
困難一：沒有周邊，程式 loop 等待
  典型案例：
    while (!(SPI_STATUS_REG & SPI_READY));  // 等 SPI controller ready
    → QEMU 沒有這個 SPI controller，SPI_STATUS_REG 讀到 0，never ready
    → 無限 loop
  
  stub 技巧：
    Unicorn/Qiling 的 hook：在這個 register address 的讀取 hook 裡 return READY
    mu.hook_add(UC_HOOK_MEM_READ, lambda...: set_reg(READY_VALUE))

困難二：SoC-specific 指令
  部分 SoC 的 BootROM 使用廠商 extension 指令（e.g., MTK 的 custom coprocessor）
  → Unicorn 不認識 → illegal instruction exception
  
  stub 技巧：hook 在 illegal instruction 的 handler，手動模擬這條指令的效果

困難三：外部 flash 的內容
  BootROM 開機時從 SPI flash 讀資料，但 QEMU 可能沒有模擬那個 flash controller
  
  stub 技巧：hook SPI flash controller 的 register access，
  從 memory buffer（你提供的 flash dump）返回對應的資料
```

```python
# 周邊 stub 範例（Unicorn）
# hook 一個 UART status register，讓 TX 永遠 ready

UART_STATUS_REG = 0x10009000   # 假設 UART status 在這個地址
UART_TX_READY = 0x01

def hook_mmio_read(uc, access, address, size, value, user_data):
    if address == UART_STATUS_REG:
        # 不管什麼時候讀，都說 TX ready
        uc.mem_write(address, UART_TX_READY.to_bytes(4, 'little'))
        return True
    return False

mu.hook_add(UC_HOOK_MEM_READ, hook_mmio_read)
```

---

## 韌體 fuzzing 的接口（預告）

動態分析環境建立後，自然接 fuzzing：

```
UEFI fuzzing 接口：
  ① UEFI variable：gRT->SetVariable 是 DXE driver 最常讀的輸入
    → 對 OVMF 跑 hypercall-based fuzzer（TriforceAFL / kAFL 的 UEFI 版）
    → 每輪設定不同的 UEFI variable，重開 UEFI driver，觀察 crash
  
  ② UEFI capsule：廠商的 update mechanism 接受外來 binary
    → 對 capsule 結構做 grammar-based fuzzing
    → 呼應 advanced_fuzzing 課的 Ch 3（grammar fuzzer）
  
  嵌入式韌體 fuzzing 接口：
  ③ 網路輸入：firmadyne 跑起路由器後，對 web interface 做 HTTP fuzzing
    → AFL++ 的 network mode 或直接用 boofuzz
  
  ④ Snapshot fuzzing（Nyx）：
    在 UEFI 某個 interesting 入口點前 snapshot，
    每輪從 snapshot 恢復，注入不同輸入，快很多
    → 接 advanced_fuzzing Ch 16 的 Nyx snapshot fuzzer
```

---

## 動手：QEMU + OVMF 建 GDB 除錯環境（真跑）

### Step 1：準備環境

```bash
# ── 真跑 ──
sudo apt-get install -y qemu-system-x86 ovmf gdb

# 確認 OVMF 存在
ls -la /usr/share/OVMF/OVMF_CODE.fd /usr/share/OVMF/OVMF_VARS.fd

# 準備工作目錄
mkdir -p ~/uefi_debug
cp /usr/share/OVMF/OVMF_VARS.fd ~/uefi_debug/OVMF_VARS_work.fd
```

### Step 2：建立 UEFI shell disk image

```bash
# ── 真跑 ──
# 建立一個含 UEFI shell 的 FAT32 disk image，供 QEMU 開機後使用
cd ~/uefi_debug

# 建立 64MB 的 FAT32 image
dd if=/dev/zero of=uefi_disk.img bs=1M count=64
mkfs.fat -F 32 uefi_disk.img

# 掛載並放入 UEFI shell（ubuntu 的 edk2-shell 套件包含 Shell.efi）
sudo apt-get install -y edk2-shell  # 或從 tianocore release 下載
mkdir -p /tmp/uefi_mnt
sudo mount uefi_disk.img /tmp/uefi_mnt

# 建立 EFI boot 目錄
sudo mkdir -p /tmp/uefi_mnt/EFI/BOOT
sudo cp /usr/share/edk2-shell/x64/Shell.efi /tmp/uefi_mnt/EFI/BOOT/BOOTX64.EFI

sudo umount /tmp/uefi_mnt
echo "[+] uefi_disk.img 建立完成"
```

### Step 3：啟動 QEMU（帶 GDB stub）

```bash
# ── 真跑 ──
# Terminal 1：啟動 QEMU
cd ~/uefi_debug

qemu-system-x86_64 \
  -machine q35 \
  -m 256M \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=OVMF_VARS_work.fd \
  -drive file=uefi_disk.img,format=raw,id=hd0 \
  -device virtio-blk-pci,drive=hd0 \
  -nographic \
  -serial mon:stdio \
  -S \
  -gdb tcp::1234

# -S 讓 QEMU 暫停在第一條指令，等 GDB
# 看到以下訊息代表等待中：（沒有輸出，只是 pause）
```

### Step 4：GDB 連接並觀察

```bash
# ── 真跑 ──
# Terminal 2：GDB

gdb -q
(gdb) set architecture i386:x86-64
(gdb) target remote localhost:1234
# 成功：Remote debugging using localhost:1234

# 查看 CPU 狀態（此時在 real mode reset vector）
(gdb) info registers
# rip 應該是 0x000000000000fff0

# 設定一個軟體斷點到 long mode 的 OVMF 主迴圈
# 先 continue 讓 OVMF 啟動
(gdb) continue
# 在 Terminal 1 中會看到 UEFI 開機 log 和 UEFI shell prompt

# 在 Terminal 1 中輸入任何 UEFI shell 命令後，Terminal 2 的 QEMU 在繼續跑
# 用 Ctrl+C 在 GDB 中暫停
# 之後可以用 (gdb) x/20i $rip 查看當前執行點的指令

# 設定一個地址 breakpoint（假設你從靜態分析知道某個函式的 VA）
(gdb) break *0x7F000ABC
(gdb) continue
# 等 UEFI 執行到那個地址時 GDB 暫停

# 查看記憶體
(gdb) x/4gx 0x7F000ABC
# x = examine，4 = 4 unit，g = giant（8 bytes），x = hex
```

### Step 5：在 UEFI Shell 下執行自製 .efi

```bash
# ── 真跑 ──
# 把一個自製的 DXE driver / UEFI application 放到 uefi_disk.img
# 在 UEFI shell 執行，同時 GDB 可以設斷點

# 掛載 image，放入你的 .efi
sudo mount ~/uefi_debug/uefi_disk.img /tmp/uefi_mnt
sudo cp /path/to/MyUefiApp.efi /tmp/uefi_mnt/
sudo umount /tmp/uefi_mnt

# 重啟 QEMU（或繼續執行）
# 在 UEFI shell 中：
Shell> fs0:\MyUefiApp.efi

# 在 GDB 中，等 app 載入後找到它的 base address（從 UEFI shell 的 load info）
# 然後設定斷點在 app 的 entry point
```

---

## 工具選擇速查表

| 工具 | 適用場景 | 不適用 | 動手複雜度 |
|------|---------|--------|-----------|
| QEMU + OVMF | 完整 UEFI 環境除錯、DXE driver 測試 | 廠商 proprietary BIOS、SMM | 中（需 OVMF）|
| Qiling | 單一 DXE driver 快速分析、hook gBS/gRT | 多模組 protocol interaction | 低（pip install）|
| Unicorn | 任意 binary 片段（ARM BootROM fragment） | OS API 依賴的 code | 中（手動設 memory）|
| firmadyne/FirmAE | Linux-based router/IoT 整機 rehosting | UEFI、bare-metal RTOS | 高（docker 環境）|
| QEMU + 真實 fw | 部分廠商固件（如 coreboot 機型） | 需 vendor-specific SMM | 高（fw 相容性問題）|

---

## 踩雷

1. **`-S` 讓 QEMU 停在 real mode，不要在這裡設 64-bit 斷點**：OVMF 從 reset vector 的 real mode 走到 long mode 要一段時間（SEC → PEI 轉換）。在 real mode 設的斷點地址在 long mode 可能無效。建議先 continue 讓 OVMF 進 long mode，再 Ctrl+C 暫停設斷點。

2. **GDB 的 `break` vs `hbreak`**：UEFI 的 code 是直接 mapped 的，沒有 page protection 問題，軟體斷點（`break`，用 0xCC int3 patch）通常可以用。但如果 OVMF 的某些段是 read-only 的，`break` 會失敗，改用硬體斷點 `hbreak`（需 QEMU 的 `-enable-kvm` 或 x86 debug register 支援）。

3. **Qiling 的 UEFI 模擬版本和 UEFI spec 有差距**：Qiling 的 UEFI 支援在持續更新，但某些 protocol（特別是 security 相關的，如 `EFI_IMAGE_SECURITY_DATABASE_GUID`）可能沒有完整實作，呼叫時直接 return EFI_UNSUPPORTED。分析結果要回到 QEMU 真跑確認。

4. **firmadyne 需要 MIPS/ARM QEMU 支援，且路由器 kernel 版本很舊**：很多路由器跑 Linux 2.6，firmadyne 的 QEMU 是舊版，不是你 WSL 裡的 QEMU。跑 FirmAE 前先確認自己的 QEMU 版本支援目標架構（`qemu-system-mips`、`qemu-system-arm`）。

5. **Unicorn 和 Capstone 的版本要配對**：Unicorn 2.x 和 Capstone 4.x/5.x 的 API 有些不相容。安裝時用 `pip3 install unicorn==2.0.1 capstone==5.0.0` 明確指定版本，避免版本衝突。

6. **QEMU GDB stub 是 RSP 協定，部分 GDB 版本的 x86 real mode 支援不好**：在 real mode 用 `info registers` 可能看到奇怪的 32-bit 或 64-bit 值。用 `set architecture i386:x86-16` 切換到 real mode 架構再看，或直接先 continue 到 long mode 再開始分析。

---

## 進階延伸

- **kAFL + UEFI（snapshot fuzzing）**：Intel 的 kAFL 可以搭配 QEMU-PT（Intel PT）對 UEFI 做硬體覆蓋率引導的 fuzzing。`nyx-net` 和 `AFL++ QEMU mode` 也有類似的 UEFI snapshot 模式。接 advanced_fuzzing 課的 Nyx 章節。
- **UEFI debug build + source-level GDB**：如果自己從 edk2 build 出 OVMF（debug 版），可以讓 GDB 看到 source code 並設函式名稱的斷點（`break SecurityStubDxe.c:123`）。這是最舒服的除錯環境，但 build 時間和 setup 成本高。
- **Renode（ARM SoC 周邊模擬）**：Renode（Antmicro 開發）的 ARM SoC 模擬比 Unicorn 完整——它有完整的 peripheral model（UART/SPI/I2C/GPIO），適合「ARM SoC BootROM 整個跑起來」的場景。是 Unicorn 的上一層工具。

---

## 本章重點

- 動態分析補靜態逆向的四大盲點：實際執行路徑、自解碼、protocol 安裝追蹤、周邊實際值
- QEMU + OVMF 是最完整的 UEFI 動態環境，`-S -gdb` 讓 GDB 全程 attach
- Qiling 的 UEFI hook 適合單一 DXE driver 快速分析，Python 可腳本化
- Unicorn 是最底層的選擇，適合 ARM BootROM 片段或需要完全控制的場景
- firmadyne/FirmAE 解決 Linux-based router/IoT 整機 rehosting，但周邊 stub 是真正的挑戰
- 周邊模擬的核心技巧：在 MMIO register 的讀取 hook 裡返回 stub 值
- 動態分析環境建立後，下一步自然是 fuzzing（接 advanced_fuzzing）

---

## 自我檢核

- [ ] 能啟動 QEMU+OVMF 並成功用 GDB 從 localhost:1234 attach
- [ ] 知道 `-S` 和 `-gdb` 各自做什麼，知道 OVMF 從 real mode 進 long mode 的過程
- [ ] 能說出 Qiling 適合什麼場景、不適合什麼（單 driver vs 完整環境）
- [ ] 能解釋 Unicorn 如何用 memory hook 做周邊 stub
- [ ] 知道 firmadyne 的 NVRAM stub 是什麼，為什麼需要它
- [ ] 能解釋韌體 fuzzing 的三種接口（UEFI variable / capsule / network input）

---

## 延伸閱讀

1. **"Debugging UEFI with QEMU and GDB" — tianocore wiki**
   讀哪裡：`github.com/tianocore/tianocore.github.io/wiki/How-to-debug-OVMF-with-GDB`
   學什麼：edk2 debug build 的完整步驟；GDB helper script 如何自動解析 UEFI image base address 和載入符號
   關聯：直接對應本章 QEMU+OVMF debug 環境的進階版（有符號的 source-level debug）

2. **Qiling 官方文件 UEFI 章節**（`github.com/qilingframework/qiling/tree/master/examples/uefidxe`）
   讀哪裡：repo 下的 `examples/uefidxe/`，特別是 `example.py` 和 `uefi_firmware_loader.py`
   學什麼：Qiling 如何模擬 EFI_SYSTEM_TABLE、gBS/gRT；如何 hook 常見 protocol 呼叫；UEFI rootfs 的結構
   關聯：直接可執行的範例，對應本章 Qiling 一節

3. **"Firmadyne: Toward Automatic Emulation of Linux-based Firmware"（NDSS 2016）**
   讀哪裡：`firmadyne.com` 或直接在 Google Scholar 搜標題，有 PDF
   學什麼：firmadyne 的系統設計——如何做 NVRAM stub、network replay、automatic boot detection；以及其限制（哪些固件跑不起來、為什麼）
   關聯：對應本章 firmadyne 一節，也是理解 FirmAE 改進了什麼的必讀背景

→ [下一章](./practice-d-firmware-re-report.md)
