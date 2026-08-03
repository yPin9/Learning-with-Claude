# 練習 C — 逆一個檔案格式並寫出 parser

> **目標**：給你一個 stripped binary，任務是靜態逆向還原它的二進位格式規格，並寫出一個獨立的 parser 能正確解析原始 binary 產生的檔案。這是 Ch 19（格式逆向）的 ground-truth 練習。

> **時限建議**：120 分鐘（靜態逆向 60 分鐘 + 寫 parser 30 分鐘 + 驗證 30 分鐘）。超過不代表失敗，記下卡在哪裡比趕完更有價值。

## 任務規格

### 步驟一：準備目標 binary

```bash
# 編譯出題程式，產生 stripped target 和一個樣本檔
$ cat > /tmp/practice_c/target.c << 'EOF'
/* 這是出題 source，寫 parser 前不要看 */
/* 內容見下方 <details>（解答區）*/
EOF

# 實際上，從下方解答區的 source 編譯：
$ gcc -O2 -o /tmp/practice_c/target /tmp/practice_c/target.c
$ strip /tmp/practice_c/target
$ /tmp/practice_c/target         # 執行後會在當前目錄產生 sample.rvrs
```

你拿到的是：
- `/tmp/practice_c/target`（stripped binary，只有 15 KB，有 `fopen`/`fread`/`fclose`/`printf` 等動態 import）
- `/tmp/practice_c/sample.rvrs`（target 產生的樣本檔，你的 parser 要能正確解析它）

`target` 執行後的輸出（這是你的 parser 要重現的結果）：

```
flags=0x0001 entries=2 data_size=11
  type=0x01 key=100 value_len=7 value=reverse
  type=0x02 key=200 value_len=4 value=deadbeef
checksum: file=0x1fd90634 calc=0x1fd90634 OK
```

### 步驟二：逆向任務

不看 source，從 stripped binary 還原：

1. **格式規格**：magic bytes 是什麼？header 有哪些欄位（大小、型別、順序）？record 格式？trailer？大小端？
2. **checksum 演算法**：是哪一種 checksum？checksunm 計算的是哪些位元組的 sum？
3. **type 欄位的語意**：`type=0x01` 和 `type=0x02` 各代表什麼資料格式？

### 步驟三：寫一個 parser

寫一個 C 程式 `parser.c`，能：
- 接受一個 `.rvrs` 檔案路徑作為命令列參數
- 輸出和 `target` 完全相同的格式
- 對 checksum 不符的檔案印出 `MISMATCH`
- 對 bad magic 印出錯誤並以非零 exit code 結束

### 步驟四：驗證

```bash
$ ./parser sample.rvrs
# 輸出應和 target 的輸出完全一致

# 進一步：自己造一個新的 .rvrs 檔，餵給 target 和 parser，輸出應相同
```

## 如果你卡住了

**卡點一：不知道從哪裡開始**
- 先跑 `strings target`，記下所有可讀字串（`"RVRS"`、`"invalid magic"`、`"flags=%..."`…）
- 這些字串是錨點——哪個函式引用了它們就是 parser。

**卡點二：找不到 parse 函式**
- 用 `readelf -h target` 找 entry point → 看 `_start` 的第一個 `mov $addr,%rdi` call = main
- main 會呼叫 write_sample 和 parse_file，找引用了 `"flags=0x%04x"` 的函式就是 parse_file

**卡點三：fread 的 size 和 count 算不清楚**
- 記住 `fread(ptr, size, nmemb, f)` 的參數在 x86-64 是 `(rdi, rsi, rdx, rcx)`
- 真正讀的 bytes = `rsi`（size per element）× `rdx`（count）
- `-O0` 的 binary 裡每個 `fread` 呼叫前都能看到 `mov $N,%esi`

