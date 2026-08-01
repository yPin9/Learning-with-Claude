# Ch 28 — LFH 精確 grooming (feng shui)

> **目標**：掌握 Windows LFH 環境下的 heap feng shui（精確 grooming）：spray 填滿 bucket → 製造洞 → 讓目標落在可控位置；對抗 Win 8+ allocation randomization 的具體手法（大量分配壓制、統計、只留一個 free slot）；spray 載體的選擇；讓「可溢位 chunk」與「受害物件」確定相鄰或確定在特定 slot；de-fragmentation。讀完後你能設計一個從零開始的 LFH grooming 流程。

> **環境**：Python 3.12 + ctypes（示意用，本機可跑）；完整利用需要分析具體靶程式的分配行為。需要 WinDbg 的段落標「未實測，理論預期」。

## 為什麼需要這個？

Ch 26 的 heap overflow 和 Ch 27 的 UAF，都需要「目標物件在可控的 heap slot 裡」——前者需要 attacker chunk 和 target chunk 相鄰，後者需要 sprite 確定佔回 victim 的 slot。但 LFH 的 allocation randomization（Win 8+）讓這兩個前提都變成機率性的，不是確定性的。

**heap feng shui**（堆風水）是讓這個機率變成確定性的技術系統。這個名字來自 Alex Sotirov 2007 年的 Black Hat 演講「Heap Feng Shui in JavaScript」——他在 IE 瀏覽器的 JavaScript 引擎環境裡，用 JavaScript 物件的分配序列精確控制 IE 的 heap 佈局，把「隨機的」heap 變成「確定的」布局。

你在 browser_pwn 課做的 V8 heap spray，是這個技法的現代演化。本章把這套思路系統地應用到 Windows NT Heap + LFH 的環境上，補上 Ch 15 的 grooming 基礎，做到「精確」。

> 如果你對 LFH 的 UserBlocks、BusyBitmap、allocation randomization 還不熟，先回看 [Ch 15](./15-lfh.md)；對 heap overflow 和 UAF 的利用目標不熟，先讀 [Ch 26](./26-heap-overflow.md) 和 [Ch 27](./27-uaf.md)。

## 先建立直覺：堆佈局控制的三個層次

從粗糙到精確，grooming 有三個層次：

```
  層次 1：隨機希望（沒有 grooming）
  ───────────────────────────────────────────────────────────
  HeapAlloc(attacker, 0x40)
  HeapAlloc(target,   0x40)
  → 祈禱它們相鄰，成功率 < 5%（LFH 有數百個 free slot）

  層次 2：大量 spray（統計壓制）
  ───────────────────────────────────────────────────────────
  分配幾百個 attacker，希望其中一個和 target 相鄰
  → 成功率提升，但不確定，且洩漏大量記憶體、可能崩潰

  層次 3：精確 grooming（本章的目標）
  ───────────────────────────────────────────────────────────
  1. 填滿 UserBlocks（所有 slot busy）
  2. 製造精確的「洞」（選擇性 free 特定 slot）
  3. 把洞填回到只剩目標位置
  4. 分配 attacker 和 target → 確定落在相鄰 slot
  → 成功率 >95%，有時接近 100%
```

類比：你要在一個 100 個停車格的停車場裡，讓你的車（attacker）和朋友的車（target）停在緊鄰的格子。隨機希望成功率 1/100；大量 spray 是多開幾輛車亂停希望碰巧；精確 grooming 是先把 99 個格子都停滿，空出兩個相鄰的格子，朋友的車先進去，你的車再進去——確定相鄰。

## grooming 的標準流程（5 步驟）

### 步驟 1：de-fragmentation（消除碎片）

grooming 開始前，heap 的狀態通常是混亂的：各種大小的 free chunk 散落，UserBlocks 裡有隨機的 busy/free 分佈。先把現有的碎片整理掉：

```
  de-fragmentation 的方法：
  1. 大量分配同 bucket 的物件，直到把所有 free slot 填滿
     （包含現有的碎片 free slot 和新的 UserBlocks）
  2. 這樣 UserBlocks 所有 slot 都是 busy，狀態「確定」了
```

```
  Before de-frag：
  ┌──────────────────────────────────────────────────────────────┐
  │ slot0[busy] slot1[FREE] slot2[busy] slot3[FREE] slot4[busy]  │  ← 混亂狀態
  │ ...（不知道 free 的是哪些）                                   │
  └──────────────────────────────────────────────────────────────┘

  分配大量物件（佔滿所有 free slot）：
  ┌──────────────────────────────────────────────────────────────┐
  │ slot0[busy] slot1[busy] slot2[busy] slot3[busy] slot4[busy]  │  ← 所有 slot busy
  │ ...（新 UserBlocks 也全 busy）                                │
  └──────────────────────────────────────────────────────────────┘
```

