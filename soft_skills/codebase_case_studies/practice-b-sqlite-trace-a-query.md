# 練習 B — 追一條 SQL 從 text 到 disk read

> **目標**：限時攻堅。拿一條最簡單的 `SELECT x FROM t WHERE id=?`，親手追它從**一串文字**變成**一次磁碟讀取**的完整路徑：tokenize → parse → codegen 出 VDBE bytecode → `sqlite3VdbeExec` 跑 opcode → B-tree 查找 → pager 取頁 → VFS 讀檔。全程在真 clone 的 3.47.2 上 `rg`/讀檔驗證，能用 `EXPLAIN` + `bytecode()` + shell 真跑就真跑。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）
> **建議時限**：90 分鐘（前 60 分鐘自己攻，卡住再看提示；最後 30 分鐘對參考解答）

## 為什麼是這個任務

Part 2 的四章各自讀了一層（Ch 8 地圖、Ch 9 VDBE、Ch 10 btree/pager、Ch 11 風格）。但**讀懂每一層 ≠ 能把它們串成一條線**。真正的讀碼功力是「拿到一個具體輸入，能一路追到最底」。這條 SQL 短到你能全程掌握，又長到穿過 SQLite 的每一層——是把「讀過」變成「會攻」的最佳器材。

這正是 `reading_code` 練習 B（追一個功能的完整路徑）在 SQLite 上的實戰。攻完你會有一條**完整的脊椎**：日後遇到任何「這個輸入怎麼變成那個輸出」的問題，你有一套可複製的追法。

## 任務規格（精確版）

在你 clone 的 `/tmp/rd_sqlite`（或你的 `~/cbcs/sqlite`）上，回答並用 code/真跑佐證以下**七站**：

1. **入口**：外部呼叫 `sqlite3_prepare_v2("SELECT x FROM t WHERE id=?")` 和 `sqlite3_step()`——這兩個 API 各自負責哪一半（編譯 vs 執行）？各自的 C 入口 function 在哪個檔？
2. **tokenize**：SQL 字串被誰切成 token？切 token 的 function 叫什麼、在哪個檔？
3. **parse + codegen**：parser 的邏輯讀哪個檔（提示：不是 `parse.c`）？codegen 用哪個 API 把一條 opcode append 進 bytecode？
4. **bytecode**：用 `EXPLAIN` 或 `bytecode()` 真跑，貼出這條 SQL 的 opcode 列表。指出哪個 opcode 是「打開表」、哪個是「查 rowid」、哪個是「取欄」、哪個是「吐一列」。
5. **VDBE 執行**：`sqlite3VdbeExec` 的 dispatch loop 長什麼樣（貼那一行 `for` + `switch`）？`OP_SeekRowid` 這個 opcode 內部呼叫哪個 B-tree function 去真正查找？
6. **B-tree → pager**：B-tree 要一頁時透過哪個 function 跟 pager 拿？頁號怎麼換算成檔案的 byte offset？
7. **VFS 落地**：pager 讀盤最終呼叫哪個函式指標？在 Unix 上它指向哪個 function？最底是哪個 syscall？

**交付**：一條從 `sqlite3_prepare_v2` 到 `pread` 的 call chain（至少 8 站，每站標檔名:行號），加上你真跑的 bytecode 輸出。

## 開始前：把環境架好

```bash
$ cd /tmp/rd_sqlite
$ git rev-parse --short HEAD          # 應該是 262de1b，和教材對齊
262de1b
```

能 build 出 shell 最好（追第 4、第 5 站時能真跑看 bytecode）。build 流程見 Ch 8/Ch 9（`./configure && make sqlite3` 或本課在 Windows/MSYS 上的 CRLF 修正法）。不能 build 也能做——第 4 站改用 `sqlite3.org` 線上的 `EXPLAIN`，或純讀 code 推。

## 如果你卡住了（5 條方向提示，別急著看解答）

<details>
<summary>提示 1：找不到「執行 SQL」的入口</summary>

SQLite 是**函式庫**，沒有 `main()`（`sqlite3.exe` 那個 shell 的 main 在 `shell.c`，但那是工具不是引擎）。引擎的入口是**公開 API**。`rg 'int sqlite3_prepare_v2\('` 找編譯入口、`rg 'int sqlite3_step\('` 找執行入口。它們分別在哪個檔？（回扣 Ch 8「函式庫的入口是 API 不是 main」。）
</details>

