# Ch 14 — MPU（Cortex-M 版）

> 目標：搞懂 Cortex-M 的 Memory Protection Unit — region-based 而非 page-based、為什麼嵌入式即時系統選這個、怎麼用 MPU 做 RTOS task 隔離。MPU 不是 MMU，這章特別注意兩者差異。

## MPU vs MMU：兩個世界

```
                MPU (Cortex-M)              MMU (Cortex-A)
            ──────────────────          ──────────────────
單位         Region (任意大小)            Page (4 KB / 16 KB / 64 KB)
數量         8–16 個 region              百萬級 page entries
位址轉換     沒有！VA = PA               有，VA → PA
TLB         沒有                         有，三級走查
適合         hard real-time, 確定性       通用、虛擬記憶體
複雜度       簡單，配 register 就好      複雜，需 page table
切換成本     幾條指令                   TLB invalidate 可能很貴
```

**MPU 不做位址轉換**：CPU 看到的位址 = 實體位址。MPU 只「**檢查**」這個位址能不能存取（read / write / execute / privilege）。

## 為什麼 Cortex-M 不要 MMU？

- **可預測 latency**：MMU TLB miss 觸發 page table walk，最壞 case 幾百 cycle，hard real-time 不接受
- **省成本**：MMU 硬體面積很大（佔晶片面積），MCU 用不上
- **不需要虛擬記憶體**：bare-metal / RTOS 沒 swap、沒 fork、沒 dynamic loader

R profile（Cortex-R）也是用 MPU，理由相同。

## ARMv7-M 的 MPU 規格

Cortex-M3 / M4 的 MPU：

- **8 個 region**（高階如 M7、M33 可到 16 個）
- 每個 region 設定：起始位址、大小、屬性（read/write/execute、cacheable、bufferable）
- **region 大小必須是 2 的次方且 ≥ 32 bytes**
- **region 起始位址必須對齊到自身大小**（256-byte region 要 256-byte 對齊）

ARMv8-M（Cortex-M23/33）改了規格：region 大小 32-byte 對齊、不必 2 的次方。但本章主要以 v7-M 為例。

## 設一個 MPU region

```c
// 把 0x20000000 開始 4 KB 設成 R/W、不可執行、給 user 讀
ARM_MPU_SetRegion(
    0,                                    // region number
    ARM_MPU_RBAR(0, 0x20000000),         // base address + region number
    ARM_MPU_RASR(
        0,                                // XN: 0 = executable, 1 = no
        ARM_MPU_AP_FULL,                  // R/W from privileged & unprivileged
        ARM_MPU_ACCESS_NORMAL(            // memory type
            ARM_MPU_CACHEP_WB_WRA,        // cacheable: write-back, write-and-read-allocate
            ARM_MPU_CACHEP_WB_WRA,
            1                              // shareable
        ),
        0,                                // SRD (sub-region disable)
        ARM_MPU_REGION_SIZE_4KB,           // 大小
        1                                  // ENABLE
    )
);
```

CMSIS 的 macro 醜，但比手寫 register 安全。

## Sub-region：32 等分

每個 region 可拆成 8 個 sub-region，每個獨立 enable/disable：

```
region size 8 KB
  sub 0: 0x00000000 - 0x000003FF (1 KB)
  sub 1: 0x00000400 - 0x000007FF
  ...
  sub 7: 0x00001C00 - 0x00001FFF
```

**只有 region size ≥ 256 bytes 時才能用 sub-region**。設定 SRD bitfield 8 bit，1 = disable 對應 sub。

用途：**讓 region 形狀不規則** — 例如 8 KB region 中只 protect 中間 1 KB。

## Overlap 與 priority

兩個 region 可以重疊；**region number 大的優先**（後設定的覆蓋前設定）。

idiom：region 0 設成「整個記憶體都不能存取」（baseline 拒絕），region 1+ 開特定地方的權限：

```c
// Region 0: cover everything, no access
setup_region(0, 0x00000000, 0xFFFFFFFF, NO_ACCESS);
// Region 1: flash, RX
setup_region(1, FLASH_BASE, FLASH_SIZE, RX);
// Region 2: SRAM, RW
setup_region(2, SRAM_BASE, SRAM_SIZE, RW);
// Region 3: 給 task 0 的 stack
setup_region(3, task0_stack_base, 1024, RW);
```

Task switch 時改 region 3 即可切 task 的 protection。

## MPU 的記憶體屬性

每個 region 的 **TEX、C、B、S** bits 決定 memory type：

| Type | 用途 |
|---|---|
| Strongly-ordered | MMIO 周邊（嚴格順序） |
| Device | MMIO 周邊（可緩衝） |
| Normal, non-cacheable | RAM 但不 cache |
| Normal, write-through | RAM cache write-through |
| Normal, write-back | RAM cache write-back |

**錯誤後果**：把 MMIO 設成 cacheable → 你寫 register 沒生效（在 cache 裡）；把 RAM 設成 strongly-ordered → 性能巨差但不會壞。

