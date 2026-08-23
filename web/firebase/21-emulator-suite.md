# Ch 21 — Emulator Suite 本地開發

> **目標**：學會用 Firebase Local Emulator Suite——在你自己電腦上跑 Auth、Firestore、Storage 的本地模擬版，讓你開發測試時**完全不碰雲端、不花錢、不污染真實資料**。這是本課驗證所有程式碼一路都在用的祕密武器。並認清一個關鍵陷阱：模擬器和正式環境的行為差異。

> **環境**：firebase-tools 15.28.1、Node v22.20.0。Firestore/Storage 模擬器需要 **Java**（本課用 Temurin JDK 21）；Auth 模擬器不需 Java。本章的模擬器啟動、連線、`emulators:exec` 都在此環境實跑驗證。

## 為什麼需要這個？

到目前為止，如果你都連著真實專案開發，你會遇到這些痛：

- **花錢/燒額度**：每次測試的讀寫都算進真實計費（Ch 22），開發階段反覆測試很浪費。
- **污染資料**：測試產生的垃圾資料混進真實資料庫，還要清。
- **慢**：每次操作都要往返雲端。
- **危險**：測試「刪除」功能不小心刪到真資料。
- **無法離線**：沒網路就不能開發。

**Emulator Suite** 解決全部——它在你本機跑一套 Firebase 服務的模擬版，你的 App 連本機模擬器而非雲端。讀寫瞬間、免費、資料隨時清空、斷網也能開發。**這就是為什麼本課能實跑驗證每一段 Auth/Firestore/規則程式碼**——全部跑在模擬器上。

## 先建立直覺：一套「本機版的 Firebase」

```
   正式開發（連雲端）                   用模擬器（連本機）
   ─────────────                      ─────────────
   你的 App ──▶ Google 雲端            你的 App ──▶ 127.0.0.1:8080 (本機模擬器)
              真實 Firestore                      本機 Firestore 模擬
              真實計費                            免費
              真實資料                            用完即丟
              要網路                              離線可跑
```

模擬器**幾乎完整重現** Firebase 服務的行為（尤其安全規則的評估和正式環境一致，Ch 16/18），所以你在模擬器上開發測試的東西，搬到正式環境大多直接能用——**除了幾個要命的差異**（下面詳述）。

## Step 1：初始化模擬器

```bash
firebase init emulators
```

會問你要模擬哪些服務、各用哪個 port：

```
? Which Firebase emulators do you want to set up?
   ◉ Authentication Emulator
   ◉ Firestore Emulator
   ◉ Storage Emulator
? Which port do you want to use for the auth emulator?       9099
? Which port for the firestore emulator?                     8080
? Which port for the storage emulator?                       9199
? Would you like to enable the Emulator UI?                   Yes
? Would you like to download the emulators now?              Yes
```

這會在 `firebase.json` 加上 `emulators` 設定：

```json
{
  "emulators": {
    "auth": { "port": 9099 },
    "firestore": { "port": 8080 },
    "storage": { "port": 9199 },
    "ui": { "enabled": true }
  }
}
```

## Step 2：啟動模擬器

```bash
firebase emulators:start
```

真實輸出（本課環境，節錄）：

```
✔  All emulators ready! It is now safe to connect your app.
┌────────────────┬────────────────┬─────────────────────────────────┐
│ Emulator       │ Host:Port      │ View in Emulator UI             │
├────────────────┼────────────────┼─────────────────────────────────┤
│ Authentication │ 127.0.0.1:9099 │ http://127.0.0.1:4000/auth      │
│ Firestore      │ 127.0.0.1:8080 │ http://127.0.0.1:4000/firestore │
└────────────────┴────────────────┴─────────────────────────────────┘
  Emulator Hub running at 127.0.0.1:4400
```

- 各服務跑在各自的 port。
- **Emulator UI**（`127.0.0.1:4000`）是一個網頁後台，像本機版的 Firebase Console——能看模擬器裡的資料、使用者、跑規則測試。開發時開著它很方便。
- `Ctrl+C` 停止（預設資料不保存，下次啟動是乾淨的）。

