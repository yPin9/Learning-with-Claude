# Ch 7 — 登入狀態管理

> **目標**：學會用 `onAuthStateChanged` 監聽登入狀態的變化，讓你的 UI 自動反映「現在登入的是誰、還是沒登入」，並正確處理登出、以及「重新整理頁面後登入狀態還在」的機制。這是把認證接進真實 UI 的最後一哩。

> **環境**：Firebase JS SDK v12.18.0。狀態監聽、登出行為以 Auth 模擬器實跑驗證。

## 為什麼需要這個？

前兩章你會讓使用者登入了，但有個問題沒解決：**登入之後，畫面怎麼知道要變？** 登入前該顯示「登入按鈕」，登入後該顯示「你好，Alice」和「登出」。而且——重新整理頁面後，使用者應該還是登入狀態，不用每次都重登。

新手最常見的錯誤，是登入成功後**直接**去讀 `auth.currentUser` 更新畫面。這在很多情況會失敗，原因下面講。正解是**監聽狀態變化**，而不是主動去問。

## 先建立直覺：不要「問」狀態，要「訂閱」狀態

想像兩種取得資訊的模式：

```
   ❌ 輪詢式（主動問）                  ✅ 訂閱式（被通知）
   「現在誰登入了？」                    「狀態一變就通知我」
   if (auth.currentUser) {...}         onAuthStateChanged(auth, user => {
                                          // 每次登入/登出/初始化，
   問題：                                 // 這裡自動被呼叫
   - 頁面剛載入時，SDK 可能還在           })
     背景還原登入狀態，你「問」
     的當下 currentUser 可能是 null，
     過一下才變有值 → 你錯過了
```

**核心觀念**：登入狀態不是一個「你想讀就讀」的靜態值，它是一條**會變化的事件流**——頁面載入時要從本地還原、使用者可能登入登出、token 可能刷新。你該做的是**註冊一個監聽器**，讓 Firebase 在「狀態確定」和「每次狀態改變」時主動呼叫你，而不是自己在某個時間點去猜。

## onAuthStateChanged：認證狀態的訂閱

這是本章最重要的 API，你的 App 通常在啟動時就註冊它一次：

```js
import { getAuth, onAuthStateChanged } from "firebase/auth";

const auth = getAuth(app);

onAuthStateChanged(auth, (user) => {
  if (user) {
    // 有人登入：user 就是當前使用者
    console.log("已登入:", user.uid, user.email);
    // → 這裡更新 UI 成「登入後」的樣子
  } else {
    // 沒人登入（未登入，或剛登出）
    console.log("未登入");
    // → 這裡更新 UI 成「登入前」的樣子
  }
});
```

**這個 callback 會在三種時機被呼叫**：

1. **一註冊就先呼叫一次**——告訴你「目前」的狀態（頁面剛開，可能是已登入或未登入）。
2. **使用者登入時**——`user` 從 `null` 變成使用者物件。
3. **使用者登出時**——`user` 變回 `null`。

所以你只要把「根據 user 有沒有值來更新 UI」的邏輯寫在這一個地方，登入、登出、重新整理，**全部情況都自動涵蓋**——你不用在登入函式成功後、登出函式成功後各寫一遍更新 UI 的程式碼。這是它最省事的地方。

## 登出：signOut

```js
import { signOut } from "firebase/auth";

async function logout() {
  await signOut(auth);
  // 不用手動更新 UI —— 上面的 onAuthStateChanged 會被觸發，
  // user 變成 null，UI 自動切回「登入前」
}
```

實測 `signOut` 後：

```
[signout] currentUser is null: true
```

登出後 `auth.currentUser` 立刻變 `null`，而且你註冊的 `onAuthStateChanged` 會收到一次 `user === null` 的通知。**登出後不用自己動 UI**——監聽器會處理，這就是把 UI 更新集中在一處的好處。

## 為什麼重新整理後還是登入狀態？

你登入後按 F5 重新整理，會發現**還是登入的**。這不是魔法：

```
   登入成功
      │
      ▼
   SDK 把「憑證」（refresh token 等）存進瀏覽器的
   IndexedDB / localStorage（本機持久化儲存）
      │
   ─── 你重新整理頁面 / 隔天再打開 ───
      │
      ▼
   SDK 啟動時，從本機儲存讀回憑證，
   在背景跟 Google 換一張新的 ID token
      │
      ▼
   換到了 → onAuthStateChanged 呼叫你，user 有值（自動還原登入）
```

**這就是為什麼「不要一載入頁面就急著讀 `auth.currentUser`」**：頁面剛開的那一瞬間，SDK 可能還在「從本機讀憑證、跟伺服器換 token」的過程中，`auth.currentUser` 暫時是 `null`，還原完成後才變有值。如果你在還原完成前就讀，會誤判成「未登入」。而 `onAuthStateChanged` 會**等狀態確定後**才呼叫你，天生就避開了這個時機問題。

