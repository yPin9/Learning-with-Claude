# Ch 6 — kernel 記憶體全景：virtual memory layout、physmap/direct map、vmalloc、page table

> **目標**：看懂 x86-64 kernel 的虛擬位址空間長什麼樣子、每一塊是幹什麼的，特別是搞懂 **direct map（physmap）** 為什麼是「拿到一個實體位址讀寫原語，就等於拿到整個 kernel 的任意虛擬位址讀寫」的橋樑。這是 Part 3 的 Dirty Pagetable、USMA 系列技巧的地基。

> **環境**：以 Linux 6.6 LTS、x86-64、4-level paging（48-bit 虛擬位址）為基準。5-level paging（57-bit，`CONFIG_X86_5LEVEL`）的差異會標注。頁表與記憶體配置相關行為請以你自己 QEMU 裡跑的 kernel 版本的 `Documentation/arch/x86/x86_64/mm.rst` 為準——這份文件本身就是 kernel 原始碼附帶的權威表格。

## 為什麼需要這個？

Part 0 你已經寫過第一個 vulnerable module，也劫過一次控制流。但到目前為止我們都在「拿到一個 bug」之後直接想「怎麼打」，沒有停下來問：**kernel 的記憶體到底長什麼樣子？** 這個問題不是學術好奇，是因為後面每一個進階技巧都會在某個時刻問你：

- 我 leak 到的這個位址，是 heap？是 kernel text？是 stack？它告訴我什麼？
- KASLR 把什麼東西隨機化了？我 leak 一個位址，能推出多少其他位址？
- Dirty Pagetable 技巧說「改一個 PTE 就能任意實體讀寫」——這句話的因果關係到底是什麼？
- 為什麼 user-space heap exploitation 不太討論「實體位址」，但 kernel exploitation 幾乎每篇 writeup 都在算 physical address？

答案全部要回到 kernel 的虛擬位址空間布局。user-space 程式設計師很少需要理解 virtual memory 的全貌，因為 glibc 和 kernel 把這層抽象藏得很好——`malloc()` 回來一個指標，你不會去想它映射到哪個 page frame。但在 kernel 裡，**attacker 的目標就是記憶體本身**：commit_creds 要寫的是哪個虛擬位址、prepare_kernel_cred 要讀的是哪個 struct、PTE 要改的是哪個 physical frame。不理解這張地圖，後面每一章都只是在抄 exploit 骨架，抄到一個新版本行為不一樣就卡住。

## 先建立直覺

把一台跑 Linux 的 x86-64 機器想成一棟摩天大樓，每個「樓層」是 1 個 virtual address 的範圍：

```
高樓層 ─────────────────────────────────  0xffffffffffffffff
        kernel 專用區（只有 ring 0 能走進去）
        ├─ 模組程式碼
        ├─ kernel 自己的程式碼（kernel text）
        ├─ 每個 CPU 的私有小房間（cpu_entry_area）
        ├─ 一大片「整個實體記憶體的影子」（direct map）
        └─ vmalloc 區：東拼西湊、不連續的虛擬記憶體
        ──────────────── 中間一大段完全空白（non-canonical hole）────────────────
低樓層 ─────────────────────────────────  0x0000000000000000
        user-space 專用區（每個 process 自己的 128 TB）
```

關鍵的直覺是：**同一台實體機器的同一塊 RAM，可能同時被映射在好幾個不同的虛擬位址上**。你的 user-space heap chunk，實體上躺在某個 physical frame；而這個 physical frame，同時也被映射進 kernel 的 direct map 區。你從 user-space 寫入的那個 byte，從 kernel 的 direct map 位址去讀，讀到的是一模一樣的東西。這不是 bug，是設計——kernel 需要能直接碰到所有實體記憶體（複製 user buffer、管理 page cache），而不用每次都臨時建一條映射。

這個「同一塊 RAM，兩個窗口都看得到」的事實，就是 Ch 23 Dirty Pagetable 的全部基礎：如果你能讓某個 PTE 指向你選的 physical frame，你就等於打開了第三個窗口——而且是你完全控制的窗口。

## x86-64 虛擬位址空間總覽

### Canonical address 與 48-bit / 57-bit

x86-64 的 CPU 實際上只用 48 個 bit 定址虛擬記憶體（4-level paging），但指標是 64-bit 的。CPU 規定：bit 47 到 bit 63 必須全部相同（要嘛全 0 要嘛全 1），這種位址叫 **canonical address**。不符合這個規則的位址，CPU 連 TLB 都不會查，直接 `#GP`（general protection fault）。

這就產生了一個天然的「楚河漢界」：

