# Ch 16 — 2023+ kernel 在 defend 什麼：random kmalloc caches、SLAB_VIRTUAL、CFI、FGKASLR

> 目標：攤開 2023 年以後主線 kernel 加的每一層 mitigation，說明每層在擋什麼、你前面學的哪些招會被打死。這章是 Part 3 與 Part 4 之間的橋。

## 總覽：mitigation 的演化邏輯

每層 mitigation 都是對著一類已知攻擊設計的。你要記的不是「這個 mitigation 叫什麼」，而是**它在擋的攻擊路徑是什麼、有沒有繞法**。

| Mitigation | 擋什麼 | 首次出現 | kernelCTF 啟用狀態 |
|---|---|---|---|
| SMEP / SMAP | ret2usr / user-space shellcode | 硬體 | 全賽道啟用 |
| KASLR | 地址猜測 | ~4.x | 全賽道啟用 |
| KPTI | Meltdown / kernel VA leak via user PT | 4.15 | 全賽道啟用 |
| Stack canary | stack overflow | 老 | 全賽道啟用 |
| `CONFIG_RANDOM_KMALLOC_CACHES` | heap spray cross-cache | 6.1 | Mitigation 賽道 |
| `SLAB_VIRTUAL` | Dirty Pagetable / cross-cache | 6.5 dev | Mitigation 賽道 |
| KCFI | function pointer hijack | 6.1（clang） | Mitigation 賽道 |
| FGKASLR | ROP gadget 定位 | patchset / 6.x | Mitigation 賽道（部分） |
| CFI（generic） | indirect call | LLVM/clang based | 部分 COS |

kernelCTF 的 **LTS 賽道**（你最常打的）通常只有前五個。**Mitigation 賽道**才開後面那幾個。所以先把前五個吃透，再回頭對付後面的。

---

## CONFIG_RANDOM_KMALLOC_CACHES（kernel 6.1+）

### 原理

沒有這個 config 時，`kmalloc(256, GFP_KERNEL)` 都走同一個 `kmalloc-256` cache，不管 call site 在哪。

開啟後，kernel 在 boot 時用一個隨機種子，對每個 call site（函式 + 行號）做 hash，hash 到 0-15 的 sub-cache index。同 size 的 `kmalloc(256)` 可能落在 `kmalloc-256-0` 到 `kmalloc-256-15` 中的任一個。

```
kmalloc_caches[KMALLOC_NORMAL][index_256][call_site_hash]
```

### 它打死什麼

你在 Ch 11 學的 spray：`msg_msg` + `kmalloc-256` 的 victim → 以前穩定落在同個 cache。現在：
- `msg_msg` 的 alloc call site 的 hash 可能是 7
- victim（你的 UAF object）的 alloc call site 的 hash 可能是 3
- 兩個落在不同的 sub-cache → spray 完全不重疊

### 繞法（Ch 17 深講）

1. **找 same call site**：如果 spray object 和 victim 走同一條 kernel code path alloc → 同 call site → 同 sub-cache
2. **hash 碰撞**：16 個 sub-cache，theoretically 1/16 機率落同一個 — 可以用大量 spray 強行覆蓋所有 16 個
3. **不走 kmalloc-cg / dedicated cache**：改用不受 random cache 影響的 spray object（例如 `nft_set` 相關，因為它走 GFP_KERNEL_ACCOUNT + dediated cache）

---

## SLAB_VIRTUAL（kernel 6.5 dev / 上游開發中）

### 原理

傳統 SLUB：slab page 的物理記憶體是從 buddy 拿的 order-N page，用完 free 回 buddy，buddy 可以把它給任何人（包括 page table allocator）。這是 Dirty Pagetable 的根基。

`SLAB_VIRTUAL`：slab page **不從 buddy allocator 拿**，而是從一個 isolated virtual address space 分配，物理 page 由 vmalloc 系統管理。slab page 的物理 page **不會回到 buddy**，也就不會被 PTE allocator 拿去。

### 它打死什麼

- **Dirty Pagetable**：cross-cache 讓 slab page 落到 PTE page 的前提是「slab page 回到 buddy」。`SLAB_VIRTUAL` 切斷這條路。
- **USMA**（基於 Dirty Pagetable）：同上，前置步驟失效。

### 現狀

`SLAB_VIRTUAL` 截至 2024 仍在 patchset 階段，尚未進主線，但 kernelCTF Mitigation 賽道的 COS kernel 已經在測試。打 Mitigation 賽道要假設它存在。

---

## KCFI（Kernel Control Flow Integrity，kernel 6.1 + clang）

### 原理

每個 indirect call site 在編譯時插入一個 type hash check：

```c
/* 編譯器在每個 indirect call 前插入 */
if (__kcfi_typeid(callee) != expected_type_id)
    __kcfi_handle_error();

/* 直接跳目標函式 */
call *function_ptr;
```

