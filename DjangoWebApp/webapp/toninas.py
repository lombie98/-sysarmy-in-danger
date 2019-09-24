import json
import time
import asyncio
from threading import Thread, Event

from random import randint

from .arduino_controller import ArduinoController


class ToninasGame:
    def __init__(self, conn_qty=8, slot_qty=32, sender_blacklist=False,
                 receiver_blacklist=False, test=False, timeout=10,
                 *args, **kwargs):
        self.test = test
        self.timeout = timeout
        self.conn_qty = conn_qty
        self.slot_qty = max([slot_qty, conn_qty])
        self.sender_blacklist = self._validate_blacklist(sender_blacklist)
        self.receiver_blacklist = self._validate_blacklist(receiver_blacklist)

        if len(self.sender_blacklist) + conn_qty > slot_qty:
            print("Sender Conn + Blacklist Overflow")
            self.sender_blacklist = []

        if len(self.receiver_blacklist) + conn_qty > slot_qty:
            print("Receiver Conn + Blacklist Overflow")
            self.receiver_blacklist = []

        self.ended = Event()
        self._compute_retry = 0

    def _validate_blacklist(self, bl):
        if not isinstance(bl, list):
            bl = []
        try:
            rbl = [int(x) for x in bl]
        except Exception:
            print("Blacklist %s is not a valid format" % bl)
            rbl = []
        return rbl

    def get_valid_index(self, pos, limit, bl):
        index = randint(0, limit - 1)
        if index not in pos and index not in bl:
            return index
        else:
            return self.get_valid_index(pos, limit, bl)

    def gen_pos_array(self, qty, limit, bl):
        pos = []
        for i in range(qty):
            index = self.get_valid_index(pos, limit, bl)
            pos.append(index)
        return pos

    def start(self):
        self.sender_pos = self.gen_pos_array(
            self.conn_qty, self.slot_qty, self.sender_blacklist
        )
        self.receiver_pos = self.gen_pos_array(
            self.conn_qty, self.slot_qty, self.receiver_blacklist
        )
        self.sender = ArduinoController(
            'sender', self.conn_qty, self.sender_pos
        )
        self.receiver = ArduinoController(
            'receiver', self.conn_qty, self.receiver_pos
        )
        self.sender.start()
        self.receiver.start()
        self.sender.health_check()
        self.receiver.health_check()
        self.conn_state = [False] * self.conn_qty

    def stop(self):
        self.ended.set()

    def run(self, socket):
        th = Thread(target=self._main, args=[socket])
        th.start()

    def _main(self, socket):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run(socket))
        loop.close()

    async def _run(self, socket):
        start_time = now = time.time()
        interval = 0
        last_interval = 0
        last_hc_interval = 0
        while interval < self.timeout and not self.ended.isSet():
            if (interval - last_hc_interval) > 0.5:
                self.sender.health_check()
                self.receiver.health_check()
                last_hc_interval = interval
                await socket.send(json.dumps({
                    'signal': 'health_check',
                    'value': {
                        'sender': self.sender.hc,
                        'receiver': self.receiver.hc,
                    },
                }))
            if (interval - last_interval) > 2:
                last_interval = interval
                print('Playing game for %.4f seconds' % (interval))
            await asyncio.sleep(.001)
            self.compute_state()
            await socket.send(json.dumps({
                'signal': 'status',
                'value': self.conn_state,
            }))
            now = time.time()
            interval = now - start_time
            if self.conn_state and isinstance(self.conn_state, list) and all(self.conn_state):
                await socket.send(json.dumps({
                    'signal': 'win',
                }))
                self.restart()
                break
        else:
            await socket.send(json.dumps({
                'signal': 'timeout',
            }))
            self.restart()
        return False

    def restart(self):
        self.sender.send("$restart:1;")
        self.receiver.send("$restart:1;")

    def compute_state(self):
        if self.test:
            self._compute_retry += 1
            if self._compute_retry > 1:
                self.conn_state = [randint(0, 1) for _ in range(self.conn_qty)]
                self._compute_retry = 0
        else:
            try:
                received_string = self.receiver.receive("$status:1;")
                if received_string == 'Err':
                    print('Error')
                else:
                    self.conn_state = [int(x) for x in received_string]
            except Exception as e:
                print("Could not parse response from Arduino. Error was: %s" % str(e))
                self.receiver.start()
                self.conn_state = [0] * self.conn_qty

    @property
    def config(self):
        conf = {
            'sender_pos': self.sender_pos,
            'receiver_pos': self.receiver_pos,
            'timeout': self.timeout,
            'conn_qty': self.conn_qty,
            'slot_qty': self.slot_qty,
            'sender_blacklist': self.sender_blacklist,
            'receiver_blacklist': self.receiver_blacklist,
            'test': self.test,
        }
        return conf
