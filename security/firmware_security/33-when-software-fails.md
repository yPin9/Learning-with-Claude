# Ch 33 — 軟體攻擊面關閉後，物理是下一步

> **目標**：建立 Part 6 的思維框架。理解威脅模型在「攻擊者實體持有裝置」情境下如何轉移、軟體防禦線全補後還剩哪些攻擊面、硬體攻擊的成本與技能光譜、以及判斷「什麼時候該伸手拿硬體工具」的決策框架。這章是第一個純框架章——不寫 exploit，寫攻擊路線圖。

---

## 先釐清威脅模型

前五個 Part 有個隱藏前提：攻擊者在**遠端**或至多在**作業系統層**持有裝置。軟體漏洞鏈成立的基礎是：

```
遠端攻擊者
    │
    ▼
作業系統層漏洞（RCE、LPE）
    │
    ▼
從 OS 打回韌體（runtime interface、capsule update、SPI 寫入）
    │
    ▼
Ring -2/-3 持久化（bootkit、SMM implant）
```

這條路不需要實體接觸，可以從地球另一端打。正因為如此，現代安全設計的主要目標就是把這條路的每一步都擋住。

**Part 6 改變這個假設**：攻擊者在你身邊。他拿著你的裝置，有工具台、有焊台、有 15 分鐘或 15 小時。這個變換讓整個攻擊面重新計算。

---

## 什麼叫「軟體攻擊面關閉」？

一台真正做好軟體安全的裝置，這些路你都走不了：

| 防禦項目 | 擋住什麼 |
|---------|---------|
| BootGuard ACM（x86）/ fuse 燒閉（ARM） | 修改 UEFI/BROM 本身 |
| SMRR + D_LCK + SMI 鎖閉 | SMM callout / SMRAM 竄改 |
| Secure Boot + dbx 更新 + SBAT | 已知 bootloader 漏洞繞過 |
| BIOS CNTL.BIOSWE=0 + PRx 保護 | SPI flash 軟體寫入 |
| JTAG/SWD fuse 燒閉 | 除錯介面直接存取 |
| UART console 量產停用 | bootloader 互動介面 |
| EDL/BL mode 鎖閉或加 auth | 下載模式直接刷寫 |
| BitLocker/LUKS 綁 TPM PCR | 離線讀取儲存內容 |
| Measured Boot + 遠端證明 | 隱性設定竄改 |

把上面這張表的每格都打勾，軟體攻擊面就接近關閉了。

現實世界極少有裝置全部打勾，通常有四到五格漏掉就會讓 Part 1–5 的某條路開通。但我們假設最壞情況：**全都打勾**。這時候還剩什麼？

---

## 剩下的攻擊面

```
軟體全封鎖之後的剩餘攻擊面：

  ┌─────────────────────────────────────────────┐
  │                 剩餘攻擊面                   │
  ├─────────────┬───────────────┬───────────────┤
  │  儲存介質   │   執行邏輯    │   通訊介面    │
  │             │               │               │
  │ SPI flash   │ RSA 驗簽計算  │ JTAG 引腳     │
  │（直接焊接   │ 條件跳轉指令  │（即使 fuse 關 │
  │  讀/寫）    │（電壓故障注入）│ 也可以直探    │
  │             │               │ silicon 層）  │
  │ eMMC 焊接   │ 金鑰載入      │               │
  │ 讀取（BGA   │（Cold Boot    │ I2C/SPI 匯流  │
  │ 重焊）      │ 搶在清除前）  │ 排竊聽 TPM    │
  │             │               │ 通訊          │
  │ DRAM 內容   │               │               │
  │（Cold Boot  │               │ USB power 線  │
  │ 攻擊）      │               │（voltage      │
  │             │               │ glitch 入口） │
  └─────────────┴───────────────┴───────────────┘
```

三個維度，每個維度都需要不同的硬體工具。把這三條路完全封閉，成本會讓很多廠商望而卻步——這也是為什麼即使是高安全性裝置，硬體攻擊面往往是最後一道但不是零的防線。

---

## 硬體攻擊的成本與技能光譜

攻擊者拿到裝置後，會先做成本評估。硬體攻擊不是一個點，是一個從 $20 到 $30,000 的連續光譜：

