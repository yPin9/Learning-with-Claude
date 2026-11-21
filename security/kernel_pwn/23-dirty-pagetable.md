# Ch 23 — Dirty Pagetable：PTE 覆寫拿任意實體記憶體 R/W

> **目標**：理解近幾年最強的 data-only 技術之一。學完你要能解釋：PTE（page table entry）長什麼樣、為什麼「改一個 PTE 的實體頁框號」就等於拿到**任意實體記憶體讀寫**、這條路為什麼幾乎不需要 leak kernel 位址、也完全繞過 CFI（它根本不碰控制流）。這是 [Ch 22](./22-cross-cache.md) 「往下一層找共用面」思維的極致——直接攻擊位址轉譯本身。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。依賴 [Ch 6](./06-kernel-memory-layout.md) 的 page table 與 physmap。本章以機制為主，未實測，理論預期行為。

## 為什麼 PTE 是終極目標

前面的技術都在搶「哪個物件和哪個物件相鄰/重疊」。Dirty Pagetable 換個角度問：**整個系統裡，哪個資料結構『說了算』地決定一塊記憶體能被誰讀寫？** 答案是 page table。

每個 process 的虛擬位址，最終都要經過 page table 翻譯成實體位址（[Ch 6](./06-kernel-memory-layout.md)）。最底層的 PTE 記著「這個虛擬頁對應哪個實體頁框、權限是什麼」。如果你能**改掉一個 PTE**，讓某個你 user-space 能存取的虛擬頁，指向一個**你指定的實體頁框**——那麼你 user-space 對那個虛擬位址的讀寫，就直接落到那個實體頁上。而透過 physmap（[Ch 6](./06-kernel-memory-layout.md)），kernel 的所有記憶體都有對應的實體頁。於是：

```
  改一個 PTE 的頁框號 → user 存取對應虛擬位址 → 讀寫任意實體頁 → 讀寫任意 kernel 記憶體
```

這是**最強的原語**（[Ch 10](./10-vulnerability-taxonomy.md) 的 arbitrary R/W 之上，還是「實體層」的任意讀寫），而且它的美妙在於：

- **幾乎不用 leak kernel 位址**：你操作的是實體頁框號（從 0 開始的小數字），不是 KASLR 後的虛擬位址。你可以掃實體記憶體找目標（如 cred、kernel text）。
- **完全繞 CFI**：從頭到尾沒有任何間接呼叫、沒有控制流轉移，CFI（[Ch 28](./28-cfi-kcfi.md)）無從施力。純資料。
- **繞 SMEP/SMAP**：你是透過合法的 user 虛擬位址存取，不是 kernel 去碰 user、也不是執行 user page。

## 先建立直覺：PTE 是「門牌轉接器」

```
  正常：user 虛擬頁 V ──PTE──▶ 實體頁框 P_normal（你自己的資料頁）
                            PTE = [ P_normal 的頁框號 | 權限 bits ]

  被弄髒後：
        user 虛擬頁 V ──PTE'──▶ 實體頁框 P_target（你指定的、別人的頁）
                            PTE' = [ P_target 的頁框號 | 權限 bits ]

  現在 *(V) 讀寫的是 P_target —— 可以是另一個 process 的頁、
  可以是 physmap 裡某塊 kernel 資料、可以是存著 cred 的頁
```

PTE 就是一個「門牌轉接器」：V 這個門牌本來轉接到你家，改掉轉接目標，V 就轉接到任何一間房。你不需要鑰匙（不需權限、不需 leak），因為你是從**合法的門牌 V**（你自己的虛擬位址，本來就有讀寫權）進去的，kernel 只看 PTE 說 V 對應哪裡。

## 機制：怎麼「弄髒」一個 PTE

page table 本身也是記憶體——PTE 存在一個 **page table page**（PTL，page table level 的頁），而 page table page 也是 kernel 配置的頁。Dirty Pagetable 的核心是**讓你的 heap 原語改寫到一個 page table page 上的 PTE**。兩條主要路徑：

```
路徑 A：UAF 讓「page table page」和「可控物件」重疊
  1. 釋放某塊記憶體
  2. 誘導 kernel 把那塊拿去當 page table page（例如 mmap 一塊記憶體
     觸發 page table 配置，剛好用到那塊）
  3. 你還有 dangling 指標 / 可控物件疊在上面
     → 你寫「可控物件」= 寫到 PTE = 改頁框號

路徑 B：overflow / cross-cache 蓋到相鄰的 page table page
  用 cross-cache（Ch 22）讓你的可控物件和 page table page 落同一塊 page 層區域，
  overflow 蓋到 PTE
```

