# Ch 26 — 2023+ mitigation 全景：random kmalloc caches、SLAB_VIRTUAL、CFI/KCFI、FGKASLR、INIT_ON_ALLOC/FREE

> **目標**：把 Part 0–3 的攻擊面主線倒過來讀一遍：攻擊鏈每一段（Bug → Primitive → Leak → Control/Data → Privesc → Cleanup）現在各被哪一層 mitigation 擋住、預設有沒有開、引入在哪個 kernel 版本、攻擊者繞法的方向在哪一章。學完你手上有一張「地圖」：看到任何一個 config 名，能說出它擋哪一招、擋不住哪一招；看到一條 exploit 鏈，能說出它賭的是哪幾層防禦「沒開」或「有縫」。

> **環境**：Linux 6.6 LTS、x86-64 基線。各 mitigation 的引入版本標在表中，標「就我所知」者表示查證時沒有找到一手來源，請以你的 kernel 原始碼 `git log` 驗證。本章的 shell 指令與預期輸出，除了明確標示「實測」的 WSL2 kernel 片段之外，**未實測，為理論預期行為**。所有實驗只在你自己擁有的 VM / 靶機操作。

## 為什麼需要這個？

[Ch 13–15](./13-smep-smap.md) 教的 SMEP / SMAP / KASLR / KPTI 是「第一代」mitigation：它們針對的是 2010 年前後最常見的攻擊形狀——stack overflow、劫 RIP、跳去 user space 的 shellcode。第一代防禦的共通點是**針對利用手法的最後一步**（控制流劫持、執行 user page）設卡。

之後發生的事我們在 Part 3 看過了：攻擊者把重心搬到 heap。UAF、heap overflow 加上 spray（[Ch 17–19](./17-heap-overflow.md)），得到的是「在已知 cache 裡放我想放的物件」這種能力，這個能力完全不碰 SMEP / SMAP / KPTI。再加上 cross-cache（[Ch 22](./22-cross-cache.md)）、Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)），連 SLUB 內部的 hardening（[Ch 9](./09-slub-hardening.md)）都被繞過去。

防守端的反應是第二代 mitigation：**不再只守最後一步，而是去破壞攻擊者的「可預測性」與「可轉換性」**。

| 攻擊者依賴的前提 | 第二代防禦怎麼打破它 |
|---|---|
| 「這個大小的 `kmalloc` 一定落在 `kmalloc-N`，我 spray 的物件和 victim 同 cache」 | 隨機 kmalloc caches：同樣大小，不同 call site 落在不同 cache |
| 「victim slab 的 page 釋放後，能被別種 cache 拿去重用」 | SLAB_VIRTUAL：slab 的虛擬位址只給同一種 cache 重用 |
| 「劫到 RIP 之後，可以跳去任何地方」 | KCFI：間接呼叫的目標型別必須吻合 |
| 「leak 一個 kernel text 位址，所有函式位址都推得出來」 | FGKASLR：函式之間的相對距離也隨機 |
| 「新配置的記憶體裡還有上一個使用者的資料」 | INIT_ON_ALLOC / INIT_ON_FREE：配置或釋放時清零 |
| 「`copy_to_user` 的長度我說了算」 | HARDENED_USERCOPY：複製範圍不能超出 object |
| 「寫 `modprobe_path` 就能讓 kernel 幫我執行 root 程式」 | STATIC_USERMODEHELPER：helper 路徑改成編譯期常數 |

注意這張表的邏輯：它們**每一項都只擋一種前提**。這是本章最重要的觀點，後面每一節都會回到它：mitigation 不是牆，是一道道只擋特定動作的閘門；攻擊者的工作是找到一條沒有閘門的路。

## 先建立直覺：把防禦疊到攻擊鏈上

全書框架是攻擊者地圖。我們把現代 mitigation 逐段釘上去：

```
 Bug ──▶ Primitive ──▶ Leak ──▶ Control / Data ──▶ Privesc ──▶ Cleanup
  │         │            │            │                │           │
  │         │            │            │                │           │
KASAN     RANDOM_      INIT_ON_     KCFI            STATIC_     (CONFIG_
KMSAN     KMALLOC_     ALLOC/FREE   (擋劫 RIP)      USERMODE-    BUG_ON_
(開發期    CACHES       HARDENED_    FGKASLR         HELPER       DATA_
 抓 bug)  SLAB_         USERCOPY     (擋函式位址      (擋 modprobe  CORRUPTION
          VIRTUAL       KASLR        推算)           _path)       等：讓失敗
          SLUB_                      kptr_restrict                 變 panic)
          HARDENED
          (Ch 9)
```

這張圖有兩個值得記住的觀察：

1. **Bug 階段只有開發期工具**。KASAN / KMSAN 能在測試時抓到 bug，但它們的成本太高，不會在 production 開（[Ch 44](./44-debug-toolchain.md) 會細講）。production 的防線從 Primitive 階段才開始。
2. **Privesc 階段幾乎沒有通用防禦**。`STATIC_USERMODEHELPER` 只擋「靠 helper 執行程式」那一類。直接改 `cred`（[Ch 16](./16-classic-primitives.md)、[Ch 24](./24-dirty-cred.md)）沒有對應的 production mitigation，這就是 data-only attack 成為主流的結構性原因（[Ch 29](./29-data-only-attack.md)）。

> 如果你對「Bug → Primitive → Leak → Control/Data」這條鏈還不熟，先回看 [Ch 1 — kernel pwn 全景](./01-kernel-pwn-landscape.md) 的攻擊者地圖，以及 [Ch 10 — 漏洞類型學](./10-vulnerability-taxonomy.md) 對 OOB / UAF / type confusion 的分類。

## 全景總表