```
成本  工具                      技能要求           典型攻擊
─────────────────────────────────────────────────────────────
$20   CH341A + SOP8 夾          焊接入門           SPI flash dump/寫入
$50   USB-UART 轉接 + 邏輯分析  電子初學           UART console / I2C 竊聽
$300  Raspberry Pi + OpenOCD    Linux 基礎          JTAG/SWD 初步（fuse 未燒）
$500  ChipWhisperer Nano        Python 基礎         時鐘/電壓 glitch（入門）
$1k   ChipWhisperer Pro         訊號分析            參數掃描 / 逐位元 glitch
$3k   Riscure Inspector         RF/EM 基礎          EMFI（電磁故障注入）
$10k  雷射故障注入站             光電、IC 封裝知識   精準打到單一指令
$30k+ FIB / 電子顯微鏡           IC 工程              去蓋逆向、金屬層分析
```

**這個光譜意味著：**

- CH341A 那一端，任何有興趣的研究者週末就能上手。SPI dump 是最低技術門檻的硬體攻擊。
- ChipWhisperer 那一端，電子背景＋時間投資＋多次失敗才能穩定工作。不是「買了就會用」。
- 雷射故障注入那一端，通常是 IC 廠商內部、國家級實驗室、頂尖安全公司才有的設備和技能。
- 大多數實際研究案例落在 $20–$1000 這個範圍——因為有創意的軟硬體組合已經夠用了。

---

## 攻擊難度 vs 可達目標

不同成本對應不同的可達目標，這是攻擊者的「投資報酬率」分析：

| 成本層 | 可達目標 | 限制 |
|--------|---------|------|
| $20（CH341A） | SPI dump（讀取韌體）、SPI 寫入（若無 PRx 保護） | 需要裝置開蓋、SPI 引腳可觸及 |
| $50（UART）  | Bootloader console（若未停用）、log 分析 | 只有 UART fuse 未燒才有效 |
| $300（JTAG） | 完整 CPU 調試（若 JTAG fuse 未燒） | fuse 燒閉後，JTAG 無效；需找測試點 |
| $500（CW Nano） | 簡單 MCU glitch（電壓/時鐘）、入門 BootROM 繞過嘗試 | 成功率低、參數不穩定 |
| $1k（CW Pro） | 目標型 glitch（RSA 跳過、Secure Boot 繞過）、攻擊特定 SoC | 仍然需要逆向 + 參數分析 |
| $3k（EMFI）  | 不需要電源連接的 glitch、可攻擊部分已燒 JTAG 的晶片 | 精度比雷射低、需要探頭定位 |
| $10k（雷射） | 精確到單一指令的故障注入、繞過任何條件跳轉 | 需要 decap（去晶片外殼）、設備昂貴 |

關鍵觀察：**fuse 燒閉和 SPI 保護，把大量廉價攻擊擋住了，但對 $1k 以上的攻擊效果有限**。BootGuard 等信任根設計在電氣層面不設防。

---

## 什麼時候該伸手拿硬體工具？

這個問題的答案不是「能軟解就不用硬」，而是**系統性判斷**。

### 判斷流程：軟體 Dead End 的確認

```
開始研究新目標
       │
       ▼
① 確認 fuse 狀態（SBC_EN/BootGuard）
       │
   fuse 未燒──→ 軟體路徑（Ch 21 T1）
       │
   fuse 已燒
       │
       ▼
② 確認 Debug Port 狀態（JTAG/UART）
       │
   Debug 開著──→ 軟體路徑（Ch 21 T2）
       │
   Debug 關閉
       │
       ▼
③ 確認 Download/Recovery mode 安全性
       │
   有漏洞──→ 軟體路徑（Ch 21 T5）
       │
   全部鎖閉
       │
       ▼
④ 分析韌體（若能 dump）
       │
   有邏輯漏洞──→ 軟體路徑（Ch 21 T3/T4/T6）
       │
   無法 dump 或韌體無漏洞
       │
       ▼
⑤ 到達軟體 Dead End
   → 問：目標價值是否值得硬體投入？
   → 評估：什麼硬體攻擊可行（SPI/glitch/cold boot）？
   → 決定：選哪個成本層？
```

### 硬體攻擊的前置條件

實體持有裝置不等於硬體攻擊就可行。還需要確認：

1. **可以開蓋嗎？** 部分裝置用防拆螺絲或導電膠，拆開會觸發 tamper detection（清除 key material）。
2. **目標晶片可觸及嗎？** BGA 封裝的 SoC 引腳全在底部，找測試點的難度大幅增加。
3. **有沒有 tamper detection 感測器？** 某些 HSM/智慧卡級別裝置有環境感測（光、溫度、電壓偏移），一旦偵測到就自毀。
4. **時間限制是什麼？** Evil maid 場景通常 15–30 分鐘。Supply chain interdiction 可以事先準備。

