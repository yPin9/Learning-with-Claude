# Ch 21 — ELF / .so 結構

> **目標**：把一個 `.so` 這個檔案本身拆開。Android 的 native 庫是 **ELF（Executable and Linkable Format）**，跟 Linux 的執行檔同一種格式。你要能回答：ELF header 裡哪些欄位告訴你「這是 32 還是 64 位、ARM64 還是 x86」？program header 與 section header 差在哪、動態載入器看哪一個？`.text`/`.data`/`.got`/`.plt`/`.init_array` 各裝什麼、逆向時盯哪個？動態符號（`.dynsym`）與重定位（relocation）是怎麼讓 `System.loadLibrary` 把 `.so` 接進進程的？本章用 **Python 實跑**解析一個 ELF header，把骨架攤在你眼前。

> **環境**：本章的 ELF 解析用 **Python 3.12** 在本機**實際跑出**（純檔案格式解析，不需 Android），輸出標「實際輸出」。我用 Python 建一個結構正確的 **AArch64** ELF（`ET_DYN`，也就是 `.so` 的型別）再解析它——這跟 `readelf` 對真實 `.so` 做的事一模一樣，只是自己動手看每個欄位。

## 為什麼需要這個？

你的工具（IDA/Ghidra/`readelf`/Frida）全都在讀 ELF，而它們對 ELF 的解讀決定了你看到什麼。搞懂 ELF，你才能：

- **判斷架構**：拿到一個 `.so`，第一件事是確認它是 ARM64 還是 x86（Ch 20 反覆強調的環境陷阱），這靠讀 ELF header 一個欄位。
- **找對切入點**：Ch 19 說 `JNI_OnLoad` 與 `.init_array` 是加固/反調試最先動手的地方——`.init_array` 就是 ELF 的一個 section，你要知道它在哪、怎麼列出來。
- **懂 hook 的底層**：Ch 25 的 PLT/GOT hook 之所以可行，是因為 `.so` 呼叫外部函式走 GOT 這張「可改的跳轉表」。不懂 GOT/PLT，那些 hook 對你就是黑魔法。
- **脫殼與 dump**：從記憶體 dump 一個 `.so` 或修復被抽取的 DEX，你得會手動修 ELF header 與 section——不懂結構就修不回能載入的檔案。

ELF 是 native 逆向的地圖。這章把地圖畫出來。

## 先建立直覺：ELF 的兩種視角

ELF 最容易搞混的一點：**同一個檔案有兩張表描述它**，一張給連結器/工具看，一張給載入器看。先把這張圖記住：

```
        一個 .so 檔案（ELF）
 ┌────────────────────────────────────┐
 │ ELF Header (64 byte)               │ ← 檔案的身分證：架構/型別/兩張表在哪
 ├────────────────────────────────────┤
 │ Program Header Table               │ ← 「載入視角」：載入器(dlopen)看這個
 │   PT_LOAD (R-X)  ← 程式碼要映射到哪 │    描述「執行時記憶體怎麼佈局」
 │   PT_LOAD (RW-)  ← 資料             │
 │   PT_DYNAMIC     ← 動態連結資訊在哪 │
 ├────────────────────────────────────┤
 │  .text  .rodata  .data  .bss ...   │ ← 實際內容（sections）
 │  .dynsym .dynstr .plt .got         │
 │  .init_array  .rela.dyn  .dynamic  │
 ├────────────────────────────────────┤
 │ Section Header Table               │ ← 「連結視角」：工具(readelf/IDA)看這個
 │   描述每個 section 叫什麼、在哪、多大 │    描述「檔案裡有哪些區塊」
 └────────────────────────────────────┘
```

兩個關鍵事實：

