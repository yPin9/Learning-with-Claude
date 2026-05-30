# Ch 8 — Runtime Instrumentation：QEMU mode 與 Frida mode

> **目標**：理解在沒有原始碼的情況下，AFL++ 如何透過 QEMU mode 和 Frida mode 對 binary 插樁，以及它們各自的底層機制與代價。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

閉源二進位（closed-source binary）是 fuzzing 的傳統難題。2014 年 AFL 問世時只支援有原始碼的 target；如果 binary 沒有 source，你只能退回盲目測試（dumb fuzzing），放棄 edge coverage feedback。

同年 AFL 的 `qemu_mode/` 出現，解開了這個限制。原理不是魔法，而是借用了 QEMU 已有的 dynamic binary translation 框架，在翻譯 basic block 的同時偷偷插入 bitmap 寫入。這讓 AFL 第一次能對 stripped ELF、商用軟體、甚至 firmware 做 coverage-guided fuzzing。

AFL++ 繼承並大幅擴展這條路線：

- **QEMU mode**：基於 QEMU User mode（`qemu-linux-user`）的修改版，針對 x86_64 / aarch64 / mips 等多架構。
- **Frida mode**：基於 Frida 的 GUM engine / Stalker，不需要 ptrace 就能 instrumentation，且在某些場景比 QEMU 更快。
- **Unicorn mode**：用 Unicorn CPU emulator，專為 embedded firmware（沒有完整 OS syscall）設計。

本章的核心問題：閉源 binary 怎麼讓 AFL++ 拿到 coverage？每種模式的翻譯/instrumentation 在哪一層發生？代價是多少？

---

## 先建立直覺

想像你是個速記員，要在一場全程中文演講中做英文逐字稿。你的做法是「翻譯完一段，立刻輸出一段」——這就是 JIT 翻譯（just-in-time translation）的直覺。

QEMU 的 TCG（Tiny Code Generator，微型程式碼產生器）就在做同一件事：把目標架構的機器碼（例如 ARM binary 在 x86 機器上跑）翻譯成 host 架構能執行的程式碼，一個 basic block 一個 basic block 地翻，翻完快取起來下次直接用。

AFL++ 的做法是「在每個 basic block 的翻譯剛完成、還在 TCG IR 的時候，偷偷在開頭插一段 bitmap 寫入程式碼」。這段插入的程式碼會在 block 被執行時更新 AFL++ 的 64KB coverage bitmap。整個過程對 binary 完全透明——你不動 binary 的任何一個 byte。

Frida mode 的直覺稍微不同：Frida 的 Stalker 是一個 **instruction-level recompiler**，它攔截執行流，把每一條指令「抄一份」到自己管理的記憶體空間，加上 instrumentation hook，再讓 CPU 跑抄好的版本。概念上像是給每條指令都設了個「執行前掛鉤」，但因為是 JIT recompile 而不是 single-step，overhead 比 ptrace breakpoint 低很多。

---

## QEMU mode 原理

### TCG 翻譯流水線

QEMU User mode 的執行核心是 TCG，流水線如下：

```
Guest binary（x86_64）
      │
      ▼ 讀取 basic block
  Decode Frontend
  （把 x86_64 指令解碼成 TCG IR）
      │
      ▼
  TCG Intermediate Representation（IR）
  （平台中立的中間表示，約 50 種 op）
      │
      ▼ ← AFL++ 在這裡插入 bitmap 更新程式碼
  Optimize + Lowerring
      │
      ▼
  TCG Backend
  （把 IR 翻譯成 host 架構的機器碼）
      │
      ▼
  Translation Block Cache（TB cache）
  （翻譯結果快取，同一個 PC 下次直接用）
      │
      ▼
  實際執行（host CPU）
```

AFL++ 修改的關鍵點：在 TCG IR 完成之後、backend 編譯之前，對每個 basic block 的翻譯輸出插入一段相當於以下 C 的邏輯：

```c
/* 插入在每個 basic block 開頭 */
cur_location = (current_block_pc >> 4) ^ (current_block_pc >> 9);
afl_area_ptr[cur_location ^ prev_location]++;
prev_location = cur_location >> 1;
```

這段邏輯和 compile-time instrumentation（Ch 6）的 bitmap 寫入**語意完全一致**，因此 QEMU mode 產出的 coverage 格式和有原始碼時的格式相同，afl-fuzz 的其餘部分不需要改。

### AFL++ 修改的 QEMU 程式碼位置

AFL++ 4.09c 的 QEMU 修改位於 `qemu_mode/patches/` 目錄，主要是：

