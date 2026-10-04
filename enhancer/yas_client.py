"""Local persistent yas reader. No agent, LLM, browser or remote OCR service."""
import atexit
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
from threading import Thread, Lock
import uuid

ROOT = Path(__file__).resolve().parents[1]


def runtime_environment():
    env = dict(os.environ)
    if not env.get("ORT_DYLIB_PATH"):
        bundled=ROOT/'bin/onnxruntime.dll'
        if bundled.exists():env['ORT_DYLIB_PATH']=str(bundled)
        else:
            import onnxruntime
            env["ORT_DYLIB_PATH"] = str(Path(onnxruntime.__file__).parent / "capi/onnxruntime.dll")
    return env


class YasClient:
    def __init__(self):
        self.messages = Queue()
        self.lock = Lock()
        directory = ROOT / "runtime/yas-service"
        directory.mkdir(parents=True, exist_ok=True)
        self.errors = (directory / "stderr.log").open("ab")
        self.process = subprocess.Popen(
            [str(ROOT / "vendor/yas/target/release/yas_readonly.exe"), "--serve"],
            cwd=directory, env=runtime_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.errors, text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        Thread(target=self._read, daemon=True).start()
        atexit.register(self.close)

    def _read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and "id" in message:
                self.messages.put(message)
        self.messages.put(None)

    def call(self, kind, directory, layout=None):
        with self.lock:
            request = {"id": uuid.uuid4().hex, "kind": kind, "output_dir": str(directory)}
            if layout is not None:
                request["layout"] = str(layout)
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            try:
                reply = self.messages.get(timeout=20)
            except Empty as exc:
                raise RuntimeError("yas observation timed out") from exc
            if reply is None or reply["id"] != request["id"]:
                raise RuntimeError("yas reader stopped or returned an unexpected response")
            if not reply["ok"]:
                raise RuntimeError(reply["error"])
            return reply["data"]

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"kind":"shutdown"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=2)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=2)
        if not self.errors.closed:
            self.errors.close()
