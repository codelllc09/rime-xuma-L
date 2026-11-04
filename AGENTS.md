# AGENTS.md — 本倉庫 agent 作業規約

本倉庫是高度定制的徐碼·L 輸入法方案（Rime），本人自用兼公開；
**輸出以繁體爲主**，文檔、提交信息一律繁體。個人裁定檔案隨庫公開是自覺決定。

## 補詞審核規約（最重要）

本節九條判例（2026-09）已全數機化：收錄閘在 `lua/codelllc09/tools/gen_freq_dict.py` 補詞階段，
字形傾向數據在 `lua/codelllc09/tools/dict_pipeline/archive/rulings/用詞傾向.txt`（偏好/禁族/例外三種行），
一致性校驗 `cd lua/codelllc09/tools && python3 check_tendency.py`（偏枯/混形即非零退出）。
判例已全數機化（收錄閘/傾向表/驗收基準），口語原文不隨庫保留。

**收錄規則**（新增詞是否入庫）：
1. 已在詞表者不重加；
2. 禁族（個/箇/裏/里/周/週）之「詞」不自動入庫（存量歷史保留，僅統計；
   管道 DROP_CHARS 另含「个」簡形，冗餘保險）；
3. 三字詞不自動入庫（手收與 `/add` 顯式收錄不受限）；
4. 顯式收錄意圖壓倒黑名單：用戶手收/`/add` 的詞，禁族與否決閘讓路。

**字形傾向**（機讀表，非硬規則）：本字偏好（气>氣）等經校驗器報告，
雙活/獨活等不一致屬「待裁」，列入傾向表待決區呈報用戶，勿擅改詞表。

**工作方式**（準則第 8/9 條）：
- 一簡對多繁歧義：自行查證，不呈報；
- 錯別字級判斷：自行判決，不上交；
- 只有異體取捨（麪 vs 麵級）與傾向表待決項才呈報；
- 審核隊列 `archive/reports/TO_BE_REVIEWED.txt`；批注後 `python3 lua/codelllc09/tools/transcribe_review.py`
  自動轉抄三清單並自驗；字形裁定落 `archive/rulings/用詞傾向.txt`；然後重跑管道。
- **命名規約：需人親自批注的文本一律全大寫英文名、語義明確**（現行：TO_BE_REVIEWED）。

## 詞庫改動的正道

- `xuma_L.dict.yaml` 數據區由 `lua/codelllc09/tools/gen_freq_dict.py` 生成，**勿手改**；
  詞库意愿經三通道表達：補詞裁定（`archive/rulings/`）、次序裁定（`manual_overrides.txt`）、
  手收詞（`handpicked.txt`）——改完重跑管道生效；
- 語料區 `corpus/sources/` 文件名字母前綴＝打分源代號；
  佈局與復現見 `corpus/sources/BUILD.md`；
- `archive/rulings/` 是人寫裁定，機器勿改；`archive/reports/` 每輪重寫，可再生。

## std_tables/（字形裁定源）

- 官方字表存檔：25 張清洗後字表（tracked，official/＝現代官方標準、classics/＝古籍語料）
  ＋ `download/` 原始件暫存區（忽略），
  是**非簡模式攔截集的唯一數據源**，與詞頻管道（corpus/）互不相干；
- `no_simple_data.lua` 是生成物，**勿手改**——改攔截集走
  `gen_no_simple_data.py`（改字表/邏輯後重生成），生成閉環：
  `python3 gen_no_simple_data.py --verify`（零漂移即綠）；
- `download/` 屬 ignore 區，變更記 `dict_pipeline/log/IGNORED_CHANGES.log` 台賬；
  表爲一次性定稿考據，重建走「原件 + README 出處」按案重做，不設一鍵復現。

## 作業紀律

- 改 schema/lua 後必須部署驗證：`ibus restart` → 取最新
  `/tmp/rime.ibus.*.log.INFO.*.log`，`grep -icE "error|E[0-9]{4}"` 必須爲 0；
- `.gitignore` 採白名單具名制（`dict_pipeline/`、`sources/` 等大目錄整體忽略，
  跟蹤件逐一具名放行）：新跟蹤文件需顯式加白名單行；
  **行內尾註不被 gitignore 支持**（`!path  # 註` 會使規則失效），註釋獨立成行；
- 詞表改動後必跑雙檢：`check_tendency.py`（字形傾向）與
  `check_acceptance.py`（44 碼首選回歸基線）皆須零退出
  （tendency：偏枯/混形任一即非零）；
- 詞表每次改動後必須產出複覈報告：
  `python3 lua/codelllc09/tools/diff_report.py <舊提交> HEAD` → `tmp/詞表改動-*.md` 供用戶逐條過目，
  用戶指出誤改即回滾調整——改動可見性優先於效率；
