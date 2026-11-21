# Ch 30 — netfilter / nf_tables：kernelCTF 最大礦區

> **目標**：搞清楚 nf_tables 的物件模型（table/chain/rule/expression/set/element）與它獨有的「兩階段 transaction」設計，解釋為什麼這個子系統多年來一直是 kernelCTF 提交量最大的攻擊面；能拆解三個公開 CVE 的 root cause（CVE-2022-32250、CVE-2023-32233、CVE-2024-1086），並說出「拿到 UAF/double-free 之後概念上怎麼升級」而不需要抄一份完整 exploit。

> **環境**：Linux 6.6 LTS、x86-64 基線。nf_tables 的操作介面（`NFT_MSG_*` netlink 訊息）跨版本相對穩定，但本章引用的函式名稱、struct 欄位以 6.x 系列為準；CVE 的 root cause 描述已用 WebSearch 查證（來源見延伸閱讀），commit hash 查不到的地方會明說。

## 從 iptables 到 nf_tables：為什麼要換一套框架

在 nf_tables 之前，Linux 防火牆靠 `iptables`/`ip6tables`/`arptables`/`ebtables` 四套獨立工具,每套各自維護一份規則格式、各自的 kernel module（`ip_tables.ko`、`ip6_tables.ko`……）。這帶來兩個長期痛點：**規則格式不能共用**（IPv4 和 IPv6 規則要分別寫兩份幾乎一樣的邏輯）、**擴充要修改 kernel ABI**（新增一種 match 或 target，往往要新增一個 kernel module 並讓使用者態工具認得它，`xt_*` 系列 module 數量因此暴增，每個都是獨立的攻擊面）。

nf_tables 的設計目標是**用一套通用的、資料驅動的規則語言**取代這些各自為政的實作：規則不再是「寫死在 kernel 裡的一串 match/target 模組」，而是一棵可以被 netlink 描述、在使用者態用 `nft` 組裝、送進 kernel 執行的**物件圖**。好處是擴充性大增（新增一種 expression 類型不必新增整個 kernel module）、格式統一（`inet` family 一份規則同時管 IPv4/IPv6）。但這個好處的代價，正是本章要講的：**物件圖越通用、越可程式化，生命週期管理就越複雜**——`iptables` 的規則本質上是靜態陣列,沒有 nf_tables 這種「netlink 操作可以動態建立、綁定、解綁一整棵物件圖」的彈性,相應的攻擊面也小得多。這是一個典型的「表達力 vs 攻擊面」取捨：nf_tables 換來的靈活性,正是它成為礦區的根源。

## 為什麼 nf_tables 是最大礦區？

先把帳算清楚。nf_tables 是 `iptables` 之後 Linux 防火牆框架的現代替代品（約 2014 年起併入主線），目的是讓使用者用一套通用、可擴充的規則語言描述封包過濾、NAT、連線追蹤動作，取代 `iptables`/`ip6tables`/`arptables`/`ebtables` 四套各自為政的實作。它用 netlink（`NFNETLINK_SUBSYS_NFTABLES`）承接使用者態的 `nft` 指令，把規則**編譯成一棵物件圖**存在 kernel 裡，每個封包經過 netfilter hook 時走這棵圖判斷放行或丟棄。

這個設計帶來礦區的三個結構性原因：

1. **物件圖本身就複雜**。table、chain、rule、expression、set、set element 彼此用指標與 refcount 串起來，expression 還能遞迴嵌套（例如一個 `lookup` expression 指向一個 set，set 裡又能放 `verdict` 跳到另一個 chain）。物件越多、關係越複雜，生命週期管理出錯的機會越多——這正是 [Ch 10](./10-vulnerability-taxonomy.md) 說的 UAF / refcount bug 的溫床。
2. **transaction 的 commit/abort 是雙倍的程式碼路徑**。使用者一次 netlink batch 可以同時建立、修改、刪除一堆物件，kernel 先在記憶體裡「演練」整個 batch 的效果（staging），全部驗證通過才真正 commit，中途任何一步失敗就要 abort、把所有已做的 staging 動作精確復原。**abort path 的用例遠少於 commit path**，意味著它被測試覆蓋的程度天生就低，而偏偏它要處理「物件在什麼狀態時該釋放、什麼狀態不該」這種最容易出錯的邏輯。本章三個 CVE 有兩個直接出在這條路徑上。
3. **可達性被 user namespace 放大**。nf_tables 的操作要求 `CAP_NET_ADMIN`，但正如 [Ch 36](./36-user-namespace.md) 講的,一般使用者可以 `unshare(CLONE_NEWUSER|CLONE_NEWNET)` 免費拿到 namespace 內的 `CAP_NET_ADMIN`。這把 nf_tables 的整個攻擊面,從「只有 root 摸得到」變成「任何本地帳號都摸得到」——這也是為什麼查證當下的 kernelCTF Hardened 目標直接把 `USER_NS` 整個關掉（見 [Ch 49](./49-kernelctf-overview.md)),而不是只補 nf_tables 本身的 bug：關掉前置條件比追著補每一個 bug 更有效。

> 本章不重複 [Ch 36](./36-user-namespace.md) 已經用過的 CVE-2022-0185（fs_context 整數下溢）案例，那是 user namespace 放大攻擊面的經典示範；這裡聚焦 nf_tables **本身**的 bug。

把這三個結構性原因對照其他子系統，能看出 nf_tables 在「bug 密度」上的位置特別極端：[Ch 34](./34-filesystem-attack-surface.md) 的 fs parser 雖然也複雜，但 unprivileged user 連碰都碰不到（要先有 mount 權限）；[Ch 32](./32-ebpf.md) 的 eBPF verifier 雖然可達性也被 user namespace 放大，但它只有「一個」驗證器要攻破；nf_tables 則是**可達性高 + 物件圖複雜 + 程式碼路徑雙倍**三個因素同時疊加,而且從 2016 年併入主線以來，幾乎每年都有新的 CVE 被公開,這也是為什麼 kernelCTF 歷史提交裡 nf_tables 類別長期佔最大比例的原因（查證時能找到的量化統計請見延伸閱讀的 Google Security Blog 文章)。

