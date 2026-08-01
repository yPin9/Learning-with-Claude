# Ch 39 — 案例研究：完整攻堅實況

> **目標**：不再談方法——**真的做一遍**。我們對 redis 7.4.0 選一個真實小任務，從零到改，把 Ch 5-36 的技巧串起來實戰演示。全程真跑、貼真實輸出。這是一篇「攻堅實況轉播」：你會看到每一個 SOP 階段怎麼落地、工具怎麼協同、假設怎麼被執行釘死、以及一個「我以為對結果錯了」的真實轉折。讀完你該有信心自己走一遍。

> **環境**：WSL Ubuntu 22.04，sandbox `~/reading_code_lab/redis`（redis 7.4.0，已 `make` 過），工具鏈同 Ch 0。本章所有指令輸出皆為實際執行後照抄。

## 任務界定（SOP 階段 0）

假設有人丟給你這個任務——你**沒讀過 redis 原始碼**：

> 「redis 的 `OBJECT ENCODING key` 會告訴你一個 key 內部用什麼編碼存。我想知道：一個 list 什麼時候是 `listpack`、什麼時候變成 `quicklist`？這個判斷邏輯在哪、怎麼運作？我要能改動那個門檻並驗證我的改動生效。」

按 Ch 38 的模板，先填三欄：

```
本次任務：搞懂 redis list 的 listpack→quicklist 編碼判斷邏輯，並能改門檻且驗證
成功標準：(1) 能指出判斷發生在哪個函式哪一行
          (2) 能說清楚「什麼條件下觸發轉換」
          (3) 改一個相關行為（例如 strEncoding 回傳字串）並跑起來看到效果
不需要懂：quicklist 內部節點結構、listpack 的位元組級編碼、LZF 壓縮
```

「不需要懂」那欄是我們對抗完美主義（Ch 37 反模式 8）的預先聲明。listpack 的位元組編碼是個誘人的 rabbit-hole，我們現在就宣告：不進去。

---

## 階段 1：偵察（Ch 5）

先體檢規模。我們對整個 `src/` 下 `cloc`：

```
$ cloc --quiet src/
---------------------------------------------------------------------------------------
Language                             files          blank        comment           code
---------------------------------------------------------------------------------------
C                                      115          15755          32001         100023
JSON                                   401              2              0          24565
Windows Module Definition                1           2094              0           9141
C/C++ Header                            69           1220           3045           8643
...
```

十萬行 C、115 個 `.c`、註解比約 1:3（勤勞）。這是我們在 Ch 0 就看過的數字——**偵察的第一招是量體，讓你對「攻堅面有多大」有數**。十萬行不是一天能通讀的，所以我們從一開始就放棄通讀，走定位路線。

任務裡有兩個關鍵字：`OBJECT ENCODING` 和 `list`。這是我們的**進攻錨點**。偵察階段不深入，先確認索引就緒（Ch 0 已建過 tags/cscope），直接進定位。

> 這裡我們**刻意壓縮偵察**——因為任務目標很明確（不是 onboarding 整個 redis），有現成的字串錨點可以反向定位。這正是 Ch 38 說的「按目標調 SOP」：明確任務的偵察可以砍很短。

---

## 階段 3：定位（Ch 11、Ch 12）— 從字串錨點反推

我們有 `OBJECT ENCODING` 這個使用者可見的命令字串。**從使用者可見的東西反推實作，是定位的黃金策略**（Ch 11）。先用 ripgrep 撒網（Ch 12），從廣到窄：

```
$ rg -n "encoding" src/object.c | head -20
25:    o->encoding = OBJ_ENCODING_RAW;
...
136:            o->encoding = OBJ_ENCODING_INT;
...
205:        d->encoding = OBJ_ENCODING_INT;
...
```

太多了——`encoding` 這個字在 `object.c` 出現幾十次。純文字搜尋的通病：**同名字串全中，不分作用域**（Ch 0、Ch 12 反覆強調）。收窄一下，我們要找的是「把 encoding 轉成字串回給使用者」的地方，關鍵字更精確：

