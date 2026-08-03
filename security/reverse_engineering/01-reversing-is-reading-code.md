# Ch 1 — 逆向即讀碼：`reading_code` 的鏡像

> **目標**：把逆向工程（reverse engineering）放進你已經會的框架——**它就是讀碼，只是讀到只剩機器碼的極端**。你會看清逆向和讀 source 共用哪一套 SOP、又在哪裡分道揚鑣，並拿到一張「source 有什麼、binary 還剩什麼」的資訊落差表，這張表決定了你逆向時每一步的難度。

## 為什麼需要這個？

你已經走過 [`reading_code`](../../soft_skills/reading_code/README.md)（讀 source）和 [`codebase_case_studies`](../../soft_skills/codebase_case_studies/README.md)（累積 pattern 字典）。你可能會問：既然要學逆向，為什麼還要回頭談讀碼？

因為**逆向不是一門新技能，是同一門技能走到光譜的極端**。`reading_code` 的開篇就講「讀碼是一種逆向工程」——面對沒文件、註解騙人、抽象層層疊疊的陌生 source，本質上就是在逆向。這門課只是把那條光譜推到底：**連 source 都沒有了，你只有一團機器碼。**

把這件事看清楚，你就不會用錯的心態學逆向。初學者最常見的失敗，是把逆向當成「背 asm 指令」——好像只要記熟 `mov`/`lea`/`jmp` 就會逆了。不對。逆向難的從來不是「這條指令做什麼」（那查手冊就有），而是「這一段指令**原本想幹嘛**」。而「重建意圖」正是 `reading_code` 教你的那套 SOP 在做的事。你不是從零開始，你是把已經會的東西搬到一個資訊更稀薄的戰場。

## 先建立直覺：把 binary 當「被剝去註解與命名的陌生 codebase」

在碰任何 asm 之前，先把心智模型定下來。**逆向的目標物，是一份被編譯器抽掉了所有人類可讀資訊的 codebase。**

想像有人拿走一份正常的 C 專案，然後：

- 把每個變數名、函式名、型別名全部換成無意義的位址與位移（`x` 變 `-0x8(%rbp)`）
- 刪掉所有註解
- 把每個高階結構（`if`/`for`/`struct`/`switch`）拆散成 `cmp`/`jmp`/位移運算
- 把小函式直接抄進呼叫它的地方（inline），原函式的邊界消失
- 有時候還幫你「算好」——把一段迴圈的結果直接換成一個常數

剩下的，就是你要逆的 binary。它**控制流還在**（CPU 得知道往哪跳），但**意圖被拆解、命名被抹除、結構被打散**。你的工作和讀陌生 source 是同一件事：**在資訊不足的情況下，一塊一塊重建作者的意圖。** 只是這裡資訊少到極致。

這個類比不是修辭，它會實際指導你怎麼做。讀陌生 source 時你會先找 entry point、抓主資料流、猜關鍵函式——逆向時你做**一模一樣**的事，只是工具從 `grep`/LSP 換成 `objdump`/`gdb`，找的東西從函式名換成 `call` 指令與字串引用。

## 共用什麼：同一套 SOP

`reading_code` 的攻堅 SOP，逆向幾乎原封不動照搬。逐條對照：

**1. 偵察、建地圖（recon）。** 讀 source 時你先 `ls`、看目錄結構、讀 README、跑 build，建出「這專案大概長怎樣」的地圖。逆向時你做的第一件事一樣是偵察，只是換工具：

```bash
$ file target          # 架構？動態/靜態連結？strip 了沒？
$ readelf -h target    # entry point、型別
$ strings target       # 可讀字串——逆向的第一個線索來源
$ objdump -d target    # 反組譯，看有哪些函式、誰呼叫誰
```

`strings` 之於逆向，就像 README 之於讀 source——最便宜、最快、最常被低估的第一手情報。等一下的例子你就會看到，光是 `strings` 抓到 `"access granted"`，你就有了一個定位點。

