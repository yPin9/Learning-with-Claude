# Ch 24 — ASLR：Windows 特性 / leak / 部分覆寫

> **目標**：徹底理解 Windows ASLR 和 Linux ASLR 的架構差異——Windows 的 per-boot image base 固定性、四個隨機化維度（image/heap/stack/PEB-TEB）、`/DYNAMICBASE` 和 `/HIGHENTROPYVA` 的意義、以及為什麼「非 ASLR 模組」是 ROP gadget 最穩定的來源。學完能說出三條 ASLR 繞過路線（info leak、部分覆寫、x86 暴力）在 Windows 上的條件和限制，並能把它和 Ch 23 的 ROP chain 接在一起。

---

## 為什麼需要 ASLR？

Ch 23 的 ROP chain 需要精確的 gadget 位址（例如 `pop rcx; ret` 在 `kernel32.dll+0x1234AB`）。如果這個位址是固定的，攻擊者只需要靜態分析一次 DLL，得到位址，寫進 payload 就能用。

早期 Windows（XP 時代）的 DLL 基址確實是固定的：`kernel32.dll` 永遠在 `0x7C800000`（XP SP2 的常見值）。攻擊者寫一個通用 payload，在任何一台 XP 機器上都能用相同的 gadget 位址——這就是為什麼那個年代 Windows exploit 的「通用性」很強。

ASLR（Address Space Layout Randomization）的核心想法：**每次 process 啟動時，把各個記憶體區域的基址隨機化**，讓攻擊者無法在不知道當前位址的情況下構造有效的指標。

> 注意：「隨機化」的粒度和品質在 Windows 和 Linux 之間有關鍵差異。這個差異直接影響 ASLR 繞過策略。

---

## 先建立直覺：Windows ASLR 是 per-boot，不是 per-exec

**這是 Windows 和 Linux ASLR 最重要的差異，必須先建立清楚的直覺。**

在 Linux 裡：

```
$ ./test_aslr
libc base: 0x7f8a12340000

$ ./test_aslr   # 再跑一次
libc base: 0x7f3c56780000   ← 每次執行都不同（per-exec randomization）
```

在 Windows 裡（以有 DYNAMICBASE 的 DLL 為例）：

```powershell
# 第一次開機後：
Get-Process notepad | ... → kernel32 @ 0x00007FF8A3210000
Get-Process calc | ...   → kernel32 @ 0x00007FF8A3210000   ← 同一 DLL，同一 base！

# 重開機後：
Get-Process notepad | ... → kernel32 @ 0x00007FF8C9840000   ← 換了
Get-Process calc | ...   → kernel32 @ 0x00007FF8C9840000   ← 但兩個 process 還是一樣
```

**Windows 的 image（DLL/EXE）基址是 per-boot 固定的**：同一台機器、同一次開機，所有 process 裡的同一個 DLL（例如 `kernel32.dll`）的 image base **是相同的**。重開機後才換新的隨機值。

為什麼 Windows 這樣設計？因為 Windows 系統 DLL 在物理記憶體裡是 **shared**（由 kernel 的 Section Object 機制共用）——如果每個 process 都有不同的 image base，就不能 copy-on-write 共用同一份物理頁，記憶體開銷爆炸。統一基址讓共用變得可能。

**對攻擊者的含義**：如果你能在同一次開機裡從任何 process 洩漏 `kernel32.dll` 的基址（例如透過任何 DLL 的指標洩漏），你就知道**這台機器目前這次開機裡所有 process** 的 kernel32.dll 基址——直到下次重開機。

---

## Windows ASLR 的四個維度

### 1. Image（DLL / EXE）隨機化

由 PE loader（`ntdll!LdrpRebaseImage` 或 `ntdll!LdrpLoadDll`）在 process 初始化時處理。

**前提**：PE 的 `DllCharacteristics` 裡有 `IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE`（0x0040）旗標。沒有這個旗標的 DLL 會嘗試載入到它的 preferred base（PE Optional Header 的 `ImageBase` 欄位），除非那個位址被佔用才 fallback 隨機。

**隨機化時機**：第一次開機後，每個有 DYNAMICBASE 的映像的隨機 base 由 kernel 決定，並在 `HKLM\SYSTEM\...` 的某個內部資料結構裡（或直接由 KASLR 機制維護）固定下來，本次開機內所有 process 共用。

**隨機熵**：依 `HIGHENTROPYVA`（High-Entropy ASLR）旗標：
- x86 / 沒有 HIGHENTROPYVA：8 bits（256 個可能的 base）
- x64 + HIGHENTROPYVA（0x0020）：19 bits（約 52 萬個可能的 base）

