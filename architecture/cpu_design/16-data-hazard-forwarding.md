# Ch 16 — Data hazard（一）：forwarding / bypassing

> **目標**：把 Ch 15 跑錯的 code 修對。你會精確定義 data hazard（RAW）與它的相依距離、搞懂 forwarding（前遞，又叫 bypassing 旁路）的核心洞見——資料在 EX 就算好了，不必等寫回 register file，直接遞給需要它的 EX 級。你會建出 **forwarding unit**（比對後級的 rd 與 EX 級的 rs1/rs2）、接上 EX 級的兩個 forward mux、理解 **EX/MEM 與 MEM/WB 兩路前遞**的優先順序，並補上 pipeline 對 register file 的一個隱含要求（write-first）才能完全修對。最後用同一段 code 真跑出 15、20、40。這是深挖章，也是 pipeline 正確性的第一個大關。

Ch 15 讓 naive pipeline 把答案算成一堆 0。這章我們不塞 NOP、不卡管，而是用最漂亮的解法：**forwarding**。它讓 pipeline 保持全速（不損 throughput）的同時算對。這是 pipeline 設計裡最優雅的一招，值得一章深挖。

## 為什麼 forwarding 是最優解？

Ch 15 末尾列了三種修法：塞 NOP（浪費 cycle）、stall（卡管，慢）、forwarding（前遞）。為什麼 forwarding 勝出？

關鍵洞見藏在 Ch 15 的 trace 裡。`add x3,x1,x2` 在 cycle 3 讀不到 x1，因為 x1 要 cycle 4 才「寫回 register file」。但——**x1 的值早在 cycle 2 就被算出來了**（`addi x1,x0,5` 在 cycle 2 的 EX 級，ALU 算出 5，這個 5 就躺在 EX/MEM pipeline register 裡）。

x1 的值明明**存在**，只是還沒「歸檔」到 register file。既然值就在 pipeline register 裡，為什麼非得等它繞一圈寫回 register file、再讓後面指令從 register file 讀？**直接把 EX/MEM register 裡那個值，遞到需要它的 EX 級不就好了？**

這就是 forwarding（也叫 bypassing，旁路）：**繞過 register file，把後級 pipeline register 裡「已算好但還沒寫回」的值，直接前遞給正在 EX 級、需要它的指令。** 不等寫回、不卡管、不塞 NOP。資料一算好就抄捷徑送過去，pipeline 全速跑。

## 先建立直覺：不等歸檔，當面把答案遞過去

```
   沒 forwarding（Ch 15 naive）：
     addi x1 算出 5 ──▶ EX/MEM ──▶ MEM/WB ──▶ 寫回 register file
                                                      │
     add x3 要 x1 ─────────────────────────────── 從這裡讀 [太晚了]

   有 forwarding：
     addi x1 算出 5 ──▶ EX/MEM
                          │
                          └──前遞──▶ add x3 的 EX [直接拿，不等寫回]
```

用辦公室類比：你要用同事剛算好的一個數字。

- **沒 forwarding**：你等他把數字填進共享 Excel（register file），存檔、你再打開讀。等他存檔那段時間你只能乾等。
- **有 forwarding**：他一算好，**當面口頭遞給你**（前遞），你立刻用。Excel 之後他自己會存（寫回照樣發生），但你不用等那步。

forwarding 就是這個「當面遞」。硬體上，它是從 EX/MEM、MEM/WB pipeline register 拉幾條線回到 EX 級 ALU 輸入前的 mux——當偵測到「我 EX 級要的 rs1，正好是前一條指令即將寫回的 rd」，mux 就選那條前遞線，不選 register file 讀出的舊值。

## 核心概念：data hazard 的精確定義與相依距離

**data hazard（RAW，Read After Write）**：指令 j 要讀某暫存器，而它前面某條指令 i（i 在 j 之前）要寫同一個暫存器，但因為 pipeline 重疊，**j 讀的時候 i 還沒寫好**。

在五級 pipeline 裡，關鍵是 **i 和 j 的距離**（相隔幾條指令）：

```
   i: addi x1,...     ← 寫 x1
      （距離 d）
   j: add ...,x1,...  ← 讀 x1
```

- **距離 1**（j 緊接 i）：i 在 EX，j 在 ID；下個 cycle i 到 MEM、j 到 EX。**j 在 EX 需要 x1 時，x1 剛在 i 的 EX/MEM register 裡**。→ 從 **EX/MEM 前遞**。
- **距離 2**（i 和 j 間隔一條）：j 在 EX 時，i 已在 WB（或剛進 MEM/WB register）。**x1 在 i 的 MEM/WB register 裡**。→ 從 **MEM/WB 前遞**。
- **距離 ≥ 3**：j 在 EX 時，i 早已寫回 register file。**只要 register file 支援「同 cycle 先寫後讀」（write-first，見後）**，j 在 ID 就從 register file 讀到正確值，不需前遞。

