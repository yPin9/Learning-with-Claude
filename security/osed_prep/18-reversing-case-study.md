# Ch 18 — 逆向實戰：從未知二進位找出可利用 overflow

> **目標**：把 Ch 14-17 串成一條完整生產線,從零逆向一支虛構但合理的 32-bit Windows 網路服務——recon → 逆協定 → 找 unsafe copy → 靜態推 offset → WinDbg 驗證控 EIP。看完這一章,你應該能複述整條思路,並在 Practice C 自己獨立走一次。

> **環境**：Win10 x64 + IDA Free 9.x/IDA Pro + WinDbg（2026）。本章目標程式為**教學示意**（虛構但合理的服務,命名為 `svctest.exe`），所有反組譯片段與位址標**教學示意,非真實逆向輸出**；流程本身依循前四章教過的真實方法。

## 為什麼需要這一章

前四章分別教了「怎麼用 IDA」「怎麼找輸入點」「怎麼認出 bug」「怎麼動態驗證」。但考場不會把題目切成四個分開的小題——你拿到的是**一整支陌生 binary**,四件事要連著做,而且往往不是一次就順利,中間會卡、會猜錯、要回頭修正。這一章的價值不在教新技術,是教**節奏**：遇到陌生目標,腦子裡的步驟應該怎麼排,卡住時怎麼退一步重新假設。

## 情境設定

你拿到一支 `svctest.exe`,只知道它是個 32-bit Windows 服務,監聽某個 TCP port。沒有原始碼,沒有文件。目標：找出至少一個可讓你控制 EIP 的漏洞。

## 第一步：靜態 recon（imports / strings / 找 recv）

打開 IDA,載入 `svctest.exe`,等自動分析跑完。先不急著看任何一個函式細節,照 Ch 14-15 教的,掃過三個地方建立全局印象：

**Imports window**：看到 `ws2_32.dll` 匯入了 `WSAStartup`、`socket`、`bind`、`listen`、`accept`、`recv`、`send`——確認這是個標準阻塞式 TCP 服務,骨架符合 Ch 15 的心智模型。

**Strings window**（`Shift+F12`）：掃過一遍,看到幾個可疑字串：`"LOGIN "`、`"FETCH "`、`"ADMIN "`、`"Unknown command\r\n"`。長度都不長、全大寫、後面帶空白——典型指令關鍵字的樣貌。先記下來,等等用 xref 驗證。

**找 recv**：Imports 裡對 `recv` 按 `X`,只有一處呼叫,在一個暫且命名為 `sub_401050` 的函式裡。雙擊跳進去。

## 第二步：逆出協定與指令

在 `sub_401050` 裡,看到 `recv` 把資料收進一塊 `sub esp, 0x400` 配置出的 stack 緩衝區。往下讀,看到一個迴圈：收資料直到緩衝區裡出現 `\r\n`,才把這一整行交給另一個函式 `sub_401200` 處理——這是 Ch 15 講的「累積緩衝區,等結束符」模式。把 `sub_401050` 重命名為 `recv_loop`，把 `sub_401200` 重命名為 `dispatch_command`（按 `N`），方便之後閱讀。

跳進 `dispatch_command`，看到一連串 `strncmp`（或教學示意裡簡化為逐字串比較),分別對比 `"LOGIN "`、`"FETCH "`、`"ADMIN "`。對 Strings 裡那幾個字串按 `X`,確認它們確實在這個函式裡被引用——跟靜態猜測一致,信心提高。把對應的三個分支目的地分別命名為 `handle_LOGIN`、`handle_FETCH`、`handle_ADMIN`。

寫下目前的協定推論：

```
格式: <COMMAND><space><參數>\r\n
已知指令: LOGIN / FETCH / ADMIN
```

## 第三步：在某 handler 發現 unsafe copy

依序看三個 handler。`handle_LOGIN` 和 `handle_FETCH` 看起來把參數複製進緩衝區前,都有一段 `cmp ecx, 0x100` / `jg too_long` 的檢查,邏輯看起來正常（跳轉方向、有號無號用法核對過,沒發現 Ch 16 講的那幾種缺陷）。

`handle_ADMIN`（教學示意反組譯）：

```
; 教學示意，非真實逆向輸出
handle_ADMIN:
sub  esp, 0x80            ; 配置 0x80 (128) bytes 的 stack 緩衝區
mov  esi, [ebp+arg_ptr]    ; 指向 "ADMIN " 後面的參數字串
lea  edi, [esp+0x10]
copy_loop:
mov  al, [esi]
mov  [edi], al
inc  esi
inc  edi
test al, al
jnz  copy_loop             ; 複製直到遇到字串結束符，edi 的容量完全沒被檢查
```