- `afl-qemu-cpu-inl.h`：定義 `afl_maybe_log()` 函式，在 TB 翻譯時被呼叫。
- `tcg-op-gvec.c` 和 `cpu-exec.c` 的 patch：在 `tb_gen_code()` 裡注入 `afl_maybe_log()` 呼叫。

這些修改是在 QEMU 的 TCG 前端完成的，因此對所有 QEMU 支援的 guest 架構（x86_64、aarch64、mips、riscv...）都自動生效。

### 範例一：對 stripped binary 啟動 QEMU mode

```bash
# 假設 target_no_src 是一個沒有 debug symbol 的 ELF，沒有原始碼
AFL_SKIP_CPUFREQ=1 afl-fuzz -Q -i seeds/ -o out/ -- ./target_no_src @@

# -Q 旗標啟用 QEMU mode
# AFL_SKIP_CPUFREQ=1 跳過 CPU 頻率檢查（虛擬機裡常常無法設定）
# @@ 是輸入檔案佔位符，afl-fuzz 會替換成實際的 seed 路徑
```

建置 QEMU mode（只需要做一次）：

```bash
cd AFLplusplus/
sudo apt-get install -y python3-dev python3-setuptools ninja-build
cd qemu_mode/
./build_qemu_support.sh
# 產出：../afl-qemu-trace
```

`python3-dev` 必須安裝，否則 QEMU build system 找不到 Python 標頭檔，build 在 `configure` 階段就會失敗，錯誤訊息是 `Python.h not found`——但這個錯誤訊息不太直觀，很容易被忽略。

---

## 底層機制：QEMU TCG 插樁的完整流程

```
afl-fuzz
  │ fork()
  ▼
qemu-linux-user（afl-qemu-trace）
  │ execve(target_no_src)
  ▼
QEMU init
  ├─ mmap 64KB bitmap（shared with afl-fuzz via shm）
  └─ 初始化 TB cache
  │
  ▼ 執行 target 的第一條指令
  │
  ┌─────────────── TB Loop ───────────────────────────┐
  │                                                    │
  │  PC = 目前 guest 程式計數器                         │
  │      │                                             │
  │      ▼ tb_find(PC) 命中快取?                       │
  │  ┌──YES─→ 直接跳到 TB 執行（快速路徑）──────────────┤
  │  │                                                  │
  │  └──NO──→ tb_gen_code(PC)                          │
  │              │                                      │
  │              ▼                                      │
  │          Decode frontend                            │
  │          （x86_64 → TCG IR）                        │
  │              │                                      │
  │              ▼                                      │
  │          afl_maybe_log(PC) ← AFL++ 插入點           │
  │          ┌───────────────────────────────────────┐ │
  │          │ cur = (PC>>4) ^ (PC>>9)               │ │
  │          │ bitmap[cur ^ prev]++                  │ │
  │          │ prev = cur >> 1                       │ │
  │          └───────────────────────────────────────┘ │
  │              │                                      │
  │              ▼                                      │
  │          TCG Backend（IR → host x86_64 機器碼）     │
  │              │                                      │
  │              ▼                                      │
  │          存入 TB cache，執行                         │
  │                                                    │
  └────────────────────────────────────────────────────┘
  │
  ▼ target 結束（exit 或 crash）
afl-fuzz 收到 status，決定這個 execution 是否找到新 coverage
```

### Persistent mode 在 QEMU 下的支援

QEMU mode 也支援 persistent mode（類似 `__AFL_LOOP()`），但設定方式不同，需要透過環境變數指定進入和退出的 PC 地址：

```bash
# 用 GDB 或 objdump 找到 parse_input 函式的地址
objdump -d target_no_src | grep -A3 '<parse_input>'
# 假設找到: 0x401234 (entry), 0x4015ab (exit point)

AFL_QEMU_PERSISTENT_ADDR=0x401234 \
AFL_QEMU_PERSISTENT_RET=0x4015ab \
AFL_QEMU_PERSISTENT_CNT=1000 \
afl-fuzz -Q -i seeds/ -o out/ -- ./target_no_src @@
```

`AFL_QEMU_PERSISTENT_CNT` 設定每個 process 在被 kill 重啟前執行幾次 iteration，預設值是 1000。

---

## Frida mode 原理

### GUM Engine 與 Stalker

Frida 是一個動態 instrumentation 框架，它的 GUM engine 包含一個叫做 Stalker 的元件。Stalker 的運作原理是：

