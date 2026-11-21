# Ch 17 — Heap overflow in kmalloc：相鄰 object 布局與 cache 選擇

> **目標**：把溢位從 stack 搬到 heap。學完你要能回答：在 SLUB 上「相鄰的 object」是誰決定的、一個 kmalloc buffer 的 OOB write 會蓋到什麼、怎麼選 cache 讓「值得蓋的物件」排到你後面、以及為什麼 heap 題沒有「saved RIP 等你蓋」這種好事。這是 Part 3 的開場——從這裡開始，exploit 的勝負從「算對位址」轉成「把 SLUB 的場布對」。

> **環境**：Linux 6.6 LTS、x86-64、QEMU，mitigation 預設開。靶沿用 [Ch 5](./05-first-vulnerable-module.md) 的 `vulndevice.c` 骨架，但這次配置的是 `kmalloc` buffer 而非 stack buffer。exploit code 未在本環境實測，標為理論預期行為。

## 為什麼 heap overflow 和 stack overflow 是兩回事

[Ch 11](./11-stack-overflow.md) 的 stack overflow 有個天上掉下來的好處：溢位 buffer 的「正上方」就是 saved RIP——一個**固定、必然存在、蓋了就控流**的目標。你不用安排任何東西，它就在那。

heap 上沒有這種好事。你 `kmalloc` 一塊 buffer，它的「正上方」是誰？**取決於那一刻這個 slab 裡還有哪些 object、allocator 把你排在哪一格。** 可能是另一個無關緊要的 buffer、可能是空的、也可能什麼值錢的都沒有。heap overflow 的核心難題因此變成：

1. **選對 cache**：你的 buffer 和「想蓋的 victim」必須落在**同一個 kmalloc-N cache**（甚至同一個 slab），否則八竿子打不著。
2. **排對位置**：要讓 victim 剛好排在你 buffer 的**後面**（高位址方向），你的 OOB write 才蓋得到它。
3. **選對 victim**：victim 被蓋的那幾個欄位，要能換成有用的原語（函式指標 → 控流；長度欄位 → 擴大讀寫；指標 → 任意讀寫）。

這三件事就是 Part 3 前半的全部內容。本章先把「布局」講清楚，grooming 的精細手法在 [Ch 20](./20-heap-grooming.md)，具體 victim 物件在 [Ch 19](./19-spray-objects.md)，蓋出控流在 [Ch 21](./21-heap-to-rip.md)。

## 先建立直覺：溢位蓋的是「鄰居」，而鄰居是你安排的

```
  一個 kmalloc-256 的 slab（每格 256 bytes）：

  ┌──────────┬──────────┬──────────┬──────────┐
  │ 別人的   │ 你的     │ victim   │ 空        │
  │ object   │ buffer   │ object   │          │
  └──────────┴──────────┴──────────┴──────────┘
                  │ OOB write 往高位址溢 ─────▶
                  └──── 蓋到 victim 的前幾個欄位

  關鍵：victim 排在你後面，不是碰運氣——
  是你先把 slab「填到剩可預測的空位」，
  再讓 alloc 順序變成「你的 buffer → victim」。
```

和 stack 的差別一句話：**stack 的鄰居（saved RIP）是編譯器決定的、固定的；heap 的鄰居是 allocator 當下的狀態決定的、而那個狀態你能操控。** 把「操控 allocator 狀態讓鄰居是我要的」做好，就是 heap 利用的基本功。

> 如果「slab、cpu_slab、freelist、LIFO」這些詞你還沒有肌肉記憶，先回 [Ch 8](./08-slub-allocator.md)。本章假設你知道「同 size 的 alloc 進同一個 cache」「free 的 object 進 freelist 頂端、下一個同 size alloc 立刻拿回」這兩件事。

## cache 選擇：你的 buffer 落在哪，決定能打誰

`kmalloc(n)` 依 n 進對應的 kmalloc-N cache（[Ch 8](./08-slub-allocator.md) 的對齊表）。所以攻擊的第一步是**算清楚你的 vuln buffer 和目標 victim 各自落在哪個 cache**：

- 你的 buffer 大小你（半）可控——很多靶讓你指定配置大小，或你能選觸發路徑。
- victim 的大小由它的 struct 決定（例如 `tty_struct` 落在某個特定 cache，[Ch 19](./19-spray-objects.md) 會列常用物件各自的 size/cache）。
- **兩者必須同 cache**，overflow 才可能蓋到。若 victim 在 kmalloc-512 而你只能配 kmalloc-64，這條路不通——要嘛換 victim、要嘛想辦法讓你的配置也落在 512（例如配一個更大的 buffer）。

