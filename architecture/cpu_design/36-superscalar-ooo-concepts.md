# Ch 36 — Superscalar 與 out-of-order 概念：Tomasulo、register renaming、ROB

> **目標**：本課的 core 是**單發射、循序（in-order）**五級 pipeline——每拍最多抓一條、發一條，遇到 hazard 就 stall。這章我們往上看一層：現代高效能 CPU（你桌機那顆、你手機的大核）是**多發射（superscalar）、亂序（out-of-order, OoO）**的。你會建立 ILP（instruction-level parallelism，指令級平行）極限的直覺，走一遍 Tomasulo 演算法的四大件——**register renaming**（消 WAR/WAW 假相依）、**reservation station**（讓指令在原地等運算元）、**ROB / reorder buffer**（亂序執行但循序退休，撐住精確例外）、**load/store queue**。全部用 ASCII 圖 + 具體指令序列走過。**明說：本課不實作這些，想手刻亂序 core 是另一門大課。** 這是深挖章，重觀念與取捨。
>
> **環境**：純概念章，無 RTL。所有指令序列、renaming 表、ROB 快照都是手推走一遍，不涉及模擬。

## 為什麼需要：in-order 單發射的天花板

我們一路做出來的 core，最好情況每拍退休一條指令（CPI=1）。要更快只有兩條路：**時脈拉高**（Ch 24 critical path 已經在擠了，物理有極限）、或**每拍做更多條**。前者撞牆之後，唯一的出路是後者——一拍發射並完成**多條**指令，CPI 壓到 1 以下（IPC，instructions per cycle，>1）。

但一拍發多條，馬上撞兩堵牆：

1. **相依性（dependency）**：`add x2, x1, x0` 後面緊跟 `add x3, x2, x0`，第二條要等第一條的結果。硬把它們同拍發，第二條讀到舊 x2，錯。
2. **循序發射的浪費**：in-order 的致命傷是**一條卡住，後面全卡**。假設 `lw x5, 0(x1)` cache miss 要等 100 拍，後面就算有一條 `add x9, x10, x11` 跟 x5 毫無關係、運算元早就備妥，in-order core 也只能陪它乾等 100 拍。這叫 **head-of-line blocking**（隊頭阻塞）。

亂序執行的核心洞見：**指令的「程式順序」和「執行順序」可以脫鉤**。只要相依性被尊重，誰的運算元先備妥誰先算，卡住的指令不該擋住後面無關的指令。這就是 1967 年 Tomasulo 為 IBM 360/91 浮點單元設計的演算法，至今所有 OoO core 的骨架都是它的變體。

## 先建立直覺：廚房出餐 vs 排隊結帳

把 in-order pipeline 想成**單人結帳的超市收銀台**：客人排一條隊，前面那個掏零錢掏半天（cache miss），整條隊全等——即使你後面那位只買一瓶水、早就準備好付錢。

亂序執行像**餐廳廚房**：訂單（指令）進來後不是嚴格照下單順序做。牛排要煎 10 分鐘（乘法 / cache miss），沙拉 30 秒就好——廚師會**先出沙拉**。哪道菜的食材（運算元）齊了、哪道就先做（execute）。但**送到客人桌上的順序**還是照帳單走（retire / commit in-order），不然客人會混亂（例外、中斷要能精確定位「做到哪」）。

```
   in-order（單收銀台）：            out-of-order（廚房）：
   ┌──────────────────┐            進單順序: A B C D
   │ 掏零錢的人(miss)  │ ← 卡住     ┌────────────────────────┐
   ├──────────────────┤            │ 派工: 食材齊的先做      │
   │ 買一瓶水(等)     │ ← 陪等     │   B(沙拉) → C → A → D   │ ← 執行亂序
   ├──────────────────┤            ├────────────────────────┤
   │ ...全部等        │            │ 出餐: 仍照 A B C D      │ ← 退休循序
   └──────────────────┘            └────────────────────────┘
```

