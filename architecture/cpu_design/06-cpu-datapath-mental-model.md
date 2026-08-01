# Ch 6 — CPU 的心智模型：datapath + control

> **目標**：在寫任何一行 RTL 之前，先在腦中建立一張完整的單週期 CPU 全景圖。你要能回答：一條 `add x3, x1, x2` 從記憶體被抓出來到寫回暫存器，中間資料流過哪些方塊、由誰決定每個路口怎麼走。這章幾乎不寫 code，但它是後面五章的地圖。

## 為什麼需要這章？

你已經讀得懂 RISC-V 的 encoding，知道 `add x3, x1, x2` 是一個 32-bit 數字 `0x002081b3`。你也知道它「語意上」做什麼：把 x1 和 x2 加起來放進 x3。

但中間那句「執行」是黑盒。真正的問題是：

- 這 32 個 bit 怎麼變成「叫 ALU 做加法」這個動作？
- x1、x2 的值從哪來、加完的結果往哪去，是誰在路口指揮？
- 為什麼 `add`、`lw`、`beq` 明明差很多，卻能用**同一套硬體**跑完？

如果你直接跳進去寫 Verilog，你會寫出一堆 module 卻不知道它們該怎麼接、每條線該接去哪。硬體的 bug 不會噴 stack trace，它就是某個 cycle 某條線值錯了。沒有全景圖，你連「哪條線該是多少」都說不出來，根本無從除錯。

所以這章我們先建地圖。地圖的核心概念只有一個分裂：**datapath（資料通路）** 和 **control（控制）** 是兩種東西。

## 先建立直覺：工廠與領班

想像一條加工生產線（datapath）：原料進來，經過切割機、鑽孔機、烤漆機，成品出去。這些機台就是 ALU、register file、記憶體。原料在機台之間流動，就是「資料在線上跑」。

但機台自己不知道今天要做什麼。有個**領班（control）** 拿著今天的工單，站在每個岔路口喊：

- 「這批料送去鑽孔機，不是切割機！」（多工器選擇）
- 「鑽孔機開加法模式！」（ALU 操作碼）
- 「這批成品貼回 3 號架位！」（暫存器寫入）

工單就是**指令**。領班讀工單（decode），把它翻譯成一連串「開關怎麼扳」的訊號，這些訊號就是 **control signal（控制訊號）**。

```
        指令 (工單)
           │
           ▼
      ┌─────────┐   一堆 control signal (扳手指令)
      │ control │ ─────────────────────────────┐
      │ (領班)  │                               │
      └─────────┘                               ▼
                              ┌───────────────────────────────┐
   原料 ──────────────────▶   │  datapath (生產線：機台 + 線)  │ ──▶ 成品
                              └───────────────────────────────┘
```

**關鍵洞見**：datapath 是「能做各種事的通用硬體」，control 是「這條指令要它做哪件事」。同一套 datapath，換不同 control signal，就能跑不同指令。這就是為什麼 47 條 RV32I 指令能共用一套硬體——它們流過的是同一批機台，只是領班在路口扳的方向不同。

## fetch-decode-execute：CPU 的心跳

任何 CPU，不管單週期還是超純量，骨子裡都在無限重複同一個循環：

```
  ┌──────────────────────────────────────────────┐
  │                                                │
  ▼                                                │
FETCH  ──▶  DECODE  ──▶  EXECUTE  ──▶  (寫回)  ─────┘
抓指令      解碼          執行
(用 PC      (看這是什麼   (真的算/存/取，
 去 imem     指令、要哪些  結果寫回暫存器
 抓一條)     暫存器)       或記憶體)
```

- **Fetch（抓取）**：用程式計數器 **PC（program counter）** 當位址，去指令記憶體 **imem** 抓一條指令出來。
- **Decode（解碼）**：把這條指令拆開——它是哪種操作？要讀哪兩個暫存器？立即數是多少？control unit 在這步產生所有 control signal。
- **Execute（執行）**：ALU 做運算、記憶體做讀寫、結果算出來。
- **Write-back（寫回）**：把結果寫回目標暫存器（或存進記憶體），然後 PC 前進到下一條。

