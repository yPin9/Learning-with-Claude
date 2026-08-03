# 讀碼健身房：攻堅六個傳奇 codebase，把「更會讀」練成肌肉

> 給讀完 [`reading_code`](../reading_code/README.md)、已經有攻堅 SOP，但想從「有方法」升級到「一眼認出」的工程師與安全研究者。

`reading_code` 教你**方法**（偵察、data flow、假設驅動、收斂到要改的 200 行）。但方法不會讓你變快——**pattern 辨識**才會。高手掃一眼就認出「這是 register-based VM 的 dispatch loop」「這是 arena allocator」「這是 reactor event loop」「這是 content-addressed store」，不用從頭推。這種能力只能靠一件事練出來：**在真正硬、真正經典的 codebase 上反覆讀，並把讀到的設計 pattern 結晶下來**。

這門課就是那座健身房。我們用 `reading_code` 的 SOP，限時攻堅六個傳奇 codebase，每讀完一個就萃取它可遷移的設計 idiom，累積成你的「一眼認出」字典。全程 clone 真實 source、真 build、真跑、真讀——不是隔空講解。

## 為什麼學這個？

- **讀碼是速度技能，速度來自 pattern 庫**：認知科學稱之為 chunking——專家不是讀得更用力，是把一大段 code 一眼 chunk 成一個已知概念。pattern 庫越大，讀越快。這門課就是系統化地擴充你的 chunk 庫。
- **最好的老師是傳奇 code 本身**：Lua、SQLite、nginx、git、CPython、PostgreSQL——這些是幾十年打磨、被無數人讀過的典範。讀懂它們，你同時學到「這個領域怎麼設計」和「頂尖工程師怎麼組織 code」。
- **職涯與研究角度**：onboarding、貢獻開源、找漏洞、逆向——全都卡在「能多快讀懂陌生 code」。讀過六個硬目標之後，第七個陌生專案對你不再陌生。

## 先修知識

- **讀完 [`reading_code`](../reading_code/README.md)**（或等價的讀碼 SOP 基礎）——本課假設你已有偵察、追 data flow、建架構地圖的方法，這裡是拿真目標練。
- **C 讀寫能力**（程度：pointer / struct / 函式指標 / 巨集看得懂；六個目標裡五個是 C）
- **命令列 + git + 能 build C 專案**（clone、make、跑 configure）
- 沒有也沒關係的：這些專案的領域知識（VM、資料庫、web server 內部）——本課邊讀邊補

## 課程地圖

### Part 0 — 訓練法：pattern 辨識的科學（Ch 0–2）
- [Ch 0 環境與六個釘死的攻堅目標](./00-environment-and-pinned-targets.md)
- [Ch 1 讀碼即 pattern 辨識：chunking 的科學](./01-reading-is-pattern-recognition.md)
- [Ch 2 訓練協定：限時攻堅 → 萃取 pattern → 費曼複述](./02-the-training-protocol.md)

### Part 1 — Lua：最小完美的語言 runtime（Ch 3–7）
- [Ch 3 Lua 偵察：2 萬行的架構地圖](./03-lua-recon.md)
- [Ch 4 register-based VM 與 dispatch loop](./04-lua-register-vm.md)
- [Ch 5 值表示與 table：TValue 與 ltable](./05-lua-values-and-tables.md)
- [Ch 6 incremental GC：讀 lgc.c](./06-lua-incremental-gc.md)
- [Ch 7 萃取 pattern：語言 runtime 的可遷移 idiom](./07-lua-patterns-extracted.md)
- [練習 A：限時攻堅一條 Lua 執行路徑](./practice-a-lua-trace-a-path.md)

### Part 2 — SQLite：儲存引擎 + VDBE（Ch 8–12）
- [Ch 8 SQLite 偵察：分層架構](./08-sqlite-recon.md)
- [Ch 9 VDBE：bytecode 虛擬機（對照 Lua VM）](./09-sqlite-vdbe.md)
- [Ch 10 B-tree 與 pager](./10-sqlite-btree-pager.md)
- [Ch 11 讀 SQLite 的防禦式 C](./11-sqlite-defensive-c.md)
- [Ch 12 萃取 pattern：VM 分派 / pager / amalgamation](./12-sqlite-patterns-extracted.md)
- [練習 B：追一條 SQL 從 text 到 disk read](./practice-b-sqlite-trace-a-query.md)

