# Ch 0 — 環境搭建

> **目標**：把這門課要用的工具一次備齊——Node.js、Firebase CLI、一個 Google 帳號、一個真實的 Firebase 專案——並且能用一行指令驗證「我的電腦真的認得我的 Firebase 專案」。這章結束時，你手上會有一個空的、但真實存在於雲端的後端。

> **環境**：本章以 Node.js v22.20.0、firebase-tools 15.28.1 在 Windows 上操作。macOS / Linux 指令幾乎相同，差異處會標注。

## 為什麼需要這個？

大部分「學 X」的第一章都在裝環境，很無聊，但 Firebase 這章特別重要，原因是：**Firebase 的開發環境橫跨「你的電腦」和「Google 的雲端」兩邊**，而很多人卡關不是因為程式碼寫錯，是因為這兩邊沒對上——CLI 沒登入、專案 ID 打錯、本機根本沒連到那個雲端專案。

先把這兩邊的橋搭好，後面每一章才能專心在「功能」上，而不是在「為什麼連不上」上鬼打牆。

## 先建立直覺：Firebase 開發環境長什麼樣

在裝任何東西之前，先在腦中畫出這張圖。整門課你都會在這三個角色之間來回：

```
   你的電腦 (本機)                          Google 雲端
 ┌────────────────────┐                ┌──────────────────────┐
 │                    │                │  你的 Firebase 專案    │
 │  瀏覽器            │  ── HTTPS ──▶  │  ┌────────────────┐  │
 │  (你的網頁 App)    │  ◀── 即時 ──   │  │ Authentication │  │
 │  用 JS SDK 連上    │                │  │ Firestore 資料庫│  │
 │                    │                │  │ Storage 檔案    │  │
 ├────────────────────┤                │  │ Hosting        │  │
 │  終端機            │                │  └────────────────┘  │
 │  Firebase CLI      │  ── 管理 ──▶   │                      │
 │  (部署/設定/模擬)  │                │  Console (網頁後台)   │
 └────────────────────┘                └──────────────────────┘
        ▲                                        ▲
        │                                        │
   你寫程式、下指令的地方              你在瀏覽器點來點去管專案的地方
```

三個角色，各自的分工：

| 角色 | 是什麼 | 你用它做什麼 |
|---|---|---|
| **Firebase Console**（網頁後台） | Google 提供的網頁管理介面 | 建專案、開關功能、看資料、設定登入方式。**用滑鼠點。** |
| **Firebase CLI**（`firebase` 指令） | 裝在你電腦上的命令列工具 | 登入、部署網站、跑本地模擬器、初始化專案設定。**用終端機打。** |
| **Firebase JS SDK**（`firebase` npm 套件） | 你網頁 App 裡 import 的 JavaScript 函式庫 | 讓網頁程式碼實際去讀寫資料庫、做登入。**寫在程式碼裡。** |

初學最常搞混的就是 CLI 和 SDK：它們都叫「firebase」，但一個是你在終端機用的工具，一個是你程式碼裡 import 的函式庫。這章裝 CLI，SDK 到 Ch 3 才會 import。

## Step 1：安裝 Node.js

Firebase CLI 和 JS SDK 都跑在 Node.js 上，所以先有 Node。