1. **載入器（`dlopen`/`ld-android`）只認 program header**，不看 section header。它照 `PT_LOAD` 把檔案內容映射進記憶體、照 `PT_DYNAMIC` 做動態連結。**section header 可以整個被刪掉，`.so` 照樣能載入執行**——這正是很多加固殼幹的事（刪 section header 讓 IDA 難分析）。
2. **工具（IDA/Ghidra/readelf）主要靠 section header** 把檔案切成 `.text`/`.data` 等有名字的區塊。所以殼一刪 section header，IDA 就只剩「一大塊沒名字的資料」，逆向難度陡升——但你仍能靠 program header 手動重建。

「載入器看 program header、工具看 section header」——這句話解釋了一大類加固與修復技巧。

## 用 Python 實跑：解析 ELF header

不空談，動手。先用 Python 建一個結構正確的 AArch64 `.so`（`ET_DYN`），再逐欄位解析它。建構那段等同於「造一個最小 `.so` 的骨架」：

```python
import struct
def u16(x): return struct.pack('<H', x)
def u32(x): return struct.pack('<I', x)
def u64(x): return struct.pack('<Q', x)

# ELF64 header (64 byte)
e_ident = b'\x7fELF' + bytes([2, 1, 1, 0]) + b'\x00' * 8   # 64bit(2), LE(1), SYSV
hdr  = e_ident
hdr += u16(3) + u16(183) + u32(1)      # e_type=ET_DYN, e_machine=EM_AARCH64, version
hdr += u64(0) + u64(64) + u64(0)       # e_entry, e_phoff=64, e_shoff=0
hdr += u32(0) + u16(64) + u16(56) + u16(2)   # flags, ehsize, phentsize, phnum=2
hdr += u16(64) + u16(0) + u16(0)       # shentsize, shnum, shstrndx

# 兩個 program header（56 byte 一個）：一個 PT_LOAD(R+X)、一個 PT_DYNAMIC
def phdr(t, fl, off, va, fs, ms, al):
    return u32(t) + u32(fl) + u64(off) + u64(va) + u64(va) + u64(fs) + u64(ms) + u64(al)
ph1 = phdr(1, 5, 0,     0,     0x1000, 0x1000, 0x1000)   # PT_LOAD  flags=R|X(5)
ph2 = phdr(2, 6, 0x800, 0x800, 0xa0,   0xa0,   8)        # PT_DYNAMIC flags=R|W(6)
open('libdemo.so', 'wb').write(hdr + ph1 + ph2)
```

其中 `e_ident` 的 magic **`\x7fELF`**（0x7f 加 ASCII "ELF"）是所有 ELF 檔的開頭，就像 APK 是 `PK\x03\x04`、DEX 是 `dex\n035`。接著的解析器（跟 `readelf` 做的事一樣）：

```python
data = open('libdemo.so', 'rb').read()
assert data[:4] == b'\x7fELF', "not ELF"
ei_class = {1: 'ELFCLASS32', 2: 'ELFCLASS64'}[data[4]]      # 32 or 64 bit
ei_data  = {1: 'little-endian', 2: 'big-endian'}[data[5]]
(e_type, e_machine, e_version, e_entry, e_phoff, e_shoff,
 e_flags, e_ehsize, e_phentsize, e_phnum,
 e_shentsize, e_shnum, e_shstrndx) = struct.unpack('<HHIQQQIHHHHHH', data[16:64])
etypes = {1: 'ET_REL', 2: 'ET_EXEC', 3: 'ET_DYN', 4: 'ET_CORE'}
machs  = {3: 'x86', 40: 'ARM', 62: 'x86_64', 183: 'AArch64'}
print("magic   :", data[:4])
print("class   :", ei_class)
print("data    :", ei_data)
print("type    :", etypes.get(e_type), f"({e_type})")
print("machine :", machs.get(e_machine), f"({e_machine})")
print("phoff   :", hex(e_phoff), " phnum:", e_phnum)
```

**實際輸出**（Python 3.12 在本機跑）：

