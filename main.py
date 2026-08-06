import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from discord import app_commands, ui
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

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
    """Kiểm tra trên Sheet xem có ai đang On (Giờ Off = 'ON') hay không"""
    records = sheet.get_all_records()
    for idx, row in enumerate(records, start=2): # Dòng 2 bắt đầu dữ liệu
        if str(row.get("Giờ Off", "")).upper() == "ON":
            return idx, row
    return None, None

def save_checkin_sheet(user_id, user_name, date_str, start_time):
    """Ghi nhận lượt báo Online mới vào Sheet"""
    sheet.append_row([
        str(user_id),
        user_name,
        date_str,
        start_time,
        "ON", # Đánh dấu đang Online
        0
    ])

def update_checkout_sheet(row_idx, end_time, hours):
    """Cập nhật Giờ Off và Tổng Giờ cho dòng đang Online"""
    sheet.update_cell(row_idx, 5, end_time) # Cột 5 là 'Giờ Off'
    sheet.update_cell(row_idx, 6, hours)    # Cột 6 là 'Tổng Giờ'

def get_user_total_hours(user_id):
    """Tính tổng giờ làm của 1 user"""
    records = sheet.get_all_records()
    total_hours = 0.0
    user_records = []

    for row in records:
        if str(row.get("User ID")) == str(user_id):
            try:
                hours_val = str(row.get("Tổng Giờ", 0)).replace(",", ".")
                hours = float(hours_val)
                total_hours += hours
                if str(row.get("Giờ Off")) != "ON":
                    user_records.append(row)
            except ValueError:
                continue

    return total_hours, user_records

def get_all_users_summary():
    """Lấy tổng hợp danh sách giờ làm của tất cả thành viên"""
    records = sheet.get_all_records()
    summary = {}

    for row in records:
        u_id = str(row.get("User ID", "")).strip()
        # Đọc linh hoạt các tiêu đề cột Tên
        u_name = str(row.get("Tên", "")).strip() or str(row.get("Tên Member", "")).strip() or str(row.get("User", "")).strip()
        
        if not u_id:
            continue

        try:
            hours_val = str(row.get("Tổng Giờ", 0)).replace(",", ".")
            hours = float(hours_val)
        except ValueError:
            hours = 0.0

        if u_id not in summary:
            summary[u_id] = {
                "name": u_name if u_name else "Không tên",
                "total_hours": 0.0,
                "count": 0
            }

        summary[u_id]["total_hours"] += hours
        if str(row.get("Giờ Off")) != "ON":
            summary[u_id]["count"] += 1
            
        if u_name and summary[u_id]["name"] == "Không tên":
            summary[u_id]["name"] = u_name

    return summary

# --- MODAL BÁO ONLINE ---
class CheckInModal(ui.Modal, title="Báo giờ Online"):
    time_input = ui.TextInput(label="Giờ bắt đầu (VD: 15h00)", placeholder="15h00")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        start_time = self.time_input.value

        # Kiểm tra ca làm trực tiếp từ Google Sheet
        row_idx, active_row = get_active_session_from_sheet()
        
        if active_row:
            current_user_id = str(active_row.get("User ID"))
            current_name = active_row.get("Tên") or active_row.get("Tên Member") or "Thành viên khác"
            
            if current_user_id == str(user_id):
                await interaction.response.send_message(
                    f"⚠️ Bạn đã báo Online rồi (lúc `{active_row.get('Giờ On')}`). Hãy báo Offline trước khi check-in lại!",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"❌ **Khung giờ này đã có người làm việc!**\n"
                f"👤 **{current_name}** đang online từ lúc `{active_row.get('Giờ On')}`.\n"
                f"Bạn không thể báo Online cho tới khi ca hiện tại kết thúc.",
                ephemeral=True
            )
            return

        today = datetime.now().strftime("%Y-%m-%d")
        save_checkin_sheet(user_id, user_name, today, start_time)

        # Gửi thông báo công khai và tự động xóa sau 10 giây
        await interaction.response.send_message(
            f"✅ **{user_name}** đã báo Online lúc `{start_time}`! *(Tin nhắn tự xóa sau 10s)*", 
            ephemeral=False
        )
        msg = await interaction.original_response()
        await msg.delete(delay=10)

