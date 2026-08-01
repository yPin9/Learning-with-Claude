# Ch 0 — 環境搭建：AVD、drozer、MobSF、Frida、靶場

> **目標**：把一套**能系統化找 App 漏洞**的工作台從零裝起來——一台可 root 的 AVD、一套元件攻擊工具（drozer）、一套自動化掃描（MobSF、semgrep、mobsfscan、apkleaks）、一套動態工具（Frida、objection）、一套抓包（mitmproxy），外加四個刻意埋洞的靶場（DIVA、AndroGoat、InsecureBankv2、Pivaa）。裝完跑通第一個冒煙測試：drozer 連上 AVD、列出一個 App 的攻擊面。之後每一章你都有地方動手打。

> **環境**：本章以 **AVD（Android 13 / API 33，x86_64，Google APIs image，可 root）**、`drozer 3.x`（新版 WithSecure fork）、`MobSF`（Docker）、`Frida 16.x`、`objection`、`semgrep` + `mobsfscan` + `apkleaks`、`mitmproxy` 為準。host 假設 Windows 11 / macOS / Linux 皆可，指令以 Linux/macOS 的 shell 為主，Windows 用 PowerShell 等價替換。

> **合法邊界（先講清楚，這是紀律不是客套）**：這整門課教的技術能打真實 App，但**你只能測你有權測試的目標**——自己寫的 App、本章這四個開源靶場、CTF 題、或**有明確 scope 的 bug bounty 專案**。對沒授權的 App 做元件攻擊、抓包、注入，在多數司法管轄區是違法的（未授權存取電腦系統）。本章之後所有 PoC 都在**你自己的 AVD + 開源靶場**上跑，這是安全的練習場。上真實目標前，先確認 scope。

## 為什麼需要這個？

android_reversing 教你把 App 拆開讀懂，那套環境（AVD + apktool/jadx + Frida）你已經有了。但「找漏洞」需要的不只是讀懂——你要能**枚舉攻擊面**（這個 App 暴露了哪些元件、開了哪些 deeplink、存了什麼在本地），還要能**主動觸發**（對一個 exported Activity 丟畸形 Intent、對 ContentProvider 下 SQL、把流量改掉重放）。

這些動作，靠 adb 一條條敲很痛苦。所以這門課多引入幾個專門工具：

- **drozer**：元件攻擊的瑞士刀。它在 AVD 裡裝一個 agent App，你在電腦上用 client 對它下指令，就能「用另一個 App 的身分」對目標的 Activity/Service/Provider/Broadcast 發動攻擊——這正是真實惡意 App 會做的事，drozer 幫你模擬。
- **MobSF**：一鍵靜態 + 動態掃描，快速偵察。它不會找到深層洞，但能在 30 秒內把「這 App 有哪些明顯問題」列出來，是 recon 的加速器。
- **semgrep / mobsfscan / apkleaks**：程式碼層與字串層的自動化掃描，補 MobSF 沒覆蓋的死角。
- **Frida / objection**：動態驗證。找到可疑點後，hook 進去看執行期真實的值、繞過 pinning、dump keystore。
- **mitmproxy**：抓包，看 App 跟伺服器說什麼。

工具會過時，但「靜態掃 → 枚舉元件 → 動態驗 → 抓包印證」這條流水線不會。這章把流水線的每個工具都裝好、串通。

## 先建立直覺：漏洞分析工作台長什麼樣

android_reversing 的工作台是「host 控制 AVD、Frida 注入、抓包」三條線。這門課在同一張圖上**多加一個角色：drozer agent**——它是一個活在 AVD 裡、扮演「攻擊方 App」的跳板。

```
   你的電腦 (host)                          AVD (guest, Android 13, root)
 ┌──────────────────────────┐            ┌────────────────────────────────┐
 │  自動化掃描 (離線)        │            │  target App (被測對象)          │
 │   MobSF (docker)          │            │    Activity / Service           │
 │   semgrep / mobsfscan     │──掃 APK──▶ │    Provider / Receiver          │
 │   apkleaks                │            │        ▲                        │
 │                           │            │        │ Intent / query / call  │
 │  元件攻擊                 │            │        │ (跨 App IPC)           │
 │   drozer (client) ────────┼── adb ────▶│  drozer agent App ─────────────┘
 │                           │   forward  │   (扮演「攻擊方 App」的跳板)     │
 │  動態分析                 │            │        │                        │
 │   frida (client) ─────────┼── 注入 ───▶│  frida-server (root)           │
 │   objection               │            │        │                        │
 │  抓包                     │◀── 流量 ───┤  網路 → host proxy             │
 │   mitmproxy               │            │                                │
 └──────────────────────────┘            └────────────────────────────────┘
```

