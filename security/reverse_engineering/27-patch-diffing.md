# Ch 27 Patch-Diff：從補丁還原漏洞

> **目標**：學會從廠商釋出的修補版 binary 逆推漏洞位置與成因——掌握 n-day 分析的核心手法，並建立「新增的檢查 = 原漏洞的倒影」這個根本洞見。

> **環境**：Linux x86-64，gcc 11+，binutils（objdump/diff），理解 CFG 與基本塊概念。BinDiff 與 Diaphora 為選讀工具，標「讀者自行重現」，本章 ground-truth 範例全程用 objdump 完成，無需商業插件。

---

## 為什麼需要？

廠商釋出安全補丁的方式通常只有一行：「修正了可能導致遠端執行程式碼的記憶體安全問題」。沒有 CVE 分析報告，沒有 PoC，沒有任何技術細節。

這個訊息對安全研究員來說意義有限。真正的問題是：

- 這個漏洞的技術成因是什麼？是 length truncation？integer overflow？
- 同樣的 pattern 在程式碼其他地方存在嗎？（variant hunting）
- 沒有部署補丁的系統暴露窗口有多大？攻擊者多容易找到 PoC？

回答這些問題的手法叫 **patch-diff**：對比修補前後的 binary，找出哪裡被改動，再反推「改了什麼 = 原本缺什麼 = 漏洞在哪」。

這是 n-day 分析（已有 CVE、等待補丁或部署補丁中）的核心手法，也是負責任的安全研究員評估風險曝光的工具，不是拿來做 1-day exploit 的捷徑。方向完全不同：前者是理解漏洞以評估影響與防禦，後者是找攻擊路徑。本章講的是前者。

---

## 先建立直覺

想像你是一個醫生，拿到兩份病人的 X 光片——一張是術前，一張是術後。你不知道手術過程，但你可以比對：「這裡多了一根鋼釘，說明這裡的骨頭原本裂了。」

Patch-diff 就是這個思路。

補丁後的 binary 相對於補丁前多了什麼？幾乎永遠是**新增的檢查**：

- `if (len > MAX) return -EINVAL;`  ← 原本沒有長度驗證
- `if (ptr == NULL) return;`       ← 原本沒有 NULL 檢查
- `if (offset + size < offset) ...` ← 原本沒有 integer overflow 檢查

這些新增的防禦動作反過來告訴你：在它們缺席的地方，程式用不受限的輸入去碰了記憶體。漏洞就在那裡。

**新增的檢查是原漏洞的倒影。** 這個洞見是 patch-diff 所有方法論的基礎。

---

## 核心方法論

### 取樣：對哪兩個版本做 diff？

正確的取樣方式直接影響訊號品質：

1. **連續版本差**：v1.2.3（有漏洞）vs v1.2.4（修補）。差異最小，雜訊最少，最適合定位單一漏洞。
2. **跳版本差**：v1.2.3 vs v2.0.0。差異過大，函式會被大量重構，很難確認哪個改動是安全修補、哪個只是功能改動。
3. **debug symbol 的有無**：有 symbol 時，函式名稱直接就是路標。沒有的話要依賴工具的相似度比對來對齊函式。

實務上，廠商有時同日釋出多個平台的 binary（Windows x64、Linux x64、ARM64），挑**最熟悉的架構、最不激進最佳化（-O1/-O2 而非 -O3 或 LTO）**的 binary 開始做。

### 定位：哪些函式被改動了？

大型 binary 可能有數千個函式。手動比對是不可能的，需要**函式相似度比對**工具：

**BinDiff**（Zynamics/Google，需 IDA 或 Ghidra 插件——讀者自行重現）：產生兩個版本函式的一對一配對表，並給出相似度分數（0.0–1.0）。分數降低的函式就是被改動的候選。

**Diaphora**（開源 IDA 插件——讀者自行重現）：原理類似 BinDiff，使用 SQLite 儲存函式指紋，可離線比對，適合重複分析同一組 binary。

這兩個工具的核心都是**函式指紋**——把函式的 CFG 結構、基本塊內容、呼叫樹等資訊摘要成可比較的表示，再做相似度計算。下一節解釋這個機制。

