# Ch 1 — OSED 是什麼：EXP-301 考綱與考試格式拆解

> **目標**：搞懂 OSED（OffSec Exploit Developer）/ EXP-301 到底考什麼、考試長什麼形狀、和 OSCP/OSEP/OSEE 的關係是什麼。這章不教任何技法，純粹建立「我在為什麼而練」的全局地圖——沒有這張地圖，後面 40 章的每一個細節都容易變成無方向的技巧堆積。

## 為什麼要先搞清楚考試的形狀

Linux CTF pwn 的世界裡，「考試」通常就是一題一題解，解出來拿 flag，沒有「報告」這個維度，也很少有「48 小時」這種跨夜的時間壓力設計。OSED 不是這樣——它的評分邏輯、時間結構、甚至允許你用什麼工具，都是刻意設計過的，目的是逼出「你真的會獨立做漏洞研究」而不是「你會抄 PoC」。

如果你不先搞懂這個形狀，很容易犯兩種錯：

- **練錯重心**：把時間砸在背誦某個特定 CTF 題的解法，而不是練「拿到一支陌生 binary 要怎麼下手」這個可遷移的流程。
- **低估報告的分量**：OSED 的及格門檻把「你能不能寫出別人看得懂、可重現的技術文件」算進去——這點很多 Linux pwn 背景的人會嚴重低估，因為 CTF 從來不要求你寫報告。

> **務必注意**：OffSec 的考試結構（時數配置、assignment 數量、報告評分細則）**歷史上調整過，未來也可能再調整**。下面描述的格式是本課寫作時（2026 下半年）業界普遍認知的樣貌，**報名前請務必以官方現行 Exam Guide 為準**——OffSec 的 PEN-200/EXP-301/EXP-312/EXP-401 Exam Guide 頁面是唯一權威來源，部落格/論壇的二手描述常常是舊版本。本章會盡量用「通常」「一般認知上」這類措辭，而不是把分數、題數寫成鐵律。

## EXP-301 的定位：OffSec 的「300 系列」是什麼

OffSec 把進階課程分成幾條線，EXP-301（對應證照 OSED）是「exploit development」這條線的入門與核心：

```
                         OffSec 證照地圖（示意，非官方圖）
                         ──────────────────────────
PEN-200 (OSCP)  ──┐
  滲透測試基礎      │
                   ├──► PEN-300 (OSEP)       進階滲透測試 / AV-EDR 迴避 / AD 攻擊鏈
                   │
                   ├──► EXP-301 (OSED)  ◄── 你在這裡
                   │      Windows x86 user-mode exploit dev
                   │      逆向找洞 → 控流 → 繞 DEP/ASLR → 手寫 shellcode
                   │
                   └──► EXP-312 (OSWE)      Web application exploit dev（白箱）

EXP-301 (OSED) ──► EXP-401 (OSEE)  Windows 核心 / 瀏覽器進階 exploit dev
```

幾個要點：

- **OSED 不是 OSCP 的「下一級」**，它是另一條垂直線——OSCP/OSEP 偏滲透測試方法論與橫向移動，OSED 偏純粹的二進位漏洞研究與 exploit 工程。兩條線的技能重疊其實不大：OSCP 教你怎麼打下一整個網域，OSED 教你怎麼讓一個會 crash 的程式變成會執行你程式碼的程式。
- **EXP-301 全程聚焦 Windows user-mode、x86（32-bit）**。這是刻意的教學選擇：32-bit 的記憶體佈局、呼叫慣例、緩解機制比 x64 簡單直觀，適合把「exploit dev 的第一原理」學透再往上疊。
- **OSEE（EXP-401）** 是這條線的終點，跳進核心 exploit dev 與瀏覽器 exploit dev，難度與投入量遠超 OSED，業界公認是 OffSec 證照裡數一數二硬的一張。

## 考綱全貌：官方學習模組對照本課地圖

