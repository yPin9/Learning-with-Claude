# Ch 37 — Rocket / BOOM 巡禮：真實 SiFive core 長怎樣

> **目標**：我們手刻了一顆 in-order 五級 RV32I core，Ch 36 又走過 out-of-order 的概念。這章把兩者接到**真實世界**：帶你認識 **Rocket**（in-order，Chisel 寫的教學到量產都用的 core，正好對照本課）和 **BOOM / SonicBOOM**（out-of-order，正好對照 Ch 36）。你會看到 Rocket 比我們的 core 多了什麼、BOOM 怎麼把 Ch 36 的 renaming/ROB 落成硬體、**Chisel 是什麼**（給小段 Chisel 示意並明確標示**這不是本課的 SystemVerilog**）、rocket-chip 專案的結構、以及**怎麼 clone 下來讀**。這是一章指路章——目的是讓你讀完後有能力自己去挖真 core 的原始碼。
>
> **環境**：本章以讀原始碼、認識生態為主，不跑 RTL。給的 Chisel 片段是示意（來自 rocket-chip / riscv-boom 公開原始碼的簡化），**本課全程 SystemVerilog，不要求你會寫 Chisel**。

## 為什麼需要：從「我做的玩具」到「業界怎麼做」

本課刻意選 SystemVerilog + verilator，章數精簡好除錯。但你若要繼續往硬體走——貢獻開源 core、做 SoC、面試 CPU 設計職位——你會撞見一個事實：**RISC-V 開源硬體生態的重心在 Chisel，尤其是 rocket-chip 這個專案。** SiFive 的量產晶片、Berkeley 的研究 core、無數學術論文的實驗平台，都長在 rocket-chip 上。

我們不改用 Chisel（那是另一套語言 + 一整個學習曲線），但你**必須認得它、讀得懂它的大意**，才能：

1. 拿真 core 對照自己做的——「原來我少了 FPU、少了 PLIC、少了 TileLink」。
2. 把 Ch 36 的抽象概念（renaming、ROB、LSQ）對到**看得見、clone 得到**的程式碼。
3. 知道往下走的路標——想深入 OoO、想上真 FPGA、想跑 Linux，rocket-chip 是公認的起點。

這章不教你寫 Chisel，教你**認路**。

## 先建立直覺：三顆 core 的家譜

```
   picorv32 ─── 極簡、面積優先、Verilog 手寫，非 pipeline（多週期）
                 └ 適合塞進 FPGA 角落當控制器

   本課 core ── in-order 5-stage RV32I，SystemVerilog，教學用
                 └ 你親手做的，dcache/VM/CSR/trap 都有

   Rocket ───── in-order 5-stage（可 6），RV64GC，Chisel，量產級
                 └ 比本課多：FPU、完整特權/PMP、TileLink、可跑 Linux
                    │
                    └ 共用 rocket-chip 的 SoC 框架（bus/debug/PLIC/CLINT）
                    │
   BOOM ─────── out-of-order superscalar，RV64GC，Chisel，研究/展示級
                 └ Ch 36 那些 renaming/ROB/LSQ 的真身
                    掛在同一個 rocket-chip 框架裡，把 Rocket 換成 OoO 核
```

關鍵洞見：**Rocket 和 BOOM 共用同一個 SoC 框架（rocket-chip）**，只是把「核心那塊 tile」換掉。這個模組化是 Chisel 生態的殺手鐧——bus、debug module、中斷控制器、記憶體介面都是共用元件，換核心像換引擎不換底盤。

## 核心概念：Rocket——本課 core 的「量產版」對照

Rocket 是 in-order、5 級（存取記憶體那級可拆成 6）的 RV64GC core。它的骨架和你做的**驚人地像**：Fetch → Decode → Execute → Memory → Writeback，一樣有 forwarding、一樣有 hazard stall、一樣有 branch 預測。把它當成「你的 core 認真做完、補齊所有 production 細節」的樣子。

Rocket 比本課 core 多了什麼（這張表是本章的重點對照）：

