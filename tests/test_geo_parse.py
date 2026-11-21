"""
Run:  python -m unittest discover -s tests -v
The short-link tests open real Google / Yandex links; they run only with
NETWORK_TESTS=1 in the environment.
"""
import asyncio
import os
import unittest

from services import geo_parse as g

# Links sent by the owner for "Lyuba shirinliklar uyi", Qarshi
YANDEX_BROWSER = "https://yandex.uz/maps/org/lyuba/72333058136/?ll=65.793878%2C38.829001&z=16.89"
YANDEX_SHARE = "https://yandex.uz/maps/-/CXWtZCPA"
YANDEX_MOBILE = "https://yandex.com/maps/org/lyuba/72333058136?si=hb5zt3nyxuvkvvkphgya0n2474"
GOOGLE_BROWSER = (
    "https://www.google.com/maps/place/Lyuba+shirinliklar+uyi/@38.8290667,65.7913626,17z/"
    "data=!3m1!4b1!4m6!3m5!1s0x3f4ea89183efbf91:0xf8366d6662fa80be!8m2!3d38.8290625!4d65.7939375"
    "!16s%2Fg%2F11f3s8t4m9?entry=ttu&g_ep=EgoyMDI2MTExOC4xIKXMDSoASAFQAw%3D%3D"
)
GOOGLE_SHARE = "https://maps.app.goo.gl/g5Kn3RXydjSWMxLJ6"
GOOGLE_MOBILE = "https://maps.app.goo.gl/s8ocGR7mbf33X4gk7"


def run(coro):
    return asyncio.run(coro)


class TextTests(unittest.TestCase):
    def test_plain_pair(self):
        self.assertEqual(g.parse_coordinates_text("38.829001, 65.793878"), (38.829001, 65.793878))

    def test_other_separators(self):
        self.assertEqual(g.parse_coordinates_text("38.829001 65.793878"), (38.829001, 65.793878))
        self.assertEqual(g.parse_coordinates_text("(38.829001;65.793878)"), (38.829001, 65.793878))

    def test_swapped_pair_is_fixed(self):
        self.assertEqual(g.parse_coordinates_text("65.793878, 38.829001"), (38.829001, 65.793878))

    def test_rejects_non_coordinates(self):
        for text in ("salom", "38, 65", "5000000", "", "123.5, 500.1"):
            self.assertIsNone(g.parse_coordinates_text(text), text)


class UrlTests(unittest.TestCase):
    def test_yandex_browser_link_lon_first(self):
        self.assertEqual(g.parse_map_url(YANDEX_BROWSER), (38.829001, 65.793878))

    def test_yandex_marker_preferred_over_centre(self):
        url = "https://yandex.uz/maps/?ll=65.0%2C38.0&pt=65.793878%2C38.829001%2Cpm2rdm&z=17"
        self.assertEqual(g.parse_map_url(url), (38.829001, 65.793878))
        url = "https://yandex.ru/maps/?whatshere%5Bpoint%5D=65.793878%2C38.829001&whatshere%5Bzoom%5D=17"
        self.assertEqual(g.parse_map_url(url), (38.829001, 65.793878))

    def test_google_pin_preferred_over_viewport(self):
        self.assertEqual(g.parse_map_url(GOOGLE_BROWSER), (38.8290625, 65.7939375))

    def test_google_query_and_viewport(self):
        self.assertEqual(g.parse_map_url("https://maps.google.com/?q=38.829001,65.793878"), (38.829001, 65.793878))
        self.assertEqual(g.parse_map_url("https://www.google.com/maps/@38.8290667,65.7913626,17z"),
                         (38.8290667, 65.7913626))
        self.assertEqual(g.parse_map_url("https://www.google.com/maps/search/38.829001,+65.793878"),
                         (38.829001, 65.793878))

    def test_links_without_coordinates(self):
        self.assertIsNone(g.parse_map_url(YANDEX_MOBILE))
        self.assertIsNone(g.parse_map_url(YANDEX_SHARE))
        self.assertIsNone(g.parse_map_url(GOOGLE_MOBILE))

    def test_foreign_hosts_are_never_parsed(self):
        self.assertFalse(g.is_allowed_url("https://evil.example/maps?q=38.8,65.7"))
        self.assertFalse(g.is_allowed_url("https://google.com.evil.example/maps"))
        self.assertFalse(g.is_allowed_url("ftp://yandex.uz/maps"))
        self.assertTrue(g.is_allowed_url("https://www.google.co.uz/maps"))
        self.assertTrue(g.is_allowed_url("https://maps.app.goo.gl/x"))
        self.assertIsNone(g.parse_map_url("https://evil.example/maps?q=38.829001,65.793878"))


class ExtractTests(unittest.TestCase):
    def test_text_and_full_links_need_no_network(self):
        self.assertEqual(run(g.extract_coordinates("38.829001, 65.793878")), (g.OK, (38.829001, 65.793878)))
        self.assertEqual(run(g.extract_coordinates(f"Mana: {YANDEX_BROWSER}")), (g.OK, (38.829001, 65.793878)))
        self.assertEqual(run(g.extract_coordinates(GOOGLE_BROWSER)), (g.OK, (38.8290625, 65.7939375)))

    def test_rejections(self):
        self.assertEqual(run(g.extract_coordinates("https://evil.example/x")), (g.NOT_ALLOWED, None))
        self.assertEqual(run(g.extract_coordinates("Eski bozor")), (g.NOT_FOUND, None))


@unittest.skipUnless(os.getenv("NETWORK_TESTS") == "1", "set NETWORK_TESTS=1 to open real short links")
class ShortLinkTests(unittest.TestCase):
    def test_share_links_resolve(self):
        self.assertEqual(run(g.extract_coordinates(YANDEX_SHARE)), (g.OK, (38.829001, 65.793878)))
        self.assertEqual(run(g.extract_coordinates(GOOGLE_SHARE)), (g.OK, (38.8290625, 65.7939375)))

    def test_mobile_links_have_no_coordinates(self):
        self.assertEqual(run(g.extract_coordinates(YANDEX_MOBILE)), (g.NOT_FOUND, None))
        self.assertEqual(run(g.extract_coordinates(GOOGLE_MOBILE)), (g.NOT_FOUND, None))


if __name__ == "__main__":
    unittest.main()
