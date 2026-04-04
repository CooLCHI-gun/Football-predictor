# Football-predictor 使用指引（Windows PowerShell）

此文件是給新使用者的實作流程，目標是用最短路徑完成：
- 離線研究（Phase 3 到 Phase 5）
- HKJC live 研究監測（Phase 6）

適用環境
- Windows PowerShell
- Python 3.11.9
- 專案根目錄虛擬環境：.venv

---

## Part 1. Setup

### 1) 建立與啟用 Python 3.11.9 虛擬環境

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2) 初始化設定檔與資料庫

```powershell
Copy-Item .env.example .env
python -m src.main init-db
```

---

## Part 2. Offline Workflow（Phase 3-5）

### 0) 先建立 HKJC 歷史資料與特徵（可選，但建議）

此流程會重用 Phase 6 已驗證的 HKJC provider / GraphQL replay 方法，輸出 HKJC 專用歷史資料與特徵檔。

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
- artifacts\hkjc_history\raw\match_results.csv
- artifacts\hkjc_history\raw\market_hdc_rows.csv
- artifacts\hkjc_history\raw\result_details.csv
- data\raw\hkjc\historical_matches_hkjc.csv
- data\processed\features_phase3_hkjc.csv

### 1) 建立 full 特徵檔（NON_HKJC）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main build-features-full `
  --input-path data\raw\real\historical_matches_real_non_hkjc.csv `
  --output-path data\processed\features_phase3_full.csv `
  --force
```

### 2) 執行 backtest

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main backtest `
  --input-csv-path data\processed\features_phase3_full.csv `
  --output-dir artifacts\backtest `
  --run-id quick_check `
  --force
```

### 3) 執行 optimizer（coverage-balanced 窄網格 + cache）

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

### 4) 讀取輸出結果

重點檔案
- 回測摘要：artifacts\backtest\quick_check\summary.csv
- 交易明細：artifacts\backtest\quick_check\trade_log.csv
- 優化結果：artifacts\optimizer\opt_coverage_balance\params_results.csv
- 最佳參數：artifacts\optimizer\opt_coverage_balance\best_params.json

快速檢查

```powershell
Get-Content artifacts\backtest\quick_check\summary.csv -TotalCount 20
Get-Content artifacts\optimizer\opt_coverage_balance\params_results.csv -TotalCount 20
```

### 5) NON_HKJC baseline 的現階段解讀

coverage-balanced 參數
- min_edge_threshold 約 0.01
- min_confidence_threshold 約 0.05
- max_alerts = 1
- policy = fractional_kelly
- fractional_kelly_factor 約 0.15
- max_stake_pct 約 0.01
- daily_max_exposure_pct 約 0.03

研究觀察
- 約 174 bets
- ROI 約 3%
- max drawdown 約 10%
- CLV 欄位目前多為 0（此 NON_HKJC 資料集暫不具 CLV 判讀力）

結論
- 這是研究基線，不是已證實可持續 edge。

---

## Part 3. Live HKJC Workflow（Phase 6）

### 1) 先做 dry-run（建議預設）

```powershell
.\.venv\Scripts\Activate.ps1
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

### 2) 檢查 live artifacts

- artifacts\live\live_snapshot.csv
- artifacts\live\live_candidates.csv
- artifacts\live\live_alert_preview.txt
- artifacts\live\live_alert_log.csv

### 3) 準備切換 live（production-safe）

建議先維持小量
- edge-threshold 約 0.02
- confidence-threshold 約 0.10 到 0.12
- max-alerts = 1
- policy = fractional_kelly
- fractional_kelly_factor 約 0.15
- max_stake_pct 約 0.01
- daily_max_exposure_pct 約 0.03

```powershell
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_DRY_RUN   = "false"
$env:TELEGRAM_BOT_TOKEN = "<your_bot_token>"
$env:TELEGRAM_CHAT_ID   = "<your_chat_id>"

python -m src.main live-run-once `
  --provider hkjc `
  --model-path artifacts\model_bundle.pkl `
  --live `
  --edge-threshold 0.02 `
  --confidence-threshold 0.10 `
  --max-alerts 1 `
  --force
```

### 4) HKJC 結果對齊驗證（可選但建議）

```powershell
.\.venv\Scripts\Activate.ps1

python -m src.main validate-results `
  --start-date 2026-04-01 `
  --end-date 2026-04-03 `
  --output-path artifacts\live\results_validation.csv `
  --force
```

---

## Part 4. Safety Checklist

Secrets 規則
- Telegram token/chat id 只可用環境變數、.env 或 GitHub Secrets。
- 不可寫入 README、user.md、程式碼、artifacts、截圖或 log。
- .env.example 只能保留 placeholder。

操作規則
- 一律先 dry-run，再切 live。
- 早期 live 測試把 max-alerts 維持 1。
- 未有 HKJC 專用回測 + CLV 穩定證據前，不要擴大 stake。
- Phase 6 alert 是研究信號，不是投注指示。

GitHub Actions only（不要貼到 PowerShell）

```yaml
env:
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
  TELEGRAM_DRY_RUN: ${{ secrets.TELEGRAM_DRY_RUN }}
```

---

## Quick Reference

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main --help
python -m src.main backtest --help
python -m src.main optimize --help
python -m src.main live-run-once --help
python -m src.main validate-results --help
python -m src.main analyze-hkjc --help
```
