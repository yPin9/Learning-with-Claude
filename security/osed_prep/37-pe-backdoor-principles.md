# Ch 37 — PE 後門原理：code cave、hijack entry point

> **目標**：理解「在一支既有的 PE 執行檔裡植入一段會先執行的程式碼，又不破壞原程式功能」這件事的機制——code cave、entry point 劫持、執行流還原、section 權限。重點是**看懂它怎麼運作**，這同時是 OSED 的考試模組，也是藍隊偵測這類寄生技術的基礎。

> **環境**：Win10 x64 22H2（32-bit PE），觀察/修改工具 Python `pefile` / CFF Explorer / PE-bear / WinDbg（2026）。**本章所有操作只在你自己擁有或明確授權的執行檔與隔離實驗室中進行**，用來理解機制與偵測，不針對任何真實散布對象。

## 為什麼 OSED 要教這個？

乍看「把後門塞進 exe」像是純攻擊技術，但它在 OSED（官方模組「Trojanizing PE Files」）和資安教育裡的定位很明確——它逼你把前面所有關於 PE 格式與 shellcode 的知識，反過來用在「修改一支既有檔案」上，這會把你對 PE 結構的理解從「讀得懂」推到「改得動」。而且：

- **理解即防禦**：惡意軟體寄生在合法程式裡是幾十年來的經典手法。你不先懂「後門怎麼塞進 entry point、怎麼藏進 code cave」，就寫不出偵測它的 YARA 規則、做不出完整性檢查。本 repo 的 `blue_team_dfir` 課正是這條技術的另一面。
- **PE 結構的壓力測試**：改 entry point、算 RVA↔file offset、調 section 權限——這些 Ch 6 學的欄位，在這章會被你親手改一遍，錯一個就跑不起來。沒有比「改壞一支 exe」更快學會 PE 的方法。

> 整門課的定位再強調一次：這是**受控、授權、純學習**的框架。你修改的是自己的載體程式，在隔離 VM 裡，目的是搞懂機制與偵測。本章不提供、也不需要「對第三方散布」的任何環節——那條線在本 repo 的信條裡講得很清楚。

## 先建立直覺：讓程式「先繞路再回家」

核心直覺一句話：**讓程式在做它原本該做的事之前，先偷偷跑一段你的程式碼，跑完再無縫接回原本的流程，讓使用者看不出差別。**

```
   原本的執行流                      植入後門後的執行流
┌──────────────────┐            ┌──────────────────┐
│ 載入器把控制權    │            │ 載入器把控制權    │
│ 交給 entry point  │            │ 交給 (新) entry   │ ← 改指向 code cave
│       │           │            │       │           │
│       ▼           │            │       ▼           │
│ ┌──────────────┐ │            │ ┌──────────────┐  │
│ │ 原始程式邏輯  │ │            │ │ 1. 存暫存器   │  │  你的 shellcode
│ │ (OEP)        │ │            │ │ 2. 跑 payload │  │  （如 reverse shell）
│ └──────────────┘ │            │ │ 3. 還原暫存器 │  │
│                  │            │ │ 4. 跳回 OEP ──┼──┼─┐
└──────────────────┘            │ └──────────────┘  │ │
                                │ ┌──────────────┐  │ │
                                │ │ 原始程式邏輯  │ ◄┼─┘ 無縫接回
                                │ │ (OEP)        │  │
                                │ └──────────────┘  │
                                └──────────────────┘
```

OEP（Original Entry Point，原始入口點）是這裡的關鍵詞。後門要做的就是：把 entry point 改指向自己，跑完**乾淨地跳回 OEP**。少了「跳回 OEP」這一步，程式一啟動就當掉，反而最容易被發現——所以「還原並回家」才是這技術的精髓，不是「塞進去」而已。

## 核心一：code cave——哪裡有空間塞程式碼？

你不能憑空在 exe 裡插入位元組（那會打亂所有 RVA 與 offset，整支檔案崩掉）。有兩條路放你的 shellcode：