### 一條不完整但有感的時間線

下表不是嚴謹統計（我們沒有可靠來源逐年計數），只是把本章查證到、與另外幾個常被引用的 nf_tables CVE 按年份排列，讓你有個「這個子系統持續在出事」的時間感：

| 年份 | CVE（若有） | 大致性質 |
|---|---|---|
| 2021 | CVE-2021-22555 | `xt_compat` 相關 heap OOB write，需要 `CAP_NET_ADMIN`，常透過 user namespace 取得（見 [Ch 36](./36-user-namespace.md) 案例三） |
| 2022 | CVE-2022-32250 | `nf_tables_bind_set` UAF（本章案例一） |
| 2023 | CVE-2023-32233 | abort path 的 UAF/double-free（本章案例二） |
| 2024 | CVE-2024-1086 | `nft_verdict_init()` 驗證缺失導致 double-free（本章案例三） |

四年四個不同層次的 bug——這組時間線本身就是本章論點「nf_tables 持續是礦區」的證據，不是單一歷史事件。

## 先建立直覺：規則是怎麼被「演練」出來的

把 nf_tables 想成一個「先畫藍圖，審核通過才動工」的工地：

```
使用者態 nft 指令
   │  netlink NFT_MSG_NEWTABLE / NEWCHAIN / NEWRULE / NEWSET / NEWSETELEM ...
   ▼
nf_tables_newxxx()  ──▶  在記憶體建立物件，串進 nft_trans_list（staging，還沒生效）
   │
   │  （一個 netlink batch 裡可以有很多條訊息，全部先 staging）
   ▼
batch 結束，使用者送 NFT_MSG_NEWGEN（或等價的 commit 訊號）
   │
   ▼
nf_tables_commit()  ──▶  走一遍 staging list，真正把物件接進生效中的規則圖
   │                     （rcu_assign_pointer 切換、舊物件丟進 call_rcu 延後釋放）
   │
   └─ 任何一步驗證失敗 ──▶ nf_tables_abort()
                            ──▶ 反向走 staging list，把已做的動作精確復原
                               （這條路徑的「物件該不該被釋放」邏輯最容易出錯）
```

**面 A（commit 是否正確切換 RCU 指標）**和**面 B（abort 是否正確復原、不多放一次 free 也不少放一次 unbind）**，是本章三個 CVE 分別踩到的兩層。

### 一個具體例子：一條 `nft` 指令背後的物件圖

光看抽象流程圖還不夠直覺，我們看一個具體例子。假設使用者（或者 `unshare` 之後的 unprivileged 使用者）依序執行：

```bash
nft add table inet filter
nft add chain inet filter input { type filter hook input priority 0 \; }
nft add set inet filter blocklist { type ipv4_addr \; }
nft add element inet filter blocklist { 10.0.0.1 }
nft add rule inet filter input ip saddr @blocklist drop
```

這五條指令在 kernel 裡建出的物件圖大致是：

```
nft_table "filter" (family=inet)
   └─ nft_chain "input" (base chain, hook=NF_INET_LOCAL_IN)
         └─ nft_rule #1
               ├─ nft_expr: payload   (讀 ip saddr 欄位)
               ├─ nft_expr: lookup    (查 set "blocklist") ──▶ nft_set "blocklist"
               │                                                 └─ nft_set_elem{10.0.0.1}
               └─ nft_expr: immediate (verdict = NF_DROP)
```

每一條指令都對應一次（或多次）`NFT_MSG_NEW*` netlink 訊息，而**最後一條 `add rule`** 才是真正讓 `lookup` expression 與 `blocklist` set 之間建立引用關係的那一步——這正是 CVE-2023-32233 踩到的場景：如果這條 `add rule` 在同一個 batch 裡因為其他原因失敗需要 abort，kernel 要精確回答「`blocklist` 這個 set 現在算不算已經被綁定」。如果 set 是用 `add rule ... { set { 10.0.0.1 } }` 這種**匿名 inline set** 語法建立（而不是像上例分開宣告），set 的生命週期會完全綁死在這條 rule 上，abort 的判斷邏輯也因此更複雜——這也是為什麼 CVE-2023-32233 的描述特別強調「匿名 set」場景。

## 物件模型與關鍵資料結構

| 概念 | struct / 介面 | 說明 |
|---|---|---|
| 表 | `struct nft_table` | 規則的最外層容器，對應一個 address family（`ip`/`ip6`/`inet`/`bridge`/`arp`/`netdev`） |
| 鏈 | `struct nft_chain` | 一組 rule 的序列，可以是 base chain（直接掛在 netfilter hook 上）或 regular chain（被其他 chain 的 verdict 跳轉） |
| 規則 | `struct nft_rule` | 一條規則 = 一串 expression 的序列，逐個執行，直到某個 expression 給出 verdict |
| 表達式 | `struct nft_expr` + `struct nft_expr_ops` | 規則的最小執行單元：`cmp`（比較）、`payload`（讀封包欄位）、`lookup`（查 set）、`dynset`（動態寫入 set）、`immediate`（直接給 verdict）…… ops 是一組函式指標，`eval()` 在封包經過時被呼叫 |
| 集合 | `struct nft_set` + `struct nft_set_ops` | key-value 集合，供 `lookup`/`dynset` 查詢；後端實作可以是 `rhash`（hash table）、`rbtree`、`bitmap`、`pipapo`（多維區間比對，給 IP 範圍/連接埠範圍用） |
| 集合元素 | `struct nft_set_elem` | 一個 key（可能還帶 data，例如跳到哪個 chain），插入/刪除都走 transaction |
| transaction 物件 | `struct nft_trans`（及 `nft_trans_rule`、`nft_trans_set`…等子型） | staging list 上的一筆動作記錄，commit/abort 依它的型別決定要做什麼 |
| 封包執行進入點 | `nft_do_chain()` | netfilter hook 呼叫這個函式，依序跑 chain 裡每條 rule 的每個 expression |