- **留痕規定**（2026-11-05 加固改寫）：凡被 gitignore 的數據承載文件，改動必須留痕——
  git 之外的第二歷史手段。**唯一寫入口是 `tools/_ledger.py`**，勿自拼字串：
  - `note(path, 動作, 說明)`：必寫 `log/events.jsonl`（機讀）；目標在忽略區時
    **另**寫 `log/IGNORED_CHANGES.log`（人讀 TSV，四欄 `ISO時間	路徑	動作	說明`）。
    已入庫文件只進 JSONL（git 即歷史），不稀釋人讀台賬；
  - `note_if_changed(...)`：僅字節真變動時留痕，防每次運行刷屏；
  - shell／手工補記走 `python3 tools/_ledger.py <路徑> <動作> <說明>`。
  - **兩檔皆入庫**：台賬若也不在 git，二重歷史與 git 同歸於盡（此前的邏輯漏洞）。
  - 已接駁：diff_report（tmp 報告）、build_sources（語料區）、handpick_commit
    （入庫事件＋**部署事件含引擎錯誤計數**）、transcribe_review（轉抄判定）、
    gen_freq_dict（xuma.addon.dict.yaml）。
  - 免記：純派生緩存 `cache/`（管道重算即得）。
  - **機械強制**：`check_consistency.py` ⑥ 留痕靜態電池——凡寫盤腳本必須有
    `_ledger` 調用，或在電池的 `WRITER_EXEMPT` 中聲明理由；未聲明者即敗。
    新增寫盤點時，二者必居其一（這是「所有需要有日誌的行爲都有日誌」的執行機制）。
- **溯源規定**（2026-11-05 新增）：生成物與其生成器、輸入指紋的對應關係記於
  `tools/PROVENANCE.json`（由 `tools/_provenance.py` 維護）。三個生成器運行時
  自動刷新；新增生成物須在 `_provenance.SPEC` 聲明輸入 glob。
  `check_consistency.py` ⑦ 溯源電池比對輸入指紋——輸入漂移而產物未重生成即敗。
- **部署前閘門**（2026-11-05）：`handpick_commit.sh` 在 `ibus restart` **之前**驗
  詞典數據行數下限＋`check_acceptance`（44 碼首選回歸）。生成可以失敗，但失敗的生成
  **絕不上線**——此前是「管道跑完就直接重啟」，壞詞典會被打字的人第一個發現。
- **原子寫**（2026-11-05）：`gen_freq_dict.py` 寫詞典與報告走 `.tmp`＋`os.replace`＋fsync。
  中斷時要麼舊檔完整、要麼新檔完整，不會留下半截詞典（半截詞典＝輸入法癱瘓）。
- **提交門禁**（2026-11-05 加固）：`core.hooksPath` 已指向 `lua/codelllc09/tools/hooks`
  （鉤子**入庫**，克隆後在，不像 `.git/hooks` 會消失）。`pre-commit` 守兩件事——
  台賬衛生＋全電池十項，⑦溯源可抓「改了 rulings／語料卻沒重跑管道」這類最常見的漏步驟。
  新克隆需一次性執行 `git config core.hooksPath lua/codelllc09/tools/hooks`。
  確需跳過用 `git commit --no-verify`，並在提交信息說明理由；**靜默跳過即違紀**。
  `post-commit` 負責把每個提交追加進本地裸鏡像（見「歷史改寫防護」）。
  `pre-push` 在推送前重跑電池（pre-commit 可被 `--no-verify` 繞過，推送是最後一關）。
- **CI**（2026-11-05 新增）：`.github/workflows/gate.yml` 在 push/PR 上跑全電池十項、
  Python 語法、提交身份白名單、敏感路徑掃描。語料不在 git，故 ③corpus／⑤tendency
  在 CI 必 SKIP——這是設計而非缺陷；需活庫語料的檢查由本機鉤子與 `backup.sh drill` 承擔。
- **一次性設置**：`./lua/codelllc09/tools/setup.sh`——鉤子／reflog／鏡像都存在
  `.git/config`（不入庫），克隆後必須重跑；腳本化以免恢復時漏步。
- **遠端分支保護**（2026-11-05）：GitHub `main` 已開保護——**禁強制推送、禁刪除，
  `enforce_admins` 亦爲 true**（連擁有者也不能繞過）。這是本地鏡像之外的第二道
  「防手滑」網，且它在雲端，本地全毀時仍生效。
  實測：正常推送可通；`git push --force origin HEAD~1:main` 被拒
  （`Cannot force-push to this branch` / `protected branch hook declined`）。
  ⚠ 今後若真要重寫歷史並推上去，**必須先去 GitHub 設置裏臨時關閉保護**——
  這一步「刻意摩擦」正是設計目的；關閉前先 `backup.sh create` 並跑 `drill`。
  刪除保護屬同一保護對象，已配置（未單獨實測，避免真刪默認分支）。
