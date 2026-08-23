# Ch 19 — Cloud Storage 檔案儲存

> **目標**：學會用 Cloud Storage 上傳/下載檔案（圖片、影片等），理解「檔案放 Storage、路徑存 Firestore」的分工，並為 Storage 寫安全規則（限制誰能傳、傳多大、傳什麼）。這是 Ch 2 講的「大檔進 Storage」的實作。

> **環境**：Firebase JS SDK v12.18.0。本章所有 Storage 操作（上傳、metadata、下載網址）都用 Storage 模擬器實跑驗證。

## 為什麼需要這個？

到現在你的資料都是文字（存 Firestore）。但真實 App 要處理**檔案**——使用者頭像、貼文照片、上傳的 PDF。這些**不能**塞進 Firestore（單文件 1 MiB 上限，又貴又慢，Ch 2/8）。Cloud Storage 就是專門放這些「大而非結構化」檔案的地方。這章教你怎麼上傳、拿到能顯示的網址、以及怎麼保護（不然有人能上傳一堆垃圾塞爆你的空間、燒你的帳單）。

## 先建立直覺：Storage 是雲端硬碟，Firestore 存「檔案在哪」

```
   使用者上傳一張頭像
        │
        ├─ 圖片檔本身（幾百 KB～幾 MB）
        │     └──▶  Cloud Storage: avatars/uid_alice.jpg   （雲端硬碟）
        │
        └─ 拿到一個「下載網址」
              └──▶  Firestore: users/uid_alice { avatarUrl: "https://..." }
                    （資料庫只存那個網址字串）
```

**分工鐵律**（Ch 2 的具體化）：**檔案本體放 Storage，Firestore 只存「檔案的下載網址或路徑」。** 顯示時，從 Firestore 讀到 `avatarUrl`，丟給 `<img src>` 就顯示了。這個分工讓資料庫保持輕巧、檔案交給專門的儲存服務。

Storage 的路徑也像檔案系統——`avatars/uid_alice.jpg`、`posts/post_1/photo.png`。你用這些路徑定位檔案。

## Step 0：Console 啟用 Storage

> Console 操作。**Build → Storage → 開始使用**，選規則模式（先 Test/開放，之後改）和地區。啟用後你會得到一個**儲存桶（bucket）**，名字像 `你的專案.firebasestorage.app`（2024 年 10 月後的新專案）或 `你的專案.appspot.com`（較舊專案）——這會出現在你的 config `storageBucket` 欄位，以 Console 顯示的為準。

## 上傳檔案：uploadBytes

```js
import { getStorage, ref, uploadBytes } from "firebase/storage";
const storage = getStorage(app);

// ref 指向「這個檔案要放的路徑」
const fileRef = ref(storage, "public/note.txt");

// 上傳（file 可以是 File 物件、Blob、Uint8Array）
const data = new TextEncoder().encode("hello storage file contents");
const result = await uploadBytes(fileRef, data, { contentType: "text/plain" });

console.log("路徑:", result.metadata.fullPath);
console.log("大小:", result.metadata.size);
```

實測輸出（Storage 模擬器）：

```
[upload] fullPath: public/note.txt | size: 27
```

要點：

- `ref(storage, "路徑")` 指向檔案位置（不存在就是要新建的位置）。
- `uploadBytes(ref, data, metadata)` 上傳，第三個參數可設 `contentType`（MIME 型別，影響瀏覽器怎麼處理）。
- 回傳的 `result.metadata` 有 `fullPath`、`size`、`contentType` 等資訊。

### 從網頁表單上傳使用者選的檔案

實務上檔案來自 `<input type="file">`：

```html
<input type="file" id="fileInput" accept="image/*">
```

```js
document.getElementById("fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];   // 使用者選的 File 物件
  if (!file) return;
  const uid = auth.currentUser.uid;
  const fileRef = ref(storage, `avatars/${uid}/${file.name}`);
  await uploadBytes(fileRef, file);   // File 直接傳，contentType 自動偵測
  console.log("上傳完成");
});
```

用 `uid` 分資料夾（`avatars/${uid}/...`）是常見做法——之後安全規則就能限制「每個使用者只能寫自己 uid 的資料夾」。

> **大檔要用 `uploadBytesResumable`**：`uploadBytes` 一次上傳完，適合小檔。大檔（影片）用 `uploadBytesResumable`，它支援**進度回報**（顯示上傳百分比）和**斷點續傳**（網路斷了能接續）。API 類似，多一個進度監聽。

## 拿到能顯示的網址：getDownloadURL

上傳完，要顯示圖片得有一個公開可存取的網址：