| 面向 | 本課 core | Rocket | 差在哪 |
|---|---|---|---|
| ISA 寬度 | RV32I | RV64GC（G = IMAFD，C = 壓縮） | Rocket 有乘除、浮點、原子、壓縮指令 |
| 浮點 | 無 | 完整 FPU（F/D，含 FMA 流水線） | 一整塊獨立浮點 pipeline |
| 分支預測 | 基本 BHT/BTB（Ch 21-22） | BTB + BHT + RAS，可配置 | 更完整、可參數化 |
| cache | 教學 I/D cache | 非阻塞 D-cache（miss 下仍能服務命中） | non-blocking，撐 miss under miss |
| 特權 / 例外 | M（Ch 31-35 做的） | M/S/U 全套 + PMP | 能跑 Linux（要 S 模式 + MMU） |
| MMU | Sv32（Ch 28-29） | Sv39/Sv48（RV64） | 64-bit 位址空間分頁 |
| bus | 教學 AXI4-Lite（Ch 30） | **TileLine（TileLink）** | SiFive 自家一致性總線 |
| 中斷 | 簡化 CLINT/PLIC | 完整 CLINT + PLIC | 標準平台中斷 |
| 除錯 | 無 | RISC-V Debug（JTAG halt/step） | 能用 OpenOCD + gdb 上板調 |
| 可配置 | 手改 SV | Chisel 參數（Config 系統） | 一行參數換 cache 大小 / 有無 FPU |

看這張表你該有的感覺：**你做的 core 抓住了骨架的全部精髓（pipeline、hazard、cache、VM、trap），Rocket 多的是「量產化」的深度與廣度**——不是概念上更難，是把每一塊做全、做對、做可配置。你已經懂了 Rocket 的每一個核心概念，差的是工程完整度。

Rocket 的一個關鍵設計哲學：**參數化（parameterization）**。cache 幾路、多大、有沒有 FPU、要不要 BOOM 換上來——全靠 Chisel 的 Config 系統一組參數決定，同一份原始碼生出千百種變體。這是 Chisel 相對手寫 SV 的最大優勢，也是它值得認識的理由。

## 核心概念：BOOM——Ch 36 的真身

BOOM（Berkeley Out-of-Order Machine，最新代 SonicBOOM）是把 Ch 36 全部概念落成硬體的**公開、可讀、可 clone** 的 OoO core。你在 Ch 36 手推的 renaming、reservation station、ROB、LSQ，在 BOOM 裡都有對應的 Chisel 模組：

```
   Ch 36 概念              BOOM 裡的模組（Chisel）
   ─────────────────────────────────────────────────
   register renaming    →  RenameStage / RenameMapTable / RenameFreeList
   reservation station  →  IssueUnit（含喚醒邏輯）
   物理暫存器 file       →  RegisterFile（int / fp 分開）
   ROB（循序退休）       →  Rob.scala
   load/store queue     →  LSU（LoadQueue + StoreQueue）
   分支預測 + 回滾       →  BranchPredictor + BranchMaskGenerationLogic
   CDB 廣播喚醒          →  writeback + wakeup ports
```

BOOM 是 superscalar（可配 2/3/4/5 寬發射）、亂序，掛在 rocket-chip 框架上——**把 Rocket 那個 tile 換成 BOOM tile，其餘 SoC（bus/PLIC/debug）不動**。這具體示範了 Ch 36 的「亂序執行、循序退休」在真實 Chisel 程式碼裡怎麼組織。

BOOM 的 pipeline 比本課多好幾級（fetch 深、rename 一級、dispatch/issue 分開、writeback、commit），因為 OoO 的每個階段都比 in-order 重。它的技術報告（docs.boom-core.org）逐段講每個 stage，是把 Ch 36 從概念變成「看得見的 stage 圖」的最佳材料。

**誠實提醒**：讀懂 BOOM 的架構圖不難（Ch 36 的概念都在），但讀懂它每一行 Chisel、甚至改它，是 Ch 36 說的「另一門大課」。這章的目標是讓你**認得出**「這塊是 rename、這塊是 ROB」，不是讓你會改。

## 核心概念：Chisel 是什麼（示意，非本課語言）

Chisel（Constructing Hardware In a Scala Embedded Language）**不是新的 HDL，是一個 Scala 函式庫**。你用 Scala 寫程式，這程式**跑起來會產生 Verilog**。所以 Chisel 是「用軟體語言生成硬體描述」的元編程（hardware generator），最終出來的還是 Verilog，交給一樣的 synthesis 工具。