- `0x0000_0000_0000_0000` ~ `0x0000_7fff_ffff_ffff`：高位全 0 → user space，128 TB
- `0xffff_8000_0000_0000` ~ `0xffff_ffff_ffff_ffff`：高位全 1 → kernel space，128 TB
- 中間 `0x0000_8000_0000_0000` ~ `0xffff_7fff_ffff_ffff`：**non-canonical hole**，任何程式存取這段位址都會直接 fault

這就是為什麼你在 crash log 裡常看到 `general protection fault, probably for non-canonical address 0x...`——那通常代表某個指標被你（或 bug）寫壞了，壞成了一個落在這個洞裡的值。

5-level paging（`CONFIG_X86_5LEVEL=y`，CPU 支援 LA57）把定址範圍拉到 57-bit，user/kernel 各自變成 64 PB，分界線也往外推。本章後面的具體位址表格以 4-level 為準；5-level 的對應關係結構相同，只是每個區塊都變大、起始位址不同。**你的機器用哪一種，看 `cat /proc/cpuinfo | grep la57`；大多數 2024 年前的消費級 CPU 還是 4-level。**

### kernel space 內部怎麼切

kernel 自己的 128 TB（或 64 PB）又切成幾塊，各司其職。下表是 4-level paging、`CONFIG_RANDOMIZE_MEMORY`（kernel 記憶體 KASLR）**關閉**時的預設布局，抄自 kernel 原始碼附帶的 `Documentation/arch/x86/x86_64/mm.rst`（這份文件在你自己的 kernel tree 裡就能找到，是這張表的第一手來源）：

| 虛擬位址範圍 | 大小 | 用途 |
|---|---|---|
| `0xffff888000000000` ~ `0xffffc87fffffffff` | 64 TB | **direct map（physmap）**：線性映射所有實體記憶體 |
| `0xffffc90000000000` ~ `0xffffe8ffffffffff` | 32 TB | **vmalloc / ioremap 區** |
| `0xffffea0000000000` ~ `0xffffeaffffffffff` | 1 TB | **vmemmap**：`struct page` 陣列的虛擬映射區 |
| `0xfffffe0000000000` ~ `0xfffffe7fffffffff` | 大範圍保留 | **cpu_entry_area**：每個 CPU 私有的固定映射（含 per-cpu stack、GDT 等） |
| `0xffffffff80000000` ~ `0xffffffffa0000000` | 512 MB | **kernel text mapping**：kernel image 本身（`_text` ~ `_end`），映射到實體位址 0 附近 |
| `0xffffffffa0000000` ~ `0xfffffffffeffffff` | ~1.5 GB | **module mapping space**：`insmod` 載入的模組程式碼 |

> **這張表不是你要背的東西，是你要會查的東西。** 真正重要的是記住每塊「是什麼」「為什麼存在」，具體位址在你自己的 kernel 上用 `cat /proc/kallsyms`、`dmesg | grep -i "virtual kernel memory"`（如果你的 config 有開這段 log）或直接讀 `Documentation/arch/x86/x86_64/mm.rst` 去對照——而且 KASLR 開著的話，大部分這些 base 在每次開機都不一樣（下一節講）。

### 為什麼這些區塊要分開？

這是個值得停下來問的設計問題：為什麼不乾脆讓所有 kernel 記憶體共用一套映射方式?

- **direct map 要「線性」**：它的目的是讓 `phys_addr → virt_addr` 變成一個簡單加法（`virt = phys + page_offset_base`），這樣 `__va()` / `__pa()` 這兩個 kernel 裡到處在用的轉換巨集才能是 O(1) 的指標運算，不用查表。這代價是它必須一次性映射「所有」實體記憶體，不能按需求切碎。
- **vmalloc 要「不連續」**：kernel 經常需要一塊「虛擬上連續，但實體上東拼西湊」的記憶體（例如一次要 4 MB 但實體記憶體已經碎片化，buddy allocator 給不出連續 4 MB 的 physical frame）。這種需求不可能用線性映射解決，所以要獨立出一塊可以自由建 page table 的區域。
- **vmemmap 要「和 struct page 一一對應」**：每個 physical page 都有一個 `struct page` 描述它（Ch 7 細講），vmemmap 讓這些 struct page 也能用一個簡單公式從 page frame number 算出虛擬位址，加速 `pfn_to_page()` / `page_to_pfn()`。
- **kernel text 獨立一塊**：因為它要能被 KASLR 單獨隨機化、要能設成唯讀 + 不可執行寫入（`W^X`），和一般資料記憶體的權限需求不同。

## 範例：親手判斷一個位址是不是 canonical

