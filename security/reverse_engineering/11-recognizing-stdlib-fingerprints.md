# Ch 11 — 認出標準庫與資料結構指紋

> **目標**：把「認 pattern」從語言 idiom（Ch 10）擴到**函式庫與資料結構**層級。學會認出 libc 呼叫（`call xxx@plt`——strip 也刪不掉的 dynsym）、靜態連結時的 libc 指紋（FLIRT/簽名概念）、常見資料結構在 binary 裡的樣子（malloc chunk、linked list 遍歷、C++ `std::string`/`std::vector`——先淺提，Ch 20 深講）、常數指紋（crypto magic constant——先提，Ch 18 深講）。真跑：一個用 strlen/malloc/memcpy 的程式，看 @plt 呼叫。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + readelf + nm。本章 asm/符號表全部真跑。

## 為什麼需要這個？

逆向一個 binary，你**不想**逐條重建 `strlen`、`malloc`、`printf` 的內部——那是別人寫好的標準庫，重建它純浪費時間。你要能一眼認出「這裡呼叫 libc 的 X」，把它當黑盒跳過，把精力留給程式**自己的**邏輯。

同樣，程式用的資料結構（linked list、hash table、`std::vector`、樹）在 binary 裡有相對穩定的**指紋**。認出「這是 linked list 遍歷」「這是 std::string 的 SSO」，你瞬間知道這塊在幹嘛，不用從指標運算裡慢慢推。

這是 pattern 辨識往上一層：Ch 10 認「一行 C 的 idiom」，這章認「一整個庫函式呼叫」和「一整個資料結構」。兩者合起來，你掃 asm 的速度會逼近老手——大部分程式碼是「呼叫已知庫 + 操作已知資料結構」，真正新穎的邏輯只是其中一小塊。

## 先建立直覺：strip 刪得掉自己的符號，刪不掉「借別人東西的收據」

Ch 0 你看過 `strip` 讓 `nm` 印 `no symbols`。但那只刪掉**程式自己的**符號（`main`、`classify`、`process`…）。程式**向動態庫借的函式**（libc 的 `strlen` 等）是另一回事——因為執行時 loader 要靠名字去 libc 找這些函式的位址，**這些名字必須留在 binary 裡**（在 `.dynsym`/`.dynstr`/`.rela.plt`），strip 刪不掉，否則程式跑不起來。

```
   strip 刪掉的（程式自己的）          strip 刪不掉的（動態連結需要的）
  ┌──────────────────────┐          ┌──────────────────────────────┐
  │ .symtab: main,classify│  ──刪──► │ .dynsym: strlen,malloc,printf │ ← 名字還在！
  │ 局部變數名、函式名      │          │ .rela.plt: JUMP_SLOT 重定位   │
  └──────────────────────┘          │ .plt.sec: strlen@plt 樁       │
                                     └──────────────────────────────┘
                                        loader 靠這些名字找 libc 函式
```

所以：**動態連結的 binary，就算 strip，libc 呼叫的名字都還在。** 這是逆向最好的免費線索——每個 `call xxx@plt` 都白紙黑字告訴你「這裡呼叫了 strlen」。下面真跑驗證。

## 真跑：@plt 呼叫，strip 前後都在

ground-truth source（`fp.c`）用了 strlen/malloc/memcpy/free/printf，還手搓一個 linked list。先看 `gcc -O0` 不 strip 的 `main`（真跑，節選 plt 呼叫）：

```asm
$ objdump -d -M att --no-show-raw-insn fp_O0
    1208:  call   10a0 <strlen@plt>     ; n = strlen(msg)
    121c:  call   10d0 <malloc@plt>     ; copy = malloc(n+1)
    123b:  call   10c0 <memcpy@plt>     ; memcpy(copy, msg, n+1)
    1256:  call   10d0 <malloc@plt>     ; nd = malloc(sizeof(node))  ← 迴圈裡
    12d0:  call   10b0 <printf@plt>
    12dc:  call   10f0 <free@plt>       ; free(copy)
```

每個 `call ...@plt` 的名字直接可讀。現在 **strip 它**，再看名字還在不在（真跑）：

