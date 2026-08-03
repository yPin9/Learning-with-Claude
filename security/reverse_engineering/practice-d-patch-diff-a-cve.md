# Practice D：Patch-Diff 一個 CVE

> **目標：** n-day 分析與 variant hunting 的核心技能。  
> CVE 發布後，你拿到的是 patch commit，不是漏洞說明。  
> 能不能從 source diff 或 binary diff 還原「原本缺什麼、為什麼能觸發、影響範圍到哪」——這是安全研究者的基本功，也是防禦方評估 patch 優先序的依據。

**環境：**

```
Ubuntu 22.04 / Debian 12（WSL 也可）
gcc 11+
binutils（objdump）
AddressSanitizer（gcc 內建，-fsanitize=address）
可選：diff、vimdiff、Ghidra（有 source 版不需要）
```

---

## 背景與動機

### 為什麼 patch-diff 是安全研究者必備技能

CVE 發布後，廠商給的 advisory 通常只說「heap buffer overflow in parse_foo()」，沒有 PoC，沒有觸發條件，沒有影響版本的精確說明。這時你能做的事只有一件：找到 patch commit，看改了什麼。

patch-diff 的用途分三層：

**第一層：理解漏洞成因。**  
從 patch 反推：修了什麼 → 原本缺什麼 → 原本缺的是什麼保護 → 漏洞的根因。這步做完，你才有辦法寫 PoC 或評估影響。

**第二層：variant hunting。**  
知道漏洞成因後，你要問「同樣的 pattern 有沒有出現在其他函式？」。真實世界的 CVE 很少是孤例——同一個 codebase 裡，犯同樣錯誤的函式通常不只一個。找 variant 的能力讓你把一個 CVE 變成多個，或者讓防禦方的 patch 更完整。

**第三層：評估 patch 品質。**  
patch 修對了嗎？修法有沒有副作用？有沒有新的邊界條件沒考慮到？這是 code review 的防禦工作，但做的人要有進攻視角才能問對問題。

這個練習用的是 source-level patch-diff——你同時有 vulnerable 和 patched 兩份 source。但流程完全模擬真實 n-day 工作：先編成 binary、strip 掉符號，再從 objdump 輸出找差異，最後用 ASAN 驗證你的分析是否正確。

有了這套流程，往後遇到沒有 source 的情況（只有兩個版本的 binary），你知道從哪裡著手。

---

## 題目設定

建立工作目錄：

```bash
mkdir -p ~/patch_diff_lab/vulnerable ~/patch_diff_lab/patched
```

### vulnerable/vuln_parser.c

```c
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    unsigned count;
    unsigned data[64];
} Record;

/* BUG: count 沒有上限檢查。
 * 如果呼叫者傳入 count > 64，memcpy 會寫出 r->data 的邊界，
 * 造成 heap buffer overflow。
 */
Record *parse_records(const unsigned *src, unsigned count) {
    Record *r = malloc(sizeof(Record));
    if (!r) return NULL;
    r->count = count;
    /* BUG: count * sizeof(unsigned) 可能超過 data[64] 的大小 */
    memcpy(r->data, src, count * sizeof(unsigned));
    return r;
}

int main(int argc, char **argv) {
    unsigned data[100];
    for (int i = 0; i < 100; i++) data[i] = i;

    /* 正常呼叫：count=5，安全 */
    Record *r = parse_records(data, 5);
    printf("count=%u data[0]=%u\n", r->count, r->data[0]);
    free(r);

    /* 觸發 bug：count=100 > 64，overflow */
    if (argc > 1) {
        r = parse_records(data, 100);
        printf("count=%u data[0]=%u\n", r->count, r->data[0]);
        free(r);
    }
    return 0;
}
```

將上面存到 `~/patch_diff_lab/vulnerable/vuln_parser.c`。

### patched/patched_parser.c

