# Ch 27 — UAF on Windows

> **目標**：徹底搞清楚 UAF（Use-After-Free）的成因與完整利用步驟：free → reclaim → 控制被釋放物件的內容 → 觸發舊指標的使用（常是虛擬呼叫→ Ch 30）；理解 LFH 的 reclaim 機制對 UAF 的影響（同 bucket 才能重新佔用、randomization 的對抗策略）；能把 UAF 和 type confusion 的關係說清楚；能選擇合適的佔位物件（可控大小與內容）。

## 為什麼需要這個？

你在 glibc 上做的 UAF 利用通常很暴力：`tcache dup`（free 兩次，偽造 next 指標）或 `fastbin dup`（類似），讓下一次 malloc 返回任意位址，再寫任意內容——利用直接建立在 free list 的指標上。

Windows 的 UAF 不一樣。NT Heap 的 LFH 路徑沒有鏈式 next 指標，free slot 只是 BusyBitmap 的一個 bit 被清掉。你不能偽造 next 指標讓 malloc 返回任意位址。但 UAF 的利用在 Windows 上**依然強大**，只是思路完全不同：

**核心思路**：被 free 的物件（slot）仍然在記憶體裡（UserBlocks 沒有被 OS 回收）。攻擊者讓一個惡意物件**佔回（reclaim）**這個 slot，把 slot 的內容填滿攻擊者控制的資料——特別是把虛擬表指標（vptr）的位置填成 fake_vtable 的位址。然後觸發「舊的（dangling）指標」對被釋放物件的使用，通常是呼叫一個虛擬函式，直接跳到攻擊者控制的位址。

這是 Windows kernel exploit、瀏覽器 exploit（你在 browser_pwn 做過）的標準 UAF 路徑，也是大量 Windows 公開 CVE 的利用方式。

## 先建立直覺：UAF 的生命週期

想像物件是一個「租房合約」：

```
  正常生命週期：
  ┌─────────────────────────────────────────────────────────────────┐
  │ 分配：HeapAlloc → 物件誕生在 slot X，有 vptr 和各欄位           │
  │ 使用：多個地方儲存指向 slot X 的 pointer（ref）                 │
  │ 釋放：HeapFree → slot X 的 BusyBitmap 位清 0，物件「死了」     │
  │ （但 pointer 還在！dangling pointer = 指向已 free 的 slot X）   │
  └─────────────────────────────────────────────────────────────────┘

  UAF 的漏洞條件：
  ┌─────────────────────────────────────────────────────────────────┐
  │ 程式程式碼還保留著對 slot X 的 pointer（忘了清空或清空太晚）    │
  │ 之後又透過這個 dangling pointer 讀/寫/呼叫 slot X 的內容       │
  └─────────────────────────────────────────────────────────────────┘

  攻擊者插入的步驟：
  ┌─────────────────────────────────────────────────────────────────┐
  │ 在 HeapFree 之後、Use 之前                                      │
  │ 分配一個大小相同的攻擊物件（sprite）→ reclaim slot X           │
  │ 把 slot X 的內容填成攻擊者控制的資料（特別是 vptr 位置）       │
  │ 讓 Use 發生 → 觸發 vptr → 跳到 fake vtable → 控制流劫持        │
  └─────────────────────────────────────────────────────────────────┘
```

和 heap overflow 的比較：

| | heap overflow | UAF |
|---|---|---|
| 前提 | attacker 和 target 相鄰 | free 後 dangling pointer 還在 |
| 目標 | overflow 蓋 target 的欄位 | reclaim 後控制 freed slot 的內容 |
| timing | 物件都存活時 | free 之後、Use 之前的窗口 |
| 難點 | 相鄰性（grooming） | reclaim 的精確度（同 bucket + randomization） |

## UAF 的完整利用步驟

### 步驟 1：了解目標物件（victim object）

