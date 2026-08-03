# Ch 0 — 環境與 ground-truth 逆向迴圈

> **目標**：把逆向工具鏈架好，並建立這門課的靈魂訓練法——**ground-truth 迴圈**：拿一份你寫的 source、編譯、strip、然後逆回去，再對照原始碼檢查你逆對了沒。同時親眼看一次逆向的第一原則：**你逆的不是你寫的 code，是編譯器改寫過的 code。**

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb + readelf + nm + strings + file。反編譯器（Ghidra 免費 / IDA）另裝，本課的可重現真跑以 objdump/gdb 為準，反編譯器輸出會標明「讀者自行重現」。

## 為什麼需要 ground-truth 迴圈？

逆向和讀 source 有一個殘酷的不對稱：讀 source 時，作者的意圖白紙黑字寫在那裡（變數名、註解、結構）；逆向時，**這些全被編譯器丟掉了**。你面對的是暫存器、位移、跳轉——意圖要你自己**重建**。

問題來了：你怎麼知道你重建對了？逆向最危險的不是讀不懂，是**自信地讀錯**——你腦補了一個 struct、一條邏輯，看起來很合理，但根本不是原意。而 binary 不會糾正你。

解法是這門課的核心訓練器材：**ground-truth 迴圈**。

```
   你寫 source  ──gcc──►  binary  ──strip──►  剝光的 binary
       │                                            │
       │  （這是標準答案，先蓋起來別看）            │ 逆向它
       ▼                                            ▼
   原始 secret(x)                          你重建的 secret'(x)
       └──────────────► 對照 ◄──────────────────────┘
                   逆對了嗎？哪裡腦補錯了？
```

拿**你自己寫的** source 編譯、strip、逆回去，再打開原始碼對答案。逆錯了當場抓到——「我以為這是乘法，其實編譯器把除法優化成乘法了」。這個即時回饋迴圈，就是把逆向從「玄學」變成「可練的技能」的關鍵。這正是姊妹課 [`codebase_case_studies`](../../soft_skills/codebase_case_studies/README.md) 用釘死 source 當標準答案的同一個哲學——只是這裡的「陌生 code」被剝到只剩機器碼。

> 這門課是 [`reading_code`](../../soft_skills/reading_code/README.md)「讀碼即逆向」的鏡像：那門把逆向直覺借來讀 source，這門走到光譜的極端——**source 一個字都沒有**，你只有 binary。

## 先建立直覺：編譯器把你的 code 改到你認不得

在架環境之前，先看一眼你即將面對的東西。這段 C 你一看就懂：

```c
int secret(int x){ if(x%2==0) return x*3+1; return x/2; }
int main(){ int s=0; for(int i=0;i<5;i++) s+=secret(i); printf("%d\n",s); return 0; }
```

現在看它在兩種優化等級下變成什麼。**`-O0`（不優化），`secret` 還是一個你認得出的函式**（真跑 `objdump -d`）：

```asm
0000000000001149 <secret>:
    1149:  endbr64
    114d:  push   %rbp                  ; ┐ 標準 prologue
    114e:  mov    %rsp,%rbp             ; ┘ 建 stack frame
    1151:  mov    %edi,-0x4(%rbp)       ; 參數 x 存進 stack（edi = 第一個參數）
    1154:  mov    -0x4(%rbp),%eax
    1157:  and    $0x1,%eax             ; ┐ x & 1
    115a:  test   %eax,%eax             ; ┤ 檢查最低位
    115c:  jne    116c <secret+0x23>    ; ┘ 奇數 → 跳去 else（x%2==0 的 asm 化身）
    115e:  mov    -0x4(%rbp),%edx       ; ┐
    1161:  mov    %edx,%eax             ; ┤ x*3+1：
    1163:  add    %eax,%eax             ; ┤   eax = x+x = 2x
    1165:  add    %edx,%eax             ; ┤   eax = 2x+x = 3x
    1167:  add    $0x1,%eax             ; ┘   eax = 3x+1
    116a:  jmp    1178 <secret+0x2f>
    116c:  mov    -0x4(%rbp),%eax       ; ┐ x/2（signed）：
    116f:  mov    %eax,%edx             ; ┤
    1171:  shr    $0x1f,%edx            ; ┤  取正負號位（x>>31）
    1174:  add    %edx,%eax             ; ┤  負數時 +1 做 rounding
    1176:  sar    %eax                  ; ┘  算術右移 1 = /2
    1178:  pop    %rbp
    1179:  ret
```