```
$ nm fp_strip
nm: fp_strip: no symbols          ← 程式自己的符號沒了

$ nm -D fp_strip                  ← 但動態符號還在！
                 U free@GLIBC_2.2.5
                 U malloc@GLIBC_2.2.5
                 U memcpy@GLIBC_2.14
                 U printf@GLIBC_2.2.5
                 U strlen@GLIBC_2.2.5

$ readelf -r fp_strip | grep -iE 'strlen|malloc|memcpy'
000000003fb8  ...JUMP_SLO ... strlen@GLIBC_2.2.5 + 0
000000003fc8  ...JUMP_SLO ... memcpy@GLIBC_2.14 + 0
000000003fd0  ...JUMP_SLO ... malloc@GLIBC_2.2.5 + 0

$ objdump -d -j .plt.sec fp_strip     ← plt 樁還帶名字
00000000000010a0 <strlen@plt>:
    10a0:  endbr64
    10a4:  bnd jmp *0x2f0d(%rip)        # 3fb8 <strlen 的 GOT 條目>
```

`nm` 說沒符號，`nm -D` 卻列出全部 libc import，連 **glibc 版本**（`GLIBC_2.14` 等）都在。**這就是逆向動態連結 binary 的免費地圖：所有 libc 呼叫的名字，strip 也帶不走。**

**逆向手法**：拿到 stripped 動態 binary，第一步跑 `nm -D` 或 `readelf -r` 看它 import 哪些 libc 函式——這份清單本身就洩露程式大概在做什麼（有 `socket`/`connect` = 網路、有 `fopen`/`fread` = 檔案、有 `AES_*`/`EVP_*` = 加密）。反編譯器（Ghidra/IDA）也是靠這個把 `call sub_10a0` 標成 `strlen`。

## 靜態連結：名字沒了，靠指紋認庫函式

上面是**動態連結**（libc 在外面，程式借名字）。但**靜態連結**（`gcc -static`）把整個 libc **複製進 binary**，然後 strip——這時 libc 函式的名字也沒了，`strlen` 變成一坨無名 asm 混在你的程式裡。這是逆向大 binary 的惡夢（Ch 22 專講）。

怎麼在無名的一大團裡認出「這塊是 libc 的 `strlen`」？靠**函式指紋（signature）**——庫函式的機器碼是固定的（同版本 libc 編出來一樣），可以事先建一個「指紋 → 函式名」的資料庫，掃 binary 比對：

- **FLIRT（Fast Library Identification and Recognition Technology）**：IDA 的招牌功能。它有一個大型簽名庫，把常見編譯器/libc 版本的每個庫函式的機器碼 pattern 記下來，掃你的靜態 binary，自動把匹配的無名函式**標回名字**（`strlen`、`memcpy`…）。Ghidra 有對應的 **Function ID**、還有 **Sigs/BSim**。
- **原理**：對每個庫函式取一段有辨識度的 byte pattern（要處理重定位造成的可變 byte，用萬用字元遮罩），做成簽名。匹配到就命名。這把「逆一個靜態 binary」從「連 libc 都要自己認」降級回「只要逆程式自己的邏輯」。

真跑對照兩者的差距。把同一份 `fp.c` 改用 `gcc -static` 編、strip，看它膨脹成什麼：

```
$ file fp_static_strip
fp_static_strip: ELF 64-bit LSB executable, ... statically linked, ... stripped

$ ls -la fp_strip fp_static_strip
   14472  fp_strip           ← 動態版：14 KB（libc 在外面）
  819664  fp_static_strip    ← 靜態版：800 KB（整個 libc 塞進來了）

$ nm -D fp_static_strip
nm: fp_static_strip: no symbols   ← 靜態版連 dynsym 都沒有，import 地圖消失
```

動態版 14KB、靜態版 **800KB**——多出來的 786KB 全是被複製進來的 libc。而且 `nm -D` 對靜態版是 `no symbols`：**動態版免費給你的 libc import 名，靜態版一個都沒有**。你程式那三個小函式（main/沒別的）淹沒在 libc 的幾百個無名函式裡——r2 用輕量分析（`aa`）在靜態 strip 版只認出 3 個函式，但完整分析會挖出數百個 libc 函式，全叫 `fcn.xxxx`。這就是為什麼 malware/保護程式偏好靜態 + strip：把逆向成本從「逆你的邏輯」抬高到「先從幾百個無名函式裡認出哪些是 libc」。

Ch 28（二進位相似度與函式指紋）會把這套「指紋 → 命名」的思路展開。這裡先有觀念：**動態連結靠 dynsym 免費得到名字；靜態連結靠 FLIRT/FunctionID 指紋庫還原名字。** 逆向前先分清你手上是哪種（`file` 會說 `dynamically linked` 或 `statically linked`）。

