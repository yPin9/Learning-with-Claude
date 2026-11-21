# 練習 A — 冷讀一段陌生的 user-space 程式碼

> **目標**：把 Ch 1–7 的心法拼起來，在**有時間限制**的情況下冷讀 Redis 7.2.17 的 `APPEND` / `SETRANGE` / `GETRANGE` 與底下的 sds 擴容策略，交出一份能回答指定問題的讀碼筆記，並用實際執行的 `redis-cli` 與 gdb 驗證筆記裡的每一個假說。完成後你驗證了自己能：診斷卡在哪一道鴻溝、用問題決定讀什麼、從入口點和資料結構切入、刻意不讀某些東西，以及把理解寫成別人看得懂的筆記。
> **鴻溝**：本練習主要練**目標鴻溝**（帶著問題讀）與**慣用法鴻溝**（sds 的 header 技巧），順帶碰一點**領域鴻溝**（記憶體配置器的 size class）。

> **環境**：Redis 7.2.17（預設 `make`，配置器為 jemalloc 5.3.0）、gdb 12、WSL Ubuntu 22.04 x86_64。所有輸出在這個環境實測。換成 `MALLOC=libc` 建置時，下面跟配置大小有關的數字會不同（見「如果你卡住了」第 4 條）。

## 背景與動機

你剛加入一個用 Redis 存大量字串的團隊。監控顯示某些 key 的記憶體用量比字串長度大了 50% 以上，有人懷疑是 `APPEND` 造成的。主管丟給你一句話：「去讀一下 Redis 的 APPEND 是怎麼長大的，下午跟我說是不是它。」

這是讀碼最真實的情境：**有一個具體問題、有時間限制、沒有人帶你**。你不可能讀完 Redis 的十幾萬行，也不需要。這個練習模擬的就是這一個下午。

> 開始之前，如果你還沒讀過 [Ch 1 — 為什麼你看不懂](./01-why-you-cant-read-it.md) 的五道鴻溝、[Ch 3 — 帶著問題讀](./03-question-driven-reading.md) 的假說驗證迴圈、[Ch 5 — 從哪裡開始讀](./05-entry-points-and-data-first.md) 的入口點與資料優先，先回去看。這個練習假設你會用那幾章的語言。

## 任務規格

### 範圍

| 項目 | 規定 |
|---|---|
| 主要檔案 | `src/t_string.c`、`src/sds.c`、`src/sds.h` |
| 可以查但不要深讀 | `src/object.c`、`src/db.c`、`src/server.c`、`src/debug.c` |
| 明確不讀 | `zmalloc.c` 內部、jemalloc 原始碼、網路層（`networking.c` 的協定解析）、replication、AOF |
| 時間上限 | **3 小時**（含建置與實驗）。超過就停，把沒解開的寫進「未解問題」 |
| 版本 | 只看 7.2.17；不要拿網路上其他版本的文章當答案 |

「明確不讀」那一列是故意的。[Ch 4 — 選擇性閱讀](./04-selective-reading.md) 講過：知道什麼東西可以當黑箱，和知道什麼東西要細讀一樣重要。這個練習會在驗收時檢查你有沒有把時間花在不該花的地方。

### 必答問題

你的筆記必須回答以下 8 題，每題都要附**證據**：程式碼位置（`檔案:行號`）或實驗輸出，最好兩者都有。

1. **入口**：從 Redis 收到 `APPEND key value` 到執行 `appendCommand()`，命令是怎麼被分派過去的？（只要找到分派點和對應表，不用讀網路層）
2. **編碼轉換**：對一個 `OBJECT ENCODING` 為 `int` 的 key 做 `APPEND`，之後編碼變成什麼？為什麼？對 `embstr` 呢？
3. **擴容策略**：`APPEND` 空間不夠時，新的配置大小怎麼算？小字串和大字串的規則有什麼不同？分界點是多少、定義在哪？
4. **header 型別**：sds 有幾種 header？誰決定用哪一種？擴容時 header 型別改變會發生什麼事（和型別不變時有什麼不同）？
5. **「多出來的空間」從哪來**：用 `SETRANGE` 在一個不存在的 key 上建一個 2 MB 字串，`DEBUG SDSLEN` 顯示 `val_sds_avail` 不是 0。這段空間不是 `sdsMakeRoomFor` 的貪婪策略給的，那是誰給的？
6. **上限檢查**：`SETRANGE` 和 `APPEND` 怎麼防止字串超過上限？上限是多少、可不可以調？溢位怎麼防？
7. **邊界行為**：`SETRANGE nokey 5 ""` 回傳什麼？會不會建立 key？`GETRANGE key -1 -5` 回傳什麼？依據哪幾行程式碼？
8. **回答主管**：`APPEND` 會不會讓記憶體用量比字串長度大 50% 以上？在什麼條件下會、上限大概多少？

### 交付格式

一份 Markdown 筆記，結構如下（沿用 [Ch 7 — 讀碼筆記與畫圖](./07-reading-notes-and-diagrams.md) 的外部化原則：寫給三天後的自己）：

