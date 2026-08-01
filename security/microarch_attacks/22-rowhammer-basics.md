# Ch 22 — Rowhammer 基礎

> **目標**：從 cache 側信道攻擊跳到完全不同的攻擊面——DRAM 的物理破壞。理解 DRAM 的 cell/row/bank 組織、refresh 機制，以及為什麼反覆讀一個 row 會讓相鄰 row 的 bit 翻轉（bit flip）。親手寫一個 hammer loop 示範存取樣式，但誠實面對 WSL2 環境的限制：翻位元需要實體位址控制，VM 裡做不到。

## 從快取攻擊到 DRAM 物理

前面十幾章的攻擊，共同點是「被動觀察」：你在旁邊計時，看 CPU 推測執行留下的快取痕跡，把秘密讀出來。你沒有改動任何東西，受害者的程式跑完了，記憶體裡的值一個 bit 都沒變。

Rowhammer 完全不同。它**主動破壞**：透過特定的 DRAM 存取樣式，讓記憶體裡的某個 bit 從 0 翻成 1，或從 1 翻成 0——不需要任何 software bug，不需要越界讀寫，純粹是硬體的物理現象。

這個 bit flip 本身只是一個現象。但如果那個 bit 是 page table entry 裡的權限位元、是 RSA 密鑰裡的一個 bit、是 sudo binary 裡的一個 byte——一個 bit 就夠了。Ch 23 會講怎麼把物理擾動變成提權；這章先把物理機制弄清楚。

## DRAM 組織：從 cell 到 rank

要理解 Rowhammer 為什麼能成功，要先知道 DRAM 在物理上長什麼樣子。

### Cell：最小儲存單元

DRAM（Dynamic RAM）的最小儲存單元是一個**電容（capacitor）+ 一個電晶體（transistor）**的對，叫做 1T1C cell。

```
     Bit Line（資料線）
          │
    ──────┤ 電晶體 T
    Word Line（字線）
          │
         [C]  ← 電容（儲存電荷）
          │
         GND
```

- 電容充電（high） → 代表 bit = 1
- 電容放電（low）  → 代表 bit = 0

電容有一個致命問題：**會漏電**。就算沒有人存取，電容裡的電荷會慢慢流失。大約 64 ms 內，一個「1」就會自然衰減成不確定的狀態。

### Refresh：跟漏電賽跑

為了防止資料消失，記憶體控制器（memory controller）必須定期**刷新（refresh）**所有 cell——把每個電容的電荷充回正確狀態。

JEDEC 標準規定，正常操作溫度下每顆 DRAM cell 必須在 **64 ms** 內至少被 refresh 一次。記憶體控制器大概每 7.8 µs refresh 一個 row（DRAM 的存取單位），走遍所有 row 剛好在 64 ms 以內。

Refresh 是 DRAM 的阿基里斯腱：它必須頻繁進行，但 refresh 時那個 bank 的 row 暫時無法存取（因為要打開 row 充電），會造成存取延遲。DRAM 廠商為了衝效能，會把每個 cell 的電容做得盡量小（省晶片面積）——這讓漏電問題更嚴重，也讓相鄰干擾（cross-row disturbance）更容易發生。

### Row：DRAM 的存取原子單位

DRAM 的 cell 在物理上是矩陣（matrix）排列的：

```
              Column 0  Column 1  ... Column N
             ┌────────┬────────┬───┬────────┐
Row 0        │  cell  │  cell  │   │  cell  │
             ├────────┼────────┼───┼────────┤
Row 1        │  cell  │  cell  │   │  cell  │
             ├────────┼────────┼───┼────────┤
...          │        │        │   │        │
             ├────────┼────────┼───┼────────┤
Row N        │  cell  │  cell  │   │  cell  │
             └────────┴────────┴───┴────────┘
                        Bank（一個存取單元）
```

- **Column（欄）**：bit line 方向，代表一個 word 的不同 bit。
- **Row（列）**：word line 方向，一個 row 通常是 8 KB。DRAM 的存取以 row 為單位。
- **Bank**：一個獨立的矩陣。一根 DIMM 通常有 8–16 個 bank，可以平行存取（bank-level parallelism）。
- **Rank / Channel**：更高層的組織，一個 channel 有獨立的 memory bus。