「亂序執行、循序退休」——這一句是整章的心臟。**執行**為了效能可以亂，**退休（讓結果對架構狀態生效）**必須循序，才能維持「看起來像循序執行」的假象，例外才精確。

## 核心概念：ILP 與它的極限

ILP（instruction-level parallelism）指一段程式裡「理論上可以同時做」的指令數。它由**資料相依鏈（dependency chain）**決定。看這段：

```
   I1: add  x1, x2, x3
   I2: add  x4, x1, x5     # 依賴 I1 的 x1
   I3: mul  x6, x7, x8     # 跟 I1/I2 無關
   I4: add  x9, x6, x1     # 依賴 I2? 不，依賴 I1(x1) 和 I3(x6)
```

畫出相依圖（箭頭 = 「必須先完成」）：

```
        I1 ────► I2
         │        
         └──► I4 ◄──── I3
```

- I1 和 I3 沒有前置，**第一拍可同時發**。
- I2 等 I1；I4 等 I1 和 I3。
- 最長相依鏈是 I1→I2 或 I1→I4，長度 2。理論上這四條 2 拍就能做完（若機器夠寬），ILP ≈ 4/2 = 2。

ILP 的三種限制：

| 限制 | 意思 | 能不能解 |
|---|---|---|
| **true dependency（RAW, read-after-write）** | I2 真的要 I1 的結果 | **不能**，這是演算法本質，只能等 |
| **false dependency（WAR/WAW）** | 只是剛好重用了同一個暫存器名 | **能**，靠 register renaming 消掉 |
| **結構限制** | 執行單元不夠、發射寬度不夠 | 加硬體（更多 ALU、更寬 issue） |

關鍵：**真正卡住平行的只有 RAW**。WAR / WAW 是「名字撞車」的假相依——RISC-V 只有 32 個架構暫存器，編譯器被逼著重用它們，製造出一堆本不存在的相依。Renaming 的全部意義就是把這些假相依拆掉，把 ILP 從「架構暫存器數量」的枷鎖裡解放出來。

真實世界的 ILP 上限：研究（如 Hennessy & Patterson 第 3 章引的 limits study）顯示，即使給無限硬體、完美分支預測，一般整數程式的 ILP 也就在 **3～6** 之間徘徊——這就是為什麼主流大核發射寬度停在 4～8 寬，再寬邊際效益急降。分支預測錯一次就把一整窗的推測工作全丟掉，這是比執行單元更硬的天花板。

## 核心概念：register renaming——消滅 WAR/WAW

先看假相依長怎樣。編譯器重用 x1：

```
   I1: add  x1, x2, x3      # 寫 x1
   I2: sub  x4, x1, x5      # 讀 x1  → RAW，真相依
   I3: add  x1, x6, x7      # 又寫 x1 → 對 I1 是 WAW、對 I2 是 WAR
   I4: mul  x8, x1, x9      # 讀新的 x1
```

- **WAW（I1→I3）**：I1、I3 都寫 x1。循序沒事，但若讓 I3 先寫完、I1 後寫，x1 就殘留 I1 的舊值——錯。
- **WAR（I2→I3）**：I2 要讀 I1 寫的 x1，I3 又要覆蓋 x1。若 I3 搶先寫，I2 讀到 I3 的新值——錯。

這兩個「錯」本質上是**同一個名字 x1 被多次重用**造成的。若 I1 和 I3 寫的是**不同的物理暫存器**，衝突瞬間消失。這就是 renaming：**維護一大堆物理暫存器（physical register file，PRF，通常 100+ 個），每次「寫」都分配一個全新的物理暫存器**，把架構暫存器名（x1）映射到物理暫存器（p37）。

走一遍。假設 free list 有 p32、p33、p34…，初始 map：x1→p10, x2→p11...

