import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import os

# 🔒 1. ระบบ Login ป้องกันคนนอกเข้าดู (Simple Auth)
def check_password():
    def password_entered():
        if st.session_state["password"] == os.getenv("DASHBOARD_PASS", "admin1234"): # รหัสผ่านเริ่มต้น
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("🔑 Enter Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("🔑 Enter Password", type="password", on_change=password_entered, key="password")
        st.error("😕 รหัสผ่านผิดครับ")
        return False
    return True

if not check_password():
    st.stop() # ถ้ายังไม่ Login ให้หยุดการทำงานตรงนี้เลย

# ⚙️ 2. ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="SENTRY Dashboard", page_icon="📈", layout="wide")
st.title("🛡️ SENTRY TRADER Dashboard")

# 🗄️ 3. ฟังก์ชันดึงข้อมูลจาก SQLite (อัปเดตให้ตรงกับ schema จริงของ SENTRY_TRADER)
@st.cache_data(ttl=60) # Cache ข้อมูล 60 วินาที จะได้ไม่ดึง DB ถี่เกินไป
def load_data():
    conn = sqlite3.connect('/app/database/sentry_trader.db')
    # ดึงประวัติไม้ที่ปิดไปแล้ว (ตาราง trades)
    df = pd.read_sql_query("SELECT * FROM trades WHERE status='CLOSED'", conn)
    
    if not df.empty:
        # แปลงคอลัมน์เวลาให้เป็น DateTime (เราใช้ closed_at)
        df['closed_at'] = pd.to_datetime(df['closed_at'])
    conn.close()
    return df

df = load_data()

if not df.empty:
    # 📊 4. คำนวณภาพรวม (KPIs)
    total_trades = len(df)
    win_trades = len(df[df['pnl_usdt'] > 0])
    win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0
    total_profit = df['pnl_usdt'].sum()

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 Total Net Profit", f"{total_profit:.2f} USDT")
    col2.metric("🎯 Win Rate", f"{win_rate:.1f} %")
    col3.metric("🧾 Total Trades", str(total_trades))

    st.markdown("---")

    # 📈 5. กราฟรายได้รายสัปดาห์ / รายวัน
    st.subheader("🗓️ กำไร/ขาดทุน (รายสัปดาห์)")
    # จัดกลุ่มตามสัปดาห์ (W) แล้วรวม PnL
    weekly_pnl = df.resample('W', on='closed_at')['pnl_usdt'].sum().reset_index()
    fig = px.bar(weekly_pnl, x='closed_at', y='pnl_usdt', 
                 title="Weekly PnL", 
                 color='pnl_usdt', color_continuous_scale=px.colors.diverging.RdYlGn)
    st.plotly_chart(fig, use_container_width=True)

    # 📜 6. ตารางประวัติไม้ที่เข้าซื้อ (Trade History)
    st.subheader("📋 ประวัติการเทรดล่าสุด")
    # จัดเรียงจากใหม่ไปเก่า และเลือกเฉพาะคอลัมน์ที่อยากดู
    recent_trades = df[['closed_at', 'symbol', 'side', 'entry_price', 'exit_price', 'pnl_usdt']].sort_values(by='closed_at', ascending=False)
    
    # ไฮไลต์สีเขียวแดงตาม PnL
    def color_pnl(val):
        color = 'green' if val > 0 else 'red'
        return f'color: {color}'
    
    st.dataframe(recent_trades.style.map(color_pnl, subset=['pnl_usdt']), use_container_width=True)

else:
    st.info("⏳ ยังไม่มีประวัติการเทรดในระบบครับ")
