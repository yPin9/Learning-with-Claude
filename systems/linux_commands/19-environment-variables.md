# Ch 19 — 環境變數

> 目標：理解環境變數的繼承機制，掌握 PATH 的查找邏輯，能管理 `.bashrc`/`.bash_profile`/`.profile` 的載入順序。

## 什麼是環境變數

每個行程都有一個環境（environment）：一組 key=value 的字串，繼承自父行程。

```bash
env             # 列出當前行程的所有環境變數
printenv        # 同上，更安全（不執行 shell 指令）
printenv PATH   # 只看某個變數
echo $HOME      # 用 $ 引用
```

## 變數 vs 環境變數

這是 bash 新手最常混淆的地方：

```bash
# 普通 shell 變數：只在當前 shell 存在
myvar="hello"
bash -c 'echo $myvar'    # 空的，子 shell 看不到

# 環境變數：export 後子行程可以繼承
export myvar="hello"
bash -c 'echo $myvar'    # 輸出 hello
```

`export` 把變數加進當前行程的環境，之後 `fork()` 出來的子行程會繼承。

### 一次性環境

只為某個命令設定環境，不影響當前 shell：

```bash
LANG=C ls                      # 只有 ls 看到 LANG=C
DEBUG=1 ./myapp                # 只有 myapp 看到 DEBUG=1
FOO=bar BAZ=qux ./script.sh    # 多個
```

## 重要的內建環境變數

| 變數 | 說明 |
|------|------|
| `PATH` | 可執行檔搜尋路徑 |
| `HOME` | 使用者家目錄 |
| `USER` / `LOGNAME` | 使用者名稱 |
| `SHELL` | 當前 shell 路徑 |
| `PWD` | 當前目錄 |
| `OLDPWD` | 上一個目錄（`cd -` 用的）|
| `LANG` / `LC_*` | 語言和地區設定 |
| `TERM` | 終端類型 |
| `EDITOR` | 預設編輯器 |
| `PS1` | 主提示符（prompt）|

## PATH：命令查找邏輯

```bash
echo $PATH
# /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/alice/.local/bin
```

當你輸入 `ls`，shell 會**依序**查找 PATH 裡的每個目錄，找到第一個 `ls` 就用它。

```bash
which ls          # 顯示 ls 的完整路徑
type ls           # 比 which 更詳細（顯示 alias/function/builtin）
type cd           # cd is a shell builtin
type ll           # ll is aliased to 'ls -alF'
```

### 加入自己的目錄到 PATH

```bash
# 加到 PATH 結尾（最低優先）
export PATH="$PATH:/opt/mytools/bin"

# 加到 PATH 前面（最高優先，會覆蓋系統命令，小心）
export PATH="/opt/mytools/bin:$PATH"
```

加到 `/etc/profile.d/mytools.sh` 可讓全系統都用到。

## .bashrc、.bash_profile、.profile 的差異

這是 bash 最讓人困惑的部分：

```
登入 shell（ssh 進來、su -）     → 讀 /etc/profile，然後讀 ~/.bash_profile 或 ~/.profile
互動式非登入 shell（開新終端）   → 只讀 ~/.bashrc
腳本（bash script.sh）          → 什麼都不讀（非互動）
```

### 實務建議

```bash
# ~/.bash_profile 或 ~/.profile：
# 只放需要「登入時執行一次」的東西，例如 PATH 修改
source ~/.bashrc   # 最後一行 source .bashrc，讓登入 shell 也有 .bashrc 的設定

# ~/.bashrc：
# 放 alias、函式、PS1、互動式設定
alias ll='ls -alF'
alias la='ls -A'

# 改完後立刻套用
source ~/.bashrc   # 或 . ~/.bashrc
```

大部分發行版（Ubuntu/Debian）的 `.bash_profile` 預設已經有 `source ~/.bashrc`，所以你只要改 `.bashrc` 就好。

## 常用操作

```bash
# 設定並 export
export EDITOR=vim
export JAVA_HOME=/usr/lib/jvm/java-17

# 取消 export（變回普通變數）
export -n EDITOR

# 刪除變數
unset EDITOR

# 永久設定：寫到 ~/.bashrc
echo 'export EDITOR=vim' >> ~/.bashrc
source ~/.bashrc
```

## 查看繼承鏈

```bash
# 目前環境（含繼承的）
env | sort

# 只看當前 shell 設定的
set | grep -v '^_'    # set 輸出包含函式，太多雜訊

# 某個行程的環境（從 /proc 讀）
strings /proc/<PID>/environ | grep -E '^PATH|^HOME'
```

## 動手練習

```bash
# 1. 觀察繼承
export MYTEST=parent
bash -c 'echo "child sees: $MYTEST"'   # 能看到
bash -c 'echo "child sees: $mylocal"'   # 看不到（沒 export）
mylocal=secret

# 2. 修改 PATH 並驗證
mkdir -p ~/bin
echo '#!/bin/bash' > ~/bin/myhello
echo 'echo "hello from ~/bin"' >> ~/bin/myhello
chmod +x ~/bin/myhello

export PATH="$HOME/bin:$PATH"
myhello              # 應該能找到

# 3. 臨時環境
LANG=C date          # 英文輸出
LANG=zh_TW.UTF-8 date  # 中文輸出（如果有安裝）

# 4. 找某個變數在哪裡被設定
grep -rn 'EDITOR' ~/.bashrc ~/.bash_profile ~/.profile /etc/profile 2>/dev/null

# 5. 看特定行程的環境
strings /proc/$$/environ | sort
```

## 自我檢核

- [ ] 理解 shell 變數和環境變數的差異（`export` 的作用）
- [ ] 知道 PATH 是依序查找，前面的優先
- [ ] 能區分 `.bash_profile`（登入）和 `.bashrc`（互動）
- [ ] 知道修改 `.bashrc` 後要 `source ~/.bashrc` 才生效

→ [練習 C：行程偵探](./practice-c-process-detective.md)
