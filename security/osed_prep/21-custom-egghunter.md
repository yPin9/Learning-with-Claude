# Ch 21 — 客製 egghunter 與受限字元調校

> **目標**：把 Ch 20 的 egghunter 理論骨架調成能真正塞進 exploit 的版本——處理 egghunter 本身的壞字元、挑一個安全的 egg 標記、決定 shellcode 放哪、處理 Unicode 轉寬字元的環境，並建立它失敗時怎麼用 WinDbg 單步除錯的習慣。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼需要這一章

Ch 20 講完的 egghunter 機制是**通用骨架**：syscall 判斷頁面可讀性、兩層迴圈掃描、比對 8-byte egg。但那段機械碼能不能真正塞進某一個特定目標，取決於一堆這個骨架沒處理到的現場細節——egghunter 本身含不含壞字元、egg 標記選得好不好、shellcode 擺在哪個欄位、目標程式會不會把你的輸入轉成寬字元。這一章處理的是「從能運作的理論，到能用在這次考試題目上的實作」這段落差。

## 先建立直覺

```
調校 egghunter 要過的四道關卡：

┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
│ ① egghunter│──►│ ② egg 標記  │──►│ ③ 放置策略  │──►│ ④ 編碼環境  │
│ 本身的壞字元│   │ 本身安不安全│   │ shellcode │   │ (Unicode/  │
│（換等價指令）│   │（不撞資料、 │   │ 擺哪？egg  │   │  寬字元)   │
│            │   │ 不含壞字元）│   │ 要在它前面│   │            │
└───────────┘   └───────────┘   └───────────┘   └───────────┘
      │                                                │
      └──────────────── 任何一關沒過都前功盡棄 ──────────┘
```

四道關卡環環相扣：egghunter 編碼過了壞字元、egg 標記也挑對了，但如果 shellcode 放置的欄位本身會被目標程式轉成 Unicode 寬字元存儲，egghunter 照 8-bit 模式去掃一樣會找不到——這就是為什麼本章要把四件事放在一起講。

## 核心：四道關卡逐一拆解

### ① egghunter 本身的壞字元

Ch 8 教過的壞字元分析（bad char analysis）同樣適用在 egghunter 身上——它也是一段機械碼，一樣可能含 `\x00`、`\x0a`、`\x0d` 之類常見壞字元，或目標協定自己額外忌諱的字元。差別是 egghunter 的指令數極少（約 32 bytes），能調整的空間也小，常見手法：

- **換等價指令**：例如某條指令編出來含壞字元，換一條做一樣事、編碼不同的指令。像用 `inc`/`dec` 組合取代某個會產生壞字元常數的 `add`/`sub`，或換一種暫存器配置讓 ModRM byte 避開問題。
- **調整立即值的位元組排列**：syscall number、頁面大小常數（`0xfff`）這類硬編碼值，如果恰好某個 byte 落在壞字元清單裡，可以用算術技巧湊出同樣效果的另一個數值組合（例如先載入一個不含壞字元的數，再用一條不含壞字元的運算指令調整到目標值）。
- **換用另一個 syscall 版本**：如果 `NtAccessCheckAndAuditAlarm` 版本的機械碼裡有個壞字元躲不掉，直接換成 `NtDisplayString` 版本試試——兩者的 syscall number 與參數佈局不同，編碼出來的 bytes 也完全不同，很多時候換一個版本就迴避掉了，比硬調同一版本容易。

mona 在這塊的實務用法：先用 `!py mona egghunter -eggtag <tag>` 產出一版，把輸出的機械碼丟進 Ch 8 教的壞字元比對流程，找到問題 byte 後回頭對照組語，定位是哪條指令、換掉它、重新組譯，再測一輪。

### ② egg 標記怎麼選

8-byte egg（4-byte tag 重複兩次）的選擇要同時滿足三個條件：

