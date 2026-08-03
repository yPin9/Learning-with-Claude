# Ch 21 — 萃取 pattern：content addressing / DAG

> **目標**：把 Ch 18–20 讀到的 git 內部結晶成五張可遷移的 pattern 卡片——content addressing、immutable DAG、loose+packed 雙層儲存、command dispatch table、delta 壓縮。每張卡片給你「在哪認出它、它的 beacon、遷移到哪」，讓你下次在別的 codebase 一眼 chunk 出它。

> **目標codebase**：git v2.47.1（commit `92999a4`）（本章是收斂章，主要回引前三章，不新讀 code）

## 為什麼需要這個？

`reading_code` 教你怎麼攻堅一個陌生 codebase；這門課多練一件事——**讀完之後，把設計 idiom 結晶成 pattern，存進你的「一眼認出」字典**（Ch 1 的 chunking 科學）。讀懂 git 的三章如果只留下「喔我知道 git 怎麼運作了」，那你下次讀 IPFS、讀 Docker 的 image layer、讀某個備份系統時，還是得從頭推。但如果你把「content addressing」抽象成一張卡片，下次看到「用內容雜湊當 key」的 code，你會**瞬間認出**，直接跳到「它一定有去重和防竄改」的結論。

pattern 卡片的價值在**遷移**。git 的這五個 pattern 沒有一個是 git 獨有的——它們是分散式系統、儲存引擎、直譯器的通用 idiom。這一章我們把它們一張一張抽出來，並明確指出「你在本課其他 Part 會在哪再遇到它」。

## Pattern 卡片的格式

每張卡片四欄：

- **一句話**：這個 pattern 是什麼。
- **在 git 哪裡**：真實檔案/函式，讓你回去核對。
- **beacon（怎麼一眼認出）**：在陌生 code 裡看到什麼形狀就該想到它。
- **遷移到哪**：本課其他 codebase / 你職涯會再遇到的地方。

---

## 卡片 1：Content Addressing（內容定址）

**一句話**：一個物件的「名字」= 它內容的雜湊。內容決定身分，而不是位置或流水號決定身分。

**在 git 哪裡**：
- `object.h` 的 `struct object` 裡的 `struct object_id oid`——每個 object 的身分。
- `object-file.c:1941` 的 `hash_object_body`：`hash(header || 內容)` 就是身分的計算。
- Ch 18 手算驗證過：`printf 'blob 10\0hello git\n' | sha1sum` = `git hash-object` 的輸出，一位不差。

**beacon（怎麼一眼認出）**：
- 看到「用某段資料的 SHA/MD5/hash 當 key 或檔名」。
- 看到「寫入前先算 hash，發現已存在就跳過」（去重的守門，git 的 `freshen_*`）。
- 看到「檔案路徑是一串 hex，且和內容無關的檔名不見了」（`.git/objects/8d/0e41...`）。

**這個 pattern 天生附帶三個性質**（認出它就知道系統有這些）：
1. **去重**：同內容 → 同 key → 只存一份。
2. **完整性/防竄改**：內容改一個 bit → key 全變 → 天生可驗證（重算 hash 比對即可）。
3. **可分散**：兩個節點各自離線產生同內容，key 一致，不需中央發號。git 的分散式協作全靠這個。

**遷移到哪**：
- **本課**：SQLite（Ch 10）的 page 不是 content-addressed（它用 page number 定址），正好是**反例對照**——讓你體會「什麼時候該用 content addressing、什麼時候不該」（可變、頻繁改的資料用位置定址；不可變、要去重的用內容定址）。
- **你職涯**：Docker image layer（layer 用 content digest 定址、跨 image 共享）、IPFS（整個系統就是 content addressing）、Nix/Bazel 的 build cache（輸入 hash 當 key 快取產物）、備份系統的區塊去重。認出這張卡片，這些系統你都秒懂它們為什麼能去重。

---

## 卡片 2：Immutable DAG（不可變的物件圖）

**一句話**：物件不可變（改了就是新物件、新 id），物件之間用 id 互指構成一張有向無環圖（DAG）；「歷史」= 沿著這張圖走。可變的東西（branch）獨立成一層「指標」。

