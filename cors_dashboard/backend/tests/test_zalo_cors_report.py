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
        self.assertEqual(region_for_question('@Bot Cors manager đo được không ở quảng nam')[0], 'Quảng Nam')
        self.assertEqual(region_for_question('ở tam kỳ đo được không')[0], 'Quảng Nam')
        self.assertEqual(region_for_question('ở hà nam đo được không')[0], 'Hà Nam')
        self.assertEqual(region_for_question('ở đà nẵng đo được không')[0], 'Đà Nẵng')
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
            self.assertIn('Chưa có số liệu RTCM/FIX', text)
            self.assertIn('mountpoint nào', text)
            self.assertNotIn('TECU', text)
            self.assertNotIn('Kp', text)
            self.assertTrue(text.startswith('📍 Tuyên Quang'))
            offline = await report_text({'status':'offline'}, 'Tuyên Quang')
            self.assertIn('chuyển sang nguồn dự phòng', offline)
        for hidden in ('form', 'đăng nhập', '14.238', 'trang dịch vụ'):
            self.assertNotIn(hidden, text)

    async def test_ionosphere_failure_still_returns_health_without_a_good_measurement_claim(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(side_effect=TimeoutError)):
            text = await report_text({'status':'online'}, 'điện ly Tuyên Quang chi tiết')
        self.assertIn('đang hoạt động', text)
        self.assertIn('chưa có dữ liệu cập nhật', text)
        self.assertNotIn('có thể đo thử', text)

    async def test_delayed_observation_does_not_become_a_field_warning(self):
        data = self.data(time_tag=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat())
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=data)):
            text = await report_text({'status':'online'}, 'điện ly Tuyên Quang chi tiết')
        self.assertIn('trễ 31 phút', text)
        self.assertIn('TEC 30.0', text)
        self.assertNotIn('Đo thận trọng', text)
        self.assertNotIn('chưa dùng bản tin', text)
        self.assertIn('không phải sai số đo hay chất lượng FIX', text)

    async def test_intents_produce_different_useful_answers(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=self.data())):
            quick = await report_text({'status':'online'}, '/cors')
            diagnostic = await report_text({'status':'online'}, 'ở Tuyên Quang máy cứ FLOAT')
            detailed = await report_text({'status':'online'}, 'điện ly Tuyên Quang chi tiết')
            future = await report_text({'status':'online'}, 'ngày mai đo được không')
        self.assertNotIn('Gợi ý cho ca đo', quick)
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

    async def test_workflow_advice_matches_job_and_service_risk(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=self.data())):
            terrain = await report_text({'status':'online'}, 'đo địa hình Tuyên Quang được không')
            control = await report_text({'status':'online'}, 'tư vấn đo khống chế Tuyên Quang')
            self.assertIn('điểm kiểm tra đầu/cuối ca', terrain)
            self.assertNotIn('Với khống chế/bố trí', terrain)
            self.assertIn('đo lặp điểm quan trọng ở thời điểm khác', control)
            self.assertNotIn('đầu/cuối ca', control)
            offline = await report_text({'status':'offline'}, 'đo địa hình Tuyên Quang được không')
            self.assertIn('chưa chốt số đo bằng nguồn Cục', offline)
            self.assertNotIn('có thể lên ca', offline)
            self.kp.return_value = {'value':7, 'stamp':'14:00 18/10'}
            storm = await report_text({'status':'online'}, 'đo địa hình Tuyên Quang được không')
            self.assertIn('Dời phần đo khống chế', storm)
            self.assertNotIn('có thể lên ca', storm)

    async def test_high_absolute_tec_and_quiet_kp_do_not_become_favorable_rtk(self):
        data = self.data()
        data['features'][0]['properties'] = {'tec':72.6, 'anomaly':1.18}
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=data)):
            text = await report_text({'status':'online'}, 'điện ly Tuyên Quang chi tiết')
        self.assertIn('TEC 72.6', text)
        self.assertIn('+1.2 đến +1.2', text)
        self.assertIn('không phải sai số đo hay chất lượng FIX', text)
        self.assertNotIn('đang thuận', text)
        self.assertNotIn('có thể lên ca', text)

    def test_opposite_local_anomalies_are_not_hidden_by_mean(self):
        data = self.data()
        data['features'][0]['properties']['anomaly'] = 20
        data['features'][1]['properties']['anomaly'] = -20
        text = ionosphere_text(data, 'Việt Nam')
        self.assertIn('xấp xỉ mức nền', text)
        self.assertIn('-20.0 đến +20.0', text)

    async def test_actual_stability_question_is_short_and_needs_no_noaa_calls(self):
        question = '@Bot Cors manager cors tại tỉnh bà rịa vũng tàu có ổn định không ạ'
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock()) as noaa:
            text = await report_text({'status': 'online'}, question)
        noaa.assert_not_awaited()
        self.kp.assert_not_awaited()
        self.assertIn('Bà Rịa - Vũng Tàu', text)
        self.assertIn('chưa có số liệu RTCM/FIX', text)
        self.assertEqual(text.count('?'), 1)
        self.assertLess(len(text), 350)
        for unwanted in ('TECU', 'Kp', 'Gợi ý cho ca đo', 'đo khống chế'):
            self.assertNotIn(unwanted, text)

    def test_typo_lookup_and_ambiguity(self):
        self.assertEqual(region_for_question('ở Tuyên Quangg đo được không')[0], 'Tuyên Quang')
        self.assertEqual(region_for_question('ở Quảg Nam đo được không')[0], 'Quảng Nam')
        self.assertEqual(region_for_question('ha noii có ổn định không')[0], 'Hà Nội')
        self.assertEqual(region_for_question('ở Hà Nam đo được không')[0], 'Hà Nam')
        self.assertEqual(region_for_question('Tuyên Quangg và Quảg Nam')[0], 'Việt Nam')
        self.assertEqual(region_for_question('mất kết nối rồi')[0], 'Việt Nam')

    def test_rule_priorities_and_bounded_shared_state(self):
        from app.zalo_cors_dialogue import cors_intent, _brain
        for question, expected in (
            ('may cu float ha noi', 'diagnostic'),
            ('ngay mai do dia hinh', 'forecast'),
            ('dien ly ha noi chi tiet', 'detail'),
            ('do chi tiet tuyen quang', 'measurement'),
            ('co on dinh khong', 'stability'),
            ('cors', 'status'),
        ):
            self.assertEqual(cors_intent(question), expected)
        self.assertFalse(_brain().get_uservars())

    async def test_local_definitions_are_not_fault_reports_and_do_not_fetch_observations(self):
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock()) as noaa:
            for question, expected in (
                ('FLOAT là gì', 'hai trạng thái nghiệm RTK'),
                ('RTCM là gì', 'định dạng dữ liệu hiệu chỉnh'),
                ('NTRIP nghĩa là gì', 'truyền dữ liệu GNSS'),
                ('mountpoint là gì', 'tên luồng'),
                ('GGA là gì', 'bản tin NMEA'),
                ('tuổi hiệu chỉnh là gì', 'thời gian đã trôi qua'),
                ('chào bot', 'Mình đây'),
            ):
                with self.subTest(question=question):
                    text = await report_text({}, question)
                    self.assertIn(expected, text)
                    self.assertNotIn('CORS Cục đang hoạt động', text)
            more = await report_text({}, 'nói rõ hơn', previous_intent='faq_fix')
            self.assertIn('pha sóng mang', more)
        noaa.assert_not_awaited()
        self.kp.assert_not_awaited()

    async def test_abbreviations_and_multiple_provinces_require_the_correct_scope(self):
        self.assertEqual(region_for_question('BRVT')[0], 'Bà Rịa - Vũng Tàu')
        self.assertEqual(region_for_question('HN')[0], 'Hà Nội')
        self.assertEqual(region_for_question('TPHCM')[0], 'Hồ Chí Minh')
        self.assertEqual(region_for_question('tuyenquangg')[0], 'Tuyên Quang')
        for question in ('DN có ổn định không', 'Hà Nội và Tuyên Quangg ổn không'):
            text = await report_text({'status': 'online'}, question)
            self.assertIn('Anh muốn kiểm tra', text)
            self.assertIn(' hay ', text)
            self.assertNotIn('đang hoạt động', text)

    def test_fuzzy_intents_preserve_qualifiers_and_context_does_not_leak(self):
        from app.zalo_cors_dialogue import cors_intent, _brain
        for question, previous, expected in (
            ('on ddinh khong', 'status', 'stability'),
            ('may cu floatt', 'status', 'diagnostic'),
            ('khong on ddinh', 'status', 'diagnostic'),
            ('giai thich them', 'diagnostic', 'diagnostic_steps'),
            ('3s ha noi', 'diagnostic', 'diagnostic_age'),
            ('3s', 'status', 'status'),
            ('cu tang lien tuc', 'diagnostic_age', 'diagnostic_stream'),
            ('van giu on dinh', 'diagnostic_age', 'diagnostic_signal'),
            ('tom tat', 'detail', 'detail_brief'),
        ):
            with self.subTest(question=question):
                self.assertEqual(cors_intent(question, previous), expected)
        self.assertFalse(_brain().get_uservars())

    async def test_brief_detail_keeps_observation_age_and_avoids_a_table(self):
        data = self.data(time_tag=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat())
        with patch('app.zalo_cors_report.ionosphere_service.get_latest', AsyncMock(return_value=data)):
            text = await report_text({'status': 'online'}, 'ngắn gọn Tuyên Quang', previous_intent='detail')
        self.assertIn('TEC 30.0', text)
        self.assertIn('trễ 31 phút', text)
        self.assertNotIn('Độ lệch từng ô', text)
        self.assertNotIn('đang ổn định', text)


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