```markdown
# 讀碼筆記：Redis 7.2.17 APPEND/SETRANGE 與 sds 擴容

## 0. 問題清單與鴻溝診斷
- 我的問題：...
- 開讀前預估會卡在哪道鴻溝：...
- 讀完後實際卡在哪道鴻溝：...

## 1. 入口點與呼叫鏈
（ASCII 呼叫圖，每個節點標 檔案:行號）

## 2. 資料結構
（sdshdr 的記憶體配置圖；robj 的 encoding 與 ptr 的關係）

## 3. 假說與驗證紀錄
| # | 假說 | 依據（程式碼） | 驗證方法 | 結果 |

## 4. 八題的答案
（每題：結論一句 + 證據）

## 5. 黑箱清單
（我刻意沒讀的東西，以及我假設它做什麼）

## 6. 未解問題
```

### 驗收標準

| 項目 | 通過條件 |
|---|---|
| 正確性 | 8 題答案與參考筆記的結論一致（措辭可以不同） |
| 證據 | 每題至少一個 `檔案:行號`；第 2、3、4、5、7 題至少一個你自己跑出來的實驗輸出 |
| 假說紀錄 | 至少 5 條假說，其中**至少 1 條被實驗推翻**（沒有推翻過任何假說，代表你沒有真的先猜再驗） |
| 黑箱清單 | 至少列出 3 個刻意沒讀的函式或模組，並寫出你對它的假設 |
| 時間 | 3 小時內完成；超時的部分移到「未解問題」也算通過 |
| 範圍紀律 | 筆記裡沒有出現對 jemalloc 內部演算法的長篇分析 |

## 環境準備

不要在 `~/code_reading/redis-7.2.17/` 原地建置（保持語料庫乾淨），複製一份到 scratch：

```bash
mkdir -p ~/code_reading/scratch/pa && cd ~/code_reading/scratch/pa
cp -r ~/code_reading/redis-7.2.17 redis
cd redis && make -j8 > build.log 2>&1; tail -2 build.log
src/redis-server --version
```

```
Redis server v=7.2.17 sha=83937c51:0 malloc=jemalloc-5.3.0 bits=64 build=f5e94f26e1cee235
```

啟動一個專用的 server。**`--enable-debug-command yes` 是必要的**：7.x 預設關閉 `DEBUG` 命令（`config.c:3162` 的 `enable-debug-command`，預設值 `PROTECTED_ACTION_ALLOWED_NO`），而本練習最重要的觀察工具就是 `DEBUG SDSLEN`。

```bash
src/redis-server --port 7399 --save "" --appendonly no \
    --enable-debug-command yes --daemonize yes --logfile /tmp/pa-redis.log
alias rc='src/redis-cli -p 7399'
```

做完練習記得 `rc SHUTDOWN NOSAVE`。

## 期望輸出範例

你的筆記第 3 節應該長得像這樣（這是一條**被推翻**的假說，示範格式）：

```
| # | 假說 | 依據 | 驗證方法 | 結果 |
|---|------|------|----------|------|
| H3 | 對 6 byte 的字串 APPEND 1 byte，貪婪策略會配置 (6+1)*2=14 byte 的 buf | sds.c:263 newlen*=2 | DEBUG SDSLEN 看 val_sds_avail | 推翻。avail=6 而非 7。newlen 是 len+addlen=6，不是 7；見下 |
```

（你會在實驗 3 看到為什麼。）

邊界輸入的期望行為：

```
輸入：SETRANGE nokey 5 ""
輸出：(integer) 0，且 EXISTS nokey 回傳 0
```

```
輸入：SETRANGE big 536870912 x
輸出：ERR string exceeds maximum allowed size (proto-max-bulk-len)
```

## 如果你卡住了

1. **找不到 `appendCommand` 是誰呼叫的**：不要從網路層往下追。反過來，`grep -n appendCommand src/*.c src/*.def`，找「它被放進哪張表」。函式指標分派表的讀法在 [Ch 10](./10-pointer-idioms.md) 的「分派表」一節。
2. **`sdslen(s)` 看不懂，`s[-1]` 是什麼鬼**：sds 的指標指向字串本體，header 在指標**前面**。先畫出 `struct sdshdr8` 的記憶體配置，標出 `s` 指在哪裡，`s[-1]` 就是 `flags`。這是本練習最大的慣用法鴻溝。
3. **`val_sds_avail` 的數字和你算的對不上**：檢查兩件事——(a) 你算的 `newlen` 是不是已經包含舊長度；(b) 有沒有忘記 malloc 實際給的大小（`usable`）可能比要求的大。`_sdsMakeRoomFor` 最後用的是 `usable`，不是 `newlen`。
4. **數字和本練習的輸出不同**：先確認 `redis-server --version` 印的是 `malloc=jemalloc-5.3.0`。用 `MALLOC=libc` 建置時，`usable` 來自 glibc 的 `malloc_usable_size()`，size class 不同，所有 `val_zmalloc` 和 `avail` 都會變。這不代表你的理解錯了，只代表黑箱換了。
5. **時間快到了還有三題沒答**：回到問題清單，挑「答了能直接回答主管」的那題先做（第 8 題依賴第 3、5 題）。剩下的寫進未解問題。Ch 4 說的「接受只懂 80%」就是這個時候用的。

## 實作步驟建議

### Step 1：鴻溝診斷與問題清單（15 分鐘）

