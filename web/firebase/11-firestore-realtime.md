# Ch 11 — 即時同步：onSnapshot 與底層機制

> **目標**：學會用 `onSnapshot` 訂閱資料，讓畫面在資料一變（不管是誰、從哪台裝置改的）時**自動更新**；理解即時同步底層是怎麼做到的（不是輪詢）；並學會正確管理監聽器生命週期避免記憶體洩漏。這是 Firestore 最值錢的功能，也是讓你的 App 有「即時感」的核心。

> **環境**：Firebase JS SDK v12.18.0。`onSnapshot` 的觸發行為以 Firestore 模擬器實跑驗證。

## 為什麼需要這個？

Ch 10 的 `getDocs` 是「拍一張照」——你讀到的是**那一瞬間**的值，之後資料庫變了，你手上的照片不會跟著變，要再讀一次才知道。

但很多 App 的靈魂是「即時」：聊天室對方發訊息你要馬上看到；多人協作看板別人移動卡片你要馬上看到；即時儀表板數字要自己跳。用 `getDocs` 做這些，你得**不停地重複讀**（輪詢），既慢又貴又醜。

Firestore 給你一個優雅太多的東西：**`onSnapshot`——訂閱一份資料，之後只要它變，Firestore 主動推給你。** 這章教你用它，並拆開它底層的魔法。

## 先建立直覺：拍照 vs 監視器

```
   getDocs（一次性讀取）          onSnapshot（即時訂閱）
   「拍一張照」                    「裝一台監視器」
        │                              │
   拿到當下的值                   先給你當下的值，
   之後資料變了                   之後只要一有變動，
   你的照片不會變                 監視器立刻把新畫面推給你
   要再拍一次                     你什麼都不用做
```

差別的本質：`getDocs` 是**你去問資料庫**（pull）；`onSnapshot` 是**資料庫變了主動通知你**（push）。後者讓「即時」變成免費的午餐。

## onSnapshot：訂閱一份資料

用法和 `getDocs` 很像，但**不回傳資料**——它回傳一個「取消訂閱」的函式，資料透過 callback 一次次送來：

```js
import { getFirestore, collection, onSnapshot } from "firebase/firestore";
const db = getFirestore(app);

const unsubscribe = onSnapshot(collection(db, "messages"), (querySnapshot) => {
  // 這個 callback 會被呼叫「很多次」：
  //   第一次：立刻，給你當下的全部資料
  //   之後：每次資料有任何變動，再被呼叫一次
  const msgs = querySnapshot.docs.map(d => ({ id: d.id, ...d.data() }));
  console.log("目前有", msgs.length, "則訊息");
  renderMessages(msgs);   // 每次都重繪，畫面永遠是最新的
});

// 之後不需要了（例如離開頁面），呼叫它停止監聽：
// unsubscribe();
```

實測 `onSnapshot` 的觸發行為——監聽一個空集合，然後往裡面加一筆：

```
[onSnapshot] fire #1 size: 0      ← 一訂閱就立刻觸發，給當下狀態（空的）
[onSnapshot] fire #2 size: 1      ← 加了一筆後，自動再觸發，size 變 1
```

**看清楚這個行為**：`onSnapshot` 一註冊就**立刻**呼叫你一次（給當下值），之後**每次**資料變動再呼叫。你不用手動「先讀一次再監聽」——第一次觸發就包含初始資料了。

### 監聽單一文件

同樣可以只監聽一份文件（不是整個集合）：

```js
import { doc, onSnapshot } from "firebase/firestore";

const unsub = onSnapshot(doc(db, "users", "uid_alice"), (docSnap) => {
  if (docSnap.exists()) {
    console.log("Alice 的資料變了:", docSnap.data());
  }
});
```

單文件監聽適合「這個人的個人資料 / 這篇文章的按讚數，一變就更新」。

## 見證即時：開兩個視窗

這是最有感的實驗，強烈建議動手：

1. 做一個頁面，用 `onSnapshot` 監聽 `messages` 集合、把每則訊息 render 出來，並有一個輸入框 + 按鈕用 `addDoc` 新增訊息。
2. **開兩個瀏覽器視窗**打開同一頁。
3. 在**視窗 A** 輸入一則訊息送出。
4. **視窗 B**——你什麼都沒做——**訊息立刻出現在 B 的畫面上**。