看一段本課 ALU 的 Chisel 示意，對照你在 Ch 9 寫的 SystemVerilog：

```scala
// 這是 Chisel（Scala），示意用，不是本課的 SystemVerilog！
import chisel3._
import chisel3.util._

class ALU extends Module {
  val io = IO(new Bundle {
    val a      = Input(UInt(32.W))
    val b      = Input(UInt(32.W))
    val alu_op = Input(UInt(4.W))
    val result = Output(UInt(32.W))
    val zero   = Output(Bool())
  })
  val shamt = io.b(4, 0)
  io.result := MuxLookup(io.alu_op, 0.U)(Seq(
    "b0000".U -> (io.a + io.b),                        // ADD
    "b0001".U -> (io.a - io.b),                        // SUB
    "b0010".U -> (io.a << shamt),                      // SLL
    "b0101".U -> (io.a ^ io.b),                        // XOR
    "b1000".U -> (io.a | io.b),                        // OR
    "b1001".U -> (io.a & io.b)                         // AND
  ))
  io.zero := (io.result === 0.U)
}
```

對照本課 Ch 9 的 SystemVerilog：

```systemverilog
// 這是本課用的 SystemVerilog（你熟悉的）
module alu (
    input  logic [31:0] a, b,
    input  logic [3:0]  alu_op,
    output logic [31:0] result,
    output logic        zero
);
    logic [4:0] shamt;
    assign shamt = b[4:0];
    always_comb begin
        unique case (alu_op)
            4'b0000: result = a + b;
            4'b0001: result = a - b;
            // ...
            default: result = 32'd0;
        endcase
    end
    assign zero = (result == 32'd0);
endmodule
```

差異一眼可見：

| 面向 | Chisel | 本課 SystemVerilog |
|---|---|---|
| 本質 | Scala 函式庫，**生成** Verilog | 直接是 HDL，直接餵 synthesis |
| 連接運算子 | `:=`（reg）、`<>`（bulk） | `assign` / `<=` / `=` |
| 相等比較 | `===`（硬體）、`==`（Scala 值） | `==` |
| 型別 | `UInt/SInt/Bool/Bundle` | `logic/wire/reg` |
| 參數化 | Scala 的全部威力（class/繼承/for/泛型） | `parameter` + generate（弱很多） |
| 學習曲線 | 要會 Scala + Chisel + FIRRTL 工具鏈 | 標準 HDL，verilator 直跑 |

**為什麼業界（尤其學界 RISC-V）愛 Chisel？** 就是那個「參數化」——用 Scala 的 for 迴圈生成 N 路 cache、用繼承組合不同 config、一份原始碼生千種變體。手寫 SV 做同樣事要靠又醜又弱的 generate。代價是多學一套語言 + FIRRTL 編譯流程，除錯時你看到的是**生成出來的** Verilog（變數名被改過），比直接寫 SV 難追。

**本課選 SV 不選 Chisel 是刻意的**：教學要的是「每一行你都看得懂、verilator 直接跑、波形直接對」，不是「一份原始碼生千種變體」。等你把 in-order core 的每個概念吃透（也就是現在），再去學 Chisel 才有意義——你會知道那些 Chisel 抽象底下生成的是什麼。

## 核心概念：rocket-chip 專案結構

rocket-chip 是「生成一顆完整 SoC」的框架，不只是一顆 core。clone 下來大概長這樣：

```
   rocket-chip/
   ├── src/main/scala/
   │   ├── rocket/          ← Rocket core 本體（RocketCore.scala, IBuf, CSR, PTW...）
   │   ├── tile/            ← tile：把 core + L1 cache + PTW 包成一個可換的單元
   │   ├── tilelink/        ← TileLink 總線協定實作（對應本課 AXI 那層）
   │   ├── diplomacy/       ← 「外交」機制：模組間協商參數/連接（Chisel 特有）
   │   ├── devices/         ← debug module, CLINT, PLIC, UART...
   │   ├── system/          ← 把上面全部組成完整 SoC 的頂層
   │   └── subsystem/       ← bus 拓撲、記憶體 map 配置
   ├── generators/          ← （用 Chipyard 時）boom 等外掛 generator
   ├── vsim/  emulator/     ← verilator / VCS 模擬環境
   └── bootrom/             ← 開機 ROM（跳到 0x80000000，跟本課 reset 一致！）
```

