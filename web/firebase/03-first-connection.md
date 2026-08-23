# Ch 3 — 第一次連上：Console、config、hello world

> **目標**：寫下你的第一段真正連上 Firebase 的程式碼，從一個純 HTML 網頁把一筆資料寫進雲端 Firestore，然後**親眼在 Console 看到它跳出來**。這章結束時，你會有「我的網頁真的連上雲端後端了」的實感——這正是你最初想要的那一刻。

> **環境**：Firebase JS SDK v12.18.0（modular），任一現代瀏覽器。本章的 SDK 程式碼已用相同 API 對 Firestore 模擬器實跑驗證通過；「在 Console 看到資料」的步驟需連你自己的真實專案，由你操作。

## 為什麼需要這個？

前兩章都在講概念。但 Firebase 是「做中學」的技術——你讀十遍「前端直連資料庫」，都不如**親手寫一筆資料、看它出現在雲端**來得有感。這章的目的就是製造那個「啊，它真的連上了！」的瞬間，把抽象概念錨定成肌肉記憶。

## 先建立直覺：連上 Firebase 需要三樣東西

你的網頁要能跟雲端專案對話，需要湊齊三樣：

```
   ┌──────────────────────────────────────────────┐
   │  1. 一個開好的服務          （Firestore 資料庫）│
   │     ── 在 Console 建立、選地區                  │
   │                                              │
   │  2. 一把「認得你專案」的鑰匙  （Firebase config）│
   │     ── 從 Console 專案設定複製那串金鑰          │
   │                                              │
   │  3. 連線用的函式庫          （Firebase JS SDK） │
   │     ── import 進你的網頁程式碼                  │
   └──────────────────────────────────────────────┘
              三樣湊齊 → initializeApp() → 連上
```

這章就是照這三步走：建資料庫 → 拿 config → 寫程式碼連上。

## Step 1：在 Console 建立 Firestore 資料庫

> 這步在 Console 網頁操作，由你自己點。

