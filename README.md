# scorer

A console app that takes a URL, downloads the music, and transcribes it into sheet music
(PDF + MusicXML + MIDI): one line per staff, with notes struck together written as chords.

```
uv run scorer "https://www.youtube.com/watch?v=..." 
```

Any source [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports works: YouTube, SoundCloud,
Bandcamp, a direct `.mp3`/`.wav` link, a `ytsearch1:<query>` search, or a local file path.
ffmpeg is bundled (via `imageio-ffmpeg`), so nothing needs to be installed besides `uv`.

## Output

Written to `./scores/<title>.*`:

- `.pdf` – the engraved sheet music, ready to read or print
- `.musicxml` – open in MuseScore, Finale, Sibelius, Dorico, or Flat.io to edit
- `.mid` – MIDI of the transcription

## Options

| Option | Purpose |
| --- | --- |
| `-o DIR` | output directory (default `scores`) |
| `-f pdf,musicxml,midi` | output formats (default: all three) |
| `--start S --duration S` | transcribe only an excerpt (recommended for long tracks) |
| `--split [NOTE]` | two-staff piano score: a treble staff for pitches from NOTE (default C4, middle C) up and a bass staff for those below |
| `--lowest C4 --highest C6` | pitch range to track; narrowing it to the melody's register helps a lot with accompanied music |
| `--tempo BPM` | override tempo detection |
| `--time-signature 3/4` | default `4/4` |
| `--grid 8` | quantise to eighths instead of sixteenths (cleaner, less detail) |
| `--min-note-ms 80` | discard shorter blips |
| `--polyphony 4` | most notes to stack in a chord on one staff (default 4); `--polyphony 1` writes each staff as a single line, with no chords |
| `--title`, `--keep-audio` | set score title / keep decoded WAV |

## API

The same job is available over HTTP: post a URL and an email address, and the score files
arrive by email as a zip. Start the server with

```
uv run scorer-api
```

and send it a job:

```
curl -X POST http://127.0.0.1:8000/transcriptions \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=...", "email": "you@example.com", "split": true}'
```

The request is accepted at once (`202` with a job id); the transcription runs in the
background, and the recipient then gets the PDF, MusicXML and MIDI zipped up, or a message
saying why it failed. Besides `url` and `email`, the body takes the CLI's options under the same
names: `title`, `split` (`true` for middle C, or a note name), `start`, `duration`, `tempo`,
`time_signature`, `grid`, `min_note_ms`, `lowest`, `highest` and `polyphony`. Interactive docs
are at `/docs`.

Mail goes out through [Mailgun](https://www.mailgun.com/), configured by environment
variables, which `scorer-api` also reads from a `.env` file in the working directory (keep it
out of git; `.gitignore` already lists it):

```
MAILGUN_API_KEY=...                     # a domain sending key (or the account's private API key)
MAILGUN_DOMAIN=mg.example.com           # a sending domain on the account
MAILGUN_FROM=scorer <scorer@mg.example.com>   # optional; defaults to scorer@<domain>
MAILGUN_API_BASE=https://api.eu.mailgun.net   # EU accounts only
```

A Mailgun sandbox domain only delivers to the addresses authorised for it in the Mailgun
dashboard (up to five); anything else is refused, and the server logs the refusal. For another
provider, set `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_FROM` and `SMTP_SECURITY` (`starttls`, the default, `ssl`, or `none`) instead. With
`SCORER_OUTBOX=<folder>` set, no mail is sent at all: each message is written to that folder as
an `.eml` file, which is handy for trying the API without a mail account.

Other settings: `SCORER_API_HOST` and `SCORER_API_PORT` (default `127.0.0.1:8000`),
`SCORER_API_WORKERS` (transcriptions run at once; default 1, they are CPU-bound), and
`SCORER_API_KEY`, which makes the endpoint require that key in an `X-API-Key` header. Set it
before exposing the server beyond your own machine: the endpoint will email anyone it is told
to. Jobs live in the server process, so one that is in flight when the server stops is lost,
and the recipient should simply submit it again.

## Deploying to Google Cloud Run

[deploy/](deploy/) holds a Terraform configuration that builds the container from the
[Dockerfile](Dockerfile) with Cloud Build, stores the Mailgun key and an API key in Secret
Manager, and runs the API as a Cloud Run service. It is a service rather than a Cloud Run
function on purpose: a function only has CPU while a request is in flight, and here the
transcription runs after the request has been answered, so the service keeps its CPU
allocated between requests (`cpu_idle = false`) and keeps one instance warm (`min_instances`).

```
cd deploy
cp terraform.tfvars.example terraform.tfvars   # project, region, Mailgun domain and key
terraform init
terraform apply
terraform output -raw api_key                  # what callers put in X-API-Key
```

`apply` needs `gcloud` logged in to the project; it enables the APIs, builds and pushes the
image (a new build whenever `src/`, the lockfile or the Dockerfile change), and prints the
service URL. The endpoint is reachable by anyone (`public = true`) but refuses requests
without the generated API key. Keep the state in a private bucket (see the `backend "gcs"`
comment in `versions.tf`): it contains both keys.

### Deploying from GitHub

[deploy-develop.yml](.github/workflows/deploy-develop.yml) runs the tests and then
`terraform apply` on every push to `develop` (a merged pull request arrives as a push), so
`develop` is always what is running on Cloud Run. It can also be started by hand from the
Actions tab with "Run workflow", from `develop` or `main`, with a plan-only option that shows
what would change without applying it. GitHub signs in to Google Cloud with Workload Identity
Federation: no service-account key is stored in GitHub, and only workflows from this
repository's `develop` and `main` branches can act as the deployer. Setting that up is a
one-time job, done as a project owner:

```
cd deploy/bootstrap
cp terraform.tfvars.example terraform.tfvars    # project id; the repository and branches default to theonej/urlody, develop and main
terraform init
terraform apply
terraform output github_variables
```

[test.yml](.github/workflows/test.yml) runs the test suite on pushes to `main` and on pull
requests.

Then, in the repository's settings, create a `develop` environment and give it the variables
the output lists (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`,
`GCP_DEPLOYER_SERVICE_ACCOUNT`, `MAILGUN_DOMAIN`, optionally `MAILGUN_FROM`) and the secrets
`MAILGUN_API_KEY` and, optionally, `SCORER_API_KEY` (left out, the API key is generated on the
first deploy and kept in the Terraform state; read it with `terraform output -raw api_key`).
The bootstrap also creates the Cloud Build staging bucket and grants the deployer access to the
state bucket, so the workflow needs nothing else. The deployment's URL appears on the
workflow run's summary page and on the `develop` environment.

Two things to know about running it there. A job in flight when Cloud Run replaces an
instance is lost; for a busier deployment, queue jobs through Cloud Tasks and run the
transcription inside the request instead. And YouTube often blocks downloads from cloud
addresses as automated traffic, so YouTube links may fail from Cloud Run where direct audio
links, SoundCloud and Bandcamp work; the recipient gets the failure by email.

## How it works

The CLI and the API both run `pipeline.py`, which goes through these steps:

1. **Fetch**: yt-dlp downloads the best audio stream, and ffmpeg decodes it to mono 22.05 kHz.
2. **Transcribe** (`transcribe.py`): harmonic/percussive separation, then pYIN pitch tracking
   of the dominant line, median smoothing, and segmentation into notes at pitch changes and
   detected onsets. Notes that jump an octave or more away from their neighbours (pYIN's usual
   slip) are pulled back into register. With `--split`, the tracker runs once per staff; the
   bass staff's audio is low-passed at the split first, because a pitch tracker hearing both
   voices reports sub-harmonics of the melody as bass notes. Tempo and beat phase come from
   librosa's beat tracker.
3. **Chords** (also `transcribe.py`): a single-pitch tracker hears a chord as one wandering
   note, so at every detected onset the tones struck together are read off a constant-Q
   spectrum instead. Tones are taken out of the spectrum strongest first, each one's partials
   being removed before the next is sought (so a note's harmonics don't pass for more notes,
   while a partial standing well above its neighbours still reveals the octave or fifth
   sharing it), and a tone counts as struck only if its energy is new since just before the
   onset, so a chord held under a moving melody is written once, not re-struck on every melody
   note. pYIN's line then fills in what happens between onsets: legato changes stay notes, and
   its wandering between the tones of a ringing chord is folded back into the chord.
4. **Engrave** (`score.py`): notes are quantised to the beat grid (notes landing on the same
   grid point become a chord), the key is estimated (Krumhansl), accidentals are spelled for
   the key, and a clef is chosen. music21 then builds measures, ties, and beams.
5. **Render** (`render.py`): verovio lays the score out on US-letter pages as SVG, and
   svglib/reportlab write the PDF. No external programs are needed.

## Limitations

Each staff is written as **one voice**: a chord or note lasts until the next attack on that
staff, so a note held under moving notes on the same staff is cut off rather than tied through,
and independent inner voices are not separated. Chord detection reads the tones struck at each
onset, so it works best on clean piano (or guitar) recordings: an octave or fifth doubling of a
loud bass note is sometimes missed because it hides in the bass note's own partials, and in a
mix with drums or several instruments, whatever else sounds at an onset can land in the chord.
For a vocal or other single line, `--polyphony 1` turns chord detection off. Use `--split` for
piano recordings and `--lowest`/`--highest` to focus on a register, and expect to tidy the
result in a notation editor.
A learned model such as Spotify's basic-pitch would do better on dense polyphony, but it
currently doesn't install on Python ≥ 3.12.