- **恢復卡**：`RECOVERY.md` 入庫——任何 clone（含從鏡像 clone 的）都帶着它。
  事故時倉庫內腳本可能已被刪光，該卡只列**純 git／系統命令**。
- **里程碑 tag**：每次「事故重建完成／大規模改動季結束／門禁與演練雙綠」打一個附註 tag
  （`git tag -a` 帶說明）。下次出事時，tag 是最便宜的「已知良好點」。
- 個人數據不入庫：`userdb/`、`/sync/`、`spool`、`add_debug.log`（含上屏原始字符＝擊鍵痕跡）、
  `default.custom.yaml` 等；`log/` 現已部分入庫（台賬與管道明細，見留痕規定）——
  該目錄整體仍忽略，僅四件白名單放行；
- YAML 縮進敏感：改嵌套層級內容後逐字覈對縮進（2026-11-04 punct 縮進事故教訓）。

## 防災（2026-11-05 成文，事故當日）

事故教訓：git 只管 12M／99 件，其餘 290M（72M 字表原件、台賬、審計報告）當時
**只有這一塊盤一份**。恢復能成，靠本地日誌與提交記錄互相印證——而日誌本身並無備份。

- **不可再生集**（丟了就是真丟）：`std_tables/download/`（一次性考據原件）、
  `dict_pipeline/log/`（台賬與明細）、`tmp/`（對賬報告與答辯）；
- **可重建集**：`corpus/sources/`（`BUILD.md` 釘版＋`SHA256SUMS`，淨室實測 29/29）、
  `cache/`（管道重算）、詞典與 lua 生成物（入庫，git 即歷史）；
- **快照**：`tools/backup.sh create` → `~/Backups/rime/rime-snapshot-*.tar.gz`
  （打包後自動校驗；目標盤餘量 <5G 告警；`--with-userdb` 另含個人學習庫，默認不含）
  （bundle 全史＋不可再生集＋逐件 sha256 ＋ `RESTORE.md`；默認約 29M，滾動保留 14 代）。
  **只含已提交狀態**——未提交改動不在快照內，故備份前先 commit；
- **異地**：另一臺設備是唯一異地點。已配好一條命令：

  ```sh
  cd lua/codelllc09/tools
  ./backup.sh offsite --fresh        # → yoga:D:/Backups/rime，自帶遠端雜湊校驗
  ```

  默認目標 `yoga:D:/Backups/rime`（可在參數裏覆蓋為 `host:目錄`）；
  兼容 Unix `sha256sum` 與 Windows `Get-FileHash` 兩種遠端。
  **每次大改之後、長期不動之前各跑一次**——這是唯一擋得住「盤壞／整機丟」的一層。
- **本地鏡像**：`backup.sh mirror-init` 另建裸庫，`post-commit` 自動追加只增不刪的快照 ref。
  它與工作區**同盤**，只防「命令毀庫」不防「盤壞」——兩者互補，缺一不可。
  見「歷史改寫防護」節；
- **演練**：`tools/backup.sh drill`＝造快照→校驗→還原到臨時目錄→復跑十二項門禁
  →驗鏡像可 clone→**雙 seed 復跑管道驗確定性**（僅工作區潔淨時執行，跑完 checkout 還原；
  全程約 30s）。
  演練未過即快照不足恃，須修復後重演。建議每月或每次改動管道後跑一次；
- `backup.sh verify|restore|list` 供校驗與還原；還原步驟亦隨快照附 `RESTORE.md`。

## 歷史改寫防護（2026-11-05 事故後成文）

**事故原型**（2026-11-05 20:32，本倉庫真事）：清洗隱私時用生成式清單餵 filter-repo——

```sh
ALLP=$(git log --all --name-only --format= | sort -u | grep -E '...')   # ← 當時返回空
git filter-repo --invert-paths --paths-from-file <(echo "$ALLP" | sed 's/^/literal:/')
```

空清單 → process substitution 產出**一行 `literal:`** → 空路徑匹配一切，配 `--invert-paths`
即把全部歷史與工作樹副本刪光。**根因不是 filter-repo，是「程序生成的輸入沒有先驗非空」。**

三層防線（按可靠性排序）：

1. **本地裸鏡像**——`backup.sh mirror-init`，之後 `post-commit` 鉤子自動把每個提交推成
   鏡像裏一個**新** ref `refs/snapshots/<時間>-<sha8>`，只增不刪。工作區倉庫裏的一切
   改寫都碰不到另一個目錄的裸庫，**這是唯一不依賴自律的一層**。
   絕不用 `push --mirror`（會連鏡像 ref 一起覆蓋，改寫清零後鏡像同步遭殃）。
   ⚠ 鏡像與工作區同盤，只防誤操作、不防盤壞——異地仍靠拷快照到另一臺。
   新克隆需一次性 `backup.sh mirror-init`；`check_consistency` ⑨ 電池檢查鏡像是否覆蓋 HEAD。
