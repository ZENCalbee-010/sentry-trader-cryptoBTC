# ☁️ SENTRY_TRADER Cloud Deployment Guide

คู่มือนี้จะสอนวิธีนำ `SENTRY_TRADER` ของคุณขึ้นไปรันบน Cloud VPS (เช่น DigitalOcean, Azure, AWS) เพื่อให้มันสามารถเฝ้าตลาดคริปโต และเทรดให้คุณได้ตลอด 24 ชั่วโมง โดยไม่ต้องเปิดคอมพิวเตอร์ทิ้งไว้!

---

## 💻 Step 1: เตรียม Server (Cloud VPS)

1. **เลือกและสมัคร VPS**: 
   - แนะนำ **DigitalOcean (Droplet)** หรือ **Azure/AWS (Free Tier)**
   - สเปคแนะนำ: 
     - OS: **Ubuntu 22.04 LTS** (มาตรฐานสุดและเสถียรสุด)
     - CPU: 1-2 Cores
     - RAM: 1-2 GB (กำลังดีสำหรับการรันบอท + Dashboard)
2. **ต่อเข้า Server (SSH)**:
   - เปิดโปรแกรม Terminal (Mac) หรือ PowerShell (Windows) ในเครื่องคอมของคุณ
   - พิมพ์คำสั่ง: `ssh root@<IP_ADDRESS_ของเซิร์ฟเวอร์คุณ>` (แทนที่ `<IP_...>` ด้วยเลข IP จริงที่ได้มา)

---

## 🛠️ Step 2: ติดตั้ง Docker ลงบน Server

หลังจากที่คุณ Login เข้า Server สำเร็จแล้ว (หน้าจอจะขึ้นขีดกระพริบให้พิมพ์คำสั่งบัญชาการ) ให้รันสคริปต์อัตโนมัติต่อไปนี้เพื่อติดตั้ง Docker และ Docker Compose:

```bash
# 1. รันคำสั่งอัปเดตระบบ
sudo apt update && sudo apt upgrade -y

# 2. ดาวน์โหลดและติดตั้ง Docker ด้วย Script ทางการ
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 3. ติดตั้ง Docker Compose
sudo apt-get install docker-compose-plugin -y
```
เมื่อเสร็จแล้ว เช็คว่าลงสำเร็จไหมด้วยคำสั่ง `docker --version`

---

## 📥 Step 3: นำโค้ด SENTRY ขึ้นไปบน Server

วิธีที่ง่ายและเป็นมืออาชีพที่สุดคือใช้ **Git**:

1. **เอาโค้ดขึ้น GitHub แบบส่วนตัว (Private Repo)** (ทำในคอมเครื่องหลักคุณก่อน)
   - ปัจจุบันโปรเจกต์คุณเป็น Git repository แล้ว นำโค้ดทั้งโฟลเดอร์ดันขึ้นไปไว้บน GitHub (ให้ตั้งค่าโปรเจกต์เป็น Private เพื่อความปลอดภัย)

2. **ดึงอัปเดตลงมาบน Server ของคุณ (Clone)**
   เปิดหน้าจอ Terminal ของ Server แล้วพิมพ์:
   ```bash
   git clone <URL_GitHub_ของคุณ>
   cd SENTRY_TRADER
   ```

---

## 🔑 Step 4: ใส่กุญแจ API Keys (จุดสำคัญ)

เนื่องจากหน้าจอ Server เป็นหน้าจอดำๆ ล้วน เราจะใช้โปรแกรมแต่งข้อความชื่อ `nano` ในการสร้างไฟล์ `.env` แทน

1. พิมพ์คำสั่ง:
   ```bash
   nano .env
   ```
2. ก๊อปปี้เนื้อหาจากไฟล์ `.env.example` ปกติของคุณ แล้วมาแก้ใส่ API Keys จิรงๆ แล้ววางลงไป (คลิกขวา = วาง) ตัวอย่าง:
   ```env
   BINANCE_API_KEY=YOUR_TESTNET_API_KEY_HERE
   BINANCE_API_SECRET=YOUR_TESTNET_SECRET_KEY_HERE
   TRADING_MODE=testnet
   TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_TOKEN
   TELEGRAM_CHAT_ID=YOUR_CHAT_ID
   DATABASE_URL=sqlite+aiosqlite:///./database/sentry_trader.db
   ```
3. บันทึกไฟล์โดยกด `Ctrl+O` ตามด้วย `Enter` และกด `Ctrl+X` เพื่อออกกลับมาหน้าปกติ

---

## 🚀 Step 5: จุดระเบิด SENTRY! (Start the Bot)

เมื่อเตรียมทุกอย่างเสร็จแล้ว สั่งให้ Docker สร้างบ้านและปล่อยบอทลงปาร์ตี้ได้เลย:

```bash
docker-compose up -d --build
```
ระบบจะใช้เวลาประมาณ 1-3 นาทีในการโหลดไลบรารี

**วิชาเช็คสเตตัสบอท:**
- ดูว่าบอททำงานเป็นปกติไหม: 
  `docker-compose logs -f sentry_trader_bot` (กด Ctrl+C เพื่อลบหน้าจอออกโดยที่บอทยังทำงานอยู่)
- ดู Dashboard ของคุณได้ที่:
  เปิด Browser มือถือ หรือคอมเครื่องหลัก แล้วเข้า URL: `http://<IP_ADDRESS_เซิร์ฟเวอร์ของคุณ>:8501`

---

## 🔄 คำสั่งที่มีประโยชน์อื่นๆ (เผื่อใช้งานในอนาคต)

- **หยุดบอท** (เพื่ออัปเดตหรือซ่อมบำรุง):
  `docker-compose down`
- **กรณีคุณแก้โค้ดใหม่บางส่วน (Git pull แบบด่วน)**:
  ```bash
  git pull
  docker-compose up -d --build
  ```
- **อัปเดตไฟล์ AI (นำไฟล์ Random Forest ตัวใหม่เข้า)**:
  ก๊อปปี้ไฟล์ `.joblib` ใหม่ของคุณเข้าไปทับในโฟลเดอร์ `/brain_center/models/` ข้อมูลจะทะลุเข้า Container ไม่ต้อง Rebuild!

🎉 **เสร็จสิ้นครับ! ยินดีต้อนรับบอท 24 ชม. เครื่องแรกของคุณ!**
