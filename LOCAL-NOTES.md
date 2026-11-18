# SupoClip local notes (gameplay shorts setup)

Written 2026-11-19. This file lives on the branch `feat/source-range-skip` of the fork
`github.com/uditdamare/supoclip`. If you are an AI assistant picking this up on a new machine,
read this whole file first. The user wants 9:16 shorts from Twitch/YouTube/Kick gameplay VODs
for a YouTube channel, and plans to continue testing on 2026-11-20.

No API keys are written here. They live only in `.env`, which git ignores.

## Start on a new machine

Needs Docker with Compose (Docker Desktop on Windows/Mac) and Git. About 8 GB of RAM for Docker and
several GB of disk (images about 10 GB, plus about 1 GB per long ranged download).

```bash
git clone https://github.com/uditdamare/supoclip.git
cd supoclip
git checkout feat/source-range-skip     # main does NOT have the music, metric, range or skip work
cp .env.example .env
```

Edit `.env` and set at least:

```
ASSEMBLY_AI_API_KEY=...        # transcription (cloud)
GOOGLE_API_KEY=...             # while LLM is a Gemini model
LLM=google-gla:gemini-3-flash-preview
# After buying an Anthropic key (see "Next steps"):
# ANTHROPIC_API_KEY=...
# LLM=anthropic:claude-sonnet-5-5
```

Then:

```bash
docker compose up -d --build    # first build takes a while
```

Open http://localhost:3107 and create a new account (accounts and past tasks live in the database
volume on the old machine and do not move with git). API docs: http://localhost:8000/docs.

| What | Command |
|---|---|
| Stop | `docker compose stop` |
| Start again | `docker compose start` (use `docker compose up -d` after editing `.env` or code) |
| Logs | `docker compose logs -f worker` |
| Never run | `docker compose down -v` (deletes database and clips) |

Never restart the backend or worker while a task is processing: it kills the job.
After editing frontend `messages/*.json` run `docker compose up -d --build frontend` (only `src/` is mounted).

## What is on this branch (on top of upstream main)

1. `ea4fb4d` optional background music: audio files in `backend/bgm/` are mixed under each newly
   rendered clip; `BGM_VOLUME` in `.env` (default 0.12); empty folder means no music. Studio exports get none.
2. `f53aace` + `84f75fc` per-task LLM token totals saved in `tasks.stage_timings_json`
   (`llm_input_tokens`, `llm_output_tokens`, `llm_calls`), highlights path only.
3. `050f84d` + `311bb73` time range, skip timestamps and batches (below).
4. A migration, `backend/src/migrations/sql/20261116_0001_stripe_account_routing.sql`, fixing sign-in on a
   fresh database. Upstream `init.sql` lacks the column `users.stripe_account_id`, so every login returned
   HTTP 500. The backend applies the migration at startup; tested on a fresh database built from `init.sql`.

## Feature: time range, skip timestamps, batches

- Per task, for YouTube/Twitch/Kick links: `source_range` ("1:10:00-2:20:00") downloads and analyses only that
  part (yt-dlp ranges). `MAX_VIDEO_DURATION` (90 min default) applies to the range, so a 10-hour stream can be
  done in batches. Clip times are relative to the start of the range.
