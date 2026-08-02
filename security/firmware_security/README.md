# 韌體安全學習筆記：UEFI / Secure Boot 漏洞研究

> 給已經懂開機流程（見 [linux_boot](../../systems/linux_boot/README.md)）、ARM 架構（見 [arm](../../architecture/arm/README.md)）、逆向的資安工程師，想從 Ring 0 往下打到 **Ring -2/-3** 的世界。

這門課以「**信任鏈（chain of trust）/ secure boot**」為貫穿全課的脊椎，x86 UEFI 與 ARM/嵌入式雙線對照，走 primitive → bypass chain → 偵測反制 的路線。我們**刻意不重教開機流程本身**（那是 linux_boot 的事），重心全放在**攻擊面、漏洞類型、真實利用鏈、以及廠商如何反制**。

## 為什麼學這個？

- **這是作業系統之下的世界**：你的 kernel exploit 打到 Ring 0 就到頂了。韌體活在 Ring -2（SMM）、Ring -3（ME/PSP），比 kernel 更早執行、比 kernel 更難偵測。攻下這裡，你在 OS 重灌後依然常駐。
- **漏洞研究的藍海**：韌體程式碼審計的人遠少於 kernel/browser，edk2 是開源的，attack surface 大、CVE 多、真實 bootkit（LoJax/MoonBounce/BlackLotus）一再證明它可被武器化。
- **職涯對口**：接你的 MTK 韌體面試線、嵌入式安全、平台安全（Intel/AMD/ARM）、supply-chain security。攻防雙修在這門課一次補齊。

## 先修知識

- C 語言與 x86-64 組語（程度：能讀 pwn writeup）
- 開機流程基礎（程度：知道 UEFI/BIOS 差別、GRUB、bzImage —— [linux_boot](../../systems/linux_boot/README.md) 打過底更好）
- ARM 基礎（程度：知道 exception level、TrustZone 概念 —— [arm](../../architecture/arm/README.md)）
- 逆向工具（程度：會用 Ghidra/IDA 看 code —— [ida_pro](../security/ida_pro/README.md)/[reading_code](../../soft_skills/reading_code/README.md)）
- 沒有也沒關係的：真實硬體（本課主力在 QEMU/OVMF/swtpm，硬體章節會標「未實測」）

## 課程地圖

### Part 0 — 韌體攻擊面全景（Ch 0–2）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼攻韌體：Ring -2/-3 的世界](./01-why-attack-firmware.md)
- [Ch 2 信任鏈解剖：全課地圖](./02-chain-of-trust-anatomy.md)

### Part 1 — x86 UEFI 內部與攻擊面（Ch 3–9）
- [Ch 3 PI 規範各階段的攻擊面](./03-uefi-pi-attack-surface.md)
- [Ch 4 惡意 DXE driver](./04-malicious-dxe-driver.md)
- [Ch 5 UEFI variable 與 NVRAM 攻擊](./05-uefi-variable-nvram-attacks.md)
- [Ch 6 capsule update 與 firmware volume/FFS](./06-capsule-update-ffs.md)
- [Ch 7 UEFI 漏洞類型與真實 CVE](./07-uefi-vulnerability-classes.md)
- [Ch 8 edk2 漏洞挖掘](./08-edk2-vuln-hunting.md)
- [Ch 9 從 OS 打回韌體：runtime 信任邊界](./09-os-to-firmware-runtime.md)
- [練習 A：寫一個攔改開機流程的 DXE driver](./practice-a-dxe-boot-hook.md)

### Part 2 — SMM 與晶片組安全（Ch 10–14）
- [Ch 10 SMM / SMRAM / SMI：Ring -2 為何是聖杯](./10-smm-smram-smi.md)
- [Ch 11 SMM 攻擊面](./11-smm-attack-surface.md)
- [Ch 12 SMM callout 與指標竄改](./12-smm-callout-pointer.md)
- [Ch 13 SMM exploitation 實戰](./13-smm-exploitation.md)
- [Ch 14 Intel ME / AMT / BootGuard 與 AMD PSP](./14-intel-me-bootguard-psp.md)
- [練習 B：CHIPSEC 稽核平台保護組態](./practice-b-chipsec-audit.md)