到 [nodejs.org](https://nodejs.org/) 下載 **LTS 版**安裝（Windows 直接下載 `.msi` 一路下一步；macOS 可用 `brew install node`）。裝完在終端機驗證：

```bash
node --version
npm --version
```

真實輸出（本課環境）：

```
v22.20.0
10.9.3
```

> **為什麼要 Node？** 就算你最後只是寫「純 HTML + JS 在瀏覽器跑」的網頁，Node 仍然是必要的——因為 Firebase CLI 本身是一個 Node 程式，本地模擬器、部署工具也都靠它。Node 版本用近期的 LTS（20/22）即可——本課釘定的 firebase-tools 15.28.1 要求 **Node 20 以上**，Node 18 會被 CLI 拒絕。

## Step 2：安裝 Firebase CLI

`npm` 是跟著 Node 一起裝的套件管理器。用它把 Firebase CLI 裝成**全域指令**：

```bash
npm install -g firebase-tools
```

`-g` 是 global（全域）的意思，裝完 `firebase` 這個指令在哪個資料夾都能用。驗證：

```bash
firebase --version
```

真實輸出：

```
15.28.1
```

看到版本號就代表 CLI 裝好了。試著看它有哪些指令：

```bash
firebase --help
```

你會看到一串子指令，這門課會用到的幾個先混個臉熟：

```
  login        log the CLI into Firebase        # 登入
  projects     manage Firebase projects         # 管理專案
  init         interactively configure ...       # 初始化專案設定
  deploy       deploy code and assets to ...     # 部署（Ch 20 用）
  emulators    start and manage local ...        # 本地模擬器（Ch 21 用）
  use          set an active Firebase project    # 切換使用中的專案
```

> **踩雷預告**：Windows 上如果 `npm install -g` 報權限錯誤，用**系統管理員身分**開一個新的 PowerShell 再裝一次。macOS/Linux 若報 `EACCES`，不要用 `sudo npm install -g`（會製造更多權限問題），改用 [nvm](https://github.com/nvm-sh/nvm) 管理 Node，權限問題會消失。

## Step 3：準備一個 Google 帳號

Firebase 是 Google 的服務，用你的 Google 帳號登入即可，不需要另外註冊 Firebase 帳號。如果你已經有 Gmail，就用它。

> 建議：如果你打算長期玩 side project，可以考慮開一個**專門的 Google 帳號**放這些實驗專案，跟你日常信箱分開，之後管理權限、刪專案都乾淨。這不是必須，日常帳號完全可以。

## Step 4：CLI 登入（把本機和你的 Google 帳號綁起來）

> 這一步會打開瀏覽器要你授權，**由你在自己的電腦上操作**（我無法代跑互動式登入）。

在終端機執行：

```bash
firebase login
```

第一次跑會問你要不要收集匿名使用資料（回答 Y 或 N 都行），接著會**自動打開瀏覽器**，要你選 Google 帳號並授權 Firebase CLI。授權完瀏覽器會顯示「Woohoo! Firebase CLI Login Successful」，終端機則會印出：

```
✔  Success! Logged in as your-email@gmail.com
```

看到你的 email，就代表本機的 CLI 已經綁上你的 Google 帳號了。想確認目前登入誰：

```bash
firebase login:list
```

> **`firebase login` 到底做了什麼？** 它跑了一趟 OAuth 授權流程，讓 Google 發一組憑證（refresh token）存在你電腦的設定檔裡（Windows 在 `%USERPROFILE%\.config\configstore\firebase-tools.json`）。之後 CLI 每次要動你的專案，都用這組憑證去換臨時的 access token。這跟 Ch 4 要講的「使用者登入」是同一套 OAuth 機制，只是這裡登入的「使用者」是你這個開發者，管的是整個專案。

## Step 5：建立你的第一個 Firebase 專案

> 這步在 **Firebase Console 網頁上操作**，由你自己點。以下是每一步會看到什麼。

1. 打開 [console.firebase.google.com](https://console.firebase.google.com/)，用剛才那個 Google 帳號登入。
2. 點 **「建立專案 / Create a project」**。
3. 輸入專案名稱，例如 `my-first-firebase`。Firebase 會自動幫你產生一個**全域唯一的專案 ID**，像 `my-first-firebase-a1b2c`——記住這個 ID，CLI 和程式碼都用它認你的專案（名稱可以改，**ID 建立後不能改**）。
4. 問你要不要開 **Google Analytics**：這門課用不到，**選關掉**（可以之後再開），流程比較單純。
5. 等十幾秒，專案就建好了，進到專案總覽頁。

此刻，你在 Google 雲端已經有一個真實存在的後端了——只是裡面還是空的。

## Step 6：驗證本機真的看得到這個專案

回到終端機，這是這章的高潮——證明「本機 CLI」和「雲端專案」這座橋通了：

```bash
firebase projects:list
```

如果一切正確，你會看到一個表格，裡面**有你剛剛建的那個專案**：

```
✔ Preparing the list of your Firebase projects
┌──────────────────────┬──────────────────────────┬────────────────┐
│ Project Display Name │ Project ID               │ Resource ...   │
├──────────────────────┼──────────────────────────┼────────────────┤
│ my-first-firebase    │ my-first-firebase-a1b2c  │ ...            │
└──────────────────────┴──────────────────────────┴────────────────┘
```

> 上面這段輸出是**示意格式**（我這環境沒有登入你的帳號，無法真的列出你的專案）。你在自己電腦跑時，`Project ID` 那欄會是你剛建的真實 ID。看到它出現，就代表整條鏈路——Node → CLI → 你的 Google 帳號 → 你的雲端專案——全部接上了。

如果這裡**沒看到**你的專案，往下看踩雷集錦第 3 條。

## 踩雷集錦

1. **把 CLI 和 SDK 搞混**：`npm install -g firebase-tools` 裝的是**命令列工具**（終端機用的 `firebase` 指令）；之後 Ch 3 的 `npm install firebase` 裝的是**程式碼用的函式庫**。兩個不一樣，都要裝，別以為裝一個就好。
2. **以為專案名稱就是專案 ID**：名稱是給人看的、可以重複、可以改；**專案 ID 才是機器認的、全域唯一、不可改**。CLI 的 `--project` 參數、程式碼的 config，用的都是 ID。建專案時記下那個 ID。
3. **`firebase projects:list` 看不到剛建的專案**：最常見原因是**登入的 Google 帳號**和**建專案的 Google 帳號不是同一個**。用 `firebase login:list` 看 CLI 登入的是誰，對照 Console 右上角的帳號。不一致就 `firebase logout` 再 `firebase login` 選對帳號。
4. **`firebase: command not found`（裝完卻找不到指令）**：`npm install -g` 的全域 bin 目錄不在系統 PATH 裡。關掉終端機重開一個新的（PATH 更新常要重開才生效）；還是不行就 `npm config get prefix` 看全域安裝路徑，確認它在 PATH 中。
5. **Node 版本太舊**：如果 `firebase` 指令報 `Node.js version ... is no longer supported`，是 Node 太舊。firebase-tools 15.x 要求 Node 20+。升級 Node 即可。

## 進階：再往深一層

- **多帳號切換**：如果你有公司帳號和私人帳號各自的 Firebase 專案，`firebase login:add` 可以加第二個帳號，之後用 `firebase use --account xxx@gmail.com` 或每個指令加 `--account` 切換，不用一直登出登入。
- **CI 環境登入**：在自動化流程（GitHub Actions 之類）裡沒有瀏覽器可以互動登入，這時改用 `firebase login:ci` 產生一組 token，或用 service account 金鑰。這在 Ch 20 部署自動化時會再碰到。
- **`.firebaserc` 是什麼**：等 Ch 3 你在專案資料夾 `firebase init` 之後，會多出一個 `.firebaserc` 檔，裡面記著「這個資料夾預設對應哪個雲端專案 ID」。它就是把「本機資料夾」和「雲端專案」綁定的那張便條紙。

## 本章重點整理

- Firebase 開發橫跨本機與雲端，三個角色分工：**Console**（網頁點）、**CLI**（終端機打）、**JS SDK**（程式碼 import）。
- CLI 和 SDK 都叫 firebase 但完全不同：一個管專案，一個在網頁裡讀寫資料。
- **專案 ID**（全域唯一、不可改）才是機器認你專案的鑰匙，不是專案名稱。
- `firebase projects:list` 能列出你的專案，就證明本機到雲端的橋通了。

## 自我檢核

- [ ] 我能說出 Firebase CLI 和 Firebase JS SDK 的差別，以及各自在什麼時候用
- [ ] 我知道「專案名稱」和「專案 ID」哪個不能改、哪個是機器認的
- [ ] 我的終端機 `firebase login:list` 顯示的帳號，和 Console 右上角的帳號是同一個
- [ ] 我跑 `firebase projects:list` 看得到自己剛建的專案
- [ ] 如果 `firebase: command not found`，我知道第一件事是重開終端機

## 延伸閱讀

### 官方文件

- **[Firebase CLI Reference](https://firebase.google.com/docs/cli)**
  - **讀哪裡**：開頭的「Install the Firebase CLI」和「Log in and test the Firebase CLI」兩節，就是本章 Step 2、4 的官方版本。
  - **能學到什麼**：CLI 全部指令的權威清單；之後忘記某個指令的參數，這裡查。
  - **前提**：本章讀完即可。

- **[Add Firebase to your project — Console 篇](https://firebase.google.com/docs/web/setup)**
  - **讀哪裡**：只看「Create a Firebase project」那一段，對照本章 Step 5。後面 SDK 的部分 Ch 3 會用到，先跳過。
  - **能學到什麼**：官方建專案流程的截圖，和你在 Console 看到的畫面對照。

### 部落格 / 影片

- **[Fireship — Firebase in 100 Seconds](https://www.youtube.com/watch?v=vAoB4VbhRzM)** — Jeff Delaney
  - **這支說什麼**：100 秒讓你對「Firebase 是一整套雲端後端」有個鳥瞰印象。裝環境的空檔看正好。
  - **為什麼值得看**：作者是前 Firebase 官方關係工程師，濃縮能力極強。內容和 Ch 1 互補。

環境備齊了，但你現在對「Firebase 到底幫我做了什麼」可能還很模糊。下一章我們先退一步，把「BaaS 是什麼、它替我省掉了哪些原本要自己做的苦工」講清楚——理解了這個，你才知道後面每個功能是在解決什麼問題。

→ [Ch 1 — Firebase 是什麼：BaaS 全貌](./01-what-is-firebase.md)
