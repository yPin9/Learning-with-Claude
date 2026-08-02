# 練習 F：Hybrid/Directed Fuzzing 打穿多層 Magic Gate

> **目標**: 對一個有五道 magic gate 保護的 heap overflow，分別用 SymCC hybrid 和 AFLGo directed 兩軌突破，並與純 AFL 的失敗對照，理解為何要用 hybrid/directed。

---

## 背景：為什麼純 AFL 打不穿

`parse_packet` 有五道 gate，每道都是機率殺手：

- Gate 1：`magic == 0xCAFEBABE`，機率 1/2³²，純隨機翻出正確值期望需要 43 億次嘗試
- Gate 2：`version == 0x0102`，機率 1/2¹⁶，但因為 gate 1 擋在前面，AFL 的 input 幾乎全死在這裡之前
- Gate 3：`ptype == 0x42`，機率 1/256，微小但不是 AFL 的主要瓶頸
- Gate 4：`payload_len` 範圍限制，AFL 的 bit-flip 偶爾能過
- Gate 5：XOR checksum 比對——這是「可解但需 concolic reasoning」的 gate

AFL 的 coverage feedback 在這裡幾乎無用：只要 gate 1 不過，後面的 basic block 永遠不會被執行，feedback map 永遠是零。AFL 無法從「更接近 0xCAFEBABE」的輸入中學到任何東西，因為比較指令的結果在 binary 層是跳/不跳，沒有「距離」資訊。

這就是 **hybrid fuzzing（concolic execution 協作）** 和 **directed fuzzing（距離引導）** 的切入點：前者直接求解 path constraint，後者加速對特定目標行的收斂。

---

## 任務規格

### 目標程式：target.c

```c
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

/* 多層 magic check 後才 crash 的目標
 * Pure AFL 極難打穿：每層 magic 各 1/2^32 機率 */

static uint32_t compute_xor_checksum(const uint8_t *buf, size_t len) {
    uint32_t s = 0;
    for (size_t i = 0; i < len; i++) s ^= ((uint32_t)buf[i] << (8 * (i % 4)));
    return s;
}

int parse_packet(const uint8_t *data, size_t len) {
    if (len < 12) return -1;

    /* Layer 1: magic header */
    uint32_t magic;
    memcpy(&magic, data, 4);
    if (magic != 0xCAFEBABE) return -1;   /* gate 1 */

    /* Layer 2: version check */
    uint16_t version;
    memcpy(&version, data + 4, 2);
    if (version != 0x0102) return -2;      /* gate 2 */

    /* Layer 3: type discriminant */
    uint8_t ptype = data[6];
    if (ptype != 0x42) return -3;          /* gate 3 */

    /* Layer 4: payload length sanity */
    uint32_t payload_len;
    memcpy(&payload_len, data + 7, 4);
    if (payload_len > 64 || payload_len == 0) return -4;

    /* Layer 5: checksum (XOR-based, solvable by concolic) */
    uint32_t expected_cs = compute_xor_checksum(data, 11);
    uint8_t stored_cs = data[11];
    if ((expected_cs & 0xFF) != stored_cs) return -5;  /* gate 5 */

    /* Bug zone: heap overflow if we get here */
    uint8_t *buf = malloc(8);
    if (!buf) return -6;
    memcpy(buf, data + 12, payload_len);   /* 越界複製 */
    printf("packet type=0x%02x payload_len=%u\n", ptype, payload_len);
    free(buf);
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <input_file>\n", argv[0]);
        return 1;
    }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("fopen"); return 1; }
    uint8_t buf[256];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    parse_packet(buf, n);
    return 0;
}
```

將這段程式碼存成 `target.c`。

### 練習目錄結構

```
practice-f/
├── target.c
├── seeds/
│   └── zero.bin          # 12 bytes 全零，初始種子
├── seeds-magic/
│   └── partial.bin       # 手工構造通過 gate 1-3 的 seed（詳見參考解答）
├── out-pure-afl/         # 純 AFL 輸出目錄
├── out-symcc/            # SymCC 輸出目錄（軌 A）
└── out-aflgo/            # AFLGo 輸出目錄（軌 B）
```