```
$ rg -n "encoding|ENCODING" src/object.c | rg "Command|strEncoding"
75:928:char *strEncoding(int encoding) {
108:1469:        addReplyBulkCString(c,strEncoding(o->encoding));
```

兩條命中，一下收斂。`strEncoding()`（928 行）把編碼碼轉成字串，`objectCommand`（1469 行附近）呼叫它。我們去看 `strEncoding`：

```
$ sed -n '928,942p' src/object.c
char *strEncoding(int encoding) {
    switch(encoding) {
    case OBJ_ENCODING_RAW: return "raw";
    case OBJ_ENCODING_INT: return "int";
    case OBJ_ENCODING_HT: return "hashtable";
    case OBJ_ENCODING_QUICKLIST: return "quicklist";
    case OBJ_ENCODING_LISTPACK: return "listpack";
    case OBJ_ENCODING_LISTPACK_EX: return "listpackex";
    case OBJ_ENCODING_INTSET: return "intset";
    case OBJ_ENCODING_SKIPLIST: return "skiplist";
    case OBJ_ENCODING_EMBSTR: return "embstr";
    case OBJ_ENCODING_STREAM: return "stream";
    default: return "unknown";
    }
}
```

**這是第一個重要發現**：`OBJECT ENCODING` 本身其實很笨——它只是讀 `o->encoding` 這個欄位、查表回傳字串。它不做任何判斷，**編碼是誰、在哪決定的，不在這裡。** 這是攻堅時常見的認知修正：使用者看到的「功能」（回傳編碼）和真正的「機制」（決定編碼）是分開的。`strEncoding` 是出口，不是機制。

那機制在哪？我們要找「誰把 `o->encoding` 設成 `OBJ_ENCODING_QUICKLIST`」。這是一個典型的 **data flow 反向追蹤**（Ch 8）：欄位在哪被寫。

---

## 階段 3.5：追寫入點（Ch 8、Ch 14）

用 ripgrep 找所有「把 list 的 encoding 設成 quicklist」的地方：

```
$ rg -n "OBJ_ENCODING_QUICKLIST" src/t_list.c
52:        o->encoding = OBJ_ENCODING_QUICKLIST;
66:    serverAssert(o->encoding == OBJ_ENCODING_QUICKLIST);
```

52 行就是那個**寫入點**——list 的 encoding 在這裡被改成 quicklist。這一定在某個轉換函式裡。我們看它的上下文：

```
$ sed -n '20,54p' src/t_list.c
```
```c
static void listTypeTryConvertListpack(robj *o, robj **argv, int start, int end,
                                       beforeConvertCB fn, void *data)
{
    serverAssert(o->encoding == OBJ_ENCODING_LISTPACK);

    size_t add_bytes = 0;
    size_t add_length = 0;

    if (argv) {
        for (int i = start; i <= end; i++) {
            if (!sdsEncodedObject(argv[i]))
                continue;
            add_bytes += sdslen(argv[i]->ptr);
        }
        add_length = end - start + 1;
    }

    if (quicklistNodeExceedsLimit(server.list_max_listpack_size,
            lpBytes(o->ptr) + add_bytes, lpLength(o->ptr) + add_length))
    {
        /* Invoke callback before conversion. */
        if (fn) fn(data);

        quicklist *ql = quicklistNew(server.list_max_listpack_size, server.list_compress_depth);
        /* Append listpack to quicklist if it's not empty, otherwise release it. */
        if (lpLength(o->ptr))
            quicklistAppendListpack(ql, o->ptr);
        else
            lpFree(o->ptr);
        o->ptr = ql;
        o->encoding = OBJ_ENCODING_QUICKLIST;
    }
}
```

**找到了核心**。`listTypeTryConvertListpack` 就是判斷發生的地方，關鍵是這個條件：

```c
if (quicklistNodeExceedsLimit(server.list_max_listpack_size,
        lpBytes(o->ptr) + add_bytes, lpLength(o->ptr) + add_length))
```

判斷用的參數：`server.list_max_listpack_size`（一個 config）、當前 listpack 的位元組數（`lpBytes`）加上即將加入的位元組、當前元素數（`lpLength`）加上即將加入的數量。**只要超過門檻，就分配一個 quicklist、把 listpack 塞進去、把 encoding 改掉。**