幾個對照本課的觀察：

- **`bootrom/`** 讓 core reset 後跳到 `0x80000000`——**和本課 Ch 7 定的 reset PC 一模一樣**（RISC-V 慣例）。
- **`rocket/RocketCore.scala`** 就是那顆 in-order pipeline，你能在裡面找到 forwarding、stall、和你做的一樣的東西，只是 Chisel 寫、更完整。
- **`tile/`** 是模組化的關鍵：一個 tile = core + L1 I/D cache + PTW + 中斷介面。要換成 BOOM，就是換 tile。
- **`tilelink/`** 是 SiFive 版的「Ch 30 AXI」——一致性總線協定。概念（位址/資料通道、handshake）你懂，細節不同。
- **`diplomacy/`** 是 Chisel 生態獨有的「參數協商」機制，讓模組在生成時自動談好位址空間、bus 寬度。這是本課沒有、也不需要的抽象。

**Chipyard** 是更上層的傘狀專案，把 rocket-chip、BOOM、各種加速器、FPGA/ASIC 流程都整合進來——你若要真的把 BOOM 跑上 FPGA 或做 SoC，入口是 Chipyard 不是裸 rocket-chip。

## 底層機制：怎麼 clone 下來讀

給你一條實際的路（**本課不要求你跑，這是指路**，rocket-chip 生成環境重、要 Scala/sbt/FIRRTL 工具鏈，非本課範圍）：

```bash
# 讀原始碼（純看，不生成）——最輕量，馬上能做
git clone https://github.com/chipsalliance/rocket-chip.git
cd rocket-chip
# 直接讀這幾個檔，對照本課概念：
#   src/main/scala/rocket/RocketCore.scala   ← in-order pipeline，對照你的 core
#   src/main/scala/rocket/CSR.scala          ← CSR/trap，對照 Ch 31-35
#   src/main/scala/rocket/PTW.scala          ← page table walker，對照 Ch 28-29

# 讀 BOOM（OoO 真身）——對照 Ch 36
git clone https://github.com/riscv-boom/riscv-boom.git
cd riscv-boom
#   src/main/scala/exu/rename-stage.scala    ← register renaming，對照 Ch 36
#   src/main/scala/exu/rob.scala             ← ROB 循序退休
#   src/main/scala/lsu/lsu.scala             ← load/store queue
```

「讀原始碼」和「生成 + 模擬 + 上板」是兩回事。**讀**只要 git clone + 一個文字編輯器，馬上能做，也是本章推薦你做的——拿 `RocketCore.scala` 對照你自己的 `pipeline` 模組，會非常有收穫：你會認出 forwarding mux、hazard 邏輯、CSR 讀寫，然後看到 Rocket 多做的那些（FPU 介面、非阻塞 cache、PMP 檢查）。**生成 + 模擬**要 sbt + FIRRTL + verilator 一整套重工具鏈（Chipyard 有腳本），非本課範圍，標「未實測，超出本課環境」。

## 對比取捨表：三顆 core 你該選哪個當下一步

| 你想做的事 | 推薦看 | 為什麼 |
|---|---|---|
| 把本課 core 對照「量產 in-order」 | Rocket（`RocketCore.scala`） | 骨架幾乎一樣，看它補了什麼，收穫最直接 |
| 理解 Ch 36 的 OoO 概念落地 | BOOM（rename/rob/lsu） | Ch 36 每個概念都有對應模組 |
| 想要極簡、能塞進小 FPGA | picorv32（Verilog，非 Chisel） | 手寫 Verilog、面積優先、好讀，跟本課同語族 |
| 想做完整 SoC / 上 FPGA | Chipyard（含 rocket-chip） | 整合 bus/debug/FPGA 流程 |
| 想繼續純 SystemVerilog | picorv32 / 自己延伸本課 core | 不必跳進 Chisel 生態 |

**觀點**：如果你不打算投入 Chisel 生態，picorv32 是你最親近的下一站——純 Verilog、和本課同語族、面積優先的設計取捨很值得學。如果你要往學界 / SiFive / OoO 走，Rocket + BOOM + Chisel 是無法繞過的路，那就從「讀原始碼對照本課」開始，別一上來就想生成整個 SoC。

## 踩雷區