**在 git 哪裡**：
- `commit.h` 的 `struct commit` 有 `struct commit_list *parents`（指向 parent commit）和 `struct tree *maybe_tree`（指向那一刻的目錄快照）。commit → parent + tree 就是 DAG 的邊。
- `commit.c:462` 的 `parse_commit_buffer` 逐行解析 `parent <oid>`，`commit_list_insert` 把 parent 串進 `parents` 鏈——這是「從 object 內容重建圖的邊」。
- **不可變**：object 一旦寫入，內容和 oid 永遠綁死；沒有任何函式「原地改一個 commit 的內容」。你 `git commit --amend` 產生的是**全新 commit**，舊的還在（reflog 找得到）。
- **可變層獨立**：ref（`.git/refs/heads/main` 裡一行 oid）是唯一可變的東西，它只是「一個指向某 commit 的名字」。改 branch = 改指標指向，object 圖本身不動。

**beacon（怎麼一眼認出）**：
- 看到「節點存的是別的節點的 id/hash，而不是指標或外鍵」（commit 存 parent 的 oid）。
- 看到「沒有 update/mutate 物件內容的 API，只有 create 新的」（immutability 的訊號）。
- 看到「一層不可變的資料 + 一層薄薄的可變指標」（object vs ref 的分離）。

**遷移到哪**：
- **本課**：三個 VM 的 AST/bytecode 也常是不可變樹（Lua 的 `Proto`、CPython 的 code object）；儲存引擎的 MVCC 版本鏈（新版本不覆蓋舊版本）是同一個「不可變 + 指標指向」哲學。
- **你職涯**：函數式資料結構（persistent data structure，改一個節點產生新版本、共享未改的子樹——git 的 tree 就是這樣，改一個檔只產生沿路的新 tree）、event sourcing（append-only 事件流 + 可變的投影）、區塊鏈（每個 block 指向前一個 block 的 hash，是 content-addressed 的線性 DAG）。認出「不可變資料 + 指標圖」你就懂了這一整類系統。

---

## 卡片 3：Loose + Packed 雙層儲存

**一句話**：寫入走「輕量、簡單、當場不優化」的一層（loose）；累積後由後台批次整理成「省空間、優化過」的另一層（packed）。關鍵路徑輕，昂貴的整理挪到背景。

**在 git 哪裡**：
- Ch 19：loose object（`write_loose_object`，一 object 一檔、zlib）vs packfile（`git gc` 打包 + delta）。
- 讀取 `do_oid_object_info_extended`（`object-file.c:1625`）**先查 pack 再查 loose**——因為整理後熱資料在 pack。
- 寫入永遠先 loose（`write_object_file_flags` 直接寫 loose，不當場打包）——寫入路徑保持輕。

**beacon（怎麼一眼認出）**：
- 看到「寫入很簡單、有個獨立的 `compact`/`gc`/`repack`/`flush`/`merge` 後台步驟」。
- 看到「讀取要同時查『新寫的』和『已整理的』兩個地方，且有優先順序」。
- 看到「寫入不做壓縮/排序/去重，整理階段才做」。

**遷移到哪**：
- **本課**：這是 **LSM-tree** 的核心哲學（你在 `database_internals` 見過）——memtable/新 SSTable（寫入快）+ 後台 compaction（整理）。SQLite 的 WAL（Ch 10）也是「先 append 到 WAL / 快，checkpoint 時再併回主 db / 慢」的同一個分層。
- **你職涯**：任何「寫入路徑要快、可以容忍後台整理」的系統：日誌型資料庫、Kafka 的 log + compaction、檔案系統的 journaling、GC 的 minor/major 分代。認出這張卡片，你就知道去找那個「後台整理」的觸發點在哪、整理時的取捨是什麼（空間 vs 讀取延遲）。

---

## 卡片 4：Command Dispatch Table（命令分派表）

**一句話**：用一張「key → 函式指標（+ metadata）」的表取代一堆 `if/else if`；分派 = 查表 + 呼叫。

**在 git 哪裡**：
- Ch 18：`git.c:32` 的 `struct cmd_struct { 名字; 函式指標 fn; option flags; }`，`git.c:506` 的 `commands[]` 陣列，`git.c:653` 的 `get_builtin`（`strcmp` 線性查）+ `run_builtin` 的 `p->fn(...)` 呼叫。

