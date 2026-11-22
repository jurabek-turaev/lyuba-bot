"""
Hiring flow, phase 2: the resume PDF (services/resume_pdf.py).

The RenderCV input is checked without RenderCV. The real render runs only when
the RenderCV virtualenv exists (.venv-rendercv locally, /opt/rendercv in Docker).

Run:  python -m unittest discover -s tests -t . -v
"""
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import config
from services import resume_pdf

FULL_FORM = {
    "full_name": "Gʻayratov Oʻktam",
    "phone": "+998901234567",
    "city": "Qarshi sh.",
    "birth_date": "15.03.1998",
    "gender": "Erkak",
    "marital_status": "Oilali",
    "education_level": "Oliy",
    "education": [{"institution": "QarDU", "specialty": "Iqtisodiyot", "year": 2019},
                  {"institution": "Kollej", "specialty": None, "year": None}],
    "jobs": [{"company": "Makro", "position": "Kassir", "start": "2021-03", "end": "present"},
             {"company": "Do'kon", "position": "Sotuvchi", "start": None}],
    "languages": [{"name": "Oʻzbek", "level": "ona tili"}, {"name": "Rus", "level": None}],
    "computer_skills": "Word, Excel",
    "driver_license": "B toifa",
    "about": "Masʼuliyatli.\n\nXushmuomala.",
    # these belong to the admin's card only and must never reach the PDF
    "position": "Savdo", "expected_salary": 5_000_000, "start_availability": "Darhol",
}

RENDERER_READY = Path(config.RESUME_RENDERER_PYTHON).exists()


class BuildCvTests(unittest.TestCase):
    def test_full_form(self):
        data = resume_pdf.build_cv(FULL_FORM, "photo.jpg", today=date(2026, 11, 22))
        cv, sections = data["cv"], data["cv"]["sections"]

        self.assertEqual((cv["name"], cv["phone"], cv["location"], cv["photo"]),
                         ("Gʻayratov Oʻktam", "+998901234567", "Qarshi sh.", "photo.jpg"))
        self.assertEqual(sections["Shaxsiy maʼlumotlar"], [
            "**Tugʻilgan sana:** 15\\.03\\.1998 (28 yosh)",
            "**Jinsi:** Erkak · **Oilaviy holati:** Oilali",
            "**Maʼlumoti:** Oliy",
        ])
        self.assertEqual(sections["Maʼlumoti"], [
            {"institution": "QarDU", "area": "Iqtisodiyot", "date": 2019},
            {"institution": "Kollej", "area": "Oliy"},
        ])
        self.assertEqual(sections["Ish tajribasi"], [
            {"company": "Makro", "position": "Kassir", "start_date": "2021-03", "end_date": "present"},
            {"company": "Do'kon", "position": "Sotuvchi"},
        ])
        self.assertEqual(sections["Koʻnikmalar"], [
            {"label": "Tillar", "details": "Oʻzbek (ona tili), Rus"},
            {"label": "Kompyuter", "details": "Word, Excel"},
            {"label": "Haydovchilik guvohnomasi", "details": "B toifa"},
        ])
        self.assertEqual(sections["Oʻzim haqimda"], ["Masʼuliyatli\\.", "Xushmuomala\\."])
        self.assertEqual(data["locale"]["present"], "hozirgacha")
        self.assertEqual(data["design"]["page"]["size"], "a4")

    def test_pdf_is_universal(self):
        text = str(resume_pdf.build_cv(FULL_FORM))
        for private in ("Savdo", "5000000", "5 000 000", "Darhol", "maosh"):
            self.assertNotIn(private, text)

    def test_minimal_form_has_no_empty_sections(self):
        data = resume_pdf.build_cv({"full_name": "Ali Valiyev"})
        self.assertEqual(data["cv"], {"name": "Ali Valiyev", "sections": {}})
        self.assertNotIn("phone_fallback", data)

    def test_candidate_text_cannot_inject_markdown(self):
        self.assertEqual(resume_pdf.md(" *bold* _x_ [a](b) #1 "), "\\*bold\\* \\_x\\_ \\[a\\]\\(b\\) \\#1")
        self.assertEqual(resume_pdf.md("Oʻktam 100% $ @ <x>"), "Oʻktam 100% $ @ <x\\>")

    def test_age(self):
        self.assertEqual(resume_pdf.age_on("22.11.2000", date(2026, 11, 22)), 26)
        self.assertEqual(resume_pdf.age_on("23.11.2000", date(2026, 11, 22)), 25)
        self.assertIsNone(resume_pdf.age_on("2000-11-23", date(2026, 11, 22)))

    def test_photo_format_comes_from_the_bytes(self):
        self.assertEqual(resume_pdf.photo_extension(b"\xff\xd8\xff\xe0rest"), "jpg")
        self.assertEqual(resume_pdf.photo_extension(b"\x89PNG\r\n\x1a\nrest"), "png")
        self.assertEqual(resume_pdf.photo_extension(b"RIFF\0\0\0\0WEBPrest"), "webp")
        self.assertIsNone(resume_pdf.photo_extension(b"GIF89a"))


class RenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_renderer_is_a_clear_error(self):
        with mock.patch.object(config, "RESUME_RENDERER_PYTHON", "/nonexistent/python"):
            with self.assertRaisesRegex(resume_pdf.ResumeRenderError, "not found"):
                await resume_pdf.render_resume_pdf({"full_name": "Ali"})

    @unittest.skipUnless(RENDERER_READY, "RenderCV virtualenv not installed")
    async def test_full_resume_renders(self):
        pdf = await resume_pdf.render_resume_pdf(FULL_FORM, photo=b"not an image")
        self.assertTrue(pdf.startswith(b"%PDF"))

    @unittest.skipUnless(RENDERER_READY, "RenderCV virtualenv not installed")
    async def test_invalid_phone_does_not_break_the_resume(self):
        pdf = await resume_pdf.render_resume_pdf({"full_name": "Ali", "phone": "+99890123"})
        self.assertTrue(pdf.startswith(b"%PDF"))
