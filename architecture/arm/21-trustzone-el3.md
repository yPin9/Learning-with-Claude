# Ch 21 — TrustZone 與 EL3

> 目標：搞懂 ARM TrustZone — 那個讓 SoC 同時跑 secure / non-secure world 的硬體機制。EL3 firmware（ARM Trusted Firmware-A）做什麼、SMC call、OP-TEE 在哪、iOS / Android Keystore 的硬體基石是什麼。

## TrustZone 是什麼

ARM TrustZone（A profile，2003 年起）把整個 SoC **拆成兩個並行 world**：

```
                    NS bit = 0                    NS bit = 1
                    ┌─────────────────┐          ┌─────────────────┐
                    │  Secure World   │          │ Non-secure World│
                    │                 │          │                 │
EL3 (Monitor)       │   Always EL3    │          │  (impossible)   │
                    │                 │          │                 │
EL2 (Hypervisor)    │  Secure EL2     │          │ NS EL2 (KVM)    │
                    │  (ARMv8.4 起)   │          │                 │
EL1 (Kernel)        │  TEE OS         │          │ Linux kernel    │
                    │  (OP-TEE等)     │          │                 │
EL0 (User)          │  Trusted Apps   │          │ Android / iOS app│
                    │  (TA)           │          │                 │
                    └─────────────────┘          └─────────────────┘
```

**同一顆 CPU，同一條指令流水線，分時跑兩個 world**。world 之間有「**NS bit (Non-Secure)**」這顆位標記，硬體層面隔離。

## NS bit 怎麼生效

每筆 memory access、每個 cache line tag、每個 TLB entry 都帶 NS bit：

```
Non-secure world 的 access：NS bit = 1
  能存取：Non-secure memory
  不能存取：Secure memory（會 fault）

Secure world 的 access：NS bit = 0（or NS-allowed access with 1）
  能存取：Secure memory + Non-secure memory（如允許）
```

**整個 memory 由 SoC 廠透過 TZASC（TrustZone Address Space Controller）切成 secure / non-secure region**。

實體記憶體可以動態 reconfig — boot 時 firmware 把 secure 區（OP-TEE、Trusted Apps）標記，剩下給 non-secure。

## Secure / Non-secure 切換

只能透過 EL3：

```
Non-secure EL1 ──smc #0──→ EL3 (Monitor)
                              │
                              ├── 切 SCR_EL3.NS = 0
                              └── ERET 到 secure EL1

Secure EL1 ──smc #0──→ EL3
                          │
                          ├── 切 SCR_EL3.NS = 1
                          └── ERET 到 non-secure EL1
```

EL3 是**唯一能改 NS bit 的層**。每次 world switch 都要：
1. 進 EL3
2. 保存當前 world 的 context（GP regs、system regs）
3. 載入目標 world 的 context
4. 切 NS bit
5. ERET 到目標 world

## Secure Monitor Call (SMC)

`SMC #imm` 指令 = 「我要進 EL3」。`#imm` 對 CPU 沒意義，只是 firmware 區分 call ID 的標記。

ARM 定義了 **SMC Calling Convention (SMCCC)**：

```
X0: function ID
X1-X7: arguments
return in X0-X3
```

Function ID 編碼：

```
bit[31]    Fast call (1) / Yielding call (0)
bit[30]    SMC32 (0) / SMC64 (1)
bit[29:24] Service: 0=PSCI, 1=SiP, 2=OEM, 3=Standard, 4=Standard Hyp...
bit[15:0]  Function number
```

例：PSCI `CPU_ON` 喚醒另一個核：

```c
#define PSCI_CPU_ON   0xC4000003
register uint64_t x0 __asm__("x0") = PSCI_CPU_ON;
register uint64_t x1 __asm__("x1") = target_cpu;
register uint64_t x2 __asm__("x2") = entry_point;
register uint64_t x3 __asm__("x3") = context_id;
asm volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3));
// x0 = 結果
```

## 常見的 EL3 firmware：ARM Trusted Firmware-A (TF-A)

幾乎所有 ARM server / 高端嵌入式都用 **ARM Trusted Firmware-A**（github.com/ARM-software/arm-trusted-firmware）：

- **BL1**：boot ROM 之後第一個 stage（EL3）
- **BL2**：platform init（可降到 EL1，做 trusted boot 流程）
- **BL31**：常駐 EL3 monitor，處理 SMC（PSCI、SiP）
- **BL32**：optional secure payload（OP-TEE、Trusty）
- **BL33**：non-secure firmware（U-Boot、UEFI）

