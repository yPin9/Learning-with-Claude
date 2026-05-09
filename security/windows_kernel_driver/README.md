# Windows Kernel Driver 學習筆記：從驅動開發到核心安全研究

> 給有 Linux 系統底子、想踏入 Windows 核心驅動開發與安全研究的工程師。

本課程從 NT 核心架構出發，帶你寫出第一個 KMDF 驅動，深入 IRP / 記憶體 / 同步模型，再進入漏洞利用、EDR 攻防、BYOVD 等進階主題。最後用一個 minifilter-based 檔案監控驅動整合所有技能。

## 為什麼學 Windows Kernel Driver？

- **攻擊面最廣**：Windows 核心漏洞（LPE/EoP）是高價值漏洞，IOCTL 介面是最常見的入口
- **安全工具的核心**：EDR、Anti-Cheat、DLP 全部住在 kernel，看不懂驅動就看不懂防護
- **真實職涯需求**：Windows driver 工程師稀缺；紅隊/惡意軟體分析師必備技能
- **有 Linux 底子再學**：很多概念（中斷、記憶體分頁、同步）有 1:1 對應，學習曲線比零基礎短很多

## 先備知識

- C/C++ 基礎（指標、結構體、型別轉換）
- x86_64 組合語言（看得懂 disassembly 即可）
- 基本 Windows 用戶態概念（Process、DLL、Handle）
- 有 Linux kernel 或 kernel pwn 經驗更好，但不強制

## 環境需求

- Windows 10/11 主機（裝 WDK + Visual Studio）
- Windows 10/11 VM（測試機，啟用 Test Signing + KD）
- VMware Workstation 或 Hyper-V（推薦 VMware，serial/network KD 更穩）
- WinDbg Preview（Microsoft Store 免費下載）

## 課程地圖

### Part 1 — NT 架構基礎
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼學 Windows Kernel Driver](./01-why-windows-kernel-driver.md)
- [Ch 2 NT 核心架構全貌](./02-nt-architecture.md)
- [Ch 3 核心模式 vs 用戶模式](./03-kernel-user-mode.md)
- [Ch 4 NT 物件模型](./04-object-model.md)
- [Ch 5 EPROCESS / ETHREAD](./05-eprocess-ethread.md)

### Part 2 — 第一個驅動
- [Ch 6 WDM 骨架](./06-wdm-skeleton.md)
- [Ch 7 KMDF 入門](./07-kmdf-intro.md)
- [Ch 8 IRP 基礎](./08-irp-basics.md)
- [Ch 9 Dispatch Routines](./09-dispatch-routines.md)
- [Ch 10 IOCTL](./10-ioctl.md)
- [練習 A：KMDF 驅動 + IOCTL 控制介面](./practice-a-kmdf-ioctl.md)

### Part 3 — 記憶體與同步
- [Ch 11 核心記憶體模型](./11-kernel-memory.md)
- [Ch 12 MDL](./12-mdl.md)
- [Ch 13 同步基元](./13-synchronization.md)
- [Ch 14 DPC 與 APC](./14-dpc-apc.md)
- [Ch 15 Lookaside List](./15-lookaside-list.md)

### Part 4 — 進階 I/O 模型
- [Ch 16 IRP 完成常式與取消](./16-irp-cancel.md)
- [Ch 17 直接 I/O](./17-direct-io.md)
- [Ch 18 非同步 I/O](./18-async-io.md)
- [Ch 19 Filter Driver](./19-filter-driver.md)
- [Ch 20 Minifilter Driver](./20-minifilter.md)

### Part 5 — 調試與安全機制
- [Ch 21 WinDbg 核心調試](./21-windbg.md)
- [Ch 22 BSOD 崩潰分析](./22-bsod-analysis.md)
- [Ch 23 Driver Verifier](./23-driver-verifier.md)
- [Ch 24 DSE + PatchGuard](./24-dse-patchguard.md)
- [Ch 25 Token、Privilege 與 ACL](./25-token-acl.md)
- [練習 B：WinDbg 崩潰分析](./practice-b-bsod-analysis.md)

### Part 6 — 漏洞與利用
- [Ch 26 Windows 核心漏洞概覽](./26-kernel-vuln-overview.md)
- [Ch 27 IOCTL 漏洞](./27-ioctl-vulnerabilities.md)
- [Ch 28 任意寫利用](./28-arbitrary-write-exploit.md)
- [Ch 29 Pool 利用](./29-pool-exploitation.md)
- [Ch 30 現代緩解機制](./30-modern-mitigations.md)
- [練習 C：IOCTL 漏洞利用模擬](./practice-c-ioctl-exploit.md)

### Part 7 — 進階驅動類型與攻擊技術
- [Ch 31 核心回調機制](./31-kernel-callbacks.md)
- [Ch 32 DKOM 與進程隱藏](./32-dkom-process-hiding.md)
- [Ch 33 WFP 網路過濾驅動](./33-wfp-network-filter.md)
- [Ch 34 NDIS 輕量過濾](./34-ndis-lwf.md)
- [Ch 35 BYOVD 攻擊](./35-byovd.md)

### Part 8 — EDR 與防護技術
- [Ch 36 ETW 核心層](./36-etw-kernel.md)
- [Ch 37 PatchGuard 深入](./37-patchguard-deep.md)
- [Ch 38 EDR 驅動架構](./38-edr-driver-arch.md)
- [Ch 39 反 EDR 技術](./39-anti-edr.md)
- [Ch 40 VBS / HVCI / Secure Boot](./40-vbs-hvci-secureboot.md)

### Final Project
- [Final Project：minifilter-based 檔案監控驅動](./final-project-minifilter-edr.md)

## 學習方式建議

1. **雙機調試從第 0 章就建好**：沒有 kernel debugger 你什麼都看不見。環境出問題先停下來解決，不要硬撐
2. **每章都實際跑過 WinDbg**：`dt nt!_EPROCESS`、`!irp`、`!devobj` 這些命令比讀一百遍文字有效
3. **故意讓驅動崩潰**：`KeBugCheck()` 是你的朋友。理解 BSOD 比什麼都重要，否則 debug 是盲的
4. **安全章節看 CVE PoC 源碼**：理論加實際漏洞代碼才能真正理解攻擊面

## 參考資料

- 《Windows Internals, Part 1 & 2》— Russinovich, Solomon, Ionescu（必讀聖經）
- 《Windows Kernel Programming》— Pavel Yosifovich（最好的 WDM/KMDF 入門書）
- WDK 官方文件 — docs.microsoft.com/windows-hardware/drivers
- OSR Online（ntdev.com）— 最活躍的 Windows driver 開發者社群
- HackSys Extreme Vulnerable Driver（HEVD）— 練習漏洞利用的最佳沙盒