**2. 假設驅動（hypothesis-driven）。** `reading_code` 的核心紀律：不要漫無目的地讀，先提假設（「我猜授權檢查在這個函式」），再去驗證或推翻。逆向時這條更重要——asm 太細碎，沒有假設你會淹死在指令海裡。你會不斷地「我猜這個迴圈是在算 checksum」→ 下斷點觀察 → 對或不對 → 修正假設。

**3. 收斂到關鍵（narrowing）。** 讀 50 萬行 source 時，你的目標是收斂到「你要改的那 200 行」。逆向時完全一樣：一個 binary 幾千個函式，你要收斂到「做授權檢查的那 30 條指令」。手法也類似——從你在乎的字串/API 呼叫往回追（誰引用了 `"granted"`？誰呼叫了 `strcmp`？），順藤摸瓜。

**4. 外化（externalize）。** 腦中讀不算讀。逆向的工作記憶負擔比讀 source 更重（沒有變數名幫你記憶），所以外化更關鍵——邊逆邊在筆記裡把 `sub_1169` 改名成 `check_password`、把 `-0x8(%rbp)` 標註成 `input_ptr`。Ghidra/IDA 讓你直接在反組譯上改名重註解，那本質就是把你重建的意圖寫回去。Part 4 的 [Ch 25](./25-externalizing-reversing-notes.md) 專講這件事。

**5. 費曼測試（Feynman）。** 逆完一段，能不能用白話講清楚「這函式吃什麼、回什麼、副作用是什麼」？講不清就是還沒逆懂，只是把 asm 抄了一遍。

**6. pattern 辨識。** 這是最深的鏡像對稱，值得單獨說。

## 最深的鏡像：pattern 辨識 → compiler idiom 辨識

`codebase_case_studies` 的核心主張是：讀碼老手快，是因為**一眼認出設計 pattern**——看到某段 code 就知道「這是 reactor event loop」「這是 visitor」「這是 RAII」，不用逐行推。這種 pattern 字典是熟練度的來源。

逆向的熟練度來自**同一件事的鏡像**：認出 **編譯器慣用語（compiler idiom）**。逆向老手快，是因為看到某段 asm 就知道「這是 signed 除以 2」「這是 switch 的 jump table」「這是 C++ 的 vtable 呼叫」，不用逐條推。

你在 Ch 0 已經見過一個：`shr $0x1f; add; sar` = signed 整數除以 2。你不需要每次都重新推導那三條指令在幹嘛——認得它，一眼掃過，標註「/2」，繼續往下。這就是 binary 版的 pattern 辨識。整門課 Part 1（尤其 [Ch 10](./10-compiler-idioms.md) 認 idiom、[Ch 11](./11-recognizing-stdlib-fingerprints.md) 認標準庫指紋）都在幫你建這本字典，[Ch 30](./30-reversers-pattern-dictionary.md) 收斂成完整版。

| `codebase_case_studies`（讀 source） | 本課（逆 binary） |
|---|---|
| 認出設計 pattern（reactor / visitor / RAII） | 認出 compiler idiom（`lea` 當乘法 / jump table / vtable 呼叫） |
| 認出標準庫用法（`std::sort`、`unique_ptr`） | 認出標準庫指紋（memcpy 展開、`std::string` 佈局） |
| pattern 字典 = 讀碼速度來源 | idiom 字典 = 逆向速度來源 |
| 靠讀大量真實 code 累積 | 靠 ground-truth 迴圈累積（寫→編→逆→對答案） |

## 在哪分道揚鑣

共用 SOP，但戰場條件天差地別。差異全部源自**一件事：意圖的載體不同**。

### 分歧一：source 的意圖是白紙黑字，binary 的意圖被編譯器丟掉、要重建

讀 source 時，作者的意圖有明確載體：變數叫 `password`，函式叫 `validate_license`，型別叫 `struct Config`，還有註解 `// TODO: 這裡有 race`。這些名字和註解**沒有被編譯進 binary**——它們只是給人看的，CPU 不需要。編譯器把 `validate_license` 換成一個位址就丟了。

