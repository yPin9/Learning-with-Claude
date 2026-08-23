# 練習 A — 完整登入頁

> **目標**：把 Ch 4–7 學到的東西拼成一個真正能用的登入頁——支援 email 註冊/登入、Google 登入、匿名登入、登出，且 UI 正確反映登入狀態、錯誤有友善提示。完成後你驗證了「獨立實作一個完整認證流程」的能力。

## 背景與動機

幾乎每個 App 的第一個畫面都是登入頁。這個練習不是假題目——你做出來的東西，之後練習 B 的即時留言板、Final Project 都會直接用到。把它做扎實，後面就有現成的登入模組。

## 任務規格

做一個單一 `index.html`（延續課程的 CDN 寫法，可直接在瀏覽器跑；若要測 Google 登入需用本地伺服器）。要求：

| 功能 | 驗收標準 |
|---|---|
| **Email 註冊** | 輸入 email + 密碼可建立帳號；密碼少於 6 字要顯示友善錯誤 |
| **Email 登入** | 已註冊帳號可登入；帳密錯誤顯示「帳號或密碼錯誤」（不洩漏是哪個錯） |
| **Google 登入** | 按鈕觸發彈窗登入（測試需 `localhost`） |
| **匿名登入** | 「先試用」按鈕，以訪客身分進入 |
| **登出** | 登出後畫面切回登入前 |
| **狀態反映** | 登入後隱藏登入表單、顯示使用者名稱（或 email）與登出鈕；重整後維持登入 |
| **匿名升級**（加分） | 匿名狀態下可用 email 或 Google「升級」，且 uid 不變 |
| **錯誤處理** | 所有登入動作都 try/catch，依 `err.code` 給對應中文提示 |

**限制**：

- UI 狀態切換**只能**寫在 `onAuthStateChanged` 一處，各按鈕只負責觸發動作。
- 顯示使用者名稱用 `displayName || email || "訪客"` 的後備鏈。
- 錯誤提示**不可**區分「帳號不存在」和「密碼錯誤」。

## 期望輸出範例

```
情境：全新使用者
  輸入 new@example.com / abc12345 → 按「註冊」
  → 畫面切換：隱藏表單，顯示「你好，new@example.com」+ 登出鈕
  → Console 的 Authentication → Users 出現這個帳號

情境：密碼太短
  輸入 x@example.com / 123 → 按「註冊」
  → 顯示「密碼至少需要 6 個字元」，畫面不切換

情境：帳密錯誤
  輸入 new@example.com / wrongpassword → 按「登入」
  → 顯示「帳號或密碼錯誤」（不說是帳號還密碼）

情境：重新整理
  已登入狀態按 F5
  → 短暫後自動回到登入後畫面（不需重新登入）

情境：登出
  按「登出」
  → 畫面切回登入前，表單重新出現
```

## 如果你卡住了

1. **UI 沒切換**：檢查你是不是把「更新畫面」寫在按鈕的 click 裡，而不是 `onAuthStateChanged` 裡。狀態切換的唯一真相來源是那個監聽器。
2. **重整就變未登入**：你可能在頁面載入時直接讀了 `auth.currentUser` 來判斷。改成在 `onAuthStateChanged` 裡判斷——它會等還原完成。
3. **Google 登入失敗、彈窗沒出來**：你是不是用 `file://` 雙擊打開？Google 登入要 `localhost`，用 `npx serve .` 或 VS Code Live Server 開。
4. **不知道怎麼分辨匿名升級和重新註冊**：升級要保留 uid，用 `linkWithCredential`/`linkWithPopup`（Ch 6）；`createUser...` 會產生新 uid。
5. **錯誤碼對不上**：正式環境帳密錯是 `auth/invalid-credential`，模擬器是 `auth/wrong-password`——兩個都要在你的 humanize 裡處理（Ch 5）。

## 實作步驟建議

### Step 1：靜態 UI 骨架
先寫兩個 `<div>`：`#authForms`（登入前）和 `#userPanel`（登入後），各放對應的 input 和按鈕。先都顯示，確認版面。

