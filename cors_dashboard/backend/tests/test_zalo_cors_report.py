from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from app.zalo_cors_report import ionosphere_text, region_for_question, report_text


class CorsReportTests(unittest.IsolatedAsyncioTestCase):
    def data(self, **metadata):
        return {
            'metadata': {'time_tag': datetime.now(timezone.utc).isoformat(), 'is_stale':False, **metadata},
            'features': [
                {'geometry': {'coordinates':[107.5, 23.75]}, 'properties': {'tec':30, 'anomaly':3}},
                {'geometry': {'coordinates':[107.5, 8.75]}, 'properties': {'tec':90, 'anomaly':9}},
                {'geometry': {'coordinates':[0, 0]}, 'properties': {'tec':200, 'anomaly':99}},
            ],
        }

    def test_province_lookup_and_regional_values_exclude_remote_cells(self):
        self.assertEqual(region_for_question('ở tuyen quang đo được không')[0], 'Tuyên Quang')
        local = ionosphere_text(self.data(), 'Tuyên Quang đo được không')
        self.assertIn('Tuyên Quang', local)
        self.assertIn('TEC 30.0 TECU', local)
        self.assertNotIn('90.0', local)
        national = ionosphere_text(self.data(), 'tình trạng Cục')
        self.assertIn('Việt Nam', national)
        self.assertIn('30.0–90.0', national)
        self.assertNotIn('200.0', national)
        self.assertEqual(region_for_question('Hà Nội và Tuyên Quang')[0], 'Việt Nam')

    def test_stale_invalid_and_out_of_region_data_cannot_become_current_conditions(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        for data in (self.data(is_stale=True), self.data(time_tag=old), self.data(time_tag='invalid'), {}):
            self.assertIn('chưa có dữ liệu', ionosphere_text(data, 'Tuyên Quang'))
        data = self.data()
        data['features'] = data['features'][-1:]
        self.assertIn('chưa có dữ liệu', ionosphere_text(data, 'Tuyên Quang'))
        data['features'][0]['properties']['tec'] = float('nan')
        self.assertIn('chưa có dữ liệu', ionosphere_text(data, 'Việt Nam'))

    async def test_report_hides_health_probe_details_and_answers_measurement_question(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=self.data())):
            text = await report_text({'status':'online'}, 'ở Tuyên Quang đo được không')
            self.assertIn('CORS Cục đang hoạt động', text)
            self.assertIn('TEC 30.0 TECU', text)
            self.assertIn('có thể đo thử', text)
            self.assertIn('FIX ổn định', text)
            offline = await report_text({'status':'offline'}, 'Tuyên Quang')
            self.assertIn('nên chờ CORS Cục hoạt động lại', offline)
        for hidden in ('form', 'đăng nhập', '14.238', 'trang dịch vụ'):
            self.assertNotIn(hidden, text)

    async def test_ionosphere_failure_still_returns_health_without_a_good_measurement_claim(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(side_effect=TimeoutError)):
            text = await report_text({'status':'online'}, 'Tuyên Quang')
        self.assertIn('đang hoạt động', text)
        self.assertIn('chưa đủ dữ liệu', text)
        self.assertNotIn('có thể đo thử', text)
