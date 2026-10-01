import email
import email.policy
import io
import zipfile
from pathlib import Path

import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from scorer import api
from scorer.pipeline import Result, TranscriptionError

REQUEST = {"url": "https://www.youtube.com/watch?v=0nbj_FuISnI", "email": "someone@example.com"}


def fake_run(url, output_dir, options, log=lambda m: None):
    """Stands in for the real pipeline: writes three small files and reports on them."""
    files = []
    for suffix in (".pdf", ".musicxml", ".mid"):
        path = Path(output_dir) / f"My_Song{suffix}"
        path.write_bytes(suffix.encode() * 100)
        files.append(path)
    return Result(
        title="My Song", files=files, tempo=94.0, key="E- major", key_name="E-flat major",
        time_signature="4/4", measures=100, staff_notes=[519, 607], chords=551,
        split=bool(options.split),
    )


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("SCORER_OUTBOX", str(tmp_path / "outbox"))
    for variable in ("SMTP_HOST", "MAILGUN_API_KEY", "SCORER_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(api, "run", fake_run)
    return tmp_path / "outbox"


def sent_mail(outbox):
    files = sorted(outbox.glob("*.eml"))
    return [email.message_from_bytes(f.read_bytes(), policy=email.policy.default) for f in files]


def test_accepts_the_job_and_emails_the_zipped_score(outbox):
    with TestClient(api.app) as client:
        response = client.post("/transcriptions", json={**REQUEST, "split": True, "tempo": 94})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted" and len(body["id"]) == 12
    assert "someone@example.com" in body["message"]

    (message,) = sent_mail(outbox)
    assert message["To"] == "someone@example.com"
    assert message["Subject"] == "Your score: My Song"
    text = message.get_body(preferencelist=("plain",)).get_content()
    assert "519 treble + 607 bass notes (551 chords) across 100 measures" in text
    assert "E-flat major" in text and "~94 BPM" in text
    (attachment,) = message.iter_attachments()
    assert attachment.get_filename() == "My_Song.zip"
    with zipfile.ZipFile(io.BytesIO(attachment.get_payload(decode=True))) as zipped:
        assert sorted(zipped.namelist()) == ["My_Song.mid", "My_Song.musicxml", "My_Song.pdf"]


def test_failure_is_reported_by_email(outbox, monkeypatch):
    def failing_run(url, output_dir, options, log=lambda m: None):
        raise TranscriptionError("no pitched notes detected; try a track with a clearer melody")

    monkeypatch.setattr(api, "run", failing_run)
    with TestClient(api.app) as client:
        assert client.post("/transcriptions", json=REQUEST).status_code == 202
    (message,) = sent_mail(outbox)
    assert message["Subject"].startswith("Could not transcribe https://www.youtube.com/")
    assert "no pitched notes detected" in message.get_content()


@pytest.mark.parametrize(
    "bad, complaint",
    [
        ({"email": "not-an-address"}, "email"),
        ({"url": "/etc/passwd"}, "url"),
        ({"url": "ftp://example.com/song.mp3"}, "url"),
        ({"lowest": "C5", "highest": "C4"}, "lowest must be below highest"),
        ({"split": "C1"}, "split must lie between lowest and highest"),
        ({"time_signature": "waltz"}, "time signature must look like 3/4 or 6/8"),
        ({"polyphony": 0}, "polyphony"),
    ],
)
def test_rejects_bad_requests_without_mailing(outbox, bad, complaint):
    with TestClient(api.app) as client:
        response = client.post("/transcriptions", json={**REQUEST, **bad})
    assert response.status_code == 422
    assert complaint in response.text
    assert not outbox.exists() or not list(outbox.glob("*.eml"))


def test_api_key_is_required_when_configured(outbox, monkeypatch):
    monkeypatch.setenv("SCORER_API_KEY", "s3cret")
    with TestClient(api.app) as client:
        assert client.post("/transcriptions", json=REQUEST).status_code == 401
        assert client.post("/transcriptions", json=REQUEST, headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.post("/transcriptions", json=REQUEST, headers={"X-API-Key": "s3cret"}).status_code == 202
    assert len(sent_mail(outbox)) == 1


def test_refuses_to_start_without_a_way_to_send_mail(monkeypatch):
    for variable in ("SMTP_HOST", "MAILGUN_API_KEY", "SCORER_OUTBOX"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(RuntimeError, match="MAILGUN_API_KEY"):
        with TestClient(api.app):
            pass


def test_mailgun_is_chosen_from_the_environment(monkeypatch):
    for variable in ("SMTP_HOST", "SCORER_OUTBOX"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MAILGUN_API_KEY", "key-x")
    monkeypatch.setenv("MAILGUN_DOMAIN", "mg.example.org")
    mailer = api.mailer_from_env()
    assert isinstance(mailer, api.Mailgun)
    assert mailer.domain == "mg.example.org" and mailer.sender == "scorer@mg.example.org"
    monkeypatch.delenv("MAILGUN_DOMAIN")
    with pytest.raises(RuntimeError, match="MAILGUN_DOMAIN"):
        api.mailer_from_env()


def test_mailgun_posts_the_message_and_its_attachment(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], request.read()
        return httpx.Response(200, json={"id": "<abc@mg.example.org>", "message": "Queued. Thank you."})

    mailer = api.Mailgun(
        sender="scorer@mg.example.org", domain="mg.example.org", api_key="key-x",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = fake_run("http://x", tmp_path, api.TranscriptionRequest(**REQUEST).options())
    archive = tmp_path / "My_Song.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for file in result.files:
            zipped.write(file, arcname=file.name)
    mailer.send(api.success_message("someone@example.com", "http://x", result, archive))

    assert seen["url"] == "https://api.mailgun.net/v3/mg.example.org/messages"
    assert seen["auth"] == "Basic " + base64.b64encode(b"api:key-x").decode()
    body = seen["body"]
    assert b'name="to"\r\n\r\nsomeone@example.com' in body
    assert b'name="subject"\r\n\r\nYour score: My Song' in body
    assert b"551 chords" in body
    assert b'name="attachment"; filename="My_Song.zip"' in body


def test_mailgun_rejection_is_an_error():
    mailer = api.Mailgun(
        sender="s@d", domain="d", api_key="k",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, text="Forbidden"))),
    )
    with pytest.raises(api.MailError, match="400"):
        mailer.send(api.failure_message("someone@example.com", "http://x", "why"))


def test_mail_trouble_is_logged_not_raised(outbox, monkeypatch, caplog):
    def broken_send(message):
        raise api.MailError("provider down")

    with TestClient(api.app) as client:
        monkeypatch.setattr(client.app.state.mailer, "send", broken_send)
        assert client.post("/transcriptions", json=REQUEST).status_code == 202
    assert "could not send mail to someone@example.com" in caplog.text