**(1) code cave（程式碼空洞）**：section 因為對齊（Ch 6 的 FileAlignment/SectionAlignment）而留下的連續空白區（通常是一串 `0x00`）。這些空間本來就在檔案裡、不會改變檔案大小與其他 offset，把 shellcode 塞進去最省事。

```
.text section (SizeOfRawData = 0x1000)
┌────────────────────────────────────────────────┐
│ 實際 code (VirtualSize = 0x0C40)                 │
│ ......................................          │
├────────────────────────────────────────────────┤
│ 對齊補零區 0x0C40 ~ 0x1000 ← code cave (約 0x3C0) │  ← 塞 shellcode 在這
└────────────────────────────────────────────────┘
```

> **為什麼 code cave 有大小限制？** 它只有「VirtualSize 到 SizeOfRawData 之間」那段補零空間，常常只有幾十到幾百 bytes。放得下小 payload（如 egghunter + egg，或精簡的 WinExec shellcode），放不下大 reverse shell 時，就需要分散到多個 cave 串接，或走第二條路。

**(2) 新增一個 section**：在 section table 加一條、在檔案尾端附上一塊夠大的新 section，把 shellcode 放進去。空間不受限，但會改變檔案結構，較顯眼。

| 方案 | 空間 | 隱蔽性 | 複雜度 |
|---|---|---|---|
| 單一 code cave | 小（數十~數百 B） | 高（不改大小/結構） | 低 |
| 多 cave 串接 | 中（跳著用） | 中 | 中 |
| 新增 section | 大（任意） | 低（多一個怪 section） | 中 |

## 核心二：劫持 entry point

PE 的 `OptionalHeader.AddressOfEntryPoint`（Ch 6）是「載入後第一條執行指令」的 RVA。後門把它改成「你的 code cave 的 RVA」：

```
改前：AddressOfEntryPoint = 0x1420  （指向原始程式起點 OEP）
改後：AddressOfEntryPoint = 0x0C40  （指向你的 code cave）
       ↑ 同時把 0x1420 這個 OEP 記下來，shellcode 跑完要跳回去
```

> **要記住 OEP 的原始值。** 改掉 entry point 前，先抄下原本的 `AddressOfEntryPoint`（這裡是 RVA 0x1420）。你的 shellcode 結尾要跳回它——跳回時用的是執行期 VA（ImageBase + 0x1420，ASLR 下再加偏移），實作上常用「相對跳轉」或「算好絕對位址再 jmp」。

## 核心三：執行流還原——別把原程式搞壞

這是初學者最常漏、也最關鍵的一步。你的 shellcode 會用掉暫存器、改動旗標、動到 stack。如果跑完直接跳回 OEP，原程式會在一個被你弄亂的 CPU 狀態下繼續跑，八成立刻崩潰——而一個「一開就閃退」的程式是最明顯的異常。

正確結構：**進來先存狀態，離開前還原狀態**。

```asm
; 後門 stub 的骨架（示意，x86）
pushad                  ; 保存所有通用暫存器
pushfd                  ; 保存旗標

; ---- 你的 payload：建立 reverse shell / 執行指定命令 ----
; （這段就是 Part 6 手寫、或 msfvenom 產生的 shellcode）

popfd                   ; 還原旗標
popad                   ; 還原所有通用暫存器
; 跳回 OEP（原始 entry point）讓程式正常運作
; 實作上：把原 OEP 的 VA 推上 stack 後 ret，或直接 jmp 過去
```

> **未實測，理論預期**：上述 stub 的行為無法在本機實跑驗證。`pushad`/`popad` 保存/還原 EAX..EDI、`pushfd`/`popfd` 保存/還原 EFLAGS，語意來自 x86 指令集。你在自己環境可用 WinDbg 在改後的 entry point 下斷、單步確認「跑完 payload、暫存器還原、乾淨跳回 OEP」。注意若 payload 內部還動了 stack 指標或建立了阻塞（如同步 reverse shell 卡住），原程式可能被卡住——實務上後門常把 payload 丟到新執行緒避免阻塞主流程。

## 核心四：section 權限——讓 cave 可執行