關鍵新概念是 **drozer 的雙邊架構**：guest 裡的 **agent** 是一個普通 App（`agent.apk`），它開了一個 embedded server；host 上的 **client**（`drozer console`）透過 adb port forward 連上它，下的每個指令都由 agent 在 guest 裡**以一個 App 的身分**執行。所以當你用 drozer「打」目標的某個 exported Activity，本質是 agent App 對目標發了一個 Intent——這跟真實惡意 App 的攻擊路徑一模一樣。這是 drozer 比「adb am start」強的地方：它幫你站在**攻擊方 App**的位置，而不是站在 shell 的位置（shell 權限太高，會掩蓋真實的權限邊界）。

## Step 0：先確保 android_reversing Ch 0 的地基在

這門課假設你已經有 android_reversing Ch 0 那套：**可 root 的 AVD（google_apis, x86_64, API 33）**、`adb`、`Frida 16.x`（client + frida-server 版本一致）。如果還沒有，先回 [android_reversing Ch 0](../android_reversing/00-environment-setup.md) 建起來——那章把 AVD 為什麼要選 Google APIs image（能 `adb root`）、frida-server 架構要對（x86_64 不是 arm64）講得很清楚，這裡不重複。

快速自檢（三條都要綠）：

```bash
adb root && adb shell whoami        # 期望: root
frida-ps -U | head -3               # 期望: 列出 guest 進程
adb shell getprop ro.build.version.sdk   # 期望: 33
```

> **未實測，理論預期行為**：本 repo 的建構沙箱沒有 Android SDK/AVD/adb，上面三條的輸出（`root`、進程列表、`33`）是依工具實際行為寫的代表值。你在自己機器跑，看到這三個關鍵字就是地基 OK。以下凡是需要 AVD/drozer/MobSF/Frida 執行的段落，我都標「未實測，理論預期行為」並附驗證步驟；純檔案/邏輯能用 Python 驗的，標「實際輸出」。

## Step 1：裝 drozer（client + agent）

drozer 是 WithSecure（原 MWR）的元件攻擊框架。原版 drozer 依賴 Python 2，早已停更；**用新的 Python 3 fork**（`WithSecureLabs/drozer` 的近年 release）。它分兩半，跟 Frida 一樣：**host 上的 client** 與 **guest 裡的 agent App**。

裝 client（host 端；建議用獨立 venv 避免污染系統 Python）：

```bash
python3 -m venv ~/venv/drozer
source ~/venv/drozer/bin/activate
pip install drozer          # 新版 fork 已上 PyPI；或從 GitHub release 裝 wheel
drozer --version            # 期望印出 3.x
```

裝 agent（guest 端）——從同一個 release 頁抓 `drozer-agent.apk`，裝進 AVD：

```bash
# 從 https://github.com/WithSecureLabs/drozer/releases 下載 drozer-agent.apk
adb install drozer-agent.apk
```

drozer 的連線靠 adb port forward（agent 的 embedded server 預設聽 31415）：

```bash
adb forward tcp:31415 tcp:31415
```

然後在 AVD 裡**手動打開 drozer Agent App，點 "Embedded Server" → "Enable"**（agent 要主動啟動 server，client 才連得上）。最後 host 連上：

```bash
drozer console connect
```

成功會進到 drozer 的互動 shell：

```
dz>
```

> **未實測，理論預期行為**：drozer client/agent 需要真的 AVD，沙箱跑不了。上面的流程與 `dz>` 提示符是 drozer 官方文件與實務的標準行為。你的驗證步驟：`drozer console connect` 後打 `dz> list`，會列出上百個 drozer module（`app.activity.info`、`app.provider.query` 等），列得出來就代表 client↔agent 通了。連不上最常見兩個原因：(1) 忘了在 agent App 裡 Enable server；(2) 忘了 `adb forward tcp:31415 tcp:31415`。