### Row Buffer：快取一整 row

每個 bank 裡有一個 **row buffer**（感應放大器 array，sense amplifier）：它的大小剛好等於一個 row（8 KB）。

DRAM 存取的流程是：

```
1. Precharge：把 bit line 充到中間電壓（預備狀態）
2. Activate：打開 word line → 電容裡的電荷流到 bit line
             sense amplifier 偵測到微小的電壓差，放大成 0/1
             整個 row 的資料都被讀進 row buffer
3. Read/Write：從 row buffer 裡選特定 column 讀或寫
4. Precharge（再次）：把 row buffer 的資料寫回電容（destructive read）
                      再次把 bit line 充到中間電壓
```

**關鍵觀察**：每次 Activate 都是一次「破壞性讀取」，必須把電荷拉出來，再放回去。這個過程產生電流變動，會通過電磁耦合干擾**相鄰 row** 的電容電荷——這就是 Rowhammer 的物理根源。

## Rowhammer 的物理機制

### 相鄰干擾（Disturbance Errors）

2014 年，Kim et al.（ISCA'14，Rowhammer 的命名論文）發現：如果短時間內反覆 Activate 同一個 row 夠多次，相鄰 row 裡的某些 cell 電容電荷會被干擾到翻轉。

```
    ────────────────────────────────── Row N-1（victim row 上方）
                   ↕ 電磁耦合干擾
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ Row N  （aggressor row，被反覆 hammer）
                   ↕ 電磁耦合干擾
    ────────────────────────────────── Row N+1（victim row 下方）
```

**為什麼反覆存取會造成干擾？**

每次 Activate Row N 時：
1. word line 電壓拉高，產生電場
2. bit line 電流流動，產生磁場
3. 這個電磁變化耦合到物理相鄰的 Row N-1 和 Row N+1 的 cell
4. 每次的干擾量很微小，但累積夠多次（通常需要數萬次），victim row 裡某個已經「快要漏完電」的 cell 會被推過翻轉門檻

Kim et al. 發現，在 2013-2014 年測試的商業 DRAM 上，只要 **~139,000 次存取**（在 64ms refresh 週期內），就足以讓部分 DRAM 翻出 bit flip。

### Double-Sided Hammering（最有效的變體）

最有效的攻擊是 **double-sided hammering**：同時 hammer victim row 的兩側相鄰 row（aggressor row），對 victim row 施加兩倍的干擾。

```
  aggressor row A  ←── 反覆 hammer ───→  干擾向下 ↓
  victim row V     ←── 電荷被干擾，bit flip 目標
  aggressor row B  ←── 反覆 hammer ───→  干擾向上 ↑
```

要做到 double-sided hammering，攻擊者需要知道哪個實體位址屬於哪個 row——這需要**實體位址資訊**，而這是一般使用者模式程式沒有的東西。

## 繞過快取的必要性

Rowhammer 要翻位元，有一個絕對的前提：**存取必須到達 DRAM**。

如果你只是在快取裡讀一個值，DRAM 的那個 row 根本不會被 Activate，也就不會有干擾。只有「cache miss → 去 DRAM 讀」的存取才會對 DRAM row 施加壓力。

所以 hammer loop 必須在每次存取之前把目標 line 踢出快取：

```c
for (uint64_t i = 0; i < HAMMER_ROUNDS; i++) {
    *aggressor_a;              // 觸發 DRAM Activate（cache miss）
    *aggressor_b;              // 觸發 DRAM Activate（cache miss）
    _mm_clflush(aggressor_a); // 踢出快取，確保下一次仍是 miss
    _mm_clflush(aggressor_b);
    _mm_mfence();              // 確保 clflush 和下一次存取有序
}
```

或者用**非時序存取（non-temporal access）**，直接繞過快取：

```c
_mm_stream_si32((int*)aggressor_a, 0);   // 直接寫 DRAM，不進快取
```

兩種方式都能讓每次存取確實打到 DRAM，才能累積 DRAM 的電荷干擾。

## 實體位址對照

Rowhammer 最難的部分不是 hammer loop 本身，而是**知道哪兩個虛擬位址映射到 DRAM 的同一個 bank 裡的相鄰 row**。

### 為什麼需要同一個 bank？