所以逆向多了一個 source 沒有的關卡：**你得先重建意圖，才能開始理解。** 讀 source 時「理解」和「有意圖」是同時給你的；逆向時意圖不見了，你得先當偵探把它挖回來，才能進入「理解」。這是逆向比讀 source 累的根本原因——不是 asm 難懂，是**你得無中生有地重建一層被刪掉的資訊**。

### 分歧二：抽象層次不同

讀 source 你在作者選的抽象層次上工作——他寫 `for (auto& item : items)`，你就在「遍歷集合」這個層次思考。逆向時這層抽象被編譯器**壓平**了：`for` 迴圈變成 `cmp`/`jl`/位址遞增，集合變成一段連續記憶體加一個步長。你得從最低的抽象層次（暫存器、位移、跳轉）**往上重建**回「這是一個遍歷迴圈」。方向是反的：讀 source 由上往下，逆向由下往上。

### 分歧三：工具不同

| 面向 | 讀 source（`reading_code`） | 逆 binary（本課） |
|---|---|---|
| 導航 | `grep`/ripgrep、LSP、ctags/cscope | `objdump`、反組譯器的 xref、`call` 圖 |
| 定位符號 | 函式/變數**名** | 位址、字串引用、PLT import 名 |
| 語意理解 | 讀型別宣告、註解 | 反編譯器 pseudocode（會騙你，[Ch 8](./08-reading-decompiler-output.md)）、認 idiom |
| 觀察執行 | debugger、加 log/print | `gdb`、`strace`/`ltrace`、DBI（[Part 2](./12-dynamic-reversing-mindset.md)） |
| 版本考古 | `git log`/`git blame` | patch-diff / bindiff（[Ch 27](./27-patch-diffing.md)） |

注意最後一列的鏡像：讀 source 用 `git blame` 看「這行是誰、為什麼改的」；逆向沒有 git，但你可以拿同一個程式的**兩個版本 binary 做 diff**，看補丁改了哪裡——那就是 patch-diff 找漏洞的基礎，是 binary 世界的考古工具。

## 逆向的資訊落差表

把「分歧一」講的資訊丟失量化成一張表。這是整章最該記住的東西——它告訴你逆向的每一步「原本有什麼、現在剩什麼、你得自己補什麼」。

| 資訊 | source 有嗎？ | 編譯後 binary 剩什麼？ | 逆向者要怎麼辦 |
|---|---|---|---|
| **控制流**（if/loop/呼叫關係） | 有 | **在**——`cmp`/`jmp`/`call`，CPU 必須知道往哪走 | 相對好重建，是逆向的立足點 |
| **型別**（int/指標/struct/大小） | 有 | **幾乎沒了**——只剩「這是 4 bytes 還 8 bytes」的線索（暫存器寬度、位移） | 從操作寬度與用法**猜**型別（[Ch 9](./09-type-and-struct-recovery.md)） |
| **名字**（變數/函式/型別名） | 有 | **strip 後全沒**；沒 strip 剩函式名 | 靠行為重建、自己重新命名 |
| **註解 / 意圖** | 有 | **全沒**——編譯器不編譯註解 | 純靠推理重建，逆向最難的部分 |
| **高階結構**（`for`、`switch`、RAII） | 有 | **被打散**成低階指令，邊界模糊 | 認 idiom 把它拼回去 |
| **常數 / 字串字面量** | 有 | **多半在**（`.rodata`/立即數），是最可靠的線索 | `strings` + 交叉引用當定位錨點 |
| **匯入的函式**（libc 呼叫） | 有 | **在**（動態符號 strip 也刪不掉，Ch 3 講為什麼） | 看到 `call strcmp@plt` 就知道在比字串 |
| **原始碼結構**（檔案/模組切分） | 有 | **沒了**——全部攤平成一個 `.text` | 靠 call 圖重新分群 |