```
== ELF Header ==
magic      : b'\x7fELF'
class      : ELFCLASS64
data       : little-endian
type       : ET_DYN (3)
machine    : AArch64 (183)
phoff      : 0x40  phnum: 2  phentsize: 56
```

逐欄位讀，這就是你逆向前的偵察：

- **magic `\x7fELF`**：確認是 ELF。
- **class = ELFCLASS64**：64 位（`.so` 幾乎都是）。
- **type = ET_DYN**：**這是 `.so`/PIE 執行檔的型別**（動態、位址無關）。`ET_EXEC` 是舊式固定位址執行檔、`ET_REL` 是 `.o` 目標檔。
- **machine = AArch64（183）**：**這一欄就是 Ch 20 反覆強調要確認的「架構」**。183=AArch64、62=x86_64、40=ARM(32)、3=x86。拿到 `.so` 先看這欄，決定你逆的是 ARM64 還是 x86。

`readelf -h libfoo.so` 印的就是這些，現在你知道每個欄位是從檔案哪個 offset、用什麼型別讀出來的。

## 用 Python 實跑：解析 program header

接著解析 program header——載入器的視角：

```python
PT = {1: 'PT_LOAD', 2: 'PT_DYNAMIC', 3: 'PT_INTERP', 4: 'PT_NOTE', 6: 'PT_PHDR'}
for i in range(e_phnum):
    off = e_phoff + i * e_phentsize
    (p_type, p_flags, p_offset, p_vaddr, p_paddr,
     p_filesz, p_memsz, p_align) = struct.unpack('<IIQQQQQQ', data[off:off+56])
    fl = ''.join(c if p_flags & b else '-' for c, b in [('R', 4), ('W', 2), ('X', 1)])
    print(f"{PT.get(p_type, hex(p_type)):<12}{fl:<7}{hex(p_offset):<10}"
          f"{hex(p_vaddr):<12}{hex(p_filesz):<9}{hex(p_memsz)}")
```

**實際輸出**：

```
== Program Headers ==
Type        Flags  Offset    VirtAddr    FileSz   MemSz
PT_LOAD     R-X    0x0       0x0         0x1000   0x1000
PT_DYNAMIC  RW-    0x800     0x800       0xa0     0xa0
```

讀法：

- **`PT_LOAD` R-X**：一段要被載入、可讀可執行的區域——**這是程式碼段**（`.text` 住這）。`R-X`（讀+執行、不可寫）是程式碼段的招牌權限。
- **通常還有第二個 `PT_LOAD` RW-**：可讀可寫、不可執行——資料段（`.data`/`.got`/`.bss`）。W^X 原則：能寫的不能執行、能執行的不能寫。
- **`PT_DYNAMIC`**：指向 `.dynamic` section，裡面是動態連結所需的一切（依賴哪些 `.so`、符號表在哪、重定位表在哪）。載入器讀它來完成連結。

`p_flags` 的 R/W/X 是位元旗標（R=4、W=2、X=1），上面用位元且測出來拼成 `R-X` 這種字串。

## Sections：逆向時各盯哪個

Program header 給載入器看，逆向的你更常打交道的是 **sections**。把最該認得的列出來：

| Section | 裝什麼 | 逆向時盯它做什麼 |
|---|---|---|
| **`.text`** | 機器碼（你的函式） | 反組譯的主戰場，所有 ARM64 指令住這 |
| **`.rodata`** | 唯讀常數（字串、常數表） | **找硬編碼字串/金鑰/URL 的第一站** |
| **`.data`** | 已初始化的可寫全域變數 | 全域狀態、初始化的指標表 |
| **`.bss`** | 未初始化全域（檔案裡不佔空間） | 執行期才配置的全域緩衝 |
| **`.dynsym`** | 動態符號表 | 找 `Java_...`（Ch 19 靜態命名）、匯出/匯入函式名 |
| **`.dynstr`** | `.dynsym` 用的字串池 | 符號名字本體存這 |
| **`.plt`** | 呼叫外部函式的跳板 | 追「這個 `bl` 呼叫的是哪個 libc 函式」 |
| **`.got`** | 全域偏移表（外部符號的真實位址） | **Ch 25 PLT/GOT hook 改的就是這** |
| **`.init_array`** | `.so` 載入時自動跑的建構子函式指標 | **Ch 19 說的反調試藏身處**，比 `JNI_OnLoad` 更早跑 |
| **`.rela.dyn`/`.rela.plt`** | 重定位表 | 描述哪些位址在載入時要被填/修正 |
| **`.dynamic`** | 動態連結的總目錄 | 指向上面各表，載入器的入口 |

