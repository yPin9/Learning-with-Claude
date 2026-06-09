# Ch 31 — CPU pipeline

> **目標**：搞懂 CPU pipeline（管線）——五階段、為什麼能加速、三種 hazard（data/control/structural）、forwarding、stall、分支預測。這是計組的 CPU 效能核心考點。

> **環境**：概念為主，經典 RISC 五階段 pipeline。前置：Ch 17（ARM/處理器）。

## 為什麼考這個

pipeline 是現代 CPU 加速的核心技術——讓多條指令「重疊執行」。但它帶來 hazard（衝突），要靠 forwarding、stall、分支預測解。面試考「pipeline 五階段」「什麼是 hazard」「怎麼解」——測你懂不懂 CPU 怎麼跑快。

## 先建立直覺：洗衣店流水線

```
   一批衣服要：洗 → 烘 → 摺 → 收，每步 1 小時

   不用 pipeline（一批做完才下一批）：
   批1: 洗烘摺收（4hr）→ 批2: 洗烘摺收（4hr）→ ... 4 批要 16 小時

   用 pipeline（重疊）：
   時間1: 批1洗
   時間2: 批1烘, 批2洗
   時間3: 批1摺, 批2烘, 批3洗
   時間4: 批1收, 批2摺, 批3烘, 批4洗
   時間5: 批2收, 批3摺, 批4烘
   ... 4 批只要 7 小時（每步的機器都沒閒著）
```

核心：**pipeline 不是讓「單條指令更快」，是讓「多條指令重疊執行」提高吞吐量（throughput）。** 每個階段的硬體單元同時處理不同指令的不同階段——像流水線，沒有單元閒著。

## 經典五階段 pipeline

RISC（如 ARM/MIPS）的經典五階段：

```
   IF（Instruction Fetch）：取指令
   ID（Instruction Decode）：解碼 + 讀暫存器
   EX（Execute）：ALU 運算
   MEM（Memory Access）：存取記憶體（load/store）
   WB（Write Back）：寫回暫存器

   理想 pipeline（每階段 1 cycle）：
   cycle:    1    2    3    4    5    6    7
   指令1:   IF   ID   EX   MEM  WB
   指令2:        IF   ID   EX   MEM  WB
   指令3:             IF   ID   EX   MEM  WB
   → 重疊執行，理想下每 cycle 完成一條指令（throughput = 1 指令/cycle）
```

理想下 N 條指令約 `N + 4` cycle（不是 5N），大幅提速。但理想很難達到——因為 **hazard（衝突）**。

## 三種 hazard（衝突，必考）

pipeline 重疊執行帶來「指令間互相干擾」的問題：

### 1. Data Hazard（資料相依）

後一條指令需要前一條的結果，但前一條還沒寫回：

```
   ADD R1, R2, R3    # R1 = R2 + R3（結果在 WB 才寫回 R1）
   SUB R4, R1, R5    # 要用 R1，但上一條的 R1 還沒寫回！

   時間：
   ADD: IF ID EX MEM WB        ← R1 在 WB（cycle 5）才寫好
   SUB:    IF ID EX ...        ← SUB 在 ID(cycle 3) 要讀 R1，但還沒寫好！
```

解法：
- **forwarding（資料前遞 / bypassing）**：不等寫回暫存器，直接把 EX/MEM 階段算好的結果「轉發」給需要的指令。多數 data hazard 用 forwarding 解（不用 stall）。
- **stall（插入氣泡）**：forwarding 解不了時（如 load 的結果，要等 MEM），插入 nop（氣泡）等一拍。

### 2. Control Hazard（控制相依）

分支指令（if/跳轉）——要等它算出「跳不跳、跳哪」，後面的指令才知道該取哪條：

```
   BEQ R1, R2, label    # 如果 R1==R2 就跳到 label
   ???                  # 下一條取什麼？要等 BEQ 算出跳不跳！

   分支的結果通常 EX 階段才知道 → 中間已經取了錯的指令 → 要丟棄
```

解法：
- **branch prediction（分支預測）**：猜「跳或不跳」，先取猜的那條。猜對 → 無損失；猜錯 → 丟棄錯取的、重來（penalty）。現代 CPU 預測準確率很高（> 90%）。
- **delay slot**（舊 RISC）：分支後固定執行一條指令（編譯器填有用的）。
- **stall**：等分支算完再取（簡單但慢）。

### 3. Structural Hazard（結構相依）

兩條指令同時需要「同一個硬體單元」：

```
   例：如果只有一個記憶體埠，IF（取指令）和 MEM（存取資料）同 cycle 都要用記憶體 → 衝突

   解法：增加硬體（如分開 指令cache 和 資料cache，即 Harvard 架構）
        → 取指令和存資料用不同的記憶體，不衝突
```

structural hazard 較少（現代設計多已避免，如分離 I-cache/D-cache）。

## forwarding（資料前遞）詳解

最重要的 hazard 解法。core idea：**結果在 EX 算好時（還沒到 WB），就直接「轉發」給後面需要它的指令的 EX，不等寫回暫存器。**

```
   ADD R1, R2, R3   # EX 算好 R1（cycle 3 結束）
   SUB R4, R1, R5   # cycle 4 的 EX 要用 R1 → forwarding 把 ADD 的 EX 結果直接給它
                    # 不用等 ADD 的 WB（cycle 5）→ 不用 stall！
```

但 forwarding **不能解 load-use hazard**：

```
   LW  R1, 0(R2)    # load：R1 的值要 MEM 階段（cycle 4）才拿到
   SUB R4, R1, R5   # cycle 4 的 EX 要 R1，但 load 的 R1 cycle 4 才從記憶體出來
                    # → forwarding 也來不及（差一拍）→ 必須 stall 一拍
```