1. **Code recompilation**：Stalker 讀取原始程式碼，將每個 basic block「重新編譯」到 Frida 自己管理的記憶體（一塊叫做 *slab* 的區域），在重新編譯的版本中插入 hook 回呼。
2. **Execution redirect**：攔截原始程式的執行，重定向到 Stalker 管理的重編版本。
3. **Transparency**：原始程式的指令語意完全保留，只是多了 instrumentation 程式碼在旁邊跑。

相對於 QEMU TCG，Stalker 的翻譯層級更細（instruction-level），而且不需要模擬整個 CPU——因為 host 和 guest 是同一個架構，只需要攔截執行流，不需要完整翻譯語意。這讓 Frida mode 在**同架構 fuzzing**（x86_64 binary 在 x86_64 host 上）時有時比 QEMU mode 稍快。

AFL++ 的 Frida mode 實作在 `frida_mode/` 目錄，核心是一個注入到 target process 的 Frida agent（`afl-frida-trace.so`），它：

- 透過 `LD_PRELOAD` 或 Frida 注入機制載入到 target process。
- 啟動 Stalker，在每個 basic block 開頭插入 AFL++ bitmap 更新。
- 接管 forkserver 協定，和 afl-fuzz 通訊。

### 範例二：Frida mode 啟動

```bash
# 建置 Frida mode（只需要做一次）
cd AFLplusplus/frida_mode/
make
# 產出：../afl-frida-trace.so

# 啟動 fuzz session
afl-fuzz -O -i seeds/ -o out/ -- ./target @@
# -O 旗標啟用 Frida mode（O = frida，Q = qemu，兩者不能同時使用）
```

指定只對特定函式範圍 instrumentation（減少 overhead）：

```bash
# 用 nm 或 objdump 找到 parse_input 的地址範圍
AFL_FRIDA_INST_RANGES=0x401234-0x401800 \
afl-fuzz -O -i seeds/ -o out/ -- ./target @@
```

---

## Unicorn mode：Embedded Firmware 的選擇

當 target 是沒有完整 OS 環境的 firmware（MCU binary、bootloader、UEFI driver），連 QEMU User mode 都不適用——因為 QEMU User mode 依賴 Linux syscall 轉發，firmware 根本不發 syscall。

Unicorn mode 使用 Unicorn Engine（QEMU 的 CPU emulation 核心被抽出來的函式庫），讓你在純粹的 CPU 模擬環境裡執行一段 firmware 程式碼片段，自己負責提供記憶體布局和 MMIO 模擬。

適用場景：

- IoT 裝置 firmware（MIPS/ARM Cortex-M binary）
- UEFI 模組的獨立函式
- 沒有完整 OS ABI 的裸機程式碼

代價：你需要手動設置模擬環境（記憶體映射、暫存器初始值、syscall 處理），門檻比 QEMU/Frida 高很多。

---

## 對比與取捨

| 指標 | Source Instrumentation | QEMU mode | Frida mode | Unicorn mode |
|------|----------------------|-----------|------------|--------------|
| 需要原始碼 | 必須 | 不需要 | 不需要 | 不需要 |
| throughput（相對值） | 1× | 0.2–0.5× | 0.3–0.6× | < 0.1× |
| 多架構支援 | 取決於 compiler | x86/ARM/MIPS/RISC-V 等 | x86/ARM | 幾乎全部 |
| Persistent mode | 完整支援 | 需設定 PC 地址 | 支援（`AFL_FRIDA_PERSISTENT_ADDR`） | 手動 |
| CmpLog 支援 | 完整支援 | 部分支援 | 不支援 | 不支援 |
| 設定複雜度 | 低（改 CC） | 中（build qemu_mode） | 中（build frida_mode） | 高（手寫 harness） |
| 適用場景 | 有 source 的任何 target | closed-source native binary | closed-source、Android、反除錯 binary | firmware、no-OS binary |
| 穩定性 | 最穩 | 良好 | 部分 glibc 版本有問題 | 依賴模擬完整度 |

**Frida mode 在哪些情況優於 QEMU mode**：

1. Binary 有反除錯機制（QEMU 的 ptrace-based 流程有時會觸發反偵測）。
2. Android native library（QEMU User mode 不支援 Android libc ABI）。
3. Binary 有大量 `syscall` 或 `vdso` 呼叫（Frida 不需要完整模擬，直接讓 OS 處理）。

---

## 踩雷集錦

**1. QEMU mode build 失敗：`Python.h not found`**

`build_qemu_support.sh` 在 `configure` 階段就掛掉，錯誤看起來很奇怪。原因通常是缺少 `python3-dev`：