```c
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define MAX_RECORDS 64

typedef struct {
    unsigned count;
    unsigned data[64];
} Record;

/* PATCHED: 在 malloc 之前加了 bounds check */
Record *parse_records(const unsigned *src, unsigned count) {
    if (count > MAX_RECORDS) {   /* ← patch 加入的檢查 */
        return NULL;
    }
    Record *r = malloc(sizeof(Record));
    if (!r) return NULL;
    r->count = count;
    memcpy(r->data, src, count * sizeof(unsigned));
    return r;
}

int main(int argc, char **argv) {
    unsigned data[100];
    for (int i = 0; i < 100; i++) data[i] = i;

    Record *r = parse_records(data, 5);
    printf("count=%u data[0]=%u\n", r->count, r->data[0]);
    free(r);

    if (argc > 1) {
        r = parse_records(data, 100);
        if (!r)
            printf("parse_records rejected count=100 (bounds check)\n");
        else {
            printf("count=%u\n", r->count);
            free(r);
        }
    }
    return 0;
}
```

將上面存到 `~/patch_diff_lab/patched/patched_parser.c`。

---

## 你要完成的任務

五件事，按順序做：

**任務一：編譯兩版並 strip**

把兩版都編成 stripped binary，模擬你拿到的是「沒有符號的生產 binary」。

**任務二：用 objdump 找到目標函式**

在 strip 後的 binary 裡找到 `parse_records` 對應的函式（沒有名字，你要用特徵定位）。

**任務三：手動 diff 兩版 asm**

找出兩版 objdump 輸出的差異——哪幾行不一樣，新增了什麼指令。

**任務四：從 asm diff 反推漏洞**

解釋：
- 新增的 `cmp $0x40` 在做什麼
- 原版缺少這個檢查導致什麼問題
- `memcpy` 的長度參數怎麼算，overflow 的條件是什麼
- 攻擊者怎麼觸發這個 bug（需要什麼輸入）

**任務五：寫漏洞分析報告**

格式如下（報告存成 `~/patch_diff_lab/vuln_report.md`）：

```
# 漏洞分析報告

## 概要
函式：parse_records()
類型：heap buffer overflow（越界寫入）
根因：count 參數缺少上限檢查

## 觸發條件
呼叫 parse_records(src, count) 時，count > 64

## 影響
memcpy 寫入量 = count * 4 bytes
data[] 大小 = 64 * 4 = 256 bytes
當 count=100，寫入 400 bytes，overflow 144 bytes

## Patch 分析
patch 在 malloc 前加入：
  if (count > MAX_RECORDS) return NULL;
對應 asm：cmp $0x40, %edi → ja <early_return>

## Variant Hunting 建議
搜尋 codebase 所有使用 count/len/size 作參數並直接傳給 memcpy/memset 的函式，
確認每一個都有對應的上限檢查。

## ASAN 驗證
gcc -O1 -fsanitize=address -o vuln_asan vuln_parser.c
./vuln_asan trigger
→ ASAN: heap-buffer-overflow
```

---

## 驗收標準

- [ ] 兩版 binary 都成功編譯並 strip，`file` 確認為 stripped ELF
- [ ] 能在 stripped binary 裡用 objdump 找到 parse_records 對應的函式入口
- [ ] 能列出兩版 asm 的差異（至少找出 `cmp $0x40` 那段）
- [ ] 能解釋 `cmp $0x40,%esi` 對應 source 裡的 `if (count > MAX_RECORDS)`
- [ ] 能正確計算 overflow 的 byte 數（count=100 時多寫了多少）
- [ ] ASAN 版跑出越界寫入錯誤（`WRITE of size 400` 落在 `260-byte region` 外；類別標籤視版本為 `heap-buffer-overflow` 或 `unknown-crash`）
- [ ] patched 版執行結果顯示 `rejected count=100`
- [ ] 漏洞分析報告寫完，包含觸發條件、影響範圍、patch 分析三個部分

---

## 時限建議

90 分鐘。

- 前 20 分鐘：編譯、strip、第一次 objdump
- 中間 40 分鐘：asm diff，找出差異，理解每條指令
- 後 30 分鐘：ASAN 驗證 + 寫報告

第一次做 asm diff 很慢是正常的。目標不是快，是準——能準確說出每個差異對應哪行 source。

---

## 如果你卡住了

**卡點一：strip 後找不到 parse_records**

strip 後函式名消失，`objdump -d` 只會顯示 `<func1>` 之類的自動命名。

策略：不要找名字，找特徵。`parse_records` 會呼叫 `malloc` 和 `memcpy`。在 objdump 輸出裡搜尋 `call` 指令，找連續呼叫這兩個的函式。