### 環境需求

**軌 A（SymCC hybrid）**
- SymCC：需要 LLVM 12，從 `https://github.com/eurecom-s3/symcc` 自行編譯
- `symcc_fuzzing_helper`（SymCC repo 內附）
- AFL++ 用於協作
- 編譯器：`symcc`（替換 clang）

**軌 B（AFLGo directed）**
- AFLGo：從 `https://github.com/aflgo/aflgo` 編譯
- GNU gold linker（`binutils-gold`）
- clang（AFLGo 使用 LLVM pass）

**對照組（必做）**
- AFL++（`afl-fuzz`）
- ASan：`-fsanitize=address`

---

## 期望輸出

### 對照組：純 AFL 30 分鐘

```
# 預期（誠實標注：理論值，非保證）
$ afl-fuzz -i seeds/ -o out-pure-afl/ -- ./target_afl @@

# 30 分鐘後 out-pure-afl/default/crashes/ 幾乎必然為空
# paths discovered 會停在極小值（< 5），因為 gate 1 封死後續 BB
# map density 接近 0%
```

這不是 fuzzer 壞了。純 AFL 在這個場景下就是無能為力——這個對照才是本練習最重要的觀察。

### 軌 A：SymCC hybrid（理論預期）

```
# SymCC 執行一次全零 seed 後，輸出：
# $SYMCC_OUTPUT_DIR/000000  ← 解了 gate 1，magic = BE BA FE CA（little-endian）
# $SYMCC_OUTPUT_DIR/000001  ← 解了 gate 1+2
# $SYMCC_OUTPUT_DIR/000002  ← 解了 gate 1+2+3
# ...持續解 gate，直到出現通過 gate 5 的 input

# AFL++ 讀取這些 input 加入佇列，coverage 立刻提升
# 最終 out-symcc/default/crashes/ 出現 heap overflow（ASAN 報告）
```

### 軌 B：AFLGo directed（理論預期）

```
# AFLGo 以 target.c:44（memcpy 越界）為目標
# warmup 15min：正常 coverage-guided 累積 seed
# 之後：distance metric 接管，優先 mutate 「距離 target 近」的 input
#
# 若配合 seeds-magic/（已過 gate 1-3 的 seed），收斂大幅加速
# 若只用全零 seed，AFLGo 仍需先靠 mutation 碰穿 gate 1（困難）
# → 這是本練習的核心觀察：directed 解決「到達後的加速」，不解決「magic gate 進入」
```

---

## 卡住提示

**提示 1：SymCC 輸出不在 stdout，也不在 AFL 的 queue**

SymCC 的求解結果寫到環境變數 `$SYMCC_OUTPUT_DIR` 指定的目錄。如果沒設這個變數，輸出會進 `/tmp/output`。執行時必須：

```bash
export SYMCC_OUTPUT_DIR=./symcc-inputs
mkdir -p $SYMCC_OUTPUT_DIR
./target_symcc seeds/zero.bin
ls $SYMCC_OUTPUT_DIR/   # 應該出現新 input
```

`symcc_fuzzing_helper` 負責把這個目錄的 input 搬到 AFL 的 queue——如果你直接跑 SymCC 而沒有 helper，AFL 不會看到這些 input。

**提示 2：AFLGo 距離計算需要 gold linker，缺少時靜默 fallback**

AFLGo 的 LLVM pass 在 link 時計算 CG/CFG 距離，這個步驟需要 `ld.gold`。如果系統只有 `ld.bfd`，AFLGo 不會報錯，但距離計算不會生效，退化成普通 AFL。

```bash
# 確認 gold 可用
which ld.gold
# 若不存在：
sudo apt install binutils-gold
# 編譯時強制使用 gold：
export LDFLAGS="-fuse-ld=gold"
```

如果跑了 AFLGo 但效果和普通 AFL 一樣，第一件事是確認 gold linker 有沒有被用到。

**提示 3：AFLGo directed 不解決 magic gate 進入問題**

很多人誤解 directed fuzzing 的能力範圍：AFLGo 的「距離」是 CFG 上到 target site 的跳轉距離，不是輸入空間的語義距離。