讀這張表的方式：**逆向者永遠站在「控制流 + 常數/字串 + 匯入函式」這三根還在的柱子上，去重建「型別 + 名字 + 意圖 + 結構」這四樣被刪掉的東西。** 你在乎的線索，永遠先從那三根還在的柱子找起。

## 底層機制：同一段 C，「source 讀法 vs binary 讀法」對照

抽象講夠了，跑一次 ground-truth 迴圈把它坐實。這是一個最小的密碼檢查（你自己寫的 source，所以有標準答案）：

```c
#include <stdio.h>
#include <string.h>
int check(const char *p){ return strcmp(p, "sesame") == 0; }
int main(int argc, char **argv){
    if (argc < 2){ puts("usage: ./a PASSWORD"); return 1; }
    if (check(argv[1])) puts("access granted");
    else puts("access denied");
    return 0;
}
```

### source 讀法

一眼掃完你就懂：`check` 拿參數跟字串 `"sesame"` 比，相等回 1；`main` 檢查有沒有給參數，有就呼叫 `check`，依結果印 granted / denied。**意圖白紙黑字**——函式叫 `check`，密碼 `"sesame"` 明擺著，邏輯線性。你花的力氣趨近於零。

### binary 讀法

現在 `gcc -O0` 編譯，反組譯 `check`（真跑 `objdump -d`，gcc 11.4.0）：

```asm
0000000000001169 <check>:
    1169:  endbr64
    116d:  push   %rbp                     ; ┐ 標準 prologue
    116e:  mov    %rsp,%rbp                 ; ┘ 建 stack frame
    1171:  sub    $0x10,%rsp                ; 挖 16 bytes 區域變數空間
    1175:  mov    %rdi,-0x8(%rbp)           ; 參數 p 存進 stack（rdi = 第一個參數）
    1179:  mov    -0x8(%rbp),%rax           ; rax = p
    117d:  lea    0xe80(%rip),%rdx          # 2004 ← 指向 .rodata 裡某個字串
    1184:  mov    %rdx,%rsi                 ; strcmp 第二參數 = 那個字串
    1187:  mov    %rax,%rdi                 ; strcmp 第一參數 = p
    118a:  call   1070 <strcmp@plt>         ; 呼叫 strcmp(p, ???)
    118f:  test   %eax,%eax                 ; strcmp 回傳 == 0 ?
    1191:  sete   %al                       ; 相等 → al=1
    1194:  movzbl %al,%eax                  ; 零擴充成回傳值
    1197:  leave
    1198:  ret
```

同一件事，逆向者得做這些**額外**工作，才追平「source 讀法」的起點：

1. **函式沒名字**——`objdump` 這裡還印得出 `<check>` 是因為我沒 strip；真實 binary 是 stripped，這裡會是 `sub_1169`，「這是密碼檢查」得你自己看出來。
2. **參數靠 ABI 重建**——`p` 不見了，你得知道 x86-64 System V ABI：第一個參數在 `%rdi`。看到 `mov %rdi,-0x8(%rbp)` 才知道「stack 上 `-0x8` 這格是第一個參數」。
3. **那個祕密字串要追一步**——`lea 0xe80(%rip),%rdx` 只給你一個位址 `0x2004`，你得去 `.rodata` 或用 `strings` 才知道它是 `"sesame"`：

   ```bash
   $ strings ch1_O0 | grep -iE 'sesame|granted|denied'
   sesame
   access granted
   access denied
   ```

   `strings` 就是逆向者的 README——密碼直接躺在那。**這也印證資訊落差表：字串字面量多半還在，是最可靠的錨點。**
4. **「== 0」是三條指令**——`test %eax,%eax; sete %al; movzbl` 才是 C 那個 `== 0` 的化身。認得這個 idiom（「test + setcc = 一個布林比較」）你才不會被三條指令嚇到。

看出重點了嗎？**source 讀法花的力氣趨近零，binary 讀法你得重建參數、追字串、認 idiom、腦補函式意圖——多做的每一步，都是在補編譯器丟掉的那層資訊。** 這就是資訊落差表在真實 asm 上的樣子。

