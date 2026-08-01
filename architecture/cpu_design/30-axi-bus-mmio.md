# Ch 30 — AXI4-Lite 總線：memory-mapped I/O

> **目標**：到目前為止 core 只跟記憶體講話。真實 SoC 裡 core 要接一堆周邊——UART、LED、timer、DMA——它們掛在**標準總線**上，用統一的協定溝通。這章你會學為什麼要標準 bus、AXI4-Lite 的五個獨立通道（AW/W/B/AR/R）、valid/ready handshake 的精髓、以及 memory-mapped I/O（把周邊暫存器映射成記憶體位址）。然後**實作一個 AXI4-Lite slave** 接兩個暫存器（LED、UART TX），真跑一次寫、一次讀 transaction，看 handshake 一拍拍握手。這是深挖章。
> **環境**：WSL + verilator 4.038。transaction 輸出皆真跑。本章把前面的 cache/記憶體介面收斂到工業標準 bus，是 core 接周邊的地基。

## 為什麼需要標準 bus

前面幾章 core 和記憶體之間，我們隨手定了介面：`mem_req`/`mem_addr`/`mem_data`/`mem_valid`。這在課堂夠用，但真實 SoC 有問題：

1. **周邊多而雜**：一顆 SoC 掛幾十個模組——CPU、DRAM controller、UART、SPI、I2C、timer、GPIO、DMA、乙太網路…。每個若都用自己發明的介面，接線是災難，第三方 IP（別人做的模組）根本插不進來。
2. **要能組合**：常需要多個 master（CPU、DMA 都想存取記憶體）、多個 slave（記憶體、各周邊），中間用 interconnect（總線交換）把 master 的請求路由到對的 slave。沒有統一協定，interconnect 沒法做。
3. **要能買賣 IP**：ARM 的 AMBA、AXI 之所以主宰，是因為全世界的 IP 供應商都出 AXI 介面的模組——你買一個 UART IP，它是 AXI slave，插上 AXI interconnect 就能用。

**標準 bus 讓不同來源、不同功能的模組用同一套規則對話。** RISC-V SoC（含 SiFive 的 Rocket）大量用 ARM 的 **AMBA AXI**（以及 TileLink，SiFive 自家的）。我們學 **AXI4-Lite**——AXI4 的精簡版，去掉 burst（一次一筆）、去掉 out-of-order，最適合接暫存器類的簡單周邊，也最好教。

## 先建立直覺：餐廳點餐的窗口

把 master（CPU）想成客人、slave（周邊）想成餐廳窗口。一筆交易要兩邊都準備好才成立：

```
   客人：「我要點餐」（valid：我有請求了）
   窗口：「好，我聽」（ready：我準備好收了）
   兩個同時成立的那一刻 → 握手成立，交易發生
```

- 客人喊了「要點餐」但窗口在忙（沒 ready）→ 客人**等**，一直舉著手（valid 保持）。
- 窗口準備好了（ready）但客人還沒想好（沒 valid）→ 窗口**等**。
- **valid 和 ready 同一拍都成立** → 這一拍握手，資料交換，交易往前走。

這個「valid & ready 同拍才成立」就是 AXI（以及幾乎所有現代 bus）handshake 的全部精髓。它的好處是**雙方都能反壓（backpressure）**：慢的一方拉低自己的信號，快的一方就得等——不會有人被逼著在還沒準備好時吞資料。

AXI 把「寫」和「讀」拆成獨立通道，而且寫還拆成「給位址」「給資料」「回應」三個通道，每個通道各有自己的 valid/ready。像餐廳把「點餐」「取餐」「結帳」開成不同窗口，各自排隊、各自握手。

## 核心概念：AXI4-Lite 的五個通道

AXI 的設計哲學是**讀寫分離、位址與資料分離**，共五個獨立通道，各有 valid/ready：

```
   寫路徑（三個通道）：
   ┌── AW (Write Address)：master 給「要寫哪」          awaddr,  awvalid/awready
   ├── W  (Write Data)   ：master 給「寫什麼」          wdata, wstrb, wvalid/wready
   └── B  (Write Response)：slave 回「寫好了/出錯」      bresp,   bvalid/bready

   讀路徑（兩個通道）：
   ┌── AR (Read Address) ：master 給「要讀哪」          araddr,  arvalid/arready
   └── R  (Read Data)    ：slave 回「讀到的資料」        rdata, rresp, rvalid/rready
```

