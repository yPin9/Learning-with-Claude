# Ch 29 — VirtualBox 架構與源碼導讀

> **目標**：建立 VirtualBox 子系統地圖，定位 device emulation 代碼在哪、VMEXIT 怎麼路由到 ring-3、攻擊面在哪個層次。

> **環境**：VirtualBox 7.0 / x86-64 / Linux 或 Windows host

---

## 為什麼需要這個？

Ch 1-28 建立了 QEMU/KVM 的完整攻擊直覺：VMEXIT → memory_dispatch → device `.write` → bug。VirtualBox 的邏輯完全一樣，但實作層次、目錄結構、編譯系統都完全不同。

如果你跳過架構直接看 CVE，就會卡在「這個 symbol 在哪個 .so」、「pfnConstruct 是什麼鬼」。本章解決這個問題。讀完之後，Ch 30 直接看 device bug、Ch 31 直接打 exploit，不再繞路。

---

## 先建立直覺

VirtualBox 是 **type-2 hypervisor（宿主型虛擬機器）**：它跑在一個完整的 host OS 之上，依賴 host OS 的記憶體管理和排程器。相對地，KVM 是 type-1.5——它把自己嵌進 Linux kernel，VMX 操作直接在 kernel space 完成。

### 和 QEMU/KVM 的最大差異

QEMU/KVM 的分工：
- **KVM（kernel module）**：負責 VT-x/AMD-V，跑 vCPU loop，攔截 VMEXIT
- **QEMU（userspace）**：所有 device emulation，memory-mapped I/O dispatch

VirtualBox 的分工：
- **VBoxDrv（kernel driver）**：負責 VT-x，跑 vCPU loop，攔截 VMEXIT
- **VBoxDD（userspace shared library）**：所有 device emulation
- 中間還多了 VMM、PDM、IOM 等子系統，全部跑在同一個 VBoxHeadless/VirtualBox process

概念上幾乎一對一，只是 VirtualBox 把 QEMU 的角色拆得更細、命名更嚴謹。

### 一個 device 的生命週期

```
guest 寫 MMIO 位址
    → CPU 觸發 VMEXIT（reason: EPT misconfig / MMIO access）
    → VBoxDrv（ring-0 driver）攔截 VMEXIT
    → 無法在 ring-0 處理 → ioctl 回 ring-3
    → IOM（I/O Manager）查表找到對應 device 的 MMIO handler
    → 呼叫 VBoxDD 裡的 device pfnMMIOWrite callback
    → device 處理請求，可能更新 guest memory
```

和 QEMU 的對應：
```
guest 寫 MMIO 位址
    → KVM 攔截 VMEXIT
    → ioctl 回 QEMU userspace
    → memory_dispatch → MemoryRegionOps.write → device handler
```

骨架相同，只是名字不一樣。

---

## 底層機制：子系統全貌

### 層次圖

```
┌─────────────────────────────────────────────────────┐
│                  Guest VM                           │
│           (x86-64 code, ring-0/3)                   │
└──────────────┬──────────────────────────────────────┘
               │ VMEXIT (MMIO/PIO/hypercall)
               ▼
┌─────────────────────────────────────────────────────┐
│        VBoxDrv  (ring-0 kernel driver)              │
│   Linux: vboxdrv.ko  /  Windows: VBoxDrv.sys        │
│   - VT-x / AMD-V 操作（VMLAUNCH/VMRESUME）          │
│   - VMEXIT 快速分類：可在 ring-0 處理 → 直接回 VM  │
│   - 無法處理 → ioctl 傳回 ring-3                   │
└──────────────┬──────────────────────────────────────┘
               │ ioctl / supdrv interface
               ▼
┌─────────────────────────────────────────────────────┐
│          VMM（Virtual Machine Monitor）             │
│   - vCPU 狀態機（CPUM、SELM、PGM for paging）      │
│   - EM（Execution Monitor）：選 HW-virt / interpret │
│   - 跑在 VBoxHeadless / VirtualBox process 內       │
└──────────────┬──────────────────────────────────────┘
               │ PDM device dispatch
               ▼
┌─────────────────────────────────────────────────────┐
│        IOM（I/O Manager）                           │
│   - 維護 MMIO region table、PIO port table          │
│   - 根據 GPA（Guest Physical Address）找到 handler  │
└──────────────┬──────────────────────────────────────┘
               │ callback
               ▼
┌─────────────────────────────────────────────────────┐
│       VBoxDD（Device Driver Library）               │
│   - 所有 device emulation：NIC、AHCI、HDA、SVGA…   │
│   - 純 ring-3 userspace shared library              │
│   - 用 PDM helper 向 IOM 登記 MMIO/PIO             │
└─────────────────────────────────────────────────────┘

橫切支援層（全 ring-3）：
┌────────────────────┐  ┌──────────────────────────────┐
│   IPRT              │  │  PDM（Pluggable Device Mgr） │
│  跨平台抽象         │  │  device lifecycle、bus 管理  │
│  RTMemAlloc / 鎖   │  │  等於 QEMU 的 QOM + bus      │
└────────────────────┘  └──────────────────────────────┘
```

