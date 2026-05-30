# 練習 B — 資料結構偵探

> **目標**：綜合 Part 2（stepping、符號、print/x、表示式、型別、stack/frame、暫存器），在一個資料結構被破壞的程式裡，靠 GDB 把現場還原、找出是哪一步把它弄壞的。完成後你會掌握「用 GDB 走訪與檢驗任意資料結構」這項日常硬功夫。

## 背景與動機

最難 debug 的 bug，往往是「資料在某處被默默改壞，崩潰在很久以後的別處」。崩潰點看起來無辜，真兇早就離場。要破這種案，你得能：手動走訪資料結構、比對「應該長怎樣 vs 實際長怎樣」、用 watchpoint（下一章）或回溯找出污染源。這個練習用一個壞掉的 linked list / binary tree 訓練你這套偵探技能——這正是真實系統 debug 最常見的場景。

## 任務規格

### 產生有 bug 的程式

```c
// detective.c — gcc -g -O0 detective.c -o detective
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Node {
    int id;
    char name[8];          // 故意很小
    struct Node *next;
} Node;

Node *make(int id, const char *name) {
    Node *n = calloc(1, sizeof *n);
    n->id = id;
    strcpy(n->name, name);  // ← BUG：沒檢查長度，name 可能 > 7
    return n;
}

Node *build(void) {
    Node *head = make(1, "alice");
    head->next = make(2, "bob");
    head->next->next = make(3, "christopher");  // ← 11 字元，溢出 name[8]！
    head->next->next->next = make(4, "dan");
    return head;
}

int sum_ids(Node *h) {
    int s = 0;
    for (Node *p = h; p != NULL; p = p->next)   // ← 可能因 next 被踩壞而崩潰/無窮迴圈
        s += p->id;
    return s;
}

int main(void) {
    Node *list = build();
    printf("sum = %d\n", sum_ids(list));
    return 0;
}
```

bug：`strcpy(n->name, "christopher")` 把 11 字元（含 `\0` 共 12）寫進 `char name[8]`，溢出去踩到緊接其後的 `next` 指標。`sum_ids` 走到那個節點時 `next` 已被污染，可能崩潰、無窮迴圈、或讀到垃圾。

### 你要做的事

1. 跑起來觀察症狀（崩潰？無窮迴圈？sum 錯誤？）。
2. 用 GDB **手動走訪這個 list**，找出哪個節點的 `next` 不對勁。
3. 用記憶體檢視（`x`）證明：是 `name` 的內容溢出蓋掉了 `next`。
4. 解釋 struct 的記憶體佈局，說明為什麼 `name[8]` 溢出剛好踩到 `next`。
5. （進階）用條件斷點或 watchpoint（下一章）抓住「`next` 被改壞」的那一刻。

### 驗收標準

- [ ] 你能說出程式的實際症狀，並用 GDB 重現
- [ ] 你能手動從 `head` 走訪到出問題的節點，指出哪個 `next` 壞了
- [ ] 你能用 `ptype/o Node` + `x` 證明 `name` overflow 蓋到 `next`
- [ ] 你能畫出 Node 的記憶體佈局，解釋溢出路徑
- [ ] 你能提出修法（限制長度 / 加大緩衝 / 用動態配置）

## 期望輸出範例

```
$ ./detective
# 可能：sum = <錯誤的數>
# 或：Segmentation fault
# 或：卡住（無窮迴圈）

GDB 中走訪：
(gdb) print *list
$1 = {id = 1, name = "alice", next = 0x...}
(gdb) print *list->next->next
$2 = {id = 3, name = "christo", next = 0x7268706f...}  # next 變成了 ASCII！
                                                        # 0x7268706f = "ophr" 之類
```

關鍵線索：壞掉的 `next` 值看起來像 **ASCII 文字**（`christopher` 後半段的字元碼），這幾乎一定是「字串溢出蓋到指標」的指紋。

## 如果你卡住了

1. **症狀不穩定？** ASLR / heap 佈局讓每次結果不同。`set disable-randomization on`（預設就是）讓它穩定些；多跑幾次觀察共同點。
2. **怎麼知道哪個節點壞？** 從 `head` 開始一個個 `print *p`、`set $p = $p->next`，看到哪個 `next` 值「不像合理的 heap 位址」（合理的通常 `0x55...` 或 `0x...` 開頭且對齊）就抓到了。
3. **怎麼證明是 name 溢出？** `ptype/o Node` 看 `name` 和 `next` 的 offset——它們相鄰。`x/16xb &壞節點` dump 出來，看 `next` 那 8 byte 是不是 `christopher` 的字元延續。
4. **想抓改壞的瞬間？** 對那個節點的 `next` 下 watchpoint（Ch 13）：`watch 壞節點->next`，會停在 `strcpy` 那刻。

