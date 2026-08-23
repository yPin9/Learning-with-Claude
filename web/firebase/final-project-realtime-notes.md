# Final Project — 即時筆記板

> **目標**：把整門課整合成一個**真正完整、安全、上線**的作品——一個即時多人筆記板：使用者登入後能新增/編輯/刪除自己的筆記、可為筆記附一張圖片、所有變更即時同步、有經過測試的安全規則保護、並部署到公開網址。完成它，你就有能力獨立用 Firebase 做出上線級的即時應用。

> **環境**：Firebase JS SDK v12.18.0、firebase-tools 15.28.1。開發用 Emulator Suite（Ch 21），上線部署到真實專案。本專案的 Auth/Firestore/Storage/Rules 程式碼模式都在本課各章實跑驗證過。

## 這個專案整合了什麼

這不是「最後幾章的延伸」，是**整門課的總驗收**。對照你會用到的能力：

| 能力 | 來自 | 在本專案怎麼用 |
|---|---|---|
| 環境 / config / 連線 | Ch 0, 3 | 建專案、初始化、連 SDK |
| 多種登入 + 狀態管理 | Ch 5, 6, 7 | Google/匿名登入、UI 反映狀態 |
| Firestore CRUD | Ch 9, 10 | 筆記的增刪改查 |
| 即時同步 | Ch 11 | 筆記變更即時反映到所有裝置 |
| NoSQL 建模 | Ch 12 | 筆記結構、owner 綁定、反正規化 |
| 索引 | Ch 13 | 「我的筆記按時間排序」的查詢 |
| Storage 檔案 | Ch 19 | 為筆記附圖片 |
| 安全規則 + 測試 | Ch 15–18 | 只能動自己的筆記 + 欄位驗證 + 測試 |
| Hosting 部署 | Ch 20 | 上線到公開網址 |
| Emulator 開發 | Ch 21 | 全程本地開發 |
| 成本意識 | Ch 22 | 加 limit、關監聽、反正規化 |

超過 70% 的核心概念都在裡面。

## 功能規格

| 功能 | 需求 |
|---|---|
| **登入** | 支援 Google 登入 + 匿名試用；未登入看不到筆記、不能操作 |
| **新增筆記** | 標題 + 內容，寫進 Firestore，綁定 `ownerId` |
| **即時列表** | 用 `onSnapshot` 顯示**自己的**筆記，按更新時間排序 |
| **編輯** | 可修改自己筆記的標題/內容，更新 `updatedAt` |
| **刪除** | 可刪除自己的筆記 |
| **附圖**（進階） | 可為筆記上傳一張圖片（存 Storage，URL 存進筆記） |
| **安全** | 安全規則保證只能讀寫自己的筆記 + 欄位驗證，且有測試 |
| **上線** | 部署到 Firebase Hosting，公開網址可用 |

## 資料結構

```
notes/{noteId} {
  ownerId: string,        // uid，綁定擁有者（Ch 12 一切用 uid 綁）
  title: string,          // 1–100 字
  content: string,        // 0–5000 字
  imageUrl: string | null, // 附圖的下載網址（Ch 19），沒有就 null
  createdAt: timestamp,    // serverTimestamp
  updatedAt: timestamp     // serverTimestamp，用來排序
}
```

Storage 路徑：`note-images/{ownerId}/{noteId}`（用 uid 分資料夾，方便寫規則，Ch 19）。

> **查詢**：「我的筆記按更新時間排序」= `where("ownerId","==",myUid)` + `orderBy("updatedAt","desc")`。這是**跨兩個欄位**的查詢（篩 ownerId + 排 updatedAt），正式環境**需要複合索引**（Ch 13）——上線前記得建，或寫進 `firestore.indexes.json`。模擬器不會提醒你（Ch 13/21 的大坑）。

## 架構圖

```
   使用者瀏覽器（你的網頁）
        │
        ├─ 登入 ──────────────▶ Authentication（Google/匿名）
        │                         拿到 uid
        │
        ├─ 監聽我的筆記 ────────▶ Firestore: notes（onSnapshot + where ownerId）
        │   ◀── 即時推送變更 ───   （安全規則：只能讀寫 ownerId==自己的）
        │
        ├─ 附圖上傳 ───────────▶ Storage: note-images/{uid}/{noteId}
        │   拿到 downloadURL       （Storage 規則：只能傳自己資料夾、限大小/型別）
        │   存回筆記的 imageUrl
        │
   全部部署在 ─────────────────▶ Firebase Hosting（公開網址 + HTTPS + CDN）
```