```
DllCharacteristics 旗標（關鍵的幾個）：
0x0020 = IMAGE_DLLCHARACTERISTICS_HIGH_ENTROPY_VA   （高熵 ASLR，x64 only）
0x0040 = IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE      （ASLR 基址隨機化）
0x0100 = IMAGE_DLLCHARACTERISTICS_NX_COMPAT         （DEP，Ch 23）
0x0400 = IMAGE_DLLCHARACTERISTICS_NO_SEH            （沒有 SEH）
0x4000 = IMAGE_DLLCHARACTERISTICS_GUARD_CF          （CFG，Ch 32）
```

### 2. Heap 隨機化

Windows 的 heap（NT Heap 和 Segment Heap，Ch 14/16）的**起始位址**是隨機的。`HeapCreate` 回傳的 heap handle（第一個 heap 的基址）每次 process 啟動都不同。

**熵**：相對較低（就常見版本而言約 5 bits），因為 heap 的對齊限制（64KB 邊界）。但對精確 heap 位址的攻擊（heap spray 除外）仍然有效增加難度。

### 3. Stack 隨機化

每個 thread 的 stack 起始位址是隨機的。在同一個 process 裡，主 thread 的 stack base 每次啟動位置不同，子 thread 的 stack 也是。

**熵**：x86 約 17 bits；x64 約 17 bits（stack 對齊到 64KB 邊界，但位移隨機）。

**對 SEH overwrite 的影響**（回扣 Ch 21/22）：SEH record 在 stack 上，如果 stack 隨機化讓 stack 位址每次不同，偽造 SEHOP fake chain 需要的 `stack_sentinel_addr` 就必須動態 leak。

### 4. PEB 和 TEB 隨機化

你在 Ch 5 學過，PEB 在 `GS:[0x60]`（x64）或 `FS:[0x30]`（x86）可以取到指標；TEB 在 `GS:[0x30]`（x64）或 `FS:[0x18]`（x86）。

**隨機化**：PEB 和 TEB 的**位址**也是隨機的，不是固定的 `0x7FFDF000`（XP 的舊 PEB 位址）。每次 process 啟動位址不同。

**對 exploit 的影響**：你不能硬編 PEB 的位址作為可寫位址（用於存 VirtualProtect 的 `lpflOldProtect` 等）。但你可以用 `GS:[0x60]`（x64 TEB 指標）在執行期讀取 PEB 的動態位址——這就是 Ch 25 的 PEB walk 的意義。

### 整理對照表

| 維度 | Linux 對應 | Windows 差異 |
|---|---|---|
| **Image（.so/.dll）** | per-exec（每次執行都隨機） | per-boot（同一次開機裡所有 process 共用同一個 DLL 的 base） |
| **Heap** | per-exec | per-process（每次 process 啟動時隨機，但 per-boot image DLL 是固定的） |
| **Stack** | per-exec | per-thread（每個 thread 啟動時隨機） |
| **Executable（PIE）** | 只有 `-fPIE -pie` 才隨機 | 只有 `DYNAMICBASE` 才隨機 |
| **VDSO/vsyscall** | 位址固定（vdso 部分隨機） | 無直接對應 |
| **Entropy（x64 image）** | 28 bits（典型 Linux） | 19 bits（`HIGHENTROPYVA`）；無 HIGHENTROPYVA 只有 8 bits |

---

## `/DYNAMICBASE` 與 `/HIGHENTROPYVA`：對應 `-fPIE`

在 Linux 裡，`-fPIE -pie` 編譯的 binary 讓 kernel 在 `exec` 時隨機化 ELF 的 load address。沒有 `-fPIE` 的 binary，即使系統開了 ASLR，binary 本身還是在固定位址。

Windows 的對應：

- **`/DYNAMICBASE`**（link.exe 旗標）：設定 PE 的 `DYNAMIC_BASE` 位元（0x0040），告訴 loader「可以隨機化這個映像的 base address」。沒有這個旗標，loader 嘗試把 binary 載到 `ImageBase`（Optional Header 裡的預設值），只有在那個位址被佔用時才 fallback 到隨機。
- **`/HIGHENTROPYVA`**（link.exe 旗標，隱含 `DYNAMICBASE`）：設定 `HIGH_ENTROPY_VA` 位元（0x0020），對 64 位元 binary 啟用高熵 ASLR（19 bits entropy）。x64 binary 應該要有這個旗標才算「真正隨機」。