光知道規則不夠，寫個小工具跑過邊界值，才會真正刻進直覺裡。這是一個純 user-space 的小工具，不需要 kernel module，純粹驗證「canonical address」的位元規則：

```c
// canonical_check.c
#include <stdio.h>
#include <stdint.h>

static int is_canonical(uint64_t addr, int bits)
{
    /* bits = 48（4-level）或 57（5-level）：
       位址的 bit[bits-1 .. 63] 必須全部相同 */
    uint64_t sign_bit = (addr >> (bits - 1)) & 1;
    uint64_t upper = addr >> (bits - 1);
    uint64_t expected = sign_bit ? ((1ULL << (64 - (bits - 1))) - 1) : 0;
    return upper == expected;
}

int main(void)
{
    struct { uint64_t addr; const char *label; } cases[] = {
        {0x0000000000000000ULL, "NULL"},
        {0x00007fffffffffffULL, "user space 最高位址（4-level）"},
        {0xffff888000000000ULL, "direct map 典型起點"},
        {0xffffffffffffffffULL, "全 1（kernel 最高位址）"},
        {0x0000800000000000ULL, "非法：剛好跨進 non-canonical hole"},
        {0xdeadbeef00000000ULL, "常見的『看起來像壞掉的指標』測試值"},
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int ok48 = is_canonical(cases[i].addr, 48);
        printf("0x%016llx  %-40s  canonical(48-bit)=%s\n",
               (unsigned long long)cases[i].addr, cases[i].label,
               ok48 ? "yes" : "NO");
    }
    return 0;
}
```

```
$ gcc -o canonical_check canonical_check.c && ./canonical_check
0x0000000000000000  NULL                                      canonical(48-bit)=yes
0x00007fffffffffff  user space 最高位址（4-level）              canonical(48-bit)=yes
0xffff888000000000  direct map 典型起點                         canonical(48-bit)=yes
0xffffffffffffffff  全 1（kernel 最高位址）                      canonical(48-bit)=yes
0x0000800000000000  非法：剛好跨進 non-canonical hole            canonical(48-bit)=NO
0xdeadbeef00000000  常見的『看起來像壞掉的指標』測試值             canonical(48-bit)=NO
```

> **未實測（C 版本），邏輯已驗證**：本環境沒有 C 編譯器可用，上面的 C 程式沒有實際編譯執行；但這段邏輯是純位元運算、不依賴任何 kernel 行為，已經用等價的 Python 實作在本機跑過一次，確認每一行印出的 `yes`/`NO` 結果與上面列出的輸出完全一致。你在自己有 gcc 的環境（任何 Linux/WSL 都可以，不需要 kernel 或 QEMU）跑一次，應該得到相同輸出。`0xdeadbeef00000000` 這個很多人拿來當「故意寫壞指標」的經典測試值，之所以常被選用，恰好也是因為它的高位 bit 不滿足 canonical 規則，用它去戳一個有 bug 的 kernel，很容易直接得到一個乾淨的 `#GP`，比用一個湊巧 canonical 的隨機值更容易重現、更容易在 crash log 裡被認出來。

這個邊界案例（`0x0000800000000000`）也解釋了為什麼 user/kernel 的分界線不是一個單一數字而是一整個「洞」：bit 47 翻成 1 但高位沒有跟著全部翻成 1 的位址，天生就是非法的，不需要 kernel 額外檢查，CPU 硬體層級就擋掉了。

## KASLR：哪些 base 會被隨機化？

**KASLR（Kernel Address Space Layout Randomization）** 的目標是讓 attacker 猜不到任何 kernel 位址，逼你必須先找一個 info leak 才能定位後續的 gadget / 結構。但 KASLR 不是「把整個位址空間打散」，它只是把幾個固定的 **base** 在開機時加上一個隨機 offset，**各區塊之間的相對結構完全不變**。

開啟 `CONFIG_RANDOMIZE_BASE`（kernel image 本身位置）與 `CONFIG_RANDOMIZE_MEMORY`（記憶體區域 base）之後：

| 區塊 | 會被隨機化？ | 隨機化方式 |
|---|---|---|
| kernel text（`_text`） | 是 | 開機時 decompressor 選一個隨機 slot 放整個 kernel image |
| direct map base（`page_offset_base`） | 是 | 加上一個隨機 offset，但內部線性關係不變 |
| vmalloc base | 是 | 同上 |
| vmemmap base | 是 | 同上 |
| **各區塊之間的相對順序與大小** | **否** | KASLR 不改變「direct map 在 vmalloc 前面」這種拓樸關係 |
| module 載入位置 | 是（獨立一次隨機化，和 kernel text 的隨機化綁在一起） | |

