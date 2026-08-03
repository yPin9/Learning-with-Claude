# Ch 25 逆向筆記與外化

> **目標**：建立一套可持續的逆向外化工作流——在工具內重命名/標注，在工具外寫 journal，讓你的逆向進度不會在關掉 Ghidra 後消失。

> **環境**：Ghidra 11.x（Codebrowser 操作標「讀者自行重現」）、objdump（可直接在 WSL/Linux 跑）、任意 markdown 編輯器。

---

## 為什麼需要？

逆向有一個根本困難是 source-reading 沒有的：**binary 沒有名字**。

讀 Redis 原始碼時，你看到 `dictAddRaw`，名字本身就是一半的文件。逆向一個編譯後的 binary，你看到的是 `FUN_00401380`，或更糟，只有一個 `0x401380`。你不知道它做什麼，不知道誰呼叫它，不知道它的參數型別。

所有這些缺失的資訊，都要你自己填回去。

沒有系統地外化這些資訊，有三種後果：

1. **會話斷線即失憶**：昨天搞清楚的 `FUN_004026a0` 是解密函式，今天重開 Ghidra，又是一堆 `FUN_`。
2. **xref 陷阱**：你在某個函式裡猜出一個局部假設，但這個函式被 40 個地方呼叫，你不知道哪些 call site 會讓假設崩潰。
3. **認知負荷爆炸**：腦子同時要記住「這裡 rax 是指標、eax 是返回值、rbx 是 counter、rcx 是 strlen 結果」，沒有文字承接，工作記憶很快就滿。

外化不是整理癖，是讓逆向可以持續的基礎設施。

---

## 先建立直覺

想像你在拼一個沒有圖案參考的 5000 片拼圖。

有人會從邊框開始，把確定的部分先固定。有人從顏色分堆，把類似的放在一起。沒有人靠腦子記住每一片的位置——他們把拼好的部分放在桌上，讓實體排列替代記憶。

逆向外化的邏輯完全相同：你正在拼的是「程式邏輯的圖案」，而你的外化工具——Ghidra 的 Label、Comment，你的 journal，你的 struct 定義——就是桌面。

每次你命名一個函式或寫下一個假設，你就把一塊拼好的區域固定了，不用再靠腦子記。

---

## 工具內的外化：Ghidra 的重命名與標注體系

這是逆向外化最直接的層次，也是最划算的投資。

### 函式重命名（最高 ROI 的操作）

Ghidra 中每個函式預設名稱是 `FUN_<address>`。你只要在 Listing 或 Decompiler 視窗對函式名雙擊或按 `L`，就能重命名。

重命名一個函式後，Ghidra 會把所有 xref——所有呼叫這個函式的地方——自動更新顯示新名稱。這是複利效應的來源：**命名一個函式，同時讓 40 個呼叫點變可讀**。

命名策略：

- **先用描述性動詞前綴**：`maybe_decrypt_payload`、`likely_parse_header`、`check_magic_bytes`。前綴 `maybe_`/`likely_` 明確標示你還不確定。
- **確認後去掉前綴**：等你驗證了再改成 `decrypt_rc4_payload`。
- **不要等完全確定才命名**：帶 `?` 的名字比 `FUN_00401380` 好一百倍。

```
# Ghidra 重命名快捷鍵（讀者自行重現）
# Listing 視窗：點到函式名 → 按 L
# Decompiler 視窗：右鍵函式名 → Rename Function
# 變數重命名：右鍵區域變數 → Rename Variable
```

### 變數重命名

反編譯器自動生成的變數名是 `local_20`、`param_1`、`uVar3`。重命名規則和函式一樣——先猜，加 `?`，驗證後確認。

特別重要的是**參數命名**：

```c
// Before
undefined8 FUN_00401380(long param_1, int param_2, undefined8 param_3)

// After 你命名
undefined8 decrypt_rc4(long key_buf, int key_len, undefined8 ciphertext_ptr)
```

命名之後，所有呼叫這個函式的地方，反編譯器輸出都會顯示命名後的參數，而不是 `param_1`。

### 型別與 Struct 定義

這是 Ch 9 的核心主題，這裡只強調外化的角度。

當你從記憶體佈局推斷出一個 struct，直接在 Ghidra 的 Data Type Manager 裡定義它：

```c
// 你在反編譯輸出裡看到類似這樣的存取模式：
*(long *)(param_1 + 0x10)    // 你猜這是某個指標欄位
*(int *)(param_1 + 0x18)     // 你猜這是 length
*(int *)(param_1 + 0x1c)     // 你猜這是 flags

// 在 Ghidra Data Type Manager 定義 struct：
struct PacketHeader {
    char magic[8];       // offset 0x00
    char *data_ptr;      // offset 0x08
    long payload_ptr;    // offset 0x10
    int length;          // offset 0x18
    int flags;           // offset 0x1c
};
```