## Step 2：裝 MobSF（Docker 最省事）

MobSF（Mobile Security Framework）是自動化靜/動態掃描平台。**用 Docker 跑**，省去它一長串 Python/Node 依賴的地獄：

```bash
docker pull opensecurity/mobile-security-framework-mobsf:latest
docker run -it --rm -p 8000:8000 \
  opensecurity/mobile-security-framework-mobsf:latest
```

開瀏覽器到 `http://localhost:8000`，把 APK 拖進去，它會跑靜態分析：列出權限、元件（並標哪些 exported）、硬編碼 secret、不安全的 API 呼叫、network config、簽名資訊，最後給一個 security score。

> **未實測，理論預期行為**：MobSF 掃描要真的跑起 Docker container 並上傳 APK，沙箱做不到。上面的 `docker run` 是 MobSF 官方 README 的標準跑法。驗證步驟：container 起來後 log 會印 `REST API Key`、`MobSF started at http://0.0.0.0:8000`，瀏覽器能開 dashboard 就 OK。MobSF 的動態分析（DAST）需要它連上你的 AVD/真機（透過 adb），設定較繁，Ch 15 自動化那章再展開；本章先用靜態就夠 recon。

> **MobSF 的定位要擺正**：它是**加速偵察**的，不是**替你找洞**的。它會告訴你「這 App 有 5 個 exported Activity、2 個明文 URL、1 個疑似硬編碼 API key」——這是線索，不是結論。真正判斷「這個 exported Activity 是不是可利用的洞」，要靠你後面章節學的手動驗證。把 MobSF 的輸出當成「該去哪裡挖」的地圖，別當成漏洞清單直接交報告。

## Step 3：裝程式碼/字串層掃描（semgrep、mobsfscan、apkleaks）

MobSF 是綜合平台，但有些細活它不做。補三個輕量工具：

```bash
pip install semgrep mobsfscan apkleaks
```

- **semgrep**：規則式源碼掃描（配 mobsfscan 的規則能掃 Android 特有 pattern）。它掃的是**反編譯出來的原始碼**——你先 `jadx -d out app.apk` 反編譯，再對 `out/sources/` 跑 semgrep 規則，找 WebView `setJavaScriptEnabled(true)` 配 `addJavascriptInterface`、`MODE_WORLD_READABLE`、弱加密等 pattern。
- **mobsfscan**：MobSF 團隊出的獨立 static analyzer，內建一大包 Android/iOS 安全規則，直接吃反編譯後的原始碼目錄。
- **apkleaks**：專掃 APK 裡的**祕密與端點**——URL、S3 bucket、API key pattern、Firebase 連結。字串層的快速淘金。

冒煙測試 apkleaks（拿任一 APK）：

```bash
apkleaks -f target.apk
```

> **未實測，理論預期行為**：這三個工具都能裝在沙箱（純 Python），但要有意義的輸出得餵真 APK，沙箱裡沒有靶場 APK。apkleaks 對真 APK 的代表性輸出是一份分類的 URL/secret 清單（`[URI]`、`[Firebase]`、`[Amazon_AWS_S3_Bucket]` 等 section）。驗證步驟：對本章下面裝的 DIVA APK 跑 `apkleaks -f diva.apk`，它會撈出 DIVA 刻意埋的字串。

## Step 4：確認 Frida / objection（動態驗證線）

Frida 你在 android_reversing Ch 0 已經裝好。這門課多用 **objection**——它是架在 Frida 上的互動式工具，內建一堆「找漏洞」常用的 hook，不用自己寫腳本：

```bash
pip install objection
```

常用招式（Ch 8/9/10 會細講）：

```bash
objection -g com.target.app explore        # 進互動式 shell
# 進去後：
android hooking list activities            # 列所有 Activity
android sslpinning disable                 # 一鍵繞過 SSL pinning（抓包用）
android keystore list                      # dump Keystore 內容
android hooking watch class_method <方法>   # 監看某方法的呼叫
```