### Part 3 — ARM / 嵌入式 secure boot 與 vendor 韌體（Ch 15–21）
- [Ch 15 ARM 開機信任鏈：BL1→BL2→BL31→BL33](./15-arm-boot-chain.md)
- [Ch 16 Trusted Firmware-A 剖析](./16-tf-a-internals.md)
- [Ch 17 U-Boot 深入與攻擊面](./17-uboot-attack-surface.md)
- [Ch 18 coreboot 與開源韌體](./18-coreboot-open-firmware.md)
- [Ch 19 Android Verified Boot](./19-android-verified-boot.md)
- [Ch 20 MTK / vendor SoC 韌體](./20-mtk-vendor-soc.md)
- [Ch 21 嵌入式 secure boot 繞過模式](./21-embedded-bypass-patterns.md)
- [練習 C：分析 U-Boot 映像構造繞過](./practice-c-uboot-analysis.md)

### Part 4 — 韌體逆向工程（Ch 22–27）
- [Ch 22 取得韌體：dump 與解包](./22-obtaining-firmware.md)
- [Ch 23 UEFI 韌體解析：UEFITool 與 FV/FFS](./23-uefi-firmware-parsing.md)
- [Ch 24 用 Ghidra 逆 UEFI 模組](./24-ghidra-uefi-re.md)
- [Ch 25 逆 ARM bootloader / BootROM](./25-arm-bootloader-re.md)
- [Ch 26 找後門與韌體 diffing](./26-backdoors-and-diffing.md)
- [Ch 27 韌體 emulation 做動態分析](./27-firmware-emulation.md)
- [練習 D：UEFITool 拆 + Ghidra 逆一個 DXE 模組](./practice-d-firmware-re-report.md)

### Part 5 — Secure Boot 繞過鏈（跨平台整合）（Ch 28–32）
- [Ch 28 Secure Boot 深入：db/dbx/KEK/PK](./28-secure-boot-internals.md)
- [Ch 29 繞過類型學](./29-bypass-taxonomy.md)
- [Ch 30 真實利用鏈剖析：BootHole/BlackLotus/LogoFAIL](./30-real-bypass-chains.md)
- [Ch 31 bootkit 構造](./31-bootkit-construction.md)
- [Ch 32 dbx / SBAT 撤銷與軍備競賽](./32-dbx-sbat-revocation.md)
- [練習 E：重現「資料檔繞簽章」最小 PoC](./practice-e-data-bypass-poc.md)

### Part 6 — 硬體 / 故障注入協同（Ch 33–36）
- [Ch 33 軟體攻擊面關閉後，物理是下一步](./33-when-software-fails.md)
- [Ch 34 故障注入繞 secure boot](./34-fault-injection-secure-boot.md)
- [Ch 35 SPI 竄改 TOCTOU 與 cold boot](./35-spi-tamper-cold-boot.md)
- [Ch 36 debug 介面當攻擊原語](./36-debug-interfaces.md)

### Part 7 — TPM 與密鑰保護深挖（Ch 37–41）
- [Ch 37 TPM 2.0 架構](./37-tpm2-architecture.md)
- [Ch 38 Measured Boot 全鏈](./38-measured-boot-chain.md)
- [Ch 39 Sealed key：BitLocker / LUKS 綁 PCR](./39-sealed-keys.md)
- [Ch 40 TPM 攻擊](./40-tpm-attacks.md)
- [Ch 41 TEE / SGX / TrustZone 對照](./41-tee-sgx-comparison.md)
- [練習 F：swtpm + QEMU 建 measured boot](./practice-f-swtpm-measured-boot.md)

### Part 8 — 防守與偵測（Ch 42–45）
- [Ch 42 韌體完整性監控](./42-firmware-integrity-monitoring.md)
- [Ch 43 遠端證明落地](./43-remote-attestation.md)
- [Ch 44 廠商緩解全景](./44-vendor-mitigations.md)
- [Ch 45 偵測 bootkit 與供應鏈](./45-detecting-bootkits.md)