**卡點四：checksum 算法認不出來**
- 先看 checksum 計算函式裡有沒有迴圈和特定常數（CRC = `0xEDB88320`/`0x1021`；Fletcher = `65535`；簡單 sum = 沒有乘法/多項式）
- 看有沒有**兩個累積器**（s1/s2）——兩個累積器、最後 `(s2 << 16) | s1` = Fletcher-32

**卡點五：parser 寫好了但輸出不一樣**
- 比對 hex 輸出：`xxd your_output.rvrs` 和 `xxd sample.rvrs`，看第一個不同的 byte 在哪裡
- 最常見問題：大小端（直接 `fread` 到 uint16_t 是 little-endian，手動組合 bytes 要確認方向）

## 分段步驟（建議工作流程）

### Phase 1：偵察（15 分鐘）

```bash
$ file target
$ strings target
$ nm target 2>&1 | head -5      # 確認 stripped
$ readelf -h target | grep Entry
$ readelf -S target | grep -E '\.text|\.rodata|\.data'
```

記下：
- magic string 是什麼（在 strings 輸出裡）
- printf format string 的樣式（告訴你 header 欄位名和格式）
- 有哪些 libc 函式（fread / fwrite / malloc / memcmp 等）

### Phase 2：靜態分析（30 分鐘）

```bash
$ objdump -d target | less
```

找到 `_start` → main → parse_file（靠字串 xref）。

記錄 parse_file 裡的 fread 序列：

```
第 1 個 fread：size = ?，count = ?   → header 第 1 個欄位
第 2 個 fread：size = ?，count = ?   → ...
...
```

特別注意：
- `memcmp` 前的那個 fread = 讀 magic（比對 .rodata 裡的常數）
- 迴圈裡的 fread = record 格式
- 迴圈後的 fread = trailer

### Phase 3：辨識 checksum（15 分鐘）

找 `fletcher32` 或等效函式：

```bash
$ objdump -d target | grep -B 2 -A 30 '65535\|0xffff'
```

看迴圈結構：單一累積器 = 簡單 sum；雙累積器 = Fletcher。

### Phase 4：寫 parser（30 分鐘）

把逆出的格式規格，翻譯成 C 的 `fread` 序列：

```c
/* 骨架 */
fread(magic, 4, 1, f);   /* 記 size=4 → 4 bytes */
memcmp(magic, "????", 4); /* magic 是什麼 */
fread(&flags,       2, 1, f);  /* size=2 → uint16 */
...
```

### Phase 5：驗證（30 分鐘）

```bash
$ gcc -o parser parser.c
$ ./parser sample.rvrs
# 對照 target 的輸出

# 自製測試檔
$ ./your_generator test2.rvrs
$ ./parser test2.rvrs
$ ./target test2.rvrs     # 應一致
```

---

## 參考解答

**寫完再看！不要偷看**——逆向的價值在過程，不在結果。

<details>
<summary>展開：出題 source（格式的真相）</summary>