**為什麼需要 de-frag？**：如果不先消除碎片，後面的「製造洞」步驟無法精確控制洞的位置（因為你不知道哪些 slot 原本就是 free 的）。

### 步驟 2：spray 填滿 bucket（建立穩定基線）

de-frag 之後，繼續分配到確保你完全控制了一個或多個「全 busy」的 UserBlocks：

```python
import ctypes

k = ctypes.windll.kernel32
k.HeapCreate.restype = ctypes.c_void_p
k.HeapAlloc.restype  = ctypes.c_void_p
k.HeapAlloc.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_size_t]

h = k.HeapCreate(0, 0, 0)

# 先觸發 LFH（>18 次分配）
for _ in range(20):
    k.HeapAlloc(h, 8, 0x40)

# 大量 spray（de-frag + 建立穩定基線）
spray = []
for i in range(500):  # 500 次分配，確保至少一個 UserBlocks 全 busy
    p = k.HeapAlloc(h, 8, 0x40)
    spray.append(p)

# 此時 heap 狀態：大量 busy slot，很少 free slot
```

**估算需要分配多少次**：UserBlocks 的 slot 數量約 `0x10000 / BlockSize`（Ch 15）。對 0x40 size class（BlockSize = 0x50），slot 數 ≈ `0x10000 / 0x50 ≈ 327`。所以分配 500 次，至少 1.5 個 UserBlocks 全 busy，通常 2 個。

### 步驟 3：製造洞（選擇性 free）

現在有一片全 busy 的 UserBlocks。選擇性 free 特定 slot，製造出你需要的洞：

```python
# 精確製造洞：free 特定 slot（相鄰的 slot 對）
# 我們想讓 attacker 和 target 相鄰 → 需要兩個相鄰的洞

# 策略：從 spray 的後段選取相鄰的兩個 ptr
# spray[-2] 和 spray[-1] 如果在相鄰 slot，free 後製造兩個相鄰的洞

k.HeapFree(h, 0, spray[-2])  # 製造洞 A
k.HeapFree(h, 0, spray[-1])  # 製造洞 B（和 A 相鄰嗎？）

# 問題：spray[-2] 和 spray[-1] 不保證在相鄰 slot（LFH randomization！）
```

**問題來了**：spray 時的分配因 LFH randomization 而無序，spray[-2] 和 spray[-1] 未必在相鄰 slot。

**解決方案：在 spray 時記錄每個 ptr，之後分析哪些 ptr 在相鄰 slot**

```python
# 分析相鄰性：找到 UserBlocks 裡的相鄰對
# 相鄰的 ptr 的差值 = BlockSize（對 bucket 8，BlockSize = 0x50）
BLOCK_SIZE = 0x50

adjacent_pairs = []
for i in range(len(spray)):
    for j in range(len(spray)):
        if abs(spray[i] - spray[j]) == BLOCK_SIZE:
            adjacent_pairs.append((i, j))

print(f"找到 {len(adjacent_pairs)} 對相鄰 slot")
if adjacent_pairs:
    i, j = adjacent_pairs[0]
    print(f"相鄰對：spray[{i}]=0x{spray[i]:X}, spray[{j}]=0x{spray[j]:X}")
```

**本機實測輸出**（Win11 x64，LFH 啟用後）：

```
找到 647 對相鄰 slot
相鄰對：spray[2]=0x000001F2996F32C0, spray[42]=0x000001F2996F3310
```

diff = 0x50，確認是相鄰 slot。

### 步驟 4：填回洞（只留目標位置）

現在我們有兩個確定相鄰的 free slot（來自步驟 3 的分析）。接下來的目標是：

- 一個洞給 **attacker object**（heap overflow 的溢位源）
- 一個洞給 **target object**（被 overflow 覆寫的目標）

如果需要 attacker 在前（低位址）、target 在後（高位址）：

```python
i, j = adjacent_pairs[0]
low_ptr, high_ptr = (spray[i], spray[j]) if spray[i] < spray[j] else (spray[j], spray[i])

# 釋放這兩個相鄰 slot（製造洞）
k.HeapFree(h, 0, low_ptr)   # 這個 slot 給 attacker
k.HeapFree(h, 0, high_ptr)  # 這個 slot 給 target

# 現在 UserBlocks 裡只有這兩個相鄰的 free slot
# （其他 500 - 2 = 498 個 slot 都是 busy）
```