一個全零 input 的 CFG 距離 = gate 1 到 crash site 的距離。AFLGo 會優先 mutate 這個 input，但 mutation 還是隨機的——翻出 `0xCAFEBABE` 的機率依然是 1/2³²。

**正確使用方式**：給 AFLGo 一個「已通過 gate 1-3」的 seed，讓 directed 發揮它真正的長處——加速在語義上已接近 bug 的 input 的收斂。這正是本練習要求你同時準備 `seeds-magic/` 的原因。

**提示 4：gate 5 的 checksum 怎麼手算**

XOR checksum 覆蓋 `data[0..10]`（共 11 bytes），結果的低 8 bit 存在 `data[11]`。構造 seed 時先填好 byte 0-10，再算 checksum 填回 byte 11。Python 計算：

```python
data = bytearray(b'\xbe\xba\xfe\xca'   # magic LE
                 b'\x02\x01'            # version LE
                 b'\x42'                # ptype
                 b'\x10\x00\x00\x00'   # payload_len = 16
                 b'\x00')               # checksum placeholder
s = 0
for i, b in enumerate(data[:11]):
    s ^= b << (8 * (i % 4))
data[11] = s & 0xFF
```

---

## 實作步驟

### 步驟 1：對照組——建立純 AFL baseline

```bash
mkdir -p practice-f/seeds practice-f/out-pure-afl
cd practice-f

# 建立全零 seed
python3 -c "import sys; sys.stdout.buffer.write(b'\x00' * 12)" > seeds/zero.bin

# 用 ASan 插樁編譯（不用 AFL 插樁，先確認程式本身能跑）
clang -fsanitize=address -g -O0 -o target_asan target.c
./target_asan seeds/zero.bin   # 應該靜默退出，return -1

# 用 AFL++ 插樁編譯
afl-clang-fast -fsanitize=address -g -O0 -o target_afl target.c

# 跑 AFL 30 分鐘
afl-fuzz -i seeds/ -o out-pure-afl/ -t 1000 -- ./target_afl @@
# Ctrl+C 停止後，確認 crashes/ 目錄狀態
ls out-pure-afl/default/crashes/   # 預期：只有 README.txt，無 crash
```

記錄：`paths_found`、`map_density`、執行速度，這是 baseline。

### 步驟 2：手工構造「magic seed」（兩軌共用）

在進入任一軌之前，先用 Python 手工構造通過所有 gate 的 seed，用來驗證 crash 路徑確實存在，也作為 AFLGo 的初始種子。

```python
#!/usr/bin/env python3
# gen_seed.py

import struct

def compute_xor_checksum(buf):
    s = 0
    for i, b in enumerate(buf):
        s ^= b << (8 * (i % 4))
    return s & 0xFFFFFFFF

# 構造 header（11 bytes，checksum 位置先填 0）
header = bytearray()
header += struct.pack('<I', 0xCAFEBABE)   # bytes 0-3: magic
header += struct.pack('<H', 0x0102)        # bytes 4-5: version
header += bytes([0x42])                    # byte 6: ptype
header += struct.pack('<I', 16)            # bytes 7-10: payload_len = 16
header += bytes([0x00])                    # byte 11: checksum placeholder

# 計算並填入 checksum
cs = compute_xor_checksum(header[:11])
header[11] = cs & 0xFF

# payload（16 bytes，全 A，觸發越界複製到 8-byte heap chunk）
payload = b'A' * 16

seed = bytes(header) + payload
print(f"seed length: {len(seed)} bytes")
print(f"hex: {seed.hex()}")
print(f"checksum: 0x{cs & 0xFF:02x}")

with open('seeds-magic/magic_seed.bin', 'wb') as f:
    f.write(seed)
print("written to seeds-magic/magic_seed.bin")
```

```bash
mkdir -p seeds-magic
python3 gen_seed.py

# 驗證：用 ASAN binary 跑這個 seed，應觸發 heap overflow
./target_asan seeds-magic/magic_seed.bin
# 預期：AddressSanitizer: heap-buffer-overflow
```

如果這一步 ASAN 沒有 crash，先不用進行後面的步驟——是 seed 構造有問題。