EXP-301 的官方教材（課程代號仍常被稱為 WEB-301，歷史命名遺留，不影響內容）大致涵蓋以下模組。下表是本課寫作時對官方模組範圍的**理解與對照**，不是逐字照抄官方目錄——**實際模組清單與順序請以你購課時官方課綱頁為準**，可能隨改版增減。

| 官方模組（概括） | 核心內容 | 本課對應 Part / 章 |
|---|---|---|
| WinDbg 與 x86 組語基礎 | 偵錯器操作、暫存器、呼叫慣例、PE 概覽 | Part 0–1，Ch 3–6 |
| Stack-based buffer overflow | fuzz → crash → 控 EIP → badchar → shellcode | Part 2，Ch 7–10 |
| SEH-based buffer overflow | SEH chain 覆寫、POP POP RET、SafeSEH | Part 3，Ch 11–13 |
| IDA Pro 入門 | graph view、xref、反組譯閱讀 | Ch 14 |
| 從反組譯中逆向找漏洞 | 不靠文件、只靠反組譯找出 unsafe copy 等缺陷 | Part 4，Ch 15–18 |
| 空間限制與 Egghunter | shellcode 放不下時的技法 | Part 5，Ch 19–21 |
| 自訂 shellcode 製作 | PEB walk、export table、手寫 WinExec/reverse shell、encoder | Part 6，Ch 22–26 |
| 為漏洞研究做逆向工程 | 把 Part 4 的找洞能力用到更複雜目標 | 貫穿 Part 4、Ch 18 案例 |
| DEP 繞過 | ROP chain、VirtualProtect/VirtualAlloc | Part 7，Ch 27–30 |
| ASLR 繞過 | 非隨機化模組、info leak | Part 8，Ch 31–33 |
| Format string 漏洞 I/II | `%n` 任意寫、Windows 上的差異 | Part 9，Ch 34–36 |
| 回頭討論：進階技巧與考試準備 | 混合情境、時間管理 | Part 11，Ch 39 |

另外有一個官方確實會在教材中出現、但並非每個版本都考的主題：

- **PE 檔案後門化（trojanizing a legitimate PE）**：插入 shellcode 到合法可執行檔並還原原本執行流。本課放在 Part 10（Ch 37–38），當作選讀加深——是否計入當前版本的實際考試範圍，**以你購課時的 Exam Guide 為準**。

> **一個重要認知糾正**：OSED 不是「教你用 mona 一鍵生成所有東西」。官方教材會教 mona 的用法，但**考試的核心評分點是你能不能逆向一支陌生程式、自己判斷漏洞型態、自己決定繞過策略**——工具只是加速你的手，不是取代你的判斷。這也是為什麼 Part 4（逆向找洞）在本課份量這麼重。

## 考試格式：48 小時實作 + 24 小時報告（概括性描述）

業界對 EXP-301 考試格式的一般認知大致如下，**細節請務必核對官方現行 Exam Guide**：

```
時間軸（概括，非官方逐字條款）
═══════════════════════════════════════════════════════
Day 1-2：實作考試窗口（通常合計 47.5 小時級別的量級）
  ├─ 數個 assignment，每個指向一支「客製的」陌生 Windows x86 程式
  ├─ 典型任務形狀：
  │    1) 逆向給定的二進位，找出記憶體損壞類漏洞
  │    2) 建立對漏洞的完整控制（控 EIP、處理 badchar）
  │    3) 繞過該環境啟用的緩解機制（通常是 DEP，有時含 ASLR）
  │    4) 交付：能在目標上取得程式碼執行（常見驗證方式是落地一個標記檔或取得 shell）
  ├─ 每個 assignment 通常有「主線」與「加分/進階」層次之分
  └─ 作答過程要求截圖 / 證據留存，供報告引用

Day 3：報告撰寫窗口（通常額外約 24 小時）
  ├─ 寫技術報告：逆向過程、漏洞原理、exploit 腳本、復現步驟
  ├─ 及格門檻通常是總分達到一個百分比門檻（常見引用數字落在 70 分區間，
  │    例如業界常提到「72/100」這類具體數字，但官方可能逐年微調）
  └─ 報告品質本身計分，不是純粹的附件
═══════════════════════════════════════════════════════
```