而 `check` 還是 `-O0`、還沒 strip、邏輯線性的最簡單情況。等到 `-O2` 把 `check` inline 進 `main`、strip 掉所有名字、`strcmp` 的比較被展開成 SIMD——難度會再往上跳好幾階。Ch 2 就要拆解編譯器到底動了哪些手腳。

## 對比與取捨

| 面向 | 讀 source | 逆 binary |
|---|---|---|
| 意圖載體 | 名字 + 註解 + 型別，白紙黑字 | 全被編譯器丟掉，要重建 |
| 抽象方向 | 由上往下（在作者的抽象層工作） | 由下往上（從指令重建抽象） |
| 起手情報 | README、目錄結構、型別宣告 | `strings`、`readelf`、`objdump`、匯入表 |
| 最貴的成本 | 理解大量已知結構的互動 | 無中生有重建被刪掉的資訊層 |
| 熟練度來源 | 設計 pattern 字典 | compiler idiom 字典 |
| 何時該用 | 有 source（開源、自家 code） | 沒 source（閉源、malware、韌體、CTF） |

取捨的實務含義：**能拿到 source 就別逆**。逆向是 source 不可得時的手段，成本高得多。但很多場景 source 就是不可得——閉源商業軟體、惡意程式、韌體 blob、CTF 題目、你要驗證的第三方依賴——這時逆向是唯一的路。

## 踩雷集錦

1. **以為「會 asm = 會逆向」**。錯誤直覺：把逆向當成背指令表，記熟 `mov`/`lea`/`jmp` 就會了。正確認知：指令語意查手冊就有，逆向真正難的是**重建意圖**——那是 `reading_code` 那套 SOP（假設驅動、收斂、外化）在做的事。asm 是字母，重建意圖才是閱讀。
2. **不先偵察就一頭栽進 asm**。錯誤直覺：拿到 binary 立刻 `objdump -d` 從第一條指令讀起。正確做法：先 `file`/`strings`/`readelf` 建地圖、找定位錨點（字串、匯入的 API），有假設再往裡鑽。跟讀 source 不先看 README 就逐檔硬讀一樣蠢。
3. **不外化、想用腦記住一切**。錯誤直覺：asm 我邊看邊記就好。正確認知：binary 沒有名字幫你記憶，工作記憶負擔比讀 source **更重**，不外化（改名、註解、畫圖）你逆到第三個函式就忘了第一個。
4. **忘記自己在「重建」而非「讀取」**。錯誤直覺：把反編譯器吐的 pseudocode 或自己的推測當成「就是原本的 source」。正確認知：你手上的每個名字、型別、結構都是你**重建**出來的假設，可能錯。時時保持「這是我猜的」的自覺，才不會自信地讀錯（Ch 0 講過，逆向最危險的不是讀不懂，是自信地讀錯）。
5. **拿讀 source 的節奏期待逆向的速度**。錯誤直覺：source 我十分鐘讀懂一個模組，逆向怎麼一小時還在一個函式。正確認知：逆向天生慢，因為你在補一整層被刪掉的資訊。慢是正常的，別因此覺得自己不會逆。

## 進階：再往深一層

- **鏡像對稱是雙向的**。這門課教你把 `reading_code` 的 SOP 借來逆 binary；反過來，逆向練出來的直覺（不信任何抽象、追 data flow 到底、假設驅動）會讓你讀 source 更犀利——尤其讀爛 code、讀你不會的語言時。兩門課會互相強化。
- **逆向者的「型別」比讀 source 的型別更基本**。讀 source 時型別是給定的（`int`/`struct Foo`）；逆向時你只有「這格是 4 bytes、被當指標解參考、乘了 24」這種線索，型別是你**推理出來的假設**。這反而逼你理解型別的物理本質（大小、對齊、佈局），比只讀 source 更深。[Ch 9](./09-type-and-struct-recovery.md) 專門練這個。
- **抽象層次會影響你選工具**。純靜態讀（`objdump`）適合小、線性的 code；一旦控制流被混淆、或你需要知道「執行時這個指標指向哪」，靜態就不夠，得動態觀察（[Part 2](./12-dynamic-reversing-mindset.md)）。這跟讀 source 時「純讀不夠就上 debugger」是同一個判斷。