**beacon（怎麼一眼認出）**：
- 看到「一個 struct 陣列，每個元素有『名字/id + 一個函式指標』」。
- 看到「一個迴圈或索引查這張表，然後 `p->fn(...)` / `table[op].handler(...)`」。
- **陷阱訊號**：看到 `p->fn(...)` 這種呼叫，LSP 跳不過去——這就是 dispatch table，你得先知道 `p` 指向表裡哪格。

**遷移到哪**：
- **本課的高光對照**：三個 VM 的 opcode 分派本質上是同一張卡片的變體——
  - Lua（Ch 4）：opcode → `case` / computed goto。
  - SQLite VDBE（Ch 9）：opcode → `case OP_xxx`。
  - CPython（Ch 23）：opcode → eval loop 的 `TARGET(xxx)`。
  - git 這裡是**最好認的入門版**，因為 key 是人類可讀的命令字串（`"cat-file"`），不是數字 opcode。認熟 git 這張，再看三個 VM 的 opcode dispatch 就一眼 chunk 出來。這是 Ch 27「三個 VM 橫向對照」的伏筆。
- **你職涯**：syscall table（`sys_call_table`，你在 kernel 課見過）、HTTP router（path → handler，nginx 的 module handler pipeline，Ch 16）、protocol 的 message type → handler、任何 plugin 系統的註冊表。

---

## 卡片 5：Delta 壓縮（差異編碼）

**一句話**：對兩個相似的物件，不各存完整內容，而是存「以 A 為基準 + 一串修改指令」得到 B。空間換運算（讀取時要「重放」修改）。

**在 git 哪裡**：
- Ch 19：packfile 裡的 `OBJ_OFS_DELTA`(6) / `OBJ_REF_DELTA`(7)（`object.h:105-107`）。一個 delta object 的「內容」是「基準的參照 + copy/insert 指令」。
- `packfile.c:1228` 附近解 delta：`if (type == OBJ_OFS_DELTA) ... else if (type == OBJ_REF_DELTA)`——分別用「pack 內偏移」或「基準 oid」定位基準。
- 基準跟 commit 歷史**無關**：git 打包時純按「哪個當基準最省空間」挑，甚至可以拿較新的 object 當較舊 object 的基準。

**beacon（怎麼一眼認出）**：
- 看到「儲存的不是完整資料，而是『base 參照 + diff/patch/指令』」。
- 看到「讀取時要先取得 base、可能遞迴取 base 的 base（delta chain）才能還原」。
- 看到「有個參數限制 chain 深度」（深度 = 空間 vs 讀取速度的旋鈕）。

**兩種 delta 參照方式（git 的細節）**：`OBJ_OFS_DELTA` 用「在同一個 pack 內往前多少 byte」指向 base（offset delta，較省），`OBJ_REF_DELTA` 用 base 的完整 oid 指向（reference delta，可跨 pack）。`packfile.c:1228` 那個 `if/else` 就是在分這兩種。認得這個區分，你讀 `git verify-pack -v` 的輸出時，`chain` 欄和 base 型別就看得懂了。

**遷移到哪**：
- **本課**：MVCC 資料庫的版本鏈有時也用 delta（存版本間差異而非完整 row）；備份系統的增量備份。
- **你職涯**：影片編碼（I-frame = 完整、P/B-frame = delta，跟 git 的 base+delta 一模一樣的哲學）、rsync 的滾動雜湊差異傳輸、二進位 patch（bsdiff）、資料庫 replication 的 binlog（存變更而非完整狀態）。認出這張卡片你就懂「為什麼讀取有時要遞迴還原一條 chain」以及它的效能取捨。

---

## 卡片 6（加碼）：三個「小而通用」的實作 idiom

前五張是 git 的骨架 pattern。但讀 Ch 18–20 時你還撞到三個更小、但在 C 系統程式裡到處出現的實作 idiom。它們不夠格當「架構 pattern」，卻是你辨識 C code 的高頻 beacon，值得各記一句：