---

## 函式相似度比對的底層原理

### CFG 結構雜湊

把一個函式的 Control Flow Graph 展開：每個基本塊（basic block）是節點，跳轉是邊。比對兩個版本函式的 CFG：

- 節點數相同、邊數相同：高度可能是同一函式
- 某個版本多了 1 個節點、2 條邊：很可能是新增了一個 if 條件分支（= 新增的 bounds check）

### 基本塊指令雜湊

單靠 CFG 結構不夠——指令常數改變（例如 `MAX = 64` 改成 `MAX = 63`）不會改變 CFG 結構，但 MD5/xxhash 雜湊值會改變。工具通常同時使用：

1. **結構性相似度**：CFG 同構程度
2. **內容相似度**：基本塊指令序列的模糊雜湊（fuzzy hash，如 ssdeep）

兩者加權組合得出最終相似度分數。

### 為什麼這個原理很重要？

因為你不需要工具也能做粗版 patch-diff：用 `objdump -d` 把兩版 binary 的函式一條條倒出來，數基本塊數量、找多出來的 `cmp`/`jbe`/`test` 指令。這是本章 ground-truth 範例要示範的核心技能。

---

## Ground-Truth 真跑：用 objdump 抓出 bounds check

### 原始碼

```c
/* vuln.c — 有 bug 版，缺 bounds check */
#include <string.h>

void parse_name(char *dst, const char *src, unsigned src_len) {
    /* src_len 完全由呼叫者控制，沒有上限驗證 */
    /* 若 src_len > 63，dst[63] 之後就是 stack overflow */
    memcpy(dst, src, src_len);
    dst[src_len] = '\0';
}
```

```c
/* patched.c — 修好版，新增 bounds check */
#include <string.h>
#define MAX_NAME 63

void parse_name(char *dst, const char *src, unsigned src_len) {
    if (src_len > MAX_NAME)      /* 新增：把 src_len 截斷到安全上限 */
        src_len = MAX_NAME;
    memcpy(dst, src, src_len);
    dst[src_len] = '\0';
}
```

### 編譯

先用 `-O0` 編。原因下面「踩雷」會講——`-O1` 會把 `memcpy` inline 展開、把 `if(src_len>63)` 折成 `cmova`，diff 反而變髒。做 ground-truth 練習時，先用最乾淨的 `-O0` 看清楚「補丁長什麼樣」，再去對付優化過的真實 binary：

```bash
gcc -O0 -c vuln.c    -o vuln.o
gcc -O0 -c patched.c -o patched.o
```

### objdump 輸出對比

以下是 Linux x86-64 上 gcc 11.4 實跑輸出。有漏洞版（`objdump -d vuln.o`，只列 `parse_name`）：

```
0000000000000000 <parse_name>:
   0:   f3 0f 1e fa    endbr64
   4:   55             push   %rbp
   5:   48 89 e5       mov    %rsp,%rbp
   8:   48 83 ec 20    sub    $0x20,%rsp
   c:   48 89 7d f8    mov    %rdi,-0x8(%rbp)     # dst
  10:   48 89 75 f0    mov    %rsi,-0x10(%rbp)    # src
  14:   89 55 ec       mov    %edx,-0x14(%rbp)    # src_len
  17:   8b 55 ec       mov    -0x14(%rbp),%edx    # ← 直接把 src_len 當 memcpy 長度，無檢查
  1a:   48 8b 4d f0    mov    -0x10(%rbp),%rcx
  1e:   48 8b 45 f8    mov    -0x8(%rbp),%rax
  22:   48 89 ce       mov    %rcx,%rsi
  25:   48 89 c7       mov    %rax,%rdi
  28:   e8 00 00 00 00 call   2d <parse_name+0x2d>  # call memcpy（reloc 待填）
  2d:   8b 55 ec       mov    -0x14(%rbp),%edx
  30:   48 8b 45 f8    mov    -0x8(%rbp),%rax
  34:   48 01 d0       add    %rdx,%rax
  37:   c6 00 00       movb   $0x0,(%rax)         # dst[src_len]='\0'
  3a:   90             nop
  3b:   c9             leave
  3c:   c3             ret
```