我們暫時把 `quicklistNodeExceedsLimit` 當黑盒（Ch 23 按需展開）——名字已經說明它在做什麼（判斷是否超過節點限制），任務目標不需要它的內部。這是 SOP 決策樹 Q3：這條路徑上不需要具體實作，當黑盒繼續。

現在成功標準 (1) 達成：判斷在 `src/t_list.c:37` 的 `quicklistNodeExceedsLimit()` 呼叫。

---

## 階段 4：追蹤觸發鏈（Ch 9）— 誰呼叫這個轉換？

我們知道了「判斷發生在哪」，但還缺一環：**什麼時候會呼叫 `listTypeTryConvertListpack`？** 也就是使用者做什麼操作會觸發它。用 cscope 反查呼叫者（Ch 14）——這正是純文字搜尋做不好、cscope 的殺手級用途：

先看它的包裝函式（`listTypeTryConvertListpack` 是 `static`，一定有非 static 的入口）：

```
$ rg -n "listTypeTryConversionAppend|listTypeTryConvertListpack" src/t_list.c
```
```
110:static void listTypeTryConversionRaw(robj *o, list_conv_type lct,
133:void listTypeTryConversionAppend(robj *o, robj **argv, int start, int end,
464:void pushGenericCommand(client *c, int where, int xx) {
479:    listTypeTryConversionAppend(lobj,c->argv,2,c->argc-1,NULL,NULL);
494:    pushGenericCommand(c,LIST_HEAD,0);    // LPUSH
499:    pushGenericCommand(c,LIST_TAIL,0);    // RPUSH
```

呼叫鏈浮現了：

```
LPUSH/RPUSH 命令
  → pushGenericCommand()          (t_list.c:464)
    → listTypeTryConversionAppend() (t_list.c:479)
      → listTypeTryConversionRaw()
        → listTypeTryConvertListpack()  ← 判斷+轉換在這
```

**這回答了「什麼時候」**：每次 `LPUSH`/`RPUSH`（push 元素進 list）時，redis 都會檢查一次要不要轉換。合理——因為 push 是 list 長大的時刻，正是可能超過門檻的時刻。

成功標準 (2) 有了雛形：**push 時檢查，超過 `list_max_listpack_size` 門檻就從 listpack 轉 quicklist。** 但「超過門檻」的確切語意還沒確認——`list_max_listpack_size` 到底是「元素個數」還是「位元組數」？這是我們的**待驗證假設**。別靠腦補（Ch 37 反模式 5），跑起來看。

---

## 階段 5：驗證（Ch 18、Ch 19）— 用執行釘死假設

### 第一次實驗：一個「我以為對、結果錯了」的轉折

先看 config 預設值，再做個直覺實驗：push 一堆元素進去，看它會不會轉。我開一個 server（非預設 port 避免撞到既有實例）：

```
$ redis-server --port 6399 --save "" --daemonize yes --logfile /tmp/rs.log
$ C="redis-cli -p 6399"
$ $C config get list-max-listpack-size
list-max-listpack-size
-2
$ $C rpush mylist a b c
$ $C object encoding mylist
listpack
$ for i in $(seq 1 200); do $C rpush mylist item$i >/dev/null; done
$ $C object encoding mylist
listpack
```

**等等——push 了 200 多個元素，還是 `listpack`？** 我原本的直覺是「元素多了就會轉 quicklist」，這下打臉了。這是攻堅中最有價值的時刻：**執行結果跟你的心智模型衝突**（Ch 10 假設驅動的核心價值）。

回頭看那個 config 值：`-2`。這不是「200 個元素」的門檻，是個負數。負數是什麼意思？回去查 config 定義（Ch 11 收斂到相關 code）：

```
$ rg -n "list-max-listpack-size" src/config.c
3152:    createIntConfig("list-max-listpack-size", "list-max-ziplist-size", MODIFIABLE_CONFIG, INT_MIN, INT_MAX, server.list_max_listpack_size, -2, INTEGER_CONFIG, NULL, NULL),
```

