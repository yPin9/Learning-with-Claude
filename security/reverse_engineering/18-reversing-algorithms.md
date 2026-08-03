# Ch 18 — 逆一個演算法：認出 crypto / hash / 壓縮指紋

> **目標**：從 binary 認出已知演算法——不靠符號名，靠常數指紋和迴圈形狀。讀完你能辨識 AES、SHA-256、MD5、CRC、RC4、zlib 的指紋，並還原一個自訂 XOR/CRC/hash 的邏輯。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb。

## 為什麼需要這個？

逆向真實 binary 最常碰到的問題之一：「這一大塊在幹什麼？」它沒有函式名、沒有註解，但它有迴圈、有 XOR、有特殊常數。

會辨識演算法指紋的人，看到 `0x67452301` 這個 magic 常數，就知道「MD5 init vector」，把幾千行 asm 折疊成一句「這是 MD5 實作」。不會辨識的人，要逐指令還原——花十倍時間，還可能搞錯。

這個技能在三個情境都用得上：惡意程式分析（加密/C2 通訊協定）、CVE 重現（漏洞在哪個 crypto 實作）、CTF RE 題（裂解自訂 XOR/CRC 加密）。本章系統化建立你的演算法指紋字典。

## 先建立直覺：演算法有三種指紋

```
演算法指紋的三個層次

  層 1 — 常數指紋       0x67452301 → MD5 init vector，沒有其他東西用這個
  層 2 — 迴圈形狀       block cipher 的「外迴圈 rounds、內迴圈 steps」
  層 3 — 操作組合       XOR + rotate + add → ARX 系列（ChaCha20、Salsa20）

逆向時從最強的指紋開始——常數最強（一找就中），迴圈形狀次之，操作組合最弱（容易誤判）。
```

工具路徑：靜態時先用 `strings` / `grep` 掃常數，再看 `objdump` 的迴圈結構。動態時在重要函式下斷點，看輸入輸出——如果輸出 16 bytes 而輸入是任意長度，很可能是某種 hash 或 MAC。

## 常數指紋字典

### MD5

MD5 在 `md5_init` 裡有四個 32-bit init vector，是全球最容易辨認的常數：

```c
a = 0x67452301
b = 0xefcdab89
c = 0x98badcfe
d = 0x10325476
```

objdump 中會看到四個 `mov $0x67452301,...` 在同一個函式（通常在 init，或第一個壓縮塊前）。另外 MD5 每輪用到 64 個 32-bit 推導常數（T 表），它們由 `abs(sin(i))*2^32` 算出，例如第一個是 `0xd76aa478`。有時 T 表會靜態存在 `.rodata`，直接掃就找到。

### SHA-256

SHA-256 有兩組常數：

**init vector（256 bits = 8 個 32-bit words）**

```
0x6a09e667  0xbb67ae85  0x3c6ef372  0xa54ff53a
0x510e527f  0x9b05688c  0x1f83d9ab  0x5be0cd19
```

**輪常數（K 陣列，64 個 32-bit，源自前 64 個質數的立方根小數部分）**

```
K[0] = 0x428a2f98
K[1] = 0x71374491
K[2] = 0xb5c0fbcf
...
K[63] = 0xc67178f2
```

逆向時最快的方法：`objdump -d bin | grep '428a2f98\|71374491'`——如果在同一個函式裡出現兩個以上，就是 SHA-256 的 round constant 陣列。SHA-1 和 SHA-512 也有類似手法，但 SHA-512 用 64-bit words。

### AES

AES 最明顯的指紋是 **S-box** 和 **T-table**（加速實作）：

- S-box：256 bytes 的靜態陣列，前四個 bytes 是 `0x63, 0x7c, 0x77, 0x7b`（SubBytes 的第一列）。
- T-table 實作（分解成四個 T0-T3 陣列，每個 256×4 bytes = 1KB）：T0 的前幾個值是 `0xc66363a5, 0xf87c7c84, 0xee777799, ...`