你剛剛做出了一個即時聊天室的核心，而且**沒寫任何 WebSocket、沒寫任何輪詢**。A 的 `addDoc` 寫進雲端，雲端把變動推給所有正在監聽的客戶端（包括 B），B 的 `onSnapshot` callback 被觸發、重繪。這就是即時同步。

> 更神奇的是：連 **A 自己**也會透過 `onSnapshot` 收到自己剛寫入的變動（甚至因為離線快取，在寫入送達伺服器**之前**就先樂觀地更新了——見下方進階）。所以你的「送出訊息後清空、把新訊息加到列表」邏輯，根本不用自己寫進 `addDoc` 的成功回呼裡，交給 `onSnapshot` 就好。

## 底層機制：它不是輪詢，是「掛一條線等推播」

新手最大的誤解是「它一定是每隔幾秒偷偷去問一次吧」。**不是。** 那樣既慢又浪費。真正的機制：

```
   客戶端（你的瀏覽器）              Firestore 後端
        │                              │
        │  onSnapshot 訂閱時，          │
        │  建立一條「長連線」──────────▶ │  記住：這個客戶端在關心
        │  （基於 WebChannel/gRPC）      │  「messages 這個查詢」
        │                              │
        │                              │  ┌─ 有人（任何裝置）寫入了
        │                              │  │  一筆符合的資料
        │  ◀──── 主動推送變動 ──────────┤ ─┘
        │  callback 被觸發               │  後端主動把「變了什麼」
        │  你重繪畫面                    │  沿著那條線推給所有訂閱者
        │                              │
```

關鍵點：

- **一條持久的連線**：訂閱時客戶端和後端之間建立一條長連線（底層是 WebChannel，一種在各種網路環境下都可靠的雙向通道，退化情況會用 long-polling，但概念是「掛著等推」不是「反覆問」）。
- **後端主動推**：資料變動時，是**後端**沿著這條線把變化推下來，客戶端被動接收。沒有變動時，這條線靜靜掛著，不耗流量。
- **只推「差異」**：後端聰明地只推「變了什麼」（哪些文件新增/修改/刪除），不是每次重傳整個結果集。這讓即時同步很省流量。

**對照輪詢**：如果用 `setInterval` 每 3 秒 `getDocs` 一次——沒變動時也在浪費請求和計費、有變動時最多要等 3 秒才看到、而且每次都重讀整批。`onSnapshot` 全面勝出：即時、省流量、只傳差異。這就是為什麼「即時」是 Firestore 的招牌而不是一個附加功能。

## 進階快照資訊：知道「變了什麼」

`onSnapshot` 的快照除了給你「現在全部的資料」，還能告訴你「這次相對上次，具體哪幾筆變了、怎麼變的」——用 `docChanges()`：

```js
onSnapshot(collection(db, "messages"), (snapshot) => {
  snapshot.docChanges().forEach((change) => {
    if (change.type === "added")    console.log("新增:", change.doc.data());
    if (change.type === "modified") console.log("修改:", change.doc.data());
    if (change.type === "removed")  console.log("刪除:", change.doc.id);
  });
});
```

這對「做動畫」「只更新變動的那一列而非整個列表重繪」很有用——例如聊天室新訊息用滑入動畫、被刪的訊息用淡出。第一次觸發時，所有現有文件都是 `added`。

## 監聽器生命週期：一定要 unsubscribe

`onSnapshot` 開了一條長連線和一個持續的 callback。**不再需要時一定要關掉**，否則：記憶體洩漏、無謂的計費（連線持續、變動持續推送持續算讀取）、以及對已卸載的畫面呼叫 callback 的錯誤。

```js
const unsubscribe = onSnapshot(collection(db, "messages"), cb);

// 離開頁面 / 切換到別的資料 / 元件卸載時：
unsubscribe();   // 關掉這個監聽
```

各情境的「該在哪關」：

- **原生 JS**：離開該畫面、或要換監聽別的東西之前，呼叫上一個的 `unsubscribe()`。
- **React**：在 `useEffect` 裡 `onSnapshot`，**return 那個 unsubscribe** 當 cleanup：