<details>
<summary>提示 2：想讀 parser 卻找不到 parse.c</summary>

`ls src/parse.c` → not found。`parse.c` 是 lemon 從 `src/parse.y` **生成**的（build 後才在建構目錄出現）。要讀 parser 邏輯，讀 `src/parse.y` 的文法規則和它 `{}` 裡呼叫的 C 動作。（回扣 Ch 8「讀文法不讀生成檔」。）
</details>

<details>
<summary>提示 3：想看 bytecode 但不會 dump</summary>

三招任選：(a) shell 裡 `.explain on` 然後 `EXPLAIN SELECT ...`；(b) `SELECT * FROM bytecode('SELECT ...')`（需 `-DSQLITE_ENABLE_BYTECODE_VTAB`）；(c) 純讀 code：`rg 'sqlite3VdbeAddOp' src/where.c src/select.c` 看 codegen 怎麼一條條發 opcode。先用 (a) 或 (b) 真跑看結果，再回去 code 對照。
</details>

<details>
<summary>提示 4：卡在 OP_SeekRowid 不知道往哪追</summary>

`rg 'case OP_SeekRowid' src/vdbe.c` 找到 opcode 實作（vdbe.c:5426）。往下讀它的 body，找 `sqlite3Btree...` 開頭的呼叫——那就是它跨進 B-tree 層的接縫。`rg 'sqlite3BtreeTableMoveto' src/vdbe.c` 直接定位。
</details>

<details>
<summary>提示 5：B-tree 到磁碟這段斷了</summary>

順著三個關鍵字往下 `rg`：`getAndInitPage`（btree.c，B-tree 要頁）→ `sqlite3PagerGet`（pager.c，pager 給頁）→ `readDbPage`（pager.c，cache miss 時讀盤）→ `sqlite3OsRead`（os.c，呼叫 VFS）。最後 `sqlite3OsRead` 裡那句 `id->pMethods->xRead` 是函式指標——在 Unix 上 `rg 'static int unixRead' src/os_unix.c` 找到它指向誰。
</details>

## 分段步驟（自己攻時照這個節奏）

**第 1 段（15 分鐘）：定位兩個入口 + tokenize。** `rg` 找 `sqlite3_prepare_v2`、`sqlite3_step`、`sqlite3RunParser`、`sqlite3GetToken`。畫出「prepare 這半在編譯、step 這半在執行」的分界。

**第 2 段（15 分鐘）：真跑看 bytecode。** build 出 shell，`.explain on; EXPLAIN SELECT x FROM t WHERE id=2;`，把輸出抄下來。逐行標注每個 opcode 幹嘛（對照 Ch 9）。

**第 3 段（20 分鐘）：進 VDBE 追一個 opcode。** 讀 `sqlite3VdbeExec` 的 dispatch loop，然後專追 `OP_SeekRowid`——它怎麼從「rowid=2」變成一次 B-tree 查找。

**第 4 段（20 分鐘）：追到磁碟。** 從 `sqlite3BtreeTableMoveto` 順藤摸瓜：`getAndInitPage` → `sqlite3PagerGet` → `readDbPage` → `sqlite3OsRead` → `unixRead` → `pread`。每站記檔名:行號。

**第 5 段（10 分鐘）：畫出完整 call chain。** 把八站串成一張圖，這是你的交付物。

## 參考解答

先自己攻，再展開。

<details>
<summary>完整攻堅實況（走真實路徑、引真 function、附真跑輸出）</summary>

### 第 1 站：兩個入口——prepare（編譯）vs step（執行）

SQLite 把「執行 SQL」拆成兩個 API：

```bash
$ rg -n "^int sqlite3_prepare_v2\(" src/prepare.c
941:int sqlite3_prepare_v2(
$ rg -n "^int sqlite3_step\(" src/vdbeapi.c
896:int sqlite3_step(sqlite3_stmt *pStmt){
```

- **`sqlite3_prepare_v2`**（prepare.c:941）：把 SQL **文字編譯成 VDBE bytecode**，產出一個 `sqlite3_stmt`（就是 `Vdbe`）。這一半跑 tokenize → parse → codegen。
- **`sqlite3_step`**（vdbeapi.c:896）：**跑那串 bytecode**，一次推進到下一個 `OP_ResultRow`（一列）。它內部呼叫 `sqlite3Step`（vdbeapi.c:754），後者呼叫 `sqlite3VdbeExec`。