這正是 Ch 16 講的「手寫複製迴圈」樣式——沒有任何 `cmp`/跳轉限制 `edi` 寫入的範圍,只要 `esi` 指向的字串夠長（夠多非 `\0` 字元),這個迴圈就會持續寫下去,遠遠超出配置的 `0x80` bytes。對照 `handle_LOGIN`/`handle_FETCH` 都有檢查,`handle_ADMIN` 獨漏——這是「假設 → 驗證」循環裡的第一個假設：**`handle_ADMIN` 的參數沒有長度限制,送夠長的參數會溢位**。

## 第四步：靜態推 offset 粗估

從 `lea edi, [esp+0x10]` 和 `sub esp, 0x80` 粗略推算：緩衝區在 frame 裡的相對位置,加上這個函式的 saved return address 通常落在 `ebp+4`（標準 prologue 情況下）,心算一個大概的溢位長度範圍——例如「大概 140 bytes 左右會碰到 saved return address」。這只是**粗估**,不是精確值——Ch 17 強調過,編譯器插入的 padding、暫存器保存、對齊,常讓實際 offset 跟粗估有出入。粗估的目的是給動態驗證一個起點,不是終點。

## 第五步：動態 WinDbg 驗證

照 Ch 17 的流程：

1. IDA 記下 `handle_ADMIN` 入口位址,算出 RVA（假設 `RVA = 0x1450`）。
2. WinDbg attach 到跑起來的 `svctest.exe`，`lm m svctest` 確認模組載入基址。
3. `bp svctest+0x1450`，`g` 繼續。
4. 攻擊機送 `ADMIN ` + 300 個 `A`（先送一個明顯超過粗估值的長度,確保一定能看到效果,而不是卡在「剛好沒溢位」的邊界附近摸索）。
5. 斷點命中,單步（`p`/`t`）走過整個 `copy_loop`，或直接放行到函式返回前,用 `dd esp` 觀察 stack 內容——core 示意：函式返回瞬間 `ret` 要彈出的那個位置,已經被 `41414141`（`A` 的 ASCII)覆寫。
6. 讓它執行到 `ret`（或直接放行),WinDbg 應該**停下**並顯示 access violation，`EIP = 41414141`。

這一刻,靜態假設被證實：`handle_ADMIN` 的參數溢位確實能控制 EIP。再用 Ch 8 教過的 cyclic pattern 方法精確定出真正的 offset（粗估值通常和精確值有出入,這裡重新做一次 pattern 定位,不要直接信任步驟 4 的粗估)。

## 「假設 → 驗證 → 修正」的循環

這整個案例從頭到尾,實際上繞了好幾次圈,而不是一條直線：

```
假設1：三個 handler 都可能有洞
  → 驗證：逐一讀,發現 LOGIN/FETCH 有檢查，ADMIN 沒有
  → 修正：聚焦在 ADMIN

假設2：ADMIN 的溢位大約在 140 bytes 處碰到返回位址
  → 驗證：送 300 bytes 觸發，WinDbg 裡看到 EIP 真的被覆寫
  → 修正（若粗估不準）：用 cyclic pattern 重新精確定位 offset

假設3（若動態驗證沒命中斷點）：也許協定格式理解錯
  → 回頭檢查 dispatch_command 的字串比較，核對送出的指令格式是否完全對應
```

每一圈都是「讀出一個假設 → 拿真實執行結果檢驗 → 根據結果決定下一步」。這個節奏,是 OSED 逆向題真正考的東西——不是「背會」某個漏洞樣式,是這個循環本身的熟練度。熟練到即使目標完全陌生、協定完全沒見過,你也知道下一步該讀什麼、該測什麼。

## 踩雷集錦