兩個東西現在就值得記住（後面 Part 1 會系統化）：

1. **`x%2==0` 不見了，變成 `and $0x1; test; jne`**——編譯器知道「除以 2 的餘數 = 最低位」。
2. **`x/2` 不是一條除法指令**，是 `shr $0x1f; add; sar` 三條——這是「signed 整數除以 2」的**編譯器慣用語（compiler idiom）**。為什麼這麼繞？因為 C 的整數除法向零取整，而 `sar`（算術右移）向負無窮取整，對負數會差 1，所以要先用 `shr $0x1f`（取符號位）補償。你逆向時看到 `shr 31; add; sar` 要一眼認出「這是 signed /2」——這種認 pattern 的能力，就是這門課要練的。

現在看 **`-O2`（優化開）** 的 `main`（真跑）：

```asm
0000000000001060 <main>:
    1060:  endbr64
    1064:  sub    $0x8,%rsp
    1068:  mov    $0x16,%edx            ; ← 22！整個迴圈 + secret 被算完了
    106d:  mov    $0x1,%edi
    1072:  xor    %eax,%eax
    1074:  lea    0xf89(%rip),%rsi      ; "%d\n"
    107b:  call   1050 <__printf_chk@plt>
    ...
```

`secret` **消失了**。整個 `for` 迴圈呼叫 `secret(0..4)` 加總的結果——22（`0x16`）——被編譯器在**編譯時就算完**，塞成一個常數。`objdump` 裡根本沒有 `<secret>` 這個 label 了。

這就是逆向的第一課，也是為什麼它難：**編譯器 inline、常數摺疊、strength reduction、向量化……你寫的結構在 binary 裡可能面目全非、甚至根本不存在。** 你逆的是編譯器的產物，不是你的原稿。Ch 2 會系統講編譯器對你的 code 做了哪些變換。

## 第一步：架工具鏈

本課的**可重現真跑骨幹**是這幾樣，WSL/Linux 幾乎都自帶或一裝就有：

```bash
$ for t in gcc objdump gdb readelf nm strings file; do
    printf "%-9s %s\n" "$t" "$(command -v $t || echo MISSING)"
  done
gcc       /usr/bin/gcc
objdump   /usr/bin/objdump
gdb       /usr/bin/gdb
readelf   /usr/bin/readelf
nm        /usr/bin/nm
strings   /usr/bin/strings
file      /usr/bin/file
```

| 工具 | 逆向用途 |
|---|---|
| `objdump -d` | 靜態反組譯（disassemble），看 asm。本課最常用 |
| `readelf` / `file` | 看 binary 的骨架：架構、段、符號、動態連結 |
| `nm` | 列符號表（strip 後就沒了——這本身是資訊） |
| `strings` | 抽可讀字串，逆向的第一個線索來源 |
| `gdb` | 動態逆向：斷點、看記憶體、改執行流（Part 2 主力，接你的 [`gdb`](../gdb/README.md) 課） |

**反編譯器（decompiler）** 是現代逆向的核心武器，它把 asm 還原成類 C 的 pseudocode：

