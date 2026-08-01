# Ch 14 — IF/ID/EX/MEM/WB 切分與 pipeline register

> **目標**：把 Part 1 那條單週期長路徑，實際切成 IF/ID/EX/MEM/WB 五級，在每級之間插入 **pipeline register**。你會搞懂每一級具體做什麼、pipeline register 到底要存哪些訊號（關鍵：控制訊號也得一路帶到它該生效的那一級）、加了 pipeline register 之後完整 datapath 長什麼樣，並用 verilator 真跑一個 pipeline register 模組，親眼看訊號如何一個 cycle 往後移一級。這章是 pipeline 的骨架，之後所有 hazard 處理都掛在這副骨架上。

Ch 13 講完「為什麼」，這章講「怎麼切」。切五級聽起來簡單——datapath 找四個斷點插暫存器就好。但魔鬼藏在一個容易忽略的地方：**控制訊號也要跟著指令一起流過 pipeline**。這章把骨架與這個關鍵細節一次講清。

## 為什麼需要 pipeline register？

pipeline 讓五條指令同時各站一級。但硬體只有一份 register file、一個 ALU、一份記憶體介面。cycle 3 時，指令1 在 EX 用 ALU、指令2 在 ID 讀 register file——它們用的是不同的硬體區塊，這沒問題。真正的問題是：**每一級算出的中間結果，怎麼在下一個 cycle 交給下一級，而不被後面湧上來的新指令覆蓋？**

單週期沒這問題：一條指令獨佔整個 cycle，所有中間值都是同一條指令的，用組合邏輯的線串起來即可。但 pipeline 裡，cycle 3 的 ALU 正在算指令1 的東西，cycle 4 的 ALU 就要換算指令2 的東西了。指令1 在 EX 算出的結果，必須**被鎖存下來**，才能在 cycle 4 交給 MEM 級——否則 cycle 4 一到，EX 的組合邏輯已經在算指令2，指令1 的結果就丟了。

**pipeline register 就是每兩級之間的一排正緣觸發暫存器（flip-flop），在每個 clock 邊沿把「這一級算好的所有中間值」鎖存下來，穩穩交給下一級。** 它是 pipeline 的「交接檯面」，讓相鄰兩級各跑各的指令而不互相踩到。

## 先建立直覺：接力賽的交接棒

```
   IF ──棒──▶ ID ──棒──▶ EX ──棒──▶ MEM ──棒──▶ WB
       ↑          ↑          ↑           ↑
    if_id_reg  id_ex_reg  ex_mem_reg  mem_wb_reg
     (交接棒)   (交接棒)    (交接棒)     (交接棒)
```

把五級想成接力賽的五棒跑者。每一棒跑者（一級的組合邏輯）跑完自己那段，在**交接區（clock 邊沿）** 把棒子（中間資料）交給下一棒。pipeline register 就是交接區：

- 每個 clock 邊沿，四個交接區**同時**發生交接：IF 把棒交給 ID、ID 交給 EX、EX 交給 MEM、MEM 交給 WB。
- 交接完，每一級手上都拿到「上游剛遞來的那條指令的資料」，開始跑自己這段。
- 下一個 clock 邊沿，再一起交接。棒子（指令）就這樣一級一級往後傳。

命名照課程約定：**pipeline register 叫 `<前級>_<後級>_reg`**——`if_id_reg`、`id_ex_reg`、`ex_mem_reg`、`mem_wb_reg`。裡面存的每個訊號，加前綴標明它屬於哪個交接檯面（例如 `id_ex_alu_op` 表示「在 ID/EX 之間傳遞的 alu_op」）。這套命名整個 Part 2 與後續 Part 都嚴格沿用。

## 核心概念：五級各做什麼

