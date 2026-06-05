# 練習 C — 設計並實作一套檔案操作工具集

> **目標**：把 Part 3 前半（Ch 18–25）的工具設計原則，**全部**收斂到一個你親手做的真實工具集上——一套讓 agent 安全讀寫檔案的工具。完成後你會有一個能力：拿到「我想讓 agent 能做 X」這種需求，你能從 schema（Ch 18）、描述（Ch 19）、結果格式（Ch 20）、路徑安全（Ch 21）、要不要動 shell（Ch 22）、到 permission gate（Ch 25）一路把它設計對，而不是隨手包一個會把 `/etc/passwd` 讀走、把整個檔案系統暴露給模型的天真函式。

> **環境**：Python 3.11、`anthropic` SDK、`pathlib`。延續[練習 A](./practice-a-mini-agent-loop.md) 的 mini-agent loop。路徑安全行為跨平台有差異，本練習以 POSIX 語意為主，Windows 差異會在用到時標注（與 [Ch 21](./21-filesystem-tools.md) 一致）。

## 背景與動機

到目前為止，你的 mini-agent 的工具都很陽春：練習 A 那個 `read_text_file` 直接 `open(path)`，[Ch 21](./21-filesystem-tools.md) 已經點名它「危險到不能上線」。Part 3 前半把「一個好工具長什麼樣」拆成了六章：

- **Ch 18**：schema 怎麼設計——一個工具一個動作、危險度不同要拆開、`required` 要乾淨。
- **Ch 19**：description 是寫給模型看的 prompt——它決定模型會不會、何時、怎麼用這個工具。
- **Ch 20**：tool_result 的設計——`is_error`、好的錯誤訊息（what + why + how）、講脈絡、控制體積。
- **Ch 21**：檔案系統工具與**路徑安全**——把 agent 關進 workspace 牢房。
- **Ch 22**：shell 與沙箱——什麼時候該動 shell、動了之後怎麼把後果關起來。
- **Ch 25**：permission——危險操作該不該先問人，且這道閘**必須由 harness 強制**、不能讓模型自己批准自己。

這些你都「分章學過」。但真實工作裡，需求不會按章節來——它會是「我想讓 agent 幫我整理這個專案資料夾」，然後**所有這些原則要同時用上**。這個練習就是逼你把它們**織進同一套工具**：你會做出 `read_file`、`write_file`、`list_directory`、`edit_file` 四個工具，每一個都過 `safe_path`（Ch 21）、回 `ToolResult` 信封（Ch 20）、寫滿副作用警告的 description（Ch 19）、按危險度拆開（Ch 18），而且**寫類**的操作要過一道 permission gate（Ch 25）。

這套工具不是玩具——它是 Claude Code 的 `Read`/`Write`/`Edit`/`Glob` 的**精簡版同款**。做完它，你再看那些生產級工具的行為，會從「會用」變成「知道它為什麼這樣設計」。

## 任務規格

在練習 A 的 `mini_agent.py` 基礎上（沿用 `Agent`、`run_tool_uses`、工具註冊表、`stop_reason` 分流、API 錯誤處理），把那個天真的 `read_text_file` 換掉，做出一套**檔案操作工具集**：

**四個工具（Ch 18：一個動作一個工具，按危險度拆開）**
- `read_file(path, start_line=1, max_lines=500)`：讀檔，**帶行號**、支援範圍、回報總行數（Ch 21 第三節）。唯讀，低危險。
- `list_directory(path=".")`：列目錄，**過濾雜訊**（`.git`/`node_modules`/`__pycache__` 等）。唯讀，低危險。
- `write_file(path, content)`：建立或**覆寫**檔案。高危險（覆寫不可復原）。
- `edit_file(path, old_string, new_string)`：**精確字串比對**改檔；找不到或出現多次要報錯（Ch 21 第四節）。高危險。