分界圖：

```
  sqlite3_prepare_v2("SELECT...")   │   sqlite3_step(stmt)
  ── 編譯半 ──────────────────────  │   ── 執行半 ──────────
  tokenize → parse → codegen        │   sqlite3VdbeExec 直譯 bytecode
  產出 Vdbe（bytecode 程式）         │   跑到 OP_ResultRow → 回一列
```

### 第 2 站：tokenize——文字切 token

```bash
$ rg -n "int sqlite3RunParser|int sqlite3GetToken" src/tokenize.c
273:int sqlite3GetToken(const unsigned char *z, int *tokenType){
599:int sqlite3RunParser(Parse *pParse, const char *zSql){
```

`sqlite3RunParser`（tokenize.c:599）是編譯的驅動：它拿 SQL 字串 `zSql`，內部迴圈呼叫 `sqlite3GetToken`（tokenize.c:273）一個一個切 token（`SELECT`→SELECT、`x`→ID、`FROM`→FROM…），把 token push 進 lemon 生成的 parser。

### 第 3 站：parse + codegen——文法在 parse.y、opcode 靠 AddOp

parser **不在 `parse.c`**（那是生成檔，`ls src/parse.c` 直接 not found）。真相之源是文法：

```bash
$ sed -n '163p;189p' src/parse.y
cmd ::= BEGIN transtype(Y) trans_opt.  {sqlite3BeginTransaction(pParse, Y);}
cmd ::= create_table create_table_args.
```

每條文法規則 reduce 時執行 `{}` 裡的 C 動作，直接呼叫 codegen（一個 `SELECT` 走 `sqlite3Select()`，select.c）。codegen 產 bytecode 統一透過：

```bash
$ rg -n "^int sqlite3VdbeAddOp2\(|^int sqlite3VdbeAddOp3\(" src/vdbeaux.c
269:int sqlite3VdbeAddOp2(Vdbe *p, int op, int p1, int p2){
272:int sqlite3VdbeAddOp3(Vdbe *p, int op, int p1, int p2, int p3){
```

`sqlite3VdbeAddOp*`（vdbeaux.c:269）= 「往正在編譯的 Vdbe 程式 append 一條 opcode」。`rg 'sqlite3VdbeAddOp' src/` 有上千命中，每個都是 codegen 在寫一行 bytecode。

### 第 4 站：bytecode——真跑看見

本課用自己 build 的 3.47.2 shell 真跑（`bytecode()` 虛擬表版本）：

```
$ sqlite3 :memory:
sqlite> CREATE TABLE t(id INTEGER PRIMARY KEY, x TEXT);
sqlite> INSERT INTO t VALUES(1,'a'),(2,'b'),(3,'c');
sqlite> SELECT addr,opcode,p1,p2,p3 FROM bytecode('SELECT x FROM t WHERE id=2');
0|Init|0|7|0
1|OpenRead|0|2|0        ← 打開表 t 的 B-tree（root page 2），cursor 0
2|Integer|2|1|0         ← r[1] = 2（WHERE id=2 的 2）
3|SeekRowid|0|6|1       ← cursor 0 找 rowid==r[1]（==2），找不到跳 6
4|Column|0|1|2          ← 從 cursor 0 當前列取第 1 欄（x），放 r[2]
5|ResultRow|2|1|0       ← 把 r[2] 當一列結果吐出
6|Halt|0|0|0
7|Transaction|0|0|1     ← 開讀交易
8|Goto|0|1|0            ← 跳回 1 開始幹活
```

四個關鍵 opcode：`OpenRead`（打開表）、`SeekRowid`（查 rowid）、`Column`（取欄）、`ResultRow`（吐一列）。（`.explain on; EXPLAIN SELECT...` 會給一樣的內容，多了 p4/p5/comment 欄。）

### 第 5 站：VDBE 執行——dispatch loop + OP_SeekRowid 進 B-tree

dispatch loop 本體：

```c
  for(pOp=&aOp[p->pc]; 1; pOp++){
    ...
    switch( pOp->opcode ){
```
（`src/vdbe.c:898,981`）