## 實作步驟建議

### Step 1：觀察症狀

```
$ ./detective          # 跑幾次，記錄症狀
(gdb) run              # 在 GDB 裡跑，看崩潰在哪
```

子目標：確定症狀類型（崩潰/迴圈/錯值），若崩潰則 `bt` 看在 `sum_ids` 哪一行。

### Step 2：理解 Node 佈局

```
(gdb) ptype/o Node
```

子目標：看到 `id`(offset 0)、`name`(offset 4, size 8)、`next`(offset 12 或 16，依對齊)。確認 `name` 之後緊接 `next`。

### Step 3：手動走訪 list

```
(gdb) break sum_ids
(gdb) run
(gdb) set $p = h
(gdb) print *$p          # 節點 1
(gdb) set $p = $p->next
(gdb) print *$p          # 節點 2 ... 一路走
```

子目標：走到第 3 個節點時，發現 `next` 值異常（像 ASCII）。

### Step 4：用記憶體層級證明溢出

```
(gdb) print &壞節點->name      # name 起始位址
(gdb) print &壞節點->next      # next 起始位址（緊接 name 之後）
(gdb) x/16xb 壞節點             # dump 整個節點的 raw bytes
(gdb) x/s &壞節點->name        # 把 name 當字串看，會看到 "christopher" 超出 8 byte
```

子目標：`x/16xb` 看到 `christopher\0` 的 byte 一路蓋過 `name[8]` 邊界進入 `next` 的位置。`x/s` 顯示完整的 "christopher" 證明它 12 byte。

### Step 5：抓改壞的瞬間（進階，用下一章的 watchpoint）

```
(gdb) break build
(gdb) run
(gdb) next 直到第三個 make 之前
(gdb) ... 取得第三個節點位址後 ...
(gdb) watch *(long*)&node3->next
(gdb) continue          # 停在 strcpy 把它蓋掉的瞬間
```

子目標：watchpoint 在 `strcpy` 寫越界的那刻觸發，`bt` 直指 `make` 的 `strcpy`——真兇當場落網。

## 完整參考解答

**自己查到 Step 4 再看。**

<details>
<summary>點開完整破案過程</summary>

### 佈局

```
(gdb) ptype/o Node
/* offset | size */
/*  0      |  4 */  int id;
/*  4      |  8 */  char name[8];
/* 12      |  4 */  (padding to 8-byte align for pointer)
/* 16      |  8 */  struct Node *next;
```

注意：因為指標要 8-byte 對齊，`name[8]`(offset 4-11) 之後有 padding 到 offset 16，`next` 在 16。所以 `name` 要溢出**超過 4 個 byte**（蓋過 offset 12-15 的 padding）才會碰到 `next`。"christopher" = 11 字元 + `\0` = 12 byte，從 offset 4 寫到 offset 15——剛好填滿到 padding 末端，第 12 個 byte（`\0`）落在 offset 15，**還沒**碰到 next(offset 16)。

> 教學轉折：所以這個特定字串可能**剛好沒踩到 next**！這正是 buffer overflow 最陰險的地方——差一個 byte 就從「無害」變「災難」。把字串改成 "christopherX"（12 字元）或更長，才會真正蓋進 next。這也是練習要你動手調的點：改字串長度，觀察「安全 → 踩到 padding → 踩到 next」的臨界。

### 用更長的名字重現

把 `make(3, "christopher")` 改成 `make(3, "christopherXY")`（13 字元，寫到 offset 17，蓋進 next 的第一個 byte）：

```
(gdb) break sum_ids
(gdb) run
(gdb) set $p = h
(gdb) print *$p
$1 = {id = 1, name = "alice", next = 0x5555555596b0}
(gdb) set $p = $p->next
(gdb) set $p = $p->next       # 走到第 3 個
(gdb) print *$p
$2 = {id = 3, name = "christo", next = 0x...59}   # next 高位被 'Y'(0x59) 蓋了！
(gdb) x/24xb $p
0x...:  0x03 0x00 0x00 0x00   # id=3
        0x63 0x68 0x72 0x69   # "chri"
        0x73 0x74 0x6f 0x70   # "stop"
        0x68 0x65 0x72 0x58   # "her" + 'X'(0x58) ← 蓋進 padding/邊界
        0x59 0x.. ...         # 'Y'(0x59) ← 蓋進 next！
(gdb) x/s &$p->name
0x...: "christopherXY"        # 證明字串 13 byte，遠超 name[8]
```