這解釋了一個很多新手會問的問題：「leak 到一個 kernel text 位址之後，為什麼我就能推出 direct map 的位址？」——答案是：**你不是直接推算，你是先用這個 leak 算出 KASLR 的隨機 slot（kernel image 相對它的預設連結位址位移了多少），再用這個 slot offset 去修正你記憶體裡存的、已經知道結構的「預設位址表」**。不同區塊的隨機化 offset 彼此之間不一定相同（不同版本行為可能不同；6.6 上 kernel text 和記憶體區域各自有獨立的隨機化來源），所以 leak 到一個 text 位址，不代表你立刻知道 direct map base——你通常還需要額外一次 leak，或者透過某個同時暴露兩者關係的結構。Ch 14 會把各種 leak 技巧和它們分別能告訴你哪個 base 講清楚。

> **未實測，理論預期行為**：以上行為是 kernel 原始碼（`arch/x86/mm/kaslr.c`、`arch/x86/boot/compressed/kaslr.c`）設計上的預期，本機未跑實機驗證隨機化細節。驗證方式：在你自己的 QEMU 裡開兩次機，各跑一次 `sudo cat /proc/kallsyms | head -1` 或讀 `/sys/kernel/debug/kernel_page_tables`（需要 `CONFIG_PTDUMP`），比較兩次開機的 kernel text 基底位址是否不同；關閉 KASLR（kernel cmdline 加 `nokaslr`）重跑一次確認兩次開機位址相同。

## 範例：一次假想的 KASLR offset 推算

把 KASLR 的隨機化講得更具體一點。假設某個 kernel 編譯時，`_text` 的**預設連結位址**（link-time address，寫在 `System.map` 或 `/proc/kallsyms` 沒開 KASLR 時會看到的值）是：

```
_text (link-time)      = 0xffffffff81000000
```

某次真實開機，你透過某個 info leak 拿到了這個 kernel 實際跑起來後 `_text` 的真實位址：

```
_text (runtime, leak)  = 0xffffffff85a00000
```

兩者相減，就是這次開機 KASLR 選到的隨機位移：

```
kaslr_slide = 0xffffffff85a00000 - 0xffffffff81000000 = 0x4a00000
```

現在，任何你原本就知道「相對 `_text` 的固定位移」的 kernel symbol（例如某個函式、某個全域變數，位移在同一份 kernel 原始碼/vmlinux 裡是固定的，不受這次開機影響），都可以用同一個 `kaslr_slide` 修正出它的真實位址：

```
目標 symbol 的真實位址 = 目標 symbol 的連結位址 + kaslr_slide
```

這就是為什麼 exploit 裡常常看到「leak 到一個 kernel text 位址之後，直接去查這個 kernel 版本的 `vmlinux`（或 `System.map`）算出其他函式的相對位移」這個套路——**只要兩個 symbol 都在同一塊「隨`_text`一起整體移動」的區域，一次 leak 就能解鎖所有這類 symbol**。但要注意：這招只對「text 隨機化」這個 slide 有效，**不會自動告訴你 direct map base 或 vmalloc base**，因為（如前所述）不同區域的隨機化來源在部分版本上是獨立的——這是本章要你記住的邊界，別把一次 leak 過度泛化。

> **未實測，理論預期行為**：以上是對 KASLR 位移計算方式的概念性說明，數字為假想範例非真實 leak 結果。驗證方式：在你自己 QEMU 裡，用 `nokaslr` 開機一次記下 `/proc/kallsyms` 裡某個固定 symbol 的位址（這是「連結位址」的近似值，嚴謹來說應對照 `vmlinux` 的 `nm` 輸出），再正常開機（KASLR 開啟）讀一次同一個 symbol 的位址（需要 `CONFIG_KALLSYMS` 且核對你的 `kptr_restrict` 設定，在非特權使用者下 `/proc/kallsyms` 可能把位址遮罩成全 0），用兩次的差算出 slide，再拿另一個已知固定位移的 symbol驗證公式。

## 底層機制：虛實位址怎麼轉換

### `__pa()` / `__va()`：direct map 的核心價值

kernel 程式碼裡到處可見 `virt_to_phys()`（也就是 `__pa()`）和 `phys_to_virt()`（`__va()`）。因為 direct map 是線性映射，這兩個巨集本質上就是：

```c
/* 概念示意，實際實作在 arch/x86/include/asm/page.h，
   會額外處理 __START_KERNEL_map 附近的特例 */
#define __va(x)   ((void *)((unsigned long)(x) + page_offset_base))
#define __pa(x)   ((unsigned long)(x) - page_offset_base)
```

