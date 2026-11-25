# Attendance Bot (Davomat boti)

Telegram bot orqali xodimlarning ishga kelish-ketishini joylashuv (GPS) va dumaloq
video tasdig'i bilan qayd qiluvchi tizim. Oy oxirida `/tabel` orqali Excel hisobot beradi.

## Imkoniyatlari

- Ishga ariza: nomzod botda anketa to'ldiradi (bot undan PDF rezyume yasaydi) yoki tayyor
  rezyumesini yuboradi → admin kartani oladi va tasdiqlaydi / rad etadi / bloklaydi.
  Tugallanmagan anketa bazada saqlanadi — bot qayta ishga tushsa ham yo'qolmaydi
- Bo'lim va filiallarni admin botning o'zida boshqaradi (yaratish, tahrirlash, faolsizlantirish)
- Bo'limda bitta filial bo'lsa — xodimga avtomatik, bir nechta bo'lsa — xodim o'zi tanlaydi
- Dumaloq video (video_note) tasdig'i — filialga biriktirilgan guruhga avtomatik yuboriladi
- GPS masofa tekshiruvi (belgilangan `MAX_DISTANCE` metr ichida bo'lishi shart);
  faqat jonli (live) lokatsiya qabul qilinadi — statik va forward qilingani rad etiladi
- "Keldim" / "Ketdim" qayd qilish, ish soatini avtomatik hisoblash
- Tungi smenani to'g'ri hisoblash (kun chegarasidan o'tsa ham)
- "Ketdim" bosilmasa — smenani avtomatik yopish (auto-close)
- Oylik Excel tabel (`/tabel` — faqat bot egasiga), ish grafigi, norma va maosh hisobi bilan
- Xodimga doimiy ish vaqti va oylik maosh belgilash (soatlik stavka avtomatik hisoblanadi)
- Xodimni ishdan chiqarish — `/hodimlar` (soft-delete: tarix saqlanadi, bot bloklaydi)
- Har oy 1-sanasi 00:30 da o'tgan oy tabeli avtomatik egasiga yuboriladi va arxivlanadi

## Egasi (OWNER_ID) uchun buyruqlar

Barcha amallar `/menu` (yoki xodim bo'lmagan egasi uchun `/start`) chiqaradigan doimiy
tugmalarda bor — buyruq yozish shart emas.

| Buyruq | Vazifa |
|--------|--------|
| `/menu` | Admin tugmalarini chiqaradi |
| `/help` | Buyruqlar bo'yicha yo'riqnoma |
| `/tabel` | Joriy oy tabelini Excel qilib oladi |
| `/hodimlar` | Faol xodimlar ro'yxati — tugma orqali ishdan chiqarish |
| `/arizalar` | Ishga arizalar: yangi, suhbat kutilmoqda, bugungi suhbatlar (menyuda: 📝 Arizalar) |
| `/ish_vaqti_belgilash` | Xodimning ish vaqti va oylik maoshini belgilash |
| `/filiallar` | Bo'lim va filiallarni boshqarish (menyuda: 🗂 Bo'lim va filiallar) |
| `/bolim_ozgartir [ID]` | O'zining yoki ko'rsatilgan xodimning bo'limini tozalaydi |
| `/blokdan_chiqar ID` | Bloklangan foydalanuvchini blokdan chiqaradi |

## Bo'lim va filiallar

Bo'limlar, filiallar, ularning koordinatalari va guruhlari **bazada** saqlanadi va
admin tomonidan `/filiallar` (yoki menyudagi «🗂 Bo'lim va filiallar») orqali boshqariladi.
Yangi filial ochilsa, kodni o'zgartirish shart emas.

- **Filial** — nomi, joylashuvi (koordinata) va video/xabarlar boradigan guruh.
- **Bo'lim** — bir yoki bir nechta filialga biriktiriladi:
  bitta filial bo'lsa xodimga avtomatik tanlanadi (masalan Zavod), bir nechta bo'lsa
  xodim har safar o'zi tanlaydi (masalan Savdo do'konlari).

**Joylashuvni kiritish** — quyidagilardan biri yuboriladi, bot nuqtani xaritada ko'rsatib
tasdiq so'raydi:
- Telegram lokatsiyasi (📎 → Location → xaritadan nuqta);
- Google yoki Yandex xarita havolasi — brauzerdagi havola yoki brauzerdagi «Ulashish»
  tugmasidan olingan qisqa havola;
- koordinata matni, masalan `38.829001, 65.793878`.

> Google/Yandex **mobil ilovasi**ning «Ulashish» havolasida koordinata bo'lmaydi — bunday
> havola yuborilsa, bot boshqa usulni so'raydi.

**Guruhlar** — botni kerakli Telegram guruhga qo'shing: bot uni avtomatik eslab qoladi
va guruh filial sozlamasida ro'yxatdan tanlanadi. Guruh nomi o'zgarsa yoki supergroup'ga
aylansa (ID o'zgaradi), bot buni ham o'zi kuzatadi.

**O'chirish va faolsizlantirish:**
- Ishlatilgan filial/bo'lim o'chirilmaydi, faqat **faolsizlantiriladi** — tarix saqlanadi.
  O'chirish faqat hech qachon ishlatilmagan (xato yaratilgan) yozuv uchun mumkin.
- Faol xodimlari bor bo'limni faolsizlantirib bo'lmaydi.
- Faol xodimlari bor bo'limning yagona filialini faolsizlantirib / ajratib bo'lmaydi.
- Ochiq smena qaysi filialda boshlangan bo'lsa, o'sha yerda yopiladi — hatto filial
  keyinroq faolsizlantirilgan bo'lsa ham.
- Filial nomi o'zgartirilsa, eski davomat yozuvlarida eski nom qoladi (tarix).

## Ishga qabul qilish (arizalar)

Xodim bo'lmagan har kim `/start` bosib ishga ariza topshira oladi.

**Nomzod tomoni**
1. Shaxsiy ma'lumotlarga rozilik (ma'lumotlar faqat ishga qabul qilish uchun ishlatiladi).
2. Ikki yo'l:
   - **📝 Rezyume yaratish** — 17 ta savol: shaxsiy ma'lumotlar, rasm, lavozim, kutilayotgan
     maosh, ma'lumoti, ish tajribasi, tillar, ko'nikmalar. Bot javoblardan **PDF rezyume** yasaydi
     (RenderCV, A4). PDF universal: unda lavozim, maosh va ishga chiqish sanasi yo'q — ular faqat
     adminning kartasida ko'rinadi.
   - **📎 Tayyor rezyume** — o'z fayli (PDF, DOC, DOCX, JPG, PNG, 20 MB gacha) + ism, telefon,
     lavozim, maosh.
3. Oldindan ko'rish → yuborish, bo'limini tahrirlash yoki bekor qilish.

Tugallanmagan anketa **bazada** saqlanadi: bot qayta ishga tushsa ham yo'qolmaydi, `/start`
uni davom ettirishni taklif qiladi. Yuborilgan arizani nomzod `/start` orqali qaytarib olishi mumkin.

**Admin tomoni** — har bir ariza bitta karta (rezyume fayli + qisqa ma'lumot), tugmalari holatga qarab:

| Holat | Amallar |
|-------|---------|
| ⏳ Yangi | 📅 Suhbatga taklif qilish · ❌ Rad etish · 🚫 Bloklash |
| 📅 Suhbat belgilangan | ✅ Ishga qabul qilish · ❌ Rad etish · 📅 Ko'chirish · 🚷 Kelmadi · 🚫 Bloklash |

- **Suhbatga taklif**: kun (keyingi ish kunlari yoki qo'lda) → vaqt → joy (filiallar ro'yxati
  yoki boshqa manzil) → izoh → nomzod oladigan xabarni oldindan ko'rish → yuborish. Nomzod
  xabarni Yandex xarita havolasi va xaritadagi nuqta bilan oladi.
- Nomzod **«🔁 Boshqa vaqt so'rash»** tugmasi bilan qulay vaqtini yozadi — admin uni
  «📅 Ko'chirish» tugmasi bilan oladi.
- **Rad etish**: ichki sabab (nomzodga aytilmaydi) → xushmuomala rad xati → yuborish.
- **Ishga qabul qilish**: nomzod xodimga aylanadi, bo'limi arizadagi lavozimdan avtomatik
  qo'yiladi. Ish vaqti va maoshini «🕘 Ish vaqtini belgilash» orqali kiriting.
- Avval ishlagan nomzodning kartasida «♻️ Avval ishlagan, qayta topshirmoqda» belgisi bo'ladi.

**Avtomatik:**
- Suhbatdan bir kun oldin 18:00 da nomzodga eslatma (taklif 24 soatdan kam oldin yuborilgan
  bo'lsa — yuborilmaydi).
- Suhbat vaqtidan 1 soat o'tgach admin natijani belgilashi uchun xabar oladi.
- Rad etilgan yoki suhbatga kelmagan nomzod 7 kundan keyin qayta ariza bera oladi.
- Ishga olinmagan nomzodlarning ma'lumotlari 1 yildan, tugallanmagan anketalar 30 kundan
  keyin avtomatik o'chiriladi.

## Xodimni ishdan chiqarish haqida

`/hodimlar` orqali chiqarilgan xodim:
- Botdan foydalana olmaydi — qaytmoqchi bo'lsa, `/start` orqali qayta ariza yuboradi.
- Faol xodimlar ro'yxatidan chiqadi.
- Lekin uning **davomat tarixi bazada saqlanadi** — hisobotlar buzilmaydi.
- Joriy oy tabelida u oy oxirigacha ko'rinadi (ishlagan kunlari adolatli hisobga olinadi).

## Oylik avtomatik arxivlash haqida

Har oyning 1-sanasi 00:30 da bot:
1. O'tgan oy tabelini Excel qilib egasiga (OWNER_ID) yuboradi.
2. O'sha oy yozuvlarini **arxivlaydi** (o'chirmaydi — `archived=1` belgisi qo'yiladi).

Arxivlangan data yo'qolmaydi: ishdan ketgan xodimning eski ma'lumoti kerak bo'lsa,
bazada saqlanib turadi. Kundalik tizim faqat joriy (arxivlanmagan) oy bilan ishlagani
uchun toza va yengil qoladi. Agar bot 1-sanasi 00:30 da o'chiq bo'lsa, keyingi ishga
tushishida bu ish avtomatik bajariladi (har oy uchun faqat bir marta).

---

## 1. Sozlash (.env)

`.env.example` ni nusxalab, `.env` yarating va qiymatlarni to'ldiring:

```bash
cp .env.example .env
```

| O'zgaruvchi | Izoh |
|-------------|------|
| `BOT_TOKEN` | BotFather'dan olingan token |
| `OWNER_ID` | Bot egasining Telegram ID'si (`/tabel` faqat shunga yuboriladi) |
| `DEVELOPER_ID` | Dasturchining Telegram ID'si (test uchun masofa tekshiruvidan ozod) |
| `MAX_DISTANCE` | Ruxsat etilgan masofa (metr), barcha filiallar uchun, masalan `200` |
| `MAX_SHIFT_HOURS` | Maksimal smena (soat), masalan `12` |
| `RESUME_RENDERER_PYTHON` | *Ixtiyoriy.* RenderCV o'rnatilgan python yo'li (rezyume PDF'i uchun). Docker'da kerak emas |
| `GROUP_ID`, `PRODUCTION_LAT`, `PRODUCTION_LON`, `PRODUCTION_GROUP_ID` | *Ixtiyoriy.* Faqat eski bazani ko'chirish uchun (pastga qarang). Yangi o'rnatishda bo'sh qoldiring |

### Eski versiyadan yangilash

Avval filiallar `config.py` da qo'lda yozilgan edi. Yangi versiya birinchi ishga
tushganda katalog jadvallarini yaratadi va eski sozlamani **bir marta** ko'chiradi:
3 ta Savdo do'koni (`GROUP_ID` guruhi bilan) va «Zavod» (`PRODUCTION_*` qiymatlari bilan),
unga Ishlab chiqarish, Haydovchi, I.T.R bo'limlari biriktiriladi. Xodimlar va davomat
yozuvlari o'zgarmaydi, qayta ro'yxatdan o'tish kerak emas.

> Yangilashdan oldin bazaning nusxasini oling: `cp data/attendance.db data/attendance.backup.db`

> ⚠️ `.env` faylni hech kimga bermang va git'ga yuklamang — u ichida bot tokeni bor.

---

## 2. Docker bilan ishga tushirish (tavsiya etiladi)

Serverda Docker va Docker Compose o'rnatilgan bo'lishi kerak.

```bash
docker compose up -d --build
```

Loglarni ko'rish:

```bash
docker compose logs -f
```

To'xtatish:

```bash
docker compose down
```

Ma'lumotlar bazasi (`data/attendance.db`) va tabellar (`data/tables/`) host mashinada
`./data` papkasida saqlanadi — konteyner o'chirilsa ham yo'qolmaydi.

---

## 3. Docker'siz ishga tushirish (lokal test uchun)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

**Rezyume PDF'i (RenderCV)** botdan alohida virtualenv'da ishlaydi — aiogram 3.13 bilan
pydantic versiyasi to'qnashadi. Docker'da u avtomatik o'rnatiladi (`/opt/rendercv`).
Lokal ishlatish uchun bir marta:

```bash
python -m venv .venv-rendercv
.venv-rendercv/bin/pip install -r requirements-rendercv.txt
```

Bot `.venv-rendercv` ni o'zi topadi; boshqa joyga o'rnatsangiz, `.env` da
`RESUME_RENDERER_PYTHON` ga o'sha python yo'lini yozing.

---

## Testlar

```bash
python -m unittest discover -s tests -t .
NETWORK_TESTS=1 python -m unittest discover -s tests -t .   # qisqa havolalarni internet orqali ham tekshiradi
```

---

## Vaqt zonasi haqida

Bot davomat vaqtlarini serverning mahalliy vaqtiga qarab yozadi. Docker'da vaqt zonasi
`Asia/Tashkent` qilib o'rnatilgan (`Dockerfile` va `docker-compose.yml` da). Docker'siz
ishlatsangiz, server vaqti to'g'ri sozlanganiga ishonch hosil qiling.

---

## Xavfsizlik eslatmalari

- Bot GPS asosida ishlaydi. Telegram joylashuvni soxtalashtirish (fake GPS) texnik
  jihatdan mumkin — bu Telegram'ning tabiiy cheklovi. Ishonchli xodimlar uchun mo'ljallangan.
- Barcha maxfiy sozlamalar `.env` da; kodda ochiq token yo'q.