### 步驟 3：軌 A——SymCC hybrid fuzzing

> 誠實標注：SymCC 在 WSL2 上需要自行從源碼編譯，LLVM 12 是必要版本（不是 LLVM 13/14），這個環境準備可能需要 1-2 小時。如果環境未就緒，跳到「理論執行流程」閱讀理解即可，重點是掌握工作原理。

**3a. SymCC 環境確認**

```bash
# 確認 symcc 可執行
which symcc
symcc --version   # 應顯示 SymCC 版本資訊

# 確認 symcc_fuzzing_helper 可執行
which symcc_fuzzing_helper
```

**3b. 編譯 target**

```bash
# SymCC 編譯（不需要額外 flag，SymCC 替換整個 compiler）
symcc -fsanitize=address -g -O0 -o target_symcc target.c

# 普通 AFL++ 編譯（協作用）
afl-clang-fast -fsanitize=address -g -O0 -o target_afl_symcc target.c
```

**3c. 啟動協作 fuzzing**

```bash
mkdir -p out-symcc symcc-inputs

# 終端 1：主 AFL（secondary 模式）
afl-fuzz -i seeds/ -o out-symcc/ -S afl -- ./target_afl_symcc @@

# 終端 2：SymCC helper（連接 AFL 和 SymCC）
# helper 會從 AFL queue 取 input → 跑 SymCC → 把結果推回 AFL queue
symcc_fuzzing_helper \
    -o out-symcc/ \
    -a afl \
    -n symcc \
    -- ./target_symcc @@
```

**3d. 觀察點**

```bash
# 每 5 分鐘確認一次
watch -n 30 'ls out-symcc/symcc/queue/ | wc -l'

# SymCC 解出的 input 進入 queue 後，AFL 的 paths_found 應快速增長
# 最終在 out-symcc/afl/crashes/ 或 out-symcc/symcc/crashes/ 出現 crash
```

**理論執行流程（供環境未就緒者閱讀）**：

SymCC 對全零 seed 執行時，會走到 gate 1 的比較：
```
if (magic != 0xCAFEBABE)  →  if (input[0:4] != 0xCAFEBABE)
```
SymCC 的 runtime 把這個比較建模為 symbolic constraint：
```
sym_var_0 == 0xBE && sym_var_1 == 0xBA && sym_var_2 == 0xFE && sym_var_3 == 0xCA
```
Z3 solver 求解，輸出一個滿足 gate 1 的新 input。新 input 被跑一次，走到 gate 2，繼續 concolic 求解。這個過程持續到所有 gate 都被解開，包括 gate 5 的 XOR checksum——因為 checksum 是純算術運算，Z3 可以直接求解。

### 步驟 4：軌 B——AFLGo directed fuzzing

> 誠實標注：AFLGo 需要 gold linker 和特定版本的 LLVM pass，如果環境有問題，觀察 AFLGo 退化成普通 AFL 的行為本身也是一個有效的學習點。

**4a. 準備 targets.txt**

```bash
# targets.txt 格式：file:line
echo "target.c:44" > targets.txt
# 第 44 行是 memcpy(buf, data + 12, payload_len)，即 crash site
```

**4b. AFLGo 編譯流程**

AFLGo 的編譯是兩階段的：

```bash
# 階段 1：產生 CFG/CG 並計算距離（需要 gold linker）
export CC=/path/to/aflgo/instrument/aflgo-clang
export LDFLAGS="-fuse-ld=gold -Wl,-plugin-opt=save-temps"

mkdir -p temp-aflgo
$CC -fsanitize=address -g -O0 \
    -fno-inline \
    -distance=/tmp/distance.cfg.txt \
    -targets=$(pwd)/targets.txt \
    -outdir=$(pwd)/temp-aflgo \
    -flto \
    $LDFLAGS \
    -o target_aflgo target.c

# 計算距離（AFLGo 附帶的腳本）
/path/to/aflgo/scripts/gen_distance_fast.py \
    temp-aflgo/ \
    /tmp/distance.cfg.txt \
    target_aflgo

# 階段 2：重新編譯，嵌入距離資訊
$CC -fsanitize=address -g -O0 \
    -distance=/tmp/distance.cfg.txt \
    -o target_aflgo_final target.c
```