## 資料結構的指紋

程式的資料結構在 binary 裡有可認的形狀。核心幾個：

### malloc chunk：`malloc` 的引數就是物件大小

看 `fp.c` 的 linked list node 配置（真跑，`main` 迴圈裡）：

```asm
    1251:  mov    $0x10,%edi           ; ← malloc(0x10) = malloc(16)
    1256:  call   10d0 <malloc@plt>    ;   16 = sizeof(struct node){int val; node* next;}
    125b:  mov    %rax,-0x8(%rbp)      ; nd = 回傳的 chunk
    1266:  mov    %edx,(%rax)          ; nd->val = i     （offset 0, 4-byte）
    1270:  mov    %rdx,0x8(%rax)       ; nd->next = head （offset 8, 8-byte 指標）
```

**指紋**：`mov $N,%edi; call malloc` — **N 就是物件大小**（呼應 Ch 9：malloc 引數即 struct 大小）。`0x10`=16 正是 `struct node`（4-byte int + 4 padding + 8-byte 指標）。緊接著對回傳的 chunk 做 `mov ...,(%rax)`（+0）和 `mov ...,0x8(%rax)`（+8）就是在填欄位——一次 malloc + 填欄位 = 「配置一個物件並初始化」。

（真實 heap 上還有 glibc 的 chunk header——size/flags 在使用者資料前 8 bytes、free chunk 有 fd/bk 指標。那是 heap 利用（`binary_exploitation`）的重點，逆向一般只需認出「這是一次配置」。）

### linked list 遍歷：迴圈裡 `p = p->next`

`fp.c` 的 list 加總（真跑，`main`）：

```asm
    128d:  mov    -0x30(%rbp),%rax      ; p = head
    1297:  mov    (%rax),%eax           ; ┐ sum += p->val（讀 +0）
    129d:  add    %eax,-0x34(%rbp)      ; ┘
    12a0:  mov    -0x28(%rbp),%rax      ; ┐ p = p->next
    12a4:  mov    0x8(%rax),%rax        ; ┤   （讀 +0x8 指標欄位）← 指紋！
    12a8:  mov    %rax,-0x28(%rbp)      ; ┘
    12ac:  cmpq   $0x0,-0x28(%rbp)      ; p != NULL ?
    12b1:  jne    1297                  ; 迴圈
```

**指紋**：迴圈裡有 `mov 常數off(%reg),%reg`（**用同一個暫存器**，把指標欄位讀進自己）+ 迴圈條件是 `cmp $0x0`（判 NULL 結束）= **linked list 遍歷**，那個常數 offset 就是 `next` 指標在 node 裡的位置（這裡 +0x8）。這是 Ch 9「指標鏈」的迴圈版。看到「跟著一個 offset 指標跳、直到 NULL」立刻認出 list walk。

（雙向 list 會有兩個指標欄位、樹遍歷會有 left/right 兩個 offset 跳且常配遞迴或明確 stack、hash table 會先算 index 再進 bucket 的 list——各有變體，但「跟指標跳到 NULL/哨兵」是共同骨架。）

### C++ std::string / std::vector（先淺提，Ch 20 深講）

C++ 容器在 binary 裡是有固定佈局的 struct：

- **`std::vector<T>`**：三個指標——`begin`（+0）、`end`（+8）、`capacity_end`（+0x10）。**指紋**：一個物件被當「三個連續 8-byte 指標」用，`size()` = `(end - begin) / sizeof(T)`（你會看到兩指標相減再除/移位）。看到「三指標、相減算大小」想 vector。
- **`std::string`（libstdc++ SSO）**：+0 是資料指標、+8 是長度、+0x10 起是 16-byte 的**小字串緩衝（SSO, small string optimization）**。短字串直接存在物件內部那 16 bytes、資料指標指向自己內部；長字串才 heap 配置、指標指向外部。**指紋**：一個物件，資料指標有時指向物件自己 +0x10（SSO）、有時指向 heap，且帶一個長度欄位。

這些是 Ch 20（逆 C++ binary）的核心，這裡先讓你「看到三指標想 vector、看到帶內嵌小緩衝的想 string」。**先修提示**：`file` 說 binary 用 libstdc++、或 `nm -D` 看到 mangled 的 `_ZNSt...` 符號，就知道要用 C++ 的 pattern 而非 C 的。

