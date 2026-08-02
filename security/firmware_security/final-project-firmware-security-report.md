# Final Project — 端到端韌體攻防研究報告

> **這是整門課的收尾整合任務。** 你已經走過 Part 0-8：從 UEFI PI 攻擊面、SMM 聖杯、ARM 信任鏈、韌體逆向、Secure Boot 繞過鏈、硬體故障注入，到 TPM measured boot 和防守偵測。這個 Final Project 要求你把所有技能合成一次完整的韌體安全研究流程，產出一份結構化的研究/稽核報告。

---

## 專案目標與定位

### 你要做什麼

選一個目標平台，走完下面這條研究流程：

```
偵察（Reconnaissance）
    │  Ch 22-23：韌體取得、UEFITool/binwalk 解包
    ▼
攻擊面測繪（Attack Surface Mapping）
    │  Ch 2/7/11/17/21/29：信任邊界、類型學、攻擊面地圖
    ▼
深入一個點（Deep Dive）
    │  Ch 24-26/28-31：逆向一個模組、分析一條繞過鏈
    ▼
防守面分析（Defensive Analysis）
    │  Ch 42-45：偵測方法、廠商緩解、attestation
    ▼
報告（Report）
    │  整合本 Final Project 的報告模板
    ▼
產出：完整研究報告 + 可重現的驗證步驟
```

### 這是什麼，不是什麼

**是**：
- 安全研究 / 稽核報告（educational security research）
- 公開資訊分析 + 自架測試環境的實驗結果
- 學術/職涯 portfolio 的核心素材

**不是**：
- 武器化工具的開發
- 針對未授權目標的實際測試
- 用於繞過你不擁有的裝置的 exploit

所有 PoC 均在自己的 QEMU 環境或聲明的測試硬體上執行，或僅為假設性技術分析。

---

## 三選一目標軌道

依你的環境選一條軌道。三條都能產出完整的研究報告，深度不同但都算完整。

---

### 軌道 A — x86 UEFI（OVMF，可真跑）

**適合：** 手邊只有 WSL / Linux，裝了 `qemu-system-x86_64 + OVMF + swtpm`

**目標描述**：OVMF（Open Virtual Machine Firmware）是 EDK2 的開源 UEFI 實作，廣泛用於 QEMU/KVM 虛擬化。選它的理由：代碼開源、可 Ghidra 逆向做橫向比對、OVMF snakeoil build 提供 Secure Boot 測試金鑰、搭配 swtpm 可建完整 measured boot 鏈。

**研究問題**：
1. OVMF 的 DXE 信任邊界在哪裡？`DxeCore` 如何驗證 FFS 模組？
2. OVMF 的 Secure Boot 信任鏈（PK/KEK/db/dbx）在哪個 DXE 初始化，有無 race window？
3. 搭配 swtpm，PCR[0-7] 的 measurement 順序和預期值是否與 TCG FW Profile spec 一致？
4. 若故意塞一個未簽章的 EFI，系統在哪個節點擋下，log 裡有什麼？

**真跑步驟概要**：

```bash
# 0. 準備 OVMF secboot + snakeoil build（Ch 0 環境已裝）
ls /usr/share/OVMF/OVMF_CODE_4M.secboot.fd
ls /usr/share/OVMF/OVMF_VARS_4M.ms.fd   # snakeoil PK/KEK/db

# 1. 拆解 OVMF 韌體（uefi_firmware + UEFITool）
python3 -c "
import uefi_firmware
with open('/usr/share/OVMF/OVMF_CODE_4M.secboot.fd', 'rb') as f:
    data = f.read()
parser = uefi_firmware.AutoParser(data)
fw = parser.parse()
fw.dump('./ovmf_dump/')
"

# 2. 用 UEFITool CLI 確認 FV/FFS 結構
# UEFIExtract（UEFITool 的 CLI）列出所有 GUID
UEFIExtract /usr/share/OVMF/OVMF_CODE_4M.secboot.fd list

# 3. 定位 SecurityPkg — SecureBootConfigDxe / AuthVariableLib
# 搜 GUID: D9A9A34B-AA14-4C75-9E6C-625D12855E52 (SecBootConfig)
grep -r "D9A9A34B" ./ovmf_dump/ 2>/dev/null

# 4. 把目標 DXE GUID 的 .efi 用 Ghidra 開（Ch 24 步驟）
# 重點：找 VerifyImageAndAuthentication 路徑
# 在 Ghidra 搜 AuthenticodeVerify / RsaPkcs1Verify

# 5. 建 swtpm + OVMF 環境，量 PCR 值
mkdir -p /tmp/swtpm_state
swtpm socket \
    --tpmstate dir=/tmp/swtpm_state \
    --ctrl type=unixio,path=/tmp/swtpm.sock \
    --tpm2 --log level=20 &

qemu-system-x86_64 \
    -M q35 -m 2048 \
    -drive if=pflash,format=raw,unit=0,readonly=on,\
       file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd \
    -drive if=pflash,format=raw,unit=1,\
       file=/tmp/ovmf_vars.fd \
    -chardev socket,id=chrtpm,path=/tmp/swtpm.sock \
    -tpmdev emulator,id=tpm0,chardev=chrtpm \
    -device tpm-tis,tpmdev=tpm0 \
    -nographic 2>&1 | tee /tmp/ovmf_boot.log

# 6. 讀 PCR 值
tpm2_pcrread sha256:0,1,2,3,4,5,6,7

# 7. 故意塞未簽章 EFI，驗證系統攔截點
# 把一個用 openssl 自簽但 db 裡沒有的 EFI 放進 FAT EFI partition
# 觀察 boot log 的 EFI_SECURITY_VIOLATION 或 SHELL> 出現時機
```

