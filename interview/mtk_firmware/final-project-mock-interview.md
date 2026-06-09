# Final Project — 完整模擬面試

> **目標**：把整門課整合成一場**計時、完整、擬真**的 MTK 韌體工程師面試自我演練——線上 C 上機考 → 技術面（C/嵌入式/OS/計組/DS 口頭+白板）→ 主管面（行為）。這是考前最後的實戰驗證：能撐過這場，你就準備好了。

## 為什麼做這個

讀過、做過練習，不等於「面試當下答得出來」。面試的壓力在於**計時 + 沒有提示 + 要說出口**。這個 final 模擬整個流程，逼你在限時下調用全課知識（涵蓋 Part 1–6 的核心）。比任何單章複習都更接近真實。

**怎麼用**：找一個不被打擾的 3 小時。嚴格計時。手寫題用紙或裸 editor（別開 IDE 自動補全）。口頭題**大聲說出來**（或錄音）——「想得出」和「說得清楚」是兩回事。全部做完再對照講評。

---

## 關卡一：線上 C 上機考（60 分鐘）

模擬 MTK 第一關線上測驗。限時 60 分鐘，用 C，可用 gcc 編譯但**不准查網路**。

### 程式題（手寫 + 編譯通過）

**P1（Ch 7）** 寫一個函式 `int count_ones(unsigned int x)` 回傳 x 的二進位有幾個 1。再寫一個 O(設定位元數) 的版本。

**P2（Ch 12）** 不用任何標準庫函式，實作 `void *my_memmove(void *dst, const void *src, size_t n)`，正確處理 dst/src 重疊。

**P3（Ch 36）** 給 singly linked list，寫 `Node *reverse(Node*)` 反轉它，並寫 `Node *find_middle(Node*)` 用快慢指標找中點。

**P4（Ch 41）** 寫 `int binary_search(int a[], int n, int target)`，正確處理所有邊界。

**P5（Ch 40）** 手寫 quicksort，並在註解說明最壞情況與如何避免。

### 觀念選擇/簡答（快答）

**P6（Ch 9）** `printf("%d\n", -20 + 6u);` 在 32-bit int 印什麼？為什麼？

**P7（Ch 3）** 下面程式為什麼可能印不出預期結果？怎麼修？
```c
int flag = 0;
while (flag == 0) { /* 等中斷把 flag 設 1 */ }
```

**P8（Ch 8）** 這個 struct 在 64-bit、預設對齊下 sizeof 是多少？
```c
struct S { char a; int b; char c; };
```

<details>
<summary>關卡一參考解答與講評</summary>

**P1**：
```c
int count_ones(unsigned int x){ int c=0; while(x){ c+=x&1; x>>=1; } return c; }
// O(設定位元數)版（Brian Kernighan）：
int count_ones_fast(unsigned int x){ int c=0; while(x){ x&=(x-1); c++; } return c; }
```
`x&(x-1)` 清掉最低位的 1，迴圈次數=1 的個數。Ch 7。

**P2**：
```c
void *my_memmove(void *dst, const void *src, size_t n){
    char *d=dst; const char *s=src;
    if(d<s) while(n--) *d++=*s++;           // 不重疊或 dst 在前：正向
    else { d+=n; s+=n; while(n--) *--d=*--s; } // dst 在後：反向（防覆蓋）
    return dst;
}
```
關鍵：重疊時要選對方向。memcpy 不處理重疊、memmove 處理。Ch 12。

**P3**：reverse 見 Ch 36（三指標）；find_middle 快慢指標 `while(fast&&fast->next)`。

**P4**：見 Ch 41，三陷阱 `<=`、`lo+(hi-lo)/2`、±1。

**P5**：見 Ch 40，最壞 O(n²)（pivot 選極值），隨機/三數取中避免。

**P6**：印一個大正數（不是 -14）。`-20` 被轉成 unsigned（`-20` 的補數位元當無號解讀），`+6` 後仍是大正數。有號遇無號→轉無號。Ch 9。

**P7**：編譯器可能把 `flag` 快取在暫存器，看不到中斷對記憶體的修改 → 無限迴圈。修：`volatile int flag = 0;`。Ch 3/14。

**P8**：12 bytes。`char a`(1) + 3 padding + `int b`(4) + `char c`(1) + 3 尾端 padding = 12（對齊到 4 的倍數）。Ch 8。

