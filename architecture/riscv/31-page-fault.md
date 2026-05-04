# Ch 31 — Page Fault 處理：Trap 流程、Load / Store / Instruction Page Fault 分類

> 目標：能分辨 RISC-V 的三種 page fault；知道 stval 在 page fault 時包含什麼；能畫出 Linux 的 do_page_fault 簡化流程；理解 demand paging 和 COW 的基本機制。

---

## 31.1 三種 Page Fault Exception

RISC-V spec 定義三種 page fault：

| Exception 名稱                | scause 值 | 觸發條件                                 |
|-----------------------------|---------|----------------------------------------|
| Instruction page fault      | 12      | 取指（fetch）時 VA 翻譯失敗                |
| Load page fault             | 13      | load 指令（lb/lh/lw/ld 等）VA 翻譯失敗    |
| Store/AMO page fault        | 15      | store/AMO 指令 VA 翻譯失敗               |

**和 Access Fault 的差別**：

```
Page fault（cause 12/13/15）：
  頁表查找失敗
  原因：V=0、權限不足（R/W/X/U）、address not canonical

Access fault（cause 1/5/7）：
  物理地址存取違反 PMA（Physical Memory Attribute）或 PMP
  原因：存取了不存在的物理記憶體、PMP 保護
  page fault 優先於 access fault（先走頁表，走完才到 PMP）
```

---

## 31.2 Page Fault 時的 Trap CSR 內容

發生 page fault 時，以下 CSR 被硬體自動設定：

```
CSR       值
-----     ---
scause    page fault 的 cause code（12/13/15）
sepc      觸發 fault 的指令地址（VA）
stval     引起 fault 的虛擬地址（不是 PC！）
sstatus   SPIE = 原來的 SIE；SPP = 原來的 privilege level
```

`stval` 的值：
- Instruction page fault：fault VA = stval（通常等於 sepc，除非分支預測等情況）
- Load/Store page fault：stval = 觸發 fault 的記憶體存取 VA

---

## 31.3 完整 Page Fault Trap 流程

```
使用者程式存取 VA 0x7FFF_0000
      |
      v
 MMU 走頁表
      |
   翻譯失敗（PTE.V=0，或無寫入權限）
      |
      v
 硬體自動：
   sepc  = 觸發 fault 的指令 PC
   scause = 13（load pf）或 15（store pf）
   stval = 觸發 fault 的 VA（0x7FFF_0000）
   sstatus.SPIE = sstatus.SIE，sstatus.SIE = 0（關中斷）
   sstatus.SPP = U（原來的 mode）
   PC = stvec（S-mode trap vector）
      |
      v
 S-mode trap handler 執行
   讀 scause：是 page fault
   讀 stval：fault VA = 0x7FFF_0000
   讀 sepc：哪條指令觸發
      |
      v
 do_page_fault()（Linux kernel）
      |
   +--+-- 是 kernel 地址？→ 別的處理路徑
   |
   v
 find_vma(fault_va)
      |
   找不到 VMA？→ send SIGSEGV
      |
   找到 VMA
      |
   權限檢查（VMA 的 vm_flags vs fault type）
      |
   dispatch：
   +-----→ demand paging（V=0，lazy alloc）
   +-----→ copy-on-write（write fault on read-only page）
   +-----→ swap-in（page 被換到磁碟）
      |
      v
 建立/修改 PTE，填入新的 PPN
 sfence.vma（刷新 TLB）
      |
      v
 sret：回到使用者程式，重新執行觸發 fault 的指令
```

---

## 31.4 Demand Paging（惰性配置）

Linux 的 mmap/malloc 不是呼叫時就分配物理記憶體，而是：

1. 只建立 VMA（Virtual Memory Area）結構，記錄這段 VA 的權限和 backing store
2. 頁表裡留 V=0 的 PTE（或根本不建立 PTE）
3. 第一次存取時觸發 page fault
4. Fault handler 分配物理 page，建立 PTE，然後重試指令

```c
// 使用者 malloc(4096)
// 只分配了 VMA，沒有物理記憶體

char *p = malloc(4096);
// 此時 *p 對應的 PTE 是 invalid 的

p[0] = 1;
// Load/Store page fault 觸發
// kernel 分配 4 KiB page frame
// 建立 PTE：V=R=W=A=D=1
// sret，p[0] = 1 重新執行，成功
```

---

## 31.5 Copy-on-Write（寫時複製）

Fork 時，parent 和 child 共享所有 page，但把 PTE 的 W bit 設為 0：

```
fork() 後：
  Parent PTE：V=R=W=X=0→R only（W=0），PPN=X
  Child  PTE：V=R=W=X=0→R only（W=0），PPN=X（同一個物理 page）

child 嘗試寫入：
  Store page fault（因為 W=0）
  scause = 15，stval = fault VA
  handler 確認是 COW page
  分配新的 page frame Y，複製 page X 的內容到 Y
  更新 child 的 PTE：PPN=Y，W=1
  sret，寫入重新執行

parent 的 PTE：PPN=X，W 還是 0 直到它也寫入
  （此時才把 parent 的 PTE 也改回 W=1）
```

---

## 31.6 Nested Page Fault

如果 S-mode page fault handler 自己又觸發了 page fault：

```
硬體沒有自動嵌套的能力。
S-mode 的 sstatus.SIE 在進入 handler 時被硬體清為 0（interrupt 關閉），
但 exception 還是可以發生。

如果在 S-mode handler 裡又 page fault：
  sepc/scause/stval 被覆蓋（！原來的值丟失了！）
  又跳回 stvec（同一個 handler 入口）

Linux 的做法：
  kernel 在 page fault handler 裡存取使用者空間時，用 fixup table：
  如果這次存取 fault，跳到 fixup code，不遞迴進入 handler
  （arch/riscv/mm/extable.c）
```

這是為什麼 kernel 不能隨便 dereference 使用者指標——要用 `copy_from_user` 等有 fixup 保護的函式。

---

## 31.7 實際 scause 值對照（完整）

```
cause code   async?  描述
----------   ------  -----------------------
0            N       Instruction address misaligned
1            N       Instruction access fault
2            N       Illegal instruction
3            N       Breakpoint (ebreak)
4            N       Load address misaligned
5            N       Load access fault
6            N       Store/AMO address misaligned
7            N       Store/AMO access fault
8            N       Environment call from U-mode (ecall)
9            N       Environment call from S-mode
11           N       Environment call from M-mode
12           N       Instruction page fault     ← 這章主角
13           N       Load page fault            ← 這章主角
15           N       Store/AMO page fault       ← 這章主角
```

---

## 自我檢核

- [ ] 能說出三種 page fault 的 scause 值（12/13/15）
- [ ] 知道 stval 在 page fault 時包含什麼（fault VA，不是 PC）
- [ ] 能說出 page fault 和 access fault 的差別（頁表 vs PMA/PMP）
- [ ] 能描述 demand paging 的流程（lazy alloc，fault 時建 PTE）
- [ ] 能描述 COW 的基本機制（W=0，寫時複製 page）

→ [Ch 32 — 64 位元 CSR 行為](32-64bit-csr.md)