```bash
sudo apt-get install -y python3-dev python3-setuptools
```

Ubuntu 22.04 預設只裝了 `python3`，沒有 dev headers。

**2. QEMU mode 不支援 target 自己 `fork()` 的情況**

如果 target binary 在執行路徑中會呼叫 `fork()`（例如 network daemon），QEMU mode 的 forkserver 機制會發生衝突。AFL++ 會輸出警告：

```
[-] PROGRAM ABORT : Fork server handshake failed
```

解法：用 `AFL_QEMU_CUSTOM_BIN=1` 並包一個 wrapper，或者改用 `afl-network-proxy`（針對 network target 的方案）。

**3. 預設不對 library 插樁**

QEMU mode 預設只對 main executable 的程式碼插樁，dlopen 載入的 library 不插。如果 target 的核心 parsing 邏輯在一個 shared library 裡，coverage 會嚴重低估：

```bash
AFL_INST_LIBS=1 afl-fuzz -Q -i seeds/ -o out/ -- ./target @@
# AFL_INST_LIBS=1 讓 QEMU mode 也對所有 loaded library 插樁
# 代價：overhead 更高，bitmap collision 更嚴重
```

**4. Frida mode 在舊版 glibc 下的奇怪行為**

Frida 的 `LD_PRELOAD` 注入機制在 glibc < 2.34 的某些版本下，`dlopen` 順序會造成 symbol resolution 問題，表現為 target 直接 segfault 而不是正常執行。

診斷方式：`AFL_DEBUG=1 afl-fuzz -O ...` 看 Frida agent 的初始化輸出。如果 crash 發生在 Frida agent 初始化之前，就是 glibc 版本問題。升級到 Ubuntu 22.04（glibc 2.35）通常可以解決。

**5. QEMU mode 的 TB cache 大小限制**

QEMU 的 TB cache 預設大小有限，對非常大的 binary（>5MB text section）翻譯完的 TB 會不斷被淘汰，造成反覆翻譯的 overhead 比預期更高。可以用：

```bash
AFL_QEMU_SIZE=16  # 設定 TB cache 大小為 16MB（預設 8MB）
```

---

## 進階：再往深一層

### 自訂 QEMU Instrumentation

如果你需要在 QEMU mode 下做不只是 edge coverage 的事（例如追蹤記憶體存取、記錄 syscall 序列），可以在 `qemu_mode/patches/afl-qemu-cpu-inl.h` 的 `afl_maybe_log()` 函式裡加入自己的邏輯：

```c
/* 在 afl-qemu-cpu-inl.h 裡 */
static inline void afl_maybe_log(abi_ulong cur_loc) {
    /* 原始 AFL++ 的 bitmap 寫入 */
    uintptr_t map_addr = (cur_loc >> 4) ^ (cur_loc >> 9);
    afl_area_ptr[(map_addr ^ afl_prev_loc) & MAP_SIZE_POW2_MINUS1]++;
    afl_prev_loc = map_addr >> 1;

    /* 自訂：記錄所有 basic block 到一個 log 文件 */
    if (custom_log_fd >= 0) {
        write(custom_log_fd, &cur_loc, sizeof(cur_loc));
    }
}
```

重新編譯 QEMU mode 後生效：`cd qemu_mode && ./build_qemu_support.sh`。

### Frida Stalker 的 transform 回呼

Frida mode 的 `AFL_FRIDA_INST_RANGES` 只是 Frida Stalker transformer 的一個應用。你也可以直接寫 Frida script，對特定 instruction 加 hook：

```javascript
// frida_mode/src/hook.js（概念示意）
Stalker.follow(Process.getCurrentThreadId(), {
    transform(iterator) {
        let instruction = iterator.next();
        do {
            // 在每個 call 指令前插入 log
            if (instruction.mnemonic === 'call') {
                iterator.putCallout(onCall);
            }
            iterator.keep();
        } while ((instruction = iterator.next()) !== null);
    }
});

function onCall(context) {
    // context.rip, context.rsp, ...
}
```

Frida Stalker 的完整 API 在 https://frida.re/docs/stalker/ 的 "Architecture" 節。

### 範例三：對 Android native library 用 Frida mode