```
   指令              動作                         renaming 後（物理）
   ────────────────────────────────────────────────────────────────
   I1: add x1,x2,x3  x1 分配新物理 p32           add p32, p11, p12
                     map[x1] = p32
   I2: sub x4,x1,x5  讀 x1→現在是 p32；x4→p33    sub p33, p32, p14
                     map[x4] = p33
   I3: add x1,x6,x7  x1 再分配新物理 p34         add p34, p15, p16
                     map[x1] = p34               ← I1 寫 p32、I3 寫 p34，WAW 沒了
   I4: mul x8,x1,x9  讀 x1→現在是 p34；x8→p35    mul p35, p34, p18
```

重點看 I2 和 I3：I2 讀的是 p32（I1 的結果），I3 寫的是 p34（全新）——**它們的目標不再撞名，I3 可以在 I2 之前執行也不會破壞 I2 的來源**。WAR 消失。同理 I1 寫 p32、I3 寫 p34，WAW 消失。

renaming 之後的相依圖只剩 RAW：

```
   I1(p32) ──► I2(p33)        I3(p34) ──► I4(p35)
```

兩條獨立鏈，可以完全平行。**renaming 把「假相依」全部溶解，暴露出真正的 ILP。** 這是整個 OoO 機器裡最關鍵的一步——沒有 renaming，reservation station 和 ROB 都無從發揮。

實作上有兩派：Tomasulo 原版把「暫存器改名」隱含在 reservation station 的 tag 裡；現代 core（BOOM、Intel/AMD 大核）用**顯式 PRF + rename table**（一張 map table 記 x→p，一個 free list 管空閒物理暫存器）。RISC-V 只有 x0～x31 共 32 個架構暫存器，BOOM 的物理暫存器 file 動輒 100+ 個，就是為了給 renaming 足夠的空間開展多條在飛的相依鏈。

## 核心概念：Tomasulo 的四大件

把上面的概念組裝成機器，Tomasulo（現代變體）有四個關鍵結構：

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ① Rename / Map table：x1→p32, x4→p33 ...                     │
   ├─────────────────────────────────────────────────────────────┤
   │  ② Reservation Stations (RS)：指令在此等運算元齊全           │
   │     ┌──────────────────────────────────────────┐             │
   │     │ op │ Vj(值) │ Vk(值) │ Qj(等誰) │ Qk(等誰)│             │
   │     └──────────────────────────────────────────┘             │
   ├─────────────────────────────────────────────────────────────┤
   │  ③ 執行單元 (ALU / MUL / LSU)：運算元齊了就抓進來算           │
   │        └── 算完，結果 + tag 廣播到 Common Data Bus (CDB)       │
   ├─────────────────────────────────────────────────────────────┤
   │  ④ ROB (Reorder Buffer)：記所有在飛指令，循序退休            │
   └─────────────────────────────────────────────────────────────┘