**Ghidra 逆向焦點（Ch 24 導向）**：
- 目標 DXE：`SecurityPkg/VariableAuthenticated/SecureBootConfigDxe`
- 找到 `EnrollX509ToDb` / `DeleteSignatureEx` 的呼叫路徑
- 繪製 Secure Boot DB 更新的信任邊界：誰能寫 AuthVar？寫入前的驗簽邏輯？
- 對照 edk2 原始碼（`SecurityPkg/Library/AuthVariableLib/AuthVariableLib.c`），確認逆向結果

**軌道 A 產出**：
- OVMF FV 結構圖（ASCII 或表格）
- 目標 DXE 的 Ghidra function call graph（截圖 + 文字說明）
- PCR[0-7] 實測值 vs TCG spec 預期值對照表
- 未簽章 EFI 被拒絕的 log 片段 + 攔截節點分析

---

### 軌道 B — ARM / 嵌入式（binwalk + QEMU rehosting，半真跑）

**適合：** 對嵌入式有興趣，手邊有 `qemu-system-aarch64 + AAVMF + binwalk`

**目標描述**：選一個公開可下載的 router 或 IoT 韌體。推薦：
- **TP-Link Archer C7 v5**（AR9344 / MIPS，但驗簽邏輯簡單，適合練習 binwalk + 類型學）
- **OpenWrt 官方 build for Raspberry Pi 4**（AArch64，有 U-Boot，原始碼開放方便對照逆向）
- **Linksys WRT1900ACS**（ARM Cortex-A9，有 U-Boot with verified boot，真實量產韌體）

選 OpenWrt for RPi 4 這條最適合本課：有 U-Boot + AArch64 + 公開原始碼。

**研究問題**：
1. 韌體的 FIT image 結構是什麼？U-Boot 的驗簽是否啟用？
2. 如果驗簽啟用，用的是什麼 key？是否可從 binary 提取 public key？（T6 偵測）
3. 搜尋 Ch 21 六大類型：哪幾個 T 可能適用？
4. 用 QEMU rehosting 能讓 U-Boot 到什麼程度執行？能進 U-Boot shell 嗎？

**真跑步驟概要**：

