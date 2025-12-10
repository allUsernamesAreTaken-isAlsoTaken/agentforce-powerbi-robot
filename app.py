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

# Professional Dark Theme CSS
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
    """
    Logic:
    1. If user types a short symbol (e.g. "NVDA"), use it directly (Fail-Safe).
    2. If user types a sentence (e.g. "Company that makes GPUs"), use AI.
    """
    # 1. FAIL-SAFE: If query is short and has no spaces, assume it is a ticker
    if len(user_query) < 6 and " " not in user_query:
        return user_query.upper(), None

    # 2. AI RESOLUTION
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Identify the stock ticker for: "{user_query}".
        Return ONLY the ticker symbol (e.g. AAPL, BTC-USD). 
        If you cannot find it, return "ERROR".
        """
        response = model.generate_content(prompt)
        ticker = response.text.strip().upper().replace('*', '').replace('`', '')
        
        if "ERROR" in ticker:
            return None, "AI could not identify this company."
            
        return ticker, None
        
    except Exception as e:
        return None, str(e)

def calculate_technicals(df):
    if len(df) < 50: return df # Not enough data for calculations
    
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

# --- 3. Sidebar Controls ---
with st.sidebar:
    st.title("⚡ ProTraders AI")
    
    # API Key Input
    api_key = st.text_input("🔑 Gemini API Key", type="password")
    st.divider()
    
    # Search Input
    query = st.text_input("Search Market", value="NVDA")
    st.caption("Tip: Type 'NVDA' or 'TSLA' directly to skip AI check.")
    
    # Filters
    timeframe = st.selectbox("Timeframe", ["1mo", "3mo", "6mo", "1y", "ytd"], index=2)
    
    st.subheader("Chart Overlays")
    show_sma = st.checkbox("Show SMA (20 & 50)", value=True)
    show_bb = st.checkbox("Show Bollinger Bands", value=False)
    
    run_btn = st.button("🚀 Analyze Market", type="primary")

# --- 4. Main Application Logic ---
if run_btn:
    if not api_key:
        st.error("⚠️ Please enter your API Key in the sidebar.")
        st.stop()

    # A. Ticker Identification
    with st.spinner("🔍 Identifying Asset..."):
        ticker, error_msg = get_ticker_from_llm(query, api_key)
    
    # Error Handling for AI
    if not ticker:
        st.error(f"❌ Error: {error_msg}")
        st.info("👉 Try typing the ticker symbol directly (e.g., 'NVDA' instead of 'Nvidia') to bypass this error.")
        st.stop()

    # B. Fetch Data
    try:
        with st.spinner(f"📥 Loading Data for {ticker}..."):
            df = yf.download(ticker, period=timeframe, interval="1d")
            
            # Handle MultiIndex columns if they exist
            if isinstance(df.columns, pd.MultiIndex): 
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()

            # Fix: Ensure 'Close' column exists (sometimes yfinance sends Adj Close)
            if 'Close' not in df.columns and 'Adj Close' in df.columns:
                df['Close'] = df['Adj Close']

            # Get Fundamentals (Try/Except block prevents crash if info is missing)
            stock_info = {}
            try:
                stock_info = yf.Ticker(ticker).info
            except:
                pass 
            
            # Calculate Indicators
            df = calculate_technicals(df)

        if df.empty:
            st.error(f"❌ No market data found for '{ticker}'. The stock might be delisted.")
            st.stop()

        # C. AI Analysis Generation (Narrative)
        ai_analysis = "AI Analysis unavailable (Check API Key)"
        try:
            current_price = df['Close'].iloc[-1]
            rsi_val = df['RSI'].iloc[-1] if 'RSI' in df.columns else 50
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            analysis_prompt = f"""
            Analyze {ticker}. Price: ${current_price:.2f}. RSI: {rsi_val:.2f}.
            Provide a 3-bullet point technical summary: Sentiment, Support/Resistance, Insight.
            """
            ai_analysis = model.generate_content(analysis_prompt).text
        except Exception as e:
            st.warning(f"⚠️ AI Narrative failed: {str(e)}")

        # --- 5. DASHBOARD VISUALS ---
        
        # Title
        long_name = stock_info.get('longName', ticker)
        st.title(f"{long_name} ({ticker})")
        
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

        # Tabs Layout
        tab1, tab2, tab3 = st.tabs(["📊 Technical Chart", "🤖 AI Insights", "🏢 Fundamentals"])

        # TAB 1: Advanced Charting
        with tab1:
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                                vertical_spacing=0.03, subplot_titles=(f'{ticker} Price Action', 'RSI Momentum'),
                                row_width=[0.2, 0.7])

            # Candlestick
            fig.add_trace(go.Candlestick(x=df['Date'],
                            open=df['Open'], high=df['High'],
                            low=df['Low'], close=df['Close'], name='OHLC'), row=1, col=1)

            # Moving Averages
            if show_sma and 'SMA_20' in df.columns:
                fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA_20'], line=dict(color='orange', width=1), name='SMA 20'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA_50'], line=dict(color='blue', width=1), name='SMA 50'), row=1, col=1)
            
            # Bollinger Bands
            if show_bb and 'BB_Upper' in df.columns:
                fig.add_trace(go.Scatter(x=df['Date'], y=df['BB_Upper'], line=dict(color='gray', width=1, dash='dot'), name='BB Upper'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df['Date'], y=df['BB_Lower'], line=dict(color='gray', width=1, dash='dot'), fill='tonexty', fillcolor='rgba(128,128,128,0.1)', name='BB Lower'), row=1, col=1)

            # RSI
            if 'RSI' in df.columns:
                fig.add_trace(go.Scatter(x=df['Date'], y=df['RSI'], line=dict(color='#9b59b6', width=2), name='RSI'), row=2, col=1)
                fig.add_shape(type="line", x0=df['Date'].iloc[0], x1=df['Date'].iloc[-1], y0=70, y1=70, line=dict(color="red", width=1, dash="dash"), row=2, col=1)
                fig.add_shape(type="line", x0=df['Date'].iloc[0], x1=df['Date'].iloc[-1], y0=30, y1=30, line=dict(color="green", width=1, dash="dash"), row=2, col=1)

            fig.update_layout(height=600, xaxis_rangeslider_visible=False, template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)

        # TAB 2: AI Narrative
        with tab2:
            st.markdown("### 🧠 AI Technical Analyst Report")
            if "unavailable" in ai_analysis:
                st.warning(ai_analysis)
            else:
                st.info(ai_analysis)

        # TAB 3: Fundamentals
        with tab3:
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("**Sector**"); st.write(stock_info.get('sector', 'N/A'))
            with c2:
                st.markdown("**Market Cap**"); st.write(f"${stock_info.get('marketCap', 0):,.0f}")
            with c3:
                st.markdown("**52 Week High**"); st.write(f"${stock_info.get('fiftyTwoWeekHigh', 0):.2f}")

    except Exception as e:
        st.error(f"Critical Error: {str(e)}")

else:
    # Landing Page
    st.markdown("## 👋 Welcome to ProTraders AI")
    st.markdown("1. Enter your **Gemini API Key** in the sidebar.")
    st.markdown("2. Type a stock symbol (e.g. **NVDA**) or a company name.")
    st.markdown("3. Click **Analyze Market**.")
