# Ch 0 — 環境搭建

> **目標**：在 WSL（Ubuntu 22.04）上備齊本課全程所需的工具鏈，並用三段真實指令確認環境健全：OVMF 跑進 UEFI shell、swtpm 起來並讀 PCR、Python 成功 import uefi_firmware。
>
> **環境**：Windows 11 + WSL2 Ubuntu 22.04（本課所有 Linux 工具均在此執行）；真實硬體章節另行標注。

---

## 為什麼要先搭環境？

韌體安全研究的最大障礙不是難度，是「沒有可以動手的對象」。
真機韌體改錯會磚，Intel ME 分析需要特殊硬體，Secure Boot 的信任根(trust anchor)燒在 fuse 裡動不了。

但這門課絕大多數的概念都能在 QEMU + OVMF 裡用軟體模擬：

- 改 UEFI variable、塞惡意 DXE driver、看 PCR measurement 怎麼走——全部可以在 QEMU 裡死一百次重來。
- swtpm 是軟體 TPM，讓你在沒有真 TPM 晶片的機器上把 measured boot 的整條鏈跑完。
- uefi_firmware 讓你把韌體二進位檔拆開來看 FV/FFS 結構，不需要真機的 SPI flash。

本章的目的：讓環境在開始讀 Ch 1 之前就能動。

---

## 工具清單與用途

| 工具 | 套件 | 用途 | 本課主要出現章節 |
|---|---|---|---|
| `qemu-system-x86_64` | `qemu-system-x86` | 跑 OVMF（x86 UEFI），SMM 測試 | Ch 3–13、Part 5、Part 7 |
| `qemu-system-aarch64` | `qemu-system-arm` | 跑 AAVMF（ARM UEFI）、TF-A | Ch 15–19 |
| OVMF | `ovmf` | x86 UEFI 韌體映像（含 secboot 版） | Ch 2–13、Part 5 |
| AAVMF | `qemu-efi-aarch64` | ARM UEFI 韌體映像 | Ch 15–19 |
| `swtpm` | `swtpm` | 軟體 TPM 2.0 模擬器 | Ch 37–41、Practice F |
| `tpm2_pcrread` / `tpm2_startup` | `tpm2-tools` | 從 TPM 讀 PCR，做 measured boot 驗證 | Ch 37–41 |
| `uefi_firmware` (Python) | `python3-uefi-firmware` | 解析 FV/FFS/capsule 結構 | Ch 6、23 |
| `gcc` | `gcc` | 編譯 C 小程式、EFI stub 測試 | Ch 4、8、Practice A/B |
| UEFITool | 手動下載 | GUI 拆韌體 FV | Ch 23–24 |
| CHIPSEC | `pip install chipsec` | 平台安全稽核、PoC 執行 | Ch 11–13、Practice B |
| Ghidra | ghidra.re | 逆向 UEFI PE32+ 模組 | Ch 24–25 |

本章只處理前六行（靠 apt 裝的那批）。UEFITool / CHIPSEC / Ghidra 各自在用到的章節再裝。

---

## 安裝指令

一次性裝齊所有 apt 套件：

```bash
sudo apt update && sudo apt install -y \
  qemu-system-x86 \
  qemu-system-arm \
  ovmf \
  qemu-efi-aarch64 \
  swtpm \
  tpm2-tools \
  python3-uefi-firmware \
  gcc \
  build-essential
```

裝完確認版本：

```bash
qemu-system-x86_64 --version
qemu-system-aarch64 --version
swtpm --version
tpm2_pcrread --version
python3 -c "import uefi_firmware; print(uefi_firmware.__version__)"
gcc --version
```

真實輸出：

```
QEMU emulator version 6.2.0 (Debian 1:6.2+dfsg-2ubuntu6.31)
Copyright (c) 2003-2021 Fabrice Bellard and the QEMU Project developers

QEMU emulator version 6.2.0 (Debian 1:6.2+dfsg-2ubuntu6.31)
Copyright (c) 2003-2021 Fabrice Bellard and the QEMU Project developers

TPM emulator version 0.6.3, Copyright (c) 2014-2021 IBM Corp.

tool="tpm2_pcrread" version="5.2" tctis="libtss2-tctildr" tcti-default=tcti-device

1.16

gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
```

---

## 驗證一：OVMF 開機進 UEFI shell

OVMF（Open Virtual Machine Firmware）是 EDK II 的 x86 UEFI 實作，也是本課最常用的「受測目標」。
它的 Code 檔（`OVMF_CODE.fd`）是唯讀的韌體，Vars 檔（`OVMF_VARS.fd`）是 NVRAM——存 Secure Boot 金鑰、BootOrder 等 variable 的地方。

每次實驗前要複製一份乾淨的 Vars，避免污染：