**4c. 啟動 AFLGo**

```bash
mkdir -p out-aflgo

# 使用 magic seed，而不是全零 seed
afl-fuzz \
    -i seeds-magic/ \
    -o out-aflgo/ \
    -t 5000 \
    -- ./target_aflgo_final @@

# AFLGo 的 warmup 期（-c 0 到 -c N 切換，或預設 60 分鐘 warmup）
# warmup 期行為和普通 AFL 相同
# warmup 結束後：temperature-based power schedule 接管，
# 優先 mutate 距離 target 近的 input
```

**4d. 觀察 AFLGo vs 純 AFL**

```bash
# 對比：用全零 seed 跑 AFLGo（期望：和純 AFL 一樣慢）
# 對比：用 magic seed 跑 AFLGo（期望：快速找到 crash）
# 對比：用 magic seed 跑純 AFL（期望：也能找到 crash，但可能更慢）
```

### 步驟 5：分析結果，撰寫對比報告

完成三組跑完後，填寫這個對比表：

| 方法 | seed 類型 | 30 分鐘後 paths | 是否找到 crash | 備注 |
|------|----------|----------------|---------------|------|
| 純 AFL | 全零 | ? | 否（預期） | gate 1 封死 |
| SymCC hybrid | 全零 | ? | 是（預期） | concolic 解 gate |
| AFLGo | 全零 | ? | 否（預期） | directed 不解 magic |
| AFLGo | magic seed | ? | 是（預期） | directed 加速收斂 |
| 純 AFL | magic seed | ? | 是（預期） | baseline 對照 |

最後一行很重要：如果給純 AFL 一個 magic seed，它其實也能找到 crash（gate 5 剩 1/256 機率，mutation 很快碰到）。這說明「magic seed 的品質」和「directed/hybrid 的能力」是正交的兩個維度。

---

## 完整參考解答

<details><summary>參考解答</summary>

### 解答 1：手工構造通過所有 gate 的 input

分析每個 byte 的含義：

```
offset  len  值              說明
------  ---  ---            ----
0-3     4    BE BA FE CA    0xCAFEBABE，little-endian
4-5     2    02 01          0x0102，little-endian
6       1    42             ptype = 0x42
7-10    4    10 00 00 00    payload_len = 16，little-endian
11      1    ??             XOR checksum of bytes 0-10
12+     N    41 41...       payload，N = payload_len = 16 bytes
```

checksum 計算（手算）：

```
i=0:  buf[0]=0xBE, shift=0,  contrib=0x000000BE
i=1:  buf[1]=0xBA, shift=8,  contrib=0x0000BA00
i=2:  buf[2]=0xFE, shift=16, contrib=0x00FE0000
i=3:  buf[3]=0xCA, shift=24, contrib=0xCA000000
i=4:  buf[4]=0x02, shift=0,  contrib=0x00000002
i=5:  buf[5]=0x01, shift=8,  contrib=0x00000100
i=6:  buf[6]=0x42, shift=16, contrib=0x00420000
i=7:  buf[7]=0x10, shift=24, contrib=0x10000000
i=8:  buf[8]=0x00, shift=0,  contrib=0x00000000
i=9:  buf[9]=0x00, shift=8,  contrib=0x00000000
i=10: buf[10]=0x00, shift=16, contrib=0x00000000

XOR 累加（小端序意味著 magic bytes 順序是 BE BA FE CA）：
初始 s=0
i=0: s = 0x000000BE
i=1: s = 0x0000BABE
i=2: s = 0x00FEBABE
i=3: s = 0xCAFEBABE
i=4: s = 0xCAFEBABC  (XOR 0x02)
i=5: s = 0xCAFEBBBC  (XOR 0x0100)
i=6: s = 0xCABC BBBC  (XOR 0x00420000)
i=7: s = 0xDABCBBBC  (XOR 0x10000000)
i=8..10: s 不變（XOR 0）

低 8 bit = 0xBC → data[11] = 0xBC
```

