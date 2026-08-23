# 練習 C — 為 app 寫一整套安全規則

> **目標**：把練習 B 的即時留言板從「Test mode 裸奔」升級成「真正安全」——寫一整套 Firestore 安全規則，涵蓋存取控制與欄位驗證，並用 `@firebase/rules-unit-testing` 寫測試證明它擋得住各種攻擊。完成後你驗證了「為真實 App 設計並驗證授權模型」的能力。

## 背景與動機

練習 B 的留言板功能完整，但有致命破綻：規則是 Test mode，攻擊者能繞過前端刪改別人的留言（Ch 15）。這個練習補上那道鎖——而且不是「寫了就算」，要**用測試證明**它真的擋得住。這是把 side project 從「能 demo」變成「敢上線」的關鍵一步。

## 任務規格

為一個含「使用者資料 + 留言」的 App 寫規則。資料結構：

```
users/{uid} {
  displayName: string,
  bio: string
}
messages/{messageId} {
  text: string,           // 1–500 字
  authorId: string,       // 必須等於發言者 uid
  authorName: string,
  createdAt: timestamp
}
```

規則要求：

| 資源 | 規則 |
|---|---|
| `users/{uid}` 讀 | 登入者皆可讀（看別人的公開檔案） |
| `users/{uid}` 寫 | 只有本人（`uid == auth.uid`）可寫 |
| `users/{uid}` 內容 | `displayName` 必為字串、1–50 字；不允許多餘欄位 |
| `messages` 讀 | 登入者可讀 |
| `messages` 建立 | 登入者；`authorId` 必須是自己；`text` 為 1–500 字字串 |
| `messages` 刪除 | 只有作者（`authorId == auth.uid`）可刪 |
| `messages` 更新 | 一律禁止（留言不可編輯） |

測試要求：用 `rules-unit-testing` 寫測試，**至少涵蓋**：

- ✅ 作者能建立/刪除自己的留言
- ❌ 未登入不能建立留言
- ❌ 不能建立 `authorId` 是別人的留言（冒名）
- ❌ 不能刪除別人的留言
- ❌ 不能建立超長（>500）或非字串的 text
- ❌ 不能更新任何留言
- ✅/❌ users 的自己可寫、別人不可寫

**限制**：

- 用 `function` 抽出重複邏輯（至少 `isSignedIn`）。
- create 用 `request.resource.data`、delete/update 用 `resource.data`，別用反。
- 測試的拒絕案例（`assertFails`）數量應多於允許案例。

## 期望輸出範例

測試全綠時：

```
[PASS] 作者建立自己的留言
[PASS] 未登入建立留言 → 拒絕
[PASS] 冒名（authorId=別人）建立 → 拒絕
[PASS] 建立超長 text → 拒絕
[PASS] 建立非字串 text → 拒絕
[PASS] 作者刪除自己的留言
[PASS] 刪除別人的留言 → 拒絕
[PASS] 更新任何留言 → 拒絕
[PASS] 本人寫自己的 user 檔案
[PASS] 寫別人的 user 檔案 → 拒絕
```

## 如果你卡住了

1. **create 規則報錯讀不到值**：create 時文件不存在，`resource` 是 null。要驗證「寫入的內容」用 `request.resource.data`，不是 `resource.data`（Ch 16/17）。
2. **「冒名」測試沒擋住**：你的 create 規則要有 `request.resource.data.authorId == request.auth.uid`，否則使用者能建立 authorId 是別人的留言。
3. **禁止更新卻不知怎麼寫**：不用寫 deny——**不寫 `allow update`** 就自動拒絕（Ch 16）。
4. **測試 hang 住不結束**：忘了 `env.cleanup()`，或沒開模擬器。用 `emulators:exec` 包起來跑。
5. **埋不進「別人的留言」來測刪除**：一般管道會被 create 規則擋。用 `withSecurityRulesDisabled` 埋前置資料（Ch 18）。
6. **欄位驗證太鬆**：只檢查登入不夠，要檢查型別（`is string`）、長度（`.size()`）、以及可選的 `hasOnly` 擋多餘欄位。

