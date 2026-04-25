# Ch 17 — 穿越 random kmalloc caches：hash 匹配、spray 策略、victim 挑選

> 目標：`CONFIG_RANDOM_KMALLOC_CACHES` 把每個 kmalloc call site hash 到 16 個 sub-cache 之一，spray object 與 victim object 不同 call site 就不同 cache — spray 失效。這章講怎麼把兩端對齊回去。

## 先搞清楚 sub-cache 的分配邏輯

kernel 6.1 引入的 `CONFIG_RANDOM_KMALLOC_CACHES`，在 `include/linux/slab.h` 裡大致是：

```c
/* 每個 size class 有 RANDOM_KMALLOC_CACHES_NR（= 16）個 sub-cache */
extern struct kmem_cache *
    kmalloc_caches[NR_KMALLOC_TYPES][KMALLOC_SIZE_CLASSES][RANDOM_KMALLOC_CACHES_NR];

static __always_inline struct kmem_cache *
kmalloc_slab(size_t size, gfp_t flags, unsigned long caller)
{
    unsigned int index = fls(size - 1);
    unsigned int type  = kmalloc_type(flags);
    /* caller = __builtin_return_address(0)，即 call site 的地址 */
    unsigned int rnd   = kmalloc_random[type][index];
    /* hash: caller 和 rnd 做 XOR，取 mod 16 */
    unsigned int idx   = (hash_ptr((void *)caller, 4) ^ rnd) & (RANDOM_KMALLOC_CACHES_NR - 1);
    return kmalloc_caches[type][index][idx];
}
```

**重點**：`idx` 取決於兩件事 —
1. `caller`（call site 的 code pointer）
2. `rnd`（boot time 隨機，固定後不變）

`rnd` 是 secret，你不知道。但 **`caller` 是固定的**：`kmalloc(256, GFP_KERNEL)` 在 `net/netfilter/nf_tables_api.c:1234` 這個 call site 的 hash 每次 boot 都一樣（因為 `rnd` 是 per-boot secret，`caller` 是 compile-time 固定地址）。

換言之：**同一個 call site 永遠走同一個 sub-cache**，但你不知道那個 sub-cache 的 idx 是多少。

---

## 策略 1：強行覆蓋所有 16 個 sub-cache

最暴力的方法：不管 victim 在哪個 sub-cache，spray 物件多到**所有 16 個 sub-cache 都被覆蓋**。

```c
/* 16 個 sub-cache，每個大概需要 200-400 個物件才能填滿一個 slab */
/* 所以一次 spray 要準備 16 × 400 = 6400 個物件 */
for (int i = 0; i < 6400; i++)
    spray_msg(qid, payload, 192);  /* kmalloc-256 × 6400 */
```

缺點：
- 記憶體壓力大，可能 OOM
- 不是每個 spray object 都能建這麼多（例如每個 tty_struct 需要 open("/dev/ptmx")，系統有 `/dev/ptmx` 的 limit）
- 對 `cred_jar` 之類的 dedicated cache 無效（它們不在 random kmalloc 裡）

適用：`msg_msg`（msgsnd 幾乎無限）、`user_key_payload`（add_key 有 quota 但幾千個沒問題）。

---

## 策略 2：利用相同 call site（Same-Cache Spray）

如果 victim object 在 `kmalloc-256`，由 `nf_tables_api.c` 的某個函式 alloc，那個 call site 的 idx 是固定的。

你需要找一個**和 victim 同 call site、或同 idx 的 spray object**。

### 怎麼找同 idx 的 call site？

方法 A（暴力）：spray 一種 object，然後 trigger vuln，觀察你的 UAF object 有沒有被 spray 覆蓋（覆蓋 = 同 cache）。如果成功率 6.25%（1/16），就是靠運氣；如果穩定命中，就是真的對到了。

方法 B（靜態分析）：讀 kernel source，找和 victim 相同 size 的 alloc call site，試遍每個，看哪個穩定落在同一個 sub-cache。這叫 **call site hunting**。

方法 C（dynamic probe）：在你的 exploit 里探測 — alloc victim，read 它的內容，然後 spray 某個 object，再 read victim 看內容有沒有變。如果同 cache，你的 spray 可能 overlap。

---

## 策略 3：換用不受 random cache 影響的 spray object

`CONFIG_RANDOM_KMALLOC_CACHES` 只影響 `GFP_KERNEL` 和 `GFP_KERNEL_ACCOUNT` 的 general-purpose kmalloc cache。以下的 spray object **不在 random cache**：