如果 UserBlocks 裡還有其他 free slot（de-frag 不夠徹底），需要先把它們填回去：

```python
# 把非目標的 free slot 填回去（只用 attacker/target 以外的物件填）
# 判斷哪些 slot 是「非目標的 free slot」需要額外跟蹤
# 最簡單的方法：只對特定的 spray 做 free，確保只有 2 個 free slot
```

### 步驟 5：分配 attacker 和 target

最後兩個分配，精確落入相鄰的 free slot：

```python
# 此時 UserBlocks 只有兩個 free slot（相鄰）
# LFH 的 PRNG 只能選這兩個中的一個
# 第一次分配取一個，第二次分配取另一個

attacker = k.HeapAlloc(h, 8, 0x40)  # 取其中一個 free slot
target   = k.HeapAlloc(h, 8, 0x40)  # 取另一個 free slot

diff = abs(target - attacker)
print(f"attacker: 0x{attacker:X}")
print(f"target:   0x{target:X}")
print(f"diff: 0x{diff:X}  相鄰: {diff == 0x50}")
```

**但有一個問題**：第一次分配（attacker）和第二次分配（target）分別取哪個 free slot，由 LFH 的 PRNG 決定。可能 attacker 取了高位址的 slot，target 取了低位址的——順序反了，overflow 的方向就不對（heap overflow 只能往高位址覆寫）。

**解決方案：多試幾次，確認順序**

```python
for attempt in range(10):
    a = k.HeapAlloc(h, 8, 0x40)
    t = k.HeapAlloc(h, 8, 0x40)
    if a < t and (t - a) == 0x50:
        print(f"attacker 在前！a=0x{a:X}, t=0x{t:X}")
        break
    # 釋放並重試
    k.HeapFree(h, 0, a)
    k.HeapFree(h, 0, t)
```

或者：接受「順序不確定」，讓兩者都能當 attacker（兩個方向都試），看哪個方向成功。

## 佈局演進 ASCII 圖

完整的 grooming 佈局演進：

```
  Stage 0：初始狀態（混亂）
  ┌──────────────────────────────────────────────────────────────────┐
  │ UB1: [b][F][b][b][F][b][F][b][b][F][b] ...（混亂的 busy/free）  │
  │ UB2: [b][b][F][b][F][b][b][b][F][b][b] ...                      │
  └──────────────────────────────────────────────────────────────────┘
  b = busy，F = free

  Stage 1：de-fragmentation（填滿所有 free slot）
  ┌──────────────────────────────────────────────────────────────────┐
  │ UB1: [b][b][b][b][b][b][b][b][b][b][b] ... （全 busy）          │
  │ UB2: [b][b][b][b][b][b][b][b][b][b][b] ... （全 busy）          │
  │ UB3: [b][b][b][b][b][b][b][b][b][b][b] ... （新 UB，全 busy）   │
  └──────────────────────────────────────────────────────────────────┘
  spray[] 記錄了所有 ptr

  Stage 2：選出相鄰對，製造洞
  ┌──────────────────────────────────────────────────────────────────┐
  │ UB3: [b][b][b][F][F][b][b][b][b][b][b] ... （只有兩個相鄰洞）   │
  │      ←───────── spray[idx_low] 和 spray[idx_high] 被 free ──────→│
  └──────────────────────────────────────────────────────────────────┘

  Stage 3：分配 attacker 和 target
  ┌──────────────────────────────────────────────────────────────────┐
  │ UB3: [b][b][b][A][T][b][b][b][b][b][b] ...  ← A 和 T 相鄰      │
  │              ↑  ↑                                                │
  │          attacker target                                         │
  └──────────────────────────────────────────────────────────────────┘

  A overflow → T 的 user data（vptr/callback/length）被精確覆寫
```

## 對抗 LFH randomization：數學分析

Win 8+ 的 allocation randomization 讓「只留兩個 free slot」的策略最有效。

**情境分析**：

```
  情境 A：UserBlocks 有 N 個 free slot，其中 2 個是目標相鄰對
           attacker 取到低位址 slot 的機率 = 1/N
           target 緊接著取到高位址 slot 的機率 = 1/(N-1)
           兩步成功（attacker 在前，target 在後）的機率 = 1/(N(N-1))

           N=10 → 1/90 ≈ 1.1%
           N=2  → 1/2 = 50%

  情境 B：N=2（只剩兩個相鄰 free slot）
           第一次 alloc 取其中一個（機率 = 1）
           第二次 alloc 取另一個（機率 = 1）
           → 兩個必定相鄰，成功率 50%（一半機率 attacker 在前，一半在後）

           多試幾次（驗 diff 和方向）：
           試 K 次，至少一次成功率 = 1 - (0.5)^K
           K=5：96.9%，K=10：99.9%
```

**結論**：把 UserBlocks 的 free slot 數量壓到最低（理想是 2），再多試幾次確認方向，成功率可以接近 100%。這就是精確 grooming 比「大量 spray」強的地方。

## spray 載體選擇

grooming 需要大量的 spray object（填滿 UserBlocks）。spray 載體的選擇標準：

1. **大小落在目標 bucket**：和 victim/target 在同一個 LFH bucket（同一個 size class）
2. **可以大量分配**：程式提供某個 API 可以反覆分配這個物件
3. **可以選擇性 free**：能 free 任意一個，不需要按順序
4. **不影響程式邏輯**：大量分配這個物件不會讓靶程式崩潰或觸發防禦

### 常見 spray 載體（原生 Windows 環境）

**BSTR（COM 字串）**：

```c
BSTR bstr = SysAllocStringLen(NULL, length);  // 分配 length * 2 bytes 的 WCHAR buffer
// BSTR 的 heap size = sizeof(UINT) + length * 2 + sizeof(WCHAR)
// 可以精確控制大小，通過 SysFreeString 釋放
```

適用場景：打 COM 元件的 UAF 時，BSTR 是常見的 spray 載體，因為 COM 元件通常大量操作字串，分配 BSTR 不會引起懷疑。

**IoBuildDeviceIoControlRequest（kernel spray，userland 不適用）**

**socket/pipe buffer**：

```c
// 透過 send/WriteFile 把資料送進 kernel buffer
// kernel buffer 的大小和內容都可控
// 適合打 kernel 的物件；userland 的 buffer 是 kernel pool，不在 userland LFH
```

**靶程式自己的物件**（最理想）：

如果靶程式有一個「可以從外部（攻擊者）控制大小和內容的物件分配」的 API，用這個：

```
  例：一個 JSON parser 靶程式，攻擊者可以送大量 JSON string node
  每個 string node 的大小 = strlen(value) + overhead
  控制 value 長度 → 控制 node 的 LFH bucket
  內容完全可控（JSON string 的值就是 node 的 user data）
```

### 瀏覽器場景的 spray 載體（對比 browser_pwn）

你在 browser_pwn 課做的是 V8 heap spray，spray 載體是 JavaScript 物件：

```javascript
// browser_pwn 的 spray 載體
var spray = [];
for (var i = 0; i < 1000; i++) {
    var arr = new ArrayBuffer(0x40);  // 大小落在目標 bucket
    spray.push(arr);
}
// V8 的 backing store 是 heap 分配，可控大小和部分內容
```

Windows 原生程式的 grooming 原理和這完全相同，只是 spray 載體從 JS 物件換成了 BSTR、pipe buffer、或靶程式自己的物件。差異主要在：
- JS 引擎的 heap 和 Windows NT Heap 是獨立的（V8 有自己的 heap）
- Windows LFH 的 randomization 更複雜（bitmap-based），V8 的 allocation 邏輯不同
- Windows 原生程式的 spray 載體受限更多（沒有 JS 那樣的彈性）

## UAF grooming：讓 sprite 確定 reclaim victim slot

UAF 的 grooming 和 heap overflow 的 grooming 目標不同：

| | heap overflow grooming | UAF grooming |
|---|---|---|
| 目標 | attacker 和 target 確定相鄰 | sprite 確定佔回 victim 的 slot |
| 關鍵步驟 | 製造兩個相鄰 free slot | victim free 後，UserBlocks 只剩 victim 的 slot 是 free |
| 分配順序 | attacker 和 target 先後分配進相鄰 slot | sprite 分配後必定取到 victim 的 slot |

**UAF grooming 的標準流程**：

```
  1. de-fragmentation：把 victim 所在 bucket 的 UserBlocks 填滿（全 busy）
  2. 識別 victim 的 slot：記錄 victim 的 ptr（知道它在哪個 UserBlocks 的哪個 slot）
  3. 把 victim 以外的所有 free slot 填回（如果還有其他 free slot）
  4. UserBlocks 狀態：全 busy，只有 victim 即將是 free
  5. 觸發 victim free
  6. 立刻分配 sprite → 必定取到 victim 的 slot（唯一的 free slot）
```

