# Ch 17 — ASID、TLB、context switch

> 目標：搞懂 TLB 怎麼運作、ASID 怎麼讓 process 切換不用 flush TLB、TLB invalidate 指令家族、什麼時候必須 flush。Linux kernel 的 mm context switch 細節都在這。

## TLB：page table walk 的 cache

```
CPU 算 VA → 查 TLB (1 cycle)
  hit  → 拿到 PA，繼續
  miss → 走 page table（10–30 cycle）→ 填回 TLB
```

TLB 容量小（每核幾百到上千 entry），但對 hot path 命中率 > 99%。**TLB miss 是 ARM 性能殺手之一**：每次 miss 至少多走幾級 page table，cache 不熱還會觸發更多 cache miss。

## ASID：Address Space ID

如果 TLB 只記 `VA → PA`，**process 切換時所有 entry 都不能用**（不同 process 有不同 mapping），必須 flush。

ARM 的解法：給每個 process 一個 **ASID**（Address Space ID），TLB entry 變成 `(ASID, VA) → PA`。Process A 的 mapping 不會被 process B 看見，**不用 flush** 就能正確區分。

```
TLB entry:
  ASID    VA       PA      attr
  ─────  ──────   ──────   ────
  0x12   0x1000   0xABC    RW
  0x12   0x2000   0xDEF    RW
  0x34   0x1000   0x123    RW   ← 同 VA，不同 ASID，不同 PA
  0x34   0x2000   0x456    RW
```

CPU 查 TLB 時用 **目前的 ASID（在 TTBR0 高 16 bit）+ VA** 一起比，命中才算數。

## ASID 寬度

- **ARMv8 預設 ASID 8-bit**（256 個）
- **ARMv8.0 起可選 16-bit**（65536 個）

```c
// 設定 16-bit ASID（TCR_EL1.AS = 1）
uint64_t tcr = ...;
tcr |= (1UL << 36);           // AS bit
asm volatile("msr tcr_el1, %0" :: "r"(tcr));
```

Linux 多數平台用 16-bit，因為 ASID 不夠時要 wrap-around 並 flush，越多越省 flush。

## ASID 設定：寫到 TTBR

ASID 不是獨立 register，是**塞在 TTBR 高位**：

```
TTBR0_EL1:
 63          48 47               0
┌────────────┬───────────────────┐
│   ASID     │   PA of L0 table  │
└────────────┴───────────────────┘
```

context switch 時：

```c
// 切換到 process X
uint64_t ttbr = process_X_pgdir | ((uint64_t)process_X_asid << 48);
asm volatile("msr ttbr0_el1, %0" :: "r"(ttbr));
asm volatile("isb");
```

**單一指令**就完成 page table 切換 + ASID 切換 + 不 flush TLB（如果 ASID 都還有效）。極快。

## Global vs non-Global pages

PT entry 有個 **nG (not Global) bit**：

- `nG = 0`：**Global page** — 所有 ASID 看得見（kernel 範圍用這個）
- `nG = 1`：**ASID-tagged** — 只有對應 ASID 看得見（user space 用這個）

Linux 慣例：
- TTBR1（kernel）的 PT entries 全 nG=0
- TTBR0（user）的 PT entries 全 nG=1

context switch 時 kernel mapping 不被 ASID 影響，永遠可達 — 進 syscall 不需要重抓 TLB。

## TLB invalidate 指令家族

當 mapping 改變（新 mmap、free page、unmap），必須通知 TLB 那條 entry 不再有效。

ARMv8 的 TLB invalidate 指令格式 `TLBI <op>{, <Xt>}`：

```asm
tlbi  vmalle1                ; invalidate ALL TLB entries for EL1
tlbi  alle1is                ; ALL EL1 entries Inner Shareable (multi-core)
tlbi  vae1is, x0             ; invalidate by VA, EL1, IS (x0 = VA)
tlbi  aside1, x0             ; invalidate by ASID
tlbi  vmalls12e1is           ; ALL stage1+stage2 entries
... 共 30+ 變體
```

幾個常用：

| 指令 | 範圍 |
|---|---|
| `tlbi vmalle1` | flush 全部 EL1 TLB（極狠，重啟才用） |
| `tlbi vae1is, x0` | 只 invalidate 一個 VA（最常用） |
| `tlbi aside1is, x0` | 只 invalidate 一個 ASID 的全部 |
| `tlbi alle2` | EL2 範圍 |

