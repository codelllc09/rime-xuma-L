# 字表與古籍下載存檔

本目錄只保存下載與整理後的參考材料，**尚未接入**上層 `std_tables/` 的字表生成流程。整理原則是：先保存可複製、可檢索的文字或字表；公開文字版不完整時，再補公版影印本供校勘。

## 內容與可用性

| 目錄 | 內容 | 可複製性／完整度 |
| --- | --- | --- |
| `mainland/` | 1955《第一批異體字整理表》、GB/Z 40637—2021 | GB/Z 已另製作 14,250 字的一行一字 TXT、序號／Unicode TSV、JSON；另附 XLSX 與 PDF 原表 |
| `hong_kong/` | 香港《常用字字形表》及異體字表 | 有 4,759／4,762 字的 TXT 整理，另附官方或公版 PDF |
| `taiwan_moe_variants/` | 《教育部異體字索引字典》離線索引及正字—異體字表 | 從 98,023 條索引機械抽出 66,232 筆對應；其中 52,762 筆字形可複製，提供 TXT／TSV／JSON，不含釋義全文 |
| `hanyu_dazidian/` | Unicode Unihan 的《漢語大字典》位置索引 | TSV 共 55,812 條 `kIRGHanyuDaZidian` 字頭—卷頁記錄；不是字典全文 |
| `songyuan_suzipu_1930/` | 《宋元以來俗字譜》 | 維基文庫全書可複製表格（Markdown、HTML、EPUB、原始維基文字）；罕見未編碼字可能以組字描述或圖片表示 |
| `zihui_1679/` | 《字彙》康熙十八年刻本 | 識典古籍全文 OCR：18 篇、2,597 頁、10,973 段；另附全 14 冊公版影印本 |
| `zhengzitong_kangxi/` | 《正字通》康熙刻本 | `copyable_internet_archive/` 是北京大學圖書館藏本全 13 冊：每冊有可搜尋 PDF、OCR TXT，並已合併全文；`copyable_shidian/` 的校訂文字只公開卷首及亥集下（378 頁、753 段）；另保留國圖本前 8 冊高清影像供校勘 |
| `source_notes/` | 第三方機讀字表的上游說明 | 用於追溯版本、欄位及授權 |
| `SHA256SUMS.json` | 各檔案大小與 SHA-256 | 用於完整性校驗 |

## 使用提醒

- OCR、維基錄文與第三方機讀表都可能有辨識、轉碼或人工錄入錯誤；正式採字前應與所附 PDF 或原站影像逐字校核。
- 臺灣教育部《異體字字典》正式網站標示版權所有，未見獲授權的完整離線機讀包，因此本目錄不批量抓取其釋義、圖片及全部頁面，只保存有明確非商業授權的字頭索引。
- 臺灣字表的 52,762 筆可複製對應中含 29,982 筆私用區（PUA）異體字，需要全宋體等配套字型；另有 13,470 筆仍是圖片字號佔位，已保留在「全部」版而沒有假造字符。
- GB/Z 40637—2021 是 14,250 個規範字的字形表，本身沒有「正字—異體」對應欄；生成檔只列序號、規範字及 Unicode。
- 《漢語大字典》本體仍受版權保護；本目錄只保存 Unicode 公開的定位索引，不含釋義、字形圖或字典掃描件。
- 識典古籍資料適合個人研究與檢索；若要再發布 OCR 整理成果，請先核對平台當時的服務與授權條款。
- Internet Archive 的《正字通》頁面說明原書有字跡不清與殘頁；其 OCR 是自動辨識衍生檔，搜尋便利但錯字較多，應以 PDF 影像為準。

## 主要來源

- 《宋元以來俗字譜》可複製本：https://zh.wikisource.org/zh-hant/宋元以來俗字譜
- 《字彙》識典古籍：https://www.shidianguji.com/book/JINANLIB03572
- 《正字通》識典古籍：https://www.shidianguji.com/book/7414832345078824996
- 《正字通》北京大學圖書館藏本（13 冊入口之一）：https://archive.org/details/02076813.cn
- 《字彙》《正字通》公版影印本：https://commons.wikimedia.org/
- 臺灣教育部《異體字字典》：https://dict.variants.moe.edu.tw/
- 可下載的教育部異體字字頭索引：https://github.com/lxs602/Chinese-Mandarin-Dictionaries
- WFG《教育部異體字索引字典》說明：https://fgwang.blogspot.com/2021/12/blog-post_29.html
- CJKVI 教育部異體字可編輯上游字表：https://github.com/cjkvi/cjkvi-dict/blob/master/twedu-dict.txt
- 《第一批異體字整理表》公版影印本：https://commons.wikimedia.org/wiki/File:第一批异体字整理表.pdf
- 《第一批異體字整理表》電子文本：https://www5b.biglobe.ne.jp/~harigaya/yitizi/index.html
- GB/Z 40637—2021 官方資訊：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=52E2DE28D439C1937EE09AE4B5AA615B
- GB/Z 40637—2021 開源字符集：https://github.com/zispace/hanzi-chars
- 香港教育局中國語文教育資源：https://www.edb.gov.hk/tc/curriculum-development/kla/chi-edu/resources/primary/lang/curriculum-materials.html
- Unicode Unihan 17.0：https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip
