# Ch 28 — 一致性雜湊

> **目標**：解掉上一章留下的難題——hash 分片下加減機器時，怎麼把 key 的搬遷量從「幾乎全部」壓到「約 1/N」。我們會先看清樸素 `hash mod N` 的致命傷（加一台機器幾乎所有 key 重映射），再親手實作一致性雜湊環（consistent hashing ring），用**虛擬節點（virtual nodes）**解決負載不均，並和 rendezvous hashing（HRW）對比。全程 Go 實作、10 萬個 key 真跑，統計實際被重映射的比例，驗證它確實 ≈ 1/N 而不是全部。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫（`hash/fnv`, `sort`）。所有數字為 WSL 實測。

## 為什麼需要這個？

上一章的 hash 分片，最直觀的實作是**取模**：key 落在 `hash(key) % N` 這台機器，`N` 是機器數。分布均勻、實作三行。

問題出在**加減機器**。你有 4 台機器（`% 4`），流量漲了要加第 5 台（`% 5`）。看看一個具體的 key 會怎樣：

```
key 的 hash = 100

  4 台時：100 % 4 = 0   → 落在機器 0
  5 台時：100 % 5 = 0   → 落在機器 0   （這個沒動）

key 的 hash = 101
  4 台時：101 % 4 = 1   → 機器 1
  5 台時：101 % 5 = 1   → 機器 1       （沒動）

key 的 hash = 102
  4 台時：102 % 4 = 2   → 機器 2
  5 台時：102 % 5 = 2   → 機器 2

key 的 hash = 103
  4 台時：103 % 4 = 3   → 機器 3
  5 台時：103 % 5 = 3   → 機器 3

key 的 hash = 104
  4 台時：104 % 4 = 0   → 機器 0
  5 台時：104 % 5 = 4   → 機器 4       ← 變了！
```

看起來前幾個沒動？別被騙了。除數從 4 變 5，`x % 4` 和 `x % 5` 對絕大多數 `x` 給出不同結果——上面剛好前四個沒動是巧合，整體看**約 80% 的 key 落點會改變**（等下真跑驗證）。

為什麼這是災難？因為在分片系統裡，「key 的落點改變」意味著**那筆資料要從舊機器實體搬到新機器**。80% 的 key 重映射 = 80% 的資料要在網路上搬動。加一台機器本該只搬入「總量的 1/5 = 20%」到新機器，結果卻觸發了幾乎全叢集的資料大搬家——搬遷期間網路飽和、快取全部失效（每台的資料都變了）、服務嚴重降級甚至癱瘓。

這就是 Ch 27 講的再平衡鐵律「搬遷量要最小」被 `hash mod N` 徹底違反。1997 年 MIT 的 Karger 等人為了解決 web 快取的這個問題，提出了**一致性雜湊（consistent hashing）**——它的核心承諾就一句話：**加減一個節點，只影響 O(K/N) 個 key（K 是 key 總數、N 是節點數），而不是幾乎全部。**

> 若對「為什麼 key 落點改變 = 資料要搬」不熟，回看 [Ch 27](./27-sharding-partitioning.md) 的再平衡一節。一致性雜湊是 hash 分片再平衡的核心利器。

## 先建立直覺：把節點和 key 都放上一個環

樸素取模的問題根源：**節點的位置由「總數 N」決定**。N 一變，所有節點的「格子」全部重排，key 跟著全亂。

一致性雜湊的破解思路：**讓節點的位置由「節點自己的雜湊」決定，跟總數 N 無關。** 把整個雜湊值空間（比如 0 到 2³²-1）想成一個**首尾相接的環**：

```
                    0 / 2³²
                       │
          n3 ●         │         ● n0
              ＼        │        ／
               ＼   ┌───────┐  ／
                ＼  │  環    │ ／
                    │(hash   │
        3/4 ────────│ space) │──────── 1/4
                 ／  │       │  ＼
                ／   └───────┘   ＼
              ／        │         ＼
          n2 ●         │         ● n1
                       │
                      1/2
```

規則：

1. **每個節點**用 `hash(節點名)` 算出它在環上的位置，放上去。
2. **每個 key** 用 `hash(key)` 算出位置，然後**順時針走**，遇到的第一個節點就是它的歸屬。

關鍵魔法在**加減節點時發生什麼**：

```
加一個新節點 n4（落在環上某處）：
       n0 ●─────────● n1
          │ 這段 key │
      加  │ 原本歸 n1│
     n4→ ●          │      n4 插進 n0 和 n1 之間，
          │只有這一段│      只「接管」它逆時針到前一個節點
          │  key 要搬│      這段的 key（原本歸 n1 的一部分）
       n3 ●─────────● n2   其他所有 key 的歸屬完全不變！
```

**新節點只從它環上的「順時針後繼」那裡接管一段 key，其他節點之間的 key 一個都不動。** 加一個節點只影響環上相鄰的一小段（平均 1/N 的 key）；減一個節點時，它的 key 全部順時針交給下一個節點，同樣只影響一段。這就是「O(K/N) 而非全部」的來源——**節點位置與 N 解耦，加減節點是局部操作。**

## 底層機制：環的實作

環在程式裡就是「一個排序好的雜湊點陣列 + 每個點屬於哪個節點」。查詢用二分搜尋找「第一個 >= key 雜湊的點」。

```
環（排序後的節點雜湊點）:  [ 0x1c.. , 0x4a.. , 0x8f.. , 0xd3.. ]
                            n1        n2        n0        n3

查詢 key（hash = 0x60..）:
    二分找第一個 >= 0x60.. 的點 → 0x8f..（n0）
    → key 歸 n0

查詢 key（hash = 0xf0..）:
    二分找第一個 >= 0xf0.. 的點 → 沒有（超過最大）
    → 繞回環首 0x1c..（n1）  ← 環是循環的
```

先看樸素取模當對照組：

```go
// hash32 用 FNV-1a，標準庫、確定、夠散。
func hash32(s string) uint32 {
	h := fnv.New32a()
	h.Write([]byte(s))
	return h.Sum32()
}

// naiveAssign：key 落在 hash(key) % len(nodes) 這台。
// 致命傷：nodes 數量一變，幾乎所有 key 的落點都改。
func naiveAssign(key string, nodes []string) string {
	return nodes[hash32(key)%uint32(len(nodes))]
}
```

一致性雜湊環：

```go
type ring struct {
	vnodes int               // 每個實體節點的虛擬節點數
	points []uint32          // 環上所有虛擬節點的雜湊點，排序
	owner  map[uint32]string // 雜湊點 -> 實體節點名
}

func (r *ring) add(node string) {
	for i := 0; i < r.vnodes; i++ {
		// 每個實體節點放 vnodes 個虛擬點。把 node 與 i 的雜湊混合，
		// 避免 "n0#0","n0#1"... 這種序列化字尾在環上聚成一團。
		p := mix(hash32(node), uint32(i))
		r.points = append(r.points, p)
		r.owner[p] = node
	}
	sort.Slice(r.points, func(i, j int) bool { return r.points[i] < r.points[j] })
}

// get：key 順時針找到第一個 >= hash(key) 的虛擬點，那個點的實體節點就是歸屬。
// 環是環形的，超過最大點就繞回第一個（二分找不到 -> index 0）。
func (r *ring) get(key string) string {
	if len(r.points) == 0 {
		return ""
	}
	h := hash32(key)
	i := sort.Search(len(r.points), func(i int) bool { return r.points[i] >= h })
	if i == len(r.points) {
		i = 0 // 繞回環首
	}
	return r.owner[r.points[i]]
}
```

`get` 是 O(log(節點數))——二分搜尋。加減節點是 O(節點數 log 節點數)——重排一次。查詢很快，這是環相對於「每次都掃全部節點」的優勢。

## 虛擬節點：解決負載不均

環有個先天問題：**如果每個節點只在環上放一個點，節點分布會不均勻。** 雜湊是隨機的，四個節點的四個點可能擠在環的某半邊，導致某個節點負責的弧段（arc）特別長、分到的 key 特別多。

```
只有 4 個點（vnode=1）的環，可能長這樣：
      n0 ●● n1        ← n0、n1 擠在一起，各自弧段短
        ╱    ╲
       ╱      ● n2    ← n2 到 n3 之間弧段超長，
      ●        ╲        這段的所有 key 全歸 n3 —— 負載爆掉
     n3 ────────╱
```

解法：**每個實體節點在環上放很多個點（虛擬節點，virtual nodes / vnodes）**。實體節點 `n0` 放 200 個虛擬點 `n0#0, n0#1, ..., n0#199`，各自雜湊到環上不同位置。這樣每個實體節點的「弧段」被打散成很多小段、遍布整個環，統計上就均勻了——**大數法則**：點越多，各節點分到的總弧長越接近 1/N。

