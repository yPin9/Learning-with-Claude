# Ch 23 — ARM64 kernel pwn 差異速查：PAC / MTE / Android kernel 生態

> 目標：x86-64 的 mental model 拿到 ARM64 哪些還成立、哪些不成立。PAC（Pointer Authentication）、MTE（Memory Tagging Extension）、Android kernel 的 GKI / KMI 生態。不教 Android 提權，只給差異對照。

## 這章的用途

kernelCTF **主賽道是 x86-64**，不碰 Android。但你遲早會：
- 讀到用 ARM64 kernel 的 CTF 題
- 在 ARM64 機器上做研究
- 碰到 "Android kernel pwn" 的縮寫術語（PAC、MTE、GKI）

這章是速查表，不是教程。

---

## 暫存器與呼叫慣例對照

| | x86-64 | ARM64 (AArch64) |
|---|---|---|
| function args | rdi, rsi, rdx, rcx, r8, r9 | x0-x7 |
| return value | rax | x0 |
| stack pointer | rsp | sp |
| frame pointer | rbp | x29 (fp) |
| link register | （return address on stack） | x30 (lr) |
| IP | rip | pc |
| syscall nr | rax | x8 |
| syscall entry | `syscall` | `svc #0` |
| scratch regs | rax-r11（部分） | x0-x18 |
| callee-saved | rbx, rbp, r12-r15 | x19-x28, x29, x30 |

**最重要的差異**：ARM64 有 link register（`x30`），function call 時 return address 存在 `x30` 裡，不 push 到 stack（對應 leaf function）。非 leaf function 才把 `x30` push 到 stack。所以 stack frame layout 和 x86-64 不同。

---

## SMEP / SMAP 在 ARM64 的對應

| 保護 | x86-64 | ARM64 |
|---|---|---|
| 阻止 ring 0 執行 user page | SMEP（CR4 bit） | PXN（Privileged Execute-Never，PTE bit） |
| 阻止 ring 0 存取 user VA | SMAP（CR4 bit） | PAN（Privileged Access Never，SCTLR_EL1 bit） |

操作：
- 開 SMAP → `stac`/`clac` 在 x86；ARM64 沒有 `clac` 等效物，kernel 用 `uaccess_enable_not_uao` / `uaccess_disable` 等宏管理 PAN。
- 開 SMEP → PXN bit 在 page table 裡，ARM64 不能臨時關掉。

---

## ROP / Gadget 差異

ARM64 gadget 比 x86-64 少，原因：
- ARM64 是 fixed-width 32-bit instruction（x86-64 是 variable-width 1-15 bytes）。你不能像 x86 那樣從 instruction 中間開始當 gadget — 每個 gadget **必須是合法 instruction 邊界**。
- 有效 gadget 數量大幅減少。

ARM64 常見 ROP 路線：

```asm
; 用 ldp（load pair）做 stack pivot
ldp x0, x1, [sp], #0x10   ; pop x0, x1
ldp x2, x3, [sp], #0x10
; ...
ldp x29, x30, [sp], #0x10 ; pop fp, lr
ret                        ; jump to x30
```

`ldp xN, xM, [sp], #N` 系列是 ARM64 ROP 的主力。

---

## PAC（Pointer Authentication Code）

PAC 是 ARMv8.3 引入的硬體功能，在 pointer 的高 bit 裡藏一個 cryptographic signature。

```
64-bit pointer：
 [63:56] = PAC（由 key + pointer value + context 生成）
 [55:0]  = 實際地址（TTBR0/1 管轄範圍）
```

Kernel 用 PAC 保護：
- **return address**（`x30`）：function return 前 `autiasp` 驗 x30 的 PAC；如果你 overwrite x30 但沒有正確 PAC，驗證失敗 → fault。
- **kernel pointer**（部分）：kernel object 的 function pointer 可以被 pac 保護。

**PAC bypass**：
- **leak PAC key**（DA / IA key 等）：每個 process 一組 key，洩漏後可以偽造 signature。但 key 在 system register，無法直接讀，要靠 speculative execution 或 side channel。
- **reuse signed pointer**：如果你能 copy 一個已有 PAC 的 pointer（而不是偽造），可以繞過驗證。這叫 **pointer spoofing** vs **PAC forgery**。
- **沒有開 PAC**：並非所有 ARM64 kernel 都開 PAC（kernelCTF LTS 的 x86-64 主賽道無關）。

---

## MTE（Memory Tagging Extension）