修補版（`objdump -d patched.o`）：

```
0000000000000000 <parse_name>:
   0:   f3 0f 1e fa          endbr64
   4:   55                   push   %rbp
   5:   48 89 e5             mov    %rsp,%rbp
   8:   48 83 ec 20          sub    $0x20,%rsp
   c:   48 89 7d f8          mov    %rdi,-0x8(%rbp)
  10:   48 89 75 f0          mov    %rsi,-0x10(%rbp)
  14:   89 55 ec             mov    %edx,-0x14(%rbp)
  17:   83 7d ec 3f          cmpl   $0x3f,-0x14(%rbp)  # ← 新增！src_len 與 63 比
  1b:   76 07                jbe    24 <parse_name+0x24>  # ← 新增！<=63 就跳過截斷
  1d:   c7 45 ec 3f 00 00 00 movl   $0x3f,-0x14(%rbp)  # ← 新增！src_len = 63
  24:   8b 55 ec             mov    -0x14(%rbp),%edx
  27:   48 8b 4d f0          mov    -0x10(%rbp),%rcx
  2b:   48 8b 45 f8          mov    -0x8(%rbp),%rax
  2f:   48 89 ce             mov    %rcx,%rsi
  32:   48 89 c7             mov    %rax,%rdi
  35:   e8 00 00 00 00       call   3a <parse_name+0x3a>  # call memcpy
  3a:   8b 55 ec             mov    -0x14(%rbp),%edx
  3d:   48 8b 45 f8          mov    -0x8(%rbp),%rax
  41:   48 01 d0             add    %rdx,%rax
  44:   c6 00 00             movb   $0x0,(%rax)
  47:   90                   nop
  48:   c9                   leave
  49:   c3                   ret
```

### 解讀

offset `17`–`1d` 那三條指令，`cmpl $0x3f` + `jbe` + `movl $0x3f`，在有漏洞版**完全不存在**——它們就是補丁本身，其他每條指令兩版一字不差。

- `cmpl $0x3f,-0x14(%rbp)`：把 `src_len` 與 `0x3f`（= 63 = `MAX_NAME`）比較
- `jbe`（jump if below or equal）：若 `src_len <= 63`（安全）就跳過截斷
- `movl $0x3f,-0x14(%rbp)`：太大就截斷成 63

有漏洞版的 offset `17` 直接把呼叫者給的 `src_len` 讀進 `edx` 當 `memcpy` 的長度，中間沒有任何上限。如果呼叫者傳入 `src_len = 200`，`memcpy` 就往 `dst` 後方寫 200 bytes，蓋掉 `dst` 緩衝區後面的東西（stack 上就是返回地址）。

**結論：找到 `cmpl $0x3f` 這組檢查的缺席，就是找到漏洞。這就是 patch-diff 在做的事。**

---

## 從 diff 輸出看「基本塊增加」

上面的例子 CFG 結構直觀：有漏洞版是線性一條路，修補版多了一個條件分支（新增一個基本塊）。

用文字描述 CFG 差異：

```
有漏洞版 CFG：
  [entry: 存參數 → call memcpy → null term → ret]   (單一基本塊)

修補版 CFG：
  [entry: 存參數]
      ↓
  [cmpl $0x3f; jbe] ←── 新增決策節點（bounds check）
     /        \
  [movl $0x3f]  (fall through)
     \        /
  [call memcpy]
      ↓
  [null term → ret]
```

修補版多了一個決策節點。如果用工具計算 CFG 相似度，這個函式的相似度分數會從 1.0 降到約 0.7–0.8，會被標記為「已修改」。

---

## 實際流程：n-day 分析的完整步驟

1. **取得兩版 binary**：patch 前後各一份，確認是同一平台/架構
2. **函式對齊**：用 BinDiff/Diaphora（或手動 objdump diff）找出相似度降低的函式清單
3. **差異排序**：優先分析相似度降最多的函式，或名稱含 `parse_`/`decode_`/`handle_` 的函式
4. **反組譯比對**：在 IDA/Ghidra 中並排檢視，標記新增的基本塊
5. **識別新增的防禦動作**：是 bounds check？NULL check？integer overflow 保護？
6. **反推漏洞成因**：新增的檢查保護了什麼路徑？什麼輸入能觸發原本未保護的路徑？
7. **variant hunting**：在同一 binary 搜尋相同 pattern——其他地方有沒有同樣缺這個檢查的函式？