```bash
cp /usr/share/OVMF/OVMF_VARS.fd /tmp/OVMF_VARS_test.fd

timeout 10 qemu-system-x86_64 \
  -machine q35 \
  -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/OVMF_VARS_test.fd \
  -nographic \
  -nodefaults \
  -serial stdio
```

真實輸出（截取、去 ANSI 色碼）：

```
BdsDxe: loading Boot0001 "EFI Internal Shell" from Fv(7CB8BDC9-F8EB-4F34-AAEA-3EE4AF6516A1)/FvFile(7C04A583-9E3E-4F1C-AD65-E05268D0B4D1)
BdsDxe: starting Boot0001 "EFI Internal Shell" from Fv(7CB8BDC9-F8EB-4F34-AAEA-3EE4AF6516A1)/FvFile(7C04A583-9E3E-4F1C-AD65-E05268D0B4D1)
UEFI Interactive Shell v2.2
EDK II
UEFI v2.70 (EDK II, 0x00010000)
Mapping table
    map: No mapping found.
Press ESC in 5 seconds to skip startup.nsh or any other key to continue.
Shell>
```

這段輸出說明了幾件事：

- `BdsDxe` 是 UEFI Boot Device Selection（開機裝置選擇）驅動，它找不到可開機磁碟，於是 fallback 到內建的 EFI Shell（`FvFile(7C04A583...)`）。
- `UEFI v2.70` 表示這份 OVMF 實作的 UEFI spec 版本。
- `map: No mapping found.` — 我們沒掛任何磁碟，UEFI shell 找不到 FS0: 之類的 mapping，這是預期行為。
- `Shell>` — UEFI Interactive Shell 等待輸入，代表韌體完整跑完 SEC→PEI→DXE→BDS 並成功進入 shell，環境健全。

`timeout 10` 讓 QEMU 在 10 秒後自動被 SIGTERM 終止，避免卡在互動模式。後面章節若需要真的輸入指令，把 `-nographic -serial stdio` 換成 `-display none -monitor stdio` 或從外部 pipe 進去。

---

## OVMF 檔案對照表

```
/usr/share/OVMF/
├── OVMF_CODE.fd          ← 標準 UEFI（無 Secure Boot 強制驗章）
├── OVMF_CODE.secboot.fd  ← 預載 Microsoft 金鑰、Secure Boot 開啟  (← .ms.fd 是 symlink)
├── OVMF_CODE_4M.fd       ← 4 MB 版（有些功能需要更大的 Flash）
├── OVMF_CODE_4M.secboot.fd
├── OVMF_VARS.fd          ← 標準 NVRAM（Secure Boot 關閉，無金鑰）
├── OVMF_VARS.ms.fd       ← 預載 MS DB/KEK/PK 的 NVRAM（配合 secboot Code 使用）
└── OVMF_VARS_4M.fd       ← 4 MB NVRAM
```

Part 5 繞 Secure Boot 的章節會切換到 `OVMF_CODE.secboot.fd` + `OVMF_VARS.ms.fd` 這對。

---

## 驗證二：swtpm + tpm2_pcrread

swtpm（Software TPM Emulator）模擬 TCG TPM 2.0，供 QEMU 掛載或直接透過 TCP socket 存取。
`tpm2-tools` 則是操作 TPM 的 CLI——`tpm2_startup` 初始化、`tpm2_pcrread` 讀平台配置暫存器(Platform Configuration Register, PCR)。

```bash
SWTPM_DIR=$(mktemp -d)

# 起 swtpm，TCP socket，--flags startup-clear 讓它自動執行 Startup(CLEAR)
swtpm socket \
  --tpmstate dir="$SWTPM_DIR" \
  --ctrl   type=tcp,port=2342 \
  --server type=tcp,port=2341 \
  --tpm2 \
  --flags startup-clear \
  --daemon --log level=0
sleep 1

export TPM2TOOLS_TCTI="swtpm:host=localhost,port=2341"

echo "--- PCR read (sha256:0,1,2,3,7) ---"
tpm2_pcrread sha256:0,1,2,3,7

pkill swtpm
rm -rf "$SWTPM_DIR"
```

真實輸出：

```
--- PCR read (sha256:0,1,2,3,7) ---
  sha256:
    0 : 0x0000000000000000000000000000000000000000000000000000000000000000
    1 : 0x0000000000000000000000000000000000000000000000000000000000000000
    2 : 0x0000000000000000000000000000000000000000000000000000000000000000
    3 : 0x0000000000000000000000000000000000000000000000000000000000000000
    7 : 0x0000000000000000000000000000000000000000000000000000000000000000
```