**6a：請求描述 struct（取代一堆 out-parameter）**
- **在 git 哪裡**：`object-file.c` 的 `struct object_info oi`（Ch 19）。呼叫 `oid_object_info_extended` 前，你把「想要型別就填 `oi.typep`、想要內容就填 `oi.contentp`」，同一個查詢函式因此既能只問型別、也能要完整內容。
- **beacon**：看到「呼叫前先填一個 struct 的一堆指標欄位、把 struct 傳進去」，而不是「函式簽章有五六個 `T **out` 參數」。
- **遷移**：`stat()` 的 `struct stat *`、`epoll_event`、任何「一次呼叫可選擇性回傳多個東西」的 API。這是 C 沒有具名/預設參數時的標準補償手法。

**6b：寫暫存檔 + 原子 rename（防禦式寫入）**
- **在 git 哪裡**：`write_loose_object`（`object-file.c:2277`）先寫暫存檔、`finalize_object_file_flags`（`object-file.c:2036`）再 `rename` 成正式檔名。
- **beacon**：看到「寫一個 `tmp`/`.lock`/隨機名檔案 → 全部寫完 → `rename` 成目標名」。
- **遷移**：SQLite 的 rollback journal / WAL（Ch 10）、`dpkg` 的 `.dpkg-tmp`、幾乎所有「要嘛全部生效、要嘛完全沒發生」的檔案更新。認出它，你就知道這段 code 在保證 crash 時不留半寫壞檔。

**6c：varint（變長整數）**
- **在 git 哪裡**：`packfile.c:1091` 的 `unpack_object_header_buffer`，`while (c & 0x80)` 每 byte 用低 7 bits 存資料、最高位當「還有下一 byte」。
- **beacon**：看到 `while (byte & 0x80) { val |= (byte & 0x7f) << shift; shift += 7; }` 這種形狀。
- **遷移**：protobuf 的 varint、DWARF 的 LEB128、WebAssembly、幾乎所有要省空間的二進位格式。這個迴圈形狀認熟，一堆二進位協定你都能一眼看出「這裡在解變長數」。

**這三張小卡片的意義**：pattern 不只有「架構級」的大東西，也有「一看形狀就懂」的小 idiom。後者數量更多、出現更頻繁，是你讀 C code 速度的真正底盤。Ch 18–20 隨手就撞到三個——這說明**傳奇 codebase 的每一頁都在教你 pattern**，只要你養成「這個形狀我在哪見過」的反射。

---

## 怎麼自己造一張 pattern 卡片

讀教材給你的卡片是被動的；**自己從陌生 code 抽出一張卡片，才是這門課要練的動作**。步驟：

1. **注意到重複**：同一種「形狀」在 code 裡出現第二次時（第二個 dispatch table、第二個 `while (c & 0x80)`），停下來——這可能是個 pattern。
2. **命名它**：給它一個你記得住的名字（「請求描述 struct」「寫暫存+rename」）。命名是 chunking 的關鍵，有名字大腦才存得住。
3. **抽出 beacon**：問「我下次在陌生 code 裡看到什麼，就該想到它？」把那個視覺形狀寫下來。
4. **問它買賣什麼**：它用什麼代價換什麼好處？（dispatch table 用一層 indirection 換好擴充。）
5. **找第二個宿主**：在另一個你看過的 codebase 裡找同一個 pattern。找到了，它才真正變成「可遷移」的，而不是「git 的某個細節」。

拿卡片 6a 練一次：你在 `object-file.c` 看到 `struct object_info`（第一次），又在別處看到 `struct stat`（第二次）→ 命名「請求描述 struct」→ beacon 是「填 struct 指標欄位再傳進去」→ 買賣是「用一個 struct 換掉一長串 out-parameter」→ 第二個宿主是 `stat()`/`epoll`。一張卡片就成形了。**這個動作本身，比記住任何一張現成卡片都重要。**

---

## 五張卡片怎麼協同（git 的整體設計）

單張卡片是零件，git 的威力來自它們**組合**：

```
   content addressing（卡1）  ──提供──▶  不可變性 + 去重
        │                                    │
        ▼                                    ▼
   immutable DAG（卡2）  ──物件互指 oid 構成──▶  版本歷史 = 走圖
        │
        │  DAG 上大量相似的 tree/blob（改一個檔，沿路 tree 都是新的但只差一點）
        ▼
   delta 壓縮（卡5）  ──把相似 object 壓成差異──▶  省空間
        │
        ▼
   loose + packed（卡3）  ──寫入輕、後台打包+delta──▶  寫入快 + 空間省
```