```bash
objdump -d vuln_stripped | grep -n "call"
```

找到 `malloc@plt` 和 `memcpy@plt` 出現在同一個函式區塊裡，那就是目標。

**卡點二：兩版 asm 怎麼比**

最直接的方式：把兩版 objdump 輸出存成文字檔，用 `diff` 比。

```bash
objdump -d vuln_stripped   > asm_vuln.txt
objdump -d patched_stripped > asm_patched.txt
diff asm_vuln.txt asm_patched.txt
```

問題是地址偏移可能不同，讓 diff 顯示很多假差異。解法：只截取 parse_records 那個函式的部分，再 diff。

如果用 Ghidra，可以用 Version Tracking 功能自動對齊兩版函式，效率更高。

**卡點三：cmp $0x40 在做什麼**

`$0x40` = 64（十六進位）。`parse_records(const unsigned *src, unsigned count)` 的第一個整數參數 `src` 在 `%rdi`（低 32 位 `%edi`），**第二個參數 `count` 在 `%esi`**——所以你看到的是 `cmp $0x40,%esi`（拿 `count` 和 64 比）。別把 `%edi` 當成 `count`，那是 `src` 指標。

後面緊接著的跳轉指令（`ja`）決定 `count > 64` 時做什麼。實跑 gcc `-O1` 下，`count > 64`（`ja`，unsigned 大於）時跳到函式結尾，`%rbp` 已被清成 0，回傳 `%rax = 0` 就是 `return NULL`。

**卡點四：memcpy 的長度參數怎麼算**

source 裡是 `count * sizeof(unsigned)`。  
`sizeof(unsigned)` = 4。

所以 memcpy 寫入量 = count * 4 bytes。

`data[64]` 的大小 = 64 * 4 = 256 bytes。

當 count = 100：寫入 400 bytes，超出 256 bytes，overflow 144 bytes。

在 asm 裡你會看到：`lea (%rdx,%rdx,...)` 或 `shl $0x2,%esi` 之類把 count 乘以 4 的操作，那就是計算 memcpy 第三個參數的地方。

**卡點五：現實中怎麼觸發**

這個 bug 的觸發條件很簡單：呼叫端控制 `count` 參數的值，而且沒有獨立的驗證層。

現實場景：網路協定裡的「length + data」欄位。攻擊者送一個 packet，裡面 `count` 欄位填 `0xFFFFFFFF`——不只 overflow，還可能因為 `count * 4` 整數溢位繞回到小數值（但本例用的是 `unsigned`，且 64-bit 不會溢位，主要風險是寫出 data[] 邊界）。

實際 CVE 裡更常見的是：`count` 來自用戶上傳的檔案、網路封包、IPC 訊息——任何外部輸入都是威脅面。

---

## 實作步驟建議

### 階段一：建立 binary（15 分鐘）

目標：產生四個檔案——兩個有符號版（驗證用）、兩個 stripped 版（練習用）。

```bash
cd ~/patch_diff_lab

# 有符號版（保留 debug info，用來對照確認函式位置）
gcc -O1 -g -o vuln_sym   vulnerable/vuln_parser.c
gcc -O1 -g -o patched_sym patched/patched_parser.c

# stripped 版（模擬真實情境）
gcc -O1 -o vuln_stripped   vulnerable/vuln_parser.c
gcc -O1 -o patched_stripped patched/patched_parser.c
strip vuln_stripped patched_stripped

# 確認 strip 有效
file vuln_stripped patched_stripped
# 應該看到：stripped

# ASAN 版（最後驗證用）
gcc -O1 -fsanitize=address -o vuln_asan vulnerable/vuln_parser.c
```

產出：4 個 binary + 1 個 ASAN binary。

### 階段二：objdump 找目標函式（20 分鐘）

先在有符號版確認 parse_records 的位置和特徵：

```bash
# 有符號版：直接看函式
objdump -d vuln_sym | grep -A 40 "<parse_records>"

# 記住：這個函式呼叫 malloc 和 memcpy，大約幾十行
```

再到 stripped 版，用同樣特徵找：

