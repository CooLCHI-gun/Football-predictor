# HKJC 足球讓球研究與執行框架（MVP）

本專案是以 Windows + PowerShell 為主的 Python 研究流程，目標是把 Phase 3 到 Phase 6 串成可重跑、可驗證、可控風險的路線。

核心定位
- Phase 3 到 Phase 5：離線特徵、回測、優化（研究與驗證）。
- Phase 6：HKJC live 監測與 Telegram 研究提示（先 dry-run，再 live）。

重要規則
- 讓球結算只用全場 90 分鐘加傷停補時。
- 不可把加時賽與十二碼納入標籤、結算、回測或警示。

免責聲明（重要）
- 本專案僅供技術研究、資料工程與模型驗證用途，不構成任何投注或投資建議。
- 本專案與香港賽馬會（HKJC）沒有任何官方關聯、授權或背書。
- 使用者需自行確保其資料存取、API/網站使用方式與自動化行為符合當地法律、平台條款與監管要求。
- 專案輸出（含 Telegram 訊息）屬研究訊號，不保證準確性、完整性或盈利結果。
- 使用本專案而產生之任何損失、爭議或法律責任，由使用者自行承擔。

---

## 1. 安裝與環境（Windows / PowerShell）

Python 版本
- 建議固定使用 Python 3.11.9。

建立並啟用虛擬環境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
pip install -r requirements.txt
```

初始化資料庫

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main init-db
```

複製環境變數範本

```powershell
Copy-Item .env.example .env
```

---

## 2. 資料與特徵（Phase 3）

建議資料路徑
- NON_HKJC 全量特徵：data\processed\features_phase3_full.csv
- HKJC 回測特徵（若已建立）：data\processed\features_phase3_hkjc.csv

HKJC 歷史資料收集（重用 Phase 6 已驗證 GraphQL replay 方法）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main collect-hkjc-history `
  --start-date 2026-03-01 `
  --end-date 2026-03-31 `
  --raw-output-dir artifacts\hkjc_history\raw `
  --output-path data\raw\hkjc\historical_matches_hkjc.csv `
  --feature-output-path data\processed\features_phase3_hkjc.csv `
  --build-features `
  --force
```

輸出重點
- 原始資料：artifacts\hkjc_history\raw\match_results.csv、market_hdc_rows.csv、result_details.csv
- 正規化資料：data\raw\hkjc\historical_matches_hkjc.csv
- Phase 3 特徵：data\processed\features_phase3_hkjc.csv

限制說明
- 此流程重用 Phase 6 的 HKJC provider / GraphQL request shape，不使用新 scraping stack。
- 若某些日期區間未提供完整盤口時間序，會採目前可得的 close-only 或近似開收盤基線，並保留後續擴充點。

建立 full 特徵檔（NON_HKJC）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main build-features-full `
  --input-path data\raw\real\historical_matches_real_non_hkjc.csv `
  --output-path data\processed\features_phase3_full.csv `
  --force
```

若你已完成資料下載，也可直接使用既有 full 特徵檔進入回測與優化。

可配置特徵欄位清單（資料來源 + 缺失值策略）
- 預設配置檔：`config\feature_fields.json`
- 可透過 `FEATURE_FIELD_CONFIG_PATH` 或 CLI `--feature-field-config-path` 覆寫
- 可配置項目：欄位啟用順序（`active_fields`）、缺失值策略（`missing_strategy`）、欄位來源（`source`）
- 內建啟動前驗證：若 `active_fields` 欄位拼寫錯誤或 `missing_strategy` 非法，`build-features*` 會立即失敗（fail-fast）
- 已實作並可配置欄位：`fixture_density_7d_home`、`fixture_density_14d_away`、`line_drift_60m`

```powershell
python -m src.main build-features-full `
  --input-path data\raw\real\historical_matches_real_non_hkjc.csv `
  --output-path data\processed\features_phase3_full.csv `
  --feature-field-config-path config\feature_fields.json `
  --force
```

---

## 3. 回測與優化（Phase 4-5）

### 3.1 Canonical backtest（NON_HKJC full features）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\backtest `
  --run-id quick_check `
  --force
```

輸出位置
- artifacts\backtest\quick_check\predictions.csv
- artifacts\backtest\quick_check\trade_log.csv
- artifacts\backtest\quick_check\summary.csv

