# Ch 18 — 測試規則

> **目標**：學會用官方工具 `@firebase/rules-unit-testing` 搭配 Firestore 模擬器，把安全規則當程式碼一樣寫**自動化單元測試**——驗證「該擋的擋、該放的放」，在上線前就抓出規則漏洞。這是把 Part 4 的規則變得可靠、可維護的關鍵。

> **環境**：`@firebase/rules-unit-testing` v5.0.2、firebase-tools 15.28.1、Firestore 模擬器、Node v22.20.0。本章所有測試程式碼與輸出都在此環境**實跑驗證通過**（8 條測試全綠）。

## 為什麼需要這個？

規則是你資料的唯一防線（Ch 15），但規則也是程式碼，**程式碼會有 bug**。手動測試規則（開網頁、切帳號、一條條試）又慢、又容易漏、又無法在每次改動後重跑。一個沒測到的規則漏洞 = 資料外洩。

解法和測其他程式碼一樣：**寫自動化測試**。Firebase 提供官方測試庫，讓你用幾行程式碼斷言「Alice 能寫自己的、不能寫 Bob 的」，一鍵跑完所有情境。改了規則，重跑一次就知道有沒有弄壞。**規則有測試，你才敢改它、才敢信它。**

## 先建立直覺：對規則做「攻防演練」

規則測試的心智模型，就是自動化的紅隊演練：

```
   對每條規則，問兩種問題：
   
   ✅ 該允許的，真的允許嗎？   assertSucceeds(alice 寫自己的資料)
   ❌ 該拒絕的，真的拒絕嗎？   assertFails(alice 寫 bob 的資料)
                             assertFails(未登入者寫任何資料)
                             assertFails(冒名建立資料)
```

**好的規則測試，拒絕案例（`assertFails`）通常比允許案例還多**——因為安全的重點是「擋住不該做的」，你要窮舉各種攻擊角度並確認都被擋。

## 環境準備

在你的專案資料夾（有 `firestore.rules` 的地方）：

```bash
npm install --save-dev @firebase/rules-unit-testing firebase
```

需要 Firestore 模擬器（`firebase-tools`）跑起來，測試庫會連上去用你的規則做評估。模擬器需要 **Java**（Ch 21 會詳談模擬器），本課驗證環境用的是可攜式 JDK。

> **好消息（呼應 Ch 16）**：規則的評估在**模擬器和正式環境行為一致**——不像索引那樣有落差（Ch 13）。所以規則測試在本地跑出的結果，就是正式環境的行為。這讓規則能被可靠地測試，是 Firebase 安全性工程的基石。

## 寫一份規則測試

核心 API 三個：`initializeTestEnvironment`（建測試環境）、`assertSucceeds`（斷言成功）、`assertFails`（斷言失敗）。以下是**本課實跑驗證**的完整測試（測 Ch 17 的 owner-based 規則）：

被測的 `firestore.rules`：

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /users/{userId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
    match /posts/{postId} {
      allow read: if true;
      allow create: if request.auth != null
                    && request.resource.data.owner == request.auth.uid
                    && request.resource.data.title is string;
      allow update, delete: if request.auth != null
                    && resource.data.owner == request.auth.uid;
    }
  }
}
```

測試檔 `rules_test.mjs`：

```js
import { initializeTestEnvironment, assertFails, assertSucceeds }
  from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc, deleteDoc } from "firebase/firestore";
import { readFileSync } from "node:fs";

// 建立測試環境，載入你的規則
const env = await initializeTestEnvironment({
  projectId: "demo-rules",
  firestore: {
    rules: readFileSync("firestore.rules", "utf8"),
    host: "127.0.0.1", port: 8080,   // 連到模擬器
  },
});

// 模擬三種身分（各自帶不同的 request.auth）
const alice = env.authenticatedContext("alice").firestore();   // 登入為 alice
const bob   = env.authenticatedContext("bob").firestore();     // 登入為 bob
const anon  = env.unauthenticatedContext().firestore();        // 未登入

// ── 測 users 規則 ──
await assertSucceeds(setDoc(doc(alice, "users/alice"), { name: "Alice" }));
await assertFails(setDoc(doc(alice, "users/bob"), { name: "hack" }));   // 不能寫別人的
await assertFails(setDoc(doc(anon, "users/alice"), { name: "x" }));     // 未登入不能寫