```bash
# 1. 下載公開韌體
wget https://downloads.openwrt.org/releases/23.05.3/targets/bcm27xx/bcm2711/\
openwrt-23.05.3-bcm27xx-bcm2711-rpi-4-ext4-factory.img.gz
gunzip openwrt-23.05.3-bcm27xx-bcm2711-rpi-4-ext4-factory.img.gz

# 2. binwalk 解包分析（Ch 22 的核心工作）
binwalk -e openwrt-23.05.3-bcm27xx-bcm2711-rpi-4-ext4-factory.img

# 找 U-Boot image
binwalk --magic /usr/share/binwalk/magic/uboot \
    openwrt-23.05.3-bcm27xx-bcm2711-rpi-4-ext4-factory.img

# 3. FIT image 分析
# 提取 FIT image 後用 fdtdump
fdtdump < extracted_fit.itb | head -100
# 找 signature node：/configurations/conf-1/signature
# 找 hash algorithm 和 sign-algo

# 4. 提取 public key（若 U-Boot 有 verified boot）
# 在 u-boot.bin 找 RSA key structure
python3 - <<'EOF'
import struct, re

with open('u-boot.bin', 'rb') as f:
    data = f.read()

# 搜 RSA public key 的 ASN.1 header（DER 格式）
# 0x30 0x82 = SEQUENCE，後接 2 bytes 長度
matches = [i for i in range(len(data)-4)
           if data[i:i+2] == b'\x30\x82' and
              data[i+4:i+6] == b'\x30\x82']
print(f"RSA candidate offsets: {[hex(m) for m in matches[:5]]}")
EOF

# 5. 類型學快速掃描（Ch 21 T1-T6）
strings u-boot.bin | grep -iE "secure|signed|verified|test.*key|dev.*key"
# T1: 找 "secure_boot=disabled" 或 fuse 狀態
# T6: 找 "test" / "dev" key 關鍵字

# 6. QEMU rehosting（Ch 27）
# AArch64 U-Boot rehosting（簡化模式）
qemu-system-aarch64 \
    -M virt \
    -cpu cortex-a72 \
    -bios u-boot.bin \
    -serial stdio \
    -nographic 2>&1 | head -50
# 觀察 U-Boot 能啟動到什麼程度（DRAM init 通常失敗，但 banner 和早期 log 可見）
```

**Ghidra 逆向焦點（Ch 25 導向）**：
- 定位 `fit_image_verify` / `fit_check_format` 函式
- 追蹤 RSA 驗簽的呼叫鏈（通常是 `rsa_verify` → `rsa_verify_key`）
- 確認 hash 算法、padding scheme（PKCS1 v1.5 vs PSS）
- 用 Ch 21 類型學做假設歸類

**軌道 B 產出**：
- binwalk 分析報告（FIT image 結構，找到的 partition 清單）
- 類型學掃描表（T1-T6，每項：不適用 / 可能 / 已確認）
- Ghidra 驗簽函式呼叫圖（ASCII 簡圖或截圖說明）
- QEMU rehosting 嘗試結果（成功 / 失敗到哪一步，原因分析）
- 假設利用鏈（基於類型學，說明前提條件）

---

### 軌道 C — 純研究（選一條公開 bypass 鏈分析）

**適合：** 沒有測試環境、或想深入理解一個真實攻擊的技術細節

**選一條分析**（選你最有興趣的）：

| 選題 | 類型學歸類 | 難度 | 特色 |
|------|-----------|------|------|
| **BlackLotus**（CVE-2023-24932）| T4 + T6 | ★★★ | 第一個公開的 in-the-wild UEFI bootkit，Secure Boot 繞過史里程碑 |
| **LogoFAIL**（CVE-2023-40238 等）| T3a/T3b | ★★★ | Binarly 2023 年底，圖片解析器做跳板，影響幾乎所有主流 OEM |
| **BootHole**（CVE-2020-10713）| T3b + T5 | ★★ | GRUB2 config 解析 buffer overflow，引發 SBAT 撤銷機制的誕生 |
| **PKfail**（2024）| T6 | ★★ | 供應鏈類型，AMI/Insyde/Phoenix 測試金鑰洩漏進量產，影響 900+ 型號 |

**推薦選 BlackLotus**：技術深度最高，T4 + T6 組合是教科書級，ESET 和 Microsoft 都有詳細分析報告可以交叉驗證。

**BlackLotus 研究路徑**：