打開 `src/sds.c`，隨便捲到 `_sdsMakeRoomFor`，**不要讀懂它**，只花五分鐘看。然後在筆記第 0 節寫下：你覺得自己會卡在哪幾道鴻溝？例如：

- 慣用法：`s[-1] & SDS_TYPE_MASK`、`SDS_HDR(8,s)` 這種巨集看不懂
- 領域：不知道 jemalloc 會給比要求更多的空間
- 目標：如果沒有這 8 題，我會從 `sds.c` 第一行開始讀

把必答 8 題抄進筆記，每題先寫一句**你現在的猜測**。這些猜測就是第 3 節的假說來源。

### Step 2：找入口點（20 分鐘）

目標：畫出 `call()` → `appendCommand()` → `sdscatlen()` → `_sdsMakeRoomFor()` 這條鏈，每個節點標行號。

- 從命令表找到 `appendCommand` 對應的條目（`commands.def`）
- 找到 `c->cmd->proc(c)`（`server.c`）
- 讀 `appendCommand()` 本體（`t_string.c:697`），只讀到「它呼叫了哪些函式」的程度

### Step 3：資料結構優先（30 分鐘）

目標：讀懂 `sds.h` 前 100 行，畫出五種 header 的記憶體配置。

[Ch 5](./05-entry-points-and-data-first.md) 的原則：**先讀資料結構，程式碼就讀懂一半**。`_sdsMakeRoomFor` 裡一半的程式碼在處理 header 型別，不懂 header 就讀不懂它。這一步要回答：

- `typedef char *sds;` 為什麼可以直接 `printf("%s")`？
- `sdshdr5` 和其他四種有什麼根本差異？
- `sdsReqType()` 和 `sdsTypeMaxSize()` 各回答什麼問題？

### Step 4：選擇性細讀（45 分鐘）

目標：細讀這五個函式，其他一律黑箱：

| 函式 | 位置 | 為什麼要讀 |
|---|---|---|
| `appendCommand` | `t_string.c:697` | 第 1、2、6 題 |
| `setrangeCommand` | `t_string.c:442` | 第 5、6、7 題 |
| `getrangeCommand` | `t_string.c:502` | 第 7 題 |
| `checkStringLength` | `t_string.c:40` | 第 6 題 |
| `_sdsMakeRoomFor` | `sds.c:247` | 第 3、4、5 題 |

讀到 `dbUnshareStringValue()`、`tryObjectEncoding()`、`s_realloc_usable()` 時，**先猜它做什麼、寫進黑箱清單，繼續往下讀**。只有在某題的答案取決於它時才跳進去（第 2 題需要看 `dbUnshareStringValue`，十行而已）。

### Step 5：實驗驗證（50 分鐘）

目標：把每條假說變成一個 `redis-cli` 實驗，先寫預測、再執行。下一節「驗證你的理解」列了 9 組實驗，**先自己設計，卡住了再看**。

### Step 6：寫結論（20 分鐘）

回答第 8 題。這一題沒有現成的程式碼行可以抄，要把第 3、5 題的結論組合起來，並給出一個有條件的數字。

## 驗證你的理解：實驗組

每組實驗都**先在筆記裡寫下預測**，再執行。下面的輸出全部是在本環境實測的結果。

### 實驗 1：`int` 編碼遇到 APPEND

```
$ rc SET n 12345
OK
$ rc OBJECT ENCODING n
int
$ rc APPEND n 6
(integer) 6
$ rc OBJECT ENCODING n
raw
$ rc GET n
123456
```

對照程式碼：`appendCommand()` 在 key 存在時呼叫 `dbUnshareStringValue()`：

```c
/* redis-7.2.17/src/db.c:434 */
robj *dbUnshareStringValue(redisDb *db, robj *key, robj *o) {
    serverAssert(o->type == OBJ_STRING);
    if (o->refcount != 1 || o->encoding != OBJ_ENCODING_RAW) {
        robj *decoded = getDecodedObject(o);
        o = createRawStringObject(decoded->ptr, sdslen(decoded->ptr));
        decrRefCount(decoded);
        dbReplaceValue(db,key,o);
    }
    return o;
}
```

條件是「被共享 **或** 不是 raw」，兩者任一成立就複製一份 raw。`int` 和 `embstr` 都不是 raw，所以 APPEND 之後一定是 raw。

另一個值得驗證的分支：key **不存在**時，APPEND 走的是另一條路，會呼叫 `tryObjectEncoding()`：

```
$ rc APPEND k1 123
(integer) 3
$ rc OBJECT ENCODING k1
int
$ rc APPEND k2 hello
(integer) 5
$ rc OBJECT ENCODING k2
embstr
$ rc APPEND k2 x
(integer) 6
$ rc OBJECT ENCODING k2
raw
```

### 實驗 2：第一次 APPEND 的貪婪配置

```
$ rc SET s hello
OK
$ rc OBJECT ENCODING s
embstr
$ rc DEBUG SDSLEN s
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:5, val_sds_avail:0, val_zmalloc: 16
$ rc APPEND s x
(integer) 6
$ rc DEBUG SDSLEN s
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:6, val_sds_avail:6, val_zmalloc: 16
$ rc APPEND s yy
(integer) 8
$ rc DEBUG SDSLEN s
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:8, val_sds_avail:4, val_zmalloc: 16
```

