# Ch 8 — 進入 long mode（64-bit）

> **目標**：把 CPU 從 32-bit protected mode 推進到 64-bit long mode——理解 long mode 為何強制分頁、建立 4 級頁表（PML4/PDPT/PD/PT）、設定 CR4.PAE、EFER.LME、CR0.PG，並用 64-bit GDT entry far jump 進 64-bit。這是本課第二個、也是最難的 assembly 高峰。

> **環境**：nasm，QEMU（需 64-bit CPU），gdb。承接 Ch 7（protected mode、GDT、模式切換模式）。原理深挖章。

## 為什麼 long mode 比 protected mode 難切？

切到 protected mode（Ch 7）你只要設 GDT + CR0.PE + far jump。切到 long mode 多了一個硬要求：**long mode 強制開啟分頁（paging）**——你必須在切換前建立好頁表。

```
protected mode：分頁可選（我們用 flat segmentation，沒開分頁）
long mode：    分頁強制（CR0.PG 必須=1，且要有合法的 4 級頁表）
        │
  所以切 long mode = 先建頁表 + 設一堆控制暫存器 bit + far jump
  比切 protected mode 多了「建頁表」這個大工程
```

這章是本課最難的 assembly——你要親手建立 x86-64 的 4 級頁表，理解虛擬位址如何透過頁表翻譯成物理位址。搞懂它，你就理解了現代 64-bit OS 記憶體管理的硬體基礎。

## 先建立直覺：long mode 用 4 級頁表翻譯位址

```
long mode 的位址翻譯（虛擬 → 物理）：

  64-bit 虛擬位址（實際只用 48 bits）
        │  拆成 4 個 9-bit 索引 + 12-bit offset
        ▼
  PML4（第 4 級）─┐
        │ index   │ 每級是一張表，512 個 entry
        ▼         │ 每個 entry 指向下一級的表
  PDPT（第 3 級）─┤
        │         │
        ▼         │
  PD（第 2 級）  ─┤
        │         │
        ▼         │
  PT（第 1 級）  ─┘ 最後一級的 entry 指向實際的物理 page（4KB）
        │
        ▼
  物理位址 = page 基址 + offset
```

x86-64 用 4 級頁表（four-level paging）。每級是一張有 512 個 entry 的表，虛擬位址被切成幾段，每段當一級表的索引，一路查下去到物理位址。我們開機時只要建一個最小的頁表——把前面一段記憶體 **identity map**（虛擬位址 = 物理位址），讓 CPU 能繼續執行。

## Identity Mapping：開機時最簡單的頁表

開機切 long mode 時，我們不需要複雜的虛擬記憶體——只要 CPU 切換後還能執行同一段 code。所以用 **identity mapping**：虛擬位址 = 物理位址。

```
Identity Map：虛擬 0x1000 → 物理 0x1000（一對一）
        │
  這樣切到 long mode 後，CPU 用虛擬位址執行，
  但虛擬位址 = 物理位址，所以還是執行原本的 code
        │
  最小做法：identity map 前 2MB（用一個 huge page 或一級 PT）
  足夠 boot code 繼續跑
```

## 建立 4 級頁表

我們在記憶體某處（如 0x1000）建立 4 級頁表，每級一張表，串起來，identity map 前 2MB：

```asm
; 假設頁表放在 0x1000 起（每張表 4KB，512 個 8-byte entry）
; PML4 在 0x1000, PDPT 在 0x2000, PD 在 0x3000
; 用 2MB huge page，所以 PD 直接指向 2MB 物理頁（省一級 PT）

setup_paging:
    ; 先清空頁表區域（0x1000 ~ 0x4000，3 張表）
    mov edi, 0x1000
    mov cr3, edi            ; CR3 = 頁表根（PML4 的物理位址）= 0x1000
    xor eax, eax
    mov ecx, 4096 * 3 / 4   ; 清 3 張表（PML4/PDPT/PD），以 dword 為單位
    rep stosd               ; 把 [edi] 清 0，edi += 4，重複 ecx 次
    mov edi, cr3            ; edi 回到 0x1000

    ; PML4[0] → PDPT（0x2000）
    mov dword [edi], 0x2000 | 0b11   ; 0x2000（PDPT 位址）+ present(1) + writable(2)
    add edi, 0x1000         ; edi = 0x2000（PDPT）

    ; PDPT[0] → PD（0x3000）
    mov dword [edi], 0x3000 | 0b11   ; 0x3000 + present + writable
    add edi, 0x1000         ; edi = 0x3000（PD）

    ; PD[0] → 2MB huge page（物理 0x0），用 huge page bit
    mov dword [edi], 0b10000011      ; present(1) + writable(2) + huge page(0x80)
                                     ; base = 0（identity map 物理 0 ~ 2MB）
    ret
```

