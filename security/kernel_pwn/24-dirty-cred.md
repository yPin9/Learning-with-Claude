# Ch 24 — Dirty Cred / file struct：不經 ROP 的 data 攻擊

> **目標**：理解一個「不碰位址、不碰控制流，純靠物件生命週期錯位」的提權技術。Dirty Cred 把 UAF 的玩法從「用假資料佔住已釋放物件」提升成「用**系統自己產生的高權限物件**佔住低權限物件的位置」。學完你要能解釋：為什麼交換 cred 物件比改 cred 欄位更不依賴 leak、file struct 變體怎麼把唯讀檔案變成可寫、以及這條路為什麼對 CFI/FREELIST_HARDENED 免疫。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。延續 [Ch 18](./18-uaf-double-free.md) 的 UAF 與 [Ch 22](./22-cross-cache.md) 的 cross-cache。機制為主，未實測，理論預期行為。

## 為什麼要「交換物件」而不是「改欄位」

[Ch 16](./16-classic-primitives.md) 的 data-only 提權是「找到 cred，改它的 uid=0」。[Ch 23](./23-dirty-pagetable.md) 是「拿任意實體讀寫去改 cred」。這兩者都需要**定位 cred 的位址**（虛擬或實體），而定位本身常要 leak 或掃描。

Dirty Cred（Zhenpeng Lin 等提出）換了個想法：**我不去找 cred 的位址、也不自己偽造 cred，我讓系統把一個『真正的高權限 cred』放到我控制的位置。** 具體說——如果我有一個 cred 物件的 UAF，我可以：

```
1. 釋放一個「低權限」cred（我自己 process 的，uid=1000）
2. 趁那塊記憶體還在 freelist，誘導系統產生一個「高權限」cred
   （例如觸發一個會 setuid(0) / 以 root 執行的路徑，它會 prepare 一份 root cred）
   → 這份 root cred 剛好配到我剛釋放的那塊
3. 我那個還指著舊位置的 dangling 參照，現在指向一份 root cred
   → 我的 process 等於用上了 root 的身分
```

關鍵差別：第 2 步的 root cred 是**系統合法產生的真貨**，我不需要知道它的位址、不需要偽造任何欄位、不需要 leak。我只是製造了一個「生命週期錯位」——讓高權限物件落到低權限物件的槽位。

## 先建立直覺：換掉整張證件，而不是塗改證件

```
  改欄位（Ch 16/23）：          交換物件（Dirty Cred）：
  找到我的證件（cred），         把我的舊證件（低權限）丟進回收箱，
  在上面把「等級」塗改成 root。   趁回收箱還沒清，讓系統發一張新的 root 證件，
  → 要先找到證件在哪             它剛好用了回收箱裡我那張的位置。
  （leak/掃描）                  → 我手上的存根還指著那個位置，
                                現在那裡是一張真的 root 證件。
                                → 完全不用知道證件放在哪
```

這個「用真貨換位置」的思路，比「塗改」更乾淨——因為真貨能通過任何對 cred 完整性的檢查（它本來就是系統發的）。

## 機制：UAF-on-cred 的兩種方向

Dirty Cred 需要一個「能作用在 cred（或 file）物件上的 UAF」。有兩個方向：

```
方向一（釋放低、換高）：
  free(我的低權限 cred) → 系統產生高權限 cred 佔位 → 我的參照升級成高權限

方向二（釋放高、搶寫）：
  某個高權限操作持有一份 cred → 我透過 UAF 提前釋放它
  → 我用「可控 cred-size 物件」佔位，但其實更常見的是反過來
  （細節依 bug 而定）
```

由於 `cred` 住在專用 `cred_jar`（[Ch 22](./22-cross-cache.md) 講過），你通常配不出「同 cache 的可控 cred」——所以 Dirty Cred 常和 **cross-cache** 搭配：用 cross-cache 把 cred slab 的 page 和你能操作的物件湊到一起，製造對 cred 的 UAF。這也是為什麼本章排在 cross-cache 之後。

## file struct 變體：把唯讀檔案變可寫

同樣的「交換物件」思路可以套在 `struct file` 上，產生另一種強原語。`struct file` 有 `f_mode`（含 `FMODE_WRITE` 等）決定這個開啟的檔案能不能寫。Dirty Cred 的 file 變體：