```
1. 閱讀一手資料（必讀清單）：
   - ESET: "BlackLotus UEFI bootkit: Myth confirmed" (2023-03)
     https://www.welivesecurity.com/2023/03/01/blacklotus-uefi-bootkit-myth-confirmed/
   - Microsoft: "Guidance for investigating attacks using CVE-2023-24932"
     https://support.microsoft.com/en-us/topic/...
   - UEFI Forum: dbx update announcement (2022-08)

2. 技術鏈重建（5 個關鍵節點）：
   ① 取得已撤銷但仍被 db 信任的 Windows boot manager 版本
   ② 利用 CVE-2022-21894（bootmgfw.efi policy bypass）進入 WinRE
   ③ 在 WinRE 關閉 Secure Boot（修改 NVRAM variable）
   ④ 安裝 UEFI bootkit（植入 MOK/NVRAM 或直接寫 ESP）
   ⑤ 重啟後 bootkit 常駐，即使重灌 OS 也無法移除

3. 類型學歸類：
   - T4（Rollback 未防護）：dbx 在 CVE 修補前未更新，讓舊版 bootmgfw 仍被 db 接受
   - T6（公開金鑰）的變形：不是金鑰洩漏，而是金鑰撤銷失效（db 信任但 dbx 沒更新）
   - T5（替代路徑）：透過 WinRE 進行而非正常 OS 路徑

4. 防守時間軸（重建 Microsoft 的回應）：
   2022-01: CVE-2022-21894 修補
   2022-08: dbx 更新（撤銷舊 bootmgfw）
   2023-03: ESET 公開 BlackLotus 分析
   2023-05: Microsoft KB5025885（強制 dbx 更新）
   2023-11: UEFI CA 2023 推出（更嚴格的撤銷架構）

5. 防守分析：
   - 為什麼 dbx 更新這麼慢？（OEM 相容性問題、Secure Boot policy 的更新困難）
   - SBAT vs dbx 的撤銷效率比較（Ch 32）
   - 如果啟用了 BootGuard Measured+Verified profile，能防住哪幾步？
```

**軌道 C 產出**：
- 完整技術鏈圖（每個節點：CVE 編號 / 技術動作 / 前提條件）
- 類型學歸類說明（T1-T6，說明為何歸此類）
- 防守時間軸（攻擊公開 → patch → 撤銷 → 架構改變）
- 「如果有 X，能防住哪一步？」分析（X = BootGuard / 即時 dbx 更新 / SBAT / Secure Boot OEM Mode）
- 延伸攻擊面分析（同一技術在 ARM AVB 上的對應是什麼？）

---

## 共同交付物：結構化研究報告模板

以下是三條軌道共用的報告結構。完成你的軌道後，填入這個模板，產出最終報告。

---

```markdown
# 韌體安全研究報告

**報告標題**：[目標名稱] 韌體安全分析  
**研究者**：  
**日期**：YYYY-MM-DD  
**軌道**：A / B / C  
**環境**：WSL Ubuntu 22.04 / QEMU x.x.x / binwalk x.x.x / Ghidra 11.x  
**性質**：教育性安全研究，所有測試在自架 QEMU 環境或聲明的測試平台執行

---

## 執行摘要（≤ 300 字）

一段話說明：目標是什麼、發現了什麼、最重要的風險點、防守建議的核心。

不寫空話（「發現了多個安全問題」）。寫具體的（「發現 DXE 驗簽在 XXX 函式的回傳值判斷反轉，屬於 T3d 類型，允許未簽章 payload 在 OVMF 模擬環境中執行」）。

---

## 目標描述

| 項目 | 值 |
|------|---|
| 目標平台 | |
| 韌體版本 | |
| 取得方式 | binwalk 解包 / OVMF 官方 build / 公開報告 |
| 研究範圍 | 限定在哪些元件 / 哪幾層信任鏈 |
| 排除項目 | （誠實聲明不在範圍內的東西） |

---

## 攻擊面測繪

### 信任鏈結構

```
（用 ASCII 繪製你的目標平台的信任鏈）

BROM / OVMF SEC
    │ 驗（什麼驗什麼）
    ▼
PEI / EarlyBoot
    │ 驗（什麼驗什麼）
    ▼
DXE / U-Boot
    │ 驗（什麼驗什麼）
    ▼
OS Loader / Kernel
```

### 攻擊面清單

| 元件 | 攻擊向量 | 前提條件 | 潛在影響 | 優先度 |
|------|---------|---------|---------|-------|
| （填入你分析到的攻擊面） | | | | |

### 類型學套用（Ch 21 T1-T6）

| 類型 | 在此目標的表現 | 狀態 | 備註 |
|------|-------------|------|------|
| T1: Fuse 未燒 | | 不適用 / 未確認 / 確認 | |
| T2: Debug Port | | | |
| T3a: Only-Header Verify | | | |
| T3b: Length Confusion | | | |
| T3c: TOCTOU | | | |
| T3d: Return Value | | | |
| T4: Rollback 未防 | | | |
| T5: 替代路徑 | | | |
| T6: 公開金鑰 | | | |

---

## 信任鏈深入分析

### 選定深入點

說明你選了哪個元件或函式做深入分析，理由（最高風險 / 最有趣 / 類型學判斷可能有問題）。

### 逆向分析（軌道 A/B）或鏈重建（軌道 C）

（Ghidra 截圖 / ASCII 函式圖 / 技術鏈描述）

重點問題：
- 驗簽流程的進入點在哪？
- 輸入（image header/body/signature）如何被解析？
- 失敗路徑：驗失敗時，系統做什麼？真的停止執行嗎？
- 發現了什麼值得注意的地方？（不一定是 exploit，anomaly 也算）

---

## 漏洞或利用鏈假設

### 假設利用鏈（Hypothetical Exploit Chain）

```
步驟 1：[前提條件]
    │ [技術動作]
    ▼
