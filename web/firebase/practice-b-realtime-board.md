# 練習 B — 即時留言板

> **目標**：把 Part 2（Auth）和 Part 3（Firestore）整合成一個真正的即時留言板——登入的使用者可以發言，所有開著頁面的人**即時**看到新留言，使用者只能刪自己的留言。完成後你驗證了「把認證和即時資料庫組成一個完整功能」的能力。

## 背景與動機

即時留言板（或聊天室）是 Firebase 的「Hello World 級」代表作——它同時用到登入、即時同步、資料建模、基本權限，是驗收 Part 2+3 的最佳題目。而且它就是 Final Project 的核心，做好這個，Final 只是加東西。

## 任務規格

做一個單一 `index.html`（延續 CDN 寫法）。要求：

| 功能 | 驗收標準 |
|---|---|
| **登入** | 沿用練習 A 的登入（至少支援匿名 + 一種正式登入），未登入不能發言 |
| **發言** | 登入後可輸入文字送出，寫進 Firestore `messages` 集合 |
| **即時顯示** | 用 `onSnapshot` 監聽，任何人發言，所有頁面**立刻**看到，按時間排序 |
| **顯示作者** | 每則留言顯示作者名稱（displayName / email / 訪客）和時間 |
| **刪除自己的** | 每則留言若是自己發的，顯示刪除鈕；別人的不顯示 |
| **時間排序** | 用 `serverTimestamp` + `orderBy` 讓留言按時間排列 |
| **監聽清理** | 登出時或不需要時正確 `unsubscribe` |

**資料結構**（建議）：

```
messages/{autoId} {
  text: string,
  authorId: string,      // uid，用來判斷「是不是我發的」
  authorName: string,    // 反正規化，直接顯示不用再查（Ch 12）
  createdAt: timestamp    // serverTimestamp，用來排序
}
```

**限制**：

- 留言必須存 `authorId`（uid），刪除鈕的顯示靠比對 `authorId === auth.currentUser.uid`。
- 作者名稱用**反正規化**存進留言（`authorName`），不要每則留言再去查一次使用者（Ch 12 的教訓）。
- 時間一律 `serverTimestamp()`，排序用 `orderBy("createdAt")`。
- UI 前端隱藏別人的刪除鈕只是**體驗**；真正的「只能刪自己的」要靠安全規則保證——但那是**練習 C** 的事。這個練習先用 Test mode（開放規則）跑通功能，練習 C 再把安全規則補上。

## 期望輸出範例

```
情境：兩個視窗
  視窗 A（Alice 登入）發「大家好」
  → A 立刻看到自己的留言（含刪除鈕）
  → 視窗 B（Bob 登入）不用刷新，「大家好」立刻出現（無刪除鈕，因為不是 Bob 發的）

情境：Bob 回覆
  視窗 B（Bob）發「哈囉」
  → A、B 兩邊都立刻看到「哈囉」，B 那邊有刪除鈕、A 那邊沒有

情境：刪除
  Alice 刪掉自己的「大家好」
  → 兩邊的「大家好」立刻消失

情境：未登入
  登出後輸入框/送出鈕應停用或發言被擋
```

## 如果你卡住了

1. **新留言沒即時出現**：你是不是用了 `getDocs`（一次性）？即時要用 `onSnapshot`。而且監聽只註冊一次，別在每次發言後重新查。
2. **留言順序亂**：確認你存了 `serverTimestamp()` 且查詢有 `orderBy("createdAt")`。剛送出的留言 `createdAt` 可能短暫是 null（等伺服器填），這是正常的樂觀更新（Ch 11 進階）。
3. **每則留言都要查一次作者、很慢**：別這樣。發言時就把 `authorName` 一起存進留言（反正規化，Ch 12），顯示時直接讀。
4. **刪除鈕每則都顯示 / 都不顯示**：比對 `msg.authorId === auth.currentUser?.uid` 決定顯不顯示。注意未登入時 `currentUser` 是 null，用 `?.`。
5. **登出後監聽還在報錯**：登出時要 `unsubscribe()`，或在 `onAuthStateChanged` 裡管理監聽的開關。

## 實作步驟建議

### Step 1：登入骨架
先把練習 A 的登入接進來（匿名 + 一種正式登入即可）。確認 `onAuthStateChanged` 能切換「登入前 / 留言板」畫面。

