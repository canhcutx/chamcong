import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from discord import app_commands, ui
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timezone, timedelta

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))

# Mức lương cố định / 1 giờ (VNĐ)
HOURLY_RATE = 15000

# --- TẠO WEB SERVER ĐỂ CHẠY RENDER WEB SERVICE FREE ---
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Discord is running!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- KẾT NỐI GOOGLE SHEETS ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open("Mechanic2.0").worksheet("Chấm công NPC")

# --- CẤU HÌNH BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- CÁC HÀM XỬ LÝ GOOGLE SHEET TỰ ĐỘNG ---
def get_active_session_from_sheet():
    """Kiểm tra dòng đang Online (Cột F / Chỉ số 5 = 'ON')"""
    all_values = sheet.get_all_values()
    if len(all_values) <= 1:
        return None, None
        
    for idx, row in enumerate(all_values[1:], start=2):
        if len(row) >= 6 and str(row[5]).strip().upper() == "ON":
            return idx, row
    return None, None

def save_checkin_sheet(user_id, gacha_id, user_name, date_str, start_time, timestamp_start):
    """Ghi nhận lượt báo Online mới vào Sheet (Cột A->H)"""
    sheet.append_row([
        str(user_id),       # A (0): User ID
        str(gacha_id),      # B (1): ID Gacha
        user_name,          # C (2): Tên
        date_str,           # D (3): Ngày Bắt Đầu
        f"{start_time}|{timestamp_start}", # E (4): Giờ On
        "ON",               # F (5): Giờ Off
        0,                  # G (6): Tổng Giờ
        0                   # H (7): Tổng Lương
    ])

def update_checkout_sheet(row_idx, end_time, hours, salary):
    """Cập nhật Giờ Off, Tổng Giờ và Tổng Lương"""
    sheet.update_cell(row_idx, 6, end_time) # Cột F: Giờ Off
    sheet.update_cell(row_idx, 7, round(hours, 2)) # Cột G: Tổng Giờ
    sheet.update_cell(row_idx, 8, round(salary))   # Cột H: Tổng Lương

def get_user_total_hours(user_id):
    """Tính tổng giờ làm và tổng lương của 1 user"""
    all_values = sheet.get_all_values()
    total_hours = 0.0
    total_salary = 0.0
    user_records = []

    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) >= 7 and str(row[0]).strip() == str(user_id):
                try:
                    hours_val = str(row[6]).replace(",", ".")
                    hours = float(hours_val)
                    salary = hours * HOURLY_RATE
                    
                    total_hours += hours
                    total_salary += salary
                    
                    if str(row[5]).strip().upper() != "ON":
                        start_time_clean = str(row[4]).split("|")[0] if "|" in str(row[4]) else str(row[4])
                        user_records.append({
                            "Ngày": row[3],
                            "Giờ On": start_time_clean,
                            "Giờ Off": row[5],
                            "Tổng Giờ": round(hours, 2),
                            "Lương": round(salary)
                        })
                except ValueError:
                    continue

    return round(total_hours, 2), round(total_salary), user_records

def get_all_users_summary():
    """Thống kê toàn bộ thành viên (Tổng giờ & Tổng lương)"""
    all_values = sheet.get_all_values()
    summary = {}

    if len(all_values) > 1:
        for row in all_values[1:]:
            if len(row) < 3:
                continue
                
            u_id = str(row[0]).strip()
            u_name = str(row[2]).strip()
            
            if not u_id:
                continue

            try:
                hours_val = str(row[6]).replace(",", ".") if len(row) >= 7 else "0"
                hours = float(hours_val)
            except ValueError:
                hours = 0.0

            salary = hours * HOURLY_RATE

            if u_id not in summary:
                summary[u_id] = {
                    "name": u_name if u_name else "Không tên",
                    "total_hours": 0.0,
                    "total_salary": 0.0,
                    "count": 0
                }

            summary[u_id]["total_hours"] += hours
            summary[u_id]["total_salary"] += salary
            
            if len(row) >= 6 and str(row[5]).strip().upper() != "ON":
                summary[u_id]["count"] += 1
                
            if u_name and summary[u_id]["name"] in ["Không tên", "Thành viên"]:
                summary[u_id]["name"] = u_name

    return summary