### 3.2 Canonical optimizer（coverage-balanced 窄網格 + cache）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_coverage_balance `
  --edge-grid 0.01,0.015,0.02 `
  --confidence-grid 0.0,0.05,0.10 `
  --max-alerts-grid 1,2 `
  --policy-grid fractional_kelly `
  --kelly-grid 0.15 `
  --max-stake-grid 0.01 `
  --daily-exposure-grid 0.03 `
  --max-runs 60 `
  --use-prediction-cache `
  --force
```

輸出位置
- artifacts\optimizer\opt_coverage_balance\params_results.csv
- artifacts\optimizer\opt_coverage_balance\best_params.json

多目標 optimizer 模板（ROI + Win rate + placed bets + drawdown）

```powershell
.\.venv\Scripts\Activate.ps1

$env:OPTIMIZER_LAMBDA_DRAWDOWN = "0.55"
$env:OPTIMIZER_LAMBDA_ROR = "0.60"
$env:OPTIMIZER_MU_CLV = "0.20"
$env:OPTIMIZER_MU_WIN_RATE = "0.35"
$env:OPTIMIZER_MU_PLACED_BETS = "0.25"
$env:OPTIMIZER_TARGET_PLACED_BETS = "120"
$env:OPTIMIZER_LAMBDA_LOW_BETS = "0.10"
$env:OPTIMIZER_MIN_BETS_TARGET = "60"

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_multi_objective `
  --edge-grid 0.01,0.015,0.02 `
  --confidence-grid 0.05,0.10,0.15 `
  --max-alerts-grid 1,2,3 `
  --policy-grid flat,fractional_kelly `
  --kelly-grid 0.10,0.15,0.20 `
  --max-stake-grid 0.008,0.01 `
  --daily-exposure-grid 0.02,0.03 `
  --max-runs 120 `
  --use-prediction-cache `
  --force
```

### 3.3 NON_HKJC baseline 解讀（目前共識）

coverage-balanced 參數（來自 NON_HKJC 歷史）
- min_edge_threshold 約 0.01
- min_confidence_threshold 約 0.05
- max_alerts = 1（映射 max_concurrent_bets）
- policy = fractional_kelly
- fractional_kelly_factor 約 0.15
- max_stake_pct 約 0.01
- daily_max_exposure_pct 約 0.03

目前觀察（研究用途）
- 約 174 bets
- ROI 約 3%
- max drawdown 約 10%
- CLV 欄位在這批 NON_HKJC 資料接近 0，現階段不具判讀力

重要聲明
- 此 preset 屬研究/觀察，不是已證實穩定優勢策略。
- 任何提高注碼或放寬門檻前，應先做 HKJC 專用回測與再次驗證。

### 3.4 HKJC-only 重新驗證（建議）

```powershell
.\.venv\Scripts\Activate.ps1

$env:BACKTEST_DATASET_SCOPE = "HKJC"
$env:MIN_EDGE_THRESHOLD = "0.01"
$env:MIN_CONFIDENCE_THRESHOLD = "0.05"
$env:MAX_CONCURRENT_BETS = "1"
$env:FRACTIONAL_KELLY_FACTOR = "0.15"
$env:BANKROLL_MAX_STAKE_PCT = "0.01"
$env:BANKROLL_DAILY_MAX_EXPOSURE_PCT = "0.03"

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_hkjc.csv `
  --output-dir artifacts\backtest `
  --run-id hkjc_coverage_balanced `
  --force
```

HKJC summary 分析（CLV + ROI）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main analyze-hkjc `
  --summary-csv-path artifacts\backtest\hkjc_coverage_balanced\summary.csv
```

---

## 4. Live Monitoring（Phase 6, HKJC）

### 4.1 Live 參數 preset（保守 / 平衡 / 進取）

建議先用「平衡」做 1-2 次 dry-run，觀察候選數與訊號品質，再決定是否切 live。

保守（訊號少、過濾較嚴）
- edge-threshold = 0.03
- confidence-threshold = 0.60
- max-alerts = 1

平衡（推薦，避免過度保守或過度進取）
- edge-threshold = 0.025
- confidence-threshold = 0.55
- max-alerts = 2

進取（訊號較多、波動較高）
- edge-threshold = 0.015
- confidence-threshold = 0.50
- max-alerts = 3

手動 Run workflow（Actions UI）建議填法
- Live provider: `hkjc`
- Edge threshold: `0.025`
- Confidence threshold: `0.55`
- Max alerts: `2`
- dry or live: 先 `dry`，穩定後再改 `live`

CLI（平衡 preset，建議起步）

```powershell
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_DRY_RUN = "true"