```bash
# 找所有 call 指令，定位 malloc+memcpy 相鄰的函式
objdump -d vuln_stripped > vuln_asm.txt
objdump -d patched_stripped > patched_asm.txt

grep -n "malloc\|memcpy\|call" vuln_asm.txt | head -40
```

定位到 parse_records 的起始地址後，截出該函式的 asm：

```bash
# 假設函式從地址 0x1149 開始（你實際看到的會不同）
# 截出函式區塊（從函式開頭到下一個函式開頭）
```

產出：兩版 parse_records 的 asm 文字，標上行號。

### 階段三：手動 diff asm（25 分鐘）

把兩版 parse_records asm 截出來分別存檔，再 diff：

```bash
# 截取 parse_records 部分（依你看到的地址調整）
# vuln 版大約截出 20-30 行
# patched 版多了幾行（bounds check 的 cmp + jmp）

diff vuln_parse.txt patched_parse.txt
```

記錄每個差異：
1. patched 版開頭多了哪幾行指令？
2. 這幾行做了什麼？
3. 跳轉目標是哪裡（return NULL 的地方）？

填寫 diff 記錄表：

```
差異位置   | vuln 版    | patched 版              | 對應 source
-----------+------------+------------------------+------------------
函式入口後 | (無)       | cmp $0x40,%edi         | if (count > 64)
+1 行      | (無)       | ja <return_null>       | return NULL;
...        | call malloc| call malloc            | 相同
```

產出：填完的 diff 記錄表。

### 階段四：ASAN 驗證（15 分鐘）

跑 ASAN 版，確認漏洞真的存在：

```bash
cd ~/patch_diff_lab

# 正常呼叫：不觸發 bug
./vuln_asan
# 預期輸出：count=5 data[0]=0

# 觸發 bug：count=100 > 64
./vuln_asan trigger
# 預期輸出：ASAN 報越界寫入（WRITE of size 400 落在 260-byte region 外）+ stack trace
```

同樣驗證 patched 版：

```bash
./patched_stripped trigger
# 預期輸出：parse_records rejected count=100 (bounds check)
```

產出：ASAN 錯誤輸出截圖或文字記錄。

### 階段五：寫報告（15 分鐘）

按前面的格式寫 `vuln_report.md`。重點是「影響範圍」那段要有數字：overflow 了多少 bytes、可能覆蓋什麼（後面的 heap chunk header 或其他資料）。

---

## 完整參考解答

<details>
<summary>先自己做完再看。看答案前確認你已經跑過 ASAN 並看到 heap-buffer-overflow。</summary>

### 編譯與 strip

```bash
cd ~/patch_diff_lab

gcc -O1 -g -o vuln_sym   vulnerable/vuln_parser.c
gcc -O1 -g -o patched_sym patched/patched_parser.c
gcc -O1 -o vuln_stripped   vulnerable/vuln_parser.c
gcc -O1 -o patched_stripped patched/patched_parser.c
strip vuln_stripped patched_stripped
gcc -O1 -fsanitize=address -o vuln_asan vulnerable/vuln_parser.c

$ file vuln_stripped
vuln_stripped: ELF 64-bit LSB pie executable, x86-64, ..., stripped
```

### objdump 輸出對比

有符號版確認函式特徵（地址依你的系統而異）：

以下是 Linux x86-64、gcc 11.4、`-O1 -c` 實跑輸出（用 `.o` 看，位址從 0 起、無 PLT 偏移干擾）。vuln 版 `objdump -d vuln.o`：

```
0000000000000000 <parse_records>:
   0:   f3 0f 1e fa          endbr64
   4:   41 54                push   %r12
   6:   55                   push   %rbp
   7:   53                   push   %rbx
   8:   49 89 fc             mov    %rdi,%r12     # 存 src
   b:   89 f3                mov    %esi,%ebx     # 存 count
   d:   bf 04 01 00 00       mov    $0x104,%edi   # sizeof(Record)=0x104=260
  12:   e8 00 00 00 00       call   17            # call malloc（reloc 待填）
  17:   48 89 c5             mov    %rax,%rbp
  1a:   48 85 c0             test   %rax,%rax
  1d:   74 19                je     38            # malloc 失敗 → return NULL
  1f:   89 18                mov    %ebx,(%rax)   # r->count = count
  21:   89 da                mov    %ebx,%edx
  23:   48 c1 e2 02          shl    $0x2,%rdx     # count * 4
  27:   48 8d 78 04          lea    0x4(%rax),%rdi # &r->data[0]
  2b:   b9 00 01 00 00       mov    $0x100,%ecx
  30:   4c 89 e6             mov    %r12,%rsi
  33:   e8 00 00 00 00       call   38            # call memcpy —— 長度無上限
  38:   48 89 e8             mov    %rbp,%rax
  3b:   5b                   pop    %rbx
  3c:   5d                   pop    %rbp
  3d:   41 5c                pop    %r12
  3f:   c3                   ret
```