所以 forwarding 只需處理**距離 1 和距離 2** 兩種，對應兩條前遞路徑：EX/MEM → EX、MEM/WB → EX。距離 3 以上交給 register file 的 write-first 特性。這是五級 pipeline forwarding 的完整版圖。

用 Ch 15 的 code 對號入座：

```
   addi x1,x0,5     ← i1
   addi x2,x0,10    ← i2
   add  x3,x1,x2    ← 讀 x1(距 i1 為 2)、x2(距 i2 為 1)
   add  x4,x3,x1    ← 讀 x3(距上條為 1)、x1(距 i1 為 3)
   add  x5,x4,x4    ← 讀 x4(距上條為 1，且 rs1=rs2 都是 x4)
```

- `add x3` 讀 x1：距離 2 → MEM/WB 前遞；讀 x2：距離 1 → EX/MEM 前遞。
- `add x4` 讀 x3：距離 1 → EX/MEM 前遞；讀 x1：距離 3 → 靠 write-first register file。
- `add x5` 讀 x4（rs1 和 rs2 都是 x4）：距離 1 → 兩個運算元都 EX/MEM 前遞。

這五條指令恰好把三種情況（EX/MEM 前遞、MEM/WB 前遞、距離 3 靠 register file）全踩到——這正是我們挑這段 code 的原因。

## 底層機制：forwarding unit 的比對邏輯

forwarding unit 是一塊純組合邏輯。它每個 cycle 看**現在在 EX 級**的指令要讀哪兩個暫存器（`ex_rs1`、`ex_rs2`），和**前一、前兩條指令**（現在分別在 MEM 級和 WB 級）要寫哪個暫存器（`ex_mem_rd`、`mem_wb_rd`），比對是否撞號，決定兩個運算元各要不要前遞、從哪條線前遞。

輸出 `forward_a`（給 rs1）、`forward_b`（給 rs2），各 2 bit：

- `2'b00`：不前遞，用 register file 讀出的值。
- `2'b10`：從 **EX/MEM** 前遞（距離 1，最新）。
- `2'b01`：從 **MEM/WB** 前遞（距離 2，次新）。

比對條件（以 rs1 為例）：

```
   if (EX/MEM 有效寫回 && ex_mem_rd != x0 && ex_mem_rd == ex_rs1)
        forward_a = 10   // 距離 1，優先
   else if (MEM/WB 有效寫回 && mem_wb_rd != x0 && mem_wb_rd == ex_rs1)
        forward_a = 01   // 距離 2
   else
        forward_a = 00   // 沒撞，用 regfile
```

三個守門條件，缺一不可：

1. **`reg_write` 有效**：前面那條指令真的要寫回（`beq`、`sw` 不寫回，它們的 rd 欄位是垃圾，不能拿來比）。
2. **`rd != x0`**：目標不是 x0。x0 恆 0，就算某指令「寫 x0」也是無效寫入，不該前遞（否則會把非 0 的 ALU 結果錯遞給讀 x0 的指令，而 x0 該永遠是 0）。
3. **`rd == ex_rs1`**：目標暫存器真的等於 EX 級要讀的來源，才是真撞號。

**優先順序很關鍵**：先比 EX/MEM，再比 MEM/WB。因為如果某暫存器被連續兩條指令都寫（例如 `add x1,...; add x1,...; add x3,x1,...`），距離 1 的（EX/MEM，較晚寫的那條）是**最新值**，必須蓋過距離 2 的（MEM/WB，較早的舊值）。用 `if / else if` 天然做到：EX/MEM 撞了就選它，不再看 MEM/WB。順序寫反，會前遞到過時的值。

`forwarding_unit.sv`：