| 級 | 全名 | 這一級的工作 | 用到的硬體 |
|---|---|---|---|
| **IF** | Instruction Fetch | 用 PC 從指令記憶體抓指令；PC += 4 | PC、指令記憶體、加法器 |
| **ID** | Instruction Decode | 解碼指令；讀 register file 取 rs1/rs2；產生立即數；control unit 算控制訊號 | control unit、register file、imm_gen |
| **EX** | Execute | ALU 做運算（算術/位址計算/比較） | ALU、alu_src mux |
| **MEM** | Memory | load 讀資料記憶體、store 寫資料記憶體（其他指令直通） | 資料記憶體 |
| **WB** | Write Back | 把結果（ALU 結果或 load 資料）寫回 register file | write-back mux、register file 寫口 |

注意 register file 被**兩級**碰到：ID 級**讀**（rs1/rs2）、WB 級**寫**（rd）。這是 pipeline 的一個天生張力——同一個 cycle 裡，前面某條指令在 WB 寫、後面某條指令在 ID 讀，可能撞到同一個暫存器。這正是 Ch 15 會爆出來、Ch 16 forwarding 要處理的 data hazard 源頭。這章先把骨架搭好。

## 核心概念：pipeline register 要存什麼——關鍵是控制訊號也要一路帶下去

這是本章最容易踩雷、也最關鍵的觀念。

單週期時，control unit 在 decode 一算出所有控制訊號（`reg_write`、`alu_src`、`alu_op`……），這些訊號**同一個 cycle 內**就餵到各自的硬體：`alu_op` 餵 ALU、`reg_write` 餵 register file 寫口。組合邏輯的線一拉就到，因為全部發生在同一 cycle。

pipeline 裡不行。控制訊號在 **ID 級**算出來，但它們要生效的時機**分散在後面各級**：

- `alu_op`、`alu_src` 要在 **EX 級**餵給 ALU（下一個 cycle）。
- （load/store 的記憶體控制）要在 **MEM 級**生效（再下一個 cycle）。
- `reg_write`、write-back mux 選擇要在 **WB 級**生效（最後一級）。

問題：控制訊號在 ID 算出時，那條指令才剛進 ID；等它走到 WB 已經過了四個 cycle，那時 ID 級早換成別的指令、control unit 早在算別條指令的訊號了。所以**每條指令的控制訊號，必須跟著它本人一起流過 pipeline register，一路帶到它該生效的那一級**。

具體做法：在 ID 算出全套控制訊號後，把它們塞進 pipeline register，隨資料一起逐級往後傳。每過一級，用不到的訊號可以丟掉（例如 `alu_op` 在 EX 用完就不用再往 MEM 帶），還要用的繼續帶。這叫**控制訊號隨管線攜帶（carrying control signals down the pipe）**。

我們把每個 pipeline register 該存的東西列清楚（本 Part 前半只做 R-type 與 I-type 算術，故先列這些指令用得到的；load/store/branch 的欄位 Ch 17–18 再補）：

**IF/ID register**（`if_id_reg`）：
- `if_id_pc`：這條指令的 PC（branch/jump 算目標要用）。
- `if_id_instr`：抓到的 32-bit 指令本體（ID 級要解碼它）。

**ID/EX register**（`id_ex_reg`）：
- 資料：`id_ex_rs1_data`、`id_ex_rs2_data`（讀出的暫存器值）、`id_ex_imm`（立即數）。
- 位址：`id_ex_rd`（目標暫存器編號，一路帶到 WB 才知道寫哪）；**Ch 16 起還要帶 `id_ex_rs1`/`id_ex_rs2`**（forwarding 比對用，本章先不加）。
- 控制：`id_ex_alu_op`、`id_ex_alu_src`（EX 用）、`id_ex_reg_write`（WB 用，先在這裡上車）。

**EX/MEM register**（`ex_mem_reg`）：
- 資料：`ex_mem_alu_result`（ALU 算出的結果）。
- 位址：`ex_mem_rd`。
- 控制：`ex_mem_reg_write`（WB 用，繼續帶）。