```bat
REM 未實測，理論預期（需 MSVC link.exe）

REM 標準安全編譯（自動包含 DYNAMICBASE + HIGHENTROPYVA + NXCOMPAT）
cl /GS /guard:cf target.c
link /DYNAMICBASE /HIGHENTROPYVA /NXCOMPAT /guard:cf target.obj

REM 關掉 ASLR（測試用）
link /DYNAMICBASE:NO target.obj
```

mingw 預設帶 `DYNAMICBASE + HIGH_ENTROPY_VA`（Ch 0 驗過的 `0x0160` 包含 `0x0020 + 0x0040`）。

### 用 objdump 或 dumpbin 驗證

```console
$ objdump -p target.exe | grep -iE "ENTROPY|DYNAMIC"
DllCharacteristics  00000160
                    HIGH_ENTROPY_VA
                    DYNAMIC_BASE
```

或 MSVC（未實測）：

```bat
dumpbin /headers target.exe
REM 找 "DLL characteristics" 欄位，看有沒有 DYNAMIC BASE 和 HIGH ENTROPY
```

---

## 非 ASLR 模組：穩定 Gadget 的黃金來源

這是本章和 Ch 23 最直接的連接點。

**定義**：沒有 `DYNAMIC_BASE` 旗標的 PE，每次被載入都在固定的 `ImageBase` 位址（前提：那個位址沒有被占用；如果被占用了，loader 會 fallback 到另一個位置，但通常還是固定的，因為 32 位元空間有限，實際上常落在同一個備用位址）。

**常見的非 ASLR 模組來源**：
- 2000 年代中期之前用舊工具鏈（MSVC 2003 以前或非 MSVC）編的第三方 DLL
- 一些嵌入在老舊應用程式裡、從未更新的 DLL
- 用 mingw 但沒有 `-Wl,--dynamicbase` 的老舊 mingw 編譯（現代 mingw 預設開）
- 一些遊戲/工業軟體的 DLL（長期不更新）

**怎麼找**（mona）：

```
!mona modules
```

mona 輸出的每行包含：

```
 Base      | Top       | Size    | Rebase | SafeSEH | ASLR  | NX    | OS DLL | Path
0x10000000 | 0x10042000| 0x42000 | False  | False   | False | False | False  | C:\target\old.dll
0x7C800000 | 0x7C8F5000| 0xF5000 | False  | True    | False | True  | True   | C:\Windows\...kernel32.dll (XP)
```

`ASLR: False` 的模組是穩定 gadget 來源。`Rebase: False` 表示從來不 rebase（嚴格的無 ASLR）。

**為什麼 Windows XP 時代的 kernel32.dll 在 XP 上是非 ASLR**：XP 的 kernel32.dll 根本沒有 `DYNAMIC_BASE` 旗標（2004 年 XP SP2 才引入 ASLR 機制），所以位址永遠是固定的。攻擊者只需要查一次位址就能對所有 XP 機器用。

---

## Windows ASLR 的粒度：per-boot 固定性帶來的攻擊面

正如前面建立的直覺，**Windows image ASLR 是 per-boot，不是 per-exec**。這帶來一個重要的攻擊面：

**同一次開機內，不同 process 的相同 DLL base 相同**。

假設你在攻擊一個 Web 服務（IIS 上的 .exe），這個 .exe 崩潰了（因為你的 overflow）。在 Linux 上，崩潰後 fork 出新的子進程，ASLR 重新隨機化——libc base 又不一樣了。在 Windows 上，如果攻擊後 .exe 重啟（同一次開機內），`kernel32.dll` 的 base 是**一樣的**，因為系統沒有重開機。

這讓某些**反覆試探（brute-force）**場景在 Windows 上比 Linux 上容易：

```
Linux 暴力：每次 crash 後，ASLR 重新隨機（28 bits entropy）→ 2^28 次嘗試（太慢）

Windows 暴力（x86）：
  - x86 image ASLR 只有 8 bits entropy → 256 個可能的 base
  - 同一次開機內 base 固定
  - 遠程服務 crash 後重啟（自動重啟），base 不變
  → 256 次嘗試內必然找到正確 base

Windows 暴力（x64）：
  - x64 + HIGHENTROPYVA：19 bits → ~52 萬次嘗試
  - 通常不實際，但沒有 HIGHENTROPYVA 的 x64 binary 熵更低
```

---

## 三條 ASLR 繞過路線

### 路線 1：Info Leak（最通用）

**核心思路**：程式碼或資料裡有一個指標洩漏——攻擊者讀到那個指標，從中算出某個模組的 base address 或 stack address，然後計算出 gadget 的精確位址。