```bash
# Android NDK 編譯出的 arm64-v8a native library，在 x86_64 Linux 上 fuzz
# 需要 aarch64 cross-compiled target，或者在 aarch64 機器/模擬器上跑

# 環境：aarch64 Ubuntu 22.04（或 QEMU system mode aarch64）
AFL_FRIDA_PERSISTENT_ADDR=$(nm libtarget.so | grep parse_input | awk '{print "0x"$1}') \
AFL_FRIDA_PERSISTENT_RET=$(nm libtarget.so | grep parse_input_end | awk '{print "0x"$1}') \
afl-fuzz -O -i seeds/ -o out/ -- ./harness libtarget.so @@
```

`AFL_FRIDA_PERSISTENT_ADDR` 和 `AFL_FRIDA_PERSISTENT_RET` 讓 Frida mode 在指定的函式入口/出口做 persistent loop，功能等同 compile-time 的 `__AFL_LOOP()`。

---

## 動手練習

1. 對一個你有原始碼的程式，分別用 compile-time 插樁（`afl-clang-fast`）和 QEMU mode（`-Q`）各跑 3 分鐘，用 `afl-whatsup out/` 比較 execs/sec。

2. 用 `objdump -d` 找出一個簡單程式的 `main()` 函式地址，用 `AFL_QEMU_PERSISTENT_ADDR` 開啟 QEMU persistent mode，確認 execs/sec 提升了。

3. 建置 Frida mode，對同一個 target 用 `-O` 跑，比較 QEMU mode 和 Frida mode 的 execs/sec。

4. 故意不安裝 `python3-dev`，嘗試 build QEMU mode，觀察錯誤訊息。安裝後再試一次，確認 build 成功。

---

## 本章重點整理

- **QEMU mode** 在 TCG IR 翻譯階段注入 bitmap 寫入，對 binary 完全透明，支援多架構，但 overhead 約為 compile-time 的 2–5 倍。
- **Frida mode** 用 Stalker 在 instruction level 做 JIT recompile，不需要 ptrace，在反除錯和 Android 場景有優勢。
- **效能階梯**：Source instrumentation（1×）> Frida mode（0.3–0.6×）≈ QEMU mode（0.2–0.5×）>> Unicorn mode（< 0.1×）——沒有銀彈，選擇取決於你有沒有 source、target 架構、以及是否有反除錯。

---

## 自我檢核

1. QEMU TCG 的 translation 流水線中，AFL++ 在哪一個步驟插入 bitmap 更新？為什麼選在那個點？

2. `AFL_INST_LIBS=1` 和預設行為的差異是什麼？啟用它會帶來什麼代價？

3. Frida Stalker 和 QEMU TCG 都是 JIT-based，但它們的翻譯層級不同——請描述這個差異，以及它如何影響適用場景。

4. 當 target binary 在執行路徑中呼叫 `fork()`，QEMU mode 會出什麼問題？原因是什麼？

5. `AFL_QEMU_PERSISTENT_ADDR` 需要你提供函式的地址，而 compile-time 的 `__AFL_INIT()` 不需要——這個差異的根本原因是什麼？

---

## 延伸閱讀

**QEMU TCG 文件**（https://www.qemu.org/docs/master/devel/tcg.html）
- 核心貢獻：QEMU 官方對 TCG IR 的完整說明，包含所有 IR op 的語意
- 讀哪裡：「TCG Intermediate Representation」節，特別是 `tcg_gen_*` 函式族
- 和本章關聯：理解 TCG IR 的 op 種類，才能看懂 AFL++ 的 `afl_maybe_log()` 是插在 IR 的哪個位置

**AFL++ `qemu_mode/README.md`**（AFL++ repo 內）
- 核心貢獻：QEMU mode 的官方使用說明，包含所有環境變數清單
- 讀哪裡：整份文件（不長，約 200 行），特別是 `KNOWN ISSUES` 節
- 和本章關聯：本章只涵蓋核心機制，README 有更完整的 edge case 和限制列表

**Frida Stalker 文件**（https://frida.re/docs/stalker/）
- 核心貢獻：Frida 官方對 Stalker 架構的說明，包含 transform callback API 和 slab allocator 設計
- 讀哪裡：「Architecture」和「JavaScript API」兩節
- 和本章關聯：本章只介紹 Stalker 的概念，文件裡的 transformer API 是自訂 Frida mode instrumentation 的入口

**「AFL++ WOOT 2020」論文**（https://www.usenix.org/conference/woot20/presentation/fioraldi）
- 核心貢獻：AFL++ 的正式學術論文，Section 4 專門討論 QEMU/Frida mode 的設計取捨
- 讀哪裡：4.1 "Binary-only Fuzzing" 節
- 和本章關聯：論文裡有量化的效能數字，可以對照本章的估算值

→ [Ch 9 — Forkserver](./09-forkserver.md)
