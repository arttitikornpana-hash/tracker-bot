"""
LINE Bot - Price Tracker v4
Flow: เลือก category → ส่งรูป (optional) → พิมพ์ข้อมูล
"""

import os
import re
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageMessage, QuickReply, QuickReplyButton, MessageAction
)
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_CHANNEL_SECRET      = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN= os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SPREADSHEET_ID           = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = "prices"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler      = WebhookHandler(LINE_CHANNEL_SECRET)

CATEGORIES = ["นม/โปรตีน","ของสด","ของแห้ง","ของใช้","เครื่องดื่ม","ขนม","สุขภาพ","อื่นๆ"]

# state: user_id → {"step": "wait_image"|"wait_product", "category": str, "image_id": str|None}
state = {}


# ─── Google Sheets ────────────────────────────────────────────
def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets",
              "https://www.googleapis.com/auth/drive"]
    creds  = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

def ensure_header(sheet):
    headers = ["วันที่-เวลา","ชื่อสินค้า","category",
               "ราคา (฿)","น้ำหนัก (g)","โปรตีน (g)",
               "ราคา/g (฿)","ราคา/โปรตีนg (฿)","ซื้อครั้งที่","image_id"]
    if sheet.row_values(1) != headers:
        sheet.insert_row(headers, 1)


# ─── Parser ───────────────────────────────────────────────────
def parse_product(text: str):
    """
    รับ:  "ยาสีฟัน 139 บาท 160g"
         "ไข่ไก่ 79 บาท 10 ฟอง"
    คืน: dict หรือ None
    """
    text = re.sub(r'กรัม','g', text.strip())
    text = re.sub(r'บาท','', text)
    text = text.strip()

    # โปรตีน optional: โปรตีน25g หรือ protein25g
    protein = 0.0
    m_prot = re.search(r'(?:โปรตีน|protein)\s*([\d.]+)\s*g', text, re.IGNORECASE)
    if m_prot:
        protein = float(m_prot.group(1))
        text = text[:m_prot.start()].strip()

    # ชื่อ ราคา ปริมาณ+หน่วย
    pattern = r'^(.+?)\s+([\d.]+)\s+([\d.]+)\s*([a-zA-Zก-๙]+)$'
    m = re.match(pattern, text.strip())
    if not m:
        return None

    name   = m.group(1).strip()
    price  = float(m.group(2))
    amount = float(m.group(3))
    unit   = m.group(4).strip()

    if amount <= 0:
        return None

    price_per_unit  = round(price / amount, 4)
    price_per_prot  = round(price / protein, 4) if protein > 0 else None

    return {
        "name":            name,
        "price":           price,
        "amount":          amount,
        "unit":            unit,
        "protein":         protein,
        "price_per_unit":  price_per_unit,
        "price_per_prot":  price_per_prot,
    }


# ─── ประวัติ ──────────────────────────────────────────────────
def get_history(sheet, name: str):
    records = sheet.get_all_records()
    return [r for r in records if str(r.get("ชื่อสินค้า","")).strip() == name.strip()]

def save_row(sheet, product, category, image_id, buy_count):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ppu  = product["price_per_unit"]
    ppro = product["price_per_prot"] or ""
    sheet.append_row([
        now, product["name"], category,
        product["price"], product["amount"], product["protein"] or "",
        ppu, ppro, buy_count, image_id or ""
    ])