掃 binary 的策略：

```bash
# 找 AES S-box 的前幾個常數
objdump -d target | grep '0x637c\|7c77'

# 或掃 .rodata 裡的 1024-byte 塊（T-table 是 4x256 uint32）
strings -t x target | grep -i 'aes\|rij' 2>/dev/null  # 若沒 strip
readelf -x .rodata target | grep 'c66363a5'
```

### RC4

RC4 的指紋不是常數，是 **KSA（Key Scheduling Algorithm）迴圈的形狀**：

1. 初始化 256 bytes 的 S 陣列（`for(i=0;i<256;i++) S[i]=i`）——產生一個從 0 到 255 的陣列。
2. KSA 主迴圈：在一個 256 次迭代的迴圈裡用 key bytes 打亂 S 陣列（兩個索引 i, j，做 swap）。
3. PRGA 階段：再一個迴圈，每次輸出一個 keystream byte，XOR 明文。

逆向辨識 RC4 的關鍵：看到一個 **256 次**的初始化迴圈 + 一個**雙索引 swap 迴圈**，幾乎可以確認是 RC4 或其變體。

```
RC4 KSA 形狀（objdump 視角）

  ; 初始化 S[0..255] = 0..255
  xor %eax,%eax
.Linit:
  mov %al, (%rsi,%rax,1)   ; S[i] = i
  inc %al
  jne .Linit              ; 256 次（al 繞一圈回 0）

  ; KSA
  xor %ecx,%ecx           ; i = 0
  xor %edx,%edx           ; j = 0
.LKSA:
  movzbl (%rsi,%rcx,1),%eax    ; S[i]
  add %al, %dl
  add (%key,%rcx,...), %dl     ; j = (j + S[i] + key[i%len]) % 256
  movzbl (%rsi,%rdx,1),%r8d    ; S[j]
  mov %r8b, (%rsi,%rcx,1)      ; S[i] = S[j]
  mov %al, (%rsi,%rdx,1)       ; S[j] = S[i]
  inc %cl
  cmp $0x100,%ecx
  jl .LKSA
```

## CRC 的常數指紋：多項式

CRC-32（最常見，用在 zlib、PNG）的**多項式常數是 `0xEDB88320`**（反位元序）。CRC-16/CCITT 的常數是 `0x1021`（或反位序 `0x8408`）。

逆向中有兩種 CRC 實作：

1. **Table-driven**：`.rodata` 裡有一個 256×4 bytes（CRC-32）或 256×2 bytes（CRC-16）的靜態陣列，值都從多項式推導出來。
2. **Bitwise loop**：每次處理一個 bit，用 XOR + conditional shift，見到 `0xEDB88320` 或 `0x1021` 就確認了。

## 真跑：ground-truth 逆向一個 XOR + CRC + hash

下面寫一個包含三種演算法的小程式，編成 `-O0` 讓迴圈形狀清晰可辨，然後逆出每個函式的目的：

```c
/* /tmp/re_part3/algo.c — 出題 source（先蓋著，逆完再看） */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

void xor_cipher(uint8_t *data, size_t len, uint8_t key) {
    for (size_t i = 0; i < len; i++)
        data[i] ^= key;
}

uint16_t crc16(const uint8_t *data, size_t len) {
    uint16_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            if (crc & 0x8000)
                crc = (crc << 1) ^ 0x1021;
            else
                crc <<= 1;
        }
    }
    return crc;
}

uint32_t djb2(const char *str) {
    uint32_t hash = 5381;
    int c;
    while ((c = (unsigned char)*str++))
        hash = ((hash << 5) + hash) + c;  /* hash * 33 + c */
    return hash;
}
```

```bash
$ gcc -O0 -o /tmp/re_part3/algo_O0 /tmp/re_part3/algo.c
$ /tmp/re_part3/algo_O0
XOR encrypted: 0a 27 2e 2e 2d 6e 62 15 2d 30 2e 26 63
XOR decrypted: Hello, World!
CRC16: 0x4fd6
djb2(test): 0x7c9e6865
```