### Step 2：初始化 + 狀態監聽
`initializeApp` + `getAuth`，寫 `onAuthStateChanged`，先只做「有 user 顯示 userPanel、沒有顯示 authForms」，用 `console.log` 印 user 確認觸發時機。

### Step 3：接上四種登入 + 登出
把 email 註冊/登入、Google、匿名、登出各接一個按鈕。每個都是「呼叫對應函式 + catch」，**不要**在這裡碰 UI。

### Step 4：錯誤 humanize
寫一個 `humanize(code)` 把常見 `err.code` 轉成中文提示，接到一個 `#error` 元素。故意觸發各種錯誤驗證。

### Step 5：匿名升級（加分）+ 收尾
在匿名登入狀態下，顯示「升級帳號」入口，用 `link...` 實作。測試升級前後 `uid` 相同。

## 完整參考解答

**先自己做，卡住再看！**

<details>
<summary>點開參考實作（index.html）</summary>

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>練習 A — 完整登入頁</title>
  <style>
    body { font-family: sans-serif; max-width: 420px; margin: 40px auto; }
    input, button { display: block; width: 100%; margin: 6px 0; padding: 8px; }
    #error { color: #c00; min-height: 1.2em; }
    .row { display: flex; gap: 8px; }
    .row button { margin: 6px 0; }
  </style>
</head>
<body>
  <h2>登入</h2>

  <div id="authForms">
    <input id="email" placeholder="email" value="alice@example.com">
    <input id="password" type="password" placeholder="密碼（至少 6 字）">
    <div class="row">
      <button id="signupBtn">註冊</button>
      <button id="signinBtn">登入</button>
    </div>
    <button id="googleBtn">用 Google 登入</button>
    <button id="anonBtn">先試用（匿名）</button>
  </div>

  <div id="userPanel" style="display:none">
    <p id="welcome"></p>
    <div id="upgradeBox" style="display:none">
      <p>你目前是訪客，升級以保存資料（uid 不變）：</p>
      <button id="upgradeGoogleBtn">用 Google 升級</button>
    </div>
    <button id="logoutBtn">登出</button>
  </div>

  <p id="error"></p>

  <script type="module">
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import {
      getAuth, onAuthStateChanged,
      createUserWithEmailAndPassword, signInWithEmailAndPassword,
      GoogleAuthProvider, signInWithPopup, signInAnonymously, signOut,
      EmailAuthProvider, linkWithCredential, linkWithPopup
    } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";

    const firebaseConfig = { /* 換成你的 config */ };
    const auth = getAuth(initializeApp(firebaseConfig));
    const $ = id => document.getElementById(id);
    const showError = msg => { $("error").textContent = msg; };

    function humanize(code) {
      switch (code) {
        case "auth/invalid-credential":
        case "auth/wrong-password":
        case "auth/user-not-found":
          return "帳號或密碼錯誤";
        case "auth/email-already-in-use":  return "這個 email 已經註冊過了";
        case "auth/weak-password":         return "密碼至少需要 6 個字元";
        case "auth/invalid-email":         return "email 格式不正確";
        case "auth/too-many-requests":     return "嘗試次數過多，請稍後再試";
        case "auth/popup-closed-by-user":  return "登入視窗被關閉了";
        default:                           return "發生錯誤：" + code;
      }
    }

    // 【唯一的 UI 真相來源】
    onAuthStateChanged(auth, (user) => {
      showError("");
      if (user) {
        $("authForms").style.display = "none";
        $("userPanel").style.display = "block";
        const name = user.displayName || user.email || "訪客";
        $("welcome").textContent = `你好，${name}（uid: ${user.uid.slice(0,8)}…）`;
        // 匿名使用者才顯示升級入口
        $("upgradeBox").style.display = user.isAnonymous ? "block" : "none";
      } else {
        $("authForms").style.display = "block";
        $("userPanel").style.display = "none";
      }
    });

    // 各動作只負責觸發 + 錯誤處理，不碰 UI 切換
    const wrap = fn => async () => {
      showError("");
      try { await fn(); } catch (e) { showError(humanize(e.code)); }
    };

    $("signupBtn").onclick = wrap(() =>
      createUserWithEmailAndPassword(auth, $("email").value, $("password").value));
    $("signinBtn").onclick = wrap(() =>
      signInWithEmailAndPassword(auth, $("email").value, $("password").value));
    $("googleBtn").onclick = wrap(() =>
      signInWithPopup(auth, new GoogleAuthProvider()));
    $("anonBtn").onclick = wrap(() => signInAnonymously(auth));
    $("logoutBtn").onclick = wrap(() => signOut(auth));

    // 匿名升級：uid 不變，資料保留
    $("upgradeGoogleBtn").onclick = wrap(() =>
      linkWithPopup(auth.currentUser, new GoogleAuthProvider()));
  </script>