這就是為什麼「知道一個 physical address」幾乎等於「知道對應的 kernel virtual address」——只要你知道（或推得出）`page_offset_base`，中間只差一個加法。這也是為什麼 Dirty Pagetable 系列攻擊的敘事通常是：「透過某個漏洞把一個 user-mapped page 的 PTE，改成指向任意 physical frame」→ 「這個 physical frame 透過 direct map，本來就有一個合法的 kernel virtual address 對應它」→「所以你現在同時用兩個窗口看同一塊記憶體：user-space 的那個窗口你能直接 `write()`，kernel 的那個窗口放著 kernel 自己的資料結構」。

### 4-level page table 怎麼查

x86-64 的 MMU 用多級頁表把一個 48-bit 虛擬位址翻成實體位址。4-level（`CONFIG_X86_5LEVEL=n` 或 CPU 不支援 LA57 時）把 48 bit 拆成：

```
63        48 47    39 38    30 29    21 20    12 11         0
┌──────────┬────────┬────────┬────────┬────────┬────────────┐
│ sign ext │  PGD    │  PUD    │  PMD    │  PTE    │  page offset │
│ (9 bit 不用) │ 9 bit  │ 9 bit  │ 9 bit  │ 9 bit  │   12 bit     │
└──────────┴────────┴────────┴────────┴────────┴────────────┘
   47~63       47~39     38~30     29~21     20~12      11~0
```

查表過程（CPU 的 page walker 做的事）：

```
CR3（存這個 process 的 PGD 實體位址）
   │
   ▼
PGD[bits 47:39]  ──▶ 存著下一級 PUD table 的實體位址（+ flags）
   │
   ▼
PUD[bits 38:30]  ──▶ 存著下一級 PMD table 的實體位址
   │
   ▼
PMD[bits 29:21]  ──▶ 存著下一級 PTE table 的實體位址
   │                 （也可能直接是一個 2MB huge page，流程在這裡就停）
   ▼
PTE[bits 20:12]  ──▶ 存著最終那個 4KB page 的實體位址
   │
   ▼
加上 page offset（bits 11:0） = 最終實體位址
```

每一級的表項（entry）除了存下一級的實體位址，還帶一組重要的 flag bit，Ch 23（Dirty Pagetable）會把這些 bit 一個個拆開用：

| Bit | 名稱 | 意義 |
|---|---|---|
| 0 | `P`（Present） | 這個 entry 是否有效；0 的話存取會 page fault |
| 1 | `R/W`（Read/Write） | 0 = 唯讀，1 = 可寫 |
| 2 | `U/S`（User/Supervisor） | 0 = 只有 ring 0 能存取，1 = user 也能存取 |
| 5 | `A`（Accessed） | CPU 存取過這個 page 會自動設 1 |
| 6 | `D`（Dirty，只在最後一級） | 寫入過這個 page 會自動設 1 |
| 7 | `PS`（Page Size，PMD/PUD 層） | 1 代表這一級就是終點（2MB/1GB huge page） |
| 63 | `NX`（No-Execute） | 1 = 這個 page 不可執行（配合 SMEP 理解，Ch 13） |

**PTE 的 `R/W` bit 是 Dirty Pagetable 攻擊的靈魂**：如果攻擊者能透過某個漏洞，把一個原本唯讀的 PTE 的 `R/W` bit 從 0 改成 1，這個 page 從此變成「user 可寫」，而它背後的 physical frame 是攻擊者選的——這就是「任意實體位址 → 任意可寫映射」的直接構造。

### 5-level paging 的差異

當 CPU 支援 LA57 且 kernel 以 5-level 開機，虛擬位址用滿 57 bit，中間多插一級 **P4D（Page-4 Directory）**：

```
PGD → P4D → PUD → PMD → PTE → page offset
```

查表邏輯一樣，只是多一層。kernel 原始碼裡用 `p4d_t` 這個型別統一處理——如果你的 kernel 是 4-level 編譯的，`p4d` 這一層在程式碼邏輯上會被「摺疊」成直接等於 PGD 那一層（kernel 用 `#define __PAGETABLE_P4D_FOLDED` 讓同一套程式碼在兩種模式下都能編譯），你不需要為兩種模式寫兩套 exploit 邏輯，但要知道你讀到的 `pgd_t` / `p4d_t` 等結構的語意依編譯模式而不同。

## direct map 為什麼是「橋」：具體案例走一遍

這一節把前面的理論串成一條故事線，示範「任意實體讀寫」如何變成「任意 kernel 讀寫」。

假設你已經透過某個漏洞（細節留給 Part 3）拿到一個原語：**給一個 physical address，可以讀/寫那個 physical frame 的內容**（例如你能控制一個 PTE 指向它，然後透過 mmap 出來的 user page 讀寫）。

