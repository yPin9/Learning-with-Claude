# Ch 2 — 靜態分析情境快捷鍵

> 目標：不用滑鼠在 disassembly 裡跑完整條分析動線 — 改名、改型別、交叉引用、跳轉、註解、data/code 切換。

## 動線思維：從一個 address 開始

拿到一個 binary，絕大多數人會這樣反射性操作：

```
  看 Strings  ──→  找線索 ──→  雙擊跳 xref ──→  看 disasm
                                                    │
      ┌─────────────────────────────────────────────┘
      ▼
   改名 / 改型別 / 加註解  ──→  看呼叫者 ──→  繼續往上爬
```

這一章的目標是讓這整條路全部可以用鍵盤做完。遇到每個「點」你都會有個反射動作的鍵可以按。

## 分類速查表

### 改名 / 改型別

| 鍵 | 動作 | 在哪用 |
|---|---|---|
| `N` | rename | function / variable / label，**最常用** |
| `Y` | set function prototype / 改型別 | function、stack var、global |
| `Shift+F1` | 打開 Local Types | 要寫新 struct / enum 時 |
| `Alt+Q` | apply structure to operand | 看到 `[rbx+0x18]` 想套 struct |
| `T` | apply struct member at operand | 游標停在 operand 上按，展開對應 struct |

### 跳轉 / 回到歷史

| 鍵 | 動作 |
|---|---|
| `G` | go to address（可填 `0x401000` 或 symbol name） |
| `Ctrl+P` | go to function（按名字搜） |
| `Esc` | **上一個位置**（= 瀏覽器的「上一頁」） |
| `Ctrl+Enter` | 下一個位置 |
| `Space` | disasm 的 Text / Graph mode 切換 |
| `Tab` | 在 IDA View 與 Pseudocode 之間跳，**維持同一個位址** |

`Esc` 這顆鍵我個人每天按超過 300 次。隨便點一個 xref 跳出去，`Esc` 回來。

### 交叉引用

| 鍵 | 動作 |
|---|---|
| `X` | List cross references **to** — 誰參照這裡 |
| `Ctrl+X` | List cross references **from** — 這裡參照了誰 |
| `Ctrl+J` | List of jumps（只看跳轉 xref） |

`X` 是逆向的核心按鍵之一：看到一個可疑 function，按 `X` 看誰呼叫它，就能沿呼叫鏈往上爬。

### 註解

| 鍵 | 動作 |
|---|---|
| `:` | 一般註解（只在當前位置顯示） |
| `;` | Repeatable comment（當前位置 + 所有 xref 過來的地方都顯示） |
| `/` | Pseudocode 註解（在 F5 偽代碼上加） |
| `Insert` (非 Mac) / `\` | function 上方的大區塊註解 |

**Repeatable 的好處**：在一個 utility function 的入口註解 `; allocate buffer from pool`，所有呼叫它的地方都會看到同一段註解。不用每個 call site 重貼。

### Data / Code 切換

反組譯最常見的誤判是「這段到底是 code 還是 data」。這組鍵用來糾正：

| 鍵 | 動作 |
|---|---|
| `U` | Undefine（撤銷當前 bytes 的分析，變回 raw） |
| `C` | Convert to code（把這裡當指令分析） |
| `D` | Convert to data byte（反覆按會在 byte/word/dword/qword 之間循環） |
| `A` | Convert to ASCII string |
| `*` | Convert to array（選一段資料變陣列） |
| `O` | Convert operand to offset（立即數改成位址，IDA 會幫你標 xref） |

**踩雷**：`C` 轉成 code 可能會「咬」到下一塊 data，把它也拆成指令。發現後先 `U` 整塊 undefine，再重新精準挑範圍 `C`。

### 搜尋

| 鍵 | 動作 |
|---|---|
| `Alt+T` | Text search（disasm 上的文字搜） |
| `Ctrl+T` | **重複** 上一次 text search |
| `Alt+B` | Binary search（十六進位 / byte pattern） |
| `Ctrl+B` | 重複上一次 binary search |
| `Alt+I` | Search immediate value（找所有用到這個常數的地方） |

### Stack frame / variable

| 鍵 | 動作 |
|---|---|
| `Ctrl+K` | 打開 stack frame window（編輯 local vars / args） |
| `Alt+K` | 改當前指令的 SP delta（修正 stack pointer 估計錯誤） |
| `K` | 改 stack variable 型別 |

## 範例動線：從一個 string 往上爬

假設在 Strings window 看到 `"license check failed"`：

```
1. 雙擊字串                    →  跳到 .rodata 的位址
2. X                           →  列出所有參照這個字串的地方
3. 雙擊第一個 xref             →  跳到某 function 裡的 lea
4. 游標停在 function 開頭
5. N → 改名為 check_license_failed
6. Y → 改 prototype 為 void check_license_failed(int)
7. X → 看誰呼叫這個 function
8. 雙擊第一個 caller
9. Esc Esc Esc                 →  一路跳回 Strings window
```

整條動線零滑鼠。這套反射比任何 IDAPython 都重要，因為它是你所有自動化的人工基準 — 你必須先能手動做得乾淨，才能寫 script 把它複製。

## 一定要改掉的預設

`Options → General → Disassembly` 建議打開這兩個：

- **Line prefixes (graph)**：graph mode 顯示位址，debug 時方便對。
- **Auto comments**：IDA 會在一些指令旁邊加上「Load Effective Address」等提示，新手期開一下，熟了之後關掉會比較清爽。

Keyboard shortcut 衝突：9.x 的 `Ctrl+1`、`Ctrl+2` 被保留做 workspace 切換；如果你是鍵盤狂人想重綁，`Options → Shortcuts` 可以改。

## 常見踩雷

- **按 `N` 沒反應**：你的游標不在一個可命名的目標上（例如停在指令 mnemonic 上就無效，要停在 operand 或 function 名稱上）。
- **`X` 跳出來是空清單**：沒人參照這裡 — 可能是 dead code，或這個 function 是透過 indirect call 呼叫的（IDA 沒辦法靜態解析所有 `call [rax]`）。
- **改名後要恢復原名**：清空輸入框後按 Enter，會變回 `sub_XXXXXX`。

## 動手練習

找一個簡單的 CTF crackme（例如 pwnable.kr 的 `fd`、picoCTF 的任何 reverse 題），不用寫 script、全程鍵盤：

1. `Shift+F12` 找可疑字串。
2. 雙擊跳過去。
3. `X` 往 caller 爬，爬到 main。
4. `N` 把路上每個重要 function 改名。
5. `;` 在關鍵位置加 repeatable comment。
6. `Esc` `Ctrl+Enter` 來回跳比較。

目標是解完整題不碰滑鼠。這是 **練習 A** 的彩排。

## 自我檢核

- [ ] 記得 `N` / `Y` / `X` 三大殺手鍵
- [ ] 能用 `G` + `Esc` 完成「跳過去 + 跳回來」的動線
- [ ] 知道 repeatable comment（`;`）和一般註解（`:`）的差異
- [ ] 能用 `U` / `C` / `D` 修正 IDA 的 code/data 誤判
- [ ] `Tab` 能在 disasm 與 pseudocode 間切換

下一章進 Pseudocode window，學 F5 專屬的快捷鍵 — 改 function prototype、改 LVAR、hide casts。

→ [Ch 3 Decompiler (F5) 情境快捷鍵](./03-decompiler-hotkeys.md)