```

**reservation station（RS，保留站）** 是「等待區」。指令 decode+rename 後不直接進 ALU，而是進 RS。每個 RS 條目記：要做什麼 op、兩個運算元的**值（Vj/Vk）**或**還在等誰算（Qj/Qk，記著要等的物理暫存器 tag）**。當某個運算元還沒好，就記下「我等 p32」；一旦 p32 被算出來、透過 **Common Data Bus（CDB，公共資料匯流排）** 廣播回來，所有在等 p32 的 RS 條目同時抓到值、把 Q 清成 V。**兩個運算元都變成值了，這條指令就 ready，被派（dispatch）進執行單元。**

這個「廣播 + 就地喚醒」是 Tomasulo 的精髓：指令不必按順序，誰的運算元先透過 CDB 湊齊誰先跑。CDB 就是廚房那句「XX 好了！」的吆喝，所有在等這道半成品的菜同時往前推一步。

## 核心概念：ROB 與精確例外

亂序執行製造一個新問題：**例外怎麼辦？** 假設 I3 亂序先執行完、把結果寫了，然後 I1 觸發 page fault。此時架構狀態被 I3 污染了——trap handler 看到的暫存器狀態「未來已經發生了一部分」，這不是精確例外（Ch 32 我們費盡力氣保證的性質）。

**ROB（reorder buffer，重排序緩衝）** 解決這個。所有指令 decode 後**按程式順序**進 ROB 尾端，執行可以亂序、結果先寫進物理暫存器（或 ROB 條目），但**只有走到 ROB 頭、且前面全部完成，才能「退休（retire / commit）」**——這一刻才讓結果對架構狀態真正生效（更新 rename table 的架構映射、或釋放舊物理暫存器）。

```
   ROB（環狀 buffer，head 退休、tail 進入）：

   head → ┌─────────────────────────┐
          │ I1  add  p32  [done]    │ ← 頭，若前面沒了就退休
          │ I2  sub  p33  [done]    │
          │ I3  add  p34  [done]    │ ← 已算完但不能先退，要等 I1/I2
          │ I4  mul  p35  [exec..]  │
   tail → │ (空)                    │ ← 新指令從這進
          └─────────────────────────┘
```

精確例外怎麼靠 ROB 達成：I1 觸發 fault 時，I1 的 ROB 條目標記 exception，但**不立刻處理**。等它一路走到 ROB 頭要退休時，才觸發 trap——此時 I1 之後的所有指令（I2/I3/I4，不管算完沒）**全部從 ROB flush 掉**，它們的物理暫存器分配回收，架構狀態乾淨停在「I1 之前」。這就精確了：亂序賺效能、循序退休還債。

同理，**分支預測錯**也靠 ROB 收拾：mispredict 的分支退休（或提早偵測）時，把它之後所有 ROB 條目 flush，rename table 回滾到分支點的快照，從正確 target 重抓。這是為什麼 Ch 22 說「亂序 core 要把預測時的 GHR 快照跟著 branch 帶下去」——回滾時要能還原預測器狀態。

## 核心概念：load/store queue——記憶體的亂序難題

暫存器相依 renaming 能靜態看出來，但**記憶體相依**要到執行期算出位址才知道。`sw x1, 0(x2)` 和 `lw x3, 0(x4)`——若 `x2+0 == x4+0`，這個 load 依賴那個 store（RAW through memory）；若不等，兩者無關可亂序。但**位址要等 x2/x4 算出來才知道**，decode 時無從判斷。

這就是 **load queue / store queue（LSQ）** 的職責：

- **store queue**：store 的位址和資料算好後先進 store queue，**不立刻寫記憶體**（要等 store 退休才真寫，維持精確狀態——推測執行的 store 不能污染記憶體）。
- **load queue**：load 算出位址後，要**檢查 store queue 裡有沒有更早、同位址、還沒寫回的 store**。有 → **store-to-load forwarding**（直接從 store queue 把資料轉給 load，不去記憶體）；沒有 → 去 cache 讀。
- **memory disambiguation（記憶體消歧）**：若一個較早的 store 位址還沒算出來，一個較晚的 load 敢不敢先執行？激進的 core 會**推測它們不衝突**先跑 load，事後若發現位址其實相同（violation），就 flush 重來。這是 OoO core 最棘手的部分之一，也是不少側通道 / 記憶體序 bug 的溫床。

```
   store queue（等退休才寫記憶體）        load 進來先問 store queue：
   ┌──────────────────────────┐          「有沒有更早的同位址 store？」
   │ ST addr=0x40 data=0xAA    │◄─────────  有 → forward 0xAA 給 load
   │ ST addr=??  data=0xBB     │            位址未知 → 保守等 / 推測
   └──────────────────────────┘
