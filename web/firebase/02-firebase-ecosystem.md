# Ch 2 — Firebase 生態全景

> **目標**：把 Firebase 的服務地圖攤開，知道每個服務管什麼、彼此怎麼分工，以及這門課為什麼選 Auth / Firestore / Storage / Hosting 當主線。讀完你打開 Console 左側那一長排功能時，每個名字你都知道是幹嘛的，不會亂點。

## 為什麼需要這個？

你第一次打開 Firebase Console，左側選單會列出十幾個服務：Authentication、Firestore Database、Realtime Database、Storage、Hosting、Functions、Messaging、Remote Config、Crashlytics、Analytics、App Check、Extensions……

面對這一排名字，新手最常見的兩種反應都不好：一種是**每個都想學**，結果什麼都學不深；另一種是**只認得 Firestore**，其他一律無視，錯過了能大幅省事的工具。

正確的做法是：先有一張**分類地圖**，知道這些服務大致分成幾群、各群解決什麼問題，然後**照需求選用**。這章給你那張地圖。

## 先建立直覺：Firebase 服務分成三大群

不要把十幾個服務當成十幾個獨立的東西背。它們其實可以歸成三群，對應「做一個 App」的三個層面：

```
┌─────────────────────────────────────────────────────────────┐
│  第 1 群：Build（打造功能）— 你的 App 靠這些「動起來」        │
│  ─────────────────────────────────────────────────────       │
│   Authentication   使用者是誰（登入）      ★本課             │
│   Cloud Firestore  資料存哪（即時資料庫）  ★本課             │
│   Realtime DB      另一個較舊的資料庫                         │
│   Cloud Storage    檔案存哪（圖片/影片）    ★本課             │
│   Cloud Functions  跑你的後端邏輯（雲端函式）                 │
│   Hosting          網站放哪（部署）        ★本課             │
│   App Check        擋掉不是你 App 的請求                      │
├─────────────────────────────────────────────────────────────┤
│  第 2 群：Engage（經營使用者）— 讓 App「留得住人」            │
│  ─────────────────────────────────────────────────────       │
│   Cloud Messaging  推播通知（FCM）                            │
│   Remote Config    不改版就換設定（開關功能/A-B 測試）        │
│   In-App Messaging App 內彈訊息                               │
├─────────────────────────────────────────────────────────────┤
│  第 3 群：Monitor（觀測品質）— 讓你「知道 App 出了什麼事」    │
│  ─────────────────────────────────────────────────────       │
│   Crashlytics      當機回報                                   │
│   Performance      效能監控                                   │
│   Google Analytics 使用者行為分析                             │
└─────────────────────────────────────────────────────────────┘
```

一句話記住三群：**Build 讓 App 能動、Engage 讓 App 留人、Monitor 讓你知道 App 好不好。** 這門課全部聚焦在第 1 群「Build」，因為那是「做出一個能動的 App」的核心；而 Build 群裡我們再挑四個最基礎的當主線。

## 本課主線四服務：各自的角色

用「做一個部落格 App」當例子，看這四個怎麼協作：

```
  使用者打開你的部落格 App
        │
        ├─ 「你是誰？」──────────▶  Authentication（登入，確認身分）
        │
        ├─ 「你的文章在哪？」──────▶  Firestore（存文章標題/內文/時間，即時同步）
        │
        ├─ 「文章的封面圖在哪？」──▶  Cloud Storage（存圖片檔案本身）
        │
        └─ 「這個網站掛在哪？」────▶  Hosting（把 HTML/JS 部署到全球 CDN）
```

| 服務 | 負責 | 一句類比 | 本課章節 |
|---|---|---|---|
| **Authentication** | 確認「使用者是誰」 | App 的門口保全，發識別證 | Part 2（Ch 4–7） |
| **Cloud Firestore** | 存「結構化資料」，且能即時同步 | App 的雲端資料櫃，還會自動廣播誰動了資料 | Part 3（Ch 8–14） |
| **Cloud Storage** | 存「大檔案」（圖片、影片、PDF） | App 的雲端倉庫，放搬不進資料庫的大東西 | Part 5（Ch 19） |
| **Hosting** | 把你的網站「放上線」 | App 的門牌與地基，給世界一個網址連進來 | Part 5（Ch 20） |

> **為什麼資料庫和檔案要分兩個服務？** 因為它們的形狀完全不同。Firestore 適合存**小而結構化**的資料（一篇文章的標題、作者、時間戳，幾 KB）；圖片影片是**大而非結構化**的二進位檔（幾 MB 甚至更大）。把大檔硬塞進資料庫又貴又慢，所以 Firebase 把「結構化資料」交給 Firestore、「大檔」交給 Storage，資料庫裡只存一個「這張圖在 Storage 的哪個路徑」的字串連結。這個分工你在 Ch 19、以及 Final Project 會實際用到。