這是「期望輸出範例」裡那條被推翻的假說的來源。關鍵在 `appendCommand` 的順序：先 `dbUnshareStringValue()` 把 embstr 複製成一個**剛好 5 byte** 的 raw sds，然後 `sdscatlen(o->ptr, "x", 1)` 才呼叫 `sdsMakeRoomFor(s, 1)`。所以 `newlen = len + addlen = 5 + 1 = 6`，乘二得 12。type 8 的 header 3 byte，加結尾 `'\0'`，要求 `3 + 12 + 1 = 16` byte，jemalloc 剛好給 16，`usable = 16 - 3 - 1 = 12`，`avail = 12 - 6 = 6`。

第二次 `APPEND s yy` 只要 2 byte，`avail 6 >= 2`，`_sdsMakeRoomFor` 第一行就 `return s`，配置大小不變。

### 實驗 3：header 型別從 8 換成 16

```
$ rc SET a <126 個 a>
OK
$ rc APPEND a x
(integer) 127
$ rc DEBUG SDSLEN a
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:127, val_sds_avail:29, val_zmalloc: 160
$ rc APPEND a <128 個 b>
(integer) 255
$ rc DEBUG SDSLEN a
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:255, val_sds_avail:379, val_zmalloc: 640
```

（`<126 個 a>` 在 shell 裡用 `"$(printf 'a%.0s' $(seq 1 126))"` 產生。）

第一步：126 byte 的 SET 建立 raw 字串（超過 embstr 上限 44，`object.c:122`），要求 `3 + 126 + 1 = 130`，jemalloc 的 size class 給 160，avail 30。APPEND 1 byte 不需擴容，avail 變 29。

第二步：要 128 byte，avail 只有 29。`newlen = 127 + 128 = 255`，乘二 510。`sdsReqType(510)` 回傳 `SDS_TYPE_16`（因為 510 ≥ 256），和舊的 type 8 不同，走 `_sdsMakeRoomFor` 的 `else` 分支：**不能 realloc**（header 變大了，字串本體要往後挪），改成 malloc 新的、memcpy、free 舊的。要求 `5 + 510 + 1 = 516`，size class 給 640，`usable = 640 - 5 - 1 = 634`，`avail = 634 - 255 = 379`。

### 實驗 4：用 gdb 親眼看 header 換型別

`ptrace_scope` 是 1 時不能 attach 到已在跑的 server（實測 `gdb -p` 會回 `ptrace: Inappropriate ioctl for device`），改成讓 gdb 自己啟動 server。條件中斷點避免被其他 `sdsMakeRoomFor` 呼叫干擾：

```bash
cat > /tmp/pa.gdb <<'G'
set pagination off
break _sdsMakeRoomFor if addlen == 200
run
bt 5
p ((struct sdshdr8 *)(s - 3))->len
p ((struct sdshdr8 *)(s - 3))->alloc
finish
p ((struct sdshdr16 *)($ - 5))->len
p ((struct sdshdr16 *)($$ - 5))->alloc
p/x ((unsigned char *)$$2)[-1]
kill
quit
G
gdb -q -batch -x /tmp/pa.gdb --args src/redis-server --port 7398 --save "" --appendonly no
# 另一個終端機：
#   src/redis-cli -p 7398 SET c <100 個 a>
#   src/redis-cli -p 7398 APPEND c <200 個 Q>
```

輸出（去掉執行緒訊息）：

```
Thread 1 "redis-server" hit Breakpoint 1, _sdsMakeRoomFor (s=0x7ffff7832073 'a' <repeats 100 times>, addlen=200, greedy=1) at .../src/sds.c:247
#0  _sdsMakeRoomFor (s=0x7ffff7832073 'a' <repeats 100 times>, addlen=200, greedy=1) at .../src/sds.c:247
#1  0x00005555555f90b1 in sdsMakeRoomFor (addlen=200, s=<optimized out>) at .../src/sds.c:303
#2  sdscatlen (s=<optimized out>, t=0x7ffff78500e3, len=200) at .../src/sds.c:496
#3  0x0000555555640b54 in appendCommand (c=0x7ffff7921700) at .../src/t_string.c:720
#4  0x00005555555ecd35 in call (c=c@entry=0x7ffff7921700, flags=flags@entry=3) at .../src/server.c:3527
$1 = 100 'd'
$2 = 108 'l'
Value returned is $3 = 0x7ffff793f005 'a' <repeats 100 times>
$4 = 100
$5 = 634
$6 = 0x2
```

讀這段輸出：

- backtrace 就是第 1 題的答案：`call()`（`server.c:3527`）→ `appendCommand`（`t_string.c:720`）→ `sdscatlen` → `sdsMakeRoomFor` → `_sdsMakeRoomFor`。
- 進入時是 type 8：`len=100`、`alloc=108`（gdb 把 `uint8_t` 當字元印，`'d'` 是 100、`'l'` 是 108）。
- 回傳的指標從 `...2073` 變成 `...f005`：**位址變了**，這就是 Ch 10 說的「sds 會搬家，一定要接回傳值」。
- `s[-1]` 是 `0x2`，也就是 `SDS_TYPE_16`；`alloc=634` 和實驗 3 用相同推導算出的值一致。