```systemverilog
// forwarding_unit.sv — 比對 EX 級來源暫存器與後級將寫回的 rd，
// 決定兩個 ALU 運算元各要不要前遞、從哪條線。
// forward_a/forward_b：00=regfile 讀值  10=EX/MEM 前遞  01=MEM/WB 前遞
module forwarding_unit (
    input  logic [4:0] ex_rs1,          // EX 級當前指令的 rs1
    input  logic [4:0] ex_rs2,          // EX 級當前指令的 rs2
    input  logic [4:0] ex_mem_rd,       // 前一條（在 MEM）要寫的 rd
    input  logic       ex_mem_reg_write,
    input  logic [4:0] mem_wb_rd,       // 前兩條（在 WB）要寫的 rd
    input  logic       mem_wb_reg_write,
    output logic [1:0] forward_a,
    output logic [1:0] forward_b
);
    always_comb begin
        // rs1
        if (ex_mem_reg_write && ex_mem_rd != 5'd0 && ex_mem_rd == ex_rs1)
            forward_a = 2'b10;                       // EX/MEM 最新，優先
        else if (mem_wb_reg_write && mem_wb_rd != 5'd0 && mem_wb_rd == ex_rs1)
            forward_a = 2'b01;                       // MEM/WB 次新
        else
            forward_a = 2'b00;                       // 都沒撞，用 regfile

        // rs2（邏輯同 rs1）
        if (ex_mem_reg_write && ex_mem_rd != 5'd0 && ex_mem_rd == ex_rs2)
            forward_b = 2'b10;
        else if (mem_wb_reg_write && mem_wb_rd != 5'd0 && mem_wb_rd == ex_rs2)
            forward_b = 2'b01;
        else
            forward_b = 2'b00;
    end
endmodule
```

## 底層機制：EX 級接上兩個 forward mux

有了 `forward_a`/`forward_b`，EX 級的 ALU 輸入不再直接吃 `id_ex_rs1_data`/`id_ex_rs2_data`，而是先過一個 mux 選「regfile 值 / EX/MEM 前遞 / MEM/WB 前遞」：

```systemverilog
// 前遞 mux：依 forward_a/b 選 regfile 值 / EX/MEM 前遞 / MEM/WB 前遞
logic [31:0] fwd_rs1, fwd_rs2;
always_comb begin
    unique case (forward_a)
        2'b10:   fwd_rs1 = ex_mem_alu_result; // 前一條算好的（在 EX/MEM）
        2'b01:   fwd_rs1 = wb_data;           // 前兩條要寫回的值（在 MEM/WB→WB）
        default: fwd_rs1 = id_ex_rs1_data;    // regfile 讀出
    endcase
    unique case (forward_b)
        2'b10:   fwd_rs2 = ex_mem_alu_result;
        2'b01:   fwd_rs2 = wb_data;
        default: fwd_rs2 = id_ex_rs2_data;
    endcase
end

// alu_src 在前遞「之後」才決定 rs2 要不要換成 imm
assign ex_alu_a = fwd_rs1;
assign ex_alu_b = id_ex_alu_src ? id_ex_imm : fwd_rs2;
```

兩個關鍵細節：

- **前遞值的來源**：`2'b10` 拿 `ex_mem_alu_result`（EX/MEM register 裡前一條算的結果）；`2'b01` 拿 `wb_data`（WB 級即將寫回的值，等於 MEM/WB register 的內容）。這兩個都是「已算好、還沒或正要寫回 register file」的值。
- **前遞與 alu_src 的順序**：先前遞（選出真正的 rs2 值 `fwd_rs2`），**再**用 `alu_src` 決定要 rs2 還是立即數。順序不能反——立即數不是暫存器、不會有 hazard，只有 rs2 需要前遞。若對 `id_ex_alu_src ? imm : rs2` 的結果整體前遞，會把該用立即數的指令錯遞成暫存器值。

另外，ID/EX register **必須多帶 `rs1`/`rs2` 的位址**下來（Ch 14 提過這是 Ch 16 才加的），forwarding unit 才有 `ex_rs1`/`ex_rs2` 可比：

```systemverilog
// ID/EX 多帶 rs1/rs2 位址供 forwarding 比對
id_ex_rs1 <= id_rs1;
id_ex_rs2 <= id_rs2;
```

## 底層機制：距離 3 的隱藏前提——register file 要 write-first

forwarding 只蓋距離 1、2。距離 3（`add x4` 讀 x1，x1 由 3 條前的 `addi x1` 產生）呢？

`add x4` 在 EX 時，`addi x1` 已在 WB **之後**——它在 cycle 4 的 WB 寫 x1，而 `add x4` 在 cycle 4 的 ID 讀 x1。**同一個 cycle：WB 寫 x1、ID 讀 x1。** 誰先？

我們 Part 1 的 register file 是**正緣寫、非同步讀**：寫在 posedge、讀是組合邏輯。同一個 cycle 裡，非同步讀在 posedge **之前**就拿值，讀到的是**舊值**（Ch 8 那個「same-cycle-read-old」測試證過）。於是 `add x4` 在 ID 讀到 x1 的舊值 0——距離 3 沒被 forwarding 蓋、register file 又給舊值，x4 就錯。

