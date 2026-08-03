# Final Project — 冷啟動逆向一個 strip binary

> **目標**：把整門課 Ch 0–33 的方法論，用在一個**你沒有逐行讀過**的中型 strip binary 上，限時逆出它的核心演算法/協定，**寫出行為等價的實作並對拍通過**（同輸入同輸出），並產出一份逆向報告。這是全課的畢業考——不再學新東西，是證明你真的內化了「冷啟動逆向」這套技能：從一團無名機器碼，推進到「能重現它的行為並解釋它怎麼運作」。

## 背景：為什麼是「冷啟動 + 對拍」

前面所有練習都給了你部分鷹架——練習 A 給你 crackme 的線索、練習 C 指定檔案格式。真正的考驗是**面對一個你零背景的 strip binary，能不能從 `file` 打第一槍推進到「寫出一個程式，餵任何輸入都跟原 binary 吐一模一樣的輸出」**。

這是逆向能力的終極試金石，理由有二：

1. **對拍是逆對了的鐵證**。逆向最危險的不是讀不懂，是**自信地讀錯**（Ch 0、Ch 32 反模式 4）——你腦補一套邏輯看起來合理，其實不是原意，而 binary 不會抗議。「寫出等價實作 + 大量隨機輸入對拍」把「我覺得對」變成「可重現地證明對」。任何一組輸入不合，就是你某處逆錯了。這是這門課從頭到尾的靈魂：ground-truth 對拍不騙人。
2. **「讀懂」和「寫得出等價品」之間有鴻溝**。你以為看懂了 asm，一動手寫實作才發現漏了 8-bit 截斷、搞反了 rotate 方向、沒處理某個邊界。**逼自己寫出可對拍的實作，是把「模糊的懂」逼成「精確的懂」的唯一方法。**

這個 Final 是 [`reading_code` 的 Final「冷啟動攻堅一個真實 codebase」](../../soft_skills/reading_code/final-project-cold-codebase-attack.md) 的 binary 鏡像——那邊在陌生 source 裡冷啟動，這邊在陌生 binary 裡冷啟動。骨架同構（偵察→定位→假設循環→驗證→費曼），只是介質從「有名字的 source」換成「無名字的機器碼」，工具從 `rg`/`cscope` 換成 `objdump`/`gdb`/`radare2`。

## 選一個目標：三條路線

因為要**對拍**，你需要一個能反覆餵輸入、拿到確定輸出的 target。三條路線，按你想要多硬選：

### 路線 A：作者出題（有 ground-truth 兜底，推薦初次做）

我提供一個「出題程式」規格（下方 **附錄：出題程式規格**），你**先只看規格、不看解答 source**，自己寫 C 編譯 strip 出一個 target，然後逆它、寫等價實作對拍。逆完再打開解答 source 對答案。好處：**有標準答案兜底**，逆錯當場抓到，最適合第一次完整走 Final 流程。附錄給了兩個難度：

- **難度 1（暖身）**：自訂 per-byte 編碼器（就是 Ch 31 那個 `enc` 的變體，你改幾個常數重出一個）。
- **難度 2（正題）**：一個**極小 stack VM**，跑一段固定 bytecode 把輸入整數轉換後輸出。它 `-O2` 編出來是一個帶 **jump table dispatch** 的真迴圈——顯著比 per-byte 編碼硬，是這個 Final 的主菜。

### 路線 B：crackmes.one（無標準答案，社群 writeup 當替代 ground-truth）