> **未實測，理論預期行為**：objection 需要 frida-server 在 AVD 裡跑 + 目標 App 已裝。上面的子命令是 objection 官方文件的標準用法。驗證步驟：`frida-server` 跑起來後，`objection -g <package> explore` 能進到它的 shell 提示符（`<package> on (google: 13) [usb] #`），就代表 Frida 動態線通了。

## Step 5：裝 mitmproxy（抓包線）

看 App 跟伺服器的對話，靠把 AVD 流量導到 host 上的 mitmproxy：

```bash
pip install mitmproxy
mitmweb                    # 帶 web UI 版，看流量最直覺
```

然後在 AVD 的 Wi-Fi 設定裡把 proxy 指向 `<host IP>:8080`，再裝 mitmproxy 的 CA 憑證為系統憑證（Android 7+ 預設不信任 user CA，要裝成 system CA，需要 `-writable-system` 開機的 AVD）。這套流程 android_reversing 的抓包章講過，Ch 9 網路層漏洞會再用到。

> **未實測，理論預期行為**：mitmproxy 本身能裝在沙箱，但抓 App 流量要 AVD + proxy 設定 + CA 憑證。驗證步驟：AVD 開瀏覽器連任一 HTTPS 網站，mitmweb 的 UI 出現該請求（且無憑證錯誤）就代表抓包 + CA 都 OK。

## Step 6：裝四個靶場

這門課每一類漏洞都要在靶場打一遍。四個開源靶各有側重，先全裝進 AVD：

| 靶場 | 埋的洞 | 主要用在 |
|---|---|---|
| **DIVA (Damn Insecure and Vulnerable App)** | 不安全儲存、硬編碼、input validation、SQLi | Ch 6、Ch 10–12 |
| **AndroGoat** | 元件暴露、深連結、WebView、root 檢測、pinning | Ch 3–9（元件 + 前端面） |
| **InsecureBankv2** | 完整銀行 App：登入、Broadcast、備份、pinning、混淆 | Ch 3–5、Ch 9、貫穿 final |
| **Pivaa (Purposefully Insecure and Vulnerable Android App)** | 較新，涵蓋現代 API 的誤用 | 補充、對照 |

裝法都一樣——從各自 GitHub release 抓 APK，`adb install`：

```bash
# 從各專案的 GitHub release 下載 apk 後：
adb install diva.apk
adb install androgoat.apk
adb install insecurebankv2.apk
adb install pivaa.apk

# 確認都裝上了
adb shell pm list packages | grep -Ei 'diva|goat|insecurebank|pivaa'
```

> **未實測，理論預期行為**：`adb install` 需要 AVD。這四個靶場的 package name 各專案文件有列（如 DIVA 是 `jakhar.aseem.diva`）。驗證步驟：`pm list packages | grep` 列出四個 package，且在 AVD launcher 裡看得到四個 App 圖示，就代表靶場就位。**注意**：InsecureBankv2 的動態部分還要跑一個後端 server（Python），它的登入功能才會活；純元件/儲存分析不需要 server。

## Step 7：End-to-End 冒煙測試 —— 列一個 App 的攻擊面

把工作台串起來跑一次：用 drozer 對一個靶場 App **枚舉它的攻擊面**——這是整門課 recon 的第一個動作。以 AndroGoat 為例：

```bash
# 1. drozer 連上
adb forward tcp:31415 tcp:31415
drozer console connect

# 2. 進到 dz> 後，先確認 package name
dz> run app.package.list -f goat

# 3. 一鍵列出這個 App 的完整攻擊面
dz> run app.package.attacksurface owasp.sat.agoat
```

`app.package.attacksurface` 的代表性輸出：

```
Attack Surface:
  5 activities exported
  2 broadcast receivers exported
  1 content providers exported
  0 services exported
  is debuggable
```

> **未實測，理論預期行為**：這需要 drozer + AndroGoat 裝在 AVD。上面的 `attacksurface` 輸出格式是 drozer 的標準輸出（實際數字依 AndroGoat 版本而異）。這一行輸出就是你對一個 App 動手的起點——它告訴你「有幾個 exported 元件可以打」「debuggable 有沒有開」。Ch 3 開始，我們就逐一把這些 exported 元件展開來攻擊。驗證步驟：看到 `Attack Surface:` 後面列出各類元件的 exported 數量，就代表 drozer + 靶場 + 你的 recon 流程全部打通。