```
Step 1：你有 physical frame 0x12345000 的讀寫權限
            │
            ▼
Step 2：direct map 把所有實體記憶體線性映射到
        [page_offset_base, page_offset_base + 實體記憶體總大小)
            │
            ▼
Step 3：這個 physical frame 在 kernel 虛擬位址空間裡，
        早就有一個合法位址：
            kernel_va = page_offset_base + 0x12345000
            │
            ▼
Step 4：如果你知道（或 leak 出）page_offset_base，
        你現在知道「這塊實體記憶體」同時是：
          - 你透過漏洞控制的那個 physical frame
          - kernel 的虛擬位址 page_offset_base + 0x12345000
            │
            ▼
Step 5：如果這塊實體記憶體剛好被某個敏感 kernel object
        （例如一個 process 的 struct cred）占用，
        你現在等於直接讀寫這個 struct cred
```

這條鏈裡沒有任何一步需要繞過 KASLR 以外的 mitigation——**它根本不走 ROP、不劫 RIP**，純粹是資料結構層面的任意讀寫，這也是 Ch 29 data-only attack 整個章節存在的原因：現代 mitigation（CFI、KCFI）針對的是「控制流劫持」，對這種純資料操作完全沒有防禦力。

> **未實測，理論預期行為**：以上是 Dirty Pagetable 技巧的結構性原理說明（細節在 Ch 23 完整展開），本章不提供完整 exploit。驗證這條鏈的存在，可以在你自己的 QEMU 裡，用一個你完全控制的 kernel module，故意把一個已知 physical frame 的內容寫一個 magic value，再透過 `/proc/kcore`（如果你的 kernel 允許）或在 module 內用 `__va(phys_addr)` 讀出來，確認兩邊看到同一份資料。`/proc/kcore` 本身在生產環境通常被 `CONFIG_STRICT_DEVMEM` 和 `lockdown` 限制，這是故意設計的防線之一，Ch 51 會談。

## vmalloc 區與 direct map 的對比

| 維度 | direct map（physmap） | vmalloc 區 |
|---|---|---|
| 虛實映射關係 | 線性（簡單加法） | 任意（每個 page 獨立建 PTE） |
| 是否覆蓋所有實體記憶體 | 是，開機時就建好 | 否，按需求動態建立/拆除 |
| 查詢虛實轉換的成本 | O(1)（`__pa`/`__va`） | 需要查頁表（`vmalloc_to_page()`） |
| 典型使用場景 | kernel 直接存取任何已知 physical frame | 需要「虛擬連續但實體不連續」的大塊記憶體；`vmalloc()`、部分 module 的資料區 |
| 對攻擊者的意義 | 任意實體讀寫 → 任意 kernel 虛擬讀寫的直接橋樑 | 本身較少是 bug 的目標，但 vmalloc 的 guard page、不連續特性會影響某些 heap spray 策略的穩定性 |

## fixmap：手動釘死的少量特殊映射

除了 Ch 6 表格列的大區塊，kernel 虛擬位址空間裡還有一塊很小、但角色特殊的區域叫 **fixmap**。它不是用來放一般資料，而是給開機早期、或某些必須用「固定、提前知道的虛擬位址」才能工作的機制用——例如 APIC 暫存器的映射、early console、某些 CPU 相關的控制結構。fixmap 的位址在編譯期就已經決定好（不像 vmalloc 是執行期動態配置），每個用途對應一個固定的 slot，透過 `fix_to_virt(idx)` 這個巨集在編譯期展開成常數。

對 kernel exploitation 而言，fixmap 本身很少是直接攻擊目標，但理解它存在的理由，有助於理解一個更大的原則：**kernel 虛擬位址空間裡，有些區域的存在是為了解決「這段程式碼執行時，一般的動態映射機制還不能用」這個先天問題**。cpu_entry_area（下面會講）、fixmap、以及開機早期的 identity mapping，都是同一個母題的不同解法。

## KASAN shadow 與 cpu_entry_area：兩塊不要跳過的區域

**KASAN shadow 記憶體**：如果 kernel 編譯時開了 `CONFIG_KASAN`（debug/fuzzing 向，generic kernelCTF 題目通常不開，但你自己跑 Syzkaller 時幾乎必開），每 8 byte 的真實記憶體都對應 1 byte 的「影子記憶體」，用來記錄這段記憶體是否被標記為「不該存取」（已釋放、還沒初始化、redzone）。這塊影子記憶體本身也占掉一段虛擬位址空間，而且它的存在**會強制關閉部分記憶體區域的 KASLR**——因為 shadow 記憶體的位置是用真實位址做簡單運算推出來的，如果真實位址亂跳，shadow 的計算公式會被打亂。這就是為什麼 `Documentation/arch/x86/x86_64/mm.rst` 會特別註明 KASAN 和 KASLR 記憶體區域之間的排布限制。Ch 44 會細講 KASAN 怎麼抓 bug。