patched 版 `objdump -d patched.o`（注意開頭多了三條）：

```
0000000000000000 <parse_records>:
   0:   f3 0f 1e fa          endbr64
   4:   41 54                push   %r12
   6:   55                   push   %rbp
   7:   53                   push   %rbx
   8:   bd 00 00 00 00       mov    $0x0,%ebp      # ← PATCH: 預先把回傳值設 0(NULL)
   d:   83 fe 40             cmp    $0x40,%esi     # ← PATCH: count vs 64
  10:   77 30                ja     42             # ← PATCH: count > 64 → 跳到結尾回傳 NULL
  12:   49 89 fc             mov    %rdi,%r12
  15:   89 f3                mov    %esi,%ebx
  17:   bf 04 01 00 00       mov    $0x104,%edi    # sizeof(Record)=260（同 vuln 版）
  1c:   e8 00 00 00 00       call   21             # call malloc
  21:   48 89 c5             mov    %rax,%rbp
  24:   48 85 c0             test   %rax,%rax
  27:   74 19                je     42
  29:   89 18                mov    %ebx,(%rax)
  2b:   89 da                mov    %ebx,%edx
  2d:   48 c1 e2 02          shl    $0x2,%rdx      # count * 4（同 vuln 版）
  31:   48 8d 78 04          lea    0x4(%rax),%rdi
  35:   b9 00 01 00 00       mov    $0x100,%ecx
  3a:   4c 89 e6             mov    %r12,%rsi
  3d:   e8 00 00 00 00       call   42             # call memcpy
  42:   48 89 e8             mov    %rbp,%rax       # 回傳 %rbp（成功=物件指標，被 ja 跳來=0）
  45:   5b                   pop    %rbx
  46:   5d                   pop    %rbp
  47:   41 5c                pop    %r12
  49:   c3                   ret
```

注意：實際位址依你的 gcc 版本、`-O` 等級、是否 strip 而異，重要的是指令序列。用 `-O0` 編的話 diff 更好看（bounds check 會是 `cmpl $0x40,-0x14(%rbp); ja`，memcpy 保持 `call`），適合第一次對照。

**diff 重點：**

```
vuln 版 parse_records 開頭：
  endbr64
  push %rbp
  ...（直接進 malloc）

patched 版 parse_records 開頭：
  endbr64
  cmp $0x40, %esi    ← 新增：把 count（第2參數，在 %esi）和 64 比較
  ja  <return_null>  ← 新增：count > 64 就跳到 return NULL
  push %rbp
  ...（才進 malloc）
```

`$0x40` = 64 = `MAX_RECORDS`，一對一對應 source 裡的 `if (count > MAX_RECORDS)`。

### shl $0x2 解析

```
shl $0x2, %edx
```

左移 2 = 乘以 4 = `sizeof(unsigned)`。

這行把 `count` 變成 `memcpy` 的第三個參數（長度）。

vuln 版裡，`count` 沒有被限制，直接進入這個計算。count=100 時：

```
memcpy 長度   = 100 * 4 = 400 bytes
r->data 大小  = 64 * 4  = 256 bytes   → 越過 data 陣列 400-256 = 144 bytes
整塊配置大小  = sizeof(Record) = 260 bytes（4 + 256）
                                      → 越過整塊 malloc 配置 400-260 = 140 bytes
```

兩個數字都對，只是看的邊界不同：C 語意上你溢出 `data[64]` 陣列 144 bytes；ASAN 從 heap 配置的角度看，你寫到 260-byte 區塊尾端外 140 bytes（`memcpy` 前 4 bytes 落在 `r->count` 上，仍在配置內）。越界的部分會覆蓋 heap 裡 Record 物件後面的資料——可能是下一個 chunk 的 header，可能是其他物件，取決於 heap 當時的佈局。