所以 load 後緊接著用該值，會有一個 stall（load-use hazard）——編譯器會盡量「重排指令」把無關的指令插在 load 和 use 之間填滿這拍。

## 考古題詳解

### Q1：pipeline 是什麼？為什麼能加速？

<details>
<summary>詳解</summary>

pipeline：把指令執行分成多個階段（IF/ID/EX/MEM/WB），讓**多條指令重疊執行**（不同指令在不同階段同時進行，像流水線）。

加速原理：**提高吞吐量**——不是讓單條指令更快，是讓多條重疊（每個硬體單元同時處理不同指令）。理想下每 cycle 完成一條（throughput ≈ 1 指令/cycle），N 條約 N+4 cycle 而非 5N。

**考點**：pipeline 概念 + 加速吞吐量（不是縮短單指令延遲），必考。
</details>

### Q2：五階段是哪五個？

<details>
<summary>詳解</summary>

**IF（取指令）→ ID（解碼+讀暫存器）→ EX（ALU 運算）→ MEM（存取記憶體）→ WB（寫回暫存器）**。

每階段理想 1 cycle，重疊執行。

**考點**：五階段，必背。
</details>

### Q3：什麼是 data hazard？怎麼解？

<details>
<summary>詳解</summary>

data hazard：後一指令需要前一指令的結果，但前一指令還沒寫回暫存器（重疊執行導致）。

解法：
- **forwarding（前遞）**：結果在 EX 算好就直接轉發給後面的指令，不等 WB。解多數 data hazard。
- **stall（氣泡）**：forwarding 解不了時（**load-use hazard**：load 結果要 MEM 才有，差一拍）插入 nop 等一拍。編譯器重排指令減少 stall。

**考點**：data hazard + forwarding + load-use stall，高頻。
</details>

### Q4：什麼是 control hazard？分支預測怎麼幫忙？

<details>
<summary>詳解</summary>

control hazard：分支指令（跳轉）要等算出「跳不跳、跳哪」，後面指令才知道取哪條——中間可能取錯。

**branch prediction（分支預測）**：猜跳或不跳，先取猜的。猜對無損失；猜錯丟棄錯取的、從正確處重來（penalty，浪費幾個 cycle）。現代 CPU 預測準確率 > 90%，所以多數時候無損失。

**考點**：control hazard + branch prediction，高頻。
</details>

### Q5：三種 hazard 各是什麼？

<details>
<summary>詳解</summary>

- **data hazard**：指令間資料相依（要前一條的結果）。解：forwarding / stall。
- **control hazard**：分支導致不知取哪條。解：branch prediction / delay slot / stall。
- **structural hazard**：兩指令搶同一硬體單元（如同時要記憶體）。解：增加硬體（分離 I/D cache）。

**考點**：三種 hazard，必考。
</details>

## 踩雷集錦

1. **以為 pipeline 讓單條指令更快**：不是，是讓多條重疊提高吞吐量。單條指令的延遲不變（甚至略增）。
2. **以為 forwarding 解所有 data hazard**：解不了 load-use hazard（load 結果要 MEM 才有，差一拍要 stall）。
3. **三種 hazard 混淆**：data（資料相依）、control（分支）、structural（搶硬體）。
4. **以為分支預測一定對**：會猜錯（penalty）。準確率高但非 100%。
5. **以為 pipeline 階段越多越好**：太多階段→分支預測錯的 penalty 大、複雜度高。有取捨（早期 Pentium 4 超長 pipeline 反而效率差）。
6. **忽略編譯器的角色**：編譯器重排指令（填 load-use 的 stall、delay slot）減少 hazard——軟硬體配合。

## 速記

- **pipeline**：指令分階段（IF/ID/EX/MEM/WB）重疊執行，提高**吞吐量**（不是縮短單指令延遲）。理想每 cycle 完成一條。
- 五階段：**IF**(取指)→**ID**(解碼+讀暫存器)→**EX**(運算)→**MEM**(存記憶體)→**WB**(寫回)。
- 三 hazard：**data**（資料相依→forwarding/stall）、**control**（分支→branch prediction）、**structural**（搶硬體→加硬體/分離I-D cache）。
- **forwarding**：EX 結果直接轉發，不等 WB；但**解不了 load-use**（差一拍要 stall）。
- 分支預測準確率高（>90%），猜錯有 penalty。

## 自我檢核

- [ ] pipeline 為什麼能加速？是讓單指令更快還是提高吞吐量？
- [ ] 五階段是哪五個？
- [ ] 三種 hazard 各是什麼、怎麼解？
- [ ] forwarding 怎麼運作？為什麼解不了 load-use hazard？
- [ ] 分支預測在解哪種 hazard？猜錯會怎樣？

## 延伸閱讀

### 書籍

- **《Computer Organization and Design》** — Patterson & Hennessy — Ch 4 The Processor
  - **讀哪幾章**：4.5（pipeline 概論）、4.7（hazard）、4.8（control hazard/分支預測）。
  - **和本章的關聯**：pipeline 的標準教材，把五階段/hazard/forwarding 講到底，本章源頭。

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — Ch 4 Processor Architecture
  - **讀哪幾章**：4.4–4.5（pipeline、hazard）。
  - **為什麼值得讀**：從程式設計師角度看 pipeline，含實際的 hazard 例子。

CPU 效能懂了，下一章補處理器的「語言」——指令集與組語基礎。

→ [Ch 32 指令集與組語基礎](./32-isa-assembly.md)
