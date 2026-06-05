# Ch 21 — 檔案系統工具

> **目標**：設計並實作一組讓 agent 安全操作檔案系統的工具——讀、寫、列目錄、改檔。讀完你能說出為什麼檔案系統是 agent 最重要的「context 以外的工作區」、怎麼把 agent **關進一個工作目錄**避免它讀寫到不該碰的地方（路徑安全是本章核心）、為什麼「改檔」用精確字串比對比「整檔重寫」好，並能把 Ch 18–20 的工具設計原則全部套到這組真實工具上。

> **環境**：Python 3.11、`pathlib`。路徑安全的行為**跨平台有差異**：本章以 POSIX（Linux/macOS）語意為主，Windows 的差異（磁碟機代號、大小寫不敏感、保留檔名）會在用到時特別標注。所有範例在 Python 3.11 都能跑。

## 為什麼需要這個？agent 需要 context 以外的工作區

到目前為止，我們的 agent 只活在 context 裡——它知道的、做過的，全在那段對話歷史。但 context 是**稀缺又短命**的（Part 2 講了整整一個 Part）。真實的 agent——寫程式、整理資料、產報告——需要一個**持久、不佔 context、容量幾乎無限**的地方放東西。那就是**檔案系統**。

檔案系統對 agent 的意義，遠超過「讀個檔」：

- **它是工作區**：agent 把中間產物（草稿、下載的資料、生成的程式）寫到檔案，而不是塞進 context。這直接呼應 [Ch 16 的 handle 模式](./16-tool-result-pruning.md)——大東西放 context 外。
- **它是長期記憶的載體**：[Ch 14 的 memory](./14-memory.md) 本質就是「讀寫特定檔案」。memory 工具是檔案工具的特例。
- **它是 agent 改變世界的主要方式**：一個 coding agent 的「成果」就是它寫/改的那些檔。

回想 [練習 A](./practice-a-mini-agent-loop.md) 的 `read_text_file`：

```python
def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:   # ← 任意路徑，毫無防護
        return f.read()
```

它能跑，但**危險到不能上線**：模型（或一段被注入的惡意指令）只要傳 `path="/etc/passwd"` 或 `path="../../../../secret.env"`，就能讀走系統上任何檔。本章就是把這個天真的工具，升級成一組**安全、好用、模型用得對**的真實檔案工具。

## 先建立直覺：檔案工具是「agent 的手」，工作目錄是它的「牢房」

把 agent 想成一個你雇來、能力很強但你**不完全信任**的工讀生。你要他幫忙整理 `~/project` 這個資料夾。你會給他那個資料夾的鑰匙——但你**不會**給他整棟大樓的萬能鑰匙。萬一他理解錯了指令（或被人騙了），你希望損害**被關在那個資料夾裡**，波及不到你的私人檔案、系統設定、別的專案。

```
   ❌ 沒有牢房：agent 的手能伸到整個檔案系統
   /  ←──────── read("../../../../etc/passwd") 一路爬上去
   ├── etc/passwd        ← 被讀走
   ├── home/you/.ssh/    ← 被讀走
   └── project/  ← 你只想讓它碰這裡

   ✅ 有牢房（workspace jail）：手只能在 project/ 裡動
   project/  ←── 所有路徑都被「夾」回這個根目錄內
   ├── src/
   ├── notes.txt
   └── (試圖 ../ 逃出去 → 被擋下，回 is_error)
```

整章兩條主線：(1) **路徑安全**——把每個檔案操作關進一個 workspace 根目錄；(2) **工具設計**——讓讀/寫/列/改這四個操作對模型好用、結果好讀、錯誤可修正（直接用上 Ch 18–20）。

## 一、核心檔案工具集：四個動作

依 [Ch 18 原則 3](./18-tool-schema-design.md)（一個工具對應一個動作、危險度不同要拆開），檔案操作拆成四個獨立工具，而不是一個 `file_op(action=...)` 上帝工具——因為「讀」和「寫/刪」危險度天差地別：