先看總表，再逐項拆。「預設」欄指的是 mainline 預設與一般 desktop / server distro 常見狀態，**會因發行版而異**，你要查自己的 kernel（本章「動手練習」教怎麼查）。

| 機制 | 主要擋什麼（對應 [Ch 10](./10-vulnerability-taxonomy.md) 類型） | 引入版本 | 預設 | 繞法方向 |
|---|---|---|---|---|
| `CONFIG_RANDOM_KMALLOC_CACHES` | 跨物件 spray：UAF、heap overflow 之後「放對物件」這一步 | 6.6（已查證） | 關（`default n`，依賴 `SLUB && !SLUB_TINY`） | 換 cg 分流、挑 victim、機率式 spray：[Ch 27](./27-random-kmalloc-caches.md) |
| SLAB_VIRTUAL | cross-cache（UAF 後以別種物件重用同一 page） | 2023-09 RFC，**未進 mainline**（就我查證的範圍） | 不適用 | 理論上封死 page 層重用；改走 data-only 或不依賴 cross-cache 的 bug |
| CFI / KCFI | 控制流劫持：heap → RIP（[Ch 21](./21-heap-to-rip.md)）、kernel ROP 的第一跳 | KCFI 6.1（x86 + arm64），需 clang 16+ | 關（distro 少數，如 Android GKI 開） | 合法 target 之間的呼叫、純 data-only：[Ch 28](./28-cfi-kcfi.md)、[Ch 29](./29-data-only-attack.md) |
| FGKASLR | 「leak 一個位址推全部」（[Ch 14](./14-kaslr-infoleak.md)） | 2020–2022 多版 patch，**未進 mainline**（就我查證的範圍） | 不適用（需自行打補丁） | 找未被重排的區段、讀 `__ksymtab`、改走 data-only |
| `INIT_ON_ALLOC` | uninitialized memory leak（[Ch 14](./14-kaslr-infoleak.md)） | 5.3 | 發行版常開（WSL2 實測：開） | 找不經過 init 的路徑（有 ctor / `RCU` 的 cache）、其他 leak 源 |
| `INIT_ON_FREE` | UAF 讀到殘留資料、freelist 殘留 | 5.3 | 通常關（WSL2 實測：關） | 不擋「寫」與「重用」，只擋「讀殘留」 |
| `HARDENED_USERCOPY` | 越界的 `copy_to_user` / `copy_from_user`（[Ch 4](./04-syscall-boundary.md)、[Ch 17](./17-heap-overflow.md)） | 4.8 | 發行版常開（WSL2 實測：開） | 在 object 範圍內的錯誤長度；不經 usercopy 的越界 |
| `STATIC_USERMODEHELPER` | `modprobe_path` / `core_pattern` 類（[Ch 16](./16-classic-primitives.md)） | 4.11（有來源互相矛盾，見下節） | 關 | 改 `cred`、Dirty Cred（[Ch 24](./24-dirty-cred.md)） |
| KASAN / KMSAN | 所有 memory corruption（發現，不是阻擋） | KASAN 4.0；KMSAN 6.1（就我所知） | 開發期專用 | 不適用（production 不開） |

這張表的讀法：**橫著讀一列，回答「它擋什麼、繞法在哪」；直著讀「預設」欄，你會發現真正在 production 預設開著的只有 INIT_ON_ALLOC、HARDENED_USERCOPY 這類成本低的**。成本高、效益大的（KCFI、random caches）要嘛少數場景開、要嘛要自己編。這解釋了 kernelCTF 為什麼有獨立的 mitigation 賽道（[Ch 49](./49-kernelctf-overview.md)）：賽方要量測「這一層到底擋掉多少 exploit」。

## 1. CONFIG_RANDOM_KMALLOC_CACHES

### 動機

回顧 [Ch 8](./08-slub-allocator.md)：`kmalloc(size)` 依 size 選一個 `kmalloc-N` cache（N 是 8、16、32、64……的 bucket）。這個選擇是**確定性的**，只看 size（和 GFP flag 決定的 cache type）。所以 Part 3 的 spray 策略成立：你知道 victim 是 `kmalloc-512`，就用 512 byte 的 `msg_msg` 或 `user_key_payload` 去填同一個 cache。

隨機 kmalloc caches 把「size → cache」這個對應打亂。

### 機制（就我們查證的範圍）

- 作者是 Gong Ruiqi（Huawei），經過多輪 patch（RFC v2、v3、v4、v5）後，由 Vlastimil Babka 在 slab 的 `6.6` pull request 中合入。我們從 LKML 的 pull request 與 Linus 的 merge commit 查證了「6.6 引入」這一點。
- 做法：為每個一般 `kmalloc-N` 另外建立**多份永不合併的複本**。最終版本是 15 份複本（`RANDOM_KMALLOC_CACHES_NR = 15`），加上原本的一份，共 16 份。
- 選哪份：以**呼叫 `kmalloc` 的 code 位址**（return address）和一個**每次開機產生的隨機 seed** 做 hash，結果決定落在哪一份。
- Kconfig：`default n`，`depends on SLUB && !SLUB_TINY`。

關鍵觀念：**同一個 call site 在同一次開機內永遠落在同一份複本**。它不是「每次呼叫擲骰子」，而是「每個 call site、每次開機擲一次骰子」。

