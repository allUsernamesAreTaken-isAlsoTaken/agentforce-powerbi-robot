import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 1. Page Configuration (The "Canvas") ---
st.set_page_config(
    page_title="AI Financial Dashboard",
    page_icon="📊",
    layout="wide", # Uses the full width like a Power BI report
    initial_sidebar_state="expanded"
)

# Custom CSS to mimic Power BI's clean look
st.markdown("""
<style>
    .metric-card {
        background-color: #0e1117;
        border: 1px solid #262730;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
    }
    .stApp {
        background-color: #000000;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# --- 2. Sidebar (The "Slicers") ---
with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    query = st.text_input("Stock Query", value="Tesla last 30 days")
    
    # Simple parser to find ticker
    ticker = "TSLA"
    if 'apple' in query.lower(): ticker = "AAPL"
    elif 'bitcoin' in query.lower(): ticker = "BTC-USD"
    elif 'ethereum' in query.lower(): ticker = "ETH-USD"
    elif 'spy' in query.lower(): ticker = "SPY"
    elif 'nvidia' in query.lower(): ticker = "NVDA"
    elif 'microsoft' in query.lower(): ticker = "MSFT"
    elif 'google' in query.lower(): ticker = "GOOGL"

    st.info(f"Detected Ticker: **{ticker}**")
    
    if st.button("🔄 Generate Report", type="primary"):
        st.session_state['generate'] = True

# --- 3. Main Logic (The "Power Query" Engine) ---
if st.session_state.get('generate'):
    
    # A. Fetch Data
    try:
        with st.spinner(f"Fetching data for {ticker}..."):
            df = yf.download(ticker, period="30d", interval="1d")
            
            # Fix MultiIndex columns (The original error fix)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            df = df.reset_index()

        if df.empty:
            st.error("No data found. Please try a different ticker.")
            st.stop()

        # B. Calculate Measures (DAX equivalent)
        df['Change%'] = df['Close'].pct_change() * 100
        df['Volatility'] = df['Change%'].rolling(window=5).std()
        df['IsAnomaly'] = df['Change%'].abs() > df['Change%'].std() * 2
        
        # Narrative Calculation
        current_price = df['Close'].iloc[-1]
        start_price = df['Close'].iloc[0]
        return_30d = ((current_price - start_price) / start_price) * 100
        anomaly_count = int(df['IsAnomaly'].sum())
        max_volatility = df['Volatility'].max()
        
        narrative = f"""
        **AI Insight:** {ticker} has shown a **{return_30d:.2f}%** return over the last 30 days. 
        We detected **{anomaly_count} anomalies** in price movement, suggesting periods of high instability. 
        The maximum volatility recorded was **{max_volatility:.2f}**.
        """

        # --- 4. The Dashboard Layout (The "Report View") ---
        
        st.title(f"📊 {ticker} Executive Dashboard")
        st.markdown(narrative)
        st.divider()

        # Row 1: KPI Cards
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(label="Current Price", value=f"${current_price:.2f}", delta=f"{df['Change%'].iloc[-1]:.2f}%")
        with col2:
            st.metric(label="30-Day Return", value=f"{return_30d:.2f}%")
        with col3:
            st.metric(label="Anomaly Count", value=str(anomaly_count), delta_color="inverse")
        with col4:
            st.metric(label="Max High", value=f"${df['High'].max():.2f}")

        # Row 2: Main Price Chart with Anomalies
        st.subheader("Price Trend & Anomaly Detection")
        
        fig_price = go.Figure()
        
        # Line Chart
        fig_price.add_trace(go.Scatter(
            x=df['Date'], y=df['Close'],
            mode='lines',
            name='Close Price',
            line=dict(color='#00B3FF', width=3)
        ))
        
        # Anomaly Dots
        anomalies = df[df['IsAnomaly']]
        fig_price.add_trace(go.Scatter(
            x=anomalies['Date'], y=anomalies['Close'],
            mode='markers',
            name='Anomaly',
            marker=dict(color='red', size=10, symbol='x')
        ))

        fig_price.update_layout(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            height=400,
            margin=dict(l=20, r=20, t=30, b=20)
        )
        st.plotly_chart(fig_price, use_container_width=True)

        # Row 3: Volume & Volatility
        col_left, col_right = st.columns(2)
        
        with col_left:
            st.subheader("Trading Volume")
            fig_vol = go.Figure(data=[go.Bar(
                x=df['Date'], y=df['Volume'],
                marker_color='#2E86C1'
            )])
            fig_vol.update_layout(
                template="plotly_dark",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                height=300,
                margin=dict(l=20, r=20, t=30, b=20)
            )
            st.plotly_chart(fig_vol, use_container_width=True)
            
        with col_right:
            st.subheader("Market Volatility (Risk)")
            fig_risk = go.Figure(data=[go.Area(
                x=df['Date'], y=df['Volatility'],
                marker_color='#E74C3C',
                opacity=0.5
            )])
            fig_risk.update_layout(
                template="plotly_dark",
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                height=300,
                margin=dict(l=20, r=20, t=30, b=20)
            )
            st.plotly_chart(fig_risk, use_container_width=True)

        # Raw Data Section (Power BI "Data View")
        with st.expander("🔎 View Raw Data"):
            st.dataframe(df.style.highlight_max(axis=0))

    except Exception as e:
        st.error(f"An error occurred: {str(e)}")

else:
    # Landing Page State
    st.markdown("### ⬅️ Enter a stock ticker in the sidebar and click Generate.")
