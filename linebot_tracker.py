"""
LINE Bot - Product Tracker
บันทึกสินค้า: ชื่อ | ราคา | โปรตีน | น้ำหนัก | แคลอรี่
คำนวณอัตโนมัติ: โปรตีน/บาท, ราคา/กรัม
"""

import os
import re
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ─── ตั้งค่า LINE ─────────────────────────────────────────────
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ─── ตั้งค่า Google Sheets ────────────────────────────────────
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = "products"

def get_sheet():
    """เชื่อมต่อ Google Sheets"""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    return sheet


def ensure_header(sheet):
    """สร้าง header row ถ้ายังไม่มี"""
    headers = [
        "วันที่-เวลา", "ชื่อสินค้า", "ราคา (฿)",
        "โปรตีน (g)", "น้ำหนัก (g)", "แคลอรี่ (kcal)",
        "โปรตีน/บาท (g/฿)", "ราคา/กรัม (฿/g)", "รูปภาพ URL"
    ]
    if sheet.row_values(1) != headers:
        sheet.insert_row(headers, 1)


# ─── Parser: แยก format "+ ชื่อ ราคา โปรตีน น้ำหนัก แคลอรี่" ──
def parse_product(text: str):
    """
    รับข้อความเช่น: + Whey Gold 590 25 907 120
    คืนค่า dict หรือ None ถ้า format ไม่ถูก
    """
    text = text.strip()
    if not text.startswith("+"):
        return None

    # ตัด + ออก แล้ว strip
    body = text[1:].strip()

    # แยกตัวเลขท้าย 4 ตัวออกจากชื่อ
    # pattern: ชื่อสินค้า (อาจมีหลายคำ) ตามด้วยตัวเลข 4 ตัว
    pattern = r"^(.+?)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)$"
    match = re.match(pattern, body)
    if not match:
        return None

    name = match.group(1).strip()
    try:
        price    = float(match.group(2))   # ราคา ฿
        protein  = float(match.group(3))   # โปรตีน g
        weight   = float(match.group(4))   # น้ำหนักรวม g
        calories = float(match.group(5))   # แคลอรี่ kcal
    except ValueError:
        return None

    # คำนวณ metric
    protein_per_baht = round(protein / price, 4) if price > 0 else 0
    price_per_gram   = round(price / weight, 4)  if weight > 0 else 0

    return {
        "name":             name,
        "price":            price,
        "protein":          protein,
        "weight":           weight,
        "calories":         calories,
        "protein_per_baht": protein_per_baht,
        "price_per_gram":   price_per_gram,
    }


def save_to_sheet(sheet, product: dict, image_url: str = ""):
    """บันทึกข้อมูลสินค้าลง Google Sheets"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    row = [
        now,
        product["name"],
        product["price"],
        product["protein"],
        product["weight"],
        product["calories"],
        product["protein_per_baht"],
        product["price_per_gram"],
        image_url,
    ]
    sheet.append_row(row)


def format_reply(product: dict) -> str:
    """สร้างข้อความตอบกลับหลังบันทึก"""
    return (
        f"บันทึกแล้ว: {product['name']}\n"
        f"ราคา: {product['price']:.0f} ฿  |  น้ำหนัก: {product['weight']:.0f} g\n"
        f"โปรตีน: {product['protein']:.1f} g  |  แคลอรี่: {product['calories']:.0f} kcal\n"
        f"──────────────────\n"
        f"โปรตีน/บาท: {product['protein_per_baht']:.4f} g/฿\n"
        f"ราคา/กรัม:  {product['price_per_gram']:.4f} ฿/g"
    )


# ─── คำสั่ง: สรุป / top ───────────────────────────────────────
def cmd_summary(sheet) -> str:
    """สรุปสินค้าทั้งหมดในรูปแบบตาราง"""
    records = sheet.get_all_records()
    if not records:
        return "ยังไม่มีสินค้าที่บันทึก"

    lines = [f"สินค้าทั้งหมด {len(records)} รายการ\n"]
    for r in records[-10:]:  # แสดง 10 รายการล่าสุด
        lines.append(
            f"• {r['ชื่อสินค้า']}  "
            f"{float(r['ราคา (฿)']):.0f}฿  "
            f"pro/฿ {float(r['โปรตีน/บาท (g/฿)']):.3f}"
        )
    return "\n".join(lines)


def cmd_top(sheet, sort_by: str) -> str:
    """top โปรตีน หรือ top ราคา/g"""
    records = sheet.get_all_records()
    if not records:
        return "ยังไม่มีข้อมูล"

    if "โปรตีน" in sort_by:
        key = "โปรตีน/บาท (g/฿)"
        label = "โปรตีน/บาท"
        unit = "g/฿"
        reverse = True
    else:
        key = "ราคา/กรัม (฿/g)"
        label = "ราคา/กรัม"
        unit = "฿/g"
        reverse = False  # ถูกที่สุดขึ้นก่อน

    sorted_rec = sorted(records, key=lambda r: float(r[key]), reverse=reverse)[:5]
    lines = [f"Top 5 {label}\n"]
    for i, r in enumerate(sorted_rec, 1):
        lines.append(
            f"{i}. {r['ชื่อสินค้า']}  "
            f"{float(r[key]):.4f} {unit}"
        )
    return "\n".join(lines)


HELP_TEXT = """คำสั่งที่ใช้ได้:
+ ชื่อ ราคา โปรตีน น้ำหนัก แคลอรี่
  → บันทึกสินค้าใหม่

สรุป         → 10 รายการล่าสุด
top โปรตีน  → เรียงโปรตีน/บาท
top ราคา/g  → เรียงราคา/กรัม
ช่วย         → แสดงคำสั่งนี้"""


# ─── Webhook endpoint ─────────────────────────────────────────
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    text = event.message.text.strip()
    reply = ""

    try:
        sheet = get_sheet()
        ensure_header(sheet)

        if text.startswith("+"):
            product = parse_product(text)
            if product:
                save_to_sheet(sheet, product)
                reply = format_reply(product)
            else:
                reply = (
                    "format ไม่ถูกต้อง\n"
                    "ตัวอย่าง: + Whey Gold 590 25 907 120\n"
                    "          + ชื่อ ราคา โปรตีน น้ำหนัก แคลอรี่"
                )

        elif text == "สรุป":
            reply = cmd_summary(sheet)

        elif text.startswith("top"):
            reply = cmd_top(sheet, text)

        elif text in ("ช่วย", "help", "?"):
            reply = HELP_TEXT

        else:
            reply = 'พิมพ์ "ช่วย" เพื่อดูคำสั่งทั้งหมด'

    except Exception as e:
        reply = f"เกิดข้อผิดพลาด: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    """รับรูปภาพ — เก็บ message_id ไว้รอข้อความ + ในขั้นถัดไป"""
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="รับรูปแล้ว! ตอนนี้พิมพ์ข้อมูลสินค้า:\n+ ชื่อ ราคา โปรตีน น้ำหนัก แคลอรี่"
        )
    )


# ─── Run ──────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
