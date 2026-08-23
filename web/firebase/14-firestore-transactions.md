# Ch 14 — 交易與批次寫入

> **目標**：學會 Firestore 保證「多筆操作一致」的兩個工具——`writeBatch`（批次：多筆寫入一起成功或一起失敗）和 `runTransaction`（交易：先讀再依讀到的值寫，全程原子），搞懂它們的差別與各自的適用場景。這是 Part 3 的最後一塊，補上「資料一致性」。

> **環境**：Firebase JS SDK v12.18.0。交易與批次行為以 Firestore 模擬器實跑驗證。

## 為什麼需要這個？

到目前為止你的寫入都是「一次一筆」。但真實需求常常是「好幾筆要綁在一起」：

- **轉帳**：A 扣 100、B 加 100，**不能只做一半**（扣了沒加 = 憑空消失）。
- **發文同時計數**：新增一篇文 + 把作者的 `postCount` +1，兩件事要一起發生。
- **搶票/庫存**：先讀「還有沒有票」，有才「扣一張」——讀和寫之間不能被別人插隊。

如果用兩個獨立的 `await`，中間任何一步失敗（斷網、當掉），你就會得到**不一致**的資料：扣了款沒發貨、發了文計數沒加。這章的兩個工具就是來保證「要嘛全做、要嘛全不做」。

## 先建立直覺：批次是「打包寄出」，交易是「邊讀邊改的保險箱」

```
   writeBatch（批次）                 runTransaction（交易）
   「把多筆寫入打包，一起送」          「先讀、依讀到的值算、再寫，全程鎖住」
                                     
   ✍️ 寫A ┐                          👀 讀X → 算出新值 → ✍️ 寫X
   ✍️ 寫B ├─ 打包 → 一起成功/失敗      期間若X被別人改了，
   ✍️ 寫C ┘                          整個交易自動重跑，確保沒算錯
   
   純寫入、不需要先讀                 需要「根據現在的值決定要寫什麼」
```

**一句話區分**：只是「多筆寫入要一起成敗」→ **批次**。「要先讀到目前的值、再根據它決定寫什麼」→ **交易**。

## writeBatch：多筆寫入，一起成敗

批次把多個 `set`/`update`/`delete` 打包，`commit()` 時**原子地**一起執行——全部成功，或全部不發生（不會只做一半）。

```js
import { getFirestore, writeBatch, doc } from "firebase/firestore";
const db = getFirestore(app);

const batch = writeBatch(db);

// 排入多筆操作（此時還沒真的寫）
batch.set(doc(db, "b", "x"), { v: 1 });
batch.set(doc(db, "b", "y"), { v: 2 });
batch.delete(doc(db, "posts", "fixed-id"));

// 一次送出，原子執行
await batch.commit();
```

實測：`commit()` 後，兩筆 set 都寫入、delete 的文件確實消失（`exists()` 為 `false`）：

```
[batch] committed; fixed-id gone: true
```

重點：

- 批次裡可以混用 `set`、`update`、`delete`（**但不能 `get`——批次只寫不讀**）。
- 排入操作時**還沒寫**，`commit()` 才一次送出。
- **原子性**：commit 成功 = 全部生效；commit 失敗 = 全部沒發生。不會出現「x 寫了但 y 沒寫」。
- 單一批次最多 **500 個操作**。

**典型場景**：發一篇文（`set` 新文件）+ 更新作者計數（`update`）+ 寫一筆通知（`set`），三件事用一個 batch 綁在一起，保證一致。

## runTransaction：先讀、再依讀到的值寫

當你要寫的值**取決於現在的值**（讀了才知道要寫什麼），批次不夠——因為批次不能讀。這時用交易。

```js
import { runTransaction, doc } from "firebase/firestore";

await runTransaction(db, async (tx) => {
  // 1. 在交易裡讀（用 tx.get，不是一般的 getDoc）
  const snap = await tx.get(doc(db, "counters", "c1"));
  const current = snap.data().n;

  // 2. 依讀到的值算出新值
  const next = current + 1;

  // 3. 在交易裡寫（用 tx.update/tx.set/tx.delete）
  tx.update(doc(db, "counters", "c1"), { n: next });
});
```

實測：`counters/c1` 原本 `n: 100`，交易後：

```
[transaction] n: 101
```