**自評**：程式題全部 gcc 編譯通過且跑對測資才算過。P2 重疊方向、P7 volatile 是最常掛的點。
</details>

---

## 關卡二：技術面（70 分鐘，口頭 + 白板）

模擬資深工程師技術面。**大聲回答**，白板題寫在紙上。每題先自己答，再看講評。

### C 與嵌入式（Ch 1–19）

**T1** volatile 的三個典型使用場景？舉一個「不加 volatile 會出 bug」的具體例子。

**T2** 你在韌體裡寫一個 ISR，要注意哪些事？為什麼 ISR 裡不該 printf 或 malloc？

**T3** reentrant 和 thread-safe 差在哪？為什麼一個用 mutex 做到 thread-safe 的函式，放進 ISR 反而危險？

**T4** 怎麼讀寫一個位於 0x40021000 的硬體暫存器？寫出 C 程式並解釋每個 keyword。

**T5** 解釋 priority inversion，以及 priority inheritance 怎麼解決它。（提示：火星探路者）

### OS（Ch 20–28）

**T6** process 和 thread 差在哪？為什麼 thread 的 context switch 比 process 便宜？

**T7** deadlock 的四個必要條件？破壞哪一個最常用、怎麼破壞？

**T8** 三個 process，burst time 5/3/8，同時到達。用 SJF 算平均等待時間。

### 計組與 DS（Ch 29–41）

**T9** cache 32KB、4-way set associative、line 64 bytes、32-bit 位址。算 tag/index/offset 各幾 bits。

**T10** quicksort 和 merge sort 怎麼選？為什麼實務 array 排序常用 quicksort？

**T11** 白板：給一個 BST，寫中序走訪，並說明為什麼輸出是排序的。

<details>
<summary>關卡二參考解答與講評</summary>

**T1**：MMIO、ISR 共享變數、多執行緒/迴圈等待的旗標。例子：關卡一 P7 的迴圈等旗標。Ch 3。

**T2**：void 無參數無回傳、快進快出、不阻塞、共享變數加 volatile、保護與主程式的共享資料。不 printf（慢、可能不可重入、可能阻塞）、不 malloc（不可重入、不可預測、可能死鎖）。Ch 14/15。

**T3**：reentrant=可被中斷後再次進入仍正確（不靠全域/靜態狀態）；thread-safe=多執行緒同時呼叫正確（常靠鎖）。用 mutex 的 thread-safe 函式在 ISR 危險：若主程式持鎖時被中斷、ISR 又要同鎖→死鎖（ISR 不能睡眠等鎖）。Ch 15。

**T4**：
```c
#define REG (*(volatile unsigned int *)0x40021000)
REG = 0x1;              // 寫
unsigned int v = REG;   // 讀
```
`volatile` 防優化、`unsigned int` 定寬度、cast 把位址當指標再解參考。Ch 13。

**T5**：低優先任務持有高優先任務需要的鎖，中優先任務又搶走 CPU，導致高優先被無限延遲。priority inheritance：持鎖的低優先任務「暫時繼承」等待者的高優先級，盡快做完釋放鎖。Ch 18。

**T6**：process 獨立位址空間（資源單位）、thread 共享所屬 process 的位址空間（執行單位）。thread 切換便宜：同 process 不用換頁表、不用刷 TLB。Ch 20。

**T7**：互斥、持有並等待、不可剝奪、循環等待。最常破壞「循環等待」——規定鎖的全域取得順序（lock ordering）。Ch 23。

**T8**：SJF 按 burst 短到長：3→5→8。等待：P(3)=0、P(5)=3、P(8)=8 → 平均 (0+3+8)/3 = 3.67。Ch 21。

**T9**：offset：line 64B=2^6→6 bits。總行數=32KB/64B=512；set 數=512/4=128=2^7→index 7 bits。tag=32-7-6=19 bits。Ch 30。

**T10**：quicksort 平均 O(n log n)、原地、cache 友善、常數小，array 首選；最壞 O(n²)、不穩定。merge 保證 O(n log n)、穩定，但 O(n) 空間，適合 linked list/外部排序/要穩定時。實務 array 用 quicksort 變體（introsort：退化轉 heapsort 保證）。Ch 40。

**T11**：中序（左根右）見 Ch 38；BST 左<根<右，先遞迴完左子（較小的全印完）、再印根、再右子（較大的）→ 遞增。

**自評**：T3、T5、T9 是高頻深挖題。T8/T9 計算要算對。答不出的回對應章。
</details>