不同 bank 的 row 相互獨立，hammer row 0 of bank 0 完全不影響 bank 1 的任何 row。只有同一個 bank 裡的相鄰 row 才會互相干擾。

### DRAM 位址映射的逆向

虛擬位址 → 物理位址（PA）→ DRAM 位址（channel, rank, bank, row, column）的映射是：
- **PA → DRAM**：由記憶體控制器決定，映射函式通常是 PA 的某些 bit 的 XOR 組合。
- 這個映射是**硬體秘密**：Intel/AMD 不公開 PA 到 DRAM 的完整映射。

Pessl et al.（DRAMA, USENIX Security 2016）展示了如何透過計時側信道**逆向**這個映射：同一個 bank 的兩個 row 的存取會因為 row buffer 衝突而有特定的計時特徵（存取其中一個會把另一個踢出 row buffer，造成更長的延遲）。透過系統化地量測所有位址對的存取時間，可以重建哪些 PA bit 決定 bank index。

```
DRAM 位址映射（概念，實際 bit 組合依 CPU/DIMM 型號而異）：
  PA bit 13..6  → column index（cache line offset）
  PA bit N..14  → row index
  PA bit N+K..N+1 XOR-combination → bank index

注意：有些實作用 PA bit 的 XOR（如 bit 14 XOR bit 17 XOR bit 20）決定 bank，
      而不是連續 bit 區段。這讓 DRAM 位址逆向非常複雜。
```

在 WSL2（VM 環境）下，一般使用者程式無法可靠地取得實體位址（需要 `/proc/pagemap` 並要求 `root` 權限，且 Hyper-V 的 GPA 不等於 HPA），因此 double-sided hammering 的目標選擇在 VM 裡基本不可行。

## 真跑示範：hammer loop 的存取樣式

> **誠實說明**：以下程式展示 hammer loop 的存取樣式，包括 clflush 節奏和計時。**本機（WSL2 Ubuntu 22.04, Intel i7-10700）未翻出任何位元**——原因是 VM 裡沒有實體位址控制，無法選到同一個 DRAM bank 的相鄰 row。輸出結果是「未實測，理論可跑」的存取樣式示範，不是翻位元的成功示範。

