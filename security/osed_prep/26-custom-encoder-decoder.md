# Ch 26 — 自寫 encoder/decoder stub + 消壞字元

> **目標**：搞懂當 shellcode 含壞字元（Ch 8）時，如何用「編碼 + 執行期解碼」解決：把 payload 編成無壞字元的形式，前面掛一段 decoder stub，執行期先把 payload 還原再跑。你會理解 XOR/加減編碼的原理、decoder stub 自身也要無壞字元的限制、以及 GetPC（取得自身位址）這個關鍵技巧為什麼需要。逐行可組譯的 stub 以權威公開教材為準（延伸閱讀）。

> **環境**：Win10 x64 22H2（32-bit shellcode），nasm，WinDbg（2026）。自有授權實驗室。

> **本章定位**：編碼/解碼是標準 shellcode 技術，本課教原理與限制（為何需要、怎麼運作、坑在哪），讓你讀得懂也能照公開教材實作；完整可組譯的 decoder+payload blob 不在本章產出範圍。

## 為什麼需要 encoder？

Ch 8 教過，目標協定常禁掉某些位元組（`\x00`、`\x0a`、`\x0d` 最常見）。但你手寫或 msfvenom 產生的 shellcode 幾乎一定含這些值——API 位址、立即值、字串裡躲不掉。硬送進去，payload 會在到達前被截斷或改寫，執行必崩。

直接「選指令時避開壞字元」（Ch 25 的手法）對小段 code 可行，但對一整段複雜 shellcode（尤其含固定的 API 位址、網路位元組）很難字字避開。這時換個策略：

```
原始 shellcode（含壞字元）
        │ 離線編碼（如 XOR 一個 key）
        ▼
編碼後 payload（無壞字元）───┐
                            │  送進目標（順利通過協定）
┌───────────────────────────┘
▼
decoder stub（無壞字元）：執行期把 payload XOR 回原樣
        │
        ▼
還原的原始 shellcode → 執行
```

核心交換：**用一小段無壞字元的 decoder，換取「整段 payload 都能無壞字元地送達」。**

## 先建立直覺：編碼就是可逆的變形

encoder 做的事很單純：把每個 byte 用一個**可逆運算**變成另一個值，使變形後的結果不含壞字元。decoder 執行期做反運算還原。最常見兩種：

```
XOR 編碼：       encoded = original XOR key        （解碼：encoded XOR key = original，XOR 自反）
ADD/SUB 編碼：   encoded = original - key           （解碼：encoded + key = original）
```

> **為什麼 XOR 最常用？** 因為 XOR 是自反的（`a XOR k XOR k = a`），編碼和解碼用同一個運算、同一把 key，decoder 最短。缺點是單純 XOR 可能某些 byte XOR 完還是壞字元——這時要換 key，或用多 byte key、或改 ADD/SUB、或滾動 key。

> **key 怎麼選？** 離線嘗試：對整段 shellcode 試不同 key，直到「編碼後完全不含壞字元」且「key 本身也不是壞字元」。這是個搜尋過程，工具（msfvenom 的 encoder、或你自己的 python 腳本）會幫你找。重點是 key 要讓**輸出和 key 都乾淨**。

## 核心一：decoder stub 自己也必須無壞字元

這是最容易被忽略的約束。整個 payload 編碼後乾淨了，但前面那段 decoder stub 是**明文、直接執行**的 code——它自己若含壞字元，一樣會在送達前壞掉。所以：

```
┌────────────────────┬──────────────────────────────┐
│ decoder stub       │ 編碼後的 payload              │
│ (必須無壞字元!)    │ (已無壞字元)                  │
└────────────────────┴──────────────────────────────┘
   ↑ 這段是明文執行，自己含壞字元就全毀
```

所以 decoder stub 要用「天生無壞字元的指令」寫成——這限制了你能用哪些 opcode，是手寫 decoder 的主要難度。msfvenom 的 `shikata_ga_nai` 之所以有名，就是它產生的 decoder 做了很多功夫讓 stub 乾淨且多型（polymorphic）。

## 核心二：GetPC —— decoder 怎麼知道 payload 在哪？

decoder 要對「後面那段編碼 payload」做解碼，就得知道 payload 的位址。但 shellcode 是 position-independent（Ch 22），decoder 不能寫死「payload 在 0x00401500」——它根本不知道自己被載到哪。