## 本章重點整理

- **逆向 = 讀碼的極端**：不是新技能，是 `reading_code` 那套 SOP（偵察、假設驅動、收斂、外化、費曼）搬到「只剩機器碼」的戰場。
- **心智模型**：把 binary 當「被剝去註解與命名、結構被打散的陌生 codebase」。
- **最深的鏡像**：讀 source 靠**設計 pattern** 字典，逆 binary 靠 **compiler idiom** 字典——本質同一種 pattern 辨識能力。
- **三個分歧**：意圖載體（白紙黑字 vs 要重建）、抽象方向（由上往下 vs 由下往上）、工具（grep/LSP vs objdump/gdb）。
- **資訊落差表**：控制流 + 常數/字串 + 匯入函式「還在」是你的三根柱子；型別 + 名字 + 意圖 + 結構「被刪掉」是你要重建的四樣東西。永遠先從還在的柱子找線索。

## 自我檢核

- [ ] 我能用一句話說清楚「逆向和讀 source 是同一件事的哪個極端」
- [ ] 我能講出逆向和讀 source **共用**的至少三條 SOP（偵察/假設驅動/收斂/外化/費曼任選）
- [ ] 我能講出它們**分道揚鑣**的三個點（意圖載體、抽象方向、工具）
- [ ] 我能默寫出資訊落差表的重點：哪三樣「還在」、哪四樣「被刪掉」
- [ ] 我理解「認 compiler idiom」是「認 design pattern」的鏡像，都是熟練度的來源
- [ ] 面對一個新 binary，我知道第一步不是 `objdump` 從頭讀，而是先偵察找錨點

## 延伸閱讀

- **[`soft_skills/reading_code` Ch 2「讀碼是一種逆向工程」](../../soft_skills/reading_code/02-reading-as-reverse-engineering.md)**
  - **定位**：本章的正對面鏡像——那章把逆向直覺借來讀 source，這章把讀碼 SOP 借來逆 binary。兩章對讀，鏡像對稱會非常清楚。
  - **讀哪裡**：整章，特別是它怎麼描述「重建意圖」——你會發現和逆向重建意圖是同一段話。
- **[`soft_skills/codebase_case_studies` README](../../soft_skills/codebase_case_studies/README.md)**
  - **定位**：pattern 字典的正版，本課 idiom 字典的鏡像參照。
  - **讀哪裡**：看它怎麼組織「認 pattern」這件事——本課 [Ch 10](./10-compiler-idioms.md)/[Ch 30](./30-reversers-pattern-dictionary.md) 對 idiom 做同構的事。
- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：把「一行 C ↔ 對應 asm」教到極致的字典級教材，正是「binary pattern 辨識」的最佳題庫。
  - **讀哪裡**：Part I 從 Hello World 到函式/迴圈的 source↔asm 對照；當字典跳著查。
  - **前提**：會讀 C；x86-64 asm 不熟沒關係，它從零帶。
- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時 source↔asm 對照網站，練「binary 讀法」時的即時 ground-truth——想確認某段 asm 對應什麼 C，反查它。
  - **怎麼用**：貼 C、選 gcc/clang + 優化等級，右邊出 asm；本章那個 `check` 例子貼進去就能重現。

心智模型定下來了：逆向就是在資訊落差表的三根柱子上，重建被刪掉的四樣東西。但要重建得準，你得先知道編譯器**到底對你的 code 做了什麼**——因為你逆的就是那些變換的產物。

→ [Ch 2 從 source 到 binary：編譯器做了什麼](./02-source-to-binary-what-compiler-does.md)