// ── 測 posts 規則 ──
// 先用「繞過規則」的管道塞一筆測試資料（模擬既有資料）
await env.withSecurityRulesDisabled(async (ctx) => {
  await setDoc(doc(ctx.firestore(), "posts/p1"), { owner: "alice", title: "hi" });
});
await assertSucceeds(getDoc(doc(anon, "posts/p1")));                    // 誰都能讀
await assertSucceeds(setDoc(doc(alice, "posts/p2"), { owner: "alice", title: "mine" }));
await assertFails(setDoc(doc(alice, "posts/p3"), { owner: "bob", title: "spoof" }));  // 不能冒名
await assertFails(deleteDoc(doc(bob, "posts/p1")));                     // 不能刪別人的
await assertFails(setDoc(doc(alice, "posts/p4"), { owner: "alice", title: 123 }));    // 型別錯

await env.cleanup();
```

用模擬器跑（Ch 21 會詳解這指令）：

```bash
firebase emulators:exec --only firestore --project demo-rules "node rules_test.mjs"
```

**本課實跑的真實輸出（8 條全綠）**：

```
[PASS] alice writes her own /users/alice
[PASS] alice DENIED writing /users/bob
[PASS] anonymous DENIED writing /users/alice
[PASS] anonymous can READ posts/p1
[PASS] alice creates post with owner=alice
[PASS] alice DENIED creating post with owner=bob
[PASS] bob DENIED deleting alice's post
[PASS] alice DENIED post with non-string title
```

每一條都驗證了一個規則行為：該允許的允許、該拒絕的拒絕。改了規則後重跑這個檔，立刻知道有沒有破壞任何一條。

## 三個核心 API 詳解

### initializeTestEnvironment

建立測試沙盒，載入你的規則。之後從它產生「不同身分的 Firestore 連線」。

### authenticatedContext / unauthenticatedContext

模擬不同的請求者身分——這是規則測試的關鍵：

```js
env.authenticatedContext("alice")                    // request.auth.uid == "alice"
env.authenticatedContext("alice", { admin: true })   // 還帶 custom claim admin=true
env.unauthenticatedContext()                          // request.auth == null（未登入）
```

第二個參數能塞 **custom claims**（Ch 17 的角色測試就靠這個——不用真的設 claim，測試裡直接模擬），讓你能測「管理員能刪任何貼文」這種規則。

### assertSucceeds / assertFails

- `assertSucceeds(操作)`：斷言這個操作**成功**（規則允許）。失敗就測試不通過。
- `assertFails(操作)`：斷言這個操作**被拒絕**（規則擋下）。**如果它意外成功了，測試不通過**——這就是抓漏洞的地方：你以為擋住了，測試告訴你其實沒擋。

### withSecurityRulesDisabled：埋測試資料

測「update/delete 自己的」時，你需要「資料庫裡先有一筆別人的資料」。但用一般管道寫入會被規則擋。`withSecurityRulesDisabled` 開一個**繞過規則**的管道，專門用來埋測試前置資料（seed），不影響被測的規則本身：

```js
await env.withSecurityRulesDisabled(async (ctx) => {
  await setDoc(doc(ctx.firestore(), "posts/p1"), { owner: "alice", title: "hi" });
});
```

這只在測試裡用，用來設定「初始狀態」，之後再用正常管道（帶身分）去測規則對這筆資料的反應。

## 該測哪些案例：一份 checklist

對每個 match 段，至少測這些角度：

```
□ 正常允許：合法使用者做合法操作 → assertSucceeds
□ 未登入：anon 做需要登入的操作 → assertFails
□ 冒名：alice 想以 bob 的身分/owner 建立資料 → assertFails
□ 越權：alice 想改/刪 bob 的資料 → assertFails
□ 欄位驗證：型別錯、必填缺、多餘欄位、超長 → assertFails
□ 邊界：剛好在長度上限/下限、剛好 0 → 依規則預期
□ 讀寫分離：能讀但不能寫的角色、能寫但不能刪的操作 → 分別測
```

**規則的每一個 `&&` 條件，理想上都該有一個測試去「單獨違反它」**，確認它真的在把關。

## 踩雷集錦

1. **只測允許案例，不測拒絕案例**：`assertSucceeds` 全過不代表安全——你沒驗證「該擋的有擋」。安全的重點在 `assertFails`，且通常要更多。
2. **`assertFails` 意外通過卻沒發現**：如果你寫 `assertFails(某操作)` 但那操作其實被允許了，`assertFails` 會讓測試**失敗**（因為它預期失敗卻成功）。這正是它的價值——別忽略這種紅燈，那是真的漏洞。
3. **忘記 `env.cleanup()`**：不清理會殘留連線，讓測試 hang 住或 Node 不結束。測試結尾一定 `cleanup()`。
4. **在模擬器沒跑的情況下跑測試**：測試庫要連模擬器（port 8080）。沒開模擬器會連線失敗。用 `emulators:exec` 包起來（它會自動起關模擬器）最省事。
5. **用真實專案跑規則測試**：**絕對不要**。規則測試會大量讀寫、還要繞過規則埋資料——只能對**模擬器**跑，用 `demo-` 開頭的假 projectId（如 `demo-rules`）確保不會誤連真專案。
6. **以為模擬器的規則行為和正式有落差**：規則行為模擬器和正式**一致**（不像索引，Ch 13）。所以規則測試在本地綠了，正式就是這個行為。放心測。

## 進階：再往深一層

- **接進測試框架與 CI**：把規則測試包進 Jest/Vitest/Mocha，用 `firebase emulators:exec "npm test"` 一鍵跑。再放進 CI（GitHub Actions），**每次改規則自動測**——規則就和其他程式碼一樣有回歸保護。這是專業團隊管理規則的標準做法。
- **測查詢（list）的規則**：不只測單文件讀寫，也要測查詢——例如「規則要求只能讀自己的，那麼查整個集合應該被拒、查 `where(owner==自己)` 應該通過」。這驗證 Ch 16 說的「規則不過濾查詢」有沒有咬到你。
- **`RulesTestEnvironment.clearFirestore()`**：每個測試之間清空資料，確保測試互相獨立、不受前一個測試殘留影響。良好的測試衛生。
- **測 Storage 規則**：同一套工具也能測 Cloud Storage 規則（Ch 19），`initializeTestEnvironment` 傳 `storage: { rules: ... }`。學會 Firestore 規則測試，Storage 規則測試是一樣的模式。
- **覆蓋率報告**：模擬器能產生規則的**覆蓋率報告**（訪問 `http://127.0.0.1:8080/emulator/v1/projects/<id>:ruleCoverage.html`），視覺化顯示哪些規則行被測到、哪些沒有。幫你找出「沒被任何測試碰到」的規則死角。