我們先驗證這個問題真的存在。把 forwarding 加上、但 register file 仍用正緣寫版，跑 Ch 15 的 code：

```
[OK ] x1  =  5   (exp  5)
[OK ] x2  = 10   (exp 10)
[OK ] x3  = 15   (exp 15)   ← 距離 1、2 被 forwarding 修好了
[BAD] x4  = 15   (exp 20)   ← 距離 3 沒修：x4 = x3(15) + x1(0) = 15
[BAD] x5  = 30   (exp 40)   ← 連鎖錯：x5 = x4(15)*2 = 30

STILL WRONG (2 wrong)
```

x3 對了（forwarding 生效），但 x4=15（該 20）——正是 `add x4,x3,x1` 讀到 x3=15 對、但 x1=0 錯（距離 3 讀到舊值），15+0=15。x5 跟著連鎖錯成 30。

**解法：register file 改成 write-first（同 cycle 先寫後讀）。** 標準做法是把寫入邊沿改到 **negedge**（負緣）：WB 在 cycle 前半（negedge）就把值寫進去，ID 在 cycle 後半（下個 posedge 前）讀，讀到的是**剛寫的新值**。port 完全不變，只改寫入邊沿：

```systemverilog
// regfile（pipeline 版）：write-first——負緣寫，同 cycle 前半完成寫入
always_ff @(negedge clk) begin        // 從 posedge 改成 negedge
    if (rd_we && rd_addr != 5'd0)
        regs[rd_addr] <= rd_data;
end
```

這是 pipeline 對 register file 的**隱含要求**：WB 寫要「早於」同 cycle 的 ID 讀。教科書（Patterson）直接假設 register file「前半 cycle 寫、後半 cycle 讀」，就是這件事。加上它，距離 3 的 same-cycle 讀寫就對了，forwarding 只要專心處理距離 1、2。

（本 Part 主線之後都用這個 write-first register file。它和 Ch 8 的 port 一模一樣，只差寫入邊沿——這是「同一個模組介面、pipeline 場景下的時序調整」，不是自創新模組。）

## 底層機制：加了 forwarding 的 EX 級 datapath

把兩條前遞路徑畫進 EX 級，你能在腦中看清資料怎麼抄捷徑：

```
   ID/EX register              EX 級                    EX/MEM      MEM/WB
   ┌─────────────┐                                      register    register
   │id_ex_rs1_data│──────┐                                 │           │
   │id_ex_rs2_data│───┐  │   forward_a ─┐                  │           │
   │id_ex_imm     │─┐ │  │              ▼                  │           │
   │id_ex_rs1(位址)│─┼─┼──┼──▶┌──────────────┐             │           │
   │id_ex_rs2(位址)│─┼─┼──┼──▶│ forwarding   │             │           │
   │id_ex_alu_src │ │ │  │   │    unit      │◀────ex_mem_rd/reg_write───┤
   └─────────────┘ │ │  │   └──────────────┘◀────mem_wb_rd/reg_write────┘
                   │ │  │              │  │
                   │ │  └──▶┌───────┐  │  │
        ex_mem_alu_result──▶│ mux A │◀─┘  │
             wb_data ──────▶│(3選1) │     │      ┌─────┐
                   │        └───┬───┘     │      │     │
                   │            └─fwd_rs1─┼─────▶│ ALU │──▶ ex_alu_result
                   │        ┌───────┐     │      │     │      │
        ex_mem_alu_result──▶│ mux B │◀────┘      └─────┘      └─▶ 存進 EX/MEM
             wb_data ──────▶│(3選1) │                                register
                   └───────▶│  +    │◀─ alu_src 選 imm 還是 fwd_rs2
                   id_ex_imm │alu_src│
                            └───────┘
```

讀圖三個重點：

- **forwarding unit 的輸入全是「位址與致能」**（`id_ex_rs1`/`id_ex_rs2` 從 ID/EX 帶下來，`ex_mem_rd`/`mem_wb_rd`/兩個 `reg_write` 從後級拉回），它只做比對、輸出兩個 2-bit 選擇訊號，不碰資料。
- **兩條前遞資料線**（`ex_mem_alu_result`、`wb_data`）從後級 pipeline register 拉回來，餵進 mux A、mux B。這兩條就是「抄捷徑的資料」。
- **mux 順序**：先 forward mux（三選一，選 regfile 值/EX/MEM/MEM/WB）得 `fwd_rs1`/`fwd_rs2`，`fwd_rs2` **再**進 alu_src mux 和 imm 二選一。rs1 直接進 ALU（rs1 從不換成 imm）。