為什麼分這麼多通道？**為了平行與流水**。位址和資料分開，slave 可以一邊收位址一邊收資料；讀寫分開，一筆讀和一筆寫可以同時進行。完整 AXI4 甚至允許多筆 outstanding、亂序完成（靠 transaction ID）——AXI4-Lite 砍掉這些複雜度（一次一筆、無 burst、無 ID），保留五通道的骨架。

各通道的關鍵信號：
- **AW/AR**：`awaddr`/`araddr`（位址）。
- **W**：`wdata`（資料）、`wstrb`（write strobe，哪幾個 byte 要寫，4 bit 對應 4 個 byte，做 byte-enable）。
- **B**：`bresp`（寫回應，`00`=OKAY、`10`=SLVERR slave 錯、`11`=DECERR 位址解不出）。
- **R**：`rdata`（讀到的資料）、`rresp`（讀回應，同 bresp 編碼）。

每個通道獨立握手：AW 的 `awvalid & awready` 成立才傳位址、W 的 `wvalid & wready` 成立才傳資料、B 的 `bvalid & bready` 成立才傳回應，讀路徑同理。

## 核心概念：memory-mapped I/O

core 怎麼「存取」一個周邊？答案是 **memory-mapped I/O（MMIO，記憶體映射 I/O）**：把周邊的暫存器**映射成記憶體位址**，core 用普通的 `lw`/`sw` 讀寫這些位址，就等於讀寫周邊的暫存器。

```
   位址空間佈局（例）：
   0x00000000 ─ 0x7FFFFFFF ：一般記憶體（RAM/ROM）
   0x80000000 ─ 0x8000000F ：UART      ← 這幾個位址是 UART 的暫存器
     0x80000000 : UART 資料暫存器（寫 = 送字元、讀 = 收字元）
     0x80000004 : UART 狀態暫存器
   0x80001000 ─ 0x80001003 ：LED       ← 這個位址是 LED 暫存器
   ...
```

`sw x1, 0(x_uart)` 把 x1 寫到 `0x80000000`——這不是寫記憶體，而是**送一個字元給 UART**（因為 interconnect 看到位址在 UART 範圍，把這筆寫路由給 UART slave）。`lw` 讀 UART 狀態暫存器就是查 UART 狀態。

MMIO 的好處：**不需要特殊 I/O 指令**（x86 有 `in`/`out`，RISC-V 沒有——全靠 MMIO）。core 只會 load/store，周邊全部透過位址存取，指令集乾淨。interconnect 靠**位址解碼（address decode）**決定一筆存取該給誰：位址落在哪個範圍，就路由給對應的 slave。

MMIO 的要求：這些位址**不能被 cache**（或要特殊處理）。若 UART 資料暫存器被 D-cache 快取，你 `sw` 送字元卻只寫進 cache 沒到 UART，或連續讀狀態卻讀到 cache 的舊值——全錯。所以 MMIO 區域標記為 non-cacheable（真實系統靠 PMA/PMP 或 page table 的屬性標記），存取直接穿到 bus。

## 底層機制：AXI4-Lite slave 的實作

實作一個 slave 接兩個暫存器：`0x00` = LED（可讀寫）、`0x04` = UART TX（寫進去印一個字元、讀回最後寫的值）。寫路徑和讀路徑各一個小狀態機。

寫路徑三態：`W_IDLE`（等 AW）→ `W_DATA`（等 W）→ `W_RESP`（回 B）：