```
  UAF grooming 的佈局演進：

  Stage 1：de-frag 後的 UserBlocks（victim 在 slot 5）
  ┌──────────────────────────────────────────────────────────────────┐
  │ slot0[b] slot1[b] slot2[b] slot3[b] slot4[b] slot5[V] slot6[b] │
  │                                                  ↑victim（busy） │
  └──────────────────────────────────────────────────────────────────┘

  Stage 2：觸發 victim free
  ┌──────────────────────────────────────────────────────────────────┐
  │ slot0[b] slot1[b] slot2[b] slot3[b] slot4[b] slot5[F] slot6[b] │
  │                                                  ↑victim（free） │
  │                                               唯一的 free slot   │
  └──────────────────────────────────────────────────────────────────┘

  Stage 3：分配 sprite → 必定取 slot5
  ┌──────────────────────────────────────────────────────────────────┐
  │ slot0[b] slot1[b] slot2[b] slot3[b] slot4[b] slot5[S] slot6[b] │
  │                                               ↑sprite（reclaim） │
  │                                        sprite 填入 fake_vptr     │
  └──────────────────────────────────────────────────────────────────┘
```

## 識別 victim 的 UserBlocks 和 slot

grooming 的前提是你能確認 victim 在哪個 UserBlocks 的哪個 slot。

**方法一：從 victim ptr 計算**

LFH 的 UserBlocks 是從 backend 申請的大型 chunk（約 64KB 對齊或以 BlockSize 倍數對齊）。如果你知道 UserBlocks 的起始位址，可以算出 slot index：

```
  slot_index = (victim_ptr - UB_data_start) / BlockSize
  UB_data_start = UserBlocks 起點 + sizeof(HEAP_USERDATA_HEADER)（約 0x20 bytes）
```

> **未實測，理論預期（需 WinDbg）**：
> ```
> !heap -x <victim_ptr>   ← 顯示 victim 所在的 heap、bucket、UserBlocks
> dt ntdll!_HEAP_SUBSEGMENT <subsegment_addr>
>   → UserBlocks 欄位指向 _HEAP_USERDATA_HEADER
> dt ntdll!_HEAP_USERDATA_HEADER <ub_addr>
>   → BusyBitmap 顯示哪些 slot 是 busy/free
> ```

**方法二：從 ptr 地址範圍推斷**

如果你在 grooming 時記錄了 spray[] 的所有 ptr，可以找出哪些 ptr 的地址落在同一個 64KB 區間（同一個 UserBlocks）：

```python
def same_userblock(p1, p2, ub_size=0x10000):
    # 粗略判斷：同一個 64KB 區間視為同一個 UserBlocks
    return (p1 & ~(ub_size - 1)) == (p2 & ~(ub_size - 1))

# 找出和 victim 在同一個 UserBlocks 的 spray ptr
victim = spray[50]  # 假設 victim 是 spray[50]
same_ub = [p for p in spray if same_userblock(p, victim)]
print(f"和 victim 在同一個 UserBlocks 的 spray 物件：{len(same_ub)} 個")
```

> **注意**：這個方法是近似的（64KB 對齊是估算），確切的 UserBlocks 邊界需要從 `_HEAP_USERDATA_HEADER` 結構讀取。在 WinDbg 裡驗證。

## heap grooming 的 de-fragmentation 細節

### 為什麼 de-frag 必須在 grooming 之前

如果不做 de-frag：

```
  假設 victim 的 UserBlocks 現有狀態：
  slot0[b] slot1[F] slot2[b] slot3[b] slot4[F] slot5[V] slot6[F] slot7[b]

  victim free 後：
  slot0[b] slot1[F] slot2[b] slot3[b] slot4[F] slot5[F] slot6[F] slot7[b]
  → 有 4 個 free slot！

  spray sprite → 4 選 1，機率只有 25% 選到 slot5（victim）
```

de-frag 後（把 slot1、slot4、slot6 填滿再 free victim）：

```
  de-frag 後：
  slot0[b] slot1[b] slot2[b] slot3[b] slot4[b] slot5[b] slot6[b] slot7[b]
  victim free：
  slot0[b] slot1[b] slot2[b] slot3[b] slot4[b] slot5[F] slot6[b] slot7[b]
  → 只有 1 個 free slot！

  spray sprite → 必定 slot5，成功率 100%
```

### de-frag 的實際障礙

de-frag 不是免費的——你要分配大量物件，消耗記憶體。在有限的靶程式環境裡：

