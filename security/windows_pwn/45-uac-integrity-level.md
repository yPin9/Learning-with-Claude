# Ch 45 — UAC 與 integrity level 繞過概觀

> **目標**：理解 UAC（User Account Control）的完整機制——為什麼 admin 帳號平時跑 Medium IL、提權時怎麼走、哪些程式被設計成可以自動提權。從防禦和研究視角理解 UAC bypass 的共同模式，知道為什麼微軟官方說 UAC「不是安全邊界」。對照 Linux 的 sudo/setuid 說出差異。

## 為什麼需要這個？

Ch 44 結尾埋了一個問題：admin 帳號登入後，token 裡的 Administrators SID 被標成 `deny-only`，IL 是 Medium——這是正常的。那要怎麼拿到 High IL 的完整 admin token？

答案是 UAC 提權流程，也就是那個彈出「您要允許此應用程式對裝置進行變更嗎？」的 consent 視窗。UAC 是 Windows Vista 引入的機制，目的是讓 admin 帳號**平時跑在 Medium IL 的受限狀態**，需要高權限時才臨時提升，避免每個行程都帶著 SYSTEM 等級的特權暴露在惡意程式面前。

**為什麼攻擊者需要了解 UAC？**

很多提權情境是：你已經以 admin 身份在目標機器執行程式碼（例如社交工程讓使用者執行了你的 payload），但行程在 Medium IL——你需要 High IL 才能做後續操作（關防毒、改系統設定、持久化）。UAC bypass 就是在使用者不察覺的情況下，讓行程從 Medium IL 跳到 High IL，**不觸發 consent 視窗**。

## 先建立直覺

```
  admin 使用者登入
         ↓
  LSASS 建立兩個 linked token
  ┌──────────────────────────────────────────┐
  │ Full Token（High IL）                     │  ← 鎖起來，平時不用
  │  User SID: S-1-5-21-xxx-500              │
  │  Administrators: SE_GROUP_ENABLED        │
  │  SeDebugPrivilege: 有                    │
  └──────────────────────────────────────────┘
  ┌──────────────────────────────────────────┐
  │ Filtered Token（Medium IL）              │  ← 平時用這個
  │  User SID: S-1-5-21-xxx-500             │
  │  Administrators: SE_GROUP_DENY_ONLY     │
  │  SeDebugPrivilege: 無                   │
  └──────────────────────────────────────────┘
         ↓
  Explorer 等行程用 Filtered Token 跑

  當使用者要求提權時（右鍵「以系統管理員執行」）：
         ↓
  consent.exe（System IL，SYSTEM SID）彈出 UAC 視窗
         ↓
  使用者點 Yes
         ↓
  以 Full Token（High IL）啟動目標行程
```

對照 Linux：

```
  一般使用者 → sudo 某指令 → pam 驗 /etc/sudoers → 以 root 執行
```

差異在：
- Linux sudo 需要**密碼驗證**（或 NOPASSWD 設定）
- Windows UAC 在 admin 帳號情境下只需要**點 Yes**（標準帳號才需要輸密碼）
- Linux 沒有「平時帶一半 token、需要時拿完整 token」的機制——你是 root 就是 root
- consent.exe 本身以 SYSTEM 跑，在 **Secure Desktop**（獨立桌面，防截圖/輸入劫持）上顯示

## Part 1：UAC 的完整機制

### Split Token（分裂 token）

Admin 帳號（不包含 Guest/Standard User）登入時，LSASS 建立兩個連結的 token：

**Filtered Token（Medium IL）**：
- Administrators 群組 SID 標成 `SE_GROUP_USE_FOR_DENY_ONLY`
- 移除危險 privileges（`SeDebugPrivilege`、`SeImpersonatePrivilege` 等）
- IL 設為 Medium（`S-1-16-8192`）

**Full Token（High IL）**：
- Administrators 群組完整 enabled
- 所有 admin privileges 完整
- IL 設為 High（`S-1-16-12288`）

兩個 token 用 `_TOKEN.TokenLinkedToken` 互相連結，可以用 `GetTokenInformation(TokenLinkedToken)` 查到另一個（但實際拿到的是無法使用的 identify-level 版本，不能直接拿來提權）。

### Auto-Elevate 可執行檔

並非所有程式都需要彈 UAC 視窗。Windows 內建許多「自動提升」的可執行檔，系統允許它們跳過 consent 視窗靜默拿 High IL。判斷條件：