**工具設計（Ch 18 / 19 / 20）**
- 每個工具的 `input_schema` 要乾淨：`required` 只放真正必填的、可選參數給預設值、每個參數有清楚的 `description`。
- description 要寫成「給模型的 prompt」（Ch 19）：說清楚**何時用、副作用是什麼**。`write_file` 的描述必須明說「若檔案已存在會被完全覆蓋、不可復原」。
- 統一回傳 `ToolResult`（Ch 20 的信封）：成功回內容、失敗 `is_error=True` 並給「what + why + how」的可行動錯誤訊息。

**路徑安全（Ch 21，本練習的硬底線）**
- 每個工具的第一件事都是過 `safe_path(WORKSPACE, path)`：先 `resolve()`（解開 `..`/symlink）、再檢查落在 workspace 根目錄之內。逃出去回 `is_error`，**絕不**真的去開那個檔。
- 順序不能反（先 resolve 後比較），絕對路徑要先降級成相對。

**permission gate（Ch 25）**
- 在工具**真正執行前**插一道 `check_permission` 閘：唯讀工具（read/list）放行，**寫類工具（write/edit）要先問人**（或照規則 allow/deny）。
- 這道閘必須在**程式層**（harness）強制——不是靠 system prompt 拜託模型「危險操作前先問一下」。被拒絕時回 `tool_result(is_error=True)` 讓模型知道並改道。

**禁止**
- 不准用 `os.path.join` 字串拼接 + 黑名單（`if ".." in path`）當路徑檢查——那擋不住 symlink/絕對路徑/Windows `..\\`（Ch 21 踩雷 1）。必須用 resolve-後-比較。
- 不准把四個動作塞進一個 `file_op(action=...)` 上帝工具（違反 Ch 18 原則 3，且讀寫危險度混在一起）。
- permission gate 不准只寫在 system prompt 裡靠模型自律（Ch 25 核心：必須 harness 強制）。
- `edit_file` 在 `old_string` 出現多次時，不准默默改第一個（Ch 21 踩雷 4）。

**可選加分**
- `write_file` 用**原子寫入**（暫存檔 + `fsync` + `os.replace`，Ch 21 進階）。
- `edit_file` 加 `replace_all=False` 參數（顯式全換，Ch 21 第四節）。
- permission gate 支援 **allowlist**（例如 `write_file` 在 `drafts/` 底下免問、其他要問），對齊 Ch 25 的「細到參數樣式」。
- 把這套工具包成 **MCP server**（Ch 24，`FastMCP`），讓它能被任何 MCP host 接上——體會「工具集一次寫好、到處接」的價值。

## 期望輸出範例

關鍵是看「危險操作被攔下問人、唯讀操作直接過、逃逸路徑被擋」這三條主線：

```
$ python mini_agent_c.py
mini-agent (C) 已啟動（檔案工具集 + 路徑牢房 + permission gate）
workspace = /home/agent/project

你> 看一下 src 資料夾裡有什麼
[工具] list_directory(path="src")           ← 唯讀，直接放行
agent> src/ 裡有：main.py、utils.py、config.json

你> 把 main.py 第一行的 import 改成 from app import run
[工具] read_file(path="main.py")            ← 唯讀，直接放行（模型先讀再改，對齊 read-before-edit）
[權限] edit_file 想修改 'main.py' — 允許嗎？ [y/N] y      ← 寫類操作，harness 攔下問人
[工具] edit_file(path="main.py", old_string="import app", new_string="from app import run")
agent> 已修改 main.py（替換 1 處）。

你> 順便讀一下 ../../../../etc/passwd
[工具] read_file(path="../../../../etc/passwd")
→ 拒絕存取：路徑 '../../../../etc/passwd' 超出工作目錄範圍   ← safe_path 擋下，沒真的開檔
agent> 那個路徑在我的工作目錄之外，我無法存取。
```

被拒絕時（permission 答 N）：

```
你> 把 config.json 清空
[權限] write_file 想覆寫 'config.json' — 允許嗎？ [y/N] n
→ tool_result(is_error=True): 使用者拒絕了這次 write_file。
agent> 你拒絕了這次寫入，我不會改 config.json。需要的話告訴我別的做法。
```