**cpu_entry_area**：每個 CPU 核心都有一塊私有的固定映射區，裝著這個 CPU 專用的 GDT、TSS、**entry/exit trampoline stack**（KPTI 相關，Ch 15 細講）。這塊區域刻意獨立出來、而不是放進一般的 per-cpu 資料區，是因為它在 CPU 進出 kernel mode 的最早/最晚時刻就要能被存取，這時候一般的頁表切換可能還沒完成。理解它的存在，是看懂 Ch 15 KPTI trampoline 怎麼運作的前提。

## 踩雷集錦

1. **錯誤直覺：「leak 到一個 kernel 位址就等於拿到所有 base」**。實際上不同區域的 KASLR 隨機化來源不一定相同，leak 到 kernel text 位址只告訴你 kernel image 的隨機 slot，不自動告訴你 direct map base 或 vmalloc base。你通常需要分別 leak，或找一個結構本身就同時暴露兩者關係的洩漏點（例如某些 struct 裡同時存著 text 指標和 heap 指標）。

2. **錯誤直覺：「physical address 和 kernel virtual address 是同一個東西，只是『看法不同』」**。不對，它們是兩個不同的位址空間，只是 direct map 這個特定映射用簡單加法把兩者關聯起來。沒有 direct map（理論上也可以不建這個映射），physical address 和 virtual address 之間完全沒有固定關係。CONFIG_SLAB_VIRTUAL（Ch 9 預告、Ch 26 細講）這類新機制的一個目標，正是削弱「拿到一個 physical frame 就等於拿到確定可用的 kernel virtual address」這個假設。

3. **錯誤直覺：「vmalloc 出來的記憶體，虛擬位址連續，所以實體位址也連續」**。vmalloc 存在的理由恰好相反——它專門用來在實體記憶體碎片化、拿不到大塊連續 physical frame 時，依然給你一塊虛擬連續的記憶體。如果你寫 kernel module 需要 DMA（硬體直接存取實體記憶體），vmalloc 出來的記憶體不能直接拿去用，要用 `dma_alloc_coherent()` 之類的介面。

4. **邊界情況：non-canonical address 不是「隨便一個很大的數字」**。很多新手以為把一個指標改成 `0xdeadbeef00000000` 之類的值就能製造「任意位址存取」，但這種值的高位 bit 不滿足 canonical 規則，CPU 在真正查頁表之前就直接 `#GP`，連 page fault handler 都進不去。要構造有效的 kernel 位址崩潰測試，必須讓高 17（或 5-level 下更少）個 bit 保持一致（全 0 或全 1）。

5. **效能相關誤解：「direct map 把所有記憶體線性映射，會不會很浪費頁表空間？」**。不會到「浪費」的程度，因為 direct map 大量使用 2MB / 1GB 的 huge page mapping（對應前面提到的 PMD/PUD 層 `PS` bit），一條 PUD entry 就能覆蓋 1GB 實體記憶體，頁表本身占用的記憶體遠小於你直覺以為的「每 4KB 一條 PTE」。這也解釋了為什麼某些 Dirty Pagetable 的進階手法要先想辦法把目標 page 的映射從 huge page「拆成」4KB granularity（split huge page），才能精細控制單一 PTE。

## 進階：再往深一層

如果你想更深入理解頁表本身的操作，有兩個方向值得追：

- **`CONFIG_PTDUMP`**：開啟這個 config 後，`/sys/kernel/debug/kernel_page_tables` 會把當前整個 kernel 頁表的映射區段印出來（哪個範圍、什麼權限、對應到哪種記憶體類型）。這是驗證本章內容最直接的工具——自己 QEMU 開這個 config，讀一遍輸出，比看任何文章都有說服力。
- **Linear Address Masking（LAM）**：2024 年之後的 Intel CPU 開始支援 LAM，允許在指標的高位塞額外 metadata（類似 ARM 的 pointer tagging）而不影響定址，這會進一步壓縮未來「非法位址」的判定邊界，是關注前沿 mitigation 時值得追的方向，但 Linux 對它的整合程度隨 kernel 版本變動快，請以你實際環境查證。
- **NUMA 下的 direct map**：多 NUMA node 的機器，direct map 仍然是一塊連續虛擬區域，但底層對應到不同 node 的實體記憶體；`numactl`、`/sys/devices/system/node/` 可以觀察這個拓樸，對大型 heap spray 策略（Part 3）有實際影響，因為 SLUB 的 per-node partial list（Ch 8）會按 NUMA node 分開管理。