python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --dry-run `
  --edge-threshold 0.025 `
  --confidence-threshold 0.55 `
  --max-alerts 2 `
  --force
```

### 4.2 production-safe（小量 live）

- 建議先沿用平衡 preset（0.025 / 0.55 / 2）跑短期 live 觀察。
- 若你要更保守，再調回（0.03 / 0.60 / 1）。
- bankroll 建議維持：fractional Kelly 0.15、max stake 1%、daily exposure 3%

```powershell
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_DRY_RUN   = "false"
$env:TELEGRAM_BOT_TOKEN = "<your_bot_token>"
$env:TELEGRAM_CHAT_ID   = "<your_chat_id>"

python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --live `
  --edge-threshold 0.025 `
  --confidence-threshold 0.55 `
  --max-alerts 2 `
  --force
```

輸出檢查重點
- artifacts\live\live_snapshot.csv
- artifacts\live\live_candidates.csv
- artifacts\live\live_alert_preview.txt
- artifacts\live\live_alert_log.csv

重要提醒
- Phase 6 alert 是研究信號，不是投注指示。

### 4.3 validate-results（HKJC 結果對齊）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main validate-results `
  --start-date 2026-04-01 `
  --end-date 2026-04-03 `
  --output-path artifacts\live\results_validation.csv `
  --force
```

---

## 5. Security 與 Secrets

必須遵守
- Telegram token / chat id 只可來自環境變數、.env（本機）或 GitHub Secrets（雲端）。
- 不可硬編碼在程式碼、README、user.md 或 artifacts。
- .env.example 只保留 placeholder（例如 <your_bot_token>、<your_chat_id>）。
- 公開倉庫不可提交 .env、.har、含 token/header/cookies 的 debug 檔。

PowerShell 與 GitHub Actions 語法分流
- PowerShell：使用 $env:NAME = "value"
- GitHub Actions：使用 secrets 注入（只在 YAML）

for GitHub Actions only

```yaml
env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  TELEGRAM_DRY_RUN: ${{ secrets.TELEGRAM_DRY_RUN }}
```

## 5.1 GitHub Actions（取代 Railway）

### 一次部署後全自動（你要的模式）

目標
- 你只需完成一次 GitHub Actions + Secrets 設定，之後按排程自動跑：
  - 每日 backtest
  - 每日 optimize
  - 每 5 分鐘 live one-shot
  - 每日 pipeline one-shot（train + backtest + optimize + live）

一次性設定步驟
1. GitHub Repository 啟用 Actions，確認 default branch 為你正式部署分支。
2. 在 Settings -> Secrets and variables -> Actions 建立 Secrets：
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `TELEGRAM_DRY_RUN`（建議正式設 `false`；若設 `true` 只會 dry-run）
3. 到 bot 對話先送一次 `/start`，再執行 `telegram-debug` 驗證 chat id。
4. 手動各跑一次 workflow_dispatch 驗證成功：
   - `Scheduled Backtest`
   - `Scheduled Optimize`
   - `Scheduled Live One-Shot`
5. 驗證後就不需再手動觸發，schedule 會自動執行。

模式規則（部署後）
- `scheduled-live`：
  - `workflow_dispatch`：跟你 UI 選 dry/live。
  - `schedule`：
    - 有 `TELEGRAM_DRY_RUN` Secret：用該值。
    - 無 `TELEGRAM_DRY_RUN` 但 token/chat_id 存在：自動 live。
    - Telegram secrets 不齊：保持 dry-run。
- `pipeline-one-shot`（手動觸發）：跟你 UI 選 dry/live。

成功通知（新增）
- `scheduled-backtest`：成功後送回測摘要（summary.csv 指標）。
- `scheduled-optimize`：成功後送最佳參數摘要（best_params.json + params_results.csv）。
- `pipeline-one-shot`：成功後送三段摘要（backtest + optimize + live）。
- 所有 workflow 失敗時仍會送 fail 通知（含 run URL）。