三個逆向最該記住的 section：

- **`.rodata` 找字串**：你要找金鑰、URL、log 訊息、演算法常數（例如 MD5 的初始值），第一站就是 `.rodata`。`strings -a libfoo.so` 或 IDA 的 Strings 視窗掃它。
- **`.init_array` 反調試**：Ch 19 講過，這裡的函式在 `dlopen` 期間、`JNI_OnLoad` 之前就跑，反調試/反 Frida 常趕在你 attach 前先在這動手。逆向卡在「還沒進主邏輯就被檢測」時，回頭列 `.init_array`。
- **`.got` 是可改的跳轉表**：`.so` 呼叫外部函式（如 `libc` 的 `open`）不是直接跳，而是跳到 `.plt`，`.plt` 去查 `.got` 裡填好的真實位址。因為 `.got` 是執行期可寫的一張表，**改一個 GOT 條目就能把某個外部函式呼叫重導到你的函式**——這是 Ch 25 GOT hook 的原理。

## 底層機制：動態符號與重定位，`.so` 怎麼被接進進程

`System.loadLibrary("foo")` → `dlopen("libfoo.so")` 之後，載入器做兩件關鍵的事，這解釋了 `.so` 為什麼能「位址無關」還能正確呼叫到別的庫的函式：

### 動態符號（`.dynsym`）

`.so` 要匯出自己的函式（讓別人呼叫，例如 `JNI_OnLoad`、`Java_...`）也要匯入別人的函式（例如它用了 `libc` 的 `malloc`）。這些「對外可見的名字」記在 `.dynsym`（符號表）+ `.dynstr`（名字字串池）。

```
.dynsym 一個條目（Elf64_Sym, 24 byte）
 ┌──────────┬──────┬──────┬────────┬──────────┬────────┐
 │ st_name  │ info │ other│ shndx  │ st_value │ st_size│
 │ (名字在  │(型別/│      │(在哪個 │ (符號的  │        │
 │ dynstr的 │binding)│    │ section)│ 位址)   │        │
 │ 偏移)    │       │      │        │          │        │
 └──────────┴──────┴──────┴────────┴──────────┴────────┘
```

逆向意義：`readelf --dyn-syms libfoo.so` 列出所有動態符號。你在 Ch 19 用 `grep Java_` 找靜態命名的 JNI 函式，找的就是 `.dynsym` 裡 `st_name` 指到 `Java_...` 的那些條目，`st_value` 就是函式在 `.so` 裡的 offset。

### 重定位（relocation）

`.so` 是 `ET_DYN`（位址無關），載入時被映射到哪個基址事先不知道。但程式碼裡有些地方（例如全域指標、GOT 條目）需要填入「絕對位址」或「別的 `.so` 的函式位址」——這些位置在編譯時填不了，只能**載入時修正**。重定位表（`.rela.dyn`/`.rela.plt`）就是一張清單：「檔案的這個 offset，載入時要填上這個計算出來的值」。

