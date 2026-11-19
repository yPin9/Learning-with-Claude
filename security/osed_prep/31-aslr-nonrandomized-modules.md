# Ch 31 — ASLR 復習 + 非隨機化模組利用

> **目標**：複習 ASLR（Address Space Layout Randomization）在 Windows x86 的隨機化範圍與熵，搞懂為什麼一個「沒開 `/DYNAMICBASE`」的模組位址每次載入都固定，並學會用 mona `modules` 指令篩出這種模組，直接從裡面取 `jmp esp` / ROP gadget——這是 OSED 考場上最省力的 ASLR 繞過路線，常常比找 info leak 快得多。
>
> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python3

---

## 為什麼需要這個？

Part 7 已經把 DEP 繞過的機制講完（ROP chain 呼叫 `VirtualProtect`），但那整套 chain 的前提是：**你知道 gadget 在哪裡**。gadget 的位址是 `模組基址 + RVA`，如果模組基址每次開機都不一樣，你寫死在 payload 裡的位址下一次就失效。

這正是 ASLR 要解決的問題，也是它帶給攻擊者的麻煩。OSED 的實務是：**先問這台靶機上有沒有一個模組完全沒開 ASLR**——如果有，直接從那裡挖 gadget，連 info leak 都不用做。這是 OSED 題目裡最常見、最划算的第一條路，比 Ch 32 的 info leak 原語簡單一個量級。

## 先建立直覺：ASLR 到底隨機化了什麼