## 里程碑（建議分階段做，別一次全上）

### Milestone 1：登入 + 空的筆記板
接 Google/匿名登入（練習 A 的模組直接搬），`onAuthStateChanged` 切換「登入前 / 筆記板」畫面。先讓登入能動，筆記板暫時空的。

### Milestone 2：新增 + 即時顯示
加「新增筆記」表單，`addDoc` 寫入含 `ownerId`、`serverTimestamp`。用 `onSnapshot(query(where ownerId, orderBy updatedAt))` 即時顯示自己的筆記。這時全程用**模擬器**（Ch 21），不碰真專案。

### Milestone 3：編輯 + 刪除
每張筆記加編輯（`updateDoc` + 更新 `updatedAt`）和刪除（`deleteDoc`）。記得監聽的 `unsubscribe`（登出時）。

### Milestone 4：安全規則 + 測試
寫 `firestore.rules`（只能讀寫自己的 + 欄位驗證），用 `rules-unit-testing` 寫測試（Ch 18），跑到全綠。**這步不能省**——沒有它你的筆記板就是裸奔。

### Milestone 5：附圖（進階）
加圖片上傳（`uploadBytes` + `getDownloadURL`，Ch 19），URL 存進筆記。寫 Storage 規則（限自己資料夾、大小、型別）。

### Milestone 6：上線
建索引（複合查詢需要）、`firebase deploy`（含 hosting + rules + indexes）、在正式網址跑一遍所有功能驗收（Ch 20 的清單，特別注意索引大坑）。

## 完整參考解答

**先自己做！這是驗收，偷看就失去意義。** 以下各部分的程式碼模式都在本課相應章節實跑驗證過。

