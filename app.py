from flask import Flask, request, jsonify
import yfinance as yf
import pandas as pd
import json
import zipfile
import os
import shutil
import base64
from datetime import datetime

app = Flask(__name__)

# --- Helper: Write file with UTF-16-LE encoding (Required for PBI) ---
def write_utf16le_json(filename, data):
    with open(filename, 'wb') as f:
        json_str = json.dumps(data, indent=2, default=str)
        f.write(json_str.encode('utf-16-le'))

# --- Helper: Generate DAX DATATABLE expression from DataFrame ---
def generate_dax_datatable(df):
    # Construct the header
    dax = "DATATABLE (\n"
    dax += '    "Date", DATETIME, "Open", DOUBLE, "High", DOUBLE, "Low", DOUBLE, "Close", DOUBLE, "Volume", INTEGER, "ChangePerc", DOUBLE, "Volatility", DOUBLE, "IsAnomaly", BOOLEAN, "Ticker", STRING,\n    {\n'
    
    # Construct rows
    rows = []
    for _, row in df.iterrows():
        # Format date safely
        date_str = row['Date'].strftime('%Y-%m-%d %H:%M:%S')
        # Handle boolean
        anomaly_val = "TRUE" if row['IsAnomaly'] else "FALSE"
        
        row_str = f'        {{ "{date_str}", {row["Open"]}, {row["High"]}, {row["Low"]}, {row["Close"]}, {int(row["Volume"])}, {row["Change%"]}, {row["Volatility"]}, {anomaly_val}, "{row["Ticker"]}" }}'
        rows.append(row_str)
    
    dax += ",\n".join(rows)
    dax += "\n    }\n)"
    return dax

# --- Create Blank PBIX Structure ---
def create_blank_pbix(filename):
    with zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
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
  <Override PartName="/DataModel" ContentType="application/vnd.ms-powerbi.content.model+binary"/>
</Types>'''
        zf.writestr('[Content_Types].xml', content_types)

        # _rels/.rels
        rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/powerbi/2016/06/reportlayout" Target="Report/Layout"/>
  <Relationship Id="rId2" Type="http://schemas.microsoft.com/powerbi/2016/06/datamodelschema" Target="DataModelSchema"/>
  <Relationship Id="rId3" Type="http://schemas.microsoft.com/powerbi/2016/06/theme" Target="Report/Theme"/>
  <Relationship Id="rId4" Type="http://schemas.microsoft.com/powerbi/2016/06/settings" Target="Settings"/>
  <Relationship Id="rId5" Type="http://schemas.microsoft.com/powerbi/2016/06/version" Target="Version"/>
  <Relationship Id="rId6" Type="http://schemas.microsoft.com/powerbi/2016/06/model" Target="DataModel"/>
</Relationships>'''
        zf.writestr('_rels/.rels', rels)

        # Version (Valid Binary Header)
        zf.writestr('Version', b'\x00\x00\x00\x00\x00\x00\x00\x00\x01\x13')
        
        # DataModel (Dummy Binary - Power BI will rebuild this from the Schema)
        # We write a minimal valid header to trick PBI into thinking it exists
        zf.writestr('DataModel', b'\x00\x00\x00\x00')

        # Settings
        settings = {"locale": "en-US"}
        zf.writestr('Settings', json.dumps(settings).encode('utf-16-le'))

