import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from django.conf import settings
from django.test import TestCase, override_settings
from livekit.api import (
    AudioCodec,
    EncodedFileType,
    EgressInfo,
    EgressStatus,
    FileInfo,
    VideoCodec,
)

from apps.meetings.models import Meeting, Recording
from apps.meetings.services.egress import (
    _STOP_READY_TIMEOUT_SECONDS,
    _egress_failure_message,
    _mp4_has_moov,
    _wait_for_local_mp4,
    build_start_request,
    record_egress_info,
    recording_object_key_ok,
)


@override_settings(
    S3_BUCKET="recordings",
    S3_KEY_ID="minio",
    S3_KEY_SECRET="minio12345",
    S3_ENDPOINT="http://minio:9000",
    S3_REGION="us-east-1",
    RECORDING_LOCAL_DIR="",
)
class RecordingRequestTests(TestCase):
    def test_egress_worker_can_accept_a_room_on_a_small_host(self):
        root = Path(__file__).resolve().parents[4]
        config = root.joinpath("infra", "egress.yaml").read_text()
        self.assertIn("api_key: meetingskey", config)
        self.assertIn("address: redis:6379", config)
        self.assertIn("ws_url: ws://livekit:7880", config)
        self.assertIn("room_composite_cpu_cost: 0.5", config)
        self.assertIn("web_cpu_cost: 0.5", config)
        self.assertIn("disable-features:", config)
        self.assertNotIn("AudioServiceOutOfProcess", config)
        entrypoint = root.joinpath("infra", "egress-entrypoint.sh").read_text()
        # The image entrypoint deletes /var/lib/pulse as the egress user and
        # exits on failure. Root has to remove that directory first.
        cleared = entrypoint.index("rm -rf \\\n  /var/run/pulse")
        dropped = entrypoint.index("runuser -u egress")
        self.assertLess(cleared, dropped)
        self.assertNotIn("mkdir -p \\\n  /var/lib/pulse", entrypoint)
        self.assertIn("/var/lib/pulse /home/egress/.config/pulse", entrypoint)
        self.assertIn("|| true", entrypoint)
        # Chrome blocks on a session bus that has no daemon, then Egress
        # reports "websocket url timeout reached" before DevTools is ready.
        session = entrypoint.index("dbus-daemon --session")
        self.assertLess(session, entrypoint.rindex("exec runuser -u egress"))
        self.assertIn("unix:path=/home/egress/.cache/xdgr/bus", entrypoint)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}"', entrypoint)
        # The distro wrapper hides the DevTools line. Launch the binary
        # itself, and open Chrome once before the service accepts a job so
        # the first recording is not a cold start.
        self.assertIn('exec -a google-chrome /opt/google/chrome/chrome "$@"', entrypoint)
        self.assertNotIn("stdbuf -oL", entrypoint)
        self.assertLess(entrypoint.index("/tmp/chrome-warm"), entrypoint.index("exec /tini -- egress"))
        self.assertIn("TINI_SUBREAPER=1", entrypoint)
        self.assertNotIn("no-dbus-session", entrypoint)
        self.assertNotIn("no-dbus-system", entrypoint)
        # A few-millisecond Pulse buffer underruns on Docker Desktop, so the
        # captured audio is choppy and the MP4 timestamps jump.
        self.assertIn("PULSE_LATENCY_MSEC=80", entrypoint)
        self.assertIn("default-fragment-size-msec = 25", entrypoint)
        next_config = root.joinpath("frontend", "next.config.js").read_text()
        self.assertIn("allowedDevOrigins: ['host.docker.internal']", next_config)

    def test_compose_can_pull_object_storage(self):
        compose = Path(__file__).resolve().parents[4].joinpath("docker-compose.yml").read_text()
        self.assertNotIn("quay.io/minio", compose)
        self.assertIn("pgsty/silo:RELEASE.2026-09-03T13-18-01Z", compose)

    def test_missing_worker_is_reported_as_egress_not_accepting(self):
        message = _egress_failure_message("twirp error unknown: no response from servers")
        self.assertIn("same Redis", message)

    def test_recording_template_starts_without_a_camera_or_microphone(self):
        response = self.client.get("/api/record/template")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("START_RECORDING", body)
        self.assertIn("END_RECORDING", body)
        self.assertIn("framesDecoded", body)
        self.assertIn("camera-off", body)
        self.assertIn("Do not wait for a camera or a microphone", body)
        self.assertNotIn("setTimeout(startRecording, 2500)", body)
        self.assertIn("START_SIGNAL_DEADLINE_MS", body)
        self.assertIn("setTimeout(beginAfterPaint, START_SIGNAL_DEADLINE_MS)", body)
        self.assertIn("createBuffer", body)
        self.assertIn("room.off(LivekitClient.RoomEvent.Disconnected, EgressHelper.endRecording)", body)
        self.assertIn("}, 15000);", body)

    def test_recorder_page_prints_the_start_signal_while_the_room_connects(self):
        root = Path(__file__).resolve().parents[4]
        ready = root.joinpath("frontend", "lib", "recordingReady.ts").read_text()
        self.assertIn("START_SIGNAL_DEADLINE_MS = 12000", ready)
        room = root.joinpath("frontend", "lib", "EgressRoom.tsx").read_text()
        self.assertIn("recordingDeadlineReached", room)
        self.assertIn("EgressHelper.startRecording()", room)
        self.assertIn("createBuffer", room)
        self.assertIn("room.off(RoomEvent.Disconnected, EgressHelper.endRecording)", room)
        self.assertIn("}, 15000);", room)
        button = root.joinpath("frontend", "lib", "RecordButton.tsx").read_text()
        self.assertIn("warmRecorderPage()", button)
        self.assertGreaterEqual(_STOP_READY_TIMEOUT_SECONDS, 75)
        message = _egress_failure_message("Start signal not received")
        self.assertIn("did not finish opening", message)

    def test_start_request_records_one_mp4(self):
        request = build_start_request("demo-room")

        self.assertEqual(request.room_name, "demo-room")
        self.assertEqual(request.template.layout, "speaker")
        self.assertEqual(request.template.custom_base_url, settings.RECORDING_TEMPLATE_URL)
        self.assertFalse(request.template.audio_only)
        self.assertFalse(request.HasField("preset"))
        options = request.advanced
        self.assertEqual(options.width, 960)
        self.assertEqual(options.height, 540)
        self.assertEqual(options.framerate, 15)
        self.assertEqual(options.audio_codec, AudioCodec.AAC)
        self.assertEqual(options.audio_frequency, 48000)
        self.assertEqual(options.video_codec, VideoCodec.H264_BASELINE)
        self.assertEqual(len(request.outputs), 1)
        output = request.outputs[0].file
        self.assertEqual(output.file_type, EncodedFileType.MP4)
        self.assertEqual(output.filepath, "{room_name}/{time}.mp4")
        self.assertTrue(request.storage.s3.force_path_style)
        self.assertEqual(request.storage.s3.bucket, "recordings")
        self.assertEqual(request.storage.s3.endpoint, "http://minio:9000")

    @override_settings(RECORDING_LOCAL_DIR=r"C:\recordings")
    def test_local_dir_writes_an_mp4_on_disk(self):
        request = build_start_request("demo-room")

        self.assertEqual(request.outputs[0].file.filepath, "/out/{room_name}/{time}.mp4")
        self.assertFalse(request.HasField("storage"))

    def test_an_mp4_is_playable_only_after_its_index_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "bare.mp4"
            bare.write_bytes(b"\x00" * 64)
            self.assertFalse(_mp4_has_moov(bare))
            indexed = root / "indexed.mp4"
            indexed.write_bytes(b"\x00" * 32 + b"moov" + b"\x00" * 32)
            self.assertTrue(_mp4_has_moov(indexed))
            # Faststart can leave the index at the end, past the first 256KB.
            tail = root / "tail.mp4"
            tail.write_bytes(b"\x00" * (300 * 1024) + b"moov")
            self.assertTrue(_mp4_has_moov(tail))

            room = root / "demo-room"
            room.mkdir()
            partial = room / "partial.mp4"
            partial.write_bytes(b"\x00" * 128)
            with (
                override_settings(RECORDING_LOCAL_DIR=tmp),
                patch("apps.meetings.services.egress.time.sleep", return_value=None),
                patch("apps.meetings.services.egress._LOCAL_FILE_TIMEOUT_SECONDS", 0.05),
                patch("apps.meetings.services.egress._LOCAL_FILE_POLL_SECONDS", 0.01),
            ):
                self.assertIsNone(_wait_for_local_mp4("demo-room", None))

            finished = room / "finished.mp4"
            finished.write_bytes(b"\x00" * 64 + b"moov" + b"\x00" * 64)
            partial.unlink()
            with (
                override_settings(RECORDING_LOCAL_DIR=tmp),
                patch("apps.meetings.services.egress.time.sleep", return_value=None),
                patch("apps.meetings.services.egress._LOCAL_FILE_TIMEOUT_SECONDS", 0.05),
                patch("apps.meetings.services.egress._LOCAL_FILE_STABLE_SECONDS", 0),
            ):
                self.assertEqual(_wait_for_local_mp4("demo-room", None), finished)
                self.assertIsNone(_wait_for_local_mp4("demo-room", None, expected_size=10_000_000))

    def test_only_this_rooms_mp4_can_be_opened(self):
        self.assertTrue(recording_object_key_ok("demo-room", "demo-room/2026-09-30T120000.mp4"))
        self.assertFalse(recording_object_key_ok("demo-room", "other-room/clip.mp4"))
        self.assertFalse(recording_object_key_ok("demo-room", "demo-room/../secret.mp4"))
        self.assertFalse(recording_object_key_ok("demo-room", "demo-room/notes.txt"))
        self.assertFalse(recording_object_key_ok("demo/room", "demo/room/clip.mp4"))

    def test_file_route_rejects_another_rooms_object(self):
        response = self.client.get(
            "/api/recordings/file",
            {"roomName": "demo-room", "key": "other-room/clip.mp4"},
        )
        self.assertEqual(response.status_code, 404)

    def test_list_requires_a_room(self):
        response = self.client.get("/api/recordings")
        self.assertEqual(response.status_code, 403)

    def test_start_requires_a_host_or_cohost(self):
        created = self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        missing = self.client.post("/api/record/start?roomName=demo-room")
        self.assertEqual(missing.status_code, 403)
        wrong = self.client.post(
            "/api/record/start?roomName=demo-room",
            HTTP_X_HOST_KEY="not-the-host-key",
        )
        self.assertEqual(wrong.status_code, 403)

    @patch("apps.meetings.services.egress._list_active", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._start_egress", new_callable=AsyncMock)
    def test_host_start_stores_the_egress(self, start_egress, list_active):
        list_active.return_value = []
        start_egress.return_value = EgressInfo(
            egress_id="EG_test",
            room_name="demo-room",
            status=EgressStatus.EGRESS_STARTING,
        )
        created = self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        response = self.client.post(
            "/api/record/start?roomName=demo-room",
            HTTP_X_HOST_KEY=created.json()["host_key"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["egressId"], "EG_test")
        self.assertEqual(response.json()["status"], "starting")
        request = start_egress.await_args.args[0]
        self.assertEqual(request.room_name, "demo-room")
        self.assertEqual(request.template.layout, "speaker")
        row = Recording.objects.get(egress_id="EG_test")
        self.assertEqual(row.status, Recording.Status.STARTING)

        again = self.client.post(
            "/api/record/start?roomName=demo-room",
            HTTP_X_HOST_KEY=created.json()["host_key"],
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(start_egress.await_count, 1)

    @patch("apps.meetings.services.egress._stop_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._get_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._list_active", new_callable=AsyncMock)
    def test_stop_of_a_failed_egress_clears_the_recording(self, list_active, get_egress, stop_egress):
        active = EgressInfo(
            egress_id="EG_failed",
            room_name="demo-room",
            status=EgressStatus.EGRESS_ACTIVE,
        )
        list_active.return_value = [active]
        get_egress.return_value = active
        stop_egress.side_effect = RuntimeError(
            "egress with status EGRESS_FAILED cannot be stopped"
        )
        created = self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        Recording.objects.create(
            meeting=Meeting.objects.get(room_name="demo-room"),
            egress_id="EG_failed",
            status=Recording.Status.ACTIVE,
        )
        response = self.client.post(
            "/api/record/stop?roomName=demo-room",
            HTTP_X_HOST_KEY=created.json()["host_key"],
        )
        self.assertEqual(response.status_code, 200)
        row = Recording.objects.get(egress_id="EG_failed")
        self.assertEqual(row.status, Recording.Status.FAILED)
        self.assertIn("failed", response.json()["recordings"][0]["error"])

    @patch("apps.meetings.services.egress._stop_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._get_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._list_active", new_callable=AsyncMock)
    def test_stop_of_a_chrome_startup_failure_returns_the_recording(
        self, list_active, get_egress, stop_egress
    ):
        failed = EgressInfo(
            egress_id="EG_chrome",
            room_name="demo-room",
            status=EgressStatus.EGRESS_FAILED,
            error="page load error: websocket url timeout reached",
        )
        list_active.return_value = [failed]
        get_egress.return_value = failed
        created = self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        Recording.objects.create(
            meeting=Meeting.objects.get(room_name="demo-room"),
            egress_id="EG_chrome",
            status=Recording.Status.STARTING,
        )
        response = self.client.post(
            "/api/record/stop?roomName=demo-room",
            HTTP_X_HOST_KEY=created.json()["host_key"],
        )
        self.assertEqual(response.status_code, 200)
        stop_egress.assert_not_awaited()
        row = Recording.objects.get(egress_id="EG_chrome")
        self.assertEqual(row.status, Recording.Status.FAILED)
        self.assertIn("port 3000", row.error)
        self.assertIn("port 3000", response.json()["recordings"][0]["error"])

    @patch("apps.meetings.services.egress.time.sleep")
    @patch("apps.meetings.services.egress._stop_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._get_egress", new_callable=AsyncMock)
    @patch("apps.meetings.services.egress._list_active", new_callable=AsyncMock)
    def test_stop_waits_while_chrome_is_still_starting(self, list_active, get_egress, stop_egress, _sleep):
        list_active.return_value = [
            EgressInfo(
                egress_id="EG_starting",
                room_name="demo-room",
                status=EgressStatus.EGRESS_STARTING,
            )
        ]
        get_egress.side_effect = [
            EgressInfo(
                egress_id="EG_starting",
                room_name="demo-room",
                status=EgressStatus.EGRESS_STARTING,
            ),
            EgressInfo(
                egress_id="EG_starting",
                room_name="demo-room",
                status=EgressStatus.EGRESS_ACTIVE,
            ),
        ]
        stop_egress.return_value = EgressInfo(
            egress_id="EG_starting",
            room_name="demo-room",
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[FileInfo(filename="/out/demo-room/2026-10-03T010000.mp4", size=32)],
        )
        created = self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        Recording.objects.create(
            meeting=Meeting.objects.get(room_name="demo-room"),
            egress_id="EG_starting",
            status=Recording.Status.STARTING,
        )
        response = self.client.post(
            "/api/record/stop?roomName=demo-room",
            HTTP_X_HOST_KEY=created.json()["host_key"],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(stop_egress.await_count, 1)
        self.assertEqual(get_egress.await_count, 2)
        row = Recording.objects.get(egress_id="EG_starting")
        self.assertEqual(row.status, Recording.Status.COMPLETE)
        self.assertEqual(row.object_key, "demo-room/2026-10-03T010000.mp4")

    def test_egress_result_stores_the_room_file(self):
        Meeting.objects.create(room_name="demo-room")
        record_egress_info(
            EgressInfo(
                egress_id="EG_done",
                room_name="demo-room",
                status=EgressStatus.EGRESS_COMPLETE,
                file_results=[FileInfo(filename="/out/demo-room/2026-09-30T120000.mp4", size=10)],
            )
        )
        row = Recording.objects.get(egress_id="EG_done")
        self.assertEqual(row.object_key, "demo-room/2026-09-30T120000.mp4")
        self.assertEqual(row.status, Recording.Status.COMPLETE)
        self.assertEqual(row.size_bytes, 10)

        record_egress_info(
            EgressInfo(
                egress_id="EG_done",
                room_name="demo-room",
                status=EgressStatus.EGRESS_COMPLETE,
                file_results=[FileInfo(filename="other-room/clip.mp4", size=4)],
            )
        )
        row.refresh_from_db()
        self.assertEqual(row.object_key, "demo-room/2026-09-30T120000.mp4")

    def test_webhook_rejects_a_missing_signature(self):
        response = self.client.post(
            "/api/livekit/webhook",
            data=b"{}",
            content_type="application/webhook+json",
        )
        self.assertEqual(response.status_code, 401)

    def test_list_requires_someone_in_the_meeting(self):
        self.client.post(
            "/api/meetings",
            data={"room_name": "demo-room"},
            content_type="application/json",
        )
        response = self.client.get("/api/recordings", {"roomName": "demo-room"})
        self.assertEqual(response.status_code, 403)