步驟 6–7 是防禦研究的核心：不是找 PoC，是找「這個漏洞模式在程式碼庫裡出現幾次，有沒有被漏掉的同類問題」。

---

## Variant Hunting：補丁的倒影不只一個

廠商修了一個函式，不代表同樣的 bug pattern 在程式碼庫裡已經清除乾淨。

以 `parse_name` 為例：如果程式碼裡還有 `parse_domain`、`parse_hostname`、`parse_email`，各自呼叫 `memcpy` 並傳入外部長度，它們可能都有同樣的問題——廠商只修了被報告的那個。

Variant hunting 的方法：

1. 確認修補模式（這裡是「呼叫 memcpy 前加 `cmp $0x3f`」）
2. 在整個 binary 搜尋所有呼叫 `memcpy`/`strcpy`/`sprintf` 的位置
3. 檢查每個呼叫點的前幾條指令，看有沒有 bounds check
4. 沒有的就是潛在 variant

這個方法連工具都不需要，一個 `objdump -d binary | grep -B5 "call.*memcpy"` 就能做粗篩。

---

## 接上 Ch 24：struct 還原讓 patch-diff 更清晰

Ch 24 講的是從 binary 還原 struct 的成員佈局。Patch-diff 可以接著用：

修補後新增的 NULL check 往往是在保護某個 struct 成員的解引用。如果你已經還原了 struct：

```c
struct conn {
    char *buf;      // offset 0x00
    size_t buf_len; // offset 0x08
    uint32_t flags; // offset 0x10
};
```

你看到修補版新增 `cmp QWORD PTR [rdi+0x00], 0` 的時候，你立刻知道：「保護的是 `conn->buf` 不能是 NULL」，原本的漏洞是 `buf` 可能為 NULL 時就被解引用。沒有 struct 還原，你只能說「保護了 `[rdi]` 的 NULL 解引用」，模糊許多。

---

## 對比與取捨

| 工具 / 方法 | 優點 | 缺點 | 適合場景 |
|---|---|---|---|
| objdump + 手動 diff | 零依賴、可重現、強制理解 | 大型 binary 不可行（數千函式） | 教學、小型 binary、已知目標函式 |
| BinDiff (IDA 插件) | 準確率高、視覺化 CFG 對比 | 需要 IDA 授權、不開源 | 企業/學術研究 |
| Diaphora (開源 IDA 插件) | 免費、SQLite 儲存可版本管理 | 仍需 IDA、大型 binary 慢 | 個人研究、長期追蹤同一產品 |
| bindiff (Ghidra 插件) | 完全開源免費 | 精度略低於 BinDiff | 開源工具鏈、教學 |
| 純 symbol diff (strip 前) | 直接看函式名稱清單差異 | 大多數 release binary 已 strip | 自己編的測試 binary |

---

## 踩雷集錦

### 1. 相似度高不代表沒有改動

BinDiff/Diaphora 給出 0.95 相似度，不代表那個函式沒有安全相關的改動。常數改動（`MAX = 255` → `MAX = 127`）、型別改動（`int` → `unsigned int`，影響比較的 signed/unsigned 語意）都不會大幅降低結構相似度，但可能是關鍵的安全修補。永遠要在相似度清單掃完後，對「高相似度但涉及長度/偏移計算的函式」再人工抽查一輪。

### 2. 最佳化等級不一致讓 CFG 爆炸

如果補丁前用 `-O2` 編，補丁後廠商換成 `-O3` 或開 LTO，整個 binary 的 CFG 結構可能面目全非。你看到的 CFG 差異有 70% 是編譯器最佳化造成的，不是漏洞修補。碰到這種情況，先用 `file`、`strings`、`readelf -d` 確認兩版 binary 是否用同一個工具鏈/版本編出來的，再決定要不要降一個最佳化等級重編自己的測試版本。