# --- MODAL BÁO OFFLINE ---
class CheckOutModal(ui.Modal, title="Báo giờ Offline"):
    time_input = ui.TextInput(label="Giờ kết thúc (VD: 18h30)", placeholder="18h30")
    hours_input = ui.TextInput(label="Tổng số giờ làm (VD: 3.5)", placeholder="3.5")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        end_time = self.time_input.value
        
        row_idx, active_row = get_active_session_from_sheet()

        if not active_row or str(active_row.get("User ID")) != str(user_id):
            await interaction.response.send_message(
                "⚠️ Bạn chưa báo Online (hoặc ca làm đang thuộc về người khác) nên không thể báo Offline!", 
                ephemeral=True
            )
            return

        try:
            hours = float(self.hours_input.value.replace("h", ".").replace("g", "."))
        except ValueError:
            hours = 0.0

        start_time = active_row.get("Giờ On")
        
        try:
            update_checkout_sheet(row_idx, end_time, hours)
            await interaction.response.send_message(
                f"📝 **{user_name}** đã báo Offline lúc `{end_time}` (Bắt đầu: `{start_time}`). Tổng: **{hours} giờ**.\n"
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

# --- BẢNG ĐIỀU KHIỂN NÚT BẤM ---
class TimekeepingView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🟢 Báo Online", style=discord.ButtonStyle.success, custom_id="btn_checkin")
    async def checkin_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CheckInModal())

    @ui.button(label="🔴 Báo Offline", style=discord.ButtonStyle.danger, custom_id="btn_checkout")
    async def checkout_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(CheckOutModal())

    @ui.button(label="👀 Ai đang Online?", style=discord.ButtonStyle.secondary, custom_id="btn_who_online")
    async def who_online_button(self, interaction: discord.Interaction, button: ui.Button):
        _, active_row = get_active_session_from_sheet()
        if not active_row:
            await interaction.response.send_message("🟢 Hiện tại **không có ai** đang trong ca làm việc.", ephemeral=True)
        else:
            name = active_row.get("Tên") or active_row.get("Tên Member") or "Thành viên"
            start_time = active_row.get("Giờ On")
            msg = f"📌 **Hiện tại đang Online:** 👤 **{name}** (Bắt đầu lúc `{start_time}`)"
            await interaction.response.send_message(msg, ephemeral=True)

# --- LỆNH TẠO BẢNG ĐIỀU KHIỂN CHẤM CÔNG ---
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_bot(ctx):
    embed = discord.Embed(
        title="⏰ BẢNG CHẤM CÔNG VÀ QUẢN LÝ CA LÀM",
        description=(
            "• Nhấn **Báo Online** để bắt đầu ca (Bot sẽ chặn nếu đã có người khác đang Online).\n"
            "• Nhấn **Báo Offline** để kết thúc ca và ghi vào Google Sheet.\n"
            "• Nhấn **Ai đang Online?** để kiểm tra ca hiện tại."
        ),
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=TimekeepingView())

# --- LỆNH TRA CỨU CÁ NHÂN (/tracuu @tênthànhviên) ---
@bot.tree.command(name="tracuu", description="[Admin] Tra cứu tổng giờ chấm công của 1 thành viên bất kỳ")
@app_commands.checks.has_permissions(administrator=True)
async def tracuu(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer(ephemeral=True)

    try:
        total_hours, user_records = get_user_total_hours(member.id)

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
        embed.add_field(name="⏳ TỔNG GIỜ LÀM", value=f"**{total_hours:.1f} giờ**", inline=False)

        recent_str = ""
        for r in user_records[-3:]:
            recent_str += f"• `{r.get('Ngày')}`: {r.get('Giờ On')} ➔ {r.get('Giờ Off')} (**{r.get('Tổng Giờ')}h**)\n"
        
        embed.add_field(name="📝 Ca làm gần đây", value=recent_str or "Không có", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"⚠️ Lỗi khi đọc Google Sheet: {e}", ephemeral=True)

# --- LỆNH TỔNG HỢP TOÀN BỘ THÀNH VIÊN (/tonghop) ---
@bot.tree.command(name="tonghop", description="[Admin] Báo cáo tổng hợp toàn bộ giờ làm của tất cả thành viên")
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
            title="📋 BẢNG TỔNG HỢP GIỜ CHẤM CÔNG THÀNH VIÊN",
            description=f"Cập nhật lúc: `{datetime.now().strftime('%H:%M - %d/%m/%Y')}`",
            color=discord.Color.gold()
        )

        grand_total_hours = 0.0
        list_content = ""

        for idx, u_info in enumerate(sorted_summary, start=1):
            grand_total_hours += u_info["total_hours"]
            list_content += f"**{idx}. {u_info['name']}**: `{u_info['total_hours']:.1f} giờ` ({u_info['count']} buổi)\n"

        embed.add_field(name="👥 Danh sách chi tiết", value=list_content, inline=False)
        embed.add_field(
            name="📊 Thống kê chung", 
            value=f"• **Tổng số thành viên:** `{len(sorted_summary)} người`\n"
                  f"• **Tổng cộng toàn máy chủ:** `{grand_total_hours:.1f} giờ`", 
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