虛擬節點還有一個副作用好處：**加節點時搬遷更分散**。vnode=1 時新節點只從一個後繼接管一段；vnode=200 時新節點的 200 個虛擬點插在環上 200 個位置，從**很多個**既有節點各接管一小段——搬遷來源分散，不會把單一節點搬到癱瘓。

代價：vnode 越多，環上的點越多，`get` 的二分搜尋 log 項變大、記憶體變多。實務甜蜜點在每個實體節點幾百個 vnode（Cassandra 預設 256、Dynamo 系統常用 100-200）。

實作上就是 `add` 裡那個 `for i := 0; i < r.vnodes` 迴圈。有個坑：如果虛擬點用 `hash("n0#0"), hash("n0#1")...` 這種序列化字尾，某些雜湊函式會讓相鄰字尾的雜湊值聚在一起（分布變差）。我們用 `mix()` 把節點雜湊與索引攪勻：

```go
// mix 把兩個 32-bit 值攪成一個分佈良好的 32-bit（避免序列化字尾在環上聚團）。
func mix(a, b uint32) uint32 {
	x := uint64(a)*0x9e3779b1 ^ uint64(b)*0x85ebca77
	x ^= x >> 16
	x *= 0x7feb352d // 這幾個常數來自 murmur/splitmix 系列的雪崩混合
	x ^= x >> 15
	return uint32(x)
}
```

（`0x9e3779b1` 是黃金比例的 32-bit 定點數，`0x7feb352d`、右移 16/15 來自 murmurhash3 finalizer 家族的雪崩常數——目的是讓輸入的每個 bit 都能影響輸出的每個 bit。）

## 動手：10 萬個 key，4 台加到 5 台，量搬遷比例

現在把樸素取模、一致性雜湊（vnode=1 和 vnode=500）、rendezvous hashing 放在一起跑同一個實驗：10 萬個 key，從 4 台機器加到 5 台，統計**實際被重映射（歸屬節點改變）的 key 比例**，以及**負載均勻度**。

理論預期：加第 5 台，理想搬遷量是「新節點該分到的份額」= 1/5 = 20%。我們看誰能逼近這個數。

實驗骨架：

```go
// remapRatio：對每個 key 比對 before/after 的歸屬，算被重映射的比例。
func remapRatio(keys []string, before, after func(string) string) float64 {
	moved := 0
	for _, k := range keys {
		if before(k) != after(k) {
			moved++
		}
	}
	return float64(moved) / float64(len(keys))
}

func main() {
	const K = 100000
	keys := makeKeys(K) // "key-0" ... "key-99999"
	nodes4 := []string{"n0", "n1", "n2", "n3"}
	nodes5 := []string{"n0", "n1", "n2", "n3", "n4"} // 加一台 n4

	// 1. 樸素 hash mod N
	rNaive := remapRatio(keys,
		func(k string) string { return naiveAssign(k, nodes4) },
		func(k string) string { return naiveAssign(k, nodes5) })

	// 2. 一致性雜湊 vnode=1（建兩個環：4 台 vs 5 台，比對）
	r1a := newRing(1); for _, n := range nodes4 { r1a.add(n) }
	r1b := newRing(1); for _, n := range nodes4 { r1b.add(n) }; r1b.add("n4")
	rCH1 := remapRatio(keys, r1a.get, r1b.get)

	// 3. 一致性雜湊 vnode=500（同上，只是每節點 500 個虛擬點）
	//    ... r2a / r2b ...

	// 4. rendezvous HRW（下節解釋）
	//    ...
}
```

真跑（WSL, Go 1.18.1，`go run .`）：

```
=== 實驗：100000 個 key，從 4 台加到 5 台，統計被重映射比例 ===

[樸素 hash mod N]   4->5 台重映射比例 = 80.2%  （理想只需搬 1/5=20%）
[一致性雜湊 vnode=1] 4->5 台重映射比例 = 4.2%  ；4 台負載最大偏差 = +75.6%（不均）
[一致性雜湊 vnode=500] 4->5 台重映射比例 = 20.5%（≈1/5=20%）；4 台負載 min=23552 max=26353 偏差=+5.4%
[rendezvous HRW]    4->5 台重映射比例 = 20.0%（≈20%）；4 台負載偏差 = +1.7%

一致性雜湊 vnode=500 的搬遷去向：搬到新節點 n4 = 20518，搬到其他既有節點 = 0
（幾乎全部搬向 n4，既有節點之間不互相搬——這正是一致性雜湊的價值）
```