### ASAN 驗證

用 `gcc -O1 -fsanitize=address -o vuln_asan vuln_parser.c` 編，帶參數觸發。以下是實跑輸出（節錄）：

```
$ ./vuln_asan trigger
count=5 data[0]=0
=================================================================
==412326==ERROR: AddressSanitizer: unknown-crash on address 0x5120000001c4 ...
WRITE of size 400 at 0x5120000001c4 thread T0
    #0 ... in parse_records (/tmp/vuln_asan+0x1402)
    #1 ... in main (/tmp/vuln_asan+0x15db)
    #2 ... in __libc_start_call_main ...
    #3 ... in __libc_start_main_impl ...
    #4 ... in _start ...

0x5120000002c4 is located 0 bytes to the right of 260-byte region [0x5120000001c0,0x5120000002c4)
allocated by thread T0 here:
    #0 ... in __interceptor_malloc ...
    #1 ... in parse_records (/tmp/vuln_asan+0x12ff)

SUMMARY: AddressSanitizer: unknown-crash (/tmp/vuln_asan+0x1402) in parse_records
```

重點看幾件事：

- **`WRITE of size 400`**——`memcpy` 一口氣寫 400 bytes（= count 100 × 4）。
- **`260-byte region`**——被寫爆的是一塊 260-byte 的 heap 配置，正是 `sizeof(Record)` = `count`(4) + `data[64]`(256) = **260**（`0x104`，就是前面 objdump 看到的 `mov $0x104,%edi`）。
- **`0 bytes to the right`**——越界起點剛好在這塊配置的尾端外，教科書式的 heap 線性溢位。
- 溢位量 = 400 − 260 = **140 bytes** 寫進了不屬於這塊配置的記憶體。

（注意 ASAN 這裡把類別標成 `unknown-crash` 而非典型的 `heap-buffer-overflow` 字串——當越界正好落在配置尾端相鄰的 redzone 型態時，某些 ASAN 版本會這樣分類。無論標籤是哪個，`WRITE of size 400` 落在 `260-byte region` 之外這件事就是鐵證：你的 `memcpy` 寫爆了。不同 gcc/clang 版本可能顯示 `heap-buffer-overflow`，語意相同。）

```bash
$ ./patched_stripped trigger
count=5 data[0]=0
parse_records rejected count=100 (bounds check)
```

patched 版正確拒絕。

### 漏洞分析報告範本

```markdown
# 漏洞分析報告

## 概要
函式：parse_records()
漏洞類型：heap buffer overflow（越界寫入）
根因：count 參數缺少上限檢查，直接用於 memcpy 長度計算

## 觸發條件
呼叫 parse_records(src, count) 時，count > 64（MAX_RECORDS）。
觸發需要攻擊者能控制 count 參數的值。

## 影響分析
- memcpy 寫入量：count * 4 bytes
- r->data[] 容量：64 * 4 = 256 bytes
- count=100 時：寫入 400 bytes，overflow 144 bytes
- 越界寫入的目標：heap 中 Record 物件之後的記憶體

潛在影響取決於 heap 佈局：
- 覆蓋相鄰 chunk 的 metadata → heap corruption
- 覆蓋相鄰物件的資料 → 資料竄改
- 精心控制的 overflow → 可能達成任意程式碼執行

## Patch 分析
patch commit 在 malloc 前加入：

  if (count > MAX_RECORDS) {
      return NULL;
  }

對應 asm（patched 版函式入口）：
  cmp  $0x40, %esi    ; count vs 64
  ja   <return_null>  ; count > 64 → return NULL

patch 策略：快速失敗（fail fast），在資源分配前驗證輸入。
評估：patch 正確修復了這個特定漏洞。

## Variant Hunting 建議
搜尋同一 codebase 所有以下 pattern：
  memcpy(dst, src, N * sizeof(T))
  memset(dst, val, N * sizeof(T))

其中 N 來自外部輸入，確認每個都有 if (N > MAX_N) 的保護。
搜尋方法：grep + semgrep rule，或 CodeQL query。

## ASAN 驗證
gcc -O1 -fsanitize=address -o vuln_asan vuln_parser.c
./vuln_asan trigger
結果：ASAN: heap-buffer-overflow in memcpy，確認漏洞可觸發。
```