| 工具 | 動作 | 危險度 | 對應 Ch 18 教訓 |
|---|---|---|---|
| `read_file(path, ...)` | 讀檔內容 | 低（唯讀） | 與寫入分開，讀可以寬鬆 |
| `list_directory(path)` | 列目錄 | 低（唯讀） | 讓 agent「看清環境」再動手 |
| `write_file(path, content)` | 建立/覆寫檔 | **高（會改變狀態、可能覆蓋）** | 危險操作獨立入口 |
| `edit_file(path, old, new)` | 局部修改 | **高** | 比 write 精準、破壞性小 |

拆開的好處（Ch 18 講過）：每個工具 `required` 乾淨、模型不必推論隱藏規則、危險操作有明確入口、`write_file` 的描述可以單獨寫滿「這會覆蓋既有檔、不可復原」這類警告（Ch 19/20 的副作用標註）。

## 二、路徑安全：把 agent 關進工作目錄（本章核心）

這是整章最重要、也最容易寫錯的一節。目標：**無論模型傳什麼 path，最終實際操作的檔案都必須落在 workspace 根目錄之內**。落在外面的，一律拒絕。

### 攻擊面：路徑怎麼「逃出去」

天真的拼接 `workspace + "/" + path` 擋不住這些：

- **`..` 向上爬**：`path="../../etc/passwd"` → 拼出來爬到 workspace 外。
- **絕對路徑**：`path="/etc/passwd"`（POSIX）或 `path="C:\\Windows\\..."`（Windows）→ 直接無視 workspace。
- **符號連結（symlink）**：workspace 裡有個 symlink 指向 `/etc`，順著它讀就出去了。
- **Windows 特例**：`..\\`、磁碟機代號 `D:`、UNC 路徑 `\\server\share`、保留名 `CON`/`NUL`。

### 正解：resolve 後再驗證「在不在牢房裡」

關鍵手法是**先把路徑完全解析（resolve）成正規絕對路徑——包含解開 `..` 和 symlink——再檢查它是不是在 workspace 根目錄底下**。順序不能反：必須先 resolve、後檢查，因為 `..` 和 symlink 只有在 resolve 之後才現形。

```python
from pathlib import Path

class PathEscapeError(Exception):
    """路徑試圖逃出 workspace。"""

def safe_path(workspace_root: str, user_path: str) -> Path:
    """把使用者給的 path 夾進 workspace。逃出去就丟 PathEscapeError。"""
    root = Path(workspace_root).resolve()          # workspace 的正規絕對路徑
    # 不論 user_path 是相對還絕對，都接到 root 底下再 resolve
    # （注意：若 user_path 是絕對路徑，Path("/a") / "/etc" 在 POSIX 會變 "/etc"，
    #   所以要先把開頭的分隔符去掉，強制當成相對路徑處理）
    candidate = (root / user_path.lstrip("/\\")).resolve()
    # strict=False：允許指向還不存在的檔（write_file 要新建檔時需要）
    # 核心檢查：resolve 後的路徑必須在 root 之內（或就是 root 本身）
    if root != candidate and root not in candidate.parents:
        raise PathEscapeError(f"路徑 '{user_path}' 超出工作目錄範圍")
    return candidate
```

逐行拆解這個防線為什麼成立：

- **`root.resolve()`**：把 workspace 自己也正規化（解開任何 symlink/`..`），否則後面的比較基準就不對。
- **`user_path.lstrip("/\\")`**：把絕對路徑「降級」成相對。否則 `Path("/ws") / "/etc/passwd"` 在 POSIX 會直接得到 `/etc/passwd`（pathlib 的 `/` 運算子遇到絕對右運算元會丟棄左邊）——這是最常見的漏洞。（Windows 的 rooted-relative 路徑如 `\foo` 行為更微妙，drive 不一定 reset，見下方平台差異說明。）
- **`.resolve()`**：**這一步解開 `..` 和 symlink**。`/ws/../etc` 會變成 `/etc`，於是下一步的檢查就能抓到它跑出去了。symlink 也在這裡被跟隨到真實目標。
- **`root not in candidate.parents`**：resolve 完才比較。只有當 candidate 真的在 root 底下（root 是它的某層 parent），或 candidate 就是 root，才放行。