```c
/* practice_target.c — 出題 source
 *
 * 自訂二進位格式 "RVRS"
 * Header:   magic[4] + flags[2] + num_entries[2] + data_size[4]   (all LE)
 * Entries:  type[1] + key[2] + value_len[2] + value[value_len]  (repeated)
 * Trailer:  checksum[4]  — Fletcher-32 of all value bytes concatenated
 * 全部 little-endian
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

/* Fletcher-32 checksum */
static uint32_t fletcher32(const uint8_t *data, size_t len) {
    uint32_t s1 = 0, s2 = 0;
    for (size_t i = 0; i < len; i++) {
        s1 = (s1 + data[i]) % 65535;
        s2 = (s2 + s1) % 65535;
    }
    return (s2 << 16) | s1;
}

static void process_entry(uint8_t type, uint16_t key,
                           const uint8_t *val, uint16_t vlen) {
    printf("  type=0x%02x key=%u value_len=%u value=", type, key, vlen);
    if (type == 0x01) {
        for (int i = 0; i < vlen && val[i]; i++) putchar(val[i]);
    } else if (type == 0x02) {
        for (int i = 0; i < vlen; i++) printf("%02x", val[i]);
    } else {
        printf("(unknown type)");
    }
    printf("\n");
}

static int parse_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return -1; }

    char magic[4];
    fread(magic, 4, 1, f);                      /* 4 bytes magic */
    if (memcmp(magic, "RVRS", 4) != 0) {
        fprintf(stderr, "invalid magic\n"); fclose(f); return -1;
    }

    uint16_t flags, num_entries;
    uint32_t data_size;
    fread(&flags,       2, 1, f);               /* 2 bytes flags */
    fread(&num_entries, 2, 1, f);               /* 2 bytes num_entries */
    fread(&data_size,   4, 1, f);               /* 4 bytes data_size */

    printf("flags=0x%04x entries=%u data_size=%u\n",
           flags, num_entries, data_size);

    uint8_t *all_data = malloc(data_size);
    size_t   data_pos = 0;

    for (int i = 0; i < num_entries; i++) {
        uint8_t  type;
        uint16_t key, vlen;
        fread(&type, 1, 1, f);                  /* 1 byte type */
        fread(&key,  2, 1, f);                  /* 2 bytes key */
        fread(&vlen, 2, 1, f);                  /* 2 bytes value_len */
        uint8_t *val = malloc(vlen);
        fread(val, vlen, 1, f);                 /* vlen bytes value */
        process_entry(type, key, val, vlen);
        if (data_pos + vlen <= data_size) {
            memcpy(all_data + data_pos, val, vlen);
            data_pos += vlen;
        }
        free(val);
    }

    uint32_t ck_file;
    fread(&ck_file, 4, 1, f);                   /* 4 bytes checksum */
    uint32_t ck_calc = fletcher32(all_data, data_pos);
    printf("checksum: file=0x%08x calc=0x%08x %s\n",
           ck_file, ck_calc, ck_file == ck_calc ? "OK" : "MISMATCH");
    free(all_data);
    fclose(f);
    return 0;
}

static int write_sample(const char *path) {
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    const char *name = "reverse";
    uint8_t bin_val[] = {0xDE,0xAD,0xBE,0xEF};
    uint16_t vlen1 = (uint16_t)strlen(name), vlen2 = 4;
    uint32_t total_data = vlen1 + vlen2;
    fwrite("RVRS", 4, 1, f);
    uint16_t flags = 0x0001, ne = 2;
    uint32_t ds = total_data;
    fwrite(&flags, 2, 1, f); fwrite(&ne, 2, 1, f); fwrite(&ds, 4, 1, f);
    uint8_t t1=0x01; uint16_t k1=100;
    fwrite(&t1,1,1,f); fwrite(&k1,2,1,f); fwrite(&vlen1,2,1,f); fwrite(name,vlen1,1,f);
    uint8_t t2=0x02; uint16_t k2=200;
    fwrite(&t2,1,1,f); fwrite(&k2,2,1,f); fwrite(&vlen2,2,1,f); fwrite(bin_val,4,1,f);
    uint8_t all[64]; memcpy(all,name,vlen1); memcpy(all+vlen1,bin_val,4);
    uint32_t ck = fletcher32(all, total_data);
    fwrite(&ck, 4, 1, f);
    fclose(f); return 0;
}

int main(void) {
    write_sample("sample.rvrs");
    parse_file("sample.rvrs");
    return 0;
}
```

</details>

<details>
<summary>展開：逆向過程詳解（真實 objdump 輸出 + 格式還原）</summary>

### Step 1：偵察結果（真跑）

```bash
$ strings target
...
RVRS              ← magic bytes！
invalid magic     ← magic 驗證失敗的訊息
flags=0x%04x entries=%u data_size=%u    ← 三個 header 欄位名和格式
  type=0x%02x key=%u value_len=%u value=   ← record 欄位
checksum: file=0x%08x calc=0x%08x %s   ← checksum 是 32-bit（%08x）
(unknown type)
```