幾個對 Linux pwn 背景的人特別關鍵、容易低估的性質：

1. **考試目標是「客製化」的程式，不是公開靶的變體**。你在 vulnserver 上練熟的指令名稱、固定 offset 不會直接搬過去——練的是方法論（fuzz 的習慣、逆向的切入點、分析思路），不是記答案。
2. **assignment 之間常有依賴或遞進關係**：前一題找到的技巧或 primitive，有時是下一題的基礎。時間分配策略（哪題先做、卡住多久要換題）本身就是考試的一部分，Ch 39 會專門講。
3. **報告不是形式**：寫得含糊、缺少可重現步驟、截圖對不上文字敘述，都會實際扣分。報告要求的嚴謹度接近真實的滲透測試/漏洞研究交付物。
4. **允許帶自己寫好的腳本/筆記進考場**（這是 OffSec 大多數考試的共通精神：考的是能力而非記憶力），但**具體允許的輔助材料範圍、是否允許特定第三方工具，一律以官方 Exam Guide 的規定為準**——不要憑印象假設。

> **未實測**：我沒有實際報考過 EXP-301，上述格式描述來自業界對 OffSec 考試結構的公開討論與官方宣傳頁的一般性描述整理，**不是逐字引用任何版本的 Exam Guide**。報名前請直接讀官方頁面確認：時數、assignment 數量、及格分數、允許工具與素材、報告格式要求。這些條款 OffSec 有紀錄地調整過，而且不同年份、不同考試版本可能不同。

## 和 OSCP、OSEP、OSEE 的關係與難度定位

| 證照 | 代號 | 核心技能 | 和 OSED 的技能重疊 | 難度感知（業界一般印象） |
|---|---|---|---|---|
| OSCP | PEN-200 | 滲透測試方法論、AD 攻擊、基礎 exploit 使用 | 低——OSCP 用別人寫好的 exploit，OSED 自己寫 | 入門門檻 |
| OSEP | PEN-300 | 進階滲透測試、AV/EDR 迴避、橫向移動 | 低——偏 TTP 與迴避技巧，不是記憶體層漏洞研究 | 中高（偏廣度與工程整合） |
| **OSED** | **EXP-301** | **Windows x86 user-mode exploit dev** | — | **中高（偏深度，技術門檻陡）** |
| OSEE | EXP-401 | Windows 核心 + 瀏覽器進階 exploit dev | 高——OSED 是地基，但難度跳級明顯 | 業界公認最硬的 OffSec 證照之一 |

一個常見的誤解要先打破：**OSCP 的經驗對 OSED 幾乎不直接遷移技術內容**，但它培養的「卡住不要慌、系統化排查」的考試心態非常有用——這也是本課把心態遷移放進 Ch 2 獨立一章的原因。真正直接遷移的是你的 **Linux userland pwn** 底子（ROP 思維、stack 佈局直覺、shellcode 概念），Ch 2 會詳細拆解哪些直接搬得動、哪些要整個重建。

## 學完站在哪

拿到 OSED 之後，你具備的是：

- **獨立逆向一支陌生 Windows x86 程式、判斷是否存在記憶體損壞類漏洞**的能力——不靠公開 CVE、不靠別人寫好的 PoC。
- **在 DEP + 基本 ASLR 情境下手工打造完整 exploit chain** 的能力：找 gadget、排 ROP、處理 badchar、落地 shellcode。
- **手寫不依賴 msfvenom 的 shellcode**：PEB walk 找 `kernel32` 基址、解析 export table、hash API resolution、自己 encode 消 badchar。
- **用漏洞研究者的語言寫技術報告**的基本功——這在紅隊、漏洞研究、exploit dev 職缺的面試與日常工作裡都直接用得到。

