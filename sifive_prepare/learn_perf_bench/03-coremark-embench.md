# Ch 3 — Coremark / Embench：RISC-V 與嵌入式主力

> 目標：理解 Coremark 跟 Embench 兩個 embedded / RISC-V 常用 benchmark 的結構、差異、跑法。這比 SPEC 更常在 RISC-V 社群被引用。

## 為什麼這兩個重要

RISC-V 的 embedded / low-power 市場重要。SPEC CPU 對 MCU 太大：

- SPEC mcf_r 需要 4 GB RAM
- MCU 可能只 512 KB
- SPEC 3 小時跑完，MCU perf 量測常要 10 秒尺度

**Coremark + Embench** 填這個空缺。

## Coremark：最簡單的標準 benchmark

**Coremark** 由 EEMBC（Embedded Microprocessor Benchmark Consortium）2009 推出。取代老舊的 Dhrystone。

### 特點

- **單檔 C code**，~3000 行
- **免費**
- **執行時間可調整**（通常跑 10 秒以上）
- **不用 libc 特定 function**（minimal dependency）
- **single-threaded**（也有 CoremarkPro 是多執行緒版）

### 測什麼

Coremark 內部跑三種 workload：

1. **List processing**：linked list search, sort, reverse
2. **Matrix math**：int 矩陣運算
3. **State machine**：CRC、input parsing

用這三種 cover 典型 embedded workload。

### 分數：CoreMark/MHz

```
Coremark 跑完印出：
Total ticks      : 12345678
Total time (secs): 12.34
Iterations/Sec   : 8112.34
Iterations       : 100000
CoreMark 1.0 : 8112.34 / GCC 11.2 -O2 ...
```

**CoreMark/MHz = Score / CPU clock MHz**。這是歸一化分數：

```
SiFive U74 @ 1200 MHz: Coremark = 5040
                      Coremark/MHz = 4.2
```

**越高越好**。典型值：

- Cortex-M0: ~1.7
- Cortex-M4: ~3.4
- Cortex-M7: ~5.0
- RISC-V SiFive U74: ~4.2
- RISC-V SiFive P870: ~9.0 (high-end)

### 缺點

- 太小 → 完全 fit L1 cache → 測不到 memory system
- Pattern 簡單 → compiler 可以過度 optimize
- 只一個 workload → 不能代表 broad coverage

**SiFive 內部從不單靠 Coremark**。但 customer marketing 常看它。

## 跑 Coremark

```bash
git clone https://github.com/eembc/coremark
cd coremark
make PORT_DIR=linux64 XCC=riscv64-linux-gnu-gcc \
     XCFLAGS="-O2 -march=rv64gc" \
     ITERATIONS=500000

qemu-riscv64 ./coremark.exe
```

或 host native：

```bash
make
./coremark.exe
```

跑出來：

```
2K performance run parameters for coremark.
...
CoreMark 1.0 : 12345.67 / GCC 11.2.0 -O2 ...
```

## Coremark 的「PORT」

每個平台要有個 `port` 資料夾：

```
coremark/
├── core_main.c
├── coremark.h
├── linux64/            ← for Linux x86_64
├── barebones/          ← for bare-metal
├── ...
```

自己的 port 要實作：

- `core_portme.h`：timing API、size typedef
- `core_portme.c`：start/stop timer、get time

RISC-V MCU 常需自己寫 port。

## Embench：新一代 RISC-V 友好 benchmark

**Embench** 是 2019 年 release 的 open-source benchmark，設計給現代 embedded。RISC-V 社群重點推動。

### 特點

- **開源**（Apache 2）
- **多個 benchmark**（不是單一 workload）
- **針對 MCU**：小於 1MB 、無 libc/OS 依賴
- **測 code size + speed**
- **設計新、ARM 不佔 legacy 優勢**

### 為什麼要新的

Coremark 被 ARM 優化了十年、pattern 已經被廠商 exploit。Embench team 想重開。

### 結構

Embench 2.0 包含約 20 個 benchmark：

```
aha-mont64      Montgomery modular math
crc32           CRC
cubic           Solve cubic equations
edn             Vector arithmetic
huffbench       Huffman encoding
matmult-int     Integer matrix multiply
md5sum          MD5 hash
minver          Matrix inversion
nbody           N-body simulation
nettle-aes      AES
nettle-sha256   SHA-256
nsichneu        Petri net simulator
picojpeg        JPEG decode
primecount      Prime counting
qrduino         QR code
sglib-combined  Data structure library
slre            Regex engine
st              Statistics
statemate       State machine
tarfind         TAR parser
ud              LU decomposition
wikisort        Sort algorithm
```

涵蓋 math / crypto / parsing / compression 等。

### 分數

Embench 回報兩個 metric：

1. **Speed**：跑 benchmark 的相對時間（normalized）
2. **Size**：binary 的 code size（normalized）

綜合可以反映 "same binary size, how fast" 的 trade-off。

### 跑 Embench