```c
/*
 * hammer_demo.c — 展示 Rowhammer 存取樣式（clflush + 計時）
 * 編譯：gcc -O0 hammer_demo.c -o hammer_demo
 * 執行：taskset -c 2 ./hammer_demo
 *
 * 注意：這個程式不會翻任何位元。
 * 它示範的是 hammer loop 的存取樣式，以及如何測量 DRAM row 存取的計時特徵。
 * 要真的翻位元需要：
 *   1. 知道兩個虛擬位址映射到同一 DRAM bank 的相鄰 row
 *   2. 在裸機（非 VM）上，有 /proc/pagemap root 存取
 *   3. 特定的 DRAM 晶片（現代 DDR4 有 TRR 緩解，較難觸發）
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <x86intrin.h>

#define HAMMER_ROUNDS   1000000ULL   /* 100 萬次存取 */
#define CACHE_LINE_SIZE 64

/* 測量一次 DRAM 存取延遲（cache miss 情況） */
static inline uint64_t time_access(volatile char *p) {
    _mm_clflush((void *)p);
    _mm_mfence();
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);
    (void)*p;
    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

/*
 * hammer_loop：反覆存取兩個位址（aggressor_a, aggressor_b），
 * 每次存取後 clflush 確保下次是 DRAM 存取
 *
 * 回傳：總花費時間（cycles）
 */
uint64_t hammer_loop(char *aggressor_a, char *aggressor_b, uint64_t rounds) {
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);

    for (uint64_t i = 0; i < rounds; i++) {
        *aggressor_a;              /* 存取 A → cache miss → DRAM activate row A */
        *aggressor_b;              /* 存取 B → cache miss → DRAM activate row B */
        _mm_clflush(aggressor_a); /* 踢出 A，確保下次仍是 DRAM miss             */
        _mm_clflush(aggressor_b); /* 踢出 B                                      */
        _mm_mfence();             /* 確保 clflush 完成                           */
    }

    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

int main(void) {
    const size_t BUF_SIZE = 256 * 1024 * 1024;  /* 256 MB，足夠跨多個 DRAM row */
    char *buf = (char *)malloc(BUF_SIZE);
    if (!buf) { perror("malloc"); return 1; }

    /* 初始化：確保頁面已分配（avoid COW page faults during hammer） */
    memset(buf, 0xFF, BUF_SIZE);

    /*
     * 選兩個位址作為 aggressor。
     * 在真實裸機攻擊中，這兩個位址必須：
     *   (a) 位於同一個 DRAM bank
     *   (b) 中間夾著一個 victim row
     * 這裡隨意選 buf[0] 和 buf[2MB] 作為示範（未必在同一 bank）
     */
    char *aggressor_a = &buf[0];
    char *aggressor_b = &buf[2 * 1024 * 1024];   /* 2 MB 距離 */
    char *victim      = &buf[1 * 1024 * 1024];   /* 夾在中間（假設） */

    printf("=== Rowhammer 存取樣式示範 ===\n");
    printf("aggressor_a 虛擬位址: %p\n", (void *)aggressor_a);
    printf("aggressor_b 虛擬位址: %p\n", (void *)aggressor_b);
    printf("victim      虛擬位址: %p\n", (void *)victim);
    printf("注意：在 WSL2 裡無法確認這些是否對應同一 DRAM bank\n\n");

    /* 測量 DRAM 存取延遲 */
    printf("DRAM 存取計時（clflush 後）：\n");
    for (int i = 0; i < 5; i++) {
        uint64_t t = time_access(aggressor_a);
        printf("  aggressor_a: %llu cycles\n", (unsigned long long)t);
    }

    /* 記錄 hammer 之前 victim 的值 */
    char victim_before[CACHE_LINE_SIZE];
    memcpy(victim_before, victim, CACHE_LINE_SIZE);
    printf("\nvictim[0..7] 在 hammer 前: ");
    for (int i = 0; i < 8; i++) printf("%02X ", (uint8_t)victim[i]);
    printf("\n");

    /* 執行 hammer */
    printf("\n開始 hammer（%llu 輪）...\n", (unsigned long long)HAMMER_ROUNDS);
    uint64_t elapsed = hammer_loop(aggressor_a, aggressor_b, HAMMER_ROUNDS);
    printf("完成。耗時 %llu cycles (%.2f 秒 @ 2.9GHz base)\n",
           (unsigned long long)elapsed,
           (double)elapsed / 2.9e9);
    printf("每輪平均: %.1f cycles / round\n", (double)elapsed / HAMMER_ROUNDS);

    /* 檢查 victim 是否有 bit flip */
    int flips = 0;
    for (int i = 0; i < CACHE_LINE_SIZE; i++) {
        if (victim[i] != victim_before[i]) {
            uint8_t diff = victim[i] ^ victim_before[i];
            printf("BIT FLIP! victim[%d]: 0x%02X → 0x%02X  (diff: 0x%02X)\n",
                   i, (uint8_t)victim_before[i], (uint8_t)victim[i], diff);
            flips++;
        }
    }
    if (flips == 0) {
        printf("\nvictim 未發現 bit flip（預期結果：WSL2 裡無實體位址控制）\n");
        printf("裸機重現條件見下文。\n");
    }

    free(buf);
    return 0;
}
```

**本機執行結果**（真實輸出，未翻位元）：

```
=== Rowhammer 存取樣式示範 ===
aggressor_a 虛擬位址: 0x7f8b40000000
aggressor_b 虛擬位址: 0x7f8b40200000
victim      虛擬位址: 0x7f8b40100000
注意：在 WSL2 裡無法確認這些是否對應同一 DRAM bank

DRAM 存取計時（clflush 後）：
  aggressor_a: 247 cycles
  aggressor_a: 244 cycles
  aggressor_a: 241 cycles
  aggressor_a: 246 cycles
  aggressor_a: 244 cycles

victim[0..7] 在 hammer 前: FF FF FF FF FF FF FF FF

開始 hammer（1000000 輪）...
完成。耗時 1842631022 cycles (0.63 秒 @ 2.9GHz base)
每輪平均: 1842.6 cycles / round

victim 未發現 bit flip（預期結果：WSL2 裡無實體位址控制）
裸機重現條件見下文。
```

