# Ch 19 — 逆一個檔案格式 / 協定

> **目標**：從 parser code 還原未知的二進位格式規格，並寫出一個能產生合法檔案的 generator。讀完你能系統化追蹤 `fread`/`recv` 如何切解 bytes，還原 header 欄位、length-prefixed record、大小端、checksum 的結構。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb + readelf + strings。

## 為什麼需要這個？

協定逆向（protocol reverse engineering）是滲透測試、漏洞研究和惡意程式分析的高頻任務：

- 閉源 IoT 設備用私有二進位協定通訊——逆向才能理解通訊內容，替換設備或偽造封包。
- 惡意程式的 C2 協定用自訂格式加密——逆出格式才能模擬合法封包、解密資料、寫偵測規則。
- 舊版 firmware 不公開格式規格——逆向才能打包、修改、重新封裝做客製化。
- 漏洞研究的「格式解析器」是最常見的攻擊面——`fread` 的 size 沒有邊界檢查就是堆積/棧溢位的種子。

本章的核心技術：**追蹤 parser 的資料流**。Parser 怎麼把 bytes 切成欄位，就是格式的定義。這和 `reading_code` 課（Ch 8，追蹤資料流）的技術完全對稱——只是這裡你追的不是 source 裡的變數賦值，而是 asm 裡的 `rdi`/`rsi` register 和 stack offset。

## 先建立直覺：格式逆向的三步模型

```
格式逆向的思路框架

  1. 找入口（parser 函式在哪）
     ─────────────────────────
     字串偵察：找 "invalid magic"、"bad header"、"version mismatch"
     → 這些錯誤字串的 xref = parser 函式

  2. 追資料切法（格式的定義）
     ─────────────────────────
     fread(ptr, size, nmemb, f) 的 size = 欄位寬度
     buf 的 stack offset = 欄位位置
     memcmp / 比較 = 欄位驗證（magic 比對、版本範圍）
     malloc(len) → fread(buf, len, 1, f) = 動態長度欄位

  3. 還原結構（輸出格式規格）
     ─────────────────────────
     每個 fread = 一個 struct field
     迴圈裡的 fread = record 格式（重複 N 次的 block）
     最後的 checksum 計算 = trailer（驗證欄位）
     → 寫出 struct 定義 + generator 驗證
```

## 核心技術：從 fread 序列還原 struct

`fread(ptr, size, nmemb, stream)` 的四個參數在 x86-64 SysV ABI 對應 `rdi, rsi, rdx, rcx`：

```
fread 呼叫的 asm 模式（-O0）

  mov %rax,%rcx        ; rcx = stream（FILE*）
  mov $0x1,%edx        ; rdx = nmemb = 1
  mov $0x4,%esi        ; esi = size = 4  ← 欄位寬度！
  lea -0xc(%rbp),%rdi  ; rdi = ptr（stack 上的欄位 buffer）
  call fread@plt
```

**關鍵**：真正讀的位元組數 = `rsi`（size）× `rdx`（nmemb）。大多數情況 nmemb=1，所以 `rsi` 直接就是欄位寬度。

逐個 fread 呼叫記下 size，就還原了格式的欄位寬度序列：

```
fread 序列記錄表（範例）

  call#  size×nmemb  含義（靜態分析後填）
  1      4×1         magic（接 memcmp）
  2      2×1         version
  3      2×1         num_records
  4      4×1         reserved/checksum 種子
  (loop)
    5    1×1         record type
    6    2×1         record length
    7    len×1       record data（動態長度）
  /loop
  8      4×1         checksum
```

## memcmp 的 magic 識別模式

Parser 通常一開始就驗 magic——在 fread 後立刻有一個 `memcmp`：

```asm
; 真實 objdump 輸出（fmtparser.c 的 read_file）
    148d:  mov    $0x4,%esi            ; size = 4
    1490:  call   1100 <fread@plt>     ; 讀 4 bytes 到 -0xc(%rbp)

    149e:  lea    0xb40(%rip),%rcx     ; → .rodata 的常數（magic）
    14ab:  call   1140 <memcmp@plt>    ; 比對
    14b2:  je     14ed                 ; 相等 → 繼續解析

    ; magic 不符 → 錯誤訊息 + 回傳 -1
    14c8:  lea    0xb4a(%rip),%rax     # 2019 → "Bad magic\n"（.rodata）
    14d2:  call   1170 <fwrite@plt>
    14de:  call   1110 <fclose@plt>
    14e3:  mov    $0xffffffff,%eax     ; return -1
    14e8:  jmp    16dd
```

