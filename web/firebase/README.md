# Firebase 學習筆記：不自己架後端，做出有登入與即時資料庫的 Web App

> 給會基本程式、看得懂 HTML/JS，但沒碰過 Firebase 或這類雲端後端（BaaS）的人。

這系列帶你從「Firebase 到底是什麼、解決什麼問題」開始，一路做到一個**有登入、即時同步、檔案上傳、安全規則正確、而且部署上線**的完整 Web 小應用。用純 HTML + JavaScript，瀏覽器直接跑，最快感受到「我的網頁真的連上雲端後端了」。學完你不只會用，還說得出它底層怎麼運作。

## 為什麼學這個？

- **實用角度**：一個人也能做出有帳號系統、即時多人同步資料的 App，不用自己寫伺服器、管資料庫、租主機。做 side project、MVP、Hackathon 的速度差一個量級。
- **底層理解的價值**：Firebase 把「client 直接連資料庫」這件看似危險的事變安全，靠的是一套安全規則模型和 token 機制。搞懂它，你會對「認證 vs 授權」「client/server 信任邊界」這些跨技術的核心概念有真實的掌握。
- **職涯角度**：BaaS（Firebase、Supabase、AWS Amplify）是現代前端/全端工程師的標配技能之一；而 Firestore 的 NoSQL 建模思維，也會逼你重新理解「資料該怎麼設計」。

## 先修知識

- **基本程式概念**（變數、函式、非同步 async/await）（程度：寫過任一種語言即可）
- **HTML / JavaScript 語法**（程度：看得懂、改得動，不需精通）
- **命令列基本操作**（程度：會在終端機打指令、cd 進資料夾）
- 沒有也沒關係的：後端經驗、資料庫經驗、雲端經驗——這門課會從零建立

## 課程地圖

### Part 1 — 地基與心智模型（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 Firebase 是什麼：BaaS 全貌](./01-what-is-firebase.md)
- [Ch 2 Firebase 生態全景](./02-firebase-ecosystem.md)
- [Ch 3 第一次連上：Console、config、hello world](./03-first-connection.md)

### Part 2 — Authentication 登入（Ch 4–7）
- [Ch 4 認證原理：身分、授權、ID token / JWT](./04-auth-principles.md)
- [Ch 5 Email/密碼登入實作](./05-email-password-auth.md)
- [Ch 6 第三方登入（Google）與匿名登入](./06-oauth-anonymous.md)
- [Ch 7 登入狀態管理](./07-auth-state.md)
- [練習 A：完整登入頁](./practice-a-login-page.md)

### Part 3 — Firestore 即時資料庫（Ch 8–14）
- [Ch 8 Firestore 是什麼：document/collection 模型](./08-firestore-model.md)
- [Ch 9 寫入資料](./09-firestore-write.md)
- [Ch 10 讀取與查詢](./10-firestore-read-query.md)
- [Ch 11 即時同步：onSnapshot 與底層機制](./11-firestore-realtime.md)
- [Ch 12 NoSQL 資料建模](./12-firestore-data-modeling.md)
- [Ch 13 索引](./13-firestore-indexes.md)
- [Ch 14 交易與批次寫入](./14-firestore-transactions.md)
- [練習 B：即時留言板](./practice-b-realtime-board.md)

### Part 4 — Security Rules 安全規則（Ch 15–18）
- [Ch 15 為什麼需要安全規則](./15-why-security-rules.md)
- [Ch 16 規則語法與運作](./16-rules-syntax.md)
- [Ch 17 常見規則模式](./17-rules-patterns.md)
- [Ch 18 測試規則](./18-testing-rules.md)
- [練習 C：為 app 寫一整套安全規則](./practice-c-security-rules.md)

### Part 5 — Storage、Hosting 與上線（Ch 19–22）
- [Ch 19 Cloud Storage 檔案儲存](./19-cloud-storage.md)
- [Ch 20 Firebase Hosting 部署上線](./20-hosting.md)
- [Ch 21 Emulator Suite 本地開發](./21-emulator-suite.md)
- [Ch 22 計費與額度](./22-pricing-quotas.md)

### Part 6 — 整合
- [Final Project：即時筆記板（登入 + 即時資料 + 上傳 + 安全規則 + 上線）](./final-project-realtime-notes.md)

## 學習方式建議

1. **讀完一章就動手**：Firebase 是「做中學」型的技術，光讀不打開瀏覽器你不會有感覺。每章的範例都貼上去跑一次。
2. **開著 Console 看**：寫入資料時，把 Firebase Console 開在旁邊，親眼看資料跳出來——這個「連上了」的即時回饋是學習動力來源。
3. **故意把它弄壞**：把安全規則寫成 `allow read, write: if false`，看你的 App 怎麼被擋；把 API key 打錯，看它怎麼報錯。失敗的訊息比成功的畫面更有教學價值。
4. **用 Emulator 練，不燒額度**：Part 5 會教本地模擬器，之後所有實驗都可以在本地跑，不碰真實帳單。

## 精選資料庫

這裡列的是整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[Firebase 官方文件](https://firebase.google.com/docs)**
  - 整門課的權威來源。行為和預期不符時，這裡是最終仲裁。特別常回去的是 Firestore、Authentication、Security Rules 三個 section。
- **[Firebase JS SDK API Reference](https://firebase.google.com/docs/reference/js)**
  - v9+ 模組化 API 的完整函式清單。忘記某個函式從哪個 package import、參數是什麼，查這裡。

### 推薦影片 / 部落格

- **[Fireship — Firebase 教學系列](https://www.youtube.com/c/Fireship)** — Jeff Delaney
  - 前 Firebase 官方關係工程師，短影片把觀念講得極清楚。適合每學一塊前先看 5 分鐘建立直覺。
- **[The Firebase Blog](https://firebase.blog/)** — Firebase 官方團隊
  - 新功能、最佳實踐、底層機制解析的一手來源。

### 讀完本課之後

- **[Cloud Firestore 資料模型設計文件](https://firebase.google.com/docs/firestore/data-model)**（把 NoSQL 建模推得更深，含大型應用的分片與聚合策略）
- **[Firebase Extensions](https://firebase.google.com/products/extensions)**（預建好的常見功能模組，學完基礎後用來加速）

## 環境與版本釘定

- **Firebase JS SDK**：v12.18.0（modular / v9+ 風格）
- **firebase-tools（CLI）**：15.28.1
- **Node.js**：v22.20.0
- 全程在 Windows 上以瀏覽器 + Node 驗證。Auth 相關程式碼以 **Firebase Auth Emulator** 實跑驗證（不需 Java）；Firestore 相關以 **Firestore Emulator** 驗證；需要真實 Google OAuth、真實 Console 畫面的地方會明確標「由你在自己環境操作」。

> Firebase 的 SDK 版本迭代很快，v8（命名空間式 `firebase.auth()`）和 v9+（模組化 `import { getAuth }`）寫法差很多。**本課全用 v9+ 模組化寫法**，這是官方現行推薦。你在網路上看到 `firebase.auth().signIn...` 那種是舊寫法，別混用。