```
一次 dlopen 的動態連結（簡化）
  1. 照 PT_LOAD 把 .so 映射到某基址 base
  2. 讀 .dynamic → 找到 .dynsym/.dynstr/.rela.* 的位置
  3. 對 .rela.dyn 每一條：算出真實位址，填到指定 offset
       （例如「GOT[3] 要填 malloc 的位址」→ 去符號表解析 malloc → 填入）
  4. 執行 .init_array 裡的建構子   ← 反調試常在這
  5. （首次呼叫 native 方法時）解析 JNI 綁定
```

逆向意義：當你在 IDA 看到一個「呼叫某個看不出名字的位址」，那多半是還沒被填的 GOT 條目——IDA 靠讀重定位表把它註解回 `malloc`/`open` 等真名。重定位表壞了或被殼加密，IDA 就標不出外部呼叫的名字，你得手動對照。

## 範例：判斷一個 `.so` 的架構與防護線索（含失敗情況）

拿到陌生 `.so`，用 ELF 知識做一輪偵察：

```bash
readelf -h libfoo.so | grep -E 'Class|Machine|Type'   # 架構與型別
readelf -d libfoo.so | grep NEEDED                    # 依賴哪些 .so
readelf --dyn-syms libfoo.so | grep -i -E 'Java_|OnLoad'  # JNI 入口
strings -a libfoo.so | grep -i -E 'http|key|secret'   # .rodata 裡的線索
```

- **成功情況**：`Machine: AArch64`、列出 `Java_com_...` 幾個匯出、`strings` 掃到一個 API URL——你已經定位好架構、入口與可疑字串，可以進 IDA。
- **失敗情況一**：`readelf -h` 報 `Not an ELF file`。多半是這個 `.so` 被殼**加密了**，磁碟上是密文，要執行期從記憶體 dump 才是明文（Ch 29 脫殼）。
- **失敗情況二**：ELF 認得，但 `readelf -S`（列 sections）幾乎是空的、`grep Java_` 什麼都沒有。這是殼**刪了 section header**（前面說過載入器不需要它）。你仍能用 `readelf -l`（program header）與 `readelf --dyn-syms`（動態符號不在 section header 裡）繼續，或在 IDA 裡靠 program header 手動重建 section。

這個「先 `readelf` 一輪」的偵察，是每次逆 `.so` 的固定開場。

## 對比與取捨：program header vs section header

| 面向 | Program Header | Section Header |
|---|---|---|
| 給誰看 | 載入器（`dlopen`） | 連結器/逆向工具（IDA/readelf） |
| 描述什麼 | 執行時記憶體怎麼映射（segments） | 檔案裡有哪些命名區塊（sections） |
| 能不能刪 | **不能**（刪了載不進來） | **能**（刪了照樣執行，但工具難分析） |
| 加固殼怎麼利用 | 通常保留（要能跑） | **常刪/偽造**（讓 IDA 看不懂） |
| 你 dump/修復時 | 靠它重建記憶體佈局 | 靠它（或重建它）讓工具能分析 |

核心取捨：**要能執行只需 program header；要好分析需要 section header。** 殼就是攻擊這個落差——留下能跑的最小資訊、砍掉幫你分析的資訊。你脫殼修復時，反過來用 program header 的資訊去重建 section header。

## 踩雷集錦

1. **沒看 `e_machine` 就開逆**：以為在讀 ARM64，其實那是 x86_64 AVD 的 `.so`（x86）。`readelf -h` 第一件事看 `Machine:`。這是 Ch 20 環境陷阱在 ELF 層的具體檢查點。
2. **以為 section header 沒了 `.so` 就壞了**：載入器不看 section header，`.so` 照樣執行。`readelf -S` 空空的不代表檔案壞，是殼刪了它。改用 `readelf -l`/`--dyn-syms` 繼續。
3. **在 `.text` 裡找字串**：字串常數在 `.rodata`（唯讀資料），不在 `.text`（程式碼）。`strings` 掃全檔沒差，但用 IDA 定位時別在 `.text` 段裡瞎找字串。
4. **把 `ET_DYN` 當成一定是共享庫**：現代 PIE 執行檔也是 `ET_DYN`（位址無關執行檔）。`.so` 是 `ET_DYN`，但 `ET_DYN` 不一定是 `.so`——看有沒有 `PT_INTERP`（執行檔有直譯器路徑、純庫沒有）與是否匯出 `JNI_OnLoad` 來區分。
5. **忽略 `.init_array` 直接看 `JNI_OnLoad`**：`.init_array` 比 `JNI_OnLoad` 更早跑，反調試/解密常藏這。只從 `JNI_OnLoad` 開始讀，會漏掉在你之前就執行的檢測。`readelf -d` 找 `INIT_ARRAY`/`INIT_ARRAYSZ` 定位它。