看到這行輸出，代表你的整套工作台活了：AVD 跑著靶場、drozer agent 扮演攻擊方 App、client 從你電腦下指令、拿回目標的攻擊面。這就是後面 15 章每一章的起手式。

## drozer module 速查：你會一直用到的那幾個

drozer 有上百個 module（`dz> list` 全列），但實務上 recon 與攻擊反覆用的就這幾組。先眼熟，Part 2 會逐一深用：

```
── recon（先摸清楚 App）──────────────────────────────────
run app.package.list -f <關鍵字>          列出符合的 package
run app.package.info -a <package>         看某 App 的權限、UID、安裝路徑
run app.package.attacksurface <package>   一鍵列 exported 元件數 + debuggable

── 枚舉各類元件（列出「可打的點」）───────────────────────
run app.activity.info -a <package>        列 exported Activity
run app.service.info -a <package>         列 exported Service
run app.broadcast.info -a <package>       列 exported Receiver
run app.provider.info -a <package>        列 exported Provider 與其 URI

── 發動攻擊（真的觸發）────────────────────────────────
run app.activity.start --component <pkg> <activity>   啟動一個 exported Activity
run app.broadcast.send --component <pkg> <receiver>   對 Receiver 發廣播
run app.provider.query content://<authority>/<path>   對 Provider 下查詢
run app.provider.read content://<authority>/<path>    讀 Provider 供出的檔案

── 自動掃（drozer 幫你先試一輪）──────────────────────────
run scanner.provider.injection -a <package>   自動測 Provider 有沒有 SQLi
run scanner.provider.traversal -a <package>   自動測 Provider 有沒有 path traversal
run scanner.provider.finduris -a <package>    枚舉可存取的 content URI
```

> **未實測，理論預期行為**：這些 module 名稱與參數是 drozer 的標準 command reference。這張表的價值是給你一個「從 recon 到攻擊」的完整動作序列——**先 info 枚舉、再 start/query 攻擊、卡住時用 scanner 自動試**。Ch 3–6 每一章都對應這裡的某幾個 module。把這頁釘起來，打靶時照著走。

## Step 8：把工作台狀態存成快照（保命）

打靶會弄髒環境——某個 PoC 改了 App 資料、某次注入把 App 搞崩、某個測試留下殘留檔案。跟 android_reversing 一樣，把**乾淨的完整工作台**存成一個 AVD 快照：

```bash
# 確保這些都就位後再存：root + frida-server 就緒 + drozer agent 已裝
#   + 四個靶場都裝好 + mitmproxy CA 已裝成 system CA
# 用 Android Studio 的 AVD Snapshot，或 emulator console:
adb emu avd snapshot save clean_lab
```

之後每次分析壞了，`adb emu avd snapshot load clean_lab` 秒回到乾淨已裝好一切的狀態，不用重跑 Step 1–7。

> **未實測，理論預期行為**：快照存取需要 AVD。這一步的意義是把「重建整套環境」的成本從幾十分鐘降到幾秒——打靶時你會頻繁弄髒環境，沒快照會很痛苦。

## 對比與取捨：這些工具各打哪一層？

| 工具 | 打哪一層 | 靜/動態 | 何時用 | 局限 |
|---|---|---|---|---|
| **MobSF** | 綜合（元件/權限/secret/net） | 靜態為主 | recon 第一步，快速掃全貌 | 只給線索不給結論；深洞抓不到 |
| **semgrep / mobsfscan** | 反編譯源碼層 | 靜態 | 找程式碼 pattern（WebView、crypto） | 需先反編譯；規則覆蓋有限 |
| **apkleaks** | 字串/端點層 | 靜態 | 快速淘 secret 與 URL | 只是字串比對，會有 false positive |
| **drozer** | 元件/IPC 層 | 動態 | 枚舉 + 攻擊 exported 元件 | 主要打 IPC，不碰 native/網路 |
| **Frida / objection** | 執行期（Java/native） | 動態 | 驗證假設、繞防護、dump | 只看到跑過的路徑 |
| **mitmproxy** | 網路層 | 動態 | 看/改 App 與 server 的對話 | 要處理 pinning |

