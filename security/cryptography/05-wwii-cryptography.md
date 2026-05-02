# Ch 5 — 二戰密碼學：Enigma、Bombe、Turing

> 目標：透過 Enigma 機械原理與 Bletchley Park 故事，看「機器密碼學」與「人 vs 工程化密碼分析」的開端。這是現代電腦科學與密碼學的共同起點 — 第一台 Colossus 為破密而生，圖靈在這裡寫了那些影響整個 CS 的論文。

## 為什麼要花一章講 Enigma

三個理由：

1. **Enigma 是真實複雜系統的最早教學樣本**：rotor、reflector、plugboard、daily key — 每個元件都對應現代密碼學概念
2. **被破解的故事教 attacker mindset**：Bletchley Park 的招式（known plaintext、crib、Banburismus）今天看 CTF 仍能找到對應
3. **電腦科學的起源故事不能少**：Turing 的 universal machine 想法、Shannon 訪 Bletchley、Colossus 真空管電腦 — 整個 CS 的土壤從這裡長出

## Enigma 機長什麼樣

```
鍵盤
  │ (按 A)
  ▼
plugboard (插線板，10 對線把 26 字母兩兩交換)
  │ (A → 變 P)
  ▼
rotor 1 (轉子 I) — 每按一鍵自轉一格
  │
  ▼
rotor 2 (轉子 II) — rotor 1 滿一圈才轉一格
  │
  ▼
rotor 3 (轉子 III) — rotor 2 滿一圈才轉一格
  │
  ▼
reflector (反射器，固定 26 對配對)
  │ 訊號反向
  ▼
rotor 3 → rotor 2 → rotor 1
  │
  ▼
plugboard
  │
  ▼
燈板亮一個字母 (假設 K)
```

**關鍵特性**：

- **每按一鍵 rotor 1 轉一格** → 同 plaintext 字母在不同位置加密成不同字母
- **3 個 rotor**：周期 26³ = 17,576
- **rotor 可換位置**（5 選 3 排列）：5 × 4 × 3 = 60
- **plugboard 對配對**：C(26,2) × C(24,2) × ... = 約 1.5 × 10¹⁴
- **總 key space**：約 1.59 × 10²⁰ 種設定 — 暴力不可能

## Reflector 與 self-encryption 缺陷

reflector 把訊號反向回來。優點：**enc = dec**（同一台機 same setting，按 K 就回 plaintext A）。

缺點：**plaintext 字母永遠不會加密成自己**：

```
按 A → 永遠不會亮 A 燈
按 K → 永遠不會亮 K 燈
```

這是 Enigma 最致命的設計缺陷。讓 cryptanalyst 能做 **negative test**：

```
假設一段 ciphertext = "KFXPB QJWMR"，
推測 plaintext 含 "WETTERBERICHT"（天氣報告，德軍每日固定詞），
試對齊：
  positions 0-12:
    K vs W: ✓ (不同)
    F vs E: ✓
    X vs T: ✓
    P vs T: ✓
    ...
  positions 1-13:
    K vs (?), F vs W: ✓
    ...
位置 i 若 ciphertext[i] == plaintext[i]，這個對齊不可能
→ 排除 mismatched positions
```

這個 elimination 是 Bombe 機自動化的關鍵。

## Operator Procedure：人為弱點

軍方 Enigma 操作流程：

1. 每天用「**daily key**」（從 codebook 拿，一個月一張）設好 rotor / plugboard
2. 為每條訊息隨機選 **3 個字母 message key**（例 "QFR"），用 daily key 加密兩次（為防傳輸錯誤），開始訊息
3. 把 rotor 設到 "QFR"，加密訊息本體
4. 接收方用 daily key 解出 message key，再設 rotor 解訊息

問題：

- **重複加密 message key**（早期程序）讓波蘭密碼學家 Marian Rejewski 1932 找到結構
- **operator 偷懶**：用 "AAA"、"ABC" 這種 key
- **同 daily key 用整天**：給 cryptanalyst 大量 ciphertext

實務上 **「operator 是最弱環節」永遠成立**。今天密碼學工程也不例外（Heartbleed、Sony PS3 都是 operator 級錯誤）。

## 波蘭三劍客：1932 開始的破譯

Marian Rejewski、Jerzy Różycki、Henryk Zygalski 是波蘭情報局密碼學家。1932 年起：

- Rejewski 從**重複加密 message key 的循環結構**反推 rotor wiring
- 發明 "**bomba kryptologiczna**"（cryptographic bomb，1938 年）— 機械化試 key
- 1939 年，二戰前 5 週，把方法交給法國 / 英國

這段歷史 1970 年代解密前完全保密。**英國早期破譯成功 90% 來自波蘭基礎**。

## Bletchley Park 與 Turing's Bombe

英國政府碼學校（Government Code & Cypher School，GC&CS）1939 搬進 Bletchley Park（倫敦北 80 km 莊園）。高峰時 9000+ 人員。

**Turing 的貢獻**（1940-1945）：

- 改良波蘭 bomba 成 **British Bombe**：機械化測試 Enigma 設定
  - 利用 reflector 缺陷與 known plaintext (crib)
  - 高峰時 200+ 台 Bombe 平行運作
  - 一個 setting 約 20 分鐘可測完