## 本章重點整理

- 規則是程式碼、會有 bug，要用 **`@firebase/rules-unit-testing` + 模擬器**寫**自動化測試**，才敢改、才敢信。
- 三個核心：`initializeTestEnvironment`（載規則）、`authenticatedContext`/`unauthenticatedContext`（模擬身分，可帶 claims）、`assertSucceeds`/`assertFails`（斷言允許/拒絕）。
- **拒絕案例（`assertFails`）通常比允許案例更多更重要**——窮舉攻擊角度（未登入、冒名、越權、欄位錯）確認都被擋。
- `withSecurityRulesDisabled` 用來繞過規則**埋測試前置資料**。
- 規則行為**模擬器和正式一致**——本地測綠就是正式行為；只對模擬器 + `demo-` 假專案跑測試。

## 自我檢核

- [ ] 我能用 `initializeTestEnvironment` 載入規則、建立不同身分的連線
- [ ] 我能為一條規則同時寫「該允許」和「該拒絕」的測試
- [ ] 我知道 `assertFails` 意外通過代表發現了漏洞，不能忽略
- [ ] 我能列出一條 owner-based 規則該測的攻擊角度（未登入、冒名、越權、欄位錯）
- [ ] 我知道規則測試只能對模擬器 + demo 專案跑，不能碰真實專案
- [ ] 我知道規則行為模擬器和正式一致，所以本地測試可信

## 延伸閱讀

### 官方文件

- **[Unit test your Security Rules](https://firebase.google.com/docs/firestore/security/test-rules-emulator)**
  - **讀哪裡**：整篇，這就是本章的官方權威版，含 `initializeTestEnvironment`、context、assert、cleanup 的完整 API。
  - **能學到什麼**：本章每個 API 的官方說明與更多範例，以及接進測試框架的做法。
  - **前提**：本章讀完即可。

- **[@firebase/rules-unit-testing API reference](https://firebase.google.com/docs/reference/rules-unit-testing)**
  - **讀哪裡**：需要某個方法（`clearFirestore`、`withSecurityRulesDisabled` 等）的細節時查。
  - **能學到什麼**：測試庫的完整 API。

- **[Rules coverage report](https://firebase.google.com/docs/emulator-suite/connect_firestore#generate_test_reports)**
  - **讀哪裡**：覆蓋率報告那段。
  - **能學到什麼**：本章進階提到的規則覆蓋率視覺化，找出沒測到的規則死角。

### 文章

- **[The Firebase Blog — Testing Security Rules 實務](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "test security rules"，講如何把規則測試變成開發流程的一部分。
  - **為什麼值得讀**：把「寫測試」升級成「規則的 TDD / CI 流程」，本章進階的實戰版。

Part 4 到此為止——你不只會寫安全規則，還會測它。是時候把 Ch 15–18 全用上了。下一個練習 C：為前面的留言板/筆記板寫一整套安全規則，並用測試證明它擋得住攻擊。這是把「能跑的 App」變成「安全的 App」的關鍵一步。

→ [練習 C：為 app 寫一整套安全規則](./practice-c-security-rules.md)