避免重複排程
- 已選定：**`scheduled-backtest` + `scheduled-optimize` + `scheduled-live`** 各自獨立排程。
- `pipeline-one-shot` 只保留手動觸發（`workflow_dispatch`），不設 cron，避免與上述三個 workflow 重複跑 backtest/optimize。

本專案已提供六個工作流程（拆分後較易除錯）：
- `.github/workflows/ci.yml`：push / pull request 觸發，執行核心 smoke 與回測測試。
- `.github/workflows/scheduled-backtest.yml`：每日 backtest（可手動觸發）。
- `.github/workflows/scheduled-optimize.yml`：每日 optimizer（可手動觸發）。
- `.github/workflows/scheduled-live.yml`：每 5 分鐘 live one-shot（可手動觸發，支援 dry/live）。
- `.github/workflows/pipeline-one-shot.yml`：一條龍 one-shot（train xgboost + train lightgbm + backtest + optimize + live），目前為手動觸發（`workflow_dispatch`）。
- `.github/workflows/telegram-consistency-check.yml`：每日 Telegram 一致性檢查（可手動觸發；檢查 report 字段與 failure 模板 drift）。

Actions 頁面快速分辨（避免揀錯）
- 要每 5 分鐘監測 live：請揀 `Scheduled Live One-Shot`（workflow 檔：`.github/workflows/scheduled-live.yml`）。
- 要每日完整一條龍流程：請揀 `Pipeline One-Shot Daily`（workflow 檔：`.github/workflows/pipeline-one-shot.yml`）。
- 目前 `Pipeline One-Shot Daily` 預設無 cron 排程；如需每日一條龍，請再自行啟用 schedule。
- 先在左側 workflow 名稱確認，再入去看 run 詳情，可減少誤判排程頻率。

新增資料閘門
- 三個排程 workflow 都會先執行 `.github/scripts/validate_training_data.py`。
- 若真實資料已達門檻（預設 `min-real-rows=300`）仍混有 synthetic rows，流程會 fail-fast。
- 若真實資料未達門檻，允許暫時混用 synthetic rows，並顯示 warning。

新增 retrain 節流
- `pipeline-one-shot` 會比較 `FEATURE_PATH` 的 SHA256 與上次快取值。
- 只有「特徵資料已變更」或「模型檔不存在」時才觸發 retrain，避免無效重訓。

新增 optimizer 強化
- 支援 `OPTIMIZER_MODE=WINRATE_GUARDED`：優先提升 win rate 並限制回撤上限。
- 支援外層 rolling 檢驗（`OPTIMIZER_OUTER_ROLLING_WINDOWS`、`OPTIMIZER_OUTER_MIN_WINDOW_MATCHES`）。
- best params 新增最小成交硬約束（`OPTIMIZER_HARD_MIN_BETS`），避免低樣本誤選。

新增 live auto-tune
- `scheduled-live` 只會讀取「當天 run-id」：`artifacts/optimizer/daily_optimize_YYYYMMDD/best_params.json`。
- 不再掃描最新檔，避免誤用舊日 optimizer 結果。

新增 live 失敗保守 fallback
- 若 primary live one-shot 失敗，workflow 會自動重跑一次保守參數：
  - `edge-threshold=0.02`
  - `confidence-threshold=0.10`
  - `max-alerts=1`

XGBoost + LightGBM 模型政策
- live workflow 會確保 `xgboost` 與 `lightgbm` 兩個模型都已訓練（缺檔即補訓）。
- 目前運行權重政策：`xgboost=0.70`、`lightgbm=0.30`（用於部署策略記錄與治理，primary execution 仍以 xgboost bundle 發送）。

Telegram 訊息正確性
- 已加入名稱清洗（例如 `nan/null/none` 不會進入訊息）。
- 比賽/球隊顯示優先使用中文欄位，無值時安全回退映射，避免隊名錯置或占位字串。
- 訊息內容維持繁體中文格式。

Telegram 報告可讀性（避免純文字「好樣衰」）
- 已改為分段版型：`訊號標題 -> 建議操作 -> 模型觀點 -> 風險提示`，減少一大段資訊堆疊。
- 已加入訊號語氣標籤（例如 `🔥 強勢訊號`、`✅ 正向訊號`、`🟡 觀察訊號`），方便快速判讀優先次序。
- 核心數值（模型勝率 / 隱含機率 / Edge / 信心 / EV）仍完整保留，避免只「好睇」但失去研究可用性。

