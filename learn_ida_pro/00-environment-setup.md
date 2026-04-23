# Ch 0 — 環境搭建

> 目標：把 IDA Pro 9.x 裝好、確認 IDAPython 能跑、打開第一個 IDB，為後面 14 章打底。

## 為什麼是 IDA 9.x

IDA 9.0 於 2024 年底推出，相對 7.x / 8.x 有幾個對我們影響最大的改動：

- **UI 統一成 Qt 6**：不再有舊版 widget bug，多螢幕、HiDPI 正常。
- **Structures / Enums 兩個 subview 被廢掉**：全部整併進 **Local Types**（`Shift+F1`）。舊教材講的「按 `Shift+F9` 進 Structures window」在 9.x 已經不適用，這點很容易踩雷。
- **Python 3 為唯一選項**：Python 2 支援徹底移除。內建 `idapyswitch` 工具讓你切換不同 Python 版本。
- **Teams / Cloud 相關功能進一步整合**：個人學習用不到，略過。

後面所有章節都以 9.x 為準，8.x 差異會在踩雷段點出。

## 安裝目錄長什麼樣

裝完以後認識一下目錄，後面我們會常進去翻：

```
<IDA 安裝目錄>/
├── ida.exe / ida64.exe     # 32/64-bit 啟動器（9.x 合併為單一 ida 執行檔，但殼還在）
├── idapyswitch.exe         # 切換 IDAPython 要綁的 Python runtime
├── python/                 # IDAPython 模組（ida_bytes.pyi 等 stub 在這裡）
├── plugins/                # 所有 plugin 的家（.dll/.so/.py）
├── loaders/                # 載入各種 binary 格式的 loader（PE、ELF、Mach-O、...）
├── procs/                  # processor module（x86、ARM、MIPS、...）
├── til/                    # Type Information Libraries（標準 C、Win32、POSIX 等原型）
├── sig/                    # FLIRT 簽名檔（幫你自動認 libc / MSVCRT function）
└── cfg/                    # 各種設定檔（idagui.cfg、hexrays.cfg 等）
```

**使用者資料目錄** 另外放在 per-user 位置（plugin、設定、hotkey 客製都在這裡）：

| OS | 路徑 |
|---|---|
| Windows | `%APPDATA%\Hex-Rays\IDA Pro\` |
| macOS   | `~/Library/Application Support/IDA Pro/` |
| Linux   | `~/.idapro/` |

之後寫自己的 plugin，檔案要丟到 **使用者資料目錄的 `plugins/`**，不要丟安裝目錄的 `plugins/` — 升級 IDA 時會被覆蓋。

## 確認 IDAPython 能跑

開 IDA，隨便拖一個 binary 進去，按 **OK** 接受預設分析。等畫面下方 Output window 的「The initial autoanalysis has been finished」訊息跳出來（這代表首輪分析結束）。

接著 `View → Other windows → Python`（或直接按 `Alt+F7` 開 Script 檔執行 window — 但這裡我們要的是 console）。在 console 裡輸入：

```python
import sys
print(sys.version)
print(idaapi.get_kernel_version())
```

應該看到類似：

```
3.12.4 ...
9.0
```

如果 Python 版本不對、或根本沒出現 Python console，跳出 IDA，到安裝目錄執行：

```
./idapyswitch          # Linux/macOS
idapyswitch.exe        # Windows
```

它會列出系統上可用的 Python 版本，讓你選一個綁定。**選和你平常開發用的同一個 Python**，後面安裝第三方套件（capstone、unicorn）時會少很多麻煩。

## 跑第一個 script

在 Python console 敲：

```python
import idautils, ida_funcs, ida_name

for ea in idautils.Functions():
    name = ida_name.get_name(ea)
    f = ida_funcs.get_func(ea)
    print(f"{ea:#x}  {name}  size={f.size()}")
```

如果看到一串 function 位址、名稱、大小印出來 — 環境 OK。

這段 code 示範了後面會反覆用到的三件事：

1. **`idautils.Functions()`** 迭代所有 function 的起始位址。
2. **`ida_name.get_name(ea)`** 查某個位址的名字。
3. **`ida_funcs.get_func(ea)`** 拿回 `func_t` 結構，含大小、flags 等。

## 第一個 IDB：認識 .i64 檔

打開一個 binary 時，IDA 會在同目錄生成 `xxx.i64`（32-bit 的話是 `.idb`，9.x 新建通常是 `.i64`）。這個檔就是 **IDA Database**，之後所有你加的註解、改的名字、畫的 struct 都存在這裡。

幾個常踩的雷：

- **IDB 開啟期間是鎖定的**：同一個 IDB 不能兩個 IDA 同時開。關閉 IDA 會自動解鎖，**但如果 IDA 當機**，你會留下 `xxx.id0.lock` 之類的鎖檔，要手動刪掉。
- **`.i64` 不是 binary 本身**：給別人分析結果時要一起附原 binary，否則很多時候會無法重做 debugger 等需要原檔的操作。
- **autoanalysis 沒跑完就亂動**：下方狀態列左下會顯示 `AU: idle` 才算跑完。沒跑完就改名、改型別，很容易被後面的分析覆蓋。

## 踩雷清單

| 症狀 | 原因 | 解 |
|---|---|---|
| Python console 找不到 | 沒裝 IDAPython 或 Python runtime 綁錯 | 跑 `idapyswitch` |
| `ImportError: ida_hexrays` | Hex-Rays decompiler 授權沒包 | 這種授權下沒 F5 能用，Ch 3/13 會跳過 |
| 9.x 找不到 Structures 視窗 | 9.x 已廢掉，用 Local Types (`Shift+F1`) | Ch 5 會深入 |
| IDB 無法開啟 `...is being used by another process` | 鎖檔未清 | 找同目錄 `.lock` 檔手動刪 |
| IDAPython script 輸出沒看到 | Output window 沒開 | `View → Other windows → Output` |

## 動手練習

1. 打開一個你手邊任何的 ELF / PE（`/bin/ls`、`notepad.exe` 都行），等 autoanalysis 跑完。
2. 在 Python console 跑上面那段 `for ea in idautils.Functions()`，把輸出貼進筆記。
3. 隨便挑一個 `sub_XXXXXX` 的 function，把它改名為 `my_first_rename`（按 `N`），存檔（`Ctrl+S`），關閉 IDA，重開 IDB — 確認改名還在。這驗證了 IDB 的 persistent 機制。

## 自我檢核

- [ ] 知道 IDA 安裝目錄與使用者資料目錄分別放什麼
- [ ] Python console 可以跑 `idaapi.get_kernel_version()`
- [ ] 知道 `.i64` 是分析資料庫，不是 binary 本身
- [ ] 能用 `idautils.Functions()` 迭代所有 function

下一章先搞清楚 IDB 裡到底裝了什麼、主要 subview 各自扮演什麼角色，後面章節講到 view 才不會每次都要解釋。

→ [Ch 1 IDA 世界觀與資料庫](./01-ida-worldview.md)