「單週期」的意思是：**這整個循環在一個 clock cycle 裡全做完**。時鐘一次上升沿到下一次上升沿之間，一條指令從 fetch 一路做到 write-back。下一個 cycle 換下一條指令，從頭再來。

> 這很浪費——ALU 忙的時候記憶體閒著，反之亦然，而 clock 週期得長到能塞下最慢那條指令的全部路徑。但它**概念最乾淨**，是理解一切的地基。Part 2 我們才把它切成 pipeline 榨效能。先把單週期搞到滾瓜爛熟，pipeline 才不會讓你崩潰。

## 核心概念：單週期 datapath 全景圖

這是本章的心臟。把它印在腦裡。下圖是一顆單週期 RV32I core 的資料通路，資料由左往右流：

```
                                                    ┌─────────────┐
                                        alu_src ───▶ │ (mux 選 b)  │
   ┌────┐   pc    ┌──────┐  instr        ┌────────┐  └──────┬──────┘
   │ PC │ ──────▶ │ imem │ ───────┬────▶ │regfile │ rs1_data │       ┌─────┐
   └─┬──┘         └──────┘        │      │ 2R1W   │ ─────────┼─────▶ │ ALU │─┐
     │  ▲                         │      │        │ rs2_data │  b    └──┬──┘ │
     │  │ pc_next                 │      └───▲────┘ ────┐    │ alu_op │  │   │ result
     │  │  ┌──────┐               │          │         └────┘         │  │zero│
     │  └──│ +4   │◀──────────────┼──────────┼──────────────┐         │  │   │
     │     └──────┘               │          │ rd_data      │         │  │   │
     │        ▲ branch/jump 時    ▼          │              │         │  ▼   ▼
     │        │ 改由 target 決定  ┌─────────┐ │           ┌──────┐    ┌────────────┐
     │        └───────────────────│imm_gen  │ │           │ dmem │◀───│ (位址=result)│
     │                            └────┬────┘ │           └──┬───┘    └────────────┘
     │                          imm    │      │              │ 讀出的資料
     │                                 ▼      │              ▼
     │                          (mux 選 b 用)  │        ┌─────────────┐
     │                                        └────────│ mux mem_to_reg│
     │  ┌───────────────┐   一堆 control signal          │ (選 ALU 結果  │
     └──│ control_unit  │ ─────────────────────────────▶│  或記憶體讀值)│
        └───────────────┘   reg_write/mem_read/         └─────────────┘
              ▲             mem_write/mem_to_reg/
              │ opcode      alu_src/branch/jump/
           instr[6:0]       alu_op/imm_type
```

ASCII 圖擠了點，但五個主角都在：

| 方塊 | 角色 | 我們哪章做 |
|---|---|---|
| **PC + imem** | 抓指令。PC 存位址，imem 依位址吐指令 | Ch 7 |
| **regfile (2R1W)** | 暫存器檔案。同時讀兩個源暫存器，寫回一個目標 | Ch 8 |
| **ALU** | 算術邏輯單元。真正做加減、比較、移位 | Ch 9 |
| **control_unit + imm_gen** | 領班 + 立即數產生器。把指令翻成 control signal 和立即數 | Ch 10 |
| **dmem + branch/jump 邏輯** | 資料記憶體、分支跳轉 | Ch 11 |

Part 1 剩下的每一章，就是把這張圖裡的一個方塊拿出來親手做出來、跑起來、驗證對，最後 Ch 12 全部接起來跑真程式。

## 底層機制：一條 `add x3, x1, x2` 走完全程

紙上談兵沒用，我們拿具體指令走一遍。`add x3, x1, x2` 的 encoding 是 `0x002081b3`。假設此刻 x1=1、x2=2。跟著資料流走：