步驟 2：[技術動作]
    │ [需要什麼能力 / 工具]
    ▼
步驟 3：...
    │
    ▼
目標達成：[攻擊者獲得什麼能力]
```

**前提條件（必須誠實列出）**：
- 需要 ring-0 / root / 物理存取 / ？
- 需要什麼工具？
- 需要什麼額外漏洞？

### PoC 或驗證（真跑或假設）

**已真跑驗證**（軌道 A/B）：
```bash
# 貼上關鍵指令和輸出，說明驗證了什麼
```

**假設性驗證**（軌道 C 或未能真跑的部分）：
說明如果要驗證，需要什麼環境，期望看到什麼輸出，為什麼這個假設合理。

---

## 防守與偵測建議

### 緩解措施優先順序

| 優先度 | 建議 | 緩解哪個攻擊面 | 實作難度 | 對口廠商 / 設定 |
|-------|------|-------------|---------|--------------|
| P0（緊急） | | | | |
| P1（高）| | | | |
| P2（中）| | | | |

### 偵測方法

| 偵測目標 | 工具 / 方法 | 對應 Ch | 備註 |
|---------|-----------|--------|------|
| 韌體完整性監控 | CHIPSEC common.bios_wp / PR check | Ch 42 | 真實硬體才有意義 |
| PCR 值異常 | tpm2_pcrread + Remote Attestation | Ch 38/43 | swtpm 可驗概念 |
| UEFI variable 竄改 | efivar + audit log | Ch 5/45 | |
| Bootkit 特徵 | ESP 掃描 / ESET EFI Scanner | Ch 45 | |
| （補充你分析目標的特有偵測點） | | | |

### 防守有效性評估

對每個已知的防守機制，評估對你分析的攻擊假設是否有效：

| 防守機制 | 能防到第幾步？ | 防不住的原因 |
|---------|------------|------------|
| UEFI Secure Boot | | |
| BootGuard | | |
| SBAT / dbx 更新 | | |
| Remote Attestation | | |
| TPM Sealed Key | | |

---

## 未實測項誠實聲明

以下項目未在本報告中實際驗證，列出原因及補充的替代驗證方式：

| 項目 | 未實測原因 | 建議驗證方式 |
|------|---------|-----------|
| CHIPSEC 稽核 | 需要真實 x86 硬體（非 QEMU），環境未備妥 | 參照 practice-b-chipsec-audit.md 的方法，需真實 PCH |
| 真實硬體 SPI dump | 需要 CH341A / Flashrom 硬體 | 參照 Ch 22/35 的 SPI flash dump 方法 |
| 故障注入（voltage glitch）| 需要 ChipWhisperer / 專用 HW | 參照 Ch 34 的 Riscure 工具鏈說明 |
| JTAG 除錯 | 需要 JTAG 硬體和目標板 | 參照 Ch 36 的 OpenOCD 設定 |
| （其他你沒跑到的項目）| | |

**聲明格式**：所有「未實測」項目均明確說明原因。報告中所有已標示「真跑驗證」的項目均在 WSL Ubuntu 22.04 的 QEMU 環境中實際執行並取得輸出。

---

## 參考資料

（列出你實際讀過的資料。不要貼一堆沒讀的連結。）

### 一手資料
- （CVE 編號、官方 advisory、UEFI spec 章節）

### 技術報告
- （ESET、Binarly、Eclypsium 等研究報告，標明你讀了哪一節）

### 工具文件
- （UEFITool、CHIPSEC、Ghidra UEFI plugin 的關鍵文件段落）

### 課程對應章節
- （對應你引用的本課章節）
```

---

## 分階段里程碑

每個里程碑對應課程章節和具體產出。**按順序完成，別跳步**：M1 的輸出是 M2 的輸入。

### M1 — 偵察與韌體取得（預估 4-6 小時）