你需要知道：
- victim object 的 size（決定它在哪個 LFH bucket）
- victim object 裡 vptr/函式指標的偏移（決定你要填什麼到哪裡）
- 誰持有 dangling pointer（是全域變數？還是其他物件的欄位？）
- Use 的時機是什麼（是哪個 API 呼叫、哪個事件觸發）

```
  典型 C++ victim object（size = 0x40）：
  +0x00: vptr              ← 8 bytes，指向 vtable（攻擊目標）
  +0x08: state             ← 4 bytes
  +0x0c: refcount          ← 4 bytes（引用計數，free 後可能還在被遞減）
  +0x10: data_ptr          ← 8 bytes，指向其他物件
  +0x18: callback          ← 8 bytes，函式指標
  +0x20: ...               ← 剩餘欄位
```

### 步驟 2：觸發 free（製造懸空指標）

```
  victim_obj* p = new VictimClass();  // p 指向 heap slot X
  // 某些程式碼把 p 存到了 cache、全域表、或另一個物件的欄位裡
  p->Release();  // 或 delete p; / HeapFree(...)
  // p（或 cache 裡的副本）現在是 dangling pointer，指向已 free 的 slot X
  // slot X 的 BusyBitmap 位被清掉，slot 可被重新分配
```

### 步驟 3：reclaim——讓攻擊物件佔回 slot X

這是 UAF 利用的核心難點。你需要：
1. 找到一個「佔位物件（sprite object）」——大小和 victim 相同（同 LFH bucket）、內容可以完全控制
2. 在 victim free 之後立刻（在 Use 發生前）分配這個佔位物件
3. 等佔位物件佔回 slot X（不確定，取決於 LFH randomization）

```
  reclaim 之後的 slot X（原本是 victim object）：
  +0x00: fake_vptr          ← 攻擊者填進去的 fake vtable 指標
  +0x08: 0x0000000000000000  ← 或者其他可以讓程式不 crash 的值
  +0x0c: 0x00000001          ← fake refcount
  +0x10: fake_data_ptr       ← 可選：進一步控制物件的其他欄位
  +0x18: ...
```

### 步驟 4：觸發 Use（dangling pointer 的使用）

```
  // 靶程式的某段程式碼拿出 dangling pointer 並使用它
  cached_ptr->virtual_method();
  // 這實際上是：
  // mov rax, [cached_ptr]         ; 讀 vptr → fake_vtable 位址
  // call qword ptr [rax + offset] ; 跳到 fake_vtable 裡的函式指標
  // → 跳到攻擊者控制的位址
```

### UAF 完整時序圖

```
  時間線（從上到下）：

  t=0: victim_obj = alloc(size=0x40)    ← slot X 分配
       ┌────────────────────────────────────────────────┐
       │ slot X: [vptr: &RealVtable][fields...]         │
       └────────────────────────────────────────────────┘
       dangling_ptr = victim_obj     ← cache 儲存指標
       (victim 正常運作)

  t=1: free(victim_obj)                 ← slot X 被 free
       ┌────────────────────────────────────────────────┐
       │ slot X: [BusyBitmap bit 清 0，記憶體不動]      │
       │         [vptr 等原始內容可能還在，也可能被清]  │
       └────────────────────────────────────────────────┘
       dangling_ptr 還在！指向已 free 的 slot X

  ──── 攻擊視窗開始 ──────────────────────────────────────

  t=2: sprite_obj = alloc(size=0x40)    ← 攻擊者分配同 size 物件
       希望 LFH 給我們 slot X（不保證，需 grooming）
       ┌────────────────────────────────────────────────┐
       │ slot X: [fake_vptr: &FakeVtable][...填充...]   │  ← 攻擊者控制
       └────────────────────────────────────────────────┘

  t=3: dangling_ptr->virtual_method()   ← dangling pointer 被使用
       mov rax, [dangling_ptr]          ← 讀到 fake_vptr
       call qword ptr [rax + N]         ← 跳到 FakeVtable[N/8]
       → 控制流劫持！

  ──── 攻擊視窗結束（已 getshell 或 crash） ──────────────
```