這張證照**不會**讓你立刻能打 Win11 現行的 CFG/CET/XFG 全緩解環境——那是本 repo `windows_pwn` 系列與 OSEE 的範疆。OSED 給的是地基：32-bit 世界裡「從逆向到控流到繞過到 shellcode」整條生產線的第一次完整閉環經驗，而且是**親手做**出來的閉環，不是看教學影片點頭的閉環。

## 本章重點整理

- EXP-301（OSED）是 OffSec「300 系列」裡專攻 **Windows x86 user-mode exploit development** 的一條線，和 OSCP/OSEP 是不同方向，和 OSEE 是同方向的進階延伸。
- 考綱覆蓋：WinDbg/x86 基礎、stack overflow、SEH overflow、IDA 逆向找洞、egghunter、自寫 shellcode、DEP/ASLR 繞過、format string、（選讀）PE 後門化。
- 考試一般認知是**實作窗口 + 獨立報告窗口**兩段式，且**報告本身計分**——這點對 Linux pwn 背景的人常被低估。
- **所有具體時數、分數、題數以官方現行 Exam Guide 為準**，本章只給方向性描述。
- 學完的產出：獨立逆向找洞 + 手工繞過 DEP/ASLR + 手寫 shellcode 的完整閉環能力，是紅隊/漏洞研究職缺的硬通貨。

## 自我檢核

- [ ] 能說出 OSED 和 OSCP/OSEP 的核心差異是什麼（方法論 vs 深度技術）
- [ ] 能列出至少 6 個 EXP-301 官方學習模組涵蓋的主題
- [ ] 能解釋為什麼報告在 OSED 考試裡不只是「附件」
- [ ] 知道考試具體時數/分數應該去哪裡查（官方 Exam Guide，不是部落格）
- [ ] 能說出 OSED 和 OSEE 的難度跳級大致在哪個維度（核心 + 瀏覽器 + 更複雜緩解）

## 延伸閱讀

- **[OffSec — Exploit Development with EXP-301 (OSED) 課程頁](https://www.offsec.com/courses/exp-301/)**
  - **讀哪裡**：課程概述與 Exam Guide 連結。
  - **學什麼**：唯一權威的考綱與考試格式來源，報名前必讀，且要讀「當下」版本。
  - **前提**：無，這是起點文件。

- **[OffSec — OSCP / PEN-200 課程頁](https://www.offsec.com/courses/pen-200/)**
  - **讀哪裡**：和 EXP-301 的課程定位對照段落（如果有）。
  - **學什麼**：確認 OSCP 與 OSED 的技能範疆差異，避免把兩條線的準備方式混在一起。

- **[Corelan — Exploit Writing Tutorials 系列總覽](https://www.corelan.be/index.php/articles/)** — Peter Van Eeckhoutte
  - **讀哪裡**：整個系列的目錄，先掃過標題建立技法地圖。
  - **學什麼**：OSED 技法的歷史源頭，能幫你理解「為什麼考綱長這樣」——這些教學系列基本就是 OSED 內容的前身。
  - **前提**：無。

- **本 repo [`windows_pwn/README.md`](../windows_pwn/README.md)**
  - **讀哪裡**：整體課程地圖與和本課的分工說明。
  - **學什麼**：確認「OSED 考場技法」和「windows_pwn 的 Win11 現代緩解」這兩門課的分工——不要把範疆搞混，浪費時間在考試不考的深度上。

搞懂了考試的形狀，下一章把焦點拉回你自己：你的 Linux pwn 底子裡，哪些東西能直接搬到 OSED 戰場，哪些要整個重建。

→ [Ch 2 從 Linux pwn 到 OSED：你會什麼、缺什麼](./02-linux-to-osed-mindset.md)