```
┌─────────────────────────────────────────────────────────────┐
│                  一個 32-bit process 的虛擬位址空間            │
│                                                               │
│   0x7FFE0000  ┌───────────┐                                  │
│               │ ntdll.dll │ ← ASLR 隨機化：每次開機換位置       │
│               └───────────┘                                  │
│   0x6xxxxxxx  ┌───────────┐                                  │
│               │kernel32   │ ← ASLR 隨機化                     │
│               └───────────┘                                  │
│   0x5xxxxxxx  ┌───────────┐                                  │
│               │  heap     │ ← ASLR 隨機化（per-process）       │
│               └───────────┘                                  │
│   0x0xxx0000  ┌───────────┐                                  │
│               │  stack    │ ← ASLR 隨機化（per-thread）        │
│               └───────────┘                                  │
│   0x00400000  ┌───────────┐                                  │
│               │ target.exe│ ← 如果沒開 DYNAMICBASE，           │
│               │ (固定基址)  │   永遠載在它的 preferred ImageBase │
│               └───────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

ASLR 要隨機化的區域主要有四塊：**image（EXE/DLL 的載入位址）**、**heap**、**stack**、**PEB/TEB**。但「隨機化」不是全有或全無——它是**逐個模組**標記的：PE 檔案的 `DllCharacteristics` 欄位裡有一個 `IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE`（`0x0040`）旗標，連結器要加這個旗標（`/DYNAMICBASE`），loader 才會把這個模組的載入位址隨機化。**沒有這個旗標的模組，loader 會嘗試把它載到 PE Optional Header 裡記錄的 `ImageBase`**（那個固定的 preferred base），只有在這個位址已經被佔用時才會 fallback 到別的位置（而且通常還是落在同一個備用位址，因為 32-bit 位址空間有限）。

這跟 Linux 的 `-fPIE -pie` 是同一個概念的 Windows 版本：沒加 `-fPIE` 編譯的執行檔，即使系統開了 ASLR，它本身還是在固定位址。Windows 上這個旗標叫 `/DYNAMICBASE`，而且**不只 EXE 本身，每一個 DLL 都要分別標記**，這是本章要利用的關鍵縫隙。

## Windows x86 的熵與限制（複習）

> 以下熵數字來源於 ASLR 的公開研究（延伸閱讀第一條），學習者請在自己環境用 mona `modules` 多次重開機觀察驗證，不同 Windows build 可能有誤差。

- **x86（32-bit）image ASLR**：沒有高熵模式可選（`/HIGHENTROPYVA` 只對 64-bit image 有意義），熵大約只有 **8 bits**——也就是 256 個可能的載入位址（以 64KB 為對齊單位，跨越 256 × 64KB = 16MB 範圍）。這遠低於 x64 系統的 19 bits。
- **per-boot 固定性**：Windows 的 image ASLR 是**同一次開機內固定**，不是每次執行都重新隨機（這點和 Linux 每次 `exec` 都重新隨機不同）。同一台機器、同一次開機，所有 process 裡的同一個系統 DLL（例如 `kernel32.dll`）的基址都相同。重開機後才會換一批新的隨機值。
- **為什麼 x86 熵特別低**：32-bit 位址空間本身只有 4GB，拿掉 kernel 保留的上半段（預設 2GB/2GB 分割），使用者空間只剩約 2GB，可供隨機化的範圍比 64-bit 系統小得多，熵自然被壓縮。這也是為什麼 Ch 24（本章稍後會對照）提到的「x86 低熵暴力」在 OSED 的場景裡是一條可行（但通常不是首選）的路線。

這兩點合起來意味著：**x86 上即使所有模組都開了 ASLR，熵也不算高**；但更重要的是，OSED 的目標程式往往是第三方軟體（FTP server、備份軟體、工控軟體之類），它們常常**整包帶自己的 DLL**，而這些 DLL 的建置流程可能完全沒有加 `/DYNAMICBASE`。

## 核心：找一個沒開 ASLR 的模組

### 用 mona `modules` 盤點

附掛上目標程式後，在 WinDbg 命令列跑：

```
!py mona modules
```

mona 會列出目前載入的每一個模組，附帶一串旗標欄位，其中你要盯的是 **Rebase** 和 **ASLR** 這兩欄：

```
 Module info :

 Base       | Top        | Size       | Rebase | SafeSEH | ASLR  | NX    | OS DLL | Version, Modulename & Path
 -----------|------------|------------|--------|---------|-------|-------|--------|---------------------------
 0x00400000 | 0x0045c000 | 0x0005c000 | False  | False   | False | False | False  | target.exe
 0x10000000 | 0x10042000 | 0x00042000 | False  | True    | False | True  | False  | essfunc.dll
 0x62500000 | 0x62716000 | 0x00216000 | False  | False   | False | False | False  | vendor_old.dll
 0x74d20000 | 0x74e10000 | 0x000f0000 | True   | True    | True  | True  | True   | kernel32.dll
 0x77c10000 | 0x77df0000 | 0x001e0000 | True   | True    | True  | True  | True   | ntdll.dll