## LFH 對 reclaim 的影響：同 bucket 才能佔回

UAF 的 reclaim 步驟在 LFH 環境下有一個硬性制約：**sprite object 的大小必須落在和 victim 相同的 LFH bucket**，才有可能佔回 victim 的 slot。

```
  LFH bucket 8（管 57–64 bytes，即 size class 0x40）：
  UserBlocks：
  ┌────────────────────────────────────────────────────────────────────────┐
  │ UB header │ slot0  │ slot1  │ slot2  │ slot3  │ slot4  │ slot5  │ ... │
  │           │[busy]  │[free]  │[busy]  │[busy]  │[free]  │[busy]  │     │
  └────────────────────────────────────────────────────────────────────────┘
                         ↑                          ↑
                    victim free 後              另一個 free slot
               BusyBitmap bit[1] = 0         BusyBitmap bit[4] = 0

  sprite alloc(size=0x40)：
  LFH 的 PRNG 從 {slot1, slot4} 中選一個（隨機）
  → 50% 機率選到 slot1（victim 的 slot）
  → 50% 機率選到 slot4（不是 victim，UAF 利用失敗）

  對策：減少 UserBlocks 裡的 free slot 數量
  → 把其他 free slot 都填滿（spray），只剩 victim 那個 slot 是 free 的
  → 下次 alloc 必定選到 victim 的 slot（確定性回來了）
```

**不同 bucket 的 sprite 不可能 reclaim**：

```
  bucket 9（管 65–80 bytes，size class 0x50）的 sprite：
  從 bucket 9 的 UserBlocks 取 slot，根本不是 bucket 8 的 UserBlocks
  → victim slot（在 bucket 8 的 UB）永遠不會被 bucket 9 的 sprite 佔到
```

這是 **UAF 利用的 bucket 匹配原則**：`sizeof(sprite) == sizeof(victim)` 必須落在同一個 bucket，不只是「相同的 size」——例如 57 bytes 和 63 bytes 都在 bucket 8，sprite 用 57 bytes 也能 reclaim victim 的 63 bytes slot（因為 LFH slot 大小是 bucket 的 BlockSize，對 bucket 8 就是 0x40 = 64 bytes，所有在 bucket 8 的分配都用同一個 slot 大小）。

## LFH Randomization 與 reclaim 的對策

### Win 8+ 的 allocation randomization

Win 8 以前的 LFH：分配是順序的，reclaim 的成功率幾乎 100%（free 哪個 slot，下一個 alloc 就拿回那個 slot）。

Win 8+ 的 LFH：分配走 PRNG，reclaim 是機率性的。在 UserBlocks 有 N 個 free slot 的情況下，每次 alloc 的成功機率 = 1/N。

**對策一：壓制法（把其他 free slot 填滿）**

在 victim free 之前，先把 victim 的 UserBlocks 裡的其他 free slot 全部填滿。victim free 後，UserBlocks 只有一個 free slot——然後 spray sprite object，必定 reclaim victim 的 slot。

```
  填滿其他 free slot 的步驟：
  1. 分配大量同 bucket 的物件，填滿整個 UserBlocks（所有 slot busy）
  2. 有選擇地 free 幾個 slot（製造「洞」），但不包含 victim
  3. 用 sprite 把這幾個洞填回去（把洞全 reclaim）
  4. 現在 UserBlocks 所有 slot 都是 busy，只有 victim 本身是唯一的 free slot
  5. 觸發 victim free
  6. 立刻 alloc sprite → 必定取到 victim 的 slot（唯一的 free slot）
```

**對策二：大量 spray（統計壓制）**

如果無法精確控制 UserBlocks 的 free slot 數量（比如行程背景一直在分配/釋放），用大量的 spray 統計壓制：

```
  victim free 後立刻分配 N 個 sprite（N 越大成功率越高）
  每個 sprite 都嘗試把自己寫成攻擊內容（fake vptr）
  最終有一個會佔到 victim 的 slot
  Use 觸發時，只要其中一個 sprite 佔到 victim，就能利用
```