2. **改寫守衛**——**禁止直接運行 `filter-repo`／`filter-branch`／`reset --hard`／
   `gc --prune=now`／`branch -D` 等破壞性命令**，一律包一層：

   ```sh
   cd lua/codelllc09/tools
   ./safe-rewrite.sh --note "爲什麼要改寫" -- <原命令...>
   ```

   它做五件事：工作區須潔淨（或 `--allow-dirty`）→ 鏡像須覆蓋 HEAD → 另打時間戳 bundle
   → **校驗所有 `--paths-from-file` 參數**（空檔／空行／`literal:` 空模式／條目在歷史中
   全不存在，四類一律拒絕）→ 事後提交數或跟蹤文件數歸零即判定災難並打印還原命令。
3. **reflog 永不過期**——`gc.reflogExpire=never`、`gc.reflogExpireUnreachable=never`
   （本倉庫已設）。對 `reset --hard` 這類「對象仍在、只是不可達」的誤操作有效；
   對 filter-repo（改寫後自身會 gc）無效，故只是輔助層。

**恢復路徑**（**純 git，不依賴倉庫內任何腳本**——事故後工具本身已被刪光，
這是 2026-11-05 演練確認過的唯一可靠寫法）：

```sh
git clone ~/Backups/rime/mirror.git /tmp/recover        # origin/main ＝ 最後同步的狀態
git fetch origin '+refs/snapshots/*:refs/snapshots/*'   # 需要更早的點時再取
git -C /tmp/recover log --oneline --all | head
# 或在改寫守衛自動留的 bundle：
mkdir -p /tmp/recover && git clone ~/Backups/rime/pre-rewrite/pre-rewrite-*.bundle /tmp/recover
```

演練記錄（2026-11-05）：沙箱克隆中重放原樣事故命令 →
`0 提交／0 件／根目錄空` → 純 git 自鏡像恢復 → `27 提交／112 件`，恢復體十一項門禁全綠。


## 只讀第三方審查（變更季後例行，2026-11-05 成文）

- **觸發**：一輪變更同時觸及以下 ≥2 類——schema/lua、詞頻管道腳本、`.gitignore` 白名單、
  目錄遷移/改名、rulings 政策檔；或單類累計 ≥10 提交。瑣碎文檔提交不觸發；
- **審查方**：獨立會話，只讀，不向其透露已知問題；會寫盤的驗證優先在 `git worktree`
  副本中進行，須活庫語料者（如管道復跑）跑後 `git status` 必須潔淨；
- **斷言紀律（雙向）**：每項發現/答辯/反訴附「可復現命令＋實測輸出」；全稱斷言
  （已全部/清零/無/皆空）與否定式指控（不存在）必須附掃描模式與範圍；指稱須帶
  `檔:行` 或命令；未驗證者標「未驗證」——**禁止把計劃當完成、把推演當實測**；
- **反訴與撤回**：被審方可以證據反訴；指控不實者（含審查方）正式撤回並記入報告；
- **修復即重審**：處置提交逐項附落點 commit＋憑證，審查方復跑後方可閉環
  （修復本身引入新錯是實測高發項）；
- **答辯格式**：處置總表四欄（發現項→處置→落點 commit→實測憑證）＋勘誤段
  （認領不實項並寫根因）；「未驗證」是合法狀態，不得以完成時態掩蓋；
- **分級與閉環**：P0＝功能/克隆損壞、數據損失；P1＝事實錯誤、可復現性破壞；
  P2＝筆誤/風格。閉環＝P0/P1 憑證關閉，P2 可批次不阻塞；
- **前置機檢**：先跑 `lua/codelllc09/tools/check_consistency.py`（白名單電池/未來日期/
  校驗單對賬/HEAD 煙測/雙檢編排；`--determinism` 供管道改動後雙 seed 復跑）——
  機械事實走腳本，人審只受理語義矛盾；
- **取證時點**：審查報告**必須**標註取證 `HEAD` sha 與時間。缺此欄的報告，隨倉庫演進
  會在數小時內失真卻仍讀作現狀（2026-11-05 實例：一份審計的三條主張在加固提交後已失效，
  但因未標時點而無從察覺）。發現項若附 `檔:行`，行號亦以該 HEAD 爲準。
- **留痕**：審查報告與答辯落 `tmp/`，並記 `dict_pipeline/log/IGNORED_CHANGES.log` 台賬。