Telegram 發送排程規則
- **非 live（每日提示）**：每日中午 12:00 HKT 出一次（`--schedule-noon`）。
- **Live 監測**：每 5 分鐘一次（`live-loop --interval-seconds 300` 或 GitHub Actions `*/5 * * * *`）。

每日中午 12:00 自動出（本機）

```powershell
# 單次（等到 12:00 才發送）
$env:TELEGRAM_DRY_RUN = "true"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --schedule-noon

# 持續每日循環（每天 12:00 自動發）
$env:TELEGRAM_DRY_RUN = "false"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --schedule-noon `
  --repeat-daily

# 即刻測試（跳過等待）
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --schedule-noon `
  --skip-wait
```

Live 監測（每 5 分鐘）

```powershell
python -m src.main live-loop `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --interval-seconds 300 `
  --dry-run
```

報告語氣一鍵切換（`ALERT_TONE`）
- `ALERT_TONE=EXPRESSIVE`：高情緒版（預設）。顯示 `🔥/✅/🟡` 訊號標籤、分隔線與逐項 emoji。
- `ALERT_TONE=NEUTRAL`：專業版。保留相同數據，移除情緒標籤，版型較克制。

PowerShell 即時切換

```powershell
# 高情緒版（預設）
$env:ALERT_TONE = "EXPRESSIVE"

# 專業版
$env:ALERT_TONE = "NEUTRAL"
```

訊息樣例（Telegram）

EXPRESSIVE 版（預設）：
```text
🏆 賽事提示｜第1場・歐洲協會聯賽
🕐 開賽：2026-04-10 03:00 HKT
━━━━━━━━━━━━
🔥 強勢訊號
⚔️ 水晶宮 對 費倫天拿
💹 盤口：讓球客 -0.75｜賠率 1.91
━━━━━━━━━━━━
📌 建議操作
🎯 選邊：客 -0.75
💰 注碼策略：分數凱利
━━━━━━━━━━━━
📊 模型分析
🔹 模型勝率：75.33%
🔸 市場隱含：49.20%
📐 優勢（Edge）：+26.13%
🎖️ 信心指數：50.66%（中）
💎 期望值（EV）：43.8808
🏷️ 資料來源：香港賽馬會（模擬）
━━━━━━━━━━━━
⚠️ 僅供研究參考・不構成投注建議
```

NEUTRAL 版（設 `ALERT_TONE=NEUTRAL`）：
```text
📋 賽事研究提示｜第1場
🏟️ 歐洲協會聯賽｜🕐 2026-04-10 03:00 HKT
📍 水晶宮 對 費倫天拿
💹 盤口：讓球客 -0.75｜賠率 1.91

🎯 建議操作
• 選邊：客 -0.75
• 注碼策略：分數凱利

📊 模型觀點
• 模型勝率：75.33%
• 市場隱含：49.20%
• 優勢（Edge）：+26.13%
• 信心指數：50.66%（中）
• 期望值（EV）：43.8808
🏷️ 資料來源：香港賽馬會（模擬）
⚠️ 僅供研究參考・不構成投注建議
```

同樣會唔會定時自動出？會。
- 若使用 `.github/workflows/scheduled-live.yml`，排程為 `*/5 * * * *`（每 5 分鐘 one-shot 一次）。
- 每次排程 run 都會依門檻篩選候選賽事：有候選就發 Telegram（或 dry-run 預覽），無候選就不發 bet alert。
- 產物會持續寫入 `artifacts/live/`（例如 `live_candidates.csv`、`live_alert_log.csv`），方便追蹤與審計。

新增 workflow 失敗自動通知
- `ci`、`scheduled-backtest`、`scheduled-optimize`、`scheduled-live`、`pipeline-one-shot` 失敗時會自動送 Telegram 通知。
- 通知內容包含 workflow 名稱、branch、觸發者與 run URL，便於快速點回失敗頁面。
- 若未設定 `TELEGRAM_BOT_TOKEN` 或 `TELEGRAM_CHAT_ID`，通知步驟會自動略過，不影響原流程。

新增 backtest / optimizer 成功摘要通知
- `scheduled-backtest` 成功後會送出 summary.csv 重點（bets、win_rate、roi、max_drawdown）。
- `scheduled-optimize` 成功後會送出 best_params.json 重點（edge/confidence/max_alerts/policy、roi、win_rate、max_drawdown）。
- 若未設定 `TELEGRAM_BOT_TOKEN` 或 `TELEGRAM_CHAT_ID`，成功摘要通知會自動略過。