## 進階：再往深一層

- **`.dynamic` 的 tag 清單**：`readelf -d` 列出的 `NEEDED`（依賴的 `.so`）、`SONAME`（自己的名字）、`INIT`/`INIT_ARRAY`（初始化函式）、`SYMTAB`/`STRTAB`（符號表位置）、`FLAGS`（如 `BIND_NOW` 立即重定位）都是動態連結的元資料。脫殼修復 `.so` 時，這張表要對，載入器才認。
- **`DT_INIT` vs `.init_array`**：更老的機制用單一 `DT_INIT` 函式，現代用 `.init_array`（一個函式指標陣列，可多個）。兩者都在 `main`/`JNI_OnLoad` 前跑，逆向時兩個都要列。
- **section header 重建（脫殼必修）**：從記憶體 dump 的 `.so` 常缺 section header 或 offset 對不上（記憶體佈局 ≠ 檔案佈局，因為 `p_vaddr` 與 `p_offset` 對齊不同）。修復要把「記憶體位址」換算回「檔案偏移」，這在 Ch 29/36 dump native 時是核心技能。工具如 SoFixer 自動化這步，但懂原理才修得動非典型情況。
- **符號雜湊表（`.hash`/`.gnu.hash`）**：動態連結靠雜湊表加速符號查找。逆向通常不管它，但殼有時破壞它擋 `dlsym`——某些反 hook 技巧會查這裡。
- **ELF 與 DEX/oat 的關係**：安裝後 ART 把 DEX 編成的 `oat` 檔其實也包在一個 ELF 裡（`.rodata` 塞 dex、`.text` 塞編譯出的機器碼）。Part 6 脫 oat 時你會再用到本章的 ELF 知識。

## 動手練習

1. 把本章的 Python 建構+解析腳本自己跑一遍，確認你複現出 `machine: AArch64 (183)`、`PT_LOAD R-X`。然後把 `e_machine` 改成 62 重跑，看它變成 `x86_64`——體會「架構就是這一個欄位」。
2. 用 arm64 AVD 撈一個真實 App 的 `.so`，`readelf -h` 看 `Machine`、`readelf -l` 看幾個 `PT_LOAD` 的權限、`readelf -S` 看有沒有 section header（有沒有被殼刪）。
3. `readelf --dyn-syms <真實.so> | grep Java_` 找靜態命名的 JNI 函式，記下一個的 `Value`（就是它在 `.so` 裡的 offset）——這是你 Ch 22 進 IDA 要跳過去的位址。
4. `strings -a <真實.so>` 掃 `.rodata`，找有沒有 URL、`key`、演算法常數。掃到的每個字串都是一條逆向線索。
5. `readelf -d <真實.so>` 找 `INIT_ARRAY`——確認這個「比 JNI_OnLoad 更早跑」的反調試藏身處存在，並記下它的位址。

## 本章重點整理

