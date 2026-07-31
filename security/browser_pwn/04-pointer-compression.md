# Ch 4 — Pointer Compression

> **目標**：搞懂 V8 怎麼把 64-bit 指標壓成 32-bit 存、怎麼壓怎麼解壓、以及這個設計對利用的三個深遠影響：（1）你在 `%DebugPrint` 看到的「位址」其實是壓縮值；（2）堆內指標只有 32-bit，所以「堆內位址」和「堆外位址」是兩個世界；（3）這個 32-bit cage 正是 V8 Sandbox（[Ch 34](./34-v8-sandbox.md)）的地基。不懂 compression，你會把壓縮值當成真位址算，滿盤皆錯。

> **環境**：V8 15.3.0（candidate）、git commit `ab2cad06`，`~/v8build/v8/out/x64.release/`（`v8_enable_pointer_compression=true`）。d8：`~/v8build/v8/out/x64.release/d8`。本章所有輸出真跑。

## 為什麼需要這個？

前一章你可能已經覺得怪：`%DebugPrint` 印的位址都是 `0x2eb4_0104b155` 這種——高 32 bit 是 `0x2eb4_0100` 這類固定前綴、真正在變的只有低 32 bit。這不是 64-bit 虛擬位址該有的樣子。原因就是 **pointer compression（指標壓縮）**。

V8 開這個功能是為了**省記憶體**：JS 程式裡指標多如牛毛（每個物件的 map、每個 property、每個 array element 若是物件……），把每個指標從 8 byte 壓到 4 byte，整個堆的記憶體用量幾乎砍半。這對「一個瀏覽器分頁動輒幾百 MB」的 Chrome 是巨大的勝利，所以現代 V8 **預設開啟**、你要打的真實目標就是這個佈局。

對做利用的你，這件事有三個非懂不可的後果，本章一一拆。**如果你照著 2018 年（compression 出現前）的 writeup 把位址當成 64-bit 算，你的 offset 全錯**——這是新手打現代 V8 最常見的翻車點之一。

## 先建立直覺：所有堆物件住在同一個 4 GB 籠子裡

```
   64-bit 虛擬位址空間（巨大）
   ┌──────────────────────────────────────────────────────────────┐
   │                                                                │
   │        ┌───────────── V8 heap cage（4 GB，位址對齊）─────────┐ │
   │        │  isolate root = cage base = 0x2eb4_00000000        │ │
   │        │                                                     │ │
   │        │   物件A @ +0x0104b154   物件B @ +0x0101e2c8  ...    │ │
   │        │   ↑ 每個堆物件只需記「相對 cage base 的 32-bit 偏移」 │ │
   │        └─────────────────────────────────────────────────────┘ │
   │                                                                │
   │   （TypedArray 的 backing store 之類的「大塊資料」放在 cage 外）  │
   └──────────────────────────────────────────────────────────────┘
```

核心點子：V8 把**整個堆塞進一個 4 GB 對齊的區域**（叫 **cage**），這個區域的起始位址叫 **isolate root**（也常叫 cage base）。既然所有堆物件都在這 4 GB 內，任何堆物件的位址 = `cage_base + 32-bit 偏移`。而 `cage_base` 對整個 isolate 是固定的、存在一個暫存器裡（x64 上通常是 `r14`）。所以**每個堆內指標只要存那 32-bit 偏移就夠了**——高 32 bit 大家都一樣，不用重複存。

這就是為什麼你看到的位址「上半固定、下半在變」：上半是 cage base，下半是壓縮值。

## 底層機制：壓與解壓

### 解壓（decompress）：32-bit → 64-bit

拿到一個 32-bit 壓縮值 `c`，要得到真正的 64-bit 位址：

```
   real_addr = cage_base | c        （cage_base 低 32 bit 為 0，所以等同 base + c）
```

實務上因為 cage 是 4 GB 對齊，`cage_base` 的低 32 bit 全 0，`|` 和 `+` 等價。x64 上這是一條指令等級的操作（`r14 + reg`），幾乎零成本——這是壓縮划算的關鍵：省一半記憶體，只花幾乎為零的解壓時間。

### 壓縮（compress）：64-bit → 32-bit

反過來，把一個 cage 內的 64-bit 位址壓成 32-bit，就是取低 32 bit：

```
   c = addr & 0xFFFFFFFF
```

### 這對 tag 的影響

上一章的 tag 規則在壓縮值裡照舊：壓縮後的 32-bit 值最低位仍然 `0`=SMI、`1`=HeapObject。`%DebugPrint` 印給你的 `0x2eb40104b155`，其實是「cage_base（`0x2eb4_00000000`）+ 壓縮值（`0x0104b155`）」的完整 64-bit tagged 位址；那個 `0x0104b155` 才是真正存在物件欄位裡的 32-bit。