### 實驗 5：超過 1 MB 改成每次加 1 MB

```
$ rc SETRANGE m 2097151 x
(integer) 2097152
$ rc DEBUG SDSLEN m
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:2097152, val_sds_avail:524278, val_zmalloc: 2621440
$ head -c 600000 /dev/zero | tr '\0' z | rc -x APPEND m
(integer) 2697153
$ rc DEBUG SDSLEN m
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:2697153, val_sds_avail:1497141, val_zmalloc: 4194304
```

（中間省略了一次 `APPEND m y`，讓長度變成 2097153。`redis-cli -x` 從 stdin 讀最後一個參數，因為 Linux 對單一命令列參數的長度有上限，600 KB 塞不進命令列。）

推導：`newlen = 2097153 + 600000 = 2697153`，已超過 `SDS_MAX_PREALLOC`（`sds.h:36`，`1024*1024`），所以走 `newlen += SDS_MAX_PREALLOC`，得 3745729。type 32 header 9 byte，型別不變走 realloc，要求 3745739，jemalloc 的下一個 size class 是 4 MB（4194304），`usable - 10 = 4194294`，`avail = 4194294 - 2697153 = 1497141`。

**同時注意第一個 `DEBUG SDSLEN`**：剛用 `SETRANGE` 建立的 2 MB 字串就已經有 524278 byte 的 avail。這是第 5 題。

### 實驗 6：type 8 的上限截斷

```
$ rc SETRANGE c 99 x
(integer) 100
$ rc DEBUG SDSLEN c
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:100, val_sds_avail:8, val_zmalloc: 112
$ rc APPEND c abcdefghijklmnopqrstuvwxyz0
(integer) 127
$ rc DEBUG SDSLEN c
key_sds_len:1, key_sds_avail:0, key_zmalloc: 8, val_sds_len:127, val_sds_avail:128, val_zmalloc: 320
```

`newlen = 127 * 2 = 254`，仍然 < 256，type 8。要求 `3 + 254 + 1 = 258`，size class 給 320，`usable = 316`。但 type 8 的 `alloc` 欄位是 `uint8_t`，最多記到 255，所以 `sds.c:294` 的 `if (usable > sdsTypeMaxSize(type)) usable = sdsTypeMaxSize(type);` 把它截成 255，`avail = 255 - 127 = 128`。配置器給了 320 byte，sds 只「知道」其中 3 + 255 + 1 = 259 byte，剩下 61 byte 是 sds 記不住的空間。

### 實驗 7：SETRANGE 的邊界

```
$ rc SETRANGE nokey 5 ""
(integer) 0
$ rc EXISTS nokey
(integer) 0
$ rc SETRANGE pad 5 "ab"
(integer) 7
$ rc GET pad
     ab
$ rc SETRANGE s -1 x
ERR offset is out of range
$ rc SETRANGE big 536870911 x
(integer) 536870912
$ rc SETRANGE big 536870912 x
ERR string exceeds maximum allowed size (proto-max-bulk-len)
$ rc SET q 1
OK
$ rc SETRANGE q 0 ""
(integer) 1
$ rc LPUSH lst a
(integer) 1
$ rc SETRANGE lst 0 x
WRONGTYPE Operation against a key holding the wrong kind of value
```

`GET pad` 前面那五個「空白」其實是 `\0`（`sdsgrowzero` 用 `memset(..., 0, ...)` 補零，`sds.c:483`），終端機把它們顯示成空白。

上限可以調：

```
$ rc CONFIG SET proto-max-bulk-len 1048576
OK
$ rc APPEND m z
ERR string exceeds maximum allowed size (proto-max-bulk-len)
$ rc CONFIG SET proto-max-bulk-len 536870912
OK
```

`proto-max-bulk-len` 的預設值是 `512ll*1024*1024`（`config.c:3227`），最小值 `1024*1024`，可在執行期修改。注意：`SETRANGE big 536870911 x` 會讓 server 吃掉 640 MB（見實驗 9 的 `DEBUG SDSLEN`），做完記得 `DEL big`。

### 實驗 8：GETRANGE 的負索引

```
$ rc SET g "Hello World"
$ rc GETRANGE g 0 4
Hello
$ rc GETRANGE g -5 -1
World
$ rc GETRANGE g -1 -5

$ rc GETRANGE g 5 100
 World
$ rc GETRANGE g 100 200

$ rc GETRANGE g -100 2
Hel
$ rc SET i 98765
$ rc GETRANGE i 1 2
87
$ rc OBJECT ENCODING i
int
```

最後兩行證明 GETRANGE 在 `int` 編碼上**不會**改變編碼：`getrangeCommand()` 用 `ll2string` 把整數轉成堆疊上的 `llbuf` 再切，唯讀命令不需要 unshare（`t_string.c:515`）。

### 實驗 9：剛建立的大字串也有 avail

```
$ rc SETRANGE big 536870911 x
(integer) 536870912
$ rc DEBUG SDSLEN big
key_sds_len:3, key_sds_avail:0, key_zmalloc: 8, val_sds_len:536870912, val_sds_avail:134217718, val_zmalloc: 671088640
```