解法叫 **GetPC（Get Program Counter）**：執行期動態取得「我自己現在在哪」，再算出後面 payload 的位址。兩種經典手法：

```
手法 A — call/pop：
   call 到緊接著的下一行  → call 會把「返回位址」(=下一行的位址) push 上 stack
   pop 一個暫存器          → 該暫存器現在就是「下一行」的位址 = 我自己的位址

手法 B — fnstenv（FPU 法）：
   fnstenv 會把 FPU 環境（含最後一次 FPU 指令的位址）寫到記憶體
   從中取出那個位址 → 推算自身位址
   （好處：某些情境比 call/pop 更能避開壞字元）
```

> **為什麼 call/pop 能拿到自身位址？** `call` 的副作用是把「call 之後那條指令的位址」當返回位址 push 到 stack。所以 `call 下一行` 緊接著 `pop reg`，reg 就拿到了「下一行的位址」——一個執行期才知道的、position-independent 的自身座標。decoder 以此為基準算出 payload 起點。

> **未實測，理論預期**：GetPC 的兩種手法行為來自 x86 指令語意（`call` 的 push 返回位址、`fnstenv` 寫 FPU 環境）。逐行組語與各自的壞字元特性見 skape / Corelan。本課只確立「為什麼 decoder 需要 GetPC、它怎麼拿到自身位址」這個機制。

## 核心三：解碼迴圈的邏輯

有了 GetPC 給的 payload 起點，decoder 的迴圈很單純（高階描述）：

```
1. GetPC：拿到編碼 payload 的起始位址，放進指標暫存器
2. 設 counter = payload 長度
3. 迴圈：
     把指標處的 byte 做反運算（XOR key / ADD key）
     指標++，counter--
   直到 counter 歸零
4. 跳到 payload 起點，執行已還原的原始 shellcode
```

結束條件可以是計數（已知長度），也可以是「解到某個標記 byte 為止」。這段迴圈本身就是要無壞字元的那部分。

## 範例：一次編碼開發的思路（不含組語清單）

```
1. 手上有原始 shellcode（Ch 25 的，含壞字元 \x00 幾處）
2. 離線用 python 試 XOR key：
   for key in 1..255: 若 (每 byte XOR key) 都不含壞字元 且 key 非壞字元 → 選它
3. 用選定 key 把 shellcode 編碼成乾淨 payload
4. 在前面接上「無壞字元的 decoder stub」(GetPC + XOR 迴圈)
5. 整體 = [decoder][encoded payload]，送進 exploit
6. WinDbg 在 decoder 入口下斷，單步看它解碼、最後跳進還原的 shellcode
```

> **未實測**：實際 key 與 stub bytes 取決於你的 shellcode。逐行 decoder asm 見延伸閱讀。驗證紀律：在 WinDbg 單步 decoder，解碼迴圈跑完後 `db` 看 payload 區是否已還原成你預期的原始 bytes，再放行執行。

## 對比與取捨

| 消壞字元手法 | 適用 | 代價 |
|---|---|---|
| 選指令時避開（Ch 25） | 小段 code、壞字元少 | 大段難字字避開 |
| 單 byte XOR encoder | 通用、decoder 最短 | 某些 byte XOR 後仍壞 → 要換 key |
| 多 byte / 滾動 key | 單 key 解不掉時 | decoder 稍長 |
| ADD/SUB encoder | XOR 解不掉某組合時 | 需處理進位/借位 |
| msfvenom shikata_ga_nai | 快、多型、stub 乾淨 | 位元組樣式已知、易被簽章命中 |

> 看最後一列：msfvenom encoder 方便但**正因太有名而易被偵測**。自寫 encoder（或自訂 hash/key）的另一個價值，就是產生 AV 簽章庫裡沒有的位元組樣式——這呼應 Ch 22「手寫躲偵測」與 Ch 37 PE 後門的 AV 討論。

## 踩雷集錦

1. **「payload 編乾淨了就沒事」**：忘了 decoder stub 自己也是明文執行的 code。stub 含壞字元一樣全毀。編碼解決 payload，不解決 stub——stub 要用天生無壞字元的指令寫。
2. **「XOR 一個 key 一定能消掉所有壞字元」**：不一定。某個 byte XOR 完可能正好變成另一個壞字元。要離線驗證「輸出完全乾淨」，不行就換 key、多 byte key 或改演算法。
3. **「decoder 寫死 payload 位址」**：違反 PIC。decoder 不知道自己被載到哪，必須用 GetPC 動態取得自身/payload 位址。寫死位址換環境即錯。
4. **「解碼範圍算錯，蓋到 decoder 自己或多解」**：長度/起點算錯會解碼到 stub 自身或超出 payload。在 WinDbg 單步確認解碼區間正好是 payload，一個 byte 都不多不少。
5. **「以為自寫 encoder 就一定繞過 AV」**：改位元組樣式只躲簽章，躲不掉行為偵測（Ch 22、37）。encoder 是戰術，不是免死金牌。