## 常數指紋（先提，Ch 18 深講）

有些演算法用**固定的魔法常數（magic constant）**，這些常數本身就是指紋——在 binary 裡看到它們，幾乎能直接認出演算法：

- **crypto/hash**：MD5 的初始值 `0x67452301`、SHA-256 的 `0x6a09e667`、AES 的 S-box、CRC32 的多項式 `0xEDB88320`…——這些是各演算法的「身分證」。逆向時 `strings`/掃 `.rodata` 找到它們，直接 Google 反查就知道是哪個演算法。
- **手法**：`objdump -s -j .rodata`（Ch 10 用過看 jump table）或專門的常數辨識工具（如 IDA 的 FindCrypt、Ghidra 的對應腳本）掃這些 magic constant。

Ch 18（逆演算法：認出 crypto/hash/壓縮指紋）會把這套展開。這裡先記：**看到一組來歷不明卻很「整齊」的大常數（尤其在 .rodata 的初始化表），先當它是某個標準演算法的魔數去反查，別急著逆內部。**

## 對比與取捨

| 你想認的 | 連結方式 | 手法 | 工具 |
|---|---|---|---|
| libc 呼叫（動態） | 動態連結 | 讀 `call xxx@plt` 名（strip 也在） | objdump / `nm -D` / `readelf -r` |
| libc 函式（靜態） | 靜態連結 | 指紋比對還原名字 | IDA FLIRT / Ghidra FunctionID |
| import 清單洩露意圖 | 動態 | 看 import 哪些庫函式 | `nm -D` / `readelf --dyn-syms` |
| malloc 物件 | — | `mov $N,%edi; call malloc`，N=大小 | objdump |
| linked list | — | 迴圈裡跟 offset 指標到 NULL | objdump |
| std::string/vector | C++ | 三指標/內嵌 SSO 佈局 + mangled 符號 | Ch 20 |
| crypto 演算法 | — | .rodata 的 magic constant 反查 | strings / FindCrypt（Ch 18） |

**取捨**：動態連結給你最多免費線索（名字全在），是逆向最友善的情況；靜態 + strip 最惡劣（連 libc 都無名，全靠指紋庫）。惡意程式常靜態連結 + strip 就是為了增加逆向成本。逆向前用 `file` 分清類型，決定策略。

## 踩雷集錦

1. **以為 strip 把 libc 呼叫名也刪了**：看到 stripped binary 就以為什麼線索都沒了，忘了跑 `nm -D`。錯誤直覺：「strip = 全無名」。正確：**動態連結的 libc import 名字 strip 刪不掉**，`nm -D`/`readelf -r` 免費給你一份 import 地圖。

2. **把靜態 binary 裡的 libc 當成程式自己的邏輯逆**：在 `-static` binary 裡逐條逆一個其實是 `memcpy`/`printf` 內部的函式，浪費幾小時。錯誤直覺：「binary 裡的函式都是作者寫的」。正確：靜態 binary 裡大半是 libc，先用 FLIRT/FunctionID 把庫函式標掉，只逆剩下的。

3. **malloc 的引數當成隨便一個數**：看到 `mov $0x18,%edi; call malloc` 沒意識到 `0x18`=24 是**物件大小**。錯誤直覺：「那只是個常數」。正確：**malloc 引數 = 物件大小**，是還原 struct 大小的直接線索（Ch 9）。

4. **把 std::string 的 SSO 內嵌緩衝當成 struct 欄位亂逆**：看到物件內有一塊 16-byte、資料指標指向物件自己，硬當成一堆獨立欄位。錯誤直覺：「這是自訂 struct」。正確：資料指標指回物件內部 + 長度欄位 = `std::string` 的 SSO，認出它別逆內部（Ch 20）。

5. **看到 crypto magic constant 還硬逆演算法內部**：在 .rodata 看到 `0x6a09e667` 這種整齊大常數，還一條條逆那堆位元運算。錯誤直覺：「這是某個自訂位元演算法」。正確：**整齊的來歷不明大常數先反查**——多半是標準 crypto/hash 的魔數，認出演算法比逆內部快一百倍（Ch 18）。

## 進階：再往深一層