DEP（Ch 27）嚴格執行 section 權限。`.text` 預設是 R-X（可讀可執行不可寫），你的 cave 若在 `.text` 裡，執行沒問題。但若你把 shellcode 放進 `.data`（RW-，不可執行）或新增的資料 section，載入後跳過去執行會直接觸發 DEP 的存取違規。

解法：把目標 section 的 `Characteristics` 加上 `IMAGE_SCN_MEM_EXECUTE`（`0x20000000`），必要時也加 `IMAGE_SCN_MEM_READ`（`0x40000000`）。

> **常數來源**：`IMAGE_SCN_MEM_EXECUTE = 0x20000000`、`IMAGE_SCN_MEM_READ = 0x40000000`、`IMAGE_SCN_MEM_WRITE = 0x80000000`，來自 PE 規範（winnt.h）。用 `pefile` 讀 `section.Characteristics` 可確認目前權限位元。

## 範例：規劃一次植入（不含完整武器碼）

把流程走一遍，數字為教學示意：

```
1. 用 pefile 開啟你自有的 hello.exe，讀出：
   - ImageBase        = 0x00400000
   - AddressOfEntryPoint (OEP RVA) = 0x1420   ← 抄下來
2. 掃 section 找夠大的 code cave：
   - .text: VirtualSize=0x0C40, SizeOfRawData=0x1000 → cave 約 0x3C0 bytes @ RVA 0x0C40
3. 在 cave 寫入 stub：pushad/pushfd → payload → popfd/popad → 跳回 OEP(0x1420)
4. 把 AddressOfEntryPoint 改成 0x0C40（cave 的 RVA）
5. 確認 .text 已含 IMAGE_SCN_MEM_EXECUTE（.text 預設有）
6. 存檔，在隔離 VM 裡用 WinDbg 在新 entry 下斷，單步驗證流程與回家
```

> **未實測**：具體 cave 大小、OEP、能否塞下取決於你那支載體檔。Ch 38 會用 `pefile` 把這流程寫成可操作的腳本（仍以你自有的載體 + 隔離環境為前提）。

## 對比與取捨

| 設計決策 | 選項 A | 選項 B | 權衡 |
|---|---|---|---|
| shellcode 放哪 | code cave | 新 section | cave 省事但小；section 大但顯眼 |
| 回到原程式 | 跳回 OEP | 不回（純後門） | 不回=一跑就露餡，正常都要回 OEP |
| payload 執行 | 同步（主執行緒） | 丟新執行緒 | 同步會卡住原程式；新執行緒較隱蔽 |
| 可執行權限 | 放 .text（已 X） | 放資料區+改權限 | .text 省事；改權限留下異常 section |

## 踩雷集錦

1. **「改了 entry point 卻忘了跳回 OEP」**：錯誤直覺是「跑完我的碼就好」。正確：不跳回 OEP，原程式不執行、一啟動就異常退出——這是最容易被發現也最沒用的後門。跳回 OEP 是這技術的核心，不是收尾。
2. **「不存/還原暫存器就接回原程式」**：你的 shellcode 污染了暫存器與旗標，原程式接手時 CPU 狀態是亂的，通常立刻崩。`pushad/pushfd` 進、`popfd/popad` 出，成對不可省。
3. **「把 shellcode 塞進 .data 卻忘了它不可執行」**：DEP 下跳過去執行=存取違規。要嘛放可執行的 section，要嘛把目標 section 加 `IMAGE_SCN_MEM_EXECUTE`。
4. **「硬在檔案中間 insert bytes」**：插入會位移後面所有資料，RVA/offset 全錯，檔案報廢。要用「既有空洞（cave）」或「尾端新增 section」，不要中間插入。
5. **「cave 不夠大還硬塞」**：cave 常只有幾十~幾百 bytes。大 payload 塞不下卻硬寫，會覆蓋到後面的真實資料。先量 cave 大小，不夠就串多個 cave 或改用 egghunter（小 stub 找大 shellcode，Ch 20）。

## 進階：再往深一層