逐段解說：

- **CR3** = PML4 的物理位址（頁表的根）。CPU 翻譯位址從 CR3 指向的 PML4 開始
- 每個 entry 是 64-bit，低位是 flag（present、writable、huge page...），高位是下一級表（或物理頁）的位址
- `present(0b1)`：這個 entry 有效
- `writable(0b10)`：可寫
- `huge page(0x80)`：PD entry 設這個 bit，表示直接映射 2MB（不用再下一級 PT），省事

> 我們用 **2MB huge page** 簡化——PD 的 entry 直接指向 2MB 物理頁，省掉第 1 級 PT。這讓開機頁表只需要 3 張表（PML4/PDPT/PD），identity map 前 2MB 夠 boot code 跑。正式 OS 會建完整的 4 級表做細粒度映射，但開機階段 2MB identity map 就夠。

## 切換步驟：比 protected mode 多三個 bit

```
real → protected（Ch 7）+ long mode 的額外步驟：

  （已在 protected mode 或從 real mode 一路上來）
  1. 建立頁表（上面做的）
  2. 設 CR4.PAE = 1     ← Physical Address Extension（long mode 必須）
  3. 設 CR3 = PML4 位址  ← 頁表根
  4. 設 EFER.LME = 1    ← Long Mode Enable（在 EFER MSR）
  5. 設 CR0.PG = 1      ← 開啟分頁（這一刻進 long mode）
  6. 用 64-bit code selector far jump
        │
  比 protected mode 多：PAE、CR3、EFER.LME、PG
```

```asm
; 假設已建好頁表（setup_paging 跑過，CR3 已設）
enter_long_mode:
    ; 2. 設 CR4.PAE
    mov eax, cr4
    or eax, 1 << 5          ; CR4 bit 5 = PAE
    mov cr4, eax

    ; 3. CR3 已在 setup_paging 設好（= 0x1000）

    ; 4. 設 EFER.LME（EFER 是 MSR 0xC0000080）
    mov ecx, 0xC0000080     ; EFER MSR 編號
    rdmsr                   ; 讀 MSR 到 edx:eax
    or eax, 1 << 8          ; EFER bit 8 = LME (Long Mode Enable)
    wrmsr                   ; 寫回

    ; 5. 設 CR0.PG（開分頁）和確保 PE
    mov eax, cr0
    or eax, 1 << 31         ; CR0 bit 31 = PG (Paging)
    or eax, 1 << 0          ; CR0 bit 0 = PE (確保 protected enable)
    mov cr0, eax            ; ← 這一刻進入 long mode（IA-32e compatibility）

    ; 6. far jump 用 64-bit code selector
    jmp CODE_SEG_64:long_mode_start

bits 64
long_mode_start:
    ; 設 data segment（long mode 大部分 segment 被忽略，但還是設一下）
    mov ax, DATA_SEG_64
    mov ds, ax
    mov es, ax
    mov ss, ax

    ; 現在在 64-bit long mode！可以用 rax/rbx... 64-bit 暫存器
    ; 印字（直接寫 video memory，64-bit 定址）
    mov rdi, 0xb8000
    mov rax, 0x0f000f000f000f00   ; 屬性
    ; ... 印字 code ...
    hlt
    jmp $
```

## 64-bit 的 GDT entry

long mode 需要 64-bit 的 code segment descriptor（和 protected mode 的 32-bit 不同）：

```asm
; long mode 的 GDT（簡化——long mode 大部分忽略 base/limit）
gdt64:
    dq 0                    ; NULL descriptor
gdt64_code:                 ; 64-bit code segment
    ; long mode code segment：limit/base 被忽略，重點是 L bit（64-bit）
    dq (1<<43) | (1<<44) | (1<<47) | (1<<53)
    ; bit 43 = executable, bit 44 = descriptor type (code/data),
    ; bit 47 = present, bit 53 = L (long mode / 64-bit code)
gdt64_data:
    dq (1<<44) | (1<<47) | (1<<41)  ; data segment

gdt64_descriptor:
    dw $ - gdt64 - 1
    dq gdt64

CODE_SEG_64 equ gdt64_code - gdt64
DATA_SEG_64 equ gdt64_data - gdt64
```

