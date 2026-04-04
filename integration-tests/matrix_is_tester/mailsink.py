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

import asyncore
import atexit
import smtpd
from multiprocessing import Process, Queue

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


class MailSinkSmtpServer(smtpd.SMTPServer):
    def __init__(self, localaddr, remoteaddr, q):
        smtpd.SMTPServer.__init__(self, localaddr, remoteaddr)
        self.queue = q

    def process_message(self, peer, mailfrom, rctpto, data, **kwargs):
        self.queue.put(
            {"peer": peer, "mailfrom": mailfrom, "rctpto": rctpto, "data": data}
        )


def run_mail_sink(q):
    MailSinkSmtpServer(("127.0.0.1", 9925), None, q)
    asyncore.loop()


class MailSink(object):
    def launch(self):
        self.queue = Queue()
        self.process = Process(target=run_mail_sink, args=(self.queue,))
        self.process.start()

    def get_mail(self):
        return self.queue.get(timeout=0.5)

    def tearDown(self):
        self.process.terminate()


if __name__ == "__main__":
    ms = MailSink()
    ms.launch()
    print("%r" % (ms.get_mail(),))
    ms.tearDown()
