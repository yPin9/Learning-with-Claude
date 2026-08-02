# Ch 16 — Trusted Firmware-A 剖析

> **目標**：從 source tree 到 runtime call path，徹底理解 TF-A 各 BL 的程式邏輯、FIP 格式、SMCCC 介面、TBBR 驗簽實作，以及已公開的攻擊面——能對照 CVE 報告在 source 裡定位問題根源。
> **環境**：本章以 source 閱讀與概念建立為主；build/run 段落標注實測狀態，QEMU virt 組合需 aarch64-linux-gnu-gcc 工具鏈，本地若未安裝可參考步驟但不強制跑通。

---

## 16.1 為什麼 TF-A 是必讀文件

ARM 嵌入式 secure boot 的標準化程度遠高於 x86。UEFI 的 x86 世界由 AMI/Phoenix 各自實作，合規性靠 UEFI Specification 定義，但沒有強制的參考實作。ARM 走的路完全不同：Arm Holdings 在 2013 年推出 **Trusted Board Boot Requirements（TBBR）** 規範，同步釋出 **Trusted Firmware-A（TF-A）** 作為官方參考實作，原始碼在 git.trustedfirmware.org 完全開放。

結果是：市面上絕大多數 Cortex-A SoC 的 secure boot 不是直接使用 TF-A，就是在架構上與它相容：

- MediaTek（MTK）Helio / Dimensity 系列：BL31 使用 TF-A upstream 加上 MTK-specific platform 層
- NXP i.MX 8/9 系列：官方 BSP 直接以 TF-A 為 BL31
- STMicroelectronics STM32MP1/MP2：TF-A 是官方 AP secure firmware
- Rockchip RK3588：TF-A BL31 + 廠商自訂 BL32
- NVIDIA Jetson（Orin/AGX）：TF-A 作為 EL3 monitor

這意味著：讀懂 TF-A source，等同讀懂這些平台 secure boot 的設計意圖。出了漏洞報告，能夠直接對到 `plat/` 目錄裡的客製化差異，判斷廠商有沒有引入額外的 attack surface。不了解 TF-A，看 CVE 報告只能看到漏洞描述，看不懂根因。

---

## 16.2 source tree 結構

TF-A 的 source tree 模組化程度很高，理解目錄分工是閱讀 call path 的前提。

```
trusted-firmware-a/
|
+-- bl1/                  BL1 主邏輯
|   +-- bl1_main.c        bl1_main(), auth_mod 呼叫入口
|   +-- aarch64/
|       +-- bl1_entrypoint.S   reset vector, 跳 bl1_setup()
|
+-- bl2/                  BL2 主邏輯
|   +-- bl2_main.c        bl2_main(), 載入 FIP images
|   +-- aarch64/
|       +-- bl2_entrypoint.S
|
+-- bl31/                 BL31 EL3 Runtime Firmware
|   +-- bl31_main.c       bl31_main(), runtime services 初始化
|   +-- aarch64/
|       +-- bl31_entrypoint.S
|       +-- runtime_exceptions.S   SMC/IRQ/FIQ 向量表
|
+-- bl32/                 BL32 Secure Payload (OP-TEE 接口)
|   +-- tsp/              Test Secure Payload（測試用）
|   +-- optee/            OP-TEE 整合（實際 OP-TEE 自己有 repo）
|
+-- plat/                 平台相關實作
|   +-- arm/              ARM FVP / Juno 參考板
|   |   +-- fvp/
|   |   +-- juno/
|   +-- qemu/             QEMU virt machine（本章用）
|   +-- mediatek/         MTK SoC family
|   +-- nxp/              NXP i.MX
|   +-- st/               STM32MP
|   +-- rockchip/
|   ...
|
+-- services/             EL3 Runtime Services
|   +-- std_svc/          標準服務：PSCI, SMCCC
|   |   +-- psci/         PSCI 實作
|   |   +-- smccc/
|   +-- arm_arch_svc/     ARM Architecture Services
|   +-- spm/              Secure Partition Manager (v8.4+)
|
+-- drivers/              硬體驅動
|   +-- auth/             驗簽引擎（TBBR CoT）
|   |   +-- mbedtls/      MbedTLS 加密後端
|   |   +-- auth_mod.c
|   +-- io/               FIP parser, 儲存 I/O 抽象層
|
+-- lib/
|   +-- el3_runtime/      EL3 CPU context 管理
|   +-- cpus/             CPU-specific erratum workaround
|
+-- include/
|   +-- common/
|   |   +-- bl_common.h   entry_point_info, bl_params 定義
|   +-- lib/
|       +-- el3_runtime/
|           +-- context.h  cpu_context_t 定義
```

核心原則：`bl*/` 目錄放各階段的**機制（mechanism）**，`plat/` 目錄放**策略（policy）**。同一個 `bl2_main.c` 邏輯，不同 SoC 靠 `plat/<vendor>/` 提供 `bl2_plat_get_bl31_meminfo()` 等 hook 注入平台差異，不修改核心邏輯。這個設計讓廠商輕鬆 port，也讓研究者可以先讀共用路徑、再看平台差異。

---

## 16.3 各 BL 職責細節

### 16.3.1 BL1：架構初始化與 BL2 載入

BL1 對應到晶片上的 BootROM，幾乎所有邏輯都在不可修改的 ROM 裡。TF-A 的 BL1 source 是「BootROM 行為的參考文件」，SoC 廠以此為藍本客製。

**entrypoint → C runtime**