```
          kmalloc(512, GFP_KERNEL)
                    │
        ┌───────────┴────────────┐
        │  Ch 8 以前：只看 size   │──▶ kmalloc-512  （唯一一個）
        └────────────────────────┘

        ┌─────────────────────────────────────────────┐
        │ 6.6 + RANDOM_KMALLOC_CACHES：                │
        │   idx = hash( caller_ip ^ boot_seed ) % 15   │
        └─────────────────────────────────────────────┘
                    │
   call site A ─────┼──▶ kmalloc-rnd-03-512
   call site B ─────┼──▶ kmalloc-rnd-11-512
   call site C ─────┼──▶ kmalloc-rnd-03-512   （碰撞：和 A 同一份）
                    │
   （一般 cache kmalloc-512 對「有 hash 的 call site」不再被選用；
     名稱格式以你的 kernel /proc/slabinfo 為準，未實測）
```

### 它擋什麼、擋不住什麼

擋的是：**spray 物件和 victim 在不同 call site**這個情況。以 [Ch 18](./18-uaf-double-free.md) 的 UAF 為例：victim 是 driver 在 call site V 配置的 512 byte 物件，你用 `msg_msg`（call site M，在 `load_msg`/`alloc_msg`）去重用它。沒有 random caches 時，V 和 M 都落在 `kmalloc-512`；有了之後，V 和 M 各自 hash 到不同的複本，你的 spray 物件根本不在 victim 所在的 slab。

擋不住的：

1. **機率性**：兩個 call site 落在同一份的機率大約是 1/16（hash 均勻分佈的假設下，粗略估計）。這是減損，不是封死。
2. **不走 `kmalloc` 這條路的配置**：dedicated cache（`kmem_cache_alloc` 直接用專屬 cache，如 `struct file` 的 `filp`）不在這個機制裡。
3. **cgroup 記帳的配置（`GFP_KERNEL_ACCOUNT`）**：6.6 的實作只對一般（normal）類型做隨機化，帶 `__GFP_ACCOUNT` 的配置走 `kmalloc-cg-N`。這會讓「victim 不帶 ACCOUNT、spray 物件帶 ACCOUNT」形成**結構性錯位**，反過來也是攻擊者能利用的分流。細節是 [Ch 27](./27-random-kmalloc-caches.md) 的主題，這裡只要記得「有這回事」。
4. **能自己決定 call site 的 spray**：如果 victim 和攻擊者都能走到「同一個 call site」（例如 victim 本身就是 `msg_msg` 或 `seq_operations` 這種 user 可觸發的配置），hash 結果必然相同。

因此我們對它的評價是：**它把「已知可行的 spray 清單」從「幾十個通用物件」砍到「必須逐一驗證 hash 是否碰撞的少數物件」**，大幅提高了成本，但沒有改變 bug 的可利用性。

## 2. SLAB_VIRTUAL

### 動機

cross-cache（[Ch 22](./22-cross-cache.md)）的核心動作是：把某個 slab 整個釋放回 buddy allocator（[Ch 7](./07-buddy-allocator.md)），再讓另一種 cache 配置到同一個 physical page。UAF 的 dangling pointer 因此指向「另一種型別的物件」。Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）更進一步：讓那個 page 變成 page table。

random kmalloc caches 擋不住這個——它只管 `kmalloc` 的 cache 選擇，不管 page 怎麼回收。

### 機制

2023 年 9 月，Matteo Rizzo（Google）貼出一個 14 patch 的 RFC，標題是「Prevent cross-cache attacks in the SLUB allocator」，程式碼原作者標示為 Jann Horn。概念：

- slab 不再直接從 physmap（direct map，[Ch 6](./06-kernel-memory-layout.md)）取 page，而是從一塊**專用的虛擬位址區域**配置。
- slab 釋放時，**實體 page 可以還給 buddy allocator，但那段虛擬位址保留給同一種 cache**。之後 dangling pointer 即使還指向舊位址，它指到的也只可能是「同一種型別的 slab」或「未映射」（存取就 page fault）。

```
  沒有 SLAB_VIRTUAL（現況）            有 SLAB_VIRTUAL（RFC 概念）
  ┌────────────────────────┐        ┌────────────────────────────┐
  │ slab page（physmap）    │        │ slab 虛擬位址 V（專屬區域） │
  │   │ 釋放                │        │   │ 釋放                    │
  │   ▼                    │        │   ▼                        │
  │ buddy allocator        │        │ 實體 page → buddy（可重用） │
  │   │ 重配置              │        │ 虛擬位址 V → 仍保留給同一   │
  │   ▼                    │        │   cache，不會被別的型別拿走 │
  │ 別種 cache / page table │        │ dangling ptr 只能打到同型別 │
  └────────────────────────┘        └────────────────────────────┘
        cross-cache 成立                    cross-cache 失敗
```

### 狀態與評價

- 我們查證到的是 2023-09 的 RFC 與討論串（含 Kees Cook 貼的 page table dump 補丁，以及維護者對「以單一 compile-time option 提供」的質疑）。**我們沒有找到後續版本被合進 mainline 的證據**，因此這裡把它標為「未進 mainline（就我查證的範圍）」，請你以 6.6 之後的 `mm/slub.c` 自行確認。
- 它的價值在**概念**：它是少數「決定型」（deterministic）而非「機率型」的 heap mitigation，直接打斷 cross-cache 的前提。讀它是為了理解「防守端認為 cross-cache 值得專門封」這個信號。
- 它擋不住：同型別物件之間的 UAF（type-safe 重用本來就是 allowed）、不依賴 cross-cache 的 data-only 路徑。

## 3. CFI / KCFI（概念，細節在 [Ch 28](./28-cfi-kcfi.md)）

[Ch 21](./21-heap-to-rip.md) 的做法：覆寫 `tty_struct->ops`（或 `seq_operations->start`）這類函式指標，下次 kernel 透過指標呼叫時 RIP 就落到我們指定的位址。CFI（Control-Flow Integrity，控制流完整性）在**每個間接呼叫點前**插一段檢查：目標函式是否真的是「這個呼叫點預期的型別」。

