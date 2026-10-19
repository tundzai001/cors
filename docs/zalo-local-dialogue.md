# Local CORS dialogue

CORS questions use RiveScript 1.15.0 to select an intent and RapidFuzz 3.14.6 to
resolve misspelled province names. They run inside the existing Python backend.
There is no model, training job, AI API key or additional service.

Install the updated backend requirements before restarting `server.py`:

```powershell
python -m pip install -r requirements.txt
```

The rules are in `cors_dashboard/backend/app/data/cors_dialogue.rive`. Rules return
intent names; Python attaches the source health and requested NOAA observations.
They handle status, stability, field measurement, FIX troubleshooting, atmospheric
detail and future-time questions. Future-time replies identify current conditions
as observations, not forecasts. This is a bounded set of conversations, not an
unrestricted language model.

Reply scope follows each chat's saved `delivery_mode`, for groups and private
chats alike. In `cors_only`, regional questions combine source availability,
regional ionosphere observations, geomagnetic conditions and practical survey
advice. This mode does not ask for a mountpoint or claim access to a receiver's
RTCM/FIX telemetry. Missing/delayed observations remain identified, and the
operational advice differs for terrain work, control points and source/storm risk.
The existing NOAA caches are reused.
Routine replies show the regional TEC estimate and timestamp without a historical
baseline comparison. Detailed replies label the raw NOAA anomaly range as
“Dị thường”, consistent with the web map. The bot does not present an internally
computed 30-day reference series; the application has no such comparison view.

In `standard`, ordinary status/stability questions retain concise diagnostic
follow-ups without fetching NOAA. Detailed atmospheric questions can request the
observations. Source availability alone never proves local RTCM/FIX quality.
Automatic source-status notifications use a separate declarative formatter;
they do not ask conversational questions or create sender context.

Province lookup recognizes common abbreviations (HN, HCM/TPHCM, BRVT) and joined
names. Fuzzy matching compares short word windows at a minimum score of 90.
Ambiguous abbreviations such as DN ask the user to choose Đà Nẵng or Đồng Nai.
Multiple provinces, including an exact name followed by a misspelled second name,
ask for the intended scope rather than silently inheriting the previous province.
An unknown explicitly named area also clears the previous scope.

RiveScript arrays define synonyms once for exact and fuzzy intent matching.
RapidFuzz handles nearby spellings and transposed characters; negation and loss
qualifiers are retained. A low-confidence or tied match cannot dispatch an
administrative command or grant permissions.

Conversation location stays scoped to the existing chat-and-sender key, expires
after 20 minutes, and is bounded to 512 entries. RiveScript user state is cleared
after each synchronous match; shared match/result caches are bounded to 256
entries. A follow-up such as “chi tiết đi” only uses that sender's current scope.
Explicit commands and Base/Rover requests retain their original routing.

The diagnostic topic accepts a short answer such as “3s” after a FIX problem,
then distinguishes “tăng liên tục” from “giữ ổn định”. A single age value never
establishes a failure or measurement accuracy. “Nói rõ hơn” follows the current
topic; “ngắn gọn thôi” summarizes it. Local definitions cover FIX/FLOAT, RTCM,
NTRIP, mountpoint, GGA and correction age. Greetings, definitions and clarification
do not fetch live source health or NOAA data. CORS-only greetings/help only offer
public source and atmospheric questions. No user-derived object code runs.

The FIX/FLOAT and GGA definitions follow the manufacturer's GPGGA field reference:
https://docs.novatel.com/OEM7/Content/Logs/GPGGA.htm

## Service structure

`app/zalo_bot_service.py` remains the public entry point. It owns the SDK lifecycle,
webhook dispatch, outgoing trace hooks and the shared `zalo_bot_service` instance.
`ZaloBotService` composes handler mixins from `app/zalo_bot/`:

| File | Responsibility |
| --- | --- |
| `common.py` | Command names, permission tables and Vietnamese intent helpers |
| `administration.py` | Authorization and administrative callback boundaries |
| `editor.py` | Configuration parsing, menus and editor field selection |
| `conversation.py` | Sender-scoped workflows, onboarding and natural-language dispatch |
| `commands.py` | Station/rover commands, confirmation, linking and public menus |
| `cors.py` | Public CORS replies, context and status notifications |
| `alerts.py` | Device alert formatting, scoped delivery and polling |

Imports of `app.zalo_bot_service.ZaloBotService` and the singleton remain valid.
The structural move preserved all 97 method bodies, signatures and decorators;
relative imports were adjusted for the package, with a lazy service import in the
editor's static formatting helper to retain its existing class lookup.

Validation covers report content, misspellings, ambiguous provinces, fresh/stale
observations, source failures, scoped follow-ups, per-chat delivery modes and
public command permissions:

```powershell
python -m unittest tests.test_zalo_cors_report tests.test_zalo_public_cors
```

The test environment must use test database settings. Tests mock outbound bot
delivery and source observations; they do not send messages to actual Zalo chats.


## Upstream research

- RiveScript-Python: https://github.com/aichaos/rivescript-python — arrays,
  substitutions, conditional replies and included topics are used here.
- RapidFuzz: https://github.com/rapidfuzz/RapidFuzz — bounded extraction, score
  cutoffs and Damerau-Levenshtein distance are used here.
- FlashText: https://github.com/vi3k6i5/flashtext — keyword extraction was considered;
  the current vocabulary does not need an additional matching library.
- SymSpellPy: https://github.com/mammothb/symspellpy — dictionary-based spelling
  correction was considered; the existing short intent/province vocabulary is
  handled by RapidFuzz without adding a dictionary service or package.