## 這門課刻意**不**教的（以及為什麼）

好的課程要說清楚邊界。以下服務本課**不深入**，但你該知道它們存在、什麼時候回來學：

- **Cloud Functions（雲端函式）**：讓你在雲端跑自己的後端程式碼（例如「使用者註冊後自動寄歡迎信」「每天半夜整理資料」）。它很重要，但屬於「當純前端邏輯不夠用時」才需要的進階工具，且**要收費方案（Blaze）才能用**。本課先讓你把「前端直連」的能力練扎實；Functions 是你學完這門課後自然的下一步。
- **Realtime Database（RTDB）**：Firebase **最早**的資料庫，比 Firestore 老。它也能即時同步，但查詢能力弱、資料結構是一棵大 JSON 樹，容易長歪。**新專案官方建議用 Firestore**（Ch 8 會講兩者差異）。本課只教 Firestore；知道 RTDB 存在、且知道「除非有特殊理由否則選 Firestore」就夠了。
- **Cloud Messaging（FCM）推播、Remote Config、Analytics 等**：屬於第 2、3 群，是「App 上線後經營」的工具，不是「把 App 做出來」的核心。學完本課、有了真實 App 之後再回來加。

> **一個原則**：這門課的目標是讓你**先能獨立做出一個完整、安全、上線的即時 App**。把這條主線走完，比什麼都碰一點更有價值。碰過主線之後，其他服務對你都只是「查文件就會用」的延伸。

## 服務之間怎麼串起來：一條真實請求

以「使用者上傳頭像」為例，看多個服務如何協作（這正是 Final Project 會做的）：

```
1. 使用者在瀏覽器選了一張圖，按上傳
        │
2. App 先確認登入：auth.currentUser 有值嗎？   ← Authentication
        │  （沒登入就擋下來）
        ▼
3. 把圖片檔上傳到 Storage 的 avatars/{uid}.jpg  ← Cloud Storage
        │  （Storage 安全規則檢查：這個 uid 只能寫自己的路徑）
        ▼
4. 上傳成功，拿到一個下載網址 (download URL)
        │
5. 把這個網址字串寫進 Firestore 的               ← Firestore
   users/{uid} 文件的 avatarUrl 欄位
        │  （Firestore 安全規則檢查：只能改自己的文件）
        ▼
6. 因為別的畫面在監聽這份文件，頭像即時更新       ← Firestore 即時同步
```

注意這條鏈裡**安全規則出現了兩次**（Storage 一次、Firestore 一次）——這再次印證 Ch 1 說的：Firebase 的資安防線就是「每個服務各自的安全規則」。這也是為什麼 Part 4 值得花四章專講。

## Console 導覽：你會在哪裡設定這些