```js
import { getDownloadURL, ref } from "firebase/storage";

const url = await getDownloadURL(ref(storage, "public/note.txt"));
console.log(url);
// 把這個 url 存進 Firestore，或直接 <img src={url}>
```

實測：

```
[downloadURL] contains token: true | host: 127.0.0.1:9199
```

那個網址帶一個 **token**（`?alt=media&token=...`）——這是一個「知道網址就能存取」的憑證。要點：

- 這個下載網址**不會過期**（除非你重新產生 token 或刪檔），適合存進 Firestore 給 `<img>` 用。
- **知道網址的人就能存取那個檔案**——所以下載網址本身是一種「公開連結」。真正的存取控制在**上傳時**由安全規則決定「誰能傳到哪」，以及讀取規則決定「誰能拿到 URL」。

### 完整流程：上傳頭像並存進 Firestore

把 Storage 和 Firestore 串起來（Ch 2 那條協作鏈的實作）：

```js
async function uploadAvatar(file) {
  const uid = auth.currentUser.uid;
  // 1. 上傳到 Storage
  const fileRef = ref(storage, `avatars/${uid}`);
  await uploadBytes(fileRef, file);
  // 2. 拿下載網址
  const url = await getDownloadURL(fileRef);
  // 3. 把網址存進 Firestore 的使用者文件
  await setDoc(doc(db, "users", uid), { avatarUrl: url }, { merge: true });
  // 4. 因為別處在監聽這份文件，頭像即時更新（Ch 11）
}
```

## Storage 安全規則

Storage 有**自己的**安全規則（和 Firestore 分開，但概念一樣，Ch 15–18 全部適用）。注意 `service` 是 `firebase.storage`（不是 `cloud.firestore`）：

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /public/{fileName} {
      allow read: if true;                          // 誰都能讀 public 下的檔
      allow write: if request.resource.size < 5 * 1024 * 1024;   // 限 5 MB 以下
    }
  }
}
```

實測：這條規則下，上傳一個小檔（27 bytes）成功。Storage 規則能檢查**檔案特有的屬性**：

| 檢查 | 寫法 |
|---|---|
| 檔案大小 | `request.resource.size < 5 * 1024 * 1024`（5 MB） |
| 檔案型別（MIME） | `request.resource.contentType.matches('image/.*')`（只准圖片） |
| 誰能寫哪個資料夾 | `match /avatars/{userId}/{f} { allow write: if request.auth.uid == userId; }` |
| 登入才能傳 | `allow write: if request.auth != null` |

一個真實的頭像規則——「每個人只能傳自己 uid 資料夾、只能傳 5 MB 以下的圖片、誰都能看」：

```
match /avatars/{userId}/{fileName} {
  allow read: if true;
  allow write: if request.auth != null
    && request.auth.uid == userId
    && request.resource.size < 5 * 1024 * 1024
    && request.resource.contentType.matches('image/.*');
}
```

> **為什麼 Storage 規則的大小/型別限制特別重要？** 因為 Storage 按**儲存量和流量**計費（Ch 22）。沒有大小限制 = 有人能上傳 GB 級檔案塞爆你的空間、燒你的帳單。沒有型別限制 = 有人能把你的 Storage 當免費檔案主機傳任何東西（包括惡意檔）。**Storage 規則的大小和型別限制，是防濫用和防帳單爆炸的第一道關。** 這是 Storage 規則比 Firestore 規則多出來、且絕不能省的一環。

## 刪除與列出檔案

```js
import { deleteObject, listAll, ref } from "firebase/storage";

// 刪除
await deleteObject(ref(storage, `avatars/${uid}`));