```
1. 開一個「唯讀」的敏感檔案（例如 /etc/passwd，你只能 O_RDONLY）
   → 得到一個 f_mode 不含 WRITE 的 file 物件
2. 透過 UAF 釋放這個 file，誘導系統產生一個「可寫」的 file（你對某個你有寫權的檔 O_WRONLY）
   佔到同一塊
3. 你對「原本那個 fd」寫 → 它現在指向可寫的 file 物件，但...
```

更實用的形狀是反過來：讓一個指向敏感檔案（如 `/etc/passwd`）的 file，其 `f_mode` 被換成含 WRITE 的——於是你能寫 `/etc/passwd`，加一個 uid=0 的帳號，直接提權。file 變體的魅力是**連 kernel 記憶體都不用碰，最後落到「改一個 root-only 檔案」**，提權路徑極乾淨。

> cred / file 的 struct 佈局、`f_mode` 的 bit、cred_jar 的 slab 幾何都依版本，必須對你的 kernel 用 `pahole` 確認。這裡給機制，數值標依實測填入。

## 為什麼對 CFI / FREELIST_HARDENED 免疫

| mitigation | 為什麼擋不住 Dirty Cred |
|---|---|
| CFI / KCFI | 沒有任何間接呼叫、控制流轉移，純物件生命週期 |
| FREELIST_HARDENED | 不改 freelist 指標；靠的是「合法 alloc 落到 UAF 的槽」 |
| KASLR | 不需 cred 的位址——系統自己放進去 |
| cred 完整性檢查 | 用的是系統產生的真 cred，本來就合法 |

Dirty Cred 把「需要定位/偽造」這個 data-only 的最後負擔也拿掉了——它只需要一個 cred/file 的 UAF 和製造錯位的時機。代價同樣是**時機敏感**（要在釋放和「系統產生高權限物件」之間精準插入），以及常需 cross-cache 搭橋。

## 踩雷集錦

1. **以為任何 UAF 都能做 Dirty Cred**——你需要的是能作用在 **cred（或 file）物件**上的 UAF。一般 kmalloc-N 的 UAF 要先透過 cross-cache（[Ch 22](./22-cross-cache.md)）轉成對 cred_jar 的攻擊，不是直接就能換 cred。
2. **沒抓準「系統產生高權限 cred」的時機**——第 2 步要靠一個會 prepare root cred 的操作（如某 setuid 程式執行、某 privileged syscall）在對的時間點配置。時機沒對，佔位的是別的 cred 或普通物件。
3. **方向搞反**——釋放低權限換高權限 vs 釋放高權限搶位，哪個可行取決於你的 bug 能釋放誰、能控制誰的配置。先想清楚「我能釋放的是哪一邊」。
4. **file 變體忘了 f_mode 的其他 bit**——`f_mode` 不只 WRITE，換物件時要確保整個 file 物件的其他欄位（f_op、f_pos 等）也是一致可用的，否則對它操作時 kernel 可能因欄位不一致而 fault。
5. **忽略 cred 的 refcount**——cred 是 refcount 管理的。交換過程若把 refcount 弄亂（舊 cred 的 put、新 cred 的 get），可能在你用之前就被釋放或 double free。要理解交換前後的 refcount 流。

## 進階：Dirty Cred 在「Dirty 家族」的位置

把 data-only 的幾條路排在一起看層次：

```
  改欄位    ：找到 cred → 改 uid              （要定位，Ch 16/23）
  改映射    ：改 PTE → 任意實體 R/W → 改 cred  （Dirty Pagetable，Ch 23）
  換物件    ：讓系統把 root cred 放到我的槽     （Dirty Cred，本章，不用定位）
  換映射關係：唯讀檔 → 可寫（file 變體）        （本章）
```

Dirty Cred 的獨特價值是**把「定位」這個負擔完全消掉**——其他 data-only 多少要知道目標在哪，Dirty Cred 讓系統自己把目標送到你手上。這使它在「leak 很難拿」的題上特別有用。

它和 Dirty Pagetable 常是互補選項：Dirty Pagetable 給你**通用的任意實體讀寫**（能幹的事多，但要操作 PTE）；Dirty Cred 給你**針對性的身分提升**（只為提權，但更直接、更少依賴）。拿到一個 cred/file 相關的 UAF（或能 cross-cache 到那裡）時，Dirty Cred 往往是最短路。拿到更一般的 page 層原語時，Dirty Pagetable 更萬用。