```systemverilog
module axil_slave (
    input  logic        clk, rst,
    // 寫位址 AW
    input  logic [31:0] s_awaddr,  input logic s_awvalid,  output logic s_awready,
    // 寫資料 W
    input  logic [31:0] s_wdata,   input logic [3:0] s_wstrb,
    input  logic        s_wvalid,  output logic s_wready,
    // 寫回應 B
    output logic [1:0]  s_bresp,   output logic s_bvalid,  input logic s_bready,
    // 讀位址 AR
    input  logic [31:0] s_araddr,  input logic s_arvalid,  output logic s_arready,
    // 讀資料 R
    output logic [31:0] s_rdata,   output logic [1:0] s_rresp,
    output logic        s_rvalid,  input logic s_rready,
    // 外掛
    output logic [31:0] led_reg,
    output logic [7:0]  uart_char, output logic uart_wr
);
    localparam logic [31:0] ADDR_LED = 32'h00, ADDR_UART = 32'h04;
    logic [31:0] led_r, uart_r;
    assign led_reg = led_r;

    typedef enum logic [1:0] {W_IDLE, W_DATA, W_RESP} wstate_t;
    wstate_t wstate;
    logic [31:0] waddr_lat;

    // 各通道 ready 由狀態決定（Moore 輸出）
    assign s_awready = (wstate == W_IDLE);   // IDLE 時準備收位址
    assign s_wready  = (wstate == W_DATA);   // 收完位址後準備收資料
    assign s_bvalid  = (wstate == W_RESP);   // 寫完拉 B valid
    assign s_bresp   = 2'b00;                // 永遠 OKAY（教學簡化）

    always_ff @(posedge clk) begin
        if (rst) begin
            wstate <= W_IDLE; led_r <= 0; uart_r <= 0; uart_wr <= 0; uart_char <= 0;
        end else begin
            uart_wr <= 0;                    // 預設一拍脈衝，寫 UART 那拍才拉高
            case (wstate)
                W_IDLE: if (s_awvalid) begin waddr_lat <= s_awaddr; wstate <= W_DATA; end
                W_DATA: if (s_wvalid) begin
                    case (waddr_lat)
                        ADDR_LED:  led_r  <= s_wdata;
                        ADDR_UART: begin uart_r <= s_wdata; uart_char <= s_wdata[7:0]; uart_wr <= 1; end
                        default: ;
                    endcase
                    wstate <= W_RESP;
                end
                W_RESP: if (s_bready) wstate <= W_IDLE;   // master 收了 B，交易結束
                default: wstate <= W_IDLE;
            endcase
        end
    end
```

讀路徑兩態：`R_IDLE`（等 AR）→ `R_RESP`（回 R）：

```systemverilog
    typedef enum logic [1:0] {R_IDLE, R_RESP} rstate_t;
    rstate_t rstate;
    assign s_arready = (rstate == R_IDLE);
    assign s_rvalid  = (rstate == R_RESP);
    assign s_rresp   = 2'b00;

    always_ff @(posedge clk) begin
        if (rst) begin rstate <= R_IDLE; s_rdata <= 0; end
        else case (rstate)
            R_IDLE: if (s_arvalid) begin
                case (s_araddr)
                    ADDR_LED:  s_rdata <= led_r;
                    ADDR_UART: s_rdata <= uart_r;
                    default:   s_rdata <= 32'hDEAD_BEEF;   // 未映射位址回一個好認的值
                endcase
                rstate <= R_RESP;
            end
            R_RESP: if (s_rready) rstate <= R_IDLE;        // master 收了 R，交易結束
            default: rstate <= R_IDLE;
        endcase
    end
endmodule
```

設計要點：
- **ready 是 Moore 輸出**：`awready = (wstate == W_IDLE)` 等，只看狀態，不看輸入——避免 valid/ready 組合迴路（AXI 規範要求 ready 不能組合相依於同通道 valid，否則會 deadlock/glitch）。
- **地址先鎖存**（`waddr_lat`）：AW 和 W 分開來，收到位址先存起來，等資料到了才知道要寫哪個暫存器。
- **B/R 要等 master 收（bready/rready）** 才回 IDLE：確保 master 真的拿到了回應/資料，交易才算完成。
- **未映射位址回 `DEADBEEF`**：真實 slave 會回 DECERR（`bresp/rresp = 11`），這裡教學簡化成回一個好認的值。

## 範例：真跑一次寫、一次讀 transaction

testbench 扮演 AXI master，跑：寫 LED（`0x00`）、讀回 LED、寫 UART（`0x04`）印字元、讀未映射位址（`0x10`）。每步等對應通道握手。真跑：