這張圖是本章所有程式碼的視覺總結：forwarding unit（比對）+ 兩個 forward mux（選資料）+ 兩條從後級拉回的前遞線。

## 範例：把 Ch 15 的 code 修對

完整 `core_fwd.sv` 相對 `core_naive.sv` 只多三處：(1) ID/EX 多帶 `rs1`/`rs2` 位址；(2) 例化 `forwarding_unit`；(3) EX 級 ALU 前多兩個 forward mux。其餘骨架完全一樣。搭配 write-first register file，跑同一段 Ch 15 的相依 code、同一個 testbench：

```bash
verilator --cc core_fwd.sv alu.sv regfile_wf.sv control_unit.sv imm_gen.sv \
  forwarding_unit.sv --exe core_fwd_tb.cpp --top-module core_fwd --Mdir obj_fwd
make -C obj_fwd -f Vcore_fwd.mk Vcore_fwd
./obj_fwd/Vcore_fwd
```

**真實輸出——同一段 Ch 15 跑成一堆 0 的 code，現在全對**：

```
[OK ] x1  =  5  (exp  5)
[OK ] x2  = 10  (exp 10)
[OK ] x3  = 15  (exp 15)
[OK ] x4  = 20  (exp 20)
[OK ] x5  = 40  (exp 40)

ALL CORRECT (0 wrong)
```

x3=15、x4=20、x5=40，全對。Ch 15 那次教學性失敗，被 forwarding + write-first register file 完整修好。

## 逐 cycle 追：看三種前遞各自生效

光看結果對還不夠，要看 forwarding unit 每個 cycle 到底怎麼判、值從哪遞。印出 EX 級的 `forward_a`/`forward_b`、兩條前遞來源的 rd 與值、最後餵進 ALU 的 `alu_a`/`alu_b`：

```
cyc| id_ex_rd exrs1 exrs2 | fA fB | exmem_rd(res) memwb_rd(res) | alu_a alu_b -> res
 2 | x1      x0    x5    | 0  0 | x0 (  0)     x0 (  0)     |     0     5 -> 5    ← addi x1
 3 | x2      x0    x10   | 0  0 | x1 (  5)     x0 (  0)     |     0    10 -> 10   ← addi x2
 4 | x3      x1    x2    | 1  2 | x2 ( 10)     x1 (  5)     |     5    10 -> 15   ← add x3
 5 | x4      x3    x1    | 2  0 | x3 ( 15)     x2 ( 10)     |    15     5 -> 20   ← add x4
 6 | x5      x4    x4    | 2  2 | x4 ( 20)     x3 ( 15)     |    20    20 -> 40   ← add x5
 7 | x0      x0    x0    | 0  0 | x5 ( 40)     x4 ( 20)     |     0     0 -> 0
```

（`fA`/`fB` 印成十進位：`1`=`2'b01`=MEM/WB 前遞，`2`=`2'b10`=EX/MEM 前遞，`0`=不前遞。）

一行一行讀，三種前遞全現形：

- **cycle 4：`add x3,x1,x2`**。`fA=1`（MEM/WB 前遞）：x1 在 MEM/WB register（`memwb_rd=x1, 值 5`），前遞 5 給 alu_a。`fB=2`（EX/MEM 前遞）：x2 在 EX/MEM register（`exmem_rd=x2, 值 10`），前遞 10 給 alu_b。ALU 算 5+10=15。**距離 2（x1）走 MEM/WB、距離 1（x2）走 EX/MEM，兩路前遞同時發生。**
- **cycle 5：`add x4,x3,x1`**。`fA=2`（EX/MEM 前遞）：x3 剛在上個 cycle 算出，躺 EX/MEM（`exmem_rd=x3, 值 15`），前遞 15。`fB=0`（不前遞）：x1 距離 3，早寫回 register file，靠 write-first 讀到 5，alu_b=5。ALU 算 15+5=20。**距離 1 走前遞、距離 3 走 register file，兩者混用。**
- **cycle 6：`add x5,x4,x4`**。`fA=2`、`fB=2`：rs1 和 rs2 都是 x4，都從 EX/MEM 前遞（`exmem_rd=x4, 值 20`），alu_a=alu_b=20。ALU 算 20+20=40。**同一個暫存器同時前遞給兩個運算元。**