```
bl1_entrypoint.S
  el3_entrypoint_common()   -- EL3 通用初始化宏：
    - 清 EL3 例外遮罩（DAIF）
    - 設定 SCTLR_EL3（關 MMU/Cache，AArch64 模式）
    - 初始化 stack pointer
    - zero-fill BSS
    - 呼叫 bl1_setup()     -- 平台早期初始化（UART、時鐘）
    - 呼叫 bl1_main()
```

**bl1_main() 關鍵路徑**（`bl1/bl1_main.c`）：

```c
void bl1_main(void)
{
    /* 1. 平台初始化：UART、看門狗等 */
    bl1_plat_arch_setup();

    /* 2. 初始化驗簽模組（auth_mod）*/
    auth_mod_init();

    /* 3. 呼叫 bl1_load_bl2()：
          - 從 FIP 找 BL2 image
          - 呼叫 auth_mod_verify_img() 驗 BL2 certificate chain
          - 驗過才複製到 SRAM */
    bl1_load_bl2();

    /* 4. 跳轉 BL2 */
    bl1_prepare_next_image(BL2_IMAGE_ID);
    /* ... ERET to BL2 */
}
```

BL1 的攻擊面集中在步驟 3：FIP header 解析和 X.509 certificate parser 都在驗簽之前執行，任何 OOB read/write 都發生在信任建立之前，攻擊者可能在 BL1 就劫持控制流。Ch 15 的 TBBR 概念在這裡對應到具體 code path。

**最小 C runtime 的意義**：BL1 執行時 DRAM 尚未初始化（DDR init 在 BL2 做），只有 SoC 上的幾十 KB SRAM 可用。stack 和 BSS 都在 SRAM，code 從 ROM 執行。這限制了 BL1 能做的事，同時也限制了漏洞利用的空間——heap 根本不存在。

### 16.3.2 BL2：映像載入協調者

BL2 是「第一個可更新的安全韌體」，儲存在 Flash，BL1 驗簽後執行。BL2 的主要職責是載入並驗簽所有後續映像，建立開機參數結構，再移交給 BL31。

**bl2_main() 核心流程**（`bl2/bl2_main.c`）：

```c
void bl2_main(void)
{
    /* 1. 平台初始化，包含 DDR init（最重要的硬體初始化之一） */
    bl2_plat_arch_setup();

    /* 2. 初始化 IO layer（讀 FIP 的抽象層）*/
    plat_io_setup();

    /* 3. 載入並驗簽所有 BL images */
    bl_params_t *bl2_to_next_bl_params = bl2_load_images();

    /* 4. 傳遞 bl_params 鏈給 BL31 */
    bl2_plat_preload_setup();

    /* 5. 跳轉 BL31 */
    smc(BL1_SMC_RUN_IMAGE, (unsigned long)bl31_ep_info, ...);
}
```

**bl_params 鏈**是 BL2 傳給 BL31 的核心資料結構，定義在 `include/common/bl_common.h`：

```c
typedef struct bl_load_info_node {
    unsigned int       image_id;
    image_info_t      *image_info;   /* 映像在記憶體的位址/大小 */
    entry_point_info_t *ep_info;     /* 執行入口點資訊 */
    struct bl_load_info_node *next_load_info;
} bl_load_info_node_t;
```

`entry_point_info_t` 包含：目標 EL 層級、SPSR、PC 值，BL31 靠它決定「跳到哪、以什麼特權層執行」。BL33（U-Boot/UEFI）的 ep_info 裡的 EL 設定一旦被篡改，BL31 可能以錯誤特權層啟動 NS firmware。

**載入順序**：BL2 先載入 BL31 到 Secure DRAM，再載入 BL32（可選）到 Secure DRAM，最後載入 BL33 到 NS DRAM（DRAM 最低端，供 U-Boot 使用）。每個 image 都跑過完整的 CoT（Chain of Trust）驗簽，下面 16.5 節詳述。

### 16.3.3 BL31：EL3 Runtime Firmware

BL31 是 TF-A 最重要的元件，系統啟動後永遠常駐 EL3，所有 SMC 呼叫都在此處理。

**bl31_main() 初始化序列**（`bl31/bl31_main.c`）：

```c
void bl31_main(void)
{
    /* 1. 平台 EL3 設定：GIC、系統計數器、電源控制器 */
    bl31_plat_arch_setup();

    /* 2. 初始化 runtime services（逐一呼叫各 service 的 init()） */
    runtime_svc_init();   /* 掃描 __RT_SVC_DESCS__ linker section */

    /* 3. 設定 BL32 入口點（若存在），準備跳轉 */
    bl31_prepare_next_image_entry();

    /* 4. 初始化後跳到 BL32（若無則直接到 BL33 路徑） */
    /* 透過 el3_exit() → ERET */
}
```

`runtime_svc_init()` 迭代一個 linker section `__RT_SVC_DESCS__`，其中每個 entry 是一個 `rt_svc_desc_t`：

```c
typedef struct rt_svc_desc {
    uint8_t       start_oen;   /* Owning Entity Number 起始 */
    uint8_t       end_oen;     /* Owning Entity Number 終止 */
    uint8_t       call_type;   /* SMC_TYPE_FAST 或 SMC_TYPE_YIELD */
    const char   *name;
    rt_svc_initfn_t init;      /* 初始化 callback */
    rt_svc_handle_t handle;    /* SMC handler callback */
} rt_svc_desc_t;
```

