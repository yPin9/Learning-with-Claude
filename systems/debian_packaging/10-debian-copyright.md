# Ch 10 — debian/copyright：授權追蹤

> **目標**：理解為什麼 Debian 對授權如此嚴格、DEP-5 machine-readable copyright 格式的結構、如何系統性地追蹤一個專案裡所有檔案的授權、以及常見的授權陷阱。

> **環境**：DEP-5 (copyright format 1.0)。授權合規由 Debian Policy §12.5 和 Debian Free Software Guidelines (DFSG) 規範。

## 為什麼授權追蹤這麼重要？

對很多開發者，授權是「丟個 LICENSE 檔就好」的瑣事。對 Debian，授權是**法律義務**和**社群契約**：

- Debian 散布幾萬個套件給全球數百萬使用者。如果某個套件的授權不允許再散布，Debian 就違法了
- 一個專案可能混合多種授權（主程式 GPL、某個 vendored library MIT、某個資料檔 CC-BY），全部都要合規
- main component 只能放符合 DFSG（Debian Free Software Guidelines）的自由軟體；non-free 的東西要分開放

`debian/copyright` 就是這個追蹤的產物：它記錄專案裡**每個檔案**的版權持有者和授權。寫不好不只是 lintian 抱怨，是真的可能讓套件無法被接受。

## 先建立直覺：copyright 是一份「授權地圖」

```
upstream 專案的檔案授權現實：
  src/*.c          → GPL-2+（主程式）
  lib/vendored/*.c → MIT（內嵌的第三方 library）
  docs/*.md        → GFDL（文件用不同授權）
  data/icons/*     → CC-BY-4.0（美術資源）
  debian/*         → 你打包工作的授權

debian/copyright 把這些「掃描歸檔」成機器可讀的地圖：
  哪些檔案 → 誰的版權 → 什麼授權 → 授權全文
```

DEP-5 格式讓這份地圖**機器可讀**——工具能自動驗證、archive 能自動分類、其他人能自動稽核。

## DEP-5 格式結構

`debian/copyright` 用類似 `control` 的 stanza 格式：

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: foo
Upstream-Contact: Jane Developer <jane@upstream.org>
Source: https://github.com/jane/foo

Files: *
Copyright: 2020-2024 Jane Developer <jane@upstream.org>
License: GPL-2+

Files: lib/sha256.c
Copyright: 2015 Bob Crypto <bob@example.com>
License: MIT