全零是對的——swtpm 剛啟動、沒有任何 firmware 量測值延伸(extend)進 PCR。Ch 38（Measured Boot 全鏈）會把 QEMU + OVMF + swtpm 接在一起，看 PCR[0] 從全零變成韌體的雜湊值。

PCR 語義速查：

| PCR | TCG 定義 |
|-----|----------|
| 0 | SRTM（BIOS/UEFI 韌體）測量 |
| 1 | UEFI 平台設定（CRTM 延伸） |
| 2 | Option ROM 程式碼 |
| 3 | Option ROM 設定 |
| 7 | Secure Boot 狀態（db/dbx/KEK/PK） |

---

## 驗證三：uefi_firmware Python 套件

```bash
python3 -c "
import uefi_firmware
print('uefi_firmware version:', uefi_firmware.__version__)

# 試著解析 OVMF_CODE.fd
with open('/usr/share/OVMF/OVMF_CODE.fd', 'rb') as f:
    data = f.read()

parser = uefi_firmware.AutoParser(data)
fw = parser.parse()
print('Firmware type:', type(fw).__name__)
print('First-level objects:', len(fw.objects) if hasattr(fw, 'objects') else 'N/A')
"
```

真實輸出：

```
uefi_firmware version: 1.16
Firmware type: EfiFirmwareVolume
First-level objects: 1
```

`EfiFirmwareVolume` 是解析到頂層固件卷(Firmware Volume, FV)的結果。Ch 6 會用這個套件深入拆 FFS（Firmware File System）格式，找出每個 DXE driver 的 GUID 與 PE32+ 區段。

---

## 驗證四：ARM QEMU + AAVMF（快速確認）

ARM 環境在 Part 3 才會大量用到，但這裡先確認 AAVMF 能起來：

```bash
cp /usr/share/AAVMF/AAVMF_VARS.fd /tmp/AAVMF_VARS_test.fd

timeout 8 qemu-system-aarch64 \
  -machine virt \
  -cpu cortex-a57 \
  -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/AAVMF_VARS_test.fd \
  -nographic \
  -nodefaults \
  -serial stdio 2>/dev/null | head -10
echo "AAVMF done"
```

AAVMF 同樣會進入 UEFI shell 或顯示「no bootable device」，輸出格式與 OVMF 一致。如果你看到 `UEFI Interactive Shell v2.2` 或 `BdsDxe`，代表 ARM 環境健全。

注意：AAVMF 的 Code/Vars 是 64 MB 大檔（每個），`ls -lh /usr/share/AAVMF/` 可以確認。這個大小是因為 ARM UEFI 需要更多空間來存放 TF-A 相關支援程式碼。

---

## QEMU 指令結構解剖

後面每一章幾乎都會用到 QEMU 指令，把各個 flag 的語義搞清楚比死背指令有用：

```
qemu-system-x86_64          ← 模擬 x86-64 機器（有 pflash/APIC/PCIe 完整支援）
  -machine q35               ← 晶片組選 Q35（Intel 82Q35，現代 UEFI 首選；替代是 i440FX，更舊）
  -m 256                     ← 系統 RAM 256 MB（OVMF 最低約需 128 MB）
  
  -drive if=pflash,          ← pflash = parallel flash interface，模擬 SPI NOR flash
    format=raw,              ← 直接讀取二進位，不用 qcow2
    readonly=on,             ← Code 槽：唯讀（改了會報錯）
    file=OVMF_CODE.fd        ← 韌體 Code 映像（包含 SEC/PEI/DXE 實作）
    
  -drive if=pflash,          ← 第二個 pflash 槽 = NVRAM
    format=raw,              ← 不加 readonly —— Vars 必須可寫
    file=OVMF_VARS.fd        ← NVRAM 映像（存 BootOrder、Secure Boot 金鑰、runtime var）
    
  -nographic                 ← 不開任何 GUI 視窗（WSL 裡沒有 X display）
  -nodefaults                ← 不自動加預設裝置（否則 QEMU 會加 VGA、USB 等，干擾輸出）
  -serial stdio              ← 把 UEFI shell 的 serial 輸出導到標準 I/O
```

幾個常用的變形：

| 用途 | 額外 flag |
|---|---|
| 掛一個可開機磁碟映像 | `-drive if=virtio,format=qcow2,file=disk.qcow2` |
| 開啟 QEMU monitor（控制台）| 把 `-serial stdio` 改成 `-monitor stdio -serial null` |
| 接 GDB 除錯（Ch 4 用到）| 加 `-s -S`（`-s` = gdbserver port 1234，`-S` = 暫停等 GDB 連線） |
| 掛 swtpm 給 OVMF 用 | 加 `-chardev socket,...` 和 `-tpmdev emulator,...` 和 `-device tpm-tis,...` |
| 限制 CPU 數量 | 加 `-smp 1`（單核，Debug 較簡單） |
| 啟用 KVM 加速 | 加 `-enable-kvm`（WSL2 支援，但 SMM 測試有時需要關掉） |