讀 source 的入口：`net/netfilter/nf_tables_api.c`（netlink 介面與 commit/abort）、`net/netfilter/nf_tables_core.c`（`nft_do_chain`）、`net/netfilter/nft_set_*.c`（各 set 後端）。物件之間的生命週期靠一套 refcount（`nft_use_inc`/`nft_use_dec` 之類的 helper，具體名稱隨版本略有調整）與 RCU 共同管理：**正在生效中的規則圖**是 RCU 讀端在跑，**transaction 的 commit/abort** 則是寫端，典型的 RCU reader/writer 搭配——而這套搭配的邊界條件正是 bug 的高發區。

### refcount 與 RCU 怎麼配合（直覺圖）

封包處理路徑（`nft_do_chain`）是高頻的 RCU 讀端：每個封包都要走一次，不能加鎖，否則效能災難。管理路徑（netlink 來的 `NEWRULE`/`DELRULE`……）是低頻的 RCU 寫端：

```
封包路徑（讀端，高頻，無鎖）              管理路徑（寫端，低頻，有 mutex）
────────────────────────             ──────────────────────────
rcu_read_lock()                      mutex_lock(&net->nft.commit_mutex)
  table = rcu_dereference(...)         修改 staging 物件
  chain = rcu_dereference(...)         rcu_assign_pointer() 切換「現在生效的是哪個版本」
  走 rule 清單，呼叫 expr->eval()        舊版本物件 ──▶ call_rcu() 排入延後釋放
rcu_read_unlock()                    mutex_unlock(...)
                                        （等所有讀端的 grace period 過了才真正 kfree）
```

這套設計的正確性依賴一個不變量：**任何時刻，一個正在被讀端走訪的物件，寫端都不能立即釋放它**，必須等 grace period。只要寫端某條路徑（尤其是 abort，因為它要「反著做」寫端的動作）在判斷「這個物件現在還有誰在用」時算錯，就會在 grace period 結束前把物件釋放，或是該釋放的卻沒釋放（refcount 洩漏，較溫和）、該保留引用的卻被提前切斷指標（UAF）。CVE-2023-32233 正是「寫端對自己是否已經切換過指標」判斷錯誤的例子。

### 為什麼 set 後端特別常出事

`nft_set` 的四種後端（`rhash`/`rbtree`/`bitmap`/`pipapo`）各自維護一套獨立的內部資料結構（hash table、紅黑樹、bitmap、多維區間樹），但它們都要共用同一套上層生命週期介面（`insert`/`remove`/`walk`/`destroy`）。這種「多套底層實作、一套共用生命週期協定」的設計,天生容易在**協定的邊界**出錯：上層以為呼叫 `destroy` 就能安全回收，但某個後端在內部還留著異步清理的 timer 或 gc worker（例如 `rhash` 後端的 rehash 機制），協定沒把這類「延遲清理」講清楚，就會有物件在協定認為已經死亡之後,底層卻還在摸它。`pipapo` 後端因為要處理多維區間（IP range × port range 的笛卡兒積比對）,內部結構最複雜,歷史上出的 bug 密度也相對高——這是「進階延伸」會指向的方向。

## 典型 bug 類型與成因：三個公開 CVE

### CVE-2022-32250：缺漏的 stateful 檢查 → `nf_tables_bind_set` 的 UAF

- **背景**：nf_tables 的 set 可以被多種 expression 綁定使用，其中 `lookup` 之類是「無狀態」的查詢，而 `dynset`、`connlimit` 之類帶有內部狀態，只允許用在被標記為 **stateful** 的 set 上。
- **root cause**：NVD 與 Red Hat 的描述指向 `net/netfilter/nf_tables_api.c` 裡，對「這個 expression 是否允許綁到這個 set」的檢查寫得不完整——incorrect `NFT_STATEFUL_EXPR` check。攻擊者可以構造一個 batch，讓一個本該被拒絕的 expression 綁定關係通過檢查，進而讓 `nf_tables_bind_set()` 在物件已經走到不該存在的生命週期階段時仍被操作，Red Hat bug report 附的 KASAN splat 顯示的是 `nf_tables_bind_set` 的 use-after-free write。
- **fix**：上游 commit（NCC Group 與多個 distro 引用的 hash 為 `520778042ccc...`，標題 "netfilter: nf_tables: disallow non-stateful expression in sets earlier"）把這個檢查提前，在更早的路徑就擋掉非法綁定，而不是等到已經部分執行才發現不合法。
- **利用脈絡**：NCC Group 的公開分析《Settlers of Netlink》描述整條鏈需要串起**四次 UAF 觸發**才能把一個「視窗很窄」的 bug 擴大成穩定的 KASLR bypass 與對 `modprobe_path` 的覆寫（[Ch 16](./16-classic-primitives.md) 的經典原語）。這說明一件事：nf_tables 的 UAF 往往不是「一次就能讀寫任意記憶體」，而是需要多輪 **觸發 → reclaim → 再觸發** 才能把原語升級到可用。

這個 bug 的模式，用偽碼還原 NVD 描述的檢查邏輯大致是：

```c
/* 偽碼，還原 NVD/Red Hat 描述的檢查邏輯，不是任何一個 kernel 版本的原文 */
static int nf_tables_bind_set(const struct nft_ctx *ctx, struct nft_set *set,
                               struct nft_set_binding *binding)
{
        if (!list_empty(&set->bindings) && nft_set_is_anonymous(set))
                return -EBUSY;

        /* 漏檢：這裡應該檢查 binding 來源的 expression 是不是 stateful，
         * 但原始程式碼對這個條件判斷不完整,讓某些 non-stateful 的 expression
         * （不該被允許綁到會被 dynset 修改的 set 上）也通過了這個檢查 */
        list_add_tail_rcu(&binding->list, &set->bindings);
        return 0;
}
```

之後當這個不該存在的綁定關係被使用,某個以為「這個 set 只會被無狀態查詢」的路徑,卻因為另一個有狀態的 expression 同時在操作同一塊記憶體,時序一錯就踩進 UAF。

### CVE-2023-32233：abort path 對已綁定 set 的二次釋放