這張表把 forwarding 的三種典型情況（MEM/WB 前遞、EX/MEM 前遞、兩運算元同時前遞、與 register file 混用）一次演完。對照 Ch 15 那張全 0 的 trace，你能精確看到「多了 forwarding unit 與兩個 mux」如何讓每個 cycle 的 ALU 都吃到正確的運算元。

## 範例二：雙寫優先——證明 EX/MEM 必須蓋過 MEM/WB

前面「雷 1」說 EX/MEM 要優先於 MEM/WB，這裡用一段會踩到雙寫的 code 真跑驗證。當同一暫存器被**連續兩條指令**寫，第三條讀它時，兩路前遞會**同時撞號**——優先順序決定拿到新值還是舊值。

```asm
    addi x1, x0, 100    # x1 = 100      （寫 x1，較舊）
    addi x1, x1, 1      # x1 = 101      （又寫 x1，較新；且它自己也 RAW 前一條）
    add  x2, x1, x0     # x2 = x1 = 101 （讀 x1——此刻 x1 同時在 EX/MEM 和 MEM/WB！）
    nop ×5
```

正確答案：x2 = 101（該拿最新的 x1）。若優先順序寫反、誤拿 MEM/WB 的舊值，x2 會變 100。真跑：

```
x1=101 (exp 101)  x2=101 (exp 101)
```

對。trace 看第三條指令那個 cycle 的雙撞號：

```
cyc| id_ex_rd exrs1 exrs2 | fA fB | exmem_rd(res) memwb_rd(res) | alu_a alu_b -> res
 2 | x1      x0    x4    | 0  0 | x0 (  0)     x0 (  0)     |     0   100 -> 100  ← addi x1,x0,100
 3 | x1      x1    x1    | 2  2 | x1 (100)     x0 (  0)     |   100     1 -> 101  ← addi x1,x1,1（自己也前遞x1=100）
 4 | x2      x1    x0    | 2  0 | x1 (101)     x1 (100)     |   101     0 -> 101  ← add x2,x1,x0
```

盯 cycle 4（`add x2,x1,x0` 讀 x1）：**EX/MEM 和 MEM/WB 的 rd 都是 x1**——`exmem_rd=x1(值101)` 是較新的 `addi x1,x1,1`，`memwb_rd=x1(值100)` 是較舊的 `addi x1,x0,100`。forwarding unit 因為 `if(EX/MEM) else if(MEM/WB)` 的順序，`fA=2` 選了 EX/MEM 的 **101**（最新），不是 MEM/WB 的 100。x2 = 101 + 0 = 101，對。

順帶看 cycle 3（`addi x1,x1,1` 自己讀 x1）：它 rs1=x1、距前一條 `addi x1,x0,100` 為 1，`fA=2` 從 EX/MEM 前遞拿到 100，算 100+1=101。連「立即數指令的 rs1」也照樣前遞——alu_src 只影響第二運算元（選 imm），第一運算元 rs1 永遠是暫存器、永遠參與前遞。

這段 code 精準示範了「為什麼 EX/MEM 必須優先」：不是理論潔癖，是雙寫時拿錯值就直接算錯。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| 修 RAW hazard | forwarding（前遞） | 全 stall / 塞 NOP | forwarding 不損 throughput；stall/NOP 每次相依都浪費 cycle |
| 前遞路徑 | EX/MEM 與 MEM/WB 兩路 | 只做一路 | 兩路才蓋齊距離 1、2；少一路某些相依仍錯 |
| 距離 3 | write-first register file | 也做第三路前遞 | write-first 更省線；ID 讀本就會碰 register file，順手解決 |
| 優先順序 | EX/MEM 優先於 MEM/WB | 反過來 / 不分優先 | 連續寫同暫存器時，EX/MEM 是最新值，必須優先 |
| 守門條件 | reg_write && rd!=x0 && 撞號 | 只比撞號 | 少了前兩個守門，會對不寫回的指令、對 x0 誤前遞 |
| 前遞 vs alu_src 順序 | 先前遞再選 imm | 先選再前遞 | 立即數無 hazard，只有 rs2 要前遞；反了會誤遞立即數指令 |

## 踩雷區

**雷 1：forwarding 優先順序寫反（先比 MEM/WB）。**
- 錯誤直覺：「兩路都比一比，撞到哪個用哪個」。
- 正確認識：當同一暫存器被連續兩條指令寫（`add x1,..; add x1,..; use x1`），EX/MEM 裝的是**較晚那條**（最新值）、MEM/WB 是較早那條（舊值）。必須 EX/MEM 優先。用 `if(EX/MEM)... else if(MEM/WB)...` 天然保證。反過來先比 MEM/WB，會前遞到過時的舊值，結果錯。