預設 `-2`。redis 的慣例（查文件/註解可證）：**負數代表「以大小為限」**——`-2` 表示每個 listpack 上限 8 KB，`-1` 是 4 KB……**正數才代表「以元素個數為限」**。所以我 push 200 個短字串，總位元組遠不到 8 KB，當然不轉。**我的直覺「元素多就轉」是錯的——真正的門檻預設是位元組大小，不是個數。**

這修正了成功標準 (2)：**`list_max_listpack_size` 正數 = 元素個數上限，負數 = 位元組大小上限；預設 `-2`（8KB）。超過就轉 quicklist。**

### 第二次實驗：驗證修正後的模型

我用兩種方式各驗一次，證明我修正後的理解對：

```
$ $C flushall
# case1: 設成正數 128（個數門檻），push 200 個 → 應該轉
$ $C config set list-max-listpack-size 128
$ for i in $(seq 1 200); do $C rpush L1 v$i >/dev/null; done
$ $C object encoding L1
quicklist
# case2: 回預設 -2（8KB 門檻），塞一個 9KB 的大元素 → 應該轉
$ $C config set list-max-listpack-size -2
$ $C rpush L2 $(python3 -c 'print(chr(120)*9000)')
$ $C object encoding L2
quicklist
```

兩個都如預期轉成 `quicklist`。**模型驗證通過**：個數超限會轉，單一大元素超位元組限也會轉。心智模型現在跟真實行為對齊了。

### 第三次實驗：gdb 看轉換函式真的被呼叫（Ch 18）

字串輸出證明了「結果」，但我還想親眼看「機制」——那個判斷函式在每次 push 時真的被呼叫、list 長度真的在增長。這是 debugger-driven reading（Ch 18）。我把門檻設成 4（很小），attach gdb 到 server，在轉換函式下斷點，然後 push 6 個元素：

```
$ redis-cli -p 6399 config set list-max-listpack-size 4
$ PID=$(redis-cli -p 6399 info server | grep -oP 'process_id:\K[0-9]+' | tr -d '\r')
$ cat > /tmp/gdbcmd.txt <<'EOF'
set pagination off
break t_list.c:37
commands
  printf "HIT convert-check: encoding=%d len=%d\n", ((robj*)o)->encoding, lpLength(((robj*)o)->ptr)
  bt 3
  continue
end
continue
EOF
$ sudo gdb -q -p $PID -x /tmp/gdbcmd.txt &
# 另一個 terminal：
$ for i in 1 2 3 4 5 6; do redis-cli -p 6399 rpush GL x$i; done
$ redis-cli -p 6399 object encoding GL
quicklist
```

gdb 打出的真實斷點記錄（節錄前幾次命中）：

```
Thread 1 "redis-server" hit Breakpoint 1, listTypeTryConvertListpack (o=o@entry=0x7aec96400000, argv=0x7aec99a5fac8, start=2, end=2, ...) at .../t_list.c:23
HIT convert-check: encoding=11 len=0
#0  listTypeTryConvertListpack (...) at src/t_list.c:23
#1  ... listTypeTryConversionRaw (... lct=LIST_CONV_GROWING ...) at src/t_list.c:119
#2  listTypeTryConversionAppend (...) at src/t_list.c:136

Thread 1 "redis-server" hit Breakpoint 1, listTypeTryConvertListpack (...) at .../t_list.c:23
HIT convert-check: encoding=11 len=1
...（省略中間）...
HIT convert-check: encoding=11 len=4
#0  listTypeTryConvertListpack (...) at src/t_list.c:23
#1  ... listTypeTryConversionRaw (... lct=LIST_CONV_GROWING ...) at src/t_list.c:119
#2  listTypeTryConversionAppend (...) at src/t_list.c:136
```

**這是攻堅的高潮**。gdb 一次證明了三件我之前只是「靜態推論」的事：