- Android 的 `.so` 是 **ELF**（magic `\x7fELF`），`.so` 的型別是 **`ET_DYN`**；`e_machine` 欄位（183=AArch64）就是 Ch 20 要確認的架構。
- ELF 有兩張表：**program header 給載入器**（照 `PT_LOAD`/`PT_DYNAMIC` 映射與連結，不能刪）、**section header 給工具**（切成 `.text`/`.rodata` 等，能刪——殼常刪它讓 IDA 看不懂）。
- 逆向盯這幾個 section：**`.rodata`（找字串/金鑰）、`.init_array`（反調試藏身，比 JNI_OnLoad 早）、`.got`（可改的跳轉表，GOT hook 的目標）、`.dynsym`（找 `Java_` JNI 入口）**。
- **動態符號 + 重定位** 是 `dlopen` 把 `.so` 接進進程的機制：`.dynsym` 記匯出/匯入的名字，重定位表把「載入時才知道的位址」填進去。
- 拿到 `.so` 的偵察開場：`readelf -h`（架構）→ `-d`（依賴/init）→ `--dyn-syms`（JNI 入口）→ `strings`（字串線索）。

## 自我檢核

- [ ] 能說出 `.so` 是哪種 ELF 型別，以及讀哪個欄位判斷它是 ARM64 還是 x86
- [ ] 能解釋 program header 與 section header 的差別，以及為什麼殼能刪 section header 而 `.so` 照樣執行
- [ ] 要找 native 裡的硬編碼字串/金鑰，該去哪個 section
- [ ] 能說出 `.init_array` 為什麼是逆向要最先列的東西之一（和 Ch 19 呼應）
- [ ] 能講清楚 GOT 為什麼是「可改的跳轉表」，這和 Ch 25 的 hook 有什麼關係

## 延伸閱讀

- **[ELF-64 Object File Format 規格](https://uclibc.org/docs/elf-64-gen.pdf)** / **[System V ABI](https://refspecs.linuxfoundation.org/elf/gabi4+/contents.html)**
  - **讀哪裡**：ELF header、program header、section header、`Elf64_Sym`、relocation 的欄位定義
  - **和本章的關聯**：本章 Python 解析每個欄位的權威出處；逐欄位對照你就懂 `struct.unpack` 那串格式碼在拆什麼
- **[man elf（Linux ELF 手冊）](https://man7.org/linux/man-pages/man5/elf.5.html)**
  - **讀哪裡**：`Elf64_Ehdr`/`Elf64_Phdr`/`Elf64_Shdr`/`Elf64_Dyn` 結構定義，比規格白話
  - **為什麼值得讀**：查「這個欄位是 u32 還是 u64、在第幾 byte」最快的參考
- **[Android linker（AOSP bionic）](https://cs.android.com/android/platform/superproject/main/+/main:bionic/linker/)**
  - **讀哪裡**：`linker.cpp` 的 `soinfo` 載入流程、如何處理 `PT_LOAD`/`.init_array`/重定位
  - **和本章的關聯**：本章講的「dlopen 做了什麼」，這是 Android 上的真實實作；脫殼修 `.so` 前值得掃一遍它對 header 的要求
- **[SoFixer（記憶體 dump 的 .so 修復工具）](https://github.com/F8LEFT/SoFixer)**
  - **這篇說什麼**：把從記憶體 dump 出、section 資訊殘缺的 `.so` 修回能被 IDA 分析的檔案
  - **讀哪裡**：README 對「記憶體位址↔檔案偏移換算」的說明
  - **前提知識**：讀過本章的兩種 header 差別，才懂它在修什麼、為什麼要修

下一章我們把工具真正打開。你已經懂 ELF 骨架、會讀 ARM64、知道 JNI 綁定怎麼找——Ch 22 教你在 **IDA / Ghidra** 裡把這些串起來：載入 `.so`、F5 反編譯、識別 JNI 函式、匯入 JNI 結構讓 `env->` 顯示函式名、重命名與交叉引用，最後用 IDAPython/Ghidra script **自動化找 RegisterNatives**，把幾千個匿名函式裡的關鍵那幾個標定出來。

→ [Ch 22 IDA / Ghidra 逆 .so](./22-ida-ghidra-so.md)
