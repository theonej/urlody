"""HTTP API: post a URL and an email address, get the score files back by email.

    uv run scorer-api            # serves http://127.0.0.1:8000

One endpoint, ``POST /transcriptions``, accepts the job and answers at once;
the transcription then runs in the background and the PDF, MusicXML and MIDI
go to the recipient zipped, or a note saying why it failed. Mail goes out
through Mailgun (``MAILGUN_API_KEY`` and ``MAILGUN_DOMAIN``), or over plain
SMTP (``SMTP_HOST`` etc.), or into a folder of .eml files for local testing
(``SCORER_OUTBOX``); a ``.env`` file in the working directory is read for
these. Set ``SCORER_API_KEY`` to require it in an ``X-API-Key`` header: the
endpoint will send mail to any address it is given.
"""

from __future__ import annotations

import logging
import os
import smtplib
import tempfile
import threading
import uuid
import warnings
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Literal, Protocol

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, HttpUrl

from . import __version__
from .pipeline import Options, Result, TranscriptionError, run, slugify

log = logging.getLogger("scorer.api")


class TranscriptionRequest(BaseModel):
    """What to transcribe, who gets it, and the same choices the CLI offers."""

    url: HttpUrl = Field(description="YouTube, SoundCloud, Bandcamp or direct audio URL")
    email: EmailStr = Field(description="where to send the zipped score files")
    title: str | None = Field(default=None, max_length=200, description="score title; defaults to the track title")
    split: bool | str = Field(
        default=False,
        description="two-staff piano score: true splits at middle C, or give the note to split at",
    )
    start: float | None = Field(default=None, ge=0, description="start offset in seconds")
    duration: float | None = Field(default=None, gt=0, description="length to transcribe in seconds")
    tempo: float | None = Field(default=None, gt=0, description="force the tempo in BPM")
    time_signature: str = "4/4"
    grid: Literal[4, 8, 16, 32] = Field(default=16, description="shortest note value to quantise to")
    min_note_ms: float = Field(default=80, ge=0, description="drop notes shorter than this")
    lowest: str = "C2"
    highest: str = "C7"
    polyphony: int = Field(default=4, ge=1, description="most notes in a chord on one staff; 1 for a single line")

    def options(self) -> Options:
        split = "C4" if self.split is True else (self.split or None)
        return Options(
            title=self.title, start=self.start, duration=self.duration, tempo=self.tempo,
            time_signature=self.time_signature, grid=self.grid, min_note_ms=self.min_note_ms,
            lowest=self.lowest, highest=self.highest, split=split, polyphony=self.polyphony,
        )


class Accepted(BaseModel):
    id: str
    status: Literal["accepted"] = "accepted"
    message: str


class MailError(Exception):
    """The mail provider would not take the message."""


class Mailer(Protocol):
    sender: str

    def send(self, message: EmailMessage) -> None: ...


def _stamp(message: EmailMessage, sender: str) -> None:
    message["From"] = sender
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()


@dataclass
class Mailgun:
    """Sends through Mailgun's HTTP API (its SMTP relay needs separate SMTP credentials)."""

    sender: str
    domain: str
    api_key: str
    base: str = "https://api.mailgun.net"  # https://api.eu.mailgun.net for EU accounts
    client: httpx.Client | None = None  # a test double; a real client is made when None

    def send(self, message: EmailMessage) -> None:
        _stamp(message, self.sender)
        text = message.get_body(preferencelist=("plain",)).get_content()
        files = [
            ("attachment", (part.get_filename(), part.get_payload(decode=True), part.get_content_type()))
            for part in message.iter_attachments()
        ]
        client = self.client or httpx.Client(timeout=60)
        response = client.post(
            f"{self.base}/v3/{self.domain}/messages",
            auth=("api", self.api_key),
            data={"from": self.sender, "to": message["To"], "subject": message["Subject"], "text": text},
            files=files or None,
        )
        if response.status_code != 200:
            raise MailError(f"Mailgun answered {response.status_code}: {response.text.strip()}")
        log.info("mail to %s accepted by Mailgun: %s", message["To"], response.json().get("id", "?"))


@dataclass
class Smtp:
    """Sends over SMTP; ``security`` is starttls, ssl or none."""

    sender: str
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    security: str = "starttls"

    def send(self, message: EmailMessage) -> None:
        _stamp(message, self.sender)
        smtp_class = smtplib.SMTP_SSL if self.security == "ssl" else smtplib.SMTP
        try:
            with smtp_class(self.host, self.port, timeout=60) as smtp:
                if self.security == "starttls":
                    smtp.starttls()
                if self.username:
                    smtp.login(self.username, self.password or "")
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"SMTP to {self.host} failed: {exc}") from exc
        log.info("mail to %s sent via %s", message["To"], self.host)


@dataclass
class Outbox:
    """Writes each message to a folder as an .eml file, for trying things out."""

    sender: str
    folder: Path

    def send(self, message: EmailMessage) -> None:
        _stamp(message, self.sender)
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{uuid.uuid4().hex}.eml"
        path.write_bytes(message.as_bytes())
        log.info("mail to %s written to %s", message["To"], path)