各 runtime service 用 `DECLARE_RT_SVC()` 宏把自己的 descriptor 放進這個 section，BL31 啟動時統一初始化、統一路由 SMC。這個架構讓 PSCI、SMCCC query、SPM 等服務以插件形式存在。

**el3_exit()** 是 BL31 跳回低特權層的出口：它恢復目標 EL 的 CPU context（從 `cpu_context_t` 取值），填好 SPSR_EL3 和 ELR_EL3，執行 `ERET`，硬體依 SPSR 決定目標 EL 和執行狀態（AArch64/AArch32）。

### 16.3.4 SMC Calling Convention（SMCCC）

SMC（Secure Monitor Call）是 AArch64 的指令，執行後觸發 EL3 例外，BL31 的 `runtime_exceptions.S` 向量表接住，分派到對應 handler。**SMCCC（SMC Calling Convention）** 規範了 SMC 的呼叫協定，ARM 發布為 SMCCC spec（DEN0028）。

**Function ID 編碼（x0 的 32-bit 值）**：

```
31      30  29  28         24  23           16  15                0
+-------+---+---+-----------+----------------+--------------------+
|  type | cc|rsvd|   OEN    |   must_be_zero  |  function_number  |
+-------+---+---+-----------+----------------+--------------------+
  bit31=0: 32-bit call
  bit31=1: 64-bit call (SMC64)
  bit30=0: yielding call
  bit30=1: fast call
  bits[29:24] = OEN (Owning Entity Number)
```

**OEN 分配**：

| OEN 值 | 擁有者 |
|--------|--------|
| 0x00   | ARM Architecture Calls（SMCCC 版本查詢等） |
| 0x01   | CPU Service（CPU-specific ARM service） |
| 0x02   | SiP Service（Silicon Provider，SoC 廠自定） |
| 0x03   | OEM Service |
| 0x04   | Standard Secure Service（PSCI、TrustZone 等） |
| 0x05-0x30 | Trusted Application Calls |
| 0x31-0x3E | Trusted OS Calls |
| 0x3F   | Hypervisor Calls |

**Fast call vs Yielding call**：

| 類型 | bit30 | 語義 |
|------|-------|------|
| Fast | 1 | 原子完成，不可被 FIQ/IRQ 中斷，handler 必須快速返回 |
| Yielding | 0 | 可以被中斷，handler 可以主動讓出 CPU（用於長時間 Secure OS 操作） |

**World switch 流程**（Fast call 路徑）：

```
NS World (EL1/EL2)
  |
  | SMC #0 (x0=Function ID, x1-x7=params)
  |
  v
EL3 vector table (runtime_exceptions.S)
  handle_sync_exception_from_lower_el()
    |
    +--> cm_el1_sysregs_context_save(NON_SECURE)
    |    -- 儲存 NS EL1 sysreg: SP_EL0, SP_EL1, ELR_EL1, SPSR_EL1...
    |
    +--> smc_handler()
    |    -- 解碼 x0 Function ID
    |    -- 查 rt_svc_descs 找對應 handler
    |    -- 呼叫 handler(x0..x7, handle, cookie)
    |
    +--> (若需切換到 Secure World)
    |    cm_el1_sysregs_context_restore(SECURE)
    |    -- 恢復 Secure EL1 sysreg
    |    SCR_EL3.NS = 0
    |    ERET → Secure World EL1 (OP-TEE)
    |
    +--> (若不需切換，直接返回 NS)
         cm_el1_sysregs_context_restore(NON_SECURE)
         SCR_EL3.NS = 1
         ERET → NS World
```

`cpu_context_t`（`include/lib/el3_runtime/context.h`）存放一個世界的全部 CPU 狀態：GP registers（x0-x30）、SP_EL0/SP_EL1、ELR_EL1、SPSR_EL1 以及所有系統暫存器快照。BL31 維護兩個 context（NS 和 Secure），世界切換時一存一還。

### 16.3.5 PSCI — Power State Coordination Interface

PSCI（Power State Coordination Interface，ARM DEN0022）規範 CPU 電源狀態的標準 API。Linux kernel 的 CPU hotplug 和 suspend/resume 在 ARM 上透過 PSCI SMC 呼叫。

**常見 PSCI Function ID**：

| Function | SMC64 ID | 用途 |
|----------|----------|------|
| PSCI_VERSION | 0x84000000 | 查詢版本 |
| CPU_SUSPEND | 0xC4000001 | 讓 CPU 進入低功耗狀態 |
| CPU_OFF | 0x84000002 | 關閉呼叫 CPU |
| CPU_ON | 0xC4000003 | 喚醒另一個 CPU |
| SYSTEM_RESET | 0x84000009 | 全系統重設 |
| SYSTEM_OFF | 0x84000008 | 關機 |

**psci_cpu_on() 實作路徑**（`services/std_svc/psci/psci_main.c`）：

```
psci_cpu_on(target_cpu, entry_point, context_id)
  |
  +--> psci_validate_mpidr(target_cpu)    -- 驗 CPU ID 合法性
  |
  +--> psci_get_target_local_pwr_states() -- 確認 CPU 現在是 OFF 狀態
  |
  +--> psci_cpu_on_start(target_cpu,      -- 設定 secondary CPU 的
  |         entry_point_info)               entry_point_info
  |    -- 把 entry_point（caller 提供的位址）存進 CPU 的 warm boot entry
  |
  +--> plat_cpu_pwrdwn_early_setup()      -- 平台相關電源序列
  |
  +--> psci_power_up_finish()             -- 觸發 SGI 或平台電源控制器
                                            喚醒目標 CPU
```

