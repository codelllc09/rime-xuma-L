# corpus/ 語料依賴——可復現途徑

> 2026-11-05 起全部詞源（目錄與散件）收攏於 `sources/` 子目錄；下表路徑省略該前綴。

流水線的語料層。全部缺失時進入**無語料模式**（詞頻休眠、禁寫盤、護欄診斷照跑）。
補齊後重跑 `gen_freq_dict.py` 即恢復。

| 文件 | 來源 | 說明 |
|---|---|---|
| `A.jieba_dict.txt`（源 a·0.27） | https://github.com/fxsjy/jieba → `dict.txt` | 結巴默認詞表（新聞語料），master 快照（滾動更新） |
| `B.wordfreq_jieba_zh.txt`（源 b·0.11） | `pip download wordfreq==3.1.1 --no-deps` 解包提取 `data/jieba_zh.txt` | 源 B；whl 其餘部分（外語詞頻/包代碼）不取 |
| `_mapping/TSCharacters.txt` / `TSPhrases.txt` | https://github.com/BYVoid/OpenCC → `data/dictionary/` | 繁→簡（查頻用） |
| `_mapping/STCharacters.txt` / `STPhrases.txt` | 同上 | 簡→繁（補詞用） |
| `_mapping/Unihan_Variants.txt` | https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip 解壓 | 異體歸一（kSemanticVariant 等） |
| `_mapping/Unihan_Readings.txt` | 同上 | kMandarin（gen_pinyin_tone.py 用） |
| `C.thuocl/`（源 c·0.09） | https://github.com/thunlp/THUOCL → `data/`（歷史抓取走 jsDelivr 鏡像；復現走 raw.githubusercontent，見 sources/BUILD.md） | 清華開放中文詞庫 11 分類（IT/財經/成語/地名/歷史名人/醫學/飲食/法律/汽車/動物/詩詞），MIT；DF 檔頻口徑，2016-17 快照；diming 原始爲 BOM+CR 換行，已規範化爲 UTF-8 無 BOM+LF |
| `D/G/H/I.bcc_*.txt`（源 d·0.30＝max(multi_domain,news)；g 文學·0.03；h 口語·0.02；i 近代·0.02）；下載原包用後即棄（復現見 `sources/BUILD.md`） | https://bcc.blcu.edu.cn → `api/datasets/<名>.txt/download`（zip 內裹 txt） | 北語 BCC 2.0 詞頻表（2026-05 更新，62 億字語料）：multi_domain 44.2 萬詞（新聞+文學+口語+科技等量平衡）＋news 78 萬＋literature 16.6 萬＋dialogue 13.6 萬＋modern_chinese(近代) 137.8 萬；`token,count` CSV 口徑（classical 詞表實爲字表複製品，已刪） |
| `E.subtlex/`（源 e·0.10） | https://www.lexique.org/databases/SUBTLEX-CH/ | SUBTLEX-CH 詞頻（Cai & Brysbaert 2010，港臺影片字幕，含 W/million 與 CD）；**GB18030 編碼**，讀取需轉碼 |
| `L.yuwei_word_freq.txt`（源 l·0.005） | https://github.com/bedlate/cn-corpus → `現代漢語語料庫詞頻表.xls` | 國家語委現代漢語語料庫詞頻（2000 萬字抽樣，>50 次才收 1.46 萬詞）；已接管道（源 l·0.005） |
| `K.googlebooks/`（源 k·0.05） | https://storage.googleapis.com/books/ngrams/books/20200217/chi_sim/1-00000-of-00001.gz（CC-BY 3.0） | Google Books chi_sim 1-gram（書籍域，至 2019）；原始 58MB 逐年計數（原包用後即棄），已聚合爲 `chi_1gram_word_freq.txt`（2-5 字純漢字詞 93,771，POS 後綴已剝離、年份求和） |
| `upstream_xuma.dict.yaml`（補詞源 A 差集基準，非打分源） | https://github.com/Ace-Who/rime-xuma → 主表 | 上游碼表快照（A 源差集） |

## 版本釘住與可複現性

- 語料是**頻分結果**的決定因素之一。上游滾動更新（jieba master、Unihan latest）會造成詞序微漂。
- 嚴格復現：備份本目錄整體遷移（60M），或按下列校驗和覈對：

```sh
cd sources && sha256sum A.jieba_dict.txt B.wordfreq_jieba_zh.txt \
          _mapping/TSCharacters.txt _mapping/TSPhrases.txt _mapping/STCharacters.txt _mapping/STPhrases.txt \
          _mapping/Unihan_Variants.txt _mapping/Unihan_Readings.txt upstream_xuma.dict.yaml
```

（校驗和記錄：見本目錄 `SHA256SUMS`，與文件同時備份）