### VBoxDrv — ring-0 driver 做什麼

VBoxDrv 是 VirtualBox 在 host kernel 中的唯一落腳點。它：

1. 提供 `/dev/vboxdrv`（Linux）或 `\\.\VBoxDrv`（Windows）ioctl 介面給 VMM process
2. 用 `VMXON`/`VMLAUNCH`/`VMRESUME` 指令控制硬體虛擬化
3. 部分 VMEXIT（例如 MSR read/write、CR 存取）直接在 ring-0 處理，不用過一次 ioctl
4. MMIO/PIO 的 VMEXIT 則傳回 ring-3，讓 VBoxDD 的 device callback 處理

從攻擊者視角，VBoxDrv 很少是直接目標（需要 kernel exploit）。有趣的 bug 通常在 VBoxDD，因為那裡跑 device emulation、有大量 C++ 物件。

### VMM — vCPU loop

`src/VBox/VMM/` 是 VirtualBox 的「核心」，等於 QEMU 中 `accel/kvm/` + `cpus.c` 的組合。重要子模組：

| 模組 | 職責 |
|------|------|
| CPUM | vCPU 通用暫存器/MSR 狀態管理 |
| PGM  | guest 分頁表追蹤（Extended Page Tables 管理） |
| IOM  | I/O Manager，MMIO/PIO 的 dispatcher |
| EM   | Execution Monitor，決定用 HW virt / interpreter |
| PDM  | Pluggable Device Manager，device 生命週期 |
| TM   | Timer Manager，虛擬時鐘 |

### VBoxDD — device 都在這裡

所有 device emulation 在 `src/VBox/Devices/`，這是 **攻擊面最集中的地方**。

重要源碼路徑：

```
src/VBox/Devices/
├── Network/
│   └── DevE1000.cpp        ← Intel e1000 NIC emulation（歷史上多個 bug）
├── Storage/
│   └── DevAHCI.cpp         ← AHCI/SATA 控制器
├── Audio/
│   └── DevHDA.cpp          ← Intel HDA 音效
├── Graphics/
│   └── DevVGA-SVGA.cpp     ← VMware SVGA II 相容層（VirtualBox 也實作了）
├── USB/
│   ├── DevOHCI.cpp         ← USB OHCI 控制器
│   └── DevXHCI.cpp         ← USB xHCI 控制器
├── Bus/
│   └── DevPCI.cpp          ← PCI bus
└── Input/
    └── DevPS2K.cpp         ← PS/2 鍵盤
```

### IPRT — 跨平台抽象層

IPRT（Independent Platform Runtime）等於 QEMU 的 `util/` + glib 的組合。它封裝了：

- 記憶體分配：`RTMemAlloc` / `RTMemFree`（heap 上）
- 檔案 I/O、socket、執行緒、鎖
- 字串處理、時間

**攻擊者注意**：VirtualBox heap 用的是 IPRT 的 `RTMemAlloc`，底層呼叫 host 的 `malloc`（Linux: glibc，Windows: CRT heap）。這意味著 heap exploit 的 primitive 和一般 userspace exploit 一致，不需要額外了解自訂 allocator。

### PDM — device 的 lifecycle 管理

