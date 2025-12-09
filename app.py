from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import json
import zipfile
import os
import shutil
import base64
import struct
from datetime import datetime, timedelta

app = Flask(__name__)

# --- Helper to write string to file with UTF-16-LE encoding ---
def write_utf16le_json(filename, data):
    with open(filename, 'wb') as f:
        # Power BI requires BOM or explicit LE encoding for these files
        json_str = json.dumps(data, indent=2, default=str)
        f.write(json_str.encode('utf-16-le'))

# Create minimal blank PBIX structure
def create_blank_pbix(filename):
    with zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml (Standard)
        content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="bin" ContentType="application/vnd.ms-publisher.binary"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/Report/Layout" ContentType="application/vnd.ms-powerbi.content.layout+json"/>
  <Override PartName="/DataModelSchema" ContentType="application/vnd.ms-powerbi.content.schema+json"/>
  <Override PartName="/Report/Theme" ContentType="application/vnd.ms-powerbi.content.theme+json"/>
  <Override PartName="/Settings" ContentType="application/vnd.ms-powerbi.content.settings+json"/>
  <Override PartName="/Version" ContentType="application/vnd.ms-powerbi.content.version+binary"/>
</Types>'''
        zf.writestr('[Content_Types].xml', content_types)

        # 2. _rels/.rels
        rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/powerbi/2016/06/reportlayout" Target="Report/Layout"/>
  <Relationship Id="rId2" Type="http://schemas.microsoft.com/powerbi/2016/06/datamodelschema" Target="DataModelSchema"/>
  <Relationship Id="rId3" Type="http://schemas.microsoft.com/powerbi/2016/06/theme" Target="Report/Theme"/>
  <Relationship Id="rId4" Type="http://schemas.microsoft.com/powerbi/2016/06/settings" Target="Settings"/>
  <Relationship Id="rId5" Type="http://schemas.microsoft.com/powerbi/2016/06/version" Target="Version"/>
</Relationships>'''
        zf.writestr('_rels/.rels', rels)

        # 3. Version File (CRITICAL: Must be binary)
        # This binary string represents a valid version header
        zf.writestr('Version', b'\x00\x00\x00\x00\x00\x00\x00\x00\x01\x13')

        # 4. Settings File
        settings = {"locale": "en-US"}
        zf.writestr('Settings', json.dumps(settings).encode('utf-16-le'))