- **TLS callback 落點**：PE 的 TLS callback 會在 entry point **之前**執行。把後門掛在 TLS callback 比改 entry point 更隱蔽，也是許多反調試/惡意軟體的招——藍隊偵測時要連 TLS directory 一起看。深入見 `windows_pwn`。
- **偵測面（防禦視角，重要）**：理解了植入，就能反過來偵測。常見偵測訊號：entry point 落在非典型 section（不在 `.text` 標準範圍）、section 權限異常（可寫又可執行 WX）、檔案尾端多出高熵 section、`AddressOfEntryPoint` 與編譯器慣例不符、PE 檢核碼不對。寫 YARA 規則與做完整性驗證時，這些就是特徵。本 repo 的 [`blue_team_dfir`](../blue_team_dfir/README.md) 從防守端展開這條線。
- **AV/EDR 為何會抓到（概念層）**：防毒靠簽章（已知 shellcode/stub 的位元組樣式）、啟發式（entry 指向怪位置、WX section、高熵）、行為（啟動就連外）。這解釋了為什麼攻擊方會對 payload 編碼/加密（Ch 26 的 encoder）來改變位元組樣式——但本課談的是「為什麼會被抓、原理是什麼」，不是針對特定防毒產品的規避食譜。理解原理同時服務攻與防兩端。

## 本章重點整理

- PE 後門=讓程式先跑一段你的碼、再乾淨跳回 OEP，使用者無感。
- 放 shellcode 兩條路：code cave（對齊空洞，省事但小）或新增 section（大但顯眼）。不可在檔案中間 insert。
- 劫持 entry point=改 `AddressOfEntryPoint` 指向 cave，並**先抄下原 OEP**。
- 執行流還原=`pushad/pushfd` 進、`popfd/popad` 出、跳回 OEP；缺了原程式會崩。
- cave 若不可執行要加 `IMAGE_SCN_MEM_EXECUTE`（0x20000000）。
- 看懂植入=看得懂偵測：entry 落點、WX section、高熵、檢核碼都是防守特徵。

## 自我檢核

- [ ] 不看筆記，能畫出「劫持 entry → stub → 跳回 OEP」的執行流
- [ ] 能解釋為什麼「跳回 OEP」和「存還原暫存器」兩步缺一不可
- [ ] 能說出 code cave 和新增 section 各自的取捨
- [ ] 知道 shellcode 放在不可執行 section 會發生什麼、怎麼解
- [ ] 能從防守端說出至少三個「這支 PE 可能被植入後門」的偵測訊號

## 延伸閱讀

- **[PE Format — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：Optional Header（`AddressOfEntryPoint`）、Section Table（`Characteristics` 旗標）。
  - **學什麼**：本章改的每個欄位的權威定義與旗標常數。
  - **前提**：先讀過本課 Ch 6。

- **[pefile 官方文件 / GitHub](https://github.com/erocarrera/pefile)**
  - **讀哪裡**：修改與重建 PE（`set_bytes_at_offset`、調整 section、`write()`）的 API。
  - **學什麼**：Ch 38 用 Python 操作 PE 的基礎。
  - **前提**：基本 Python。

- **[`blue_team_dfir` — 惡意程式鑑識 / 持久化偵測章節](../blue_team_dfir/README.md)**
  - **讀哪裡**：PE 異常、entry point 劫持、持久化偵測相關章節。
  - **和本章的關聯**：本章是攻擊面原理，那門課是對應的偵測與鑑識——兩面一起看才完整。

- **OffSec EXP-301「Trojanizing PE Files」模組**
  - **讀哪裡**：官方教材對應模組。
  - **學什麼**：考試對這主題的範圍界定與要求；本章是其原理版補充。
  - **注意**：以你手上的官方課程版本為準。

原理清楚後，下一章把它落到 `pefile` 的實作上——在你自己的載體程式上，把 code cave、entry 劫持、流程還原真正做出來並在隔離環境驗證。

→ [Ch 38 手工 trojanize 合法 PE：插入 shellcode + 還原執行流](./38-trojanizing-pe.md)