1. 進 [Firebase Console](https://console.firebase.google.com/)，選你在 Ch 0 建的專案。
2. 左側選 **Build → Firestore Database**，按 **「建立資料庫 / Create database」**。
3. 選**地區（location）**：選離你近的（如 `asia-east1` 台灣、`asia-northeast1` 東京）。**地區選了不能改**，但練習用隨便選近的即可。
4. 選啟動模式：會問「Production mode」還是「Test mode」。**這次選 Test mode**——它會套用一條「30 天內允許任何人讀寫」的寬鬆規則，方便我們先跑起來。

> ⚠️ **Test mode 很危險，只是為了教學**：它等於把資料庫大門敞開 30 天。這正是 Ch 1 說的「規則寫錯 = 門戶大開」的活教材。Part 4 我們會學怎麼寫真正安全的規則，把這扇門關好。現在先用 Test mode 感受連線，但**絕不要把 Test mode 的專案放上真實產品**。

建完你會進到一個空的資料庫畫面——這就是你等一下要寫入資料的地方，把這個分頁**開著別關**。

## Step 2：拿到你的 Firebase config

> 這步也在 Console 操作。

1. 點左上角**齒輪 → 專案設定（Project settings）**。
2. 往下滑到 **「你的應用程式 / Your apps」**，如果還沒有 Web App，點 **`</>`（Web）** 圖示新增一個，取個暱稱（例如 `web-demo`），**不用**勾「Firebase Hosting」（Ch 20 再設）。
3. 註冊後，Console 會顯示一段 `firebaseConfig` 程式碼，長這樣：

```js
const firebaseConfig = {
  apiKey: "AIzaSyD...(一長串)",
  authDomain: "my-first-firebase-a1b2c.firebaseapp.com",
  projectId: "my-first-firebase-a1b2c",
  storageBucket: "my-first-firebase-a1b2c.appspot.com",
  messagingSenderId: "1234567890",
  appId: "1:1234567890:web:abcdef123456"
};
```

**把這整段複製起來**，等一下貼進程式碼。這串就是 Step 圖裡的「鑰匙」。

> 小提醒：`storageBucket` 這一欄，2024 年 10 月之後**新建的專案** Console 顯示的可能是 `你的專案.firebasestorage.app` 而非 `.appspot.com`。**以你 Console 實際顯示的為準**複製即可，兩種都對。

> **`apiKey` 是密碼嗎？可以公開嗎？** 這是 Firebase 最常被誤解的一點：**這個 `apiKey` 不是密碼，公開沒關係。** 它只是用來「識別你是哪個 Firebase 專案」，不是用來「授權存取」。任何人打開你的網站都能在原始碼看到它——這是**設計上就預期**的。真正保護你資料的是**安全規則**（Part 4），不是把這串藏起來。這也是為什麼 Firebase 的資安模型跟傳統「API key 要保密」的直覺不同。（例外：真正要保密的是 service account 私鑰，那是後端用的，跟這個前端 config 不同東西。）

## Step 3：寫 hello world 網頁

現在寫程式碼。建一個資料夾，裡面放一個 `index.html`。我們用瀏覽器原生的 ES module，直接從 CDN import Firebase SDK——**不需要 npm、不需要打包工具**，存檔用瀏覽器打開就能跑。這是體驗連線最快的方式。

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>Firebase Hello World</title>
</head>
<body>
  <h1>Firebase 第一次連線</h1>
  <button id="writeBtn">寫一筆資料到 Firestore</button>
  <p id="status">尚未寫入</p>

  <script type="module">
    // 1. 從 CDN import 需要的函式（modular v9+ 寫法）
    import { initializeApp } from "https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js";
    import { getFirestore, collection, addDoc, serverTimestamp }
      from "https://www.gstatic.com/firebasejs/12.18.0/firebase-firestore.js";

    // 2. 貼上你從 Console 複製的 config（換成你自己的！）
    const firebaseConfig = {
      apiKey: "換成你的",
      authDomain: "換成你的.firebaseapp.com",
      projectId: "換成你的",
      storageBucket: "換成你的.appspot.com",
      messagingSenderId: "換成你的",
      appId: "換成你的"
    };

    // 3. 初始化 App，並拿到 Firestore 的 handle
    const app = initializeApp(firebaseConfig);
    const db = getFirestore(app);

    // 4. 按鈕按下時，寫一筆資料進 "messages" collection
    const statusEl = document.getElementById("status");
    document.getElementById("writeBtn").addEventListener("click", async () => {
      try {
        const docRef = await addDoc(collection(db, "messages"), {
          text: "Hello Firebase!",
          createdAt: serverTimestamp()
        });
        statusEl.textContent = "寫入成功！文件 ID：" + docRef.id;
      } catch (err) {
        statusEl.textContent = "寫入失敗：" + err.message;
      }
    });
  </script>
</body>
</html>
```

把 `firebaseConfig` 換成你自己的，存檔，用瀏覽器打開這個 `index.html`（直接雙擊即可），按下按鈕。

> **為什麼可以直接雙擊打開、不用架伺服器？** 因為我們用 CDN 的 ES module（`type="module"` + 完整 https 網址），瀏覽器能直接載入。這是體驗連線最無痛的方式。到了要用 Google 登入（Ch 6）或部署（Ch 20）時，才會需要本地伺服器或建置工具——現在先享受零設定。

## Step 4：見證連線——三個地方同時發生的事

按下按鈕後，如果一切正確：

**(a) 網頁上**，狀態文字變成：

```
寫入成功！文件 ID：kJ8fQ2mN...（一串 20 字元的隨機 ID）
```

那串 ID 是 Firestore 自動產生的文件 ID（`addDoc` 的特性，Ch 9 細講）。實測 `addDoc` 產生的自動 ID 固定是 **20 個字元**。

**(b) 回到 Console 的 Firestore 分頁**（Step 1 開著的那個），你會看到——不用重新整理——一個叫 `messages` 的 collection 冒出來，裡面有一筆文件：

```
messages (collection)
  └─ kJ8fQ2mN...（document）
       ├─ text: "Hello Firebase!"
       └─ createdAt: 2026年8月23日 ...（時間戳）
```

**這一刻就是重點**：你在**自己電腦的瀏覽器**按了按鈕，資料**瞬間出現在 Google 雲端**的 Console 裡。沒有伺服器、沒有後端 API、沒有資料庫連線字串——你的網頁**直接**把資料寫進了雲端資料庫。這就是「連上了」。

**(c) 那個 `createdAt` 欄位**：你程式碼裡寫的是 `serverTimestamp()`，它不是你電腦的時間，而是一個「請 Firebase 伺服器在寫入當下填上它的時間」的指令。實測這個值存進去後會變成一個 Firestore 的 `Timestamp` 物件。這證明了資料真的經過了伺服器，不是只留在你瀏覽器。

> 多按幾次按鈕，看 Console 裡的文件一筆一筆增加。再開一個瀏覽器分頁打開同一個 `index.html`，在一邊寫、看另一邊的 Console——你正在見證雲端資料庫的即時性（Ch 11 會讓網頁本身也即時更新）。

## 剛剛那段程式碼到底做了什麼

逐行拆解四個關鍵動作：

```js
const app = initializeApp(firebaseConfig);
```
拿著你的 config（鑰匙），建立一個「Firebase App」物件——這是所有 Firebase 服務的**起點**。它此刻還沒真的連線，只是「準備好知道要連哪個專案」。

```js
const db = getFirestore(app);
```
從這個 app 拿到 **Firestore 服務的 handle**。之後所有資料庫操作都透過這個 `db`。同理，之後要用登入就 `getAuth(app)`、要用檔案就 `getStorage(app)`——**同一個 app，長出不同服務**。

```js
collection(db, "messages")
```
指向資料庫裡一個叫 `messages` 的 **collection（集合）**。注意：**這個 collection 不存在也沒關係**——Firestore 在你第一次寫入時自動建立它。你不需要像 SQL 那樣先 `CREATE TABLE`。

```js
await addDoc(collection(db, "messages"), { text: "...", createdAt: serverTimestamp() });
```
往那個 collection **新增一筆文件**，內容是後面那個物件。`addDoc` 會自動配一個唯一 ID。因為是網路操作，它回傳 Promise，所以用 `await`。

## 踩雷集錦

1. **忘記把 config 換成自己的**：範例裡是 `"換成你的"` 佔位字串，直接跑會連不上。一定要貼上你 Console 複製的那一份。
2. **`permission-denied` 錯誤**：如果狀態顯示寫入失敗且訊息含 `Missing or insufficient permissions`，代表你的 Firestore 不是 Test mode，或 Test mode 的 30 天過期了。回 Console 的 Firestore → 規則（Rules）分頁，暫時把規則改成 `allow read, write: if true;`（**只限練習**），發布後再試。
3. **`Failed to get document because the client is offline` 之類**：多半是 `projectId` 打錯，SDK 連到不存在的專案。仔細核對 config 裡的 `projectId` 和 Console 的專案 ID 一致。
4. **CDN 版本號和 import 對不上**：如果你把某一行的版本號改了、另一行沒改（例如 app.js 用 12.18.0、firestore.js 用 10.x），會出現奇怪錯誤。**所有 firebase CDN import 的版本號要一致**。
5. **以為 `apiKey` 洩漏很嚴重跑去重設**：不用。前端 config 本來就會公開，它不是祕密。保護資料靠安全規則。真的別把時間花在藏 config 上。
6. **用了舊版 v8 寫法**：如果你 Google 到 `firebase.initializeApp(config)` 然後 `firebase.firestore()`，那是舊的 v8 命名空間寫法，和本課的 `import { getFirestore }` 模組化寫法不相容。認明 `import { }` 開頭的才是 v9+。

## 進階：再往深一層

- **CDN vs npm 兩種載入方式**：本章為了無痛用 CDN import。實務專案（用 Vite/webpack 等打包工具）會改成 `npm install firebase` 然後 `import { getFirestore } from "firebase/firestore"`——注意 import 的**來源不同**（一個是完整 https 網址，一個是套件名），但**函式名完全一樣**。Ch 21 用模擬器、Ch 20 部署時會用到 npm 方式。
- **`initializeApp` 可以有多個**：一個網頁通常只 `initializeApp` 一次。但如果你要同時連兩個不同的 Firebase 專案，可以 `initializeApp(config2, "second")` 給第二個 app 取名字。少見，但知道有這回事。
- **tree-shaking（搖樹優化）**：v9+ 模組化寫法最大的好處是——你只 import 用到的函式（`addDoc`、`getFirestore`…），打包工具會把**沒用到的**程式碼砍掉，最終檔案更小。這就是官方從 v8 命名空間式（`firebase.firestore().xxx`，整包都載入）改成 v9 模組化的主因。你現在用的寫法，順便就享受到了這個優化。

## 本章重點整理

- 連上 Firebase 三步驟：**建服務**（Console 建 Firestore）→ **拿 config**（Console 複製鑰匙）→ **寫程式碼**（`initializeApp` + `getFirestore`）。
- `apiKey` **不是祕密**，公開是設計上預期的；保護資料靠**安全規則**不是藏 key。
- `initializeApp(config)` 是所有服務的起點；`getFirestore(app)`、`getAuth(app)` 從同一個 app 長出不同服務。
- Firestore 的 collection **不用預先建立**，第一次寫入自動生成，不像 SQL 要 `CREATE TABLE`。
- 你在瀏覽器寫的資料**直接**進了雲端 Console，中間沒有你自己的伺服器——這就是「前端直連」。

## 自我檢核

- [ ] 我親手跑過一次，在 Console 看到自己從網頁寫進去的資料
- [ ] 我能解釋為什麼 `apiKey` 可以公開，以及真正保護資料的是什麼
- [ ] 我能說出 `initializeApp`、`getFirestore`、`collection`、`addDoc` 各自做了什麼
- [ ] 我知道 Test mode 為什麼危險、它只是暫時的
- [ ] 遇到 `permission-denied`，我知道第一件事是去看 Firestore 的規則分頁

## 延伸閱讀

### 官方文件

- **[Add Firebase to your JavaScript project](https://firebase.google.com/docs/web/setup)**
  - **讀哪裡**：「Add the Firebase SDK」和「Initialize Firebase」兩節，就是本章 Step 2–3 的官方版本，含 CDN 與 npm 兩種寫法對照。
  - **能學到什麼**：官方對 config、初始化的權威說明；npm 方式的完整步驟（本章用 CDN，這裡補 npm）。

- **[Get started with Cloud Firestore](https://firebase.google.com/docs/firestore/quickstart)**
  - **讀哪裡**：「Add data」那段，對照本章的 `addDoc`。「Read data」先跳過，Ch 10 會講。
  - **能學到什麼**：官方 quickstart 的寫入範例，和本章互相印證。

- **[Firebase API keys 是否需要保密](https://firebase.google.com/docs/projects/api-keys)**
  - **讀哪裡**：整篇不長。標題就是「Learn about using and managing API keys for Firebase」。
  - **能學到什麼**：官方親自解釋「為什麼前端 apiKey 可以公開」，把本章的踩雷第 5 條講到底。遇到有人質疑「你 key 洩漏了」時，這是你的依據。

### 影片

- **[Fireship — Firebase Firestore 快速上手](https://www.youtube.com/c/Fireship/search?query=firestore)** — Jeff Delaney
  - **這支說什麼**：幾分鐘內從建專案到寫入第一筆資料，和本章同一條路徑的影片版。
  - **為什麼值得看**：如果本章某步卡住，看影片版對照畫面很有幫助。

你已經感受到「連上」的魔法了。但目前這個 App 有個大問題：任何人都能寫資料，而且我們還不知道「使用者是誰」。下一個 Part 就來解決「你是誰」——認證系統。先從原理開始：登入到底在背後發生了什麼、為什麼前端能「安全地」登入。

→ [Ch 4 — 認證原理：身分、授權、ID token / JWT](./04-auth-principles.md)