secondary CPU 醒來後從 `psci_entrypoint()` 開始（冷啟動之後是 warm boot），最終跳到 `entry_point` 所指的位址。**攻擊面在於**：如果 `entry_point` 的合法性只靠值域檢查而非密碼學驗證，呼叫者可以將 secondary CPU 引導到任意位址執行（PSCI CPU_ON secondary 濫用，是已有 CVE 的漏洞類別）。

---

## 16.4 FIP 格式深挖

**Firmware Image Package（FIP）** 是 TF-A 定義的容器格式，用來在單一 binary 中封裝多個 bootloader image 及其對應的 X.509 certificate。

### fip.bin Layout

```
fip.bin
+------------------------------------------+  offset 0
|  FIP ToC Header                          |
|  magic: 0xAA640001                       |  4 bytes
|  serial_number: (platform-defined)       |  4 bytes
|  flags: 0                                |  8 bytes
+------------------------------------------+  offset 16
|  ToC Entry [0]                           |
|  uuid[16]: BL2 UUID                      |
|  offset_address: 0x...(from file start)  |  8 bytes
|  size: ...                               |  8 bytes
|  flags: 0                                |  8 bytes
+------------------------------------------+  offset 16 + 40
|  ToC Entry [1]                           |
|  uuid[16]: BL31 UUID                     |
|  ...                                     |
+------------------------------------------+
|  ToC Entry [N-1]                         |
|  uuid[16]: all_zeros (END marker)        |
+------------------------------------------+
|  BL2 image data                          |
|  BL2 trusted key certificate            |
|  BL2 content certificate                |
+------------------------------------------+
|  BL31 image data                         |
|  BL31 certificate                        |
+------------------------------------------+
|  BL32 image data (optional)              |
|  BL32 certificate                        |
+------------------------------------------+
|  BL33 image data                         |
|  BL33 certificate                        |
+------------------------------------------+
```

每個 ToC Entry 是 40 bytes（UUID 16 + offset 8 + size 8 + flags 8），以全零 UUID 的 Entry 作為結尾標記。image 資料和對應的 X.509 certificate 緊接在一起，但 certificate 在 ToC 裡有獨立的 UUID entry（certificate UUID 與 image UUID 不同）。

**常見 UUID 對照**（定義在 `include/tools_share/firmware_image_package.h`）：

| Image | UUID 末尾 bytes（簡記） |
|-------|------------------------|
| BL2 | ...0x7e...07bf |
| BL31 | ...0x47...47 |
| BL32 / OP-TEE header | ...0x94...06 |
| BL33 (U-Boot/UEFI) | ...0xa7...06 |
| Trusted Key Certificate | ...0x4a...74 |
| BL31 Key Certificate | ...0x65...5a |
| BL2 Content Certificate | ...0x37...50 |
| FW_CONFIG | ...0x76...0f |
| TB_FW_CONFIG | ...0xce...b8 |

**fiptool 操作**（source 在 `tools/fiptool/`）：

```bash
# 列出 FIP 內容
fiptool info fip.bin

# 拆解所有 images
fiptool unpack fip.bin --out ./extracted/

# 建立 FIP（build 時自動呼叫，手動範例）
fiptool create \
  --tb-fw bl2.bin \
  --soc-fw bl31.bin \
  --nt-fw u-boot.bin \
  --tb-fw-cert bl2.crt \
  fip.bin

# 更新單一 image（常用於研究：把 BL33 換掉）
fiptool update --nt-fw my-bl33.bin fip.bin
```

安全研究時，`fiptool unpack` 配合 `binwalk` 是分析 production 裝置韌體的第一步。

---

## 16.5 TBBR Chain of Trust 驗簽實作

### 驗簽模組架構

TF-A 的驗簽實作叫 `auth_mod`（`drivers/auth/auth_mod.c`），支援不同的 CoT descriptor（每個平台可以定義自己的信任鏈結構）。

```
auth_mod_verify_img(image_id)
  |
  +--> auth_get_parent_img_id()     -- 從 CoT 定義找出這個 image 的父 cert
  |
  +--> (遞迴驗父 cert，直到 Root of Trust)
  |
  +--> crypto_mod_verify_signature()
  |    -- 呼叫 MbedTLS 做 RSA/ECDSA 驗簽
  |
  +--> crypto_mod_verify_hash()
  |    -- 驗 image hash 是否與 cert 裡的 SubjectPublicKeyInfo 匹配
  |
  +--> (若任何步驟失敗)
       plat_error_handler(err)     -- 平台決定後續：reset 或 halt
```

### TRUSTED_BOARD_BOOT=1 build flag

這個 flag 控制是否編譯 TBBR 驗簽邏輯。

```bash
make PLAT=qemu TRUSTED_BOARD_BOOT=1 \
     MBEDTLS_DIR=/path/to/mbedtls all fip
```

**沒有加這個 flag，驗簽邏輯根本不進 binary**。debug build 預設 `TRUSTED_BOARD_BOOT=0`，這意味著在開發過程中跑的 TF-A 完全不驗簽——所有 FIP 裡的 image 不管內容都會被執行。這不是設計缺陷，但是廠商把 debug build 流進生產是有先例的。

### CoT Descriptor 結構

每個平台在 `plat/<vendor>/` 或共用路徑定義 CoT，說明「誰驗誰」：