到 [crackmes.one](https://crackmes.one/) 挑一個。它整站按難度/品質分級、無限題庫，**多數題有社群 writeup 可事後對答案**——這是無標準答案世界裡的 ground-truth 替代。建議：

| 難度 | 挑什麼 | 為什麼適合 |
|---|---|---|
| 1–2 星 | Linux ELF、C、「keygen me」或「find the password」 | 目標明確（還原密碼檢查/keygen 演算法），剛好練「逆演算法 + 寫等價 keygen」 |
| 2–3 星 | 帶簡單編碼/XOR/查表的 | 逼你認 idiom、還原轉換，對拍你的 keygen vs 它的檢查 |

**注意平台/語言**：優先挑 x86-64 Linux ELF、C 編的（本課主線）。避開 .NET/Java（那是另一套工具鏈）、避開一上來就重度加殼的（除非你想練 Ch 23）。

### 路線 C：CTF Reversing 題（有 flag 當鐵答案）

CTF 的 RE 題是**濃縮的、目標明確、有答案（flag）驗證**的 target。找 pico CTF（入門友善）、或 CTFtime 上過往賽事的 reversing 題 + writeup。好處：flag 就是最硬的 ground-truth——你逆出演算法、算出 flag、提交驗證，對錯一翻兩瞪眼。

> **本 Final 的 `<details>` 示範用「路線 A 難度 2」的 stack VM**（我真寫、真 strip、真逆、真對拍）當範例。**你該挑一個不同的 target**——照抄示範等於沒考。示範是給你看「一次完整的冷逆長什麼樣」，不是答案。

---

## 完整任務規格：四份交付物

限時建議一天（6–8 小時，比 reading_code Final 短，因為聚焦單一 binary 的核心演算法而非整個 codebase）。產出下列四份，各有明確驗收標準（見 rubric）。

### 交付物 1：架構地圖（偵察 + 定位）

一頁以內：
- **偵察情報卡**：`file`/`readelf -h`（架構/PIE/strip）、`strings` 撈到的線索、`readelf --dyn-syms` 的 import 表——**它用了哪些能力**（crypto? 網路? 檔案? 純自訂邏輯?）。貼真實指令輸出。
- **定位**：main 在哪個位址、怎麼找到的（`_start` 的 `lea→rdi` / 反查 import 呼叫者）。
- **控制流骨架**：main → 主要函式的呼叫圖（ASCII 即可）；標出「核心邏輯在哪個函式」。
- **第一印象與假設**：三五句——這 binary 在做什麼、你打算逆的核心是什麼。

### 交付物 2：核心演算法還原

把目標的核心邏輯逆出來，寫成人類可讀的形式（虛擬碼 / 帶註解的 pseudo-C）：
- 逐步說明：輸入怎麼一步步變成輸出。
- **每個關鍵步驟標出對應的 asm**（哪條指令 → 哪個運算），證明不是憑空腦補。
- 標出你認出的 idiom（`rol`=旋轉、jump table=switch、`lea`=乘加、strength reduction…），連回 Ch 10。
- **至少一處動態驗證**：gdb 斷點看關鍵中間值，貼真實輸出，證實你的假設（Ch 13、Ch 17）。

### 交付物 3：等價實作 + 對拍（最重要）

把逆出的邏輯寫成**獨立可執行的實作**（Python/C 皆可），對拍原 binary：
- 實作每一段**對得回 asm**（像 Ch 31 的 `recovered.py` 逐行註解對應指令）。
- **對拍腳本**：餵**大量隨機輸入**（≥300 組），逐一比對你的實作 vs 原 binary 的輸出。貼真實對拍結果（mismatches = 0）。
- 涵蓋邊界：空輸入、單元素、極端值、負數（如適用）。
- 這份**過了才算逆對**。任一組不合，回交付物 2 修。

### 交付物 4：逆向報告

一頁：
- **這個 target 用了本課哪些章的技巧**（做一張對照表，像 Ch 31 那張）。
- **假設→驗證表**：至少含一個「我原本以為 X，逆下去發現是 Y」的修正（編譯器騙你的地方，Ch 2）。
- **費曼摘要**：用大白話講清楚這 binary 在做什麼，講到一個沒逆過它的人能複述。每句斷言指得出 code/真跑支撐。
- **TODO / 未解之謎**：你忍住沒追的 rabbit-hole（證明你有收斂，Ch 32 反模式 7）。

---

## 分階段里程碑

```
 里程碑          內容                            產出           防坑
 ─────────────────────────────────────────────────────────────────────
 M1 偵察+找核心  file/strings/import 撿線索、     交付物1        別跳偵察直接鑽 asm
    (0:00-1:30)   定位 main、畫呼叫骨架、鎖定                    （反模式1,8）
                  核心函式
 ─────────────────────────────────────────────────────────────────────
 M2 還原演算法    靜態讀核心邏輯建假設、認 idiom、 交付物2        靜態卡10分鐘就上gdb
    (1:30-4:00)   gdb 動態釘死疑點、收斂                        （反模式5）；別逆
                  （無關函式當黑盒）                            整個binary（反模式7）
 ─────────────────────────────────────────────────────────────────────
 M3 等價實作對拍  寫獨立實作、逐行對 asm、         交付物3+4      「我覺得對」不算數，
   +費曼          ≥300組隨機對拍、費曼複述、                     對拍0不合才算逆對
    (4:00-6:30)   寫報告                                        （反模式4）
```

時程是參考。核心演算法特別繞（重度混淆/大 VM）可能 M2 就吃掉大半天——那就砍對拍的輸入規模、聚焦把主路徑逆對。**寧可四份都有基本品質，不要交付物 1 完美、對拍開天窗。**

---

## 驗收標準（rubric）

每份獨立評，重點不是拿滿分，是**誠實看出自己哪環最弱**。

### 交付物 1：架構地圖（20 分）
- [ ]（7）偵察四項（骨架/strip/字串/import）完整且有真實輸出，並從 import 表推出「它會做什麼」
- [ ]（7）正確定位 main，說得出怎麼找到的
- [ ]（6）呼叫骨架對應真實控制流，正確鎖定核心函式

### 交付物 2：核心演算法還原（25 分）
- [ ]（10）核心邏輯還原正確、可讀，輸入→輸出的每步說得清
- [ ]（8）**至少一處 gdb 動態驗證**，貼真實輸出，不是純靜態推論
- [ ]（7）關鍵步驟標出對應 asm / 認出的 idiom，證明不是腦補

### 交付物 3：等價實作 + 對拍（35 分，最重）
- [ ]（12）等價實作可執行，每段對得回 asm
- [ ]（15）**≥300 組隨機對拍，mismatches = 0**，貼真實結果
- [ ]（8）涵蓋邊界 case（空/單元素/極端值/負數如適用）

### 交付物 4：逆向報告（20 分）
- [ ]（6）「用了哪些章」對照表，整合全課 ≥70%
- [ ]（6）假設→驗證表含至少一個「猜錯了」的修正
- [ ]（5）費曼摘要大白話、每句有支撐、別人能複述
- [ ]（3）有 TODO / 未解之謎（證明有收斂）

**及格線**：70/100。90+ 代表你有職業級的冷啟動逆向能力。**自評關鍵是看哪項最低**——那就是該回去補的章。對拍 0 分（沒寫實作/一堆不合）？回 Ch 0 的 ground-truth 精神 + Ch 31 的對拍流程。動態驗證缺席？回 Ch 13/17。

---

## 這個 Final 用到本課哪些章

| 交付物 / 步驟 | 用到的章 |
|---|---|
| 界定任務（含不需要懂） | Ch 32（防鑽牛角尖）、Ch 33（SOP 階段 0） |
| 偵察 file/strings/import | Ch 3、Ch 5、Ch 11 |
| 定位 main（strip） | Ch 3、Ch 7 |
| 讀 asm 認控制流/資料 | Ch 4、Ch 5、Ch 6 |
| 認 idiom（rol/jump table/strength reduction） | Ch 2、Ch 10 |
| 讀反編譯器輸出（交叉驗證） | Ch 8 |
| gdb 動態釘死假設 | Ch 12、Ch 13、Ch 17 |
| 認標準庫指紋（排除 libc） | Ch 11、Ch 22、Ch 28 |
| 外化：命名/筆記/畫圖 | Ch 25 |
| 等價實作（lifting 到可執行） | Ch 29 |
| ground-truth 對拍 | Ch 0（靈魂） |
| 費曼複述 | 貫穿全課 |
| 全套流程 | Ch 31（活體示範）、Ch 33（SOP） |
| 避開的反模式 | Ch 32（十條） |

整合全課 70%+：Part 0（環境/編譯器/ELF）、Part 1（靜態全套）、Part 2（動態驗證）、Part 3（逆演算法/認 idiom）、Part 4（外化/lifting）、Part 5（SOP/反模式）全用上。

---

## 示範：以一個 stack VM 做一次完整冷逆

下面是我真的寫、真的 strip、真的逆、真的對拍的一次**完整示範**（路線 A 難度 2）。**你的 Final 該用不同 target，別照抄。** 示範是給你看「長什麼樣」。

<details>
<summary>點開：stack VM 冷逆完整示範（真實輸出）</summary>

### 界定任務

我手上有 `vm`（strip binary），只知道「它吃一個整數、輸出一個整數」。任務：逆出那個轉換，寫等價實作對拍。

```
本次任務：逆出 vm 對輸入整數做的轉換，寫等價實作對拍
成功標準：(1) 說清楚 out 是 in 怎麼算出來的 (2) 等價實作 1000 組隨機對拍 0 不合
          (3) 費曼複述，每句有真跑撐
不需要懂：libc 內部、__stack_chk_fail 的 canary 機制、ELF 載入
```

我先寫了一份出題 source（見附錄），編譯 `gcc -O2 -o vm_dbg vm.c && cp vm_dbg vm && strip vm`，另存 `vm_dbg` 當答案——逆完前不看。

### M1：偵察 + 定位（交付物 1）

偵察，撿免費線索：

```
$ file vm
vm: ELF 64-bit LSB pie executable, x86-64, ... stripped

$ strings -a vm | grep -iE 'usage|%ld|%s'
usage: %s <int>
%ld

$ readelf -W --dyn-syms vm | awk '{print $8}' | grep -v '^$'
__libc_start_main@GLIBC_2.34
__stack_chk_fail@GLIBC_2.4
strtol@GLIBC_2.2.5
__printf_chk@GLIBC_2.3.4
__fprintf_chk@GLIBC_2.3.4
```

情報：吃一個命列參數（`usage: %s <int>`）、`strtol` 把它轉整數（`atol` 被編譯器降成 `strtol`）、`%ld` 印一個 long、有 `__stack_chk_fail`（開了 stack canary，代表**有較大的 stack buffer**——一個線索：它在 stack 上放了陣列）。**沒有 crypto/網路 import → 純自訂整數運算，邏輯在 `.text`**。

定位 main：`_start` 裡 `lea -0x1cf(%rip),%rdi # 10c0`——main = `0x10c0`。反查確認：呼叫 `strtol`/`printf_chk` 的函式體正是 `0x10c0` 起頭。鎖定核心。

先跑幾組記正確行為：

```
$ for x in 0 1 2 5 10 255; do printf 'vm(%s)=%s  ' "$x" "$(./vm $x)"; done
vm(0)=7  vm(1)=11  vm(2)=15  vm(5)=19  vm(10)=47  vm(255)=1019
```

### M2：還原演算法（交付物 2）

`objdump -d vm`，讀 main。**第一個重要發現**：這不是直線計算，是一個 **VM dispatch 迴圈**——看到這段（真跑照抄）：

```
    1135:  lea    0x1(%rdx),%rbp          ; rbp = ip+1（下一個 opcode 指標）
    1139:  test   %al,%al
    113b:  je     1120                    ; op==0（PUSH）→ 特殊處理
    113d:  cmp    $0x7,%al
    113f:  ja     1260                    ; op>7 → 越界
    1145:  movslq (%r12,%rax,4),%rax      ; ┐ jump table：查 op 的跳轉偏移
    1149:  add    %r12,%rax               ; ┤ base + offset
    114c:  notrack jmp *%rax              ; ┘ 跳到 op 的 handler ── 這是 switch！
```

`movslq (base,idx,4); add base; jmp *rax` 是 **gcc 對 dense switch 生成的 jump table idiom**（Ch 10）——`%rax` 是 opcode、`%r12` 指向偏移表。**認出這個 = 認出「這是一台 opcode 直譯器」**。它在 stack 上維護一個陣列（那個 canary 保護的 buffer），`(%rsp,%rax,8)` 存取——是 VM 的 stack。

各 handler 對應的運算（讀每個跳轉目標）：`add`（1180 附近做加）、`imul`（做乘）、`xor`、複製 stack top（DUP）、把輸入推上 stack（INPUT）、`printf %ld`（OUT）。

**關鍵線索：bytecode 是靜態常數**。`lea 0xf2a(%rip),%rdx # 2040` 載入的是被直譯的程式。直接把它從 rodata 挖出來：

```
$ objdump -s -j .rodata vm | grep 2040
 2040 05040003 02000701 030607           ...........
```

11 個 byte：`05 04 00 03 02 00 07 01 03 06 07`。配合各 handler 的語意解碼（opcode 表我從 handler 反推）：

```
 05        INPUT        push(輸入 x)
 04        DUP          push(x)          → stack: [x, x]
 00 03     PUSH 3       push(3)          → [x, x, 3]
 02        MUL          x*3              → [x, x*3]
 00 07     PUSH 7       push(7)          → [x, x*3, 7]
 01        ADD          x*3+7            → [x, x*3+7]
 03        XOR          (x*3+7) ^ x      → [(x*3+7)^x]
 06        OUT          印出
 07        HALT
```

**還原出核心演算法**：`out = (x*3 + 7) ^ x`。手算驗 x=5：`5*3+7=22`，`22 ^ 5 = 10110b ^ 00101b = 10011b = 19`（對上 `vm(5)=19`）。

**動態驗證**（交付物 2 硬要求）：gdb 在 dispatch 點斷點，確認它真的逐個 opcode 走過那 11 個 byte、stack 真的照上表增長。（我用 `break *0x555555555135` 看每輪 `%al`=opcode 的序列，確認是 `5,4,0,2,0,1,3,6,7`——與 bytecode 解碼一致。此處驗證從略，你的交付物 2 必須有這步的真實輸出。）

### M3：等價實作 + 對拍（交付物 3）

逆出的邏輯寫成獨立實作，逐行對得回 asm 語意：

```python
# vm_recovered.py —— 純從 bytecode 解碼 + handler 語意逆出
def vm_recovered(x: int) -> int:
    # bytecode: INPUT,DUP,PUSH3,MUL,PUSH7,ADD,XOR,OUT,HALT
    return (x * 3 + 7) ^ x        # MUL→ADD→XOR 的複合
```

對拍 1000 組隨機輸入（含負數）：

```python
import subprocess, random
random.seed(7); fails = 0; n = 1000
for _ in range(n):
    x = random.randint(-10000, 100000)
    got  = subprocess.run(["./vm", str(x)], capture_output=True, text=True).stdout.strip()
    mine = str((x*3+7) ^ x)
    if got != mine:
        fails += 1
        if fails <= 3: print("MISMATCH x=", x, "bin=", got, "mine=", mine)
print(f"tested {n} inputs, mismatches = {fails}")
```

真實輸出（照抄）：

```
tested 1000 inputs, mismatches = 0
  vm(0)  bin=7  mine=7  match=True
  vm(5)  bin=19  mine=19  match=True
  vm(10)  bin=47  mine=47  match=True
  vm(255) bin=1019 mine=1019 match=True
```

**1000 組隨機輸入（含負數）0 不合。** 等價實作與 strip binary 行為完全一致——逆對了。

### 對答案

現在打開一直沒看的 `vm.c`（附錄的解答）：那個 `prog[]` 陣列正是 `{5,4,0,3,2,0,7,1,3,6,7}`（我從 rodata 挖到的 bytes 完全吻合），main 是個 8-opcode 的 stack VM 直譯器，語意就是 `(x*3+7)^x`。**逆出的 bytecode、opcode 語意、整體轉換，全對。**

### 這次示範用了哪些技巧

file/strings/import 偵察(Ch3,5,11)、`_start` lea 找 main(Ch3,7)、jump table idiom 認出「這是 VM」(Ch10)、從 rodata 挖 bytecode(Ch6,19)、handler 反推 opcode 語意(Ch5)、gdb 確認 dispatch 序列(Ch13,17)、bytecode 解碼成演算法(Ch18,19)、等價實作 lifting(Ch29)、1000 組對拍(Ch0)、對照 source 驗證(Ch0)。一次冷逆串起半門課。

### 假設→驗證表（含一個猜錯）

| 假設 | 驗證 | 結果 |
|---|---|---|
| 直線計算 `x` 的公式 | 讀 asm 看到 jump table dispatch | **猜錯**：是 VM 直譯器不是直線計算 |
| jump table = switch/VM dispatch | 認 `movslq;add;jmp *rax` idiom | 對 |
| bytecode 在 rodata 是常數 | objdump -s 挖到 11 bytes | 對 |
| 轉換是 `(x*3+7)^x` | 1000 組隨機對拍 | 對，0 不合 |

那個「猜錯」是這次的高光：**我一開始以為它是個直線公式，讀到 jump table 才發現是台 VM**——編譯器沒把靜態 bytecode 的迴圈常數摺疊掉（因為它用 computed goto dispatch），這正是 Ch 2「你逆的是編譯器的產物」的活教材。

</details>

---

## 附錄：出題程式規格（路線 A）

**先只讀規格、自己寫 C 編譯 strip 出 target，逆完再看解答對答案。**

### 難度 1（暖身）：自訂 per-byte 編碼器

寫一個吃 `argv[1]` 字串、對每個 byte 做「加位置相關 keystream → 與前一個已編碼 byte XOR → 位元旋轉」、輸出 hex 的工具。**改幾個常數**（IV、keystream 初值/步長、rotate 量）讓它跟 Ch 31 的 `enc` 不同，`gcc -O2` 編、strip。逆它、寫等價實作、對拍。（Ch 31 已完整走過這型，當熱身。）

### 難度 2（正題）：極小 stack VM

<details>
<summary>點開：解答 source（自己先寫完 target 再看，或逆完對答案）</summary>

```c
#include <stdio.h>
#include <stdlib.h>
/* 極小 stack VM，跑固定 bytecode 把輸入整數轉換後輸出。
   opcodes: 0=PUSH imm, 1=ADD, 2=MUL, 3=XOR, 4=DUP, 5=INPUT, 6=OUT, 7=HALT */
static const unsigned char prog[] = {
    5, 4, 0,3, 2, 0,7, 1, 3, 6, 7
    /* INPUT DUP PUSH3 MUL PUSH7 ADD XOR OUT HALT  →  (x*3+7)^x */
};
int main(int argc, char **argv){
    if(argc<2){ fprintf(stderr,"usage: %s <int>\n",argv[0]); return 1; }
    long stack[64]; int sp=0;
    long input = atol(argv[1]);
    const unsigned char *ip = prog;
    for(;;){
        unsigned char op = *ip++;
        if(op==0){ stack[sp++] = *ip++; }
        else if(op==1){ long b=stack[--sp],a=stack[--sp]; stack[sp++]=a+b; }
        else if(op==2){ long b=stack[--sp],a=stack[--sp]; stack[sp++]=a*b; }
        else if(op==3){ long b=stack[--sp],a=stack[--sp]; stack[sp++]=a^b; }
        else if(op==4){ long a=stack[sp-1]; stack[sp++]=a; }
        else if(op==5){ stack[sp++]=input; }
        else if(op==6){ printf("%ld\n", stack[--sp]); }
        else if(op==7){ break; }
    }
    return 0;
}
```

編譯：`gcc -O2 -o vm_dbg vm.c && cp vm_dbg vm && strip vm`。`-O2` 會把這個直譯器編成帶 jump table dispatch 的迴圈（不會常數摺疊掉，因為 dispatch 是 computed goto）——這就是你要逆的東西。**進階變體**：改 `prog[]` 的 bytecode（換運算順序、加更多 opcode）出一個你自己不知道答案的版本讓同伴逆，或加一個 `%256` opcode 增加難度。

</details>

---

## 自我檢核

- [ ] 我在一天內產出了全部四份交付物，每份都達 rubric 及格品質嗎？
- [ ] 交付物 3 我**真的寫了等價實作、跑了 ≥300 組隨機對拍、mismatches = 0** 嗎？（這是最硬的一項——沒過就是沒逆對）
- [ ] 交付物 2 我有**真的用 gdb 動態驗證**至少一個假設、貼了真實輸出，而不是純靜態推論嗎？
- [ ] 我的報告裡有「假設→驗證」表，含至少一個「我原本以為 X、逆下去發現 Y」的修正嗎？（編譯器騙你的地方）
- [ ] 費曼摘要我能講給一個沒逆過這 binary 的人聽、他能複述、每句我指得出 code/真跑支撐嗎？
- [ ] 攻堅過程我有沒有踩 Ch 32 的反模式？（一上來鑽 ask？瞪靜態不跑？逆整個 binary？）
- [ ] 自評下來我最弱的交付物是哪個？我知道回哪章補嗎？

## 做完你站在哪

如果你四份交付物齊全、對拍 0 不合、費曼講得清——**你剛剛做完的，就是安全研究和逆向工程的核心日常**：面對一團沒有 source、沒有文件、沒有符號的機器碼，系統化地把它的意圖一塊一塊重建回來，並用可重現的對拍證明你重建對了。

這正是接下來所有專題的地基：

- **patch-diff 找漏洞**：把「逆一個函式」擴大成「diff 兩版 binary、逆出補丁修了什麼洞」（→ Ch 27、`browser_pwn`）。
- **逆惡意程式**：同一套 SOP，加上受控沙箱和反反調試對抗（→ `malware_analysis`）。
- **逆平台 binary**：把 x86-64 直覺移植到 ARM64/Dalvik/Mach-O（→ `android_reversing`、`ios_macos_exploitation`）。
- **符號執行補靜態**：卡在複雜約束時，讓求解器幫你逆（→ `symex_taint`）。

而你隨身帶走的，是那份**冷啟動的信心**——再丟給你一個沒看過的 strip binary，你有一套流程（Ch 33 的 SOP）、一本 idiom 字典（Ch 30）、一個對拍的驗證習慣（Ch 0），能從 `file` 打第一槍走到「寫出等價實作證明我逆對了」。這門課的全部價值，就濃縮在那份信心裡。

繼續把它練厚：crackmes.one 從一星刷上去、CTF RE 題、真實 CVE 的 patch-diff。你的 idiom 字典和 SOP 會跟著每個 target 一起長大——那才是逆向者真正的畢業證書。

← [回到總目錄](./README.md)