## 進階：再往深一層

- **多型（polymorphic）encoder**：每次產生的 decoder stub 都長得不一樣（換暫存器、插 NOP 等價指令、變 key），讓簽章無從下手。shikata_ga_nai 就是多型的。自寫到這層是進階挑戰。
- **為什麼 fnstenv 有時優於 call/pop**：call/pop 的 `call` opcode（`\xe8`）或其相對位移可能正好是壞字元，fnstenv 法能繞開。選哪種 GetPC 取決於你的壞字元集合。
- **stage 掉編碼問題**：若 payload 太大或編碼太麻煩，改用 staged（Ch 22）——只送一個極小的乾淨第一階，大 payload 由攻擊機送進 RWX 記憶體，從源頭減少要編碼的量。
- **防禦視角**：解碼迴圈（一段對自身後方記憶體做 XOR/ADD 的小迴圈 + GetPC）是 encoded shellcode 的典型指紋。逆向分析師看到 call/pop 緊接 XOR 迴圈，立刻知道「後面是編碼 payload，先跑 decoder 再看」（見 `malware_analysis`、`reverse_engineering`）。

## 本章重點整理

- 壞字元多到無法字字避開時，用「離線編碼成乾淨 payload + 執行期 decoder 還原」。
- XOR 最常用（自反、decoder 短）；某 byte 解不掉就換 key / 多 byte key / 改 ADD-SUB。
- decoder stub 自己也必須無壞字元——它是明文執行的 code。
- decoder 靠 GetPC（call/pop 或 fnstenv）取得自身/payload 位址，才能 position-independent 地解碼。
- 自寫/自訂 encoder 的額外價值：產生 AV 簽章庫沒有的位元組樣式；但躲不掉行為偵測。

## 自我檢核

- [ ] 不看筆記，能說出「編碼乾淨了 payload，為什麼 decoder stub 還要另外無壞字元」
- [ ] 能解釋 XOR 為什麼適合當 encoder（自反性）
- [ ] 能說出 decoder 為什麼需要 GetPC、call/pop 怎麼拿到自身位址
- [ ] 知道選 key 的條件是什麼（輸出與 key 都要乾淨）
- [ ] 能說出解碼迴圈算錯區間會出什麼問題、怎麼在 WinDbg 確認

## 延伸閱讀

- **[Corelan — Exploit writing tutorial part 9（encoding 段落）](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：encoder/decoder 與 GetPC 段落，含逐行組語。
  - **學什麼**：本章機制的逐行實作出處。
  - **前提**：Ch 25、本章的編碼直覺。

- **[skape — Understanding Windows Shellcode](https://www.hick.org/code/skape/papers/win32-shellcode.pdf)**
  - **讀哪裡**：GetPC（call/pop、fnstenv）與 self-modifying decoder 章節。
  - **學什麼**：GetPC 各手法的壞字元特性與取捨源頭。

- **[shikata_ga_nai 原理解析（搜 "shikata ga nai polymorphic xor"）]**
  - **讀哪裡**：任一拆解 msfvenom 招牌 encoder 的技術文。
  - **為什麼值得讀**：看一個「多型 + 回饋式 XOR」encoder 怎麼做到 stub 乾淨又多變，是自寫 encoder 的進階範本。
  - **注意**：它的位元組樣式已被簽章——學原理，不要原封照用。

- **本 repo [`malware_analysis`](../malware_analysis/README.md) / [`reverse_engineering`](../reverse_engineering/README.md)**
  - **和本章的關聯**：從逆向端辨認「GetPC + 解碼迴圈 = 後面有 encoded payload」——攻防同一套知識。

Part 6 的機制都備齊了。下一個是整合練習：在自有授權環境裡，把 PEB 走訪、EAT 解析、呼叫慣例、消壞字元串起來，產出一個能用的 payload。

→ [練習 D：手寫 bad-char-free reverse shell shellcode](./practice-d-custom-reverse-shell.md)
