# Ch 0 — 環境搭建：uv、pytest-bdd、Playwright、Spec Kit 與 Claude Code

> **目標**：搭出本課所有章節共用的實驗室：一個釘死版本的 Python 3.12 venv（pytest-bdd、FastAPI、Playwright）、一份放在 venv 外面的 Chromium、用 uvx 固定在 1.2.5 的 Spec Kit、Claude Code 2.1.295 與 skill-creator plugin。最後用一支 `check_env.py` 與一個「一份 feature、兩個 driver」的冒煙測試證明它們接得起來，並親眼看過七種最常見的環境錯誤。

> **環境**：macOS 26.6（Apple Silicon，Darwin 25.6.0）、zsh 5.9、uv 0.13.1、CPython 3.12.15、pytest 9.2.0、pytest-bdd 9.0.1（gherkin-official 42.0.1）、FastAPI 0.162.0、uvicorn 0.54.0、httpx 0.28.1、Playwright 1.64.1（Chromium 156.0.8078.4）、PyYAML 6.0.3、Spec Kit 1.2.5、Claude Code 2.1.295。本章所有指令與輸出都是 2026-11-02 在這台機器上實際跑出來的。

## 為什麼需要這個？

repo 裡其他 AI 課已經教了大部分概念，但沒有一門把 BDD 迴圈、Playwright 與 Spec Kit 1.x 真的跑起來；這門課要**跑**：各章輪流會用到一份被 pytest-bdd 執行的 Gherkin feature、一個由 uvicorn 啟動的 FastAPI app、一個被 Playwright 驅動的瀏覽器、一份 Spec Kit scaffold、一組 Claude Code 的 hook 與 skill。這些工具各走各的版本時鐘，其中四個特別容易讓輸出對不上：

- **Spec Kit**：[SDD 課](../spec_driven_development/00-environment-setup.md)用 0.11.10（2026-06-29），本課用 1.2.5（2026-10-28），CHANGELOG 從 0.11.10 之後到 1.2.5 為止記了 62 個 release。書和實物也會脫節：SDD 課原本照文件把 Claude Code 的指令寫成 `.claude/commands/speckit.*.md`，實跑才發現這兩版裝的都是 `.claude/skills/speckit-*/`（CHANGELOG 0.4.5 就改成 skill；SDD 課已依實跑修正，[Ch 12](./12-spec-kit-1x.md) 兩版都實跑過）。
- **Claude Code**：原生安裝預設在背景自動更新，今天的 2.1.295 下週可能就不是了；本章實跑當天（2026-11-02），[官方 CHANGELOG](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) 最上面的版本已經是 2.1.316。
- **Playwright**：每個版本只認一個特定 build 的瀏覽器，升級套件卻沒重裝瀏覽器，UI 測試就起不來。
- **pytest-bdd**：9.0.1 在本章實跑前兩天（2026-10-31）才上 PyPI，它的 Gherkin 解析器又是另一個有自己版本號的套件 gherkin-official。

版本一漂，書上的輸出和你看到的就對不上，而你分不出是自己做錯還是環境不同。所以本章的重點不是「裝起來」，是**釘死、再驗證**：每個版本有出處，每個工具有一條會 pass 或 fail 的檢查。uv 與 `specify` 的基本安裝在 [SDD Ch 0](../spec_driven_development/00-environment-setup.md) 講過，用隔離設定目錄試裝 plugin 的手法在 [pstack Ch 0 的「一、先在隔離目錄試裝（推薦做法）」](../pstack/00-environment-setup.md)，本章不重講，只補它們沒有的：Python 測試工具鏈、Spec Kit 1.2.5 的固定版本用法、瀏覽器，以及把全部串起來的冒煙測試（smoke test，最小的「整條線通不通」檢查）。

## 先建立直覺：三個世界、三個版本時鐘

```
  你的 zsh
   ├─ 世界一：/tmp/lab/aibdd/.venv（uv 建的 venv，本課唯一的 Python 環境）
   │     python 3.12.15 ─ pytest ─ pytest-bdd ─ FastAPI ─ uvicorn ─ httpx ─ PyYAML
   │     playwright 1.64.1（Python client）
   │          └─ 啟動內附的 Node driver ─▶ Chromium 156（在 ~/Library/Caches/ms-playwright，venv 外面）
   ├─ 世界二：uvx 的暫時環境（用到時才組出來，版本寫在指令裡）
   │     specify 1.2.5，跑在 uvx 自己挑的 Python 上（這台是 3.15.0）
   └─ 世界三：Claude Code（原生執行檔，預設會自己更新）
         ~/.local/share/claude/versions/2.1.295；設定與 plugin 在 ~/.claude/（CLAUDE_CONFIG_DIR 可換）
```

把它想成一間實驗室：venv 是你的**工作台**，上面每樣工具都貼了型號；Chromium 是**公用器材櫃**裡的儀器，所有工作台共用一台；Spec Kit 是每次去**租借櫃台**領的工具，領的時候報型號（tag）；Claude Code 是一位**晚上會自己進修**的助理。這個比喻的邊界：器材櫃是「這個使用者、整台機器」共用的，清掉它（刪快取），壞掉的是所有用同一版 Playwright 的專案，不只這門課。

「裝好了」因此不是一個狀態，是三個。本章最後的 `check_env.py` 一次檢查三個世界，這是後面每章出問題時第一個要跑的指令。

## 一、實驗室的版圖

```
/tmp/lab/aibdd/
├── .venv/                   共用 venv：只裝本章這 7 個套件（與它們的依賴）
├── ch00-setup/              本章：check_env.py、smoke/（冒煙測試）
├── ch03-loop/ ...           每章一個資料夾 chNN-<slug>/，從空目錄開始
├── practice-a-first-loop/   練習 practice-x-<slug>/
└── final/                   Final Project
/tmp/lab/bin/specify125      Spec Kit 1.2.5 的固定版本 wrapper（選用，第四節）
```