> **控制「記住多久」——persistence（持久性）**：預設是「本機持久化」（`browserLocalPersistence`），關瀏覽器再開仍登入。你可以改：`browserSessionPersistence`（只在這個分頁有效，關了就登出）或 `inMemoryPersistence`（重整就登出，最嚴格）。用 `setPersistence(auth, browserSessionPersistence)` 設定。「記住我」這種功能就是切換這個。

## 完整範例：一個會反映登入狀態的頁面

把 Ch 5、6、7 拼起來，一個依登入狀態切換畫面的完整頁面：

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head><meta charset="UTF-8"><title>登入狀態</title></head>
<body>
  <!-- 登入前顯示 -->
  <div id="loggedOut">
    <input id="email" placeholder="email">
    <input id="password" type="password" placeholder="密碼">
    <button id="signinBtn">登入</button>
    <button id="googleBtn">用 Google 登入</button>
  </div>

  <!-- 登入後顯示 -->
  <div id="loggedIn" style="display:none">
    <p id="welcome"></p>
    <button id="logoutBtn">登出</button>
  </div>

  <script type="module">
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import { getAuth, onAuthStateChanged, signInWithEmailAndPassword,
             GoogleAuthProvider, signInWithPopup, signOut }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js";

    const auth = getAuth(initializeApp({ /* 你的 config */ }));
    const $ = id => document.getElementById(id);

    // 【核心】一處監聽，處理所有狀態切換
    onAuthStateChanged(auth, (user) => {
      if (user) {
        $("loggedOut").style.display = "none";
        $("loggedIn").style.display = "block";
        $("welcome").textContent = `你好，${user.displayName || user.email}`;
      } else {
        $("loggedOut").style.display = "block";
        $("loggedIn").style.display = "none";
      }
    });

    // 各個動作只管「觸發」狀態改變，不管更新 UI（交給上面的監聽器）
    $("signinBtn").onclick = () =>
      signInWithEmailAndPassword(auth, $("email").value, $("password").value)
        .catch(e => alert(e.code));
    $("googleBtn").onclick = () =>
      signInWithPopup(auth, new GoogleAuthProvider()).catch(e => alert(e.code));
    $("logoutBtn").onclick = () => signOut(auth);
  </script>