### Part 3 — nginx：event-driven 高並發架構（Ch 13–17）
- [Ch 13 nginx 偵察：master/worker 與模組化](./13-nginx-recon.md)
- [Ch 14 event loop / reactor：epoll 封裝](./14-nginx-event-loop.md)
- [Ch 15 memory pool、buffer chain、資料結構慣例](./15-nginx-memory-and-buffers.md)
- [Ch 16 module / handler pipeline](./16-nginx-module-pipeline.md)
- [Ch 17 萃取 pattern：reactor / object pool / plugin pipeline](./17-nginx-patterns-extracted.md)
- [練習 C：追一個 HTTP request 的完整處理鏈](./practice-c-nginx-trace-a-request.md)

### Part 4 — git：資料模型即一切（Ch 18–21）
- [Ch 18 git 偵察：plumbing vs porcelain 與 object model](./18-git-recon-object-model.md)
- [Ch 19 content-addressed store 與 packfile](./19-git-object-store-packfiles.md)
- [Ch 20 讀一個 git 子命令的完整實作](./20-git-reading-a-command.md)
- [Ch 21 萃取 pattern：content addressing / DAG](./21-git-patterns-extracted.md)
- [練習 D：讀懂一個 git 子命令](./practice-d-git-read-a-subcommand.md)

### Part 5 — CPython：大型 runtime（Ch 22–26）
- [Ch 22 CPython 偵察：object model 與 eval 入口](./22-cpython-recon.md)
- [Ch 23 ceval.c：bytecode eval loop（三個 VM 的第三個）](./23-cpython-eval-loop.md)
- [Ch 24 object model：PyObject / type / refcount + cyclic GC](./24-cpython-object-model.md)
- [Ch 25 大型專案的分而治之實戰](./25-cpython-divide-and-conquer.md)
- [Ch 26 萃取 pattern：refcount / object protocol / C-API 邊界](./26-cpython-patterns-extracted.md)
- [練習 E：追一個 Python 語意到 C](./practice-e-cpython-trace-a-semantic.md)

### Part 6 — Capstone 與畢業（Ch 27–31）
- [Ch 27 三個 VM 橫向對照：pattern 遷移的高光](./27-three-vms-compared.md)
- [Ch 28 Capstone：冷讀 PostgreSQL executor](./28-capstone-postgres-executor.md)
- [Ch 29 Capstone 攻堅實況：限時、外化、費曼](./29-capstone-attack-live.md)
- [Ch 30 你的 pattern 字典：六個 codebase 的 idiom 收斂成一張表](./30-your-pattern-dictionary.md)
- [Ch 31 打造持續讀碼的訓練習慣](./31-sustained-reading-practice.md)
- [Final Project：冷啟動攻堅一個你沒看過的 codebase](./final-project-cold-codebase-attack.md)

## 學習方式建議

1. **每個 Part 都真的 clone 那個 repo**：本課所有章節都釘死了版本（見 Ch 0），你 clone 同一個 tag，看到的檔名、行號、function 都和教材一致。不 clone 來讀等於沒上這門課。
2. **先自己限時攻堅，再看教材**：每個 Part 開頭的偵察章，先給自己 60 分鐘自己讀，再對照教材怎麼攻。你的地圖和教材的差距，就是你要補的。
3. **萃取章要自己先寫**：讀完一個 codebase，先合上教材寫下「我認出了哪些 pattern」，再看萃取章。pattern 要自己說出來才會進你的長期記憶。
4. **費曼測試**：每個 Part 結束，試著對著空氣把這個 codebase 的核心機制講一遍。講不順的地方就是沒讀懂的地方。

## 精選資料庫

每章「延伸閱讀」會指向更具體的小節與該 codebase 的官方設計文件。

### 必讀基礎

- **《The Programmer's Brain》** — Felienne Hermans（Manning, 2021）
  - chunking / working memory / beacon 的認知科學基礎；本課 Part 0 的理論支柱，解釋「為什麼 pattern 庫讓你讀得快」。
- **各 codebase 的官方架構文件**（Ch 0 會列出每個目標的權威內部文件連結）
  - SQLite [Architecture](https://www.sqlite.org/arch.html)、git [Git Internals](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)、CPython [Internals docs](https://devguide.python.org/internals/) 等——讀 code 前先讀它們的自述，事半功倍。

### 推薦讀物

- **[500 Lines or Less / The Architecture of Open Source Applications](https://aosabook.org/)**
  - 頂尖工程師親自導讀自己專案架構的系列；本課讀的幾個目標在裡面有專章，是絕佳的對照與補充。
- **John Ousterhout, [A Philosophy of Software Design](https://web.stanford.edu/~ouster/cgi-bin/aposd.php)**
  - 反向理解「好 code 長怎樣」能加速你判斷「這段想幹嘛」；與本課萃取 pattern 的視角互補。

### 讀完本課之後

- 把 Final 的冷啟動攻堅法變成習慣：每季挑一個你領域最硬的新 codebase，限時攻堅一次，pattern 字典就會持續長大。