---

## 本章哪裡最容易出錯

**QEMU 開機沒輸出、卡住**：`-nodefaults -serial stdio` 組合把所有預設裝置拿掉，只留 serial。如果你的 WSL2 的 QEMU 是用 GTK 後端編的，加 `-display none` 才能確保沒有視窗彈出要求 display。

**tpm2-tools 連不上 swtpm（`tcti_from_file` 失敗）**：tpm2-tools 5.x 的 `TPM2TOOLS_TCTI` 格式是 `swtpm:host=...,port=...`，不是 unix socket 那個格式——兩者格式不同，容易貼錯。確認 `swtpm --daemon` 已啟動再執行 `tpm2_startup`。

**`--flags startup-clear` 省略造成 `0x00000101` 錯誤**：swtpm 預設不自動執行 `Startup(CLEAR)`，tpm2-tools 要求 TPM 必須先 startup 才接受命令。解法就是加 `--flags startup-clear`，或在連線後手動跑 `tpm2_startup -c`（但此時要確定 swtpm 還沒被任何 startup 過的 state 污染）。

**OVMF_CODE.fd 唯讀但沒給 readonly=on**：QEMU 會試著寫入，失敗報錯。`if=pflash` 的 Code 槽要永遠帶 `readonly=on`，Vars 槽才是可寫的。

---

## 進階延伸

想更貼近真實韌體研究環境，可考慮：

- **CHIPSEC**：在真實 x86 硬體（或 KVM 虛擬機）上稽核 SMM/BIOS 保護設定，`pip install chipsec` 後 `python chipsec_main.py`。Ch 11–13 用到。
- **EDK II 原始碼編譯**：從 tianocore/edk2 自己 build OVMF，你就能插入 debug print 觀察 DXE 載入過程，Ch 4 的 DXE driver 實作需要這個能力。
- **qemu-system-aarch64 + AAVMF**：把上面的 OVMF 實驗複製到 ARM，AAVMF 的 Code/Vars 在 `/usr/share/AAVMF/`，`-machine virt -cpu cortex-a57` 即可啟動。Ch 15 詳細做。

---

## 本章重點

- OVMF 是本課的主要受測目標，分「標準」與「secboot」兩種 Code + 對應的 Vars。
- swtpm 提供軟體 TPM 2.0，搭配 `--flags startup-clear` 避開手動 startup 問題。
- `TPM2TOOLS_TCTI="swtpm:host=localhost,port=N"` 是 tpm2-tools 連 swtpm TCP socket 的正確格式。
- uefi_firmware 1.16 能直接解析 OVMF_CODE.fd 並識別 FV 結構，是 Part 4 逆向的基礎工具。
- QEMU 命令中 Code pflash 加 `readonly=on`、Vars pflash 不加，這個區別在後面每一章都要用對。

## 自我檢核

- [ ] 我跑了 `OVMF_CODE.fd` 那段 QEMU 指令，看到 `Shell>` 提示符
- [ ] 我知道 `map: No mapping found.` 出現的原因，以及正常情況下 FS0: 是怎麼來的
- [ ] 我能說出 swtpm 使用 `--flags startup-clear` 的理由
- [ ] 我能說出 PCR[0] 和 PCR[7] 各量測什麼
- [ ] 我知道 OVMF_CODE.secboot.fd 和 OVMF_CODE.fd 的差別，以及什麼時候用哪個

## 延伸閱讀

1. **[OVMF Wiki — tianocore/edk2](https://github.com/tianocore/edk2/wiki/OVMF)**
   - 讀「Building OVMF」和「Running OVMF」兩節；學 OVMF 的 build flag 與 Secure Boot 預設金鑰的配置方式，對 Part 5 繞 Secure Boot 的實驗至關重要。

2. **[swtpm man page 與 IBM Research 部落格](https://github.com/stefanberger/swtpm/wiki)**
   - 讀「QEMU Integration」一節；學如何把 swtpm 當作 QEMU 的 `-tpmdev` backend，讓 OVMF 的 measured boot 真的把值延伸進 PCR。Practice F 直接用這個架構。

3. **[TCG PC Client Platform Firmware Profile Specification](https://trustedcomputinggroup.org/resource/pc-client-specific-platform-firmware-profile-specification/)**
   - 讀 Section 2（Concepts）和 Section 10（PCR Usage）；理解 PCR[0–7] 的分配標準，這是 Ch 38 Measured Boot 全鏈以及 Ch 39 Sealed key 的基礎語彙。

---

→ [Ch 1 為什麼攻韌體：Ring -2/-3 的世界](./01-why-attack-firmware.md)