- **背景**：一個 `rule` 裡可以嵌入一個匿名 set（例如 `lookup` 查一個 inline 定義的集合），這個 set 會被「綁」到建立它的那條 rule。如果同一個 batch 裡，建立這條 rule 失敗（需要 abort），kernel 必須知道：這個 set 是不是已經被某條 rule 綁定過，如果有，abort 時該不該連 set 一起釋放。
- **root cause**：Ubuntu kernel-team 的 patch 說明與 oss-security 的描述指出，abort path 原本對「這個 set 的 `bound` 欄位」的檢查邏輯有誤——一個已經在 commit path 走過 `bind` 的 set，在對應 rule 的建立被 abort 時，它的生命週期判斷用的是過期的狀態，導致同一塊記憶體被釋放兩次（double free），或是 abort 之後仍有指標指向已釋放的 set（use-after-free）。series 裡至少有兩個互相配合的修補：「unbind set in rule from commit path」（把 unbind 動作從 commit path 移到正確的地方）與「fix set double-free in abort path」，並且補上了 abort path 原本缺的 `NFT_MSG_NEWSETELEM` 處理（元素層級的 abort 之前沒被完整考慮）。
- **影響範圍**：NVD 描述這個 bug 可以造成任意 kernel 記憶體讀寫，受影響到 6.3.1。這是本章開頭說的「abort path 用例少、覆蓋低」最直接的體現——問題不是某一行程式碼寫錯，而是 commit/abort 兩條路徑對同一個物件的生命週期假設**互相不一致**。

### CVE-2024-1086：`nft_verdict_init()` 的 verdict 參數沒驗證 → double free

- **root cause**：`nft_verdict_init()` 負責把使用者在 `immediate` expression 裡給的 verdict 參數（`NF_ACCEPT`/`NF_DROP`/`NF_QUEUE`/跳轉到某個 chain）轉成內部表示。問題在於它**把正值當成合法的 drop error code 接受**。netfilter 的 hook 回傳值約定裡，`NF_DROP` 可以附帶一個「丟棄原因」的 errno，但這個 errno 應該是負值；`nft_verdict_init()` 沒有擋掉正值輸入。當這個帶著「看起來像 `NF_ACCEPT`」數值的 drop verdict 一路傳到 `nf_hook_slow()`，會讓這個函式對同一個 `skb` 走上兩條互斥的釋放路徑——一次因為「這是 drop」釋放，一次因為「這個值剛好等於 accept」又被當作還存活的封包繼續處理後再釋放，形成 double free。
- **fix**：Florian Westphal 的上游修補 `f342de4e2f33e0e39165d8639387aa6c19dff660`（標題 "netfilter: nf_tables: reject QUEUE/DROP verdict parameters"）直接在 `nft_verdict_init()` 拒絕這類不合法的 verdict 參數組合，從源頭堵住，而不是在 `nf_hook_slow()` 那端修補分支邏輯。

用偽碼還原這個檢查漏洞（還原自 fix commit 的 diff 描述，細節請以原始 diff 為準）：

```c
/* 偽碼，示意修補前的檢查缺失 */
static int nft_verdict_init(const struct nft_ctx *ctx, int *verdict,
                             struct nft_data_desc *desc, const struct nlattr *nla)
{
        int err = nft_parse_u32_check(nla, S32_MAX, verdict);
        /* ... */
        switch (*verdict) {
        case NF_ACCEPT:
        case NF_DROP:
        case NF_QUEUE:
                /* 漏檢：NF_DROP 允許附帶一個「丟棄原因」的負數 errno，
                 * 但這裡沒檢查使用者給的值是不是真的落在合法的負數範圍，
                 * 一個「剛好等於 NF_ACCEPT 數值」的輸入會被當成合法的 drop 原因接受 */
                break;
        }
        return 0;
}
```

修補後的版本明確拒絕 `NF_QUEUE`/`NF_DROP` 搭配不合法參數的組合，等於把「這個欄位的值域」重新收斂到只有真正合法的子集，而不是靠下游 `nf_hook_slow()` 去猜測一個數值「看起來像什麼」。
- **為什麼這個 CVE 特別值得記**：它**不是**記憶體管理那層的 UAF/refcount bug，而是一個**語意層的輸入驗證缺失**——對照 [Ch 10](./10-vulnerability-taxonomy.md) 的分類，這屬於「邏輯 bug 導致記憶體管理原語失效」，不是傳統的指標運算錯誤。而且它的影響範圍極廣（受影響到 v6.8-rc6/rc7，幾乎每個發行版都中），加上 Google kCTF 研究者 Notselwyn 公開發表了完整分析與利用，CISA 把它列進 Known Exploited Vulnerabilities Catalog——這是本章三個案例裡少數被官方認證「野外已有已知利用」的一個。

### 三案例對照

| CVE | bug 所在路徑 | 根本原因類型 | 對 Ch 10 分類 |
|---|---|---|---|
| CVE-2022-32250 | commit 前的綁定檢查 | 檢查寫得不完整（stateful 判斷漏洞） | UAF |
| CVE-2023-32233 | abort path | commit/abort 對物件生命週期假設不一致 | UAF + double-free |
| CVE-2024-1086 | verdict 參數驗證 | 語意層輸入驗證缺失，間接導致記憶體管理走錯分支 | double-free（邏輯誘發） |

三者的共同模式：**bug 都不在「封包資料本身」，而在「使用者對 nf_tables 物件圖的操作序列」**。這是 nf_tables 和 [Ch 34](./34-filesystem-attack-surface.md) 的 fs parser 最大的差異——fs 的 input 是一塊靜態 image，nf_tables 的 input 是一串**有時序、有狀態依賴**的 netlink 操作，bug 藏在「操作之間的交互」而不是單一欄位的邊界檢查。這也解釋了為什麼 Syzkaller 對 nf_tables 特別有效：它天生擅長生成 syscall **序列**（[Ch 43](./43-syzkaller.md)），而不只是單一輸入的變異。

### 第四個常見模式：netlink 屬性解析本身

