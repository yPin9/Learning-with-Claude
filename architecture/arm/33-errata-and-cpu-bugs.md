# Ch 33 — ARM 的 CPU bug 與 errata 史

> 目標：看 ARM 的「CPU 也會 bug」歷史。Cortex-A53 errata、Spectre / Meltdown 對 ARM 的影響、Apple A 系列補丁、kernel 怎麼處理 errata workaround。理解 errata 不是「ARM 的恥辱」是 hardware 工程必然。

## 什麼是 errata

**errata** = 已知的硬體 bug 或意外行為，廠商承認、文件化、提供 workaround。每一顆 Cortex 核心都有自己的 errata document。

```
Errata document 結構（典型）：
  Title: ARM-Cortex-A53 Software Developers Errata Notice
  Revision: A.0
  ─────────────────────────────────────────────────
  Errata #819472:  Cache Maintenance Operations may
                   fail to invalidate cache entries
    Affected core revisions: r0p0, r0p1, r0p2
    Workaround: 
      Boot loader 配 SCTLR_EL1.WXN before MMU enable...
  
  Errata #835769:  Static Mispredict in BTAC...
    Affected: r0p0–r0p4
    Workaround:
      kernel patch sequence: ...
```

每條 errata 標 **affected revisions**（哪幾批晶片有這 bug）、**workaround**（軟體怎麼避開）。

## 為什麼 errata 那麼多

CPU 是極複雜系統：

- **Cortex-A78 約 1.5B transistor**
- **超過 1000 個 verification engineer-year 工作量**
- **microarchitecture 與 ISA 的互動極多**

完美驗證做不到。**errata 是工程現實**，不是 ARM / Intel / AMD 的失職。Intel 的 errata 文件動輒上百條 — 比 ARM 還多。

## 經典 ARM errata 案例

### Cortex-A53 #835769：static mispredict

ARM Cortex-A53（樹莓派 3 / 多數 entry-level Android）某些 revision 有：

**症狀**：特定情況下 branch predictor 永遠 mispredict（性能損失）。

**Workaround**：kernel 在受影響範圍前後加 NOP 序列、或避免特定 instruction sequence。

linux/arch/arm64/kernel/cpu_errata.c：

```c
const struct arm64_cpu_capabilities arm64_errata[] = {
    {
        .desc = "ARM erratum 835769",
        .capability = ARM64_WORKAROUND_843419,
        .matches = is_affected_midr_range,
        .midr_range = MIDR_RANGE(...),
        .cpu_enable = enable_workaround,
    },
    ...
};
```

kernel 開機檢查 CPU MIDR，若是受影響 chip 自動啟用 workaround code path。

### Cortex-A53 / A57 #843419：ADRP overflow

某些 instruction sequence 使 ADRP 計算錯誤位址。

**Workaround**：linker 偵測這個 pattern、自動插入 NOP 或重新排列。GNU ld 的 `--fix-cortex-a53-843419` flag 開啟。

### Cortex-A72 / A73 cache coherency

特定 race condition 下，多核 cache coherency 失敗，可能讀到 stale data。

**Workaround**：cache maintenance 操作後加 DSB barrier。

## Spectre / Meltdown：跨架構的災難

2018 年公布的 Spectre / Meltdown 影響 Intel、AMD、ARM 等多家。**ARM 的影響取決於 microarchitecture**：

| Variant | 影響 ARM |
|---|---|
| Spectre v1 (bounds check bypass) | 大部分 OoO ARM core |
| Spectre v2 (branch target injection) | 大部分 OoO 核 |
| Meltdown (rogue data cache load) | Cortex-A75、A72 一些 |

ARM 公布 list：<https://developer.arm.com/Arm%20Security%20Center/Speculative%20Processor%20Vulnerability>

**Workaround**：

- **CSDB / SSBB / PSSBB**：ARMv8.0 加的 speculative barrier 指令
- **firmware-side mitigation**：BL31 提供 SMC 給 OS 呼叫
- **kernel KPTI-like**：page table isolation