## 實作步驟建議

### Step 1：先寫「全鎖」再逐條開
從 `allow read, write: if false;` 開始（default deny，Ch 15），確認什麼都不能做，再逐條放開需要的。

### Step 2：users 規則
`allow read: if isSignedIn()`；`allow write: if isOwner(userId)` + 欄位驗證。

### Step 3：messages 規則
`read`、`create`（含 authorId 驗證 + text 驗證）、`delete`（作者）、不寫 update。

### Step 4：寫測試
建立 alice/bob/anon 三種身分，逐一斷言。拒絕案例要窮舉攻擊角度。

### Step 5：跑測試、修到全綠
`firebase emulators:exec --only firestore "node rules_test.mjs"`，紅的就修規則或修測試。

## 完整參考解答

**先自己做！** 以下規則與測試已在 `@firebase/rules-unit-testing` v5.0.2 + Firestore 模擬器實跑驗證通過。

<details>
<summary>點開參考規則（firestore.rules）</summary>

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    function isSignedIn() {
      return request.auth != null;
    }
    function isOwner(userId) {
      return isSignedIn() && request.auth.uid == userId;
    }

    // 使用者公開檔案
    match /users/{userId} {
      allow read: if isSignedIn();
      allow write: if isOwner(userId)
        && request.resource.data.displayName is string
        && request.resource.data.displayName.size() >= 1
        && request.resource.data.displayName.size() <= 50
        && request.resource.data.keys().hasOnly(['displayName', 'bio']);
    }

    // 留言
    match /messages/{messageId} {
      allow read: if isSignedIn();

      allow create: if isSignedIn()
        && request.resource.data.authorId == request.auth.uid   // 不能冒名
        && request.resource.data.text is string
        && request.resource.data.text.size() >= 1
        && request.resource.data.text.size() <= 500;

      allow delete: if isSignedIn()
        && resource.data.authorId == request.auth.uid;          // 只能刪自己的

      // 不寫 allow update → 留言不可編輯（預設拒絕）
    }
  }
}
```

</details>

<details>
<summary>點開參考測試（rules_test.mjs）</summary>

```js
import { initializeTestEnvironment, assertFails, assertSucceeds }
  from "@firebase/rules-unit-testing";
import { doc, setDoc, deleteDoc, updateDoc } from "firebase/firestore";
import { readFileSync } from "node:fs";

const env = await initializeTestEnvironment({
  projectId: "demo-practice-c",
  firestore: { rules: readFileSync("firestore.rules", "utf8"), host: "127.0.0.1", port: 8080 },
});
const log = (...a) => console.log(...a);

const alice = env.authenticatedContext("alice").firestore();
const bob   = env.authenticatedContext("bob").firestore();
const anon  = env.unauthenticatedContext().firestore();

const msg = (author, text) => ({
  text, authorId: author, authorName: author, createdAt: new Date()
});

// ── messages: create ──
await assertSucceeds(setDoc(doc(alice, "messages/m1"), msg("alice", "hi")));
log("[PASS] 作者建立自己的留言");

await assertFails(setDoc(doc(anon, "messages/m2"), msg("anon", "hi")));
log("[PASS] 未登入建立留言 → 拒絕");

await assertFails(setDoc(doc(alice, "messages/m3"), msg("bob", "冒名")));
log("[PASS] 冒名（authorId=別人）建立 → 拒絕");

await assertFails(setDoc(doc(alice, "messages/m4"), msg("alice", "x".repeat(501))));
log("[PASS] 建立超長 text → 拒絕");

await assertFails(setDoc(doc(alice, "messages/m5"), { ...msg("alice",""), text: 123 }));
log("[PASS] 建立非字串 text → 拒絕");

// ── messages: delete / update ──
await env.withSecurityRulesDisabled(async (ctx) => {
  await setDoc(doc(ctx.firestore(), "messages/owned-by-alice"), msg("alice", "seed"));
});
await assertSucceeds(deleteDoc(doc(alice, "messages/m1")));
log("[PASS] 作者刪除自己的留言");
await assertFails(deleteDoc(doc(bob, "messages/owned-by-alice")));
log("[PASS] 刪除別人的留言 → 拒絕");
await assertFails(updateDoc(doc(alice, "messages/owned-by-alice"), { text: "改" }));
log("[PASS] 更新任何留言 → 拒絕");