**對應章節**：Ch 22（取得韌體）、Ch 23（UEFITool/FFS 解析）、Ch 0（環境確認）

**工作**：
- [ ] 確認目標平台（選定軌道 A/B/C 並說明選擇理由）
- [ ] 取得韌體（OVMF build / 下載公開韌體 / 選定報告閱讀清單）
- [ ] 第一輪解包（`uefi_firmware` 或 `binwalk -e`）
- [ ] 繪製韌體高層結構（FV 數量、主要 DXE 清單、partition 表）

**真跑（軌道 A/B）**：
```bash
# 軌道 A
python3 -m uefi_firmware.analysis /usr/share/OVMF/OVMF_CODE_4M.secboot.fd
# 軌道 B
binwalk -e <target_firmware.bin>
file _target_firmware.bin.extracted/*
```

**M1 產出**：韌體結構表（FV/FFS 清單 或 partition 清單）

---

### M2 — 攻擊面測繪（預估 6-8 小時）

**對應章節**：Ch 2（信任鏈解剖）、Ch 7（UEFI 漏洞類型）、Ch 11（SMM 攻擊面）、Ch 17（U-Boot 攻擊面）、Ch 21（類型學）、Ch 29（繞過類型學）

**工作**：
- [ ] 繪製目標的信任鏈 ASCII 圖（每個箭頭：驗什麼、用什麼驗、在哪執行）
- [ ] 填寫類型學表（T1-T6，每項：不適用 / 未確認 / 確認，並說明判斷依據）
- [ ] 確定攻擊面清單（至少 5 個具體攻擊向量，帶前提條件和潛在影響）
- [ ] 選定 M3 深入點（從攻擊面清單選最值得投入的一個）

**M2 產出**：信任鏈圖 + 類型學掃描表 + 攻擊面清單（帶優先度）

---

### M3 — 深入一個點（預估 8-12 小時）

**對應章節**：Ch 24（Ghidra 逆 UEFI）、Ch 25（ARM bootloader RE）、Ch 26（diffing）、Ch 28-31（Secure Boot 繞過鏈）

**工作（軌道 A/B）**：
- [ ] 用 Ghidra 開目標 DXE/U-Boot binary，完成基礎設定（processor、compiler/ABI）
- [ ] 定位驗簽函式（搜字串 "verify"/"signature"/"RSA"/"auth"）
- [ ] 追蹤驗簽的完整呼叫鏈（至少 3 層）
- [ ] 分析失敗路徑（驗失敗 → 是否真的不執行？有無 fallback？）
- [ ] 提出具體假設：這個驗簽邏輯最可能屬於哪個漏洞類型？

**工作（軌道 C）**：
- [ ] 讀完至少 2 份一手分析報告（ESET + Microsoft / Eclypsium + CERT）
- [ ] 重建完整技術鏈（每個步驟：CVE/技術動作 + 前提條件 + 在信任鏈的哪一層）
- [ ] 歸類到 T1-T6，說明每個類型在此鏈中的角色
- [ ] 分析「如果有 X」的反事實防守（至少 3 個 X）

**M3 產出**：Ghidra 分析圖（A/B）或技術鏈重建圖（C）+ 假設利用鏈（帶前提條件）

---

### M4 — 防守面分析（預估 4-6 小時）

**對應章節**：Ch 38（Measured Boot）、Ch 42（完整性監控）、Ch 43（Remote Attestation）、Ch 44（廠商緩解）、Ch 45（偵測 bootkit）

**工作**：
- [ ] 對應 M3 的假設利用鏈，分析每個步驟可以被哪個防守機制擋下
- [ ] 填寫「防守有效性評估」表（每個防守機制：能防到哪步、為何防不住後面）
- [ ] 列出具體的偵測方法（工具、指標、log 欄位）
- [ ] 如果是軌道 A，真跑 swtpm + tpm2_pcrread，確認 PCR 值並對照 TCG spec

**真跑（軌道 A）**：
```bash
# 比較正常開機 vs 植入未簽章 EFI 後的 PCR[0-7]
# 正常開機
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 > /tmp/pcr_normal.txt
# 塞未簽章 EFI 後開機
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 > /tmp/pcr_tampered.txt
diff /tmp/pcr_normal.txt /tmp/pcr_tampered.txt
# 預期：PCR[4] 或 PCR[7] 會變（EV_EFI_BOOT_SERVICES_APPLICATION 或 Secure Boot policy）
```