PSCI（Power State Coordination Interface）是 TF-A 提供的標準 SMC service：CPU_ON / CPU_OFF / SYSTEM_RESET / CPU_SUSPEND 等，給 OS 用。**Linux 啟動 secondary core 就是 SMC PSCI_CPU_ON**。

## TEE OS：OP-TEE 的位置

OP-TEE（Open Portable TEE）跑在 **Secure EL1**，是 secure world 的 mini OS：

- 載入 trusted apps（TA）跑在 secure EL0
- 提供 syscall / GlobalPlatform TEE API
- 透過 SMC 與 non-secure 通訊

非 secure 端怎麼用 OP-TEE：

```c
// non-secure Linux app
TEEC_Context ctx;
TEEC_Session sess;
TEEC_Operation op;
TEEC_InitializeContext(NULL, &ctx);
TEEC_OpenSession(&ctx, &sess, &uuid_of_TA, ...);
TEEC_InvokeCommand(&sess, CMD_ENCRYPT, &op, NULL);
```

底下走 `tee` driver → SMC → BL31 → OP-TEE → 對應 TA 跑邏輯 → 返回。

iOS / Android Keystore / 指紋識別、行動支付的「secure key 不離開 SoC」承諾，硬體基石都是這個架構。

## iOS Secure Enclave：另一條路

Apple 沒用 OP-TEE，**自己做 Secure Enclave Processor (SEP)**：

- 完全獨立的 CPU 核（不是分時，是物理獨立）
- 跑自家 sepOS
- 與主 CPU 透過 mailbox 通訊
- 控制 Touch ID / Face ID / Apple Pay 密鑰

設計上比 TrustZone「**更隔離**」（物理隔離 vs 分時隔離），但概念類似。Android 早期 SoC（高通 / 聯發科）大多走 TrustZone + TEE 路線。

## TrustZone 對 attack 的影響

TrustZone 不是萬靈藥。被攻破過的有名案例：

- **CVE-2015-6639** Qualcomm TrustZone：bootloader 漏洞，能在 secure world 跑任意 code
- **Spectre / Meltdown 變種對 TrustZone**：透過 cache side-channel 從 non-secure 推測 secure data
- **Foreshadow-NG**：類似 Spectre，影響 Intel SGX，ARM 變種還在研究中

工程含義：**TrustZone 提高攻擊門檻，但不是「絕對安全」**。寫 TA 還是要有 defensive coding。

## ARMv8.4 的 Secure EL2

原本 secure world 只有 EL3 / EL1 / EL0。ARMv8.4-A 加了 **Secure EL2**，讓 secure world 也能虛擬化（多個 TEE OS 共存）。

實務上 還是非常少見的場景，但 Apple SEP 與 Google Trusty 的高階用法可能用到。

## 一個常見誤解

「TrustZone 跑得是不是會比 non-secure 慢？」

不會。**同一條物理 CPU，指令速度一樣**。代價只在 **world switch 的開銷**（SMC + context save/restore），單次大概數百 cycle。

如果你寫的 TA 不頻繁切換，幾乎沒性能損失。但每秒幾千次切（例如想用 secure world 做加密 streaming）會變 hot path 瓶頸。設計時要考慮 batching、shared memory。

## Cortex-M 也有 TrustZone：TrustZone-M

ARMv8-M（Cortex-M23/M33）加了 **TrustZone for ARMv8-M**，把 MCU 也分 secure / non-secure：

- 不用分 EL，直接 secure / non-secure mode
- 用 SAU / IDAU 做 memory partition
- 配 secure / non-secure stack pointer

NXP / Microchip / ST 等的 secure MCU 都用這個（NXP LPC55、Microchip ATSAML11、STM32L5）。

設計目標：**讓嵌入式裝置也能有「不可改的 boot loader / 密鑰儲存」**，IoT 安全升級。

## 自我檢核

- [ ] 我能畫出 secure / non-secure 兩個 world 與 EL 對照
- [ ] 我能解釋 NS bit 怎麼影響 memory access
- [ ] 我能說出 SMC 是什麼以及 SMCCC 的 ABI
- [ ] 我能說出 ARM TF-A 的 BL1/2/31/32/33 各做什麼
- [ ] 我能描述 OP-TEE 在系統中的位置
- [ ] 我能列出一個 TrustZone 已知漏洞或攻擊面

下一章看 ARM 的虛擬化擴展 — EL2、stage-2 translation、KVM 怎麼用這個。

→ [Ch 22 虛擬化擴展：EL2 與 stage-2 translation](./22-virtualization-el2.md)
