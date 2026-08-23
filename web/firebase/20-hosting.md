# Ch 20 — Firebase Hosting 部署上線

> **目標**：把你到現在都只在本機跑的網頁**部署到網際網路**——用 Firebase Hosting，一行指令推上全球 CDN，得到一個真實的 HTTPS 網址，任何人都能打開。學會 `firebase init` 初始化、`firebase deploy` 部署、以及版本回滾。

> **環境**：firebase-tools 15.28.1、Node v22.20.0。CLI 指令與設定檔以本課環境驗證；實際 `deploy` 需登入你的專案並會產生公開網址，由你操作，本章標注哪些步驟是你在自己環境跑。

## 為什麼需要這個？

你的 `index.html` 一直是雙擊在本機開（`file://`），或用本地伺服器跑（`localhost`）——這些**只有你看得到**。要讓別人用你的 App，它得掛在一個公開網址上。Firebase Hosting 就是做這個的：專門託管網頁前端（HTML/CSS/JS）的服務，一行指令部署、自動 HTTPS、全球 CDN 加速、還和你的 Firebase 專案天然整合。

## 先建立直覺：Hosting 放的是「前端」，不是「後端」

先澄清一個常見混淆：

```
   Firebase Hosting 放的是                Firebase Hosting 不放
   ────────────────────                 ──────────────
   你的 HTML / CSS / JS 檔案              後端伺服器程式（那是 Functions）
   圖片、字型等靜態資源                    資料庫（那是 Firestore）
   （＝使用者瀏覽器要載入的東西）           使用者上傳的檔案（那是 Storage）
```

Hosting 是**靜態網站託管**——它把你的前端檔案放到全球的 CDN 節點，使用者連進來時從最近的節點快速載入。你的「後端」（登入、資料庫、檔案）本來就是 Firebase 那些服務，前端透過 SDK 直連它們（Ch 1 的架構）。所以 Hosting 只負責「把前端送到使用者瀏覽器」這一段。

> **為什麼用 Firebase Hosting 而不是隨便找個空間？** 三個好處：① 一行指令部署、自動 HTTPS 憑證（不用自己搞）；② 全球 CDN（使用者在哪都快）；③ 和你的 Firebase 專案同一個地方管理，且你的網域自動被加進 Auth 授權網域（Ch 6 的 Google 登入白名單）。對 Firebase App 來說是最順的選擇。

## Step 1：初始化 Hosting

> 這步在你的專案資料夾操作，由你自己跑。

在你放 `index.html` 的資料夾（或它的上層）執行：

```bash
firebase init hosting
```

它會互動式地問幾個問題：

```
? Please select an option:  Use an existing project
? Select a default Firebase project:  你的專案 (my-first-firebase-a1b2c)
? What do you want to use as your public directory?  public
? Configure as a single-page app (rewrite all urls to /index.html)?  No
? Set up automatic builds and deploys with GitHub?  No
```

幾個關鍵回答：

- **public directory（公開目錄）**：你要部署的檔案放哪個資料夾。預設 `public`——它會建一個 `public/` 資料夾，你要部署的 `index.html` 等要放進去。
- **single-page app**：如果你用 React/Vue 這種前端路由（所有網址都導向 index.html）選 Yes；純多頁 HTML 選 No。本課的單頁範例選 No 或 Yes 都行。
- **GitHub 自動部署**：進階功能（每次 push 自動部署），先 No。

完成後你的資料夾多出：

```
firebase.json      ← Hosting 設定（哪個資料夾、重寫規則等）
.firebaserc        ← 綁定哪個雲端專案（Ch 0 提過）
public/            ← 你要部署的檔案放這（裡面預設有個 index.html 範例）
```

`firebase.json` 的 hosting 部分長這樣：

```json
{
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"]
  }
}
```

`"public": "public"` 就是「部署 `public/` 資料夾裡的東西」。把你的 `index.html`（含 Firebase config）放進 `public/`。

## Step 2：部署

> 這步由你在登入狀態下跑，會產生公開網址。

```bash
firebase deploy --only hosting
```

`--only hosting` 表示只部署 Hosting（不碰 Firestore 規則、索引等）。真實輸出大致是（示意格式，實際網址是你的專案）：