Console（[console.firebase.google.com](https://console.firebase.google.com/)）是你設定這些服務的地方。左側選單的排列大致對應上面的分群。這門課你最常去的四個位置：

| Console 位置 | 你在這裡做什麼 |
|---|---|
| **Build → Authentication** | 開啟登入方式（Email、Google…）、看已註冊的使用者清單 |
| **Build → Firestore Database** | 建立資料庫、**用眼睛看資料**、寫安全規則、建索引 |
| **Build → Storage** | 建立儲存桶、看上傳的檔案、寫 Storage 安全規則 |
| **專案設定（齒輪）→ 一般** | 拿到你的 **Firebase config**（Ch 3 程式碼要用的那串金鑰） |

> **踩雷預告**：很多服務**第一次用要先在 Console 手動「啟用」**。例如 Firestore 你得先按「建立資料庫」選地區，才能開始用；Authentication 你得先開啟某個登入方式，程式碼呼叫才不會報錯。不是裝了 SDK 就自動好——Console 這一側的「開關」要先打開。這是新手最常見的卡點之一。

## 對比與取捨：Firebase 的兩個資料庫

因為你一定會在 Console 看到兩個資料庫選項，先把差別講清楚，免得選錯：

| | **Cloud Firestore**（本課用） | **Realtime Database**（較舊） |
|---|---|---|
| 資料模型 | document / collection（像資料夾與檔案） | 一棵大 JSON 樹 |
| 查詢能力 | 較強（複合查詢、排序） | 較弱 |
| 即時同步 | 有 | 有 |
| 收費模型 | 按讀寫**次數** | 按**下載流量** |
| 官方建議 | **新專案首選** | 特定低延遲場景才考慮 |
| 適合 | 絕大多數應用 | 極高頻的簡單狀態同步（如遊戲即時位置） |

結論很簡單：**沒有特殊理由就用 Firestore**。本課接下來講的「資料庫」都是指 Firestore。

## 踩雷集錦

1. **想一次學完所有服務**：十幾個服務不是設計來一起學的。它們是「工具箱」，用到再拿。先把 Build 群的四個主線走完，其餘查文件即可。
2. **搞混 Firestore 和 Realtime Database**：兩個都叫「資料庫」、都能即時同步，但資料模型、查詢、計費全不同。新專案選 Firestore；看到舊教學用 `getDatabase()`／`ref()`／`onValue()` 那是 RTDB，和本課的 Firestore API 不一樣，別跟錯。
3. **以為裝了 SDK 服務就能用**：多數服務要先在 Console **手動啟用**（建資料庫、開登入方式）。程式碼報 `permission-denied` 或 `configuration-not-found` 時，先回 Console 檢查那個服務開了沒。
4. **把大檔存進 Firestore**：Firestore 單一文件有 **1 MiB 上限**，且按文件讀寫次數計費，不是設計來存圖片影片的。大檔一律進 Storage，Firestore 只存路徑/網址。搞錯這個會又貴又撞上限。
5. **忽略 App Check 就以為安全規則是全部**：安全規則管「這個使用者能不能做這件事」，App Check 管「這個請求是不是來自我真正的 App（而不是別人寫腳本直接打你的 API）」。本課聚焦安全規則，但你該知道 App Check 補的是另一個面向的洞。

## 進階：再往深一層

- **Firebase 之下是 Google Cloud**：Ch 1 提過，這裡具體一點——你在 Console 建的 Firestore、Storage、Functions，其實都是 Google Cloud 的資源，只是包了一層更簡單的介面。當你點某些進階設定，Console 會把你導去 Google Cloud Console。理解這層，你就不會被「同一個東西兩個後台」搞混。
- **Firebase Extensions**：預先打包好的常見功能模組（例如「自動把上傳的圖片壓縮成縮圖」「把 Firestore 資料同步到 BigQuery」），一鍵安裝。本課學完基礎後，Extensions 能讓你不重造輪子。逛一下 [extensions.dev](https://extensions.dev/) 看有哪些。
- **多個 App 共用一個專案**：一個 Firebase 專案可以同時掛 Web App、iOS App、Android App，它們**共用**同一個 Auth 使用者庫、同一個 Firestore 資料庫。這就是為什麼一個使用者能在網頁註冊、然後用手機 App 登入同一個帳號。

## 本章重點整理

- Firebase 服務分三群：**Build**（做出功能）、**Engage**（經營使用者）、**Monitor**（觀測品質）。本課全在 Build 群。
- 本課主線四服務：**Auth**（誰）、**Firestore**（結構化資料 + 即時）、**Storage**（大檔）、**Hosting**（上線）。
- 資料庫存**結構化小資料**，大檔進 **Storage**，資料庫只存連結——這個分工要記牢。
- 多數服務**要先在 Console 手動啟用**，不是裝 SDK 就好。
- 兩個資料庫選 **Firestore**（新專案首選）。

## 自我檢核

- [ ] 我能把 Firebase 服務歸成三群，並說出每群解決什麼問題
- [ ] 我能說出本課四個主線服務各自負責什麼
- [ ] 有人問我「圖片該存 Firestore 還是 Storage」，我能答對並解釋為什麼
- [ ] 我知道 Firestore 和 Realtime Database 的差別，以及新專案該選哪個
- [ ] 我知道「裝了 SDK 但服務報錯」時，第一件事是回 Console 看服務啟用了沒

## 延伸閱讀

### 官方文件

- **[Firebase 產品總覽（All products）](https://firebase.google.com/products)**
  - **讀哪裡**：整頁掃一遍，對照本章的三群分類。每個產品卡片有一句官方定位。
  - **能學到什麼**：官方對每個服務的一句話描述，補全本章沒展開的 Engage/Monitor 群。
  - **前提**：本章讀完即可。

- **[Cloud Firestore vs Realtime Database](https://firebase.google.com/docs/database/rtdb-vs-firestore)**
  - **讀哪裡**：整篇，特別是最後的決策表。
  - **能學到什麼**：兩個資料庫差異的權威版本，比本章的對照表更細（含資料結構、擴展性、計費的深入比較）。

### 影片

- **[Fireship — 10 Firebase Services in 100 Seconds 類影片](https://www.youtube.com/c/Fireship/search?query=firebase)** — Jeff Delaney
  - **這支說什麼**：快速掃過各服務用途，和本章的地圖互相印證。
  - **為什麼值得看**：幫你把「服務名字」和「用途」在腦中連起來，之後看 Console 選單不陌生。

地圖有了。下一章是這個 Part 的高潮——你會實際建立資料庫、拿到 config、寫下第一段真正連上 Firebase 的程式碼，並**親眼在 Console 看到你從網頁寫進去的資料跳出來**。這是「感受到它連上了」的那一刻。

→ [Ch 3 — 第一次連上：Console、config、hello world](./03-first-connection.md)