現在看逆向視角——以下是真實 `objdump -d` 輸出（`-O0`）：

### xor_cipher：識別 XOR 迴圈

```asm
00000000000011a9 <xor_cipher>:
    11a9:  endbr64
    11ad:  push   %rbp
    11ae:  mov    %rsp,%rbp
    11b1:  mov    %rdi,-0x18(%rbp)    ; data 指標
    11b5:  mov    %rsi,-0x20(%rbp)    ; len
    11b9:  mov    %edx,%eax
    11bb:  mov    %al,-0x24(%rbp)     ; key（uint8_t，用 al）
    11be:  movq   $0x0,-0x8(%rbp)     ; i = 0
    11c6:  jmp    11eb <xor_cipher+0x42>   ; → 迴圈條件
    11c8:  mov    -0x18(%rbp),%rdx    ; ┐ data[i] ^= key
    11cc:  mov    -0x8(%rbp),%rax     ; │
    11d0:  add    %rdx,%rax           ; │ ptr = data + i
    11d3:  movzbl (%rax),%eax         ; │ load data[i]
    11d6:  mov    -0x18(%rbp),%rcx    ; │
    11da:  mov    -0x8(%rbp),%rdx     ; │
    11de:  add    %rcx,%rdx           ; │ ptr = data + i
    11e1:  xor    -0x24(%rbp),%al     ; │ al ^= key  ← 核心操作
    11e4:  mov    %al,(%rdx)          ; ┘ store
    11e6:  addq   $0x1,-0x8(%rbp)    ; i++
    11eb:  mov    -0x8(%rbp),%rax
    11ef:  cmp    -0x20(%rbp),%rax    ; i < len
    11f3:  jb     11c8               ; 繼續迴圈
    11f7:  ret
```

辨識要點：
- 單一迴圈、每次迭代做一次 `xor` 操作
- 參數三個（指標+長度+byte key），輸出就地修改輸入——典型 symmetric 串流密碼形狀
- 沒有 look-up table、沒有多輪——這是最簡單的形式

### crc16：辨識雙重迴圈 + 多項式

```asm
00000000000011f9 <crc16>:
    1209:  movw   $0x0,-0xe(%rbp)     ; crc = 0（16-bit）
    ...
    ; 外迴圈 — 逐 byte
    1219:  mov    -0x18(%rbp),%rdx    ; data
    122a:  shl    $0x8,%eax           ; data[i] << 8
    122f:  movzwl -0xe(%rbp),%eax
    1233:  xor    %edx,%eax           ; crc ^= data[i] << 8
    ; 內迴圈 — 逐 bit（j = 0..7）
    1246:  test   %ax,%ax
    1249:  jns    125b                ; if (!(crc & 0x8000)) → skip xor
    124b:  add    %eax,%eax           ; crc <<= 1
    1251:  xor    $0x1021,%ax         ; crc ^= 0x1021  ← 多項式！
    1255:  mov    %ax,-0xe(%rbp)
    125b:  shlw   -0xe(%rbp)         ; else: crc <<= 1
    1263:  cmpl   $0x7,-0xc(%rbp)    ; j < 8
```

辨識要點：
- **雙重迴圈**（外 len 次、內 8 次 = 處理 8 bits）
- **`xor $0x1021,%ax`** — CRC-16/CCITT 的多項式 `0x1021`，看到這個直接確認
- 結果 16-bit（`movzwl`，zero-extend to 32-bit）

### djb2：辨識「hash * 33 + c」模式

```asm
000000000000127e <djb2>:
    128a:  movl   $0x1505,-0x8(%rbp)  ; hash = 0x1505 = 5381 ← djb2 magic
    1296:  shl    $0x5,%eax           ; hash << 5
    129b:  mov    -0x8(%rbp),%eax
    129e:  add    %eax,%edx           ; (hash << 5) + hash = hash * 33
    12a0:  mov    -0x4(%rbp),%eax     ; c
    12a3:  add    %edx,%eax           ; + c
    12a5:  mov    %eax,-0x8(%rbp)
    12b4:  movzbl (%rax),%eax         ; *str++
    12c1:  jne    1293               ; while (c != 0)
```