---

## 關卡三：主管面（行為，30 分鐘）

模擬主管面。對著鏡子或錄音，**完整講出來**，計時。

**B1** 用 90 秒做自我介紹（中文）。再用英文做一次。

**B2** 講一個你做過最有挑戰的專案：你負責什麼、遇到什麼技術困難、怎麼 debug 解決的、學到什麼。（用 STAR）

**B3** 講一次你犯的技術錯誤，以及你怎麼處理、之後怎麼避免。

**B4** 為什麼想做韌體？為什麼選 MTK？

**B5** 你對這個職位/team 有什麼想問的？（列出 3 個問題）

<details>
<summary>關卡三自評標準</summary>

不是背標準答案，是檢查你的回答有沒有達到這些標準：

- **B1**：90 秒內、有結構（我是誰→最相關經歷→為何適合+為何 MTK），不流水帳。英文版能流暢講完（不用完美口音）。Ch 42。
- **B2**：用 STAR、講「我」不是「我們」、debug 過程具體（用了什麼工具/怎麼縮小範圍，連結 Ch 19）、有量化結果、有反思。
- **B3**：是真的錯誤（不是假謙虛）、重點在學到什麼與如何避免、展現成長心態。
- **B4**：對韌體有具體理由（喜歡底層/硬體軟體交界/...）、對 MTK 有做功課（知道 Dimensity/Wi-Fi/IoT/車用、為什麼吸引你），不講「大公司穩定」。
- **B5**：3 個有思考的問題（team 平台/RTOS/開發流程/新人任務/技術挑戰），不問薪水加班、不問可 Google 的。

**最重要的檢查**：B2 講的專案，如果主管針對任何技術細節追問，你都答得出來嗎？履歷上的東西扛得住問，是行為面試的底線。Ch 42。
</details>

---

## 總評分表

做完三關，誠實打分（每項 0–2 分：0 不行、1 普通、2 穩）：

| 關卡 | 項目 | 分數 |
|---|---|---|
| 一 | C 上機程式題全編譯通過跑對 | |
| 一 | C 觀念簡答（轉換/volatile/對齊）正確 | |
| 二 | C/嵌入式深挖（volatile/ISR/reentrant/MMIO/優先反轉）| |
| 二 | OS（process/deadlock/排程計算）| |
| 二 | 計組/DS（cache 計算/排序/走訪）| |
| 三 | 自我介紹（中+英）有結構流暢 | |
| 三 | 專案 STAR + 扛得住深挖 | |
| 三 | 行為題（失敗/動機）有料不空話 | |
| 三 | 反問問題有準備 | |

- **14–18 分**：準備充分，可以上場。把錯的少數點補一下。
- **9–13 分**：及格但有破口。針對 0–1 分的項目回對應 Part 加強，重做一次。
- **8 分以下**：基礎還不穩，別急著面試。回頭按一週計畫（Ch 43）系統補，C 和嵌入式優先。

## 做完之後

1. **把所有答錯/卡住的題，回到對應章節**重讀那一節（不是整章，是那個點）。
2. **手寫題沒一次過的**，反覆寫到肌肉記憶（關卡一 P1–P5）。
3. **計算題算錯的**（cache/排程），多找幾題練（練習 C/D + 延伸閱讀的考古題）。
4. **行為題講得卡的**，寫成草稿、練到順（別背稿，練到自然）。
5. 隔一兩天**再做一次這個 final**，看分數有沒有上去。

這門課到這裡結束。你已經把 MTK 韌體面試的五大領域（C / 嵌入式 / OS / 計組 / DS）+ 行為面試走過一遍，並用上機/技術/主管三關自測過。剩下的是**反覆**——手寫題練到不用想、計算題練到不會錯、結論練到能展開解釋。

祝面試順利。把底層功夫做扎實，這不只是為了一個 offer，是韌體工程師一輩子的本錢。

## 自我檢核

- [ ] 我在限時內完成了三關，沒有中途放棄或偷看
- [ ] 關卡一的程式題全部 gcc 編譯通過且跑對測資
- [ ] 關卡二的口頭題我能「說出來」而不只是「想得到」
- [ ] 關卡三我履歷上的專案扛得住主管深挖
- [ ] 我知道自己最弱的是哪個 Part，並有具體補強計畫
- [ ] 我的總分達到可上場標準（14+），或我知道還要補什麼