```
=== Deploying to 'my-first-firebase-a1b2c'...

i  deploying hosting
i  hosting[my-first-firebase-a1b2c]: beginning deploy...
i  hosting[my-first-firebase-a1b2c]: found 3 files in public
✔  hosting[my-first-firebase-a1b2c]: file upload complete
✔  hosting[my-first-firebase-a1b2c]: release complete

✔  Deploy complete!

Hosting URL: https://my-first-firebase-a1b2c.web.app
```

**那個 `Hosting URL` 就是你的網站的公開網址。** 打開它——你的 App 現在在網際網路上，自動有 HTTPS，任何人都能連。你剛剛把一個有登入、即時資料庫的完整 App 上線了。

每個專案預設給兩個網域：`專案.web.app` 和 `專案.firebaseapp.com`，兩個都指向同一個網站。

## Step 3：部署後的驗證清單

上線後花五分鐘檢查這些，避免「本機好好的、上線壞掉」：

1. **Google 登入還能用嗎**：Hosting 的網域會自動加進 Auth 授權網域，所以 Google 登入應該直接可用（Ch 6 說的白名單）。若不行，去 Authentication → Settings → 授權網域確認你的網域在列。
2. **Firestore 讀寫正常嗎**：這是驗證「規則」和「索引」的時刻——如果某個查詢在本機模擬器能跑、上線卻報 `requires an index`（Ch 13 的大坑！），現在會現形。把所有功能點一遍。
3. **config 是對的嗎**：確認部署的 `index.html` 裡是**正式專案**的 config，不是模擬器設定。
4. **安全規則部署了嗎**：`firebase deploy --only hosting` **不會**部署規則！規則要另外 `firebase deploy --only firestore:rules`（下面詳述）。

## 別忘了部署規則和索引

**這是超常見的上線事故**：你部署了 Hosting，網站上線了，但**安全規則還是 Test mode 或舊版**——因為 `deploy --only hosting` 不碰規則。你的資料就裸奔了（Ch 15）。

規則和索引要各自部署：

```bash
firebase deploy --only firestore:rules     # 部署 firestore.rules
firebase deploy --only firestore:indexes   # 部署 firestore.indexes.json（Ch 13）
firebase deploy --only storage             # 部署 storage.rules（Ch 19）

# 或全部一起部署：
firebase deploy
```

**上線前的正確流程**：確認 `firestore.rules` 是你 Part 4 寫好測過的版本 → `firebase deploy`（部署全部：hosting + rules + indexes）→ 跑一遍功能驗證。**別只 deploy hosting 就以為上線完成了。**

## 版本管理與回滾

Firebase Hosting 保留**每次部署的歷史版本**。如果新版部署後發現壞了，可以**一鍵回滾**到上一個好的版本：

- 在 Console 的 **Hosting** 頁，能看到所有部署版本的列表，每個旁邊有「回復（Rollback）」按鈕，點下去立刻切回那個版本。
- 這讓部署很安全——出事能秒退，不用手忙腳亂重新部署舊碼。

> **預覽頻道（preview channels）**：`firebase hosting:channel:deploy preview` 能把一個版本部署到一個**臨時預覽網址**（不影響正式站），有效期可設。適合「給人看看新版、或 PR review」而不動到線上。進階但很實用。

## 踩雷集錦

1. **只 `deploy --only hosting`，忘了部署規則**（最危險）：網站上線了但規則沒更新，資料庫可能還是 Test mode 裸奔。上線用 `firebase deploy`（全部）或記得單獨 deploy rules。
2. **把 `index.html` 放錯資料夾**：Hosting 部署的是 `public`（或你 init 時設的目錄）裡的東西。放在外面不會被部署。確認檔案在 `public/` 裡。
3. **部署的是模擬器版 config / 本機測試碼**：上線的 `index.html` 要用正式專案 config，別把 `connectFirestoreEmulator`（Ch 21）之類的本機設定帶上線。
4. **本機能跑、上線 `requires an index`**：模擬器不強制索引（Ch 13 大坑），上線才炸。所以上線後一定要把所有查詢功能點一遍，或事先部署好 `firestore.indexes.json`。
5. **以為 Hosting 能跑後端程式**：Hosting 只放靜態前端。要跑伺服器邏輯是 Cloud Functions，不是 Hosting。別想著「把 Node 後端部署到 Hosting」。
6. **SPA 路由設定錯**：用 React/Vue 前端路由卻在 init 時 single-page app 選 No，重新整理子頁面會 404。前端路由要選 Yes（把所有網址 rewrite 到 index.html）。