```
  你能配的大小   victim 的 cache    可行？
  ─────────────  ────────────────  ──────
  48 (→ kmalloc-64)   kmalloc-64        ✓ 同 cache，可相鄰
  48 (→ kmalloc-64)   kmalloc-256       ✗ 不同 cache，碰不到
  200(→ kmalloc-256)  kmalloc-256       ✓
```

> 現代複雜化：`GFP_KERNEL_ACCOUNT` 的物件走 `kmalloc-cg-N`（[Ch 9](./09-slub-hardening.md)），和一般 `kmalloc-N` **分流**。所以「同 size」還不夠，要「同 size 且同帳戶類別」。選 victim 時要確認它的配置 flag。random kmalloc caches（[Ch 27](./27-random-kmalloc-caches.md)）更把同 size 拆成多個 cache——那是 Part 4 的硬仗，本章先假設標準情形。

## 靶：一個 heap 版的 overflow

把 [Ch 5](./05-first-vulnerable-module.md) 的靶改成在 heap 上開 buffer：

```c
/* vulndevice.c 的 heap 變體（節錄）：kmalloc 一塊，copy_from_user 無上界 */
struct vuln_req { unsigned long size; void __user *buf; };

static char *g_obj;                 /* 指向我們 kmalloc 出來的 buffer */

static long vuln_do_alloc(struct vuln_req *req)
{
    g_obj = kmalloc(req->alloc_size, GFP_KERNEL);   /* 配置大小（半）可控 */
    return g_obj ? 0 : -ENOMEM;
}

static long vuln_do_write(struct vuln_req *req)
{
    /* BUG：req->size 無上界檢查，可大於當初 kmalloc 的大小 → heap OOB write */
    if (copy_from_user(g_obj, req->buf, req->size))
        return -EFAULT;
    return 0;
}
```

bug 形狀和 stack 版同構（`copy_from_user` 長度無檢查），但後果不同：溢出 `g_obj` 的部分會蓋到**同 slab 裡排在 `g_obj` 後面的 object**——那個 object 是誰，由你怎麼安排配置順序決定。

## 把 victim 排到你後面：最小 grooming

要讓 victim 緊跟在你的 buffer 後面，基本手法是利用 SLUB 的**順序性**：同一個 cpu_slab 上，連續的 alloc 傾向拿到**連續的格子**（freelist 是 LIFO，但一個新 slab 剛切好時格子是順序串的）。一個最小的「排鄰居」流程：

```
1. 大量 alloc 同 size 的物件，把當前 slab「用舊的空洞填滿」
   → 逼 allocator 開一個乾淨的新 slab（格子順序排列）
2. 在這個乾淨 slab 上：
     alloc 你的 vuln buffer          （拿到格子 i）
     alloc 一個 victim object        （拿到格子 i+1，就在你後面）
3. 觸發 overflow：vuln buffer 的 OOB write 蓋到 victim（格子 i+1）
```

這只是概念骨架——實務上「怎麼確定拿到乾淨 slab」「怎麼確定 victim 真的在你後面而不是前面」有很多細節，那是 [Ch 20](./20-heap-grooming.md) 的主題。本章你要先建立的是**意圖**：heap overflow 前一定有一段「把場布成我要的樣子」的動作，不能 alloc 完就直接溢。

## 選 victim：蓋哪幾個欄位值錢

victim 被你蓋的是它**開頭的幾個欄位**（你從低位址往高位址溢）。所以好的 victim 是「開頭就有值錢欄位」的 struct。三類最常見：

| victim 類型 | 蓋它的開頭能拿到什麼 | 代表物件 |
|---|---|---|
| 開頭是**函式指標 / ops 指標** | 蓋成你的位址 → 之後呼叫它就控流 | `tty_struct`（ops）、`seq_operations` |
| 開頭是**長度 / 大小欄位** | 蓋大它 → 後續讀寫越界，升級成更強原語 | 各種帶 len 的 buffer、`msg_msg` |
| 開頭是**指標（指向 data）** | 蓋成任意位址 → 讀寫那個位址 = 任意讀寫 | `msg_msg`（next）、帶 data pointer 的物件 |