- **本身不含壞字元**：既然 egghunter 要在記憶體裡用純字元比對找它，egg 本身也要先過壞字元分析。
- **不會跟正常協定流量/記憶體內容衝突**：挑一個幾乎不可能在目標程式正常運作時自然出現的 4-byte 序列。預設值 `w00t` 之所以流行，就是因為它是個不常見的字串，一般二進位資料或正常封包內容幾乎不會剛好湊出這個序列。如果目標協定本身大量出現某些固定模式（比如都是全 0 填充、或協定關鍵字恰好撞到你挑的 tag），換一個更冷門的組合。
- **是你自己能完整掌控的常數**：挑好之後，shellcode payload 前面貼的 egg 和 egghunter 裡比對用的 egg 必須是同一個值——這聽起來顯而易見，但手動調整機械碼時改錯一邊是常見的筆誤來源。

mona 產生 egg 的指令：

```
!py mona egg -t w00t
```

會印出完整 8-byte egg 的實際內容，供你核對 payload 組裝時是否一致。

### ③ 放置策略：真 shellcode 放哪，egg 要貼在它前面

延續 Ch 19 的討論，egghunter 要掃到的大 shellcode 通常放在：

- **另一個先送的封包/欄位**，內容原封不動留在行程記憶體裡（Ch 19 的方案 (a) 使用的那塊記憶體，現在不需要知道它的確切位址，只要知道它存在）。
- **同一個溢位 payload 裡，egghunter 之外的部分**——如果 buffer 本身夠大，只是「控制流到達的那個點」附近空間小，可以把 egghunter 放在控制流落點，完整 shellcode（前面貼 egg）放在同一個 buffer 的別處（比如最前面），egghunter 掃描照樣能找到。

**egg 必須貼在 shellcode 正前方，緊鄰**，這樣 egghunter 一比對成功、`jmp edi`（或等價跳轉）落下去的位置，正好是 shellcode 的第一個 byte，不需要再額外計算偏移。如果 egg 和 shellcode 之間留了一段其他資料，egghunter 跳過去執行的會是那段雜訊，不是 shellcode。

> **常見誤區**：只放一份 egg。多數教學和 mona 預設版本只找「一個」8-byte egg 然後直接跳，前提是你確定 shellcode 只出現一次、位置唯一。如果同一個 payload 裡可能有多處相似資料造成誤判風險，可以考慮在 egg 前再加一層校驗（比如比對更長的標記），但這會讓 egghunter 變大，要重新考慮空間預算。

### ④ Unicode 環境下的 egghunter

有些目標程式會把輸入從單位元組字串（ANSI）轉成**寬字元（wide char / UTF-16LE）**再存進記憶體——常見於走 `MultiByteToWideChar` 或 Unicode 版 Win32 API 的程式路徑。轉換後，你送進去的每個 byte 會變成「該 byte + `0x00`」交錯排列，這會直接打壞：

- **shellcode 本身**：純 8-bit 機械碼在寬字元化之後，指令序列全部被插入了 `0x00`，不再是合法可執行的指令流——這通常要靠 "venetian blind"（百葉窗）風格的 shellcode 寫法或 alpha-numeric/Unicode-safe encoder 處理，超出本章範圍，留給 Ch 26（自寫 encoder/decoder）細講。
- **egghunter 的比對邏輯**：如果你的 egg 和 shellcode 都被寬字元化，egghunter 原本以 4-byte 為單位的比對（`scasd`）會比對不到——因為記憶體裡實際排列已經變成每個字元中間都插了一個 `0x00`。

對策通常是：egghunter 自己盡量不受 Unicode 轉換影響（因為它常被放在一個不經過轉換的欄位，比如直接溢位到的 return address 後面），但它要找的 egg + shellcode，如果是透過一個會被轉寬字元的輸入路徑送進去，就要相應調整 egg 比對寬度（例如改成比對經過寬字元化後的樣式）或換一個不會被轉換的欄位放置它們。**先用 WinDbg 看一眼實際送進去的資料在記憶體裡長什麼樣**（有沒有被插入 `0x00`），比憑經驗猜測可靠。

## 底層對比：egghunter 變體一覽

