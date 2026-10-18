from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.zalo_cors_report import _regions, ionosphere_text, region_for_question, report_text


class CorsReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch('app.zalo_cors_report.geomagnetic_observation', AsyncMock(return_value={'value': 1.33, 'stamp': '14:00 18/10'}))
        self.kp = patcher.start()
        self.addCleanup(patcher.stop)

    def data(self, **metadata):
        return {
            'metadata': {'time_tag': datetime.now(timezone.utc).isoformat(), 'is_stale':False, **metadata},
            'features': [
                {'geometry': {'coordinates':[107.5, 23.75]}, 'properties': {'tec':30, 'anomaly':3}},
                {'geometry': {'coordinates':[107.5, 8.75]}, 'properties': {'tec':90, 'anomaly':9}},
                {'geometry': {'coordinates':[0, 0]}, 'properties': {'tec':200, 'anomaly':99}},
            ],
        }

    def test_vietnamese_province_file_uses_utf8_on_windows(self):
        original_open = Path.open
        def windows_open(path, *args, **kwargs):
            if kwargs.get('encoding') in (None, 'locale'):
                kwargs['encoding'] = 'cp1252'
            return original_open(path, *args, **kwargs)
        _regions.cache_clear()
        try:
            with patch.object(Path, 'open', windows_open):
                self.assertEqual(region_for_question('hà nội đo được ko')[0], 'Hà Nội')
                self.assertEqual(region_for_question('Tuyên Quang đo được không')[0], 'Tuyên Quang')
        finally:
            _regions.cache_clear()

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
        for data in (self.data(time_tag=old), self.data(time_tag='invalid'), {}):
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
            self.assertIn('chưa thấy trở ngại để bắt đầu ca đo', text)
            self.assertIn('Kp 1.33', text)
            self.assertTrue(text.startswith('Tuyên Quang nhé.'))
            self.assertNotIn('cao hơn nền 30 ngày', text)
            offline = await report_text({'status':'offline'}, 'Tuyên Quang')
            self.assertIn('chuyển sang nguồn dự phòng', offline)
        for hidden in ('form', 'đăng nhập', '14.238', 'trang dịch vụ'):
            self.assertNotIn(hidden, text)

    async def test_ionosphere_failure_still_returns_health_without_a_good_measurement_claim(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(side_effect=TimeoutError)):
            text = await report_text({'status':'online'}, 'Tuyên Quang')
        self.assertIn('đang hoạt động', text)
        self.assertIn('đang mất cập nhật', text)
        self.assertNotIn('có thể đo thử', text)

    async def test_delayed_observation_does_not_become_a_field_warning(self):
        data = self.data(time_tag=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat())
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=data)):
            text = await report_text({'status':'online'}, 'Tuyên Quang đo được không')
        self.assertIn('trễ 31 phút', text)
        self.assertIn('TEC 30.0', text)
        self.assertNotIn('Đo thận trọng', text)
        self.assertNotIn('chưa dùng bản tin', text)
        self.assertIn('chưa thấy trở ngại', text)

    async def test_intents_produce_different_useful_answers(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=self.data())):
            quick = await report_text({'status':'online'}, '/cors')
            diagnostic = await report_text({'status':'online'}, 'ở Tuyên Quang máy cứ FLOAT')
            detailed = await report_text({'status':'online'}, 'điện ly Tuyên Quang chi tiết')
            future = await report_text({'status':'online'}, 'ngày mai đo được không')
        self.assertNotIn('cao hơn nền 30 ngày', quick)
        self.assertIn('tuổi hiệu chỉnh', diagnostic)
        self.assertNotIn('Có thể triển khai đo', diagnostic)
        self.assertIn('TEC 30.0', detailed)
        self.assertIn('chưa phải dự báo', future)

    async def test_storm_and_missing_kp_never_get_quiet_conditions_claim(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=self.data())):
            self.kp.return_value = {'value':7, 'stamp':'14:00 18/10'}
            storm = await report_text({'status':'online'}, 'Tuyên Quang đo được không')
            self.kp.return_value = None
            missing = await report_text({'status':'online'}, 'Tuyên Quang đo được không')
        self.assertIn('Nên hoãn đo khống chế', storm)
        self.assertNotIn('Có thể triển khai đo', missing)
        self.assertNotIn('địa từ yên tĩnh', missing)


class GeomagneticTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_fresh_index_is_selected_and_cached(self):
        from app import zalo_cors_report as report
        from unittest.mock import Mock
        now = datetime.now(timezone.utc)
        rows = [
            {'time_tag':now.isoformat(), 'estimated_kp':1.33},
            {'time_tag':(now-timedelta(minutes=10)).isoformat(), 'estimated_kp':7},
        ]
        response = Mock(json=Mock(return_value=rows))
        with patch.object(report, '_kp_cache', None), patch.object(report, '_kp_fetched', 0), patch(
            'app.zalo_cors_report.httpx.AsyncClient.get', AsyncMock(return_value=response)
        ) as get:
            result = await report.geomagnetic_observation()
            self.assertEqual(result['value'], 1.33)
            self.assertEqual(await report.geomagnetic_observation(), result)
            self.assertEqual(get.await_count, 1)

    async def test_old_and_invalid_index_and_network_failure_are_unavailable(self):
        from app import zalo_cors_report as report
        from unittest.mock import Mock
        for row in (
            {'time_tag':(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(), 'estimated_kp':1},
            {'time_tag':datetime.now(timezone.utc).isoformat(), 'estimated_kp':float('nan')},
            {'time_tag':'broken', 'estimated_kp':1},
        ):
            with self.subTest(row=row), patch.object(report, '_kp_fetched', 0), patch(
                'app.zalo_cors_report.httpx.AsyncClient.get', AsyncMock(return_value=Mock(json=Mock(return_value=[row])))
            ):
                self.assertIsNone(await report.geomagnetic_observation())

        import httpx
        with patch.object(report, '_kp_fetched', 0), patch(
            'app.zalo_cors_report.httpx.AsyncClient.get', AsyncMock(side_effect=httpx.ReadTimeout('timeout'))
        ) as get:
            self.assertIsNone(await report.geomagnetic_observation())
            self.assertIsNone(await report.geomagnetic_observation())
            self.assertEqual(get.await_count, 1)
