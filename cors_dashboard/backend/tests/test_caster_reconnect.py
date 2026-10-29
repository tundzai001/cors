"""Reconnect before sending; never replay a control command after sending."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.caster_client import CasterClient


class CasterReconnectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        CasterClient._connection_errors.clear()
        self.client = CasterClient('caster.test', 2101)
        self.reader = Mock(read=AsyncMock(return_value=b'OK\r\n'))
        self.writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())

    async def test_refused_connection_retries_then_sends_once(self):
        with patch('app.caster_client.asyncio.open_connection', AsyncMock(side_effect=[
            ConnectionRefusedError(), ConnectionRefusedError(), (self.reader, self.writer)
        ])) as connect, patch('app.caster_client.asyncio.sleep', AsyncMock()) as sleep:
            result = await self.client._send_command('GET /newuser HTTP/1.0\r\n\r\n')
        self.assertEqual(result, 'OK\r\n')
        self.assertEqual(connect.await_count, 3)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [1, 2])
        self.writer.write.assert_called_once()

    async def test_outage_is_bounded_and_recovery_is_logged(self):
        with patch('app.caster_client.asyncio.open_connection', AsyncMock(side_effect=ConnectionRefusedError())) as connect, \
             patch('app.caster_client.asyncio.sleep', AsyncMock()), \
             patch('app.caster_client.logger.warning') as warning:
            self.assertEqual(await self.client._send_command('GET /sourcetable HTTP/1.0\r\n\r\n'), '')
            self.assertEqual(await self.client._send_command('GET /sourcetable HTTP/1.0\r\n\r\n'), '')
            self.assertEqual(connect.await_count, 6)
            warning.assert_called_once()
        with patch('app.caster_client.asyncio.open_connection', AsyncMock(return_value=(self.reader, self.writer))), \
             patch('app.caster_client.logger.info') as info:
            await self.client._send_command('GET /status HTTP/1.0\r\n\r\n')
            info.assert_called_once()
        self.assertFalse(CasterClient._connection_errors)

    async def test_response_failure_does_not_replay_sent_command(self):
        self.reader.read.side_effect = ConnectionResetError()
        with patch('app.caster_client.asyncio.open_connection', AsyncMock(return_value=(self.reader, self.writer))) as connect, \
             patch('app.caster_client.logger.warning'):
            self.assertEqual(await self.client._send_command('GET /deluser HTTP/1.0\r\n\r\n'), '')
        connect.assert_awaited_once()
        self.writer.write.assert_called_once()
        self.writer.close.assert_called_once()

    async def test_shutdown_cancels_retry(self):
        with patch('app.caster_client.asyncio.open_connection', AsyncMock(side_effect=ConnectionRefusedError())), \
             patch('app.caster_client.asyncio.sleep', AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.client._send_command('GET /status HTTP/1.0\r\n\r\n')
        self.writer.write.assert_not_called()