這組數字把整章講完了，逐行拆解：

- **樸素 hash mod N：80.2% 重映射。** 加一台機器，八成的 key 落點改變——災難性搬遷，遠超理想的 20%。這就是 `% N` 的致命傷，實測坐實。

- **一致性雜湊 vnode=1：4.2% 重映射，但負載偏差 +75.6%。** 搬遷量很小（甚至低於 20%，因為單點環上新節點只接管一小段），但**負載嚴重不均**——某台比理想多分了 75.6% 的 key。這暴露了「沒有虛擬節點的環」的問題：搬遷少了，但均衡壞了。均衡與搬遷不能只顧一頭。

- **一致性雜湊 vnode=500：20.5% 重映射，負載偏差只 +5.4%。** 這才是我們要的——搬遷量 ≈ 1/N（20%），同時負載相當均勻（min 23552 / max 26353，四台差距不到 12%）。虛擬節點把「均衡」和「搬遷最小」同時做到了。

- **最後一行是關鍵驗證**：vnode=500 搬遷的 20518 個 key **全部搬到新節點 n4，搬到其他既有節點的是 0**。這證明一致性雜湊做到了「加節點只影響新節點該接管的那份，既有節點之間完全不互相搬」——搬遷不但量小，而且**方向純粹**（只流向新節點）。這是 `% N` 永遠做不到的：`% N` 下 key 會在各既有節點之間亂搬，快取全失效。

## Rendezvous hashing（HRW）：另一條路

一致性雜湊環不是唯一解。**Rendezvous hashing**（又叫 highest random weight, HRW）用完全不同的思路達到同樣的「加減節點最小搬遷」：

**對每個 key，計算它和每個節點的「配對分數」`score = hash(key, node)`，key 歸給分數最高的那個節點。**

```go
func rendezvousAssign(key string, nodes []string) string {
	var best string
	var bestScore uint64
	kh := uint64(hash32(key))
	for _, n := range nodes {
		nh := uint64(hash32(n))
		s := (kh ^ (nh * 0x9e3779b97f4a7c15)) * 0xff51afd7ed558ccd // 混合 key 與 node
		if s >= bestScore {
			bestScore, best = s, n
		}
	}
	return best
}
```

為什麼它也是最小搬遷？加一個新節點 `n4`，一個 key 只有在「`score(key, n4)` 比它和所有舊節點的分數都高」時才會搬過去——這件事發生的機率恰好是 1/(N+1)。**不需要環、不需要虛擬節點，天生均衡**（上面實測負載偏差只 +1.7%，比 vnode=500 的環還均勻）、搬遷剛好 20%。

代價寫在演算法裡：**每次查詢要對所有節點算一次分數，O(N)**，而環是 O(log(vnodes·N))。節點多時 HRW 的查詢變慢——這是它和環的核心取捨。

實務選擇：節點數少、要極致均衡、不想調 vnode 參數 → HRW（GlusterFS、部分 CDN、Ceph 的 CRUSH 有 HRW 的影子）。節點數多、查詢要快 → 一致性雜湊環（Cassandra、DynamoDB、大部分分散式快取）。

## 對比與取捨

| 方案 | 加節點重映射比例 | 負載均衡 | 查詢複雜度 | 需要調參 | 典型使用 |
|---|---|---|---|---|---|
| hash mod N | **~80%（災難）** | 好 | O(1) | 無 | 靜態叢集、絕不加減機器 |
| 一致性雜湊 vnode=1 | ~4%（很低但方向對） | **差（±75%）** | O(log N) | 無 | 幾乎不用（不均） |
| **一致性雜湊 vnode=多** | **~1/N（≈20%）** | **好（±5%）** | O(log(vnode·N)) | vnode 數 | **Cassandra, DynamoDB, 快取** |
| rendezvous (HRW) | **~1/N（≈20%）** | **最好（±2%）** | **O(N)** | 無 | 節點少、要均衡（Ceph CRUSH、CDN） |