### 親眼看：cage 內 vs cage 外

這是 compression 對利用最重要的一課。看一個 `Float64Array` 的內部——它同時牽涉 cage 內指標和 cage 外指標：

```
$ d8 --allow-natives-syntax -e 'let f=new Float64Array(4); f[0]=1.5; %DebugPrint(f);'
DebugPrint: 0x226d0104b175: [JSTypedArray]
 - map: 0x226d01007a85 <Map[60](FLOAT64ELEMENTS)> [FastProperties]
 - buffer: 0x226d0104b119 <ArrayBuffer map = 0x226d01010ce1>
 - byte_length: 32
 - length: 4
 - data_ptr: 0x226d0104b154
   - base_pointer: 0x104b14d
   - external_pointer: 0x226d00000007
```

拆給你看（每個數字都有來源）：

- **`0x226d0104b175`（JSTypedArray 本體）、`0x226d01007a85`（map）、`0x226d0104b119`（buffer）** ——這些都在 **cage 內**，高 32 bit 全是 `0x226d_0100` 這類同一個 cage 前綴。它們存在記憶體裡的其實是 `0x0104b175`、`0x01007a85`、`0x0104b119` 這些 32-bit 壓縮值。
- **`base_pointer: 0x104b14d`** ——這是一個 32-bit 壓縮值（指向 cage 內的 backing store，因為這個小 array 的 backing store 被放在 V8 堆內）。
- **`external_pointer: 0x226d00000007`** ——注意這個值：它是 `cage_base(0x226d_00000000) + 7`。這是 sandbox 開啟時的 **external pointer table** 機制的痕跡（[Ch 8](./08-arraybuffer-typedarray.md)、[Ch 34](./34-v8-sandbox.md) 細講），`data_ptr` 是由 `base_pointer` 和 external 部分算出來的最終 `0x226d0104b154`。

對照一個 backing store 放在 cage **外**的 ArrayBuffer：

```
$ d8 --allow-natives-syntax -e 'let ab=new ArrayBuffer(16); %DebugPrint(ab);'
DebugPrint: 0x2320104b261: [JSArrayBuffer]
 - backing_store: 0x23400004000
 - byte_length: 16
```

**`backing_store: 0x23400004000`** ——這個位址的高位是 `0x234_00...`，和 cage base `0x232_0100...` **完全不同**。這就是重點：**ArrayBuffer 的資料 backing store 住在 cage 外**，用的是**完整 64-bit raw pointer**，不是壓縮值。這個「cage 內 vs cage 外是兩個世界」的分界，直接決定了現代利用的路線圖。

## cage 內外的分界，為什麼決定利用路線

把上面的觀察整理成一張利用者最該記住的圖：

| 東西 | 存哪 | 用什麼指標 | 你能不能只靠「堆內 OOB」摸到它 |
|---|---|---|---|
| JSObject / JSArray 本體、map、properties、elements（小的） | **cage 內** | 32-bit 壓縮 | 能（堆內相對讀寫就到得了） |
| ArrayBuffer 的 `backing_store` | **cage 外** | 64-bit raw | **不能**（32-bit 壓縮指標指不出 cage） |
| `TypedArray` 的 `data_ptr` / `external_pointer` | 經 pointer table（sandbox 開） | 見 [Ch 34](./34-v8-sandbox.md) | 受 sandbox 保護 |

這張表解釋了整個現代 V8 利用的結構性難題：

**在 compression + sandbox 出現前**，你只要拿到一個堆內的相對讀寫（例如陣列 OOB），就能改掉一個 `TypedArray` 的 `backing_store` 指標（那時它是明碼的 64-bit raw pointer 存在物件裡），把它指到任意位址 → 立刻任意讀寫整個進程記憶體 → 直接寫 JIT code / GOT 拿 shell。這是傳說中的一步登天。

**compression 把第一刀砍下來**：堆內指標只有 32-bit，你在堆內的 OOB 讀寫，天然被限制在 4 GB cage 裡打轉——你改不到 cage 外的東西，因為你手上的相對 primitive 是壓縮語意的。**sandbox 再補第二刀**（[Ch 34](./34-v8-sandbox.md)）：連 backing store 指標本身都不再明碼存在物件裡，改用 pointer table 間接。