從 format string 可以直接讀出：
- `flags` 是 16-bit（`0x%04x` = 4 hex digits = 16 bits）
- `entries` 是無號整數
- `data_size` 是無號整數
- record 有 `type`（8-bit，`%02x`）、`key`（無號整數）、`value_len`、`value`
- checksum 是 32-bit（`%08x`）

### Step 2：定位 main 和 parse_file（真跑）

```bash
$ readelf -h target | grep Entry
  Entry point address:  0x1880

$ objdump -d target | grep -A 10 '1880:'
```

`_start` 在 `0x1880`，找 `mov $addr,%rdi` 前的 call → main 在哪。

用字串 xref 找 parse_file：

```bash
$ objdump -d target | grep -B 5 'flags=0x'
# 找到引用 "flags=0x%04x" format string 的 lea 指令 → 所在函式 = parse_file
```

### Step 3：追蹤 parse_file 的 fread 序列（真實 `-O0` objdump 輸出）

以下是 `-O0`（非 strip）版本的 `parse_file` 開頭，fread 序列清晰可讀：

```asm
0000000000001485 <parse_file>:
    ...
    ; 第 1 個 fread：讀 magic（size=4，count=1）
    14e6:  mov    $0x1,%edx           ; count = 1
    14eb:  mov    $0x4,%esi           ; size = 4  ← 4 bytes！
    14f3:  call   1130 <fread@plt>

    ; memcmp 比對 magic
    14fc:  mov    $0x4,%edx           ; len = 4
    1501:  lea    0xb40(%rip),%rcx    ; → .rodata：指向 "RVRS"
    150e:  call   1180 <memcmp@plt>
    1515:  je     1550                ; magic OK

    ; 第 2 個 fread：flags（size=2，count=1）
    155b:  mov    $0x1,%edx
    1560:  mov    $0x2,%esi           ; size = 2  ← uint16!
    1568:  call   1130 <fread@plt>

    ; 第 3 個 fread：num_entries（size=2，count=1）
    1578:  mov    $0x1,%edx
    157d:  mov    $0x2,%esi           ; size = 2  ← uint16!
    1585:  call   1130 <fread@plt>

    ; 第 4 個 fread：data_size（size=4，count=1）
    1595:  mov    $0x1,%edx
    159a:  mov    $0x4,%esi           ; size = 4  ← uint32!
    15a2:  call   1130 <fread@plt>
```

**Header 確認：magic[4] + flags[2] + num_entries[2] + data_size[4] = 12 bytes**

```bash
$ xxd sample.rvrs | head -3
00000000: 5256 5253 0100 0200 0b00 0000 ...
# 52 56 52 53 = "RVRS"（magic）
# 01 00 = 0x0001（flags，LE uint16）
# 02 00 = 2（num_entries，LE uint16）
# 0b 00 00 00 = 11（data_size，LE uint32）
```

繼續追 fread 序列（record 部分，在迴圈裡）：

```asm
    ; 第 5 個 fread：type（size=1，count=1）
    fread(&type, 1, 1, f)   ; 1 byte

    ; 第 6 個 fread：key（size=2，count=1）
    fread(&key,  2, 1, f)   ; 2 bytes (uint16)

    ; 第 7 個 fread：vlen（size=2，count=1）
    fread(&vlen, 2, 1, f)   ; 2 bytes (uint16)

    ; 第 8 個 fread：value（size=vlen，count=1）
    fread(val, vlen, 1, f)  ; vlen bytes（動態長度）
```

**Record 確認：type[1] + key[2] + value_len[2] + value[value_len]**