// ── users ──
await assertSucceeds(setDoc(doc(alice, "users/alice"), { displayName: "Alice", bio: "hi" }));
log("[PASS] 本人寫自己的 user 檔案");
await assertFails(setDoc(doc(alice, "users/bob"), { displayName: "hack", bio: "" }));
log("[PASS] 寫別人的 user 檔案 → 拒絕");

await env.cleanup();
process.exit(0);
```

執行：`firebase emulators:exec --only firestore --project demo-practice-c "node rules_test.mjs"`

**解答說明**：

- **default deny 心態**：只寫 allow，沒寫的（如 messages 的 update）自動拒絕。
- **create 驗證 `authorId == auth.uid`**：這是防冒名的關鍵——沒有這條，「只能刪自己的」會被「一開始就用別人的 authorId 建立」繞過（Ch 17 踩雷第 3 條）。
- **create 用 `request.resource.data`、delete 用 `resource.data`**：create 沒有舊資料，delete 判斷的是現有資料的擁有者。
- **`hasOnly` 擋多餘欄位**：users 只允許 `displayName`/`bio`，防止塞入 `isAdmin: true` 之類的惡意欄位。
- **拒絕案例（7 個）多於允許案例（3 個）**：符合「安全的重點在擋住不該做的」。

</details>

## 測試用例

| 測試 | 斷言 | 驗證的攻擊角度 |
|---|---|---|
| 作者建自己的留言 | 成功 | 正常路徑 |
| 未登入建留言 | 失敗 | 匿名攻擊 |
| authorId=別人 | 失敗 | 冒名 |
| text 超長 / 非字串 | 失敗 | 垃圾資料注入 |
| 作者刪自己的 | 成功 | 正常路徑 |
| 刪別人的 | 失敗 | 越權（練習 B 的破綻） |
| 更新留言 | 失敗 | 未授權的操作 |
| 寫別人的 user | 失敗 | 越權 |

## 延伸挑戰（加分）

- **加管理員**：管理員（`request.auth.token.admin == true`，測試用 `authenticatedContext("admin", {admin:true})` 模擬）能刪任何留言。加一條 `allow delete: if isAdmin() || isOwner(...)`。
- **驗證 createdAt**：要求 `request.resource.data.createdAt == request.time`，防止偽造時間戳（Ch 17 進階）。
- **只允許改特定欄位**：若你想開放「編輯留言但只能改 text」，用 `diff().affectedKeys().hasOnly(['text'])`（Ch 17 進階）。
- **接進 CI**：把測試包進 `npm test` + GitHub Actions，每次改規則自動跑（Ch 18 進階）。
- **測查詢（list）**：加測「規則要求登入才能讀，未登入查整個 messages 集合被拒」。

## 自我檢核

- [ ] 我的規則涵蓋了存取控制**和**欄位驗證，不只檢查登入
- [ ] create 驗證了 `authorId == auth.uid`，我理解不驗證會被冒名繞過
- [ ] 我用對了 `request.resource.data`（create）和 `resource.data`（delete）
- [ ] 我的測試拒絕案例多於允許案例，且窮舉了攻擊角度
- [ ] 我親自跑測試到全綠，並試著「故意寫錯一條規則」看對應測試變紅
- [ ] 我理解這套規則怎麼擋住練習 B 那個「繞過前端刪別人留言」的攻擊

Part 4 完成——你的 App 現在有了真正的、可測試的安全防線。最後一個 Part 把剩下的拼圖補齊：檔案上傳（Storage）、部署上線（Hosting）、本地開發（Emulator）、以及怎麼不被帳單嚇到（計費）。下一章從 Cloud Storage 開始。

→ [Ch 19 — Cloud Storage 檔案儲存](./19-cloud-storage.md)