**`is` 字尾 = Inner Shareable**：這個 invalidate 廣播到同 cluster 的其他核心。多核 SMP 必須加 IS，不然其他核 TLB 還有舊 entry。

完整 invalidate sequence：

```asm
dsb  ishst        ; 確保 mmu 操作排序到位
tlbi vae1is, x0   ; flush 那個 VA
dsb  ish          ; 等 invalidate 完成
isb               ; 確保後續指令看到新 TLB
```

四條一組是經典 idiom。Linux kernel 的 `flush_tlb_page()` 大致就這樣。

## TTBR 切換並不 invalidate TLB

這個是初學者常踩的坑：**改 TTBR0 不會自動 flush 對應 TLB**。

正確做法：

```c
// 1. 先 invalidate 舊 ASID（如果要 reuse 此 ASID）
asm volatile("tlbi aside1, %0" :: "r"((uint64_t)old_asid << 48));

// 2. dsb + isb
asm volatile("dsb ish; isb");

// 3. 再切 TTBR0
asm volatile("msr ttbr0_el1, %0" :: "r"(new_ttbr));

// 4. ISB
asm volatile("isb");
```

或者用 ASID rolling — 不 invalidate 舊 ASID，把它標記「已死」，等 ASID 池用完再 flush 全部 + 重發 ASID。Linux 用這個策略。

## ASID rolling（Linux idiom）

ASID 池只有 65536 個，當所有都發完怎麼辦？

```
generation 0: ASID 1..65535 給 process A、B、C... 用完
generation 0 結束 → flush 全部 TLB → generation 1 開始 → 重新發 ASID
```

每個 process 記「我的 ASID 屬於 generation N」。context switch 時：

```
if process.asid_gen == current_gen:
    # 還能用，直接切
    set_ttbr0(process.pgdir | process.asid)
else:
    # 過期，分配新 ASID（如果池滿，bump generation 並 flush）
    process.asid = allocate_new_asid()
    set_ttbr0(process.pgdir | process.asid)
```

這個 trick 讓 65536 ASID 對「process 數量比 ASID 多但同時 active 的少」這個常見情境很省。

## DMA 與 TLB：不要 cache 寫了又 invalidate

DMA 寫一塊 buffer 後 CPU 來讀 — 不只是 cache 問題（Ch 18 講），TLB 通常**不影響** DMA。但若你動了 page table（重 map DMA buffer），就要 invalidate TLB 那段 VA。

實務上 DMA buffer 都長期 map 不變，TLB 沒問題，問題在 cache coherency（下一章）。

## 一個常見誤解

「ASID 多了會浪費資源吧？沒有 ASID 直接 flush 不簡單嗎？」

對 single-process 系統是。**但對多任務 OS，flush 全部 TLB 每次 context switch 是性能災難**：每次 flush 後最初幾百次 memory access 都 TLB miss、每個 miss 觸發 page table walk、每個 walk 多 4 次 cache access。Linux 在沒 ASID 的舊架構（ARMv6 部分）上 context switch 慢 3-5×。

ASID 是個微小成本（多幾位元 storage），換來巨大性能收益。x86 PCID 完全同概念。

## Linux ARM64 的 mm context switch（精簡）

```c
// arch/arm64/mm/context.c
void check_and_switch_context(struct mm_struct *mm) {
    u64 asid = atomic64_read(&mm->context.id);
    if (asid_generation_unchanged(asid)) {
        // 同 generation，直接切
        set_ttbr0(mm->pgd | asid);
    } else {
        // 過期，分配新 ASID
        asid = new_context(mm);
        atomic64_set(&mm->context.id, asid);
        set_ttbr0(mm->pgd | asid);
    }
}
```

實際 code 比這複雜得多（per-cpu lock、generation 升級、SMP coordination），但骨架就是 ASID gen + TTBR 切換。

## 自我檢核

- [ ] 我能解釋 ASID 的目的與如何避免 TLB flush
- [ ] 我能說出 ASID 在 TTBR 中的位置
- [ ] 我能寫一個正確的 TLB invalidate sequence（DSB+TLBI+DSB+ISB）
- [ ] 我能區分 IS 字尾與沒 IS 的差別
- [ ] 我能解釋 nG bit 的意義
- [ ] 我能說明 ASID rolling / generation 的 idea

下一章看 cache 階層、PIPT/VIPT、coherency、shareability domain。

→ [Ch 18 Cache 階層、coherency、shareable domain](./18-cache-and-coherency.md)