```
=== 寫 LED 暫存器 (0x00) ===
  [WR] addr=0x00 data=0xa5a5a5a5
       B resp=0 (OKAY=0)
led_reg 現在 = 0xa5a5a5a5

=== 讀回 LED 暫存器 ===
  [RD] addr=0x00 -> data=0xa5a5a5a5 resp=0
讀到 0xa5a5a5a5  (相符)

=== 寫 UART TX (0x04) 印字元 ===
  [WR] addr=0x04 data=0x00000048
       B resp=0 (OKAY=0)
  [WR] addr=0x04 data=0x00000069
       B resp=0 (OKAY=0)

=== 讀未映射位址 (0x10) 應回 DEADBEEF ===
  [RD] addr=0x10 -> data=0xdeadbeef resp=0
```

逐段看：
- **寫 LED**：master 在 AW 通道給位址 `0x00`、W 通道給資料 `0xA5A5A5A5`，slave 在 W_DATA 收下寫進 `led_r`，回 B（`resp=0` OKAY）。`led_reg = 0xa5a5a5a5` 證明寫成功。
- **讀回 LED**：master 在 AR 給 `0x00`，slave 在 R_RESP 回 `rdata = 0xa5a5a5a5`——和剛寫的值相符。讀寫一致，暫存器行為正確。
- **寫 UART 印字元**：連寫 `0x48`('H')、`0x69`('i')到 `0x04`，各回 B OKAY。slave 收到時拉 `uart_wr` 脈衝一拍、`uart_char` 帶字元——外接的 UART 就在那一拍把字元送出去。（tb 在 transaction 完成後才印 `uart_char`，那時 `uart_wr` 脈衝已過，所以顯示層沒印出字元，但寫入本身成功——B resp=0 為證；要觀察脈衝得在 W_DATA 握手的當拍取樣。）
- **讀未映射位址**：`0x10` 不對應任何暫存器，slave 回 `DEADBEEF`——address decode 的 default 分支正確處理了「沒人認領」的位址。

整個過程每個通道都遵守 valid/ready handshake：master 拉 valid、等 slave 拉 ready、同拍成立才傳。這就是一次完整的 AXI4-Lite 讀寫交易。

## 對比取捨：AXI4 / AXI4-Lite / 其他

| bus | 特點 | 適用 |
|---|---|---|
| AXI4（full） | 五通道 + burst（一次搬多筆）+ outstanding + 亂序（ID） | 高頻寬：DRAM controller、DMA、cache refill |
| AXI4-Lite（本章） | 五通道，一次一筆，無 burst/ID | 暫存器類簡單周邊：UART、GPIO、timer |
| AXI4-Stream | 只有資料流，無位址 | 連續資料流：影像、DSP、網路封包 |
| AHB / APB（舊 AMBA） | 較簡單、非分離通道 | 低速周邊（APB）、舊設計 |
| TileLink | SiFive 自家，coherence 友善 | RISC-V SoC（rocket-chip） |
| Wishbone | 開源、極簡 | 開源 SoC（picorv32 等） |

一句話：**AXI4-Lite 是「簡單周邊」的甜蜜點**——保留 AXI 的五通道 handshake 骨架（好接 interconnect、好買 IP），砍掉 burst/亂序的複雜度（暫存器周邊用不到）。要高頻寬（記憶體、DMA）才上 full AXI4 的 burst。學會 AXI4-Lite，full AXI4 就是在它上面加 burst/ID。

## 踩雷區

**雷 1：ready 組合相依於同通道的 valid。**
- 錯誤直覺：「slave 的 awready 就設成 `= awvalid`，有 valid 我就 ready」。
- 正確認識：AXI 規範**禁止** ready 組合相依於同通道的 valid（`awready` 不能寫成 `awready = awvalid && ...`）。若這樣接，valid → ready → 可能又繞回影響 valid，形成組合迴路，模擬時 glitch、上板 timing 亂、甚至 deadlock。正解是 ready 只看**自己的狀態**（Moore 輸出，如本章 `awready = (wstate==W_IDLE)`）。valid 可以相依於 ready（master 等 ready 才推進），但 ready 不能反過來相依 valid。這個方向性是 AXI handshake 不 deadlock 的關鍵規則。