**MEM/WB register**（`mem_wb_reg`）：
- 資料：`mem_wb_result`（要寫回的值）。
- 位址：`mem_wb_rd`。
- 控制：`mem_wb_reg_write`（WB 這一級終於用到它）。

看 `reg_write` 這個訊號的旅程：ID 算出 → 上 `id_ex_reg` → `ex_mem_reg` → `mem_wb_reg` → WB 級終於拿它決定「這條指令要不要寫回」。它跟著指令走了三個 pipeline register、四個 cycle 才生效。**這就是控制訊號隨管線攜帶的具體樣貌。** 忘了帶某個控制訊號，或帶錯級，是新手寫 pipeline 最常見的 bug。

## 底層機制：加了 pipeline register 的完整 datapath

```
   IF          | ID              | EX            | MEM        | WB
   ┌────┐      |                 |               |            |
   │ PC │─┬───▶│ 解碼             │               |            |
   └────┘ │    │ ┌──────────┐    │               |            |
     ▲    │    │ │control_u │─控制訊號─────────────────────────────▶(各級取用)
     │  +4│    │ └──────────┘    │               |            |
   [imem] │    │ ┌──────────┐    │ ┌────┐        |            |
     │    │    │ │ regfile  │rs1─┼▶│    │        |            |
     └────┘    │ │  (讀口)  │rs2─┼▶│ALU │─result─┼─▶[dmem]─┬──┼─▶┌────────┐
               │ └──────────┘    │ └────┘        |         │  |   │regfile │
               │ ┌──────────┐    │  ▲            |    (直通)│  |   │ (寫口) │
               │ │ imm_gen  │imm─┼──┘alu_src mux |         │  |   └────────┘
               │ └──────────┘    │               |         │  |      ▲
               |                 |               |         │  |      │
      [if_id_reg]      [id_ex_reg]      [ex_mem_reg]   [mem_wb_reg]───┘
        ▲                ▲                ▲              ▲
        └── 每個 clock 邊沿，四排暫存器同時鎖存，資料整體往後移一級 ──┘
```

讀圖重點：

- 四排 `[..._reg]` 把 datapath 切成五段。每段的組合邏輯只涵蓋那一級的工作。
- control unit 在 ID 算出的控制訊號，橫向穿過後面的 pipeline register（虛線那條），在各級被取用。這就是「控制訊號隨管線攜帶」。
- register file 的**讀口在 ID、寫口在 WB**，橫跨整條 pipeline。WB 寫回的 `rd` 編號，是四個 cycle 前在 ID 解出、一路 `id_ex_rd`→`ex_mem_rd`→`mem_wb_rd` 帶過來的。

**追一條指令 `rd` 的旅程**，把「位址也要隨管線走」看具體：`add x3,x1,x2` 在 ID 解出 rd=x3（`id_rd`），但 WB 級四個 cycle 後才寫，那時 ID 早換人。所以 x3 這個編號得坐 pipeline register：`id_ex_rd <= id_rd`（3 上車）→ `ex_mem_rd <= id_ex_rd`（3 續傳）→ `mem_wb_rd <= ex_mem_rd`（3 到 WB 前）→ WB 用 `mem_wb_rd` 當寫入位址。**不只控制訊號，連「要寫哪個暫存器」的位址也得隨管線攜帶**——否則 WB 級根本不知道現在這個結果該落到哪。這和控制訊號攜帶是同一件事的兩面：一條指令走到某級時需要的所有資訊（控制、位址、資料），都得跟著它一起流過來。

**為什麼記憶體要獨立成 MEM 一級**：R-type、I-type 算術根本不碰資料記憶體，MEM 級對它們是「直通」（本 Part 前半就是這樣，見 Ch 15 程式碼）。既然大多數指令不用它，為何不把記憶體存取併進 EX？因為 (a) 記憶體存取延遲大，併進 EX 會讓 EX 級變成最慢一級、拖垮全 pipeline 時脈；(b) load 要「先算位址（EX）再讀記憶體（MEM）」，本就有先後，硬要塞同一級會讓那級延遲 = 算址 + 讀記憶體，又長又不均衡。把記憶體獨立一級，EX 專心算、MEM 專心存取，各級延遲才平衡——這正是 Ch 13 講的「切點要讓各級等長」。