`0xb40(%rip)` 的 RIP-relative 地址指向 `.rodata` 的字串——用 `readelf -x .rodata target` 在那個 offset 看到的 4 bytes 就是 magic。在這個範例裡指向 `"MYFT"`（`0x4D594654`）。

## 真跑：還原自訂二進位格式

準備出題 source（邏輯同 Ch 19 說明的格式）：

```c
/* 格式：header(magic[4] + version[2] + nrec[2])
 *        records(type[1] + len[2] + data[len]) × nrec
 *        trailer(checksum[2]) = sum(all data bytes) % 65536 */
```

```bash
$ gcc -O0 -o /tmp/re_part3/fmtparser_O0 /tmp/re_part3/fmtparser.c
$ /tmp/re_part3/fmtparser_O0
version=1 records=2
rec[0]: type=0x01 len=5 data=68 65 6c 6c 6f
rec[1]: type=0x02 len=2 data=de ad
checksum: file=0x039f calc=0x039f OK
```

strip 並偵察：

```bash
$ cp /tmp/re_part3/fmtparser_O0 /tmp/re_part3/fmtparser_stripped
$ strip /tmp/re_part3/fmtparser_stripped
$ strings /tmp/re_part3/fmtparser_stripped | head -30
/lib64/ld-linux-x86-64.so.2
fopen fclose free memcmp stderr fread putchar fwrite printf...
Bad magic
version=%u records=%u
rec[%d]: type=0x%02x len=%u data=
checksum: file=0x%04x calc=0x%04x %s
OK
MISMATCH
```

線索：`Bad magic`（有 magic 驗證）、format string 告訴你欄位名和格式（`version`、`records`、`type`、`len`）、`checksum 16-bit`（`%04x` = 4 hex digits）。

### 追蹤 read_file 的 fread 序列（真實 objdump 輸出）

```bash
$ objdump -d /tmp/re_part3/fmtparser_O0 | grep -A 60 '<read_file>:' | head -80
```

以下是從真實輸出摘出的 fread 序列：

```asm
; read_file 函式的 fread 序列

; 第 1 個 fread：magic（size=4，count=1）
    148d:  mov    $0x4,%esi            ; size = 4 bytes
    1490:  call   fread@plt
    14ab:  call   memcmp@plt           ; 立刻跟著 memcmp → 這是 magic 驗證

; 第 2 個 fread：version（size=2，count=1）
    1560:  mov    $0x2,%esi            ; size = 2 bytes → uint16
    1568:  call   fread@plt

; 第 3 個 fread：nrec（size=2，count=1）
    157d:  mov    $0x2,%esi            ; size = 2 bytes → uint16
    1585:  call   fread@plt

; （進入 for 迴圈，i < nrec）

; 第 4 個 fread：type（size=1，count=1）
    ; mov $0x1,%esi
    ; call fread@plt

; 第 5 個 fread：record len（size=2，count=1）
    ; mov $0x2,%esi
    ; call fread@plt

; malloc(len) 配置動態 buffer

; 第 6 個 fread：data（size=len，count=1）
    ; len 在 register，不是常數 → 動態長度
    ; call fread@plt

; （迴圈結束後）

; 第 7 個 fread：checksum（size=2，count=1）
    ; mov $0x2,%esi
    ; call fread@plt
```

還原格式規格：

```
MYFT 格式（Little-Endian）

 偏移  大小  型別     欄位
 0x00  4    char[4]  magic = "MYFT"
 0x04  2    uint16   version
 0x06  2    uint16   num_records
 0x08  (重複 num_records 次)：
        1   uint8    type
        2   uint16   data_len
        N   uint8[]  data（N = data_len）
 最後  2    uint16   checksum = sum(all data bytes) % 65536
```

### 辨識 checksum 類型

checksum 計算的 asm 模式（在 fread 讀完 data 後的迴圈裡）：

```asm
; 累積 checksum（迴圈裡累加每個 data byte）
    ; 單一累積器 ck_calc
    movzbl (%rax),%edx    ; 讀一個 data byte
    add    %edx,%ecx      ; ck_calc += byte
    ; 沒有乘法、沒有位移 → 不是 CRC
    ; 只有加法 → 是簡單 sum（不是 Fletcher，Fletcher 有兩個累積器）
```