## 動手練習

在自有 QEMU 靶上（機制與流程為主）：

1. 用 `pahole` 查 `struct cred` 與 `struct file` 的佈局，標出 `uid`/`f_mode` 的 offset 與 refcount 欄位。
2. 觀察 cred 生命週期：寫一個小程式 `fork` + `execve` 一個 setuid 程式，用 gdb/ftrace 觀察 `prepare_creds`/`commit_creds` 何時產生新 cred——理解「系統何時會產生高權限 cred」。
3. 分析題：給一個「能 cross-cache 到 cred_jar 的 UAF」，寫出你的 Dirty Cred 計畫（釋放誰、用什麼操作製造 root cred、怎麼確認換到）。
4. 設計 file 變體：規劃「把指向 /etc/passwd 的唯讀 file 換成可寫」的步驟，列出需要確認的 f_mode bit 與 refcount 細節。

## 本章重點整理

- Dirty Cred 用「物件生命週期錯位」讓系統把真的高權限 cred 放到低權限 cred 的槽位——不需定位、不需偽造。
- cred 住專用 cred_jar，所以 Dirty Cred 常和 cross-cache（Ch 22）搭配製造對 cred 的 UAF。
- file 變體：換掉指向敏感檔案的 file 的 f_mode（或整個 file），把唯讀變可寫，直接改 /etc/passwd 提權。
- 對 CFI / FREELIST_HARDENED / KASLR 免疫（純物件生命週期、用系統產生的真貨）；代價是時機敏感 + 需 cross-cache 搭橋。
- 在 data-only 譜系裡，它的獨特價值是消掉「定位目標」的負擔；與 Dirty Pagetable 互補。

## 自我檢核

- [ ] 能解釋「交換 cred 物件」為什麼比「改 cred 欄位」更不依賴 leak
- [ ] 能說出為什麼 Dirty Cred 常需要 cross-cache 搭配（cred 在哪個 cache）
- [ ] 能描述 file struct 變體怎麼把唯讀檔案變可寫，以及它為什麼提權路徑乾淨
- [ ] 能說出 Dirty Cred 為什麼對 CFI 和 FREELIST_HARDENED 免疫
- [ ] 能比較 Dirty Cred 與 Dirty Pagetable 的適用時機

## 延伸閱讀

- **[DirtyCred: Escalating Privilege in Linux Kernel](https://zplin.me/papers/DirtyCred.pdf)** — Zhenpeng Lin 等，CCS 2022
  - **核心貢獻**：提出 Dirty Cred，系統化「交換高/低權限物件」的提權範式，含 cred 與 file 兩種。
  - **讀哪裡**：Section 3（技術）與 Section 4（兩種物件）；評估章可略。
  - **和本章的關聯**：本章的原始來源，把機制與適用條件講到最完整。
- **[DirtyCred 演講 / Black Hat 簡報](https://www.blackhat.com/us-22/briefings/schedule/#dirtycred-cascading-kernel-exploitation-27127)**
  - **這篇說什麼**：作者親講的流程與 demo，比論文易懂。
  - **讀哪裡**：整場；交換時機與 cross-cache 搭配的部分。
  - **為什麼值得讀**：把論文的細節用更直觀的方式呈現。
- **[struct cred / struct file 與 refcount（include/linux/cred.h、include/linux/fs.h）](https://elixir.bootlin.com/linux/v6.6/source/include/linux/cred.h)**
  - **讀哪裡**：cred 的 refcount 與欄位、file 的 `f_mode`。
  - **和本章的關聯**：交換時 refcount 流與 f_mode bit 的 source 依據。
  - **前提知識**：Ch 10 refcount、Ch 22 cross-cache。

Dirty Cred 和 Dirty Pagetable 都在 object/page 層動手腳。Part 3 的最後一章再給一條不同路線：USMA——把 kernel 的 page 直接映射進 user-space，以及用 packet socket 在 page 層大規模 spray。

→ [Ch 25 — USMA 與 page-level spray：把 kernel page 映射進 userspace、pgv/packet socket](./25-usma-page-spray.md)