三條規則。**每章從空目錄開始**：章節給你每個檔案的完整內容，或明講要從哪一章複製狀態，所以任何一章壞了都能單獨重建。**共用 venv 只裝這 7 個套件**：某章真的需要別的套件（例如 Ch 32 的 SQLAlchemy）就建自己的 venv，因為共用環境一旦被某章「順手」加了東西，後面的章節就會在你沒注意時依賴它。**`/tmp` 是暫存區**：內容可能在重開機或系統清理時消失（以你的系統實際行為為準），所有東西都能照章重建，但別把只存在這裡的筆記當唯一一份。另外，macOS 的 `/tmp` 是 symlink（`ls -ld /tmp` 印出 `/tmp -> private/tmp`），所以輸出裡常出現 `/private/tmp`，指的是同一個地方。

## 二、Python 工具鏈：uv、venv 與釘死的版本

uv 是 Astral 用 Rust 寫的 Python 套件與環境管理工具。在它之前常見的組合是 pyenv 或 Homebrew 裝 Python、`python -m venv` 建虛擬環境（virtual environment，有自己 `site-packages` 的獨立 Python 目錄）、pip 安裝。uv 把「找到或下載對的 Python」「建 venv」「安裝」收進同一個工具。

```bash
uv venv --python 3.12 /tmp/lab/aibdd/.venv
uv pip install --python /tmp/lab/aibdd/.venv/bin/python "pytest==9.2.0" "pytest-bdd==9.0.1" \
  "fastapi==0.162.0" "uvicorn==0.54.0" "httpx==0.28.1" "playwright==1.64.1" "pyyaml==6.0.3"
```

```
Using CPython 3.12.15
Creating virtual environment at: /tmp/lab/aibdd/ch00-setup/.venv
Activate with: source /tmp/lab/aibdd/ch00-setup/.venv/bin/activate
Using Python 3.12.15 environment at: /tmp/lab/aibdd/ch00-setup/.venv
Resolved 33 packages in 549ms
Installed 33 packages in 44ms
…（略：之後是 33 行「+ 套件==版本」，例如 + pytest-bdd==9.0.1）
```

（共用 venv 在寫這章前就建好了，其他章節正在用，所以上面的輸出是在 `ch00-setup/.venv` 用同一組指令重跑的；除了路徑，你看到的會一樣。整個安裝 0.65 秒，前提是套件已在 uv 的快取裡。）機器上沒有 Python 3.12 時，uv 會自己下載一份。我們用一個空的安裝目錄模擬了這個情境（`UV_PYTHON_INSTALL_DIR=... uv venv --managed-python --python 3.12 ...`），它印出 `Downloading cpython-3.12.15-macos-aarch64-none (download) (23.9MiB)` 後照常建好 venv。

7 個直接依賴展開成 33 個套件，有幾個後面會碰到：`gherkin-official` 與 `mako` 是 pytest-bdd 帶進來的（前者真正解析 `.feature`，Ch 28 的 lint 直接用它；後者是 Ch 27 產生 step 骨架的樣板引擎），`starlette` 與 `pydantic` 是 FastAPI 帶的（`HTTPException` 的底層類別在 starlette，422 驗證錯誤的明細由 pydantic 產生；把錯誤包成 `{"detail": ...}` JSON 的是 FastAPI 自己的 exception handler，starlette 預設只回純文字）。

**為什麼寫 `--python`，而不是先 activate 再裝？** `uv pip install` 沒指定時，會依序找 `VIRTUAL_ENV`、`CONDA_PREFIX`、目前目錄或**最近一層父目錄**的 `.venv`。在一個用完即丟的 `uvdemo/` 裡問三次（還沒 activate 任何 venv）：

```bash
mkdir -p /tmp/lab/aibdd/ch00-setup/uvdemo/sub
cd /tmp/lab/aibdd/ch00-setup/uvdemo/sub && uv pip list 2>&1 | head -1
cd /tmp/lab/aibdd/ch00-setup/uvdemo && uv venv -q --python 3.12 && cd sub && uv pip list 2>&1 | head -1
cd /tmp && uv pip install --dry-run pyyaml
rm -rf /tmp/lab/aibdd/ch00-setup/uvdemo
```

```
Using Python 3.12.15 environment at: /private/tmp/lab/aibdd/.venv
Using Python 3.12.15 environment at: /private/tmp/lab/aibdd/ch00-setup/uvdemo/.venv
error: No virtual environment found; run `uv venv` to create an environment, or pass `--system` to install into a non-virtual environment
```

第一行是好的一面：沒有自己 `.venv` 的目錄，uv 會一路往上找到共用 venv。第二行是壞的一面：中間只要有一層自己的 `.venv`，它就**遮住**了共用 venv，在底下裝的東西會進錯地方；寫這章時建的 `ch00-setup/.venv` 對 `smoke/` 就是這樣。第三行：`/tmp` 往上沒有任何 venv，uv 拒絕安裝、exit 2。明寫 `--python`，安裝目標就不靠目錄位置決定。另外，重跑 `uv venv` 不會默默蓋掉既有的 venv：uv 0.13.1 回 `error: Failed to create virtual environment`、exit 2，並提示 ``Use the `--clear` flag``。重讀本章不會毀掉你的環境，真要重建再加 `--clear`。

## 三、Playwright：瀏覽器不在 venv 裡

`playwright` 套件只是 Python 端，瀏覽器要另外裝：

```bash
source /tmp/lab/aibdd/.venv/bin/activate
python -m playwright install chromium; echo "exit=$?"
python -m playwright install --dry-run chromium | grep -E "^[A-Z]|Install location"
```

```
exit=0
Chrome for Testing 156.0.8078.4 (playwright chromium v1248)
  Install location:    /Users/robertliao/Library/Caches/ms-playwright/chromium-1248
FFmpeg (playwright ffmpeg v1013)
  Install location:    /Users/robertliao/Library/Caches/ms-playwright/ffmpeg-1013
Chrome Headless Shell 156.0.8078.4 (playwright chromium-headless-shell v1248)
  Install location:    /Users/robertliao/Library/Caches/ms-playwright/chromium_headless_shell-1248
```