關於 `user_path.lstrip("/\\")` 這步要講清楚它的**性質**：它不是「安全步驟」，而是一個**產品決策**——「把使用者給的絕對路徑重新解釋成 workspace 相對路徑」。`/etc/passwd` 被重新解釋成 `<root>/etc/passwd`，最後仍由 parents 檢查保證沒逃出去。真正擋住逃逸的是 `resolve()` + parents 檢查，不是 `lstrip`。另一種同樣合理的設計是**直接拒絕**絕對路徑（`if Path(user_path).is_absolute(): raise ...`），看你想讓模型用相對還絕對路徑。在 Windows 上尤其要注意：`lstrip` 並不會「降級」帶磁碟機代號的 `C:\Windows\...`，那種路徑得靠 parents 檢查擋下，或在前面就明確 reject。

> **威脅模型（一定要講清楚防什麼、不防什麼）**：這個 `safe_path` 防的是「**模型（或被注入的指令）傳入惡意路徑**」——`..`、絕對路徑、指向外部的既有 symlink。它**不防**「**同機上另一個不受信任的程序在你檢查之後、實際開檔之前，把檔案樹換掉**」這種競態（TOCTOU, time-of-check to time-of-use）：`resolve()` 通過檢查到 `read_text`/`write_text` 真正開檔之間有時間差，攻擊者若能改動 workspace 內的目錄/symlink，仍可能讓實際開的檔指向外面。要防到這個等級，光靠路徑檢查不夠——得用 OS 層的隔離（容器、sandbox、權限降級）或 `openat`/`O_NOFOLLOW` 類的原子開檔 API。本章的 `safe_path` 是「應用層的第一道牆」，不是全部的牆——這正是下一章（Ch 22 沙箱）要補的。

> **認識論誠實（平台差異）**：`resolve()` 跟隨 symlink 的行為、`strict` 參數的語意，在不同 OS 與 Python 版本上有細節差異。Windows 上的攻擊面更廣：磁碟機代號（`C:\...`、相對磁碟機 `D:foo`）、UNC 路徑（`\\server\share`）、rooted-relative（`\foo`，pathlib 文件指出此時 drive 不一定會 reset）、大小寫通常不敏感但**取決於檔案系統設定、不能絕對化**、保留檔名（`CON`/`NUL`/`AUX`）、交替資料流（ADS，`file.txt:stream`）、extended-length 路徑（`\\?\...`）。本章的 `safe_path` 給的是**正確的骨架與順序（先 resolve 後比較）**；要上生產，務必針對你的目標平台把上面這些都列成測試項，別假設一份程式碼在所有 OS 行為一致。

### 接進工具

每個檔案工具的第一件事，都是過 `safe_path`。逃出去就回 `is_error`（Ch 20）：

```python
WORKSPACE = "/home/agent/project"   # agent 的牢房根目錄

def read_file(path: str) -> "ToolResult":          # ToolResult 見 Ch 20
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(content=f"拒絕存取：{e}", is_error=True)
    if not p.exists():
        # Ch 20 的「好錯誤」：what + why + how
        siblings = [c.name for c in p.parent.iterdir()] if p.parent.exists() else []
        return ToolResult(
            content=f"找不到 '{path}'。同層現有檔案：{siblings}。請確認檔名。",
            is_error=True)
    return ToolResult(content=p.read_text(encoding="utf-8"))
```

注意這裡 Ch 20 的教訓全用上了：路徑逃逸與檔案不存在都標 `is_error=True`、錯誤訊息給「現有檔案清單」讓模型能自我修正。

## 三、讀檔設計：行號與範圍——為了「改」而讀

天真的 `read_file` 回整檔純文字。但真實 agent 讀檔常常是**為了接下來改它**——而要改，模型得能精確指出「改哪一行」。所以好的 `read_file` 應該：