**雷 2：忘了 `rd != x0` 守門，對 x0 誤前遞。**
- 錯誤直覺：「rd == rs 撞號就前遞」。
- 正確認識：如果前一條指令 rd 是 x0（例如 `add x0,x2,x3`，把結果丟棄的慣用寫法），它的 ALU 結果非 0，但 x0 該恆 0。若後面指令讀 x0，撞號成立就把那個非 0 結果前遞過去，x0 就「不是 0」了，違反 ISA。必須加 `rd != x0` 守門，寫 x0 的指令永遠不前遞。

**雷 3：忘了 `reg_write` 守門，對不寫回的指令的 rd 前遞。**
- 錯誤直覺：「比 rd 欄位就好」。
- 正確認識：`beq`、`sw` 這類不寫回的指令，它們的指令編碼裡 rd 欄位（bit 11:7）是別的用途或無意義的 bit。若不看 `reg_write` 就拿這些 bit 當 rd 比對，會把「根本不會寫回的假 rd」誤判成撞號，前遞一個垃圾值。必須 `ex_mem_reg_write`/`mem_wb_reg_write` 為真才比。

**雷 4：以為 forwarding 修好一切，忘了 write-first / 距離 3。**
- 錯誤直覺：「加了 EX/MEM 和 MEM/WB 兩路前遞，所有 RAW 都對了」。
- 正確認識：兩路前遞只蓋距離 1、2。距離 3 的 same-cycle（WB 寫、ID 讀同一暫存器）要靠 register file 是 write-first（負緣寫）。本章實測過：只加 forwarding、register file 仍正緣寫，x4 會算成 15（該 20）。兩者要**一起**上，才完整。這也是為什麼教科書講 forwarding 前，會先假設 register file「前半寫後半讀」。

**雷 5：對 `alu_src` 選出的結果整體前遞。**
- 錯誤直覺：「ALU 第二輸入就一個值，前遞它就好」。
- 正確認識：ALU 第二輸入可能是 rs2（暫存器，會 hazard）或立即數（不會 hazard）。必須**先對 rs2 前遞**得 `fwd_rs2`，**再**用 `alu_src` 在 `fwd_rs2` 和 `imm` 間選。若先 `alu_src` 選好、再對結果前遞，會把該用立即數的指令（如 `addi`）也套上前遞邏輯，錯遞一個暫存器值進去。順序是「先前遞、後選 imm」。

## 進階延伸

- **load-use hazard：forwarding 救不了的一種**。本章的相依指令，前一條是 ALU 型（`add`/`addi`），它的結果在 EX 就算好，能前遞。但如果前一條是 **load**（`lw`），它的結果要到 **MEM 級**（讀完記憶體）才有——比後一條需要它的 EX 級**晚一個 cycle**。這種「值來得太晚、連前遞都來不及」的 hazard 叫 **load-use hazard**，光靠 forwarding 修不了，必須 stall 一個 cycle（插一個 bubble）再前遞。這是 Ch 17 的主題。本章的 forwarding 是它的前置，Ch 17 會在 forwarding 之上加一個 hazard detection unit 專抓這個 case。
- **forwarding 是關鍵路徑的常見兇手**。EX 級 ALU 輸入前多了一個三選一 mux，還從兩個後級 pipeline register 拉線回來——這些 mux 和長線增加了 EX 級的組合延遲。forwarding 修好了功能，但可能拖慢時脈（Ch 24 關鍵路徑會量化）。真設計要在「forwarding 蓋多全」和「別讓 EX 級太慢」之間權衡。這也解釋為什麼有些 core 不做全部前遞路徑，寧可某些少見的 hazard 用 stall。
- **forward 到 ID 而非 EX？branch 提前 resolve 的伏筆**。本章前遞到 EX 級的 ALU 輸入。但分支比較若想提前到 ID 級做（縮短 branch penalty，Ch 18），那 ID 級也需要前遞路徑——把後級的值遞到 ID 的比較器。所以「前遞到哪一級」取決於「哪一級需要最新的暫存器值」。本課 branch 在 EX resolve，故只需前遞到 EX；提前到 ID 就得多一組 forwarding 到 ID。這是 Ch 18 的取捨。
- **為什麼不乾脆全 stall 就好，何必 forwarding**：全 stall（偵測到 RAH 就凍住後面指令等寫回）也能保正確，程式碼還更簡單。但每次相依都卡 2~3 個 cycle，而相依在真實程式裡極常見（連續運算幾乎條條相依），CPI 會飆高、pipeline 的 throughput 優勢蒸發大半。forwarding 讓絕大多數 RAW 零代價解決（不卡管），只有 load-use 這種前遞也來不及的才 stall 一拍。這是「大多數情況全速、極少數才付代價」的漂亮設計，值得這一整章。