```

欄位意思：

| 欄位 | 意思 |
|---|---|
| **ASLR** | `False` = 這個模組完全沒開 `/DYNAMICBASE`，每次載入都在同一個固定位址 |
| **Rebase** | `False` = loader 從未需要把它移走（通常伴隨 ASLR: False 一起出現，代表非常穩定） |
| **SafeSEH** | 跟本章無關（Ch 13 的話題），但找 gadget 時如果你同時需要 SEH-based 跳轉，這欄也要看 |
| **NX** | 這個模組的記憶體頁是否標記不可執行（DEP 相關，Ch 27） |
| **OS DLL** | `True` 代表是系統 DLL（`kernel32`/`ntdll` 這類），通常都開了 ASLR |

上面的範例裡，`target.exe` 和 `essfunc.dll`、`vendor_old.dll` 三個模組的 `ASLR` 欄都是 `False`——**這三個模組的位址，不管重開機幾次、process 重啟幾次，都是同一組固定值**。`kernel32.dll` 和 `ntdll.dll` 則乖乖開著 ASLR（`True`），這是系統 DLL 的常態。

### 直接從固定模組挖 gadget

既然 `vendor_old.dll` 的基址固定在 `0x62500000`，你可以直接對它跑 `jmp esp` 搜尋或 ROP gadget 搜尋：

```
!py mona find -s "\xff\xe4" -m vendor_old.dll
```

找到的任何位址，直接寫進 payload 就能長期有效——不用算基址、不用 leak，因為基址根本不會變。這跟 Ch 9（stack overflow 練習裡找 `jmp esp`）的操作完全相同，只是這裡你是「先篩模組再找 gadget」，而不是「隨便在整個記憶體找」。

> **未實測，理論預期**：上面的位址、模組名稱都是示意值。請在你自己的靶機對實際目標跑 `!py mona modules`，找出 `ASLR: False` 那一行，記下它的模組名稱和 Base，再用 Ch 28 教的 gadget 搜尋流程在那個模組裡找你需要的 gadget。

## 為什麼第三方程式自帶的 DLL 常常忘了開 ASLR

這不是巧合，是現實世界軟體供應鏈的通病，原因大致有三類：

1. **舊工具鏈遺留**：`/DYNAMICBASE` 在 Visual Studio 2010 之後才變成預設開啟。很多商用軟體的核心模組是十幾年前寫的，建置腳本從沒更新連結器旗標，升級 OS、升級 Visual Studio 版本時也沒人回頭重新檢查 `DllCharacteristics`。
2. **第三方/OEM 元件沒有重新連結**：有些 DLL 是從更早的程式碼庫直接搬過來，甚至是跟供應商買的 SDK 元件，原始連結設定早已遺失，沒人敢重新編譯怕破壞相容性。
3. **「反正有系統幫忙開 ASLR」的錯誤假設**：開發者以為「Windows 10 系統整體有開 ASLR 保護」，但沒意識到**ASLR 是逐模組標記的**，系統層級的保護不會自動幫你的 DLL 補上旗標。

這也是為什麼 OSED 的靶機/題目常常是刻意挑選、或刻意不更新的第三方服務——這類程式天然就留著這個縫隙，出題者不需要特別製造漏洞，現實世界本來就這樣。

## ASLR 全開 vs 一個固定模組：記憶體對照圖

```
情境 A：所有模組都開 ASLR（理想情況，攻擊者需要 info leak）

  開機 1：  target.exe @ 0x00e30000   kernel32 @ 0x74d20000
  開機 2：  target.exe @ 0x00960000   kernel32 @ 0x75a10000   ← 位址全變了
  開機 3：  target.exe @ 0x00c10000   kernel32 @ 0x76220000

  → 寫死在 payload 裡的位址，下次開機就失效，必須先 leak 出位址才能算 gadget


情境 B：vendor_old.dll 沒開 ASLR（本章情境，攻擊者直接用固定位址）

  開機 1：  vendor_old.dll @ 0x62500000   （kernel32/ntdll 還是在變）
  開機 2：  vendor_old.dll @ 0x62500000   ← 永遠一樣
  開機 3：  vendor_old.dll @ 0x62500000   ← 永遠一樣

  → gadget 位址 = 0x62500000 + gadget_RVA，寫死進 payload，長期有效
    （只要這個版本的 vendor_old.dll 沒被升級替換）