`next` 的低位 byte 變成 `0x59`（'Y'），指標被污染，`sum_ids` 下一次 `p = p->next` 就跳到一個亂位址 → 崩潰或讀垃圾。

### 抓改壞瞬間（watchpoint）

```
(gdb) break build
(gdb) run
(gdb) next                    # 跑到第 3 個 make 之前，記下將寫入的節點
... 取得 node3 位址，假設 0x...700 ...
(gdb) watch *(char*)(0x...700 + 16)    # 監視 next 的第一個 byte
(gdb) continue
Hardware watchpoint: ... 
Old value = 0 '\000'
New value = 89 'Y'
make (id=3, name=...) at detective.c:14
14          strcpy(n->name, name);     # ← 真兇！strcpy 寫越界
```

watchpoint 直接停在 `strcpy` 那行，`bt` 確認是 `make` 裡的 `strcpy` 把 `next` 蓋掉。案破。

### 修法

1. `strncpy(n->name, name, sizeof n->name - 1)`（截斷，保留 `\0`）
2. 把 `name` 改成 `char *name` + `strdup`（動態長度）
3. 加大 `name[]`（治標）
4. 用編譯期防護：`-fsanitize=address`（AddressSanitizer 會當場報 heap-buffer-overflow，Ch 對照工具）

**解答說明**：這題的教學核心有三層——(1) 用 convenience variable 當游標手動走訪資料結構；(2) 用 `x/xb` 在記憶體層級看穿「字串溢出 → 指標污染」，並認得「指標值像 ASCII」這個指紋；(3) 理解 struct padding 讓「差一個 byte」決定生死，這是真實 overflow 的陰險之處。watchpoint 把「結果」debug 變成「原因」debug——直接抓寫入的瞬間。

</details>

## 測試用例

| 名字長度 | 預期現象 | 說明 |
|---|---|---|
| `"bob"` (3) | 正常 | 在 name[8] 內 |
| `"christo"` (7) | 正常 | 剛好填滿 name[8]（含 \0 = 8） |
| `"christopher"` (11) | 可能正常 | 蓋到 padding 但未必碰 next |
| `"christopherXY"` (13) | next 污染 → 崩潰/亂值 | 蓋進 next |
| 超長 (>20) | 必崩 | 整個 next + 後續被蓋 |

## 延伸挑戰（加分）

1. **二元樹版**：把 list 改成 binary tree，故意讓某節點 `left`/`right` 被踩壞，用 GDB 遞迴走訪（配合 Ch 20 的命令語言寫走訪迴圈）找出壞枝。
2. **用 watchpoint 自動抓污染源**：對整個結構的關鍵指標下 watchpoint，不靠猜直接定位寫入點（Ch 13）。
3. **對照 AddressSanitizer**：`gcc -fsanitize=address -g` 重編，看 ASan 怎麼一行報出 heap-buffer-overflow 的精確位置，對比純 GDB 的偵探過程——體會「工具自動化 vs 手動偵查」的取捨。
4. **寫成 Python 自動走訪**（學完 Part 5 回來）：寫一個 GDB Python 指令 `checklist <head>`，自動走完整個 list 並標出「next 不像合法 heap 位址」的節點。這就是 Final Project 插件能力的雛形。

## 自我檢核

- [ ] 我能用 convenience variable 當游標，手動走訪任意 linked list / tree
- [ ] 我能用 `x/xb` 在記憶體層級看穿「字串溢出蓋掉相鄰欄位」
- [ ] 我認得「指標值看起來像 ASCII」是字串 overflow 的指紋
- [ ] 我理解 struct padding 為什麼讓 overflow 的後果「差一個 byte 天差地遠」
- [ ] 我能用 watchpoint 把「debug 結果」變成「debug 原因」

把「看穿狀態」練成本能後，Part 3 進入進階的執行控制：條件斷點、watchpoint、catchpoint、signal、多執行緒、多行程——讓 GDB 在精準的時機、精準的條件下停下來。

→ [Ch 12 條件斷點與 breakpoint commands](./12-conditional-breakpoints-and-commands.md)