- Linux 的實作是 clang 的 `-fsanitize=kcfi`（簡稱 KCFI），**Linux 6.1 起支援 x86-64 與 arm64**，需要 **clang 16** 以上（查證：Sami Tolvanen 的 patch 系列、v6.1-rc1 的 kcfi pull request；最低 clang 版本在討論串中提到 clang 16，我們沒有對照 Kconfig 逐字驗證，請以你 kernel 的 `Documentation/` 與 Kconfig 為準）。
- 預設：關。需要自己用 clang 編譯並開 `CONFIG_CFI_CLANG`。Android GKI（arm64）是大規模量產使用的主要例子；一般 x86 desktop distro 的 kernel 大多不開。
- 擋：「劫 RIP 去 gadget / `commit_creds`」。
- 擋不住：`ret` 型的 ROP、data-only、型別吻合的合法目標之間的呼叫。這三點是 [Ch 28](./28-cfi-kcfi.md) 的主題。

這裡只強調一個結構：**KCFI 沒有改變 Primitive 階段**。你還是可以 UAF、還是可以 spray、還是可以拿到任意寫。它只是把「Control」那一格的出口封了，把攻擊者推向 data-only。

## 4. FGKASLR

KASLR（[Ch 14](./14-kaslr-infoleak.md)）把整個 kernel image 以一個隨機 offset 搬家。意味著「leak 任何一個 kernel text 位址 → 減去它的已知 offset → 得到 base → 其他所有符號位址 = base + 已知 offset」。**一次 leak 推全部**。

FGKASLR（Function Granular KASLR，函式粒度 KASLR）的做法：用 `-ffunction-sections` 讓每個函式各自一個 section，開機時把這些 section 的順序洗牌。leak `commit_creds` 的位址，推不出 `prepare_kernel_cred` 在哪裡，因為兩者的相對距離在每次開機都不同。

- 作者 Kristen Accardi（Intel），2020 年起發了多版 patch（v3 在 2020-06），Alexander Lobakin 後來重做並發到 v10（2022-02）。**就我們查證的範圍，沒有進 mainline**，所以它不是「你的 6.6 kernel 預設有的東西」。
- 代價：patch 作者的測量報告是 build 時間約慢 1%、壓縮後 kernel image 約大 15%（來自討論串，不同版本數字有出入）。
- 實務上你會在兩個地方碰到它：**CTF 出題者自己打補丁**（就我所知）、以及理論文獻。

### 繞法方向（概念）

FGKASLR 只洗牌函式 section，**不洗牌 data**。所以：

1. `.data` / `.bss` 裡的目標（`modprobe_path`、`init_cred`、`init_task`）相對 base 的 offset 不變。**data-only 路徑對 FGKASLR 免疫**。
2. 部分 section 不參與洗牌（例如 entry code 相關的區段，包含 KPTI return trampoline 所在的 `.entry.text`），這些位址仍然可從 base 推得。這就是 [Ch 15](./15-kpti.md) 的 `swapgs_restore_regs_and_return_to_usermode` 在 FGKASLR 下依然可用的原因。
3. `__ksymtab` 裡存的是**相對位址**，且該表本身不洗牌。如果你有任意讀，可以讀這張表把 `commit_creds` 等符號解出來。

結論：FGKASLR 讓「純 leak + ROP」變貴，但對有任意讀的攻擊者幾乎無效。它和 KCFI 一起指向同一個方向——別走控制流。

## 5. INIT_ON_ALLOC / INIT_ON_FREE

### 動機

[Ch 14](./14-kaslr-infoleak.md) 的 uninitialized memory leak：driver 用 `kmalloc`（不是 `kzalloc`）配一個 struct，只填了部分欄位就 `copy_to_user` 整個 struct，剩下的欄位洩漏了前一個使用者留在這塊記憶體的資料——可能是 kernel 指標。

這是一整類 bug，修法是「每個 call site 都改 `kzalloc`」，但 kernel 有數百萬行。

### 機制

- Alexander Potapenko（Google）的 patch，**5.3** 引入（就我所知）。靈感來自 PaX 的 `PAX_MEMORY_SANITIZE`（見 [Ch 51](./51-defense-detection.md) 的 grsecurity 歷史段）。
- `init_on_alloc=1`（或 `CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y`）：page allocator 與 slab allocator 在**配置**時把記憶體清零（`__GFP_ZERO` 效果），除非該 cache 有 constructor 或 `SLAB_TYPESAFE_BY_RCU` 等必須保留內容的情況。
- `init_on_free=1`（或 `CONFIG_INIT_ON_FREE_DEFAULT_ON=y`）：在**釋放**時清零。
- 兩者是 boot parameter，可開可關。

```
  kmalloc(n)  ──▶ slab_alloc ──▶ [取出 object] ──▶ memset 0 ◀── init_on_alloc
                                                       │
                                                       ▼
                                                  回傳給 caller

  kfree(p)    ──▶ [memset 0] ◀── init_on_free ──▶ 放回 freelist
```

### 實測：一份真實 kernel 的狀態

下面是在本機 WSL2 的 kernel（**6.18，不是 6.6**，僅作「真實發行 kernel 長這樣」的樣本）上跑 `zcat /proc/config.gz | grep ...` 的**實測**輸出：

```
CONFIG_INIT_ON_ALLOC_DEFAULT_ON=y
# CONFIG_INIT_ON_FREE_DEFAULT_ON is not set
CONFIG_HARDENED_USERCOPY=y
CONFIG_HARDENED_USERCOPY_DEFAULT_ON=y
CONFIG_SLAB_FREELIST_RANDOM=y
CONFIG_SLAB_FREELIST_HARDENED=y
# CONFIG_RANDOM_KMALLOC_CACHES is not set
# CONFIG_STATIC_USERMODEHELPER is not set
# CONFIG_SECURITY_LOCKDOWN_LSM is not set
CONFIG_X86_KERNEL_IBT=y
```