## 範例 1：pipeline register 模組，看訊號逐級傳遞

先用一個乾淨的小模組，抽掉 datapath 的複雜度，只看 pipeline register 的本質行為：**每個 clock，值往後移一級**。我們塞一個 8-bit tag 進 IF，追它一路往後跑。

`pipe_demo.sv`：

```systemverilog
// pipe_demo.sv — 示範 pipeline register 逐級傳遞：把一個 8-bit tag
// 灌進 IF，看它每個 clock 往後移一級（IF→ID→EX→MEM→WB）。
module pipe_demo (
    input  logic       clk,
    input  logic       rst,
    input  logic [7:0] if_tag,      // 這一拍餵進 IF 的標記
    output logic [7:0] id_tag,
    output logic [7:0] ex_tag,
    output logic [7:0] mem_tag,
    output logic [7:0] wb_tag
);
    always_ff @(posedge clk) begin
        if (rst) begin
            id_tag <= 8'd0; ex_tag <= 8'd0; mem_tag <= 8'd0; wb_tag <= 8'd0;
        end else begin
            id_tag  <= if_tag;   // IF/ID
            ex_tag  <= id_tag;   // ID/EX
            mem_tag <= ex_tag;   // EX/MEM
            wb_tag  <= mem_tag;  // MEM/WB
        end
    end
endmodule
```

要點：

- 四個 `<=`（非阻塞賦值，non-blocking）在同一個 `always_ff` 裡，**同時**發生。`ex_tag <= id_tag` 用的是 `id_tag` 這個 clock 邊沿**之前**的舊值，不是這行之後的新值。這是 non-blocking 的精髓，也是 pipeline register 一定要用 `<=` 而非 `=` 的原因——四級交接必須「同時」，用阻塞賦值 `=` 會變成一個 cycle 內全串下去（值瞬間衝到 WB），語意全錯。
- 每級是一排 flip-flop，`posedge clk` 觸發。這就是 pipeline register 的骨。真正的 pipeline register 只是把 8-bit tag 換成「那一級要傳的整組訊號」。

testbench `pipe_demo_tb.cpp`，前 3 拍灌三個不同 tag，之後灌 0，看它們排隊往後移：

```cpp
#include "Vpipe_demo.h"
#include "verilated.h"
#include <cstdio>

static Vpipe_demo *dut;
static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vpipe_demo;
    dut->rst = 1; dut->if_tag = 0; tick();
    dut->rst = 0;

    uint8_t feed[] = {0xA1, 0xB2, 0xC3, 0, 0, 0, 0};
    printf("cyc | if | id ex mem wb\n");
    for (int c = 0; c < 7; c++) {
        dut->if_tag = feed[c];
        dut->eval();
        printf("%2d  | %02x | %02x %02x  %02x %02x\n",
               c, dut->if_tag, dut->id_tag, dut->ex_tag, dut->mem_tag, dut->wb_tag);
        tick();
    }
    delete dut;
    return 0;
}
```

編譯執行：

```bash
verilator --cc pipe_demo.sv --exe pipe_demo_tb.cpp --top-module pipe_demo --Mdir obj_demo
make -C obj_demo -f Vpipe_demo.mk Vpipe_demo
./obj_demo/Vpipe_demo
```

真實輸出：

```
cyc | if | id ex mem wb
 0  | a1 | 00 00  00 00
 1  | b2 | a1 00  00 00
 2  | c3 | b2 a1  00 00
 3  | 00 | c3 b2  a1 00
 4  | 00 | 00 c3  b2 a1
 5  | 00 | 00 00  c3 b2
 6  | 00 | 00 00  00 c3
```

