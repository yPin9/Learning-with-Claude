# Ch 16 — 記憶體與資料流動態追蹤

> **目標**：把斷點與 watchpoint 深用成一件事——**追一個值/一段輸入怎麼流過整個程式**：輸入進來 → 被變換 → 拿去比較 → 落到記憶體哪裡。學會用 gdb watchpoint 攔截「一個值每次被改」的瞬間、找出 buffer 在記憶體哪裡、dump 解密後才出現的字串/key。最後接上動態污點分析（taint）的概念。真跑：用 watchpoint 逐步看一個 hash 累加器怎麼被輸入一個 byte 一個 byte 改出來。

> **環境**：WSL2 / Linux x86-64，gcc + gdb 12.1。本章 watchpoint 會話是真跑貼上的。

前幾章的動態工具都在回答「這裡是什麼」——這個函式參數是什麼、它比對了什麼字串。這一章換一個更難也更有威力的問題：**「這個值是怎麼變成現在這樣的？」**「我的輸入進去之後，被折騰成了什麼、跑到哪裡去了？」這是**資料流追蹤（data flow tracking）**，逆向裡最能一舉看穿演算法的一招。

## 為什麼需要這個？

回想 Ch 12 對「找正確密碼」題的分析：動態只看得到你這次輸入走的路，餵錯密碼就只看到失敗分支。但有一種輸入**一定會經過**的東西——**它對你輸入做的變換與比較**。不管密碼對不對，程式都得把你的輸入拿去算、去比。攔住這個「算」的過程，你就能反推「要算出什麼才會過」。

資料流追蹤就是攔這個過程。它回答的問題是：

- **輸入的每個 byte 怎麼被吃進去、變成什麼中間值？**（反推變換演算法）
- **這個關鍵值（hash、checksum、解密 key）是在哪裡、被誰、怎麼算出來的？**
- **解密後的明文/key 出現在記憶體哪個位址、哪個瞬間？**（趁它剛解好還沒用掉時 dump）

靜態逆這些要手算一長串指令、極易出錯。動態資料流追蹤讓你**看著值一步步變**，演算法自己浮現。

## 先建立直覺：跟著資料走，而不是跟著指令走

一般 debug 是「跟著**指令**走」——`stepi` 一條一條往前。資料流追蹤是「跟著**資料**走」——我盯住某個值，程式跑到哪不管，**只在這個值被改動時停下來**。這個視角翻轉靠的是 gdb 的 **watchpoint（監視點）**。

```
   跟著指令走 (stepi)          跟著資料走 (watch)
   ─────────────              ─────────────
   停在每條指令               只停在「acc 被寫」的那一刻
   你決定何時停               資料決定何時停
   看控制流                   看資料流
   適合：理解一段邏輯          適合：追一個值的一生
```

watchpoint 是資料流追蹤的核心武器：你 `watch acc`，之後不管程式跑過多少指令、進出多少函式，**只要 `acc` 這個值被改寫，gdb 就停下來告訴你「舊值→新值」**。把每次停下來的新值串起來，就是這個值的完整演化史——演算法藏在裡面。

## watchpoint 實戰：看一個 hash 累加器被輸入改出來（真跑）

用一個 ground-truth 目標——一個對輸入做 rolling hash、比對 magic 的程式。source（標準答案，逆時蓋起來）：

```c
// xform.c
#include <stdio.h>
int main(int argc,char**argv){
    if(argc<2) return 1;
    unsigned int acc = 0x1505;                 // hash 種子（0x1505 = djb2 的 5381）
    for(const char*p=argv[1]; *p; p++)
        acc = acc*33 + (unsigned char)*p;      // 每個 byte：acc = acc*33 + c
    if(acc == 0x7c9a03cf) { printf("correct\n"); return 0; }
    printf("nope: 0x%x\n", acc); return 1;
}
```

編譯（這次留 `-g` 讓 watchpoint 能用變數名——真實 strip binary 就 `watch *(unsigned*)位址`，原理一樣）：

```bash
$ gcc -O0 -g -o xform xform.c
$ ./xform test
nope: 0x7c9e6865           ← "test" 算出來是這個，不對
```

假裝你只知道「它對輸入算了某種 hash，比對某個 magic」。目標：**看清楚那個 hash 怎麼算**。我們用一個短輸入 `AB`（只 2 個 byte，好觀察），`watch acc`，看它每次被改：

```bash
$ gdb -q ./xform
(gdb) break main
(gdb) run AB
Breakpoint 1, main (argc=2, argv=0x7fffffffe588) at xform.c:4
4       if(argc<2) return 1;
(gdb) watch acc
Hardware watchpoint 2: acc
(gdb) continue
```

真跑，一路 `continue` 看 `acc` 每次變化：