**M4 產出**：防守有效性評估表 + 偵測方法清單

---

### M5 — 報告整合（預估 4-6 小時）

**工作**：
- [ ] 用「共同交付物」模板整合 M1-M4 的所有產出
- [ ] 確認每個 section 都有具體內容（沒有空的 「TBD」）
- [ ] 所有未實測項目都在「誠實聲明」表中列出
- [ ] 報告總字數至少 2000 字（不含程式碼）
- [ ] 給自己用 Rubric 打分，確認達標

**M5 產出**：完整研究報告（一份 `.md` 檔案）

---

## 評分量表（Rubric）

自評用。每項 0-4 分，滿分 24 分。合格 16+（約 67%），優秀 20+。

### 1. 技術深度（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 只有高層描述，無具體技術細節 |
| 1 | 有技術術語但缺乏分析（「這個函式很危險」） |
| 2 | 能說明漏洞機制，但分析停在第一層（找到了問題，沒有說為什麼是問題） |
| 3 | 完整分析一個深入點，從 binary 層到影響層，帶具體的呼叫鏈 / 技術步驟 |
| 4 | 分析連結到更廣的攻擊面（這個問題是孤立 bug 還是設計缺陷？對整條信任鏈的意義？） |

### 2. 真跑驗證（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 無任何真跑，全為引用或假設 |
| 1 | 有跑工具但只貼了 help 輸出，沒有對目標分析 |
| 2 | 有真跑（binwalk/Ghidra/QEMU/tpm2），有截圖或輸出，但和分析結論連結不清 |
| 3 | 真跑結果明確支撐分析結論，能說明「我看到 X，所以判斷 Y」 |
| 4 | 真跑包含正 / 負兩個對照（正常狀態 vs 被竄改狀態，或預期行為 vs 實際行為），差異被解釋清楚 |

### 3. 類型學運用（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 完全沒提 T1-T6 |
| 1 | 提到類型學但只是列名稱，沒有對目標做具體套用 |
| 2 | 對目標做了 T1-T6 掃描，但判斷理由薄弱（「T3 可能適用」沒有說為什麼） |
| 3 | 對每個類型有明確的適用 / 不適用理由，有工具輸出支撐 |
| 4 | 能說明類型的組合方式（利用鏈為什麼需要 T4 + T6？換掉其中一個鏈會斷在哪裡？） |

### 4. 攻防雙視角（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 只有攻擊面，完全沒有防守分析 |
| 1 | 有「建議更新韌體」這類通用建議，沒有具體針對你的攻擊假設 |
| 2 | 防守建議是針對你分析的攻擊，但沒有評估各防守機制能防到哪一步 |
| 3 | 有「防守有效性評估」表，能說明每個防守機制的防線在哪裡、被哪些前提繞過 |
| 4 | 防守分析帶反事實推理（「如果在 2022 年及時更新 dbx，BlackLotus 的哪幾步就無法執行？」） |

### 5. 報告品質（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 結構零散，讀者不知道你在分析什麼 |
| 1 | 有大標題但各 section 內容混亂 |
| 2 | 結構清楚，但技術 claim 沒有對應支撐（「XXX 是漏洞」但沒說為什麼） |
| 3 | 每個技術 claim 都有支撐（工具輸出 / 代碼引用 / spec 引用），報告可被第三方驗證 |
| 4 | 報告品質達到公開發表水準：執行摘要讓非技術讀者理解風險，技術細節讓同行能重現 |

### 6. 誠實標未實測（0-4 分）

| 分 | 描述 |
|----|------|
| 0 | 假裝跑過沒跑過的東西，或完全不說明驗證程度 |
| 1 | 有提到「未實測」但沒有說原因或替代驗證方法 |
| 2 | 列出未實測項目和原因，但沒有提補充驗證建議 |
| 3 | 未實測表完整，每項有原因 + 建議驗證方式（「如果有真實 x86 硬體，執行 chipsec_main.py -m common.bios_wp 驗證」） |
| 4 | 將已驗證 / 未驗證的 claim 在報告全文中始終分清，讀者不需要猜哪部分是真跑哪部分是假設 |

---

## 延伸與求職連結

### 把這份報告變成 Portfolio

研究報告的價值在於**可重現性和技術深度**，不在長度。面試時拿得出的報告：