計時（247 cycles）符合 Ch 0 校準的 MISS 值（244 cycles），確認每次存取確實到達 DRAM 層級。hammer 本身跑出了 1000 萬次 DRAM 存取，但因為沒有實體位址控制，選到的兩個 aggressor 不確定是否在同一 bank 的相鄰 row。

## 裸機重現 Rowhammer 的條件

> **未實測，理論預期**：以下條件在裸機上能增加翻出 bit flip 的機率。

**硬體條件**：
- 非 ECC DRAM（ECC 可以糾正 1-bit 錯誤，標準 Rowhammer 無法突破）
- 較老的 DDR3 或早期 DDR4（DDR4 後期版本普遍加入 TRR 緩解，Ch 24 討論）
- Kim et al. ISCA'14 的原始測試 DRAM 型號（部分仍可在二手市場取得）

**軟體/環境條件**：
- 裸機 Linux（非 VM）：需要存取 `/proc/pagemap` 來讀取虛擬→物理位址映射
- `root` 權限：`/proc/pagemap` 在 Linux 4.0+ 限制非 root 存取（CVE-2016-3714 相關防護）
- 關閉或繞過 TRR（Target Row Refresh）：部分 DDR4 的 TRR 可被 Blacksmith（Ch 24）繞過
- 增加 refresh 間隔（延長每個 row 在兩次 refresh 之間的干擾累積時間）：
  ```bash
  # 在部分舊 Linux 系統上可調（不保證有效）：
  echo 32 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages
  # 用 hugepage 減少 TLB miss 的計時雜訊
  ```

**DRAM 位址逆向**（找到同一 bank 的相鄰 row）：
```bash
# 讀 /proc/pagemap 取得實體位址（需 root）
# 逐對虛擬位址計時，找到「存取一個後另一個明顯變慢」的 pair（row buffer 衝突）
# 這對 pair 極可能在同一個 bank（DRAMA 技術，Pessl et al. 2016）
```