def mailer_from_env() -> Mailer:
    """The mailer the environment asks for: an outbox folder wins (it is a test
    switch), then Mailgun, then SMTP."""
    env = os.environ.get
    if outbox := env("SCORER_OUTBOX"):
        return Outbox(sender=env("SMTP_FROM") or env("MAILGUN_FROM") or "scorer@localhost", folder=Path(outbox))
    if key := env("MAILGUN_API_KEY"):
        domain = env("MAILGUN_DOMAIN")
        if not domain:
            raise RuntimeError("MAILGUN_DOMAIN is needed alongside MAILGUN_API_KEY")
        return Mailgun(
            sender=env("MAILGUN_FROM") or f"scorer@{domain}",
            domain=domain,
            api_key=key,
            base=(env("MAILGUN_API_BASE") or "https://api.mailgun.net").rstrip("/"),
        )
    if host := env("SMTP_HOST"):
        return Smtp(
            sender=env("SMTP_FROM") or f"scorer@{host}",
            host=host,
            port=int(env("SMTP_PORT", "587")),
            username=env("SMTP_USERNAME"),
            password=env("SMTP_PASSWORD"),
            security=(env("SMTP_SECURITY") or "starttls").lower(),
        )
    raise RuntimeError(
        "no way to send mail: set MAILGUN_API_KEY and MAILGUN_DOMAIN (and MAILGUN_FROM), or "
        "SMTP_HOST (and SMTP_FROM, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, "
        "SMTP_SECURITY=starttls|ssl|none), or SCORER_OUTBOX=<folder> to write .eml files instead"
    )


def success_message(to: str, url: str, result: Result, archive: Path) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"Your score: {result.title}"
    if result.split:
        upper, lower = result.staff_notes
        notes = f"{upper} treble + {lower} bass notes ({result.chords} chords)"
    else:
        notes = f"{sum(result.staff_notes)} notes ({result.chords} chords)"
    message.set_content(
        f"Your transcription of {result.title} is attached.\n"
        f"\n"
        f"  Source   {url}\n"
        f"  Tempo    ~{result.tempo:.0f} BPM\n"
        f"  Key      {result.key_name}\n"
        f"  Time     {result.time_signature}\n"
        f"  Notes    {notes} across {result.measures} measures\n"
        f"\n"
        f"The zip holds the score as a PDF, as MusicXML (open it in MuseScore, Finale, "
        f"Sibelius, Dorico or Flat.io to edit) and as MIDI.\n"
    )
    message.add_attachment(
        archive.read_bytes(), maintype="application", subtype="zip", filename=archive.name
    )
    return message


def failure_message(to: str, url: str, reason: str) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"Could not transcribe {url}"
    message.set_content(
        f"Sorry, the transcription of {url} failed:\n\n  {reason}\n\n"
        f"A track with a clearer melody, or a different link to it, may do better.\n"
    )
    return message


def process(request: TranscriptionRequest, mailer: Mailer, jobs: threading.Semaphore) -> None:
    """Transcribe, zip and mail; on failure, mail the reason instead."""
    url, to = str(request.url), str(request.email)
    with jobs, tempfile.TemporaryDirectory(prefix="scorer-api-") as tmp:
        try:
            result = run(url, Path(tmp), request.options(), log=log.info)
            archive = Path(tmp) / f"{slugify(result.title)}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
                for file in result.files:
                    zipped.write(file, arcname=file.name)
            message = success_message(to, url, result, archive)
        except TranscriptionError as exc:
            log.warning("transcription of %s failed: %s", url, exc)
            message = failure_message(to, url, str(exc))
        except Exception:
            log.exception("transcription of %s crashed", url)
            message = failure_message(to, url, "an unexpected error; the server has logged it")
        try:
            mailer.send(message)
        except Exception:
            log.exception("could not send mail to %s", to)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mailer = mailer_from_env()
    app.state.jobs = threading.Semaphore(int(os.environ.get("SCORER_API_WORKERS", "1")))
    app.state.api_key = os.environ.get("SCORER_API_KEY")
    yield


app = FastAPI(
    title="scorer",
    version=__version__,
    description="Transcribes the music at a URL into a score and emails it as PDF, MusicXML and MIDI.",
    lifespan=lifespan,
)


@app.post("/transcriptions", status_code=status.HTTP_202_ACCEPTED, response_model=Accepted)
def create_transcription(
    body: TranscriptionRequest,
    request: Request,
    background: BackgroundTasks,
    x_api_key: str | None = Header(default=None),
) -> Accepted:
    """Queue a transcription; the score files are emailed to ``email`` when done."""
    state = request.app.state
    if state.api_key and x_api_key != state.api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a valid X-API-Key header is required")
    try:
        body.options().validate()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    job_id = uuid.uuid4().hex[:12]
    log.info("job %s: %s for %s", job_id, body.url, body.email)
    background.add_task(process, body, state.mailer, state.jobs)
    return Accepted(
        id=job_id,
        message=f"Transcribing {body.url}; the score will be emailed to {body.email}.",
    )


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from ``path`` into the environment, not overriding
    variables already set. Keeps secrets like the Mailgun key out of the repo."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> None:
    """Console entry point ``scorer-api``: serve on SCORER_API_HOST:SCORER_API_PORT."""
    import uvicorn

    warnings.filterwarnings("ignore", category=RuntimeWarning, module="numba")
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        "scorer.api:app",
        host=os.environ.get("SCORER_API_HOST", "127.0.0.1"),
        # PORT is what Cloud Run and similar platforms set.
        port=int(os.environ.get("SCORER_API_PORT") or os.environ.get("PORT") or 8000),
        log_config=None,
    )