</body>
</html>
```

注意這個結構的精髓：**登入/登出的按鈕只負責「觸發」動作，完全不碰 UI 切換；所有 UI 切換集中在 `onAuthStateChanged` 一處。** 登入成功 → 狀態變 → 監聽器被呼叫 → UI 切換。登出同理。這讓狀態和畫面永遠一致，不會出現「登出了但畫面還顯示使用者名字」的 bug。

## 進階時機：等狀態確定再決定要不要導頁

實務上常需要「還沒登入就踢去登入頁」。但因為前述的「還原時機」問題，你不能頁面一載入就檢查。正確做法還是在 `onAuthStateChanged` 裡做：

```js
onAuthStateChanged(auth, (user) => {
  if (!user) {
    window.location.href = "/login.html";  // 確定沒登入才導頁
  } else {
    renderApp(user);
  }
});
```

如果你需要「只等第一次狀態確定，之後不再監聽」，用 `onAuthStateChanged` 回傳的取消函式，或用 `authStateReady()`（回傳一個 Promise，狀態首次確定後 resolve）：

```js
await auth.authStateReady();   // 等 SDK 還原完成
if (auth.currentUser) { ... }  // 此時讀 currentUser 才可靠
```

## 踩雷集錦

1. **頁面載入就讀 `auth.currentUser`**：最經典的錯誤。剛載入時 SDK 可能還在還原登入，`currentUser` 暫時是 `null`，你會誤判未登入然後把使用者踢走。**永遠在 `onAuthStateChanged`（或 `await authStateReady()` 之後）判斷。**
2. **在登入函式成功後手動更新 UI**：能動，但你會在登入、登出、Google 登入…每個地方各寫一次更新 UI，還容易漏。把 UI 更新**只**放在 `onAuthStateChanged`，各動作只負責觸發。
3. **重複註冊 `onAuthStateChanged`**：每次某事件就 `onAuthStateChanged(...)` 註冊一個新的，會累積多個監聽器、callback 被呼叫多次。通常整個 App 只在啟動時註冊**一次**。若在元件裡註冊（React/Vue），記得在卸載時呼叫它回傳的取消函式。
4. **以為 `signOut` 要自己清 UI 和資料**：`signOut` 後監聽器會通知你 user 變 null，UI 交給監聽器。但注意：登出**不會**自動清掉你自己存在畫面上、變數裡的使用者資料，那些要你自己清（或靠監聽器切回登入前畫面自然蓋掉）。
5. **把 persistence 設成 inMemory 還抱怨「重整就登出」**：那是 `inMemoryPersistence` 的預期行為。要「關瀏覽器還記得」用預設的 local；要「關分頁就登出」用 session。搞清楚你要哪種。
6. **忘記 Google 登入的 `displayName` 才有值、email 登入可能是 null**：範例用 `user.displayName || user.email` 做後備正是為此（Ch 5 提過 email 註冊 displayName 預設 null）。直接顯示 `user.displayName` 對 email 使用者會是「你好，null」。

## 進階：再往深一層

- **`onIdTokenChanged` vs `onAuthStateChanged`**：前者除了登入/登出，還會在 **token 刷新**（每小時）時觸發，適合「需要拿到最新 token 傳給自己後端」的場景。一般 UI 用 `onAuthStateChanged` 就好；要即時同步 token 到後端才用 `onIdTokenChanged`。
- **多分頁同步**：因為狀態存在共用的本機儲存，你在一個分頁登出，**其他分頁的 `onAuthStateChanged` 也會收到通知**跟著登出。這是內建的、免費的跨分頁同步，你不用寫任何東西。可以開兩個分頁實測：一邊登出，看另一邊自動切回登入前。
- **框架整合**：在 React 裡通常把 `onAuthStateChanged` 包進一個 `AuthContext`，用 `useEffect` 註冊、回傳的函式在 cleanup 取消，把 `user` 放進 context 給全 App 用。概念一樣，只是套進框架的生命週期。本課用原生 JS，但這個模式直接可搬。
- **安全提醒**：`onAuthStateChanged` 給你的 UI 判斷「登入了沒」只是**體驗**層面（決定畫面顯示什麼）。**真正的安全**還是靠安全規則（Part 4）——就算有人繞過你的前端判斷，安全規則在資料庫那關還會擋。前端的登入判斷方便使用者，後端的規則保護資料，兩者分工，別把前端判斷當成安全防線。

## 本章重點整理

- 登入狀態是**事件流**不是靜態值，用 **`onAuthStateChanged` 訂閱**，別主動去讀 `currentUser`。
- 監聽器在**註冊時、登入時、登出時**都會被呼叫，把 UI 更新**集中在這一處**，各動作只負責觸發。
- 重整後仍登入，是因為 SDK 把憑證存在本機、啟動時自動還原——所以剛載入時別急著讀 `currentUser`。
- **`signOut(auth)`** 後 `currentUser` 變 `null`，監聽器會通知，UI 自動切回。
- 前端的登入判斷是**體驗**，資料安全靠**安全規則**（Part 4），兩者分工。

## 自我檢核

- [ ] 我能解釋為什麼「頁面載入就讀 `auth.currentUser`」是錯的，正解是什麼
- [ ] 我能說出 `onAuthStateChanged` 的 callback 會在哪幾個時機被呼叫
- [ ] 我的登入/登出按鈕只負責觸發動作，UI 切換集中在一處
- [ ] 我知道「重整後還登入」背後是本機憑證還原，以及怎麼用 persistence 控制記住多久
- [ ] 我理解前端登入判斷是體驗、安全靠規則，這兩件事的分工

## 延伸閱讀

### 官方文件

- **[Manage Users / Get the currently signed-in user](https://firebase.google.com/docs/auth/web/manage-users#get_the_currently_signed-in_user)**
  - **讀哪裡**：「Get the currently signed-in user」那段，官方明確建議用 observer（`onAuthStateChanged`）而非直接讀 `currentUser`，和本章核心一致。
  - **能學到什麼**：官方為什麼推 observer 模式，以及各種 user 屬性。

- **[Authentication State Persistence](https://firebase.google.com/docs/auth/web/auth-state-persistence)**
  - **讀哪裡**：三種 persistence（local / session / none）的說明表。
  - **能學到什麼**：本章「記住多久」的完整選項與 `setPersistence` 用法，做「記住我」功能必讀。

### 影片 / 文章

- **[Fireship — Firebase Auth 狀態與路由保護](https://www.youtube.com/c/Fireship/search?query=firebase+auth)** — Jeff Delaney
  - **這支說什麼**：如何用 auth 狀態做「未登入導去登入頁」的路由守衛。
  - **為什麼值得看**：把本章「等狀態確定再導頁」放進真實 App 結構。

Part 2 到此結束——你已經能讓使用者用多種方式登入、並讓 UI 正確反映狀態。是時候把 Ch 4–7 整合成一個真正的東西了。下一個是練習 A：親手做一個完整的登入頁。

→ [練習 A：完整登入頁](./practice-a-login-page.md)