對比：
- 簡單 sum：單累積器 + 加法
- Fletcher-32：**雙累積器**（s1、s2 各一個 register）+ `% 65535` 操作
- CRC-16：雙重迴圈 + `0x1021` 多項式

## 協定逆向的進階模式

### Length-prefixed vs Delimiter-based vs Fixed-field

| 模式 | 識別（asm）| 典型協定 |
|---|---|---|
| Length-prefixed | `fread(len_buf, 2/4, ...)` 讀長度；`fread(data_buf, len, ...)` 讀 body | TLV、Protobuf、本章範例 |
| Delimiter-based | `recv` 一字節迴圈 + `cmp $0x0a,%al; je done`（等換行）| HTTP headers、FTP、SMTP |
| Fixed-field | 全程固定 size 的 `fread`，沒有動態長度 | ARP、IP header、DNS header |

### 大小端辨識

看資料如何被組合成多位元組整數：

```asm
; 小端（little-endian）= x86 native，直接讀
movzwl -0x2e(%rbp),%eax   ; fread 進來後直接用 movzwl 讀 → 小端

; 大端（big-endian）= 需要手動拼湊（或呼叫 ntohs/ntohl）
movzbl (%rbp),%eax
shl    $0x8,%eax
movzbl 0x1(%rbp),%edx
or     %edx,%eax          ; (buf[0]<<8) | buf[1] = big-endian uint16
```

或者呼叫 `ntohs`/`ntohl`——看到這兩個函式直接確認是大端網路格式。

### 狀態機式協定（TCP 長連線）

TCP 上的長連線協定通常是狀態機：解析一個 type，根據 type 決定下一步。逆向關鍵：找 **switch / jump table**（Ch 5 已學），jump table 的每個 case 對應一個 message type handler，從 handler 裡的 recv/fread 序列還原各 type 的格式。

## 寫 Generator 驗證逆向結果

逆向結論最直接的驗證方法：根據還原的格式規格，寫一個 generator 產生合法檔案，再拿原始 binary 解析它——能 parse 且 checksum OK = 格式逆向正確。

```c
/* generator.c：根據逆向出的 MYFT 格式規格寫 */
#include <stdio.h>
#include <stdint.h>
int main(void) {
    FILE *f = fopen("gen.myft", "wb");
    fwrite("MYFT", 4, 1, f);            /* magic */
    uint16_t ver = 1, nrec = 1;
    fwrite(&ver,  2, 1, f);             /* version */
    fwrite(&nrec, 2, 1, f);             /* num_records */
    /* record: type=0x01 len=3 data=41 42 43 */
    uint8_t t=0x01; uint16_t l=3;
    fwrite(&t, 1, 1, f); fwrite(&l, 2, 1, f);
    fwrite("ABC", 3, 1, f);
    /* checksum: 0x41+0x42+0x43 = 0xC6 */
    uint16_t ck = 0x41+0x42+0x43;
    fwrite(&ck, 2, 1, f);
    fclose(f);
    return 0;
}
```

```bash
$ gcc -o generator generator.c && ./generator
$ ./fmtparser_stripped gen.myft       # 用原始 binary 解析
version=1 records=1
rec[0]: type=0x01 len=3 data=41 42 43
checksum: file=0x00c6 calc=0x00c6 OK   ← 格式正確！
```

## 踩雷集錦

1. **fread 的 size 和 nmemb 搞反了**：`fread(buf, 2, 3, f)` 讀 6 bytes（3 個 uint16），不是 2 bytes。逆向時把 `rsi`（size per element）× `rdx`（count）才是真正的位元組數。`nmemb=1` 是最常見的情況（每次讀一個欄位），但 `fread(buf, 1, len, f)`（一次讀 len 個 byte）也很常見——size=1、nmemb=len。

2. **struct alignment padding 讓 offset 跳空**：在 source 裡 `struct { uint8_t a; uint32_t b; }` 在記憶體不是 5 bytes——編譯器在 `a` 後填 3 bytes padding。逆向時如果兩個 fread 之間有明顯的 stack offset gap，可能是 padding——用 gdb 在 fread 後 `x/Nb %rbp-N` 看記憶體佈局確認。

3. **格式沒有 magic 時更難定位 parser**：沒有 magic 的格式，parser 入口不能靠 `memcmp` 找——要靠格式本身的結構常數（比如固定的 version 欄位值），或靠 `fread` + 後續分支的「異常處理字串」（`"invalid version"`）。

