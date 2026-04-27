# Ch 7 — IDAPython 入門

> 目標：搞懂 `idc` / `idaapi` / `ida_*` 三套 API 的關係、console 與 script 怎麼跑、9.x 該用哪套（劇透：模組化 `ida_*` 為主）。

## IDAPython 的三層 API（歷史包袱介紹）

翻 GitHub 找 IDA script 時，你會看到三種寫法混用：

```python
# 寫法 A — idc 風格（最舊）
import idc
name = idc.get_name(0x401000, idc.GN_VISIBLE)
idc.set_name(0x401000, "my_func", idc.SN_NOWARN)

# 寫法 B — idaapi 單一大模組
import idaapi
name = idaapi.get_name(0x401000)

# 寫法 C — ida_* 模組化（新）
import ida_name
name = ida_name.get_name(0x401000)
ida_name.set_name(0x401000, "my_func", ida_name.SN_NOWARN)
```

它們幾乎做一樣的事。為什麼有三套？

| API | 時期 | 特色 | 9.x 該用嗎 |
|---|---|---|---|
| `idc` | 史前 | 包裝 IDA 自己的 IDC scripting 語言，函式命名像 C | 只在 legacy script 用 |
| `idaapi` | 早期 Python bindings | 把所有東西塞進單一 module | 不建議新 code 用 |
| `ida_*` | 現在 (6.95+) | 拆成 `ida_name`、`ida_bytes` 等模組，對應 SDK 結構 | **首選** |

**我的建議**：新寫 script 全 `ida_*`，`idc` / `idaapi` 只在接別人 code 時容忍。9.x 官方文件也是以 `ida_*` 為主。

## `idautils` 是什麼

除了上面三個，還有一個：

```python
import idautils
for ea in idautils.Functions():
    ...
```

`idautils` 是純 Python 的 **高階 helper 集合**，給你迭代器、方便的包裝。它底下也是呼叫 `ida_*`，但介面好用很多。

記法：
- **要遍歷某個集合**（functions、xrefs、heads、segments）→ `idautils`
- **要對某個位址做 CRUD** → `ida_*`

這個分工之後你會常用。

## 怎麼跑 IDAPython

### 方式 1：Python console（即時）

`View → Other windows → Python`。適合：
- 互動探索 API
- 測試一兩行
- 查某個位址的現狀

### 方式 2：Script command（單段 snippet）

`File → Script Command`，或快捷鍵 `Shift+F2`。彈出輸入框，貼一段 code，按 Run 執行。

**對比 console 的差別**：Script Command 是一次跑完，不保留互動 state；console 是 REPL，變數會留著。

### 方式 3：Script file（完整檔案）

`File → Script File...`，或 `Alt+F7`。選 `.py` 檔執行。

- 改檔案後 **不能熱重載**（Python import cache），要用 `importlib.reload()` 或重開 IDA。
- 適合：已經寫好的工具 script。

### 方式 4：放 plugins 目錄（自動載入）

把 `.py` 檔丟進 **使用者資料目錄的 `plugins/`**（Windows: `%APPDATA%\Hex-Rays\IDA Pro\plugins\`），IDA 啟動時會自動讀。

但這要符合 **plugin_t 介面** — 一個有 `init()`、`run()`、`term()` 的 class。普通腳本這樣丟進去不會跑。Ch 14 會寫。

## 第一個實用 script

用 `ida_*` 寫一個列出所有 function 和它引用字串的小工具：

```python
import idautils
import ida_funcs, ida_name, ida_bytes, ida_xref

def strings_used_by(func_ea):
    """收集這個 function 引用到的所有字串"""
    strings = []
    func = ida_funcs.get_func(func_ea)
    if not func:
        return strings

    for ea in idautils.FuncItems(func_ea):
        # 看這條指令的所有 data xref
        for xref in idautils.DataRefsFrom(ea):
            s = ida_bytes.get_strlit_contents(xref, -1, 0)
            if s:
                strings.append(s.decode("utf-8", errors="replace"))
    return strings

for f_ea in idautils.Functions():
    fname = ida_name.get_name(f_ea)
    ss = strings_used_by(f_ea)
    if ss:
        print(f"{fname}:")
        for s in ss:
            print(f"  {s!r}")