**交易的魔法在「並發安全」**：如果在你「讀 100、還沒寫回 101」的這段時間，**別人也改了** `c1`，Firestore 會偵測到「你讀的值已經過期」，**自動把整個交易函式重跑一次**（重新讀到最新值、重新計算）。所以就算 100 個人同時 +1，最終結果一定是 +100，一次都不會漏。

> **為什麼不用 `increment` 就好？** 對「單純 +1」用 `increment`（Ch 9）確實更簡單、也原子。但交易的威力在於**更複雜的「讀了才能決定」邏輯**——例如「如果庫存 > 0 才扣 1，否則拒絕」「把兩個帳戶的餘額對調」——這些不是單純加減，需要讀到值、跑判斷邏輯、再寫，`increment` 做不到，非交易不可。

### 交易的鐵律

1. **所有讀（`tx.get`）必須在所有寫之前**：交易函式裡要先做完所有讀取，才能開始寫。順序錯會報錯。
2. **交易函式可能被重跑多次**：因為並發衝突會觸發重試。所以**函式裡不要放有副作用的東西**（別在裡面送通知、改畫面、改外部變數）——它可能執行好幾遍。只做「讀 Firestore → 算 → 寫 Firestore」。
3. **用 `tx.get`/`tx.set`/`tx.update`/`tx.delete`**，不是一般的 `getDoc`/`setDoc`。交易內外的 API 不同。

## 轉帳範例：交易的經典用途

```js
async function transfer(fromId, toId, amount) {
  await runTransaction(db, async (tx) => {
    const fromRef = doc(db, "accounts", fromId);
    const toRef = doc(db, "accounts", toId);

    // 先讀（都在寫之前）
    const fromSnap = await tx.get(fromRef);
    const toSnap = await tx.get(toRef);

    const fromBalance = fromSnap.data().balance;
    if (fromBalance < amount) {
      throw new Error("餘額不足");   // throw 會讓整個交易取消，什麼都不寫
    }

    // 再寫（依讀到的餘額計算）
    tx.update(fromRef, { balance: fromBalance - amount });
    tx.update(toRef, { balance: toSnap.data().balance + amount });
  });
}
```

這保證：扣款和加款**一起**發生（原子）；餘額不足時**兩邊都不動**（throw 取消整個交易）；且就算同時有多筆轉帳操作同一帳戶，也不會算錯（衝突自動重試）。**在 `runTransaction` 裡 `throw`，整個交易回滾，一筆都不寫**——這是拒絕操作的乾淨做法。

## 對比與取捨

| | `writeBatch`（批次） | `runTransaction`（交易） |
|---|---|---|
| 能讀嗎 | 不能，只寫 | 能，且必須先讀後寫 |
| 用途 | 多筆寫入一起成敗 | 依現值決定要寫什麼 |
| 會重試嗎 | 不會 | 會（並發衝突自動重跑） |
| 副作用安全 | 是 | **否**（函式可能跑多次） |
| 上限 | 500 操作/批次 | 有讀寫數量與時間限制 |
| 例子 | 發文+計數+通知一起寫 | 轉帳、搶庫存、對調值 |

**選擇法則**：不用讀、只是多筆寫要綁一起 → **批次**（簡單、不重試）。要「讀到現值再決定寫什麼」→ **交易**（並發安全但函式要純）。單純計數 +1/陣列增減 → 直接 `increment`/`arrayUnion`（Ch 9，最簡單）。

## 踩雷集錦

1. **用兩個獨立 `await` 假裝原子**：`await updateDoc(A); await updateDoc(B);` 中間任何一步失敗就不一致。要一起成敗必須用 batch 或 transaction，不能靠兩個獨立寫入。
2. **在交易函式裡放副作用**：交易可能重跑多次。若你在裡面 `sendEmail()`、改全域變數、更新畫面，會執行好幾遍。交易函式**只**做讀 Firestore → 算 → 寫 Firestore，副作用放交易成功之後。
3. **交易裡先寫後讀**：交易要求所有讀在所有寫之前。先 `tx.update` 再 `tx.get` 會報錯。先讀完、再寫。
4. **交易內用了 `getDoc` 而非 `tx.get`**：一般的 `getDoc` 不受交易保護、拿到的值不參與衝突偵測，等於破壞了交易的意義。交易內一律用 `tx.` 系列。
5. **批次想在裡面讀**：batch 只能寫。需要讀就用 transaction。
6. **超過 500 操作的批次**：單批上限 500，超過要拆成多批（但拆開後就失去「全部一起原子」的保證，大量操作要另想策略，如用 Function 分段）。
7. **對單純計數硬用交易**：`+1` 這種用 `increment` 就好，又簡單又原子。交易留給「讀了才能決定」的複雜邏輯，別殺雞用牛刀。