所以本課 Part 3「任意讀寫」教的，其實是 **cage 內的任意讀寫**（能任意改 cage 內任何物件）；要打穿到 cage 外、真正拿 RCE，得再破 sandbox（Part 5 的 [Ch 34](./34-v8-sandbox.md)）。**這個「兩層牢籠」的心智模型，現在建立起來，後面每一章都用得到。**

## 對比：開 compression vs 不開

| 面向 | 開 pointer compression（你的 build、現代 Chrome） | 不開（老 build / 部分 CTF） |
|---|---|---|
| tagged value 寬度 | **32-bit** | 64-bit |
| SMI payload | 31-bit（±2³⁰） | 32-bit（±2³¹） |
| 堆內指標 | cage base + 32-bit 偏移 | 完整 64-bit |
| 堆內 OOB 能摸到的範圍 | 限 4 GB cage 內 | 整個位址空間 |
| `%DebugPrint` 位址樣子 | 高位固定的 `0x2eb4_01...` | 各式各樣的 64-bit |
| 老 writeup 的 offset | **多半失效** | 可能還能用 |

**看任何 V8 writeup / CTF 附件，第一件事和看 heap 題看 glibc 版本一樣：確認它開沒開 compression。** SMI 範圍、位址算法、能摸到的地盤全都不同。

## 進階：再往深一層

- **cage base 存在哪**：x64 上 V8 把 isolate root / cage base 放在 **`r14`** 暫存器，JIT 出來的 code 解壓指標就是 `mov rax, [r14 + reg]` 這種。你在 [Ch 11](./11-optimization-pipeline.md) 用 `--print-opt-code` 看 TurboFan 機器碼時，看到 `r14` 到處出現就是它。
- **多個 cage**：現代 V8（開 sandbox）其實有不只一個 cage——**pointer compression cage**（放一般堆物件）和分開的 **trusted space / code cage**。Code、某些 trusted 物件被移出主 cage，正是為了不讓「cage 內任意寫」污染到程式碼與可信元資料。[Ch 34](./34-v8-sandbox.md) 會展開。
- **`--no-enable-pointer-compression`**：你可以另編一個關 compression 的 d8 對照，會看到位址變回散落的 64-bit、SMI 範圍變 32-bit。學習期拿來對照「壓縮到底改了什麼」很有幫助，但別拿它練現代利用——佈局不對。
- **ASLR 和 cage 的關係**：cage base 本身還是隨機化的（每次跑 `0x2eb4...`、`0x226d...`、`0x232...` 都不同），所以你仍需 leak 出 cage base 才知道絕對位址。但**cage 內部的相對佈局**在同版本很穩定——這就是為什麼利用要「leak 一個 cage 內物件位址 → 推出 cage base → 其餘用固定偏移」。[Ch 14](./14-first-oob.md) 的 heap groom 建立在這個穩定性上。

## 踩雷集錦

1. **錯誤直覺：「`%DebugPrint` 的 `0x2eb40104b155` 是 64-bit 虛擬位址」。正確：** 它是 `cage_base + 32-bit 壓縮值`。真正存在物件欄位裡的只有低 32 bit `0x0104b155`。用 gef 看時心裡要清楚哪半是 base、哪半是壓縮值。
2. **錯誤直覺：「拿到堆內 OOB 就能改任意記憶體」。正確：** 堆內 OOB 是**壓縮語意**的，天然關在 4 GB cage 內。要出 cage 得另外破 sandbox（[Ch 34](./34-v8-sandbox.md)）。這是現代 V8 比老 V8 難打一大截的根本原因。
3. **錯誤直覺：「ArrayBuffer 的 backing_store 和物件本體在同一個位址空間、偏移固定」。正確：** backing store 在 **cage 外**、是完整 64-bit raw pointer，和 cage base 高位完全不同（本章實測 `0x234...` vs cage `0x232...`）。
4. **錯誤直覺：「照抄老 writeup 的位址偏移就行」。正確：** compression 出現前後 SMI 範圍、指標寬度、offset 全變。看 writeup 先確認 build config，如同 heap 看 glibc。
5. **錯誤直覺：「cage base 固定所以位址可以寫死」。正確：** cage base 每次執行都隨機（ASLR），要 leak；穩定的是 cage **內部的相對偏移**，利用靠這個穩定性而非絕對位址。

## 動手練習