ARMv8.5 的功能。每個 16-byte 的記憶體區塊（chunk）有一個 4-bit tag；pointer 的高 bits（bit 59:56）存 tag。load/store 時 hardware 比對 pointer tag 和 memory tag，不一致 → fault（`SEGV_MTESERR`）。

```
ptr tag:  ptr[59:56]  (4 bits)
mem tag:  memory tag for 16B chunk (stored in tag RAM)
access: if ptr_tag != mem_tag → tag fault
```

**MTE 對 kernel pwn 的影響**：
- Heap UAF 被擋：free 後，allocator 會改掉那個 chunk 的 memory tag；你 dangling pointer 的 ptr tag 和新 memory tag 不符 → access fault。
- 無法「自然地」觸發 UAF，需要先 bypass MTE。
- **MTE bypass 方法**（研究中）：leak memory tag（某些 side channel），或找不受 MTE 保護的 object（vmalloc 不受 MTE）。

---

## Android kernel 生態速查

| 術語 | 意思 |
|---|---|
| GKI（Generic Kernel Image） | Google 維護的 Android kernel，手機 OEM 用這個基底 |
| KMI（Kernel Module Interface） | GKI 保證的 ABI，OEM 的 driver module 靠這個 ABI 不改 |
| LTS base | GKI 基於 upstream LTS kernel（5.10, 5.15, 6.1...） |
| Android Security Bulletin | Google 每月發布的 Android 安全公告，CVE 在這裡 |
| Pixel-specific patch | Pixel 手機額外的 patch，不在 AOSP |

**kernelCTF 不是 Android 賽道**：kernelCTF 打的是 upstream Linux kernel（x86-64）。如果你看到有人把 kernelCTF 和 Android 混在一起，多半是搞錯了。

---

## x86-64 → ARM64 差異速查表

| 主題 | x86-64 | ARM64 |
|---|---|---|
| return address | push to stack | x30 (link register) |
| gadget 豐富度 | 高（variable-width insn） | 低（fixed 32-bit insn） |
| SMEP 等效 | CR4.SMEP bit | PXN PTE bit |
| SMAP 等效 | CR4.SMAP bit | PAN / SCTLR_EL1 |
| stack canary | 在 stack frame 裡 | 同 x86，但位置可能不同 |
| KASLR | 同 | 同（KIMG_OFFSET） |
| KPTI | 是 | 是（用 TTBR0_EL1 switching） |
| ROP pivot | `mov rsp, rdx; ret` | `ldr x8, [x0]; blr x8` 等 |
| ops hijack | tty_struct->ops (kmalloc-1024) | 同物件，不同 gadget |
| PAC | 無 | ARMv8.3+，kernel 上保護 x30 |
| MTE | 無 | ARMv8.5+，保護 heap access |
| Shadow Call Stack | 無（x86 用 KCFI 補） | 是（`-mbranch-protection=pac-ret+bti`）|

---

## 動手練習

1. **找 ARM64 gadget**：下載一個 ARM64 vmlinux，用 `ROPgadget --binary vmlinux --rop` 搜 `ldp x29, x30`，比較和 x86-64 vmlinux 的 gadget 數量差異。
2. **讀 PAC 指令**：ARM64 Manual Vol D（AArch64 System Register），找 `PACIA`、`AUTIA`、`PACIASP` 的 encoding，理解 `autiasp` 怎麼驗 return address。
3. **在 QEMU ARM64 跑你的 kernel**：下載 ARM64 的 `linux-image-arm64` 或自己 cross-compile，在 QEMU `virt` 機器上跑，確認基本 exploit 環境可用。
4. **驗 PAN**：在 ARM64 QEMU kernel 裡，寫一個 kernel module 嘗試 dereference user-space pointer，確認 KERN_ERR "Unhandled fault: Privileged Access Never" 被觸發。
5. **讀 Android Security Bulletin**：挑最近一期，找三個 kernel 相關的 CVE，確認它們在哪個子系統（kernel driver / nf_tables / ...）和對應的 GKI kernel 版本。

## 自我檢核

- [ ] 能說出 ARM64 的 function arg 暫存器（x0-x7）和 x86-64 的差異
- [ ] 知道 ARM64 link register（x30）如何影響 ROP 路線
- [ ] 知道 PXN / PAN 分別對應 x86 的哪個保護
- [ ] 能解釋 PAC 的高 bit 存放位置和驗證機制
- [ ] 知道 MTE 怎麼讓 UAF 失效（memory tag mismatch）
- [ ] 知道 GKI 和 kernelCTF 的關係（kernelCTF 是 upstream x86-64，不是 Android）

→ [Ch 24 — kernelCTF 賽制與流程](./24-kernelctf-overview.md)