1. **有具體 target**：「我分析了 OVMF 的 AuthVariable 實作」比「我研究了 UEFI 安全」強十倍
2. **有真跑證據**：terminal 輸出、Ghidra 截圖、PCR 值比對，比文字描述有說服力
3. **攻防都有**：只說「這個有漏洞」不夠，要說「我建議這樣偵測 / 緩解，原因是」
4. **誠實聲明限制**：「我在 QEMU 環境驗證了概念，真實硬體的 CHIPSEC 稽核是下一步」比假裝跑過更有信任感

**GitHub 呈現格式**：
```
firmware-security-research/
├── README.md           ← 一段話說這份報告在做什麼
├── report.md           ← 你的 Final Project 報告
├── artifacts/
│   ├── ovmf_dump/      ← binwalk/uefi_firmware 輸出（若不太大）
│   ├── ghidra_exports/ ← Ghidra 匯出的函式圖（CSV 或截圖）
│   └── pcr_values/     ← tpm2_pcrread 輸出
└── tools/
    └── analysis_notes.md ← 分析過程筆記（給面試官看你怎麼思考）
```

### CVE Hunting 起點

完成這份 Final Project 後，你有能力做的下一步：

**針對 edk2（軌道 A 延伸）**：
- 訂閱 `edk2-devel` mailing list，找近期的 CVE patch，做 patch-diff 分析（Ch 26 技能）
- 聚焦 `NetworkPkg`（PixieFail 2024，8 個 CVSS 8+ CVE 都在這裡）和 `CryptoPkg`（RSA/X.509 parsing 是歷史 bug 密集區）
- `git log --since="2024-01-01" --grep="CVE" -- NetworkPkg/` 找已修的 bug，往前找同類型的未修版本

**針對 U-Boot（軌道 B 延伸）**：
- `u-boot.git` 的 `net/` 目錄（TFTP/DHCP client）和 `cmd/` 目錄（FIT image 解析）是歷史 bug 密集區
- 找廠商的自訂 U-Boot patch（從 GPL release 取得），diff 對比 upstream，廠商 patch 往往引入新 bug

**針對 bypass 鏈研究（軌道 C 延伸）**：
- Binarly 的 [FirmwareBleed](https://binarly.io/) 是目前韌體漏洞 POC 最密集的地方
- 選一個 Binarly 的分析，自己重建技術鏈，寫成你的延伸報告

### 對口職缺

| 職缺方向 | 這份報告對應展示的能力 | 話術建議 |
|---------|-----------------|--------|
| **平台安全工程師**（Intel/AMD/Arm） | UEFI DXE 逆向、SMM 攻擊面分析、BootGuard/TrustZone 機制 | 「我做了 OVMF 的 DXE 驗簽分析，理解 PI 架構各階段的信任邊界」 |
| **韌體資安研究員**（Binarly/Eclypsium/百度安全） | bypass 鏈分析、類型學、Ghidra 逆向 UEFI | 「我重建了 BlackLotus 的完整技術鏈，從 T4 rollback 到 bootkit 常駐的每個步驟」 |
| **嵌入式安全工程師**（IoT/router 廠商） | binwalk 解包、U-Boot 逆向、T1-T6 類型學稽核 | 「我對 OpenWrt build 做了類型學掃描，識別了 T4 Rollback 未防護的問題點」 |
| **供應鏈安全 / PSIRT**（OEM/ODM） | CHIPSEC 稽核流程、CVE 影響分析、防守時間軸 | 「我分析了 PKfail 對供應鏈的影響，建立了 OEM 應對 T6 類漏洞的時間軸」 |
| **MTK 韌體工程師**（面試線） | ARM TF-A/U-Boot 信任鏈、SoC 開機流程、efuse/AVB | 「我理解 MTK 的 BROM → Preloader → LK → Kernel 信任鏈，並分析過 T1（SBC_EN）和 T5（Download Mode）攻擊面」 |

---

## 這門課到這裡結束了

你從 Ring 0 往下打到了 Ring -2（SMM）和 Ring -3（Intel ME/PSP）。你知道 SMRAM 的 SMRR 是什麼在保護、知道 BootGuard fuse 沒燒意味著什麼、知道 BlackLotus 為什麼能繞過每一台沒更新 dbx 的 Windows 機器、知道 OVMF 的 DXE 信任邊界在哪裡、知道 U-Boot 的 FIT image 驗簽可能有六種方式出錯。

再下去就是矽。那是硬體 hacking 的領域，本課 Part 6 留了接口。你已經準備好了。

→ [回到課程首頁](./README.md)