但這不保證確定性。多次嘗試、加上對 Use 的時機控制，是一個常見但不夠精確的手法。更精確的做法看 Ch 28。

## 佔位物件選擇：可控大小與內容

reclaim 的效果取決於「sprite object 的內容你能控制多少」。

### 理想的 sprite object

1. **大小精確匹配 victim 的 LFH bucket**：大小必須落在同一個 bucket。
2. **內容完全可控**：sprite object 的每個 byte 都由攻擊者決定（例如，分配一個 buffer，填入攻擊者數據，buffer 的前 8 bytes = fake_vptr）。
3. **不干擾程式邏輯**：sprite object 被分配後，靶程式不要對它做複雜操作（會干擾你填進去的 fake_vptr）。

### 常見 sprite object 選擇

**場景一：純資料物件（最理想）**

如果靶程式有一個 API 允許「分配任意大小、任意內容的 buffer」，用這個：

```
  例：CreateHeapBuffer(size, data, len)
  → 直接在 heap 上分配 size bytes，內容填 data[0..len-1]
  → 大小和內容都完全可控
```

**場景二：字串/Blob 物件**

很多 Windows 元件對外暴露「儲存一段任意 bytes 的物件」的能力，例如：

```
  BSTR（COM 字串）：HeapAlloc + 前 4 bytes 存長度 + 後面是 UTF-16 字元
  → 如果 BSTR 大小落在 victim 的 bucket，可以用 BSTR 當 sprite
  → 內容由你決定（字串的內容就是 fake_vptr + 其他欄位）
```

**場景三：JavaScript/腳本引擎物件（瀏覽器場景）**

你在 browser_pwn 課做過。JS 引擎的 ArrayBuffer、TypedArray、String 等物件的後端是 heap 分配的，大小可控，內容可填任意 bytes。在瀏覽器 UAF 利用裡，JS 物件是最常見的 sprite。

**場景四：Windows kernel 的 Pool spray（kernel UAF）**

在 kernel UAF 裡，佔位物件通常是 kernel pool 裡的「可控大小的 kernel object」，例如 `IoBuildDeviceIoControlRequest` 建立的 IRP，或 pipe buffer。kernel 的 UAF 在 kernel_pwn 課有完整一章，這裡只建立概念。

### sprite 選擇的反面教材

```
  不好的 sprite：
  1. 大小和 victim 不同 bucket → 不可能 reclaim
  2. 前 8 bytes（vptr 位置）是由 heap manager 填的（例如 free chunk 的 Flink）
     → 你沒辦法控制 vptr 位置的內容
  3. 物件被分配後立刻被靶程式讀取 vptr → 觸發 Use 前就被驗了
```

## type confusion 與 UAF 的關係

UAF 的本質是「指標仍指向一個記憶體位址，但那個位址的語意改變了」。如果 sprite object 的型別和 victim 不同，堆 manager 成功 reclaim 了，但靶程式以「舊的型別」解釋 sprite 的內容——這就是 type confusion。

```
  victim type:  ClassA（有 vptr，vptr 在 +0x00）
  sprite type:  ClassB（沒有 vptr，+0x00 是一個普通欄位）

  reclaim 後，dangling ptr（型別標注為 ClassA*）指向的其實是 ClassB 物件
  → dangling_ptr->virtual_method()
  → 讀 +0x00，解釋為 vptr → 但其實是 ClassB 的普通欄位
  → 跳到「ClassB 的普通欄位」所代表的地址
  → 攻擊者控制 ClassB 物件的 +0x00，就等於控制了跳轉目標
```

這就是 type confusion：**不是同一個型別的兩個物件共用了同一個 slot**，一個被當成另一個解釋。

在 browser_pwn 課（V8 的 type confusion），你做的是「讓 V8 認為某個 HeapObject 的 Map 是另一個型別的 Map」，本質和這裡的 UAF type confusion 相同：指標沒錯，但型別解讀錯了，讓原本的資料被當成指標或反之。