4. **網路協定的大端讓你以為 value 不對**：逆向出一個 uint16 值 `0x0100`，但程式把它當 256 而你以為是 `0x100=256`（小端）——其實是大端的 1。先確認大小端，再解讀數字。

5. **動態長度欄位讓靜態 fread 序列看起來「沒有」**：`fread(val, vlen, 1, f)` 的 size 不是常數（在 register 裡），所以 objdump 看到 `mov %eax,%esi; call fread@plt`——size 是在執行期由前一個 fread 讀進來的 `vlen`。這種情況：往前找一個讀 uint16 的 fread，那個值就是動態長度。

6. **checksum 的覆蓋範圍有時不包括 header**：有些格式的 checksum 只計算 data（records 部分），不包含 header——這是常見設計（header 本身有 magic 做完整性保護）。看 checksum 計算的迴圈從哪裡開始，是從 header 後面的 file offset 還是從頭。

## 進階：再往深一層

- **動態追蹤 fread 資料流（接 Ch 16）**：用 gdb `watch` 或 Frida hook `fread`/`recv`，觀察每次讀了什麼 bytes——直接得到格式的動態側影，不需要讀 asm。特別適合加密協定的中間段（加密前的明文 hook）。
- **AFLNet：格式感知的協定 fuzzing**（接 `advanced_fuzzing` 課）：如果只是要找漏洞而不需要完整還原格式，AFLNet 可以從正常的網路互動 seed 出發，對協定做有狀態的 fuzzing——等於讓程式自己告訴你什麼格式合法。
- **Wireshark dissector 作為逆向起點**：目標協定若有現成 Wireshark plugin，先讀 dissector source 了解欄位名再對照 binary——節省大量時間，dissector 本身就是格式規格的可執行版本。

## 本章重點整理

- 格式逆向的核心：**追 `fread(ptr, size, nmemb, f)` 的 size 序列**——`rsi`×`rdx` = 欄位寬度。
- 從 **magic check**（memcmp 後接錯誤字串）確認 parser 入口，逐欄位還原 header。
- 從 **for 迴圈裡的 fread 序列** 確認 record 格式（重複 N 次的 block）。
- 大小端辨識：有 `ntohs`/`ntohl` = 大端；直接 `movzwl` 讀 = 小端。
- **寫 generator 對拍**是驗證逆向結果最直接的方式——能 parse 且 checksum OK = 逆向正確。
- Alignment padding 可能造成 struct offset 有「空隙」——gdb 觀察記憶體佈局確認。

## 自我檢核

- [ ] 我能從 objdump 的 fread 序列還原 struct 欄位的大小和順序
- [ ] 我能從 `memcmp` + `.rodata` offset 找出 magic bytes 的實際值
- [ ] 我能區分 length-prefixed 和 delimiter-based 兩種協定模式的 asm 特徵
- [ ] 我能從 asm 辨識大小端的處理方式（直接讀 vs `ntohs`/手動位移）
- [ ] 我知道 `fread(buf, size, nmemb, f)` 中總位元組數 = size × nmemb

## 延伸閱讀

1. **《Practical Malware Analysis》Ch 3（分析網路行為）** — Sikorski & Honig（No Starch, 2012）
   - 學什麼：閉源惡意程式協定逆向的完整案例，從 pcap 到 binary 雙向比對
   - 前提：本章基礎 + 能讀 x86 asm

2. **RFC 5246（TLS 1.2 record layer）+ openssl `ssl/record/ssl3_record.c`**
   - 學什麼：有完整規格的真實協定，對照 C parser 實作——練習「從 parser 反推規格」的技能
   - 前提：能讀 C，了解 TCP/IP（接 `networking` 課）

3. **Wireshark 開發者文件：Writing a Dissector**（[https://wiki.wireshark.org/Dissector](https://wiki.wireshark.org/Dissector)）
   - 學什麼：dissector 本身就是「格式規格的可執行版本」，讀 dissector = 讀格式文件
   - 前提：了解 Wireshark 基本操作

練習 C 把本章技術做成一個 ground-truth 逆向任務——給你一個 strip binary，任務是逆出完整格式規格並寫出 parser。

→ [練習 C：逆一個檔案格式並寫出 parser](./practice-c-reverse-a-format-write-parser.md)
