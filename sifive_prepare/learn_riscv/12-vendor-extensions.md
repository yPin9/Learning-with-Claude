# Ch 12 — SiFive Intelligence / XuanTie / Vector Crypto 巡禮

> 目標：看業界真正在用的 custom 與 vendor extension，理解每家廠商的優勢 domain、編碼風格、以及 compiler 支援成熟度。面試 SiFive 前能講出「我知道你們有 Intelligence 系列，它補了 V 擴充哪些缺」會很加分。

## 讀這章的方法

每個 extension 都附：

- **為誰設計**（domain / 市場）
- **補什麼空缺**
- **典型指令 / 風格**
- **compiler 支援狀態**

不求你背指令表，求你能講「為什麼這個 extension 存在」。

## SiFive Intelligence 系列

SiFive 針對 **AI inference / DSP / 邊緣運算** 設計的 extension 家族。對應他們的 P-series 跟 X-series 核心。

### XSfVector / XSfvqmaccdod / XSfvqmaccqoq

對 RVV 的補強：加入 **quantized multiply-accumulate**。AI inference 的熱點是 int8 × int8 → int32 的 MAC，RVV 1.0 的 `vmacc` 可以做但效率未極致。SiFive 加了：

```
sf.vqmaccu.4x8x4     # 4x4 int8 outer product + int32 accumulate
sf.vqmaccsu.4x8x4    # signed × unsigned variant
```

一條指令做 16 個 MAC（4×4 matrix 一次算），對 transformer 推論關鍵。

**設計要點**：

- 輸入是 vector register、輸出累加到另一個 vector register（widening）
- 結果在 int32 space（輸入 int8 相乘不會溢位，加起來 32 bit 夠用）
- 跟 RVV 的 vtype 協同（LMUL 必須相容）

### XSfVcp (Coprocessor Interface)

更大膽：**把整個 co-processor 介面標準化**。客戶可以掛自家的 AI accelerator 當作 SiFive core 的外掛，透過 XSfVcp 指令傳資料。

```
sf.vc.x      # pass scalar to coproc
sf.vc.v      # pass vector to coproc
sf.vc.i      # pass immediate
sf.vc.xv     # scalar + vector
... 一堆變種
```

這是 SiFive 賣「半客製」的核心賣點 — 客戶買 RISC-V core、掛自己的 accelerator、用 XSfVcp 串接。

### compiler 支援

- **LLVM**: 有 upstream，`-march=rv64gcv_xsfvcp` 等可用
- **GCC**: 在加（2025 仍進度中）
- **Intrinsic**: `__riscv_sf_vc_x_*` 系列

SiFive 的 compiler 團隊主力就在做這些。**job spec 的「add new RISC-V extensions for all SiFive processor families」多半是這個範疇**。

## T-Head XuanTie 系列

T-Head（阿里巴巴 / 倚天） 的 XuanTie（玄鐵）核心。中國市場主力，大量 extension。

### XTHeadBa / XTHeadBb / XTHeadBs

**T-Head 版本的 bitmanip**，比標準 B 擴充早出，所以指令編碼跟 Zba/Zbb/Zbs 不同但功能重疊。

```
th.srriw    rd, rs, shamt        # rotate right immediate word (T-Head 版)
th.ext      rd, rs, msb, lsb     # bit-field extract
```

**compiler 要區分**：同樣是 bitmanip，碰到 `-march` 有 `_xtheadbb` 就走 XTHead 編碼，有 `_zbb` 就走標準。有些 compiler 會雙重支援以滿足不同客戶。

### XTHeadMemIdx / XTHeadMemPair

指令擴充的 memory 家族：
- `th.lbib` (load byte with immediate increment, before) — 類似 ARM 的 pre-increment
- `th.ldd` / `th.sdd` — load/store **pair**（兩個 register 一起）

這些補了標準 RISC-V 故意不給的「load/store pair」（base RV 覺得 load-store 架構就該單純）。T-Head 認為效能差太多不得不做。

### XTHeadSync / XTHeadCmo

Cache / memory 操作：
- `th.sync.s` — sync instruction stream
- `th.dcache.iall` — invalidate all dcache

對應後來標準化的 Zicbom 相關，但 T-Head 早做、編碼不同。

