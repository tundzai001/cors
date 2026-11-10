"""Firmware-aware BASE wire commands and receiver-side readback."""
import ast
import base64
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from test_interference_config import builder, packets
from test_receiver_base_readback import load


def monver(version='1.51'):
    extensions = ['FWVER=HPG '+version, 'PROTVER=27.50' if version in ('1.50','1.51') else 'PROTVER=27.12', 'MOD=ZED-F9P']
    return b'CORE'.ljust(30,b'\0')+b'00190000\0\0'+b''.join(value.encode().ljust(30,b'\0') for value in extensions)


def live(version='1.51'):
    return {'timestamp':time.time(),'gnss_receiver':{'source':'ubx_mon_ver','age_seconds':0,'payload_b64':base64.b64encode(monver(version)).decode()}}


class FirmwareCommandsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = load()
        self.profile = lambda version: builder.select_ublox_profile(live(version),{'gnss_valset_supported':True})

    def test_firmware_selection_rejects_unknown_stale_and_old_agent_for_new_firmware(self):
        for version in ('1.13','1.30','1.32','1.50','1.51'):
            profile = self.profile(version)
            self.assertTrue(profile['modern'])
            self.assertEqual(profile['itfm'],version not in ('1.50','1.51'))
        for packet in ({},live('1.99'),dict(live(),timestamp=time.time()-121)):
            with self.assertRaises(ValueError): builder.select_ublox_profile(packet,{'gnss_valset_supported':True})
        packet = live()
        with self.assertRaisesRegex(ValueError,'agent'):
            builder.select_ublox_profile(packet,{})
        self.assertFalse(builder.select_ublox_profile(live('1.13'),{})['modern'])

    def values(self,commands):
        merged={}
        for command in commands:
            for packet in packets(command):
                self.assertEqual(packet[2:4],b'\x06\x8a')
                self.assertEqual(packet[6:10],b'\0\x07\0\0')
            merged.update(self.s['_ubx_valset_values'](command))
        return merged

    def test_jamming_sensitivity_is_boolean_persisted_and_only_for_supported_firmware(self):
        self.assertIs(builder.normalize_gnss_options(None)['jamming_sensitivity_high'],True)
        for invalid in (None,0,1,'false',[]):
            with self.assertRaises(ValueError): builder.normalize_gnss_options({'jamming_sensitivity_high':invalid})
        for version in ('1.13','1.50','1.51'):
            for high in (True,False):
                options={'jamming_sensitivity_high':high}
                for commands in (
                    builder.build_base_fixed_lla_command('Ublox',21,105,10,.01,options,receiver_profile=self.profile(version)),
                    builder.build_base_survey_in_command('Ublox',300,3,options,receiver_profile=self.profile(version)),
                ):
                    values=self.values(commands)
                    if version == '1.13': self.assertNotIn(0x10f60051,values)
                    else: self.assertEqual(values[0x10f60051],bytes([high]))
        for chip in ('Unicorecomm',):
            self.assertEqual(builder.build_base_fixed_lla_command(chip,21,105,10,.01,{'jamming_sensitivity_high':True}),
                             builder.build_base_fixed_lla_command(chip,21,105,10,.01,{'jamming_sensitivity_high':False}))

    def test_modern_base_enables_selected_ports_and_verifies_the_enable_keys(self):
        enabled_keys = {'UART1': 0x10520005, 'UART2': 0x10530005, 'USB': 0x10650001}
        for ports in (['UART1'], ['UART2'], ['USB'], list(enabled_keys)):
            options = {'ports': ports}
            profile = self.profile('1.51')
            for commands in (
                builder.build_base_fixed_lla_command('Ublox', 21, 105, 2, .01, options, receiver_profile=profile),
                builder.build_base_survey_in_command('Ublox', 300, 3, options, receiver_profile=profile),
            ):
                values = self.values(commands)
                for port, key in enabled_keys.items():
                    if port in ports:
                        self.assertEqual(values[key], b'\x01')
                    else:
                        self.assertNotIn(key, values)
            config = {'base_setup_method': 'FIXED_LLA', 'coords': {'lat': 21, 'lon': 105, 'alt': 2},
                      'accuracy': .01, 'gnss_options': options}
            payload = builder.build_ublox_verification_payload(config, receiver_profile=profile)
            expected = dict(struct.iter_unpack('<IB', base64.b64decode(payload['output_b64'])))
            for port in ports:
                self.assertEqual(expected[enabled_keys[port]], 1)
            self.s['_query_ubx_serial'] = lambda ser, cls, mid, poll: bytes([1, poll[1], 0, 0]) + b''.join(
                struct.pack('<I', key) + (b'\0' if key in enabled_keys.values() else values[key])
                for key, in struct.iter_unpack('<I', poll[4:]))
            with self.assertRaisesRegex(RuntimeError, 'configuration differs'):
                self.s['_verify_ublox_values'](None, values)

    def test_fixed_survey_persistence_precision_and_no_legacy_for_every_profile(self):
        for version in ('1.13','1.30','1.32','1.50','1.51'):
            for output_mode in ('rtcm_only','diagnostics_rtcm'):
                for msm in (4,7,'both'):
                    options={'ports':['UART1','USB'],'output_mode':output_mode,'msm_level':msm,'interference_detection_enabled':False}
                    legacy = builder.build_base_fixed_lla_command('Ublox','-21.123456789','-105.987654321','-8.1154','.0001',options)[0][6:-2]
                    commands=builder.build_base_fixed_lla_command('Ublox','-21.123456789','-105.987654321','-8.1154','.0001',options,receiver_profile=self.profile(version))
                    values=self.values(commands)
                    for key,value in builder._ubx_tmode_entries(legacy).items(): self.assertEqual(values[key],value)
                    self.assertEqual(0x1041000d in values,version not in ('1.50','1.51'))
                    self.assertEqual(values[0x10760004],b'\0') # unselected UART2 cannot emit conflicting RTCM
                    self.assertEqual(values[0x10740004],b'\x01')
                    survey=self.values(builder.build_base_survey_in_command('Ublox',300,3,options,receiver_profile=self.profile(version)))
                    self.assertEqual(survey[0x20030001],b'\x01')
                    self.assertEqual(int.from_bytes(survey[0x40030010],'little'),300)
                    self.assertEqual(int.from_bytes(survey[0x40030011],'little'),30000)
        um=builder.build_base_fixed_lla_command('Unicorecomm',21,105,10,.01)
        self.assertEqual(um,builder.build_base_fixed_lla_command('Unicorecomm',21,105,10,.01,receiver_profile=self.profile('1.51')))

    async def test_modern_tmode_readback_uses_only_valget_preserving_signed_hp(self):
        config=builder.build_base_fixed_lla_command('Ublox','-21.123456789','105.987654321','-8.1154','.01')[0][6:-2]
        values=builder._ubx_tmode_entries(config)
        for key,offset,size in self.s['_ubx_tmode_fields'](): values.setdefault(key,bytes(size))
        async def respond(packet,key):
            self.assertEqual(key,(6,0x8b))
            keys=struct.iter_unpack('<I',packet[10:-2])
            body=b'\x01\0\0\0'+b''.join(struct.pack('<I',k)+values[k] for k, in keys if k in values)
            return self.s['_ubx_packet'](6,0x8b,body)
        reader=SimpleNamespace(query_base=AsyncMock(side_effect=respond))
        actual=await self.s['_read_ublox_tmode'](reader,True)
        self.assertEqual(actual,config)
        values.pop(0x40030009)
        with self.assertRaisesRegex(ValueError,'Incomplete'): await self.s['_read_ublox_tmode'](reader,True)

    async def test_modern_monver_skips_removed_itfm_polls(self):
        reader=SimpleNamespace(query_base=AsyncMock(return_value=self.s['_ubx_packet'](0x0a,4,monver())))
        await self.s['_refresh_ubx_receiver_info'](reader)
        self.assertEqual(reader.query_base.await_count,2)
        poll=reader.query_base.await_args.args[0]
        self.assertEqual(poll[10:14],struct.pack('<I',0x10f60051))
        self.assertNotIn(struct.pack('<I',0x1041000d),poll)
        info=self.s['LAST_UBX_RECEIVER_INFO']
        self.assertFalse(info['itfm_supported'])
        self.assertEqual(info['firmware'],'HPG 1.51')

    def test_serial_verifier_checks_ram_bbr_flash_and_does_not_accept_wrong_values(self):
        values={0x20030001:b'\x02',0x40030009:struct.pack('<i',-211234567)}
        seen=[]
        def query(ser,cls,mid,poll):
            seen.append(poll[1])
            return bytes([1,poll[1],0,0])+b''.join(struct.pack('<I',k)+v for k,v in values.items())
        self.s['_query_ubx_serial']=query
        self.s['_verify_ublox_values'](None,values)
        self.assertEqual(seen,[0,1,2])
        def wrong(*args): return b'\x01\0\0\0'+struct.pack('<I',0x20030001)+b'\0'
        self.s['_query_ubx_serial']=wrong
        with self.assertRaises(RuntimeError): self.s['_verify_ublox_values'](None,values)

    async def test_modern_configuration_verification_has_no_legacy_polls(self):
        # Reuse the public verifier harness; only responses change with firmware.
        from test_configuration_verification import ConfigurationVerificationTests
        case=ConfigurationVerificationTests();case.setUp()
        payload=builder.build_ublox_verification_payload(case.config,receiver_profile=self.profile('1.51'))
        canonical=base64.b64decode(payload['tmode_b64'])
        values=builder._ubx_tmode_entries(canonical)
        for key,offset,size in self.s['_ubx_tmode_fields'](): values.setdefault(key,bytes(size))
        for port in payload['readback']['ports']:
            for key,value in struct.iter_unpack('<IB',base64.b64decode(port['output_b64'])):
                values.setdefault(key,bytes([value]))
        for key,value in struct.iter_unpack('<IB',base64.b64decode(payload['output_b64'])):
            values[key]=bytes([value])
        for key in (0x10520005,0x10530005,0x10650001): values[key]=b'\x01'
        values[0x40520001]=values[0x40530001]=struct.pack('<I',38400)
        async def respond(packet,key):
            self.assertEqual(key,(6,0x8b))
            keys=struct.iter_unpack('<I',packet[10:-2])
            return case.pack(6,0x8b,b'\x01\0\0\0'+b''.join(struct.pack('<I',k)+values[k] for k, in keys))
        case.reader.query_base.side_effect=respond
        result=await case.verify(payload)
        self.assertEqual(result['state'],'match')

    async def test_both_configure_routes_reject_missing_firmware_before_saving(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[2]
        class HTTPException(Exception):
            def __init__(self,status_code,detail): self.status_code=status_code
        for relative in ('cors_dashboard/backend/app/device_command_routes.py','cors_dashboard/backend/app/routers/devices.py'):
            path=root/relative
            node=next(n for n in ast.parse(path.read_text()).body if getattr(n,'name',None)=='configure_chip_endpoint')
            node.decorator_list=[];node.args.defaults=[];node.returns=None
            for arg in node.args.args: arg.annotation=None
            device=SimpleNamespace(user_id=1,base_config={},system_info={})
            crud=SimpleNamespace(get_device_by_serial=AsyncMock(return_value=device))
            db=SimpleNamespace(commit=AsyncMock(),rollback=AsyncMock())
            scope=dict(crud=crud,HTTPException=HTTPException,command_builder=builder,
                       auth=SimpleNamespace(Role=SimpleNamespace(COORDINATOR='coordinator')),
                       mqtt_handler=SimpleNamespace(device_telemetry_cache={}))
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(path),'exec'),scope)
            request=SimpleNamespace(payload={'sensor_type':'Ublox','mode':'BASE','params':{'base_setup_method':'FIXED_LLA'}})
            admin = SimpleNamespace(role='admin', id=1)
            with self.assertRaises(HTTPException) as error:
                await scope['configure_chip_endpoint']('test',request,db,admin)
            self.assertEqual(error.exception.status_code,409)
            db.commit.assert_not_awaited()
