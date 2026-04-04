# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import atexit
from multiprocessing import Process, Queue

from aiosmtpd.controller import Controller

shared_instance = None


def get_shared_mailsink():
    global shared_instance
    if shared_instance is None:
        shared_instance = MailSink()
        shared_instance.launch()
        atexit.register(destroy_shared)
    return shared_instance


def destroy_shared():
    global shared_instance
    shared_instance.tearDown()


class _MailSinkHandler:
    def __init__(self, queue):
        self.queue = queue

    async def handle_DATA(self, server, session, envelope):
        self.queue.put(
            {
                "peer": session.peer,
                "mailfrom": envelope.mail_from,
                "rctpto": envelope.rcpt_tos,
                "data": envelope.content.decode("utf-8", errors="replace"),
            }
        )
        return "250 OK"


def _run_mail_sink(q):
    handler = _MailSinkHandler(q)
    controller = Controller(handler, hostname="127.0.0.1", port=9925)
    controller.start()
    # Block forever (the controller runs in a thread)
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


class MailSink:
    def launch(self):
        self.queue = Queue()
        self.process = Process(target=_run_mail_sink, args=(self.queue,))
        self.process.start()

    def get_mail(self):
        return self.queue.get(timeout=2.0)

    def tearDown(self):
        self.process.terminate()


if __name__ == "__main__":
    ms = MailSink()
    ms.launch()
    print(f"{ms.get_mail()!r}")
    ms.tearDown()