1. **帶行號**：讓模型能引用「第 42 行」。
2. **支援範圍**：大檔不必一次全讀（Ch 16 的源頭裁剪），讀 `start`–`end` 行。
3. **回報總行數**：讓模型知道還有沒有沒讀到的部分（Ch 20 的「講脈絡」）。

```python
def read_file(path: str, start_line: int = 1, max_lines: int = 500) -> "ToolResult":
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(content=f"拒絕存取：{e}", is_error=True)
    if not p.is_file():
        return ToolResult(content=f"找不到檔案 '{path}'。", is_error=True)

    lines = p.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = min(total, start + max_lines - 1)
    body = "\n".join(f"{i:>5}\t{lines[i-1]}" for i in range(start, end + 1))  # 行號靠右對齊
    header = f"檔案 '{path}'（共 {total} 行，顯示 {start}-{end}）：\n"
    note = "" if end >= total else f"\n…（還有 {total - end} 行未顯示，可用 start_line={end + 1} 繼續讀）"
    return ToolResult(content=header + body + note)
```

行號格式（`{i:>5}\t`）值得講：行號靠右對齊、用 tab 和內容分開，模型容易解析「哪行是哪行」，同時不會把行號誤當成檔案內容的一部分。`max_lines=500` 是個**保底範圍**——避免一次把超大檔倒進 context（Ch 16），數字本身是經驗值，不是魔法常數：太小模型要多次往返，太大又失去裁剪意義，500 行對多數原始碼/文字檔是個合理折衷。

## 四、編輯檔案：三種策略，為什麼選「精確字串比對」

讓 agent 改檔，有三種設計，破壞性與精準度差很多：

```
   策略           模型要提供什麼            風險
   ─────────────────────────────────────────────────────
   ① 整檔重寫     整個新檔內容              超高：模型重打整檔，
                                          很容易漏掉/改壞沒要動的部分
   ② 行號替換     行號範圍 + 新內容          中：行號會因前面的編輯而位移，
                                          模型容易算錯行
   ③ 精確字串比對  舊字串 + 新字串           低：只動明確指定的片段
```

**策略 ③（精確字串比對）是現代 coding agent（含 Claude Code 的 Edit 工具）的主流選擇**，因為它最不容易出錯：模型提供「要被取代的原文片段」和「替換後的新片段」，工具在檔案裡找到那段**唯一**的原文、換掉它。模型不必重打整檔（省 token、不會誤改），也不必算行號（不會因位移出錯）。

```python
def edit_file(path: str, old_string: str, new_string: str) -> "ToolResult":
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(content=f"拒絕存取：{e}", is_error=True)
    if not p.is_file():
        return ToolResult(content=f"找不到檔案 '{path}'，無法編輯。", is_error=True)

    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        # 找不到要改的原文：給可行動的錯誤（Ch 20）
        return ToolResult(
            content=f"在 '{path}' 找不到要替換的內容。請先 read_file 確認原文（含空白與縮排要完全一致）。",
            is_error=True)
    if count > 1:
        # 出現多次 → 替換哪個有歧義，拒絕並要求更多上下文
        return ToolResult(
            content=f"要替換的內容在 '{path}' 出現 {count} 次，無法確定改哪一處。"
                    f"請在 old_string 多帶幾行上下文，讓它在檔案中唯一。",
            is_error=True)
    p.write_text(text.replace(old_string, new_string), encoding="utf-8")
    return ToolResult(content=f"已修改 '{path}'（替換 1 處）。")
```

這個設計的兩個關鍵防呆，都是把「歧義」變成「明確的錯誤」而不是「猜」：

- **找不到 → 報錯、要求先 `read_file`**：模型常憑記憶拼 `old_string`、空白縮排對不上。明確告訴它「原文要完全一致，先去讀」，比默默失敗好。
- **出現多次 → 拒絕、要求更多上下文**：如果 `old_string` 在檔裡出現兩次，替換哪一個是歧義的。強迫模型多帶上下文讓片段**唯一**——這是「讓模型一次改對」的關鍵。