瀏覽器已經在了，所以 `install chromium` 什麼都沒印、0.15 秒結束（第一次會下載）。`--dry-run` 告訴我們三件事：瀏覽器在 `~/Library/Caches/ms-playwright/`，**不在 venv 裡**，刪 venv 不會刪它，新 venv 也不會重新下載；`v1248` 這個 build 是 Playwright 1.64.1 寫死的（它的 `driver/package/browsers.json` 記著 `chromium 1248 156.0.8078.4`），換 Playwright 版本就會去找另一個 build；Chromium 有兩份，完整版給有視窗（headed）模式，Chrome Headless Shell 給無頭（headless）模式，我們的測試預設 headless，實際啟動的是後者。這台機器上三者共佔約 572MB（`du -sh`：366M、203M、2.9M）。

## 四、Spec Kit 1.2.5：把版本寫在指令裡

SDD 課用 `uv tool install specify-cli --from git+...@v0.11.10` 把 `specify` 永久裝到 PATH 上。本課**不這樣做**：PATH 上只能有一個 `specify`，裝了 1.2.5，SDD 課的 0.11.10 指令就跑不了，反之亦然。我們改用 uvx（`uv tool run` 的簡寫），每次執行時在 uv 的快取裡組一個暫時環境，版本寫在指令裡：

```bash
uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify --version
```

```
specify 1.2.5
```

快取熱的時候一次約 1.7 到 2.2 秒（每次仍會向 GitHub 確認 tag）。指令太長，實驗室放一個兩行的 wrapper；後面章節的正文都寫出 uvx 完整形式（有幾章把它包成章內定義的 shell 函式 `specify`），不依賴這個 wrapper，它只是你自己打字的捷徑：

```bash
mkdir -p /tmp/lab/bin
printf '#!/bin/sh\nexec uvx --from git+https://github.com/github/spec-kit.git@v1.2.5 specify "$@"\n' \
  > /tmp/lab/bin/specify125
chmod +x /tmp/lab/bin/specify125
/tmp/lab/bin/specify125 --version
```

```
specify 1.2.5
```

（這台機器的 `/tmp/lab/bin/specify125` 在寫本章前就有了，所以我們把同一段指令寫到另一個目錄執行，再用 `diff` 確認兩份逐位元組相同。想直接打 `specify125`，把 `/tmp/lab/bin` 加進 `PATH`。）`specify version` 的資訊框裡有一行值得注意：`Python    3.15.0`。Spec Kit 跑的不是我們 venv 的 3.12，而是 uvx 自己挑的、符合它 `requires-python = ">=3.11"` 的直譯器，和你 activate 了哪個 venv 無關。這正是我們要的隔離：Spec Kit 的依賴永遠不會汙染課程 venv。`specify check` 在這台機器上列出 `● Claude Code (available)` 與 `Specify CLI is ready to use!`。`specify init` 與 1.2.5 的目錄結構留給 [Ch 12](./12-spec-kit-1x.md)。

## 五、Claude Code 2.1.295 與 skill-creator plugin

```bash
claude --version
claude auth status | python3 -c 'import json,sys; d=json.load(sys.stdin); print("loggedIn=%s authMethod=%s" % (d["loggedIn"], d["authMethod"]))'
claude doctor
```

```
2.1.295 (Claude Code)
loggedIn=True authMethod=claude.ai
…（略：doctor 的標題）
Running: native (2.1.295)
…（略）
Auto-updates: enabled
Auto-update channel: latest
…（略）
No installation issues found.
```