一句話總結取捨：**環用「多放虛擬點」換均衡、用「二分」換快查詢，但要調 vnode；HRW 天生均衡免調參，但查詢 O(N)。** 兩者都把加減節點的搬遷從「幾乎全部」壓到「約 1/N」——這是它們共同的、相對於 `% N` 的決定性勝利。

## 踩雷集錦

1. **「hash mod N 加一台機器只搬 1/N 的資料」→ 錯得離譜，實測搬 80%。** `x % 4` 和 `x % 5` 對絕大多數 `x` 給不同結果，除數一變幾乎全部重映射。這是最反直覺、也最多人踩的——「取模很均勻」是真的，但「取模的均勻在 N 改變時完全不穩定」也是真的。

2. **「一致性雜湊環放一個點就夠了」→ 負載會嚴重不均（實測 +75%）。** 單點環的搬遷量確實小，但雜湊隨機性讓各節點的弧段長度差異巨大，某台被塞爆。**必須用虛擬節點**（每節點幾百個）才能同時做到搬遷少 + 負載勻。別只驗證搬遷比例就以為環對了，一定要一起量負載均衡。

3. **「虛擬節點的雜湊隨便用 `hash(name+i)` 就好」→ 分布可能變差。** 序列化字尾（`#0, #1, #2`）在某些雜湊函式下會讓相鄰虛擬點的雜湊值聚團，破壞均勻性。要用雪崩性好的混合（本章的 `mix()`）確保每個虛擬點在環上真正隨機散開。壞的 vnode 雜湊會讓「加了 vnode 卻還是不均」。

4. **「一致性雜湊能救單一超熱 key」→ 救不了（承 Ch 27）。** 一致性雜湊解決的是「加減節點時的搬遷量」，不是「單一 key 太熱」。一個超熱 key 不管環怎麼設計都只落在一個節點上。這兩個是不同問題，別指望一致性雜湊解熱點。

5. **「rendezvous 比環好，因為更均衡又免調參」→ 忽略了 O(N) 查詢。** HRW 每次查詢要遍歷所有節點算分數，節點數大時（幾百上千個）查詢成本線性上升，遠慢於環的二分。節點少時 HRW 香，節點多時環贏。選型要看你的節點規模，不能只看均衡度。

## 進階：再往深一層

- **weighted / heterogeneous 節點**：機器規格不同（大機器該多分資料）。環上讓大節點放**更多虛擬點**（vnode 數與容量成比例），HRW 則把分數乘上權重（`score * weight`，或用 weighted rendezvous 的對數變換）。兩者都能表達「按容量分配」。

- **一致性雜湊的變體 jump consistent hash**：Google 提出的 jump hash（2014）用一個巧妙的機率跳躍公式，**不需要儲存環**（O(1) 空間）、查詢 O(log N)、加節點搬遷最小，缺點是節點只能「從尾端加減」（不能任意刪中間節點）。適合「桶數只增不刪中間」的場景（如 sharded cache）。

- **有界負載的一致性雜湊（consistent hashing with bounded loads）**：Google 2016 的改進，在環的基礎上加一個「每節點負載上限」，key 若順時針第一個節點已滿就繼續找下一個。保證沒有節點超載超過設定倍數，代價是搬遷量略增。Vimeo、部分 LB 在用。

- **和 Ch 27 分片的接合**：一致性雜湊是 **hash 分片**的再平衡引擎——它決定「哪個 key 屬於哪個節點/shard」。但它本身不管複製與共識：真實系統（Cassandra）在一致性雜湊決定的「主節點」之上，再往環的順時針方向取 R 個節點當副本（replication factor），這 R 個節點用 quorum 複製。**一致性雜湊定位置、quorum/Raft 管複製**——又是 Ch 27「分片 × 複製正交」的體現。

- **加密雜湊 vs 非加密雜湊**：本章用 FNV（非加密，快）。生產環境的一致性雜湊常用 MD5/SHA 的前幾個 byte 當環座標——不是為了安全，是因為加密雜湊的雪崩性極好、分布極均勻。但慢。非加密雜湊（murmur、xxhash、FNV）配好的混合函式通常夠用且快得多。

## 本章重點整理