看 `a1` 這個 tag 的旅程：cyc0 在 IF，cyc1 到 ID，cyc2 到 EX，cyc3 到 MEM，cyc4 到 WB。**每個 cycle 精準往後移一級**，五個 cycle 走完全程。三個 tag（a1/b2/c3）像接力棒一樣排成一條斜線往右下移動——這正是 Ch 13 斜階梯圖的硬體實體。你看到的是 pipeline register 最純粹的行為：資料整體逐級後推。

## 範例 2：把控制訊號也塞進 pipeline register

pipeline register 存的不只是資料，還有前面強調的**控制訊號**。真正的 `id_ex_reg` 長這樣（節錄自我們的 pipelined core，Ch 15 會用到完整版）：

```systemverilog
// ID/EX pipeline register：資料 + 位址 + 控制訊號一起鎖存
logic        id_ex_reg_write, id_ex_alu_src;   // 控制訊號
logic [3:0]  id_ex_alu_op;                      // 控制訊號
logic [31:0] id_ex_rs1_data, id_ex_rs2_data, id_ex_imm; // 資料
logic [4:0]  id_ex_rd;                          // 位址（帶到 WB 才知道寫哪）

always_ff @(posedge clk) begin
    if (rst) begin
        id_ex_reg_write <= 1'b0;   // reset 清成「不寫回」的安全值
        id_ex_alu_src   <= 1'b0;
        id_ex_alu_op    <= 4'b0000;
        id_ex_rs1_data  <= 32'd0;
        id_ex_rs2_data  <= 32'd0;
        id_ex_imm       <= 32'd0;
        id_ex_rd        <= 5'd0;
    end else begin
        id_ex_reg_write <= id_reg_write;  // 控制訊號從 ID 上車
        id_ex_alu_src   <= id_alu_src;
        id_ex_alu_op    <= id_alu_op;
        id_ex_rs1_data  <= id_rs1_data;
        id_ex_rs2_data  <= id_rs2_data;
        id_ex_imm       <= id_imm;
        id_ex_rd        <= id_rd;
    end
end
```

`id_ex_reg_write` 這個控制訊號從這裡上車後，下一個 pipeline register 是 `ex_mem_reg_write <= id_ex_reg_write`，再下一個 `mem_wb_reg_write <= ex_mem_reg_write`，最後 WB 級用 `mem_wb_reg_write` 決定要不要寫回。整組控制訊號就這樣坐著 pipeline register 往後傳。這段程式碼在 Ch 15 的完整 core 裡真跑，那時你會看到它讓整條 pipeline 動起來（雖然還會算錯——那是 hazard，Ch 16 修）。

**reset 值的講究**：`id_ex_reg_write` reset 成 `0`（不寫回）。為什麼不是隨便給？因為 reset 後、pipeline 還沒填滿時，各級 register 裡是「垃圾指令」。若 `reg_write` reset 成 1，這些垃圾會亂寫 register file。把所有「會產生副作用的控制訊號」（`reg_write`、記憶體寫致能等）reset 成關閉，等於在 pipeline 裡塞了幾條無害的 NOP 佔位——這是 pipeline 一個重要的正確性細節。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| pipeline register 賦值 | non-blocking `<=` | 阻塞 `=` | `<=` 才能讓四級「同時」交接；`=` 會一個 cycle 串到底，語意全錯 |
| 控制訊號傳遞 | 隨管線逐級攜帶 | ID 直接拉線到各級 | 直接拉線只對「同 cycle」有效；pipeline 各級跑不同指令，必須攜帶 |
| register 命名 | `<前級>_<後級>_reg` | 隨意命名 | 全課一致，波形除錯時一眼看出訊號在哪個交接檯面 |
| 有副作用控制訊號 reset 值 | 一律關閉（0） | 不管 / 給 X | 防止 pipeline 未填滿時的垃圾指令亂寫狀態 |
| pipeline register 寬度 | 只帶用得到的訊號 | 全訊號都帶到底 | 帶多了浪費面積；每級丟掉後面用不到的，省 flip-flop |