辨識要點：
- **初始值 `0x1505`（即 5381）** — djb2 的 magic seed，這是最強指紋
- **`shl $0x5; add`** — 這是 `hash * 33`（strength reduction：`x*33 = (x<<5)+x`）
- 字串逐字元迴圈，終止條件是 `\0`

## 壓縮演算法的指紋

### zlib / DEFLATE

zlib 的指紋有兩層：

1. **CRC-32 table**（`.rodata` 裡 256×4 bytes，第一個 word 是 `0x00000000`，第二個是 `0x77073096`）
2. **Huffman 編碼/解碼結構**：大量的位移、mask、look-up table 組合；`deflate.c` 的結構在 binary 裡有很典型的「滑動視窗 + 距離/長度對」形狀。

實作辨識：找 `0x77073096`（CRC-32 table 第二個 entry），或找 `zlib_version` 字串（非 strip binary）。

### LZ 系列（LZ4 / LZMA）

- **LZ4**：輸出格式有 magic `0x184D2204`，壓縮迴圈以複製 literal + match 為主，結構相對平坦（無多輪）。
- **LZMA / 7z**：range coder + probability model，比 zlib 複雜；有一個 11-bit probability table（`0x800` 個 entry）。

## 工具輔助：FindCrypt 的概念

FindCrypt 是 IDA/Ghidra 的外掛，它把已知演算法的常數陣列雜湊起來，掃描整個 binary 的 `.rodata` / `.data`，一次找出所有命中的演算法。概念如下：

```
FindCrypt 的工作原理（你可以手動模擬）

  預先建一個「常數指紋 → 演算法名稱」的字典，例如：
    0x67452301 → MD5 init
    0x428a2f98 → SHA-256 K[0]
    0x63 0x7c 0x77 0x7b（連續 4 bytes）→ AES S-box

  掃描目標 binary 的所有段，對每個地址嘗試比對字典。
  命中 → 在該位址貼上演算法名稱。
```

手動版：

```bash
# 找 SHA-256 的第一個 K 常數
objdump -d target | grep '428a2f98'

# 找 MD5 init vector
objdump -d target | grep '67452301\|efcdab89'

# 找 AES S-box（第一個 byte 是 0x63）
readelf -x .rodata target | grep '637c777b'
```

## 對比與取捨

| 指紋類型 | 強度 | 適用情境 | 可靠性 |
|---|---|---|---|
| 常數（init vector / K 陣列）| 最強 | SHA/MD5/AES 快速辨識 | 極高（碰撞率幾乎為零） |
| look-up table（S-box/CRC table）| 強 | AES/CRC/RC4 的 table 實作 | 高（要確認 table 內容） |
| 迴圈形狀（輪數/雙層迴圈）| 中 | block cipher / hash 壓縮函式 | 中（需結合其他線索） |
| 操作組合（XOR+rotate+add）| 弱 | ARX 系列（ChaCha20/Salsa20）| 低（多種演算法共用） |

## 踩雷集錦

1. **魔數被改過（自訂變體）**：看到迴圈形狀很像 MD5，但 init vector 不是 `0x67452301`——這是故意改過 magic 的「自訂 MD5」。別假設沒改，動態跑完看輸出長度（128 bits = 16 bytes → MD5 家族）。

2. **-O2 的 loop unroll 讓迴圈消失**：CRC 的 8 次內迴圈在 `-O2` 下可能被展開成 8 條直線指令，雙重迴圈結構就不見了。逆向時先搜多項式常數，不要只看迴圈次數。