這套行為和 Claude Code 的 `Edit` 工具一致，但真實版還多兩個機制值得知道：(1) **read-before-edit**——要求模型編輯前必須先讀過該檔（避免憑記憶瞎拼、也能偵測檔案在這期間是否被改動）；(2) **`replace_all` 選項**——當你**確實**想把所有出現處一起換（例如重命名變數），可顯式開 `replace_all=true`，這時「出現多次」就不報錯而是全換。也就是說「多處報錯」是**預設**的安全行為，不是唯一行為——把決定權留給呼叫方。

## 五、寫檔與列目錄的設計細節

**`write_file`：覆寫是危險的**

`write_file` 會建立新檔或**覆蓋**既有檔。覆蓋不可復原，所以：

- **description 要明說**（Ch 19/20 的副作用標註）：「若檔案已存在會被完全覆蓋」。
- **考慮「先讀再寫」的約束**：有些 harness 規定「要覆蓋既有檔前，模型必須先 `read_file` 過它」，避免模型在沒看過內容的情況下盲蓋（Claude Code 的 `Write` 對既有檔就有這個要求）。這把一個破壞性操作變得更謹慎。
- **原子寫入**（進階，見下節）：避免寫到一半失敗留下半截檔。

```python
def write_file(path: str, content: str) -> "ToolResult":
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(content=f"拒絕存取：{e}", is_error=True)
    existed = p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)     # 需要的話建中間目錄
    p.write_text(content, encoding="utf-8")
    verb = "覆寫" if existed else "建立"
    return ToolResult(content=f"已{verb} '{path}'（{len(content)} 字元）。")  # 講清楚做了什麼
```

**`list_directory`：過濾雜訊**

列目錄要幫模型「看清環境」，但別把 `.git/`、`node_modules/`、`__pycache__/` 這種雜訊全倒出來（Ch 16 訊噪比）：

```python
IGNORE = {".git", "node_modules", "__pycache__", ".venv", ".DS_Store"}

def list_directory(path: str = ".") -> "ToolResult":
    try:
        p = safe_path(WORKSPACE, path)
    except PathEscapeError as e:
        return ToolResult(content=f"拒絕存取：{e}", is_error=True)
    if not p.is_dir():
        return ToolResult(content=f"'{path}' 不是目錄。", is_error=True)
    entries = []
    for c in sorted(p.iterdir()):
        if c.name in IGNORE:
            continue
        entries.append(f"{c.name}/" if c.is_dir() else c.name)  # 目錄加斜線，一眼分辨
    return ToolResult(content=f"'{path}' 內容（已濾掉 {IGNORE}）：\n" + "\n".join(entries))
```

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 路徑檢查順序 | 先比較字串再 resolve | **先 resolve 再比較** | 一定先 resolve——`..`/symlink 只有 resolve 後現形 |
| 工具粒度 | `file_op(action=...)` | **read/write/edit/list 分開** | 分開：讀與寫危險度不同（Ch 18） |
| 改檔方式 | 整檔重寫 / 行號替換 | **精確字串比對** | 字串比對：省 token、不誤改、無行號位移 |
| `edit` 找到多處 | 改第一個 / 全改 | **報錯要更多上下文** | 報錯：歧義不該用猜的 |
| 讀檔回傳 | 整檔純文字 | **帶行號 + 範圍 + 總行數** | 後者：為「改」鋪路、可裁剪、給脈絡 |
| list 輸出 | 全部倒出 | **濾掉 .git/node_modules** | 過濾：訊噪比（Ch 16） |

## 踩雷集錦