### 3. 把 inline 函式的展開誤判為漏洞，以及「乾淨的 diff 只存在於 -O0」

兩件相關的事。

第一，編譯器在 `-O1` 以上會 inline 小函式。有漏洞版可能有 `call check_bounds`，修補版把同樣的 check inline 進去，導致你看到 CFG 節點數增加、多了 `cmp` 指令——但這不是新增的安全保護，只是 inline 展開。驗證方式：grep `check_bounds`（或其雜湊）看它在兩版是否都存在。

第二，也是更常見的坑：**上面那個乾淨的 `cmpl $0x3f; jbe` diff，只在 `-O0` 長那樣。** 你會以為把同一份 `patched.c` 用 `-O1` 編，只是多一條 `cmp` 指令——實跑會嚇到你。`-O1` 下 gcc 做了兩件事：(1) 把 `if(src_len>63) src_len=63` 折成無分支的 `mov $0x3f,%ecx; cmp %ecx,%edx; cmova %ecx,%edx`（`cmova` = conditional move，補丁的形狀從「多一個基本塊」變成「多一條 cmov」，CFG 節點數根本沒增加）；(2) 把 `memcpy` 整個 inline 展開成一長串依長度分派的 `mov`（4-byte/2-byte/8-byte 對齊處理），原本乾淨的 `call memcpy` 直接消失。結果是 `-O1` 版 diff 出來一大坨看似無關的變化，真正的 bounds check（那條 `cmova`）藏在裡面。**教訓：ground-truth 練習用 `-O0` 看清補丁本質；但真實 release binary 幾乎都 `-O2`，你要習慣「補丁可能是一條 `cmova` 或一條 `cmp` 而非一個完整分支」。這正是為什麼 patch-diff 要靠函式對齊工具，而不是肉眼掃 CFG 形狀。**

### 4. 對齊 binary 卻忘了對齊函式呼叫慣例

Windows x64 和 Linux x64 的 calling convention 不同（前者用 rcx/rdx/r8/r9，後者用 rdi/rsi/rdx/rcx）。如果你拿到的兩版 binary 一個是 Windows build 一個是 Linux build，函式參數位置完全錯開，你會以為 bounds check 被移掉了，其實是暫存器不同。永遠確認兩版 binary 在同一個 OS 平台。

### 5. 過度依賴函式名稱

有 debug symbol 的時候很容易：找到 `is_valid_length` 在補丁後新增就知道了。但去掉 symbol 後，工具給的對齊可能把「舊的 parse_a」對到「新的 parse_b」——因為它們 CFG 結構剛好接近。一定要從彙編語意確認兩個函式的用途真的一樣，才能說某個差異是安全修補。

---

## 進階：再往深一層

### 二進位層級的 semantic diff

CFG 結構比對抓的是「形狀」，語意比對抓的是「等價性」。有一些學術工具（如 Kam1n0、BinDiff 的部分模式）嘗試做 semantic equivalence checking：把基本塊的指令序列轉成 SMT 公式，用 Z3/Boolector 判斷兩個序列是否等價。

這比 CFG 比對精確，但計算代價高得多，目前只在函式層級或基本塊層級可用，大型 binary 不現實。但它的概念很重要：純指令雜湊比對在 NOP padding 或暫存器重新分配時會誤報差異，semantic diff 不會。

### 時序 diff（version timeline）

追蹤一個 CVE 的修補歷程，不只看「補丁前 vs 補丁後」，而是看整個版本線：

- v1.2.0 引入了某個 pattern
- v1.2.2 部分修補（不完整）
- v1.2.4 完全修補

這個時序分析能揭露「不完整的修補」——廠商的第一次嘗試修了表面症狀，沒有根治成因，進而在 v1.2.3 被 bypass。這類分析在 CVE 的 bypass/variant 研究中相當常見。

### 跨架構 patch-diff

