# -*- encoding: utf-8 -*-

import json
from urllib import urlencode

from twisted.web import http
from twisted.internet.defer import inlineCallbacks, DeferredQueue

from vumi.tests.utils import MockHttpServer
from vumi.utils import http_request_full
from vumi.transports.apposit import AppositTransport
from vumi.transports.tests.utils import TransportTestCase


class TestAppositTransport(TransportTestCase):
    transport_name = 'test_apposit_transport'
    transport_class = AppositTransport

    @inlineCallbacks
    def setUp(self):
        super(TestAppositTransport, self).setUp()

        self.mock_server = MockHttpServer(self.handle_inbound_request)
        self.outbound_requests = DeferredQueue()
        self.mock_server_response = ''
        self.mock_server_response_code = http.OK
        yield self.mock_server.start()

        config = {
            'transport_name': self.transport_name,
            'web_path': 'api/v1/apposit/sms',
            'web_port': 0,
            'credentials': {
                '8123': {
                    'username': 'root',
                    'password': 'toor',
                    'service_id': 'service-id-1',
                },
                '8124': {
                    'username': 'admin',
                    'password': 'nimda',
                    'service_id': 'service-id-2',
                }
            },
            'outbound_url': self.mock_server.url,
        }
        self.transport = yield self.get_transport(config)
        self.transport_url = self.transport.get_transport_url()
        self.web_path = config['web_path']

    @inlineCallbacks
    def tearDown(self):
        yield self.mock_server.stop()
        yield super(TestAppositTransport, self).tearDown()

    def send_full_inbound_request(self, **params):
        return http_request_full(
            '%s%s?%s' % (self.transport_url, self.web_path, urlencode(params)),
            '', method='POST')

    def send_inbound_request(self, **kwargs):
        params = {
            'from': '251911223344',
            'to': '8123',
            'channel': 'SMS',
            'content': 'never odd or even',
            'isTest': 'true',
        }
        params.update(kwargs)
        return self.send_full_inbound_request(**params)

    def handle_inbound_request(self, request):
        self.outbound_requests.put(request)
        request.setResponseCode(self.mock_server_response_code)
        return self.mock_server_response

    def set_mock_server_response(self, code=http.OK, body=''):
        self.mock_server_response_code = code
        self.mock_server_response = body

    def mk_outbound_message(self, **kwargs):
        args = {
            'from_addr': '8123',
            'to_addr': '251911223344',
            'content': 'so many dynamos',
            'transport_type': 'sms',
        }
        args.update(kwargs)
        return self.mkmsg_out(**args)

    def assert_outbound_request(self, request, **kwargs):
        expected_args = {
            'username': ['root'],
            'password': ['toor'],
            'serviceId': ['service-id-1'],
            'fromAddress': ['8123'],
            'toAddress': ['251911223344'],
            'content': ['so many dynamos'],
            'channel': ['SMS'],
        }
        expected_args.update(kwargs)

        self.assertEqual(request.path, '/')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(expected_args, request.args)

    def assert_message_fields(self, msg, **kwargs):
        fields = {
            'transport_name': self.transport_name,
            'transport_type': 'sms',
            'from_addr': '251911223344',
            'to_addr': '8123',
            'content': 'so many dynamos',
            'provider': 'apposit',
            'transport_metadata': {'apposit': {'isTest': 'true'}},
        }
        fields.update(kwargs)

        for field_name, expected_value in fields.iteritems():
            self.assertEqual(msg[field_name], expected_value)

    def assert_ack(self, ack, msg):
        self.assertEqual(ack.payload['event_type'], 'ack')
        self.assertEqual(ack.payload['user_message_id'], msg['message_id'])
        self.assertEqual(ack.payload['sent_message_id'], msg['message_id'])

    def assert_nack(self, nack, msg, reason):
        self.assertEqual(nack.payload['event_type'], 'nack')
        self.assertEqual(nack.payload['user_message_id'], msg['message_id'])
        self.assertEqual(nack.payload['nack_reason'], reason)

    @inlineCallbacks
    def test_inbound(self):
        response = yield self.send_inbound_request(**{
            'from': '251911223344',
            'to': '8123',
            'content': 'so many dynamos',
            'channel': 'SMS',
            'isTest': 'true',
        })

        [msg] = self.get_dispatched_messages()
        self.assert_message_fields(msg,
            transport_name=self.transport_name,
            transport_type='sms',
            from_addr='251911223344',
            to_addr='8123',
            content='so many dynamos',
            provider='apposit',
            transport_metadata={'apposit': {'isTest': 'true'}})

        self.assertEqual(response.code, http.OK)
        self.assertEqual(json.loads(response.delivered_body),
                         {'message_id': msg['message_id']})

    @inlineCallbacks
    def test_outbound(self):
        msg = self.mk_outbound_message(
            from_addr='8123',
            to_addr='251911223344',
            content='racecar')
        yield self.dispatch(msg)

        request = yield self.outbound_requests.get()
        self.assert_outbound_request(request, **{
            'username': ['root'],
            'password': ['toor'],
            'serviceId': ['service-id-1'],
            'content': ['racecar'],
            'fromAddress': ['8123'],
            'toAddress': ['251911223344'],
            'channel': ['SMS']
        })

        [ack] = yield self.wait_for_dispatched_events(1)
        self.assert_ack(ack, msg)

    @inlineCallbacks
    def test_inbound_requests_for_non_ascii_content(self):
        response = yield self.send_inbound_request(content='Hliðskjálf')
        [msg] = self.get_dispatched_messages()
        self.assert_message_fields(msg, content=u'Hliðskjálf')

        self.assertEqual(response.code, http.OK)
        self.assertEqual(json.loads(response.delivered_body),
                         {'message_id': msg['message_id']})

    @inlineCallbacks
    def test_inbound_requests_for_unsupported_channel(self):
        response = yield self.send_full_inbound_request(**{
            'from': '251911223344',
            'to': '8123',
            'channel': 'steven',
            'content': 'never odd or even',
            'isTest': 'false',
        })

        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.delivered_body),
                         {'unsupported_channel': 'steven'})

    @inlineCallbacks
    def test_inbound_requests_for_unexpected_param(self):
        response = yield self.send_full_inbound_request(**{
            'from': '251911223344',
            'to': '8123',
            'channel': 'SMS',
            'steven': 'its a trap',
            'content': 'never odd or even',
            'isTest': 'false',
        })

        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.delivered_body),
                         {'unexpected_parameter': ['steven']})

    @inlineCallbacks
    def test_inbound_requests_for_missing_param(self):
        response = yield self.send_full_inbound_request(**{
            'from': '251911223344',
            'to': '8123',
            'content': 'never odd or even',
            'isTest': 'false',
        })

        self.assertEqual(response.code, 400)
        self.assertEqual(json.loads(response.delivered_body),
                         {'missing_parameter': ['channel']})

    @inlineCallbacks
    def test_outbound_request_credential_selection(self):
        msg1 = self.mk_outbound_message(from_addr='8123')
        yield self.dispatch(msg1)
        request1 = yield self.outbound_requests.get()
        self.assert_outbound_request(request1,
            fromAddress=['8123'],
            username=['root'],
            password=['toor'],
            serviceId=['service-id-1'])

        msg2 = self.mk_outbound_message(from_addr='8124')
        yield self.dispatch(msg2)
        request2 = yield self.outbound_requests.get()
        self.assert_outbound_request(request2,
            fromAddress=['8124'],
            username=['admin'],
            password=['nimda'],
            serviceId=['service-id-2'])

        [ack1, ack2] = yield self.wait_for_dispatched_events(2)
        self.assert_ack(ack1, msg1)
        self.assert_ack(ack2, msg2)

    @inlineCallbacks
    def test_outbound_requests_for_non_ascii_content(self):
        msg = self.mk_outbound_message(content=u'Hliðskjálf')
        yield self.dispatch(msg)
        request = yield self.outbound_requests.get()
        self.assert_outbound_request(request, content=['Hliðskjálf'])

        [ack] = yield self.wait_for_dispatched_events(1)
        self.assert_ack(ack, msg)

    @inlineCallbacks
    def test_outbound_requests_for_known_error_responses(self):
        code = '102999'
        self.set_mock_server_response(http.BAD_REQUEST, code)

        msg = self.mkmsg_out(
            from_addr='8123',
            to_addr='251911223344',
            content='racecar',
            transport_type='sms')
        yield self.dispatch(msg)

        [nack] = yield self.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "(%s) %s" % (
            code, self.transport.KNOWN_ERROR_RESPONSE_CODES[code]))

    @inlineCallbacks
    def test_outbound_requests_for_unknown_error_responses(self):
        code = '103000'
        self.set_mock_server_response(http.BAD_REQUEST, code)

        msg = self.mk_outbound_message()
        yield self.dispatch(msg)

        [nack] = yield self.wait_for_dispatched_events(1)
        self.assert_nack(
            nack, msg, self.transport.UNKNOWN_RESPONSE_CODE_ERROR % code)

    @inlineCallbacks
    def test_outbound_requests_for_unsupported_transport_types(self):
        transport_type = 'steven'
        msg = self.mk_outbound_message(transport_type=transport_type)
        yield self.dispatch(msg)

        [nack] = yield self.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg,
            self.transport.UNSUPPORTED_TRANSPORT_TYPE_ERROR % transport_type)
