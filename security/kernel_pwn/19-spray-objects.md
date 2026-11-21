# Ch 19 — Heap spray 物件大全：msg_msg / sk_buff / pipe_buffer / tty_struct / seq_operations / user_key_payload

> **目標**：把 kernel heap 利用的「牌庫」一次攤開。學完你要能對著一個 UAF/overflow 的 size 說出「這個 cache 我能 spray 哪些可控物件、哪些能當 leak 來源、哪些能當控流載體」。這是一章**工具箱 / 查表**章——[Ch 18](./18-uaf-double-free.md)、[Ch 21](./21-heap-to-rip.md)、[Ch 22](./22-cross-cache.md) 都會回來翻它。

> **環境**：Linux 6.6 LTS、x86-64。各物件的 size/cache、欄位 offset **隨版本/config 變動**，本章給的是概念與數量級，實際值以你的 kernel 用 `pahole`/gdb 確認。未實測，理論預期行為。

## 為什麼要背牌庫

[Ch 17](./17-heap-overflow.md)、[Ch 18](./18-uaf-double-free.md) 反覆出現同一個動作：**spray 一批「我能控制內容、size 落在目標 cache」的物件**。能不能利用一個 heap bug，一半取決於「目標 cache 裡，我手上有沒有合適的牌」。這章就是把最常用的牌，連同它們的 size、怎麼從 user-space 配出來、內容可控到什麼程度、適合當什麼角色，整理成你隨時能查的表。

牌分三種角色（對應 [Ch 10](./10-vulnerability-taxonomy.md) 的升級方向）：

```
  spray 物件的三種角色
  ├─ 可控內容載體：整塊 bytes 幾乎任我填 → 佔位、填假結構、填假函式指標
  ├─ leak 來源    ：內含 kernel 指標（ops/自身/相鄰）→ 讀出來推 KASLR base
  └─ 值錢 victim  ：開頭有函式指標/長度/指標 → 被蓋後給控流或原語升級
```

同一個物件常身兼多角：`tty_struct` 既是 leak 來源（有 ops 指標）又是控流 victim（蓋 ops）；`msg_msg` 既是可控載體又能當 leak/任意讀寫。下面逐一拆。

## 三個維度看一張牌

評估任何 spray 物件，問三件事：

| 維度 | 問題 | 為什麼重要 |
|---|---|---|
| **size / cache** | 它落在哪個 kmalloc-N？size 可調嗎？ | 要和目標 bug 的 cache 對上（Ch 17/18） |
| **帳戶類別** | `GFP_KERNEL` 還是 `GFP_KERNEL_ACCOUNT`？ | cg 分流（Ch 9），帳戶不同配不到同 slab |
| **可控度** | user 能控哪幾個 bytes？整塊還是部分？ | 決定能填假結構/假指標，還是只能佔位 |

## 牌庫總表

| 物件 | 怎麼配（user 觸發） | size / cache | 可控度 | 最佳角色 |
|---|---|---|---|---|
| `msg_msg` (+ `msg_msgseg`) | `msgsnd()` System V 訊息 | 可調（header 48B + 內容，跨多個 cache） | 內容幾乎全可控 | 可控載體、leak、任意讀寫 |
| `sk_buff` (data) | `send()`/socket 收發 | 依資料長度可調 | data 區可控 | 可控載體、佔位 |
| `pipe_buffer` | `pipe()` + 寫入 | `pipe_buffer` 陣列落特定 cache | 部分（flags/ops/page） | 控流（ops）、Dirty Pipe 類 |
| `tty_struct` | `open("/dev/ptmx")` | ~0x2b0+，固定 cache（kmalloc-1k 級） | 低（固定結構） | leak（ops 指標）、控流（蓋 ops） |
| `seq_operations` | `open("/proc/self/stat")` 等 seq_file | 32B（kmalloc-32） | 無（全是函式指標） | leak + 控流（4 個函式指標） |
| `user_key_payload` | `add_key()` keyring | header + 可調內容 | 內容可控 | 可控載體、leak |
| `setxattr` 的 value | `setxattr()` | 任意 size（暫時配置） | 整塊可控 | 填假結構的「一次性」寫入 |
| `pg_vec` / packet ring | `setsockopt(PACKET_TX_RING)` | page 級 | 可控 | page-level spray（Ch 25） |