<details>
<summary>點開 index.html（前端）</summary>

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>即時筆記板</title>
  <style>
    body { font-family: sans-serif; max-width: 700px; margin: 30px auto; padding: 0 12px; }
    .note { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin: 10px 0; }
    .note img { max-width: 100%; border-radius: 6px; margin-top: 8px; }
    .note .meta { color: #999; font-size: 0.8em; }
    input, textarea { width: 100%; padding: 8px; margin: 4px 0; box-sizing: border-box; }
    .hidden { display: none; }
    button { padding: 6px 12px; margin-right: 6px; }
  </style>
</head>
<body>
  <h1>即時筆記板</h1>

  <div id="authPanel">
    <button id="googleBtn">用 Google 登入</button>
    <button id="anonBtn">匿名試用</button>
  </div>

  <div id="appPanel" class="hidden">
    <p>你好，<span id="me"></span> <button id="logoutBtn">登出</button></p>

    <h3>新增筆記</h3>
    <input id="title" placeholder="標題（1–100 字）">
    <textarea id="content" placeholder="內容…" rows="3"></textarea>
    <input type="file" id="image" accept="image/*">
    <button id="addBtn">新增</button>

    <h3>我的筆記</h3>
    <div id="notes"></div>
  </div>

  <script type="module">
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import { getAuth, onAuthStateChanged, GoogleAuthProvider, signInWithPopup,
             signInAnonymously, signOut }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";
    import { getFirestore, collection, addDoc, doc, updateDoc, deleteDoc,
             query, where, orderBy, limit, onSnapshot, serverTimestamp,
             connectFirestoreEmulator }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-firestore.js";
    import { getStorage, ref, uploadBytes, getDownloadURL, connectStorageEmulator }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-storage.js";
    import { connectAuthEmulator }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";

    const firebaseConfig = { /* 換成你的 config */ };
    const app = initializeApp(firebaseConfig);
    const auth = getAuth(app);
    const db = getFirestore(app);
    const storage = getStorage(app);

    // 只在本機開發連模擬器（Ch 21），別上線！
    if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
      connectAuthEmulator(auth, "http://127.0.0.1:9099", { disableWarnings: true });
      connectFirestoreEmulator(db, "127.0.0.1", 8080);
      connectStorageEmulator(storage, "127.0.0.1", 9199);
    }

    const $ = id => document.getElementById(id);
    let unsub = null;

    onAuthStateChanged(auth, user => {
      if (user) {
        $("authPanel").classList.add("hidden");
        $("appPanel").classList.remove("hidden");
        $("me").textContent = user.displayName || user.email || "訪客";
        listenNotes(user.uid);
      } else {
        $("authPanel").classList.remove("hidden");
        $("appPanel").classList.add("hidden");
        if (unsub) { unsub(); unsub = null; }
      }
    });

    function listenNotes(uid) {
      if (unsub) return;
      // 我的筆記、按更新時間排序、限 100 筆（Ch 22 成本）
      const q = query(
        collection(db, "notes"),
        where("ownerId", "==", uid),
        orderBy("updatedAt", "desc"),
        limit(100)
      );
      unsub = onSnapshot(q, snap => {
        render(snap.docs.map(d => ({ id: d.id, ...d.data() })));
      }, err => console.error("監聽錯誤:", err.code));
    }

    function render(notes) {
      $("notes").innerHTML = notes.map(n => `
        <div class="note" data-id="${n.id}">
          <b>${esc(n.title)}</b>
          <div>${esc(n.content || "")}</div>
          ${n.imageUrl ? `<img src="${n.imageUrl}">` : ""}
          <div class="meta">${n.updatedAt?.toDate ? n.updatedAt.toDate().toLocaleString() : "儲存中…"}</div>
          <button data-edit="${n.id}">編輯</button>
          <button data-del="${n.id}">刪除</button>
        </div>`).join("");
      $("notes").querySelectorAll("[data-del]").forEach(b =>
        b.onclick = () => deleteDoc(doc(db, "notes", b.dataset.del)));
      $("notes").querySelectorAll("[data-edit]").forEach(b =>
        b.onclick = () => editNote(b.dataset.edit, notes));
    }

    async function editNote(id, notes) {
      const n = notes.find(x => x.id === id);
      const title = prompt("新標題：", n.title);
      if (title === null) return;
      await updateDoc(doc(db, "notes", id), { title, updatedAt: serverTimestamp() });
    }

    async function addNote() {
      const title = $("title").value.trim();
      if (!title) return alert("標題不能空");
      const user = auth.currentUser;
      // 先建立筆記（拿到 id 給圖片路徑用）
      const noteRef = await addDoc(collection(db, "notes"), {
        ownerId: user.uid,
        title,
        content: $("content").value.trim(),
        imageUrl: null,
        createdAt: serverTimestamp(),
        updatedAt: serverTimestamp()
      });
      // 有附圖就上傳，再把 URL 更新回筆記（Ch 19）
      const file = $("image").files[0];
      if (file) {
        const imgRef = ref(storage, `note-images/${user.uid}/${noteRef.id}`);
        await uploadBytes(imgRef, file);
        const url = await getDownloadURL(imgRef);
        await updateDoc(noteRef, { imageUrl: url });
      }
      $("title").value = ""; $("content").value = ""; $("image").value = "";
    }

    function esc(s) {
      return String(s).replace(/[&<>"]/g, c =>
        ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
    }

    $("googleBtn").onclick = () => signInWithPopup(auth, new GoogleAuthProvider()).catch(e => alert(e.code));
    $("anonBtn").onclick = () => signInAnonymously(auth).catch(e => alert(e.code));
    $("logoutBtn").onclick = () => signOut(auth);
    $("addBtn").onclick = () => addNote().catch(e => alert(e.code));
  </script>
</body>
</html>
```

</details>

<details>
<summary>點開 firestore.rules（安全規則）</summary>

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    function isSignedIn() { return request.auth != null; }
    function isOwner(uid)  { return isSignedIn() && request.auth.uid == uid; }
    // 欄位驗證抽成 function，create 和 update 都套（Ch 17）
    function isValidNote() {
      let d = request.resource.data;
      return d.title is string && d.title.size() >= 1 && d.title.size() <= 100
        && d.content is string && d.content.size() <= 5000;
    }

    match /notes/{noteId} {
      // 只能讀自己的筆記（查詢也要 where ownerId==自己，否則被拒，Ch 16）
      allow read: if isOwner(resource.data.ownerId);

      // 新增：必須以自己為 owner、欄位合法
      allow create: if isSignedIn()
        && request.resource.data.ownerId == request.auth.uid
        && isValidNote();

      // 修改：只能動自己的、不能偷改 ownerId，且欄位一樣要重新驗證
      //（否則編輯時可繞過 create 的長度/型別限制，Ch 17 進階）
      allow update: if isOwner(resource.data.ownerId)
        && request.resource.data.ownerId == resource.data.ownerId
        && isValidNote();

      // 刪除：只能刪自己的
      allow delete: if isOwner(resource.data.ownerId);
    }
  }
}
```

</details>

<details>
<summary>點開 storage.rules（Storage 規則）</summary>

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /note-images/{userId}/{fileName} {
      allow read: if request.auth != null;
      allow write: if request.auth != null
        && request.auth.uid == userId                          // 只能傳自己的資料夾
        && request.resource.size < 5 * 1024 * 1024             // 限 5 MB（Ch 19/22 成本防線）
        && request.resource.contentType.matches('image/.*');   // 只准圖片
    }
  }
}
```

</details>

<details>
<summary>點開 rules_test.mjs（規則測試片段，Ch 18）</summary>

```js
import { initializeTestEnvironment, assertFails, assertSucceeds } from "@firebase/rules-unit-testing";
import { doc, setDoc, deleteDoc, getDoc } from "firebase/firestore";
import { readFileSync } from "node:fs";

