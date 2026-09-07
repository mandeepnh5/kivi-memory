# The data

The brief gives one example sentence and no dataset, so building the data was
part of the work. This file lists everything the system was built and judged
on, with the actual lines.

- **Starting memory** - what a demo begins from (`seed/`)
- **Dictated lines** - spoken into the Kivi Windows app (`eval/harvested_raw.json`)
- **Written lines** - composed by category (`eval/cases.jsonl`, which also
  holds the 11 cases built from the dictated lines above)
- **Generated input** - invented at run time by the fuzzer (`tests/fuzz.py`)

---

## 1. Starting memory - `seed/seed_events.jsonl`

Five terms, stored as **seven events** rather than five rows, because memory
here is a fold over an event log. `python -m app.cli reset` replays them and
rebuilds the same state every time.

| Term | How it got there | Ends at |
|---|---|---|
| Kivi | added directly, with a note | trusted |
| Sarvam | added directly | trusted |
| Aaditya | learned from two observations | trusted |
| Shreyaa | learned from one observation | watching, never corrects |
| Advaith | added, then forced to 0 | muted |

That spread is deliberate. One demo database contains a term that acts, one
still watching, and one switched off, so all three states are visible with no
setup.

---

## 2. Dictated lines - `eval/harvested_raw.json`

**25 takes**, spoken into the Kivi Windows app. One line per hotkey press, at
normal speed, without self-correcting mid-sentence, because the mistakes are
the data. The app saves every capture with both the speech model's text and
the formatter's text, and all 25 pairs are kept here exactly as they came
back, including the clean ones.

**What I spoke, and why:**

| Group | Lines |
|---|---|
| **A. Names in ordinary sentences** | "Remind me to return Aditya's charger before he leaves for the weekend."; "Adithya from the second floor fixed the Wi-Fi router again."; "Shreya and Dhruv said they will come to the mess around 8."; "Ask Srinivasan sir if Saurabh can also join the project review."; "Tell Nikhil that Pranav already booked the badminton court." |
| **B. Product, org and place names** | "The Sarvam Kiwi demo crashed right in front of everyone."; "I have to finish the KiviMark before the deadline."; "My Zerodha account got locked again third time this month."; "Submit the assignment on Moodle before midnight or at 12."; "Meeting Aishwarya near Gajendra Circle at 5."; "Upload the poster to the Insti Drive and share it in the group." |
| **C. The fruit, on purpose** | "I added a kiwi and some cornflakes for breakfast today."; "Apparently the kiwi bird cannot fly at all." |
| **D. Nothing personal (controls)** | "There has to be an easier way to submit this form."; "The quiz got postponed to four o'clock tomorrow."; "The endsem for thermodynamics is on the 23rd."; "The professor said attendance is mandatory for the lab session."; "Can you check the document once and tell me if anything is missing?"; "Let me know when you reach. I will come down to the gate." |
| **E. Hindi and Hinglish** | "kal aaditya ko bolna ki Kiwi wala demo ready hai"; "shreya se poochna ki notes bheje ya nahi"; "yaar this week is too hectic matlab 1 din bhi free nahi hai"; "Maine push the viva to next Friday, so we have time." |
| **F. The same name again, worded differently** | "Aditya said he will pay me back after the mess bill."; "Is the Sarvam Kivi thing working now or not?" |

**What came back:**

| Spoken | Came back as |
|---|---|
| Adithya | **Itya** |
| Moodle | **nodal** |
| quiz | **cruise** |
| endsem | **endsum** |
| KiviMark | **Kiwi mark right up**, which the formatter turned into "Kivi markup" |
| Maine | **Me na**, which the formatter turned back into "Maine" |
| Hindi speech | came back in Hindi script, and the formatter transliterated it |
| Sarvam Kiwi | already written as **Kivi** by the app's own dictionary |

The last two shaped the design. My layer runs after the formatter, so it sees
Roman script rather than Hindi script. Everything in this repo is written in
Roman script for that reason: it is the only script the system ever reads, so
the Hindi takes are recorded here the way the formatter emits them. And
because some words arrive already corrected, running on already-correct text
has to change nothing, which is why idempotency is an invariant here and not
a nice-to-have.

**Ten takes became eleven eval cases** (one take feeds both a correction case
and a learning case). Four of them are misses the system cannot solve, Itya,
"Me na", nodal and the KiviMark split, kept as cases that assert it leaves
them alone instead of guessing.

---

## 3. Written lines - `eval/cases.jsonl`

**52 cases**, one for each rule the design states, each carrying the memory
it assumes. Grouped by what they hunt:

**A. Plain corrections**
- Ask Aditya to send the deck before lunch.
- Tell the team kivi is live.

**B. Edge forms** (possessive, sentence-initial, split words, digits)
- Aditya's laptop is with IT.
- Aditya will demo today.
- Ask a ditya to join the call.
- Ask gpt4 to summarise the thread.

