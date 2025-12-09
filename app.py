# app.py
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

# --- Helper: Write file with UTF-16-LE encoding (use for parts PowerBI expects) ---
def write_utf16le_json(filename, data):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'wb') as f:
        json_str = json.dumps(data, indent=2, default=str)
        f.write(json_str.encode('utf-16-le'))

# --- Helper: Generate DAX DATATABLE expression (careful formatting) ---
def generate_dax_datatable(df):
    # Ensure the DataFrame columns match schema names
    # Required order: Date, Open, High, Low, Close, Volume, ChangePerc, Volatility, IsAnomaly, Ticker
    rows = []
    for _, row in df.iterrows():
        # Format date as ISO string (Power BI DATATABLE accepts quoted datetime string)
        date_str = row['Date'].strftime('%Y-%m-%d %H:%M:%S')
        # Numeric formatting: ensure dot decimal and no thousands separators
        def fmt_num(x):
            if pd.isna(x):
                return "BLANK()"
            # Force Python representation with dot decimal
            return f"{float(x):.8f}".rstrip('0').rstrip('.') if float(x) != int(float(x)) else str(int(float(x)))
        # Volume as integer
        volume_val = int(row['Volume']) if not pd.isna(row['Volume']) else 0
        # ChangePerc numeric
        change_val = row.get('ChangePerc', 0.0)
        # Volatility numeric
        vol_val = row.get('Volatility', 0.0)
        # Boolean as TRUE/FALSE
        anomaly_val = "TRUE" if bool(row.get('IsAnomaly', False)) else "FALSE"
        # Ensure ticker string is escaped
        ticker_val = str(row.get('Ticker', ''))
        # Construct DAX row: keep numeric literals unquoted, strings quoted
        row_str = (
            f'        {{ "{date_str}", {fmt_num(row["Open"])}, {fmt_num(row["High"])}, '
            f'{fmt_num(row["Low"])}, {fmt_num(row["Close"])}, {volume_val}, {fmt_num(change_val)}, '
            f'{fmt_num(vol_val)}, {anomaly_val}, "{ticker_val}" }}'
        )
        rows.append(row_str)

    header = (
        'DATATABLE (\n'
        '    "Date", DATETIME, "Open", DOUBLE, "High", DOUBLE, "Low", DOUBLE, "Close", DOUBLE, '
        '"Volume", INTEGER, "ChangePerc", DOUBLE, "Volatility", DOUBLE, "IsAnomaly", BOOLEAN, "Ticker", STRING,\n    {\n'
    )
    dax = header + ",\n".join(rows) + "\n    }\n)"
    return dax

# --- Helper: Create Visual Configs (kept simple) ---
def create_visual(type, x, y, w, h, name, column_name):
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
    if type != "card":
        config["singleVisual"]["projections"]["Category"] = [{"queryRef": "Finance.Date"}]
        config["singleVisual"]["prototypeQuery"]["Select"].append(
            {"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": "Date"}, "Name": "Finance.Date"}
        )
    return {
        "x": x, "y": y, "width": w, "height": h,
        "config": json.dumps(config)
    }

# --- Create STRICT PBIT Structure (No DataModel binary, using DataModelSchema) ---
def create_strict_pbit_structure(filename):
    with zipfile.ZipFile(filename, 'w', zipfile.ZIP_DEFLATED) as zf:
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
        # Version – keep same small binary structure
        zf.writestr('Version', b'\x00\x00\x00\x00\x00\x00\x00\x00\x01\x13')

        # Settings file will be written later into extracted folder (we only create skeleton here)
        # Same for other files: we will extract this zip and overwrite files with correct encodings
        # Create minimal required files
        zf.writestr('Report/Theme', json.dumps({"name":"Theme"}))
        zf.writestr('Metadata', json.dumps({"type": "Report", "name": "GeneratedReport"}))

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
        df = yf.download(ticker, period='30d', interval='1d', progress=False)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df['Ticker'] = ticker

        if 'Close' not in df.columns or df.empty:
            return jsonify({"error": "No data found"}), 400

        # Calculations
        df['ChangePerc'] = df['Close'].pct_change() * 100
        df['Volatility'] = df['ChangePerc'].rolling(window=5).std()
        df['IsAnomaly'] = df['ChangePerc'].abs() > df['ChangePerc'].std() * 2
        df = df.dropna().fillna(0)

        # Generate DAX
        dax_data_expression = generate_dax_datatable(df)
        narrative = f"{ticker}: {int(df['IsAnomaly'].sum())} anomalies. Max Move: {df['ChangePerc'].max():.2f}%"

        # Prepare Folder
        extract_dir = "pbit_extracted"
        shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)

        # Create strict structure zip and extract to folder
        temp_zip = "temp_structure.zip"
        create_strict_pbit_structure(temp_zip)
        with zipfile.ZipFile(temp_zip, 'r') as zin:
            zin.extractall(extract_dir)
        os.remove(temp_zip)

        # --- 1. MODEL SCHEMA (write as UTF-16-LE) ---
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

        # --- 2. REPORT LAYOUT (UTF-16-LE) ---
        card_close = create_visual("card", 10, 10, 300, 150, "CardClose", "Close")
        card_high = create_visual("card", 320, 10, 300, 150, "CardHigh", "High")
        card_vol = create_visual("card", 630, 10, 300, 150, "CardVol", "Volume")
        line_chart = create_visual("line", 10, 170, 920, 350, "MainChart", "Close")
        bar_chart = create_visual("bar", 10, 530, 920, 150, "VolChart", "Volume")
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
        write_utf16le_json(os.path.join(extract_dir, "Report", "Layout"), report_config)

        # --- 3. THEME & SETTINGS ---
        # Theme (utf-8 is acceptable, but we'll write JSON pretty)
        theme = {"name":"Theme","dataColors": ["#118DFF", "#12239E", "#E66C37", "#6B007B"], "background": "#FFFFFF", "foreground": "#000000"}
        os.makedirs(os.path.join(extract_dir, "Report"), exist_ok=True)
        with open(os.path.join(extract_dir, "Report", "Theme"), 'w', encoding='utf-8') as f:
            json.dump(theme, f, indent=2)

        settings = {"locale": "en-US"}
        write_utf16le_json(os.path.join(extract_dir, "Settings"), settings)

        # --- 4. ZIP TO .PBIT ---
        output_filename = f"{ticker}_dashboard.pbit"
        with zipfile.ZipFile(output_filename, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    full = os.path.join(root, file)
                    rel = os.path.relpath(full, extract_dir)
                    zout.write(full, rel)

        shutil.rmtree(extract_dir)

        # Encode and send
        with open(output_filename, "rb") as f:
            pbix_b64 = base64.b64encode(f.read()).decode('utf-8')
        if os.path.exists(output_filename):
            os.remove(output_filename)

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
    <script>
        async function generateDashboard() {
            const query = document.getElementById('query').value;
            const status = document.getElementById('status');
            const error = document.getElementById('error');
            const download = document.getElementById('download');
            status.innerHTML = 'Generating...';
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
