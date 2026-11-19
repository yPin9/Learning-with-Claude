# Ch 6 — PE 格式與記憶體佈局速成

> **目標**：讀懂一支 Windows 執行檔的骨架——從檔案上的 header 到載入後的記憶體佈局。你會知道 ImageBase、entry point、section 權限、IAT/EAT、relocation 各是什麼、放哪、對 exploit 有什麼用。這章是後面逆向找洞（Part 4）、egghunter（Part 5）、ASLR 繞過（Part 8）、PE 後門（Part 10）共同的地基。

> **環境**：Win10 x64 22H2，觀察工具 WinDbg（2026）/ CFF Explorer / PE-bear / Python `pefile`，對象為 32-bit PE。

## 為什麼 exploit 開發要懂 PE？

你可能想：「我是來打洞的，又不是來寫 loader，幹嘛懂檔案格式？」因為 OSED 後半每一個關鍵動作，背後都是 PE 的某個欄位：

- **找 jmp esp 跳板**（Ch 9）：你是在某個模組的 `.text` section 裡找 opcode——得知道 section 在哪、載到哪。
- **挑無 ASLR 的模組**（Ch 31）：ASLR 開關就是 PE optional header 裡的一個旗標（`DYNAMICBASE`）。
- **寫 shellcode 找 API**（Ch 23–24）：你要手動走 kernel32 的 **EAT**（匯出表）解析函式位址。
- **PE 後門**（Ch 37–38）：你要改 **entry point**、找 **code cave**、調 **section 權限**。

所以 PE 不是背景知識，是工具。這章只取 OSED 用得到的切片——完整的 PE 規範很大（本 repo 的 `windows_pwn` Ch 3 挖得更深，想要細節去那裡）。

## 先建立直覺：一支 exe 的兩種樣貌

同一支 PE 有兩種樣貌：**躺在磁碟上**和**被載入記憶體後**。它們佈局相似但不完全一樣，差別正是很多雷的根源。

```
   磁碟上 (file)                      載入記憶體後 (image)
┌────────────────────┐            ┌────────────────────┐  ← ImageBase (如 0x00400000)
│ DOS header (MZ)    │            │ headers（映射）     │
│ DOS stub           │            ├────────────────────┤
│ NT headers (PE\0\0)│            │ .text  (code, R-X)  │
│  - FileHeader      │   載入器    │                    │
│  - OptionalHeader  │ ─────────► ├────────────────────┤
│ Section headers    │  依對齊     │ .rdata (常數, R--)  │
├────────────────────┤  展開並     ├────────────────────┤
│ .text  (raw)       │  套權限     │ .data  (變數, RW-)  │
│ .rdata (raw)       │            ├────────────────────┤
│ .data  (raw)       │            │ .reloc (重定位表)   │
│ .reloc (raw)       │            └────────────────────┘
└────────────────────┘
   用 FileAlignment 對齊            用 SectionAlignment 對齊
   (常 0x200)                       (常 0x1000 = 一頁)
```

兩個關鍵差異，先記住：

1. **對齊不同**：磁碟用 `FileAlignment`（常 0x200），記憶體用 `SectionAlignment`（常 0x1000，一個記憶體頁）。所以同一個東西在檔案裡的 offset 和在記憶體裡的 offset **不一樣**——這就是 RVA 換算的來源。
2. **權限是記憶體才有的概念**：磁碟上就是一堆 bytes，載入後每個 section 才被套上「可讀/可寫/可執行」。`.text` 是 R-X（可執行不可寫），`.data` 是 RW-（可寫不可執行）。DEP（Ch 27）就是嚴格執行這套權限。

## 核心一：header 的層層結構

從檔案開頭一路往下：

```
DOS Header          起始 "MZ"（0x4D 0x5A）。唯一有用的欄位是 e_lfanew：
                    指向 NT headers 的 offset（相容性殘留，但 loader 真的用它）

NT Headers          起始 "PE\0\0"（0x50 0x45 0x00 0x00），分兩塊：
  ├ FileHeader      Machine（i386=0x14c 表 32-bit）、NumberOfSections、
  │                 SizeOfOptionalHeader、Characteristics
  └ OptionalHeader  （名字騙人，它不是選用的）最重要的欄位都在這：
                    - AddressOfEntryPoint  程式從哪開始跑（RVA）
                    - ImageBase            希望被載到哪（如 0x00400000）
                    - SectionAlignment / FileAlignment
                    - DllCharacteristics   ← ASLR/DEP/SafeSEH 的旗標在這
                    - DataDirectory[]      指向各種表（匯入/匯出/重定位…）

Section Headers     每個 section 一條：Name、VirtualAddress(RVA)、
                    VirtualSize、PointerToRawData(檔案offset)、
                    SizeOfRawData、Characteristics(權限)
```