## 進階：再往深一層

- **自訂網域**：Hosting 頁能加你自己的網域（`myapp.com`），Firebase 幫你設定 DNS 和自動 HTTPS 憑證。免費、幾步驟完成。上線給真實使用者時通常會綁自己的網域而非 `.web.app`。
- **Hosting + Functions 整合**：Hosting 可以把特定路徑（如 `/api/**`）**重寫（rewrite）**到 Cloud Functions，讓你的靜態前端和動態 API 在同一個網域下。這是「前端靜態 + 少量後端邏輯」的常見架構，也解決了 CORS。是你學完本課、需要後端邏輯時的下一步。
- **快取控制**：Hosting 預設對靜態資源設快取標頭。你可以在 `firebase.json` 的 `headers` 自訂——例如讓 `index.html` 不快取（每次拿最新）、但 JS/CSS（帶版本 hash）長快取。影響使用者多快看到你的更新。
- **CI/CD 自動部署**：`firebase init hosting` 時選 GitHub 整合，或用 `firebase deploy` 搭配 GitHub Actions（`FirebaseExtended/action-hosting-deploy`），每次 push 自動部署 + PR 自動產生預覽網址。專業團隊的標配。
- **`.web.app` vs `.firebaseapp.com`**：兩個都給你，`.web.app` 較新較短。功能一樣，選一個對外用即可。

## 本章重點整理

- Firebase Hosting 託管**靜態前端**（HTML/CSS/JS），一行指令部署、自動 HTTPS、全球 CDN；後端仍是 Firestore/Auth/Storage 那些服務。
- 流程：`firebase init hosting`（設定 public 目錄）→ 把檔案放 `public/` → `firebase deploy --only hosting` → 得到 `專案.web.app` 網址。
- **`deploy --only hosting` 不部署規則**——上線用 `firebase deploy`（全部）或記得單獨部署 `firestore:rules`，否則資料裸奔。
- 上線後**務必點一遍功能**驗證規則、索引（模擬器不強制索引的大坑會在這裡現形）。
- Console 可**一鍵回滾**到舊版本；預覽頻道可部署不影響正式站的臨時版。

## 自我檢核

- [ ] 我能說出 Hosting 放什麼、不放什麼（靜態前端 vs 後端/資料庫）
- [ ] 我能完成 `firebase init hosting` → 放檔案 → `deploy` 拿到公開網址的流程
- [ ] 我知道 `deploy --only hosting` 不含規則，上線要記得部署規則
- [ ] 我知道上線後要驗證規則和索引，以及為什麼（模擬器不強制索引）
- [ ] 我知道出事能在 Console 一鍵回滾
- [ ] 我不會把 Hosting 當成能跑後端程式的地方

## 延伸閱讀

### 官方文件

- **[Get started with Firebase Hosting](https://firebase.google.com/docs/hosting/quickstart)**
  - **讀哪裡**：整篇，`init` 到 `deploy` 的官方流程，對照本章 Step 1–2。
  - **能學到什麼**：本章部署流程的權威版，含 `firebase.json` 各設定。
  - **前提**：本章讀完即可。

- **[Configure Hosting behavior（rewrites/headers/redirects）](https://firebase.google.com/docs/hosting/full-config)**
  - **讀哪裡**：`rewrites`（SPA、導向 Functions）和 `headers`（快取）那幾節。
  - **能學到什麼**：本章進階提到的 SPA 路由、Functions 整合、快取控制的設定寫法。

- **[Deploy to live and preview channels](https://firebase.google.com/docs/hosting/test-preview-deploy)**
  - **讀哪裡**：preview channels 那段。
  - **能學到什麼**：本章進階的預覽頻道完整用法，適合團隊 review。

### 文章

- **[The Firebase Blog — Hosting + CI/CD 自動部署](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "hosting github actions"，講自動部署與預覽網址。
  - **為什麼值得讀**：把「手動 deploy」升級成「push 自動部署」，本章進階的實戰版。

你的 App 上線了！但每次改東西都部署到正式環境、燒真實額度、還可能影響真實資料——開發時這樣很危險也很慢。下一章介紹本課驗證一路都在用的祕密武器：**Emulator Suite**（本地模擬器），讓你在本機完整開發測試，不碰雲端、不花錢。

→ [Ch 21 — Emulator Suite 本地開發](./21-emulator-suite.md)
