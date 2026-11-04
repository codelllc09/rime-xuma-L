# sources/ 数据清洗与整理全过程（复现手册）

目标：**任何人（含未来的你）在本目录执行一条命令，即可从公网重建与本校验单
（`../SHA256SUMS`）逐字节一致的 `sources/`。**

```sh
uv run --with xlrd python build_sources.py        # 已存在的文件自动跳过；--force 全量重做
cd .. && sha256sum -c SHA256SUMS                  # 验收：29 条应全 OK
```

净室实测（2026-11-05）：空目录起建，29/29 件与快照逐字节一致。

## 一、下载源与版本钉住

| 源 | URL | 版本策略 |
|---|---|---|
| A jieba | raw.githubusercontent.com/fxsjy/jieba@master `/jieba/dict.txt` | master 快照（注意：在 `jieba/` 子目录，非仓库根） |
| B wordfreq | files.pythonhosted.org …wordfreq-3.1.1-py3-none-any.whl | 釘 3.1.1，只提取 `wordfreq/data/jieba_zh.txt` |
| C THUOCL | raw.githubusercontent.com/thunlp/THUOCL@master `/data/THUOCL_*.txt` ×11 + LICENSE | 静态快照 |
| D/G/H/I BCC | bcc.blcu.edu.cn `/api/datasets/<名>.txt/download` | API 下發 zip 內裹 txt，原樣 |
| E SUBTLEX | www.lexique.org/databases/SUBTLEX-CH/ `SUBTLEX-CH-WF.tsv` | 原樣字節 |
| K GoogleBooks | storage.googleapis.com/books/ngrams/books/20200217/chi_sim/1-00000-of-00001.gz | 版本化 URL，靜態 |
| L 語委 | github.com/bedlate/cn-corpus `现代汉语语料库词频表.xls` | 靜態 |
| _mapping OpenCC | raw.githubusercontent.com/BYVoid/OpenCC@提交 | **釘版**：TS×2@`0137a24b`（07-15 起）、ST×2@`3acca985`（08-19 起），經快照考古逐件核實 |
| _mapping Unihan | www.unicode.org/Public/**17.0.0**/ucd/Unihan.zip | **釘 17.0.0**（`latest` 會漂） |
| upstream | raw.githubusercontent.com/Ace-Who/rime-xuma@master `/schema/xuma.dict.yaml` | master 快照 |

滾動源（A/C/upstream）日後漂移屬預期：以 `SHA256SUMS` 爲本機快照憑據；
要嚴格復現舊快照，從備份整體回遷而非重下。

## 二、清洗与转换口径（全部在脚本内实现）

1. **A/B/D/E/upstream**：無轉換，下載字節即成品（B 從 wheel 內提取單文件；E 下載 .tsv 存爲 .txt，字節不變）。
2. **C.thuocl/THUOCL_diming.txt**：上游帶 UTF-8 BOM 與 CRLF——剝 BOM、
   CRLF→LF，**並剝除末行換行**（首建歷史口徑，爲對齊校驗單而復刻）。
   其餘 10 表 + LICENSE 原樣。
3. **D/G/H/I**：zip 解裹出唯一 txt，原樣（`token,count` 帶表頭 CSV）。
   D 源「現代」= multi_domain 與 news 兩文件**取 max 合併**——此合併是
   詞頻管道 `gen_freq_dict.py` 的職責，不在本腳本，故兩文件分別落盤。
4. **K**：官方 1-gram 聚合，口徑經逐字節對拍鎖定：
   - 詞取首個 `_` 前段；帶尾綴者**詞性段須純字母**，否則整行棄
     （實例：`經濟_` 空尾綴、`第十一條_.` 點尾綴——各棄 18852/159 次）；
   - 僅留 2–5 字純漢字詞（URO/擴A/兼容區/擴B+/擴C–G）；
   - 次數取「年,次,卷」三元組**中段**；同詞跨年份/詞性/重複行累加；
   - 按總次降序輸出 `詞\t次` TSV（93,771 詞）。
5. **L**：xls（sheet `wordfreq_50`）→ 取「詞語/出現次數」兩列，
   棄註釋/表頭/空行，行序照原表，輸出 `詞\t次`（14,629 行）。
6. **_mapping**：OpenCC 四件按釘版提交分別下載；Unihan.zip 解出兩文件。

## 三、佈局約定

- 文件名字母前綴 = 詞頻管道打分源代號（見 `gen_freq_dict.py` 權重表）：
  `('d','BCC現代',…,0.30)` ↔ `D.bcc_*.txt`；
- `_mapping/` 下劃線前綴 = 輔助映射，非語料；
- `upstream_xuma.dict.yaml` = 補詞差集基準，不參與打分，不挂字母；
- 本手冊與 `build_sources.py` 本身不參與重建對象（SHA256SUMS 不含它們）。

## 四、校准史（为何口径长这样）

2026-11-05 校準過程三處非顯然發現，留檔防退化：
1. K 的 `_`/`_.` 畸形尾綴必須棄——不棄則 `經濟` 多 18852、`第十一條` 多 159；
2. diming 末行換行剝除是首建時的意外產物，復刻以對齊校驗單
   （若他日重建校驗單，可改爲保留換行並同步更新 SHA256SUMS）；
3. OpenCC TS 與 ST 四件的最後變更提交不同，必須分別釘版；
   Unihan 必須用版本化 URL（17.0.0），`latest` 已漂。