（`auth status` 預設印整份 JSON，含 email 與組織 id，我們只挑兩個欄位。）`Auto-updates: enabled` 就是版本漂移的來源。官方 [Advanced setup](https://code.claude.com/docs/en/setup) 文件的關法是在 settings 的 `env` 設 `DISABLE_AUTOUPDATER=1`，再用 `claude doctor` 確認；我們只用環境變數驗證效果，沒改這台機器的設定檔：`DISABLE_AUTOUPDATER=1 claude doctor | grep "Auto-updates"` 印出 `Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)`。要裝回指定版本，同一份文件的寫法是 `curl -fsSL https://claude.ai/install.sh | bash -s <版本號>`（文件的例子是 2.1.89，換成 2.1.295 就是本課的版本），**本章未實測**（它會改動這台機器正在用的 Claude Code），請以文件與你的環境為準。

確認登入、模型與旗標都能用，最便宜的方法是一次不給任何工具的 headless 呼叫：

```bash
cd /tmp/lab/aibdd/ch00-setup
claude -p "Reply with exactly: OK" --max-turns 1 --tools "" --no-session-persistence \
  --output-format json > claude-ok.json
python3 -c "import json; d=json.load(open('claude-ok.json')); print({k: d[k] for k in ['subtype','result','num_turns','duration_ms','total_cost_usd','permission_denials']})"
```

```
{'subtype': 'success', 'result': 'OK', 'num_turns': 1, 'duration_ms': 674, 'total_cost_usd': 0.018043, 'permission_denials': []}
```

（實際執行紀錄，2026-11-02，每次執行結果不同。`total_cost_usd` 是 CLI 端的估計，這次用帳號預設模型，換模型或快取狀態不同，數字就不同。）這三個旗標是本課所有 `claude -p` 實跑的固定配備：

- `--max-turns`：迴圈上限。它在 2.1.295 的 `claude --help` 裡**看不到**，但 [CLI reference](https://code.claude.com/docs/en/cli-reference) 有列，這次執行也證明它被接受。
- `--tools`：決定有哪些內建工具可用，`""` 是全關。別拿 `--allowedTools` 代替它：那只讓列出的工具免詢問，其他工具照樣在（CLI reference：「To restrict which tools are available, use `--tools` instead」）。
- `--no-session-persistence`：不把這次對話存進你的 session 清單。

在腳本裡跑、stdin 不是終端機時，尾端再加 `< /dev/null`，否則 CLI 會先空等 3 秒，stderr 出現 `Warning: no stdin data received in 3s, proceeding without it.`（同一天其他章的實跑紀錄裡就有這行）。

**skill-creator**（Ch 5 要用）是官方 marketplace（plugin 的上架目錄）`claude-plugins-official` 裡的 plugin。照 pstack Ch 0 的做法，先在隔離的設定目錄試裝，不碰真正的 `~/.claude`：

```bash
export CLAUDE_CONFIG_DIR=/tmp/lab/aibdd/ch00-setup/cc-config && mkdir -p "$CLAUDE_CONFIG_DIR"
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin install skill-creator@claude-plugins-official
claude plugin details skill-creator@claude-plugins-official
```

```
…（略）
✔ Successfully added marketplace: claude-plugins-official (declared in user settings)
Installing plugin "skill-creator@claude-plugins-official"...✔ Successfully installed plugin: skill-creator@claude-plugins-official (scope: user)
…（略）
  Skills (1)  skill-creator
…（略：Agents、Hooks、MCP 與 LSP servers 都是 0）
  Always-on:   ~87 tok   added to every session
…（略）
  skill-creator        ~90      ~8.2k
```

`installed_plugins.json` 記下的版本是 `a7ba5b93db0c`（commit `a7ba5b93db0c7c8d…`），Ch 5 引用的檔案以這個 commit 為準；常駐約 87 tokens，叫用時再加約 8.2k。試完先 `unset CLAUDE_CONFIG_DIR`：`export` 會一直留在這個 shell 裡，之後打的每個 `claude` 都去讀試裝目錄，而新的設定目錄沒有登入。把它指到另一個空目錄、用前面同一行過濾跑 `claude auth status`，得到 `loggedIn=False authMethod=none`（`claude` 本身 exit 1）；`unset` 之後再問，回到 `loggedIn=True`。試裝目錄本身用完 `rm -rf` 即可。正式裝進日常環境，依官方 [Install and manage plugins](https://code.claude.com/docs/en/discover-plugins)：session 裡打 `/plugin install skill-creator@claude-plugins-official`（它會打開 `/plugin` 面板讓你檢視內容、選 scope，不會直接裝），或 shell 裡 `claude plugin install skill-creator@claude-plugins-official`。官方 marketplace 在第一次開互動式 session 時會自動登記，從沒開過的新機器才要先 `marketplace add`。**正式安裝這一步本章沒有在真正的 `~/.claude` 實測**，隔離目錄的結果就是它會做的事。

## 六、冒煙測試：一份 feature、兩個 driver

最後要證明「這些東西接得起來」。我們用一個極小的會議室預約 spike（為了驗證可行性而寫的拋棄式程式）：一份 feature、兩種 driver。`--driver api` 用 httpx 打 HTTP API，`--driver ui` 用 Playwright 開瀏覽器填表單，兩者跑**同一份 Gherkin**。現在照抄，[Ch 30](./30-one-gherkin-two-drivers.md) 會按層拆解這個設計，再把它改寫成四層的版本。每個檔案的第一行是標註檔名的註解（Gherkin 也接受 `#` 開頭的整行註解），下面的結果就是帶著這些註解行跑的。

<details>
<summary>五個檔案的完整內容（放在 <code>/tmp/lab/aibdd/ch00-setup/smoke/</code>）</summary>

```python
# smoke/app.py
from datetime import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI()
BOOKINGS: list[dict] = []

class Booking(BaseModel):
    room: str
    start: time
    end: time
    by: str

@app.post("/bookings", status_code=201)
def create(b: Booking):
    if b.end <= b.start:
        raise HTTPException(422, "end must be after start")
    for x in BOOKINGS:
        if x["room"] == b.room and b.start < x["end"] and x["start"] < b.end:
            raise HTTPException(409, f"{b.room} is taken {x['start']:%H:%M}-{x['end']:%H:%M} by {x['by']}")
    BOOKINGS.append(b.model_dump())
    return b

@app.get("/bookings")
def list_bookings():
    return BOOKINGS

@app.post("/reset")
def reset():
    BOOKINGS.clear()

@app.get("/", response_class=HTMLResponse)
def index():
    return """<form id=f><input name=room aria-label=Room><input name=start aria-label=Start>
<input name=end aria-label=End><input name=by aria-label=Name><button>Book</button></form>
<p role=status></p>
<script>f.onsubmit=async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(f));
const r=await fetch('/bookings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(d)});
const j=await r.json();document.querySelector('[role=status]').textContent=r.ok?'Booked '+j.room:'Rejected: '+j.detail}</script>"""
```

```gherkin
# smoke/features/booking.feature
Feature: Room booking

  Rule: A room cannot be booked twice for overlapping times

    Scenario: Booking a free slot
      Given room "Taipei" is free all day
      When Alice books "Taipei" from 10:00 to 11:00
      Then the booking is confirmed

    Scenario: Booking an overlapping slot
      Given Bob has booked "Taipei" from 10:00 to 11:00
      When Alice books "Taipei" from 10:30 to 11:30
      Then the booking is rejected because "Taipei" is taken 10:00-11:00 by Bob
```

```python
# smoke/tests/conftest.py
import socket, threading, time
import pytest, uvicorn
from playwright.sync_api import sync_playwright
from app import app
from drivers import ApiDriver, UiDriver

@pytest.fixture(scope="session")
def base_url():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started: time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True

@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(); yield b; b.close()

def pytest_addoption(parser):
    parser.addoption("--driver", choices=["api", "ui"], default="api")

@pytest.fixture
def driver(request, base_url):
    if request.config.getoption("--driver") == "api":
        d = ApiDriver(base_url)
    else:
        d = UiDriver(base_url, request.getfixturevalue("browser").new_page())
    d.reset(); return d
```

```python
# smoke/tests/drivers.py
import httpx

class ApiDriver:
    def __init__(self, base): self.http = httpx.Client(base_url=base)
    def reset(self): self.http.post("/reset")
    def book(self, room, start, end, by):
        r = self.http.post("/bookings", json={"room": room, "start": start, "end": end, "by": by})
        return ("confirmed", None) if r.status_code == 201 else ("rejected", r.json()["detail"])

class UiDriver:
    def __init__(self, base, page): self.base, self.page = base, page
    def reset(self): httpx.post(self.base + "/reset")
    def book(self, room, start, end, by):
        p = self.page
        p.goto(self.base + "/")
        for label, value in [("Room", room), ("Start", start), ("End", end), ("Name", by)]:
            p.get_by_label(label).fill(value)
        p.get_by_role("button", name="Book").click()
        status = p.get_by_role("status")
        status.filter(has_text="Booked").or_(status.filter(has_text="Rejected")).wait_for()
        text = status.inner_text()
        return ("confirmed", None) if text.startswith("Booked") else ("rejected", text.removeprefix("Rejected: "))
```

```python
# smoke/tests/test_booking.py
from pytest_bdd import scenarios, given, when, then, parsers

scenarios("../features/booking.feature")

@given(parsers.parse('room "{room}" is free all day'))
def free(room): pass

@given(parsers.parse('{who} has booked "{room}" from {start} to {end}'))
def booked(driver, who, room, start, end):
    assert driver.book(room, start, end, who)[0] == "confirmed"

@when(parsers.parse('{who} books "{room}" from {start} to {end}'), target_fixture="result")
def book(driver, who, room, start, end):
    return driver.book(room, start, end, who)

@then("the booking is confirmed")
def confirmed(result): assert result == ("confirmed", None)

@then(parsers.parse('the booking is rejected because "{room}" is taken {slot} by {who}'))
def rejected(result, room, slot, who):
    assert result == ("rejected", f"{room} is taken {slot} by {who}")
```

</details>

```bash
cd /tmp/lab/aibdd/ch00-setup/smoke && source /tmp/lab/aibdd/.venv/bin/activate
PYTHONPATH=.:tests pytest -q tests --driver api
PYTHONPATH=.:tests pytest -q tests --driver ui
```

```
..                                                                       [100%]
2 passed in 0.08s
..                                                                       [100%]
2 passed in 0.95s
```

兩個綠燈證明的東西不一樣。`--driver api` 證明 Python 3.12、pytest-bdd 解析 Gherkin、FastAPI、uvicorn 起得來、httpx 打得到；它**沒證明**瀏覽器存在。`--driver ui` 再加上 Playwright driver、Chromium headless 啟動與 DOM 互動。兩者都不涉及 Claude Code 與 Spec Kit，那是 `check_env.py` 的工作。

## 七、check_env.py：把「環境是好的」變成一個指令

冒煙測試證明「接得起來」，但不告訴你**哪個版本**在跑。`check_env.py` 逐一比對釘住的版本、真的啟動一次 Chromium、問 Claude Code 與 Spec Kit 的版本，最後一行是總結，有 FAIL 就 exit 1。和本課所有腳本一樣有 `--help`；除了它要檢查的 Playwright，只用標準函式庫。

```python
"""check_env.py: verify the ai_bdd course environment against its pinned versions.
Usage: python check_env.py [--offline]   (exit 1 if any check FAILs; WARN never fails)"""
from __future__ import annotations  # no effect yet; keeps future "x: str | None" hints loadable on 3.9
import argparse, importlib.metadata as md, shutil, subprocess, sys

PINS = {"pytest": "9.2.0", "pytest-bdd": "9.0.1", "gherkin-official": "42.0.1", "fastapi": "0.162.0",
        "starlette": "1.10.0", "pydantic": "2.14.2", "uvicorn": "0.54.0", "httpx": "0.28.1",
        "playwright": "1.64.1", "PyYAML": "6.0.3"}
SPECKIT = ["uvx", "--from", "git+https://github.com/github/spec-kit.git@v1.2.5", "specify", "--version"]
TOOLS = {"uv": (["uv", "--version"], "uv 0.13."), "claude": (["claude", "--version"], "2.1.295"),
         "specify": (SPECKIT, "specify 1.2.5")}

def package(name, want):
    try:
        got = md.version(name)
    except md.PackageNotFoundError:
        return "FAIL", name, "missing", "want " + want
    return ("ok", name, got, "") if got == want else ("FAIL", name, got, "want " + want)

def chromium():
    try:  # really launch it: a binary on disk can still fail to start
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); version = b.version; b.close()
        return "ok", "chromium", version, "headless launch works"
    except Exception as e:  # missing module, missing browser, sandbox problems
        return "FAIL", "chromium", "unusable", (str(e).strip().splitlines() or [type(e).__name__])[0][:110]

def tool(name, cmd, expect):
    if shutil.which(cmd[0]) is None:
        return "FAIL", name, "not on PATH", "install it, then open a new shell"
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip().splitlines()
    line = out[-1] if out else ""
    return ("ok", name, line, "") if expect in line else ("WARN", name, line, "course ran with " + expect)

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="skip the Spec Kit check (it needs the network)")
    args = ap.parse_args()
    py = "%d.%d.%d" % sys.version_info[:3]
    results = [("ok", "python", py, sys.prefix) if sys.version_info[:2] == (3, 12)
               else ("FAIL", "python", py, "want 3.12.x; activate the course venv")]
    results += [package(n, v) for n, v in PINS.items()] + [chromium()]
    results += [tool(n, c, e) for n, (c, e) in TOOLS.items() if not (args.offline and n == "specify")]
    for r in results:
        print(("%-5s %-17s %-14s %s" % r).rstrip())
    fails, warns = (sum(r[0] == s for r in results) for s in ("FAIL", "WARN"))
    print("%d checks, %d problems, %d warnings" % (len(results), fails, warns))
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
```

```bash
cd /tmp/lab/aibdd/ch00-setup && source /tmp/lab/aibdd/.venv/bin/activate && python check_env.py; echo "exit=$?"
```

```
ok    python            3.12.15        /private/tmp/lab/aibdd/.venv
ok    pytest            9.2.0
ok    pytest-bdd        9.0.1
…（略：另外 8 個套件都是 ok）
ok    chromium          156.0.8078.4   headless launch works
ok    uv                uv 0.13.1 (Homebrew 2026-10-14 aarch64-apple-darwin)
ok    claude            2.1.295 (Claude Code)
ok    specify           specify 1.2.5
15 checks, 0 problems, 0 warnings
exit=0
```

三個設計決定。**套件不符是 FAIL，外部工具不符只是 WARN**：venv 完全在我們掌控之下，裝錯就是錯；Claude Code 會自己更新，`2.1.300` 不代表你做錯了什麼，只代表你看到的輸出可能和書上不同。**Chromium 是真的啟動一次**，不是檢查檔案在不在：檔案在、但權限或依賴壞掉的情況，只有真的 launch 才抓得到。**故意保持舊版 Python 也能執行的語法**（不用 `match`，也不用 3.10 才有的 `str | None` 型別寫法）：最常見的錯誤就是用錯 Python，這時腳本要能好好報告「你用錯了」，而不是自己先噴 `SyntaxError`。開頭的 `from __future__ import annotations` 目前沒有作用（檔案裡沒有型別註記，拿掉它用 3.9.6 跑結果一樣），是替日後加註記的人留的保險：沒有它，3.9 一讀到 `def f(x: str | None)` 就是 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`。失敗的樣子都在踩雷集錦，每一條都是這支腳本或冒煙測試真的抓到的。

## 底層機制：一行 `python -m pytest` 背後的四次查找

```
 python -m pytest tests --driver ui
   ├─(1) shell 查 PATH ─▶ .venv/bin/python（activate 把 .venv/bin 放到 PATH 最前面）
   │                        └─ symlink ─▶ uv 管理的 CPython 3.12.15
   ├─(2) Python 啟動讀 .venv/pyvenv.cfg ─▶ sys.prefix = .venv，只看 .venv 的 site-packages
   ├─(3) pytest 讀 entry points（pytest11）─▶ 載入 pytest_bdd.plugin；再載入 tests/conftest.py
   └─(4) Playwright 啟動 driver/node package/cli.js run-driver
          └─ 依 browsers.json 找 chromium_headless_shell-1248
             └─ 先看 PLAYWRIGHT_BROWSERS_PATH，沒設就看 ~/Library/Caches/ms-playwright
```

**(1)(2) venv 不是複製一份 Python**。`ls -l /tmp/lab/aibdd/.venv/bin/python` 顯示它是指向 `~/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12` 的 symlink；「這是哪個環境」由旁邊的 `pyvenv.cfg` 決定，裡面有 `home = ...`、`version_info = 3.12`、`include-system-site-packages = false`。Python 啟動時看到這個檔案，就把 `sys.prefix` 設成 venv、只從 venv 的 `site-packages` 匯入。所以「沒 activate」的後果不只是少了套件：你根本在跑另一個直譯器（這台是 `/usr/bin/python3` 3.9.6）。

**(3) pytest-bdd 是被自動載入的**。它的 `entry_points.txt` 有一段 `[pytest11]`、內容是 `pytest-bdd = pytest_bdd.plugin`；pytest 啟動時載入所有這類 plugin，`pytest -v` 的標頭因此出現 `plugins: bdd-9.0.1, anyio-4.16.0`。而 `--driver` 不是 pytest 內建的選項，是 `tests/conftest.py` 的 `pytest_addoption` 加的，pytest 必須在啟動時就載入那份 conftest 才認得它（踩雷第 5 條）。

**(4) Playwright for Python 是一層 client**。真正驅動瀏覽器的是 wheel 裡附的 Node.js：`playwright/_impl/_driver.py` 算出 `driver/node` 與 `driver/package/cli.js` 的路徑，`_transport.py` 用 `run-driver` 把它啟動成子程序，Python 端透過 pipe 和它溝通。這台機器上附的 Node 是 v24.22.1、佔 128M，所以本課的 Python 程式碼不需要你另外裝 Node。

## 對比與取捨

| 選擇 | 本課的做法 | 替代方案 | 為什麼這樣選 |
|---|---|---|---|
| 建環境 | `uv venv` + `uv pip install` 寫死 7 個版本 | `python -m venv` + pip；Poetry 或 `uv add` 管一個 pyproject 專案 | 實驗室不是一個套件，而是三十幾個獨立目錄；一個共用 venv 加精確版本最直接。pip 也做得到，只是 Python 本身要另外裝 |
| Spec Kit | `uvx --from ...@v1.2.5` 每次指定 | `uv tool install`（SDD 課的做法） | PATH 上只能有一個 `specify`；uvx 讓 0.11.10 與 1.2.5 共存。代價是每次約 2 秒、要連網（`--offline` 直接失敗） |
| 瀏覽器 | Playwright 預設的使用者快取 | `PLAYWRIGHT_BROWSERS_PATH` 指到專案內 | 共用快取約 572MB 只存一份；指到專案內比較隔離，但每個專案各下載一份 |
| Claude Code 版本 | 記錄 2.1.295，不符只給 WARN | 關自動更新並裝回指定版本 | 凍結版本會錯過修正；本課選擇「知道差異」而不是「凍結」 |
| plugin | 先在 `CLAUDE_CONFIG_DIR` 隔離目錄試裝 | 直接裝進 `~/.claude` | 先看 `details` 的常駐成本與元件，代價只是一個暫存目錄 |

## 踩雷集錦

1. **以為 zsh 和 bash 一樣處理 `=` 與 `>`**：zsh 把以 `=` 開頭的字當成「找這個指令的路徑」（這台機器上 `echo =ls` 印 `/bin/ls`），所以 `zsh -f -c 'echo ===='` 得到 `zsh:1: === not found`。更隱蔽的是沒加引號的版本範圍：`uv pip install --dry-run ... pytest>=9.2` 的 `>` 是重新導向，目標 `=9.2` 又被 zsh 當指令去找，結果 `zsh:1: 9.2 not found`、exit 1，什麼都沒裝。同一行交給 `bash -c` 則**默默成功**：exit 0，uv 收到的參數只剩 `pytest`（版本限制被 shell 吃掉了；因為是 `--dry-run` 且已經裝過，它回 `Would make no changes`），目錄裡多了一個叫 `=9.2` 的空檔案。同一類問題還有萬用字元：`zsh -f -c 'grep -rn mako --include=*.py .'` 因為 `*` 找不到符合的檔名而報 `zsh:1: no matches found: --include=*.py`。正確認識：版本字串、萬用字元都加引號；zsh 的報錯其實是比較友善的結果。
2. **以為打開終端機，`python3` 就是課程的 Python**：沒 activate 的新 shell 裡跑 `python3 check_env.py --offline`，跑的是系統的 `/usr/bin/python3`，第一行就是 `FAIL  python  3.9.6  want 3.12.x; activate the course venv`，接著 10 個套件全是 `missing`、`FAIL chromium unusable No module named 'playwright'`，總結 `14 checks, 12 problems, 0 warnings`、exit 1（uv 與 claude 兩行仍是 ok，它們不靠 venv）。正確認識：每個新終端機都要 `source /tmp/lab/aibdd/.venv/bin/activate`，不確定就 `which python`。Ch 3 的 hook 會再遇到同一個問題，因為 hook 繼承的是 Claude Code 的環境，不是你某個終端機的。
3. **API 綠燈就以為 UI 沒問題**：`export PLAYWRIGHT_BROWSERS_PATH=/tmp/lab/aibdd/ch00-setup/no-browsers`（一個空目錄，模擬「升級 Playwright 卻沒重裝瀏覽器」），`--driver api` 照樣 `2 passed`，`--driver ui` 則是 `2 failed in 0.33s`，原因是 `BrowserType.launch: Executable doesn't exist at /tmp/lab/aibdd/ch00-setup/no-browsers/chromium_headless_shell-1248/...`，後面接一個建議你跑 `playwright install` 的框。`check_env.py --offline` 在同樣情況下報 `FAIL chromium unusable`、`14 checks, 1 problems`。試完記得 `unset PLAYWRIGHT_BROWSERS_PATH`。正確認識：每個 driver 只證明它走過的那一段。
4. **以為 port 8000 一定是空的**：在這台機器上直接 `uvicorn app:app`，得到 `ERROR:    [Errno 48] error while attempting to bind on address ('127.0.0.1', 8000): [errno 48] address already in use`、exit 3。`lsof -nP -iTCP:8000 -sTCP:LISTEN` 一查，是同一位使用者另一個專案的 Python 程序，已經跑了好幾天。正確認識：測試不要用固定 port；冒煙測試的 `base_url` 先 `bind(("127.0.0.1", 0))` 讓作業系統挑一個空的 port，再交給 uvicorn。
5. **以為 `--driver` 是 pytest 的選項，在哪裡跑都一樣**：在 `smoke/` 外面跑 `pytest -q --driver api`，pytest 沒在啟動時載入 `tests/conftest.py`，於是 `pytest: error: unrecognized arguments: --driver`；在 `smoke/` 裡但忘了 `PYTHONPATH=.:tests`，則是 `ModuleNotFoundError: No module named 'app'`。兩者都是 exit 4。（改用 `python -m pytest` 不會撞到第二個錯，因為 `-m` 會把目前目錄放進 `sys.path`，實測 `2 passed`；本課統一寫 `PYTHONPATH=.:tests pytest`，讓匯入路徑不取決於你怎麼啟動 pytest。）正確認識：看到 `unrecognized arguments` 先懷疑「conftest 沒被載入」，不是「pytest 版本不對」。
6. **以為 uvx 報錯的第一行就是原因**：把 tag 打成不存在的 `@v1.2.55`，uvx 的錯誤一層層包起來（`Failed to resolve ...`、`Git operation failed`），真正的原因在最後一行 `fatal: couldn't find remote ref refs/tags/v1.2.55`。正確認識：uvx 的錯誤從最後一個 `cause` 往上讀。
7. **以為貼進終端機的 `# 註解` 會被忽略**：本課不少指令區塊在行尾加註解，例如 [Ch 1](./01-course-map.md) 的 `PYTHONPATH=.:tests pytest -q tests --driver api   # 2 passed in 0.11s`。bash 的互動模式預設認得 `#` 註解，zsh 的 `interactivecomments` 卻預設關閉：在一個只讀 macOS `/etc/zshrc` 的互動式 zsh 5.9 裡，對冒煙測試的副本貼上這行，`#` 和後面的字全成了 pytest 的參數，得到 `ERROR: file or directory not found: #`、exit 4。`zsh -c` 與腳本檔不受這個選項影響，所以本課的實跑看不到它。正確認識：在互動式 zsh 貼指令前先 `setopt interactivecomments`（寫進 `~/.zshrc` 就一直有效），同一行就是 `2 passed`。

## 進階：再往深一層

- **鎖住整個依賴閉包**：寫死的只有 7 個直接依賴。`uv pip freeze --python /tmp/lab/aibdd/.venv/bin/python > requirements.lock` 得到 33 行；拿它對另一個 venv 跑 `uv pip install --dry-run -r requirements.lock`，回報 `Checked 33 packages in 15ms` 與 `Would make no changes`，證明兩個環境一致。注意狀態訊息走 stderr，只能重新導向 stdout：我們第一次寫成 `> requirements.lock 2>&1`，`Using Python ...` 那行混進 lock 檔，安裝時在第一行就解析失敗：``Couldn't parse requirement in `bad.lock` at position 0``，cause 是 ``Expected one of `@`, `(`, `<`, `=`, `>`, `~`, `!`, `;`, found `P` ``（`Using` 被當成套件名，接著的 `P` 不是版本運算子）。
- **port 0 的競態**：冒煙測試是「bind port 0、讀號碼、關 socket、再讓 uvicorn 綁同一個號碼」，關掉和重綁之間理論上別的程序能搶走這個 port（推論，本章沒有重現）。uvicorn 0.54.0 本身接受 `--port 0` 並印出實際的 port（實測 `Uvicorn running on http://127.0.0.1:61839`），更嚴謹的做法是讓 uvicorn 自己綁 0，再從它的 socket 讀號碼。
- **用乾淨的 Claude 設定做實驗**：後面章節比較「有 hook / 沒 hook」時，`--setting-sources project,local` 能確保你個人的 `~/.claude/settings.json`（這台機器上它就覆寫了 `PATH`）不混進實驗，登入狀態不受影響。換一個 `CLAUDE_CONFIG_DIR` 隔離得更徹底，但如第五節所見，新目錄要先登入一次。

## 動手練習

1. 從空目錄照本章重建實驗室，跑 `check_env.py`，抄下最後一行。有 WARN 的話，寫下差了哪個版本、你預期它影響哪幾章。
2. 照第二節的兩個指令建一個暫時 venv：兩處 `/tmp/lab/aibdd/.venv` 都換成 `/tmp/lab/aibdd/ch00-setup/tmpvenv/.venv`（漏換第二處，降版的就是共用 venv），`"httpx==0.28.1"` 換成 `"httpx==0.27.2"`，其餘照抄。用那個 venv 的 Python 跑 `check_env.py --offline`，確認只有一行 FAIL，而且那行告訴你該裝哪個版本。（只裝 httpx 一個套件的話，其他 9 個套件與 chromium 會一起 FAIL，我們實測是 `14 checks, 11 problems`。）
3. 在 `fake-bin/` 放一個會印 `2.1.300 (Claude Code)` 的假 `claude`，放到 `PATH` 最前面再跑一次，你應該看到 WARN、exit 0。然後替它加一個 `--json` 旗標，每個檢查印一行 JSON：Ch 10 會解釋為什麼 agent 用得到這種輸出。
4. 把 conftest 的 `p.chromium.launch()` 改成 `p.chromium.launch(headless=False, slow_mo=500)` 跑 `--driver ui`，看瀏覽器自己填表單。這時啟動的是 `chromium-1248` 還是 `chromium_headless_shell-1248`？

## 本章重點整理

- 實驗室是三個世界：uv 建的 venv（Python 套件全部寫死）、uvx 的暫時環境（Spec Kit 1.2.5，版本寫在指令裡）、Claude Code（原生執行檔，預設自動更新）；Chromium 在 venv 外面的使用者快取
- venv 的身分由 `pyvenv.cfg` 決定；沒 activate 時你跑的是另一個直譯器，不只是少了套件
- `uv pip install` 會往父目錄找 `.venv`，明寫 `--python` 讓安裝目標不靠目錄位置；每個需求字串都加引號
- 冒煙測試的兩個 driver 證明不同的東西；`check_env.py` 把「版本對不對」變成一條 pass/fail 的指令
- `claude -p` 實跑一律帶 `--max-turns`、用 `--tools` 限制工具（`--allowedTools` 只管免詢問）、`--no-session-persistence`；試裝 plugin 用的 `CLAUDE_CONFIG_DIR` 用完要 `unset`

## 自我檢核

- [ ] 不看本章，畫出三個世界各自的位置，並說出 Chromium 為什麼不在 venv 裡
- [ ] 解釋 `uv pip install pytest>=9.2` 在 zsh 與 bash 下各發生什麼，哪一種比較危險
- [ ] 為什麼本課用 uvx 而不是 SDD 課的 `uv tool install` 跑 Spec Kit？說出一個會衝突的具體情境
- [ ] `--driver api` 綠、`--driver ui` 紅，你第一個懷疑什麼？用哪個指令確認？
- [ ] `check_env.py` 為什麼把 Claude Code 版本不符當 WARN、把 pytest 版本不符當 FAIL？你同意嗎？

## 延伸閱讀

- **[uv — Using Python environments](https://docs.astral.sh/uv/pip/environments/)**（Astral，官方文件）
  - **讀哪裡**：「Discovery of Python environments」一節
  - **能學到什麼**：`uv pip` 找環境的完整順序，也就是本章「為什麼寫 `--python`」的依據
  - **前提**：知道 venv 是什麼即可
- **[Playwright for Python — Browsers](https://playwright.dev/python/docs/browsers)**（Microsoft，官方文件）
  - **讀哪裡**：「Install browsers」「Chromium: headless shell」與「Managing browser binaries」
  - **能學到什麼**：預設安裝位置、`PLAYWRIGHT_BROWSERS_PATH`、「每個 Playwright 版本需要特定版本的瀏覽器」，以及 headless shell 與完整 Chromium 的分工；踩雷第 3 條的成因，Ch 23 前值得先讀
- **[pytest-bdd 文件](https://pytest-bdd.readthedocs.io/en/stable/)**（pytest-dev，9.0.1）
  - **讀哪裡**：「Step arguments」「Override fixtures via given steps」「Scenarios shortcut」三節
  - **能學到什麼**：冒煙測試裡 `parsers.parse`、`target_fixture`、`scenarios()` 的正式定義；Ch 26–27 會把這三節用到底
  - **前提**：會寫一般的 pytest fixture
- **[Claude Code — Advanced setup](https://code.claude.com/docs/en/setup)** 與 **[Install and manage plugins](https://code.claude.com/docs/en/discover-plugins)**（Anthropic，官方文件）
  - **讀哪裡**：Advanced setup 的「Install a specific version」「Disable auto-updates」；Install and manage plugins 的「Install a plugin」「Install from your shell」
  - **能學到什麼**：版本釘選、`DISABLE_AUTOUPDATER`、`/plugin install` 與 `claude plugin install` 的差別
  - **注意**：文件跟著最新版走，沒標版本的段落寫的就是最新版的行為；2026-11-02 同一套文件的 hooks 頁已經寫到 v2.1.304、v2.1.309 才有的行為，細節以你版本的 `--help` 為準
- **[Spec Kit — Installation](https://github.github.io/spec-kit/installation.html)** 與 **[One-time Usage (uvx)](https://github.github.io/spec-kit/install/one-time.html)**（GitHub，官方文件）
  - **讀哪裡**：Installation 的「Install from Source — Persistent Installation (Recommended)」「Install from PyPI」；One-time Usage 的「When to use persistent installation instead」
  - **能學到什麼**：持久安裝（SDD 課的做法）與一次性 uvx（本課的做法）的官方說法，以及官方列出的持久安裝好處（留在 PATH、不必每次重新下載），也就是本課為共存付出的代價；PyPI 那條路一樣把 `specify` 裝上 PATH。搭配 [SDD Ch 27](../spec_driven_development/27-spec-kit-install.md) 讀更快

實驗室搭好了。動手之前，先看一眼整門課要走的路線：從「agent 怎麼寫程式」走到「一份 Gherkin 同時驅動 API 與 UI」，每一段補的是 repo 裡哪一塊空白。

→ [Ch 1 全貌：從 Agent Coding 到可執行規格的五段路線](./01-course-map.md)