- 樸素 `hash mod N` 的致命傷：加減一台機器，除數變、**幾乎全部 key 重映射**（實測 4→5 台搬 80%），觸發全叢集資料大搬家。
- 一致性雜湊把節點位置由「節點自己的雜湊」決定、與 N 解耦，加減節點變成**局部操作**，只影響環上相鄰一段——搬遷量 O(K/N) ≈ 1/N。
- **虛擬節點**是必需品：單點環搬遷少但負載嚴重不均（+75%）；每節點幾百個 vnode 才能同時做到搬遷 ≈1/N + 負載勻（±5%）。
- 實測關鍵：一致性雜湊加節點時，搬遷的 key **全部流向新節點、既有節點之間零搬遷**——`% N` 永遠做不到，這是快取友善的根本。
- **rendezvous (HRW)** 是另一條路：`argmax hash(key, node)`，天生均衡免調參、搬遷同樣 ≈1/N，代價是查詢 O(N)（節點多時慢）。
- 一致性雜湊是 **hash 分片**的再平衡引擎，只管「位置」；複製由其上的 quorum/Raft 管——分片與複製正交（承 Ch 27）。

## 自我檢核

- [ ] 不看筆記，我能解釋為什麼 `hash mod N` 加一台機器會重映射幾乎全部 key（而非 1/N）
- [ ] 我能描述一致性雜湊環的查詢規則（順時針找第一個節點）與加節點時「只影響一段」的機制
- [ ] 我能講清楚虛擬節點解決什麼問題，以及「只驗搬遷比例不驗負載均衡」會漏掉什麼
- [ ] 我能說出實測裡「搬遷全流向新節點、既有節點零搬遷」為什麼是一致性雜湊的核心價值
- [ ] 我能比較一致性雜湊環與 rendezvous hashing 的取捨（均衡度、查詢複雜度、是否要調參）
- [ ] 我知道一致性雜湊「不能」解決什麼（單一超熱 key、複製），這些歸誰管

## 延伸閱讀

- **[Consistent Hashing and Random Trees](https://www.akamai.com/site/en/documents/research-paper/consistent-hashing-and-random-trees-distributed-caching-protocols-for-relieving-hot-spots-on-the-world-wide-web-technical-publication.pdf)** — Karger et al., STOC（1997）
  - **這篇說什麼**：一致性雜湊的原始論文，為 web 快取熱點問題而生（後來成了 Akamai CDN 的基礎）
  - **讀哪裡**：第 4 節「Consistent Hashing」的定義與 monotonicity/balance/spread 性質，本章直覺的嚴謹版
  - **前提**：讀得懂本章即可，論文比想像中好讀

- **[Dynamo: Amazon's Highly Available Key-value Store](https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf)** — DeCandia et al., SOSP（2007）
  - **這篇說什麼**：一致性雜湊 + 虛擬節點在生產系統的落地，含「為什麼要 virtual node」「node heterogeneity」的工程理由
  - **讀哪裡**：第 4.2 節「Partitioning」與 4.3「Replication」——正好把本章的「一致性雜湊定位置 + 順時針取 R 個當副本」講清楚

- **[A Fast, Minimal Memory, Consistent Hash Algorithm (Jump Hash)](https://arxiv.org/abs/1406.2294)** — Lamping & Veach, Google（2014）
  - **這篇說什麼**：進階提到的 jump consistent hash，用 O(1) 空間、O(log N) 時間達到最小搬遷，數學很漂亮
  - **讀哪裡**：整篇很短（幾頁），核心是那個跳躍迴圈；讀懂它會對「一致性雜湊不一定要環」有新認識
  - **前提**：一點機率直覺

- **[Rendezvous Hashing (Wikipedia) 與 Thaler & Ravishankar 原始論文](https://en.wikipedia.org/wiki/Rendezvous_hashing)** — Thaler & Ravishankar（1998）
  - **這是什麼**：HRW 的來源與 weighted rendezvous 的推導，補足本章 HRW 那節
  - **讀哪裡**：Wikipedia 條目講清楚基本版與 weighted 版；要深挖再追原始論文
  - **注意**：weighted rendezvous 的對數變換是讓「按容量分配」正確的關鍵，值得看

一致性雜湊解決了「key 該落在哪個節點」與「加減節點如何最小搬遷」。但還有一個前提沒解決：**節點加入/離開叢集，其他節點怎麼知道？誰宣布一個節點死了？** 這是成員管理與失敗偵測的問題，下一章用 SWIM/Gossip 來解。

→ [Ch 29 成員與失敗偵測：SWIM/Gossip](./29-membership-failure-detection-swim.md)
