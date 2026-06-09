# 練習 C — OS 綜合考古題

> **目標**：把 Part 3（Ch 20–28）的 OS 考點綜合驗收——process/thread、scheduling、同步、deadlock、memory/VM、IPC、syscall。先遮答案，用「面試官問你會怎麼答」的方式自測。

> **環境**：概念 + 計算。前置：Part 3 全部。建議當口頭面試模擬（說出答案，不只想）。

## 怎麼用這份練習

OS 題多是「解釋概念 + 比較 + 計算」。**用嘴巴答**（模擬面試口頭回答），不只在腦中想——能流暢說出來才是真懂。計算題（scheduling、page replacement）拿紙筆算。

---

## 第一部分：概念與比較（口頭答）

### Q1（Ch 20）process 和 thread 差在哪？什麼時候用哪個？

<details>
<summary>要點</summary>

process = 資源擁有單位（獨立位址空間）；thread = 執行單位（共享 process 記憶體）。thread 建立/切換便宜（不換位址空間/TLB）、溝通快（共享記憶體）但要同步、不安全（一崩全崩）；process 隔離安全但溝通要 IPC、切換貴。

用 thread：頻繁共享、輕量並行；用 process：要隔離/安全（如瀏覽器每分頁一 process）。Ch 20。
</details>

### Q2（Ch 22）mutex 和 semaphore 差在哪？binary semaphore 和 mutex 一樣嗎？

<details>
<summary>要點</summary>

mutex：鎖（有擁有權，誰 lock 誰 unlock），用於互斥。semaphore：計數器（無擁有權），binary（互斥/通知）/counting（管 N 資源）。

binary semaphore ≈ mutex 但**少了擁有權**——semaphore 可由不同執行緒 signal/wait（如 ISR signal、task wait），mutex 要同執行緒。所以跨執行緒通知用 semaphore。Ch 22。
</details>

### Q3（Ch 23）deadlock 的四個條件？怎麼預防？

<details>
<summary>要點</summary>

四條件（同時成立才 deadlock）：互斥、持有並等待、不可剝奪、循環等待。破壞任一個就預防。

最實用：破壞循環等待——**lock ordering（按固定順序拿鎖）**。其他：一次拿全部（破持有並等待）、拿不到就釋放（破不可剝奪）。Ch 23。
</details>

### Q4（Ch 25）什麼是 virtual memory？paging 怎麼運作？internal/external fragmentation 差在哪？

<details>
<summary>要點</summary>

virtual memory：每 process 獨立虛擬位址空間，OS+MMU 轉實體 → 隔離+抽象+超量。

paging：固定大小切（page↔frame），page table 對映；無 external fragmentation、有 internal。

internal = 塊內部用不滿（paging）；external = 塊之間零碎（segmentation）。Ch 25。
</details>

### Q5（Ch 28）user mode 和 kernel mode 差在哪？system call 是 context switch 嗎？

<details>
<summary>要點</summary>

user mode 受限（不能碰硬體/特權指令）、kernel mode 完整權限。分 mode 為保護。

syscall 是 **mode switch**（同 process，user↔kernel）**不是 context switch**（換 process）。很多人搞錯。Ch 28。
</details>

### Q6（Ch 20, 25）為什麼 process 的 context switch 比 thread 貴？

<details>
<summary>要點</summary>

process 切換要換位址空間（換 page table）+ flush TLB（之後 TLB miss 增加）；thread 同位址空間不用換 → 便宜。TLB flush 是關鍵成本。Ch 20/25。
</details>

---

## 第二部分：計算題（紙筆算）

### Q7（Ch 21）算 FCFS 和 SJF（non-preemptive）的平均 waiting time

```
process  Arrival  Burst
P1       0        6
P2       1        2
P3       2        4
```

<details>
<summary>解答</summary>

**FCFS**（按到達 P1→P2→P3）：
```
P1: 0-6  完成6   waiting = 6-0-6 = 0
P2: 6-8  完成8   waiting = 8-1-2 = 5
P3: 8-12 完成12  waiting = 12-2-4 = 6
平均 = (0+5+6)/3 = 3.67
```

**SJF**（non-preemptive，每次選 ready 中 burst 最短）：
```
t=0: 只有 P1 → P1 跑 0-6
t=6: P2(2), P3(4) 都到，選短的 P2 → 6-8
t=8: P3 → 8-12
waiting: P1: 0, P2: 8-1-2=5, P3: 12-2-4=6 → 平均 3.67
```

（這組剛好 P1 太早到先佔了，FCFS=SJF。若 P1 burst 大、後面小，SJF 會明顯較好。）

Waiting = Completion - Arrival - Burst。Ch 21。
</details>

### Q8（Ch 26）reference string `1 2 3 4 1 2 5 1 2 3 4 5`，3 frames，算 FIFO 和 LRU 的 page fault 數

<details>
<summary>解答</summary>

**FIFO**（換最早載入）：
```
1[1]F 2[12]F 3[123]F 4[234]F(換1) 1[341]F(換2) 2[412]F(換3)
5[125]F(換4) 1[125]hit 2[125]hit 3[253]F(換1) 4[534]F(換2) 5[534]hit
→ 9 faults
```