1. **呼叫鏈是真的**：backtrace 明明白白 `listTypeTryConversionAppend → listTypeTryConversionRaw → listTypeTryConvertListpack`，跟我用 cscope 靜態推的一模一樣（Ch 14 的靜態呼叫鏈被動態證實）。
2. **每次 push 都檢查**：斷點在 6 次 push 中每次都命中，`len` 從 0→1→2→3→4 一路增長——證實「push 是檢查時機」。
3. **`encoding=11` 就是 `OBJ_ENCODING_LISTPACK`**。順手查一下這個魔數對不對：

```
$ rg -n "OBJ_ENCODING_LISTPACK |OBJ_ENCODING_QUICKLIST" src/server.h
891:#define OBJ_ENCODING_QUICKLIST 9 /* Encoded as linked list of listpacks */
893:#define OBJ_ENCODING_LISTPACK 11 /* Encoded as a listpack */
```

`11` = `OBJ_ENCODING_LISTPACK`，`9` = quicklist。gdb 印出的 `encoding=11` 完全吻合——在轉換發生**前**，物件確實還是 listpack。動態與靜態閉環對上，理解釘死。

> **注意一個 gdb 現身的細節**：backtrace 裡 `lct=LIST_CONV_GROWING`。回去看 `t_list.c` 會發現 push 走的是「growing」路徑（只考慮 listpack→quicklist），而刪元素走「shrinking」路徑（考慮反向轉回去）。這是靜態讀容易漏、動態一眼看到的分支資訊。Ch 24 講的狀態機/事件驅動在這裡具體化了。

---

## 階段 6：改一個小功能並驗證（Ch 33、Ch 21）

成功標準 (3)：做一個能觀察到效果的改動並驗證。我們選一個最小、安全、可回退的改動——讓 `OBJECT ENCODING` 對 int 編碼回傳一個自訂字串，證明我們真的掌控了這條出口路徑。

先備份、改、增量重編（Ch 21：只重編改到的 object.o + link，不全 build）：

```
$ cp src/object.c /tmp/object.c.bak
$ sed -i 's/case OBJ_ENCODING_INT: return "int";/case OBJ_ENCODING_INT: return "int-MODIFIED";/' src/object.c
$ grep -n "int-MODIFIED" src/object.c
931:    case OBJ_ENCODING_INT: return "int-MODIFIED";
$ make -C src redis-server 2>&1 | tail -3
    CC logreqres.o
    LINK redis-server
make: Leaving directory '/home/ypp/reading_code_lab/redis/src'
```

重跑、驗證：

```
$ redis-server --port 6399 --save "" --daemonize yes --logfile /tmp/rs.log
$ redis-cli -p 6399 set num 12345
$ redis-cli -p 6399 object encoding num
int-MODIFIED
```

**改動生效**。`OBJECT ENCODING` 對整數編碼的 key 回傳了我們自訂的 `int-MODIFIED`。這證明我們完整掌握了這條路徑：從命令分派 → `objectCommand` → `strEncoding` → 我們改的那一行。

改完馬上還原（Ch 38 安全改動 checklist：留退路）：

```
$ cp /tmp/object.c.bak src/object.c
$ make -C src redis-server 2>&1 | tail -1
make: Leaving directory '/home/ypp/reading_code_lab/redis/src'
$ redis-cli -p 6399 shutdown nosave
```

> 這個改動刻意選在「出口」（`strEncoding`）而非「判斷」（`quicklistNodeExceedsLimit`），因為出口的改動最小、副作用最可控——`strEncoding` 只被 introspection 命令用，改它不影響任何實際存儲邏輯（cscope 反查呼叫者可證）。要改「判斷門檻」其實更簡單（直接 `config set list-max-listpack-size`，根本不用改 code），這也是一個攻堅心得：**很多「行為」是 config 驅動的，改 config 比改 code 更該先試。**

---

## 費曼摘要（Ch 36）：把整條線講清楚

用大白話講給一個沒讀過 redis 的人聽：