```
Hardware watchpoint 2: acc
Old value = 0
New value = 5381               ← ① acc 初始化成種子 0x1505 = 5381
main (...) at xform.c:6
6       for(const char*p=argv[1]; *p; p++)

Hardware watchpoint 2: acc
Old value = 5381
New value = 177638             ← ② 吃了 'A'(65)：5381*33 + 65 = 177638
main (...) at xform.c:6

Hardware watchpoint 2: acc
Old value = 177638
New value = 5862120            ← ③ 吃了 'B'(66)：177638*33 + 66 = 5862120
main (...) at xform.c:6
```

**演算法在這三步裡完全現形，我們一行 asm 都沒手算**：

1. `acc` 從 0 變成 **5381**——這是種子（`0x1505`）。
2. 吃第一個 byte `'A'`（ASCII 65）：`5381 × 33 + 65 = 177638`。驗證：`5381*33=177573`，`+65=177638`。✓
3. 吃第二個 byte `'B'`（66）：`177638 × 33 + 66 = 5862120`。✓

看到「舊值 × 33 + 一個 byte = 新值」的規律，你就逆出了：**這是 `acc = acc*33 + c` 的 rolling hash（djb2）**，種子 0x1505。watchpoint 把一個原本要瞪著 `imul`/`add`/`movzbl` 手算的迴圈，變成三行「舊值→新值」的對照——資料流追蹤的核心價值就在這。

### strip binary 沒有變數名怎麼辦

上面 `watch acc` 靠的是 `-g` 的除錯資訊。真實 strip binary 沒有 `acc` 這名字，但 watchpoint 照樣能用——你 `watch` **記憶體位址**或**運算式**：

```
(gdb) watch *(unsigned int*)($rbp-0x4)      # acc 若在 rbp-0x4，watch 那塊記憶體
(gdb) watch *(unsigned int*)0x7fffffffe4dc  # 或直接 watch 絕對位址
```

先靜態（objdump）看出 `acc` 存在 stack 哪個偏移（找那個被 `imul $33` 反覆讀寫的位置），再 `watch` 那個位址。無符號世界的資料流追蹤 = watch 記憶體位址而非變數名，其餘完全一樣。

## watchpoint 的三種與陷阱

gdb 有三種 watchpoint，逆向常搞混：

| 指令 | 何時停 | 逆向用途 |
|---|---|---|
| `watch expr` | expr 的值**被寫改**時 | 追一個值怎麼演化（最常用） |
| `rwatch expr` | expr **被讀取**時 | 找「誰用了這個 key/buffer」 |
| `awatch expr` | 讀或寫都停 | 追一個值的完整生命週期 |

**硬體 vs 軟體 watchpoint**：現代 CPU 有除錯暫存器（debug register），gdb 優先用**硬體 watchpoint**（幾乎零開銷，上面輸出的 `Hardware watchpoint` 就是）。但硬體 watchpoint **數量有限（x86 通常 4 個）且監視範圍有大小限制**（通常 ≤8 byte）。超過就退回**軟體 watchpoint**——gdb 每執行一步就檢查一次值，**慢到幾乎不能用**（可能慢幾百倍）。

> **一個真實踩雷**：`watch` 一個大 buffer（`watch buf` 而 buf 是 64 byte）會超過硬體 watchpoint 範圍，gdb 悄悄退回軟體 watchpoint，程式跑到像凍住。要監視「這塊 buffer 有沒有被動」，改 watch buffer 裡**關鍵的 4/8 byte**，或用 `rwatch` 只盯讀取。

另一個陷阱：**watchpoint 有作用域**。watch 一個 stack 上的區域變數，函式一返回、那塊 stack 失效，gdb 會警告 watchpoint 出了作用域並刪掉它。追跨函式的值要 watch 全域位址或 heap 位址。

## 找 buffer 在記憶體哪裡

資料流追蹤的另一半：不是追一個純量值，而是找「我的輸入被複製/存到記憶體哪塊」。典型場景——你輸入一串東西，想知道它落腳在哪，好在那裡下 watchpoint 或 dump。

幾個定位手法：

1. **從參數暫存器順藤摸瓜**：輸入通常經 `argv`、`read`、`fgets` 進來。斷在 `read`/`fgets` 的**返回**，回傳的緩衝區位址（或 `rsi`/`rdi` 指向的）就是輸入落點。`x/s` 印出來確認。
2. **gdb `find` 搜記憶體**：知道輸入內容（如你輸了 `"AAAA"`），`find` 在整個位址空間搜它，找出所有副本：
   ```
   (gdb) find $rsp, +0x10000, "AAAA"     # 在 stack 往上 64KB 內搜 "AAAA"
   0x7fffffffe820
   0x7fffffffe4a0                         # 找到兩個副本——它被複製了一份
   ```
   找到多個副本本身是線索：程式把你的輸入拷了一份去處理。