## 踩雷區

**雷 1：pipeline register 用阻塞賦值 `=`。**
- 錯誤直覺：「賦值就賦值，`=` 和 `<=` 應該差不多」。
- 正確認識：在 `always_ff` 裡，`=`（阻塞）會讓 `id_tag = if_tag; ex_tag = id_tag;` 在同一個 cycle 內**串連生效**——`ex_tag` 拿到的是剛更新的 `id_tag`（也就是 `if_tag`），值一個 cycle 就從 IF 衝到 WB，pipeline 塌成單週期。必須用 `<=`（非阻塞），所有 `<=` 讀的都是邊沿前的舊值，四級同時各移一格。pipeline register 永遠 `<=`。

**雷 2：忘了把控制訊號帶下去，或帶到錯的級。**
- 錯誤直覺：「control unit 算出 `reg_write`，直接拉條線到 register file 寫口就好」。
- 正確認識：那條線是「ID 這個 cycle 的 `reg_write`」，但那條指令四個 cycle 後才在 WB 寫回，那時 ID 早換人了。`reg_write` 必須坐 `id_ex_reg`→`ex_mem_reg`→`mem_wb_reg` 一路帶到 WB。少帶一級、或在中途就接出去用，寫回會用到別條指令的控制訊號，是 pipeline 最經典的 bug。

**雷 3：pipeline register 沒 reset，或 reset 值亂給。**
- 錯誤直覺：「反正會被覆蓋，reset 值不重要」。
- 正確認識：reset 後 pipeline 要幾個 cycle 才填滿真指令，這期間各級是垃圾。若 `reg_write`、記憶體寫致能這類**有副作用**的控制訊號 reset 成 1，垃圾指令會亂寫 register file / 記憶體，狀態被污染。把有副作用的控制訊號一律 reset 成關閉（等同塞 NOP），資料訊號 reset 成 0 即可。

**雷 4：以為切了級 latency 會變短。**
- 錯誤直覺：「切五級，一條指令應該更快走完」。
- 正確認識：切級是為 throughput，不為 latency。範例 1 明明白白：一個 tag 要**五個 cycle**才從 IF 走到 WB，比單週期的一個 cycle 還「久」（latency 略增）。賺的是「五條指令重疊，每 cycle retire 一條」的吞吐量。Ch 13 已強調，這裡程式碼實證。

## 進階延伸

- **pipeline register 也可以打包成 struct**：本課為教學清晰，把每個訊號單獨宣告。工業級 RTL 常用 `typedef struct packed { ... }` 把一整級要傳的訊號打包成一個型別，pipeline register 就宣告成該型別的一個變數，賦值一行搞定、加欄位不用改一堆地方。verilator 4.038 支援 packed struct，但 SystemVerilog `interface`/`program` 這類建構它不吃，本課避開。想看打包寫法，讀 Ch 20 整合時會提。
- **flush 與 stall 是對 pipeline register 動手腳**：這章的 pipeline register 每個 cycle 無條件往後移。但處理 hazard 時，我們要能「凍住」某級（stall：這個 cycle 不更新，值保持不動）或「清空」某級（flush：塞進 NOP，作廢一條指令）。stall = pipeline register 加一個 enable、該 cycle 不鎖存；flush = pipeline register 加一個清零/塞 NOP 的路徑。Ch 17（load-use stall）、Ch 18（branch flush）就是在這副骨架上加這兩個動作。這章先讓它單純往後跑。
- **為什麼是五級不是四級或六級**：切點選在「延遲大致均衡」且「功能邊界自然」的地方。RISC-V 的 load/store 要獨立一級（MEM）是因為記憶體存取慢、且只有 load/store 用得到，把它和 ALU 分開讓 EX 級不被記憶體延遲拖累。register file 讀（ID）寫（WB）分兩端，中間夾 EX/MEM，是為了讓「讀-算-寫」有清楚的時間分離。這五級切法是幾十年沉澱的經典，Rocket 這類工業 core 的整數 pipeline 也基本是這個骨架加料。