讀法：這個 kernel 開了便宜的（init_on_alloc、hardened usercopy、freelist hardening），關了貴的與需要整合的（init_on_free、random caches、static usermodehelper、lockdown）。這個分布大致代表主流 distro 的立場：**預設開成本趨近於零的，其餘留給管理員選**。

### 它擋什麼、擋不住什麼

擋：uninitialized 欄位洩漏（Ch 14 的一類 leak 源）。`init_on_free` 另外擋「UAF 讀到上一個物件的殘留資料」。

擋不住：

1. **UAF 的重用**。清零發生在 free，之後攻擊者 spray 新物件進去，內容是攻擊者寫的，清零無關。`init_on_free` 不是 UAF mitigation，是 UAF 的資訊洩漏 mitigation。
2. **被 constructor 管著的 cache**：有 ctor 的 cache 不能在 alloc 時清零（否則 ctor 的初始化被毀），所以 `init_on_alloc` 跳過它們。
3. **非 slab / page allocator 路徑的 buffer**：stack 上的 uninitialized 變數是另一條線（`CONFIG_INIT_STACK_ALL_ZERO` 等 stack 初始化選項，本章不展開）。
4. **有意義的指標**：被清零的是記憶體內容；攻擊者仍可從 `/proc`、dmesg、side channel 等其他源 leak（見 Ch 14 的 `kptr_restrict`）。

成本：`init_on_alloc` 一般認為只有幾個百分點以內，`init_on_free` 更高。具體數字見 5.3 時期 commit message 的 benchmark，我們沒有在你的硬體上量過，所以不給數字，請自行 benchmark。

## 6. HARDENED_USERCOPY

[Ch 4](./04-syscall-boundary.md) 教過 `copy_from_user(dst, src, n)`：n 是呼叫者給的。如果 driver 的 `n` 來自 user 輸入而沒檢查，就是 heap overflow（[Ch 17](./17-heap-overflow.md)）；`copy_to_user(dst, kbuf, n)` 的 `n` 過大就是 OOB read leak（[Ch 14](./14-kaslr-infoleak.md)）。

`CONFIG_HARDENED_USERCOPY`（**4.8**，源自 PaX 的 `PAX_USERCOPY`，Kees Cook 移植）在每次 usercopy 前呼叫 `check_object_size()`，檢查**核心這端的緩衝區**：

```
  copy_to_user(ubuf, kptr, n)
        │
        ▼
  check_object_size(kptr, n, to_user=true)
        │
        ├─ kptr 是 stack 上的？  → 必須整個落在目前 stack frame 內
        ├─ kptr 是 slab object？ → [kptr, kptr+n) 必須在該 object 的
        │                          usercopy 白名單區間內
        ├─ kptr 是 kernel text / 不合法位址？ → 拒絕
        └─ 跨多個 page 的複合 page？ → 檢查是否同一 allocation
        │
   違反 → 印 "usercopy: Kernel memory exposure/overwrite attempt detected"
          並 BUG（oops / panic）
```

重點是 **slab 白名單**：dedicated cache 用 `kmem_cache_create_usercopy` 宣告「這個 struct 的哪一段允許與 user 互傳」；`kmalloc-N` 一般 cache 則整個 object 都在白名單內。所以它擋的是「**超出 object 邊界**」，而不是「在 object 內但欄位錯誤」。

- 預設：Kconfig 有 `HARDENED_USERCOPY_DEFAULT_ON`，可用 boot parameter `hardened_usercopy=on|off` 覆蓋。WSL2 樣本實測為開。
- 擋：長度過大的 OOB read 與 OOB write（只要它走 `copy_{to,from}_user` 這條路）。
- 擋不住：
  1. 在 object 範圍內的錯誤（例如 `kmalloc-512` 的物件、`n = 512`，但裡面一半是未初始化——那是 `INIT_ON_ALLOC` 的事）。
  2. 不走 usercopy 的越界：kernel 內部的 `memcpy` / 迴圈寫入（例如 driver 內部的 off-by-one，[Ch 17](./17-heap-overflow.md) 的主要情境）。
  3. 不經 copy 的原語：UAF、type confusion 本身不涉及 usercopy。

## 7. CONFIG_STATIC_USERMODEHELPER

### 回顧攻擊

[Ch 16](./16-classic-primitives.md)：`modprobe_path` 是一個 kernel 全域字串（預設 `/sbin/modprobe`）。當 kernel 要載入未知格式的 binary 或模組，會 `call_usermodehelper` 以 root 身分執行這個路徑。攻擊者用任意寫把它改成 `/tmp/x`，再觸發一次「未知 binary 格式」的 exec，root 就執行了攻擊者的腳本。`core_pattern`（以 `|` 開頭的 coredump handler）、`poweroff_cmd` 同理。

### 機制

`CONFIG_STATIC_USERMODEHELPER` 與 `CONFIG_STATIC_USERMODEHELPER_PATH`（預設 `/sbin/usermode-helper`）：開啟後，`call_usermodehelper_setup()` **忽略呼叫者傳進來的 path**，一律執行 Kconfig 指定的那個單一 binary，原本想執行的路徑變成 `argv[0]` 傳給它。管理員自己寫那個 binary，在裡面決定哪些 helper 可以執行。