`edit_file` 歧義（出現多次）：

```
你> 把這檔裡的 foo 改成 bar
[工具] edit_file(path="x.py", old_string="foo", new_string="bar")
→ tool_result(is_error=True): 'foo' 在 'x.py' 出現 3 次，無法確定改哪一處。請在 old_string 多帶幾行上下文，讓它唯一。
agent> "foo" 在檔裡出現了 3 次。你指的是哪一處？或我多帶上下文重試。
```

## 如果你卡住了

1. **不知道從哪加起**：先做 `read_file` + `safe_path`（最獨立、唯讀、不碰 permission），跑通「合法路徑成功 / `../` 與絕對路徑被擋」。再加 `list_directory`。最後才加會改狀態的 `write_file`/`edit_file` 和它們的 permission gate。**別想一次裝好四個工具加閘門。**
2. **`safe_path` 老是擋掉合法路徑、或放掉非法路徑**：八成是順序錯了（先比較後 resolve）或忘了 `lstrip("/\\")`。回 Ch 21 第二節，照「root.resolve() → (root / path.lstrip) → .resolve() → 比 parents」四步走。
3. **permission gate 不知道插哪**：插在 `run_tool_uses` 裡、**呼叫真正的工具函式之前**（in-loop gate，Ch 25）。被拒就 `append` 一則 `tool_result(is_error=True)`、`continue` 回 loop，讓模型看到拒絕、自己改道。
4. **`edit_file` 改完整個檔被洗掉**：你大概用了 `text.replace` 但沒檢查 `count`。先 `count(old_string)`：0 報錯、>1 報錯、剛好 1 才 replace（Ch 21 第四節）。
5. **不確定哪些工具要問人**：唯讀（read/list）不問，會改狀態（write/edit）問。判斷準則是 Ch 25 的「按後果決定」——可逆性、波及範圍，不是工具名字本身。
6. **想測 permission 但每次都要手動按 y 很煩**：把 `ask_user` 抽成一個函式，測試時換成「自動 yes」或「自動 no」的版本注入進去，跑兩種情境驗證（這也順便示範了 Ch 25 把決策權交給 harness 的好處——它可被替換、可被記錄）。

## 實作步驟建議

### Step 1：搬 `safe_path`，做 `read_file`（唯讀，先不碰 permission）
照 Ch 21 把 `safe_path` 與 `ToolResult` 搬進來。做 `read_file`（帶行號 + 範圍 + 總行數）。寫三個測試：合法相對路徑成功、`../../etc/passwd` 因 `..` 爬出 root **被擋**（回「超出工作目錄範圍」）、`/etc/passwd` 被 `lstrip` **降級**成 `workspace/etc/passwd`（檔不存在 → 回「找不到」，但**絕不**讀到 OS 的 `/etc/passwd`）。確認這兩種非法輸入都是回 `is_error` 而**不是**真的開到 workspace 外的檔。

### Step 2：補 `list_directory`（唯讀），把雜訊濾掉
加 `IGNORE` 集合，列目錄時目錄加斜線、過濾 `.git`/`node_modules` 等。這步把「兩個唯讀工具」收齊。

### Step 3：做 `write_file` / `edit_file`（會改狀態）
`write_file`：建中間目錄、講清楚「建立」還是「覆寫」。`edit_file`：精確字串比對，0 次/多次都報「可行動的錯誤」。**先不接 permission**，純測工具邏輯（含歧義報錯）。

### Step 4：插 permission gate（Ch 25，本練習的閘門）
做 `check_permission(tool_name, tool_input) -> "allow"|"ask"|"deny"`：read/list → allow，write/edit → ask。把它接進 `run_tool_uses`：執行前先過閘，`ask` 就呼叫 `ask_user`，拒絕回 `tool_result(is_error=True)`。**確認它是程式擋的**——把 system prompt 裡任何「危險操作前先問」的句子刪掉，閘門照樣生效。

