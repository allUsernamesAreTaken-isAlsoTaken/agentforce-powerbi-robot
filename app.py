import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import google.generativeai as genai
from datetime import datetime, timedelta

# --- 1. Page Config & Advanced Styling ---
st.set_page_config(
    page_title="ProTraders AI Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for a Professional Dark Theme
st.markdown("""
<style>
    .stApp { background-color: #0e1117; color: #FAFAFA; }
    .stTabs [data-baseweb="tab-list"] { gap: 24px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px; white-space: pre-wrap; background-color: #0e1117;
        border-radius: 4px 4px 0px 0px; gap: 1px; padding-top: 10px; padding-bottom: 10px;
    }
    .metric-card {
        background-color: #1a1c24; border: 1px solid #30333d;
        padding: 15px; border-radius: 8px; text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# --- 2. Helper Functions ---

def get_ticker_from_llm(user_query, api_key):
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Identify the stock ticker for: "{user_query}".
        Return ONLY the ticker (e.g., AAPL, BTC-USD). If unclear, return "ERROR".
        """
        response = model.generate_content(prompt)
        return response.text.strip().upper().replace('*', '').replace('`', '')
    except:
        return "ERROR"

def calculate_technicals(df):
    # Simple Moving Averages
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    
    # Bollinger Bands
    df['BB_Middle'] = df['Close'].rolling(window=20).mean()
    df['BB_Std'] = df['Close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Middle'] + (df['BB_Std'] * 2)
    df['BB_Lower'] = df['BB_Middle'] - (df['BB_Std'] * 2)
    
    # RSI (Relative Strength Index)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    return df

# --- 3. Sidebar ---
with st.sidebar:
    st.title("⚡ ProTraders AI")
    
    # API Key
    api_key = st.text_input("🔑 Gemini API Key", type="password")
    
    st.divider()
    
    # Search Controls
    query = st.text_input("Search Market", value="Nvidia")
    timeframe = st.selectbox("Timeframe", ["1mo", "3mo", "6mo", "1y", "ytd"], index=2)
    
    # Technical Overlays
    st.subheader("Chart Overlays")
    show_sma = st.checkbox("Show SMA (20 & 50)", value=True)
    show_bb = st.checkbox("Show Bollinger Bands", value=False)
    
    run_btn = st.button("🚀 Analyze Market", type="primary")

# --- 4. Main App Logic ---
if run_btn:
    if not api_key:
        st.error("Please provide a Google Gemini API Key in the sidebar.")
        st.stop()

    # A. Ticker Identification
    with st.spinner("🔍 Identifying Asset..."):
        ticker = get_ticker_from_llm(query, api_key)
    
    if ticker == "ERROR" or not ticker:
        st.error("Could not identify ticker. Try entering the symbol directly.")
        st.stop()

    # B. Fetch Data
    try:
        with st.spinner(f"📥 Loading Data for {ticker}..."):
            # Get History
            df = yf.download(ticker, period=timeframe, interval="1d")
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            
            # Get Fundamentals
            stock_info = yf.Ticker(ticker).info
            
            # Run Technical Calculations
            df = calculate_technicals(df)

        if df.empty:
            st.error("No market data found.")
            st.stop()

        # C. AI Analysis Generation
        with st.spinner("🧠 AI Analyst is thinking..."):
            current_price = df['Close'].iloc[-1]
            rsi_val = df['RSI'].iloc[-1]
            model = genai.GenerativeModel('gemini-1.5-flash')
            
            analysis_prompt = f"""
            Analyze {ticker}. Current Price: ${current_price:.2f}. RSI: {rsi_val:.2f}.
            Trend: Last 30 days return is {((df['Close'].iloc[-1] - df['Close'].iloc[0])/df['Close'].iloc[0]*100):.2f}%.
            Provide a 3-bullet point technical summary:
            1. Trend Sentiment (Bullish/Bearish).
            2. Key Support/Resistance (Estimate based on recent highs/lows).
            3. Actionable insight.
            Keep it professional and concise.
            """
            ai_analysis = model.generate_content(analysis_prompt).text

        # --- 5. DASHBOARD LAYOUT ---
        st.title(f"{stock_info.get('longName', ticker)} ({ticker})")
        
        # Top KPI Row
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        last_close = df['Close'].iloc[-1]
        prev_close = df['Close'].iloc[-2]
        change_pct = ((last_close - prev_close) / prev_close) * 100
        
        kpi1.metric("Current Price", f"${last_close:.2f}", f"{change_pct:.2f}%")
        kpi2.metric("High (Period)", f"${df['High'].max():.2f}")
        kpi3.metric("Low (Period)", f"${df['Low'].min():.2f}")
        kpi4.metric("Volume (Avg)", f"{df['Volume'].mean():,.0f}")

        st.divider()

        # TABS for Advanced Views
        tab1, tab2, tab3 = st.tabs(["📊 Technical Chart", "🤖 AI Insights", "🏢 Fundamentals"])

        # TAB 1: Advanced Charting
        with tab1:
            # Create Subplots: Row 1 = Price, Row 2 = RSI
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                vertical_spacing=0.03, subplot_titles=(f'{ticker} Price Action', 'RSI Momentum'),
                                row_width=[0.2, 0.7])

            # 1. Candlestick
            fig.add_trace(go.Candlestick(x=df['Date'],
                            open=df['Open'], high=df['High'],
                            low=df['Low'], close=df['Close'], name='OHLC'), row=1, col=1)

            # 2. Overlays
            if show_sma:
                fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA_20'], line=dict(color='orange', width=1), name='SMA 20'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA_50'], line=dict(color='blue', width=1), name='SMA 50'), row=1, col=1)
            
            if show_bb:
                fig.add_trace(go.Scatter(x=df['Date'], y=df['BB_Upper'], line=dict(color='gray', width=1, dash='dot'), name='BB Upper'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df['Date'], y=df['BB_Lower'], line=dict(color='gray', width=1, dash='dot'), fill='tonexty', fillcolor='rgba(128,128,128,0.1)', name='BB Lower'), row=1, col=1)

            # 3. RSI Subplot
            fig.add_trace(go.Scatter(x=df['Date'], y=df['RSI'], line=dict(color='#9b59b6', width=2), name='RSI'), row=2, col=1)
            # RSI Lines (70/30)
            fig.add_shape(type="line", x0=df['Date'].iloc[0], x1=df['Date'].iloc[-1], y0=70, y1=70, line=dict(color="red", width=1, dash="dash"), row=2, col=1)
            fig.add_shape(type="line", x0=df['Date'].iloc[0], x1=df['Date'].iloc[-1], y0=30, y1=30, line=dict(color="green", width=1, dash="dash"), row=2, col=1)

            fig.update_layout(height=600, xaxis_rangeslider_visible=False, template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)

        # TAB 2: AI Narrative
        with tab2:
            st.markdown("### 🧠 AI Technical Analyst Report")
            st.info(ai_analysis)
            
            st.markdown("### 📋 Recent Data (Last 5 Days)")
            st.dataframe(df[['Date', 'Close', 'Volume', 'RSI', 'SMA_20']].tail(5).style.format({"Close": "${:.2f}", "RSI": "{:.1f}", "SMA_20": "${:.2f}"}))

        # TAB 3: Fundamentals
        with tab3:
            c1, c2, c3 = st.columns(3)
            info = stock_info
            
            with c1:
                st.markdown("**Sector**")
                st.write(info.get('sector', 'N/A'))
                st.markdown("**Industry**")
                st.write(info.get('industry', 'N/A'))
            
            with c2:
                st.markdown("**Market Cap**")
                st.write(f"${info.get('marketCap', 0):,.0f}")
                st.markdown("**Beta (Volatility)**")
                st.write(info.get('beta', 'N/A'))
                
            with c3:
                st.markdown("**P/E Ratio**")
                st.write(info.get('trailingPE', 'N/A'))
                st.markdown("**52 Week High**")
                st.write(f"${info.get('fiftyTwoWeekHigh', 0):.2f}")

    except Exception as e:
        st.error(f"Error analyzing {ticker}: {str(e)}")

else:
    # Landing Page
    st.markdown("## 👋 Welcome to ProTraders AI")
    st.markdown("Use the sidebar to enter your API key and search for any asset.")
    st.markdown("Features included in this build:")
    st.markdown("- **Candlestick Charts** with SMA & Bollinger Bands")
    st.markdown("- **RSI Momentum** Oscillator")
    st.markdown("- **Generative AI** Technical Analysis")