---

## 硬體攻擊 vs 軟體攻擊：完整取捨表

| 維度 | 軟體攻擊 | 硬體攻擊 |
|------|---------|---------|
| **前提** | 通訊管道（網路/USB/串口）或 OS 層存取 | 實體持有裝置 |
| **成本** | 通常低（工具免費/廉價） | $20–$30,000 不等 |
| **可重複性** | 高（腳本化，一鍵攻擊） | 低–中（每次需要手動調參） |
| **可遠端化** | 是 | 否（除非 supply chain 預埋） |
| **防禦難度** | 軟體更新可修補（多數情況） | 通常無法 patch（硬體層問題） |
| **隱蔽性** | 視攻擊類型（bootkit 極隱蔽） | 開蓋/焊接留下物理痕跡 |
| **通用性** | 針對 CVE 的 exploit 通常限特定版本 | glitch 技術跨 SoC 泛用性較高 |
| **可拒絕性** | 遠端可抵賴 | 物理痕跡不可否認 |
| **致命攻擊** | bootkit、SMRAM 植入 | BootROM bypass、Cold boot key 提取 |
| **技能門檻** | 軟體安全研究者普遍熟悉 | 需要電子/硬體背景 |
| **法律風險** | 遠端高（未授權存取） | 實體竊取更明確的刑事 |

---

## 威脅場景：什麼人用硬體攻擊？

### Evil Maid Attack（惡意女傭）

攻擊者在目標離開裝置時（離開旅館房間、過機場安全門）進行攻擊，裝置在 15–30 分鐘後歸還，目標不知道被動了手腳。

**典型手法：**
- 插入預先準備好的 USB 裝置，搭配 UEFI 漏洞取得執行
- 用 CH341A 快速 dump SPI flash（約 3–5 分鐘）
- Cold boot attack——讓筆電在低溫下快速強制斷電，DRAM 上的金鑰暫時保留，用預備啟動媒體讀出

**為什麼全碟加密不夠用：** BitLocker 預設依賴 TPM 自動解鎖，正常開機不需要 PIN。Evil maid 插入修改過的 bootloader，在自動解鎖後、使用者登入前竊取 Volume Master Key（VMK）。要防這個，需要 BitLocker + 開機前 PIN + Secure Boot 鎖閉，三者缺一。

### Supply Chain Interdiction（供應鏈截擊）

裝置在從廠商到目標的途中被擷截，植入硬體/韌體後繼續送達，目標拿到的是「看起來全新」的裝置但內部已有 implant。

**典型手法：**
- 替換 SPI flash 晶片（焊接），裡面放有 SMM rootkit 的 UEFI image
- 在 PCB 上追加小型 implant 晶片（截聽鍵盤、網路流量）
- 修改韌體但保留外觀，外箱封條重新貼過

**現實案例（公開文件）：**

NSA ANT catalog（2013 年 Snowden 洩露）揭露 DEITYBOUNCE、SOUFFLETROUGH 等 BIOS/JTAG implant 的存在，供應鏈截擊作為國家級攻擊手段已被記錄在案。Bloomberg 2018 年報導的「Super Micro 間諜晶片」爭議（真實性存疑）揭示這個威脅的討論在業界持續升溫。

### Forensic / Intelligence Access

執法機關、情報機構持有目標裝置，需要提取加密儲存的內容。法庭上「不能解鎖」的抗辯有效性取決於硬體攻擊能不能繞過加密。

**典型手法：**
- Cellebrite/Graykey 等商業工具（利用 iOS/Android 漏洞，本質上是軟體攻擊，但附帶裝置）
- JTAG/Chip-off 讀取 NAND（從 PCB 摘下記憶體晶片，用專用讀取器讀原始資料）
- 向廠商要後門或 GK（Golden Key）——政策爭議，但技術層面存在可能性

---

## 硬體攻擊的「不可修補性」

軟體漏洞有 patch。硬體漏洞——尤其是 BootROM 層的——**無法更新**。

這是硬體攻擊的關鍵特性，也是為什麼 BootROM exploit 被研究者視為「聖杯」：

```
Fusée Gelée（Nintendo Switch Tegra X1，2018）：
  BootROM 的 USB 協定 length overflow
  → 所有 Tegra X1 裝置永久可 exploit
  → 唯一解法：換晶片（Mariko 修訂版 SoC）
  → 原版 Switch 至今仍可 exploit

Apple Secure Enclave BootROM（A12 之前）：
  checkm8（2019）利用 USB DFU exploit BootROM
  → iPhone 4s – iPhone X 永久可 jailbreak（彼時 iOS 版本無關）
  → Apple 在 A12 之後修復矽層，A11 及之前無法 patch
```