@app.route('/generate', methods=['POST'])
def generate_pbix():
    try:
        query = request.json.get('query', 'Tesla last 30 days')
        
        # 1. Ticker Logic
        ticker = 'TSLA'
        if 'apple' in query.lower(): ticker = 'AAPL'
        elif 'bitcoin' in query.lower(): ticker = 'BTC-USD'
        elif 'ethereum' in query.lower(): ticker = 'ETH-USD'
        elif 'spy' in query.lower(): ticker = 'SPY'
    
        # 2. Fetch Data
        df = yf.download(ticker, period='30d', interval='1d')
        
        # Fix MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df['Ticker'] = ticker
        
        # Validation
        if 'Close' not in df.columns or df.empty:
            return jsonify({"error": "No data found"}), 400

        # Calculations
        df['Change%'] = df['Close'].pct_change() * 100
        df['Volatility'] = df['Change%'].rolling(window=5).std()
        df['IsAnomaly'] = df['Change%'].abs() > df['Change%'].std() * 2
        df = df.dropna().fillna(0) # Ensure no NaNs for DAX

        # 3. Generate DAX Data String
        dax_data_expression = generate_dax_datatable(df)
    
        # Narrative
        anomaly_count = int(df['IsAnomaly'].sum())
        max_change = df['Change%'].max()
        narrative = f"{ticker}: {anomaly_count} anomalies. Max Move: {max_change:.2f}%"

        # 4. Prepare Files
        blank_pbix = "blank.pbix"
        create_blank_pbix(blank_pbix)
        extract_dir = "pbix_extracted"
        shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(blank_pbix, 'r') as zin:
            zin.extractall(extract_dir)

        # 5. Build Model Schema (with Calculated Table)
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
                        {"name": "Volume", "dataType": "int64"},
                        {"name": "ChangePerc", "dataType": "double"}, # Renamed from Change% to avoid DAX issues
                        {"name": "Volatility", "dataType": "double"},
                        {"name": "IsAnomaly", "dataType": "boolean"},
                        {"name": "Ticker", "dataType": "string"}
                    ],
                    "partitions": [{
                        "name": "Finance",
                        "mode": "import",
                        "source": {
                            "type": "calculated",
                            "expression": dax_data_expression
                        }
                    }]
                }],
                "relationships": []
            }
        }
        write_utf16le_json(os.path.join(extract_dir, "DataModelSchema"), model_wrapper)

        # 6. Report Layout
        report_config = {
            "sections": [{
                "name": "ReportSection1",
                "displayName": "Overview",
                "visualContainers": [
                    {
                        "x": 100, "y": 100, "width": 800, "height": 400,
                        "config": json.dumps({
                            "name": "Visual1",
                            "singleVisual": {
                                "visualType": "lineChart",
                                "projections": {
                                    "Category": [{"queryRef": "Finance.Date"}],
                                    "Y": [{"queryRef": "Finance.Close"}]
                                },
                                "prototypeQuery": {
                                    "Version": 2,
                                    "From": [{"Name": "f", "Entity": "Finance", "Type": 0}],
                                    "Select": [
                                        {"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Date"}, "Name": "Finance.Date"},
                                        {"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Close"}, "Name": "Finance.Close"}
                                    ]
                                }
                            }
                        })
                    },
                    {
                         "x": 100, "y": 550, "width": 800, "height": 100,
                         "config": json.dumps({
                             "name": "TextBox1",
                             "singleVisual": {
                                 "visualType": "textbox",
                                 "objects": {
                                     "general": [{"properties": {"paragraphs": [{"textRuns": [{"value": narrative}]}]}}]
                                 }
                             }
                         })
                    }
                ]
            }]
        }
        os.makedirs(os.path.join(extract_dir, "Report"), exist_ok=True)
        write_utf16le_json(os.path.join(extract_dir, "Report/Layout"), report_config)

        # 7. Theme
        theme = {"name":"Theme","dataColors": ["#118DFF", "#12239E", "#E66C37", "#6B007B"], "background": "#FFFFFF", "foreground": "#000000"}
        with open(os.path.join(extract_dir, "Report/Theme"), 'w') as f:
            json.dump(theme, f)

        # 8. Re-zip
        output_pbix = f"{ticker}_dashboard.pbix"
        with zipfile.ZipFile(output_pbix, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    zout.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), extract_dir))

        shutil.rmtree(extract_dir)
        if os.path.exists(blank_pbix): os.remove(blank_pbix)

        # 9. Return Base64
        with open(output_pbix, "rb") as f:
            pbix_b64 = base64.b64encode(f.read()).decode('utf-8')
        if os.path.exists(output_pbix): os.remove(output_pbix)

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
