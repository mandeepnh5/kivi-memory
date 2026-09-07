"""Unit tests for the deterministic core. Run: python -m tests.test_core"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import Engine
from app.phonetics import bounded_distance, phonetic_key, sounds_alike
from app.pipeline import run as run_pipeline
from app.store import Store, connect
from app.textutil import replace_spans


def test_phonetic_key():
    # transliteration variants collapse to one key
    assert phonetic_key("Aaditya") == phonetic_key("adithya") == phonetic_key("Aditya")
    assert phonetic_key("kiwi") == phonetic_key("Kivi")
    assert phonetic_key("Lakshmi") == phonetic_key("Laxmi")
    assert phonetic_key("Saurabh") == phonetic_key("Sourabh")
    # unrelated words stay apart
    assert phonetic_key("there") != phonetic_key("their")
    assert phonetic_key("meeting") != phonetic_key("kivi")


def test_bounded_distance():
    assert bounded_distance("aditya", "aditya", 1) == 0
    assert bounded_distance("aditya", "adtya", 1) == 1
    assert bounded_distance("aditya", "xyz", 1) == 2      # capped at limit+1
    assert bounded_distance("ab", "ba", 1) == 1           # transposition


def test_sounds_alike_gate():
    assert sounds_alike("Aditya", "Aaditya")
    assert sounds_alike("kiwi", "Kivi")
    assert not sounds_alike("meeting", "sync")            # rewrite, not phonetic


def test_replace_spans():
    assert replace_spans("ask aditya now", [(4, 10, "Aaditya")]) == "ask Aaditya now"


def _engine() -> Engine:
    return Engine(Store(connect(":memory:")))


def test_learning_threshold_and_idempotency():
    engine = _engine()
    engine.observe("ask dhruv", "Ask Dhruv.", "Ask Dhruvv.")
    assert engine.store.get_term("Dhruvv").status == "watching"
    out1 = run_pipeline("Dhruv is here.", engine.store, record_trace=False)
    assert out1.output == "Dhruv is here."               # one sighting: no action
    engine.observe("dhruv again", "Dhruv again.", "Dhruvv again.")
    assert engine.store.get_term("Dhruvv").status == "trusted"
    out2 = run_pipeline("Dhruv is here.", engine.store, record_trace=False)
    assert out2.output == "Dhruvv is here."              # two sightings: corrects
    out3 = run_pipeline(out2.output, engine.store, record_trace=False)
    assert out3.output == out2.output                    # idempotent on own output


def test_replay_reproduces_state():
    # Comparing state before and after replay is not enough on its own: a
    # replay that did nothing at all would pass it. So wipe the folded tables
    # first and assert the state comes BACK from the event log, which is the
    # actual product claim ("terms are a fold over events").
    engine = _engine()
    engine.add_term("Kivi", "product", "the product")
    engine.observe("x", "Ask Dhruv.", "Ask Dhruvv.")
    before = [(t.preferred, t.trust, sorted(t.misspellings))
              for t in engine.store.all_terms()]
    assert before, "fixture built no state to replay"

    engine.store.clear_state()
    assert engine.store.all_terms() == [], "folded state was not wiped"

    replayed = engine.replay()
    after = [(t.preferred, t.trust, sorted(t.misspellings))
             for t in engine.store.all_terms()]
    assert after == before                       # rebuilt, not merely untouched
    assert replayed == len(engine.store.events())   # every event was folded


def test_common_word_never_learned():
    engine = _engine()
    engine.observe("x", "Send thier report.", "Send their report.")
    assert engine.store.get_term("their") is None


def test_proper_noun_escape():
    # a colleague named after a common word IS learnable (capitalized mid-sentence)
    engine = _engine()
    engine.observe("x", "Ask joy to send it.", "Ask Joy to send it.")
    assert engine.store.get_term("Joy") is not None


def test_case_only_revert():
    engine = _engine()
    engine.add_term("Priya", "person", None)
    report = engine.observe("x", "Tell priya about it.", "Tell priya about it.",
                            presented="Tell Priya about it.")
    assert report["reverted"], "user undoing a case-only correction must count"
    assert engine.store.get_term("Priya").trust <= 0


def test_partial_revert_is_not_a_confirmation():
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    report = engine.observe("x", "Ask aditya and adithya to join.",
                            "Ask Aaditya and adithya to join.",
                            presented="Ask Aaditya and Aaditya to join.")
    assert any(r["preferred"] == "Aaditya" for r in report["confirmed"])
    assert any(r["preferred"] == "Aaditya" for r in report["reverted"])
    assert engine.store.get_term("Aaditya").trust <= 0


def test_multiword_revert():
    engine = _engine()
    engine.add_term("Gajendra Circle", "place", None)
    report = engine.observe("x", "Meet at gajendra circle gate.",
                            "Meet at gajendra circle gate.")
    assert any(r["preferred"] == "Gajendra Circle" for r in report["reverted"])


def test_malformed_llm_decision_degrades():
    from app.llm import JudgeResult

    class BrokenJudge:
        available = True

        def judge(self, sentence, items):
            # schema-incomplete decision: no "apply", no "preferred"
            return JudgeResult({0: {"index": 0}}, "fake", 0, 0, 0.0)

    engine = _engine()
    engine.add_term("Kivi", "product", "the product")
    result = run_pipeline("I ate a kiwi today.", engine.store,
                          judge=BrokenJudge(), record_trace=False)
    assert result.output == "I ate a kiwi today."      # degraded, not crashed
    assert result.candidates[0]["decision"] == "llm-error"


def test_explicit_add_updates_spelling():
    engine = _engine()
    engine.observe("x", "Ask dhruv to come.", "Ask dhruvv to come.")
    assert engine.store.get_term("dhruvv").preferred == "dhruvv"
    engine.add_term("Dhruvv", "person", None)
    assert engine.store.get_term("dhruvv").preferred == "Dhruvv"


def test_context_guard_conditional():
    engine = _engine()
    engine.add_term("Kivi", "product", "the product")
    engine.submit("set_trust", {"preferred": "Kivi", "trust": 4})
    # user reverted a runtime (LLM-path) correction in a food sentence
    report = engine.observe("x", "I ate a kiwi for breakfast.",
                            "I ate a kiwi for breakfast.",
                            presented="I ate a Kivi for breakfast.")
    assert report["reverted"]
    term = engine.store.get_term("Kivi")
    assert term.trusted                                   # 4 - 2 = still trusted
    assert term.misspellings["kiwi"]["guard"]                # conditional guard
    # similar context -> deterministic learned restraint, no LLM needed
    r1 = run_pipeline("He ate a kiwi at breakfast.", engine.store,
                      record_trace=False)
    assert r1.candidates[0]["decision"] == "skipped:guarded"
    # different context -> still open for contextual judgment
    r2 = run_pipeline("The kiwi dashboard is loading.", engine.store,
                      record_trace=False)
    assert r2.candidates[0]["decision"] != "skipped:guarded"


def test_context_guard_unconditional_for_names():
    engine = _engine()
    engine.add_term("Zerodha", "org", None)
    engine.submit("set_trust", {"preferred": "Zerodha", "trust": 4})
    engine.observe("x", "Send it to Zeroda.", "Send it to Zeroda.")
    term = engine.store.get_term("Zerodha")
    assert term.misspellings["zeroda"]["guard"] == []        # name-like: no condition
    r = run_pipeline("Zeroda called about the demo.", engine.store,
                     record_trace=False)
    assert r.candidates[0]["decision"] == "skipped:vetoed"


def test_retired_terms():
    engine = _engine()
    engine.observe("x", "Ask Dhruv to come.", "Ask Dhruvv to come.")
    assert engine.store.get_term("Dhruvv") is not None
    engine.delete_term("Dhruvv")
    report = engine.observe("x", "Dhruv is waiting.", "Dhruvv is waiting.")
    assert any(i["reason"] == "retired" for i in report["ignored"])
    assert engine.store.get_term("Dhruvv") is None       # stayed deleted
    engine.add_term("Dhruvv", "person", None)            # explicit re-add wins
    assert engine.store.get_term("Dhruvv").status == "trusted"
    engine.observe("x", "Dhruv again.", "Dhruvv again.")  # learnable again
    assert engine.store.get_term("Dhruvv").trust >= 2


def test_variant_spelling_reinforces_one_term():
    # user alternates spellings: must strengthen ONE term, never create a
    # same-sound duplicate; the first-learned preferred is kept (per README)
    engine = _engine()
    engine.observe("", "ask aditya to come", "ask Aaditya to come")
    engine.observe("", "aditya is here", "Aaditya is here")
    engine.observe("", "tell aditya the plan", "tell Adithya the plan")  # variant spelling
    terms = engine.store.all_terms()
    assert len(terms) == 1 and terms[0].preferred == "Aaditya"
    assert terms[0].trust >= 3                       # variant edit counted
    out = run_pipeline("Ping aditya now.", engine.store, record_trace=False)
    assert out.output == "Ping Aaditya now."            # no conflict created


def test_wordlist_data_integrity():
    # the safety list is DATA - renames must never touch its words
    from app.wordlist import COMMON_WORDS, is_commonish
    assert "surface" in COMMON_WORDS
    assert "misspelling" not in COMMON_WORDS
    assert is_commonish("surfaces")           # inflection inherits caution


def test_empty_name_rejected():
    engine = _engine()
    assert "error" in engine.add_term("   ")
    assert "error" in engine.add_term("!!!")
    assert engine.store.all_terms() == []


def test_digit_bearing_term_roundtrip():
    # "GPT4": the digit is part of the token, so the correction is exact and
    # idempotent; spellings the tokenizer cannot round-trip are rejected at
    # add time instead of splicing stray characters on every run
    engine = _engine()
    engine.add_term("GPT4", "product", None)
    out = run_pipeline("Ask gpt4 to summarise.", engine.store, record_trace=False)
    assert out.output == "Ask GPT4 to summarise."
    again = run_pipeline(out.output, engine.store, record_trace=False)
    assert again.output == out.output
    # digits are part of the key: the bare "gpt" must NOT splice in "GPT4",
    # but the split "gpt 4" joins to it cleanly
    out = run_pipeline("the gpt 4 demo is ready", engine.store, record_trace=False)
    assert out.output == "the GPT4 demo is ready"
    assert "error" in engine.add_term("GPT-4")
    assert "error" in engine.add_term("A/B Corp")
    # a pure-digit word anchors its own span and never leaks elsewhere
    engine.add_term("Sector 7", "place", None)
    out = run_pipeline("meet in sector 7 today", engine.store, record_trace=False)
    assert out.output == "meet in Sector 7 today"
    assert run_pipeline(out.output, engine.store,
                        record_trace=False).output == out.output
    out = run_pipeline("the sector is quiet", engine.store, record_trace=False)
    assert out.output == "the sector is quiet"
    # digit runs never collapse: 511 is not 51, and quantities stay intact
    engine.add_term("Area 51", "place", None)
    out = run_pipeline("head to area 511 now", engine.store, record_trace=False)
    assert out.output == "head to area 511 now"
    out = run_pipeline("head to area 51 now", engine.store, record_trace=False)
    assert out.output == "head to Area 51 now"
    assert "error" in engine.add_term("7")     # digit-only is not a word


def test_title_casing_is_not_name_evidence():
    engine = _engine()
    engine.observe("x", "meeting notes for the team", "Meeting Notes For The Team")
    engine.observe("x", "weekly notes for the group", "Weekly Notes For The Group")
    # two-word headings are the most common shape and must not slip through,
    # and a leading year must not disguise one
    engine.observe("x", "project notes", "Project Notes")
    engine.observe("x", "release notes", "Release Notes")
    engine.observe("x", "2024 release notes", "2024 Release Notes")
    engine.observe("x", "2025 release notes", "2025 Release Notes")
    engine.observe("x", "2024 notes", "2024 Notes")   # single alphabetic word
    engine.observe("x", "2025 notes", "2025 Notes")
    assert engine.store.all_terms() == []
    engine.observe("x", "subject: their quarterly report",
                   "Subject: Their quarterly report")
    assert engine.store.all_terms() == []
    # deliberate tradeoff: a fully capitalized two-word line reads as a
    # heading, so a case-only "call priya" -> "Call Priya" does not learn
    # (a spelling edit or a longer sentence still does)
    engine.observe("x", "call priya", "Call Priya")
    assert engine.store.get_term("Priya") is None
    engine.observe("x", "ask priya to call back", "Ask Priyaa to call back")
    assert engine.store.get_term("Priyaa") is not None


def test_join_revert_detected():
    # undoing a join correction ("a ditya" -> "Aaditya") must count as a
    # revert even though the token counts differ across the alignment
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    report = engine.observe("x", "ask a ditya to come", "ask a ditya to come")
    assert any(r["preferred"] == "Aaditya" for r in report["reverted"])
    out = run_pipeline("ask a ditya to come", engine.store, record_trace=False)
    assert out.output == "ask a ditya to come"


def test_unseen_lowercase_spelling_needs_context():
    # an exact key match is not enough when the spelling is a lowercase word
    # the system has never seen the user correct: it may be plain English
    # the wordlist lacks ("dew" ~ Dev). Proven forms still correct.
    engine = _engine()
    for name in ("Dev", "Neel", "Sunny", "Ram", "Jay"):
        engine.add_term(name, "person", None)
    for s in ("The morning dew was heavy on the grass.", "The score was nil at half time.",
              "It is sunny outside, bring a hat.", "My laptop needs more ram to run this.",
              "A blue jay landed on the fence."):
        assert run_pipeline(s, engine.store, record_trace=False).output == s
    out = run_pipeline("Ask dev to review it.", engine.store, record_trace=False)
    assert out.candidates[0]["decision"] == "skipped:context-unavailable"
    assert run_pipeline("Ask Dev to review it.", engine.store,      # capitalized
                        record_trace=False).candidates == []         # already preferred
    engine.observe("x", "Ask dev to come.", "Ask Dev to come.")     # user proves it
    engine.observe("x", "dev is here.", "Dev is here.")
    assert run_pipeline("Ping dev now.", engine.store,
                        record_trace=False).output == "Ping Dev now."


def test_identifiers_are_never_edited():
    # emails, URLs, paths, handles and env vars are dictated verbatim;
    # editing a word inside one breaks it
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    engine.add_term("Kivi", "product", None)
    for s in ("Mail aditya.kumar@sarvam.ai about it.",
              "Run C:/users/aditya/kivi/app.py now.",
              "Set KIVI_HOST before starting.",
              "Ping @aditya on slack.",
              "The file is aditya_report_v2.pdf here."):
        assert run_pipeline(s, engine.store, record_trace=False).output == s
    # ordinary prose around the same words still corrects: a trailing
    # period ends a sentence, a hyphen and a comma are punctuation, and
    # none of them make the word part of an identifier
    for s, want in (("Ask Aditya.", "Ask Aaditya."),
                    ("Aditya-ji is here.", "Aaditya-ji is here."),
                    ("Meet Aditya, then leave.", "Meet Aaditya, then leave.")):
        assert run_pipeline(s, engine.store, record_trace=False).output == want


def test_delete_missing_term_no_event():
    engine = _engine()
    before = len(engine.store.events())
    assert engine.delete_term("Nobody") == {"deleted": False}
    assert len(engine.store.events()) == before


def test_revert_reported_once():
    # both scoring paths (deterministic replay and `presented`) see the same
    # revert; the user must be told about it once, not twice
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    formatted = "Ask Aditya to review it."
    report = engine.observe("x", formatted, formatted,
                            presented="Ask Aaditya to review it.")
    assert len(report["reverted"]) == 1
    assert engine.store.get_term("Aaditya").trust == 0    # penalty applied once


def test_divergent_spelling_hint_reaches_judge():
    # when the preferred already appears verbatim elsewhere in the sentence,
    # the judge request must carry that as evidence for the ambiguous span
    from app.llm import JudgeResult

    class Capture:
        available = True
        seen = None

        def judge(self, sentence, items):
            Capture.seen = items
            return JudgeResult({items[0]["index"]: {
                "apply": False, "preferred": "Kivi", "reason": "fruit"}},
                "fake", 0, 0, 0.0)

    engine = _engine()
    engine.add_term("Kivi", "product", None)
    r = run_pipeline("The Kivi demo crashed while I was eating a kiwi.",
                     engine.store, judge=Capture(), record_trace=False)
    assert "already appears elsewhere" in Capture.seen[0].get("hint", "")
    assert r.output == "The Kivi demo crashed while I was eating a kiwi."
    run_pipeline("I ate a kiwi today.", engine.store, judge=Capture(),
                 record_trace=False)
    assert "already appears" not in Capture.seen[0].get("hint", "")


def test_env_overrides_tolerate_junk():
    # a host can inject an empty or malformed value; nothing that has no
    # bearing on the setting should break because of it
    import os
    from app.config import _float_env, _int_env
    for bad in ("", "   ", "abc", "15   (default)"):
        os.environ["KIVI_TEST_VAL"] = bad
        assert _int_env("KIVI_TEST_VAL", 8000) == 8000
        assert _float_env("KIVI_TEST_VAL", 15.0) == 15.0
    os.environ["KIVI_TEST_VAL"] = "9123"
    assert _int_env("KIVI_TEST_VAL", 8000) == 9123
    assert _float_env("KIVI_TEST_VAL", 15.0) == 9123.0
    del os.environ["KIVI_TEST_VAL"]
    assert _int_env("KIVI_TEST_VAL", 8000) == 8000


def test_reset_is_atomic_on_bad_seed():
    # a broken seed file must leave the existing database untouched
    import tempfile
    from pathlib import Path as P
    engine = _engine()
    engine.add_term("Kivi", "product", None)
    engine.observe("x", "Ask dhruv.", "Ask Dhruvv.")
    events_before = len(engine.store.events())
    bad = P(tempfile.mkdtemp()) / "seed.jsonl"
    bad.write_text('{"type": "add_term", "payload": {"preferred": "Good"}}\n'
                   '{broken json\n', encoding="utf-8")
    try:
        engine.reset(bad)
        raise AssertionError("malformed seed must raise")
    except ValueError:                      # JSONDecodeError is a ValueError
        pass
    assert len(engine.store.events()) == events_before
    assert engine.store.get_term("Kivi") is not None
    bad.write_text('{"type": "observation", "payload": {"asr": "x"}}\n',
                   encoding="utf-8")       # parses, but the fold must fail
    try:
        engine.reset(bad)
        raise AssertionError("bad payload must raise")
    except KeyError:
        pass
    assert len(engine.store.events()) == events_before   # rolled back whole
    assert engine.store.get_term("Kivi") is not None


def test_guard_words_recency_wins():
    # a full guard cache must evict OLD words, never newly learned restraint
    engine = _engine()
    engine.add_term("Kivi", "product", None)
    term = engine.store.get_term("Kivi")
    engine.store.record_misspelling(term.id, "kiwi", "t0", veto_delta=1,
                                    guard_words={f"a{i:02d}" for i in range(24)})
    engine.store.record_misspelling(term.id, "kiwi", "t1",
                                    guard_words={"zesty", "smoothie"})
    guard = engine.store.get_term("Kivi").misspellings["kiwi"]["guard"]
    assert "zesty" in guard and "smoothie" in guard
    assert len(guard) <= 24
    # the other half of the claim: eviction trims the old words, it does not
    # throw the whole history away
    assert any(w.startswith("a") for w in guard), "all older restraint was lost"


def test_nfc_normalization_on_add():
    # NFD input (combining accent, as some IMEs emit) must be accepted and
    # stored as the same term as its precomposed NFC form
    engine = _engine()
    assert "error" not in engine.add_term("Zoe\u0308")    # decomposed in
    assert engine.store.get_term("Zo\u00eb") is not None  # stored precomposed
    assert len(engine.store.all_terms()) == 1


def test_lookup_matches_the_form_the_user_types():
    # writes normalise to NFC, so reads must too: a name typed the decomposed
    # way on one machine has to find the term added the precomposed way on
    # another, or it can be added and never deleted
    engine = _engine()
    engine.add_term("Zo\u00eb", "person", None)             # precomposed in
    assert engine.store.get_term("Zoe\u0308") is not None   # decomposed lookup
    assert engine.delete_term("Zoe\u0308") == {"deleted": True}
    assert engine.store.all_terms() == []



def test_already_correct_span_blocks_a_shorter_match():
    # a longer span written exactly as its preferred wins its text even though
    # the answer is "leave it alone" - otherwise a shorter overlapping term
    # edits inside an already-correct name and the pipeline oscillates
    engine = _engine()
    engine.add_term("Aaditya Kumar", "person", None)
    engine.add_term("Aadityaa", "person", None)
    # positive control: without it, a pipeline that edits nothing at all would
    # satisfy the idempotency loop below and this test would prove nothing
    assert run_pipeline("Ask aditya kumar to review.", engine.store,
                        record_trace=False).output == "Ask Aaditya Kumar to review."
    text = "Ask Aaditya Kumar to review."
    for _ in range(3):
        out = run_pipeline(text, engine.store, record_trace=False).output
        assert out == text, f"not idempotent: {out!r}"
        text = out


def test_identifier_guard_survives_an_intervening_hyphen():
    # the giveaway character can sit further along the chunk than the
    # neighbouring character: a one-character lookaround walks past it
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    engine.add_term("Sarvam", "org", None)
    for s in ("Mail aditya-kumar@sarvam.ai today.",
              "aditya-k.sharma@corp.example.com",
              "export SARVAM-KEY=1",
              "file: aditya-notes.md",
              "Release 1.2.3-aditya shipped.",
              "See https://x.com/aditya/repo now."):
        assert run_pipeline(s, engine.store, record_trace=False).output == s, s
    # trimming sentence punctuation keeps quoted prose correctable
    for s, want in (('He said "Aditya." loudly.', 'He said "Aaditya." loudly.'),
                    ("(Aditya) will come.", "(Aaditya) will come."),
                    ("Tell Aditya!", "Tell Aaditya!")):
        assert run_pipeline(s, engine.store, record_trace=False).output == want, s


def test_multiword_match_never_reflows_text():
    # the replacement is written with single spaces, so a term is only allowed
    # to match across a single space - never a newline, tab or double space,
    # which it would silently collapse and merge two lines into one
    engine = _engine()
    engine.add_term("Sarvam AI", "org", None)
    for s in ("- sarvam\n  ai\n- next item", "Welcome to sarvam\nai team.",
              "call sarvam  ai now", "call sarvam\tai now"):
        assert run_pipeline(s, engine.store, record_trace=False).output == s, repr(s)
    assert run_pipeline("the sarvam ai demo", engine.store,
                        record_trace=False).output == "the Sarvam AI demo"


def test_undoing_a_join_vetoes_only_the_split():
    # the recorded misspelling must be the text as written ("a ditya"), not the
    # two tokens glued together ("aditya") - vetoing the glued form would
    # silence the correction the term exists for
    engine = _engine()
    engine.observe("x", "Ask Aditya to review.", "Ask Aaditya to review.")
    engine.observe("x", "Aditya will join.", "Aaditya will join.")
    text = "Give a ditya of the plan."
    shown = run_pipeline(text, engine.store, record_trace=False).output
    assert shown == "Give Aaditya of the plan."
    engine.observe("x", text, text, presented=shown)          # user puts it back
    heard = engine.store.get_term("Aaditya").misspellings
    assert heard["a ditya"]["vetoes"] == 1
    assert heard["aditya"]["vetoes"] == 0, "the unsplit spelling must stay usable"
    engine.add_term("Aaditya", "person", None)                # user restores trust
    assert run_pipeline("Ask Aditya to review.", engine.store,
                        record_trace=False).output == "Ask Aaditya to review."
    assert run_pipeline(text, engine.store, record_trace=False).output == text


def test_replay_keeps_the_decision_traces():
    # traces record what was decided at runtime; no event replays them, so a
    # rebuild of the folded tables must leave them alone
    engine = _engine()
    engine.add_term("Aaditya", "person", None)
    for i in range(3):
        run_pipeline(f"Ask Aditya {i}.", engine.store, record_trace=True)
    assert engine.store.row_counts()["traces"] == 3
    # the replay has to do real work here, or this says nothing: wipe the
    # folded tables so a no-op rebuild would leave the terms missing
    engine.store.clear_state()
    engine.replay()
    assert engine.store.row_counts()["traces"] == 3      # traces survived
    assert engine.store.get_term("Aaditya") is not None   # terms came back
    engine.reset()                       # reset means wipe, traces included
    assert engine.store.row_counts()["traces"] == 0


def test_capitalised_unseen_spelling_in_a_phrase_needs_context():
    # a never-seen capitalised spelling inside a longer capitalised phrase is
    # more likely a different entity that sounds alike than a variant of the
    # user's own term, so it must not be edited deterministically
    engine = _engine()
    engine.reset()
    for s in ("Adithya Birla Group announced results.",
              "Sharvam Joshi joined the team.",
              "Sarwam Textiles is a different company.",
              "SARVAM AI RAISED A ROUND.",
              "SUBJECT: SARVAM KIWI LAUNCH"):
        assert run_pipeline(s, engine.store, record_trace=False).output == s, s
    # a lone capitalised variant is still the phonetic key doing its job
    for s, want in (("Adithya pushed the fix.", "Aaditya pushed the fix."),
                    ("Ask Aditya to send the deck.", "Ask Aaditya to send the deck."),
                    ("Remind me to return Aditya's charger now.",
                     "Remind me to return Aaditya's charger now.")):
        assert run_pipeline(s, engine.store, record_trace=False).output == want, s


def test_correction_keeps_the_case_it_found():
    # an all-caps span is a heading or emphasis; writing mixed case into it
    # breaks the line even when the spelling fix itself is right. A shouted
    # line with capitalised neighbours needs the judge (it could be an
    # organisation - see test_shouted_phrase_is_not_rewritten), so the case
    # rule is checked on a span whose neighbours are ordinary words.
    engine = _engine()
    engine.reset()
    out = run_pipeline("ADITYA", engine.store, record_trace=False).output
    assert out == "AADITYA", out
    out = run_pipeline("shout ADITYA loudly", engine.store, record_trace=False).output
    assert out == "shout AADITYA loudly", out


def test_shouted_phrase_is_not_rewritten():
    # upper case is how organisations and missions are written, so a shouted
    # phrase gets the same protection as "Aditya Birla Group" in title case.
    # The cost is that a shouted instruction also waits for the judge, which
    # is the cheap direction to be wrong in.
    engine = _engine()
    engine.reset()
    for text in ("ADITYA BIRLA GROUP ANNOUNCED ITS RESULTS.",
                 "ISRO LAUNCHED ADITYA YESTERDAY."):
        out = run_pipeline(text, engine.store, record_trace=False)
        assert out.output == text, out.output
        assert out.candidates[0]["decision"].startswith("skipped")


def test_traces_limit_is_clamped():
    # ?limit is user input and must not reach SQLite unbounded
    from app.server import api_traces, app as _app
    _app.state.db_path = ":memory:"
    assert api_traces(limit=10**30) == []          # would overflow SQLite raw
    assert api_traces(limit=-5) == []


def test_pipeline_survives_any_judge_failure():
    # "the pipeline never crashes" has to hold for a judge that raises
    # something other than LLMError, and for a malformed endpoint URL
    import os
    class Boom:
        available = True
        def judge(self, sentence, items):
            raise RuntimeError("kaboom")
    engine = _engine()
    engine.add_term("Kivi", "product", None)
    r = run_pipeline("I ate a kiwi today.", engine.store, judge=Boom(),
                     record_trace=False)
    assert r.output == "I ate a kiwi today."
    assert r.candidates[0]["decision"] == "llm-error"

    from app.llm import Judge
    os.environ["LLM_BASE_URL"] = "not a url"
    os.environ.setdefault("GROQ_API_KEY", "test-key-not-used")
    try:
        r = run_pipeline("I ate a kiwi today.", engine.store, judge=Judge(),
                         record_trace=False)
        assert r.output == "I ate a kiwi today."
        assert r.candidates[0]["decision"] == "llm-error"
    finally:
        del os.environ["LLM_BASE_URL"]
        if os.environ.get("GROQ_API_KEY") == "test-key-not-used":
            del os.environ["GROQ_API_KEY"]   # do not leak a fake credential


def test_shared_first_name_is_not_rewritten():
    # Someone else's name that merely shares a first name with a known term
    # must not be edited, however many times the bare spelling was corrected.
    # "Proven spelling" is evidence about the word on its own, not a licence to
    # rewrite a stranger's surname - and this is the deterministic path, so no
    # judge is available to catch it.
    engine = _engine()
    engine.add_term("Aaditya", "person", "colleague")
    for _ in range(3):                       # make "aditya" thoroughly proven
        engine.observe("x", "Ask Aditya.", "Ask Aaditya.")
    assert engine.store.get_term("Aaditya").misspellings["aditya"]["seen"] >= 2

    for text in ("I met Aditya Sharma from the Delhi office.",
                 "Aditya Birla Group announced its results.",
                 "We drove past Aditya Nagar on the way."):
        out = run_pipeline(text, engine.store, record_trace=False)
        assert out.output == text, f"rewrote a different person: {out.output!r}"
        assert out.candidates[0]["decision"].startswith("skipped"), \
            out.candidates[0]["decision"]

    # a digit or a possessive must not let the guard walk past the phrase
    for text in ("We watched Aditya 369 last night.",
                 "Aditya's Kitchen on Main Street does good biryani."):
        assert run_pipeline(text, engine.store, record_trace=False).output == text

    # the control: the same term among ordinary words still corrects
    assert run_pipeline("Ask Aditya to review.", engine.store,
                        record_trace=False).output == "Ask Aaditya to review."
    assert run_pipeline("Is Aditya's laptop back?", engine.store,
                        record_trace=False).output == "Is Aaditya's laptop back?"


def test_judge_response_parsing():
    # _parse_decisions is the only place untrusted model output is
    # interpreted, so every shape the docstring claims to handle is asserted
    # here. Pure function: no key and no network needed.
    from app.llm import _parse_decisions

    plain = '{"decisions":[{"index":0,"apply":true,"preferred":"Kivi","reason":"r"}]}'
    assert _parse_decisions(plain)[0]["preferred"] == "Kivi"

    fenced = "```json\n" + plain + "\n```"
    assert _parse_decisions(fenced)[0]["apply"] is True     # code fences

    chatty = "Sure, here you go:\n" + plain + "\nHope that helps!"
    assert _parse_decisions(chatty)[0]["reason"] == "r"     # prose either side

    # a string index must not silently orphan the span it belongs to
    stringy = '{"decisions":[{"index":"2","apply":false,"preferred":"x","reason":""}]}'
    assert 2 in _parse_decisions(stringy)

    multi = ('{"decisions":[{"index":0,"apply":true,"preferred":"a","reason":""},'
             '{"index":1,"apply":false,"preferred":"b","reason":""}]}')
    assert sorted(_parse_decisions(multi)) == [0, 1]

    for junk in ("no json here", "", "{", "{}", '{"decisions":"nope"}',
                 '{"decisions":[{"apply":true}]}'):
        try:
            _parse_decisions(junk)
            raise AssertionError(f"should have rejected {junk!r}")
        except (ValueError, KeyError, TypeError):
            pass          # the pipeline turns any of these into llm-error


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # any exception is a failure, not an abort
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