**常見的 leak 原語**：

| Leak 來源 | 能洩漏什麼 | 說明 |
|---|---|---|
| 格式字串漏洞 | stack 上的 saved return address → .text 位址；或 heap 指標 | `%p %p %p ...` 印出 stack 上的值 |
| Out-of-bounds read | heap chunk 的 fwd/bk 指標（Segment Heap 的 free list）| 指向 ntdll 等模組的指標 |
| Use-after-free read | free chunk 的 metadata | 同上 |
| Type confusion | 物件欄位被讀為指標 | 取決於物件佈局 |
| Uninitialized memory | stack 上的殘留值（old return address 等）| 隨程式行為而定 |
| Stack overflow 讀回 | 某些協議的 echo 功能把 stack 上的值讀回來 | Heartbleed 類型 |

**在 exploit 裡的使用**：

```python
# 理論示意（完整 leak 原語因靶機而異）

# step 1：觸發 leak（假設是格式字串洩漏）
leak_payload = b"%p." * 20
send(leak_payload)
response = recv()

# step 2：解析 response，找到 kernel32 的返回位址
ptrs = [int(p, 16) for p in response.split(b".") if p]
# 假設 index 5 的值是 kernel32.dll 裡某個函式的位址
kernel32_ptr = ptrs[5]
kernel32_base = kernel32_ptr - 0x12345  # 已知的偏移（靜態分析 kernel32.dll 得到）

print(f"kernel32 base: {hex(kernel32_base)}")

# step 3：用 base 計算 gadget 位址
vp_addr     = kernel32_base + vp_rva          # VirtualProtect 的 RVA
pop_rcx_ret = kernel32_base + pop_rcx_rva     # pop rcx; ret 的 RVA
```

**RVA 的取得**：靜態分析目標系統的 DLL（用 IDA/Ghidra/dumpbin），或 `rp++`/`ropper` 搜尋。注意：不同 patch level 的 DLL，相同函式或 gadget 的 RVA 可能不同。

### 路線 2：部分覆寫（Partial Overwrite）

**核心思路**：ASLR 隨機的是 base 的高位 bytes，而低位的 12 bits（頁內偏移）是固定的（因為頁大小是 4KB，位址低 12 bits 永遠是 0 到 0xFFF 的頁內偏移，不受 ASLR 隨機化）。如果 overflow 只蓋 1-2 bytes（位址的低位），可以繞過隨機化的高位部分。

**實例（x86）**：

```
原本的 saved return address：  0x7C 82 34 AB   （kernel32.dll + 0x234AB）
ASLR 後：                      0xXX XX 34 AB   （高 2 bytes 隨機）

如果 overflow 只蓋最低 1 byte：
  payload 結尾是：              0xC4               （改成 ... 0x34 C4）
  結果位址：                    0xXX XX 34 C4      （高 3 bytes 保留，低 1 byte 改）
  可能落在：                    kernel32.dll 裡的另一個位址（如果 RVA 剛好）
```

**條件**：
1. 必須有一個「只影響低幾位 bytes」的覆寫原語（例如 off-by-one 漏洞，或者字串處理截斷 null byte 的特殊情況）
2. 目標 offset 必須讓新位址指向有用的 gadget 或 `ret` sled
3. 對 x64 效果更受限（位址空間更大，部分覆寫的 spray 空間也更大，但成功率下降）

**部分覆寫的典型場景**：

```
stack 上存著 dll_base + gadget_rva 的指標
overflow 改了最低 2 bytes（2 bytes = 16 bits，4096 個可能值）
→ 掃描 2 bytes 的可能值，找一個讓新位址指向 `ret` sled 或有用 gadget 的偏移
```

### 路線 3：x86 低熵暴力（Brute Force）

**條件**：
1. 目標是 x86（32 位元）binary
2. x86 image ASLR 只有 8 bits entropy
3. 服務崩潰後能自動重啟（daemon 模式、inetd 風格服務等）
4. 同一次開機：不需要等重開機，base 固定

**步驟**：

```
已知目標 DLL 只有 256 個可能的 base address：
  可能的基址：{ ImageBase, ImageBase±0x10000, ImageBase±0x20000, ... }（8 bits × 64KB granularity）

loop 256 次（最多）：
  guess_base = known_preferred_base + (i * 0x10000)
  gadget_addr = guess_base + gadget_rva
  送 payload（用 guess_base 算的 gadget 位址）
  if 服務有預期行為（反彈 shell、特定回應）:
    ASLR 繞過成功
  else:
    服務崩潰 → 等 restart → 繼續

平均嘗試次數：128 次（256/2）
```