完整 seed（28 bytes）：

```
BE BA FE CA 02 01 42 10 00 00 00 BC
41 41 41 41 41 41 41 41 41 41 41 41 41 41 41 41
```

實際值以 Python 腳本計算為準，手算容易出錯。

### 解答 2：Python seed 生成腳本

```python
#!/usr/bin/env python3
"""
gen_magic_seed.py
生成通過 parse_packet 所有 gate 並觸發 heap overflow 的測試 input。
"""

import struct
import pathlib

def compute_xor_checksum(buf: bytes) -> int:
    s = 0
    for i, b in enumerate(buf):
        s ^= b << (8 * (i % 4))
    return s & 0xFFFFFFFF

def build_packet(payload_len: int = 16, payload_byte: int = 0x41) -> bytes:
    """
    建構一個通過所有 gate 的封包。
    payload_len > 8 → 觸發 heap overflow（malloc(8) 但複製 payload_len bytes）
    """
    assert 0 < payload_len <= 64, "payload_len 必須在 (0, 64] 範圍"

    header = bytearray()
    header += struct.pack('<I', 0xCAFEBABE)       # bytes 0-3: magic
    header += struct.pack('<H', 0x0102)            # bytes 4-5: version
    header += bytes([0x42])                        # byte 6: ptype
    header += struct.pack('<I', payload_len)       # bytes 7-10: payload_len
    header += bytes([0x00])                        # byte 11: checksum（待填）

    assert len(header) == 12

    cs = compute_xor_checksum(bytes(header[:11]))
    header[11] = cs & 0xFF

    payload = bytes([payload_byte] * payload_len)
    packet = bytes(header) + payload

    print(f"[*] packet length: {len(packet)} bytes")
    print(f"[*] checksum: 0x{cs & 0xFF:02x}")
    print(f"[*] hex dump:")
    for i in range(0, len(packet), 16):
        chunk = packet[i:i+16]
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        print(f"    {i:04x}: {hex_part}")

    return packet

if __name__ == '__main__':
    # 觸發 heap overflow：payload_len=16 > malloc(8)
    pkt = build_packet(payload_len=16)

    out = pathlib.Path('seeds-magic/magic_seed.bin')
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(pkt)
    print(f"[+] written to {out}")

    # 也生成一個 payload_len=1 的 valid（不 crash）版本，供 gate5 驗證
    pkt_valid = build_packet(payload_len=1)
    out2 = pathlib.Path('seeds-magic/valid_seed.bin')
    out2.write_bytes(pkt_valid)
    print(f"[+] written valid (no crash) seed to {out2}")
```

### 解答 3：SymCC 理論執行流程

SymCC 對 `seeds/zero.bin`（12 bytes 全零）執行一次，產生如下 constraint 序列：

**執行路徑 1（全零 input）**：
```
len=12 ✓ → gate 1: 0x00000000 != 0xCAFEBABE → return -1
```
SymCC runtime 記錄到 Z3：`sym[0:4] == 0xBE 0xBA 0xFE 0xCA`（little-endian），求解輸出 new_input_0。

**執行路徑 2（new_input_0，已過 gate 1）**：
```
gate 1 ✓ → gate 2: 0x0000 != 0x0102 → return -2
```
新 constraint：`sym[4:6] == 0x02 0x01`，求解輸出 new_input_1。

**執行路徑 3–5**（類似，逐 gate 解開）

**執行路徑 6（已過 gate 1–4，checksum 不對）**：
```
gates 1-4 ✓ → gate 5: checksum mismatch → return -5
```
SymCC 對整個 XOR 迴圈都有 symbolic 追蹤，constraint 是：
```
(sym[0] XOR (sym[4] << 8) XOR ... XOR sym[10] << 16) & 0xFF == sym[11]
```
這是線性算術，Z3 在毫秒內求解，輸出 new_input_5。

**執行路徑 7（通過所有 gate）**：
```
all gates ✓ → malloc(8) → memcpy(buf, data+12, 16) → heap overflow
```
ASAN 觸發，SymCC 記錄為 crash，AFL 也記錄到 crashes/。