# --- MODAL BÁO ONLINE ---
class CheckInModal(ui.Modal, title="Báo giờ Online (Tự động)"):
    gacha_input = ui.TextInput(label="ID Gacha", placeholder="Nhập ID Gacha của bạn...")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        gacha_id = self.gacha_input.value

        row_idx, active_row = get_active_session_from_sheet()
        
        if active_row:
            current_user_id = str(active_row[0])
            current_name = str(active_row[2]) if len(active_row) >= 3 and active_row[2] else "Thành viên khác"
            start_display = str(active_row[4]).split("|")[0] if "|" in str(active_row[4]) else str(active_row[4])

            if current_user_id == str(user_id):
                await interaction.response.send_message(
                    f"⚠️ Bạn đã báo Online rồi (lúc `{start_display}`). Hãy báo Offline trước khi check-in lại!",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"❌ **Khung giờ này đã có người làm việc!**\n"
                f"👤 **{current_name}** đang online từ lúc `{start_display}`.\n"
                f"Bạn không thể báo Online cho tới khi ca hiện tại kết thúc.",
                ephemeral=True
            )
            return

        now_vn = datetime.now(VN_TZ)
        today_str = now_vn.strftime("%Y-%m-%d")
        start_time_str = now_vn.strftime("%H:%M")
        current_timestamp = int(time.time())

        save_checkin_sheet(user_id, gacha_id, user_name, today_str, start_time_str, current_timestamp)

        await interaction.response.send_message(
            f"✅ **{user_name}** (ID Gacha: `{gacha_id}`) đã báo Online lúc `{start_time_str}`! *(Tin nhắn tự xóa sau 10s)*", 
            ephemeral=False
        )
        msg = await interaction.original_response()
        await msg.delete(delay=10)

# --- BẢNG ĐIỀU KHIỂN NÚT BẤM ---
class TimekeepingView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🟢 Báo Online", style=discord.ButtonStyle.success, custom_id="btn_checkin")
    async def checkin_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CheckInModal())

    @ui.button(label="🔴 Báo Offline", style=discord.ButtonStyle.danger, custom_id="btn_checkout")
    async def checkout_button(self, interaction: discord.Interaction, button: ui.Button):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        
        row_idx, active_row = get_active_session_from_sheet()

        if not active_row or str(active_row[0]) != str(user_id):
            await interaction.response.send_message(
                "⚠️ Bạn chưa báo Online (hoặc ca làm đang thuộc về người khác) nên không thể báo Offline!", 
                ephemeral=True
            )
            return

        now_vn = datetime.now(VN_TZ)
        end_time_str = now_vn.strftime("%H:%M")
        current_timestamp = int(time.time())

        raw_start = str(active_row[4])
        if "|" in raw_start:
            start_display, start_ts = raw_start.split("|")
            start_timestamp = int(start_ts)
        else:
            start_display = raw_start
            start_timestamp = current_timestamp

        elapsed_seconds = max(0, current_timestamp - start_timestamp)
        hours = elapsed_seconds / 3600.0
        salary = hours * HOURLY_RATE

        try:
            update_checkout_sheet(row_idx, end_time_str, hours, salary)
            await interaction.response.send_message(
                f"📝 **{user_name}** đã báo Offline lúc `{end_time_str}` (Bắt đầu: `{start_display}`).\n"
                f"⏳ **Thời gian làm:** `{hours:.2f} giờ` ({int(elapsed_seconds//60)} phút).\n"
                f"💵 **Lương ca này:** `{salary:,.0f} VNĐ`\n"
                f"📊 *Dữ liệu đã được lưu vào Google Sheet! (Tin nhắn tự xóa sau 10s)*",
                ephemeral=False
            )
            msg = await interaction.original_response()
            await msg.delete(delay=10)
        except Exception as e:
            await interaction.response.send_message(
                f"⚠️ Lỗi khi cập nhật Google Sheet: {e}",
                ephemeral=True
            )

    @ui.button(label="👀 Ai đang Online?", style=discord.ButtonStyle.secondary, custom_id="btn_who_online")
    async def who_online_button(self, interaction: discord.Interaction, button: ui.Button):
        _, active_row = get_active_session_from_sheet()
        if not active_row:
            await interaction.response.send_message("🟢 Hiện tại **không có ai** đang trong ca làm việc.", ephemeral=True)
        else:
            name = str(active_row[2]) if len(active_row) >= 3 and active_row[2] else "Thành viên"
            gacha_id = str(active_row[1]) if len(active_row) >= 2 and active_row[1] else "N/A"
            raw_start = active_row[4] if len(active_row) >= 5 else "N/A"
            start_display = str(raw_start).split("|")[0] if "|" in str(raw_start) else str(raw_start)
            
            msg = f"📌 **Hiện tại đang Online:** 👤 **{name}** (ID Gacha: `{gacha_id}`) - Bắt đầu lúc `{start_display}`"
            await interaction.response.send_message(msg, ephemeral=True)