除了前面三個環繞「物件生命週期」的模式，nf_tables 還有一類值得單獨點名：**netlink attribute（`struct nlattr`）的巢狀解析**。一條 `nft` 規則裡的 expression 參數、set 的 key/data 定義，都是用巢狀的 `NLA_NESTED` 屬性表示——類似 JSON 的巢狀物件，但用二進位 TLV（type-length-value）編碼。每一層巢狀都要呼叫 `nla_parse_nested()` 之類的 helper 往下拆，而**巢狀深度、每層的 type 是否符合預期、length 是否落在合理範圍**都是攻擊者可控的輸入。這一類 bug 的根因更接近 [Ch 34](./34-filesystem-attack-surface.md) 講的「長度欄位未檢查 → OOB」模式，只是載體換成了 netlink 而不是磁碟 image——值得在讀 `nft_set_elem_expr_alloc()`、`nft_expr_type_get()` 這類「把一段 netlink attribute 轉成內部物件」的函式時，特別留意邊界檢查是否完整。

## 利用思路（概念，不寫 turnkey）

拿到一個 nf_tables 的 UAF 之後，概念上的升級路徑：

1. **原語定性**：先搞清楚這是「free 之後還能讀」還是「free 之後還能寫」，以及被釋放的物件是哪個 struct、落在哪個 kmalloc cache（[Ch 8](./08-slub-allocator.md)）。`nft_set`、`nft_rule`、`nft_trans_*` 這些物件通常不大，落在中等大小的 kmalloc-N cache，和很多常見 spray 物件共享 cache。
2. **reclaim 與 spray**：用 [Ch 19](./19-spray-objects.md) 的「牌庫」去佔用被釋放的那塊記憶體——如果 victim 是一個帶函式指標的物件（例如某些 `nft_expr_ops` 相關的間接呼叫路徑），可以考慮 `seq_operations`、`tty_struct` 這類控流用牌；如果只是要先穩定地讀出內容做 KASLR leak，`msg_msg` 這類可控載體更合適。NCC Group 對 CVE-2022-32250 的分析正是這個模式：先用一輪 UAF 讀出 kernel 指標做 leak，再用後續的 UAF 寫入做控流。
3. **選擇升級方向**：[Ch 28](./28-cfi-kcfi.md) 講過，CFI/KCFI 普及之後，直接劫持函式指標去跳 ROP chain 的路越走越窄。更穩的方向是 [Ch 29](./29-data-only-attack.md) 的 data-only：不碰函式指標，改寫 `cred`、`modprobe_path`（[Ch 16](./16-classic-primitives.md)）之類的資料欄位。CVE-2024-1086 的公開分析（Notselwyn）走的也是這個路線。
4. **穩定化**：nf_tables 的 transaction 語意意味著你的 PoC 很容易牽涉多筆 netlink 訊息的精確排列順序；[Ch 45](./45-exploit-stabilization.md) 的 grooming 與 retry 技巧在這裡特別重要——commit/abort 的 race window 往往比單純的 syscall race 更難重現。

這四步合起來，和 [Ch 21](./21-heap-to-rip.md)、[Ch 29](./29-data-only-attack.md) 講的「heap bug → 原語升級」骨架完全一致，nf_tables 只是提供了**觸發 bug 的那一步**，升級與收尾的工具箱是共用的。這也是為什麼本書把「子系統攻擊面」（Part 5）和「heap 利用核心」（Part 3）分開講：子系統決定你拿到什麼形狀的原語，Part 3 的技術決定你怎麼把它變成 root。

## 防禦面

和 [Ch 34](./34-filesystem-attack-surface.md) 的結論一致：**修 parser／邏輯 bug 是打地鼠，限制可達性才是結構性防禦**。對 nf_tables 而言，分層如下：

| 防禦層 | 具體做法 | 擋得住什麼 | 擋不住什麼 |
|---|---|---|---|
| 禁止 unprivileged user namespace | `sysctl kernel.unprivileged_userns_clone=0`（Debian/Ubuntu 系列的自訂 sysctl）或 `user.max_user_namespaces=0` | 一般使用者完全無法 `unshare(CLONE_NEWUSER)`，nf_tables 整條入口被堵死 | 已經是 root、或系統本來就允許 unprivileged userns 給容器用（Docker/Podman 常態需求，犧牲這層等於犧牲容器化能力） |
| LSM 層收斂 | SELinux/AppArmor 規則限制特定 process 的 `CAP_NET_ADMIN` 使用範圍；seccomp 過濾 `socket(AF_NETLINK, ..., NETLINK_NETFILTER)` | 把 nf_tables 可達性收斂到白名單內的 process | 設定錨點多、容易漏；對「系統本來就要讓一般使用者跑容器」的場景不適用 |
| 直接不編譯/不載入 `nf_tables` module | `CONFIG_NF_TABLES=n` 或 `install nf_tables /bin/false` | 完全消除攻擊面 | 需要 nftables 防火牆功能的系統不能用這招；多數發行版的基礎防火牆工具已經轉向 nftables，不編譯的代價很高 |
| 攻擊面縮減（kernelCTF Hardened 的選擇） | 整個 target 關閉 `USER_NS` | 同時擋掉 nf_tables、tc、xfrm 等一整類「靠 user namespace 放大」的攻擊面 | 犧牲合法的 unprivileged 容器化；是「評測環境」的選擇，不代表一般生產系統都該這麼做 |
| KASAN / KFENCE 偵測 | 開發與 fuzz 階段用 KASAN，生產環境可選擇性開 KFENCE 採樣 | 把 silent UAF/OOB 變成立即可見的 crash，加速修補 | 偵測用，不是防護；KFENCE 採樣率低，生產環境漏網機率高 |

我們的立場：**生產系統的現實解法通常是前兩層的某種組合**——如果系統本來就不需要讓一般使用者跑容器（例如單租戶的伺服器），關掉 unprivileged user namespace 是性價比最高的一步；如果需要容器化（多租戶、CI runner），就要接受 nf_tables 這類攻擊面存在，靠及時跟進 CVE 修補、LSM 收斂、與 [Ch 51](./51-defense-detection.md) 的偵測機制做縱深防禦。