有時廠商同一份程式碼編出 x86-64 和 ARM64 版，你手上只有其中一個版本的符號表或原始碼片段。跨架構 binary 比對的挑戰是指令集完全不同，只能靠語意等價——這是目前研究前沿，Trex 等學術工具嘗試用神經網路把不同架構的指令映射到同一個語意嵌入空間再做比對。Ch 28 會繼續延伸二進位相似度的更多手法。

---

## 本章重點整理

1. Patch-diff 的核心洞見：**新增的檢查是原漏洞的倒影**。找出補丁版多出來的 `cmp`/`test`/`jbe`，就找到了原本缺少的保護。

2. Ground-truth 手法：`gcc -O1 -c`，`objdump -d`，比對兩版函式的基本塊數量與 `cmp` 指令的有無。不需要商業工具就能定位漏洞。

3. 函式相似度工具（BinDiff/Diaphora）的原理是 CFG 結構 + 基本塊雜湊，相似度降低的函式是優先分析對象，但高相似度不代表安全無虞。

4. Variant hunting 是防禦研究的核心：確認補丁模式後，在整個 binary 搜尋同樣缺少這個保護的其他位置。

5. 常見陷阱：最佳化等級不一致、inline 展開、calling convention 差異、高相似度函式中的語意細節改動。

---

## 自我檢核

1. 用上面的 `vuln.c` 和 `patched.c` 自己跑 `gcc -O1 -c`，再用 `objdump -d` 輸出，確認你能在輸出中找到 `cmp $0x3f`。如果看不到，說明最佳化把 check 折疊掉了——改用 `-O0` 再試一次，理解差異。

2. 在修補版 `objdump` 輸出裡，`jbe` 後面的兩條路徑分別是什麼？把它們和 C 原始碼的 if-else 對應起來。

3. 假設 `parse_name` 旁邊還有一個 `parse_email(char *dst, const char *src, unsigned src_len)`，函式體和 `vuln.c` 的 `parse_name` 一模一樣但缺 bounds check。描述你要如何確認它是 variant，以及確認後應該向誰回報、用什麼格式。

4. 如果兩版 binary 用了不同的 gcc 版本（一個 gcc 9，一個 gcc 11），可能出現哪些「假差異」？列出至少兩種。

5. BinDiff 給你一份清單，某個函式相似度 0.97。你會不會直接跳過它？為什麼會或不會？

---

## 延伸閱讀

1. **Google Project Zero — "One-Day Exploits and Patch Diffing"**
   Project Zero 團隊的公開 blog 有多篇 n-day 分析，展示真實 CVE 的 patch-diff 過程與思路。搜尋 "project zero patch diffing" 找具體文章。

2. **Zynamics BinDiff 白皮書**（Google 收購後已開放）
   說明函式相似度比對的演算法細節，包含 CFG 圖同構的處理方式與相似度計算公式。網址：google.github.io/bindiff

3. **Diaphora 官方文件與 Joxean Koret 的演講**
   Diaphora 作者在多場安全會議（BHEU、Ekoparty）的演講從實際漏洞分析出發講工具設計取捨，比文件更有實用價值。在 YouTube 搜尋 "Joxean Koret Diaphora" 或 BHEU 2021 錄影。

4. **"Variant Analysis" — Pwnie Express / GitHub Security Lab 系列文章**
   GitHub Security Lab 的 CodeQL variant hunting 案例——雖然用的是原始碼層級工具，但分析思路（確認 bug pattern → 搜尋整個程式碼庫的同類 pattern）和 binary 層級 variant hunting 完全對應，可用來理解方法論本質。

5. **Chris Domas — "RE2"（DEF CON 24）**
   從底層視角講 binary 分析自動化，雖然主題不完全是 patch-diff，但他對「binary 差異的語意等價」的討論直接影響後來工具的設計方向。DEF CON YouTube 頻道有錄影。

---

Patch-diff 是把 binary 分析技能和安全研究連接起來的關鍵橋樑。掌握了「新增的檢查 = 原漏洞的倒影」這個洞見，你就能從廠商每次釋出的安全更新中主動萃取技術情資，用於評估暴露風險、找出 variant、理解漏洞成因——而不是等待別人的分析報告。

→ [Ch 28 二進位相似度與函式指紋](./28-binary-similarity-fingerprinting.md)