# --- LỆNH TẠO BẢNG ĐIỀU KHIỂN CHẤM CÔNG ---
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_bot(ctx):
    embed = discord.Embed(
        title="⏰ BẢNG CHẤM CÔNG VÀ QUẢN LÝ CA LÀM",
        description=(
            f"• Lương cơ bản: **{HOURLY_RATE:,.0f} VNĐ / 1 giờ**\n"
            "• Nhấn **Báo Online** để bắt đầu ca (Nhập ID Gacha - Thời gian tự động).\n"
            "• Nhấn **Báo Offline** để kết thúc ca (Tự động tính giờ & lương).\n"
            "• Nhấn **Ai đang Online?** để kiểm tra ca hiện tại."
        ),
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=TimekeepingView())

# --- LỆNH TRA CỨU CÁ NHÂN (/tracuu @tênthànhviên) ---
@bot.tree.command(name="tracuu", description="[Admin] Tra cứu tổng giờ & lương chấm công của 1 thành viên")
@app_commands.checks.has_permissions(administrator=True)
async def tracuu(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        total_hours, total_salary, user_records = get_user_total_hours(member.id)

        if not user_records:
            await interaction.followup.send(
                f"❌ Chưa có dữ liệu chấm công hoàn tất nào trên Google Sheet cho **{member.display_name}**.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📊 Báo Cáo Chấm Công: {member.display_name}",
            color=discord.Color.green()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)

        embed.add_field(name="👤 Thành viên", value=member.mention, inline=True)
        embed.add_field(name="📅 Số buổi làm", value=f"`{len(user_records)} buổi`", inline=True)
        embed.add_field(name="⏳ TỔNG GIỜ LÀM", value=f"**{total_hours:.2f} giờ**", inline=False)
        embed.add_field(name="💰 TỔNG LƯƠNG TẠM TÍNH", value=f"**{total_salary:,.0f} VNĐ**", inline=False)

        recent_str = ""
        for r in user_records[-3:]:
            recent_str += f"• `{r.get('Ngày')}`: {r.get('Giờ On')} ➔ {r.get('Giờ Off')} (**{r.get('Tổng Giờ')}h** | {r.get('Lương'):,.0f}đ)\n"
        
        embed.add_field(name="📝 Ca làm gần đây", value=recent_str or "Không có", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ Lỗi khi đọc Google Sheet: {e}", ephemeral=True)

# --- LỆNH TỔNG HỢP TOÀN BỘ THÀNH VIÊN (/tonghop) ---
@bot.tree.command(name="tonghop", description="[Admin] Báo cáo tổng hợp toàn bộ giờ làm và tiền lương")
@app_commands.checks.has_permissions(administrator=True)
async def tonghop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        summary_data = get_all_users_summary()

        if not summary_data:
            await interaction.followup.send("❌ Dữ liệu trên Google Sheet hiện đang trống.", ephemeral=True)
            return

        sorted_summary = sorted(summary_data.values(), key=lambda x: x["total_hours"], reverse=True)

        embed = discord.Embed(
            title="📋 BẢNG TỔNG HỢP GIỜ & LƯƠNG THÀNH VIÊN",
            description=f"Cập nhật lúc: `{datetime.now(VN_TZ).strftime('%H:%M - %d/%m/%Y')}`\nĐơn giá: `{HOURLY_RATE:,.0f} VNĐ/giờ`",
            color=discord.Color.gold()
        )

        grand_total_hours = 0.0
        grand_total_salary = 0.0
        list_content = ""

        for idx, u_info in enumerate(sorted_summary, start=1):
            grand_total_hours += u_info["total_hours"]
            grand_total_salary += u_info["total_salary"]
            list_content += f"**{idx}. {u_info['name']}**: `{u_info['total_hours']:.2f}h` ➔ **{u_info['total_salary']:,.0f} VNĐ** ({u_info['count']} buổi)\n"

        embed.add_field(name="👥 Danh sách chi tiết", value=list_content, inline=False)
        embed.add_field(
            name="📊 Thống kê chung", 
            value=f"• **Tổng số thành viên:** `{len(sorted_summary)} người`\n"
                  f"• **Tổng số giờ làm:** `{grand_total_hours:.2f} giờ`\n"
                  f"• **TỔNG CHI PHÍ LƯƠNG:** **{grand_total_salary:,.0f} VNĐ**", 
            inline=False
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ Đã xảy ra lỗi khi tổng hợp dữ liệu: {e}", ephemeral=True)

# --- SỰ KIỆN KHỞI ĐỘNG BOT ---
@bot.event
async def on_ready():
    bot.add_view(TimekeepingView())
    await bot.tree.sync()
    print(f"Bot {bot.user.name} đã kết nối và đồng bộ thành công!")

token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("❌ Lỗi: Chưa cấu hình DISCORD_TOKEN!")
