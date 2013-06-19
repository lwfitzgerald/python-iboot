#!/usr/bin/env python

import logging
import socket
import struct

HELLO_STR = 'hello-000'

HEADER_STRUCT = struct.Struct('<B21s21sBBH')

COMMAND_MAP = {
    'NULL': 0,
    'SET': 1,
    'GET': 2,
    'IO': 3,
    'KEEPALIVE': 4,
    'RSS': 5,
    'RCU': 6
}

SOCKET_TIMEOUT = 10


class DXPCommand(object):
    COMMAND = None
    DESCRIPTOR_MAP = None
    DESCRIPTOR = None
    PAYLOAD_STRUCT = None

    def __init__(self, interface):
        self.interface = interface

    def _build_header(self):
        if not self.COMMAND:
            raise Exception("'COMMAND' type not specified for class")

        if not self.DESCRIPTOR_MAP:
            raise Exception("'DESCRIPTOR_MAP' not specified for class")

        if not self.DESCRIPTOR:
            raise Exception("'DESCRIPTOR' type not specified for class")

        return HEADER_STRUCT.pack(COMMAND_MAP[self.COMMAND],
                                  self.interface.username,
                                  self.interface.password,
                                  self.DESCRIPTOR_MAP[self.DESCRIPTOR],
                                  0,
                                  self.interface.get_seq_num())

    def _build_payload(self, *pack_args):
        if not self.PAYLOAD_STRUCT:
            raise Exception("'PAYLOAD_STRUCT' not specified for class")

        return self.PAYLOAD_STRUCT.pack(*pack_args)

    def _get_response(self, socket):
        """
        Parse the response from the request
        """
        raise Exception('get_response method not implemented')

    def _get_boolean_response(self):
        response = self.interface.socket.recv(1)
        if not response:
            return False

        self.interface.increment_seq_num()
        return self._parse_bool(response)

    def _parse_bool(self, string):
        return not struct.unpack('?', string)[0]

    def do_request(self):
        header = self._build_header()
        payload = self._build_payload()
        request = header + payload
        self.interface.socket.sendall(request)
        return self._get_response()

    def _do_payloadless_request(self):
        request = self._build_header()
        self.interface.socket.sendall(request)
        return self._get_response()


class IOCommand(DXPCommand):
    COMMAND = 'IO'
    DESCRIPTOR_MAP = {
        'NULL': 0,
        'CHANGE_RELAY': 1,
        'CHANGE_RELAYS': 2,
        'GET_RELAY': 3,
        'GET_RELAYS': 4,
        'GET_INPUT': 5,
        'GET_INPUTS': 6,
        'PULSE_RELAY': 7
    }


class RelayCommand(IOCommand):
    STATE_MAP = {
        True: 1,
        False: 0,
        'NO_CHANGE': 2
    }

    def _get_response(self):
        return self._get_boolean_response()


class ChangeRelayCommand(RelayCommand):
    DESCRIPTOR = 'CHANGE_RELAY'
    PAYLOAD_STRUCT = struct.Struct('<BB')

    def __init__(self, interface, relay, state):
        super(ChangeRelayCommand, self).__init__(interface)
        self.relay = relay
        self.state = state

    def _build_payload(self):
        return super(ChangeRelayCommand, self)._build_payload(
            self.relay, self.STATE_MAP[self.state])


class ChangeRelaysCommand(RelayCommand):
    DESCRIPTOR = 'CHANGE_RELAYS'
    PAYLOAD_STRUCT = struct.Struct('<' + ('B' * 32))  # 32 unsigned chars

    def __init__(self, interface, relay_state_dict):
        super(ChangeRelaysCommand, self).__init__(interface)
        self.relay_state_dict = relay_state_dict

    def _build_payload(self):
        state_list = []

        self.interface.logger.debug(self.relay_state_dict)

        for relay in xrange(32):
            if (relay + 1) not in self.relay_state_dict:
                state_list.append(self.STATE_MAP['NO_CHANGE'])
            else:
                state_list.append(
                    self.STATE_MAP[self.relay_state_dict[relay + 1]])

        self.interface.logger.debug(state_list)

        return super(ChangeRelaysCommand, self)._build_payload(*state_list)


class GetRelaysRequest(IOCommand):
    DESCRIPTOR = 'GET_RELAYS'

    def do_request(self):
        return self._do_payloadless_request()

    def _get_response(self):
        response = self.interface.socket.recv(self.interface.num_relays)
        if not response:
            return None

        self.interface.increment_seq_num()
        return [True if ord(relay_status) == 1 else False
                for relay_status in response]


class PulseRelayRequest(RelayCommand):
    DESCRIPTOR = 'PULSE_RELAY'
    PAYLOAD_STRUCT = struct.Struct('<BBH')

    def __init__(self, interface, relay, state, width):
        super(PulseRelayRequest, self).__init__(interface)
        self.relay = relay
        self.state = state
        self.width = width

    def _build_payload(self):
        return super(PulseRelayRequest, self)._build_payload(
            self.relay, self.STATE_MAP[self.state], self.width)


class iBootInterface(object):
    def __init__(self, ip, username, password, port=9100, num_relays=3):
        self.ip = ip
        self.username = username
        self.password = password
        self.port = port
        self.num_relays = num_relays
        self.seq_num = None
        self.socket = None
        logging.basicConfig()
        self.logger = logging.getLogger('iBootInterface')
        self.logger.setLevel(logging.DEBUG)

    def get_seq_num(self):
        seq_num = self.seq_num
        self.seq_num += 1
        return seq_num

    def increment_seq_num(self):
        self.seq_num += 1

    def connect(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(SOCKET_TIMEOUT)

        try:
            self.socket.connect((self.ip, self.port))
        except socket.error:
            self.logger.error('Socket failed to connect')
            return False

        try:
            self.socket.sendall(HELLO_STR)
            return self._get_initial_seq_num()
        except socket.error:
            self.logger.error('Socket error')
            return False

    def _get_initial_seq_num(self):
        response = self.socket.recv(2)

        if not response:
            return False

        self.seq_num = struct.unpack('H', response)[0] + 1
        return True

    def disconnect(self):
        try:
            self.socket.close()
        except:
            pass

    def switch(self, relay, on):
        """Switch the given relay on or off"""
        self.connect()
        request = ChangeRelayCommand(self, relay, on)

        try:
            return request.do_request()
        except socket.error:
            return False
        finally:
            self.disconnect()

    def switch_multiple(self, relay_state_dict):
        """
        Change the state of multiple relays at once

        State dictionary should be of the form:
            {1: True}
        where the key is the relay and the value is the new state
        """
        self.connect()

        for relay, new_state in relay_state_dict.items():
            request = ChangeRelayCommand(relay, new_state)

            try:
                result = request.do_request()

                if not result:
                    return False
            except socket.error:
                self.disconnect()
                return False

        self.disconnect()
        return True

    def get_relays(self):
        self.connect()
        request = GetRelaysRequest(self)

        try:
            return request.do_request()
        except socket.error:
            return False
        finally:
            self.disconnect()

    def pulse_relay(self, relay, on, length):
        self.connect()
        request = PulseRelayRequest(self, relay, on, length)

        try:
            return request.do_request()
        except socket.error:
            return False
        finally:
            self.disconnect()

if __name__ == '__main__':
    interface = iBootInterface('192.168.0.105', 'admin', 'admin')