UAF type confusion 在 Windows 核心漏洞（kernel object 的型別混淆）和瀏覽器漏洞裡極其常見，是「UAF → 控制流劫持」最常見的橋接方式。

## glibc tcache/fastbin reclaim UAF 的對比

| 維度 | glibc tcache/fastbin UAF | Windows LFH UAF |
|---|---|---|
| free 後 slot 的狀態 | chunk 被插入 tcache 鏈（next 指標在 user ptr +0x00） | BusyBitmap bit 清 0，記憶體內容不動（可能有 OS fill） |
| reclaim 機制 | malloc 從 tcache 鏈頭取——確定性 LIFO | LFH 用 PRNG 選 free slot——機率性 |
| 攻擊者控制 next 的方式 | 直接寫 free chunk 的 user ptr（dangling ptr 寫入） | 分配 sprite object 佔回 slot，填入攻擊者控制的內容 |
| reclaim 成功率 | 接近 100%（純 LIFO，free 後立刻 alloc 必定拿回） | 機率性（1/N，N = UserBlocks 的 free slot 數） |
| 對策（提升確定性） | 幾乎不需要（tcache 無隨機化） | 壓制法（把其他 free slot 填滿）或大量 spray |
| 主要利用原語 | tcache dup → arbitrary alloc → arbitrary write | reclaim → 填 fake_vptr → 虛擬呼叫劫持 |
| glibc 2.32+ 保護 | PROTECT_PTR（next 指標 XOR 加密） | （LFH 無 next 指標，這個保護不適用） |

**最大差異**：glibc tcache 的利用「直接建立在 free list 指標的偽造上」（改 next = 任意位址），Windows LFH 的利用「建立在把任意內容寫進 reclaimed slot 上」（不需要偽造任何 LFH 內部指標）。兩者的防護方向因此完全不同：glibc 加密 next 指標（PROTECT_PTR），Windows 加密 SubSegment 指標但不加密 slot 內容——slot 內容本來就是給使用者用的。

## 底層機制：LFH slot free 後的記憶體狀態

```
  slot X 被 HeapFree 後（LFH 路徑）：

  HeapFree 內部邏輯：
  ┌───────────────────────────────────────────────────────────────┐
  │ 1. 識別 ExtendedBlockSignature = 0x80 → 走 LFH 路徑          │
  │ 2. 找到所屬 SubSegment → 找到 UserBlocks                      │
  │ 3. 計算 slot index：index = (slot_ptr - UB_data_start) / BlockSize │
  │ 4. BusyBitmap.bit[index] = 0  ← slot 標記為 free             │
  │ 5. SubSegment.FreeCount++                                     │
  │ 6. （記憶體內容不清除，原 vptr 等欄位可能還在那裡）          │
  └───────────────────────────────────────────────────────────────┘

  slot X 的記憶體狀態（free 後，未被 OS fill 的情況）：
  ┌────────────────────────────────────────────────────────────────┐
  │ header: [ExtSig=0x80|...]                                      │  ← LFH header，Flags 的 busy bit 清 0
  │ +0x00:  [原 vptr 可能還在！] / [heap manager 填的 pattern]     │  ← 取決於是否有 fill 機制
  │ +0x08:  [原 state 欄位...]                                     │
  │ ...                                                            │
  └────────────────────────────────────────────────────────────────┘
```

**是否有 fill pattern？**：如果 heap 建立時或 ProcessHeap 的設定開了 `HEAP_FREE_CHECKING_ENABLED`，free chunk 的 user data 會被填成 `0xFEEEFEEE`（4-byte pattern），這樣你讀 dangling pointer 看到的是 `0xFEEEFEEE` 而不是原始欄位。但在一般 release 環境，fill 通常沒有開。