```js
useEffect(() => {
  const unsub = onSnapshot(collection(db, "messages"), snap => setMsgs(...));
  return unsub;   // 元件卸載時自動取消訂閱
}, []);
```

- **Vue**：`onUnmounted(() => unsub())`。

> **每筆推送都計費**：`onSnapshot` 每收到一筆文件（初始 + 之後每次變動）都算**一次讀取**。一個沒關的監聽器監聽一個熱門集合，會持續產生讀取費用。忘記 unsubscribe 不只是記憶體問題，是**帳單問題**。這是 Firestore 成本失控的常見原因之一（Ch 22）。

## 對比與取捨：getDocs vs onSnapshot

| | `getDocs`（一次性） | `onSnapshot`（即時） |
|---|---|---|
| 拿到的 | 當下的值，之後不變 | 當下值 + 之後每次變動 |
| 通訊模式 | 你問一次（pull） | 掛線等推（push） |
| 適合 | 一次性載入、不需即時（如設定頁） | 即時列表、聊天、協作、通知 |
| 要管理生命週期？ | 不用（拿完就結束） | **要**（記得 unsubscribe） |
| 計費 | 讀取一次 | 初始 + 每次變動都計 |

**選擇原則**：需要「一變就看到」用 `onSnapshot`；只是進頁面載一次、之後不管它變不變的，用 `getDocs`（更省，不用管理生命週期）。別無腦全用 `onSnapshot`——不需要即時的地方用它是浪費。

## 踩雷集錦

1. **忘記 `unsubscribe`**：記憶體洩漏 + 持續計費 + 對已卸載畫面呼叫 callback 的錯誤。監聽器一定要在不需要時關掉，React/Vue 用生命週期 cleanup。
2. **在 `onSnapshot` callback 裡又觸發寫入、造成無限迴圈**：callback 收到變動 → 你寫入 → 又觸發 callback → 又寫入…。callback 裡要寫入時，務必有條件擋住循環。
3. **以為第一次要自己先 `getDocs` 再監聽**：不用，`onSnapshot` 一訂閱就立刻觸發一次給你初始資料（實測 fire #1）。多此一舉還多算一次讀取。
4. **每次資料變都重建監聽**：例如在每次 render 都 `onSnapshot(...)`，會疊加一堆監聽器。同一份資料只訂閱一次，變動靠 callback 收。
5. **不處理錯誤**：`onSnapshot` 第三個參數是錯誤 callback。權限不足（安全規則擋）等錯誤會走那裡，不處理的話監聽會靜默失敗。`onSnapshot(ref, onNext, onError)`。
6. **把「即時」用在不需要的地方**：靜態的設定、一次性的詳情頁用 `onSnapshot`，白白掛連線、多計費。不需要即時就用 `getDocs`。

## 進階：再往深一層

- **樂觀更新與 `hasPendingWrites`**：因為 SDK 有本機快取，你 `addDoc` 後，**本機的 `onSnapshot` 會在資料真正送達伺服器之前就先觸發**（把你的寫入樂觀地反映出來），此時快照的 `metadata.hasPendingWrites` 為 `true`。若這筆寫入含 `serverTimestamp()`，伺服器確認時那個欄位會從 null 變成實際 Timestamp——這是一次**資料變動**，所以會**再觸發一次**、`hasPendingWrites` 變 `false`。但要注意：如果一筆寫入**沒有**任何伺服器端解析的值（純本機資料），「pending → 已確認」只是 metadata 變化，預設**不會**單獨再觸發（見下一條）。這讓 UI 反應「瞬間」（不用等網路往返），需要區分「本機暫存 vs 已落地」就看這個旗標。
- **`includeMetadataChanges`**：預設 `onSnapshot` 只在**資料**變時觸發，不會為「pending → 已確認」這種純 metadata 變化單獨觸發。要接收 metadata 變化（例如顯示「傳送中… → 已送達」）得傳 `{ includeMetadataChanges: true }`。
- **離線也能運作**：斷網時 `onSnapshot` 繼續從本機快取供應資料、你的寫入排隊；恢復連線後自動同步、監聽器補上這段期間的所有變動。行動 App（iOS/Android SDK 預設開啟持久化）幾乎免費得到這個離線體驗；**Web SDK 的跨工作階段離線持久化預設是關的**，要自己啟用（`persistentLocalCache` / IndexedDB 持久化）——單一工作階段內的延遲補償（上面那條）則是免費的。
- **監聽的是「查詢」不只是「集合」**：你可以 `onSnapshot(query(collection..., where(...), orderBy(...)))`——監聽一個**帶條件的查詢**，只有符合條件的變動才推給你。例如只監聽「我的、未讀的」通知。這讓即時同步很精準、不會被無關變動吵到（也更省，因為只推符合的）。
- **連線數與擴展**：每個 `onSnapshot` 目標會佔用連線資源。Firestore 對單一客戶端的同時監聽數、單一文件的監聽者數都有很高但存在的上限。一般 App 碰不到，但設計「所有人都監聽同一個超熱門文件」時要留意（可用分片等技巧）。