### Part 9 — 整合專案
- [Final Project：端到端韌體攻防研究報告](./final-project-firmware-security-report.md)

## 學習方式建議

1. **每章開 QEMU 跑**：本課主力環境是 `qemu-system-x86_64 + OVMF`、`qemu-system-aarch64 + AAVMF`、`swtpm + tpm2-tools`。讀完就動手開機、下中斷、dump variable。
2. **故意把信任鏈弄壞**：改 OVMF variable、塞一個沒簽章的 EFI、竄改 PCR measurement，看它在哪一步擋下來、又在哪一步沒擋。防線的形狀，從「哪裡沒擋」看得最清楚。
3. **對照兩條線**：每學一個 x86 概念（SMM、Secure Boot），去 Part 3 找 ARM 對應（TrustZone、TF-A verified boot），trust anchor 的設計哲學是相通的。
4. **真硬體的部分誠實面對**：SPI dump、故障注入、TPM bus sniffing 需要 CH341A/ChipWhisperer/邏輯分析儀，本課標「未實測」的段落給的是原理與驗證方法，不是假裝跑過。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Beyond BIOS: Developing with the Unified Extensible Firmware Interface》(3rd ed.)** — Vincent Zimmer 等（Intel Press / De Gruyter）
  - UEFI/PI 架構的權威書，SEC/PEI/DXE/BDS 各階段講最清楚，本課 Part 1 的骨架
- **[UEFI / PI Specifications](https://uefi.org/specifications)**
  - 官方 spec；遇到 protocol/variable/capsule 行為不符預期時的最終仲裁，Part 1/5 反覆用
- **[TCG PC Client Platform Firmware Profile](https://trustedcomputinggroup.org/resource/pc-client-specific-platform-firmware-profile-specification/)**
  - measured boot 到底測了什麼、PCR[0-7] 如何分配的權威來源，Part 7 必讀

### 推薦論文 / 白皮書

- **[Attacking and Defending BIOS in 2015](https://www.c7zero.info/stuff/AttackingAndDefendingBIOS-RECon2015.pdf)** — Bulygin, Loucaides 等（RECon 2015）
  - SMM / BIOS 保護（SMRR/D_LCK/BIOS_CNTL）攻防的經典整理，Part 2 的地圖
- **[BlackLotus 分析報告](https://www.welivesecurity.com/2023/03/01/blacklotus-uefi-bootkit-myth-confirmed/)** — ESET
  - 第一個公開繞過 Secure Boot 的 UEFI bootkit，Part 5 逐步拆它的利用鏈

### 推薦部落格 / 工具

- **[CHIPSEC](https://github.com/chipsec/chipsec)** — Intel（原 Intel Advanced Threat Research）
  - 平台安全稽核與 PoC 框架，Part 2/8 大量使用；`chipsec_main` 的模組本身就是一本 attack 教材
- **[Binarly Research](https://www.binarly.io/blog)** — Binarly
  - 目前韌體漏洞研究產出最猛的團隊，LogoFAIL、PKfail 都是他們挖的，Part 5/8 追這個 blog

### 讀完本課之後

- **《Rootkits and Bootkits》** — Matrosov, Rodionov, Bratus（No Starch, 2019）—— 把 bootkit 的歷史演進與偵測講得最完整，接本課 Part 5
- **[edk2 原始碼](https://github.com/tianocore/edk2)** —— 讀 `MdeModulePkg`/`SecurityPkg`，本課所有 x86 概念都能在這裡找到實作對應

## 這門課在課群裡的位置

```
       kernel_pwn / windows_kernel_driver   ← Ring 0
                    │
                    ▼
    ┌────────── firmware_security ──────────┐   ← 你在這裡（Ring -2/-3）
    │  x86 UEFI/SMM/ME  ×  ARM TF-A/vendor  │
    └───────────────────────────────────────┘
        ▲              ▲              ▲
   linux_boot        arm         mtk_firmware
   (開機流程)     (TrustZone)    (SoC 韌體面試)
```

往下沒有更低的軟體層了。再下去就是矽（hardware hacking / 故障注入），本課 Part 6 留了接口。