> redis 的 list 有兩種內部長相。小的時候用 **listpack**——一塊連續記憶體，省空間、快。長大到超過門檻（預設每塊 8 KB，或你設定的元素個數上限）就升級成 **quicklist**——一串 listpack 用鏈結串起來，適合放很多元素。
>
> 這個「要不要升級」的判斷，發生在**每次你 `LPUSH`/`RPUSH` 塞元素進去的時候**：`pushGenericCommand` 會呼叫 `listTypeTryConvertListpack`，用 `quicklistNodeExceedsLimit` 檢查「加上新元素後會不會超過門檻」，超過就當場把 listpack 包進一個新 quicklist，並把物件的 `encoding` 欄位從 `LISTPACK`（11）改成 `QUICKLIST`（9）。
>
> 而你打 `OBJECT ENCODING` 看到的字串，只是 `strEncoding()` 把那個 `encoding` 欄位查表翻成人話而已——它是**顯示器，不是決策者**。決策者是 push 路徑上那個轉換函式。

能一口氣講到這個程度，就是真懂了（Ch 36 費曼測試通過）。注意這段摘要裡沒有一句是「我猜」——每個斷言背後都有一次真跑（config 值、object encoding 輸出、gdb backtrace、魔數比對）撐著。

---

## 這一戰用了哪些技巧（回顧全課）

把這次攻堅拆開，看每一步對應哪一章的技巧，體會方法論如何協同：

```
 步驟                         用到的技巧              章節
 ───────────────────────────────────────────────────────
 填任務三欄（含「不需要懂」）  界定任務、防完美主義    Ch 11,37,38
 cloc 量體                     偵察                    Ch 5
 rg 找 encoding 字串           文字搜尋撒網            Ch 12
 rg 收窄到 strEncoding         精確化查詢              Ch 12
 發現「顯示≠決策」             認知修正、以行為為準    Ch 30
 rg 找 OBJ_ENCODING_QUICKLIST  data flow 反查寫入點    Ch 8
 quicklistNodeExceedsLimit黑盒 按需展開 indirection    Ch 23
 cscope/rg 反查呼叫者          呼叫鏈、call graph      Ch 9,14
 「-2 是什麼」的實驗           假設驅動、只讀不跑之戒  Ch 10,18
 兩次對照實驗                  驗證修正後的模型        Ch 10
 gdb 斷點 + backtrace          debugger-driven reading Ch 18
 魔數 11 比對 server.h         靜態動態閉環            Ch 14,18
 sed 改 + make 增量重編        改動、build system      Ch 21,33
 費曼摘要                      費曼測試                Ch 36
```

**一次真實攻堅，橫跨了 Part 2 到 Part 5 的十幾個技巧**。它們不是各自為政——rg 撒網、cscope 建鏈、gdb 釘死、實驗校準，是一套**協同的閉環**：靜態工具給你假設，動態工具驗證假設，實驗校正你的心智模型，最後費曼測試確認你真懂。這就是整門課要教的東西，濃縮在一個下午的實戰裡。

## 踩雷集錦

1. **錯誤直覺**：「`OBJECT ENCODING` 回傳 encoding，所以判斷編碼的邏輯一定在 `objectCommand` 附近。」→ **正確認識**：使用者可見的「顯示」和背後的「決策」常常在完全不同的地方。`strEncoding` 只是查表出口，真正決定編碼的是 push 路徑上的轉換函式。攻堅時要區分「呈現層」和「機制層」——順著字串找到的往往是呈現層，得再往回追一層才到機制。

2. **錯誤直覺**：「list 元素多了就會變 quicklist。」→ **正確認識**：預設門檻 `-2` 是**位元組大小**（8KB）不是元素個數。我 push 200 個短字串它紋風不動，就是這個原因。負數配置在 redis 裡代表「以大小為限」是個容易踩的慣例陷阱——**看到反直覺的結果，先去查 config 的語意，別怪自己或工具**。

3. **錯誤直覺**：「靜態把呼叫鏈追出來就夠了，不用真的跑。」→ **正確認識**：靜態推的呼叫鏈可能漏分支（例如 growing vs shrinking 兩條路徑）、可能猜錯魔數。gdb 一跑，backtrace 直接證實呼叫鏈、`encoding=11` 直接對上 `OBJ_ENCODING_LISTPACK`——五分鐘動態驗證，抵得上半天靜態糾結。「只讀不跑」在這個案例被具體打臉。