### 概念骨架：從一個 UAF 到提權的完整流程圖

把前面四步串成一張圖，幫助你在讀任何一篇 nf_tables writeup 時都能對號入座（這是骨架，不是任何特定 exploit 的完整步驟）：

```
①觸發 UAF/double-free（本章三個 CVE 任一個）
      │
      ▼
②定性原語：free-then-read？free-then-write？victim 物件多大、哪個 cache？
      │
      ▼
③第一輪 spray：用 Ch19 牌庫佔住被釋放的記憶體
      │         ├─ 需要 leak    ──▶ 選帶 kernel 指標的牌（如 msg_msg 內嵌指標）
      │         └─ 需要控流/寫  ──▶ 選帶函式指標或關鍵欄位的牌（如 pipe_buffer）
      ▼
④用①的原語再觸發一次，讀出③牌裡的內容（leak）或覆寫③牌的內容（corrupt）
      │
      ▼
⑤選升級路線
      │  ├─ data-only：定位 cred / modprobe_path 之類資料目標，直接改內容（Ch 16, 29）
      │  └─ 控流（CFI 關閉的環境才可行）：改函式指標，接 ROP（Ch 13, 28）
      ▼
⑥觸發目標行為（如 execve 一個被劫持的 helper、呼叫被改過的 cred）拿到 root
      │
      ▼
⑦清理與穩定化：避免 panic、讓 PoC 可重複跑（Ch 45）
```

每一步在不同 CVE 上的「細節填空」不同——這正是我們刻意不給 turnkey 完整程式碼的原因：骨架通用，填空需要你自己對著目標 kernel 版本、目標 bug 的實際行為去驗證。

概念骨架對應到 C 的程式結構大致是這樣（同樣是骨架，省略所有觸發特定 CVE 所需的精確 netlink payload 構造）：

```c
/* 概念骨架，不是任何特定 CVE 的完整 exploit */
int main(void) {
        /* 1. 進入放大過的攻擊面：unshare 取得 ns 內 CAP_NET_ADMIN（見 Ch 36） */
        unshare(CLONE_NEWUSER | CLONE_NEWNET);

        /* 2. 建立觸發 bug 所需的 nf_tables 物件圖
         *    （用 libmnl / libnftnl 或手刻 netlink 訊息組出 table/chain/set/rule） */
        int nfnl_sock = setup_nftables_objects();

        /* 3. 觸發 UAF / double-free（本章任一 CVE 的特定序列） */
        trigger_vulnerability(nfnl_sock);

        /* 4. 第一輪 spray：佔住被釋放的記憶體，用 Ch19 的 msg_msg 或 pipe_buffer */
        int spray_fds[NSPRAY];
        spray_reclaim_objects(spray_fds, NSPRAY);

        /* 5. 利用①的原語再觸發一次，從③的牌裡讀出 kernel 指標（KASLR leak） */
        unsigned long kbase = leak_via_reclaimed_object(spray_fds);

        /* 6. 定位並覆寫 data-only 目標（cred / modprobe_path 等，位址依 kbase 計算） */
        corrupt_target_via_reclaimed_object(spray_fds, kbase);

        /* 7. 觸發目標行為，驗證是否已拿到 root */
        if (getuid() == 0)
                system("/bin/sh");

        /* 8. 清理：確保 fd、namespace 狀態不會讓 kernel panic（Ch 45） */
        cleanup(spray_fds, NSPRAY);
        return 0;
}
```

每個函式名稱都是佔位——`setup_nftables_objects()`、`trigger_vulnerability()`、`leak_via_reclaimed_object()` 這些函式裡要填的 netlink payload 構造、spray 的物件選擇、位址計算公式，都要對著你選定的 CVE 與目標 kernel 版本實測填入，不存在「複製貼上就能跑」的通用版本。

## 對比取捨表

| 利用方向 | 優點 | 缺點 | 現狀 |
|---|---|---|---|
| 直接函式指標劫持（走 `nft_expr_ops`/`nft_set_ops`） | 控流直觀，一步到 RIP | CFI/KCFI 下大多被擋 | 現代 kernel 上越來越難 |
| data-only 改 `cred`/`modprobe_path` | 繞過 CFI，穩定性較高 | 需要先找到可控的任意寫原語，前置工程量大 | 主流方向（Ch 29） |
| 多輪 UAF 升級（leak→再觸發→寫） | 不依賴特定 victim 物件存在 | 步驟多，exploit 複雜度高，race window 窄 | nf_tables 類 bug 常見形態 |
| 跳過 nf_tables 本身，先堵 user namespace | 一次性堵掉整類入口 | 犧牲合法 unprivileged 容器化需求 | Hardened target 的選擇（Ch 49） |

## 踩雷集錦