**LRU**（換最久沒用）：
```
1[1]F 2[12]F 3[123]F 4[234]F(換1) 1[341]F(換2) 2[412]F(換3)
5[125]F(換4) 1[251]hit 2[512]hit 3[123]F(換5) 4[234]F(換1) 5[345]F(換2)
→ 10 faults
```

有趣：這組 LRU(10) 比 FIFO(9) 還多——說明沒有絕對最好的（OPT 才保證最少）。Ch 26。
</details>

---

## 第三部分：情境/應用題

### Q9（Ch 22, 24）寫 producer-consumer 的 pseudo code，並說明 semaphore 順序為什麼重要

<details>
<summary>解答</summary>

```c
semaphore empty = N, full = 0;  mutex m;
// producer: produce; wait(empty); wait(m); add; signal(m); signal(full);
// consumer: wait(full); wait(m); remove; signal(m); signal(empty); consume;
```

順序：**先 wait(empty/full)（資源計數）再 wait(m)（互斥）**。反了會 deadlock——生產者拿了 m 卻發現 buffer 滿要等 empty，消費者要 m 才能取資料釋放 empty，互相等。Ch 24。
</details>

### Q10（Ch 23）兩個 thread 這樣拿鎖，會 deadlock 嗎？怎麼修？

```c
// Thread A: lock(m1); lock(m2); ... unlock(m2); unlock(m1);
// Thread B: lock(m2); lock(m1); ... unlock(m1); unlock(m2);
```

<details>
<summary>解答</summary>

**會 deadlock**。A 拿 m1 等 m2、B 拿 m2 等 m1 → 循環等待（Ch 23 四條件之一）。

修法：**lock ordering**——兩個 thread 都按同順序拿鎖（如都先 m1 再 m2）：
```c
// Thread B 改成: lock(m1); lock(m2); ...
```
這樣不可能形成環（沒有人先 m2 再 m1）。Ch 23 最實用的預防法。
</details>

### Q11（Ch 26, 25）page fault 和 segmentation fault 差在哪？

<details>
<summary>解答</summary>

- **page fault**：存取的 page 不在實體 RAM（在磁碟）→ OS **正常處理**（從磁碟載入），只是慢。不是錯誤。
- **segmentation fault**：程式存取**非法位址**（沒映射的、沒權限的，如解 NULL/野指標）→ **錯誤**，OS 送 SIGSEGV，程式 crash。

完全不同：page fault 是 OS 正常的 VM 機制；segfault 是程式 bug。名字像但意義天差地遠。Ch 25/26。
</details>

### Q12（Ch 20, 22）多 thread 同時 `count++` 為什麼結果錯？怎麼修？

<details>
<summary>解答</summary>

`count++` 是讀-改-寫三步（非原子）。兩 thread 同時：都讀到舊值、各+1寫回 → 一次更新丟失（race condition，Ch 22）。

修：用 mutex 保護（`lock; count++; unlock;`）或 atomic 操作。volatile **不能**解（只保證重讀不保證原子，Ch 3）。Ch 20/22。
</details>

## 自評與弱點

| 題 | 章 | 考點 |
|---|---|---|
| Q1 | Ch 20 | process vs thread |
| Q2 | Ch 22 | mutex vs semaphore |
| Q3 | Ch 23 | deadlock 四條件 + 預防 |
| Q4 | Ch 25 | VM/paging/碎片 |
| Q5 | Ch 28 | user/kernel mode、mode vs context switch |
| Q6 | Ch 20,25 | context switch 成本（TLB）|
| Q7 | Ch 21 | scheduling 計算 |
| Q8 | Ch 26 | page replacement 計算 |
| Q9 | Ch 24 | producer-consumer |
| Q10 | Ch 23 | deadlock + lock ordering |
| Q11 | Ch 25,26 | page fault vs segfault |
| Q12 | Ch 20,22 | race condition |

- **概念題（Q1-6）說不流暢** → 那章重看，OS 面試很多是口頭問答，要能講。
- **計算題（Q7-8）算錯** → 練畫 Gantt chart / page frame 表，這是必拿分的計算題。
- **deadlock 四條件背不出（Q3, Q10）** → 最高頻，務必背熟。

## 如果你卡住了

1. **概念題**：用「定義 + 對比 + 例子」的結構答（如 mutex vs semaphore：先各自定義、再對比擁有權、再舉 ISR 通知的例子）。
2. **計算題**：scheduling 先畫 Gantt chart、page replacement 先畫 frame 表，再數——別心算。
3. **deadlock 題**：反射性想四條件、想 lock ordering。
4. **「為什麼」類**：很多 OS 設計是「取捨」——回答時點出取捨（如 thread 快但不安全）。

## 自我檢核

- [ ] 我能口頭流暢解釋 process/thread、mutex/semaphore、deadlock 四條件、virtual memory
- [ ] 我能畫 Gantt chart 算 scheduling 的 waiting time
- [ ] 我能畫 frame 表算 FIFO/LRU 的 page fault 數
- [ ] 我能寫 producer-consumer pseudo code 並解釋 semaphore 順序
- [ ] 我分得清 page fault vs segfault、mode switch vs context switch（易混的）

Part 3（OS）綜合驗收完成。Part 4 進入計算機組織——cache、pipeline、編譯連結，技術面的硬體側考點。

→ [Ch 29 數字表示與運算](./29-number-representation.md)