這是第 5 題的關鍵實驗。`setrangeCommand()` 對不存在的 key 呼叫 `sdsnewlen(NULL, offset+sdslen(value))`，要求剛好 512 MB + header；**沒有經過貪婪策略**（隨後的 `sdsgrowzero` 發現長度已經夠，直接 return）。但 jemalloc 對這個大小的 size class 是 640 MB，`_sdsnewlen` 把 `usable` 記進 `alloc`（`sds.c:124–135`），於是多出 128 MB 的 avail。

## 完整參考筆記

**寫完再看！不要偷看**，否則你練到的是「讀別人的筆記」，不是讀碼。

<details>
<summary>點開參考筆記</summary>

```markdown
# 讀碼筆記：Redis 7.2.17 APPEND/SETRANGE 與 sds 擴容

## 0. 問題清單與鴻溝診斷
- 問題：題目給的 8 題。
- 開讀前預估：慣用法（sds header 巨集）、領域（配置器行為）。
- 實際：慣用法鴻溝最大——`s[-1]`、`SDS_HDR(T,s)` 花了最多時間；
  領域鴻溝在實驗 9 才出現（以為 avail 一定來自貪婪策略）；
  目標鴻溝沒出現，因為有 8 題撐著。

## 1. 入口點與呼叫鏈

  commands.def:10870  {MAKE_CMD("append", ..., appendCommand, 3, ...)}
          │  （啟動時把表載入命令 dict；查表細節黑箱）
          ▼
  server.c:3527  call(): c->cmd->proc(c)        ← 間接呼叫，靜態追蹤在此斷掉
          ▼
  t_string.c:697 appendCommand()
     ├─ key 不存在：tryObjectEncoding(argv[2]) → dbAdd        （可能是 int/embstr）
     └─ key 存在：
          checkType → checkStringLength (t_string.c:40)
          dbUnshareStringValue (db.c:434)   ← 非 raw 或被共享就複製成 raw
          sdscatlen (sds.c:493)
               └─ sdsMakeRoomFor (sds.c:302) → _sdsMakeRoomFor(s, addlen, greedy=1) (sds.c:247)

  t_string.c:442 setrangeCommand()
     ├─ key 不存在：value 為空 → 回 0；否則 sdsnewlen(NULL, offset+len)
     └─ key 存在：value 為空 → 回原長度；否則 dbUnshareStringValue
     └─ sdsgrowzero (sds.c:475) → sdsMakeRoomFor（只在長度不足時）→ memcpy

## 2. 資料結構

  sds 指標指向 buf，header 在前面：

     sdshdr8:   [len:1][alloc:1][flags:1][ b u f ... '\0' ]
                                   ▲      ▲
                                 s[-1]    s

  flags 低 3 bit = 型別（SDS_TYPE_MASK 7）。
  五種：TYPE_5（無 len/alloc，長度藏在 flags 高 5 bit，無法記錄 avail）、
        8 / 16 / 32 / 64（len 與 alloc 的寬度不同）。
  sdsReqType(n)：n 需要哪種 header 才裝得下（sds.c:61）。
  sdsTypeMaxSize(t)：該型別 alloc 最多能記多大（sds.c:77）。

  robj（server.h）：type=OBJ_STRING 時，encoding 可能是
    INT（ptr 直接存 long）、EMBSTR（robj 和 sds 一次配置、相鄰，長度 ≤ 44）、
    RAW（ptr 指向獨立配置的 sds）。

## 3. 假說與驗證紀錄

| # | 假說 | 依據 | 驗證 | 結果 |
|---|------|------|------|------|
| H1 | APPEND 透過命令表的函式指標呼叫 | server.c:3527 | gdb bt（實驗 4） | 成立 |
| H2 | int 編碼 APPEND 後變 raw | db.c:436 條件 | 實驗 1 | 成立 |
| H3 | 6+1 byte 會配置 14 byte buf | sds.c:263 | 實驗 2 | 推翻：dbUnshare 先建 5 byte sds，newlen=6，×2=12，avail=6 |
| H4 | 跨過 256 會換 header 並搬家 | sds.c:278-292 | 實驗 3、4 | 成立，指標位址改變 |
| H5 | ≥1MB 時每次多給 1MB | sds.c:263-266 | 實驗 5 | 成立，但 avail 還要再加 size class 的零頭 |
| H6 | 剛建立的字串 avail 為 0 | 沒經過貪婪策略 | 實驗 5、9 | 推翻：jemalloc 的 usable 被記進 alloc |
| H7 | type 8 最多記 255 byte 的 alloc | sdsTypeMaxSize | 實驗 6 | 成立，配置器多給的 61 byte 被丟棄 |
| H8 | SETRANGE 在原長度內改寫不會呼叫 MakeRoomFor | sdsgrowzero 第一行 | DEBUG SDSLEN（SET k3 hello; SETRANGE k3 0 J → val_zmalloc 8, avail 0） | 成立；而且 unshare 出來的 5 byte sds 是 TYPE_5 |

## 4. 八題的答案

1. 分派：commands.def 的 MAKE_CMD 條目把 "append" 對應到 appendCommand；
   call() 在 server.c:3527 以 c->cmd->proc(c) 間接呼叫。gdb backtrace 佐證。
2. int / embstr 經 dbUnshareStringValue（db.c:434）一律複製成 raw，
   因為條件是 refcount != 1 || encoding != RAW。只有 raw 且未被共享才原地改。
3. newlen = len + addlen；若 newlen < SDS_MAX_PREALLOC（1MB，sds.h:36）則 ×2，
   否則 +1MB（sds.c:262-267）。最後實際可用空間 = 配置器的 usable − header − 1，
   並以該型別上限截斷（sds.c:292-296）。
4. 五種 header。sdsReqType(newlen) 決定；擴容時 TYPE_5 一律升成 TYPE_8
   （TYPE_5 記不住 avail）。型別不變 → realloc；型別改變 → malloc 新的、
   memcpy、free 舊的、寫新 flags 與 len（sds.c:278-292），指標必然改變。
5. 來自配置器。_sdsnewlen 用 s_malloc_usable 取得實際大小並記入 alloc
   （sds.c:124-135）。jemalloc 的 size class 讓 2MB+10 落在 2.5MB、512MB+10 落在 640MB。
6. checkStringLength（t_string.c:40）：total = (uint64_t)size + append，
   檢查 > proto_max_bulk_len 或溢位（total < size || total < append）。
   預設 512MB（config.c:3227），可 CONFIG SET。SETRANGE 另外拒絕負 offset。
7. SETRANGE nokey 5 "" → 0，不建 key（t_string.c:456-459）。
   GETRANGE g -1 -5 → 空字串：start、end 皆負且 start > end（t_string.c:524-527）。
8. 會。小於 1MB 時，剛擴容完的字串最多約有一倍的閒置空間（newlen ×2），
   之後持續 APPEND 會消耗它；超過 1MB 後閒置空間上限約 1MB 加上配置器零頭。
   另外即使從未 APPEND，配置器的 size class 本身就可能多給最多約 25%
   （實驗 9：512MB 字串配到 640MB）。所以「比長度大 50% 以上」在
   「最近一次 APPEND 剛觸發擴容、字串 < 1MB」時最容易出現。
   要確認，可對可疑 key 跑 DEBUG SDSLEN 或 MEMORY USAGE。

## 5. 黑箱清單
- s_malloc_usable / s_realloc_usable：假設回傳配置器實際給的大小（zmalloc 包裝）。
- tryObjectEncoding：假設「能轉 int 就轉 int，≤44 就轉 embstr」。實驗 1 佐證。
- 命令表載入成 dict 的過程（populateCommandTable 一類）：假設啟動時建好、查名稱得到 redisCommand。
- jemalloc size class 的計算方式：只用觀察值（112、160、320、640、2.5MB、4MB、640MB）。

## 6. 未解問題
- 為什麼 SDS_MAX_PREALLOC 選 1MB？（脈絡鴻溝；要去翻 git log / issue，超出 3 小時）
- 有沒有任何路徑會把 raw 字串的閒置空間收回？（猜：sdsRemoveFreeSpace，還沒查呼叫點）
```