```
ROTPK（OTP fuse 燒入）
  |
  v
Trusted Key Certificate
  |  （用 ROTPK 驗簽，包含 Trusted World Key 和 Non-Trusted World Key）
  |
  +-- Trusted World Key
  |     |
  |     v
  |   BL31 Key Certificate
  |     |
  |     v
  |   BL31 Content Certificate（含 BL31 image hash）
  |     |
  |     v
  |   BL31 Image（驗 hash）
  |
  +-- Non-Trusted World Key
        |
        v
      BL33 Key Certificate
        |
        v
      BL33 Content Certificate（含 BL33 image hash）
        |
        v
      BL33 Image（驗 hash）
```

BL2 自己也有獨立的 certificate chain，BL1 從 ROTPK 開始驗。

### MbedTLS 整合

TF-A 使用 MbedTLS 作為加密後端，但編譯時只取 MbedTLS 的靜態函式庫，不帶動態連結。實際呼叫路徑：

```
auth_mod → crypto_mod → mbedtls_crypto_lib
  mbedtls_x509_crt_parse_der()     -- 解析 DER 格式 X.509 cert
  mbedtls_x509_crt_verify_with_profile()  -- 驗 cert chain
  mbedtls_rsa_rsassa_pkcs1_v15_verify()  -- RSA 驗簽
  mbedtls_sha256()                 -- hash 計算
```

**錯誤路徑保證**：驗簽失敗後，TF-A 規範要求執行不得繼續到目標 image。實作上：

```c
err = auth_mod_verify_img(image_id);
if (err != 0) {
    ERROR("Image id=%d failed authentication\n", image_id);
    plat_error_handler(err);
    /* plat_error_handler() 必須不返回（平台實作） */
    panic();  /* 雙重保險 */
}
```

`plat_error_handler()` 的實作因平台而異：QEMU 平台直接呼叫 `panic()`，生產 SoC 可能額外清除 DRAM（DRAM wipe）防止殘留機密資料被後續程式碼讀取，再硬重設（系統reset）。

---

## 16.6 TrustZone 銜接——SCR_EL3 與 TZASC

BL31 控制 TrustZone 的關鍵在兩個層面：暫存器層（`SCR_EL3`）和匯流排層（TZASC）。

### SCR_EL3（Secure Configuration Register）

```
SCR_EL3 關鍵位元：

bit[0]  NS   Non-secure bit：0 = Secure world, 1 = NS world
             BL31 在 ERET 前設好此位元，硬體在 ERET 後依此切世界
bit[1]  IRQ  IRQ 路由到 EL3（1）或當前 EL（0）
bit[2]  FIQ  FIQ 路由到 EL3（1）或當前 EL（0）
bit[3]  EA   External Abort 路由
bit[7]  SCD  Disable SMC at EL1 NS（設 1 可阻止 NS EL1 直呼 SMC）
bit[10] RW   1 = 下一低 EL 跑 AArch64，0 = AArch32
```

BL31 在跳轉 BL33（U-Boot）之前設定：

```c
/* 設定進入 NS world，AArch64 EL2 */
scr_val = SCR_RES1_BITS | SCR_NS_BIT | SCR_RW_BIT;
write_scr_el3(scr_val);
/* ERET → EL2-NS (或 EL1-NS if no hypervisor) */
```

跳轉 BL32（OP-TEE）時：

```c
/* 清 NS bit → Secure world */
scr_val &= ~SCR_NS_BIT;
write_scr_el3(scr_val);
/* ERET → EL1-S (OP-TEE) */
```

### TZASC（TrustZone Address Space Controller）

TZASC 是 DRAM 控制器前的硬體防護元件，可以把 DRAM 位址空間切成多個 region，每個 region 獨立設定「只有 Secure 存取」或「Secure 和 NS 都可存取」。

```
                +----------+
  Secure CPU -->|          |-->  Secure DRAM region   (e.g. 0x0E000000-0x0E1FFFFF)
                |  TZASC   |
  NS CPU ------>|          |-->  NS DRAM region        (e.g. 0x40000000+)
                +----------+
                     |
                 NS 存取 Secure region → Bus Error（或 DECERR）
```

BL2 在 DDR init 之後、載入 images 之前配置 TZASC：

```c
/* plat/arm/common/arm_tzc_setup.c (以 ARM FVP 為例) */
arm_tzc400_setup(ARM_TZC_BASE, NULL);
tzc400_partition_mem(BL31_BASE, BL31_LIMIT, TZC_ATTR_REGION_S_RDWR);
tzc400_partition_mem(OPTEE_DRAM_S_BASE, OPTEE_DRAM_S_SIZE, TZC_ATTR_REGION_S_RDWR);
tzc400_set_action(TZC_ACTION_RV_LOWOK);  /* NS 違規 → bus error */
tzc400_enable_filters();
```

TZASC 配置完成後，BL31 常駐的 DRAM 區段 NS world 完全不可讀寫，即使 Linux kernel 被 pwn 也無法直接讀取 BL31 的記憶體（攻擊者仍可透過 SMC 漏洞間接攻擊）。

---

## 16.7 攻擊面分析

### 16.7.1 SMC Handler 漏洞

SMC handler 是 BL31 對外的唯一介面，攻擊者從 NS world（甚至 NS EL0）透過 SMC 觸達 EL3。handler 的安全要求：

1. 嚴格驗證所有 x0-x7 參數——這些都是 caller 控制的值
2. 不能假設 caller 是可信的（即使來自 NS kernel）
3. 指標型參數若指向 shared memory，必須在使用前驗合法性

**Type confusion**：若 handler 根據 x0 高位元選 code path 但對低位元假設類型，caller 可以傳入邊界值繞過型別檢查，在 handler 中做越界存取。OEM SiP service（OEN=0x02）的 handler 是最常見的問題來源，因為 SoC 廠的審計深度遠低於 ARM upstream。