// 列出某資料夾下的檔案
const res = await listAll(ref(storage, `avatars/${uid}`));
res.items.forEach(itemRef => console.log(itemRef.fullPath));
```

## 踩雷集錦

1. **把圖片塞進 Firestore**（base64 字串）：又貴又撞 1 MiB 上限又慢。檔案一律進 Storage，Firestore 只存下載網址。這是 Firebase 新手的經典錯誤。
2. **Storage 規則忘了限制大小/型別**：等於開放任何人上傳任意大小任意類型的檔案，帳單和儲存都會失控。大小 + 型別限制是 Storage 規則的必備項，不是選配。
3. **以為下載網址是私密的**：`getDownloadURL` 的網址帶 token，知道就能存取、且不過期。它是「公開連結」等級。敏感檔案不要用這種公開 URL，改用「每次存取時透過規則驗證」的方式（或後端簽發短期 URL）。
4. **Storage 規則和 Firestore 規則搞混**：兩者分開、各寫各的。`service firebase.storage` vs `service cloud.firestore`。保護了 Firestore 不代表保護了 Storage。
5. **忘了 Console 啟用 Storage**：和其他服務一樣要先在 Console 啟用（Ch 2），否則 `getStorage` 操作會失敗。
6. **上傳大檔用 `uploadBytes` 沒有進度**：使用者盯著一個沒反應的畫面等好幾秒。大檔用 `uploadBytesResumable` 顯示進度。

## 進階：再往深一層

- **上傳進度與續傳**：`uploadBytesResumable` 回傳一個 task，可 `task.on('state_changed', snapshot => { 進度 = snapshot.bytesTransferred / snapshot.totalBytes })` 做進度條，還能 `pause()`/`resume()`/`cancel()`。做「上傳大影片顯示百分比」必用。
- **圖片自動處理（Extensions）**：Firebase 有現成的 Extension「Resize Images」——上傳圖片後自動產生縮圖（thumbnail）。前端顯示列表用縮圖、點開看原圖，省流量。這比自己寫圖片處理省事（Ch 2 的 Extensions）。
- **自訂 metadata**：上傳時可帶自訂 metadata（`customMetadata: { uploadedBy: uid }`），規則裡能用 `request.resource.metadata.uploadedBy` 判斷。用於更細的存取控制。
- **CORS 設定**：如果你要從網頁**直接 fetch** Storage 的檔案（不是透過 `<img>`），可能撞 CORS。要用 `gsutil` 設定 bucket 的 CORS 政策。一般 `<img src>` 不會遇到，但 canvas 處理圖片、fetch 檔案內容時會。
- **Storage 也能用 rules-unit-testing 測**：Ch 18 的測試工具支援 Storage 規則（`initializeTestEnvironment` 傳 `storage: {rules}`）。你的大小/型別限制規則也該寫測試驗證。

## 本章重點整理

- **檔案本體放 Storage，Firestore 只存下載網址/路徑**——大檔絕不塞 Firestore。
- 上傳用 **`uploadBytes`**（小檔）/ **`uploadBytesResumable`**（大檔，有進度）；`ref(storage, "路徑")` 定位；用 `uid` 分資料夾方便寫規則。
- **`getDownloadURL`** 拿到帶 token 的公開網址，可存進 Firestore 給 `<img>` 用；但它是「知道就能存取」的公開連結。
- Storage 有**自己的**安全規則（`service firebase.storage`），能檢查**檔案大小和 MIME 型別**——這是防濫用/防帳單爆炸的必備關卡。
- 完整流程：上傳 → 拿 URL → 存進 Firestore → 即時顯示（串起 Storage + Firestore + 即時同步）。

## 自我檢核

- [ ] 我能解釋「檔案放 Storage、網址存 Firestore」的分工與原因
- [ ] 我能用 `uploadBytes` + `getDownloadURL` 完成「上傳並拿到可顯示的網址」
- [ ] 我知道 Storage 規則要限制大小和型別，以及不限制的後果
- [ ] 我知道下載網址是「公開連結」等級，敏感檔案不能這樣放
- [ ] 我知道 Storage 規則和 Firestore 規則是分開的兩套
- [ ] 我能串起「上傳頭像 → 存進 Firestore → 即時更新」的完整流程

## 延伸閱讀

### 官方文件

- **[Upload files with Cloud Storage on Web](https://firebase.google.com/docs/storage/web/upload-files)**
  - **讀哪裡**：`uploadBytes` 和 `uploadBytesResumable`（含進度監聽）兩節。
  - **能學到什麼**：本章上傳的官方完整版，特別是續傳與進度的細節。
  - **前提**：本章讀完即可。

- **[Download files / Use File URLs](https://firebase.google.com/docs/storage/web/download-files)**
  - **讀哪裡**：`getDownloadURL` 那段。
  - **能學到什麼**：下載網址的產生與存取控制說明。

- **[Cloud Storage Security Rules](https://firebase.google.com/docs/storage/security)**
  - **讀哪裡**：整篇，特別是「Data validation」——檔案大小、contentType 的限制寫法。
  - **能學到什麼**：本章 Storage 規則的官方權威版，防濫用的完整做法。

### 文章

- **[Fireship — Firebase Storage 上傳與圖片處理](https://www.youtube.com/c/Fireship/search?query=firebase+storage)** — Jeff Delaney
  - **這支說什麼**：從表單上傳到顯示、進度條的實作。
  - **為什麼值得看**：把本章的上傳流程放進真實 UI，含進度條。

檔案能上傳了。但你的 App 到現在都還只在你電腦本機跑——別人連不到。下一章教你把它**部署上線**：用一行指令把你的網頁推到全球 CDN，得到一個真實網址，任何人都能打開。

→ [Ch 20 — Firebase Hosting 部署上線](./20-hosting.md)