下面挑最關鍵的幾張細說。

## msg_msg：最萬用的一張

System V 訊息佇列。`msgsnd(qid, &msg, size, 0)` 會在 kernel 配一塊記憶體存你的訊息：開頭是 `struct msg_msg`（header，含 `m_list` 雙向鏈結、`m_type`、`m_ts` 長度、`next` 指向後續 segment），header 之後緊跟你的訊息內容。

為什麼它萬用：

- **size 任你調**：訊息多長，配多大。想打哪個 cache 就送多長的訊息（扣掉 header）。超過一個 slab 的大訊息會串 `msg_msgseg`，連大 cache 都能打。
- **內容幾乎全可控**：header 後面整塊是你的 bytes。拿來填假結構、假函式指標表最理想。
- **能當任意讀寫原語**：若你能改 `msg_msg.m_ts`（長度）或 `next` 指標（透過 overflow/UAF），`msgrcv` 就會越界讀、或沿著你改的 `next` 讀到任意位址——經典的「msg_msg 任意讀」。

```c
/* spray msg_msg（概念）：送 N 個訊息，每個內容可控、size 對齊目標 cache */
struct { long mtype; char buf[SIZE - 48]; } m;   /* 48 = msg_msg header 概數 */
m.mtype = 1;
memset(m.buf, 0x41, sizeof(m.buf));              /* 填你要的 bytes */
for (int i = 0; i < N; i++) msgsnd(qid[i], &m, sizeof(m.buf), 0);
```

> `msg_msg` 走 `GFP_KERNEL`（非 ACCOUNT），落一般 `kmalloc-N`。header 大小與欄位 offset 以你的 kernel 為準。

## tty_struct：leak + 控流一條龍

`open("/dev/ptmx")` 會配一個 `tty_struct`（相當大，約 0x2b0+ bytes，落在 kmalloc-1k 等級的 cache）。它的價值：

- **開頭附近有 magic 與 `ops` 指標**：`ops` 指向 `tty_operations`（一組函式指標），而 `tty_operations` 在 kernel text / 固定資料區。用 UAF/OOB **讀** 出 `ops` → 推 KASLR base（leak 來源）。
- **蓋 `ops` 指標 → 控流**：用 overflow/UAF **寫** 把 `ops` 指向你偽造的 fake ops table（放在你能控的記憶體），之後對這個 tty 做 ioctl/write，kernel 呼叫 `ops->xxx`，就跳到你的函式（控流載體）。[Ch 21](./21-heap-to-rip.md) 細講這條。
- **magic 值（`TTY_MAGIC = 0x5401`）** 方便你在 spray 後確認「哪一塊是 tty_struct」。

tty_struct 的缺點是 size 固定（不能調去打小 cache），且落在較大的 cache。它是「大 cache 題」的首選 victim。

## seq_operations：最小的控流牌

`open("/proc/self/stat")`（或其他 seq_file 介面）會配一個 `struct seq_operations`——**只有 32 bytes（kmalloc-32），而且整個結構就是 4 個函式指標**（`start`/`stop`/`next`/`show`）。

它的用途極純粹：**打 kmalloc-32 的 UAF/overflow 時，用它當 victim**。spray 一批 `seq_operations` 佔住洞，用 bug 把其中一個的某個函式指標（例如 `start`）蓋成你的值，再對那個 fd 做 `read`，kernel 呼叫 `seq->start()`，控流到手。因為它全是函式指標、又落在最小的 cache，是 kmalloc-32 題的教科書牌。

## pipe_buffer：控流與 Dirty Pipe 家族

`pipe()` 配一組 `struct pipe_buffer`（陣列）。每個 `pipe_buffer` 有 `page`（指向資料頁）、`offset`、`len`、`ops`（指向 `pipe_buf_operations`，函式指標表）、`flags`。

- 蓋 `ops` → 控流（和 tty 同理）。
- 動 `flags`（加上 `PIPE_BUF_FLAG_CAN_MERGE`）是 Dirty Pipe（CVE-2022-0847）那類的根，雖然 Dirty Pipe 本身是不同 bug，但 `pipe_buffer` 的 `flags`/`page` 欄位是很多 data-only 技術的著力點（[Ch 24](./24-dirty-cred.md)/[Ch 29](./29-data-only-attack.md)）。