> **未實測，理論預期**：在 Page Heap 開啟時（`gflags +hpa`），free 後的記憶體行為會更激進（可能全填 0 或特定 pattern）；release 環境通常沒有 fill，dangling pointer 讀到的是原始欄位或 heap manager 的輕量處理。

**這意味著**：在有些 UAF 漏洞裡，即使沒有 reclaim，dangling pointer 讀到的 vptr 也可能是舊的（原始的 vptr）。攻擊者 reclaim 前如果能「讀」dangling pointer 的 vptr（info leak），可以洩漏原物件類別的 vtable 位址，進而算出 module base——這是 UAF 在 info leak 上的常見用途。

## 踩雷集錦

1. **「UAF 一定要 free 之後立刻 reclaim」**：不一定。很多 UAF 漏洞的利用視窗很寬——victim free 後，靶程式還要做一堆其他事情才觸發 Use。攻擊者有充裕的時間做 spray/grooming。重要的是在 Use 之前完成 reclaim，而不是在 free 之後立刻完成。

2. **「sprite object 只要 size 相同就能 reclaim」**：需要在同一個 LFH bucket。0x38 bytes 和 0x40 bytes 都在 bucket 8（1–64 bytes 的 granularity 是 8），但 0x40 bytes 和 0x50 bytes 分別在 bucket 8 和 bucket 9，不能互相 reclaim。要看 Ch 15 的 bucket 對應表確認。

3. **「reclaim 後，slot 裡的 vptr 位置就是你的內容」**：前提是 sprite object 的「第 0 個 byte」對應 victim object 的「vptr 欄位位置」。sprite 和 victim 的欄位 layout 可能不同——如果 sprite 是另一種物件，它的 +0x00 可能是長度欄位，不是 vptr。要精確計算：victim.vptr 在 slot 的哪個 offset，sprite 的那個 offset 填的是什麼。

4. **「LFH free 後，slot 記憶體立刻回收給 OS」**：完全錯誤。LFH slot 屬於 UserBlocks，只要 UserBlocks 裡還有任何一個 busy slot，整個 UserBlocks 都不會還給 backend（更不會還給 OS）。dangling pointer 指向的記憶體在很長一段時間內都是 committed、可讀寫的。這是 UAF 利用視窗能那麼長的根本原因。

5. **「type confusion 只在 C++ 多型才有」**：錯。任何把「同一塊記憶體當成不同型別解釋」的情況都是 type confusion。C 語言裡的 union、協議解析器把 buffer 的前 N bytes 當成不同型別的 header……都是潛在的 type confusion 場景。UAF type confusion 只是其中最常見的利用形式。

## 對比與取捨

| 面向 | UAF（Windows LFH） | heap overflow（Ch 26） |
|---|---|---|
| 觸發條件 | free 後 dangling pointer 存在 | 物件存活時有越界寫 |
| 利用視窗 | free 到 Use 之間（可能很長） | overflow 發生到程式使用被蓋欄位之間 |
| grooming 難點 | reclaim 的確定性（同 bucket + randomization） | 相鄰性（同 slot 的 attacker 和 target） |
| info leak 需求 | 必要（需要知道 fake vtable 的位址） | 必要（需要知道目標位址、header 原始值） |
| 攻擊的精確性 | 需要控制 sprite 的內容和大小 | 需要精確計算 overflow 長度和 payload 佈局 |
| 結合方式 | UAF 取 reclaim，heap overflow 打 layout | 常見：UAF leak vptr → 算 module base → ROP |

## 進階：再往深一層

### 引用計數 (refcount) UAF

很多 Windows 物件用引用計數管理生命週期（COM 的 IUnknown、kernel object 的 ObpDecrementReferenceCount）。典型的 UAF 場景：

```
  obj->AddRef()  // refcount = 2
  obj->Release() // refcount = 1，不 free
  obj->Release() // refcount = 0，free！但...
  // 如果兩個 Release 在不同執行緒，或呼叫順序有 race condition：
  // 可能在 free 發生後，另一個執行緒還持有舊指標並呼叫方法
```