1. **錯誤直覺：找到第一個看起來有問題的函式就該立刻深挖到底** → **正確**：先快速掃過全部 handler 建立全局印象（哪些有檢查、哪些沒有),再挑最可疑的深入——像本案例裡三個 handler 只有一個明顯缺檢查,直接鎖定它比逐行死磕第一個碰到的函式更有效率。
2. **錯誤直覺：粗估的 offset 可以直接拿來寫 exploit** → **正確**：粗估只用來決定「送多長的測試資料」和「WinDbg 要盯哪裡」,精確 offset 永遠要用 cyclic pattern（Ch 8）動態定位。
3. **錯誤直覺：第一次送觸發輸入沒反應就代表沒有洞** → **正確**：先懷疑協定格式理解錯或輸入長度不夠（Ch 17 的排錯優先序),送一個明顯超量的測試值（像本案例的 300 bytes）排除「剛好不夠長」的可能性。
4. **錯誤直覺：這個案例研究的具體位址、offset 數字可以照抄到別的目標上** → **正確**：本章的位址、RVA、`0x80`/`0x100` 都是**教學示意**,換一支 binary 這些數字全部不同。要學的是**方法**,不是記下這些數字。
5. **錯誤直覺：逆向到找到一個漏洞就結束任務** → **正確**：Practice C 會要求找第二個漏洞——真實考場目標常常不只藏一個洞,養成「找到一個之後繼續掃剩下的 handler」的習慣。

## 進階：再往深一層

- **自動化掃描候選**：對大型 binary,可以用 IDAPython 寫腳本,批次找出「所有呼叫了 `strcpy`/`memcpy` 且目的地是固定大小 stack 緩衝區」的位置,先篩出候選清單再逐一人工核對,比每個函式都手讀快。
- **多個漏洞的優先排序**：如果同一支程式有好幾個候選漏洞,先評估哪個「觸發條件最單純」「可控範圍最大」優先深挖——不是每個洞都一樣好用,考場時間有限,挑容易穩定利用的先做。

## 本章重點整理

- 完整逆向工作流：imports/strings recon → 找 `recv` → 逆 dispatch 邏輯 → 逐一檢視每個 handler 找 unsafe copy → 靜態粗估 offset → WinDbg 動態驗證控 EIP。
- 核心節奏是「假設 → 驗證 → 修正」反覆循環,不是一條直線——中途卡住或驗證失敗是常態,退回上一步重新假設即可。
- 逆向多個 handler 時先全局掃一遍找「明顯缺檢查」的那個,再深入,比逐一死磕效率高。
- 一個漏洞不是終點——同一程式常藏多個問題,養成繼續掃描的習慣。

## 自我檢核

- [ ] 能複述從拿到陌生 binary 到證實控制 EIP 的完整步驟順序
- [ ] 能說出本案例裡「假設 → 驗證 → 修正」循環具體跑了幾次、每次修正了什麼
- [ ] 能解釋為什麼先全局掃描所有 handler,比一開始就深挖第一個可疑函式更有效率
- [ ] 能說出粗估 offset 和精確 offset 分別用在流程的哪個階段
- [ ] 能說明為什麼找到一個漏洞後還要繼續檢查其他 handler

## 延伸閱讀

- **[OffSec EXP-301 課程教材 — 逆向案例研究章節]**
  - **讀哪裡**：官方教材走完整案例的模組（通常是官方自製靶程式）。
  - **學什麼**：本章示意流程對應的真實官方範例,補足教學示意無法提供的真實反組譯輸出。
  - **前提**：已有 OffSec 帳號存取課程教材,且已讀完 Ch 14-17。

- **[vulnserver（GitHub）— Stephen Bradshaw](https://github.com/stephenbradshaw/vulnserver)**
  - **讀哪裡**：整份原始碼,挑一個你還沒練過的指令（例如 `KSTET`、`LHLO`）。
  - **學什麼**：拿已知有原始碼的目標,自己重做一次本章流程（逆向→驗證),用原始碼核對自己的逆向結論是否正確——最佳的自我訓練素材。
  - **前提**：已完成 Ch 14-17。

- **[Corelan — Exploit Writing Tutorials（全系列）](https://www.corelan.be/index.php/articles/)**
  - **讀哪裡**：系列裡任一篇處理陌生程式逆向定位漏洞的文章。
  - **學什麼**：真實世界逆向案例的敘事節奏,和本章教的「假設→驗證→修正」對照。
  - **前提**：無,但已讀過本課 Part 2-3 更容易抓到對應關係。

- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)**
  - **讀哪裡**：涉及未知 binary 逆向找洞的篇章。
  - **學什麼**：另一個作者視角下的逆向找洞敘事,補充不同的思考路徑。
  - **前提**：無。

方法學完整走過一次之後,輪到你自己上場——Practice C 給你一個目標,不給現成 offset,要你自己走完整條逆向鏈並在 WinDbg 裡證實控制 EIP。

→ [練習 C：逆向自訂網路服務找出並觸發漏洞](./practice-c-reverse-and-trigger.md)