Files: debian/*
Copyright: 2024 Your Name <you@example.com>
License: GPL-2+

License: GPL-2+
 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.
 .
 (full license text or pointer to /usr/share/common-licenses/GPL-2)

License: MIT
 Permission is hereby granted, free of charge, to any person obtaining
 a copy of this software ...
 (full MIT text)
```

三種 stanza：

| Stanza 類型 | 作用 |
|---|---|
| Header（第一個）| `Format`、`Upstream-Name`、`Source` 等專案層級資訊 |
| `Files:` stanza | 一組檔案的版權與授權（可多個）|
| `License:` stanza（獨立）| 授權全文，供 `Files` stanza 用 `License:` 短名引用 |

## Files 的匹配規則

`Files:` 用 glob 模式匹配，**後面的覆蓋前面的**（最後匹配的勝出）：

```
Files: *
Copyright: 2024 Main Author
License: GPL-2+
            ← 預設：所有檔案都是 GPL-2+

Files: lib/*.c
Copyright: 2015 Library Author
License: MIT
            ← 例外：lib/ 下的 .c 是 MIT（覆蓋上面的 *）

Files: debian/*
Copyright: 2024 You
License: GPL-2+
            ← debian/ 你的打包工作
```

> 匹配規則是「最後一個匹配的 stanza 勝出」，不是「最具體的勝出」。所以順序很重要：先寫通用的 `Files: *`，再寫越來越具體的例外。寫反了會匹配錯誤。

## 引用 common-licenses

常見授權（GPL、LGPL、Apache、BSD...）的全文很長。Debian 在 `/usr/share/common-licenses/` 放了這些標準授權全文，你可以**引用**而不貼全文：

```
License: GPL-2+
 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.
 .
 On Debian systems, the complete text of the GNU General Public
 License version 2 can be found in "/usr/share/common-licenses/GPL-2".
```

```bash
ls /usr/share/common-licenses/
# Apache-2.0  BSD  GPL-1  GPL-2  GPL-3  LGPL-2  LGPL-2.1  LGPL-3  MPL-1.1 ...
```

> 但**不是所有授權都能只用引用**。GPL 要求「附上授權聲明」，引用 common-licenses 的全文是可以的（搭配一段聲明）。MIT/BSD 這種「必須保留 copyright notice」的授權，每個 copyright holder 的聲明要寫進來。實務上：GPL 系列可引用 common-licenses，permissive 授權（MIT/BSD）通常要貼全文（因為每個專案的 copyright 行不同）。

## 工具輔助：掃描授權

手動檢查每個檔案的授權對大專案不現實。工具幫忙：

```bash
# licensecheck：掃描檔案找授權聲明
licensecheck -r src/
# src/main.c: GPL-2+
# src/util.c: GPL-2+
# lib/sha256.c: MIT
# ...

# 安裝
sudo apt install licensecheck

# debmake 能根據掃描結果生成 copyright 草稿
sudo apt install debmake
debmake -cc > debian/copyright.draft   # 生成 DEP-5 草稿（要人工審核！）
```

> **工具生成的 copyright 必須人工審核**。licensecheck 靠檔案開頭的授權聲明文字判斷，會漏掉沒寫聲明的檔案、誤判變體授權、抓不到 binary 資料檔的授權。它給你草稿，你負責正確性。

## DFSG 與 main/contrib/non-free

Debian 把套件分到不同 component，依據是授權：

```
main          → 完全符合 DFSG 的自由軟體，且不依賴 main 外的東西
contrib       → 本身自由，但依賴 non-free 的東西（如需要非自由 firmware）
non-free      → 不符合 DFSG（如禁止商業使用、不可修改）
non-free-firmware → 非自由韌體（bookworm 後從 non-free 分出來）
```

DFSG（Debian Free Software Guidelines）的核心要求：
- 可自由再散布
- 必須提供 source code
- 允許修改和衍生
- 不歧視個人或團體、不限制使用領域

```
常見「以為自由其實不是」的授權：
  CC-BY-NC（禁止商業）        → non-free（限制使用領域）
  "free for non-commercial"  → non-free
  JSON License（"do no evil"）→ non-free（限制使用目的）
  專有 firmware blob          → non-free-firmware
```

> 判斷一個授權是否 DFSG-free 有時很微妙。Debian 有 ftp-master 團隊和 debian-legal 郵件清單專門處理爭議案例。打包時如果不確定，查 [DFSG FAQ](https://www.debian.org/social_contract) 和過往案例。

## 完整範例：混合授權專案

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: greet
Upstream-Contact: Jane Dev <jane@example.org>
Source: https://github.com/jane/greet

Files: *
Copyright: 2020-2024 Jane Dev <jane@example.org>
License: Apache-2.0

Files: third_party/utf8.h
Copyright: 2018 Sheredom <admin@sheredom.com>
License: Unlicense

Files: data/flags/*.svg
Copyright: 2019 Wikimedia Commons contributors
License: CC-BY-SA-4.0

Files: debian/*
Copyright: 2024 Your Name <you@example.com>
License: Apache-2.0

License: Apache-2.0
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 .
 On Debian systems, the full text of the Apache License version 2.0
 can be found in "/usr/share/common-licenses/Apache-2.0".

License: Unlicense
 This is free and unencumbered software released into the public domain.
 .
 (full Unlicense text — not in common-licenses, must include)

License: CC-BY-SA-4.0
 (full CC-BY-SA-4.0 text or a proper pointer — this is long)
```

設計重點：
- `third_party/utf8.h` 是 vendored 的第三方檔案，授權不同，單獨列出
- 美術資源（SVG）常用 CC 授權，要追蹤
- Apache-2.0 引用 common-licenses；Unlicense 不在 common-licenses 要貼全文
- `debian/*` 是你的打包工作，標你的授權

## 故意弄壞：漏掉一個檔案的授權

```bash
# lintian 會抓出 copyright 沒涵蓋的情況
lintian foo_1.0-1.dsc
# W: foo source: missing-license-paragraph-in-dep5-copyright unlicense
#    （引用了 License: Unlicense 但沒提供它的 License stanza）
# E: foo source: copyright-without-copyright-notice
#    （某些檔案沒有對應的 copyright 條目）
```

lintian 會交叉檢查：`Files:` 引用的每個 `License:` 短名是否有對應的全文 stanza、是否有檔案沒被任何 `Files:` 匹配到。這些檢查抓出 copyright 的不完整。

## 踩雷集錦

1. **`Files: *` 寫在最後**：匹配規則是「最後匹配勝出」，通用的 `*` 寫最後會覆蓋掉所有具體例外。`*` 要寫**最前面**，例外越來越具體往後排

2. **引用 License 短名但沒提供全文 stanza**：`Files` 裡寫 `License: MIT` 卻沒有獨立的 `License: MIT` stanza 提供全文，lintian 報錯

3. **vendored 第三方檔案漏掉**：upstream 內嵌了第三方 library（`third_party/`、`vendor/`），它們的授權常和主程式不同，必須單獨追蹤。licensecheck 幫你找

4. **把 CC-BY-NC 放進 main**：NC（non-commercial）限制使用領域，不符合 DFSG，必須放 non-free。誤放 main 會被 ftp-master 退件

5. **GPL 軟體連結 GPL-incompatible library**：授權相容性是另一層問題。GPL 程式不能連結某些 GPL-incompatible 的授權（如 OpenSSL 的舊授權曾有爭議）。這超出 copyright 檔案範圍，但打包時要注意

6. **以為 copyright 檔案可有可無**：每個 binary package 的 `/usr/share/doc/<pkg>/copyright` 是 Policy **強制要求**的（Ch 4 練習也踩過）。沒有它 lintian 報 error，套件不合格

## 進階：machine-readable 的價值與自動稽核

DEP-5 不只是「整齊」，它讓授權**可被機器處理**：

- **Debian 的授權稽核工具**能自動掃描整個 archive，找出授權衝突、過期、不相容
- **SPDX**（Software Package Data Exchange）是更通用的跨生態授權標準；DEP-5 和 SPDX license identifier 對齊（`GPL-2.0-or-later` 等），讓 Debian 的授權資料能匯入更大的供應鏈分析
- 企業的 **SBOM（Software Bill of Materials）** 需求越來越強（美國政府要求），DEP-5 這種精確授權追蹤正是 SBOM 的基礎

```bash
# 看一個套件的 copyright（已裝）
cat /usr/share/doc/curl/copyright
# 它是 DEP-5 格式，可以被工具 parse
```

理解這點，你會發現 copyright 追蹤不是官僚儀式，而是現代軟體供應鏈安全的一環。

## 動手練習

1. 看幾個真實套件的 copyright：`cat /usr/share/doc/curl/copyright`、`/usr/share/doc/git/copyright`（git 是混合授權的好例子），觀察 `Files:` stanza 怎麼分

2. 對一個有第三方檔案的專案跑 `licensecheck -r .`，看它找出幾種授權。對照專案實際的 LICENSE 檔，看 licensecheck 漏了什麼

3. 故意寫一個不完整的 copyright（引用 `License: MIT` 但不提供 stanza），跑 lintian 看它報什麼錯

4. 找一個 non-free 的套件（如某些 firmware），看它為什麼在 non-free，讀它的 copyright 授權有什麼 DFSG 不允許的限制

## 本章重點整理

- `debian/copyright` 是專案每個檔案的授權地圖，是法律義務也是社群契約
- DEP-5 格式：Header + 多個 `Files:` stanza（glob 匹配，最後勝出）+ 獨立 `License:` stanza（全文）
- 常見授權可引用 `/usr/share/common-licenses/`；permissive 授權通常要貼全文
- DFSG 決定套件進 main/contrib/non-free；NC、no-evil 等限制使用的授權非自由
- licensecheck 等工具輔助掃描，但必須人工審核

## 自我檢核

- [ ] 能解釋為什麼 `Files: *` 要寫在最前面（最後匹配勝出規則）
- [ ] 知道什麼授權能引用 common-licenses，什麼必須貼全文
- [ ] 能判斷「free for non-commercial use」屬於 main 還是 non-free，並說出理由
- [ ] 知道 vendored 第三方檔案為什麼要單獨追蹤授權
- [ ] 能說出 DEP-5 machine-readable 格式在供應鏈安全（SBOM）的價值

## 延伸閱讀

### 官方文件

- **[DEP-5: Machine-readable copyright format](https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/)**
  - **讀哪裡**：整份規格，特別是 `Files` 匹配規則和 syntax
  - **學什麼**：copyright 格式的權威定義，所有欄位和邊界情況
  - **前提**：讀完本章

- **[Debian Free Software Guidelines (DFSG)](https://www.debian.org/social_contract#guidelines)**
  - **讀哪裡**：10 條 guideline 全文（很短）
  - **學什麼**：判斷授權是否「自由」的官方標準
  - **前提**：無

### 部落格 / 文章

- **[Debian Policy §12.5 (Copyright information)](https://www.debian.org/doc/debian-policy/ch-docs.html#copyright-information)**
  - **這篇說什麼**：copyright 檔案的強制要求和放置位置
  - **讀哪裡**：§12.5 整節
  - **為什麼值得讀**：說明為什麼 copyright 是強制的、必須放哪、common-licenses 的使用規則

→ [Ch 11 Quilt patches 系統](./11-quilt-patches.md)