- `skip_ranges` ("12:30-18:45, 1:02:00-1:10:00", on the stream's own timestamps): removed from the transcript the
  AI reads, and clips touching them are dropped. Highlights only. Accurate to a few seconds.
- Web form: settings chip, "Range & skips" section (start/end, skip box, "split into batches of N minutes").
  Batches are contiguous (no gaps) and run one after another because the account renders one video at a time.
  `POST /tasks/source-info` reports a link's length. API body fields: `source_range`, `skip_ranges`.
- Section files are kept in `uploads/range-sources/<task>/` (about 1 GB per 70-minute 1080p batch) so regenerate and
  the editor work. Clear them by hand when not needed.
- Limits: cuts land on keyframes (a range may start a few seconds early); ranged tasks ignore `YOUTUBE_PROXY_URL`;
  a 70-minute 1080p60 Twitch batch is about 3.9 GB, above `YOUTUBE_DOWNLOAD_MAX_BYTES` (2 GB), so raise it;
  YouTube downloads ran at about 0.5 MB/s here (a 70-minute batch may take 30-40 minutes).
- Tested: invalid input is rejected with clear errors; a 3-minute ranged task with a skip made 4 clips, none in the
  skipped span; the transcript filter removed the 4 lines in the skipped span; regenerate reuses the saved section;
  a two-batch run queued in order and did not merge as duplicates; resume keeps the range; batch 1 completed.
- NOT tested: Twitch/Kick ranges, clicking through the form in a browser (it was only type-checked and linted), and a
  batch run all the way through (batch 2 stopped at the Gemini quota below).
- Unit tests: `backend/tests/unit/test_source_ranges.py`, `test_task_source_range_preflight.py`,
  `frontend/src/lib/time-ranges.test.ts`. One frontend test (`create/route.test.ts`) fails only inside the
  container because it expects the backend at `localhost`.

## Problem found: Gemini free tier

- Google's error: free tier of `gemini-3-flash` allows 20 requests per day. One task makes several calls
  (3+ windows, a final ranking, retries), so a few hours of video use it up.
- Earlier on 2026-11-18 the preview model also stalled (some calls took 150-213 s, other identical calls 3-10 s),
  so in two tasks one analysis window and the final ranking timed out and clips came from about two thirds of the transcript.
- Fix: enable billing on the Google AI Studio key, or switch to Claude (next steps).

## Measurements

| Stage | 32-min video | 27-min video | Runs on |
|---|---|---|---|
| Download | 4% | 49% | internet |
| Transcribing | 4% | 2% | AssemblyAI |
| Pick clips, scores, titles (one LLM step) | 55% | 31% | Gemini |
| Crop, captions, render | 36% | 18% | the PC's CPU |

- About 70K input and 25K output tokens per hour of video; platform does not matter, speech density does.
- Rendering is about 1.5 minutes per clip, one task at a time. More Docker CPU would not help much.
- Selection reads only the transcript, not the picture.

## How clips are controlled

- Count: `MAX_CLIPS` and `FAST_MODE_MAX_CLIPS` in `.env` (10; hard ceiling 20); about one clip per 5 minutes of source.
- Per task: `output_format` (`vertical_fit` = whole frame over blurred background, safest for gameplay),
  `caption_template` (default, hormozi, mrbeast, minimal, tiktok, neon, podcast), `clip_length`
  (short 15-30, medium 30-60, long 60-120 s). Hook titles cannot be switched off for highlights without a code edit.
- The per-task `instructions` field needs the account to be admin: `update users set is_admin=true where email='...'`.
  Suggested text: "Gameplay footage with streamer commentary. Pick moments where the reaction shows something happened:
  laughing, shouting, disbelief, clutch plays, close wins, fails, funny banter. Include one or two calm, serious moments.
  Start a few seconds before the moment and end right after the reaction. Skip menus, loading screens, queue waiting,
  sponsor reads and chat reading."
- `MAX_VIDEO_DURATION` is still 5400 (90 min) in `.env.example`; with ranges you do not need to raise it.

## Next steps (in order)

1. Buy an Anthropic API key. Put it in `.env` as `ANTHROPIC_API_KEY` and set `LLM=anthropic:claude-sonnet-5-5`
   (this model id is used elsewhere in the repo but was not tried for clip selection here). Restart with
   `docker compose up -d` when no task is processing. The same key switches on studio AI editing and AI-drafted captions.
2. Run one video through both models and compare clips (the app caches analysis per video; check how before clearing it).
3. Re-run a batch of a long stream (YouTube first) and watch the new token metric in `stage_timings_json`.
4. Try a Twitch link with a range (untested); raise `YOUTUBE_DOWNLOAD_MAX_BYTES` if the download size limit trips.
5. Click through the "Range & skips" section in the browser.
6. Idea: upload a finished clip and let the studio "AI edit" box (needs the Anthropic key) apply instructions such as
   "remove filler words and the opening pause". Upload and automatic captions work now; a short upload may come back
   as 1 clip, so check it keeps the whole thing.
7. Idea, not built: choose background music by mood (`bgm/funny`, `bgm/hype`, `bgm/chill`), with the model adding a
   mood label per clip and the start point at the loudest section.

## Rights and platform notes

- Credit the streamer in the description, but credit is not permission: check their clipping policy.
  Reused-content rules can limit monetization; your own commentary or editing helps.
- Music that is not licensed for reuse can trigger a Content ID claim; YouTube's own library avoids that.
- Publishing from SupoClip to YouTube needs a Google OAuth client in `.env` (not set up).