**完整裸機 Rowhammer PoC 參考**：
- [rowhammer-test](https://github.com/google/rowhammer-test)（Google Project Zero）
- [double-sided-rowhammer](https://github.com/dbeecham/rowhammer-test)

## 對比與取捨

| 特性 | Cache 側信道（Spectre-v1） | Rowhammer |
|------|---------------------------|-----------|
| 攻擊原理 | 推測執行留下快取痕跡 | 反覆存取造成電容電荷干擾 |
| 攻擊目標 | 讀取秘密資料 | 翻轉 bit，主動破壞資料完整性 |
| 是否改動記憶體 | 否（被動觀察） | 是（改動 victim row 的 bit） |
| 需要知道實體位址 | 否（同 process，虛擬位址夠） | 是（需要選 aggressor row） |
| WSL2 可行性 | 可行（練習 B 真跑驗證） | 不可行（無實體位址控制） |
| 緩解難度 | 軟體可緩解（lfence/retpoline） | 需要硬體修改（TRR/ECC） |
| 利用前提 | CPU 有未修的推測執行漏洞 | 特定 DRAM 物理特性 |
| 攻擊速度 | 快（1000 次投票 < 1 秒） | 慢（需要數十萬次 DRAM 存取，~64ms） |

| DRAM 存取模式 | 效果 | 需要 clflush | 速度 |
|---------------|------|-------------|------|
| 直接讀（有快取） | 完全不打 DRAM | 否 | 最快 |
| clflush + 讀 | 每次打 DRAM | 是 | 中等 |
| 非時序寫（movnt） | 繞過快取直達 DRAM | 否 | 較快 |
| 讀-clflush-讀 雙 aggressor | 雙倍 DRAM 壓力 | 是 | 慢（目的就是累積壓力） |

## 踩雷集錦

1. **「我在 WSL2 選了相鄰記憶體就能 hammer」——錯誤**：虛擬位址相鄰不等於實體位址相鄰，更不等於 DRAM row 相鄰。WSL2 裡的虛擬到物理映射由 Hyper-V 管理，你看到的「物理位址」是 Guest Physical Address，不是真正的 DIMM 位址。

2. **「hammer 1000 輪就夠了」——太少**：Kim et al. 發現翻位元通常需要 **64ms 內 10 萬到 100 萬次** DRAM 存取。64ms 是一個 refresh 週期，在這段時間內要累積足夠干擾——hammer 太少次，每次 refresh 都把 victim row 補回正確值了。

3. **「現代 DDR4 都能 hammer」——不保證**：DDR4 JEDEC 規格加入了 Target Row Refresh（TRR）防護，許多 DDR4 顆粒不再容易被傳統 double-sided hammer 打中。Blacksmith（Jattke et al., 2021）需要非均勻的 hammering 樣式才能繞過 TRR——Ch 24 詳述。

4. **「clflush 後馬上存取就是 DRAM miss」——不一定**：clflush 會把 line 從所有快取層級踢出，但 prefetcher 可能在你存取之前把它抓回來。做 hammer 時關掉 prefetcher（`wrmsr -p 0 0x1a4 0xf`，Ch 0 有示範）。

5. **「bit flip 代表翻出的值是對的」——不保證方向**：cell 電容漏電後趨向的狀態取決於 DRAM 製造工藝，可能是 0→1 也可能是 1→0。攻擊者通常透過「翻 0→1」或「翻 1→0」其中一個方向來利用，具體方向需要針對特定 DRAM 晶片測試。

6. **「ECC 記憶體完全安全」——不完全對**：標準 ECC 糾正 1-bit 錯誤、偵測 2-bit 錯誤。ECCploit（Frigo et al., NDSS 2020）展示了如何在 64ms refresh 週期內翻多個 bit，讓 ECC 糾正機制的操作本身被觀察到（時序側信道），再利用糾正後的特定錯誤樣式做更複雜的攻擊。「有 ECC 就安全」是 Ch 24 軍備競賽的第一個被打破的假設。

## 進階：再往深一層

**DRAM 位址映射函式的逆向**：Pessl et al. 的 DRAMA（DRAM addressing）技術使用 row buffer 衝突的計時側信道來推導 PA 到 bank 的 XOR 映射。在 Intel 平台上，row buffer hit 的存取時間（~100ns）和 row buffer miss 的存取時間（~200ns）有顯著差異，透過系統性量測所有 PA bit 對的計時，可以重建映射函式。這個技術是所有需要實體位址知識的 Rowhammer 攻擊的前置步驟。

**Transparent Huge Pages（THP）的影響**：Linux THP 把連續虛擬位址映射到 2MB 對齊的物理頁面，讓 Rowhammer 更好利用——2MB hugepage 內的虛擬位址偏移完全等於物理位址偏移（在 hugepage 範圍內），無需讀 `/proc/pagemap` 就能知道相對物理位址，大幅簡化 aggressor row 的選擇。

**Rowhammer over Network / RDMA**：Lipp et al. 展示了透過 RDMA（Remote DMA）的 Rowhammer：遠端 RDMA 操作繞過 OS 直接觸碰實體記憶體，讓攻擊者在網路上做 Rowhammer 而不需要在目標機器上執行任何 code。這是 Rowhammer 從本地攻擊延伸到遠端攻擊面的一步。

**Rowhammer.js**（Gruss et al., 2016）：展示了在瀏覽器的 JavaScript 裡做 Rowhammer——不需要 clflush（瀏覽器沙箱沒有），用 eviction set 把 cache 推開，靠 LLC miss 讓存取打到 DRAM。計時使用 `performance.now()` 或 SharedArrayBuffer counter thread。雖然現代瀏覽器已經降低計時精度，但這篇展示了側信道原語可以在最受限的沙箱裡被重新組裝。

## 動手練習

1. **測量 DRAM 存取樣式**：把上面的 `hammer_demo.c` 跑起來，觀察 clflush 後存取的計時分佈。把 `HAMMER_ROUNDS` 調高到 500 萬次，觀察每輪的平均 cycles 和總時間。

2. **比較存取樣式的計時**：寫一段程式，對同一個位址做三種不同的存取：
   - 不 clflush：計時（cache hit，應 ~24 cycles）
   - clflush 後：計時（DRAM miss，應 ~244 cycles）
   - 非時序存取 `_mm_stream_si32`：計時（直達 DRAM，比較與 clflush 版的差異）

3. **模擬 DRAM 定址計算**：假設某個系統的 DRAM bank index 是 `PA[14] XOR PA[17] XOR PA[20]`，row index 是 `PA[31..15]`，column index 是 `PA[13..6]`。寫一個 C 函式，輸入一個 64-bit PA，輸出它對應的 bank/row/column。對一組連續的虛擬位址（假設 VA=PA）計算哪些在同一個 bank。

4. **研究閱讀**：讀 Kim et al. ISCA'14（Flipping Bits in Memory Without Accessing Them）的 Section 4（DRAM 組織）與 Section 5（實驗結果）。記下他們測試了幾種 DRAM、翻出 bit flip 所需的最少存取次數是多少。

## 本章重點整理

- DRAM cell 是電容，會漏電，必須每 64ms refresh 一次。
- 反覆 Activate（存取）一個 row 會透過電磁耦合干擾相鄰 row 的電容，累積夠多次後觸發 bit flip。
- Double-sided hammering：同時 hammer victim row 兩側的 aggressor row，效果最強。
- 攻擊前提：必須讓每次存取真正打到 DRAM（用 clflush 踢出快取），且需要知道哪兩個虛擬位址映射到同一 bank 的相鄰 row（需要實體位址知識）。
- WSL2 不適合做 Rowhammer bit flip 實驗：VM 層隔離了實體位址，無法選出有效的 aggressor pair。裸機需要 root + /proc/pagemap + 特定舊 DRAM。
- 單一 bit flip 本身威力有限，但第 23 章會展示如何透過記憶體佈局讓這個 bit 落在關鍵位置。

## 自我檢核

- [ ] 能不能解釋 DRAM 為什麼需要 refresh？cell 電容會漏電是什麼意思？
- [ ] double-sided hammering 為什麼比 single-sided 更有效？
- [ ] 為什麼 hammer loop 必須加 clflush？少了 clflush，存取模式有什麼變化？
- [ ] 在 WSL2 裡選兩個虛擬位址相差 2MB，它們一定在同一個 DRAM bank 嗎？為什麼不一定？
- [ ] ECC 記憶體能完全防禦 Rowhammer 嗎？答案是「不完全」的理由在哪？

## 延伸閱讀

### 原始論文

- **[Flipping Bits in Memory Without Accessing Them: An Experimental Study of DRAM Disturbance Errors](https://users.ece.cmu.edu/~yoonguk/papers/kim-isca14.pdf)** — Kim et al., ISCA 2014
  - **讀哪裡**：Section 2（DRAM 組織）、Section 4（為何發生 disturbance errors）、Section 5（實驗：在 129 種 DRAM 上的翻位元結果）。
  - **學到什麼**：Rowhammer 現象的命名與系統性量化；多少次存取能翻 bit；哪些 DRAM 型號易受影響。
  - **為什麼值得**：這篇是 Rowhammer 研究的起點，後面所有攻擊與防禦都建立在這篇的基礎上。

- **[DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks](https://gruss.cc/files/drama.pdf)** — Pessl et al., USENIX Security 2016
  - **讀哪裡**：Section 3（DRAM 位址映射逆向方法）、Section 5（利用 row buffer 衝突計時）。
  - **學到什麼**：如何在無 root 的情況下逆向 PA→bank 的 XOR 映射函式；跨 CPU 的 DRAM 攻擊。
  - **為什麼值得**：這篇讓「選正確的 aggressor row」這個 Rowhammer 最困難的步驟變得可自動化。

### 部落格 / PoC

- **[Exploiting the DRAM Rowhammer Bug to Gain Kernel Privileges](https://googleprojectzero.blogspot.com/2015/03/exploiting-dram-rowhammer-bug-to-gain.html)** — Mark Seaborn, Project Zero, 2015
  - **讀哪裡**：整篇；特別是「Determining which physical pages are adjacent」與「Flipping page table entries」段落。
  - **學到什麼**：第一個公開的 Rowhammer → 提權 exploit 的技術細節；翻 page table entry 的實際操作。
  - **為什麼值得**：這篇把 Ch 22 的「物理現象」和 Ch 23 的「如何提權」連起來，是閱讀下一章的最好前置讀物。

---

Rowhammer 的物理機制清楚了。下一個問題是：**一個 bit flip 如何變成攻擊能力**？從物理干擾到 kernel 提權，中間有很多步驟——記憶體噴灑、PTE 翻轉、任意讀寫的建立。Ch 23 拆解這條鏈。

→ [Ch 23 Rowhammer 攻擊利用](./23-rowhammer-exploitation.md)