1. **Fetch**：PC = `0x80000008`。imem 用這位址吐出 `0x002081b3`。
2. **Decode**：control unit 看 opcode（低 7 bit = `0110011`），認出這是 R-type 算術指令。它產生：
   - `alu_op = 0000`（ADD）
   - `alu_src = 0`（ALU 的 b 輸入選 rs2_data，不是立即數）
   - `reg_write = 1`（要寫回暫存器）
   - `mem_read = mem_write = 0`（不碰記憶體）
   - `mem_to_reg = 0`（寫回的是 ALU 結果，不是記憶體讀值）
   - `branch = jump = 0`（PC 正常 +4）
   同時，指令的 `rs1=x1`、`rs2=x2`、`rd=x3` 欄位被抽出，送進 regfile。
3. **讀暫存器**：regfile 用 rs1_addr=1、rs2_addr=2，**非同步**吐出 rs1_data=1、rs2_data=2。
4. **Execute**：`alu_src=0` 所以 ALU 的 b 選 rs2_data=2。ALU 收到 a=1, b=2, alu_op=0000，算出 result=3。
5. **Write-back**：`mem_to_reg=0` 所以寫回值選 ALU 的 result=3。`reg_write=1` 且 rd=x3，在**這個 cycle 的 clock 上升沿**，3 被寫進 x3。
6. **PC 前進**：`branch=jump=0`，pc_next = pc + 4 = `0x8000000c`，同一個上升沿更新 PC。

注意時序：步驟 1–5 全是**組合邏輯**，在一個 cycle 內像水流一樣瞬間傳導完。步驟 5、6 的「寫入」發生在 cycle 結尾的 clock 上升沿——這是唯一有「記憶」的地方（regfile 和 PC 是 always_ff）。下個 cycle 一到，新 PC 指向下條指令，一切重來。

**這就是單週期的本質**：一堆組合邏輯把資料從 PC 一路推到 ALU 結果，然後在 cycle 邊界把「該記住的東西」（新 PC、新暫存器值）鎖進 flip-flop。

## RV32I 六種指令格式複習

control unit 和 imm_gen 之所以能運作，全靠 RV32I encoding 規整。六種格式的欄位位置幾乎對齊，硬體才能用固定的線去抽欄位。快速複習（這是 Ch 10 的前置）：

```
 31       25 24   20 19   15 14  12 11    7 6      0
┌───────────┬───────┬───────┬──────┬───────┬────────┐
│  funct7   │  rs2  │  rs1  │funct3│  rd   │ opcode │  R-type  add/sub/and...
├───────────┴───────┼───────┼──────┼───────┼────────┤
│    imm[11:0]      │  rs1  │funct3│  rd   │ opcode │  I-type  addi/lw/jalr...
├───────────┬───────┼───────┼──────┼───────┼────────┤
│ imm[11:5] │  rs2  │  rs1  │funct3│imm[4:0]│ opcode │  S-type  sw/sh/sb
├───────────┼───────┼───────┼──────┼───────┼────────┤
│imm[12|10:5]│ rs2  │  rs1  │funct3│imm[4:1|11]│opcode│ B-type  beq/bne...
├───────────┴───────┴───────┴──────┼───────┼────────┤
│          imm[31:12]              │  rd   │ opcode │  U-type  lui/auipc
├───────────────────────────────────┼───────┼────────┤
│      imm[20|10:1|11|19:12]        │  rd   │ opcode │  J-type  jal
└───────────────────────────────────┴───────┴────────┘
```

三個要記住的規整性，它們是後面硬體省事的關鍵：

- **opcode 永遠在 `instr[6:0]`**：control unit 第一件事就是看這 7 bit。
- **rs1 永遠在 `instr[19:15]`、rs2 在 `instr[24:20]`、rd 在 `instr[11:7]`**（有這些欄位的格式）。所以 regfile 的三個位址口可以用固定的線接過去，不用先解碼。
- **funct3 在 `instr[14:12]`、funct7 在 `instr[31:25]`**：這兩個是「同一大類裡再細分」的欄位。opcode 說「這是 R-type 算術」，funct3+funct7 才說「是 add 還是 sub 還是 and」。這正是 Ch 9 尾聲 alu_control 要做的翻譯。