共通點：**把你的寫原語導向「某個 PTE 的位元組」**。改掉 PTE 的高位（頁框號部分），保留權限 bits（要可讀寫、present），然後從 user-space 存取對應的虛擬位址。

## 拿到任意實體 R/W 之後做什麼

有了「讀寫任意實體頁」，提權的路很多，且都是 data-only：

- **找並改 cred**：掃實體記憶體找你自己 process 的 `cred`（可用已知 pattern / 自己先寫 magic 進 cred 再掃），把 uid 寫 0（[Ch 16](./16-classic-primitives.md)）。
- **改 kernel text 附近的資料**：改 `modprobe_path` 等全域字串（[Ch 16](./16-classic-primitives.md)），但要先定位它的實體位址。
- **改另一個高權限 process 的記憶體**。

最常見是改 cred，因為它不需要 kernel text base——你在實體空間掃自己的 cred，改 uid，純資料。

> 「掃實體記憶體找 cred」聽起來慢，實務上有技巧：先在自己 cred 裡放可辨識的 pattern（例如特定的 uid/gid 組合或 capability），再掃；或利用 cred 在實體記憶體的分布規律縮小範圍。細節依版本，未實測。

## 為什麼它是 FREELIST_HARDENED / CFI 時代的寵兒

把 Dirty Pagetable 和前面的 hardening 對一遍：

| mitigation | 為什麼擋不住 Dirty Pagetable |
|---|---|
| FREELIST_HARDENED | 不改 freelist 指標，改的是 PTE |
| CFI / KCFI | 沒有任何間接呼叫/控制流轉移 |
| SMEP / SMAP | 透過合法 user 虛擬位址存取，非 kernel 碰 user |
| KASLR | 操作實體頁框號，不太需要 kernel 虛擬位址 leak |
| random kmalloc caches | 配合 cross-cache 在 page 層操作 |

幾乎把所有主流 mitigation 都繞過了——這就是為什麼它在 2023 之後的 kernelCTF 這麼紅。它的代價和 cross-cache 一樣：**不穩**（要精準讓你的寫落到 page table page 上），且需要對 page table 配置時機有掌握。但一旦成立，威力無人能比。

## 踩雷集錦

1. **改 PTE 時把權限 bits 也蓋掉**——PTE 不只有頁框號，還有 present/writable/user 等 bits。只能改頁框號部分，權限要保持「present + 可讀寫 + user 可存取」，否則存取那個虛擬位址直接 fault。
2. **忘了頁框號和實體位址的換算**——PTE 存的是頁框號（physical address >> 12），不是實體位址本身。要寫「目標實體位址 / PAGE_SIZE」到頁框欄位，不是直接寫位址。
3. **誘導不出「那塊被拿去當 page table page」**——路徑 A 的關鍵是讓 kernel 把你釋放的那塊拿去當 PTL。這需要觸發 page table 配置（mmap 大量頁、特定 fault 模式），且時機對。沒誘導對，你的寫落在普通頁上，不是 PTE。
4. **以為不用任何 leak**——「幾乎不用」不是「完全不用」。你可能仍需要知道一些實體佈局規律、或先 leak 一個實體位址當錨點。別以為零資訊就能掃到 cred。
5. **掃實體記憶體碰到未映射區域 panic**——亂掃實體頁框，碰到保留區 / MMIO / 未映射的頁會出事。要在合理的實體範圍內掃，並對讀到的內容做 sanity check。

## 進階：Dirty Pagetable 與它的近親

「攻擊位址轉譯 / 共用底層」這條線上還有幾個相關技術，一起理解：

- **Dirty COW（CVE-2016-5195）**：更早的、針對 copy-on-write 競爭的經典，讓 user 寫到唯讀映射的檔案頁。它是 race（[Ch 40](./40-race-conditions.md)）不是 PTE 覆寫，但精神相通——都在玩「映射與實體頁的關係」。
- **Dirty Pipe（CVE-2022-0847）**：利用 `pipe_buffer` 的 flags 寫到不該寫的 page cache 頁。也是「資料層改映射關係」。
- **Dirty Cred（[Ch 24](./24-dirty-cred.md)）**：下一章，換掉 cred 物件本身而非改 PTE，另一種 data-only 路。

這些「Dirty 家族」共享一個世界觀：**不劫控制流，改的是『誰能存取哪塊記憶體』的那層關係。** Dirty Pagetable 是其中最通用、最強的——因為 page table 是所有存取的最終仲裁者。讀懂它，你就理解了 data-only 攻擊的天花板在哪。