整個過程從「全零 seed 到 crash」不需要任何隨機 mutation，完全由 concolic 求解驅動，通常在幾分鐘內完成。

### 解答 4：AFLGo target 設定說明

`targets.txt` 行數的確定：

```bash
# 確認 memcpy 越界那行的行號
grep -n "memcpy(buf, data" target.c
# 輸出：44:    memcpy(buf, data + 12, payload_len);   /* 越界複製 */
```

也可以設置多個目標：

```
# targets.txt（雙目標版本，延伸挑戰用）
target.c:24
target.c:44
```

AFLGo 的距離計算邏輯：
- 從每個 BB 到 target BB 的最短路徑（在 CG 和 CFG 上）
- 距離越小 → power schedule 分配更多 mutation 能量
- warmup 期結束後，simulated annealing「溫度」下降，increasingly 偏向低距離 input

若 gold linker 不可用的驗證方法：

```bash
# AFLGo 正常工作時，編譯後 binary 中應有距離相關的符號
nm target_aflgo_final | grep "__afl_area_ptr"
# 最直接的驗證：對比純 AFL 和 AFLGo 在 magic seed 上的 crash 發現速度
```

</details>

---

## 測試用例表

| 輸入描述 | 通過的最後 gate | 停止 gate | 回傳值 | 備注 |
|---------|---------------|----------|--------|------|
| 全零 12 bytes | 長度檢查 | gate 1 | -1 | magic=0x00000000 |
| magic=CAFEBABE，其餘全零 12 bytes | gate 1 | gate 2 | -2 | version=0x0000 |
| magic+version 正確，其餘全零 12 bytes | gate 2 | gate 3 | -3 | ptype=0x00 |
| magic+version+ptype 正確，payload_len=0 | gate 3 | gate 4 | -4 | payload_len==0 被拒 |
| magic+version+ptype 正確，payload_len=65 | gate 3 | gate 4 | -4 | payload_len>64 被拒 |
| gates 1-4 正確，checksum=0x00 但正確值不為 0 | gate 4 | gate 5 | -5 | checksum mismatch |
| 完整有效 input，payload_len=1 | 全部通過 | 無 | 0 | malloc(8)，複製 1 byte，安全 |
| 完整有效 input，payload_len=8 | 全部通過 | 無或 crash | 0 或 overflow | 取決於 ASAN shadow 設定，實測為準 |
| 完整有效 input，payload_len=9 | 全部通過 | crash | heap-overflow | 複製 9 bytes 到 8-byte chunk |
| 完整有效 input，payload_len=16 | 全部通過 | crash | heap-overflow | 標準 crash case |
| 完整有效 input，payload_len=64 | 全部通過 | crash | heap-overflow | 最大 payload_len |
| 長度不足 11 bytes，magic 正確 | 無 | len check | -1 | `len < 12` 先擋 |

> payload_len=8 是否真的安全取決於 malloc 實作和 ASAN 的 shadow memory 邊界。glibc 的 malloc(8) 實際上會分配更多 bytes（metadata + alignment），但 ASAN 按要求大小記錄邊界，所以 payload_len > 8 的情況下 ASAN 仍會報告越界。不要靠理論推算，用 ASAN binary 實際跑。

---

## 延伸挑戰

### 挑戰 1：把 gate 5 改成 CRC32，觀察 SymCC 的行為

```c
/* 替換 compute_xor_checksum */
#include <stdint.h>

static uint32_t crc32_table[256];
static int crc32_initialized = 0;

static void crc32_init(void) {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int j = 0; j < 8; j++)
            c = (c >> 1) ^ (c & 1 ? 0xEDB88320U : 0);
        crc32_table[i] = c;
    }
    crc32_initialized = 1;
}

static uint32_t compute_crc32(const uint8_t *buf, size_t len) {
    if (!crc32_initialized) crc32_init();
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < len; i++)
        crc = (crc >> 8) ^ crc32_table[(crc ^ buf[i]) & 0xFF];
    return crc ^ 0xFFFFFFFF;
}
```

SymCC 仍然能 concolic 執行 CRC32（每個 XOR 和移位都可以 symbolic 追蹤），但 constraint 規模會大很多（CRC32 展開後是一個深度 bit-level 的 constraint）。Z3 能解，但可能慢一個量級。