對性能影響：**5-15%** 在某些 workload。Linux ARM64 自 4.16 起內建 mitigation。

## 後續變種：Spectre-BHB、Spectre-RSB

- **Spectre-BHB（branch history buffer）**：2022，Cortex-A 多家受影響
- **Spectre-RSB（return stack buffer）**：return address speculation 攻擊

ARM 文件持續更新 mitigation。Cortex-A720 / Neoverse V2 等加了 hardware mitigation。

## Apple A 系列：自家錯誤的故事

Apple 自家設計核（A6 起），雖然不公開 errata，但有時揭露：

- **A11 (iPhone X)**：發布後幾個月做 silicon spin 修一些 bug（沒對外宣布細節）
- **M1 (2020)**：early adopter 發現某些 kernel-level bug，Apple 透過 SecurityUpdate 修
- **M3 PAC bypass (2024)**：學術論文 PACMAN 顯示 PAC 在某些情境可被 spectre-style attack bypass

Apple 的 errata 處理大多透過 OS / firmware update 默默修，不像 ARM 公開文件。**但機制本質一樣 — silicon 不完美，靠 software 補**。

## 寫嵌入式 firmware 怎麼處理 errata

1. **看 chip vendor 的 application note**：ST、NXP、Microchip 等常出 「Errata Sheet for STM32F4xx」之類
2. **CMSIS device header 內常有 workaround**：`SystemInit()` 內可能有 magic register write 解某 errata
3. **bootloader / firmware 階段做 一次性 workaround**：例如 cache maintenance、dummy memory access

**自家 SoC firmware bring-up 必讀 errata sheet**，否則 random bug 會找你麻煩。

## 一些現實 errata：踩過才會記得

- **STM32 USB enumeration race**：某些 batch USB 訊號上電 race，需要 firmware 延遲 init 50 µs 後手動 reset
- **Allwinner H6 boot ROM hang**：特定 SD card 開機 hang，要先 erase partition table
- **Raspberry Pi 4 USB-C bug（早期 batch）**：某些 USB-C 充電器拒絕，硬體 PCB bug，後續 batch 修了

**硬體不完美是常態**，學會找 erratum 是工程師的關鍵技能。

## 看 errata 的工具：MIDR

每顆 ARM 核有 **MIDR (Main ID Register)** 標記 implementer / part / revision：

```asm
mrs   x0, midr_el1
; 解析：
;  bits[31:24] Implementer (ARM=0x41, Apple=0x61)
;  bits[23:20] Variant
;  bits[19:16] Architecture
;  bits[15:4]  Part number
;  bits[3:0]   Revision
```

對應 errata sheet 的 `r1p2` 之類（variant 1, revision 2）。Linux `dmesg` 或 `/proc/cpuinfo` 印 MIDR — 你可以對照 errata document 看哪些影響你。

## 一個常見誤解

「erratum workaround 是不是會被惡意者利用？」

**有時候可以**。Cortex-A 的某些 errata 在沒打 workaround 的 firmware 下變成 attack vector。例如：

- Spectre 變種沒打 patch → 資訊洩漏
- 某些 coherency bug → 多核 data race 利用

定期更新 firmware、kernel、BL31 patch 是基本紀律。

## 自我檢核

- [ ] 我能說出 errata 是什麼以及為什麼會存在
- [ ] 我能舉一個經典 Cortex-A53 errata 並說明 workaround
- [ ] 我能解釋 Spectre / Meltdown 對 ARM 的影響範圍
- [ ] 我能讀 MIDR 找出我手上的 chip revision
- [ ] 我能說出 ARM-software/arm-trusted-firmware 何時提供 errata mitigation
- [ ] 我能比較 Apple、ARM、Intel 在 errata 處理透明度上的差異

下一章看怎麼讀 Arm Architecture Reference Manual — 那本 9000 頁的怪物書。

→ [Ch 34 怎麼讀 Arm Architecture Reference Manual](./34-reading-arm-arm.md)