套用 struct 之後，反編譯輸出從一堆數字偏移變成欄位名稱存取。這是可讀性的躍升。

### Ghidra 的 Comment 系統

Ghidra 提供四種 comment：

| Comment 類型 | 位置 | 用途 |
|---|---|---|
| EOL Comment | 行尾 | 快速備注，猜測 |
| Pre Comment | 指令之前 | 標記「這裡開始某個邏輯區塊」 |
| Post Comment | 指令之後 | 標記副作用 |
| Plate Comment | 函式開頭前的大塊 | 函式說明：輸入/輸出/注意事項 |

實務上最常用的是 EOL 和 Plate。Plate Comment 是你的函式文件，標準格式：

```
; ====================================
; Function: decrypt_rc4
; Args:
;   param_1 (RDI) = key buffer ptr
;   param_2 (ESI) = key length
;   param_3 (RDX) = ciphertext ptr (also output)
; Returns: 0 on success, -1 on error
; Notes:
;   key is NOT null-terminated, use key_len
;   decrypts in-place
;   CONFIRMED: matches output at 0x402c10
; ====================================
```

---

## 外部筆記：逆向 Journal

工具內的外化有一個根本限制：Ghidra project 不是筆記工具。你無法在裡面寫「目前的假設是 X，如果假設錯了要去看 Y」、「待辦：還有 3 個 switch case 沒分析」、「這個函式的行為和 OpenSSL EVP_EncryptInit 幾乎一樣，可能是移植的」。

這類**跨越函式邊界的推理、假設、待辦事項**，需要一份獨立的逆向 journal。

### 逆向 Journal 模板

以下是一份可以直接使用的模板：

```markdown
# 逆向 Journal：<target_binary>

**分析日期**：YYYY-MM-DD  
**Binary**：`target.elf`（SHA256: `xxxxxxxx`）  
**目標**：找出加密協定實作 / 理解持久化機制 / ...

---

## 當前假設

| ID | 假設 | 依據 | 信心 | 狀態 |
|----|------|------|------|------|
| H1 | 0x401380 是 RC4 初始化 | 看到 256 bytes KSA loop | 中 | 待驗 |
| H2 | 0x402a10 從網路讀 C2 指令 | recv() → parse 路徑 | 高 | 確認 |
| H3 | struct offset 0x18 = length | 搭配 memcpy 第三參數 | 低 | 需更多 xref |

---

## 已還原的資料結構

### PacketHeader（offset 0x00 of param_1 in 0x401a20）

```c
struct PacketHeader {
    uint32_t magic;       // 0x00: 0xDEADBEEF
    uint32_t version;     // 0x04
    uint64_t payload_len; // 0x08
    uint8_t  cmd;         // 0x10
    uint8_t  flags;       // 0x11
    uint16_t _pad;        // 0x12
    // total: 0x14 bytes
};
```

---

## 關鍵地址索引

| 地址 | 命名 | 備注 |
|------|------|------|
| 0x401380 | `init_rc4_key` | KSA，256 bytes |
| 0x401430 | `rc4_encrypt` | 呼叫 init_rc4_key 之後 |
| 0x402a10 | `recv_and_dispatch` | main loop 的分派函式 |
| 0x40310c | `?persistence_check` | 存取 /etc/crontab，用途待確認 |

---

## Call Graph 摘要（文字版）

```
main()
  └── recv_and_dispatch()
        ├── parse_header()       → H2 確認
        ├── init_rc4_key()       → H1 待驗
        │     └── rc4_encrypt()
        └── ?persistence_check() → 還沒看
```

---

## 待辦

- [ ] 驗證 H1：用 frida hook 0x401380，觀察 256-byte buffer 是否符合 KSA 輸出
- [ ] 分析 `?persistence_check`（0x40310c）的 syscall 序列
- [ ] 搞清楚 struct offset 0x18 的真正型別（目前看到兩種用法矛盾）
- [ ] 比對 0x401430 和 OpenSSL rc4.c 的組語差異

---

## 廢棄的假設（記錄失敗）

| ID | 假設 | 為什麼錯 | 日期 |
|----|------|----------|------|
| H0 | 0x401380 是 AES KeyExpansion | KSA loop 只有 256 iterations，不是 10/12/14 | 2026-08-01 |
```

journal 不需要完美格式，重點是**寫下來**。假設、地址、已知的 struct、待辦——這四個維度基本夠用。

---

## 為什麼「邊逆邊命名」是複利

