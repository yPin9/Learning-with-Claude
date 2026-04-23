# Ch 14 — 把 script 包成一鍵觸發（action + hotkey）

> 目標：用 `action_handler_t` + `register_action` 把你寫的 script 掛到自訂 hotkey 和右鍵選單，不再每次 File → Script File。

## 為什麼重要

Ch 9–13 的 script 每個都要 `Alt+F7` 挑檔，煩。掛到 hotkey 後：

- 游標在可疑 function 上，按 `Ctrl-Shift-R` → 立刻跑「batch rename + report」。
- 在 pseudocode 的變數上按 `Ctrl-Shift-S` → 立刻跑「struct recovery for this LVAR」。

IDA 的 action API 讓你可以這樣做，並且不需寫 plugin_t class（Ch 3 問你要 (a) 就是這個界線）。

## 最小範例

```python
import ida_kernwin

ACTION_NAME = "mytools:hello"

class HelloHandler(ida_kernwin.action_handler_t):
    def __init__(self):
        ida_kernwin.action_handler_t.__init__(self)

    def activate(self, ctx):
        ida_kernwin.msg("hello from my action\n")
        return 1                        # non-zero = executed OK

    def update(self, ctx):
        return ida_kernwin.AST_ENABLE_ALWAYS

desc = ida_kernwin.action_desc_t(
    ACTION_NAME,                        # unique id
    "My: Hello",                        # 顯示名
    HelloHandler(),                     # handler
    "Ctrl-Shift-H",                     # hotkey
    "Say hello",                        # tooltip
    199                                 # icon id (任選，-1 = none)
)

ida_kernwin.register_action(desc)
```

跑一次這個 script 後：

- `Ctrl-Shift-H` 在任何地方會印 `hello from my action`。
- `Edit → Plugins` 或命令列面板都找得到 `My: Hello`。

## 加到右鍵 popup

只註冊 action 只能用 hotkey 和 command palette。想在右鍵選單出現：

```python
class AttachHook(ida_kernwin.UI_Hooks):
    def finish_populating_widget_popup(self, widget, popup):
        # 限制在特定 widget（例如 pseudocode / disasm）
        if ida_kernwin.get_widget_type(widget) in (
            ida_kernwin.BWN_PSEUDOCODE,
            ida_kernwin.BWN_DISASM,
        ):
            ida_kernwin.attach_action_to_popup(widget, popup, ACTION_NAME, "My tools/")

hooks = AttachHook()
hooks.hook()
```

`finish_populating_widget_popup` 在每次你右鍵時觸發，我們判斷當前 widget 是 pseudocode 或 disasm，才掛 action。`"My tools/"` 是 submenu 路徑。

右鍵現在會看到：

```
── My tools
   └── My: Hello
```

## 實戰：把 Ch 11 的 struct 推斷掛上 hotkey

場景：游標在 pseudocode 裡某個 `v3`，按 `Ctrl-Shift-S` 自動從 `v3` 的存取 pattern 推 struct 並套上。

```python
import ida_kernwin, ida_hexrays, ida_funcs
import ida_typeinf

ACTION_NAME = "mytools:auto_struct"

def get_current_lvar():
    """拿到當前 pseudocode 游標下的 lvar_t，沒有就 None"""
    vu = ida_hexrays.get_widget_vdui(ida_kernwin.get_current_widget())
    if not vu:
        return None, None
    vu.get_current_item(ida_hexrays.USE_KEYBOARD)
    item = vu.item
    if item.citype == ida_hexrays.VDI_LVAR:
        return vu.cfunc, item.get_lvar()
    if item.citype == ida_hexrays.VDI_EXPR and item.e.op == ida_hexrays.cot_var:
        return vu.cfunc, vu.cfunc.get_lvars()[item.e.v.idx]
    return None, None

def auto_struct_for_current_lvar():
    cfunc, lv = get_current_lvar()
    if not lv:
        ida_kernwin.warning("cursor is not on an LVAR")
        return

    func_ea = cfunc.entry_ea
    reg_hint = lv.location.is_reg() and lv.location.reg1() or None

    # 實際推斷用 Ch 11 的 collect_accesses / fields_from_accesses / render_struct
    # 並用 Ch 13 的 apply_struct_to_lvar 套到 LVAR
    # （此處省略內部邏輯，參考 Ch 11 / Ch 13）
    struct_name = f"Auto_{func_ea:X}_{lv.name}"
    ida_kernwin.msg(f"inferring struct for {lv.name} in {func_ea:#x}\n")
    # ... 跑推斷 + apply ...
    ida_kernwin.msg(f"applied {struct_name}\n")

class AutoStructHandler(ida_kernwin.action_handler_t):
    def activate(self, ctx):
        auto_struct_for_current_lvar()
        return 1

    def update(self, ctx):
        if ctx.widget_type == ida_kernwin.BWN_PSEUDOCODE:
            return ida_kernwin.AST_ENABLE_FOR_WIDGET
        return ida_kernwin.AST_DISABLE_FOR_WIDGET

desc = ida_kernwin.action_desc_t(
    ACTION_NAME,
    "My: Auto-struct for this LVAR",
    AutoStructHandler(),
    "Ctrl-Shift-S",
    "Infer struct from access pattern of cursor LVAR",
    -1
)
ida_kernwin.register_action(desc)
```