相比之下，軟體層的 Secure Boot 繞過（BlackLotus）可以透過 dbx 更新擋掉——代價是推送 patch 然後等全球裝置更新完畢的漫長過程。但至少能修。

BootROM exploit 一旦公開，裝置的安全保障就看**誰先知道**、目標裝置的**資料保護層**（全碟加密、TPM 綁定）是否獨立於 boot chain 之外。

---

## 本章在 Part 6 中的定位

```
Ch 33（本章）— 思維框架
    │
    ├── Ch 34 — 故障注入繞 secure boot（voltage/clock/EMFI/laser glitch）
    │
    ├── Ch 35 — SPI 竄改 TOCTOU 與 cold boot（直接存取儲存）
    │
    └── Ch 36 — debug 介面當攻擊原語（JTAG/SWD/UART，fuse 之前之後）
```

Part 6 的三章各佔一個維度：執行邏輯的硬體干擾（Ch 34）、儲存介質的直接存取（Ch 35）、通訊介面的直接存取（Ch 36）。本章給的框架解釋「為什麼」，後三章講「怎麼做」。

**重要聲明**：Part 6 所有章節——包括本章描述的硬體技術——均為**未實測**內容。作者沒有 CH341A、ChipWhisperer、邏輯分析儀等硬體工具。本課提供的是公開研究文獻的整理、攻擊原理的分析、參數範圍的參考，以及「在你有工具的情況下如何驗證」的步驟描述。誠實標記比假裝跑過有意義。

---

## 踩雷

1. **「軟體 Dead End」可能只是「你還沒找到漏洞」的 Dead End**：在決定轉向硬體攻擊之前，確認是真的 dead end 還是分析不足。硬體攻擊成本高，先確認真的沒有軟體路。

2. **開蓋不等於可以攻擊**：部分裝置（HSM、TPM 模組、某些手機）有 tamper detection，開蓋觸發後 key zeroization 讓你什麼都讀不到。事先研究裝置是否有 tamper-evident 或 tamper-resistant 設計。

3. **Evil Maid 的時間限制很嚴苛**：30 分鐘聽起來夠，但包含開蓋、找引腳、接線、讀取、驗證、復原外觀。實際演練這個流程，任何「沒預料到的困難」都是超時。

4. **Supply chain 攻擊需要準備期**：不是到手就能改，你需要事先分析目標裝置的韌體和 PCB 佈局，準備好替換的 SPI flash 晶片或 implant 板。這通常意味著需要預先取得一台相同型號的裝置做分析。

5. **「fuse 燒閉」不等於「電氣層不可觸及」**：fuse 燒閉擋的是韌體讀取 JTAG 狀態後停用接口的邏輯，但電氣引腳仍在。雷射故障注入可以繞過 fuse check 本身——只是成本到了 $10k 那個層次。

6. **法律與授權**：所有這些技術在**未授權**的情況下用於他人裝置都是違法的。研究者用自己購買的裝置、CTF 靶機、或有合法授權的設備。

---

## 進階延伸

- **Tamper-resistant 硬體設計**：Yubico、Ledger 等廠商發布的硬體安全設計文件，描述如何抵抗物理攻擊（mesh、環境感測、定時 zeroization）。對照攻擊者視角看防禦側的思考。

- **NSA ANT Catalog**（The Intercept，2013 年洩露）：30 多個硬體/韌體 implant 的技術規格摘要，對「supply chain implant 長什麼樣」有概念性了解。技術細節是 2008–2013 年代，今日同樣的原理仍適用於更先進的平台。

- **Fail0verflow 和 ReSwitched 的 Switch 研究**：checkm8 和 Fusée Gelée 兩個 BootROM exploit 的公開技術說明，是「硬體不可修補漏洞」最好的學習案例，有完整的 BootROM 分析流程記錄。

---

## 動手練習

### 練習：成本/風險/目標三角評估

對以下三個目標，分別做攻擊路線評估：說明你會先嘗試哪條軟體路徑、軟體 dead end 的判斷標準、以及如果決定走硬體路線你會選擇哪個成本層及原因。

**目標 A**：一台路由器，型號未知，UART 接上後有 bootlog 但無互動 prompt，Web 介面是 OpenWrt 衍生版，軟體漏洞研究未發現已知 CVE。

