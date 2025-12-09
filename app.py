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

# --- Helper: Write file with UTF-16-LE encoding (Required for Power BI) ---
def write_utf16le_json(filename, data):
    with open(filename, 'wb') as f:
        json_str = json.dumps(data, indent=2, default=str)
        f.write(json_str.encode('utf-16-le'))

# --- Helper: Generate DAX DATATABLE expression ---
def generate_dax_datatable(df):
    # This creates a massive DAX string that contains the actual data
    dax = "DATATABLE (\n"
    dax += '    "Date", DATETIME, "Open", DOUBLE, "High", DOUBLE, "Low", DOUBLE, "Close", DOUBLE, "Volume", INTEGER, "ChangePerc", DOUBLE, "Volatility", DOUBLE, "IsAnomaly", BOOLEAN, "Ticker", STRING,\n    {\n'
    
    rows = []
    for _, row in df.iterrows():
        date_str = row['Date'].strftime('%Y-%m-%d %H:%M:%S')
        anomaly_val = "TRUE" if row['IsAnomaly'] else "FALSE"
        # DAX requires specific formatting for rows
        row_str = f'        {{ "{date_str}", {row["Open"]}, {row["High"]}, {row["Low"]}, {row["Close"]}, {int(row["Volume"])}, {row["Change%"]}, {row["Volatility"]}, {anomaly_val}, "{row["Ticker"]}" }}'
        rows.append(row_str)
    
    dax += ",\n".join(rows)
    dax += "\n    }\n)"
    return dax

# --- Helper: Create Visual JSON Config ---
def create_visual(type, x, y, w, h, name, column_name):
    # Map simple types to Power BI visual IDs
    visual_type_map = { "line": "lineChart", "bar": "columnChart", "card": "card" }
    v_type = visual_type_map.get(type, "lineChart")
    
    config = {
        "name": name,
        "singleVisual": {
            "visualType": v_type,
            "projections": {
                "Y": [{"queryRef": f"Finance.{column_name}"}]
            },
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": "f", "Entity": "Finance", "Type": 0}],
                "Select": [
                    {"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": column_name}, "Name": f"Finance.{column_name}"}
                ]
            }
        }
    }
    
    # Charts need an X-Axis (Date), Cards do not
    if type != "card":
        config["singleVisual"]["projections"]["Category"] = [{"queryRef": "Finance.Date"}]
        config["singleVisual"]["prototypeQuery"]["Select"].append(
            {"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Date"}, "Name": "Finance.Date"}
        )

    return {
        "x": x, "y": y, "width": w, "height": h,
        "config": json.dumps(config)
    }

# --- Create Blank PBIT Structure (No DataModel Binary) ---
def create_blank_pbit_structure(filename):
    with zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. [Content_Types].xml - Defines the files inside the zip
        content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/Report/Layout" ContentType="application/vnd.ms-powerbi.content.layout+json"/>
  <Override PartName="/DataModelSchema" ContentType="application/vnd.ms-powerbi.content.schema+json"/>
  <Override PartName="/Report/Theme" ContentType="application/vnd.ms-powerbi.content.theme+json"/>
  <Override PartName="/Settings" ContentType="application/vnd.ms-powerbi.content.settings+json"/>
  <Override PartName="/Version" ContentType="application/vnd.ms-powerbi.content.version+binary"/>
  <Override PartName="/Metadata" ContentType="application/json"/>
</Types>'''
        zf.writestr('[Content_Types].xml', content_types)

        # 2. _rels/.rels - Defines relationships (Notice: NO DataModel here)
        rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.microsoft.com/powerbi/2016/06/reportlayout" Target="Report/Layout"/>
  <Relationship Id="rId2" Type="http://schemas.microsoft.com/powerbi/2016/06/datamodelschema" Target="DataModelSchema"/>
  <Relationship Id="rId3" Type="http://schemas.microsoft.com/powerbi/2016/06/theme" Target="Report/Theme"/>
  <Relationship Id="rId4" Type="http://schemas.microsoft.com/powerbi/2016/06/settings" Target="Settings"/>
  <Relationship Id="rId5" Type="http://schemas.microsoft.com/powerbi/2016/06/version" Target="Version"/>
  <Relationship Id="rId6" Type="http://schemas.microsoft.com/powerbi/2016/06/metadata" Target="Metadata"/>
</Relationships>'''
        zf.writestr('_rels/.rels', rels)

        # 3. Version (Binary Header)
        zf.writestr('Version', b'\x00\x00\x00\x00\x00\x00\x00\x00\x01\x13')
        
        # 4. Settings
        settings = {"locale": "en-US"}
        zf.writestr('Settings', json.dumps(settings).encode('utf-16-le'))
        
        # 5. Metadata (Required for PBIT)
        metadata = {"type": "Report", "name": "GeneratedReport"}
        zf.writestr('Metadata', json.dumps(metadata).encode('utf-8'))