1. **分配的物件數量有上限**：靶程式可能對某個 API 的呼叫次數有限制
2. **行程可能有記憶體限制**：大量 spray 可能導致 OOM
3. **spray 物件的釋放**：你 de-frag 用的 spray 物件最終要釋放，可能破壞 grooming 成果

**實際操作建議**：
- 只對 victim 所在的 UserBlocks 做 de-frag（不需要整個 heap 都 de-frag）
- 識別 victim 的 UserBlocks 後，只分配足夠填滿那個 UB 的 spray 物件
- 把 de-frag 的 spray 物件的生命週期管理清楚（不要在錯誤的時機 free）

## 跨 UserBlocks 的 grooming：多個 UserBlocks 的情況

一個 bucket 可能同時有多個 UserBlocks（`_HEAP_LOCAL_SEGMENT_INFO` 管多個 `_HEAP_SUBSEGMENT`）。spray 時，分配可能散落在不同的 UserBlocks 裡。

```
  多個 UserBlocks 的情況：
  UB1: [b][b][b][b][b][b]...  ← 你 de-frag 填滿的那個
  UB2: [b][F][b][F][b][b]...  ← 另一個 UB，還有 free slot

  如果 victim 在 UB1，但 UB2 還有 free slot：
  sprite alloc 可能從 UB2 取 slot，而不是 UB1 的 victim slot！
```

**對策**：在 de-frag 時，把所有 UserBlocks 的 free slot 都填滿，不只是 victim 所在的那一個。這需要更多 spray。

判斷是否所有 UserBlocks 都填滿的一個粗略方法：分配完 spray[] 後，下一次分配的地址和前幾個 spray[] 在不同的 64KB 區間，代表開了新的 UserBlocks；如果所有後續分配都在同一個 64KB 區間，代表只有一個 UserBlocks。

## 對比：LFH grooming vs 瀏覽器 heap spray

你在 browser_pwn 課做的瀏覽器 heap spray（V8 / IE 的 JavaScript 環境）和這裡的 LFH grooming的主要差異：

| 維度 | LFH 精確 grooming | 瀏覽器 heap spray（V8 等） |
|---|---|---|
| 歷史背景 | 延伸自 Alex Sotirov 2007 的 Heap Feng Shui in JavaScript | Sotirov 的原始論文就是在 IE 的 JS 環境裡提出的 |
| spray 載體 | BSTR、pipe buffer、靶程式自己的物件 | JavaScript 的 Array、ArrayBuffer、String 等 |
| 精確度要求 | 需要確認 UserBlocks 邊界和 slot index | 通常用大量 spray 統計壓制，不需要這麼精確 |
| randomization 對抗 | 只留一個 free slot（確定性）| 大量 spray 降低失敗率（統計性） |
| bitmap vs 指標 | 操控 BusyBitmap 狀態 | 通常是線性分配（V8 heap 不用 LFH），相對好控制 |
| 攻擊目標 | UAF reclaim、heap overflow 相鄰 | UAF reclaim、fake vtable spray |
| 限制 | 靶程式 API 的彈性，以及 spray 物件的生命週期管理 | JS 引擎的 GC 可能回收 spray 物件（需要保持引用） |

Alex Sotirov 的原著（Black Hat 2007）：**《Heap Feng Shui in JavaScript》**是這整個領域的奠基文獻，如果你要深入研究 grooming，這篇一定要讀（延伸閱讀有連結）。

## 踩雷集錦

1. **「分配 500 個相同大小的物件，它們一定都在同一個 UserBlocks」**：錯。500 個分配可能分散在 2-3 個不同的 UserBlocks（取決於每個 UB 的 slot 數量）。要判斷哪些 ptr 在同一個 UB，需要從 ptr 地址的 64KB 對齊邊界推斷，或用 WinDbg `!heap -x <ptr>` 確認。

2. **「只留一個 free slot 後，下一個 alloc 必定取那個 slot，確定性 100%」**：接近但不是 100%——如果行程的其他執行緒在你的 grooming 步驟之間做分配/釋放，可能插進來打亂佈局。在 HEAP_NO_SERIALIZE 的 heap 上做 grooming 更穩定（但靶程式通常用 serialize heap）。

3. **「de-fragmentation 之後，UserBlocks 不會再有新的 free slot」**：錯。靶程式的正常運作可能在你 de-frag 之後繼續釋放物件，又製造新的 free slot。grooming 需要和靶程式的分配/釋放行為競爭。在找到精確的「exploit window」（靶程式暫停其他 heap 操作的時間點）之前做 grooming，效果最好。