沒有一個工具能包辦。實務上是 **MobSF/semgrep 掃出線索 → drozer 枚舉元件 → Frida 動態驗證 → mitmproxy 抓包印證**，四條線互相補。

## 踩雷集錦

1. **用舊版 Python 2 的 drozer**：原版 drozer 停在 Python 2、早就跑不動。錯誤直覺「drozer 裝不起來、過時了」——正確：用 **WithSecureLabs 的 Python 3 fork**（近年 release），它才是活的。
2. **drozer console 連不上**：`Could not connect`。錯誤直覺「client 壞了」——正確：檢查兩件事，(1) AVD 裡的 agent App 有沒有點 **Enable embedded server**，(2) 有沒有 `adb forward tcp:31415 tcp:31415`。兩者缺一就連不上。
3. **把 MobSF score 當漏洞報告**：MobSF 給了 40 分就以為找到一堆洞。錯誤直覺「工具說有洞就是有洞」——正確：MobSF 的每個 flag 都是**待驗證的線索**，要手動確認可利用性才算數。直接把 MobSF 輸出貼進報告是新手最大的信譽殺手。
4. **semgrep 掃 APK 本體**：對著 `.apk`（zip）跑 semgrep 什麼都掃不到。錯誤直覺「餵 APK 就好」——正確：semgrep 掃**原始碼**，要先 `jadx -d out app.apk` 反編譯，再對 `out/sources/` 掃。
5. **抓包忘了 CA 是 system 還是 user**：Android 7+ 預設 App **不信任 user 憑證**，你裝了 mitmproxy CA 卻還是抓不到、一堆 SSL 錯。錯誤直覺「憑證裝了就該通」——正確：要裝成 **system CA**（需 `-writable-system` 的 AVD + `adb remount`），或用 objection/Frida 繞 pinning。Ch 9 細講。
6. **在沒授權的 App 上練手**：拿商店下載的真實 App 開 drozer 打元件——這可能違法。錯誤直覺「反正只在我自己手機上」——正確：**未授權存取他人系統**跟裝置是誰的無關，只在自己寫的 App、開源靶、有 scope 的 bounty 上動手。

## 進階：再往深一層

- **drozer 的 module 生態**：`dz> list` 列出的上百個 module 是 drozer 的全部能力。除了本章的 `app.package.attacksurface`，還有 `app.activity.start`（啟動 exported Activity）、`app.provider.query`（對 Provider 下 SQL）、`app.broadcast.send`（發 Broadcast）、`scanner.provider.injection`（自動測 Provider SQLi）。Part 2 會一個個用到。drozer 還能寫**自訂 module**（Python），把你的重複攻擊流程自動化——Ch 15 自動化那章會碰。
- **MobSF 的 DAST + Frida 整合**：MobSF 的動態分析模式能連上你的 AVD，跑 App 時自動抓 API、runtime 行為、甚至內建 Frida script（列 class、繞 pinning）。設定較繁（要給 MobSF adb 存取權），本章先用靜態，Ch 15 展開動態。
- **快照回滾保命**：跟 android_reversing 一樣，把「乾淨已 root + frida-server + drozer agent + 四個靶場都裝好」的 AVD 狀態存一個 snapshot。打靶打崩了、或某個 PoC 把 App 資料弄髒了，秒回滾到乾淨狀態重來，不用重跑整套 Step 1–6。
- **objection 的 patchapk**：對付沒 root 的真機，objection 能把 Frida gadget 直接**塞進 APK 重打包**（`objection patchapk`），讓 App 自帶注入能力，不需要 frida-server。真機評估時很有用，這裡先知道有這條路。

## 動手練習

1. 把 drozer client + agent 裝起來，`drozer console connect` 進到 `dz>`，打 `list` 看它有多少 module。再打 `run app.package.list` 看 AVD 上所有 App 的 package name——這是你之後每次動手的第一步。
2. 用 Docker 跑起 MobSF，把 DIVA 的 APK 拖進去掃，看它的 report：記下它列出的 exported 元件數、硬編碼 secret、security score。**先不要相信它**——把這份 report 當成「後面章節要逐一驗證的清單」存起來，學完 Part 2 回來看你能驗證幾個。
3. 對四個靶場各跑一次 `run app.package.attacksurface`，把四個 App 的攻擊面（幾個 exported activity/provider/receiver/service、debuggable 與否）抄下來，做成一張表。這張表就是你這門課的「作業清單」——每學一類漏洞，回來勾掉一個。