3. **table-driven 和 bitwise 形狀完全不同**：table-driven CRC 只需一個外迴圈 + 一次查表，幾乎認不出「8 次內迴圈」——但 `0xEDB88320` 這個 table 的內容會在 `.rodata` 裡，用 `readelf -x .rodata` 掃就找到。

4. **壓縮輸出的 entropy 高**：看到一個函式輸出 entropy 很高的資料，不一定是加密——可能是壓縮（zlib/LZ4）。區分方式：加密的輸出幾乎所有 byte 都等概率，壓縮的輸出有一定的 Huffman 偏斜；或者看有沒有 CRC-32 table（zlib 必有）。

5. **djb2 的 `shl $5; add` 和 MD5 的某個 step 長得很像**：都是「位移 + 加法」——不要只看一條指令，要看整個函式的常數 seed 和結果大小（djb2 輸出 32-bit、迭代直到 `\0`）。

## 進階：再往深一層

- **angr 找演算法（接 `symex_taint` 課）**：對 unknown 函式，跑幾組已知的 MD5/SHA-256 測試向量當輸入，觀察輸出是否匹配——不用完整逆向就能確認算法。
- **自訂演算法的 differential cryptanalysis**：如果確認是「魔改 MD5」（改了 constant 但保留結構），可以用差分分析找出 MD5→自訂版的差異——接 `cryptography` 課。
- **YARA 規則掃描**：把演算法常數指紋寫進 YARA 規則，批次掃描大量 binary，在惡意程式分析中常用（接 `malware_analysis` 課）。

## 本章重點整理

- **演算法指紋有三層**：常數（最強）→ look-up table → 迴圈形狀 → 操作組合（最弱）。
- 找常數先用 `objdump -d | grep` 或 `readelf -x .rodata`，找 MD5 初始向量、SHA-256 K 陣列、AES S-box 前幾 bytes、CRC 多項式。
- RC4 的指紋是 **256-byte 初始化 + 雙索引 swap 迴圈**，不靠常數。
- djb2 hash 的 magic seed 是 `5381`（`0x1505`），`shl $5; add` = hash×33。
- `-O2` 會 unroll 迴圈、打亂形狀——優先查常數，形狀辨識當輔助。

## 自我檢核

- [ ] 我能從 objdump 輸出認出 `crc ^= 0x1021` 是 CRC-16 的多項式
- [ ] 我知道 SHA-256 的 K[0] 是 `0x428a2f98`，能用 grep 在 binary 裡定位
- [ ] 我能解釋 RC4 的「256-byte 初始化 + KSA 雙索引 swap」形狀
- [ ] 我理解為什麼 `-O2` 的 loop unroll 會讓雙層迴圈消失，以及如何應對
- [ ] 我能用 `readelf -x .rodata | grep` 找 AES S-box 或 CRC table

## 延伸閱讀

1. **《Reverse Engineering for Beginners》Ch 94-98（加密章節）** — Dennis Yurichev（[免費](https://beginners.re/)）
   - 學什麼：大量 XOR cipher / CRC / 自訂 hash 的逆向案例，每個都有 source↔asm 對照
   - 前提：本課 Part 1（基本 asm 辨識）

2. **OpenSSL 源碼的 SHA / AES 實作**（`openssl/crypto/sha/sha256.c`，`openssl/crypto/aes/`）
   - 學什麼：真實生產 code 的優化實作長什麼樣（T-table、SSE intrinsic），對照 `-O2` binary 看哪些常數還在
   - 前提：能讀 C

3. **Compiler Explorer 反查演算法**（[godbolt.org](https://godbolt.org/)）
   - 學什麼：把你認為是 CRC/djb2 的 C code 貼進去，選 gcc -O0/-O2，對照你逆向的 asm 是否匹配——最快的 ground-truth 方式
   - 前提：猜出演算法後想驗證

逆出演算法是「認出零件」，下一步是「認出格式」——一個 parser 按什麼規則把 bytes 切成有意義的結構。

→ [Ch 19 逆一個檔案格式 / 協定](./19-reversing-file-formats-protocols.md)