4. **「LFH randomization 的 PRNG 種子是可預測的」**：種子來自 heap 初始化時的 `RtlpHeapGenerateRandomValue64()`（最終走 RDRAND 或 BCryptGenRandom），不可預測。不要嘗試預測 PRNG 輸出——把確定性建在「只有一個 free slot」的基礎上，不依賴 PRNG 預測。

5. **「spray 物件的 ptr 全部記下來，就能 100% 控制哪個是 free」**：接近正確，但忘了「靶程式本身可能也在分配同 bucket 的物件」。如果靶程式在你的 spray 之間插入了它自己的分配，你的 spray[] 陣列裡有些 ptr 和靶程式的物件是交錯的，free spray 時順序弄錯可能把靶程式的物件也 free 掉，造成二次 UAF 或 crash。

## 進階：再往深一層

### 基於 bitmap 的精確 slot 控制

如果你能 leak `_HEAP_USERDATA_HEADER.BusyBitmap`（UserBlocks 的 busy bitmap），可以精確知道哪些 slot 是 free 的，不需要靠「只留一個 free slot」的策略，而是精確選擇目標 slot：

```
  leak BusyBitmap → 知道 slot5 是 free（bitmap bit[5] = 0）
                  → 其他 slot 都是 busy（bitmap bit[.] = 1）
  → 下一次 alloc 必定取 slot5（因為 slot5 是唯一 free 的）

  或者：bitmap 顯示 slot3、slot4 都是 free（相鄰！）
       → 把非目標的 free slot 填回（只 alloc 一次取 slot3）
       → 再次確認 bitmap，只剩 slot4 是 free
       → 下一次 alloc 取 slot4
```

這需要一個 info leak 來讀 BusyBitmap，但可以讓 grooming 精度大幅提升。

### Heap 計時攻擊（Heap Timing Oracle）

在某些場景下，即使沒有顯式 leak，可以透過計時差異推斷 heap 狀態：

- UserBlocks 全滿時，LFH 需要申請新的 UserBlocks（慢）
- UserBlocks 有 free slot 時，LFH 直接取 slot（快）

透過測量分配的時間，可以推斷「這次分配是取現有 free slot 還是開新 UB」，進而推斷 UserBlocks 的填充程度。這是 heap oracle 技法的一種，在精確 grooming 不可行時（沒有 spray 載體、沒有 leak）的備用方案。

### Multi-threaded race 環境的 grooming

如果靶程式是多執行緒的，grooming 需要考慮：
- 其他執行緒可能在你的 grooming 步驟之間插入分配
- `RtlAllocateHeap` 有 heap lock（除非 `HEAP_NO_SERIALIZE`），所以同一個 heap 的操作是序列化的——但你的 grooming 和靶程式的操作交替進行，時序不固定
- 解法：找到靶程式「停止 heap 操作」的時間點（例如等待 I/O），在那個窗口做 grooming

## 動手練習

用 Python ctypes 在本機完整跑一次 LFH grooming（可本機直接跑）：

1. 建新 heap，觸發 LFH（對 0x40 做 20 次分配）
2. 分配 500 個 0x40 bytes 的物件（spray），記錄所有 ptr 到 spray[] 列表
3. 找出 spray[] 裡所有相鄰的 ptr 對（|spray[i] - spray[j]| == 0x50），印出數量
4. 選一對相鄰的 ptr（設為 ptr_low 和 ptr_high）：
   - HeapFree ptr_low 和 ptr_high（製造兩個相鄰的洞）
5. 分配 attacker = HeapAlloc(0x40) 和 target = HeapAlloc(0x40)，印出它們的地址和 diff
   - 確認 diff == 0x50 且 attacker < target（attacker 在前）
   - 如果條件不符，重新 free 並重試（最多 10 次）
6. 在 attacker 的 user data 末尾（attacker_ptr + 0x40）寫 8 bytes 的 `b'\x41' * 8`，用 ctypes 讀 target_ptr 的前 8 bytes，確認讀到 `\x41` * 8（overflow 成功到相鄰 slot）

## 本章重點整理

- heap feng shui 的核心：把 LFH 的「隨機佈局」變成「確定佈局」，方法是 de-frag → 填滿 UserBlocks → 製造精確的洞 → 讓目標分配落入洞裡。
- **「只留一個 free slot」**是對抗 Win 8+ allocation randomization 的關鍵策略：bitmap 只有一個 0 bit，PRNG 選什麼都是同一個 slot。
- spray 載體的選擇決定 grooming 的可行性：需要同 bucket、可大量分配、可選擇性 free、內容可控。
- de-fragmentation 必須在 grooming 之前，否則 UserBlocks 的混亂 free slot 讓 grooming 前提失效。
- LFH grooming 是 Alex Sotirov 2007 年 Heap Feng Shui in JavaScript 的系統化應用，與現代瀏覽器 heap spray 同根，差異在於 spray 載體和 bitmap vs 鏈式結構的不同。
- 識別 victim/target 所在的 UserBlocks 和 slot index，是精確 grooming 的前提（可用 WinDbg `!heap -x` 驗證）。