## user_key_payload 與 setxattr：純粹的「可控 bytes」

兩者都不是「值錢 victim」，而是**把你指定的 bytes 放進 kernel heap 指定 size** 的工具：

- `add_key("user", desc, payload, plen, ...)` 配一個 `user_key_payload`（header + 你的 payload），size 由 `plen` 調。內容可控，可當載體與 leak（`payload` 前有 `rcu` 等欄位可能殘留指標）。
- `setxattr(path, name, value, size, 0)` 會**暫時**配一塊 `size` 大、內容全可控的 kernel buffer（在呼叫期間存在）。適合「我只需要在某個瞬間，讓某個 size 的 slab 上有一塊全可控內容」的 grooming/佔位，尤其配合 `userfaultfd`/FUSE 把那塊「卡住不釋放」（[Ch 41](./41-widening-race-window.md)）。

## 選牌的決策流程

```
  我的 bug 在哪個 cache？ size = ?
        │
   ┌────┴─────────────────────────────────────┐
   │ kmalloc-32 ──────────────▶ seq_operations（控流）                       │
   │ 中等 (64~512) ───────────▶ msg_msg（可控/leak/任意讀寫）、user_key_payload │
   │ 大 (~1k) ────────────────▶ tty_struct（leak+控流）                        │
   │ 要 page 級 ──────────────▶ pg_vec / packet ring（Ch 25）                  │
   │ 要「一次性全可控 bytes」──▶ setxattr                                       │
   └──────────────────────────────────────────────────────────────────────────┘
        │ 再確認：帳戶類別（GFP_KERNEL vs _ACCOUNT）和 bug 物件一致嗎？
```

兩個鐵律再強調一次：**size 要同 cache、帳戶類別要一致**（[Ch 9](./09-slub-hardening.md) 的 cg 分流）。很多「怎麼 spray 都佔不到」的鬼故事，根因就是 victim 是 `GFP_KERNEL_ACCOUNT` 而你的牌是 `GFP_KERNEL`，分在不同 slab。

## 踩雷集錦

1. **只看 size 不看帳戶類別**——`kmalloc-cg-N` 和 `kmalloc-N` 是不同 slab。victim 若由 user 觸發配置（常是 ACCOUNT），你的牌也得是 ACCOUNT（如某些 socket 路徑）才同 slab。選牌先查 GFP flag。
2. **以為 `seq_operations` 能當可控載體**——它全是函式指標、結構固定，你不能往裡塞任意 bytes。它只能當「被蓋的 victim」，不能當「填假資料的載體」。角色別搞反。
3. **tty_struct 拿去打小 cache**——它 size 固定在大 cache，打 kmalloc-64 的洞用不上。小 cache 用 seq_operations / msg_msg（調短）/ user_key_payload。
4. **spray 數量太少**——要有一個牌剛好落到 victim 的洞，通常得 spray 幾十到幾百個（視 slab 碎片與 grooming）。spray 三五個就想中，多半落空。數量是 grooming（[Ch 20](./20-heap-grooming.md)）要調的。
5. **msg_msg 忘了 header 佔掉的空間**——你要打 kmalloc-64，送的訊息內容不是 64 而是 `64 - sizeof(msg_msg header)`。沒扣 header，你的物件落到下一個 cache 去了。
6. **setxattr 的可控 buffer 用完就釋放**——它只在 syscall 執行期間存在，呼叫一返回就 free。要「佔住」得配合阻塞技巧（userfaultfd/FUSE）卡在半途，否則佔位瞬間即逝。

## 進階：牌庫是會變的

這份牌庫是攻防拉鋸的快照。每當某張牌太好用，kernel 就會 hardening 它：

- `tty_struct`、`seq_operations` 這些「含函式指標、user 可 spray」的物件，近年有把部分移到專用 cache、或加 `GFP_KERNEL_ACCOUNT` 分流的趨勢，削弱它們和一般 bug 同 cache 的機會。
- random kmalloc caches（[Ch 27](./27-random-kmalloc-caches.md)）把同 size 拆成多個 cache，讓「我的牌和 victim 同 cache」變成機率問題。
- 於是攻擊者持續找**新牌**（新的、還沒被 hardening 的可 spray 物件）——讀新的 kernelCTF writeup，很大一部分價值就是「他們這次用了什麼新 spray 物件」。

