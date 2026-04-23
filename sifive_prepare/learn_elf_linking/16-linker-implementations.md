# Ch 16 — ld / gold / lld / mold：四個 linker 的取捨

> 目標：理解四個主流 linker 的歷史、設計哲學、對 RISC-V 的支援。面試被問「你偏好哪個 linker、為什麼」能答得有料。

## 四個 linker 的 timeline

```
1987: GNU ld            （binutils 的一部分，C 寫的）
2008: gold               （Google 為 C++ 大型 project 設計）
2017: LLD                （LLVM 官方 linker）
2020: mold               （Rui Ueyama 個人 project，後來加入 LLVM）
```

每個都解決前任的某個問題，但也有 trade-off。

## GNU ld — 祖師爺

**出身**：GNU binutils，跟 GCC 一起長大。

**語言**：C，用 BFD (Binary File Descriptor) library。

**優點**：

- 支援所有 ISA（最廣泛）
- 支援所有 ELF feature（linker script、overlay、relaxation...）
- 經過 30+ 年戰場檢驗
- GNU 生態的 default

**缺點**：

- **慢**：大型 C++ project 可以 link 一小時
- 記憶體用量高
- Single-threaded（主流 version 沒 parallelize）

**典型 target**：

- 通用 Linux
- 嵌入式 / baremetal（script 支援最完整）
- ARM / MIPS / 罕見 arch

RISC-V 支援：**完整**，包括 relaxation、all extensions。

## gold — 曾經的 revolution，後來的遺產

**出身**：Ian Lance Taylor 在 Google 寫的。設計目標：**加速 Chrome / Android 等大型 C++ project 的 link**。

**語言**：C++。

**優點**：

- 比 GNU ld 快 5-10×
- 第一個 widely used 的 "現代 linker"
- 2008-2015 年大幅改進 link 體驗

**缺點**：

- 只支援 ELF（不像 GNU ld 支援 COFF、a.out 等）
- **不支援 RISC-V 的 relaxation**
- 維護停滯（Google 轉做 LLD）

**現狀**：

- Fedora 2020 後不再預裝
- Ubuntu 仍有但不是 default
- 新 project 不建議

**結論**：歷史有意義，實際選擇已被 LLD / mold 超越。**RISC-V 生態不要用 gold**。

## LLD — LLVM 的答案

**出身**：LLVM 官方 linker，2017 年取代 system ld 成為 clang 預設。

**語言**：C++（模板風，乾淨）。

**優點**：

- 快，跟 gold 接近、比 GNU ld 5-10×
- **Parallel design**：多核 scale 好
- 支援 ELF、COFF、Mach-O、WASM
- RISC-V relaxation 完整支援（2024 後）
- LLVM 生態原生整合（LTO、CFI、...）
- Active maintenance

**缺點**：

- **Linker script 支援不如 GNU ld 完整**：複雜嵌入式 script 可能遇到 corner case 不支援
- 某些 GNU 特定 flag / 行為不一致

**RISC-V 支援**：

- 基本功能：完整
- Relaxation：LLVM 17+ 穩定
- 向量相關 relocation：新（2024）加入
- Custom extension 的 relocation：有些 edge case

**典型用途**：

- LLVM 生態（Rust、Swift、Apple 相關）
- 快 CI（Google、Meta 內部都用）
- 輕量 Linux distro

## mold — 個人 project 的革命

**出身**：Rui Ueyama（LLD 原作者之一）2020 年獨立開發。純 C++。現已成為 LLVM 子 project。

**設計哲學**：**極致並行化**。把每個 link 階段盡可能切小塊、parallel 執行。

**優點**：

- **再快 2-3×**：大型 project link 20 秒變 5 秒
- 對大型 project 效益最明顯
- 現代 C++ 風格，原始碼乾淨
- MIT license

**缺點**：

- 比較新（2020+），某些 corner case 尚未處理
- **RISC-V 支援較晚**：2024 才加 RISC-V relaxation
- 不支援所有罕見 feature（linker script 的某些寫法、overlay 等）
- 不支援非 ELF format