你可能以為先把整個 binary 瀏覽一遍、再集中命名比較有效率。逆下去你會發現：這是錯的。

原因在於 Ghidra 的 xref 系統是雙向的。你命名 `init_rc4_key` 之後：

1. 所有 call `init_rc4_key` 的地方，Ghidra Decompiler 立刻顯示 `init_rc4_key(param_1, 256)`，不再是 `FUN_00401380(local_18, 0x100)`。
2. 你在看其他函式的 decompiler 輸出時，看到 `init_rc4_key` 就知道這裡在初始化 RC4，不需要跳回去確認。
3. 從 `init_rc4_key` 的 xref 列表，你立刻看到有幾個地方會初始化加密，這讓你決定下一步要看哪個 call site。

命名的複利效應：**每命名一個函式，後續分析這個函式的所有 caller 和 callee 的成本都降低**。

相比之下，不命名的代價是線性的：每次看到 `FUN_00401380`，你都要重新回想它是什麼。

---

## 一套逆向外化 SOP

以下是進入新的逆向任務時，具體的外化步驟：

**第一步：建立 journal 檔**

在開始動 Ghidra 之前，先建一個 `target_reversing.md`，填上 binary 名稱、SHA256、分析目標。這個動作讓你強迫自己先想清楚「我要找什麼」。

**第二步：strings + 函式列表掃描（10 分鐘）**

```bash
# 真跑：快速建立初始地址索引
objdump -d target.elf | grep -E "^[0-9a-f]+ <" | head -50
strings target.elf | grep -E "(http|key|encrypt|cmd|config)" | head -30
```

把你看到的可疑字串和函式數量記進 journal 的「關鍵地址索引」，哪怕只是「有 200 個函式，strings 裡看到 RC4/AES 字樣」。

**第三步：從 main 或入口點開始，立刻命名**

不要「等到搞清楚再命名」。看到一個函式做某件事，哪怕只是「好像在讀 config」，就命名成 `maybe_read_config`。

**第四步：每次發現假設，立刻寫進 journal**

不要靠記憶。假設寫進 journal 的 H 表，信心欄填低/中/高，狀態欄填「待驗」。

**第五步：每分析完一個「邏輯單元」（函式群），更新 Call Graph 摘要**

不需要畫圖，文字縮排版的 call graph 已經夠用。

**第六步：假設被推翻時，移到「廢棄的假設」區**

這是關鍵的一步，很多人省略，結果同樣的錯誤假設在一週後又冒出來。記錄「為什麼錯」比記錄「它是對的」更有學習價值。

---

## 對比與取捨

| 做法 | 優點 | 缺點 | 適用場景 |
|------|------|------|----------|
| 只靠 Ghidra 內建 label/comment | 不需要切換工具 | 跨函式推理、待辦難記 | 小型 binary、單次分析 |
| 外部 markdown journal | 假設追蹤、多日分析 | 需要同步到 Ghidra | 長期逆向、團隊協作 |
| 先瀏覽全部再命名 | 有整體感再決定命名 | 重複閱讀浪費時間 | 幾乎沒有適用場景 |
| 邊逆邊命名（推薦） | xref 複利效應 | 早期命名可能要改 | 所有場景 |
| 只用腦記 | 零成本 | 會話斷線即失憶 | 不推薦，除非 < 5 分鐘的任務 |

---

## 踩雷集錦

**踩雷一：命名太確定，後來發現錯**

你看到一個函式做 XOR 操作，立刻命名 `decrypt_xor`。逆到一半發現它其實是 `encrypt_xor`——加解密共用同一個函式是常見設計。結果你改名改到一半，xref 裡有些地方的語意就不對了。

對策：初期用 `xor_cipher` 這種中性命名，或明確加前綴 `maybe_`，等確認加解密用途分開後再區分。

**踩雷二：把假設當事實放進工具內**

你把 `param_1` 命名成 `user_password_ptr`，因為你「很確定」。兩天後，你在另一條路徑看到同一個函式被呼叫，`param_1` 傳的是 API key，不是密碼。你現在需要在腦子裡把所有看過 `user_password_ptr` 的地方重新詮釋。

對策：工具內的命名反映「目前最好的假設」，工具外的 journal 記錄「信心程度和依據」。這樣你才知道哪些命名是確定的、哪些是待驗的。

**踩雷三：不記廢棄假設**

分析第一天，你假設 `FUN_00401a10` 是網路接收函式。第三天你發現它其實是磁碟讀取，重新命名。第七天，你又對同一個函式產生「這是不是網路接收？」的疑問——因為你忘了三天前已經否定過這個假設了。