**OOB 讀寫**：handler 若把 x1 當 buffer size 而沒上限，caller 填入超大值可以讓 EL3 讀寫越界。EL3 的記憶體寫越界直接影響 BL31 自身的資料結構（`cpu_context_t`、runtime service 狀態）。

**SMC Fuzzing 工具**：

- **TriForce**：基於 AFL 的 hypervisor fuzzing 框架，可以改寫來 fuzz SMC interface
- **SyzVegas**：將 Syzkaller 延伸到 SMC interface，在 QEMU 上對 BL31 做 coverage-guided fuzzing
- 實務上：在 QEMU virt 跑 TF-A，在 Linux guest 寫一個 kernel module 送各種 SMC，接上 QEMU GDB stub 觀察 EL3 狀態

### 16.7.2 BL2 Image 載入的 TOCTOU

BL2 的驗簽流程：計算 hash → 比對 cert 裡的 hash → hash 比對通過 → 複製 image 到目標記憶體。

**TOCTOU 問題（Time-of-Check to Time-of-Use）**：如果 image 在 DRAM（而非 SRAM），且 DRAM 在計算 hash 後、複製前可以被修改（例如透過 DMA），攻擊者可以：

1. 放置合法 image 讓 hash 計算通過
2. 在 hash 計算完成後、`memcpy` 前，透過 DMA 控制器把 DRAM 內容換掉
3. BL2 把惡意 image 複製到 Secure 記憶體

防禦：讓 image 在 SRAM（不可被 NS DMA 存取），或在 TZASC 配置確保後才讀取。

**驗簽失敗不繼續執行**的保證：如 16.5 節所述，規範要求 `plat_error_handler()` 不返回，但如果廠商客製時沒有嚴格實作（例如只 print error 然後 return），錯誤路徑就變成繞過點。

### 16.7.3 已公開的 CVE

**CVE-2022-47630（TF-A BL2 image parser OOB read）**

已公開報告顯示，BL2 在解析 FIP ToC Entry 時，若 ToC Entry 的 `size` 欄位超過分配的 buffer，code path 在計算末端位址時可能發生整數溢位，導致後續讀取超出 buffer 邊界。影響版本為 TF-A v2.0-v2.8。這個 issue 在 BL2 驗簽之前的 IO 層就觸發，屬於「繞過驗簽而非破解驗簽」的漏洞類別。修復方式：在 `io_fip.c` 增加 size 欄位的合法性檢查。

**CVE-2023-49100（SMC handler memory corruption）**

已公開報告指出，特定 platform 的 SiP SMC service 在處理某個 fast call 時，對 x2 參數做指標運算前未驗合法範圍，在 EL3 內部觸發記憶體越界寫入。影響平台特定（非 upstream 通用路徑）。修復方式：在 handler 入口增加參數範圍驗證。

這兩個 CVE 共同說明的模式：FIP 解析層和 SMC handler 是 TF-A 攻擊面的兩個主要節點，分別對應「在驗簽之前」和「在驗簽之後的 runtime 期間」。

---

## 16.8 真跑驗證

### Build TF-A 給 QEMU virt

**本段未實測，為理論預期行為。** 需要 `aarch64-linux-gnu-gcc` 工具鏈與 MbedTLS source tree，本地未必有裝。

```bash
# 取得 source
git clone https://git.trustedfirmware.org/TF-A/trusted-firmware-a.git
cd trusted-firmware-a

# 取得 MbedTLS（TF-A 需要它做加密）
git clone https://github.com/ARMmbed/mbedtls.git

# Build BL1, BL2, BL31（不含 TRUSTED_BOARD_BOOT，簡化用）
# 需要先自備 BL33（U-Boot 或 UEFI image）
make PLAT=qemu \
     BL33=/path/to/u-boot.bin \
     QEMU_USE_GIC_DRIVER=QEMU_GICV3 \
     DEBUG=0 \
     all fip

# 產出：
#   build/qemu/release/bl1.bin     (放 -bios 位置)
#   build/qemu/release/fip.bin     (BL2+BL31+BL33 打包)
```

**QEMU 執行**：

```bash
qemu-system-aarch64 \
  -machine virt,secure=on,gic-version=3 \
  -cpu cortex-a57 \
  -nographic \
  -m 1G \
  -bios build/qemu/release/bl1.bin \
  -d unimp,mmu \
  -smp 2
```

QEMU `virt` machine 的 `secure=on` 啟用 TrustZone 模擬，BIOS 位置對應到 0x0 reset vector，BL1 從此啟動。`-d unimp,mmu` 可以在 QEMU log 中看到 TLB/MMU 相關事件，輔助理解 EL3 MMU 設定過程。

### 與 Ch 15 QEMU UEFI 實測的關係

Ch 15 驗證過的 QEMU UEFI 環境（`qemu-system-aarch64 -bios QEMU_EFI.fd`）是「QEMU 直接 boot UEFI（BL33 層）」的路徑，QEMU firmware 本身扮演了 BL1+BL2+BL31 的角色。那個實測驗證的是 BL33 (UEFI) 層正常工作——以 Ch 15 的真跑為基礎，本章的 TF-A BL31 在 QEMU virt 是 optional 的 EL3 layer，QEMU 可以選擇略過 BL31 直接 boot BL33。兩者的差別：