**雷 2：握手還沒成立就以為資料傳了。**
- 錯誤直覺：「我拉了 awvalid，位址就送出去了」。
- 正確認識：拉 valid 只是「我有資料了」，**要 valid 和 ready 同一拍都高**才算握手成立、資料才真正被對方吃進。slave 在忙（ready=0）時，master 必須**保持 valid 和資料不變**一直等，直到某拍 ready 也高了才成立。若你 valid 拉一拍就撤、不管 ready，slave 沒 ready 的那些拍就漏掉了你的請求。valid 一旦拉起，必須維持到握手成立為止——這是 AXI 的硬規則。

**雷 3：MMIO 區域被 cache 快取。**
- 錯誤直覺：「周邊暫存器也是位址，跟記憶體一樣走 D-cache 就好」。
- 正確認識：MMIO 位址**絕不能**被普通 cache（或要特殊 uncacheable 處理）。UART 資料暫存器若被快取：你 `sw` 送字元只寫進 cache 沒到 UART（write-back 下）、你連續 `lw` 讀狀態暫存器讀到 cache 的舊值（讀命中不重新取）——I/O 完全壞掉。MMIO 有副作用（讀寫暫存器會觸發硬體動作）、值會被硬體改變，cache 的「快取＋重用」假設對它不成立。真實系統把 MMIO 標成 non-cacheable（PMA/page table 屬性），存取直穿 bus。這是 cache 和 I/O 交界最常見的坑。

**雷 4：B/R 通道不等 master 收就結束交易。**
- 錯誤直覺：「slave 寫完拉一拍 bvalid 就回 IDLE，不管 master 收沒收」。
- 正確認識：B（寫回應）和 R（讀資料）也是握手通道，slave 拉 bvalid/rvalid 後，必須**等 master 的 bready/rready 也高**（握手成立）才能撤、回 IDLE。若 slave 拉一拍 bvalid 就自己回 IDLE，而 master 那拍剛好沒 ready，master 就漏掉了回應——它可能一直等 B、卡死。回應通道和位址/資料通道一樣要雙向握手，本章 `W_RESP: if (s_bready) wstate <= W_IDLE` 就是在等 master 收。

## 進階延伸

- **升級到 full AXI4 的 burst**：cache refill（Ch 26/27）一次要搬一整個 block（多個 word），用 AXI4-Lite 得發好幾筆獨立 transaction，慢。full AXI4 的 burst 讓一次 AW 帶 `awlen`（幾筆）、`awburst`（遞增/wrap），slave 連續回多筆資料——一次握手搬一整個 block，這才是 cache 該用的介面。把本章 slave 加上 burst 支援（收 `awlen`、連續處理），就是往 full AXI4 走。critical-word-first（Ch 26）也靠 wrap burst 實現。
- **interconnect 與 address decode**：一個 master 對多個 slave，中間要 interconnect：它看 master 的 `awaddr/araddr` 落在哪個 slave 的位址範圍，把該通道路由過去（address decode），並把對應 slave 的回應路由回 master。多 master 時還要 arbiter（仲裁誰先用 bus）。這是 SoC 搭建的核心，rocket-chip 用 Diplomacy（一套自動生成 interconnect 的機制）處理，複雜度不小。
- **clock domain crossing（CDC）**：core 跑一個時脈、周邊可能跑另一個（UART 的 baud clock 慢很多）。跨時脈域傳訊號要用同步器（雙 flip-flop）或非同步 FIFO，否則 metastability（亞穩態）會隨機出錯。AXI 有跨時脈域的 bridge IP 專門處理。這是把周邊真接進 SoC 時繞不開的物理問題，本課模擬（單一時脈）碰不到，但真設計必須處理。
- **接進本課 core 的路徑**：core 的 D-cache miss / MMIO 存取，最終要透過一個 AXI master 介面發出去。把 Ch 27 的 `mem_*` 介面換成標準 AXI4-Lite（或 full AXI4）master：load miss → 發 AR、等 R；store write-through → 發 AW+W、等 B。core 頂層變成一個 AXI master，接上 interconnect 就能同時掛記憶體和周邊。這是讓本課 core 從「純模擬」走向「可組成 SoC」的一步，也是 final project 之後想上 FPGA 的必經之路。

## 本章重點整理