| 變體 | 核心機制 | 典型使用情境 | 相對體積 |
|---|---|---|---|
| `NtAccessCheckAndAuditAlarm` 版 | syscall 回傳 `0xC0000005` 判斷可讀性 | 預設首選，mona 直出 | 基準（約 32 bytes） |
| `NtDisplayString` 版 | 同機制、不同 syscall | ① 版機械碼踩到壞字元時的替代 | 與①相近，略有差異 |
| SEH-based 版 | 自裝 exception handler 接住違規 | 兩種 syscall 法都踩雷、或目標環境對 `Nt*` 呼叫有額外限制時 | 明顯更大，邏輯更複雜 |
| Unicode-safe 版 | 比對寬字元化後的樣式 | 輸入會被轉成 UTF-16LE 存儲的目標 | 依比對寬度調整，通常略大 |

## 速度與可靠性取捨

egghunter 越大（例如加了額外校驗、換成 SEH-based 版本），可靠性可能提升（避開特定壞字元、減少誤判），但：

- **佔用空間變大**，可能又撞回 Ch 19 的空間限制問題——選 egghunter 本來是為了解決空間不夠，不能選到一個比可用空間還大的變體。
- **掃描速度可能變慢**（更多指令、每輪迴圈開銷更高），在考場的有限時間裡，每次重測的等待時間都是成本。

實務上的取捨順序通常是：先試最小的 `NtAccessCheckAndAuditAlarm` 版本，踩到壞字元才換 `NtDisplayString`，兩者都不行才上 SEH-based，而不是一開始就選最保險但最肥大的版本。

## egghunter 失敗時怎麼除錯

延續 Ch 20「驗證方法」的單步流程，這裡列出幾種典型失敗現象和對應的排查方向：

- **完全沒反應，服務看起來卡住**：很可能卡在掃描迴圈裡，一直找不到 egg。先確認 shellcode payload 真的有送進行程記憶體（在 WinDbg 裡用 `s`（search）指令搜尋 egg 的 8 bytes，確認它確實存在於某個已知可讀位置）。如果搜不到，問題在 payload 組裝或協定處理邏輯，不在 egghunter 本身。
- **服務直接崩潰，且崩潰點在 egghunter 裡**：代表 syscall 判斷可讀性那段沒生效——常見原因是 syscall number 錯（對應 Windows 版本不對）、或某個暫存器被你移植機械碼時改壞了。單步比對每條指令執行後的暫存器狀態，跟 mona 原始輸出對照。
- **egghunter 跑完整個掃描卻跳到錯誤的位置**：通常是 egg 比對邏輯的位移算錯（`scasd` 之後 `EDI` 的位置，或 `jmp` 用的暫存器搞混），或者是 payload 裡 egg 和 shellcode 之間多塞了不該有的 bytes。
- **在 Unicode 環境下，egg 看起來存在但 egghunter 找不到**：用 `db`（display bytes）實際看一眼那段記憶體，確認是不是被插入了 `0x00`——這會直接說明是④的問題。

## 踩雷

1. **改了 egghunter 裡的 egg 常數，卻忘了同步改 payload 裡的 egg**：兩邊必須是同一個 8-byte 值，調壞字元時最容易漏改一邊。
2. **選的 egg tag 和正常協定資料撞上**：導致 egghunter 在錯誤位置就提前判定找到，跳到一堆雜訊去執行。選 tag 前先看一眼目標協定的正常封包內容。
3. **換了 syscall 版本卻沒有重新做壞字元分析**：`NtDisplayString` 版本的機械碼跟 `NtAccessCheckAndAuditAlarm` 完全不同，不能假設前者沒踩雷的字元在後者也一樣安全。
4. **沒意識到目標會做 Unicode 轉換**：對著一個會把輸入轉寬字元的欄位硬塞 8-bit egghunter，百分之百找不到東西，卻可能誤判成「位址算錯」而在錯的方向排查老半天。
5. **為了保險直接上 SEH-based 版本，結果占用空間超過可控預算**：回頭看 Ch 19 的空間限制——選 egghunter 本身也要考慮它自己有多大，不是無條件的萬能解。

