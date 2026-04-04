# -*- coding: utf-8 -*-

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

import atexit
import threading
from multiprocessing import Event, Process, Queue

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


class MailSinkHandler:
    def __init__(self, q):
        self.queue = q

    async def handle_DATA(self, server, session, envelope):
        self.queue.put(
            {
                "peer": session.peer,
                "mailfrom": envelope.mail_from,
                "rctpto": envelope.rcpt_tos,
                "data": envelope.content,
            }
        )
        return "250 OK"


def run_mail_sink(q, ready):
    # Controller.start() returns once the server accepts connections, and the
    # server runs in a thread of its own.
    Controller(MailSinkHandler(q), hostname="127.0.0.1", port=9925).start()
    ready.set()
    threading.Event().wait()


class MailSink(object):
    def launch(self):
        self.queue = Queue()
        ready = Event()
        self.process = Process(target=run_mail_sink, args=(self.queue, ready))
        self.process.start()
        if not ready.wait(timeout=30):
            self.process.terminate()
            raise RuntimeError("The mail sink did not start listening")

    def get_mail(self):
        return self.queue.get(timeout=0.5)

    def tearDown(self):
        self.process.terminate()


if __name__ == "__main__":
    ms = MailSink()
    ms.launch()
    print("%r" % (ms.get_mail(),))
    ms.tearDown()