**雷 1：以為 Chisel 是「更高階的 HDL」，會取代 Verilog。**
- 錯誤直覺：「Chisel 比 Verilog 新，是下一代 HDL」。
- 正確認識：Chisel **生成 Verilog**，最終還是 Verilog 進 synthesis 工具。它是「硬體生成器 / 元編程層」，不是新的底層描述語言。synthesis、STA、上板流程（Ch 38）對 Chisel 生成的 Verilog 和你手寫的 SV 完全一樣。Chisel 贏在「一份原始碼生千種變體」的參數化能力，不是抽象層級更接近硬體。

**雷 2：以為 Rocket 因為是「量產級」所以概念上比本課難很多。**
- 錯誤直覺：「量產 core 一定用了我不懂的高深技術」。
- 正確認識：Rocket 是 **in-order 5 級**，核心概念**你已經全會了**（pipeline、forwarding、hazard、cache、VM、trap）。它多的是**工程完整度**——FPU、非阻塞 cache、完整特權模式、PMP、TileLink、debug、可配置——不是概念難度。你和 Rocket 的差距是「做全 vs 做出骨架」，不是「懂 vs 不懂」。

**雷 3：把 BOOM 當成「Rocket 的升級版」，以為新專案就該用 BOOM。**
- 錯誤直覺：「BOOM 亂序更快，是 Rocket 的進化，做東西該用 BOOM」。
- 正確認識：Rocket（in-order）和 BOOM（OoO）服務不同需求（Ch 36 雷 5）。BOOM 面積、功耗大得多，多數嵌入式 / 低功耗 / 面積敏感場景反而該用 Rocket 或更小的核。它們在生態裡**並存**是刻意的——BOOM 是研究 / 高效能展示，不是「取代 Rocket 的下一版」。

**雷 4：以為 clone rocket-chip 就能馬上像 verilator 跑本課 core 一樣跑起來。**
- 錯誤直覺：「git clone 完就能 make 跑模擬」。
- 正確認識：rocket-chip 要 Scala + sbt + FIRRTL 一整套工具鏈才能**生成** Verilog，再接 verilator 模擬，環境比本課重得多（常靠 Chipyard 的腳本一鍵配）。但**只讀原始碼**不需要這些——git clone + 文字編輯器就夠，這才是本章推薦你先做的。別把「讀」和「生成+跑」搞混，前者門檻極低、後者是一天的環境設定。

## 進階延伸

- **TileLink vs AXI**：rocket-chip 用 TileLink 而非 AXI，因為 TileLink 原生支援 cache 一致性（多核共享 L2 需要）。你在 Ch 30 學的 AXI handshake 概念完全遷移得過去，但 TileLink 多了 coherence 訊息（Acquire/Grant/Probe）。想做多核，這是必修。
- **SonicBOOM 的改進**：最新的 BOOM（SonicBOOM）在分支預測（TAGE-L）、load/store 消歧、前端頻寬上都比早期版強。技術報告會講它相對前代改了什麼，是「OoO core 怎麼一版版擠效能」的活教材。
- **Chisel 生成的 Verilog 長怎樣**：好奇的話，rocket-chip 生成後的 `.v` 檔可讀——變數名被 FIRRTL 改過（一堆 `_T_123`），但結構還在。看它一眼會理解「為什麼除錯 Chisel 生成的 Verilog 比手寫 SV 難」。
- **verilator 也能跑 Rocket/BOOM**：生成出 Verilog 後，模擬引擎和你本課用的 verilator 是同一個。你熟的 verilator + C++ testbench 技能完全能用在 Rocket 上，只是被包在 Chipyard 的腳本裡。這是本課技能往上遷移的直接接點。
- **面試與職涯**：CPU 設計職位（SiFive、各家 IP 公司）常拿 rocket-chip / BOOM 當共同語言。能講清「Rocket 是 in-order、比我做的多了 X、BOOM 把 renaming/ROB 落成 Y」本身就是很強的訊號——這正是本章和 Ch 36 給你的。

## 本章重點整理