4. **錯誤直覺**：「要改行為就得改 code。」→ **正確認識**：redis 的編碼門檻是 config 驅動的（`list-max-listpack-size`）。很多你以為要改 code 的「行為」，其實 `config set` 一行就變了。攻堅時先問「這是 config 驅動的嗎」，往往省下改 code + 重編的功夫。

5. **錯誤直覺**：「gdb attach 到 redis-server 很麻煩，還是乾讀吧。」→ **正確認識**：`sudo gdb -p $PID -x cmdfile` + `break … / commands / continue` 腳本化，一次設好，push 幾次就把所有你想看的值印出來——比你盯著 code 猜快太多。門檻沒你想的高，習慣它。

## 動手練習

1. **自己重跑一遍**：照本章步驟，對你的 redis sandbox 走一遍。特別是那個「push 200 個還是 listpack」的轉折——親手撞一次那個反直覺，比讀十遍都深刻。

2. **換一個資料型別攻**：把同樣的流程套到 **hash** 上——`OBJECT ENCODING` 對 hash 何時是 `listpack`、何時是 `hashtable`？找到對應的轉換函式（提示：`t_hash.c`、`hash-max-listpack-entries`），走完偵察→定位→追蹤→gdb 驗證。

3. **反向：從 quicklist 轉回 listpack**：本章只看了 growing（listpack→quicklist）。刪元素時的 shrinking 路徑（quicklist→listpack）長怎樣？找 `listTypeTryConvertQuicklist`，注意它為什麼用「一半的門檻」（提示：避免頻繁來回轉換）。用 gdb 在刪元素時斷點驗證。

4. **改判斷而非出口**：本章改的是 `strEncoding`（出口）。試著改 `listTypeTryConvertListpack` 裡的判斷邏輯（例如加一行 log 印出「正在轉換，當前 len=X」），重編、觸發、看你的 log。體會改「機制層」和「呈現層」的不同風險。

## 本章重點整理

- 一次真實攻堅 = 界定任務（含不需要懂）→ 偵察量體 → 從使用者可見字串定位 → data flow 反查寫入點 → cscope 反查觸發鏈 → 實驗+gdb 釘死 → 改動驗證 → 費曼摘要。
- 關鍵認知修正：`OBJECT ENCODING`（`strEncoding`）是**顯示器不是決策者**；真正的編碼決策在 push 路徑的 `listTypeTryConvertListpack`（`t_list.c:37`）。
- 關鍵反直覺：`list-max-listpack-size` 預設 `-2` 是**位元組門檻**（8KB）不是個數；負數=大小、正數=個數。執行結果打臉直覺是攻堅最有價值的時刻。
- gdb 一次閉環三件事：證實靜態呼叫鏈、證實每次 push 都檢查、`encoding=11` 對上 `OBJ_ENCODING_LISTPACK`。
- 十幾個章節的技巧在一個下午協同：靜態工具產生假設、動態工具驗證假設、實驗校正模型、費曼測試確認真懂。

## 自我檢核

- [ ] 我能不看本章，說出 redis list 從 listpack 轉 quicklist 的判斷發生在哪個函式、什麼時機觸發嗎？
- [ ] 我理解「`OBJECT ENCODING` 是顯示器不是決策者」這個區分，以及它對「怎麼定位機制」的啟示嗎？
- [ ] 那個「push 200 個還是 listpack」的轉折，我能解釋為什麼、以及正確的門檻語意是什麼嗎？
- [ ] 我能自己寫出一段 gdb 腳本（break + commands + printf + continue）去觀察某個函式每次被呼叫時的參數嗎？
- [ ] 換一個型別（hash/set/zset），我有信心自己走完整套「定位→追蹤→驗證」流程嗎？

方法論演練完畢，你看過一次完整的攻堅實況。最後——把這一切用在一個**你完全沒看過的真實專案**上，限時產出偵察報告、架構地圖、路徑 trace、費曼摘要和一個 PR-ready 改動。這是全課的畢業考。

→ [Final Project：冷啟動攻堅一個真實 codebase](./final-project-cold-codebase-attack.md)