```

跑起來會看到每個 function 引用的字串。這種 output 對 stripped binary 極實用 — `sub_401200` 引用 `"license check"`，你大概知道這是 check 函式。

### 這段示範了什麼

| API | 用途 |
|---|---|
| `idautils.Functions()` | 遍歷所有 function |
| `idautils.FuncItems(ea)` | 遍歷這個 function 裡的每一條指令位址 |
| `idautils.DataRefsFrom(ea)` | 這條指令 refer 到哪些 data 位址 |
| `ida_name.get_name(ea)` | 查位址的 symbol 名 |
| `ida_bytes.get_strlit_contents(ea, len, strtype)` | 讀字串，`len=-1` 表示自動 |

**命名規律**：`idautils` 給 iterator；`ida_<area>` 給 CRUD。

## 常用入口 cheatsheet

```python
import idaapi             # 少量工具 (msg, jumpto)，不建議新 code 大量用
import idautils           # 高階 iterator
import idc                # legacy，不建議
import ida_kernwin        # UI（popup、確認框、hotkey）
import ida_bytes          # 讀寫 byte / word / dword / qword / str
import ida_name           # symbol 名字的讀寫
import ida_funcs          # function 邊界、flags
import ida_xref           # xref CRUD
import ida_ua             # decode 指令
import ida_segment        # segment info
import ida_hexrays        # Hex-Rays API (Ch 13)
import ida_struct         # struct 操作（9.x 部分 API 整併到 ida_typeinf）
import ida_typeinf        # 9.x 的 type system 主力 — struct/enum/typedef 都在這
import ida_nalt           # name alternative，像 comment 之類附註
```

## 查文件的三個來源

遇到「我忘了這 API 叫什麼」的時候：

1. **官方 API docs**：<https://hex-rays.com/products/ida/support/idapython_docs/>
2. **安裝目錄的 `python/3/`**：pyi stub 檔，有 type hint，IDE 最適合翻這
3. **SDK 的 C header**：`<ida-sdk>/include/`，`.hpp` 檔的 function signature 幾乎對應 Python 版（去掉 `hexapi_`、`idaapi` namespace）

我最常用的是 (2) — 在 VS Code 打開 stub 檔案，fuzzy search function 名字，比翻 HTML 快。

## 常見踩雷

- **在 console 寫了東西忘了 import**：IDA 的 console 不會像 Python REPL 繼承已載入 module，除非你手動 import。寫 `ida_funcs.get_func(ea)` 前要先 `import ida_funcs`。
- **script file 改了沒效果**：Python import cache。要重跑 script file 可以直接 `Alt+F7` 重跑整個檔；如果你用 `from mymod import foo`，改完要 `importlib.reload(mymod)`。
- **function 參數順序錯**：`set_name(ea, name, flags)` 和 `set_name(name, ea, flags)` — 不對。翻 stub 確認順序。
- **大 IDB 跑 script 當機**：`ida_kernwin.show_wait_box(...)` 讓 UI 不凍，但長跑最好拆成 chunk，中間 `ida_kernwin.user_cancelled()` 檢查使用者按 Cancel。

## 動手練習

1. Python console 印出當前 IDB 的 binary 路徑：
   ```python
   import ida_nalt
   print(ida_nalt.get_input_file_path())
   ```
2. 改上面的 `strings_used_by` 版本：只收集長度 `> 8` 的字串。
3. 寫一個小 snippet：找所有 `call sub_XXX` 被呼叫超過 10 次的 function（hot functions）。
4. 把這個 snippet 存成 `.py` 檔，用 `Alt+F7` load 跑一次。
5. `Shift+F2` 再貼一次一行版本跑一次。體會三種執行方式的差異。

## 自我檢核

- [ ] 知道 `idc` / `idaapi` / `ida_*` 的關係與建議選擇
- [ ] 知道 `idautils` 是給 iterator 用
- [ ] 能用 Python console / Script Command / Script File 三種方式跑 code
- [ ] 知道 user plugins 目錄在哪
- [ ] 能跑出第一個「list function 與 string refs」的 script

下一章把常用 API 集中攤開 — 之後寫任何 script 知道去哪個模組找函式。

→ [Ch 8 核心 API 地圖](./08-idapython-api-map.md)