```
QEMU virt（直接 UEFI）：
  QEMU 本身 → UEFI (EFI.fd) → GRUB → Linux
  EL3 由 QEMU 模擬，無 TF-A BL31

QEMU virt + TF-A：
  QEMU 本身 → BL1 (bl1.bin) → BL2 → BL31 (常駐) → BL33 (u-boot) → Linux
  EL3 由 TF-A BL31 掌控，SMC 完整可用
```

在安全研究場景，要 fuzz BL31 的 SMC 介面必須走後者，讓 TF-A BL31 實際存在於記憶體。

---

## 16.9 對比取捨

TF-A 與廠商私有 bootloader 的比較：

| 面向 | TF-A (開源) | 廠商私有（如 MTK preloader/LK, Qualcomm XBL） |
|------|-------------|----------------------------------------------|
| 可審計性 | 完整 source 公開，社群 audit | 只有 binary，逆向成本高，漏洞窗口期長 |
| PSCI 合規 | upstream 嚴格對標 DEN0022 | 廠商常自定電源管理，PSCI 支援可能部分缺失 |
| SMC handler 品質 | upstream code review 嚴格；plat/ 層差異大 | 閉源，OEM SiP service 邏輯從未公開審計 |
| 更新速度 | trustedfirmware.org 定期 release | OTA 依廠商意願，部分 SoC 永不更新 |
| 攻擊面大小 | 已知（可從 source 列出所有 SMC entry point）| 未知（需逆向確認 handler 數量） |
| CVE 揭露 | TF-A 有專屬 security 郵件組，修復公開 | 廠商斟酌揭露，很多 patch 只在 BSP diff 裡 |
| 客製化風險 | plat/ 層差異可能引入新 bug | 全都是「plat/ 層」，風險面更廣 |
| TZASC 配置 | arm_tzc400_setup() 有參考實作 | 廠商私有 TZC 配置，設錯的案例不少 |

**結論**：TF-A 的「開源」不代表「無漏洞」，而是代表「漏洞找得到、修復追得到、攻擊者研究成本與防禦方研究成本對稱」。廠商私有 bootloader 反轉了這個對稱：研究者挖到漏洞很難自己修、廠商修好漏洞很難讓研究者確認。

---

## 16.10 踩雷

**1. `TRUSTED_BOARD_BOOT=0` 是 debug 預設，等同沒有驗簽**

新建 TF-A 環境預設不開驗簽，`make PLAT=qemu all fip` 不加 `TRUSTED_BOARD_BOOT=1` 跑出來的 binary 不驗任何 image。這個 binary 流進生產裝置（測試版韌體外洩），攻擊者可以換掉 FIP 裡的任何 image 而不被偵測。驗方式：看 `build/<plat>/<debug|release>/bl1/bl1.map`，如果沒有 `auth_mod.o`，就是沒驗簽。

**2. BL31 load address 必須在 Secure DRAM region，否則 NS world 能改**

如果 TZASC 配置把 BL31 所在的 DRAM 位址設成 NS 可讀寫（配置順序錯誤，或平台 `arm_tzc_setup()` 沒覆蓋正確範圍），Linux kernel 可以直接 `mmap` 或 `/dev/mem` 讀寫 BL31 的記憶體，包括 `cpu_context_t` 和 runtime service 狀態。結果：NS world 可以偽造 SMC 的返回值，或竄改 Secure context 觸發下次世界切換時執行任意 EL3 代碼。確認方式：`cat /proc/iomem` 看 `reserved` 標記的區域是否與 TF-A build log 中的 BL31 位址一致，再確認 TZASC 配置有保護它。

**3. PSCI CPU_ON 的 secondary CPU boot address 未驗證**

PSCI `CPU_ON` 的第二個參數 `entry_point_address` 是 secondary CPU 醒來後要跳到的位址，caller 完全控制。若平台的 `psci_cpu_on()` 實作只驗 MPIDR（確認是合法 CPU ID）而不驗 `entry_point_address` 的範圍，攻擊者可以把 secondary CPU 導向任意位址——包括 Secure DRAM 或者精心準備的 gadget chain。TF-A upstream 的防禦是在 warm boot entry 做 measurement，但廠商客製的平台未必都引入此保護。這個漏洞類別在嵌入式 SoC 上有真實 CVE 案例。

**4. OEM SiP SMC handler（OEN=0x02）幾乎從不公開審計**

SiP service 是廠商用來暴露晶片特有功能的 SMC 介面（電壓調整、efuse 存取、thermal 控制等）。這些 handler 通常是廠商工程師快速寫成、沒有 code review、沒有 fuzz 測試，Function ID 也不在任何公開規範裡。問題是：這些 handler 跑在 EL3，任何 NS EL0 的 user process 都可以呼叫 SMC。只要知道 Function ID（可以從 vendor kernel driver 的 `smc()` 呼叫反推），就能在毫無提權的狀態下把任意參數送進 EL3 handler。OEM range 的起點是 `0xC2000000`（SMC64 fast call），廠商在 `0xC2000000-0xC200FFFF` 範圍自由定義，完全無標準約束。

---

## 進階延伸