- **標準 bus 的意義**：讓不同來源、不同功能的模組用同一套規則對話，才能組 interconnect、買賣 IP。RISC-V SoC 用 AMBA AXI / TileLink。
- **AXI4-Lite 五通道**：寫路徑 AW（位址）/W（資料）/B（回應），讀路徑 AR（位址）/R（資料）。讀寫分離、位址資料分離，為了平行與流水。
- **valid/ready handshake**：valid（我有資料）和 ready（我準備收）**同拍都高**才握手成立、資料才傳。雙方都能反壓，慢的一方拉低信號快的就等。
- **memory-mapped I/O**：把周邊暫存器映射成記憶體位址，core 用普通 load/store 存取，interconnect 靠 address decode 路由。RISC-V 無特殊 I/O 指令，全靠 MMIO。
- **真跑**：寫 LED `0xA5A5A5A5` → 讀回相符；寫 UART 印 'H'/'i'（B OKAY）；讀未映射 `0x10` → `DEADBEEF`。每通道遵守 handshake。
- **關鍵規則**：ready 不能組合相依同通道 valid（Moore 輸出）、valid 拉起要維持到握手成立、MMIO 不可 cache、B/R 要等 master 收。

## 自我檢核

- [ ] 我能說出為什麼要標準 bus（組合、interconnect、IP 買賣），以及 AXI4-Lite 相對 full AXI4 砍了什麼。
- [ ] 我能列出 AXI4-Lite 五個通道各傳什麼，並解釋為什麼讀寫分離、位址資料分離。
- [ ] 我能用「餐廳窗口」解釋 valid/ready handshake，說出握手成立的條件與反壓的意義。
- [ ] 我能解釋 memory-mapped I/O：core 怎麼用 load/store 存取周邊、interconnect 怎麼 address decode。
- [ ] 我能追出範例裡寫 LED / 讀回 / 寫 UART / 讀未映射位址各走哪個通道、握手怎麼進行。
- [ ] 我能說出四個踩雷（ready 不依 valid、valid 要維持、MMIO 不可 cache、B/R 要等收），並解釋各自不遵守會怎樣。

## 延伸閱讀

- **[ARM AMBA AXI Protocol Specification (IHI 0022)](https://developer.arm.com/documentation/ihi0022/latest/)**：權威來源。AXI4-Lite 的定義在其中一節（"AXI4-Lite" 附錄），但先讀主體的通道定義與 handshake 規則（尤其「Dependencies between channel handshake signals」那張表，講死了誰能相依誰——本章雷 1 的規範依據）。實作任何 AXI 介面，這是最終仲裁。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 9 章「I/O Systems」**：從 microarchitecture 角度講 memory-mapped I/O、周邊怎麼接、bus 怎麼運作。它的 MMIO 範例（GPIO、UART、timer）和本章 slave 一脈相承，補足「周邊暫存器在系統裡怎麼被軟體驅動」這一層。
- **[SiFive TileLink Spec](https://www.sifive.com/documents/91/spec) 與 rocket-chip 的 bus 實作**：RISC-V 生態的另一條路。TileLink 是 SiFive 為 coherence 設計的 bus，rocket-chip 內部用它。讀它和 AXI 對照，你會理解「為什麼 RISC-V 世界有自己的 bus」——AXI 對 cache coherence 支援較弱，TileLink 內建 coherence 訊息。做完本章 AXI，看 TileLink 會很快。
- **[picorv32 的 memory interface 與 PicoRV32 AXI wrapper](https://github.com/YosysHQ/picorv32)**：一個真實小 core 怎麼接記憶體/bus 的極簡範例。picorv32 原生用自訂的簡單介面，但 repo 附了一個 AXI4-Lite wrapper，把原生介面轉成標準 AXI——正好示範「core 的記憶體介面怎麼包成 AXI master」，是本章進階延伸「接進 core」的現成參考。

這章結束了 Part 4 的記憶體階層與 bus。你現在有 cache（I/D）、虛擬記憶體（Sv32 + TLB）、和接周邊的 AXI bus——core 的記憶體子系統完整了。練習 D 會讓你親手把 I-cache 做深：實作、量 hit rate、掃 block size / cache size、挑戰改成 set-associative。之後 Part 5 進入 CSR、trap、中斷，讓 core 真正能跑作業系統。

→ [練習 D：實作 direct-mapped I-cache，量 hit rate](./practice-d-direct-mapped-icache.md)