> **為什麼 `AddressOfEntryPoint` 對 PE 後門關鍵？** 它是「程式載入後第一條執行的指令」的 RVA。Ch 37 的 PE 後門，核心動作就是把這個欄位改成指向你注入的 shellcode，讓後門在原程式邏輯之前先跑。

`DllCharacteristics` 這個欄位要特別記，它是一組 bit flag：

| 旗標 | 意義 | 對 exploit 的影響 |
|---|---|---|
| `DYNAMICBASE` | 支援 ASLR | 清掉這 bit 的模組不隨機化 → Ch 31 挑來用 |
| `NXCOMPAT` | 相容 DEP | 影響 stack 能不能執行 → Ch 27 |
| `NO_SEH` / SafeSEH（另在 Load Config） | SEH 保護 | 影響能不能拿它的 POP POP RET → Ch 13 |

mona 的 `!py mona modules` 就是把每個載入模組的這些旗標列成一張表，讓你一眼挑出「無 ASLR、無 SafeSEH、無壞字元」的模組——那是你 gadget 的金礦。

## 核心二：RVA、file offset、VA 的換算

這是 PE 最常把人搞糊塗、也最常害你下錯斷點的地方。三個詞：

- **RVA（Relative Virtual Address）**：相對 ImageBase 的偏移。IDA 預設、PE 檔內的欄位，大多是 RVA。
- **VA（Virtual Address）**：載入後的實際位址 = ImageBase + RVA（還要再加 ASLR 的隨機偏移）。WinDbg 看到的是 VA。
- **file offset（raw offset）**：在磁碟檔案裡的位元組位置。

換算關係：

```
VA        = 實際ImageBase + RVA                （實際ImageBase 可能因 ASLR ≠ 檔案裡寫的）
file off  = RVA - section.VirtualAddress + section.PointerToRawData
```

> **為什麼非懂不可？** 你在 IDA 看到可疑函式在 `0x00401740`（這是以檔案 ImageBase 0x400000 算的 VA，即 RVA 0x1740）。但 ASLR 下該模組這次載到 `0x00960000`，WinDbg 裡它的實際位址是 `0x00961740`。你若直接 `bp 0x00401740` 會打空。正確做法是 `bp 模組名+0x1740`，讓 WinDbg 自己加這次的基址。Ch 17 的靜態↔動態雙軌就是在處理這個換算。

## 核心三：IAT 與 EAT

程式要呼叫別的 DLL 的函式（如 `kernel32!VirtualProtect`），靠兩張表牽線：

```
匯出端 (kernel32.dll)              匯入端 (你的程式)
┌─────────────────────┐          ┌──────────────────────┐
│ EAT                 │          │ IAT                   │
│ (Export Addr Table) │          │ (Import Addr Table)   │
│  VirtualProtect →    │  loader  │  [VirtualProtect] ────┼→ 被 loader 填入
│      函式 RVA        │ ───────► │  [WinExec]            │   實際 VA
│  WinExec →           │  填表    │  [recv]               │
└─────────────────────┘          └──────────────────────┘
```

- **EAT（匯出表）**：一個 DLL 宣告「我提供哪些函式、各在我內部哪個 RVA」。
- **IAT（匯入表）**：程式宣告「我要用哪些外部函式」，載入時 loader 逐一查對應 DLL 的 EAT，把實際 VA 填進 IAT。程式之後透過 IAT 間接呼叫。

> **這對 OSED 的兩個用途**：(1) **逆向**（Ch 15）——看一支服務 import 了 `recv`/`socket`，立刻知道它是網路服務；import 了 `strcpy` 就警覺可能有溢位。(2) **shellcode**（Ch 23–24）——你的 shellcode 不能依賴目標的 IAT（你不知道佈局），得自己走 kernel32 的 **EAT** 把 API 位址挖出來。這就是 Ch 24「手動解析 export table」在做的事。

## 核心四：relocation 與 ASLR 的關係