### 在 stripped binary 裡定位函式（完整流程）

```bash
# step 1: 找所有外部呼叫
objdump -d vuln_stripped | grep "call.*plt" | sort -u

# step 2: 找 malloc 和 memcpy 在同一函式裡的區塊
objdump -d vuln_stripped | awk '
  /^[0-9a-f]+ <[^>]+>:/ { fn = $0; block = ""; }
  { block = block "\n" $0 }
  /malloc/ && /memcpy/ { print fn; print block }
'
# 這個 awk 不完美，但思路對：找同時含兩個符號的函式區塊

# step 3: 手動確認
# 看到疑似函式後，用地址截出來核對指令序列
```

</details>

---

## 延伸挑戰

### 挑戰一：variant hunting

在 vuln_parser.c 加入以下函式，再重複整個 patch-diff 流程：

```c
// 另一個有相同 pattern 的函式
char *parse_names(const char *src, unsigned len) {
    char *buf = malloc(256);
    if (!buf) return NULL;
    memcpy(buf, src, len);   // 同樣缺 bounds check
    return buf;
}
```

問題：
- 這個 bug 的觸發條件和 parse_records 一樣嗎？
- 如果 `len` 是 `unsigned`，有沒有整數溢位的額外風險？
- 你能寫一個 semgrep rule 找出 codebase 裡所有這類 pattern 嗎？

### 挑戰二：寫 fuzzer

用 libFuzzer 或暴力 fuzzer 找這個 bug，不依賴 ASAN 標示的位置：

```c
// fuzzer entry point
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 4) return 0;
    unsigned count = *(unsigned *)data;
    // 你的 fuzzer 邏輯
}
```

編譯：`clang -fsanitize=address,fuzzer -O1 -o fuzzer vuln_parser.c`

問：fuzzer 多快能找到 count=65 的 overflow？

### 挑戰三：沒有 ASAN 的情況下用 gdb 觀察 overflow

```bash
# 正常版 binary，用 gdb 在 memcpy 設 watchpoint
gdb ./vuln_stripped
(gdb) b *<parse_records 地址>
(gdb) r trigger
(gdb) p $rsi           # 看 count 值
(gdb) p $rdx           # 看 memcpy 長度
# 在 data[] 邊界設 watchpoint
(gdb) watch -l *<data[] 邊界地址>
(gdb) c
# 觸發 watchpoint 表示有越界寫入
```

比 ASAN 麻煩，但在沒有 sanitizer 的生產 binary 上這是你能用的方式。

---

## 自我檢核

- [ ] 我能在 stripped binary 裡，不靠符號名稱，只靠呼叫 malloc+memcpy 的特徵定位到目標函式
- [ ] 我能解釋 `cmp $0x40,%esi` + `ja <addr>` 這兩行合在一起做的事，對應到哪行 C source
- [ ] 我能計算出 count=100 時 overflow 的精確 byte 數，並說明為什麼是 144 bytes
- [ ] 我知道 ASAN 輸出裡「260-byte region」的 260 從哪來（= sizeof(Record) = 4 + 64×4），也知道 memcpy 寫 400 bytes、越過整塊配置 140 bytes / 越過 data 陣列 144 bytes 的差別
- [ ] 我能說明：如果要找這個 codebase 裡的 variant，我會搜什麼、用什麼工具

---

patch-diff 的核心能力就是這個：拿到兩個 binary，在沒有 source、沒有符號的情況下，從 asm 差異還原出「改了什麼、為什麼改、原本的問題是什麼」。這個練習有 source 版輔助，讓你能交叉驗證。真實的 n-day 工作裡，你只有 binary diff 和 CVE 標題——從那裡出發，能說出完整的漏洞故事，才算真正掌握了這個技能。

Part 4 的工程化與自動化到此完整。接下來 Part 5 把 Part 0–4 的所有技巧串成一次完整的冷啟動攻堅，然後畢業。

→ [Ch 31 完整攻堅實況：冷逆一個 strip binary](./31-full-attack-live.md)
