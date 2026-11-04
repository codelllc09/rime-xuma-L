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
- **留痕規定**：凡被 gitignore 的數據承載文件，改動同時必須在
  `dict_pipeline/log/IGNORED_CHANGES.log`（append-only 台賬）記一行
  `ISO時間	路徑	動作	說明`——git 之外的第二歷史手段。
  已接駁：diff_report.py（tmp/ 報告）、build_sources.py（語料區）、
  handpick_commit.sh（入庫事件一行索引；全量明細由調用方 add_word_proc.lua
  重定向至 add_commit.log——明細與台賬兩級，非重複）；
  手改 corpus/cache 等場景由 agent 手工補記。純派生緩存（cache/）免記。
- 個人數據不入庫：`userdb/`、`/sync/`、`spool`、`log/`、`default.custom.yaml` 等；
- YAML 縮進敏感：改嵌套層級內容後逐字覈對縮進（2026-11-04 punct 縮進事故教訓）。

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
- **留痕**：審查報告與答辯落 `tmp/`，並記 `dict_pipeline/log/IGNORED_CHANGES.log` 台賬。