**參考筆記說明**：

- 第 3 節的 H3 和 H6 是**設計好會被推翻的**。H3 推翻的原因是「讀程式碼時跳過了 `dbUnshareStringValue`」，H6 推翻的原因是領域鴻溝（不知道配置器會多給）。如果你兩條都猜對了，回頭看看是不是先看了實驗再寫假說。
- 第 8 題的答案刻意寫成「有條件的數字」。讀碼得出的結論幾乎都應該是這種形式——「會，在 X 條件下，上限約 Y」——而不是「會」或「不會」。
- 黑箱清單裡的每一條都寫了「假設它做什麼」。黑箱不是「沒讀」，是「讀了它的介面、信任它的行為、在需要時可以回來驗證」。

</details>

## 測試用例：預測再驗證

拿這張表當自我測驗：先蓋住「實測結果」那欄，自己預測，再對答案（全部在本環境實測）。

| 指令序列 | 實測結果 | 考的是 |
|---|---|---|
| `SET n 12345` → `APPEND n 6` → `OBJECT ENCODING n` | `raw` | 第 2 題 |
| `APPEND k1 123`（k1 不存在）→ `OBJECT ENCODING k1` | `int` | key 不存在的分支 |
| `SET s hello` → `APPEND s x` → `DEBUG SDSLEN s` | `val_sds_len:6, val_sds_avail:6` | 第 3 題、H3 |
| 126 byte 的 a → `APPEND` 1 byte → `APPEND` 128 byte | `val_sds_len:255, val_sds_avail:379, val_zmalloc: 640` | 第 4 題 |
| `SETRANGE c 99 x` → `APPEND c` 27 byte | `val_sds_avail:128, val_zmalloc: 320` | 型別上限截斷 |
| `SETRANGE m 2097151 x` → `DEBUG SDSLEN m` | `val_sds_avail:524278` | 第 5 題 |
| `SET k3 hello` → `SETRANGE k3 0 J` → `DEBUG SDSLEN k3` | `val_sds_avail:0, val_zmalloc: 8` | 長度內改寫不擴容 |
| `SETRANGE nokey 5 ""` → `EXISTS nokey` | `0`、`0` | 第 7 題 |
| `SETRANGE s -1 x` | `ERR offset is out of range` | 非法輸入 |
| `SETRANGE big 536870912 x` | `ERR string exceeds maximum allowed size (proto-max-bulk-len)` | 第 6 題 |
| `LPUSH lst a` → `APPEND lst x` | `WRONGTYPE ...` | `checkType` |
| `GETRANGE g -1 -5`（g = "Hello World"） | 空字串 | 第 7 題 |
| `GETRANGE g -100 2` | `Hel` | 負索引夾到 0 |

