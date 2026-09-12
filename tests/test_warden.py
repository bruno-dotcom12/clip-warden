"""What the checker must never get wrong.

Every case here is a way a real submission has been thrown out: a second over
the limit, a missing tag, a scratch track on a campaign that adds its own sound.
The point of the suite is that these fail loudly in a build rather than quietly
in a payout.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "warden-shared", "scripts"))

os.environ["WARDEN_DIR"] = tempfile.mkdtemp(prefix="warden-tests-")

import warden
import warden_rules as R


def rules(**over):
    base = R.blank()
    base.update({"id": "test", "name": "Test campaign", "schema": 1})
    base["video"].update({"duration_min_s": 10, "duration_max_s": 30,
                          "width": 1080, "height": 1920, "aspect": "9:16",
                          "audio": "forbidden"})
    base["caption"].update({"required_hashtags": ["#AD"],
                            "required_mentions": ["@brand"],
                            "banned_terms": ["aposta"]})
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def media(**over):
    base = {"width": 1080, "height": 1920, "duration_s": 20.0,
            "audio_codec": None, "size_mb": 9.0, "codec": "h264"}
    base.update(over)
    return base


def levels(findings, level):
    return [m for lv, m in findings if lv == level]


class RuleSet(unittest.TestCase):
    def test_template_validates(self):
        good = rules()
        self.assertEqual(R.validate(good), [])

    def test_typo_in_a_key_is_caught(self):
        broken = rules()
        broken["video"]["duration_max"] = 30          # the real key is duration_max_s
        self.assertTrue(any("duration_max" in p for p in R.validate(broken)))

    def test_inverted_window_is_caught(self):
        broken = rules(video={"duration_min_s": 40, "duration_max_s": 10})
        self.assertTrue(any("above" in p for p in R.validate(broken)))

    def test_hashtag_without_hash(self):
        broken = rules(caption={"required_hashtags": ["AD"]})
        self.assertTrue(any("does not start with #" in p for p in R.validate(broken)))


class Checking(unittest.TestCase):
    def test_a_clean_clip_passes(self):
        found = warden.check(rules(), media(), "hook @brand #AD", [])
        self.assertEqual(warden.verdict(found), warden.OK)

    def test_one_second_over_is_a_reject(self):
        found = warden.check(rules(), media(duration_s=30.4), "@brand #AD", [])
        self.assertEqual(warden.verdict(found), warden.REPROVA)
        self.assertTrue(any("over the 30s maximum" in m for m in levels(found, warden.REPROVA)))

    def test_under_the_minimum_is_a_reject(self):
        found = warden.check(rules(), media(duration_s=8), "@brand #AD", [])
        self.assertEqual(warden.verdict(found), warden.REPROVA)

    def test_wrong_resolution_is_a_reject(self):
        found = warden.check(rules(), media(width=720, height=1280), "@brand #AD", [])
        self.assertTrue(any("1080x1920" in m for m in levels(found, warden.REPROVA)))

    def test_scratch_audio_when_the_platform_adds_the_sound(self):
        found = warden.check(rules(), media(audio_codec="aac"), "@brand #AD", [])
        self.assertTrue(any("audio track" in m for m in levels(found, warden.REPROVA)))

    def test_missing_hashtag(self):
        found = warden.check(rules(), media(), "just a hook @brand", [])
        self.assertTrue(any("#AD" in m for m in levels(found, warden.REPROVA)))

    def test_case_and_accents_do_not_smuggle_a_banned_word(self):
        found = warden.check(rules(), media(), "APOSTÁ @brand #AD", [])
        self.assertTrue(any("bans" in m for m in levels(found, warden.REPROVA)))

    def test_tags_match_regardless_of_case(self):
        found = warden.check(rules(), media(), "@Brand #ad", [])
        self.assertEqual(warden.verdict(found), warden.OK)

    def test_the_cap_stops_free_work(self):
        capped = rules(posting={"per_clipper_cap": 2})
        found = warden.check(capped, media(), "@brand #AD", [{}, {}])
        self.assertTrue(any("cap is 2" in m for m in levels(found, warden.REPROVA)))

    def test_a_closed_campaign_is_a_reject(self):
        closed = rules(posting={"deadline": "2020-01-01"})
        found = warden.check(closed, media(), "@brand #AD", [])
        self.assertTrue(any("closed on" in m for m in levels(found, warden.REPROVA)))

    def test_silence_of_the_brief_is_reported_not_invented(self):
        quiet = R.blank()
        quiet.update({"id": "quiet", "schema": 1})
        quiet["unknown"] = ["whether edits may carry an embedded track"]
        found = warden.check(quiet, media(), None, [])
        self.assertEqual(warden.verdict(found), warden.OK)
        self.assertTrue(any("does not settle it" in m for m in levels(found, warden.ATENCAO)))

    def test_source_rules_are_never_claimed_as_checked(self):
        sourced = rules(sources={"allowed": ["the official archive"]})
        found = warden.check(sourced, media(), "@brand #AD", [])
        self.assertTrue(any("only you can confirm" in m for m in levels(found, warden.ATENCAO)))
        self.assertFalse(any("only you can confirm" in m for m in levels(found, warden.OK)))


class Packaging(unittest.TestCase):
    def test_the_caption_carries_what_the_brief_demands(self):
        path = warden.campaign_path("pack")
        packed = rules(id="pack", caption={"required_text": ["Season 4 now streaming"]})
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(packed, fh)
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            warden.main(["package", "--campaign", "pack", "--hook", "pov: he inherited it"])
        caption = buffer.getvalue()
        self.assertIn("pov: he inherited it", caption)
        self.assertIn("Season 4 now streaming", caption)
        self.assertIn("@brand", caption)
        self.assertIn("#AD", caption)

    def test_the_packaged_caption_passes_its_own_check(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            warden.main(["package", "--campaign", "pack", "--hook", "pov: he inherited it"])
        found = warden.check(rules(), media(), buffer.getvalue(), [])
        self.assertEqual(warden.verdict(found), warden.OK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