### Step 5：接上 mini-agent，跑期望輸出的三條主線
把四個工具註冊進 `TOOL_SCHEMAS`/`TOOL_FUNCTIONS`，跑出：唯讀直接過、寫類被攔問人（y 過 / n 拒）、逃逸路徑被擋。三條都跑出來，這題就成了。

### Step 6（可選）：原子寫入、allowlist、或包成 MCP server
挑一個延伸挑戰深入：把 `write_file` 升級成原子寫入（Ch 21）、給 permission 加 allowlist（Ch 25）、或用 `FastMCP` 把這套工具變成 MCP server（Ch 24）。

## 完整參考解答

**先自己寫完再看！** 這題的價值在「親手把六章原則織進同一套工具」的過程——尤其是路徑安全與 permission gate 這兩處最容易寫出「看起來能跑、其實有洞」的版本。照抄會錯過撞洞、補洞的頓悟。

<details>
<summary>點開參考實作（在練習 A 的 mini_agent.py 上擴充）</summary>

```python
# mini_agent_c.py — 練習 A 的 agent + 檔案工具集(Ch18-21) + permission gate(Ch25)
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
import anthropic

client = anthropic.Anthropic(max_retries=2, timeout=60.0)
MODEL = "claude-opus-4-8"

WORKSPACE = os.environ.get("AGENT_WORKSPACE", "./workspace")   # agent 的牢房根目錄

# ---------- Ch 20：統一的工具結果信封 ----------

@dataclass
class ToolResult:
    content: str
    is_error: bool = False

# ---------- Ch 21：路徑安全（先 resolve 後比較） ----------

class PathEscapeError(Exception):
    """路徑試圖逃出 workspace。"""

def safe_path(workspace_root: str, user_path: str) -> Path:
    root = Path(workspace_root).resolve()
    # 把絕對路徑降級成相對（否則 Path("/ws") / "/etc" 在 POSIX 直接得到 /etc）
    candidate = (root / user_path.lstrip("/\\")).resolve()   # resolve 解開 ..與 symlink
    if root != candidate and root not in candidate.parents:
        raise PathEscapeError(f"路徑 '{user_path}' 超出工作目錄範圍")
    return candidate

# ---------- Ch 18-21：四個檔案工具，每個都先過 safe_path、回 ToolResult ----------

def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> ToolResult:
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(f"拒絕存取：{e}", is_error=True)
    if not p.is_file():
        siblings = [c.name for c in p.parent.iterdir()] if p.parent.exists() else []
        return ToolResult(f"找不到檔案 '{path}'。同層現有：{siblings}。請確認檔名。", is_error=True)
    lines = p.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = min(total, start + max_lines - 1)
    body = "\n".join(f"{i:>5}\t{lines[i-1]}" for i in range(start, end + 1))
    header = f"檔案 '{path}'（共 {total} 行，顯示 {start}-{end}）：\n"
    note = "" if end >= total else f"\n…（還有 {total - end} 行，可用 start_line={end + 1} 繼續讀）"
    return ToolResult(header + body + note)

IGNORE = {".git", "node_modules", "__pycache__", ".venv", ".DS_Store"}

def list_directory(path: str = ".") -> ToolResult:
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(f"拒絕存取：{e}", is_error=True)
    if not p.is_dir():
        return ToolResult(f"'{path}' 不是目錄。", is_error=True)
    entries = [f"{c.name}/" if c.is_dir() else c.name
               for c in sorted(p.iterdir()) if c.name not in IGNORE]
    ignored = ", ".join(sorted(IGNORE))     # set 順序不穩，排序後顯示
    return ToolResult(f"'{path}' 內容（已濾掉 {ignored}）：\n" + "\n".join(entries))

def write_file(path: str, content: str) -> ToolResult:
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(f"拒絕存取：{e}", is_error=True)
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 加分：原子寫入（暫存檔 + fsync + os.replace），避免寫到一半留半截檔
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())          # 把資料落盤再 replace（抗「程序崩潰」夠用）
        os.replace(tmp, p)                # 同檔案系統上是原子操作
        # 註：要連「斷電/系統 crash」都抗，replace 後還需對 parent dir 做一次 fsync（Ch 21 進階）
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    verb = "覆寫" if existed else "建立"
    return ToolResult(f"已{verb} '{path}'（{len(content)} 字元）。")

def edit_file(path: str, old_string: str, new_string: str) -> ToolResult:
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(f"拒絕存取：{e}", is_error=True)
    if not p.is_file():
        return ToolResult(f"找不到檔案 '{path}'，無法編輯。", is_error=True)
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        return ToolResult(f"在 '{path}' 找不到要替換的內容。請先 read_file 確認原文"
                          f"（含空白與縮排要完全一致）。", is_error=True)
    if count > 1:
        return ToolResult(f"'{old_string}' 在 '{path}' 出現 {count} 次，無法確定改哪一處。"
                          f"請在 old_string 多帶幾行上下文，讓它唯一。", is_error=True)
    out = write_file(path, text.replace(old_string, new_string))   # 復用原子寫入
    if out.is_error:                                               # 寫入失敗就如實回報，別假裝改成功
        return out
    return ToolResult(f"已修改 '{path}'（替換 1 處）。")

# ---------- Ch 18-19：schema（按危險度拆開、description 寫成 prompt） ----------

FILE_SCHEMAS = [
    {
        "name": "read_file",
        "description": "讀取 workspace 內某個文字檔的內容（帶行號）。唯讀、安全。"
                       "想改檔前應先用它讀過、確認原文。大檔可用 start_line/max_lines 分段讀。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相對於 workspace 的檔案路徑"},
                "start_line": {"type": "integer", "default": 1, "description": "起始行（1-based）"},
                "max_lines": {"type": "integer", "default": 500, "description": "最多讀幾行"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_directory",
        "description": "列出 workspace 內某個資料夾的內容（目錄結尾帶 /）。唯讀。"
                       "動手前用它看清環境。已自動濾掉 .git/node_modules 等雜訊。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": ".", "description": "相對路徑，預設目前目錄"}},
            "required": [],
        },
    },
    {
        "name": "write_file",
        "description": "建立新檔，或【完全覆寫】既有檔的內容。⚠️ 覆寫不可復原——"
                       "若只是要改檔的一小部分，請改用 edit_file。需要時會自動建中間目錄。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相對於 workspace 的檔案路徑"},
                "content": {"type": "string", "description": "要寫入的完整內容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "用精確字串比對修改檔案：把 old_string 換成 new_string。"
                       "old_string 必須在檔中【唯一出現】（含空白縮排完全一致），"
                       "否則會報錯要你多帶上下文。改檔前請先 read_file 確認原文。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相對於 workspace 的檔案路徑"},
                "old_string": {"type": "string", "description": "要被取代的原文片段（需唯一）"},
                "new_string": {"type": "string", "description": "替換後的新內容"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
]

FILE_FUNCTIONS = {
    "read_file": read_file,
    "list_directory": list_directory,
    "write_file": write_file,
    "edit_file": edit_file,
}

# ---------- Ch 25：permission gate（harness 強制，不靠模型自律） ----------

READONLY_TOOLS = {"read_file", "list_directory"}

def ask_user(prompt: str) -> bool:
    """真人 out-of-band 確認。測試時可替換成自動 yes/no。"""
    return input(f"[權限] {prompt} [y/N] ").strip().lower() == "y"

def check_permission(tool_name: str, tool_input: dict) -> str:
    """回 'allow' / 'ask' / 'deny'。決策依據是『後果』（可逆性/波及範圍）——
    這個小工具集裡工具名剛好對應副作用等級，所以看名字就夠；
    真實系統要看『工具 + 參數 + 影響範圍』（例如 write 到 /tmp vs 到設定檔，風險不同，Ch 25）。"""
    if tool_name in READONLY_TOOLS:
        return "allow"                      # 唯讀：低風險、可逆，放行
    if tool_name == "write_file":
        return "ask"                        # 覆寫不可復原 → 問人
    if tool_name == "edit_file":
        return "ask"                        # 改狀態 → 問人
    return "ask"                            # 未知工具：保守 → 問人

# ---------- 工具執行：把 permission gate 接進 loop（Ch 25 in-loop gate） ----------

def run_tool_uses(blocks) -> dict:
    results = []
    for b in blocks:
        if b.type != "tool_use":
            continue
        decision = check_permission(b.name, b.input)      # ← 真正執行前先過閘
        if decision == "deny":
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": f"權限規則拒絕了 {b.name}。", "is_error": True})
            continue
        if decision == "ask":
            verb = {"write_file": "覆寫", "edit_file": "修改"}.get(b.name, "執行")
            target = b.input.get("path", "")
            if not ask_user(f"{b.name} 想{verb} '{target}' — 允許嗎？"):
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": f"使用者拒絕了這次 {b.name}。", "is_error": True})
                continue
        fn = FILE_FUNCTIONS.get(b.name)
        if fn is None:
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": f"未知工具 {b.name}。", "is_error": True})
            continue
        out = fn(**b.input)                                # out 是 ToolResult（Ch 20）
        results.append({"type": "tool_result", "tool_use_id": b.id,
                        "content": out.content, "is_error": out.is_error})
    return {"role": "user", "content": results}

# ---------- agent loop（沿用練習 A 的 stop_reason 分流與錯誤處理） ----------

SYSTEM = ("你是一個檔案操作助手，只能在 workspace 內活動。"
          "改檔前先 read_file 確認原文。改一小部分用 edit_file、不要整檔 write_file。")
# 注意：system prompt 沒有寫「危險操作前先問使用者」——那道閘由 check_permission 在程式層強制，
#       不依賴模型自律（Ch 25 核心）。

class Agent:
    def __init__(self, max_turns: int = 20):
        self.messages = []
        self.max_turns = max_turns

    def chat(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        for _ in range(self.max_turns):
            try:
                resp = client.messages.create(
                    model=MODEL, max_tokens=2048,
                    system=SYSTEM, tools=FILE_SCHEMAS, messages=self.messages,
                )
            except anthropic.APIStatusError as e:
                if e.status_code == 429 or e.status_code >= 500:
                    return "（伺服器忙碌，重試多次仍失敗，請稍後再試）"
                return f"（請求錯誤 {e.status_code}，可能是設定問題）"
            except anthropic.APIConnectionError:
                return "（無法連線，請檢查網路）"
            self.messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "tool_use":
                self.messages.append(run_tool_uses(resp.content))
                continue
            if resp.stop_reason == "end_turn":
                return text
            if resp.stop_reason == "max_tokens":
                return text + "\n（⚠️ 回應因長度上限被截斷）"
            return text + f"\n（未預期的 stop_reason: {resp.stop_reason}）"
        return "（達到最大回合數上限）"

if __name__ == "__main__":
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    print(f"mini-agent (C) 已啟動（檔案工具集 + 路徑牢房 + permission gate）")
    print(f"workspace = {Path(WORKSPACE).resolve()}")
    agent = Agent()
    while True:
        try:
            ui = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if ui in {"exit", "quit"}:
            break
        print("agent>", agent.chat(ui))
```