const env = await initializeTestEnvironment({
  projectId: "demo-notes",
  firestore: { rules: readFileSync("firestore.rules","utf8"), host:"127.0.0.1", port:8080 },
});
const alice = env.authenticatedContext("alice").firestore();
const bob   = env.authenticatedContext("bob").firestore();

const note = (owner) => ({ ownerId: owner, title: "t", content: "", imageUrl: null,
                           createdAt: new Date(), updatedAt: new Date() });

// 埋一筆 alice 的筆記
await env.withSecurityRulesDisabled(async ctx => {
  await setDoc(doc(ctx.firestore(), "notes/n1"), note("alice"));
});

await assertSucceeds(setDoc(doc(alice, "notes/n2"), note("alice")));  // 建自己的 ✓
await assertFails(setDoc(doc(alice, "notes/n3"), note("bob")));        // 冒名 ✗
await assertFails(getDoc(doc(bob, "notes/n1")));                        // 讀別人的 ✗
await assertFails(deleteDoc(doc(bob, "notes/n1")));                     // 刪別人的 ✗
await assertSucceeds(deleteDoc(doc(alice, "notes/n1")));                // 刪自己的 ✓
await assertFails(setDoc(doc(alice, "notes/n4"),
  { ...note("alice"), title: "x".repeat(101) }));                       // 標題超長 ✗