```
   無 STATIC_USERMODEHELPER             有 STATIC_USERMODEHELPER
   ──────────────────────              ─────────────────────────────
   call_usermodehelper(                call_usermodehelper(
       path = modprobe_path,               path = modprobe_path,   ← 被改成 /tmp/x
       argv = {...})                       argv = {...})
        │                                       │
        ▼                                       ▼
   exec(modprobe_path)                 exec(CONFIG_STATIC_USERMODEHELPER_PATH)
   ← 攻擊者改過的 /tmp/x                ← 固定值，攻擊者改不到
                                        argv[0] = "/tmp/x"（原 path）
                                        由管理員的 wrapper 決定放不放行
```

### 版本與預設

- 引入：我們查到 2017-01 的 LKML patch 系列（Greg Kroah-Hartman），指向 **4.11**。有一個第三方 Kconfig 索引站標的是 4.2，與 LKML 日期矛盾；我們採 4.11，**但請以 `git log -S STATIC_USERMODEHELPER` 在你的 tree 上驗證**。
- 預設：`n`。一般 distro 不開，因為它要求管理員自己提供 wrapper binary，否則 `modprobe`、`coredump` 到 `poweroff` 的流程全部要重新設計。Kconfig help 裡的建議是路徑設成空字串則禁掉所有 usermode helper（在伺服器 / appliance 情境可行，desktop 不行）。

### 它擋什麼、擋不住什麼

擋：所有「改 path 字串來讓 root 幫我執行程式」。

擋不住：

1. 不靠 helper 的提權：直接改 `cred`、`init_cred`（[Ch 16](./16-classic-primitives.md) 前半）。
2. Dirty Cred（[Ch 24](./24-dirty-cred.md)）：換掉 `struct file` 讓低權限 process 取得高權限檔案的寫入權。
3. 管理員的 wrapper 寫得太鬆（例如讓所有 argv[0] 通過）等於沒開。

## 8. KASAN / KMSAN：開發期的角色

這兩個**不是 production mitigation**，放進來是因為它們是 syzkaller（[Ch 43](./43-syzkaller.md)）找洞的主要判官，也是你寫 exploit 時看 crash log 的主要來源。

| 工具 | 抓什麼 | 引入 | 成本 |
|---|---|---|---|
| KASAN（Kernel Address Sanitizer） | OOB、UAF（用 shadow memory + redzone + quarantine） | generic 4.0；arm64 SW_TAGS / HW_TAGS 後續加入 | shadow memory 約佔 1/8 記憶體，加上 redzone 與 quarantine，執行時間數倍；不適合 production（就我所知，HW_TAGS 例外，見 [Ch 46](./46-arm64-kernel-pwn.md)） |
| KMSAN（Kernel Memory Sanitizer） | 使用未初始化記憶體（uninit leak 的根源） | 6.1（就我所知），僅 clang | 記憶體約 2–3 倍等級，只在測試用 |

關係：INIT_ON_ALLOC 是「不修 bug，讓 bug 無害」；KMSAN 是「把 bug 找出來修掉」。防守端兩條路都需要。

## 例子：同一條 exploit 在不同 kernel 上的命運

我們用概念性的描述走一遍，**未實測，為理論預期行為**。假設有個 driver 的 UAF：victim 為 `kmalloc-512` 的 `struct vuln_obj`（含函式指標）。

**場景 A：6.6 LTS，只有 Ch 0 的預設設定（SMEP / SMAP / KASLR / KPTI + SLAB_FREELIST_HARDENED）**

1. UAF：釋放 `vuln_obj`，dangling pointer 保留。
2. spray：大量 `msg_msg`（或 `user_key_payload`），其中一個佔到原位。
3. leak：透過 `msg_msg` 的讀取拿到 heap / text 位址。
4. 劫 RIP：覆寫 `vuln_obj` 的函式指標，ROP，`commit_creds`。

**場景 B：同一份 kernel + `RANDOM_KMALLOC_CACHES` + `INIT_ON_ALLOC` + KCFI**

1. UAF：同上。
2. spray：`msg_msg` 的 call site 與 `vuln_obj` 的 hash 到不同複本（約 15/16 機率），你 spray 不進去。你得改找其他 call site，或換 victim。
3. leak：就算有，`INIT_ON_ALLOC` 讓大部分 uninit 路徑無料可洩。
4. 劫 RIP：就算覆寫成功，KCFI 在呼叫點檢查目標，不通過直接 oops。

**場景 C：場景 B 的攻擊者轉進**

只要能找到一個 call site 與 victim 同 cache 的物件（Ch 27），並把 UAF 轉成「改某個資料結構」而非「改函式指標」，這個 kernel 仍可被打穿：Ch 22–25 的 data-only 技術不需要 RIP。

這個例子的教訓：**每個 mitigation 單獨看都「可被繞」，疊起來的效果是把攻擊者能用的技術壓縮到一個很窄的集合**。而這個集合，就是 kernelCTF 近年 submission 的樣貌（[Ch 49](./49-kernelctf-overview.md)）。

## 對比與取捨

| 機制 | 類型 | 效能成本 | 覆蓋面 | 對攻擊者的實際影響 |
|---|---|---|---|---|
| RANDOM_KMALLOC_CACHES | 機率型（約 1/16） | 低到中（記憶體碎片化較多） | 只管 `kmalloc` 通用 cache | 迫使 victim 與 spray 做 hash 匹配 |
| SLAB_VIRTUAL | 決定型 | 中（虛擬位址空間、TLB 影響，RFC 有討論） | 只管 slab 的 page 回收 | 封死 cross-cache 的 page 重用 |
| KCFI | 決定型（型別層級） | 中（每個間接呼叫多一次比較；Android 實務可接受） | 只管 forward-edge 間接呼叫 | 封死「任意 RIP」 |
| FGKASLR | 機率型（熵很高） | 低（build 與 image 大小） | 只管函式 text | 迫使攻擊者改用任意讀或 data-only |
| INIT_ON_ALLOC | 決定型 | 低 | 只管 slab / page 配置 | 封死大多數 uninit leak |
| INIT_ON_FREE | 決定型 | 中到高 | 同上 + UAF 殘留 | 封死殘留資料讀取 |
| HARDENED_USERCOPY | 決定型 | 低 | 只管 usercopy 的核心側範圍 | 封死超長度的 copy |
| STATIC_USERMODEHELPER | 決定型 | 幾乎為零，管理成本高 | 只管 usermode helper | 封死 modprobe_path 類 |