> long mode 的 segment descriptor 大幅簡化——base 和 limit 基本被忽略（long mode 的記憶體保護全靠分頁）。關鍵是 **L bit（bit 53）**：設了表示這是 64-bit code segment。這反映了 x86-64 的設計：long mode 放棄了 segmentation 的記憶體保護（保留 segment 只為相容），全用 paging。Ch 7 的 flat segmentation 到 long mode 變成「segmentation 幾乎不存在」。

## 完整的三模式接力

把 Ch 6-8 串起來，這是 BIOS 線從開機到 64-bit 的完整接力：

```
BIOS 載入 boot sector 到 0x7C00（16-bit real mode）
        │ Ch 6：搭舞台、印字
        ▼
real mode → protected mode（Ch 7）
        │ cli, 開 A20, lgdt, CR0.PE, far jump
        ▼
32-bit protected mode
        │ Ch 8：建頁表
        ▼
protected mode → long mode（Ch 8）
        │ CR4.PAE, CR3, EFER.LME, CR0.PG, far jump (64-bit selector)
        ▼
64-bit long mode ← 現在能跑 64-bit OS / kernel
```

這個三段接力（real → protected → long）是 x86-64 BIOS 開機的核心。每段都是「設好控制暫存器 + far jump」的模式，long mode 多了「建頁表」。

## 用 gdb 觀察進入 long mode

```bash
qemu-system-x86_64 -drive format=raw,file=boot.img -s -S &
gdb
```

```gdb
(gdb) target remote localhost:1234
# ... 一路單步到 enter_long_mode ...
(gdb) p/x $cr4           # 設 PAE 前後看 bit 5
(gdb) p/x $cr3           # 頁表根位址
# 設 CR0.PG 那一刻
(gdb) si
(gdb) p/x $cr0           # bit 31 (PG) 變 1
(gdb) set architecture i386:x86-64   # 切 gdb 到 64-bit 解讀
(gdb) si                 # far jump 後
(gdb) info registers     # 看 64-bit 暫存器（rax, rbx...）
(gdb) x/4i $pc           # 確認在執行 64-bit 指令
```

## 故意弄壞：忘記建頁表就設 CR0.PG

```asm
; 錯誤：沒建頁表（CR3 指向垃圾或 0），直接設 PG
mov eax, cr0
or eax, 1 << 31         ; set PG
mov cr0, eax            ; ← CPU 開分頁，但 CR3 指向的頁表是垃圾
                        ;   第一次取指令就找不到合法映射 → triple fault
```

long mode 強制分頁。設 CR0.PG 時 CPU 立刻開始用 CR3 指向的頁表翻譯位址。如果頁表沒建好（CR3 指向垃圾，或 identity map 沒涵蓋當前 code 的位址），CPU 翻譯失敗 → page fault → 因為沒有 handler → triple fault → QEMU reset。

```bash
qemu-system-x86_64 -drive format=raw,file=boot.img -d int -no-reboot
# 沒建頁表會看到 triple fault
```

這是 long mode 比 protected mode 多的陷阱——分頁強制，頁表沒建好就 set PG 必死。

## 踩雷集錦

1. **沒建頁表就設 CR0.PG**：long mode 強制分頁，CR3 沒指向合法頁表，set PG 立刻 triple fault。先建頁表

2. **identity map 沒涵蓋當前 code**：頁表只 map 了某段，但 boot code 的位址不在那段，set PG 後取指令 page fault。確保 identity map 涵蓋 code 所在位址

3. **順序錯（PAE/LME/PG）**：必須 PAE → CR3 → LME → PG。例如 LME 要在 PG 之前設（先宣告「要進 long mode」再開分頁）

4. **用 32-bit GDT entry 跳 long mode**：long mode 需要設了 L bit（bit 53）的 64-bit code segment。用 protected mode 的 32-bit entry far jump 會失敗

5. **頁表 entry 的 flag 漏 present**：每個用到的 entry 都要設 present bit（0b1），否則 CPU 認為該 entry 無效，page fault

6. **頁表沒對齊**：頁表必須 4KB 對齊（位址低 12 bit 為 0）。放在非對齊位址，CPU 解讀錯誤

## 進階：long mode 的記憶體模型革新

long mode 不只是「64-bit 暫存器」，它重新設計了記憶體模型：