```

## 一次走完：從指令到退休

把全部串起來，一條指令在 OoO core 的生命週期：

```
   ① Fetch    ── 抓指令（配分支預測，推測抓）
   ② Decode   ── 解碼
   ③ Rename   ── 架構暫存器 → 物理暫存器（消 WAR/WAW）；進 ROB 尾
   ④ Dispatch ── 進 reservation station 等運算元
   ⑤ Issue    ── 運算元齊（CDB 喚醒），派進執行單元 ── 這步「亂序」
   ⑥ Execute  ── ALU/LSU 計算
   ⑦ Writeback── 結果 + tag 廣播上 CDB，喚醒等它的 RS 條目
   ⑧ Commit   ── 走到 ROB 頭，循序退休，對架構狀態生效 ── 這步「循序」
```

⑤～⑦ 是亂序賺效能的地方，⑧ 是循序還債保正確。整台機器就是「盡量讓 ⑤ 早發生、保證 ⑧ 照順序」的權衡藝術。in-order 的本課 core 等於把 ④⑤ 綁死成「照程式順序、卡住就等」——省了 renaming、RS、ROB、LSQ 這一大坨硬體，換來簡單與可控，代價是隊頭阻塞。

## 對比取捨表

| 面向 | in-order 單發射（本課 core） | out-of-order superscalar（BOOM / 大核） |
|---|---|---|
| 每拍發射 | 1 條 | 2～8 條（IPC 可 >1） |
| 遇長延遲指令 | 隊頭阻塞，後面全等 | 亂序繞過，跑無關指令 |
| WAR/WAW | 靠 pipeline 循序天然不衝突 | 需 register renaming 消除 |
| 精確例外 | 循序執行天然精確 | 靠 ROB 循序退休維持 |
| 記憶體相依 | 循序 load/store 簡單 | 需 LSQ + 消歧 + forwarding |
| 硬體複雜度 | 低（本課能手刻） | 高（RS/ROB/PRF/LSQ/喚醒網路） |
| 功耗 / 面積 | 小 | 大（喚醒 CAM、廣播網路很耗） |
| 典型代表 | Rocket、picorv32、Cortex-M | BOOM、Cortex-A7x、Intel/AMD 大核 |

**取捨的本質**：OoO 用大量硬體（尤其是「就地喚醒」需要的 content-addressable memory 比對網路，很耗電）換 ILP。嵌入式 / 低功耗場景（微控制器、能效核）反而選 in-order——效能夠、面積小、功耗低、可預測（即時系統怕亂序的不確定延遲）。這也是為什麼 big.LITTLE / P-core+E-core 架構會混用兩者。

## 踩雷區

**雷 1：以為 renaming 是為了「有更多暫存器可用」。**
- 錯誤直覺：「物理暫存器多，程式就有更多變數可放，跑更快」。
- 正確認識：renaming **不改變程式能用的架構暫存器數（還是 x0～x31）**，編譯器看不到物理暫存器。它的唯一目的是**消除 WAR/WAW 假相依**，讓同名暫存器的不同「版本」各自佔一個物理暫存器、能平行在飛。多出來的物理暫存器是拿來裝「同一個架構暫存器的多個在途版本」，不是給編譯器多的變數空間。

**雷 2：以為亂序執行代表結果亂序、程式行為會變。**
- 錯誤直覺：「亂序執行，那結果順序也亂，多執行緒會出錯」。
- 正確認識：亂序的是**執行（execute）**，退休（commit）嚴格循序。從架構狀態（暫存器、記憶體）的角度看，一切**看起來像循序執行**——單執行緒語意完全不變。多執行緒的記憶體序問題是另一回事（memory consistency model），那是 store buffer / LSQ 對外可見順序的問題，不是「指令亂序退休」（指令永遠循序退休）。

**雷 3：把 ROB 當成「存結果的地方」而忽略它的核心是「循序退休」。**
- 錯誤直覺：「ROB 就是個放暫時結果的 buffer」。
- 正確認識：ROB 的本質價值是**強制循序退休這個時間點**——它是「亂序執行」和「精確架構狀態」之間唯一的閘門。沒有 ROB 這個循序退休點，例外無法精確、分支 mispredict 無法乾淨回滾、推測執行的結果無法安全撤銷。結果存哪（ROB 條目 / 物理暫存器）是實作選擇，「循序退休」才是不可省的靈魂。

**雷 4：以為發射寬度越寬越快，堆到 16 寬就爆快。**
- 錯誤直覺：「4 寬不夠就做 16 寬，IPC 翻四倍」。
- 正確認識：ILP 有天花板（一般整數程式 3～6），發射寬度超過 ILP，多出來的槽大多空著。而且寬度翻倍，rename、喚醒、bypass 網路的複雜度**超線性**成長（喚醒比對是 N² 級），功耗和 critical path 都爆炸。真實大核卡在 4～8 寬不是做不出更寬，是**邊際效益趕不上邊際成本**。分支預測錯一次丟掉整窗工作，才是比寬度更硬的瓶頸。

**雷 5：以為 in-order 就是「過時」、OoO 全面更好。**
- 錯誤直覺：「亂序更快，in-order 是舊時代產物」。
- 正確認識：in-order 在**能效、面積、可預測性**上完勝。微控制器、DSP、即時系統、GPU 的執行單元、以及 big.LITTLE 的能效核，大量用 in-order——因為它們要的是「每焦耳能算多少」和「延遲可預測」，不是峰值單執行緒 IPC。Rocket（in-order）和 BOOM（OoO）在 rocket-chip 裡並存，正是因為它們服務不同需求。設計是選擇，不是進化。

## 進階延伸

- **推測執行的安全代價**：OoO 的推測執行（分支預測後先跑、事後可撤銷）是 Spectre / Meltdown 一整族攻擊的根源——推測路徑上的 load 會在 cache 留下痕跡，即使架構上被撤銷，微架構狀態（cache line）已洩漏，可用側通道還原。renaming、ROB、推測 load 讓亂序快，也讓它成為硬體安全的重災區。這是「亂序執行看起來像循序」這句話的裂縫：架構狀態確實像循序，微架構狀態不是。
- **物理暫存器回收與 checkpoint**：mispredict 回滾要還原 rename table，實作上有 checkpoint（存快照）和 walk-back（逐條倒退）兩派，各有面積 / 延遲取捨。物理暫存器什麼時候能回收 free list（等覆蓋它的指令退休）也是一門精細的 bookkeeping。
- **clustered / 分區的執行後端**：寬度越大，全域 bypass 網路越貴。有些設計把執行單元分群（cluster），群內快、跨群慢，用局部性換 critical path。這是「寬度 vs 頻率」取捨的硬體回應。
- **想真的手刻**：BOOM 的 `RegisterRename`、`ReorderBuffer`、`IssueUnit`、`LoadStoreUnit` 是公開可讀的完整 OoO 實作（Chisel）。但正如標題說的，這是**另一門大課**——光是把喚醒邏輯、推測回滾、LSQ 消歧做對，工作量遠超本課整顆 in-order core。Ch 37 我們會實際去 clone BOOM 看它長怎樣，把這章的概念對回真程式碼。

## 本章重點整理

- 想更快只有拉時脈或每拍做更多；時脈撞牆後，出路是 **superscalar（多發射）+ out-of-order（亂序）**，把 CPI 壓到 1 以下（IPC>1）。
- ILP 由**真相依鏈（RAW）**決定；WAR/WAW 是「名字撞車」的假相依，可消除。一般整數程式 ILP 上限僅 **3～6**，這是發射寬度停在 4～8 的原因。
- **register renaming**（架構暫存器→物理暫存器，每次寫分配新物理暫存器）消除 WAR/WAW，暴露真 ILP。這是 OoO 最關鍵一步。
- **Tomasulo 四大件**：rename table、reservation station（就地等運算元，CDB 廣播喚醒）、執行單元、ROB。
- **ROB（reorder buffer）** 用「亂序執行、循序退休」維持精確例外與可回滾的推測執行——循序退休點是它不可省的靈魂。
- **load/store queue** 處理執行期才知道的記憶體相依：store 等退休才寫、store-to-load forwarding、memory disambiguation。
- **本課不實作以上任何一項**——core 是 in-order 單發射，簡單、能效好、可預測。OoO 是硬體換 ILP 的取捨，不是「更進步」。

## 自我檢核

- [ ] 我能說出 in-order 單發射的兩堵牆（相依、隊頭阻塞），並解釋 OoO 各自怎麼繞。
- [ ] 我能拿一段有 WAW/WAR 的指令序列，手動做 register renaming，並畫出 renaming 前後的相依圖。
- [ ] 我能解釋「真正卡住平行的只有 RAW，WAR/WAW 是假相依」這句話。
- [ ] 我能畫出 Tomasulo 四大件，說清 reservation station 怎麼用 CDB 就地喚醒。
- [ ] 我能解釋 ROB 如何用「循序退休」同時解決精確例外和分支 mispredict 回滾。
- [ ] 我能說出 load/store queue 為什麼必要（記憶體相依要執行期才知道），以及 store-to-load forwarding 是什麼。
- [ ] 我能舉出至少兩個 in-order 反而比 OoO 適合的場景，並說明理由。

## 延伸閱讀

- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 第 3 章「Instruction-Level Parallelism and Its Exploitation」**：本章所有概念的教科書源頭。3.4～3.7 節完整推導 Tomasulo（含 reservation station 逐拍表格）、3.10 節的 ILP limits study 給出「為何 ILP 上限 3～6」的實證。想把本章從直覺變成能算的機制，這章是必讀，也是整個微架構領域的聖經章節。
- **BOOM 技術報告（docs.boom-core.org，"The Berkeley Out-of-Order Machine" 文件）**：一顆**真的、公開的** OoO RISC-V core 怎麼把本章每個概念落成 Chisel。讀它的 "Rename Stage"、"Reorder Buffer"、"Issue Units"、"Load/Store Unit" 各節，會看到「教學概念」和「工業實作」之間的所有補丁（部分推測、回滾、消歧細節）。這是把本章接地的最佳公開資料，Ch 37 會帶你 clone。
- **Tomasulo, "An Efficient Algorithm for Exploiting Multiple Arithmetic Units" (IBM Journal, 1967)**：原始論文，講 IBM 360/91 浮點單元怎麼用 reservation station + CDB 做動態排程。歷史文獻，讀它能看到「renaming 隱含在 tag 裡」的原初形態，理解現代顯式 PRF 是後來的演化。短而經典。
- **Smith & Sohi, "The Microarchitecture of Superscalar Processors" (Proceedings of the IEEE, 1995)**：一篇把 superscalar 各部件（fetch/decode/rename/issue/execute/commit）系統性講清楚的綜述。比教科書更聚焦在「一台完整亂序機器怎麼組起來」，是理解 fetch 到 commit 全流程的好地圖。
- **Agner Fog microarchitecture manual 的 out-of-order 執行章節**：從**實測逆向**真實 Intel/AMD 大核的角度，看 ROB 大小、物理暫存器數、發射寬度、LSQ 深度這些「本章的抽象量」在真硬體上是多少，以及它們如何限制你程式的 IPC。把本章理論對回你桌機那顆 CPU。

概念看完了，下一章我們去看**真的存在、能 clone 下來讀**的兩顆 RISC-V core：Rocket（in-order，正好對照本課）和 BOOM（out-of-order，正好對照這章），順便認識 Chisel 生態和 rocket-chip 專案結構。

→ [Ch 37 Rocket / BOOM 巡禮：真實 SiFive core 長怎樣](./37-rocket-boom-tour.md)
