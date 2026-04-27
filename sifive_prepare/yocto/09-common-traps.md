# Ch 9 — 常見雷：sstate-cache / DEPENDS / PREFERRED_VERSION

> 目標：總結 Yocto 日常最容易踩的 15+ 個坑。每個附 symptom + 診斷 + 修法。這章就是那本你看完吐槽 "早說！" 的指南。

## 陷阱 1：sstate-cache 讓修改沒生效

**Symptom**：改了 recipe，rebuild、行為沒變。

**原因**：bitbake 從 sstate-cache 拿 prebuilt、沒真的 re-run 你的 task。

**Debug**：

```bash
bitbake -c compile -f gcc     # force re-compile
# 或
bitbake -c cleansstate gcc && bitbake gcc
```

**避免**：改 recipe 後明確 `cleansstate`，別信 auto-invalidation。

## 陷阱 2：DEPENDS 漏加

**Symptom**：Build fail "file not found" on some include。

**原因**：Recipe 用 library X 但沒宣告 `DEPENDS = "X"`。bitbake 不知道 X 要先 build。

**Fix**：

```
DEPENDS = "zlib openssl-native"
```

- `zlib`：target lib
- `openssl-native`：host tool

## 陷阱 3：RDEPENDS 漏加

**Symptom**：binary build 成功、rootfs 組好、runtime 跑 `./myapp` 說 "libfoo.so.1 not found"。

**原因**：package 本身沒列 runtime dependency。image 裡沒 include libfoo。

**Fix**：

```
RDEPENDS:${PN} = "libfoo"
```

## 陷阱 4：PREFERRED_VERSION 衝突

**Symptom**：想用 gcc 12、bitbake 還是用 gcc 11。

**原因**：PREFERRED_VERSION 沒設對、或 layer priority 沒調。

**Debug**：

```bash
bitbake -e gcc | grep "^PV="
# 或
bitbake-layers show-recipes gcc
```

**Fix**：

```
# conf/local.conf
PREFERRED_VERSION_gcc = "12.%"
PREFERRED_VERSION_gcc-cross-${TARGET_ARCH} = "12.%"
PREFERRED_VERSION_gcc-crosssdk-${SDK_SYS} = "12.%"
```

多個 gcc variant 都要設。

## 陷阱 5：Layer priority 衝突

**Symptom**：recipe 用的是舊 layer 的 version、不是新 layer。

**原因**：layer priority 順序錯。

**Fix**：

```
# meta-mycompany/conf/layer.conf
BBFILE_PRIORITY_mycompany = "15"        # higher than meta-riscv's 6
```

數字越大優先級越高。

## 陷阱 6：DEPENDS 加了 `-native` wrong

**Symptom**：build fail "command not found: xyz"。

**原因**：`DEPENDS = "xyz"` 找 target version、但 `xyz` 是 host tool。

**Fix**：

```
DEPENDS = "xyz-native"
```

例：`flex-native`, `bison-native`, `python3-native`。

## 陷阱 7：TUNE 跟 MACHINE 不一致

**Symptom**：build 到一半 "incompatible abi" 錯誤。

**原因**：`MACHINE` 的 TUNE_FEATURES 不 match `DEFAULTTUNE` 或 package 的 ABI。

**Fix**：確認 `conf/machine/*.conf` 跟 `tune-riscv.inc` 一致。兩方都寫 `riscv64`、不混。

## 陷阱 8：Patch path 錯 (FILESEXTRAPATHS 忘加)

**Symptom**：bitbake "Unable to find file: 0001-foo.patch"。

**原因**：bbappend 裡加了 SRC_URI += file://0001-foo.patch，但沒設 FILESEXTRAPATHS。

**Fix**：

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://0001-foo.patch"
```

`${THISDIR}/files/0001-foo.patch` 放 patch file。

## 陷阱 9：Source 版本 tag 跟 SRCREV 不一致

**Symptom**：`do_fetch` fail 或 pull 錯 commit。

**原因**：git SRC_URI + `branch=main` + SRCREV="xxx"，但 xxx 不在 main branch。

**Fix**：確認 branch 跟 SRCREV 一致：

```
SRC_URI = "git://github.com/foo.git;protocol=https;branch=main"
SRCREV = "abc123..."
```

或用 tag：

```
SRC_URI = "git://...;tag=v1.0"
```

## 陷阱 10：LIC_FILES_CHKSUM 錯

**Symptom**：bitbake ERROR："LICENSE file MD5 mismatch"。

**原因**：upstream 改 LICENSE file、你的 recipe 還有舊 md5。

**Fix**：算新 md5：

```bash
md5sum /path/to/LICENSE
# 更新 recipe
LIC_FILES_CHKSUM = "file://LICENSE;md5=<new-hash>"
```

*注意 review LICENSE 本身變化、確認 license 沒變*。

## 陷阱 11：WORKDIR 被清

**Symptom**：`devtool modify` 後改了 source、bitbake 後 source 沒了。

**原因**：bitbake 預設 `do_unpack` 清 WORKDIR 再 extract source。

**Fix**：用 `devtool` 的 workflow 或加：

```
do_unpack:append() {
    # custom logic to preserve local changes
}
```

正常情況 devtool 已處理。

## 陷阱 12：Python 函式 scope 錯

**Symptom**：recipe 的 python function 引用變數 "not defined"。

**原因**：在 module-level python function vs task function 的 scope 不同。

**Fix**：用 `d` datastore 通訊：

```
python () {
    # Parse-time. d is available.
    val = d.getVar('MYVAR')
}