> **x64 不適用**：x64 + HIGHENTROPYVA 是 19 bits，約 52 萬種可能，暴力通常不實際（除非服務回應很快且沒有 rate limiting）。

---

## 底層機制：Loader 如何做 ASLR Rebase

> 此節為機制說明，回扣 Ch 4（Loader）的知識。

PE loader（`ntdll!LdrpRebaseImage`）的工作流：

1. **檢查 PE 的 DYNAMIC_BASE 旗標**：如果沒有，嘗試在 `ImageBase` 載入（如果被佔用，由 VAD 管理記憶體分配選另一個位址）
2. **選定新 base address**：kernel 的記憶體管理系統（ASLR seed + 當次 boot 的隨機值）決定新 base。x64 + HIGHENTROPYVA 的情況下，在 64 位元 VAS 裡隨機選一個 64KB 對齊的位址。
3. **Apply relocation**：PE 的 `.reloc` 節包含所有需要 fix-up 的位址（IMAGE_REL_BASED_HIGHLOW 或 IMAGE_REL_BASED_DIR64 類型）。Loader 把每個記錄的 delta（新 base - ImageBase）加到對應的位置。
4. **更新 LDR 資料結構**：把新 base 記到 `LDR_DATA_TABLE_ENTRY.DllBase`（可從 PEB→Ldr→InMemoryOrderModuleList 走訪，Ch 5 的 PEB walk 的基礎）

```
Rebase 流程（概念）：

  PE load 時：
    delta = new_base - old_ImageBase

  對每個 .reloc 記錄（VA 位址）：
    *(ULONG_PTR*)(new_base + VA) += delta
                                    ↑
                    加 delta 讓指標從 old base 移到 new base

  結果：PE 裡所有的絕對位址指標都被更新，指向 new_base 的對應偏移
```

**為什麼 per-boot 固定**：kernel 的 ASLR seed 在開機時產生一次，這次開機裡所有 image 的隨機化都用同一組 seed 衍生出來的 offset。重開機後 seed 更新，所有 image base 都換一批。

---

## ASLR 和 Per-Boot 固定性的攻擊場景

### 場景 1：瀏覽器 exploiting

瀏覽器（Chrome/Firefox/Edge）在一個進程裡載入大量 DLL。如果攻擊者能透過渲染引擎的 bug 讀到任何一個 DLL 的指標（例如 `kernel32` 的位址通過 JScript 引擎的 object 洩漏），整個 DLL 的 base 就洩漏了，同一次開機裡其他 DLL 的 base 也可以透過 RVA 差推算（如果已知 DLL load order）。

### 場景 2：同一台機器的多 process 攻擊

攻擊者先打一個低權限 process（例如沙盒裡的服務），拿到 ntdll.dll 的 base（從 leak 或已知路徑）；然後用相同的 base 去打另一個高權限 process（例如 IIS Worker Process），因為同一台機器同一次開機，兩個 process 的 ntdll base 是一樣的。這是「per-boot 固定性」帶來的跨 process 攻擊面。

### 場景 3：info leak + partial overwrite 配合

完整的現代 Windows exploit 常是這樣：

```
step 1: info leak → ntdll base 或 stack base
step 2: 計算 VirtualProtect / gadget 的精確位址
step 3: overflow → ROP chain（用 step 2 算好的位址）
step 4: ROP chain 呼叫 VirtualProtect → shellcode 執行
```

每一步都是獨立的「原語」，然後串接。Ch 31（info leak 原語大全）是 step 1 的工具箱。

---

## 對照 Linux ASLR/PIE

| 面向 | Linux | Windows |
|---|---|---|
| **Shared lib（.so / .dll）** | per-exec 隨機 | per-boot 固定（同一次開機所有 process 共用） |
| **Executable（PIE / DYNAMICBASE）** | per-exec 隨機（需要 `-fPIE -pie`） | per-exec 隨機（需要 `DYNAMICBASE`）注意：exe 是 per-exec，DLL 是 per-boot |
| **Heap** | per-exec | per-exec（每個 process 獨立隨機） |
| **Stack** | per-exec | per-thread |
| **Entropy（x64 lib）** | 28 bits（典型 glibc ASLR） | 19 bits（HIGHENTROPYVA）或 8 bits（無 HIGHENTROPYVA） |
| **Entropy（x86 lib）** | 16 bits（典型）| 8 bits |
| **Granularity** | 頁大小（4KB）對齊 | 64KB（allocation granularity）對齊 |