立即數是唯一被打散重組的東西：B-type 和 J-type 的立即數 bit 順序被刻意打亂（為了讓其他欄位對齊），imm_gen（Ch 10）負責把它們拼回正確的數值並做符號延伸。

> 如果你對這六種格式的 bit 佈局不熟，或忘了為什麼 B/J 型立即數要那樣打散，回看 `architecture/riscv` 課的指令編碼章節。本課假設你看得懂上圖，我們只複習「硬體怎麼利用它」的角度。

## 對比取捨：單週期 vs 多週期 vs pipeline

先給你三種微架構的大局，讓你知道單週期在光譜的哪一端。細節後面 Part 2 展開：

| 面向 | 單週期（本 Part） | 多週期 | 五級 pipeline（Part 2） |
|---|---|---|---|
| 一條指令花幾個 cycle | 1 | 3–5（依指令） | 表面 1（實際 5 級重疊） |
| clock 週期長度 | 極長（塞下最慢指令全路徑） | 短（每步一小段） | 短（塞下一級） |
| 硬體重用 | 差（ALU/記憶體各一份，多數時間閒置） | 好（分時共用） | 中（每級一份） |
| 概念複雜度 | **最低** | 中 | 高（hazard 地獄） |
| 效能 | 最差 | 中 | 最好 |
| 適合學習階段 | **入門地基** | 過渡（本課跳過） | 主線 |

本課不做多週期——它是歷史過渡型，教學價值被單週期（概念）和 pipeline（效能）夾掉了。我們單週期打穿後直接進 pipeline。

## 踩雷區

**雷 1：以為 control 和 datapath 是兩塊實體晶片。**
- 錯誤直覺：「control unit 是一顆晶片，datapath 是另一顆」。
- 正確認識：它們是**同一片矽上邏輯閘的兩種角色**。control 就是一堆從 opcode 算出 control signal 的組合邏輯；datapath 是資料流過的那些閘。分開講是為了**思考**方便（誰決定 vs 誰執行），不是物理上分家。實作時它們的閘交錯在一起。

**雷 2：以為「單週期」代表指令執行很快。**
- 錯誤直覺：「一個 cycle 做完一條，好快！」
- 正確認識：單週期的 clock 週期被**最慢的指令**綁死。`lw` 要跑完 PC→imem→regfile→ALU 算位址→dmem 讀→寫回，這條路徑超長，clock 就得慢到能塞下它。結果每條指令（連 `addi` 這種快的）都被迫用這個慢 clock。單週期的 IPC=1 但頻率極低，總效能最差。它贏在**簡單**，不是快。

**雷 3：以為 fetch-decode-execute 是三個 cycle。**
- 錯誤直覺：「fetch 一個 cycle、decode 一個、execute 一個」。
- 正確認識：那是**多週期或 pipeline** 的分法。在**單週期**裡，這三步是**同一個 cycle 內**的組合邏輯先後傳導——訊號從 PC 出發，像骨牌一樣在一個 cycle 內推到 write-back。「三步」是邏輯上的階段，不是三個時鐘週期。Part 2 我們才把它們切開成不同 cycle。

**雷 4：以為每條指令都會用到 datapath 每個方塊。**
- 錯誤直覺：「所有指令都會讀記憶體、都會用 ALU」。
- 正確認識：`add` 不碰 dmem（mem_read=mem_write=0），`beq` 不寫暫存器（reg_write=0），`lui` 不讀 rs2。datapath 是**所有指令需求的聯集**——每條指令只點亮自己需要的路徑，其他方塊的輸出被 control signal（那些 mux 選擇、write enable）擋掉、丟棄。control 的工作正是「關掉這條指令用不到的路」。

## 進階延伸

