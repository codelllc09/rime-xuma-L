# archive/ — 審裁層

詞頻管道的裁定與報告區，按**誰寫它**分兩個子目錄：

## rulings/ — 人寫裁定（管道輸入，長期勿失，入庫）

人工審定的裁定檔。rejected/reviewed/reviewed_done 由 `gen_freq_dict.py` 補詞階段直接讀取；驗收基準由 `check_acceptance.py` 讀、用詞傾向由 `check_tendency.py` 讀：

| 文件 | 作用 |
|---|---|
| `rejected.txt` | 否決閘：表中詞形永久禁止自動補入（防復活；繁形對 A 閘生效） |
| `用詞傾向.txt` | 字形傾向（偏好/禁族/例外三種行，check_tendency.py 校驗） |
| `reviewed_done.txt` | 已定案：不再列入 TO_BE_REVIEWED（避免舊案回鍋） |
| `reviewed.txt` | 已放行：直接按碼入表（竈臺/灶臺/灰巖等） |
| `驗收基準.txt` | 回歸基線：44 碼首選斷言（check_acceptance.py 校驗；2026-11 自 9 月驗收快照機化） |

修改裁定 → 重跑 `gen_freq_dict.py` 生效。新文件入此目錄需在
根 `.gitignore` 白名單具名追加（防呆設計）。

## 補充：第三條裁定通道——次序覆蓋

同碼組內的候選次序裁定不在此處，走 `../manual_overrides.txt`
（`dict_pipeline/` 根，同樣人寫、重跑生效）：
兩詞法「A > B」換頻分、名次法「A > B > C」按名次重排分數。
語料更新後若自然滿足則自動退役（日誌印「已滿足次序」）。
三通道歸納：**補詞裁定（rulings/）+ 次序裁定（manual_overrides）+ 手收詞（handpicked.txt）**。

## reports/ — 管道輸出（每輪重寫，可再生；三件已收編入庫隨提交留審計）

| 文件 | 作用 |
|---|---|
| `TO_BE_REVIEWED.txt` | 人工審核隊列（唯一需人親自批注的文件，全大寫命名規約） |
| `addon_review.txt` | 補詞審查輔助表（歧義詞帶頻分明細） |
| `freq_report.md` | 詞頻重排報告 |

**手批流程**：批注 `reports/TO_BE_REVIEWED.txt`（行首 ? 改 + 或 -）→
`python3 lua/codelllc09/tools/transcribe_review.py` 自動轉抄三清單並自驗（放行繁形入 reviewed、
否決雙形入 rejected、定案簡形入 reviewed_done）→ 重跑管道。