注意這個因果鏈：**因為 content addressing 讓物件不可變、且改一點就產生大量相似物件（新 tree/blob），git 才特別需要 delta 壓縮來省空間，而 delta 昂貴所以放進後台的 pack 層。** 五個 pattern 不是各自獨立，是一個設計決定（content addressing）逼出的一整套配套。這就是 Ch 0 說的「模型即一切」——一個核心決定，其餘設計順勢展開。而 command dispatch（卡4）是操作這整個模型的門面。**認出這種「一個核心決定 + 配套 pattern」的結構，是讀懂任何系統設計意圖的關鍵。**

具體感受一下：跟蹤「你改一個檔、`git commit`」時，五個 pattern 怎麼一個個被觸發——

```
   你改 src/a.c 的一行，git commit
      │
      ① content addressing：新內容 → 算出新 blob oid（改一個字 = 全新 blob）
      │
      ② immutable DAG：a.c 所在目錄的 tree 也得換（指向新 blob）→ 新 tree oid
      │              → 沿路每一層父目錄的 tree 都換 → 新 root tree
      │              → 新 commit 指向新 root tree + 舊 commit 當 parent
      │  （舊 blob/tree/commit 全都還在，只是沒被新 commit 指到）
      │
      ③ command dispatch：這一切由 cmd_commit（commands[] 查表分派）驅動
      │
      ④ loose+packed：新產生的 object 先寫成 loose（快），丟著
      │
      ⑤ delta 壓縮：等你哪天 git gc，新舊 a.c 的 blob 高度相似
                    → 打包時存成 delta（只記那一行的差異）
```

**看清楚 ② 那步的漣漪**：改一個檔，不只產生一個新 blob，還產生**沿路每一層目錄的新 tree**（因為 tree 存子項的 oid，子項變了 tree 就變了）。這就是「不可變 + content addressing」的必然後果——也正是為什麼 git 特別需要 ⑤ delta 壓縮（大量高度相似的 tree/blob）。你把這個漣漪在腦中跑一遍，五個 pattern 為什麼非得配套出現就一清二楚了。這種「跟蹤一個操作、看它觸發哪些 pattern」的練習，是把孤立卡片織成系統理解的最好方法。

## 一頁速查表（貼進你的 pattern 字典）

把六張卡片壓成一張掃描表——這是 Ch 30「你的 pattern 字典」會累積的那種格式，git 這一 Part 先貢獻六列：

| pattern | 一眼認出的 beacon | git 的錨點 | 下次在哪遇到 |
|---|---|---|---|
| content addressing | 用內容 hash 當 key/檔名；寫入前查 hash 去重 | `hash_object_body`（object-file.c:1941） | Docker layer、IPFS、Nix cache、Bazel |
| immutable DAG | 節點存別的節點的 id；沒有 mutate API；不可變資料 + 薄可變指標層 | `struct commit.parents`（commit.h） | persistent DS、event sourcing、blockchain |
| loose+packed | 寫入簡單 + 獨立的 gc/compact 後台步驟 | `do_oid_object_info_extended`（object-file.c:1625） | LSM-tree、WAL、Kafka log compaction |
| dispatch table | struct 陣列（名字 + 函式指標）+ 查表 `p->fn(...)` | `commands[]`（git.c:506） | 三個 VM 的 opcode、HTTP router、syscall table |
| delta 壓縮 | 存「base 參照 + 修改指令」；讀取要重放 chain | `OBJ_*_DELTA`（object.h:105） | 影片編碼、rsync、bsdiff、binlog |
| 請求描述 struct | 呼叫前填 struct 指標欄位再傳進去 | `struct object_info`（object-file.c） | stat()、epoll_event、多回傳值 API |

這張表的用法：讀新 codebase 時，掃到某段 code 覺得「這形狀好像看過」，來這張表比對 beacon 欄。對上了，右邊兩欄告訴你「它在 git 長怎樣」和「它還會出現在哪」——你就 chunk 掉了一整段 code，不用從頭推。**這就是這門課要給你的：一本越翻越厚的 beacon → pattern 對照表。**