**64KB 對齊**是 Windows 的重要細節：Windows 的 `VirtualAlloc` 的基址對齊是 64KB（`AllocationGranularity`），DLL 的 image base 也遵守這個對齊。所以 8 bits ASLR 實際上是在 `256 × 64KB = 16MB` 的範圍裡選，而不是在 256 bytes 的範圍裡選。

```
x86 Windows DLL ASLR（8 bits entropy）：
  可能的 base = { preferred_base + i × 0x10000 : i ∈ [0, 255] }
  跨越 256 × 64KB = 16MB 的範圍
  → brute force 最多 256 次（但每次嘗試要等服務重啟）
```

---

## 底層機制：ASLR 的 Entropy 為什麼 Windows x64 比 Linux 低？

這是一個值得深挖的設計取捨：

- **Linux x64**：`mmap` 的 ASLR 用 28 bits，從 `0x555500000000` 到 `0x7FFFFFFFFFFF` 的大範圍裡隨機選。PIE executable 也類似。
- **Windows x64 + HIGHENTROPYVA**：19 bits entropy，基址在 `0x000700000000` 到 `0x7FFFFF000000` 的範圍，但以 64KB 對齊，實際選 `(2^19) × 64KB = 32TB` 的虛擬空間裡的 64KB 對齊位置。

為什麼 Windows 的熵低一些？

1. **相容性**：Windows 應用程式生態裡有大量老舊 binary 依賴特定的記憶體佈局（COM 元件、舊版 ATL/MFC 等），太激進的隨機化會破壞它們。Microsoft 歷來在相容性和安全性之間有更保守的取捨。
2. **per-boot 共用的副作用**：per-boot 固定讓 entropy 的實際意義降低——開機後那個值就是固定了，brute force 不需要跨 boot。

---

## 踩雷集錦

1. **「Windows ASLR 和 Linux 一樣，每次跑程式位址都不同」**：不對。Windows 的 DLL（系統 DLL 如 kernel32.dll、ntdll.dll）在同一次開機內位址固定。exe 本身（如果有 DYNAMICBASE）是 per-exec 隨機的，但 DLL 是 per-boot。這個差異讓「只 leak 一次 DLL base 就能用一整次開機」成為可能。

2. **「關掉 DYNAMICBASE 就能讓 binary 跑在固定位址」**：通常如此，但如果 preferred base 被占用（另一個 DLL 先佔了那個位置），loader 會 fallback 到隨機位址。關掉 DYNAMICBASE 只是「我不要 ASLR 隨機化我，我想在我的 ImageBase 位址跑」的請求，不是保證。如果 preferred base 衝突，位址仍然可能不固定（只是熵更低，通常落在備用的幾個固定位址之一）。

3. **「部分覆寫需要 null byte 把高位 bytes 截斷」**：這是 x86 的常見情形（`strcpy` 複製到 null byte 就停），用來部分覆寫指標。但不是唯一方式。off-by-one 漏洞（只蓋了一個 byte）也是部分覆寫的形式。形式取決於漏洞類型，null byte 截斷只是最常見的觸發方式之一。

4. **「x64 的低 12 bits 可以用 partial overwrite 覆蓋到頁內偏移」**：x64 上的部分覆寫通常只有 1-2 bytes 可以可靠地控（因為漏洞通常只蓋少量 bytes）。1 byte = 256 個可能；2 bytes = 65536 個可能。能否找到有用的 gadget 在那 256/65536 個位置裡，取決於目標 DLL 的程式碼密度。

5. **「非 ASLR 模組只有老系統才有」**：老舊第三方 DLL 在現代 Windows 10/11 上仍然存在（它們的 DllCharacteristics 不會因為你升級 OS 就自動加 DYNAMICBASE）。很多企業軟體、工業系統、遊戲模組到今天仍然沒有 DYNAMICBASE。這是現實世界 exploit 的重要攻擊面。

---

## 進階：再往深一層

### ASLR 和 CFG 的組合

CFG（Control Flow Guard，Ch 32）本身不依賴 ASLR——即使沒有 ASLR，CFG 也能保護 indirect call 的目標。但 ASLR 讓「找一個 CFG 保護的函式的精確位址」更困難，因為每次開機 function table 的位址都變。ASLR + CFG 的組合讓「洩漏一個 CFG valid target 的位址 + 跳到那裡」這條路需要更多步驟。Ch 32/33 會深挖。

### KASLR（Kernel ASLR）和 userland 的關係