**現狀**（2026 時點）：

- Fedora / Debian 有 package
- Linux kernel 開始考慮用 mold build（快很多）
- LLVM 本身仍用 LLD

## 一個簡單比較

用 Chromium（~1 GB binary）的 link time：

```
GNU ld:    ~3 分鐘
gold:      ~20 秒
LLD:       ~15 秒
mold:      ~5 秒
```

差距驚人。對 incremental build 特別明顯。

## RISC-V 的選擇

作為 SiFive compiler 工程師，你會接觸：

1. **GNU ld（主力）**：所有 RISC-V production toolchain 默認用。customer support 要優先這個。
2. **LLD**：LLVM 生態配 Clang 用。SiFive 自家 Freedom Studio 等工具鏈可能預設。
3. **mold**：新興但未成熟到 RISC-V 全面部署。關注動態。
4. **gold**：**不要用**。RISC-V 支援不夠。

面試可能問：「若要加一個新 relocation type 到 linker，你會選哪個實作？」

合理答：**GNU ld 跟 LLD 都要做**（GNU 是 production、LLD 是 LLVM 生態）。先做 LLD（source 乾淨、快迭代），再 port 到 GNU ld。

## 讀 linker 原始碼的入口

### GNU ld

- `bfd/` 目錄：BFD library，處理 ELF
- `bfd/elfnn-riscv.c`：RISC-V 相關 logic
- `ld/` 目錄：主 linker
- `ld/scripttempl/`：內建 linker script templates

BFD 是 C + 大量 macro，閱讀門檻最高。但功能全面。

### LLD

- `lld/ELF/`：ELF linker 主程式
- `lld/ELF/Arch/RISCV.cpp`：RISC-V 專屬
- `lld/ELF/Relocations.cpp`：relocation 處理
- `lld/ELF/Writer.cpp`：output 產生

C++ 寫的、結構清楚、500-1000 行就能讀完核心 class。**想學 linker 實作最推薦讀 LLD**。

### mold

- `mold/elf/`：主程式
- `mold/elf/arch-riscv.cc`：RISC-V 專屬

C++20，現代化設計。用 parallel-for 很激進。讀了你會佩服 engineering。

## 看你用哪個 linker

```bash
gcc -v hello.c -o hello 2>&1 | grep collect2
# 看實際被呼叫的 linker
```

或：

```bash
ld --version              # 系統預設 ld
ld.lld --version          # LLVM lld
mold --version            # mold
ld.gold --version         # gold (如果安裝)
```

## 切換 linker

```bash
# 用 LLD
gcc -fuse-ld=lld ...

# 用 mold
gcc -fuse-ld=mold ...

# 用 gold
gcc -fuse-ld=gold ...
```

Clang 也支援 `-fuse-ld=`. 對 make 來說：

```
export LDFLAGS="-fuse-ld=mold"
```

## Link 時間 optimization 的實際建議

如果你開發過程 link 慢：

1. **換 linker**（GNU ld → LLD or mold）→ 5-10× 快
2. **開 `-Wl,--gc-sections`**：砍無用 section
3. **關 LTO**（除非 release build）
4. **用 split DWARF**
5. **`--incremental` linker**：LLD 有，實驗性質
6. **CCache / DistCC** 類工具

對 CI：一次 clean build mold 可以省 50% build time。值得切換。

## 選 linker 的決策樹

```
是嵌入式 / baremetal / kernel?
  是 → GNU ld（linker script 完整支援）
  否 → 繼續

是 LLVM 生態的 project?
  是 → LLD
  否 → 繼續

是大型 project (> 100 MB binary) 且想要最快?
  是 → mold
  否 → LLD 或 GNU ld 都行
```

## Linker 特性支援表