決定用不用它：當你有一個能寫到 page 層的強原語（UAF/overflow + cross-cache），而且目標環境 mitigation 全開（CFI、FREELIST_HARDENED、random caches 都在），Dirty Pagetable 往往是唯一還走得通的路。反之，環境寬鬆時用更簡單的 data-only（改 cred 直接）就好，別為了用它而增加不穩定性。

## 動手練習

在自有 QEMU 靶上（機制觀察為主）：

1. 用 gdb 看一個 user mmap 的頁對應的 PTE：找出 PTE 的位址、解析它的頁框號與權限 bits，手動算「頁框號 × PAGE_SIZE = 實體位址」驗證。
2. 觀察 page table page 的配置：mmap 一大塊、觸發缺頁，看 kernel 配了哪些 PTL（可用 `/proc/<pid>/pagemap` 或 gdb）。理解「什麼操作會讓 kernel 配 page table page」。
3. 分析題：給一個「UAF 於某 cache」，寫出你用 Dirty Pagetable 的計畫——怎麼誘導釋放塊變成 PTL、改哪個 PTE、改成指向哪、之後怎麼掃 cred。重在流程與「保留權限 bits」的細節。
4. 對照：把同一個 bug 改用「直接改 cred」（若 cred 可達）的路線，比較兩者對 mitigation 的敏感度與穩定性。

## 本章重點整理

- Dirty Pagetable 攻擊位址轉譯本身：改一個 PTE 的頁框號 → user 存取對應虛擬位址 → 任意實體記憶體讀寫 → 任意 kernel 記憶體。
- 幾乎不需 leak kernel 虛擬位址（操作實體頁框號）、完全繞 CFI（無控制流）、繞 SMEP/SMAP（走合法 user 位址）。
- 機制：讓寫原語落到一個 page table page 的 PTE 上（UAF 讓釋放塊變 PTL，或 cross-cache + overflow）。
- 改 PTE 只改頁框號、保留權限 bits；頁框號 = 實體位址 >> 12。
- 幾乎繞過所有主流 mitigation，代價是不穩；是「Dirty 家族」data-only 攻擊的最強代表。

## 自我檢核

- [ ] 能解釋為什麼「改一個 PTE」等於拿到任意實體記憶體讀寫
- [ ] 能說出 Dirty Pagetable 為什麼幾乎不用 leak kernel 位址、為什麼繞 CFI
- [ ] 能描述「讓釋放塊被拿去當 page table page」這一步的意義與難處
- [ ] 能說出改 PTE 時哪些 bits 要保留、頁框號和實體位址怎麼換算
- [ ] 能把 Dirty Pagetable 放進「Dirty 家族 / data-only」的脈絡，說出它和 Dirty COW/Pipe 的共通世界觀

## 延伸閱讀

- **[Dirty Pagetable: A novel exploitation technique（Nassim Asrir / 原始 writeup）](https://yanglingxi1993.github.io/dirty_pagetable/dirty_pagetable.html)**
  - **這篇說什麼**：提出並系統化 Dirty Pagetable，含 PTE 覆寫與實體掃描的完整流程。
  - **讀哪裡**：整篇；PTE 操作與「怎麼讓釋放塊變 PTL」兩段最關鍵。
  - **為什麼值得讀**：本章機制的原始來源，把時機與細節講到可實作。
- **[Linux page table 與 PTE 格式（Documentation / arch/x86/include/asm/pgtable_types.h）](https://elixir.bootlin.com/linux/v6.6/source/arch/x86/include/asm/pgtable_types.h)**
  - **讀哪裡**：PTE 的 bit 定義（`_PAGE_PRESENT`、`_PAGE_RW`、`_PAGE_USER`、頁框號欄位）。
  - **和本章的關聯**：本章「保留權限 bits、只改頁框號」的 source 依據。
  - **前提知識**：Ch 6 的 page table 階層。
- **[CVE-2022-0847 Dirty Pipe 分析（Max Kellermann 原始 writeup）](https://dirtypipe.cm4all.com/)**
  - **這篇說什麼**：Dirty 家族的另一代表，pipe_buffer flags 寫 page cache。
  - **讀哪裡**：整篇；對照本章「Dirty 家族共享的世界觀」。
  - **為什麼值得讀**：理解 data-only 攻擊「改映射關係」的另一種形狀。

Dirty Pagetable 改的是「位址轉譯」。下一章換一個 data-only 目標：直接換掉 credential 物件本身——Dirty Cred，用物件生命週期的錯位把低權限 cred 換成高權限 cred。

→ [Ch 24 — Dirty Cred / file struct：不經 ROP 的 data 攻擊](./24-dirty-cred.md)