## 延伸挑戰（加分）

### 挑戰一：同樣的流程，換 Lua 5.4.9 的字串 interning

對象：`lua-5.4.9/lstring.c`（274 行）。必答問題：

1. 什麼字串會被 intern？長度門檻是多少、定義在哪？
2. intern 用的 hash table 在哪裡、滿了怎麼辦？
3. 長字串和短字串比較相等時，成本有什麼不同？

建置方法（這台環境沒有 readline 開發檔，要關掉 `LUA_USE_READLINE`）：

```bash
cp -r ~/code_reading/lua-5.4.9 ~/code_reading/scratch/pa/lua && cd ~/code_reading/scratch/pa/lua
make -j8 CFLAGS="-Wall -O2 -std=c99 -DLUA_USE_LINUX" MYLIBS="" LIBS="-lm -ldl"
./lua -v
```

```
Lua 5.4.9  Copyright (C) 1994-2026 Lua.org, PUC-Rio
```

一個驗證入口：Lua 5.4 的 `string.format("%p", s)` 會印出字串物件的位址。用它檢驗「兩個內容相同、分別建出來的字串是不是同一個物件」：

```bash
./lua -e 'local a="hello"; local b="hel".."lo"; print(string.format("%p %p", a, b), a==b)
local s=string.rep("y",40); local t=string.rep("y",39).."y"; print(string.format("%p %p", s, t), s==t)
local x=string.rep("x",41); local y=string.rep("x",40).."x"; print(string.format("%p %p", x, y), x==y)'
```

```
0x574b8cbfd9f0 0x574b8cbfd9f0	true
0x574b8cbfda70 0x574b8cbfda70	true
0x574b8cbff570 0x574b8cbff610	true
```

40 個字元的兩個字串是同一個物件，41 個字元的不是。拿這個結果去 `lstring.c` 找原因（提示：`luaS_newlstr()` 在第 222 行，第一行就是一個長度比較；門檻常數在 `llimits.h`）。

### 挑戰二：Redis 的主動過期

對象：`redis-7.2.17/src/expire.c`（754 行）的 `activeExpireCycle()`。問題：每次 cycle 最多花多少時間？怎麼決定要不要繼續掃下一輪？`ACTIVE_EXPIRE_CYCLE_ACCEPTABLE_STALE` 是什麼？用 `DEBUG SET-ACTIVE-EXPIRE 0/1` 和 `INFO stats` 的 `expired_keys` 設計實驗。這一題的領域鴻溝比本練習大（要懂「抽樣」的統計直覺），適合練「先補背景再讀」。

### 挑戰三：誰把閒置空間收回去？

在本練習的筆記裡，「未解問題」第二條是「有沒有路徑會收回閒置空間」。用 `grep -n sdsRemoveFreeSpace src/*.c` 找出所有呼叫點，判斷 `APPEND` 長大的字串在什麼情況下會被瘦身，並設計一個 `DEBUG SDSLEN` 實驗驗證。這一題練的是 Ch 5 的「從資料往回找操作它的程式碼」。

### 挑戰四：考古 `SDS_MAX_PREALLOC`

用 `git log -S SDS_MAX_PREALLOC` 和 `git blame src/sds.h` 找出 1 MB 這個值何時出現、有沒有被改過。注意本環境的 `~/code_reading/redis-7.2.17` 雖然有 `.git`，但只是一個 commit 的淺 clone（`git rev-list --count HEAD` 回傳 1，對 `sds.h` 跑 `git log -S` 只會找到 "Redis 7.2.17" 那一個 commit），要做考古得另外 clone 完整歷史。這是 Part 6 的預習：[Ch 37 — git 考古](./37-git-archaeology.md)。

## 自我檢核

- [ ] 能在 3 小時內交出一份含 8 題答案、5 條以上假說（至少 1 條被推翻）、3 條以上黑箱的筆記
- [ ] 不看筆記，能在白板上畫出 `sdshdr8` 的記憶體配置，標出 `s` 和 `s[-1]` 的位置
- [ ] 能解釋為什麼 `APPEND` 一個 embstr 字串後的 avail 是 6 而不是 7
- [ ] 能說出 `val_sds_avail` 的兩個來源（貪婪策略、配置器 size class），並各舉一個實驗證明
- [ ] 能說出自己在這次冷讀中實際卡在哪一道鴻溝、用了哪一招解開
- [ ] 能說出你的筆記和參考筆記的差異，並解釋哪些差異是你刻意的取捨（例如你多讀了某個函式，值不值得）

你剛才用 3 小時讀懂了一個 user-space 子系統，靠的是問題清單和實驗。但過程中你一定在某些行停了很久——`goto`、`s[-1]`、`do { } while (0)`、一串位元運算。Part 2 要把這些「每個字都認得、整行看不懂」的地方一個個拆掉，從控制流開始。

→ [Ch 8 控制流慣用法：goto 收尾、early return、迴圈與 switch 的讀法](./08-control-flow-idioms.md)