await env.cleanup();
console.log("all rule tests passed");
process.exit(0);
```

跑：`firebase emulators:exec --only firestore --project demo-notes "node rules_test.mjs"`

</details>

<details>
<summary>點開 firestore.indexes.json（複合索引，Ch 13）</summary>

```json
{
  "indexes": [
    {
      "collectionGroup": "notes",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "ownerId", "order": "ASCENDING" },
        { "fieldPath": "updatedAt", "order": "DESCENDING" }
      ]
    }
  ],
  "fieldOverrides": []
}
```

這對應「`where ownerId` + `orderBy updatedAt desc`」的查詢。**模擬器不會要求它，但正式環境要（Ch 13 大坑）——所以一定要寫進來一起部署。**

</details>

**整體設計說明**：

- **一切用 uid 綁**（Ch 4/12）：`ownerId` 是所有權限與查詢的核心。安全規則、查詢、Storage 路徑都靠它。
- **模擬器連線用條件包**（Ch 21）：`location.hostname` 判斷，確保上線不會執行 `connect...Emulator`。
- **附圖流程**（Ch 19）：先建筆記拿 id → 用 id 當圖片路徑上傳 → URL 更新回筆記。圖片進 Storage，Firestore 只存 URL。
- **成本意識**（Ch 22）：查詢加 `limit(100)`、登出 `unsubscribe`、`updatedAt` 存成欄位供排序（不即時算）。
- **規則 + 測試**（Part 4）：存取控制 + 欄位驗證 + 防冒名 + 不可偷改 ownerId，且有測試證明。
- **XSS 防護**：`esc()` 跳脫使用者輸入（顯示層），配合規則的資料層驗證。

## 驗收清單

上線後逐項確認（Ch 20 的驗收清單具體化）：

```
□ Google 登入、匿名登入都能用（正式網域，非 file://）
□ 新增筆記後即時出現，兩個視窗同帳號能即時互看
□ 只看得到自己的筆記（用另一個帳號登入，看不到別人的）
□ 編輯/刪除只對自己的筆記有效
□ 附圖能上傳、顯示，且超過 5MB / 非圖片被擋
□ 複合索引已建（否則「我的筆記排序」查詢會 requires an index）
□ firestore.rules 是測過的版本，已 deploy（不是 Test mode！）
□ 規則測試全綠
□ 直接在 console 用程式碼試「讀/刪別人的筆記」→ 被 permission-denied 擋下
```

最後一項是**真正的驗收**：模擬攻擊者繞過前端，確認安全規則擋得住。這是這門課的靈魂——你的 App 不只能用，而且**安全**。

## 延伸挑戰（做完基本版再挑戰）

- **分享筆記**：加一個 `sharedWith: [uid...]` 欄位，讓筆記能分享給特定使用者讀（改讀規則：`isOwner || request.auth.uid in resource.data.sharedWith`）。
- **標籤與搜尋**：加 `tags` 陣列，用 `array-contains` 篩選（Ch 10）；體會「Firestore 沒有全文搜尋」的限制（Ch 10）。
- **協作編輯**：多人能編輯同一筆記，用交易（Ch 14）處理並發衝突。
- **軟刪除 + 回收桶**：刪除改成 `deleted: true`，30 天後用 TTL 自動清（Ch 22 進階）。
- **接 CI**：規則測試 + `emulators:exec` 進 GitHub Actions，每次 push 自動測 + 自動部署預覽（Ch 18/20 進階）。
- **換框架重寫**：用 React/Vue 重做，把 `onAuthStateChanged` 包進 context、`onSnapshot` 進 `useEffect` 並在 cleanup unsubscribe（Ch 7/11 提過的模式）。

## 自我檢核（整門課的總驗收）

- [ ] 我獨立做出了一個有登入、即時同步、CRUD 的完整 App
- [ ] 我的安全規則保證「只能讀寫自己的」，且我寫了測試證明、也親手試過攻擊被擋
- [ ] 我為複合查詢建了索引，理解為什麼模擬器沒提醒我
- [ ] 我用模擬器開發全程、上線前在真專案驗收
- [ ] 我的設計有成本意識（limit、關監聽、聚合存欄位）
- [ ] 我把它部署到公開網址，任何人都能打開
- [ ] 回頭看，我能說出這個 App 裡每個 Firebase 功能在解決什麼問題，以及它的底層機制

---

## 結業

你從「Firebase 是什麼」一路走到「獨立做出一個安全上線的即時 App」。回顧你現在會的：

- **理論**：BaaS 的取捨、前端直連的安全模型、認證 vs 授權、Firestore 為什麼即時、NoSQL 為什麼這樣建模。
- **實作**：登入、即時資料庫、檔案、部署，全套能動。
- **工程素養**：會寫**且測試**安全規則、懂成本、會用模擬器、知道上線的坑。

最重要的是——你不只「會呼叫 API」，你**懂它為什麼這樣設計**。這讓你有能力判斷「這個專案該不該用 Firebase」，也讓你學下一個 BaaS（Supabase…）或 Firebase 的進階服務（Functions、FCM…）時，一點就通。

**下一步的方向**（如果你想繼續）：

- **Cloud Functions**：當「前端直連」不夠用時（要寄信、要在後端跑邏輯、要用 Admin SDK 做高權限操作），這是自然的下一步。
- **框架整合**：把這套搬進 React/Vue/Next.js，學 Firebase 在真實前端框架裡的組織方式。
- **進階服務**：FCM 推播、Remote Config、Analytics——讓 App 從「能用」到「留得住人」（Ch 2 的 Engage 群）。

恭喜完成。去做點東西上線吧。