python do_mytask() {
    # Task-time. d is available.
    val = d.getVar('MYVAR')
    ...
}
```

## 陷阱 13：do_package 太大

**Symptom**：Package 裡含意外的 file（e.g., debug symbol）。

**原因**：FILES:${PN} 沒正確限定。

**Fix**：

```
FILES:${PN} = "${bindir}/myapp ${libdir}/libmine.so.*"
FILES:${PN}-dbg = "${libdir}/.debug/*"
FILES:${PN}-dev = "${libdir}/libmine.so ${includedir}/*"
```

清楚分 runtime / debug / dev。

## 陷阱 14：TMPDIR 沒清就 rebuild

**Symptom**：某些 task 不 update、舊 artifact 用了。

**原因**：bitbake 某些 task 的 hash 算法有 edge case、sstate 判 reuse 錯。

**Fix**：核選項：

```
rm -rf tmp/
bitbake ...
```

浪費 disk/time 但 clean。Production CI 常這樣跑以求 reproducibility。

## 陷阱 15：LAYERSERIES_COMPAT 過舊

**Symptom**：warning "layer 'xxx' does not support ... 'scarthgap'".

**原因**：layer.conf 的 `LAYERSERIES_COMPAT` 沒列 current Yocto release。

**Fix**：

```
# layer.conf
LAYERSERIES_COMPAT_mycompany = "kirkstone scarthgap"
```

## 陷阱 16：multiple provider of virtual/xxx

**Symptom**：build fail "Multiple providers of virtual/kernel"。

**原因**：兩個 recipe claim 是同 virtual 提供者。

**Fix**：

```
PREFERRED_PROVIDER_virtual/kernel = "linux-yocto"
```

## 陷阱 17：Host 的 gcc 被用到

**Symptom**：build target package 時用到 host 的 `/usr/bin/gcc`。

**原因**：Makefile / build script 硬寫 `gcc`，沒尊重 `$CC`。

**Fix**：Patch build script 用 `$(CC)` or `CC=$(CC)`。或 override：

```
EXTRA_OEMAKE = "CC=${CC}"
```

## 陷阱 18：Shell vs Python task mismatch

**Symptom**：`do_compile` 的 python 版本被 override 成 shell 版（或反向）。

**原因**：同一 task 不能 shell + python 混用。

**Fix**：決定一個、remove 另一個。

## 陷阱 19：Do_rootfs 失敗因 package conflict

**Symptom**：

```
ERROR: Package 'xxx' conflicts with 'yyy'
```

**原因**：兩個 package 裝到相同 file path。

**Fix**：fix 一個 package 的 install path、或設 CONFLICT。

## 陷阱 20：網路 fetch 失敗

**Symptom**：`do_fetch` error on some git / http URL。

**原因**：網路斷、proxy 沒設、upstream server 被 rate-limit。

**Fix**：

```
# conf/local.conf
HTTP_PROXY = "http://proxy.company.com:8080"
HTTPS_PROXY = "http://proxy.company.com:8080"

# Or use mirror
SOURCE_MIRROR_URL = "https://mirrors.yoctoproject.org/..."
INHERIT += "own-mirrors"
```

## 診斷的好 command

```bash
# 環境 dump
bitbake -e <recipe> | less

# Dependency graph
bitbake -g <recipe>; cat pn-depends.dot

# Task graph
bitbake -g <recipe> -u taskexp

# Log viewer
bitbake -u taskdetails

# Parse only (no build)
bitbake -p

# Show appended files
bitbake-layers show-appends

# Trace a specific task
bitbake -c listtasks <recipe>
bitbake -c do_compile <recipe> -f -v
```

## 動手練習

1. 故意踩 sstate-cache 陷阱：改 recipe、rebuild、修 symptom。
2. 故意忘 DEPENDS、看 error、加上修復。
3. 改 PREFERRED_VERSION gcc，rebuild、verify version。
4. 寫 `.bbappend` 漏加 FILESEXTRAPATHS、看 error。
5. 讀你系統的 log.do_fetch 一個 fail 範例（製造一個 broken URL）。

## 常見誤會

1. **「bitbake 永遠 incremental 正確」**：大部分 yes、edge case 要 `cleansstate`。
2. **「DEPENDS 寫多沒事」**：會拖慢 build。但比漏加好。
3. **「PREFERRED_VERSION 統一寫一個」**：gcc / gcc-cross / gcc-crosssdk 可能要分別。
4. **「layer priority 高就贏」**：看 recipe name collision。priority 不解 version selection。
5. **「Yocto 的 error message 詳細」**：Honestly not great. Read log.do_* 常比 bitbake output 有用。

## 自我檢核

- [ ] 我能列 10 個以上常見 Yocto 陷阱
- [ ] 我能用 `bitbake -e` 診斷變數
- [ ] 我知道何時該 `cleansstate`
- [ ] 我能讀 log.do_* 找 error 根本
- [ ] 我能避免 DEPENDS / PREFERRED_VERSION 衝突

最後一章：Yocto vs Buildroot 比較。

→ [Ch 10 Yocto vs Buildroot：何時該選誰](./10-yocto-vs-buildroot.md)