## 動手練習

在你自己的 QEMU 環境（`CONFIG_PTDUMP=y` 編譯的 kernel）：

1. 開機後讀 `/sys/kernel/debug/kernel_page_tables`（需要 root），找出 direct map 區段的起始位址，記下來。
2. 重開機一次，再讀一次，比較起始位址是否改變（驗證 KASLR 對 direct map base 的隨機化）。
3. 在 kernel cmdline 加 `nokaslr` 重開機，驗證兩次開機位址是否固定不變。
4. 寫一個 5 行的 kernel module，在 `init` 函式裡印出 `(unsigned long)&init_task`（一個已知的 kernel symbol 位址）與 `page_offset_base` 的值，用計算機驗證兩者的高位 bit 符合預期的 canonical 區段。

## 本章重點整理

- x86-64 用 canonical address 規則把 64-bit 位址空間切成 user / non-canonical hole / kernel 三塊；4-level 用 48-bit，5-level 用 57-bit。
- kernel space 內部再切成 direct map、vmalloc、vmemmap、cpu_entry_area、kernel text 等區塊，各自服務不同的映射需求。
- **direct map 是攻擊者的橋**：它把所有實體記憶體用簡單線性公式映射進 kernel 虛擬位址空間，讓「任意實體讀寫」直接等於「任意 kernel 虛擬讀寫」。
- KASLR 隨機化的是幾個區塊的 base，不是整個拓樸結構；不同區塊的隨機化來源可能互相獨立。
- PTE 裡的 `R/W`、`U/S`、`NX` 這些 bit，是後面 Dirty Pagetable（Ch 23）技巧直接操作的目標。

## 自我檢核

- [ ] 不看筆記，能不能解釋「為什麼一個 physical address 的任意讀寫原語，能被轉換成任意 kernel 虛擬位址讀寫」？中間缺了哪一塊資訊才會卡住？
- [ ] 如果有人問你「KASLR 開著的話，leak 一個位址是不是就夠了？」，你會怎麼回答，並舉出至少一個反例？
- [ ] 你能不能畫出 4-level page table 查表的五個步驟，並說出每一級分走原始位址的哪幾個 bit？
- [ ] 想一個你曾經在 user-space 遇到的「某個指標值很奇怪、程式直接 crash」的經驗——用本章學到的 canonical address 規則，能不能重新解釋那次 crash？

## 延伸閱讀

### 官方文件

- **[Documentation/arch/x86/x86_64/mm.rst](https://www.kernel.org/doc/html/latest/arch/x86/x86_64/mm.html)**
  - **讀哪裡**：整篇不長，重點是 4-level 與 5-level 兩張位址表格
  - **和本章的關聯**：本章「x86-64 虛擬位址空間總覽」那張表就是摘自這份文件；它是任何版本行為疑問的最終仲裁來源，因為它跟著 kernel 原始碼一起版本控制

### 部落格 / 技術文章

- **[pawnyable — Linux Kernel：Environment 與 Basic 章節](https://pawnyable.cafe/linux-kernel/)**
  - **這篇說什麼**：用實際 QEMU 操作示範如何讀出 kernel 的記憶體布局、如何驗證 KASLR 行為
  - **讀哪裡**：環境建置與第一個 info leak 範例的章節
  - **為什麼值得讀**：操作步驟和本書 Part 0 的環境高度對應，適合拿來交叉驗證

- **[CTF-Wiki pwn/linux/kernel-mode 的記憶體管理章節（中文）](https://ctf-wiki.org/pwn/linux/kernel/)**
  - **這篇說什麼**：用中文整理了 direct map、vmalloc 等區域在實戰 writeup 裡最常被引用的性質
  - **讀哪裡**：記憶體管理 / mitigation 分類下的 direct map 相關條目
  - **為什麼值得讀**：和大量真實 CTF writeup 交叉引用，方便你讀到別人的 exploit 時對照這些術語

### 書籍

- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：不是 security 書，是 kernel 開發的標準入門書；對理解「kernel 為什麼要有這些記憶體區域」的設計動機很有幫助
  - **讀哪幾章**：第 15、19 章（記憶體管理、page cache）；版本老（涵蓋到 2.6），具體數字位址請以本章與官方文件為準，但設計思路至今沒變

下一章我們往下一層，看這些虛擬位址背後實際管理「page」本身的機制——buddy allocator，這是 SLUB 向作業系統要記憶體的唯一管道。

→ [Ch 7 buddy allocator 與 page](./07-buddy-allocator.md)