```
long mode 的設計決策：
  1. 放棄 segmentation 保護（CS/DS 的 base/limit 大多忽略）
     → 為什麼？segmentation 是 1980 年代的記憶體保護，
       笨重且和現代 OS 的 paging 重複。x86-64 趁機砍掉
  2. 強制 paging（48-bit 虛擬位址，4 級頁表）
     → 所有保護和虛擬記憶體統一用 paging
  3. 16 個通用暫存器（real/protected 只有 8 個）
     → 多的 r8-r15 讓編譯器優化更好
        │
  long mode = x86 趁 64-bit 轉換「丟掉歷史包袱」的機會
  （但 real/protected mode 還在，為了相容開機和舊軟體）
```

x86-64 是 AMD 設計的（AMD64），Intel 後來跟進。AMD 的關鍵設計選擇是「64-bit 模式趁機簡化」——砍掉 segmentation 保護、強制 paging、加暫存器。這讓 long mode 比 protected mode 「乾淨」，但 CPU 還是要支援 real/protected mode（開機和相容）。理解這個，你會懂為什麼開機要爬三個模式——每個模式是 x86 不同年代的產物，疊在一起。

## 動手練習

1. 跑通完整的三模式接力（real → protected → long），在 64-bit long mode 印字到 video memory。用 gdb 觀察 CR4.PAE、CR3、EFER.LME、CR0.PG 依序變化

2. 用 gdb 在進 long mode 後，檢查頁表：`x/8gx 0x1000`（看 PML4）、`x/8gx 0x2000`（PDPT）、`x/8gx 0x3000`（PD），確認 entry 的 flag 和指向

3. 故意弄壞：跳過 setup_paging（不建頁表）直接 set CR0.PG，用 `-d int -no-reboot` 看 triple fault

4. 進階：改成 identity map 前 4MB（用兩個 2MB huge page，PD 設兩個 entry），驗證能 map 更大範圍

## 本章重點整理

- long mode 強制分頁（CR0.PG=1 且要合法頁表）——這是它比 protected mode 多的大工程
- x86-64 用 4 級頁表（PML4/PDPT/PD/PT）；開機用 identity map（虛擬=物理）+ 2MB huge page 簡化
- 切換步驟：建頁表 → CR4.PAE → CR3 → EFER.LME(MSR) → CR0.PG → 64-bit selector far jump
- long mode 的 GDT entry 靠 L bit（bit 53）標記 64-bit；base/limit 被忽略（保護全靠 paging）
- 三模式接力（real→protected→long）是 x86-64 BIOS 開機核心；long mode 趁 64-bit 砍掉 segmentation 包袱

## 自我檢核

- [ ] 能解釋為什麼切 long mode 必須先建頁表（強制分頁）
- [ ] 能畫出 4 級頁表的查詢流程（PML4→PDPT→PD→PT→物理頁）
- [ ] 知道 identity mapping 是什麼、開機為什麼用它
- [ ] 能不看範本說出切 long mode 的步驟（PAE/CR3/LME/PG/far jump）
- [ ] 能解釋 long mode 為什麼放棄 segmentation 保護（設計簡化）

## 延伸閱讀

### 官方文件

- **[Intel SDM Vol 3, Ch 4 (Paging)](https://www.intel.com/sdm)** 和 Ch 9.8.5（Long Mode 初始化）
  - **讀哪裡**：4.5（4-level paging）、9.8.5（IA-32e mode initialization）
  - **學什麼**：頁表結構、進 long mode 的官方步驟；本章是教學版
  - **前提**：本章 + Ch 7

- **[OSDev Wiki: Setting Up Long Mode](https://wiki.osdev.org/Setting_Up_Long_Mode)** 和 **[Paging](https://wiki.osdev.org/Paging)**
  - **讀哪裡**：兩個條目，特別是頁表建立的完整 code
  - **學什麼**：long mode 切換和頁表的實作細節、所有 flag bit
  - **前提**：本章

### 部落格 / 文章

- **[Writing a Bootloader Part 3](https://3zanders.co.uk/2017/10/18/writing-a-bootloader3/)** — Alex Parker
  - **這篇說什麼**：實作進 long mode、建頁表的完整過程
  - **讀哪裡**：整篇
  - **為什麼值得讀**：和本章互補，code 完整可跑

- **[os.phil-opp.com: Entering Long Mode](https://os.phil-opp.com/entering-longmode/)** — Philipp Oppermann
  - **這篇說什麼**：用 Rust 但對 long mode、頁表的概念解釋極清晰
  - **讀哪裡**：paging 和 long mode 那幾節
  - **為什麼值得讀**：概念圖示清楚，跨語言通用

→ [Ch 9 兩階段 bootloader 與磁碟讀取](./09-two-stage-bootloader.md)