1. **用字串拼接做路徑檢查**：`if "../" in path` 這種黑名單擋不住 symlink、絕對路徑、編碼變體、Windows `..\\`。應用層可靠的做法是「resolve 成絕對路徑後，檢查在不在 root 底下」——但記得它防的是「惡意路徑輸入」，不防 TOCTOU 競態（那要靠 OS 隔離，見威脅模型與 Ch 22）。
2. **先檢查後 resolve（順序反了）**：在 resolve 之前比較路徑字串，`..` 和 symlink 還沒現形，等於沒檢查。**必須先 resolve、後比較**。
3. **忘了把絕對路徑降級**：`Path(root) / "/etc/passwd"` 在 POSIX 直接得到 `/etc/passwd`，root 被丟掉。要先 `lstrip("/\\")` 把使用者路徑當相對處理。
4. **`edit_file` 在出現多處時默默改第一個**：歧義時模型以為改對了、其實改錯地方。出現多次要報錯、要求唯一上下文。
5. **`write_file` 不警告覆寫**：覆蓋不可復原。description 要標明，並考慮「先讀再寫」約束。
6. **list 把 .git/node_modules 全倒出來**：幾千個檔淹沒 context、訊噪比極差。要過濾雜訊。
7. **假設一份路徑安全程式碼跨平台通用**：Windows 的大小寫不敏感、磁碟機代號、保留檔名、UNC 路徑都是額外攻擊面。骨架通用，但要針對平台補測試。

## 進階：再往深一層

- **原子寫入（atomic write）**：`write_text` 寫到一半若程式崩潰/斷電，會留下半截檔——對重要檔很糟。生產做法是「寫到同目錄的暫存檔，再 `os.replace()` 原子改名」。`os.replace` 在同一檔案系統上是原子操作，要嘛舊檔要嘛完整新檔，不會有中間狀態。為什麼要同目錄？因為跨檔案系統的 rename 不保證原子。但要分清楚兩種耐久性：這招對「**程序崩潰**」夠用（檔案系統 metadata 一致）；要抗「**斷電/系統 crash**」則還不夠——得在 `os.replace` 前先把暫存檔 `flush()` + `os.fsync()` 把資料真正落盤、replace 後再對目錄 fd 做一次 `fsync` 確保改名也落盤。多數 agent 場景到 `os.replace` 就夠，但你若在寫不能丟的資料，要知道 fsync 這層。
- **symlink 的兩難**：本章 `resolve()` 會跟隨 symlink、把指向 workspace 外的連結擋下——安全。但有些合法場景需要 workspace 內的 symlink（例如 monorepo）。要支援就得更細緻地判斷「symlink 的目標在不在 root 內」，而不是一律解開。安全與彈性的取捨，依你的威脅模型決定。
- **編碼與二進位檔**：本章假設 UTF-8 文字。遇到非 UTF-8（Big5、latin-1）或二進位檔，`read_text(encoding="utf-8")` 會丟 `UnicodeDecodeError`。真實工具要嘛偵測編碼、要嘛對二進位檔回「這是二進位檔，無法當文字讀」的明確錯誤（Ch 20），而不是崩潰。
- **大檔與 streaming**：`read_text()` 把整檔讀進記憶體。GB 級的檔會爆記憶體。配合第三節的行範圍 + `max_lines`，或用 `mmap`/逐行讀。多數 agent 場景檔不大，但要知道這個邊界。
- **並發與檔案鎖**：多個 agent（或 subagent，Ch 26）同時寫同一檔會互相覆蓋。需要時用檔案鎖（`filelock` 套件）或把寫入序列化。單 agent 通常不必，但 multi-agent（Ch 27）要當心。
- **這就是 Claude Code 的工具設計**：Claude Code 的 `Read`（帶行號）、`Write`、`Edit`（精確字串、要求唯一）、`Glob`、`Grep` 正是本章原則的成熟版。你做的這組工具，是同一套設計的精簡版——理解了本章，再看那些工具的行為就很有共鳴。

## 動手練習

1. 把練習 A 那個天真的 `read_text_file` 換成本章的 `safe_path` + `read_file`。寫測試：`path="../../etc/passwd"`、`path="/etc/passwd"`、workspace 內的合法檔，確認前兩個被擋、第三個成功。
2. **故意把 `safe_path` 寫錯**：把「先 resolve 後比較」改成「先比較字串 `if '..' in path`」，再用一個指向 workspace 外的 symlink 攻擊它，親眼看到黑名單防線怎麼被繞過。然後改回正解。
3. 實作 `edit_file` 的「出現多次」分支：造一個有重複內容的檔，用一個會匹配兩處的 `old_string` 去改，確認工具報錯而非默默改第一個。再多帶一行上下文讓它唯一，確認成功。
4. 給 `read_file` 加上行號與 `start_line`/`max_lines`，讀一個 2000 行的檔，觀察它怎麼分段、怎麼提示「還有 N 行」。
5. （進階）把 `write_file` 改成原子寫入（暫存檔 + `os.replace`），並在寫入中途 `raise` 模擬崩潰，確認原檔沒有被寫成半截。