PDM（Pluggable Device Manager）負責：
- 從 VirtualBox 設定（.vbox XML）決定要載入哪些 device
- 呼叫每個 device 的 `pfnConstruct` 初始化
- 關機時呼叫 `pfnDestruct`
- 提供 helper API 讓 device 向 IOM 登記 MMIO/PIO

等於 QEMU 的 QOM（`object_class_register`）+ device bus（`qbus_realize`）的組合。

---

## C++ Device 骨架

VirtualBox 7.0 的 device 用 C++ 寫，透過 `PDMDEVREG` 結構登記。

### PDMDEVREG 最小骨架

```c
/* src/VBox/Devices/MyDev/DevMyDev.cpp 示意 */

static PDMDEVREG g_DeviceMyDev =
{
    .u32Version             = PDM_DEVREG_VERSION,
    .szName                 = "mydev",
    .szRCMod                = "",        /* Raw-mode context module（通常空） */
    .szR0Mod                = "",        /* Ring-0 module（通常空，device 在 ring-3） */
    .pszDescription         = "Example device",
    .fFlags                 = PDM_DEVREG_FLAGS_DEFAULT_BITS | PDM_DEVREG_FLAGS_NEW_STYLE,
    .fClass                 = PDM_DEVREG_CLASS_MISC,
    .cMaxInstances          = 1,
    .cbInstanceCC           = sizeof(MYDEVSTATE),   /* per-instance 狀態大小 */
    .pfnConstruct           = myDevConstruct,       /* 初始化時呼叫 */
    .pfnDestruct            = myDevDestruct,
    .pfnReset               = myDevReset,
    .pfnSuspend             = NULL,
    .pfnResume              = NULL,
    .pfnAttach              = NULL,
    .pfnDetach              = NULL,
    .pfnMMIO2Ptr            = NULL,
    .pfnRelocate            = NULL,
    .pfnMemSetup            = NULL,
    .pfnPowerOn             = NULL,
    .pfnPowerOff            = NULL,
    .u32VersionEnd          = PDM_DEVREG_VERSION,
};
```

### pfnConstruct — 初始化和 MMIO 登記

```c
static DECLCALLBACK(int)
myDevConstruct(PPDMDEVINS pDevIns, int iInstance, PCFGMNODE pCfg)
{
    PDMDEV_CHECK_VERSIONS_RETURN(pDevIns);
    PMYDEVSTATE pThis = PDMDEVINS_2_DATA(pDevIns, PMYDEVSTATE);

    /* 向 IOM 登記一個 4KB MMIO region */
    IOMMMIOHANDLE hMmio;
    int rc = PDMDevHlpMmioCreateAndMap(
        pDevIns,
        0xFEBC0000,         /* GPA 起始位址 */
        0x1000,             /* 大小 4KB */
        myDevMmioWrite,     /* write callback */
        myDevMmioRead,      /* read callback */
        IOMMMIO_FLAGS_READ_DWORD | IOMMMIO_FLAGS_WRITE_DWORD_ZEROED,
        "MyDev MMIO",
        &hMmio
    );
    AssertRCReturn(rc, rc);

    return VINF_SUCCESS;
}

/* MMIO write handler */
static DECLCALLBACK(VBOXSTRICTRC)
myDevMmioWrite(PPDMDEVINS pDevIns, void *pvUser, RTGCPHYS off,
               void const *pv, unsigned cb)
{
    PMYDEVSTATE pThis = PDMDEVINS_2_DATA(pDevIns, PMYDEVSTATE);
    uint32_t u32Val = *(uint32_t const *)pv;

    /* 處理 guest 的 MMIO write，off 是 region 內偏移 */
    switch (off)
    {
        case 0x00: pThis->uReg0 = u32Val; break;
        case 0x04: myDevTriggerAction(pThis, u32Val); break;
        default:   break;
    }
    return VINF_SUCCESS;
}
```

### 對比 QEMU 的 MemoryRegionOps