3. **watch 落點看它被怎麼變換**：找到 buffer 位址後 `awatch` 它的頭幾個 byte，看哪段 code 讀它、改它——把「輸入 buffer」和「處理它的迴圈」連起來。

## dump 解密後的字串/key

Ch 12 講過：加密/packed 的字串靜態是亂數，但程式跑起來一定得解開才能用。資料流追蹤讓你攔在「剛解好、還沒用掉」的黃金瞬間，把明文 dump 出來。

工作流：

1. **找解密函式**：靜態找可疑的 xor 迴圈、或 `strings` 抽不到但程式明明會顯示字串 → 字串是動態解出來的。或 ltrace/strace 看它在 `puts`/`write` 前一刻做了什麼。
2. **斷在解密之後**：在解密迴圈的**出口**下斷點（不是入口——入口時還是密文）。
3. **dump 記憶體**：解密結果通常寫回某個 buffer，`x/s 那個位址` 或 `x/64xb` 把明文/key 挖出來：
   ```
   (gdb) break *解密迴圈出口位址
   (gdb) continue
   (gdb) x/s $rax               # 解密結果的 buffer
   0x555555559260: "S3CR3T-KEY"  ← 明文現形
   ```
4. **key 在暫存器/heap 也一樣**：runtime 才組出的 key、動態算的密碼，全用「斷在它剛做好那刻 → dump」的套路。這是 Frida（Ch 15 `Memory.readByteArray`）和 gdb 都能做的事，機制相同。

## 動態污點分析（taint）：資料流追蹤的自動化終形

手動 watch 一個值很有效，但如果想追「**輸入的哪些 byte 影響了最終那個比較**」，一個一個 watch 太累。**動態污點分析（dynamic taint analysis）** 把這件事自動化：

```
   標記（taint）輸入的每個 byte 為「髒」
        │
        ▼
   程式每執行一條指令，追蹤「髒」怎麼傳播：
   mov 髒→乾淨，乾淨變髒；add 髒+乾淨，結果變髒 ……
        │
        ▼
   到達那個關鍵 cmp 時，看它的運算元是不是髒的、
   髒是從輸入的哪幾個 byte 傳來的
        └──► 自動得出「輸入 byte 3~8 決定了這個檢查」
```

概念是「給輸入貼標籤，看標籤怎麼在指令間流動」。實作靠 DBI（Ch 15 的 Pin/DynamoRIO——每條指令插 callback 更新污點狀態），或符號執行。這是你 [`symex_taint`](../symex_taint/README.md) 課的主題——它把「輸入怎麼流過程式」從手動 watch 升級成自動、全覆蓋的分析，還能反過來（符號執行）自動解出「要輸入什麼才能讓那個 cmp 成立」。本章的手動 watchpoint 是它的直覺原型；那門課是它的引擎。

## 對比與取捨

| 手段 | 追什麼 | 成本 | 適合 |
|---|---|---|---|
| `watch` 一個值 | 純量值的演化史 | 硬體 watch 幾乎零開銷 | 逆一個 hash/checksum/累加器 |
| `find` 搜記憶體 | 輸入落在哪些位址 | 低 | 定位 buffer、找副本 |
| 斷在解密出口 dump | 解密後的明文/key | 低 | 加密字串、runtime key |
| 手動 watch + stepi | 一段邏輯的細節 | 中（要盯著） | 小範圍精細分析 |
| 動態污點（taint） | 輸入哪些 byte 影響哪個判斷 | 高（要 DBI/符號執行） | 全自動、大範圍、找可控輸入 |

原則：**單一值/小範圍，手動 watchpoint 最快**；**要問「輸入的哪部分影響哪個決定」、要全覆蓋，上污點/符號執行**（`symex_taint` 課）。

## 踩雷集錦

1. **watch 大 buffer 導致軟體 watchpoint、程式凍住**（本章真陷阱）：硬體 watchpoint 範圍有限（通常 ≤8 byte、x86 只 4 個）。watch 整個 64-byte buffer 會退回軟體 watchpoint，慢到不能用。改 watch 關鍵的 4/8 byte。
2. **watch 的區域變數出了作用域被刪**：追一個 stack 局部變數，函式返回後那塊 stack 失效，gdb 刪掉 watchpoint。追跨函式的值要 watch 全域/heap 位址，或在對的函式層下 watch。
3. **斷在解密迴圈入口就 dump，看到的還是密文**：解密發生在迴圈**執行完**之後。斷點要下在**出口**（迴圈後那條指令）或解密函式的 `ret`，這時 buffer 才是明文。
4. **以為 watchpoint 停下時 `$pc` 就是「改它的那條指令」**：硬體 watchpoint 是「值變了才通知」，gdb 停下來時 `$pc` 通常已經**越過**了那條寫入指令（停在下一條）。想看是哪條指令改的，往回看一兩條（`x/-3i $pc`）。
5. **strip binary 上 watch 變數名失敗**：沒有 `-g` 就沒有 `acc` 這名字。改 watch 記憶體位址/運算式（`watch *(unsigned*)($rbp-0x4)`）——先靜態找出那個值存在哪。
6. **輸入有多份副本，只 watch 到其中一份**：程式常把輸入拷貝再處理。`find` 搜出所有副本，確認你 watch/dump 的是「真正被拿去比對的那一份」，不是原始 argv。