Windows 的 kernel base（`ntoskrnl.exe`、`hal.dll`）也受 KASLR 保護（Vista+ 的 KASLR）。userland exploit 如果需要 kernel 位址（例如 token stealing，Ch 46），必須先打 KASLR——這是 userland-to-kernel 提權鏈的一部分。KASLR bypass 是 kernel exploit 的主題（`windows_kernel_driver` 課程）。

### heap 噴射（Heap Spray）和 ASLR

Heap spray 是一種「用大量重複的 NOP sled + shellcode 把 heap 填滿，然後猜一個 heap 位址跳進去」的技法。在 ASLR 出現之前，heap spray 非常有效（heap 位址可預測）。ASLR 讓 heap 位址隨機化後，heap spray 需要更大的 spray 量才能讓猜中機率足夠高。在 Windows 10+ 的高熵 ASLR 下，純 heap spray 基本已失效，但「精確 heap grooming」（Ch 28）是另一回事——那是在知道 heap 位址的情況下控制 layout，不是盲猜。

### 面試題：Windows ASLR 的主要弱點是什麼？

**答**：

1. **per-boot image 固定性**：同一次開機裡，DLL base 對所有 process 相同，leak 一次能用整個 boot session。
2. **x86 低熵（8 bits）**：256 個可能的 base，服務反覆 crash/restart 情境下暴力可行（最多 256 次）。
3. **非 ASLR 模組**：老舊 DLL 沒有 DYNAMICBASE，位址永遠固定。
4. **per-boot 不對抗 info leak**：ASLR 的根本假設是「攻擊者不知道位址」；如果有 info leak 漏洞，ASLR 的隨機化被直接繞過。ASLR 本身不防 leak，只防「沒有 leak 的純猜測」。

---

## 動手練習

> **環境**：Python 3.12 + ctypes（本機可直接跑）。不需要 MSVC 或特殊工具。

驗 Windows ASLR 的 per-boot 固定性：

```python
# aslr_probe.py — 驗 Windows DLL 的 per-boot ASLR 特性
# 真實可跑（ctypes + Windows API）

import ctypes
import ctypes.wintypes

def get_module_base(module_name: str) -> int:
    """取得載入 DLL 的基址（透過 GetModuleHandle）"""
    handle = ctypes.windll.kernel32.GetModuleHandleW(module_name)
    return handle  # GetModuleHandle 回傳的就是 DLL 的 load base（HMODULE = PVOID）

# 取得幾個系統 DLL 的 base address
modules = ["kernel32.dll", "ntdll.dll", "user32.dll"]
for mod in modules:
    base = get_module_base(mod)
    if base:
        print(f"{mod:20s}: 0x{base:016X}")
    else:
        print(f"{mod:20s}: not loaded")

print()
print("Run this script twice in the same boot — bases should be identical.")
print("Reboot and run again — bases should change.")
```

跑這個腳本兩次（同一次開機），觀察輸出。重開機後再跑一次，確認 base 改變。這直接驗證了 Windows ASLR 的 per-boot 固定性。

（選做）再開一個 PowerShell 視窗執行同樣的腳本，確認不同 process 裡相同 DLL 的 base 是一樣的。

---

## 本章重點整理

- Windows image ASLR 是 **per-boot 固定**：同一次開機所有 process 共用同一個 DLL 的 base（不同於 Linux 的 per-exec 隨機）。Leak 一次 DLL base 可以在本次開機內通用。
- ASLR 的四個維度：image（per-boot）、heap（per-exec）、stack（per-thread）、PEB/TEB（per-exec）；x64 + HIGHENTROPYVA 的 entropy 是 19 bits，x86 只有 8 bits。
- 非 ASLR 模組（沒有 `DYNAMIC_BASE` 的老舊 DLL）是 ROP chain 最穩定的 gadget 來源，位址完全固定。`!mona modules` 的 `ASLR: False` 欄位是快速篩選手段。
- 三條繞過路線：info leak（最通用，取得 base 後計算 gadget 位址）、部分覆寫（只改低位 bytes，需要特定漏洞形式）、x86 暴力（8 bits entropy，服務反覆 crash/restart 時 256 次內必中）。

---

## 自我檢核