```
自動提升的條件（全部滿足）：
  1. 有有效的程式碼簽章
  2. 簽章者是 Microsoft（在受信任的發行者清單）
  3. 可執行檔位於受保護的系統目錄
     （%SystemRoot%\System32\ 等 SYSTEM 才能寫的路徑）
  4. 資源區段裡的 manifest 有 <autoElevate>true</autoElevate>
```

查哪些執行檔有 autoElevate 的方法（實跑，Python 3 + ctypes）：

```python
# 用 Python xml 解析 PE 的 manifest resource，找 autoElevate=true 的執行檔
# 需要安裝：pip install pefile
# （此腳本僅做示範，掃描 System32 可能需要幾秒）
import os, pefile, xml.etree.ElementTree as ET

SYSTEM32 = r"C:\Windows\System32"
found = []

for fname in os.listdir(SYSTEM32):
    if not fname.lower().endswith(".exe"):
        continue
    path = os.path.join(SYSTEM32, fname)
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        for rsrc in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", []):
            for entry in rsrc.directory.entries:
                for res in entry.directory.entries:
                    data = pe.get_data(
                        res.data.struct.OffsetToData,
                        res.data.struct.Size
                    )
                    try:
                        text = data.decode("utf-16-le", errors="ignore")
                        if "autoElevate" in text.lower() and "true" in text.lower():
                            found.append(fname)
                    except Exception:
                        pass
    except Exception:
        pass

print(f"Found {len(found)} auto-elevate executables:")
for f in sorted(set(found)):
    print(f"  {f}")
```

常見的 auto-elevate 執行檔（以 Windows 11 為例，實際清單版本相依）：

```
fodhelper.exe       computerdefaults.exe    sdclt.exe
eventvwr.exe        wsreset.exe             dccw.exe
```

這些都是 UAC bypass 的長期攻擊目標，因為它們在高權限下執行但允許外部輸入影響行為。

### Consent 流程的角色分工

```
  使用者行程（Medium IL）
    呼叫 ShellExecute(..., "runas", ...)
         ↓
  appinfo.dll（AppInfo 服務，System IL）
    驗證目標是否符合自動提升條件
    若否 → 通知 consent.exe
         ↓
  consent.exe（System IL，Secure Desktop）
    顯示提升對話框
    使用者確認
         ↓
  AppInfo 服務以 Full Token 啟動目標行程
```

關鍵：整個提升動作發生在 AppInfo 服務（`appinfo.dll` in svchost）和 consent.exe，都是 SYSTEM 或更高。使用者的行程**從不直接觸摸 Full Token**——它只能請求，AppInfo 決定。

## Part 2：UAC Bypass 概觀（教育性 / 防禦視角）

### 微軟的官方立場