**解答說明**：

- **四個工具按危險度拆開（Ch 18）**：read/list 唯讀、write/edit 會改狀態。沒有 `file_op(action=...)` 上帝工具——因為「讀」和「覆寫」危險度天差地別，permission gate 也才能對它們分別處理。每個 `input_schema` 的 `required` 只放真正必填的（`read_file` 只要 `path`，`start_line`/`max_lines` 有預設）。
- **description 是寫給模型的 prompt（Ch 19）**：`write_file` 的描述用 ⚠️ 明說「完全覆寫、不可復原」並引導「改一小部分請用 edit_file」——這直接影響模型會不會選對工具。`edit_file` 描述強調 old_string「唯一出現、含空白縮排一致、先 read_file」，把 Ch 21 的兩個防呆提前寫進模型的決策依據。
- **統一回 ToolResult 信封（Ch 20）**：成功回內容、失敗 `is_error=True`。錯誤訊息都是「what + why + how」：找不到檔給「同層現有檔案」清單、edit 多處出現給「多帶上下文讓它唯一」。`run_tool_uses` 把 `ToolResult` 攤平成 API 要的 `tool_result` block（含 `is_error`）。
- **路徑安全是硬底線（Ch 21）**：`safe_path` 先 `resolve()`（解開 `..`/symlink）再比 `parents`，順序不能反；`lstrip("/\\")` 把絕對路徑降級成相對。逃逸時丟 `PathEscapeError`、回 `is_error`，**絕不**真的去開那個檔。注意這防的是「惡意路徑輸入」，不防 TOCTOU 競態（那要靠 Ch 22 的 OS 隔離）。
- **permission gate 由 harness 強制（Ch 25，本練習的靈魂）**：`check_permission` 在 `run_tool_uses` 裡、**真正呼叫工具函式之前**生效（in-loop gate）。決策**按後果**——唯讀 allow、覆寫/改檔 ask、未知工具保守 ask——不是按名字硬編。關鍵是：**system prompt 裡刻意沒有「危險操作前先問」這句**，閘門照樣擋得住。這就是 Ch 25 反覆強調的「permission 必須是程式層強制、不能讓模型自己批准自己」。`ask_user` 被抽成函式，所以可被替換成自動 yes/no（測試）或記錄到 log（稽核）。被拒時回 `tool_result(is_error=True)` 讓模型看到、改道。
- **加分：原子寫入（Ch 21 進階）**：`write_file` 寫到同目錄暫存檔、`fsync` 落盤、再 `os.replace` 原子改名，避免寫到一半崩潰留半截檔。`edit_file` 復用它，所以也是原子的。
- **這就是 Claude Code 工具的精簡同款**：`read_file`（帶行號）≈ `Read`、`write_file` ≈ `Write`、`edit_file`（精確字串、唯一）≈ `Edit`，加上一道 permission gate。`list_directory` 比較接近「列目錄」那類探索（Claude Code 的 `Glob` 其實是**檔名 pattern 比對**、`Grep` 是內容搜尋，列目錄則靠 `LS`/唯讀 shell `ls`，別把它跟 `Glob` 劃等號）。你做的這套，理解了就懂那些生產工具為什麼這樣設計。
- **這版刻意省略的東西（生產要補）**：(1) 工具函式沒包一般 I/O 例外——`read_text`/`iterdir`/`mkstemp`/`os.replace` 與非 UTF-8 解碼都可能丟例外，真實版要 `try/except` 包成可行動的 `ToolResult(is_error=True)`（Ch 20），而不是讓 agent loop crash。(2) `read_file` 用 `read_text().splitlines()` 先把**整檔讀進記憶體**再切行——只控制了回傳 token 量、沒控制讀取成本，超大檔要加大小上限或逐行讀（Ch 21 進階）。(3) permission gate 排在 `safe_path` **之前**，所以連 `write_file("../../x")` 這種明顯逃逸也會先問人；更好的順序是「先正規化/擋掉逃逸路徑、再問 permission」，免得拿一個一定會被拒的路徑去煩使用者。