| Object | Cache | 不受影響原因 |
|---|---|---|
| `cred` | `cred_jar`（dedicated） | kmem_cache_create，不是 kmalloc |
| `file` / `filp` | `filp`（dedicated） | 同上 |
| `task_struct` | `task_struct` cache | 同上 |
| `nft_*`（部分） | dedicated cache | nf_tables 用自己的 cache |
| `pipe_buffer` | `kmalloc-cg-1024` (accounted) | 走 cg cache，但也有 random |

**最有效的繞法**：如果你的 UAF object 的 source cache 也是 dedicated（例如 `nft_set_elem_cache`），那 spray 和 victim 都不在 random kmalloc，問題自然消失。

所以 **kernelCTF 很多現代 exploit 直接選用 nf_tables 的 dedicated object 作為 spray target**，完全避開 random kmalloc caches 問題。

---

## 策略 4：heap cross-cache 繞 random cache

如果你非要用 random kmalloc 的 spray：

```
spray 大量 kmalloc-256 物件（覆蓋全部 16 個 sub-cache）
  ↓
trigger vuln alloc victim（落在某個 sub-cache）
  ↓
不在乎 victim 在哪個 sub-cache
  ↓
所有 16 個 sub-cache 的 slab 都被 spray 填滿
  ↓
free 全部 spray 物件（全部 16 個 sub-cache 的 slab 都空了）
  ↓
cross-cache：buddy allocator 回收這些 slab page
  ↓
dest cache 從 buddy 拿 page（16 個 sub-cache 都有貢獻）
```

**Cross-cache 在 random kmalloc caches 下反而更容易**，因為你釋放了 16 倍多的 slab page 給 buddy，dest cache 命中的機率更高。

---

## 實戰：victim 挑選的黃金標準

在開了 `CONFIG_RANDOM_KMALLOC_CACHES` 的 kernel 上，挑 victim 的優先順序：

1. **victim 在 dedicated cache**（cred_jar, filp, nft_*）→ 直接走，random cache 不影響
2. **victim 在 `GFP_KERNEL_ACCOUNT`（cg cache）** → 16 個 cg sub-cache，spray cg object 同樣會分散，但 cg cache 物件種類更少，容易找同 call site
3. **victim 在普通 `kmalloc-N`** → 暴力覆蓋策略，spray 6400+ 物件

---

## sub-cache idx 的探測技術（CTF 環境）

在 QEMU debug 環境下，你可以直接讀 `/sys/kernel/slab/kmalloc-256-*/` 看有幾個 sub-cache 是 active（`active_objs > 0`），再對比你 spray 後的 active_objs 變化，大致推算 victim 落在哪個 sub-cache。

```bash
# spray 前
cat /sys/kernel/slab/kmalloc-256-*/active_objs
# spray 後
cat /sys/kernel/slab/kmalloc-256-*/active_objs
# 看哪個 sub-cache 的 active_objs 跳最多
```

---

## 動手練習

1. **確認 kernel 有沒有開 random kmalloc caches**：`grep RANDOM_KMALLOC /boot/config-$(uname -r)` 或 `cat /proc/config.gz | gunzip | grep RANDOM_KMALLOC`。
2. **實驗 sub-cache 分佈**：用 `user_key_payload` spray 1000 個 kmalloc-256，觀察 16 個 sub-cache 的 active_objs 分佈。理論上每個約 62-63 個（1000/16）。
3. **call site hunting**：在 kernel source 搜 `kmalloc(256` 或 `kmalloc(0x100`，找 5 個不同 call site。用 exploit 實驗哪個和你的 victim call site 在同一個 sub-cache。
4. **暴力覆蓋驗證**：spray 6400 個 msg_msg，確認 16 個 sub-cache 的 active_objs 都非零（代表每個 sub-cache 都被你覆蓋到）。
5. **換 dedicated cache**：把 Ch 13 的 exploit 改成用 `nft_set` 系列的物件做 spray，比較在 random cache 環境下的成功率。

## 自我檢核

- [ ] 能解釋 sub-cache idx 的決定因素（caller hash ^ rnd，rnd 是 boot secret）
- [ ] 知道同一個 call site 在每次 boot 都走同一個 sub-cache（rnd 固定）
- [ ] 能說出暴力策略的 spray 量估算（16 × per-subcache 需求）
- [ ] 知道 dedicated cache（cred_jar, filp）不受 random kmalloc caches 影響
- [ ] 知道 cross-cache 在 random cache 環境下反而可能更容易（更多 page 還給 buddy）
- [ ] 能用 `/sys/kernel/slab/` 探測 sub-cache 分佈

→ [Ch 18 — CFI / KCFI 之後：data-only attack 為什麼成主流](./18-data-only-attack.md)