## 本章重點整理

- **`onSnapshot`** 訂閱資料：一註冊**立刻觸發一次**給當下值，之後**每次變動自動再觸發**——畫面永遠最新，不用手動重讀。
- 底層是**掛一條長連線等後端推播**（push），**不是輪詢**；只推差異，即時又省流量。
- `docChanges()` 能拿到「這次具體哪幾筆、怎麼變」，適合做動畫/精準更新。
- **一定要 `unsubscribe`**：不然記憶體洩漏 + 持續計費；React/Vue 用生命週期 cleanup。
- 需要即時用 `onSnapshot`，只載一次用 `getDocs`——別無腦全用即時。

## 自我檢核

- [ ] 我能說出 `getDocs` 和 `onSnapshot` 的本質差別（pull vs push、拍照 vs 監視器）
- [ ] 我親手開兩個視窗見證了一邊寫、另一邊自動更新
- [ ] 我能解釋 `onSnapshot` 為什麼不是輪詢、底層大致怎麼運作
- [ ] 我知道 `onSnapshot` 一訂閱就會先觸發一次，不用自己先讀
- [ ] 我知道為什麼一定要 unsubscribe，以及不關的後果（記憶體 + 帳單）
- [ ] 我知道什麼時候該用 `onSnapshot`、什麼時候該用 `getDocs`

## 延伸閱讀

### 官方文件

- **[Get realtime updates with Cloud Firestore](https://firebase.google.com/docs/firestore/query-data/listen)**
  - **讀哪裡**：整篇，這就是本章的官方權威版，含 `onSnapshot`、`docChanges()`、錯誤處理、detach listener、metadata changes。
  - **能學到什麼**：每個功能的官方寫法，特別是 metadata changes 和錯誤處理的細節。
  - **前提**：本章讀完即可。

- **[Access data offline](https://firebase.google.com/docs/firestore/manage-data/enable-offline)**
  - **讀哪裡**：離線持久化與 `hasPendingWrites` 那幾段。
  - **能學到什麼**：本章進階提到的樂觀更新、離線行為的完整機制。

### 文章

- **[The Firebase Blog — Firestore 即時查詢底層（WebChannel/連線）相關文章](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "realtime" 或 "listener"，講即時查詢的底層連線與擴展考量。
  - **為什麼值得讀**：把本章「底層機制」從概念推到真實的連線與擴展細節。
  - **前提**：本章讀完即可。

- **[Fireship — Firestore Realtime 應用實作](https://www.youtube.com/c/Fireship/search?query=firestore+realtime)** — Jeff Delaney
  - **這支說什麼**：用 `onSnapshot` 做即時列表/聊天的實作，和本章的「兩個視窗」實驗同精神。
  - **為什麼值得看**：看即時同步在真實 App 結構裡怎麼組織。

你已經掌握了讀、寫、即時三大能力。但這些都建立在「資料結構設計得好」的前提上。下一章是 Firestore 最需要「換腦袋」的一章——NoSQL 資料建模：沒有 JOIN 的世界裡，你該怎麼組織資料？答案常常是「故意重複」，而這需要一整套新思維。

→ [Ch 12 — NoSQL 資料建模](./12-firestore-data-modeling.md)