這張表有個容易忽略的欄：**「機率型」與「決定型」的差別**。機率型（random caches、FGKASLR、KASLR）讓攻擊成功率降低但可重試（kernelCTF 的 exploit 因此要追求穩定，Ch 45）；決定型（KCFI、SLAB_VIRTUAL）則是「這條路被封」，只能換路。

## 踩雷集錦

1. **「開了 CONFIG_X 就安全了」**：每個 mitigation 只擋一種動作（本章第一張表）。KCFI 開了，攻擊者轉去 data-only；random caches 開了，攻擊者轉去 cg 分流。評估防禦要問「它擋哪一招、攻擊者還剩哪幾招」，而不是「它有沒有開」。
2. **「random kmalloc caches = 每次 kmalloc 都隨機」**：不是。一個 call site 在一次開機內固定落在同一份複本。這一點決定了 Ch 27 的整個攻擊面——你可以在同一次開機內重複觀察同一個 call site 的行為。
3. **「`INIT_ON_FREE` 能擋 UAF」**：它只擋 UAF 的**讀殘留**。重用（spray 填入攻擊者資料）完全不受影響。擋 UAF 重用要靠 SLAB_VIRTUAL 這類型別安全機制。
4. **「看 mainline 就知道我的 kernel 有什麼」**：mitigation 的啟用高度取決於 distro、Android GKI、雲端廠商的 config。實務上你一定要看 `/proc/config.gz` 或 `/boot/config-$(uname -r)`。同一個 6.6，Ubuntu 與 Debian 與 COS 的 config 可以差很多。
5. **把 FGKASLR 和 SLAB_VIRTUAL 當成「現代 kernel 都有」**：就我們查證的範圍，兩者都沒進 mainline。教材描述它們是因為它們代表防守端的研究方向，讀 patch 討論串是理解「為什麼 mainline 卡住」的好教材。
6. **「`STATIC_USERMODEHELPER` 開了就沒有 modprobe 了」**：開了之後 modprobe 仍可執行，只是**經過你指定的 wrapper**。路徑變成編譯期常數，攻擊者無法重導向。

## 進階：再往深一層

### 為什麼防禦疊得起來：每一層擋不同的 invariant

用資訊流角度看：

- **KASLR / FGKASLR**：保護「位址資訊」。
- **KCFI**：保護「控制流 invariant」。
- **INIT_ON_***、**HARDENED_USERCOPY**：保護「資料邊界與內容 invariant」。
- **random caches / SLAB_VIRTUAL**：保護「記憶體布局可預測性 invariant」。
- **STATIC_USERMODEHELPER**：保護「特權動作的呼叫路徑 invariant」。

攻擊者的 data-only 路徑能繞過大半，因為它**破壞的是更高層的 invariant**：`cred` 的 uid、`file` 的 mode、page table 的 PTE。這些資料結構的完整性，沒有任何一個 production mitigation 在保護（這是 [Ch 29](./29-data-only-attack.md) 的起點）。更上層的保護（例如 hypervisor 保護 cred 的研究，或 ARM64 的 PAC 簽 cred 指標）是研究與廠商特有的方向，見 [Ch 46](./46-arm64-kernel-pwn.md)。

### 讀 patch 討論串：判斷一個 mitigation 的真實價值

三個你應該問的問題：

1. **它的威脅模型是什麼？** random kmalloc caches 的作者明確說是「提高 spray 的成本」，不是封死；SLAB_VIRTUAL 的作者說是「決定型封死 cross-cache」。
2. **成本誰付？** FGKASLR 卡在 image 大小與 live patching / kprobe 等工具的相容性；SLAB_VIRTUAL 被問到的是虛擬位址空間與 config 模型。
3. **反方的繞法有沒有被作者自己列出來？** 好的 patch 描述會自陳限制。看作者怎麼承認限制，比看他怎麼宣傳更有資訊量。

## 動手練習

> 以下全部在你自己的 VM / 靶機操作，**未實測於 6.6，為理論預期行為**；`zcat /proc/config.gz` 在 WSL2 上已實測可用。

1. **盤點你的 kernel**：
   ```bash
   zcat /proc/config.gz 2>/dev/null || cat /boot/config-$(uname -r) \
     | grep -E 'RANDOM_KMALLOC|CFI|INIT_ON|HARDENED_USERCOPY|STATIC_USERMODEHELPER|SLAB_FREELIST|RANDOMIZE_BASE|PAGE_TABLE_ISOLATION|LOCKDOWN'
   cat /proc/cmdline
   ```
   對照本章總表，列出「開了哪些、沒開哪些、沒開的是因為成本還是整合困難」。