```

這張圖要帶走的重點：**ASLR 的強度是整個系統裡最弱的那個模組決定的**。只要有一個模組漏了旗標，攻擊者就不需要理會其他模組開得多嚴。

## 對比取捨：三種找 gadget 來源的方式

| 方式 | 前提 | 穩定性 | 複雜度 |
|---|---|---|---|
| **非 ASLR 模組（本章）** | 至少一個載入模組的 `ASLR: False` | 極高（固定位址，重開機不變） | 低（找到就能用） |
| **info leak 算基址（Ch 32）** | 有洩漏原語可用 | 中（每次開機要重新 leak 一次） | 中～高（需要額外漏洞/功能） |
| **x86 低熵暴力（延伸閱讀）** | 服務會自動重啟、8 bits 熵可接受的嘗試次數 | 低（平均 128 次嘗試，可能被偵測/鎖定） | 中（需要自動化嘗試迴圈） |

OSED 考場的現實策略：**先跑一次 `!py mona modules`，看有沒有免費的非 ASLR 模組**。如果有，直接用，省掉後面所有麻煩。只有在所有相關模組都乾淨地開了 ASLR 時，才需要進到 Ch 32 的 info leak 路線。

## 踩雷集錦

1. **以為「這個模組看起來是系統元件，一定有開 ASLR」**：不對，要看 mona 的 `ASLR` 欄，而不是看模組名稱猜。有些第三方 DLL 名字聽起來很正式（例如 `ws2_32helper.dll`），但完全不是系統模組，也完全沒開 `/DYNAMICBASE`。永遠用 `!py mona modules` 的實際旗標判斷，不要靠印象。

2. **只看 `ASLR` 欄，沒看 `Rebase` 欄**：`ASLR: False` 但 `Rebase: True` 代表這個模組雖然沒有隨機化基址，但 loader 曾經因為位址衝突把它**rebase 到別的固定位址**（不是它的 preferred ImageBase，但依然固定）。只要同一份二進位檔案、同一套載入順序不變，這個 rebase 後的位址通常也是穩定的，但嚴謹一點的做法是在你自己的環境多次重啟驗證它是否真的每次都落在同一個位址。

3. **用了對的模組，但 gadget 選錯**：固定位址只解決「基址不變」的問題，gadget 本身還是要正確找（避開壞字元、符合暫存器需求等），這部分邏輯跟 Ch 9（`jmp esp`）、Ch 28（ROP gadget）完全一樣，本章只是換了一個「不用算基址」的模組來源。

4. **忽略版本差異**：同一支軟體的不同版本，`vendor_old.dll` 的內容可能不同，gadget 的 RVA 會跟著變。拿到靶機後第一件事是確認你分析的 DLL 版本跟靶機上實際跑的版本一致（檔案大小、`File Version` 資訊、或直接比對 hash），不要套用別的環境測出來的 RVA。

5. **誤以為「沒開 ASLR 的模組永遠安全」**：這只是對攻擊者有利的一個巧合縫隙，不代表這個模組本身沒做其他防護（它仍然可能有 SafeSEH、有 /GS）。本章只解決「基址固定」這一個子問題，不代表整條 exploit chain 其他環節都自動放行。

## 進階：再往深一層

- **部分開 ASLR 的混合情境**：有時候不是「整個模組」沒開 ASLR，而是模組本身開了 ASLR，但它靜態連結、內嵌進去的某個子元件（例如一個很舊的第三方函式庫以 static lib 形式編進主執行檔）在記憶體佈局上造成某些區段相對位移固定。這種情況比純粹的「模組旗標」更細緻，需要搭配 IDA 逐個檢查，Part 4 的逆向工作流在這裡會派上用場。
- **WDEG 的「強制隨機化」能不能補救**：Windows Defender Exploit Guard（WDEG）有一個「Force randomization for images (Mandatory ASLR)」選項，可以在系統層級強制給沒開 `/DYNAMICBASE` 的模組套上隨機化（即使它自己沒標記）。如果你發現靶機上「本該固定的模組」位址居然每次都在變，先檢查是不是系統開了這個 Mandatory ASLR 原則——這是本章「非 ASLR 模組永遠能用」假設唯一可能失效的系統層級因素。
- **x86 低熵暴力的實際操作**：如果真的沒有任何非 ASLR 模組，也沒有 info leak 原語，8 bits 熵的 x86 image ASLR 理論上可以暴力：利用服務崩潰自動重啟、同一次開機內 base 不變的特性，逐一嘗試 256 個可能基址。這條路線比 info leak 原始，但在某些「服務掛了會自動重啟、沒有 rate limiting」的靶機上依然可行，windows_pwn Ch 24 有更完整的熵分析和暴力步驟示範。

## 本章重點整理

- ASLR 隨機化四塊區域：image（模組載入基址）、heap、stack、PEB/TEB；旗標是**逐模組**標記的（`DllCharacteristics` 裡的 `DYNAMIC_BASE`，對應連結器的 `/DYNAMICBASE`）。
- Windows x86 image ASLR 的熵只有約 8 bits（256 個可能基址，64KB 對齊），且是 **per-boot 固定**，不是每次執行重新隨機。
- 一個系統裡只要有任何一個模組的 `ASLR: False`，這個模組的位址就永遠固定，可以直接從裡面找 gadget，不需要 info leak。
- 第三方軟體自帶的老舊 DLL 常常忘了加 `/DYNAMICBASE`，是現實世界和 OSED 考場最常見的突破口。
- 判斷工具：`!py mona modules`，看 `ASLR` 和 `Rebase` 兩欄；`ASLR: False` 就是可以直接使用的穩定 gadget 來源。

## 自我檢核

- [ ] 能說出 ASLR 的旗標是逐模組標記，不是系統整體開關，並解釋為什麼這帶來「找一個漏標模組」的攻擊面
- [ ] 能解釋 Windows x86 image ASLR 熵為什麼只有約 8 bits，以及「per-boot 固定」對攻擊者的實際意義
- [ ] 能說出 `!py mona modules` 輸出裡要看哪兩個欄位來判斷一個模組是否可以當穩定 gadget 來源
- [ ] 能解釋為什麼第三方/老舊 DLL 常常沒開 `/DYNAMICBASE`，列出至少兩種常見原因
- [ ] 能說出「非 ASLR 模組」「info leak」「x86 低熵暴力」三條繞過路線的優先順序與各自前提

## 延伸閱讀

- **[windows_pwn Ch 24 — ASLR：Windows 特性 / leak / 部分覆寫](../windows_pwn/24-aslr.md)**
  - 讀哪裡：「非 ASLR 模組：穩定 Gadget 的黃金來源」一節，以及「三條 ASLR 繞過路線」整節。
  - 學什麼：同一機制更深入的 x64 對照、per-boot 固定性的攻擊場景（跨 process 共用基址）、以及 DllCharacteristics 旗標的完整對照表。
  - 前提：本章讀完後對照，會發現兩邊的「非 ASLR 模組」推導互相印證，windows_pwn 那邊多了 x64/HIGHENTROPYVA 的對比。
- **Corelan — "Exploit writing tutorial part 6: Bypassing Stack Cookies, SafeSEH, SEHOP, HW DEP and ASLR"**（[corelan.be](https://www.corelan.be/index.php/2009/09/21/exploit-writing-tutorial-part-6-bypassing-stack-cookies-safeseh-hw-dep-and-aslr/)）
  - 讀哪裡：「Bypassing ASLR」一節，特別是 non-ASLR DLL 和 `!mona modules` 的實踐部分。
  - 學什麼：從 x86 exploit 開發者視角看「找漏標模組」這條路線的完整操作，和本章的 mona 流程是同一套方法論。
  - 前提：Ch 9（找 jmp esp）讀過，熟悉 mona 基本操作。
- **[/DYNAMICBASE — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/reference/dynamicbase-use-address-space-layout-randomization)**
  - 讀哪裡：全文（很短），特別是「Remarks」裡關於 preferred base 衝突時 fallback 行為的說明。
  - 學什麼：`/DYNAMICBASE` 旗標行為的官方權威定義，本章「ASLR 是逐模組標記」判斷的依據。
  - 前提：無，可直接讀。
- **"On the Effectiveness of Address Space Randomization"** — Shacham et al.（ACM CCS 2004）
  - 讀哪裡：Section 3「How Long Does It Take to Guess?」。
  - 學什麼：ASLR 熵與暴力猜測時間的理論分析框架，本章「x86 低熵」判斷背後的學術依據。
  - 前提：基本概率概念即可。

非 ASLR 模組是最省力的路線，但 OSED 的題目不會永遠留這個缺口給你——很多時候所有相關模組都乾乾淨淨開著 ASLR，你就必須想辦法在執行期洩漏一個真實位址，再自己算出基址。下一章進入這條更通用、但也更費工的路線：info leak。

→ [Ch 32 — info leak 製造：部分覆寫、可預測位址](./32-info-leak-primitives.md)