# ─── ข้อความตอบกลับ ───────────────────────────────────────────
def format_result(product, category, history, image_id) -> str:
    name  = product["name"]
    price = product["price"]
    amt   = product["amount"]
    unit  = product["unit"]
    ppu   = product["price_per_unit"]
    ppro  = product["price_per_prot"]
    buy_n = len(history) + 1

    lines = [f"✅ บันทึกแล้ว: {name}  [{category}]", ""]

    lines.append(f"💸 ราคา/{unit}: {ppu:.2f} ฿")
    if ppro:
        lines.append(f"💪 ราคา/โปรตีนg: {ppro:.2f} ฿")
    lines.append("")

    if buy_n > 1:
        lines.append(f"📦 ซื้อซ้ำครั้งที่ {buy_n}")

        all_ppu  = [float(r["ราคา/g (฿)"]) for r in history if r.get("ราคา/g (฿)")]
        avg_ppu  = sum(all_ppu) / len(all_ppu)
        min_ppu  = min(all_ppu)
        last_ppu = float(history[-1].get("ราคา/g (฿)", ppu))
        diff     = ppu - last_ppu
        diff_pct = (diff / last_ppu * 100) if last_ppu > 0 else 0
        avg_diff_pct = ((ppu - avg_ppu) / avg_ppu * 100) if avg_ppu > 0 else 0

        # เทียบครั้งก่อน
        if diff > 0:
            lines.append(f"🔴 แพงขึ้น +{diff:.2f} ฿/{unit}  (+{diff_pct:.1f}%)  โดนฟัน!")
        elif diff < 0:
            lines.append(f"🟢 ถูกลง {abs(diff):.2f} ฿/{unit}  ({diff_pct:.1f}%)")
        else:
            lines.append("⚪ ราคาเท่าเดิม")

        # เทียบค่าเฉลี่ย
        if avg_diff_pct < -5:
            lines.append(f"🟢 ถูกกว่าค่าเฉลี่ย {abs(avg_diff_pct):.1f}%")
        elif avg_diff_pct > 5:
            lines.append(f"🔴 แพงกว่าค่าเฉลี่ย {avg_diff_pct:.1f}%")

        # ราคาถูกสุด?
        if ppu <= min_ppu:
            lines.append("🏆 นี่คือราคาถูกที่สุดที่เคยซื้อ!")

        lines.append(f"📊 ช่วงราคา: {min_ppu:.2f} – {max(all_ppu):.2f} ฿/{unit}")
    else:
        lines.append("📦 บันทึกครั้งแรก จะเปรียบเทียบได้ในครั้งถัดไป")

    return "\n".join(lines)


# ─── Quick Reply helpers ───────────────────────────────────────
def send_category_prompt(reply_token):
    buttons = [
        QuickReplyButton(action=MessageAction(label=c, text=f"__cat__{c}"))
        for c in CATEGORIES
    ]
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(
            text="เลือก Category สินค้าค่ะ 👇",
            quick_reply=QuickReply(items=buttons)
        )
    )

def send_after_category(reply_token):
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(
            text="ส่งรูปสินค้าได้เลยค่ะ 📸\n(หรือข้ามได้โดยพิมพ์ \"เพิ่มสินค้า\")",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="ข้ามรูป / เพิ่มสินค้า", text="เพิ่มสินค้า"))
            ])
        )
    )

def send_product_prompt(reply_token):
    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(
            text="พิมพ์ข้อมูลสินค้าได้เลยค่ะ ✨\n\nตัวอย่าง:\nAlly clear protein 47 บาท 30g โปรตีน25g\nยาสีฟัน 139 บาท 160g\nไข่ไก่ 79 บาท 10 ฟอง"
        )
    )


# ─── คำสั่งดูข้อมูล ───────────────────────────────────────────
def cmd_history_text(sheet, name: str) -> str:
    history = get_history(sheet, name)
    if not history:
        return f"ไม่พบประวัติ: {name}"
    lines = [f"ประวัติ: {name} ({len(history)} ครั้ง)\n"]
    for r in history[-5:]:
        date = str(r["วันที่-เวลา"])[:10]
        price= float(r["ราคา (฿)"])
        ppu  = float(r["ราคา/g (฿)"])
        unit = r["น้ำหนัก (g)"]
        lines.append(f"{date}  {price:.0f}฿  →  {ppu:.2f}฿/หน่วย")
    return "\n".join(lines)

def cmd_summary(sheet, cat_filter="") -> str:
    records = sheet.get_all_records()
    if cat_filter:
        records = [r for r in records if r.get("category","") == cat_filter]
    if not records:
        return "ไม่มีข้อมูล" + (f" ใน [{cat_filter}]" if cat_filter else "")
    latest = {}
    for r in records:
        latest[r["ชื่อสินค้า"]] = r
    lines = [f"{'['+cat_filter+'] ' if cat_filter else ''}สินค้า {len(latest)} ชนิด\n"]
    for name, r in list(latest.items())[-10:]:
        cat  = r.get("category","")
        ppu  = float(r.get("ราคา/g (฿)",0))
        price= float(r.get("ราคา (฿)",0))
        lines.append(f"• {name}  [{cat}]  {price:.0f}฿  ({ppu:.2f}฿/หน่วย)")
    return "\n".join(lines)