| Feature | GNU ld | LLD | mold | gold |
|---------|--------|-----|------|------|
| RISC-V base | ✓ | ✓ | ✓ | ✗ |
| RISC-V relaxation | ✓ | ✓ | ✓ (2024+) | ✗ |
| Linker script (full) | ✓ | ✓ (mostly) | partial | ✓ |
| LTO | ✓ | ✓ | ✓ | ✓ |
| PIE / RELRO | ✓ | ✓ | ✓ | ✓ |
| Vector Crypto | 部分 | ✓ | ✓ | ✗ |
| Custom ext patches | 容易 | 較易 | 中等 | 難 |
| Incremental link | ✗ | exp | ✗ | ✗ |
| Multi-thread | 部分 | ✓ | ✓✓ | ✗ |

**對 SiFive 工程師**：GNU ld 仍是最終 target（customer 用），LLD 是內部開發環境，mold 關注中。

## RISC-V 生態的 linker 現況

Linux distro：

- Ubuntu 24.04：GNU ld default、可切 LLD
- Fedora 40：GNU ld default、LLD 可選
- Arch：GNU ld default

Embedded toolchain：

- `riscv64-unknown-elf-ld`（GNU ld）：標準
- LLD 近年增加支援，有些 vendor 在用

**「新寫的 RISC-V project 選哪個 linker？」** 2026 時點我的建議：

- 主要 dev：LLD（快、功能夠、RISC-V 支援完整）
- CI / release：仍測 GNU ld（確保跟 GNU toolchain 相容）
- 別用 gold

## 一個真實 war story

某 SiFive 客戶 2023 年回報：「Vector Crypto code 在 LLD 下 link 產生錯誤 encoding」。

Debug 流程：

1. 用 GNU ld 同個 `.o` 重 link → 正常
2. 比對兩個 binary 的 `.text` → 發現 LLD 某條 `vaesem.vs` 被 relax 錯
3. 查 LLD source → RISCV.cpp 裡某個 check 漏了 vector extension 的 relocation type
4. 送 patch、upstream 修掉、發新版

**這是 toolchain 工程師的日常**。同時懂兩個 linker 的人修這種 bug 最快。

## 常見誤會

1. **「新 linker 就一定好」**：看 feature 需求。嵌入式用 mold 容易踩坑。
2. **「LLD 比 GNU ld 更多 feature」**：不。GNU ld feature 更多（老但廣）。LLD 勝在速度。
3. **「gold 被廢棄很可惜」**：當初 gold 是 revolution，但 LLD 超越它了。一個時代的產物。
4. **「mold 免費所以不夠好」**：mold 是 MIT license，商業支援需求可以找作者（paid consulting）。
5. **「切換 linker 會影響 code」**：理論上不會（都遵守 ELF + linker script spec）。實務上 edge case 可能有差。

## 動手練習

1. 安裝 LLD 跟 mold，同一 `hello.c` 用四個 linker 各 build 一次，用 `ls -la` 比 binary size、`time` 比 link 時間。
2. 用 mold build 一個中型 open source project（例 SQLite），對比 GNU ld 時間差。
3. 讀 LLD 的 `lld/ELF/Arch/RISCV.cpp` 的 `relaxTlsLe` function（LLD 對 TLS relax 的實作）。
4. 切換你自己的 project 用 LLD，觀察有沒有 warning 或行為差。
5. 在 GitHub 找 LLD RISC-V 相關 open issue，讀一個 discussion。這是 upstream contribution 的入口。

## 自我檢核

- [ ] 我能列四個 linker 的出身、設計哲學、優缺點
- [ ] 我能解釋為什麼 gold 不適合 RISC-V
- [ ] 我知道什麼時候選 GNU ld vs LLD vs mold
- [ ] 我能切換 `-fuse-ld=` 使用不同 linker
- [ ] 我知道 LLD 的源碼入口在哪

下一章是實戰 — 讀 real-world link error 並 debug。這章整合前面所有知識。

→ [Ch 17 實戰 debug：讀懂真實 link error](./17-debug-real-link-errors.md)