> UAC 不是安全邊界（security boundary）。——[Microsoft Security Servicing Commitments](https://www.microsoft.com/en-us/msrc/windows-security-servicing-criteria)

這話的意思：微軟不把 UAC bypass 當成安全漏洞修補（除非攻擊者能從標準使用者帳號提升，那才是真正的 EoP 漏洞）。在 admin 帳號下繞過 UAC consent 視窗，**不算是 Windows 安全漏洞**，最多算「行為問題」。

這不代表攻擊者不在乎——惡意程式非常喜歡用 UAC bypass 靜默拿 High IL，因為不跳出視窗就不會驚動使用者。

### 共同攻擊模式

UAC bypass 的路線很多，但共同本質是：**找一個自動提升的合法程式，讓它幫你跑你的 payload**。

#### 模式一：Registry Hijack（HKCU 劫持）

Auto-elevate 程式在 High IL 跑時，如果它讀取 registry 時**先查 HKCU（使用者 hive，Medium IL 行程可寫）再查 HKLM（系統 hive，需 admin）**，攻擊者可以在 HKCU 放假的值，讓高權限程式讀到攻擊者控制的內容。

```
典型路徑：
  1. 找到 fodhelper.exe 在高權限下查詢
     HKCU\Software\Classes\ms-settings\Shell\Open\Command
  2. Medium IL 行程寫入這個 key，放上 payload 路徑
  3. 觸發 fodhelper.exe（不需要 consent）
  4. fodhelper 在 High IL 下讀到 HKCU 的值，執行攻擊者 payload
```

**為什麼能成功**：Windows registry 的 HKCU 是 per-user 的，Medium IL 行程就能寫；但 HKCU 的 key 比 HKLM 有更高優先度——這是 Windows registry 的歷史設計，不是漏洞，只是被濫用。

**防禦偵測**：監控 `HKCU\Software\Classes\` 下的 `\Shell\Open\Command` 建立；特別是在 fodhelper.exe/computerdefaults.exe 等自動提升程式執行前後的 registry 變更。

#### 模式二：DLL 側載（DLL Side-Loading）

Auto-elevate 程式在 High IL 跑，如果它載入 DLL 時搜尋路徑包含可寫目錄，攻擊者可以在那個目錄放一個同名的惡意 DLL。

```
典型路徑：
  1. 目標程式 A.exe 在 High IL 下 LoadLibrary("helper.dll")
  2. Windows DLL 搜尋順序：當前目錄 → System32 → ...
  3. 若當前目錄是可寫的（如 %TEMP%），攻擊者放 helper.dll 進去
  4. A.exe 載入惡意 DLL，DLL 的 DllMain 以 High IL 執行
```

> 注意：現代 Windows 的 SafeDllSearchMode（預設開啟）會把系統目錄的優先度拉高，但仍有繞過空間（KnownDLLs 清單之外的 DLL）。

**防禦偵測**：監控高完整性行程從非系統目錄載入的 DLL 事件（Windows Defender / ETW `ImageLoad` 事件）；Sysmon EventID 7 (ImageLoaded) 過濾出 High IL 行程從 %TEMP% 等路徑載入的 DLL。

#### 模式三：Mock Trusted Directory

Windows 驗證「程式是否在系統目錄」時，有時用字串比較。攻擊者建立一個路徑看起來像 `C:\Windows\System32` 的目錄：

```
C:\Windows \System32\   ← 注意 Windows 後面有空格
```

Windows 的路徑正規化在某些 API 下會把尾隨空格去掉，讓驗證邏輯誤以為是合法系統目錄，但實際上攻擊者可寫。

**防禦偵測**：這個技法在現代 Windows（11）已被修補或難以觸發，但原理是「路徑正規化的歧義」——凡是看到非標準空格的路徑建立事件都要警戒。

#### 模式四：環境變數操縱

部分 auto-elevate 程式透過環境變數決定要執行什麼（`%windir%`、`%SystemRoot%`、`%ComSpec%`）。如果行程在提升前可以寫入環境變數，High IL 的程式執行時可能走到攻擊者指定的路徑。

```
典型路徑（已被大量修補，理解原理即可）：
  1. 寫入 HKCU\Environment: windir = C:\Malicious
  2. 觸發某個讀 %windir% 的 auto-elevate 程式
  3. 程式在 High IL 執行 C:\Malicious\System32\cmd.exe
```

**防禦偵測**：監控 `HKCU\Environment` 下的 `windir`、`SystemRoot`、`ComSpec` 修改；這幾個 key 被改的行為在正常使用中極為罕見。

### UAC Bypass 的「有效性下降曲線」

```
Windows Vista/7     大量 bypass（程式簽章驗證鬆、registry 查詢廣泛）
Windows 8/8.1       修補部分 registry bypass，加強路徑驗證
Windows 10 初期     fodhelper/computerdefaults bypass 被廣泛使用
Windows 10 後期     逐漸縮小自動提升程式的攻擊面；Defender 增加偵測
Windows 11          大多數已知 bypass 被修補，新的難度更高
                    但仍有研究者持續找新的（auto-elevate 程式永遠是攻擊面）
```

重點：UAC bypass 是貓鼠遊戲，沒有永久有效的通用技法。防禦更有意義——偵測「自動提升程式在執行前有不尋常的 registry / 檔案系統變更」比封堵每個特定 bypass 更有效。

## Integrity Level 操縱概觀

除了 UAC 提升（Medium → High），IL 的另一個攻擊方向是**降低 IL** 或**跨 IL 通訊**：

**降低 IL 建立沙箱**：服務程式可以建立 Low IL 的子行程（`CreateProcessAsUser` + 調整 token IL），這是瀏覽器建立 renderer sandbox 的做法。研究方向是找服務的 Low IL 行程和 Medium IL 行程之間的通訊 IPC 通道，看是否有過濾不足的地方。

**UIPI（User Interface Privilege Isolation）**：Low IL 行程不能對 Medium IL 視窗發送滑鼠/鍵盤輸入（`SendMessage`/`PostMessage` 被 UIPI 擋掉）。某些訊息豁免（`ChangeWindowMessageFilterEx`）是過去 UIPI bypass 的研究方向。

**跨 IL 共享記憶體**：Section 物件的 DACL 可以設置讓不同 IL 行程共用記憶體，但如果 IL 高的行程沒有正確驗證來自低 IL 行程的資料，就有資料混淆攻擊的可能。

## 對比與取捨

| 面向 | Linux sudo / setuid | Windows UAC | 差異 |
|---|---|---|---|
| 提升機制 | 密碼驗證（`/etc/sudoers`）或 setuid bit | Admin 帳號點 Yes；標準帳號輸密碼 | UAC 對 admin 使用者幾乎沒有阻礙 |
| 是否安全邊界 | sudo 被視為安全邊界（sudo group 有密碼保護）| UAC 明確**不是**安全邊界 | 不能把 UAC 當成隔離保障 |
| 攻擊面 | setuid 程式（歷史上大量 suid-root 漏洞）| Auto-elevate 程式（manifest + 簽章）| 兩者都是「帶特殊權限的執行檔」 |
| bypass 難度 | 難（需要真正的 suid 程式漏洞）| 較容易（registry/環境變數劫持）| Windows 的 bypass 門檻在現代已提高 |
| 防禦方式 | 最小化 suid，使用 capabilities | 關閉 UAC auto-elevate / 使用標準帳號 | 建議最嚴模式：Always Notify |

## 踩雷集錦

1. **「UAC 是 Windows 的安全邊界，繞過它等於漏洞」**：微軟明確說 UAC 不是安全邊界。在 admin 帳號下的 UAC bypass，微軟不視為需修補的安全問題。真正的 EoP 漏洞是「標準使用者（non-admin）→ admin / SYSTEM」，這才是 MSRC 會修的。

2. **「fodhelper bypass 在 Windows 11 還能用」**：大多數已知的 fodhelper registry hijack 在 Windows 11 已被 Defender 的行為偵測標記，且部分路徑被修補。用已知的 PoC 在現代系統測試要先關 Defender 才能看到原始行為——在真實環境裡，這些 bypass 已有偵測規則。

3. **「UAC 開到最高就安全了」**：UAC 的最高設定（Always Notify）會讓自動提升失效，每次提升都彈 consent 視窗。這確實更難 bypass，但對熟悉 Secure Desktop 以外攻擊路徑的攻擊者仍有辦法——UAC 從設計上就不是要防禦已拿到本機程式碼執行的攻擊者。

4. **「Secure Desktop 讓 consent 視窗完全防截圖/劫持」**：Secure Desktop 防的是同一 session 的使用者態行程讀取它的內容（低 IL 行程無法截圖）。但如果攻擊者已有 SYSTEM 或 kernel 層執行能力，Secure Desktop 就不是屏障了。

5. **「關掉 UAC 就不用擔心這些」**：關 UAC 等於讓每個行程都帶著 Full Token 跑——反而讓惡意程式不需要任何 bypass 就能拿到 High IL。從攻擊者視角，關 UAC 是更理想的環境；從防禦視角，UAC 開著至少增加了摩擦力。

## 進階：再往深一層

**MSRC 的 defense-in-depth 與安全邊界分類**：微軟把 Windows 安全功能分類：有些是「安全邊界」（如 kernel ↔ userland 邊界），有些是「defense-in-depth」（UAC、Defender...）。前者的 bypass 是 Critical 漏洞，後者只算中等或非漏洞。搞清楚這個分類，你才知道提權報告應該怎麼定位嚴重性。

**Token Linked Token 的竊取路徑**：有研究（如 James Forshaw 的早期研究）探討能否直接拿到 Filtered Token 對應的 Full Token handle。現代 Windows 的 `GetTokenInformation(TokenLinkedToken)` 只給回一個 Identify-level 版本，做不了存取操作——這個關口是有意設計的。

**面試題**：「標準使用者帳號（非 admin）在 Windows 上能做 UAC bypass 嗎？」——不能，至少不能用傳統的 UAC bypass 手法（那些利用的是 admin split token 的特性）。標準使用者要提權，要的是真正的 EoP 漏洞（核心提權、kernel exploit、或某個以 SYSTEM 跑的服務漏洞）。

## 動手練習

**目標**：理解 auto-elevate 執行檔的機制，不動手 bypass，而是**偵測它的行為**。

步驟：
1. 在一台有 admin 帳號的 Windows 11 機器上，開啟 Process Monitor（Sysinternals），過濾器設：`Process Name is fodhelper.exe`
2. 從普通（Medium IL）PowerShell 執行 `fodhelper.exe`
3. 觀察 Process Monitor 裡 fodhelper 讀取的 registry 路徑——特別找 `HKCU\Software\Classes\ms-settings` 相關的操作
4. 用 Sysmon EventID 13（RegistryValueSet）設規則，只要 `fodhelper.exe` 父行程之前有人寫 `HKCU\Software\Classes\ms-settings`，就觸發警報

這個練習的目的不是學 bypass，而是**建立偵測規則的直覺**——攻擊者用什麼路徑，防禦者就在那條路徑上設絆腳線。

## 本章重點整理

- UAC 的核心是 split token：admin 帳號登入後拿到兩個 linked token（Medium IL 平時用 / High IL 需要時用），提升觸發走 consent.exe。
- Auto-elevate 執行檔（有微軟簽章 + 在系統目錄 + manifest 有 `<autoElevate>true</autoElevate>`）可以靜默拿 High IL——這就是 UAC bypass 的攻擊面。
- UAC bypass 的共同模式：找自動提升的合法程式，透過 registry 劫持、DLL 側載、環境變數操縱讓它幫你跑 payload。防禦重點是偵測「提升前的異常前置操作」，不是封堵每個具體 bypass。
- 微軟官方說 UAC 不是安全邊界：admin 帳號的 UAC bypass 不算安全漏洞；真正的 EoP 是標準使用者 → admin/SYSTEM，那才是 kernel 或服務漏洞的範疇。

## 自我檢核

- [ ] 不看筆記，能畫出 admin 帳號登入到取得 High IL token 的完整流程（LSASS → split token → consent → AppInfo → High IL）
- [ ] 能解釋為什麼 Medium IL 行程可以寫 `HKCU`，但這樣寫會讓 auto-elevate 程式在 High IL 下讀到
- [ ] 被問「fodhelper UAC bypass 的原理是什麼」——能說出 registry hijack 路徑和 autoElevate manifest 的關係，不需要背 PoC 步驟
- [ ] 能說出為什麼微軟說 UAC 不是安全邊界，以及這對 bug bounty 報告有什麼影響
- [ ] 能設計一條 Sysmon 規則，偵測「在 auto-elevate 程式執行前，有人寫了相關的 HKCU registry key」

## 延伸閱讀

### 官方文件

- **[How User Account Control Works — Microsoft Learn](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)**
  - **讀哪裡**：「UAC Architecture」和「The UAC Elevation Prompts」兩節，特別是 consent.exe 的角色
  - **學什麼**：微軟官方對 UAC 流程的完整說明；consent.exe 為什麼必須在 System IL / Secure Desktop 跑
  - **和本章關聯**：本章 consent 流程圖的一手來源

- **[Windows Security Servicing Criteria（UAC 不是安全邊界的依據）](https://www.microsoft.com/en-us/msrc/windows-security-servicing-criteria)**
  - **讀哪裡**：「Defense in depth features」一節（UAC 在此分類）
  - **學什麼**：微軟怎麼分類哪些是安全邊界、哪些是 defense-in-depth；這決定了漏洞嚴重性
  - **前提**：本章全讀

### 研究 / 部落格

- **[UAC bypass techniques — UACME（hfiref0x）](https://github.com/hfiref0x/UACME)**
  - **讀哪裡**：README 裡的技法索引（不要直接跑工具，研究每個技法對應的 Windows 版本和原理）
  - **學什麼**：有史以來最完整的 UAC bypass 技法目錄（70+ 種），每種都標了 OS 版本和狀態（是否已修補）；是研究 UAC bypass 的一次索引
  - **前提**：本章的 autoElevate / registry hijack 概念

- **[Ghosts of UAC Past — James Forshaw（Project Zero）](https://bugs.chromium.org/p/project-zero/issues/detail?id=1524)**
  - **讀哪裡**：整篇分析；重點是「token linked token」的邊界和 AppInfo 服務的驗證邏輯
  - **學什麼**：從安全研究者角度剖析 UAC 設計的邊界在哪裡、哪些被錯誤假設為安全但其實不是
  - **前提**：Ch 44 + 本章

- **[PrintSpoofer — From LOCAL/NETWORK SERVICE to SYSTEM（itm4n）](https://itm4n.github.io/printspoofer-abusing-impersonate-privileges/)**
  - **讀哪裡**：整篇（不長）；重點是 `SeImpersonatePrivilege` 如何接上 named pipe 強制認證
  - **學什麼**：這是 Ch 46 Potato 家族的前導；本章讀完後去看這篇，理解「從服務帳號到 SYSTEM」的完整路徑
  - **前提**：Ch 44 的 SeImpersonatePrivilege 概念

→ [Ch 46 — token stealing / EoP 原語概觀](./46-token-stealing-eop.md)