- **Ghidra**（NSA 開源、免費）——本課的預設，[ghidra-sre.org](https://ghidra-sre.org/) 下載，需 JDK。
- **IDA Pro + Hex-Rays**——業界標準，你已經有 [`ida_pro`](../ida_pro/README.md) 課；本課會交叉引用它的 F5。
- **radare2 / Cutter**、**Binary Ninja**——其他選擇。

> 本課的紀律：**凡是 objdump/gdb/readelf 能產出的，一律真跑貼真實輸出**（可重現）。反編譯器（Ghidra/IDA）的 pseudocode 輸出，除非章節明確標「實跑」，否則標「反編譯器預期輸出，讀者自行以 Ghidra/IDA 重現」——因為反編譯器版本不同輸出會有差異，我們不假裝跑過。這和 Ch 0 的 ground-truth 精神一致：不確定的不硬說成確定。

## 第二步：跑一次完整的 ground-truth 迴圈

這是你之後每個練習都會做的循環。現在走一遍：

```bash
# 1) 寫一份你「知道答案」的 source
$ cat > gt.c <<'EOF'
#include <stdio.h>
int secret(int x){ if(x%2==0) return x*3+1; return x/2; }
int main(){ int s=0; for(int i=0;i<5;i++) s+=secret(i); printf("%d\n",s); return 0; }
EOF

# 2) 編譯 + 跑，記下正確行為
$ gcc -O0 -g -o gt_O0 gt.c && ./gt_O0
22

# 3) strip 掉符號，模擬「陌生 binary」
$ cp gt_O0 gt_stripped && strip gt_stripped
$ nm gt_stripped
nm: gt_stripped: no symbols          # ← 名字全沒了，這才是真實逆向的起點

# 4) 逆它（先別看 gt.c），重建 secret 在幹嘛
$ objdump -d gt_stripped | less

# 5) 對答案：打開 gt.c，檢查你重建的邏輯對不對
```

第 3 步的 `no symbols` 是關鍵——**strip 之後，`secret`/`main`/`s`/`x` 這些名字全部消失**，你只剩位址和指令。真實世界你拿到的 binary 幾乎都是 stripped 的。這門課教你在這種「無名世界」裡重建意圖。

> 進階變體（Ch 27 會用）：`git worktree` 式地編譯**同一份 source 的兩個版本**（改一行、或換優化等級），做 binary diff，看差異——這是 patch-diff 找漏洞的基礎。

## 底層機制：一個 binary 從檔案到執行

逆向前你得知道手上這團 bytes 的骨架。`file` + `readelf` 給你第一層地圖：

```bash
$ file gt_stripped
gt_stripped: ELF 64-bit LSB pie executable, x86-64, ... stripped

$ readelf -h gt_stripped | grep -E 'Type|Machine|Entry'
  Type:       DYN (Position-Independent Executable file)
  Machine:    Advanced Micro Devices X86-64
  Entry point address:  0x1060
```

```
   ELF 檔案                         載入到記憶體後
  ┌──────────────┐                ┌──────────────┐ 高位址
  │ ELF header   │ 架構/entry     │  stack       │
  ├──────────────┤                ├──────────────┤
  │ .text (code) │──── 載入 ────► │  .text 映射   │ ← 你逆的指令在這
  │ .rodata      │                │  .data/.bss  │
  │ .data/.bss   │                │  heap        │
  │ symbol/reloc │ strip 後消失   │  libc 映射    │
  └──────────────┘                └──────────────┘ 低位址
```

Entry point `0x1060` 不是 `main`——是 libc 的啟動樁 `_start`，它做完初始化才呼叫 `main`。這類「找到真正的 main」的技巧 Ch 3（ELF 解剖）和 Part 1 會細講。這裡先有個骨架概念：**你逆的 `.text` 只是整個載入映像的一小塊，外面還有 libc、動態連結、stack/heap。**

## 踩雷集錦

1. **以為 binary 忠實反映 source**：最致命的錯覺。編譯器會 inline、刪死碼、把迴圈展開/向量化、把 `x/2` 變 `sar`、把 `x*10` 變 `lea`。你看到的是**優化後**的產物。逆向時永遠假設「編譯器動過手腳」。
2. **只靠反編譯器的 F5，不看 asm**：Ghidra/IDA 的 pseudocode 很香，但它會**猜錯型別、掰出不存在的變數、把一個 struct 拆成散落的 offset**。反編譯器是輔助不是真相，卡住時一定回去讀 asm（Ch 8 專講反編譯器怎麼騙你）。
3. **忽略優化等級**：拿 `-O0` 的教材例子去對照真實世界的 `-O2` binary，會覺得「怎麼完全不一樣」。真實 release binary 幾乎都 `-O2`/`-O3`。本課會兩者都給，但你要知道自己在看哪個。
4. **不建 ground-truth 就硬逆**：初學就找一個沒有答案的 strip binary 硬啃，逆錯了也不知道。**先用你自己編的 binary 練**，有標準答案能對，練成了再上無答案的真實目標。
5. **strip 了才想起要留符號**：自己做 ground-truth 練習時，先留一份**沒 strip、帶 `-g`** 的版本當答案，另一份 strip 的拿來逆。別把唯一的副本 strip 掉。

## 進階：再往深一層

- **裝 Ghidra 並試 headless**：`analyzeHeadless` 能腳本化批次反編譯，Part 4「腳本化逆向」會用。先把 GUI 版跑起來，對 `gt_stripped` 按 F5 看它把 `secret` 還原成什麼——你會發現它連 `x/2` 的 `sar` idiom 都可能還原成 `x/2` 或還原成 `x>>1`（看版本），這正是「反編譯器也在猜」的活教材。
- **對照不同編譯器**：同一份 source 用 `gcc` vs `clang` 編，asm 慣用語不同（不同 idiom 指紋）。逆向老手能從 asm 風格猜出編譯器。Ch 10 會講。
- **`objdump -d` 不夠時**：它對付不了自修改 code、packed binary、間接跳轉密集的混淆 code——那時要動態逆向（Part 2）或更強的工具。Ch 23 講對抗混淆。

## 本章重點整理

- 逆向的第一原則：**你逆的是編譯器改寫過的 code，不是原稿**——inline、常數摺疊、strength reduction 會讓你的結構面目全非。
- **ground-truth 迴圈**（寫→編→strip→逆→對答案）是這門課的核心訓練器材：拿有標準答案的 binary 練，逆錯當場抓到。
- 可重現真跑骨幹 = gcc + objdump + gdb + readelf；反編譯器（Ghidra/IDA）是核心武器但輸出會標「讀者自行重現」。
- strip 移除所有符號名——真實世界的 binary 幾乎都 stripped，這門課教你在「無名世界」重建意圖。

## 自我檢核

- [ ] 我的工具鏈（gcc/objdump/gdb/readelf/nm/strings/file）都在，Ghidra 或 IDA 至少裝好一個
- [ ] 我跑過一次完整 ground-truth 迴圈：寫 gt.c → 編 → strip（確認 `no symbols`）→ objdump 逆 → 對照 source
- [ ] 我能解釋為什麼 `-O2` 下 `secret` 會消失，`-O0` 下卻是完整函式
- [ ] 我認得出 `shr $0x1f; add; sar` 是「signed 除以 2」的編譯器慣用語（不是三個無關操作）
- [ ] 我理解為什麼「不建 ground-truth 就硬逆」對初學是壞習慣

## 延伸閱讀

### 書籍

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：Linux/ELF/x86-64 二進位分析的最佳入門，工具與原理並重，本課 Part 0–1 的主要對照書
  - **讀哪幾章**：Ch 1–2（ELF 與載入，對應本課 Ch 3）、Ch 5–6（disassembly 原理）
- **《Reverse Engineering for Beginners》(a.k.a. RE101)** — Dennis Yurichev（免費，[開放下載](https://beginners.re/)）
  - **定位**：從「一行 C 對應到什麼 asm」教起，超大量 compiler idiom 對照，本課 Ch 4–10 的最佳題庫
  - **讀哪裡**：Part I（從 Hello World 到函式/迴圈/struct 的 asm 對照）；跳著讀當字典

### 官方文件 / 工具

- **[Ghidra 官方](https://ghidra-sre.org/) + [Ghidra Book《The Ghidra Book》(No Starch)](https://nostarch.com/GhidraBook)**
  - **讀哪裡**：官方 Getting Started 先把它跑起來；書的前幾章講反編譯器怎麼運作（對應本課 Ch 8）
  - **前提**：裝好 JDK
- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時看「C/C++/Rust source ↔ asm」對照的網站，逆向練習的神器——想確認某個 asm pattern 對應什麼 source，反查它
  - **怎麼用**：貼 source、選 gcc/clang + 優化等級，右邊即時出 asm；本課全程可拿它當 ground-truth 的快速版

工具與訓練法就位。下一章我們把這門課定位清楚：逆向和讀 source 到底是不是同一件事？它們共用什麼、又在哪裡分道揚鑣？

→ [Ch 1 逆向即讀碼：reading_code 的鏡像](./01-reversing-is-reading-code.md)