</body>
</html>
```

**解答說明**：

- **UI 只在 `onAuthStateChanged` 切換**：所有按鈕透過 `wrap()` 只做「觸發動作 + catch 錯誤」，畫面切換全靠監聽器。這保證狀態和畫面永遠一致。
- **`wrap()` 高階函式**：把「清錯誤 → try → catch humanize」的樣板抽出來，每個動作一行搞定，不重複。
- **升級入口按 `isAnonymous` 顯示**：只有匿名使用者看得到，升級用 `linkWithPopup` 保 uid。
- **後備名稱鏈** `displayName || email || "訪客"`：涵蓋 Google（有 displayName）、email（可能只有 email）、匿名（都沒有 → 訪客）三種來源。
- 沒有任何地方讀「頁面載入當下的 `currentUser`」，所以重整不會誤判。

</details>

## 測試用例

| 操作 | 預期結果 | 驗證的重點 |
|---|---|---|
| 全新 email + 6 字密碼，按註冊 | 切到登入後畫面，Console Users 多一筆 | 註冊流程 |
| email + `123`，按註冊 | 顯示「密碼至少需要 6 個字元」 | 弱密碼錯誤處理 |
| 已註冊 email + 錯密碼，按登入 | 顯示「帳號或密碼錯誤」 | 不洩漏帳號存在 |
| 登入後按 F5 | 短暫後回到登入後畫面 | 狀態持久化 |
| 按登出 | 切回登入前畫面 | signOut + 監聽器 |
| 按「先試用」 | 進入登入後畫面，顯示升級入口 | 匿名登入 + isAnonymous 判斷 |
| 匿名狀態按「用 Google 升級」，記下 uid | 升級後 uid 與升級前相同 | link 保留 uid |
| 開兩個分頁，一邊登出 | 另一邊也自動切回登入前 | 跨分頁狀態同步 |

## 延伸挑戰（加分）

- **加「記住我」勾選框**：勾選用 `browserLocalPersistence`，不勾用 `browserSessionPersistence`（Ch 7 進階）。登入前用 `setPersistence` 設定。
- **加「忘記密碼」**：一個輸入 email + 按鈕，呼叫 `sendPasswordResetEmail`，提示「重設信已寄出」。
- **email 驗證流程**：註冊後呼叫 `sendEmailVerification`，並在登入後畫面顯示「你的 email 尚未驗證」提示（讀 `user.emailVerified`）。
- **升級衝突處理**：當升級的 email 已屬於另一帳號（`credential-already-in-use`），給出合理的引導訊息。

## 自我檢核

- [ ] 我的 UI 狀態切換只寫在 `onAuthStateChanged` 一處，各按鈕不碰 UI
- [ ] 四種登入 + 登出都能運作，且都有錯誤處理
- [ ] 錯誤提示不洩漏「帳號是否存在」
- [ ] 重整後登入狀態維持，且我知道背後原理
- [ ] （加分）我驗證了匿名升級後 uid 不變
- [ ] 能說出我的實作和參考解答的差異，並解釋各自取捨

Part 2 完成，你手上有一個能用的認證模組了。接下來進入這門課的重頭戲——資料庫。Part 3 我們要讓 App 真的能存取資料，而且是**即時**的：一邊改，另一邊立刻看到。先從「Firestore 到底是什麼、它的資料長什麼樣」開始。

→ [Ch 8 — Firestore 是什麼：document/collection 模型](./08-firestore-model.md)