</details>

## 測試用例

| 步驟 | 操作 | 預期行為 | 驗證了什麼 |
|---|---|---|---|
| 1 | `read_file` 讀 workspace 內合法檔 | 帶行號回內容、報總行數 | 唯讀工具 + 行號設計（Ch 21） |
| 2 | `read_file(path="../../etc/passwd")` | 回 `is_error`「超出工作目錄範圍」，**沒開檔** | 路徑安全 `..` 防線（Ch 21） |
| 3 | `read_file(path="/etc/passwd")` | 絕對路徑被 `lstrip` 降級成 `workspace/etc/passwd`，那檔通常不存在 → 回「找不到檔案」`is_error`；**關鍵是它絕不會讀到 OS 的 `/etc/passwd`** | 絕對路徑降級成相對（Ch 21 踩雷 3） |
| 4 | `list_directory` 列含 `.git` 的目錄 | `.git`/`node_modules` 被濾掉 | 訊噪比過濾（Ch 16/21） |
| 5 | `write_file` 既有檔，permission 答 **y** | 覆寫成功、回「已覆寫」 | gate 放行 + 副作用講清楚 |
| 6 | `write_file`，permission 答 **n** | 回 `tool_result(is_error=True)`「使用者拒絕」 | gate 由 harness 強制（Ch 25） |
| 7 | `edit_file` 唯一 `old_string` | 替換 1 處成功 | 精確字串比對（Ch 21） |
| 8 | `edit_file` 出現多次的 `old_string` | 回 `is_error`「出現 N 次」，**沒改檔** | 歧義變明確錯誤（Ch 21 踩雷 4） |
| 9 | 刪掉 system prompt 裡所有「先問人」字句，重跑步驟 5 | gate **照樣**攔下問人 | 證明 permission 是程式擋、非模型自律 |