1. **「nf_tables 要 CAP_NET_ADMIN，一般使用者摸不到」**：錯。在允許 unprivileged user namespace 的發行版預設設定下，`unshare(CLONE_NEWUSER|CLONE_NEWNET)` 就能免費拿到 namespace 內的 `CAP_NET_ADMIN`。評估一個 nf_tables CVE 的實際危害前，先確認目標環境是否限制了 `kernel.unprivileged_userns_clone`。
2. **「commit 和 abort 是同一套邏輯，差別只是方向相反」**：錯。commit 是把 staging 物件**接進生效圖**，abort 是把它們**精確復原到操作前的狀態**——這兩者不是簡單的互逆操作，尤其涉及「這個物件是不是已經被另一筆 transaction 綁定過」這種跨筆記錄的狀態，正是 CVE-2023-32233 的根源。
3. **「只要 bug 在 UAF 類別，利用方式都差不多」**：錯。CVE-2022-32250 是「寫得不完整的前置檢查」、CVE-2023-32233 是「commit/abort 狀態不一致」、CVE-2024-1086 是「語意層驗證缺失」——同樣是記憶體安全問題，根本原因層次完全不同，讀 patch 時要分清楚「哪一層的假設被打破了」，不能只貼標籤「UAF」就結案。
4. **「一個 CVE 修完，這個 class 的 bug 就沒了」**：錯。nf_tables 從 2016 年至今反覆出現 commit/abort 路徑的生命週期 bug，修一個漏洞點不代表 abort path 上其他尚未被測到的交互組合是安全的——這正是這個子系統持續被列為最大礦區的原因。
5. **「只要沒開 nf_tables 相關功能就安全」**：不準確。只要 kernel 編入 `nf_tables` module（多數發行版預設編入，即使使用者沒手動用過 `nft`），`unshare` 進新的 net namespace 後依然可以建立全新的 table/chain 從零開始操作，不需要系統原本就有任何 nftables 規則在跑。
6. **「nf_tables 的 CVE 都需要複雜的多步觸發」**：不一定。CVE-2024-1086 的觸發條件相對單純——送一條帶有不合法 verdict 參數的規則就能命中,複雜的是後續「怎麼把 double free 變成穩定提權」的工程,不是觸發本身。別把「觸發難度」和「利用難度」混為一談。
7. **「bitmap/rbtree 這種簡單的 set 後端比 pipapo 安全」**：不必然。簡單的後端出事機率可能較低,但不是零——判斷風險要看這個後端的程式碼最近是否活躍修改（`git log` 該檔案的頻率），不是看資料結構表面上「簡單」與否。

## 進階：再往深一層