### Step 2：即時顯示留言（先不管發言）
登入後，用 `onSnapshot(query(collection(db,"messages"), orderBy("createdAt")))` 監聽，把每則留言 render 出來。先手動在 Console 加幾筆資料測試即時顯示。

### Step 3：發言
接上輸入框 + 送出，用 `addDoc` 寫入 `{ text, authorId, authorName, createdAt: serverTimestamp() }`。送出後**不用**手動加到畫面——`onSnapshot` 會處理。

### Step 4：刪除自己的
render 每則留言時，若 `authorId === currentUser.uid`，多畫一個刪除鈕，接 `deleteDoc`。

### Step 5：清理與收尾
在登出時 `unsubscribe`，測試兩個視窗的即時效果，處理未登入狀態。

## 完整參考解答

**先自己做！**

<details>
<summary>點開參考實作（index.html）</summary>

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>練習 B — 即時留言板</title>
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 30px auto; }
    #board { border: 1px solid #ccc; padding: 10px; min-height: 200px; margin: 10px 0; }
    .msg { padding: 6px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; }
    .meta { color: #888; font-size: 0.8em; }
    .composer { display: flex; gap: 8px; }
    .composer input { flex: 1; padding: 8px; }
    button { padding: 6px 10px; }
  </style>
</head>
<body>
  <h2>即時留言板</h2>

  <div id="auth">
    <button id="anonBtn">匿名進入</button>
    <button id="googleBtn">用 Google 進入</button>
  </div>

  <div id="app" style="display:none">
    <p>你好，<span id="me"></span> <button id="logoutBtn">登出</button></p>
    <div id="board"></div>
    <div class="composer">
      <input id="text" placeholder="說點什麼…">
      <button id="sendBtn">送出</button>
    </div>
  </div>

  <script type="module">
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import { getAuth, onAuthStateChanged, signInAnonymously,
             GoogleAuthProvider, signInWithPopup, signOut }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";
    import { getFirestore, collection, addDoc, deleteDoc, doc,
             query, orderBy, onSnapshot, serverTimestamp }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-firestore.js";

    const app = initializeApp({ /* 你的 config */ });
    const auth = getAuth(app);
    const db = getFirestore(app);
    const $ = id => document.getElementById(id);

    let unsubMessages = null;   // 保存監聽的取消函式

    onAuthStateChanged(auth, (user) => {
      if (user) {
        $("auth").style.display = "none";
        $("app").style.display = "block";
        $("me").textContent = user.displayName || user.email || "訪客";
        startListening();
      } else {
        $("auth").style.display = "block";
        $("app").style.display = "none";
        if (unsubMessages) { unsubMessages(); unsubMessages = null; }  // 登出清理
      }
    });

    function startListening() {
      if (unsubMessages) return;   // 避免重複註冊
      const q = query(collection(db, "messages"), orderBy("createdAt"));
      unsubMessages = onSnapshot(q, (snap) => {
        const msgs = snap.docs.map(d => ({ id: d.id, ...d.data() }));
        render(msgs);
      });
    }

    function render(msgs) {
      const me = auth.currentUser?.uid;
      $("board").innerHTML = msgs.map(m => {
        const time = m.createdAt?.toDate ? m.createdAt.toDate().toLocaleTimeString() : "傳送中…";
        const delBtn = m.authorId === me
          ? `<button data-del="${m.id}">刪除</button>` : "";
        return `<div class="msg">
                  <span><b>${escapeHtml(m.authorName || "訪客")}</b>：${escapeHtml(m.text)}
                    <span class="meta">${time}</span></span>
                  ${delBtn}
                </div>`;
      }).join("");
      // 綁刪除
      $("board").querySelectorAll("[data-del]").forEach(btn => {
        btn.onclick = () => deleteDoc(doc(db, "messages", btn.dataset.del));
      });
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"]/g, c =>
        ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
    }

    async function send() {
      const text = $("text").value.trim();
      if (!text) return;
      const user = auth.currentUser;
      await addDoc(collection(db, "messages"), {
        text,
        authorId: user.uid,
        authorName: user.displayName || user.email || "訪客",
        createdAt: serverTimestamp()
      });
      $("text").value = "";   // 清空，不用手動加到畫面（onSnapshot 會處理）
    }

    $("anonBtn").onclick = () => signInAnonymously(auth);
    $("googleBtn").onclick = () => signInWithPopup(auth, new GoogleAuthProvider());
    $("logoutBtn").onclick = () => signOut(auth);
    $("sendBtn").onclick = send;
    $("text").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  </script>
</body>
</html>
```

**解答說明**：

- **監聽生命週期**：`unsubMessages` 保存取消函式，登出時呼叫、避免重複註冊（Ch 11 的教訓）。
- **反正規化 `authorName`**：發言時就把名字存進留言，`render` 直接讀，不用每則查使用者（Ch 12）。
- **刪除鈕靠 `authorId === me`**：只有自己的留言顯示刪除鈕。注意 `auth.currentUser?.uid` 的 `?.` 防未登入。
- **`createdAt?.toDate()` 防樂觀更新的 null**：剛送出、伺服器還沒填 timestamp 時 `createdAt` 是 null，顯示「傳送中…」（Ch 11 進階的 `hasPendingWrites` 現象）。
- **注意剛送出訊息的「位置跳動」**：升序 `orderBy("createdAt")` 下，`createdAt` 為 null 的 pending 訊息會**排在最前面**（Firestore 排序中 null 最小），等伺服器填上時間戳後才「跳」到正確的底部位置。這在以排序為賣點的即時板上會讓人愣一下。想讓新訊息自然出現在頂部、避免這個跳動，可改用 `orderBy("createdAt","desc")`（新訊息在上），pending 的 null 一樣排最前、正好就是頂部。
- **`escapeHtml`**：把使用者輸入當文字插進 innerHTML 有 XSS 風險，一定要跳脫。這是任何顯示使用者內容的地方都該做的（練習 C 的安全規則管資料庫層，這個管顯示層）。
- **送出後不手動更新畫面**：交給 `onSnapshot`，這是 Ch 11 的核心心法。

</details>

## 測試用例

| 操作 | 預期結果 | 驗證重點 |
|---|---|---|
| 兩視窗，A 發言 | B 不刷新即看到 | onSnapshot 即時 |
| A 看自己的留言 | 有刪除鈕 | authorId === me |
| B 看 A 的留言 | 無刪除鈕 | 別人的不給刪鈕 |
| A 刪自己的留言 | 兩邊都消失 | deleteDoc + 即時 |
| 連發三則 | 按時間順序排列 | serverTimestamp + orderBy |
| 剛送出瞬間 | 顯示「傳送中…」再變時間 | 樂觀更新 null 處理 |
| 登出再登入 | 不會累積多個監聽（留言不重複閃動） | unsubscribe 正確 |
| 輸入 `<script>` | 顯示為文字，不執行 | escapeHtml |

## 延伸挑戰（加分）

- **只載最近 50 則 + 載入更多**：`limit(50)` + `startAfter` 分頁（Ch 10），避免留言多了一次拉爆。
- **編輯自己的留言**：加編輯功能，用 `updateDoc`，並存一個 `editedAt`。
- **顯示「正在輸入…」**：用另一個集合存「誰正在打字」的即時狀態（`onSnapshot` + `setDoc`/`deleteDoc`）。
- **按讚**：每則留言加 `likeCount`，用 `increment`（Ch 9），並防止同一人重複按（存 `likedBy` 陣列用 `arrayUnion`）。
- **多房間**：把 `messages` 改成 `rooms/{roomId}/messages` 子集合（Ch 12），做多個聊天室。

## 自我檢核

- [ ] 兩個視窗能即時互看留言，我親眼驗證過
- [ ] 我用 `onSnapshot` 而非 `getDocs`，且送出後沒手動更新畫面
- [ ] 作者名稱是反正規化存的，我沒有每則留言查一次使用者
- [ ] 刪除鈕只在自己的留言顯示，靠 authorId 比對
- [ ] 我正確處理了監聽的清理（登出 unsubscribe、不重複註冊）
- [ ] 我對使用者輸入做了跳脫，理解為什麼要防 XSS
- [ ] 能說出我的實作和參考解答的差異與取捨

你做出了一個能用的即時留言板！但它現在有個大破綻：**規則是 Test mode，任何人都能改、刪別人的留言**——前端藏了刪除鈕沒用，有心人直接用程式碼就能刪別人的。這正是下一個 Part 要解決的：安全規則，讓「只能刪自己的」在**資料庫層**被真正強制。這是這門課的靈魂。

→ [Ch 15 — 為什麼需要安全規則](./15-why-security-rules.md)