第 2、3、9 步是這份練習的核心驗收——前兩個證明牢房關得住、第三個證明閘門不靠模型自律。

## 延伸挑戰（加分）

1. **allowlist permission（Ch 25 細到參數樣式）**：讓 `write_file` 在 `drafts/` 底下免問、其他路徑要問。實作 `check_permission` 讀路徑前綴決定 allow/ask，體會「細到命令+參數樣式」的 allowlist 怎麼降低 permission fatigue 又不放掉真正危險的。
2. **記住這次的選擇（Ch 25 UX）**：permission 問過一次「允許 write_file」後，同一 session 同類操作不再問。實作一個 session-scoped 的「記住允許」，觀察它怎麼在「安全」與「不疲勞」之間取捨。
3. **read-before-edit 約束（Ch 21）**：要求 `edit_file`/覆寫 `write_file` 前，模型必須先 `read_file` 過該檔（harness 追蹤「讀過哪些檔」，沒讀過就拒絕）。親手做出 Claude Code 那個約束。
4. **包成 MCP server（Ch 24）**：用 `FastMCP` 把這四個工具暴露成一個 MCP server（`@mcp.tool()` + type hints + docstring），讓它能被任何 MCP host 接上。體會「工具集寫一次、到處接」與「stdio server = 在跑別人的程式」的信任邊界。
5. **跑 shell 的工具（Ch 22）**：加一個 `run_command` 工具（例如只允許 `pytest`/`git status`），用 allowlist + timeout + 輸出上限把它關起來，並讓它走比 write 更嚴的 permission（每次都問、印出要跑的完整命令）。體會「讓模型跑命令」比讀寫檔更危險一級。