@app.route('/generate', methods=['POST'])
def generate_pbix():
    try:
        query = request.json.get('query', 'Tesla last 30 days')
        ticker = 'TSLA'
        if 'apple' in query.lower(): ticker = 'AAPL'
        elif 'bitcoin' in query.lower(): ticker = 'BTC-USD'
        elif 'ethereum' in query.lower(): ticker = 'ETH-USD'
        elif 'spy' in query.lower(): ticker = 'SPY'
    
        # Fetch Data
        df = yf.download(ticker, period='30d', interval='1d')
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df['Ticker'] = ticker
        
        if 'Close' not in df.columns or df.empty: return jsonify({"error": "No data found"}), 400

        # Calculations
        df['Change%'] = df['Close'].pct_change() * 100
        df['Volatility'] = df['Change%'].rolling(window=5).std()
        df['IsAnomaly'] = df['Change%'].abs() > df['Change%'].std() * 2
        df = df.dropna().fillna(0)

        # Generate DAX
        dax_data_expression = generate_dax_datatable(df)
        narrative = f"{ticker}: {int(df['IsAnomaly'].sum())} anomalies. Max Move: {df['Change%'].max():.2f}%"

        # Prepare Extract Directory
        extract_dir = "pbit_extracted"
        shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)
        
        # Create the Base Structure
        temp_zip = "temp_structure.zip"
        create_blank_pbit_structure(temp_zip)
        with zipfile.ZipFile(temp_zip, 'r') as zin:
            zin.extractall(extract_dir)
        os.remove(temp_zip)

        # 1. MODEL SCHEMA (The Core)
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
                        {"name": "ChangePerc", "dataType": "double"},
                        {"name": "Volatility", "dataType": "double"},
                        {"name": "IsAnomaly", "dataType": "boolean"},
                        {"name": "Ticker", "dataType": "string"}
                    ],
                    "partitions": [{
                        "name": "Finance",
                        "mode": "import",
                        "source": { "type": "calculated", "expression": dax_data_expression }
                    }]
                }]
            }
        }
        write_utf16le_json(os.path.join(extract_dir, "DataModelSchema"), model_wrapper)

        # 2. REPORT LAYOUT
        # KPI Cards
        card_close = create_visual("card", 10, 10, 300, 150, "CardClose", "Close")
        card_high = create_visual("card", 320, 10, 300, 150, "CardHigh", "High")
        card_vol = create_visual("card", 630, 10, 300, 150, "CardVol", "Volume")
        
        # Charts
        line_chart = create_visual("line", 10, 170, 920, 350, "MainChart", "Close")
        bar_chart = create_visual("bar", 10, 530, 920, 150, "VolChart", "Volume")
        
        # Text Box
        text_box = {
             "x": 10, "y": 690, "width": 920, "height": 50,
             "config": json.dumps({
                 "name": "TextBox1",
                 "singleVisual": {
                     "visualType": "textbox",
                     "objects": { "general": [{"properties": {"paragraphs": [{"textRuns": [{"value": narrative}]}]}}] }
                 }
             })
        }

        report_config = {
            "sections": [{
                "name": "ReportSection1",
                "displayName": "Overview",
                "visualContainers": [card_close, card_high, card_vol, line_chart, bar_chart, text_box]
            }]
        }
        os.makedirs(os.path.join(extract_dir, "Report"), exist_ok=True)
        write_utf16le_json(os.path.join(extract_dir, "Report/Layout"), report_config)

        # 3. THEME
        theme = {"name":"Theme","dataColors": ["#118DFF", "#12239E", "#E66C37", "#6B007B"], "background": "#FFFFFF", "foreground": "#000000"}
        with open(os.path.join(extract_dir, "Report/Theme"), 'w') as f: json.dump(theme, f)

        # 4. ZIP TO .PBIT (Template)
        output_filename = f"{ticker}_dashboard.pbit"
        with zipfile.ZipFile(output_filename, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    zout.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), extract_dir))

        shutil.rmtree(extract_dir)
        
        # Encode
        with open(output_filename, "rb") as f:
            pbix_b64 = base64.b64encode(f.read()).decode('utf-8')
        if os.path.exists(output_filename): os.remove(output_filename)

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
    <title>Power BI Dashboard Generator</title>
    <style>
        body { font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 50px auto; text-align: center; background: #f4f4f4; padding: 20px; }
        input { width: 80%; padding: 12px; font-size: 16px; border: 1px solid #ddd; border-radius: 5px; }
        button { background: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; margin-top: 15px; }
        button:hover { background: #0056b3; }
        #status { margin-top: 20px; font-weight: bold; color: green; }
        #error { color: red; margin-top: 20px; }
        #download { margin-top: 10px; display: none; }
        .note { font-size: 12px; color: #666; margin-top: 5px; }
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
    <a id="download"><button>Download Dashboard (.pbit)</button></a>
    <p class="note">Note: This downloads a <b>.pbit</b> template. Open it in Power BI Desktop and it will automatically build your dashboard.</p>

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
                        // Extension is now .pbit
                        download.download = query.replace(/[^a-z0-9]/gi, '_').toLowerCase() + '_dashboard.pbit';
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