- **import 清單即行為畫像**：對 stripped 動態 binary，`nm -D` 的 import 清單就是一份行為摘要——`socket`/`recv` = 網路、`ptrace` = 反調試、`dlopen` = 動態載入、`system`/`execve` = 執行命令。惡意程式分析（→ [`malware_analysis`](../malware_analysis/README.md)）常從這份清單先畫像，再深逆。
- **PLT/GOT 的延遲綁定機制**：`call strlen@plt` 第一次跳進 PLT 樁、經 GOT 觸發 loader 解析真實位址、之後直接跳——這套 lazy binding 是 Ch 3（ELF 載入）和你的 [`elf_linking`](../elf_linking/README.md) 課的核心。逆向時知道「`@plt` 是外部函式的跳板」就夠用，細節在那兒。
- **版本指紋縮小範圍**：`nm -D` 顯示的 `GLIBC_2.34`（如本章 `__libc_start_main@GLIBC_2.34`）洩露 binary 需要的最低 glibc 版本 → 反推編譯環境/年代。靜態 binary 則可從 libc 函式的 byte pattern 認出 glibc 版本，幫 FLIRT 選對簽名庫。

## 本章重點整理

- **動態連結的 libc import 名字 strip 刪不掉**（在 `.dynsym`/`.rela.plt`）：`nm -D`/`readelf -r`/`call xxx@plt` 免費給你一份 import 地圖，這是逆向最好的線索來源。
- **靜態連結 + strip** 讓 libc 也無名——靠**函式指紋**（IDA FLIRT / Ghidra FunctionID，Ch 28）比對還原名字，只逆程式自己的邏輯。
- 資料結構指紋：**malloc 引數 = 物件大小**；**迴圈跟 offset 指標到 NULL = linked list 遍歷**；**三指標/內嵌 SSO = std::vector/std::string**（Ch 20）。
- **常數指紋**：.rodata 裡整齊的來歷不明大常數（`0x6a09e667` 等）多是 crypto/hash 魔數，反查認演算法比逆內部快得多（Ch 18）。
- 逆向前用 `file` 分清動態/靜態、C/C++，決定策略——這決定你有多少免費線索。

## 自我檢核

- [ ] 我知道 `nm` 說 no symbols 後，還能用 `nm -D`/`readelf -r` 撈出 libc import 名
- [ ] 我能解釋為什麼 strip 刪得掉 `main` 卻刪不掉 `strlen@plt` 的名字
- [ ] 看到 `mov $0x10,%edi; call malloc` 我知道在配置一個 16-byte 物件
- [ ] 看到迴圈裡 `mov 0x8(%rax),%rax` + `cmp $0,...; jne` 我認出 linked list 遍歷
- [ ] 我知道靜態 binary 要用 FLIRT/FunctionID 而非動態的 dynsym
- [ ] 看到 .rodata 整齊大常數我會先當 crypto 魔數反查（而非硬逆）

## 延伸閱讀

### 書籍

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：ELF 動態連結、PLT/GOT、符號表的權威解說；本章「為什麼 strip 刪不掉 import 名」的深化。
  - **讀哪幾章**：ELF 格式與動態連結、符號解析相關章。
- **《The IDA Pro Book》** — Chris Eagle（No Starch, 2nd ed.）
  - **定位**：FLIRT 簽名的權威來源。
  - **讀哪裡**：FLIRT signatures 章——靜態 binary 怎麼靠指紋庫還原庫函式名。

### 工具與文件

- **`nm -D` / `readelf --dyn-syms` / `readelf -r`**
  - **這是什麼**：撈動態符號與重定位的標準工具；stripped 動態 binary 的第一手 import 地圖。
  - **怎麼用**：拿到 binary 先 `file` 分類，動態的立刻 `nm -D` 看它借了哪些 libc 函式。
- **Ghidra Function ID / [FindCrypt 類工具](https://github.com/polymorf/findcrypt-ghidra)**
  - **這是什麼**：Function ID 做靜態庫函式指紋辨識；FindCrypt 掃 crypto magic constant。
  - **前提**：接你的 [`ida_pro`](../ida_pro/README.md)（FLIRT）與本課 Ch 18（crypto 指紋）、Ch 28（函式指紋）。

到這裡 Part 1 的靜態逆向技能齊了：你會讀 asm 的控制流與資料、看穿反編譯器、還原型別與 struct、認 idiom 與庫指紋。接下來用一個練習把全部串起來——靜態逆一個 strip 過的 crackme，還原它的密碼。

→ [練習 A：靜態逆一個 strip crackme](./practice-a-static-reverse-crackme.md)