PE 裡的 code 常含「絕對位址」（例如「跳到 0x00401500」）。這些位址是以「希望的 ImageBase」算的。如果模組實際沒載到希望的位址（ASLR 把它搬走了），這些絕對位址全錯——所以需要 **relocation（重定位）**。

```
.reloc section 記著「哪些位置寫了絕對位址、需要修正」
         │
載入到非預設基址時，loader 走 .reloc，
把每個記錄的位址都加上「實際基址 − 預設基址」的差值
```

關鍵推論：**一個模組要支援 ASLR（被搬動），就必須有 `.reloc` 且設了 `DYNAMICBASE`。** 反過來，有些老舊或第三方模組沒設 `DYNAMICBASE`，它每次都載到固定的預設 ImageBase——這正是 Ch 31 ASLR 繞過要找的「非隨機化模組」。

## 範例：用 pefile 看一支 PE（可在本機跑的部分）

`pefile` 是純 Python 的 PE 解析庫，不需要 Windows 就能讀 PE 檔。這是本章少數能實跑的部分：

```python
import pefile
pe = pefile.PE("vulnserver.exe")

print(hex(pe.OPTIONAL_HEADER.ImageBase))          # 希望的載入基址
print(hex(pe.OPTIONAL_HEADER.AddressOfEntryPoint)) # entry point RVA
print(hex(pe.OPTIONAL_HEADER.DllCharacteristics))  # ASLR/DEP 旗標組合

for s in pe.sections:
    print(s.Name.decode().strip('\x00'),
          hex(s.VirtualAddress),       # RVA
          hex(s.Misc_VirtualSize),
          hex(s.Characteristics))      # 權限 bits
```

> **未實測**：我無法在本機確認 `vulnserver.exe` 的實際欄位值（需要那支檔案）。上面的 `pefile` API 來自其官方文件（見延伸閱讀），你拿到任一 32-bit PE 都能跑出對應數字。`DllCharacteristics` 若含 `0x0040` 代表 `DYNAMICBASE`（ASLR）、`0x0100` 代表 `NXCOMPAT`（DEP）——這兩個常數來自 PE 規範，可用 `pefile` 的旗標解析確認。

## 對比與取捨：PE vs ELF（給 Linux 背景）

| 面向 | ELF (Linux) | PE (Windows) |
|---|---|---|
| 魔術位元組 | `\x7fELF` | `MZ` → `PE\0\0` |
| 入口點 | `e_entry` | `AddressOfEntryPoint`（RVA） |
| 匯入/匯出 | `.dynsym` / PLT / GOT | IAT / EAT |
| 位址無關寫法 | GOT + PLT，PIE | IAT + relocation |
| 繞 ASLR 靠改寫 | 改 GOT 條目 | 沒 GOT；改 IAT / 返回位址 / SEH / 函式指標 |
| 隨機化開關 | 編譯 PIE + 系統設定 | PE 旗標 `DYNAMICBASE` |

> 對 Linux pwn 老手最重要的一句話：**Windows 沒有 GOT 可打。** 你習慣的「改 GOT 劫持」在這裡沒有直接對應；Windows 上等價的劫持點是 IAT 條目、返回位址、SEH handler、或各種函式指標（vtable、callback）。Ch 36 的 format string 任意寫會回到這個主題。

## 踩雷集錦

1. **「IDA 的位址直接拿去 WinDbg 下斷」**：錯誤直覺是兩者位址相同。正確：IDA 是 ImageBase+RVA，WinDbg 是含 ASLR 的實際 VA。用 `bp 模組名+RVA` 讓 WinDbg 補基址。
2. **「以為 OptionalHeader 是選用的，可以跳過」**：名字騙人。entry point、ImageBase、ASLR/DEP 旗標、DataDirectory 全在這塊，是 PE 最核心的 header。
3. **「file offset 和 RVA 混用」**：在檔案裡 patch（如 PE 後門改 entry）要用 file offset；在記憶體裡下斷要用 RVA/VA。換算公式記住：`file = RVA − VirtualAddress + PointerToRawData`，別直接拿 RVA 當檔案位置。
4. **「找 gadget 卻挑了開 ASLR 的模組」**：那模組位址每次變，你寫死的 gadget 位址下次就錯。先用 mona 看 `DYNAMICBASE`，挑 Rebase=False 的模組（Ch 9、Ch 31）。
5. **「想在 .text 寫東西」**：`.text` 是 R-X，不可寫。想寫就要找可寫 section 或先改權限。這也是為什麼 shellcode 不能直接放 `.text`。

