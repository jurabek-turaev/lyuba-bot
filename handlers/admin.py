from datetime import date

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from config import OWNER_ID
from services import excel

router = Router()
router.message.filter(F.chat.type == "private")

OWNER_SELF_REMOVAL = "O'zingizni ishdan chiqara olmaysiz: keyin xodim sifatida qaytib kira olmaysiz."

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
        
        "🏢 <b>«Bo'limni o'zgartirish» tugmasi</b>\n"
        "└ Xodimni tanlang → yangi bo'lim → lavozim: xodim boshqa bo'lim/lavozimga o'tkaziladi. "
        "⚠️ belgisi — lavozimi hali belgilanmagan xodim.\n"
        "└ «🔄 Xodimning o'zi qayta tanlasin» — bo'lim va lavozim tozalanadi, xodim /start bosganda "
        "o'zi tanlaydi.\n"
        "└ <code>/bolim_ozgartir 123456789</code> — ko'rsatilgan ID'ga ega xodimning bo'limini tozalaydi "
        "(<code>/bolim_ozgartir</code> — o'zingiznikini).\n\n"
        
        "👥 <b>/hodimlar</b>\n"
        "└ Hozirda faol bo'lgan barcha xodimlar ro'yxatini va ularning bo'limlarini chiqaradi.\n"
        "└ Xodimni ro'yxatdan tanlab, uni botdan bloklash (ishdan chiqarish) imkonini beradi.\n\n"

        "📝 <b>/arizalar</b>\n"
        "└ Ishga arizalar bo'limlar bo'yicha: ⏳ yangi, 📅 suhbat kutilmoqda, 📋 bugungi suhbatlar. "
        "Bo'lim tugmasi o'sha arizalarning kartalarini (rezyume fayli bilan) yuboradi.\n"
        "└ Yangi ariza: suhbatga taklif qilish (kun, vaqt, joy, izoh), rad etish yoki bloklash.\n"
        "└ Suhbatga taklif qilingan: ishga qabul qilish, rad etish, vaqtini ko'chirish, «kelmadi».\n"
        "└ Rad javobi kadrga xushmuomala xat bilan boradi; tanlangan sabab faqat sizga ko'rinadi.\n"
        "└ Kadrga suhbatdan bir kun oldin 18:00 da eslatma boradi; suhbatdan 1 soat keyin bot "
        "sizdan natijani so'raydi.\n"
        "└ <code>/blokdan_chiqar 123456789</code> — bloklangan foydalanuvchini blokdan chiqaradi.\n\n"

        "🕘 <b>/ish_vaqti_belgilash</b>\n"
        "└ Xodimning doimiy ish vaqtini (norma) va belgilangan oylik maoshini belgilaydi: "
        "bo'lim → xodim → boshlanish va tugash vaqti → oylik maosh → tasdiqlash.\n"
        "└ Qayta bajarilsa, eski qiymatlar yangisi bilan almashtiriladi.\n\n"

        "🗂 <b>/filiallar</b>\n"
        "└ Bo'lim, Savdo filiallari va lavozimlarni boshqarish: yangi filial yoki bo'lim qo'shish, nomini, "
        "joylashuvini (Telegram lokatsiyasi, Google/Yandex havolasi yoki koordinata), guruhini o'zgartirish, "
        "bo'limlarga biriktirish, faolsizlantirish.\n"
        "└ 🏭 Zavod: filiali yo'q bo'limlar (Ishlab chiqarish, Rahbariyat) ish joyi — ularning xodimlariga "
        "filial so'ralmaydi. Joylashuvi va guruhini shu yerda o'zgartirasiz.\n"
        "└ 💼 Lavozimlar: har bir bo'lim uchun lavozimlar ro'yxati (bir nechtasini bir xabarda, "
        "har birini yangi qatordan yozib qo'shish mumkin). Xodim va nomzod bo'limdan keyin lavozim "
        "tanlaydi; lavozimi yo'q bo'limda bu savol berilmaydi.\n"
        "└ Guruh ro'yxatda bo'lishi uchun botni o'sha guruhga qo'shing — u avtomatik eslab qoladi.\n\n"

        "📋 <b>/menu</b>\n"
        "└ Hamma amallarni pastdagi doimiy tugmalar sifatida chiqaradi (buyruq yozish shart emas)."
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

    # The owner is not listed: removing yourself cannot be undone from the bot
    # (an owner's /start always opens the admin menu, never the application form)
    employees = [row for row in await db.get_active_employees() if row[0] != OWNER_ID]

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

    if target_id == OWNER_ID:
        await callback_query.answer(OWNER_SELF_REMOVAL, show_alert=True)
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
        "U botdan foydalana olmaydi (faqat /start orqali qayta ariza yubora oladi). Davomat tarixi saqlanadi.",
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

    if target_id == OWNER_ID:
        await callback_query.answer(OWNER_SELF_REMOVAL, show_alert=True)
        return

    row = await db.get_employee_short(target_id)
    if row is None:
        await callback_query.answer("Xodim topilmadi.", show_alert=True)
        return

    full_name = row[0] or f"ID {target_id}"
    await db.deactivate_employee(target_id)

    await callback_query.message.edit_text(
        f"✅ <b>{full_name}</b> ishdan chiqarildi. U botdan foydalana olmaydi.\n"
        "Qayta ishga olish uchun u /start bosib ariza yuborishi kerak. Davomat tarixi saqlanib qoldi.",
        parse_mode="HTML",
    )
    await callback_query.answer("Bajarildi")