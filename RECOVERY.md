# 恢復卡（RECOVERY）

> 本檔案入庫，因此**任何 clone（含從鏡像 clone 出來的）都帶着它**。
> 事故發生時，倉庫內的腳本可能已被刪光——下面的命令只用 `git` 與系統工具。

## 情境 0：本機整台沒了 —— 先找異地副本

異地副本在 **另一臺設備**（Windows，`yoga`）：`D:\Backups\rime\rime-snapshot-*.tar.gz`

```sh
# 從本機或任一臺能 ssh 過去的機器取回
scp yoga:'D:/Backups/rime/rime-snapshot-*.tar.gz' /tmp/
tar -xzf /tmp/rime-snapshot-*.tar.gz -C /tmp/snap
# 之後走「情境 2」
```

（Windows 端 console 是 GBK，中文檔名會顯示成亂碼——**內容無損**，以 sha256 爲準。）

## 情境 1：歷史被改寫／倉庫被清空（filter-repo、reset --hard、gc --prune=now）

```sh
git clone ~/Backups/rime/mirror.git /tmp/recover
cd /tmp/recover
git log --oneline -5                       # origin/main ＝ 最後一次自動同步的狀態
```

要更早的點：

```sh
git fetch origin '+refs/snapshots/*:refs/snapshots/*'
git log --oneline --all | head -20
git checkout -B main refs/snapshots/20261105-063917-be046f08
```

自守衛留的改寫前 bundle：

```sh
mkdir -p /tmp/recover && git clone ~/Backups/rime/pre-rewrite/pre-rewrite-*.bundle /tmp/recover
```

## 情境 2：整台機／倉庫目錄全丟

```sh
# 有快照檔（在另一臺設備或 ~/Backups/rime/）
tar -xzf rime-snapshot-YYYYmmdd-HHMMSS.tar.gz -C /tmp/snap
git clone /tmp/snap/repo.bundle ~/.config/ibus/rime
cd ~/.config/ibus/rime
cp -a /tmp/snap/ignored/. .               # 不可再生區：download/、log/、tmp/
git remote set-url origin https://github.com/codelllc09/rime-xuma-L.git
```

## 情境 3：恢復後的一次性設置

```sh
cd ~/.config/ibus/rime
git config core.hooksPath lua/codelllc09/tools/hooks     # 鉤子入庫，但 config 不入庫
git config gc.reflogExpire never
git config gc.reflogExpireUnreachable never
lua/codelllc09/tools/backup.sh mirror-init               # 重建本地裸鏡像
cd lua/codelllc09/tools && python3 check_consistency.py  # 應全綠
```

## 情境 4：只是詞典壞了（想退回上一版）

```sh
cd ~/.config/ibus/rime
git checkout -- xuma_L.dict.yaml            # 詞典是生成物且入庫，git 就是版本庫
ibus restart
```

## 備忘：什麼是可再生的

| 路徑 | 可再生？ |
|---|---|
| `std_tables/download/`（72M） | **否**——一次性考據原件 |
| `dict_pipeline/log/` | **否**——台賬與管道明細（已入庫，另存快照） |
| `tmp/` | **否**——對賬報告與審查答辯 |
| `dict_pipeline/corpus/sources/`（60M） | 是——`build_sources.py` ＋ `SHA256SUMS`，淨室實測 29/29 |
| `dict_pipeline/cache/`（92M） | 是——管道重算（熱 10s／冷 50s） |
| 詞典與 lua 生成物 | 是——生成器＋`PROVENANCE.json` 輸入指紋 |

## 驗證恢復是否成功

```sh
cd lua/codelllc09/tools
python3 check_consistency.py     # 十一項電池；③corpus SKIP 屬正常（語料不在 git）
./backup.sh drill                # 造快照→還原→復跑門禁→驗鏡像可 clone
```
