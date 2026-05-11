# Ch 13 — Repository 結構

> 目標：理解一個 APT repo 的完整目錄結構，知道 APT 在 `apt update` 時下載了什麼、驗證了什麼，以及如何自己查看 repo 的 metadata。

## 用瀏覽器看 repo

Ubuntu 的 repo 是普通的 HTTP 目錄，可以直接在瀏覽器打開：

```
http://tw.archive.ubuntu.com/ubuntu/
```

你會看到：
```
pool/          ← 實際的 .deb 檔案
dists/         ← 發行版 metadata
```

## dists/ 目錄結構

```
dists/
└── jammy/                      ← Suite 名稱
    ├── InRelease               ← 簽章過的 Release 檔（主入口）
    ├── Release                 ← 未簽章的 Release
    ├── Release.gpg             ← Release 的 GPG 簽章（舊格式）
    ├── main/                   ← Component
    │   ├── binary-amd64/
    │   │   ├── Packages        ← 套件列表（未壓縮）
    │   │   ├── Packages.gz     ← 壓縮版（最常用）
    │   │   └── Packages.xz
    │   ├── binary-arm64/
    │   │   └── Packages.gz
    │   └── source/
    │       └── Sources.gz      ← 源碼套件列表
    ├── universe/
    │   └── binary-amd64/
    │       └── Packages.gz
    └── restricted/
        └── binary-amd64/
            └── Packages.gz
```

```bash
# 直接用 curl 看 Release 檔
curl http://tw.archive.ubuntu.com/ubuntu/dists/jammy/InRelease | head -50
```

## InRelease 檔的內容

`InRelease` 是 GPG 簽章內嵌的 Release 檔（Clearsigned）：

```
-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Origin: Ubuntu
Label: Ubuntu
Suite: jammy
Version: 22.04
Codename: jammy
Date: Thu, 21 Apr 2022 17:16:08 UTC
Acquire-By-Hash: yes
MD5Sum:                                          ← 各 Packages 檔的 MD5
 abcdef1234...  12345  main/binary-amd64/Packages.gz
SHA256:                                          ← 各 Packages 檔的 SHA256
 abcdef1234...  12345  main/binary-amd64/Packages.gz
 ...
-----BEGIN PGP SIGNATURE-----
...
-----END PGP SIGNATURE-----
```

APT 在 `apt update` 時：
1. 下載 `InRelease`
2. 驗證 GPG 簽章
3. 讀取 SHA256 列表
4. 下載 `Packages.gz`
5. 驗證 SHA256 是否和 InRelease 記載的一致

這確保了整個鏈的完整性：GPG 保護了 Release，Release 的 hash 保護了 Packages。

## pool/ 目錄結構

```
pool/
├── main/
│   ├── a/
│   │   └── apt/
│   │       ├── apt_2.4.10_amd64.deb
│   │       └── apt_2.4.10.dsc
│   ├── c/
│   │   └── curl/
│   │       ├── curl_7.81.0-1ubuntu1.15_amd64.deb
│   │       └── libcurl4_7.81.0-1ubuntu1.15_amd64.deb
│   └── ...
└── universe/
    └── ...
```

`pool/` 的目錄結構是 `component/首字母/套件名/`。這個設計讓檔案在目錄下平均分布，避免單一目錄有太多檔案。

Packages.gz 裡的 `Filename` 欄位就指向 pool/ 的路徑：
```
Filename: pool/main/c/curl/curl_7.81.0-1ubuntu1.15_amd64.deb
```

## 直接查看 Packages 索引

```bash
# 解壓並搜尋
zcat /var/lib/apt/lists/tw.archive.ubuntu.com_ubuntu_dists_jammy_main_binary-amd64_Packages \
    | grep -A20 "^Package: nginx$"

# 或直接從 repo 下載（不用 apt update）
curl http://tw.archive.ubuntu.com/ubuntu/dists/jammy/main/binary-amd64/Packages.gz \
    | zcat | grep -A5 "^Package: curl$"
```

## 小型 repo（Flat Repository）

最簡單的 repo 格式——不分 component，Packages 直接放在根目錄：

```
myrepo/
├── Packages          ← dpkg-scanpackages 生成
├── Packages.gz
└── mytool_1.0_amd64.deb
```

sources.list 格式：
```
deb [trusted=yes] http://myserver/myrepo ./
```

最後的 `./` 表示 flat repo（沒有 suite/component 結構）。這是 Ch 23 之前的快速解決方案，reprepro 架的是標準結構。

## 驗證一個 repo 的完整性

```bash
# 1. 抓 InRelease 並驗章（用 gpgv）
curl http://example.com/debian/dists/stable/InRelease -o InRelease
gpgv --keyring /usr/share/keyrings/debian-archive-keyring.gpg InRelease

# 2. 從 InRelease 讀取 SHA256，驗證 Packages.gz
grep "main/binary-amd64/Packages.gz" InRelease
# 比對下載的 Packages.gz 的 SHA256

# apt update 自動做這一切，但手動做一次能加深理解
```

## 自我檢核

- [ ] repo 結構：`dists/<suite>/<component>/binary-<arch>/Packages.gz` + `pool/<component>/<首字母>/<pkg>/<file>.deb`
- [ ] `InRelease` = GPG 簽章內嵌的 Release（Clearsigned）；APT 先驗簽章，再驗 Packages.gz 的 SHA256
- [ ] `apt update` 下載的 metadata 存在 `/var/lib/apt/lists/`，檔名 = repo URL 路徑的變體
- [ ] Flat repo：沒有 suite/component 階層，sources.list 用 `./`；簡單但功能有限

→ [Ch 14 GPG 簽章與信任鏈](./14-gpg-signing.md)