`update()` 回 `AST_ENABLE_FOR_WIDGET` 只有在 pseudocode window 才啟用這個 action，其他 widget hotkey 不會觸發。

## Action 的 lifecycle

- **register_action(desc)**：安裝。回傳 bool。
- **unregister_action(name)**：拔除。寫 pluginify 的 script 時在 `term()` 記得 call。
- **update_action_label(name, label)** / **update_action_shortcut(name, shortcut)**：動態改。

### 重複註冊

如果你重新跑同一個 script：

```python
ida_kernwin.unregister_action(ACTION_NAME)      # 先拔舊的
ida_kernwin.register_action(desc)               # 再裝新的
```

不然第二次 register 會失敗（名字已存在）。

## 做成正式 plugin 檔案

把 script 存成 `.py` 丟進 **使用者資料目錄的 `plugins/`**：

```python
# my_tools_plugin.py
import ida_idaapi

class MyToolsPlugin(ida_idaapi.plugin_t):
    flags = ida_idaapi.PLUGIN_FIX               # 常駐不卸
    comment = "My personal tools"
    help = "No help"
    wanted_name = "MyTools"
    wanted_hotkey = ""                          # plugin 本體 hotkey（此處用 action 掛）

    def init(self):
        register_actions()
        return ida_idaapi.PLUGIN_KEEP

    def run(self, arg):
        pass

    def term(self):
        unregister_actions()

def PLUGIN_ENTRY():
    return MyToolsPlugin()
```

IDA 啟動時會掃 plugins 目錄，每個 `.py` 呼叫 `PLUGIN_ENTRY()` 拿 plugin 實例、跑 `init()`。你的 action 一 load IDA 就註冊好，不用每次 `Alt+F7`。

**這跨越了 (a)/(b) 邊界**：已經是 plugin 形式（有 `plugin_t` class）。但你只用了最基本的骨架，沒做 processor hook 或 loader — 所以還算 (a) 的自然延伸。

## Hotkey 格式規則

`register_action` 的 shortcut 字串：

```
"Ctrl-Shift-S"           # OK
"Ctrl-Alt-U"             # OK
"Shift-F9"               # OK
"Alt-."                  # OK
"X"                      # 只按 X，會和 xref 衝突 — 別這樣
```

**避開 IDA 已用的組合**：`F2–F9`、`N`、`Y`、`X`、`G`、`Ctrl-S`、`Ctrl-Z` 等。`Ctrl-Shift-` 開頭基本安全。

## 常見踩雷

- **action 按了沒反應**：`update()` 回錯值讓 action 被 disable。在 `update` 裡打 log 確認被 call 到且回對值。
- **右鍵選單沒看到**：`UI_Hooks` 的 `hooks.hook()` 忘了叫，或 `get_widget_type` 判錯。
- **Hotkey 衝突**：兩個 action 綁同一個 hotkey — 後註冊的贏或兩個都失靈，依版本行為。
- **重新跑 script 爆錯 `already exists`**：忘了 unregister。固定做成：

```python
try:
    ida_kernwin.unregister_action(ACTION_NAME)
except:
    pass
ida_kernwin.register_action(desc)
```

## 動手練習

1. 把 Ch 9 的 `rename_by_strings()` 掛成 `Ctrl-Shift-R`，按下去 IDB 做一次 rename pass。
2. 做一個 `Ctrl-Shift-X`：在 pseudocode 游標所在的 call expression 上按，把 callee function 取名為 `fn_<當前 file line context>`。
3. 加一個 UI hook：只有在 function window 右鍵才出現「Batch rename」選項。
4. 把所有 action 整理成一個 `my_tools_plugin.py`，丟進 plugins 目錄，確認 IDA 啟動後 hotkey 立刻可用。

## 自我檢核

- [ ] 能 register_action + 綁 hotkey
- [ ] 能用 UI_Hooks 掛到右鍵選單
- [ ] 知道 `update()` 控制 action 啟用條件
- [ ] 能用 `get_widget_vdui` + `vu.item` 拿游標當下 context
- [ ] 知道 plugin 檔案放哪、`PLUGIN_ENTRY` 要寫什麼

Part 3 結束。你已經會寫分析 script、操控 pseudocode、把 script 包成 plugin-lite。下一步是把前面各章技巧整合到一個實戰 script — **Final Project**。

→ [Final Project：Malware Unpacker Helper](./final-project-unpacker-helper.md)