這就是為什麼 [Ch 10](./10-vulnerability-taxonomy.md) 說「原語升級」是 exploit 的本質：一次 heap overflow 蓋一個長度欄位，可能就把「只能蓋相鄰 object」升級成「任意讀寫」，再從任意讀寫走 [Ch 16](./16-classic-primitives.md) 的 data-only 提權。victim 的選擇直接決定你能升到多強。[Ch 19](./19-spray-objects.md) 會把常用 victim 的欄位佈局一個個攤開。

## 一個常見的升級鏈：蓋長度欄位

具體看「蓋長度」怎麼升級。假設 victim 開頭是 `{ size_t len; char *data; ... }`，而有另一條路徑會根據 `len` 把 `data` 讀回 user：

```
原語 before：relative OOB write，只能蓋 victim 開頭幾 bytes
   │ 把 victim.len 從 0x20 蓋成 0x1000
   ▼
原語 after：用 victim 的 read 路徑讀 0x1000 bytes
   → 越界讀出 victim 之後一大片記憶體（含其他物件的指標）
   → info leak（KASLR base，Ch 14）
   → 甚至配合 write 路徑變成更大範圍的 write
```

一次 OOB write（弱），透過「蓋長度」換成「大範圍 OOB read/write」（強）。這種「用一個小原語撬開一個大原語」的思路，是 heap 利用和 stack 利用最不一樣的地方——stack 你直接控流，heap 你常要繞兩三手。

## 踩雷集錦

1. **以為 alloc 完就能直接溢到 victim**——不排場的話，你 buffer 後面是誰完全隨機，大概率什麼值錢的都蓋不到，或蓋到一半 slab 邊界外觸發別的 corruption 直接 panic。heap overflow **必然**伴隨 grooming，不是可選步驟。
2. **victim 和 vuln buffer 不同 cache 還硬打**——size 落在不同 kmalloc-N，兩者永遠不相鄰。先算清楚兩者的 size → cache，對不上就換 victim 或換配置大小。別對著不同 cache 的物件猛 spray。
3. **忽略 `kmalloc-cg-N` 分流**——victim 若是 `GFP_KERNEL_ACCOUNT` 配的（很多 user 可觸發的物件是），它在 `kmalloc-cg-N`，和你一般 `kmalloc-N` 的 buffer 不同 slab。看物件的配置 flag，別只看 size。
4. **溢位長度沒控好，蓋過頭蓋爆 slab metadata / 下下個 object**——你只想蓋 victim 開頭幾個欄位，卻灌太長，把 victim 整個和後面的物件都蓋爛，觸發 `kfree` 時 freelist corruption 直接 panic。溢位長度要精確到「剛好蓋到要改的欄位」。
5. **把 stack 的直覺套過來找「saved RIP」**——heap 上沒有 saved RIP。控流要透過「蓋一個 victim 的函式指標，再觸發它被呼叫」間接達成（Ch 21）。別在 heap dump 裡找 return address。

## 進階：為什麼 heap 是 kernelCTF 的主戰場

stack overflow 題在現代 kernelCTF 幾乎絕跡——真實 kernel 的 syscall 路徑很少留下「user 可控長度直接灌進固定 stack 陣列」這種洞，而且 stack canary 讓它更難。真實的洞絕大多數在 heap：某個子系統的 object 有 UAF、有 refcount bug、有 OOB，而這些 object 都在 SLUB 上。所以 Part 3 的每一章（UAF、cross-cache、Dirty Pagetable……）本質上都在回答同一個問題的不同變形：**我手上這個 heap 原語（蓋相鄰 / 用已釋放 / 跨 cache），怎麼一步步升級成任意讀寫或控流？**

本章的 heap overflow 是最直白的那個起點（「蓋相鄰」）。下一章的 UAF 起點不同（「用已釋放的」）但終點相同——都要靠 spray victim、都要升級原語。你會發現 Part 3 後面所有技術共用同一套肌肉：算 cache、排場、選 victim、升級。把這套在最簡單的 heap overflow 上練熟，後面只是換起手式。

一個實戰建議：拿到一個 heap bug，先畫三欄——「我的原語是什麼（Ch 10 四軸）」「我的 buffer 在哪個 cache」「這個 cache 裡有哪些值錢的 victim（Ch 19）」。三欄填完，exploit 路線大概就浮出來了。