| 概念 | VirtualBox 7.0 | QEMU 8.x |
|------|---------------|----------|
| device 登記結構 | `PDMDEVREG` | `TypeInfo` + `DeviceClass` |
| MMIO 登記函數 | `PDMDevHlpMmioCreateAndMap` | `memory_region_init_io` + `memory_region_add_subregion` |
| read/write handler 簽名 | `(PPDMDEVINS, void*, RTGCPHYS, void*, unsigned)` | `(void*, hwaddr, uint64_t*, unsigned)` |
| per-device 狀態 | `PDMDEVINS_2_DATA(pDevIns, PSTATE)` | `OBJECT_CHECK(State, obj, TYPE_NAME)` |
| 初始化 callback | `pfnConstruct` | `realize` |
| heap allocator | `RTMemAlloc` → glibc `malloc` | glibc `malloc` |
| C++ vtable | 有（device class 繼承） | 有（QOM TypeInfo）|

---

## 編譯 debug build

**【未實測，理論預期】** 以下步驟基於官方文件和源碼分析，讀者需自行驗證。

### 取得源碼

VirtualBox 開源版本（OSE）在 [https://www.virtualbox.org/svn/vbox/trunk](https://www.virtualbox.org/svn/vbox/trunk)，也可用 GitHub mirror。版本確認：`VBox/version-generated.h` 的 `VBOX_VERSION_STRING`。

### Linux host 編譯

```bash
# 安裝依賴（Ubuntu/Debian）
sudo apt install build-essential libssl-dev libxcursor-dev libxinerama-dev \
  libidl-dev libsdl2-dev gsoap libpulse-dev python3-dev

# 進源碼根目錄
./configure --disable-hardening   # 關掉 hardening，方便 gdb attach
source env.sh                     # 設定 PATH / VBOX_*環境變數

# 編譯 debug build
kmk KBUILD_TYPE=debug
```

關掉 hardening（`--disable-hardening` 或 `LocalConfig.kmk` 寫 `VBOX_WITH_HARDENING =`）是必要的，否則 VBoxHeadless 會有 setuid/code-signing 保護，gdb attach 會被擋掉。

### Windows host 編譯

```bat
cscript configure.vbs --with-vc="C:\...\VC" --disable-hardening
env.bat
kmk KBUILD_TYPE=debug
```

### LocalConfig.kmk 常用設定

```makefile
# 放在源碼根目錄
VBOX_WITH_HARDENING      =       # 空值 = 關掉
VBOX_WITH_TESTCASES      =       # 不編測試
KBUILD_VERBOSE           = 2     # 看完整編譯命令
```

---

## 怎麼 attach 調試

### Linux：gdb attach VBoxHeadless

```bash
# 先啟動 VM（headless 模式）
VBoxHeadless --startvm "MyVM" &

# 找 pid
pgrep -la VBoxHeadless

# gdb attach
gdb -p <pid>
# 此時 VM 暫停，輸入 continue 繼續
```

debug build 的 VBoxDD 帶 DWARF 符號。`src/VBox/Devices/Network/DevE1000.cpp` 的函數可以直接設斷點：

```
(gdb) b DevE1000.cpp:e1000R3NetworkUp_ReceiveFlushIf
```

### Windows：WinDbg attach

**【未實測，理論預期】** 在 Windows host，debug build 的 VirtualBox.exe / VBoxHeadless.exe 帶 PDB。用 WinDbg 的 `File → Attach to Process` 選 VBoxHeadless.exe 即可。VBoxDD.dll 的 symbols 會自動載入（如果 PDB 路徑正確）。

### VirtualBox 內建調試器

```bash
# 啟動時開 debugger
VBoxHeadless --startvm "MyVM" --debugger

# 或用 VBoxManage
VBoxManage debugvm "MyVM" info "cpumguesthwvirt"
VBoxManage debugvm "MyVM" getregisters --cpu 0 rip rsp
```

內建 debugger 可以看 guest 暫存器、記憶體，但對 host-side device debug 用途有限，還是 gdb 最直接。

---

## 對比與取捨

| 面向 | VirtualBox 7.0 | QEMU/KVM |
|------|---------------|----------|
| hypervisor 類型 | type-2，依賴 host OS | KVM 是 type-1.5，嵌入 Linux kernel |
| device emulation 位置 | ring-3 VBoxDD（shared library） | ring-3 QEMU process |
| kernel driver 職責 | VBoxDrv：只做 VT-x，不做 device | KVM：VT-x + 部分 MMIO fast path |
| C++ 使用程度 | 大量（PDMDEVREG、IPRT、繼承） | C 為主，QOM 用 C 模擬 OOP |
| heap allocator | IPRT RTMemAlloc → host glibc/CRT | glibc malloc |
| 編譯系統 | kmk（kBuild，自訂） | Meson + Ninja |
| 源碼可讀性 | 命名嚴謹但 C++ 層次深 | C 較直觀，但 QOM 繁瑣 |
| 歷史 CVE 數量 | 較少（社群較小，但仍有穩定產出） | 更多（生態大、fuzzer 更多） |
| 攻擊入口點 | VBoxDD device callback（ring-3 C++） | QEMU device callback（ring-3 C） |

---

## 踩雷集錦

1. **開了 hardening 就 gdb 不了**：VirtualBox 開 hardening 後，VBoxHeadless 有 setuid bit 或 integrity check，gdb attach 直接失敗。Debug 環境必須 `--disable-hardening` 重編並且重裝（或不裝，直接跑 build 目錄的 binary）。

2. **`PDMDevHlpMmioCreateAndMap` 和舊版 API 不相容**：VirtualBox 6.x 用 `IOMMMIORegisterR3`，7.0 換成 `PDMDevHlpMmioCreateAndMap`。看舊 CVE 的 PoC 時要注意函數名稱差異。

3. **kmk 不是 make**：VirtualBox 用的 kBuild 系統叫 `kmk`，不是 GNU make。編譯前要先 `source env.sh`（Linux）或 `env.bat`（Windows），否則找不到 `kmk`。

4. **源碼目錄有 `VBoxDD.cpp` 但 device 各自有自己的 `.cpp`**：`VBoxDD.cpp` 只是把所有 `PDMDEVREG` 收集起來 export，實際邏輯在各 `DevXxx.cpp`。搜索漏洞看 `DevE1000.cpp`、`DevAHCI.cpp` 等，不要只看 `VBoxDD.cpp`。

5. **Windows host 上 VBoxDrv.sys 有 DSE 保護**：在 Windows host 上如果想替換或 patch VBoxDrv.sys，需要關 Secure Boot 或用 test signing mode。這是研究環境設置問題，不是 exploit 本身。

---

## 進階：再往深一層

### IOM 的 MMIO 路由細節

當 guest 寫某個 GPA，EPT 的 `EPT_MISCONFIG` 或 `EPT_VIOLATION` 觸發 VMEXIT，VBoxDrv 的 VMEXIT handler（`HM.cpp`）呼叫 `IOMMMIOPhysHandler`。IOM 維護一個有序的 MMIO handle 陣列，binary search GPA 找到對應的 `IOMMMIOENTRY`，取出 `pfnWriteCallback`/`pfnReadCallback` 呼叫。

這個路由是 **VirtualBox 的 MMIO dispatch 核心**。如果能控制 `IOMMMIOENTRY` 的 callback pointer（例如 heap overflow 覆蓋），就能 redirect execution。

### C++ vtable 作為劫持目標

VBoxDD 裡大量使用 C++ class 繼承，每個 class 有 vtable。如果 heap overflow 能覆蓋到某個 device 物件的 vtable pointer，在下次 callback 呼叫時就能 redirect RIP。這是 VirtualBox exploit 的常見技巧，Ch 31 會用到。

### IPRT heap 和 glibc heap 的關係

`RTMemAlloc` 在 Linux 底層呼叫 `malloc`，所以 VirtualBox 的 heap 就是 glibc tcache heap。這意味著你在 QEMU/KVM exploit 上學到的 tcache poisoning、unsorted bin attack 等技術，在 VirtualBox 上同樣適用，不需要學習新的 allocator。

---

## 動手練習

1. **克隆源碼，定位 DevE1000.cpp**：找 `src/VBox/Devices/Network/DevE1000.cpp`，搜索 `PDMDevHlpMmioCreateAndMap` 或 `IOMMMIORegisterR3`，確認 e1000 的 MMIO region 大小和起始 GPA 設定。

2. **追 VMEXIT 路徑**：從 `src/VBox/VMM/VMMR3/HM.cpp`（或 `HMVMXR0.cpp`）找 EPT violation handler，一路 trace 到 `IOMMMIOPhysHandler`，畫出函數呼叫鏈。

3. **讀 PDMDEVREG 定義**：在 `src/VBox/VMM/include/PDMInternal.h` 或 `include/VBox/vmm/pdmdev.h` 找 `PDMDEVREG` 結構，對照本章的骨架，確認每個欄位的型別和用途。

4. **【選做，需 debug build】** 編出 debug build，gdb attach VBoxHeadless，在 `DevE1000.cpp` 的 MMIO write handler 設斷點，從 guest 內用 `devmem2` 或 mmap 觸發，觀察 call stack。

---

## 本章重點整理

- VirtualBox 是 type-2 hypervisor；VBoxDrv 做 VT-x，VBoxDD 做 device emulation（ring-3）
- 子系統：VBoxDrv（kernel）→ VMM → IOM → PDM → VBoxDD
- 攻擊面集中在 `src/VBox/Devices/` 的 device callback（C++ ring-3 code）
- MMIO 路由：guest VMEXIT → VBoxDrv → ioctl → ring-3 IOM → device `pfnMMIOWrite`
- Device 用 `PDMDEVREG` 登記，`pfnConstruct` 裡呼叫 `PDMDevHlpMmioCreateAndMap`
- Heap 是 IPRT RTMemAlloc → glibc malloc，exploit primitive 和一般 userspace 相同
- Debug 必須 `--disable-hardening`；Linux 用 gdb attach VBoxHeadless
- 對比 QEMU：命名不同、編譯系統不同，但架構邏輯一對一

---

## 自我檢核

- [ ] 我能說出 VBoxDrv、VMM、VBoxDD、PDM、IOM、IPRT 各自的職責
- [ ] 我知道 device MMIO callback 跑在哪個 ring（ring-3）、哪個 library（VBoxDD）
- [ ] 我能找到 DevE1000.cpp 在源碼樹的哪個路徑
- [ ] 我知道 `PDMDEVREG.pfnConstruct` 和 `PDMDevHlpMmioCreateAndMap` 的關係
- [ ] 我能對比 `PDMDevHlpMmioCreateAndMap` 和 QEMU 的 `memory_region_init_io`
- [ ] 我知道為什麼 debug build 要 `--disable-hardening`
- [ ] 我理解 VirtualBox heap 為什麼等於 glibc heap

---

## 延伸閱讀

1. **VirtualBox 官方開發者文件**
   - [https://www.virtualbox.org/wiki/DevNote](https://www.virtualbox.org/wiki/DevNote)
   - 讀「Architecture」和「Device emulation」兩節。這是官方對 VMM/PDM/IOM 分工最簡潔的說明，適合和本章對照。

2. **VirtualBox 源碼：`src/VBox/VMM/VMMR3/PDMDevice.cpp`**
   - 直接讀 PDM 的 device 初始化流程：`pdmR3DevInit` → `pfnConstruct` 呼叫鏈。比任何二手資料都準確。

3. **「VirtualBox VMSVGA out-of-bounds read/write」（CVE-2022-21571 系列分析）**
   - 搜索「VirtualBox SVGA exploit writeup」，有多篇公開分析在 `DevVGA-SVGA.cpp`。這是理解 VBoxDD device bug 模式的最佳入門案例，讀完後直接對接 Ch 30。

4. **phrack #69 "Attacking the Core: Kernel Exploiting Notes"**
   - 和 VirtualBox 無直接關係，但 ring-3 → ring-0 的 exploit primitive 思路（type confusion、vtable hijack）是 Ch 31 的前置直覺。讀 section 3-5 即可。

5. **kBuild 文件：[https://trac.netlabs.org/kbuild/wiki](https://trac.netlabs.org/kbuild/wiki)**
   - 搞懂 `kmk`、`LocalConfig.kmk`、`KBUILD_TYPE` 這些編譯系統概念，否則 debug build 失敗不知道從哪裡下手。

---

前三章（Ch 29-31）是一個完整的 VirtualBox 打法序列：本章建立地圖，Ch 30 看具體 device bug，Ch 31 把 bug 變成 exploit。

→ [Ch 30](./30-virtualbox-device-bugs.md)