1. 跑 `%DebugPrint` 在多個不同物件（`{}`、`[1,2,3]`、`new Float64Array(4)`、一個字串）上，把每個位址的高 32 bit 抄下來，確認它們在**同一次執行內**共用同一個 cage 前綴；再重跑一次，確認前綴變了（ASLR）但內部相對關係還在。
2. 用本章的 `Float64Array` 例子，找出 `data_ptr`、`base_pointer`、`external_pointer` 三個值，畫出它們的關係（哪個在 cage 內、哪個經 pointer table）。跑幾次觀察 `external_pointer` 是不是總是 `cage_base + 小常數`。
3. 建一個 `new ArrayBuffer(0x1000)`，`%DebugPrint` 看它的 `backing_store`，確認高位和 cage base 不同。試著推想：如果你有「cage 內任意 32-bit 相對寫」，你**改得到**這個 backing_store 值嗎？（答案關係到你為什麼需要 [Ch 34](./34-v8-sandbox.md)。）

## 本章重點整理

- **pointer compression** 把堆內指標從 64-bit 壓成 32-bit：所有堆物件住在一個 4 GB **cage** 內，指標 = `cage_base + 32-bit 偏移`，cage base 存在 `r14`。
- 解壓 = `cage_base | c`（一條指令）；壓縮 = 取低 32 bit；tag 規則在壓縮值裡照舊。
- **cage 內（物件、map、小 elements）用壓縮指標，cage 外（ArrayBuffer backing store）用 64-bit raw pointer**——這條分界決定了利用路線。
- 現代利用是「**兩層牢籠**」：Part 3 的任意讀寫是 **cage 內**的；要出 cage 拿 RCE 得再破 **sandbox**（[Ch 34](./34-v8-sandbox.md)）。
- compression 讓 SMI 變 31-bit、讓老 writeup 的 offset 多半失效——看任何資料先確認開沒開 compression。

## 自我檢核

- [ ] 能畫出 cage / cage_base / 32-bit 偏移 的關係圖，並解釋解壓怎麼算
- [ ] 看到 `%DebugPrint` 位址，能指出哪半是 cage base、哪半是真正存在物件裡的壓縮值
- [ ] 能解釋為什麼「堆內 OOB」被天然限制在 cage 內、摸不到 backing store
- [ ] 能說出「cage 內任意讀寫」和「真正的任意讀寫（出 cage）」差在哪、後者為何需要破 sandbox
- [ ] （面試題）「V8 pointer compression 是什麼、為什麼做、對 exploit 有什麼影響？」能答出省記憶體 + cage 限制 + 兩層牢籠
- [ ] 知道 cage base 隨機、但 cage 內相對偏移穩定，且理解利用如何倚賴這個穩定性

## 延伸閱讀

- **[“Pointer Compression in V8” — v8.dev/blog/pointer-compression](https://v8.dev/blog/pointer-compression)**
  - **這篇說什麼**：官方第一手講壓縮的動機（省記憶體）、cage、SMI/指標在壓縮下的位元佈局、效能取捨。
  - **讀哪裡**：整篇。特別看它畫的 tagged value bit 圖，和本章的 cage 圖互補。
  - **關聯**：本章的機制、SMI 變 31-bit 的原因，這篇是權威來源。
- **[“The V8 Sandbox” — v8.dev/blog/sandbox](https://v8.dev/blog/sandbox)**
  - **這篇說什麼**：sandbox 如何建立在 cage 之上——把 backing store 指標移進 external pointer table、限制堆內破壞的殺傷範圍。
  - **讀哪裡**：現在先讀「Motivation」和「The Sandbox」開頭，理解「cage 是 sandbox 的地基」。細節留到 [Ch 34](./34-v8-sandbox.md)。
  - **關聯**：本章的「兩層牢籠」在這篇是完整版；你會懂為什麼 external_pointer 長成 `cage_base + 小常數`。
- **[saelo 的 V8 exploitation 系列 / Phrack “Exploiting Logic Bugs in JavaScript JIT Engines”](http://www.phrack.org/papers/jit_exploitation.html)**
  - **這篇說什麼**：實戰角度講 V8 利用原語，其中對「cage / sandbox 前後利用差異」有精準的操作級描述。
  - **讀哪裡**：先看它對 addrof/fakeobj 與「為什麼要 leak cage base」的段落。
  - **關聯**：把本章的抽象牢籠模型，落地成真實 exploit 步驟；是通往 Part 3 的最佳實戰橋樑。

現在你知道位址是壓縮的、物件都住在 cage 裡。但「一個物件到底長什麼形狀、V8 怎麼知道它有哪些欄位、怎麼解讀後面的 bytes」——這由每個 HeapObject 的第一個欄位 **map** 決定。下一章拆 map，這是整個物件模型、也是利用時最關鍵的一塊。

→ [Ch 5 — Map / hidden class：物件的形狀與 transition](./05-map-hidden-class.md)