## 本章重點整理

- **data hazard（RAW）**：後面指令要讀的暫存器，前面指令還沒寫回。根源是 pipeline 重疊執行，讀取早於寫回。
- **forwarding（前遞/bypassing）** 的洞見：資料在 EX 就算好、躺在後級 pipeline register 裡，不必等寫回 register file，**直接遞給需要它的 EX 級**。不卡管、不損 throughput。
- 五級 pipeline 只需兩路前遞：**EX/MEM → EX**（距離 1）、**MEM/WB → EX**（距離 2）。**距離 ≥ 3** 靠 register file 的 **write-first（負緣寫）** 解決。
- **forwarding unit** 比對 `ex_rs1`/`ex_rs2` 與 `ex_mem_rd`/`mem_wb_rd`，三個守門條件缺一不可：**reg_write 有效、rd != x0、撞號**。EX/MEM **優先於** MEM/WB（連續寫時前者是最新值）。
- EX 級接兩個 forward mux 選「regfile 值 / EX/MEM 前遞 / MEM/WB 前遞」；**先前遞、再用 alu_src 選立即數**（立即數無 hazard）。ID/EX 須多帶 rs1/rs2 位址供比對。
- 實測：只加 forwarding、register file 仍正緣寫，x4 錯成 15；補上 write-first 後 x1~x5 全對（15/20/40）。trace 顯示三種前遞（MEM/WB、EX/MEM、雙運算元）各自生效。

## 自我檢核

- [ ] 我能定義 RAW hazard，並解釋 forwarding 的核心洞見（值已算好、不必等寫回）。
- [ ] 我能說出五級 pipeline 為何只需 EX/MEM 和 MEM/WB 兩路前遞、距離 3 為何靠 write-first register file。
- [ ] 我能寫出 forwarding unit 的比對邏輯，並解釋三個守門條件（reg_write、rd!=x0、撞號）各防什麼錯。
- [ ] 我能解釋 EX/MEM 為什麼要優先於 MEM/WB，舉出優先順序寫反會錯的例子。
- [ ] 我能說明「先前遞、後選立即數」的順序為什麼不能反。
- [ ] 我能對著 forwarding trace，指出某個 cycle 的 fA/fB 是哪種前遞、值從哪個 pipeline register 來、ALU 算出什麼。
- [ ] 我能解釋為什麼 forwarding 修不了 load-use hazard（指向 Ch 17）。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7 節「Data Hazards: Forwarding versus Stalling」**：本章的教科書藍本，把 forwarding unit 的比對條件（含 EX hazard 與 MEM hazard 兩路、x0 與 reg_write 守門、雙寫優先）逐條推導，並畫出 EX 級 forward mux 的完整 datapath。想看每個條件的形式化推導，這是最完整的來源。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5.3「Data Hazards」與 HDL 實作**：給出可合成的 forwarding 邏輯 SystemVerilog，和本章 `forwarding_unit.sv` 一脈相承。對照它確認你的比對條件與 mux 接法正確，特別是它怎麼處理 register file 的 write-first。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) Appendix C.2「Data Hazards Requiring Stalls」之前的 forwarding 段落**：從一般化角度講 forwarding（bypassing）——不只五級，任意深度 pipeline 的前遞路徑該怎麼系統性推導。想把「兩路前遞」推廣成通則、之後理解更深 pipeline 的前遞網路，讀它。
- **[Sodor 五級 core 的 dpath.scala 中 forwarding 相關邏輯](https://github.com/ucb-bar/riscv-sodor)**：一顆真教學 core 的 forwarding 實作。看它怎麼命名前遞訊號、怎麼接 mux、怎麼和 stall 邏輯共存——本章只做 forwarding，Ch 17 加 stall 後，回頭對照它的完整版最有收穫。

我們用 forwarding 修好了 ALU 型指令的 RAW hazard，同一段 code 從一堆 0 變成正確的 15、20、40。但有一種 hazard 連 forwarding 都來不及救：`lw` 的結果要到 MEM 才有，比後面用它的 EX 晚一拍。下一章我們補上這最後一塊——load-use hazard，得先 stall 一個 bubble 再前遞。

→ [Ch 17 Data hazard（二）：load-use hazard、stall / bubble](./17-data-hazard-load-use-stall.md)