- RISC-V 開源硬體生態重心在 **Chisel + rocket-chip**；Rocket（in-order）和 BOOM（OoO）**共用同一 SoC 框架**，換核心像換引擎不換底盤。
- **Rocket** 是本課 in-order core 的「量產版」：骨架幾乎一樣，多的是 FPU、非阻塞 cache、完整特權/PMP、TileLink、debug、可配置——是工程完整度差距，不是概念難度差距。
- **BOOM** 是 Ch 36 概念的真身：renaming→RenameStage、reservation station→IssueUnit、ROB→Rob.scala、LSQ→LSU，都是可 clone 可讀的 Chisel。
- **Chisel** 是生成 Verilog 的 Scala 函式庫（硬體生成器），不是新 HDL；贏在參數化，代價是多學一套語言 + FIRRTL 流程。**本課刻意用 SystemVerilog 不用 Chisel**。
- **讀原始碼**（git clone + 編輯器）門檻極低、馬上能做，是本章推薦的下一步；**生成+模擬**要 Chipyard 一整套重工具鏈，超出本課環境。
- 下一步路標：不進 Chisel 生態選 picorv32（純 Verilog 同語族）；要走學界/OoO 走 Rocket→BOOM→Chisel，從「對照本課讀原始碼」起步。

## 自我檢核

- [ ] 我能列出 Rocket 比本課 core 多的至少五樣東西，並說出哪些是「工程完整度」而非「新概念」。
- [ ] 我能把 Ch 36 的 renaming / ROB / LSQ 各對到 BOOM 的一個模組名。
- [ ] 我能解釋 Chisel 的本質（生成 Verilog 的 Scala 函式庫），以及它相對手寫 SV 的一個優勢和一個代價。
- [ ] 我能說出 Rocket 和 BOOM「共用 rocket-chip 框架、只換 tile」是什麼意思。
- [ ] 我能區分「clone 下來讀原始碼」和「生成 + 模擬 + 上板」的門檻差異。
- [ ] 我能根據自己的目標（純 SV / 上 FPGA / 學 OoO）說出下一顆該看哪個 core。

## 延伸閱讀

- **rocket-chip GitHub（github.com/chipsalliance/rocket-chip）**：直接 clone 讀 `src/main/scala/rocket/RocketCore.scala`，拿它和你自己的 pipeline 模組逐段對照——你會認出 forwarding、hazard、CSR，然後看到 Rocket 多做的部分。這是把本課接到量產 in-order core 最直接的一步，讀原始碼不必配任何工具鏈。
- **BOOM 技術報告與原始碼（docs.boom-core.org + github.com/riscv-boom/riscv-boom）**：技術報告逐 stage 講 OoO 微架構（把 Ch 36 概念變成 stage 圖），原始碼的 `exu/rename-stage.scala`、`exu/rob.scala`、`lsu/lsu.scala` 是 renaming/ROB/LSQ 的真身。想看「Ch 36 抽象怎麼落成程式碼」，這是唯一公開可讀的完整 OoO RISC-V core。
- **Chipyard 文件（chipyard.readthedocs.io）**：想真的把 Rocket/BOOM 生成、模擬、上 FPGA 或走 ASIC 流程，Chipyard 是整合入口（rocket-chip + BOOM + 加速器 + 流程腳本）。讀它的 "Basics of Chipyard" 和 core 選擇章節，理解生成 SoC 的完整環境長怎樣，也會讓你明白「讀 vs 生成」的門檻差。
- **picorv32 GitHub（github.com/YosysHQ/picorv32）**：如果你想留在純 Verilog、不進 Chisel 生態，這是最親近本課的下一站——手寫、面積優先、多週期（非 pipeline）的取捨和本課 pipeline 正好對比。它的 README 講「為了面積放棄了什麼」，是設計取捨的好教材。
- **Chisel 官方 tutorial（chisel-lang.org）**：若決定投入 Chisel 生態，官方 tutorial 從 Scala 基礎到第一個 Module。讀前先問自己「我真的要進這個生態嗎」——本課刻意不走這條，只有你確定要往學界/SiFive/OoO 走才值得投入這條學習曲線。

我們認識了真 core 和它們用的語言。但無論 SV 還是 Chisel 生成的 Verilog，寫完的 RTL 怎麼變成真晶片？下一章走完 **RTL → synthesis → gate netlist → P&R → GDSII** 的全流程，還會用 yosys 對本課的 ALU 真跑一次合成，看它變成多少個閘。

→ [Ch 38 從 RTL 到晶片：synthesis / STA / FPGA 流程原理](./38-rtl-to-silicon.md)