## 進階：再往深一層

- **Load Config 與 SafeSEH 表**：SafeSEH 的合法 handler 清單存在 Load Config directory 指向的 `SEHandlerTable`。Ch 13 繞 SafeSEH 時，你挑的是「這張表為空（未啟用 SafeSEH）」的模組。
- **TLS callback**：PE 可在 entry point 之前跑 TLS callback——這是比改 entry point 更隱蔽的 PE 後門落點，也是反調試常用招。深入見 `windows_pwn`。
- **Rich header / 編譯器指紋**：PE 裡藏有編譯器版本資訊，逆向時可用來推測目標怎麼編的（有沒有開 /GS、/SAFESEH）。
- 更完整的 PE 內部（資料目錄全表、延遲匯入、資源）見 **[`windows_pwn` Ch 3 PE 格式深挖](../windows_pwn/03-pe-format.md)**，本章只取 OSED 必要切片。

## 本章重點整理

- PE 有磁碟與記憶體兩種佈局，差在對齊（FileAlignment 0x200 vs SectionAlignment 0x1000）與權限（記憶體才有 R/W/X）。
- 核心欄位都在 OptionalHeader：`AddressOfEntryPoint`、`ImageBase`、`DllCharacteristics`（ASLR/DEP 旗標）、`DataDirectory`。
- RVA（相對）↔ VA（實際，含 ASLR）↔ file offset 三者要會換算；下斷用 `模組+RVA`。
- IAT（我要用的外部函式）/ EAT（我提供的函式）——shellcode 走 EAT 找 API，逆向看 IAT 猜功能。
- 支援 ASLR 的模組必有 `.reloc` + `DYNAMICBASE`；沒設的模組位址固定，是繞 ASLR 的切入點。
- Windows 沒有 GOT；劫持點改走 IAT / 返回位址 / SEH / 函式指標。

## 自我檢核

- [ ] 不看筆記，能說出同一個函式在檔案 offset 和記憶體 VA 為何不同
- [ ] 能解釋為什麼在 IDA 看到 `0x401740` 不能直接 `bp 0x401740`
- [ ] 能說出 IAT 和 EAT 的差別，以及 shellcode 為什麼要走 EAT
- [ ] 知道「一個模組沒開 ASLR」在 PE 裡表現為哪個旗標，怎麼查
- [ ] 能說出 Windows 上有哪些可劫持的指標（對照 Linux 的 GOT）

## 延伸閱讀

- **[PE Format — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：Optional Header、Section Table、Import/Export Directory 三節。
  - **學什麼**：所有 PE 欄位的權威定義；本章每個欄位名的出處。
  - **注意**：規範很長，只讀上述三節即可覆蓋 OSED 需求。

- **[pefile 官方文件 / GitHub](https://github.com/erocarrera/pefile)**
  - **讀哪裡**：README 與 `OPTIONAL_HEADER`、`sections` 的屬性清單。
  - **學什麼**：用 Python 解析 PE，本章範例與 Ch 38 PE 後門都靠它。
  - **前提**：會基本 Python 即可。

- **[An In-Depth Look into the Win32 Portable Executable File Format — Matt Pietrek（MSDN Magazine）](https://learn.microsoft.com/en-us/archive/msdn-magazine/2002/february/inside-windows-win32-portable-executable-file-format-in-detail)**
  - **讀哪裡**：headers 與 sections 的圖解段落。
  - **為什麼值得讀**：PE 格式最經典的導讀文，圖解清楚；年代久但格式本身變動極小。
  - **前提**：搭配本章的雙佈局圖一起看最有感。

- **[`windows_pwn` Ch 3 — PE 格式深挖](../windows_pwn/03-pe-format.md)**
  - **讀哪裡**：想要資料目錄全表、TLS、延遲匯入等本章略過的細節時。
  - **和本章的關聯**：本章是 OSED 切片版，那章是參考書版。

地基到此完成——偵錯器會用了、PE 讀得懂了。從下一章起進入正題：Part 2 的 stack overflow 完整方法論。你會第一次把前面所有工具串起來，打出一個能彈 shell 的 exploit。

→ [Ch 7 vanilla stack overflow：fuzz → crash → 控 EIP](./07-stack-overflow-methodology.md)