**目標 B**：一台 Android 手機，LOCKED bootloader，fastboot 無法 unlock，已知廠商不接受 bug bounty，想提取裡面的對話記錄。

**目標 C**：一台工業 PLC，ARM 核心，無網路服務，只有 RS-485 通訊，有簽章驗證韌體更新，JTAG fuse 狀態未知。

思路提示：
- A：先確認 UART 是否只是 rate mismatch（多試幾個 baud rate）；看 bootlog 是否有 Secure Boot 相關 print；dump SPI 看韌體內容（需 CH341A）→ 分析韌體有無 T3/T4/T6；如果韌體加密讀不出來，考慮 glitch attack 繞驗簽。
- B：評估廠商 SoC 是否有已知 BootROM exploit（查 checkra1n/Fusée Gelée 的 Tegra/Apple 清單）；EDL 模式有無 auth 繞過；如無軟體路，cold boot 或 chip-off 是唯一選項但成本高且破壞性大。
- C：先用邏輯分析儀掃常見 debug 介面（UART/SWD 測試點）；JTAG fuse 未知→先接 OpenOCD 試；如果 JTAG 無效考慮 EMFI 繞 fuse 讀邏輯；RS-485 協定本身是否有注入機會（軟體路徑）。

---

## 本章重點

- 威脅模型從「遠端攻擊者」轉移到「實體持有攻擊者」，整個攻擊面重算
- 軟體攻擊面全部封閉後，剩餘面向：SPI/eMMC 直接存取、執行邏輯干擾（glitch）、通訊介面竊聽
- 硬體攻擊成本光譜：$20 CH341A 到 $30k 雷射故障注入，技能門檻對應成本
- 判斷「何時轉向硬體工具」需要系統性確認軟體 dead end，而非「遇到困難就拿硬體」
- Evil maid 和 supply chain interdiction 是兩個主要的硬體攻擊威脅場景
- BootROM 層的漏洞具有**不可修補性**，是硬體攻擊的最高價值目標
- Part 6 全部章節均未實測（無對應硬體工具），誠實標記

---

## 自我檢核

- [ ] 能說出「軟體攻擊面關閉」需要哪 9 項防禦同時就位
- [ ] 能描述硬體攻擊成本光譜的四個層次（$20/$500/$3k/$10k）及對應工具
- [ ] 知道 Evil Maid Attack 的標準流程和 BitLocker 為何不夠用
- [ ] 能說明 Supply Chain Interdiction 需要哪些前置準備
- [ ] 理解 BootROM exploit 為何具有不可修補性，並能舉兩個例子
- [ ] 能用「軟體 Dead End 判斷流程」對一個新目標系統性地確認何時轉向硬體

---

## 延伸閱讀

1. **"Evil Maid Attacks" — Joanna Rutkowska（2009）及後續更新版本**
   讀哪裡：invisiblethingslab.com 的原始 blog post，以及 ESET 2019 年針對現代系統的更新分析
   學什麼：Evil Maid 的最早系統性描述——為什麼全碟加密不夠、攻擊需要哪些條件、TPM + PIN 如何應對
   關聯：直接對應本章 Evil Maid 威脅場景，建立對「攻擊者有實體存取、時間有限」這個假設的直覺

2. **"NSA ANT Catalog" — Der Spiegel / The Intercept（2013–2014）**
   讀哪裡：The Intercept 的整理頁面（theintercept.com/2015/01/15/ant-catalog/），含原始文件 PDF
   學什麼：國家級 supply chain implant 的技術種類（BIOS implant/JTAG backdoor/硬體 tap），「現實中的 supply chain 攻擊長什麼樣」最好的公開參考
   關聯：本章 Supply Chain Interdiction 威脅場景的真實世界依據，建立威脅的真實感而不是假設

3. **"Checkm8 — a permanent unpatchable bootrom exploit for hundreds of millions of iOS devices" — axi0mX（2019）**
   讀哪裡：GitHub 的發布公告（github.com/axi0mX/ipwndfu），以及 Siguza 等人的後續技術分析
   學什麼：BootROM USB DFU 介面的 use-after-free 漏洞如何導致不可修補的全機型 exploit，「硬體漏洞為何比軟體漏洞更致命」的最佳案例
   關聯：對應本章「BootROM 不可修補性」小節，接 Ch 34 fault injection 對 BootROM 的不同攻擊路徑

→ [下一章](./34-fault-injection-secure-boot.md)