## 本章重點整理

- **pipeline register** 是每兩級之間的一排正緣觸發 flip-flop，每個 clock 邊沿鎖存「上一級算好的中間值」交給下一級，讓相鄰兩級各跑不同指令而不互相覆蓋。命名 `<前級>_<後級>_reg`（`if_id_reg`/`id_ex_reg`/`ex_mem_reg`/`mem_wb_reg`）。
- 五級分工：**IF** 取指、**ID** 解碼+讀 regfile+算控制訊號+立即數、**EX** ALU 運算、**MEM** 存取記憶體、**WB** 寫回。register file 讀口在 ID、寫口在 WB，橫跨整條 pipeline。
- 最關鍵觀念：**控制訊號在 ID 算出，但要生效的級分散在後面（EX/MEM/WB），所以必須隨指令一起流過 pipeline register，一路帶到該生效的那一級**。`reg_write` 從 ID 上車，坐三個 register 到 WB 才用。
- pipeline register 一律用 **non-blocking `<=`**，四級才能「同時」交接；有副作用的控制訊號（`reg_write` 等）**reset 成關閉**，防未填滿時垃圾亂寫狀態。
- 真跑 `pipe_demo` 看到 tag 每 cycle 精準往後移一級、五個 cycle 走完全程——這是斜階梯圖的硬體實體，也印證 latency 是增的、賺的是 throughput。

## 自我檢核

- [ ] 我能說出五級各做什麼、各用到哪些硬體，並指出 register file 為什麼被 ID 和 WB 兩級碰到。
- [ ] 我能解釋為什麼控制訊號必須隨 pipeline register 一路帶下去，而不能在 ID 直接拉線到 register file 寫口。
- [ ] 我能追出 `reg_write` 這個訊號從 ID 到 WB 經過哪幾個 pipeline register、走了幾個 cycle 才生效。
- [ ] 我能解釋 pipeline register 為什麼一定用 `<=` 而非 `=`，說錯了會發生什麼。
- [ ] 我能說明有副作用的控制訊號為什麼要 reset 成關閉，不這樣做會有什麼後果。
- [ ] 我能看著 `pipe_demo` 的輸出，指出某個 tag 在第幾個 cycle 到哪一級。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.6 節「Pipelined Datapath and Control」**：本章的教科書藍本。它一步步把單週期 datapath 切成五級、逐一畫出四個 pipeline register 該裝哪些欄位，並專門用一小節講「控制訊號如何隨 pipeline 攜帶」。想看每個欄位的完整清單（含 load/store/branch），讀它。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5.1–7.5.2 小節**：從 HDL 與時序角度呈現 pipeline register，給出可合成的 SystemVerilog 片段，和本章的 `always_ff` 寫法一脈相承。適合對照本章程式碼看「教科書怎麼寫 pipeline register」。
- **[Sodor 五級 core（rv32_5stage）的 cpath.scala / dpath.scala](https://github.com/ucb-bar/riscv-sodor)**：Chisel 寫的教學五級 core。dpath 是資料路徑、cpath 是控制路徑，兩者分開正對應本章「資料 vs 控制訊號」的切分。看它怎麼把控制訊號打包隨管線傳，是本章觀念的「標準答案」實作。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) Appendix C.2「The Major Hurdle of Pipelining—Pipeline Hazards」的前半**：在正式進 hazard 前，它先把「pipeline register 存了什麼、資源如何在各級間分配」講清楚。這章的骨架搭好後讀它，正好銜接下一章要爆的 hazard。

骨架搭好了：五級切開、pipeline register 就位、控制訊號會隨管線走。下一章我們把它接上真正的 datapath，跑一段有資料相依的程式——然後你會親眼看到它**算錯**。那個錯，就是我們接下來三章要對付的 hazard。

→ [Ch 15 naive pipeline：先切開，故意跑錯給你看](./15-naive-pipeline.md)