## 對比與取捨（每張卡片的代價）

| pattern | 買到什麼 | 付出什麼 |
|---|---|---|
| content addressing | 去重、防竄改、可分散 | 內容改一點就是全新物件（配 delta 補救）；不適合頻繁原地改的資料 |
| immutable DAG | 歷史完整、可回溯、分支合併天然 | 空間（舊版本都留著）；需要 gc 清無人指向的物件 |
| loose + packed | 寫入快 + 空間省 | 讀取要查兩層；需要後台 gc 觸發時機的調校 |
| dispatch table | 好擴充、好讀、少分支 | 一層 indirection（LSP 跳不過去）；查表有微小成本 |
| delta 壓縮 | 省空間 | 讀取要重放 chain（深度太深會慢）；打包時要花 CPU 挑基準 |

沒有免費的 pattern。認出一個 pattern，同時就要問「它的代價在這個系統裡可接受嗎」——這是從「認得 pattern」升級到「判斷設計好壞」的關鍵一步。

## 反例對照：什麼時候**不**該用這些 pattern

認出 pattern 只學到一半；知道它的**邊界**（什麼時候不適用）才是另一半。用本課其他 codebase 當反例：

- **content addressing 不適合頻繁原地改的資料。** SQLite（Ch 10）的 page **不是** content-addressed——它用 page number（位置）定址。為什麼？資料庫的一頁會被反覆改（insert/update/delete），如果用內容雜湊當 id，改一個 byte 整頁 id 就變，所有指向它的 B-tree 指標全要更新，代價爆炸。**可變、頻繁改、需要就地更新 → 用位置定址；不可變、要去重、要防竄改 → 用內容定址。** git 的 object 一次寫死永不改，所以 content addressing 完美；DB 的 page 一直改，所以用 page number。這個對比是「同一個問題（怎麼給資料命名）在不同約束下的相反答案」，比記住任一個 pattern 都值錢。
- **immutable DAG 的代價是空間，需要 gc 收尾。** git 的舊 object 不會自動消失（`git commit --amend` 後舊 commit 還在），要靠 `git gc` 清掉沒有任何 ref 指向的 object。純不可變 = 只增不刪 = 需要一個回收機制。如果你的系統無法容忍「舊資料堆積 + 定期 gc」，immutable DAG 就不合適。
- **dispatch table 在分支極少時是過度設計。** git 有一百多個命令，用表值得；但如果只有兩三個分支，一個 `if/else` 更直白。看到「只有 3 個 case 卻搞一張函式指標表」，那是 over-engineering，不是好 pattern。
- **loose+packed 的後台整理需要觸發時機。** 如果整理（gc/compaction）跟不上寫入速度，兩層會膨脹失控（LSM 的「compaction 追不上」是真實的生產事故）。這個 pattern 隱含一個假設：**整理有空檔可做**。持續高寫入、沒有喘息的系統要特別小心。

**規律**：每個 pattern 都對應一組「約束假設」。content addressing 假設「資料不可變」，loose+packed 假設「整理有空檔」，dispatch table 假設「分支夠多」。認出 pattern 時，同時要問「這個系統滿足它的約束假設嗎」。滿足才是好設計，不滿足就是誤用。這是從「認得 pattern」到「判斷設計對錯」的最後一哩。

## 踩雷集錦

1. **把 pattern 當標籤貼完就走。** 認出「這是 content addressing」只是第一步，重點是接著推「所以它有去重、防竄改、可分散」，並問「它的代價（不適合頻繁改）在這裡可接受嗎」。pattern 是推理的起點，不是終點。
2. **以為 git 的 pattern 是 git 獨有的。** 全不是。content addressing 是 IPFS/Docker/Nix 的地基，dispatch table 是每個 VM/router 的骨架，loose+packed 是 LSM-tree 的哲學。把它們鎖死在「git 的東西」會浪費遷移價值。
3. **看到 delta 就想成「版本間的 diff」。** git 的 delta 基準跟版本歷史無關，純按省空間挑。影片編碼的 P-frame、rsync 的差異也一樣——delta 是**儲存/傳輸層的壓縮**，別跟「邏輯上的版本差異」混為一談。
4. **忽略 pattern 之間的因果。** 五張卡片不是並列清單，是一條因果鏈（content addressing 逼出 delta、delta 昂貴放進 pack）。看不到這條鏈，你就只記住五個孤立技巧，而不是「一個核心決定如何展開成整套設計」的思考方式。
5. **不回去核對就相信卡片。** 每張卡片都標了真實檔案/函式（`git.c:653`、`commit.c:462`…）。pattern 是抽象，但抽象必須錨定在你親眼核對過的真實 code 上，否則會退化成模糊的口號。