- **OP-TEE + TF-A 完整棧**：在 QEMU 上跑 BL31 + OP-TEE (BL32) + Linux，實際追蹤一個 Trusted Application 呼叫的 SMC path，對照 `services/spd/opteed/opteed_main.c` 裡的世界切換邏輯。
- **ARM CCA（Confidential Compute Architecture）**：TF-A 正在整合 RMM（Realm Management Monitor）支援，在 EL3 之下再加一個保護層（Realm world）。理解 TF-A 的現在才能看懂 CCA 在加什麼。
- **PSCI 電源管理漏洞類別系統整理**：搜尋 TF-A issue tracker 和 Project Zero blog，整理 CPU_ON / CPU_SUSPEND / SYSTEM_RESET 各 function 的歷史漏洞。
- **平台差異比較**：取 MTK Android BSP（LineageOS kernel source 可合法取得），找 `plat/mediatek/` 對應目錄，比較與 ARM upstream `plat/arm/` 的差異，列出 MTK 加了哪些 SiP SMC handler。

---

## 動手練習

1. **fiptool 拆解與分析**：從網路上下載一份開放 ARM 裝置的韌體（Raspberry Pi 4 的 `armstub8.bin` 是可合法取得的 TF-A binary），用 `fiptool info` 和 `binwalk` 分析它的結構，確認它是否包含 X.509 cert，推斷 `TRUSTED_BOARD_BOOT` 的開關狀態。

2. **SMC call trace**：在 QEMU virt 上跑 Linux（不加 TF-A BL31 也可以，QEMU 自身提供 PSCI），用 `strace -e trace=all` 追一次 `echo mem > /sys/power/state` 觸發的路徑，再在 kernel source 找到 `psci_suspend_finisher()`，確認實際發出的 PSCI SMC function ID。

3. **ScatterLoad the context**：在 TF-A source 的 `include/lib/el3_runtime/context.h` 裡找到 `cpu_context_t` 的完整定義，計算這個結構的 byte size，以及在 BL31 的 BSS/data 段裡有幾份（hint：每個 CPU 核心有幾個世界就有幾份）。確認 BL31 為一個 4-core SoC 分配的 context 記憶體總量。

4. **CVE 定位練習**：CVE-2022-47630 影響 `io_fip.c`，在 TF-A source 的 `drivers/io/io_fip.c` 找到 ToC Entry 解析迴圈，指出哪一行的計算在修補前可能產生整數溢位，再找對應的 git commit 確認修復方式。

---

## 本章重點

- TF-A source tree 分成 mechanism（`bl*/`）和 policy（`plat/`），理解這個分工才能快速定位廠商客製的攻擊面
- BL1 在 SRAM 中執行，BL2 做 DDR init 並建立 `bl_params` 鏈，BL31 常駐 EL3 處理所有 SMC
- FIP 格式是 UUID-indexed 的 image 容器，`fiptool` 可以直接拆解，是韌體分析的入門工具
- TBBR 驗簽由 `auth_mod` 呼叫 MbedTLS 完成，`TRUSTED_BOARD_BOOT=0` 完全不驗簽
- SMCCC 定義 Function ID 編碼，OEN 決定哪個 runtime service 處理，handler 入口是 EL3 攻擊面的集中點
- SCR_EL3.NS bit 控制世界切換，TZASC 在匯流排層保護 Secure DRAM，兩者缺一不可
- CVE-2022-47630 和 CVE-2023-49100 分別代表「驗簽前 parser」和「runtime SMC handler」兩個主要漏洞節點

---

## 自我檢核

- [ ] 能夠從記憶體描述 FIP ToC 的 binary layout（header + entry 格式 + end marker）
- [ ] 能列出 BL1 → BL2 → BL31 的職責邊界，以及每個 BL 執行時 DRAM 是否已初始化
- [ ] 能解碼一個 32-bit SMC Function ID（說出 OEN、call type、是否 SMC64）
- [ ] 能解釋 Fast call 和 Yielding call 的語義差異，以及各自適用場景
- [ ] 能描述 TOCTOU 攻擊在 BL2 image 載入時的前提條件與防禦方式
- [ ] 能說明 `TRUSTED_BOARD_BOOT=0` 的影響，以及如何從 build artifact 確認開關狀態
- [ ] 能解釋為什麼 OEM SiP SMC handler 是最高風險的 attack surface，以及攻擊者如何發現 Function ID
- [ ] 知道 TZASC 配置錯誤的後果，以及如何從 Linux userland 驗證 BL31 記憶體是否受保護

---

## 延伸閱讀

1. **TF-A 官方文件**：https://trustedfirmware-a.readthedocs.io/en/latest/ — Threat Model 章節是理解官方認定攻擊面的最直接資料，`Design > Firmware Update` 說明 CoT 實作細節
2. **ARM SMCCC Specification（DEN0028）**：https://developer.arm.com/documentation/den0028 — Function ID encoding 和 OEN 分配的一手規範
3. **ARM PSCI Specification（DEN0022）**：https://developer.arm.com/documentation/den0022 — CPU_ON / CPU_SUSPEND 語義定義，對應 TF-A 實作找 `services/std_svc/psci/`
4. **"Breaking Samsung's ARM TrustZone" (Azimuth Security, 2017)**：公開研究展示如何從 Samsung SoC 的 SiP SMC handler 出發，到達 EL3 任意代碼執行，是 SMC fuzzing 攻擊鏈的經典案例
5. **CVE-2022-47630 advisory 與 git fix**：https://git.trustedfirmware.org/TF-A/trusted-firmware-a.git 的 `drivers/io/io_fip.c` diff，完整看懂 ToC size overflow 修法
6. **"Mind the Gap: Analyzing the TrustZone Attack Surface"（ACM CCS 論文，Cerdeira et al. 2020）**：系統性整理 TrustZone 攻擊面，含 SMC handler 分類統計，確立了本章 16.7.1 節的框架基礎

→ [下一章](./17-uboot-attack-surface.md)