如果把 CRC32 替換成調用 SHA-256 的 checksum，SymCC 會遇到 SHA-256 的非線性 S-box，Z3 constraint 爆炸。SymCC 會切換到 optimistic solving——假設這個 constraint 為真，繼續探索後面的路徑。結果是 SymCC 生成的 input 在 concolic 執行中「通過」了 gate 5，但實際拿去跑 target 時 checksum 仍然不對，gate 5 仍然擋住。這個「optimistic 的代價」值得用實驗觀察。

### 挑戰 2：給 AFLGo 設雙目標，比較收斂速度

```
# targets_dual.txt
target.c:24   ← gate 5 的 if 判斷行
target.c:44   ← crash site
```

```
# targets_single.txt
target.c:44   ← 只設 crash site
```

分別跑兩組，比較哪組更快找到 crash、哪組 paths discovered 更多。AFLGo 在多目標時取每個 BB 到所有 target 距離的最小值，這個設計會讓「接近任何一個目標」的 input 都獲得高 priority，有時反而讓 fuzzer 分心在 gate 5 附近而不推向 crash site。

### 挑戰 3：用 LibAFL 自己實作 magic value 偵測

不依賴 SymCC，在 LibAFL 的 mutator 層實作一個簡化版的 magic byte 偵測：

1. 在 feedback 層記錄每次執行的回傳值（-1/-2/-3/...）
2. 當 feedback 卡在同一個回傳值超過 N 次，觸發「magic value injector」：把已知的 magic bytes 直接寫入對應 offset（0xCAFEBABE 到 offset 0、0x0102 到 offset 4 等）
3. 這是「帶先驗知識的 mutator」，等效於給 AFL 一個 dictionary，但動態生成

這個挑戰的重點是理解 SymCC 的 concolic approach 和「帶 domain knowledge 的 dictionary」在能力上的本質差異——前者無需先驗知識，後者需要逆向分析。實際上很多工業 fuzzer 是兩者並用：先用 SymCC/Driller 破 magic gate，再用 AFL 做 coverage-guided exploration，再用 dictionary 加速人已知的結構。

---

## 自我檢核

完成本練習後，應該能回答以下問題：

**原理層面**
- [ ] 為什麼 AFL 的 coverage feedback 對 magic byte 比較「盲」？branch coverage 記錄的是什麼，對 magic gate 有什麼限制？
- [ ] SymCC 的 concolic execution 和 KLEE 的 symbolic execution 最大的工程差異是什麼？為什麼 SymCC 能跑在 fuzzer 旁邊而不是替代 fuzzer？
- [ ] AFLGo 的距離 metric 在 CFG 和 CG 兩層如何計算？warmup 結束後的 power schedule 為什麼叫「simulated annealing」？

**實作層面**
- [ ] 是否成功構造出通過所有 gate 的 input（用 ASAN binary 驗證 heap overflow）？
- [ ] SymCC 的 `$SYMCC_OUTPUT_DIR` 設定是否正確？`symcc_fuzzing_helper` 是否成功把 SymCC 輸出推回 AFL queue？
- [ ] AFLGo 是否使用了 gold linker？如何驗證距離計算有生效（而非退化成普通 AFL）？

**觀察層面**
- [ ] 純 AFL 30 分鐘的結果：paths_found 是多少？是否有任何 crash？這個結果符合理論預期嗎？
- [ ] 給純 AFL 一個 magic seed（通過 gate 1-3）後，它多快找到 crash？與 AFLGo+magic seed 的速度如何比較？
- [ ] 如果只有 directed fuzzing 沒有 hybrid，對於「完全隨機的 magic value 輸入空間」，directed 能提供什麼幫助？

**設計層面**
- [ ] 如果你是 target 的開發者，如何修改程式讓它對 fuzzer 更友好（不是讓它更容易被攻擊，而是讓安全測試更有效率）？
- [ ] 在真實的 CVE 場景中，哪些情況適合用 SymCC hybrid，哪些情況適合用 AFLGo directed？兩者能同時使用嗎？