- [ ] 不看筆記，能說出 Windows DLL ASLR「per-boot 固定」的含義，以及和 Linux「per-exec 隨機」的實際差異（在攻擊者的 exploit 工作流裡有什麼不同影響）
- [ ] 能解釋 `/DYNAMICBASE` 和 `/HIGHENTROPYVA` 各自做什麼、少了哪個 ASLR 的 entropy 會下降到多少（x64）
- [ ] 面試被問「為什麼 Windows x86 ASLR 可以暴力」：能說出 8 bits entropy、256 個可能、服務 crash restart 情境、per-boot 固定性的完整邏輯
- [ ] 能說出 `!mona modules` 輸出裡要看哪個欄位來判斷一個 DLL 是否可以作為穩定 gadget 來源
- [ ] 能畫出「info leak → ASLR bypass → ROP chain → VirtualProtect → shellcode」這條完整路線的每個步驟，並說出每個步驟依賴什麼前提

---

## 延伸閱讀

### 論文 / 研究

- **"On the Effectiveness of Address Space Randomization"** — Shacham et al.（ACM CCS 2004）
  - **讀哪裡**：Section 3「How Long Does It Take to Guess?」（brute force 分析）和 Section 4（partial overwrite）
  - **學什麼**：ASLR 在 32 位元系統上被暴力的理論分析；本章 x86 brute force 路線的學術依據
  - **和本章關聯**：本章暴力繞過的 entropy 分析直接來自這篇的框架
  - **前提**：基本概率和密碼學概念

- **"Advanced Windows Exploitation: ASLR Bypass Techniques"** — Sean Dillon（DEF CON 24, 2016）
  - **讀哪裡**：搜尋 DEF CON 24 slides，找 ASLR bypass 的 partial overwrite 和 info leak 那部分
  - **學什麼**：現代 Windows ASLR 繞過的完整技法譜系，比本章更多細節
  - **和本章關聯**：本章是原理，這份是技法詳解

### 官方文件

- **[Enable ASLR — Microsoft Learn (WDEG)](https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/exploit-protection-reference#randomize-memory-images-bottom-up-aslr)**
  - **讀哪裡**：「Randomize memory images (Bottom-up ASLR)」和「Force randomization for images (Mandatory ASLR)」條目
  - **學什麼**：WDEG 裡 ASLR 的 per-process 細粒度設定方式（對 opt-out DLL 強制開 ASLR 的選項）
  - **和本章關聯**：本章的「如何在現代 Windows 控制 ASLR 行為」的官方來源

- **[/DYNAMICBASE — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/reference/dynamicbase-use-address-space-layout-randomization)**
  - **讀哪裡**：全文（很短）；特別注意「Remarks」裡關於 rebase 和 preferred base 的說明
  - **學什麼**：DYNAMICBASE 的行為細節和限制（preferred base 被占用時的 fallback 行為）
  - **和本章關聯**：本章「關掉 DYNAMICBASE 不保證固定位址」踩雷的官方依據

### 部落格

- **j00ru // windows kernel logs — "Windows Address Space Layout Randomization in Depth"**（[j00ru.vexillium.org](https://j00ru.vexillium.org/)）
  - **讀哪裡**：搜尋 j00ru blog 裡的 ASLR 文章（他有數篇 Windows 記憶體隨機化的深挖）
  - **學什麼**：kernel 層面的 ASLR 實作，比本章的 loader 側說明更深；以及 KASLR 和 userland ASLR 的關係
  - **和本章關聯**：本章的 loader rebase 機制說明的深化；想做 kernel exploit 前的必讀
  - **前提**：本章 + Ch 4（loader）+ 基本 Windows kernel 概念

- **Corelan Team — "Exploit writing tutorial part 6: Bypassing Stack Cookies, SafeSEH, SEHOP, HW DEP and ASLR"**（[corelan.be](https://www.corelan.be/index.php/2009/09/21/exploit-writing-tutorial-part-6-bypassing-stack-cookies-safeseh-hw-dep-and-aslr/)）
  - **讀哪裡**：「Bypassing ASLR」一節，特別是 non-ASLR DLL 和 partial overwrite 的實踐部分
  - **學什麼**：從 x86 exploit 開發者視角看 ASLR 繞過；`!mona modules` 的 ASLR 欄位解讀
  - **和本章關聯**：本章的「非 ASLR 模組」和「三條繞過路線」的實踐面
  - **前提**：Ch 20（/GS）+ Ch 21（SEH overwrite）+ 本章

ASLR 把「位址在哪裡」變成 exploit 的第一個難題，而 info leak 是最通用的解法。下一章進 Windows shellcode 的核心技法：從 PEB 動態走 LDR 找 kernel32、動態解析 API，讓 shellcode 不依賴任何硬編位址——這正是在 ASLR 環境裡讓 shellcode 能活下去的設計。

→ [Ch 25 — Windows shellcode：PEB 找 kernel32 / resolve API / PIC](./25-windows-shellcode.md)