`pOp` 是程式計數器（指標），`pOp++` 前進、跳轉用 `pOp=&aOp[p2-1]`。專追 `OP_SeekRowid`（vdbe.c:5426），它把「找 rowid=iKey」變成一次 B-tree 查找：

```c
  res = 0;
  rc = sqlite3BtreeTableMoveto(pCrsr, iKey, 0, &res);
```
（`src/vdbe.c:5466-5467`）

**接縫在這**：`sqlite3BtreeTableMoveto`（btree.c:5727）就是從 VDBE 層跨進 B-tree 層的那一步。`iKey` 是 2，`pCrsr` 是 cursor 0 的 B-tree cursor（`OpenRead` 開的）。

### 第 6 站：B-tree → pager——要頁與頁號換算

`sqlite3BtreeTableMoveto` 從 root page 往葉頁走，每下一層都要把子頁撈上來：

```bash
$ rg -n "sqlite3PagerGet\(pBt->pPager" src/btree.c | head -1
```
`getAndInitPage`（btree.c:2369）內部核心：

```c
  rc = sqlite3PagerGet(pBt->pPager, pgno, (DbPage**)&pDbPage, bReadOnly);
```
（`src/btree.c:2384`）

`sqlite3PagerGet`（pager.c:5707）本體只 `return pPager->xGet(...)`（函式指標，正常模式指向 `getPageNormal`，pager.c:5516）。cache miss 時 `readDbPage`（pager.c:3018）真讀盤，那句換算就是頁號 → byte offset：

```c
    i64 iOffset = (pPg->pgno-1)*(i64)pPager->pageSize;
    rc = sqlite3OsRead(pPager->fd, pPg->pData, pPager->pageSize, iOffset);
```
（`src/pager.c:3037-3038`）

**`(pgno-1)*pageSize`** 就是「檔案是一串頁」抽象最終落地的那行乘法。（WAL 模式下 `readDbPage` 開頭會先 `sqlite3WalFindFrame` 問 WAL 有沒有這頁的新版本，見 Ch 10。）

### 第 7 站：VFS 落地——函式指標到 syscall

```c
int sqlite3OsRead(sqlite3_file *id, void *pBuf, int amt, i64 offset){
  DO_OS_MALLOC_TEST(id);
  return id->pMethods->xRead(id, pBuf, amt, offset);
}
```
（`src/os.c:88`）

`id->pMethods->xRead` 是函式指標（VFS 可插拔後端）。Unix 上它指向 `unixRead`（os_unix.c:3347），內部（略過 mmap fast path 後）呼叫 `pread`/`read` syscall，把 page 2 的 4096 bytes 讀進記憶體。

### 交付：完整 call chain

```
sqlite3_prepare_v2 (prepare.c:941)              ── 編譯半 ──
  → sqlite3RunParser (tokenize.c:599)
      → sqlite3GetToken (tokenize.c:273)          切 token
      → lemon parser (parse.y → 生成 parse.c)     文法
      → sqlite3Select (select.c) 等 codegen
          → sqlite3VdbeAddOp* (vdbeaux.c:269)     發 bytecode
  產出 Vdbe（一串 opcode）

sqlite3_step (vdbeapi.c:896)                      ── 執行半 ──
  → sqlite3Step (vdbeapi.c:754)
      → sqlite3VdbeExec (vdbe.c:813)              巨型 switch dispatch
          for(pOp=&aOp[p->pc];1;pOp++) switch(pOp->opcode)  (vdbe.c:898,981)
          → case OP_OpenRead (vdbe.c:4319)        開 B-tree cursor
          → case OP_SeekRowid (vdbe.c:5426)
              → sqlite3BtreeTableMoveto (btree.c:5727)   B-tree 查找
                  → getAndInitPage (btree.c:2369)
                      → sqlite3PagerGet (pager.c:5707)   → xGet → getPageNormal
                          → readDbPage (pager.c:3018)    cache miss 讀盤
                              iOffset=(pgno-1)*pageSize   (pager.c:3037)
                              → sqlite3OsRead (os.c:88)   → pMethods->xRead
                                  → unixRead (os_unix.c:3347)
                                      → pread() syscall   ← 磁碟
          → case OP_Column (vdbe.c:2930)          取第 1 欄
          → case OP_ResultRow (vdbe.c:1712)       → return SQLITE_ROW（一列）
```