## 進階：再往深一層

考試現場時間有限,調校 egghunter 最省時間的方式往往是「準備好幾個候選版本（兩種 syscall、必要時 SEH-based）+ 一套標準化的壞字元測試流程」,出現壞字元衝突時能快速切換版本而不是從頭手改機械碼。另外,egghunter 的概念不是 OSED 專屬——同樣「在不確定記憶體佈局的情況下安全探測」的思路,在堆疊風水未知的現代利用技巧(heap grooming 後的盲目搜尋)裡也能看到類似的影子,值得之後接觸更進階漏洞研究時留意這個模式的延伸。

## 本章重點整理

- 客製 egghunter 要過四關:egghunter 本身壞字元、egg 標記安全性、shellcode 放置策略(egg 必須貼在 shellcode 正前方)、Unicode 轉寬字元環境。
- 踩到壞字元優先換 syscall 版本(`NtAccessCheckAndAuditAlarm` ↔ `NtDisplayString`),比硬調同一版本機械碼省力。
- egg 標記要同時滿足:不含壞字元、不撞正常資料、兩端(egghunter 裡與 payload 裡)完全一致。
- 更大更保險的變體(SEH-based)會占用更多空間,要回頭核對 Ch 19 的空間預算。
- 失敗排查的第一步永遠是 WinDbg 單步 + 搜尋記憶體確認資料真的在那裡,不要憑理論猜測。

## 自我檢核

- [ ] 能說出 egghunter 遇到壞字元時,換 syscall 版本和調整單條指令兩種對策的取捨
- [ ] 能說出 egg 標記選擇要滿足的三個條件
- [ ] 能解釋為什麼 egg 必須緊貼在 shellcode 正前方
- [ ] 能說出 Unicode 轉寬字元會如何打壞 egghunter 的比對邏輯
- [ ] 能列出至少三種 egghunter 失敗的典型現象與對應排查方向
- [ ] 能說出為什麼 SEH-based 版本通常是最後選項而不是預設首選

## 延伸閱讀

- **[Corelan — Exploit Writing Tutorial part 8: Win32 Egg Hunting](https://www.corelan.be/index.php/2010/01/09/exploit-writing-tutorials-part-8-win32-egg-hunting/)** — Peter Van Eeckhoutte
  - **讀哪裡**：文中關於挑選 egg tag、以及多種 egghunter 變體優缺點比較的段落。
  - **學什麼**：本章對比表格與 egg 挑選策略的直接依據。
  - **前提**：先讀完 Ch 20。
- **skape — "Safely Searching Process Virtual Address Space"（2004）**
  - **讀哪裡**：討論不同探測手法適用情境與限制的段落。
  - **學什麼**：為什麼會有多種 egghunter 變體存在,各自解決哪種限制。
  - **前提**：已讀過 Ch 20 的機制介紹。
- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：egghunter 搭配 WinDbg 單步除錯的實作文章。
  - **學什麼**：本章「失敗時怎麼除錯」段落的實務延伸,看真實單步輸出長什麼樣。
  - **前提**：熟悉 WinDbg 單步操作（Ch 4–5）。
- **[corelan/mona（GitHub）](https://github.com/corelan/mona)** — Corelan
  - **讀哪裡**：`egg`/`egghunter` 指令支援的參數清單（tag 選擇、輸出格式）。
  - **學什麼**：mona 實際能幫你自動化到什麼程度，哪些部分仍需手動調整。
  - **前提**：環境已裝好 mona v3（Ch 0）。

Egghunter 的實戰調校講完了，Part 5 到此結束。下一章轉入全書另一個重頭戲——為什麼 OSED 要求你手寫 shellcode，而不是全程依賴 msfvenom，這會決定接下來 Part 6 每一章的切入角度。

→ [Ch 22 為什麼手寫 shellcode（vs msfvenom）](./22-why-write-shellcode.md)
