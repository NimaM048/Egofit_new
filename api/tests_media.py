from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from api.decorators import _readable_validation_message, api_endpoint
from sport_shop.middleware import SelectableGZipMiddleware


class SelectableGZipMiddlewareTests(SimpleTestCase):
    """Text payloads may be gzipped; binary media must pass through untouched."""

    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, content_type: str, body: bytes):
        middleware = SelectableGZipMiddleware(
            lambda request: HttpResponse(body, content_type=content_type)
        )
        request = self.factory.get("/", HTTP_ACCEPT_ENCODING="gzip")
        return middleware(request)

    def test_video_response_is_not_compressed(self):
        body = b"\x00\x01\x02\x03" * 512
        response = self._run("video/mp4", body)
        self.assertFalse(response.has_header("Content-Encoding"))
        self.assertEqual(response.content, body)

    def test_video_response_still_varies_on_accept_encoding(self):
        response = self._run("video/mp4", b"x" * 1024)
        self.assertIn("Accept-Encoding", response.get("Vary", ""))

    def test_json_response_is_compressed(self):
        response = self._run("application/json", b'{"ok": true}' * 200)
        self.assertEqual(response.get("Content-Encoding"), "gzip")


class ResilientMediaViewTests(TestCase):
    """Range requests back Android's MediaPlayer and HTML5 <video> seeking."""

    def setUp(self):
        # FileResponse keeps the file handle open until the response is closed;
        # allow the temp dir to be discarded even if a test did not consume it.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.body = bytes(range(256)) * 4
        (self.root / "clip.mp4").write_bytes(self.body)

    def _get(self, **extra):
        with override_settings(MEDIA_ROOT=self.root):
            return self.client.get("/media-files/clip.mp4", **extra)

    def test_range_request_returns_partial_content(self):
        response = self._get(HTTP_RANGE="bytes=0-9")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes 0-9/{len(self.body)}")
        self.assertEqual(response["Content-Length"], "10")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertEqual(b"".join(response.streaming_content), self.body[:10])

    def test_open_ended_range_returns_tail(self):
        start = len(self.body) - 16
        response = self._get(HTTP_RANGE=f"bytes={start}-")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], f"bytes {start}-{len(self.body) - 1}/{len(self.body)}")
        self.assertEqual(b"".join(response.streaming_content), self.body[start:])

    def test_unsatisfiable_range_returns_416(self):
        response = self._get(HTTP_RANGE="bytes=99999-100000")
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response["Content-Range"], f"bytes */{len(self.body)}")

    def test_full_request_sets_content_length(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Length"], str(len(self.body)))
        self.assertEqual(response["Accept-Ranges"], "bytes")


class ApiValidationMessageTests(SimpleTestCase):
    """Validation errors should reach mobile clients as one readable sentence."""

    def test_readable_message_prefers_first_text(self):
        details = {"start_date": ["این فیلد لازم است."], "user": ["invalid"]}
        self.assertEqual(_readable_validation_message(details), "این فیلد لازم است.")

    def test_readable_message_handles_empty_payloads(self):
        self.assertEqual(_readable_validation_message(None), "")
        self.assertEqual(_readable_validation_message({"field": []}), "")

    def test_api_endpoint_returns_plain_message_not_a_dict(self):
        @api_endpoint
        def view(request):
            raise ValidationError({"start_date": ["این فیلد لازم است."]})

        response = view(RequestFactory().post("/"))
        payload = json.loads(response.content)
        message = payload["error"]["message"]
        self.assertEqual(response.status_code, 400)
        self.assertEqual(message, "این فیلد لازم است.")
        self.assertNotIn("{", message)