### compiler 支援

- **LLVM 15+** 支援，透過 `-march=rv64gc_xtheadba_xtheadbb_xtheadcondmov_xtheadmac_xtheadmemidx_xtheadmempair_xtheadsync`（真的很長）
- GCC 13+ 支援

T-Head 是 upstream-friendly 的 — 他們的 extension 都公開 spec、主動貢獻 LLVM/GCC。

## Andes 系列

台灣 RISC-V 老兵，早在 RV 標準化前就做自家 RV-like ISA。他們的 extension 偏 DSP / embedded。

### XAndesPerf / XAndesDsp

- Saturating arithmetic 指令
- Round + shift 一起做
- Mac variants

主要對應 audio/DSP workload。Andes 的客戶多是 IoT / MCU。

### compiler 支援

GCC 支援較深（他們自己 maintain fork）。LLVM upstream 較少，多半要用 Andes 提供的 prebuilt toolchain。**這是小廠 extension 的典型困境**：生態分散。

## Vector Crypto extension（Zvbb / Zvbc / Zvkg / Zvkned / Zvknha / Zvknhb / Zvksed / Zvksh）

這其實是**標準 extension**（不是 vendor custom），但值得放這章因為：

- 2024 年才 ratify
- 高度專門化（每個 Zv* 只做一件 crypto primitive）
- 跟 V 擴充綁緊（每條指令都操作 vector register）

| Extension | 內容                    |
|-----------|------------------------|
| `Zvbb`    | Vector bit-manipulation |
| `Zvbc`    | Vector carry-less multiply |
| `Zvkg`    | Vector GCM/GMAC (for AES-GCM) |
| `Zvkned`  | Vector AES encrypt/decrypt |
| `Zvknha`  | Vector SHA2 (SHA-256 subset) |
| `Zvknhb`  | Vector SHA2 (SHA-512 subset) |
| `Zvksed`  | Vector SM4 (Chinese block cipher) |
| `Zvksh`   | Vector SM3 (Chinese hash) |

**為什麼一個 crypto primitive 要一組 vector 指令？**

因為 crypto 的每一輪都有高度並行性。AES 的 AddRoundKey 是 byte-wise XOR，`vxor.vv` 就是；但 SubBytes (S-box lookup) 對應 `vaesem.vs`，這是新加的。有專用指令比軟體 lookup table 快 10×。

典型 usage：

```c
// AES-256 encrypt
vxor.vv   v0, v0, v16        // AddRoundKey
vaesem.vs v0, v17            // SubBytes + ShiftRows + MixColumns + AddRoundKey (一條)
// ... repeat for each round
```

OpenSSL 與 Linux kernel crypto 的 RISC-V fast path 2024 後全面使用。

## 常見誤會：vendor extension vs standard extension

很多人把「有字母 X 開頭」當作「非標準」，**但 X 只代表 'extension by convention'**。有些情況：

- `XSfVector` = SiFive vendor extension（明確）
- `Xventana` = Ventana 的 custom（明確）
- `Xsfvqmaccdod` 會被寫成 `sf_vqmaccdod`（命名風格混亂）

2024 的新規範把 vendor 前綴統一成 `X<vendor><name>`，但舊命名還會繼續出現。**讀 spec 時看前綴判斷**。

## Profile 怎麼收拾這堆 extension？

`RVA22` / `RVA23` 這類 profile 文件是解法：**列出一個「application class」的 mandatory + optional 擴充清單**。

例如 RVA23 要求：
- Base: RV64I
- Mandatory: M, A, F, D, C, Zicsr, Zifencei, Zicntr, Zihpm, Zba, Zbb, Zbs, Zcb, Zihintpause, Zca, Zcd, Zfhmin, V, Zvbb, Zvbc, H
- Optional: Zvkn, Zvksh, Zfh, ...

**一個 RVA23 binary 保證能跑在所有 RVA23 硬體**。vendor extension 不進 profile，所以**跑 vendor extension 的 binary 只能在對應硬體跑**。這就是為什麼 Linux distro 發行 binary 要嚴格遵守 profile。

## SiFive 工程師會碰哪些？

從 job spec 推測：