scheduled-live dry/live 模式說明（重要）
- `workflow_dispatch`：沿用你在 UI 選擇的 `dry` 或 `live`。
- `schedule`：
  - 若設有 `TELEGRAM_DRY_RUN` Secret，會以該值為準（`true`/`false`）。
  - 若未設 `TELEGRAM_DRY_RUN` 但已設好 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`，會自動以 live 模式運行。
  - 若 Telegram secrets 未設定，維持 dry-run。

建議做法
- 把 daily 與 live 任務改由 GitHub Actions 排程，不再依賴 Railway 常駐程序。
- 先用 `workflow_dispatch` 手動跑通，再啟用 schedule。

需要設定的 Secrets / Variables
- Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_DRY_RUN`

排程說明（UTC）
- `30 17 * * *`：01:30 HKT backtest
- `30 19 * * *`：03:30 HKT optimize
- `*/5 * * * *`：每 5 分鐘一次 live one-shot（schedule 會按上面 dry/live 模式規則自動決定）
- `40 16 * * *`：00:40 HKT Telegram 一致性檢查（report/failure template drift guard）

備註
- GitHub Actions 是「排程觸發一次就跑完退出」模型，不是 24 小時常駐單進程。
- 若要做到 24 小時監測效果，做法是提高 cron 頻率（例如每 5 或 15 分鐘）+ one-shot job。
- GitHub Actions 最短排程粒度是 5 分鐘，且可能有排程延遲，不保證秒級準時。
- 若要真實送出 Telegram，請把 `TELEGRAM_DRY_RUN` 設為 `false` 並確認金鑰配置。

Telegram 收不到訊息排查（快速）
1. 先到 Actions run log 搜 `scheduled-live mode: TELEGRAM_DRY_RUN=... LIVE_FLAG=...`。
2. 若 `TELEGRAM_DRY_RUN=true`，請把 `TELEGRAM_DRY_RUN` Secret 改為 `false`，或在 workflow_dispatch 選 `live`。
3. 確認 bot 先收到你在 Telegram 的 `/start`，再以 `python -m src.main telegram-debug --send-test-message` 驗證 chat id。
4. backtest/optimizer 只在 workflow 成功後發摘要；若 workflow fail，會走 failure 通知。
5. 若 CSV 有新賽事但仍無 alert，檢查 live run 產物：`artifacts/live/live_candidates.csv` 是否為 0 筆；0 筆代表門檻過濾後無候選，不會送 bet alert。
6. pipeline 成功通知會連發 3 則（backtest/optimize/live）；若只收到 fail 訊息，表示前序 step 已失敗。

---

## 6. 常用命令快速表

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main --help
python -m src.main build-features --help
python -m src.main build-features-full --help
python -m src.main download-real-data --help
python -m src.main train --help
python -m src.main predict --help
python -m src.main predict-full --help
python -m src.main backtest --help
python -m src.main optimize --help
python -m src.main analyze-hkjc --help
python -m src.main live-run-once --help
python -m src.main live-loop --help
python -m src.main validate-results --help
python -m src.main alert --help
```

---

## 6.1 每日推薦與分析 command 清單（GitHub Actions / 本機共用）

### A. 每日分析（Backtest + Optimizer + 分析報告）

1) 每日雙時段分析（本機手動）

```powershell
python -m src.main daily-maintenance `
  --timezone-name Asia/Hong_Kong `
  --backtest-time 01:30 `
  --optimize-time 03:30 `
  --backtest-input-path data\processed\features_phase3_full.csv `
  --optimize-input-path data\processed\features_phase3_full.csv `
  --backtest-output-dir artifacts\backtest `
  --optimize-output-dir artifacts\optimizer `
  --use-date-run-id `
  --use-prediction-cache `
  --max-runs 120 `
  --force
```

2) 針對某日 backtest summary 產生分析建議

```powershell
python -m src.main analyze-hkjc `
  --summary-csv-path artifacts\backtest\daily_backtest_20260404\summary.csv
```

### B. 每日推薦（Prediction + Alert）

1) 產生全量預測

```powershell
python -m src.main predict-full `
  --input-path data\processed\features_phase3_full.csv `
  --model-path artifacts\model_bundle.pkl `
  --output-path artifacts\predictions_full.csv `
  --force