所以別把這張表當聖經背死，要理解**挑牌的三維度（size/帳戶/可控度）**——這套判準不變，牌會換。遇到新 kernel 版本，拿三維度去評估「這個物件現在還能不能用、落在哪」，比記死一張表耐用得多。

## 動手練習

在自有 QEMU 靶上（接 [Ch 18](./18-uaf-double-free.md) 的 UAF 靶）：

1. 用 `pahole`/gdb 查出你 kernel 上 `tty_struct`、`seq_operations`、`msg_msg` header 的實際 size 與關鍵欄位 offset，填進你自己的牌庫筆記。
2. 對 kmalloc-32 的 UAF，用 `seq_operations` 當 victim 做一次控流（蓋 `start` 指標）。
3. 對中等 cache 的 UAF，用 `msg_msg` 當可控載體 + leak 來源各做一次（讀出一個 kernel 指標）。
4. 故意把 spray 物件選成和 victim 不同帳戶類別（一個 ACCOUNT 一個不是），驗證怎麼 spray 都佔不到——親身確認帳戶分流。

## 本章重點整理

- spray 物件分三角色：可控載體、leak 來源、值錢 victim；很多物件身兼多角。
- 挑牌看三維度：size/cache、帳戶類別（cg 分流）、可控度。
- 常用牌：msg_msg（萬用、可控/leak/任意讀寫）、tty_struct（大 cache，leak+控流）、seq_operations（kmalloc-32，純控流）、pipe_buffer（控流/data-only）、user_key_payload + setxattr（可控 bytes）。
- 鐵律：size 同 cache、帳戶類別一致，否則佔不到。
- 牌庫會隨 hardening 變；記判準（三維度）比記死表耐用。

## 自我檢核

- [ ] 給一個 cache size，能說出至少一張適合的 spray 牌，並說明它的角色
- [ ] 能解釋為什麼 `seq_operations` 只能當 victim 不能當可控載體
- [ ] 能說出 msg_msg 為什麼最萬用，以及送訊息時為什麼要扣 header 長度
- [ ] 能解釋「帳戶類別不一致就佔不到」的原因（連回 Ch 9 的 cg 分流）
- [ ] 能說出挑牌的三維度，並用它評估一個沒列在表上的新物件

## 延伸閱讀

- **[Reviewing the kernel heap spray objects（各 CTF-Wiki / writeup 彙整）](https://ctf-wiki.org/pwn/linux/kernel/)**
  - **這篇說什麼**：中文圈對常用 spray 物件（msg_msg、tty_struct、seq_operations…）的欄位與用法彙整。
  - **讀哪裡**：kernel 章的「常用結構體」節；當本章表格的擴充查閱。
  - **為什麼值得讀**：補上本章沒展開的欄位細節與更多物件。
- **[msg_msg 任意讀寫技術（Alexander Popov / 各 writeup）](https://a13xp0p0v.github.io/)**
  - **這篇說什麼**：示範怎麼透過改 `msg_msg` 的 `m_ts`/`next` 做越界讀與任意讀。
  - **讀哪裡**：msg_msg 相關段落；對照本章「msg_msg 當任意讀寫」。
  - **為什麼值得讀**：把最萬用的那張牌的進階玩法講透。
- **[struct tty_struct / seq_operations 定義（include/linux/tty.h、include/linux/seq_file.h）](https://elixir.bootlin.com/linux/v6.6/source/include/linux/tty.h)**
  - **讀哪裡**：兩個結構的定義，數清楚 `ops`/函式指標的 offset。
  - **和本章的關聯**：本章給概念，實際 offset 要從 source 對你的版本確認。
  - **前提知識**：會讀 C struct、Ch 8 的 cache 概念。

你現在手上有牌，也知道 free→spray→use 的節奏。但「spray 一批就剛好有一個落到洞裡」聽起來太美好——實際上 slab 有碎片、有其他雜訊 allocation、你還不知道中的是哪一個。下一章專講怎麼把這件事做穩：heap grooming。

→ [Ch 20 — Heap grooming：精確控制 slab 布局、defrag、穩定 spray](./20-heap-grooming.md)