## 進階：再往深一層

- **寫下你自己版本的卡片。** 合上這一章，憑記憶把五張卡片各寫一句 + 一個 beacon。寫不出來的那張，就是還沒真正 chunk 進去的——回 Ch 18–20 重讀那部分。這是 Ch 2「訓練協定」的費曼複述在 git 上的實踐。
- **找第六張卡片。** 這三章還有 pattern 沒收進來：`struct object_info`（用「請求描述 struct」取代一堆 out-parameter，Ch 19 讀 `repo_read_object_file` 時提過）、「寫暫存 + 原子 rename」的防禦式寫入（Ch 19）、varint 變長編碼（Ch 19 的 pack header）。試著自己為其中一個補一張卡片，練習「從 code 抽 pattern」這個動作本身。
- **反向練習：拿一張卡片去別的 codebase 找。** 帶著「dispatch table」這張卡片去掃 Lua 的 `lvm.c` / SQLite 的 `vdbe.c`，看你能不能在還沒讀那門課之前就一眼認出它們的 opcode 分派。認得出來，代表這張卡片真的進了你的字典。

## 這些卡片接下來會在哪兌現

pattern 卡片的價值要在「下次遇到」時才變現。先預告本課接下來哪裡會用到 git 這一 Part 的卡片，讓你帶著預期去讀：

- **CPython（Part 5，馬上就到）**：`ceval.c` 的 eval loop 是 **dispatch table**（卡 4）的又一個宿主——只是 key 從 git 的命令字串換成 bytecode opcode。你在 git 認熟的「表 + 查 + 呼叫」形狀，讀 CPython 的 `TARGET(opcode)` 分派時會直接 chunk 出來，不用重學。CPython 的 PyObject 也有「不可變 vs 可變」的區分（int/str 不可變 vs list 可變），和 git 的 object vs ref 是同一個直覺。
- **Ch 27（三個 VM 橫向對照）**：dispatch table 卡片的高光時刻——Lua/SQLite/CPython 三個 VM 的 opcode 分派並排，你會看到同一張卡片的三種變體（switch / computed goto / label 陣列）。git 是它們的入門版。
- **Capstone PostgreSQL（Ch 28）**：executor 的火山模型節點樹，本質是 **immutable-ish 的 plan 樹 + dispatch**（每個節點型別對應一個 `ExecProcNode` 函式指標）——又是 dispatch table + DAG 的組合。

**帶著卡片讀，比空手讀快一個檔次。** 這就是這門課設計成「讀一個 → 萃取 → 再讀下一個」的原因：前面萃取的卡片，是後面讀碼的加速器。

## 費曼複述：把 git 的設計對空氣講一遍

Ch 2 的訓練協定最後一步是費曼測試——對著空氣把核心機制講一遍，講不順的地方就是沒讀懂的地方。這裡示範一段 git object model 的費曼腳本，你讀完該能自己講出類似的一段（講的時候不准看教材）：

> 「git 的核心是四種不可變的 object：blob 是純內容、tree 是把檔名綁到 oid 的一層目錄、commit 指向一棵 tree 加零到多個 parent、tag 指向某個 object。每個 object 的名字是它內容的雜湊——這叫 content addressing，我可以用 `printf 'blob 10\0內容' | sha1sum` 手動重現。因為名字是內容雜湊，同內容自動去重、改一個 bit 名字全變所以防竄改、兩個節點離線算出同 oid 所以能分散協作。這些 object 平常一個一個以 zlib 壓縮存成 loose 檔，路徑用 oid 前兩碼分目錄；累積多了 `git gc` 打包成 packfile，相似的 object 之間存 delta 只記差異。讀取時先查 pack 再查 loose。至於命令，`git.c` 有一張 `commands[]` 表把命令字串對到函式指標，分派就是查表 + 呼叫 `p->fn`——追任何命令我都先 `rg` 這張表找 entry。ref（branch/tag/HEAD）不是 object，是一層可變的指標，指向某個 commit oid；所有改歷史的命令動的都是這層指標，object 本身永不變。」