- **讀一整個 kernelCTF submission**：[kernelCTF submission repo](https://github.com/google/security-research/tree/master/pocs/linux/kernelctf) 裡搜尋 `nf_tables`，挑一篇完整 writeup，對照本章的三個 CVE root cause，看作者實際怎麼從 bug 走到穩定 exploit——這是本章刻意沒寫 turnkey 細節的地方。
- **nft_set 的多種後端各有各的 bug 史**：`pipapo`（多維區間比對後端，用在 IP/port range 這類查詢）結構特別複雜，是另一個常出 bug 的角落，值得單獨深入讀 `nft_pipapo.c`。
- **和 [Ch 40](./40-race-conditions.md) 的連結**：nf_tables 的 commit/abort 本身有鎖保護（`nft_net->commit_mutex` 之類），但物件跨 transaction 的狀態機轉換仍可能有邊界競態，值得在學完並發模型後回來重新讀一遍 CVE-2023-32233 的修補。
- **從防禦角度量化風險**：如果你要寫一份 hardening 評估報告，別只寫「nf_tables 有歷史漏洞」，試著統計近三年 `net/netfilter/nf_tables_api.c` 的 CVE 修補 commit 數量與分布（用 `git log --grep=CVE -- net/netfilter/`），量化「這條程式碼的歷史 bug 密度」。
- **netlink 屬性解析的通用防禦模式**：讀 `lib/nlattr.c` 的 `nla_parse()`/`nla_validate()` 系列函式，理解 kernel 近年怎麼用 `struct nla_policy` 把「這個屬性應該長什麼樣」宣告成資料而不是寫成散落各處的手寫檢查——這是一種把驗證邏輯集中化、降低遺漏機率的設計，值得和 [Ch 34](./34-filesystem-attack-surface.md) 的 fs sanity_check 函式比較異同。
- **和 eBPF 的對照**：[Ch 32](./32-ebpf.md) 的 eBPF verifier 是「用一個集中的驗證器去擋所有可能的惡意程式」，nf_tables 沒有類似的集中驗證層——它的驗證邏輯分散在每個 netlink handler 裡。思考一下：如果 nf_tables 也有一個類似 verifier 的集中驗證層，CVE-2024-1086 這類「單一欄位沒驗證」的 bug 會不會更容易被系統性擋掉？這也是部分 kernel 開發者主張「用更強的型別系統與集中驗證取代手寫檢查」的論點來源。
- **追蹤最新狀態**：nf_tables 的 bug 修補持續在進行,訂閱 [netfilter-devel mailing list](https://lists.netfilter.org/mailman3/postorang/netfilter-devel/) 或定期看 kernel.org 的 `net/netfilter/` 目錄 commit log,是追蹤這個子系統最新動態最直接的方式。

## 動手練習

> 以下操作在**你自己擁有的 Linux VM** 內進行，涉及 kernel 原始碼閱讀的部分**未實測**，為理論預期行為；驗證方式見各步驟說明。

1. **讀三個 fix commit 的完整 diff**：分別找 CVE-2022-32250（搜尋 `520778042ccc` 或關鍵字 "disallow non-stateful expression in sets earlier"）、CVE-2023-32233（搜尋 "unbind set in rule from commit path"）、CVE-2024-1086（`f342de4e2f33e0e39165d8639387aa6c19dff660`）的 commit，逐行比對修補前後，確認本章的 root cause 描述與原始 diff 是否一致。
2. **建立一個最小的 nf_tables 規則並觀察 netlink 訊息**：在 VM 裡裝 `nftables`，用 `nft -a list ruleset` 的同時跑 `strace -e trace=network nft add table inet test`，觀察發出的是什麼 netlink 訊息型別，對照本章「規則怎麼被演練出來」那張圖裡的 `NFT_MSG_NEW*`。
3. **用 `unshare` 驗證可達性**：`unshare -Un` 進入新的 user+net namespace 後跑 `nft list tables`，確認在沒有 root 的情況下依然能操作 nf_tables（若系統設了 `kernel.unprivileged_userns_clone=0`，這步會失敗，記錄下這個差異）。
4. **讀 `nf_tables_commit()` 與 `nf_tables_abort()` 的原始碼**，列出兩者分別處理了哪幾種 `nft_trans` 子型別，標出哪些子型別只在其中一邊有特殊處理——這類不對稱往往是候選 bug 點。
5. **驗證防禦面的 sysctl**：在 VM 裡試著設定 `sysctl kernel.unprivileged_userns_clone=0`（若你的發行版支援這個 sysctl；主線 kernel 本身沒有這個 knob，是 Debian/Ubuntu 的 patch，查一下你的發行版是否帶這個 patch），重開一個非特權 shell 後再跑 `unshare -Un`，記錄行為差異，並思考這對日常使用容器工具（如 `podman`，它常依賴 unprivileged userns）的影響。
6. **讀一份 Notselwyn 對 CVE-2024-1086 的公開分析**（搜尋關鍵字 "Notselwyn nft_verdict_init"），記下他怎麼把「double free」原語升級成穩定的提權——對照本章「利用思路」四步，標出他的每一步分別對應哪一步。

## 本章重點整理

- nf_tables 的礦區本質：從 iptables 換成通用、可程式化的物件圖換來的表達力，代價是複雜物件圖 + 雙倍程式碼路徑（commit/abort）+ 被 user namespace 放大的可達性。
- 三個代表 CVE 分屬三種根本原因層次：前置檢查不完整（CVE-2022-32250）、commit/abort 狀態不一致（CVE-2023-32233）、語意層輸入驗證缺失（CVE-2024-1086）——同樣標 UAF/double-free，根因不同。
- 利用升級走「reclaim + spray」的老路（[Ch 19](./19-spray-objects.md)），現代趨勢是繞開 CFI 走 data-only（[Ch 29](./29-data-only-attack.md)）。
- 防禦的結構性解法是堵前置條件（限制 unprivileged user namespace），不是無止盡地補 abort path 的下一個 corner case；kernelCTF Hardened target 直接整個關掉 `USER_NS` 正是這個邏輯的實踐。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼 abort path 比 commit path 更容易出 bug」。
- [ ] 能畫出 nf_tables 規則從 netlink 訊息到生效的完整流程，標出 staging/commit/abort 三個階段。
- [ ] 能分別用一句話講出三個 CVE 的根本原因，並說出它們分屬 Ch 10 分類的哪一類。
- [ ] 面試官問「為什麼 nf_tables 是 kernelCTF 最大礦區」，你能從物件複雜度、程式碼路徑、可達性三個角度回答。
- [ ] 能解釋為什麼 Hardened target 選擇直接關閉 `USER_NS` 而不是只補 nf_tables 的 bug。

## 延伸閱讀

### 漏洞分析（CVE 第一手或接近第一手來源）

- **[SETTLERS OF NETLINK: Exploiting a limited UAF in nf_tables (CVE-2022-32250)](https://research.nccgroup.com/2022/09/01/settlers-of-netlink-exploiting-a-limited-uaf-in-nf_tables-cve-2022-32250)** — NCC Group
  - **讀哪裡**：全篇，重點在「如何把一個視窗很窄的 UAF 擴大成四次觸發的穩定鏈」段落。
  - **和本章的關聯**：本章「利用思路」一節的「多輪 UAF 升級」直接取自這篇的手法。
  - **前提**：需要先懂 [Ch 18](./18-uaf-double-free.md) 的 UAF 基礎與 [Ch 19](./19-spray-objects.md) 的 spray 物件。
- **[Red Hat Bugzilla 2092427 — CVE-2022-32250](https://bugzilla.redhat.com/show_bug.cgi?id=2092427)**
  - **讀哪裡**：附帶的 KASAN splat 與 "Fixes" 欄位引用的 commit。
  - **學什麼**：怎麼從一份 bug report 反推出「哪個檢查漏了」。
- **[Ubuntu kernel-team mailing list — CVE-2023-32233 patch series](https://lists.snapcraft.io/archives/kernel-team/2023-May/139517.html)**
  - **讀哪裡**："unbind set in rule from commit path" 與 "fix set double-free in abort path" 兩篇 patch 說明。
  - **為什麼值得讀**：這是少數能看到「abort path 的狀態不一致」怎麼被官方描述與修補的完整過程，比單看 fix commit 的 diff 更容易理解動機。

### 原始碼

- **[`net/netfilter/nf_tables_api.c`（Bootlin Elixir, Linux 6.6）](https://elixir.bootlin.com/linux/v6.6/source/net/netfilter/nf_tables_api.c)**
  - **讀哪裡**：`nf_tables_commit()`、`nf_tables_abort()`、`nf_tables_bind_set()`。
  - **學什麼**：親眼看 `nft_trans` 各子型別在 commit/abort 兩邊分別被怎麼處理，練習找出不對稱之處。

- **[CVE-2024-1086 upstream fix commit](https://github.com/torvalds/linux/commit/f342de4e2f33e0e39165d8639387aa6c19dff660)**
  - **讀哪裡**：整個 diff，只有 `net/netfilter/nf_tables_api.c` 的 6 行新增、10 行刪除，適合當「小改動、大影響」的案例精讀。
  - **學什麼**：一個語意層的輸入驗證缺失（不是指標運算錯誤）如何導致記憶體管理原語失效；對照本章「為什麼這個 CVE 特別值得記」一節。

### 官方規則與背景

- **[kernelCTF rules](https://google.github.io/security-research/kernelctf/rules)**
  - **讀哪裡**：目標環境與加固設定說明。
  - **和本章的關聯**：解釋為什麼查證當下的 Hardened target 選擇關閉 `USER_NS`，見 [Ch 49](./49-kernelctf-overview.md) 的詳細對照。
- **[Learnings from kCTF VRP's 42 Linux kernel exploits submissions](https://security.googleblog.com/2023/06/learnings-from-kctf-vrps-42-linux.html)** — Google Security Blog（2023）
  - **讀哪裡**：全篇，重點在「哪些子系統最常被用作入口」的統計與分析段落。
  - **為什麼值得讀**：這是官方第一手視角，直接回答「為什麼 netfilter/io_uring 會成為最大礦區」這個問題，是本章與 [Ch 31](./31-io-uring.md) 開頭論點的來源之一。
  - **前提**：不需要特別背景，適合在讀完本章後對照驗證本章宣稱是否與官方統計一致。

nf_tables 靠「物件圖複雜 + 需要前置權限」成為礦區；下一章的 io_uring 走的是另一條路——它不需要任何 capability，任何 unprivileged process 都能直接用，靠的是「async 執行模型本身就容易出 race」。

→ [Ch 31 io_uring：SQE/CQE 與 async ring 的 UAF 模式](./31-io-uring.md)