CMSIS 提供常用組合 macro：

```c
ARM_MPU_ACCESS_NORMAL(ARM_MPU_CACHEP_WB_WRA, ARM_MPU_CACHEP_WB_WRA, 1)
ARM_MPU_ACCESS_DEVICE(0)            // shareable / non-shareable
ARM_MPU_ACCESS_ORDERED()
```

## Access Permission：8 種選擇

| AP | privileged | unprivileged |
|---|---|---|
| 000 | no access | no access |
| 001 | RW | no access |
| 010 | RW | RO |
| 011 | RW | RW |
| 100 | reserved | reserved |
| 101 | RO | no access |
| 110 | RO | RO |
| 111 | RO | RO |

加上 XN bit（execute never），組合出讀/寫/執行各種 protection。

## 啟用 MPU

設完所有 region 後：

```c
// 開 MPU，背景 region disabled（region 0 沒設就拒絕）
ARM_MPU_Enable(0);

// 開 MPU，背景 region enabled（沒被任何 region cover 的位址用預設 system control）
ARM_MPU_Enable(MPU_CTRL_PRIVDEFENA_Msk);
```

**`PRIVDEFENA`**（Privileged Default Enable）很常被誤用：設 1 表示「privileged code 對沒被 region cover 的位址有預設存取權」。寫 RTOS 通常想要這個（kernel code 不被自己 protect 卡死），但給 user task 的 MPU 配置時要小心—不要洩漏 kernel 區到 user。

## 觸發違規：MemManage_Handler

存取被 MPU 拒絕 → 觸發 **MemManage exception**。Handler:

```c
void MemManage_Handler(void) {
    uint32_t mmfsr = SCB->CFSR & 0xFF;          // MemManage status
    uint32_t mmfar = SCB->MMFAR;                // 違規位址（如有效）

    if (mmfsr & 0x01) /* IACCVIOL: instr access violation */;
    if (mmfsr & 0x02) /* DACCVIOL: data access violation */;
    if (mmfsr & 0x80) /* MMARVALID: MMFAR valid */;

    /* 通常 panic 或 kill task */
    while (1);
}
```

`SCB->CFSR`（Configurable Fault Status Register）細分 MemManage / BusFault / UsageFault 的原因。debug 時這幾個 register 是黃金（Ch 27 會深入）。

## Cortex-M MPU 的真實用途

1. **NULL pointer protection**：region 0 設 `0x00000000-0x000000FF` 為 no access。寫 NULL 立刻 fault，不會默默踩記憶體。
2. **Task stack overflow**：在 task stack 後面留個小 unmapped region。stack 衝過頭就 fault。
3. **Privileged kernel / unprivileged task**：user task 跑在 Unprivileged + 受 MPU 限制；kernel 跑 Privileged + PRIVDEFENA。
4. **MMIO protection**：保護周邊 register 不被普通 code 寫
5. **TrustZone-M**（M23/M33）：MPU + Secure region 達成嵌入式 TEE

## 限制與痛點

- **8 個 region 不夠**：複雜系統（FreeRTOS + 5 個 task + MMIO 區）很快用完
- **2 的次方限制**：把 task stack 設 2 KB region 容易，設 1.5 KB 就要拆
- **對齊限制**：linker script 要把 stack 對齊 region 大小，麻煩
- **每次 task switch 改 region**：成本不大但要記得做

ARMv8-M 的 MPU 改善了一部分（任意大小、32-byte 對齊），但 region 數仍受限。

## 一個常見誤解

「沒 MMU 是不是就沒辦法做 isolation？」

可以做，**但是有限**。MPU 提供「task 之間不能互踩記憶體」「user 不能 access kernel code」等基本 isolation，足夠 RTOS 做 process model。但**沒有虛擬記憶體（VA→PA mapping）**，所以：

- 不能做 fork / mmap / shared memory（ELF loader 級別的功能）
- 多個 task 看的是同一塊 PA，不能各自有獨立位址空間
- copy-on-write 等技術沒戲

要做這些就要用 Cortex-A 的 MMU。Part 3 開講。

## 自我檢核

- [ ] 我能說出 MPU 與 MMU 的關鍵差異
- [ ] 我能設一個 4 KB R/W region
- [ ] 我能解釋 sub-region 的用途
- [ ] 我能比較 region overlap 時哪個贏
- [ ] 我能寫 NULL pointer protection 用的 region 0
- [ ] 我能解釋 PRIVDEFENA 的兩種設定意義

到這裡 Part 2 chapter 結束。下一個是練習 A — 拿 STM32（或 QEMU mps2-an385）寫一個無 HAL 的 bare-metal 韌體，把 reset / vector table / linker / NVIC / SysTick / MPU 全部串起來。

→ [練習 A：STM32 bare-metal 韌體](./practice-a-stm32-baremetal.md)