這段話裡的每一句你都在 Ch 18–20 讀過真實 code 佐證。**如果你講到某一句卡住、或發現自己在含糊帶過（「大概就是…」），那一句就是你要回去重讀 code 的地方。** 費曼測試的價值不是複述，是**逼出你以為懂其實沒懂的縫隙**。試著現在就講一遍，記下你卡在哪。

## 本章重點整理

- 五張 git pattern 卡片：**content addressing**（內容雜湊當身分 → 去重/防竄改/可分散）、**immutable DAG**（不可變物件互指 oid + 獨立的可變 ref 層）、**loose+packed 雙層儲存**（寫入輕、後台批次整理，= LSM 哲學）、**command dispatch table**（key→函式指標表，= 三個 VM 的 opcode 分派）、**delta 壓縮**（base + 修改指令，= 影片編碼/rsync）。
- 每張卡片記三件事：**在 git 哪裡**（回去核對）、**beacon**（一眼認出的形狀）、**遷移到哪**（本課其他 Part + 職涯）。
- 五個 pattern 是一條因果鏈，不是並列清單：**content addressing 逼出不可變 + 大量相似物件 → 需要 delta 壓縮 → delta 昂貴放進 pack 層**。認出「一個核心決定展開成整套配套」是讀懂設計意圖的關鍵。
- pattern 是推理起點不是終點：認出後要推它附帶的性質、問它的代價在這系統可否接受。

## 自我檢核

- [ ] 我能不看教材說出五張卡片，每張給一句話 + 一個 beacon。
- [ ] 對每張卡片，我能指出它在 git 的哪個檔案/函式，以及本課哪個其他 Part 會再遇到它。
- [ ] 我能解釋 content addressing 附帶的三個性質，以及它為什麼逼出 delta 壓縮。
- [ ] 我能說出「dispatch table」這張卡片怎麼從 git 的命令表遷移到三個 VM 的 opcode 分派。
- [ ] 我能對至少一張卡片說出它的代價，展示我不只是貼標籤而是在判斷取捨。

## 延伸閱讀

- **Ch 27「三個 VM 橫向對照」（本課後續）**
  - **讀哪裡**：整章。它把 command dispatch table（卡片 4）在 Lua / SQLite / CPython 三個 VM 的具體形態並排，是本章「遷移到哪」的兌現。
  - **學什麼**：同一張 pattern 卡片在三個不同 codebase 的變體，親眼看 chunking 遷移的高光。
  - **前提**：讀過 Part 1、2、5 的 VM 章。
- **[Git from the Bottom Up — John Wiegley](https://jwiegley.github.io/git-from-the-bottom-up/)**
  - **讀哪裡**：「Repository: Directory content tracking」到「The branch, and its relationship to commits」。它從 object model 往上推整個 git，和本章「一個核心決定展開成配套」的視角一致。
  - **學什麼**：把五張卡片重新編織成一個連貫的「為什麼 git 這樣設計」的故事。
  - **前提**：讀過 Ch 18–20。
- **《The Programmer's Brain》第 2–3 章（chunking / beacon）**
  - **讀哪裡**：chunking 與 beacon 兩節。本章「beacon」欄的理論根據——為什麼「一眼認出形狀」能讓你讀得快。
  - **學什麼**：pattern 卡片為什麼有效的認知科學解釋，讓你相信「結晶 pattern」這個動作值得花時間。
  - **前提**：無。

git 這一 Part 讀完了：模型（Ch 18）→ 儲存（Ch 19）→ 讀一個命令（Ch 20）→ 結晶 pattern（本章）。接下來用一個限時攻堅練習把肌肉練實——這次沒有教材帶你，你自己讀懂一個沒挑過的 git 子命令的完整實作。

→ [練習 D：讀懂一個 git 子命令](./practice-d-git-read-a-subcommand.md)