**C. Transliteration variants**
- Adithya pushed the fix.
- Ask Adtya to review it. (one edit beyond the rules, so it needs judgment)
- Laxminarayanan sir is taking the extra class on Thursday.

**D. Multi-word and split names**
- Meet me at gajendra circle gate.
- Push the changes to the kiwi mark staging branch tonight.

**E. Traps that must never be touched**
- I ate a kiwi for breakfast.
- The kiwi is a flightless bird from New Zealand.
- I bought two kiwis at the market.
- I picked a ripe berry from the bush. (a friend named Berry is in memory)
- The nickel battery died again. (a friend named Nikhil is in memory)
- As if that helps. (a friend named Asif is in memory)
- The morning dew was heavy on the grass. | It is sunny outside, bring a hat.
  (friends named Dev and Sunny are in memory; an ordinary English word is not
  edited without context, whatever it sounds like and however many times the
  user has corrected that spelling elsewhere)
- Mail aditya.kumar@sarvam.ai when you can. | Run C:/users/aditya/kivi/app.py now.
  (a name inside an email address or a file path is left alone)
- I met Aditya Sharma from the Delhi office yesterday. | Aditya Birla Group
  announced its results. | We watched Aditya 369 last night. | ADITYA BIRLA
  GROUP ANNOUNCED ITS RESULTS. (strangers, organisations and titles that
  happen to share a first name with a known term, even though that spelling
  has been corrected many times. The last two exist because a digit and a
  shouted line both used to slip past the guard. This case runs in both
  modes, so the judge has to refuse them too.)
- The meeting is scheduled for four o'clock tomorrow.
- There is a bug in the build.
- Ask Aaditya to review the Sarvam Kivi service. (already correct)

**F. Trap and term in the same sentence**
- The Kivi demo crashed while I was eating a kiwi.
- Kash still has not returned the cash from the movie tickets.

**G. Not proven yet**
- Send it to Shreya today. (seen once, so it watches and does not act)
- Advait left the company last month. (a muted term)

**H. Learning over time**
- Ask Saurabh to check the logs. -> Ask Sourabh to check the logs.
  (then again, and only after the second does it start correcting)
- Ask joy to send the notes. -> Ask Joy to send the notes.
  (a name that is also an English word)

**I. Unlearning, when the user pushes back**
- Send the invoice to Zeroda. -> left as Zeroda (undone, so never touch it again)
- Tell priya about it. -> left lowercase (a case-only undo still counts)
- Meet at gajendra circle gate. -> left alone (a multi-word undo)
- Ask aditya and adithya to join. -> Ask Aaditya and adithya to join.
  (one kept and one undone, in the same sentence)
- ask a ditya to come -> left split (undoing a joined-word fix)
- I ate a kiwi for breakfast. -> undone, and the restraint is remembered for
  food sentences while product sentences stay correctable

**J. Deleted stays deleted**
- Ask Dhruv to come. -> Ask Dhruvv to come., then delete Dhruvv, then make the
  same correction again. It must not come back.

**K. Two people, one sound**
- Call adithya tonight. (Aditya the cousin, Aaditya the colleague)
- Ask adithya to merge the platform branch. (the note decides)
- I told adithya about it, not Aditya, he was not even there.

**L. Formatting is not vocabulary**
- Send there report. -> Send their report. (grammar, never learned)
- meeting notes for the team -> Meeting Notes For The Team (a heading)

**M. Hinglish**
- Kal aditya ko bolna ki kivi wala demo ready hai.
- Priya ko file bhejna. -> Priyaa ko file bhejna.

**N. With and without the judge**
- Did the kiwi deployment finish? | The Sarvam Kiwi service went down during
  the demo. (the judge applies these) - and the same spans under `--no-llm`,
  where the right answer is to leave them alone.

**48 of the 66 scored runs expect nothing to change at all.** Most of the
dataset tests restraint, because a wrong correction costs more than a missed
one.

---

## 4. Generated input - `tests/fuzz.py`

I wrote both the code and the cases above, so a third layer uses input
neither of them shaped: random invented names, mangled the way ASR mangles
them, random memories, and junk (emoji, other scripts, 3000-character
strings). Every trial checks the same promises. Only trusted terms may
correct. Common words are never learned. Empty memory changes nothing.
Running twice equals running once. Replay rebuilds the same state. Nothing
crashes. It is seeded, so a failure prints the seed that reproduces it.

---

## 5. Not in the data

- **No contacts, files or messages.** The system learns only from edits made
  to the user's own transcripts.
- **No audio.** The brief waives speech recognition, so every input is text.
- **No native-script matching.** Roman-script Hinglish is in scope; matching
  inside Hindi or Tamil script is not.
- **No real personal data.** The names are common Indian given names used as
  fixtures.

---

## 6. Where the results land

Commands are in [RUN.md](RUN.md). Per case, `eval/results/results.jsonl`
records the input, what was expected, what happened, the memory state at that
step, and the reason behind every decision. The headline numbers land in
`eval/results/summary.md`. Both are committed, so the numbers in the README
can be checked against a fresh run.