HELP_TEXT = """คำสั่งทั้งหมด:

📌 เริ่มบันทึก:
พิมพ์ "บันทึก" → เลือก category → ส่งรูป → พิมพ์ข้อมูล

📋 ดูข้อมูล:
สรุป              → ทั้งหมด
สรุป ของสด        → เฉพาะ category
ประวัติ ชื่อสินค้า → ราคาย้อนหลัง

📦 Format สินค้า:
ชื่อ ราคา บาท น้ำหนัก+หน่วย [โปรตีนXXg]
เช่น: Whey 590 บาท 907g โปรตีน25g
เช่น: ไข่ไก่ 79 บาท 10 ฟอง"""


# ─── Webhook ──────────────────────────────────────────────────
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature","")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id  = event.source.user_id
    image_id = event.message.id
    s = state.get(user_id, {})

    if s.get("step") == "wait_image":
        state[user_id]["image_id"] = image_id
        state[user_id]["step"]     = "wait_product"
        send_product_prompt(event.reply_token)
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text='รับรูปแล้วค่ะ! พิมพ์ "บันทึก" เพื่อเริ่มบันทึกสินค้า')
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):
    user_id = event.source.user_id
    text    = event.message.text.strip()
    s       = state.get(user_id, {})

    try:
        sheet = get_sheet()
        ensure_header(sheet)

        # ── เลือก category ──
        if text.startswith("__cat__"):
            cat = text.replace("__cat__","").strip()
            state[user_id] = {"step": "wait_image", "category": cat, "image_id": None}
            send_after_category(event.reply_token)
            return

        # ── ข้ามรูป / เพิ่มสินค้า ──
        if text == "เพิ่มสินค้า":
            if s.get("step") in ("wait_image", "wait_product"):
                state[user_id]["step"] = "wait_product"
                send_product_prompt(event.reply_token)
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text='กรุณาเลือก category ก่อนนะคะ พิมพ์ "บันทึก" เพื่อเริ่ม')
                )
            return

        # ── เริ่ม flow บันทึก ──
        if text == "บันทึก":
            send_category_prompt(event.reply_token)
            return

        # ── รับข้อมูลสินค้า ──
        if s.get("step") == "wait_product":
            product = parse_product(text)
            if product:
                cat      = s.get("category","อื่นๆ")
                image_id = s.get("image_id")
                history  = get_history(sheet, product["name"])
                buy_n    = len(history) + 1
                save_row(sheet, product, cat, image_id, buy_n)
                reply_text = format_result(product, cat, history, image_id)
                state.pop(user_id, None)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        text="ยังอ่านข้อมูลไม่ได้ค่ะ ลองใหม่นะคะ\n\nตัวอย่าง:\nAlly clear protein 47 บาท 30g โปรตีน25g\nยาสีฟัน 139 บาท 160g"
                    )
                )
            return

        # ── คำสั่งอื่นๆ ──
        if text.startswith("ประวัติ"):
            name = text.replace("ประวัติ","").strip()
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage(text=cmd_history_text(sheet, name)))

        elif text.startswith("สรุป"):
            arg = text.replace("สรุป","").strip()
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage(text=cmd_summary(sheet, arg)))

        elif text in ("ช่วย","help","?"):
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage(text=HELP_TEXT))

        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text='พิมพ์ "บันทึก" เพื่อเริ่มบันทึกสินค้า\nหรือ "ช่วย" เพื่อดูคำสั่งทั้งหมดค่ะ',
                    quick_reply=QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="บันทึกสินค้า", text="บันทึก")),
                        QuickReplyButton(action=MessageAction(label="สรุปทั้งหมด", text="สรุป")),
                        QuickReplyButton(action=MessageAction(label="ช่วยเหลือ", text="ช่วย")),
                    ])
                )
            )

    except Exception as e:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"เกิดข้อผิดพลาด: {str(e)}")
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