如果有記廢棄假設，第七天你翻一下 journal，看到「H0 被否定：因為 syscall 序列是 open/read 不是 recv」，這個問題五秒解決。沒記，你要重新逆一遍。

**踩雷四：call graph 只在腦子裡**

你花一小時搞清楚了主要的呼叫鏈：`main → dispatcher → handler_A → decrypt → validate`。沒有寫下來。隔天繼續，你在 `handler_B` 裡看到 `decrypt` 的呼叫，但你忘了 `handler_B` 在 call graph 裡的位置，需要重新從 `main` 往下追。

對策：call graph 文字摘要五分鐘可以寫，省掉的重複工作遠超過這五分鐘。

---

## 進階：再往深一層

### 從 journal 到逆向報告

如果你的逆向是為了交付報告（漏洞研究、惡意程式分析），journal 本身就是報告的草稿。「假設表」變成「發現摘要」，「廢棄假設」變成「排除路徑說明」，「關鍵地址索引」變成「IoC 列表」。

邊逆邊寫 journal 等於邊逆邊寫報告。

### 團隊逆向的 journal 管理

多人同時逆向同一個 binary 時，journal 要放在共享的地方（git repo、協作文件）。每個人負責不同的函式群，但假設表和廢棄假設表要共用——避免兩個人各自在腦子裡否定了同一個假設，卻沒有同步。

### Ghidra Script 自動匯出命名

Ghidra 允許你用 Python script 把所有你設定過的 label 和 comment 匯出成 JSON 或 CSV，方便版本控制和跨 project 匯入。這比手動截圖存好一百倍。

```python
# 概念示意，讀者自行根據 Ghidra API 實作
# from ghidra.program.model.symbol import SymbolType
# for sym in currentProgram.getSymbolTable().getAllSymbols(True):
#     if sym.getSymbolType() == SymbolType.FUNCTION:
#         print(sym.getAddress(), sym.getName())
```

### 與 reading_code 的對比

soft_skills/reading_code Ch 35 講的外化（心智圖、call graph、術語表）和本章的技巧在結構上完全相同，但有一個根本差異：

讀 source code 時，你的外化是在「已有的符號體系上加一層你的理解」。逆向時，你的外化是**從零建立整個符號體系**。這讓逆向的命名工作比讀碼的外化工作重得多，也讓「邊做邊命名」而不是「最後再整理」的原則更加重要。

---

## 本章重點整理

- Binary 沒有名字，你的所有外化工作就是在填補這個缺失。
- 工具內外化的核心：函式重命名、變數重命名、struct 定義、Plate Comment——這些讓 Ghidra 的 xref 系統替你工作。
- 複利法則：命名一個函式，它的所有 caller 的可讀性同時提升。
- 工具外 journal 記四件事：假設（帶信心）、地址索引、已知 struct、待辦。
- 廢棄的假設要記錄，理由和命名一樣重要。
- SOP：開 journal → 掃描建立初始索引 → 邊逆邊命名 → 邊逆邊記假設 → 假設被推翻就移到廢棄區。

---

## 自我檢核

1. 你對 Ghidra 的 Plate Comment 和 EOL Comment 的使用場景能說出差異嗎？
2. 為什麼「邊逆邊命名」比「瀏覽完再集中命名」更有效率？試用 xref 的角度解釋。
3. 逆向 journal 的假設表裡，「信心欄」和「狀態欄」各記錄什麼？兩者有何差別？
4. 你分析到一半，發現三天前設的 `decrypt_aes` 其實是 `encrypt_aes`，你應該做哪些事？（工具內 + journal 都要說）
5. 廢棄假設表的用途是什麼？不記廢棄假設會造成什麼具體問題？

---

## 延伸閱讀

1. **Ghidra Book（NSA Research Directorate 官方書）**——Ch 9 和 Ch 10 完整說明 Ghidra 的 Data Type Manager 和 Symbol 系統，是工具內外化的官方參考。
2. **"The Practice of Network Security Monitoring"（Richard Bejtlich）**——雖然是藍隊書，但第一部分關於「分析師如何記錄假設和推論鏈」的論述，直接適用於逆向分析的 journal 方法論。
3. **"Reverse Engineering for Beginners"（Dennis Yurichev，又名 RE4B）**——書中大量範例都有完整的「我看到什麼 → 我推斷什麼 → 我驗證什麼」的推理過程，是外化推理風格的示範。

---

本章建立了逆向外化的基礎設施——工具內的命名體系和工具外的 journal。你現在有 SOP，有模板，有命名策略。下一章進一步把這套工作流自動化：用腳本批量分析、自動產生 xref 報告、讓重複性的逆向任務交給程式做。

→ [Ch 26 腳本化逆向](./26-scripting-reversing.md)