## 進階：再往深一層

- **交易的並發控制（樂觀鎖）**：Firestore 交易用「樂觀並發」——它不預先鎖資料，而是記下你讀到的版本，commit 時檢查「這些資料在我讀之後有沒有被改」，有就重試。這在低衝突時效率很高（大多數交易一次過），高衝突時（大家搶同一筆）會反覆重試甚至失敗。搶購熱點要配合分片（Ch 12 進階）降低衝突。
- **交易的讀取也要遵守查詢限制**：交易裡可以 `tx.get` 文件，較新版本也支援在交易內跑查詢，但一樣受索引/查詢規則約束。
- **批次 vs 多次寫的計費**：批次寫 N 筆算 **N 次寫入**計費，不是一次——它省的是「往返次數」和「原子性」，不是錢。交易的讀寫也各自照常計費，重試會產生額外的讀。
- **和安全規則的互動**：batch/transaction 裡的每一筆寫入**都要各自通過安全規則**（Part 4）。批次不是「整批一個權限」，是「每筆都檢查」，任何一筆被規則擋，整批失敗。設計規則時要考慮這點。
- **冪等性（idempotency）**：因為交易可能重試、網路可能讓你不確定「到底成功沒」，重要操作最好設計成「重複執行也不會出錯」（例如用固定 ID `setDoc` 而非 `addDoc`，重跑不會產生重複文件）。這是分散式寫入的通用心法。

## 本章重點整理

- 要「多筆操作一起成敗」，不能用多個獨立 `await`——要用 **`writeBatch`** 或 **`runTransaction`**。
- **`writeBatch`**：打包多筆**寫入**，原子一起成敗，**不能讀**，上限 500 操作。用於「發文+計數+通知」這種純多寫。
- **`runTransaction`**：**先讀後寫**，依讀到的值決定寫什麼，並發衝突**自動重試**——用於轉帳、搶庫存這種「讀了才能決定」的邏輯。
- 交易函式**可能重跑多次**，裡面**不能有副作用**；讀用 `tx.get`、寫用 `tx.set/update/delete`；`throw` 會回滾整個交易。
- 單純計數/陣列增減用 `increment`/`arrayUnion` 最簡單；別對簡單操作硬套交易。

## 自我檢核

- [ ] 我能說出「用兩個獨立 await 寫兩筆」為什麼危險
- [ ] 我能判斷一個需求該用批次、交易、還是 `increment`
- [ ] 我知道交易為什麼要「先讀後寫」、為什麼函式裡不能有副作用
- [ ] 我能寫出一個「餘額不足就拒絕」的交易（用 throw 回滾）
- [ ] 我知道 batch/transaction 的每筆寫入都要各自通過安全規則
- [ ] 我理解交易的樂觀鎖機制，以及高衝突時的問題

## 延伸閱讀

### 官方文件

- **[Transactions and batched writes](https://firebase.google.com/docs/firestore/manage-data/transactions)**
  - **讀哪裡**：整篇，這就是本章的官方權威版，含 batch、transaction、重試機制、限制。
  - **能學到什麼**：每個 API 的完整用法、交易失敗與重試的官方說明、以及交易內查詢的支援。
  - **前提**：本章讀完即可。

- **[Distributed counters](https://firebase.google.com/docs/firestore/solutions/counters)**
  - **讀哪裡**：整篇。
  - **能學到什麼**：本章進階提到的「高衝突熱點」解法，交易反覆失敗時的分片策略。

### 文章

- **[The Firebase Blog — 交易與並發相關文章](https://firebase.blog/)** — Firebase 團隊
  - **這篇說什麼**：在部落格搜 "transaction"，講樂觀並發與實務案例。
  - **為什麼值得讀**：把本章「樂觀鎖、自動重試」的機制講到更深，理解為什麼交易函式要純。

Part 3 完成——你已經掌握 Firestore 的讀、寫、即時、建模、索引、一致性全套。是時候把 Auth 和 Firestore 整合成一個真東西了。下一個練習 B：做一個**即時留言板**，登入的使用者能發言、所有人即時看到。

→ [練習 B：即時留言板](./practice-b-realtime-board.md)