```

2) 輸出每日推薦（Telegram dry-run / live）

```powershell
# dry-run（建議先用）—— 即時發送（不等 12 點）
$env:TELEGRAM_DRY_RUN = "true"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --edge-threshold 0.02 `
  --confidence-threshold 0.56 `
  --max-alerts 3
```

```powershell
# 每日中午 12:00 自動發送（正式模式）
$env:TELEGRAM_DRY_RUN = "false"
python -m src.main alert `
  --predictions-path artifacts\predictions_full.csv `
  --edge-threshold 0.02 `
  --confidence-threshold 0.56 `
  --max-alerts 3 `
  --schedule-noon `
  --repeat-daily
```

### C. 常見錯誤（`daily-maintenance` 退出碼 1）

- 只輸入 `python -m src.main daily-maintenance` 但已有舊檔案，可能被覆蓋保護擋住：加 `--force`。
- 輸入資料不存在：確認 `data\\processed\\features_phase3_full.csv` 存在。
- 時間格式錯誤：`--backtest-time` / `--optimize-time` 必須是 `HH:MM`。
- 想即刻測試流程：加 `--skip-wait`。

補充：每次 `train` 完成後，會自動輸出 proxy 特徵監控檔：
- `artifacts/debug/proxy_feature_monitor.json`
- `artifacts/debug/proxy_feature_monitor.csv`

欄位解讀重點：
- `importance_value` / `importance_rank`：來自 feature importance，數值越高代表模型越常使用該 proxy 特徵。
- `missing_rate`：缺失比例（`1.0` 代表全部缺失）。
- `drift_abs`：前半段與後半段樣本均值的絕對差。
- `drift_ratio`：`drift_abs / abs(first_half_mean)`，用於看相對漂移強度。
- `drift_cohen_d`：以前後半段 pooled std 計算的效果量，絕對值越大代表分布漂移越明顯。

實務上可先用以下規則做保留/降權候選檢查：
- 低重要度 + 高缺失（例如 `importance_value` 接近 0 且 `missing_rate` 長期 > 0.9）。
- 低重要度 + 高漂移（例如 `importance_value` 接近 0 且 `abs(drift_cohen_d)` 持續偏高）。

switch 審計檔內容重點
- `passed`：今日門檻是否通過
- `reasons`：失敗原因清單
- `pass_streak`：連續通過日數
- `switch_mode`：`MIXED` 或 `HKJC_ONLY`
- `auto_apply_effective`：今日是否已實際套用 HKJC 路徑

---

## 6.2 CSV 與 GraphQL 使用政策（檔案大小治理）

結論
- live 即時資料來源以 GraphQL 為主（HKJC provider）。
- CSV 主要是「落地快照 / 回測輸入 / 審計追蹤」，不是唯一資料源。
- push repo 不會自動上傳 artifacts；必須在 workflow 明確使用 `actions/upload-artifact@v4`。
- 現行 workflow 已統一設定 `retention-days: 90`，對齊長期留存治理。
- 留存制度文件：`config/data_retention_policy.yml`。

如果 repository 太大上不到 GitHub，建議：
- 不把大型輸出 CSV commit 到 repo（特別是 `artifacts/`、`data/raw/`、`data/processed/` 的大檔）。
- 只保留必要的 schema/sample，小樣本示範即可。
- 需要重現時由 command 重新產生，或改存 GitHub Actions artifact。
- 針對長期保存歷史，放外部儲存（例如 object storage），repo 只留索引與說明。

建議保留在 Git 的內容
- 程式碼、workflow、設定檔、測試。
- 小型範例資料（可在 CI 快速驗證）。

建議不要進 Git 的內容
- `artifacts/live/*.csv` 大量累積檔。
- 大型歷史原始資料與完整特徵檔。

---

## 7. 最短端到端流程（Phase 3 到 Phase 6）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\backtest `
  --run-id quick_check `
  --force

python -m src.main optimize `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\optimizer `
  --run-id opt_quick `
  --max-runs 30 `
  --use-prediction-cache `
  --force

$env:TELEGRAM_DRY_RUN = "true"
python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --dry-run `
  --edge-threshold 0.02 `
  --confidence-threshold 0.10 `
  --max-alerts 3 `
  --force
```