```
sample.rvrs 的 record 部分：
offset 12: 01        → type = 0x01（string）
offset 13: 64 00     → key = 100（LE）
offset 15: 07 00     → value_len = 7（LE）
offset 17: 72 65 76 65 72 73 65   → "reverse"（7 bytes）

offset 24: 02        → type = 0x02（binary）
offset 25: c8 00     → key = 200（LE）
offset 27: 04 00     → value_len = 4（LE）
offset 29: de ad be ef  → 4 bytes binary data

offset 33: 34 06 d9 1f → checksum = 0x1fd90634（LE uint32）
```

### Step 4：辨識 checksum（Fletcher-32）

找計算 checksum 的函式，搜尋 `65535`（`0xffff`）：

```bash
$ objdump -d target | grep -A 30 '0xffff\|65535'
```

看到**雙累積器**迴圈：

```asm
; fletcher32 函式的核心迴圈
; s1 = 0, s2 = 0 初始化
.loop:
    ; s1 = (s1 + data[i]) % 65535
    movzbl  (%rbx+%rax), %edx    ; data[i]
    add     %edx, %ecx           ; s1 += data[i]
    ; 模 65535：
    imul    ...                   ; or: sub/cmp/cmov pattern for % 65535
    
    ; s2 = (s2 + s1) % 65535
    add     %ecx, %esi
    ...

; 最後：return (s2 << 16) | s1
    shl     $0x10, %esi           ; s2 <<= 16
    or      %ecx, %esi            ; | s1
```

雙累積器 + `65535` 常數 + `s2 << 16 | s1` = **Fletcher-32**（確認）。

用 Python 驗算：

```python
def fletcher32(data):
    s1, s2 = 0, 0
    for b in data:
        s1 = (s1 + b) % 65535
        s2 = (s2 + s1) % 65535
    return (s2 << 16) | s1

data = b"reverse" + bytes([0xDE,0xAD,0xBE,0xEF])
print(hex(fletcher32(data)))  # → 0x1fd90634  ✓ 和 target 輸出一致
```

### Step 5：完整格式規格（逆向結論）

```
RVRS 格式（Little-Endian 全文）

 Offset  Size  型別      欄位         備注
 0x00    4     char[4]  magic        "RVRS"（0x52565253）
 0x04    2     uint16   flags        功能旗標
 0x06    2     uint16   num_entries  entry 數量
 0x08    4     uint32   data_size    所有 value 的總 bytes 數

 Header 後接 num_entries 個 entry：
 [0]     1     uint8    type         0x01=字串, 0x02=binary hex
 [1]     2     uint16   key          entry 的 key
 [3]     2     uint16   value_len    value 的 bytes 數
 [5]     N     uint8[]  value        N = value_len

 最後（所有 entries 後）：
 [-4]    4     uint32   checksum     Fletcher-32 of concatenated values
```

</details>

<details>
<summary>展開：參考 parser 實作（驗證用）</summary>

```c
/* parser.c — 根據逆向結果實作的 parser */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

static uint32_t fletcher32(const uint8_t *data, size_t len) {
    uint32_t s1 = 0, s2 = 0;
    for (size_t i = 0; i < len; i++) {
        s1 = (s1 + data[i]) % 65535;
        s2 = (s2 + s1) % 65535;
    }
    return (s2 << 16) | s1;
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <file.rvrs>\n", argv[0]);
        return 1;
    }

    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }

    /* header */
    char magic[4];
    fread(magic, 4, 1, f);
    if (memcmp(magic, "RVRS", 4) != 0) {
        fprintf(stderr, "invalid magic\n");
        fclose(f); return 1;
    }

    uint16_t flags, num_entries;
    uint32_t data_size;
    fread(&flags,       2, 1, f);
    fread(&num_entries, 2, 1, f);
    fread(&data_size,   4, 1, f);
    printf("flags=0x%04x entries=%u data_size=%u\n",
           flags, num_entries, data_size);

    /* entries */
    uint8_t *all_data = malloc(data_size + 1);
    size_t   data_pos = 0;

    for (int i = 0; i < num_entries; i++) {
        uint8_t  type;
        uint16_t key, vlen;
        fread(&type, 1, 1, f);
        fread(&key,  2, 1, f);
        fread(&vlen, 2, 1, f);
        uint8_t *val = malloc(vlen + 1);
        fread(val, vlen, 1, f);

        printf("  type=0x%02x key=%u value_len=%u value=", type, key, vlen);
        if (type == 0x01) {
            for (int j = 0; j < vlen && val[j]; j++) putchar(val[j]);
        } else if (type == 0x02) {
            for (int j = 0; j < vlen; j++) printf("%02x", val[j]);
        } else {
            printf("(unknown type)");
        }
        printf("\n");

        if (data_pos + vlen <= data_size) {
            memcpy(all_data + data_pos, val, vlen);
            data_pos += vlen;
        }
        free(val);
    }

    /* trailer */
    uint32_t ck_file;
    fread(&ck_file, 4, 1, f);
    uint32_t ck_calc = fletcher32(all_data, data_pos);
    printf("checksum: file=0x%08x calc=0x%08x %s\n",
           ck_file, ck_calc, ck_file == ck_calc ? "OK" : "MISMATCH");
    free(all_data);
    fclose(f);
    return 0;
}
```

```bash
$ gcc -o parser parser.c
$ ./parser sample.rvrs
flags=0x0001 entries=2 data_size=11
  type=0x01 key=100 value_len=7 value=reverse
  type=0x02 key=200 value_len=4 value=deadbeef
checksum: file=0x1fd90634 calc=0x1fd90634 OK
```

和 `target` 輸出**完全一致**——逆向正確。

</details>

## 驗證方式

```bash
# 1. 基本對拍
$ ./parser sample.rvrs > my_output.txt
$ ./target  > ref_output.txt  # target 產生 sample.rvrs 同時印輸出
$ diff my_output.txt ref_output.txt  # 無差異 = 通過

# 2. 自製新測試檔（進階驗證）
# 寫一個 generator，產生 sample2.rvrs
# 同時跑 target 和 parser，輸出應一致

# 3. 錯誤情境
$ echo "XXXX" > bad.rvrs
$ ./parser bad.rvrs
# 應印 "invalid magic" 並以非零 exit 退出
```

## 延伸挑戰

1. **寫一個 generator**：根據逆向出的格式規格，寫一個能產生合法 `.rvrs` 檔的 generator，讓 `target` 能正確解析。
2. **逆向 `-O2` 版本**：用 `gcc -O2 -o target_O2 target.c; strip target_O2` 產生優化版 binary，同樣任務但更難——迴圈可能被 unroll、inline 消失。
3. **加入錯誤注入**：修改 `sample.rvrs` 的 checksum，確認你的 parser 能偵測 `MISMATCH`；再修改 data_size，確認 parser 不會越界讀。
4. **Fuzzing**：用 AFL++ 對你的 parser 做 fuzzing（接 `advanced_fuzzing` 課），看看格式邊界條件是否安全（`vlen=0`、`num_entries=65535`、`data_size` 遠大於實際資料）。

## 自我檢核

做完後你應該能回答：

- [ ] 我能解釋「追 fread 的 size 序列」如何讓我還原出 header 的欄位大小
- [ ] 我知道 Fletcher-32 和簡單 sum checksum 的 asm 辨識差異（雙累積器 vs 單累積器）
- [ ] 我能從 format string（`%04x`/`%02x`/`%08x`）推斷欄位的 bit width
- [ ] 我的 parser 通過了基本對拍和錯誤情境測試
- [ ] 我理解為什麼 `-O0` binary 的 fread 呼叫序列比 `-O2` 更容易逆向（register vs stack variable 差異）

→ [Ch 20 逆 C++ binary：vtable / RTTI / name mangling](./20-reversing-cpp-binaries.md)
