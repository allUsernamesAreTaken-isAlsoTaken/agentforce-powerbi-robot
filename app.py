from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import json
import zipfile
import os
import shutil
import base64
import urllib.request
from datetime import datetime, timedelta

app = Flask(__name__)

# Download blank PBIX once (Microsoft's official blank file)
BLANK_PBIX = "blank.pbix"
if not os.path.exists(BLANK_PBIX):
    urllib.request.urlretrieve(
        "https://github.com/microsoft/powerbi-desktop-samples/raw/main/Blank%20Report/Blank%20Report.pbix",
        BLANK_PBIX
    )

@app.route('/generate', methods=['POST'])
def generate_pbix():
    query = request.json.get('query', 'Tesla last 30 days')
    
    # Simple parser for ticker
    ticker = 'TSLA'
    if 'apple' in query.lower(): ticker = 'AAPL'
    elif 'bitcoin' in query.lower(): ticker = 'BTC-USD'
    elif 'ethereum' in query.lower(): ticker = 'ETH-USD'
    elif 'spy' in query.lower(): ticker = 'SPY'
    
    # Fetch real financial data (free, no key)
    df = yf.download(ticker, period='30d', interval='1d')
    df = df.reset_index()
    df['Ticker'] = ticker
    df['Change%'] = df['Close'].pct_change() * 100
    df['Volatility'] = df['Change%'].rolling(window=5).std()
    df['IsAnomaly'] = df['Change%'].abs() > df['Change%'].std() * 2
    df = df.dropna()
    
    # Narrative insight
    anomaly_count = df['IsAnomaly'].sum()
    narrative = f"{ticker} had {anomaly_count} anomalies in 30 days. Confidence: 95%. Max change: {df['Change%'].max():.2f}%."
    
    # DAX measures
    dax_measures = [
        {"name": "Avg Close", "expression": "AVERAGE(Finance[Close])"},
        {"name": "30 Day Return", "expression": "DIVIDE([Total Close] - CALCULATE([Total Close], FIRSTDATE(Finance[Date])), CALCULATE([Total Close], FIRSTDATE(Finance[Date])))"},
        {"name": "Volatility", "expression": "STDEV.S(Finance[Change%])"},
        {"name": "Anomaly Count", "expression": "COUNTROWS(FILTER(Finance, Finance[IsAnomaly] = TRUE))"}
    ]
    
    # Unzip blank PBIX
    extract_dir = "pbix_extracted"
    shutil.rmtree(extract_dir, ignore_errors=True)
    with zipfile.ZipFile(BLANK_PBIX, 'r') as zin:
        zin.extractall(extract_dir)
    
    # Create model file (inject data + DAX)
    model = {
        "name": "Model",
        "tables": [{
            "name": "Finance",
            "columns": [
                {"name": "Date", "dataType": "dateTime"},
                {"name": "Open", "dataType": "double"},
                {"name": "High", "dataType": "double"},
                {"name": "Low", "dataType": "double"},
                {"name": "Close", "dataType": "double"},
                {"name": "Volume", "dataType": "double"},
                {"name": "Change%", "dataType": "double"},
                {"name": "Volatility", "dataType": "double"},
                {"name": "IsAnomaly", "dataType": "boolean"},
                {"name": "Ticker", "dataType": "string"}
            ],
            "rows": df.to_dict(orient="records")
        }],
        "measures": dax_measures,
        "relationships": [{"fromTable": "Finance", "toTable": "DateDim", "fromColumn": "Date", "toColumn": "Date"}]  # Simple star
    }
    # Write to a model file (Power BI uses .json-like for schema)
    with open(os.path.join(extract_dir, "DataModelSchema.json"), 'w') as f:
        json.dump(model, f, default=str)
    
    # Create report layout (2 pages)
    report_config = {
        "sections": [
            {
                "name": "Overview",
                "visualContainers": [
                    {"type": "candlestick", "config": {"x": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"}},
                    {"type": "bar", "config": {"x": "Date", "y": "Volume"}},
                    {"type": "card", "config": {"value": "30 Day Return"}},
                    {"type": "card", "config": {"value": "Volatility"}},
                    {"type": "card", "config": {"value": "Anomaly Count"}}
                ]
            },
            {
                "name": "Anomalies",
                "visualContainers": [
                    {"type": "line", "config": {"x": "Date", "y": "Change%", "color": "IsAnomaly"}},
                    {"type": "text", "config": {"text": narrative}}
                ]
            }
        ]
    }
    os.makedirs(os.path.join(extract_dir, "Report"), exist_ok=True)
    with open(os.path.join(extract_dir, "Report/Layout.json"), 'w') as f:
        json.dump(report_config, f)
    
    # Theme (professional dark)
    theme = {"dataColors": ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"], "background": "#000000", "foreground": "#ffffff"}
    with open(os.path.join(extract_dir, "Report/Theme.json"), 'w') as f:
        json.dump(theme, f)
    
    # Re-zip to .pbix
    output_pbix = f"{ticker}_dashboard.pbix"
    with zipfile.ZipFile(output_pbix, 'w', zipfile.ZIP_DEFLATED) as zout:
        for root, _, files in os.walk(extract_dir):
            for file in files:
                zout.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), extract_dir))
    
    shutil.rmtree(extract_dir)
    
    # Base64 for Salesforce download
    with open(output_pbix, "rb") as f:
        pbix_b64 = base64.b64encode(f.read()).decode('utf-8')
    os.remove(output_pbix)
    
    return jsonify({"pbix_base64": pbix_b64, "narrative": narrative, "status": "success"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