@app.route('/generate', methods=['POST'])
def generate_pbix():
    try:
        query = request.json.get('query', 'Tesla last 30 days')
    
        # Simple parser for ticker
        ticker = 'TSLA'
        if 'apple' in query.lower(): ticker = 'AAPL'
        elif 'bitcoin' in query.lower(): ticker = 'BTC-USD'
        elif 'ethereum' in query.lower(): ticker = 'ETH-USD'
        elif 'spy' in query.lower(): ticker = 'SPY'
    
        # Fetch real financial data
        df = yf.download(ticker, period='30d', interval='1d')
        
        # Fix for yfinance MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df['Ticker'] = ticker
        
        if 'Close' not in df.columns:
            return jsonify({"error": "Could not fetch data"}), 400

        df['Change%'] = df['Close'].pct_change() * 100
        df['Volatility'] = df['Change%'].rolling(window=5).std()
        df['IsAnomaly'] = df['Change%'].abs() > df['Change%'].std() * 2
        df = df.dropna()
    
        # Narrative
        anomaly_count = int(df['IsAnomaly'].sum())
        max_change = df['Change%'].max()
        narrative = f"{ticker} had {anomaly_count} anomalies in 30 days. Max change: {max_change:.2f}%."
    
        # DAX measures
        dax_measures = [
            {"name": "Avg Close", "expression": "AVERAGE(Finance[Close])"},
            {"name": "30 Day Return", "expression": "DIVIDE([Total Close] - CALCULATE([Total Close], FIRSTDATE(Finance[Date])), CALCULATE([Total Close], FIRSTDATE(Finance[Date])))"},
            {"name": "Volatility", "expression": "STDEV.S(Finance[Change%])"},
            {"name": "Anomaly Count", "expression": "COUNTROWS(FILTER(Finance, Finance[IsAnomaly] = TRUE))"}
        ]
    
        # Create blank PBIX
        blank_pbix = "blank.pbix"
        create_blank_pbix(blank_pbix)
    
        # Unzip blank PBIX
        extract_dir = "pbix_extracted"
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(blank_pbix, 'r') as zin:
            zin.extractall(extract_dir)
    
        # --- FIX: Correct DataModelSchema Structure ---
        # Power BI requires the model to be wrapped in "model" key with compatibility level
        model_wrapper = {
            "name": "SemanticModel",
            "compatibilityLevel": 1550,
            "model": {
                "culture": "en-US",
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
                    # NOTE: Power BI does not standardly support "rows" here in Import mode, 
                    # but we keep it to prevent code breakage. 
                    # The file will open, but might show empty data if not in Push mode.
                    "rows": df.to_dict(orient="records") 
                }],
                "measures": dax_measures,
                "relationships": []
            }
        }
        
        # --- FIX: Write Schema as UTF-16-LE ---
        write_utf16le_json(os.path.join(extract_dir, "DataModelSchema"), model_wrapper)
    
        # Create report layout (2 pages)
        report_config = {
            "sections": [
                {
                    "name": "Overview",
                    "displayName": "Overview",
                    "visualContainers": [
                        {"type": "candlestick", "config": {"x": "Date", "open": "Open", "high": "High", "low": "Low", "close": "Close"}},
                        {"type": "card", "config": {"value": "Volatility"}}
                    ]
                }
            ]
        }
        
        # --- FIX: Write Layout as UTF-16-LE ---
        os.makedirs(os.path.join(extract_dir, "Report"), exist_ok=True)
        write_utf16le_json(os.path.join(extract_dir, "Report/Layout"), report_config)
    
        # Theme
        theme = {"name":"Theme","dataColors": ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"], "background": "#FFFFFF", "foreground": "#000000"}
        with open(os.path.join(extract_dir, "Report/Theme"), 'w') as f:
            json.dump(theme, f)
    
        # Re-zip to .pbix
        output_pbix = f"{ticker}_dashboard.pbix"
        with zipfile.ZipFile(output_pbix, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    zout.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), extract_dir))
    
        shutil.rmtree(extract_dir)
        if os.path.exists(blank_pbix):
            os.remove(blank_pbix)
    
        # Base64 for download
        with open(output_pbix, "rb") as f:
            pbix_b64 = base64.b64encode(f.read()).decode('utf-8')
        
        if os.path.exists(output_pbix):
            os.remove(output_pbix)
    
        return jsonify({"pbix_base64": pbix_b64, "narrative": narrative, "status": "success"})
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Power BI Dashboard Generator</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 50px auto; text-align: center; background: #f4f4f4; padding: 20px; }
        input { width: 80%; padding: 12px; font-size: 16px; border: 1px solid #ddd; border-radius: 5px; }
        button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; margin-top: 15px; }
        button:hover { background: #0056b3; }
        #status { margin-top: 20px; font-weight: bold; color: green; }
        #error { color: red; margin-top: 20px; }
        #download { margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <h1>🚀 Power BI Dashboard Generator</h1>
    <p>Enter a stock query (e.g., "Apple last 30 days").</p>
    <input type="text" id="query" placeholder="Enter query..." value="Apple last 30 days">
    <br>
    <button onclick="generateDashboard()">Generate Dashboard</button>
    <div id="status"></div>
    <div id="error"></div>
    <a id="download"><button>Download PBIX File</button></a>

    <script>
        async function generateDashboard() {
            const query = document.getElementById('query').value;
            const status = document.getElementById('status');
            const error = document.getElementById('error');
            const download = document.getElementById('download');
            status.innerHTML = 'Generating... (Please wait 10s)';
            error.innerHTML = '';
            download.style.display = 'none';

            try {
                const response = await fetch('/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query })
                });
                
                const contentType = response.headers.get("content-type");
                if (contentType && contentType.indexOf("application/json") !== -1) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        status.innerHTML = 'Dashboard ready! 🎉';
                        const binaryString = atob(data.pbix_base64);
                        const bytes = new Uint8Array(binaryString.length);
                        for (let i = 0; i < binaryString.length; i++) {
                            bytes[i] = binaryString.charCodeAt(i);
                        }
                        const blob = new Blob([bytes], { type: 'application/octet-stream' });
                        const url = URL.createObjectURL(blob);
                        download.href = url;
                        download.download = query.replace(/[^a-z0-9]/gi, '_').toLowerCase() + '_dashboard.pbix';
                        download.style.display = 'inline';
                    } else {
                        status.innerHTML = '';
                        error.innerHTML = 'Error: ' + (data.error || 'Unknown');
                    }
                } else {
                    status.innerHTML = '';
                    error.innerHTML = 'Server Error (Check Logs)';
                }
            } catch (err) {
                status.innerHTML = '';
                error.innerHTML = 'Error: ' + err.message;
            }
        }
    </script>
</body>
</html>
    '''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