- 設計 **Banburismus**：機率性方法縮小搜尋空間
  - 用 sequential analysis（後來 Wald 1945 學術正名）
- 與 Welchman 合作 diagonal board 大幅減少 false positive

**Bombe 不是一台「計算機」**：是電氣機械的 brute-force 引擎。給它一個 crib（如 "WETTERBERICHT"），它測試所有 60 × 17576 = 1,054,560 個 rotor setting 看哪個能讓 crib 對齊不矛盾。

## Crib：known plaintext attack 的祖宗

**crib** = 推測的 plaintext 片段。來源：

- 德軍每日 6:00 發 "WETTERBERICHT"（天氣報告）
- 訊息結尾常有 "HEILHITLER"
- "OBERKOMMANDO DER WEHRMACHT"（德軍最高司令部）出現頻繁
- 制式報告開頭 "AN DIE HEERESLEITUNG"

**Garden 戰術**：盟軍故意在某海域佈雷，知道德軍會發報告 → 預期報告含 "Minen" → 給 crib。

對 modern crypto 對應：**known plaintext attack 至今仍是攻擊面**（PNG header 永遠 magic byte、PDF 永遠 `%PDF-`）。設計密碼系統不能假設 plaintext 不可預測。

## Lorenz / Tunny：另一條戰線

Enigma 是戰術級（軍級以下），**戰略級用 Lorenz SZ40/42**，盟軍代號 "Tunny"。比 Enigma 更複雜（12 rotor、teleprinter 級）。

破譯靠：

- **Bill Tutte**：純數學分析，從未見過 Lorenz 機就推出整個結構（密碼學史上頂級成就）
- **Colossus**：真空管電腦（1943）
  - 第一台可程式化電子數位計算機（早 ENIAC 三年）
  - 為 Tunny 破譯而生
  - Tommy Flowers 設計建造，1500 真空管

戰後 Churchill 命令所有 Colossus 銷毀（保密程度極高），1970 年代才解密。**英國電腦工業因此**（戰後沒法用 Colossus 經驗）**落後美國數年**。

## SIGABA：唯一沒被破的

美國的 SIGABA（1940 部署）：

- 15 個 rotor（5 個 cipher、5 個 control、5 個 index）
- rotor 不規則前進（不像 Enigma 規律）

**整個二戰沒被破**。德軍嘗試但失敗。設計上規避了 Enigma 的所有已知缺陷。

可惜 SIGABA 機 + key 整套體積大、操作慢，戰後讓 NSA 設計更輕便的後繼。

## 數字：盟軍從破密得到什麼

歷史學家估計：

- **Atlantic 戰役**：U-boat 損失因 Enigma 破譯增加 50%+
- **Battle of the Mediterranean**：海運 logistics 大量受惠
- **D-Day**：盟軍知道德軍對登陸地點的判斷
- **戰爭縮短**：估計 2-4 年

「**密碼分析改變二戰結局**」不誇張。但這個事實到 1970 年代才公開，因為情報來源「Ultra」必須繼續保密（戰後初期蘇聯仍用類 Enigma 機）。

## Turing 的後續

戰後 Turing 寫了 1948 「Intelligent Machinery」、1950 "Computing Machinery and Intelligence"（Turing test 那篇）。

但 1952 年因同性戀「gross indecency」被起訴（當時英國刑罰）。被迫接受荷爾蒙治療（化學閹割）。1954 自殺，41 歲。

2009 英國首相布朗正式道歉。2013 女王發出皇家赦免（Royal Pardon）。

**Turing 法則**（Alan Turing law，2017）：對英國歷史上因同性戀被起訴的人追溯赦免。

## 一個常見誤解

「Enigma 被破是因為 key space 不夠大」

**錯**。1.59 × 10²⁰ 在當時是天文數字，純暴力不可能。被破的原因：

1. **設計缺陷**（reflector 的 self-encryption restriction、rotor 預測前進）
2. **Operator 程序錯誤**（重複 message key、用 predictable key）
3. **Crib 提供大量 known plaintext**
4. **Bletchley 機械化破譯**（Bombe + Colossus）
5. **波蘭 / 英國 / 美國情報合作**

**現代密碼學每個角度都比 Enigma 嚴謹**：演算法公開審查、AES 沒已知 plaintext-related weakness、CSPRNG 取代 operator-chosen nonce、constant-time 防 side-channel。但**「設計沒缺陷 + operator 不犯錯 + 系統沒漏洞」**仍是永恆難題 — 看現代 CVE 你會發現 Enigma 的教訓沒有過時。

## 自我檢核

- [ ] 我能畫出 Enigma 機的訊號流（plugboard → rotor → reflector → ...）
- [ ] 我能解釋為什麼 reflector 讓 plaintext 永遠不加密成自己
- [ ] 我能說出至少兩個 operator 程序錯誤
- [ ] 我能解釋 crib 是什麼以及它怎麼讓 Bombe 工作
- [ ] 我能說出 Colossus 與 Bombe 的差別
- [ ] 我知道 Turing 的下場以及 Turing 法則

下一章從 Shannon 1949 的 perfect secrecy 證明出發，看「真正不可破」的密碼存在嗎，以及為什麼工程上不用它。

→ [Ch 6 Shannon 與一次性密碼本](./06-shannon-otp.md)