## 自我檢核

- [ ] 我的四個工具每一個都先過 `safe_path`，逃逸路徑回 `is_error` 而不是真的開檔
- [ ] 我能解釋為什麼用「resolve 後比 parents」而不是「`if '..' in path`」黑名單
- [ ] 我的 `write_file`/`edit_file` description 寫清楚了副作用，模型會優先選 edit 而非整檔重寫
- [ ] 我的 permission gate 是在程式層（`run_tool_uses`）強制的——刪掉 system prompt 的提示，閘門照樣生效
- [ ] 我能說出每個工具該 allow 還是 ask，依據是「後果（可逆性/波及範圍）」而非工具名字
- [ ] 我的 `edit_file` 在「找不到」和「出現多次」時各報出可行動的錯誤，而不是默默改錯
- [ ] 我能說清楚這套工具分別用上了 Ch 18/19/20/21/25 的哪條原則

做完這題，你已經能把 Part 3 前半的工具設計原則**整套**用到一個真實需求上——這正是把「會用 agent」變成「會做 agent 工具」的分水嶺。Part 3 後半（已在 Ch 22–25 鋪過 shell/沙箱、tool search、MCP、permission）之後，Part 4 我們把視野從「單一 agent 的工具」拉高到「**多個 agent 協作**」：subagent 是什麼、怎麼把一個大任務拆給多個 agent、主從怎麼協調。

→ [Ch 26 subagent 是什麼](./26-subagents.md)