2. **自己編一個全開的 6.6**：用 Ch 0 的環境，`make menuconfig`，把 `RANDOM_KMALLOC_CACHES`、`INIT_ON_ALLOC_DEFAULT_ON`、`INIT_ON_FREE_DEFAULT_ON`、`HARDENED_USERCOPY`、`STATIC_USERMODEHELPER`（路徑設空字串）打開，再用 `make LLVM=1` 以 clang 16+ 開 `CFI_CLANG`。
3. **觀察 random caches**：開機後 `cat /proc/slabinfo | grep kmalloc`（需 root），數 `kmalloc-rnd-*` 複本有幾份，與 Ch 8 的 `kmalloc-N` 對照。
4. **重跑 Ch 18 的 UAF**：先在沒有 random caches 的 kernel 跑通，再換有的，記錄 `msg_msg` spray 成功率的變化（建議每種設定跑 20 次；成功率是你自己量的數字，別引用我的）。
5. **故意觸發 HARDENED_USERCOPY**：寫一個 [Ch 5](./05-first-vulnerable-module.md) 風格的 module，ioctl 裡 `copy_to_user(ubuf, kbuf, 4096)` 但 `kbuf` 是 `kmalloc(64)`。預期 oops 訊息含 `usercopy:`；`hardened_usercopy=off` 開機後則讀出相鄰資料。
6. **讀 patch 討論串**：在 lore.kernel.org 搜 "Randomized slab caches for kmalloc" 與 "Prevent cross-cache attacks in the SLUB allocator"，各寫一段「作者自己承認的限制」。

## 本章重點整理

- 第二代 mitigation 的目標是**破壞攻擊者的可預測性與可轉換性**，不是守最後一步。
- 每個 mitigation 只擋一種動作；攻擊者的工作是找沒有閘門的路，防守者的工作是知道閘門在哪、缺口在哪。
- 真正在 production 預設開的以低成本的為主（`INIT_ON_ALLOC`、`HARDENED_USERCOPY`、freelist hardening）；高成本的（`RANDOM_KMALLOC_CACHES`、KCFI）要自己啟用，FGKASLR 與 SLAB_VIRTUAL 就我們查證沒進 mainline。
- 疊起來的結果：Control 那一格被 KCFI 封、spray 被 random caches 加難、leak 被 init 清掉，攻擊者被推向 data-only（Ch 29）。

## 自我檢核

- [ ] 不看筆記，能畫出攻擊鏈 Bug → Primitive → Leak → Control/Data → Privesc → Cleanup，並在每一格標上對應的 mitigation 與它擋什麼嗎？
- [ ] 能解釋為什麼 random kmalloc caches 是「per call site per boot」而不是「per call」，以及這對攻擊者意味著什麼嗎？
- [ ] 能說出 `INIT_ON_FREE` 擋 UAF 的哪一部分、擋不住哪一部分嗎？
- [ ] 面試被問「HARDENED_USERCOPY 擋什麼」，你能在 30 秒內說出擋與不擋的各兩個例子嗎？
- [ ] 想一個你最近看到的 CVE：它的利用鏈需要哪些 mitigation 沒開？如果全開，哪一步會失敗？
- [ ] 能說出 FGKASLR 為什麼擋不住 `modprobe_path` 類的攻擊嗎？
- [ ] 知道哪些機制就我們查證沒進 mainline，並且知道怎麼自己驗證嗎？

## 延伸閱讀

### Patch 與討論串（一手來源）

- **[Randomized slab caches for kmalloc()（Gong Ruiqi，LKML 2023，v3–v5）](https://lkml.rescloud.iu.edu/2307.1/07505.html)** — Gong Ruiqi 等
  - **這篇說什麼**：random kmalloc caches 的設計理由、15 份複本的取捨、Kconfig 的依賴關係。
  - **讀哪裡**：cover letter 的 motivation 與 benchmark 段，以及 Kees Cook 的 review 回覆。
  - **和本章的關聯**：本章第 1 節的所有細節的出處；前置知識是 [Ch 8](./08-slub-allocator.md)。
- **[RFC PATCH 00/14 Prevent cross-cache attacks in the SLUB allocator（Matteo Rizzo，2023-09）](https://lore.kernel.org/all/20230915105933.495735-2-matteorizzo@google.com/T/)**
  - **這篇說什麼**：SLAB_VIRTUAL 的原始提案，含威脅模型與效能討論。
  - **讀哪裡**：cover letter 的設計與限制段、以及討論串中對 config 模型的意見。
  - **和本章的關聯**：第 2 節。前置知識：[Ch 22](./22-cross-cache.md)。
- **[Function Granular KASLR（Kristen Accardi 起，v3–v10）](https://lore.kernel.org/lkml/YerMYcin4woehiL9@zn.tnic/T/)**
  - **這篇說什麼**：FGKASLR 的實作（`-ffunction-sections`、開機時洗牌）、成本、與 live patching 的衝突。
  - **讀哪裡**：任一版 cover letter 的「Performance impact」與「Security」兩段。
  - **和本章的關聯**：第 4 節；前置知識：[Ch 14](./14-kaslr-infoleak.md)。

### 專案 / 文件

- **[Kernel Self Protection Project（KSPP）](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)**
  - **讀哪裡**：Recommended Settings 頁與 Feature Status 表（哪些 mitigation 進了哪個版本）。
  - **和本章的關聯**：本章版本表的交叉驗證來源；也是 [Ch 51](./51-defense-detection.md) 的主要參考。
- **Linux kernel 原始碼 `mm/usercopy.c` 與 `include/linux/slab.h`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/mm/usercopy.c)）
  - **讀哪裡**：`__check_object_size()` 的檢查順序；`slab.h` 中 `kmalloc_type()` 與 `KMALLOC_RANDOM_START`（6.6 tag）。
  - **前提**：C 與 [Ch 2](./02-reading-kernel-source.md) 的讀碼方法。

Part 4 的地圖到此為止。下一章我們把地圖上的第一個點拆開：random kmalloc caches 到底怎麼擋 spray，攻擊者在 hash 碰撞與 cg 分流上還有多少空間。

→ [Ch 27 穿越 random kmalloc caches：hash 匹配、cg 分流、spray 策略、victim 挑選](./27-random-kmalloc-caches.md)
