from datetime import date

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import OWNER_ID
import database as db
from services import excel

router = Router()
router.message.filter(F.chat.type == "private")

@router.message(Command("help"))
async def send_help_message(message: types.Message):
    """
    Faqat OWNER_ID uchun barcha mavjud admin komandalari va ularning ishlatilishi haqida ma'lumot beradi.
    """
    if message.from_user.id != OWNER_ID:
        return

    help_text = (
        "<b>📖 Botni boshqarish va foydalanish yo'riqnomasi</b>\n\n"
        "Siz tizim egasi (Admin) bo'lganingiz uchun quyidagi komandalardan foydalanishingiz mumkin:\n\n"
        
        "📊 <b>/tabel</b>\n"
        "└ Joriy oy uchun barcha xodimlarning davomat tabelini Excel formatida generatsiya qiladi va yuboradi.\n\n"
        
        "🏢 <b>/bolim_ozgartir</b>\n"
        "└ O'zingizning bo'limingizni tozalaydi.\n"
        "└ <code>/bolim_ozgartir 123456789</code> — ko'rsatilgan ID'ga ega xodimning bo'limini tozalaydi.\n"
        "<i>(Bo'lim tozalangach, xodim /start bosganda bo'lim tanlash menyusi qayta chiqadi)</i>\n\n"
        
        "👥 <b>/hodimlar</b>\n"
        "└ Hozirda faol bo'lgan barcha xodimlar ro'yxatini va ularning bo'limlarini chiqaradi.\n"
        "└ Xodimni ro'yxatdan tanlab, uni botdan bloklash (ishdan chiqarish) imkonini beradi."
    )

    await message.answer(help_text, parse_mode="HTML")


@router.message(Command("tabel"))
async def send_current_month_tabel(message: types.Message):
    """Joriy oy uchun tabelni generatsiya qilib, FAQAT egasiga (OWNER_ID) yuboradi."""
    if message.from_user.id != OWNER_ID:
        return  # boshqa hech kimga hatto rad javobi ham bermaydi

    today = date.today()

    await message.answer("⏳ Tabel tayyorlanmoqda...")

    file_path = await excel.generate_monthly_tabel(today.year, today.month)

    await message.answer_document(
        FSInputFile(file_path),
        caption=f"📊 {today.month:02d}.{today.year} oyi uchun tabel",
    )


@router.message(Command("bolim_ozgartir"))
async def reset_department(message: types.Message, command: CommandObject):
    """
    /bolim_ozgartir            — o'zingizning bo'limingizni tozalaydi
    /bolim_ozgartir 123456789  — ko'rsatilgan xodimning bo'limini tozalaydi
    Ikkalasi ham FAQAT OWNER_ID uchun ishlaydi. Tozalangach, o'sha odam
    /start bosganda bo'lim tanlash savoli yana beriladi.
    """
    if message.from_user.id != OWNER_ID:
        return

    target_id = message.from_user.id

    if command.args:
        try:
            target_id = int(command.args.strip())
        except ValueError:
            await message.answer("⚠️ Foydalanuvchi ID raqam bo'lishi kerak.\nMisol: /bolim_ozgartir 123456789")
            return

    await db.reset_employee_department(target_id)

    if target_id == message.from_user.id:
        await message.answer("✅ Bo'limingiz tozalandi. /start bosib qaytadan tanlang.")
    else:
        await message.answer(f"✅ {target_id} uchun bo'lim tozalandi. U /start bosib qaytadan tanlashi mumkin.")


# ===========================
# Xodimlarni boshqarish — /hodimlar (faqat OWNER_ID)
# ===========================
@router.message(Command("hodimlar"))
async def list_employees(message: types.Message):
    """
    Faol xodimlar ro'yxatini tugmalar bilan chiqaradi. Har bir tugmani bosish —
    o'sha xodimni ishdan chiqarish (soft-delete) tasdig'iga olib boradi.
    """
    if message.from_user.id != OWNER_ID:
        return

    employees = await db.get_active_employees()

    if not employees:
        await message.answer("Hozircha faol xodim yo'q.")
        return

    builder = InlineKeyboardBuilder()
    for user_id, full_name, department in employees:
        label = full_name or f"ID {user_id}"
        if department:
            label = f"{label} — {department}"
        builder.add(types.InlineKeyboardButton(text=label, callback_data=f"emp_{user_id}"))
    builder.adjust(1)

    await message.answer(
        f"👥 Faol xodimlar ({len(employees)} ta).\n"
        "Ishdan chiqarish uchun xodimni tanlang:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("emp_"))
async def confirm_deactivate(callback_query: types.CallbackQuery):
    """Xodim tanlangach, o'chirishdan oldin tasdiq so'raladi."""
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer()
        return

    try:
        target_id = int(callback_query.data[len("emp_"):])
    except ValueError:
        await callback_query.answer("Noto'g'ri ma'lumot.", show_alert=True)
        return

    row = await db.get_employee_short(target_id)
    if row is None:
        await callback_query.answer("Xodim topilmadi.", show_alert=True)
        return

    full_name, department, is_active = row
    if not is_active:
        await callback_query.answer("Bu xodim allaqachon ishdan chiqarilgan.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="✅ Ha, chiqarish", callback_data=f"empdel_{target_id}"))
    builder.add(types.InlineKeyboardButton(text="❌ Bekor qilish", callback_data="empcancel"))
    builder.adjust(2)

    name_label = full_name or f"ID {target_id}"
    dept_label = f" ({department})" if department else ""
    await callback_query.message.edit_text(
        f"⚠️ <b>{name_label}</b>{dept_label} xodimini ishdan chiqarasizmi?\n\n"
        "U botdan bloklanadi (bot unga javob bermaydi), lekin davomat tarixi saqlanadi.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback_query.answer()


@router.callback_query(F.data == "empcancel")
async def cancel_deactivate(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer()
        return
    await callback_query.message.edit_text("Bekor qilindi.")
    await callback_query.answer()


@router.callback_query(F.data.startswith("empdel_"))
async def do_deactivate(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != OWNER_ID:
        await callback_query.answer()
        return

    try:
        target_id = int(callback_query.data[len("empdel_"):])
    except ValueError:
        await callback_query.answer("Noto'g'ri ma'lumot.", show_alert=True)
        return

    row = await db.get_employee_short(target_id)
    if row is None:
        await callback_query.answer("Xodim topilmadi.", show_alert=True)
        return

    full_name = row[0] or f"ID {target_id}"
    await db.deactivate_employee(target_id)

    await callback_query.message.edit_text(
        f"✅ <b>{full_name}</b> ishdan chiqarildi. Bot endi unga javob bermaydi.\n"
        "Davomat tarixi saqlanib qoldi.",
        parse_mode="HTML",
    )
    await callback_query.answer("Bajarildi")