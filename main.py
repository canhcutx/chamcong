import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- KẾT NỐI GOOGLE SHEETS ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

# Tự động kết nối tới credentials.json (ở máy cục bộ hoặc Secret File trên Render)
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

# ⚠️ ĐỔI TÊN NÀY THÀNH TÊN EXACT CỦA TỆP GOOGLE SHEET CỦA BẠN
sheet = client.open("Chấm công NPC").sheet1

# --- CẤU HÌNH BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Dictionary lưu tạm các ca đang làm việc (reset khi restart bot)
active_sessions = {} # {user_id: {"name": user_name, "start_time": start_time}}

def save_to_sheet(user_id, user_name, date_str, start_time, end_time, hours):
    """Hàm ghi 1 hàng mới vào Google Sheets"""
    sheet.append_row([
        str(user_id),
        user_name,
        date_str,
        start_time,
        end_time,
        hours
    ])

# --- MODAL BÁO ONLINE ---
class CheckInModal(ui.Modal, title="Báo giờ Online"):
    time_input = ui.TextInput(label="Giờ bắt đầu (VD: 15h00)", placeholder="15h00")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        start_time = self.time_input.value

        # Chặn nếu đã có người khác đang Online
        if active_sessions:
            current_user_id = list(active_sessions.keys())[0]
            current_info = active_sessions[current_user_id]
            
            if current_user_id == user_id:
                await interaction.response.send_message(
                    f"⚠️ Bạn đã báo Online rồi (lúc `{current_info['start_time']}`). Hãy báo Offline trước khi check-in lại!",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"❌ **Khung giờ này đã có người làm việc!**\n"
                f"👤 **{current_info['name']}** đang online từ lúc `{current_info['start_time']}`.\n"
                f"Bạn không thể báo Online cho tới khi ca làm hiện tại kết thúc.",
                ephemeral=True
            )
            return

        # Lưu phiên online
        active_sessions[user_id] = {
            "name": user_name,
            "start_time": start_time
        }

        await interaction.response.send_message(
            f"✅ **{user_name}** đã báo Online lúc `{start_time}`!", 
            ephemeral=False
        )

# --- MODAL BÁO OFFLINE ---
class CheckOutModal(ui.Modal, title="Báo giờ Offline"):
    time_input = ui.TextInput(label="Giờ kết thúc (VD: 18h30)", placeholder="18h30")
    hours_input = ui.TextInput(label="Tổng số giờ làm (VD: 3.5)", placeholder="3.5")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.display_name
        end_time = self.time_input.value
        
        if user_id not in active_sessions:
            await interaction.response.send_message(
                "⚠️ Bạn chưa báo Online nên không thể báo Offline!", 
                ephemeral=True
            )
            return

        try:
            hours = float(self.hours_input.value.replace("h", ".").replace("g", "."))
        except ValueError:
            hours = 0.0

        session_info = active_sessions.pop(user_id)
        start_time = session_info["start_time"]
        today = datetime.now().strftime("%Y-%m-%d")

        # Ghi trực tiếp vào Google Sheets
        try:
            save_to_sheet(user_id, user_name, today, start_time, end_time, hours)
            await interaction.response.send_message(
                f"📝 **{user_name}** đã báo Offline lúc `{end_time}` (Bắt đầu: `{start_time}`). Tổng: **{hours} giờ**.\n"
                f"📊 *Dữ liệu đã được lưu vào Google Sheet!*",
                ephemeral=False
            )
        except Exception as e:
            await interaction.response.send_message(
                f"⚠️ Đã lưu thông tin nhưng gặp lỗi kết nối Google Sheet: {e}",
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
        if not active_sessions:
            await interaction.response.send_message("🟢 Hiện tại **không có ai** đang trong ca làm việc.", ephemeral=True)
        else:
            msg = "**📌 Danh sách người đang Online hiện tại:**\n"
            for u_id, info in active_sessions.items():
                msg += f"- 👤 **{info['name']}** (Online từ lúc `{info['start_time']}`)\n"
            await interaction.response.send_message(msg, ephemeral=True)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_bot(ctx):
    """Lệnh để Admin hiển thị bảng nút bấm vào Channel"""
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

@bot.event
async def on_ready():
    bot.add_view(TimekeepingView())
    print(f"Bot {bot.user.name} đã kết nối và sẵn sàng làm việc!")

# Lấy Token từ biến môi trường của Render
token = os.getenv("DISCORD_TOKEN")
if token:
    bot.run(token)
else:
    print("❌ Lỗi: Chưa cấu hình DISCORD_TOKEN trong Environment Variable!")