- **為什麼 mux 無所不在**：datapath 裡每個「這個值可能有兩個來源」的地方就是一個 mux，由一根 control signal 選。`alu_src` 選 ALU 的 b（rs2 還是立即數）、`mem_to_reg` 選寫回值（ALU 結果還是記憶體讀值）、PC 前面選下一個 PC（+4 還是分支目標）。學會「看到分歧就是 mux」，你就能自己畫出任何指令的 datapath。
- **control signal 從哪算出來**：在單週期裡 control unit 是**純組合邏輯**——opcode/funct3/funct7 進去，一組 control signal 出來，就是一張真值表（Ch 10 會實作）。到了有 CSR/trap 的複雜 core，才需要 FSM。本 Part 全部組合。
- **這張圖如何長成 pipeline**：Part 2 的做法是在 datapath 的四個位置插入 pipeline register（IF/ID、ID/EX、EX/MEM、MEM/WB），把一個大 cycle 切成五段。今天這張單週期圖的每根線，屆時都會被歸到某一級。所以現在把線的走向記牢，pipeline 就是「同一張圖插了四道柵欄」。

## 本章重點整理

- CPU 的骨架是 **fetch → decode → execute → write-back** 無限循環；單週期把整個循環塞進**一個** clock cycle。
- **datapath** 是能做各種事的通用硬體（PC/imem/regfile/ALU/dmem），**control** 是「這條指令要它做哪件事」的一組控制訊號。同一套 datapath 換 control 就能跑不同指令。
- 一條指令走完全程：fetch 抓指令 → control decode 出訊號 → regfile 讀源 → ALU 執行 → 在 cycle 邊界把新 PC 和結果鎖進 flip-flop。中間全是組合邏輯，只有 PC 和 regfile 是時序元件。
- RV32I 六格式 encoding 規整（opcode/rs1/rs2/rd/funct3/funct7 位置固定），硬體才能用固定線抽欄位；只有立即數需要 imm_gen 重組。
- 單週期贏在概念最簡單，輸在 clock 被最慢指令綁死。它是後面 pipeline 的地基。

## 自我檢核

- [ ] 我能不看圖，說出單週期 datapath 的五個主要方塊，以及資料從 PC 到寫回的流動順序。
- [ ] 我能解釋「datapath 和 control 的分工」，並舉出至少三個 control signal 各自控制哪個 mux 或 write enable。
- [ ] 我能拿 `add x3, x1, x2`（或任一指令），口述它走完 fetch-decode-execute 每一步的 control signal 值。
- [ ] 我能說清楚為什麼單週期「一 cycle 一指令」卻總效能最差。
- [ ] 我能指出 RV32I encoding 裡 opcode、rs1、rs2、rd、funct3、funct7 各在哪些 bit，以及為什麼這種規整讓硬體省事。
- [ ] 我能解釋為什麼 `add` 不碰 dmem、`beq` 不寫暫存器，但它們共用同一套 datapath。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.1–4.4 節**：讀 4.3 的單週期 datapath 逐步搭建圖，作者一個 mux 一個 mux 加上去，正好對照本章那張全景圖。4.4 講 control signal 怎麼從 opcode 產生，是 Ch 10 的預習。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3 節**：同一顆單週期 core 的另一種畫法，圖比 P&H 更工整。搭配讀能交叉驗證你對 datapath 的理解。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2 章 "RV32I Base Integer Instruction Set"**：權威 encoding 來源。翻到 "Base Instruction Formats" 那張表，確認你記的 opcode/funct 欄位位置正確——後面 control unit 的真值表全靠它。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 的 `picorv32.v`**：一個真被人用的極簡 RV32 core。現在先別細讀，掃一眼它的 module 結構，感受「一顆真 core 也就是這些方塊」。Ch 12 我們會拿它對照設計取捨。

搞懂了全景圖，我們就從第一個方塊開刀——PC 與 instruction fetch，讓 CPU 學會「抓下一條指令」。

→ [Ch 7 PC 與 instruction fetch](./07-pc-instruction-fetch.md)