引用計數 UAF 是 Windows 核心漏洞的大宗（例如 GDI 物件、COM 物件的 race condition）。利用步驟和上面描述的完全相同，只是觸發條件是多執行緒 race。

### UAF 轉 info leak

即使 reclaim 沒有成功（或你想在 reclaim 之前先 leak），dangling pointer 在某些情況下可以用來讀資訊：

```
  // victim free 後，slot 裡可能還有原始 vptr
  void* leaked_vptr = *(void**)dangling_ptr;
  // leaked_vptr 指向 victim 類別的 vtable（在某個 module 裡）
  // → module base = leaked_vptr - vtable_rva（從 binary 靜態分析算出 RVA）
  // → 有了 module base，後續 ROP 的 gadget 位址就算得出來了
```

UAF info leak → module base → bypass ASLR → ROP 是標準的漏洞利用鏈，特別常見在 browser exploit（V8、Edge 的 CVE）和 Windows kernel exploit 裡。

### 堆 spray 的大量 spray 數量估算

如果你不能做精確的 grooming，而是用大量 spray：

```
  UserBlocks 有 N 個 free slot，victim 是其中一個
  每次 alloc（spray）成功 reclaim victim 的機率 = 1/N
  失敗機率 = (N-1)/N
  K 次 spray 後至少一次成功的機率 = 1 - ((N-1)/N)^K

  若 N = 10，K = 30：
  成功率 = 1 - (9/10)^30 = 1 - 0.042 ≈ 95.8%

  若 N = 64（一個 UserBlocks 滿載時的 free slot 數），K = 200：
  成功率 = 1 - (63/64)^200 ≈ 95.9%
```

這說明：spray 數量越大、UserBlocks 的 free slot 越少，成功率越高。Ch 28 講的 grooming 是讓 N=1（確定性），這裡的大量 spray 是讓 K 夠大（統計壓制）。

## 動手練習

用 Python ctypes 模擬 UAF 場景（本機可跑）：

1. 建一個新 heap，分配 victim chunk（0x40 bytes），記錄 victim_ptr
2. 在 victim chunk 的 user data +0x00 手動寫入 `0xDEADBEEFDEADBEEF`（模擬 vptr）
3. HeapFree(victim_ptr)——victim 的 slot 現在是 free 狀態
4. 立刻讀 victim_ptr +0x00（dangling pointer 讀）：印出讀到的 8 bytes
   - 觀察：free 後原始內容是否還在？（預期：大機率還在，因 LFH 不清空 slot 內容）
5. 分配 sprite chunk（0x40 bytes），在 sprite 的 user data +0x00 寫入 `0x4141414141414141`（fake_vptr）
6. 再次讀 victim_ptr +0x00：印出讀到的 8 bytes
   - 如果 sprite 佔回了 victim 的 slot，讀到 `0x4141414141414141`；否則讀到舊值或被 heap manager 改過的值
7. 重複步驟 3-6 一百次，統計 sprite 成功 reclaim victim slot 的次數——感受 LFH randomization 的機率性

## 本章重點整理

- UAF 的本質：free 後 dangling pointer 還在；攻擊者讓 sprite object 佔回（reclaim）freed slot，填入 fake_vptr；Use 觸發時跳到攻擊者控制的位址。
- LFH 的 reclaim 前提：sprite 和 victim 必須在同一個 LFH bucket（size class 相同）；Win 8+ 的 allocation randomization 讓 reclaim 是機率性的，對策是「壓制法」（把 UserBlocks 其他 free slot 填滿）。
- type confusion：sprite 型別和 victim 不同，dangling pointer 以 victim 的型別解讀 sprite 的內容——+0x00 被當 vptr 讀，但實際上是 sprite 的其他欄位。
- UAF 的 reclaim 和 glibc tcache 的 reclaim 根本不同：tcache 是確定性 LIFO（改 next 指標），LFH 是機率性（PRNG 選 slot），需要不同的利用策略。
- 常見利用鏈：UAF dangling pointer 讀 vptr → leak module base → bypass ASLR → reclaim + fake_vptr → 虛擬呼叫劫持 → ROP。