1. **標準 extension 的 compiler 支援**（RVA23 的所有成員）
2. **SiFive Intelligence 系列**（XSfVector / XSfVcp / Intelligence）
3. **客戶客製 extension**（SiFive 幫客戶設計一條新指令，toolchain 要跟上）
4. **效能 tuning**：為 SiFive 的 P550 / P670 / P870 等 core 調 scheduling model、autovectorizer heuristic

這些都需要「能讀 ISA spec + 能改 LLVM/GCC backend + 能解讀 perf data」的三位一體能力。

## 作為候選人的準備

### 讀哪些 spec / 文件

- **SiFive Intelligence Extension Specifications**: <https://github.com/sifive/sifive-intelligence-extensions-specifications>
- **XuanTie XTHead Spec**: <https://github.com/T-head-Semi/thead-extension-spec>
- **Vector Crypto spec**: <https://github.com/riscv/riscv-crypto>

不求背，求能在面試時講出「它填補什麼空白」。

### 讀哪些 compiler 碼

- `llvm/lib/Target/RISCV/RISCVInstrInfoXSf*.td`（SiFive）
- `llvm/lib/Target/RISCV/RISCVInstrInfoXTHead*.td`（T-Head）
- `gcc/config/riscv/sync.md`（atomic pattern，多 vendor 分支）

選其中一個讀 50 行，看 TableGen 怎麼描述一條指令。

### 實測一下

```bash
riscv64-unknown-elf-gcc -march=rv64gc_xsfvector -E /dev/null   # 看 compiler 認不認
```

不認 → 你的 toolchain 版本太舊。

## 常見誤會

1. **「SiFive 的 extension 都是 SiFive 設計」**：有些是客戶驅動的（某大客戶付錢，SiFive 做出 extension 然後也可能 open source）。
2. **「T-Head extension 是中國專屬」**：T-Head 積極 upstream，全世界 LLVM / GCC 都能支援。用不用是客戶決定，不是政治問題。
3. **「vendor extension 永遠不進標準」**：有前例。Vector Crypto 裡的很多 idea 來自 vendor 提案，經過 working group 打磨後變成 Zvk*。
4. **「Zvkned 就是 AES」**：不完整。Zvkned 只做一輪（round function），完整 AES 要軟體 orchestrate。這是刻意設計 — 讓指令 latency 短、pipeline 友善。
5. **「compiler auto-vectorize 就會用 Zvbb」**：不保證。當前 LLVM 對 crypto pattern 的 auto-vectorize 還弱，手寫 intrinsic 或 asm 是主流。

## 動手練習

1. clone SiFive 的 intelligence-extensions 文件，隨便挑一條 `sf.vqmacc*`，寫 C 語言呼叫它的 intrinsic（需要 `<riscv_vector.h>` 的對應 helper）。
2. 找一段 LLVM 的 `RISCVInstrInfoXSfvcp.td`（SiFive Vcp），說出其中一個 pattern 的 input / output / scheduling class。
3. 比較 Zbb 的 `rol` 跟 XTHeadBb 的 `th.srriw`：兩者做的事相同嗎？編碼同嗎？用 objdump 驗證。
4. 寫一段 AES-128 encrypt、用 `-march=rv64gcv_zvkned` 編，比對軟體實作的效能差距。
5. 找一個你熟悉的 domain（例：影像處理、網路協議），假設你是 SiFive 工程師面試客戶，**提一個可能有用的 custom extension** — 要能 justify。

## 自我檢核

- [ ] 我能說出 SiFive Intelligence 的兩個代表 extension 以及它們解決什麼問題
- [ ] 我能區別 vendor custom（XSf / XTHead / XAndes）跟標準 Z* 擴充
- [ ] 我知道 Vector Crypto 家族的 Zvk* 分支作用
- [ ] 我能解釋 profile 如何收拾 extension fragmentation
- [ ] 我能列出 SiFive compiler 工程師日常可能碰的三類 extension

下一章看 extension 的政治 — 從 proposal 到 ratified 的完整流程，以及為什麼 RISC-V 的標準化速度這麼重要。

→ [Ch 13 擴充是怎麼從 proposal 走到 ratified 的](./13-extension-ratification.md)