> **⚠️ Java 需求**：Firestore 和 Storage 模擬器是 Java 程式，**需要安裝 JDK**（Java 11+）。沒有 Java 會報 `Could not spawn 'java'`。裝一個 JDK（如 [Temurin](https://adoptium.net/)）並確保 `java` 在 PATH 上。**Auth 模擬器不需要 Java**——所以只測 Auth 時就算沒 Java 也能跑。（本課驗證環境本機沒有 Java，是另外裝了可攜式 JDK 21 才能跑 Firestore 模擬器。）

## Step 3：讓你的 App 連到模擬器

初始化 SDK 後，加幾行「連到模擬器」的程式碼——**只在本機開發時執行**：

```js
import { getAuth, connectAuthEmulator } from "firebase/auth";
import { getFirestore, connectFirestoreEmulator } from "firebase/firestore";
import { getStorage, connectStorageEmulator } from "firebase/storage";

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const storage = getStorage(app);

// 只在本機開發時連模擬器（用某個條件判斷，別讓它上線！）
if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
  connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
  connectFirestoreEmulator(db, "127.0.0.1", 8080);
  connectStorageEmulator(storage, "127.0.0.1", 9199);
}
```

加了這幾行，你的 App 的所有 Auth/Firestore/Storage 操作就打到本機模擬器，不碰雲端。這正是本課所有驗證的做法。

> **千萬別讓 `connect...Emulator` 上線**：用 `location.hostname` 之類的條件包起來，確保部署到正式環境（Ch 20）時**不會**執行這幾行——否則你上線的 App 會試圖連使用者電腦的 127.0.0.1（不存在），整個壞掉。Ch 20 踩雷第 3 條講的就是這個。

## emulators:exec：一鍵跑測試（本課的驗證方式）

`emulators:start` 是「啟動並掛著」。但要跑自動化測試（如 Ch 18 的規則測試），用 **`emulators:exec`**——它**啟動模擬器 → 跑你的指令 → 自動關閉**，一氣呵成：

```bash
firebase emulators:exec --only firestore "node my_test.mjs"
```

**本課驗證每一段 Firestore/規則程式碼，用的就是這個指令**——它保證每次測試都在乾淨的模擬器上跑、跑完自動清理，適合放進 CI（Ch 18 進階）。例如本課跑安全規則測試：

```bash
firebase emulators:exec --only firestore --project demo-rules "node rules_test.mjs"
```

`--project demo-xxx` 用一個 `demo-` 開頭的假專案 ID——這會讓模擬器用「demo 模式」，**保證不會誤連任何真實雲端服務**，是測試的安全網。

## ⚠️ 關鍵：模擬器和正式環境的差異

模擬器很像正式環境，但**不完全一樣**。這些差異踩到會讓你「本機全綠、上線炸掉」，務必牢記：

| 面向 | 模擬器 | 正式環境 | 影響 |
|---|---|---|---|
| **複合索引**（Ch 13） | **不強制**（查詢直接跑） | **強制**（要建索引） | 本機能跑的查詢，上線可能 `requires an index`。**最大的坑。** |
| **安全規則評估** | 和正式**一致** ✅ | — | 規則能放心在本機測（Ch 18） |
| **Auth 錯誤碼**（Ch 5） | `auth/wrong-password` | `auth/invalid-credential`（防列舉） | 錯誤處理要同時處理兩者 |
| **效能/延遲** | 本機極快 | 有網路往返 | 別用模擬器判斷正式效能 |
| **計費** | 免費、不計數 | 計費 | 別用模擬器估成本 |
| **某些進階功能** | 部分不支援 | 支援 | 少數功能要在真專案測 |

**最重要的一條**：**模擬器不強制複合索引**（Ch 13 實測驗證過）。你在模擬器開發時所有查詢都順，上線才發現要建一堆索引。避免方法：把索引寫進 `firestore.indexes.json` 一起部署（Ch 13/20），或上線前在真實測試專案跑一遍所有查詢。

> **認識論誠實**：因為這些差異，「在模擬器測過」**不等於**「正式環境沒問題」。模擬器是開發利器，但上線前**一定要在真實（測試用）專案至少跑一遍完整流程**，尤其是查詢和 Auth 錯誤處理。把模擬器當「日常開發」，把真專案當「上線前驗收」。

## Emulator UI：本機版 Console

啟動時的 `127.0.0.1:4000` 是模擬器的網頁後台，開發時很好用：

- **看資料**：Firestore 分頁能像 Console 一樣瀏覽、手動增改模擬器裡的資料。
- **看使用者**：Auth 分頁列出模擬器裡註冊的測試使用者，能手動加。
- **規則遊樂場**：試不同請求會被規則允許還是拒絕。
- **清空**：一鍵清掉所有模擬資料，重新開始。

## 踩雷集錦

1. **沒裝 Java 就啟動 Firestore 模擬器**：報 `Could not spawn 'java'`。Firestore/Storage 模擬器要 JDK；裝一個並確認 `java` 在 PATH。（只測 Auth 不用 Java。）
2. **`connect...Emulator` 忘了用條件包、跟著上線**：正式站會試圖連 127.0.0.1 而壞掉。一定要用 `location.hostname` 之類條件只在本機執行。
3. **以為模擬器測過就萬無一失**（最重要）：模擬器**不強制索引**、Auth 錯誤碼不同、不反映效能和成本。上線前務必在真專案驗收，尤其查詢和索引。
4. **port 衝突**：8080 是很多工具（其他 dev server）的常用 port。若被佔用，改 `firebase.json` 裡的 port（本課驗證時就遇過 8080 被別的程式佔，改用 8085）。
5. **模擬器資料以為會保存**：預設 `Ctrl+C` 後資料就沒了。要保存/載入用 `--export-on-exit` 和 `--import`（下面進階）。別以為模擬器是持久資料庫。
6. **用真實 projectId 跑測試**：測試用 `--project demo-xxx` 假專案，避免萬一連到真雲端。`demo-` 前綴會強制 demo 模式。

## 進階：再往深一層

- **資料持久化（export/import）**：`firebase emulators:start --export-on-exit=./emulator-data --import=./emulator-data`——結束時把模擬器資料存檔、下次啟動時載回。讓你有一組固定的測試資料（seed），不用每次重建。
- **connect 一次的注意事項**：`connectFirestoreEmulator` 等必須在**任何讀寫之前**呼叫，且對同一個實例只能連一次。在模組頂層、initializeApp 之後立刻連。
- **模擬 Functions 與觸發**：完整的 Emulator Suite 還能模擬 Cloud Functions 和它的觸發器（如「Firestore 文件建立時觸發某函式」），讓你在本機測整套「前端 + 後端函式 + 資料庫」的互動。本課不含 Functions，但知道模擬器涵蓋到這層。
- **接進測試框架 + CI**：`emulators:exec "npm test"` 把整套測試（規則測試 + 應用邏輯測試）在乾淨模擬器上跑，放進 GitHub Actions 就有了「每次 push 自動測」的保護。這是專業 Firebase 開發的標準工作流（Ch 18 進階）。
- **規則覆蓋率報告**：模擬器能產生安全規則的覆蓋率報告（`127.0.0.1:8080/emulator/v1/projects/<id>:ruleCoverage.html`），視覺化哪些規則被測到（Ch 18 進階）。

## 本章重點整理

- **Emulator Suite** 在本機跑 Firebase 服務的模擬版：開發測試**免費、瞬間、可清空、離線可跑、不碰真資料**。本課的所有驗證都靠它。
- 流程：`firebase init emulators` → `firebase emulators:start`（或 `emulators:exec` 跑測試自動起關）→ App 用 `connect...Emulator` 連本機（**要用條件包，別上線**）。
- Firestore/Storage 模擬器**需要 Java**；Auth 模擬器不需要。
- **關鍵差異**：模擬器**不強制複合索引**（本機能跑上線炸的最大坑）、Auth 錯誤碼不同、不反映效能與成本；但**安全規則評估和正式一致**。
- 模擬器是「日常開發」，上線前**務必在真實測試專案驗收**，尤其查詢與索引。

## 自我檢核

- [ ] 我能說出用模擬器開發的四個好處（免費/瞬間/可清/離線）
- [ ] 我能設定並啟動模擬器，並讓 App 用 `connect...Emulator` 連上（且用條件包住）
- [ ] 我知道 Firestore 模擬器需要 Java、Auth 不需要
- [ ] 我知道模擬器不強制索引這個大坑，以及上線前要做什麼
- [ ] 我知道 `emulators:exec` 適合跑自動化測試，且用 `demo-` 假專案
- [ ] 我理解「模擬器測過 ≠ 正式沒問題」，知道哪些要在真專案驗收

## 延伸閱讀

### 官方文件

- **[Install, configure and integrate Local Emulator Suite](https://firebase.google.com/docs/emulator-suite/install_and_configure)**
  - **讀哪裡**：整篇，`init emulators`、`start`、`exec`、port 設定的官方版。
  - **能學到什麼**：本章設定流程的權威版，含 export/import、各服務的模擬支援範圍。
  - **前提**：本章讀完即可。

- **[Connect your app to the Firestore Emulator](https://firebase.google.com/docs/emulator-suite/connect_firestore)**
  - **讀哪裡**：`connectFirestoreEmulator` 和「安全規則測試 + 覆蓋率報告」那幾節。
  - **能學到什麼**：連線細節與規則覆蓋率，補 Ch 18。

- **[Emulator Suite 與正式環境的差異](https://firebase.google.com/docs/emulator-suite)**
  - **讀哪裡**：overview 頁提到的「known limitations / differences」。
  - **能學到什麼**：本章「關鍵差異」表的官方版，上線前的檢查依據。

### 文章

- **[The Firebase Blog — 用 Emulator Suite 做本地開發與測試](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "emulator"，講如何把模擬器融入日常開發與 CI。
  - **為什麼值得讀**：把模擬器從「偶爾用」變成「開發流程核心」，本章進階的實戰版。

模擬器讓你開發免費又安全。但正式環境是要錢的——最後一個知識章來面對這件事：Firebase 怎麼計費、各服務的免費額度、以及怎麼避免「一覺醒來收到天價帳單」。這是每個 Firebase 開發者都該懂的自保知識。

→ [Ch 22 — 計費與額度](./22-pricing-quotas.md)