## 自我檢核

- [ ] 不看筆記，能畫出 UAF 的完整時序（t=0 分配、t=1 free、t=2 reclaim、t=3 Use），並說出每個步驟攻擊者在做什麼
- [ ] 面試被問「Windows LFH 的 UAF reclaim 和 glibc tcache 的 UAF reclaim 有什麼根本差異」，能說出 PRNG vs LIFO、bitmap vs next 指標這兩個關鍵點
- [ ] 能解釋 type confusion 和 UAF 的關係，以及為什麼「不同型別的 sprite 佔回 victim slot」會帶來 type confusion
- [ ] 能說出一個「理想 sprite object」的三個條件（大小、內容可控性、不干擾程式邏輯）
- [ ] 知道「壓制法」是怎麼把 LFH reclaim 的機率提升到接近 100% 的
- [ ] 能說明為什麼 LFH slot free 後，dangling pointer 在很長一段時間內都可以安全讀寫（記憶體不被 OS 回收的原因）

## 延伸閱讀

### 論文 / 白皮書

- **[Exploiting Windows Kernel Pool Allocations](https://www.coresecurity.com/sites/default/files/private-files/publications/2016/05/Windows-Kernel-Pool-Exploitation-ccs2012.pdf)** — Tarjei Mandt，CCS 2012
  - **讀哪裡**：Kernel Pool 的 UAF 利用技法，特別是 lookaside list 的 reclaim 機制（和 LFH reclaim 概念相同但在 kernel pool 裡）
  - **學什麼**：UAF 利用在 kernel 層的系統分析，以及 pool spray 的精確計算方法；補充本章的 userland 視角
  - **前提知識**：本章 + Ch 14/15 + kernel pool 基礎

- **[Attacking Windows Heap Internals (from 2004 to 2012)](https://illmatics.com/Windows%208%20Heap%20Internals.pdf)** — Chris Valasek & Tarjei Mandt，BH US 2012
  - **讀哪裡**：第 5 節「Use-After-Free」——對 LFH UAF 利用的系統性分析，包含 reclaim 機率的計算
  - **學什麼**：LFH UAF 的攻擊模型，以及研究者如何評估 reclaim 的成功率
  - **前提知識**：Ch 15 + 本章

### 部落格

- **[j00ru — Exploiting Windows Kernel Vulnerabilities: UAF](https://j00ru.vexillium.org/)** — Mateusz Jurczyk
  - **讀哪裡**：搜索 kernel UAF 相關文章；j00ru 的 Windows 漏洞利用研究包含大量 UAF 案例分析
  - **學什麼**：真實 CVE 的 UAF 利用細節，包含 reclaim 的 timing 控制和 spray 策略
  - **前提知識**：本章 + 一定的 kernel pwn 基礎

- **[Connor McGarr — Windows Heap Exploitation: UAF to Code Execution](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：UAF 利用系列，從 free → reclaim → vptr 覆寫 → code execution 的完整鏈
  - **學什麼**：現代 Win10/11 環境下的 UAF 完整利用流程；本章所有步驟的實戰對應
  - **前提知識**：本章全部 + Ch 14/15

- **[Saar Amar — Windows Heap Exploitation Techniques](https://saarlab.com/)** — Saar Amar
  - **讀哪裡**：LFH UAF 的 spray 策略和 reclaim 精確控制技法
  - **學什麼**：把「壓制法」和「大量 spray」的數學分析說清楚的少數資源之一；本章「對策」段落的技術來源
  - **前提知識**：Ch 15 + 本章

UAF 把「控制被 free 物件的內容」的能力建立起來了，但要讓 reclaim 有確定性、讓 spray 有精確的佈局控制，還需要更系統的 heap feng shui 技法。

→ [Ch 28 — LFH 精確 grooming (feng shui)](./28-lfh-grooming.md)