`__kcfi_typeid` 是函式 prototype 的 hash，在 compile time 算出。兩個函式 signature 完全一樣才有相同 type id。

### 它打死什麼

- **tty_struct ops hijack**（Ch 12）：你把 ops 換成自己的 buffer，但 buffer 裡的函式 pointer 指向錯誤 type 的 function → type id mismatch → KCFI 觸發 oops 而不是跳到你的 gadget。
- **seq_operations hijack**：同上。
- **任何 ops struct 替換**：只要 function pointer 指向 wrong-type function 就死。

### 什麼不被擋

- **data-only attack**：你不走 indirect call，改的是 `cred->uid` 這種純 data。
- **合法 function pointer 替換**：如果你能找到 type-compatible 的函式（同 signature），KCFI 不擋。這叫 **type-compatible gadget** — 比 ROP gadget 更稀少但存在。
- **JIT code**（eBPF JIT）：JIT 生成的 code 不一定有 KCFI instrumentation。某些 eBPF verifier bypass 可能繞過這個。

### Shadow Call Stack（SCS，ARM64）

ARM64 上的 KCFI 伴隨 Shadow Call Stack：每次 function call 把 return address 存到 shadow stack，return 時比對。繞過 shadow call stack 比 x86 的 canary 更難。x86 目前主要靠 KCFI，SCS 還不是標配。

---

## FGKASLR（Fine-Grained KASLR）

### 原理

普通 KASLR：整個 kernel image 在 boot 時隨機 slide 一個固定值。你 leak 任何一個 symbol → 算出 base → 所有 gadget 地址確定。

FGKASLR：每個 function 在 kernel .text 段內的**相對位置也隨機化**。即使你 leak 了 `commit_creds` 的地址，`prepare_kernel_cred` 和 `commit_creds` 的相對 offset 也是隨機的（不再是 build-time 的 fixed offset）。

### 它打死什麼

- **ROP chain**：你的 gadget 地址算不準（每個函式獨立 slide）。
- **靠 fixed offset 的攻擊**：`kernel_base + 0x12345 = prepare_kernel_cred` 這種算法失效。

### 什麼不被擋

- **每個 symbol 獨立 leak**：如果你能 leak 每個你需要的 symbol 的地址，FGKASLR 不擋你。
- **data-only attack**：不用 gadget，直接改 data。
- **module symbol**：module 通常不受 FGKASLR 影響（module 有自己的 load offset）。

---

## 各 mitigation 對 Ch 1-15 技術的影響矩陣

| 技術 | KCFI | random kmalloc caches | SLAB_VIRTUAL | FGKASLR |
|---|---|---|---|---|
| ret2usr（Ch 4） | 無影響（SMEP/SMAP 已擋） | 無影響 | 無影響 | 無影響 |
| tty_struct ops（Ch 12） | **打死** | 無影響 | 無影響 | gadget 不穩 |
| seq_operations（Ch 12） | **打死** | 無影響 | 無影響 | gadget 不穩 |
| 任意 spray（Ch 11） | 無影響 | **打死（機率降低）** | 無影響 | 無影響 |
| Cross-cache → PTE（Ch 13-14） | 無影響 | **打死（source/dest 分離）** | **打死** | 無影響 |
| USMA（Ch 15） | 無影響 | **打死（前置失效）** | **打死** | 不穩定 |
| Dirty Cred / data-only（Ch 14, 18） | **不受影響** | 部分影響 | 部分影響 | **不受影響** |

---

## 打 Mitigation 賽道的心態

Mitigation 賽道的分數最高，也最難。面對上面這些防禦，現代 exploit 的路線：

1. **UAF / OOB 拿到 heap primitive** — 這步不受 mitigation 影響
2. **info leak** — 把 KASLR / FGKASLR 的 slide 一個一個 leak 出來
3. **cross-cache 不走 PTE，走 dedicated slab**（SLAB_VIRTUAL 未覆蓋的 cache）
4. **data-only 路線**：cred, modprobe_path, 不走 RIP / indirect call
5. 如果非走 indirect call：找 **type-compatible function** 當 target（bypass KCFI）

---

## 自我檢核

- [ ] 能解釋 `CONFIG_RANDOM_KMALLOC_CACHES` 的 sub-cache 選擇機制（call site hash）
- [ ] 知道 `SLAB_VIRTUAL` 切斷 Dirty Pagetable 的哪個步驟（slab page 不回 buddy）
- [ ] 能說出 KCFI 擋什麼、不擋什麼（indirect call type check vs data write）
- [ ] 知道 FGKASLR 和普通 KASLR 的差異（function 級 vs image 級 randomization）
- [ ] 能填上面的影響矩陣（每個技術被哪個 mitigation 打死）
- [ ] 知道 Mitigation 賽道的 4 步思路（heap primitive → leak → cross-cache → data-only）

→ [Ch 17 — 穿越 random kmalloc caches](./17-random-kmalloc-caches.md)