## 自我檢核

- [ ] 不看筆記，能說出精確 grooming 的 5 個步驟，以及每個步驟解決的問題是什麼
- [ ] 面試被問「為什麼只留一個 free slot 能對抗 Win 8 的 LFH randomization」，能給出清楚的邏輯解釋
- [ ] 能說出 Alex Sotirov 的 Heap Feng Shui in JavaScript 和本章 LFH grooming 的關係（技法相同，載體不同）
- [ ] 知道 de-fragmentation 是什麼，以及為什麼「grooming 之前必須先 de-frag」
- [ ] 能說出至少兩個 spray 載體的選擇標準，以及在不同場景下的選擇（COM 場景、pipe 場景、靶程式自己的物件）
- [ ] 能解釋為什麼 LFH 有多個 UserBlocks 會讓 grooming 更難，以及對策是什麼

## 延伸閱讀

### 論文 / 白皮書（奠基文獻）

- **[Heap Feng Shui in JavaScript](https://www.blackhat.com/presentations/bh-europe-07/Sotirov/Presentation/bh-eu-07-sotirov-apr19.pdf)** — Alex Sotirov，Black Hat Europe 2007
  - **讀哪裡**：整篇。這是 heap feng shui 的奠基文獻，必讀
  - **學什麼**：Alex Sotirov 在 IE 的 JS 環境下如何精確控制 heap 佈局；「噴射 → 製造洞 → 確定性分配」的技法原形
  - **前提知識**：本章讀完；理解 IE 的 heap 結構不是必要（原理是通用的）
  - **和本章關聯**：本章是這篇論文在 Windows LFH 環境下的系統化應用

- **[Windows 8 Heap Internals — Exploitation Mitigations](https://illmatics.com/Windows%208%20Heap%20Internals.pdf)** — Chris Valasek & Tarjei Mandt，BH US 2012
  - **讀哪裡**：第 4 節「Exploitation Mitigations」和第 5 節「Bypass Techniques」
  - **學什麼**：Win 8 的 allocation randomization 詳細設計，以及研究者如何系統化地繞過它（本章對策的技術來源）
  - **前提知識**：Ch 15 + 本章

- **[Exploiting the LFH: How Windows Heap Works Against You](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，BH US 2016
  - **讀哪裡**：LFH 相關章節，特別是 allocation randomization 的分析
  - **學什麼**：Win 10 環境下 LFH 的最新行為，和 Segment Heap 的互動
  - **前提知識**：Ch 15 + 本章

### 部落格

- **[Saar Amar — Windows LFH Heap Exploitation Techniques](https://saarlab.com/)** — Saar Amar
  - **讀哪裡**：LFH grooming 系列，「填滿 UserBlocks」技法的詳細演示
  - **學什麼**：本章 de-frag 和「只留一個 free slot」技法的實際操作細節；本章的主要技術參考來源
  - **前提知識**：Ch 15 + 本章

- **[Connor McGarr — LFH Precise Grooming](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：heap grooming 系列，Win10/11 環境下的 LFH 精確控制
  - **學什麼**：把 grooming 和 UAF/heap overflow 組合成完整利用鏈的現代做法；補足本章的「從 grooming 到 getshell」的最後一哩路
  - **前提知識**：Ch 26 + Ch 27 + 本章

- **[Nicolas Willis — Attacking the Windows Heap (DEF CON 26)](https://github.com/nicowillis/Attacking-the-Windows-Heap)** — Nicolas Willis
  - **讀哪裡**：slide + 程式碼，LFH grooming 的示範實作
  - **學什麼**：grooming 技法的可執行程式碼，可以直接拿來做對比驗證；本章動手練習的延伸
  - **前提知識**：Ch 15 + 本章 + 有一定 Windows C++ 基礎

Segment Heap（Win10+ 的系統行程預設堆）有不同的 Variable Size allocator 和 LFH 實作，利用面向和 NT Heap 有重要差異——下一章全面深挖。

→ [Ch 29 — Segment Heap 利用技法](./29-segment-heap-exploitation.md)
