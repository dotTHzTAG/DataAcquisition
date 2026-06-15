# -*- coding: utf-8 -*-
from pywebchannel.asynchronous import QWebChannel
from pywebchannel.qwebchannel import QObject, Signal
import websockets

import websockets.client
import nest_asyncio
nest_asyncio.apply()

import enum
import asyncio
import json
import numpy
import base64

class QWebChannelWebSocketProtocol(websockets.client.WebSocketClientProtocol):
    """ Bridges WebSocketClientProtocol and QWebChannel.

    Continuously reads messages in a task and invokes QWebChannel.message_received()
    for each. Calls QWebChannel.connection_open() when connected.
    Also patches QWebChannel.send() to run the websocket's send() in a task"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _task_send(self, data):
        if not isinstance(data, str):
            data = json.dumps(data)
        self.loop.create_task(self.send(data))

    def connection_open(self):
        super().connection_open()

        self.webchannel = QWebChannel()
        self.webchannel.send = self._task_send
        self.webchannel.connection_made(self)

        self.loop.create_task(self.read_msgs())

    async def read_msgs(self):
        async for msg in self:
            self.webchannel.message_received(msg)

class PulseFlags(enum.Enum):
    NoPulseFlags = 0x0
    FlippedPulse = 0x1
    Trigger1 = 0x10
    Trigger2 = 0x20
    Trigger3 = 0x40
    Trigger4 = 0x80
    TriggerMask = 0xF0

class ScanControlStatus(enum.IntEnum):
    Uninitialized = 0
    Initializing = 1
    Idle = 2
    Acquiring = 3
    Busy = 4
    Error = 5

class ScanControlClient(QObject):

    def __init__(self, loop=None):

        self.loop = loop
        if self.loop is None:
            self.loop = asyncio.get_event_loop()

    def _decodeData(self, data):
        return numpy.frombuffer(base64.b64decode(data), dtype=numpy.float64)

    def _decodeAmpArray(self, data):

        encAmpData = data['amplitude']
        decAmpData = []
        for set in encAmpData:
            decAmpData.append(self._decodeData(set))

        data['amplitude'] = decAmpData
        return data

    def _onDisplayPulseReady(self,data):
        data = self._decodeAmpArray(data)
        if self.scancontrol.timeAxis is not None:
            decTimeAxis = self._decodeData(self.scancontrol.timeAxis)
            if len(data['amplitude'][0]) == len(decTimeAxis):
                data['timeaxis']=decTimeAxis
                self.scancontrol._invokeSignalCallbacks(-2, [data])

    def _onPulseReady(self,data):

        data = self._decodeAmpArray(data)
        if self.scancontrol.timeAxis is not None:
            decTimeAxis = self._decodeData(self.scancontrol.timeAxis)
            if len(data['amplitude'][0]) == len(decTimeAxis):
                data['timeaxis']=decTimeAxis
                self.scancontrol._invokeSignalCallbacks(-1, [data])

    async def _establish_connection(self, webchannel):
        # Wait for initialized
        await webchannel
        print("Connected.")
        self.scancontrol = webchannel.objects["scancontrol"]
        #overwrite
        self.scancontrol.displayPulseReadyEncoded = self.scancontrol.displayPulseReady
        self.scancontrol.displayPulseReady = Signal(self.scancontrol, -2,
                                                    'decodedDisplayPulse',True)
        self.scancontrol.displayPulseReadyEncoded.connect(self._onDisplayPulseReady)

        self.scancontrol.pulseReadyEncoded = self.scancontrol.pulseReady
        self.scancontrol.pulseReady = Signal(self.scancontrol, -1,
                                                    'decodedPulse', True)
        self.scancontrol.pulseReadyEncoded.connect(self._onPulseReady)

    def exception_handler(self, loop, context):
        print(context)
        print('Exception handler called')

    def run(self, target_function):
        self.loop.run_until_complete(target_function)

    def connect(self, host="localhost", port = "8002"):
        self.host = host
        self.port = port
        url = "ws://" + self.host + ":" + self.port


        proto = self.loop.run_until_complete(websockets.client.connect(url, create_protocol=QWebChannelWebSocketProtocol))
        self.loop.run_until_complete(self._establish_connection(proto.webchannel))