從一串文字到一次 `pread`，八層打通。這就是「一條 SQL 的一生」。

</details>

## 測試 / 驗證方式

- **你的 call chain 每一站都要 `rg` 得到**。隨手抽驗：`rg -n 'case OP_SeekRowid' src/vdbe.c` 應命中 5426；`rg -n 'sqlite3BtreeTableMoveto' src/vdbe.c` 應在 SeekRowid body 內（5467）命中。行號和你 clone 的 262de1b 對得上才算數。
- **bytecode 真跑對照**：`bytecode()` / `EXPLAIN` 的輸出，第 1、3、4、5 條應該分別是 `OpenRead`、`SeekRowid`、`Column`、`ResultRow`。跟你的 code 追蹤對得起來。
- **費曼測試**：合上這份文件，對著空氣把「`SELECT x FROM t WHERE id=2` 怎麼從文字變成一次 `pread`」講一遍。講不順的那站，就是你沒真讀懂、需要回去補的那站。

## 延伸挑戰

1. **換 rowid 為非 rowid 的 WHERE**：把查詢改成 `SELECT x FROM t WHERE x='b'`（`x` 不是 primary key，沒有 index）。`EXPLAIN` 看 bytecode——你會看到 `Rewind`/`Next` 取代了 `SeekRowid`（本課真跑過：多了一個 `Column`→`Ne`→`Next` 的全表掃描迴圈）。追這個迴圈：`OP_Rewind`（vdbe.c:6303）和 `OP_Next`（vdbe.c:6415）各呼叫哪個 B-tree function？對比「有 rowid 直接 seek」vs「無 index 全表掃」在 code 上的差別。

2. **加一個 index 再看**：`CREATE INDEX ix ON t(x)` 之後再 `EXPLAIN SELECT x FROM t WHERE x='b'`。bytecode 會多開一個 cursor 走 index B-tree。追它——這就是「query planner（`where.c`）決定用 index」在 bytecode 上的體現。

3. **用 gdb 真的中斷**（能 build 的話）：在 `sqlite3OsRead` 或 `readDbPage` 下中斷點，跑一條查詢，`bt` 看真實 call stack。對照你手追的 chain——`reading_code` Ch 18 debugger-driven reading 的實戰。你手推的和 gdb 印的一致嗎？

4. **追寫入路徑**：把 `SELECT` 換成 `INSERT INTO t VALUES(4,'d')`。`EXPLAIN` 看它的 bytecode（會有 `OP_MakeRecord`、`OP_Insert`），追 `OP_Insert` 一路到 pager 怎麼把 dirty page 寫回、journal 怎麼備份。這條線帶你進 Ch 10 的交易那半。

## 自我檢核

- [ ] 我能不看解答，畫出從 `sqlite3_prepare_v2` 到 `pread` 的 call chain，至少 8 站、每站標檔名
- [ ] 我能解釋 prepare 和 step 各負責哪一半（編譯 vs 執行），入口在哪個檔
- [ ] 我真跑（EXPLAIN 或 bytecode()）看過這條 SQL 的 opcode，並能逐條說明前 6 個 opcode 幹嘛
- [ ] 我能指出至少三個「跨層接縫」：VDBE→B-tree（`sqlite3BtreeTableMoveto`）、B-tree→pager（`sqlite3PagerGet`）、pager→VFS（`sqlite3OsRead`→`xRead`）
- [ ] 我能解釋 `(pgno-1)*pageSize` 這行乘法的意義，以及 `xRead` 為什麼是函式指標
- [ ] 我通過了費曼測試：能對空氣完整講一遍這條 SQL 從文字到磁碟的一生

追完這條路徑，Part 2 就結業了。你不只讀懂了 SQLite 的每一層，還親手把它們串成一條可複述的脊椎——這正是本課要練的「一眼認出 + 能追到底」的功力。下一站 Part 3，換一個完全不同形狀的硬目標：nginx 的 event-driven 高並發架構。同樣的攻堅 SOP，全新的 pattern（reactor / memory pool / plugin pipeline）等你認出。

→ [Ch 13 nginx 偵察：master/worker 與模組化](./13-nginx-recon.md)