## 本章重點整理

- 漏洞分析工作台 = android_reversing 的地基（AVD + Frida）**再加 drozer（元件攻擊跳板）+ MobSF/semgrep/apkleaks（自動化偵察）+ objection/mitmproxy（動態驗證與抓包）**。
- **drozer 是雙邊架構**：guest 的 agent App 扮演「攻擊方 App」，host 的 client 下指令，模擬真實惡意 App 的攻擊路徑——比 adb shell 更貼近真實權限邊界。
- **MobSF/semgrep 只給線索不給結論**；把它們的輸出當「該去哪挖」的地圖，可利用性要靠後面章節手動驗證。
- 用 **`app.package.attacksurface` 列一個 App 的攻擊面**是每一章的起手式，也是本章的冒煙測試。
- **合法邊界是紀律**：只在自己的 App、開源靶、有 scope 的 bounty 上動手。

## 自我檢核

- [ ] 能說出 drozer 的 client 與 agent 各在哪一端、連線靠什麼（adb forward + agent 的 embedded server）
- [ ] 能解釋為什麼 drozer 用「agent App 的身分」攻擊，比用 adb shell 更貼近真實攻擊
- [ ] 能講出為什麼「MobSF 給的 flag 不能直接當漏洞」，該怎麼處理它的輸出
- [ ] 知道 semgrep 要掃什麼（反編譯後的源碼，不是 APK 本體）
- [ ] 跑通了冒煙測試，用 `attacksurface` 看到一個靶場 App 的 exported 元件數
- [ ] 能說清楚自己「可以」與「不可以」在哪些 App 上練手

## 延伸閱讀

### 工具官方文件

- **[drozer 官方文件（WithSecureLabs）](https://github.com/WithSecureLabs/drozer)**
  - **讀哪裡**：README 的安裝與 `console connect` 流程；`docs/` 裡的 command reference（`app.*` module）
  - **和本章的關聯**：Step 1 的裝法與 Step 7 的 `attacksurface` 都源自這；Part 2 打元件時你會一直回來查 module
- **[MobSF 官方文件](https://mobsf.github.io/docs/)**
  - **讀哪裡**：Docker 安裝那節，以及 Static Analysis 的報告怎麼讀
  - **注意**：DAST（動態）那節先略讀，Ch 15 再回來設定連 AVD

### 方法論

- **[OWASP MASTG — 測試環境設定](https://mas.owasp.org/MASTG/tools/android/)**
  - **這篇說什麼**：業界標準的 Android 測試工具清單（drozer/MobSF/Frida/objection 都在）與各自用途
  - **讀哪裡**：Android 的 tools 一節，對照你這章裝的每個工具
  - **為什麼值得讀**：本課全程以 MASTG 為骨架，這頁是「該用哪些工具」的權威依據

### 靶場

- **[DIVA / AndroGoat / InsecureBankv2 專案頁](https://github.com/payatu/diva-android)**
  - **讀哪裡**：各專案 README 的「有哪些漏洞」清單與 package name
  - **和本章的關聯**：Step 6 裝的四個靶，每個 README 都列了它埋了哪些洞——是你對照學習進度的檢查表

### 社群 cheat sheet

- **[HackTricks — Android Pentesting](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **這篇說什麼**：drozer/MobSF/objection 的實戰指令，每類攻擊都有可複製的 command
  - **讀哪裡**：environment setup 與 drozer 那幾段
  - **前提知識**：讀過本章，這頁給你更多現成指令當作弊條

工作台裝好了，但在動手打之前，得先有一張**攻擊面地圖**——一個 App 到底有哪些地方能被打、OWASP 的 MASVS/MASTG 怎麼把「找漏洞」系統化成可重複的流程。下一章我們把整張攻擊面攤開，建立這門課的方法論骨架。

→ [Ch 1 App 攻擊面全貌與 MASVS/MASTG 方法論](./01-attack-surface-masvs.md)