```bash
git clone https://github.com/embench/embench-iot
cd embench-iot

# RISC-V baremetal
./build_all.py --arch=riscv32 --chip=generic --board=riscv32
./run_all.py --target-module=run_spike

# Linux userspace RISC-V
./build_all.py --arch=native --chip=generic --board=default \
    --cc=riscv64-linux-gnu-gcc \
    --cflags="-O2 -march=rv64gc"
./run_all.py --target-module=run_native
```

Build 跟 run 分離。`run_spike` 跑 RISC-V simulator。

## Coremark vs Embench 選擇

| 議題 | Coremark | Embench |
|------|----------|---------|
| 代表性 | 低（單一 workload） | 高（多 workload） |
| 成熟度 | 高（10+ 年）| 中（5 年）|
| 客戶認受 | 廣（傳統） | 增加中 |
| Code size metric | 無 | 有 |
| 作弊風險 | 高（compiler specific）| 中 |
| RISC-V 社群推 | 歷史 | 是 |

**SiFive 雙跑**。Coremark 給傳統客戶、Embench 給技術對話。

## 相關 micro benchmark

### Dhrystone

1984 年，老到不能老。現在不太該用，但某些 MCU datasheet 還印 Dhrystone 分數。

```
DMIPS = Dhrystone MIPS
DMIPS/MHz = 1.0 (VAX 11/780 reference)
Cortex-M0: 0.9
Cortex-M4: 1.25
```

### Whetstone

FP 版 Dhrystone。更老。幾乎不用。

### BEEBS / Milepost

不那麼 mainstream 的 embedded benchmark。RISC-V 社群很少提。

## 本課不會跑完整的 SPEC，但會跑這些

Coremark / Embench 的小尺寸讓它們適合本課的 hands-on：

- 在 QEMU 跑: ~30 秒
- 在 SBC 上跑: ~1-10 分鐘
- Compiler flag iterate 快

**本課 Practice A 會讓你寫一份 Coremark report**。

## Compiler flag 對 Coremark 的影響

實測 SiFive U74 的例子：

```
-O0:             900 Coremark
-O1:            3800 Coremark
-O2:            4500 Coremark
-O2 -mtune=...: 4600 Coremark
-O3:            4650 Coremark
-O2 -flto:      4700 Coremark
```

`-O2` 到 `-O3` 差距小。但 `-flto` 多 2-3%。

**這些數字你自己測 → 建立 intuition**。書本數字會過時。

## 真實 Coremark 數字解讀

**注意「per MHz」**：

- Coremark 不等於 CoreMark/MHz
- 頻率越高、CoreMark 絕對值越高
- CoreMark/MHz 才比較架構

```
SiFive U74 @ 1.2 GHz: Coremark 5000, Coremark/MHz 4.2
SiFive P870 @ 3.0 GHz: Coremark 18000, Coremark/MHz 6.0
```

U74 實際分數高於老 Cortex-M7，但 P870 per-MHz 才真的強。

## 關於 Dhrystone / Coremark 的「unreliable」論

資深 performance engineer 對 Coremark 的看法：

> Coremark 是「給 marketing 的東西」。真 benchmark 用 SPEC / 客戶 workload。

有道理，但也不完全：對 MCU / constrained system 無可替代。**關鍵是別把 Coremark 分數當絕對性能**。

## 動手練習

1. 下載 Coremark，用 x86 native 跑、記分數。
2. Cross-compile RISC-V、用 qemu 跑、對比分數。
3. 改 flag：`-O0`、`-O1`、`-O2`、`-O3`、`-O2 -flto`，各跑一次、記錄。
4. Clone Embench、build + run 一個 benchmark（不用全部）。
5. 讀 SiFive 某 core 的 datasheet，找 Coremark/MHz 數字、對比 ARM 同級 core。

## 常見誤會

1. **「Coremark 分數高 = 實際 workload 快」**：不直接。Coremark 是 single-workload、代表性有限。
2. **「Dhrystone 還在用」**：極少。遇到先 check 這 datasheet 寫作時間。
3. **「Embench 完全取代 Coremark」**：長期可能、短期不會。客戶 habit 難改。
4. **「-O3 永遠比 -O2 好」**：Coremark 上差距小、某些 benchmark -O3 反而差（code size 問題）。
5. **「Coremark 簡單所以可靠」**：反過來。簡單 → pattern 少 → 容易被 compiler over-fit。

## 自我檢核

- [ ] 我能 build + run Coremark 並解讀分數
- [ ] 我知道 CoreMark/MHz 跟 CoreMark 絕對值的差別
- [ ] 我能 build + run Embench 的至少一個 benchmark
- [ ] 我能 justify 「什麼時候該用 Coremark、什麼時候 Embench」
- [ ] 我知道為什麼 resource-constrained device 不用 SPEC

下一章進統計基本功 — 有了 benchmark 數字，怎麼嚴謹處理？

→ [Ch 4 統計基本功：geomean / CI / noise 控制](./04-statistical-basics.md)