## 進階：再往深一層

- **conditional watchpoint**：`watch acc if acc > 0x100000`——只在值超過某門檻時停，追一個變化很多次的值時省掉大量無關的停頓。配合 Ch 13 的 conditional breakpoint 一起用。
- **reverse debugging 追資料的來源**：watchpoint 告訴你「值變了」，但你想知道「**這個壞值是哪來的**」時，`rr`（時間旅行，你的 [`gdb`](../gdb/README.md) 課 Ch 35）能從壞值往**回**放，反向找到寫入它的源頭。逆向一個複雜資料流的污染源，這比正向 watch 強太多。
- **符號執行反解輸入**：手動追出「輸入 → hash → 比 magic」後，要**解出讓 hash == magic 的輸入**，正向 watch 幫不了（你得試）。符號執行（`angr`，`symex_taint` 課）把輸入設成符號、把整條變換和比較丟給 SMT solver，直接解出滿足條件的具體輸入。這是資料流追蹤的終極自動化。

## 本章重點整理

- 資料流追蹤回答的是「**這個值怎麼變成現在這樣、我的輸入被折騰成了什麼**」——逆向裡最能一舉看穿演算法的一招。視角從「跟著指令走」翻轉成「跟著資料走」。
- **watchpoint 是核心武器**：`watch` 一個值，之後只在它被改時停，把「舊值→新值」串起來就是演化史。本章真跑看到 `acc*33+c` 的 rolling hash 在三步裡現形。
- strip binary 上 watch 記憶體位址/運算式（非變數名）；`find` 搜輸入落點；斷在解密**出口**dump 明文/key。
- 硬體 watchpoint 幾乎零開銷但範圍/數量有限，watch 大 buffer 會退回軟體 watchpoint 而凍住——watch 關鍵幾 byte。
- 手動 watch 是**動態污點分析**的直覺原型；要問「輸入哪些 byte 影響哪個判斷」「要輸入什麼才能過」，升級到污點/符號執行（`symex_taint` 課）。

## 自我檢核

- [ ] 我能用 `watch` 追一個累加器，從「舊值→新值」序列反推它的變換公式
- [ ] 我知道 strip binary 沒有變數名時，怎麼改 watch 記憶體位址/運算式
- [ ] 我知道 `watch`/`rwatch`/`awatch` 的差別，以及硬體 vs 軟體 watchpoint 的成本陷阱
- [ ] 我能用「斷在解密出口 → dump 記憶體」的套路挖出解密後的明文/key
- [ ] 我理解動態污點分析在做什麼，以及它和手動 watchpoint、符號執行的關係

## 延伸閱讀

### 官方文件 / 課程

- **你自己的 [`gdb`](../gdb/README.md) 課 Ch 13**
  - **讀哪裡**：Watchpoint 全解——硬體/軟體、`watch`/`rwatch`/`awatch`、作用域、conditional watchpoint。本章是它的逆向應用摘要
- **你自己的 [`symex_taint`](../symex_taint/README.md) 課**
  - **這是什麼**：動態污點分析與符號執行的完整課——本章末尾「輸入哪些 byte 影響哪個判斷」「反解輸入」的引擎在那

### 書籍 / 論文

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **讀哪幾章**：Ch 11（動態污點分析，libdft 實戰）——本章 taint 概念的完整實作
- **《All You Ever Wanted to Know About Dynamic Taint Analysis...》** — Schwartz, Avgerinos, Brumley（IEEE S&P, 2010）
  - **這是什麼**：動態污點分析與前向符號執行的經典綜述，把本章末尾的概念講到底層語意

我們已經備齊斷點、trace、插樁、資料流追蹤四樣動態武器。下一章把它們和 Part 1 的靜態技術綁在一起，走一遍完整的**假設驅動逆向**——靜態建假設、動態一跑就確認或推翻，收斂到真相。

→ [Ch 17 靜動結合：假設驅動逆向](./17-combining-static-dynamic.md)
