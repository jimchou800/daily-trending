"""Regression coverage from saved GitHub Daily cards captured on 2026-11-10."""

from pathlib import Path
import unittest

from daily_trending.collector import parse_trending_html


HTML = (Path(__file__).parent / "fixtures" / "trending_live_cards.html").read_text(encoding="utf-8")


class LivePageCollectorTests(unittest.TestCase):
    def test_sponsor_before_heading_preserves_actual_repository_and_metrics(self):
        entries = parse_trending_html(HTML)
        self.assertEqual(len(entries), 2)
        self.assertEqual([entry["rank"] for entry in entries], [1, 2])
        self.assertEqual(entries[0]["repository"], "homeassistant-ai/ha-mcp")
        self.assertEqual((entries[0]["stars_total"], entries[0]["stars_today"]), (6025, 31))
        self.assertEqual(entries[-1]["repository"], "milind-soni/OpenMausBot")
        self.assertEqual(entries[-1]["repository_url"], "https://github.com/milind-soni/OpenMausBot")
        self.assertEqual(entries[-1]["stars_total"], 19755)
        self.assertEqual(entries[-1]["stars_today"], 1587)
        self.assertEqual(entries[-1]["language"], "TypeScript")
        self.assertEqual(
            entries[-1]["description"], "Open-source Grok Bot alternative with a virtual machine that bots can use"
        )

    def test_card_without_heading_is_rejected_even_after_valid_card(self):
        malformed = HTML.replace("</body>", '<article class="Box-row"><p>No repository heading</p></article></body>')
        with self.assertRaisesRegex(ValueError, "repository|complete"):
            parse_trending_html(malformed)

    def test_truncated_last_card_is_rejected_instead_of_returning_partial_snapshot(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            parse_trending_html(HTML[: HTML.rindex("</article>")])

    def test_truncation_at_each_complete_card_boundary_is_not_a_complete_page(self):
        boundary = 0
        while True:
            boundary = HTML.find("</article>", boundary)
            if boundary < 0:
                break
            boundary += len("</article>")
            with self.subTest(boundary=boundary), self.assertRaisesRegex(ValueError, "complete"):
                parse_trending_html(HTML[:boundary])

    def test_missing_page_end_cannot_validate_even_after_list_closes(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            parse_trending_html(HTML[: HTML.rindex("</html>")])

    def test_nested_metric_text_is_captured_until_its_own_closing_tag(self):
        nested = HTML.replace("1,587 stars today", "<span>1,587</span> stars today")
        self.assertEqual(parse_trending_html(nested)[-1]["stars_today"], 1587)

    def test_mismatched_heading_link_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "repository"):
            parse_trending_html(HTML.replace('href="/milind-soni/OpenMausBot"', 'href="/wrong/project"'))

    def test_duplicate_heading_links_are_rejected(self):
        malformed = HTML.replace(
            "OpenMausBot</a>  </h2>", 'OpenMausBot</a><a href="/wrong/project">wrong/project</a></h2>'
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            parse_trending_html(malformed)

    def test_nested_new_card_cannot_overwrite_an_unclosed_card(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            parse_trending_html(HTML.replace("</article>", "", 1))