## 本章重點整理

- 檔案系統是 agent「context 以外的工作區」——放中間產物、當長期記憶、是 agent 改變世界的主要方式。
- **路徑安全是核心**：把 agent 關進 workspace 牢房。唯一可靠的做法是「先 `resolve()`（解開 `..`/symlink）、再檢查路徑在不在 root 底下」，順序不能反；絕對路徑要先降級成相對。
- 四個工具按危險度拆開（read/list 唯讀、write/edit 會改狀態），套用 Ch 18 的粒度原則。
- 讀檔帶行號 + 範圍 + 總行數——為「改」鋪路、可裁剪、給脈絡。
- 改檔用精確字串比對：省 token、不誤改、無行號位移；找不到或出現多次要報錯（把歧義變明確錯誤）。
- 寫檔覆寫危險要警告；list 要過濾雜訊。錯誤全部用 Ch 20 的「what+why+how」。

## 自我檢核

- [ ] 我能解釋為什麼「先 resolve 後比較」的順序不能反，並舉出一個反過來會被繞過的例子
- [ ] 不看本章，我能說出 `Path(root) / "/etc/passwd"` 的陷阱，以及怎麼修
- [ ] 面試被問「為什麼 coding agent 用字串比對改檔而不是行號」，我能講出三個理由
- [ ] 我能說明 `edit_file` 在「找不到」和「出現多次」時各該怎麼處理，以及為什麼
- [ ] 我知道這套工具在 Windows 上有哪些額外的路徑安全考量

## 延伸閱讀

### 官方文件

- **[Python — `pathlib`](https://docs.python.org/3/library/pathlib.html)**
  - **讀哪裡**：`Path.resolve()`、`Path.is_relative_to()`（3.9+）、`/` 運算子對絕對路徑的行為那幾段。
  - **能學到什麼**：本章 `safe_path` 的每個 API 的精確語意——尤其 `resolve()` 對 symlink 和 `..` 的處理、以及 `/` 遇到絕對右運算元會丟棄左邊這個陷阱。
  - **前提知識**：懂基本檔案路徑即可。

- **[OWASP — Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal)**
  - **讀哪裡**：攻擊範例那節（`../`、絕對路徑、編碼變體）。
  - **能學到什麼**：本章「攻擊面」一節的權威清單——理解你到底在防什麼，才知道 `safe_path` 為什麼要那樣寫。
  - **前提知識**：無。

### 部落格 / 技術文章

- **[Anthropic — Claude Code overview](https://code.claude.com/docs/en/overview)** 與 **[Tools reference](https://code.claude.com/docs/en/tools-reference)** — Anthropic Docs
  - **這篇說什麼**：Claude Code 的工具集（Read/Write/Edit/Glob/Grep）與它如何在工作目錄內操作。`Edit` 的精確字串、read-before-edit、`replace_all` 等行為的精準定義在 **tools reference** 那頁，overview 只是概覽。
  - **讀哪裡**：tools reference 裡 `Read`/`Write`/`Edit` 各自的行為與約束。
  - **為什麼值得讀**：本章設計的「成熟版範本」——看一個生產級 coding agent 怎麼設計檔案工具，對照你做的精簡版，本章第四節的 edit 設計就是它的精簡版。

下一章我們把「能力」再往上推一級：除了讀寫檔案，agent 還常常需要**執行命令**（跑測試、編譯、git）。但「讓模型跑任意 shell 命令」是檔案讀寫之外更危險的一步——下一章談怎麼用沙箱把這個能力關起來。

→ [Ch 22 執行 shell 與沙箱](./22-shell-and-sandbox.md)