## 動手練習

在自有 QEMU 靶上，用上面的 heap 版 `vulndevice.c`：

1. 配一個 kmalloc-256 的 vuln buffer，不排場直接溢 512 bytes，看 dmesg——多半是 `kfree` 時 freelist corruption 或 slab 相關 BUG。體會「不排場的 heap overflow 就是亂蓋」。
2. 加最小 grooming：先 spray 一批同 size 物件逼開新 slab，再 alloc vuln buffer + 一個你自製的 victim struct（開頭放一個好認的 magic 值），溢位精確蓋掉 victim 開頭 8 bytes，用 victim 的 read 路徑確認 magic 被你改了。
3. 把 victim 的開頭改成一個「長度欄位」，做一次「蓋長度 → 越界讀」的原語升級，讀出相鄰物件的內容。
4. 觀察：故意讓 vuln buffer 配成 kmalloc-64、victim 配成 kmalloc-256，確認無論怎麼 spray 都蓋不到——親眼驗證 cache 選擇的硬約束。

## 本章重點整理

- heap overflow 沒有固定的 saved RIP；溢位蓋到的「鄰居」由 allocator 當下狀態決定，而那個狀態你能操控。
- 三個核心：選對 cache（vuln buffer 與 victim 同 kmalloc-N、同帳戶類別）、排對位置（victim 在你後面）、選對 victim（開頭欄位值錢）。
- victim 三類：函式/ops 指標（控流）、長度欄位（升級讀寫）、data 指標（任意讀寫）。
- heap 利用常要「用小原語撬大原語」，不像 stack 直接控流。
- heap 是現代 kernelCTF 主戰場；Part 3 所有技術共用「算 cache→排場→選 victim→升級」這套肌肉。

## 自我檢核

- [ ] 能解釋為什麼 heap overflow 的「鄰居」和 stack overflow 的「saved RIP」本質不同
- [ ] 能說出 heap overflow 成功的三個前提（cache、位置、victim），並各舉一個失敗情境
- [ ] 能解釋 `kmalloc-cg-N` 分流為什麼讓「同 size」還不夠
- [ ] 能描述一條「蓋長度欄位 → 越界讀 → info leak」的原語升級鏈
- [ ] 拿到一個 heap bug，能用「原語/cache/victim」三欄法推出大致路線

## 延伸閱讀

- **[pawnyable — Linux Kernel: Heap Overflow（Holstein v2/v3）](https://pawnyable.cafe/linux-kernel/LK01-3/)**
  - **這篇說什麼**：用和本章同構的靶，示範 heap overflow 蓋 `tty_struct`、做原語升級，附完整 module 與 exploit。
  - **讀哪裡**：整篇；「heap overflow」與「tty_struct」兩段對應本章與 Ch 19/21。
  - **為什麼值得讀**：把「排場 → 蓋 victim → 升級」落到可跑的 code，是本章最直接的實作搭檔。
- **[SLUB freelist 與 object 佈局（mm/slub.c）](https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c)**
  - **讀哪裡**：`alloc`/`free` 的 slow/fast path 與 object 在 slab 內的排列；配合 Ch 8。
  - **和本章的關聯**：理解「連續 alloc 為什麼傾向連續格子」「溢位蓋過頭為什麼在 kfree 時爆」，要讀這段 source。
  - **前提知識**：Ch 8 的 SLUB 心智模型。
- **[A systematic study of elastic objects in kernel exploitation](https://dl.acm.org/doi/10.1145/3372297.3423353)** — Chen 等，CCS 2020
  - **核心貢獻**：形式化「可控長度物件」怎麼把 OOB 升級成 leak/overwrite，正是本章「蓋長度撬大原語」的學術版。
  - **讀哪裡**：Section 3（elastic object 分類）與 Section 4（利用）。
  - **和本章的關聯**：本章的升級鏈在這篇有系統化的對應與更多 victim 範例。

heap overflow 是「蓋相鄰」的起手式。但現代 kernelCTF 更常見的起點是「用已經釋放的物件」——UAF。下一章換這個起手式：bug 不給你越界，而給你一個指向已釋放記憶體的指標，玩法完全不同，但一樣要 spray victim、一樣要升級原語。

→ [Ch 18 — UAF / Double Free：SLUB freelist corruption 與 poisoning](./18-uaf-double-free.md)
